"""A wing stood up from its ribs: axes, edges, the loft between, and where the
offset wing's sections go. No window, no SolidWorks."""

import dataclasses
import math

import pytest

from airfoil_converter import export, geometry, parser, store, wing, wing_build, writer
from airfoil_converter.export import InputError
from airfoil_converter.geometry import GeometryError

from conftest import SAMPLE_CSV, SyntheticWing


def ribs_of(synthetic):
    data = parser.parse_csv(SAMPLE_CSV)
    return [wing.rib_from_record(r, data) for r in synthetic.records()]


def model_of(synthetic, folder=None, **overrides):
    return wing_build.stand_up(synthetic.wing_spec(folder, **overrides), synthetic.sidecar())


# -- the spline -------------------------------------------------------------


def test_a_spline_is_exact_on_a_straight_line():
    spline = wing.Spline([0.0, 1.0, 3.0, 7.0], [1.0, 3.0, 7.0, 15.0])
    assert spline(5.0) == pytest.approx(11.0)
    assert spline.slope(2.0) == pytest.approx(2.0)


def test_a_spline_follows_a_smooth_curve():
    xs = [k * 0.1 for k in range(64)]
    spline = wing.Spline(xs, [math.sin(x) for x in xs])
    for x in (0.55, 2.02, 4.4):
        assert spline(x) == pytest.approx(math.sin(x), abs=1e-4)
        assert spline.slope(x) == pytest.approx(math.cos(x), abs=1e-3)


def test_a_spline_refuses_points_that_do_not_advance():
    with pytest.raises(GeometryError, match="advance"):
        wing.Spline([0.0, 1.0, 1.0], [0.0, 1.0, 2.0])


# -- ribs -------------------------------------------------------------------


def test_a_rib_is_its_exported_outline_without_its_own_offset(straight_wing):
    record = straight_wing.records()[0]
    spec = dataclasses.replace(record.spec, offset="2")
    record.settings = spec.to_dict()
    data = parser.parse_csv(SAMPLE_CSV)
    rib = wing.rib_from_record(record, data)
    plain = export.build_curves(data, dataclasses.replace(spec, offset=""), "rib")
    assert rib.outline == plain[0].points


def test_an_adopted_rib_is_refused(straight_wing):
    record = straight_wing.records()[0]
    record.adopted, record.settings = True, {}
    with pytest.raises(GeometryError, match="no settings"):
        wing.rib_from_record(record, parser.parse_csv(SAMPLE_CSV))


def test_split_ribs_are_refused():
    synthetic = SyntheticWing([0.0, 100.0], lambda s: 0.0, lambda s: -150.0,
                              te_mode=export.TE_SPLIT)
    with pytest.raises(GeometryError, match="upper and lower"):
        ribs_of(synthetic)


def test_blunt_ribs_left_open_are_refused():
    synthetic = SyntheticWing([0.0, 100.0], lambda s: 0.0, lambda s: -150.0,
                              te_mode=export.TE_OPEN, te_thickness="0.5")
    with pytest.raises(GeometryError, match="TE line"):
        ribs_of(synthetic)


def test_ribs_must_finish_their_trailing_edges_alike(straight_wing):
    ribs = ribs_of(straight_wing)
    ribs[1].spec = dataclasses.replace(ribs[1].spec, te_thickness="0.4")
    with pytest.raises(GeometryError, match="differently"):
        wing.check_ribs_agree(ribs)


# -- the wing's axes --------------------------------------------------------


def test_the_root_is_the_rib_nearer_the_origin(hooked_wing):
    ribs = list(reversed(ribs_of(hooked_wing)))
    frame, stations = wing.wing_frame(ribs)
    assert frame.origin == pytest.approx(ribs[-1].le)
    assert stations == pytest.approx([1000.0, 900.0, 500.0, 0.0], abs=1e-6)
    assert frame.span == pytest.approx((-1.0, 0.0, 0.0))


def test_the_ends_can_be_swapped(hooked_wing):
    frame, stations = wing.wing_frame(ribs_of(hooked_wing), root_at_origin=False)
    assert frame.span == pytest.approx((1.0, 0.0, 0.0))
    assert stations == pytest.approx([1000.0, 500.0, 100.0, 0.0], abs=1e-6)


def test_ribs_leaning_too_far_apart_are_refused(straight_wing):
    ribs = ribs_of(straight_wing)
    ribs[1].u, ribs[1].v = (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    with pytest.raises(GeometryError, match="leans 90.0°"):
        wing.wing_frame(ribs)


def test_two_ribs_at_one_station_are_refused():
    synthetic = SyntheticWing([0.0, 0.0], lambda s: 0.0, lambda s: -150.0)
    with pytest.raises(GeometryError, match="distinct"):
        wing.wing_frame(ribs_of(synthetic))


def test_placed_ribs_run_root_to_tip_with_their_edge_points(hooked_wing):
    ribs = ribs_of(hooked_wing)
    frame, _ = wing.wing_frame(ribs)
    sections = wing.place_ribs(list(reversed(ribs)), frame)
    assert [s.station for s in sections] == pytest.approx([0, 500, 900, 1000], abs=1e-6)
    tip = sections[-1]
    chord = hooked_wing.le_z(1000.0) - hooked_wing.te_z(1000.0)
    assert math.hypot(tip.te[0] - tip.le[0], tip.te[1] - tip.le[1]) == pytest.approx(chord, rel=1e-6)


# -- edge curves ------------------------------------------------------------


def test_an_edge_curve_is_read_whichever_way_it_runs(hooked_wing, tmp_path):
    ribs = ribs_of(hooked_wing)
    frame, _ = wing.wing_frame(ribs)
    points = parser.parse_curve(hooked_wing.edge_file(tmp_path, "le"))
    forward = wing.Guide.from_points(frame, points, "leading edge")
    backward = wing.Guide.from_points(frame, list(reversed(points)), "leading edge")
    assert forward.at(733.0) == pytest.approx(backward.at(733.0))


def test_an_edge_curve_that_doubles_back_is_refused(straight_wing):
    frame, _ = wing.wing_frame(ribs_of(straight_wing))
    points = [(0.0, 0.0, 0.0), (-50.0, 0.0, 0.0), (-40.0, 0.0, 0.0), (-300.0, 0.0, 0.0)]
    with pytest.raises(GeometryError, match="doubles back"):
        wing.Guide.from_points(frame, points, "leading edge")


def test_a_straight_edge_past_a_rib_off_the_line_says_to_use_a_file(hooked_wing):
    with pytest.raises(GeometryError, match="curve file instead"):
        model_of(hooked_wing)


def test_an_edge_curve_must_reach_both_end_ribs(hooked_wing, tmp_path):
    spec = hooked_wing.wing_spec(tmp_path)
    ribs = hooked_wing.records()
    short = SyntheticWing([0.0, 800.0], hooked_wing.le_z, hooked_wing.te_z, hooked_wing.le_y)
    path = short.edge_file(tmp_path / "..", "le")
    spec = dataclasses.replace(spec, le_source=path)
    with pytest.raises(GeometryError, match="reach both end ribs"):
        wing_build.stand_up(spec, store.Sidecar(exports=ribs))


def test_an_edge_curve_that_misses_a_rib_is_refused(hooked_wing, tmp_path):
    spec = hooked_wing.wing_spec(tmp_path)
    moved = parser.parse_curve(spec.le_source)
    moved = [(x, y, z + 0.5) for x, y, z in moved]
    writer.write_curve(spec.le_source, moved)
    with pytest.raises(GeometryError, match="misses rib by 0.500 mm"):
        wing_build.stand_up(spec, hooked_wing.sidecar())


def test_a_trailing_edge_line_may_be_crossed_anywhere_along_it(tmp_path):
    synthetic = SyntheticWing([0.0, 300.0], lambda s: 0.0, lambda s: -200.0,
                              te_mode=export.TE_LINE, te_thickness="0.8", keep_chord=True)
    # The line stands a little above the chord; a TE curve through its lower
    # corner still crosses it.
    model = wing_build.stand_up(synthetic.wing_spec(), synthetic.sidecar())
    root = model.sections[0]
    low = root.outline[-1]
    assert math.hypot(root.te[0] - low[0], root.te[1] - low[1]) > 0.1

    guide = wing.Guide.straight(0.0, low, 300.0, model.sections[-1].outline[-1])
    wing.check_guide(guide, model.sections, "trailing edge", "te", te_line=True)
    assert root.te == pytest.approx(low)


# -- the loft ---------------------------------------------------------------


def test_the_loft_passes_through_its_sections(hooked_wing, tmp_path):
    model = model_of(hooked_wing, tmp_path)
    loft = model.loft
    for sec in model.sections:
        outline = loft.outline(sec.station)
        # The nose sample is the rib's own point nearest its leading edge; an
        # airfoil file need not hold the leading edge itself as a point.
        nearest = min(sec.outline, key=lambda p: math.hypot(p[0] - sec.le[0], p[1] - sec.le[1]))
        assert outline[wing.SAMPLES] == pytest.approx(nearest, abs=1e-6)
        assert outline[0] == pytest.approx(sec.te, abs=1e-6)
        loop = geometry.clean_loop(sec.outline)
        assert max(geometry.distance_to_loop(p, loop) for p in outline) < 1e-6


def test_the_loft_follows_the_edges_between_ribs(hooked_wing, tmp_path):
    model = model_of(hooked_wing, tmp_path)
    loft = model.loft
    le, u, _ = loft.axes(700.0)
    placed = model.frame.to_3d(700.0, le)
    assert placed == pytest.approx(hooked_wing.point(700.0, hooked_wing.le_z(700.0)), abs=1e-3)
    assert loft.chord(700.0) == pytest.approx(
        hooked_wing.le_z(700.0) - hooked_wing.te_z(700.0), abs=1e-3
    )


def test_sweep_is_read_off_the_edges(swept_wing):
    loft = model_of(swept_wing).loft
    assert loft.sweep(100.0) == pytest.approx(30.0, abs=1e-6)


# -- where the offset wing's sections go -------------------------------------


def test_sections_are_never_further_apart_than_the_limit(straight_wing):
    loft = model_of(straight_wing).loft
    stations = [st.station for st in wing.plan_stations(loft, -2.0, wing.OPEN, wing.OPEN)]
    assert stations[0] == 0.0 and stations[-1] == 300.0
    assert max(b - a for a, b in zip(stations, stations[1:])) <= wing.MAX_SPACING


def test_sections_crowd_where_the_edge_turns(hooked_wing, tmp_path):
    loft = model_of(hooked_wing, tmp_path).loft
    stations = [st.station for st in wing.plan_stations(loft, -2.0, wing.OPEN, wing.CLOSED)]
    near_tip = [s for s in stations if s > 950.0]
    assert len(near_tip) >= 4


def test_a_closed_end_moves_in_with_an_inward_offset(hooked_wing, tmp_path):
    loft = model_of(hooked_wing, tmp_path).loft
    stations = [st.station for st in wing.plan_stations(loft, -2.0, wing.OPEN, wing.CLOSED)]
    assert stations[0] == 0.0
    assert stations[-1] == pytest.approx(998.0)


def test_the_offset_does_not_move_the_sections_between(hooked_wing, tmp_path):
    loft = model_of(hooked_wing, tmp_path).loft
    one = [st.station for st in wing.plan_stations(loft, -1.0, wing.OPEN, wing.CLOSED)]
    two = [st.station for st in wing.plan_stations(loft, -2.0, wing.OPEN, wing.CLOSED)]
    assert [s for s in one if s < 990.0] == [s for s in two if s < 990.0]


def test_a_closed_end_grown_outward_is_rounded_by_rim_sections(straight_wing):
    loft = model_of(straight_wing).loft
    plan = wing.plan_stations(loft, 2.0, wing.CLOSED, wing.CLOSED)
    rims = [st for st in plan if st.rim]
    assert [st.rim for st in rims] == ["root"] * 3 + ["tip"] * 3
    assert plan[0].station == pytest.approx(-2.0)
    assert plan[-1].station == pytest.approx(302.0)
    assert plan[-1].rim_offset == pytest.approx(0.0, abs=1e-12)
    assert plan[-3].rim_offset == pytest.approx(2.0 * math.cos(math.radians(30.0)))


def test_an_inward_offset_from_two_closed_ends_can_eat_the_span():
    synthetic = SyntheticWing([0.0, 3.0], lambda s: 0.0, lambda s: -100.0)
    loft = model_of(synthetic).loft
    with pytest.raises(GeometryError, match="leaves no span"):
        wing.plan_stations(loft, -2.0, wing.CLOSED, wing.CLOSED)


# -- what a wing needs to be built ------------------------------------------


def test_a_wing_needs_two_ribs(straight_wing):
    spec = dataclasses.replace(straight_wing.wing_spec(), ribs=("exp-0",))
    with pytest.raises(InputError, match="at least two ribs"):
        wing_build.stand_up(spec, straight_wing.sidecar())


def test_a_rib_gone_from_the_record_is_reported(straight_wing):
    spec = dataclasses.replace(straight_wing.wing_spec(), ribs=("exp-0", "exp-9"))
    with pytest.raises(InputError, match="no longer"):
        wing_build.stand_up(spec, straight_wing.sidecar())


def test_a_rib_whose_source_moved_is_reported(straight_wing, tmp_path):
    sidecar = straight_wing.sidecar()
    sidecar.exports[1].source = str(tmp_path / "gone.csv")
    with pytest.raises(InputError, match="not where it was"):
        wing_build.stand_up(straight_wing.wing_spec(), sidecar)
