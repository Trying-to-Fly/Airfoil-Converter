"""Getting a set of curves into a SolidWorks part, in the right order.

The order is the design, so it lives here rather than in the window or in the
COM module. What SolidWorks is reached through is a :class:`SolidWorks`
protocol, which means every rule below is testable against a fake on a machine
with no CAD package at all — and the rules are the part that was expensive to
learn.

Three of them, each paid for by somebody's afternoon:

* **Write every file, reload every curve, then rebuild once.** Reloading with
  the rebuild live shows a real but transient error partway through, because
  mid-refresh some curves carry the old geometry and some the new, and it
  rebuilds once per curve for nothing.
* **Restore the rebuild flag in a ``finally``.** A document left suppressed
  looks fine and silently stops updating, which is about the worst state to
  hand back to someone.
* **Read a name back after setting it.** SolidWorks quietly keeps its own name
  on a collision, and a record pointing at a feature that does not exist is a
  failure nothing later can detect.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Collection, Dict, List, Optional, Protocol, Sequence, Tuple

from . import writer
from .export import Curve, joinable
from .swcom import MINIMUM_MAJOR, DocInfo, FeatureInfo, SolidWorksError, meets_minimum, version_label


class SolidWorks(Protocol):
    """The slice of SolidWorks this module needs, and nothing else."""

    revision: Tuple[int, ...]

    def active_document(self) -> Optional[DocInfo]: ...
    def curve_features(self) -> List[FeatureInfo]: ...
    def insert_curve(self, path: str, name: str) -> str: ...
    def insert_composite_curve(self, sources: Sequence[str], name: str) -> str: ...
    def feature_names(self) -> List[str]: ...
    def reload_curve(self, name: str, path: str) -> None: ...
    def set_rebuild_suppressed(self, suppressed: bool) -> None: ...
    def rebuild(self) -> bool: ...


class LinkError(SolidWorksError):
    """The push could not go ahead at all."""


@dataclass
class PushResult:
    part: str = ""
    inserted: List[str] = field(default_factory=list)
    refreshed: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    written: List[str] = field(default_factory=list)
    hashes: Dict[str, str] = field(default_factory=dict)
    failures: List[Tuple[str, str]] = field(default_factory=list)
    rebuilt: bool = False
    joined: str = ""      # the joined curve, whether or not this push made it
    joined_now: bool = False   # ...and whether this push is what made it

    @property
    def touched(self) -> bool:
        return bool(self.inserted or self.refreshed)

    def summary(self) -> str:
        parts = []
        if self.inserted:
            parts.append(f"{len(self.inserted)} inserted")
        if self.refreshed:
            parts.append(f"{len(self.refreshed)} refreshed")
        if self.unchanged:
            parts.append(f"{len(self.unchanged)} unchanged")
        if self.joined_now:
            parts.append(f"joined as {self.joined}")
        if self.rebuilt:
            parts.append("rebuilt")
        if self.failures:
            parts.append(f"{len(self.failures)} failed")
        return ", ".join(parts) or "nothing to do"


def orphaned(previous: Sequence[str], curves: Sequence[Curve]) -> List[str]:
    """Features a previous export made that this one no longer produces.

    A refresh can replace a curve's points but not change how many curves there
    are, so switching the trailing edge from a closing line to auto-close drops
    a feature that a loft may still be built on. Naming them is all this does;
    nothing here ever deletes a feature.
    """
    wanted = {curve.feature for curve in curves}
    return [name for name in previous if name not in wanted]


def write_files(curves: Sequence[Curve], folder: str) -> Tuple[List[str], Dict[str, str]]:
    """Put every curve on disk. Returns the ones that changed, and all hashes.

    These files are not leftovers. A refresh re-reads them by path, so the file
    is the payload, and it is written whether or not SolidWorks is reachable.
    """
    changed: List[str] = []
    hashes: Dict[str, str] = {}
    for curve in curves:
        path = os.path.join(folder, curve.filename)
        was_changed, digest = writer.write_curve_if_changed(path, curve.points)
        hashes[curve.feature] = digest
        if was_changed:
            changed.append(curve.feature)
    return changed, hashes


def push(
    sw: SolidWorks,
    curves: Sequence[Curve],
    folder: str,
    *,
    insert_missing: bool = True,
    force: Collection[str] = (),
    join_as: str = "",
) -> PushResult:
    """Write the curves and put them into the open part."""
    if not curves:
        raise LinkError("There are no curves to send.")

    if not meets_minimum(sw.revision):
        raise LinkError(
            f"{version_label(sw.revision)} is open, but this needs revision "
            f"{MINIMUM_MAJOR} or newer."
        )

    document = sw.active_document()
    if document is None:
        raise LinkError("No document is open in SolidWorks.")
    if not document.is_part:
        raise LinkError(
            f"{document.title} is not a part, and a curve can only go into a part."
        )

    result = PushResult(part=document.title)
    changed, result.hashes = write_files(curves, folder)
    result.written = list(changed)

    existing = {f.name for f in sw.curve_features()}
    forced = set(force)

    to_insert = [c for c in curves if c.feature not in existing]
    to_reload = [
        c
        for c in curves
        if c.feature in existing and (c.feature in changed or c.feature in forced)
    ]
    result.unchanged = [
        c.feature for c in curves if c not in to_insert and c not in to_reload
    ]

    if not insert_missing:
        for curve in to_insert:
            result.failures.append((curve.feature, "not in the part, and inserting is off"))
        to_insert = []

    if to_insert or to_reload:
        _apply(sw, result, to_insert, to_reload, folder)

    # After the rebuild, so the composite is built on curves that already hold
    # the new points, and outside the block above, so it is still made on a
    # push where nothing else needed doing.
    if join_as and insert_missing:
        _join(sw, result, curves, join_as)
    return result


def _apply(
    sw: SolidWorks,
    result: PushResult,
    to_insert: Sequence[Curve],
    to_reload: Sequence[Curve],
    folder: str,
) -> None:
    sw.set_rebuild_suppressed(True)
    try:
        for curve in to_insert:
            path = os.path.abspath(os.path.join(folder, curve.filename))
            try:
                kept = sw.insert_curve(path, curve.feature)
            except SolidWorksError as exc:
                result.failures.append((curve.feature, str(exc)))
                continue
            if kept != curve.feature:
                # SolidWorks kept a name of its own, so the record and the tree
                # would disagree from here on. Say so rather than store a lie.
                result.failures.append(
                    (curve.feature, f"SolidWorks named it {kept!r} instead")
                )
                continue
            result.inserted.append(kept)

        for curve in to_reload:
            path = os.path.abspath(os.path.join(folder, curve.filename))
            try:
                sw.reload_curve(curve.feature, path)
            except SolidWorksError as exc:
                # One curve failing must not abandon the rest mid-refresh: the
                # part would be left with a mix of old and new geometry.
                result.failures.append((curve.feature, str(exc)))
                continue
            result.refreshed.append(curve.feature)
    finally:
        sw.set_rebuild_suppressed(False)

    if result.touched:
        result.rebuilt = bool(sw.rebuild())


def _join(sw: SolidWorks, result: PushResult, curves: Sequence[Curve], join_as: str) -> None:
    """Make the one selectable curve, if it is not there already.

    A composite is derived from its inputs, so it follows them whenever they
    are reloaded and only ever has to be made once.
    """
    sources = joinable(curves)
    if not sources:
        return
    if join_as in set(sw.feature_names()):
        # Already there, from an earlier push. Report it anyway, so a record
        # made before it existed still learns the name and stops calling it a
        # stray.
        result.joined = join_as
        return

    present = {f.name for f in sw.curve_features()}
    absent = [name for name in sources if name not in present]
    if absent:
        result.failures.append((join_as, f"cannot join without {', '.join(absent)}"))
        return

    try:
        kept = sw.insert_composite_curve(sources, join_as)
    except SolidWorksError as exc:
        result.failures.append((join_as, str(exc)))
        return

    if kept != join_as:
        result.failures.append((join_as, f"SolidWorks named it {kept!r} instead"))
        return
    result.joined = kept
    result.joined_now = True
