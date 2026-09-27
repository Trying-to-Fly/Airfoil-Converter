import pytest

from airfoil_converter import writer


def test_format_points_uses_tabs_ten_decimals_and_a_unit():
    text = writer.format_points([(175.0, 0.0, 75.0), (1.5, -2.25, 0.0)])
    assert text == (
        "175.0000000000mm\t0.0000000000mm\t75.0000000000mm\r\n"
        "1.5000000000mm\t-2.2500000000mm\t0.0000000000mm\r\n"
    )


def test_a_written_section_stays_flat_to_well_under_a_millionth():
    """SolidWorks refused solid lofts ending on sections that six decimals left
    half a millionth of a millimetre off their plane."""
    value = 998.0955123456789
    assert abs(float(writer.format_value(value)[:-2]) - value) < 1e-9


def test_format_value_strips_a_signed_zero_that_only_appears_when_rounded():
    # -1e-12 is not equal to zero, so a pre-format test would miss it and leave
    # "-0.0000000000mm" — enough to make a section differ from its own mirror.
    assert writer.format_value(-1e-12) == "0.0000000000mm"
    assert writer.format_value(-0.0) == "0.0000000000mm"
    assert writer.format_value(-0.5) == "-0.5000000000mm"


def test_sanitize_strips_characters_a_name_cannot_hold():
    assert writer.sanitize("bad:name*") == "bad_name_"
    assert writer.sanitize("  .  ") == "airfoil"


def test_write_curve_round_trip(tmp_path):
    path = str(tmp_path / "curve.sldcrv")
    count = writer.write_curve(path, [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)])
    assert count == 2
    with open(path, "rb") as handle:
        assert handle.read() == (
            b"0.0000000000mm\t0.0000000000mm\t0.0000000000mm\r\n"
            b"1.0000000000mm\t2.0000000000mm\t3.0000000000mm\r\n"
        )


def test_write_curve_refuses_empty(tmp_path):
    with pytest.raises(ValueError, match="empty curve"):
        writer.write_curve(str(tmp_path / "empty.txt"), [])


def test_curve_bytes_are_what_the_hash_covers():
    points = [(1.0, 2.0, 3.0)]
    data = writer.curve_bytes(points)
    assert data == writer.format_points(points).encode("ascii")
    assert writer.sha256_hex(data) == writer.sha256_hex(writer.curve_bytes(points))


def test_write_curve_if_changed_writes_once(tmp_path, monkeypatch):
    path = str(tmp_path / "curve.sldcrv")
    points = [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)]

    changed, first = writer.write_curve_if_changed(path, points)
    assert changed is True

    # SolidWorks re-reads this file on every refresh, so an unchanged export
    # must not touch it at all — not even to rewrite identical bytes.
    def fail(*args):
        raise AssertionError("os.replace was called for identical content")

    monkeypatch.setattr(writer.os, "replace", fail)
    changed, again = writer.write_curve_if_changed(path, points)
    assert changed is False
    assert again == first


def test_write_curve_if_changed_rewrites_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "curve.sldcrv"
    writer.write_curve_if_changed(str(path), [(0.0, 0.0, 0.0)])
    changed, digest = writer.write_curve_if_changed(str(path), [(9.0, 0.0, 0.0)])

    assert changed is True
    assert digest == writer.sha256_hex(writer.curve_bytes([(9.0, 0.0, 0.0)]))
    assert path.read_bytes().startswith(b"9.0000000000mm")
    assert [p.name for p in tmp_path.iterdir()] == ["curve.sldcrv"]
