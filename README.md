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
6. Set where the **leading edge** lands. This is where the CSV's 2D origin is placed.
   It defaults to the origin for main planes, and to `P1` for custom planes,
   until you type in it yourself.
7. **Export.** Output goes next to the CSV by default.

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
