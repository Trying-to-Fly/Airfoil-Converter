from ui import *

# ------------------------------------------------------------- pick block
def glyph(state):
    if state == "done":
        return f'<span style="width: 16px; height: 16px; flex-shrink: 0; border-radius: 50%; background: {OK}; display: inline-flex; align-items: center; justify-content: center; color: #ffffff;">{CHECK}</span>'
    if state == "active":
        return f'<span style="width: 16px; height: 16px; flex-shrink: 0; box-sizing: border-box; border-radius: 50%; border: 2px solid {ACCENT}; display: inline-flex; align-items: center; justify-content: center;"><span style="width: 6px; height: 6px; border-radius: 50%; background: {ACCENT};"></span></span>'
    return f'<span style="width: 16px; height: 16px; flex-shrink: 0; box-sizing: border-box; border-radius: 50%; border: 1.5px solid #b5b5b5; background: #ffffff;"></span>'

def step(state, name, detail, detail_color=None, trailing=""):
    name_style = f"font-weight: 600; color: {INK};" if state == "active" else (f"color: {INK};" if state == "done" else f"color: {FAINT};")
    color = detail_color or (INK if state == "active" else (LABEL if state == "done" else FAINT))
    return (f'<div style="display: flex; align-items: center; gap: 8px; min-height: 24px;">{glyph(state)}'
            f'<span style="width: 88px; flex-shrink: 0; font-size: 12.5px; {name_style}">{name}</span>'
            f'<span style="flex-grow: 1; font-size: 12px; color: {color};">{detail}</span>{trailing}</div>')

def pick_block_active():
    return (f'<div style="border: 1px solid #cfcfcf; border-radius: 2px; background: #fafafa; padding: 8px 10px; display: flex; flex-direction: column; gap: 4px;">'
            f'<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 2px;">'
            f'<span style="font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: {ACCENT};">Picking from SolidWorks</span>'
            f'<span style="font-size: 11px; color: {MUTED};">step 2 of 3 · 1:12 left</span>'
            f'<span style="margin-left: auto; display: flex; gap: 6px;">{button("Cancel", h=22)}</span></div>'
            + step("done", "Plane", f'<span style="font-family: {MONO};">Top Plane</span> · reference plane')
            + step("active", "Chord line", "That is a circular edge. Click a straight edge or a sketch line.", detail_color=ERR, trailing=button("Skip", h=22))
            + step("pending", "Leading edge", "optional · a sketch point or a corner")
            + f'<div style="display: flex; align-items: center; gap: 8px; margin-top: 4px;">'
              f'<span style="font-size: 11.5px; color: {MUTED}; flex-grow: 1;">Each click clears itself in SolidWorks — that is how one click is told from the last.</span>'
              f'{button("Use what I picked", h=22)}</div></div>')

def pick_block_idle():
    return row(button("Pick from SolidWorks", CURSOR), hint("Fills P1–P3 from a plane, a line and a point you click in the part."))

def pick_block_done():
    return row(button("Pick from SolidWorks", CURSOR),
               f'<span style="display: flex; align-items: center; gap: 6px; font-size: 12px; color: {OK};">{glyph("done")}Read the plane, the chord and the leading edge from SolidWorks.</span>')

# ------------------------------------------------------------- plane panel
def plane_panel(mode="XY", pick="active"):
    picked = mode == "3 points"
    main = mode in ("XY", "XZ", "YZ")
    readout = "XY · nose −X · up +Y" if main else "3 points · picked from Top Plane"
    pt_bg = "#ffffff" if picked else None
    def pfield(v):
        if picked:
            return (f'<div style="width: 82px; height: 26px; box-sizing: border-box; border: 1px solid #2b2b2b; border-radius: 2px; background: {pt_bg}; '
                    f'padding: 0 8px; display: flex; align-items: center; font-family: {MONO}; font-size: 12.5px; color: {INK};">{v}</div>')
        return field(v, disabled=True)
    pts = {"P1": ("12.5", "0", "40"), "P2": ("187.5", "0", "40"), "P3": ("12.5", "0", "-135")} if picked else {p: ("0", "0", "0") for p in ("P1", "P2", "P3")}
    rows = [
        seg(["XY", "XZ", "YZ", "3 points", "2 points", "Normal", "Loaded"], "3 points" if picked else "XY", h=24, disabled=("Loaded",)),
        row(label(""), '<span style="width: 22px; flex-shrink: 0;"></span>',
            *[f'<span style="width: 82px; text-align: center; font-size: 11px; color: #8f8f8f;">{a}</span>' for a in "XYZ"]),
    ]
    for i, p in enumerate(("P1", "P2", "P3")):
        rows.append(row(label("Points" if i == 0 else "", disabled=not picked),
                        f'<span style="width: 22px; flex-shrink: 0; color: {LABEL if picked else FAINT}; font-size: 11.5px; font-family: {MONO};">{p}</span>',
                        *[pfield(v) for v in pts[p]],
                        ))
    rows += [
        row(label("Constraint", disabled=True), dropdown("Perpendicular", "128px", disabled=True, mono=False),
            f'<span style="color: {FAINT}; font-size: 12.5px;">to</span>', dropdown("XY", "62px", disabled=True, mono=False)),
        f'<div style="height: 1px; background: {TRACK}; margin: 2px 0;"></div>',
        row(label("Chord runs along", disabled=picked), dropdown("P1 → P2" if picked else "−X", "92px" if picked else "64px", disabled=picked),
            f'<span style="color: {FAINT if picked else LABEL}; font-size: 12.5px; margin-left: 6px;">Up</span>',
            dropdown("+Y", "64px", disabled=picked), checkbox("Flip up", False, disabled=not picked, ml="8px")),
        row(label("Rotate in plane"), seg(["0°", "90°", "180°", "270°"], "0°", w="168px", mono=True)),
        row(label("Angle of attack"), field("0", "°"), hint("+ pitches the nose up")),
        f'<div style="height: 1px; background: {TRACK}; margin: 2px 0;"></div>',
        {"active": pick_block_active, "idle": pick_block_idle, "done": pick_block_done}[pick](),
    ]
    return panel("Plane", readout, rows)

def placement_panel(picked=False):
    vals = ("12.5", "0", "40") if picked else ("0", "0", "0")
    return panel("Placement", "where the 2D origin lands", [
        row(label("Leading edge at"), *[field(v, grow=True, prefix=a) for a, v in zip("XYZ", vals)],
            button("Pick", CURSOR, disabled=not picked)),
    ])

# ------------------------------------------------------------- other panels
source = panel("Source", "CSV or curve file", [
    row(field("D:\\Wings\\sd7037-il.csv", grow=True), button("Browse", FOLDER)),
    row(f'<svg width="110" height="14" viewBox="-3 -16 181 22" fill="none" stroke="#505050" stroke-width="1.8" stroke-linejoin="round"><path d="{AIRFOIL}"></path></svg>',
        f'<span style="font-size: 12px; color: #505050;"><span style="font-weight: 600; color: {INK};">SD7037-092-88</span> · 175 mm · 61 pts · camber line</span>', gap=10),
])
shape = panel("Shape", "mm of the finished part", [
    row(label("Curves"), checkbox("Airfoil surface", True), checkbox("Camber line", True, ml="12px")),
    row(label("Trailing edge"), seg(["Auto-close", "Leave open", "Split", "TE line"], "Auto-close")),
    row(label("TE thickness"), field("0", "mm"), checkbox("Keep chord after the cut", False, ml="6px")),
    row(label("Target chord"), field("175", "mm", placeholder=True), hint("blank keeps the source chord")),
    row(label("Offset"), field("none", "mm", placeholder=True), seg(["Inward", "Outward"], "Inward", w="140px")),
])
output = panel("Output", "Curve Through XYZ Points", [
    row(label("Format"), seg([".sldcrv", ".txt"], ".sldcrv", w="150px", mono=True)),
    row(label("Folder"), field("D:\\Wings", grow=True), button("Browse", FOLDER)),
])
SIDE_ICON = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="10" rx="1.5"></rect><path d="M10 3v10"></path></svg>'
INFO_ICON = f'<span style="width: 15px; flex-shrink: 0; display: inline-flex; justify-content: center; color: {ACCENT};"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="6.3"></circle><path d="M8 7.2v4"></path><path d="M8 4.8v0.2"></path></svg></span>'

def sw_bar(flyout_open=True):
    toggle = button("Hide curves" if flyout_open else "Curves", SIDE_ICON, h=20)
    header = (f'<span style="display: flex; align-items: center; gap: 8px;">'
              f'<span style="display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: #8f8f8f;"><span style="width: 7px; height: 7px; border-radius: 50%; background: {OK};"></span>2026 — WingRib.SLDPRT</span>{toggle}</span>')
    return panel("SolidWorks", "", [
        row(INFO_ICON, f'<span style="font-size: 12px; color: {INK};">Export will update <span style="font-family: {MONO};">sd7037-il_airfoil</span>, <span style="font-family: {MONO};">sd7037-il_camber</span>.</span>', gap=7),
        row(checkbox("Insert new curves into the open part", True)),
    ], extra_header=header)

GREY = "#777777"
TREE_ROWS = [
    ("sd7037-il", "0, 0, 0", OK, 0, True, True, True),
    ("sd7037-il_airfoil", "linked", OK, 1, True, False, False),
    ("sd7037-il_camber", "linked", OK, 1, True, False, False),
    ("sd7037-il (2)", "0, 0, 120", ERR, 0, False, True, True),
    ("sd7037-il_airfoil_2", "not pushed", ERR, 1, False, False, False),
    ("sd7037-il (3)", "0, 0, 240", OK, 0, False, True, True),
    ("sd7037-il_airfoil_3", "linked", OK, 1, False, False, False),
    ("sd7037-il_airfoil_3_te", "linked", OK, 1, False, False, False),
    ("sd7037-il_airfoil_3_joined", "linked", OK, 1, False, False, False),
    ("sd7037-il_camber_3", "linked", OK, 1, False, False, False),
    ("naca2412-il", "0, 0, 360", ERR, 0, False, True, True),
    ("naca2412-il_airfoil", "drifted", ERR, 1, False, False, False),
    ("naca2412-il_camber", "missing", ERR, 1, False, False, False),
    ("In the part, not tracked", "", GREY, 0, False, True, False),
    ("Curve1", "orphan", GREY, 1, False, False, False),
    ("Curve7", "orphan", GREY, 1, False, False, False),
]
def tree_list():
    rows = "".join(tree_row(n, v, c, depth=d, selected=s, caret=k, bold=b) for n, v, c, d, s, k, b in TREE_ROWS)
    return (f'<div style="flex-grow: 1; min-height: 0; border: 1px solid {BORDER_SOFT}; border-radius: 2px; background: #ffffff; overflow: hidden; display: flex; flex-direction: column;">'
            f'<div style="display: flex; align-items: center; height: 22px; flex-shrink: 0; padding: 0 8px 0 26px; background: #f5f5f5; border-bottom: 1px solid #e3e3e3; font-size: 11px; color: #6e6e6e; letter-spacing: 0.04em; text-transform: uppercase;">'
            f'<span style="flex-grow: 1;">Curve</span><span style="width: 84px; flex-shrink: 0; padding-left: 8px;">State</span></div>'
            f'<div style="display: flex; flex-direction: column; flex-grow: 1; overflow: hidden; padding: 2px 0;">{rows}</div></div>')

def flyout():
    return (f'<div style="width: 360px; flex-shrink: 0; box-sizing: border-box; border-left: 1px solid {BORDER_SOFT}; background: #f8f8f8; display: flex; flex-direction: column; gap: 8px; padding: 12px;">'
            f'<div style="display: flex; align-items: center; justify-content: space-between; height: 26px;">'
            f'<span style="font-size: 11.5px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #505050;">SolidWorks · curves in the part</span>'
            f'{button("Hide", h=22)}</div>'
            f'<div style="display: flex; flex-direction: column; gap: 2px;">'
            f'<span style="font-size: 12.5px; color: {INK}; font-weight: 600;">SolidWorks 2026 — WingRib.SLDPRT</span>'
            f'<span style="font-size: 11.5px; color: {MUTED};">Settings kept beside the part in <span style="font-family: {MONO};">WingRib.airfoils.json</span></span></div>'
            f'<div style="display: flex; gap: 6px; align-items: center;">{button("Refresh", h=24)}{button("New curve", h=24)}'
            f'<span style="margin-left: auto; font-size: 11.5px; color: {MUTED};">16 rows</span></div>'
            + tree_list() +
            f'<div style="display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; border: 1px solid {BORDER_SOFT}; border-radius: 2px; background: #ffffff;">'
            f'<div style="display: flex; align-items: center; justify-content: space-between;"><span style="font-size: 12.5px; font-weight: 600; color: {INK};">sd7037-il</span><span style="font-size: 11.5px; color: {OK};">2 curves linked</span></div>'
            f'<span style="font-size: 11.5px; color: {MUTED};">Leading edge at 0, 0, 0 · XY · chord 175 mm · TE 0 mm · no offset</span>'
            f'<span style="font-size: 11.5px; color: {MUTED};">Click a record to load its settings into the form. Export then updates it in place.</span></div>'
            f'</div>')

footer = (f'<div style="margin-top: auto; display: flex; align-items: center; justify-content: space-between; gap: 12px;">'
          f'<div style="display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: {OK}; min-width: 0;"><span style="width: 8px; height: 8px; flex-shrink: 0; border-radius: 50%; background: {OK};"></span>'
          f'<span style="overflow: hidden; white-space: nowrap; text-overflow: ellipsis;">CSV loaded. Set the plane and export.</span></div>'
          f'{button("Export", primary=True, h=32)}</div>')

HEAD = f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
  <style>
    body {{ margin: 0; background: #f0f0f0; }}
    a {{ color: {ACCENT}; }} a:hover {{ color: #000000; }}
  </style>
</helmet>
'''
TAIL = '''</x-dc>
</body>
</html>
'''
def column(height, body, gap=8, width=460):
    return f'<div style="width: {width}px; height: {height}px; flex-shrink: 0; box-sizing: border-box; background: #f0f0f0; color: {INK}; font-family: {SANS}; font-size: 13px; line-height: 1.3; display: flex; flex-direction: column; gap: {gap}px; padding: 12px;">\n' + body + '\n</div>'
def page(height, body, gap=8):
    return HEAD + column(height, body, gap) + '\n' + TAIL
def window(height, body, side):
    return HEAD + f'<div style="display: flex; width: 820px; height: {height}px; box-sizing: border-box; color: {INK}; font-family: {SANS}; font-size: 13px; line-height: 1.3; background: #f0f0f0;">' + column(height, body) + side + '</div>\n' + TAIL

main = window(1190, "\n".join([source, shape, plane_panel("XY", "active"), placement_panel(False), output, sw_bar(True), footer]), flyout())
open("Main.dc.html", "w").write(main)

def caption(text):
    return f'<div style="flex-shrink: 0; font-size: 12px; color: {MUTED}; padding: 2px 2px 0 2px;">{text}</div>'
states = page(1400, "\n".join([
    caption("1 · Idle — nothing picked yet. One button, one line saying what it will do."),
    plane_panel("XY", "idle"),
    caption("2 · Mid-pick — the plane was read, the last click was refused, the next is awaited. The countdown shows the 90-second timeout."),
    plane_panel("XY", "active"),
    caption("3 · Done — the form switched to 3 points, the picked values are tinted and tagged, the summary says exactly what was read."),
    plane_panel("3 points", "done"),
    placement_panel(True),
]), gap=10)
open("PickStates.dc.html", "w").write(states)
print("Main", len(main), "PickStates", len(states))
