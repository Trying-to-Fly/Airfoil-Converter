"""Wings: check one, build it, and plan its loft.

Check and build are the Wing tab's Check and Export with SolidWorks not there
to take the curves: the same :func:`wing_build.build_wing` on a copy of the
project's record, the curves written by :func:`swlink.write_files`, and the
record replaced the way ``WingTab._remember`` replaces it. As in the window, a
wing's record is only ever written by a build, settings, folder and curves
together; a check tries the same settings and saves nothing.
"""

from __future__ import annotations

import copy
import dataclasses
import math
import os
import uuid
from typing import Annotated, Any, Dict, List, Optional, Sequence

from pydantic import Field

from .. import export, store, swlink, swloft, wing, wing_build, writer
from ..export import InputError, WingSpec
from .server import (
    ServerContext,
    add_tool,
    point_list,
    rounded,
    settings_from,
    vector,
)
from .tools_section import ProjectPath, IncludePoints, find_rib

# Where along the span the planform is sampled, as fractions root to tip.
PLANFORM_SAMPLES = (0.0, 0.25, 0.5, 0.75, 1.0)


def find_wing(sidecar: store.Sidecar, key: str) -> store.WingRecord:
    """A wing by its id, or by its name (``wing``, ``wing_2``) or stem."""
    record = sidecar.find_wing(key)
    if record is not None:
        return record
    named = [w for w in sidecar.wings if export.wing_base(w.stem, w.name_index) == key]
    if not named:
        named = [w for w in sidecar.wings if w.stem == key]
    if len(named) == 1:
        return named[0]
    wings = ", ".join(f"{export.wing_base(w.stem, w.name_index)} ({w.export_id})"
                      for w in sidecar.wings) or "none"
    if named:
        raise InputError(f"{key} names {len(named)} wings; give its id or full name. Wings: {wings}.")
    raise InputError(f"This project has no wing {key}. Wings: {wings}.")


def loft_sections(record: store.WingRecord) -> int:
    """``WingTab._loft_sections``: how many profiles an edit of an offset wing keeps."""
    return record.station_count if record.station_count > 2 else 0


def left_behind(record: store.WingRecord, curves, joins) -> List[str]:
    """``wing_tab.left_behind``: the curves a new export of the wing no longer makes."""
    wanted = {c.feature for c in curves} | {name for _, name in joins}
    return [c.feature for c in record.live_curves() if c.feature not in wanted]


def wing_settings(ctx: ServerContext, proj, wing_key: Optional[str],
                  ribs: Optional[Sequence[str]], name: Optional[str],
                  spec: Optional[Dict[str, Any]], folder: Optional[str], make: bool):
    """Everything a check or a build of a wing needs, read off the call and the record.

    ``WingTab._prepare`` with the form's fields given here instead: the wing
    being edited, or a new one by name; its settings laid over the record's,
    or over the defaults for a new wing. Nothing is saved. Returns the record
    (None for a new wing), its name and index, the settings, and its folder.
    """
    sidecar = proj.sidecar
    record = find_wing(sidecar, wing_key) if wing_key else None
    name = (name or "").strip()
    if record is None:
        if not name:
            raise InputError("Give the wing a name, or name the wing to build again.")
        stem = name
    else:
        stem = record.stem
        if name and name != stem:
            raise InputError(
                f"A wing keeps the name it was made with ({stem}). Leave out the wing "
                f"to make a new one called {name}."
            )

    settings = settings_from(WingSpec, spec, record.spec if record else None, skip=("ribs",))
    if ribs is not None:
        rib_ids = tuple(find_rib(sidecar, key).export_id for key in ribs)
    else:
        rib_ids = record.spec.ribs if record else ()
    if len(rib_ids) < 2:
        raise InputError("A wing needs at least two ribs: the root and the tip.")
    if len(set(rib_ids)) != len(rib_ids):
        raise InputError("A rib is listed twice.")

    edges = {}
    for key, what in (("le_source", "leading-edge curve"), ("te_source", "trailing-edge curve")):
        path = getattr(settings, key)
        if path:
            path = ctx.readable(path, what)
            if not os.path.isfile(path):
                raise InputError(f"The {what} file is not there: {ctx.show(path)}")
        edges[key] = path
    settings = dataclasses.replace(settings, ribs=rib_ids, **edges)
    for value, allowed, what in (
        (settings.root_end, wing.END_KINDS, "root end"),
        (settings.tip_end, wing.END_KINDS, "tip end"),
        (settings.thickness, export.THICKNESS_CHOICES, "thickness rule"),
        (settings.offset_dir, export.OFFSET_DIRECTIONS, "offset direction"),
        (settings.extension, writer.EXTENSIONS, "extension"),
    ):
        if value not in allowed:
            raise InputError(f"Unknown {what} {value!r}: use one of {', '.join(allowed)}.")
    settings.offset_mm()

    if folder:
        out = folder
    elif record is not None and record.output_folder:
        out = record.output_folder
    else:
        # As the Wing tab fills its folder in: from the first rib that has one.
        out = next((r.output_folder for r in sidecar.exports if r.output_folder),
                   os.path.dirname(proj.part_path))
    out = ctx.output_folder(out, make=make)
    index = record.name_index if record is not None else sidecar.next_index(stem)
    return record, stem, index, settings, out


def stand_up(ctx: ServerContext, sidecar: store.Sidecar, record: Optional[store.WingRecord],
             spec: WingSpec, stem: str, index: int, check: bool) -> wing_build.WingBuild:
    """``build_wing`` as the Wing tab calls it, on a copy of the record.

    Every path the build opens is resolved first, on the copy: a rib's source
    as a record stores it, or an edge curve, is checked against the workspace
    and the read roots and then handed over resolved, never as it was stored.
    """
    snapshot = copy.deepcopy(sidecar)
    for rib_id in spec.ribs:
        rib = snapshot.find(rib_id)
        if rib is not None and rib.source:
            rib.source = ctx.readable(
                rib.source, f"source of {export.folder_name(rib.stem, rib.name_index)}"
            )
    spec = dataclasses.replace(
        spec,
        le_source=ctx.readable(spec.le_source, "leading-edge curve") if spec.le_source else "",
        te_source=ctx.readable(spec.te_source, "trailing-edge curve") if spec.te_source else "",
    )
    return wing_build.build_wing(
        spec, snapshot, stem, index, check=check,
        own_names=record.feature_names() if record is not None else (),
        sections=loft_sections(record) if record is not None else 0,
    )


def foresee(record: Optional[store.WingRecord], made: wing_build.WingBuild) -> Dict[str, Any]:
    """What writing this build over the wing's record would change, as the Wing tab
    asks before it does: the curves left behind, and a loft's profile count moving."""
    if record is None:
        return {"retires": [], "station_count_change": None}
    change = None
    if record.station_count and made.station_count and made.station_count != record.station_count:
        change = {"from": record.station_count, "to": made.station_count}
    return {"retires": left_behind(record, made.curves, made.joins),
            "station_count_change": change}


def _rib_name(sidecar: store.Sidecar, rib_id: str) -> str:
    record = sidecar.find(rib_id)
    return export.folder_name(record.stem, record.name_index) if record else rib_id


def _chord(sec: wing.Section) -> float:
    return math.hypot(sec.te[0] - sec.le[0], sec.te[1] - sec.le[1])


def report(build: wing_build.WingBuild) -> Dict[str, Any]:
    """What a check or a build says about a wing: the Result panel's lines, and numbers."""
    model = build.model
    loft = model.loft
    samples = []
    for fraction in PLANFORM_SAMPLES:
        s = loft.start + (loft.end - loft.start) * fraction
        le, te = loft.le_guide.at(s), loft.te_guide.at(s)
        samples.append({
            "station_mm": rounded(s),
            "leading_edge": vector(le),
            "trailing_edge": vector(te),
            "chord_mm": rounded(math.hypot(te[0] - le[0], te[1] - le[1])),
            "le_sweep_deg": rounded(loft.le_guide.sweep(s), 4),
            "te_sweep_deg": rounded(loft.te_guide.sweep(s), 4),
        })
    answer: Dict[str, Any] = {
        "describe": wing_build.describe(build),
        "station_count": build.station_count,
        "sections": build.section_names,
        "guides": build.edge_names,
        "curve_count": len(build.curves),
        "joins": [{"name": name, "curves": list(sources)} for sources, name in build.joins],
        "te_mode": model.te_mode,
        "te_thickness_mm": rounded(model.te_thickness),
        "planform": {
            "coordinates": (
                "station along the span from the root rib's nose; across it (a, b): a along "
                "the root rib's chord, b square to it; mm"
            ),
            "span_mm": rounded(model.span),
            "root_chord_mm": rounded(_chord(model.sections[0])),
            "tip_chord_mm": rounded(_chord(model.sections[-1])),
            "ribs": [
                {"name": sec.name, "station_mm": rounded(sec.station), "chord_mm": rounded(_chord(sec))}
                for sec in model.sections
            ],
            "samples": samples,
        },
        "offset": None,
    }
    result = build.offset
    if result is not None:
        wall = result.report
        answer["offset"] = {
            "offset_mm": rounded(result.offset),
            "sections": len(result.sections),
            "rim_sections": sum(1 for sec in result.sections if sec.station.rim),
            "steep_from_mm": None if result.steep_from is None else rounded(result.steep_from),
            "te_shift_mm": vector(result.te_shift),
            "wall": None if wall is None else {
                "thinnest_mm": rounded(wall.thinnest),
                "thickest_mm": rounded(wall.thickest),
                "thinnest_at": [rounded(wall.thinnest_at[0]), wall.thinnest_at[1]],
                "thickest_at": [rounded(wall.thickest_at[0]), wall.thickest_at[1]],
                "points_outside": wall.outside,
                "points_checked": wall.checked,
            },
        }
    return answer


def check(ctx: ServerContext, project: str, wing_key: Optional[str],
          ribs: Optional[Sequence[str]], name: Optional[str], spec: Optional[Dict[str, Any]],
          folder: Optional[str]) -> Dict[str, Any]:
    with ctx.lock:
        proj = ctx.open_project(project, read_only=True)
        record, stem, index, settings, out = wing_settings(
            ctx, proj, wing_key, ribs, name, spec, folder, make=False)
        made = stand_up(ctx, proj.sidecar, record, settings, stem, index, check=True)
    answer = {
        "wing": record.export_id if record is not None else None,
        "name": export.wing_base(stem, index),
        "new": record is None,
        "folder": ctx.show(out),
        "ribs": [{"rib": r, "name": _rib_name(proj.sidecar, r)} for r in settings.ribs],
        "settings": settings.to_dict(),
    }
    answer.update(report(made))
    answer.update(foresee(record, made))
    return answer


def remember(sidecar: store.Sidecar, record: Optional[store.WingRecord],
             build: wing_build.WingBuild, spec: WingSpec, stem: str, folder: str, index: int,
             hashes: Dict[str, str], retire: Sequence[str] = ()) -> store.WingRecord:
    """Replace the wing's record with what this export made of it.

    ``WingTab._remember`` with nothing pushed: no join has been made, since only
    SolidWorks makes them. Kept in step with it so a record from here is the
    window's record.
    """
    stamp = store.now()
    fresh = store.wing_record_from_export(
        wing_id=record.export_id if record else f"wing-{uuid.uuid4().hex[:8]}",
        curves=build.curves,
        spec=spec,
        stem=stem,
        folder=folder,
        index=index,
        hashes=hashes,
        station_count=build.station_count,
    )
    for curve in fresh.curves:
        curve.last_pushed = store.push_stamp(curve.feature, stamp, False, (), record)
    if record is not None:
        fresh.created = record.created or stamp
        known = {c.feature for c in fresh.curves}
        retiring = set(retire)
        for c in record.curves:
            if c.feature in known:
                continue
            if c.feature in retiring:
                fresh.curves.append(dataclasses.replace(c, retired=True))
            elif c.retired or c.is_derived:
                fresh.curves.append(c)
        sidecar.wings[sidecar.wings.index(record)] = fresh
    else:
        sidecar.wings.append(fresh)
    return fresh


def loft_plan(ctx: ServerContext, record: store.WingRecord, sidecar: store.Sidecar,
              joins: Sequence) -> Dict[str, Any]:
    """The loft a CAD bridge makes of the wing: :func:`swloft.plan_for_wing`, and the files.

    A section closed by a trailing-edge line is two curves, which the window
    joins in the part before lofting. Nothing here can, so the joins are listed,
    and the profiles are given both as recorded and as they read once joined.
    """
    plan = swloft.plan_for_wing(record, sidecar)
    joined = {source: name for sources, name in joins for source in sources}
    files = {}
    for curve in record.written_curves():
        files[curve.feature] = ctx.show(store.resolve_file(curve, record.output_folder))
    return {
        "name": plan.name,
        "profiles": list(plan.profiles),
        "profiles_once_joined": [joined.get(p, p) for p in plan.profiles],
        "guides": list(plan.guides),
        "joins": [{"name": name, "curves": list(sources)} for sources, name in joins],
        "keep_tangency": plan.keep_tangency,
        "guide_influence": plan.guide_influence,
        "files": files,
    }


def build(ctx: ServerContext, project: str, wing_key: Optional[str],
          ribs: Optional[Sequence[str]], name: Optional[str], spec: Optional[Dict[str, Any]],
          folder: Optional[str], include_points: bool) -> Dict[str, Any]:
    with ctx.lock:
        proj = ctx.open_project(project)
        sidecar = proj.sidecar
        record, stem, index, settings, out = wing_settings(
            ctx, proj, wing_key, ribs, name, spec, folder, make=True)
        made = stand_up(ctx, sidecar, record, settings, stem, index,
                        check=bool(settings.offset_mm()))
        ahead = foresee(record, made)
        warnings = []
        change = ahead["station_count_change"]
        if change:
            warnings.append(
                f"The wing's loft had {change['from']} profiles and now has {change['to']}. "
                "A loft picks its profiles one by one, so it will need them picked again."
            )
        ctx.check_targets([os.path.join(out, c.filename) for c in made.curves]
                          + [proj.sidecar_path])
        changed, hashes = swlink.write_files(made.curves, out)
        fresh = remember(sidecar, record, made, settings, stem, out, index, hashes,
                         retire=ahead["retires"])
        saved = ctx.save_project(proj)
        plan = loft_plan(ctx, fresh, sidecar, made.joins)

    curves = []
    for curve in made.curves:
        entry: Dict[str, Any] = {
            "role": curve.role,
            "feature": curve.feature,
            "file": ctx.show(os.path.join(out, curve.filename)),
            "point_count": len(curve.points),
            "closed": curve.closed,
            "changed": curve.feature in changed,
        }
        if include_points:
            entry.update(point_list(curve.points))
        curves.append(entry)
    answer = {
        "project": ctx.show(proj.part_path),
        "record": ctx.show(proj.sidecar_path),
        "record_changed": saved,
        "wing": fresh.export_id,
        "name": export.wing_base(fresh.stem, fresh.name_index),
        "new": record is None,
        "folder": ctx.show(out),
        "ribs": [{"rib": r, "name": _rib_name(sidecar, r)} for r in settings.ribs],
        "settings": fresh.settings,
        "warnings": warnings,
        "retired": ahead["retires"],
    }
    answer.update(report(made))
    answer["curves"] = curves
    answer["loft_plan"] = plan
    return answer


WingKeyArg = Annotated[Optional[str], Field(description=(
    "An existing wing, by id or name. Leave out for a new wing, which needs a name and ribs."
))]
RibsArg = Annotated[Optional[List[str]], Field(description=(
    "The ribs to loft through, by id or name, two at least, in any order. Leave out to "
    "keep an existing wing's own."
))]
NameArg = Annotated[Optional[str], Field(description=(
    "A new wing's name. A name already used gets the next index (wing, wing_2, ...)."
))]
WingSpecArg = Annotated[Optional[Dict[str, Any]], Field(description=(
    "WingSpec settings to change, by field name (see converter_info): le_source and "
    "te_source (a curve file, or '' for a straight line), root_end and tip_end, "
    "thickness, offset and offset_dir, swap_ends, extension. Not ribs. Fields left out "
    "keep an existing wing's own settings, or the defaults."
))]
FolderArg = Annotated[Optional[str], Field(description=(
    "Where the wing's curve files go, in the workspace. Defaults to the wing's own "
    "folder, or for a new wing the first rib's."
))]


def register(server: Any, ctx: ServerContext) -> None:
    def check_wing(project: ProjectPath, wing: WingKeyArg = None, ribs: RibsArg = None,
                   name: NameArg = None, spec: WingSpecArg = None,
                   folder: FolderArg = None) -> Dict[str, Any]:
        """Try a wing — an existing one, with any changes given, or a new one — and save
        nothing: what the window's Result panel says, the station count, the planform
        (span, root and tip chord, edge positions and sweep along the span), the wall an
        offset is expected to leave, and for an existing wing the curves a build would
        retire and whether its loft's profile count would change. A wing that cannot be
        built is refused with the reason. A record that cannot be read is left where it
        is."""
        return check(ctx, project, wing, ribs, name, spec, folder)

    def build_wing(project: ProjectPath, wing: WingKeyArg = None, ribs: RibsArg = None,
                   name: NameArg = None, spec: WingSpecArg = None, folder: FolderArg = None,
                   include_points: IncludePoints = False) -> Dict[str, Any]:
        """Build a wing and write it: its curve files — sections, edges and surface
        guides, or an offset wing's own — and its record in the project, settings, folder
        and curves together, as the Wing tab's Export does. The only tool that writes a
        wing's record. Returns what check_wing does, every curve's file, the curves it
        retired, and the loft plan: the profiles in order, root to tip, and the guides,
        so a CAD bridge can loft it without knowing the naming."""
        return build(ctx, project, wing, ribs, name, spec, folder, include_points)

    add_tool(server, check_wing, read_only=True, idempotent=True)
    add_tool(server, build_wing)
