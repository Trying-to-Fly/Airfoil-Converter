import math

import pytest

from airfoil_converter import geometry as g
from airfoil_converter.geometry import GeometryError

LOOP = [(100.0, 0.0), (50.0, 5.0), (0.0, 0.0), (50.0, -5.0), (100.0, 0.0)]
OPEN_TE = [(100.0, 1.0), (50.0, 5.0), (0.0, 0.0), (50.0, -5.0), (100.0, -1.0)]


def approx(vec):
    return pytest.approx(vec, abs=1e-12)


# ---------------------------------------------------------------- main planes


@pytest.mark.parametrize(
    "plane,u,v",
    [
        ("XY", (1, 0, 0), (0, 1, 0)),
        ("XZ", (1, 0, 0), (0, 0, 1)),
        ("YZ", (0, 1, 0), (0, 0, 1)),
    ],
)
def test_main_plane_frames(plane, u, v):
    assert g.plane_frame(plane) == (approx(u), approx(v))


def test_flip_negates_up_only():
    u, v = g.plane_frame("XY", flip=True)
    assert u == approx((1, 0, 0))
    assert v == approx((0, -1, 0))


def test_unknown_mode():
    with pytest.raises(GeometryError, match="Unknown plane mode"):
        g.plane_frame("weird")


# ------------------------------------------------- explicit chord / up axes


def test_default_axes_reproduce_the_conventional_frames():
    for plane in g.MAIN_PLANES:
        chord, up = g.default_axes(plane)
        assert g.plane_frame(plane, chord_axis=chord, up_axis=up) == g.plane_frame(plane)


def test_chord_axis_options_are_the_two_in_plane_axes():
    assert g.chord_axis_options("YZ") == ("+Y", "-Y", "+Z", "-Z")
    assert g.chord_axis_options("XZ") == ("+X", "-X", "+Z", "-Z")
    assert g.chord_axis_options("XY") == ("+X", "-X", "+Y", "-Y")


def test_up_axis_options_exclude_the_chord_axis():
    assert g.up_axis_options("YZ", "-Z") == ("+Y", "-Y")
    assert g.up_axis_options("YZ", "+Y") == ("+Z", "-Z")


def test_up_axis_options_reject_an_out_of_plane_chord():
    with pytest.raises(GeometryError, match="does not lie in the YZ plane"):
        g.up_axis_options("YZ", "+X")


def test_leading_edge_can_be_aimed_along_plus_z_on_yz():
    """The reported case: on YZ the nose pointed -Y; aiming the chord at +Z points it +Z."""
    u, v = g.plane_frame("YZ", chord_axis="+Z", up_axis="+Y")
    assert u == approx((0, 0, -1))
    assert v == approx((0, 1, 0))
    # The trailing edge lands at -Z, so the nose at the origin points +Z.
    points = g.to_3d([(0, 0), (175, 0)], (0, 0, 0), u, v)
    assert points[0] == approx((0, 0, 0))
    assert points[1] == approx((0, 0, -175))


def test_chord_axis_names_the_trailing_to_leading_direction():
    """The body extends opposite the named axis, so the nose points along it."""
    for plane in g.MAIN_PLANES:
        for chord in g.chord_axis_options(plane):
            up = g.up_axis_options(plane, chord)[0]
            u, _ = g.axis_frame(chord, up, main_plane=plane)
            assert u == approx(g.negate(g.AXIS_VECTORS[chord]))


def test_axis_frame_rejects_a_repeated_axis():
    with pytest.raises(GeometryError, match="both run along Y"):
        g.axis_frame("+Y", "-Y")


def test_axis_frame_rejects_an_unknown_axis():
    with pytest.raises(GeometryError, match="Unknown axis"):
        g.axis_frame("+Q", "+Y")


def test_axis_frame_rejects_axes_outside_the_named_plane():
    with pytest.raises(GeometryError, match=r"Chord axis \+X is not in the YZ plane"):
        g.axis_frame("+X", "+Y", main_plane="YZ")
    with pytest.raises(GeometryError, match=r"Up axis \+X is not in the YZ plane"):
        g.axis_frame("+Y", "+X", main_plane="YZ")


def test_axis_frame_is_always_orthonormal():
    for plane in g.MAIN_PLANES:
        for chord in g.chord_axis_options(plane):
            for up in g.up_axis_options(plane, chord):
                u, v = g.axis_frame(chord, up, main_plane=plane)
                assert g.length(u) == pytest.approx(1.0)
                assert g.length(v) == pytest.approx(1.0)
                assert g.dot(u, v) == pytest.approx(0.0)


# ------------------------------------------------------- 180-degree rotation


def test_rotate180_negates_both_axes():
    u, v = g.plane_frame("XY", rotate180=True)
    assert u == approx((-1, 0, 0))
    assert v == approx((0, -1, 0))


def test_rotate180_swaps_nose_and_tail_about_the_leading_edge():
    """Nose stays put; the body and the camber both swing to the other side."""
    u, v = g.plane_frame("XY")
    ru, rv = g.plane_frame("XY", rotate180=True)
    section = [(0.0, 0.0), (100.0, 0.0), (50.0, 10.0)]
    normal = g.to_3d(section, (0, 0, 0), u, v)
    rotated = g.to_3d(section, (0, 0, 0), ru, rv)
    assert normal[0] == approx((0, 0, 0)) and rotated[0] == approx((0, 0, 0))
    assert normal[1] == approx((100, 0, 0)) and rotated[1] == approx((-100, 0, 0))
    assert normal[2] == approx((50, 10, 0)) and rotated[2] == approx((-50, -10, 0))


def test_rotate180_preserves_shape():
    """A 180-degree rotation is rigid: every pairwise distance survives it."""
    section = [(0.0, 0.0), (175.0, 0.0), (60.0, 12.0), (40.0, -4.0)]
    u, v = g.plane_frame("XZ")
    ru, rv = g.plane_frame("XZ", rotate180=True)
    plain = g.to_3d(section, (3, 4, 5), u, v)
    spun = g.to_3d(section, (3, 4, 5), ru, rv)
    for i in range(len(section)):
        for j in range(i + 1, len(section)):
            assert g.length(g.sub(plain[i], plain[j])) == pytest.approx(
                g.length(g.sub(spun[i], spun[j]))
            )


def test_rotate180_applied_twice_is_identity():
    u, v = g.plane_frame("YZ")
    ru, rv = g.plane_frame("YZ", rotate180=True)
    assert g.negate(ru) == approx(u)
    assert g.negate(rv) == approx(v)


def test_rotate180_and_flip_compose_to_a_chord_only_mirror():
    """Rotating then flipping up leaves 'up' alone and reverses only the chord."""
    u, v = g.plane_frame("XY", rotate180=True, flip=True)
    assert u == approx((-1, 0, 0))
    assert v == approx((0, 1, 0))


def test_rotate180_works_on_custom_planes():
    u, v = g.plane_frame("3points", p1=(0, 0, 0), p2=(1, 0, 0), p3=(0, 1, 0), rotate180=True)
    assert u == approx((-1, 0, 0))
    assert v == approx((0, -1, 0))


def test_rotate180_combines_with_explicit_axes():
    u, v = g.plane_frame("YZ", chord_axis="+Z", up_axis="+Y", rotate180=True)
    assert u == approx((0, 0, 1))
    assert v == approx((0, -1, 0))


# --------------------------------------------------------- angle of attack


def test_zero_pitch_changes_nothing():
    assert g.plane_frame("XY", pitch=0.0) == g.plane_frame("XY")


def test_positive_pitch_drops_the_trailing_edge():
    """The leading edge is the pivot, so a nose-up section puts its tail below."""
    u, v = g.plane_frame("XY", pitch=30.0)
    nose, tail = g.to_3d([(0, 0), (100, 0)], (0, 0, 0), u, v)
    assert nose == approx((0, 0, 0))
    assert tail == approx((100 * math.cos(math.radians(30)), -50.0, 0))


def test_negative_pitch_raises_the_trailing_edge():
    u, v = g.plane_frame("XY", pitch=-30.0)
    _, tail = g.to_3d([(0, 0), (100, 0)], (0, 0, 0), u, v)
    assert tail[1] == pytest.approx(50.0)


def test_pitch_is_measured_against_the_chosen_up_axis():
    """On YZ with the nose at +Z and up at +Y, a nose-up tail sinks in -Y."""
    u, v = g.plane_frame("YZ", chord_axis="+Z", up_axis="+Y", pitch=10.0)
    _, tail = g.to_3d([(0, 0), (100, 0)], (0, 0, 0), u, v)
    assert tail[2] < 0  # still behind the nose
    assert tail[1] == pytest.approx(-100 * math.sin(math.radians(10)))


def test_pitch_keeps_the_frame_orthonormal():
    u, v = g.plane_frame("3points", p1=(1, 2, 3), p2=(4, -1, 2), p3=(0, 5, 7), pitch=17.5)
    assert g.length(u) == pytest.approx(1.0)
    assert g.length(v) == pytest.approx(1.0)
    assert g.dot(u, v) == pytest.approx(0.0, abs=1e-12)


def test_pitch_stays_in_the_plane():
    """Rotating by the angle of attack must not tilt the section out of its plane."""
    normal = g.cross(*g.plane_frame("3points", p1=(1, 2, 3), p2=(4, -1, 2), p3=(0, 5, 7)))
    u, v = g.plane_frame("3points", p1=(1, 2, 3), p2=(4, -1, 2), p3=(0, 5, 7), pitch=42.0)
    assert g.dot(u, normal) == pytest.approx(0.0, abs=1e-12)
    assert g.dot(v, normal) == pytest.approx(0.0, abs=1e-12)


def test_pitch_preserves_shape():
    section = [(0.0, 0.0), (175.0, 0.0), (60.0, 12.0), (40.0, -4.0)]
    plain = g.to_3d(section, (3, 4, 5), *g.plane_frame("XZ"))
    pitched = g.to_3d(section, (3, 4, 5), *g.plane_frame("XZ", pitch=8.0))
    for i in range(len(section)):
        for j in range(i + 1, len(section)):
            assert g.length(g.sub(plain[i], plain[j])) == pytest.approx(
                g.length(g.sub(pitched[i], pitched[j]))
            )


def test_pitch_accumulates_with_rotate180():
    """rotate180 spins first, so the pitch still measures against the final up."""
    u, v = g.plane_frame("XY", rotate180=True, pitch=90.0)
    assert u == approx((0, 1, 0))
    assert v == approx((-1, 0, 0))


def test_pitch_rejects_a_non_finite_angle():
    with pytest.raises(GeometryError, match="finite number of degrees"):
        g.plane_frame("XY", pitch=float("inf"))


# ------------------------------------------------------------------ 3 points


def test_three_point_frame_matches_xy():
    u, v = g.plane_frame("3points", p1=(0, 0, 0), p2=(1, 0, 0), p3=(0, 1, 0))
    assert u == approx((1, 0, 0))
    assert v == approx((0, 1, 0))


def test_three_point_p3_picks_the_up_side():
    _, v = g.plane_frame("3points", p1=(0, 0, 0), p2=(1, 0, 0), p3=(0, -1, 0))
    assert v == approx((0, -1, 0))


def test_three_point_chord_runs_p1_to_p2():
    u, _ = g.plane_frame("3points", p1=(2, 0, 0), p2=(0, 0, 0), p3=(0, 1, 0))
    assert u == approx((-1, 0, 0))


def test_three_point_frame_is_orthonormal():
    u, v = g.plane_frame("3points", p1=(1, 2, 3), p2=(4, -1, 2), p3=(0, 5, 7))
    assert g.length(u) == pytest.approx(1.0)
    assert g.length(v) == pytest.approx(1.0)
    assert g.dot(u, v) == pytest.approx(0.0, abs=1e-12)


def test_collinear_three_points():
    with pytest.raises(GeometryError, match="collinear"):
        g.plane_frame("3points", p1=(0, 0, 0), p2=(1, 0, 0), p3=(2, 0, 0))


def test_coincident_chord_points():
    with pytest.raises(GeometryError, match="coincident"):
        g.plane_frame("3points", p1=(1, 1, 1), p2=(1, 1, 1), p3=(0, 1, 0))


# -------------------------------------------------------- 2 points + constraint


def test_perpendicular_to_xy_gives_xz_frame():
    u, v = g.plane_frame(
        "2points", p1=(0, 0, 0), p2=(1, 0, 0), constraint=g.PERPENDICULAR, main_plane="XY"
    )
    assert u == approx((1, 0, 0))
    assert v == approx((0, 0, 1))


def test_perpendicular_up_leans_toward_the_main_plane_normal():
    """A 45-degree chord in XZ: 'up' stays in-plane but tilts toward +Z."""
    u, v = g.plane_frame(
        "2points", p1=(0, 0, 0), p2=(1, 0, 1), constraint=g.PERPENDICULAR, main_plane="XY"
    )
    assert u == approx((1 / math.sqrt(2), 0, 1 / math.sqrt(2)))
    assert g.dot(v, (0, 0, 1)) > 0
    assert g.dot(u, v) == pytest.approx(0.0, abs=1e-12)


def test_perpendicular_fails_when_chord_is_along_the_normal():
    with pytest.raises(GeometryError, match="perpendicular to the XY plane"):
        g.plane_frame(
            "2points", p1=(0, 0, 0), p2=(0, 0, 5), constraint=g.PERPENDICULAR, main_plane="XY"
        )


def test_parallel_to_xy_at_an_offset():
    u, v = g.plane_frame(
        "2points", p1=(0, 0, 5), p2=(1, 0, 5), constraint=g.PARALLEL, main_plane="XY"
    )
    assert u == approx((1, 0, 0))
    assert v == approx((0, 1, 0))


def test_parallel_requires_points_in_the_plane():
    with pytest.raises(GeometryError, match="Z coordinates must be equal"):
        g.plane_frame(
            "2points", p1=(0, 0, 0), p2=(1, 0, 3), constraint=g.PARALLEL, main_plane="XY"
        )


def test_parallel_to_yz_names_the_x_axis():
    with pytest.raises(GeometryError, match="X coordinates must be equal"):
        g.plane_frame(
            "2points", p1=(0, 0, 0), p2=(2, 1, 0), constraint=g.PARALLEL, main_plane="YZ"
        )


def test_unknown_constraint():
    with pytest.raises(GeometryError, match="Unknown constraint"):
        g.plane_frame("2points", p1=(0, 0, 0), p2=(1, 0, 0), constraint="Tangent")


# ------------------------------------------------------------- normal line


def test_normal_line_along_z_gives_the_xy_frame():
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, 0, 5))
    assert u == approx((1, 0, 0))
    assert v == approx((0, 1, 0))


def test_normal_line_along_x_gives_the_yz_frame():
    """The line runs along X, so the chord falls back to the +Y convention."""
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(3, 0, 0))
    assert u == approx((0, 1, 0))
    assert v == approx((0, 0, 1))


def test_normal_line_along_minus_y_gives_the_xz_frame():
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, -2, 0))
    assert u == approx((1, 0, 0))
    assert v == approx((0, 0, 1))


def test_swapping_the_line_ends_flips_up():
    """Reversing the line turns the plane over: same chord, opposite 'up'."""
    _, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, 0, 5))
    _, v_swapped = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 5), p2=(0, 0, 0))
    assert v_swapped == approx(g.negate(v))


def test_normal_line_frame_is_perpendicular_to_the_line():
    n = g.normalize(g.sub((4.0, -1.0, 2.0), (1.0, 2.0, 3.0)))
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(1, 2, 3), p2=(4, -1, 2))
    assert g.dot(u, n) == pytest.approx(0.0, abs=1e-12)
    assert g.dot(v, n) == pytest.approx(0.0, abs=1e-12)


def test_normal_line_frame_is_orthonormal():
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(1, 2, 3), p2=(4, -1, 2))
    assert g.length(u) == pytest.approx(1.0)
    assert g.length(v) == pytest.approx(1.0)
    assert g.dot(u, v) == pytest.approx(0.0, abs=1e-12)


def test_normal_line_only_the_direction_matters():
    """Moving the line without turning it keeps the same frame."""
    frame = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(1, 2, 3))
    moved = g.plane_frame(g.NORMAL_LINE, p1=(7, -4, 9), p2=(8, -2, 12))
    assert moved[0] == approx(frame[0])
    assert moved[1] == approx(frame[1])


def test_normal_line_chord_faces_plus_x_seen_from_p2():
    """The convention: chord toward +X, and (u, v, line) right-handed."""
    n = g.normalize(g.sub((4.0, -1.0, 2.0), (1.0, 2.0, 3.0)))
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(1, 2, 3), p2=(4, -1, 2))
    assert g.dot(u, (1.0, 0.0, 0.0)) > 0
    assert g.cross(u, v) == approx(n)


def test_normal_line_still_flips_rotates_and_pitches():
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, 0, 5), flip=True)
    assert (u, v) == (approx((1, 0, 0)), approx((0, -1, 0)))
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, 0, 5), rotate180=True)
    assert (u, v) == (approx((-1, 0, 0)), approx((0, -1, 0)))
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, 0, 5), pitch=90.0)
    assert (u, v) == (approx((0, -1, 0)), approx((1, 0, 0)))


def test_normal_line_rejects_coincident_points():
    with pytest.raises(GeometryError, match="coincident"):
        g.plane_frame(g.NORMAL_LINE, p1=(1, 1, 1), p2=(1, 1, 1))


# ------------------------------------------------------------------ transform


def test_to_3d_places_the_origin_at_the_leading_edge():
    u, v = g.plane_frame("XZ")
    points = g.to_3d([(0, 0), (175, 0), (50, 10)], (1, 2, 3), u, v)
    assert points[0] == approx((1, 2, 3))
    assert points[1] == approx((176, 2, 3))
    assert points[2] == approx((51, 2, 13))


def test_to_3d_applies_the_scale_factor():
    u, v = g.plane_frame("XY")
    points = g.to_3d([(100, 10)], (0, 0, 0), u, v, scale_factor=0.5)
    assert points[0] == approx((50, 5, 0))


def test_scale_factor():
    assert g.scale_factor(175.0, None) == 1.0
    assert g.scale_factor(175.0, 350.0) == pytest.approx(2.0)


def test_scale_factor_rejects_nonpositive_target():
    with pytest.raises(GeometryError, match="greater than zero"):
        g.scale_factor(175.0, 0.0)


# --------------------------------------------------------- trailing edge modes


def test_drop_duplicate_removes_the_repeated_te():
    out = g.drop_duplicate(LOOP)
    assert len(out) == 4
    assert out[0] == (100.0, 0.0)
    assert out[-1] == (50.0, -5.0)


def test_drop_duplicate_leaves_an_open_te_alone():
    assert g.drop_duplicate(OPEN_TE) == OPEN_TE


def test_auto_close_keeps_an_already_closed_loop():
    out = g.auto_close(LOOP)
    assert len(out) == 5
    assert out[0] == out[-1]


def test_auto_close_appends_across_a_finite_te_gap():
    out = g.auto_close(OPEN_TE)
    assert len(out) == len(OPEN_TE) + 1
    assert out[0] == out[-1] == (100.0, 1.0)


def test_split_surfaces_both_run_le_to_te():
    upper, lower = g.split_surfaces(LOOP)
    assert upper == [(0.0, 0.0), (50.0, 5.0), (100.0, 0.0)]
    assert lower == [(0.0, 0.0), (50.0, -5.0), (100.0, 0.0)]


def test_split_surfaces_needs_an_interior_leading_edge():
    with pytest.raises(GeometryError, match="leading edge is at an end"):
        g.split_surfaces([(0.0, 0.0), (50.0, 1.0), (100.0, 0.0)])


def test_split_surfaces_rejects_tiny_input():
    with pytest.raises(GeometryError, match="Not enough points"):
        g.split_surfaces([(0.0, 0.0), (1.0, 0.0)])


# ----------------------------------------------------------------- offsetting


RECT = [(100.0, 5.0), (0.0, 5.0), (0.0, -5.0), (100.0, -5.0), (100.0, 5.0)]

# Trimmed corners land on the chord line to within rounding, not exactly on it.
POINT_EPS = 1e-9

# LOOP is a wedge 5 thick per 50 of chord, so its nose and tail recede by
# distance / sin(atan(5/50)) = 10.0499 per unit of inward offset.
WEDGE_RECESSION = 1.0 / math.sin(math.atan2(5.0, 50.0))


def circle(radius, n=180):
    pts = [
        (radius * math.cos(2 * math.pi * i / n), radius * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]
    return pts + [pts[0]]


def wall(offset, original):
    """How far each point of an offset curve stands from the curve it came from."""
    loop = g.clean_loop(original)
    return [g.distance_to_loop(p, loop) for p in offset]


def extent(points):
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return max(xs) - min(xs), max(ys) - min(ys)


@pytest.mark.parametrize("distance", [-5.0, -0.5, 0.5, 5.0])
def test_offset_holds_a_constant_distance_all_the_way_round(distance):
    original = circle(50.0)
    gaps = wall(g.offset_airfoil(original, distance), original)
    assert min(gaps) == pytest.approx(abs(distance), rel=1e-6)
    assert max(gaps) == pytest.approx(abs(distance), rel=1e-6)


def test_outward_offset_grows_and_inward_offset_shrinks():
    original = circle(50.0)
    grown = abs(g.signed_area(g.clean_loop(g.offset_airfoil(original, 5.0))))
    shrunk = abs(g.signed_area(g.clean_loop(g.offset_airfoil(original, -5.0))))
    assert grown > abs(g.signed_area(g.clean_loop(original))) > shrunk
    assert shrunk == pytest.approx(math.pi * 45.0**2, rel=1e-3)


def test_offset_is_a_wall_not_a_rescale():
    """A rescale keeps the profile; an offset takes the same distance off every face."""
    chord, thickness = extent(g.offset_airfoil(RECT, -1.0))
    assert chord == pytest.approx(98.0)
    # A rescale to a 98 chord would leave 9.8 of thickness. An offset leaves 8.
    assert thickness == pytest.approx(8.0)


def test_inward_offset_trims_the_sharp_trailing_edge():
    """The offset upper and lower surfaces cross ahead of a sharp TE; the tail past it goes."""
    inner = g.offset_airfoil(LOOP, -2.0)
    assert max(x for x, _ in inner) == pytest.approx(100.0 - 2.0 * WEDGE_RECESSION)
    assert max(wall(inner, LOOP)) == pytest.approx(2.0, rel=1e-6)


@pytest.mark.parametrize("distance", [-4.0, -2.0, -0.5, 0.5, 3.0])
def test_offset_never_comes_closer_than_the_distance(distance):
    """The trim's whole job: no surviving point may undercut the wall thickness."""
    assert min(wall(g.offset_airfoil(LOOP, distance), LOOP)) >= abs(distance) - 1e-6


@pytest.mark.parametrize("distance", [-1.0, 1.0])
def test_offset_returns_a_closed_loop_starting_at_the_trailing_edge(distance):
    out = g.offset_airfoil(LOOP, distance)
    assert out[0] == out[-1]
    assert out[0][0] == pytest.approx(max(x for x, _ in out))


def test_offset_survives_a_clockwise_loop():
    """The CSV's winding must not decide which way 'outward' points."""
    ccw = g.clean_loop(g.offset_airfoil(LOOP, -1.0))
    cw = g.clean_loop(g.offset_airfoil(list(reversed(LOOP)), -1.0))
    assert g.signed_area(cw) == pytest.approx(-g.signed_area(ccw))
    for from_cw, from_ccw in zip(sorted(cw), sorted(ccw)):
        assert from_cw == approx(from_ccw)


def test_offset_output_still_splits_into_two_surfaces():
    upper, lower = g.split_surfaces(g.offset_airfoil(LOOP, -1.0))
    assert upper[0] == lower[0]  # both start at the leading edge
    assert upper[-1] == lower[-1]  # and end at the trailing edge
    assert all(y >= -POINT_EPS for _, y in upper)
    assert all(y <= POINT_EPS for _, y in lower)


def test_zero_offset_just_closes_the_loop():
    assert g.offset_airfoil(LOOP, 0.0) == LOOP


def test_offset_rejects_eating_the_whole_section():
    with pytest.raises(GeometryError, match="eats the whole section"):
        g.offset_airfoil(LOOP, -20.0)


def test_offset_rejects_a_non_finite_distance():
    with pytest.raises(GeometryError, match="finite"):
        g.offset_airfoil(LOOP, float("nan"))


def test_offset_rejects_a_degenerate_loop():
    with pytest.raises(GeometryError, match="Not enough points"):
        g.offset_airfoil([(0.0, 0.0), (1.0, 0.0)], -1.0)


def test_offset_rejects_a_curve_that_does_not_close():
    """One surface of an airfoil encloses nothing — there is no wall to walk round."""
    upper, _ = g.split_surfaces(LOOP)
    with pytest.raises(GeometryError, match="does not close on itself"):
        g.offset_airfoil(upper, -1.0)


def test_offset_accepts_the_gap_a_dropped_trailing_edge_leaves():
    """Drop-duplicate output is open by one segment of a dense loop; still a section."""
    dropped = g.drop_duplicate(circle(50.0))
    assert min(wall(g.offset_airfoil(dropped, -5.0), dropped)) >= 5.0 - 1e-6


# --------------------------------------------------------- blunt trailing edge

# LOOP is a diamond: aft of x=50 it thins from 10 mm to nothing at x=100, so it
# stands T mm thick at x = 100 - 5T.


@pytest.mark.parametrize("thickness", [0.5, 2.0, 5.0])
def test_blunt_te_cuts_the_section_back_to_a_vertical_line(thickness):
    cut = g.blunt_trailing_edge(LOOP, thickness)
    (x0, y0), (x1, y1) = cut[0], cut[-1]
    assert x0 == pytest.approx(x1)  # One end sits directly above the other.
    assert abs(y0 - y1) == pytest.approx(thickness, rel=1e-9)
    assert x0 == pytest.approx(100.0 - 5.0 * thickness, rel=1e-9)


def test_blunt_te_shortens_the_airfoil_rather_than_stretching_it():
    """The chord ends at the cut: what the cut takes off is not made up elsewhere."""
    cut = g.blunt_trailing_edge(LOOP, 2.0)
    assert max(x for x, _ in cut) == pytest.approx(90.0)
    assert min(x for x, _ in cut) == pytest.approx(0.0)  # The nose does not move.


def test_zero_thickness_leaves_the_section_alone():
    assert g.blunt_trailing_edge(LOOP, 0.0) == LOOP


def test_blunt_te_leaves_an_already_blunter_trailing_edge_alone():
    """A section that ends thicker than asked for has nothing to give up."""
    assert g.blunt_trailing_edge(OPEN_TE, 1.0) == OPEN_TE


def test_blunt_te_cuts_a_section_that_already_ends_blunt():
    cut = g.blunt_trailing_edge(OPEN_TE, 3.0)
    (x0, y0), (x1, y1) = cut[0], cut[-1]
    assert x0 == pytest.approx(x1) == pytest.approx(93.75)
    assert abs(y0 - y1) == pytest.approx(3.0)


def test_blunt_te_keeps_the_leading_edge_and_both_surfaces():
    cut = g.blunt_trailing_edge(LOOP, 2.0)
    upper, lower = g.split_surfaces(cut)
    assert upper[0] == lower[0] == (0.0, 0.0)
    assert upper[-1] == pytest.approx((90.0, 1.0))
    assert lower[-1] == pytest.approx((90.0, -1.0))


def test_auto_close_shuts_a_blunt_trailing_edge_with_the_vertical_line():
    closed = g.auto_close(g.blunt_trailing_edge(LOOP, 2.0))
    assert closed[-1] == closed[0]
    assert closed[-2][0] == pytest.approx(closed[-1][0])  # The closing line is vertical.


def test_blunt_te_squares_off_a_rounded_offset_trailing_edge():
    """An outward offset rounds the sharp tail; the cut flattens it again."""
    cut = g.blunt_trailing_edge(g.offset_airfoil(LOOP, 2.0), 1.5)
    (x0, y0), (x1, y1) = cut[0], cut[-1]
    assert x0 == pytest.approx(x1)
    assert abs(y0 - y1) == pytest.approx(1.5, rel=1e-6)


def test_blunt_te_rejects_a_cut_thicker_than_the_section():
    with pytest.raises(GeometryError, match="thicker than the section itself"):
        g.blunt_trailing_edge(LOOP, 20.0)


def test_blunt_te_rejects_a_negative_thickness():
    with pytest.raises(GeometryError, match="not be negative"):
        g.blunt_trailing_edge(LOOP, -1.0)


def test_blunt_te_rejects_a_non_finite_thickness():
    with pytest.raises(GeometryError, match="finite"):
        g.blunt_trailing_edge(LOOP, float("inf"))


# ------------------------------------------------- reading a curve back in 2D

# A section shaped like an airfoil: blunt at x=0, sharp at x=100.
SECTION = [
    (100.0, 0.0), (60.0, 6.0), (30.0, 8.0), (8.0, 6.0), (0.0, 0.0),
    (8.0, -4.0), (30.0, -5.0), (60.0, -3.0), (100.0, 0.0),
]


@pytest.mark.parametrize(
    "plane,le,pitch",
    [
        ("XY", (0.0, 0.0, 0.0), 0.0),
        ("XZ", (10.0, -3.0, 7.0), 0.0),
        ("YZ", (1.0, 2.0, 3.0), 12.0),
        ("3points", (5.0, 5.0, 5.0), -8.0),
    ],
)
def test_flatten_puts_a_curve_back_exactly_where_it_stood(plane, le, pitch):
    """Read a curve in, write it out unchanged: the points must land back on themselves."""
    u, v = g.plane_frame(plane, p1=(1, 2, 3), p2=(4, -1, 2), p3=(0, 5, 7), pitch=pitch)
    drawn = g.to_3d(SECTION, le, u, v)

    section = g.flatten_curve(drawn)
    back = g.to_3d(section.points, section.origin, section.u, section.v)
    for was, now in zip(drawn, back):
        assert now == approx(was)


def test_flatten_finds_the_chord_and_the_blunt_end():
    section = g.flatten_curve(g.to_3d(SECTION, (3.0, 4.0, 5.0), *g.plane_frame("XZ")))
    assert section.chord == pytest.approx(100.0)
    assert section.origin == approx((3.0, 4.0, 5.0))  # the blunt nose, not the sharp tail
    assert section.points[0] == approx((100.0, 0.0))  # and the 2D section reads like a CSV's


def test_flatten_reads_a_reversed_curve_the_same_way_round():
    """A curve drawn tail-first still comes back nose-at-the-origin."""
    drawn = g.to_3d(SECTION, (0.0, 0.0, 0.0), *g.plane_frame("XY"))
    section = g.flatten_curve(list(reversed(drawn)))
    assert section.origin == approx((0.0, 0.0, 0.0))
    assert section.chord == pytest.approx(100.0)
    assert g.signed_area(g.clean_loop(section.points)) > 0  # still counter-clockwise


def test_flatten_rejects_a_curve_that_is_not_flat():
    drawn = g.to_3d(SECTION, (0.0, 0.0, 0.0), *g.plane_frame("XY"))
    drawn[3] = (drawn[3][0], drawn[3][1], 5.0)  # lift one point out of the plane
    with pytest.raises(GeometryError, match="not flat"):
        g.flatten_curve(drawn)


def test_flatten_tolerates_the_rounding_in_a_written_file():
    """Curves are written to six decimals, so they are never quite flat."""
    drawn = [
        tuple(round(c, 6) for c in p)
        for p in g.to_3d(SECTION, (1.0, 2.0, 3.0), *g.plane_frame("3points",
                         p1=(1, 2, 3), p2=(4, -1, 2), p3=(0, 5, 7)))
    ]
    assert g.flatten_curve(drawn).chord == pytest.approx(100.0, abs=1e-4)


def test_flatten_rejects_a_straight_line():
    with pytest.raises(GeometryError, match="straight line"):
        g.flatten_curve([(0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (2.0, 2.0, 2.0)])


def test_flatten_rejects_a_curve_with_too_few_points():
    with pytest.raises(GeometryError, match="at least three points"):
        g.flatten_curve([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])


def test_loaded_plane_mode_keeps_the_curves_own_frame():
    u, v = g.plane_frame("XZ")
    assert g.plane_frame(g.LOADED, frame=(u, v)) == (approx(u), approx(v))


def test_loaded_plane_mode_still_pitches_and_flips():
    u, v = g.plane_frame("XY")
    assert g.plane_frame(g.LOADED, frame=(u, v), flip=True)[1] == approx((0, -1, 0))
    assert g.plane_frame(g.LOADED, frame=(u, v), rotate180=True)[0] == approx((-1, 0, 0))


def test_loaded_plane_mode_needs_a_frame():
    with pytest.raises(GeometryError, match="no plane to keep it on"):
        g.plane_frame(g.LOADED)
