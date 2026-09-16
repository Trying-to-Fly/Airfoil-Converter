"""What the app remembers about the curves it put into a part.

A Curve Through XYZ Points feature holds points and nothing else. The chord,
the plane, the angle of attack, the trailing-edge mode, the source file: none
of it survives into SolidWorks, and none of it can be read back out. So if
clicking a curve is ever going to put its settings back on the form, the app
has to remember them, and remember them somewhere that outlives it.

That somewhere is a JSON file beside the part. It travels with the model, one
part has one obvious file, and a part that has never been saved has nowhere to
put it — which is a real state, reported rather than papered over.

Nothing here touches COM. Reconciliation is a pure function of two lists, so
every state it can report is testable without SolidWorks running.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Collection, Dict, List, Optional, Sequence, Tuple

from .export import ROLE_JOINED, ROLES, Curve, ExportSpec, WingSpec

# A sidecar with wings in it is schema 2. One without is still written as 1,
# so a part that never had a wing still opens in a version that knows none.
SCHEMA_VERSION = 2
PLAIN_SCHEMA = 1
SIDECAR_SUFFIX = ".airfoils.json"


class StoreError(Exception):
    """The remembered curves could not be read or written."""


class UnknownSchema(StoreError):
    """Written by a newer version of the app than this one."""


# -- what a curve can be ----------------------------------------------------

LINKED = "linked"
RENAMED = "renamed"
MISSING = "missing"
NOT_PUSHED = "not pushed"
DRIFTED = "drifted"
RETIRED = "retired"
ORPHAN = "orphan"


@dataclass
class CurveRecord:
    role: str
    feature: str
    file: str
    points: int = 0
    closed: bool = False
    sha256: str = ""
    persist_ref: str = ""
    last_written: str = ""
    last_pushed: str = ""
    retired: bool = False

    @property
    def is_derived(self) -> bool:
        """SolidWorks builds this one from our curves; we write no file for it.

        A derived curve is never orphaned by a change of settings, because no
        export ever produces it directly.
        """
        return not self.file


@dataclass
class ExportRecord:
    """One press of Export: its settings, and the curves it produced."""

    export_id: str
    name_index: int = 1
    stem: str = ""
    source: str = ""
    source_sha256: str = ""
    output_folder: str = ""
    settings: Dict[str, Any] = field(default_factory=dict)
    loaded_section: Optional[Dict[str, Any]] = None
    curves: List[CurveRecord] = field(default_factory=list)
    created: str = ""
    # Built from the names in the part rather than from an export, so it knows
    # what its curves are called and nothing about how they were made.
    adopted: bool = False

    @property
    def spec(self) -> ExportSpec:
        return ExportSpec.from_dict(self.settings)

    def feature_names(self) -> List[str]:
        return [c.feature for c in self.curves]

    def live_curves(self) -> List[CurveRecord]:
        return [c for c in self.curves if not c.retired]

    def written_curves(self) -> List[CurveRecord]:
        """The curves an export actually writes, so derived ones are left out."""
        return [c for c in self.curves if not c.retired and not c.is_derived]


@dataclass
class WingRecord:
    """A wing: ribs, the edge curves lofting them, and any offset of the whole.

    Its id is kept under the same name as an export's, so everything that
    lines records up against the part treats the two alike.
    """

    export_id: str
    name_index: int = 1
    stem: str = ""
    output_folder: str = ""
    settings: Dict[str, Any] = field(default_factory=dict)
    curves: List[CurveRecord] = field(default_factory=list)
    # How many sections the offset wing had, so a change can be warned about:
    # a loft picks its sections one by one.
    station_count: int = 0
    created: str = ""

    @property
    def spec(self) -> WingSpec:
        return WingSpec.from_dict(self.settings)

    def feature_names(self) -> List[str]:
        return [c.feature for c in self.curves]

    def live_curves(self) -> List[CurveRecord]:
        return [c for c in self.curves if not c.retired]

    def written_curves(self) -> List[CurveRecord]:
        return [c for c in self.curves if not c.retired and not c.is_derived]


@dataclass
class Sidecar:
    part_path: str = ""
    part_title: str = ""
    # The version the file was read as; what gets written depends on its wings.
    schema_version: int = PLAIN_SCHEMA
    written_by: str = ""
    exports: List[ExportRecord] = field(default_factory=list)
    wings: List[WingRecord] = field(default_factory=list)

    def records(self) -> List[Any]:
        """Every record that claims curves in the part, ribs first."""
        return list(self.exports) + list(self.wings)

    def find_wing(self, wing_id: str) -> Optional[WingRecord]:
        for record in self.wings:
            if record.export_id == wing_id:
                return record
        return None

    def find(self, export_id: str) -> Optional[ExportRecord]:
        for record in self.exports:
            if record.export_id == export_id:
                return record
        return None

    def find_by_feature(self, feature: str) -> Optional[ExportRecord]:
        for record in self.exports:
            if feature in record.feature_names():
                return record
        return None

    def all_feature_names(self) -> List[str]:
        return [name for record in self.records() for name in record.feature_names()]

    def next_index(self, stem: str) -> int:
        """The lowest index no record of this name is already using.

        Ribs and wings share the count: both name a folder after their stem.
        """
        used = {r.name_index for r in self.records() if r.stem == stem}
        index = 1
        while index in used:
            index += 1
        return index


# -- where it lives ---------------------------------------------------------


def sidecar_path(part_path: str) -> str:
    """``C:\\parts\\Wing.SLDPRT`` -> ``C:\\parts\\Wing.airfoils.json``."""
    if not part_path:
        raise StoreError(
            "This part has never been saved, so there is nowhere to keep its "
            "curve settings yet. Save the part and they will stick."
        )
    stem, _ = os.path.splitext(part_path)
    return stem + SIDECAR_SUFFIX


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# -- reading and writing ----------------------------------------------------


def _relative(path: str, folder: str) -> str:
    """Store a file beside its sidecar as a relative path, so the job can move."""
    try:
        return os.path.relpath(path, folder).replace(os.sep, "/")
    except ValueError:  # a different drive on Windows
        return path


def resolve_file(record: CurveRecord, folder: str) -> str:
    return record.file if os.path.isabs(record.file) else os.path.join(folder, record.file)


def to_json(sidecar: Sidecar) -> str:
    """Byte-deterministic, so an unchanged sidecar is an unchanged file."""
    payload: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION if sidecar.wings else PLAIN_SCHEMA,
        "writtenBy": sidecar.written_by,
        "document": {"path": sidecar.part_path, "title": sidecar.part_title},
        "exports": [asdict(record) for record in sidecar.exports],
    }
    if sidecar.wings:
        payload["wings"] = [asdict(record) for record in sidecar.wings]
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def from_json(text: str) -> Sidecar:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise StoreError(f"The curve record could not be read: {exc}") from None
    if not isinstance(raw, dict):
        raise StoreError("The curve record is not an object.")

    version = raw.get("schemaVersion", 0)
    if not isinstance(version, int):
        raise StoreError("The curve record has no readable schema version.")
    if version > SCHEMA_VERSION:
        raise UnknownSchema(
            f"This part's curve record was written by a newer version of Airfoil "
            f"Converter (schema {version}, this one reads {SCHEMA_VERSION}). "
            "It is being left alone rather than overwritten."
        )

    document = raw.get("document") or {}
    exports = []
    for item in raw.get("exports") or []:
        curves = [CurveRecord(**_known(CurveRecord, c)) for c in item.get("curves") or []]
        fields = _known(ExportRecord, item)
        fields["curves"] = curves
        exports.append(ExportRecord(**fields))

    wings = []
    for item in raw.get("wings") or []:
        curves = [CurveRecord(**_known(CurveRecord, c)) for c in item.get("curves") or []]
        fields = _known(WingRecord, item)
        fields["curves"] = curves
        wings.append(WingRecord(**fields))

    return Sidecar(
        part_path=document.get("path", ""),
        part_title=document.get("title", ""),
        schema_version=version,
        written_by=raw.get("writtenBy", ""),
        exports=exports,
        wings=wings,
    )


def _known(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
    import dataclasses

    allowed = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in raw.items() if k in allowed and k != "curves"}


def load(path: str) -> Optional[Sidecar]:
    """Read a sidecar, or None when there is not one yet.

    A file that cannot be parsed is moved aside rather than deleted or
    overwritten: it is the only copy of settings someone may still want.
    """
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        return from_json(text)
    except UnknownSchema:
        raise
    except StoreError:
        spoiled = f"{path}.bad-{time.strftime('%Y%m%d-%H%M%S')}"
        os.replace(path, spoiled)
        raise StoreError(
            f"The curve record beside this part could not be read, so it was "
            f"moved to {os.path.basename(spoiled)} and a fresh one will be started."
        ) from None


def save(path: str, sidecar: Sidecar) -> bool:
    """Write the sidecar if it changed. Atomic, so it is never half-written."""
    text = to_json(sidecar)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if handle.read() == text:
                    return False
        except OSError:
            pass
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)
    return True


def record_from_export(
    export_id: str,
    curves: Sequence[Curve],
    spec: ExportSpec,
    stem: str,
    source: str,
    folder: str,
    index: int = 1,
    hashes: Optional[Dict[str, str]] = None,
    source_sha256: str = "",
    loaded_section: Optional[Dict[str, Any]] = None,
) -> ExportRecord:
    stamp = now()
    hashes = hashes or {}
    return ExportRecord(
        export_id=export_id,
        name_index=index,
        stem=stem,
        source=source,
        source_sha256=source_sha256,
        output_folder=folder,
        settings=spec.to_dict(),
        loaded_section=loaded_section,
        created=stamp,
        curves=[
            CurveRecord(
                role=curve.role,
                feature=curve.feature,
                file=curve.filename,
                points=len(curve.points),
                closed=curve.closed,
                sha256=hashes.get(curve.feature, ""),
                last_written=stamp,
            )
            for curve in curves
        ],
    )


def wing_record_from_export(
    wing_id: str,
    curves: Sequence[Curve],
    spec: WingSpec,
    stem: str,
    folder: str,
    index: int = 1,
    hashes: Optional[Dict[str, str]] = None,
    station_count: int = 0,
) -> WingRecord:
    stamp = now()
    hashes = hashes or {}
    return WingRecord(
        export_id=wing_id,
        name_index=index,
        stem=stem,
        output_folder=folder,
        settings=spec.to_dict(),
        station_count=station_count,
        created=stamp,
        curves=[
            CurveRecord(
                role=curve.role,
                feature=curve.feature,
                file=curve.filename,
                points=len(curve.points),
                closed=curve.closed,
                sha256=hashes.get(curve.feature, ""),
                last_written=stamp,
            )
            for curve in curves
        ],
    )


# -- taking over curves that are already there ------------------------------
#
# Two ways a part ends up holding curves nothing remembers. It was never saved
# when they were made, so there was nowhere to write the record; or the record
# was lost. Either way the curves are still ours, and their names say so: the
# naming scheme is the only thing that survived, so it is what they are read
# back from. Nothing here can recover the settings — those existed only in the
# record — so an adopted record says plainly that it has none.


def parse_feature(name: str) -> Optional[Tuple[str, str, int]]:
    """``rib_airfoil_te_2`` -> ``("rib", "airfoil_te", 2)``, or None.

    None means the name was not made by this app, which is the answer that
    matters most: somebody else's curve must never be claimed.
    """
    head, separator, tail = name.rpartition("_")
    index = 1
    if separator and tail.isdigit() and int(tail) >= 2:
        name, index = head, int(tail)
    # Longest first, so ``airfoil_te`` is never read as ``airfoil``.
    for role in sorted(ROLES, key=len, reverse=True):
        suffix = "_" + role
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            if stem:
                return stem, role, index
    return None


@dataclass(frozen=True)
class Adoption:
    """One export's worth of untracked curves, as read back from their names."""

    stem: str
    index: int
    curves: Tuple[Tuple[str, str], ...] = ()  # (feature, role), in document order


def adoptable(features: Sequence[str], known: Collection[str] = ()) -> List[Adoption]:
    """Group the curves nothing tracks by the export that would have made them."""
    groups: Dict[Tuple[str, int], List[Tuple[str, str]]] = {}
    order: List[Tuple[str, int]] = []
    for name in features:
        if name in known:
            continue
        parsed = parse_feature(name)
        if parsed is None:
            continue
        stem, role, index = parsed
        key = (stem, index)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((name, role))
    return [Adoption(stem, index, tuple(groups[(stem, index)])) for stem, index in order]


def adopt(
    sidecar: Sidecar,
    features: Sequence[str],
    folder: str = "",
    extension: str = ".sldcrv",
) -> List[ExportRecord]:
    """Make a record for every group of untracked curves, and add them.

    The file a curve would be written to is recorded whether or not it is there
    now: it is where a refresh will put it, and naming it is what lets the
    record be exported over. The joined curve is the exception — SolidWorks
    derives it from two of ours and no file is ever written for it.
    """
    known = set(sidecar.all_feature_names())
    made: List[ExportRecord] = []
    for group in adoptable(features, known):
        curves = []
        for feature, role in group.curves:
            filename = "" if role == ROLE_JOINED else feature + extension
            digest = ""
            if filename and folder:
                path = os.path.join(folder, filename)
                if os.path.exists(path):
                    digest = _file_hash(path)
            curves.append(
                CurveRecord(role=role, feature=feature, file=filename, sha256=digest)
            )
        record = ExportRecord(
            export_id=f"adopted-{uuid.uuid4().hex[:8]}",
            name_index=group.index,
            stem=group.stem,
            output_folder=folder,
            settings={},
            curves=curves,
            created=now(),
            adopted=True,
        )
        sidecar.exports.append(record)
        made.append(record)
    return made


def carry_over(
    sidecar: Sidecar,
    records: Sequence[ExportRecord],
    present: Sequence[str],
) -> List[ExportRecord]:
    """Move records made before the part had a path into the part's own file.

    A part that has never been saved has nowhere to keep a record, so the app
    holds it in memory. The moment it is saved there is somewhere, and losing
    the record at exactly that point would be the cruellest possible time.

    A record only moves if every curve it names is in the document — otherwise
    what happened was not a save but a different part being opened, and those
    curves have nothing to do with this one.
    """
    known = set(sidecar.all_feature_names())
    in_document = set(present)
    moved: List[ExportRecord] = []
    for record in records:
        names = [curve.feature for curve in record.live_curves()]
        if not names or any(name in known for name in names):
            continue
        if not all(name in in_document for name in names):
            continue
        sidecar.exports.append(record)
        known.update(names)
        moved.append(record)
    return moved


# -- reconciliation ---------------------------------------------------------


@dataclass(frozen=True)
class CurveState:
    feature: str
    state: str
    record: Optional[CurveRecord] = None
    export_id: str = ""
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.state in (MISSING, DRIFTED, ORPHAN, NOT_PUSHED)


def reconcile(
    sidecar: Sidecar,
    document_features: Sequence[str],
    folder: str = "",
    renamed: Optional[Dict[str, str]] = None,
) -> List[CurveState]:
    """Line the remembered curves up against the ones in the document.

    ``renamed`` maps a remembered feature name to what a durable reference says
    it is called now. Following that is the only way a rename shows as one true
    row instead of a phantom missing curve and a phantom stray one.
    """
    present = list(document_features)
    in_document = set(present)
    renamed = renamed or {}
    states: List[CurveState] = []
    claimed = set()

    for record in sidecar.records():
        for curve in record.curves:
            if curve.retired:
                states.append(CurveState(curve.feature, RETIRED, curve, record.export_id))
                claimed.add(curve.feature)
                continue

            name = curve.feature
            detail = ""
            if name not in in_document and name in renamed and renamed[name] in in_document:
                detail = f"renamed in SolidWorks to {renamed[name]}"
                claimed.add(renamed[name])
                states.append(CurveState(name, RENAMED, curve, record.export_id, detail))
                continue

            if name not in in_document:
                states.append(CurveState(name, MISSING, curve, record.export_id))
                continue

            claimed.add(name)
            state = LINKED
            path = resolve_file(curve, folder or record.output_folder)
            if curve.sha256 and folder is not None and os.path.exists(path):
                if _file_hash(path) != curve.sha256:
                    state = DRIFTED
                    detail = "this file was changed outside the app"
            if state == LINKED and curve.last_written and curve.last_pushed < curve.last_written:
                state = NOT_PUSHED
                detail = "the file is newer than the part"
            states.append(CurveState(name, state, curve, record.export_id, detail))

    for name in present:
        if name not in claimed:
            states.append(CurveState(name, ORPHAN, None, "", "in the part, not tracked"))

    return states


def _file_hash(path: str) -> str:
    from . import writer

    try:
        with open(path, "rb") as handle:
            return writer.sha256_hex(handle.read())
    except OSError:
        return ""
