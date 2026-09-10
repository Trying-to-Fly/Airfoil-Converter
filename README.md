# Airfoil Converter

Converts airfoil-plotter CSV exports into SolidWorks *Curve Through XYZ Points*
files (`.sldcrv` or `.txt`), with plane placement, leading-edge offset, optional
chord rescale, and a constant-distance surface offset.

With SolidWorks 2026 or newer open, it puts those curves straight into the part
and keeps them there: change a setting, press **Export** again, and the same
feature refreshes in place, so a loft built on it rebuilds without a single
reference being re-picked. See *Driving SolidWorks* below.

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
   - **Close with TE line** — the surface as one open curve, plus a second file
     holding just the two trailing-edge points, which imports as a straight line
     closing the gap. For a blunt trailing edge (see *Trailing edge* below).
4. Optionally enter a **TE thickness** to blunt the trailing edge (see *Trailing
   edge* below). `0` keeps the section's own trailing edge, and its full chord;
   tick **Keep chord after the cut** to get both the blunt edge and the chord.
5. Optionally enter a **target chord** to rescale (blank keeps the source's chord).
6. Optionally enter an **offset** and pick *Inward* or *Outward* (see *Offset* below).
7. Choose the **plane**: a main plane (XY / XZ / YZ), a plane through 3 points,
   a plane through 2 points constrained perpendicular or parallel to a main
   plane, a plane **normal to a line** — through `P1`, perpendicular to the
   line `P1 -> P2` — or, for a loaded curve, **As loaded**, its own plane. With
   the part open you can click the plane in SolidWorks instead of typing it; see
   *Picking the plane out of the model* below.
8. Point the airfoil where you want it (see *Orientation* below).
9. Set where the **leading edge** lands, by typing it or by clicking a sketch
   point in SolidWorks. This is where the 2D origin is placed.
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
in CAD. *Close with TE line* writes that line as its own two-point curve file,
`..._airfoil_te.sldcrv`, which imports as a genuine straight segment; *Auto-close*
instead shuts the loop inside the spline, which bulges slightly across a blunt
gap; in *Leave open* you draw the line yourself, and in *Split upper/lower* the
two halves end level with each other.

**By default the chord ends at the cut.** What the cut takes off is not made up
elsewhere, so a 175 mm airfoil given a 1.5 mm trailing edge comes out about
166 mm long, nose to cut — the airfoil is simply shorter. Set the thickness to
`0`, the default, to keep the section's own trailing edge and its full chord.

**Keep chord after the cut** gets you both. The section is grown back about the
leading edge until it spans its original chord again, and the cut is made a
little further aft to allow for that growth, so the finished section carries the
full chord *and* a trailing edge exactly as thick as you asked: a 275 mm section
cut to a 0.8 mm trailing edge stays 275 mm long with a 0.8 mm gap, instead of the
267 mm it would come out at otherwise. Growing it is a true scaling, not a
chordwise stretch, so the profile is unchanged — but everything grows with it:
that 275 mm SD7037 comes back about 2.8 % thicker, since that is what it takes to
make up the 7 mm the cut removed. The chord it returns to is the one the points
actually span, which for a curve read back in is the chord it reports, not the
nominal one.

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

On a **normal-to-line plane** the line fixes only the plane, not the airfoil's
directions on it, so they follow a convention: seen from `P2`, looking back
along the line at the plane, the chord runs toward global +X (or toward +Y when
the line itself runs along X) and up completes that view. A line along +Z
therefore reproduces the XY plane's frame exactly. Swapping `P1` and `P2` turns
the plane over — same plane, opposite up — and **Flip up direction**, the
in-plane rotation and the angle of attack all apply as usual.

**Rotate in plane** turns the section by `0°`, `90°`, `180°` or `270°` about the
leading-edge point, and works in every mode. A turn goes the way a positive
angle of attack goes — the trailing edge swings toward the *down* side first — so
`90°` stands the section on its tail, with the nose still at the leading-edge
point, and `270°` stands it on its back. `180°` swaps nose with tail *and* top
with bottom, so the shape is unchanged and a cambered section ends up cambered
the other way; it is not a nose-to-tail mirror. The turns are exact: `90°` swaps
and negates the plane's two axes rather than taking a sine of anything, so an
airfoil on a main plane stays exactly on its axes.

**Angle of attack** pitches the section within its plane, about the leading-edge
point, and applies in every mode. Positive is nose-up: the leading edge stays
put and the trailing edge swings toward the *down* side of whatever you chose as
up. It is applied last, so it measures against the final orientation — after any
flip or in-plane rotation. Blank or `0` leaves the section unpitched.

Coordinates are written as three tab-separated columns per line, six decimals,
with `mm` on every value. The suffix is what makes the document's own unit
setting irrelevant: SolidWorks holds curve points in metres whatever the file
says, so a 175 mm chord arrives as 175 mm in an inch document too.

> **Trailing edge note:** a *Curve Through XYZ Points* is always an open spline,
> so *Leave open* leaves a visible gap at the trailing edge — it removes the
> point that closed the loop. *Auto-close* is the default and gives a closed
> section; use *Split upper/lower* if your SolidWorks version rejects the
> coincident first/last points of a closed curve as self-intersecting, and
> *Close with TE line* when the trailing edge is blunt and the gap should be
> closed by a straight line rather than by the spline.

## Driving SolidWorks

With a part open in SolidWorks 2026 or newer, the **curves flyout** down the
right-hand side lists the curves in that part and what the app knows about each.
It is opened and closed from **Curves** in the window's SolidWorks bar, and the
window grows and shrinks sideways with it, so the form itself never moves.
Press **Export** and the curves go straight in: any that are not there yet are
imported and named, and any that are there have their points replaced. The
document is rebuilt once, at the end.

The point of replacing rather than re-importing is that the feature keeps its
identity. Loft two ribs together, change a chord, press Export, and the loft
rebuilds. Nothing is re-picked.

**It needs `pywin32`.** Without it the app behaves exactly as it did before and
the panel says so. The prebuilt executable already has it.

```
pip install pywin32
```

To see what the app can see, without opening the app:

```
python -m airfoil_converter.swcom
```

That prints every SolidWorks it can reach, its version, the open documents and
their curve features. It is the first thing to run when the panel says something
unexpected. Add `--selection` and it describes whatever is clicked in SolidWorks
instead, which is the first thing to run when a pick refuses something.

### Picking the plane out of the model

The coordinates are already in the part, so they need not be typed twice. Press
**Pick from SolidWorks** under the plane settings and click, in SolidWorks:

1. **A plane** — a reference plane, or any flat face. This is the plane the
   section is placed on.
2. **A line** — a straight edge, or a sketch line. The chord runs along it.
   Press **Skip** to leave the chord to the normal-to-line convention.
3. **The leading edge** — a sketch point, or a corner. Press **Skip** to leave
   the leading edge where it is.

The form then switches to **3 points** and fills `P1`, `P2` and `P3` in for
you: `P1` is the leading edge, `P2` the far end of the line, `P3` one chord
length to the up side. Nothing new is stored — a picked plane is an ordinary
3-point plane, so the settings save, reload and export exactly as a typed one
does.

Skipping the line leaves nothing to fix the chord *on* the plane, so the form
falls back to **Normal to line**, whose convention is described under
*Orientation* above.

There is a second **Pick** button beside the leading edge, for moving a section
without touching its plane.

Two things worth knowing:

- **The chord runs nose to tail, and a line has two ends.** The end nearer the
  leading edge you picked becomes the nose. Skip the leading edge and the line
  keeps its own direction, which is a coin toss — press **Flip up direction**,
  or swap `P1` and `P2` by hand, if the section comes out backwards.
- **Up follows SolidWorks, not the app's own main planes.** A picked plane is
  oriented the way a sketch on that plane is, which on some planes is the
  opposite of the `XY` / `XZ` / `YZ` convention. **Flip up direction** is the
  cure and stays live in 3-point mode.

Picking clears the selection in SolidWorks each time it reads it. That is what
tells one click from the last one still being selected, and it is why a click
visibly deselects itself. **Cancel** leaves every field exactly as it was, and a
pick nobody finishes gives up after a minute and a half.

### Names

A curve's name comes from the source file and what the curve *is*, never from
the settings that shaped it: `sd7037-il_airfoil`, `sd7037-il_airfoil_te`,
`sd7037-il_camber`. That is deliberate. A name carrying the offset would change
the moment you changed the offset, and the next export would insert a *second*
curve while your loft went on pointing at the first.

Several ribs cut from one aerofoil get a number: `sd7037-il_airfoil`,
`sd7037-il_airfoil_2`, and so on. The number is handed out once, when the record
is created, and never moves afterwards. Press **New curve** to start a fresh one
while keeping every field, which is how the next rib along gets made.

### The blunt trailing edge comes back as one curve

*Close with TE line* deliberately writes two curves: the surface, left open, and
the straight line closing the gap. It has to. A *Curve Through XYZ Points* is one
spline through all its points, so folding the two trailing-edge corners into the
surface curve would make the spline run smoothly through them, rounding both
corners and bowing the flat face outward.

A loft wants one thing to pick, though, so after the two curves are in the part
the app joins them with a **Composite Curve**, named `..._airfoil_joined`. That
joins them rather than refitting them, so the corners stay sharp. Loft the joined
curve, not either half.

It is made once. A composite is derived from its inputs, so every later export
just reloads the two curves underneath and the joined one follows. The camber is
never part of it and stays its own curve, and *Auto-close* and *Split
upper/lower* have nothing to join, so nothing is made for them.

Joining creates a feature, so the **Insert new curves into the open part**
checkbox governs it too.

### What it remembers

SolidWorks stores points and nothing else. The chord, the plane, the angle of
attack, the trailing edge, the source file: none of that survives into the part,
and none of it can be read back out. So the app keeps it beside the part, in
`<part>.airfoils.json`. Click a curve in the panel and its settings come back to
the form.

A part that has never been saved has nowhere to keep that file, so its settings
last only until the app closes. The panel says so.

### Things it will not do

- **It never deletes a feature.** A wrong delete destroys work; a wrong insert
  only clutters. Change the trailing-edge mode from *Close with TE line* to
  *Auto-close* and the app warns that the `_te` curve is now unused, then leaves
  it in the tree for you to remove.
- **An inserted curve is only a curve.** Adding it to a loft or a surface is
  still yours to do, and the status line says so each time.
- **It follows the active document only.** Curves go into the part in front of
  you, and nowhere else.

### The files stay live

The exported `.sldcrv` files are no longer output you can throw away. A refresh
re-reads them by path, so moving or deleting them breaks the link. Keep them
where they land.

## Building the executable

```
pip install pyinstaller pywin32
build.bat
```

This produces `dist\Airfoil Converter v1.5.exe`, a single self-contained file.
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
  export.py     # the form as data, and the curves it produces
  store.py      # what the app remembers about a part's curves
  swcom.py      # the only module that talks to SolidWorks
  swlink.py     # insert, refresh, rebuild -- in that order
  gui.py        # tkinter window: the panels, the handlers, the poll
  theme.py      # every colour, size and face the window uses
  widgets.py    # the controls the design asks for, drawn by hand
  ui_text.py    # the readouts and tracker rows, worked out without a window
tests/
main.py         # entry point
```
