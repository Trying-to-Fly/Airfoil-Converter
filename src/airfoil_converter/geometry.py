"""Plane frames, the 2D<->3D transform, trailing-edge handling, and offsetting."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Point2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

# Points closer than this (in mm) are the same point.
POINT_TOL = 1e-9
# Directions this close to degenerate are unusable as a plane axis.
DIR_TOL = 1e-9

MAIN_PLANES = ("XY", "XZ", "YZ")

# The plane a loaded curve file already lies on.
LOADED = "loaded"

# A plane given by its normal: the line P1 -> P2, with the plane through P1.
NORMAL_LINE = "normal_line"

# Corner arcs on the outer side of a turn are chorded at this angular step.
ARC_STEP_DEG = 5.0
# A miter reaching further than this many offset distances is bevelled instead.
MITER_LIMIT = 4.0

PLANE_NORMALS: dict[str, Vec3] = {
    "XY": (0.0, 0.0, 1.0),
    "XZ": (0.0, 1.0, 0.0),
    "YZ": (1.0, 0.0, 0.0),
}

MAIN_PLANE_FRAMES: dict[str, Tuple[Vec3, Vec3]] = {
    "XY": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "XZ": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "YZ": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
}

# The two axis letters spanning each main plane, in (chord, up) order.
PLANE_LETTERS: dict[str, Tuple[str, str]] = {"XY": ("X", "Y"), "XZ": ("X", "Z"), "YZ": ("Y", "Z")}

AXIS_VECTORS: dict[str, Vec3] = {
    "+X": (1.0, 0.0, 0.0),
    "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0),
    "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0),
    "-Z": (0.0, 0.0, -1.0),
}

PERPENDICULAR = "Perpendicular"
PARALLEL = "Parallel"


class GeometryError(Exception):
    """The requested plane cannot be built from the given points."""


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Vec3, what: str = "direction") -> Vec3:
    n = length(a)
    if n <= DIR_TOL:
        raise GeometryError(f"Degenerate {what}: the points are coincident.")
    return scale(a, 1.0 / n)


def negate(a: Vec3) -> Vec3:
    return (-a[0], -a[1], -a[2])


def chord_axis_options(main_plane: str) -> Tuple[str, ...]:
    """The four signed axes the chord may run along inside a main plane.

    A chord axis names the trailing-edge -> leading-edge direction, i.e. the way
    the nose points.
    """
    try:
        a, b = PLANE_LETTERS[main_plane]
    except KeyError:
        raise GeometryError(f"Unknown plane {main_plane!r}.") from None
    return (f"+{a}", f"-{a}", f"+{b}", f"-{b}")


def up_axis_options(main_plane: str, chord_axis: str) -> Tuple[str, ...]:
    """The two signed axes 'up' may take, once the chord has claimed the other one."""
    a, b = PLANE_LETTERS[main_plane]
    letter = chord_axis[-1]
    if letter not in (a, b):
        raise GeometryError(f"Chord axis {chord_axis} does not lie in the {main_plane} plane.")
    other = b if letter == a else a
    return (f"+{other}", f"-{other}")


def default_axes(main_plane: str) -> Tuple[str, str]:
    """The (chord, up) axis names reproducing the plane's conventional frame.

    The body extends along +a, so the nose — and hence the chord axis — points -a.
    """
    a, b = PLANE_LETTERS[main_plane]
    return f"-{a}", f"+{b}"


def axis_frame(
    chord_axis: str, up_axis: str, main_plane: Optional[str] = None
) -> Tuple[Vec3, Vec3]:
    """Build a frame from two named signed axes, e.g. ('-Z', '+Y').

    ``chord_axis`` runs trailing edge -> leading edge, so the returned chord
    vector — which runs leading edge -> trailing edge — is its negation.
    """
    for name in (chord_axis, up_axis):
        if name not in AXIS_VECTORS:
            raise GeometryError(f"Unknown axis {name!r}. Use one of {sorted(AXIS_VECTORS)}.")

    if chord_axis[-1] == up_axis[-1]:
        raise GeometryError(
            f"Chord and up cannot both run along {chord_axis[-1]} — they must be perpendicular."
        )

    u = negate(AXIS_VECTORS[chord_axis])
    v = AXIS_VECTORS[up_axis]

    if main_plane is not None:
        normal = PLANE_NORMALS[main_plane]
        for label, name, vec in (("Chord", chord_axis, u), ("Up", up_axis, v)):
            if abs(dot(vec, normal)) > 1e-12:
                raise GeometryError(f"{label} axis {name} is not in the {main_plane} plane.")
    return u, v


def main_plane_frame(plane: str) -> Tuple[Vec3, Vec3]:
    try:
        return MAIN_PLANE_FRAMES[plane]
    except KeyError:
        raise GeometryError(f"Unknown plane {plane!r}.") from None


def three_point_frame(p1: Vec3, p2: Vec3, p3: Vec3) -> Tuple[Vec3, Vec3]:
    """Chord runs P1 -> P2; P3 picks which side of the chord is 'up'."""
    u = normalize(sub(p2, p1), "chord direction P1->P2")
    n = cross(u, sub(p3, p1))
    if length(n) <= DIR_TOL:
        raise GeometryError("P1, P2 and P3 are collinear — they do not define a plane.")
    return u, cross(normalize(n), u)


def perpendicular_frame(p1: Vec3, p2: Vec3, main_plane: str) -> Tuple[Vec3, Vec3]:
    """Plane through P1,P2 perpendicular to a main plane; 'up' leans toward its normal."""
    m = PLANE_NORMALS[main_plane]
    u = normalize(sub(p2, p1), "chord direction P1->P2")
    n = cross(u, m)
    if length(n) <= DIR_TOL:
        raise GeometryError(
            f"P1->P2 is perpendicular to the {main_plane} plane, so no perpendicular "
            f"plane through both points is defined. Use 3 points instead."
        )
    return u, cross(normalize(n), u)


def normal_line_frame(p1: Vec3, p2: Vec3) -> Tuple[Vec3, Vec3]:
    """Plane through P1 whose normal is the line P1 -> P2.

    The line fixes the plane but not the airfoil's directions on it, so the
    frame follows a convention: seen from P2, looking back along the line at
    the plane, the chord runs toward global +X — or toward +Y when the line
    itself runs along X — and 'up' completes that view. Swapping P1 and P2
    turns the plane over, flipping 'up'.
    """
    n = normalize(sub(p2, p1), "normal direction P1->P2")
    for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        chord = sub(axis, scale(n, dot(axis, n)))
        if length(chord) > DIR_TOL:
            u = normalize(chord)
            return u, cross(n, u)
    raise GeometryError("Could not orient a frame on the plane.")  # pragma: no cover


def parallel_frame(p1: Vec3, p2: Vec3, main_plane: str) -> Tuple[Vec3, Vec3]:
    """Plane through P1,P2 parallel to a main plane. Requires (P2-P1) to lie in it."""
    m = PLANE_NORMALS[main_plane]
    d = sub(p2, p1)
    u = normalize(d, "chord direction P1->P2")
    offset = dot(u, m)
    if abs(offset) > 1e-6:
        axis = "XYZ"[m.index(1.0)]
        raise GeometryError(
            f"P1 and P2 do not lie in a plane parallel to {main_plane}: their "
            f"{axis} coordinates must be equal (they differ by {abs(dot(d, m)):.6g})."
        )
    return u, cross(m, u)


def pitch_frame(u: Vec3, v: Vec3, degrees: float) -> Tuple[Vec3, Vec3]:
    """Rotate a frame within its own plane by an angle of attack, in degrees.

    Positive pitches the nose up: the leading edge is the pivot, so the trailing
    edge swings toward -v.
    """
    if degrees == 0.0:
        return u, v
    if not math.isfinite(degrees):
        raise GeometryError("Angle of attack must be a finite number of degrees.")
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return (
        sub(scale(u, c), scale(v, s)),
        add(scale(u, s), scale(v, c)),
    )


def plane_frame(
    mode: str,
    *,
    p1: Vec3 = (0.0, 0.0, 0.0),
    p2: Vec3 = (0.0, 0.0, 0.0),
    p3: Vec3 = (0.0, 0.0, 0.0),
    constraint: str = PERPENDICULAR,
    main_plane: str = "XY",
    flip: bool = False,
    rotate180: bool = False,
    pitch: float = 0.0,
    chord_axis: Optional[str] = None,
    up_axis: Optional[str] = None,
    frame: Optional[Tuple[Vec3, Vec3]] = None,
) -> Tuple[Vec3, Vec3]:
    """Return the (chord, up) unit vectors for the selected plane mode.

    On main planes, ``chord_axis``/``up_axis`` name the signed axes directly
    (e.g. '-Z', '+Y'); the chord axis points trailing edge -> leading edge, and
    omitting both keeps the plane's conventional frame. Mode ``NORMAL_LINE``
    takes the plane through ``p1`` perpendicular to the line ``p1 -> p2``.
    Mode ``LOADED`` takes the plane a curve file was already drawn on, passed
    in as ``frame``.
    ``rotate180`` spins the airfoil within its plane, swapping nose with tail
    and top with bottom. ``flip`` mirrors 'up' alone. ``pitch`` is the angle of
    attack in degrees, applied last, about the leading edge and relative to the
    final 'up'.
    """
    if mode == LOADED:
        if frame is None:
            raise GeometryError("No curve is loaded, so there is no plane to keep it on.")
        u, v = frame
    elif mode in MAIN_PLANES:
        if chord_axis is None and up_axis is None:
            u, v = main_plane_frame(mode)
        else:
            defaults = default_axes(mode)
            u, v = axis_frame(
                chord_axis or defaults[0],
                up_axis or defaults[1],
                main_plane=mode,
            )
    elif mode == "3points":
        u, v = three_point_frame(p1, p2, p3)
    elif mode == NORMAL_LINE:
        u, v = normal_line_frame(p1, p2)
    elif mode == "2points":
        if constraint == PERPENDICULAR:
            u, v = perpendicular_frame(p1, p2, main_plane)
        elif constraint == PARALLEL:
            u, v = parallel_frame(p1, p2, main_plane)
        else:
            raise GeometryError(f"Unknown constraint {constraint!r}.")
    else:
        raise GeometryError(f"Unknown plane mode {mode!r}.")

    if rotate180:
        u, v = negate(u), negate(v)
    if flip:
        v = negate(v)
    return pitch_frame(u, v, pitch)


def to_3d(
    points: Sequence[Point2],
    leading_edge: Vec3,
    u: Vec3,
    v: Vec3,
    scale_factor: float = 1.0,
) -> List[Vec3]:
    """Map 2D airfoil points onto the plane: LE + (x*s)*u + (y*s)*v."""
    out: List[Vec3] = []
    for x, y in points:
        out.append(add(leading_edge, add(scale(u, x * scale_factor), scale(v, y * scale_factor))))
    return out


# ------------------------------------------------- reading a curve back in 2D

# A loaded curve may stray this far from its own plane, as a fraction of its
# chord, before we call it a 3D curve rather than a flat section.
PLANARITY_TOL = 1e-4
# The section's bluntness is judged over this much of the chord at either end.
NOSE_BAND = 0.1


@dataclass
class FlatSection:
    """A 3D curve read back as a 2D section, with the frame it was found on.

    ``to_3d(points, origin, u, v)`` reproduces the curve where it stood.
    """

    points: List[Point2]
    origin: Vec3  # The leading-edge point: the 2D origin.
    u: Vec3
    v: Vec3
    chord: float


def _newell_normal(points: Sequence[Vec3]) -> Vec3:
    """The area normal of a closed polygon: steady even when the points are noisy."""
    n = [0.0, 0.0, 0.0]
    count = len(points)
    for i in range(count):
        (x0, y0, z0), (x1, y1, z1) = points[i], points[(i + 1) % count]
        n[0] += (y0 - y1) * (z0 + z1)
        n[1] += (z0 - z1) * (x0 + x1)
        n[2] += (x0 - x1) * (y0 + y1)
    return (n[0], n[1], n[2])


def _farthest_pair(points: Sequence[Vec3]) -> Tuple[Vec3, Vec3, float]:
    """The two points furthest apart. On an airfoil these are the nose and the tail."""
    best = (points[0], points[1], -1.0)
    for i, a in enumerate(points):
        for b in points[i + 1 :]:
            span = length(sub(b, a))
            if span > best[2]:
                best = (a, b, span)
    return best


def _spread(points: Sequence[Point2], lo: float, hi: float) -> float:
    """How thick the section stands between two chordwise stations."""
    ys = [y for x, y in points if lo <= x <= hi]
    return max(ys) - min(ys) if ys else 0.0


def flatten_curve(points: Sequence[Vec3], tol: float = PLANARITY_TOL) -> FlatSection:
    """Read a 3D curve back as a 2D section lying on its own plane.

    The chord is taken as the longest span across the curve — nose to tail, on
    an airfoil — and the blunter of its two ends is called the leading edge, so
    the section comes back the way a CSV's does: nose at the 2D origin, tail out
    along +x, and the loop running counter-clockwise.
    """
    pts = list(points)
    if len(pts) < 3:
        raise GeometryError("A curve needs at least three points to be read as a section.")

    a, b, span = _farthest_pair(pts)
    if span <= POINT_TOL:
        raise GeometryError("Every point of the curve is in the same place.")

    normal = _newell_normal(pts)
    if length(normal) <= DIR_TOL * span * span:
        raise GeometryError("The curve is a straight line — it does not lie on a plane.")
    normal = normalize(normal, "curve normal")

    strays = max(abs(dot(sub(p, pts[0]), normal)) for p in pts)
    if strays > tol * span:
        raise GeometryError(
            f"The curve is not flat: it strays {strays:.4g} mm off its own plane, more "
            f"than the {tol * span:.4g} mm allowed for a {span:.4g} mm chord. Only a "
            "planar section can be offset."
        )

    # The chord line, laid flat on the plane.
    along = sub(b, a)
    u = normalize(sub(along, scale(normal, dot(along, normal))), "chord direction")
    v = cross(normal, u)

    def project(origin: Vec3, u: Vec3, v: Vec3) -> List[Point2]:
        return [(dot(sub(p, origin), u), dot(sub(p, origin), v)) for p in pts]

    # Whichever end carries more thickness just behind it is the leading edge.
    flat = project(a, u, v)
    if _spread(flat, span - NOSE_BAND * span, span) > _spread(flat, 0.0, NOSE_BAND * span):
        a, u = b, negate(u)
        v = cross(normal, u)
        flat = project(a, u, v)

    # Face the plane from the side that sees the loop run counter-clockwise, so
    # that 'up' means up and the surfaces split the way the CSV's do.
    if signed_area(clean_loop(flat)) < 0.0:
        v = negate(v)
        flat = [(x, -y) for x, y in flat]

    return FlatSection(points=flat, origin=a, u=u, v=v, chord=max(x for x, _ in flat))


def scale_factor(csv_chord: float, target_chord: float | None) -> float:
    if target_chord is None:
        return 1.0
    if csv_chord <= 0:
        raise GeometryError("CSV chord must be positive to rescale.")
    if target_chord <= 0:
        raise GeometryError("Target chord must be greater than zero.")
    return target_chord / csv_chord


def _same_point(a: Point2, b: Point2) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= POINT_TOL


def drop_duplicate(points: Sequence[Point2]) -> List[Point2]:
    """Remove the repeated final trailing-edge point, leaving one open curve."""
    pts = list(points)
    if len(pts) > 1 and _same_point(pts[0], pts[-1]):
        pts.pop()
    return pts


def auto_close(points: Sequence[Point2]) -> List[Point2]:
    """Make the last point exactly equal the first, appending it if the TE has a gap."""
    pts = list(points)
    if not pts:
        return pts
    if _same_point(pts[0], pts[-1]):
        pts[-1] = pts[0]
    else:
        pts.append(pts[0])
    return pts


def clean_open_curve(points: Sequence[Point2]) -> List[Point2]:
    """Drop repeated points from an open curve, keeping both of its ends."""
    pts: List[Point2] = []
    for p in points:
        if not pts or not _same_point(pts[-1], p):
            pts.append(p)
    return pts


# --------------------------------------------------------- blunt trailing edge


def _thickness_at(loop: Sequence[Point2], x: float) -> float:
    """How far the section stands, top to bottom, where a vertical line at ``x`` cuts it."""
    ys: List[float] = []
    n = len(loop)
    for i in range(n):
        (x0, y0), (x1, y1) = loop[i], loop[(i + 1) % n]
        if (x0 <= x < x1) or (x1 <= x < x0):
            ys.append(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return max(ys) - min(ys) if len(ys) >= 2 else 0.0


def _thickest_station(loop: Sequence[Point2]) -> Tuple[float, float]:
    """The chordwise station where the section stands thickest, and how thick it is."""
    return max(((x, _thickness_at(loop, x)) for x, _ in loop), key=lambda s: s[1])


def _cut_at(loop: Sequence[Point2], x: float) -> List[Point2]:
    """Keep the part of a closed loop ahead of a vertical line, cut ends included.

    The walk starts at the aftmost point — behind the line, so the chain always
    opens where the loop first crosses it and closes where it crosses back.
    """
    start = max(range(len(loop)), key=lambda i: loop[i][0])
    pts = list(loop[start:]) + list(loop[:start])
    n = len(pts)

    chains: List[List[Point2]] = []
    chain: List[Point2] = []
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        ahead = x0 <= x
        if ahead:
            chain.append((x0, y0))
        if ahead != (x1 <= x):
            chain.append((x, y0 + (y1 - y0) * (x - x0) / (x1 - x0)))
            if ahead:
                chains.append(chain)
                chain = []
    if chain:
        chains.append(chain)
    if not chains:
        raise GeometryError("The trailing-edge cut misses the section entirely.")
    return max(chains, key=len)


def blunt_trailing_edge(points: Sequence[Point2], thickness: float) -> List[Point2]:
    """Cut a closed airfoil loop back to a trailing edge of the given thickness.

    The cut is the vertical line — a line of constant chordwise station — where
    the section stands exactly ``thickness`` thick, so the curve comes back open,
    its two ends one directly above the other, ready to be closed by a straight
    line in CAD. The chord ends at that line: the section is not stretched to make
    up what the cut took off, so the airfoil simply comes out shorter than the
    chord it was drawn to. A thickness of zero leaves the loop as it stands.
    """
    if not math.isfinite(thickness):
        raise GeometryError("Trailing-edge thickness must be a finite number.")
    if thickness < 0.0:
        raise GeometryError("Trailing-edge thickness must not be negative.")
    if thickness <= POINT_TOL:
        return list(points)

    loop = clean_loop(points)
    if len(loop) < 3:
        raise GeometryError("Not enough points to blunt the trailing edge.")

    # A section that already ends blunter than this has nothing to give up.
    tail = max(x for x, _ in loop)
    ends = [y for x, y in loop if x >= tail - POINT_TOL]
    if max(ends) - min(ends) >= thickness:
        return list(points)

    station, thickest = _thickest_station(loop)
    if thickness >= thickest:
        raise GeometryError(
            f"A {thickness:g} mm trailing edge is thicker than the section itself, which "
            f"stands {thickest:g} mm at its thickest — the cut would take the whole airfoil."
        )

    # Aft of its thickest station a section only thins, down to nothing at the
    # tail, so the one place it stands this thick can be closed in on.
    lo, hi = station, tail
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _thickness_at(loop, mid) > thickness:
            lo = mid
        else:
            hi = mid

    return clean_open_curve(_cut_at(loop, 0.5 * (lo + hi)))


# ------------------------------------------------------------------ offsetting


def _cross2(a: Point2, b: Point2) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _dot2(a: Point2, b: Point2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _direction(a: Point2, b: Point2) -> Point2:
    """Unit vector a -> b. The loop is de-duplicated first, so b != a."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n)


def clean_loop(points: Sequence[Point2]) -> List[Point2]:
    """Drop repeated points, including the one that closes the loop."""
    pts: List[Point2] = []
    for p in points:
        if not pts or not _same_point(pts[-1], p):
            pts.append(p)
    while len(pts) > 2 and _same_point(pts[0], pts[-1]):
        pts.pop()
    return pts


def signed_area(points: Sequence[Point2]) -> float:
    """Positive when the closed loop runs counter-clockwise."""
    n = len(points)
    total = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return 0.5 * total


def _point_segment_distance(p: Point2, a: Point2, b: Point2) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    span = dx * dx + dy * dy
    if span <= 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / span
    t = min(1.0, max(0.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def distance_to_loop(p: Point2, loop: Sequence[Point2]) -> float:
    """Shortest distance from a point to a closed polyline."""
    n = len(loop)
    return min(_point_segment_distance(p, loop[i], loop[(i + 1) % n]) for i in range(n))


def _segment_crossing(
    a: Point2, b: Point2, c: Point2, d: Point2
) -> Optional[Tuple[float, float, Point2]]:
    """Where segments a->b and c->d cross in their interiors: (t, u, point)."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denom = _cross2(r, s)
    if abs(denom) < 1e-15:
        return None
    ac = (c[0] - a[0], c[1] - a[1])
    t = _cross2(ac, s) / denom
    u = _cross2(ac, r) / denom
    eps = 1e-9
    if not (eps < t < 1.0 - eps and eps < u < 1.0 - eps):
        return None
    return t, u, (a[0] + t * r[0], a[1] + t * r[1])


def _corner_offset(
    cur: Point2, e0: Point2, e1: Point2, n0: Point2, n1: Point2, distance: float
) -> List[Point2]:
    """The offset point(s) for the inner side of a turn: the mitred corner."""
    a = (cur[0] + n0[0] * distance, cur[1] + n0[1] * distance)
    b = (cur[0] + n1[0] * distance, cur[1] + n1[1] * distance)
    denom = _cross2(e0, e1)
    if abs(denom) > 1e-12:
        t = _cross2((b[0] - a[0], b[1] - a[1]), e1) / denom
        p = (a[0] + e0[0] * t, a[1] + e0[1] * t)
        if math.hypot(p[0] - cur[0], p[1] - cur[1]) <= MITER_LIMIT * abs(distance):
            return [p]
    # A corner that nearly reverses — a sharp trailing edge, say — throws its
    # miter off towards infinity. Bevel it: the two offset edges cross each other
    # further along anyway, and the trim keeps that crossing instead.
    return [a, b]


def _raw_offset(loop: Sequence[Point2], distance: float, arc_step: float) -> List[Point2]:
    """Offset every vertex of a counter-clockwise loop, self-crossings and all."""
    n = len(loop)
    out: List[Point2] = []
    for i in range(n):
        prev, cur, nxt = loop[i - 1], loop[i], loop[(i + 1) % n]
        e0 = _direction(prev, cur)
        e1 = _direction(cur, nxt)
        # Outward normals, for a counter-clockwise loop.
        n0 = (e0[1], -e0[0])
        n1 = (e1[1], -e1[0])
        turn = _cross2(e0, e1)
        if turn * distance > 0.0:
            # Outer side of the turn: the two offset edges leave a wedge-shaped
            # gap, which a true offset fills with an arc of radius |distance|.
            angle = math.atan2(turn, _dot2(e0, e1))
            steps = max(1, math.ceil(abs(angle) / arc_step))
            for k in range(steps + 1):
                c, s = math.cos(angle * k / steps), math.sin(angle * k / steps)
                rx, ry = n0[0] * c - n0[1] * s, n0[0] * s + n0[1] * c
                out.append((cur[0] + rx * distance, cur[1] + ry * distance))
        else:
            out.extend(_corner_offset(cur, e0, e1, n0, n1, distance))
    return out


def _trim_self_intersections(
    loop: Sequence[Point2], original: Sequence[Point2], distance: float
) -> List[Point2]:
    """Cut away the loops a self-crossing offset ties in itself.

    Every point of a true offset stands at least ``distance`` from the original
    curve. The loops thrown by an offset that overruns itself — over a sharp
    trailing edge, or inside a leading-edge radius tighter than the offset — do
    not, so splitting the polyline at its self-crossings and dropping the points
    that fall short leaves exactly the trimmed offset.
    """
    n = len(loop)
    hits: List[List[Tuple[float, Point2]]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # The first and last segments share a vertex.
            crossing = _segment_crossing(
                loop[i], loop[(i + 1) % n], loop[j], loop[(j + 1) % n]
            )
            if crossing is None:
                continue
            t, u, p = crossing
            hits[i].append((t, p))
            hits[j].append((u, p))

    dense: List[Point2] = []
    for i in range(n):
        dense.append(loop[i])
        dense.extend(p for _, p in sorted(hits[i]))

    tol = max(1e-9, 1e-6 * distance)
    kept = [p for p in dense if distance_to_loop(p, original) >= distance - tol]
    return clean_loop(kept)


def offset_airfoil(
    points: Sequence[Point2], distance: float, arc_step_deg: float = ARC_STEP_DEG
) -> List[Point2]:
    """Offset a closed airfoil loop by a constant distance along its normals.

    Positive grows the section, negative shrinks it — SolidWorks *Offset
    Entities*, not a rescale: the wall between the two curves is ``distance``
    thick everywhere, so the offset section is not the same profile. Returns a
    closed loop starting at the trailing edge, with the first point repeated at
    the end, as the CSV's own loop is.
    """
    if not math.isfinite(distance):
        raise GeometryError("Offset distance must be a finite number.")

    given = list(points)
    loop = clean_loop(given)
    if len(loop) < 3:
        raise GeometryError("Not enough points to offset the airfoil surface.")

    # An offset needs a section to walk around. One surface of an airfoil — an
    # upper skin on its own, say — encloses nothing, and its ends stand a chord
    # apart rather than meeting at the trailing edge. Measure that on the points
    # as given: cleaning the loop drops the very point that closes it.
    ends = math.hypot(given[0][0] - given[-1][0], given[0][1] - given[-1][1])
    span = max(
        max(x for x, _ in loop) - min(x for x, _ in loop),
        max(y for _, y in loop) - min(y for _, y in loop),
    )
    if ends > 0.1 * span:
        raise GeometryError(
            "This curve does not close on itself — its two ends stand far apart, so it "
            "encloses no section to offset. Offsetting needs the whole airfoil loop, "
            "not a single surface of it."
        )
    if abs(distance) <= POINT_TOL:
        return auto_close(loop)

    area = signed_area(loop)
    if abs(area) <= POINT_TOL:
        raise GeometryError("The airfoil surface encloses no area, so it cannot be offset.")

    # Work counter-clockwise so a positive distance always means outward, then
    # hand the loop back the way round it came in.
    flipped = area < 0.0
    ccw = list(reversed(loop)) if flipped else loop

    raw = _raw_offset(ccw, distance, math.radians(arc_step_deg))
    trimmed = _trim_self_intersections(raw, ccw, abs(distance))
    if len(trimmed) < 3:
        raise GeometryError(
            "An inward offset that large eats the whole section — nothing is left to "
            "export. Try a smaller distance."
            if distance < 0
            else "The offset collapses the section — try a smaller distance."
        )

    if flipped:
        trimmed.reverse()
    return auto_close(_start_at_trailing_edge(trimmed))


def _start_at_trailing_edge(loop: Sequence[Point2]) -> List[Point2]:
    """Rotate the loop to begin at its aftmost point, as the CSV's loop does."""
    start = max(range(len(loop)), key=lambda i: loop[i][0])
    return list(loop[start:]) + list(loop[:start])


def leading_edge_index(points: Sequence[Point2]) -> int:
    """The leading edge is the point of least chordwise extent."""
    return min(range(len(points)), key=lambda i: points[i][0])


def split_surfaces(points: Sequence[Point2]) -> Tuple[List[Point2], List[Point2]]:
    """Split a TE -> upper -> LE -> lower -> TE loop into two LE -> TE curves."""
    pts = list(points)
    if len(pts) < 3:
        raise GeometryError("Not enough points to split the airfoil surface.")
    le = leading_edge_index(pts)
    upper = list(reversed(pts[: le + 1]))
    lower = pts[le:]
    if len(upper) < 2 or len(lower) < 2:
        raise GeometryError(
            "Could not split the surface: the leading edge is at an end of the curve."
        )
    return upper, lower
