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


def _class_named(module, name):
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not in {module.__name__} any more")


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


@pytest.mark.parametrize("module,name", [
    ("gui", "ConverterApp"),
    ("wing_tab", "WingTab"),
])
def test_every_pending_call_is_set_up_before_the_first_tick(module, name):
    """A poll method reading an attribute the constructor never set raises on
    every tick, and the tick catches it: the window then never hears back from
    SolidWorks and sits on "Looking for SolidWorks..." with nothing to say why.
    That is a bug this had, and it cost an evening to find."""
    import ast
    import importlib

    app = _class_named(importlib.import_module(f"airfoil_converter.{module}"), name)
    methods = {n.name: n for n in app.body if isinstance(n, ast.FunctionDef)}
    read = set()
    for name, node in methods.items():
        if name.startswith(("_tick", "_collect")):
            read |= _self_attributes(node)

    pending = {name for name in read if name.startswith("_pending")}
    assert pending, "the poll loop no longer has a pending call to check"
    assert pending <= _self_attributes(methods["__init__"], store=True)


# -- the wing tab's form ----------------------------------------------------


def test_the_wing_form_round_trips_through_a_spec():
    from airfoil_converter import wing_tab
    from airfoil_converter.export import OFFSET_OUTWARD, WingSpec

    spec = WingSpec(ribs=("a", "b"), le_source="le.sldcrv", root_end="closed",
                    swap_ends=True, offset="1.5", offset_dir=OFFSET_OUTWARD, extension=".txt")
    form = wing_tab.spec_to_form(spec)
    assert form["te_mode"] == wing_tab.EDGE_LINE
    assert wing_tab.form_to_spec(form, ["a", "b"]) == spec


def test_a_wing_form_asking_for_a_file_needs_one():
    from airfoil_converter import wing_tab
    from airfoil_converter.export import InputError, WingSpec

    form = wing_tab.spec_to_form(WingSpec())
    form["le_mode"] = wing_tab.EDGE_FILE
    with pytest.raises(InputError, match="leading-edge curve file"):
        wing_tab.form_to_spec(form, [])


def test_an_offset_copy_is_named_for_its_direction():
    from airfoil_converter import wing_tab
    from airfoil_converter.export import OFFSET_INWARD, OFFSET_OUTWARD

    assert wing_tab.offset_copy_name("wing", OFFSET_INWARD) == "wing_inner"
    assert wing_tab.offset_copy_name("wing", OFFSET_OUTWARD) == "wing_outer"


def test_an_export_that_makes_fewer_curves_retires_the_rest():
    from airfoil_converter import store, wing_tab
    from airfoil_converter.export import Curve

    record = store.WingRecord(export_id="wing-1", curves=[
        store.CurveRecord(role="section", feature="w_s01", file="w_s01.sldcrv"),
        store.CurveRecord(role="section", feature="w_s02", file="w_s02.sldcrv"),
        store.CurveRecord(role="section_joined", feature="w_s02_joined", file=""),
    ])
    kept = [Curve(role="section", points=[(0, 0, 0)], closed=True, feature="w_s01")]
    assert wing_tab.retire_missing(record, kept, []) == ["w_s02", "w_s02_joined"]
    assert [c.retired for c in record.curves] == [False, True, True]


# -- the poll ------------------------------------------------------------------


class _PolledSession:
    """Counts what the once-a-second poll asks SolidWorks."""

    pid = 7
    label = "SolidWorks 2026"
    revision = (34, 0, 0)

    def __init__(self):
        self.walks = 0
        self.keys = 0

    def active_document(self):
        from airfoil_converter.swcom import DocInfo
        return DocInfo(title="Wing.SLDPRT", path=r"C:\p\Wing.SLDPRT", doc_type=1)

    def change_key(self):
        self.keys += 1
        return (120, 1292)

    def features(self):
        self.walks += 1
        return []

    def curve_features(self):
        self.walks += 1
        return []


def test_the_second_by_second_poll_never_walks_the_tree():
    """A walk took 650 ms of SolidWorks' drawing thread; orbiting stuttered."""
    session = _PolledSession()
    key = gui._read_key(session)
    assert key == (7, "Wing.SLDPRT", r"C:\p\Wing.SLDPRT", (120, 1292))
    assert session.walks == 0


def test_a_full_read_walks_the_tree_once():
    session = _PolledSession()
    snapshot = gui._read_snapshot(session)
    assert session.walks == 1
    assert snapshot["key"] == gui._read_key(_PolledSession())
