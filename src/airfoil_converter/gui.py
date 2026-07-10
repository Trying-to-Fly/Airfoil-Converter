"""Single-window tkinter front end for the airfoil converter."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk
from typing import List, Optional, Sequence, Tuple

from . import geometry, writer
from .geometry import GeometryError
from .parser import AirfoilData, AirfoilParseError, parse_csv

Point2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

TE_DROP = "Drop duplicate"
TE_CLOSE = "Auto-close"
TE_SPLIT = "Split upper/lower"
TE_MODES = (TE_DROP, TE_CLOSE, TE_SPLIT)

MODE_3POINTS = "3points"
MODE_2POINTS = "2points"

OK_COLOR = "#1a7f37"
ERROR_COLOR = "#b42318"

# Tabbing or arrowing through the leading-edge fields is not an edit.
NAVIGATION_KEYS = frozenset(
    {
        "Tab", "ISO_Left_Tab", "Left", "Right", "Up", "Down", "Home", "End",
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    }
)


class InputError(Exception):
    """A field on the form could not be read."""


def _parse_float(text: str, field: str) -> float:
    try:
        return float(text.strip())
    except ValueError:
        raise InputError(f"{field} must be a number (got {text.strip()!r}).") from None


class ConverterApp(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.data: Optional[AirfoilData] = None
        self._le_manual = False
        self._syncing = False
        self._syncing_axes = False

        self.csv_path = tk.StringVar()
        self.loaded_text = tk.StringVar(value="No CSV loaded.")
        self.export_airfoil = tk.BooleanVar(value=True)
        self.export_camber = tk.BooleanVar(value=True)
        self.te_mode = tk.StringVar(value=TE_DROP)
        self.target_chord = tk.StringVar()
        self.plane_mode = tk.StringVar(value="XY")
        self.constraint = tk.StringVar(value=geometry.PERPENDICULAR)
        self.main_plane = tk.StringVar(value="XY")
        self.flip = tk.BooleanVar(value=False)
        self.rotate180 = tk.BooleanVar(value=False)
        default_chord, default_up = geometry.default_axes("XY")
        self.chord_axis = tk.StringVar(value=default_chord)
        self.up_axis = tk.StringVar(value=default_up)
        self.extension = tk.StringVar(value=".sldcrv")
        self.out_folder = tk.StringVar()
        self.status = tk.StringVar(value="Load a CSV to begin.")

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

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        row = 0

        source = ttk.LabelFrame(self, text="Source", padding=8)
        source.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="CSV file:").grid(row=0, column=0, sticky="w")
        ttk.Entry(source, textvariable=self.csv_path).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(source, text="Browse", command=self._browse_csv).grid(row=0, column=2)
        ttk.Label(source, textvariable=self.loaded_text, foreground="#555").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        row += 1

        export = ttk.LabelFrame(self, text="Export", padding=8)
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

        ttk.Label(export, text="Target chord (mm):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(export, textvariable=self.target_chord, width=12).grid(
            row=2, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Label(export, text="(blank = keep CSV chord)", foreground="#555").grid(
            row=2, column=2, sticky="w", padx=(6, 0), pady=(6, 0)
        )
        row += 1

        plane = ttk.LabelFrame(self, text="Plane", padding=8)
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

        self.flip_check = ttk.Checkbutton(plane, text="Flip up direction", variable=self.flip)
        self.flip_check.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            plane, text="Flip airfoil (rotate 180° in plane)", variable=self.rotate180
        ).grid(row=7, column=0, columnspan=6, sticky="w")
        row += 1

        placement = ttk.LabelFrame(self, text="Placement", padding=8)
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

        out = ttk.LabelFrame(self, text="Output", padding=8)
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

        ttk.Button(self, text="Export", command=self._export).grid(row=row, column=0, pady=(0, 8))
        row += 1

        self.status_label = ttk.Label(self, textvariable=self.status, wraplength=520)
        self.status_label.grid(row=row, column=0, sticky="w")

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
        custom = mode in (MODE_3POINTS, MODE_2POINTS)
        needed = 3 if mode == MODE_3POINTS else (2 if mode == MODE_2POINTS else 0)
        for pi, entries in enumerate(self.p_entries):
            state = "normal" if pi < needed else "disabled"
            for entry in entries:
                entry.configure(state=state)

        constraint_state = "readonly" if mode == MODE_2POINTS else "disabled"
        self.constraint_box.configure(state=constraint_state)
        self.main_plane_box.configure(state=constraint_state)

        # On main planes the axis pickers set 'up' outright, so the flip checkbox
        # would only be a confusing second way to say the same thing.
        axis_state = "disabled" if custom else "readonly"
        self.chord_axis_box.configure(state=axis_state)
        self.up_axis_box.configure(state=axis_state)
        self.flip_check.configure(state="normal" if custom else "disabled")
        if not custom:
            self.flip.set(False)
            self._reset_axes(mode)

        self._le_manual = False
        self._sync_leading_edge()
        self.le_hint.configure(
            text="Defaults to P1 — the 2D origin lands here." if custom
            else "Defaults to (0, 0, 0) — the 2D origin lands here."
        )

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
        self._syncing = True
        try:
            custom = self.plane_mode.get() in (MODE_3POINTS, MODE_2POINTS)
            source = self.p_vars[0] if custom else None
            for i, var in enumerate(self.le_vars):
                var.set(source[i].get() if source else "0")
        finally:
            self._syncing = False

    def _browse_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Select airfoil CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.csv_path.set(path)
            self._load(path)

    def _browse_folder(self) -> None:
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.out_folder.set(path)

    def _load(self, path: str) -> None:
        try:
            data = parse_csv(path)
        except AirfoilParseError as exc:
            self.data = None
            self.loaded_text.set("No CSV loaded.")
            self._set_status(str(exc), ok=False)
            return

        self.data = data
        has_camber = bool(data.camber)
        self.camber_check.configure(state="normal" if has_camber else "disabled")
        if not has_camber:
            self.export_camber.set(False)

        summary = f"Loaded: {data.name}, chord {data.chord:g} mm, {len(data.airfoil)} pts"
        if not has_camber:
            summary += " (no camber line)"
        self.loaded_text.set(summary)
        if not self.out_folder.get():
            self.out_folder.set(os.path.dirname(os.path.abspath(path)))
        self._set_status("CSV loaded. Set the plane and export.")

    # ---------------------------------------------------------------- export

    def _read_vec(self, vars_: Sequence[tk.StringVar], name: str) -> Vec3:
        values = [_parse_float(v.get(), f"{name} {axis}") for v, axis in zip(vars_, "XYZ")]
        return (values[0], values[1], values[2])

    def _read_target_chord(self) -> Optional[float]:
        text = self.target_chord.get().strip()
        if not text:
            return None
        return _parse_float(text, "Target chord")

    def _curves(self) -> List[Tuple[str, List[Point2]]]:
        assert self.data is not None
        curves: List[Tuple[str, List[Point2]]] = []

        if self.export_airfoil.get():
            surface = self.data.airfoil
            mode = self.te_mode.get()
            if mode == TE_DROP:
                curves.append(("airfoil", geometry.drop_duplicate(surface)))
            elif mode == TE_CLOSE:
                curves.append(("airfoil", geometry.auto_close(surface)))
            elif mode == TE_SPLIT:
                upper, lower = geometry.split_surfaces(surface)
                curves.append(("airfoil_upper", upper))
                curves.append(("airfoil_lower", lower))
            else:
                raise InputError(f"Unknown TE handling mode {mode!r}.")

        if self.export_camber.get():
            camber = self.data.camber
            if not camber:
                raise InputError("This CSV has no camber line to export.")
            curves.append(("camber", list(camber)))

        if not curves:
            raise InputError("Nothing selected to export.")
        return curves

    def _export(self) -> None:
        try:
            self._export_unsafe()
        except (InputError, GeometryError, AirfoilParseError, ValueError) as exc:
            self._set_status(str(exc), ok=False)
        except OSError as exc:
            self._set_status(f"Could not write the output: {exc}", ok=False)

    def _export_unsafe(self) -> None:
        if self.data is None:
            raise InputError("Load a CSV first.")

        folder = self.out_folder.get().strip()
        if not folder:
            raise InputError("Choose an output folder.")
        if not os.path.isdir(folder):
            raise InputError(f"Output folder does not exist: {folder}")

        factor = geometry.scale_factor(self.data.chord, self._read_target_chord())

        mode = self.plane_mode.get()
        kwargs = {"flip": self.flip.get(), "rotate180": self.rotate180.get()}
        if mode in geometry.MAIN_PLANES:
            kwargs.update(chord_axis=self.chord_axis.get(), up_axis=self.up_axis.get())
        elif mode == MODE_3POINTS:
            kwargs.update(
                p1=self._read_vec(self.p_vars[0], "P1"),
                p2=self._read_vec(self.p_vars[1], "P2"),
                p3=self._read_vec(self.p_vars[2], "P3"),
            )
        elif mode == MODE_2POINTS:
            kwargs.update(
                p1=self._read_vec(self.p_vars[0], "P1"),
                p2=self._read_vec(self.p_vars[1], "P2"),
                constraint=self.constraint.get(),
                main_plane=self.main_plane.get(),
            )
        u, v = geometry.plane_frame(mode, **kwargs)

        leading_edge = self._read_vec(self.le_vars, "Leading edge")
        curves = self._curves()

        stem = os.path.splitext(os.path.basename(self.csv_path.get()))[0]
        extension = self.extension.get()

        written = []
        for suffix, points in curves:
            path = writer.output_path(folder, stem, suffix, extension)
            count = writer.write_curve(path, geometry.to_3d(points, leading_edge, u, v, factor))
            written.append(f"{os.path.basename(path)} ({count} pts)")

        self._set_status("Wrote " + ", ".join(written))


def main() -> None:
    root = tk.Tk()
    root.title("Airfoil Converter")
    root.minsize(560, 200)
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
