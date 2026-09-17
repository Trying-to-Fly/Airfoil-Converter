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


# -- the Wing tab --------------------------------------------------------------

from types import SimpleNamespace as NS  # noqa: E402

from airfoil_converter import export, wing  # noqa: E402


def test_the_wing_panel_says_which_wing_export_is_aimed_at():
    assert ui_text.wing_readout("") == "new wing"
    assert ui_text.wing_readout("wing_inner") == "editing wing_inner"


def test_a_rib_row_is_its_name_and_its_leading_edge():
    rib = store.ExportRecord(export_id="r", stem="sd7037-il", name_index=2,
                             settings=ExportSpec(leading_edge=("0", "0", "350")).to_dict())
    assert ui_text.rib_columns(rib) == ("sd7037-il (2)", "0, 0, 350")
    floats = store.ExportRecord(export_id="f", stem="r", settings=ExportSpec(
        leading_edge=("-350.0", "0.0", "-8.845895220737566")).to_dict())
    assert ui_text.rib_columns(floats) == ("r", "-350, 0, -8.8459")
    adopted = store.ExportRecord(export_id="a", stem="Curve7", adopted=True)
    assert ui_text.rib_columns(adopted) == ("Curve7", "taken over · no settings")


def test_the_ribs_readout_counts_the_ticks():
    assert ui_text.ribs_readout(4, 4) == "4 of 4 ticked"
    assert ui_text.ribs_readout(0, 0) == "no ribs yet"
    assert ui_text.ribs_readout(0, 0, have_record=False) == "no part's record open"


def test_the_edges_and_ends_read_out_in_a_line():
    assert ui_text.edges_readout(True, False) == "LE from a file · TE straight"
    assert ui_text.edges_readout(False, True) == "LE straight · TE from a file"
    assert ui_text.ends_readout(wing.OPEN, wing.CLOSED) == "root open · tip closed"
    assert ui_text.END_HINTS[wing.OPEN] == "carries on, as at a centreline"
    assert ui_text.END_HINTS[wing.CLOSED] == "a flat face, moved by an offset"


def test_the_offset_readout_says_none_or_how_far():
    assert ui_text.offset_readout("", "Inward") == "none · the wing itself"
    assert ui_text.offset_readout(" 2.5 ", "Inward") == "2.5 mm inward"


def test_the_guide_counts_come_from_the_wing_itself():
    fractions = wing.SURFACE_GUIDES
    assert ui_text.surface_guide_count(False) == 2 * len(fractions)
    assert ui_text.surface_guide_count(True) == 2 * len(
        [f for f in fractions if f >= wing.OFFSET_GUIDES_FROM]
    )
    assert ui_text.surface_guide_count(False) == 32
    assert ui_text.surface_guide_count(True) == 30


def test_the_offset_hint_follows_the_settings():
    assert ui_text.offset_hint("", export.PROFILES_ENDS) == (
        "Blank exports the wing's own edges and 32 surface guides."
    )
    assert ui_text.offset_hint("2.5", export.PROFILES_ENDS) == (
        "Root and tip go in, held by 30 guides worked out through every section."
    )
    assert "Every section" in ui_text.offset_hint("2.5", export.PROFILES_ALL)


def test_the_export_line_names_what_a_new_wing_will_make():
    runs = ui_text.export_line("wing", 1)
    assert ui_text.plain(runs) == (
        "Export will make wing_le, wing_te and 32 surface guides under Wing Curves."
    )
    assert ("wing_le", ui_text.MONO) in runs and ("Wing Curves", ui_text.STRONG) in runs
    assert ui_text.plain(ui_text.export_line("wing", 2, te_line=True)) == (
        "Export will make wing_2_le, wing_2_te_upper, wing_2_te_lower and 32 surface "
        "guides under Wing Curves."
    )
    assert ui_text.plain(ui_text.export_line("wing_inner", 1, offset=True)) == (
        "Export will make wing_inner's root and tip, wing_inner_le, wing_inner_te and "
        "30 surface guides under Wing Curves."
    )
    assert ui_text.plain(ui_text.export_line("w", 1, offset=True, all_sections=True)) == (
        "Export will make w's sections, w_le and w_te under Wing Curves."
    )
    assert "once it has a name" in ui_text.plain(ui_text.export_line(" ", 1))


def test_the_export_line_for_a_wing_being_edited():
    runs = ui_text.export_line("ignored", 1, editing="wing_inner", live=36)
    assert ui_text.plain(runs) == "Export will update the 36 curves of wing_inner in place."
    assert ("wing_inner", ui_text.MONO) in runs
    assert ui_text.plain(ui_text.export_line("", 1, editing="w")) == (
        "Export will update w in place."
    )


def test_the_result_readout():
    assert ui_text.result_readout("idle") == "not checked yet"
    assert ui_text.result_readout("checked", "36 curves linked") == (
        "checked · 36 curves linked"
    )
    assert ui_text.result_readout("checked") == "checked"
    assert ui_text.result_readout("exporting") == "exporting"
    assert ui_text.result_subtitle(-2.5, False) == "2.5 mm inward · nothing exported"
    assert ui_text.result_subtitle(1.0, True) == "1 mm outward · exported"


def test_the_wall_is_a_range_and_how_far_it_strays():
    assert ui_text.wall_text(2.45, 2.52, -2.5) == (
        "2.45 \u2013 2.52 mm", "as modelled · \u22122.0 % / +0.8 %"
    )


def _fake_build(steep_from=610.0, outside=0, rims=0):
    """What result_rows reads off a WingBuild, and nothing else."""
    sections = [NS(station=NS(rim="")) for _ in range(9 - rims)]
    sections += [NS(station=NS(rim="tip")) for _ in range(rims)]
    offset = NS(
        offset=-2.5, sections=sections, steep_from=steep_from, te_shift=(2.5, 2.6),
        report=NS(thinnest=2.45, thickest=2.52, outside=outside),
    )
    model = NS(sections=[None] * 4, span=1000.0,
               loft=NS(thickness=export.THICKNESS_SCALED))
    curves = [NS(role=export.ROLE_WING_SURFACE)] * 30
    return NS(offset=offset, model=model, curves=curves)


def test_the_checked_block_has_a_row_for_each_thing_found():
    rows = ui_text.result_rows(_fake_build())
    text = {row.label: ui_text.plain(row.runs) for row in rows}
    assert text == {
        "Ribs": "4 over 1000.0 mm of span",
        "Shape between": "airfoil scaled to the chord",
        "Sections": "9, solved in 3D from 610 mm where the edges sweep past 20\u00b0",
        "Trailing edge": "moves 2.5 mm at the root, 2.6 mm at the tip",
        "Wall": "2.45 \u2013 2.52 mm  as modelled · \u22122.0 % / +0.8 %",
    }
    wall = rows[-1]
    assert wall.big and wall.runs[0] == ("2.45 \u2013 2.52 mm", ui_text.STRONG)


def test_a_wall_with_points_outside_says_so_in_red():
    rows = ui_text.result_rows(_fake_build(steep_from=None, outside=3, rims=2))
    assert rows[-1].error and "3 checked points" in ui_text.plain(rows[-1].runs)
    sections = ui_text.plain(rows[2].runs)
    assert sections == "9, 2 of them rounding a closed end"


def test_the_loft_footer_names_the_profiles_and_counts_the_guides():
    runs = ui_text.loft_footer(
        ["wing_inner_root_joined", "wing_inner_tip_joined"],
        ["wing_inner_le", "wing_inner_te"], 30, "wing_inner_loft",
    )
    assert ui_text.plain(runs) == (
        "Loft wing_inner_root_joined and wing_inner_tip_joined with wing_inner_le, "
        "wing_inner_te and the 30 surface guides. Loft does it here as wing_inner_loft."
    )
    assert ("wing_inner_loft", ui_text.MONO) in runs
    many = ui_text.plain(ui_text.loft_footer(["s1", "s2", "s3"], ["le", "te"], 0, "w_loft"))
    assert many.startswith("Loft s1 to s3 (3 sections, in order) with le and te.")


def test_the_export_tracker_counts_its_steps():
    rows = [ui_text.PhaseRow("Work out", "done", ()),
            ui_text.PhaseRow("Send curves", "active", ()),
            ui_text.PhaseRow("Loft", "pending", ())]
    assert ui_text.phase_title(rows) == "step 2 of 3"
    done = [ui_text.PhaseRow("Work out", "done", ())]
    assert ui_text.phase_title(done) == "step 1 of 1"


def test_the_tracker_rows_say_what_each_phase_did():
    assert ui_text.plain(ui_text.work_detail(_fake_build())) == (
        "9 sections · wall 2.45 \u2013 2.52 mm"
    )
    assert ui_text.plain(ui_text.send_detail(36, "WingRib.SLDPRT")) == (
        "36 curves to WingRib.SLDPRT, under Wing Curves"
    )
    assert ui_text.plain(ui_text.send_detail(36, "", pushed=False, folder="D:/W")) == (
        "36 files written to D:/W"
    )
    assert ui_text.plain(ui_text.loft_detail("wing_inner_loft", 2, 32)) == (
        "wing_inner_loft through 2 profiles and 32 guides"
    )
    assert ui_text.plain(ui_text.loft_detail("w_loft")) == (
        "w_loft through its profiles and guides"
    )
