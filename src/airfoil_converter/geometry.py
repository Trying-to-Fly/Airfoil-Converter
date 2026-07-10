"""Plane frames, the 2D->3D transform, and trailing-edge handling."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

Point2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

# Points closer than this (in mm) are the same point.
POINT_TOL = 1e-9
# Directions this close to degenerate are unusable as a plane axis.
DIR_TOL = 1e-9

MAIN_PLANES = ("XY", "XZ", "YZ")

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

# The two axis letters spanning each main plane, in their default (chord, up) order.
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
    """The four signed axes the chord may run along inside a main plane."""
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
    """The (chord, up) axis names reproducing the plane's conventional frame."""
    a, b = PLANE_LETTERS[main_plane]
    return f"+{a}", f"+{b}"


def axis_frame(
    chord_axis: str, up_axis: str, main_plane: Optional[str] = None
) -> Tuple[Vec3, Vec3]:
    """Build a frame from two named signed axes, e.g. ('-Z', '+Y')."""
    for name in (chord_axis, up_axis):
        if name not in AXIS_VECTORS:
            raise GeometryError(f"Unknown axis {name!r}. Use one of {sorted(AXIS_VECTORS)}.")

    if chord_axis[-1] == up_axis[-1]:
        raise GeometryError(
            f"Chord and up cannot both run along {chord_axis[-1]} — they must be perpendicular."
        )

    u = AXIS_VECTORS[chord_axis]
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
    chord_axis: Optional[str] = None,
    up_axis: Optional[str] = None,
) -> Tuple[Vec3, Vec3]:
    """Return the (chord, up) unit vectors for the selected plane mode.

    On main planes, ``chord_axis``/``up_axis`` name the signed axes directly
    (e.g. '-Z', '+Y'); omitting them keeps the plane's conventional frame.
    ``rotate180`` spins the airfoil within its plane, swapping nose with tail
    and top with bottom. ``flip`` mirrors 'up' alone.
    """
    if mode in MAIN_PLANES:
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
    return u, v


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
