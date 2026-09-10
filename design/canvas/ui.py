# Emits Main.dc.html (direction D) from small helpers, so every row shares one style.
AIRFOIL = "M175.00,-0.00 L174.43,-0.07 L172.74,-0.32 L170.01,-0.76 L166.32,-1.42 L161.79,-2.27 L156.49,-3.26 L150.53,-4.36 L143.96,-5.50 L136.85,-6.63 L129.26,-7.72 L121.26,-8.77 L112.94,-9.75 L104.40,-10.65 L95.71,-11.44 L86.99,-12.10 L78.30,-12.62 L69.76,-12.97 L61.43,-13.13 L53.39,-13.10 L45.72,-12.88 L38.48,-12.45 L31.74,-11.82 L25.55,-11.00 L19.97,-10.00 L15.03,-8.84 L10.76,-7.53 L7.18,-6.10 L4.31,-4.61 L2.16,-3.10 L0.73,-1.64 L0.04,-0.32 L0.22,0.69 L1.41,1.47 L3.57,2.15 L6.65,2.70 L10.63,3.11 L15.48,3.38 L21.15,3.53 L27.59,3.56 L34.74,3.48 L42.52,3.31 L50.85,3.07 L59.62,2.78 L68.75,2.44 L78.13,2.08 L87.63,1.71 L97.16,1.33 L106.60,0.96 L115.84,0.61 L124.78,0.29 L133.31,0.02 L141.32,-0.18 L148.69,-0.32 L155.32,-0.39 L161.12,-0.38 L166.00,-0.32 L169.88,-0.23 L172.71,-0.12 L174.42,-0.04 L175.00,-0.00"

SANS = "'Libre Franklin', 'Segoe UI', system-ui, sans-serif"
MONO = "'JetBrains Mono', Consolas, ui-monospace, monospace"
INK, LABEL, MUTED, FAINT = "#1f1f1f", "#3c3c3c", "#6e6e6e", "#9c9c9c"
BORDER, BORDER_SOFT, TRACK, DISABLED_BG = "#c6c6c6", "#d8d8d8", "#ebebeb", "#f4f4f4"
ACCENT, OK, ERR = "#2b2b2b", "#1a7f37", "#b42318"

FOLDER = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5h4.5A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z"></path></svg>'
CHECK = '<svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-7"></path></svg>'
def chevron(color): return f'<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"></path></svg>'
CARET_OPEN = '<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="#8f8f8f" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"></path></svg>'
CURSOR = '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 2.5l9.5 5.5-4 1-2.2 4z"></path></svg>'

def field(text, unit="", w="84px", disabled=False, placeholder=False, grow=False, prefix=""):
    width = "flex-grow: 1; min-width: 0;" if grow else f"width: {w};"
    bg = DISABLED_BG if disabled else "#ffffff"
    border = "#e3e3e3" if disabled else BORDER
    color = FAINT if (disabled or placeholder) else INK
    unit_html = f'<span style="color: #8f8f8f; font-family: {SANS}; font-size: 11.5px;">{unit}</span>' if unit else ""
    prefix_html = f'<span style="color: #8f8f8f; font-size: 11px; margin-right: 7px;">{prefix}</span>' if prefix else ""
    just = "space-between" if unit else "flex-start"
    return (f'<div style="{width} height: 26px; box-sizing: border-box; border: 1px solid {border}; border-radius: 2px; background: {bg}; '
            f'padding: 0 8px; display: flex; align-items: center; justify-content: {just}; font-family: {MONO}; font-size: 12.5px; color: {color}; overflow: hidden; white-space: nowrap;">'
            f'{prefix_html}<span>{text}</span>{unit_html}</div>')

def dropdown(text, w, disabled=False, mono=True):
    bg = DISABLED_BG if disabled else "#ffffff"
    border = "#e3e3e3" if disabled else BORDER
    color = FAINT if disabled else INK
    fam = MONO if mono else SANS
    return (f'<div style="width: {w}; height: 26px; box-sizing: border-box; border: 1px solid {border}; border-radius: 2px; background: {bg}; '
            f'padding: 0 6px 0 8px; display: flex; align-items: center; justify-content: space-between; font-family: {fam}; font-size: 12.5px; color: {color};">'
            f'<span>{text}</span>{chevron("#b5b5b5" if disabled else "#6e6e6e")}</div>')

def seg(items, selected, w=None, h=22, mono=False, disabled=()):
    width = f"width: {w};" if w else "flex-grow: 1;"
    fam = f" font-family: {MONO};" if mono else ""
    out = [f'<div style="display: flex; {width} box-sizing: border-box; border: 1px solid {BORDER_SOFT}; border-radius: 2px; background: {TRACK}; padding: 2px; gap: 2px;">']
    for it in items:
        if it == selected:
            out.append(f'<div style="flex-grow: 1; height: {h}px; border-radius: 2px; background: #ffffff; box-shadow: 0 0 0 1px {BORDER}; color: {INK}; font-weight: 600; display: flex; align-items: center; justify-content: center; font-size: 12px;{fam}">{it}</div>')
        else:
            color = "#b5b5b5" if it in disabled else "#505050"
            out.append(f'<div style="flex-grow: 1; height: {h}px; border-radius: 2px; color: {color}; display: flex; align-items: center; justify-content: center; font-size: 12px;{fam}">{it}</div>')
    out.append('</div>')
    return "".join(out)

def checkbox(label, checked, disabled=False, ml="0"):
    if checked:
        box = f'<span style="width: 15px; height: 15px; box-sizing: border-box; border: 1px solid {ACCENT}; border-radius: 2px; background: {ACCENT}; display: inline-flex; align-items: center; justify-content: center; color: #ffffff;">{CHECK}</span>'
    elif disabled:
        box = f'<span style="width: 15px; height: 15px; box-sizing: border-box; border: 1px solid {BORDER_SOFT}; border-radius: 2px; background: {DISABLED_BG};"></span>'
    else:
        box = '<span style="width: 15px; height: 15px; box-sizing: border-box; border: 1px solid #8f8f8f; border-radius: 2px; background: #ffffff;"></span>'
    color = f" color: {FAINT};" if disabled else ""
    return f'<span style="display: inline-flex; align-items: center; gap: 7px; margin-left: {ml};{color}">{box}{label}</span>'

def button(label, icon="", primary=False, disabled=False, h=26):
    if primary:
        style = f"background: {ACCENT}; color: #ffffff; border: 1px solid {ACCENT}; font-weight: 600;"
    elif disabled:
        style = f"background: {DISABLED_BG}; color: {FAINT}; border: 1px solid #e3e3e3; font-weight: 500;"
    else:
        style = f"background: #f8f8f8; color: {INK}; border: 1px solid {BORDER}; font-weight: 500;"
    return (f'<div style="height: {h}px; flex-shrink: 0; box-sizing: border-box; border-radius: 2px; padding: 0 10px; display: flex; align-items: center; gap: 6px; font-size: 12.5px; {style}">{icon}{label}</div>')

def label(text, disabled=False, w="118px"):
    return f'<span style="width: {w}; flex-shrink: 0; color: {FAINT if disabled else LABEL}; font-size: 12.5px;">{text}</span>'

def row(*parts, gap=8, align="center"):
    return f'<div style="display: flex; align-items: {align}; gap: {gap}px;">' + "".join(parts) + '</div>'

def hint(text):
    return f'<span style="font-size: 12px; color: {MUTED};">{text}</span>'

def panel(title, readout, body_rows, gap=6, extra_header=""):
    right = extra_header or (f'<span style="font-size: 11.5px; color: #8f8f8f;">{readout}</span>' if readout else "")
    return (f'<div style="background: #ffffff; border: 1px solid {BORDER_SOFT}; border-radius: 2px; overflow: hidden; flex-shrink: 0; display: flex; flex-direction: column;">'
            f'<div style="height: 26px; box-sizing: border-box; padding: 0 12px; background: #f5f5f5; border-bottom: 1px solid #e3e3e3; display: flex; align-items: center; justify-content: space-between;">'
            f'<span style="font-size: 11.5px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #505050;">{title}</span>{right}</div>'
            f'<div style="display: flex; flex-direction: column; gap: {gap}px; padding: 8px 12px;">' + "".join(body_rows) + '</div></div>')

def xyz(prefix_label, values, disabled, w="82px"):
    return [field(v, w=w, disabled=disabled) for v in values]

def tree_row(name, value, color, depth=0, selected=False, caret=False, bold=False):
    pad = 10 + depth * 16
    bg = "background: #e9e9e9;" if selected else ""
    weight = "font-weight: 600;" if bold else ""
    caret_html = f'<span style="width: 12px; display: inline-flex; align-items: center; justify-content: center; margin-right: 4px;">{CARET_OPEN if caret else ""}</span>'
    return (f'<div style="display: flex; align-items: center; height: 20px; padding: 0 8px 0 {pad}px; {bg} font-size: 12px; color: {color};">'
            f'{caret_html}<span style="flex-grow: 1; font-family: {MONO}; font-size: 12px; {weight} overflow: hidden; white-space: nowrap; text-overflow: ellipsis;">{name}</span>'
            f'<span style="width: 84px; flex-shrink: 0; font-size: 11.5px; text-align: left; padding-left: 8px;">{value}</span></div>')

