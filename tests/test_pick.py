"""What a click in SolidWorks turns into, tested without SolidWorks.

The pick reduces a plane and a line to three points, and a wrong reduction is
invisible: the section still exports, still lands on a plane, still looks like
an aerofoil. It is only wrong against the model. So the reduction is pinned
here, by rebuilding the frame from the points the pick produced and asserting
it is the plane that was picked.
"""

import pytest

from airfoil_converter import geometry as g
from airfoil_converter import pick
from airfoil_converter.export import MODE_3POINTS, MODE_NORMAL
from airfoil_converter.swcom import PickedLine, PickedPlane, PickedPoint, Refused

XY = PickedPlane(normal=(0.0, 0.0, 1.0), root=(0.0, 0.0, 0.0))
TILTED = PickedPlane(normal=(0.0, 1.0, 1.0), root=(5.0, 5.0, 5.0))
ALONG_X = PickedLine(start=(0.0, 0.0, 0.0), end=(100.0, 0.0, 0.0))
NOSE = PickedPoint(where=(0.0, 0.0, 0.0))


def approx(vec):
    return pytest.approx(vec, abs=1e-12)


def frame(placement):
    """The chord and up vectors the form would end up producing."""
    p1, p2 = placement.points[0], placement.points[1]
    p3 = placement.points[2] if len(placement.points) > 2 else (0.0, 0.0, 0.0)
    return g.plane_frame(placement.plane_mode, p1=p1, p2=p2, p3=p3)


def run(steps, *picks):
    session = pick.Pick(steps)
    for picked in picks:
        session.accept(picked)
    return session


# ------------------------------------------------------------ the four rows


def test_a_plane_a_line_and_a_point_give_three_points():
    placement = run(pick.PLANE_PICK, XY, ALONG_X, PickedPoint((12.5, 4.0, 0.0))).resolve()
    assert placement.plane_mode == MODE_3POINTS
    assert len(placement.points) == 3
    assert placement.points[0] == (12.5, 4.0, 0.0)
    assert placement.leading_edge == (12.5, 4.0, 0.0)


def test_a_plane_and_a_line_without_a_point_leave_the_leading_edge_alone():
    """P1 still lands on the line, and the form's own default fills the leading
    edge in from it — so skipping the point is not the same as picking nothing."""
    session = run(pick.PLANE_PICK, XY, ALONG_X)
    session.skip()
    placement = session.resolve()
    assert placement.plane_mode == MODE_3POINTS
    assert placement.leading_edge is None
    assert placement.points[0] == approx((0.0, 0.0, 0.0))


def test_a_plane_and_a_point_without_a_line_fall_back_to_the_normal():
    session = run(pick.PLANE_PICK, XY)
    session.skip()
    session.accept(PickedPoint((1.0, 2.0, 0.0)))
    placement = session.resolve()
    assert placement.plane_mode == MODE_NORMAL
    assert placement.points == ((1.0, 2.0, 0.0), (1.0, 2.0, 1.0))
    assert placement.leading_edge == (1.0, 2.0, 0.0)


def test_a_plane_alone_is_anchored_at_the_plane_root():
    session = run(pick.PLANE_PICK, TILTED)
    session.stop()
    placement = session.resolve()
    assert placement.plane_mode == MODE_NORMAL
    assert placement.points[0] == TILTED.root
    assert placement.leading_edge is None


def test_a_point_alone_moves_the_leading_edge_and_nothing_else():
    placement = run(pick.POINT_PICK, PickedPoint((7.0, 8.0, 9.0))).resolve()
    assert placement.plane_mode is None
    assert placement.points == ()
    assert placement.leading_edge == (7.0, 8.0, 9.0)


# ------------------------------------------------------------ the plane comes back


@pytest.mark.parametrize(
    "plane,line",
    [
        (XY, ALONG_X),
        (TILTED, PickedLine((5.0, 5.0, 5.0), (5.0, 15.0, -5.0))),
        (PickedPlane((1.0, 0.0, 0.0), (2.0, 0.0, 0.0)), PickedLine((2.0, 0.0, 0.0), (2.0, 9.0, 0.0))),
    ],
)
def test_the_frame_rebuilds_the_plane_that_was_picked(plane, line):
    placement = run(pick.PLANE_PICK, plane, line, PickedPoint(line.start)).resolve()
    u, v = frame(placement)
    assert g.cross(u, v) == approx(g.normalize(plane.normal))


def test_the_chord_runs_along_the_line_that_was_picked():
    placement = run(pick.PLANE_PICK, XY, ALONG_X, NOSE).resolve()
    u, _ = frame(placement)
    assert u == approx((1.0, 0.0, 0.0))


def test_a_line_off_the_plane_still_gives_a_chord_on_it():
    """A sketch line rarely lies exactly on the plane, and only its shadow can
    be a chord."""
    placement = run(
        pick.PLANE_PICK, XY, PickedLine((0.0, 0.0, 30.0), (100.0, 0.0, 90.0)), NOSE
    ).resolve()
    u, _ = frame(placement)
    assert u == approx((1.0, 0.0, 0.0))


def test_a_line_perpendicular_to_the_plane_is_refused():
    session = run(pick.PLANE_PICK, XY, PickedLine((0.0, 0.0, 0.0), (0.0, 0.0, 50.0)), NOSE)
    with pytest.raises(g.GeometryError, match="perpendicular to the plane"):
        session.resolve()


# ------------------------------------------------------------------ chord sign


def test_the_end_nearest_the_leading_edge_is_the_nose():
    """P1 -> P2 runs nose to tail, so picking the far end of the line as the
    leading edge has to turn the chord round."""
    at_start = run(pick.PLANE_PICK, XY, ALONG_X, PickedPoint((0.0, 0.0, 0.0))).resolve()
    at_end = run(pick.PLANE_PICK, XY, ALONG_X, PickedPoint((100.0, 0.0, 0.0))).resolve()
    assert frame(at_start)[0] == approx((1.0, 0.0, 0.0))
    assert frame(at_end)[0] == approx((-1.0, 0.0, 0.0))


def test_without_a_leading_edge_the_line_keeps_its_own_direction():
    session = run(pick.PLANE_PICK, XY, PickedLine((100.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
    session.skip()
    assert frame(session.resolve())[0] == approx((-1.0, 0.0, 0.0))


# ------------------------------------------------------------------ the steps


def test_the_steps_run_plane_then_line_then_point():
    session = pick.Pick(pick.PLANE_PICK)
    assert session.step.wants == pick.PLANE
    session.accept(XY)
    assert session.step.wants == pick.LINE
    session.accept(ALONG_X)
    assert session.step.wants == pick.POINT
    session.accept(NOSE)
    assert session.finished


def test_the_wrong_kind_of_thing_is_refused_and_the_step_stays_put():
    session = pick.Pick(pick.PLANE_PICK)
    assert session.accept(PickedPoint((1.0, 1.0, 1.0))) is False
    assert session.step.wants == pick.PLANE
    assert "Click a reference plane" in session.says
    assert session.accept(XY) is True
    assert session.says == pick.Step(pick.LINE, optional=True).ask


def test_something_that_is_no_use_at_all_says_what_it_was():
    session = pick.Pick(pick.PLANE_PICK)
    session.accept(Refused("a curved edge"))
    assert session.says.startswith("That is a curved edge.")


def test_the_first_step_cannot_be_skipped():
    session = pick.Pick(pick.PLANE_PICK)
    assert session.skippable is False
    assert session.skip() is False
    assert session.step.wants == pick.PLANE


def test_the_optional_steps_can_be_skipped():
    session = run(pick.PLANE_PICK, XY)
    assert session.skippable is True
    assert session.skip() is True
    assert session.step.wants == pick.POINT
    assert session.skip() is True
    assert session.finished


def test_the_leading_edge_pick_is_one_step_and_not_optional():
    session = pick.Pick(pick.POINT_PICK)
    assert session.skippable is False
    assert "Click the leading edge" in session.says
    session.accept(PickedPoint((0.0, 0.0, 0.0)))
    assert session.finished


def test_nothing_is_accepted_once_the_steps_are_done():
    session = run(pick.POINT_PICK, NOSE)
    assert session.accept(PickedPoint((9.0, 9.0, 9.0))) is False
    assert session.point == NOSE


def test_a_refusal_is_cleared_by_the_next_good_pick():
    session = pick.Pick(pick.PLANE_PICK)
    session.accept(Refused("a whole feature"))
    assert session.says.startswith("That is")
    session.accept(XY)
    assert session.says.startswith("Click a line")


# -- telling a click from a selection still there ----------------------------


def test_what_was_selected_before_the_pick_is_not_a_click():
    watch = pick.SelectionWatch()
    plane = PickedPlane(normal=(0.0, 0.0, 1.0), root=(0.0, 0.0, 0.0))
    assert watch.fresh(plane) is None
    assert watch.fresh(plane) is None


def test_a_changed_selection_is_a_click_once():
    watch = pick.SelectionWatch()
    point = PickedPoint(where=(1.0, 2.0, 3.0))
    assert watch.fresh(None) is None
    assert watch.fresh(point) == point
    assert watch.fresh(point) is None   # still selected, polled again


def test_the_same_thing_clicked_again_after_nothing_counts():
    watch = pick.SelectionWatch()
    point = PickedPoint(where=(1.0, 2.0, 3.0))
    watch.fresh(None)
    assert watch.fresh(point) == point
    assert watch.fresh(None) is None
    assert watch.fresh(point) == point
