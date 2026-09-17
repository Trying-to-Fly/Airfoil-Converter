"""Lofting a wing in SolidWorks, tested against a fake with no CAD package."""

import pytest

from airfoil_converter import export, store, swloft
from airfoil_converter.store import CurveRecord
from airfoil_converter.swcom import DocInfo, SolidWorksError
from airfoil_converter.swloft import LoftPlan, loft_and_export, loft_in_part, wing_plan


def rib(export_id, stem, joined=True):
    curves = [CurveRecord(role=export.ROLE_AIRFOIL, feature=f"{stem}_airfoil", file="a",
                          closed=not joined)]
    if joined:
        curves += [
            CurveRecord(role=export.ROLE_TE, feature=f"{stem}_airfoil_te", file="t"),
            CurveRecord(role=export.ROLE_JOINED, feature=f"{stem}_airfoil_joined", file=""),
        ]
    return store.ExportRecord(export_id=export_id, stem=stem, curves=curves)


def guides(stem):
    return [
        CurveRecord(role=export.ROLE_WING_TE_UPPER, feature=f"{stem}_te_upper", file="x"),
        CurveRecord(role=export.ROLE_WING_SURFACE, feature=f"{stem}_upper_05", file="x"),
        CurveRecord(role=export.ROLE_WING_LE, feature=f"{stem}_le", file="x"),
        CurveRecord(role=export.ROLE_WING_TE_LOWER, feature=f"{stem}_te_lower", file="x"),
        CurveRecord(role=export.ROLE_WING_SURFACE, feature=f"{stem}_lower_05", file="x"),
    ]


def outer_wing():
    return store.WingRecord(
        export_id="wing-1", stem="wing",
        settings=export.WingSpec(ribs=("root", "tip")).to_dict(),
        curves=guides("wing"),
    )


def inner_wing():
    sections = []
    for tag in ("root", "tip"):
        sections += [
            CurveRecord(role=export.ROLE_SECTION, feature=f"wing_inner_{tag}", file="x"),
            CurveRecord(role=export.ROLE_SECTION_TE, feature=f"wing_inner_{tag}_te", file="x"),
            CurveRecord(role=export.ROLE_SECTION_JOINED, feature=f"wing_inner_{tag}_joined", file=""),
        ]
    return store.WingRecord(
        export_id="wing-2", stem="wing_inner",
        settings=export.WingSpec(ribs=("root", "tip"), offset="2.5").to_dict(),
        curves=sections + guides("wing_inner"),
    )


def part():
    return store.Sidecar(exports=[rib("root", "r275"), rib("tip", "r136")],
                         wings=[outer_wing(), inner_wing()])


def test_an_outer_wing_lofts_its_ribs_joined_curves_along_every_guide():
    plan = wing_plan(outer_wing(), part())
    assert plan.name == "wing_loft"
    assert plan.profiles == ("r275_airfoil_joined", "r136_airfoil_joined")
    assert plan.guides == ("wing_le", "wing_upper_05", "wing_lower_05",
                           "wing_te_upper", "wing_te_lower")


def test_the_ribs_go_in_the_order_given():
    plan = wing_plan(outer_wing(), part(), rib_order=["tip", "root"])
    assert plan.profiles == ("r136_airfoil_joined", "r275_airfoil_joined")


def test_a_rib_without_a_trailing_edge_line_offers_its_closed_outline():
    sidecar = store.Sidecar(exports=[rib("root", "a", joined=False), rib("tip", "b", joined=False)])
    assert wing_plan(outer_wing(), sidecar).profiles == ("a_airfoil", "b_airfoil")


def test_an_offset_wing_lofts_its_own_joined_sections():
    plan = wing_plan(inner_wing(), part())
    assert plan.name == "wing_inner_loft"
    assert plan.profiles == ("wing_inner_root_joined", "wing_inner_tip_joined")
    assert "wing_inner_le" in plan.guides and len(plan.guides) == 5


def test_retired_curves_are_left_out():
    wing = inner_wing()
    wing.curves.append(CurveRecord(role=export.ROLE_SECTION, feature="wing_inner_s03",
                                   file="x", retired=True))
    wing.curves.append(CurveRecord(role=export.ROLE_WING_SURFACE, feature="wing_inner_upper_99",
                                   file="x", retired=True))
    plan = wing_plan(wing, part())
    assert "wing_inner_s03" not in plan.profiles
    assert "wing_inner_upper_99" not in plan.guides


def test_a_missing_rib_is_refused():
    with pytest.raises(export.InputError):
        wing_plan(outer_wing(), store.Sidecar(exports=[rib("root", "a")]))


class FakeSolidWorks:
    def __init__(self, features=()):
        self.doc = DocInfo(title="Part1", path=r"C:\p\Part1.SLDPRT", doc_type=1)
        self.names = list(features)
        self.lofts = [n for n in features if n.startswith("Loft")]
        self.suppressed = set()
        self.calls = []
        self.loft_fails = set()
        self.surface_fails = set()
        self.export_fails = False
        self.sketching = False

    def active_document(self):
        return self.doc

    def editing_sketch(self):
        return self.sketching

    def feature_names(self):
        return list(self.names)

    def features_of_type(self, *type_names):
        return list(self.lofts)

    def insert_loft(self, profiles, guides, name, merge=False, keep_tangency=True,
                    guide_influence=0, solid=True):
        self.calls.append(("loft" if solid else "surface", name))
        if name in (self.loft_fails if solid else self.surface_fails):
            raise SolidWorksError("no loft")
        self.names.append(name)
        self.lofts.append(name)
        return name

    def delete_feature(self, name):
        self.calls.append(("delete", name))
        self.names.remove(name)
        self.lofts.remove(name)

    def set_suppressed(self, name, suppressed):
        self.calls.append(("suppress" if suppressed else "unsuppress", name))
        (self.suppressed.add if suppressed else self.suppressed.discard)(name)

    def rebuild(self):
        return True

    def export_step(self, path):
        self.calls.append(("step", path, frozenset(self.suppressed)))
        if self.export_fails:
            raise SolidWorksError("no step")


PLANS = [LoftPlan("wing_loft", ("a", "b"), ("g",)), LoftPlan("wing_inner_loft", ("c", "d"), ("h",))]


def test_each_loft_is_written_alone(tmp_path):
    sw = FakeSolidWorks(features=["Loft1"])
    results = loft_and_export(sw, PLANS, str(tmp_path))
    assert all(r.ok for r in results)
    steps = [c for c in sw.calls if c[0] == "step"]
    assert steps[0][1].endswith("wing_loft.STEP")
    assert steps[0][2] == {"Loft1", "wing_inner_loft"}
    assert steps[1][2] == {"Loft1", "wing_loft"}
    assert sw.suppressed == set()


def test_a_loft_of_the_same_name_is_replaced(tmp_path):
    sw = FakeSolidWorks(features=["wing_loft"])
    sw.lofts = ["wing_loft"]
    loft_and_export(sw, PLANS[:1], str(tmp_path))
    assert sw.calls[:2] == [("delete", "wing_loft"), ("loft", "wing_loft")]


def test_a_refused_solid_is_made_as_a_surface(tmp_path):
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_inner_loft"}
    first, second = loft_and_export(sw, PLANS, str(tmp_path))
    assert first.ok and not first.surface
    assert second.ok and second.surface
    assert ("surface", "wing_inner_loft") in sw.calls


def test_the_surface_fallback_can_be_turned_off(tmp_path):
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_inner_loft"}
    _, second = loft_and_export(sw, PLANS, str(tmp_path), surface_fallback=False)
    assert not second.ok
    assert ("surface", "wing_inner_loft") not in sw.calls


def test_a_failed_loft_does_not_stop_the_others(tmp_path):
    sw = FakeSolidWorks()
    sw.loft_fails = sw.surface_fails = {"wing_loft"}
    first, second = loft_and_export(sw, PLANS, str(tmp_path))
    assert not first.ok and "no loft" in first.error
    assert second.ok and second.step.endswith("wing_inner_loft.STEP")


def test_everything_is_unsuppressed_even_when_the_export_fails(tmp_path):
    sw = FakeSolidWorks(features=["Loft1"])
    sw.export_fails = True
    results = loft_and_export(sw, PLANS, str(tmp_path))
    assert not any(r.ok for r in results)
    assert sw.suppressed == set()


def test_nothing_is_done_while_a_sketch_is_open(tmp_path):
    sw = FakeSolidWorks()
    sw.sketching = True
    with pytest.raises(SolidWorksError):
        loft_and_export(sw, PLANS, str(tmp_path))
    assert sw.calls == []


def test_a_section_in_two_halves_is_one_profile():
    wing = inner_wing()
    for curve in wing.curves:
        if curve.feature == "wing_inner_tip":
            curve.role, curve.feature = export.ROLE_SECTION_UPPER, "wing_inner_tip_upper"
    wing.curves.append(CurveRecord(role=export.ROLE_SECTION_LOWER,
                                   feature="wing_inner_tip_lower", file="x"))
    assert wing_plan(wing, part()).profiles == ("wing_inner_root_joined", "wing_inner_tip_joined")


# -- lofting in the part being worked on --------------------------------------


def test_lofting_in_the_part_makes_each_loft_and_writes_no_step():
    sw = FakeSolidWorks()
    results = loft_in_part(sw, PLANS)
    assert [r.feature for r in results] == ["wing_loft", "wing_inner_loft"]
    assert not any(c[0] == "step" for c in sw.calls)
    assert all("lofted through" in r.describe() for r in results)


def test_a_loft_already_in_the_part_is_left_alone():
    """Something may be built on it, and it follows its curves anyway."""
    sw = FakeSolidWorks(features=["wing_loft"])
    sw.lofts = ["wing_loft"]
    first, second = loft_in_part(sw, PLANS)
    assert first.kept and first.ok
    assert ("delete", "wing_loft") not in sw.calls
    assert ("loft", "wing_loft") not in sw.calls
    assert "already in the part" in first.describe()
    assert second.feature == "wing_inner_loft"


def test_a_surface_made_in_the_part_says_so():
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_loft"}
    first, _ = loft_in_part(sw, PLANS)
    assert first.surface and "as a surface" in first.describe()


def test_a_loft_that_fails_in_the_part_says_why():
    sw = FakeSolidWorks()
    sw.loft_fails = sw.surface_fails = {"wing_loft"}
    first, _ = loft_in_part(sw, PLANS)
    assert "could not be lofted: no loft" in first.describe()
