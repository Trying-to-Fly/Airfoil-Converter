"""Lofting a wing in SolidWorks, and writing what it made to STEP.

The app hands SolidWorks curves; the loft through them is SolidWorks' own, and
how far it strays from the shape the curves describe can only be measured, not
worked out. This builds that loft the way it would be built by hand — the
settings are the ones read back off a loft made in the Loft property page — and
writes each loft to a STEP file of its own, so the real surfaces can be
compared.

A solid SolidWorks refuses is built a second way before it is given up on:
the surface loft it does accept, a planar surface across the end edges of that
surface at each end, and the three knitted with "try to form solid". It is the
same geometry asked for in a different order, and on a wing whose section loop
SolidWorks will not use as the boundary of a solid that is the whole
difference — a copy of the real part gave 1 solid body of 2.5 million cubic
millimetres for the four offsets whose solid loft it had refused outright. The
part is left as the surface fallback alone would have left it if any of the
three steps does not work.

What SolidWorks is reached through is a :class:`SolidWorks` protocol, as in
:mod:`swlink`, so the order of things is testable without a CAD package.

Run ``python -m airfoil_converter.swloft <part> --copy-to <folder>`` to loft
every wing recorded for a part in a copy of it, and write one STEP per wing.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence, Tuple

from . import export, store
from .export import (
    ROLE_AIRFOIL,
    ROLE_JOINED,
    ROLE_SECTION_JOINED,
    ROLE_SECTION_UPPER,
    ROLE_WING_LE,
    ROLE_WING_SURFACE,
    ROLE_WING_TE,
    ROLE_WING_TE_LOWER,
    ROLE_WING_TE_UPPER,
    InputError,
)
from .swcom import (
    GUIDE_TO_NEXT_GUIDE,
    KNIT_TYPE_NAME,
    LOFT_SURFACE_TYPE_NAME,
    LOFT_TYPE_NAME,
    DocInfo,
    SolidWorksError,
)

LOFT_SUFFIX = "_loft"
# What the surface loft is called while it is being capped, before the knit
# takes the plan's own name. It only exists between the two.
SURFACE_SUFFIX = "_surface"
CAP_SUFFIX = "_cap"
COPY_SUFFIX = " lofted"
BODY_FEATURE_TYPES = (LOFT_TYPE_NAME, LOFT_SURFACE_TYPE_NAME, KNIT_TYPE_NAME)

# The order the guides are given in can change the loft: on SolidWorks 2026 it
# moved one wing's inner surface by up to 0.04 mm, and its outer not at all. A
# fixed order keeps one run comparable with the next.
_GUIDE_ORDER = {ROLE_WING_LE: 0, ROLE_WING_SURFACE: 1, ROLE_WING_TE: 2,
                ROLE_WING_TE_UPPER: 2, ROLE_WING_TE_LOWER: 3}


# Which surface guides a rung leaves out, and how to say so. A loft held by
# every guide can come out with a sliver face running along the tip, and then
# nothing will put a surface across the loop that face leaves — that is one
# guide's doing, and on the wing it was found on, the lower one at 2% of the
# chord. Dropping it gave three faces, two caps and a solid at every offset
# that had failed, of a volume within 0.006% of the solid loft where the solid
# loft worked at all. Dropping the upper one instead did nothing, so the rungs
# below start with the lower and widen from there rather than guessing.
# How far along the chord the guides nearest the nose stand, as wing.py places
# them.
NOSE_STATIONS = (2.0, 3.5)
LADDER: Tuple[Tuple[Optional[Tuple[Tuple[str, float], ...]], str], ...] = (
    ((("lower", 2.0),), "the lower 2% guide was dropped to close the tip"),
    ((("upper", 2.0),), "the upper 2% guide was dropped to close the tip"),
    ((("lower", 2.0), ("upper", 2.0)), "both 2% guides were dropped to close the tip"),
    (tuple((side, at) for at in NOSE_STATIONS for side in ("lower", "upper")),
     "the four nose guides were dropped to close the tip"),
    (None, "only the edge guides were kept, to close the tip"),
)

# ``wing_upper_3p5`` is the guide 3.5% along the upper surface. The trailing
# edge's own guides end in _te_upper and _te_lower and carry no station, so
# this passes over them, as it does the leading edge.
_SURFACE_GUIDE = re.compile(r"_(upper|lower)_(\d+)(?:p(\d))?$")


def surface_guide(name: str) -> Optional[Tuple[str, float]]:
    """Which surface a guide holds and how far along it, or None if it is an edge."""
    found = _SURFACE_GUIDE.search(name)
    if found is None:
        return None
    tenths = float(found.group(3)) / 10.0 if found.group(3) else 0.0
    return found.group(1), float(found.group(2)) + tenths


def guide_ladder(guides: Sequence[str]) -> List[Tuple[Tuple[str, ...], str]]:
    """The guide sets to try in order, each with what it leaves out.

    The first is every guide the wing asked for. The rest drop surface guides
    near the nose, in the app's own order otherwise, and a rung that would drop
    nothing — or nothing more than the rung before it — is left out rather than
    run for a second time: each attempt is a loft of 10 to 15 seconds in
    SolidWorks, and a cap a fifth of a second, so the whole ladder is about a
    minute at worst.
    """
    out: List[Tuple[Tuple[str, ...], str]] = [(tuple(guides), "")]
    for dropped, said in LADDER:
        kept = tuple(
            name for name in guides
            if surface_guide(name) is None
            or (dropped is not None and surface_guide(name) not in dropped)
        )
        if len(kept) < len(guides) and all(kept != before for before, _ in out):
            out.append((kept, said))
    return out


class SolidWorks(Protocol):
    def active_document(self) -> Optional[DocInfo]: ...
    def editing_sketch(self) -> bool: ...
    def feature_names(self) -> List[str]: ...
    def features_of_type(self, *type_names: str) -> List[str]: ...
    def insert_loft(self, profiles: Sequence[str], guides: Sequence[str], name: str,
                    merge: bool = False, keep_tangency: bool = True,
                    guide_influence: int = GUIDE_TO_NEXT_GUIDE, solid: bool = True) -> str: ...
    def cap_end(self, loft: str, profile: str, name: str) -> str: ...
    def knit_to_solid(self, surfaces: Sequence[str], name: str, solid: bool = True) -> str: ...
    def rename_feature(self, current: str, new: str) -> str: ...
    def delete_feature(self, name: str) -> None: ...
    def set_suppressed(self, name: str, suppressed: bool) -> None: ...
    # No argument: this module only ever rebuilds what changed.
    def rebuild(self) -> bool: ...
    def export_step(self, path: str) -> None: ...


@dataclass(frozen=True)
class LoftPlan:
    """One loft: what to call it, and the curves it goes through."""

    name: str
    profiles: Tuple[str, ...]
    guides: Tuple[str, ...]
    keep_tangency: bool = True
    guide_influence: int = GUIDE_TO_NEXT_GUIDE


@dataclass
class LoftResult:
    plan: LoftPlan
    feature: str = ""
    step: str = ""
    error: str = ""
    # Set when SolidWorks refused the solid, a surface loft was made instead,
    # and capping that surface into a solid did not work either.
    surface: bool = False
    # Set when the solid was refused but the surface, capped at both ends and
    # knitted, made one anyway.
    capped: bool = False
    # Set when the loft was there already and was left as it is.
    kept: bool = False
    # Why the capping did not happen, when it was tried and did not work. Not
    # an error: the surface loft is still there and still measurable.
    note: str = ""
    # How many guides the loft that now stands was built with, and — when that
    # is fewer than the wing asked for — what was left out and why.
    guides_used: int = 0
    dropped: str = ""

    def describe(self) -> str:
        name = self.plan.name
        if self.error:
            return f"{name} could not be lofted: {self.error}"
        if self.kept:
            return (f"{name} is already in the part and follows the curves. Delete it "
                    "to loft it again.")
        if self.capped and self.dropped:
            said = (f"{name} lofted as a surface and capped into a solid "
                    f"({self.guides_used} of {len(self.plan.guides)} guides; "
                    f"{self.dropped}).")
        elif self.capped:
            said = (f"{name} lofted as a surface and capped into a solid "
                    f"({len(self.plan.guides)} guides).")
        elif self.surface:
            said = (f"{name} lofted as a surface: SolidWorks would not make it a solid "
                    f"({len(self.plan.guides)} guides).")
        else:
            said = (f"{name} lofted through {len(self.plan.profiles)} profiles and "
                    f"{len(self.plan.guides)} guides.")
        return f"{said} [{self.note}]" if self.note else said

    @property
    def ok(self) -> bool:
        return not self.error


def _live(curves: Sequence[store.CurveRecord], *roles: str) -> List[store.CurveRecord]:
    return [c for c in curves if not c.retired and c.role in roles]


def rib_profile(record: store.ExportRecord) -> str:
    """The one curve a rib offers a loft: its joined curve, or its closed outline."""
    joined = _live(record.curves, ROLE_JOINED)
    if joined:
        return joined[0].feature
    outline = [c for c in _live(record.curves, ROLE_AIRFOIL) if c.closed]
    if outline:
        return outline[0].feature
    name = export.folder_name(record.stem, record.name_index) if record.stem else record.export_id
    raise InputError(f"{name} has no single closed curve to loft through.")


def wing_plan(
    wing: store.WingRecord,
    sidecar: store.Sidecar,
    rib_order: Optional[Sequence[str]] = None,
) -> LoftPlan:
    """The loft a wing record describes.

    An outer wing lofts its ribs; an offset wing lofts its own sections, joined
    where they have a trailing-edge line. Either way every edge curve and
    surface guide the wing wrote is a guide. ``rib_order`` gives the ribs'
    export ids root to tip, when the record's own order may not be.
    """
    spec = wing.spec
    if spec.offset_mm():
        joined = {c.feature for c in _live(wing.curves, ROLE_SECTION_JOINED)}
        profiles = []
        for curve in _live(wing.curves, *export.SECTION_HEAD_ROLES):
            base = curve.feature
            if curve.role == ROLE_SECTION_UPPER:
                base = base[: -len("_upper")]
            wanted = base + "_joined"
            profiles.append(wanted if wanted in joined else curve.feature)
    else:
        profiles = []
        for rib_id in rib_order or spec.ribs:
            record = sidecar.find(rib_id)
            if record is None:
                raise InputError("One of this wing's ribs is no longer in the part's record.")
            profiles.append(rib_profile(record))
    guides = sorted(
        _live(wing.curves, *_GUIDE_ORDER),
        key=lambda c: _GUIDE_ORDER[c.role],
    )
    if len(profiles) < 2:
        raise InputError(f"{wing.stem} has fewer than two profiles to loft.")
    return LoftPlan(
        name=export.wing_base(wing.stem, wing.name_index) + LOFT_SUFFIX,
        profiles=tuple(profiles),
        guides=tuple(c.feature for c in guides),
    )


def _ready(sw: SolidWorks) -> None:
    if sw.active_document() is None:
        raise SolidWorksError("No document is open in SolidWorks.")
    if sw.editing_sketch():
        raise SolidWorksError(
            "A sketch is open for editing in SolidWorks. Close it first: selecting "
            "from outside while one is open can crash SolidWorks."
        )


def _make_lofts(
    sw: SolidWorks, plans: Sequence[LoftPlan], replace: bool, surface_fallback: bool
) -> List[LoftResult]:
    results = [LoftResult(plan) for plan in plans]
    present = set(sw.feature_names())
    for result in results:
        plan = result.plan
        try:
            if plan.name in present:
                if not replace:
                    # Built on the wing's own curves, it follows them whenever
                    # they are reloaded; there is nothing to do.
                    result.feature = plan.name
                    result.kept = True
                    continue
                sw.delete_feature(plan.name)
                # A capped loft is four features under three names. Whatever
                # deleting the knit left of it goes too, or the next attempt
                # builds under names SolidWorks has had to make up.
                left = set(sw.feature_names())
                for piece in _capping_pieces(result):
                    if piece in left:
                        sw.delete_feature(piece)
            try:
                result.feature = sw.insert_loft(
                    plan.profiles, plan.guides, plan.name,
                    keep_tangency=plan.keep_tangency, guide_influence=plan.guide_influence,
                )
                result.guides_used = len(plan.guides)
            except SolidWorksError:
                if not surface_fallback:
                    raise
                # SolidWorks turns down some solids whose surface it makes
                # without complaint, and a capped surface is a solid again.
                _cap_into_solid(sw, result)
        except SolidWorksError as exc:
            result.error = str(exc)
    return results


def _capping_pieces(result: LoftResult) -> Tuple[str, str, str]:
    """The three features a capped loft is made of, by the names given to them."""
    name = result.plan.name
    return (name + SURFACE_SUFFIX, f"{name}_root{CAP_SUFFIX}", f"{name}_tip{CAP_SUFFIX}")


def _cap_into_solid(sw: SolidWorks, result: LoftResult) -> None:
    """Loft the surface, cap both ends, knit the three into a solid.

    The same geometry as the solid loft SolidWorks refused, built the other way
    round: it accepts the surface every time, and a planar face across the end
    edges of that surface is a thing it can be asked for on its own.

    An end that will not take a cap is the loft's own doing rather than the
    section's: held by every guide it can come out with a sliver face along the
    tip, and no surface will span the loop that leaves. So the guides come off
    a rung at a time — see :data:`LADDER` — and the first set that caps and
    knits is the one that stays. Each rung is a loft of 10 to 15 seconds and
    two caps of a fifth of a second, so the ladder costs about a minute at
    worst, against a wing that otherwise has no solid at all.

    Whatever happens, the part ends up either with the solid or with the
    surface loft the wing asked for, under the plan's name, and with nothing
    else of this left in the tree.
    """
    plan = result.plan
    surface_name, root_cap, tip_cap = _capping_pieces(result)
    ends = ((root_cap, plan.profiles[0]), (tip_cap, plan.profiles[-1]))
    note, left = "", []

    for guides, dropped in guide_ladder(plan.guides):
        if result.feature:
            # What the rung before made, which is not what this one wants.
            left += _take_out(sw, [result.feature])
            result.feature = ""
        try:
            result.feature = sw.insert_loft(
                plan.profiles, guides, surface_name,
                keep_tangency=plan.keep_tangency, solid=False,
            )
        except SolidWorksError:
            if not result.guides_used:
                # Not even the surface can be lofted, which is the end of it.
                raise
            continue
        result.surface = True
        result.guides_used = len(guides)

        caps: List[str] = []
        try:
            for cap, profile in ends:
                caps.append(sw.cap_end(result.feature, profile, cap))
        except SolidWorksError as exc:
            # Only the caps go. The loft stands until the next rung takes it
            # out, and if there is no next rung it is what is handed back.
            note = str(exc)
            left += _take_out(sw, caps)
            continue
        try:
            result.feature = sw.knit_to_solid([result.feature] + caps, plan.name)
        except SolidWorksError as exc:
            # Three surfaces that will not knit is not something fewer guides
            # would mend, so the ladder stops here.
            note = str(exc)
            left += _take_out(sw, caps)
            break
        result.capped = True
        result.surface = False
        result.dropped = dropped
        return

    _leave_the_surface(sw, result, note, left)


def _take_out(sw: SolidWorks, names: Sequence[str]) -> List[str]:
    """Delete what an attempt made, and say which of it would not go."""
    left = []
    for name in names:
        if not name:
            continue
        try:
            sw.delete_feature(name)
        except SolidWorksError:
            left.append(name)
    return left


def _leave_the_surface(
    sw: SolidWorks, result: LoftResult, note: str, left: List[str]
) -> None:
    """Leave the part holding the surface loft the wing asked for, under its name.

    The loft standing at this point is whatever the last rung made, which is
    not what was asked for; if it is not the full set it goes, and the full one
    is made again. That costs another loft, and it is the difference between
    handing back the wing's own surface and handing back a reduced one nothing
    in the record describes.
    """
    plan = result.plan
    if result.feature and result.guides_used == len(plan.guides):
        result.feature = sw.rename_feature(result.feature, plan.name)
    else:
        left += _take_out(sw, [result.feature])
        result.feature = ""
        result.feature = sw.insert_loft(
            plan.profiles, plan.guides, plan.name,
            keep_tangency=plan.keep_tangency, solid=False,
        )
    result.surface = True
    result.capped = False
    result.dropped = ""
    result.guides_used = len(plan.guides)
    if left:
        # Saying so is all that can be done, and it is worth more than a tidy
        # message: these are features in the user's part under names nothing
        # else knows about.
        note += f"; left in the part as {', '.join(left)}"
    result.note = note


def loft_in_part(
    sw: SolidWorks, plans: Sequence[LoftPlan], surface_fallback: bool = True
) -> List[LoftResult]:
    """Build each loft in the open part, leaving any that is there already.

    A loft already there is never deleted: something may be built on it, and
    it follows the curves it was made from anyway.
    """
    _ready(sw)
    results = _make_lofts(sw, plans, replace=False, surface_fallback=surface_fallback)
    if any(r.feature and not r.kept for r in results):
        sw.rebuild()
    return results


def loft_and_export(
    sw: SolidWorks,
    plans: Sequence[LoftPlan],
    step_folder: str,
    replace: bool = True,
    surface_fallback: bool = True,
) -> List[LoftResult]:
    """Build each loft, then write each one alone to ``<step_folder>/<name>.STEP``.

    A loft already in the part under a plan's name is deleted first when
    ``replace`` is set, so a run can be repeated — which is for a copy of the
    part, never the one being worked on. While one loft is written, every
    other loft in the part is suppressed, and all of them are put back
    afterwards whatever happens. A solid SolidWorks refuses is made as a
    surface instead, unless ``surface_fallback`` is off.
    """
    _ready(sw)
    os.makedirs(step_folder, exist_ok=True)
    results = _make_lofts(sw, plans, replace, surface_fallback)

    # The body a capped loft offers is its knit, which counts here by type; the
    # surface it was knitted from is still in the tree beside it, absorbed but
    # still calling itself a lofted surface. Suppressing that would take the
    # solid with it, so the pieces are known by name rather than by type.
    absorbed = {name for result in results if result.capped for name in _capping_pieces(result)}
    bodies = [b for b in sw.features_of_type(*BODY_FEATURE_TYPES) if b not in absorbed]
    for result in results:
        if not result.ok:
            continue
        others = [name for name in bodies if name != result.feature]
        hidden: List[str] = []
        try:
            for name in others:
                sw.set_suppressed(name, True)
                hidden.append(name)
            sw.rebuild()
            path = os.path.join(step_folder, result.plan.name + ".STEP")
            sw.export_step(path)
            result.step = path
        except SolidWorksError as exc:
            result.error = str(exc)
        finally:
            errors = []
            for name in hidden:
                try:
                    sw.set_suppressed(name, False)
                except SolidWorksError as exc:
                    errors.append(str(exc))
            sw.rebuild()
            if errors and result.ok:
                result.error = "; ".join(errors)
    return results


def plan_for_wing(wing: store.WingRecord, sidecar: store.Sidecar) -> LoftPlan:
    """The loft a wing record describes, its ribs put in order root to tip."""
    from . import wing_build  # reading the ribs back is slow; only here is it needed

    order = None
    if not wing.spec.offset_mm():
        model = wing_build.stand_up(wing.spec, sidecar)
        by_name = {}
        for rib_id in wing.spec.ribs:
            record = sidecar.find(rib_id)
            if record is not None:
                by_name[export.folder_name(record.stem, record.name_index)] = rib_id
        order = [by_name[name] for name in model.rib_names if name in by_name] or None
    return wing_plan(wing, sidecar, order)


def plans_for_part(sidecar: store.Sidecar, stems: Sequence[str] = ()) -> List[LoftPlan]:
    """A loft for every wing recorded for the part, or for the ones named."""
    return [plan_for_wing(w, sidecar) for w in sidecar.wings if not stems or w.stem in stems]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m airfoil_converter.swloft", description=__doc__)
    parser.add_argument("part", help="the .SLDPRT whose wings to loft")
    parser.add_argument("--copy-to", help="copy the part (and its record) here and work on the copy")
    parser.add_argument("--wing", action="append", default=[], help="only this wing (by name); repeatable")
    parser.add_argument("--step-folder", help="where the STEP files go (default: next to the part lofted)")
    parser.add_argument("--keep-open", action="store_true", help="leave the copy open afterwards")
    args = parser.parse_args(argv)

    from . import swcom

    part = os.path.abspath(args.part)
    with open(store.sidecar_path(part), encoding="utf-8") as handle:
        sidecar = store.from_json(handle.read())
    plans = plans_for_part(sidecar, args.wing)
    if not plans:
        print("No wings are recorded for this part.")
        return 1

    target = part
    if args.copy_to:
        os.makedirs(args.copy_to, exist_ok=True)
        # SolidWorks opens one document per file name, and the original is
        # likely open, so the copy is named apart from it.
        base, ext = os.path.splitext(os.path.basename(part))
        target = os.path.join(os.path.abspath(args.copy_to), base + COPY_SUFFIX + ext)
        shutil.copy2(part, target)
        shutil.copy2(store.sidecar_path(part), store.sidecar_path(target))
    step_folder = args.step_folder or os.path.dirname(target)

    session = swcom.connect()
    doc = session.open_part(target)
    print(f"lofting in {doc.path}")
    try:
        results = loft_and_export(session, plans, step_folder)
    finally:
        if args.copy_to and not args.keep_open:
            session.save()
            session.close_document(doc.title)
    failed = 0
    for result in results:
        plan = result.plan
        print(f"\n{plan.name}: {len(plan.profiles)} profiles, {len(plan.guides)} guides")
        print(f"   profiles: {', '.join(plan.profiles)}")
        print(f"   guides:   {', '.join(plan.guides)}")
        if result.ok:
            made = ""
            if result.capped:
                made = " (a surface loft capped at both ends and knitted into a solid)"
            elif result.surface:
                made = " (as a surface: SolidWorks refused the solid)"
            print(f"   -> {result.step}{made}")
        else:
            failed += 1
            print(f"   FAILED: {result.error}")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
