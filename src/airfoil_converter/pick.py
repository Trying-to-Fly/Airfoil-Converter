"""Turning clicks in SolidWorks into the numbers on the form.

The window arms a pick, SolidWorks reports what was clicked, and this module
decides what it means: which step it satisfies, whether it can be used at all,
and finally which plane mode and which points the form should be set to. None
of that touches COM or tkinter, so all of it is testable on a machine with no
CAD package — the same bargain :mod:`swlink` makes for the push ordering.

Two rules here are worth stating outright, because both are invisible when
broken:

* **A picked plane and a picked line are a 3-point plane.** There is no plane
  mode for a pick, and there should not be: a normal and a direction reduce
  exactly to P1, P2 and P3, so a pick writes into fields the app already
  understands and everything downstream — the spec, the sidecar, the export —
  carries on knowing nothing about SolidWorks.
* **The chord runs P1 -> P2, which is nose to tail.** A line has two ends and
  says nothing about which is which, so the leading edge decides: the end
  nearer the point the user picked is the nose. Get this backwards and the
  section is exported the right shape, on the right plane, facing the wrong
  way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from . import geometry
from .export import MODE_3POINTS, MODE_NORMAL
from .geometry import Vec3, add, dot, length, normalize, scale, sub
from .swcom import Picked, PickedLine, PickedPlane, PickedPoint, Refused

# What a step is waiting for.
PLANE = "plane"
LINE = "line"
POINT = "point"

_ASK = {
    PLANE: "Click the plane the airfoil sits on: a reference plane, or a flat face.",
    LINE: "Click a line for the chord: a straight edge, or a sketch line.",
    POINT: "Click the leading edge: a sketch point, or a corner.",
}

_INSTEAD = {
    PLANE: "Click a reference plane or a flat face.",
    LINE: "Click a straight edge or a sketch line.",
    POINT: "Click a sketch point or a corner.",
}


@dataclass(frozen=True)
class Step:
    wants: str
    optional: bool = False

    @property
    def ask(self) -> str:
        return _ASK[self.wants] + (" Or press Skip." if self.optional else "")


# The full pick: a plane, then how the chord runs on it, then where the nose
# goes. Only the plane is needed — without a line the chord follows the
# normal-to-line convention, and without a point the leading edge is left to
# fill itself in from P1.
PLANE_PICK: Tuple[Step, ...] = (
    Step(PLANE),
    Step(LINE, optional=True),
    Step(POINT, optional=True),
)

# The short one, for moving a section without changing its plane.
POINT_PICK: Tuple[Step, ...] = (Step(POINT),)


@dataclass(frozen=True)
class Placement:
    """What the form should be set to. ``None`` means leave that part alone."""

    plane_mode: Optional[str] = None
    points: Tuple[Vec3, ...] = ()
    leading_edge: Optional[Vec3] = None


def describe(picked: Picked) -> str:
    if isinstance(picked, Refused):
        return picked.what
    if isinstance(picked, PickedPoint):
        return "a point"
    if isinstance(picked, PickedLine):
        return "a line"
    return "a plane"


class Pick:
    """One run of pick mode: what has been clicked, and what to click next."""

    def __init__(self, steps: Sequence[Step] = PLANE_PICK) -> None:
        self._steps = tuple(steps)
        self._at = 0
        self.plane: Optional[PickedPlane] = None
        self.line: Optional[PickedLine] = None
        self.point: Optional[PickedPoint] = None
        self._note = ""

    @property
    def finished(self) -> bool:
        return self._at >= len(self._steps)

    @property
    def steps(self) -> Tuple[Step, ...]:
        """Every step of this pick, so a tracker can draw the ones not reached yet."""
        return self._steps

    @property
    def index(self) -> int:
        """How many steps are behind us, which is also the active step's position."""
        return self._at

    @property
    def refusal(self) -> str:
        """The last click that could not be used, or empty. Shown in red, not grey."""
        return self._note

    @property
    def step(self) -> Optional[Step]:
        return None if self.finished else self._steps[self._at]

    @property
    def skippable(self) -> bool:
        step = self.step
        return step is not None and step.optional

    @property
    def says(self) -> str:
        """The one sentence the window shows: a refusal, or what to click next."""
        if self._note:
            return self._note
        step = self.step
        return "" if step is None else step.ask

    def accept(self, picked: Picked) -> bool:
        """Take one click. True if it satisfied the step, False if it was refused."""
        step = self.step
        if step is None:
            return False

        wanted = {
            PLANE: PickedPlane,
            LINE: PickedLine,
            POINT: PickedPoint,
        }[step.wants]
        if not isinstance(picked, wanted):
            self._note = f"That is {describe(picked)}. {_INSTEAD[step.wants]}"
            return False

        setattr(self, step.wants, picked)
        self._note = ""
        self._at += 1
        return True

    def skip(self) -> bool:
        """Pass over an optional step. False if the step is not optional."""
        if not self.skippable:
            return False
        self._note = ""
        self._at += 1
        return True

    def stop(self) -> None:
        """Give up on the rest of the steps and use whatever has been picked."""
        self._at = len(self._steps)
        self._note = ""

    def resolve(self) -> Placement:
        """What was picked, as a plane mode and the points to put in the form."""
        nose = self.point.where if self.point is not None else None

        if self.plane is None:
            return Placement(leading_edge=nose)

        if self.line is not None:
            chord = _nose_to_tail(self.line, nose)
            anchor = nose if nose is not None else _onto(self.line.start, self.plane)
            points = geometry.picked_points(self.plane.normal, anchor, chord)
            return Placement(plane_mode=MODE_3POINTS, points=points, leading_edge=nose)

        # No line, so nothing fixes the chord on the plane. Hand the normal to
        # the mode that already has a convention for exactly that, rather than
        # inventing a second one.
        anchor = nose if nose is not None else self.plane.root
        along = normalize(self.plane.normal, "plane normal")
        return Placement(
            plane_mode=MODE_NORMAL,
            points=(anchor, add(anchor, along)),
            leading_edge=nose,
        )


def _nose_to_tail(line: PickedLine, nose: Optional[Vec3]) -> Vec3:
    """Which way along the line the chord runs.

    With a leading edge picked, the nearer end of the line is the nose and the
    chord runs away from it. Without one there is nothing to go on, so the line
    keeps its own direction and the user flips it if it came out backwards.
    """
    if nose is None:
        return sub(line.end, line.start)
    if length(sub(line.start, nose)) <= length(sub(line.end, nose)):
        return sub(line.end, line.start)
    return sub(line.start, line.end)


def _onto(point: Vec3, plane: PickedPlane) -> Vec3:
    """The point's own shadow on the plane, straight down the normal."""
    n = normalize(plane.normal, "plane normal")
    return sub(point, scale(n, dot(sub(point, plane.root), n)))
