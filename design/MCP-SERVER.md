# MCP server

**Status: in progress on branch `mcp-server`.** This file is the build plan; it becomes the server's reference once the work lands.

## Goal

Let another agent drive the Airfoil Converter without its window: read a section, place and finish it, write the curve files, stand a wing up from ribs, and get back numbers it can check. The first client is the Optimizer's winglet study, whose running list of needs is
[CONVERTER_TOOLS.md](https://github.com/M0ooBot/Optimizer/blob/winglet/runs/winglet-b/CONVERTER_TOOLS.md) (Optimizer `winglet` branch, `runs/winglet-b/CONVERTER_TOOLS.md`).

## Ground rules

- **The core stays dependency-free.** `mcp` is an optional extra (`pip install airfoil-converter[mcp]`), exactly as `pywin32` is for the SolidWorks link. Nothing under `src/airfoil_converter/` outside `mcp/` may import it, and the core keeps Python 3.9 support; numpy and scipy are not added.
- **One code path.** A tool calls the same functions the window does (`export.build_curves`, `wing_build.build_wing`, `store`, `writer`). A rib exported through the server and the same rib exported from the window give byte-identical curve files. New geometry goes into a core module with its own unit tests, then gets a thin tool.
- **Projects are sidecars.** A project is a part path; its record is the same `*.airfoils.json` sidecar the window keeps (`store.sidecar_path`). The part file need not exist. If it does (a real `.SLDPRT` on Windows), the window opens the same records the agent wrote, and the reverse.
- **A workspace bounds the server.** Started with `--workspace DIR`; every path a tool takes is resolved against it and refused if it escapes (symlinks resolved). Extra read-only roots via `--read-root DIR` (repeatable) for source CSVs kept elsewhere. Writes only inside the workspace.
- **Small answers, files for bulk.** Tools return summaries (counts, chord, thickness, bounding boxes, names, paths, checks). Point lists come back only when asked (`include_points: true`), capped and rounded to the writer's precision. Big geometry goes to curve files the agent can pass on.
- **Errors are answers.** `InputError`, `GeometryError`, `AirfoilParseError`, `StoreError` become tool errors carrying the Converter's own message, which already says what to change. No tracebacks leak to the client.
- **Units and axes are stated once.** `converter_info` reports millimetres, degrees, the main-plane axis conventions and the meaning of every `ExportSpec` / `WingSpec` field, taken from the dataclasses so it cannot drift.
- **Transport:** stdio, `mcp>=2.3,<3` (`from mcp.server.mcpserver import MCPServer`; v2 renamed FastMCP). Console script `airfoil-converter-mcp`, also `python -m airfoil_converter.mcp`.

## Layout

```
src/airfoil_converter/mcp/
  __init__.py
  __main__.py      # python -m airfoil_converter.mcp
  server.py        # CLI, workspace and path rules, error mapping, make_server()
  tools_info.py    # converter_info
  tools_section.py # one section: read, finish, export, project records
  tools_wing.py    # wings: define, check, build, loft plan
  tools_profile.py # Phase 2 profile operations
```

Each `tools_*.py` exposes `register(server, ctx)` so phases can be built in parallel without touching the same file.

## Phase 1 — what the Converter already does

Done when every tool below exists, has tests through a real MCP client session (in-memory or stdio; `pytest.importorskip("mcp")` so the suite still runs without it), the existing suite still passes, and an agent can go from `sd7037-il.csv` to a lofted-wing curve set by tool calls alone.

| Tool | Does |
| --- | --- |
| `converter_info` | Version, git revision when run from a checkout, units, axes, field reference for `ExportSpec` and `WingSpec` (name, type, default, meaning), trailing-edge modes, whether SolidWorks is reachable (`swcom.is_available()` / `unavailable_reason()`), workspace and read roots. |
| `read_section` | Parse a plotter CSV or curve file. Name/metadata, point count, chord, leading- and trailing-edge points, max thickness and its station, closed or open, TE gap, for a curve its plane (`FlatSection`). Optional points. |
| `finish_section` | Run the export pipeline on a source with an `ExportSpec` (partial dict over defaults) without writing: returns each curve's role, feature name, point count, closed flag, bounding box, finished chord, TE thickness; optional points. |
| `export_rib` | Same, then write the curve files and record the rib in the project's sidecar (new or update by id/stem, exactly as the window does), keeping the window's naming and folder rules. Returns paths, feature names, sidecar path, and which files changed (`write_curve_if_changed`). |
| `list_project` | What a project's sidecar holds: ribs and wings, their specs, curves, file paths, and whether each file is present and matches its recorded hash. |
| `define_wing` | Create or update a wing record in the project: ribs (by id or stem), each edge (straight or a curve file), end conditions, thickness mode, offset. |
| `check_wing` | `build_wing(check=True)`: what `wing_build.describe` says, station count, planform (span, root/tip chord, LE/TE sweep samples), expected wall when offset, and any refusal. Writes nothing. |
| `build_wing` | Build and write the wing's curves (sections, edges, surface guides; inner set when offset), record them, and return the loft plan from `swloft.plan_for_wing`: which profiles in order and which guides, so a CAD bridge can loft without knowing the naming. |

## Phase 2 — what the winglet needs (CONVERTER_TOOLS.md)

Core module `src/airfoil_converter/profile.py` (pure Python, unit-tested against known values), tools in `tools_profile.py`. Each item names its acceptance check.

1. **Blunt trailing edge as a smooth family.** `blunt_trailing_edge` also reports the cut station and the regrow scale, so a caller can interpolate a smooth family by chord. Existing callers unchanged. Check: reported scale times the cut chord equals the original chord to 1e-9.
2. **NACA 4-digit** (`profile.naca4("0010", points=161, closed_te=True)`), cosine-spaced, Selig order, 161 points per surface (321 in all, nose shared) by default: enough that the spline through them has NACA's nose radius to within 0.5 %. Check: 0012 max thickness 12 % at 30 % chord to 1e-3.
3. **UIUC `.dat`** in the parser (Selig and Lednicer layouts, title line, count row as metadata, upper surface first whichever way the file runs), so the window's Browse reads it too — the `codex/uiuc-dat-import-note` proposal. The chord runs from the origin (where UIUC put the leading edge) to the tail, so a section in fractions has chord exactly 1 and a target chord scales every point by that many millimetres; such a section is marked as fractions of its chord and refuses to export without a target chord. Fetch by name from the UIUC database only when the server is started with `--allow-network`; save the fetched file into the workspace with its URL and retrieval time. Check: bundled copies of sd7037.dat and clarky.dat parse to the expected point counts, orientation and TE endpoints.
4. **Camber and thickness on a common grid.** Find the true nose on a spline through the points (not the nearest vertex: the point furthest from the middle of the trailing edge), resample upper and lower on a cosine grid, return camber and half-thickness. Thickness is measured square to the chord line from the true nose — not square to the camber line, as NACA lay their sections out — because that keeps a blunt trailing edge exactly the thickness it was cut to. Check: a NACA 2412 recovers its analytic camber, cut square to the chord as the split cuts it, to 1e-4 chord. Against NACA's own mean line the split's camber sits up to about 0.11 % of the chord off for a 2412 and 0.22 % for a 4412, near the nose.
5. **Morph between two sections** by weight, fading camber and blending thickness without its TE ramp, then re-adding a ramp that holds the TE at a given thickness in mm at a given chord. Check: weight 0 and 1 reproduce the inputs; TE thickness exact in mm at every weight.
6. **Leading-edge radius.** From the spline, the nose radius, and the radius normal to a swept leading edge given the sweep. Check: NACA 0012 nose radius 1.1019 t² ≈ 0.0159 c to 1 %.
7. **Place a section in any plane.** One call: profile (2D, chord-normalised or mm), leading-edge point, chord direction, up direction, chord, twist about a chord-fraction pivot. Returns the 3D curve, written if asked. Check: round trip through `flatten_curve` gives back the plane and the profile.
8. **Sections and guides along a caller's law.** The caller gives stations (each a placement as in 7, plus a profile or a morph weight); the server writes section curves, LE and TE curves, and surface guides through matching points at the window's guide fractions, and a loft plan. Check: every guide passes through every section within 1e-6 mm.

## Phase 3 — SolidWorks (Windows only, decide before starting)

Push curves into an open part (`swlink.push`) and loft (`swloft.loft_in_part` / `loft_and_export`) when `swcom` is available. Needs a Windows Python with pywin32 and a running SolidWorks, and overlaps SW-Agent's bridge role; the alternative is to stop at curve files and loft plans and let SW-Agent do the CAD. Not started until the designer chooses.

## Testing

`uv run --no-project --with pytest --with 'mcp>=2.3,<3' python -m pytest -m 'not slow'` (about 3 minutes for the whole suite). New server tests live in `tests/test_mcp_*.py`.
