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


# -- what the poll loop is allowed to reach for ------------------------------


def _converter_app_class():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(gui))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ConverterApp":
            return node
    raise AssertionError("ConverterApp is not in gui.py any more")


def _self_attributes(node, store=False):
    import ast

    wanted = ast.Store if store else ast.Load
    return {
        child.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute)
        and isinstance(child.value, ast.Name)
        and child.value.id == "self"
        and isinstance(child.ctx, wanted)
    }


def test_every_pending_call_is_set_up_before_the_first_tick():
    """A poll method reading an attribute the constructor never set raises on
    every tick, and the tick catches it: the window then never hears back from
    SolidWorks and sits on "Looking for SolidWorks..." with nothing to say why.
    That is a bug this had, and it cost an evening to find."""
    import ast

    app = _converter_app_class()
    methods = {n.name: n for n in app.body if isinstance(n, ast.FunctionDef)}
    read = set()
    for name, node in methods.items():
        if name.startswith(("_tick", "_collect")):
            read |= _self_attributes(node)

    pending = {name for name in read if name.startswith("_pending")}
    assert pending, "the poll loop no longer has a pending call to check"
    assert pending <= _self_attributes(methods["__init__"], store=True)
