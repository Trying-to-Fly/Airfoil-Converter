"""Phase 2: sections as shapes — made, cut, split, morphed, measured, placed, laid along a law.

Thin tools over :mod:`..profile` and :func:`geometry.cut_trailing_edge`, plus
``fetch_uiuc``, which downloads a UIUC coordinate file and is only offered when
the server was started with ``--allow-network``.

Every tool takes a section the same way: a path to a source the Converter
reads (a plotter CSV, a UIUC .dat file, a curve file), or the points inline
as ``[[x, y], ...]`` in the section's own plane — from the trailing edge round
the nose and back, upper surface first, x along the chord and y up. A file
says its own units: a CSV and a curve file are in millimetres, a UIUC file in
fractions of its chord. Inline points are taken as millimetres. Where a tool
answers in millimetres for a section in fractions, it asks for ``chord_mm``.

The chord frame is :func:`profile.split_profile`'s: the nose at the origin
(the point of the curve SolidWorks draws through the points that stands
furthest from the middle of the trailing edge), the middle of the trailing
edge at (1, 0), thickness measured square to that chord.
"""

from __future__ import annotations

import dataclasses
import glob
import http.client
import json
import math
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import Field

from .. import export, geometry, parser, profile, store, swlink, wing, writer
from ..export import InputError
from ..geometry import Point2
from .server import (
    Refused,
    ServerContext,
    add_tool,
    bounding_box,
    distance,
    point_list,
    rounded,
    vector,
)
from . import tools_section

# Fractions of the chord, to the writer's ten decimals.
FINE = writer.DECIMALS
# More stations than this along a chord is no longer a summary.
MAX_STATIONS = 2001

UIUC_HOST = "m-selig.web.engr.illinois.edu"
UIUC_URL = "https://" + UIUC_HOST + "/ads/coord/{name}.dat"
# A UIUC name is a plain identifier: it can say which file, never where.
UIUC_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
FETCH_TIMEOUT = 20.0  # seconds
FETCH_LIMIT = 1_000_000  # bytes; the largest UIUC file is a few kilobytes
FETCH_CHUNK = 65536

SectionArg = Annotated[Union[str, List[List[float]]], Field(description=(
    "A section: a path to a plotter CSV, UIUC .dat or curve file (relative to the "
    "workspace, or in a read root), or its points inline as [[x, y], ...] in its own "
    "plane — from the trailing edge round the nose and back, upper surface first, x along "
    "the chord, y up. Files say their own units (CSV and curve files mm, UIUC .dat "
    "fractions of the chord); inline points are taken as mm."
))]
ChordArg = Annotated[Optional[float], Field(description=(
    "The section's chord in mm, from its true nose to the middle of its trailing edge (the "
    "chord frame's chord; the export's target_chord means the source's own chord instead). "
    "Needed to answer in mm for a section in fractions of its chord; for one in mm it "
    "rescales it to this chord."
))]
Overwrite = Annotated[bool, Field(description=(
    "Replace a file already there that this tool did not write. Without it such a file is "
    "left alone and the call refused."
))]
IncludePoints = Annotated[bool, Field(description=(
    "Also return the points, at the curve files' precision and capped."
))]
SaveAs = Annotated[Optional[str], Field(description=(
    "Also save the profile as a UIUC-style .dat file (Selig order, fractions of the chord) "
    "at this path in the workspace, so read_section, finish_section and export_rib take it."
))]


# -- a section, however it is given --------------------------------------------


@dataclasses.dataclass
class Given:
    """A section as a tool received it."""

    points: List[Point2]
    name: str
    units: str  # "mm" or parser.CHORD_FRACTIONS
    chord: float  # in its own units, as the export measures it
    source: Optional[str] = None  # the file, as answers show it
    _true: Optional[float] = None

    def true_chord(self) -> float:
        """The chord of the chord frame, nose to the middle of the trailing edge, in its units."""
        if self._true is None:
            self._true = profile.split_profile(self.points, 3).chord
        return self._true

    @property
    def in_chords(self) -> bool:
        return self.units == parser.CHORD_FRACTIONS

    def summary(self) -> Dict[str, Any]:
        return {"source": self.source, "name": self.name, "units": self.units,
                "point_count": len(self.points)}

    def scale(self, chord_mm: Optional[float], needed: bool = True) -> Optional[float]:
        """Millimetres per unit of the points: from ``chord_mm``, or the file's own mm.

        ``chord_mm`` is the chord of the chord frame — from the true nose to the middle
        of the trailing edge — as everywhere in the profile tools.
        """
        if not (math.isfinite(self.chord) and self.chord > 0.0):
            raise InputError(
                f"{self.name} spans no chord: its points do not reach along x. A section runs "
                "from the trailing edge round the nose and back, x along the chord."
            )
        if chord_mm is not None:
            size = _positive(chord_mm, "chord_mm")
            return size / self.true_chord()
        if self.in_chords:
            if needed:
                raise InputError(
                    f"{self.name} is in fractions of its chord, so it has no size of its own. "
                    "Give chord_mm, the chord it is to have in millimetres."
                )
            return None
        return 1.0


def _positive(value: Any, what: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise InputError(f"{what} must be a finite number greater than zero (got {value!r}).")
    return number


def _inline(points: Any, what: str) -> List[Point2]:
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise InputError(f"The {what} needs at least three [x, y] points.")
    out: List[Point2] = []
    for number, p in enumerate(points, start=1):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise InputError(
                f"Point {number} of the {what} is not an [x, y] pair (got {p!r}). A section "
                "is 2D, in its own plane."
            )
        x, y = float(p[0]), float(p[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise InputError(f"Point {number} of the {what} is not finite.")
        out.append((x, y))
    return out


def take(ctx: ServerContext, section: Any, what: str = "section") -> Given:
    """A section from a path or from inline points."""
    if isinstance(section, str):
        path, data, _ = tools_section.load(ctx, section)
        units = parser.CHORD_FRACTIONS if data.in_chords else "mm"
        return Given(list(data.airfoil), data.name, units, data.chord, ctx.show(path))
    points = _inline(section, what)
    return Given(points, what, "mm", geometry.chord_span(points))


def scaled(points: Sequence[Sequence[float]], factor: float) -> List[List[float]]:
    return [[c * factor for c in p] for p in points]


# -- writing a profile as a .dat file -------------------------------------------


def _fraction(value: float) -> str:
    text = f"{value:.{FINE}f}"
    return text[1:] if text.startswith("-") and float(text) == 0.0 else text


def dat_bytes(title: str, points: Sequence[Point2]) -> bytes:
    """A Selig-order UIUC coordinate file: a title line, then one x y pair per line."""
    title = " ".join(str(title).split()) or "airfoil"
    lines = [title] + [f"{_fraction(x)} {_fraction(y)}" for x, y in points]
    return ("\n".join(lines) + "\n").encode("ascii", errors="replace")


def may_replace(ctx: ServerContext, path: str, data: Optional[bytes], overwrite: bool) -> None:
    """Refuse to replace a file that is there, unless asked to or it would come out the same.

    A file the caller names may be anyone's — a project's record, a source —
    so it is only written over on ``overwrite``.
    """
    if overwrite or not os.path.lexists(path):
        return
    if data is not None and os.path.isfile(path):
        with open(path, "rb") as handle:
            if handle.read() == data:
                return
    raise Refused(
        f"{ctx.show(os.path.dirname(path))}/{os.path.basename(path)} is already there, and "
        "this tool did not write it. Pass overwrite=true to replace it, or choose another name."
    )


def save_dat(ctx: ServerContext, path: str, title: str, points: Sequence[Point2],
             overwrite: bool = False) -> Dict[str, Any]:
    """Save a profile in fractions of its chord where read_section and export_rib can take it."""
    if not os.path.basename(path or "").lower().endswith(".dat"):
        raise InputError(f"A profile is saved as a .dat file (got {path}).")
    data = dat_bytes(title, points)
    with ctx.lock:
        real = ctx.target_file(path, "profile file")
        may_replace(ctx, real, data, overwrite)
        changed = ctx.write_file(real, data)
    return {"saved": ctx.show(real), "changed": changed}


# -- summaries -------------------------------------------------------------------


def split_summary(split: profile.ProfileSplit, chord_mm: Optional[float]) -> Dict[str, Any]:
    """A split in fractions of its chord, and in mm where its chord in mm is known."""
    thickest, where = split.max_thickness()
    k = max(range(len(split.x)), key=lambda i: abs(split.camber[i]))
    answer: Dict[str, Any] = {
        "stations": len(split.x),
        "te_thickness": rounded(split.te_thickness, FINE),
        "sharp": split.sharp,
        "max_thickness": rounded(thickest, FINE),
        "max_thickness_at": rounded(where, 6),
        "max_camber": rounded(split.camber[k], FINE),
        "max_camber_at": rounded(split.x[k], 6),
    }
    if chord_mm is not None:
        answer.update(
            chord_mm=rounded(chord_mm),
            te_thickness_mm=rounded(split.te_thickness * chord_mm),
            max_thickness_mm=rounded(thickest * chord_mm),
        )
    return answer


def split_points(split: profile.ProfileSplit) -> Dict[str, Any]:
    return {
        "x": [rounded(v, FINE) for v in split.x],
        "camber": [rounded(v, FINE) for v in split.camber],
        "thickness": [rounded(v, FINE) for v in split.thickness],
    }


def _stations(stations: int) -> int:
    if not 3 <= int(stations) <= MAX_STATIONS:
        raise InputError(f"stations must be between 3 and {MAX_STATIONS} (got {stations}).")
    return int(stations)


# -- the tools' work ---------------------------------------------------------------


def blunt(ctx: ServerContext, section: Any, thickness_mm: float, keep_chord: bool,
          chord_mm: Optional[float], include_points: bool) -> Dict[str, Any]:
    given = take(ctx, section)
    factor = given.scale(chord_mm)
    if not math.isfinite(thickness_mm) or thickness_mm < 0.0:
        raise InputError("thickness_mm must be a finite number, not negative.")
    cut = geometry.cut_trailing_edge(given.points, thickness_mm / factor, keep_chord)
    pts = cut.points
    try:
        true_chord: Optional[float] = rounded(given.true_chord() * factor, 9)
    except geometry.GeometryError:
        true_chord = None
    answer: Dict[str, Any] = {
        "section": given.summary(),
        "thickness_mm": rounded(thickness_mm, 9),
        "keep_chord": keep_chord,
        "cut": cut.cut,
        "chord_mm": true_chord,
        "span_mm": cut.chord * factor,
        "station_mm": rounded((cut.station - cut.nose) * factor),
        "fraction": rounded(cut.fraction, FINE),
        "cut_chord_mm": cut.cut_chord * factor,
        "scale": cut.scale,
        "point_count": len(pts),
        "te_thickness_mm": rounded(distance(pts[0], pts[-1]) * factor),
        "finished_chord_mm": rounded(geometry.chord_span(pts) * factor),
        "bounding_box_mm": bounding_box(scaled(pts, factor)),
        "coordinates": "2D mm in the section's own frame, as the source's x and y.",
    }
    if include_points:
        answer.update(point_list(scaled(pts, factor)))
    return answer


def naca(ctx: ServerContext, digits: str, points: int, closed_te: bool,
         chord_mm: Optional[float], save_as: Optional[str], include_points: bool,
         overwrite: bool = False) -> Dict[str, Any]:
    if not 3 <= int(points) <= MAX_STATIONS:
        raise InputError(f"points must be between 3 and {MAX_STATIONS} (got {points}).")
    outline = profile.naca4(digits, int(points), closed_te)
    code = str(digits).strip()
    code = code[4:].strip() if code.upper().startswith("NACA") else code
    name = f"NACA {code}"
    size = None if chord_mm is None else _positive(chord_mm, "chord_mm")
    split = profile.split_profile(outline)
    radius = profile.nose_radius(outline)
    answer: Dict[str, Any] = {
        "name": name,
        "units": parser.CHORD_FRACTIONS,
        "point_count": len(outline),
        "closed_te": closed_te,
        "te_gap": rounded(distance(outline[0], outline[-1]), FINE),
        "nose_radius": rounded(radius["ratio"], FINE),
        **split_summary(split, size),
    }
    if size is not None:
        answer["nose_radius_mm"] = rounded(radius["ratio"] * size)
    if save_as:
        answer.update(save_dat(ctx, save_as, name, outline, overwrite))
    if include_points:
        answer["point_units"] = parser.CHORD_FRACTIONS if size is None else "mm"
        answer.update(point_list(outline if size is None else scaled(outline, size)))
    return answer


def split_section(ctx: ServerContext, section: Any, stations: int, extra: Sequence[float],
                  chord_mm: Optional[float], include_points: bool) -> Dict[str, Any]:
    given = take(ctx, section)
    extra = list(extra or ())
    count = _stations(stations)
    if count + len(extra) > MAX_STATIONS:
        raise InputError(
            f"{count} stations and {len(extra)} extra fractions make more than "
            f"{MAX_STATIONS} stations along the chord. Ask for fewer."
        )
    split = profile.split_profile(given.points, count, extra)
    if chord_mm is not None:
        size: Optional[float] = _positive(chord_mm, "chord_mm")
    else:
        given.scale(None, needed=False)  # refuses a section that spans no chord
        size = None if given.in_chords else split.chord
    answer: Dict[str, Any] = {
        "section": given.summary(),
        "chord": rounded(split.chord, 9),
        "chord_angle_deg": rounded(split.angle, 9),
        "nose": vector(split.nose, 9),
        **split_summary(split, size),
        "frame": (
            "Fractions of the chord: x from the true nose (0) to the middle of the trailing "
            "edge (1); camber is the middle of each cut square to the chord, thickness half "
            "its length. chord, nose and chord_angle_deg place that frame in the source's "
            "own units and axes."
        ),
    }
    if include_points:
        answer.update(split_points(split))
    return answer


def morph(ctx: ServerContext, first: Any, second: Any, weight: float,
          te_thickness_mm: Optional[float], chord_mm: Optional[float], stations: int,
          save_as: Optional[str], include_points: bool, overwrite: bool = False) -> Dict[str, Any]:
    a, b = take(ctx, first, "first section"), take(ctx, second, "second section")
    count = _stations(stations)
    made = profile.morph(profile.split_profile(a.points, count),
                         profile.split_profile(b.points, count),
                         weight, te_thickness_mm, chord_mm)
    outline = made.outline()
    size = None if chord_mm is None else _positive(chord_mm, "chord_mm")
    answer: Dict[str, Any] = {
        "first": a.summary(),
        "second": b.summary(),
        "weight": rounded(weight, 12),
        "units": parser.CHORD_FRACTIONS,
        "point_count": len(outline),
        **split_summary(made, size),
    }
    if save_as:
        answer.update(save_dat(ctx, save_as, f"{a.name} to {b.name} at {rounded(weight, 12):g}",
                               outline, overwrite))
    if include_points:
        answer["point_units"] = parser.CHORD_FRACTIONS if size is None else "mm"
        answer.update(point_list(outline if size is None else scaled(outline, size)))
        answer["split"] = split_points(made)
    return answer


def nose(ctx: ServerContext, section: Any, sweep_deg: float,
         chord_mm: Optional[float]) -> Dict[str, Any]:
    given = take(ctx, section)
    found = profile.nose_radius(given.points, sweep_deg)
    given.scale(None, needed=False)  # refuses a section that spans no chord
    # Millimetres per unit, the chord in mm being the chord frame's, as the nose's is.
    if chord_mm is not None:
        factor: Optional[float] = _positive(chord_mm, "chord_mm") / found["chord"]
    else:
        factor = None if given.in_chords else 1.0
    answer: Dict[str, Any] = {
        "section": given.summary(),
        "ratio": rounded(found["ratio"], FINE),
        "radius": rounded(found["radius"], FINE),
        "normal_radius": rounded(found["normal_radius"], FINE),
        "sweep_deg": rounded(found["sweep"], 12),
        "chord": rounded(found["chord"], 9),
        "nose": vector(found["nose"], 9),
        "radius_mm": None,
        "normal_radius_mm": None,
        "meaning": (
            "radius is along the flow, in the section's own units; ratio is radius over the "
            "chord; normal_radius is across a leading edge swept by sweep_deg, "
            "radius / cos(sweep)."
        ),
    }
    if factor is not None:
        answer.update(radius_mm=rounded(found["radius"] * factor),
                      normal_radius_mm=rounded(found["normal_radius"] * factor))
    return answer


def place(ctx: ServerContext, section: Any, leading_edge: Sequence[float],
          chord_direction: Sequence[float], up_direction: Sequence[float],
          chord_mm: Optional[float], twist_deg: float, pivot: float, write: Optional[str],
          include_points: bool, overwrite: bool = False) -> Dict[str, Any]:
    given = take(ctx, section)
    given.scale(chord_mm)  # refuses a section in fractions with no chord, or one with none
    placed = profile.place_section(given.points, leading_edge, chord_direction, up_direction,
                                   chord_mm, twist_deg, pivot)
    answer: Dict[str, Any] = {
        "section": given.summary(),
        "origin": vector(placed.origin),
        "chord_direction": vector(placed.u, 12),
        "up_direction": vector(placed.v, 12),
        "normal": vector(placed.normal, 12),
        "chord_mm": rounded(placed.chord),
        "scale": rounded(placed.scale, 12),
        "pivot_point": vector(placed.pivot),
        "chord_angle_deg": rounded(placed.angle, 9),
        "point_count": len(placed.points),
        "bounding_box": bounding_box(placed.points),
        "written": None,
    }
    if write:
        if os.path.splitext(os.path.basename(write))[1].lower() not in writer.EXTENSIONS:
            raise InputError(f"A curve file ends in {' or '.join(writer.EXTENSIONS)} (got {write}).")
        data = writer.curve_bytes(geometry.thin_curve(placed.points))
        with ctx.lock:
            real = ctx.target_file(write, "curve file")
            may_replace(ctx, real, data, overwrite)
            changed = ctx.write_file(real, data)
        answer["written"] = {"file": ctx.show(real), "changed": changed}
    if include_points:
        answer.update(point_list(placed.points))
    return answer


def law(ctx: ServerContext, name: str, stations: List[Dict[str, Any]], folder: Optional[str],
        project: Optional[str], first: Any, second: Any, te_thickness_mm: Optional[float],
        fractions: Optional[List[float]], resolution: int, extension: str,
        include_points: bool, overwrite: bool = False) -> Dict[str, Any]:
    stem = (name or "").strip()
    if not stem:
        raise InputError("Give the surface a name: its curves are called after it.")
    if not isinstance(stations, list) or not all(isinstance(s, dict) for s in stations):
        raise InputError("stations must be a list of objects, one per section.")
    laid = []
    for number, raw in enumerate(stations, start=1):
        raw = dict(raw)
        if isinstance(raw.get("profile"), str):
            raw["profile"] = take(ctx, raw["profile"], f"profile of station {number}").points
        laid.append(profile.LawStation.from_dict(raw))
    a = take(ctx, first, "first section").points if first is not None else None
    b = take(ctx, second, "second section").points if second is not None else None
    built = profile.build_law(
        laid, stem, a, b, te_thickness_mm,
        wing.SURFACE_GUIDES if fractions is None else fractions,
        _stations(resolution), extension,
    )

    base = export.wing_base(stem)
    names = [c.feature for c in built.curves] + [n for _, n in built.joins]
    with ctx.lock:
        default = ctx.workspace
        records: List[Tuple[str, store.Sidecar]] = []
        reserved = ()
        if project:
            proj = ctx.open_project(project, read_only=True)
            records.append((ctx.show(proj.part_path), proj.sidecar))
            default = os.path.dirname(proj.part_path)
            reserved = (proj.part_path,)
        out = ctx.output_folder(folder or default, make=False)
        # Every project kept in the folder the curves go to, as well as the one
        # named: a law's curve taking a recorded curve's name would write over it.
        for path in sorted(glob.glob(os.path.join(glob.escape(out), "*" + store.SIDECAR_SUFFIX))):
            path = ctx.readable(path, "project record")
            sidecar = store.load(path, move_aside=False)
            if sidecar is not None:
                records.append((ctx.show(path), sidecar))
        for where, sidecar in records:
            taken = {os.path.normcase(n) for n in sidecar.all_feature_names()}
            clash = [n for n in names if os.path.normcase(n) in taken]
            if clash:
                raise InputError(
                    f"{clash[0]} is already a curve of a record in {where}. Give the surface "
                    "another name."
                )
        targets = [os.path.join(out, c.filename) for c in built.curves]
        manifest = os.path.join(out, base + LAW_SUFFIX)
        ctx.check_targets(targets + [manifest], reserved=reserved)
        own = law_files(manifest, base)
        if own is None:
            may_replace(ctx, manifest, None, overwrite)
            own = set()
        for curve, path in zip(built.curves, targets):
            if curve.filename not in own:
                may_replace(ctx, path, writer.curve_bytes(curve.points), overwrite)
        ctx.output_folder(out)
        changed, _ = swlink.write_files(built.curves, out)
        note = {"law": base, "files": sorted(c.filename for c in built.curves),
                "written": store.now()}
        ctx.write_file(manifest, (json.dumps(note, indent=2, sort_keys=True) + "\n").encode())

    heads = [c.feature for c in built.curves if c.role in export.SECTION_HEAD_ROLES]
    guides = [c.feature for c in built.curves if c.role in export.WING_EDGE_ROLES]
    curves = []
    for curve in built.curves:
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
    plan = built.plan
    held = [shape.te_thickness * sec.chord for sec, shape in zip(built.sections, built.shapes)]
    return {
        "name": base,
        "folder": ctx.show(out),
        "recorded": False,
        "manifest": ctx.show(manifest),
        "station_count": len(built.sections),
        "sections": heads,
        "guides": guides,
        "curve_count": len(built.curves),
        "joins": [{"name": n, "curves": list(s)} for s, n in built.joins],
        # One figure only when every station holds the same; else see each station's.
        "te_thickness_mm": (rounded(held[0]) if max(held) - min(held) <= 1e-9 else None),
        "stations": [
            {
                "station": number,
                "chord_mm": rounded(sec.chord),
                "nose": vector(sec.origin),
                "chord_direction": vector(sec.u, 12),
                "up_direction": vector(sec.v, 12),
                "te_thickness_mm": rounded(shape.te_thickness * sec.chord),
                "max_thickness": rounded(shape.max_thickness()[0], FINE),
            }
            for number, (sec, shape) in enumerate(zip(built.sections, built.shapes), start=1)
        ],
        "curves": curves,
        "loft_plan": {
            "name": plan.name,
            "profiles": heads,
            "profiles_once_joined": list(plan.profiles),
            "guides": list(plan.guides),
            "joins": [{"name": n, "curves": list(s)} for s, n in built.joins],
            "keep_tangency": plan.keep_tangency,
            "guide_influence": plan.guide_influence,
            "files": {c.feature: ctx.show(os.path.join(out, c.filename)) for c in built.curves},
        },
    }


LAW_SUFFIX = ".law.json"


def law_files(path: str, base: str) -> Optional[set]:
    """The files a law of this name wrote last time, by its manifest; None if it has none.

    What lets a law be built again over its own curves without overwrite,
    while a file it did not write is still left alone.
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            note = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(note, dict) or note.get("law") != base or not isinstance(note.get("files"), list):
        return None
    return {f for f in note["files"] if isinstance(f, str)}


# -- fetching from UIUC --------------------------------------------------------------


class _StayOnHost(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another https address on the UIUC host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.hostname != UIUC_HOST:
            raise urllib.error.URLError(
                f"it redirected off {UIUC_HOST}, to {target.scheme}://{target.hostname}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(url: str, timeout: float, limit: int) -> Tuple[str, bytes]:
    """GET ``url``: the address it was finally answered from, and at most ``limit + 1`` bytes."""
    opener = urllib.request.build_opener(_StayOnHost)
    request = urllib.request.Request(url, headers={"User-Agent": "airfoil-converter-mcp"})
    with opener.open(request, timeout=timeout) as response:
        # Read a chunk at a time until the cap is passed: a read may return less
        # than asked, and an answer larger than any coordinate file is never
        # held whole.
        chunks: List[bytes] = []
        size = 0
        while size <= limit:
            chunk = response.read(min(FETCH_CHUNK, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return response.geturl(), b"".join(chunks)


def fetch_digest(path: str, url: str) -> Optional[str]:
    """The digest recorded by a prior fetch of this URL, if any."""
    try:
        with open(path, encoding="utf-8") as handle:
            note = json.load(handle)
    except (OSError, ValueError):
        return None
    if isinstance(note, dict) and note.get("url") == url and isinstance(note.get("sha256"), str):
        return note["sha256"]
    return None


def check_fetch_replacement(ctx: ServerContext, path: str, note_path: str,
                            url: str, overwrite: bool) -> None:
    if overwrite:
        return
    digest = fetch_digest(note_path, url)
    if digest is None:
        may_replace(ctx, note_path, None, False)
    if digest is not None and os.path.isfile(path):
        with open(path, "rb") as handle:
            body = handle.read(FETCH_LIMIT + 1)
        if len(body) <= FETCH_LIMIT and writer.sha256_hex(body) == digest:
            return
    may_replace(ctx, path, None, False)


def fetch(ctx: ServerContext, name: str, folder: str, overwrite: bool = False) -> Dict[str, Any]:
    if not isinstance(name, str) or not UIUC_NAME.match(name):
        raise Refused(
            f"{name!r} is not a UIUC file name. Give the name alone, as the database lists "
            "it — letters, digits, '-' and '_', such as sd7037 or naca2412 — without .dat."
        )
    url = UIUC_URL.format(name=name)
    # Where it would go, checked before anything is asked of the network.
    out = ctx.output_folder(folder, make=False)
    path = os.path.join(out, f"{name}.dat")
    note_path = os.path.join(out, f"{name}.source.json")
    ctx.check_targets([path, note_path])
    check_fetch_replacement(ctx, path, note_path, url, overwrite)
    opener = ctx.opener or fetch_url
    try:
        final, body = opener(url, FETCH_TIMEOUT, FETCH_LIMIT)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise Refused(f"The UIUC database has no {name}.dat ({url}).") from None
        raise Refused(f"The UIUC database answered {exc.code} for {url}.") from None
    except (urllib.error.URLError, http.client.HTTPException, socket.timeout, OSError) as exc:
        reason = getattr(exc, "reason", None) or f"{type(exc).__name__}: {exc}"
        raise Refused(f"Could not fetch {url}: {reason}.") from None
    landed = urllib.parse.urlsplit(final)
    if landed.scheme != "https" or landed.hostname != UIUC_HOST:
        raise Refused(f"Fetching {url} ended at {final}, off {UIUC_HOST}. Nothing was saved.")
    if len(body) > FETCH_LIMIT:
        raise Refused(f"{url} is larger than {FETCH_LIMIT} bytes, which no coordinate file is.")
    # Decoded as the parser decodes a file it reads, so the note names the
    # section exactly as read_section will.
    text = body.decode("utf-8-sig", errors="replace")
    # Read before it is kept: a page that is not a coordinate file is refused
    # with the parser's own reason, and nothing is saved.
    data = parser.parse_dat_lines(text.splitlines(), name)

    note = {
        "url": url,
        "final_url": final,
        "retrieved": store.now(),
        "bytes": len(body),
        "sha256": writer.sha256_hex(body),
        "name": data.name,
        "format": data.metadata.get("Format"),
    }
    with ctx.lock:
        ctx.check_targets([path, note_path])
        check_fetch_replacement(ctx, path, note_path, url, overwrite)
        ctx.output_folder(folder)
        changed = ctx.write_file(path, body)
        ctx.write_file(note_path, (json.dumps(note, indent=2, sort_keys=True) + "\n").encode())
        answer = tools_section.read(ctx, path)
    answer.update(saved=ctx.show(path), changed=changed, note=ctx.show(note_path), url=url,
                  retrieved=note["retrieved"])
    return answer


# -- registration ----------------------------------------------------------------------


Vector3 = Annotated[List[float], Field(description="x, y, z in mm, in the part's own axes.")]


def register(server: Any, ctx: ServerContext) -> None:
    def blunt_trailing_edge(
        section: SectionArg,
        thickness_mm: Annotated[float, Field(description="The trailing edge's thickness, mm.")],
        keep_chord: Annotated[bool, Field(description=(
            "Grow the cut section back about its nose until it spans its original chord."
        ))] = False,
        chord_mm: ChordArg = None,
        include_points: IncludePoints = False,
    ) -> Dict[str, Any]:
        """Cut a section's trailing edge blunt, as the export's TE thickness does: on a line
        of constant x where the section stands that thick. Reports the chord in mm (chord_mm,
        the chord frame's, true nose to the middle of the trailing edge), how far the points
        reach along x (span_mm, which the cut is measured on), where the cut fell (station_mm
        from the nose, and as a fraction of span_mm), how far the cut section reaches
        (cut_chord_mm), and the regrowth scale (1 unless keep_chord; then scale * cut_chord_mm
        = span_mm), so a caller can make a family smooth in chord. Answers in mm in the
        section's own x and y. Writes nothing."""
        return blunt(ctx, section, thickness_mm, keep_chord, chord_mm, include_points)

    def naca4(
        digits: Annotated[str, Field(description="Four digits, such as '2412' or 'NACA 0012'.")],
        points: Annotated[int, Field(description=(
            "Points on each surface, nose and tail included; the outline holds 2 * points - 1."
        ))] = profile.NACA_POINTS,
        closed_te: Annotated[bool, Field(description=(
            "Close the trailing edge to a point (NACA's modified last coefficient)."
        ))] = True,
        chord_mm: Annotated[Optional[float], Field(description=(
            "Also give mm figures, and points in mm, for this chord."
        ))] = None,
        save_as: SaveAs = None,
        include_points: IncludePoints = False,
        overwrite: Overwrite = False,
    ) -> Dict[str, Any]:
        """Make a NACA four-digit section: cosine-spaced, Selig order (upper trailing edge
        round the nose to the lower), chord 1, thickness laid square to the camber line as
        NACA define it. Returns its thickness, camber, nose radius and trailing edge as
        fractions of the chord, and saves it as a .dat file when asked."""
        return naca(ctx, digits, points, closed_te, chord_mm, save_as, include_points, overwrite)

    def split_section_tool(
        section: SectionArg,
        stations: Annotated[int, Field(description=(
            "Chord stations, cosine-spaced so they crowd the nose and the tail."
        ))] = profile.STATIONS,
        extra_fractions: Annotated[Optional[List[float]], Field(description=(
            "Chord fractions (0 to 1) to add to the stations."
        ))] = None,
        chord_mm: ChordArg = None,
        include_points: IncludePoints = False,
    ) -> Dict[str, Any]:
        """Split a section into a camber line and a half-thickness on a common grid of chord
        stations, in fractions of the chord, the nose found on the curve SolidWorks draws
        through the points (not the nearest point) and thickness measured square to the chord
        line from there. Returns the maximum thickness and camber and where, the trailing
        edge, and the frame; x, camber and thickness lists only with include_points. The
        stations and extra fractions together are at most 2001 stations."""
        return split_section(ctx, section, stations, extra_fractions or (), chord_mm,
                             include_points)

    def morph_sections(
        first: SectionArg,
        second: SectionArg,
        weight: Annotated[float, Field(description="0 is the first section, 1 the second.")],
        te_thickness_mm: Annotated[Optional[float], Field(description=(
            "Hold the trailing edge this many mm thick, at chord_mm. Needs chord_mm."
        ))] = None,
        chord_mm: Annotated[Optional[float], Field(description=(
            "The chord, in mm, the trailing edge is held at, and mm figures are given for."
        ))] = None,
        stations: Annotated[int, Field(description="Chord stations both are split on.")]
        = profile.STATIONS,
        save_as: SaveAs = None,
        include_points: IncludePoints = False,
        overwrite: Overwrite = False,
    ) -> Dict[str, Any]:
        """A section weight of the way from one section to another: camber fades between
        them and thickness blends with each one's trailing-edge ramp taken off, then a ramp is
        put back holding the trailing edge at te_thickness_mm on a chord of chord_mm. The
        result is in fractions of its chord, in the split's chord frame; saved as a .dat file
        when asked, it can be exported as a rib with a target chord."""
        return morph(ctx, first, second, weight, te_thickness_mm, chord_mm, stations,
                     save_as, include_points, overwrite)

    def nose_radius(
        section: SectionArg,
        sweep_deg: Annotated[float, Field(description=(
            "The leading edge's sweep in degrees, the section being cut along the flow."
        ))] = 0.0,
        chord_mm: ChordArg = None,
    ) -> Dict[str, Any]:
        """The radius of a section's nose, off the curve SolidWorks draws through its points,
        at its true nose: in the section's own units, as a fraction of its chord, and in mm
        where they are known. With a sweep, also the radius square to the swept leading edge,
        radius / cos(sweep)."""
        return nose(ctx, section, sweep_deg, chord_mm)

    def place_section(
        section: SectionArg,
        leading_edge: Vector3,
        chord_direction: Annotated[List[float], Field(description=(
            "The chord's direction, nose to tail, in the part's axes."
        ))],
        up_direction: Annotated[List[float], Field(description=(
            "Which way the upper surface faces; it need only lean that way, it is squared to "
            "the chord."
        ))],
        chord_mm: Annotated[Optional[float], Field(description=(
            "The chord in mm, true nose to the middle of the trailing edge; without it a "
            "section in mm keeps its own. Needed for a section in fractions of its chord."
        ))] = None,
        twist_deg: Annotated[float, Field(description=(
            "Turn in the section's plane, as a positive angle of attack turns it: nose up, "
            "tail towards -up."
        ))] = 0.0,
        pivot: Annotated[float, Field(description=(
            "The chord fraction from the nose the twist turns about (0.25 the quarter chord); "
            "that point stays where the untwisted section had it."
        ))] = 0.0,
        write: Annotated[Optional[str], Field(description=(
            "Also write the 3D curve to this .sldcrv or .txt file in the workspace."
        ))] = None,
        include_points: IncludePoints = False,
        overwrite: Overwrite = False,
    ) -> Dict[str, Any]:
        """Stand a 2D section in space on any plane: its true nose at leading_edge, its chord
        line along chord_direction, its top towards up_direction, scaled to chord_mm, then
        twisted about the pivot. Returns the frame it stands on and its bounding box, and
        writes the curve file when asked."""
        return place(ctx, section, leading_edge, chord_direction, up_direction, chord_mm,
                     twist_deg, pivot, write, include_points, overwrite)

    def build_law(
        name: Annotated[str, Field(description=(
            "The surface's name; its curves are named as a wing's (name_s01, name_le, "
            "name_upper_30) and its loft name_loft."
        ))],
        stations: Annotated[List[Dict[str, Any]], Field(description=(
            "One object per section, root to tip: leading_edge [x, y, z] mm, chord_direction "
            "and up_direction [x, y, z], chord (mm), and optionally twist (degrees) and pivot "
            "(chord fraction), with either profile (a section: a path, or [[x, y], ...]) or "
            "weight (0 to 1, a morph from first to second)."
        ))],
        folder: Annotated[Optional[str], Field(description=(
            "Where the curve files go, in the workspace; made if missing. Defaults to the "
            "project's folder, or the workspace."
        ))] = None,
        project: Annotated[Optional[str], Field(description=(
            "A project whose curve names this surface must not take. Its record is read, "
            "never written."
        ))] = None,
        first: Annotated[Optional[Union[str, List[List[float]]]], Field(description=(
            "The section weight 0 is; a section as everywhere else."
        ))] = None,
        second: Annotated[Optional[Union[str, List[List[float]]]], Field(description=(
            "The section weight 1 is."
        ))] = None,
        te_thickness_mm: Annotated[Optional[float], Field(description=(
            "Hold every section's trailing edge this thick in mm at its own chord; 0 sharp."
        ))] = None,
        fractions: Annotated[Optional[List[float]], Field(description=(
            "The chord fractions the surface guides run at; the window's own when left out."
        ))] = None,
        resolution: Annotated[int, Field(description="Chord stations each section is rebuilt on.")]
        = profile.STATIONS,
        extension: Annotated[str, Field(description="'.sldcrv' or '.txt'.")] = ".sldcrv",
        include_points: IncludePoints = False,
        overwrite: Annotated[bool, Field(description=(
            "Replace files already there that this law did not write last time. The names "
            "are always checked against every project record in the folder, and the project's."
        ))] = False,
    ) -> Dict[str, Any]:
        """Lay sections along the caller's own law and write them: section curves, the
        leading- and trailing-edge curves, and surface guides through the same chord fraction
        of every section, with a loft plan in build_wing's shape. Each station is placed as
        place_section places one. The files are not recorded in a project: a law has neither
        ribs nor edges, which is all a wing record holds. A note beside them (name.law.json)
        lists what it wrote, so building the same law again replaces its own curves."""
        return law(ctx, name, stations, folder, project, first, second, te_thickness_mm,
                   fractions, resolution, extension, include_points, overwrite)

    def fetch_uiuc(
        name: Annotated[str, Field(description=(
            "The file's name in the UIUC database, without .dat: sd7037, naca2412, e387."
        ))],
        folder: Annotated[str, Field(description=(
            "Where to save it in the workspace; made if missing."
        ))] = "uiuc",
        overwrite: Annotated[bool, Field(description=(
            "Replace a name.dat or name.source.json already there, including locally edited "
            "coordinates. Without this, an earlier download must still match its fetch note."
        ))] = False,
    ) -> Dict[str, Any]:
        """Download a coordinate file from the UIUC Airfoil Coordinates Database
        (https://m-selig.web.engr.illinois.edu/ads/coord/<name>.dat), read it, and only then
        save it into the workspace beside a note of its URL and when it was retrieved. Answers
        as read_section does. Its points are fractions of the chord, so exporting it needs a
        target chord. The folder and both files are checked before the network is asked."""
        return fetch(ctx, name, folder, overwrite)

    add_tool(server, blunt_trailing_edge, read_only=True, idempotent=True)
    # Each of these can replace a file: its own earlier output, or with overwrite anyone's.
    add_tool(server, naca4, destructive=True, idempotent=True)
    add_tool(server, split_section_tool, name="split_section", read_only=True, idempotent=True)
    add_tool(server, morph_sections, destructive=True, idempotent=True)
    add_tool(server, nose_radius, read_only=True, idempotent=True)
    add_tool(server, place_section, destructive=True, idempotent=True)
    add_tool(server, build_law, destructive=True, idempotent=True)
    if ctx.allow_network:
        add_tool(server, fetch_uiuc, destructive=True, idempotent=True, open_world=True)
