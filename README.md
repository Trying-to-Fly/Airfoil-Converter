# Airfoil Converter

Converts airfoil-plotter CSV exports into SolidWorks *Curve Through XYZ Points*
files (`.sldcrv` or `.txt`), with plane placement, leading-edge offset, optional
chord rescale, and a constant-distance surface offset.

It also reads curve files back in, so a section you already have — one this app
wrote, or one exported from SolidWorks — can be re-placed, rescaled, pitched, or
offset without going back to the CSV.

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

1. **Browse** to an airfoil-plotter CSV (see `sd7037-il.csv` for the expected
   format), or to a curve file (see *Curve files as input* below).
2. Choose what to export: the airfoil surface, the camber line, or both.
3. Pick how to handle the trailing edge:
   - **Auto-close** (the default) — make the last point exactly equal the first,
     giving a closed curve.
   - **Leave open** — remove the repeated final point; one open curve, left open
     at the trailing edge.
   - **Split upper/lower** — two files, each running leading edge to trailing edge.
4. Optionally enter a **TE thickness** to blunt the trailing edge (see *Trailing
   edge* below). `0` keeps the section's own trailing edge, and its full chord.
5. Optionally enter a **target chord** to rescale (blank keeps the source's chord).
6. Optionally enter an **offset** and pick *Inward* or *Outward* (see *Offset* below).
7. Choose the **plane**: a main plane (XY / XZ / YZ), a plane through 3 points,
   a plane through 2 points constrained perpendicular or parallel to a main
   plane, or — for a loaded curve — **As loaded**, its own plane.
8. Point the airfoil where you want it (see *Orientation* below).
9. Set where the **leading edge** lands. This is where the 2D origin is placed.
   It defaults to the origin for main planes, to `P1` for custom planes, and to
   the curve's own leading edge for a loaded curve, until you type in it yourself.
10. **Export.** Output goes next to the source file by default.

## Curve files as input

The **Browse** dialog takes a *Curve Through XYZ Points* file — `.sldcrv` or
`.txt`, one `X Y Z` point per line in millimetres — as well as a CSV. Feed it
one this app wrote, or one exported from SolidWorks. The format is worked out
from the file's contents, not its extension, so a plotter CSV saved as `.txt`
still reads as a CSV.

A curve already stands somewhere in space, so the app reads it back onto the
plane it was drawn on: the longest span across the curve is taken as the chord,
the blunter of its two ends as the leading edge. The plane picker gains an **As
loaded** option, chosen by default, which keeps the curve exactly where it is —
so *load a rib, offset it 2 mm inward, export* gives you the inner wall in the
same plane, in the same place, with nothing else moved. Pick a different plane
instead and the curve is re-placed like a CSV would be; **target chord**,
**angle of attack** and the flips all still apply.

Two things to know:

- **The chord is read off the points, not from a header.** A curve carries no
  metadata, so it comes back to within the point spacing at the nose — a 175 mm
  section may report 174.964 mm. Rescaling to a target chord measures from that.
- **A curve must be flat and must close.** A section that strays off its own
  plane is refused, and so is half a surface: *Split upper/lower* output encloses
  no area, so there is no wall to walk around. Offset the whole loop, and split
  it afterwards if you need the halves.

## Trailing edge

A drawn airfoil ends in a point of no thickness. A built one cannot, so **TE
thickness** cuts the section back to a trailing edge you can actually make.

The cut is a *vertical* line — a line of constant chordwise station — placed
where the section stands exactly that thick. The curve comes back with its two
ends one directly above the other, so a single straight line closes the section
in CAD. In *Auto-close* that line is drawn for you; in *Leave open* you draw it
yourself, and in *Split upper/lower* the two halves end level with each other.

**The chord ends at the cut.** What the cut takes off is not made up elsewhere:
the section is not stretched back out to its nominal chord, so a 175 mm airfoil
given a 1.5 mm trailing edge comes out about 166 mm long, nose to cut. The
airfoil is simply shorter. Set the thickness to `0` — the default — to keep the
section's own trailing edge and its full chord.

The thickness is in millimetres **of the finished part**, so it is applied after
any target-chord rescale, and after any offset — the cut lands on the curve that
actually gets exported. A trailing edge thicker than the section itself is
refused, and a section that already ends blunter than you asked for is left
alone: it has nothing to give up.

## Offset

**Offset** does what SolidWorks *Offset Entities* does to a closed contour: it
walks the surface at a fixed perpendicular distance, so the wall between the
original section and the offset one is the same thickness everywhere. Use it for
a skin, a wall, or a rib pocket.

This is *not* a rescale, and the offset curve is generally **not the same
airfoil**. Holding a constant distance is what matters, and where the two
disagree, distance wins:

- **A sharp trailing edge cannot survive an inward offset.** Where the section
  is thinner than twice the offset, the offset upper and lower surfaces run into
  each other, so the curve is trimmed back to where they cross. An inward offset
  therefore ends in a new, blunter trailing edge, some way forward of the old
  one — the more you offset, the further forward. The same happens at the nose
  once the offset exceeds the leading-edge radius.
- **An outward offset rounds a sharp trailing edge**, to an arc of the offset
  radius, because that is what a true offset of a corner is.
- Offset **too far inward** and the section runs out entirely; the app says so
  rather than writing a curve.

The distance is in millimetres **of the finished part** — it is applied after any
target-chord rescale, so a 2 mm offset is 2 mm of wall whatever the chord. Only
the airfoil surface is offset; the camber line is written unchanged. The offset
appears in the filename (`sd7037-il_airfoil_in2mm.sldcrv`), so a set of walls
does not overwrite itself.

To get a *smaller but identical* profile instead, leave the offset blank and use
**target chord** — that scales the section, thickness and all.

## Orientation

On a **main plane**, two dropdowns place the airfoil outright:

- **Chord runs along** — the signed axis from the trailing edge toward the
  leading edge. Pick the axis the *nose* points along; the body then extends the
  opposite way. On YZ, `+Z` points the nose at +Z and puts the trailing edge at −Z.
- **Up direction** — which way the airfoil's thickness and camber face. Only the
  remaining in-plane axis is offered, and changing the chord axis re-offers it.

On a **3-point or 2-point plane** the chord is fixed by `P1 -> P2`, and on an
**As loaded** plane it is fixed by the curve itself, so those dropdowns are
disabled. Instead, **Flip up direction** mirrors which side of the chord counts
as up.

**Flip airfoil (rotate 180° in plane)** works in every mode. It spins the section
a half-turn about the leading-edge point: nose swaps with tail *and* top swaps
with bottom, so the shape is unchanged and a cambered section ends up cambered
the other way. It is not a nose-to-tail mirror.

**Angle of attack** pitches the section within its plane, about the leading-edge
point, and applies in every mode. Positive is nose-up: the leading edge stays
put and the trailing edge swings toward the *down* side of whatever you chose as
up. It is applied last, so it measures against the final orientation — after any
flip or 180° rotation. Blank or `0` leaves the section unpitched.

Coordinates are written in millimetres, six decimals, three space-separated
columns per line — use with millimetre-unit SolidWorks documents.

> **Trailing edge note:** a *Curve Through XYZ Points* is always an open spline,
> so *Leave open* leaves a visible gap at the trailing edge — it removes the
> point that closed the loop. *Auto-close* is the default and gives a closed
> section; use *Split upper/lower* if your SolidWorks version rejects the
> coincident first/last points of a closed curve as self-intersecting.

## Building the executable

```
pip install pyinstaller
build.bat
```

This produces `dist\Airfoil Converter v1.1.exe`, a single self-contained file.
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
  parser.py     # CSV -> metadata + point sections; curve file -> XYZ points
  geometry.py   # plane frames, 2D<->3D transform, trailing-edge handling, offsetting
  writer.py     # .sldcrv / .txt output
  gui.py        # tkinter window
tests/
main.py         # entry point
```
