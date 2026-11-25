"""Wings through the server: check, build, and the loft plan a CAD bridge needs.

The first test is the whole job by tool calls alone: from sd7037-il.csv to a
wing's curve set. The last ones run the Wing tab's own export code on a
stand-in for the tab and compare every byte with the server's.
"""

import copy
import math
import os
import uuid

import pytest

pytest.importorskip("mcp.server.mcpserver")

from airfoil_converter import export, store, swloft, wing_build, writer  # noqa: E402

from mcp_tools import (  # noqa: E402
    anyio_backend,  # noqa: F401
    call,
    refused,
    rib_spec,
    session,
    workspace_with_sample,
)

pytestmark = pytest.mark.anyio

PROJECT = "wing.SLDPRT"
RIBS = ["root", "tip"]


@pytest.fixture
def workspace(tmp_path):
    return workspace_with_sample(tmp_path / "ws")


async def two_ribs(client, **extra):
    """A root of 200 mm at x = 0 and a tip of 120 mm 300 mm out along -x, swept 20 mm."""
    await call(client, "export_rib", project=PROJECT, source="sd7037-il.csv", name="root",
               folder="ribs", spec=rib_spec(200, **extra))
    await call(client, "export_rib", project=PROJECT, source="sd7037-il.csv", name="tip",
               folder="ribs", spec=rib_spec(120, x=-300, z=-20, **extra))


def snapshot(folder):
    taken = {}
    for root, _, names in os.walk(folder):
        for name in names:
            if not name.endswith(".csv"):
                path = os.path.join(root, name)
                with open(path, "rb") as handle:
                    taken[os.path.relpath(path, folder)] = handle.read()
    return taken


async def test_from_a_csv_to_a_lofted_wings_curves_by_tool_calls_alone(workspace):
    async with session(workspace) as client:
        await two_ribs(client)
        ends = {"root_end": "open", "tip_end": "closed"}

        before = snapshot(workspace)
        checked = await call(client, "check_wing", project=PROJECT, ribs=RIBS, name="wing",
                             spec=ends)
        assert snapshot(workspace) == before  # a check writes nothing

        built = await call(client, "build_wing", project=PROJECT, ribs=RIBS, name="wing",
                           spec=ends)
        listed = await call(client, "list_project", project=PROJECT)
        again = await call(client, "build_wing", project=PROJECT, wing=built["wing"])

    assert checked["new"] and checked["wing"] is None and checked["name"] == "wing"
    assert checked["folder"] == "ribs"  # the first rib's, as the Wing tab fills it in
    assert [r["name"] for r in checked["ribs"]] == RIBS
    assert checked["retires"] == [] and checked["station_count_change"] is None
    assert checked["describe"][0] == "2 ribs over 300.0 mm of span (root, tip)"
    planform = checked["planform"]
    assert planform["span_mm"] == pytest.approx(300.0)
    assert planform["root_chord_mm"] == pytest.approx(200.0)
    assert planform["tip_chord_mm"] == pytest.approx(120.0)
    sweep = math.degrees(math.atan(20.0 / 300.0))
    assert [s["le_sweep_deg"] for s in planform["samples"]] == pytest.approx([sweep] * 5, abs=1e-3)
    assert checked["offset"] is None
    assert checked["station_count"] == len(checked["sections"]) >= 2

    # The build says what the check said, and writes what it described.
    assert built["new"] and built["name"] == "wing"
    for key in ("describe", "station_count", "sections", "guides", "planform", "settings"):
        assert built[key] == checked[key]
    assert built["curve_count"] == len(built["curves"])
    for curve in built["curves"]:
        assert curve["changed"]
        assert os.path.isfile(os.path.join(workspace, curve["file"]))
        assert curve["file"].startswith("ribs/wing_")

    plan = built["loft_plan"]
    assert plan["name"] == "wing_loft"
    assert plan["profiles"] == built["sections"]  # root to tip, in order
    assert plan["profiles"][0] == "wing_s01"
    assert plan["guides"][0] == "wing_le" and plan["guides"][-1] == "wing_te"
    assert set(plan["guides"]) == set(built["guides"])
    assert plan["joins"] == [] and plan["profiles_once_joined"] == plan["profiles"]
    for name in plan["profiles"] + plan["guides"]:
        assert os.path.isfile(os.path.join(workspace, plan["files"][name]))

    (wing,) = listed["wings"]
    assert wing["station_count"] == built["station_count"]
    assert all(c["present"] and c["matches"] for c in wing["curves"])
    assert len(listed["ribs"]) == 2

    assert not again["new"] and again["wing"] == built["wing"]
    assert not any(c["changed"] for c in again["curves"])
    assert again["warnings"] == [] and again["retired"] == []


async def test_only_a_build_writes_a_wings_record(workspace):
    async with session(workspace) as client:
        await two_ribs(client)
        record = os.path.join(workspace, "wing.airfoils.json")
        before = snapshot(workspace)
        await call(client, "check_wing", project=PROJECT, ribs=RIBS, name="wing",
                   folder="not_made_by_a_check")
        listed = await call(client, "list_project", project=PROJECT)
        tools = {tool.name for tool in (await client.list_tools()).tools}
    assert "define_wing" not in tools
    assert snapshot(workspace) == before
    assert listed["wings"] == []
    # A wing only checked is nothing a loft could be planned from.
    assert swloft.plans_for_part(store.load(record)) == []


async def test_moving_a_built_wings_folder_moves_its_curves_with_it(workspace):
    async with session(workspace) as client:
        await two_ribs(client)
        built = await call(client, "build_wing", project=PROJECT, ribs=RIBS, name="wing")
        checked = await call(client, "check_wing", project=PROJECT, wing="wing",
                             folder="elsewhere")
        assert checked["retires"] == [] and checked["folder"] == "elsewhere"
        moved = await call(client, "build_wing", project=PROJECT, wing="wing",
                           folder="elsewhere")
        listed = await call(client, "list_project", project=PROJECT)
    assert moved["wing"] == built["wing"] and moved["retired"] == []
    (wing,) = listed["wings"]
    assert wing["folder"] == "elsewhere"
    assert len(wing["curves"]) == built["curve_count"]
    for curve in wing["curves"]:
        assert curve["file"].startswith("elsewhere/")
        assert curve["present"] and curve["matches"] and not curve["retired"]


async def test_a_check_says_what_a_build_would_retire_and_whether_the_loft_changes(workspace):
    async with session(workspace) as client:
        await two_ribs(client)
        built = await call(client, "build_wing", project=PROJECT, ribs=RIBS, name="wing",
                           spec={"offset": 1})
        before = snapshot(workspace)
        checked = await call(client, "check_wing", project=PROJECT, wing="wing",
                             spec={"offset": ""})
        assert snapshot(workspace) == before
        rebuilt = await call(client, "build_wing", project=PROJECT, wing="wing",
                             spec={"offset": ""})
    assert "wing_s01_upper" in checked["retires"]
    assert checked["station_count_change"] == {
        "from": built["station_count"], "to": checked["station_count"],
    }
    assert checked["station_count"] != built["station_count"]
    assert rebuilt["retired"] == checked["retires"]
    assert "profiles" in rebuilt["warnings"][0]


async def test_ribs_closed_by_a_trailing_edge_line_say_what_to_join(workspace):
    async with session(workspace) as client:
        await two_ribs(client, te_mode=export.TE_LINE, te_thickness="0.5")
        built = await call(client, "build_wing", project=PROJECT, ribs=RIBS, name="wing")
    plan = built["loft_plan"]
    assert built["te_thickness_mm"] == pytest.approx(0.5)
    assert {"wing_te_upper", "wing_te_lower"} <= set(plan["guides"])
    assert plan["joins"] == built["joins"]
    assert plan["joins"][0] == {"name": "wing_s01_joined", "curves": ["wing_s01", "wing_s01_te"]}
    assert plan["profiles_once_joined"] == [p + "_joined" for p in plan["profiles"]]


async def test_edges_can_be_curve_files_in_the_workspace(workspace):
    edges = os.path.join(workspace, "edges")
    os.makedirs(edges)
    # A leading edge bowed forward by 5 mm at mid-span; a straight trailing edge.
    spans = [300.0 * k / 60 for k in range(61)]
    writer.write_curve(os.path.join(edges, "le.sldcrv"),
                       [(-s, 0.0, -20.0 * s / 300.0 + 5.0 * math.sin(math.pi * s / 300.0))
                        for s in spans])
    writer.write_curve(os.path.join(edges, "te.sldcrv"),
                       [(-s, 0.0, -200.0 + 60.0 * s / 300.0) for s in spans])
    async with session(workspace) as client:
        await two_ribs(client)
        checked = await call(client, "check_wing", project=PROJECT, ribs=RIBS, name="wing",
                             spec={"le_source": "edges/le.sldcrv",
                                   "te_source": "edges/te.sldcrv"})
    assert checked["settings"]["le_source"] == os.path.realpath(os.path.join(edges, "le.sldcrv"))
    mid = checked["planform"]["samples"][2]
    assert mid["leading_edge"][0] == pytest.approx(10.0 - 5.0, abs=0.05)


async def test_an_offset_wing_reports_the_wall_it_expects(workspace):
    async with session(workspace) as client:
        await two_ribs(client)
        checked = await call(client, "check_wing", project=PROJECT, ribs=RIBS, name="wing",
                             spec={"offset": 1, "offset_dir": "Inward"})
    offset = checked["offset"]
    assert offset["offset_mm"] == -1.0
    wall = offset["wall"]
    assert wall["thinnest_mm"] <= 1.0 + 0.1 and wall["thickest_mm"] >= 1.0 - 0.1
    assert wall["points_checked"] > 0


async def test_a_wing_built_again_keeps_its_identity_and_a_new_name_gets_an_index(workspace):
    async with session(workspace) as client:
        await two_ribs(client)
        first = await call(client, "build_wing", project=PROJECT, ribs=RIBS, name="wing")
        changed = await call(client, "build_wing", project=PROJECT, wing="wing",
                             spec={"tip_end": "open"})
        assert changed["wing"] == first["wing"] and not changed["new"]
        assert changed["settings"]["tip_end"] == "open"
        assert changed["settings"]["ribs"] == first["settings"]["ribs"]
        text = await refused(client, "build_wing", project=PROJECT, wing="wing", name="other")
        assert "keeps the name it was made with" in text
        second = await call(client, "build_wing", project=PROJECT, ribs=["tip", "root"],
                            name="wing ")
        assert second["name"] == "wing_2" and second["wing"] != first["wing"]


@pytest.mark.parametrize("arguments, said", [
    (dict(ribs=["root"], name="w"), "at least two ribs"),
    (dict(ribs=["root", "root"], name="w"), "listed twice"),
    (dict(ribs=["root", "nothing"], name="w"), "no rib nothing"),
    (dict(ribs=RIBS), "Give the wing a name"),
    (dict(ribs=RIBS, name="  "), "Give the wing a name"),
    (dict(ribs=RIBS, name="w", spec={"root_end": "ajar"}), "Unknown root end 'ajar'"),
    (dict(ribs=RIBS, name="w", spec={"offset": "-1"}), "must not be negative"),
    (dict(ribs=RIBS, name="w", spec={"ribs": ["a"]}), "Unknown setting(s) ribs"),
    (dict(ribs=RIBS, name="w", spec={"le_source": "../le.sldcrv"}), "outside"),
    (dict(ribs=RIBS, name="w", spec={"le_source": "le.sldcrv"}), "is not there"),
    (dict(wing="nothing"), "no wing nothing"),
])
@pytest.mark.parametrize("tool", ["check_wing", "build_wing"])
async def test_a_wing_that_cannot_be_is_refused_with_the_reason(workspace, tool, arguments, said):
    async with session(workspace) as client:
        await two_ribs(client)
        before = snapshot(workspace)
        text = await refused(client, tool, project=PROJECT, **arguments)
    assert said in text
    assert snapshot(workspace) == before


async def test_a_wing_its_ribs_cannot_make_is_refused_by_the_check(workspace):
    async with session(workspace) as client:
        await call(client, "export_rib", project=PROJECT, source="sd7037-il.csv", name="root",
                   spec=rib_spec(200))
        await call(client, "export_rib", project=PROJECT, source="sd7037-il.csv", name="tip",
                   spec=rib_spec(120, x=-300, te_mode=export.TE_SPLIT))
        text = await refused(client, "check_wing", project=PROJECT, ribs=RIBS, name="wing")
    assert "upper and lower" in text


async def test_a_rib_whose_source_is_outside_what_may_be_read_is_not_read(workspace, tmp_path):
    async with session(workspace) as client:
        await two_ribs(client)
    # Someone else's record, pointing a rib at a file the server may not read.
    path = os.path.join(workspace, "wing.airfoils.json")
    sidecar = store.load(path)
    stray = tmp_path / "stray.csv"
    stray.write_bytes(open(os.path.join(workspace, "sd7037-il.csv"), "rb").read())
    sidecar.exports[0].source = str(stray)
    store.save(path, sidecar)
    async with session(workspace) as client:
        text = await refused(client, "check_wing", project=PROJECT, ribs=RIBS, name="wing")
    assert "source of root" in text and "outside" in text


async def test_paths_a_record_stores_relative_are_read_in_the_workspace(workspace, tmp_path,
                                                                        monkeypatch):
    """Not wherever the server was started: a relative path is the workspace's."""
    os.rename(os.path.join(workspace, "sd7037-il.csv"), os.path.join(workspace, "section.csv"))
    edges = os.path.join(workspace, "edges")
    os.makedirs(edges)
    writer.write_curve(os.path.join(edges, "le.sldcrv"),
                       [(-300.0 * k / 10, 0.0, -20.0 * k / 10) for k in range(11)])
    async with session(workspace) as client:
        await call(client, "export_rib", project=PROJECT, source="section.csv", name="root",
                   folder="ribs", spec=rib_spec(200))
        await call(client, "export_rib", project=PROJECT, source="section.csv", name="tip",
                   folder="ribs", spec=rib_spec(120, x=-300, z=-20))
        await call(client, "build_wing", project=PROJECT, ribs=RIBS, name="wing",
                   spec={"le_source": "edges/le.sldcrv"})
    # A record written with relative paths, as a hand or another tool might write it.
    path = os.path.join(workspace, "wing.airfoils.json")
    sidecar = store.load(path)
    for record in sidecar.exports:
        record.source = "section.csv"
        record.output_folder = "ribs"
    (wing,) = sidecar.wings
    wing.settings["le_source"] = "edges/le.sldcrv"
    wing.output_folder = "ribs"
    store.save(path, sidecar)

    elsewhere = tmp_path / "started_here"
    elsewhere.mkdir()
    monkeypatch.chdir(str(elsewhere))
    async with session(workspace) as client:
        checked = await call(client, "check_wing", project=PROJECT, wing="wing")
        listed = await call(client, "list_project", project=PROJECT)
    assert checked["planform"]["span_mm"] == pytest.approx(300.0)
    assert all(rib["source_present"] for rib in listed["ribs"])
    for record in listed["ribs"] + listed["wings"]:
        for curve in record["curves"]:
            assert curve["file"].startswith("ribs/")
            assert curve["present"] and curve["matches"], curve["feature"]


# -- the same wing as the window's ----------------------------------------------------


def window_tab(gui, monkeypatch):
    """The Wing tab's offline export, on a stand-in for the tab and its window."""
    wing_tab = gui.wing_tab
    # The tab asks before retiring curves or changing a loft's profile count.
    asked = []
    monkeypatch.setattr(wing_tab.messagebox, "askokcancel",
                        lambda title, message, parent=None: asked.append(title) or True)

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    class Host:
        _save_sidecar = gui.ConverterApp._save_sidecar
        _sidecar_writable = gui.ConverterApp._sidecar_writable

        def __init__(self, part):
            self._snapshot = {"path": part, "title": os.path.basename(part)}
            self._sidecar_path = store.sidecar_path(part)
            self._sidecar_held = ""
            self._sidecar = store.load(self._sidecar_path)
            self.insert_missing = Var(True)

        def _can_push(self):
            return False

        def _offline_reason(self):
            return ""

        def _set_status(self, message, ok=True):
            assert ok, message

    class Tab:
        _finish_export = wing_tab.WingTab._finish_export
        _remember = wing_tab.WingTab._remember
        _sidecar = wing_tab.WingTab._sidecar
        _loft_sections = staticmethod(wing_tab.WingTab._loft_sections)

        def __init__(self, part):
            self.host = Host(part)
            self._editing = ""
            self.asked = asked

        def export(self, spec, stem, folder):
            """What WingTab._prepare and export hand the thread, then _finish_export."""
            sidecar = self._sidecar()
            record = sidecar.find_wing(self._editing) if self._editing else None
            stem = record.stem if record else stem
            index = record.name_index if record else sidecar.next_index(stem)
            own = record.feature_names() if record else []
            build = wing_build.build_wing(spec, copy.deepcopy(sidecar), stem, index,
                                          check=bool(spec.offset_mm()), own_names=own,
                                          sections=self._loft_sections(record))
            self._finish_export(build, dict(
                record_id=record.export_id if record else "", stem=stem, index=index,
                spec=spec, folder=folder, sidecar_path=self.host._sidecar_path))

        def _fail(self, message):
            raise AssertionError(message)

        def _tick_phase(self, *args, **kwargs):
            pass

        def _idle(self):
            pass

        def _set_status(self, message, ok=True):
            assert ok, message

        def refresh(self):
            pass

    return Tab


def freeze(monkeypatch):
    """Ids and times that come out the same on both sides."""
    monkeypatch.setattr(store, "now", lambda: "2026-11-25T00:00:00Z")
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0xC0FFEE))


def restore(folder, kept):
    """Put a folder back to ``kept``: what the window made goes, the ribs come back."""
    for name in set(snapshot(folder)) - set(kept):
        os.remove(os.path.join(folder, name))
    for name, data in kept.items():
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(data)


def wing_form(record, **fields):
    """A WingSpec as the Wing tab's form makes it: every value the text in its field."""
    return export.WingSpec(ribs=tuple(r.export_id for r in record.exports), **fields)


async def test_a_wing_from_the_server_is_byte_for_byte_the_windows(workspace, monkeypatch):
    gui = pytest.importorskip("airfoil_converter.gui", reason="tkinter is not available here")
    real = os.path.realpath(workspace)
    async with session(workspace) as client:
        await two_ribs(client, te_mode=export.TE_LINE, te_thickness="0.4")
    ribs = snapshot(real)
    freeze(monkeypatch)
    record = store.load(os.path.join(real, "wing.airfoils.json"))
    form = dict(root_end="open", tip_end="closed", thickness="scaled")

    window = window_tab(gui, monkeypatch)(os.path.join(real, PROJECT))
    window.export(wing_form(record, **form), "wing", os.path.join(real, "ribs"))
    by_window = snapshot(real)
    restore(real, ribs)

    async with session(workspace) as client:
        await call(client, "build_wing", project=PROJECT, ribs=RIBS, name="wing", spec=form)
    by_server = snapshot(real)

    assert sorted(by_server) == sorted(by_window)
    assert len(set(by_window) - set(ribs)) > 10  # the wing's curves, not just its record
    for name in by_window:
        assert by_server[name] == by_window[name], name


async def test_an_offset_wing_and_its_rebuild_are_byte_for_byte_the_windows(workspace,
                                                                           monkeypatch):
    """An offset wing made, then made again without the offset: its offset sections
    retired, and its loft's profile count changed — both as the tab leaves them."""
    gui = pytest.importorskip("airfoil_converter.gui", reason="tkinter is not available here")
    real = os.path.realpath(workspace)
    async with session(workspace) as client:
        await two_ribs(client)
    ribs = snapshot(real)
    freeze(monkeypatch)
    record = store.load(os.path.join(real, "wing.airfoils.json"))

    window = window_tab(gui, monkeypatch)(os.path.join(real, PROJECT))
    window.export(wing_form(record, offset="1.5", offset_dir="Inward"), "wing",
                  os.path.join(real, "ribs"))
    by_window_offset = snapshot(real)
    window._editing = window.host._sidecar.wings[0].export_id
    window.export(wing_form(record, offset=""), "wing", os.path.join(real, "ribs"))
    by_window_again = snapshot(real)
    assert window.asked == ["The offset wing's profiles changed", "Curves would be left behind"]
    restore(real, ribs)

    async with session(workspace) as client:
        await call(client, "build_wing", project=PROJECT, ribs=RIBS, name="wing",
                   spec={"offset": 1.5, "offset_dir": "Inward"})
        by_server_offset = snapshot(real)
        again = await call(client, "build_wing", project=PROJECT, wing="wing",
                           spec={"offset": ""})
    by_server_again = snapshot(real)

    assert again["retired"] and again["warnings"]
    for by_window, by_server in ((by_window_offset, by_server_offset),
                                 (by_window_again, by_server_again)):
        assert sorted(by_server) == sorted(by_window)
        for name in by_window:
            assert by_server[name] == by_window[name], name
    final = store.from_json(by_server_again["wing.airfoils.json"].decode()).wings[0]
    assert any(c.retired for c in final.curves)
