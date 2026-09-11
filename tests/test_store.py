"""What the app remembers, and how it lines up against a real document."""

import json
import os

import pytest

from airfoil_converter import store, writer
from airfoil_converter.export import Curve, ExportSpec
from airfoil_converter.store import (
    DRIFTED,
    LINKED,
    MISSING,
    NOT_PUSHED,
    ORPHAN,
    RENAMED,
    RETIRED,
    CurveRecord,
    ExportRecord,
    Sidecar,
    StoreError,
    UnknownSchema,
    reconcile,
)


def curve(role="airfoil", feature="rib_airfoil", y=0.0):
    return Curve(
        role=role,
        points=[(0.0, y, 0.0), (10.0, y, 0.0), (10.0, y + 1.0, 0.0)],
        closed=False,
        feature=feature,
        filename=feature + ".sldcrv",
    )


def sidecar_with(*curve_records, folder=""):
    return Sidecar(
        part_path=r"C:\parts\Wing.SLDPRT",
        part_title="Wing.SLDPRT",
        exports=[ExportRecord(export_id="exp-1", stem="rib", output_folder=folder,
                              curves=list(curve_records))],
    )


# -- where it lives ---------------------------------------------------------


def test_the_sidecar_sits_beside_the_part():
    assert store.sidecar_path(r"C:\parts\Wing.SLDPRT") == r"C:\parts\Wing.airfoils.json"


def test_an_unsaved_part_says_why_it_cannot_be_remembered():
    with pytest.raises(StoreError, match="never been saved"):
        store.sidecar_path("")


# -- the file ---------------------------------------------------------------


def test_a_sidecar_round_trips():
    original = sidecar_with(CurveRecord(role="airfoil", feature="rib_airfoil",
                                        file="rib_airfoil.sldcrv", points=61, sha256="abc"))
    assert store.from_json(store.to_json(original)) == original


def test_the_settings_survive_the_round_trip():
    spec = ExportSpec(pitch="-2.5", target_chord="175", plane_mode="XZ", le_manual=True)
    record = store.record_from_export("exp-1", [curve()], spec, "rib", "rib.csv", "/out")
    back = store.from_json(store.to_json(Sidecar(exports=[record]))).exports[0]
    assert back.spec == spec


def test_writing_twice_gives_identical_bytes():
    """Hash comparison is only trustworthy if the writing is deterministic."""
    side = sidecar_with(CurveRecord(role="airfoil", feature="a", file="a.sldcrv"))
    assert store.to_json(side) == store.to_json(side)


def test_saving_an_unchanged_sidecar_leaves_the_file_alone(tmp_path, monkeypatch):
    path = str(tmp_path / "Wing.airfoils.json")
    side = sidecar_with(CurveRecord(role="airfoil", feature="a", file="a.sldcrv"))
    assert store.save(path, side) is True

    def fail(*args):
        raise AssertionError("os.replace was called for an unchanged sidecar")

    monkeypatch.setattr(store.os, "replace", fail)
    assert store.save(path, side) is False


def test_saving_leaves_no_temporary_behind(tmp_path):
    path = str(tmp_path / "Wing.airfoils.json")
    store.save(path, sidecar_with())
    assert [p.name for p in tmp_path.iterdir()] == ["Wing.airfoils.json"]


def test_loading_what_is_not_there_is_not_an_error(tmp_path):
    assert store.load(str(tmp_path / "nothing.json")) is None


def test_a_record_from_a_newer_version_is_refused_not_overwritten(tmp_path):
    path = tmp_path / "Wing.airfoils.json"
    path.write_text(json.dumps({"schemaVersion": 99, "exports": []}), encoding="utf-8")
    with pytest.raises(UnknownSchema, match="newer version"):
        store.load(str(path))
    assert path.exists()  # left exactly as it was


def test_an_unreadable_record_is_moved_aside_never_deleted(tmp_path):
    path = tmp_path / "Wing.airfoils.json"
    path.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(StoreError, match="moved to"):
        store.load(str(path))
    assert not path.exists()
    assert any(p.name.startswith("Wing.airfoils.json.bad-") for p in tmp_path.iterdir())


def test_unknown_keys_from_a_future_version_are_ignored():
    raw = json.dumps({
        "schemaVersion": 1,
        "exports": [{"export_id": "exp-1", "invented_later": 5,
                     "curves": [{"role": "airfoil", "feature": "a", "file": "a.sldcrv",
                                 "also_invented": True}]}],
    })
    side = store.from_json(raw)
    assert side.exports[0].curves[0].feature == "a"


# -- files are stored relative to the sidecar -------------------------------


def test_a_curve_file_resolves_against_its_folder():
    record = CurveRecord(role="airfoil", feature="a", file="a.sldcrv")
    assert store.resolve_file(record, os.sep + "out") == os.path.join(os.sep + "out", "a.sldcrv")


def test_an_absolute_stored_path_is_left_alone():
    absolute = os.path.abspath("elsewhere/a.sldcrv")
    record = CurveRecord(role="airfoil", feature="a", file=absolute)
    assert store.resolve_file(record, os.sep + "out") == absolute


# -- allocating an index ----------------------------------------------------


def test_the_first_record_of_a_source_gets_index_one():
    assert Sidecar().next_index("sd7037-il") == 1


def test_a_second_record_of_the_same_source_gets_the_next_index():
    side = Sidecar(exports=[ExportRecord(export_id="a", stem="sd7037-il", name_index=1)])
    assert side.next_index("sd7037-il") == 2


def test_a_different_source_starts_again_at_one():
    side = Sidecar(exports=[ExportRecord(export_id="a", stem="sd7037-il", name_index=1)])
    assert side.next_index("clarky") == 1


def test_a_gap_left_by_a_forgotten_record_is_reused():
    side = Sidecar(exports=[
        ExportRecord(export_id="a", stem="rib", name_index=1),
        ExportRecord(export_id="c", stem="rib", name_index=3),
    ])
    assert side.next_index("rib") == 2


# -- reconciliation ---------------------------------------------------------


def written(tmp_path, feature, points, sha=True):
    path = tmp_path / (feature + ".sldcrv")
    _, digest = writer.write_curve_if_changed(str(path), points)
    return CurveRecord(
        role="airfoil", feature=feature, file=feature + ".sldcrv",
        points=len(points), sha256=digest if sha else "",
        last_written="2026-01-01T00:00:00Z", last_pushed="2026-01-01T00:00:00Z",
    )


def test_a_curve_in_both_places_is_linked(tmp_path):
    record = written(tmp_path, "rib_airfoil", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    states = reconcile(sidecar_with(record), ["rib_airfoil"], str(tmp_path))
    assert [(s.feature, s.state) for s in states] == [("rib_airfoil", LINKED)]


def test_a_curve_gone_from_the_part_is_missing(tmp_path):
    record = written(tmp_path, "rib_airfoil", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    states = reconcile(sidecar_with(record), [], str(tmp_path))
    assert states[0].state == MISSING


def test_a_rename_is_followed_rather_than_shown_as_two_wrong_rows(tmp_path):
    """Without this it reads as one curve lost and one stranger found."""
    record = written(tmp_path, "rib_airfoil", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    states = reconcile(
        sidecar_with(record), ["RootRib"], str(tmp_path), renamed={"rib_airfoil": "RootRib"}
    )
    assert [(s.feature, s.state) for s in states] == [("rib_airfoil", RENAMED)]
    assert "RootRib" in states[0].detail


def test_a_curve_the_app_did_not_make_is_an_orphan(tmp_path):
    states = reconcile(sidecar_with(), ["SomebodyElsesCurve"], str(tmp_path))
    assert [(s.feature, s.state) for s in states] == [("SomebodyElsesCurve", ORPHAN)]
    assert states[0].record is None


def test_a_file_edited_outside_the_app_has_drifted(tmp_path):
    record = written(tmp_path, "rib_airfoil", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    (tmp_path / "rib_airfoil.sldcrv").write_bytes(b"9.000000mm\t0.000000mm\t0.000000mm\r\n")
    states = reconcile(sidecar_with(record), ["rib_airfoil"], str(tmp_path))
    assert states[0].state == DRIFTED


def test_a_file_newer_than_the_part_is_not_pushed(tmp_path):
    record = written(tmp_path, "rib_airfoil", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    record.last_written = "2026-02-01T00:00:00Z"
    record.last_pushed = "2026-01-01T00:00:00Z"
    states = reconcile(sidecar_with(record), ["rib_airfoil"], str(tmp_path))
    assert states[0].state == NOT_PUSHED


def test_a_retired_curve_is_listed_and_never_refreshed(tmp_path):
    record = written(tmp_path, "rib_airfoil_te", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    record.retired = True
    states = reconcile(sidecar_with(record), ["rib_airfoil_te"], str(tmp_path))
    assert states[0].state == RETIRED


def test_a_record_can_be_found_from_any_of_its_curves():
    side = sidecar_with(
        CurveRecord(role="airfoil", feature="rib_airfoil", file="a.sldcrv"),
        CurveRecord(role="camber", feature="rib_camber", file="b.sldcrv"),
    )
    assert side.find_by_feature("rib_camber").export_id == "exp-1"
    assert side.find_by_feature("nothing_like_it") is None


# -- derived curves ---------------------------------------------------------


def test_a_curve_with_no_file_of_its_own_is_derived():
    """SolidWorks builds the joined curve from two of ours, so no export ever
    produces it directly and no change of settings can orphan it."""
    written = CurveRecord(role="airfoil", feature="rib_airfoil", file="rib_airfoil.sldcrv")
    joined = CurveRecord(role="airfoil_joined", feature="rib_airfoil_joined", file="")
    assert written.is_derived is False
    assert joined.is_derived is True


def test_written_curves_leaves_the_derived_one_out():
    record = ExportRecord(
        export_id="exp-1",
        curves=[
            CurveRecord(role="airfoil", feature="a", file="a.sldcrv"),
            CurveRecord(role="airfoil_te", feature="a_te", file="a_te.sldcrv"),
            CurveRecord(role="airfoil_joined", feature="a_joined", file=""),
        ],
    )
    assert [c.feature for c in record.written_curves()] == ["a", "a_te"]
    assert [c.feature for c in record.live_curves()] == ["a", "a_te", "a_joined"]


def test_a_retired_curve_is_left_out_of_both():
    record = ExportRecord(
        export_id="exp-1",
        curves=[CurveRecord(role="airfoil_te", feature="a_te", file="a_te.sldcrv", retired=True)],
    )
    assert record.written_curves() == []
    assert record.live_curves() == []


# -- taking over curves that are already there ------------------------------


def test_a_feature_name_is_read_back_into_its_parts():
    assert store.parse_feature("n0012_airfoil") == ("n0012", "airfoil", 1)
    assert store.parse_feature("n0012_airfoil_te") == ("n0012", "airfoil_te", 1)
    assert store.parse_feature("n0012_camber_2") == ("n0012", "camber", 2)
    assert store.parse_feature("n0012_airfoil_joined_3") == ("n0012", "airfoil_joined", 3)


def test_a_name_this_app_did_not_make_is_not_claimed():
    assert store.parse_feature("Curve1") is None
    assert store.parse_feature("_airfoil") is None
    assert store.parse_feature("rib_spline") is None


def test_untracked_curves_group_into_the_exports_that_made_them():
    groups = store.adoptable(
        [
            "n0012_airfoil", "n0012_airfoil_te", "n0012_camber",
            "n0012_airfoil_2", "Curve1", "rib_airfoil",
        ],
        known=["rib_airfoil"],
    )
    assert [(g.stem, g.index, len(g.curves)) for g in groups] == [
        ("n0012", 1, 3),
        ("n0012", 2, 1),
    ]


def test_adopting_writes_a_record_that_knows_the_names_and_nothing_else(tmp_path):
    folder = str(tmp_path)
    (tmp_path / "n0012_airfoil.sldcrv").write_text("1\t2\t3\n")
    side = Sidecar(part_path=r"C:\parts\Wing.SLDPRT")

    made = store.adopt(
        side, ["n0012_airfoil", "n0012_airfoil_te", "n0012_airfoil_joined"], folder
    )

    assert len(made) == 1
    record = made[0]
    assert record.adopted and record.settings == {} and record.stem == "n0012"
    assert side.exports == [record]
    # The joined curve is derived, so it is recorded with no file of its own.
    assert [c.file for c in record.curves] == [
        "n0012_airfoil.sldcrv", "n0012_airfoil_te.sldcrv", ""
    ]
    # A file that is there is hashed, so a later edit to it still shows drift.
    assert record.curves[0].sha256 and not record.curves[1].sha256


def test_adopting_twice_claims_nothing_a_second_time():
    side = Sidecar()
    store.adopt(side, ["n0012_airfoil"])
    assert store.adopt(side, ["n0012_airfoil"]) == []
    assert len(side.exports) == 1


def test_an_adopted_record_takes_the_index_its_names_carry():
    side = Sidecar()
    store.adopt(side, ["n0012_airfoil_2"])
    assert side.exports[0].name_index == 2
    assert side.next_index("n0012") == 1


def test_records_made_before_the_part_was_saved_move_into_its_file():
    homeless = [ExportRecord(export_id="exp-1", stem="rib", curves=[
        CurveRecord(role="airfoil", feature="rib_airfoil", file="rib_airfoil.sldcrv")
    ])]
    side = Sidecar(part_path=r"C:\parts\Wing.SLDPRT")

    moved = store.carry_over(side, homeless, ["rib_airfoil", "Curve1"])

    assert moved == homeless
    assert side.exports == homeless


def test_records_do_not_follow_the_app_into_a_different_part():
    homeless = [ExportRecord(export_id="exp-1", stem="rib", curves=[
        CurveRecord(role="airfoil", feature="rib_airfoil", file="rib_airfoil.sldcrv")
    ])]
    side = Sidecar(part_path=r"C:\parts\Other.SLDPRT")

    assert store.carry_over(side, homeless, ["somebody_elses_curve"]) == []
    assert side.exports == []


def test_a_record_the_part_already_holds_is_not_carried_in_twice():
    record = ExportRecord(export_id="exp-1", stem="rib", curves=[
        CurveRecord(role="airfoil", feature="rib_airfoil", file="rib_airfoil.sldcrv")
    ])
    side = Sidecar(exports=[record])

    assert store.carry_over(side, [record], ["rib_airfoil"]) == []
    assert side.exports == [record]
