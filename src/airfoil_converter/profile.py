"""Sections as shapes: made, split into camber and thickness, morphed, and placed.

The rest of the app takes a section as it comes — a plotter CSV, a curve file —
and places it. This works on the shape itself, for a caller laying out a wing
whose sections change along the span: a blended winglet that turns SD7037 into
a symmetric section through its bend, holding a 0.4 mm trailing edge, is the
case it was written for.

A section here is what it is everywhere in the app: 2D points running from
the trailing edge round the nose and back, nose at the origin and chord along
+x once it is placed. Everything takes and returns plain lists and numbers, so
that a tool can hand them straight on.

The outline between the points is the curve SolidWorks draws through them — a
natural cubic spline parametrised by the straight distance from point to point
(:func:`wing.as_drawn`) — not the polyline, so the true nose and its radius
come off the curve rather than off whichever point happens to lie nearest.

Nothing here touches SolidWorks or the window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import export, swloft, wing, writer
from . import geometry as g
from .export import (
    ROLE_SECTION,
    ROLE_SECTION_JOINED,
    ROLE_SECTION_TE,
    ROLE_WING_LE,
    ROLE_WING_SURFACE,
    ROLE_WING_TE,
    ROLE_WING_TE_LOWER,
    ROLE_WING_TE_UPPER,
    Curve,
)
from .geometry import GeometryError, Point2, Vec3
from .parser import AIRFOIL, CHORD_FRACTIONS, UNITS, AirfoilData
from .wing import Spline

# Stations along the chord a section is split on, nose to tail inclusive.
STATIONS = 121
# Points along each surface of a generated NACA section, nose and tail included:
# enough that the curve drawn through them has the nose radius NACA give the
# section to well inside 1%.
NACA_POINTS = 161
# Two of the outline's own points closer than this, as a fraction of the
# outline's length, are one point.
_SAME = 1e-12


def _number(value: Any, what: str) -> float:
    """A finite number, or an error that says which value was not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"The {what} must be a number (got {value!r}).")
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError(f"The {what} must be a finite number (got {value!r}).")
    return number


def _count(value: Any, what: str) -> int:
    """A whole number, given as one."""
    number = _number(value, what)
    if number != int(number):
        raise GeometryError(f"The {what} must be a whole number (got {value!r}).")
    return int(number)


def _row(value: Any, size: int, what: str) -> Tuple[float, ...]:
    """Exactly ``size`` numbers: a point or a direction, never quietly cut short."""
    if isinstance(value, (str, bytes)) or not hasattr(value, "__len__"):
        raise GeometryError(f"The {what} must be a list of {size} numbers (got {value!r}).")
    if len(value) != size:
        raise GeometryError(
            f"The {what} must be a list of {size} numbers, not {len(value)} (got {value!r})."
        )
    return tuple(_number(c, what) for c in value)


def _points2(points: Any, what: str = "section") -> List[Point2]:
    """A 2D section's points as [x, y] pairs. A third column is refused, not dropped."""
    if isinstance(points, (str, bytes)) or not hasattr(points, "__iter__"):
        raise GeometryError(f"The {what} must be a list of [x, y] points.")
    out: List[Point2] = []
    for number, p in enumerate(points, start=1):
        if isinstance(p, (str, bytes)) or not hasattr(p, "__len__") or len(p) != 2:
            raise GeometryError(
                f"Point {number} of the {what} is not an [x, y] pair (got {p!r}). A "
                "profile is 2D, in its own plane: give it as [x, y] points."
            )
        out.append((_number(p[0], f"x of point {number} of the {what}"),
                    _number(p[1], f"y of point {number} of the {what}")))
    return out


def cosine_stations(count: int = STATIONS) -> List[float]:
    """Chord fractions from 0 to 1, close together at the nose and the tail."""
    count = _count(count, "number of stations")
    if count < 3:
        raise GeometryError("A section needs at least three stations along its chord.")
    out = [0.5 * (1.0 - math.cos(math.pi * k / (count - 1))) for k in range(count)]
    out[0], out[-1] = 0.0, 1.0
    return out


def _with_fractions(stations: Sequence[float], extra: Sequence[float]) -> List[float]:
    """The stations, with each extra chord fraction in among them.

    An interior station all but on top of an extra one gives way to it.
    The endpoints always stay at the nose and the trailing edge.
    """
    wanted = []
    for f in extra:
        f = float(f)
        if not 0.0 < f < 1.0:
            raise GeometryError(f"A chord fraction must lie between 0 and 1 (got {f:g}).")
        wanted.append(f)
    grid = [s for s in stations[1:-1] if all(abs(s - f) > 1e-6 for f in wanted)]
    return [stations[0], *sorted(set(grid) | set(wanted)), stations[-1]]


# -- NACA four-digit sections ----------------------------------------------


def naca4(digits: str, points: int = NACA_POINTS, closed_te: bool = True) -> List[Point2]:
    """A NACA four-digit section, chord 1, from the upper trailing edge round the nose.

    The upper and lower surfaces are each ``points`` long, nose and tail
    included, spaced closer at both ends (cosine spacing), and share the nose
    point, so the outline holds ``2 * points - 1``. The thickness is laid off
    square to the camber line, as NACA define it. ``closed_te`` uses the
    modified last coefficient (-0.1036) that closes the trailing edge to a
    point, which is then repeated at the end, as a CSV's loop is; without it the
    original coefficient (-0.1015) leaves the small gap the published sections
    have.
    """
    code = str(digits).strip()
    if code.upper().startswith("NACA"):
        code = code[4:].strip()
    if len(code) != 4 or any(digit not in "0123456789" for digit in code):
        raise GeometryError(f"A NACA four-digit section is named by four digits (got {digits!r}).")
    m = int(code[0]) / 100.0
    p = int(code[1]) / 10.0
    t = int(code[2:]) / 100.0
    if t <= 0.0:
        raise GeometryError(f"NACA {code} has no thickness.")
    if m > 0.0 and p <= 0.0:
        raise GeometryError(
            f"NACA {code} is cambered but puts its highest camber at the nose. The "
            "second digit gives where, in tenths of the chord."
        )
    try:
        count = int(points)
    except (TypeError, ValueError):
        raise GeometryError("The number of points must be a whole number.") from None
    if count < 3:
        raise GeometryError("A NACA section needs at least three points on each surface.")
    last = -0.1036 if closed_te else -0.1015

    upper: List[Point2] = []
    lower: List[Point2] = []
    for x in cosine_stations(count):
        half = 5.0 * t * (
            0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x * x + 0.2843 * x ** 3 + last * x ** 4
        )
        if m == 0.0:
            yc, rise = 0.0, 0.0
        elif x < p:
            yc, rise = m / p ** 2 * (2.0 * p * x - x * x), 2.0 * m / p ** 2 * (p - x)
        else:
            yc = m / (1.0 - p) ** 2 * ((1.0 - 2.0 * p) + 2.0 * p * x - x * x)
            rise = 2.0 * m / (1.0 - p) ** 2 * (p - x)
        angle = math.atan(rise)
        s, c = math.sin(angle), math.cos(angle)
        upper.append((x - half * s, yc + half * c))
        lower.append((x + half * s, yc - half * c))
    upper[0] = lower[0] = (0.0, 0.0)
    if closed_te:
        upper[-1] = lower[-1] = (1.0, 0.0)
    return list(reversed(upper)) + lower[1:]


def airfoil_data(
    points: Sequence[Sequence[float]], name: str = "airfoil", in_chords: bool = True
) -> AirfoilData:
    """A section as the export takes it: its chord is the distance its points span.

    What lets a generated or morphed profile go through ``export.build_curves``
    exactly as a CSV does, target chord and all. A profile in fractions of its
    chord — as ``naca4`` and :meth:`ProfileSplit.outline` give one — is marked so,
    and the export then asks for a target chord, as it does for a UIUC file.
    """
    pts = _points2(points)
    if len(pts) < 3:
        raise GeometryError("A section needs at least three points.")
    chord = g.chord_span(pts)
    if chord <= 0.0:
        raise GeometryError("The section's points span no chord.")
    metadata = {"Name": name}
    if in_chords:
        metadata[UNITS] = CHORD_FRACTIONS
    return AirfoilData(name=name, chord=chord, metadata=metadata, sections={AIRFOIL: pts})


# -- the outline as SolidWorks draws it ------------------------------------


def _turn(a: Point2, b: Point2, c: Point2) -> float:
    """How far, in degrees, a path a -> b -> c turns at b."""
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = c[0] - b[0], c[1] - b[1]
    return abs(math.degrees(math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)))


def _open_blunt_loop(pts: List[Point2], same: float) -> List[Point2]:
    """A loop closed across a blunt trailing edge, opened at its trailing-edge line.

    *Auto-close* shuts a blunt section by running its last point back to its
    first, so the loop ends where it began and looks sharp; the trailing edge
    is then the last segment, standing square across the chord with a corner
    at each end. That segment is taken off, leaving the two corners as the
    ends. A loop that closes along its chord, as a sharp one does, is left as
    it is; one that closes with a long segment that is neither is refused.
    """
    a, b = pts[0], pts[-1]
    if math.hypot(b[0] - a[0], b[1] - a[1]) > same:
        return pts
    far = max(pts, key=lambda p: math.hypot(p[0] - a[0], p[1] - a[1]))
    reach = math.hypot(far[0] - a[0], far[1] - a[1])
    if reach <= 0.0:
        return pts
    cx, cy = (far[0] - a[0]) / reach, (far[1] - a[1]) / reach
    steps = sorted(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:]))
    typical = steps[len(steps) // 2]
    for at_end in (True, False):
        p, q = (pts[-2], pts[-1]) if at_end else (pts[0], pts[1])
        before, after = (pts[-3], pts[1]) if at_end else (pts[-2], pts[2])
        size = math.hypot(q[0] - p[0], q[1] - p[1])
        lean = abs((q[0] - p[0]) * cx + (q[1] - p[1]) * cy) / size
        if lean < 0.5 and _turn(before, p, q) >= 45.0 and _turn(p, q, after) >= 45.0:
            return pts[:-1] if at_end else pts[1:]
    size = math.hypot(pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
    if size > 4.0 * typical:
        raise GeometryError(
            "This loop closes with a long straight segment that does not stand square "
            "across the chord, so it is neither a sharp trailing edge nor a blunt one "
            "closed by its own line. Give the section open at a blunt trailing edge, or "
            "closed at a sharp one."
        )
    return pts


class _Outline:
    """A section's outline: the spline SolidWorks draws through its points."""

    def __init__(self, points: Sequence[Sequence[float]]):
        raw = _points2(points)
        span = max(g.chord_span(raw), max((y for _, y in raw), default=0.0)
                   - min((y for _, y in raw), default=0.0)) if raw else 0.0
        pts: List[Point2] = []
        for p in raw:
            if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > _SAME * max(span, 1.0):
                pts.append(p)
        if len(pts) < 5:
            raise GeometryError(
                "A section needs at least five distinct points to be followed round its nose."
            )
        pts = _open_blunt_loop(pts, _SAME * max(span, 1.0))
        run = [0.0]
        for a, b in zip(pts, pts[1:]):
            run.append(run[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        self.points = pts
        self.run = run
        self._x = Spline(run, [p[0] for p in pts])
        self._y = Spline(run, [p[1] for p in pts])
        a, b = pts[0], pts[-1]
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 0.25 * g.chord_span(pts):
            raise GeometryError(
                "The section's two ends stand far apart. A section runs from its trailing "
                "edge round the nose and back, so both ends are at the trailing edge."
            )
        self.te = (0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]))

    def at(self, t: float) -> Point2:
        return self._x(t), self._y(t)

    def slope(self, t: float) -> Point2:
        return self._x.slope(t), self._y.slope(t)

    def bend(self, t: float) -> Point2:
        return self._x.bend(t), self._y.bend(t)

    def curvature(self, t: float) -> float:
        (dx, dy), (ddx, ddy) = self.slope(t), self.bend(t)
        return abs(dx * ddy - dy * ddx) / (dx * dx + dy * dy) ** 1.5

    def nose(self) -> float:
        """Where the outline stands furthest from its trailing edge.

        That is the nose XFOIL finds too: the point whose tangent stands square
        to the chord line drawn to it from the trailing edge, wherever the
        points fall, and whichever way the section lies.
        """

        def away(t: float) -> float:
            (x, y), (dx, dy) = self.at(t), self.slope(t)
            return (x - self.te[0]) * dx + (y - self.te[1]) * dy

        best: Optional[Tuple[float, float]] = None
        values = [away(t) for t in self.run]
        for i in range(len(self.run) - 1):
            if values[i] > 0.0 >= values[i + 1]:
                lo, hi = self.run[i], self.run[i + 1]
                for _ in range(200):
                    mid = 0.5 * (lo + hi)
                    if away(mid) > 0.0:
                        lo = mid
                    else:
                        hi = mid
                    if hi - lo <= 1e-16 * self.run[-1]:
                        break
                t = 0.5 * (lo + hi)
                x, y = self.at(t)
                reach = math.hypot(x - self.te[0], y - self.te[1])
                if best is None or reach > best[0]:
                    best = (reach, t)
        if best is None:
            raise GeometryError("Could not find the section's nose.")  # pragma: no cover
        t = best[1]
        if t <= self.run[1] or t >= self.run[-2]:
            raise GeometryError(
                "The section's nose is at an end of its outline. A section runs from its "
                "trailing edge round the nose and back."
            )
        return t


class _Side:
    """One surface, walked from the nose to the tail, cut by lines square to the chord."""

    def __init__(self, outline: _Outline, nose: float, origin: Point2, u: Point2, chord: float,
                 forward: bool):
        self.o = outline
        self.origin = origin
        self.u = u
        self.chord = chord
        knots = range(len(outline.run)) if forward else range(len(outline.run) - 1, -1, -1)
        kept = [i for i in knots if (outline.run[i] > nose if forward else outline.run[i] < nose)]
        self.t = [nose] + [outline.run[i] for i in kept]
        self.knot_xi = [0.0] + [self._xi_point(outline.points[i]) for i in kept]
        self.hint = 1

    def _xi_point(self, p: Point2) -> float:
        return ((p[0] - self.origin[0]) * self.u[0] + (p[1] - self.origin[1]) * self.u[1]) / self.chord

    def xi(self, t: float) -> float:
        return self._xi_point(self.o.at(t))

    def _rate(self, t: float) -> float:
        dx, dy = self.o.slope(t)
        return (dx * self.u[0] + dy * self.u[1]) / self.chord

    def reach(self, x: float) -> float:
        """The parameter where this surface stands at chord fraction ``x``."""
        j = max(1, min(self.hint, len(self.t) - 1))
        while j > 1 and self.knot_xi[j - 1] >= x:
            j -= 1
        while j < len(self.t) and self.knot_xi[j] < x:
            j += 1
        if j == len(self.t):
            # Past the last point: a blunt trailing edge that leans a hair
            # across the chord line. The spline runs on, as SolidWorks' does.
            t = self.t[-1]
            for _ in range(60):
                step = (self.xi(t) - x) / self._rate(t)
                t -= step
                if abs(step) <= 1e-15 * self.o.run[-1]:
                    break
            if abs(t - self.t[-1]) > 0.05 * self.o.run[-1]:
                raise GeometryError(
                    "One surface of the section stops short of its trailing edge."
                )
            return t
        self.hint = j
        a, b = self.t[j - 1], self.t[j]
        fa, fb = self.knot_xi[j - 1] - x, self.knot_xi[j] - x
        if fb == 0.0:
            return b
        t = a + (b - a) * (-fa) / (fb - fa) if fb != fa else 0.5 * (a + b)
        for _ in range(100):
            ft = self.xi(t) - x
            if ft == 0.0:
                break
            if (ft > 0.0) == (fb > 0.0):
                b, fb = t, ft
            else:
                a, fa = t, ft
            rate = self._rate(t)
            nxt = t - ft / rate if rate else 0.5 * (a + b)
            if not min(a, b) < nxt < max(a, b):
                nxt = 0.5 * (a + b)
            if abs(nxt - t) <= 1e-15 * self.o.run[-1]:
                t = nxt
                break
            t = nxt
        return t


# -- camber and thickness ---------------------------------------------------


@dataclass
class ProfileSplit:
    """A section as a camber line and a thickness, on stations along its chord.

    Everything is in fractions of the chord, in the section's own chord frame:
    the nose at the origin, the middle of the trailing edge at (1, 0). At each
    station ``x`` the upper surface stands at ``camber + thickness`` and the lower
    at ``camber - thickness`` — thickness is the half-thickness, measured square
    to the chord. ``nose``, ``chord`` and ``angle`` say where that frame stood in
    the points the section was split from, so it can be put back.
    """

    x: List[float]
    camber: List[float]
    thickness: List[float]
    nose: Point2 = (0.0, 0.0)
    chord: float = 1.0
    angle: float = 0.0  # degrees from the source's +x to the chord line, nose to tail

    @property
    def te_thickness(self) -> float:
        """The trailing edge, top to bottom, as a fraction of the chord."""
        return 2.0 * self.thickness[-1] + 0.0

    @property
    def sharp(self) -> bool:
        return self.thickness[-1] <= 1e-12

    def upper(self) -> List[Point2]:
        return [(x, c + t) for x, c, t in zip(self.x, self.camber, self.thickness)]

    def lower(self) -> List[Point2]:
        return [(x, c - t) for x, c, t in zip(self.x, self.camber, self.thickness)]

    def outline(self) -> List[Point2]:
        """The section from the upper trailing edge round the nose, chord 1.

        A sharp trailing edge ends where it began, as a CSV's loop does; a
        blunt one ends at its lower corner, straight below the upper one.
        """
        upper = self.upper()
        lower = self.lower()
        out = list(reversed(upper)) + lower[1:]
        if self.sharp:
            out[-1] = out[0]
        return out

    def max_thickness(self) -> Tuple[float, float]:
        """The section's greatest thickness, top to bottom, and the station it is at."""
        k = max(range(len(self.x)), key=lambda i: self.thickness[i])
        if 0 < k < len(self.x) - 1:
            curve = Spline(self.x, self.thickness)
            lo, hi = self.x[k - 1], self.x[k + 1]
            for _ in range(100):
                mid = 0.5 * (lo + hi)
                if curve.slope(mid) > 0.0:
                    lo = mid
                else:
                    hi = mid
            at = 0.5 * (lo + hi)
            return 2.0 * curve(at), at
        return 2.0 * self.thickness[k], self.x[k]

    def to_dict(self) -> Dict[str, Any]:
        thickest, where = self.max_thickness()
        return {
            "x": list(self.x),
            "camber": list(self.camber),
            "thickness": list(self.thickness),
            "nose": list(self.nose),
            "chord": self.chord,
            "angle": self.angle,
            "te_thickness": self.te_thickness,
            "max_thickness": thickest,
            "max_thickness_at": where,
            "max_camber": max(self.camber, key=abs),
        }


def _frame(outline: _Outline, nose: float) -> Tuple[Point2, Point2, float]:
    origin = outline.at(nose)
    cx, cy = outline.te[0] - origin[0], outline.te[1] - origin[1]
    chord = math.hypot(cx, cy)
    if chord <= 0.0:
        raise GeometryError("The section has no chord.")  # pragma: no cover
    return origin, (cx / chord, cy / chord), chord


def _locate(points: Sequence[Sequence[float]]) -> Tuple[_Outline, float, Point2, Point2, float]:
    """The outline, its true nose, and the chord frame from there to the trailing edge.

    Checks the outline really starts and ends at its trailing edge: one rotated
    to start at its nose would otherwise be read back to front, with the tail
    taken for the nose, since that is then the point furthest from its ends.
    """
    outline = _Outline(points)
    nose = outline.nose()
    origin, u, chord = _frame(outline, nose)
    v = (-u[1], u[0])

    def thickness(x: float) -> float:
        a, b = (outline.at(_Side(outline, nose, origin, u, chord, f).reach(x)) for f in (True, False))
        return abs(((a[0] - b[0]) * v[0] + (a[1] - b[1]) * v[1]) / chord)

    front = max(thickness(x) for x in (0.05, 0.1, 0.15))
    back = max(thickness(x) for x in (0.85, 0.9, 0.95))
    if front <= back:
        raise GeometryError(
            "The outline must start and end at the trailing edge, running round the nose "
            "between. This one stands thicker near its ends than near the point furthest "
            "from them, so it seems to start somewhere else — at its nose, perhaps."
        )
    return outline, nose, origin, u, chord


def split_profile(
    points: Sequence[Sequence[float]],
    stations: int = STATIONS,
    extra: Sequence[float] = (),
) -> ProfileSplit:
    """Split a section into a camber line and a thickness on a common grid.

    The section is followed along the curve SolidWorks would draw through it,
    its true nose found on that curve — the point furthest from the middle of
    the trailing edge, which a sampled outline rarely has a point exactly on —
    and its chord taken from there to the trailing edge. Both surfaces are cut
    square to that chord at ``stations`` chord fractions, cosine-spaced so they
    crowd the nose and the tail, plus any ``extra`` ones asked for; at each,
    the camber is the middle of the cut and the thickness half its length.

    Any section the app reads will do: sharp or blunt, open at a blunt trailing
    edge or closed across it by its line (as *Auto-close* writes it), in chord
    fractions or millimetres, running round either way — but starting and
    ending at the trailing edge. A blunt trailing edge comes back exactly as
    thick as it was cut: its two ends are the last station.

    Rebuilt from the split (:meth:`ProfileSplit.outline`), a sharp section lies
    on the curve it came from, to the accuracy of the stations. A blunt one
    does too, except at its trailing edge, which comes back square to the
    chord rather than square to the source's x: SD7037 cut to 0.4 mm at a
    175 mm chord, whose chord leans 0.1° off its x, has its two corners moved
    about 0.0004 mm.

    The chord runs from the true nose, so the split's frame is generally
    turned a little from the source's own axes (``angle`` says by how much).
    :func:`morph` and :func:`build_law` work in that frame and leave the
    source's angle behind: a section placed from them has its chord line —
    nose to the middle of the trailing edge — along the chord direction given.

    Measuring square to the chord is not how NACA lay a cambered section out:
    they set the thickness off square to the camber line. Near the nose the
    two part: in the split's own frame, the camber found here for a NACA 2412
    sits up to about 0.11% of the chord off NACA's mean line, and for a 4412
    up to about 0.22%. Square to the chord was chosen because it keeps a blunt
    trailing edge exactly the thickness it was cut to, which the morph holds in
    millimetres.
    """
    outline, nose, origin, u, chord = _locate(points)
    v = (-u[1], u[0])
    grid = _with_fractions(cosine_stations(stations), extra)
    sides = [_Side(outline, nose, origin, u, chord, forward) for forward in (True, False)]

    def eta(p: Point2) -> float:
        return ((p[0] - origin[0]) * v[0] + (p[1] - origin[1]) * v[1]) / chord

    heights: List[Tuple[float, float]] = [(0.0, 0.0)]
    for x in grid[1:-1]:
        a, b = (eta(outline.at(side.reach(x))) for side in sides)
        heights.append((a, b))
    # The trailing edge is the two ends themselves: the chord runs to the middle
    # of them, and their distance apart is the trailing edge's thickness, even
    # where the chord leans a little across a cut made square to the source's x.
    first, last = outline.points[0], outline.points[-1]
    gap = 0.5 * math.hypot(last[0] - first[0], last[1] - first[1]) / chord
    heights.append((gap, -gap) if eta(last) >= eta(first) else (-gap, gap))
    if sum(a - b for a, b in heights) < 0.0:
        heights = [(b, a) for a, b in heights]
    camber = [0.5 * (a + b) for a, b in heights]
    thickness = [0.5 * (a - b) for a, b in heights]
    for x, t in zip(grid, thickness):
        if t < -1e-9:
            raise GeometryError(
                f"The section's surfaces cross each other {100.0 * x:.3g}% of the way "
                "along its chord, so it has no thickness to split off there."
            )
    thickness = [t if t > 0.0 else 0.0 for t in thickness]
    angle = math.degrees(math.atan2(u[1], u[0]))
    return ProfileSplit(grid, camber, thickness, origin, chord, angle)


def nose_radius(points: Sequence[Sequence[float]], sweep: float = 0.0) -> Dict[str, Any]:
    """The radius of the section's nose, off the curve SolidWorks draws through it.

    Taken at the true nose (see :func:`split_profile`), in the units of the
    points, and as a fraction of the chord. ``sweep`` is the sweep of the
    leading edge in degrees, with the section cut along the flow: across the
    leading edge itself the nose stands rounder, ``radius / cos(sweep)``, and
    that is the radius a rule for a buildable leading edge is about.
    """
    sweep = _number(sweep, "sweep")
    outline, nose, origin, _, chord = _locate(points)
    bend = outline.curvature(nose)
    if not bend > 1e-9 / chord:
        raise GeometryError(
            "The curve through this section runs straight across its nose, so the nose "
            "has no radius to measure. Give it more points round the nose."
        )
    radius = 1.0 / bend
    return {
        "radius": radius,
        "ratio": radius / chord,
        "normal_radius": swept_radius(radius, sweep),
        "sweep": sweep,
        "nose": list(origin),
        "chord": chord,
    }


def swept_radius(radius: float, sweep: float) -> float:
    """A nose radius seen across a leading edge swept by ``sweep`` degrees.

    A section cut along the flow crosses a swept leading edge at a slant, so
    it is the long way through the same round nose: radius r along the flow is
    r / cos(sweep) square to the edge.
    """
    sweep = _number(sweep, "sweep")
    if abs(sweep) >= 90.0:
        raise GeometryError("Sweep must be a finite number of degrees, less than 90 either way.")
    return _number(radius, "radius") / math.cos(math.radians(sweep))


# -- morphing -----------------------------------------------------------------


def _ramp(split: ProfileSplit) -> List[float]:
    """The thickness with its trailing-edge ramp taken off: zero at the tail."""
    end = split.thickness[-1]
    return [t - x * end for x, t in zip(split.x, split.thickness)]


def morph(
    a: ProfileSplit,
    b: ProfileSplit,
    weight: float,
    te_thickness: Optional[float] = None,
    chord: Optional[float] = None,
) -> ProfileSplit:
    """A section ``weight`` of the way from ``a`` to ``b``.

    The camber fades from one to the other, and the thickness blends with each
    one's trailing-edge ramp taken off — the straight rise from nothing at the
    nose to the trailing edge's half-thickness at the tail — so a trailing edge
    blunted for one chord does not leak into the other. Then a ramp is put back
    that holds the trailing edge at ``te_thickness`` millimetres on a section of
    ``chord`` millimetres, at every weight. Without a ``te_thickness`` the two
    ramps blend with the rest.

    Weight 0 is ``a`` and 1 is ``b``, exactly, when the trailing edge asked for
    is the one that section has. Both must be split on the same stations.

    A trailing edge more than half as thick as the morphed section stands at
    its thickest, ramp taken off, is refused: the ramp would no longer be a
    small correction at the tail but the shape of the section itself.
    """
    if len(a.x) != len(b.x) or any(abs(p - q) > 1e-12 for p, q in zip(a.x, b.x)):
        raise GeometryError(
            "The two sections are split on different stations. Split both with the "
            "same number of stations and the same extra fractions."
        )
    w = _number(weight, "morph weight")
    if not 0.0 <= w <= 1.0:
        raise GeometryError(f"The morph weight must lie between 0 and 1 (got {weight!r}).")
    camber = [(1.0 - w) * p + w * q for p, q in zip(a.camber, b.camber)]
    if te_thickness is None:
        thickness = [(1.0 - w) * p + w * q for p, q in zip(a.thickness, b.thickness)]
    else:
        half = 0.5 * _te_fraction(te_thickness, chord)
        bare = [(1.0 - w) * p + w * q for p, q in zip(_ramp(a), _ramp(b))]
        if half > 0.5 * max(bare):
            raise GeometryError(
                f"A {_number(te_thickness, 'trailing-edge thickness'):g} mm trailing edge is "
                f"more than half as thick as this {_number(chord, 'chord'):g} mm section "
                f"stands at its thickest ({2.0 * max(bare) * chord:.3g} mm). Ask for a "
                "thinner trailing edge."
            )
        thickness = [t + x * half for x, t in zip(a.x, bare)]
        thickness[-1] = half
    return ProfileSplit(list(a.x), camber, thickness)


def _te_fraction(te_thickness: float, chord: Optional[float]) -> float:
    te = _number(te_thickness, "trailing-edge thickness")
    if te < 0.0:
        raise GeometryError("Trailing-edge thickness must be a finite number, not negative.")
    if chord is None:
        raise GeometryError(
            "A trailing edge in millimetres needs the chord, in millimetres, it is held on."
        )
    size = _number(chord, "chord")
    if size <= 0.0:
        raise GeometryError("The chord must be a finite number greater than zero.")
    if te >= size:
        raise GeometryError(f"A {te:g} mm trailing edge is as long as the {size:g} mm chord.")
    return te / size


def morph_profiles(
    a: Sequence[Sequence[float]],
    b: Sequence[Sequence[float]],
    weight: float,
    te_thickness: Optional[float] = None,
    chord: Optional[float] = None,
    stations: int = STATIONS,
) -> ProfileSplit:
    """:func:`morph` of two sections given as points: each split on the same stations first."""
    return morph(
        split_profile(a, stations), split_profile(b, stations), weight, te_thickness, chord
    )


# -- placing a section in space ---------------------------------------------


def _vector(value: Sequence[float], what: str) -> Vec3:
    x, y, z = _row(value, 3, what)
    return (x, y, z)


@dataclass
class PlacedSection:
    """A section standing in space, and the frame it stands on.

    ``to_3d(profile, origin, u, v, scale)`` gives ``points``; ``origin`` is where
    the profile's nose landed after any twist, ``pivot`` the point the twist
    turned it about. ``angle`` is how far, in degrees, the profile's own chord
    line stood turned from its +x: placing it lays that line along the chord
    direction.
    """

    points: List[Vec3]
    origin: Vec3
    u: Vec3
    v: Vec3
    chord: float
    scale: float
    pivot: Vec3
    angle: float = 0.0

    @property
    def normal(self) -> Vec3:
        return g.cross(self.u, self.v)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "points": [list(p) for p in self.points],
            "origin": list(self.origin),
            "u": list(self.u),
            "v": list(self.v),
            "normal": list(self.normal),
            "chord": self.chord,
            "scale": self.scale,
            "pivot": list(self.pivot),
            "angle": self.angle,
        }


def place_section(
    points: Sequence[Sequence[float]],
    leading_edge: Sequence[float],
    chord_direction: Sequence[float],
    up_direction: Sequence[float],
    chord: Optional[float] = None,
    twist: float = 0.0,
    pivot: float = 0.0,
) -> PlacedSection:
    """Stand a 2D profile in space on any plane, in one call.

    The profile is a section as the app reads one, running from the trailing
    edge round the nose and back, with its top towards +y. It is placed by its
    true nose and chord line, found as :func:`split_profile` finds them — the
    point of the curve through it furthest from the middle of its trailing
    edge, and the line from there to that middle — not by its first point or
    its x axis. Given a ``chord`` in millimetres it is scaled so that chord is
    that long, so a profile in fractions of the chord and one in millimetres
    both do; without one it is taken as millimetres.

    The untwisted section has its nose at ``leading_edge``, its chord along
    ``chord_direction`` (nose to tail) and its top towards ``up_direction``,
    which need only lean that way: it is squared up to the chord. ``twist``, in
    degrees, then turns it within its plane the way a positive angle of attack
    does — nose up, tail towards -up — about the point ``pivot`` of the chord
    back from the nose (0.25 for the quarter chord), which stays where it was.
    """
    pts = _points2(points)
    _, _, (ox, oy), (ux, uy), true_chord = _locate(pts)
    if chord is None:
        factor = 1.0
        size = true_chord
    else:
        size = _number(chord, "chord")
        if size <= 0.0:
            raise GeometryError("The chord must be a finite number greater than zero.")
        factor = size / true_chord
    own = [((x - ox) * ux + (y - oy) * uy, (y - oy) * ux - (x - ox) * uy) for x, y in pts]
    placed = _place(own, factor, size, leading_edge, chord_direction, up_direction, twist, pivot)
    placed.angle = math.degrees(math.atan2(uy, ux))
    return placed


def _place(
    pts: Sequence[Point2],
    factor: float,
    size: float,
    leading_edge: Sequence[float],
    chord_direction: Sequence[float],
    up_direction: Sequence[float],
    twist: float,
    pivot: float,
) -> PlacedSection:
    le = _vector(leading_edge, "leading-edge point")
    along = _vector(chord_direction, "chord direction")
    up = _vector(up_direction, "up direction")
    twist, pivot = _number(twist, "twist"), _number(pivot, "pivot")
    u0 = g.normalize(along, "chord direction")
    v0 = g.sub(up, g.scale(u0, g.dot(up, u0)))
    if g.length(v0) <= 1e-9 * max(g.length(up), 1.0):
        raise GeometryError(
            "The up direction runs along the chord, so it does not say which way is up."
        )
    v0 = g.normalize(v0, "up direction")
    u, v = g.pitch_frame(u0, v0, twist)
    turn_about = g.add(le, g.scale(u0, pivot * size))
    origin = g.sub(turn_about, g.scale(u, pivot * size))
    placed = g.to_3d(pts, origin, u, v, factor)
    return PlacedSection(placed, origin, u, v, size, factor, turn_about)


# -- sections and guides along a caller's law -----------------------------------


@dataclass(frozen=True)
class LawStation:
    """One section of a surface laid out by the caller.

    Placed as :func:`place_section` places one. Its shape is its own
    ``profile``, or a ``weight`` of the way from the build's first section to
    its second (see :func:`morph`) — one or the other.
    """

    leading_edge: Vec3
    chord_direction: Vec3
    up_direction: Vec3
    chord: float
    twist: float = 0.0
    pivot: float = 0.0
    profile: Optional[Tuple[Point2, ...]] = None
    weight: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LawStation":
        known = {"leading_edge", "chord_direction", "up_direction", "chord", "twist", "pivot",
                 "profile", "weight"}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise GeometryError(f"A station has no setting called {unknown[0]!r}.")
        missing = [k for k in ("leading_edge", "chord_direction", "up_direction", "chord")
                   if k not in raw]
        if missing:
            raise GeometryError(f"A station needs its {missing[0].replace('_', ' ')}.")
        profile = raw.get("profile")
        weight = raw.get("weight")
        return cls(
            leading_edge=_vector(raw["leading_edge"], "station's leading-edge point"),
            chord_direction=_vector(raw["chord_direction"], "station's chord direction"),
            up_direction=_vector(raw["up_direction"], "station's up direction"),
            chord=_number(raw["chord"], "station's chord"),
            twist=_number(raw.get("twist", 0.0), "station's twist"),
            pivot=_number(raw.get("pivot", 0.0), "station's pivot"),
            profile=None if profile is None else tuple(_points2(profile, "station's profile")),
            weight=None if weight is None else _number(weight, "station's morph weight"),
        )


@dataclass
class LawBuild:
    """What a law export produces: the curves to write, their joins, and the loft."""

    curves: List[Curve]
    joins: List[Tuple[Tuple[str, ...], str]]
    plan: "swloft.LoftPlan"
    sections: List[PlacedSection] = field(default_factory=list)
    shapes: List[ProfileSplit] = field(default_factory=list)


def _thin_keeping(points: Sequence[Vec3], keep: Sequence[int]) -> List[int]:
    """Which points of a section to write: none a hair apart, but every one in ``keep``.

    A spline forced through two all but coincident points ties a knot in itself
    (:data:`geometry.MIN_STEP`); the points a guide runs through are the
    exception, since a guide has to find them on every section.
    """
    wanted = set(keep) | {0, len(points) - 1}
    kept: List[int] = []
    for i, p in enumerate(points):
        if i in wanted:
            while kept and kept[-1] not in wanted and math.dist(points[kept[-1]], p) < g.MIN_STEP:
                kept.pop()
            kept.append(i)
        elif not kept or math.dist(points[kept[-1]], p) >= g.MIN_STEP:
            kept.append(i)
    return kept


def build_law(
    stations: Sequence[LawStation],
    stem: str,
    first: Optional[Sequence[Sequence[float]]] = None,
    second: Optional[Sequence[Sequence[float]]] = None,
    te_thickness: Optional[float] = None,
    fractions: Sequence[float] = wing.SURFACE_GUIDES,
    resolution: int = STATIONS,
    extension: str = ".sldcrv",
    index: int = 1,
) -> LawBuild:
    """Sections at the caller's stations, the edges through them, and guides between.

    Each station is placed as :func:`place_section` places a section; its shape
    is its own profile or a morph weight between ``first`` and ``second``, and
    with ``te_thickness`` every section's trailing edge is held that many
    millimetres thick at its own chord. Every section is rebuilt on the same
    stations along its chord, with a point of its own at each guide fraction,
    so a guide runs through the same point of every section, exactly, and the
    leading edge through every nose. Successive guide and edge points along
    each section must be far enough apart to write as distinct spline points.

    A trailing edge is judged in millimetres at each station's own chord: one
    thinner than :data:`geometry.MIN_STEP` is closed to a point, since a line
    that short is no line SolidWorks will draw, and a ``te_thickness`` above
    nothing but below that is refused. The sections must then all be sharp or
    all blunt.

    The curves are named as a wing's are — ``stem_s01``, ``stem_le``,
    ``stem_upper_30`` — and the plan lofts the sections, joined to their
    trailing-edge lines where those are blunt, held by every guide.
    """
    if extension not in writer.EXTENSIONS:
        raise GeometryError(f"Unsupported extension {extension!r}.")
    stations = list(stations)
    if len(stations) < 2:
        raise GeometryError("A loft needs at least two stations.")
    fractions = sorted(_number(f, "guide fraction") for f in fractions)
    for fraction in fractions:
        if not 0.0 < fraction < 1.0:
            raise GeometryError(f"A chord fraction must lie between 0 and 1 (got {fraction:g}).")
    for f, after in zip(fractions, fractions[1:]):
        if after - f <= 1e-6:
            raise GeometryError(
                f"The guide fraction {f:g} is given twice. Each guide needs a fraction of its own."
            )
        if wing.guide_tag("upper", f) == wing.guide_tag("upper", after):
            raise GeometryError(
                f"Guide fractions {f:g} and {after:g} give the same curve name. "
                "Choose fractions with different values when rounded to tenths of a percent."
            )
    if te_thickness is not None:
        te_thickness = _number(te_thickness, "trailing-edge thickness")
        if 0.0 < te_thickness < g.MIN_STEP:
            raise GeometryError(
                f"A {te_thickness:g} mm trailing edge is thinner than the {g.MIN_STEP:g} mm "
                "SolidWorks needs between two points of a curve, so its line cannot be "
                "drawn. Give 0 for a sharp trailing edge, or at least "
                f"{g.MIN_STEP:g} mm."
            )
    for number, st in enumerate(stations, start=1):
        if st.profile is None and st.weight is None:
            raise GeometryError(f"Station {number} needs a profile or a morph weight.")
        if st.profile is not None and st.weight is not None:
            raise GeometryError(
                f"Station {number} has both a profile and a morph weight. Give one or the other."
            )
        if _number(st.chord, f"chord of station {number}") <= 0.0:
            raise GeometryError(f"Station {number} needs a chord greater than zero.")
    pair: List[ProfileSplit] = []
    if any(st.weight is not None for st in stations):
        if first is None or second is None:
            raise GeometryError(
                "A station given by a morph weight needs the two sections it morphs between."
            )
        pair = [split_profile(p, resolution, fractions) for p in (first, second)]

    shapes: List[ProfileSplit] = []
    for st in stations:
        if st.profile is not None:
            own = split_profile(st.profile, resolution, fractions)
            shape = own if te_thickness is None else morph(own, own, 0.0, te_thickness, st.chord)
        else:
            shape = morph(pair[0], pair[1], st.weight, te_thickness, st.chord)
        if not shape.sharp and shape.te_thickness * st.chord < g.MIN_STEP:
            # Too thin a gap to draw a line across: close it to a point.
            shape = morph(shape, shape, 0.0, 0.0, st.chord)
        shapes.append(shape)
    sharp = {shape.sharp for shape in shapes}
    if len(sharp) > 1:
        raise GeometryError(
            "Some sections end in a sharp trailing edge and some in a blunt one, at least "
            f"{g.MIN_STEP:g} mm thick. Give a trailing-edge thickness to hold them all at."
        )
    sharp_te = sharp.pop()

    count = len(shapes[0].x)
    nose_at = count - 1
    upper_at = {f: count - 1 - shapes[0].x.index(f) for f in fractions}
    lower_at = {f: count - 1 + shapes[0].x.index(f) for f in fractions}
    keep = sorted({0, 2 * nose_at, nose_at, *upper_at.values(), *lower_at.values()})

    curves: List[Curve] = []
    joins: List[Tuple[Tuple[str, ...], str]] = []
    placed: List[PlacedSection] = []

    def named(role: str, points: List[Vec3], closed: bool, section: int = 0,
              tag: str = "") -> Curve:
        feature = export.wing_feature_name(stem, role, index, section, tag)
        return Curve(role=role, points=points, closed=closed, feature=feature,
                     filename=feature + extension)

    profiles = []
    for number, (st, shape) in enumerate(zip(stations, shapes), start=1):
        outline = shape.outline()
        section = _place(outline, st.chord, st.chord, st.leading_edge, st.chord_direction,
                         st.up_direction, st.twist, st.pivot)
        if placed and g.length(g.sub(section.origin, placed[-1].origin)) < g.MIN_STEP:
            raise GeometryError(
                f"Stations {number - 1} and {number} stand in the same place."
            )
        if any(math.dist(section.points[a], section.points[b]) < g.MIN_STEP
               for a, b in zip(keep, keep[1:])):
            raise GeometryError(
                f"At station {number}, successive guide or edge points must be at least "
                f"{g.MIN_STEP:g} mm apart. Use more widely spaced guide fractions."
            )
        placed.append(section)
        kept = _thin_keeping(section.points, keep)
        body = [section.points[i] for i in kept]
        if sharp_te:
            body[-1] = body[0]
            curves.append(named(ROLE_SECTION, body, True, number))
            profiles.append(curves[-1].feature)
        else:
            curves.append(named(ROLE_SECTION, body, False, number))
            curves.append(named(ROLE_SECTION_TE, [body[-1], body[0]], False, number))
            joined = export.wing_feature_name(stem, ROLE_SECTION_JOINED, index, number)
            joins.append(((curves[-2].feature, curves[-1].feature), joined))
            profiles.append(joined)

    guides = [named(ROLE_WING_LE, [s.points[nose_at] for s in placed], False)]
    for side, where in (("upper", upper_at), ("lower", lower_at)):
        for f in fractions:
            guides.append(named(ROLE_WING_SURFACE, [s.points[where[f]] for s in placed], False,
                                tag=wing.guide_tag(side, f)))
    if sharp_te:
        guides.append(named(ROLE_WING_TE, [s.points[0] for s in placed], False))
    else:
        guides.append(named(ROLE_WING_TE_UPPER, [s.points[0] for s in placed], False))
        guides.append(named(ROLE_WING_TE_LOWER, [s.points[-1] for s in placed], False))
    curves.extend(guides)

    plan = swloft.LoftPlan(
        name=export.wing_base(stem, index) + swloft.LOFT_SUFFIX,
        profiles=tuple(profiles),
        guides=tuple(c.feature for c in guides),
    )
    return LawBuild(curves=curves, joins=joins, plan=plan, sections=placed, shapes=shapes)
