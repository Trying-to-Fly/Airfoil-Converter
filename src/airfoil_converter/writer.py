"""Write SolidWorks Curve Through XYZ Points files (.sldcrv / .txt).

Three tab-separated columns, six decimals, every value carrying its unit, CRLF
line endings. Tab is the delimiter SolidWorks documents as safe for the import
dialog, and the unit suffix is what stops the document's own unit setting
changing the result: the API holds curve points in metres whatever the file
says, so ``175.000000mm`` lands as 175 mm in an inch document just as it does
in a millimetre one.

Curve files are read back by :mod:`airfoil_converter.parser`, which strips the
suffix again, so a file written here is still valid input to the app.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]

EXTENSIONS = (".sldcrv", ".txt")
DECIMALS = 6
DELIMITER = "\t"
UNIT_SUFFIX = "mm"
LINE_ENDING = "\r\n"


def format_value(value: float, decimals: int = DECIMALS, unit: str = UNIT_SUFFIX) -> str:
    """One coordinate, fixed-point, with its unit and without a signed zero.

    The sign is stripped *after* formatting, not before. Testing ``value == 0``
    would leave -1e-9 to print as ``-0.000000``, and a section that differs from
    its mirror by a minus sign on a zero is not bit-identical to it.
    """
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and float(text) == 0.0:
        text = text[1:]
    return text + unit


def format_points(
    points: Sequence[Vec3],
    decimals: int = DECIMALS,
    unit: str = UNIT_SUFFIX,
    delimiter: str = DELIMITER,
) -> str:
    """One point per line, three columns, terminated by CRLF including the last."""
    rows = [
        delimiter.join(format_value(c, decimals, unit) for c in point) for point in points
    ]
    return "".join(row + LINE_ENDING for row in rows)


def curve_bytes(
    points: Sequence[Vec3],
    decimals: int = DECIMALS,
    unit: str = UNIT_SUFFIX,
    delimiter: str = DELIMITER,
) -> bytes:
    """Exactly the bytes that land on disk, so a hash of them means something."""
    if not points:
        raise ValueError("Refusing to build an empty curve.")
    return format_points(points, decimals, unit, delimiter).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize(stem: str) -> str:
    """A source name reduced to something legal as a file name and a feature name."""
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", stem).strip().strip(".")
    return cleaned or "airfoil"


def write_curve(path: str, points: Sequence[Vec3]) -> int:
    """Write the curve and return the number of points written."""
    data = curve_bytes(points)
    with open(path, "wb") as handle:
        handle.write(data)
    return len(points)


def write_curve_if_changed(path: str, points: Sequence[Vec3]) -> Tuple[bool, str]:
    """Write the curve only if its bytes differ, and report ``(changed, sha256)``.

    The write goes to a temporary file beside the target and is then renamed
    over it, so SolidWorks — which re-reads these files on every refresh — can
    never see a half-written curve. Leaving an unchanged file alone keeps its
    modification time meaningful and lets a re-export skip the feature entirely.
    """
    data = curve_bytes(points)
    digest = sha256_hex(data)

    if os.path.exists(path):
        try:
            with open(path, "rb") as handle:
                if handle.read() == data:
                    return False, digest
        except OSError:
            pass  # unreadable: fall through and overwrite

    temporary = path + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(data)
    os.replace(temporary, path)
    return True, digest
