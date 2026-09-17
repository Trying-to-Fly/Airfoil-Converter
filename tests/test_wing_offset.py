"""The whole-wing offset: the wall it leaves, measured in 3D, and the curves
an export of it makes. The hooked-tip wings take a few seconds each."""

import dataclasses
import math
import os

import pytest

from airfoil_converter import export, geometry, store, wing, wing_build, wing_offset
from airfoil_converter.export import InputError
from airfoil_converter.geometry import GeometryError

from conftest import SAMPLE_CSV, SyntheticWing

slow = pytest.mark.slow


def model_of(synthetic, folder=None, **overrides):
    return wing_build.stand_up(synthetic.wing_spec(folder, **overrides), synthetic.sidecar())


def offset_of(model, offset, root=wing.OPEN, tip=wing.CLOSED, check=True):
    return wing_offset.offset_wing(
        model.frame, model.loft, offset, root, tip, model.te_mode, model.te_thickness, check=check
    )


def skin_of(model, offset, root=wing.OPEN, tip=wing.CLOSED):
    return wing_offset.Skin(model.loft, root, tip, step=max(abs(offset) / 6.0, 0.02),
                            frame=model.frame)


def within(report, d, low, high):
    assert report.outside == 0
    assert report.thinnest >= d * (1 - low), report
    assert report.thickest <= d * (1 + high), report


# -- where a flat offset is already right -----------------------------------


def test_an_unswept_wing_is_offset_as_each_rib_would_be(straight_wing):
    model = model_of(straight_wing)
    skin = skin_of(model, -2.0, wing.OPEN, wing.OPEN)
    ours = wing_offset.offset_outline(skin, 150.0, -2.0, exact=False)

    loft = model.loft
    flat = geometry.offset_airfoil(geometry.auto_close(loft.outline(150.0)), -2.0)
    theirs = geometry.clean_loop(flat)
    mine = geometry.clean_loop(ours)
    assert max(geometry.distance_to_loop(p, theirs) for p in mine) < 1e-6
    assert max(geometry.distance_to_loop(p, mine) for p in theirs) < 1e-6


def test_an_unswept_wing_keeps_its_wall(straight_wing):
    result = offset_of(model_of(straight_wing), -2.0, wing.OPEN, wing.OPEN)
    within(result.report, 2.0, 0.005, 0.005)
    assert all(not sec.exact for sec in result.sections)


# -- where it is not --------------------------------------------------------


def test_a_swept_nose_is_offset_further_in_its_own_plane(swept_wing):
    model = model_of(swept_wing)
    flat = offset_of(model, -1.0, wing.OPEN, wing.OPEN, check=False)
    s = 100.0
    mid = min(flat.sections, key=lambda sec: abs(sec.station.station - s))
    nose = wing_offset._to_local(model.loft, mid.station.station, mid.le)[0]
    # A flat 1 mm offset of this nose moves it about 1.08 mm; the skin leans 30°
    # along the span, so the plane has to give it 1/cos 30° of that.
    plain = geometry.offset_airfoil(
        [wing_offset._to_local(model.loft, s, p) for p in model.loft.outline(s)]
        + [wing_offset._to_local(model.loft, s, model.loft.outline(s)[0])],
        -1.0,
    )
    flat_nose = min(x for x, _ in plain)
    assert nose == pytest.approx(flat_nose / math.cos(math.radians(30.0)), rel=0.03)


def test_a_swept_wing_keeps_its_wall(swept_wing):
    result = offset_of(model_of(swept_wing), -1.0, wing.OPEN, wing.OPEN)
    within(result.report, 1.0, 0.025, 0.025)


def test_an_offset_too_deep_is_refused():
    thin = SyntheticWing([0.0, 100.0], lambda s: 0.0, lambda s: -40.0)
    with pytest.raises(GeometryError, match="eats the whole section"):
        offset_of(model_of(thin), -5.0, wing.OPEN, wing.OPEN, check=False)


def test_a_zero_offset_is_refused(straight_wing):
    with pytest.raises(GeometryError, match="other than zero"):
        offset_of(model_of(straight_wing), 0.0)


# -- closed ends ------------------------------------------------------------


def test_an_inward_offset_stops_short_of_a_closed_tip(straight_wing):
    result = offset_of(model_of(straight_wing), -2.0, wing.OPEN, wing.CLOSED, check=False)
    assert result.sections[0].station.station == 0.0
    assert result.sections[-1].station.station == pytest.approx(298.0)


def test_an_outward_offset_rounds_a_closed_tip(straight_wing):
    model = model_of(straight_wing)
    result = offset_of(model, 2.0, wing.OPEN, wing.CLOSED, check=False)
    rims = [sec for sec in result.sections if sec.station.rim]
    assert [round(sec.station.station, 6) for sec in rims] == pytest.approx(
        [300.0 + 2.0 * math.sin(math.radians(t)) for t in wing.RIM_ANGLES]
    )
    # The last one is the tip's own outline, standing one offset past it.
    last = geometry.clean_loop(rims[-1].curves[0][1])
    tip = geometry.clean_loop(model.loft.outline(300.0))
    assert max(geometry.distance_to_loop(p, tip) for p in last) < 1e-6
    # The edge curves run out over the rim too.
    assert result.le.end == pytest.approx(302.0)


# -- the edge curves of the offset wing --------------------------------------


def test_the_offset_edges_pass_through_every_offset_section(hooked_wing, tmp_path):
    result = offset_of(model_of(hooked_wing, tmp_path), -2.0, check=False)
    for sec in result.sections:
        s = sec.station.station
        assert result.le.at(s) == pytest.approx(sec.le, abs=1e-9)
        assert result.te.at(s) == pytest.approx(sec.te, abs=1e-9)


def test_the_trailing_edge_moves_forward_where_the_wall_meets(straight_wing):
    result = offset_of(model_of(straight_wing), -2.0, wing.OPEN, wing.OPEN, check=False)
    root, tip = result.te_shift
    # SD7037 closes to a thin wedge, so a 2 mm wall meets well ahead of the tail.
    assert 15.0 < root < 30.0
    assert root == pytest.approx(tip, abs=1e-6)


def test_blunt_ribs_give_blunt_sections_and_joins(tmp_path):
    synthetic = SyntheticWing([0.0, 300.0], lambda s: 0.0, lambda s: -200.0,
                              te_mode=export.TE_LINE, te_thickness="0.8", keep_chord=True)
    spec = synthetic.wing_spec(offset="2", profiles=export.PROFILES_ALL)
    build = wing_build.build_wing(spec, synthetic.sidecar(), "w", check=True)
    within(build.offset.report, 2.0, 0.01, 0.01)
    sections = [c for c in build.curves if c.role in export.SECTION_HEAD_ROLES]
    lines = [c for c in build.curves if c.role == export.ROLE_SECTION_TE]
    assert len(sections) == len(lines) == len(build.joins)
    assert all(len(line.points) == 2 for line in lines)
    # SD7037's nose is rounder than 2 mm on a 200 mm chord only just: offset
    # 2 mm in, it comes to a corner, so each section goes in two halves.
    sources, name = build.joins[0]
    assert name == "w_s01_joined"
    assert sorted(sources) == ["w_s01_lower", "w_s01_te", "w_s01_upper"]
    assert sources[-1] == "w_s01_te"   # the halves end to end, then the line
    by_name = {c.feature: c.points for c in build.curves}
    first, second = by_name[sources[0]], by_name[sources[1]]
    assert first[-1] == second[0]
    assert build.section_names[0] == "w_s01_joined"
    assert len(build.section_names) == len(build.joins)
    for line in lines:
        a, b = line.points
        assert math.dist(a, b) == pytest.approx(0.8, abs=1e-6)


# -- the hooked tip ---------------------------------------------------------


@slow
def test_a_hooked_tip_keeps_its_wall_inward(hooked_wing, tmp_path):
    result = offset_of(model_of(hooked_wing, tmp_path), -2.0)
    within(result.report, 2.0, 0.03, 0.04)
    assert result.steep_from is not None
    assert result.sections[-1].station.station == pytest.approx(998.0)
    assert len(result.sections) <= wing_offset.MAX_SECTIONS


@slow
def test_a_hooked_tip_keeps_its_wall_outward(hooked_wing, tmp_path):
    result = offset_of(model_of(hooked_wing, tmp_path), 2.0)
    within(result.report, 2.0, 0.03, 0.07)


REAL_WING = os.path.join(
    "/mnt/c/Users/M0obo/OneDrive/Desktop/RC/RC-v2/1 Concept/1.0 References/wing_curves"
)


@slow
@pytest.mark.skipif(not os.path.isdir(REAL_WING), reason="the reference wing is not on this machine")
@pytest.mark.parametrize("thickness,tip_shift", [
    (export.THICKNESS_SCALED, 23.9),
    (export.THICKNESS_BLENDED, 24.3),
])
def test_the_reference_wing_keeps_its_wall(tmp_path, thickness, tip_shift):
    def read(name):
        with open(os.path.join(REAL_WING, name)) as handle:
            return [tuple(map(float, line.split())) for line in handle if line.strip()]

    les = read("wing_LE_loft_stations_starboard_Zfwd_Yup_mm.txt")
    tes = read("wing_TE_loft_stations_starboard_Zfwd_Yup_mm.txt")
    records = []
    for k, (le, te) in enumerate(zip(les, tes)):
        spec = export.ExportSpec(
            export_camber=False, plane_mode="YZ", chord_axis="+Z", up_axis="+Y",
            target_chord=repr(le[2] - te[2]), leading_edge=tuple(repr(c) for c in le),
        )
        records.append(store.ExportRecord(
            export_id=f"exp-{k}", name_index=k + 1, stem="rib",
            source=SAMPLE_CSV, settings=spec.to_dict(),
        ))
    spec = export.WingSpec(
        ribs=tuple(r.export_id for r in records),
        le_source=os.path.join(REAL_WING, "wing_LE_curve_starboard_Zfwd_Yup_mm.txt"),
        te_source=os.path.join(REAL_WING, "wing_TE_curve_starboard_Zfwd_Yup_mm.txt"),
        offset="2",
        thickness=thickness,
    )
    build = wing_build.build_wing(spec, store.Sidecar(exports=records), "w", check=True)
    within(build.offset.report, 2.0, 0.03, 0.04)
    root, tip = build.offset.te_shift
    assert root == pytest.approx(26.5, abs=0.3)
    assert tip == pytest.approx(tip_shift, abs=0.3)


# -- what an export of a wing makes -----------------------------------------


def test_a_wing_without_an_offset_makes_its_edge_curves(hooked_wing, tmp_path):
    spec = hooked_wing.wing_spec(tmp_path)
    build = wing_build.build_wing(spec, hooked_wing.sidecar(), "wing")
    assert [c.feature for c in build.curves][:2] == ["wing_le", "wing_te"]
    # A tracked copy of the file, point for point.
    from airfoil_converter import parser
    assert build.curves[0].points == parser.parse_curve(spec.le_source)
    assert build.offset is None


def test_a_straight_edge_is_two_points(straight_wing):
    build = wing_build.build_wing(straight_wing.wing_spec(), straight_wing.sidecar(), "wing")
    edges = [c for c in build.curves if c.role != export.ROLE_WING_SURFACE]
    assert all(len(c.points) == 2 for c in edges)
    assert build.curves[0].points[0] == pytest.approx((0.0, 0.0, 0.0))
    assert build.curves[0].points[1] == pytest.approx((-300.0, 0.0, 0.0))


def test_an_offset_wing_names_its_sections_root_to_tip(straight_wing):
    spec = straight_wing.wing_spec(offset="1", profiles=export.PROFILES_ALL)
    build = wing_build.build_wing(spec, straight_wing.sidecar(), "inner", index=2)
    names = [c.feature for c in build.curves]
    sections = build.section_names
    # A sharp trailing edge closes the loop; cut at the nose, its halves are joined.
    assert sections[0] == "inner_2_s01_joined"
    assert names[-2:] == ["inner_2_le", "inner_2_te"]
    assert not any(c.role == export.ROLE_WING_SURFACE for c in build.curves)
    assert build.station_count == len(sections)
    assert sorted(build.joins[0][0]) == ["inner_2_s01_lower", "inner_2_s01_upper"]


def test_a_wing_may_not_take_another_records_names(straight_wing):
    sidecar = straight_wing.sidecar()
    sidecar.exports[0].curves.append(store.CurveRecord(role="airfoil", feature="w_le", file=""))
    with pytest.raises(InputError, match="w_le is already"):
        wing_build.build_wing(straight_wing.wing_spec(), sidecar, "w")
    # ...but may keep its own.
    wing_build.build_wing(straight_wing.wing_spec(), sidecar, "w", own_names=["w_le"])


def test_the_result_is_described_in_plain_lines(straight_wing):
    build = wing_build.build_wing(
        straight_wing.wing_spec(offset="2"), straight_wing.sidecar(), "w", check=True
    )
    lines = wing_build.describe(build)
    assert lines[0].startswith("2 ribs over 300.0 mm of span")
    assert any(line.startswith("Wall ") for line in lines)
    assert any("Trailing edge moves" in line for line in lines)


def test_the_planform_is_the_wing_seen_from_above(straight_wing):
    build = wing_build.build_wing(
        straight_wing.wing_spec(offset="2"), straight_wing.sidecar(), "w", check=True
    )
    plan = wing_build.planform(build, samples=10)
    assert (plan.start, plan.end) == pytest.approx((0.0, 300.0))
    assert len(plan.le) == len(plan.te) == 11
    # Unswept and untapered: the edges run straight, a chord apart.
    assert {round(a, 6) for _, a in plan.le} == {round(plan.le[0][1], 6)}
    assert abs(plan.te[0][1] - plan.le[0][1]) == pytest.approx(200.0)
    assert [s for s, _, _ in plan.ribs] == pytest.approx([0.0, 300.0])
    # The offset sections stand inside the chord, and none past the tip.
    assert plan.sections
    low, high = sorted((plan.le[0][1], plan.te[0][1]))
    for s, a, b in plan.sections:
        assert 0.0 <= s <= 300.0
        assert low < a < high and low < b < high


def test_the_result_panel_reads_the_build_itself(straight_wing):
    from airfoil_converter import ui_text

    build = wing_build.build_wing(
        straight_wing.wing_spec(offset="2"), straight_wing.sidecar(), "w", check=True
    )
    rows = {row.label: ui_text.plain(row.runs) for row in ui_text.result_rows(build)}
    assert rows["Ribs"] == "2 over 300.0 mm of span"
    assert rows["Shape between"] == "thickness blended root to tip"
    assert rows["Sections"].startswith(f"{len(build.offset.sections)}")
    assert rows["Wall"].startswith(f"{build.offset.report.thinnest:.2f} \u2013 ")
    detail = ui_text.plain(ui_text.work_detail(build))
    assert detail.startswith(f"{len(build.offset.sections)} sections · wall ")

    plain = wing_build.build_wing(straight_wing.wing_spec(), straight_wing.sidecar(), "w")
    lines = [ui_text.plain(row.runs) for row in ui_text.result_rows(plain)]
    assert lines == wing_build.describe(plain)
    assert ui_text.plain(ui_text.work_detail(plain)) == "2 ribs · 32 surface guides"


# -- ribs that lean with the dihedral ----------------------------------------


def test_leaning_ribs_stand_on_their_own_planes(dihedral_wing):
    model = model_of(dihedral_wing)
    frame = model.frame
    tip = dihedral_wing.point(600.0, 0.0)
    normal = frame.normals[-1]
    along = (-math.cos(math.radians(5.0)), math.sin(math.radians(5.0)), 0.0)
    assert abs(sum(a * b for a, b in zip(normal, along))) == pytest.approx(1.0)
    # Stations run along the line square to the root; the leaning tip crosses
    # it a little further out than its own leading edge stands.
    assert frame.stations[-1] == pytest.approx(600.0 / math.cos(math.radians(5.0)) ** 2)
    # The loft between runs through the ribs where they stand.
    for sec in model.sections:
        for p in sec.outline[::10]:
            s, a, b = frame.to_wing(frame.to_3d(sec.station, p))
            assert s == pytest.approx(sec.station, abs=1e-9)
            assert (a, b) == pytest.approx(p, abs=1e-9)
    last = model.sections[-1]
    assert frame.to_3d(last.station, last.le) == pytest.approx(tip, abs=1e-9)


def test_a_rib_square_to_the_dihedral_is_offset_flat(dihedral_wing):
    """Past the root the wing is a prism along the dihedral line, so its true
    offset, cut square to that line, is just the section offset flat."""
    model = model_of(dihedral_wing)
    skin = skin_of(model, -2.0, wing.OPEN, wing.OPEN)
    ours = geometry.clean_loop(wing_offset.offset_outline(skin, 450.0, -2.0, exact=False))
    theirs = geometry.clean_loop(
        geometry.offset_airfoil(geometry.auto_close(model.loft.outline(450.0)), -2.0)
    )
    assert max(geometry.distance_to_loop(p, theirs) for p in ours) < 2e-3
    assert max(geometry.distance_to_loop(p, ours) for p in theirs) < 2e-3


def test_a_dihedral_wing_keeps_its_wall(dihedral_wing):
    result = offset_of(model_of(dihedral_wing), -2.0, wing.OPEN, wing.CLOSED)
    within(result.report, 2.0, 0.01, 0.01)


def test_an_offset_leaning_rib_stays_on_its_plane(dihedral_wing):
    spec = dihedral_wing.wing_spec(offset="2")
    build = wing_build.build_wing(spec, dihedral_wing.sidecar(), "w")
    frame = build.model.frame
    for curve in build.curves:
        if curve.role != export.ROLE_SECTION:
            continue
        stations = {round(frame.locate(p), 6) for p in curve.points}
        assert len(stations) == 1


def test_ribs_whose_planes_cross_are_refused():
    # A rib some 15 mm tall, leaning 20°, reaches 5 mm across: through a rib 1 mm off.
    close = SyntheticWing([0.0, 1.0], lambda s: 0.0, lambda s: -200.0,
                          lean=lambda s: 20.0 if s else 0.0)
    with pytest.raises(GeometryError, match="cross inside the wing"):
        model_of(close)


def test_ribs_may_lean_only_so_far():
    steep = SyntheticWing([0.0, 500.0], lambda s: 0.0, lambda s: -200.0,
                          lean=lambda s: 40.0 if s else 0.0)
    with pytest.raises(GeometryError, match="leans 40.0°"):
        model_of(steep)


@slow
def test_a_hooked_tip_leaning_with_its_dihedral_keeps_its_wall(hooked_leaning_wing, tmp_path):
    result = offset_of(model_of(hooked_leaning_wing, tmp_path), -2.0)
    within(result.report, 2.0, 0.03, 0.04)
    assert result.steep_from is not None


# -- a blunt trailing edge gets an edge along each corner ---------------------


def blunt_wing(**kw):
    return SyntheticWing([0.0, 300.0], lambda s: 0.0, lambda s: -200.0,
                         te_mode=export.TE_LINE, te_thickness="0.8", keep_chord=True, **kw)


def rib_te_ends(synthetic):
    """Each rib's own trailing-edge line, as the Airfoil tab exports it."""
    from airfoil_converter import parser

    data = parser.parse_csv(SAMPLE_CSV)
    ends = []
    for s in synthetic.stations:
        curves = export.build_curves(data, synthetic.spec(s), "rib")
        line = next(c for c in curves if c.role == export.ROLE_TE)
        ends.append(sorted(line.points, key=lambda p: -p[1]))  # upper first
    return ends


def test_a_blunt_wing_has_an_edge_along_each_corner():
    synthetic = blunt_wing()
    build = wing_build.build_wing(synthetic.wing_spec(), synthetic.sidecar(), "w")
    by_name = {c.feature: c.points for c in build.curves if c.role != export.ROLE_WING_SURFACE}
    assert set(by_name) == {"w_le", "w_te_upper", "w_te_lower"}
    (root_up, root_low), (tip_up, tip_low) = rib_te_ends(synthetic)
    assert by_name["w_te_upper"] == pytest.approx([root_up, tip_up], abs=1e-9)
    assert by_name["w_te_lower"] == pytest.approx([root_low, tip_low], abs=1e-9)


def test_a_trailing_edge_file_off_the_corners_still_places_them(tmp_path):
    """The file runs along the chord line, a little below both corners: it only
    says where the trailing edge runs, and the corners come from the ribs."""
    synthetic = blunt_wing()
    spec = synthetic.wing_spec(tmp_path)
    build = wing_build.build_wing(spec, synthetic.sidecar(), "w")
    by_name = {c.feature: c.points for c in build.curves}
    (root_up, root_low), (tip_up, tip_low) = rib_te_ends(synthetic)
    for name, root, tip in (("w_te_upper", root_up, tip_up), ("w_te_lower", root_low, tip_low)):
        assert by_name[name][0] == pytest.approx(root, abs=1e-9)
        assert by_name[name][-1] == pytest.approx(tip, abs=1e-9)
        # An untapered wing's corners run straight.
        mid = by_name[name][len(by_name[name]) // 2]
        assert mid[1] == pytest.approx(root[1], abs=1e-6)


def test_a_trailing_edge_file_that_misses_the_edge_line_is_refused(tmp_path):
    synthetic = blunt_wing()
    spec = synthetic.wing_spec(tmp_path)
    from airfoil_converter import parser, writer

    points = [(x, y, z + 0.5) for x, y, z in parser.parse_curve(spec.te_source)]
    writer.write_curve(spec.te_source, points)
    with pytest.raises(GeometryError, match="misses"):
        wing_build.build_wing(spec, synthetic.sidecar(), "w")


def test_an_offset_blunt_wing_has_an_edge_along_each_offset_corner():
    synthetic = blunt_wing()
    spec = synthetic.wing_spec(offset="2", profiles=export.PROFILES_ALL)
    build = wing_build.build_wing(spec, synthetic.sidecar(), "w")
    by_name = {c.feature: c.points for c in build.curves}
    assert "w_te" not in by_name
    upper, lower = by_name["w_te_upper"], by_name["w_te_lower"]
    for name, points in by_name.items():
        if not name.endswith("_te") or name == "w_te":
            continue
        top, bottom = sorted(points, key=lambda p: -p[1])
        assert min(math.dist(top, p) for p in upper) < 1e-9
        assert min(math.dist(bottom, p) for p in lower) < 1e-9


# -- a loft through the root and tip alone -----------------------------------


def test_root_and_tip_only_exports_two_profiles_and_surface_guides(hooked_wing, tmp_path):
    spec = hooked_wing.wing_spec(tmp_path, offset="2", profiles=export.PROFILES_ENDS)
    build = wing_build.build_wing(spec, hooked_wing.sidecar(), "w")
    names = [c.feature for c in build.curves]
    fractions = [f for f in wing.SURFACE_GUIDES if f >= wing.OFFSET_GUIDES_FROM]
    guides = ["w_" + wing.guide_tag(side, f) for side in ("upper", "lower") for f in fractions]
    # A sharp SD7037 offset 2 mm in comes to a corner at its nose, so each end
    # goes in two halves, joined.
    ends = [n for n in names if n.startswith(("w_root_", "w_tip_"))]
    assert sorted(ends) == ["w_root_lower", "w_root_upper", "w_tip_lower", "w_tip_upper"]
    assert names == ends + ["w_le", "w_te"] + guides
    assert build.section_names == ["w_root_joined", "w_tip_joined"]
    assert build.station_count == 2
    assert len(build.offset.sections) > 2   # still worked out through all of them
    profiles = {c.feature: c.points for c in build.curves}
    profiles["w_root"] = profiles["w_root_upper"] + profiles["w_root_lower"]
    profiles["w_tip"] = profiles["w_tip_upper"] + profiles["w_tip_lower"]
    for curve in build.curves:
        if curve.role != export.ROLE_WING_SURFACE:
            continue
        # Each guide lands on a point of each end profile, which is what lets
        # the loft take it.
        assert min(math.dist(curve.points[0], p) for p in profiles["w_root"]) < 1e-9
        assert min(math.dist(curve.points[-1], p) for p in profiles["w_tip"]) < 1e-9


def test_surface_guides_follow_the_offset_wing_between_its_ends(straight_wing):
    spec = straight_wing.wing_spec(offset="2", profiles=export.PROFILES_ENDS)
    build = wing_build.build_wing(spec, straight_wing.sidecar(), "w", check=True)
    frame = build.model.frame
    inner = build.offset.loft()
    guides = {c.feature: c.points for c in build.curves if c.role == export.ROLE_WING_SURFACE}
    upper, lower = guides["w_upper_30"], guides["w_lower_30"]
    for a, b in zip(upper, lower):
        assert a[1] > b[1]  # the upper guide runs over the top
    # Between the ends each point lies on the offset wing's own surface.
    for point in upper[1:-1]:
        s, x, y = frame.to_wing(point)
        outline = geometry.clean_loop(inner.outline(s))
        assert geometry.distance_to_loop((x, y), outline) < 0.05


def test_every_exported_point_stands_clear_of_the_last(hooked_wing, tmp_path):
    spec = hooked_wing.wing_spec(tmp_path, offset="2")
    build = wing_build.build_wing(spec, hooked_wing.sidecar(), "w")
    for curve in build.curves:
        steps = [math.dist(a, b) for a, b in zip(curve.points, curve.points[1:])]
        assert min(steps) >= geometry.MIN_STEP - 1e-9, curve.feature



# -- the wing itself, held to its shape --------------------------------------


def test_the_wings_own_guides_land_on_every_rib(hooked_wing, tmp_path):
    build = wing_build.build_wing(hooked_wing.wing_spec(tmp_path), hooked_wing.sidecar(), "w")
    from airfoil_converter import parser

    data = parser.parse_csv(SAMPLE_CSV)
    # Each rib as SolidWorks draws it: the spline through its exported points.
    ribs = [wing.as_drawn(export.build_curves(data, hooked_wing.spec(s), "rib")[0].points)
            for s in hooked_wing.stations]
    guides = [c for c in build.curves if c.role == export.ROLE_WING_SURFACE]
    assert len(guides) == 2 * len(wing.SURFACE_GUIDES)
    for guide in guides:
        for rib in ribs:
            assert min(math.dist(p, q) for p in guide.points for q in rib) < 1e-9, guide.feature


@pytest.mark.parametrize("thickness", export.THICKNESS_CHOICES)
def test_the_thickness_rule_decides_the_shape_between_ribs(thickness, tmp_path):
    # The leading edge curves back, so the chord does not taper in a straight
    # line — which is where the two rules part.
    curved = SyntheticWing([0.0, 500.0], lambda s: -0.0004 * s * s, lambda s: -300.0)
    spec = dataclasses.replace(
        curved.wing_spec(thickness=thickness), le_source=curved.edge_file(tmp_path, "le")
    )
    loft = wing_build.stand_up(spec, curved.sidecar()).loft
    assert loft.chord(250.0) == pytest.approx(275.0, abs=0.01)

    def thickness_at(s):
        ys = [loft.local_point(s, i)[1] * loft.chord(s) for i in range(loft.count)]
        return max(ys) - min(ys)

    root, tip, mid = thickness_at(0.0), thickness_at(500.0), thickness_at(250.0)
    straight_across = 0.5 * (root + tip)
    in_proportion = root / loft.chord(0.0) * loft.chord(250.0)
    assert abs(straight_across - in_proportion) > 1.0
    if thickness == export.THICKNESS_BLENDED:
        assert mid == pytest.approx(straight_across, rel=1e-3)
    else:
        assert mid == pytest.approx(in_proportion, rel=1e-3)


# -- cutting a section at its nose --------------------------------------------


def pointed_nose():
    # A nose come to a point at (0, 0), upper surface first.
    upper = [(100.0 - x, 5.0 * (1 - (1 - x / 100.0) ** 2)) for x in range(0, 101, 5)]
    lower = [(float(x), -3.0 * (1 - (1 - x / 100.0) ** 2)) for x in range(5, 101, 5)]
    return upper, lower


def test_a_section_with_a_corner_is_cut_at_the_corner():
    upper, lower = pointed_nose()
    # Told its nose is elsewhere: the corner wins.
    pieces = wing_build.split_at_nose(upper + lower, False, (0.0, 1.0), (30.0, 3.0))
    assert [role for role, _, _ in pieces] == [export.ROLE_SECTION_UPPER, export.ROLE_SECTION_LOWER]
    (_, top, _), (_, bottom, _) = pieces
    assert top == upper and bottom == [upper[-1]] + lower


def test_the_halves_are_named_by_which_way_is_up():
    upper, lower = pointed_nose()
    pieces = wing_build.split_at_nose(upper + lower, False, (0.0, -1.0), (0.0, 0.0))
    # Still in the outline's order, so they join end to end.
    assert [role for role, _, _ in pieces] == [export.ROLE_SECTION_LOWER, export.ROLE_SECTION_UPPER]
    assert pieces[1][1] == [upper[-1]] + lower


def test_a_smooth_section_is_cut_at_its_nose_too():
    """A loft will not join profiles cut into different numbers of pieces."""
    loop = [(50.0 + 50.0 * math.cos(math.radians(a)), 10.0 * math.sin(math.radians(a)))
            for a in range(0, 361, 5)]
    pieces = wing_build.split_at_nose(loop, True, (0.0, 1.0), (0.0, 0.0))
    assert [role for role, _, _ in pieces] == [export.ROLE_SECTION_UPPER, export.ROLE_SECTION_LOWER]
    assert pieces[0][1][-1] == pieces[1][1][0] == loop[36]


def test_every_section_of_an_offset_wing_comes_in_the_same_pieces(tmp_path):
    synthetic = SyntheticWing([0.0, 300.0], lambda s: 0.0, lambda s: -200.0,
                              te_mode=export.TE_LINE, te_thickness="0.8")
    for direction in (export.OFFSET_OUTWARD, export.OFFSET_INWARD):
        spec = synthetic.wing_spec(offset="2", offset_dir=direction)
        build = wing_build.build_wing(spec, synthetic.sidecar(), "w")
        assert [sorted(s) for s, _ in build.joins] == [
            ["w_root_lower", "w_root_te", "w_root_upper"],
            ["w_tip_lower", "w_tip_te", "w_tip_upper"],
        ]


def test_a_hairpin_left_by_the_trim_is_taken_out():
    # A nose corner at (0, 0), turning about 100°, with a point 0.07 mm past it
    # that the outline runs out to and straight back from.
    loop = [(100.0, 5.0), (3.0, 4.0), (-0.042, -0.056), (0.0, 0.0), (3.0, -3.0), (100.0, -1.0)]
    front = lambda p: p[0] < 50.0
    cleaned = wing_offset._despike(loop, front)
    assert cleaned == [(100.0, 5.0), (3.0, 4.0), (0.0, 0.0), (3.0, -3.0), (100.0, -1.0)]
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert wing_offset._despike(square, front) == square
    # A sharp tail turns back on itself too, and is left alone.
    tail = [(0.0, 5.0), (99.0, 1.0), (100.0, 0.0), (99.0, -1.0), (0.0, -5.0)]
    assert wing_offset._despike(tail, front) == tail
