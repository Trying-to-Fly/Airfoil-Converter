"""The MCP server: the Converter, driven by another agent instead of the window.

Every tool goes through the functions the window uses — :func:`export.build_curves`,
:func:`wing_build.build_wing`, :mod:`store`, :mod:`writer` — so a rib or a wing
written here is the one the window would have written, byte for byte. What
lives here is only what a server needs and the window does not: the command
line, the workspace a server may not step outside, the Converter's own errors
turned into tool errors, and the shapes the answers share.

A project is a part's path, and its record is the sidecar the window keeps
beside that part (``store.sidecar_path``). The part itself need not exist; if
it does, the window opens the same records an agent wrote, and the reverse.

``mcp`` is an optional extra, as pywin32 is for the SolidWorks link, so nothing
outside this package imports it.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import inspect
import json
import math
import os
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .. import __version__, store, writer
from ..export import InputError
from ..geometry import GeometryError
from ..parser import AirfoilParseError

NAME = "airfoil-converter"

# A point list comes back at the precision the writer puts on disk, and never
# longer than this: asking for points must not flood the client.
MAX_POINTS = 5000
# Chords, gaps, bounding boxes: to a millionth of a millimetre.
SUMMARY_DECIMALS = 6

INSTRUCTIONS = """\
Airfoil Converter: airfoil sections to SolidWorks Curve Through XYZ Points
files, and wings lofted from them. Millimetres and degrees, except where a
section is in fractions of its chord: a UIUC .dat file, and the NACA and
morphed profiles the profile tools make.

Start with converter_info: it says what every setting means. Then:
read_section to look at a source (a plotter CSV, a UIUC .dat file or a curve
file); finish_section to see what an export would make, writing nothing;
export_rib to write a rib's curve files and record it in a project;
check_wing to try a wing from those ribs and build_wing to write its curves
and record it, with the loft plan a CAD bridge needs; list_project to see
what a project holds.

The profile tools work on a section's shape: naca4 makes one,
blunt_trailing_edge cuts its trailing edge, split_section gives camber and
thickness, morph_sections blends two, nose_radius measures the nose,
place_section stands one in space, and build_law lays sections along stations
of your own with edges and guides. fetch_uiuc, when the server allows the
network, downloads a UIUC coordinate file.

A project is a part's path, such as "wing.SLDPRT" (the part need not exist):
its record is the "*.airfoils.json" file the Converter's window keeps beside
it. Paths are relative to the server's workspace; nothing is written outside
it, and sources may also be read from the server's read roots.
"""


class PathError(Exception):
    """A path that resolves outside what the server may read or write."""


class Refused(Exception):
    """A request the server itself turns down, saying why: a fetch that failed, say."""


# The Converter's own refusals. Each already says what to change, so it goes
# to the client as it is; the window shows the same text in its status line.
# Nothing broader: an error the Converter did not mean for the user is the
# SDK's to report, which it does without its text.
ANSWERED = (InputError, GeometryError, AirfoilParseError, store.StoreError, PathError, Refused)


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # a different drive on Windows
        return False


@dataclasses.dataclass
class Project:
    """A part's path, where its record lives, and the record as read."""

    part_path: str
    sidecar_path: str
    sidecar: store.Sidecar


@dataclasses.dataclass
class ServerContext:
    """What every tool shares: the workspace, the read roots, and one lock.

    Paths are resolved with symlinks followed before they are checked, so a
    link inside the workspace pointing out of it is refused like the path it
    points to. The lock is held by every tool that reads a project's record and
    writes it back, so two calls at once cannot lose each other's records.
    """

    workspace: str
    read_roots: Tuple[str, ...] = ()
    lock: Any = dataclasses.field(default_factory=threading.RLock)
    # Whether a tool may reach the network (--allow-network), and what it
    # reaches it with: a stand-in in tests, never the real network there.
    allow_network: bool = False
    opener: Optional[Callable[..., Any]] = None

    @classmethod
    def create(cls, workspace: str, read_roots: Sequence[str] = (), allow_network: bool = False,
               opener: Optional[Callable[..., Any]] = None) -> "ServerContext":
        def folder(path: str, what: str) -> str:
            real = os.path.realpath(path)
            if not os.path.isdir(real):
                raise PathError(f"The {what} {path} is not a folder.")
            return real

        return cls(
            workspace=folder(workspace, "workspace"),
            read_roots=tuple(folder(root, "read root") for root in read_roots),
            allow_network=allow_network,
            opener=opener,
        )

    # -- paths --------------------------------------------------------------

    def _resolve(self, path: str, what: str) -> str:
        if not isinstance(path, str) or not path.strip():
            raise PathError(f"No {what} was given.")
        if "\0" in path:
            raise PathError(f"The {what} {path!r} is not a path.")
        return os.path.realpath(os.path.join(self.workspace, path))

    def readable(self, path: str, what: str = "file") -> str:
        """A path the server may read: in the workspace or a read root."""
        real = self._resolve(path, what)
        if any(_inside(real, root) for root in (self.workspace,) + self.read_roots):
            return real
        roots = ", ".join((self.workspace,) + self.read_roots)
        raise PathError(
            f"The {what} {path} is outside the folders this server may read ({roots}). "
            "Copy it into the workspace, or start the server with --read-root for its folder."
        )

    def resolved(self, path: str) -> Optional[str]:
        """A path a record names, resolved as a tool's would be; None if it may not be read.

        A stored path is never opened as it stands: a relative one would be
        read against wherever the server happened to start.
        """
        try:
            return self.readable(path)
        except PathError:
            return None

    def writable(self, path: str, what: str = "file") -> str:
        """A path the server may write: in the workspace, and nowhere else."""
        real = self._resolve(path, what)
        if _inside(real, self.workspace):
            return real
        raise PathError(
            f"The {what} {path} is outside the workspace ({self.workspace}), "
            "and the server writes nowhere else."
        )

    def show(self, path: str) -> str:
        """A path as an answer gives it: relative to the workspace when it is in it."""
        if not path:
            return path
        real = os.path.realpath(path)
        if _inside(real, self.workspace):
            return os.path.relpath(real, self.workspace).replace(os.sep, "/")
        return path

    # -- projects -----------------------------------------------------------

    def open_project(self, project: str, create: bool = False,
                     read_only: bool = False, check_writes: bool = False) -> Project:
        """A project's record, or a fresh one when ``create`` and there is none yet.

        A record that cannot be read is moved aside by :func:`store.load`, as
        the window does, and the error says so — unless ``read_only``, when it
        is left where it is: a tool that only reads changes nothing. A preview
        can use ``check_writes`` to validate the same targets as the actual write.
        """
        part = self.writable(project, "project")
        if os.path.isdir(part):
            raise PathError(
                f"The project {project} is a folder. A project is a part's path, "
                "such as wing.SLDPRT; the part itself need not exist."
            )
        sidecar_path = store.sidecar_path(part)
        if not read_only or check_writes:
            self.check_targets([sidecar_path])
        else:
            sidecar_path = self.writable(sidecar_path, "project record")
        sidecar = store.load(sidecar_path, move_aside=not read_only)
        if sidecar is None:
            if not create:
                raise InputError(
                    f"{self.show(part)} has no record yet ({self.show(sidecar_path)}). "
                    "Export a rib into it first."
                )
            sidecar = store.Sidecar(part_path=part, part_title=os.path.basename(part))
        return Project(part_path=part, sidecar_path=sidecar_path, sidecar=sidecar)

    def save_project(self, project: Project) -> bool:
        """Write the record as the window does: stamped with this version and its part."""
        self.check_targets([project.sidecar_path])
        sidecar = project.sidecar
        sidecar.written_by = f"airfoil-converter {__version__}"
        sidecar.part_path = project.part_path
        sidecar.part_title = os.path.basename(project.part_path)
        return store.save(project.sidecar_path, sidecar)

    def output_folder(self, folder: str, make: bool = True) -> str:
        """A folder to write curves into, made if it is not there yet and ``make``."""
        real = self.writable(folder, "output folder")
        self._check_folder(real)
        if make:
            os.makedirs(real, exist_ok=True)
        return real

    def _check_folder(self, folder: str) -> None:
        """Check that the nearest existing ancestor can contain an output folder."""
        parent = folder
        while parent and not os.path.isdir(parent):
            if os.path.lexists(parent):
                raise PathError(f"The output folder {self.show(parent)} is a file.")
            ancestor = os.path.dirname(parent)
            if parent == ancestor:
                break
            parent = ancestor

    def target_file(self, path: str, what: str = "file", make: bool = True) -> str:
        """Where a file named by a tool is written: its folder resolved, its name not.

        Only the folder is resolved, and must lie in the workspace; the name is
        joined on as given and :meth:`check_targets` refuses it if it, or its
        ``.tmp``, is a link. Resolving the whole path instead would follow a
        link at the name itself and write wherever it points — into a project's
        record, say — and check the extension of the name given rather than the
        file written.
        """
        if not isinstance(path, str) or not path.strip():
            raise PathError(f"No {what} was given.")
        folder, base = os.path.split(os.path.join(self.workspace, path))
        if base in ("", ".", ".."):
            raise PathError(f"The {what} {path} names a folder, not a file.")
        target = os.path.join(self.output_folder(folder, make=make), base)
        if os.path.isdir(target):
            raise PathError(f"The {what} {path} is a folder.")
        self.check_targets([target])
        return target

    def check_targets(self, paths: Sequence[str], *, reserved: Sequence[str] = ()) -> None:
        """Refuse to write any of ``paths`` unless every one can be written safely.

        The writer and the store write through ``path + ".tmp"`` and rename it
        over the file, and ``open`` follows a link: a link planted at either
        name would carry the write wherever it points. So neither may be a
        link, and both must resolve inside the workspace. All are checked
        before anything is written, including directories that occupy a file
        name and files that occupy a parent directory, including files this
        same operation plans to write. Reserved paths cannot be used for
        output files or directories.
        """
        files = []
        for path in paths:
            for target in (path, path + ".tmp"):
                if os.path.islink(target):
                    raise PathError(
                        f"{os.path.basename(target)} in {self.show(os.path.dirname(target))} "
                        "is a symbolic link, and the server does not write through one."
                    )
                files.append(self.writable(target))
                if os.path.isdir(target):
                    raise PathError(f"The file {self.show(target)} is a folder.")
                self._check_folder(os.path.dirname(target))

        reserved_paths = {os.path.normcase(path) for path in reserved}
        planned_files = {os.path.normcase(file) for file in files}
        for file in files:
            if os.path.normcase(file) in reserved_paths:
                raise PathError(f"The output would overwrite {self.show(file)}.")
            parent = os.path.dirname(file)
            while os.path.dirname(parent) != parent:
                if os.path.normcase(parent) in reserved_paths:
                    raise PathError(f"The output would use {self.show(parent)} as a folder.")
                if os.path.normcase(parent) in planned_files:
                    raise PathError(
                        f"The output needs {self.show(parent)} as both a file and a folder."
                    )
                parent = os.path.dirname(parent)

    def write_file(self, path: str, data: bytes) -> bool:
        """Write ``data`` to a checked path the way the writer does, if it changed.

        Through a temporary file beside it, renamed over it, so nothing ever
        sees half a file; and only after :meth:`check_targets`. Returns whether
        the file changed.
        """
        self.check_targets([path])
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                if handle.read() == data:
                    return False
        temporary = path + ".tmp"
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
        return True


# -- the shapes answers share ------------------------------------------------


def rounded(value: float, decimals: int = SUMMARY_DECIMALS) -> float:
    value = round(float(value), decimals)
    return 0.0 if value == 0.0 else value  # never a signed zero


def vector(values: Sequence[float], decimals: int = SUMMARY_DECIMALS) -> List[float]:
    return [rounded(v, decimals) for v in values]


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((p - q) ** 2 for p, q in zip(a, b)))


def bounding_box(points: Sequence[Sequence[float]]) -> Dict[str, List[float]]:
    columns = list(zip(*points))
    return {"min": vector(min(c) for c in columns), "max": vector(max(c) for c in columns)}


def point_list(points: Sequence[Sequence[float]]) -> Dict[str, Any]:
    """Points at the writer's precision, capped at :data:`MAX_POINTS`."""
    kept = points[:MAX_POINTS]
    return {
        "points": [vector(p, writer.DECIMALS) for p in kept],
        "truncated": len(points) > len(kept),
    }


def settings_from(cls: Any, raw: Optional[Dict[str, Any]], base: Any = None,
                  skip: Sequence[str] = ()) -> Any:
    """A spec from a partial dict laid over ``base`` (the dataclass's defaults when None).

    The specs keep every value as the text that was typed, so a number given
    here becomes its text; a name the spec does not have is refused rather than
    quietly dropped, since a misspelt setting would otherwise do nothing.
    """
    raw = raw or {}
    if not isinstance(raw, dict):
        raise InputError("Settings must be an object of setting names and values.")
    defaults = cls()
    known = [f.name for f in dataclasses.fields(cls) if f.name not in skip]
    unknown = sorted(k for k in raw if k not in known)
    if unknown:
        raise InputError(
            f"Unknown setting(s) {', '.join(unknown)}. Known: {', '.join(known)}."
        )
    values = (base if base is not None else defaults).to_dict()
    for key, value in raw.items():
        values[key] = _as_field(key, value, getattr(defaults, key))
    return cls.from_dict(values)


def number_text(value: float) -> str:
    """A number as it would be typed: 200, not 200.0, and every digit of 0.1234567891."""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def _as_text(name: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputError(f"{name} must be text or a number (got {value!r}).")
    return number_text(value) if isinstance(value, (int, float)) else value


def _as_field(name: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise InputError(f"{name} must be true or false (got {value!r}).")
        return value
    if isinstance(default, tuple):
        if not isinstance(value, (list, tuple)):
            raise InputError(f"{name} must be a list (got {value!r}).")
        return [_as_text(name, v) for v in value]
    return _as_text(name, value)


# -- tools -------------------------------------------------------------------


def add_tool(
    server: MCPServer,
    fn: Callable[..., Dict[str, Any]],
    *,
    name: Optional[str] = None,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
) -> None:
    """Register ``fn`` as a tool whose answer is its dict, and whose refusals are answers.

    The dict goes back as structured content, and as the same JSON in text for
    a client that reads only that. The Converter's own errors become tool errors
    carrying its message; anything else is the SDK's to report, which it does
    without the traceback.
    """
    signature = inspect.signature(fn, eval_str=True)

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> CallToolResult:
        try:
            answer = fn(*args, **kwargs)
        except ANSWERED as exc:
            raise ToolError(str(exc)) from None
        except OSError as exc:
            raise ToolError(f"Could not read or write a file: {exc}") from None
        text = json.dumps(answer, indent=2)
        return CallToolResult(content=[TextContent(type="text", text=text)],
                              structured_content=answer)

    run.__signature__ = signature.replace(return_annotation=CallToolResult)  # type: ignore[attr-defined]
    server.add_tool(
        run,
        name=name,
        annotations=ToolAnnotations(
            read_only_hint=read_only,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=open_world,
        ),
    )


def make_server(workspace: str, read_roots: Sequence[str] = (), allow_network: bool = False,
                opener: Optional[Callable[..., Any]] = None) -> MCPServer:
    """The server, every tool registered, bounded by ``workspace``.

    ``allow_network`` registers the tools that fetch from the internet, and
    nothing else does; ``opener`` replaces how they fetch (see
    :func:`.tools_profile.fetch_url`), for tests.
    """
    from . import tools_info, tools_profile, tools_section, tools_wing

    ctx = ServerContext.create(workspace, read_roots, allow_network, opener)
    server = MCPServer(NAME, version=__version__, instructions=INSTRUCTIONS)
    for module in (tools_info, tools_section, tools_wing, tools_profile):
        module.register(server, ctx)
    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airfoil-converter-mcp",
        description="Serve the Airfoil Converter's tools over MCP, on stdio.",
    )
    parser.add_argument(
        "--workspace", required=True,
        help="the folder every path is resolved against; nothing is written outside it",
    )
    parser.add_argument(
        "--read-root", action="append", default=[], dest="read_roots", metavar="DIR",
        help="another folder sources may be read from (read only; repeatable)",
    )
    parser.add_argument(
        "--allow-network", action="store_true",
        help="offer fetch_uiuc, which downloads coordinate files from the UIUC database",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        server = make_server(args.workspace, args.read_roots, args.allow_network)
    except PathError as exc:
        print(f"airfoil-converter-mcp: {exc}", file=sys.stderr)
        return 2
    server.run("stdio")
    return 0
