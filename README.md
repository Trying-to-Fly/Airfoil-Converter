# Airfoil Converter

Converts airfoil-plotter CSV exports into SolidWorks *Curve Through XYZ Points*
files (`.sldcrv` or `.txt`), with plane placement, leading-edge offset, and
optional chord rescale.

No runtime dependencies — just Python 3.9+ with tkinter (bundled with the
standard Windows Python installer).

## Running

Double-click `main.py`, or from a terminal:

```
python main.py
```

Prebuilt Windows executables are attached to the
[Releases](https://github.com/Trying-to-Fly/Airfoil-Converter/releases) page —
download and double-click, no Python needed.

## Using it

1. **Browse** to an airfoil-plotter CSV (see `sd7037-il.csv` for the expected format).
2. Choose what to export: the airfoil surface, the camber line, or both.
3. Pick how to handle the trailing edge:
   - **Drop duplicate** — remove the repeated final point; one open curve.
   - **Auto-close** — make the last point exactly equal the first, giving a closed curve.
   - **Split upper/lower** — two files, each running leading edge to trailing edge.
4. Optionally enter a **target chord** to rescale (blank keeps the CSV's chord).
5. Choose the **plane**: a main plane (XY / XZ / YZ), a plane through 3 points,
   or a plane through 2 points constrained perpendicular or parallel to a main plane.
6. Point the airfoil where you want it (see *Orientation* below).
7. Set where the **leading edge** lands. This is where the CSV's 2D origin is placed.
   It defaults to the origin for main planes, and to `P1` for custom planes,
   until you type in it yourself.
8. **Export.** Output goes next to the CSV by default.

## Orientation

On a **main plane**, two dropdowns place the airfoil outright:

- **Chord runs along** — the signed axis from the leading edge toward the
  trailing edge. Pick the axis the *body* extends along; the nose then points
  the opposite way. On YZ, `-Z` puts the trailing edge at −Z, so the nose
  points **+Z**.
- **Up direction** — which way the airfoil's thickness and camber face. Only the
  remaining in-plane axis is offered, and changing the chord axis re-offers it.

On a **3-point or 2-point plane** the chord is fixed by `P1 -> P2`, so those
dropdowns are disabled. Instead, **Flip up direction** mirrors which side of the
chord counts as up.

**Flip airfoil (rotate 180° in plane)** works in every mode. It spins the section
a half-turn about the leading-edge point: nose swaps with tail *and* top swaps
with bottom, so the shape is unchanged and a cambered section ends up cambered
the other way. It is not a nose-to-tail mirror.

Coordinates are written in millimetres, six decimals, three space-separated
columns per line — use with millimetre-unit SolidWorks documents.

> **Trailing edge note:** try *Drop duplicate* first. Some SolidWorks versions
> reject the coincident first/last points produced by *Auto-close* as
> self-intersecting.

## Building the executable

```
pip install pyinstaller
build.bat
```

This produces `dist\Airfoil Converter v1.0.exe`, a single self-contained file.
If `build.bat` cannot find `pyinstaller`, use `python -m PyInstaller` instead —
pip may have installed the scripts outside your PATH.

## Development

```
pip install pytest
python -m pytest
```

Layout:

```
src/airfoil_converter/
  parser.py     # CSV -> metadata + point sections
  geometry.py   # plane frames, 2D->3D transform, trailing-edge handling
  writer.py     # .sldcrv / .txt output
  gui.py        # tkinter window
tests/
main.py         # entry point
```
