"""Write SolidWorks Curve Through XYZ Points files (.sldcrv / .txt)."""

from __future__ import annotations

import os
import re
from typing import Sequence, Tuple

Vec3 = Tuple[float, float, float]

EXTENSIONS = (".sldcrv", ".txt")
DECIMALS = 6


def format_points(points: Sequence[Vec3]) -> str:
    """One point per line, three space-separated columns, 6 decimals, in mm."""
    lines = [" ".join(f"{c:.{DECIMALS}f}" for c in point) for point in points]
    return "\n".join(lines) + "\n"


def _sanitize(stem: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", stem).strip().strip(".")
    return cleaned or "airfoil"


def output_path(folder: str, stem: str, suffix: str, extension: str) -> str:
    if extension not in EXTENSIONS:
        raise ValueError(f"Unsupported extension {extension!r}.")
    return os.path.join(folder, f"{_sanitize(stem)}_{suffix}{extension}")


def write_curve(path: str, points: Sequence[Vec3]) -> int:
    """Write the curve and return the number of points written."""
    if not points:
        raise ValueError(f"Refusing to write an empty curve to {path}.")
    with open(path, "w", encoding="ascii", newline="\r\n") as handle:
        handle.write(format_points(points))
    return len(points)
