import re, ui
from ui import *

# (name, sans stack, mono stack, size shift in px, note)
OPTIONS = [
    ("1 · IBM Plex Sans + Plex Mono", "'IBM Plex Sans', 'Segoe UI', system-ui, sans-serif", "'IBM Plex Mono', Consolas, ui-monospace, monospace", 0,
     "IBM's corporate face. Rational and slightly technical, with numerals built for tables."),
    ("2 · Source Sans 3 + Source Code Pro", "'Source Sans 3', 'Segoe UI', system-ui, sans-serif", "'Source Code Pro', Consolas, ui-monospace, monospace", 0.5,
     "Adobe's neutral humanist. Compact and very readable small, so dense rows stay calm."),
    ("3 · Fira Sans + Fira Mono", "'Fira Sans', 'Segoe UI', system-ui, sans-serif", "'Fira Mono', Consolas, ui-monospace, monospace", -0.5,
     "Wider and sturdier, the developer-tool feel. Reads well on a monitor across the desk."),
    ("4 · Libre Franklin + JetBrains Mono", "'Libre Franklin', 'Segoe UI', system-ui, sans-serif", "'JetBrains Mono', Consolas, ui-monospace, monospace", 0,
     "Franklin Gothic's industrial heritage: the face of drawings and datasheets, without ornament."),
    ("5 · Red Hat Text + Red Hat Mono", "'Red Hat Text', 'Segoe UI', system-ui, sans-serif", "'Red Hat Mono', Consolas, ui-monospace, monospace", 0,
     "Modern enterprise software. Geometric bones kept restrained; the most contemporary of the six."),
    ("6 · Noto Sans + Noto Sans Mono", "'Noto Sans', 'Segoe UI', system-ui, sans-serif", "'Noto Sans Mono', Consolas, ui-monospace, monospace", -0.5,
     "Google's plain workhorse. The least characterful option, which is the point: nothing to notice."),
]
FONTS_URL = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500"
             "&family=Source+Sans+3:wght@400;500;600&family=Source+Code+Pro:wght@400;500"
             "&family=Fira+Sans:wght@400;500;600&family=Fira+Mono:wght@400;500"
             "&family=Libre+Franklin:wght@400;500;600&family=JetBrains+Mono:wght@400;500"
             "&family=Red+Hat+Text:wght@400;500;600&family=Red+Hat+Mono:wght@400;500"
             "&family=Noto+Sans:wght@400;500;600&family=Noto+Sans+Mono:wght@400;500&display=swap")

def glyph(state):
    if state == "done":
        return f'<span style="width: 16px; height: 16px; flex-shrink: 0; border-radius: 50%; background: {OK}; display: inline-flex; align-items: center; justify-content: center; color: #ffffff;">{CHECK}</span>'
    if state == "active":
        return f'<span style="width: 16px; height: 16px; flex-shrink: 0; box-sizing: border-box; border-radius: 50%; border: 2px solid {ACCENT}; display: inline-flex; align-items: center; justify-content: center;"><span style="width: 6px; height: 6px; border-radius: 50%; background: {ACCENT};"></span></span>'
    return '<span style="width: 16px; height: 16px; flex-shrink: 0; box-sizing: border-box; border-radius: 50%; border: 1.5px solid #b5b5b5; background: #ffffff;"></span>'

def step(state, name, detail, trailing=""):
    name_style = f"font-weight: 600; color: {INK};" if state == "active" else (f"color: {INK};" if state == "done" else f"color: {FAINT};")
    color = INK if state == "active" else (LABEL if state == "done" else FAINT)
    return (f'<div style="display: flex; align-items: center; gap: 8px; min-height: 24px;">{glyph(state)}'
            f'<span style="width: 88px; flex-shrink: 0; font-size: 12.5px; {name_style}">{name}</span>'
            f'<span style="flex-grow: 1; font-size: 12px; color: {color};">{detail}</span>{trailing}</div>')

def pick_block(mono):
    return (f'<div style="border: 1px solid #cfcfcf; border-radius: 2px; background: #fafafa; padding: 8px 10px; display: flex; flex-direction: column; gap: 4px;">'
            f'<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 2px;">'
            f'<span style="font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: {ACCENT};">Picking from SolidWorks</span>'
            f'<span style="font-size: 11px; color: {MUTED};">step 2 of 3</span>'
            f'<span style="margin-left: auto; display: flex; gap: 6px;">{button("Cancel", h=22)}</span></div>'
            + step("done", "Plane", f'<span style="font-family: {mono};">Top Plane</span> · reference plane')
            + step("active", "Chord line", "Click a straight edge or a sketch line.", trailing=button("Skip", h=22))
            + step("pending", "Leading edge", "optional")
            + '</div>')

def fragment(name, sans, mono, shift, note):
    ui.SANS, ui.MONO = sans, mono
    globals()["SANS"], globals()["MONO"] = sans, mono
    title = (f'<div style="display: flex; flex-direction: column; gap: 3px; padding: 0 2px;">'
             f'<span style="font-size: 14px; font-weight: 600; color: {INK};">{name}</span>'
             f'<span style="font-size: 12px; color: {MUTED};">{note}</span></div>')
    shape = panel("Shape", "mm of the finished part", [
        row(label("Curves"), checkbox("Airfoil surface", True), checkbox("Camber line", True, ml="12px")),
        row(label("Trailing edge"), seg(["Auto-close", "Leave open", "Split", "TE line"], "Auto-close")),
        row(label("TE thickness"), field("1.5", "mm"), checkbox("Keep chord after the cut", False, ml="6px")),
        row(label("Target chord"), field("275", "mm"), hint("blank keeps the source chord")),
    ])
    plane = panel("Plane", "XY · nose −X · up +Y", [
        seg(["XY", "XZ", "YZ", "3 points", "2 points", "Normal", "Loaded"], "XY", h=24, disabled=("Loaded",)),
        row(label("Chord runs along"), dropdown("−X", "64px"), f'<span style="color: {LABEL}; font-size: 12.5px; margin-left: 6px;">Up</span>', dropdown("+Y", "64px"), checkbox("Flip up", False, disabled=True, ml="8px")),
        row(label("Angle of attack"), field("2.5", "°"), hint("+ pitches the nose up")),
        pick_block(mono),
    ])
    tree = (f'<div style="border: 1px solid {BORDER_SOFT}; border-radius: 2px; overflow: hidden; display: flex; flex-direction: column;">'
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
              f'<div style="display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: {OK};"><span style="width: 8px; height: 8px; border-radius: 50%; background: {OK};"></span>Wrote sd7037-il_airfoil.sldcrv, sd7037-il_camber.sldcrv</div>'
              f'{button("Export", primary=True, h=32)}</div>')
    html = "\n".join([shape, plane, sw, footer])
    if shift:
        html = re.sub(r"font-size: ([0-9.]+)px", lambda m: f"font-size: {float(m.group(1)) + shift:g}px", html)
    return (f'<div style="width: 460px; flex-shrink: 0; box-sizing: border-box; background: #f0f0f0; border: 1px solid #d8d8d8; display: flex; flex-direction: column; gap: 8px; padding: 12px; font-family: {sans};">{title}{html}</div>')

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
  <link rel="stylesheet" href="{FONTS_URL}">
  <style>
    body {{ margin: 0; background: #e6e6e6; }}
    a {{ color: #1f1f1f; }} a:hover {{ color: #000000; }}
  </style>
</helmet>
<div style="width: 1460px; height: 1560px; box-sizing: border-box; background: #e6e6e6; color: {INK}; font-size: 13px; line-height: 1.3; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; padding: 20px; align-content: start;">
{cells}
</div>
</x-dc>
</body>
</html>
'''
open("FontOptions.dc.html", "w").write(html)
print("FontOptions", len(html))
