"""The words the window puts on screen, worked out without a window.

The readouts in the panel headers, the pick tracker's rows and the flyout's
card are all *derived* text: one short line standing for the state of a panel.
Deriving them here rather than in :mod:`gui` keeps them testable on a machine
with no display — the same bargain :mod:`pick` and :mod:`swlink` already make —
and it keeps :mod:`gui` to laying widgets out and wiring handlers.

Nothing here touches tkinter, and nothing here holds state.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable, List, Optional, Sequence, Tuple

from . import export, geometry, store, wing
from .export import (
    MODE_2POINTS,
    MODE_3POINTS,
    MODE_LOADED,
    MODE_NORMAL,
    THICKNESS_BLENDED,
    ExportSpec,
    WingSpec,
)
from .pick import LINE, PLANE, POINT, Pick
from .swcom import PickedLine, PickedPlane, PickedPoint

# What each step of a pick is called on its own row, and what it is waiting for
# said as a noun phrase rather than as an instruction — a row that is still to
# come is not telling the user to do anything yet.
STEP_NAMES = {PLANE: "Plane", LINE: "Chord line", POINT: "Leading edge"}
STEP_WANTS = {
    PLANE: "a reference plane, or a flat face",
    LINE: "a straight edge, or a sketch line",
    POINT: "a sketch point, or a corner",
}

PLANE_LABELS = {
    MODE_3POINTS: "3 points",
    MODE_2POINTS: "2 points",
    MODE_NORMAL: "Normal",
    MODE_LOADED: "Loaded",
}


def _number(value: float) -> str:
    return f"{value:g}"


def _point(values: Iterable[float]) -> str:
    return ", ".join(_number(float(v)) for v in values)


# ------------------------------------------------------------- the readouts


def plane_readout(mode: str, chord_axis: str, up_axis: str, constraint: str,
                  main_plane: str, picked: bool = False) -> str:
    """The Plane panel's header line: what the plane is, in one breath.

    On a main plane the two axis pickers decide which way the section faces,
    and that is the thing a user gets wrong, so the readout says it outright
    rather than making them read two dropdowns halfway down the panel.
    """
    if mode in geometry.MAIN_PLANES:
        return f"{mode} · nose {chord_axis} · up {up_axis}"
    if mode == MODE_3POINTS:
        return "3 points · picked in SolidWorks" if picked else "3 points"
    if mode == MODE_2POINTS:
        return f"2 points · {constraint.lower()} to {main_plane}"
    if mode == MODE_NORMAL:
        return "Normal to line"
    if mode == MODE_LOADED:
        return "As loaded"
    return mode


def placement_readout(mode: str, manual: bool = False, picked: bool = False) -> str:
    """Where the 2D origin lands — the old ``le_hint``, shortened to a readout."""
    if picked:
        return "picked in SolidWorks"
    if manual:
        return "typed in by hand"
    if mode == MODE_LOADED:
        return "the curve's own leading edge"
    if mode in (MODE_3POINTS, MODE_2POINTS, MODE_NORMAL):
        return "defaults to P1"
    return "where the 2D origin lands"


def source_line(name: str, chord: float, points: int, has_camber: bool,
                is_curve: bool) -> Tuple[str, str]:
    """The loaded file, as a name and the rest of the line.

    Two pieces because the name is set in bold and the rest is not; joining
    them here and splitting them in the window would be worse.
    """
    parts = [f"{_number(chord)} mm", f"{points} pts"]
    if is_curve:
        parts.append("curve file")
    parts.append("camber line" if has_camber else "no camber line")
    return name, " · ".join(parts)


def link_headline(available: bool, snapshot: Optional[dict]) -> Tuple[str, str]:
    """The SolidWorks bar's readout, and which colour its dot takes.

    The state is one of ``ok``, ``bad`` or ``off``: connected to a part, tried
    and failed, or never able to try because pywin32 is not installed.
    """
    if not available:
        return "pywin32 not installed", "off"
    if snapshot is None:
        return "not reachable", "bad"
    label = snapshot.get("version") or "SolidWorks"
    if snapshot.get("newer_than_tested"):
        label += " (newer than tested)"
    title = snapshot.get("title")
    if not title:
        return f"{label} — no document open", "bad"
    if not snapshot.get("is_part"):
        return f"{label} — {title} is not a part", "bad"
    return f"{label} — {title}", "ok"


def link_detail(snapshot: Optional[dict], sidecar_path: str, fallback: str) -> str:
    """The line under the headline in the flyout: where the settings live.

    Off a saved part there is nothing to say about a sidecar, so the full
    sentence from ``sw_status`` is shown instead — that is the case where the
    user most needs the reason.
    """
    if snapshot is None or not snapshot.get("is_part"):
        return fallback
    if not snapshot.get("path"):
        return (
            "This part has never been saved, so its curve settings are only "
            "remembered until you close the app."
        )
    if not sidecar_path:
        return fallback
    name = sidecar_path.replace("\\", "/").rsplit("/", 1)[-1]
    return f"Settings kept beside the part in {name}"


# --------------------------------------------------------- the pick tracker


@dataclass(frozen=True)
class TrackerRow:
    """One step of a pick, as the tracker draws it."""

    name: str
    state: str          # done | active | pending
    detail: str
    error: bool = False
    skippable: bool = False


def countdown(seconds: float) -> str:
    """``1:12 left``. Whole seconds; a pick that is over reads ``0:00 left``."""
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d} left"


def tracker_title(session: Pick, seconds_left: float) -> str:
    """``step 2 of 3 · 1:12 left`` — the countdown is the pick's own timeout."""
    total = len(session.steps)
    step = min(session.index + 1, total)
    return f"step {step} of {total} · {countdown(seconds_left)}"


def picked_detail(picked) -> str:
    """What a finished step actually read, as a number rather than a promise.

    The design asks for the clicked object's *name* here (``Top Plane``). The
    branch's :mod:`swcom` never learns a name — it reads geometry off whatever
    was selected and hands back three kinds of value — so what is shown instead
    is the value itself, which is the thing that will end up in the fields and
    is checkable against the model.
    """
    if isinstance(picked, PickedPlane):
        n = picked.normal
        size = sqrt(sum(c * c for c in n)) or 1.0
        return "normal " + _point(c / size for c in n)
    if isinstance(picked, PickedLine):
        span = tuple(b - a for a, b in zip(picked.start, picked.end))
        return f"{_number(sqrt(sum(c * c for c in span)))} mm line"
    if isinstance(picked, PickedPoint):
        return _point(picked.where)
    return ""


def tracker_rows(session: Pick) -> List[TrackerRow]:
    """Every step of the pick, in order, in the state the tracker draws it in."""
    rows: List[TrackerRow] = []
    for position, step in enumerate(session.steps):
        wants = step.wants
        name = STEP_NAMES.get(wants, wants)
        if position < session.index:
            rows.append(TrackerRow(
                name, "done", picked_detail(getattr(session, wants, None))
            ))
        elif position == session.index:
            refusal = session.refusal
            rows.append(TrackerRow(
                name, "active", refusal or step.ask, bool(refusal), step.optional
            ))
        else:
            prefix = "optional · " if step.optional else ""
            rows.append(TrackerRow(name, "pending", prefix + STEP_WANTS.get(wants, "")))
    return rows


# ------------------------------------------------------------- the flyout's
#                                                                record card


# An adopted record knows what its curves are called and nothing else. Both
# lines say so outright: a card that showed the defaults instead would read as
# a set of settings somebody chose.
ADOPTED_SETTINGS = "Taken over from the part. Its settings were not remembered."
ADOPTED_HINT = (
    "Export will rebuild these curves from the form as it stands now, keeping "
    "their names. From then on the settings are kept with the part."
)


def record_summary(spec: ExportSpec) -> str:
    """One line of a remembered export's settings, for the card under the tree."""
    parts = [f"Leading edge at {', '.join(spec.leading_edge)}"]
    parts.append(PLANE_LABELS.get(spec.plane_mode, spec.plane_mode))
    parts.append(f"chord {spec.target_chord} mm" if spec.target_chord else "source chord")
    parts.append(f"TE {spec.te_thickness or '0'} mm")
    parts.append(
        f"offset {spec.offset} mm {spec.offset_dir.lower()}" if spec.offset else "no offset"
    )
    return " · ".join(parts)


def wing_summary(spec: WingSpec) -> str:
    """One line of a remembered wing's settings, for the card and the Wing tab."""
    parts = [f"{len(spec.ribs)} rib{'' if len(spec.ribs) == 1 else 's'}"]
    edges = [
        f"{name} {'from a file' if source else 'straight'}"
        for name, source in (("LE", spec.le_source), ("TE", spec.te_source))
    ]
    parts.append(", ".join(edges))
    parts.append(f"root {spec.root_end}, tip {spec.tip_end}")
    parts.append(
        f"offset {spec.offset} mm {spec.offset_dir.lower()}" if spec.offset.strip()
        else "no offset"
    )
    return " · ".join(parts)


def wing_place(spec: WingSpec) -> str:
    """What the tree's state column shows for a wing, where a rib shows its leading edge."""
    if not spec.offset.strip():
        return "wing"
    return f"wing, {spec.offset} mm {spec.offset_dir.lower()}"


def curves_linked(states: Sequence["store.CurveState"]) -> Tuple[str, bool]:
    """``2 curves linked``, or what is wrong with them. True when all is well."""
    if not states:
        return "no curves yet", True
    wrong = [s for s in states if s.needs_attention]
    if not wrong:
        count = len(states)
        return f"{count} curve{'' if count == 1 else 's'} linked", True
    if len(wrong) == 1:
        return wrong[0].state, False
    return f"{len(wrong)} need attention", False


# ------------------------------------------------------------ the Wing tab
#
# Some of the Wing tab's lines set curve names in mono inside a sentence, which
# one Tk label cannot do. Those come back as *runs*: ``(text, style)`` pairs,
# where the style is one of the four below and the window decides what each
# looks like. :func:`plain` joins them back up, for a test or a status line.

Run = Tuple[str, str]
PLAIN = ""
MONO = "mono"          # a curve name, a number, a part
STRONG = "strong"      # the one figure a block is about
QUIET = "quiet"        # said after it, smaller and muted

WING_FOLDER = "Wing Curves"

RIB_ADOPTED = "taken over · no settings"
RIBS_HINT = "the root and the tip at least"
NO_RIBS = "No ribs in the part's record yet. Export them on the Airfoil tab first."

EDGE_HINTS = {"le": "runs through every rib", "te": "corner to corner"}
# What a straight edge's disabled path field says, so that it never looks like
# a file somebody forgot to choose.
EDGE_LINE_TEXT = "a line from the root rib to the tip rib"

END_HINTS = {
    wing.OPEN: "carries on, as at a centreline",
    wing.CLOSED: "a flat face, moved by an offset",
}

COPY_HINT = "starts a wing offset from this one"
NAME_KEPT_HINT = "keeps its curves' names"

RESULT_IDLE = (
    "Check works the wing out without exporting it, and says the wall it expects."
)
EXPORT_FOOT = (
    "A loft already in the part is left as it is, since it follows its curves. "
    "Delete it to loft again."
)

PHASE_WORK = "Work out"
PHASE_SEND = "Send curves"
PHASE_WRITE = "Write files"
PHASE_LOFT = "Loft"


def plain(runs: Sequence[Run]) -> str:
    return "".join(text for text, _ in runs)


def _mm(value: float, places: int = 1) -> str:
    return f"{value:.{places}f} mm"


def _signed(value: float) -> str:
    """``+0.8 %`` or ``−2.0 %``, with a real minus: it sits beside a range."""
    return f"{value:+.1f}".replace("-", "\u2212") + " %"


def wing_readout(editing: str) -> str:
    """The Wing panel's header: which wing Export is aimed at."""
    return f"editing {editing}" if editing else "new wing"


def rib_columns(record: "store.ExportRecord") -> Tuple[str, str]:
    """A rib's row in the list: its name, and where its leading edge stands.

    The two halves of the Wing tab's ``rib_label``, for a list that sets them
    in two columns the way the flyout's tree does.
    """
    name = record.stem if record.name_index <= 1 else f"{record.stem} ({record.name_index})"
    if record.adopted:
        return name, RIB_ADOPTED
    return name, _point_text(record.spec.leading_edge)


def _point_text(values: Iterable[str]) -> str:
    """Each coordinate as a number would be written, so a float never prints in full."""
    def one(value) -> str:
        try:
            return _number(float(value))
        except (TypeError, ValueError):
            return str(value)
    return ", ".join(one(v) for v in values)


def ribs_readout(ticked: int, total: int, have_record: bool = True) -> str:
    if not have_record:
        return "no part's record open"
    if not total:
        return "no ribs yet"
    return f"{ticked} of {total} ticked"


def edges_readout(le_from_file: bool, te_from_file: bool) -> str:
    """``LE from a file · TE straight``."""
    return " · ".join(
        f"{name} {'from a file' if from_file else 'straight'}"
        for name, from_file in (("LE", le_from_file), ("TE", te_from_file))
    )


def ends_readout(root: str, tip: str) -> str:
    return f"root {root} · tip {tip}"


def offset_readout(offset: str, direction: str) -> str:
    if not offset.strip():
        return "none · the wing itself"
    return f"{offset.strip()} mm {direction.lower()}"


def surface_guide_count(offset: bool) -> int:
    """How many surface guides an export makes: a set per surface, per fraction.

    An offset wing's start further back than the wing's own, which is why the
    two counts differ; both come from :mod:`wing` so that they cannot drift
    from what the export actually writes.
    """
    fractions = [
        f for f in wing.SURFACE_GUIDES if not offset or f >= wing.OFFSET_GUIDES_FROM
    ]
    return 2 * len(fractions)


def offset_hint(offset: str) -> str:
    """The Offset panel's one line, which says what the settings above will export."""
    if not offset.strip():
        return (f"Blank exports the wing's own sections, its edges and "
                f"{surface_guide_count(False)} surface guides.")
    return (f"The offset wing's sections go in, with its edges and "
            f"{surface_guide_count(True)} surface guides through every one.")


def _and(parts: List[Run]) -> List[Run]:
    """Names joined as a list is said: ``a, b and c``."""
    out: List[Run] = []
    for k, part in enumerate(parts):
        if k:
            out.append((" and " if k == len(parts) - 1 else ", ", PLAIN))
        out.append(part)
    return out


def export_line(stem: str, index: int, editing: str = "", live: int = 0,
                te_line: bool = False, offset: bool = False) -> List[Run]:
    """What Export will do to the part, in the wing's own terms.

    ``editing`` names the wing being edited, whose ``live`` curves are updated
    in place; otherwise the names are the ones a new wing called ``stem``
    will get.
    """
    if editing:
        if not live:
            return [("Export will update ", PLAIN), (editing, MONO), (" in place.", PLAIN)]
        return [(f"Export will update the {live} curves of ", PLAIN),
                (editing, MONO), (" in place.", PLAIN)]
    if not stem.strip():
        return [("Export will make a new wing, once it has a name.", PLAIN)]
    try:
        base = export.wing_base(stem, index)
        le = export.wing_feature_name(stem, export.ROLE_WING_LE, index)
        te_roles = ((export.ROLE_WING_TE_UPPER, export.ROLE_WING_TE_LOWER) if te_line
                    else (export.ROLE_WING_TE,))
        tes = [export.wing_feature_name(stem, role, index) for role in te_roles]
    except export.InputError:
        return [("Export will make a new wing.", PLAIN)]
    names: List[Run] = [(le, MONO)] + [(te, MONO) for te in tes]
    under = [(" under ", PLAIN), (WING_FOLDER, STRONG), (".", PLAIN)]
    guides = f"{surface_guide_count(offset)} surface guides"
    return ([("Export will make ", PLAIN), (base, MONO), ("'s sections, ", PLAIN)]
            + _and(names + [(guides, PLAIN)]) + under)


# -- the Result panel


def result_readout(state: str, linked: str = "") -> str:
    """``not checked yet``, ``checked · 36 curves linked``, ``exporting``..."""
    if state in ("checked", "exported"):
        return f"{state} · {linked}" if linked else state
    return {
        "idle": "not checked yet",
        "checking": "checking",
        "exporting": "exporting",
        "lofting": "lofting",
        "stopped": "stopped",
    }.get(state, state)


def offset_words(offset_mm: float) -> str:
    """``2.5 mm inward`` from the signed distance a build carries."""
    if not offset_mm:
        return "no offset"
    return f"{abs(offset_mm):g} mm {'inward' if offset_mm < 0 else 'outward'}"


def result_subtitle(offset_mm: float, exported: bool) -> str:
    return f"{offset_words(offset_mm)} · {'exported' if exported else 'nothing exported'}"


@dataclass(frozen=True)
class ResultRow:
    """A label and a value in the checked block; ``big`` is the wall."""

    label: str
    runs: Tuple[Run, ...]
    big: bool = False
    error: bool = False


def wall_text(thinnest: float, thickest: float, asked: float) -> Tuple[str, str]:
    """``2.45 – 2.52 mm`` and ``as modelled · −2.0 % / +0.8 %``."""
    asked = abs(asked)
    spread = f"{thinnest:.2f} \u2013 {thickest:.2f} mm"
    if not asked:
        return spread, "as modelled"
    low = 100.0 * (thinnest - asked) / asked
    high = 100.0 * (thickest - asked) / asked
    return spread, f"as modelled · {_signed(low)} / {_signed(high)}"


def _rule_words(thickness: str) -> str:
    return ("thickness blended root to tip" if thickness == THICKNESS_BLENDED
            else "airfoil scaled to the chord")


def result_rows(build) -> List[ResultRow]:
    """The checked block's lines, read off a :class:`wing_build.WingBuild`.

    Without an offset there is little to say, and :func:`wing_build.describe`
    already says it in two lines; those are shown as they are.
    """
    from . import wing_build  # the wing's maths is only needed once there is a build

    result = build.offset
    if result is None:
        return [ResultRow("", ((line, PLAIN),)) for line in wing_build.describe(build)]
    model = build.model
    rows = [
        ResultRow("Ribs", ((f"{len(model.sections)}", MONO), (" over ", PLAIN),
                           (_mm(model.span), MONO), (" of span", PLAIN))),
        ResultRow("Shape between", ((_rule_words(model.loft.thickness), PLAIN),)),
    ]
    sections: List[Run] = [(f"{len(result.sections)}", MONO)]
    rims = sum(1 for sec in result.sections if sec.station.rim)
    if rims:
        sections.append((f", {rims} of them rounding a closed end", PLAIN))
    if result.steep_from is not None:
        sections += [(", solved in 3D from ", PLAIN),
                     (f"{round(result.steep_from, 1):g} mm", MONO),
                     (f" where the edges sweep past {wing.STEEP_SWEEP:g}\u00b0", PLAIN)]
    rows.append(ResultRow("Sections", tuple(sections)))
    root, tip = result.te_shift
    rows.append(ResultRow("Trailing edge", (
        ("moves ", PLAIN), (_mm(root), MONO), (" at the root, ", PLAIN),
        (_mm(tip), MONO), (" at the tip", PLAIN),
    )))
    report = result.report
    if report is not None:
        spread, how = wall_text(report.thinnest, report.thickest, result.offset)
        rows.append(ResultRow("Wall", ((spread, STRONG), ("  " + how, QUIET)), big=True))
        if report.outside:
            rows.append(ResultRow("Outside", (
                (f"{report.outside} checked points fell outside the skin", PLAIN),
            ), error=True))
    return rows


def loft_footer(section_names: Sequence[str], edge_names: Sequence[str],
                guide_count: int, loft_name: str) -> List[Run]:
    """What to loft through, and that the Loft button does it here.

    ``edge_names`` are the wing's edge curves, named; its ``guide_count``
    surface guides are counted rather than listed.
    """
    guides: List[Run] = [(name, MONO) for name in edge_names]
    if guide_count:
        guides.append((f"the {guide_count} surface guides", PLAIN))
    if len(section_names) == 2:
        runs: List[Run] = [("Loft ", PLAIN)] + _and(
            [(section_names[0], MONO), (section_names[1], MONO)]
        )
    elif section_names:
        runs = [("Loft ", PLAIN), (section_names[0], MONO), (" to ", PLAIN),
                (section_names[-1], MONO),
                (f" ({len(section_names)} sections, in order)", PLAIN)]
    else:
        runs = [("Loft the ribs", PLAIN)]
    runs.append((" with ", PLAIN))
    runs += _and(guides)
    runs += [(". ", PLAIN), ("Loft", STRONG), (" does it here as ", PLAIN),
             (loft_name, MONO), (".", PLAIN)]
    return runs


# -- the export tracker


@dataclass(frozen=True)
class PhaseRow:
    """One phase of an export, as the tracker draws it."""

    name: str
    state: str          # done | active | pending
    runs: Tuple[Run, ...]
    error: bool = False


def phase_title(rows: Sequence[PhaseRow]) -> str:
    """``step 2 of 3``: the active phase, or the last one once all are done."""
    total = len(rows)
    for position, row in enumerate(rows):
        if row.state != "done":
            return f"step {position + 1} of {total}"
    return f"step {total} of {total}"


def work_detail(build) -> List[Run]:
    """``9 sections · wall 2.45 – 2.52 mm``, or what a wing without an offset made."""
    result = build.offset
    guides = sum(1 for c in build.curves if c.role == export.ROLE_WING_SURFACE)
    if result is None:
        return [(f"{len(build.model.sections)} ribs · {guides} surface guides", PLAIN)]
    runs: List[Run] = [(f"{len(result.sections)} sections", PLAIN)]
    if result.report is not None:
        spread, _ = wall_text(result.report.thinnest, result.report.thickest, result.offset)
        runs += [(" · wall ", PLAIN), (spread, MONO)]
    return runs


def send_detail(count: int, part: str, pushed: bool = True, folder: str = "") -> List[Run]:
    """``36 curves to WingRib.SLDPRT, under Wing Curves``, or the files written instead."""
    if not pushed:
        return [(f"{count} files written to ", PLAIN), (folder, MONO)]
    what = f"{count} curves" if count else "the curves"
    return [(f"{what} to ", PLAIN), (part or "the part", MONO),
            (f", under {WING_FOLDER}", PLAIN)]


def loft_detail(name: str, profiles: int = 0, guides: int = 0) -> List[Run]:
    """``wing_inner_loft through 2 profiles and 32 guides``."""
    if not profiles:
        return [(name, MONO), (" through its profiles and guides", PLAIN)]
    return [(name, MONO), (f" through {profiles} profiles and {guides} guides", PLAIN)]
