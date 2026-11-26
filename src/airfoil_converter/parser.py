"""Read the source formats: an airfoil-plotter CSV, a UIUC coordinate file, or a curve file of XYZ points."""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Point2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

AIRFOIL = "airfoil surface"
CAMBER = "camber line"
CHORD = "chord line"


# The metadata key saying what units a section's points are in, when it is
# not millimetres: a UIUC file's points are fractions of its chord.
UNITS = "Units"
CHORD_FRACTIONS = "fractions of the chord"


class AirfoilParseError(Exception):
    """The CSV could not be read as an airfoil-plotter export."""


@dataclass
class AirfoilData:
    name: str
    chord: float
    metadata: Dict[str, str] = field(default_factory=dict)
    sections: Dict[str, List[Point2]] = field(default_factory=dict)

    @property
    def airfoil(self) -> List[Point2]:
        return self.sections[AIRFOIL]

    @property
    def camber(self) -> Optional[List[Point2]]:
        return self.sections.get(CAMBER)

    @property
    def in_chords(self) -> bool:
        """The points are fractions of the chord, not millimetres: it needs a target chord."""
        return self.metadata.get(UNITS) == CHORD_FRACTIONS


def _is_section_title(row: Sequence[str]) -> bool:
    """Section titles carry a single non-empty cell: ``Airfoil surface,``."""
    return bool(row[0]) and all(cell == "" for cell in row[1:])


def _as_point(row: Sequence[str]) -> Optional[Point2]:
    try:
        return float(row[0]), float(row[1])
    except (ValueError, IndexError):
        return None


def _chord_from_sections(sections: Dict[str, List[Point2]]) -> Optional[float]:
    """Fall back to the length of the chord line when the header lacks Chord(mm)."""
    chord_line = sections.get(CHORD)
    if not chord_line or len(chord_line) < 2:
        return None
    (x0, y0), (x1, y1) = chord_line[0], chord_line[-1]
    length = math.hypot(x1 - x0, y1 - y0)
    return length if length > 0 else None


def parse_rows(rows: Sequence[Sequence[str]]) -> AirfoilData:
    metadata: Dict[str, str] = {}
    sections: Dict[str, List[Point2]] = {}
    current: Optional[str] = None

    for raw in rows:
        row = [cell.strip() for cell in raw]
        if not row or all(cell == "" for cell in row):
            continue

        if _is_section_title(row):
            current = row[0].lower()
            sections.setdefault(current, [])
            continue

        if current is None:
            if len(row) >= 2:
                metadata[row[0]] = row[1]
            continue

        point = _as_point(row)
        if point is not None:
            sections[current].append(point)
        # Non-numeric rows inside a section are column headers (X(mm),Y(mm)) — skip.

    if AIRFOIL not in sections or not sections[AIRFOIL]:
        raise AirfoilParseError(
            'No "Airfoil surface" points found — is this an airfoil-plotter CSV?'
        )

    chord: Optional[float] = None
    raw_chord = metadata.get("Chord(mm)")
    if raw_chord:
        try:
            chord = float(raw_chord)
        except ValueError:
            chord = None
    if chord is None or chord <= 0:
        chord = _chord_from_sections(sections)
    if chord is None or chord <= 0:
        raise AirfoilParseError("Could not determine the chord length from the CSV.")

    name = metadata.get("Name") or "airfoil"
    return AirfoilData(name=name, chord=chord, metadata=metadata, sections=sections)


def parse_csv(path: str) -> AirfoilData:
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
    except OSError as exc:
        raise AirfoilParseError(f"Could not read {path}: {exc}") from exc
    return parse_rows(rows)


# ---------------------------------------------------------------- curve files


def _read_text(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            return handle.readlines()
    except OSError as exc:
        raise AirfoilParseError(f"Could not read {path}: {exc}") from exc


# Millimetres per unit, longest suffix first so "mm" is never read as "m".
UNIT_SCALES = (("mm", 1.0), ("cm", 10.0), ("in", 25.4), ("m", 1000.0))


def _as_number(field: str) -> Optional[float]:
    """A coordinate, with or without a unit suffix, in millimetres.

    A bare number wins outright, so nothing that parsed before parses
    differently now — that also keeps "inf" and "1e-3" out of the suffix path.
    Only when the plain read fails is a trailing unit considered, and only one
    of the units we know; anything else stays a parse failure.
    """
    try:
        return float(field)
    except ValueError:
        pass

    lowered = field.lower()
    for suffix, scale in UNIT_SCALES:
        if lowered.endswith(suffix):
            try:
                return float(field[: -len(suffix)]) * scale
            except ValueError:
                return None
    return None


def _as_xyz(line: str) -> Optional[Vec3]:
    fields = line.replace(",", " ").split()
    if len(fields) != 3:
        return None
    values = [_as_number(field) for field in fields]
    if any(value is None for value in values):
        return None
    return values[0], values[1], values[2]


def is_curve_file(path: str) -> bool:
    """A curve file leads with a point, not with an airfoil-plotter header row."""
    for line in _read_text(path):
        if line.strip():
            return _as_xyz(line) is not None
    return False


def parse_curve(path: str) -> List[Vec3]:
    """Read a Curve Through XYZ Points file: one point per line, three columns, in mm."""
    points: List[Vec3] = []
    for number, line in enumerate(_read_text(path), start=1):
        if not line.strip():
            continue
        point = _as_xyz(line)
        if point is None:
            raise AirfoilParseError(
                f"Line {number} of {path} is not three numbers: {line.strip()!r}. "
                "A curve file holds one XYZ point per line."
            )
        points.append(point)

    if len(points) < 3:
        raise AirfoilParseError(f"{path} holds {len(points)} points — too few to be a curve.")
    return points


# ------------------------------------------------------- UIUC coordinate files
#
# The UIUC Airfoil Coordinates Database's .dat files: a title line, then one
# "x y" pair per line in fractions of the chord. Two layouts are in use. Selig
# runs as one contour from the upper trailing edge round the nose to the lower
# trailing edge — the plotter CSV's own order. Lednicer gives a row of two
# point counts, then the upper surface and the lower surface each from the
# nose to the tail, as separate blocks.

DAT_SELIG = "Selig"
DAT_LEDNICER = "Lednicer"


def _as_pair(line: str) -> Optional[Point2]:
    fields = line.split()
    if len(fields) != 2:
        return None
    try:
        x, y = float(fields[0]), float(fields[1])
    except ValueError:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def _dat_rows(
    lines: Sequence[str],
) -> Tuple[Optional[str], List[Optional[Point2]], Optional[Tuple[int, str]]]:
    """The title, if there is one, the rows after it, and the first line that is neither.

    A row is a pair, or None for a blank line. The third item names the first
    line after the title that is not two numbers, by its number and text, so
    a CSV or a curve file is never mistaken for a coordinate file and a broken
    one says where it broke.
    """
    body = list(enumerate(lines, start=1))
    while body and not body[0][1].strip():
        body.pop(0)
    if not body:
        return None, [], None
    title: Optional[str] = None
    if _as_pair(body[0][1]) is None:
        title = body.pop(0)[1].strip()
    rows: List[Optional[Point2]] = []
    for number, line in body:
        if not line.strip():
            rows.append(None)
            continue
        pair = _as_pair(line)
        if pair is None:
            return title, rows, (number, line.strip())
        rows.append(pair)
    while rows and rows[-1] is None:
        rows.pop()
    return title, rows, None


def is_dat_file(path: str) -> bool:
    """A UIUC file is a title line, if any, then nothing but pairs of numbers."""
    _, rows, bad = _dat_rows(_read_text(path))
    return bad is None and sum(1 for row in rows if row is not None) >= 3


def _is_count_row(row: Point2) -> bool:
    """Lednicer's first row counts the upper and lower points: two whole numbers above 1."""
    return all(v >= 2.0 and v == int(v) for v in row)


def _blocks(rows: Sequence[Optional[Point2]]) -> List[List[Point2]]:
    blocks: List[List[Point2]] = []
    current: List[Point2] = []
    for row in rows:
        if row is None:
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(row)
    if current:
        blocks.append(current)
    return blocks


def _surfaces(
    counts: Point2, rest: Sequence[Optional[Point2]]
) -> Optional[Tuple[List[Point2], List[Point2]]]:
    """A Lednicer file's two surfaces, if its first row really counts them.

    Two blocks must hold the counted points, the lower one perhaps leaving out
    the nose the two share; a mismatch is refused. Without blank lines between
    them the counts must add up to the points there are, or the first row is
    taken for a point after all and the file read as Selig.
    """
    upper_count, lower_count = int(counts[0]), int(counts[1])
    blocks = _blocks(rest)
    flat = [p for block in blocks for p in block]
    if len(blocks) == 2:
        upper, lower = blocks
        if len(upper) == upper_count and len(lower) in (lower_count, lower_count - 1):
            upper = _nose_first(upper)
            return upper, _lednicer_lower(upper[0], lower, lower_count)
        raise AirfoilParseError(
            f"The count row promises {upper_count} upper and {lower_count} lower points, "
            f"but the two blocks hold {len(upper)} and {len(lower)}. A Lednicer file gives "
            "each surface as its own block, as many points as it counts."
        )
    if len(blocks) == 1:
        if len(flat) == upper_count + lower_count:
            return _nose_first(flat[:upper_count]), _nose_first(flat[upper_count:])
        if len(flat) == upper_count + lower_count - 1:
            upper = _nose_first(flat[:upper_count])
            return upper, _lednicer_lower(upper[0], flat[upper_count:], lower_count)
    return None


def _nose_first(surface: List[Point2]) -> List[Point2]:
    return list(reversed(surface)) if surface[0][0] > surface[-1][0] else surface


def _lednicer_lower(nose: Point2, lower: List[Point2], count: int) -> List[Point2]:
    lower = _nose_first(lower)
    return lower if len(lower) == count else [nose] + lower


def _upper_first(contour: List[Point2]) -> List[Point2]:
    """The contour running from the upper trailing edge, as the plotter CSV's does.

    Run that way round — tail, top, nose, bottom — a section encloses its area
    counter-clockwise; one that encloses it clockwise is turned round.
    """
    n = len(contour)
    area = sum(
        contour[i][0] * contour[(i + 1) % n][1] - contour[(i + 1) % n][0] * contour[i][1]
        for i in range(n)
    )
    return list(reversed(contour)) if area < 0.0 else contour


def parse_dat_lines(lines: Sequence[str], name: str = "airfoil") -> AirfoilData:
    """Read a UIUC coordinate file's lines, in either layout.

    The points come back as the plotter CSV gives them — one contour from the
    upper trailing edge round the nose to the lower, whichever way round the
    file runs — and in the file's own units, normally fractions of the chord.
    The chord runs from the origin, where a UIUC file puts the leading edge, to
    the midpoint of the trailing edge along x. A cambered profile can extend
    ahead of the origin or have a slanted trailing edge without changing its
    nominal chord. The title names the section; ``name`` stands in when
    there is none. A Lednicer file's count row is kept in the metadata, never
    read as a point, and the nose its two surfaces share is given once.
    """
    title, rows, bad = _dat_rows(lines)
    if bad is not None:
        number, text = bad
        raise AirfoilParseError(
            f"Line {number} is not an x y pair: {text!r}. A UIUC coordinate file holds "
            "a title, then one x y pair per line."
        )
    points = [row for row in rows if row is not None]
    if len(points) < 3:
        raise AirfoilParseError(
            "This is not a UIUC coordinate file: after the title it should hold one "
            "x y pair per line."
        )

    metadata: Dict[str, str] = {UNITS: CHORD_FRACTIONS}
    surfaces = None
    if _is_count_row(points[0]):
        surfaces = _surfaces(points[0], rows[rows.index(points[0]) + 1:])
    if surfaces is not None:
        upper, lower = surfaces
        if len(upper) < 2 or len(lower) < 2:
            raise AirfoilParseError("Each surface of a Lednicer file needs at least two points.")
        metadata["Format"] = DAT_LEDNICER
        metadata["Points"] = f"{int(points[0][0])} {int(points[0][1])}"
        contour = list(reversed(upper))
        if math.hypot(lower[0][0] - upper[0][0], lower[0][1] - upper[0][1]) <= 1e-12:
            contour += lower[1:]
        else:
            contour += lower
    else:
        metadata["Format"] = DAT_SELIG
        contour = points
    contour = _upper_first(contour)

    xs = [x for x, _ in contour]
    chord = 0.5 * xs[0] + 0.5 * xs[-1]
    if chord <= 0.0 or max(xs) == min(xs):
        raise AirfoilParseError("The coordinates span no chord.")
    label = title or name
    metadata["Name"] = label
    return AirfoilData(name=label, chord=chord, metadata=metadata, sections={AIRFOIL: contour})


def parse_dat(path: str) -> AirfoilData:
    """Read a UIUC .dat coordinate file; the file name stands in for a missing title."""
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        return parse_dat_lines(_read_text(path), name)
    except AirfoilParseError as exc:
        raise AirfoilParseError(f"{path}: {exc}") from exc
