import ui
from ui import *

OPTIONS = [
    ("1 · Windows blue", "#0067c0", "4px", "The OS's own accent. Follows what the rest of Windows does, so nothing about it looks designed."),
    ("2 · Navy", "#1f3a5f", "2px", "Dark and conservative. Still blue, but the shade of corporate engineering software, with nothing playful in it."),
    ("3 · Steel", "#4a5a68", "3px", "A grey with a hint of blue. Checked states stay findable without any real colour on the page."),
    ("4 · Graphite", "#2b2b2b", "2px", "No accent at all. Black controls on white; the only colour left is state — green linked, red attention."),
]

def glyph(state):
    A = ui.ACCENT
    if state == "done":
        return f'<span style="width: 16px; height: 16px; flex-shrink: 0; border-radius: 50%; background: {OK}; display: inline-flex; align-items: center; justify-content: center; color: #ffffff;">{CHECK}</span>'
    if state == "active":
        return f'<span style="width: 16px; height: 16px; flex-shrink: 0; box-sizing: border-box; border-radius: 50%; border: 2px solid {A}; display: inline-flex; align-items: center; justify-content: center;"><span style="width: 6px; height: 6px; border-radius: 50%; background: {A};"></span></span>'
    return '<span style="width: 16px; height: 16px; flex-shrink: 0; box-sizing: border-box; border-radius: 50%; border: 1.5px solid #b5b5b5; background: #ffffff;"></span>'

def step(state, name, detail, detail_color=None, trailing=""):
    name_style = f"font-weight: 600; color: {INK};" if state == "active" else (f"color: {INK};" if state == "done" else f"color: {FAINT};")
    color = detail_color or (INK if state == "active" else (LABEL if state == "done" else FAINT))
    return (f'<div style="display: flex; align-items: center; gap: 8px; min-height: 24px;">{glyph(state)}'
            f'<span style="width: 88px; flex-shrink: 0; font-size: 12.5px; {name_style}">{name}</span>'
            f'<span style="flex-grow: 1; font-size: 12px; color: {color};">{detail}</span>{trailing}</div>')

def pick_block():
    A = ui.ACCENT
    return (f'<div style="border: 1px solid #cfcfcf; border-radius: 4px; background: #fafafa; padding: 8px 10px; display: flex; flex-direction: column; gap: 4px;">'
            f'<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 2px;">'
            f'<span style="font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: {A};">Picking from SolidWorks</span>'
            f'<span style="font-size: 11px; color: {MUTED};">step 2 of 3</span>'
            f'<span style="margin-left: auto; display: flex; gap: 6px;">{button("Cancel", h=22)}</span></div>'
            + step("done", "Plane", f'<span style="font-family: {MONO};">Top Plane</span> · reference plane')
            + step("active", "Chord line", "Click a straight edge or a sketch line.", trailing=button("Skip", h=22))
            + step("pending", "Leading edge", "optional")
            + '</div>')

def fragment(name, accent, radius, note):
    ui.ACCENT = accent
    globals()["ACCENT"] = accent
    title = (f'<div style="display: flex; align-items: center; gap: 10px; padding: 0 2px;">'
             f'<span style="font-size: 14px; font-weight: 600; color: {INK};">{name}</span>'
             f'<span style="display: inline-flex; align-items: center; gap: 6px; font-family: {MONO}; font-size: 11.5px; color: {MUTED};"><span style="width: 14px; height: 14px; border-radius: 3px; background: {accent};"></span>{accent}</span></div>'
             f'<div style="font-size: 12px; color: {MUTED}; padding: 0 2px;">{note}</div>')
    shape = panel("Shape", "mm of the finished part", [
        row(label("Curves"), checkbox("Airfoil surface", True), checkbox("Camber line", True, ml="12px")),
        row(label("Trailing edge"), seg(["Auto-close", "Leave open", "Split", "TE line"], "Auto-close")),
        row(label("TE thickness"), field("0", "mm"), checkbox("Keep chord after the cut", False, ml="6px")),
    ])
    plane = panel("Plane", "XY · nose −X · up +Y", [
        seg(["XY", "XZ", "YZ", "3 points", "2 points", "Normal", "Loaded"], "XY", h=24, disabled=("Loaded",)),
        row(label("Chord runs along"), dropdown("−X", "64px"), f'<span style="color: {LABEL}; font-size: 12.5px; margin-left: 6px;">Up</span>', dropdown("+Y", "64px"), checkbox("Flip up", False, disabled=True, ml="8px")),
        pick_block(),
    ])
    tree = (f'<div style="border: 1px solid {BORDER_SOFT}; border-radius: 3px; overflow: hidden; display: flex; flex-direction: column;">'
            + tree_row("sd7037-il", "0, 0, 0", OK, caret=True, bold=True, selected=True)
            + tree_row("sd7037-il_airfoil", "linked", OK, depth=1, selected=True)
            + tree_row("sd7037-il (2)", "0, 0, 120", ERR, caret=True, bold=True)
            + tree_row("sd7037-il_airfoil_2", "not pushed", ERR, depth=1)
            + '</div>')
    sw = panel("SolidWorks", "2026 — WingRib.SLDPRT", [
        tree,
        row(checkbox("Insert new curves into the open part", True), f'<span style="margin-left: auto; display: flex; gap: 6px;">{button("Refresh", h=24)}{button("New curve", h=24)}</span>'),
    ])
    footer = (f'<div style="display: flex; align-items: center; justify-content: space-between; gap: 12px;">'
              f'<div style="display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: {OK};"><span style="width: 8px; height: 8px; border-radius: 50%; background: {OK};"></span>CSV loaded. Set the plane and export.</div>'
              f'{button("Export", primary=True, h=32)}</div>')
    html = "\n".join([title, shape, plane, sw, footer])
    # The option's corner radius, applied to every control and panel (not the round glyphs).
    html = html.replace("border-radius: 3px", f"border-radius: {radius}").replace("border-radius: 4px", f"border-radius: {radius}")
    return (f'<div style="width: 460px; flex-shrink: 0; box-sizing: border-box; background: #f0f0f0; border: 1px solid #d8d8d8; display: flex; flex-direction: column; gap: 8px; padding: 12px;">{html}</div>')

cells = "".join(fragment(*o) for o in OPTIONS)
html = f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600&family=Source+Code+Pro:wght@400;500&display=swap">
  <style>
    body {{ margin: 0; background: #e6e6e6; }}
    a {{ color: #1f1f1f; }} a:hover {{ color: #000000; }}
  </style>
</helmet>
<div style="width: 980px; height: 1400px; box-sizing: border-box; background: #e6e6e6; color: {INK}; font-family: {SANS}; font-size: 13px; line-height: 1.3; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; padding: 20px; align-content: start;">
{cells}
</div>
</x-dc>
</body>
</html>
'''
open("AccentOptions.dc.html", "w").write(html)
print("AccentOptions", len(html))
