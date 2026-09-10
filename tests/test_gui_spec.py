"""The bridge between the window's widgets and ExportSpec.

Only the parts that are data. Building a window needs a display, and the
machine this is developed on has none, which is exactly why the mapping is a
table rather than a run of assignments buried in a method.
"""

import dataclasses

import pytest

pytest.importorskip("tkinter", reason="tkinter is not available here")

from airfoil_converter import gui  # noqa: E402
from airfoil_converter.export import ExportSpec  # noqa: E402


def test_the_mapping_covers_every_field_of_the_spec():
    """A field added to ExportSpec and forgotten here would be silently lost
    when a curve's settings are put back into the form."""
    mapped = {field for field, _ in gui.SPEC_VARS} | set(gui.SPEC_COMPOUND)
    declared = {f.name for f in dataclasses.fields(ExportSpec)}
    assert mapped == declared


def test_the_mapping_names_no_variable_twice():
    attrs = [attr for _, attr in gui.SPEC_VARS]
    assert len(attrs) == len(set(attrs))


def test_the_compound_fields_are_not_also_mapped_as_plain_variables():
    plain = {field for field, _ in gui.SPEC_VARS}
    assert plain.isdisjoint(gui.SPEC_COMPOUND)
