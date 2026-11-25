"""The server itself: its paths, its errors, converter_info, and how it is launched.

Every tool is called through a real MCP client session, in memory, except one
check that the console entry point serves over stdio.
"""

import ast
import dataclasses
import glob
import os
import sys

import pytest

pytest.importorskip("mcp.server.mcpserver")

from airfoil_converter import export  # noqa: E402
from airfoil_converter.mcp import server as mcp_server, tools_info  # noqa: E402

from mcp_tools import (  # noqa: E402
    anyio_backend,  # noqa: F401
    call,
    refused,
    rib_spec,
    session,
    workspace_with_sample,
)

pytestmark = pytest.mark.anyio

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

TOOLS = {
    "converter_info", "read_section", "finish_section", "export_rib", "list_project",
    "check_wing", "build_wing",
}


@pytest.fixture
def workspace(tmp_path):
    return workspace_with_sample(tmp_path / "ws")


@pytest.fixture
def outside(tmp_path):
    """A source just outside the workspace, beside it."""
    folder = tmp_path / "elsewhere"
    workspace_with_sample(folder)
    return str(folder)


# -- the tools there are ------------------------------------------------------


async def test_every_phase_one_tool_is_served(workspace):
    async with session(workspace) as client:
        listed = await client.list_tools()
    assert {tool.name for tool in listed.tools} == TOOLS
    for tool in listed.tools:
        assert tool.description, f"{tool.name} has no description"


async def test_read_only_tools_say_so(workspace):
    async with session(workspace) as client:
        listed = await client.list_tools()
    hints = {tool.name: tool.annotations.read_only_hint for tool in listed.tools}
    assert {name for name, ro in hints.items() if ro} == {
        "converter_info", "read_section", "finish_section", "list_project", "check_wing",
    }


# -- converter_info ------------------------------------------------------------


@pytest.mark.parametrize("spec", [export.ExportSpec, export.WingSpec])
def test_every_field_has_a_meaning_and_no_meaning_is_for_a_field_that_is_gone(spec):
    """A field added to a spec without a line here would go undescribed."""
    declared = [f.name for f in dataclasses.fields(spec)]
    assert sorted(tools_info.MEANINGS[spec.__name__]) == sorted(declared)
    assert set(tools_info.CHOICES[spec.__name__]) <= set(declared)
    assert all(tools_info.MEANINGS[spec.__name__].values())


def test_every_trailing_edge_mode_is_described():
    assert set(tools_info.TE_MODE_MEANINGS) == set(export.TE_MODES)


async def test_converter_info_reads_the_fields_off_the_dataclasses(workspace):
    async with session(workspace) as client:
        info = await call(client, "converter_info")
    for spec in (export.ExportSpec, export.WingSpec):
        fields = info["fields"][spec.__name__]
        assert [f["name"] for f in fields] == [f.name for f in dataclasses.fields(spec)]
        defaults = spec()
        for entry in fields:
            value = getattr(defaults, entry["name"])
            assert entry["default"] == (list(value) if isinstance(value, tuple) else value)
    by_name = {f["name"]: f for f in info["fields"]["ExportSpec"]}
    assert by_name["te_mode"]["choices"] == list(export.TE_MODES)
    # A comment beside a field in the source comes through with it.
    assert "chord from the nose" in by_name["reference"]["note"]
    assert by_name["leading_edge"]["type"] == "Tuple[str, str, str]"


async def test_converter_info_says_where_it_stands(workspace, outside):
    async with session(workspace, [outside]) as client:
        info = await call(client, "converter_info")
    assert info["version"]
    assert info["units"] == {"length": "mm", "angle": "degrees"}
    assert info["workspace"] == os.path.realpath(workspace)
    assert info["read_roots"] == [os.path.realpath(outside)]
    assert info["revision"] is None or len(info["revision"]) == 40
    sw = info["solidworks"]
    assert isinstance(sw["available"], bool)
    assert sw["available"] or sw["reason"]
    assert [p["plane"] for p in info["axes"]["main_planes"]] == ["XY", "XZ", "YZ"]


def test_the_revision_is_read_without_touching_the_servers_stdin(monkeypatch):
    """The server's stdin is the MCP channel; git must not inherit it."""
    import subprocess

    seen = {}

    def run(args, **kwargs):
        seen.update(kwargs)
        raise subprocess.SubprocessError("not here")

    monkeypatch.setattr(tools_info.subprocess, "run", run)
    assert tools_info.git_revision() is None
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


# -- the workspace -------------------------------------------------------------


async def test_a_source_outside_the_workspace_is_refused(workspace, outside):
    async with session(workspace) as client:
        for path in ("../elsewhere/sd7037-il.csv", os.path.join(outside, "sd7037-il.csv")):
            text = await refused(client, "read_section", source=path)
            assert "outside" in text


async def test_an_absolute_path_inside_the_workspace_is_fine(workspace):
    async with session(workspace) as client:
        answer = await call(client, "read_section", source=os.path.join(workspace, "sd7037-il.csv"))
    assert answer["source"] == "sd7037-il.csv"


async def test_a_symlink_out_of_the_workspace_is_refused(workspace, outside):
    os.symlink(os.path.join(outside, "sd7037-il.csv"), os.path.join(workspace, "linked.csv"))
    os.symlink(outside, os.path.join(workspace, "linked_dir"))
    async with session(workspace) as client:
        assert "outside" in await refused(client, "read_section", source="linked.csv")
        assert "outside" in await refused(
            client, "export_rib", project="wing.SLDPRT", source="sd7037-il.csv",
            folder="linked_dir",
        )
        assert "outside" in await refused(
            client, "export_rib", project="linked_dir/wing.SLDPRT", source="sd7037-il.csv",
        )
    assert sorted(os.listdir(outside)) == ["sd7037-il.csv"]


async def test_a_symlink_that_stays_inside_is_followed(workspace):
    os.symlink(os.path.join(workspace, "sd7037-il.csv"), os.path.join(workspace, "same.csv"))
    async with session(workspace) as client:
        answer = await call(client, "read_section", source="same.csv")
    assert answer["source"] == "sd7037-il.csv"


async def test_nothing_is_written_outside_the_workspace(workspace, outside):
    async with session(workspace, [outside]) as client:
        for project, folder in (
            ("../wing.SLDPRT", None),
            (os.path.join(outside, "wing.SLDPRT"), None),
            ("wing.SLDPRT", "../out"),
            ("wing.SLDPRT", outside),
            ("sub/../../wing.SLDPRT", None),
        ):
            arguments = dict(project=project, source="sd7037-il.csv")
            if folder:
                arguments["folder"] = folder
            assert "outside the workspace" in await refused(client, "export_rib", **arguments)
    parent = os.path.dirname(workspace)
    assert sorted(os.listdir(parent)) == ["elsewhere", "ws"]
    assert sorted(os.listdir(outside)) == ["sd7037-il.csv"]


async def test_a_read_root_is_read_but_never_written(workspace, outside):
    source = os.path.join(outside, "sd7037-il.csv")
    async with session(workspace, [outside]) as client:
        answer = await call(client, "read_section", source=source)
        assert answer["source"] == source
        rib = await call(client, "export_rib", project="wing.SLDPRT", source=source,
                         spec=rib_spec(200))
        assert rib["folder"] == "."
        assert "outside the workspace" in await refused(
            client, "export_rib", project="wing.SLDPRT", source=source, folder=outside,
        )
    assert sorted(os.listdir(outside)) == ["sd7037-il.csv"]


def test_paths_are_checked_after_resolving(tmp_path):
    ws = workspace_with_sample(tmp_path / "ws")
    ctx = mcp_server.ServerContext.create(ws)
    assert ctx.writable("a/b/../c.sldcrv") == os.path.join(os.path.realpath(ws), "a", "c.sldcrv")
    for bad in ("", "  ", "..", "../ws2/x", "/", "a/../../x"):
        with pytest.raises(mcp_server.PathError):
            ctx.writable(bad)
    # A sibling whose name starts with the workspace's is not inside it.
    os.makedirs(str(tmp_path / "ws2"))
    with pytest.raises(mcp_server.PathError):
        ctx.readable(str(tmp_path / "ws2" / "x.csv"))


# -- errors are answers -------------------------------------------------------


async def test_the_converters_own_errors_come_back_as_its_message(workspace):
    async with session(workspace) as client:
        text = await refused(client, "finish_section", source="sd7037-il.csv",
                             spec={"target_chord": "abc"})
        assert "Target chord must be a number (got 'abc')." in text
        text = await refused(client, "finish_section", source="sd7037-il.csv",
                             spec={"te_mode": "Wavy"})
        assert "Unknown TE handling mode 'Wavy'." in text
        text = await refused(client, "finish_section", source="sd7037-il.csv",
                             spec={"chord": "200"})
        assert "Unknown setting(s) chord" in text
        text = await refused(client, "read_section", source="missing.csv")
        assert "is not there" in text


async def test_a_file_that_is_not_a_section_is_refused_by_the_parser(workspace):
    with open(os.path.join(workspace, "notes.csv"), "w") as handle:
        handle.write("Name,thing\nsome,words\n")
    async with session(workspace) as client:
        text = await refused(client, "read_section", source="notes.csv")
    assert 'No "Airfoil surface" points found' in text


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
async def test_an_unexpected_failure_says_nothing_of_its_insides(workspace, monkeypatch, error):
    from airfoil_converter.mcp import tools_section

    def broken(*args, **kwargs):
        raise error("a secret from deep inside")

    monkeypatch.setattr(tools_section, "read", broken)
    async with session(workspace) as client:
        text = await refused(client, "read_section", source="sd7037-il.csv")
    assert "secret" not in text


async def test_a_file_that_is_not_text_is_said_to_be_so(workspace):
    with open(os.path.join(workspace, "binary.csv"), "wb") as handle:
        handle.write(bytes(range(256)) * 4)
    async with session(workspace) as client:
        text = await refused(client, "read_section", source="binary.csv")
    assert "binary.csv is not a text file" in text


# -- writing through links -------------------------------------------------------


@pytest.mark.parametrize("planted", [
    "root_airfoil.sldcrv.tmp", "part.airfoils.json.tmp", "root_airfoil.sldcrv",
    "part.airfoils.json",
])
async def test_nothing_is_written_through_a_planted_link(workspace, outside, planted):
    target = os.path.join(outside, "victim.txt")
    with open(target, "w") as handle:
        handle.write("untouched")
    os.symlink(target, os.path.join(workspace, planted))
    async with session(workspace) as client:
        text = await refused(client, "export_rib", project="part.SLDPRT",
                             source="sd7037-il.csv", name="root", spec=rib_spec(200))
    assert "symbolic link" in text or "outside" in text
    with open(target) as handle:
        assert handle.read() == "untouched"
    assert sorted(os.listdir(workspace)) == sorted(["sd7037-il.csv", planted])


async def test_a_link_inside_the_workspace_is_not_written_through_either(workspace):
    real = os.path.join(workspace, "kept.sldcrv")
    with open(real, "w") as handle:
        handle.write("kept")
    os.symlink(real, os.path.join(workspace, "root_airfoil.sldcrv"))
    async with session(workspace) as client:
        text = await refused(client, "export_rib", project="part.SLDPRT",
                             source="sd7037-il.csv", name="root", spec=rib_spec(200))
    assert "symbolic link" in text
    with open(real) as handle:
        assert handle.read() == "kept"


async def test_a_wing_is_not_written_through_a_planted_link(workspace, outside):
    target = os.path.join(outside, "victim.txt")
    with open(target, "w") as handle:
        handle.write("untouched")
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="root", spec=rib_spec(200))
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="tip", spec=rib_spec(120, x=-300, z=-20))
        os.symlink(target, os.path.join(workspace, "wing_le.sldcrv.tmp"))
        text = await refused(client, "build_wing", project="part.SLDPRT",
                             ribs=["root", "tip"], name="wing")
    assert "symbolic link" in text
    with open(target) as handle:
        assert handle.read() == "untouched"
    assert not any(name.startswith("wing_") and name.endswith(".sldcrv")
                   for name in os.listdir(workspace))


# -- the core stays free of it -------------------------------------------------


def imports_mcp(source):
    """The modules a source imports that are the MCP SDK, or this package's server."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names = [node.module]
            else:  # from . import mcp
                names = [alias.name for alias in node.names]
        else:
            continue
        found += [n for n in names if n == "mcp" or n.startswith("mcp.")]
    return found


@pytest.mark.parametrize("source", [
    "import mcp",
    "import mcp.server",
    "from mcp.server.mcpserver import MCPServer",
    "from .mcp import server",
    "from .mcp.server import make_server",
    "from . import mcp",
    "from ..mcp import tools_info",
])
def test_an_import_of_mcp_is_seen_however_it_is_written(source):
    assert imports_mcp(source)


def test_an_import_that_only_sounds_like_it_is_not_flagged():
    assert not imports_mcp("import mcpx\nfrom .store import mcp_like\nfrom . import export")


def test_nothing_outside_the_mcp_package_imports_mcp():
    package = os.path.join(SRC, "airfoil_converter")
    for path in glob.glob(os.path.join(package, "*.py")):
        with open(path, encoding="utf-8") as handle:
            assert imports_mcp(handle.read()) == [], path


# -- launching it --------------------------------------------------------------


def test_a_workspace_that_is_not_there_stops_it_with_a_reason(tmp_path, capsys):
    assert mcp_server.main(["--workspace", str(tmp_path / "nowhere")]) == 2
    assert "is not a folder" in capsys.readouterr().err


async def test_it_serves_over_stdio(workspace):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "airfoil_converter.mcp", "--workspace", workspace],
        env=env,
    )
    async with Client(params) as client:
        listed = await client.list_tools()
        answer = await call(client, "read_section", source="sd7037-il.csv")
    assert {tool.name for tool in listed.tools} == TOOLS
    assert answer["point_count"] == 61
