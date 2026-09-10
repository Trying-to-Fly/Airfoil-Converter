"""Single-window tkinter front end for the airfoil converter."""

from __future__ import annotations

import os
import sys
import tkinter as tk
import uuid
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Any, Dict, List, Optional

from . import geometry, parser, store, swcom, swlink, writer
from .export import (
    MODE_2POINTS,
    MODE_3POINTS,
    MODE_LOADED,
    MODE_NORMAL,
    OFFSET_DIRECTIONS,
    OFFSET_INWARD,
    ROTATIONS,
    TE_CLOSE,
    TE_MODES,
    ROLE_JOINED,
    ExportSpec,
    InputError,
    build_curves,
    feature_name,
    joinable,
)
from .geometry import GeometryError
from .parser import AIRFOIL, AirfoilData, AirfoilParseError, parse_csv
from . import __version__

OK_COLOR = "#1a7f37"
ERROR_COLOR = "#b42318"

# Tabbing or arrowing through the leading-edge fields is not an edit.
NAVIGATION_KEYS = frozenset(
    {
        "Tab", "ISO_Left_Tab", "Left", "Right", "Up", "Down", "Home", "End",
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    }
)

# Every ExportSpec field, and the widget variable holding it. Kept as data so a
# test can prove it covers the dataclass exactly: a field added without a line
# here would be silently dropped when a curve's settings are put back.
SPEC_VARS = (
    ("export_airfoil", "export_airfoil"),
    ("export_camber", "export_camber"),
    ("te_mode", "te_mode"),
    ("te_thickness", "te_thickness"),
    ("keep_chord", "keep_chord"),
    ("target_chord", "target_chord"),
    ("offset", "offset"),
    ("offset_dir", "offset_dir"),
    ("plane_mode", "plane_mode"),
    ("constraint", "constraint"),
    ("main_plane", "main_plane"),
    ("chord_axis", "chord_axis"),
    ("up_axis", "up_axis"),
    ("flip", "flip"),
    ("rotation", "rotation"),
    ("pitch", "pitch"),
    ("extension", "extension"),
)

# Held in grids of variables rather than one each, or derived from app state.
SPEC_COMPOUND = ("p1", "p2", "p3", "leading_edge", "le_manual")


def _record_label(record: "Optional[store.ExportRecord]", export_id: str) -> str:
    """Several ribs come off one aerofoil, so the stem alone does not tell them apart."""
    if record is None:
        return export_id
    return record.stem if record.name_index <= 1 else f"{record.stem} ({record.name_index})"


def _record_place(record: "Optional[store.ExportRecord]") -> str:
    """Where the leading edge sits, which is what distinguishes one rib from the next."""
    if record is None:
        return ""
    try:
        x, y, z = ExportSpec.from_dict(record.settings).leading_edge
    except Exception:  # noqa: BLE001
        return ""
    return f"{x}, {y}, {z}"


def _read_key(session) -> tuple:
    """The cheapest question worth asking every second."""
    document = session.active_document()
    return (
        session.pid,
        document.title if document else "",
        document.path if document else "",
        len(session.features()) if document and document.is_part else 0,
    )


def _read_snapshot(session) -> dict:
    """Everything the panel draws, as plain data. No COM object comes back."""
    document = session.active_document()
    curves: List[str] = []
    if document is not None and document.is_part:
        curves = [f.name for f in session.curve_features()]
    return {
        "key": (
            session.pid,
            document.title if document else "",
            document.path if document else "",
            len(session.features()) if document and document.is_part else 0,
        ),
        "version": session.label,
        "newer_than_tested": swcom.is_newer_than_tested(session.revision),
        "title": document.title if document else "",
        "path": document.path if document else "",
        "is_part": bool(document and document.is_part),
        "curves": curves,
    }


class ConverterApp(ttk.Frame):
    def __init__(self, master: tk.Misc, scale: float = 1.0) -> None:
        super().__init__(master, padding=int(round(12 * scale)))
        self.scale = scale
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.data: Optional[AirfoilData] = None
        self.section: Optional[geometry.FlatSection] = None  # Set by a curve file only.
        self._le_manual = False
        self._syncing = False
        self._syncing_axes = False

        self.csv_path = tk.StringVar()
        self.loaded_text = tk.StringVar(value="No file loaded.")
        self.export_airfoil = tk.BooleanVar(value=True)
        self.export_camber = tk.BooleanVar(value=True)
        self.te_mode = tk.StringVar(value=TE_CLOSE)
        self.te_thickness = tk.StringVar(value="0")
        self.keep_chord = tk.BooleanVar(value=False)
        self.target_chord = tk.StringVar()
        self.offset = tk.StringVar()
        self.offset_dir = tk.StringVar(value=OFFSET_INWARD)
        self.plane_mode = tk.StringVar(value="XY")
        self.constraint = tk.StringVar(value=geometry.PERPENDICULAR)
        self.main_plane = tk.StringVar(value="XY")
        self.flip = tk.BooleanVar(value=False)
        self.rotation = tk.StringVar(value=ROTATIONS[0])
        self.pitch = tk.StringVar(value="0")
        default_chord, default_up = geometry.default_axes("XY")
        self.chord_axis = tk.StringVar(value=default_chord)
        self.up_axis = tk.StringVar(value=default_up)
        self.extension = tk.StringVar(value=".sldcrv")
        self.out_folder = tk.StringVar()
        self.status = tk.StringVar(value="Load a CSV to begin.")

        self.sw_status = tk.StringVar(value="Looking for SolidWorks...")
        self.editing_text = tk.StringVar(value="Export will make a new curve.")
        self.insert_missing = tk.BooleanVar(value=True)

        # The link to SolidWorks. None until the first look, and dropped again
        # whenever a call fails, so a reopened session is picked up on its own.
        self._worker: Optional[swcom.Worker] = None
        self._pending: Optional[Any] = None
        self._pending_kind = ""
        self._pending_push: Optional[Any] = None
        self._push_context: Optional[Dict[str, Any]] = None
        self._snapshot: Optional[dict] = None
        self._sidecar: Optional[store.Sidecar] = None
        self._sidecar_path = ""
        self._editing: str = ""          # the export id being edited, or ""
        self._restoring = False
        self._quiet_ticks = 0

        self.p_vars: List[List[tk.StringVar]] = [
            [tk.StringVar(value="0") for _ in range(3)] for _ in range(3)
        ]
        self.le_vars = [tk.StringVar(value="0") for _ in range(3)]

        self.p_entries: List[List[ttk.Entry]] = []

        self._build()
        for row in self.p_vars:
            for var in row:
                var.trace_add("write", lambda *_: self._sync_leading_edge())
        self.chord_axis.trace_add("write", lambda *_: self._on_chord_axis_changed())
        self._on_plane_mode_changed()

        self._start_link()
        master.bind("<FocusIn>", self._on_window_focused, add="+")

    # ---------------------------------------------------------------- layout

    def _px(self, pixels: int) -> int:
        """A pixel count, grown to the screen's DPI. Fonts scale themselves; these do not."""
        return int(round(pixels * self.scale))

    def _build(self) -> None:
        """The form on the left, the live view of the part on the right.

        Side by side rather than stacked: the panel is read *while* filling the
        form in — click a curve, read the fields — and the window is already
        six sections tall.
        """
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        form = ttk.Frame(self)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(0, weight=1)
        self._build_form(form)

        self._build_solidworks(self)

    def _build_form(self, parent: tk.Misc) -> None:
        row = 0
        pad = self._px(8)

        source = ttk.LabelFrame(parent, text="Source", padding=pad)
        source.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="CSV or curve:").grid(row=0, column=0, sticky="w")
        ttk.Entry(source, textvariable=self.csv_path).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(source, text="Browse", command=self._browse_csv).grid(row=0, column=2)
        ttk.Label(source, textvariable=self.loaded_text, foreground="#555").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        row += 1

        export = ttk.LabelFrame(parent, text="Export", padding=pad)
        export.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        export.columnconfigure(3, weight=1)
        ttk.Checkbutton(export, text="Airfoil surface", variable=self.export_airfoil).grid(
            row=0, column=0, sticky="w"
        )
        self.camber_check = ttk.Checkbutton(
            export, text="Camber line", variable=self.export_camber
        )
        self.camber_check.grid(row=0, column=1, sticky="w", padx=(12, 0))

        ttk.Label(export, text="TE handling:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            export,
            textvariable=self.te_mode,
            values=TE_MODES,
            state="readonly",
            width=18,
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(
            export,
            text="(TE line = the surface open, plus a\ntwo-point curve closing the gap)",
            foreground="#555",
            justify="left",
        ).grid(row=1, column=3, sticky="w", padx=(6, 0), pady=(6, 0))

        ttk.Label(export, text="TE thickness (mm):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(export, textvariable=self.te_thickness, width=12).grid(
            row=2, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Checkbutton(
            export, text="Keep chord after the cut", variable=self.keep_chord
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=(12, 0), pady=(6, 0))
        ttk.Label(
            export,
            text="(0 = keep the section's own trailing edge, and the full chord. Otherwise it is\n"
            "cut back to a vertical line where it stands this thick — so it comes out shorter,\n"
            "unless 'Keep chord' scales it back up to the full chord with the gap still exact.)",
            foreground="#555",
            justify="left",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))

        ttk.Label(export, text="Target chord (mm):").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(export, textvariable=self.target_chord, width=12).grid(
            row=4, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Label(export, text="(blank = keep CSV chord)", foreground="#555").grid(
            row=4, column=2, sticky="w", padx=(6, 0), pady=(6, 0)
        )

        ttk.Label(export, text="Offset (mm):").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(export, textvariable=self.offset, width=12).grid(
            row=5, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Combobox(
            export,
            textvariable=self.offset_dir,
            values=OFFSET_DIRECTIONS,
            state="readonly",
            width=9,
        ).grid(row=5, column=2, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(
            export,
            text="(blank = none. Offsets the surface at a constant distance, like\n"
            "SolidWorks Offset Entities — the camber line is left alone.)",
            foreground="#555",
            justify="left",
        ).grid(row=6, column=0, columnspan=4, sticky="w", pady=(2, 0))
        row += 1

        plane = ttk.LabelFrame(parent, text="Plane", padding=pad)
        plane.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        radios = ttk.Frame(plane)
        radios.grid(row=0, column=0, columnspan=8, sticky="w")
        for i, (label, value) in enumerate(
            [
                ("XY", "XY"),
                ("XZ", "XZ"),
                ("YZ", "YZ"),
                ("3 points", MODE_3POINTS),
                ("2 points + constraint", MODE_2POINTS),
            ]
        ):
            ttk.Radiobutton(
                radios,
                text=label,
                value=value,
                variable=self.plane_mode,
                command=self._on_plane_mode_changed,
            ).grid(row=0, column=i, sticky="w", padx=(0, 10))
        ttk.Radiobutton(
            radios,
            text="Normal to line",
            value=MODE_NORMAL,
            variable=self.plane_mode,
            command=self._on_plane_mode_changed,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Label(
            radios, text="(the plane through P1 perpendicular to the line P1 → P2)",
            foreground="#555",
        ).grid(row=1, column=3, columnspan=3, sticky="w", pady=(2, 0))
        self.loaded_radio = ttk.Radiobutton(
            radios,
            text="As loaded",
            value=MODE_LOADED,
            variable=self.plane_mode,
            command=self._on_plane_mode_changed,
            state="disabled",
        )
        self.loaded_radio.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Label(
            radios, text="(keeps a loaded curve on its own plane, where it stands)",
            foreground="#555",
        ).grid(row=2, column=3, columnspan=3, sticky="w")

        for pi, name in enumerate(("P1", "P2", "P3")):
            ttk.Label(plane, text=name).grid(row=1 + pi, column=0, sticky="w", pady=(4, 0))
            entries = []
            for ci, axis in enumerate("XYZ"):
                ttk.Label(plane, text=axis).grid(
                    row=1 + pi, column=1 + ci * 2, sticky="e", padx=(6, 2), pady=(4, 0)
                )
                entry = ttk.Entry(plane, textvariable=self.p_vars[pi][ci], width=9)
                entry.grid(row=1 + pi, column=2 + ci * 2, sticky="w", pady=(4, 0))
                entries.append(entry)
            self.p_entries.append(entries)

        ttk.Label(plane, text="Constraint:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.constraint_box = ttk.Combobox(
            plane,
            textvariable=self.constraint,
            values=(geometry.PERPENDICULAR, geometry.PARALLEL),
            state="readonly",
            width=14,
        )
        self.constraint_box.grid(row=4, column=1, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(plane, text="to").grid(row=4, column=4, sticky="e", pady=(6, 0))
        self.main_plane_box = ttk.Combobox(
            plane,
            textvariable=self.main_plane,
            values=geometry.MAIN_PLANES,
            state="readonly",
            width=5,
        )
        self.main_plane_box.grid(row=4, column=5, sticky="w", padx=(4, 0), pady=(6, 0))

        ttk.Label(plane, text="Chord runs along:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.chord_axis_box = ttk.Combobox(
            plane,
            textvariable=self.chord_axis,
            values=geometry.chord_axis_options("XY"),
            state="readonly",
            width=5,
        )
        self.chord_axis_box.grid(row=5, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(plane, text="Up direction:").grid(row=5, column=3, sticky="e", pady=(6, 0))
        self.up_axis_box = ttk.Combobox(
            plane,
            textvariable=self.up_axis,
            values=geometry.up_axis_options("XY", "+X"),
            state="readonly",
            width=5,
        )
        self.up_axis_box.grid(row=5, column=4, columnspan=2, sticky="w", padx=(4, 0), pady=(6, 0))
        ttk.Label(
            plane, text="(trailing edge → leading edge: the nose points this way)",
            foreground="#555",
        ).grid(row=6, column=0, columnspan=6, sticky="w")

        self.flip_check = ttk.Checkbutton(plane, text="Flip up direction", variable=self.flip)
        self.flip_check.grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(plane, text="Rotate in plane:").grid(row=8, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            plane,
            textvariable=self.rotation,
            values=ROTATIONS,
            state="readonly",
            width=6,
        ).grid(row=8, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(
            plane,
            text="(quarter turns about the leading edge, the way a\n+ angle of attack turns)",
            foreground="#555",
            justify="left",
        ).grid(row=8, column=3, columnspan=3, sticky="w", padx=(6, 0), pady=(6, 0))

        ttk.Label(plane, text="Angle of attack (°):").grid(row=9, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(plane, textvariable=self.pitch, width=9).grid(
            row=9, column=1, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Label(
            plane, text="(+ pitches the nose up, about the leading edge)", foreground="#555"
        ).grid(row=9, column=3, columnspan=3, sticky="w", padx=(6, 0), pady=(6, 0))
        row += 1

        placement = ttk.LabelFrame(parent, text="Placement", padding=pad)
        placement.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(placement, text="Leading edge at:").grid(row=0, column=0, sticky="w")
        self.le_entries: List[ttk.Entry] = []
        for ci, axis in enumerate("XYZ"):
            ttk.Label(placement, text=axis).grid(row=0, column=1 + ci * 2, sticky="e", padx=(6, 2))
            entry = ttk.Entry(placement, textvariable=self.le_vars[ci], width=9)
            entry.grid(row=0, column=2 + ci * 2, sticky="w")
            entry.bind("<Key>", self._on_le_typed)
            self.le_entries.append(entry)
        self.le_hint = ttk.Label(placement, text="", foreground="#555")
        self.le_hint.grid(row=1, column=0, columnspan=7, sticky="w", pady=(4, 0))
        row += 1

        out = ttk.LabelFrame(parent, text="Output", padding=pad)
        out.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        out.columnconfigure(1, weight=1)
        fmt = ttk.Frame(out)
        fmt.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(fmt, text="Format:").grid(row=0, column=0, sticky="w")
        for i, ext in enumerate(writer.EXTENSIONS):
            ttk.Radiobutton(fmt, text=ext, value=ext, variable=self.extension).grid(
                row=0, column=1 + i, sticky="w", padx=(10, 0)
            )
        ttk.Label(out, text="Output folder:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(out, textvariable=self.out_folder).grid(
            row=1, column=1, sticky="ew", padx=6, pady=(6, 0)
        )
        ttk.Button(out, text="Browse", command=self._browse_folder).grid(
            row=1, column=2, pady=(6, 0)
        )
        row += 1

        self.export_button = ttk.Button(parent, text="Export", command=self._export)
        self.export_button.grid(row=row, column=0, pady=(0, 8))
        row += 1

        self.status_label = ttk.Label(
            parent, textvariable=self.status, wraplength=self._px(520)
        )
        self.status_label.grid(row=row, column=0, sticky="w")

    def _build_solidworks(self, parent: tk.Misc) -> None:
        pad = self._px(8)
        panel = ttk.LabelFrame(parent, text="SolidWorks", padding=pad)
        panel.grid(row=0, column=1, sticky="ns", padx=(pad, 0))
        panel.rowconfigure(1, weight=1)

        self.sw_status_label = ttk.Label(
            panel, textvariable=self.sw_status, wraplength=self._px(280), foreground="#555"
        )
        self.sw_status_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self.curve_tree = ttk.Treeview(
            panel, columns=("state",), height=10, selectmode="browse"
        )
        self.curve_tree.heading("#0", text="Curve")
        self.curve_tree.heading("state", text="State")
        self.curve_tree.column("#0", width=self._px(170), stretch=False)
        self.curve_tree.column("state", width=self._px(90), stretch=False)
        self.curve_tree.grid(row=1, column=0, sticky="nsew")
        self.curve_tree.bind("<<TreeviewSelect>>", self._on_curve_selected)

        scroll = ttk.Scrollbar(panel, orient="vertical", command=self.curve_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.curve_tree.configure(yscrollcommand=scroll.set)

        self.curve_tree.tag_configure("linked", foreground=OK_COLOR)
        self.curve_tree.tag_configure("attention", foreground=ERROR_COLOR)
        self.curve_tree.tag_configure("untracked", foreground="#777")

        buttons = ttk.Frame(panel)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(buttons, text="New curve", command=self._new_curve).grid(row=0, column=0)
        ttk.Button(buttons, text="Refresh", command=self._refresh_panel).grid(
            row=0, column=1, padx=(6, 0)
        )

        ttk.Checkbutton(
            panel,
            text="Insert new curves into the open part",
            variable=self.insert_missing,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.editing_label = ttk.Label(
            panel, textvariable=self.editing_text, wraplength=self._px(280)
        )
        self.editing_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # ----------------------------------------------------------- interaction

    def _set_status(self, message: str, ok: bool = True) -> None:
        self.status.set(message)
        self.status_label.configure(foreground=OK_COLOR if ok else ERROR_COLOR)

    def _on_le_typed(self, event: tk.Event) -> None:
        """Stop auto-filling the leading edge once the user edits it themselves."""
        if event.keysym in NAVIGATION_KEYS:
            return
        self._le_manual = True
        self.le_hint.configure(text="")

    def _on_plane_mode_changed(self) -> None:
        mode = self.plane_mode.get()
        custom = mode in (MODE_3POINTS, MODE_2POINTS, MODE_NORMAL)
        on_main_plane = mode in geometry.MAIN_PLANES
        needed = 3 if mode == MODE_3POINTS else (2 if mode in (MODE_2POINTS, MODE_NORMAL) else 0)
        for pi, entries in enumerate(self.p_entries):
            state = "normal" if pi < needed else "disabled"
            for entry in entries:
                entry.configure(state=state)

        constraint_state = "readonly" if mode == MODE_2POINTS else "disabled"
        self.constraint_box.configure(state=constraint_state)
        self.main_plane_box.configure(state=constraint_state)

        # On main planes the axis pickers set 'up' outright, so the flip checkbox
        # would only be a confusing second way to say the same thing. Off them —
        # on a custom plane, or on a loaded curve's own plane — there are no axes
        # to pick, and flipping 'up' is the only choice left.
        axis_state = "readonly" if on_main_plane else "disabled"
        self.chord_axis_box.configure(state=axis_state)
        self.up_axis_box.configure(state=axis_state)
        self.flip_check.configure(state="disabled" if on_main_plane else "normal")
        if on_main_plane:
            self.flip.set(False)
            self._reset_axes(mode)

        self._le_manual = False
        self._sync_leading_edge()
        if mode == MODE_LOADED:
            hint = "Defaults to the curve's own leading edge — it exports back in place."
        elif custom:
            hint = "Defaults to P1 — the 2D origin lands here."
        else:
            hint = "Defaults to (0, 0, 0) — the 2D origin lands here."
        self.le_hint.configure(text=hint)

    def _reset_axes(self, main_plane: str) -> None:
        """Restore the plane's conventional chord/up axes and refresh both dropdowns."""
        chord, up = geometry.default_axes(main_plane)
        self._syncing_axes = True
        try:
            self.chord_axis_box.configure(values=geometry.chord_axis_options(main_plane))
            self.chord_axis.set(chord)
            self.up_axis_box.configure(values=geometry.up_axis_options(main_plane, chord))
            self.up_axis.set(up)
        finally:
            self._syncing_axes = False

    def _on_chord_axis_changed(self) -> None:
        """The chord claims one axis; 'up' must come from the other one."""
        mode = self.plane_mode.get()
        if self._syncing_axes or mode not in geometry.MAIN_PLANES:
            return
        options = geometry.up_axis_options(mode, self.chord_axis.get())
        self._syncing_axes = True
        try:
            self.up_axis_box.configure(values=options)
            if self.up_axis.get() not in options:
                self.up_axis.set(options[0])
        finally:
            self._syncing_axes = False

    def _sync_leading_edge(self) -> None:
        """Keep the leading edge at its default until the user edits it."""
        if self._le_manual or self._syncing:
            return
        mode = self.plane_mode.get()
        self._syncing = True
        try:
            if mode == MODE_LOADED and self.section is not None:
                values = [f"{c:g}" for c in self.section.origin]
            elif mode in (MODE_3POINTS, MODE_2POINTS, MODE_NORMAL):
                values = [var.get() for var in self.p_vars[0]]
            else:
                values = ["0", "0", "0"]
            for var, value in zip(self.le_vars, values):
                var.set(value)
        finally:
            self._syncing = False

    def _browse_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Select an airfoil CSV or a curve file",
            filetypes=[
                ("Airfoil CSV or curve", "*.csv *.sldcrv *.txt"),
                ("Airfoil CSV", "*.csv"),
                ("Curve files", "*.sldcrv *.txt"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.csv_path.set(path)
            self._load(path)

    def _browse_folder(self) -> None:
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.out_folder.set(path)

    def _load_curve(self, path: str) -> AirfoilData:
        """Read a curve file back into a 2D section on the plane it was drawn on."""
        section = geometry.flatten_curve(parser.parse_curve(path))
        self.section = section
        return AirfoilData(
            name=os.path.splitext(os.path.basename(path))[0],
            chord=section.chord,
            sections={AIRFOIL: section.points},
        )

    def _load(self, path: str) -> None:
        self.section = None
        try:
            curve = parser.is_curve_file(path)
            data = self._load_curve(path) if curve else parse_csv(path)
        except (AirfoilParseError, GeometryError) as exc:
            self.data = None
            self.loaded_text.set("No file loaded.")
            self.loaded_radio.configure(state="disabled")
            self._set_status(str(exc), ok=False)
            return

        self.data = data
        has_camber = bool(data.camber)
        self.camber_check.configure(state="normal" if has_camber else "disabled")
        if not has_camber:
            self.export_camber.set(False)

        summary = f"Loaded: {data.name}, chord {data.chord:g} mm, {len(data.airfoil)} pts"
        if curve:
            summary += " (curve file)"
        elif not has_camber:
            summary += " (no camber line)"
        self.loaded_text.set(summary)

        # A curve already stands somewhere, so keeping it there is the sane default.
        self.loaded_radio.configure(state="normal" if curve else "disabled")
        if curve:
            self.plane_mode.set(MODE_LOADED)
        elif self.plane_mode.get() == MODE_LOADED:
            self.plane_mode.set("XY")
        self._on_plane_mode_changed()

        if not self.out_folder.get():
            self.out_folder.set(os.path.dirname(os.path.abspath(path)))
        self._set_status(
            "Curve loaded on its own plane. Set an offset and export."
            if curve
            else "CSV loaded. Set the plane and export."
        )

    # ---------------------------------------------------------------- export

    def _spec(self) -> ExportSpec:
        """The form, as data. Every value goes across as the text that was typed."""
        values = {field: getattr(self, attr).get() for field, attr in SPEC_VARS}
        values["p1"] = tuple(v.get() for v in self.p_vars[0])
        values["p2"] = tuple(v.get() for v in self.p_vars[1])
        values["p3"] = tuple(v.get() for v in self.p_vars[2])
        values["leading_edge"] = tuple(v.get() for v in self.le_vars)
        values["le_manual"] = self._le_manual
        return ExportSpec(**values)

    # ------------------------------------------------------ the live link

    POLL_MS = 1000
    SLOW_EVERY = 5  # a full walk this many quiet ticks apart, to catch renames

    def _start_link(self) -> None:
        if not swcom.is_available():
            self.sw_status.set(
                "SolidWorks link off: pywin32 is not installed, so curves are "
                "written to files only."
            )
            return
        self._worker = swcom.Worker()
        self.after(200, self._tick)

    def _tick(self) -> None:
        """One turn of the poll. Never blocks, never raises into the loop."""
        try:
            self._collect_push()
            self._collect()
            if self._worker is not None and self._pending is None and self._pending_push is None:
                self._quiet_ticks += 1
                if self._quiet_ticks >= self.SLOW_EVERY:
                    self._quiet_ticks = 0
                    self._ask_for_snapshot()
                else:
                    self._ask_for_key()
        except Exception:  # noqa: BLE001 - a tick must never kill the timer
            pass
        self.after(self.POLL_MS, self._tick)

    def _ask(self, kind: str, work) -> None:
        if self._worker is None or self._pending is not None:
            return
        self._pending_kind = kind
        self._pending = self._worker.submit(work)

    def _ask_for_key(self) -> None:
        """The cheap question: is anything different enough to look properly?"""
        self._ask("key", _read_key)

    def _ask_for_snapshot(self) -> None:
        self._ask("snapshot", _read_snapshot)

    def _refresh_panel(self) -> None:
        """The Refresh button, and every path that needs the truth now.

        The tree is redrawn straight away as well as re-asked for: it shows the
        remembered curves too, and those change on export without anything in
        SolidWorks moving.
        """
        self._quiet_ticks = 0
        self._fill_tree()
        self._ask_for_snapshot()

    def _on_window_focused(self, _event: tk.Event) -> None:
        """Coming back from SolidWorks is when a rename is freshest."""
        self._refresh_panel()

    def _collect(self) -> None:
        if self._pending is None:
            return
        answer = self._pending.poll()
        if answer is None:
            return
        value, error = answer
        kind, self._pending, self._pending_kind = self._pending_kind, None, ""

        if error is not None:
            self._snapshot = None
            self.sw_status.set(self._link_message(error))
            self._fill_tree()
            return

        if kind == "key":
            if self._snapshot is None or value != self._snapshot.get("key"):
                self._ask_for_snapshot()
            return

        previous = self._snapshot
        self._snapshot = value
        if previous is None or previous != value:
            self._load_sidecar()
            self._describe_link()
            self._fill_tree()

    @staticmethod
    def _link_message(error: BaseException) -> str:
        if isinstance(error, swcom.NotRunning):
            return "SolidWorks is not running. Curves will be written to files only."
        if isinstance(error, swcom.WrongVersion):
            return str(error)
        if isinstance(error, swcom.NotAvailable):
            return "pywin32 is not installed, so curves are written to files only."
        return f"SolidWorks could not be reached: {error}"

    def _describe_link(self) -> None:
        snapshot = self._snapshot or {}
        label = snapshot.get("version", "SolidWorks")
        if snapshot.get("newer_than_tested"):
            label += " — newer than tested"
        title = snapshot.get("title")
        if not title:
            self.sw_status.set(f"{label}. No document open.")
        elif not snapshot.get("is_part"):
            self.sw_status.set(f"{label}. {title} is not a part, so it can hold no curves.")
        elif not snapshot.get("path"):
            self.sw_status.set(
                f"{label} — {title}. This part has never been saved, so its curve "
                "settings are only remembered until you close the app."
            )
        else:
            self.sw_status.set(f"{label} — {title}")

    # ------------------------------------------------------- remembered work

    def _load_sidecar(self) -> None:
        path = (self._snapshot or {}).get("path") or ""
        if not path:
            self._sidecar_path = ""
            if self._sidecar is None:
                self._sidecar = store.Sidecar()
            return
        try:
            self._sidecar_path = store.sidecar_path(path)
            self._sidecar = store.load(self._sidecar_path) or store.Sidecar(
                part_path=path, part_title=(self._snapshot or {}).get("title", "")
            )
        except store.StoreError as exc:
            self._sidecar = store.Sidecar(part_path=path)
            self._set_status(str(exc), ok=False)

    def _save_sidecar(self) -> None:
        if self._sidecar is None or not self._sidecar_path:
            return
        self._sidecar.written_by = f"airfoil-converter {__version__}"
        self._sidecar.part_path = (self._snapshot or {}).get("path", "")
        self._sidecar.part_title = (self._snapshot or {}).get("title", "")
        try:
            store.save(self._sidecar_path, self._sidecar)
        except OSError as exc:
            self._set_status(f"Could not write the curve record: {exc}", ok=False)

    def _states(self) -> List[store.CurveState]:
        if self._sidecar is None:
            return []
        features = (self._snapshot or {}).get("curves", [])
        return store.reconcile(self._sidecar, features, self.out_folder.get().strip())

    def _fill_tree(self) -> None:
        """Rewrite the list, keeping the selection where it was."""
        selected = self.curve_tree.selection()
        keep = selected[0] if selected else ""
        self.curve_tree.delete(*self.curve_tree.get_children())

        by_export: Dict[str, List[store.CurveState]] = {}
        strays: List[store.CurveState] = []
        for state in self._states():
            if state.export_id:
                by_export.setdefault(state.export_id, []).append(state)
            else:
                strays.append(state)

        for export_id, states in by_export.items():
            record = self._sidecar.find(export_id) if self._sidecar else None
            worst = "linked" if all(not s.needs_attention for s in states) else "attention"
            node = self.curve_tree.insert(
                "", "end", iid=export_id, text=_record_label(record, export_id), open=True,
                values=(_record_place(record),), tags=(worst,),
            )
            for position, state in enumerate(states):
                self.curve_tree.insert(
                    node, "end", iid=f"{export_id}/{position}/{state.feature}",
                    text=state.feature, values=(state.state,),
                    tags=("attention" if state.needs_attention else "linked",),
                )

        if strays:
            node = self.curve_tree.insert(
                "", "end", iid="__strays__", text="In the part, not tracked",
                open=True, values=("",), tags=("untracked",),
            )
            for position, state in enumerate(strays):
                self.curve_tree.insert(
                    node, "end", iid=f"__strays__/{position}/{state.feature}",
                    text=state.feature, values=(store.ORPHAN,), tags=("untracked",),
                )

        if keep and self.curve_tree.exists(keep):
            self.curve_tree.selection_set(keep)

    # --------------------------------------------------------- quick switch

    def _on_curve_selected(self, _event: tk.Event) -> None:
        if self._restoring:
            return
        selected = self.curve_tree.selection()
        if not selected:
            return
        export_id = selected[0].split("/")[0]
        if export_id == "__strays__" or self._sidecar is None:
            return
        record = self._sidecar.find(export_id)
        if record is None or record.export_id == self._editing:
            return
        if self._is_dirty() and not messagebox.askokcancel(
            "Unsaved changes",
            "The form has changes that have not been exported. Discard them and "
            f"open {record.stem}?",
            parent=self,
        ):
            self._select_editing()
            return
        self._apply_record(record)

    def _is_dirty(self) -> bool:
        if self._editing == "" or self._sidecar is None:
            return False
        record = self._sidecar.find(self._editing)
        if record is None:
            return False
        try:
            return self._spec() != record.spec
        except Exception:  # noqa: BLE001 - a half-typed field is not a reason to block
            return True

    def _apply_record(self, record: store.ExportRecord) -> None:
        """Put a remembered export back on the form.

        The order matters. Setting the plane mode resets the manual-leading-edge
        flag and the point fields re-sync it, so the leading edge and that flag
        have to come last or they are quietly overwritten.
        """
        self._restoring = True
        try:
            if record.source:
                if os.path.exists(record.source):
                    self.csv_path.set(record.source)
                    self._load(record.source)
                else:
                    self.csv_path.set(record.source)
                    self._set_status(
                        f"The source file has moved: {record.source}. Browse to it "
                        "again before exporting.",
                        ok=False,
                    )
            if record.loaded_section:
                self.section = geometry.FlatSection(
                    points=[tuple(p) for p in record.loaded_section["points"]],
                    origin=tuple(record.loaded_section["origin"]),
                    u=tuple(record.loaded_section["u"]),
                    v=tuple(record.loaded_section["v"]),
                    chord=record.loaded_section["chord"],
                )

            spec = record.spec
            for name, attr in SPEC_VARS:
                if name in ("plane_mode", "chord_axis", "up_axis"):
                    continue
                getattr(self, attr).set(getattr(spec, name))

            self.plane_mode.set(spec.plane_mode)
            self._on_plane_mode_changed()
            if spec.chord_axis:
                self.chord_axis.set(spec.chord_axis)
            if spec.up_axis:
                self.up_axis.set(spec.up_axis)

            for grid, values in zip(self.p_vars, (spec.p1, spec.p2, spec.p3)):
                for var, value in zip(grid, values):
                    var.set(value)
            for var, value in zip(self.le_vars, spec.leading_edge):
                var.set(value)
            self._le_manual = spec.le_manual

            if record.output_folder:
                self.out_folder.set(record.output_folder)
        finally:
            self._restoring = False

        self._editing = record.export_id
        self._describe_editing()
        self._select_editing()

    def _new_curve(self) -> None:
        """Keep every field, drop the identity: the way a second rib is made."""
        self._editing = ""
        self._describe_editing()
        for item in self.curve_tree.selection():
            self.curve_tree.selection_remove(item)

    def _select_editing(self) -> None:
        if self._editing and self.curve_tree.exists(self._editing):
            self.curve_tree.selection_set(self._editing)

    def _describe_editing(self) -> None:
        """Say which of the two things Export is about to do."""
        if not self._editing or self._sidecar is None:
            self.editing_text.set("Export will make a new curve.")
            return
        record = self._sidecar.find(self._editing)
        names = ", ".join(record.feature_names()) if record else self._editing
        self.editing_text.set(f"Export will update {names}.")

    def _export(self) -> None:
        try:
            self._export_unsafe()
        except (InputError, GeometryError, AirfoilParseError, ValueError) as exc:
            self._set_status(str(exc), ok=False)
        except swlink.LinkError as exc:
            self._set_status(str(exc), ok=False)
        except OSError as exc:
            self._set_status(f"Could not write the output: {exc}", ok=False)

    def _export_unsafe(self) -> None:
        if self._pending_push is not None:
            raise InputError("SolidWorks is still working on the last export.")
        if self.data is None:
            raise InputError("Load a CSV first.")

        folder = self.out_folder.get().strip()
        if not folder:
            raise InputError("Choose an output folder.")
        if not os.path.isdir(folder):
            raise InputError(f"Output folder does not exist: {folder}")

        record = self._sidecar.find(self._editing) if (self._sidecar and self._editing) else None
        stem = os.path.splitext(os.path.basename(self.csv_path.get()))[0]
        index = record.name_index if record else self._next_index(stem)

        spec = self._spec()
        curves = build_curves(self.data, spec, stem, self.section, index=index)

        if record is not None and not self._confirm_orphans(record, curves):
            return

        if not self._can_push():
            changed, hashes = swlink.write_files(curves, folder)
            self._remember(record, curves, spec, stem, folder, index, hashes, pushed=False)
            self._set_status(
                f"Wrote {len(curves)} file(s). " + self._offline_reason()
            )
            return

        self._begin_push(
            curves, folder,
            join_as=feature_name(stem, ROLE_JOINED, index),
            context=dict(record=record, curves=curves, spec=spec, stem=stem,
                         folder=folder, index=index),
        )

    # ------------------------------------------------------ pushing, without
    # blocking the window. Waiting on the worker from here would stop this
    # thread pumping messages, and a cross-apartment COM call that needs it
    # then never completes — which is a hang, not an error.

    def _begin_push(self, curves, folder: str, join_as: str, context: Dict[str, Any]) -> None:
        assert self._worker is not None
        insert = self.insert_missing.get()
        self._push_context = context
        self._pending_push = self._worker.submit(
            lambda session: swlink.push(
                session, curves, folder, insert_missing=insert, join_as=join_as
            )
        )
        self.export_button.configure(state="disabled")
        self._set_status("Sending to SolidWorks...")

    def _collect_push(self) -> None:
        if self._pending_push is None:
            return
        answer = self._pending_push.poll()
        if answer is None:
            return

        result, error = answer
        context = self._push_context or {}
        self._pending_push = None
        self._push_context = None
        self.export_button.configure(state="normal")

        if error is not None:
            self._set_status(f"SolidWorks could not be driven: {error}", ok=False)
            return

        self._remember(
            context["record"], context["curves"], context["spec"], context["stem"],
            context["folder"], context["index"], result.hashes,
            pushed=True, joined=result.joined or self._joined_name(context["record"]),
        )
        self._refresh_panel()

        message = f"{result.part}: {result.summary()}"
        if result.inserted:
            message += (
                f". Newly inserted: {', '.join(result.inserted)} — a new curve still "
                "has to be added to a loft by hand."
            )
        if result.joined_now:
            message += (
                f". The surface and its trailing edge are joined as {result.joined}; "
                "loft that rather than either half."
            )
        if result.failures:
            detail = "; ".join(f"{name}: {why}" for name, why in result.failures)
            self._set_status(f"{message}. Failed — {detail}", ok=False)
        else:
            self._set_status(message)

    def _next_index(self, stem: str) -> int:
        return self._sidecar.next_index(stem) if self._sidecar else 1

    def _can_push(self) -> bool:
        snapshot = self._snapshot or {}
        return bool(self._worker and snapshot.get("is_part"))

    def _offline_reason(self) -> str:
        if not swcom.is_available():
            return "pywin32 is not installed, so nothing was sent to SolidWorks."
        snapshot = self._snapshot
        if snapshot is None:
            return "SolidWorks was not reachable, so nothing was sent to it."
        if not snapshot.get("title"):
            return "No document is open in SolidWorks, so nothing was sent to it."
        if not snapshot.get("is_part"):
            return f"{snapshot['title']} is not a part, so nothing was sent to it."
        return "Nothing was sent to SolidWorks."

    def _confirm_orphans(self, record: "store.ExportRecord", curves) -> bool:
        """Warn before leaving a feature behind that a loft may still use."""
        # Only the curves an export writes can be orphaned by it. A joined
        # curve is derived from two of those, so no change of settings ever
        # stops producing it directly.
        orphans = swlink.orphaned([c.feature for c in record.written_curves()], curves)
        if not orphans:
            return True

        listed = ", ".join(orphans)
        also = ""
        derived = [c.feature for c in record.live_curves() if c.is_derived]
        if derived:
            also = f"\n\n{', '.join(derived)} is built on it and will go into error too."
        allowed = messagebox.askokcancel(
            "A curve would be left behind",
            f"These settings no longer produce {listed}.\n\n"
            "Anything built on it will stop updating. The app will leave it in the "
            f"part rather than delete it.{also}\n\nGo ahead?",
            parent=self,
        )
        if allowed:
            for curve_record in record.curves:
                if curve_record.feature in orphans:
                    curve_record.retired = True
        return allowed

    @staticmethod
    def _joined_name(record) -> str:
        if record is None:
            return ""
        for curve_record in record.curves:
            if curve_record.role == ROLE_JOINED:
                return curve_record.feature
        return ""

    def _remember(self, record, curves, spec, stem, folder, index, hashes,
                  pushed: bool, joined: str = "") -> None:
        if self._sidecar is None:
            return
        stamp = store.now()
        fresh = store.record_from_export(
            export_id=record.export_id if record else f"exp-{uuid.uuid4().hex[:8]}",
            curves=curves,
            spec=spec,
            stem=stem,
            source=self.csv_path.get(),
            folder=folder,
            index=index,
            hashes=hashes,
            loaded_section=self._section_as_dict(),
        )
        for curve_record in fresh.curves:
            curve_record.last_pushed = stamp if pushed else ""

        # The joined curve holds no points and has no file of its own: it is a
        # feature SolidWorks derives from two of ours. It is recorded so the
        # panel knows it is ours rather than a stray.
        if joined and joinable(curves):
            fresh.curves.append(
                store.CurveRecord(
                    role=ROLE_JOINED, feature=joined, file="",
                    last_written=stamp, last_pushed=stamp if pushed else "",
                )
            )
        if record is not None:
            fresh.created = record.created or stamp
            # Retired curves are carried forward: they are still in the part.
            fresh.curves.extend(c for c in record.curves if c.retired)
            self._sidecar.exports[self._sidecar.exports.index(record)] = fresh
        else:
            self._sidecar.exports.append(fresh)

        self._editing = fresh.export_id
        self._describe_editing()
        self._save_sidecar()
        self._fill_tree()
        self._select_editing()

    def _section_as_dict(self) -> Optional[Dict[str, Any]]:
        if self.section is None or self.plane_mode.get() != MODE_LOADED:
            return None
        return {
            "points": [list(p) for p in self.section.points],
            "origin": list(self.section.origin),
            "u": list(self.section.u),
            "v": list(self.section.v),
            "chord": self.section.chord,
        }


def _declare_dpi_aware() -> None:
    """Ask Windows for real pixels, before Tk opens a window.

    A process that does not say it understands high-DPI screens is handed a
    pretend 96-DPI one and has everything it draws — its own widgets, and the
    common dialogs it opens — bitmap-stretched up to the true resolution. That
    stretching is what makes the Browse dialog look coarse and blurry.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System DPI aware.
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Windows 8 and older.
        except (AttributeError, OSError):
            pass


def _scale_to_dpi(root: tk.Tk) -> float:
    """Grow Tk's fonts to the screen we just admitted to. Returns the scale factor."""
    dpi = root.winfo_fpixels("1i")
    if dpi <= 0:
        return 1.0
    root.tk.call("tk", "scaling", dpi / 72.0)

    factor = dpi / 96.0
    if factor <= 1.01:
        return 1.0
    # A font sized in points already follows the scaling set above; one sized in
    # pixels — Tk writes those as a negative size — has to be grown by hand.
    for name in tkfont.names(root):
        try:
            f = tkfont.nametofont(name, root)
        except tk.TclError:
            continue
        size = f.cget("size")
        if size < 0:
            f.configure(size=int(round(size * factor)))
    return factor


def main() -> None:
    _declare_dpi_aware()
    root = tk.Tk()
    root.title("Airfoil Converter")
    scale = _scale_to_dpi(root)
    root.minsize(int(560 * scale), int(200 * scale))
    ConverterApp(root, scale=scale)
    root.mainloop()


if __name__ == "__main__":
    main()
