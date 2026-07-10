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
