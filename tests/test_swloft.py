"""Lofting a wing in SolidWorks, tested against a fake with no CAD package."""

import os

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
        self.cap_fails = set()       # the profiles whose end will not cap
        self.cap_fails_at = set()    # the guide counts whose loft leaves a sliver
        self.held_by = 0             # guides the surface loft standing now has
        self.knit_fails = False
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
        self.calls.append(("loft", name) if solid else ("surface", name, len(guides)))
        if not solid:
            self.held_by = len(guides)
        if name in (self.loft_fails if solid else self.surface_fails):
            raise SolidWorksError("no loft")
        self.names.append(name)
        self.lofts.append(name)
        return name

    def cap_end(self, loft, profile, name):
        self.calls.append(("cap", loft, profile, name))
        if profile in self.cap_fails or self.held_by in self.cap_fails_at:
            raise SolidWorksError("no cap")
        # A planar cap is not one of the loft types features_of_type is asked for.
        self.names.append(name)
        return name

    def knit_to_solid(self, surfaces, name, solid=True):
        self.calls.append(("knit", tuple(surfaces), name))
        if self.knit_fails:
            raise SolidWorksError("no knit")
        # What it knits stays in the tree, absorbed but still calling itself a
        # lofted surface; the knit itself is a type of its own.
        self.names.append(name)
        return name

    def rename_feature(self, current, new):
        self.calls.append(("rename", current, new))
        for held in (self.names, self.lofts):
            if current in held:
                held[held.index(current)] = new
        return new

    def delete_feature(self, name):
        self.calls.append(("delete", name))
        for held in (self.names, self.lofts):
            if name in held:
                held.remove(name)

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

# A wing's own guides, in the order the app makes them: the two edges, then the
# surface guides by their station along the chord.
LADDER_GUIDES = ("w_le", "w_upper_02", "w_lower_02", "w_upper_3p5", "w_lower_3p5",
                 "w_upper_10", "w_te_upper", "w_te_lower")
LADDER_PLAN = LoftPlan("w_loft", ("w_root_joined", "w_tip_joined"), LADDER_GUIDES)


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


def test_replacing_a_capped_loft_clears_what_it_was_made_of(tmp_path):
    """Four features under three names: deleting the knit alone would leave the
    surface and both caps holding the names the next attempt wants."""
    sw = FakeSolidWorks(features=["wing_loft", "wing_loft_surface",
                                  "wing_loft_root_cap", "wing_loft_tip_cap"])
    sw.lofts = ["wing_loft"]
    loft_and_export(sw, PLANS[:1], str(tmp_path))

    assert [c for c in sw.calls if c[0] == "delete"] == [
        ("delete", "wing_loft"),
        ("delete", "wing_loft_surface"),
        ("delete", "wing_loft_root_cap"),
        ("delete", "wing_loft_tip_cap"),
    ]


def test_a_refused_solid_is_made_as_a_surface_and_capped(tmp_path):
    """The same geometry, built the other way round: the surface loft, a patch
    across each end curve, and the three knitted into a solid."""
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_inner_loft"}
    first, second = loft_and_export(sw, PLANS, str(tmp_path))

    assert first.ok and not first.surface and not first.capped
    assert second.ok and second.capped and not second.surface
    assert second.feature == "wing_inner_loft"      # the knit carries the plan's name
    assert [c for c in sw.calls if c[0] in ("surface", "cap", "knit")] == [
        ("surface", "wing_inner_loft_surface", 1),
        ("cap", "wing_inner_loft_surface", "c", "wing_inner_loft_root_cap"),
        ("cap", "wing_inner_loft_surface", "d", "wing_inner_loft_tip_cap"),
        ("knit",
         ("wing_inner_loft_surface", "wing_inner_loft_root_cap", "wing_inner_loft_tip_cap"),
         "wing_inner_loft"),
    ]
    assert "capped into a solid" in second.describe()


def test_a_capped_loft_is_not_suppressed_by_its_own_pieces(tmp_path):
    """The surface inside a capped solid still calls itself a lofted surface.
    Suppressing it while another loft is written would take the solid with it."""
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_inner_loft"}
    results = loft_and_export(sw, PLANS, str(tmp_path))
    assert results[1].capped

    written = {c[1].split(os.sep)[-1]: c[2] for c in sw.calls if c[0] == "step"}
    assert "wing_inner_loft_surface" not in written["wing_loft.STEP"]
    assert written["wing_loft.STEP"] == frozenset()
    assert written["wing_inner_loft.STEP"] == frozenset({"wing_loft"})


def test_an_accepted_solid_is_not_capped(tmp_path):
    sw = FakeSolidWorks()
    first, _ = loft_and_export(sw, PLANS, str(tmp_path))
    assert first.ok and not first.capped
    assert not [c for c in sw.calls if c[0] in ("surface", "cap", "knit")]


def test_an_end_that_will_not_cap_leaves_the_surface_as_it_was():
    """Never a half-built construction: the one cap that was made comes out
    again and the surface goes back under the plan's own name."""
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_loft"}
    sw.cap_fails = {"b"}                 # the tip profile of the first plan
    first, _ = loft_in_part(sw, PLANS)

    assert first.ok and first.surface and not first.capped
    assert first.feature == "wing_loft"
    assert ("cap", "wing_loft_surface", "a", "wing_loft_root_cap") in sw.calls
    assert ("delete", "wing_loft_root_cap") in sw.calls
    assert ("rename", "wing_loft_surface", "wing_loft") in sw.calls
    assert "wing_loft_root_cap" not in sw.names
    assert "no cap" in first.note
    assert "as a surface" in first.describe() and "[no cap]" in first.describe()


def test_a_knit_that_will_not_form_a_solid_leaves_the_surface_as_it_was():
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_loft"}
    sw.knit_fails = True
    first, _ = loft_in_part(sw, PLANS)

    assert first.ok and first.surface and not first.capped
    assert first.feature == "wing_loft"
    assert [c for c in sw.calls if c[0] == "delete"] == [
        ("delete", "wing_loft_root_cap"), ("delete", "wing_loft_tip_cap"),
    ]
    assert ("rename", "wing_loft_surface", "wing_loft") in sw.calls
    assert "no knit" in first.note


def test_a_cap_that_cannot_be_taken_out_again_is_named_in_the_note():
    """A feature left in the user's part under a name nothing else knows is
    worth saying out loud, however tidy the message would be without it."""
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_loft"}
    sw.knit_fails = True

    def refuse(name):
        sw.calls.append(("delete", name))
        raise SolidWorksError("cannot delete")

    sw.delete_feature = refuse
    first, _ = loft_in_part(sw, PLANS)
    assert "left in the part as wing_loft_root_cap, wing_loft_tip_cap" in first.note


def test_the_surface_fallback_can_be_turned_off(tmp_path):
    sw = FakeSolidWorks()
    sw.loft_fails = {"wing_inner_loft"}
    _, second = loft_and_export(sw, PLANS, str(tmp_path), surface_fallback=False)
    assert not second.ok
    assert not [c for c in sw.calls if c[0] in ("surface", "cap", "knit")]


def test_a_failed_loft_does_not_stop_the_others(tmp_path):
    sw = FakeSolidWorks()
    sw.loft_fails, sw.surface_fails = {"wing_loft"}, {"wing_loft_surface"}
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
    sw.cap_fails = {"a", "b"}
    first, _ = loft_in_part(sw, PLANS)
    assert first.surface and "as a surface" in first.describe()


def test_a_loft_that_fails_in_the_part_says_why():
    sw = FakeSolidWorks()
    sw.loft_fails, sw.surface_fails = {"wing_loft"}, {"wing_loft_surface"}
    first, _ = loft_in_part(sw, PLANS)
    assert "could not be lofted: no loft" in first.describe()


# -- taking guides off until the tip will close -----------------------------


def test_a_surface_guide_is_told_from_an_edge_guide_by_its_name():
    """``_upper_3p5`` is 3.5% along the upper surface. The trailing edge's own
    guides end in the same words and carry no station."""
    assert swloft.surface_guide("w_upper_02") == ("upper", 2.0)
    assert swloft.surface_guide("wing_inner_1.4mm_lower_3p5") == ("lower", 3.5)
    assert swloft.surface_guide("w_upper_95") == ("upper", 95.0)
    assert swloft.surface_guide("w_te_upper") is None
    assert swloft.surface_guide("w_te_lower") is None
    assert swloft.surface_guide("w_le") is None


def test_the_ladder_takes_the_nose_guides_off_a_rung_at_a_time():
    rungs = swloft.guide_ladder(LADDER_GUIDES)
    assert [len(guides) for guides, _ in rungs] == [8, 7, 7, 6, 4, 3]
    assert rungs[0][1] == ""
    assert rungs[1][0] == tuple(g for g in LADDER_GUIDES if g != "w_lower_02")
    assert "lower 2%" in rungs[1][1]
    assert rungs[2][0] == tuple(g for g in LADDER_GUIDES if g != "w_upper_02")
    assert rungs[4][0] == ("w_le", "w_upper_10", "w_te_upper", "w_te_lower")
    assert rungs[5][0] == ("w_le", "w_te_upper", "w_te_lower")
    # Whatever is dropped, what is left keeps the order the app made it in.
    for guides, _ in rungs:
        assert list(guides) == [g for g in LADDER_GUIDES if g in guides]


def test_a_rung_that_would_drop_nothing_is_not_climbed_twice():
    """A wing with no guides near its nose has only the last rung to offer."""
    rungs = swloft.guide_ladder(("w_le", "w_upper_50", "w_te_upper"))
    assert [len(guides) for guides, _ in rungs] == [3, 2]


def test_a_tip_that_will_not_cap_is_lofted_again_with_fewer_guides():
    """One guide's doing: on the wing this was found on, the lower one at 2% of
    the chord left a sliver face along the tip that no surface would span."""
    sw = FakeSolidWorks()
    sw.loft_fails = {"w_loft"}
    sw.cap_fails_at = {8}
    first, = loft_in_part(sw, [LADDER_PLAN])

    assert first.capped and not first.surface
    assert first.guides_used == 7 and first.feature == "w_loft"
    assert first.describe() == (
        "w_loft lofted as a surface and capped into a solid "
        "(7 of 8 guides; the lower 2% guide was dropped to close the tip).")
    assert [c for c in sw.calls if c[0] in ("loft", "surface", "cap", "knit", "delete")] == [
        ("loft", "w_loft"),
        ("surface", "w_loft_surface", 8),
        ("cap", "w_loft_surface", "w_root_joined", "w_loft_root_cap"),
        ("delete", "w_loft_surface"),
        ("surface", "w_loft_surface", 7),
        ("cap", "w_loft_surface", "w_root_joined", "w_loft_root_cap"),
        ("cap", "w_loft_surface", "w_tip_joined", "w_loft_tip_cap"),
        ("knit", ("w_loft_surface", "w_loft_root_cap", "w_loft_tip_cap"), "w_loft"),
    ]


def test_the_ladder_carries_on_to_the_rung_that_works():
    """Dropping the upper 2% guide instead did nothing on the real wing, so the
    rung after it drops both."""
    sw = FakeSolidWorks()
    sw.loft_fails = {"w_loft"}
    sw.cap_fails_at = {8, 7}
    first, = loft_in_part(sw, [LADDER_PLAN])

    assert first.capped and first.guides_used == 6
    assert "both 2% guides were dropped" in first.describe()
    assert [c[2] for c in sw.calls if c[0] == "surface"] == [8, 7, 7, 6]


def test_when_no_rung_closes_the_tip_the_wing_s_own_surface_is_what_stays():
    sw = FakeSolidWorks()
    sw.loft_fails = {"w_loft"}
    sw.cap_fails_at = {8, 7, 6, 4, 3}
    first, = loft_in_part(sw, [LADDER_PLAN])

    assert first.ok and first.surface and not first.capped
    assert first.feature == "w_loft" and first.guides_used == 8
    assert [c[2] for c in sw.calls if c[0] == "surface"] == [8, 7, 7, 6, 4, 3, 8]
    assert "no cap" in first.note
    assert first.describe().startswith("w_loft lofted as a surface: SolidWorks would not")
    assert "w_loft_surface" not in sw.names and "w_loft" in sw.names


def test_a_wing_whose_full_guides_cap_is_not_put_through_the_ladder():
    sw = FakeSolidWorks()
    sw.loft_fails = {"w_loft"}
    first, = loft_in_part(sw, [LADDER_PLAN])

    assert first.capped and first.guides_used == 8 and not first.dropped
    assert first.describe().endswith("capped into a solid (8 guides).")
    assert [c[2] for c in sw.calls if c[0] == "surface"] == [8]
