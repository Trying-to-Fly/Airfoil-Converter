"""One section through the server: read, finished, exported as a rib, and listed.

The rib a tool exports has to be the rib the window exports: the same curve
bytes, and the same record. The last tests run the window's own Export code —
``ConverterApp._export_unsafe`` and ``_remember``, on a stand-in for the
window's widgets — and compare every byte.
"""

import dataclasses
import os
import uuid

import pytest

pytest.importorskip("mcp.server.mcpserver")

from airfoil_converter import export, store, writer  # noqa: E402

from mcp_tools import (  # noqa: E402
    anyio_backend,  # noqa: F401
    call,
    refused,
    rib_spec,
    session,
    workspace_with_sample,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def workspace(tmp_path):
    return workspace_with_sample(tmp_path / "ws")


def files_in(folder):
    return sorted(os.listdir(folder))


def snapshot_all(folder):
    """Every file under a folder, by its path in it, and its bytes."""
    taken = {}
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                taken[os.path.relpath(path, folder)] = handle.read()
    return taken


def restore_all(folder, kept):
    for name in set(snapshot_all(folder)) - set(kept):
        os.remove(os.path.join(folder, name))
    for name, data in kept.items():
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(data)


# -- reading -------------------------------------------------------------------


async def test_a_csv_reads_as_its_numbers(workspace):
    async with session(workspace) as client:
        answer = await call(client, "read_section", source="sd7037-il.csv")
    assert answer["kind"] == "csv"
    assert answer["name"] == "SD7037-092-88"
    assert answer["metadata"]["Chord(mm)"] == "175"
    assert answer["chord_mm"] == 175.0
    assert answer["point_count"] == 61
    assert answer["closed"] and answer["te_gap_mm"] == 0.0
    assert answer["trailing_edge"] == [[175.0, 0.0], [175.0, 0.0]]
    assert answer["leading_edge"][0] == pytest.approx(0.0, abs=0.1)
    # SD7037 is 9.2 % thick, at about 29 % of the chord.
    assert answer["max_thickness_percent"] == pytest.approx(9.2, abs=0.05)
    assert answer["max_thickness_at_percent"] == pytest.approx(29.0, abs=1.0)
    assert answer["camber_point_count"] == 31
    assert "airfoil" not in answer and "plane" not in answer


async def test_points_come_only_when_asked_and_at_the_writers_precision(workspace):
    async with session(workspace) as client:
        answer = await call(client, "read_section", source="sd7037-il.csv", include_points=True)
    points = answer["airfoil"]["points"]
    assert len(points) == 61 and not answer["airfoil"]["truncated"]
    for point in points:
        for value in point:
            assert round(value, writer.DECIMALS) == value
    assert len(answer["camber"]["points"]) == 31


async def test_a_curve_file_reads_back_onto_its_plane(workspace):
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="rib", spec=rib_spec(200, x=-50))
        answer = await call(client, "read_section", source="rib_airfoil.sldcrv")
    assert answer["kind"] == "curve"
    assert answer["chord_mm"] == pytest.approx(200.0, abs=0.1)
    plane = answer["plane"]
    assert plane["normal"] == pytest.approx([1.0, 0.0, 0.0]) or \
        plane["normal"] == pytest.approx([-1.0, 0.0, 0.0])
    assert plane["origin"][0] == pytest.approx(-50.0)
    # Read off the points, so only as true as the point spacing at the nose.
    assert plane["chord_direction"] == pytest.approx([0.0, 0.0, -1.0], abs=0.01)


# -- finishing ------------------------------------------------------------------


async def test_finishing_writes_nothing_and_says_what_an_export_would_make(workspace):
    async with session(workspace) as client:
        answer = await call(client, "finish_section", source="sd7037-il.csv",
                            spec={"target_chord": 200, "te_mode": export.TE_LINE,
                                  "te_thickness": "0.5", "keep_chord": True})
    assert files_in(workspace) == ["sd7037-il.csv"]
    assert answer["settings"]["target_chord"] == "200"
    assert [c["role"] for c in answer["curves"]] == ["airfoil", "airfoil_te", "camber"]
    assert [c["feature"] for c in answer["curves"]] == [
        "sd7037-il_airfoil", "sd7037-il_airfoil_te", "sd7037-il_camber",
    ]
    assert answer["chord_mm"] == pytest.approx(200.0, abs=0.05)
    assert answer["te_thickness_mm"] == pytest.approx(0.5, abs=1e-6)
    te = answer["curves"][1]
    assert te["point_count"] == 2 and te["ends_gap_mm"] == pytest.approx(0.5, abs=1e-6)
    box = answer["curves"][0]["bounding_box"]
    assert box["max"][0] - box["min"][0] == pytest.approx(200.0, abs=0.05)


async def test_without_keep_chord_the_cut_shortens_the_section(workspace):
    async with session(workspace) as client:
        answer = await call(client, "finish_section", source="sd7037-il.csv",
                            spec={"te_thickness": 1.5, "export_camber": False})
    assert answer["chord_mm"] < 170.0
    assert answer["te_thickness_mm"] == pytest.approx(1.5, abs=1e-6)
    assert [c["closed"] for c in answer["curves"]] == [True]


async def test_the_reference_point_places_the_section(workspace):
    async with session(workspace) as client:
        answer = await call(client, "finish_section", source="sd7037-il.csv",
                            spec={"reference": 25, "leading_edge": [10, 20, 30]})
    placement = answer["placement"]
    assert placement["reference_point"] == [10.0, 20.0, 30.0]
    assert placement["nose"] == pytest.approx([10.0 - 0.25 * 175.0, 20.0, 30.0])


# -- exporting --------------------------------------------------------------------


async def test_an_export_writes_its_curves_and_records_the_rib(workspace):
    async with session(workspace) as client:
        answer = await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                            name="root", folder="curves", spec=rib_spec(200))
    assert answer["new"] and answer["name"] == "root" and answer["index"] == 1
    assert answer["record"] == "part.airfoils.json"
    assert answer["folder"] == "curves"
    assert files_in(os.path.join(workspace, "curves")) == ["root_airfoil.sldcrv"]
    (curve,) = answer["curves"]
    assert curve["file"] == "curves/root_airfoil.sldcrv" and curve["changed"]

    sidecar = store.load(os.path.join(workspace, "part.airfoils.json"))
    (record,) = sidecar.exports
    assert record.export_id == answer["rib"]
    assert record.source == os.path.realpath(os.path.join(workspace, "sd7037-il.csv"))
    assert record.output_folder == os.path.realpath(os.path.join(workspace, "curves"))
    assert record.spec.target_chord == "200"
    with open(os.path.join(workspace, "curves", "root_airfoil.sldcrv"), "rb") as handle:
        assert writer.sha256_hex(handle.read()) == record.curves[0].sha256 == curve["sha256"]
    assert sidecar.part_path == os.path.realpath(os.path.join(workspace, "part.SLDPRT"))
    assert sidecar.written_by.startswith("airfoil-converter ")


async def test_a_second_rib_of_the_same_name_takes_the_next_index(workspace):
    async with session(workspace) as client:
        first = await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv")
        second = await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv")
    assert (first["name"], second["name"]) == ("sd7037-il", "sd7037-il_2")
    assert second["curves"][0]["feature"] == "sd7037-il_airfoil_2"


async def test_new_rib_creates_separate_curve_and_project_folders(workspace):
    async with session(workspace) as client:
        answer = await call(client, "export_rib", project="project/part.SLDPRT",
                            source="sd7037-il.csv", name="root", folder="curves",
                            spec=rib_spec(200))
    assert answer["record"] == "project/part.airfoils.json"
    assert os.path.isfile(os.path.join(workspace, answer["record"]))
    assert os.path.isfile(os.path.join(workspace, answer["curves"][0]["file"]))


async def test_obstructed_project_folder_refuses_rib_before_creating_curve_folder(workspace):
    with open(os.path.join(workspace, "project"), "w") as handle:
        handle.write("keep")
    async with session(workspace) as client:
        text = await refused(client, "export_rib", project="project/nested/part.SLDPRT",
                             source="sd7037-il.csv", name="root", folder="curves",
                             spec=rib_spec(200))
    assert "project is a file" in text
    assert not os.path.exists(os.path.join(workspace, "curves"))


@pytest.mark.parametrize("folder", ["part.airfoils.json", "part.airfoils.json.tmp/nested"])
async def test_rib_refuses_curve_folder_conflicting_with_planned_project_file(workspace, folder):
    async with session(workspace) as client:
        text = await refused(client, "export_rib", project="part.SLDPRT",
                             source="sd7037-il.csv", name="root", folder=folder,
                             spec=rib_spec(200))
    assert "both a file and a folder" in text
    assert os.listdir(workspace) == ["sd7037-il.csv"]


@pytest.mark.parametrize(("project", "folder", "message"), [
    ("part.SLDPRT", "part.SLDPRT/curves", "would use part.SLDPRT as a folder"),
    ("root_airfoil.sldcrv", ".", "would overwrite root_airfoil.sldcrv"),
])
async def test_rib_refuses_to_occupy_part_path(workspace, project, folder, message):
    async with session(workspace) as client:
        text = await refused(client, "export_rib", project=project, folder=folder,
                             source="sd7037-il.csv", name="root", spec=rib_spec(200))
    assert message in text
    assert os.listdir(workspace) == ["sd7037-il.csv"]


async def test_exporting_a_rib_again_keeps_its_identity_and_changes_only_what_changed(workspace):
    async with session(workspace) as client:
        first = await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                           name="root", spec=rib_spec(200, export_camber=True))
        same = await call(client, "export_rib", project="part.SLDPRT", rib="root")
        assert [c["changed"] for c in same["curves"]] == [False, False]
        assert same["rib"] == first["rib"] and not same["new"]
        moved = await call(client, "export_rib", project="part.SLDPRT", rib=first["rib"],
                           spec={"pitch": "3"})
        assert moved["settings"]["target_chord"] == "200"  # the rib's own settings, not defaults
        assert [c["changed"] for c in moved["curves"]] == [True, True]
        text = await refused(client, "export_rib", project="part.SLDPRT", rib="root", name="tip")
        assert "keeps the name it was made with" in text
        text = await refused(client, "export_rib", project="part.SLDPRT", rib="nothing")
        assert "no rib nothing" in text


async def test_a_curve_the_new_settings_no_longer_make_is_retired_not_forgotten(workspace):
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="root", spec=rib_spec(200, te_mode=export.TE_LINE, te_thickness="0.5"))
        answer = await call(client, "export_rib", project="part.SLDPRT", rib="root",
                            spec={"te_mode": export.TE_CLOSE}, folder="new")
        assert answer["retired"] == ["root_airfoil_te"]
        listed = await call(client, "list_project", project="part.SLDPRT")
    curves = {c["feature"]: c for c in listed["ribs"][0]["curves"]}
    retired = curves["root_airfoil_te"]
    assert retired["retired"] and retired["present"] and retired["matches"]
    assert retired["file"] == "root_airfoil_te.sldcrv"
    assert not curves["root_airfoil"]["retired"]
    assert curves["root_airfoil"]["file"] == "new/root_airfoil.sldcrv"


async def test_a_project_with_no_record_is_not_invented_by_a_read(workspace):
    async with session(workspace) as client:
        text = await refused(client, "list_project", project="part.SLDPRT")
    assert "no record yet" in text
    assert files_in(workspace) == ["sd7037-il.csv"]


async def test_listing_says_which_files_are_there_and_which_were_changed(workspace):
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="root", spec=rib_spec(200, export_camber=True))
        with open(os.path.join(workspace, "root_camber.sldcrv"), "ab") as handle:
            handle.write(b"0mm\t0mm\t0mm\r\n")
        os.remove(os.path.join(workspace, "root_airfoil.sldcrv"))
        listed = await call(client, "list_project", project="part.SLDPRT")
    (rib,) = listed["ribs"]
    assert rib["name"] == "root" and rib["source"] == "sd7037-il.csv" and rib["source_present"]
    states = {c["feature"]: (c["present"], c["matches"]) for c in rib["curves"]}
    assert states == {"root_airfoil": (False, None), "root_camber": (True, False)}
    assert listed["wings"] == [] and not listed["part_present"]


async def test_a_name_is_stripped_as_the_window_strips_it(workspace):
    async with session(workspace) as client:
        first = await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                           name="root", spec=rib_spec(200))
        second = await call(client, "export_rib", project="part.SLDPRT",
                            source="sd7037-il.csv", name="root ", spec=rib_spec(150))
    assert (first["name"], second["name"]) == ("root", "root_2")
    assert second["curves"][0]["feature"] == "root_airfoil_2"


async def test_a_name_that_makes_another_ribs_feature_names_is_refused_before_writing(workspace):
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="root", spec=rib_spec(200))
        before = snapshot_all(workspace)
        # "root." is a name of its own, and sanitizes to root_airfoil all the same.
        text = await refused(client, "export_rib", project="part.SLDPRT",
                             source="sd7037-il.csv", name="root.", spec=rib_spec(150))
    assert "root_airfoil is already a curve of another record" in text
    assert snapshot_all(workspace) == before


async def test_a_curve_file_finishes_on_its_own_plane_without_a_camber_line(workspace):
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="placed", spec=rib_spec(200, x=-50))
        finished = await call(client, "finish_section", source="placed_airfoil.sldcrv")
        exported = await call(client, "export_rib", project="part.SLDPRT",
                              source="placed_airfoil.sldcrv", name="copy")
        from_csv = await call(client, "finish_section", source="sd7037-il.csv")
    for answer in (finished, exported):
        assert answer["settings"]["plane_mode"] == export.MODE_LOADED
        assert answer["settings"]["export_camber"] is False
        assert [c["role"] for c in answer["curves"]] == ["airfoil"]
        assert answer["placement"]["nose"][0] == pytest.approx(-50.0)
    # A CSV has its camber line, and goes on XY, as the window loads one.
    assert from_csv["settings"]["plane_mode"] == "XY"
    assert [c["role"] for c in from_csv["curves"]] == ["airfoil", "camber"]


async def test_a_ribs_remembered_plane_is_kept_however_its_source_was_stored(tmp_path):
    """A rib exported again after its source curve has moved stands on the plane it
    was made on, as the window puts it back — whether its record holds the source's
    path whole, or relative to the workspace."""
    answers = []
    for stored in ("whole", "relative"):
        workspace = workspace_with_sample(tmp_path / stored)
        async with session(workspace) as client:
            await call(client, "export_rib", project="placed.SLDPRT", source="sd7037-il.csv",
                       name="placed", folder="placed", spec=rib_spec(180, x=-40, pitch="4"))
            await call(client, "export_rib", project="part.SLDPRT",
                       source="placed/placed_airfoil.sldcrv", name="inner",
                       spec={"offset": 1.5})
            if stored == "relative":
                path = os.path.join(workspace, "part.airfoils.json")
                sidecar = store.load(path)
                sidecar.exports[0].source = "placed/placed_airfoil.sldcrv"
                store.save(path, sidecar)
            await call(client, "export_rib", project="placed.SLDPRT", rib="placed",
                       spec={"pitch": "9"})
            answers.append(await call(client, "export_rib", project="part.SLDPRT",
                                      rib="inner", include_points=True))
    whole, relative = answers
    assert relative["placement"] == whole["placement"]
    assert relative["curves"][0]["points"] == whole["curves"][0]["points"]


async def test_a_new_ribs_missing_source_is_said_to_be_missing(workspace):
    async with session(workspace) as client:
        text = await refused(client, "export_rib", project="part.SLDPRT", source="missing.csv")
    assert "The source missing.csv is not there." in text
    assert os.path.realpath(workspace) not in text


async def test_a_ribs_source_can_be_replaced_after_losing_access_to_the_old_source(
    workspace, tmp_path,
):
    outside = workspace_with_sample(tmp_path / "outside")
    async with session(outside) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="old", spec=rib_spec(180))
    old_source = os.path.join(outside, "old_airfoil.sldcrv")
    async with session(workspace, read_roots=(outside,)) as client:
        first = await call(client, "export_rib", project="part.SLDPRT",
                           source=old_source, name="rib")
    async with session(workspace) as client:
        updated = await call(client, "export_rib", project="part.SLDPRT", rib=first["rib"],
                             source="sd7037-il.csv", spec=rib_spec(200))
    assert updated["rib"] == first["rib"] and updated["source"] == "sd7037-il.csv"


async def test_reading_a_spoiled_record_leaves_it_where_it_is(workspace):
    path = os.path.join(workspace, "part.airfoils.json")
    with open(path, "w") as handle:
        handle.write("{ not json")
    async with session(workspace) as client:
        for tool, arguments in (("list_project", {}),
                                ("check_wing", {"ribs": ["a", "b"], "name": "w"})):
            text = await refused(client, tool, project="part.SLDPRT", **arguments)
            assert "could not be read" in text and "left as it is" in text
    assert files_in(workspace) == ["part.airfoils.json", "sd7037-il.csv"]


# -- the same rib as the window's ---------------------------------------------------


class Var:
    """A tkinter variable's get and set, without tkinter."""

    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def window_export(gui):
    """The window's Export, offline, on a stand-in for the window."""

    class Window:
        _export_unsafe = gui.ConverterApp._export_unsafe
        _remember = gui.ConverterApp._remember
        _next_index = gui.ConverterApp._next_index
        _section_as_dict = gui.ConverterApp._section_as_dict
        _sidecar_writable = gui.ConverterApp._sidecar_writable
        _save_sidecar = gui.ConverterApp._save_sidecar

        def __init__(self, part):
            self._snapshot = {"path": part, "title": os.path.basename(part)}
            self._sidecar_path = store.sidecar_path(part)
            self._sidecar_held = ""
            self._sidecar = store.load(self._sidecar_path) or store.Sidecar(
                part_path=part, part_title=os.path.basename(part))
            self._homeless = []
            self._editing = ""
            self.csv_path, self.out_folder, self.plane_mode = Var(), Var(), Var()

        def run(self, source, folder, spec, name="", editing=""):
            self.csv_path.set(source)
            self.out_folder.set(folder)
            self.plane_mode.set(spec.plane_mode)
            self.data, self.section = export.load_source(source)
            record = self._sidecar.find(editing) if editing else None
            if record is not None and record.loaded_section:
                self.section = export.section_from_dict(record.loaded_section)
            self._editing, self._form, self._name = editing, spec, name
            self._export_unsafe()

        def busy(self):
            return False

        def _spec(self):
            return self._form

        def _stem(self):
            return self._name

        def _can_push(self):
            return False

        def _confirm_orphans(self, record, orphans):
            return True

        def _set_status(self, message, ok=True):
            assert ok, message

        def _offline_reason(self):
            return ""

        def _memory_note(self):
            return ""

        def _describe_editing(self):
            pass

        def _fill_tree(self):
            pass

        def _select_editing(self):
            pass

    return Window


@pytest.fixture
def frozen(monkeypatch):
    """Ids and times that come out the same on both sides."""
    monkeypatch.setattr(store, "now", lambda: "2026-11-25T00:00:00Z")
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0xC0FFEE))


def snapshot(folder):
    taken = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and not name.endswith(".csv"):
            with open(path, "rb") as handle:
                taken[name] = handle.read()
    return taken


def clear(folder):
    for name in os.listdir(folder):
        if not name.endswith(".csv"):
            os.remove(os.path.join(folder, name))


async def test_a_rib_from_the_server_is_byte_for_byte_the_windows(workspace, frozen):
    gui = pytest.importorskip("airfoil_converter.gui", reason="tkinter is not available here")
    real = os.path.realpath(workspace)
    part = os.path.join(real, "part.SLDPRT")
    source = os.path.join(real, "sd7037-il.csv")
    # The window's form holds text as typed; the agent sends numbers.
    form = export.ExportSpec(
        export_camber=True, plane_mode="YZ", chord_axis="+Z", up_axis="+Y",
        target_chord="200", leading_edge=("-10", "0", "5"), te_mode=export.TE_LINE,
        te_thickness="0.5", keep_chord=True, reference="25", pitch="-2",
    )
    first = rib_spec(200, x=-10, z=5, te_mode=export.TE_LINE, te_thickness=0.5,
                     keep_chord=True, reference=25, pitch=-2, export_camber=True)
    second = {"te_mode": export.TE_CLOSE, "pitch": 1.5}

    # The window: a new rib, then the same rib exported again with a change
    # that leaves its trailing-edge line behind.
    window = window_export(gui)(part)
    window.run(source, real, form, name="root")
    made = window._sidecar.exports[0]
    window.run(source, real, dataclasses.replace(made.spec, te_mode=export.TE_CLOSE, pitch="1.5"),
               editing=made.export_id)
    by_window = snapshot(real)
    clear(real)

    async with session(workspace) as client:
        answer = await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                            name="root", spec=first, folder=".")
        await call(client, "export_rib", project="part.SLDPRT", rib=answer["rib"], spec=second)
    by_server = snapshot(real)

    assert sorted(by_server) == sorted(by_window) == [
        "part.airfoils.json", "root_airfoil.sldcrv", "root_airfoil_te.sldcrv",
        "root_camber.sldcrv",
    ]
    for name in by_window:
        assert by_server[name] == by_window[name], name
    retired = [c for c in store.from_json(by_server["part.airfoils.json"].decode()).exports[0].curves
               if c.retired]
    assert [c.feature for c in retired] == ["root_airfoil_te"]


async def test_a_rib_on_a_remembered_plane_is_the_windows_too(workspace, frozen):
    """A rib made on a curve file's own plane, then exported again after that curve
    file has moved: the window puts the remembered plane back, and so does the server."""
    gui = pytest.importorskip("airfoil_converter.gui", reason="tkinter is not available here")
    real = os.path.realpath(workspace)
    part = os.path.join(real, "part.SLDPRT")
    source = os.path.join(real, "placed", "placed_airfoil.sldcrv")
    out = os.path.join(real, "out")
    async with session(workspace) as client:
        await call(client, "export_rib", project="placed.SLDPRT", source="sd7037-il.csv",
                   name="placed", spec=rib_spec(180, x=-40, pitch="4"), folder="placed")
    os.makedirs(out)
    start = snapshot_all(real)

    async def move_the_source():
        async with session(workspace) as client:
            await call(client, "export_rib", project="placed.SLDPRT", rib="placed",
                       spec={"pitch": 9})

    # The window: a form as a curve file's load leaves it, the reference point
    # filled in to six figures, and an offset typed in.
    data, section = export.load_source(source)
    form = export.ExportSpec(export_camber=False, plane_mode=export.MODE_LOADED,
                             chord_axis="-X", up_axis="+Y", reference="25", offset="1.5")
    filled = tuple(f"{c:g}" for c in export.reference_point(data, form, section, section.origin))
    form = dataclasses.replace(form, leading_edge=filled)
    window = window_export(gui)(part)
    window.run(source, out, form, name="inner")
    await move_the_source()
    made = window._sidecar.exports[0]
    window.run(source, out, dataclasses.replace(made.spec, offset="2"), editing=made.export_id)
    by_window = snapshot_all(real)
    restore_all(real, start)

    async with session(workspace) as client:
        inner = await call(client, "export_rib", project="part.SLDPRT",
                           source="placed/placed_airfoil.sldcrv", name="inner", folder="out",
                           spec={"chord_axis": "-X", "up_axis": "+Y", "reference": 25,
                                 "offset": 1.5, "leading_edge": list(filled)})
    await move_the_source()
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", rib=inner["rib"],
                   spec={"offset": 2})
    by_server = snapshot_all(real)

    assert sorted(by_server) == sorted(by_window)
    for name in by_window:
        assert by_server[name] == by_window[name], name
    sidecar = store.from_json(by_server["part.airfoils.json"].decode())
    assert sidecar.exports[0].loaded_section is not None
