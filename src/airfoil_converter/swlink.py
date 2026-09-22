"""Getting a set of curves into a SolidWorks part, in the right order.

The order is the design, so it lives here rather than in the window or in the
COM module. What SolidWorks is reached through is a :class:`SolidWorks`
protocol, which means every rule below is testable against a fake on a machine
with no CAD package at all — and the rules are the part that was expensive to
learn.

Four of them, each paid for by somebody's afternoon:

* **Write every file, reload every curve, then rebuild once, and only what
  changed.** Reloading with the rebuild live shows a real but transient error
  partway through, because mid-refresh some curves carry the old geometry and
  some the new, and it rebuilds once per curve for nothing. The one rebuild at
  the end regenerates the features the reloaded curves feed, not the whole
  part: on a part of 397 features with six lofts through thirty guides each,
  that is 0.8 seconds against 25 for the same geometry.
* **Roll the tree back to the curves before reloading them.** SolidWorks
  commits a curve's new points against everything standing below it in the
  tree, and charges for it: on that same part each commit took 25 seconds with
  the tree rolled forward — 16.5 minutes for one wing's 39 curves — and half a
  second with the bar sitting just after those curves, where nothing built on
  them has been made yet. Rolled back, reloaded, rolled forward and rebuilt,
  the whole wing takes 29 seconds. New curves land at the bar too, which is
  beside the record's own curves rather than at the end of the part.
* **Restore the rebuild flag, and the bar, in a ``finally``.** A document left
  suppressed looks fine and silently stops updating; one left rolled back
  looks like a part with half its features gone. Both are about the worst
  state to hand back to someone.
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
    def composite_sources(self, name: str) -> List[str]: ...
    def rename_feature(self, current: str, new: str) -> str: ...
    def feature_names(self) -> List[str]: ...
    def reload_curve(self, name: str, path: str) -> None: ...
    def roll_back_to(self, name: str) -> None: ...
    def roll_forward(self) -> None: ...
    def set_rebuild_suppressed(self, suppressed: bool) -> None: ...
    # No argument: this module only ever rebuilds what changed.
    def rebuild(self) -> bool: ...
    def folders(self) -> Dict[str, List[str]]: ...
    def insert_folder(self, names: Sequence[str], folder: str) -> str: ...
    def delete_folder(self, folder: str) -> None: ...
    def editing_sketch(self) -> bool: ...


# What a joined curve is renamed to when it is made again.
OLD_JOIN_SUFFIX = "_old"


class LinkError(SolidWorksError):
    """The push could not go ahead at all."""


SKETCH_OPEN = (
    "A sketch is open for editing in SolidWorks. Close it first: changing the "
    "part's selection from outside while one is open can crash SolidWorks."
)


def _refuse_while_sketching(sw: SolidWorks) -> None:
    """Joining and foldering select features, which is not safe mid-sketch."""
    if sw.editing_sketch():
        raise LinkError(SKETCH_OPEN)


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
    joined_all: List[str] = field(default_factory=list)   # every join, for a wing's many
    joined_new: int = 0   # ...and how many of them this push made
    # Joins made again because what they join changed, each with the name the
    # old one was moved to. A loft that picked one as a profile still has the
    # old one, and has to be given the new one.
    remade: List[Tuple[str, str]] = field(default_factory=list)
    arranged: Optional["ArrangeResult"] = None   # what the tree tidy-up did

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
        if self.joined_new > 1:
            parts.append(f"{self.joined_new} joined")
        elif self.joined_now:
            parts.append(f"joined as {self.joined}")
        if self.remade:
            parts.append(f"{len(self.remade)} joined curve(s) made again — "
                         "pick them again in any loft (the old ones end in "
                         f"{OLD_JOIN_SUFFIX})")
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
    joins: Sequence[Tuple[Tuple[str, ...], str]] = (),
) -> PushResult:
    """Write the curves and put them into the open part.

    ``join_as`` names the one composite a rib makes of its airfoil and its
    trailing-edge line. ``joins`` lists any number of ``(sources, name)``
    composites outright, which is what a wing of such sections needs.
    """
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

    _refuse_while_sketching(sw)

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
    wanted = list(joins)
    if join_as:
        sources = joinable(curves)
        if sources:
            wanted.insert(0, (sources, join_as))
    if wanted and insert_missing:
        present = set(sw.feature_names())
        curve_names = {f.name for f in sw.curve_features()}
        for sources, name in wanted:
            _join(sw, result, tuple(sources), name, present, curve_names)
    return result


def _apply(
    sw: SolidWorks,
    result: PushResult,
    to_insert: Sequence[Curve],
    to_reload: Sequence[Curve],
    folder: str,
) -> None:
    # The bar goes just after the last curve of this record that the part
    # already has, so that committing points to any of them costs nothing
    # below. New curves are made at the bar, which puts them beside their own
    # kind rather than at the end of the part.
    order = {name: i for i, name in enumerate(sw.feature_names())}
    here = [c.feature for c in to_reload if c.feature in order]
    last = max(here, key=lambda name: order[name]) if here else ""
    rolled = False
    if last:
        try:
            sw.roll_back_to(last)
            rolled = True
        except SolidWorksError:
            # A bar that will not move costs time, not correctness. The push
            # goes ahead on the live tree, the way it always used to, rather
            # than refusing an export over how long it is going to take.
            pass

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
        try:
            if rolled:
                sw.roll_forward()
        finally:
            # Whatever the bar does, the document must not be left suppressed.
            sw.set_rebuild_suppressed(False)

    if result.touched:
        result.rebuilt = bool(sw.rebuild())


def _join(
    sw: SolidWorks,
    result: PushResult,
    sources: Tuple[str, ...],
    join_as: str,
    present: Collection[str],
    curve_names: Collection[str],
) -> None:
    """Make one selectable curve, if it is not there already.

    A composite is derived from its inputs, so it follows them whenever they
    are reloaded and only ever has to be made once.
    """
    if join_as in present:
        # Already there, from an earlier push. Report it anyway, so a record
        # made before it existed still learns the name and stops calling it a
        # stray. A section can change how many pieces it comes in — a nose
        # that has come to a corner is two — so what it joins is checked.
        try:
            same = list(sw.composite_sources(join_as)) == list(sources)
        except SolidWorksError as exc:
            result.failures.append((join_as, str(exc)))
            return
        if same:
            _joined(result, join_as, made=False)
            return
        # What an existing composite joins cannot be changed from here — the
        # new pieces sit below it in the tree, out of its reach — and deleting
        # it takes the loft built on it along. So it is renamed out of the way,
        # still holding up that loft, and made again under its own name.
        absent = [name for name in sources if name not in curve_names]
        if absent:
            result.failures.append((join_as, f"cannot join without {', '.join(absent)}"))
            return
        taken = set(present)
        old = join_as + OLD_JOIN_SUFFIX
        count = 2
        while old in taken:
            old = f"{join_as}{OLD_JOIN_SUFFIX}{count}"
            count += 1
        try:
            kept = sw.rename_feature(join_as, old)
        except SolidWorksError as exc:
            result.failures.append((join_as, str(exc)))
            return
        if kept == join_as:
            result.failures.append((join_as, "SolidWorks would not rename the old one out of the way"))
            return
        result.remade.append((join_as, kept))

    absent = [name for name in sources if name not in curve_names]
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
    _joined(result, kept, made=True)


def _joined(result: PushResult, name: str, made: bool) -> None:
    if not result.joined:
        result.joined = name
        result.joined_now = made
    result.joined_all.append(name)
    if made:
        result.joined_new += 1


# -- the shape of the tree --------------------------------------------------
#
# Four curves per rib, loose in the tree, is four rows that say nothing about
# belonging together. So each export's curves go in a folder named after the
# export, and the folders go in one folder of their own.
#
# There is exactly one primitive to build this with: select some features and
# SolidWorks wraps them in a new folder. There is no way to add a feature to a
# folder that already exists — ``MoveToFolder`` answers False whatever it is
# offered, on a curve, on a plane, on a folder, before or after a rebuild. What
# saves it is that deleting a folder deletes only the folder: everything it held
# stays put, in order. So the arrangement is not patched, it is rebuilt, and
# only where it is already wrong.

PARENT_FOLDER = "Airfoil Curves"


@dataclass(frozen=True)
class TreeGroup:
    """One export's curves, and what their folder should be called."""

    folder: str
    features: Tuple[str, ...]


@dataclass
class ArrangeResult:
    made: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)
    failures: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return bool(self.made)

    def summary(self) -> str:
        if not self.made:
            return ""
        return f"{len(self.made)} folder{'' if len(self.made) == 1 else 's'}"


def _holder(folders: Dict[str, List[str]], wanted: Sequence[str]) -> Optional[str]:
    """The folder holding exactly these and nothing else, if there is one."""
    target = set(wanted)
    for name, contents in folders.items():
        if set(contents) == target:
            return name
    return None


def _named(folders: Dict[str, List[str]], wanted: Sequence[str], fallback: str,
           ignore: Collection[str] = ()) -> str:
    """The name to give the rebuilt folder: the one it already has, if any.

    A folder whose contents have changed has to be made again, and making it
    again would otherwise hand back the generated name and quietly undo a
    rename somebody made in SolidWorks. One folder already holding some of
    these is that folder, under whatever it is now called.
    """
    holders = [
        name for name, contents in folders.items()
        if name not in ignore and any(item in contents for item in wanted)
    ]
    return holders[0] if len(holders) == 1 else fallback


def _clear_out(sw: SolidWorks, folders: Dict[str, List[str]], wanted: Sequence[str],
               result: ArrangeResult, keep: Collection[str] = ()) -> None:
    """Remove any folder holding some of these, so a new one can hold them all.

    A folder that holds only part of a group is the wrong folder, and leaving
    it would mean a group split across two. Deleting it costs nothing: its
    features do not move.
    """
    for name, contents in list(folders.items()):
        if name in keep:
            continue
        if any(feature in contents for feature in wanted):
            try:
                sw.delete_folder(name)
            except SolidWorksError as exc:
                result.failures.append((name, str(exc)))
                continue
            folders.pop(name, None)


def arrange(
    sw: SolidWorks,
    groups: Sequence[TreeGroup],
    parent: str = PARENT_FOLDER,
) -> ArrangeResult:
    """Put each export's curves in a folder, and the folders in one folder.

    A folder that is already right is left alone, name and all, so renaming one
    in SolidWorks sticks. Nothing here ever deletes a curve; the only thing it
    deletes is a folder, which is a row in the tree and not geometry.
    """
    result = ArrangeResult()
    if sw.editing_sketch():
        result.failures.append(("folders", SKETCH_OPEN))
        return result
    present = set(sw.feature_names())
    folders = sw.folders()

    inner: List[str] = []
    for group in groups:
        wanted = [name for name in group.features if name in present]
        if not wanted:
            continue
        holder = _holder(folders, wanted)
        if holder is not None:
            inner.append(holder)
            result.kept.append(holder)
            continue
        name = _named(folders, wanted, group.folder)
        _clear_out(sw, folders, wanted, result)
        try:
            made = sw.insert_folder(wanted, name)
        except SolidWorksError as exc:
            result.failures.append((group.folder, str(exc)))
            continue
        folders = sw.folders()
        inner.append(made)
        result.made.append(made)

    if not inner:
        return result

    holder = _holder(folders, inner)
    if holder is not None:
        result.kept.append(holder)
        return result

    name = _named(folders, inner, parent, ignore=inner)
    _clear_out(sw, folders, inner, result, keep=set(inner))
    try:
        result.made.append(sw.insert_folder(inner, name))
    except SolidWorksError as exc:
        result.failures.append((name, str(exc)))
    return result
