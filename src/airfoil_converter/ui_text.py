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

from . import geometry, store
from .export import (
    MODE_2POINTS,
    MODE_3POINTS,
    MODE_LOADED,
    MODE_NORMAL,
    ExportSpec,
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
