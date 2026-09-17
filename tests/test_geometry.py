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


# ---------------------------------------------------------- quarter turns


def test_no_quarter_turns_changes_nothing():
    assert g.plane_frame("XY", quarter_turns=0) == g.plane_frame("XY")


def test_one_quarter_turn_stands_the_section_on_its_tail():
    """A turn goes the way a positive angle of attack goes: the tail swings to -v."""
    u, v = g.plane_frame("XY")
    ru, rv = g.plane_frame("XY", quarter_turns=1)
    assert ru == approx(g.negate(v))
    assert rv == approx(u)


def test_a_quarter_turn_matches_a_90_degree_pitch():
    for turns in range(4):
        turned = g.plane_frame("XZ", quarter_turns=turns)
        pitched = g.plane_frame("XZ", pitch=90.0 * turns)
        assert turned == (approx(pitched[0]), approx(pitched[1]))


def test_three_quarter_turns_are_one_the_other_way():
    back = g.plane_frame("YZ", quarter_turns=-1)
    assert g.plane_frame("YZ", quarter_turns=3) == (approx(back[0]), approx(back[1]))


def test_four_quarter_turns_are_identity():
    assert g.plane_frame("XY", quarter_turns=4) == g.plane_frame("XY")


def test_quarter_turns_stay_exact():
    """Turning by whole quarters only swaps and negates axes — no trigonometry creeps in."""
    for turns in range(1, 4):
        for vec in g.plane_frame("XY", quarter_turns=turns):
            assert sorted(abs(c) for c in vec) == [0.0, 0.0, 1.0]


def test_a_quarter_turn_swings_the_tail_toward_the_down_side():
    u, v = g.plane_frame("XY", quarter_turns=1)
    section = [(0.0, 0.0), (100.0, 0.0), (50.0, 10.0)]
    turned = g.to_3d(section, (0, 0, 0), u, v)
    assert turned[0] == approx((0, 0, 0))
    assert turned[1] == approx((0, -100, 0))
    assert turned[2] == approx((10, -50, 0))


def test_quarter_turns_reject_a_fractional_count():
    with pytest.raises(GeometryError, match="whole number of quarter turns"):
        g.plane_frame("XY", quarter_turns="half")


def test_two_quarter_turns_negate_both_axes():
    u, v = g.plane_frame("XY", quarter_turns=2)
    assert u == approx((-1, 0, 0))
    assert v == approx((0, -1, 0))


def test_two_quarter_turns_swap_nose_and_tail_about_the_leading_edge():
    """Nose stays put; the body and the camber both swing to the other side."""
    u, v = g.plane_frame("XY")
    ru, rv = g.plane_frame("XY", quarter_turns=2)
    section = [(0.0, 0.0), (100.0, 0.0), (50.0, 10.0)]
    normal = g.to_3d(section, (0, 0, 0), u, v)
    rotated = g.to_3d(section, (0, 0, 0), ru, rv)
    assert normal[0] == approx((0, 0, 0)) and rotated[0] == approx((0, 0, 0))
    assert normal[1] == approx((100, 0, 0)) and rotated[1] == approx((-100, 0, 0))
    assert normal[2] == approx((50, 10, 0)) and rotated[2] == approx((-50, -10, 0))


def test_two_quarter_turns_preserve_shape():
    """A half turn is rigid: every pairwise distance survives it."""
    section = [(0.0, 0.0), (175.0, 0.0), (60.0, 12.0), (40.0, -4.0)]
    u, v = g.plane_frame("XZ")
    ru, rv = g.plane_frame("XZ", quarter_turns=2)
    plain = g.to_3d(section, (3, 4, 5), u, v)
    spun = g.to_3d(section, (3, 4, 5), ru, rv)
    for i in range(len(section)):
        for j in range(i + 1, len(section)):
            assert g.length(g.sub(plain[i], plain[j])) == pytest.approx(
                g.length(g.sub(spun[i], spun[j]))
            )


def test_two_quarter_turns_applied_twice_is_identity():
    u, v = g.plane_frame("YZ")
    ru, rv = g.plane_frame("YZ", quarter_turns=2)
    assert g.negate(ru) == approx(u)
    assert g.negate(rv) == approx(v)


def test_a_half_turn_and_flip_compose_to_a_chord_only_mirror():
    """Rotating then flipping up leaves 'up' alone and reverses only the chord."""
    u, v = g.plane_frame("XY", quarter_turns=2, flip=True)
    assert u == approx((-1, 0, 0))
    assert v == approx((0, 1, 0))


def test_quarter_turns_work_on_custom_planes():
    u, v = g.plane_frame("3points", p1=(0, 0, 0), p2=(1, 0, 0), p3=(0, 1, 0), quarter_turns=2)
    assert u == approx((-1, 0, 0))
    assert v == approx((0, -1, 0))


def test_quarter_turns_combine_with_explicit_axes():
    u, v = g.plane_frame("YZ", chord_axis="+Z", up_axis="+Y", quarter_turns=2)
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


def test_pitch_accumulates_with_quarter_turns():
    """The quarter turns spin first, so the pitch still measures against the final up."""
    u, v = g.plane_frame("XY", quarter_turns=2, pitch=90.0)
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
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, 0, 5), quarter_turns=2)
    assert (u, v) == (approx((-1, 0, 0)), approx((0, -1, 0)))
    u, v = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, 0, 5), pitch=90.0)
    assert (u, v) == (approx((0, -1, 0)), approx((1, 0, 0)))


def test_normal_line_rejects_coincident_points():
    with pytest.raises(GeometryError, match="coincident"):
        g.plane_frame(g.NORMAL_LINE, p1=(1, 1, 1), p2=(1, 1, 1))


# ------------------------------------------------------- points picked off a model


PICKED = [
    ((0, 0, 1), (1, 0, 0)),          # XY, chord along +X
    ((0, 1, 0), (0, 0, 1)),          # XZ, chord along +Z
    ((1, 0, 0), (0, 1, 0)),          # YZ, chord along +Y
    ((1, 1, 0), (0, 0, 1)),          # a plane on no axis at all
    ((2, -3, 6), (1, 1, 1)),         # normal not a unit vector
]


@pytest.mark.parametrize("normal,chord", PICKED)
def test_picked_points_rebuild_the_plane_they_came_from(normal, chord):
    """The whole point of the pick: what SolidWorks was asked for is what comes
    back. A plane reconstructed even slightly off would put a rib slightly off,
    and nothing downstream would notice."""
    n = g.normalize(normal)
    flat = g.normalize(g.sub(chord, g.scale(n, g.dot(chord, n))))
    p1, p2, p3 = g.picked_points(normal, (7.0, -2.0, 3.0), chord)
    u, v = g.plane_frame("3points", p1=p1, p2=p2, p3=p3)
    assert g.cross(u, v) == approx(n)
    assert u == approx(flat)


def test_picked_points_put_the_nose_at_p1():
    p1, _, _ = g.picked_points((0, 0, 1), (12.5, 4.0, 0.0), (1, 0, 0))
    assert p1 == (12.5, 4.0, 0.0)


def test_the_points_keep_the_length_of_the_line_they_came_from():
    """Only the directions reach the frame, but these three numbers are shown
    to someone: P2 belongs at the end of the line they clicked, not a
    millimetre along it."""
    p1, p2, p3 = g.picked_points((0, 0, 1), (0, 0, 0), (100.0, 0.0, 0.0))
    assert p2 == approx((100, 0, 0))
    assert g.length(g.sub(p3, p1)) == pytest.approx(100.0)


def test_a_line_off_the_plane_is_projected_onto_it():
    """A sketch line rarely lies exactly on the plane you picked, and only its
    shadow on that plane can be a chord."""
    p1, p2, p3 = g.picked_points((0, 0, 1), (0, 0, 0), (1, 0, 0.9))
    u, _ = g.plane_frame("3points", p1=p1, p2=p2, p3=p3)
    assert u == approx((1, 0, 0))


def test_the_chord_runs_the_way_the_line_was_given():
    forward = g.picked_points((0, 0, 1), (0, 0, 0), (1, 0, 0))
    backward = g.picked_points((0, 0, 1), (0, 0, 0), (-1, 0, 0))
    u_forward, _ = g.plane_frame("3points", p1=forward[0], p2=forward[1], p3=forward[2])
    u_backward, _ = g.plane_frame("3points", p1=backward[0], p2=backward[1], p3=backward[2])
    assert u_forward == approx((1, 0, 0))
    assert u_backward == approx((-1, 0, 0))


def test_picked_points_are_right_handed_like_a_normal_to_line_plane():
    """Both build a frame from a normal alone, so they had better agree."""
    line = g.plane_frame(g.NORMAL_LINE, p1=(0, 0, 0), p2=(0, 0, 1))
    p1, p2, p3 = g.picked_points((0, 0, 1), (0, 0, 0), line[0])
    assert g.plane_frame("3points", p1=p1, p2=p2, p3=p3)[1] == approx(line[1])


def test_a_line_perpendicular_to_the_plane_is_refused():
    with pytest.raises(GeometryError, match="perpendicular to the plane"):
        g.picked_points((0, 0, 1), (0, 0, 0), (0, 0, 5))


def test_a_plane_with_no_normal_is_refused():
    with pytest.raises(GeometryError, match="plane normal"):
        g.picked_points((0, 0, 0), (0, 0, 0), (1, 0, 0))


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


# ------------------------------------------- blunt trailing edge, chord kept


@pytest.mark.parametrize("thickness", [0.5, 2.0, 5.0])
def test_keep_chord_returns_the_full_chord_and_the_asked_for_gap(thickness):
    cut = g.blunt_trailing_edge(LOOP, thickness, keep_chord=True)
    (x0, y0), (x1, y1) = cut[0], cut[-1]
    assert g.chord_span(cut) == pytest.approx(100.0, rel=1e-9)
    assert x0 == pytest.approx(x1)  # The gap is still a vertical line.
    assert abs(y0 - y1) == pytest.approx(thickness, rel=1e-6)


def test_keep_chord_cuts_further_aft_and_thinner_than_a_plain_cut():
    """Growing the section back thickens the gap, so the cut allows for that."""
    plain = g.blunt_trailing_edge(LOOP, 2.0)
    kept = g.blunt_trailing_edge(LOOP, 2.0, keep_chord=True)
    # The cut is made at x = 2000/22 on a section that is then grown by 100/x.
    assert max(x for x, _ in plain) == pytest.approx(90.0)
    assert abs(kept[0][1] - kept[-1][1]) == pytest.approx(2.0, rel=1e-6)
    assert len(kept) == len(plain)


def test_keep_chord_scales_the_whole_section_rather_than_stretching_it():
    """A true scaling: the profile is unchanged, just very slightly larger."""
    kept = g.blunt_trailing_edge(LOOP, 2.0, keep_chord=True)
    factor = 100.0 / (2000.0 / 22.0)  # chord / the station the cut is made at
    assert min(x for x, _ in kept) == pytest.approx(0.0)  # The nose stays at the origin.
    assert max(y for _, y in kept) == pytest.approx(5.0 * factor)
    assert [x for x, y in kept if y == max(yy for _, yy in kept)] == [
        pytest.approx(50.0 * factor)
    ]


def test_keep_chord_on_a_section_that_already_ends_blunt():
    kept = g.blunt_trailing_edge(OPEN_TE, 3.0, keep_chord=True)
    assert g.chord_span(kept) == pytest.approx(100.0, rel=1e-9)
    assert abs(kept[0][1] - kept[-1][1]) == pytest.approx(3.0, rel=1e-6)


def test_keep_chord_still_rejects_a_cut_thicker_than_the_section():
    with pytest.raises(GeometryError, match="thicker than the section itself"):
        g.blunt_trailing_edge(LOOP, 20.0, keep_chord=True)


# ------------------------------------------------------- trailing-edge line


def test_te_line_is_the_two_ends_of_a_blunt_section():
    cut = g.blunt_trailing_edge(LOOP, 2.0)
    line = g.trailing_edge_line(cut)
    assert line == [cut[0], cut[-1]]
    assert line[0][0] == pytest.approx(line[1][0])  # Vertical.
    assert abs(line[0][1] - line[1][1]) == pytest.approx(2.0, rel=1e-9)


def test_te_line_closes_a_chord_keeping_cut_too():
    kept = g.blunt_trailing_edge(LOOP, 0.8, keep_chord=True)
    line = g.trailing_edge_line(kept)
    assert abs(line[0][1] - line[1][1]) == pytest.approx(0.8, rel=1e-6)


def test_te_line_rejects_a_section_that_ends_in_a_point():
    with pytest.raises(GeometryError, match="ends in a point"):
        g.trailing_edge_line(LOOP)


def test_te_line_rejects_ends_that_are_not_a_trailing_edge_gap():
    with pytest.raises(GeometryError, match="do not stand one above the other"):
        g.trailing_edge_line(g.drop_duplicate(LOOP))


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
    assert g.plane_frame(g.LOADED, frame=(u, v), quarter_turns=2)[0] == approx((-1, 0, 0))


def test_loaded_plane_mode_needs_a_frame():
    with pytest.raises(GeometryError, match="no plane to keep it on"):
        g.plane_frame(g.LOADED)


# -- curves SolidWorks will import -----------------------------------------


def test_thinning_drops_points_that_crowd_the_one_before():
    pts = [(0.0, 0.0), (1.0, 0.0), (1.000001, 0.0), (2.0, 0.0), (2.005, 0.0), (2.02, 0.0),
           (3.0, 0.0)]
    assert g.thin_curve(pts) == [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.02, 0.0), (3.0, 0.0)]


def test_thinning_keeps_both_ends():
    pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.001, 0.0)]
    assert g.thin_curve(pts) == [(0.0, 0.0), (1.0, 0.0), (2.001, 0.0)]
    closed = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
    assert g.thin_curve(closed) == closed


def test_an_offset_puts_no_two_points_a_hair_apart():
    """A slight turn used to become an arc of two points almost on top of each
    other, which makes a curve file SolidWorks refuses."""
    loop = [(math.cos(t) * (3 + 0.02 * math.sin(7 * t)), math.sin(t))
            for t in [2 * math.pi * k / 180 for k in range(180)]]
    for distance in (-0.3, 0.3):
        out = g.clean_loop(g.offset_airfoil(g.auto_close(loop), distance))
        steps = [math.dist(a, b) for a, b in zip(out, out[1:])]
        assert min(steps) > 1e-4
