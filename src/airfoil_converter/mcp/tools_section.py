"""One section: read it, finish it, export it as a rib, and see what a project holds.

Export does what the window's Export does when SolidWorks is not there to take
the curves: the same curves from :func:`export.build_curves`, written by
:func:`swlink.write_files`, and recorded the way ``ConverterApp._remember`` records
them — a new rib gets the next free name index, an existing one keeps its id,
name and creation time, and a curve its new settings no longer make is kept on
the record, retired, because it may still be in the part.
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from typing import Annotated, Any, Dict, Optional, Sequence

from pydantic import Field

from .. import export, geometry, store, swlink, writer
from ..export import ExportSpec, InputError
from ..geometry import GeometryError
from .server import (
    ServerContext,
    add_tool,
    bounding_box,
    distance,
    number_text,
    point_list,
    rounded,
    settings_from,
    vector,
)

Source = Annotated[str, Field(description=(
    "A plotter CSV or a curve file (.sldcrv/.txt), relative to the workspace "
    "or in a read root."
))]
ProjectPath = Annotated[str, Field(description=(
    "The project: a part's path relative to the workspace, such as 'wing.SLDPRT'. "
    "The part need not exist; its record is the '.airfoils.json' file beside it."
))]
Spec = Annotated[Optional[Dict[str, Any]], Field(description=(
    "ExportSpec settings to change, by field name (see converter_info). Fields left "
    "out keep an existing rib's own settings, or for a new section the defaults the "
    "window starts from when it loads one: the camber line written if the source has "
    "one, a curve file kept on its own plane and a CSV put on XY. One difference: the "
    "reference point defaults to 0 here, the leading edge, where the window's fresh "
    "form starts at 25, the quarter chord."
))]
IncludePoints = Annotated[bool, Field(description=(
    "Also return each curve's points, at the curve files' precision and capped."
))]


# -- reading -----------------------------------------------------------------


def load(ctx: ServerContext, source: str):
    """A source's path, as read; its data; and, for a curve file, the plane it stands on."""
    path = ctx.readable(source, "source")
    if not os.path.isfile(path):
        raise InputError(f"The source {ctx.show(path)} is not there.")
    return path, *read_source(ctx, path)


def read_source(ctx: ServerContext, path: str):
    """:func:`export.load_source`, with a file that is not text said to be so.

    The parser reads a CSV as UTF-8 and lets a file that is not decode as one
    fail the way Python fails it; here that is the source being wrong, not
    the server.
    """
    try:
        return export.load_source(path)
    except UnicodeDecodeError:
        raise InputError(
            f"{ctx.show(path)} is not a text file: give a plotter CSV or a curve file."
        ) from None


def fresh_spec(data, section) -> ExportSpec:
    """The settings a new section starts from, as the window's form takes them on a load.

    ``ConverterApp._load`` turns the camber line off for a source without one
    and puts a curve file on its own plane. Everything else is ExportSpec's
    own default, including the reference point at the leading edge.
    """
    return ExportSpec(
        export_camber=bool(data.camber),
        plane_mode=export.MODE_LOADED if section is not None else "XY",
    )


def new_settings(data, section, raw: Optional[Dict[str, Any]]) -> ExportSpec:
    """A new section's settings: the caller's laid over :func:`fresh_spec`.

    Where the reference point lands follows the plane, as the window's form
    fills it in until it is typed (``ConverterApp._sync_leading_edge``): on a
    curve file's own plane, wherever keeps the curve where it was drawn; on a
    plane through points, P1. Given, it is the caller's. The window shows the
    filled-in point to six figures; here it keeps every digit, so the curve
    stays exactly where it was.
    """
    spec = settings_from(ExportSpec, raw, fresh_spec(data, section))
    if raw and "leading_edge" in raw:
        return spec
    if spec.plane_mode == export.MODE_LOADED and section is not None:
        point = export.reference_point(data, spec, section, section.origin)
        return dataclasses.replace(spec, leading_edge=tuple(number_text(c) for c in point))
    if spec.plane_mode in (export.MODE_3POINTS, export.MODE_2POINTS, export.MODE_NORMAL):
        return dataclasses.replace(spec, leading_edge=spec.p1)
    return spec


def source_stem(path: str) -> str:
    """What a new rib is called when no name is given: its file's own name, as in the window."""
    return os.path.splitext(os.path.basename(path))[0]


def read(ctx: ServerContext, source: str, include_points: bool = False) -> Dict[str, Any]:
    path, data, section = load(ctx, source)
    airfoil = data.airfoil
    nose = airfoil[geometry.leading_edge_index(airfoil)]
    first, last = airfoil[0], airfoil[-1]
    gap = distance(first, last)
    chord = data.chord
    station, thickness = geometry._thickest_station(geometry.drop_duplicate(airfoil))
    answer: Dict[str, Any] = {
        "source": ctx.show(path),
        "kind": "curve" if section is not None else "csv",
        "name": data.name,
        "metadata": dict(data.metadata),
        "sections": list(data.sections),
        "point_count": len(airfoil),
        "chord_mm": rounded(chord),
        "leading_edge": vector(nose),
        "trailing_edge": [vector(first), vector(last)],
        "te_gap_mm": rounded(gap),
        "closed": gap <= geometry.POINT_TOL,
        "max_thickness_mm": rounded(thickness),
        "max_thickness_percent": rounded(100.0 * thickness / chord, 4),
        "max_thickness_at_percent": rounded(100.0 * (station - nose[0]) / chord, 4),
        "camber_point_count": len(data.camber or []),
        "coordinates": (
            "2D, in mm, in the section's own plane: x along the chord from the nose, y up."
        ),
    }
    if section is not None:
        answer["plane"] = {
            "origin": vector(section.origin),
            "chord_direction": vector(section.u),
            "up_direction": vector(section.v),
            "normal": vector(geometry.cross(section.u, section.v)),
        }
    if include_points:
        answer["airfoil"] = point_list(airfoil)
        if data.camber:
            answer["camber"] = point_list(data.camber)
    return answer


# -- finishing ---------------------------------------------------------------


def shape(data, spec: ExportSpec) -> Dict[str, Any]:
    """The finished chord and trailing edge, measured on the surface the export cuts.

    Split, the two halves end at the two trailing-edge corners whatever mode is
    asked for, so the gap between their ends is the trailing edge itself.
    """
    if not spec.export_airfoil:
        return {}
    factor = geometry.scale_factor(data.chord, spec.target_chord_mm())
    try:
        halves = export.build_sections(
            data, dataclasses.replace(spec, te_mode=export.TE_SPLIT, export_camber=False)
        )
    except GeometryError:
        return {}
    upper, lower = halves[0][1], halves[1][1]
    return {
        "chord_mm": rounded(geometry.chord_span(list(upper) + list(lower)) * factor),
        "te_thickness_mm": rounded(distance(upper[-1], lower[-1]) * factor),
        "scale": rounded(factor, 9),
    }


def curve_summary(curve: export.Curve, include_points: bool) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "role": curve.role,
        "feature": curve.feature,
        "file": curve.filename,
        "point_count": len(curve.points),
        "closed": curve.closed,
        "bounding_box": bounding_box(curve.points),
    }
    if not curve.closed:
        entry["ends_gap_mm"] = rounded(distance(curve.points[0], curve.points[-1]))
    if include_points:
        entry.update(point_list(curve.points))
    return entry


def finished(data, spec: ExportSpec, section, curves: Sequence[export.Curve],
             include_points: bool) -> Dict[str, Any]:
    nose, u, v = export.placement(data, spec, section)
    return {
        "settings": spec.to_dict(),
        **shape(data, spec),
        "placement": {
            "nose": vector(nose),
            "reference_point": vector(spec.vector("leading_edge")),
            "chord_direction": vector(u),
            "up_direction": vector(v),
        },
        "curves": [curve_summary(c, include_points) for c in curves],
    }


# -- the project's record ----------------------------------------------------


def find_rib(sidecar: store.Sidecar, key: str) -> store.ExportRecord:
    """A rib by its export id, or by its name (``root``, ``sd7037-il_2``) or stem."""
    record = sidecar.find(key)
    if record is not None:
        return record
    named = [r for r in sidecar.exports if export.folder_name(r.stem, r.name_index) == key]
    if not named:
        named = [r for r in sidecar.exports if r.stem == key]
    if len(named) == 1:
        return named[0]
    ribs = ", ".join(f"{export.folder_name(r.stem, r.name_index)} ({r.export_id})"
                     for r in sidecar.exports) or "none"
    if named:
        raise InputError(f"{key} names {len(named)} ribs; give its id or full name. Ribs: {ribs}.")
    raise InputError(f"This project has no rib {key}. Ribs: {ribs}.")


def remember(sidecar: store.Sidecar, record: Optional[store.ExportRecord],
             curves: Sequence[export.Curve], spec: ExportSpec, stem: str, source: str,
             folder: str, index: int, hashes: Dict[str, str],
             loaded_section: Optional[Dict[str, Any]],
             retire: Sequence[str] = ()) -> store.ExportRecord:
    """Replace the rib's record with what this export made of it.

    ``ConverterApp._remember`` with nothing pushed: no curve has reached a part,
    and the joined curve, which only SolidWorks makes, is not made. Kept in
    step with it so a record from here is the window's record.
    """
    stamp = store.now()
    fresh = store.record_from_export(
        export_id=record.export_id if record else f"exp-{uuid.uuid4().hex[:8]}",
        curves=curves,
        spec=spec,
        stem=stem,
        source=source,
        folder=folder,
        index=index,
        hashes=hashes,
        loaded_section=loaded_section,
    )
    for curve_record in fresh.curves:
        curve_record.last_pushed = store.push_stamp(curve_record.feature, stamp, False, (), record)
    if record is not None:
        fresh.created = record.created or stamp
        known = {c.feature for c in fresh.curves}
        for c in record.curves:
            if c.feature in known:
                continue
            if c.feature in retire:
                fresh.curves.append(dataclasses.replace(c, retired=True))
            elif c.retired:
                fresh.curves.append(c)
        sidecar.exports[sidecar.exports.index(record)] = fresh
    else:
        sidecar.exports.append(fresh)
    return fresh


def export_rib(ctx: ServerContext, project: str, source: Optional[str], rib: Optional[str],
               name: Optional[str], spec: Optional[Dict[str, Any]], folder: Optional[str],
               include_points: bool) -> Dict[str, Any]:
    with ctx.lock:
        proj = ctx.open_project(project, create=rib is None)
        sidecar = proj.sidecar
        record = find_rib(sidecar, rib) if rib else None

        if source:
            path, data, section = load(ctx, source)
        elif record is not None and record.source:
            path = ctx.readable(record.source, "rib's source")
            if not os.path.isfile(path):
                raise InputError(
                    f"The source file has moved: {ctx.show(path)}. Give it again to export."
                )
            data, section = read_source(ctx, path)
        else:
            raise InputError("Give the source to export: a plotter CSV or a curve file.")
        # A rib remembers the plane its curve file stood on, as the window
        # restores it, unless it is now being made from a different file.
        if record is not None and record.loaded_section and record.source and \
                ctx.readable(record.source, "rib's source") == path:
            section = export.section_from_dict(record.loaded_section)

        # Typed names are stripped, as the window strips its name field.
        name = (name or "").strip()
        if record is not None and name and name != record.stem:
            raise InputError(
                f"A rib keeps the name it was made with ({record.stem}). Export without "
                f"a rib to make a new one called {name}."
            )
        stem = record.stem if record else (name or source_stem(path))
        index = record.name_index if record else sidecar.next_index(stem)
        settings = (settings_from(ExportSpec, spec, record.spec) if record is not None
                    else new_settings(data, section, spec))
        default = (record.output_folder if record is not None and record.output_folder
                   else os.path.dirname(proj.part_path))
        out = ctx.output_folder(folder or default)

        curves = export.build_curves(data, settings, stem, section, index=index)
        # Two names can make one feature name once sanitized ("root" and
        # "root."), and the second export would write over the first's files.
        # Refused before anything is written, as a wing's clash is.
        taken = set(sidecar.all_feature_names()) - set(record.feature_names() if record else ())
        clash = [c.feature for c in curves if c.feature in taken]
        if clash:
            raise InputError(
                f"{clash[0]} is already a curve of another record in this part. "
                "Give the rib another name."
            )
        # Only the curves an export writes can be orphaned by it; they stay
        # in the part, so the record keeps them, retired.
        orphans = (swlink.orphaned([c.feature for c in record.written_curves()], curves)
                   if record is not None else [])
        ctx.check_targets([os.path.join(out, c.filename) for c in curves] + [proj.sidecar_path])
        changed, hashes = swlink.write_files(curves, out)
        loaded = (export.section_to_dict(section)
                  if section is not None and settings.plane_mode == export.MODE_LOADED else None)
        fresh = remember(sidecar, record, curves, settings, stem, path, out, index, hashes,
                         loaded, retire=orphans)
        saved = ctx.save_project(proj)

    answer = {
        "project": ctx.show(proj.part_path),
        "record": ctx.show(proj.sidecar_path),
        "record_changed": saved,
        "rib": fresh.export_id,
        "name": export.folder_name(stem, index),
        "stem": stem,
        "index": index,
        "new": record is None,
        "source": ctx.show(path),
        "folder": ctx.show(out),
        "retired": orphans,
    }
    answer.update(finished(data, settings, section, curves, include_points))
    for entry in answer["curves"]:
        entry["changed"] = entry["feature"] in changed
        entry["sha256"] = hashes[entry["feature"]]
        entry["file"] = ctx.show(os.path.join(out, entry["file"]))
    return answer


def curve_state(ctx: ServerContext, curve: store.CurveRecord, folder: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "role": curve.role,
        "feature": curve.feature,
        "point_count": curve.points,
        "closed": curve.closed,
        "retired": curve.retired,
        "derived": curve.is_derived,
    }
    if curve.is_derived:
        return entry
    stored = store.resolve_file(curve, folder)
    path = ctx.resolved(stored)
    if path is None:
        # A record the window wrote may name files anywhere; this server
        # says nothing about one outside what it may read.
        entry.update(file=stored, present=None, matches=None)
        return entry
    present = os.path.isfile(path)
    matches = None
    if present and curve.sha256:
        with open(path, "rb") as handle:
            matches = writer.sha256_hex(handle.read()) == curve.sha256
    entry.update(file=ctx.show(path), present=present, matches=matches)
    return entry


def describe_project(ctx: ServerContext, project: str) -> Dict[str, Any]:
    with ctx.lock:
        proj = ctx.open_project(project, read_only=True)
    sidecar = proj.sidecar
    ribs = []
    for record in sidecar.exports:
        source = ctx.resolved(record.source) if record.source else None
        ribs.append({
            "rib": record.export_id,
            "name": export.folder_name(record.stem, record.name_index),
            "stem": record.stem,
            "index": record.name_index,
            "adopted": record.adopted,
            "source": ctx.show(record.source),
            "source_present": None if source is None else os.path.isfile(source),
            "folder": ctx.show(record.output_folder),
            "settings": record.settings,
            "on_loaded_plane": record.loaded_section is not None,
            "created": record.created,
            "curves": [curve_state(ctx, c, record.output_folder) for c in record.curves],
        })
    wings = []
    for record in sidecar.wings:
        wings.append({
            "wing": record.export_id,
            "name": export.wing_base(record.stem, record.name_index),
            "stem": record.stem,
            "index": record.name_index,
            "folder": ctx.show(record.output_folder),
            "settings": record.settings,
            "station_count": record.station_count,
            "created": record.created,
            "curves": [curve_state(ctx, c, record.output_folder) for c in record.curves],
        })
    return {
        "project": ctx.show(proj.part_path),
        "record": ctx.show(proj.sidecar_path),
        "part_present": os.path.isfile(proj.part_path),
        "schema_version": sidecar.schema_version,
        "written_by": sidecar.written_by,
        "ribs": ribs,
        "wings": wings,
    }


def register(server: Any, ctx: ServerContext) -> None:
    def read_section(source: Source, include_points: IncludePoints = False) -> Dict[str, Any]:
        """Read a plotter CSV or a curve file: its name and metadata, point count, chord,
        leading- and trailing-edge points, trailing-edge gap and whether it closes, maximum
        thickness and where it is, and for a curve file the plane it stands on. Writes
        nothing."""
        return read(ctx, source, include_points)

    def finish_section(
        source: Source,
        spec: Spec = None,
        name: Annotated[Optional[str], Field(description=(
            "The name the curves would carry; the source file's own name when left out."
        ))] = None,
        include_points: IncludePoints = False,
    ) -> Dict[str, Any]:
        """Run the export on a source without writing anything: each curve's role,
        feature name, point count, closed or not, and bounding box, with the finished
        chord, trailing-edge thickness, and where the section is placed. Names are those
        of a first rib of that name; export_rib gives the real ones. The reference point
        defaults to 0 (the leading edge); the window's fresh form starts at 25."""
        path, data, section = load(ctx, source)
        settings = new_settings(data, section, spec)
        stem = (name or "").strip() or source_stem(path)
        curves = export.build_curves(data, settings, stem, section)
        answer = {"source": ctx.show(path), "stem": stem}
        answer.update(finished(data, settings, section, curves, include_points))
        return answer

    def export_rib_tool(
        project: ProjectPath,
        source: Annotated[Optional[str], Field(description=(
            "A plotter CSV or a curve file. For an existing rib, leave out to use its own."
        ))] = None,
        rib: Annotated[Optional[str], Field(description=(
            "An existing rib to export again, by id or name. Leave out to make a new rib."
        ))] = None,
        name: Annotated[Optional[str], Field(description=(
            "A new rib's name; the source file's own name when left out. A name already "
            "used gets the next index (root, root_2, ...)."
        ))] = None,
        spec: Spec = None,
        folder: Annotated[Optional[str], Field(description=(
            "Where the curve files go, in the workspace; made if missing. Defaults to the "
            "rib's own folder, or for a new rib the project's folder."
        ))] = None,
        include_points: IncludePoints = False,
    ) -> Dict[str, Any]:
        """Export a rib: build its curves, write the curve files, and record the rib in
        the project, as the window's Export does. Returns the rib's id and name, each
        curve's file, feature name and whether the file changed, the curves the new
        settings retired, and the record's path. A new rib's reference point defaults to
        0 (the leading edge); the window's fresh form starts at 25."""
        return export_rib(ctx, project, source, rib, name, spec, folder, include_points)

    def list_project(project: ProjectPath) -> Dict[str, Any]:
        """What a project's record holds: its ribs and wings, their settings and curves,
        and for each curve file whether it is there and still matches what was written.
        Changes nothing: a record that cannot be read is left where it is."""
        return describe_project(ctx, project)

    add_tool(server, read_section, read_only=True, idempotent=True)
    add_tool(server, finish_section, read_only=True, idempotent=True)
    add_tool(server, export_rib_tool, name="export_rib")
    add_tool(server, list_project, read_only=True, idempotent=True)
