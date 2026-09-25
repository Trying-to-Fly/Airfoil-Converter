"""From a wing's settings to the curves it puts in the part.

The window hands over a :class:`~.export.WingSpec` and the part's records;
this reads the ribs back off their sources, stands the wing up, offsets it if
asked, and returns named curves ready for :mod:`swlink`. Nothing here touches
the window or SolidWorks, and it can take a while, so the window runs it on a
thread of its own.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from . import export, parser, wing, wing_offset, writer
from .export import (
    ROLE_SECTION,
    ROLE_SECTION_JOINED,
    ROLE_SECTION_LOWER,
    ROLE_SECTION_TE,
    ROLE_SECTION_UPPER,
    ROLE_WING_LE,
    ROLE_WING_TE,
    ROLE_WING_TE_LOWER,
    ROLE_WING_TE_UPPER,
    Curve,
    InputError,
    WingSpec,
    wing_feature_name,
)
from . import geometry
from .geometry import GeometryError, Point2, Vec3
from .parser import AirfoilParseError

WING_FOLDER = "Wing Curves"
# How far along an offset section its turning is added up, either way from a
# point, to find its nose's corner. A crease the gap filling drew as a small
# arc of ten-degree steps turns just as hard over this stretch as one drawn
# with a single point.
CORNER_REACH = 0.15

Progress = Callable[[str], None]


@dataclass
class WingModel:
    """A wing stood up from its ribs, ready to export or offset."""

    frame: wing.WingFrame
    loft: wing.Loft
    sections: List[wing.Section]
    te_mode: str
    te_thickness: float
    le_points: List[Vec3]
    # The trailing edge as it is exported: one curve, or for a blunt trailing
    # edge one along each corner. (role, points)
    te_edges: List[Tuple[str, List[Vec3]]]
    rib_names: List[str]

    @property
    def span(self) -> float:
        return self.loft.end - self.loft.start


@dataclass
class WingBuild:
    """What one export of a wing produces."""

    curves: List[Curve]
    joins: List[Tuple[Tuple[str, ...], str]]
    model: WingModel
    offset: Optional[wing_offset.OffsetWing] = None
    @property
    def section_names(self) -> List[str]:
        """The loft's profiles, root to tip: what to pick, in order."""
        joined = {}
        for sources, name in self.joins:
            for source in sources:
                joined[source] = name
        return [
            joined.get(curve.feature, curve.feature)
            for curve in self.curves
            if curve.role in export.SECTION_HEAD_ROLES
        ]

    @property
    def edge_names(self) -> List[str]:
        return [c.feature for c in self.curves if c.role in export.WING_EDGE_ROLES]

    @property
    def station_count(self) -> int:
        """How many profiles a loft of this has to be given, one by one."""
        return len(self.section_names)


def _source_error(record, exc: Exception) -> InputError:
    name = export.folder_name(record.stem, record.name_index) if record.stem else record.export_id
    return InputError(f"{name}: its source could not be read again — {exc}")


def load_rib(record) -> wing.Rib:
    if not record.source or not os.path.exists(record.source):
        name = export.folder_name(record.stem, record.name_index) if record.stem else record.export_id
        raise InputError(
            f"{name}'s source file is not where it was ({record.source or 'none recorded'}). "
            "Open it on the Airfoil tab and point it at the file again."
        )
    try:
        data, section = export.load_source(record.source)
    except (AirfoilParseError, GeometryError, OSError) as exc:
        raise _source_error(record, exc) from None
    if record.loaded_section:
        section = export.section_from_dict(record.loaded_section)
    return wing.rib_from_record(record, data, section)


def _edge(
    path: str,
    frame: wing.WingFrame,
    sections: Sequence[wing.Section],
    attr: str,
    what: str,
) -> Tuple[wing.Guide, List[Vec3]]:
    """An edge curve from its file, or a straight line from root to tip."""
    if not path:
        first, last = sections[0], sections[-1]
        guide = wing.Guide.straight(
            first.station, getattr(first, attr), last.station, getattr(last, attr)
        )
        return guide, guide.to_3d(frame)
    if not os.path.exists(path):
        raise InputError(f"The {what} curve file is not there: {path}")
    try:
        points = parser.parse_curve(path)
    except (AirfoilParseError, OSError) as exc:
        raise InputError(f"The {what} curve could not be read: {exc}") from None
    return wing.Guide.from_points(frame, points, what), list(points)


def stand_up(spec: WingSpec, sidecar) -> WingModel:
    """Read every rib and both edges, and check they make a wing."""
    if len(spec.ribs) < 2:
        raise InputError("Tick at least two ribs for the wing: the root and the tip.")
    records = []
    for rib_id in spec.ribs:
        record = sidecar.find(rib_id)
        if record is None:
            raise InputError(
                "One of this wing's ribs is no longer in the part's record. "
                "Pick the ribs again."
            )
        records.append(record)
    for end in (spec.root_end, spec.tip_end):
        if end not in wing.END_KINDS:
            raise InputError(f"Unknown end kind {end!r}.")

    ribs = [load_rib(record) for record in records]
    mode, thickness = wing.check_ribs_agree(ribs)
    frame, _ = wing.wing_frame(ribs, root_at_origin=not spec.swap_ends)
    sections = wing.place_ribs(ribs, frame)
    le, le_points = _edge(spec.le_source, frame, sections, "le", "leading edge")
    te, te_points = _edge(spec.te_source, frame, sections, "te", "trailing edge")
    wing.check_guide(le, sections, "leading edge", "le")
    wing.check_guide(te, sections, "trailing edge", "te", te_line=mode == export.TE_LINE)
    if spec.thickness not in export.THICKNESS_CHOICES:
        raise InputError(f"Unknown thickness rule {spec.thickness!r}.")
    loft = wing.Loft(sections, le, te, spec.thickness)
    if mode == export.TE_LINE:
        if spec.te_source:
            upper, lower = wing.corner_guides(loft.axes, sections, te, te.stations)
        else:
            upper, lower = wing.check_corner_lines(sections)
        te_edges = [
            (ROLE_WING_TE_UPPER, upper.to_3d(frame)),
            (ROLE_WING_TE_LOWER, lower.to_3d(frame)),
        ]
    else:
        te_edges = [(ROLE_WING_TE, te_points)]
    return WingModel(
        frame=frame,
        loft=loft,
        sections=sections,
        te_mode=mode,
        te_thickness=thickness,
        le_points=le_points,
        te_edges=te_edges,
        rib_names=[sec.name for sec in sections],
    )


def build_wing(
    spec: WingSpec,
    sidecar,
    stem: str,
    index: int = 1,
    progress: Optional[Progress] = None,
    check: bool = False,
    own_names: Sequence[str] = (),
    sections: int = 0,
) -> WingBuild:
    """Every curve a wing export produces, named and placed in space.

    A wing is exported as its sections and the guides between them, whether it
    is the wing itself or an offset of it. ``sections`` is how many profiles a
    loft of an offset wing already runs through: it keeps that many if its wall
    allows, so the loft follows the edit.
    """
    if spec.extension not in writer.EXTENSIONS:
        raise InputError(f"Unsupported extension {spec.extension!r}.")
    say = progress or (lambda _message: None)
    say("Reading the ribs...")
    model = stand_up(spec, sidecar)
    offset = spec.offset_mm()

    def named(role: str, points: List[Vec3], closed: bool, section: int = 0,
              tag: str = "") -> Curve:
        feature = wing_feature_name(stem, role, index, section, tag)
        # A section is thinned already, in its own plane, so that the surface
        # guides can land on its points; thinning it again could move them.
        if role not in (ROLE_SECTION, ROLE_SECTION_TE, ROLE_SECTION_UPPER, ROLE_SECTION_LOWER):
            points = geometry.thin_curve(points)
        return Curve(
            role=role, points=points, closed=closed,
            feature=feature, filename=feature + spec.extension,
        )

    curves: List[Curve] = []
    joins: List[Tuple[Tuple[str, ...], str]] = []
    result: Optional[wing_offset.OffsetWing] = None

    up = model.sections[0].up or (0.0, 1.0)

    def section(number: int, s: float, pieces) -> None:
        sources = []
        for kind, piece, shut in pieces:
            curves.append(named(kind, [model.frame.to_3d(s, p) for p in piece], shut, number))
            sources.append(wing_feature_name(stem, kind, index, number))
        if len(sources) > 1:
            joins.append((tuple(sources), wing_feature_name(stem, ROLE_SECTION_JOINED, index, number)))

    if not offset:
        # The wing's own sections, where its shape is planned to change, and
        # guides that each follow one point of every section. Lofted through
        # the two ribs alone, held by guides, SolidWorks' loft sagged up to
        # 0.08 mm off the wing aft of mid-chord; through these it stays within
        # 0.005 mm of it.
        loft = model.loft
        stations = [st.station for st in wing.plan_stations(loft, 0.0, spec.root_end, spec.tip_end)]
        for number, s in enumerate(stations, start=1):
            section(number, s, outline_pieces(loft, s, up))
        samples = wing.guide_samples(stations, loft.le_guide.stations, loft.te_guide.stations)
        for role, tag, i in wing.guide_indices(loft, up):
            points = [model.frame.to_3d(s, loft.point(s, i)) for s in samples]
            curves.append(named(role, points, False, tag=tag))
    else:
        result = wing_offset.offset_wing(
            model.frame,
            model.loft,
            offset,
            spec.root_end,
            spec.tip_end,
            model.te_mode,
            model.te_thickness,
            progress=say,
            check=check,
            sections_wanted=sections,
            spare=wing_offset.SPARE_SECTIONS,
        )
        # Every section, each given a point at every surface guide's chord
        # fraction so the guides meet it exactly, as they meet each other.
        inner = result.loft()
        fractions = [f for f in wing.SURFACE_GUIDES if f >= wing.OFFSET_GUIDES_FROM]
        profiles = []
        for number, sec in enumerate(result.sections, start=1):
            s = sec.station.station
            body = wing.with_guide_points(
                inner, wing.Profile(s, sec.curves[0][1], sec.le), fractions
            )
            profiles.append(body)
            pieces = []
            for role, points, closed in sec.curves:
                if role == export.ROLE_TE:
                    pieces.append((ROLE_SECTION_TE, points, closed))
                else:
                    pieces += split_at_nose(body.points, closed, up, sec.le)
            section(number, s, pieces)
        curves.append(named(ROLE_WING_LE, result.le.to_3d(model.frame), False))
        if result.te_corners is not None:
            upper, lower = result.te_corners
            curves.append(named(ROLE_WING_TE_UPPER, upper.to_3d(model.frame), False))
            curves.append(named(ROLE_WING_TE_LOWER, lower.to_3d(model.frame), False))
        else:
            curves.append(named(ROLE_WING_TE, result.te.to_3d(model.frame), False))
        stations = [sec.station.station for sec in result.sections]
        samples = wing.guide_samples(stations, result.le_samples)
        for tag, guide in wing.surface_guides(inner, profiles, samples, up, fractions):
            curves.append(named(export.ROLE_WING_SURFACE, guide.to_3d(model.frame), False,
                                tag=tag))

    taken = set(sidecar.all_feature_names()) - set(own_names)
    wanted = [c.feature for c in curves] + [name for _, name in joins]
    clash = [name for name in wanted if name in taken]
    if clash:
        raise InputError(
            f"{clash[0]} is already a curve of another record in this part. "
            "Give the wing another name."
        )
    return WingBuild(curves=curves, joins=joins, model=model, offset=result)


def outline_pieces(loft: wing.Loft, s: float, up: Point2) -> List[Tuple[str, List[Point2], bool]]:
    """The wing's own section at ``s`` as the curves to export, as a rib is.

    One curve round the nose, closed on itself where the trailing edge is
    sharp, and with the trailing edge's own straight line where it is blunt.
    Not cut at the nose, as an offset wing's sections are: SolidWorks draws
    each half of a cut curve with no curvature at its end, and a round nose
    drawn as two such halves came out a wedge, up to 0.36 mm off the wing.
    """
    pts = loft.outline(s)
    if loft.sharp:
        return [(ROLE_SECTION, pts + [pts[0]], True)]
    return [(ROLE_SECTION, pts, False), (ROLE_SECTION_TE, [pts[-1], pts[0]], False)]


def split_at_nose(
    points: Sequence[Point2], closed: bool, up: Point2, nose: Point2
) -> List[Tuple[str, List[Point2], bool]]:
    """A section's outline as the curves to export: upper and lower, meeting at the nose.

    SolidWorks draws one smooth spline through a curve's points, and round a
    corner that spline swings wide, or loops and will not loft at all. An
    inward offset deeper than the airfoil's nose radius has just such a corner
    at its nose, so the outline is cut there, each half keeping it as an end.
    Every section is cut, corner or not: a loft will not join profiles cut
    into different numbers of pieces. Offset sections are dense enough at the
    nose that a cut through a smooth one changes nothing to speak of.

    Where the cut falls is the seam between the loft's upper and lower faces,
    so it has to run smoothly from one section to the next. It is the point of
    the front half that turns hardest over CORNER_REACH either way — the
    corner, or where a smooth nose is tightest. It used to be the sharpest
    single point, or the point nearest the leading edge when no point turned
    thirty degrees; a crease drawn as a small arc in one section and as one
    point in the next then had its cut jump half a millimetre and back from
    section to section, and SolidWorks would not make even the surface.
    """
    pts = list(points)
    # Only the front half is searched for the corner. A blunt trailing edge
    # that has been auto-closed turns a right angle onto its closing line,
    # and cutting there would leave the nose corner inside one piece — the
    # very thing the cut is for — and name the pieces upper and lower of
    # nothing.
    reach = 0.5 * max((math.hypot(p[0] - nose[0], p[1] - nose[1]) for p in pts), default=0.0)
    turns = [0.0] + [_turn(pts[i - 1], pts[i], pts[i + 1]) for i in range(1, len(pts) - 1)] + [0.0]
    along = [0.0]
    for a, b in zip(pts, pts[1:]):
        along.append(along[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    best, at = -1.0, -1
    lo = 0
    for i in range(1, len(pts) - 1):
        while along[i] - along[lo] > CORNER_REACH:
            lo += 1
        if math.hypot(pts[i][0] - nose[0], pts[i][1] - nose[1]) > reach:
            continue
        hi = i
        while hi + 1 < len(pts) and along[hi + 1] - along[i] <= CORNER_REACH:
            hi += 1
        total = sum(turns[lo:hi + 1])
        if total > best:
            best, at = total, i
    if at > 0:
        # The cut goes at the sharpest point of that stretch.
        near = [j for j in range(1, len(pts) - 1) if abs(along[j] - along[at]) <= CORNER_REACH]
        at = max(near, key=lambda j: turns[j])
    if not 0 < at < len(pts) - 1:
        return [(ROLE_SECTION, pts, closed)]
    first, second = pts[: at + 1], pts[at:]
    height = lambda piece: sum(p[0] * up[0] + p[1] * up[1] for p in piece) / len(piece)
    # Kept in the outline's own order, so the pieces and the trailing-edge line
    # join end to end: a composite given them out of order will not loft.
    if height(first) >= height(second):
        return [(ROLE_SECTION_UPPER, first, False), (ROLE_SECTION_LOWER, second, False)]
    return [(ROLE_SECTION_LOWER, first, False), (ROLE_SECTION_UPPER, second, False)]


def _turn(a: Point2, b: Point2, c: Point2) -> float:
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = c[0] - b[0], c[1] - b[1]
    size = math.hypot(ux, uy) * math.hypot(vx, vy)
    if size <= 0.0:
        return 0.0
    cos = max(-1.0, min(1.0, (ux * vx + uy * vy) / size))
    return math.degrees(math.acos(cos))


@dataclass
class Planform:
    """The wing seen from above, as the Result panel draws it.

    Every position is ``(station, across)``: along the span, and along the
    root rib's chord, in the wing's own millimetres. ``ribs`` and
    ``sections`` are ``(station, leading edge, trailing edge)``.
    """

    start: float
    end: float
    le: List[Tuple[float, float]]
    te: List[Tuple[float, float]]
    ribs: List[Tuple[float, float, float]]
    sections: List[Tuple[float, float, float]]
    steep_from: Optional[float] = None


def planform(build: WingBuild, samples: int = 48) -> Planform:
    """The outline from the edge curves, the ribs, and the offset wing's sections.

    A section rounding a closed end stands past the wing, not on it, so it is
    left out: the drawing is of the span the ribs cover.
    """
    loft = build.model.loft
    start, end = loft.start, loft.end
    stations = [start + (end - start) * k / samples for k in range(samples + 1)]
    le = [(s, loft.le_guide.at(s)[0]) for s in stations]
    te = [(s, loft.te_guide.at(s)[0]) for s in stations]
    ribs = [(sec.station, sec.le[0], sec.te[0]) for sec in build.model.sections]
    sections: List[Tuple[float, float, float]] = []
    steep = None
    if build.offset is not None:
        sections = [
            (sec.station.station, sec.le[0], sec.te[0])
            for sec in build.offset.sections if not sec.station.rim
        ]
        steep = build.offset.steep_from
    return Planform(start, end, le, te, ribs, sections, steep)


def describe(build: WingBuild) -> List[str]:
    """What the Result panel shows, a line each."""
    model = build.model
    lines = [
        f"{len(model.sections)} ribs over {model.span:.1f} mm of span "
        f"({', '.join(model.rib_names)})",
    ]
    result = build.offset
    guides = sum(1 for c in build.curves if c.role == export.ROLE_WING_SURFACE)
    rule = ("thickness blended root to tip" if model.loft.thickness == export.THICKNESS_BLENDED
            else "airfoil scaled to the chord")
    if result is None:
        lines.append(f"No offset: the wing itself — its edges, and {guides} surface guides "
                     f"holding it to {rule}.")
        return lines
    rims = sum(1 for sec in result.sections if sec.station.rim)
    lines.append(f"Shape between ribs: {rule}")
    if guides:
        lines.append(
            f"Root and tip exported, with {guides} surface guides worked out "
            f"through all {len(result.sections)} sections"
        )
    lines.append(
        f"{len(result.sections)} sections"
        + (f", {rims} of them rounding a closed end" if rims else "")
    )
    if result.steep_from is not None:
        lines.append(
            f"Solved in 3D from {result.steep_from:.1f} mm, where the edges sweep past "
            f"{wing.STEEP_SWEEP:g}°"
        )
    root, tip = result.te_shift
    lines.append(f"Trailing edge moves {root:.1f} mm at the root, {tip:.1f} mm at the tip")
    report = result.report
    if report is not None:
        d = abs(result.offset)
        lines.append(
            f"Wall {report.thinnest:.3f} to {report.thickest:.3f} mm "
            f"({100 * (report.thinnest - d) / d:+.1f}% / {100 * (report.thickest - d) / d:+.1f}%), "
            f"as modelled"
        )
        if report.outside:
            lines.append(f"{report.outside} checked points fell outside the skin")
    return lines
