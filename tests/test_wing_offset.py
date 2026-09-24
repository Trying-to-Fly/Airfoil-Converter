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
    spec = synthetic.wing_spec(offset="2")
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


def test_a_wing_without_an_offset_is_its_sections_and_guides_through_them(hooked_wing, tmp_path):
    """Lofted through its two ribs alone, SolidWorks' loft sagged up to 0.08 mm
    off the wing between its guides; through its own sections it did not."""
    spec = hooked_wing.wing_spec(tmp_path)
    build = wing_build.build_wing(spec, hooked_wing.sidecar(), "wing")
    assert build.offset is None
    assert build.station_count > 2
    # A sharp trailing edge: each section is one curve closed on itself, as the
    # rib is, so there is nothing to join.
    assert build.section_names[0] == "wing_s01" and not build.joins
    sections = section_points(build)
    guides = [c for c in build.curves if c.role in export.WING_EDGE_ROLES]
    assert {c.role for c in guides} >= {export.ROLE_WING_LE, export.ROLE_WING_SURFACE}
    for guide in guides:
        for number, points in sections.items():
            # Each guide meets every section at a point of its own.
            assert min(math.dist(p, q) for p in guide.points for q in points) < 1e-9, (
                guide.feature, number)
    # The leading edge still follows the curve file it was drawn from, to
    # within what a rib may miss that file by.
    from airfoil_converter import parser
    drawn = parser.parse_curve(spec.le_source)
    le = next(c for c in guides if c.role == export.ROLE_WING_LE)
    assert max(to_polyline(p, drawn) for p in le.points[::10]) < wing.EDGE_TOL


def to_polyline(p, line):
    """How far a point stands from a polyline in 3D."""
    best = math.inf
    for a, b in zip(line, line[1:]):
        ab = [b[k] - a[k] for k in range(3)]
        size = sum(c * c for c in ab) or 1e-30
        t = max(0.0, min(1.0, sum((p[k] - a[k]) * ab[k] for k in range(3)) / size))
        best = min(best, math.dist(p, [a[k] + t * ab[k] for k in range(3)]))
    return best


def section_points(build):
    """Every exported section's points, by section number."""
    out = {}
    for c in build.curves:
        if c.role in (export.ROLE_SECTION_UPPER, export.ROLE_SECTION_LOWER, export.ROLE_SECTION):
            number = c.feature.split("_s")[-1].split("_")[0]
            out.setdefault(number, []).extend(c.points)
    return out

def test_a_straight_edge_runs_straight(straight_wing):
    build = wing_build.build_wing(straight_wing.wing_spec(), straight_wing.sidecar(), "wing")
    # The leading-edge guide follows the sections' nose point, which is the
    # rib's own point nearest its leading edge: on the line, to within that.
    le = next(c for c in build.curves if c.role == export.ROLE_WING_LE).points
    assert le[0][0] == pytest.approx(0.0, abs=1e-6)
    assert le[-1][0] == pytest.approx(-300.0, abs=1e-6)
    assert all(math.hypot(p[1], p[2]) < wing.EDGE_TOL for p in le)
    # An untapered wing's does not wander: the same offset all the way along.
    assert max(p[1] for p in le) - min(p[1] for p in le) < 1e-6

def test_an_offset_wing_names_its_sections_root_to_tip(straight_wing):
    spec = straight_wing.wing_spec(offset="1")
    build = wing_build.build_wing(spec, straight_wing.sidecar(), "inner", index=2)
    names = [c.feature for c in build.curves]
    sections = build.section_names
    # A sharp trailing edge closes the loop; cut at the nose, its halves are joined.
    assert sections[0] == "inner_2_s01_joined"
    assert {"inner_2_le", "inner_2_te", "inner_2_upper_50", "inner_2_lower_50"} <= set(names)
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
    by_name = {c.feature: c.points for c in build.curves
               if c.role in export.WING_EDGE_ROLES and c.role != export.ROLE_WING_SURFACE}
    assert set(by_name) == {"w_le", "w_te_upper", "w_te_lower"}
    (root_up, root_low), (tip_up, tip_low) = rib_te_ends(synthetic)
    for name, root, tip in (("w_te_upper", root_up, tip_up), ("w_te_lower", root_low, tip_low)):
        assert by_name[name][0] == pytest.approx(root, abs=1e-6)
        assert by_name[name][-1] == pytest.approx(tip, abs=1e-6)
    # Each section goes in as a rib does: one curve round the nose, and the
    # edge line, joined.
    assert all(len(sources) == 2 for sources, _ in build.joins)
    assert build.joins[0] == (("w_s01", "w_s01_te"), "w_s01_joined")

def test_a_trailing_edge_file_off_the_corners_still_places_them(tmp_path):
    """The file runs along the chord line, a little below both corners: it only
    says where the trailing edge runs, and the corners come from the ribs."""
    synthetic = blunt_wing()
    spec = synthetic.wing_spec(tmp_path)
    build = wing_build.build_wing(spec, synthetic.sidecar(), "w")
    by_name = {c.feature: c.points for c in build.curves}
    (root_up, root_low), (tip_up, tip_low) = rib_te_ends(synthetic)
    for name, root, tip in (("w_te_upper", root_up, tip_up), ("w_te_lower", root_low, tip_low)):
        # The wing's own corner, which is the rib's to within where the file
        # puts the trailing edge's line through it.
        assert by_name[name][0] == pytest.approx(root, abs=0.01)
        assert by_name[name][-1] == pytest.approx(tip, abs=0.01)
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
    spec = synthetic.wing_spec(offset="2")
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




def test_surface_guides_follow_the_offset_wing_between_its_sections(straight_wing):
    spec = straight_wing.wing_spec(offset="2")
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


def test_the_wings_end_sections_are_its_ribs_as_drawn(hooked_wing, tmp_path):
    build = wing_build.build_wing(hooked_wing.wing_spec(tmp_path), hooked_wing.sidecar(), "w")
    from airfoil_converter import parser

    data = parser.parse_csv(SAMPLE_CSV)
    # Each rib as SolidWorks draws it: the spline through its exported points.
    ribs = [wing.as_drawn(export.build_curves(data, hooked_wing.spec(s), "rib")[0].points)
            for s in hooked_wing.stations]
    sections = section_points(build)
    first, last = sections[min(sections)], sections[max(sections)]
    for points, rib in ((first, ribs[0]), (last, ribs[-1])):
        assert max(to_polyline(p, rib) for p in points[::7]) < 0.01


def test_an_offset_wing_s_guides_meet_every_section_at_their_own_fraction(straight_wing):
    """A guide landed on the point of each section nearest its fraction, and
    between sections a tenth of a millimetre apart that zigzagged too hard for
    SolidWorks to loft two of them."""
    build = wing_build.build_wing(straight_wing.wing_spec(offset="2"), straight_wing.sidecar(), "w")
    sections = section_points(build)
    guides = [c for c in build.curves if c.role == export.ROLE_WING_SURFACE]
    fractions = [f for f in wing.SURFACE_GUIDES if f >= wing.OFFSET_GUIDES_FROM]
    assert len(guides) == 2 * len(fractions)
    for guide in guides:
        for number, points in sections.items():
            assert min(math.dist(p, q) for p in guide.points for q in points) < 1e-9, (
                guide.feature, number)

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


def test_a_blunt_closed_section_is_cut_at_its_nose_not_at_its_trailing_edge():
    """Auto-closing a blunt trailing edge turns a right angle onto the closing
    line. That corner is the sharpest on the outline, and it is not the nose:
    cutting there left the whole section in one piece with the nose inside it."""
    loop = [(50.0 + 50.0 * math.cos(math.radians(a)), 10.0 * math.sin(math.radians(a)))
            for a in range(10, 351, 5)]
    loop.append(loop[0])
    pieces = wing_build.split_at_nose(loop, True, (0.0, 1.0), (0.0, 0.0))
    assert [role for role, _, _ in pieces] == [export.ROLE_SECTION_UPPER, export.ROLE_SECTION_LOWER]
    assert pieces[0][1][-1] == pieces[1][1][0] == loop[34]
    assert len(pieces[0][1]) == 35 and len(pieces[1][1]) == 36


def test_every_section_of_an_offset_wing_comes_in_the_same_pieces(tmp_path):
    synthetic = SyntheticWing([0.0, 300.0], lambda s: 0.0, lambda s: -200.0,
                              te_mode=export.TE_LINE, te_thickness="0.8")
    for direction in (export.OFFSET_OUTWARD, export.OFFSET_INWARD):
        spec = synthetic.wing_spec(offset="2", offset_dir=direction)
        build = wing_build.build_wing(spec, synthetic.sidecar(), "w")
        assert len(build.joins) == len(build.offset.sections) > 2
        for number, (sources, name) in enumerate(build.joins, start=1):
            assert name == f"w_s{number:02d}_joined"
            assert sorted(sources) == [f"w_s{number:02d}_{piece}" for piece in ("lower", "te", "upper")]


def test_a_swallowtail_left_at_a_crease_is_cut_out_at_its_crossing():
    """A square whose top edge runs past its corner and whose left edge comes
    back through it: the small loop goes, and where they crossed is the corner."""
    loop = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (-1.0, 10.0), (0.0, 11.0)]
    assert len(wing_offset._crossing_pairs(loop)) == 1
    assert wing_offset._untangle(loop) == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    # The same loop started inside the little loop keeps the square all the same.
    turned = loop[3:] + loop[:3]
    assert sorted(wing_offset._untangle(turned)) == sorted([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert wing_offset._untangle(square) == square

def _blunt_wing():
    return SyntheticWing([0.0, 300.0], lambda s: 0.0, lambda s: -200.0,
                         te_mode=export.TE_LINE, te_thickness="0.8")


def test_an_edit_keeps_the_sections_the_loft_runs_through():
    """A loft runs through its profiles by name, and one that gained or lost a
    profile broke on every change of offset."""
    synthetic = _blunt_wing()
    first = wing_build.build_wing(synthetic.wing_spec(offset="2"), synthetic.sidecar(), "w")
    for offset in ("1.5", "3"):
        edited = wing_build.build_wing(synthetic.wing_spec(offset=offset), synthetic.sidecar(),
                                       "w", sections=first.station_count)
        assert edited.station_count == first.station_count
        assert [name for _, name in edited.joins] == [name for _, name in first.joins]


def test_a_wing_lofted_through_every_section_has_some_to_spare():
    synthetic = _blunt_wing()
    model = wing_build.stand_up(synthetic.wing_spec(offset="2"), synthetic.sidecar())

    def count(spare):
        return len(wing_offset.offset_wing(
            model.frame, model.loft, -2.0, wing.OPEN, wing.CLOSED, model.te_mode,
            model.te_thickness, spare=spare).sections)

    assert count(wing_offset.SPARE_SECTIONS) == count(0) + wing_offset.SPARE_SECTIONS

def test_a_count_the_wall_cannot_be_kept_to_is_not_forced():
    """Too few to hold the wall: the loft will have to be picked again, so the
    wing comes out as a first export would, spare sections and all."""
    synthetic = _blunt_wing()
    spec = synthetic.wing_spec(offset="2")
    free = wing_build.build_wing(spec, synthetic.sidecar(), "w")
    squeezed = wing_build.build_wing(spec, synthetic.sidecar(), "w", sections=3)
    assert squeezed.station_count == free.station_count

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


def test_a_near_duplicate_beside_a_corner_is_collapsed_onto_the_corner():
    """The real tip nose of a 1.35 mm inward offset, to scale: a corner, and
    0.014 mm past it a second point, between steps of 0.22 and 0.36 mm. The
    spline SolidWorks draws through that kinks, and the loft folds along it."""
    corner = (0.0, 0.0)
    beside = (0.0017, -0.0139)
    loop = [(2.0, 1.4), (0.183, 0.300), (0.054, 0.218), corner, beside,
            (0.044, -0.357), (0.151, -0.619), (2.0, -1.4)]
    cleaned = wing_offset._uncrowd(loop)

    assert cleaned == [p for p in loop if p != beside]
    steps = [math.dist(a, b) for a, b in zip(cleaned, cleaned[1:] + cleaned[:1])]
    assert min(steps) > wing_offset.CROWD_STEP
    # The corner is what the pair is there to say; only the second copy goes.
    assert corner in cleaned


class RoundSkin:
    """A skin that is a circle, so the wall inside it is a circle too.

    Enough of :class:`~airfoil_converter.wing_offset.Skin` for the gap filling:
    the section's outline, and how far a point stands off it.
    """

    def __init__(self, radius, points=240):
        self.radius = radius
        step = 2.0 * math.pi / points
        self.pts = [(radius * math.cos(k * step), radius * math.sin(k * step))
                    for k in range(points)]
        self.loft = self

    def outline(self, _s):
        return list(self.pts)

    def distance(self, _s, p, faces=True):
        return abs(self.radius - math.hypot(p[0], p[1]))


def on_circle(radius, degrees):
    a = math.radians(degrees)
    return (radius * math.cos(a), radius * math.sin(a))


def fill_between(skin, loop, d, passes=4):
    """What the offset builder does: fill, then look again at what was made."""
    bridges = [True] * len(loop)
    for _ in range(passes):
        loop, bridges = wing_offset._fill_gaps(skin, 0.0, loop, bridges, d, True)
        if not any(bridges):
            break
    return loop


def test_a_tight_nose_is_drawn_round_rather_than_cut_across():
    """A wall of a quarter-millimetre radius, which is what an inward offset
    leaves at a hooked tip. Drawn with two points and 50° between them
    SolidWorks will not make a solid of the section; drawn round, it is an arc
    like any other."""
    skin = RoundSkin(2.0)
    d = 1.75                      # wall radius 0.25
    loop = [on_circle(0.25, 100.0), on_circle(0.25, -100.0),
            on_circle(0.25, -170.0), on_circle(0.25, 170.0)]
    out = fill_between(skin, loop, d)

    nose = out[: out.index(loop[1]) + 1]      # the stretch round the nose itself
    assert len(nose) >= 6
    for p in nose:
        assert abs(math.hypot(*p) - 0.25) <= 0.005
    turns = [wing_build._turn(a, b, c) for a, b, c in zip(nose, nose[1:], nose[2:])]
    assert max(turns) <= 15.0
    steps = [math.dist(a, b) for a, b in zip(nose, nose[1:])]
    assert min(steps) >= 0.02


class FlatSkin:
    """A skin that is the line x = -2, so the wall inside it is a line too."""

    def __init__(self):
        self.pts = [(-2.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-2.0, 10.0)]
        self.loft = self

    def outline(self, _s):
        return list(self.pts)

    def distance(self, _s, p, faces=True):
        return p[0] + 2.0


def test_a_straight_run_of_wall_is_left_where_it_is():
    """The rule only draws what bends. A flank already on the wall keeps its
    own points, however long its steps are."""
    straight = [(-0.25, y) for y in (-0.6, -0.2, 0.2, 0.6)]
    out, fresh = wing_offset._fill_gaps(FlatSkin(), 0.0, straight, [True] * 4, 1.75, True)
    assert out == straight
    assert not any(fresh)


def test_a_nose_too_tight_to_draw_is_left_as_one_corner():
    """Below about a seventh of a millimetre an arc cannot be drawn at ten
    degrees a step without steps shorter than the walk can place, so it stays
    a corner — which is what a deep offset's nose and every tail already are."""
    skin = RoundSkin(1.0)
    d = 0.95                      # wall radius 0.05, tighter than MIN_ARC
    loop = [on_circle(0.05, 80.0), on_circle(0.05, -80.0),
            on_circle(0.05, -175.0), on_circle(0.05, 175.0)]
    out = fill_between(skin, loop, d)
    assert out[:2] == loop[:2]


class WedgeSkin:
    """A skin that is a wedge, nose at the origin: the wall inside it is a
    wedge too, with a crease where its two sides meet."""

    def __init__(self, half_angle_deg=30.0):
        self.t = math.radians(half_angle_deg)
        reach = 10.0 * math.tan(self.t)
        self.pts = [(0.0, 0.0), (10.0, -reach), (10.0, reach)]
        self.loft = self

    def outline(self, _s):
        return list(self.pts)

    def distance(self, _s, p, faces=True):
        up = (-math.sin(self.t), math.cos(self.t))
        down = (-math.sin(self.t), -math.cos(self.t))
        return min(-(p[0] * n[0] + p[1] * n[1]) for n in (up, down))


def test_a_crease_the_trim_stopped_short_of_gets_its_corner_back():
    """At the tip of one wing, offset 2.65 mm, the trim stopped both sides of
    the nose's crease short of where they meet, and a 0.12 mm line across stood
    0.06 mm inside it: the wall there read 2.69. The corner goes back in."""
    skin = WedgeSkin(30.0)
    d = 1.0
    apex = (d / math.sin(skin.t), 0.0)
    c, s = math.cos(skin.t), math.sin(skin.t)
    loop = [(apex[0] + 0.08 * c, 0.08 * s), (apex[0] + 0.08 * c, -0.08 * s),
            (apex[0] + 5.0 * c, -5.0 * s), (apex[0] + 5.0 * c, 5.0 * s)]
    out, _ = wing_offset._fill_gaps(skin, 0.0, loop, [True, False, False, False], d, True)
    assert len(out) == 5
    assert out[1] == pytest.approx(apex, abs=1e-3)
    assert skin.distance(0.0, out[1]) == pytest.approx(d, abs=1e-4)


def test_two_points_closer_than_a_fiftieth_of_a_millimetre_are_one_point():
    """The real tip of a 1.6 mm inward offset, where the nose is becoming a
    swallowtail: the trim leaves four points inside 0.07 mm, each too near the
    last for a rule that judges a pair by its neighbours to see."""
    huddle = [(138.459, -34.805), (138.451, -34.845), (138.440, -34.859),
              (138.440, -34.870)]
    loop = [(139.5, -34.5), (138.486, -34.797)] + huddle + [(138.444, -34.987), (139.5, -35.5)]
    out = wing_offset._uncrowd(loop)

    steps = [math.dist(a, b) for a, b in zip(out, out[1:])]
    assert min(steps) >= wing_offset.HUDDLE_STEP
    # The corner of the huddle is what stays: the point furthest off the line.
    assert (138.451, -34.845) in out or (138.440, -34.859) in out


def test_an_evenly_dense_nose_is_left_alone():
    """The 1.5 mm wing's own root nose, which SolidWorks lofts as a solid:
    0.035 mm steps, but between 0.07 mm ones. That is the shape, drawn to one
    scale throughout, not a leftover of the trim."""
    nose = [(1.633, 0.293), (1.596, 0.226), (1.566, 0.141), (1.547, 0.061),
            (1.538, -0.006), (1.533, -0.078), (1.532, -0.114), (1.533, -0.149),
            (1.536, -0.221), (1.544, -0.293), (1.566, -0.398), (1.611, -0.545)]
    loop = nose + [(60.0, -2.0), (60.0, 2.0)]
    assert wing_offset._uncrowd(loop) == loop
