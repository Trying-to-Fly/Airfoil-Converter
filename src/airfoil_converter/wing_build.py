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
# A turn this sharp between two neighbouring points of an offset section is a
# corner, and the section is cut there rather than at the point nearest its
# leading edge.
CORNER_DEG = 30.0

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
) -> WingBuild:
    """Every curve a wing export produces, named and placed in space."""
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

    if not offset:
        curves.append(named(ROLE_WING_LE, model.le_points, False))
        for role, points in model.te_edges:
            curves.append(named(role, points, False))
        # The ribs alone would let the loft take its own shape between them;
        # these hold it to the one the wing is meant to have, and the one the
        # offset wing is worked out from.
        loft = model.loft
        # The outline is the curve SolidWorks draws through the rib's points, to
        # well under its own tolerance, so a guide may land anywhere along it.
        profiles = [wing.Profile(sec.station, sec.outline, sec.le) for sec in model.sections]
        samples = wing.span_samples(
            loft.start, loft.end, loft.le_guide.stations, loft.te_guide.stations
        )
        up = model.sections[0].up or (0.0, 1.0)
        for tag, guide in wing.surface_guides(loft, profiles, samples, up):
            curves.append(named(export.ROLE_WING_SURFACE, guide.to_3d(model.frame), False,
                                tag=tag))
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
        )
        ends_only = spec.profiles == export.PROFILES_ENDS
        if ends_only:
            exported = [(result.sections[0], 0, "root"), (result.sections[-1], 0, "tip")]
        else:
            exported = [(sec, number, "") for number, sec in enumerate(result.sections, start=1)]
        up = model.sections[0].up or (0.0, 1.0)
        for sec, number, tag in exported:
            s = sec.station.station
            sources = []
            for role, points, closed in sec.curves:
                if role == export.ROLE_TE:
                    pieces = [(ROLE_SECTION_TE, points, closed)]
                else:
                    pieces = split_at_nose(points, closed, up, sec.le)
                for kind, piece, shut in pieces:
                    placed = wing_offset.to_3d(model.frame, s, piece)
                    curves.append(named(kind, placed, shut, number, tag))
                    sources.append(wing_feature_name(stem, kind, index, number, tag))
            if len(sources) > 1:
                joins.append(
                    (tuple(sources), wing_feature_name(stem, ROLE_SECTION_JOINED, index, number, tag))
                )
        curves.append(named(ROLE_WING_LE, result.le.to_3d(model.frame), False))
        if result.te_corners is not None:
            upper, lower = result.te_corners
            curves.append(named(ROLE_WING_TE_UPPER, upper.to_3d(model.frame), False))
            curves.append(named(ROLE_WING_TE_LOWER, lower.to_3d(model.frame), False))
        else:
            curves.append(named(ROLE_WING_TE, result.te.to_3d(model.frame), False))
        if ends_only:
            up = model.sections[0].up or (0.0, 1.0)
            ends = (result.sections[0], result.sections[-1])
            for tag, guide in wing_offset.surface_guides(result, ends, up):
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
    """
    pts = list(points)
    sharpest, at = 0.0, -1
    for i in range(1, len(pts) - 1):
        turn = _turn(pts[i - 1], pts[i], pts[i + 1])
        if turn > sharpest:
            sharpest, at = turn, i
    if sharpest < CORNER_DEG:
        at = min(range(len(pts)), key=lambda i: math.hypot(pts[i][0] - nose[0], pts[i][1] - nose[1]))
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
