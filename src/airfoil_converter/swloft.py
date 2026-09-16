"""Lofting a wing in SolidWorks, and writing what it made to STEP.

The app hands SolidWorks curves; the loft through them is SolidWorks' own, and
how far it strays from the shape the curves describe can only be measured, not
worked out. This builds that loft the way it would be built by hand — the
settings are the ones read back off a loft made in the Loft property page — and
writes each loft to a STEP file of its own, so the real surfaces can be
compared.

What SolidWorks is reached through is a :class:`SolidWorks` protocol, as in
:mod:`swlink`, so the order of things is testable without a CAD package.

Run ``python -m airfoil_converter.swloft <part> --copy-to <folder>`` to loft
every wing recorded for a part in a copy of it, and write one STEP per wing.
"""

from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence, Tuple

from . import export, store
from .export import (
    ROLE_AIRFOIL,
    ROLE_JOINED,
    ROLE_SECTION,
    ROLE_SECTION_JOINED,
    ROLE_WING_LE,
    ROLE_WING_SURFACE,
    ROLE_WING_TE,
    ROLE_WING_TE_LOWER,
    ROLE_WING_TE_UPPER,
    InputError,
)
from .swcom import (
    GUIDE_TO_NEXT_GUIDE,
    LOFT_SURFACE_TYPE_NAME,
    LOFT_TYPE_NAME,
    DocInfo,
    SolidWorksError,
)

LOFT_SUFFIX = "_loft"
COPY_SUFFIX = " lofted"
BODY_FEATURE_TYPES = (LOFT_TYPE_NAME, LOFT_SURFACE_TYPE_NAME)

# The order a hand-made loft lists its guides in does not change its shape, but
# a fixed order keeps one run comparable with the next.
_GUIDE_ORDER = {ROLE_WING_LE: 0, ROLE_WING_SURFACE: 1, ROLE_WING_TE: 2,
                ROLE_WING_TE_UPPER: 2, ROLE_WING_TE_LOWER: 3}


class SolidWorks(Protocol):
    def active_document(self) -> Optional[DocInfo]: ...
    def editing_sketch(self) -> bool: ...
    def feature_names(self) -> List[str]: ...
    def features_of_type(self, *type_names: str) -> List[str]: ...
    def insert_loft(self, profiles: Sequence[str], guides: Sequence[str], name: str,
                    merge: bool = False, keep_tangency: bool = True,
                    guide_influence: int = GUIDE_TO_NEXT_GUIDE) -> str: ...
    def delete_feature(self, name: str) -> None: ...
    def set_suppressed(self, name: str, suppressed: bool) -> None: ...
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
        for curve in _live(wing.curves, ROLE_SECTION):
            wanted = curve.feature + "_joined"
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


def loft_and_export(
    sw: SolidWorks,
    plans: Sequence[LoftPlan],
    step_folder: str,
    replace: bool = True,
) -> List[LoftResult]:
    """Build each loft, then write each one alone to ``<step_folder>/<name>.STEP``.

    A loft already in the part under a plan's name is deleted first when
    ``replace`` is set, so a run can be repeated. While one loft is written,
    every other loft in the part is suppressed, and all of them are put back
    afterwards whatever happens.
    """
    if sw.active_document() is None:
        raise SolidWorksError("No document is open in SolidWorks.")
    if sw.editing_sketch():
        raise SolidWorksError(
            "A sketch is open for editing in SolidWorks. Close it first: selecting "
            "from outside while one is open can crash SolidWorks."
        )
    os.makedirs(step_folder, exist_ok=True)
    results = [LoftResult(plan) for plan in plans]
    present = set(sw.feature_names())
    for result in results:
        plan = result.plan
        try:
            if plan.name in present:
                if not replace:
                    raise SolidWorksError(f"{plan.name} is already in the part.")
                sw.delete_feature(plan.name)
            result.feature = sw.insert_loft(
                plan.profiles, plan.guides, plan.name,
                keep_tangency=plan.keep_tangency, guide_influence=plan.guide_influence,
            )
        except SolidWorksError as exc:
            result.error = str(exc)

    bodies = sw.features_of_type(*BODY_FEATURE_TYPES)
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


def plans_for_part(sidecar: store.Sidecar, stems: Sequence[str] = ()) -> List[LoftPlan]:
    """A loft for every wing recorded for the part, or for the ones named."""
    from . import wing_build  # reading the ribs back is slow; only here is it needed

    plans = []
    for wing in sidecar.wings:
        if stems and wing.stem not in stems:
            continue
        order = None
        if not wing.spec.offset_mm():
            model = wing_build.stand_up(wing.spec, sidecar)
            by_name = {}
            for rib_id in wing.spec.ribs:
                record = sidecar.find(rib_id)
                if record is not None:
                    by_name[export.folder_name(record.stem, record.name_index)] = rib_id
            order = [by_name[name] for name in model.rib_names if name in by_name] or None
        plans.append(wing_plan(wing, sidecar, order))
    return plans


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
            print(f"   -> {result.step}")
        else:
            failed += 1
            print(f"   FAILED: {result.error}")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
