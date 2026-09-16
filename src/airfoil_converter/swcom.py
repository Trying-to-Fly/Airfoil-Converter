"""The only module that talks to SolidWorks.

Everything COM-shaped is confined here, behind plain data. ``pywin32`` is
imported inside a ``try`` so this module imports anywhere — the tests run on
Linux — and :func:`is_available` reports whether it is really usable.

Everything below about this connection was established by probe rather than
guessed, and each point is load-bearing:

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
* **What was clicked is settled by asking the object, not by its type
  number.** ``GetSelectedObjectType3`` is a long list of constants, and getting
  one wrong would be silent. So a vertex is whatever answers ``GetPoint``, an
  edge whatever answers ``GetCurve``, a sketch line whatever answers
  ``GetStartPoint2``; the type numbers only supply a noun for a refusal
  message. Run ``python -m airfoil_converter.swcom --selection`` to see what
  any click actually offers.
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar, Union

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
Vec3 = Tuple[float, float, float]

# SolidWorks majors advance by one a year: 32 is 2024, 33 is 2025, 34 is 2026.
MINIMUM_MAJOR = 34
HIGHEST_TESTED_MAJOR = 34
_YEAR_OFFSET = 1992

MONIKER_PREFIX = "solidworks_pid_"
_MONIKER = re.compile(r"^SolidWorks_PID_(\d+)$", re.IGNORECASE)

CURVE_TYPE_NAME = "CurveInFile"
COMPOSITE_TYPE_NAME = "CompositeCurve"
FOLDER_TYPE_NAME = "FtrFolder"

# A folder in the tree is two features: the folder and a closing tag after
# everything it holds. The tag is an implementation detail of the walk and is
# never shown or counted.
FOLDER_END_TAG = "___EndTag___"

# swFeatureTreeFolderType_e. Only "containing" is any use here, and on SolidWorks
# 2026 it is the only value that makes a folder around what is selected: 1 makes
# an empty one, 3 hands back the Surface Bodies folder, 0 does nothing. The
# numbers were read off a live session rather than trusted to the header.
FOLDER_CONTAINING = 2

# Composite Curve reads its inputs from the selection, and only from selection
# mark 1. At mark 0 it returns False and says nothing about why.
COMPOSITE_SELECT_MARK = 1

# A loft reads its profiles from selection mark 1 and its guide curves from
# mark 2, as the Loft property page does.
LOFT_PROFILE_MARK = 1
LOFT_GUIDE_MARK = 2
LOFT_TYPE_NAME = "Blend"
LOFT_SURFACE_TYPE_NAME = "BlendRefSurface"

# Values read out of the SolidWorks 2026 constant library (swconst.tlb) rather
# than remembered: swGuideCurveInfluence_e, swFeatureSuppressionAction_e,
# swInConfigurationOpts_e, swOpenDocOptions_e, swSaveAsOptions_e,
# swDocumentTypes_e.
GUIDE_TO_NEXT_GUIDE = 0
GUIDE_TO_NEXT_SHARP = 1
GUIDE_TO_NEXT_EDGE = 2
GUIDE_GLOBAL = 3
SUPPRESS = 0
UNSUPPRESS = 1
THIS_CONFIGURATION = 1
SW_DOC_PART = 1
OPEN_SILENT = 1
SAVE_SILENT = 1
SAVE_AS_COPY = 2

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


# -- what a click in SolidWorks comes back as -------------------------------
#
# Plain data, in millimetres, so the pick can be reasoned about and tested on a
# machine with no CAD package. Nothing below holds a COM pointer.


@dataclass(frozen=True)
class PickedPoint:
    where: Vec3


@dataclass(frozen=True)
class PickedLine:
    start: Vec3
    end: Vec3


@dataclass(frozen=True)
class PickedPlane:
    normal: Vec3
    root: Vec3


@dataclass(frozen=True)
class Refused:
    """Something was selected, but not something this step can use."""

    what: str


Picked = Union[PickedPoint, PickedLine, PickedPlane, Refused]

# Only ever used to put a noun in a refusal, never to decide how to read
# something — that is done by asking the object itself, below. So a number
# missing or wrong here costs one imprecise word in one message, and nothing
# else. swSelectType_e; 1, 2, 3, 4, 24 and 25 are what the probe reported.
SELECTION_NAMES = {
    1: "an edge",
    2: "a face",
    3: "a corner",
    4: "a plane",
    5: "an axis",
    6: "a reference point",
    9: "a sketch",
    24: "a sketch curve",
    25: "a sketch point",
}


def transform_point(data: Sequence[float], point: Vec3) -> Vec3:
    """Put a point through a SolidWorks transform.

    ``IMathTransform.ArrayData`` is sixteen doubles: nine of rotation, three of
    translation, then a scale. Sketch geometry reports itself in the sketch's
    own coordinates, and this is what carries it back into the model's.

    **The rotation is stored by columns.** The first three values are where the
    local X axis ends up, the next three the local Y, the next three the local
    Z. Reading them as rows instead transposes the rotation, which is silent on
    the Front plane because its transform is the identity, and wrong on every
    other plane: the probe reported the Top plane's normal as -Y and the Right
    plane's as -X before this was turned the right way round.
    """
    r = [float(v) for v in data[:9]]
    tx, ty, tz = (float(v) for v in data[9:12])
    s = float(data[12]) if len(data) > 12 else 1.0
    x, y, z = point
    return (
        s * (r[0] * x + r[3] * y + r[6] * z) + tx,
        s * (r[1] * x + r[4] * y + r[7] * z) + ty,
        s * (r[2] * x + r[5] * y + r[8] * z) + tz,
    )


def _try(obj: Any, name: str, *args: Any) -> Any:
    """A member the object may simply not have.

    What was clicked is worked out by asking it what it can do, rather than by
    trusting a table of type numbers. A vertex has ``GetPoint``, an edge has
    ``GetCurve``, a sketch line has ``GetStartPoint2``; anything else answers
    this with None.
    """
    if obj is None:
        return None
    try:
        return call(obj, name, *args)
    except Exception:  # noqa: BLE001 - "no such member" is the answer, not a fault
        return None


def _in_mm(values: Sequence[float]) -> Vec3:
    return (
        float(values[0]) * MM_PER_METRE,
        float(values[1]) * MM_PER_METRE,
        float(values[2]) * MM_PER_METRE,
    )


def _interpret(manager: Any, index: int) -> Picked:
    obj = call(manager, "GetSelectedObject6", index, -1)
    if obj is None:
        return Refused("something this app cannot read")
    for reader in (_as_plane, _as_line, _as_point):
        found = reader(manager, index, obj)
        if found is not None:
            return found
    type_id = _try(manager, "GetSelectedObjectType3", index, -1)
    return Refused(SELECTION_NAMES.get(int(type_id or 0), "something this app cannot use"))


def _as_plane(manager: Any, index: int, obj: Any) -> Optional[PickedPlane]:
    surface = _try(obj, "GetSurface")
    if surface is not None and _try(surface, "IsPlane"):
        # PlaneParams runs normal first, then a root point on the plane. Probed
        # against the six faces of a box: the first three always come back a
        # unit vector along an axis, the last three a corner of the box.
        params = _try(surface, "PlaneParams")
        if params is not None and len(params) >= 6:
            normal = (float(params[0]), float(params[1]), float(params[2]))
            return PickedPlane(normal=normal, root=_in_mm(params[3:6]))
        return None

    # A reference plane arrives as a feature, and carries its frame as a
    # transform rather than as parameters. Its local Z is the normal, which is
    # read by sending the local origin and a step along Z through the same
    # transform rather than by picking the rotation apart.
    data = _try(_try(_try(obj, "GetSpecificFeature2"), "Transform"), "ArrayData")
    if data is None or len(data) < 12:
        return None
    root = transform_point(data, (0.0, 0.0, 0.0))
    tip = transform_point(data, (0.0, 0.0, 1.0))
    return PickedPlane(
        normal=(tip[0] - root[0], tip[1] - root[1], tip[2] - root[2]),
        root=_in_mm(root),
    )


def _as_line(manager: Any, index: int, obj: Any) -> Optional[PickedLine]:
    # An edge and a sketch segment both answer GetCurve, and that is what keeps
    # arcs and splines out: only a straight one says IsLine. They part company
    # over their ends — an edge has vertices, a sketch segment has points — so
    # both are tried, and the sketch is not reached by falling off the edge.
    curve = _try(obj, "GetCurve")
    if curve is None or not _try(curve, "IsLine"):
        return None

    start = _try(_try(obj, "GetStartVertex"), "GetPoint")
    end = _try(_try(obj, "GetEndVertex"), "GetPoint")
    if start is not None and end is not None:
        return PickedLine(start=_in_mm(start), end=_in_mm(end))

    to_model = _sketch_transform(manager, index, obj)
    start = _sketch_coords(_try(obj, "GetStartPoint2"), to_model)
    end = _sketch_coords(_try(obj, "GetEndPoint2"), to_model)
    if start is None or end is None:
        return None
    return PickedLine(start=start, end=end)


def _as_point(manager: Any, index: int, obj: Any) -> Optional[PickedPoint]:
    where = _try(obj, "GetPoint")
    if where is not None and len(where) >= 3:
        return PickedPoint(where=_in_mm(where))

    coords = _sketch_coords(obj, _sketch_transform(manager, index, obj))
    return None if coords is None else PickedPoint(where=coords)


def _sketch_transform(manager: Any, index: int, obj: Any) -> Optional[Sequence[float]]:
    """Sketch coordinates to model coordinates, if this thing is in a sketch."""
    sketch = _try(manager, "GetSelectedObjectsSketch", index) or _try(obj, "GetSketch")
    inverse = _try(_try(sketch, "ModelToSketchTransform"), "Inverse")
    return _try(inverse, "ArrayData")


def _sketch_coords(obj: Any, to_model: Optional[Sequence[float]]) -> Optional[Vec3]:
    x, y, z = _try(obj, "X"), _try(obj, "Y"), _try(obj, "Z")
    if x is None or y is None or z is None or to_model is None:
        return None
    return _in_mm(transform_point(to_model, (float(x), float(y), float(z))))


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

    # -- folders in the tree -----------------------------------------------
    #
    # Three calls, and one of them is a deletion, so: deleting a folder deletes
    # the folder and nothing else. Its features stay exactly where they were,
    # in the order they were in. That is what makes the arrangement rebuildable
    # instead of something to be patched feature by feature -- which is just as
    # well, because MoveToFolder answers False on every argument it was offered.

    def folders(self) -> Dict[str, List[str]]:
        """Every tree folder, and the names it holds, one level deep.

        The tree is read along the top-level chain rather than through
        :meth:`_walk`, which also descends into subfeatures: a sketch absorbed
        into a feature would land in the middle of a folder's contents and the
        nesting would stop adding up.
        """
        out: Dict[str, List[str]] = {}
        stack: List[str] = []
        feature = call(self._active(), "FirstFeature")
        guard = 0
        while feature is not None and guard < 5000:
            guard += 1
            name = str(call(feature, "Name"))
            try:
                type_name = str(call(feature, "GetTypeName2"))
            except Exception:  # noqa: BLE001 - a feature that will not describe itself
                type_name = "?"
            feature = call(feature, "GetNextFeature")

            if type_name != FOLDER_TYPE_NAME:
                if stack:
                    out[stack[-1]].append(name)
                continue
            if name.endswith(FOLDER_END_TAG):
                if stack:
                    stack.pop()
                continue
            if stack:
                out[stack[-1]].append(name)
            out.setdefault(name, [])
            stack.append(name)
        return out

    def insert_folder(self, names: Sequence[str], folder: str) -> str:
        """Wrap features in a new folder, and report the name it kept."""
        if not names:
            raise SolidWorksError("A folder has to be made around something.")
        doc = self._active()
        call(doc, "ClearSelection2", True)
        for position, name in enumerate(names):
            if not call(self._curve_feature(name), "Select2", position > 0, 0):
                raise SolidWorksError(f"{name} could not be selected.")
        made = call(call(doc, "FeatureManager"), "InsertFeatureTreeFolder2",
                    FOLDER_CONTAINING)
        call(doc, "ClearSelection2", True)
        if made is None or made is False:
            raise SolidWorksError(f"SolidWorks would not make a folder for {folder}.")
        made.Name = folder
        return str(call(made, "Name"))

    def delete_folder(self, folder: str) -> None:
        """Remove the folder, leaving everything it held where it was."""
        doc = self._active()
        call(doc, "ClearSelection2", True)
        if not call(self._curve_feature(folder), "Select2", False, 0):
            raise SolidWorksError(f"{folder} could not be selected.")
        deleted = call(call(doc, "Extension"), "DeleteSelection2", 0)
        call(doc, "ClearSelection2", True)
        if not deleted:
            raise SolidWorksError(f"SolidWorks would not remove the folder {folder}.")

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

    # -- documents ------------------------------------------------------------

    def open_part(self, path: str) -> DocInfo:
        """Open a part (or bring it forward if it is open already) and make it active."""
        path = os.path.abspath(path)
        errors, warnings = _out_long(), _out_long()
        doc = call(self._app, "OpenDoc6", path, SW_DOC_PART, OPEN_SILENT, "", errors, warnings)
        if doc is None:
            raise SolidWorksError(f"SolidWorks would not open {path} (error {errors.value}).")
        title = str(call(doc, "GetTitle"))
        call(self._app, "ActivateDoc3", title, False, 0, _out_long())
        active = self.active_document()
        if active is None or os.path.normcase(active.path) != os.path.normcase(path):
            raise SolidWorksError(f"{title} opened, but did not become the active document.")
        return active

    def close_document(self, title: str) -> None:
        """Close a document without saving it."""
        call(self._app, "CloseDoc", title)

    def save(self) -> None:
        """Save the active document where it already is."""
        doc = self._active()
        errors, warnings = _out_long(), _out_long()
        if not call(doc, "Save3", SAVE_SILENT, errors, warnings):
            raise SolidWorksError(f"SolidWorks would not save {call(doc, 'GetTitle')} (error {errors.value}).")

    # -- lofts and bodies -----------------------------------------------------
    #
    # A loft reads its inputs from the selection, the way a composite curve
    # does: its profiles at mark 1, in order, and its guide curves at mark 2.
    # The settings copied here are the ones a loft made by hand in the Loft
    # property page carries, read back off such a loft rather than assumed.

    def features_of_type(self, *type_names: str) -> List[str]:
        return [f.name for f in self.features() if f.type_name in type_names]

    def insert_loft(
        self,
        profiles: Sequence[str],
        guides: Sequence[str],
        name: str,
        merge: bool = False,
        keep_tangency: bool = True,
        guide_influence: int = GUIDE_TO_NEXT_GUIDE,
    ) -> str:
        """A solid loft through ``profiles`` in order, held by ``guides``, named ``name``."""
        if len(profiles) < 2:
            raise SolidWorksError("A loft needs at least two profiles.")
        doc = self._active()
        extension = call(doc, "Extension")
        call(doc, "ClearSelection2", True)
        picks = [(p, LOFT_PROFILE_MARK) for p in profiles] + [(g, LOFT_GUIDE_MARK) for g in guides]
        for position, (curve, mark) in enumerate(picks):
            selected = call(
                extension, "SelectByID2", curve, "REFERENCECURVES",
                0.0, 0.0, 0.0, position > 0, mark, _null_dispatch(), 0,
            )
            if not selected:
                call(doc, "ClearSelection2", True)
                raise SolidWorksError(f"{curve} could not be selected for the loft.")

        before = set(self.feature_names())
        made = call(
            call(doc, "FeatureManager"), "InsertProtrusionBlend2",
            False,           # Closed
            keep_tangency,   # KeepTangency: "Maintain tangency" in the page
            False,           # ForceNonRational
            1.0,             # TessToleranceFactor
            0, 0,            # start and end constraints: none
            1.0, 1.0,        # tangent lengths, unused with no constraint
            False, False,    # tangent directions, likewise
            False, 0.0, 0.0, 0,  # not a thin feature
            merge,
            False, True,     # feature scope: every body
            guide_influence,
        )
        call(doc, "ClearSelection2", True)
        if made is None or made is False:
            raise SolidWorksError(
                f"SolidWorks would not loft {' to '.join(profiles)} along {len(guides)} guide curve(s)."
            )
        created = [n for n in self.feature_names() if n not in before]
        if len(created) != 1:
            raise SolidWorksError(
                f"The loft added {len(created)} features, so which one it is cannot be told."
            )
        return self.rename_feature(created[0], name)

    def delete_feature(self, name: str) -> None:
        """Delete one feature, leaving what it was built from."""
        doc = self._active()
        call(doc, "ClearSelection2", True)
        if not call(self._curve_feature(name), "Select2", False, 0):
            raise SolidWorksError(f"{name} could not be selected.")
        deleted = call(call(doc, "Extension"), "DeleteSelection2", 0)
        call(doc, "ClearSelection2", True)
        if not deleted:
            raise SolidWorksError(f"SolidWorks would not delete {name}.")

    def set_suppressed(self, name: str, suppressed: bool) -> None:
        action = SUPPRESS if suppressed else UNSUPPRESS
        feature = self._curve_feature(name)
        if not call(feature, "SetSuppression2", action, THIS_CONFIGURATION, None):
            raise SolidWorksError(
                f"SolidWorks would not {'suppress' if suppressed else 'unsuppress'} {name}."
            )

    def export_step(self, path: str) -> None:
        """Write every visible body of the active part to a STEP file, as a copy.

        The part stays the open document under its own name.
        """
        doc = self._active()
        path = os.path.abspath(path)
        errors, warnings = _out_long(), _out_long()
        saved = call(
            call(doc, "Extension"), "SaveAs", path, 0, SAVE_SILENT | SAVE_AS_COPY,
            _null_dispatch(), errors, warnings,
        )
        if not saved or not os.path.exists(path):
            raise SolidWorksError(f"SolidWorks would not write {path} (error {errors.value}).")

    # -- reading what the user clicked --------------------------------------

    def editing_sketch(self) -> bool:
        """Is a sketch open for editing in the active document?

        Selecting and deselecting from outside while one is open can crash
        SolidWorks outright, so the operations that must select things refuse
        to start then.
        """
        doc = self._active()
        return _try(_try(doc, "SketchManager"), "ActiveSketch") is not None

    def read_selection(self) -> Optional[Picked]:
        """What was most recently selected, read and left exactly as it is.

        **Never clear the selection here.** This is called while the user is
        clicking in SolidWorks — often in a sketch, with the Point property
        page open on the very point just clicked. ``ClearSelection2`` then
        pulls that point out from under the page, and SolidWorks 2026 dies with
        an access violation in its own ``ClearSelectionsNotify``: twice, on
        2026-09-16, both times with this read and that clear as the last API
        calls. Telling a new click from one still selected is done by the
        caller instead, by noticing when the selection changes.

        ``None`` means nothing is selected.
        """
        doc = self._active()
        manager = call(doc, "SelectionManager")
        if manager is None:
            return None
        count = int(call(manager, "GetSelectedObjectCount2", -1) or 0)
        if count < 1:
            return None
        # The last one is the latest click, when several are held with Ctrl.
        return _interpret(manager, count)


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


def probe_selection(session: "Session") -> None:
    """Print what is selected in SolidWorks, and every way of reading it.

    Run as ``python -m airfoil_converter.swcom --selection`` with something
    clicked. The pick works out what was clicked by asking the object what
    members it has, so it does not depend on any of these numbers — but two
    layouts underneath it are conventions rather than deductions, and this is
    what settles them on a real machine: whether a plane's parameters run
    normal-then-root, and whether a transform's rotation is stored by rows.
    A face reports its normal both ways at once, so a reference plane read that
    disagrees with a flat face on the same plane is the transform being
    transposed.
    """
    doc = call(session._app, "ActiveDoc")
    if doc is None:
        print("no active document, so nothing can be selected")
        return
    manager = call(doc, "SelectionManager")
    count = int(call(manager, "GetSelectedObjectCount2", -1) or 0)
    print(f"\nselected: {count}")
    if count < 1:
        print("Click something in SolidWorks and run this again.")
        return

    for index in range(1, count + 1):
        type_id = int(_try(manager, "GetSelectedObjectType3", index, -1) or 0)
        name = SELECTION_NAMES.get(type_id, "not in the table")
        obj = call(manager, "GetSelectedObject6", index, -1)
        print(f"\n  [{index}] type={type_id} ({name})")
        print(f"      read as: {_interpret(manager, index)}")

        surface = _try(obj, "GetSurface")
        if surface is not None:
            print(f"      IsPlane={_try(surface, 'IsPlane')}  PlaneParams={_try(surface, 'PlaneParams')}")
        curve = _try(obj, "GetCurve")
        if curve is not None:
            print(f"      IsLine={_try(curve, 'IsLine')}  LineParams={_try(curve, 'LineParams')}")
        for member in ("GetPoint", "X", "Y", "Z"):
            value = _try(obj, member)
            if value is not None:
                print(f"      {member}={value}")
        specific = _try(obj, "GetSpecificFeature2")
        if specific is not None:
            data = _try(_try(specific, "Transform"), "ArrayData")
            print(f"      GetSpecificFeature2 Transform.ArrayData={data}")
        sketch = _try(manager, "GetSelectedObjectsSketch", index) or _try(obj, "GetSketch")
        if sketch is not None:
            forward = _try(_try(sketch, "ModelToSketchTransform"), "ArrayData")
            inverse = _try(_try(_try(sketch, "ModelToSketchTransform"), "Inverse"), "ArrayData")
            print(f"      ModelToSketchTransform={forward}")
            print(f"      its Inverse={inverse}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Report what this machine's SolidWorks looks like from here.

    Run as ``python -m airfoil_converter.swcom``. Everything the live link
    depends on is printed by this one command, which is what makes a failure
    somewhere later cheap to place. Add ``--selection`` to have it describe
    what is clicked in SolidWorks instead of the curves in the part.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
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
    if "--selection" in arguments:
        probe_selection(session)
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
