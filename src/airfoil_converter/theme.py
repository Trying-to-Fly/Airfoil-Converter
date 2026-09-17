"""Every colour, size and face the window uses, in one place.

This is §3 and §4 of ``design/UI-DESIGN.md`` as code. Nothing here draws
anything; :mod:`widgets` and :mod:`gui` ask it for a colour, a font or a pixel
count and it answers the same way every time, so a change of mind about the
grey of a border is one edit rather than forty.

Two things Tk cannot do are worth saying outright rather than leaving as a
puzzle for whoever compares the window with ``design/renders``:

* **No corner radius.** The design's 2 px radius is not expressible on a Tk
  widget, so every rectangle here is square. At 2 px the difference is a
  pixel at each corner.
* **No letter-spacing, and only two weights.** Panel headers are uppercase and
  bold rather than uppercase, semibold and tracked out; 500 and 600 both land
  on Tk's ``bold`` or on ``normal``, whichever is nearer the design's intent.
"""

from __future__ import annotations

import atexit
import os
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import Optional

# ------------------------------------------------------------------ colours

WINDOW = "#f0f0f0"          # the ground both the strip and the flyout sit on
WHITE = "#ffffff"           # panel body, fields, the selected segment
HEADER_BG = "#f5f5f5"       # panel header, tree header
SECONDARY_BG = "#f8f8f8"    # secondary button, flyout ground
TRACKER_BG = "#fafafa"      # the pick tracker's own ground
DISABLED_BG = "#f4f4f4"
TRACK = "#ebebeb"           # segmented-control track
ROW_SELECTED = "#e9e9e9"
RULE = "#e3e3e3"            # header rule, disabled border
BORDER_SOFT = "#d8d8d8"     # panel border, tree border
BORDER = "#c6c6c6"          # control border
TRACKER_BORDER = "#cfcfcf"
GLYPH_OFF = "#b5b5b5"       # a glyph or a segment that cannot be chosen
FAINT = "#9c9c9c"           # disabled labels, placeholders
READOUT = "#8f8f8f"         # the readout in a panel header
MUTED = "#6e6e6e"           # hints
HEADER_TEXT = "#505050"
LABEL = "#3c3c3c"
INK = "#1f1f1f"
ACCENT = "#2b2b2b"          # checked boxes, primary button, the active step
OK = "#1a7f37"
ERROR = "#b42318"
UNTRACKED = "#777777"

# --------------------------------------------------------------------- type

UI_FAMILIES = ("Libre Franklin", "Segoe UI")
MONO_FAMILIES = ("JetBrains Mono", "Consolas")

# The bundled faces, if a build carries them. Both are OFL; see §4.
FONT_FILES = (
    "LibreFranklin-Regular.ttf",
    "LibreFranklin-Medium.ttf",
    "LibreFranklin-SemiBold.ttf",
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Medium.ttf",
)


def font_dir() -> str:
    """Where the bundled faces live, whether run from source or from one exe."""
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled:
        return os.path.join(bundled, "assets", "fonts")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "assets", "fonts")


def register_bundled_fonts() -> int:
    """Make the bundled faces visible to this process only, before Tk starts.

    Windows will not use a font file it has not been told about, and installing
    one system-wide needs a right the app does not have and should not want. A
    private registration lasts for the life of the process and is undone on the
    way out. Returns the number of files taken.

    A build without the files — which is every build until someone drops them
    in — silently gets Segoe UI and Consolas instead, which the design says it
    must survive.
    """
    if sys.platform != "win32":
        return 0
    folder = font_dir()
    if not os.path.isdir(folder):
        return 0

    import ctypes

    fr_private = 0x10
    taken = []
    for name in FONT_FILES:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            if ctypes.windll.gdi32.AddFontResourceExW(path, fr_private, 0):
                taken.append(path)
        except (AttributeError, OSError):
            return len(taken)

    if taken:
        def drop() -> None:
            for path in taken:
                try:
                    ctypes.windll.gdi32.RemoveFontResourceExW(path, fr_private, 0)
                except (AttributeError, OSError):
                    pass

        atexit.register(drop)
    return len(taken)


def _first_available(root: tk.Misc, wanted, fallback: str) -> str:
    families = {name.lower() for name in tkfont.families(root)}
    for name in wanted:
        if name.lower() in families:
            return name
    return fallback


class Fonts:
    """The seven or eight fonts the window actually uses, named for their job.

    Sizes are given in pixels, negative in Tk's spelling, because the design is
    in pixels and because a pixel size is the only one that can be grown by the
    screen's scale rather than twice — once by Tk's own scaling and again here.
    """

    def __init__(self, root: tk.Misc, scale: float = 1.0) -> None:
        self.scale = scale
        sans = _first_available(root, UI_FAMILIES, "TkDefaultFont")
        mono = _first_available(root, MONO_FAMILIES, "TkFixedFont")
        self.sans_family = sans
        self.mono_family = mono

        def sized(family: str, px: float, bold: bool = False) -> tkfont.Font:
            return tkfont.Font(
                root=root,
                family=family,
                size=-max(1, int(round(px * scale))),
                weight="bold" if bold else "normal",
            )

        self.body = sized(sans, 13)
        self.body_bold = sized(sans, 13, bold=True)
        self.label = sized(sans, 12.5)
        self.label_bold = sized(sans, 12.5, bold=True)
        self.hint = sized(sans, 12)
        self.hint_bold = sized(sans, 12, bold=True)
        self.readout = sized(sans, 11.5)
        self.header = sized(sans, 11.5, bold=True)
        self.tiny = sized(sans, 11)

        self.mono = sized(mono, 12.5)
        self.mono_bold = sized(mono, 12.5, bold=True)
        self.mono_small = sized(mono, 12)
        self.mono_tiny = sized(mono, 11.5)
        self.mono_tiny_bold = sized(mono, 11.5, bold=True)
        self.mono_micro = sized(mono, 10)


_fonts: Optional[Fonts] = None
_scale = 1.0


def fonts() -> Fonts:
    if _fonts is None:  # pragma: no cover - a programming error, not a state
        raise RuntimeError("theme.setup() has not been called yet")
    return _fonts


def px(pixels: float) -> int:
    """A design pixel, grown to the screen this window is on."""
    return int(round(pixels * _scale))


def setup(root: tk.Misc, scale: float = 1.0) -> Fonts:
    """Build the fonts and teach ttk the few widgets that stay ttk widgets.

    Most controls here are drawn by hand in :mod:`widgets`, because the design
    asks for colours and borders that a themed widget will not give up. What is
    left to ttk is what would be a great deal of work to replace and is close
    enough once restyled: the tree, its scrollbar, and the dropdowns.
    """
    global _fonts, _scale
    _scale = scale
    _fonts = Fonts(root, scale)
    f = _fonts

    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # the one theme that lets its colours be set
    except tk.TclError:  # pragma: no cover - a Tk without clam
        pass

    style.configure("TFrame", background=WINDOW)
    style.configure("TLabel", background=WINDOW, foreground=INK, font=f.body)

    # Dropdowns: a text field with a chevron, per §5. The look goes on
    # TCombobox itself so that the two named styles below — which differ only
    # in their face — inherit it; a style inherits from its own suffix, not
    # from another named style.
    style.configure(
        "TCombobox",
        foreground=INK,
        fieldbackground=WHITE,
        background=WHITE,
        bordercolor=BORDER,
        lightcolor=WHITE,
        darkcolor=WHITE,
        arrowcolor=MUTED,
        arrowsize=px(12),
        padding=(px(6), px(2), px(4), px(2)),
        relief="flat",
    )
    style.map(
        "TCombobox",
        fieldbackground=[("disabled", DISABLED_BG), ("readonly", WHITE)],
        foreground=[("disabled", FAINT)],
        bordercolor=[("disabled", RULE), ("focus", ACCENT)],
        arrowcolor=[("disabled", GLYPH_OFF)],
        background=[("disabled", DISABLED_BG), ("readonly", WHITE)],
        selectbackground=[("readonly", WHITE)],
        selectforeground=[("readonly", INK)],
    )
    style.configure("Af.TCombobox", font=f.label)
    style.configure("Mono.TCombobox", font=f.mono)
    # The popup list is a Tk widget, not a themed one, so it is set here.
    root.option_add("*TCombobox*Listbox.background", WHITE)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", WHITE)
    root.option_add("*TCombobox*Listbox.font", f.mono)

    # The curve tree in the flyout.
    style.configure(
        "Af.Treeview",
        background=WHITE,
        fieldbackground=WHITE,
        foreground=INK,
        bordercolor=BORDER_SOFT,
        lightcolor=WHITE,
        darkcolor=WHITE,
        borderwidth=0,
        rowheight=px(20),
        font=f.mono_small,
    )
    style.map(
        "Af.Treeview",
        background=[("selected", ROW_SELECTED)],
        foreground=[("selected", INK)],
    )
    style.configure(
        "Af.Treeview.Heading",
        background=HEADER_BG,
        foreground=MUTED,
        font=f.tiny,
        relief="flat",
        padding=(px(6), px(3)),
        borderwidth=0,
    )
    style.map("Af.Treeview.Heading", background=[("active", HEADER_BG)])
    style.layout("Af.Treeview.Item", _tree_item_layout(style))

    style.configure(
        "Af.Vertical.TScrollbar",
        background=SECONDARY_BG,
        troughcolor=HEADER_BG,
        bordercolor=RULE,
        arrowcolor=MUTED,
        lightcolor=SECONDARY_BG,
        darkcolor=SECONDARY_BG,
        relief="flat",
        width=px(12),
    )
    return f


def _tree_item_layout(style: ttk.Style):
    """The tree's own rows, with the focus ring taken off.

    A dotted rectangle around the focused row is Tk's own idea and belongs to
    no design here; the selected row is already a grey band.
    """
    try:
        layout = style.layout("Treeview.Item")
    except tk.TclError:  # pragma: no cover
        return []

    def strip(elements):
        out = []
        for name, options in elements:
            if name.endswith("focus"):
                out.extend(strip(options.get("children", [])))
                continue
            options = dict(options)
            if "children" in options:
                options["children"] = strip(options["children"])
            out.append((name, options))
        return out

    return strip(layout)
