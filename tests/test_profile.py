"""Sections as shapes: NACA sections, camber and thickness, morphs, placement, laws."""

import math
import os

import pytest

from airfoil_converter import export, geometry as g, parser, profile, wing, writer
from airfoil_converter.geometry import GeometryError

HERE = os.path.dirname(__file__)
SD7037_DAT = os.path.join(HERE, "uiuc", "sd7037.dat")
SAMPLE_CSV = os.path.join(os.path.dirname(HERE), "sd7037-il.csv")


def naca_half(t, x, last=-0.1036):
    return 5.0 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x * x + 0.2843 * x ** 3
                      + last * x ** 4)


def naca_camber(m, p, x):
    """The mean line and its slope."""
    if x < p:
        return m / p ** 2 * (2 * p * x - x * x), 2 * m / p ** 2 * (p - x)
    return m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x * x), 2 * m / (1 - p) ** 2 * (p - x)


def naca_surface(m, p, t, x, side):
    yc, rise = naca_camber(m, p, x)
    a = math.atan(rise)
    h = naca_half(t, x)
    return x - side * h * math.sin(a), yc + side * h * math.cos(a)


def bisect(f, lo, hi):
    flo = f(lo)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def distance_to_polyline(p, line):
    best = math.inf
    for a, b in zip(line, line[1:]):
        d = [bi - ai for ai, bi in zip(a, b)]
        span = sum(c * c for c in d)
        t = 0.0 if span == 0 else max(0.0, min(1.0, sum((pi - ai) * c for pi, ai, c in zip(p, a, d)) / span))
        q = [ai + t * c for ai, c in zip(a, d)]
        best = min(best, math.dist(p, q))
    return best


def turned(points, degrees, scale=1.0, shift=(0.0, 0.0)):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return [(shift[0] + scale * (x * c - y * s), shift[1] + scale * (x * s + y * c)) for x, y in points]


# ------------------------------------------------------------------- NACA 4


def test_naca_0012_is_twelve_per_cent_thick_at_thirty_per_cent():
    thickest, where = profile.split_profile(profile.naca4("0012")).max_thickness()
    assert thickest == pytest.approx(0.12, abs=1e-3)
    assert where == pytest.approx(0.30, abs=1e-3)


def test_naca_runs_selig_order_round_a_shared_nose():
    pts = profile.naca4("2412", 41)
    assert len(pts) == 2 * 41 - 1
    assert pts[0] == pts[-1] == (1.0, 0.0)  # A closed trailing edge, repeated as a CSV's is.
    assert pts[40] == (0.0, 0.0)
    assert pts[1][1] > pts[-2][1]  # Upper surface first.
    xs = [x for x, _ in pts[40:]]
    assert xs == sorted(xs)


def test_naca_points_are_cosine_spaced_and_lie_on_the_section():
    pts = profile.naca4("4415", 21)
    for k, x in enumerate(profile.cosine_stations(21)[1:-1], start=1):
        assert pts[20 + k] == pytest.approx(naca_surface(0.04, 0.4, 0.15, x, -1), abs=1e-15)
        assert pts[20 - k] == pytest.approx(naca_surface(0.04, 0.4, 0.15, x, +1), abs=1e-15)


def test_naca_open_trailing_edge_keeps_the_published_gap():
    pts = profile.naca4("0012", closed_te=False)
    assert pts[0][0] == pts[-1][0] == 1.0
    assert pts[0][1] - pts[-1][1] == pytest.approx(2 * naca_half(0.12, 1.0, -0.1015))
    assert pts[0][1] - pts[-1][1] == pytest.approx(0.00252, abs=1e-5)


@pytest.mark.parametrize("digits", ["001", "00a2", "2012", "0000", ""])
def test_naca_rejects_what_is_not_a_four_digit_section(digits):
    with pytest.raises(GeometryError):
        profile.naca4(digits)


def test_naca_takes_its_name_with_the_prefix():
    assert profile.naca4("NACA 0010", 11) == profile.naca4("0010", 11)


def test_a_generated_profile_needs_a_target_chord_to_export():
    data = profile.airfoil_data(profile.naca4("0010", 41), "NACA 0010")
    with pytest.raises(export.InputError, match="Set a target chord"):
        export.build_curves(data, export.ExportSpec(export_camber=False), "n0010")
    in_mm = profile.airfoil_data([(175 * x, 175 * y) for x, y in profile.naca4("0010", 41)],
                                 "rib", in_chords=False)
    (curve,) = export.build_curves(in_mm, export.ExportSpec(export_camber=False), "rib")
    assert max(p[0] for p in curve.points) == pytest.approx(175.0)


def test_a_generated_profile_exports_like_a_csv():
    data = profile.airfoil_data(profile.naca4("0010", 41), "NACA 0010")
    spec = export.ExportSpec(export_camber=False, target_chord="80")
    (curve,) = export.build_curves(data, spec, "n0010")
    assert max(p[0] for p in curve.points) == pytest.approx(80.0)
    assert curve.points[0] == curve.points[-1]


# ------------------------------------------------------ camber and thickness


def test_a_symmetric_section_splits_into_no_camber_and_its_own_thickness():
    split = profile.split_profile(profile.naca4("0012"))
    assert split.nose == pytest.approx((0.0, 0.0), abs=1e-12)
    assert split.chord == pytest.approx(1.0, abs=1e-12)
    assert max(abs(c) for c in split.camber) < 1e-12
    for x, t in zip(split.x, split.thickness):
        assert t == pytest.approx(naca_half(0.12, x), abs=2e-5)


def test_naca_2412_recovers_its_camber():
    """The analytic camber, cut square to the chord as the split cuts it, to 1e-4 chord.

    NACA lay a cambered section's thickness off square to the camber line, and
    the split cuts square to the chord; the reference is the analytic section
    cut the same way, so what is checked is the split, not the convention.
    """
    m, p, t = 0.02, 0.4, 0.12
    split = profile.split_profile(profile.naca4("2412"))
    nx, ny = split.nose
    a = math.radians(split.angle)
    u, v = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))

    def local(q):
        dx, dy = q[0] - nx, q[1] - ny
        return (dx * u[0] + dy * u[1]) / split.chord, (dx * v[0] + dy * v[1]) / split.chord

    # The analytic upper surface turns back at its nose; start beyond that.
    turn = min((k / 20000.0 for k in range(2001)),
               key=lambda s: local(naca_surface(m, p, t, s, +1))[0])
    worst = 0.0
    for x, camber in zip(split.x[1:], split.camber[1:]):
        heights = []
        for side, lo in ((+1, turn), (-1, 0.0)):
            s = bisect(lambda s: local(naca_surface(m, p, t, s, side))[0] - x, lo, 1.0)
            heights.append(local(naca_surface(m, p, t, s, side))[1])
        worst = max(worst, abs(camber - 0.5 * sum(heights)))
    assert worst < 1e-4


@pytest.mark.parametrize("code,m,most", [("2412", 0.02, 1.2e-3), ("4412", 0.04, 2.3e-3)])
def test_the_camber_parts_from_naca_own_by_what_the_docstring_says(code, m, most):
    """Square to the chord rather than to the camber line: about 0.11% and 0.22%."""
    split = profile.split_profile(profile.naca4(code))
    worst = max(abs(c - naca_camber(m, 0.4, x)[0]) for x, c in zip(split.x, split.camber))
    assert most / 2 < worst < most


def test_naca_2412_camber_is_near_naca_own_away_from_the_nose():
    split = profile.split_profile(profile.naca4("2412"))
    for x, c in zip(split.x, split.camber):
        if x >= 0.3:
            assert c == pytest.approx(naca_camber(0.02, 0.4, x)[0], abs=2e-3)
    assert max(split.camber) == pytest.approx(0.02, abs=1e-3)


def test_the_true_nose_lies_between_the_points():
    """SD7037's nose falls between two of its points; the split finds it on the curve."""
    data = parser.parse_dat(SD7037_DAT)
    split = profile.split_profile(data.airfoil)
    assert split.nose not in data.airfoil
    reach = [math.dist(q, (1.0, 0.0)) for q in data.airfoil]
    assert math.dist(split.nose, (1.0, 0.0)) > max(reach)


def test_a_rebuilt_outline_lies_on_the_curve_it_came_from():
    dense = profile.naca4("4412", 801)
    split = profile.split_profile(profile.naca4("4412", 61))
    back = turned(split.outline(), split.angle, split.chord, split.nose)
    assert max(distance_to_polyline(q, dense) for q in back) < 2e-5


def test_the_split_does_not_care_how_the_section_lies():
    pts = profile.naca4("2412", 81)
    plain = profile.split_profile(pts)
    moved = profile.split_profile(turned(pts, 17.0, 150.0, (12.0, -40.0)))
    backwards = profile.split_profile(list(reversed(pts)))
    for other in (moved, backwards):
        assert other.camber == pytest.approx(plain.camber, abs=1e-9)
        assert other.thickness == pytest.approx(plain.thickness, abs=1e-9)
    assert moved.chord == pytest.approx(150.0 * plain.chord)
    assert moved.angle == pytest.approx(17.0 + plain.angle)


def test_a_blunt_trailing_edge_comes_back_as_thick_as_it_was_cut():
    data = parser.parse_csv(SAMPLE_CSV)
    cut = g.blunt_trailing_edge(data.airfoil, 0.4, keep_chord=True)
    split = profile.split_profile(cut)
    assert split.te_thickness * split.chord == pytest.approx(0.4, abs=1e-9)
    assert not split.sharp


def test_an_auto_closed_blunt_rib_keeps_its_trailing_edge(tmp_path):
    """Exported with Auto-close, read back from its curve file: still 0.4 mm blunt."""
    data = parser.parse_csv(SAMPLE_CSV)
    spec = export.ExportSpec(export_camber=False, te_mode=export.TE_CLOSE, te_thickness="0.4",
                             keep_chord=True, plane_mode="YZ", leading_edge=("5", "0", "0"))
    (curve,) = export.build_curves(data, spec, "rib")
    assert curve.closed and curve.points[0] == curve.points[-1]
    path = str(tmp_path / curve.filename)
    writer.write_curve(path, curve.points)
    back, _ = export.load_source(path)
    split = profile.split_profile(back.airfoil)
    assert not split.sharp
    assert split.te_thickness * split.chord == pytest.approx(0.4, abs=1e-6)
    assert split.chord == pytest.approx(175.0, abs=0.05)


def test_a_loop_closed_by_a_long_slanting_segment_is_refused():
    pts = profile.naca4("0012", 41)
    cut = pts[:52] + [pts[0]]  # The lower surface stops at mid-chord and runs straight back.
    with pytest.raises(GeometryError, match="neither a sharp trailing edge nor a blunt"):
        profile.split_profile(cut)


def test_a_sharp_trailing_edge_is_zero_not_minus_zero():
    te = profile.split_profile(profile.naca4("2412")).te_thickness
    assert te == 0.0 and math.copysign(1.0, te) == 1.0


def test_an_outline_starting_at_its_nose_is_refused():
    pts = profile.naca4("2412", 61)[:-1]
    rotated = pts[60:] + pts[:60] + [pts[60]]
    with pytest.raises(GeometryError, match="must start and end at the trailing edge"):
        profile.split_profile(rotated)
    with pytest.raises(GeometryError, match="must start and end at the trailing edge"):
        profile.nose_radius(rotated)


def test_extra_fractions_are_stations_of_their_own():
    split = profile.split_profile(profile.naca4("0012"), stations=11, extra=(0.035, 0.3))
    assert 0.035 in split.x and 0.3 in split.x
    assert split.x == sorted(split.x)
    with pytest.raises(GeometryError, match="between 0 and 1"):
        profile.split_profile(profile.naca4("0012"), extra=(1.5,))


def test_a_split_reports_as_plain_numbers():
    data = profile.split_profile(profile.naca4("2412")).to_dict()
    assert data["max_thickness"] == pytest.approx(0.12, abs=1e-3)
    assert data["max_camber"] == pytest.approx(0.02, abs=1e-3)
    assert len(data["x"]) == len(data["camber"]) == len(data["thickness"]) == profile.STATIONS


def test_a_section_must_run_round_from_its_trailing_edge():
    upper = profile.naca4("0012")[:161]
    with pytest.raises(GeometryError, match="ends stand far apart"):
        profile.split_profile(upper)
    with pytest.raises(GeometryError, match="at least five"):
        profile.split_profile([(1, 0), (0, 0), (1, 0)])


# ---------------------------------------------------------------- morphing

CHORD = 120.0
TE = 0.4


@pytest.fixture(scope="module")
def ends():
    """SD7037 and NACA 0010, each cut to a 0.4 mm trailing edge at a 120 mm chord."""
    sd = g.blunt_trailing_edge(parser.parse_dat(SD7037_DAT).airfoil, TE / CHORD, keep_chord=True)
    n10 = g.blunt_trailing_edge(profile.naca4("0010"), TE / CHORD, keep_chord=True)
    return profile.split_profile(sd), profile.split_profile(n10)


def test_weight_zero_and_one_lie_on_the_two_sections():
    """Rebuilt at either end, the morph lies on each source section, drawn densely."""
    a, b = profile.split_profile(profile.naca4("0012")), profile.split_profile(profile.naca4("4412"))
    for w, code in ((0.0, "0012"), (1.0, "4412")):
        dense = profile.naca4(code, 1601)
        ref = profile.split_profile(profile.naca4(code))
        back = turned(profile.morph(a, b, w).outline(), ref.angle, ref.chord, ref.nose)
        assert max(distance_to_polyline(q, dense) for q in back) < 2e-5


def test_weight_zero_and_one_reproduce_the_inputs(ends):
    a, b = ends
    for w, src in ((0.0, a), (1.0, b)):
        te = src.te_thickness * CHORD
        out = profile.morph(a, b, w, te_thickness=te, chord=CHORD)
        assert out.camber == pytest.approx(src.camber, abs=1e-15)
        assert out.thickness == pytest.approx(src.thickness, abs=1e-15)
    plain = profile.morph(a, b, 0.0)
    assert plain.outline() == a.outline()


@pytest.mark.parametrize("weight", [0.0, 0.2, 0.5, 0.8, 1.0])
def test_the_trailing_edge_is_held_in_millimetres_at_every_weight(ends, weight):
    a, b = ends
    for chord in (40.0, CHORD, 300.0):
        out = profile.morph(a, b, weight, te_thickness=TE, chord=chord)
        loop = out.outline()
        assert math.dist(loop[0], loop[-1]) * chord == pytest.approx(TE, abs=1e-9)
        assert loop[0][0] == loop[-1][0] == 1.0  # Square across the chord.
        # Measured again, the long way: cut by the export's own blunt cut.
        mm = [(x * chord, y * chord) for x, y in loop]
        ends_ = g.cut_trailing_edge(g.auto_close(mm), TE * 0.999)
        assert ends_.station == pytest.approx(chord, abs=0.02)


def test_the_morph_fades_camber_and_blends_the_thickness_without_its_ramp(ends):
    a, b = ends
    half = profile.morph(a, b, 0.5, te_thickness=TE, chord=CHORD)
    for i, x in enumerate(a.x):
        assert half.camber[i] == pytest.approx(0.5 * (a.camber[i] + b.camber[i]), abs=1e-15)
        bare = 0.5 * (a.thickness[i] - x * a.thickness[-1] + b.thickness[i] - x * b.thickness[-1])
        assert half.thickness[i] == pytest.approx(bare + x * TE / CHORD / 2, abs=1e-15)


def test_a_morph_between_points_splits_both_on_the_same_stations():
    out = profile.morph_profiles(profile.naca4("0012"), profile.naca4("4412"), 0.5)
    assert max(out.camber) == pytest.approx(0.02, abs=2e-3)


def test_a_morph_refuses_what_it_cannot_do(ends):
    a, b = ends
    coarse = profile.split_profile(profile.naca4("0010"), stations=21)
    with pytest.raises(GeometryError, match="different stations"):
        profile.morph(a, coarse, 0.5)
    with pytest.raises(GeometryError, match="between 0 and 1"):
        profile.morph(a, b, 1.5)
    with pytest.raises(GeometryError, match="needs the chord"):
        profile.morph(a, b, 0.5, te_thickness=0.4)
    with pytest.raises(GeometryError, match="not negative"):
        profile.morph(a, b, 0.5, te_thickness=-0.4, chord=100.0)
    with pytest.raises(GeometryError, match="more than half as thick"):
        profile.morph(a, b, 0.5, te_thickness=8.0, chord=100.0)
    with pytest.raises(GeometryError, match="morph weight must be a number"):
        profile.morph(a, b, "0.5")


# ------------------------------------------------------- leading-edge radius


def test_naca_0012_nose_radius():
    """1.1019 t^2 of the chord, about 0.0159, to 1%."""
    radius = profile.nose_radius(profile.naca4("0012"))
    assert radius["ratio"] == pytest.approx(1.1019 * 0.12 ** 2, rel=0.01)
    assert radius["ratio"] == pytest.approx(0.0159, rel=0.01)


def test_the_nose_radius_is_in_the_units_of_the_points():
    pts = profile.naca4("0012")
    small = profile.nose_radius(pts)
    big = profile.nose_radius(turned(pts, 30.0, 200.0, (5.0, 5.0)))
    assert big["radius"] == pytest.approx(200.0 * small["radius"], rel=1e-9)
    assert big["ratio"] == pytest.approx(small["ratio"], rel=1e-9)


def test_a_swept_leading_edge_is_rounder_across_the_edge():
    pts = profile.naca4("0010")
    plain = profile.nose_radius(pts)
    swept = profile.nose_radius(pts, sweep=35.0)
    assert swept["radius"] == plain["radius"]
    assert swept["normal_radius"] == pytest.approx(plain["radius"] / math.cos(math.radians(35.0)))
    assert profile.swept_radius(2.0, -60.0) == pytest.approx(4.0)
    with pytest.raises(GeometryError, match="less than 90"):
        profile.swept_radius(1.0, 90.0)


def test_a_nose_with_no_curvature_is_an_error_not_infinity(monkeypatch):
    monkeypatch.setattr(profile._Outline, "curvature", lambda self, t: 0.0)
    with pytest.raises(GeometryError, match="no radius to measure"):
        profile.nose_radius(profile.naca4("0012"))


# ------------------------------------------------------------ placing a section


def test_a_placed_section_flattens_back_to_its_plane_and_profile():
    pts = profile.naca4("0012", 81)
    up = (0.3, 0.8, 0.5)
    placed = profile.place_section(pts, (10.0, -20.0, 30.0), (1.0, 2.0, -0.5), up, chord=150.0,
                                   twist=7.0, pivot=0.25)
    flat = g.flatten_curve(placed.points)
    assert flat.origin == pytest.approx(placed.origin, abs=1e-9)
    assert flat.u == pytest.approx(placed.u, abs=1e-12)
    assert flat.v == pytest.approx(placed.v, abs=1e-12)
    assert flat.chord == pytest.approx(150.0, abs=1e-9)
    for got, want in zip(flat.points, pts):
        assert got == pytest.approx((150.0 * want[0], 150.0 * want[1]), abs=1e-9)
    assert g.dot(placed.u, placed.v) == pytest.approx(0.0, abs=1e-15)


def test_twist_turns_the_section_about_its_pivot_nose_up():
    pts = profile.naca4("0012", 41)
    kw = dict(leading_edge=(0.0, 0.0, 0.0), chord_direction=(1.0, 0.0, 0.0),
              up_direction=(0.0, 0.0, 1.0), chord=100.0, pivot=0.25)
    flat = profile.place_section(pts, **kw)
    twisted = profile.place_section(pts, twist=10.0, **kw)
    assert twisted.pivot == pytest.approx((25.0, 0.0, 0.0))
    assert flat.pivot == twisted.pivot
    tail = twisted.points[0]
    assert tail[2] < 0.0  # Nose up: the tail swings down.
    assert math.dist(twisted.origin, twisted.pivot) == pytest.approx(25.0)
    assert twisted.origin[2] > 0.0


def test_up_need_only_lean_the_right_way():
    pts = profile.naca4("0012", 41)
    leaning = profile.place_section(pts, (0, 0, 0), (1, 0, 0), (0.7, 0.0, 1.0), chord=50.0)
    assert leaning.v == pytest.approx((0.0, 0.0, 1.0))
    with pytest.raises(GeometryError, match="runs along the chord"):
        profile.place_section(pts, (0, 0, 0), (1, 0, 0), (-2.0, 0.0, 0.0), chord=50.0)


def drawn_nose(points3d):
    """The true nose, found by brute force on the curve SolidWorks draws: the
    densely sampled point furthest from the middle of the trailing edge."""
    a, b = points3d[0], points3d[-1]
    te = tuple(0.5 * (p + q) for p, q in zip(a, b))
    dense = wing.as_drawn(points3d, per_span=2000)
    return max(dense, key=lambda p: math.dist(p, te)), te


@pytest.mark.parametrize("chord", [None, 200.0])
def test_a_section_is_placed_by_its_true_nose_and_chord(chord):
    """SD7037's nose lies between its points and off its x axis; it lands on the LE point."""
    pts = parser.parse_dat(SD7037_DAT).airfoil if chord else parser.parse_csv(SAMPLE_CSV).airfoil
    le = (10.0, -5.0, 3.0)
    placed = profile.place_section(pts, le, (0, 0, -1), (0, 1, 0), chord=chord)
    nose, te = drawn_nose(placed.points)
    assert math.dist(nose, le) < 3e-6 * placed.chord  # The sampling's own step.
    assert math.dist(te, le) == pytest.approx(placed.chord, abs=1e-9)
    assert te[0] == pytest.approx(le[0], abs=1e-9) and te[1] == pytest.approx(le[1], abs=1e-9)
    if chord:
        assert placed.chord == chord
    else:
        assert placed.scale == 1.0
        assert placed.chord == pytest.approx(175.0, abs=0.05)


def test_place_section_refuses_bad_input_with_a_reason():
    pts = profile.naca4("0012", 41)
    kw = dict(leading_edge=(0, 0, 0), chord_direction=(1, 0, 0), up_direction=(0, 1, 0))
    with pytest.raises(GeometryError, match="x of point 3"):
        profile.place_section(pts[:2] + [("a", 0.0)] + pts[3:], **kw)
    with pytest.raises(GeometryError, match=r"not an \[x, y\] pair"):
        profile.place_section([(x, y, 0.0) for x, y in pts], **kw)
    with pytest.raises(GeometryError, match="chord must be a number"):
        profile.place_section(pts, chord="50", **kw)
    with pytest.raises(GeometryError, match="twist must be a number"):
        profile.place_section(pts, chord=50.0, twist="3", **kw)
    with pytest.raises(GeometryError, match="leading-edge point must be a list of 3"):
        profile.place_section(pts, (0, 0), (1, 0, 0), (0, 1, 0))
    with pytest.raises(GeometryError, match="number of stations must be a number"):
        profile.split_profile(pts, stations="x")
    with pytest.raises(GeometryError, match="sweep must be a number"):
        profile.nose_radius(pts, sweep="30")


# ------------------------------------------------------- sections along a law


def bend_stations(count=7, chord=(60.0, 30.0), turn=75.0):
    """A winglet's bend: the section plane rotates through the turn, in degrees."""
    out = []
    for k in range(count):
        w = k / (count - 1)
        a = math.radians(turn * w)
        radius = 80.0
        le = (-radius * math.sin(a), radius * (1.0 - math.cos(a)), -15.0 * w)
        out.append(profile.LawStation(
            leading_edge=le, chord_direction=(0.0, 0.0, -1.0),
            up_direction=(math.sin(a), math.cos(a), 0.0),
            chord=chord[0] + (chord[1] - chord[0]) * w, twist=-2.0 * w, pivot=0.25,
            weight=w,
        ))
    return out


@pytest.fixture(scope="module")
def blunt_law():
    sd = parser.parse_dat(SD7037_DAT).airfoil
    return profile.build_law(bend_stations(), "wl", sd, profile.naca4("0010"), te_thickness=TE)


def by_role(build, role):
    return [c for c in build.curves if c.role == role]


def test_every_guide_passes_through_every_section(blunt_law):
    sections = by_role(blunt_law, export.ROLE_SECTION)
    guides = [c for c in blunt_law.curves if c.role in export.WING_EDGE_ROLES]
    assert len(guides) == 1 + 2 * len(wing.SURFACE_GUIDES) + 2
    for guide in guides:
        assert len(guide.points) == len(sections)
        for point, section in zip(guide.points, sections):
            assert distance_to_polyline(point, section.points) < 1e-6
            assert min(math.dist(point, q) for q in section.points) < 1e-6


def test_the_guides_meet_each_section_at_their_chord_fraction(blunt_law):
    upper = {c.feature: c for c in by_role(blunt_law, export.ROLE_WING_SURFACE)}["wl_upper_30"]
    for point, sec in zip(upper.points, blunt_law.sections):
        along = g.dot(g.sub(point, sec.origin), sec.u)
        assert along == pytest.approx(0.30 * sec.chord, abs=1e-9)
        assert g.dot(g.sub(point, sec.origin), sec.v) > 0.0


def test_every_section_holds_its_trailing_edge_and_stands_on_its_plane(blunt_law):
    sections = by_role(blunt_law, export.ROLE_SECTION)
    lines = by_role(blunt_law, export.ROLE_SECTION_TE)
    for placed, sec, te in zip(blunt_law.sections, sections, lines):
        assert math.dist(*te.points) == pytest.approx(TE, abs=1e-9)
        assert te.points == [sec.points[-1], sec.points[0]]
        flat = g.flatten_curve(sec.points)  # Flat, or it would raise.
        assert abs(g.dot(g.cross(flat.u, flat.v), placed.normal)) == pytest.approx(1.0, abs=1e-12)
        assert all(abs(g.dot(g.sub(p, placed.origin), placed.normal)) < 1e-9 for p in sec.points)
        steps = [math.dist(a, b) for a, b in zip(sec.points, sec.points[1:])]
        assert min(steps) >= g.MIN_STEP


def test_the_law_is_named_as_a_wing_is_and_lofts_its_joined_sections(blunt_law):
    names = [c.feature for c in blunt_law.curves]
    assert names[:3] == ["wl_s01", "wl_s01_te", "wl_s02"]
    assert "wl_le" in names and "wl_te_upper" in names and "wl_te_lower" in names
    assert "wl_lower_3p5" in names
    plan = blunt_law.plan
    assert plan.name == "wl_loft"
    assert plan.profiles == tuple(f"wl_s{k:02d}_joined" for k in range(1, 8))
    assert plan.guides[0] == "wl_le" and plan.guides[-2:] == ("wl_te_upper", "wl_te_lower")
    assert blunt_law.joins[0] == (("wl_s01", "wl_s01_te"), "wl_s01_joined")
    assert all(c.filename == c.feature + ".sldcrv" for c in blunt_law.curves)


def test_the_ends_of_a_law_are_its_two_sections(blunt_law):
    first, last = blunt_law.shapes[0], blunt_law.shapes[-1]
    assert first.te_thickness * 60.0 == pytest.approx(TE, abs=1e-12)
    assert max(abs(c) for c in last.camber) < 1e-9  # NACA 0010 carries no camber.
    # NACA 0010's own thickness, with the ramp to a 0.4 mm trailing edge at 30 mm.
    thickest, where = last.max_thickness()
    assert thickest == pytest.approx(0.10 + where * TE / 30.0, abs=2e-3)


def test_a_law_writes_files_the_app_reads_back(blunt_law, tmp_path):
    curve = by_role(blunt_law, export.ROLE_SECTION)[3]
    path = str(tmp_path / curve.filename)
    writer.write_curve(path, curve.points)
    back = parser.parse_curve(path)
    assert max(math.dist(a, b) for a, b in zip(back, curve.points)) < 1e-9


def test_guides_lie_on_the_analytic_sections():
    """NACA 0012 sections on a swept, tapered law, checked against the formula."""
    stations = [
        profile.LawStation((-0.3 * z, 0.0, z), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), c,
                           profile=tuple(profile.naca4("0012")))
        for z, c in ((0.0, 120.0), (40.0, 90.0), (100.0, 60.0))
    ]
    build = profile.build_law(stations, "w")
    guides = {c.feature: c for c in build.curves}
    for st, k in zip(stations, range(3)):
        le = st.leading_edge
        assert guides["w_le"].points[k] == pytest.approx(le, abs=1e-9)
        assert guides["w_te"].points[k] == pytest.approx((le[0] + st.chord, 0.0, le[2]), abs=1e-9)
        for f in wing.SURFACE_GUIDES:
            for side, sign in (("upper", 1.0), ("lower", -1.0)):
                tag = wing.guide_tag(side, f)
                got = guides[f"w_{tag}"].points[k]
                want = (le[0] + f * st.chord, sign * naca_half(0.12, f) * st.chord, le[2])
                assert got == pytest.approx(want, abs=3e-5 * st.chord)


def test_a_sharp_law_closes_its_sections_and_has_one_trailing_edge():
    stations = [
        profile.LawStation((0.0, 0.0, z), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 100.0,
                           profile=tuple(profile.naca4(code, 41)))
        for z, code in ((0.0, "2412"), (50.0, "0012"))
    ]
    build = profile.build_law(stations, "w", fractions=(0.25, 0.5))
    sections = by_role(build, export.ROLE_SECTION)
    assert all(s.closed and s.points[0] == s.points[-1] for s in sections)
    (te,) = by_role(build, export.ROLE_WING_TE)
    assert te.points == [s.points[0] for s in sections]
    assert build.plan.profiles == ("w_s01", "w_s02")
    assert not build.joins
    assert len(by_role(build, export.ROLE_WING_SURFACE)) == 4


def nearly_closed(gap):
    """NACA 0012 with its trailing edge opened by ``gap``, as a fraction of the chord."""
    pts = profile.naca4("0012", 41)
    return tuple([(1.0, 0.5 * gap)] + pts[1:-1] + [(1.0, -0.5 * gap)])


def test_a_trailing_edge_too_thin_to_draw_is_closed():
    """1e-7 mm and 0.005 mm gaps: no line that short is written, nor a joined profile."""
    st = [profile.LawStation((0.0, 0.0, z), (1, 0, 0), (0, 1, 0), 100.0, profile=nearly_closed(gap))
          for z, gap in ((0.0, 1e-9), (20.0, 5e-5), (40.0, 0.0))]
    build = profile.build_law(st, "w")
    assert not by_role(build, export.ROLE_SECTION_TE) and not build.joins
    assert build.plan.profiles == ("w_s01", "w_s02", "w_s03")
    assert all(s.points[0] == s.points[-1] for s in by_role(build, export.ROLE_SECTION))


def test_a_trailing_edge_thick_enough_to_draw_stays_blunt():
    st = [profile.LawStation((0.0, 0.0, z), (1, 0, 0), (0, 1, 0), 100.0, profile=nearly_closed(gap))
          for z, gap in ((0.0, 2e-4), (20.0, 3e-4))]
    build = profile.build_law(st, "w")
    lines = by_role(build, export.ROLE_SECTION_TE)
    assert [math.dist(*c.points) for c in lines] == pytest.approx([0.02, 0.03], abs=1e-9)
    mixed = [st[0], profile.LawStation((0.0, 0.0, 40.0), (1, 0, 0), (0, 1, 0), 100.0,
                                       profile=nearly_closed(0.0))]
    with pytest.raises(GeometryError, match="at least 0.01 mm thick"):
        profile.build_law(mixed, "w")


def test_a_station_given_as_a_dict():
    raw = {"leading_edge": [0, 0, 0], "chord_direction": [1, 0, 0], "up_direction": [0, 1, 0],
           "chord": 80, "twist": 2, "pivot": 0.25, "weight": 0.5}
    st = profile.LawStation.from_dict(raw)
    assert st.weight == 0.5 and st.chord == 80.0 and st.leading_edge == (0.0, 0.0, 0.0)
    with pytest.raises(GeometryError, match="no setting called 'span'"):
        profile.LawStation.from_dict(dict(raw, span=3))
    with pytest.raises(GeometryError, match="needs its chord"):
        profile.LawStation.from_dict({k: v for k, v in raw.items() if k != "chord"})
    with pytest.raises(GeometryError, match="station's chord must be a number"):
        profile.LawStation.from_dict(dict(raw, chord="eighty"))
    with pytest.raises(GeometryError, match="up direction must be a list of 3 numbers, not 2"):
        profile.LawStation.from_dict(dict(raw, up_direction=[0, 1]))
    with pytest.raises(GeometryError, match=r"Point 2 of the station's profile is not an \[x, y\]"):
        profile.LawStation.from_dict(dict(raw, weight=None, profile=[[1, 0], [0.5, 0.1, 0], [0, 0]]))


def test_a_law_refuses_what_it_cannot_build():
    sharp = tuple(profile.naca4("0012", 41))
    blunt = tuple(profile.naca4("0012", 41, closed_te=False))

    def st(z, **kw):
        return profile.LawStation((0.0, 0.0, z), (1, 0, 0), (0, 1, 0), 50.0, **kw)

    with pytest.raises(GeometryError, match="at least two"):
        profile.build_law([st(0.0, profile=sharp)], "w")
    with pytest.raises(GeometryError, match="sharp trailing edge and some in a blunt"):
        profile.build_law([st(0.0, profile=sharp), st(10.0, profile=blunt)], "w")
    with pytest.raises(GeometryError, match="needs the two sections"):
        profile.build_law([st(0.0, weight=0.0), st(10.0, weight=1.0)], "w")
    with pytest.raises(GeometryError, match="both a profile and a morph weight"):
        profile.build_law([st(0.0, profile=sharp, weight=0.0), st(10.0, profile=sharp)], "w")
    with pytest.raises(GeometryError, match="needs a profile or a morph weight"):
        profile.build_law([st(0.0), st(10.0, profile=sharp)], "w")
    with pytest.raises(GeometryError, match="given twice"):
        profile.build_law([st(0.0, profile=sharp), st(10.0, profile=sharp)], "w",
                          fractions=(0.3, 0.5, 0.3))
    with pytest.raises(GeometryError, match="thinner than the 0.01 mm"):
        profile.build_law([st(0.0, profile=sharp), st(10.0, profile=sharp)], "w",
                          te_thickness=0.004)
    with pytest.raises(GeometryError, match="same place"):
        profile.build_law([st(0.0, profile=sharp), st(0.0, profile=sharp)], "w")