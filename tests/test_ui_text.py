"""The window's derived text, tested without a window.

Every readout, tracker row and card line in the redesigned window comes from
:mod:`airfoil_converter.ui_text`, which is why these can be run on a machine
with no display and no SolidWorks — the same reason the pick rules live in
:mod:`airfoil_converter.pick` rather than in the handlers that call them.
"""

from airfoil_converter import pick, store, ui_text
from airfoil_converter.export import (
    MODE_2POINTS,
    MODE_3POINTS,
    MODE_LOADED,
    MODE_NORMAL,
    ExportSpec,
)
from airfoil_converter.swcom import PickedLine, PickedPlane, PickedPoint, Refused


# ------------------------------------------------------------- the readouts


def test_a_main_plane_reads_out_which_way_the_section_faces():
    assert ui_text.plane_readout("XY", "-X", "+Y", "Perpendicular", "XY") == (
        "XY · nose -X · up +Y"
    )


def test_a_picked_plane_says_where_it_came_from():
    assert ui_text.plane_readout(MODE_3POINTS, "", "", "", "") == "3 points"
    assert ui_text.plane_readout(MODE_3POINTS, "", "", "", "", picked=True) == (
        "3 points · picked in SolidWorks"
    )


def test_two_points_reads_out_its_constraint():
    assert ui_text.plane_readout(MODE_2POINTS, "", "", "Parallel", "XZ") == (
        "2 points · parallel to XZ"
    )


def test_the_other_modes_name_themselves():
    assert ui_text.plane_readout(MODE_NORMAL, "", "", "", "") == "Normal to line"
    assert ui_text.plane_readout(MODE_LOADED, "", "", "", "") == "As loaded"


def test_the_placement_readout_says_where_the_leading_edge_came_from():
    assert ui_text.placement_readout("XY") == "where the 2D origin lands"
    assert ui_text.placement_readout(MODE_3POINTS) == "defaults to P1"
    assert ui_text.placement_readout(MODE_LOADED) == "the curve's own leading edge"
    assert ui_text.placement_readout("XY", manual=True) == "typed in by hand"
    assert ui_text.placement_readout("XY", manual=True, picked=True) == (
        "picked in SolidWorks"
    )


def test_the_source_line_is_a_name_and_the_rest():
    name, detail = ui_text.source_line("SD7037", 175.0, 61, True, False)
    assert name == "SD7037"
    assert detail == "175 mm · 61 pts · camber line"


def test_a_curve_file_says_so_and_a_missing_camber_line_says_so():
    _, detail = ui_text.source_line("rib", 120.0, 40, False, True)
    assert detail == "120 mm · 40 pts · curve file · no camber line"


# ----------------------------------------------------------------- the link


def test_the_link_headline_carries_its_own_state():
    assert ui_text.link_headline(False, None) == ("pywin32 not installed", "off")
    assert ui_text.link_headline(True, None) == ("not reachable", "bad")

    snapshot = {"version": "SolidWorks 2026", "title": "", "is_part": False}
    assert ui_text.link_headline(True, snapshot)[1] == "bad"

    snapshot = {"version": "SolidWorks 2026", "title": "Wing.SLDASM", "is_part": False}
    text, state = ui_text.link_headline(True, snapshot)
    assert "not a part" in text and state == "bad"

    snapshot = {"version": "SolidWorks 2026", "title": "Rib.SLDPRT", "is_part": True}
    assert ui_text.link_headline(True, snapshot) == (
        "SolidWorks 2026 — Rib.SLDPRT", "ok"
    )


def test_the_flyout_says_where_the_settings_are_kept():
    snapshot = {"is_part": True, "path": r"D:\Wings\Rib.SLDPRT"}
    assert ui_text.link_detail(snapshot, r"D:\Wings\Rib.airfoils.json", "") == (
        "Settings kept beside the part in Rib.airfoils.json"
    )


def test_an_unsaved_part_is_warned_about_rather_than_named():
    snapshot = {"is_part": True, "path": ""}
    assert "never been saved" in ui_text.link_detail(snapshot, "", "")


def test_with_no_part_the_reason_stands_in():
    assert ui_text.link_detail(None, "", "SolidWorks is not running.") == (
        "SolidWorks is not running."
    )


# --------------------------------------------------------- the pick tracker


def test_the_countdown_is_minutes_and_seconds():
    assert ui_text.countdown(72) == "1:12 left"
    assert ui_text.countdown(0.4) == "0:00 left"
    assert ui_text.countdown(-5) == "0:00 left"


def test_a_fresh_pick_has_one_active_row_and_two_still_to_come():
    rows = ui_text.tracker_rows(pick.Pick(pick.PLANE_PICK))
    assert [r.state for r in rows] == ["active", "pending", "pending"]
    assert [r.name for r in rows] == ["Plane", "Chord line", "Leading edge"]
    assert rows[1].detail.startswith("optional · ")


def test_an_accepted_click_is_shown_as_what_was_read():
    session = pick.Pick(pick.PLANE_PICK)
    session.accept(PickedPlane(normal=(0.0, 0.0, 2.0), root=(0.0, 0.0, 0.0)))
    rows = ui_text.tracker_rows(session)
    assert rows[0].state == "done"
    assert rows[0].detail == "normal 0, 0, 1"
    assert rows[1].state == "active"


def test_a_refusal_lands_on_the_active_row_and_is_marked_as_one():
    session = pick.Pick(pick.PLANE_PICK)
    session.accept(PickedPlane(normal=(0.0, 0.0, 1.0), root=(0.0, 0.0, 0.0)))
    session.accept(Refused("a circular edge"))
    rows = ui_text.tracker_rows(session)
    assert rows[1].state == "active"
    assert rows[1].error is True
    assert "circular edge" in rows[1].detail
    assert rows[1].skippable is True


def test_the_title_counts_the_steps_and_shows_the_timeout():
    session = pick.Pick(pick.PLANE_PICK)
    session.accept(PickedPlane(normal=(0.0, 0.0, 1.0), root=(0.0, 0.0, 0.0)))
    assert ui_text.tracker_title(session, 72) == "step 2 of 3 · 1:12 left"


def test_a_finished_pick_does_not_count_past_its_last_step():
    session = pick.Pick(pick.POINT_PICK)
    session.accept(PickedPoint(where=(1.0, 2.0, 3.0)))
    assert ui_text.tracker_title(session, 10) == "step 1 of 1 · 0:10 left"


def test_each_kind_of_picked_thing_describes_itself():
    assert ui_text.picked_detail(PickedPoint(where=(12.5, 0.0, 40.0))) == "12.5, 0, 40"
    assert ui_text.picked_detail(
        PickedLine(start=(0.0, 0.0, 0.0), end=(175.0, 0.0, 0.0))
    ) == "175 mm line"
    assert ui_text.picked_detail(Refused("a circular edge")) == ""


# ------------------------------------------------------------- the flyout's
#                                                                record card


def test_a_records_settings_fit_on_one_line():
    spec = ExportSpec(leading_edge=("0", "0", "120"), plane_mode="XY",
                      target_chord="175", te_thickness="0.4")
    assert ui_text.record_summary(spec) == (
        "Leading edge at 0, 0, 120 · XY · chord 175 mm · TE 0.4 mm · no offset"
    )


def test_a_blank_chord_and_an_offset_are_said_in_words():
    spec = ExportSpec(offset="0.8", offset_dir="Outward")
    summary = ui_text.record_summary(spec)
    assert "source chord" in summary
    assert summary.endswith("offset 0.8 mm outward")


def test_curves_all_linked_reads_as_a_count():
    states = [
        store.CurveState(feature="a", state=store.LINKED),
        store.CurveState(feature="b", state=store.LINKED),
    ]
    assert ui_text.curves_linked(states) == ("2 curves linked", True)


def test_one_curve_in_trouble_says_what_is_wrong_with_it():
    states = [
        store.CurveState(feature="a", state=store.LINKED),
        store.CurveState(feature="b", state=store.MISSING),
    ]
    assert ui_text.curves_linked(states) == (store.MISSING, False)


def test_several_in_trouble_are_counted_rather_than_listed():
    states = [
        store.CurveState(feature="a", state=store.DRIFTED),
        store.CurveState(feature="b", state=store.MISSING),
    ]
    assert ui_text.curves_linked(states) == ("2 need attention", False)


# -- wings ------------------------------------------------------------------


def test_a_wing_is_summed_up_in_one_line():
    from airfoil_converter.export import WingSpec

    spec = WingSpec(ribs=("a", "b", "c"), le_source="le.sldcrv", offset="2")
    assert ui_text.wing_summary(spec) == (
        "3 ribs · LE from a file, TE straight · root open, tip closed · offset 2 mm inward"
    )
    assert ui_text.wing_summary(WingSpec()).endswith("no offset")


def test_a_wing_says_what_it_is_where_a_rib_says_where_it_stands():
    from airfoil_converter.export import OFFSET_OUTWARD, WingSpec

    assert ui_text.wing_place(WingSpec()) == "wing"
    assert ui_text.wing_place(WingSpec(offset="1.5", offset_dir=OFFSET_OUTWARD)) == (
        "wing, 1.5 mm outward"
    )
