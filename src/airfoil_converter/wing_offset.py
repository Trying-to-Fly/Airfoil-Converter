"""Offset a whole wing: its sections, and the edge curves that loft them.

A flat offset of each rib is a true offset only where the skin runs straight
along the span. Where it leans — swept, tapered, raised — the skin's normal
tilts out of the rib plane, and a flat offset leaves the wall thinner than
asked. Two remedies, chosen per section:

- Where the edges sweep gently, each point is offset further in its own plane
  by exactly what the tilt of the skin there calls for.
- Where they sweep hard, the tilt changes too quickly along the span for that
  to hold, so each point's depth is solved against the true 3D distance to the
  skin around it.

Either way the result is trimmed wherever it comes closer to the skin than the
offset: behind a thin trailing edge, inside a tight nose. What the trim takes
out is then drawn again by walking onto the wall, ten degrees of turn at a
step, so a nose of a quarter of a millimetre comes out as an arc rather than
as the two or three points a fixed spacing leaves on it.

The offset wing gets more sections than the ribs, because a loft only blends
straight between sections and the offset shape does not change in a straight
line. Its edge curves are solved too, then pinned to its own sections so the
loft takes them.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import export, geometry as g
from .geometry import GeometryError, Point2, Vec3
from .wing import (
    CLOSED,
    OFFSET_GUIDES_FROM,
    OPEN,
    STEEP_SWEEP,
    SURFACE_GUIDES,
    Guide,
    Loft,
    Section,
    Station,
    WingFrame,
    corner_guides,
    Profile,
    plan_stations,
    span_samples,
    te_point,
    upper_first,
)
from .wing import surface_guides as wing_surface_guides

# Arc points on the outside of a turn, as geometry's own offset does.
ARC_STEP = math.radians(g.ARC_STEP_DEG)
# The edge curves of the offset wing are sampled this often...
GUIDE_STEP = 20.0
# ...and this often where the edges sweep hard.
GUIDE_STEP_STEEP = 1.0
# A section is added halfway between two whose blend strays this far off the
# offset, as a fraction of it...
REFINE_TOL = 0.02
# ...unless they already stand this close together.
REFINE_MIN = 0.05
REFINE_PASSES = 6
# However the blend behaves, a loft through more sections than this is no use.
MAX_SECTIONS = 60
# A wing lofted through every section gets this many more than the refinement
# asks for, placed where the blend strays most, so that a later change of
# offset that wants a section or two more can keep the count the loft has. On
# one 1 m wing the refinement asked for 16 to 21 across offsets of 1.20 to
# 2.50 mm.
SPARE_SECTIONS = 3
# Kept to the loft's count with fewer sections than the refinement asks for, a
# wing's wall may stray this much of the offset: a loft that loses a profile
# breaks, which is worse than a few hundredths of a millimetre of wall.
KEEP_WALL = 0.05
Progress = Callable[[str], None]


def _inside(p: Point2, poly: Sequence[Point2]) -> bool:
    inside = False
    n = len(poly)
    x, y = p
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _line_distance(p: Point2, a: Point2, b: Point2) -> float:
    ex, ey = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ex, ey)
    if length <= g.POINT_TOL:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs((p[0] - a[0]) * ey - (p[1] - a[1]) * ex) / length


class _Poly:
    """A closed outline, laid out for measuring many points against it quickly.

    The outline is also taken coarsely, a segment for every few points; a point
    is measured exactly only against the fine segments under its nearest few
    coarse ones. Choosing by segment rather than by point matters: the points
    crowd at the nose, so the nearest points can all lie to one side of the
    nearest stretch of outline.
    """

    STRIDE = 8

    def __init__(self, pts: Sequence[Point2]):
        self.pts = list(pts)
        n = len(self.pts)
        self.segs = [self._segment(self.pts[i], self.pts[(i + 1) % n]) for i in range(n)]
        marks = list(range(0, n, self.STRIDE)) + [n]
        self.coarse = [
            (self._segment(self.pts[a % n], self.pts[b % n]), a, b)
            for a, b in zip(marks, marks[1:])
        ]

    @staticmethod
    def _segment(a: Point2, b: Point2) -> Tuple[float, float, float, float, float]:
        dx, dy = b[0] - a[0], b[1] - a[1]
        span = dx * dx + dy * dy
        return a[0], a[1], dx, dy, (1.0 / span if span > 0.0 else 0.0)

    @staticmethod
    def _gap2(px: float, py: float, seg: Tuple[float, float, float, float, float]) -> float:
        ax, ay, dx, dy, inv = seg
        t = ((px - ax) * dx + (py - ay) * dy) * inv
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        ex = ax + t * dx - px
        ey = ay + t * dy - py
        return ex * ex + ey * ey

    def distance(self, p: Point2) -> float:
        px, py = p
        gap2 = self._gap2
        near = sorted((gap2(px, py, seg), a, b) for seg, a, b in self.coarse)[:3]
        segs = self.segs
        n = len(segs)
        best = math.inf
        for _, a, b in near:
            for j in range(a, b):
                d2 = gap2(px, py, segs[j % n])
                if d2 < best:
                    best = d2
        return math.sqrt(best)

    def inside(self, p: Point2) -> bool:
        return _inside(p, self.pts)


def _near_distance(p: Point2, poly: Sequence[Point2]) -> float:
    return _Poly(poly).distance(p)


def _parabola(fa: float, fb: float, fc: float) -> float:
    """Where a parabola through three evenly spaced values bottoms out, in steps from the middle."""
    denom = fa - 2.0 * fb + fc
    if denom <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, 0.5 * (fa - fc) / denom))


class Skin:
    """The outer wing as a surface to measure distances to.

    An open end carries the skin on, mirrored — the other half of the wing, or
    whatever it joins. A closed end is a flat face, and counts as skin too.

    Distances are true 3D ones: a point is measured against each nearby
    station's own plane, which need not be parallel to its own.
    """

    def __init__(self, loft: Loft, root_end: str, tip_end: str, step: float,
                 frame: WingFrame):
        self.loft = loft
        self.root_end = root_end
        self.tip_end = tip_end
        self.step = step
        self.frame = frame
        self._outlines: Dict[int, Optional[_Poly]] = {}
        self._points: Dict[Tuple[int, int], Optional[Point2]] = {}
        self._face_polys: Dict[float, _Poly] = {}
        # Edge points already walked to, by station and height.
        self.walked: Dict[Tuple[float, float, bool], Point2] = {}

    def _fold(self, s: float) -> Optional[float]:
        """The station whose skin stands at ``s``, or None past a closed end."""
        lo, hi = self.loft.start, self.loft.end
        if s < lo:
            return 2.0 * lo - s if self.root_end == OPEN and 2.0 * lo - s <= hi else None
        if s > hi:
            return 2.0 * hi - s if self.tip_end == OPEN and 2.0 * hi - s >= lo else None
        return s

    def _seen_from(self, s: float, point: Vec3) -> Vec3:
        """The point as the skin past an open end sees it: mirrored in that end."""
        if s < self.loft.start:
            return self.frame.mirror(point, self.loft.start)
        if s > self.loft.end:
            return self.frame.mirror(point, self.loft.end)
        return point

    def outline(self, s: float) -> Optional[List[Point2]]:
        folded = self._fold(s)
        return None if folded is None else self.loft.outline(folded)

    def _grid_outline(self, k: int) -> Optional[_Poly]:
        if k not in self._outlines:
            if len(self._outlines) > 4000:
                self._outlines.clear()
            outline = self.outline(k * self.step)
            self._outlines[k] = None if outline is None else _Poly(outline)
        return self._outlines[k]

    def _face(self, end: float) -> _Poly:
        if end not in self._face_polys:
            self._face_polys[end] = _Poly(self.loft.outline(end))
        return self._face_polys[end]

    def _grid_point(self, k: int, i: int) -> Optional[Point2]:
        key = (k, i)
        if key not in self._points:
            if len(self._points) > 200000:
                self._points.clear()
            folded = self._fold(k * self.step)
            self._points[key] = None if folded is None else self.loft.point(folded, i)
        return self._points[key]

    def _walk(
        self, s: float, at: Callable[[float, Optional[int]], Optional[Tuple[float, float]]]
    ) -> float:
        """Least distance over the stations near ``s``: a grid walk, then a parabola.

        ``at`` gives, for a station, how far off its plane the point stands and
        how far from the skin it lands on it.
        """
        step = self.step
        k0 = int(round(s / step))
        values: Dict[int, float] = {}
        best, best_k = math.inf, None
        for direction in (0, 1, -1):
            k = k0 if direction == 0 else k0 + direction
            while True:
                got = at(k * step, k)
                if got is None:
                    break  # past a closed end: nothing further this way
                off, across = got
                if direction != 0 and abs(off) >= best:
                    break
                f = math.hypot(off, across)
                values[k] = f
                if f < best:
                    best, best_k = f, k
                if direction == 0:
                    break
                k += direction
        if best_k is None:
            return math.inf
        fa, fc = values.get(best_k - 1), values.get(best_k + 1)
        if fa is not None and fc is not None:
            t = _parabola(fa, best, fc)
            if t:
                got = at((best_k + t) * step, None)
                if got is not None:
                    best = min(best, math.hypot(*got))
        return best

    def _faces(self, point: Vec3) -> float:
        best = math.inf
        for kind, end in ((self.root_end, self.loft.start), (self.tip_end, self.loft.end)):
            if kind != CLOSED:
                continue
            off, q = self.frame.project(end, point)
            if abs(off) >= best:
                continue
            face = self._face(end)
            if face.inside(q):
                best = min(best, abs(off))
            else:
                best = min(best, math.hypot(off, face.distance(q)))
        return best

    def distance(self, s: float, p: Point2, faces: bool = True) -> float:
        """True 3D distance from wing point ``(s, p)`` to the outer skin.

        ``faces`` counts a closed end's flat face as skin, which it is; leave it
        out only where the face is known to stand at least as far off.
        """
        point = self.frame.to_3d(s, p)

        def at(station: float, k: Optional[int]) -> Optional[Tuple[float, float]]:
            folded = self._fold(station)
            if folded is None:
                return None
            off, q = self.frame.project(folded, self._seen_from(station, point))
            if k is not None:
                return off, self._grid_outline(k).distance(q)
            return off, _near_distance(q, self.loft.outline(folded))

        skin = self._walk(s, at)
        return min(skin, self._faces(point)) if faces else skin

    def local_distance(self, s: float, p: Point2, i: int, lines: bool) -> float:
        """Distance to the skin swept by the two segments meeting at point ``i``.

        ``lines`` measures to the segments as infinite lines, which is what puts
        a mitred corner exactly on the offset.
        """
        n = self.loft.count
        point = self.frame.to_3d(s, p)

        def at(station: float, k: Optional[int]) -> Optional[Tuple[float, float]]:
            folded = self._fold(station)
            if folded is None:
                return None
            if k is not None:
                a, b, c = (self._grid_point(k, j % n) for j in (i - 1, i, i + 1))
            else:
                a, b, c = (self.loft.point(folded, j % n) for j in (i - 1, i, i + 1))
            off, q = self.frame.project(folded, self._seen_from(station, point))
            if lines:
                return off, min(_line_distance(q, a, b), _line_distance(q, b, c))
            return off, min(
                g._point_segment_distance(q, a, b), g._point_segment_distance(q, b, c)
            )

        return self._walk(s, at)


# -- offsetting one section -------------------------------------------------


def _normals(poly: Sequence[Point2]) -> List[Tuple[Point2, Point2, Point2, Point2]]:
    """Per vertex: incoming and outgoing directions and outward normals (CCW loop)."""
    out = []
    n = len(poly)
    for i in range(n):
        e0 = g._direction(poly[i - 1], poly[i])
        e1 = g._direction(poly[i], poly[(i + 1) % n])
        out.append((e0, e1, (e0[1], -e0[0]), (e1[1], -e1[0])))
    return out


def _tilt_depths(skin: Skin, s: float, poly: Sequence[Point2], d: float) -> List[float]:
    """In-plane depth per point giving a true wall of ``d`` where the skin leans.

    Near a point the skin is a plane, and its offset a parallel plane ``d``
    further in. The station's plane cuts that offset plane at ``d / (N . n)``
    along the section's own normal ``n`` — ``N`` being the skin's true normal,
    found from the way the point runs along the span and around the section.
    """
    loft, frame = skin.loft, skin.frame
    h = 0.25
    lo, hi = s - h, s + h
    if lo < loft.start:
        lo = s
    if hi > loft.end:
        hi = s
    if hi - lo <= 0.0:
        return [d] * len(poly)
    _, _, e1, e2 = frame.frame(s)
    before = [frame.to_3d(lo, loft.point(lo, i)) for i in range(len(poly))]
    after = [frame.to_3d(hi, loft.point(hi, i)) for i in range(len(poly))]
    depths = []
    for i, (e0, e1_, n0, n1) in enumerate(_normals(poly)):
        nx, ny = n0[0] + n1[0], n0[1] + n1[1]
        size = math.hypot(nx, ny) or 1.0
        nx, ny = nx / size, ny / size
        across = g.add(g.scale(e1, -ny), g.scale(e2, nx))    # along the section
        outward = g.add(g.scale(e1, nx), g.scale(e2, ny))    # the section's own normal
        along = g.sub(after[i], before[i])                    # along the span
        skin_normal = g.cross(across, along)
        size = g.length(skin_normal)
        if size <= 1e-12:
            depths.append(d)
            continue
        lean = g.dot(skin_normal, outward) / size
        depths.append(d / abs(lean) if abs(lean) > 1e-3 else d * 1e3)
    return depths


def _vertex_offsets(
    poly: Sequence[Point2], depths: Sequence[float], sign: float
) -> List[List[Tuple[Point2, float]]]:
    """Offset every vertex by its own depth: arcs outside a turn, mitres inside."""
    out: List[List[Tuple[Point2, float]]] = []
    for (x, y), (e0, e1, n0, n1), depth in zip(poly, _normals(poly), depths):
        dist = sign * depth
        turn = g._cross2(e0, e1)
        if turn * dist > 0.0:
            angle = math.atan2(turn, g._dot2(e0, e1))
            steps = max(1, math.ceil(abs(angle) / ARC_STEP))
            if steps == 1:
                # One point midway round a slight turn: two a hair apart make a
                # curve file that SolidWorks will not import.
                c, sn = math.cos(angle / 2.0), math.sin(angle / 2.0)
                rx, ry = n0[0] * c - n0[1] * sn, n0[0] * sn + n0[1] * c
                out.append([((x + rx * dist, y + ry * dist), depth)])
                continue
            arc = []
            for k in range(steps + 1):
                c, sn = math.cos(angle * k / steps), math.sin(angle * k / steps)
                rx, ry = n0[0] * c - n0[1] * sn, n0[0] * sn + n0[1] * c
                arc.append(((x + rx * dist, y + ry * dist), depth))
            out.append(arc)
            continue
        mx, my = n0[0] + n1[0], n0[1] + n1[1]
        length = math.hypot(mx, my)
        if length > 1e-9:
            mx, my = mx / length, my / length
            reach = dist / g._dot2((mx, my), n0)
            if abs(reach) <= g.MITER_LIMIT * depth:
                out.append([((x + mx * reach, y + my * reach), depth)])
                continue
        # A corner that nearly reverses is bevelled; the trim sorts it out.
        out.append([
            ((x + n0[0] * dist, y + n0[1] * dist), depth),
            ((x + n1[0] * dist, y + n1[1] * dist), depth),
        ])
    return out


def _trim(
    raw: Sequence[Tuple[Point2, float]], keep: Callable[[Point2, float], bool]
) -> Tuple[List[Point2], List[bool]]:
    """Split the offset at its self-crossings and keep what stands far enough off.

    Also says, for each kept segment, whether it bridges points the trim took
    out — the only places a straight line has replaced the offset.
    """
    pts = [p for p, _ in raw]
    need = [r for _, r in raw]
    n = len(pts)
    hits: List[List[Tuple[float, Point2, float]]] = [[] for _ in range(n)]
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            crossing = g._segment_crossing(a, b, pts[j], pts[(j + 1) % n])
            if crossing is None:
                continue
            t, u, p = crossing
            r = min(need[i], need[(i + 1) % n], need[j], need[(j + 1) % n])
            hits[i].append((t, p, r))
            hits[j].append((u, p, r))
    dense: List[Tuple[Point2, float]] = []
    for i in range(n):
        dense.append((pts[i], need[i]))
        dense.extend((p, r) for _, p, r in sorted(hits[i]))
    kept = [(k, p) for k, (p, r) in enumerate(dense) if keep(p, r)]
    loop: List[Point2] = []
    marks: List[int] = []
    for k, p in kept:
        if loop and math.hypot(p[0] - loop[-1][0], p[1] - loop[-1][1]) <= g.POINT_TOL:
            continue
        loop.append(p)
        marks.append(k)
    while len(loop) > 2 and math.hypot(loop[0][0] - loop[-1][0], loop[0][1] - loop[-1][1]) <= g.POINT_TOL:
        loop.pop()
        marks.pop()
    m = len(dense)
    bridges = [
        (marks[(i + 1) % len(marks)] - marks[i]) % m > 1 for i in range(len(marks))
    ]
    return loop, bridges


# A gap in the trimmed offset longer than this is checked, and filled if the
# straight line across it strays off the wall by more than GAP_TOL of the
# offset.
GAP = 0.2
GAP_TOL = 0.005
# However straight the line is, the loop must not turn more than this at a
# point: past about ten degrees a step the wall is being drawn as a polygon
# rather than followed. An inward offset's nose used to come out with four
# points and fifty degrees between them, and SolidWorks would not make a solid
# of it: it draws each half of such a section as a spline that ends at that
# vertex with no curvature, so the two halves graze each other within microns
# of it and the loop is no use as the boundary of a face. The surface loft
# still built, which is what made it look like a tolerance problem.
TURN_STEP = math.radians(10.0)
# A turn is only worth filling in if the wall really does bend there. Walk the
# middle of the gap onto the wall: if it moves off the line by more than this
# much of the gap's own length the wall is curved, and if it stays on the line
# the turn is a corner — the tail, or where two offsets meet — which is meant
# to stay one point.
SAG_FRACTION = 0.02
# What the walk itself can tell apart. The wall is found by measuring to a skin
# sampled every d/6 along the span, so a point can sit a thousandth or two off
# where the true wall is; chasing anything smaller than that is chasing noise.
MIN_DEVIATION = 0.002
# However tight the turn, no step shorter than this. Below it the points stand
# closer to each other than they do to the wall they were walked onto.
MIN_FILL = 0.025
# How far off a line the wall it spans can be, as a fraction of the line's own
# length: half, for a bend of up to a half turn. A walk that comes back with
# more than that has found some other part of the wall.
REACH = 0.75
# A bend tighter than this is left as a corner rather than drawn as an arc:
# ten degrees a step round it would want steps shorter than MIN_FILL, and a
# corner drawn with one vertex and full-length steps either side is what
# SolidWorks makes a clean job of. A deep offset's nose, and every tail, is
# one of these.
MIN_ARC = MIN_FILL / TURN_STEP


def _turn(a: Point2, b: Point2, c: Point2) -> float:
    """How far the way from ``a`` to ``c`` bends at ``b``, in radians."""
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = c[0] - b[0], c[1] - b[1]
    size = math.hypot(ux, uy) * math.hypot(vx, vy)
    if size <= 0.0:
        return 0.0
    return math.acos(max(-1.0, min(1.0, (ux * vx + uy * vy) / size)))


def _fill_gaps(
    skin: Skin, s: float, loop: List[Point2], bridges: Sequence[bool], d: float, inward: bool
) -> Tuple[List[Point2], List[bool]]:
    """Put back the stretch of offset the trim took out and a straight line replaced.

    Where the skin at a neighbouring station stands closer than the one in this
    plane — a leading edge hooking back — points solved against their own
    patch of skin come out too close and are trimmed, and the offset jumps
    straight across. The true offset lies off that line; walk to it.

    Two things ask for points. A line that strays off the wall is the plain
    case, measured by what the wall's distance does along it. A line that the
    loop turns hard at either end of is the other: round a tight nose the
    distance hardly moves — near a tip what the wall stands off is the end
    face, out of this plane, so walking about in the plane barely changes it —
    and the only sign left that the wall bends is the loop's own corner.
    """
    poly = skin.loft.outline(s)
    out: List[Point2] = []
    # Which of the segments out of here were just made, and so want checking.
    fresh: List[bool] = []
    n = len(loop)
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        out.append(a)
        fresh.append(False)
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if not bridges[i]:
            continue
        bend = max(_turn(loop[i - 1], a, b), _turn(a, b, loop[(i + 2) % n]))
        straying = False
        if length > GAP:
            probes = [(a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])) for f in (0.25, 0.5, 0.75)]
            straying = any(
                abs(skin.distance(s, q, faces=False) - d) > GAP_TOL * d for q in probes
            )
        if not straying and (bend <= TURN_STEP or length <= 2.0 * MIN_FILL):
            continue
        ex, ey = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        # Toward the skin: outward from an inner loop, inward from an outer one.
        nx, ny = (ey, -ex) if inward else (-ey, ex)
        count = int(math.ceil(length / GAP)) if straying else 0
        if not straying:
            middle = (0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]))
            found = _level_point(skin, s, poly, middle, (nx, ny), d, inward)
            if found is None:
                continue
            off = math.hypot(found[0] - middle[0], found[1] - middle[1])
            if off <= max(SAG_FRACTION * length, MIN_DEVIATION):
                # The wall runs straight across this gap, so the turn at its
                # end is a corner of the offset and belongs where it is.
                continue
            if off > REACH * length:
                # A line across a bend of less than a half turn stands off it
                # by at most half its own length. Further than that and the
                # walk has left this stretch of wall for another one — round
                # the nose, or the far side of a corner — and what it found is
                # not a point of this gap.
                continue
            # A chord of length L across an arc that turns by t stands L/2 ·
            # tan(t/4) off it, which is what says how tight the bend is and
            # how many steps of ten degrees it takes.
            turn = 4.0 * math.atan(2.0 * off / length)
            if length < 2.0 * MIN_ARC * math.sin(0.5 * min(turn, math.pi)):
                continue   # tighter than MIN_ARC: a corner, left as one point
            count = int(math.ceil(turn / TURN_STEP))
        count = min(max(count, 2), 40, max(2, int(length / MIN_FILL)))
        for k in range(1, count):
            c = (a[0] + ex * length * k / count, a[1] + ey * length * k / count)
            found = _level_point(skin, s, poly, c, (nx, ny), d, inward)
            if found is not None and not straying and (
                math.hypot(found[0] - c[0], found[1] - c[1]) > REACH * length
            ):
                found = None   # off this stretch of wall altogether; see above
            if found is not None:
                fresh[-1] = True
                out.append(found)
                fresh.append(True)
    return out, fresh


def _level_point(
    skin: Skin, s: float, poly: Sequence[Point2], c: Point2, toward: Point2, d: float, inward: bool
) -> Optional[Point2]:
    """The point along a line through ``c`` standing exactly ``d`` off the skin."""

    def excess(t: float) -> Optional[float]:
        p = (c[0] + toward[0] * t, c[1] + toward[1] * t)
        if _inside(p, poly) != inward:
            return None
        # A closed end's face stands a full offset or more away from any
        # section, so it never shapes the wall here — but at the section that
        # stands exactly an offset in from it, it would hide where the wall is.
        return skin.distance(s, p, faces=False) - d

    e0 = excess(0.0)
    if e0 is None:
        return None
    direction = 1.0 if e0 > 0 else -1.0
    lo, hi = 0.0, None
    step = d / 8.0
    t = 0.0
    while t < 2.0 * d:
        t += step
        e = excess(direction * t)
        if e is None or (e > 0) != (e0 > 0):
            hi = t
            break
        lo = t
    if hi is None:
        return None
    for _ in range(20):
        mid = 0.5 * (lo + hi)
        e = excess(direction * mid)
        if e is not None and (e > 0) == (e0 > 0):
            lo = mid
        else:
            hi = mid
    t = direction * lo
    return (c[0] + toward[0] * t, c[1] + toward[1] * t)


def _start_at_tail(loop: List[Point2], tail: Callable[[Point2], float]) -> List[Point2]:
    start = max(range(len(loop)), key=lambda i: tail(loop[i]))
    return loop[start:] + loop[:start]


def offset_outline(
    skin: Skin, s: float, offset: float, exact: bool
) -> List[Point2]:
    """The outer section at ``s`` offset by ``offset`` (signed), in wing coordinates.

    Returns a closed loop, counter-clockwise, starting at its aftmost point and
    with that point repeated at the end.
    """
    loft = skin.loft
    d = abs(offset)
    sign = 1.0 if offset > 0 else -1.0
    poly = loft.outline(s)
    if g.signed_area(poly) <= 0:
        raise GeometryError("The section at this station encloses no area.")

    if exact:
        # Inward, a point where the section turns right back on itself — a
        # sharp tail — has no side to offset from, and the trim closes the gap
        # it leaves. Outward, the same corner is rounded by an arc, and needs it.
        skip = set()
        if sign < 0:
            for i, (e0, e1, _, _) in enumerate(_normals(poly)):
                if g._dot2(e0, e1) < -0.3:
                    skip.add(i)
        guess = _tilt_depths(skin, s, poly, d)
        depths = []
        for i, (p, (e0, e1, n0, n1)) in enumerate(zip(poly, _normals(poly))):
            if i in skip:
                depths.append(guess[i])
                continue
            depths.append(_solve_depth(skin, s, p, i, n0, n1, sign, d, guess[i]))
        per_vertex = _vertex_offsets(poly, depths, sign)
        raw = [item for i, items in enumerate(per_vertex) if i not in skip for item in items]
        tol = d * (1.0 - 2e-3)

        def keep(p: Point2, r: float) -> bool:
            if _inside(p, poly) != (sign < 0):
                return False
            return skin.distance(s, p) >= tol

    else:
        depths = _tilt_depths(skin, s, poly, d)
        raw = [item for items in _vertex_offsets(poly, depths, sign) for item in items]

        def keep(p: Point2, r: float) -> bool:
            return g.distance_to_loop(p, poly) >= r * (1.0 - 1e-6)

    trimmed, bridges = _trim(raw, keep)
    if len(trimmed) < 3:
        raise GeometryError(
            f"An inward offset of {d:g} mm eats the whole section at station {s:.1f} mm — "
            "nothing is left there. Try a smaller distance."
            if sign < 0
            else f"The offset collapses the section at station {s:.1f} mm."
        )
    if exact:
        # Walking onto the wall across a wide gap can leave the new points
        # unevenly spread, so what was just made is checked again.
        for _ in range(4):
            trimmed, bridges = _fill_gaps(skin, s, trimmed, bridges, d, sign < 0)
            if not any(bridges):
                break
    trimmed = _untangle(trimmed)
    _, u, _ = loft.axes(s)
    along = [p[0] * u[0] + p[1] * u[1] for p in trimmed]
    middle = 0.5 * (min(along) + max(along))
    trimmed = _despike(trimmed, lambda p: p[0] * u[0] + p[1] * u[1] < middle)
    trimmed = _uncrowd(trimmed)
    if g.signed_area(trimmed) < 0:
        trimmed.reverse()
    looped = _start_at_tail(trimmed, lambda p: p[0] * u[0] + p[1] * u[1])
    return g.auto_close(looped)


def _crossing_pairs(loop: Sequence[Point2]) -> List[Tuple[int, int, Point2]]:
    """Every pair of non-neighbouring segments of a closed loop that cross."""
    n = len(loop)
    boxes = []
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        boxes.append((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])))
    out = []
    for i in range(n):
        x0, x1, y0, y1 = boxes[i]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            u0, u1, v0, v1 = boxes[j]
            if u0 > x1 or u1 < x0 or v0 > y1 or v1 < y0:
                continue
            hit = g._segment_crossing(loop[i], loop[(i + 1) % n], loop[j], loop[(j + 1) % n])
            if hit is not None:
                out.append((i, j, hit[2]))
    return out


def _untangle(loop: List[Point2]) -> List[Point2]:
    """Cut out any little loop the offset still makes, keeping the crossing as a corner.

    Where two branches of an offset meet at a crease, what lies past the
    crossing — a swallowtail — stands closer to the skin than the offset, and
    the trim takes it out. Not all of it, always: its points can stand within
    the trim's tolerance of the wall, and a few are kept. Walking onto the wall
    across a gap near it can then find the far side of the nose, and the next
    pass walks on from there. At the tip of the 1.20, 1.22, 1.23 and 1.33 mm
    offsets of one wing that grew into loops round the nose — thirty-seven
    crossings at 1.33 — which SolidWorks will not take as a curve, and which
    the wall model measured at a hundredth of a millimetre. Of the two loops a
    crossing makes, the smaller is the one that does not belong: cutting it
    out, crossing by crossing, left every section of those wings within 0.06
    mm of the offset.
    """
    for _ in range(len(loop)):
        pairs = _crossing_pairs(loop)
        if not pairs:
            break
        i, j, at = pairs[0]
        between = loop[i + 1:j + 1]
        rest = loop[j + 1:] + loop[:i + 1]
        if abs(g.signed_area(between + [at])) <= abs(g.signed_area(rest + [at])):
            loop = loop[:i + 1] + [at] + loop[j + 1:]
        else:
            loop = [at] + between
    return loop


# A loop that turns back on itself by more than this at a point has a spike
# there, not a corner.
SPIKE_DEG = 150.0


def _despike(loop: List[Point2], where: Callable[[Point2], bool]) -> List[Point2]:
    """The loop without the hairpins trimming can leave where two offsets meet.

    Where an inward offset comes to a corner at the nose, the trim can keep a
    point a hair's breadth past it, so the outline runs out and straight back.
    A spline through that loops, and SolidWorks will not join the curve at all.
    Only points ``where`` says are taken out: a sharp trailing edge turns back
    on itself too, and is meant to.
    """
    pts = list(loop)
    if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= 1e-12:
        pts.pop()
    limit = math.cos(math.radians(SPIKE_DEG))
    changed = True
    while changed and len(pts) > 3:
        changed = False
        n = len(pts)
        for i in range(n):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            ux, uy = b[0] - a[0], b[1] - a[1]
            vx, vy = c[0] - b[0], c[1] - b[1]
            size = math.hypot(ux, uy) * math.hypot(vx, vy)
            if (size <= 0.0 or (ux * vx + uy * vy) / size < limit) and where(b):
                del pts[i]
                changed = True
                break
    return pts


# Points of a trimmed offset this close together, in mm, are candidates for
# being one point — but only the ones that are also this small a fraction of
# the steps on either side of them.
CROWD_STEP = 0.05
CROWD_FRACTION = 0.25
# Closer than this and they are one point whatever their neighbours do. The
# gaps filled round a nose are never smaller than MIN_FILL, so anything under
# this is a leftover of the trim rather than a step of the wall — and at a
# corner the trim can leave several, each too near the last for the rule above
# to see, because the step before them is just as short.
HUDDLE_STEP = 0.02


def _unhuddle(pts: List[Point2]) -> List[Point2]:
    """The loop with no two points left closer together than ``HUDDLE_STEP``.

    The tightest pair goes first, and of the two the one standing nearer the
    line across the pair — so a corner keeps its own point and what is dropped
    is the near-copy of it beside it. Taking one point at a time rather than a
    whole run is what stops a stretch that is merely fine from collapsing: each
    time one goes the gap left is twice what it was.
    """
    while len(pts) > 3:
        n = len(pts)
        at, tightest = -1, HUDDLE_STEP
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            gap = math.hypot(b[0] - a[0], b[1] - a[1])
            if gap < tightest:
                at, tightest = i, gap
        if at < 0:
            break
        before, after = pts[at - 1], pts[(at + 2) % n]
        first, second = pts[at], pts[(at + 1) % n]
        keep_first = _line_distance(first, before, after) >= _line_distance(second, before, after)
        del pts[at if not keep_first else (at + 1) % n]
    return pts


def _uncrowd(loop: List[Point2]) -> List[Point2]:
    """The loop with a huddle of points at a corner collapsed onto the corner.

    Where an inward offset comes to a corner at the nose, the offsets of two
    neighbouring vertices of the section both land within a hair of it, and
    both can stand just far enough off the skin for the trim to keep them.
    Neither is a spike, so :func:`_despike` leaves them, and they are further
    apart than the thinning in :mod:`geometry` takes out. One real wing's tip,
    offset 1.35 mm inward, came out with 0.014 mm between two steps of 0.2 and
    0.4 mm. SolidWorks draws a Curve Through XYZ Points as one spline
    parametrised by the length along it, so a step a twentieth of its
    neighbours is a kink in that spline, and the loft folds along it — which
    is what a solid loft SolidWorks refuses looks like from here. A sharp tail
    collects the same huddle, from the two sides of the offset meeting there.

    A huddle has to be short twice over: shorter than ``CROWD_STEP`` outright,
    and shorter than a quarter of the steps entering and leaving it. The
    fraction is what leaves a nose alone that is merely dense — a nose sampled
    at 0.035 mm between 0.07 mm steps is the shape, evenly drawn, and a spline
    through it is smooth — and the 0.05 mm ceiling keeps the rule away from
    anything anyone would call geometry, whatever its neighbours do. What is
    kept is whichever point of the huddle stands furthest off the line across
    it: that is the corner, and rounding the corner off is the one thing this
    must not do.
    """
    pts = _unhuddle(list(loop))
    n = len(pts)
    if n < 5:
        return pts
    step = [math.hypot(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1])
            for i in range(n)]
    # Walk from a point something arrives at from far enough away, so that no
    # huddle is cut in half by the start of the loop.
    start = next((i for i in range(n) if step[i - 1] >= CROWD_STEP), None)
    if start is None:
        return pts
    out: List[Point2] = []
    k = 0
    while k < n:
        run = [(start + k) % n]
        while k + 1 < n and step[run[-1]] < CROWD_STEP:
            k += 1
            run.append((start + k) % n)
        k += 1
        if len(run) > 1:
            before, after = pts[run[0] - 1], pts[(run[-1] + 1) % n]
            span = sum(step[i] for i in run[:-1])
            if span < CROWD_FRACTION * min(step[run[0] - 1], step[run[-1]]):
                out.append(max(run, key=lambda i: _line_distance(pts[i], before, after)))
                continue
        out.extend(run)
    return [pts[i] for i in out]


def _solve_depth(
    skin: Skin, s: float, p: Point2, i: int, n0: Point2, n1: Point2,
    sign: float, d: float, guess: float,
) -> float:
    """Depth along the corner's bisector at which the local skin stands ``d`` off."""
    mx, my = n0[0] + n1[0], n0[1] + n1[1]
    length = math.hypot(mx, my) or 1.0
    mx, my = mx / length, my / length
    # Inside a turn the offset is a mitre, measured to the segments' lines;
    # outside it is an arc, measured to the segments themselves.
    lines = sign < 0
    reach = 1.0 / max(g._dot2((mx, my), n0), 1e-6) if lines else 1.0

    def wall(depth: float) -> float:
        q = (p[0] + sign * mx * depth * reach, p[1] + sign * my * depth * reach)
        return skin.local_distance(s, q, i, lines)

    # Secant from the tilt estimate; the wall grows almost linearly with depth.
    k0, w0 = guess, wall(guess)
    k1 = k0 * d / w0 if w0 > 1e-12 else k0 * 1.5
    w1 = wall(k1)
    for _ in range(8):
        if abs(w1 - d) <= 1e-4 * d or abs(w1 - w0) <= 1e-12:
            break
        k0, w0, k1 = k1, w1, k1 + (d - w1) * (k1 - k0) / (w1 - w0)
        k1 = max(k1, 0.25 * d)
        w1 = wall(k1)
    return k1


# -- the offset wing --------------------------------------------------------


@dataclass
class OffsetSection:
    station: Station
    curves: List[Tuple[str, List[Point2], bool]]  # wing coordinates, as finish_surface gives
    le: Point2
    te: Point2
    sharp_te: Point2
    exact: bool
    section: Section


@dataclass
class WallReport:
    thinnest: float
    thickest: float
    thinnest_at: Tuple[float, str]
    thickest_at: Tuple[float, str]
    outside: int
    checked: int


@dataclass
class OffsetWing:
    frame: WingFrame
    offset: float
    sections: List[OffsetSection]
    le: Guide
    te: Guide
    le_samples: List[float] = field(default_factory=list)
    te_samples: List[float] = field(default_factory=list)
    steep_from: Optional[float] = None
    te_shift: Tuple[float, float] = (0.0, 0.0)
    report: Optional[WallReport] = None
    # For a blunt trailing edge: the edges along its upper and lower corners.
    te_corners: Optional[Tuple[Guide, Guide]] = None
    thickness: str = export.THICKNESS_BLENDED

    def loft(self) -> Loft:
        return Loft([sec.section for sec in self.sections], self.le, self.te, self.thickness)


def _to_local(loft: Loft, s: float, p: Point2) -> Point2:
    le, u, v = loft.axes(s)
    dx, dy = p[0] - le[0], p[1] - le[1]
    return dx * u[0] + dy * u[1], dx * v[0] + dy * v[1]


def _from_local(loft: Loft, s: float, p: Point2) -> Point2:
    le, u, v = loft.axes(s)
    return le[0] + p[0] * u[0] + p[1] * v[0], le[1] + p[0] * u[1] + p[1] * v[1]


def _finish(
    loft: Loft, s: float, loop: List[Point2], mode: str, thickness: float, up_sign: float = 1.0
) -> Tuple[List[Tuple[str, List[Point2], bool]], Point2, Point2, Point2, Section]:
    """Cut and close the offset loop the way the ribs are, in the section's chord frame.

    ``up_sign`` says which side of the chord frame is the ribs' top, for
    telling a blunt trailing edge's upper corner from its lower one.
    """
    local = [_to_local(loft, s, p) for p in loop]
    sharp = max(local, key=lambda p: p[0])
    surface = local
    if thickness:
        surface = g.blunt_trailing_edge(surface, thickness, keep_chord=False)
    curves_local = export.finish_surface(surface, mode)
    curves = [
        (role, g.thin_curve([_from_local(loft, s, p) for p in pts]), closed)
        for role, pts, closed in curves_local
    ]
    airfoil = curves[0][1]
    nose = min(airfoil, key=lambda p: _to_local(loft, s, p)[0])
    te = te_point(airfoil, mode)
    outline = airfoil if mode != export.TE_OPEN else g.auto_close(airfoil)
    corners = None
    for role, pts, _ in curves:
        if role == export.ROLE_TE:
            _, _, v = loft.axes(s)
            corners = upper_first(pts[0], pts[-1], (v[0] * up_sign, v[1] * up_sign))
    section = Section(station=s, outline=outline, le=nose, te=te, corners=corners)
    return curves, nose, te, _from_local(loft, s, sharp), section


def _rim(
    loft: Loft, station: Station, mode: str, thickness: float, up_sign: float = 1.0
) -> Tuple[List[Tuple[str, List[Point2], bool]], Point2, Point2, Point2, Section]:
    """A section rounding a closed end: that end's outline, offset flat."""
    end = loft.start if station.rim == "root" else loft.end
    local = [_to_local(loft, end, p) for p in loft.outline(end)]
    if not loft.sharp:
        local = local + [local[0]]
    grown = g.offset_airfoil(g.auto_close(local), station.rim_offset)
    placed = [_from_local(loft, end, p) for p in grown]
    curves, nose, te, sharp, section = _finish(loft, end, placed, mode, thickness, up_sign)
    section.station = station.station
    return curves, nose, te, sharp, section


def _guide_samples(stations: Sequence[float], loft: Loft) -> List[float]:
    out: List[float] = []
    for a, b in zip(stations, stations[1:]):
        out.append(a)
        steep = max(loft.sweep(a), loft.sweep(b), loft.sweep(0.5 * (a + b))) > STEEP_SWEEP
        step = GUIDE_STEP_STEEP if steep else GUIDE_STEP
        count = int(math.floor((b - a) / step))
        for k in range(1, count + 1):
            s = a + k * (b - a) / (count + 1)
            out.append(s)
    out.append(stations[-1])
    return out


def _walk_to_wall(
    skin: Skin, s: float, start: Point2, direction: Point2, d: float, inward: bool, limit: float
) -> Point2:
    """The first point from ``start`` along ``direction`` standing ``d`` off the skin."""
    poly = skin.loft.outline(s)

    def good(t: float) -> bool:
        p = (start[0] + direction[0] * t, start[1] + direction[1] * t)
        if _inside(p, poly) != inward:
            return False
        return skin.distance(s, p) >= d

    step = d / 4.0
    t_prev, t = 0.0, 0.0
    while not good(t):
        t_prev, t = t, t + step
        if t > limit:
            raise GeometryError(
                f"No point {d:g} mm off the skin was found at station {s:.1f} mm."
            )
    if t == 0.0:
        return start
    lo, hi = t_prev, t
    for _ in range(18):
        mid = 0.5 * (lo + hi)
        if good(mid):
            hi = mid
        else:
            lo = mid
    return (start[0] + direction[0] * hi, start[1] + direction[1] * hi)


def _interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    i = min(max(bisect.bisect_right(xs, x) - 1, 0), len(xs) - 2)
    w = (x - xs[i]) / (xs[i + 1] - xs[i])
    return ys[i] + w * (ys[i + 1] - ys[i])


def _inner_guides(
    skin: Skin,
    sections: Sequence[OffsetSection],
    offset: float,
) -> Tuple[Guide, Guide, List[float], List[float]]:
    loft = skin.loft
    d = abs(offset)
    inward = offset < 0
    body = [sec for sec in sections if not sec.station.rim]
    body_s = [sec.station.station for sec in body]
    samples = _guide_samples(body_s, loft)

    # Each section's edge points, in its own chord frame, to carry between them.
    def local(sec: OffsetSection, p: Point2) -> Point2:
        return _to_local(loft, sec.station.station, p)

    le_y = [local(sec, sec.le)[1] for sec in body]
    tail_y = [local(sec, sec.te)[1] for sec in body]
    tail_gap = [loft.chord(sec.station.station) - local(sec, sec.te)[0] for sec in body]
    exact_le = {sec.station.station: sec.le for sec in body}
    exact_te = {sec.station.station: sec.te for sec in body}

    le_pts: List[Tuple[float, Point2]] = []
    te_pts: List[Tuple[float, Point2]] = []
    for s in samples:
        if s in exact_le:
            le_pts.append((s, exact_le[s]))
            te_pts.append((s, exact_te[s]))
            continue
        chord = loft.chord(s)
        _, u, _ = loft.axes(s)
        forward = (-u[0], -u[1])
        height = _interp(body_s, le_y, s)
        key = (s, round(height, 9), inward)
        if key not in skin.walked:
            nose = _from_local(loft, s, (0.0, height))
            skin.walked[key] = _walk_to_wall(
                skin, s, nose, u if inward else forward, d, inward, 0.5 * chord
            )
        le_pts.append((s, skin.walked[key]))
        # The tail sits in a wedge too thin to walk into reliably — a hair off
        # its bisector and the walk runs on for millimetres — but how far it
        # moves changes smoothly along the span, so it is carried between the
        # sections instead.
        te_pts.append(
            (s, _from_local(loft, s, (chord - _interp(body_s, tail_gap, s), _interp(body_s, tail_y, s))))
        )

    # A rim adds its own edge points, beyond the end it rounds.
    for sec in sections:
        if sec.station.rim:
            le_pts.append((sec.station.station, sec.le))
            te_pts.append((sec.station.station, sec.te))
    le_pts.sort(key=lambda item: item[0])
    te_pts.sort(key=lambda item: item[0])
    le = Guide([s for s, _ in le_pts], [p for _, p in le_pts])
    te = Guide([s for s, _ in te_pts], [p for _, p in te_pts])
    return le, te, [s for s, _ in le_pts], [s for s, _ in te_pts]


def offset_wing(
    frame: WingFrame,
    loft: Loft,
    offset: float,
    root_end: str,
    tip_end: str,
    mode: str,
    thickness: float,
    progress: Optional[Progress] = None,
    check: bool = False,
    sections_wanted: int = 0,
    spare: int = 0,
) -> OffsetWing:
    """Offset the whole wing by ``offset`` mm (signed: positive grows it).

    ``sections_wanted`` is how many sections a loft already runs through,
    when one does: the wing comes out with that many if the wall allows it.
    Otherwise ``spare`` more sections than the refinement asks for are added.
    """
    if not math.isfinite(offset) or abs(offset) <= g.POINT_TOL:
        raise GeometryError("The wing offset must be a distance other than zero.")
    say = progress or (lambda _message: None)
    d = abs(offset)
    skin = Skin(loft, root_end, tip_end, step=max(d / 6.0, 0.02), frame=frame)
    stations = plan_stations(loft, offset, root_end, tip_end)

    # Which side of the chord frame the ribs call their top, read off the
    # ribs' own trailing-edge corners, so the offset wing's agree with them.
    up_sign = 1.0
    first = loft.sections[0]
    if first.corners is not None:
        upper, lower = (_to_local(loft, first.station, p)[1] for p in first.corners)
        up_sign = 1.0 if upper >= lower else -1.0

    steep_from: Optional[float] = None

    def make(station: Station) -> OffsetSection:
        nonlocal steep_from
        if station.rim:
            curves, nose, te, sharp, section = _rim(loft, station, mode, thickness, up_sign)
            return OffsetSection(station, curves, nose, te, sharp, False, section)
        s = station.station
        exact = loft.sweep(s) > STEEP_SWEEP
        if exact and (steep_from is None or s < steep_from):
            steep_from = s
        loop = offset_outline(skin, s, offset, exact)
        curves, nose, te, sharp, section = _finish(loft, s, loop, mode, thickness, up_sign)
        return OffsetSection(station, curves, nose, te, sharp, exact, section)

    sections: List[OffsetSection] = []
    for number, station in enumerate(stations, start=1):
        say(f"Offsetting section {number} of {len(stations)}...")
        sections.append(make(station))

    say("Working out the edge curves...")
    le, te, le_samples, te_samples = _inner_guides(skin, sections, offset)

    # A loft blends straight between sections; where the offset shape changes
    # faster than that, the blend strays off the wall. Measure halfway between
    # each pair and add a section wherever it does.
    def error(trial: OffsetWing, spans: Sequence[float]) -> float:
        report = measure_wall(skin, trial, stride=2, spans=spans)
        if report.outside:
            return math.inf
        return max(abs(report.thinnest - d), abs(report.thickest - d))

    own: Dict[float, float] = {}

    def strays(sections: List[OffsetSection], le: Guide, te: Guide) -> List[Tuple[float, float]]:
        """For each gap that can still be split, how far its blend strays past
        what the sections either side carry, and the station halfway across."""
        trial = OffsetWing(frame, offset, sections, le, te, thickness=loft.thickness)
        body = [sec for sec in sections if not sec.station.rim]
        for sec in body:
            if sec.station.station not in own:
                own[sec.station.station] = error(trial, [sec.station.station])
        out = []
        for a, b in zip(body, body[1:]):
            gap = b.station.station - a.station.station
            if gap < 2.0 * REFINE_MIN:
                continue
            probes = [a.station.station + 0.5 * gap]
            # Where the edges sweep hard the shape turns quickly, so look at
            # the quarters as well as the middle.
            if a.exact or b.exact:
                probes += [a.station.station + 0.25 * gap, a.station.station + 0.75 * gap]
            # Only a blend worse than the sections either side is the blend's
            # fault; more sections cannot fix what the sections themselves carry.
            floor = max(own[a.station.station], own[b.station.station])
            out.append((error(trial, probes) - floor, a.station.station + 0.5 * gap))
        return out

    def with_more(sections: List[OffsetSection], added: Sequence[Station]):
        sections = sorted(
            sections + [make(station) for station in added], key=lambda sec: sec.station.station
        )
        return (sections,) + _inner_guides(skin, sections, offset)

    planned = (sections, le, te, le_samples, te_samples)
    for _ in range(REFINE_PASSES):
        added = [Station(at) for stray, at in strays(sections, le, te) if stray > REFINE_TOL * d]
        added = added[: max(0, MAX_SECTIONS - len(sections))]
        if not added:
            break
        say(f"Adding {len(added)} section{'s' if len(added) != 1 else ''} where the blend strays...")
        sections, le, te, le_samples, te_samples = with_more(sections, added)

    def worst_first(state, count: int):
        """Sections added one at a time where the blend strays most, up to ``count``.

        The edge guides run through every section's edge point, and a section
        crowded in among others a tenth of a millimetre apart can set them
        swinging in the gap next door: at the tip of one wing a third spare
        section made the wall there 0.06 mm thin. So a section that leaves the
        worst gap worse than it was is taken out again and its gap passed over;
        and if every gap is passed over before the count is reached, the widest
        are split, which is where a section cannot crowd anything.
        """
        gaps = strays(state[0], state[1], state[2])
        passed: set = set()
        while len(state[0]) < count:
            options = [(stray, at) for stray, at in gaps if at not in passed]
            if not options:
                break
            _, at = max(options)
            tried = with_more(state[0], [Station(at)])
            after = strays(tried[0], tried[1], tried[2])
            if max(stray for stray, _ in after) > max(stray for stray, _ in gaps) + 1e-9:
                passed.add(at)
                continue
            state, gaps = tried, after
        while len(state[0]) < count:
            body = [sec.station.station for sec in state[0] if not sec.station.rim]
            a, b = max(zip(body, body[1:]), key=lambda pair: pair[1] - pair[0])
            state = with_more(state[0], [Station(0.5 * (a + b))])
        return state

    if sections_wanted and len(sections) > sections_wanted:
        # A loft already runs through this wing's sections by name, and one
        # that loses a profile breaks. Fewer than the refinement asked for,
        # placed where the blend strays most, will do if the wall they make
        # still holds to KEEP_WALL.
        say(f"Placing {sections_wanted} sections, as the loft has...")
        fewer = worst_first(planned, sections_wanted)
        trial = OffsetWing(frame, offset, fewer[0], fewer[1], fewer[2], thickness=loft.thickness)
        report = measure_wall(skin, trial)
        if len(fewer[0]) == sections_wanted and not report.outside and max(
            abs(report.thinnest - d), abs(report.thickest - d)
        ) <= KEEP_WALL * d:
            sections, le, te, le_samples, te_samples = fewer
    if sections_wanted and len(sections) <= sections_wanted:
        wanted = sections_wanted
    else:
        # The first time, or a count that could not be kept: the loft will be
        # picked afresh, and a few spare keep the next change of offset from
        # needing more.
        wanted = len(sections) + spare
    if len(sections) < wanted:
        say(f"Placing {wanted} sections...")
        sections, le, te, le_samples, te_samples = worst_first(
            (sections, le, te, le_samples, te_samples), wanted
        )

    body = [sec for sec in sections if not sec.station.rim]
    shifts = []
    for sec in (body[0], body[-1]):
        s = sec.station.station
        shifts.append(loft.chord(s) - _to_local(loft, s, sec.te)[0])
    result = OffsetWing(
        frame=frame,
        offset=offset,
        sections=sections,
        le=le,
        te=te,
        le_samples=le_samples,
        te_samples=te_samples,
        steep_from=steep_from,
        te_shift=(shifts[0], shifts[1]),
        thickness=loft.thickness,
    )
    if mode == export.TE_LINE:
        result.te_corners = corner_guides(
            loft.axes, [sec.section for sec in sections], te, te_samples
        )
    if check:
        say("Measuring the wall...")
        result.report = measure_wall(skin, result)
    return result


def surface_guides(
    wing_: OffsetWing, ends: Sequence[OffsetSection], up: Point2
) -> List[Tuple[str, Guide]]:
    """The offset wing's surface guides, through its end profiles alone."""
    inner = wing_.loft()
    first, last = ends
    profiles = [
        Profile(sec.station.station, sec.curves[0][1], sec.le) for sec in (first, last)
    ]
    samples = list(wing_.le_samples) + [sec.station.station for sec in wing_.sections]
    samples = span_samples(first.station.station, last.station.station, samples)
    fractions = [f for f in SURFACE_GUIDES if f >= OFFSET_GUIDES_FROM]
    return wing_surface_guides(inner, profiles, samples, up, fractions)


def measure_wall(
    skin: Skin, wing: OffsetWing, stride: int = 4, spans: Optional[Sequence[float]] = None
) -> WallReport:
    """The wall between the offset wing and the skin, as modelled."""
    inner = wing.loft()
    inward = wing.offset < 0
    if spans is None:
        body = [sec.station.station for sec in wing.sections if not sec.station.rim]
        spans = []
        for a, b in zip(body, body[1:]):
            spans += [a, a + 0.5 * (b - a)]
        spans.append(body[-1])
    thin = (math.inf, (0.0, ""))
    thick = (-math.inf, (0.0, ""))
    outside = 0
    checked = 0
    for s in spans:
        outer = skin.outline(s)
        for i in range(0, inner.count, stride):
            p = inner.point(s, i)
            checked += 1
            if outer is not None and _inside(p, outer) != inward:
                outside += 1
                continue
            wall = skin.distance(s, p)
            where = _describe(i, inner.count)
            if wall < thin[0]:
                thin = (wall, (s, where))
            if wall > thick[0]:
                thick = (wall, (s, where))
    return WallReport(thin[0], thick[0], thin[1], thick[1], outside, checked)


def _describe(i: int, count: int) -> str:
    from .wing import SAMPLES, _T

    if i <= SAMPLES:
        return f"surface A at {100 * _T[SAMPLES - i]:.0f}% chord"
    return f"surface B at {100 * _T[min(i - SAMPLES, SAMPLES)]:.0f}% chord"


def to_3d(frame: WingFrame, s: float, points: Sequence[Point2]) -> List[Vec3]:
    return [frame.to_3d(s, p) for p in points]
