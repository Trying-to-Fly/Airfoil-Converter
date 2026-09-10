import os

import pytest

from airfoil_converter.parser import (
    AIRFOIL,
    CAMBER,
    CHORD,
    AirfoilParseError,
    is_curve_file,
    parse_csv,
    parse_curve,
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
