# Airfoil Converter — UI design (settled 2026-09-10)

The visual and behavioural spec for the redesigned window. It is written for
whoever builds it, and it assumes the code on the `solidworks-link` branch:
the SolidWorks panel, the pick flow and the curve records in `gui.py` are all
kept — this is a new skin and a new arrangement for them, not new features.

Everything here was chosen on a canvas and is recorded so it need not be
re-argued. The canvas, with the earlier options that were passed over, is at
<https://claude.ai/code/artifact/6f52786b-c864-43b9-a5b9-24f3d5aa320a>.
The artboards under `canvas/` open in any browser as static pages
(`Main.dc.html` is the whole window with the flyout open; `PickStates.dc.html`
is the pick flow in its three states) and `renders/` has them as PNGs.
`canvas/gen.py` regenerates them from `canvas/ui.py`, which holds every
colour and size below in one place.

## 1. What was decided, and what was rejected

| Decision | Chosen | Passed over |
|---|---|---|
| Layout | **D · Workbench** — every field on screen, titled panels, no scrolling | A (bare property sheet, "looks like the current Python tool"), B (wizard steps, "too arcadey"), C (dark preview-first) |
| SolidWorks list | **Side flyout**, full height, toggled from a two-line bar in the strip | A panel at the bottom of the strip (too short for a long list of ribs) |
| Colour | **Graphite** — black controls on white and grey; colour only for state | Any tinted neutral (the blue-grey greys were rejected), Windows blue, navy, steel |
| Type | **Libre Franklin** for UI, **JetBrains Mono** for numbers, paths and curve names | IBM Plex, Source Sans, Fira, Red Hat, Noto, Public Sans |
| Pick feedback | **A per-step tracker** with a tick, a ring and a hollow ring | The branch's one sentence and four buttons |

Two things the user said that shape everything: it is *a serious design
tool*, not an app; and *there is very little feedback today* — a successful
plane click in SolidWorks looks the same as nothing having happened.

## 2. The window

**Portrait.** The window is a strip beside SolidWorks on a wide monitor.
Content width is **460 px at 100 % scaling** (Windows scaling multiplies it;
`_scale_to_dpi` already does this). Natural height is about **1190 px** with
the pick tracker open, less when idle. Nothing scrolls; if a monitor is
shorter than that the window scrolls as a whole, but that is the exception.

**The flyout.** A **360 px** column docked to the right edge, full height,
holding the SolidWorks curve list. It is shown and hidden from a button in
the strip's SolidWorks bar (`Curves` / `Hide curves`) and from `Hide` in its
own header. When it opens the *window* grows sideways by 360 px and shrinks
back when it closes; the strip never reflows. On an ultrawide that is the
point of it.

Strip and flyout share one window ground, `#f0f0f0`. The flyout's own ground
is `#f8f8f8` with a 1 px `#d8d8d8` left border.

## 3. Colour

Plain white and grey. The only chromatic colours are the two state colours,
which the app already has (`OK_COLOR`, `ERROR_COLOR`).

| Role | Hex |
|---|---|
| Window ground | `#f0f0f0` |
| Panel body, fields, selected segment | `#ffffff` |
| Panel header, tree header | `#f5f5f5` |
| Secondary button, flyout ground | `#f8f8f8` |
| Pick tracker ground | `#fafafa` |
| Disabled fill | `#f4f4f4` |
| Segmented-control track | `#ebebeb` |
| Selected tree row | `#e9e9e9` |
| Header rule, disabled border | `#e3e3e3` |
| Panel border, tree border | `#d8d8d8` |
| Control border | `#c6c6c6` |
| Pick tracker border | `#cfcfcf` |
| Disabled glyph | `#b5b5b5` |
| Faint text (disabled labels, placeholders) | `#9c9c9c` |
| Header readout text | `#8f8f8f` |
| Muted text (hints) | `#6e6e6e` |
| Panel header text | `#505050` |
| Label text | `#3c3c3c` |
| Ink | `#1f1f1f` |
| **Accent** — checked boxes, primary button, active pick step | `#2b2b2b` |
| OK — linked, status good, done pick step | `#1a7f37` |
| Error — attention states, refusals, status bad | `#b42318` |
| Untracked tree rows | `#777777` |

Corner radius is **2 px** on everything rectangular. Round glyphs (pick
tracker, radio-style marks) stay round.

## 4. Type

| Use | Face | Weight | Size |
|---|---|---|---|
| Body, labels, buttons | Libre Franklin | 400 / 500 (buttons) / 600 (emphasis) | 13 px base; labels 12.5; hints 12; readouts 11.5 |
| Panel and section headers | Libre Franklin | 600, uppercase, letter-spacing 0.06 em | 11.5 px |
| Numbers, paths, axis names, curve names | JetBrains Mono | 400 / 500 | 12.5 px in fields; 12 in the tree; 11.5 in readouts |

Neither face ships with Windows. Bundle both (`LibreFranklin-Regular/
Medium/SemiBold.ttf`, `JetBrainsMono-Regular/Medium.ttf`; both are OFL) and
register them at launch — on Windows a private `AddFontResourceExW` call with
`FR_PRIVATE` before Tk creates its fonts, unregistered on exit. The fallback
stack is **Segoe UI** and **Consolas**, and the app must look right on them
too, because that is what a build without the font files gets.

Unicode minus (`−`) is used in the axis names, `−X`, exactly as the branch
already writes them.

## 5. Controls

Heights are at 100 % scaling; the app multiplies by its DPI scale.

- **Text field** — 26 px high, 1 px `#c6c6c6` border, white, 8 px side
  padding, mono. A unit (`mm`, `°`) sits inside at the right edge in Franklin
  11.5 px `#8f8f8f`. Placeholder text (the source chord in *Target chord*,
  `none` in *Offset*) is `#9c9c9c`. Disabled: `#f4f4f4` fill, `#e3e3e3`
  border, `#9c9c9c` text.
- **Dropdown** — a text field with a chevron at the right, 6 px from the
  edge. Same disabled treatment.
- **Segmented control** — replaces every radio row. Track `#ebebeb` with a
  1 px `#d8d8d8` border, 2 px inner padding, 2 px gaps; segments 22 px high
  (24 in the plane selector), 12 px text, `#505050`; the selected segment is
  white with a 1 px `#c6c6c6` ring and 600 weight, ink. A segment that cannot
  be chosen (`Loaded` with no curve file) is `#b5b5b5`. Numeric segments
  (`0°`, `.sldcrv`) are mono.
- **Checkbox** — 15 px box, 2 px radius, 1 px `#8f8f8f` border; checked fills
  `#2b2b2b` with a white tick. Label 7 px to the right. Disabled: `#f4f4f4`
  fill, `#d8d8d8` border, `#9c9c9c` label.
- **Secondary button** — 26 px (24 in toolbars, 22 inside the pick tracker,
  20 in a panel header), `#f8f8f8` fill, 1 px `#c6c6c6` border, 500 weight,
  10 px side padding, optional 14 px stroke icon at the left.
- **Primary button** (Export only) — 32 px, `#2b2b2b` fill, white 600 text,
  18 px side padding.
- **Panel** — white, 1 px `#d8d8d8` border. Header 26 px, `#f5f5f5`, 1 px
  `#e3e3e3` rule below, title at left, a *readout* at right in 11.5 px
  `#8f8f8f`. Body padding 8 px vertical, 12 px horizontal; rows 6–7 px apart.
- **Row** — label column **118 px** wide, 12.5 px `#3c3c3c`, then controls
  with 8 px gaps. A row's hint is 12 px `#6e6e6e` and is one line; anything
  longer belongs in the README, not the window.
- **Icons** — stroke SVG on a 16 px grid, 1.5 px stroke, current colour.
  Folder (Browse), cursor (Pick), sidebar (Curves), info circle, tick,
  chevron. No emoji, no glyph fonts.

## 6. The strip, top to bottom

Panels are 8 px apart; the strip has 12 px padding all round.

### Source — readout `CSV or curve file`
1. Path field (grows) + **Browse**.
2. A 110 × 14 px outline of the loaded section in `#505050`, then
   `**SD7037-092-88** · 175 mm · 61 pts · camber line` (12 px; the name 600
   weight). This is `loaded_text`, reformatted. With nothing loaded the line
   reads `No file loaded.` in `#9c9c9c` and the outline is absent.

### Shape — readout `mm of the finished part`
1. `Curves` — [x] Airfoil surface, [x] Camber line (12 px apart).
2. `Trailing edge` — segmented, full width: Auto-close · Leave open · Split ·
   TE line (the four `TE_MODES`, shortened labels).
3. `TE thickness` — 84 px field with `mm`, then [ ] Keep chord after the cut.
4. `Target chord` — 84 px field, placeholder is the source chord; hint
   *blank keeps the source chord*.
5. `Offset` — 84 px field, placeholder `none`; then a 140 px segmented
   Inward · Outward.

### Plane — readout `XY · nose −X · up +Y` (or `3 points · picked from Top Plane`)
1. Plane selector, segmented, full width, 24 px: XY · XZ · YZ · 3 points ·
   2 points · Normal · Loaded. `Loaded` is choosable only with a curve file,
   as today.
2. An X / Y / Z header line over the point columns (11 px `#8f8f8f`).
3. `Points` — P1, P2, P3 rows: a 22 px mono row label, then three 82 px
   fields. Disabled on a main plane, exactly as `_on_plane_mode_changed`
   already decides. After a pick the filled fields carry a 1 px `#2b2b2b`
   border instead of `#c6c6c6` (see §7) until the mode changes.
4. `Constraint` — 128 px dropdown (Perpendicular / Parallel), `to`, 62 px
   dropdown (XY / XZ / YZ). Enabled only for 2 points.
5. A 1 px `#ebebeb` rule.
6. `Chord runs along` — 64 px mono dropdown; `Up` — 64 px mono dropdown;
   [ ] Flip up. On a main plane the two dropdowns are live and Flip is
   disabled; off it the reverse, and the chord dropdown reads `P1 → P2`
   (92 px), disabled — the same rule the branch applies, made visible.
7. `Rotate in plane` — 168 px segmented, mono: 0° · 90° · 180° · 270°.
8. `Angle of attack` — 84 px field with `°`; hint *+ pitches the nose up*.
9. A rule, then the **pick tracker** (§7).

The readout is live: `<plane> · nose <chord axis> · up <up axis>` on a main
plane; `3 points · picked from <plane name>` after a pick; `2 points ·
perpendicular to XY`, `Normal to line`, `As loaded` otherwise.

### Placement — readout `where the 2D origin lands`
1. `Leading edge at` — three fields that share the width, each prefixed
   inside with a faint `X` / `Y` / `Z`, then a **Pick** button (cursor icon).
   The button is enabled exactly when `_can_push()` is true, like the plane
   pick. The `le_hint` text is folded into the readout; it is not a second
   line.

### Output — readout `Curve Through XYZ Points`
1. `Format` — 150 px segmented, mono: .sldcrv · .txt.
2. `Folder` — path field (grows) + Browse.

### SolidWorks bar — readout is the connection: `● 2026 — WingRib.SLDPRT`
The header's right side holds a 7 px status dot (`#1a7f37` connected,
`#b42318` when `_offline_reason()` would fire, `#9c9c9c` with no pywin32),
the version and document from `sw_status`, and a 20 px **Curves** /
**Hide curves** button with the sidebar icon. Body:
1. An info-circle icon and `editing_text` — *Export will update
   `sd7037-il_airfoil`, `sd7037-il_camber`.* or *Export will make a new
   curve.* Curve names in mono.
2. [x] Insert new curves into the open part (`insert_missing`).

When SolidWorks is unreachable the bar stays, the dot goes red, the readout
carries `sw_status`'s reason, and the Curves button is disabled.

### Footer (no panel; `margin-top: auto`)
Left: an 8 px dot in the status colour and `status` in 12.5 px of the same
colour, one line, ellipsised. Right: the primary **Export** button. This is
the existing `status_label`, moved into a bar.

## 7. The pick tracker

This replaces `pick_text` and the four buttons in a row. Each step of a pick
has its own row, so a successful click is visibly different from nothing.
`PickStates.dc.html` shows all three states.

**Idle** — one row: the **Pick from SolidWorks** button (cursor icon) and a
hint, *Fills P1–P3 from a plane, a line and a point you click in the part.*
Disabled, with the same button, when `_can_push()` is false; the status line
carries `_no_pick_reason()` if it is pressed anyway, as today.

**Active** — a block, `#fafafa` with a 1 px `#cfcfcf` border, 8 × 10 px
padding:

- Title row: `PICKING FROM SOLIDWORKS` (11 px, 600, uppercase, letter-spaced,
  `#2b2b2b`), then `step 2 of 3 · 1:12 left` in 11 px `#6e6e6e` — the
  countdown is the pick's own timeout, shown so the user knows it exists —
  and **Cancel** at the far right.
- One row per step (`Plane`, `Chord line`, `Leading edge`; the point pick
  has one row), 24 px, with a 16 px glyph, an 88 px step name and a detail:
  - **done** — a `#1a7f37` disc with a white tick; the detail is what was
    read: the object's name in mono, then ` · reference plane` / ` · flat
    face` / ` · sketch line` / ` · straight edge` / ` · sketch point` /
    ` · corner`, from `describe(picked)`.
  - **active** — a 2 px `#2b2b2b` ring with a centre dot; the name in 600;
    the detail is `Pick.says` for that step. **A refusal is shown on this
    row in `#b42318`** (*That is a circular edge. Click a straight edge or a
    sketch line.*) and stays until the next accepted click. An optional step
    carries a 22 px **Skip** at the row's right.
  - **pending** — a 1.5 px `#b5b5b5` hollow ring; name and detail in
    `#9c9c9c`; the detail says `optional · a sketch point, or a corner` or
    just what it will want.
- Footer row: *Each click clears itself in SolidWorks — that is how one click
  is told from the last.* in 11.5 px `#6e6e6e`, and **Use what I picked** at
  the right. This sentence is there because the deselection is otherwise
  read as a failure.

**Done** — the block collapses back to the idle row, but the hint is replaced
by a green tick glyph and `_picked_summary(session)`: *Read the plane, the
chord and the leading edge from SolidWorks.* The plane selector moves to
3 points, P1–P3 fill with a `#2b2b2b` border, the readout says `3 points ·
picked from <plane>`, and the leading edge fields fill (and their own Pick
button enables). A cancelled pick shows *Pick cancelled. Nothing on the form
was changed.* in `#6e6e6e` the same way.

The mapping to the branch is one-to-one: `_begin_pick` opens the block,
`_took_pick` / `_skip_pick` advance a row, `Pick.says` is the active row's
detail, `_end_pick(message)` and `_apply_pick` collapse it with their
message. The countdown is `_pick_ticks` against the pick's limit.

## 8. The flyout

Header row, 26 px: `SOLIDWORKS · CURVES IN THE PART` (panel-header style) and
**Hide** at the right. Then:

1. `sw_status` in two lines: the version and document in 12.5 px 600, and
   below it in 11.5 px `#6e6e6e` where the settings live — *Settings kept
   beside the part in `WingRib.airfoils.json`* — or the branch's never-saved
   warning in full.
2. A toolbar: **Refresh**, **New curve** (24 px), and at the right `16 rows`
   in 11.5 px `#6e6e6e`.
3. The tree, filling the remaining height, white with a `#d8d8d8` border.
   Header row 22 px `#f5f5f5`: `CURVE` and `STATE` (11 px uppercase
   `#6e6e6e`), the state column **84 px** wide. Rows 20 px, 12 px mono
   names, 11.5 px states, indented 16 px per level with a chevron on group
   rows. Colour by tag exactly as `_fill_tree` sets it: `linked` green,
   `attention` red (`missing`, `drifted`, `orphan`, `not pushed`),
   `untracked` `#777777`. A record row shows its leading edge (`0, 0, 120`)
   in the state column, in 600. The selected record and its children get a
   `#e9e9e9` background.
4. A card for the selected record, white with a `#d8d8d8` border, 8 × 10 px
   padding: its label (`sd7037-il`) and `2 curves linked` in green at the
   right; one 11.5 px line of its settings — *Leading edge at 0, 0, 0 · XY ·
   chord 175 mm · TE 0 mm · no offset* — read from the record's `ExportSpec`;
   and the sentence *Click a record to load its settings into the form.
   Export then updates it in place.* With nothing selected the card says
   *Export will make a new curve.*

Everything the flyout shows already exists in `gui.py` — `sw_status`,
`curve_tree`, `_fill_tree`, `_on_curve_selected`, `_new_curve`,
`_refresh_panel`. Only the container moves.

## 9. Implementation notes for tkinter

- **Segmented controls** are a `ttk.Frame` of `ttk.Radiobutton`s in a custom
  `Toolbutton`-derived style, or a small canvas widget; the visual spec in
  §5 is what matters, not the widget.
- **The flyout** is the existing `_build_solidworks` frame gridded in
  column 1 and toggled with `grid()` / `grid_remove()`; on toggle set the
  root's geometry wider or narrower by 360 × scale so the strip does not
  reflow.
- **Panel headers with readouts** are a `ttk.Frame` with two labels; the
  readout is a `StringVar` updated from the same handlers that already
  update `loaded_text`, `sw_status` and `editing_text`.
- **The tracker** is a frame that is `grid_remove`d when idle; its rows are
  three label pairs and a glyph drawn on a 16 px `Canvas`.
- **Fonts**: register the bundled TTFs before `tk.Tk()`; fall back to
  Segoe UI / Consolas silently. Add the files to `build.bat` with
  `--add-data`.
- Keep every existing variable, handler and test. The GUI spec test
  (`tests/test_gui_spec.py`) must still pass; nothing here changes what the
  form *means*, only how it is laid out.
- Test by looking: run it on Windows beside SolidWorks with the flyout open
  and closed, at 100 % and 150 % scaling, and compare against `renders/`.

## 10. Files

```
design/
  UI-DESIGN.md              this document
  renders/
    workbench-strip-with-flyout.png   the window, flyout open (2× scale)
    pick-feedback-states.png          the pick tracker: idle, active, done
  canvas/
    Main.dc.html            the window, as a static page
    PickStates.dc.html      the three pick states
    AccentOptions.dc.html   the four accents that were compared (Graphite won)
    FontOptions.dc.html     the six type pairings that were compared (4 won)
    PropertySheet.dc.html   direction A, passed over
    GuidedSteps.dc.html     direction B, passed over
    canvas.json             how the artboards sit on the canvas
    ui.py                   every colour, size and control, as small builders
    gen.py                  regenerates Main and PickStates
    gen_accents.py, gen_fonts.py   regenerate the two comparison sheets
```

The `.dc.html` files reference `./support.js`, which only exists inside the
canvas editor; a browser ignores the missing script and renders the page as
is.

## 11. As built

The window in `gui.py` is this document. What follows is only where the built
window differs from the spec above, and why — so that a reader comparing it
against `renders/` is not left wondering.

**Tk cannot do three of the things §3 to §5 ask for.** There is no corner
radius on a Tk widget, so every rectangle is square rather than 2 px round;
there is no letter-spacing, so panel headers are uppercase and bold rather
than tracked out; and a font has two weights rather than four, so 500 and 600
both land on bold or on normal, whichever is nearer. Everything else — the
colours, the heights, the borders, the 118 px label column, the 460 px strip
and the 360 px flyout — is exactly as given, which is why most of the controls
are drawn by hand in `widgets.py` rather than themed.

**A done step in the pick tracker shows what was read, not what it was
called.** §7 asks for `Top Plane · reference plane`. Nothing in the branch
learns an object's name: `swcom` reads geometry off whatever is selected and
hands back a normal, two ends, or a point. So a finished row reads
`normal 0, 0, 1`, `175 mm line` or `12.5, 0, 40` — the value that is about to
go into the fields, which is checkable against the model. Giving the rows
their names means teaching `swcom` to ask for one, which is a change to the
link and not to the window.

**The axis names stay ASCII.** §4 says the axis names carry a Unicode minus,
`−X`. The branch writes `-X`, and the dropdown shows the value the export
uses; a readout that spelled it differently from the control above it would be
worse than a hyphen.

**The Curves button stays live when the link is down.** §6 disables it. The
flyout is also where the reason is written out in full, and a user who had
closed it could not then get it back.

**The plane readout says `3 points · picked in SolidWorks`** rather than
naming the plane picked from, for the same reason the tracker rows do.

**The strip scrolls as a page when the screen is shorter than it is**, which
§2 allows for and calls the exception. It is not: with the pick tracker open
the strip is about 1225 px and a 1080p screen has about 1000 to give it, so
the footer — the status line and Export — would sit under the taskbar. The
window opens at the height the screen's work area actually has, and a
scrollbar appears beside the strip only while there is something to scroll.
Nothing inside the strip scrolls on its own.

The fonts are registered from `src/airfoil_converter/assets/fonts`, which
ships empty: see the README there. `tests/test_ui_text.py` covers every line
of derived text — the readouts, the tracker rows, the flyout's card — on a
machine with no display.
