"""The controls §5 of the design asks for, drawn by hand.

Every one of these exists because a themed Tk widget would not take the colour,
the border or the height the design gives it. They are small on purpose: a
frame, a label, and where a shape is needed, a canvas. None of them knows
anything about airfoils — they take a variable and a callback, and that is all.

Three of them — :class:`Check`, :class:`Button` and :class:`Segmented` — answer
``configure(state=...)`` the way a ttk widget does, so the handlers in
:mod:`gui` that enable and disable controls read exactly as they did before.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import theme
from .theme import px

# ------------------------------------------------------------------- pieces


class Rule(tk.Frame):
    """A one-pixel line across whatever it is put in."""

    def __init__(self, parent: tk.Misc, color: str = theme.RULE, **kwargs) -> None:
        super().__init__(parent, height=max(1, px(1)), bg=color, bd=0,
                         highlightthickness=0, **kwargs)


class Box(tk.Frame):
    """A white, bordered rectangle: a panel body, a card, the tracker."""

    def __init__(self, parent: tk.Misc, bg: str = theme.WHITE,
                 border: str = theme.BORDER_SOFT, **kwargs) -> None:
        super().__init__(
            parent, bg=bg, bd=0, highlightthickness=max(1, px(1)),
            highlightbackground=border, highlightcolor=border, **kwargs
        )


# -------------------------------------------------------------------- icons
#
# Stroke drawings on a 16 px grid, per §5: no emoji, no glyph font, nothing
# that depends on a character being present on the machine.

def _scaled(points: Sequence[Tuple[float, float]], size: int) -> List[float]:
    out: List[float] = []
    for x, y in points:
        out.extend((x * size / 16.0, y * size / 16.0))
    return out


def draw_icon(canvas: tk.Canvas, name: str, color: str, size: int) -> None:
    """Draw one 16-grid icon at the canvas's own size, in ``color``."""
    canvas.delete("icon")
    width = max(1, int(round(1.5 * size / 16.0)))
    common = dict(fill=color, width=width, capstyle="round", joinstyle="round", tags="icon")

    def line(points, smooth=False):
        canvas.create_line(*_scaled(points, size), smooth=smooth, **common)

    def oval(x0, y0, x1, y1, **kw):
        canvas.create_oval(*_scaled(((x0, y0), (x1, y1)), size),
                           outline=color, width=width, tags="icon", **kw)

    if name == "folder":
        line([(2, 12.6), (2, 4.4), (6.4, 4.4), (7.9, 6.3), (13.8, 6.3),
              (13.8, 12.6), (2, 12.6)])
    elif name == "cursor":
        line([(3, 2.4), (12.6, 8), (8.4, 9.1), (6.2, 13.2), (3, 2.4)])
    elif name == "sidebar":
        line([(2.4, 3), (13.6, 3), (13.6, 13), (2.4, 13), (2.4, 3)])
        line([(10, 3), (10, 13)])
    elif name == "info":
        oval(2, 2, 14, 14)
        line([(8, 7.4), (8, 11.2)])
        line([(8, 4.9), (8, 5.1)])
    elif name == "tick":
        line([(3.4, 8.4), (6.5, 11.5), (12.6, 4.6)])
    elif name == "chevron":
        line([(4, 6.2), (8, 10.2), (12, 6.2)])
    elif name == "refresh":
        canvas.create_arc(
            *_scaled(((2.6, 2.6), (13.4, 13.4)), size), start=40, extent=280,
            style="arc", outline=color, width=width, tags="icon",
        )
        line([(12.4, 2.4), (12.9, 6.2), (9.2, 5.4)])
    elif name == "plus":
        line([(8, 3.2), (8, 12.8)])
        line([(3.2, 8), (12.8, 8)])
    elif name == "link":
        # Two half-rings and the bar between them: a chain link, for taking
        # hold of curves the app did not put in the part itself.
        for box, start in ((((2.2, 4.6), (9.0, 11.4)), 90), (((7.0, 4.6), (13.8, 11.4)), 270)):
            canvas.create_arc(*_scaled(box, size), start=start, extent=180,
                              style="arc", outline=color, width=width, tags="icon")
        line([(5.6, 8), (10.4, 8)])


class Icon(tk.Canvas):
    def __init__(self, parent: tk.Misc, name: str, color: str = theme.INK,
                 size: int = 14, bg: str = theme.WHITE) -> None:
        side = px(size)
        super().__init__(parent, width=side, height=side, bg=bg,
                         highlightthickness=0, bd=0)
        self._icon_name = name
        self._size = side
        self.set_color(color)

    def set_color(self, color: str) -> None:
        draw_icon(self, self._icon_name, color, self._size)

    def set_background(self, color: str) -> None:
        self.configure(bg=color)


class Dot(tk.Canvas):
    """The 7–8 px disc that carries a state colour in the header and the footer."""

    def __init__(self, parent: tk.Misc, color: str, size: int = 7,
                 bg: str = theme.WHITE) -> None:
        side = px(size)
        super().__init__(parent, width=side, height=side, bg=bg,
                         highlightthickness=0, bd=0)
        self._side = side
        self._id = self.create_oval(0, 0, side - 1, side - 1, outline="", fill=color)

    def set_color(self, color: str) -> None:
        self.itemconfigure(self._id, fill=color)


# ------------------------------------------------------------------- panels


class Panel(tk.Frame):
    """A titled box with a readout in its header, per §5.

    The readout is the panel's state in one short line — it is what lets the
    strip stay this dense without the user having to read the fields back to
    find out what the panel is set to.
    """

    def __init__(self, parent: tk.Misc, title: str,
                 readout: Optional[tk.StringVar] = None,
                 header_pad: int = 12) -> None:
        super().__init__(parent, bg=theme.BORDER_SOFT, bd=0,
                         highlightthickness=0)
        f = theme.fonts()
        border = max(1, px(1))
        inner = tk.Frame(self, bg=theme.WHITE, bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=border, pady=border)
        inner.columnconfigure(0, weight=1)

        header = tk.Frame(inner, bg=theme.HEADER_BG, height=px(26), bd=0,
                          highlightthickness=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(1, weight=1)
        tk.Label(
            header, text=title.upper(), bg=theme.HEADER_BG,
            fg=theme.HEADER_TEXT, font=f.header,
        ).grid(row=0, column=0, sticky="w", padx=(px(header_pad), 0), pady=px(5))
        self.header = header

        self.readout_label = tk.Label(
            header, textvariable=readout, text="" if readout else "",
            bg=theme.HEADER_BG, fg=theme.READOUT, font=f.readout, anchor="e",
        )
        self.readout_label.grid(row=0, column=1, sticky="e", padx=(px(8), px(header_pad)))

        Rule(inner).grid(row=1, column=0, sticky="ew")

        self.body = tk.Frame(inner, bg=theme.WHITE, bd=0, highlightthickness=0)
        self.body.grid(row=2, column=0, sticky="nsew", padx=px(12), pady=px(8))
        inner.rowconfigure(2, weight=1)

    def header_slot(self) -> tk.Frame:
        """Room at the right of the header for a button, ahead of the readout."""
        slot = tk.Frame(self.header, bg=theme.HEADER_BG)
        slot.grid(row=0, column=2, sticky="e", padx=(px(8), px(12)), pady=px(2))
        return slot


# ------------------------------------------------------------------- fields


class Field(tk.Frame):
    """A 26 px text field: mono text, an optional unit, an optional placeholder.

    The placeholder is a label sitting on top of an empty field rather than
    text put into the variable. The form is read straight out of these
    variables by :meth:`gui.ConverterApp._spec`, and a placeholder that could
    be read back as a value would be a silent wrong number.
    """

    def __init__(self, parent: tk.Misc, variable: tk.StringVar, width: int = 84,
                 unit: str = "", placeholder: Optional[str] = None, prefix: str = "",
                 mono: bool = True, grow: bool = False, height: int = 26) -> None:
        super().__init__(parent, bg=theme.WHITE, bd=0,
                         highlightthickness=max(1, px(1)),
                         highlightbackground=theme.BORDER,
                         highlightcolor=theme.BORDER,
                         height=px(height))
        f = theme.fonts()
        self.variable = variable
        self._enabled = True
        self._marked = False
        self._grow = grow
        if not grow:
            self.configure(width=px(width))
        self.grid_propagate(False)
        self.pack_propagate(False)

        self._prefix_label = None
        if prefix:
            self._prefix_label = tk.Label(self, text=prefix, bg=theme.WHITE,
                                          fg=theme.READOUT, font=f.tiny)
            self._prefix_label.pack(side="left", padx=(px(8), 0))

        self._unit_label = None
        if unit:
            self._unit_label = tk.Label(self, text=unit, bg=theme.WHITE,
                                        fg=theme.READOUT, font=f.readout)
            self._unit_label.pack(side="right", padx=(0, px(8)))

        self.entry = tk.Entry(
            self, textvariable=variable, bd=0, highlightthickness=0,
            relief="flat", bg=theme.WHITE, fg=theme.INK,
            disabledbackground=theme.DISABLED_BG, disabledforeground=theme.FAINT,
            readonlybackground=theme.DISABLED_BG,
            insertbackground=theme.INK, font=f.mono if mono else f.label,
            width=1, justify="left",
        )
        left = 0 if prefix else px(8)
        right = 0 if unit else px(8)
        self.entry.pack(side="left", fill="both", expand=True, padx=(left, right))

        # A field that will only know its placeholder later — Target chord
        # shows the source chord, which arrives with the file — is built with
        # the label already there and nothing in it.
        self._placeholder = None
        if placeholder is not None:
            self._placeholder = tk.Label(
                self, text=placeholder, bg=theme.WHITE, fg=theme.FAINT,
                font=f.mono if mono else f.label, anchor="w",
            )
            self._placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())
            variable.trace_add("write", lambda *_: self._show_placeholder())
            self.entry.bind("<FocusIn>", self._on_focus, add="+")
            self.entry.bind("<FocusOut>", self._on_blur, add="+")
            self.after_idle(self._show_placeholder)

        self.entry.bind("<FocusIn>", lambda _e: self._paint_border(), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self._paint_border(), add="+")

        # What a disabled field says instead of its value: a straight edge on
        # the Wing tab has no file, and a blank grey field reads as one
        # somebody forgot. Sans, because it is a sentence and not a path.
        self._note = tk.Label(self, text="", bg=theme.DISABLED_BG, fg=theme.FAINT,
                              font=f.hint, anchor="w")

    # -- placeholder

    def set_placeholder(self, text: str) -> None:
        if self._placeholder is None:
            return
        self._placeholder.configure(text=text)
        if text:
            self._show_placeholder()
        else:
            self._placeholder.place_forget()

    def _show_placeholder(self) -> None:
        if self._placeholder is None or not self._placeholder.cget("text"):
            return
        empty = not self.variable.get() and self.focus_get() is not self.entry
        if empty and self._enabled:
            self._placeholder.place(in_=self.entry, x=0, rely=0.5, anchor="w")
        else:
            self._placeholder.place_forget()

    def _on_focus(self, _event: tk.Event) -> None:
        if self._placeholder is not None:
            self._placeholder.place_forget()

    def _on_blur(self, _event: tk.Event) -> None:
        self._show_placeholder()

    # -- state

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        bg = theme.WHITE if enabled else theme.DISABLED_BG
        fg = theme.READOUT if enabled else theme.FAINT
        self.configure(bg=bg)
        self.entry.configure(state="normal" if enabled else "disabled", bg=bg)
        for label in (self._prefix_label, self._unit_label):
            if label is not None:
                label.configure(bg=bg, fg=fg)
        if self._placeholder is not None:
            self._placeholder.configure(bg=bg)
            self._show_placeholder()
        self._show_note()
        self._paint_border()

    def set_note(self, text: str) -> None:
        """Say ``text`` over the field while it is disabled; ``""`` says nothing."""
        self._note.configure(text=text)
        self._show_note()

    def _show_note(self) -> None:
        if self._note.cget("text") and not self._enabled:
            self._note.place(in_=self.entry, x=0, y=0, relwidth=1.0, relheight=1.0)
        else:
            self._note.place_forget()

    def set_marked(self, marked: bool) -> None:
        """Mark a field as filled by a pick, per §6: an ink border, not a tint."""
        self._marked = marked
        self._paint_border()

    def _paint_border(self) -> None:
        if not self._enabled:
            color = theme.RULE
        elif self.focus_get() is self.entry or self._marked:
            color = theme.ACCENT
        else:
            color = theme.BORDER
        self.configure(highlightbackground=color, highlightcolor=color)


# ------------------------------------------------------------- segmented row


class Segmented(tk.Frame):
    """A row of segments over one variable — every radio row in the old window.

    Which segments exist is fixed; which can be *chosen* is not (``Loaded``
    only means something once a curve file is open), so a segment can be turned
    off on its own without leaving the row.
    """

    def __init__(self, parent: tk.Misc, variable: tk.StringVar,
                 values: Sequence[str], labels: Optional[Dict[str, str]] = None,
                 command: Optional[Callable[[], None]] = None, mono: bool = False,
                 height: int = 22, width: Optional[int] = None,
                 bg: str = theme.WHITE) -> None:
        super().__init__(parent, bg=theme.TRACK, bd=0,
                         highlightthickness=max(1, px(1)),
                         highlightbackground=theme.BORDER_SOFT,
                         highlightcolor=theme.BORDER_SOFT)
        f = theme.fonts()
        self.variable = variable
        self.command = command
        self._values = tuple(values)
        self._font = f.mono_small if mono else f.hint
        self._enabled = True
        self._off: set = set()
        self._cells: Dict[str, Tuple[tk.Frame, tk.Label]] = {}
        if width:
            # Fixing one axis in Tk fixes both, so the height the segments
            # would have taken has to be given back to it explicitly.
            border = max(1, px(1))
            self.configure(width=px(width),
                           height=px(height) + 2 * px(2) + 2 * border)
            self.grid_propagate(False)

        pad = px(2)
        for column, value in enumerate(self._values):
            self.columnconfigure(column, weight=1, uniform="seg")
            cell = tk.Frame(self, bg=theme.TRACK, bd=0, height=px(height),
                            highlightthickness=max(1, px(1)),
                            highlightbackground=theme.TRACK,
                            highlightcolor=theme.TRACK)
            cell.grid(row=0, column=column, sticky="nsew",
                      padx=(pad, 0) if column == 0 else (pad, 0),
                      pady=pad)
            if column == len(self._values) - 1:
                cell.grid_configure(padx=(pad, pad))
            cell.grid_propagate(False)
            cell.columnconfigure(0, weight=1)
            cell.rowconfigure(0, weight=1)
            text = (labels or {}).get(value, value)
            label = tk.Label(cell, text=text, bg=theme.TRACK,
                             fg=theme.HEADER_TEXT, font=self._font)
            label.grid(row=0, column=0, sticky="nsew")
            for widget in (cell, label):
                widget.bind("<Button-1>", lambda _e, v=value: self._choose(v))
            self._cells[value] = (cell, label)

        variable.trace_add("write", lambda *_: self._paint())
        self._paint()

    def _choose(self, value: str) -> None:
        if not self._enabled or value in self._off or self.variable.get() == value:
            return
        self.variable.set(value)
        if self.command is not None:
            self.command()

    def _paint(self) -> None:
        f = theme.fonts()
        current = self.variable.get()
        for value, (cell, label) in self._cells.items():
            chosen = value == current
            if not self._enabled:
                bg, fg, ring = theme.DISABLED_BG, theme.FAINT, theme.DISABLED_BG
                if chosen:
                    ring = theme.RULE
            elif chosen:
                bg, fg, ring = theme.WHITE, theme.INK, theme.BORDER
            elif value in self._off:
                bg, fg, ring = theme.TRACK, theme.GLYPH_OFF, theme.TRACK
            else:
                bg, fg, ring = theme.TRACK, theme.HEADER_TEXT, theme.TRACK
            cell.configure(bg=bg, highlightbackground=ring, highlightcolor=ring)
            bold = self._font is f.mono_small
            label.configure(
                bg=bg, fg=fg,
                font=(f.mono_bold if bold else f.label_bold) if chosen else self._font,
            )
        self.configure(bg=theme.DISABLED_BG if not self._enabled else theme.TRACK)

    def set_option_enabled(self, value: str, enabled: bool) -> None:
        if enabled:
            self._off.discard(value)
        else:
            self._off.add(value)
        self._paint()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._paint()

    def configure(self, cnf=None, **kwargs):  # noqa: D401 - matches ttk's spelling
        state = kwargs.pop("state", None)
        if state is not None:
            self.set_enabled(state != "disabled")
        return super().configure(cnf, **kwargs)

    config = configure


# --------------------------------------------------------------- check, box


class Check(tk.Frame):
    """A 15 px box and a label, per §5. Toggles from either."""

    def __init__(self, parent: tk.Misc, variable: tk.BooleanVar, text: str,
                 command: Optional[Callable[[], None]] = None,
                 bg: str = theme.WHITE) -> None:
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        f = theme.fonts()
        self.variable = variable
        self.command = command
        self._enabled = True
        self._bg = bg
        side = px(15)
        self.canvas = tk.Canvas(self, width=side, height=side, bg=bg,
                                highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0)
        self.label = tk.Label(self, text=text, bg=bg, fg=theme.LABEL, font=f.label)
        self.label.grid(row=0, column=1, padx=(px(7), 0))
        for widget in (self.canvas, self.label):
            widget.bind("<Button-1>", lambda _e: self._toggle())
        # The variable can outlive the box — the Wing tab rebuilds its rib
        # rows over the same ticks — so the box stops listening when it goes.
        self._trace = variable.trace_add("write", lambda *_: self._paint())
        self.bind("<Destroy>", self._forget_variable, add="+")
        self._paint()

    def _forget_variable(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        try:
            self.variable.trace_remove("write", self._trace)
        except tk.TclError:
            pass

    def _toggle(self) -> None:
        if not self._enabled:
            return
        self.variable.set(not self.variable.get())
        if self.command is not None:
            self.command()

    def _paint(self) -> None:
        side = px(15)
        c = self.canvas
        c.delete("all")
        checked = bool(self.variable.get())
        if not self._enabled:
            fill, outline = theme.DISABLED_BG, theme.BORDER_SOFT
        elif checked:
            fill, outline = theme.ACCENT, theme.ACCENT
        else:
            fill, outline = theme.WHITE, theme.READOUT
        c.create_rectangle(0, 0, side - 1, side - 1, fill=fill, outline=outline,
                           width=max(1, px(1)))
        if checked:
            width = max(1, int(round(2.2 * side / 16.0)))
            c.create_line(
                *_scaled(((3.6, 8.2), (6.4, 11.2), (12.2, 4.8)), side),
                fill=theme.WHITE if self._enabled else theme.FAINT,
                width=width, capstyle="round", joinstyle="round",
            )
        self.label.configure(fg=theme.LABEL if self._enabled else theme.FAINT)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._paint()

    def set_background(self, color: str) -> None:
        self._bg = color
        for widget in (self, self.canvas, self.label):
            widget.configure(bg=color)
        self._paint()

    def configure(self, cnf=None, **kwargs):
        state = kwargs.pop("state", None)
        if state is not None:
            self.set_enabled(state != "disabled")
        return super().configure(cnf, **kwargs)

    config = configure


# ------------------------------------------------------------------ buttons


class Button(tk.Frame):
    """A secondary or primary button, with an optional 14 px stroke icon.

    It answers ``configure(state=...)``, because the window enables and
    disables buttons from several handlers that predate it.
    """

    def __init__(self, parent: tk.Misc, text: str,
                 command: Optional[Callable[[], None]] = None, icon: str = "",
                 primary: bool = False, height: int = 26,
                 bg: str = theme.WHITE) -> None:
        super().__init__(parent, bd=0, highlightthickness=max(1, px(1)))
        f = theme.fonts()
        self.command = command
        self._primary = primary
        self._enabled = True
        self._hover = False
        # A button is as wide as its text and as tall as the design says, and
        # Tk will not fix one axis without the other — so the height is made
        # out of padding rather than taken away from the geometry manager.
        font = f.label_bold if primary else f.label
        pad = px(18 if primary else 10)
        tall = max(0, (px(height) - font.metrics("linespace")) // 2)
        self._icon = Icon(self, icon, theme.INK, 14, theme.WHITE) if icon else None
        if self._icon is not None:
            self._icon.pack(side="left", padx=(pad, px(6)), pady=tall)
        self.label = tk.Label(self, text=text, font=font)
        self.label.pack(side="left", padx=(0 if self._icon is not None else pad, pad),
                        pady=tall)

        for widget in (self, self.label) + ((self._icon,) if self._icon else ()):
            widget.bind("<Button-1>", self._press)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)
        self._paint()

    def _press(self, _event: tk.Event) -> None:
        if self._enabled and self.command is not None:
            self.command()

    def _enter(self, _event: tk.Event) -> None:
        self._hover = True
        self._paint()

    def _leave(self, _event: tk.Event) -> None:
        self._hover = False
        self._paint()

    def set_text(self, text: str) -> None:
        self.label.configure(text=text)

    def set_icon(self, name: str) -> None:
        if self._icon is not None:
            self._icon._icon_name = name  # noqa: SLF001 - its own kind of widget
            self._icon.set_color(self.label.cget("fg"))

    def _paint(self) -> None:
        if self._primary:
            fill = theme.INK if self._hover and self._enabled else theme.ACCENT
            border, text = fill, theme.WHITE
            if not self._enabled:
                fill = border = theme.GLYPH_OFF
        elif not self._enabled:
            fill, border, text = theme.DISABLED_BG, theme.RULE, theme.FAINT
        else:
            fill = theme.HEADER_BG if self._hover else theme.SECONDARY_BG
            border, text = theme.BORDER, theme.INK
        self.configure(bg=fill, highlightbackground=border, highlightcolor=border)
        self.label.configure(bg=fill, fg=text)
        if self._icon is not None:
            self._icon.set_background(fill)
            self._icon.set_color(text)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._paint()

    def configure(self, cnf=None, **kwargs):
        state = kwargs.pop("state", None)
        if state is not None:
            self.set_enabled(state != "disabled")
        return super().configure(cnf, **kwargs)

    config = configure


# ------------------------------------------------------- the pick tracker's
#                                                          three glyphs


class StepGlyph(tk.Canvas):
    """Done, active, or still to come — the whole point of the tracker."""

    def __init__(self, parent: tk.Misc, bg: str = theme.TRACKER_BG,
                 size: int = 16) -> None:
        side = px(size)
        super().__init__(parent, width=side, height=side, bg=bg,
                         highlightthickness=0, bd=0)
        self._side = side
        self.set_state("pending")

    def set_state(self, state: str) -> None:
        side = self._side
        self.delete("all")
        inset = max(1, int(round(side * 0.06)))
        box = (inset, inset, side - inset - 1, side - inset - 1)
        if state == "done":
            self.create_oval(*box, fill=theme.OK, outline="")
            self.create_line(
                *_scaled(((4.2, 8.2), (6.9, 10.9), (11.8, 5.4)), side),
                fill=theme.WHITE, width=max(1, int(round(2.0 * side / 16.0))),
                capstyle="round", joinstyle="round",
            )
        elif state == "active":
            self.create_oval(*box, outline=theme.ACCENT,
                             width=max(1, int(round(2.0 * side / 16.0))))
            middle = side / 2.0
            radius = side * 0.16
            self.create_oval(middle - radius, middle - radius,
                             middle + radius, middle + radius,
                             fill=theme.ACCENT, outline="")
        else:
            self.create_oval(*box, outline=theme.GLYPH_OFF,
                             width=max(1, int(round(1.5 * side / 16.0))))


class Outline(tk.Canvas):
    """The loaded section, 110 × 14, so the file's name is not the only proof."""

    def __init__(self, parent: tk.Misc, width: int = 110, height: int = 14,
                 bg: str = theme.WHITE) -> None:
        super().__init__(parent, width=px(width), height=px(height), bg=bg,
                         highlightthickness=0, bd=0)
        self._width = px(width)
        self._height = px(height)

    def show(self, points: Optional[Iterable[Tuple[float, float]]]) -> None:
        self.delete("all")
        if not points:
            return
        pts = list(points)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        span_x = (max(xs) - min(xs)) or 1.0
        span_y = (max(ys) - min(ys)) or 1.0
        # The chord fills the width; the thickness is drawn to the same scale
        # unless it would not fit, which no aerofoil does.
        scale = min(self._width / span_x, self._height / span_y)
        left = (self._width - span_x * scale) / 2.0
        middle = self._height / 2.0
        flat: List[float] = []
        for x, y in pts:
            flat.append(left + (x - min(xs)) * scale)
            flat.append(middle - (y - (min(ys) + span_y / 2.0)) * scale)
        self.create_line(*flat, fill=theme.HEADER_TEXT, width=max(1, px(1)),
                         capstyle="round", joinstyle="round")


# ------------------------------------------------------------------ the tabs


class TabRow(tk.Frame):
    """``Airfoil · Wing`` above the panels: navigation, so not a segmented control.

    28 px, a rule along the bottom, the open tab in ink and bold with a 2 px
    accent underline, the others muted.
    """

    def __init__(self, parent: tk.Misc, command: Callable[[str], None],
                 bg: str = theme.WINDOW) -> None:
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0, height=px(28))
        self.pack_propagate(False)
        self.command = command
        self._bg = bg
        self._tabs: Dict[str, Tuple[tk.Label, tk.Frame]] = {}
        Rule(self, theme.BORDER_SOFT).pack(side="bottom", fill="x")
        self._bar = tk.Frame(self, bg=bg)
        self._bar.pack(side="top", fill="both", expand=True)
        self._current = ""

    def add(self, name: str) -> None:
        f = theme.fonts()
        cell = tk.Frame(self._bar, bg=self._bg)
        cell.pack(side="left", fill="y", padx=(px(18) if self._tabs else 0, 0))
        line = tk.Frame(cell, bg=self._bg, height=max(1, px(2)))
        line.pack(side="bottom", fill="x")
        label = tk.Label(cell, text=name, bg=self._bg, fg=theme.MUTED, font=f.label,
                         padx=px(2), cursor="hand2")
        label.pack(side="top", fill="both", expand=True)
        for widget in (cell, label):
            widget.bind("<Button-1>", lambda _e, n=name: self.command(n))
        self._tabs[name] = (label, line)
        self._paint()

    def select(self, name: str) -> None:
        self._current = name
        self._paint()

    def _paint(self) -> None:
        f = theme.fonts()
        for name, (label, line) in self._tabs.items():
            on = name == self._current
            label.configure(fg=theme.INK if on else theme.MUTED,
                            font=f.label_bold if on else f.label)
            line.configure(bg=theme.ACCENT if on else self._bg)


# ------------------------------------------------------- text in two faces


class RichText(tk.Text):
    """A wrapping line of runs, some of them mono: ``Export will make wing_le``.

    A Tk label has one font. A read-only text widget has as many as it has
    tags, so a sentence can set its curve names in mono the way the design
    does; it grows to as many lines as the words take at its width.

    ``styles`` maps a run's style to ``(font, colour)``; the ``""`` style is
    the sentence itself.
    """

    def __init__(self, parent: tk.Misc, styles: Dict[str, Tuple[object, str]],
                 bg: str = theme.WHITE,
                 on_resize: Optional[Callable[[], None]] = None) -> None:
        _, base_color = styles[""]
        # A line is as tall as the widget's own font, so that is the tallest
        # of the faces, or a line set in it would lose its descenders.
        base_font = max((font for font, _ in styles.values()),
                        key=lambda font: font.metrics("linespace"))
        # Called when the text wraps to a different number of lines, which
        # happens only once it has been laid out: a page that fixes its own
        # height has to be told.
        self.on_resize = on_resize
        super().__init__(
            parent, bd=0, highlightthickness=0, relief="flat", bg=bg,
            fg=base_color, font=base_font, wrap="word", width=1, height=1,
            padx=0, pady=0, cursor="arrow", takefocus=0,
            selectbackground=bg, selectforeground=base_color,
            inactiveselectbackground=bg, insertwidth=0,
            spacing1=0, spacing2=0, spacing3=0,
        )
        for style, (font, color) in styles.items():
            self.tag_configure(style or "plain", font=font, foreground=color)
        self._lines = 1
        self._runs: Tuple[Tuple[str, str], ...] = ()
        self.configure(state="disabled")
        self.bind("<Configure>", lambda _e: self._fit(), add="+")

    def set_runs(self, runs: Sequence[Tuple[str, str]]) -> None:
        runs = tuple((text, style) for text, style in runs)
        if runs == self._runs:
            return
        self._runs = runs
        self.configure(state="normal")
        self.delete("1.0", "end")
        for text, style in runs:
            self.insert("end", text, style or "plain")
        self.configure(state="disabled")
        self._fit()

    def set_color(self, color: str, style: str = "") -> None:
        self.tag_configure(style or "plain", foreground=color)

    def _fit(self) -> None:
        """As many lines as the text wraps to at the width it has been given."""
        try:
            counted = self.count("1.0", "end-1c", "displaylines")
        except tk.TclError:
            return
        if isinstance(counted, tuple):
            counted = counted[0] if counted else 0
        lines = max(1, int(counted or 0) + 1)
        if lines != self._lines:
            self._lines = lines
            self.configure(height=lines)
            if self.on_resize is not None:
                self.on_resize()


# ----------------------------------------------------------- the rib list


class CheckList(Box):
    """A list drawn like the flyout's tree, with a box on each row.

    A 22 px header row, then 22 px rows: the box, the name in mono, and a
    state column. A row without a variable cannot be ticked and says why in
    its state column, in faint.
    """

    STATE_WIDTH = 84

    def __init__(self, parent: tk.Misc, heading: str, state_heading: str,
                 empty: str = "") -> None:
        super().__init__(parent, bg=theme.WHITE, border=theme.BORDER_SOFT)
        f = theme.fonts()
        self.columnconfigure(0, weight=1)
        self._empty = empty
        header = tk.Frame(self, bg=theme.HEADER_BG, height=px(22))
        header.grid(row=0, column=0, sticky="ew")
        header.pack_propagate(False)
        # The heading stands over the names, past the box and its gap.
        tk.Label(header, text=heading.upper(), bg=theme.HEADER_BG, fg=theme.MUTED,
                 font=f.tiny).pack(side="left", padx=(px(8 + 15 + 7), 0))
        holder = tk.Frame(header, bg=theme.HEADER_BG, width=px(self.STATE_WIDTH))
        holder.pack(side="right", fill="y", padx=(0, px(8)))
        holder.pack_propagate(False)
        tk.Label(holder, text=state_heading.upper(), bg=theme.HEADER_BG,
                 fg=theme.MUTED, font=f.tiny, anchor="w").pack(side="left")
        self.rows = tk.Frame(self, bg=theme.WHITE)
        self.rows.grid(row=1, column=0, sticky="ew", pady=(0, px(2)))
        self._checks: List[Check] = []
        # What a row that cannot be ticked shows. One for the list's lifetime:
        # a Tk variable made per row would be left for the garbage collector,
        # which may run on a worker thread, where Tk must not be called.
        self._off = tk.BooleanVar(self, value=False)

    def set_rows(self, rows: Sequence[Tuple[str, str, Optional[tk.BooleanVar]]]) -> None:
        """``(name, state, variable)`` for each row; a ``None`` variable is a row
        that cannot be ticked."""
        f = theme.fonts()
        for child in self.rows.winfo_children():
            child.destroy()
        self._checks = []
        if not rows and self._empty:
            tk.Label(self.rows, text=self._empty, bg=theme.WHITE, fg=theme.FAINT,
                     font=f.hint, anchor="w", justify="left",
                     wraplength=px(400)).pack(fill="x", padx=px(8), pady=px(4))
            return
        for name, state, variable in rows:
            row = tk.Frame(self.rows, bg=theme.WHITE, height=px(22))
            row.pack(fill="x")
            row.pack_propagate(False)
            live = variable is not None
            check = Check(row, variable if live else self._off, name)
            check.label.configure(font=f.mono_small)
            if not live:
                check.set_enabled(False)
            else:
                self._checks.append(check)
            check.pack(side="left", padx=(px(8), 0))
            label = tk.Label(row, text=state, bg=theme.WHITE,
                             fg=theme.INK if live else theme.FAINT,
                             font=f.mono_tiny_bold if live else f.tiny, anchor="w")
            width = max(px(self.STATE_WIDTH), label.winfo_reqwidth())
            holder = tk.Frame(row, bg=theme.WHITE, width=width)
            holder.pack(side="right", fill="y", padx=(px(8), px(8)))
            holder.pack_propagate(False)
            label.pack(in_=holder, side="left")
            label.lift(holder)

    def set_enabled(self, enabled: bool) -> None:
        for check in self._checks:
            check.set_enabled(enabled)


# ---------------------------------------------------- the checked wing


def _fmt(value: float) -> str:
    return f"{round(value, 3):g}"


class Planform(tk.Canvas):
    """The wing from above: its outline, its ribs, and the offset wing's sections.

    Drawn from :func:`wing_build.planform`. The span is stretched to the
    width and the chord to the height, as a plot is; it is a picture of
    where things are along the span, not a scale drawing.
    """

    HEIGHT = 132

    def __init__(self, parent: tk.Misc, width: int = 412,
                 bg: str = theme.TRACKER_BG) -> None:
        super().__init__(parent, width=px(width), height=px(self.HEIGHT), bg=bg,
                         highlightthickness=0, bd=0)
        self._width = width

    def show(self, plan) -> None:
        f = theme.fonts()
        self.delete("all")
        if plan is None:
            return
        left, right = px(18), px(self._width - 18)
        top, bottom = px(20), px(self.HEIGHT - 20)
        span = (plan.end - plan.start) or 1.0

        def x(s: float) -> float:
            return left + (s - plan.start) / span * (right - left)

        across = [a for _, a in plan.le + plan.te]
        across += [v for _, a, b in plan.ribs + plan.sections for v in (a, b)]
        low, high = min(across), max(across)
        size = (high - low) or 1.0
        # The leading edge goes at the top, whichever way the chord runs.
        forward = sum(a for _, a in plan.te) >= sum(a for _, a in plan.le)

        def y(a: float) -> float:
            t = (a - low) / size if forward else (high - a) / size
            return top + t * (bottom - top)

        outline: List[float] = []
        for s, a in plan.le:
            outline += [x(s), y(a)]
        for s, a in reversed(plan.te):
            outline += [x(s), y(a)]
        self.create_polygon(*outline, fill=theme.HEADER_BG, outline=theme.HEADER_TEXT,
                            width=max(1.0, 1.2 * theme.px(1)), joinstyle="round")
        dash = (px(3) or 1, px(2) or 1)
        for s, a, b in plan.sections:
            self.create_line(x(s), y(a), x(s), y(b), fill=theme.READOUT, width=1, dash=dash)
        for s, a, b in plan.ribs:
            self.create_line(x(s), y(a), x(s), y(b), fill=theme.INK,
                             width=max(1.0, 1.4 * theme.px(1)))

        if plan.steep_from is not None and plan.start <= plan.steep_from < plan.end:
            x0, x1 = x(plan.steep_from), right
            level = px(14)
            color = theme.GLYPH_OFF
            self.create_line(x0, level - px(2), x0, level + px(2), fill=color)
            self.create_line(x0, level, x1, level, fill=color)
            self.create_line(x1, level - px(2), x1, level + px(2), fill=color)
            text = "solved in 3D"
            half = f.tiny.measure(text) / 2
            middle = min(max((x0 + x1) / 2, half + px(2)), px(self._width - 2) - half)
            self.create_text(middle, level - px(3), text=text, anchor="s",
                             fill=theme.MUTED, font=f.tiny)

        base = px(self.HEIGHT - 2)
        self.create_text(left, base, text="0", anchor="sw", fill=theme.MUTED,
                         font=f.mono_micro)
        self.create_text(right, base, text=f"{span:.0f} mm", anchor="se",
                         fill=theme.MUTED, font=f.mono_micro)
        mid = px(90)
        swatch = (base - px(9), base - px(2))
        self.create_line(mid, swatch[0], mid, swatch[1], fill=theme.INK,
                         width=max(1.0, 1.4 * theme.px(1)))
        gap = px(10)  # between a sample line and the word it stands for
        end = self.bbox(self.create_text(mid + gap, base, text="ribs", anchor="sw",
                                         fill=theme.MUTED, font=f.tiny))[2]
        if plan.sections:
            at = end + px(18)
            self.create_line(at, swatch[0], at, swatch[1], fill=theme.READOUT,
                             width=1, dash=dash)
            self.create_text(at + gap, base, text="offset sections", anchor="sw",
                             fill=theme.MUTED, font=f.tiny)


class WallMeter(tk.Canvas):
    """The wall as a range, against the distance asked for.

    The scale runs 10 % either side of the distance; the grey band is 5 %
    either side, and the bar is the wall from its thinnest to its thickest.
    A bar that leaves the band is drawn in the error colour.
    """

    WIDTH = 240
    HEIGHT = 30

    def __init__(self, parent: tk.Misc, bg: str = theme.TRACKER_BG) -> None:
        super().__init__(parent, width=px(self.WIDTH), height=px(self.HEIGHT), bg=bg,
                         highlightthickness=0, bd=0)

    def show(self, thinnest: float, thickest: float, asked: float) -> None:
        f = theme.fonts()
        self.delete("all")
        asked = abs(asked)
        if not asked:
            return
        low, high = asked * 0.9, asked * 1.1
        left, right = px(10), px(self.WIDTH - 10)

        def x(value: float) -> float:
            value = min(max(value, low), high)
            return left + (value - low) / (high - low) * (right - left)

        middle = px(11)
        self.create_rectangle(x(asked * 0.95), px(6), x(asked * 1.05), px(16),
                              fill=theme.TRACK, outline="")
        self.create_line(left, middle, right, middle, fill=theme.BORDER, width=1)
        inside = asked * 0.95 <= thinnest and thickest <= asked * 1.05
        bar = px(6)
        a, b = x(thinnest), x(thickest)
        # A round-capped line runs past its ends by half its width.
        a, b = min(a + bar / 2, (a + b) / 2), max(b - bar / 2, (a + b) / 2)
        self.create_line(a, middle, b, middle, width=bar, capstyle="round",
                         fill=theme.OK if inside else theme.ERROR)
        mark = x(asked)
        self.create_line(mark, px(3), mark, px(19), fill=theme.INK,
                         width=max(1.0, 1.5 * theme.px(1)))
        base = px(self.HEIGHT)
        self.create_text(left, base, text=_fmt(low), anchor="sw", fill=theme.MUTED,
                         font=f.mono_micro)
        self.create_text(right, base, text=_fmt(high), anchor="se", fill=theme.MUTED,
                         font=f.mono_micro)
        self.create_text(mark, base, text=f"{_fmt(asked)} asked", anchor="s",
                         fill=theme.MUTED, font=f.mono_micro)
