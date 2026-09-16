"""The Wing tab: exported ribs, lofted along two edges, and offset whole.

Plain ttk widgets for now, laid out the way the app was before its redesign;
the tab gets its own design pass later. What it does is settled:

* pick the ribs a wing is lofted through, and its two edge curves — each a
  curve file, or a straight line from root to tip;
* say whether each end of the wing is open (the skin carries on, as at a
  mirrored centreline) or closed (a flat end face);
* export the edge curves for the wing itself, or offset the whole wing and
  export its own sections and edges.

It borrows the Airfoil tab's link to SolidWorks and its record of the part
rather than keeping a second of each: one part, one record, one worker.
"""

from __future__ import annotations

import copy
import os
import queue
import threading
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from . import store, swcom, swlink, theme, ui_text, wing, wing_build
from .export import (
    OFFSET_DIRECTIONS,
    OFFSET_INWARD,
    PROFILES_ALL,
    PROFILES_ENDS,
    THICKNESS_BLENDED,
    THICKNESS_SCALED,
    ROLE_SECTION_JOINED,
    InputError,
    WingSpec,
    folder_name,
    wing_base,
)
from .geometry import GeometryError

NEW_WING = "New wing"
EDGE_LINE = "line"
EDGE_FILE = "file"

CURVE_TYPES = [("Curve files", "*.sldcrv *.txt"), ("All files", "*.*")]


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
        profiles=form.get("profiles", PROFILES_ENDS),
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
    name = record.stem if record.name_index <= 1 else f"{record.stem} ({record.name_index})"
    if record.adopted:
        return f"{name}   (taken over; no settings)"
    x, y, z = record.spec.leading_edge
    return f"{name}   LE {x}, {y}, {z}"


def retire_missing(record, curves, joins) -> List[str]:
    """Mark the curves a new export no longer makes. Returns their names."""
    wanted = {c.feature for c in curves} | {name for _, name in joins}
    gone = []
    for curve in record.curves:
        if not curve.retired and curve.feature not in wanted:
            curve.retired = True
            gone.append(curve.feature)
    return gone


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


class WingTab(ttk.Frame):
    POLL_MS = 150

    def __init__(self, master: tk.Misc, host) -> None:
        super().__init__(master, padding=10)
        self.host = host

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
        self.profiles = tk.StringVar(value=PROFILES_ENDS)
        self.thickness = tk.StringVar(value=THICKNESS_BLENDED)
        self.ribs_text = tk.StringVar(value="")
        self.result_text = tk.StringVar(value="Check works the wing out without exporting it.")
        self.status = tk.StringVar(value="Export the ribs on the Airfoil tab, then pick them here.")

        self._editing = ""            # the wing id being edited, or ""
        self._rib_ids: List[str] = []
        self._rib_ticks: Dict[str, tk.BooleanVar] = {}
        self._wing_ids: Dict[str, str] = {}
        self._pending_job: Optional[_Job] = None
        self._job_kind = ""
        self._job_context: Optional[Dict[str, Any]] = None
        self._pending_push: Optional[Any] = None
        self._push_context: Optional[Dict[str, Any]] = None

        self._build()
        self.refresh()
        self.after(self.POLL_MS, self._tick)

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        # Two columns: the window is as wide as the Airfoil tab with its curve
        # list open, and one tall column would not fit a laptop screen.
        self.columnconfigure(0, weight=1, uniform="side")
        self.columnconfigure(1, weight=1, uniform="side")
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left.columnconfigure(0, weight=1)
        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right.columnconfigure(0, weight=1)
        pad = {"padx": 6, "pady": 3}
        wrap = 360

        box = ttk.LabelFrame(left, text="Wing", padding=8)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Record").grid(row=0, column=0, sticky="w", **pad)
        self.record_box = ttk.Combobox(box, textvariable=self.record_choice, state="readonly")
        self.record_box.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)
        self.record_box.bind("<<ComboboxSelected>>", lambda _e: self._on_record_chosen())
        buttons = ttk.Frame(box)
        buttons.grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Button(buttons, text="New wing", command=self.new_wing).pack(side="left", padx=(6, 4))
        ttk.Button(buttons, text="Make offset copy", command=self.offset_copy).pack(side="left")
        ttk.Label(box, text="Name").grid(row=2, column=0, sticky="w", **pad)
        self.name_entry = ttk.Entry(box, textvariable=self.wing_name)
        self.name_entry.grid(row=2, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Label(box, text="Output folder").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.folder).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(box, text="Browse...", command=self._browse_folder).grid(row=3, column=2, **pad)
        ttk.Label(box, text="Format").grid(row=4, column=0, sticky="w", **pad)
        formats = ttk.Frame(box)
        formats.grid(row=4, column=1, sticky="w")
        for ext in (".sldcrv", ".txt"):
            ttk.Radiobutton(formats, text=ext, value=ext, variable=self.extension).pack(
                side="left", padx=(6, 8)
            )

        ribs = ttk.LabelFrame(left, text="Ribs", padding=8)
        ribs.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        left.rowconfigure(1, weight=1)
        ribs.columnconfigure(0, weight=1)
        ribs.rowconfigure(1, weight=1)
        ttk.Label(
            ribs, wraplength=wrap, justify="left",
            text="Tick every rib the wing is lofted through — the root and the tip at "
                 "least. Export them on the Airfoil tab first; the wing reads their "
                 "settings from there.",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        # A tick box per rib, in a scrolling page: a list whose rows toggle on a
        # click gives no hint that it wants clicking, and a double-click there
        # selects a row and unselects it again.
        holder = tk.Frame(ribs, bg="white", highlightthickness=1, highlightbackground="#a0a0a0")
        holder.grid(row=1, column=0, sticky="nsew")
        self._rib_canvas = tk.Canvas(holder, bg="white", highlightthickness=0, height=150)
        self._rib_canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(ribs, orient="vertical", command=self._rib_canvas.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self._rib_canvas.configure(yscrollcommand=scroll.set)
        self._rib_frame = tk.Frame(self._rib_canvas, bg="white")
        self._rib_canvas.create_window((0, 0), window=self._rib_frame, anchor="nw")
        self._rib_frame.bind(
            "<Configure>",
            lambda _e: self._rib_canvas.configure(scrollregion=self._rib_canvas.bbox("all")),
        )
        under = ttk.Frame(ribs)
        under.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(under, text="Refresh list", command=self.refresh).pack(side="left")
        ttk.Button(under, text="All", width=5,
                   command=lambda: self._tick_all(True)).pack(side="left", padx=(6, 0))
        ttk.Button(under, text="None", width=5,
                   command=lambda: self._tick_all(False)).pack(side="left", padx=(4, 0))
        ttk.Label(under, textvariable=self.ribs_text).pack(side="left", padx=8)

        edges = ttk.LabelFrame(right, text="Edges", padding=8)
        edges.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        edges.columnconfigure(1, weight=1)
        for row, (label, mode, path) in enumerate(
            (("Leading edge", self.le_mode, self.le_path), ("Trailing edge", self.te_mode, self.te_path))
        ):
            ttk.Label(edges, text=label).grid(row=row * 2, column=0, sticky="w", **pad)
            choice = ttk.Frame(edges)
            choice.grid(row=row * 2, column=1, columnspan=2, sticky="w")
            ttk.Radiobutton(choice, text="Straight line", value=EDGE_LINE, variable=mode,
                            command=self._sync_edges).pack(side="left", padx=(6, 8))
            ttk.Radiobutton(choice, text="Curve file", value=EDGE_FILE, variable=mode,
                            command=self._sync_edges).pack(side="left")
            entry = ttk.Entry(edges, textvariable=path)
            entry.grid(row=row * 2 + 1, column=1, sticky="ew", **pad)
            button = ttk.Button(edges, text="Browse...",
                                command=lambda v=path, l=label: self._browse_curve(v, l))
            button.grid(row=row * 2 + 1, column=2, **pad)
            setattr(self, f"_{'le' if row == 0 else 'te'}_widgets", (entry, button))

        ends = ttk.LabelFrame(right, text="Ends", padding=8)
        ends.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        for row, (label, var) in enumerate((("Root", self.root_end), ("Tip", self.tip_end))):
            ttk.Label(ends, text=label).grid(row=row, column=0, sticky="w", **pad)
            ttk.Radiobutton(ends, text="Open", value=wing.OPEN, variable=var).grid(
                row=row, column=1, sticky="w", **pad)
            ttk.Radiobutton(ends, text="Closed", value=wing.CLOSED, variable=var).grid(
                row=row, column=2, sticky="w", **pad)
        ttk.Checkbutton(ends, text="The root is the end further from the origin",
                        variable=self.swap_ends).grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(
            ends, wraplength=wrap, justify="left",
            text="Open: the skin carries on past this end, as at a mirrored centreline. "
                 "Closed: the wing ends in a flat face, which an offset moves too.",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))

        offset = ttk.LabelFrame(right, text="Offset", padding=8)
        offset.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(offset, text="Distance").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(offset, textvariable=self.offset, width=10).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(offset, text="mm").grid(row=0, column=2, sticky="w")
        for column, direction in enumerate(OFFSET_DIRECTIONS, start=3):
            ttk.Radiobutton(offset, text=direction, value=direction,
                            variable=self.offset_dir).grid(row=0, column=column, sticky="w", **pad)
        ttk.Label(offset, text="Profiles").grid(row=1, column=0, sticky="w", **pad)
        choice = ttk.Frame(offset)
        choice.grid(row=1, column=1, columnspan=4, sticky="w")
        ttk.Radiobutton(choice, text="All sections", value=PROFILES_ALL,
                        variable=self.profiles).pack(side="left", padx=(6, 8))
        ttk.Radiobutton(choice, text="Root and tip only", value=PROFILES_ENDS,
                        variable=self.profiles).pack(side="left")
        ttk.Label(offset, text="Thickness").grid(row=2, column=0, sticky="w", **pad)
        rule = ttk.Frame(offset)
        rule.grid(row=2, column=1, columnspan=4, sticky="w")
        ttk.Radiobutton(rule, text="Blended root to tip", value=THICKNESS_BLENDED,
                        variable=self.thickness).pack(side="left", padx=(6, 8))
        ttk.Radiobutton(rule, text="Airfoil scaled to chord", value=THICKNESS_SCALED,
                        variable=self.thickness).pack(side="left")
        ttk.Label(
            offset, wraplength=wrap, justify="left",
            text="Blank: export the wing's own edges and surface guides, to loft it "
                 "through its ribs. A distance: export the offset wing — through all its "
                 "sections, or its root and tip held by surface guides. Thickness says "
                 "what shape the guides hold both to.",
        ).grid(row=3, column=0, columnspan=5, sticky="w", pady=(2, 0))

        result = ttk.LabelFrame(right, text="Result", padding=8)
        result.grid(row=3, column=0, sticky="nsew", pady=(0, 6))
        right.rowconfigure(3, weight=1)
        result.columnconfigure(0, weight=1)
        ttk.Label(result, textvariable=self.result_text, wraplength=wrap - 60,
                  justify="left").grid(row=0, column=0, sticky="nw")
        self.check_button = ttk.Button(result, text="Check", command=self.check)
        self.check_button.grid(row=0, column=1, sticky="ne", padx=(8, 0))

        footer = ttk.Frame(self)
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(footer, textvariable=self.status, wraplength=2 * wrap,
                                      justify="left")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.export_button = ttk.Button(footer, text="Export", command=self.export)
        self.export_button.grid(row=0, column=1, sticky="e")
        self._sync_edges()

    # ---------------------------------------------------------------- state

    def _sidecar(self) -> Optional[store.Sidecar]:
        return self.host._sidecar

    def _set_status(self, message: str, ok: bool = True) -> None:
        self.status.set(message)
        self.status_label.configure(foreground=theme.OK if ok else theme.ERROR)

    def _sync_edges(self) -> None:
        for mode, widgets in ((self.le_mode, self._le_widgets), (self.te_mode, self._te_widgets)):
            state = "normal" if mode.get() == EDGE_FILE else "disabled"
            for widget in widgets:
                widget.configure(state=state)

    @property
    def pushing(self) -> bool:
        return self._pending_push is not None

    def refresh(self) -> None:
        """Re-read the ribs and wings the part's record holds."""
        sidecar = self._sidecar()
        for child in self._rib_frame.winfo_children():
            child.destroy()
        self._rib_ids = []
        self._wing_ids = {}
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
                tk.Checkbutton(
                    self._rib_frame, text=rib_label(record), variable=var, anchor="w",
                    bg="white", activebackground="white", highlightthickness=0,
                ).pack(fill="x", anchor="w", padx=4)
            for record in sidecar.wings:
                self._wing_ids[wing_label(record)] = record.export_id
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

    def _show_choice(self) -> None:
        sidecar = self._sidecar()
        record = sidecar.find_wing(self._editing) if (sidecar and self._editing) else None
        self.record_choice.set(wing_label(record) if record else NEW_WING)
        # A wing keeps the name it was made with, as a rib does: renaming it
        # would leave its curves behind in the part.
        self.name_entry.configure(state="disabled" if record else "normal")

    def _chosen_ribs(self) -> List[str]:
        return [r for r in self._rib_ids if self._rib_ticks[r].get()]

    def _tick_all(self, on: bool) -> None:
        for rib_id in self._rib_ids:
            self._rib_ticks[rib_id].set(on)

    def _count_ticks(self) -> None:
        if self._sidecar() is None:
            self.ribs_text.set("No part's record is open.")
            return
        count = len(self._rib_ids)
        self.ribs_text.set(f"{len(self._chosen_ribs())} of {count} rib"
                           f"{'' if count == 1 else 's'} ticked")

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

    # -------------------------------------------------------------- the work

    def _start(self, kind: str, work, context: Dict[str, Any]) -> None:
        self._job_kind = kind
        self._job_context = context
        self._pending_job = _Job(work)
        self.export_button.configure(state="disabled")
        self.check_button.configure(state="disabled")
        self._set_status("Working the wing out...")

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

    def check(self) -> None:
        if self._pending_job is not None:
            return
        try:
            _, _, stem, index, spec, own, snapshot = self._prepare()
        except (InputError, GeometryError) as exc:
            self._set_status(str(exc), ok=False)
            return

        def work(say):
            return wing_build.build_wing(spec, snapshot, stem, index, progress=say,
                                         check=True, own_names=own)

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
            folder = self.folder.get().strip()
            if not folder:
                raise InputError("Choose an output folder.")
            if not os.path.isdir(folder):
                raise InputError(f"Output folder does not exist: {folder}")
        except (InputError, GeometryError) as exc:
            self._set_status(str(exc), ok=False)
            return

        def work(say):
            return wing_build.build_wing(spec, snapshot, stem, index, progress=say,
                                         check=bool(spec.offset_mm()), own_names=own)

        self._start("export", work, dict(
            record_id=record.export_id if record else "", stem=stem, index=index,
            spec=spec, folder=folder, sidecar_path=self.host._sidecar_path,
        ))

    def _tick(self) -> None:
        try:
            self._collect_job()
            self._collect_push()
        except Exception as exc:  # noqa: BLE001 - never kill the timer
            self._set_status(f"The Wing tab hit a problem — {type(exc).__name__}: {exc}", ok=False)
            self._pending_job = None
            self._pending_push = None
            self._idle()
        self.after(self.POLL_MS, self._tick)

    def _idle(self) -> None:
        self.export_button.configure(state="normal")
        self.check_button.configure(state="normal")

    def _collect_job(self) -> None:
        job = self._pending_job
        if job is None:
            return
        said = job.latest()
        if not job.done:
            if said:
                self.status.set(said)
            return
        self._pending_job = None
        kind, context = self._job_kind, self._job_context or {}
        if job.error is not None:
            self._idle()
            error = job.error
            if isinstance(error, (InputError, GeometryError, ValueError, OSError)):
                self._set_status(str(error), ok=False)
            else:
                self._set_status(f"The wing could not be worked out: "
                                 f"{type(error).__name__}: {error}", ok=False)
            return
        build: wing_build.WingBuild = job.result
        self.result_text.set("\n".join(wing_build.describe(build)))
        if kind == "check":
            self._idle()
            self._set_status("Checked. Nothing was exported.")
            return
        self._finish_export(build, context)

    def _finish_export(self, build: wing_build.WingBuild, context: Dict[str, Any]) -> None:
        sidecar = self._sidecar()
        if sidecar is None or self.host._sidecar_path != context["sidecar_path"]:
            self._idle()
            self._set_status("The part in SolidWorks changed while the wing was worked out. "
                             "Nothing was exported.", ok=False)
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
            wanted = {c.feature for c in build.curves} | {n for _, n in build.joins}
            leaving = [c.feature for c in record.live_curves() if c.feature not in wanted]
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
                retire_missing(record, build.curves, build.joins)

        folder = context["folder"]
        if not self.host._can_push():
            try:
                _, hashes = swlink.write_files(build.curves, folder)
            except OSError as exc:
                self._idle()
                self._set_status(f"Could not write the output: {exc}", ok=False)
                return
            self._remember(record, build, context, hashes, pushed=False, joined=[])
            self._idle()
            self._set_status(
                f"Wrote {len(build.curves)} file(s). " + self.host._offline_reason()
            )
            return

        groups = self._groups(record, build, context)
        insert = self.host.insert_missing.get()
        curves, joins = build.curves, build.joins

        def work(session):
            result = swlink.push(session, curves, folder, insert_missing=insert, joins=joins)
            try:
                result.arranged = swlink.arrange(session, groups, parent=wing_build.WING_FOLDER)
            except swcom.SolidWorksError as exc:
                result.failures.append(("folders", str(exc)))
            return result

        self._push_context = dict(context, record=record, build=build)
        self._pending_push = self.host._worker.submit(work)
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
            mine.extend(c.feature for c in record.curves if c.retired and c.feature not in mine)
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
        self._idle()
        if error is not None:
            self._set_status(f"SolidWorks could not be driven: {error}", ok=False)
            return
        build = context["build"]
        self._remember(context["record"], build, context, result.hashes,
                       pushed=True, joined=result.joined_all)
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
            self._set_status(f"{message} Failed — {detail}", ok=False)
        else:
            self._set_status(message)

    def _remember(self, record, build, context, hashes, pushed: bool, joined: List[str]) -> None:
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
            curve.last_pushed = stamp if pushed else ""
        made = set(joined)
        for _, name in build.joins:
            if name in made:
                fresh.curves.append(store.CurveRecord(
                    role=ROLE_SECTION_JOINED, feature=name, file="",
                    last_written=stamp, last_pushed=stamp,
                ))
        if record is not None:
            fresh.created = record.created or stamp
            known = {c.feature for c in fresh.curves}
            # Retired curves are still in the part, and joins made before this
            # push are still this wing's even if this push had none to make.
            fresh.curves.extend(
                c for c in record.curves
                if c.feature not in known and (c.retired or c.is_derived)
            )
            sidecar.wings[sidecar.wings.index(record)] = fresh
        else:
            sidecar.wings.append(fresh)
        self._editing = fresh.export_id
        self.host._save_sidecar()
        self.refresh()
