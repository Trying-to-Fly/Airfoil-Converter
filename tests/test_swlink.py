"""The push ordering, tested against a fake on a machine with no SolidWorks.

These rules were expensive to learn and are invisible when broken — a part
left with the rebuild suppressed looks perfectly normal — so they are pinned
here rather than trusted to a careful reading.
"""

import os

import pytest

from airfoil_converter import swlink
from airfoil_converter.export import Curve
from airfoil_converter.swcom import DocInfo, FeatureInfo, SolidWorksError
from airfoil_converter.swlink import LinkError, orphaned, push


class FakeSolidWorks:
    """Records every call, in order, so the order can be asserted."""

    def __init__(self, revision=(34, 0, 0), features=(), doc_type=1, title="Part1"):
        self.revision = revision
        self.doc = DocInfo(title=title, path=r"C:\parts\Part1.SLDPRT", doc_type=doc_type)
        self.curves = list(features)
        self.calls = []
        self.rename_to = {}      # asked name -> the name SolidWorks keeps instead
        self.insert_fails = set()
        self.reload_fails = set()
        self.composites = []
        self.join_fails = False

    def active_document(self):
        return self.doc

    def curve_features(self):
        return [FeatureInfo(name=n, type_name="CurveInFile") for n in self.curves]

    def insert_curve(self, path, name):
        self.calls.append(("insert", name))
        if name in self.insert_fails:
            raise SolidWorksError(f"cannot import {name}")
        kept = self.rename_to.get(name, name)
        self.curves.append(kept)
        return kept

    def reload_curve(self, name, path):
        self.calls.append(("reload", name))
        if name in self.reload_fails:
            raise SolidWorksError(f"cannot reload {name}")

    def feature_names(self):
        return list(self.curves) + list(self.composites)

    def insert_composite_curve(self, sources, name):
        self.calls.append(("join", tuple(sources), name))
        if self.join_fails:
            raise SolidWorksError("SolidWorks would not join them")
        kept = self.rename_to.get(name, name)
        self.composites.append(kept)
        return kept

    def set_rebuild_suppressed(self, suppressed):
        self.calls.append(("suppress", suppressed))

    def rebuild(self):
        self.calls.append(("rebuild",))
        return True


def curve(role, feature, y=0.0):
    return Curve(
        role=role,
        points=[(0.0, y, 0.0), (10.0, y, 0.0), (10.0, y + 1.0, 0.0)],
        closed=False,
        feature=feature,
        filename=feature + ".sldcrv",
    )


@pytest.fixture
def curves():
    return [curve("airfoil", "rib_airfoil"), curve("camber", "rib_camber")]


def indexes(calls, kind):
    return [i for i, c in enumerate(calls) if c[0] == kind]


# -- the ordering rules -----------------------------------------------------


def test_everything_reloads_before_the_single_rebuild(tmp_path, curves):
    sw = FakeSolidWorks(features=["rib_airfoil", "rib_camber"])
    result = push(sw, curves, str(tmp_path))

    assert indexes(sw.calls, "rebuild") == [len(sw.calls) - 1]
    assert len(indexes(sw.calls, "rebuild")) == 1
    assert max(indexes(sw.calls, "reload")) < indexes(sw.calls, "rebuild")[0]
    assert result.refreshed == ["rib_airfoil", "rib_camber"]


def test_the_rebuild_is_suppressed_around_every_change(tmp_path, curves):
    sw = FakeSolidWorks(features=["rib_airfoil", "rib_camber"])
    push(sw, curves, str(tmp_path))

    on = sw.calls.index(("suppress", True))
    off = sw.calls.index(("suppress", False))
    reloads = indexes(sw.calls, "reload")
    assert on < min(reloads)
    assert max(reloads) < off


def test_suppression_is_lifted_even_when_a_reload_fails(tmp_path, curves):
    """A part left suppressed looks fine and silently stops updating."""
    sw = FakeSolidWorks(features=["rib_airfoil", "rib_camber"])
    sw.reload_fails = {"rib_airfoil"}
    result = push(sw, curves, str(tmp_path))

    assert ("suppress", False) in sw.calls
    assert result.failures and result.failures[0][0] == "rib_airfoil"
    # The rest of the set still went in, rather than being abandoned halfway.
    assert result.refreshed == ["rib_camber"]


def test_one_failure_does_not_abandon_the_others_or_the_rebuild(tmp_path, curves):
    sw = FakeSolidWorks(features=["rib_airfoil", "rib_camber"])
    sw.reload_fails = {"rib_camber"}
    result = push(sw, curves, str(tmp_path))
    assert result.rebuilt is True
    assert result.refreshed == ["rib_airfoil"]


# -- deciding what to do ----------------------------------------------------


def test_an_empty_part_gets_inserts_and_no_reloads(tmp_path, curves):
    sw = FakeSolidWorks()
    result = push(sw, curves, str(tmp_path))

    assert result.inserted == ["rib_airfoil", "rib_camber"]
    assert result.refreshed == []
    assert indexes(sw.calls, "reload") == []


def test_a_second_push_of_identical_curves_touches_nothing(tmp_path, curves):
    sw = FakeSolidWorks()
    push(sw, curves, str(tmp_path))
    sw.calls.clear()

    result = push(sw, curves, str(tmp_path))
    assert sw.calls == []
    assert result.unchanged == ["rib_airfoil", "rib_camber"]
    assert result.rebuilt is False


def test_changed_points_refresh_the_same_feature(tmp_path, curves):
    sw = FakeSolidWorks()
    push(sw, curves, str(tmp_path))
    moved = [curve("airfoil", "rib_airfoil", y=5.0), curves[1]]
    sw.calls.clear()

    result = push(sw, moved, str(tmp_path))
    assert result.refreshed == ["rib_airfoil"]
    assert result.unchanged == ["rib_camber"]
    assert sw.curves == ["rib_airfoil", "rib_camber"]  # no second feature appeared


def test_a_feature_deleted_between_pushes_is_put_back(tmp_path, curves):
    sw = FakeSolidWorks()
    push(sw, curves, str(tmp_path))
    sw.curves.remove("rib_airfoil")
    sw.calls.clear()

    result = push(sw, curves, str(tmp_path), force=["rib_camber"])
    assert result.inserted == ["rib_airfoil"]
    assert result.refreshed == ["rib_camber"]


def test_force_refreshes_a_curve_whose_file_did_not_change(tmp_path, curves):
    sw = FakeSolidWorks(features=["rib_airfoil", "rib_camber"])
    push(sw, curves, str(tmp_path))
    sw.calls.clear()

    result = push(sw, curves, str(tmp_path), force=["rib_airfoil"])
    assert result.refreshed == ["rib_airfoil"]
    assert result.unchanged == ["rib_camber"]


def test_inserting_can_be_turned_off_and_says_what_it_skipped(tmp_path, curves):
    sw = FakeSolidWorks()
    result = push(sw, curves, str(tmp_path), insert_missing=False)

    assert result.inserted == []
    assert [name for name, _ in result.failures] == ["rib_airfoil", "rib_camber"]
    assert sw.calls == []


# -- names ------------------------------------------------------------------


def test_a_name_solidworks_would_not_take_is_reported_not_recorded(tmp_path, curves):
    """SolidWorks keeps a name of its own on a collision, silently."""
    sw = FakeSolidWorks()
    sw.rename_to = {"rib_airfoil": "Curve3"}
    result = push(sw, curves, str(tmp_path))

    assert result.inserted == ["rib_camber"]
    assert result.failures == [("rib_airfoil", "SolidWorks named it 'Curve3' instead")]


# -- refusals ---------------------------------------------------------------


def test_an_older_solidworks_is_refused_by_name(tmp_path, curves):
    sw = FakeSolidWorks(revision=(33, 3, 0))
    with pytest.raises(LinkError, match="SolidWorks 2025"):
        push(sw, curves, str(tmp_path))
    assert sw.calls == []


def test_a_newer_solidworks_is_pushed_to_normally(tmp_path, curves):
    """The gate is a floor. A 2027 install must simply work."""
    sw = FakeSolidWorks(revision=(35, 0, 0))
    result = push(sw, curves, str(tmp_path))
    assert result.inserted == ["rib_airfoil", "rib_camber"]
    assert result.rebuilt is True


def test_an_assembly_is_refused(tmp_path, curves):
    sw = FakeSolidWorks(doc_type=2, title="Wing.SLDASM")
    with pytest.raises(LinkError, match="not a part"):
        push(sw, curves, str(tmp_path))


def test_no_document_is_refused(tmp_path, curves):
    sw = FakeSolidWorks()
    sw.active_document = lambda: None
    with pytest.raises(LinkError, match="No document is open"):
        push(sw, curves, str(tmp_path))


def test_pushing_nothing_is_refused(tmp_path):
    with pytest.raises(LinkError, match="no curves to send"):
        push(FakeSolidWorks(), [], str(tmp_path))


# -- the files are the payload ----------------------------------------------


def test_the_files_are_written_even_before_any_com_call(tmp_path, curves):
    sw = FakeSolidWorks()
    result = push(sw, curves, str(tmp_path))
    for c in curves:
        assert (tmp_path / c.filename).exists()
    assert set(result.hashes) == {"rib_airfoil", "rib_camber"}


def test_files_are_written_even_when_inserting_is_off(tmp_path, curves):
    push(FakeSolidWorks(), curves, str(tmp_path), insert_missing=False)
    assert (tmp_path / "rib_airfoil.sldcrv").exists()


# -- orphans ----------------------------------------------------------------


def test_dropping_the_trailing_edge_curve_names_the_orphan():
    before = ["rib_airfoil", "rib_airfoil_te"]
    after = [curve("airfoil", "rib_airfoil")]
    assert orphaned(before, after) == ["rib_airfoil_te"]


def test_splitting_the_surface_orphans_the_whole_one():
    before = ["rib_airfoil"]
    after = [curve("airfoil_upper", "rib_airfoil_upper"), curve("airfoil_lower", "rib_airfoil_lower")]
    assert orphaned(before, after) == ["rib_airfoil"]


def test_adding_a_curve_orphans_nothing():
    before = ["rib_airfoil"]
    after = [curve("airfoil", "rib_airfoil"), curve("camber", "rib_camber")]
    assert orphaned(before, after) == []


# -- joining the surface to its trailing edge -------------------------------


def te_pair():
    return [
        curve("airfoil", "rib_airfoil"),
        curve("airfoil_te", "rib_airfoil_te"),
        curve("camber", "rib_camber"),
    ]


def test_the_surface_and_its_trailing_edge_are_joined(tmp_path):
    sw = FakeSolidWorks()
    result = push(sw, te_pair(), str(tmp_path), join_as="rib_airfoil_joined")

    assert result.joined == "rib_airfoil_joined"
    assert ("join", ("rib_airfoil", "rib_airfoil_te"), "rib_airfoil_joined") in sw.calls


def test_the_camber_is_left_out_of_the_join(tmp_path):
    sw = FakeSolidWorks()
    push(sw, te_pair(), str(tmp_path), join_as="rib_airfoil_joined")
    joined = [c for c in sw.calls if c[0] == "join"][0]
    assert "rib_camber" not in joined[1]


def test_the_join_happens_after_the_rebuild(tmp_path):
    """So the composite is built on curves that already hold the new points."""
    sw = FakeSolidWorks()
    push(sw, te_pair(), str(tmp_path), join_as="rib_airfoil_joined")
    assert indexes(sw.calls, "join")[0] > indexes(sw.calls, "rebuild")[0]


def test_a_second_push_does_not_join_again(tmp_path):
    """A composite is derived, so it follows its inputs and is made once."""
    sw = FakeSolidWorks()
    curves = te_pair()
    push(sw, curves, str(tmp_path), join_as="rib_airfoil_joined")
    sw.calls.clear()

    result = push(sw, curves, str(tmp_path), join_as="rib_airfoil_joined")
    assert indexes(sw.calls, "join") == []
    assert result.joined_now is False
    # Still named, so a record made before it existed learns about it.
    assert result.joined == "rib_airfoil_joined"


def test_auto_close_has_nothing_to_join(tmp_path, curves):
    sw = FakeSolidWorks()
    result = push(sw, curves, str(tmp_path), join_as="rib_airfoil_joined")
    assert result.joined == ""
    assert indexes(sw.calls, "join") == []


def test_split_halves_are_not_joined(tmp_path):
    """Two halves enclose no area, so joining them would say something untrue."""
    halves = [curve("airfoil_upper", "rib_upper"), curve("airfoil_lower", "rib_lower")]
    sw = FakeSolidWorks()
    result = push(sw, halves, str(tmp_path), join_as="rib_airfoil_joined")
    assert result.joined == ""


def test_a_refused_join_is_reported_and_does_not_sink_the_push(tmp_path):
    sw = FakeSolidWorks()
    sw.join_fails = True
    result = push(sw, te_pair(), str(tmp_path), join_as="rib_airfoil_joined")

    assert result.joined == ""
    assert len(result.inserted) == 3
    assert result.failures == [("rib_airfoil_joined", "SolidWorks would not join them")]


def test_a_join_solidworks_renames_is_reported(tmp_path):
    sw = FakeSolidWorks()
    sw.rename_to = {"rib_airfoil_joined": "CompCurve1"}
    result = push(sw, te_pair(), str(tmp_path), join_as="rib_airfoil_joined")
    assert result.joined == ""
    assert result.failures == [("rib_airfoil_joined", "SolidWorks named it 'CompCurve1' instead")]


def test_nothing_is_joined_when_inserting_is_off(tmp_path):
    """Joining creates a feature, so the same control governs it."""
    sw = FakeSolidWorks()
    result = push(sw, te_pair(), str(tmp_path), insert_missing=False, join_as="rib_airfoil_joined")
    assert result.joined == ""
    assert indexes(sw.calls, "join") == []


def test_the_join_still_happens_when_nothing_else_needed_doing(tmp_path):
    """The files can already be right and the curves already there — but with
    no composite yet, which is the case that used to slip through."""
    sw = FakeSolidWorks(features=["rib_airfoil", "rib_airfoil_te", "rib_camber"])
    curves = te_pair()
    push(sw, curves, str(tmp_path))          # writes the files, touches nothing
    sw.calls.clear()

    result = push(sw, curves, str(tmp_path), join_as="rib_airfoil_joined")
    assert result.unchanged == ["rib_airfoil", "rib_airfoil_te", "rib_camber"]
    assert result.joined == "rib_airfoil_joined"


def test_a_composite_from_an_earlier_run_is_recognised_not_remade(tmp_path):
    """Otherwise the app calls its own curve a stray and never records it."""
    sw = FakeSolidWorks(features=["rib_airfoil", "rib_airfoil_te", "rib_camber"])
    sw.composites.append("rib_airfoil_joined")

    result = push(sw, te_pair(), str(tmp_path), join_as="rib_airfoil_joined")
    assert result.joined == "rib_airfoil_joined"
    assert result.joined_now is False
    assert indexes(sw.calls, "join") == []
