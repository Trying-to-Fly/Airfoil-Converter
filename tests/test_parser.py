import os

import pytest

from airfoil_converter.parser import AIRFOIL, CAMBER, CHORD, AirfoilParseError, parse_csv, parse_rows

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
