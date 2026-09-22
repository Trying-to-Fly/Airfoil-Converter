"""Single-window tkinter front end for the airfoil converter."""

from __future__ import annotations

import dataclasses
import os
import sys
import tkinter as tk
import uuid
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Any, Callable, Collection, Dict, List, Optional, Tuple

from . import geometry, pick, store, swcom, swlink, theme, ui_text, widgets, wing_tab, writer
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
    folder_name,
    joinable,
    load_source,
    section_from_dict,
    section_to_dict,
)
from .geometry import GeometryError
from .parser import AirfoilData, AirfoilParseError
from . import __version__

OK_COLOR = theme.OK
ERROR_COLOR = theme.ERROR

# The window is a portrait strip beside SolidWorks, with the curve list in a
# flyout docked to its right. Both numbers are §2 of design/UI-DESIGN.md, at
# 100 % scaling; everything drawn is multiplied by the screen's scale.
STRIP_WIDTH = 460
FLYOUT_WIDTH = 360

# The row standing for a curve that has not been exported yet. It is not an
# export id and never reaches the sidecar; it only ever names a tree row.
NEW_CURVE = "__new__"

# The four trailing-edge modes, said shortly enough to fit four segments across
# the strip. The values are still export.TE_MODES; only the labels are short.
TE_LABELS = {
    TE_MODES[0]: "Auto-close",
    TE_MODES[1]: "Leave open",
    TE_MODES[2]: "Split",
    TE_MODES[3]: "TE line",
}

PLANE_MODES = ("XY", "XZ", "YZ", MODE_3POINTS, MODE_2POINTS, MODE_NORMAL, MODE_LOADED)
PLANE_LABELS = {
    MODE_3POINTS: "3 points",
    MODE_2POINTS: "2 points",
    MODE_NORMAL: "Normal",
    MODE_LOADED: "Loaded",
}

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


def _record_label(record: "Optional[Any]", export_id: str) -> str:
    """Several ribs come off one aerofoil, so the stem alone does not tell them apart."""
    if record is None:
        return export_id
    return record.stem if record.name_index <= 1 else f"{record.stem} ({record.name_index})"


def _record_place(record: "Optional[Any]") -> str:
    """Where the leading edge sits, which is what distinguishes one rib from the next."""
    if record is None:
        return ""
    if isinstance(record, store.WingRecord):
        # A wing stands on its ribs; what tells one from the next is its offset.
        return ui_text.wing_place(record.spec)
    if record.adopted:
        # Read back from the names in the part, which say nothing about where
        # the curves were put. A plausible ``0, 0, 0`` here would be a lie.
        return "adopted"
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
        session.change_key() if document and document.is_part else (0, 0),
    )


def _read_snapshot(session) -> dict:
    """Everything the panel draws, as plain data. No COM object comes back."""
    document = session.active_document()
    curves: List[str] = []
    if document is not None and document.is_part:
        curves = [f.name for f in session.curve_features()]
    return {
        "key": _read_key(session),
        "version": session.label,
        "newer_than_tested": swcom.is_newer_than_tested(session.revision),
        "title": document.title if document else "",
        "path": document.path if document else "",
        "is_part": bool(document and document.is_part),
        "curves": curves,
    }


def _picked_summary(session) -> str:
    """What a finished pick actually read, in one sentence."""
    got = [
        name
        for name, value in (
            ("the plane", session.plane),
            ("the chord", session.line),
            ("the leading edge", session.point),
        )
        if value is not None
    ]
    if not got:
        return "Nothing was picked, so the form is unchanged."
    if len(got) > 1:
        got = [", ".join(got[:-1]) + " and " + got[-1]]
    return f"Read {got[0]} from SolidWorks."


def _read_selection(session):
    """What is selected now. Left selected: see Session.read_selection."""
    return session.read_selection()


class ConverterApp(ttk.Frame):
    def __init__(self, master: tk.Misc, scale: float = 1.0) -> None:
        super().__init__(master, padding=0)
        self.scale = scale
        self.fonts = theme.setup(master, scale)
        master.configure(background=theme.WINDOW)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

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
        self.pick_text = tk.StringVar(value="")

        # Derived text. Every one of these is a panel's state in one line, so
        # the strip can be read from its headers without going through the
        # fields; ui_text works them out, and this is only where they are put.
        self.loaded_name = tk.StringVar(value="No file loaded.")
        self.loaded_detail = tk.StringVar(value="")
        self.plane_readout = tk.StringVar(value="")
        self.placement_readout = tk.StringVar(value="")
        self.sw_headline = tk.StringVar(value="looking for SolidWorks")
        self.sw_detail = tk.StringVar(value="")
        self.pick_title = tk.StringVar(value="")
        self.tree_count = tk.StringVar(value="")
        self.curve_name = tk.StringVar()
        self.card_title = tk.StringVar(value="Export will make a new curve.")
        self.card_state = tk.StringVar(value="")
        self.card_settings = tk.StringVar(value="")

        # Has the name been typed in, or is it still following the source file?
        self._name_typed = False
        # Was the plane last set by a pick? Only the readout cares, but it is
        # the difference between "3 points" and "3 points · picked in SolidWorks".
        self._picked_plane = False
        # Was the leading edge filled by a pick rather than typed? Same idea.
        self._le_picked = False
        # "", "done" or "stopped": what the idle pick row has to say for itself.
        self._pick_outcome = ""
        self._flyout_open = True
        # Every tab's SolidWorks bar shows the one link: see _sw_header.
        self._sw_dots: List[widgets.Dot] = []
        self._curves_buttons: List[widgets.Button] = []
        self._sw_dot_color = theme.FAINT

        # The link to SolidWorks. None until the first look, and dropped again
        # whenever a call fails, so a reopened session is picked up on its own.
        self._worker: Optional[swcom.Worker] = None
        self._pending: Optional[Any] = None
        self._pending_kind = ""
        self._pending_push: Optional[Any] = None
        self._pending_arrange: Optional[Any] = None
        self._tick_failure = ""   # the last poll failure reported, so it is said once
        self._push_context: Optional[Dict[str, Any]] = None
        self._snapshot: Optional[dict] = None
        self._sidecar: Optional[store.Sidecar] = None
        self._sidecar_path = ""
        # Why the sidecar file must not be written this session, or "". Set
        # when the file is there but could not be read — written by a newer
        # version, say — because writing over it would destroy what it holds.
        self._sidecar_held = ""
        # Records made against a document that has no path yet, kept until
        # there is somewhere on disk to put them.
        self._homeless: List[store.ExportRecord] = []
        self._editing: str = ""          # the export id being edited, or ""
        # The Wing tab, which borrows this link and this record, and what
        # opens a wing on it when one is clicked in the list here.
        self._wing_tab: Optional[Any] = None
        self._show_wing: Optional[Callable[[str], None]] = None
        self._last_states: List[store.CurveState] = []
        self._restoring = False
        self._quiet_ticks = 0

        # Pick mode: None unless the user is clicking things in SolidWorks.
        self._pick: Optional[pick.Pick] = None
        self._pick_ticks = 0
        self._pick_watch = pick.SelectionWatch()

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
        for var in (self.up_axis, self.constraint, self.main_plane):
            var.trace_add("write", lambda *_: self._refresh_plane_readout())
        self.curve_name.trace_add("write", lambda *_: self._on_name_changed())
        self._on_plane_mode_changed()
        self._show_pick()
        self._refresh_link_labels()
        # The list starts with the curve about to be made in it, so that what
        # Export is aimed at is never a thing the user has to remember.
        self._fill_tree()

        self._start_link()
        master.bind("<FocusIn>", self._on_window_focused, add="+")

    # ---------------------------------------------------------------- layout
    #
    # The window is design/UI-DESIGN.md §6, top to bottom: six panels in a
    # 460 px strip, a footer pinned under them, and the curve list in a flyout
    # docked to the right. Every control comes from :mod:`widgets` and every
    # colour and size from :mod:`theme`, so nothing below picks a number.

    def _px(self, pixels: int) -> int:
        """A pixel count, grown to the screen's DPI. The same number as theme.px."""
        return theme.px(pixels)

    def _build(self) -> None:
        """A portrait strip, and the part's curves in a flyout beside it.

        The strip holds every field at once, because this is a form that is
        filled in once per rib and read while SolidWorks is on screen next to
        it — scrolling to find a field is worse than a tall window. The curve
        list is the one thing that grows without limit, so it is the one thing
        that moves out into a column of its own.
        """
        self.columnconfigure(0, weight=0, minsize=theme.px(STRIP_WIDTH + 24))
        self.columnconfigure(3, weight=1, minsize=theme.px(FLYOUT_WIDTH))
        self.rowconfigure(0, weight=1)

        # The strip is a page, not a scrolling list: nothing inside it scrolls
        # on its own. But it is about 1200 px tall with the pick tracker open,
        # and a 1080p screen is not, so the whole page scrolls when it does not
        # fit and the scrollbar is not there at all when it does.
        self._page = tk.Canvas(self, bg=theme.WINDOW, highlightthickness=0, bd=0,
                               width=theme.px(STRIP_WIDTH + 24))
        self._page.grid(row=0, column=0, sticky="nsew")
        self._page_bar = ttk.Scrollbar(self, orient="vertical",
                                       style="Af.Vertical.TScrollbar",
                                       command=self._page.yview)
        self._page.configure(yscrollcommand=self._page_bar.set)

        strip = tk.Frame(self._page, bg=theme.WINDOW)
        self._page_window = self._page.create_window(
            (0, 0), window=strip, anchor="nw", width=theme.px(STRIP_WIDTH + 24)
        )
        self._build_strip(strip)
        strip.bind("<Configure>", self._fit_page)
        self._page.bind("<Configure>", self._fit_page)
        self._page.bind_all("<MouseWheel>", self._on_wheel, add="+")
        # A canvas asks for a size of its own and knows nothing about what is
        # in it, so the window would open at Tk's default height. It opens at
        # the strip's height instead, or the screen's, whichever is less.
        self.after_idle(self._size_page)

        self._flyout_edge = tk.Frame(self, bg=theme.BORDER_SOFT,
                                     width=max(1, theme.px(1)))
        self._flyout_edge.grid(row=0, column=2, sticky="ns")
        self.flyout = tk.Frame(self, bg=theme.SECONDARY_BG)
        self.flyout.grid(row=0, column=3, sticky="nsew")
        self._build_solidworks(self.flyout)

    def _size_page(self) -> None:
        strip = self._page.nametowidget(self._page.itemcget(self._page_window, "window"))
        room = _work_area_height(self.winfo_toplevel()) - theme.px(40)
        self._page.configure(height=min(strip.winfo_reqheight(), room))

    def _relayout(self) -> None:
        """Say that the strip changed height, because nothing else will.

        The page fixes the strip's height to the canvas item's, so the strip is
        never resized by its own contents and its ``<Configure>`` never fires —
        which is exactly what happens when the pick tracker opens.
        """
        # A moment later, not now and not at the next idle: Tk works out what
        # a frame is asking for as it settles, and both of those read the
        # height the strip had before the tracker opened.
        if hasattr(self, "_page"):
            self.after(50, self._fit_page)

    def _fit_page(self, _event: Optional[tk.Event] = None) -> None:
        """Keep the page as tall as its content, or as tall as the window."""
        strip = self._page.nametowidget(self._page.itemcget(self._page_window, "window"))
        wanted = strip.winfo_reqheight()
        visible = self._page.winfo_height()
        self._page.itemconfigure(self._page_window, height=max(wanted, visible))
        self._page.configure(scrollregion=(0, 0, self._page.winfo_width(),
                                           max(wanted, visible)))
        if wanted > visible:
            self._page_bar.grid(row=0, column=1, sticky="ns")
        else:
            self._page_bar.grid_remove()
            self._page.yview_moveto(0)

    def _on_wheel(self, event: tk.Event) -> None:
        """The wheel scrolls the page, but only while there is a page to scroll."""
        if not self._page_bar.winfo_ismapped():
            return
        over = self.winfo_containing(event.x_root, event.y_root)
        while over is not None:
            if over is self.flyout:
                return  # the tree does its own scrolling
            over = getattr(over, "master", None)
        self._page.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _build_strip(self, strip: tk.Misc) -> None:
        strip.columnconfigure(0, weight=1)
        strip.rowconfigure(0, weight=1)
        body = tk.Frame(strip, bg=theme.WINDOW)
        body.grid(row=0, column=0, sticky="nsew", padx=theme.px(12), pady=theme.px(12))
        body.columnconfigure(0, weight=1, minsize=theme.px(STRIP_WIDTH))

        # The tabs share the strip and the flyout: each is a page gridded in
        # the same cell under the tab row, and only the open one is shown.
        self.tab_row = widgets.TabRow(body, self.show_tab)
        self.tab_row.grid(row=0, column=0, sticky="ew", pady=(0, theme.px(10)))
        body.rowconfigure(1, weight=1)
        self.page_holder = body
        self._pages: Dict[str, tk.Misc] = {}
        self._tab_shown: Dict[str, Callable[[], None]] = {}
        self._open_tab = ""

        page = tk.Frame(body, bg=theme.WINDOW)
        page.columnconfigure(0, weight=1)
        row = 0
        for build in (
            self._build_source, self._build_shape, self._build_plane,
            self._build_placement, self._build_output, self._build_sw_bar,
        ):
            build(page).grid(row=row, column=0, sticky="ew", pady=(0, theme.px(8)))
            row += 1

        # The footer sits at the bottom of the window however tall it is: this
        # empty row takes whatever height is going spare.
        page.rowconfigure(row, weight=1)
        tk.Frame(page, bg=theme.WINDOW).grid(row=row, column=0, sticky="nsew")
        self._build_footer(page).grid(row=row + 1, column=0, sticky="ew")
        self.add_page("Airfoil", page)
        self.show_tab("Airfoil")

    def add_page(self, name: str, page: tk.Misc,
                 shown: Optional[Callable[[], None]] = None) -> None:
        """Put another tab's page in the strip, under a tab of its own.

        ``page`` must be a child of :attr:`page_holder`; ``shown`` is called
        each time its tab is opened.
        """
        page.grid(row=1, column=0, sticky="nsew")
        page.grid_remove()
        self._pages[name] = page
        if shown is not None:
            self._tab_shown[name] = shown
        self.tab_row.add(name)
        self.tab_row.select(self._open_tab)

    def show_tab(self, name: str) -> None:
        if name not in self._pages:
            return
        changed = name != self._open_tab
        self._open_tab = name
        for other, page in self._pages.items():
            if other == name:
                page.grid()
            else:
                page.grid_remove()
        self.tab_row.select(name)
        if changed:
            self._page.yview_moveto(0)
        shown = self._tab_shown.get(name)
        if shown is not None:
            shown()
        self._relayout()

    # -- the pieces every panel is made of

    def _row_label(self, parent: tk.Misc, row: int, text: str) -> tk.Label:
        label = tk.Label(parent, text=text, bg=theme.WHITE, fg=theme.LABEL,
                         font=self.fonts.label, anchor="w")
        label.grid(row=row, column=0, sticky="w", pady=(0, theme.px(6)))
        return label

    def _cell(self, parent: tk.Misc, row: int, column: int = 1, **grid) -> tk.Frame:
        """A white strip of a panel body to line controls up in."""
        frame = tk.Frame(parent, bg=theme.WHITE)
        grid.setdefault("sticky", "w")
        grid.setdefault("pady", (0, theme.px(6)))
        frame.grid(row=row, column=column, **grid)
        return frame

    def _hint(self, parent: tk.Misc, text: str) -> tk.Label:
        return tk.Label(parent, text=text, bg=theme.WHITE, fg=theme.MUTED,
                        font=self.fonts.hint)

    def _combo(self, parent: tk.Misc, variable: tk.StringVar, values, width: int,
               mono: bool = True):
        """A dropdown of an exact width. ttk measures in characters; §5 does not."""
        holder = tk.Frame(parent, bg=theme.WHITE, width=theme.px(width),
                          height=theme.px(26))
        # The box inside is packed, and pack has a propagation flag of its own.
        holder.grid_propagate(False)
        holder.pack_propagate(False)
        box = ttk.Combobox(
            holder, textvariable=variable, values=values, state="readonly",
            style="Mono.TCombobox" if mono else "Af.TCombobox",
        )
        box.pack(fill="both", expand=True)
        return holder, box

    # -- Source

    def _build_source(self, parent: tk.Misc) -> widgets.Panel:
        panel = widgets.Panel(parent, "Source")
        panel.readout_label.configure(text="CSV or curve file")
        body = panel.body
        body.columnconfigure(0, weight=1)

        top = tk.Frame(body, bg=theme.WHITE)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        widgets.Field(top, self.csv_path, grow=True).grid(row=0, column=0, sticky="ew")
        widgets.Button(top, "Browse", command=self._browse_csv, icon="folder").grid(
            row=0, column=1, padx=(theme.px(8), 0)
        )

        loaded = tk.Frame(body, bg=theme.WHITE)
        loaded.grid(row=1, column=0, sticky="w", pady=(theme.px(7), 0))
        self.outline = widgets.Outline(loaded)
        self.outline.grid(row=0, column=0, padx=(0, theme.px(8)))
        self.outline.grid_remove()
        self.loaded_name_label = tk.Label(
            loaded, textvariable=self.loaded_name, bg=theme.WHITE,
            fg=theme.FAINT, font=self.fonts.hint_bold,
        )
        self.loaded_name_label.grid(row=0, column=1, sticky="w")
        tk.Label(loaded, textvariable=self.loaded_detail, bg=theme.WHITE,
                 fg=theme.LABEL, font=self.fonts.hint).grid(
            row=0, column=2, sticky="w", padx=(theme.px(4), 0)
        )
        return panel

    # -- Shape

    def _build_shape(self, parent: tk.Misc) -> widgets.Panel:
        panel = widgets.Panel(parent, "Shape")
        panel.readout_label.configure(text="mm of the finished part")
        body = panel.body
        body.columnconfigure(0, minsize=theme.px(118))
        body.columnconfigure(1, weight=1)

        self._row_label(body, 0, "Curves")
        curves = self._cell(body, 0)
        widgets.Check(curves, self.export_airfoil, "Airfoil surface").grid(row=0, column=0)
        self.camber_check = widgets.Check(curves, self.export_camber, "Camber line")
        self.camber_check.grid(row=0, column=1, padx=(theme.px(12), 0))

        self._row_label(body, 1, "Trailing edge")
        widgets.Segmented(body, self.te_mode, TE_MODES, labels=TE_LABELS).grid(
            row=1, column=1, sticky="ew", pady=(0, theme.px(6))
        )

        self._row_label(body, 2, "TE thickness")
        te = self._cell(body, 2)
        widgets.Field(te, self.te_thickness, width=84, unit="mm").grid(row=0, column=0)
        widgets.Check(te, self.keep_chord, "Keep chord after the cut").grid(
            row=0, column=1, padx=(theme.px(8), 0)
        )

        self._row_label(body, 3, "Target chord")
        chord = self._cell(body, 3)
        self.target_field = widgets.Field(
            chord, self.target_chord, width=84, unit="mm", placeholder="",
        )
        self.target_field.grid(row=0, column=0)
        self._hint(chord, "blank keeps the source chord").grid(
            row=0, column=1, padx=(theme.px(8), 0)
        )

        self._row_label(body, 4, "Offset")
        offset = self._cell(body, 4, pady=0)
        widgets.Field(offset, self.offset, width=84, unit="mm", placeholder="none").grid(
            row=0, column=0
        )
        widgets.Segmented(
            offset, self.offset_dir, OFFSET_DIRECTIONS, width=140,
        ).grid(row=0, column=1, padx=(theme.px(8), 0))
        return panel

    # -- Plane

    def _build_plane(self, parent: tk.Misc) -> widgets.Panel:
        panel = widgets.Panel(parent, "Plane", self.plane_readout)
        body = panel.body
        body.columnconfigure(0, minsize=theme.px(118))
        body.columnconfigure(1, weight=1)

        self.plane_seg = widgets.Segmented(
            body, self.plane_mode, PLANE_MODES, labels=PLANE_LABELS,
            command=self._on_plane_mode_changed, height=24,
        )
        self.plane_seg.grid(row=0, column=0, columnspan=2, sticky="ew",
                            pady=(0, theme.px(6)))
        self.plane_seg.set_option_enabled(MODE_LOADED, False)

        # The X / Y / Z header and the three point rows share one column
        # geometry, so the header stands over the fields it names.
        header = tk.Frame(body, bg=theme.WHITE)
        header.grid(row=1, column=1, sticky="w")
        points = tk.Frame(body, bg=theme.WHITE)
        points.grid(row=2, column=1, sticky="w", pady=(0, theme.px(6)))
        for frame in (header, points):
            frame.columnconfigure(0, minsize=theme.px(22))
            for column in range(1, 4):
                frame.columnconfigure(column, minsize=theme.px(82 + 8))
        for column, axis in enumerate("XYZ"):
            tk.Label(header, text=axis, bg=theme.WHITE, fg=theme.READOUT,
                     font=self.fonts.tiny).grid(row=0, column=1 + column,
                                                padx=(theme.px(8), 0))
        self._row_label(body, 2, "Points")

        for pi, name in enumerate(("P1", "P2", "P3")):
            tk.Label(points, text=name, bg=theme.WHITE, fg=theme.LABEL,
                     font=self.fonts.mono_small).grid(row=pi, column=0, sticky="w")
            row_fields = []
            for ci in range(3):
                field = widgets.Field(points, self.p_vars[pi][ci], width=82)
                field.grid(row=pi, column=1 + ci, padx=(theme.px(8), 0),
                           pady=(0, theme.px(4)))
                row_fields.append(field)
            self.p_entries.append(row_fields)

        self.constraint_label = self._row_label(body, 3, "Constraint")
        constraint = self._cell(body, 3)
        holder, self.constraint_box = self._combo(
            constraint, self.constraint, (geometry.PERPENDICULAR, geometry.PARALLEL),
            128, mono=False,
        )
        holder.grid(row=0, column=0)
        tk.Label(constraint, text="to", bg=theme.WHITE, fg=theme.LABEL,
                 font=self.fonts.label).grid(row=0, column=1, padx=theme.px(8))
        holder, self.main_plane_box = self._combo(
            constraint, self.main_plane, geometry.MAIN_PLANES, 62,
        )
        holder.grid(row=0, column=2)

        widgets.Rule(body, theme.TRACK).grid(row=4, column=0, columnspan=2,
                                             sticky="ew", pady=(theme.px(2), theme.px(8)))

        self.chord_label = self._row_label(body, 5, "Chord runs along")
        axes = self._cell(body, 5)
        holder, self.chord_axis_box = self._combo(
            axes, self.chord_axis, geometry.chord_axis_options("XY"), 64,
        )
        holder.grid(row=0, column=0)
        # Off a main plane the chord is the line P1 → P2 and no dropdown can
        # say so; this stands in for the one that would lie about it.
        self.chord_from_points = widgets.Field(
            axes, tk.StringVar(value="P1 → P2"), width=92,
        )
        self.chord_from_points.set_enabled(False)
        self.up_label = tk.Label(axes, text="Up", bg=theme.WHITE, fg=theme.LABEL,
                                 font=self.fonts.label)
        self.up_label.grid(row=0, column=2, padx=(theme.px(8), theme.px(8)))
        holder, self.up_axis_box = self._combo(
            axes, self.up_axis, geometry.up_axis_options("XY", "+X"), 64,
        )
        holder.grid(row=0, column=3)
        self.flip_check = widgets.Check(axes, self.flip, "Flip up")
        self.flip_check.grid(row=0, column=4, padx=(theme.px(10), 0))

        self._row_label(body, 6, "Rotate in plane")
        widgets.Segmented(body, self.rotation, ROTATIONS, width=168, mono=True).grid(
            row=6, column=1, sticky="w", pady=(0, theme.px(6))
        )

        self._row_label(body, 7, "Angle of attack")
        pitch = self._cell(body, 7, pady=0)
        widgets.Field(pitch, self.pitch, width=84, unit="°").grid(row=0, column=0)
        self._hint(pitch, "+ pitches the nose up").grid(row=0, column=1,
                                                        padx=(theme.px(8), 0))

        widgets.Rule(body, theme.TRACK).grid(row=8, column=0, columnspan=2,
                                             sticky="ew", pady=theme.px(8))
        self._build_tracker(body).grid(row=9, column=0, columnspan=2, sticky="ew")
        return panel

    # -- the pick tracker (§7)

    def _build_tracker(self, parent: tk.Misc) -> tk.Frame:
        """Idle it is one button and a line; picking it is a row per step.

        Both live here and are shown one at a time, because the whole point of
        the block is that a click that landed looks different from one that
        did nothing.
        """
        area = tk.Frame(parent, bg=theme.WHITE)
        area.columnconfigure(0, weight=1)

        idle = tk.Frame(area, bg=theme.WHITE)
        idle.grid(row=0, column=0, sticky="ew")
        idle.columnconfigure(1, weight=1)
        self.pick_button = widgets.Button(
            idle, "Pick from SolidWorks", command=self._start_plane_pick, icon="cursor",
        )
        self.pick_button.grid(row=0, column=0, sticky="w")
        self.pick_outcome_glyph = widgets.StepGlyph(idle, bg=theme.WHITE)
        self.pick_idle_text = tk.Label(
            idle, text="", bg=theme.WHITE, fg=theme.MUTED, font=self.fonts.hint,
            justify="left", anchor="w", wraplength=theme.px(230),
        )
        self.pick_idle_text.grid(row=0, column=2, sticky="w", padx=(theme.px(8), 0))
        self.pick_idle = idle

        block = widgets.Box(area, bg=theme.TRACKER_BG, border=theme.TRACKER_BORDER)
        block.columnconfigure(0, weight=1)
        inner = tk.Frame(block, bg=theme.TRACKER_BG)
        inner.pack(fill="both", expand=True, padx=theme.px(10), pady=theme.px(8))
        inner.columnconfigure(0, weight=1)

        title = tk.Frame(inner, bg=theme.TRACKER_BG)
        title.grid(row=0, column=0, sticky="ew")
        title.columnconfigure(2, weight=1)
        tk.Label(title, text="PICKING FROM SOLIDWORKS", bg=theme.TRACKER_BG,
                 fg=theme.ACCENT, font=self.fonts.header).grid(row=0, column=0, sticky="w")
        tk.Label(title, textvariable=self.pick_title, bg=theme.TRACKER_BG,
                 fg=theme.MUTED, font=self.fonts.tiny).grid(
            row=0, column=1, sticky="w", padx=(theme.px(8), 0)
        )
        self.pick_cancel = widgets.Button(title, "Cancel", command=self._cancel_pick,
                                          height=22, bg=theme.TRACKER_BG)
        self.pick_cancel.grid(row=0, column=3, sticky="e")

        self.pick_rows = tk.Frame(inner, bg=theme.TRACKER_BG)
        self.pick_rows.grid(row=1, column=0, sticky="ew", pady=(theme.px(6), 0))
        self.pick_rows.columnconfigure(2, weight=1)

        footer = tk.Frame(inner, bg=theme.TRACKER_BG)
        footer.grid(row=2, column=0, sticky="ew", pady=(theme.px(6), 0))
        footer.columnconfigure(0, weight=1)
        tk.Label(
            footer,
            text="Each click clears itself in SolidWorks — that is how one "
                 "click is told from the last.",
            bg=theme.TRACKER_BG, fg=theme.MUTED, font=self.fonts.readout,
            justify="left", anchor="w", wraplength=theme.px(250),
        ).grid(row=0, column=0, sticky="w")
        self.pick_done = widgets.Button(footer, "Use what I picked",
                                        command=self._finish_pick, height=22,
                                        bg=theme.TRACKER_BG)
        self.pick_done.grid(row=0, column=1, sticky="e", padx=(theme.px(8), 0))

        # The Skip button belongs to whichever row is active, so it is made
        # once here and moved rather than rebuilt with the rows.
        self.pick_skip = widgets.Button(self.pick_rows, "Skip", command=self._skip_pick,
                                        height=22, bg=theme.TRACKER_BG)
        self.pick_block = block
        self._tracker_widgets: List[tk.Widget] = []
        return area

    # -- Placement

    def _build_placement(self, parent: tk.Misc) -> widgets.Panel:
        panel = widgets.Panel(parent, "Placement", self.placement_readout)
        body = panel.body
        body.columnconfigure(0, minsize=theme.px(118))
        body.columnconfigure(1, weight=1)

        self._row_label(body, 0, "Leading edge at")
        cell = self._cell(body, 0, sticky="ew", pady=0)
        for column in range(3):
            cell.columnconfigure(column, weight=1, uniform="le")
        self.le_entries: List[widgets.Field] = []
        for ci, axis in enumerate("XYZ"):
            field = widgets.Field(cell, self.le_vars[ci], prefix=axis, grow=True)
            field.grid(row=0, column=ci, sticky="ew", padx=(0 if ci == 0 else theme.px(6), 0))
            field.entry.bind("<Key>", self._on_le_typed)
            self.le_entries.append(field)
        self.le_pick_button = widgets.Button(cell, "Pick", command=self._start_point_pick,
                                             icon="cursor")
        self.le_pick_button.grid(row=0, column=3, padx=(theme.px(8), 0))
        return panel

    # -- Output

    def _build_output(self, parent: tk.Misc) -> widgets.Panel:
        panel = widgets.Panel(parent, "Output")
        panel.readout_label.configure(text="Curve Through XYZ Points")
        body = panel.body
        body.columnconfigure(0, minsize=theme.px(118))
        body.columnconfigure(1, weight=1)

        self._row_label(body, 0, "Format")
        widgets.Segmented(body, self.extension, writer.EXTENSIONS, width=150,
                          mono=True).grid(row=0, column=1, sticky="w",
                                          pady=(0, theme.px(6)))

        self._row_label(body, 1, "Folder")
        folder = self._cell(body, 1, sticky="ew", pady=0)
        folder.columnconfigure(0, weight=1)
        widgets.Field(folder, self.out_folder, grow=True).grid(row=0, column=0, sticky="ew")
        widgets.Button(folder, "Browse", command=self._browse_folder,
                       icon="folder").grid(row=0, column=1, padx=(theme.px(8), 0))
        return panel

    # -- the SolidWorks bar, whose header is the connection itself

    def _sw_header(self, panel: widgets.Panel) -> None:
        """The connection, in a panel's header: dot, document, Curves.

        Every tab's SolidWorks bar has one, and all of them follow the one
        link this window keeps — the dots and buttons are registered here so
        that :meth:`_refresh_link_labels` and :meth:`_toggle_flyout` reach
        each of them.
        """
        slot = panel.header_slot()
        dot = widgets.Dot(slot, theme.FAINT, bg=theme.HEADER_BG)
        dot.grid(row=0, column=0, padx=(0, theme.px(6)))
        tk.Label(slot, textvariable=self.sw_headline, bg=theme.HEADER_BG,
                 fg=theme.READOUT, font=self.fonts.readout).grid(row=0, column=1)
        button = widgets.Button(
            slot, "Hide curves" if self._flyout_open else "Curves",
            command=self._toggle_flyout, icon="sidebar", height=20, bg=theme.HEADER_BG,
        )
        button.grid(row=0, column=2, padx=(theme.px(8), 0))
        dot.set_color(self._sw_dot_color)
        self._sw_dots.append(dot)
        self._curves_buttons.append(button)

    def _build_sw_bar(self, parent: tk.Misc) -> widgets.Panel:
        panel = widgets.Panel(parent, "SolidWorks")
        self._sw_header(panel)

        body = panel.body
        body.columnconfigure(1, weight=1)
        widgets.Icon(body, "info", theme.MUTED, 14).grid(row=0, column=0, sticky="nw")
        self.editing_label = tk.Label(
            body, textvariable=self.editing_text, bg=theme.WHITE, fg=theme.INK,
            font=self.fonts.hint, justify="left", anchor="w",
            wraplength=theme.px(STRIP_WIDTH - 60),
        )
        self.editing_label.grid(row=0, column=1, sticky="w", padx=(theme.px(8), 0))
        widgets.Check(body, self.insert_missing,
                      "Insert new curves into the open part").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(theme.px(7), 0)
        )
        return panel

    # -- the footer: what just happened, and the one button that does anything

    def _build_footer(self, parent: tk.Misc) -> tk.Frame:
        footer = tk.Frame(parent, bg=theme.WINDOW)
        footer.columnconfigure(1, weight=1)
        self.status_dot = widgets.Dot(footer, theme.OK, size=8, bg=theme.WINDOW)
        self.status_dot.grid(row=0, column=0, padx=(0, theme.px(8)))
        self.status_label = tk.Label(
            footer, textvariable=self.status, bg=theme.WINDOW, fg=theme.OK,
            font=self.fonts.label, anchor="w", justify="left",
            wraplength=theme.px(STRIP_WIDTH - 120),
        )
        self.status_label.grid(row=0, column=1, sticky="w")
        self.export_button = widgets.Button(footer, "Export", command=self._export,
                                            primary=True, height=32, bg=theme.WINDOW)
        self.export_button.grid(row=0, column=2, sticky="e", padx=(theme.px(8), 0))
        return footer

    # -- the flyout (§8): everything in it already existed; only the box is new

    def _build_solidworks(self, parent: tk.Misc) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        pad = theme.px(12)

        head = tk.Frame(parent, bg=theme.SECONDARY_BG)
        head.grid(row=0, column=0, sticky="ew", padx=pad, pady=(pad, 0))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="SOLIDWORKS · CURVES IN THE PART", bg=theme.SECONDARY_BG,
                 fg=theme.HEADER_TEXT, font=self.fonts.header).grid(row=0, column=0,
                                                                    sticky="w")
        widgets.Button(head, "Hide", command=self._toggle_flyout, height=20,
                       bg=theme.SECONDARY_BG).grid(row=0, column=1, sticky="e")

        status = tk.Frame(parent, bg=theme.SECONDARY_BG)
        status.grid(row=1, column=0, sticky="ew", padx=pad, pady=(theme.px(10), 0))
        status.columnconfigure(0, weight=1)
        tk.Label(status, textvariable=self.sw_headline, bg=theme.SECONDARY_BG,
                 fg=theme.INK, font=self.fonts.label_bold, anchor="w",
                 wraplength=theme.px(FLYOUT_WIDTH - 24)).grid(row=0, column=0, sticky="w")
        self.sw_status_label = tk.Label(
            status, textvariable=self.sw_detail, bg=theme.SECONDARY_BG,
            fg=theme.MUTED, font=self.fonts.readout, anchor="w", justify="left",
            wraplength=theme.px(FLYOUT_WIDTH - 24),
        )
        self.sw_status_label.grid(row=1, column=0, sticky="w", pady=(theme.px(2), 0))

        tools = tk.Frame(parent, bg=theme.SECONDARY_BG)
        tools.grid(row=2, column=0, sticky="ew", padx=pad, pady=(theme.px(8), theme.px(8)))
        tools.columnconfigure(3, weight=1)
        widgets.Button(tools, "Refresh", command=self._refresh_panel, icon="refresh",
                       height=24, bg=theme.SECONDARY_BG).grid(row=0, column=0)
        widgets.Button(tools, "New curve", command=self._new_curve, icon="plus",
                       height=24, bg=theme.SECONDARY_BG).grid(row=0, column=1,
                                                              padx=(theme.px(6), 0))
        self.track_button = widgets.Button(
            tools, "Track", command=self._track_strays, icon="link", height=24,
            bg=theme.SECONDARY_BG,
        )
        self.track_button.grid(row=0, column=2, padx=(theme.px(6), 0))
        tk.Label(tools, textvariable=self.tree_count, bg=theme.SECONDARY_BG,
                 fg=theme.MUTED, font=self.fonts.readout).grid(row=0, column=3, sticky="e")

        holder = widgets.Box(parent, bg=theme.WHITE)
        holder.grid(row=3, column=0, sticky="nsew", padx=pad)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.curve_tree = ttk.Treeview(
            holder, columns=("state",), height=10, selectmode="browse",
            style="Af.Treeview",
        )
        self.curve_tree.heading("#0", text="CURVE")
        self.curve_tree.heading("state", text="STATE")
        self.curve_tree.column("#0", width=theme.px(FLYOUT_WIDTH - 150), stretch=True)
        self.curve_tree.column("state", width=theme.px(84), stretch=False)
        self.curve_tree.grid(row=0, column=0, sticky="nsew")
        self.curve_tree.bind("<<TreeviewSelect>>", self._on_curve_selected)

        scroll = ttk.Scrollbar(holder, orient="vertical", style="Af.Vertical.TScrollbar",
                               command=self.curve_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.curve_tree.configure(yscrollcommand=scroll.set)

        self.curve_tree.tag_configure("linked", foreground=OK_COLOR)
        self.curve_tree.tag_configure("attention", foreground=ERROR_COLOR)
        self.curve_tree.tag_configure("untracked", foreground=theme.UNTRACKED)
        self.curve_tree.tag_configure("record", font=self.fonts.mono_bold)
        self.curve_tree.tag_configure("pending", foreground=theme.ACCENT)
        self.curve_tree.tag_configure("pending_child", foreground=theme.FAINT)

        self._build_card(parent).grid(row=4, column=0, sticky="ew", padx=pad,
                                      pady=(theme.px(8), pad))

    def _build_card(self, parent: tk.Misc) -> tk.Frame:
        """What the selected record is, and what Export will therefore do."""
        card = widgets.Box(parent, bg=theme.WHITE)
        inner = tk.Frame(card, bg=theme.WHITE)
        inner.pack(fill="both", expand=True, padx=theme.px(10), pady=theme.px(8))
        inner.columnconfigure(0, weight=1)

        top = tk.Frame(inner, bg=theme.WHITE)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        tk.Label(top, textvariable=self.card_title, bg=theme.WHITE, fg=theme.INK,
                 font=self.fonts.label_bold, anchor="w").grid(row=0, column=0, sticky="w")
        self.card_state_label = tk.Label(top, textvariable=self.card_state,
                                         bg=theme.WHITE, fg=theme.OK,
                                         font=self.fonts.readout)
        self.card_state_label.grid(row=0, column=1, sticky="e")

        name = tk.Frame(inner, bg=theme.WHITE)
        name.grid(row=1, column=0, sticky="ew", pady=(theme.px(6), 0))
        name.columnconfigure(1, weight=1)
        self.card_name_label = tk.Label(name, text="Name", bg=theme.WHITE,
                                        fg=theme.LABEL, font=self.fonts.label)
        self.card_name_label.grid(row=0, column=0, sticky="w", padx=(0, theme.px(8)))
        self.card_name_field = widgets.Field(name, self.curve_name, grow=True)
        self.card_name_field.grid(row=0, column=1, sticky="ew")
        self.card_name_field.entry.bind("<Key>", self._on_name_typed)

        tk.Label(inner, textvariable=self.card_settings, bg=theme.WHITE,
                 fg=theme.MUTED, font=self.fonts.readout, anchor="w", justify="left",
                 wraplength=theme.px(FLYOUT_WIDTH - 44)).grid(
            row=2, column=0, sticky="w", pady=(theme.px(6), 0)
        )
        self.card_hint = tk.Label(
            inner, text="", bg=theme.WHITE, fg=theme.MUTED, font=self.fonts.readout,
            anchor="w", justify="left", wraplength=theme.px(FLYOUT_WIDTH - 44),
        )
        self.card_hint.grid(row=3, column=0, sticky="w", pady=(theme.px(4), 0))
        return card

    def _toggle_flyout(self) -> None:
        """Show or hide the curve list, growing the window rather than the strip.

        The strip is a fixed width and must not reflow when the list appears:
        on an ultrawide the whole point is that the list opens into space that
        was not being used.
        """
        self._flyout_open = not self._flyout_open
        root = self.winfo_toplevel()
        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
        delta = theme.px(FLYOUT_WIDTH) + max(1, theme.px(1))
        if self._flyout_open:
            self._flyout_edge.grid()
            self.flyout.grid()
            root.geometry(f"{width + delta}x{height}")
        else:
            self._flyout_edge.grid_remove()
            self.flyout.grid_remove()
            root.geometry(f"{max(width - delta, theme.px(STRIP_WIDTH + 24))}x{height}")
        for button in self._curves_buttons:
            button.set_text("Hide curves" if self._flyout_open else "Curves")

    # ----------------------------------------------------------- interaction

    def _set_status(self, message: str, ok: bool = True) -> None:
        self.status.set(message)
        color = OK_COLOR if ok else ERROR_COLOR
        self.status_label.configure(foreground=color)
        self.status_dot.set_color(color)

    def _on_le_typed(self, event: tk.Event) -> None:
        """Stop auto-filling the leading edge once the user edits it themselves."""
        if event.keysym in NAVIGATION_KEYS:
            return
        self._le_manual = True
        self._le_picked = False
        self._refresh_placement_readout()

    def _refresh_plane_readout(self) -> None:
        """The Plane panel's header line, whenever anything it names moves."""
        self.plane_readout.set(ui_text.plane_readout(
            self.plane_mode.get(), self.chord_axis.get(), self.up_axis.get(),
            self.constraint.get(), self.main_plane.get(), self._picked_plane,
        ))

    def _refresh_placement_readout(self) -> None:
        """Where the 2D origin lands: the old ``le_hint``, in the panel header."""
        self.placement_readout.set(ui_text.placement_readout(
            self.plane_mode.get(), self._le_manual, self._le_picked
        ))

    def _on_plane_mode_changed(self) -> None:
        mode = self.plane_mode.get()
        on_main_plane = mode in geometry.MAIN_PLANES
        needed = 3 if mode == MODE_3POINTS else (2 if mode in (MODE_2POINTS, MODE_NORMAL) else 0)
        for pi, fields in enumerate(self.p_entries):
            for field in fields:
                field.set_enabled(pi < needed)
                # The ink border says "a pick put this here", and a change of
                # mode is the user saying they are past that.
                field.set_marked(False)

        constraint_state = "readonly" if mode == MODE_2POINTS else "disabled"
        self.constraint_box.configure(state=constraint_state)
        self.main_plane_box.configure(state=constraint_state)
        self.constraint_label.configure(
            fg=theme.LABEL if mode == MODE_2POINTS else theme.FAINT
        )

        # On main planes the axis pickers set 'up' outright, so the flip checkbox
        # would only be a confusing second way to say the same thing. Off them —
        # on a custom plane, or on a loaded curve's own plane — there are no axes
        # to pick, and flipping 'up' is the only choice left.
        axis_state = "readonly" if on_main_plane else "disabled"
        self.chord_axis_box.configure(state=axis_state)
        self.up_axis_box.configure(state=axis_state)
        self.flip_check.configure(state="disabled" if on_main_plane else "normal")
        self.chord_label.configure(fg=theme.LABEL if on_main_plane else theme.FAINT)
        self.up_label.configure(fg=theme.LABEL if on_main_plane else theme.FAINT)
        # Off a main plane the chord is the line P1 → P2; the dropdown that
        # cannot say that steps aside for the field that can.
        if on_main_plane:
            self.chord_from_points.grid_remove()
            self.chord_axis_box.master.grid()
        else:
            self.chord_axis_box.master.grid_remove()
            self.chord_from_points.grid(row=0, column=0)
        if on_main_plane:
            self.flip.set(False)
            self._reset_axes(mode)
        if mode != MODE_3POINTS:
            self._picked_plane = False

        self._le_manual = False
        self._le_picked = False
        self._sync_leading_edge()
        self._refresh_plane_readout()
        self._refresh_placement_readout()

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

    def _unload(self) -> None:
        """Take the loaded section off the form: there is nothing to export."""
        self.data = None
        self.section = None
        self.loaded_text.set("No file loaded.")
        self._show_loaded(None, curve=False)
        self.plane_seg.set_option_enabled(MODE_LOADED, False)

    def _show_loaded(self, data: Optional[AirfoilData], curve: bool) -> None:
        """The Source panel's second line: the section, drawn and named.

        A path proves a file was chosen. The outline proves an aerofoil was
        read out of it, which is the thing that actually goes wrong — a CSV
        with the wrong columns parses into a shape nobody would loft.
        """
        if data is None:
            self.loaded_name.set("No file loaded.")
            self.loaded_detail.set("")
            self.loaded_name_label.configure(fg=theme.FAINT)
            self.outline.show(None)
            self.outline.grid_remove()
            self.target_field.set_placeholder("")
            return

        name, detail = ui_text.source_line(
            data.name, data.chord, len(data.airfoil), bool(data.camber), curve
        )
        self.loaded_name.set(name)
        self.loaded_detail.set(f"· {detail}")
        self.loaded_name_label.configure(fg=theme.INK)
        self.outline.grid()
        self.outline.show(data.airfoil)
        self.target_field.set_placeholder(f"{data.chord:g}")
        self._relayout()

    def _load(self, path: str) -> None:
        self.section = None
        try:
            data, self.section = load_source(path)
            curve = self.section is not None
        except (AirfoilParseError, GeometryError) as exc:
            self._unload()
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
        self._show_loaded(data, curve)

        # A new curve is named after the file it came from until someone says
        # otherwise; a remembered one keeps the name it was exported with.
        if not self._editing and not self._name_typed:
            self.curve_name.set(self._source_stem())

        # A curve already stands somewhere, so keeping it there is the sane default.
        self.plane_seg.set_option_enabled(MODE_LOADED, bool(curve))
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
    # A full read of the part's curves this many quiet ticks apart, to catch a
    # rename, which moves neither the feature count nor the update stamp. It
    # costs SolidWorks a few hundred milliseconds of its drawing thread, so it
    # is only made while this window has the focus — and coming back to the
    # window reads the part afresh anyway.
    SLOW_EVERY = 5

    # A pick is a click being waited for, not a panel being kept fresh, so it
    # polls fast — and only while it is armed, which is why the panel's own
    # rate is left alone. A pick nobody finishes gives up rather than polling
    # SolidWorks for the rest of the afternoon.
    PICK_MS = 200
    PICK_GIVES_UP_AFTER = 90.0  # seconds

    def _start_link(self) -> None:
        if not swcom.is_available():
            self.sw_status.set(
                "SolidWorks link off: pywin32 is not installed, so curves are "
                "written to files only."
            )
            self._refresh_link_labels()
            return
        self._worker = swcom.Worker()
        self.after(200, self._tick)

    def _tick(self) -> None:
        """One turn of the poll. Never blocks, never raises into the loop.

        Each collector is guarded on its own. They share one channel to the
        worker, so a collector that throws every time would otherwise starve
        the ones after it: the answers pile up uncollected and the panel says
        "Looking for SolidWorks..." for ever, with nothing on screen to say why.
        That is a real bug this once had, so the trouble is now reported rather
        than swallowed — once per reason, not once a second.
        """
        for collect in (self._collect_push, self._collect_arrange, self._collect):
            try:
                collect()
            except Exception as exc:  # noqa: BLE001 - never kill the timer
                self._tick_trouble(exc)

        try:
            if self._worker is not None and self._pending is None and self._pending_push is None:
                if self._pick is not None:
                    self._ask_for_pick()
                else:
                    self._quiet_ticks += 1
                    if self._quiet_ticks >= self.SLOW_EVERY and self._window_has_focus():
                        self._quiet_ticks = 0
                        self._ask_for_snapshot()
                    else:
                        self._ask_for_key()
        except Exception as exc:  # noqa: BLE001 - never kill the timer
            self._tick_trouble(exc)

        self.after(self.PICK_MS if self._pick is not None else self.POLL_MS, self._tick)

    def _window_has_focus(self) -> bool:
        try:
            return self.focus_displayof() is not None
        except (KeyError, tk.TclError):
            # A combobox's drop-down list is not a widget tkinter knows by name.
            return True

    def _tick_trouble(self, exc: BaseException) -> None:
        reason = f"{type(exc).__name__}: {exc}"
        if reason == self._tick_failure:
            return
        self._tick_failure = reason
        try:
            self._set_status(f"The SolidWorks poll hit a problem — {reason}", ok=False)
        except Exception:  # noqa: BLE001 - the status line is not worth a crash
            pass

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

    def _ask_for_pick(self) -> None:
        """The only question worth asking while the user is off clicking."""
        self._pick_ticks += 1
        if self._pick_ticks * self.PICK_MS > self.PICK_GIVES_UP_AFTER * 1000:
            self._end_pick("Pick stopped: nothing was clicked.")
            return
        self._update_pick_title()
        self._ask("pick", _read_selection)

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

    # ------------------------------------------------- picking in SolidWorks
    #
    # The app is polling, not being told. So a pick clears the selection every
    # time it reads it, which is what makes one click distinguishable from the
    # same thing still being selected a fifth of a second later — and shows the
    # user their click landed. The first answer of a pick is thrown away for
    # the same reason: it is whatever happened to be selected when the button
    # was pressed, not a click.

    def _start_plane_pick(self) -> None:
        self._begin_pick(pick.PLANE_PICK)

    def _start_point_pick(self) -> None:
        self._begin_pick(pick.POINT_PICK)

    def _begin_pick(self, steps) -> None:
        if not self._can_push():
            self._set_status(self._no_pick_reason(), ok=False)
            return
        self._pick = pick.Pick(steps)
        self._pick_ticks = 0
        self._pick_watch = pick.SelectionWatch()
        self._quiet_ticks = 0
        self._pick_outcome = ""
        self.pick_text.set(self._pick.says)
        self._show_pick()

    def _cancel_pick(self) -> None:
        self._end_pick("Pick cancelled. Nothing on the form was changed.")

    def _end_pick(self, message: str = "") -> None:
        """Leave pick mode without applying anything."""
        if self._pick is None:
            return
        self._pick = None
        self.pick_text.set(message)
        self._pick_outcome = "stopped" if message else ""
        self._show_pick()

    def _skip_pick(self) -> None:
        if self._pick is None or not self._pick.skip():
            return
        self._after_pick_step()

    def _finish_pick(self) -> None:
        if self._pick is None:
            return
        self._pick.stop()
        self._after_pick_step()

    def _took_pick(self, picked) -> None:
        if self._pick is None:
            return
        # Whatever was selected when the pick began, and the same selection
        # read again on every poll after, are not clicks.
        clicked = self._pick_watch.fresh(picked)
        if clicked is None:
            return
        self._pick.accept(clicked)
        self._after_pick_step()

    def _after_pick_step(self) -> None:
        session = self._pick
        if session is None:
            return
        self.pick_text.set(session.says)
        self._show_pick()
        if session.finished:
            self._apply_pick(session)

    def _apply_pick(self, session) -> None:
        try:
            placement = session.resolve()
        except GeometryError as exc:
            self._end_pick(f"Pick stopped: {exc}")
            return
        self._pick = None
        self._picked_plane = placement.plane_mode == MODE_3POINTS
        self._apply_placement(placement)
        self.pick_text.set(_picked_summary(session))
        self._pick_outcome = "done"
        self._show_pick()

    def _apply_placement(self, placement) -> None:
        """Put a resolved pick on the form.

        The order is the whole method. Setting the plane mode clears the
        hand-typed leading-edge flag and re-syncs the leading edge from P1, so
        the points have to be written after the mode and the leading edge after
        the points — the same order :meth:`_apply_record` follows, and for the
        same reason.
        """
        if placement.plane_mode is not None:
            self.plane_mode.set(placement.plane_mode)
            self._on_plane_mode_changed()
            for pi, point in enumerate(placement.points):
                for ci, value in enumerate(point):
                    self.p_vars[pi][ci].set(f"{value:g}")
                    self.p_entries[pi][ci].set_marked(True)
        if placement.leading_edge is not None:
            for ci, value in enumerate(placement.leading_edge):
                self.le_vars[ci].set(f"{value:g}")
                self.le_entries[ci].set_marked(True)
            self._le_manual = True
            self._le_picked = True
            self._refresh_placement_readout()
        self._refresh_plane_readout()

    # ------------------------------------------------------- the pick tracker
    #
    # One row per step, per §7. The rows are rebuilt rather than updated in
    # place: there are three of them at most, and a row's shape changes with
    # its state.

    def _show_pick(self) -> None:
        """Draw pick mode as it stands: idle, mid-pick, or just finished."""
        picking = self._pick is not None
        ready = self._can_push()
        start = "disabled" if picking or not ready else "normal"
        self.pick_button.configure(state=start)
        self.le_pick_button.configure(state=start)

        if picking:
            self.pick_idle.grid_remove()
            self.pick_block.grid(row=0, column=0, sticky="ew")
            self._fill_tracker()
        else:
            self.pick_block.grid_remove()
            self.pick_idle.grid()
            self._fill_idle_row()
        self._relayout()

    def _fill_idle_row(self) -> None:
        """The button, and either what a pick would do or what the last one did."""
        message = self.pick_text.get()
        if self._pick_outcome == "done" and message:
            self.pick_outcome_glyph.set_state("done")
            self.pick_outcome_glyph.grid(row=0, column=1, padx=(theme.px(8), 0))
            self.pick_idle_text.configure(text=message, fg=theme.INK)
        elif self._pick_outcome == "stopped" and message:
            self.pick_outcome_glyph.grid_remove()
            self.pick_idle_text.configure(text=message, fg=theme.MUTED)
        else:
            self.pick_outcome_glyph.grid_remove()
            self.pick_idle_text.configure(
                text="Fills P1–P3 from a plane, a line and a point you click "
                     "in the part.",
                fg=theme.MUTED,
            )

    def _fill_tracker(self) -> None:
        session = self._pick
        if session is None:
            return
        for widget in self._tracker_widgets:
            widget.destroy()
        self._tracker_widgets = []
        self.pick_skip.grid_remove()
        self._update_pick_title()

        for row, step in enumerate(ui_text.tracker_rows(session)):
            glyph = widgets.StepGlyph(self.pick_rows)
            glyph.set_state(step.state)
            glyph.grid(row=row, column=0, sticky="w", pady=(0, theme.px(4)))

            faint = step.state == "pending"
            name = tk.Label(
                self.pick_rows, text=step.name, bg=theme.TRACKER_BG,
                fg=theme.FAINT if faint else theme.INK,
                font=self.fonts.label_bold if step.state == "active" else self.fonts.label,
                anchor="w",
            )
            name.grid(row=row, column=1, sticky="w", padx=(theme.px(8), 0))

            detail = tk.Label(
                self.pick_rows, text=step.detail, bg=theme.TRACKER_BG,
                fg=theme.ERROR if step.error else (theme.FAINT if faint else theme.MUTED),
                font=self.fonts.mono_tiny if step.state == "done" else self.fonts.hint,
                anchor="w", justify="left", wraplength=theme.px(210),
            )
            detail.grid(row=row, column=2, sticky="w", padx=(theme.px(8), 0))
            self._tracker_widgets.extend((glyph, name, detail))

            if step.state == "active" and step.skippable:
                self.pick_skip.grid(row=row, column=3, sticky="e", padx=(theme.px(8), 0))

    def _update_pick_title(self) -> None:
        """``step 2 of 3 · 1:12 left`` — the pick's own timeout, made visible."""
        session = self._pick
        if session is None:
            return
        left = self.PICK_GIVES_UP_AFTER - self._pick_ticks * self.PICK_MS / 1000.0
        self.pick_title.set(ui_text.tracker_title(session, left))

    def _no_pick_reason(self) -> str:
        if not swcom.is_available():
            return "pywin32 is not installed, so nothing can be picked in SolidWorks."
        snapshot = self._snapshot
        if snapshot is None:
            return "SolidWorks is not reachable, so there is nothing to pick in."
        if not snapshot.get("title"):
            return "No document is open in SolidWorks, so there is nothing to pick in."
        if not snapshot.get("is_part"):
            return f"{snapshot['title']} is not a part, so there is nothing to pick in it."
        return "Nothing can be picked just now."

    def _collect(self) -> None:
        if self._pending is None:
            return
        answer = self._pending.poll()
        if answer is None:
            return
        value, error = answer
        kind, self._pending, self._pending_kind = self._pending_kind, None, ""

        if error is not None:
            if kind == "pick":
                self._end_pick(f"Pick stopped: {error}")
                return
            self._snapshot = None
            self.sw_status.set(self._link_message(error))
            self._refresh_link_labels()
            self._fill_tree()
            self._show_pick()
            return

        if kind == "pick":
            self._took_pick(value)
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
        self._show_pick()
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
        self._refresh_link_labels()

    def _refresh_link_labels(self) -> None:
        """The bar's dot and readout, and the flyout's two lines under it.

        ``sw_status`` stays the one sentence it always was and is still what
        the flyout falls back to; what is new is that the *state* of the link
        is a colour on a dot, which is readable without reading.
        """
        headline, state = ui_text.link_headline(swcom.is_available(), self._snapshot)
        self.sw_headline.set(headline)
        self._sw_dot_color = {"ok": theme.OK, "bad": theme.ERROR}.get(state, theme.FAINT)
        for dot in self._sw_dots:
            dot.set_color(self._sw_dot_color)
        self.sw_detail.set(
            ui_text.link_detail(self._snapshot, self._sidecar_path, self.sw_status.get())
        )
        # The design disables the Curves button when the link is down. It is
        # left live here: the flyout is also where the reason is written out in
        # full, and a user who had closed it could otherwise not get it back.

    # ------------------------------------------------------- remembered work

    def _load_sidecar(self) -> None:
        """Read the part's record, and never drop one on the way.

        Records made while the part had no path have nowhere to go, so they are
        held aside. Saving the part is what gives them somewhere — and it is
        also what brings this method back round — so this is the one place that
        has to carry them across rather than read straight over them.

        They are only ever carried into a document that actually holds the
        curves they name. Otherwise what happened was not a save but a different
        part being opened, and those curves are nothing to do with this one.
        """
        snapshot = self._snapshot or {}
        path = snapshot.get("path") or ""
        features = snapshot.get("curves", [])

        if not path:
            # An unsaved document must not be shown the last part's records, so
            # its memory starts empty and only the homeless records whose curves
            # it holds come back into it.
            if self._sidecar is None or self._sidecar_path or self._sidecar.part_path:
                self._sidecar = store.Sidecar()
            self._sidecar_path = ""
            self._sidecar_held = ""
            store.carry_over(self._sidecar, self._homeless, features)
            self._forget_stale_editing()
            return

        sidecar_path = store.sidecar_path(path)
        held = ""
        try:
            loaded = store.load(sidecar_path)
        except store.StoreError as exc:
            loaded = None
            if os.path.exists(sidecar_path):
                # Still there, and unread: written by a newer version, or not
                # readable at all. Whatever it holds, writing over it would
                # destroy it, so nothing this session saves may go there.
                held = str(exc)
            self._set_status(str(exc), ok=False)
        # All three set only now. The records on show and the file they came
        # from must never disagree, and a load that fails must not leave the
        # last part's records standing against this part's path.
        self._sidecar_path = sidecar_path
        self._sidecar_held = held
        self._sidecar = loaded or store.Sidecar(
            part_path=path, part_title=snapshot.get("title", "")
        )

        moved = store.carry_over(self._sidecar, self._homeless, features)
        if moved and self._sidecar_writable():
            ids = {record.export_id for record in moved}
            self._homeless = [r for r in self._homeless if r.export_id not in ids]
            self._save_sidecar()
            curves = sum(len(record.live_curves()) for record in moved)
            self._set_status(
                f"{os.path.basename(self._sidecar_path)} was written: the {curves} "
                "curve(s) made before the part was saved are remembered now."
            )
        self._forget_stale_editing()

    def _forget_stale_editing(self) -> None:
        """The record being edited belongs to the part that was open.

        Another part's list will not hold it, and an id pointing at nothing is
        worse than starting a new curve: Export would have nothing to update.
        """
        if self._editing and (
            self._sidecar is None or self._sidecar.find(self._editing) is None
        ):
            self._editing = ""
            self._describe_editing()

    def _sidecar_writable(self) -> bool:
        """Is there a file to keep the records in, and may it be written?"""
        return bool(self._sidecar_path) and not self._sidecar_held

    def _save_sidecar(self) -> None:
        if self._sidecar is None or not self._sidecar_writable():
            return
        self._sidecar.written_by = f"airfoil-converter {__version__}"
        self._sidecar.part_path = (self._snapshot or {}).get("path", "")
        self._sidecar.part_title = (self._snapshot or {}).get("title", "")
        try:
            store.save(self._sidecar_path, self._sidecar)
        except OSError as exc:
            self._set_status(f"Could not write the curve record: {exc}", ok=False)

    def _memory_note(self) -> str:
        """Said after an export that had nowhere to keep its settings."""
        if self._sidecar_held:
            return (
                " The curve record beside this part could not be read, so what you "
                "export is held in the app only and the file is left as it is."
            )
        if self._sidecar_path:
            return ""
        return (
            " This part has never been saved, so its settings are held in the app "
            "only — save the part and they will be kept beside it."
        )

    def _track_strays(self) -> None:
        """Take over curves in the part that no record claims.

        Their names are all that is left of them, and the names carry the stem,
        the role and the index — enough to update the same features again
        rather than make a second set beside them. The settings are gone; the
        record says so rather than showing a plausible set of defaults.
        """
        if self._sidecar is None or not (self._snapshot or {}).get("is_part"):
            self._set_status(
                "No part is open in SolidWorks, so there is nothing to track.",
                ok=False,
            )
            return
        features = (self._snapshot or {}).get("curves", [])
        made = store.adopt(
            self._sidecar, features, self.out_folder.get().strip(), self.extension.get()
        )
        if not made:
            self._set_status(
                "Nothing to track: every curve in the part is either already in a "
                "record or was not made by this app."
            )
            return
        if not self._sidecar_writable():
            self._homeless.extend(made)
        self._save_sidecar()
        self._fill_tree()
        curves = sum(len(record.live_curves()) for record in made)
        self._set_status(
            f"Tracked {curves} curve(s) as {len(made)} record(s). Their settings "
            "were not remembered, so open one and export to make it yours again."
            + self._memory_note()
        )
        self._arrange_tree()

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
        self._last_states = self._states()
        for state in self._last_states:
            if state.export_id:
                by_export.setdefault(state.export_id, []).append(state)
            else:
                strays.append(state)

        for export_id, states in by_export.items():
            record = self._find_record(export_id)
            worst = "linked" if all(not s.needs_attention for s in states) else "attention"
            node = self.curve_tree.insert(
                "", "end", iid=export_id, text=_record_label(record, export_id), open=True,
                values=(_record_place(record),), tags=(worst, "record"),
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
                open=True, values=("",), tags=("untracked", "record"),
            )
            for position, state in enumerate(strays):
                self.curve_tree.insert(
                    node, "end", iid=f"__strays__/{position}/{state.feature}",
                    text=state.feature, values=(store.ORPHAN,), tags=("untracked",),
                )

        # A curve that has not been exported yet has no feature and no record,
        # so nothing above would list it. It is listed anyway, at the top and
        # selected, because the whole danger of "New curve" is that it looks
        # exactly like still editing the last one.
        if not self._editing:
            self.curve_tree.insert(
                "", 0, iid=NEW_CURVE, text=self._stem() or "New curve", open=True,
                values=(self._le_place(),), tags=("pending", "record"),
            )
            self.curve_tree.insert(
                NEW_CURVE, "end", iid=f"{NEW_CURVE}/0", text="not exported yet",
                values=("new",), tags=("pending_child",),
            )
            keep = keep if keep and keep != NEW_CURVE else NEW_CURVE

        if keep and self.curve_tree.exists(keep):
            self.curve_tree.selection_set(keep)

        rows = len(by_export) + len(strays) + sum(len(v) for v in by_export.values())
        rows += 1 if strays else 0
        rows += 2 if not self._editing else 0
        self.tree_count.set(f"{rows} row{'' if rows == 1 else 's'}")
        self._describe_selection()

    def _le_place(self) -> str:
        """Where the form says the leading edge is, in the tree's own spelling."""
        return ", ".join(var.get() for var in self.le_vars)

    def _stem(self) -> str:
        """The name the next export will carry into SolidWorks.

        Typed in, or the source file's own name when nothing has been typed.
        It is sanitized at the point it becomes a feature name, by
        :func:`export.feature_name`, so what is typed here is left alone.
        """
        return self.curve_name.get().strip() or self._source_stem()

    def _on_name_typed(self, event: tk.Event) -> None:
        """Once a name is typed it stops following the source file."""
        if event.keysym in NAVIGATION_KEYS:
            return
        self._name_typed = True

    def _on_name_changed(self) -> None:
        """Keep the pending row's label on the name as it is typed."""
        if self.curve_tree.exists(NEW_CURVE):
            self.curve_tree.item(NEW_CURVE, text=self._stem() or "New curve")
        self._describe_editing()

    def _describe_selection(self) -> None:
        """The card under the tree: what is selected, and what Export will do.

        It answers the question the tree cannot: a row says a curve is linked,
        the card says which settings produced it, so a rib can be recognised
        before it is opened.
        """
        record = None
        selected = self.curve_tree.selection()
        if selected and self._sidecar is not None:
            export_id = selected[0].split("/")[0]
            if export_id not in ("__strays__", NEW_CURVE):
                record = self._find_record(export_id)

        # The name is only editable on a curve that does not exist yet.
        # Renaming one that does would leave its old features behind in the
        # part, and that is what Export's own warning is for, not a text field.
        self.card_name_field.set_enabled(record is None)
        self.card_name_label.configure(
            fg=theme.LABEL if record is None else theme.FAINT
        )

        if record is None:
            self.card_title.set("New curve")
            self.card_state.set("not exported yet")
            self.card_state_label.configure(fg=theme.MUTED)
            try:
                self.card_settings.set(ui_text.record_summary(self._spec()))
            except Exception:  # noqa: BLE001 - a half-typed field is not a card
                self.card_settings.set("")
            self.card_hint.configure(
                text="Export will make it, and name its curves after this. "
                     "Click a record above to go back to editing that one."
            )
            return

        self.card_title.set(_record_label(record, record.export_id))
        states = [s for s in self._last_states if s.export_id == record.export_id]
        text, ok = ui_text.curves_linked(states)
        self.card_state.set(text)
        self.card_state_label.configure(fg=theme.OK if ok else theme.ERROR)
        if isinstance(record, store.WingRecord):
            try:
                self.card_settings.set(ui_text.wing_summary(record.spec))
            except Exception:  # noqa: BLE001 - a record written by a later version
                self.card_settings.set("")
            self.card_hint.configure(text="A wing. Click it to open it on the Wing tab.")
            return
        if record.adopted:
            self.card_settings.set(ui_text.ADOPTED_SETTINGS)
            self.card_hint.configure(text=ui_text.ADOPTED_HINT)
            return
        try:
            self.card_settings.set(ui_text.record_summary(record.spec))
        except Exception:  # noqa: BLE001 - a record written by a later version
            self.card_settings.set("")
        self.card_hint.configure(
            text="Click a record to load its settings into the form. Export "
                 "then updates it in place."
        )

    # --------------------------------------------------------- quick switch

    def _on_curve_selected(self, _event: tk.Event) -> None:
        if self._restoring:
            return
        self._describe_selection()
        selected = self.curve_tree.selection()
        if not selected:
            return
        export_id = selected[0].split("/")[0]
        if export_id in ("__strays__", NEW_CURVE) or self._sidecar is None:
            return
        if self._sidecar.find_wing(export_id) is not None:
            if self._show_wing is not None:
                self._show_wing(export_id)
            self._select_editing()
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
        if record is None or record.adopted:
            # An adopted record never put anything on the form, so nothing on
            # the form is a change to it.
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
        if record.adopted:
            self._editing = record.export_id
            if record.output_folder:
                self.out_folder.set(record.output_folder)
            self.curve_name.set(record.stem)
            self._name_typed = False
            self._describe_editing()
            self._fill_tree()
            self._select_editing()
            self._set_status(
                f"Editing {record.stem}. Nothing was remembered about how its "
                "curves were made, so the form is left as it is and Export will "
                "rebuild them from what it says now."
            )
            return

        self._restoring = True
        try:
            if record.source:
                if os.path.exists(record.source):
                    self.csv_path.set(record.source)
                    self._load(record.source)
                else:
                    # The form must not keep another file's section under this
                    # record's name: Export would rebuild its curves from it.
                    self.csv_path.set(record.source)
                    self._unload()
                    self._set_status(
                        f"The source file has moved: {record.source}. Browse to it "
                        "again before exporting.",
                        ok=False,
                    )
            if record.loaded_section:
                self.section = section_from_dict(record.loaded_section)

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
            self.curve_name.set(record.stem)
            self._name_typed = False
        finally:
            self._restoring = False

        self._editing = record.export_id
        self._describe_editing()
        # The row for a curve that was about to be made has to go: from here
        # Export updates this record instead of making anything.
        self._fill_tree()
        self._select_editing()

    def _new_curve(self) -> None:
        """Keep every field, drop the identity: the way a second rib is made.

        The form does not change, which is exactly why this has to be visible
        somewhere: the danger of pressing this is believing you are still
        editing the rib you were editing a moment ago, or the other way round.
        So a row for the curve appears at the top of the list, selected, and
        the name it will take is editable in the card underneath.
        """
        self._editing = ""
        self._name_typed = False
        self.curve_name.set(self._source_stem())
        self._describe_editing()
        self._fill_tree()
        self._select_editing()
        self._set_status(
            f"New curve: Export will make {self._stem() or 'a new curve'} rather "
            "than update the last one."
        )

    def _source_stem(self) -> str:
        return os.path.splitext(os.path.basename(self.csv_path.get()))[0]

    def _select_editing(self) -> None:
        wanted = self._editing or NEW_CURVE
        if self.curve_tree.exists(wanted):
            self.curve_tree.selection_set(wanted)

    def _describe_editing(self) -> None:
        """Say which of the two things Export is about to do."""
        if not self._editing or self._sidecar is None:
            name = self._stem()
            self.editing_text.set(
                f"Export will make a new curve, {name}." if name
                else "Export will make a new curve."
            )
            return
        record = self._sidecar.find(self._editing)
        names = ", ".join(record.feature_names()) if record else self._editing
        self.editing_text.set(f"Export will update {names}.")

    def _export(self) -> None:
        self._end_pick()
        try:
            self._export_unsafe()
        except (InputError, GeometryError, AirfoilParseError, ValueError) as exc:
            self._set_status(str(exc), ok=False)
        except swlink.LinkError as exc:
            self._set_status(str(exc), ok=False)
        except OSError as exc:
            self._set_status(f"Could not write the output: {exc}", ok=False)

    def busy(self) -> bool:
        """Is either tab still waiting on SolidWorks for an export?"""
        wing_push = self._wing_tab is not None and self._wing_tab.pushing
        return self._pending_push is not None or wing_push

    def _find_record(self, export_id: str) -> "Optional[Any]":
        """A rib's record, or a wing's: the list shows both."""
        if self._sidecar is None:
            return None
        return self._sidecar.find(export_id) or self._sidecar.find_wing(export_id)

    def _export_unsafe(self) -> None:
        if self.busy():
            raise InputError("SolidWorks is still working on the last export.")
        if self.data is None:
            source = self.csv_path.get().strip()
            if source and not os.path.exists(source):
                raise InputError(
                    f"The source file has moved: {source}. Browse to it again before exporting."
                )
            raise InputError("Load a CSV first.")

        folder = self.out_folder.get().strip()
        if not folder:
            raise InputError("Choose an output folder.")
        if not os.path.isdir(folder):
            raise InputError(f"Output folder does not exist: {folder}")

        record = self._sidecar.find(self._editing) if (self._sidecar and self._editing) else None
        # A record keeps the name it was made with; a new curve takes the one
        # in the card, which starts as the source file's own name.
        stem = record.stem if record else self._stem()
        index = record.name_index if record else self._next_index(stem)

        spec = self._spec()
        curves = build_curves(self.data, spec, stem, self.section, index=index)

        # Only the curves an export writes can be orphaned by it. A joined
        # curve is derived from two of those, so no change of settings ever
        # stops producing it directly. The record is not marked until the
        # export lands: what it says about the part has to stay true if the
        # push fails.
        orphans = (
            swlink.orphaned([c.feature for c in record.written_curves()], curves)
            if record is not None else []
        )
        if orphans and not self._confirm_orphans(record, orphans):
            return

        if not self._can_push():
            changed, hashes = swlink.write_files(curves, folder)
            self._remember(record, curves, spec, stem, folder, index, hashes,
                           pushed=False, retire=orphans)
            self._set_status(
                f"Wrote {len(curves)} file(s). " + self._offline_reason()
                + self._memory_note()
            )
            return

        join_as = feature_name(stem, ROLE_JOINED, index)
        self._begin_push(
            curves, folder,
            join_as=join_as,
            # A file rewritten while SolidWorks was closed is unchanged by this
            # export, so the push has to be told the part has never read it.
            force=store.stale_in_part(record) if record is not None else (),
            groups=self._tree_groups(record, curves, stem, index, join_as, retiring=orphans),
            context=dict(record=record, curves=curves, spec=spec, stem=stem,
                         folder=folder, index=index, retire=orphans),
        )

    def _tree_groups(self, record, curves, stem: str, index: int,
                     join_as: str, retiring: Collection[str] = ()) -> "List[swlink.TreeGroup]":
        """What the feature tree should look like once this export lands.

        Every record, not just this one: the parent folder holds all of them,
        so it can only be checked against the whole set. The export being made
        is described from the curves it is about to write, because its record
        does not exist yet on the first export of a new curve.
        """
        editing = record.export_id if record is not None else ""
        groups = self._recorded_groups(skip=editing)

        mine = [c.feature for c in curves]
        if joinable(curves):
            mine.append(join_as)
        if record is not None:
            # A retired curve is still in the part, and still this rib's — as
            # is one this export is about to retire.
            mine.extend(c.feature for c in record.curves
                        if (c.retired or c.feature in retiring) and c.feature not in mine)
        if mine:
            groups.append(swlink.TreeGroup(folder_name(stem, index), tuple(mine)))
        return groups

    def _recorded_groups(self, skip: str = "") -> "List[swlink.TreeGroup]":
        """A folder for every record that has curves in the part."""
        groups: List[swlink.TreeGroup] = []
        for record in (self._sidecar.exports if self._sidecar else []):
            if record.export_id == skip or not record.curves:
                continue
            groups.append(swlink.TreeGroup(
                folder_name(record.stem, record.name_index),
                tuple(c.feature for c in record.curves),
            ))
        return groups

    def _arrange_tree(self) -> None:
        """Tidy the tree on its own, outside an export.

        Tracking curves makes records without touching SolidWorks, so this is
        what puts the folders round them. It goes through the worker like every
        other COM call, and reports when it lands.
        """
        groups = self._recorded_groups()
        if not groups or self._worker is None or not (self._snapshot or {}).get("is_part"):
            return
        if self._pending_arrange is not None or self._pending_push is not None:
            return
        self._pending_arrange = self._worker.submit(
            lambda session: swlink.arrange(session, groups)
        )

    def _collect_arrange(self) -> None:
        if self._pending_arrange is None:
            return
        answer = self._pending_arrange.poll()
        if answer is None:
            return
        result, error = answer
        self._pending_arrange = None
        if error is not None:
            self._set_status(f"The tree could not be tidied: {error}", ok=False)
            return
        if result.made:
            self._set_status(f"{self.status.get()} Tidied the tree: "
                             f"{result.summary()}.")
        self._refresh_panel()

    # ------------------------------------------------------ pushing, without
    # blocking the window. Waiting on the worker from here would stop this
    # thread pumping messages, and a cross-apartment COM call that needs it
    # then never completes — which is a hang, not an error.

    def _begin_push(self, curves, folder: str, join_as: str, groups,
                    context: Dict[str, Any], force: Collection[str] = ()) -> None:
        assert self._worker is not None
        insert = self.insert_missing.get()
        self._push_context = context

        def work(session):
            result = swlink.push(
                session, curves, folder, insert_missing=insert, force=force, join_as=join_as
            )
            # The curves are in by now. Tidying the tree is the last thing, and
            # never the thing that loses a successful push: it reports its own
            # trouble rather than raising through it.
            try:
                result.arranged = swlink.arrange(session, groups)
            except swcom.SolidWorksError as exc:
                result.failures.append(("folders", str(exc)))
            return result

        self._pending_push = self._worker.submit(work)
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
            failed={name for name, _ in result.failures}, retire=context.get("retire", ()),
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
        if result.arranged is not None and result.arranged.made:
            message += f". Tidied the tree: {result.arranged.summary()}"
        if result.failures:
            detail = "; ".join(f"{name}: {why}" for name, why in result.failures)
            self._set_status(f"{message}. Failed — {detail}", ok=False)
        else:
            self._set_status(message + self._memory_note())

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

    def _confirm_orphans(self, record: "store.ExportRecord", orphans: List[str]) -> bool:
        """Warn before leaving a feature behind that a loft may still use."""
        listed = ", ".join(orphans)
        also = ""
        derived = [c.feature for c in record.live_curves() if c.is_derived]
        if derived:
            also = f"\n\n{', '.join(derived)} is built on it and will go into error too."
        return messagebox.askokcancel(
            "A curve would be left behind",
            f"These settings no longer produce {listed}.\n\n"
            "Anything built on it will stop updating. The app will leave it in the "
            f"part rather than delete it.{also}\n\nGo ahead?",
            parent=self,
        )

    @staticmethod
    def _joined_name(record) -> str:
        if record is None:
            return ""
        for curve_record in record.curves:
            if curve_record.role == ROLE_JOINED:
                return curve_record.feature
        return ""

    def _remember(self, record, curves, spec, stem, folder, index, hashes,
                  pushed: bool, joined: str = "", failed: Collection[str] = (),
                  retire: Collection[str] = ()) -> None:
        """Replace the record with what this export made of it.

        ``failed`` names the curves SolidWorks would not take, which keep the
        last push that worked; ``retire`` names the ones this export no longer
        makes, which stay in the part and are marked so.
        """
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
            curve_record.last_pushed = store.push_stamp(
                curve_record.feature, stamp, pushed, failed, record
            )

        # The joined curve holds no points and has no file of its own: it is a
        # feature SolidWorks derives from two of ours. It is recorded so the
        # panel knows it is ours rather than a stray.
        if joined and joinable(curves):
            fresh.curves.append(
                store.CurveRecord(
                    role=ROLE_JOINED, feature=joined, file="",
                    last_written=stamp,
                    last_pushed=store.push_stamp(joined, stamp, pushed, failed, record),
                )
            )
        if record is not None:
            fresh.created = record.created or stamp
            known = {c.feature for c in fresh.curves}
            for c in record.curves:
                if c.feature in known:
                    continue
                if c.feature in retire:
                    # Retired on a copy, not on the record: had the push
                    # failed, the record would still have to say it is live.
                    fresh.curves.append(dataclasses.replace(c, retired=True))
                elif c.retired:
                    # Still in the part, so still this rib's.
                    fresh.curves.append(c)
            self._sidecar.exports[self._sidecar.exports.index(record)] = fresh
        else:
            self._sidecar.exports.append(fresh)

        # A record made with nowhere to write it is kept aside as well, so that
        # saving the part later — or coming back to the document after looking
        # at another one — still finds it.
        self._homeless = [r for r in self._homeless if r.export_id != fresh.export_id]
        if not self._sidecar_writable():
            self._homeless.append(fresh)

        self._editing = fresh.export_id
        self._describe_editing()
        self._save_sidecar()
        self._fill_tree()
        self._select_editing()

    def _section_as_dict(self) -> Optional[Dict[str, Any]]:
        if self.section is None or self.plane_mode.get() != MODE_LOADED:
            return None
        return section_to_dict(self.section)


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


def _work_area_height(root: tk.Tk) -> int:
    """The screen minus the taskbar, which is what a window can actually use."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        try:
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0  # SPI_GETWORKAREA
            ):
                return rect.bottom - rect.top
        except (AttributeError, OSError):
            pass
    return root.winfo_screenheight()


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


def build_window(root: tk.Tk, scale: float) -> "Tuple[ConverterApp, wing_tab.WingTab]":
    """Two tabs in one strip: the converter, and the wing built from its ribs.

    The wing tab is a page of the converter's strip rather than a window of
    its own, and it borrows the converter's link and record of the part.
    """
    app = ConverterApp(root, scale=scale)
    wing_page = wing_tab.WingTab(app.page_holder, app)
    app._wing_tab = wing_page
    app.add_page("Wing", wing_page, shown=wing_page.refresh)

    def show_wing(wing_id: str) -> None:
        app.show_tab("Wing")
        wing_page.open_wing(wing_id)

    app._show_wing = show_wing
    return app, wing_page


def main() -> None:
    _declare_dpi_aware()
    # Windows will not use a font file it has not been told about, and Tk asks
    # it for its families the moment a window exists, so this goes first.
    theme.register_bundled_fonts()
    root = tk.Tk()
    root.title("Airfoil Converter")
    scale = _scale_to_dpi(root)
    root.configure(background=theme.WINDOW)
    root.minsize(int((STRIP_WIDTH + 24) * scale), int(480 * scale))
    app, _wing = build_window(root, scale)

    # The strip can be taller than the screen. Open at the height the screen
    # has rather than at the height the form wants, or the footer — the status
    # line and Export — opens underneath the taskbar. Twice, because the first
    # pass is what tells the page how tall its contents are and the second is
    # what carries that up to the window.
    root.update_idletasks()
    app._size_page()
    root.update_idletasks()
    room = _work_area_height(root) - int(round(40 * scale))
    root.geometry(f"{root.winfo_reqwidth()}x{min(root.winfo_reqheight(), room)}")
    root.mainloop()


if __name__ == "__main__":
    main()
