"""The form as data, and the curves it produces. No window required."""

import dataclasses
import os

import pytest

from airfoil_converter import export, geometry, parser
from airfoil_converter.export import ExportSpec, InputError

SAMPLE_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sd7037-il.csv")


@pytest.fixture
def data():
    return parser.parse_csv(SAMPLE_CSV)


def flat(**overrides):
    """A spec on the XY plane, which needs no P-points."""
    base = dict(plane_mode="XY", chord_axis="-X", up_axis="+Y", export_camber=False)
    base.update(overrides)
    return ExportSpec(**base)


# -- the spec round-trips ---------------------------------------------------


def test_spec_round_trips_through_a_dict():
    spec = flat(pitch="-2.5", offset="2", p1=("0", "0", "400"))
    assert ExportSpec.from_dict(spec.to_dict()) == spec


def test_from_dict_ignores_keys_it_does_not_know():
    spec = ExportSpec.from_dict({"pitch": "3", "invented_later": True})
    assert spec.pitch == "3"


def test_from_dict_defaults_what_is_missing():
    assert ExportSpec.from_dict({}) == ExportSpec()


def test_from_dict_rejects_a_point_that_is_not_three_values():
    with pytest.raises(InputError, match="three values"):
        ExportSpec.from_dict({"p1": ["0", "0"]})


def test_a_typed_number_keeps_the_text_the_user_typed():
    """175 must not come back as 175.0, and blank must not come back as 0."""
    spec = flat(target_chord="175")
    assert ExportSpec.from_dict(spec.to_dict()).target_chord == "175"
    assert ExportSpec().target_chord == ""
    assert ExportSpec().target_chord_mm() is None


# -- reading the fields -----------------------------------------------------


@pytest.mark.parametrize("label,turns", [("0°", 0), ("90°", 1), ("180°", 2), ("270°", 3)])
def test_rotation_reads_as_quarter_turns(label, turns):
    assert flat(rotation=label).quarter_turns() == turns


def test_an_inward_offset_reads_negative():
    assert flat(offset="2", offset_dir="Inward").offset_mm() == -2.0
    assert flat(offset="2", offset_dir="Outward").offset_mm() == 2.0


def test_a_negative_offset_is_refused_because_the_direction_says_it():
    with pytest.raises(InputError, match="choose Inward or Outward"):
        flat(offset="-2").offset_mm()


def test_a_negative_trailing_edge_thickness_is_refused():
    with pytest.raises(InputError, match="must not be negative"):
        flat(te_thickness="-1").te_thickness_mm()


@pytest.mark.parametrize("text", ["inf", "-inf", "nan"])
def test_a_field_that_is_not_a_finite_number_is_refused(text):
    """float() reads these, and a chord of inf comes out as a file of nan."""
    with pytest.raises(InputError, match="Target chord must be a finite number"):
        flat(target_chord=text).target_chord_mm()


def test_a_field_that_is_not_a_number_names_itself():
    with pytest.raises(InputError, match="Angle of attack must be a number"):
        flat(pitch="steep").pitch_degrees()


# -- naming -----------------------------------------------------------------


def test_a_name_is_the_source_and_the_role():
    assert export.feature_name("sd7037-il", "airfoil") == "sd7037-il_airfoil"
    assert export.feature_name("sd7037-il", "camber") == "sd7037-il_camber"


def test_the_index_only_shows_from_the_second_record_on():
    assert export.feature_name("sd7037-il", "airfoil", 1) == "sd7037-il_airfoil"
    assert export.feature_name("sd7037-il", "airfoil", 2) == "sd7037-il_airfoil_2"
    assert export.feature_name("sd7037-il", "airfoil_te", 3) == "sd7037-il_airfoil_te_3"


def test_a_name_survives_a_source_that_is_not_a_legal_file_name():
    assert export.feature_name("bad:name*", "airfoil") == "bad_name__airfoil"


def test_an_unknown_role_is_refused():
    with pytest.raises(InputError, match="Unknown curve role"):
        export.feature_name("x", "fuselage")


def test_changing_a_setting_never_moves_a_name(data):
    """The whole live link rests on this: tweak a setting, refresh that feature.

    A name that carried the offset would make every tweak insert a second
    curve and leave the loft pointing at the first one, silently.
    """
    two = export.build_curves(data, flat(offset="2"), "sd7037-il")
    three = export.build_curves(data, flat(offset="3"), "sd7037-il")

    assert [c.feature for c in two] == [c.feature for c in three]
    assert [c.filename for c in two] == [c.filename for c in three]
    assert two[0].points != three[0].points


# -- what an export produces ------------------------------------------------


def test_auto_close_gives_one_closed_curve(data):
    curves = export.build_curves(data, flat(te_mode=export.TE_CLOSE), "s")
    assert [c.role for c in curves] == ["airfoil"]
    assert curves[0].closed is True
    assert curves[0].points[0] == curves[0].points[-1]


def test_leave_open_gives_one_open_curve(data):
    curves = export.build_curves(data, flat(te_mode=export.TE_OPEN), "s")
    assert [c.role for c in curves] == ["airfoil"]
    assert curves[0].closed is False
    assert curves[0].points[0] != curves[0].points[-1]


def test_split_gives_an_upper_and_a_lower(data):
    curves = export.build_curves(data, flat(te_mode=export.TE_SPLIT), "s")
    assert [c.role for c in curves] == ["airfoil_upper", "airfoil_lower"]


def test_close_with_a_line_gives_the_surface_and_a_two_point_curve(data):
    spec = flat(te_mode=export.TE_LINE, te_thickness="1.5")
    curves = export.build_curves(data, spec, "s")
    assert [c.role for c in curves] == ["airfoil", "airfoil_te"]
    assert len(curves[1].points) == 2


def test_camber_adds_a_curve(data):
    curves = export.build_curves(data, flat(export_camber=True), "s")
    assert [c.role for c in curves] == ["airfoil", "camber"]


def test_exporting_nothing_is_refused(data):
    with pytest.raises(InputError, match="Nothing selected"):
        export.build_curves(data, flat(export_airfoil=False, export_camber=False), "s")


def test_an_unknown_trailing_edge_mode_is_refused(data):
    with pytest.raises(InputError, match="Unknown TE handling mode"):
        export.build_curves(data, flat(te_mode="Round it off"), "s")


# -- placement --------------------------------------------------------------


def test_moving_the_leading_edge_moves_the_whole_section(data):
    """The leading edge is where the section's own origin is put, so it is a
    pure translation — nothing about the shape may change with it."""
    here = export.build_curves(data, flat(leading_edge=("0", "0", "0")), "s")[0].points
    there = export.build_curves(data, flat(leading_edge=("10", "20", "30")), "s")[0].points

    assert len(here) == len(there)
    for a, b in zip(here, there):
        assert b == pytest.approx((a[0] + 10.0, a[1] + 20.0, a[2] + 30.0), abs=1e-9)


def test_the_trailing_edge_lands_a_chord_away_along_the_chord_axis(data):
    """The section's own origin goes to the leading edge and its x axis runs
    along the chord, so the trailing edge is an exact chord's distance away.

    The nose is not checked instead: the CSV's closest sampled point to the
    leading edge is a third of a millimetre off it, which is the point spacing
    at the nose, not anything this code decides.
    """
    # A chord axis names the direction the *nose* points, so a nose pointing
    # -X puts the trailing edge a chord further along +X.
    spec = flat(chord_axis="-X", up_axis="+Y", leading_edge=("10", "20", "30"))
    points = export.build_curves(data, spec, "s")[0].points
    assert (10.0 + 175.0, 20.0, 30.0) in [pytest.approx(p, abs=1e-9) for p in points]


def test_a_target_chord_rescales_the_section(data):
    """The chord comes from the CSV header, and the sampled points fall a
    little short of it at the nose, so the check is on the ratio."""
    before = export.build_curves(data, flat(), "s")[0].points
    after = export.build_curves(data, flat(target_chord="100"), "s")[0].points

    def span(points):
        return max(p[0] for p in points) - min(p[0] for p in points)

    assert span(after) / span(before) == pytest.approx(100.0 / 175.0, rel=1e-9)


def test_as_loaded_needs_a_curve_to_have_been_loaded(data):
    with pytest.raises(InputError, match="Load a curve file"):
        export.build_curves(data, ExportSpec(plane_mode=export.MODE_LOADED), "s")


def test_an_unsupported_extension_is_refused(data):
    with pytest.raises(InputError, match="Unsupported extension"):
        export.build_curves(data, flat(extension=".sldprt"), "s")


def test_a_three_point_plane_reads_its_points(data):
    spec = ExportSpec(
        plane_mode=export.MODE_3POINTS,
        export_camber=False,
        p1=("0", "0", "0"),
        p2=("1", "0", "0"),
        p3=("0", "1", "0"),
        leading_edge=("0", "0", "0"),
    )
    curves = export.build_curves(data, spec, "s")
    assert all(p[2] == pytest.approx(0.0) for p in curves[0].points)


def test_a_plane_point_that_is_not_a_number_names_itself(data):
    spec = ExportSpec(plane_mode=export.MODE_3POINTS, p1=("nought", "0", "0"))
    with pytest.raises(InputError, match="P1 X must be a number"):
        export.build_curves(data, spec, "s")


# -- wings ------------------------------------------------------------------


def test_a_wing_spec_round_trips():
    spec = export.WingSpec(ribs=("a", "b"), le_source="le.txt", offset="1.5",
                           offset_dir=export.OFFSET_OUTWARD, swap_ends=True)
    assert export.WingSpec.from_dict(spec.to_dict()) == spec


def test_a_wing_offset_is_signed_by_direction():
    assert export.WingSpec(offset="2").offset_mm() == -2.0
    assert export.WingSpec(offset="2", offset_dir=export.OFFSET_OUTWARD).offset_mm() == 2.0
    assert export.WingSpec().offset_mm() == 0.0
    with pytest.raises(InputError, match="negative"):
        export.WingSpec(offset="-1").offset_mm()


@pytest.mark.parametrize("role,section,name", [
    (export.ROLE_WING_LE, 0, "wing_le"),
    (export.ROLE_WING_TE, 0, "wing_te"),
    (export.ROLE_SECTION, 3, "wing_s03"),
    (export.ROLE_SECTION_TE, 12, "wing_s12_te"),
    (export.ROLE_SECTION_JOINED, 1, "wing_s01_joined"),
])
def test_wing_curve_names(role, section, name):
    assert export.wing_feature_name("wing", role, 1, section) == name


def test_a_second_wing_of_one_name_is_numbered_in_its_base():
    assert export.wing_feature_name("wing", export.ROLE_SECTION, 2, 1) == "wing_2_s01"


def test_a_section_needs_a_number():
    with pytest.raises(InputError):
        export.wing_feature_name("wing", export.ROLE_SECTION, 1, 0)


def test_load_source_reads_a_csv_without_a_section():
    data, section = export.load_source(SAMPLE_CSV)
    assert section is None
    assert data.chord == 175


def test_load_source_reads_a_curve_back_onto_its_plane(tmp_path, data):
    curves = export.build_curves(data, flat(), "rib")
    path = tmp_path / "rib_airfoil.sldcrv"
    from airfoil_converter import writer
    writer.write_curve(str(path), curves[0].points)
    loaded, section = export.load_source(str(path))
    assert section is not None
    assert loaded.chord == pytest.approx(175, abs=0.1)
    assert export.section_from_dict(export.section_to_dict(section)) == section


def test_surface_guides_and_end_profiles_are_named_by_tag():
    assert export.wing_feature_name("w", export.ROLE_WING_SURFACE, tag="upper_30") == "w_upper_30"
    assert export.wing_feature_name("w", export.ROLE_SECTION, 2, tag="root") == "w_2_root"
    assert export.wing_feature_name("w", export.ROLE_SECTION_TE, tag="tip") == "w_tip_te"
    assert export.wing_feature_name("w", export.ROLE_WING_TE_UPPER) == "w_te_upper"
    with pytest.raises(InputError):
        export.wing_feature_name("w", export.ROLE_WING_SURFACE)


def test_a_wing_spec_keeps_its_profiles_choice():
    spec = export.WingSpec(profiles=export.PROFILES_ALL, thickness=export.THICKNESS_SCALED)
    back = export.WingSpec.from_dict(spec.to_dict())
    assert (back.profiles, back.thickness) == (export.PROFILES_ALL, export.THICKNESS_SCALED)
    # Root and tip with guides is what an export gives unless told otherwise,
    # and the thickness follows the loft SolidWorks would make on its own.
    assert export.WingSpec.from_dict({}).profiles == export.PROFILES_ALL
    assert export.WingSpec.from_dict({}).thickness == export.THICKNESS_BLENDED
