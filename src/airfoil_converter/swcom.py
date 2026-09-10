"""The only module that talks to SolidWorks.

Everything COM-shaped is confined here, behind plain data. ``pywin32`` is
imported inside a ``try`` so this module imports anywhere — the tests run on
Linux — and :func:`is_available` reports whether it is really usable.

Three things about this connection were established by probe rather than
guessed, and each is load-bearing:

* **Attach through the Running Object Table, never a ProgID.** This machine
  carries SolidWorks 2024, 2025 and 2026 side by side. The unversioned
  ``SldWorks.Application`` ProgID resolves to whichever registered last, which
  is not necessarily the one on screen. Every live session instead publishes a
  moniker named ``SolidWorks_PID_<pid>``, and that name is the same across
  versions.
* **The table hands back an IUnknown.** ``dynamic.Dispatch`` cannot ask it for
  type information until it has been asked for its IDispatch face.
* **A zero-argument method is a property get.** Late binding turns
  ``RevisionNumber()`` into a plain string attribute and ``ActiveDoc`` into a
  document object, and *invoking* either raises "Member not found". Only
  members that take arguments are called. That is what :func:`call` encodes.
* **Two calls need their arguments typed by hand.** ``ModifyDefinition`` takes
  a component that is absent for a part, and a plain ``None`` there is a type
  mismatch; it needs a null of dispatch type. ``GetObjectByPersistReference3``
  has an out parameter that must be supplied as a by-reference integer. Both
  were found by probe, and both are silent until they are not.
"""

from __future__ import annotations

import os
import queue
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple, TypeVar

try:  # pragma: no cover - exercised only on Windows with pywin32 present
    import pythoncom
    import win32com.client.dynamic as _dynamic
    from win32com.client import VARIANT

    _IMPORT_ERROR: Optional[str] = None
except ImportError as exc:  # pragma: no cover - the Linux and no-pywin32 path
    pythoncom = None  # type: ignore[assignment]
    _dynamic = None  # type: ignore[assignment]
    VARIANT = None  # type: ignore[assignment]
    _IMPORT_ERROR = str(exc)

T = TypeVar("T")

# SolidWorks majors advance by one a year: 32 is 2024, 33 is 2025, 34 is 2026.
MINIMUM_MAJOR = 34
HIGHEST_TESTED_MAJOR = 34
_YEAR_OFFSET = 1992

MONIKER_PREFIX = "solidworks_pid_"
_MONIKER = re.compile(r"^SolidWorks_PID_(\d+)$", re.IGNORECASE)

CURVE_TYPE_NAME = "CurveInFile"
COMPOSITE_TYPE_NAME = "CompositeCurve"

# Composite Curve reads its inputs from the selection, and only from selection
# mark 1. At mark 0 it returns False and says nothing about why.
COMPOSITE_SELECT_MARK = 1

# SolidWorks holds curve points in metres however the file is written, so
# everything crossing this boundary is scaled. The file says "175.000000mm"
# and the part reads back 0.175.
MM_PER_METRE = 1000.0


class SolidWorksError(Exception):
    """Something about the SolidWorks connection did not work."""


class NotAvailable(SolidWorksError):
    """pywin32 is not installed, or this is not Windows."""


class NotRunning(SolidWorksError):
    """No SolidWorks session is reachable."""


class WrongVersion(SolidWorksError):
    """Every reachable session is older than this app supports."""


def is_available() -> bool:
    return pythoncom is not None


def unavailable_reason() -> str:
    if is_available():
        return ""
    return _IMPORT_ERROR or "pywin32 is not installed, so SolidWorks cannot be reached."


# -- pure helpers, testable without SolidWorks ------------------------------


def parse_revision(text: str) -> Tuple[int, ...]:
    """``"34.0.0"`` -> ``(34, 0, 0)``."""
    parts = str(text).strip().split(".")
    try:
        numbers = tuple(int(p) for p in parts if p != "")
    except ValueError:
        raise SolidWorksError(f"Could not read a version out of {text!r}.") from None
    if not numbers:
        raise SolidWorksError(f"Could not read a version out of {text!r}.")
    return numbers


def release_year(major: int) -> int:
    """The marketing year for a major revision. Display only, never a decision.

    Holds for the three installs on this machine. Being display-only, a broken
    convention costs one wrong word in a message and nothing else.
    """
    return major + _YEAR_OFFSET


def version_label(revision: Sequence[int]) -> str:
    return f"SolidWorks {release_year(revision[0])} (revision {'.'.join(str(n) for n in revision)})"


def is_solidworks_moniker(name: str) -> bool:
    return bool(_MONIKER.match(name or ""))


def pid_from_moniker(name: str) -> int:
    match = _MONIKER.match(name or "")
    if match is None:
        raise SolidWorksError(f"{name!r} is not a SolidWorks moniker.")
    return int(match.group(1))


@dataclass(frozen=True)
class Candidate:
    """One reachable session, described without holding a COM pointer."""

    moniker: str
    pid: int
    revision: Tuple[int, ...]
    has_active_doc: bool = False
    app: Any = field(default=None, compare=False, repr=False)


def meets_minimum(revision: Sequence[int]) -> bool:
    return bool(revision) and revision[0] >= MINIMUM_MAJOR


def is_newer_than_tested(revision: Sequence[int]) -> bool:
    return bool(revision) and revision[0] > HIGHEST_TESTED_MAJOR


def choose(candidates: Sequence[Candidate]) -> Candidate:
    """Pick the session to drive: the newest one that clears the floor.

    Never refuses a version for being too new. A 2027 install reports 35 and is
    used exactly as 2026 is; the API this app leans on has been stable across
    many releases, so the realistic failure there is changed behaviour, not a
    missing member, and locking the user out would cost everything and buy
    nothing.
    """
    if not candidates:
        raise NotRunning(
            "No running SolidWorks was found. Open the part you want the curves in."
        )

    usable = [c for c in candidates if meets_minimum(c.revision)]
    if not usable:
        found = ", ".join(sorted({version_label(c.revision) for c in candidates}))
        raise WrongVersion(
            f"Found {found}. This needs SolidWorks {release_year(MINIMUM_MAJOR)} "
            f"(revision {MINIMUM_MAJOR}) or newer."
        )

    # Newest first; a session with a document open wins a tie, because that is
    # almost certainly the window the user is looking at. PID last, only so the
    # choice is deterministic when nothing else separates them.
    return sorted(usable, key=lambda c: (c.revision, c.has_active_doc, -c.pid), reverse=True)[0]


# -- talking to COM ---------------------------------------------------------


def call(obj: Any, name: str, *args: Any) -> Any:
    """Reach a member of a late-bound COM object.

    Attribute access already invokes a zero-argument member, so ``Name``,
    ``RevisionNumber`` and ``GetNextFeature`` all come back as their results.
    Testing ``callable`` and invoking would be wrong: a member returning a
    document is itself callable, and calling it raises "Member not found".
    """
    member = getattr(obj, name)
    return member(*args) if args else member


def _dispatch(raw: Any) -> Any:
    return _dynamic.Dispatch(raw.QueryInterface(pythoncom.IID_IDispatch))


def _null_dispatch() -> Any:
    """An absent COM object, typed. A bare None is a type mismatch."""
    return VARIANT(pythoncom.VT_DISPATCH, None)


def _out_long() -> Any:
    """A by-reference integer for an out parameter SolidWorks insists on."""
    return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)


def find_candidates(out_unreachable: "Optional[List[Tuple[str, str]]]" = None) -> List[Candidate]:
    """Every reachable session, interrogated. Never launches SolidWorks.

    ``Dispatch("SldWorks.Application")`` would *start* a copy of SolidWorks, so
    the table is the only way in: nothing here creates anything.
    """
    if not is_available():
        raise NotAvailable(unavailable_reason())

    table = pythoncom.GetRunningObjectTable()
    context = pythoncom.CreateBindCtx(0)
    found: List[Candidate] = []
    unreachable: List[Tuple[str, str]] = [] if out_unreachable is None else out_unreachable

    for moniker in table.EnumRunning():
        try:
            name = moniker.GetDisplayName(context, None)
        except pythoncom.com_error:
            continue
        if not is_solidworks_moniker(name):
            continue

        # One unresponsive session — sitting on a modal dialog, say — must not
        # sink the whole scan. It is skipped, but never silently: a session
        # that vanishes without explanation is the hardest kind of bug to
        # place later.
        try:
            app = _dispatch(table.GetObject(moniker))
            revision = parse_revision(call(app, "RevisionNumber"))
            has_doc = call(app, "ActiveDoc") is not None
        except (pythoncom.com_error, SolidWorksError, AttributeError) as exc:
            unreachable.append((name, str(exc)))
            continue

        found.append(
            Candidate(
                moniker=name,
                pid=pid_from_moniker(name),
                revision=revision,
                has_active_doc=has_doc,
                app=app,
            )
        )
    return found


@dataclass(frozen=True)
class DocInfo:
    title: str
    path: str
    doc_type: int

    @property
    def is_part(self) -> bool:
        return self.doc_type == 1  # swDocPART

    @property
    def is_saved(self) -> bool:
        return bool(self.path)


@dataclass(frozen=True)
class FeatureInfo:
    name: str
    type_name: str

    @property
    def is_curve(self) -> bool:
        return self.type_name in (CURVE_TYPE_NAME, COMPOSITE_TYPE_NAME)

    @property
    def is_composite(self) -> bool:
        return self.type_name == COMPOSITE_TYPE_NAME


class Session:
    """One connected SolidWorks. Only ever touched from the worker thread."""

    def __init__(self, app: Any, revision: Tuple[int, ...], pid: int) -> None:
        self._app = app
        self.revision = revision
        self.pid = pid

    @property
    def label(self) -> str:
        return version_label(self.revision)

    def active_document(self) -> Optional[DocInfo]:
        doc = call(self._app, "ActiveDoc")
        return None if doc is None else self._describe(doc)

    def documents(self) -> List[DocInfo]:
        out: List[DocInfo] = []
        doc = call(self._app, "GetFirstDocument")
        guard = 0
        while doc is not None and guard < 200:
            guard += 1
            out.append(self._describe(doc))
            doc = call(doc, "GetNext")
        return out

    @staticmethod
    def _describe(doc: Any) -> DocInfo:
        return DocInfo(
            title=call(doc, "GetTitle"),
            path=call(doc, "GetPathName"),
            doc_type=int(call(doc, "GetType")),
        )

    def _active(self) -> Any:
        doc = call(self._app, "ActiveDoc")
        if doc is None:
            raise SolidWorksError("No document is open in SolidWorks.")
        return doc

    def features(self) -> List[FeatureInfo]:
        """Every feature in the active document, subfeatures included."""
        return self._walk(call(self._active(), "FirstFeature"))

    def curve_features(self) -> List[FeatureInfo]:
        return [f for f in self.features() if f.is_curve]

    def _walk(self, first: Any, depth: int = 0) -> List[FeatureInfo]:
        out: List[FeatureInfo] = []
        feature = first
        guard = 0
        while feature is not None and guard < 5000:
            guard += 1
            try:
                type_name = str(call(feature, "GetTypeName2"))
            except Exception:  # noqa: BLE001 - a feature that will not describe itself
                type_name = "?"
            out.append(FeatureInfo(name=str(call(feature, "Name")), type_name=type_name))

            if depth < 4:  # curves can sit inside a folder
                try:
                    child = call(feature, "GetFirstSubFeature")
                except Exception:  # noqa: BLE001
                    child = None
                if child is not None:
                    out.extend(self._walk(child, depth + 1))

            feature = call(feature, "GetNextFeature")
        return out


    # -- the write path ----------------------------------------------------

    def _curve_feature(self, name: str) -> Any:
        doc = self._active()
        feature = call(doc, "FirstFeature")
        guard = 0
        while feature is not None and guard < 5000:
            guard += 1
            if str(call(feature, "Name")) == name:
                return feature
            feature = call(feature, "GetNextFeature")
        raise SolidWorksError(f"No feature called {name!r} is in {call(doc, 'GetTitle')}.")

    def feature_names(self) -> List[str]:
        return [f.name for f in self.features()]

    def insert_curve(self, path: str, name: str) -> str:
        """Import a curve file as a new feature and give it the name we want.

        ``InsertCurveFile`` answers only True or False, so which feature it
        made is found by diffing the tree either side of the call. The name is
        then read back rather than assumed: SolidWorks quietly appends a digit
        on a collision, and a record holding a name that does not exist is the
        silent failure this whole design is built to avoid.
        """
        doc = self._active()
        before = set(self.feature_names())
        if not call(doc, "InsertCurveFile", os.path.abspath(path)):
            raise SolidWorksError(f"SolidWorks refused to import {path}.")

        created = [n for n in self.feature_names() if n not in before]
        if len(created) != 1:
            raise SolidWorksError(
                f"Importing {os.path.basename(path)} added {len(created)} features, "
                "so which one it is cannot be told."
            )
        return self.rename_feature(created[0], name)

    def rename_feature(self, current: str, new: str) -> str:
        """Rename, and report the name SolidWorks actually kept."""
        feature = self._curve_feature(current)
        feature.Name = new
        return str(call(feature, "Name"))

    def reload_curve(self, name: str, path: str) -> None:
        """Point an existing curve at a file again and commit it.

        The points are replaced; the feature, and everything referring to it,
        is untouched. No rebuild happens here — see :meth:`rebuild`.
        """
        doc = self._active()
        feature = self._curve_feature(name)
        definition = call(feature, "GetDefinition")
        if definition is None:
            raise SolidWorksError(f"{name} has no editable definition.")
        if not call(definition, "LoadPointsFromFile", os.path.abspath(path)):
            raise SolidWorksError(f"{name} would not read {os.path.basename(path)}.")
        # The third argument is the component the feature belongs to, which a
        # part does not have. A bare None is a type mismatch; the null has to
        # carry a type.
        if not call(feature, "ModifyDefinition", definition, doc, _null_dispatch()):
            raise SolidWorksError(f"SolidWorks would not commit the new points for {name}.")

    def curve_points(self, name: str) -> List[Tuple[float, float, float]]:
        """The points a curve actually holds, in millimetres."""
        definition = call(self._curve_feature(name), "GetDefinition")
        flat = call(definition, "PointArray")
        if flat is None:
            return []
        values = [float(v) * MM_PER_METRE for v in flat]
        return [tuple(values[i:i + 3]) for i in range(0, len(values) - 2, 3)]  # type: ignore[misc]

    def persist_reference(self, name: str) -> bytes:
        """A handle to a feature that outlives its name."""
        doc = self._active()
        extension = call(doc, "Extension")
        return bytes(call(extension, "GetPersistReference3", self._curve_feature(name)))

    def name_from_persist_reference(self, reference: bytes) -> Optional[str]:
        """What that feature is called now, or None if it is gone."""
        doc = self._active()
        extension = call(doc, "Extension")
        try:
            result = call(extension, "GetObjectByPersistReference3", reference, _out_long())
        except Exception:  # noqa: BLE001 - a stale reference is an answer, not a fault
            return None
        found = result[0] if isinstance(result, tuple) else result
        if found is None:
            return None
        try:
            return str(call(found, "Name"))
        except Exception:  # noqa: BLE001
            return None

    def insert_composite_curve(self, sources: Sequence[str], name: str) -> str:
        """Join several curves into one selectable curve, and name it.

        A Curve Through XYZ Points is one spline through its points, so an
        aerofoil and the straight line closing its blunt trailing edge cannot
        be one of those without the spline rounding the corners. A composite
        joins them instead of refitting them, so the corners stay sharp — and
        being derived, it follows its inputs whenever they are reloaded.
        """
        if len(sources) < 2:
            raise SolidWorksError("A composite curve needs at least two curves to join.")

        doc = self._active()
        extension = call(doc, "Extension")
        call(doc, "ClearSelection2", True)
        for position, source in enumerate(sources):
            selected = call(
                extension, "SelectByID2", source, "REFERENCECURVES",
                0.0, 0.0, 0.0, position > 0, COMPOSITE_SELECT_MARK, _null_dispatch(), 0,
            )
            if not selected:
                call(doc, "ClearSelection2", True)
                raise SolidWorksError(f"{source} could not be selected to join.")

        before = set(self.feature_names())
        made = call(doc, "InsertCompositeCurve")
        call(doc, "ClearSelection2", True)
        if not made:
            raise SolidWorksError(
                f"SolidWorks would not join {' and '.join(sources)} into one curve."
            )

        created = [n for n in self.feature_names() if n not in before]
        if len(created) != 1:
            raise SolidWorksError(
                f"Joining added {len(created)} features, so which one it is cannot be told."
            )
        return self.rename_feature(created[0], name)

    def set_rebuild_suppressed(self, suppressed: bool) -> None:
        """Hold the rebuild off while several curves are reloaded.

        Reloading with rebuild live shows a real but transient error partway
        through — mid-refresh some curves carry the old geometry and some the
        new, so the surface genuinely does not close — and rebuilds once per
        curve for nothing.
        """
        self._app.CommandInProgress = bool(suppressed)

    def rebuild(self) -> bool:
        """Rebuild the active document. Called once, after the last curve."""
        return bool(call(self._active(), "ForceRebuild3", False))


def connect() -> Session:
    """Attach to the newest reachable SolidWorks that this app supports."""
    chosen = choose(find_candidates())
    return Session(chosen.app, chosen.revision, chosen.pid)


# -- the apartment ----------------------------------------------------------


class Worker:
    """One dedicated apartment-threaded thread, and everything COM on it.

    A COM object belongs to the apartment that made it. Keeping every call on
    a single thread means no interface ever has to be marshalled — and nothing
    COM-shaped is allowed back across the queue, only plain data. It also keeps
    a slow call off the interface: a modal dialog open in SolidWorks blocks a
    call for as long as it stays open, and there is no safe way to cancel one.
    """

    def __init__(self, connect_fn: Callable[[], Any] = connect) -> None:
        self._connect = connect_fn
        self._jobs: "queue.Queue[Optional[Tuple[Callable[[Any], Any], queue.Queue]]]" = queue.Queue()
        self._session: Any = None
        self._busy = threading.Event()
        self._thread = threading.Thread(target=self._pump, name="solidworks", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        if pythoncom is not None:  # pragma: no branch
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    return
                work, outbox = job
                self._busy.set()
                try:
                    if self._session is None:
                        self._session = self._connect()
                    outbox.put((work(self._session), None))
                except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
                    self._session = None
                    outbox.put((None, exc))
                finally:
                    self._busy.clear()
        finally:
            self._session = None
            if pythoncom is not None:  # pragma: no branch
                pythoncom.CoUninitialize()

    def busy(self) -> bool:
        return self._busy.is_set() or not self._jobs.empty()

    def submit(self, work: Callable[[Any], T]) -> "Call":
        outbox: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put((work, outbox))
        return Call(outbox)

    def forget_session(self) -> None:
        """Drop the cached connection so the next call attaches afresh."""
        self.submit(lambda _session: None)

    def shutdown(self, timeout: float = 5.0) -> None:
        self._jobs.put(None)
        self._thread.join(timeout)


class Call:
    """A submitted job, collected without blocking the interface."""

    def __init__(self, outbox: "queue.Queue") -> None:
        self._outbox = outbox
        self._result: Optional[Tuple[Any, Optional[BaseException]]] = None

    def poll(self) -> Optional[Tuple[Any, Optional[BaseException]]]:
        if self._result is None:
            try:
                self._result = self._outbox.get_nowait()
            except queue.Empty:
                return None
        return self._result

    def wait(self, timeout: Optional[float] = None) -> Tuple[Any, Optional[BaseException]]:
        if self._result is None:
            self._result = self._outbox.get(timeout=timeout)
        return self._result


# -- diagnostic -------------------------------------------------------------

EXIT_OK = 0
EXIT_NOT_AVAILABLE = 2
EXIT_NOT_RUNNING = 3
EXIT_WRONG_VERSION = 4
EXIT_COM_ERROR = 5


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Report what this machine's SolidWorks looks like from here.

    Run as ``python -m airfoil_converter.swcom``. Everything the live link
    depends on is printed by this one command, which is what makes a failure
    somewhere later cheap to place.
    """
    if not is_available():
        print(f"pywin32 is not usable here: {unavailable_reason()}")
        print("Install it with: pip install pywin32")
        return EXIT_NOT_AVAILABLE

    unreachable: List[Tuple[str, str]] = []
    try:
        candidates = find_candidates(unreachable)
    except SolidWorksError as exc:
        print(f"error: {exc}")
        return EXIT_COM_ERROR

    for moniker, reason in unreachable:
        print(f"unreachable: {moniker}  {reason}")

    if not candidates:
        print("No running SolidWorks was found in the Running Object Table.")
        return EXIT_NOT_RUNNING

    print(f"sessions found: {len(candidates)}")
    for candidate in candidates:
        print(f"  {candidate.moniker}  {version_label(candidate.revision)}")

    try:
        chosen = choose(candidates)
    except WrongVersion as exc:
        print(f"\n{exc}")
        return EXIT_WRONG_VERSION
    except NotRunning as exc:
        print(f"\n{exc}")
        return EXIT_NOT_RUNNING

    session = Session(chosen.app, chosen.revision, chosen.pid)
    note = " — newer than tested" if is_newer_than_tested(chosen.revision) else ""
    print(f"\nusing {session.label}, PID {session.pid}{note}")

    documents = session.documents()
    if not documents:
        print("no documents open")
        return EXIT_OK

    active = session.active_document()
    for doc in documents:
        mark = "*" if active and doc.title == active.title else " "
        where = doc.path or "(never saved)"
        print(f"{mark} {doc.title}  type={doc.doc_type}  {where}")

    if active is None:
        print("\nno active document, so no features listed")
        return EXIT_OK
    if not active.is_part:
        print(f"\n{active.title} is not a part, so it can hold no curves")
        return EXIT_OK

    try:
        features = session.features()
    except SolidWorksError as exc:
        print(f"\nerror walking {active.title}: {exc}")
        return EXIT_COM_ERROR

    curves = [f for f in features if f.is_curve]
    print(f"\n{active.title}: {len(features)} features, {len(curves)} curve(s)")
    for curve in curves:
        print(f"    {curve.name}  ({curve.type_name})")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
