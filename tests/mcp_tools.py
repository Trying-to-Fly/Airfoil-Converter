"""Calling the MCP server's tools through a real client session, for the test_mcp_* files.

Imported only after ``pytest.importorskip("mcp")``, so the suite still runs
without the extra.
"""

import os
import shutil

import pytest
from mcp import Client

from airfoil_converter.mcp.server import make_server

from conftest import SAMPLE_CSV


class Refused(AssertionError):
    """A tool answered with an error where an answer was expected."""


async def call(client, tool, /, **arguments):
    """A tool's structured answer, or :class:`Refused` carrying its error text."""
    result = await client.call_tool(tool, arguments)
    if result.is_error:
        raise Refused(result.content[0].text)
    return result.structured_content


async def refused(client, tool, /, **arguments):
    """The error text of a call that has to be refused."""
    result = await client.call_tool(tool, arguments)
    assert result.is_error, f"{tool} answered instead of refusing: {result.structured_content}"
    text = result.content[0].text
    assert "Traceback" not in text
    return text


def workspace_with_sample(folder):
    """A workspace holding a copy of sd7037-il.csv."""
    os.makedirs(str(folder), exist_ok=True)
    shutil.copy(SAMPLE_CSV, os.path.join(str(folder), "sd7037-il.csv"))
    return str(folder)


def session(workspace, read_roots=()):
    return Client(make_server(workspace, read_roots))


# A rib on a YZ plane, nose toward +Z, as conftest's synthetic wings stand.
def rib_spec(chord, x=0.0, z=0.0, **extra):
    spec = dict(
        export_camber=False,
        plane_mode="YZ",
        chord_axis="+Z",
        up_axis="+Y",
        target_chord=chord,
        leading_edge=[x, 0, z],
    )
    spec.update(extra)
    return spec


@pytest.fixture
def anyio_backend():
    return "asyncio"
