"""converter_info: what this server is, and what every setting means.

The field reference is read off :class:`~..export.ExportSpec` and
:class:`~..export.WingSpec` themselves — each field's name, type and default,
with any comment the source carries beside it — so a field added to either
shows up here with no edit. Only the one-line meanings are written out below,
and a test holds them to the dataclasses exactly, as ``gui.SPEC_VARS`` is held.
"""

from __future__ import annotations

import dataclasses
import inspect
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

from .. import __version__, export, geometry, swcom, wing, writer
from ..export import ExportSpec, WingSpec
from .server import ServerContext, add_tool, vector

MEANINGS: Dict[str, Dict[str, str]] = {
    "ExportSpec": {
        "export_airfoil": "Write the airfoil surface.",
        "export_camber": "Write the camber line too. A CSV only: a curve file has none.",
        "te_mode": "How the trailing edge is finished: one of te_modes.",
        "te_thickness": (
            "Blunt the trailing edge to this thickness, in finished mm, cut on a line of "
            "constant chord station. '0' or blank keeps the section's own trailing edge."
        ),
        "keep_chord": (
            "After a blunting cut, grow the section back about its nose until it spans its "
            "original chord again; otherwise the chord ends at the cut."
        ),
        "target_chord": (
            "Rescale to this chord, in mm: the source's own chord (a CSV's Chord(mm), a UIUC "
            "file's from its origin) is scaled to it, as in the window — not the profile "
            "tools' true-nose chord. Blank keeps the source's chord."
        ),
        "offset": (
            "Offset the airfoil surface by this constant distance, in finished mm (never "
            "negative; offset_dir says which way). Blank for none. Not a rescale."
        ),
        "offset_dir": "Which way the offset goes: Inward or Outward.",
        "plane_mode": (
            "The plane the section is drawn on: XY, XZ or YZ; '3points' (chord P1->P2, P3 "
            "on the up side); '2points' (chord P1->P2, the plane perpendicular or parallel "
            "to main_plane); 'normal_line' (through P1, square to the line P1->P2); "
            "'loaded' (a curve file's own plane)."
        ),
        "constraint": "For '2points': the plane is Perpendicular or Parallel to main_plane.",
        "main_plane": "For '2points': the main plane the constraint refers to.",
        "chord_axis": (
            "On a main plane: the signed axis the nose points along (trailing edge to "
            "leading edge), such as '+Z'. Blank keeps the plane's default."
        ),
        "up_axis": (
            "On a main plane: the signed in-plane axis the upper surface faces. Blank "
            "keeps the plane's default."
        ),
        "flip": "Mirror which side of the chord counts as up.",
        "rotation": (
            "Turn the section in its plane by whole quarter turns: one of rotations. A turn "
            "goes the way a positive angle of attack does."
        ),
        "pitch": (
            "Angle of attack in degrees, nose up positive, about the reference point, "
            "applied last. Blank or '0' for none."
        ),
        "reference": (
            "The point the section is placed by, in percent of the chord from the nose: "
            "'0' the leading edge, '25' the quarter chord."
        ),
        "p1": "Point P1 (x, y, z in mm), for the 3points, 2points and normal_line planes.",
        "p2": "Point P2 (x, y, z in mm), for the 3points, 2points and normal_line planes.",
        "p3": "Point P3 (x, y, z in mm), for the 3points plane: on the up side of P1->P2.",
        "leading_edge": "Where the reference point lands (x, y, z in mm).",
        "extension": "The curve files' extension: one of extensions.",
        "le_manual": (
            "The window's own: whether the reference point was typed rather than filled in. "
            "Recorded so the window reopens it unchanged; it moves nothing."
        ),
    },
    "WingSpec": {
        "ribs": "The ribs the wing is lofted through, by export id. Two at least.",
        "le_source": "The leading-edge curve file, or blank for a straight line root to tip.",
        "te_source": "The trailing-edge curve file, or blank for a straight line root to tip.",
        "root_end": (
            "The root end: 'open' (the skin carries on, as at a mirrored centreline) or "
            "'closed' (a flat end face)."
        ),
        "tip_end": "The tip end: 'open' or 'closed'.",
        "swap_ends": "Call the rib further from the part's origin the root instead.",
        "offset": (
            "Offset the whole wing by this distance in mm (never negative; offset_dir says "
            "which way), writing the offset wing's own sections and edges. Blank for the "
            "wing itself."
        ),
        "offset_dir": "Which way the offset goes: Inward or Outward.",
        "extension": "The curve files' extension: one of extensions.",
        "thickness": (
            "How the thickness runs between ribs: 'blended' in mm from one to the next, as "
            "a SolidWorks loft does on its own, or 'scaled' (the airfoil kept whole and "
            "scaled to the chord)."
        ),
    },
}

CHOICES: Dict[str, Dict[str, List[str]]] = {
    "ExportSpec": {
        "te_mode": list(export.TE_MODES),
        "offset_dir": list(export.OFFSET_DIRECTIONS),
        "plane_mode": list(geometry.MAIN_PLANES) + [
            export.MODE_3POINTS, export.MODE_2POINTS, export.MODE_NORMAL, export.MODE_LOADED,
        ],
        "constraint": [geometry.PERPENDICULAR, geometry.PARALLEL],
        "main_plane": list(geometry.MAIN_PLANES),
        "rotation": list(export.ROTATIONS),
        "extension": list(writer.EXTENSIONS),
    },
    "WingSpec": {
        "root_end": list(wing.END_KINDS),
        "tip_end": list(wing.END_KINDS),
        "offset_dir": list(export.OFFSET_DIRECTIONS),
        "extension": list(writer.EXTENSIONS),
        "thickness": list(export.THICKNESS_CHOICES),
    },
}

TE_MODE_MEANINGS = {
    export.TE_CLOSE: "One closed curve: the last point made exactly the first.",
    export.TE_OPEN: "One open curve, left open at the trailing edge.",
    export.TE_SPLIT: "Two open curves, upper and lower, each leading edge to trailing edge.",
    export.TE_LINE: (
        "The surface as one open curve, and a second two-point curve closing the trailing "
        "edge with a straight line. For a blunt trailing edge."
    ),
}


def _plain(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def source_notes(cls: Any) -> Dict[str, str]:
    """The comments written beside each field in the dataclass's source.

    A comment block above a field, or one trailing it on its line. Empty when
    the source cannot be read, as in a frozen executable.
    """
    try:
        lines, _ = inspect.getsourcelines(cls)
    except (OSError, TypeError):
        return {}
    names = {f.name for f in dataclasses.fields(cls)}
    notes: Dict[str, str] = {}
    pending: List[str] = []
    for line in lines:
        text = line.strip()
        if text.startswith("#"):
            pending.append(text.lstrip("#").strip())
            continue
        match = re.match(r"(\w+)\s*:", text)
        if match and match.group(1) in names:
            said = list(pending)
            code, _, trailing = text.partition("  #")
            if trailing.strip():
                said.append(trailing.strip())
            if said:
                notes[match.group(1)] = " ".join(said)
        pending = []
    return notes


def field_reference(cls: Any) -> List[Dict[str, Any]]:
    """Every field of a spec: name, type, default, meaning, and any choices or note."""
    meanings = MEANINGS[cls.__name__]
    choices = CHOICES.get(cls.__name__, {})
    notes = source_notes(cls)
    defaults = cls()
    reference = []
    for f in dataclasses.fields(cls):
        entry: Dict[str, Any] = {
            "name": f.name,
            "type": f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type)),
            "default": _plain(getattr(defaults, f.name)),
            "meaning": meanings.get(f.name, ""),
        }
        if f.name in choices:
            entry["choices"] = choices[f.name]
        if f.name in notes:
            entry["note"] = notes[f.name]
        reference.append(entry)
    return reference


def git_revision() -> Optional[str]:
    """The commit this is running from, when it runs from a checkout of the repository."""
    package = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        # The server's stdin is the MCP channel: git must not read from it. On
        # Windows it must not open a console window either.
        top = subprocess.run(
            ["git", "-C", package, "rev-parse", "--show-toplevel", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    if len(top) != 2:
        return None
    # Installed into a virtual environment that happens to sit in some other
    # repository, the package is not that repository's source.
    if os.path.realpath(os.path.join(top[0], "src", "airfoil_converter")) != os.path.realpath(package):
        return None
    return top[1]


def axes() -> Dict[str, Any]:
    planes = []
    for plane in geometry.MAIN_PLANES:
        chord, up = geometry.default_axes(plane)
        u, v = geometry.main_plane_frame(plane)
        planes.append({
            "plane": plane,
            "default_chord_axis": chord,
            "default_up_axis": up,
            "chord_axis_choices": list(geometry.chord_axis_options(plane)),
            "default_chord_direction": vector(u),
            "default_up_direction": vector(v),
        })
    return {
        "conventions": (
            "A chord_direction runs nose to tail; a chord_axis names the opposite, the way "
            "the nose points. Up is the side the upper surface faces. Points are (x, y, z) "
            "in the part's own millimetres."
        ),
        "main_planes": planes,
        "chord_axis": inspect.getdoc(geometry.chord_axis_options),
        "plane_frame": inspect.getdoc(geometry.plane_frame),
        "placement": inspect.getdoc(export.placement),
    }


def describe(ctx: ServerContext) -> Dict[str, Any]:
    """Everything converter_info answers."""
    return {
        "name": "Airfoil Converter",
        "version": __version__,
        "revision": git_revision(),
        "units": {"length": "mm", "angle": "degrees"},
        "axes": axes(),
        "fields": {
            "ExportSpec": field_reference(ExportSpec),
            "WingSpec": field_reference(WingSpec),
        },
        "te_modes": [{"mode": mode, "meaning": TE_MODE_MEANINGS[mode]} for mode in export.TE_MODES],
        "settings_are_text": (
            "Every number in a spec is kept as the text given ('175', not 175.0), as the "
            "window keeps what was typed; a number sent here is turned into its text."
        ),
        "curve_files": {
            "extensions": list(writer.EXTENSIONS),
            "decimals": writer.DECIMALS,
            "unit": writer.UNIT_SUFFIX,
        },
        "solidworks": {
            "available": swcom.is_available(),
            "reason": swcom.unavailable_reason(),
        },
        "capabilities": {
            "sources": ["plotter CSV (mm)", "UIUC .dat (fractions of the chord)",
                        "curve file (mm, on its own plane)"],
            "sections": ["read_section", "finish_section", "export_rib", "list_project"],
            "wings": ["check_wing", "build_wing"],
            "profiles": ["naca4", "blunt_trailing_edge", "split_section", "morph_sections",
                         "nose_radius", "place_section", "build_law"],
            "fetch_uiuc": ctx.allow_network,
            "drives_solidworks": False,
        },
        "network": ctx.allow_network,
        "chord_frame": (
            "Profile tools work in the section's own chord frame: the true nose (the point of "
            "the curve through the points furthest from the middle of the trailing edge) at "
            "the origin, the middle of the trailing edge at (1, 0), thickness square to that "
            "chord. A profile tool's chord_mm is that chord, true nose to the middle of the "
            "trailing edge. An export's target_chord keeps the window's meaning instead: the "
            "source's own chord scaled to it — a CSV's Chord(mm), a UIUC file's from its "
            "origin, a curve file's longest span — which can differ from the true chord by "
            "a few hundredths of a millimetre. A section in fractions of its chord (a UIUC "
            ".dat, a NACA or morphed profile) needs a chord in mm to be exported or answered "
            "in mm."
        ),
        "workspace": ctx.workspace,
        "read_roots": list(ctx.read_roots),
    }


def register(server: Any, ctx: ServerContext) -> None:
    def converter_info() -> Dict[str, Any]:
        """What this server is: version, units and axes, the meaning of every ExportSpec
        and WingSpec field (name, type, default, choices), the trailing-edge modes, the
        profile tools' chord frame, what it can do (and whether it may use the network),
        whether SolidWorks is reachable, and the folders it may read and write."""
        return describe(ctx)

    add_tool(server, converter_info, read_only=True, idempotent=True)
