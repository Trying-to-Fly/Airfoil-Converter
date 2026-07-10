import os

import pytest

from airfoil_converter import writer


def test_format_points_uses_six_decimals_and_spaces():
    text = writer.format_points([(175.0, 0.0, 75.0), (1.5, -2.25, 0.0)])
    assert text == "175.000000 0.000000 75.000000\n1.500000 -2.250000 0.000000\n"


def test_output_path_naming():
    path = writer.output_path(os.sep + "out", "sd7037-il", "airfoil", ".sldcrv")
    assert os.path.basename(path) == "sd7037-il_airfoil.sldcrv"
    assert os.path.basename(
        writer.output_path(os.sep + "out", "sd7037-il", "airfoil_upper", ".txt")
    ) == "sd7037-il_airfoil_upper.txt"


def test_output_path_sanitizes_the_stem():
    path = writer.output_path(os.sep + "out", "bad:name*", "camber", ".txt")
    assert os.path.basename(path) == "bad_name__camber.txt"


def test_output_path_rejects_other_extensions():
    with pytest.raises(ValueError, match="Unsupported extension"):
        writer.output_path(os.sep + "out", "x", "airfoil", ".sldprt")


def test_write_curve_round_trip(tmp_path):
    path = str(tmp_path / "curve.sldcrv")
    count = writer.write_curve(path, [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)])
    assert count == 2
    with open(path, "rb") as handle:
        assert handle.read() == b"0.000000 0.000000 0.000000\r\n1.000000 2.000000 3.000000\r\n"


def test_write_curve_refuses_empty(tmp_path):
    with pytest.raises(ValueError, match="empty curve"):
        writer.write_curve(str(tmp_path / "empty.txt"), [])
