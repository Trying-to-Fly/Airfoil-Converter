"""Read the two source formats: an airfoil-plotter CSV, or a curve file of XYZ points."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Point2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

AIRFOIL = "airfoil surface"
CAMBER = "camber line"
CHORD = "chord line"


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
