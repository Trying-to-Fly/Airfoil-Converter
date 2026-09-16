"""A wing as data: sections lofted along a leading and a trailing edge.

A loft through ribs is only defined where there are ribs. Between them the
shape is whatever the loft makes of it, steered by the two edge curves. This
module models that — the same way for the wing the ribs describe and for the
offset wing built inside it — so the offset can be worked out in 3D and
checked against the skin it is meant to follow.

Everything is worked in *wing coordinates*: ``s`` along the span, measured
from the root rib's leading edge along the normal of its plane, and ``(a, b)``
across the plane. Every rib lies in a plane of constant ``s``, which is what
lets a section at any station be a plain 2D outline.

Nothing here touches SolidWorks or the window.
"""

from __future__ import annotations

import bisect
import dataclasses
import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import export, geometry as g
from .geometry import GeometryError, Point2, Vec3

OPEN = "open"
CLOSED = "closed"
END_KINDS = (OPEN, CLOSED)

# An edge curve may miss a rib's edge point by this much, in mm.
EDGE_TOL = 0.1
# A curve along a straight trailing edge may meet that edge's line this far
# beyond either corner: with the corners exported as edges of their own, it
# only has to say where the trailing edge runs along the span.
TE_LINE_REACH = 1.0
# Two ribs may lean on each other by this much at most.
MAX_LEAN_DEG = 30.0
# The offset wing gets a section at least this often along the span...
MAX_SPACING = 140.0
# ...and wherever an edge's sweep turns by more than this between two of them...
MAX_SWEEP_STEP = 5.0
# ...but never two closer together than this.
MIN_SPACING = 0.5
# Past this much sweep the offset is solved in 3D instead of corrected in 2D.
STEEP_SWEEP = 20.0
# Points along each surface, nose to tail, in a resampled outline.
SAMPLES = 80
# Where along the chord the guides for a root-and-tip loft run, on each surface.
SURFACE_GUIDES = (0.02, 0.05, 0.15, 0.30, 0.50, 0.75, 0.90)
# Between ribs the surface guides are sampled at least this often.
SURFACE_STEP = 5.0
# A closed end grown outward is rounded by sections at these angles.
RIM_ANGLES = (30.0, 60.0, 90.0)

# The trailing-edge modes a wing can be built from. Split halves enclose no
# section, so there is no skin to offset.
WING_TE_MODES = (export.TE_CLOSE, export.TE_OPEN, export.TE_LINE)


# -- a natural cubic spline -------------------------------------------------


class Spline:
    """A natural cubic spline through ``(x, y)``, ``x`` strictly increasing."""

    def __init__(self, xs: Sequence[float], ys: Sequence[float]):
        n = len(xs)
        if n < 2:
            raise GeometryError("A curve needs at least two points.")
        self.x = list(xs)
        self.y = list(ys)
        h = [self.x[i + 1] - self.x[i] for i in range(n - 1)]
        if any(step <= 0.0 for step in h):
            raise GeometryError("The curve's points must advance steadily along the span.")
        sub = [0.0] * n
        diag = [1.0] * n
        sup = [0.0] * n
        rhs = [0.0] * n
        for i in range(1, n - 1):
            sub[i], diag[i], sup[i] = h[i - 1], 2.0 * (h[i - 1] + h[i]), h[i]
            rhs[i] = 6.0 * (
                (self.y[i + 1] - self.y[i]) / h[i] - (self.y[i] - self.y[i - 1]) / h[i - 1]
            )
        for i in range(1, n):
            m = sub[i] / diag[i - 1]
            diag[i] -= m * sup[i - 1]
            rhs[i] -= m * rhs[i - 1]
        curvature = [0.0] * n
        curvature[-1] = rhs[-1] / diag[-1]
        for i in range(n - 2, -1, -1):
            curvature[i] = (rhs[i] - sup[i] * curvature[i + 1]) / diag[i]
        self._m = curvature
        self._h = h

    def _segment(self, t: float) -> int:
        return min(max(bisect.bisect_right(self.x, t) - 1, 0), len(self.x) - 2)

    def __call__(self, t: float) -> float:
        i = self._segment(t)
        h, m = self._h[i], self._m
        a = (self.x[i + 1] - t) / h
        b = (t - self.x[i]) / h
        return (
            a * self.y[i]
            + b * self.y[i + 1]
            + ((a ** 3 - a) * m[i] + (b ** 3 - b) * m[i + 1]) * h * h / 6.0
        )

    def slope(self, t: float) -> float:
        i = self._segment(t)
        h, m = self._h[i], self._m
        a = (self.x[i + 1] - t) / h
        b = (t - self.x[i]) / h
        return (
            (self.y[i + 1] - self.y[i]) / h
            - (3.0 * a * a - 1.0) / 6.0 * h * m[i]
            + (3.0 * b * b - 1.0) / 6.0 * h * m[i + 1]
        )


# -- the wing's own axes ----------------------------------------------------


class WingFrame:
    """Where the wing's stations are: one plane for every station along the span.

    At each rib the plane is that rib's own. Between two ribs it turns from one
    to the other — each plane between is a blend of the two ribs' plane
    equations, so every point of the space between lies on exactly one of them,
    and a vertical root beside ribs leaning with the dihedral is no different
    from a wing of parallel ribs. Past the end ribs the planes run on parallel
    to the end ones.

    A station ``s`` is measured along the *spine*: the line through the root
    rib's leading edge, square to the root rib. Across each plane, ``(a, b)``
    runs along the root rib's chord direction, as nearly as that plane allows,
    and square to it.
    """

    def __init__(self, origin: Vec3, spine: Vec3, normals: Sequence[Vec3],
                 offsets: Sequence[float], chord: Vec3):
        self.origin = origin
        self.spine = spine
        self.normals = list(normals)
        self.offsets = list(offsets)
        self._chord = chord
        self.stations = [self._pierce(n, d) for n, d in zip(self.normals, self.offsets)]
        self._frames: Dict[float, Tuple[Vec3, Vec3, Vec3, Vec3]] = {}

    @property
    def span(self) -> Vec3:
        return self.spine

    def _pierce(self, n: Vec3, d: float) -> float:
        """How far along the spine the plane ``n . x = d`` crosses it."""
        return (d - g.dot(n, self.origin)) / g.dot(n, self.spine)

    def _plane(self, s: float) -> Tuple[Vec3, float]:
        st = self.stations
        if s <= st[0] or s >= st[-1]:
            k = 0 if s <= st[0] else -1
            n = self.normals[k]
            return n, self.offsets[k] + (s - st[k]) * g.dot(n, self.spine)
        k = min(bisect.bisect_right(st, s) - 1, len(st) - 2)
        t = (s - st[k]) / (st[k + 1] - st[k])
        m = g.add(g.scale(self.normals[k], 1.0 - t), g.scale(self.normals[k + 1], t))
        e = (1.0 - t) * self.offsets[k] + t * self.offsets[k + 1]
        size = g.length(m)
        return g.scale(m, 1.0 / size), e / size

    def frame(self, s: float) -> Tuple[Vec3, Vec3, Vec3, Vec3]:
        """The plane at ``s``: its normal, its origin on the spine, and its two axes."""
        hit = self._frames.get(s)
        if hit is None:
            n, d = self._plane(s)
            along = (d - g.dot(n, self.origin)) / g.dot(n, self.spine)
            origin = g.add(self.origin, g.scale(self.spine, along))
            e1 = g.normalize(g.sub(self._chord, g.scale(n, g.dot(self._chord, n))), "chord")
            hit = (n, origin, e1, g.cross(n, e1))
            if len(self._frames) > 20000:
                self._frames.clear()
            self._frames[s] = hit
        return hit

    def locate(self, p: Vec3) -> float:
        """The station whose plane ``p`` lies on."""
        f = [g.dot(n, p) - d for n, d in zip(self.normals, self.offsets)]
        st = self.stations
        if f[0] <= 0.0:
            return st[0] + f[0] / g.dot(self.normals[0], self.spine)
        if f[-1] >= 0.0:
            return st[-1] + f[-1] / g.dot(self.normals[-1], self.spine)
        for k in range(len(f) - 1):
            if f[k] >= 0.0 >= f[k + 1]:
                t = f[k] / (f[k] - f[k + 1])
                return st[k] + t * (st[k + 1] - st[k])
        raise GeometryError("A point lies where the ribs' planes cross.")

    def to_wing(self, p: Vec3) -> Tuple[float, float, float]:
        s = self.locate(p)
        _, origin, e1, e2 = self.frame(s)
        r = g.sub(p, origin)
        return s, g.dot(r, e1), g.dot(r, e2)

    def to_3d(self, s: float, p: Point2) -> Vec3:
        _, origin, e1, e2 = self.frame(s)
        return g.add(origin, g.add(g.scale(e1, p[0]), g.scale(e2, p[1])))

    def project(self, s: float, p: Vec3) -> Tuple[float, Point2]:
        """How far ``p`` stands off the plane at ``s``, and where it lands on it."""
        n, origin, e1, e2 = self.frame(s)
        r = g.sub(p, origin)
        return g.dot(r, n), (g.dot(r, e1), g.dot(r, e2))

    def mirror(self, p: Vec3, s: float) -> Vec3:
        """``p`` reflected in the plane at ``s``."""
        n, origin, _, _ = self.frame(s)
        return g.sub(p, g.scale(n, 2.0 * g.dot(g.sub(p, origin), n)))


# -- edge curves ------------------------------------------------------------


class Guide:
    """An edge curve, as a point across the plane for every station."""

    def __init__(self, stations: Sequence[float], points: Sequence[Point2]):
        self.stations = list(stations)
        self.points = [tuple(p) for p in points]
        self._a = Spline(self.stations, [p[0] for p in self.points])
        self._b = Spline(self.stations, [p[1] for p in self.points])

    @property
    def start(self) -> float:
        return self.stations[0]

    @property
    def end(self) -> float:
        return self.stations[-1]

    def at(self, s: float) -> Point2:
        return self._a(s), self._b(s)

    def slope(self, s: float) -> Point2:
        return self._a.slope(s), self._b.slope(s)

    def sweep(self, s: float) -> float:
        """Degrees the edge leans off the span direction, here."""
        da, db = self.slope(s)
        return math.degrees(math.atan(math.hypot(da, db)))

    @classmethod
    def straight(cls, s0: float, p0: Point2, s1: float, p1: Point2) -> "Guide":
        return cls([s0, s1], [p0, p1])

    @classmethod
    def from_points(cls, frame: WingFrame, points: Sequence[Vec3], what: str) -> "Guide":
        """Read an edge curve given in part coordinates."""
        placed = [frame.to_wing(p) for p in points]
        if len(placed) < 2:
            raise GeometryError(f"The {what} curve needs at least two points.")
        if placed[0][0] > placed[-1][0]:
            placed.reverse()
        for (s0, _, _), (s1, _, _) in zip(placed, placed[1:]):
            if s1 <= s0:
                raise GeometryError(
                    f"The {what} curve doubles back along the span near station "
                    f"{s0:.3f} mm. An edge curve has to run steadily from root to tip."
                )
        return cls([p[0] for p in placed], [(p[1], p[2]) for p in placed])

    def to_3d(self, frame: WingFrame, samples: Optional[Sequence[float]] = None) -> List[Vec3]:
        stations = self.stations if samples is None else samples
        return [frame.to_3d(s, self.at(s)) for s in stations]


# -- one section, placed ----------------------------------------------------


@dataclass
class Section:
    """A section standing at one station, in wing coordinates.

    ``outline`` runs from the trailing edge, round the nose and back, as the
    exported curve does: for a sharp trailing edge it ends where it started,
    for a blunt one it ends at the other corner and the straight line between
    the two closes it.
    """

    station: float
    outline: List[Point2]
    le: Point2
    te: Point2
    name: str = ""
    # The two ends of a straight trailing edge, upper first. None when the
    # trailing edge is not closed by a line of its own.
    corners: Optional[Tuple[Point2, Point2]] = None
    # Which way the rib calls up, across its plane.
    up: Optional[Point2] = None


def _rotation_keeping_start(points: List[Point2], closed: bool) -> List[Point2]:
    """The same outline walked the other way round, from the same start."""
    if closed:
        return [points[0]] + list(reversed(points[1:-1])) + [points[0]]
    return list(reversed(points))


def _area(points: Sequence[Point2]) -> float:
    return g.signed_area(g.clean_loop(points))


def upper_first(a: Point2, b: Point2, up: Point2) -> Tuple[Point2, Point2]:
    """The two ends of a trailing edge, the one further along ``up`` first."""
    rise = (a[0] - b[0]) * up[0] + (a[1] - b[1]) * up[1]
    return (a, b) if rise >= 0.0 else (b, a)


def te_point(outline: Sequence[Point2], mode: str) -> Point2:
    """Where the trailing-edge curve has to pass through a section.

    A straight trailing-edge line is part of the section, so its midpoint is on
    it; any other outline only surely passes through its own first point.
    """
    if mode == export.TE_LINE:
        a, b = outline[0], outline[-1]
        return (0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]))
    return outline[0]


# -- the ribs a wing is built from ------------------------------------------


@dataclass
class Rib:
    """A rib record, read far enough to stand it in space."""

    name: str
    spec: export.ExportSpec
    outline: List[Vec3]  # the exported airfoil, without the rib's own offset
    le: Vec3
    u: Vec3
    v: Vec3
    closed: bool


def rib_from_record(record, data, section=None) -> Rib:
    """Everything a wing needs from one rib record and its source."""
    name = export.folder_name(record.stem, record.name_index) if record.stem else record.export_id
    if record.adopted or not record.settings:
        raise GeometryError(
            f"{name} was taken over from the part and has no settings, so the app does "
            "not know where it stands. Open it on the Airfoil tab and export it first."
        )
    spec = record.spec
    if spec.te_mode not in WING_TE_MODES:
        raise GeometryError(
            f"{name} is split into upper and lower halves, which enclose no section. "
            "A wing needs its ribs as whole outlines."
        )
    if spec.te_mode == export.TE_OPEN and spec.te_thickness_mm():
        raise GeometryError(
            f"{name} is left open with a blunt trailing edge, so no edge curve can run "
            "through its trailing edge. Close it with a TE line instead."
        )
    # The wing is the ribs as drawn; any offset of their own is not the skin.
    plain = dataclasses.replace(spec, offset="", export_airfoil=True, export_camber=False)
    built = export.build_sections(data, plain)
    role, points, closed = built[0]
    if spec.te_mode == export.TE_OPEN:
        # Sharp and left open: the loop still closes at its trailing edge.
        points, closed = g.auto_close(points), True
    factor = g.scale_factor(data.chord, plain.target_chord_mm())
    u, v = export.plane_frame(plain, section)
    le = plain.vector("leading_edge")
    # Thinned just as the rib's exported curve is, so guides can land on its points.
    outline = g.thin_curve(g.to_3d(points, le, u, v, factor))
    return Rib(name=name, spec=spec, outline=outline, le=le, u=u, v=v, closed=closed)


def _normal(rib: Rib) -> Vec3:
    return g.normalize(g.cross(rib.u, rib.v), "rib plane")


def wing_frame(ribs: Sequence[Rib], root_at_origin: bool = True) -> Tuple[WingFrame, List[float]]:
    """The wing's stations, and each rib's station along them, in the order given.

    The span runs from the root to the tip; the root is the end nearer the
    part's origin, unless told otherwise. Ribs may lean on one another — a
    vertical root and outer ribs square to the dihedral, say — as long as no
    rib's plane passes through another rib.
    """
    if len(ribs) < 2:
        raise GeometryError("A wing needs at least two ribs.")
    first = ribs[0]
    reference = _normal(first)
    normals = []
    for rib in ribs:
        n = _normal(rib)
        if g.dot(n, reference) < 0.0:
            n = g.negate(n)
        normals.append(n)

    raw = [g.dot(g.sub(r.le, first.le), reference) for r in ribs]
    lo = min(range(len(ribs)), key=lambda i: raw[i])
    hi = max(range(len(ribs)), key=lambda i: raw[i])
    near_lo = abs(g.dot(ribs[lo].le, reference))
    near_hi = abs(g.dot(ribs[hi].le, reference))
    root = lo if (near_lo <= near_hi) == root_at_origin else hi
    if root == hi:
        normals = [g.negate(n) for n in normals]
        raw = [-r for r in raw]
    order = sorted(range(len(ribs)), key=lambda i: raw[i])

    for a in order:
        for b in order:
            cos = min(1.0, abs(g.dot(normals[a], normals[b])))
            if math.degrees(math.acos(cos)) > MAX_LEAN_DEG:
                raise GeometryError(
                    f"{ribs[b].name} leans {math.degrees(math.acos(cos)):.1f}° from "
                    f"{ribs[a].name}. Ribs may lean on each other by up to "
                    f"{MAX_LEAN_DEG:g}°."
                )
    # Every rib has to stand wholly on its own side of every other: a plane
    # through another rib would give points of the wing two stations.
    offsets = [g.dot(normals[i], ribs[i].le) for i in range(len(ribs))]
    for a, b in zip(order, order[1:]):
        if raw[b] - raw[a] < MIN_SPACING:
            raise GeometryError(
                f"{ribs[a].name} and {ribs[b].name} stand {raw[b] - raw[a]:.3f} mm apart "
                "along the span. A wing needs its ribs at distinct stations."
            )
    for rank, a in enumerate(order):
        for b in order[rank + 1:]:
            ahead = [g.dot(normals[a], p) - offsets[a] for p in ribs[b].outline]
            behind = [g.dot(normals[b], p) - offsets[b] for p in ribs[a].outline]
            if min(ahead) <= 0.0 or max(behind) >= 0.0:
                raise GeometryError(
                    f"The planes of {ribs[a].name} and {ribs[b].name} cross inside the "
                    "wing: one rib reaches through the other's plane. Ribs this close "
                    "together cannot lean this far apart."
                )

    root_rib = ribs[root]
    spine = normals[root]
    chord = g.normalize(g.sub(root_rib.u, g.scale(spine, g.dot(root_rib.u, spine))), "chord")
    frame = WingFrame(
        origin=root_rib.le,
        spine=spine,
        normals=[normals[i] for i in order],
        offsets=[offsets[i] for i in order],
        chord=chord,
    )
    stations = [frame.locate(r.le) for r in ribs]
    return frame, stations


def place_ribs(ribs: Sequence[Rib], frame: WingFrame) -> List[Section]:
    """Each rib as a section in wing coordinates, root first, all walked alike."""
    placed = []
    orientation = 0.0
    for rib in ribs:
        s = frame.locate(rib.le)
        # Every point of a rib lies on its own station's plane, so it is laid
        # out there directly rather than located point by point.
        outline = [frame.project(s, p)[1] for p in rib.outline]
        area = _area(outline)
        if orientation == 0.0:
            orientation = 1.0 if area > 0 else -1.0
        # Every section is walked counter-clockwise, from the trailing edge.
        if area < 0:
            outline = _rotation_keeping_start(outline, rib.closed)
        le = frame.project(s, rib.le)[1]
        top = frame.project(s, g.add(rib.le, rib.v))[1]
        up = (top[0] - le[0], top[1] - le[1])
        corners = None
        if rib.spec.te_mode == export.TE_LINE:
            corners = upper_first(outline[0], outline[-1], up)
        placed.append(
            Section(
                station=s,
                outline=outline,
                le=le,
                te=te_point(outline, rib.spec.te_mode),
                name=rib.name,
                corners=corners,
                up=up,
            )
        )
    placed.sort(key=lambda sec: sec.station)
    for a, b in zip(placed, placed[1:]):
        if b.station - a.station < MIN_SPACING:
            raise GeometryError(
                f"{a.name} and {b.name} stand {b.station - a.station:.3f} mm apart along the "
                "span. A wing needs its ribs at distinct stations."
            )
    return placed


def check_ribs_agree(ribs: Sequence[Rib]) -> Tuple[str, float]:
    """The trailing-edge handling every rib shares, which the offset wing follows."""
    mode = ribs[0].spec.te_mode
    thickness = ribs[0].spec.te_thickness_mm()
    for rib in ribs[1:]:
        if rib.spec.te_mode != mode or abs(rib.spec.te_thickness_mm() - thickness) > 1e-9:
            raise GeometryError(
                f"{rib.name} finishes its trailing edge differently from {ribs[0].name} "
                f"({rib.spec.te_mode}, {rib.spec.te_thickness_mm():g} mm against {mode}, "
                f"{thickness:g} mm). A wing's ribs have to agree, so its offset can follow them."
            )
    return mode, thickness


def _onto_te_line(p: Point2, a: Point2, b: Point2) -> Tuple[Point2, float, float]:
    """``p`` dropped onto the line through a trailing edge's corners.

    Returns the point, how far ``p`` stands off the line, and how far past the
    nearer corner the point lands (zero between the corners).
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    span = math.hypot(dx, dy)
    if span <= 0.0:
        return a, math.hypot(p[0] - a[0], p[1] - a[1]), 0.0
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (span * span)
    foot = (a[0] + t * dx, a[1] + t * dy)
    off = math.hypot(p[0] - foot[0], p[1] - foot[1])
    return foot, off, max(0.0, -t, t - 1.0) * span


def check_guide(
    guide: Guide, sections: Sequence[Section], what: str, attr: str, te_line: bool = False
) -> None:
    """An edge curve has to pass through every rib, or the loft will not take it.

    A trailing edge closed by a straight line gets an edge along each of its
    corners, which pass through the corners exactly; the curve given for it
    only says where the trailing edge runs. So there it has to meet the line
    through the corners — anywhere between them, or a little beyond — and the
    section takes the point where it does as its trailing edge.
    """
    if guide.start > sections[0].station + EDGE_TOL or guide.end < sections[-1].station - EDGE_TOL:
        raise GeometryError(
            f"The {what} curve runs from station {guide.start:.2f} to {guide.end:.2f} mm, "
            f"but the ribs stand from {sections[0].station:.2f} to "
            f"{sections[-1].station:.2f} mm. It has to reach both end ribs."
        )
    for sec in sections:
        got = guide.at(sec.station)
        if te_line and attr == "te":
            foot, off, past = _onto_te_line(got, sec.outline[0], sec.outline[-1])
            if off <= EDGE_TOL and past <= TE_LINE_REACH:
                sec.te = foot
                continue
            if off <= EDGE_TOL:
                raise GeometryError(
                    f"The {what} curve passes {past:.3f} mm beyond "
                    f"{sec.name or 'a rib'}'s trailing edge (allowed {TE_LINE_REACH:g} mm)."
                )
            want = foot
        else:
            want = getattr(sec, attr)
        miss = math.hypot(got[0] - want[0], got[1] - want[1])
        if miss > EDGE_TOL:
            straight = len(guide.stations) == 2
            advice = (
                " A straight edge only works when the ribs between line up — give it a "
                "curve file instead."
                if straight
                else ""
            )
            raise GeometryError(
                f"The {what} curve misses {sec.name or 'a rib'} by {miss:.3f} mm "
                f"(allowed {EDGE_TOL} mm).{advice}"
            )


def corner_guides(
    axes: Callable[[float], Tuple[Point2, Point2, Point2]],
    sections: Sequence[Section],
    te: Guide,
    samples: Sequence[float],
) -> Tuple[Guide, Guide]:
    """The edges through a straight trailing edge's upper and lower corners.

    A blunt trailing edge is a face, so a loft wants an edge curve along each of
    its two corners rather than one down its middle. Each runs beside the
    trailing-edge curve, as far off it as the corners stand at the sections to
    either side, and through the corners exactly at every section.

    ``axes`` gives the chord frame at a station, as :meth:`Loft.axes` does.
    """
    stations = [sec.station for sec in sections]

    def local(s: float, p: Point2, base: Point2) -> Point2:
        _, u, v = axes(s)
        dx, dy = p[0] - base[0], p[1] - base[1]
        return dx * u[0] + dy * u[1], dx * v[0] + dy * v[1]

    offsets = [
        (local(sec.station, sec.corners[0], sec.te), local(sec.station, sec.corners[1], sec.te))
        for sec in sections
    ]
    exact = {sec.station: sec.corners for sec in sections}
    near = 1e-6
    wanted = sorted(
        set(stations)
        | {s for s in samples if all(abs(s - t) > near for t in stations)}
    )
    uppers: List[Point2] = []
    lowers: List[Point2] = []
    for s in wanted:
        if s in exact:
            upper, lower = exact[s]
        else:
            k = min(max(bisect.bisect_right(stations, s) - 1, 0), len(stations) - 2)
            w = (s - stations[k]) / (stations[k + 1] - stations[k])
            w = min(max(w, 0.0), 1.0)
            base = te.at(s)
            _, u, v = axes(s)
            placed = []
            for side in (0, 1):
                a, b = offsets[k][side], offsets[k + 1][side]
                x = a[0] + w * (b[0] - a[0])
                y = a[1] + w * (b[1] - a[1])
                placed.append((base[0] + x * u[0] + y * v[0], base[1] + x * u[1] + y * v[1]))
            upper, lower = placed
        uppers.append(upper)
        lowers.append(lower)
    return Guide(wanted, uppers), Guide(wanted, lowers)


def check_corner_lines(sections: Sequence[Section]) -> Tuple[Guide, Guide]:
    """Straight trailing-edge corners from root to tip, which the ribs between must meet."""
    first, last = sections[0], sections[-1]
    lines = tuple(
        Guide.straight(first.station, first.corners[side], last.station, last.corners[side])
        for side in (0, 1)
    )
    for sec in sections[1:-1]:
        for side, name in ((0, "upper"), (1, "lower")):
            got = lines[side].at(sec.station)
            want = sec.corners[side]
            miss = math.hypot(got[0] - want[0], got[1] - want[1])
            if miss > EDGE_TOL:
                raise GeometryError(
                    f"A straight trailing edge misses {sec.name or 'a rib'}'s {name} corner "
                    f"by {miss:.3f} mm (allowed {EDGE_TOL} mm). Give the trailing edge a "
                    "curve file instead."
                )
    return lines


# -- the loft ---------------------------------------------------------------


# Cosine spacing: tight at the nose and the tail, where the outline turns.
_T = [0.5 * (1.0 - math.cos(math.pi * j / SAMPLES)) for j in range(SAMPLES + 1)]


def _resample(branch: Sequence[Point2]) -> List[Point2]:
    """A surface as ``SAMPLES + 1`` points, at fixed fractions of its length.

    Length rather than chordwise station: close behind a blunt nose a surface
    can run nearly straight up, and sampling that by station cuts the corner.
    """
    pts = [branch[0]]
    for p in branch[1:]:
        if math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 1e-12:
            pts.append(p)
    if len(pts) < 2:
        raise GeometryError("A surface of this section has no length to follow.")
    run = [0.0]
    for a, b in zip(pts, pts[1:]):
        run.append(run[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = run[-1]
    out = []
    for t in _T:
        at = t * total
        i = min(max(bisect.bisect_right(run, at) - 1, 0), len(run) - 2)
        f = (at - run[i]) / (run[i + 1] - run[i])
        a, b = pts[i], pts[i + 1]
        out.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    return out


def _normalise(sec: Section) -> Tuple[List[Point2], List[Point2]]:
    """The section's two surfaces in its own chord frame: LE at 0, TE point at (1, 0)."""
    cx, cy = sec.te[0] - sec.le[0], sec.te[1] - sec.le[1]
    chord2 = cx * cx + cy * cy
    if chord2 <= 0.0:
        raise GeometryError(f"{sec.name or 'A section'} has no chord.")

    def local(p: Point2) -> Point2:
        dx, dy = p[0] - sec.le[0], p[1] - sec.le[1]
        return ((dx * cx + dy * cy) / chord2, (-dx * cy + dy * cx) / chord2)

    pts = [local(p) for p in sec.outline]
    nose = min(range(len(pts)), key=lambda i: math.hypot(*pts[i]))
    if nose == 0 or nose == len(pts) - 1:
        raise GeometryError(f"{sec.name or 'A section'} has its leading edge at an end.")
    first = list(reversed(pts[: nose + 1]))
    second = pts[nose:]
    return _resample(first), _resample(second)


class Loft:
    """Sections blended along the span, placed by the two edge curves.

    Between two sections the shape is a blend of the two, as a SolidWorks loft
    makes it (see :meth:`_mix`), and the edge curves say where it stands.
    At a section the blend is that section, so the loft passes through it.
    """

    def __init__(self, sections: Sequence[Section], le: Guide, te: Guide,
                 thickness: str = export.THICKNESS_BLENDED):
        if thickness not in export.THICKNESS_CHOICES:
            raise GeometryError(f"Unknown thickness rule {thickness!r}.")
        self.thickness = thickness
        if len(sections) < 2:
            raise GeometryError("A loft needs at least two sections.")
        self.sections = list(sections)
        self.stations = [sec.station for sec in self.sections]
        self.le_guide = le
        self.te_guide = te
        self._shapes = [_normalise(sec) for sec in self.sections]
        self._chords = [
            math.hypot(sec.te[0] - sec.le[0], sec.te[1] - sec.le[1]) for sec in self.sections
        ]
        ends = [
            math.hypot(a[-1][0] - b[-1][0], a[-1][1] - b[-1][1]) <= 1e-9
            for a, b in self._shapes
        ]
        if any(ends) and not all(ends):
            raise GeometryError(
                "Some sections end in a sharp trailing edge and some in a blunt one. "
                "A wing's ribs have to agree."
            )
        self.sharp = all(ends)
        # Index i of an outline: the first surface from its tail to the nose,
        # then the second from the nose to its tail. A sharp outline shares its
        # tail point, so it is not repeated.
        self.count = 2 * SAMPLES + (0 if self.sharp else 1)
        self._frames: Dict[float, Tuple[Point2, Point2]] = {}

    @property
    def start(self) -> float:
        return self.stations[0]

    @property
    def end(self) -> float:
        return self.stations[-1]

    def _frame(self, s: float) -> Tuple[Point2, Point2]:
        hit = self._frames.get(s)
        if hit is None:
            le = self.le_guide.at(s)
            te = self.te_guide.at(s)
            hit = (le, (te[0] - le[0], te[1] - le[1]))
            if len(self._frames) > 50000:
                self._frames.clear()
            self._frames[s] = hit
        return hit

    def _blend(self, s: float) -> Tuple[int, float]:
        k = min(max(bisect.bisect_right(self.stations, s) - 1, 0), len(self.stations) - 2)
        w = (s - self.stations[k]) / (self.stations[k + 1] - self.stations[k])
        return k, min(max(w, 0.0), 1.0)

    def _mix(self, s: float, k: int, w: float, a: Point2, b: Point2) -> Point2:
        """Blend a point of two sections, by the wing's thickness rule.

        *Blended* is what a SolidWorks loft through two ribs does left to
        itself, as measured on a real one through SD7037 ribs of 275 and 136.5
        mm along curved edges: the profiles blended in millimetres, straight
        along the span, then stretched along the chord to reach the edges. Its
        thickness runs straight from root to tip. *Scaled* keeps the airfoil
        whole and scales it to the local chord, which mid-span on that wing
        stands up to 3 mm thicker. Either way the loft only takes that shape
        when the surface guides hold it there.
        """
        if self.thickness == export.THICKNESS_SCALED:
            return a[0] + w * (b[0] - a[0]), a[1] + w * (b[1] - a[1])
        ca, cb = self._chords[k], self._chords[k + 1]
        wa, wb = (1.0 - w) * ca, w * cb
        chord = self.chord(s)
        return (wa * a[0] + wb * b[0]) / (wa + wb), (wa * a[1] + wb * b[1]) / chord

    def _local(self, s: float, k: int, w: float, i: int) -> Point2:
        if i <= SAMPLES:
            side, j = 0, SAMPLES - i
        else:
            side, j = 1, i - SAMPLES
        return self._mix(s, k, w, self._shapes[k][side][j], self._shapes[k + 1][side][j])

    def local_point(self, s: float, i: int) -> Point2:
        """Point ``i`` in the chord frame at ``s``: LE at 0, TE point at (1, 0)."""
        k, w = self._blend(s)
        return self._local(s, k, w, i)

    def upper_side(self, up: Point2) -> int:
        """Which surface — 0, the one the outlines start along, or 1 — is the top.

        ``up`` is the ribs' own up, across the plane at the first section.
        """
        s = self.stations[0]
        _, _, v = self.axes(s)
        facing = 1.0 if v[0] * up[0] + v[1] * up[1] >= 0.0 else -1.0
        a = self.surface_local(s, 0.3, 0)[1]
        b = self.surface_local(s, 0.3, 1)[1]
        return 0 if (a - b) * facing >= 0.0 else 1

    def surface_local(self, s: float, fraction: float, side: int) -> Point2:
        """The point on one surface at ``fraction`` of the chord, in the chord frame."""
        k, w = self._blend(s)
        a, b = self._shapes[k][side], self._shapes[k + 1][side]
        branch = [self._mix(s, k, w, p, q) for p, q in zip(a, b)]
        for p, q in zip(branch, branch[1:]):
            if p[0] <= fraction <= q[0] and q[0] > p[0]:
                t = (fraction - p[0]) / (q[0] - p[0])
                return p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])
        return min(branch, key=lambda p: abs(p[0] - fraction))

    def surface_point(self, s: float, fraction: float, side: int) -> Point2:
        return self.place(s, self.surface_local(s, fraction, side))

    def place(self, s: float, p: Point2) -> Point2:
        (ax, ay), (cx, cy) = self._frame(s)
        return ax + p[0] * cx - p[1] * cy, ay + p[0] * cy + p[1] * cx

    def unplace(self, s: float, p: Point2) -> Point2:
        (ax, ay), (cx, cy) = self._frame(s)
        chord2 = cx * cx + cy * cy
        dx, dy = p[0] - ax, p[1] - ay
        return (dx * cx + dy * cy) / chord2, (-dx * cy + dy * cx) / chord2

    def point(self, s: float, i: int) -> Point2:
        return self.place(s, self.local_point(s, i))

    def outline(self, s: float) -> List[Point2]:
        k, w = self._blend(s)
        return [self.place(s, self._local(s, k, w, i)) for i in range(self.count)]

    def chord(self, s: float) -> float:
        return math.hypot(*self._frame(s)[1])

    def axes(self, s: float) -> Tuple[Point2, Point2, Point2]:
        """LE point, unit chord direction, and unit 'up' at ``s``."""
        le, (cx, cy) = self._frame(s)
        c = math.hypot(cx, cy)
        u = (cx / c, cy / c)
        return le, u, (-u[1], u[0])

    def sweep(self, s: float) -> float:
        return max(self.le_guide.sweep(s), self.te_guide.sweep(s))


@dataclass
class Profile:
    """A profile a loft is given, which every surface guide has to land on."""

    station: float
    points: List[Point2]
    le: Point2


def _on_surface(loft: "Loft", profile: Profile, fraction: float, side: int) -> Tuple[Point2, float]:
    """The profile's own point nearest ``fraction`` of its chord on one surface,
    and the fraction it actually stands at."""
    s = profile.station
    points = profile.points
    nose = min(
        range(len(points)),
        key=lambda i: math.hypot(points[i][0] - profile.le[0], points[i][1] - profile.le[1]),
    )
    branch = points[: nose + 1] if side == 0 else points[nose:]
    best = min(branch, key=lambda p: abs(loft.unplace(s, p)[0] - fraction))
    return best, loft.unplace(s, best)[0]


def surface_guides(
    loft: "Loft", profiles: Sequence[Profile], samples: Sequence[float], up: Point2
) -> List[Tuple[str, Guide]]:
    """Guides along the upper and lower surfaces, through every profile.

    A loft blends between its profiles in its own way, and that is not the
    shape the wing is meant to have; these hold it there, at a few places round
    the section, the way the edge curves hold its edges. Each passes through a
    point of every profile, so the loft accepts it, and follows the wing's
    shape in between. Named ``upper_05`` and so on: surface, then per cent of
    chord.
    """
    profiles = sorted(profiles, key=lambda p: p.station)
    stations = [p.station for p in profiles]
    s0, s1 = stations[0], stations[-1]
    wanted = sorted(
        {s for s in samples if s0 < s < s1 and all(abs(s - t) > 1e-6 for t in stations)}
        | set(stations)
    )
    upper = loft.upper_side(up)
    guides = []
    for side, name in ((upper, "upper"), (1 - upper, "lower")):
        for fraction in SURFACE_GUIDES:
            hits = [_on_surface(loft, p, fraction, side) for p in profiles]
            points = []
            for s in wanted:
                if s in stations:
                    points.append(hits[stations.index(s)][0])
                    continue
                k = min(bisect.bisect_right(stations, s) - 1, len(stations) - 2)
                t = (s - stations[k]) / (stations[k + 1] - stations[k])
                f = hits[k][1] + t * (hits[k + 1][1] - hits[k][1])
                points.append(loft.surface_point(s, f, side))
            guides.append((f"{name}_{round(fraction * 100):02d}", Guide(wanted, points)))
    return guides


def span_samples(start: float, end: float, *extra: Sequence[float]) -> List[float]:
    """Stations to sample a guide at: every few millimetres, and wherever asked."""
    count = max(1, int(math.ceil((end - start) / SURFACE_STEP)))
    grid = {start + (end - start) * k / count for k in range(count + 1)}
    for more in extra:
        grid |= {s for s in more if start <= s <= end}
    return sorted(grid)


# -- where the offset wing's sections go ------------------------------------


@dataclass(frozen=True)
class Station:
    """One section of the offset wing.

    ``rim`` marks a section rounding a closed end grown outward: it is that
    end's outline, offset flat by ``rim_offset``, standing past the end.
    """

    station: float
    rim: str = ""
    rim_offset: float = 0.0


def _subdivide(loft: Loft, a: float, b: float, out: List[float]) -> None:
    span = b - a
    if span < 2.0 * MIN_SPACING:
        return
    m = 0.5 * (a + b)
    le = [loft.le_guide.sweep(x) for x in (a, m, b)]
    te = [loft.te_guide.sweep(x) for x in (a, m, b)]
    turn = max(
        abs(le[0] - le[1]), abs(le[1] - le[2]), abs(te[0] - te[1]), abs(te[1] - te[2])
    )
    if span > MAX_SPACING or turn > MAX_SWEEP_STEP:
        _subdivide(loft, a, m, out)
        out.append(m)
        _subdivide(loft, m, b, out)


def plan_stations(loft: Loft, offset: float, root_end: str, tip_end: str) -> List[Station]:
    """The offset wing's sections, root to tip.

    ``offset`` is signed: positive grows the wing, negative shrinks it. Where
    the sections fall between the ribs depends only on the wing itself, so a
    change of offset does not move them — only a closed end, which moves with
    the offset, can take some away.
    """
    for end in (root_end, tip_end):
        if end not in END_KINDS:
            raise GeometryError(f"Unknown end kind {end!r}.")
    inner: List[float] = []
    for a, b in zip(loft.stations, loft.stations[1:]):
        _subdivide(loft, a, b, inner)
    stations = sorted(set(loft.stations) | set(inner))

    d = abs(offset)
    lo, hi = loft.start, loft.end
    if offset < 0.0:
        if root_end == CLOSED:
            lo = loft.start + d
        if tip_end == CLOSED:
            hi = loft.end - d
        if hi - lo < MIN_SPACING:
            raise GeometryError(
                f"An inward offset of {d:g} mm from both closed ends leaves no span."
            )
        kept = [s for s in stations if lo + MIN_SPACING <= s <= hi - MIN_SPACING]
        return [Station(lo)] + [Station(s) for s in kept] + [Station(hi)]

    chosen = [Station(s) for s in stations]
    if offset > 0.0:
        if root_end == CLOSED:
            rim = [
                Station(lo - d * math.sin(math.radians(t)), "root", d * math.cos(math.radians(t)))
                for t in RIM_ANGLES
            ]
            chosen = list(reversed(rim)) + chosen
        if tip_end == CLOSED:
            chosen += [
                Station(hi + d * math.sin(math.radians(t)), "tip", d * math.cos(math.radians(t)))
                for t in RIM_ANGLES
            ]
    return chosen
