import os

import pytest

from airfoil_converter.parser import (
    AIRFOIL,
    CAMBER,
    CHORD,
    AirfoilParseError,
    is_curve_file,
    is_dat_file,
    parse_csv,
    parse_curve,
    parse_dat,
    parse_dat_lines,
    parse_rows,
)

SAMPLE_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sd7037-il.csv")


def rows(text):
    return [line.split(",") for line in text.strip("\n").split("\n")]


MINIMAL = """
Name,TEST-1
Chord(mm),100
Pitch(deg),0
,
Airfoil surface,
X(mm),Y(mm)
100.000000,0.000000
50.000000,5.000000
0.000000,0.000000
50.000000,-5.000000
100.000000,0.000000
,
Camber line,
X(mm),Y(mm)
0.000000,0.000000
100.000000,0.000000
,
Chord line,
X(mm),Y(mm)
0.000000,0.000000
100.000000,0.000000
"""


def test_parses_metadata_and_sections():
    data = parse_rows(rows(MINIMAL))
    assert data.name == "TEST-1"
    assert data.chord == 100.0
    assert data.metadata["Pitch(deg)"] == "0"
    assert set(data.sections) == {AIRFOIL, CAMBER, CHORD}
    assert len(data.airfoil) == 5
    assert data.airfoil[0] == (100.0, 0.0)
    assert data.camber == [(0.0, 0.0), (100.0, 0.0)]


def test_column_headers_are_not_points():
    data = parse_rows(rows(MINIMAL))
    assert all(isinstance(x, float) for point in data.airfoil for x in point)


def test_unknown_sections_are_kept_but_ignored():
    text = MINIMAL + "\n,\nMystery data,\nX(mm),Y(mm)\n1.0,2.0\n"
    data = parse_rows(rows(text))
    assert data.sections["mystery data"] == [(1.0, 2.0)]
    assert len(data.airfoil) == 5


def test_missing_camber_is_allowed():
    text = MINIMAL.split("Camber line,")[0]
    data = parse_rows(rows(text))
    assert data.camber is None
    assert data.airfoil


def test_missing_airfoil_surface_is_an_error():
    text = "Name,X\nChord(mm),100\n,\nCamber line,\nX(mm),Y(mm)\n0.0,0.0\n"
    with pytest.raises(AirfoilParseError, match="Airfoil surface"):
        parse_rows(rows(text))


def test_chord_falls_back_to_chord_line():
    text = MINIMAL.replace("Chord(mm),100\n", "")
    data = parse_rows(rows(text))
    assert data.chord == 100.0


def test_chord_unavailable_is_an_error():
    text = "Name,X\n,\nAirfoil surface,\nX(mm),Y(mm)\n1.0,0.0\n"
    with pytest.raises(AirfoilParseError, match="chord"):
        parse_rows(rows(text))


def test_missing_name_defaults():
    text = MINIMAL.replace("Name,TEST-1\n", "")
    assert parse_rows(rows(text)).name == "airfoil"


def test_real_sample_file():
    data = parse_csv(SAMPLE_CSV)
    assert data.name == "SD7037-092-88"
    assert data.chord == 175.0
    assert len(data.airfoil) == 61
    assert data.airfoil[0] == (175.0, 0.0)
    assert data.airfoil[-1] == (175.0, 0.0)
    assert len(data.camber) == 31
    assert data.sections[CHORD] == [(0.0, 0.0), (175.0, 0.0)]


def test_unreadable_file_is_an_error():
    with pytest.raises(AirfoilParseError, match="Could not read"):
        parse_csv(os.path.join(os.path.dirname(SAMPLE_CSV), "does-not-exist.csv"))


# ---------------------------------------------------------------- curve files

CURVE = """175.000000 0.000000 0.000000
90.000000 12.000000 0.000000
0.000000 0.000000 0.000000
90.000000 -6.000000 0.000000
"""


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="ascii")
    return str(path)


def test_parse_curve_reads_three_columns(tmp_path):
    points = parse_curve(write(tmp_path, "c.sldcrv", CURVE))
    assert points[0] == (175.0, 0.0, 0.0)
    assert points[1] == (90.0, 12.0, 0.0)
    assert len(points) == 4


def test_parse_curve_skips_blank_lines(tmp_path):
    points = parse_curve(write(tmp_path, "c.txt", "\n" + CURVE.replace("\n", "\n\n")))
    assert len(points) == 4


def test_parse_curve_rejects_a_line_that_is_not_a_point(tmp_path):
    with pytest.raises(AirfoilParseError, match="not three numbers"):
        parse_curve(write(tmp_path, "c.sldcrv", CURVE + "175.0 0.0\n"))


def test_parse_curve_rejects_too_few_points(tmp_path):
    with pytest.raises(AirfoilParseError, match="too few"):
        parse_curve(write(tmp_path, "c.sldcrv", "0 0 0\n1 1 1\n"))


def test_a_curve_file_is_told_from_a_csv(tmp_path):
    assert is_curve_file(write(tmp_path, "c.sldcrv", CURVE))
    assert not is_curve_file(SAMPLE_CSV)


def test_a_curve_file_is_told_by_content_not_extension(tmp_path):
    """The app writes .txt curves, and a plotter CSV may well be named .txt too."""
    assert is_curve_file(write(tmp_path, "curve.txt", CURVE))
    assert not is_curve_file(write(tmp_path, "plotter.txt", MINIMAL))


def test_a_file_this_app_wrote_reads_back(tmp_path):
    """The writer's own output is valid input, or 'As loaded' would break."""
    from airfoil_converter import writer

    points = [(175.0, 0.0, 0.0), (90.0, 12.0, 0.0), (0.0, 0.0, 0.0), (90.0, -6.0, 0.0)]
    path = str(tmp_path / "written.sldcrv")
    writer.write_curve(path, points)

    assert is_curve_file(path)
    assert parse_curve(path) == points


def test_units_are_read_back_as_millimetres(tmp_path):
    text = "1cm\t2m\t3in\n0mm 0mm 0mm\n1 2 3\n"
    points = parse_curve(write(tmp_path, "mixed.sldcrv", text))
    assert points[0][0] == pytest.approx(10.0)
    assert points[0][1] == pytest.approx(2000.0)
    assert points[0][2] == pytest.approx(76.2)
    assert points[1] == (0.0, 0.0, 0.0)
    assert points[2] == (1.0, 2.0, 3.0)


def test_a_legacy_space_separated_file_still_reads(tmp_path):
    """Curve files written before 1.5 carry bare numbers and single spaces."""
    legacy = "175.000000 0.000000 0.000000\n90.000000 12.000000 0.000000\n0.000000 0.000000 0.000000\n"
    path = write(tmp_path, "legacy.sldcrv", legacy)
    assert is_curve_file(path)
    assert parse_curve(path)[0] == (175.0, 0.0, 0.0)


def test_a_bare_number_never_takes_the_unit_path(tmp_path):
    """'1e-3' ends in no unit, and 'inf' must not be read as 'f' inches."""
    from airfoil_converter.parser import _as_number

    assert _as_number("1e-3") == pytest.approx(0.001)
    assert _as_number("1E3") == pytest.approx(1000.0)
    assert _as_number("nan") != _as_number("nan")  # still a float nan, not None
    assert _as_number("zzz") is None


# ------------------------------------------------------- UIUC coordinate files

UIUC = os.path.join(os.path.dirname(__file__), "uiuc")
SD7037_DAT = os.path.join(UIUC, "sd7037.dat")
CLARKY_DAT = os.path.join(UIUC, "clarky.dat")


def test_a_selig_file_reads_as_one_contour_from_the_upper_trailing_edge():
    data = parse_dat(SD7037_DAT)
    assert data.name == "SD7037-092-88"
    assert data.metadata["Format"] == "Selig"
    assert len(data.airfoil) == 61
    assert data.airfoil[0] == data.airfoil[-1] == (1.0, 0.0)
    assert data.airfoil[1] == (0.99672, 0.00042)  # Upper surface first.
    assert data.airfoil[-2] == (0.99671, 0.00021)
    assert data.chord == 1.0
    assert data.camber is None


def test_a_lednicer_file_reads_in_the_same_order_with_its_counts_kept_aside():
    data = parse_dat(CLARKY_DAT)
    assert data.name == "CLARK Y AIRFOIL"
    assert data.metadata["Format"] == "Lednicer"
    assert data.metadata["Points"] == "61 61"
    assert len(data.airfoil) == 61 + 61 - 1  # The shared nose once.
    assert data.airfoil[0] == (1.0, 0.0005993)  # The upper trailing edge first...
    assert data.airfoil[-1] == (1.0, -0.0005993)  # ...and the lower one last.
    assert data.airfoil[60] == (0.0, 0.0)
    assert data.airfoil.count((0.0, 0.0)) == 1
    assert data.airfoil[59] == (0.0005, 0.002339)
    assert data.airfoil[61] == (0.0005, -0.00467)
    assert data.chord == 1.0


def test_a_dat_file_is_told_by_its_content(tmp_path):
    assert is_dat_file(SD7037_DAT)
    assert is_dat_file(CLARKY_DAT)
    assert not is_dat_file(SAMPLE_CSV)
    assert not is_dat_file(write(tmp_path, "curve.dat", CURVE))
    assert not is_curve_file(SD7037_DAT)
    with open(SD7037_DAT, encoding="ascii") as handle:
        assert is_dat_file(write(tmp_path, "renamed.txt", handle.read()))


def test_a_dat_file_loads_wherever_a_source_does():
    from airfoil_converter import export

    data, section = export.load_source(CLARKY_DAT)
    assert section is None
    assert data.name == "CLARK Y AIRFOIL"
    assert len(data.airfoil) == 121


def test_a_dat_file_exports_at_a_target_chord_point_for_point():
    """Opened unmodified, set to a 200 mm chord: scaled, the right way up, the ends kept."""
    from airfoil_converter import export

    for path, mode in ((SD7037_DAT, export.TE_CLOSE), (CLARKY_DAT, export.TE_OPEN)):
        data, _ = export.load_source(path)
        spec = export.ExportSpec(export_camber=False, target_chord="200", te_mode=mode)
        (curve,) = export.build_curves(data, spec, "rib")
        source = data.airfoil
        assert len(curve.points) == len(source)
        for got, (x, y) in zip(curve.points, source):
            assert got == pytest.approx((200.0 * x, 200.0 * y, 0.0), abs=1e-12)
    assert curve.points[0] == pytest.approx((200.0, 0.11986, 0.0))
    assert curve.points[-1] == pytest.approx((200.0, -0.11986, 0.0))


def test_a_dat_file_without_a_title_is_named_after_the_file(tmp_path):
    text = "1.0 0.0\n0.5 0.06\n0.0 0.0\n0.5 -0.04\n1.0 0.0\n"
    data = parse_dat(write(tmp_path, "my-section.dat", text))
    assert data.name == "my-section"
    assert len(data.airfoil) == 5


def test_a_lednicer_file_without_blank_lines_is_split_by_its_counts():
    lines = ["TEST", "3. 3.", "0 0", "0.5 0.06", "1 0.001", "0 0", "0.5 -0.04", "1 -0.001"]
    data = parse_dat_lines(lines)
    assert data.airfoil == [(1.0, 0.001), (0.5, 0.06), (0.0, 0.0), (0.5, -0.04), (1.0, -0.001)]


def test_a_lednicer_count_that_does_not_match_its_blocks_is_an_error():
    lines = ["TEST", "4. 3.", "", "0 0", "0.5 0.06", "1 0.001", "", "0 0", "0.5 -0.04", "1 -0.001"]
    with pytest.raises(AirfoilParseError, match="promises 4 upper and 3 lower points, but the two "
                                                "blocks hold 3 and 3"):
        parse_dat_lines(lines)


def test_a_first_pair_that_counts_nothing_is_read_as_a_point():
    """Whole numbers that do not add up to the points there are are a point, Selig-style."""
    lines = ["MM SECTION", "100 2", "50 6", "0 0", "50 -4", "100 -2"]
    data = parse_dat_lines(lines)
    assert data.metadata["Format"] == "Selig"
    assert data.airfoil[0] == (100.0, 2.0)
    assert len(data.airfoil) == 5


def test_a_lednicer_lower_block_may_leave_out_the_shared_nose():
    lines = ["TEST", "3. 3.", "", "0 0", "0.5 0.06", "1 0.001", "", "0.5 -0.04", "1 -0.001"]
    data = parse_dat_lines(lines)
    assert data.airfoil == [(1.0, 0.001), (0.5, 0.06), (0.0, 0.0), (0.5, -0.04), (1.0, -0.001)]


def test_a_line_that_cannot_be_read_is_named():
    with pytest.raises(AirfoilParseError, match=r"Line 4 is not an x y pair: '1 2 3'"):
        parse_dat_lines(["TITLE", "1 0", "0 0", "1 2 3", "1 -0.1"])


def test_a_selig_file_running_the_other_way_is_turned_upper_first(tmp_path):
    with open(SD7037_DAT, encoding="ascii") as handle:
        lines = handle.read().splitlines()
    path = write(tmp_path, "reversed.dat", "\n".join([lines[0]] + lines[1:][::-1]) + "\n")
    assert parse_dat(path).airfoil == parse_dat(SD7037_DAT).airfoil


def test_a_dat_file_is_marked_as_fractions_of_its_chord():
    from airfoil_converter import export

    data = parse_dat(SD7037_DAT)
    assert data.in_chords
    assert not parse_csv(SAMPLE_CSV).in_chords
    with pytest.raises(export.InputError, match="Set a target chord in millimetres"):
        export.build_curves(data, export.ExportSpec(export_camber=False), "rib")


def test_a_file_that_is_not_pairs_of_numbers_is_not_a_dat_file():
    with pytest.raises(AirfoilParseError, match="not a UIUC coordinate file"):
        parse_dat_lines(["TITLE", "0 0", "4 5"])
