"""The Wing tab: exported ribs, lofted along two edges, and offset whole.

A page of the Airfoil tab's strip, built to ``design/canvas/Wing.dc.html``
from the same panels and controls (``design/UI-DESIGN.md`` §11 says where it
differs). What it does:

* pick the ribs a wing is lofted through, and its two edge curves — each a
  curve file, or a straight line from root to tip;
* say whether each end of the wing is open (the skin carries on, as at a
  mirrored centreline) or closed (a flat end face);
* export the edge curves for the wing itself, or offset the whole wing and
  export its own sections and edges;
* check a wing without exporting it, and follow an export phase by phase.

It borrows the Airfoil tab's link to SolidWorks and its record of the part
rather than keeping a second of each: one part, one record, one worker.
"""

from __future__ import annotations

import copy
import dataclasses
import os
import queue
import threading
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox
from typing import Any, Callable, Collection, Dict, List, Optional, Sequence

from . import store, swcom, swlink, swloft, theme, ui_text, widgets, wing, wing_build, writer
from .export import (
    OFFSET_DIRECTIONS,
    OFFSET_INWARD,
    PROFILES_ALL,
    PROFILES_ENDS,
    THICKNESS_BLENDED,
    THICKNESS_SCALED,
    ROLE_SECTION_JOINED,
    ROLE_WING_SURFACE,
    TE_LINE,
    WING_EDGE_ROLES,
    InputError,
    WingSpec,
    folder_name,
    wing_base,
)
from .geometry import GeometryError
from .theme import px

NEW_WING = "New wing"
EDGE_LINE = "line"
EDGE_FILE = "file"

CURVE_TYPES = [("Curve files", "*.sldcrv *.txt"), ("All files", "*.*")]

EDGE_LABELS = {EDGE_FILE: "Curve file", EDGE_LINE: "Straight line"}
END_LABELS = {wing.OPEN: "Open", wing.CLOSED: "Closed"}
PROFILE_LABELS = {PROFILES_ENDS: "Root and tip only", PROFILES_ALL: "All sections"}
THICKNESS_LABELS = {THICKNESS_BLENDED: "Blended root to tip",
                    THICKNESS_SCALED: "Airfoil scaled to chord"}

# The strip is the Airfoil tab's, and so is its width.
STRIP_WIDTH = 460


# -- the form as plain values, so it can be tested without a window ----------


def form_to_spec(form: Dict[str, Any], ribs: List[str]) -> WingSpec:
    """The Wing tab's fields, as a spec. ``form`` holds each field's value."""
    le = form["le_path"].strip() if form["le_mode"] == EDGE_FILE else ""
    te = form["te_path"].strip() if form["te_mode"] == EDGE_FILE else ""
    if form["le_mode"] == EDGE_FILE and not le:
        raise InputError("Choose the leading-edge curve file, or make it a straight line.")
    if form["te_mode"] == EDGE_FILE and not te:
        raise InputError("Choose the trailing-edge curve file, or make it a straight line.")
    return WingSpec(
        ribs=tuple(ribs),
        le_source=le,
        te_source=te,
        root_end=form["root_end"],
        tip_end=form["tip_end"],
        swap_ends=bool(form["swap_ends"]),
        offset=form["offset"].strip(),
        offset_dir=form["offset_dir"],
        extension=form["extension"],
        profiles=form.get("profiles", PROFILES_ALL),
        thickness=form.get("thickness", THICKNESS_BLENDED),
    )


def spec_to_form(spec: WingSpec) -> Dict[str, Any]:
    return {
        "le_mode": EDGE_FILE if spec.le_source else EDGE_LINE,
        "le_path": spec.le_source,
        "te_mode": EDGE_FILE if spec.te_source else EDGE_LINE,
        "te_path": spec.te_source,
        "root_end": spec.root_end,
        "tip_end": spec.tip_end,
        "swap_ends": spec.swap_ends,
        "offset": spec.offset,
        "offset_dir": spec.offset_dir,
        "extension": spec.extension,
        "profiles": spec.profiles,
        "thickness": spec.thickness,
    }


def offset_copy_name(stem: str, direction: str) -> str:
    return f"{stem}_{'inner' if direction == OFFSET_INWARD else 'outer'}"


def wing_label(record: store.WingRecord) -> str:
    base = record.stem if record.name_index <= 1 else f"{record.stem} ({record.name_index})"
    return f"{base} — {ui_text.wing_place(record.spec)}"


def rib_label(record: store.ExportRecord) -> str:
    """A rib on one line. The list shows the two halves in columns of their own."""
    name, place = ui_text.rib_columns(record)
    if record.adopted:
        return f"{name}   (taken over; no settings)"
    return f"{name}   LE {place}"


def left_behind(record, curves, joins) -> List[str]:
    """The curves a new export no longer makes.

    They stay in the part, and are retired on the record once the export
    lands — not before, so a push that fails leaves the record true.
    """
    wanted = {c.feature for c in curves} | {name for _, name in joins}
    return [c.feature for c in record.live_curves() if c.feature not in wanted]


class _Job:
    """Work on a thread of its own, polled from the window."""

    def __init__(self, work: Callable[[Callable[[str], None]], Any]):
        self.messages: "queue.Queue[str]" = queue.Queue()
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, args=(work,), daemon=True)
        self._thread.start()

    def _run(self, work) -> None:
        try:
            self.result = work(self.messages.put)
        except BaseException as exc:  # noqa: BLE001 - handed back to the window
            self.error = exc

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def latest(self) -> str:
        message = ""
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                return message


class _Phase:
    """One row of the export tracker, as it stands now."""

    def __init__(self, key: str, name: str, runs: Sequence[ui_text.Run]) -> None:
        self.key = key
        self.name = name
        self.state = "pending"
        self.runs = list(runs)
        self.error = False

    def row(self) -> ui_text.PhaseRow:
        return ui_text.PhaseRow(self.name, self.state, tuple(self.runs), self.error)


class WingTab(tk.Frame):
    POLL_MS = 150

    def __init__(self, master: tk.Misc, host) -> None:
        super().__init__(master, bg=theme.WINDOW)
        self.host = host
        self.fonts = theme.fonts()

        self.record_choice = tk.StringVar(value=NEW_WING)
        self.wing_name = tk.StringVar(value="wing")
        self.folder = tk.StringVar()
        self.extension = tk.StringVar(value=".sldcrv")
        self.le_mode = tk.StringVar(value=EDGE_FILE)
        self.le_path = tk.StringVar()
        self.te_mode = tk.StringVar(value=EDGE_FILE)
        self.te_path = tk.StringVar()
        self.root_end = tk.StringVar(value=wing.OPEN)
        self.tip_end = tk.StringVar(value=wing.CLOSED)
        self.swap_ends = tk.BooleanVar(value=False)
        self.offset = tk.StringVar()
        self.offset_dir = tk.StringVar(value=OFFSET_INWARD)
        self.profiles = tk.StringVar(value=PROFILES_ALL)
        self.thickness = tk.StringVar(value=THICKNESS_BLENDED)
        self.loft_after = tk.BooleanVar(value=False)
        self.ribs_text = tk.StringVar(value="")
        self.result_text = tk.StringVar(value="Check works the wing out without exporting it.")
        self.status = tk.StringVar(value="Export the ribs on the Airfoil tab, then pick them here.")

        # Derived text: each panel's state in one line, from ui_text.
        self.wing_readout = tk.StringVar(value=ui_text.wing_readout(""))
        self.edges_readout = tk.StringVar()
        self.ends_readout = tk.StringVar()
        self.root_hint = tk.StringVar()
        self.tip_hint = tk.StringVar()
        self.offset_readout = tk.StringVar()
        self.offset_hint = tk.StringVar()
        self.result_readout = tk.StringVar(value=ui_text.result_readout("idle"))

        self._editing = ""            # the wing id being edited, or ""
        self._rib_ids: List[str] = []
        self._rib_ticks: Dict[str, tk.BooleanVar] = {}
        self._wing_ids: Dict[str, str] = {}
        self._pending_job: Optional[_Job] = None
        self._job_kind = ""
        self._job_context: Optional[Dict[str, Any]] = None
        self._pending_push: Optional[Any] = None
        self._push_context: Optional[Dict[str, Any]] = None
        self._pending_loft: Optional[Any] = None
        self._after_export = ""       # what the export said, shown with the loft's result

        # The Result panel: the last wing worked out, and the tracker of the
        # work under way (or of the work that just stopped, with its reason).
        self._shown_build: Optional[wing_build.WingBuild] = None
        self._shown_name = ""
        self._shown_exported = False
        self._phases: List[_Phase] = []
        self._tracker_word = ""
        self._job_name = ""

        self._build()
        for var in (self.le_mode, self.te_mode, self.root_end, self.tip_end, self.offset,
                    self.offset_dir, self.profiles, self.wing_name):
            var.trace_add("write", lambda *_: self._refresh_text())
        self.refresh()
        self._show_result()
        self.after(self.POLL_MS, self._tick)

    # ---------------------------------------------------------------- layout
    #
    # Wing.dc.html, top to bottom: the Airfoil tab's panels and controls in
    # the same strip, in the order the work happens. The row helpers are the
    # Airfoil tab's own, so that a label column or a gap cannot drift apart.

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        row = 0
        for build in (
            self._build_wing, self._build_ribs, self._build_edges, self._build_ends,
            self._build_offset, self._build_output, self._build_sw_bar,
            self._build_result,
        ):
            build(self).grid(row=row, column=0, sticky="ew", pady=(0, px(8)))
            row += 1
        self.rowconfigure(row, weight=1)
        tk.Frame(self, bg=theme.WINDOW).grid(row=row, column=0, sticky="nsew")
        self._build_footer(self).grid(row=row + 1, column=0, sticky="ew")
        self._sync_edges()

    @staticmethod
    def _columns(body: tk.Misc) -> None:
        body.columnconfigure(0, minsize=px(118))
        body.columnconfigure(1, weight=1)

    def _wrapped_hint(self, parent: tk.Misc, width: int, **label) -> tk.Label:
        """A hint that wraps where the row runs out, rather than off the panel."""
        return tk.Label(parent, bg=theme.WHITE, fg=theme.MUTED, font=self.fonts.hint,
                        justify="left", anchor="w", wraplength=px(width), **label)

    # -- Wing

    def _build_wing(self, parent: tk.Misc) -> widgets.Panel:
        h = self.host
        panel = widgets.Panel(parent, "Wing", self.wing_readout)
        body = panel.body
        self._columns(body)

        h._row_label(body, 0, "Wing")
        holder, self.record_box = h._combo(body, self.record_choice, [NEW_WING], 120,
                                           mono=False)
        holder.grid(row=0, column=1, sticky="ew", pady=(0, px(6)))
        self.record_box.bind("<<ComboboxSelected>>", lambda _e: self._on_record_chosen())

        buttons = h._cell(body, 1, sticky="ew")
        buttons.columnconfigure(2, weight=1)
        self.new_button = widgets.Button(buttons, "New wing", command=self.new_wing,
                                         height=24)
        self.new_button.grid(row=0, column=0)
        self.copy_button = widgets.Button(buttons, "Make offset copy",
                                          command=self.offset_copy, height=24)
        self.copy_button.grid(row=0, column=1, padx=(px(6), 0))
        self.copy_hint = self._wrapped_hint(buttons, 110, text=ui_text.COPY_HINT)
        self.copy_hint.grid(row=0, column=2, sticky="w", padx=(px(8), 0))

        self.name_label = h._row_label(body, 2, "Name")
        name = h._cell(body, 2, sticky="ew", pady=0)
        name.columnconfigure(0, weight=1)
        self.name_entry = widgets.Field(name, self.wing_name, grow=True)
        self.name_entry.grid(row=0, column=0, sticky="ew")
        self.name_hint = h._hint(name, ui_text.NAME_KEPT_HINT)
        self.name_hint.grid(row=0, column=1, padx=(px(8), 0))
        self.name_hint.grid_remove()
        return panel

    # -- Ribs

    def _build_ribs(self, parent: tk.Misc) -> widgets.Panel:
        panel = widgets.Panel(parent, "Ribs", self.ribs_text)
        body = panel.body
        body.columnconfigure(0, weight=1)
        # A tick box per rib, drawn like the flyout's tree. A list whose rows
        # toggle on a click gives no hint that it wants clicking.
        self.rib_list = widgets.CheckList(body, "Rib", "Leading edge", empty=ui_text.NO_RIBS)
        self.rib_list.grid(row=0, column=0, sticky="ew")

        tools = tk.Frame(body, bg=theme.WHITE)
        tools.grid(row=1, column=0, sticky="ew", pady=(px(6), 0))
        tools.columnconfigure(3, weight=1)
        self.refresh_button = widgets.Button(tools, "Refresh", command=self.refresh,
                                             icon="refresh", height=24)
        self.refresh_button.grid(row=0, column=0)
        self.all_button = widgets.Button(tools, "All", command=lambda: self._tick_all(True),
                                         height=24)
        self.all_button.grid(row=0, column=1, padx=(px(6), 0))
        self.none_button = widgets.Button(tools, "None",
                                          command=lambda: self._tick_all(False), height=24)
        self.none_button.grid(row=0, column=2, padx=(px(6), 0))
        self.host._hint(tools, ui_text.RIBS_HINT).grid(row=0, column=3, sticky="e")
        return panel

    # -- Edges

    def _build_edges(self, parent: tk.Misc) -> widgets.Panel:
        h = self.host
        panel = widgets.Panel(parent, "Edges", self.edges_readout)
        body = panel.body
        self._columns(body)
        edges = (
            ("le", "Leading edge", self.le_mode, self.le_path),
            ("te", "Trailing edge", self.te_mode, self.te_path),
        )
        for k, (key, label, mode, path) in enumerate(edges):
            row = 2 * k
            h._row_label(body, row, label)
            top = h._cell(body, row)
            widgets.Segmented(top, mode, (EDGE_FILE, EDGE_LINE), labels=EDGE_LABELS,
                              width=170, command=self._sync_edges).grid(row=0, column=0)
            h._hint(top, ui_text.EDGE_HINTS[key]).grid(row=0, column=1, padx=(px(8), 0))

            last = k == len(edges) - 1
            under = h._cell(body, row + 1, sticky="ew", pady=0 if last else (0, px(8)))
            under.columnconfigure(0, weight=1)
            field = widgets.Field(under, path, grow=True)
            field.grid(row=0, column=0, sticky="ew")
            button = widgets.Button(under, "Browse", icon="folder",
                                    command=lambda v=path, l=label: self._browse_curve(v, l))
            button.grid(row=0, column=1, padx=(px(8), 0))
            setattr(self, f"_{key}_widgets", (field, button))
        return panel

    # -- Ends

    def _build_ends(self, parent: tk.Misc) -> widgets.Panel:
        h = self.host
        panel = widgets.Panel(parent, "Ends", self.ends_readout)
        body = panel.body
        self._columns(body)
        for row, (label, var, hint) in enumerate(
            (("Root", self.root_end, self.root_hint), ("Tip", self.tip_end, self.tip_hint))
        ):
            h._row_label(body, row, label)
            cell = h._cell(body, row, sticky="ew")
            widgets.Segmented(cell, var, wing.END_KINDS, labels=END_LABELS,
                              width=140).grid(row=0, column=0, sticky="nw")
            self._wrapped_hint(cell, STRIP_WIDTH - 24 - 118 - 140 - 8,
                               textvariable=hint).grid(row=0, column=1, sticky="w",
                                                       padx=(px(8), 0))
        widgets.Check(body, self.swap_ends,
                      "The root is the end further from the origin").grid(
            row=2, column=1, sticky="w"
        )
        return panel

    # -- Offset

    def _build_offset(self, parent: tk.Misc) -> widgets.Panel:
        h = self.host
        panel = widgets.Panel(parent, "Offset", self.offset_readout)
        body = panel.body
        self._columns(body)

        h._row_label(body, 0, "Distance")
        distance = h._cell(body, 0)
        widgets.Field(distance, self.offset, width=84, unit="mm",
                      placeholder="none").grid(row=0, column=0)
        widgets.Segmented(distance, self.offset_dir, OFFSET_DIRECTIONS,
                          width=140).grid(row=0, column=1, padx=(px(8), 0))

        # Profiles only says what an offset wing exports, so it is greyed out
        # while there is no offset — kept on screen, so it is not a surprise.
        self.profiles_label = h._row_label(body, 1, "Profiles")
        self.profiles_seg = widgets.Segmented(
            body, self.profiles, (PROFILES_ALL, PROFILES_ENDS), labels=PROFILE_LABELS,
        )
        self.profiles_seg.grid(row=1, column=1, sticky="ew", pady=(0, px(6)))

        h._row_label(body, 2, "Thickness")
        widgets.Segmented(
            body, self.thickness, (THICKNESS_BLENDED, THICKNESS_SCALED),
            labels=THICKNESS_LABELS,
        ).grid(row=2, column=1, sticky="ew", pady=(0, px(6)))

        self._wrapped_hint(body, STRIP_WIDTH - 24 - 118,
                           textvariable=self.offset_hint).grid(row=3, column=1, sticky="w")
        return panel

    # -- Output: the Airfoil tab's panel, on this tab's variables

    def _build_output(self, parent: tk.Misc) -> widgets.Panel:
        h = self.host
        panel = widgets.Panel(parent, "Output")
        panel.readout_label.configure(text="Curve Through XYZ Points")
        body = panel.body
        self._columns(body)

        h._row_label(body, 0, "Format")
        widgets.Segmented(body, self.extension, writer.EXTENSIONS, width=150,
                          mono=True).grid(row=0, column=1, sticky="w", pady=(0, px(6)))

        h._row_label(body, 1, "Folder")
        folder = h._cell(body, 1, sticky="ew", pady=0)
        folder.columnconfigure(0, weight=1)
        widgets.Field(folder, self.folder, grow=True).grid(row=0, column=0, sticky="ew")
        widgets.Button(folder, "Browse", command=self._browse_folder,
                       icon="folder").grid(row=0, column=1, padx=(px(8), 0))
        return panel

    # -- SolidWorks: the Airfoil tab's header, and what Export will do here

    def _build_sw_bar(self, parent: tk.Misc) -> widgets.Panel:
        f = self.fonts
        panel = widgets.Panel(parent, "SolidWorks")
        self.host._sw_header(panel)
        body = panel.body
        body.columnconfigure(1, weight=1)
        widgets.Icon(body, "info", theme.MUTED, 14).grid(row=0, column=0, sticky="nw",
                                                         pady=(px(2), 0))
        self.export_text = widgets.RichText(body, {
            "": (f.hint, theme.INK),
            ui_text.MONO: (f.mono_small, theme.INK),
            ui_text.STRONG: (f.hint_bold, theme.INK),
        }, on_resize=self.host._relayout)
        self.export_text.grid(row=0, column=1, sticky="ew", padx=(px(8), 0))
        widgets.Check(body, self.host.insert_missing,
                      "Insert new curves into the open part").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(px(7), 0)
        )
        self.loft_check = widgets.Check(body, self.loft_after,
                                        "Loft in SolidWorks after export")
        self.loft_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(px(6), 0))
        return panel

    # -- Result: Check and Loft, what a check found, and an export as it runs

    def _build_result(self, parent: tk.Misc) -> widgets.Panel:
        panel = widgets.Panel(parent, "Result", self.result_readout)
        slot = panel.header_slot()
        self.check_button = widgets.Button(slot, "Check", command=self.check, height=20,
                                           bg=theme.HEADER_BG)
        self.check_button.grid(row=0, column=0)
        self.loft_button = widgets.Button(slot, "Loft", command=self.loft, height=20,
                                          bg=theme.HEADER_BG)
        self.loft_button.grid(row=0, column=1, padx=(px(6), 0))

        body = panel.body
        body.columnconfigure(0, weight=1)
        self.result_idle = self._wrapped_hint(body, STRIP_WIDTH - 48, text=ui_text.RESULT_IDLE)
        self.result_idle.grid(row=0, column=0, sticky="w")
        self.result_block = self._build_checked(body)
        self.result_block.grid(row=1, column=0, sticky="ew")
        self.tracker_block = self._build_tracker(body)
        self.tracker_block.grid(row=2, column=0, sticky="ew")
        return panel

    def _block(self, parent: tk.Misc) -> "tuple":
        """The pick tracker's box, with its title row: word, name, and a note."""
        f = self.fonts
        block = widgets.Box(parent, bg=theme.TRACKER_BG, border=theme.TRACKER_BORDER)
        inner = tk.Frame(block, bg=theme.TRACKER_BG)
        inner.pack(fill="both", expand=True, padx=px(10), pady=px(8))
        inner.columnconfigure(0, weight=1)
        title = tk.Frame(inner, bg=theme.TRACKER_BG)
        title.grid(row=0, column=0, sticky="ew")
        labels = (
            tk.Label(title, bg=theme.TRACKER_BG, fg=theme.ACCENT, font=f.header),
            tk.Label(title, bg=theme.TRACKER_BG, fg=theme.ACCENT, font=f.mono_tiny_bold),
            tk.Label(title, bg=theme.TRACKER_BG, fg=theme.MUTED, font=f.tiny),
        )
        for column, label in enumerate(labels):
            label.grid(row=0, column=column, sticky="w",
                       padx=(px(8) if column == 2 else (px(4) if column else 0), 0))

        def set_title(word: str, name: str, note: str) -> None:
            labels[0].configure(text=word.upper() + (" ·" if name else ""))
            labels[1].configure(text=name)
            labels[2].configure(text=note)

        return block, inner, set_title

    def _build_checked(self, parent: tk.Misc) -> tk.Frame:
        f = self.fonts
        block, inner, self._set_result_title = self._block(parent)
        self.planform = widgets.Planform(inner)
        self.planform.grid(row=1, column=0, sticky="w", pady=(px(6), px(2)))
        self.result_rows = tk.Frame(inner, bg=theme.TRACKER_BG)
        self.result_rows.grid(row=2, column=0, sticky="ew")
        self.result_rows.columnconfigure(0, minsize=px(108))
        self.result_rows.columnconfigure(1, weight=1)
        self.wall_meter = widgets.WallMeter(self.result_rows)
        self.result_foot = widgets.RichText(inner, {
            "": (f.readout, theme.MUTED),
            ui_text.MONO: (f.mono_tiny, theme.INK),
            ui_text.STRONG: (f.header, theme.MUTED),
        }, bg=theme.TRACKER_BG, on_resize=self.host._relayout)
        self.result_foot.grid(row=3, column=0, sticky="ew", pady=(px(6), 0))
        return block

    def _build_tracker(self, parent: tk.Misc) -> tk.Frame:
        block, inner, self._set_tracker_title = self._block(parent)
        self.phase_rows = tk.Frame(inner, bg=theme.TRACKER_BG)
        self.phase_rows.grid(row=1, column=0, sticky="ew", pady=(px(6), 0))
        self.phase_rows.columnconfigure(1, minsize=px(88))
        self.phase_rows.columnconfigure(2, weight=1)
        # Said only when something is going to be lofted; a check lofts nothing.
        self.tracker_foot = tk.Label(inner, text=ui_text.EXPORT_FOOT, bg=theme.TRACKER_BG,
                                     fg=theme.MUTED, font=self.fonts.readout,
                                     justify="left", anchor="w",
                                     wraplength=px(STRIP_WIDTH - 48 - 22))
        self.tracker_foot.grid(row=2, column=0, sticky="w", pady=(px(6), 0))
        return block

    # -- the footer, exactly as the Airfoil tab's

    def _build_footer(self, parent: tk.Misc) -> tk.Frame:
        footer = tk.Frame(parent, bg=theme.WINDOW)
        footer.columnconfigure(1, weight=1)
        self.status_dot = widgets.Dot(footer, theme.OK, size=8, bg=theme.WINDOW)
        self.status_dot.grid(row=0, column=0, padx=(0, px(8)))
        self.status_label = tk.Label(
            footer, textvariable=self.status, bg=theme.WINDOW, fg=theme.OK,
            font=self.fonts.label, anchor="w", justify="left",
            wraplength=px(STRIP_WIDTH - 120),
        )
        self.status_label.grid(row=0, column=1, sticky="w")
        self.export_button = widgets.Button(footer, "Export", command=self.export,
                                            primary=True, height=32, bg=theme.WINDOW)
        self.export_button.grid(row=0, column=2, sticky="e", padx=(px(8), 0))
        return footer

    # ---------------------------------------------------------------- state

    def _sidecar(self) -> Optional[store.Sidecar]:
        return self.host._sidecar

    def _set_status(self, message: str, ok: bool = True) -> None:
        self.status.set(message)
        color = theme.OK if ok else theme.ERROR
        self.status_label.configure(foreground=color)
        self.status_dot.set_color(color)

    def _sync_edges(self) -> None:
        for mode, (field, button) in ((self.le_mode, self._le_widgets),
                                      (self.te_mode, self._te_widgets)):
            on = mode.get() == EDGE_FILE
            field.set_enabled(on)
            field.set_note("" if on else ui_text.EDGE_LINE_TEXT)
            button.configure(state="normal" if on else "disabled")

    def _refresh_text(self) -> None:
        """Every readout and hint on the tab, from the form as it stands."""
        self.edges_readout.set(ui_text.edges_readout(
            self.le_mode.get() == EDGE_FILE, self.te_mode.get() == EDGE_FILE
        ))
        self.ends_readout.set(ui_text.ends_readout(self.root_end.get(), self.tip_end.get()))
        self.root_hint.set(ui_text.END_HINTS.get(self.root_end.get(), ""))
        self.tip_hint.set(ui_text.END_HINTS.get(self.tip_end.get(), ""))
        offset, profiles = self.offset.get(), self.profiles.get()
        self.offset_readout.set(ui_text.offset_readout(offset, self.offset_dir.get()))
        self.offset_hint.set(ui_text.offset_hint(offset, profiles))
        has_offset = bool(offset.strip())
        self.profiles_seg.set_enabled(has_offset)
        self.profiles_label.configure(fg=theme.LABEL if has_offset else theme.FAINT)

        sidecar = self._sidecar()
        record = sidecar.find_wing(self._editing) if (sidecar and self._editing) else None
        self.wing_readout.set(ui_text.wing_readout(
            wing_base(record.stem, record.name_index) if record else ""
        ))
        stem = self.wing_name.get().strip()
        index = sidecar.next_index(stem) if (sidecar is not None and stem) else 1
        self.export_text.set_runs(ui_text.export_line(
            stem, index,
            editing=wing_base(record.stem, record.name_index) if record else "",
            live=len(record.live_curves()) if record else 0,
            te_line=self._te_line(),
            offset=has_offset,
            all_sections=profiles == PROFILES_ALL,
        ))
        self._show_readout()

    def _te_line(self) -> bool:
        """Do the ticked ribs end in a trailing-edge line? The wing's edge then splits."""
        sidecar = self._sidecar()
        for rib_id in self._chosen_ribs():
            record = sidecar.find(rib_id) if sidecar else None
            if record is not None and not record.adopted:
                try:
                    return record.spec.te_mode == TE_LINE
                except Exception:  # noqa: BLE001 - a record written by a later version
                    return False
        return False

    @property
    def pushing(self) -> bool:
        return self._pending_push is not None or self._pending_loft is not None

    @property
    def working(self) -> bool:
        return self._pending_job is not None or self.pushing

    def refresh(self) -> None:
        """Re-read the ribs and wings the part's record holds."""
        sidecar = self._sidecar()
        self._rib_ids = []
        self._wing_ids = {}
        rows = []
        if sidecar is not None:
            for record in sidecar.exports:
                self._rib_ids.append(record.export_id)
                var = self._rib_ticks.get(record.export_id)
                if var is None:
                    # A rib new to the list is ticked for a new wing, which is
                    # usually made of every rib in the part, and left alone for
                    # a wing already made of its own.
                    var = tk.BooleanVar(value=not self._editing and not record.adopted)
                    var.trace_add("write", lambda *_: self._count_ticks())
                    self._rib_ticks[record.export_id] = var
                name, place = ui_text.rib_columns(record)
                rows.append((name, place, None if record.adopted else var))
            for record in sidecar.wings:
                self._wing_ids[wing_label(record)] = record.export_id
        self.rib_list.set_rows(rows)
        self.record_box.configure(values=[NEW_WING] + list(self._wing_ids))
        if self._editing and (sidecar is None or sidecar.find_wing(self._editing) is None):
            self._editing = ""
        self._show_choice()
        self._count_ticks()
        if not self.folder.get() and sidecar is not None:
            for record in sidecar.exports:
                if record.output_folder:
                    self.folder.set(record.output_folder)
                    break
        self.host._relayout()

    def _show_choice(self) -> None:
        sidecar = self._sidecar()
        record = sidecar.find_wing(self._editing) if (sidecar and self._editing) else None
        self.record_choice.set(wing_label(record) if record else NEW_WING)
        # A wing keeps the name it was made with, as a rib does: renaming it
        # would leave its curves behind in the part.
        self.name_entry.set_enabled(record is None)
        if record:
            self.name_hint.grid()
            self.copy_hint.grid_remove()
        else:
            self.name_hint.grid_remove()
            self.copy_hint.grid()
        self._refresh_text()
        self._sync_buttons()

    def _chosen_ribs(self) -> List[str]:
        return [r for r in self._rib_ids if self._rib_ticks[r].get()]

    def _tick_all(self, on: bool) -> None:
        sidecar = self._sidecar()
        for rib_id in self._rib_ids:
            record = sidecar.find(rib_id) if sidecar else None
            # A rib taken over from the part has no settings to loft from.
            self._rib_ticks[rib_id].set(on and not (record is not None and record.adopted))

    def _count_ticks(self) -> None:
        sidecar = self._sidecar()
        # A rib taken over from the part cannot be ticked, so it is not counted.
        records = [sidecar.find(r) for r in self._rib_ids] if sidecar else []
        tickable = sum(1 for record in records if record is not None and not record.adopted)
        self.ribs_text.set(ui_text.ribs_readout(
            len(self._chosen_ribs()), tickable, sidecar is not None
        ))
        self._refresh_text()

    def _form(self) -> Dict[str, Any]:
        return {
            "le_mode": self.le_mode.get(), "le_path": self.le_path.get(),
            "te_mode": self.te_mode.get(), "te_path": self.te_path.get(),
            "root_end": self.root_end.get(), "tip_end": self.tip_end.get(),
            "swap_ends": self.swap_ends.get(), "offset": self.offset.get(),
            "offset_dir": self.offset_dir.get(), "extension": self.extension.get(),
            "profiles": self.profiles.get(),
            "thickness": self.thickness.get(),
        }

    def _spec(self) -> WingSpec:
        return form_to_spec(self._form(), self._chosen_ribs())

    def _apply(self, spec: WingSpec) -> None:
        form = spec_to_form(spec)
        for key, value in form.items():
            getattr(self, key).set(value)
        for rib_id in self._rib_ids:
            self._rib_ticks[rib_id].set(rib_id in spec.ribs)
        self._sync_edges()

    # -------------------------------------------------------------- records

    def open_wing(self, wing_id: str) -> None:
        """Load a remembered wing into the form."""
        sidecar = self._sidecar()
        record = sidecar.find_wing(wing_id) if sidecar else None
        if record is None:
            return
        self.refresh()
        self._editing = record.export_id
        self.wing_name.set(record.stem)
        if record.output_folder:
            self.folder.set(record.output_folder)
        self._apply(record.spec)
        self._forget_result()
        self._show_choice()
        self._set_status(f"Editing {wing_base(record.stem, record.name_index)}. "
                         "Export updates its curves in place.")

    def _on_record_chosen(self) -> None:
        choice = self.record_choice.get()
        if choice == NEW_WING:
            self.new_wing()
        elif choice in self._wing_ids:
            self.open_wing(self._wing_ids[choice])

    def new_wing(self) -> None:
        """Stop editing, keeping every field: the next export makes another wing."""
        self._editing = ""
        self._forget_result()
        self._show_choice()
        self._set_status("Export will make a new wing.")

    def offset_copy(self) -> None:
        """Start a new wing from this one, offset, beside it in the part."""
        sidecar = self._sidecar()
        record = sidecar.find_wing(self._editing) if (sidecar and self._editing) else None
        stem = record.stem if record else self.wing_name.get().strip() or "wing"
        if not self.offset.get().strip():
            self.offset.set("2")
        self._editing = ""
        self.wing_name.set(offset_copy_name(stem, self.offset_dir.get()))
        self._forget_result()
        self._show_choice()
        self._set_status(
            f"A new wing, {self.wing_name.get()}, offset from {stem}. Set the distance and export."
        )

    def _browse_folder(self) -> None:
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.folder.set(path)

    def _browse_curve(self, var: tk.StringVar, what: str) -> None:
        path = filedialog.askopenfilename(title=f"Select the {what.lower()} curve",
                                          filetypes=CURVE_TYPES)
        if path:
            var.set(path)

    # ------------------------------------------------------- the Result panel
    #
    # Three bodies, one shown at a time: the idle hint, the block for the
    # last wing worked out, and the tracker of an export. The tracker opens
    # in _start, gains a tick in each collector, and folds back in _idle —
    # unless a phase failed, when it stays up with the reason on its row.

    def _loft_ready(self) -> bool:
        """The test loft() makes: the wing being edited has curves in the part."""
        sidecar = self._sidecar()
        record = sidecar.find_wing(self._editing) if (sidecar and self._editing) else None
        return record is not None and any(c.last_pushed for c in record.live_curves())

    def _sync_buttons(self) -> None:
        busy = self.working
        state = "disabled" if busy else "normal"
        for button in (self.export_button, self.check_button, self.new_button,
                       self.copy_button, self.refresh_button, self.all_button,
                       self.none_button):
            button.configure(state=state)
        self.record_box.configure(state="disabled" if busy else "readonly")
        self.loft_button.configure(
            state="normal" if not busy and self._loft_ready() else "disabled"
        )

    def _forget_result(self) -> None:
        """The block describes one wing; another wing on the form makes it stale."""
        if self.working:
            return
        self._shown_build = None
        self._shown_exported = False
        self._phases = []
        self._show_result()

    def _open_tracker(self, word: str, phases: Sequence[_Phase]) -> None:
        self._tracker_word = word
        self._phases = list(phases)
        if self._phases:
            self._phases[0].state = "active"
        self._show_result()

    def _phase(self, key: str) -> Optional[_Phase]:
        for phase in self._phases:
            if phase.key == key:
                return phase
        return None

    def _active_phase(self) -> Optional[_Phase]:
        for phase in self._phases:
            if phase.state == "active":
                return phase
        return None

    def _activate(self, key: str, runs: Optional[Sequence[ui_text.Run]] = None) -> None:
        phase = self._phase(key)
        if phase is None:
            return
        for other in self._phases:
            if other.state == "active" and other is not phase:
                other.state = "done"
        phase.state = "active"
        if runs is not None:
            phase.runs = list(runs)
        self._show_result()

    def _tick_phase(self, key: str, runs: Sequence[ui_text.Run], ok: bool = True) -> None:
        """Mark a phase done with what it found, and the next one under way."""
        phase = self._phase(key)
        if phase is None:
            return
        phase.state = "done" if ok else "active"
        phase.runs = list(runs)
        phase.error = not ok
        if ok:
            later = self._phases[self._phases.index(phase) + 1:]
            for other in later:
                if other.state == "pending":
                    other.state = "active"
                    break
        else:
            self._tracker_word = "stopped"
        self._show_result()

    def _say(self, message: str) -> None:
        """A progress message: the status line, and the active phase's detail."""
        self.status.set(message)
        phase = self._active_phase()
        if phase is not None and not phase.error:
            phase.runs = [(message.rstrip("."), ui_text.PLAIN)]
            self._fill_phases()

    def _fail(self, message: str) -> None:
        """Put a failure on the row that was under way, and in the status line."""
        self._set_status(message, ok=False)
        phase = self._active_phase()
        if phase is not None:
            phase.runs = [(message, ui_text.PLAIN)]
            phase.error = True
            self._tracker_word = "stopped"
            self._show_result()

    def _failed(self) -> bool:
        return any(phase.error for phase in self._phases)

    def _show_result(self) -> None:
        """Show whichever body the Result panel is in, and its readout."""
        tracking = bool(self._phases) and (self.working or self._failed())
        if tracking:
            self.result_idle.grid_remove()
            self.result_block.grid_remove()
            self.tracker_block.grid()
            self._fill_phases()
        elif self._shown_build is not None:
            self.result_idle.grid_remove()
            self.tracker_block.grid_remove()
            self.result_block.grid()
            self._fill_checked()
        else:
            self.result_block.grid_remove()
            self.tracker_block.grid_remove()
            self.result_idle.grid()
        self._show_readout()
        self.host._relayout()

    def _show_readout(self) -> None:
        if self._phases and (self.working or self._failed()):
            self.result_readout.set(ui_text.result_readout(self._tracker_word))
            return
        if self._shown_build is None:
            self.result_readout.set(ui_text.result_readout("idle"))
            return
        linked = ""
        if self._editing:
            states = [s for s in self.host._last_states if s.export_id == self._editing]
            if states:
                linked, _ = ui_text.curves_linked(states)
        self.result_readout.set(ui_text.result_readout(
            "exported" if self._shown_exported else "checked", linked
        ))

    def _fill_checked(self) -> None:
        build = self._shown_build
        if build is None:
            return
        f = self.fonts
        offset_mm = build.offset.offset if build.offset is not None else 0.0
        self._set_result_title("checked" if not self._shown_exported else "exported",
                               self._shown_name,
                               ui_text.result_subtitle(offset_mm, self._shown_exported))
        try:
            self.planform.show(wing_build.planform(build))
        except Exception:  # noqa: BLE001 - a picture is not worth losing the numbers
            self.planform.show(None)

        for child in self.result_rows.winfo_children():
            if child is not self.wall_meter:
                child.destroy()
        self.wall_meter.grid_remove()
        row = 0
        for line in ui_text.result_rows(build):
            color = theme.ERROR if line.error else theme.INK
            if line.label:
                tk.Label(self.result_rows, text=line.label, bg=theme.TRACKER_BG,
                         fg=theme.ERROR if line.error else theme.MUTED,
                         font=f.hint, anchor="nw").grid(row=row, column=0, sticky="nw",
                                                        pady=(px(3) if line.big else 0, 0))
            value = widgets.RichText(self.result_rows, {
                "": (f.body_bold if line.big else f.hint, color),
                ui_text.MONO: (f.mono_small, color),
                ui_text.STRONG: (f.mono_bold, color),
                ui_text.QUIET: (f.hint, theme.MUTED),
            }, bg=theme.TRACKER_BG, on_resize=self.host._relayout)
            value.set_runs(line.runs)
            value.grid(row=row, column=1 if line.label else 0,
                       columnspan=1 if line.label else 2, sticky="ew",
                       pady=(px(2) if line.big else 0, px(2)))
            row += 1
            report = build.offset.report if build.offset is not None else None
            if line.big and report is not None:
                self.wall_meter.show(report.thinnest, report.thickest, build.offset.offset)
                self.wall_meter.grid(row=row, column=1, sticky="w", pady=(0, px(2)))
                row += 1

        loft_name = self._shown_name + swloft.LOFT_SUFFIX
        edges = [c.feature for c in build.curves
                 if c.role in WING_EDGE_ROLES and c.role != ROLE_WING_SURFACE]
        guides = sum(1 for c in build.curves if c.role == ROLE_WING_SURFACE)
        self.result_foot.set_runs(ui_text.loft_footer(
            build.section_names, edges, guides, loft_name,
        ))

    def _fill_phases(self) -> None:
        f = self.fonts
        rows = [phase.row() for phase in self._phases]
        self._set_tracker_title(self._tracker_word, self._job_name,
                                ui_text.phase_title(rows) if rows else "")
        if self._phase("loft") is not None:
            self.tracker_foot.grid()
        else:
            self.tracker_foot.grid_remove()
        for child in self.phase_rows.winfo_children():
            child.destroy()
        for position, step in enumerate(rows):
            pending = step.state == "pending"
            glyph = widgets.StepGlyph(self.phase_rows)
            glyph.set_state(step.state)
            glyph.grid(row=position, column=0, sticky="nw", pady=(px(4), px(4)))
            tk.Label(
                self.phase_rows, text=step.name, bg=theme.TRACKER_BG,
                fg=theme.FAINT if pending else theme.INK,
                font=f.label_bold if step.state == "active" else f.label, anchor="w",
            ).grid(row=position, column=1, sticky="nw", padx=(px(8), 0), pady=(px(3), 0))
            color = theme.ERROR if step.error else (theme.FAINT if pending else theme.LABEL)
            detail = widgets.RichText(self.phase_rows, {
                "": (f.hint, color),
                ui_text.MONO: (f.mono_small, color if pending or step.error else theme.INK),
                ui_text.STRONG: (f.hint_bold, color),
            }, bg=theme.TRACKER_BG, on_resize=self.host._relayout)
            detail.set_runs(step.runs)
            detail.grid(row=position, column=2, sticky="ew", padx=(px(8), 0),
                        pady=(px(4), px(2)))

    # -------------------------------------------------------------- the work

    def _start(self, kind: str, work, context: Dict[str, Any]) -> None:
        self._job_kind = kind
        self._job_context = context
        self._pending_job = _Job(work)
        following = self._phase("loft")
        if kind == "loft" and following is not None and following.state != "done" \
                and not self._failed():
            # A loft after an export carries on the export's tracker.
            self._activate("loft")
        elif kind == "loft":
            self._job_name = context.get("name", "")
            self._open_tracker("lofting", [
                _Phase("loft", ui_text.PHASE_LOFT, ui_text.loft_detail(self._job_name)),
            ])
        self._sync_buttons()
        self._show_result()
        self._set_status("Working the wing out..." if kind != "loft"
                         else "Reading the wing's loft off its record...")

    def _open_work_tracker(self, kind: str, name: str, loft_name: str) -> None:
        """The rows an export or a check will go through, all still to come."""
        self._job_name = name
        phases = [_Phase("work", ui_text.PHASE_WORK,
                         [("reading the ribs and standing the wing up", ui_text.PLAIN)])]
        if kind == "export":
            online = self.host._can_push()
            part = (self.host._snapshot or {}).get("title", "")
            phases.append(_Phase(
                "send", ui_text.PHASE_SEND if online else ui_text.PHASE_WRITE,
                ui_text.send_detail(0, part) if online
                else [("the curve files, to the output folder", ui_text.PLAIN)],
            ))
            if online and self.loft_after.get():
                phases.append(_Phase("loft", ui_text.PHASE_LOFT,
                                     ui_text.loft_detail(loft_name)))
        self._open_tracker("exporting" if kind == "export" else "checking", phases)

    def _prepare(self):
        """Everything an export or a check needs, read off the form now."""
        sidecar = self._sidecar()
        if sidecar is None:
            raise InputError("No part's record is open. Open the part in SolidWorks first.")
        record = sidecar.find_wing(self._editing) if self._editing else None
        stem = record.stem if record else self.wing_name.get().strip()
        if not stem:
            raise InputError("Give the wing a name.")
        index = record.name_index if record else sidecar.next_index(stem)
        spec = self._spec()
        own = record.feature_names() if record else []
        # The work runs on another thread; it gets a copy of the record, so an
        # export on the Airfoil tab meanwhile cannot change it underneath.
        snapshot = copy.deepcopy(sidecar)
        return sidecar, record, stem, index, spec, own, snapshot

    @staticmethod
    def _loft_sections(record) -> int:
        """How many profiles the wing's loft runs through, when it was exported
        with every section: an edit keeps that many so the loft follows it."""
        return record.station_count if record is not None and record.station_count > 2 else 0

    def check(self) -> None:
        if self._pending_job is not None:
            return
        try:
            _, record, stem, index, spec, own, snapshot = self._prepare()
        except (InputError, GeometryError) as exc:
            self._set_status(str(exc), ok=False)
            return
        sections = self._loft_sections(record)

        def work(say):
            return wing_build.build_wing(spec, snapshot, stem, index, progress=say,
                                         check=True, own_names=own, sections=sections)

        self._open_work_tracker("check", wing_base(stem, index), "")
        self._start("check", work, {})

    def export(self) -> None:
        if self._pending_job is not None:
            return
        try:
            if self.host.busy():
                raise InputError("SolidWorks is still working on the last export.")
            sidecar, record, stem, index, spec, own, snapshot = self._prepare()
            if not self.host._sidecar_path:
                raise InputError(
                    "This part has never been saved, so there is nowhere to keep the wing. "
                    "Save the part first."
                )
            if self.host._sidecar_held:
                # The record file is there but unread, and must not be written
                # over: there is nowhere to keep the wing, as above.
                raise InputError(f"There is nowhere to keep the wing. {self.host._sidecar_held}")
            folder = self.folder.get().strip()
            if not folder:
                raise InputError("Choose an output folder.")
            if not os.path.isdir(folder):
                raise InputError(f"Output folder does not exist: {folder}")
        except (InputError, GeometryError) as exc:
            self._set_status(str(exc), ok=False)
            return

        sections = self._loft_sections(record)

        def work(say):
            return wing_build.build_wing(spec, snapshot, stem, index, progress=say,
                                         check=bool(spec.offset_mm()), own_names=own,
                                         sections=sections)

        base = wing_base(stem, index)
        self._open_work_tracker("export", base, base + swloft.LOFT_SUFFIX)
        self._start("export", work, dict(
            record_id=record.export_id if record else "", stem=stem, index=index,
            spec=spec, folder=folder, sidecar_path=self.host._sidecar_path,
        ))

    def loft(self) -> None:
        """Loft the wing being edited in the open part, through the curves it exported."""
        if self._pending_job is not None or self.pushing:
            return
        try:
            if self.host.busy():
                raise InputError("SolidWorks is still working on the last export.")
            if not self.host._can_push():
                raise InputError("SolidWorks is not connected. " + self.host._offline_reason())
            sidecar = self._sidecar()
            record = sidecar.find_wing(self._editing) if (sidecar and self._editing) else None
            if record is None or not any(c.last_pushed for c in record.live_curves()):
                raise InputError("Export this wing to SolidWorks first; the loft is built "
                                 "from its curves.")
        except InputError as exc:
            self._set_status(str(exc), ok=False)
            return
        self._start_loft(record)

    def _start_loft(self, record) -> None:
        snapshot = copy.deepcopy(self._sidecar())
        wing_id = record.export_id

        def work(say):
            say("Reading the wing's loft off its record...")
            return swloft.plan_for_wing(snapshot.find_wing(wing_id), snapshot)

        self._start("loft", work, {
            "sidecar_path": self.host._sidecar_path,
            "name": wing_base(record.stem, record.name_index),
        })

    def _send_loft(self, plan: "swloft.LoftPlan", context: Dict[str, Any]) -> None:
        if self.host._sidecar_path != context["sidecar_path"] or not self.host._can_push():
            self._fail("The part in SolidWorks changed; nothing was lofted.")
            self._idle()
            return
        self._pending_loft = self.host._worker.submit(
            lambda session: swloft.loft_in_part(session, [plan])
        )
        self._activate("loft", ui_text.loft_detail(plan.name, len(plan.profiles),
                                                   len(plan.guides)))
        self._set_status(f"Lofting {plan.name} through {len(plan.profiles)} profiles and "
                         f"{len(plan.guides)} guides in SolidWorks...")

    def _collect_loft(self) -> None:
        if self._pending_loft is None:
            return
        answer = self._pending_loft.poll()
        if answer is None:
            return
        results, error = answer
        self._pending_loft = None
        if error is not None:
            self._fail(f"SolidWorks could not loft the wing: {error}")
            self._idle()
            return
        text = " ".join(r.describe() for r in results)
        ok = all(r.ok for r in results)
        self._tick_phase("loft", [(text, ui_text.PLAIN)], ok=ok)
        self._idle()
        self.host._refresh_panel()
        before = self._after_export
        self._after_export = ""
        self._set_status((before + " " if before else "") + text, ok=ok)

    def _tick(self) -> None:
        try:
            self._collect_job()
            self._collect_push()
            self._collect_loft()
        except Exception as exc:  # noqa: BLE001 - never kill the timer
            self._pending_job = None
            self._pending_push = None
            self._pending_loft = None
            self._fail(f"The Wing tab hit a problem — {type(exc).__name__}: {exc}")
            self._idle()
        self.after(self.POLL_MS, self._tick)

    def _idle(self) -> None:
        """Nothing is running: the buttons come back and the tracker folds away.

        It folds a moment later rather than now, because an export that lofts
        next calls this and then starts the loft in the same breath, and the
        tracker has a row for that still to come. A tracker with a failed row
        stays up until the next piece of work replaces it.
        """
        self._sync_buttons()
        self.after_idle(self._settle)

    def _settle(self) -> None:
        if self.working:
            return
        self._sync_buttons()
        if not self._failed():
            self._phases = []
        self._show_result()

    def _collect_job(self) -> None:
        job = self._pending_job
        if job is None:
            return
        said = job.latest()
        if not job.done:
            if said:
                self._say(said)
            return
        self._pending_job = None
        kind, context = self._job_kind, self._job_context or {}
        if job.error is not None:
            error = job.error
            if isinstance(error, (InputError, GeometryError, ValueError, OSError)):
                self._fail(str(error))
            else:
                self._fail(f"The wing could not be worked out: "
                           f"{type(error).__name__}: {error}")
            self._idle()
            return
        if kind == "loft":
            self._send_loft(job.result, context)
            return
        build: wing_build.WingBuild = job.result
        self.result_text.set("\n".join(wing_build.describe(build)))
        self._shown_build = build
        self._shown_name = self._job_name
        self._shown_exported = False
        self._tick_phase("work", ui_text.work_detail(build))
        if kind == "check":
            self._idle()
            self._set_status("Checked. Nothing was exported.")
            return
        self._finish_export(build, context)

    def _finish_export(self, build: wing_build.WingBuild, context: Dict[str, Any]) -> None:
        sidecar = self._sidecar()
        if sidecar is None or self.host._sidecar_path != context["sidecar_path"]:
            self._fail("The part in SolidWorks changed while the wing was worked out. "
                       "Nothing was exported.")
            self._idle()
            return
        record = sidecar.find_wing(context["record_id"]) if context["record_id"] else None
        if record is not None:
            if record.station_count and build.station_count and \
                    build.station_count != record.station_count:
                if not messagebox.askokcancel(
                    "The offset wing's profiles changed",
                    f"The wing's loft had {record.station_count} profiles and now has "
                    f"{build.station_count}. A loft picks its profiles one by one, so it "
                    "will need them picked again.\n\nGo ahead?",
                    parent=self,
                ):
                    self._idle()
                    self._set_status("Export cancelled.")
                    return
            leaving = left_behind(record, build.curves, build.joins)
            if leaving:
                shown = ", ".join(leaving[:6]) + (" ..." if len(leaving) > 6 else "")
                if not messagebox.askokcancel(
                    "Curves would be left behind",
                    f"This export no longer makes {len(leaving)} curve(s): {shown}\n\n"
                    "They stay in the part, unused, rather than being deleted. Go ahead?",
                    parent=self,
                ):
                    self._idle()
                    self._set_status("Export cancelled.")
                    return
            context = dict(context, retire=leaving)

        folder = context["folder"]
        if not self.host._can_push():
            try:
                _, hashes = swlink.write_files(build.curves, folder)
            except OSError as exc:
                self._fail(f"Could not write the output: {exc}")
                self._idle()
                return
            self._remember(record, build, context, hashes, pushed=False, joined=[])
            self._shown_exported = True
            self._tick_phase("send", ui_text.send_detail(len(build.curves), "",
                                                         pushed=False, folder=folder))
            self._idle()
            self._set_status(
                f"Wrote {len(build.curves)} file(s). " + self.host._offline_reason()
            )
            return

        groups = self._groups(record, build, context)
        insert = self.host.insert_missing.get()
        curves, joins = build.curves, build.joins
        # A file rewritten while SolidWorks was closed is unchanged by this
        # export, so the push has to be told the part has never read it.
        force = store.stale_in_part(record) if record is not None else ()

        def work(session):
            result = swlink.push(session, curves, folder, insert_missing=insert,
                                 force=force, joins=joins)
            try:
                result.arranged = swlink.arrange(session, groups, parent=wing_build.WING_FOLDER)
            except swcom.SolidWorksError as exc:
                result.failures.append(("folders", str(exc)))
            return result

        self._push_context = dict(context, record=record, build=build)
        self._pending_push = self.host._worker.submit(work)
        part = (self.host._snapshot or {}).get("title", "")
        self._activate("send", ui_text.send_detail(len(curves), part))
        self._set_status(f"Sending {len(curves)} curve(s) to SolidWorks...")

    def _groups(self, record, build, context) -> List[swlink.TreeGroup]:
        sidecar = self._sidecar()
        groups = []
        editing = record.export_id if record else ""
        for other in (sidecar.wings if sidecar else []):
            if other.export_id == editing or not other.curves:
                continue
            groups.append(swlink.TreeGroup(
                folder_name(other.stem, other.name_index),
                tuple(c.feature for c in other.curves),
            ))
        mine = [c.feature for c in build.curves] + [name for _, name in build.joins]
        if record is not None:
            # A retired curve is still in the part, and still this wing's — as
            # is one this export is about to retire.
            retiring = context.get("retire", ())
            mine.extend(c.feature for c in record.curves
                        if (c.retired or c.feature in retiring) and c.feature not in mine)
        groups.append(swlink.TreeGroup(folder_name(context["stem"], context["index"]), tuple(mine)))
        return groups

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
        if error is not None:
            self._fail(f"SolidWorks could not be driven: {error}")
            self._idle()
            return
        build = context["build"]
        self._remember(context["record"], build, context, result.hashes,
                       pushed=True, joined=result.joined_all,
                       moved_aside=[old for _, old in result.remade],
                       failed={name for name, _ in result.failures})
        self._shown_exported = True
        self.host._refresh_panel()

        sections = build.section_names
        guides = " and ".join(build.edge_names)
        if len(sections) == 2:
            lofted = f"Loft {sections[0]} and {sections[1]} with {guides} as guide curves."
        elif sections:
            lofted = (f"Loft {sections[0]} to {sections[-1]} ({len(sections)} sections, in "
                      f"order) with {guides} as guide curves.")
        else:
            lofted = f"Use {guides} as the loft's guide curves."
        message = f"{result.part}: {result.summary()}. {lofted}"
        if result.failures:
            detail = "; ".join(f"{name}: {why}" for name, why in result.failures[:5])
            self._tick_phase("send", [(f"Failed — {detail}", ui_text.PLAIN)], ok=False)
            self._idle()
            self._set_status(f"{message} Failed — {detail}", ok=False)
            return
        self._tick_phase("send", ui_text.send_detail(len(build.curves), result.part))
        self._idle()
        self._set_status(message)
        if self.loft_after.get():
            record = self._sidecar().find_wing(self._editing) if self._sidecar() else None
            if record is not None:
                self._after_export = f"{result.part}: {result.summary()}."
                self._start_loft(record)

    def _remember(self, record, build, context, hashes, pushed: bool, joined: List[str],
                  moved_aside: Sequence[str] = (), failed: Collection[str] = ()) -> None:
        """Replace the wing's record with what this export made of it.

        ``failed`` names the curves SolidWorks would not take, which keep the
        last push that worked. The curves this export no longer makes are in
        the context as ``retire``; they stay in the part and are marked so.
        """
        sidecar = self._sidecar()
        if sidecar is None:
            return
        stamp = store.now()
        fresh = store.wing_record_from_export(
            wing_id=record.export_id if record else f"wing-{uuid.uuid4().hex[:8]}",
            curves=build.curves,
            spec=context["spec"],
            stem=context["stem"],
            folder=context["folder"],
            index=context["index"],
            hashes=hashes,
            station_count=build.station_count,
        )
        for curve in fresh.curves:
            curve.last_pushed = store.push_stamp(curve.feature, stamp, pushed, failed, record)
        made = set(joined)
        for _, name in build.joins:
            if name in made:
                fresh.curves.append(store.CurveRecord(
                    role=ROLE_SECTION_JOINED, feature=name, file="",
                    last_written=stamp, last_pushed=stamp,
                ))
        # A join made again leaves the old one renamed, still under any loft
        # built on it; it stays the wing's, retired.
        for name in moved_aside:
            fresh.curves.append(store.CurveRecord(
                role=ROLE_SECTION_JOINED, feature=name, file="",
                last_written=stamp, last_pushed=stamp, retired=True,
            ))
        if record is not None:
            fresh.created = record.created or stamp
            known = {c.feature for c in fresh.curves}
            retiring = set(context.get("retire", ()))
            for c in record.curves:
                if c.feature in known:
                    continue
                if c.feature in retiring:
                    # Retired on a copy, not on the record: had the push
                    # failed, the record would still have to say it is live.
                    fresh.curves.append(dataclasses.replace(c, retired=True))
                elif c.retired or c.is_derived:
                    # Retired curves are still in the part, and joins made
                    # before this push are still this wing's even if this
                    # push had none to make.
                    fresh.curves.append(c)
            sidecar.wings[sidecar.wings.index(record)] = fresh
        else:
            sidecar.wings.append(fresh)
        self._editing = fresh.export_id
        self.host._save_sidecar()
        self.refresh()
