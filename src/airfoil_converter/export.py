"""The form as data, and the curves an export produces from it.

Two things live here that used to live in the window. The first is
:class:`ExportSpec`, every field of the form captured as the text the user
typed — which is what lets a curve already in a SolidWorks part be selected
later and its settings put back. The second is :func:`build_curves`, which
turns a spec and a parsed source into the curves to write.

Keeping both out of ``gui.py`` is what makes them testable: the whole path
from "these settings" to "these 3D points" can be exercised without a window.

Every value stays a string on the way through, because ``""`` means something
different from ``"0"`` — a blank target chord keeps the source's chord, while
``"0"`` is a chord of nothing — and because a number that round-trips through
``float`` comes back as ``175.0`` when the user typed ``175``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import geometry, writer
from .geometry import GeometryError  # noqa: F401  (re-exported for gui.py)
from .parser import AirfoilData

Point2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

SCHEMA = 1

TE_CLOSE = "Auto-close"
TE_OPEN = "Leave open"
TE_SPLIT = "Split upper/lower"
TE_LINE = "Close with TE line"
TE_MODES = (TE_CLOSE, TE_OPEN, TE_SPLIT, TE_LINE)

MODE_3POINTS = "3points"
MODE_2POINTS = "2points"
MODE_NORMAL = geometry.NORMAL_LINE
MODE_LOADED = geometry.LOADED

# In-plane rotation, in quarter turns: the label's index is the number of turns.
ROTATIONS = ("0°", "90°", "180°", "270°")

OFFSET_INWARD = "Inward"
OFFSET_OUTWARD = "Outward"
OFFSET_DIRECTIONS = (OFFSET_INWARD, OFFSET_OUTWARD)

# A role is what a curve *is*, and it is all that a feature name carries. The
# offset and the trailing-edge thickness deliberately do not appear: they are
# exactly the settings a user changes between exports, and a name that moved
# with them would make every tweak insert a second curve and orphan the loft.
ROLE_AIRFOIL = "airfoil"
ROLE_UPPER = "airfoil_upper"
ROLE_LOWER = "airfoil_lower"
ROLE_TE = "airfoil_te"
ROLE_CAMBER = "camber"
# Not a curve this app writes: the aerofoil and the line closing its blunt
# trailing edge, joined inside SolidWorks so a loft has one thing to pick.
ROLE_JOINED = "airfoil_joined"
ROLES = (ROLE_AIRFOIL, ROLE_UPPER, ROLE_LOWER, ROLE_TE, ROLE_CAMBER, ROLE_JOINED)


class InputError(Exception):
    """A field on the form could not be read."""


def parse_float(text: str, name: str) -> float:
    try:
        return float(text.strip())
    except ValueError:
        raise InputError(f"{name} must be a number (got {text.strip()!r}).") from None


@dataclass(frozen=True)
class ExportSpec:
    """Every setting on the form, as typed."""

    export_airfoil: bool = True
    export_camber: bool = True
    te_mode: str = TE_CLOSE
    te_thickness: str = "0"
    keep_chord: bool = False
    target_chord: str = ""
    offset: str = ""
    offset_dir: str = OFFSET_INWARD
    plane_mode: str = "XY"
    constraint: str = geometry.PERPENDICULAR
    main_plane: str = "XY"
    chord_axis: str = ""
    up_axis: str = ""
    flip: bool = False
    rotation: str = ROTATIONS[0]
    pitch: str = "0"
    p1: Tuple[str, str, str] = ("0", "0", "0")
    p2: Tuple[str, str, str] = ("0", "0", "0")
    p3: Tuple[str, str, str] = ("0", "0", "0")
    leading_edge: Tuple[str, str, str] = ("0", "0", "0")
    extension: str = ".sldcrv"
    # Whether the leading edge is the user's own value rather than an
    # auto-filled default. Restoring a spec without it lets the plane-mode
    # handler clear the flag and quietly overwrite the leading edge with P1.
    le_manual: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key in ("p1", "p2", "p3", "leading_edge"):
            data[key] = list(data[key])
        return data

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ExportSpec":
        known = {f.name for f in fields(cls)}
        values: Dict[str, Any] = {k: v for k, v in raw.items() if k in known}
        for key in ("p1", "p2", "p3", "leading_edge"):
            if key in values:
                triple = list(values[key])
                if len(triple) != 3:
                    raise InputError(f"{key} must hold three values.")
                values[key] = (str(triple[0]), str(triple[1]), str(triple[2]))
        return cls(**values)

    # -- reading the fields ------------------------------------------------

    def quarter_turns(self) -> int:
        try:
            return ROTATIONS.index(self.rotation)
        except ValueError:
            raise InputError(
                f"Rotation must be one of {', '.join(ROTATIONS)} (got {self.rotation!r})."
            ) from None

    def pitch_degrees(self) -> float:
        text = self.pitch.strip()
        return parse_float(text, "Angle of attack") if text else 0.0

    def target_chord_mm(self) -> Optional[float]:
        text = self.target_chord.strip()
        return parse_float(text, "Target chord") if text else None

    def te_thickness_mm(self) -> float:
        """Finished millimetres. Zero means the section keeps its own trailing edge."""
        text = self.te_thickness.strip()
        if not text:
            return 0.0
        value = parse_float(text, "TE thickness")
        if value < 0.0:
            raise InputError("TE thickness must not be negative.")
        return value

    def offset_mm(self) -> float:
        """Finished millimetres, positive outward and negative inward."""
        text = self.offset.strip()
        if not text:
            return 0.0
        value = parse_float(text, "Offset")
        if value < 0.0:
            raise InputError(
                "Offset must not be negative — choose Inward or Outward instead."
            )
        return -value if self.offset_dir == OFFSET_INWARD else value

    def vector(self, name: str) -> Vec3:
        labels = {
            "p1": "P1", "p2": "P2", "p3": "P3", "leading_edge": "Leading edge",
        }
        texts = getattr(self, name)
        values = [parse_float(t, f"{labels[name]} {axis}") for t, axis in zip(texts, "XYZ")]
        return (values[0], values[1], values[2])


@dataclass
class Curve:
    """One curve: one file on disk, one feature in the part."""

    role: str
    points: List[Vec3]
    closed: bool
    feature: str = ""
    filename: str = ""


def feature_name(stem: str, role: str, index: int = 1) -> str:
    """The name a curve carries in SolidWorks, and the stem of its file.

    The index disambiguates several records cut from one source — six ribs from
    one aerofoil all want to be ``sd7037-il_airfoil`` — and is allocated once,
    when the record is created. It describes nothing, so it never moves when a
    setting changes.
    """
    if role not in ROLES:
        raise InputError(f"Unknown curve role {role!r}.")
    if index < 1:
        raise InputError(f"Name index must be 1 or more (got {index}).")
    suffix = f"_{index}" if index > 1 else ""
    return f"{writer.sanitize(stem)}_{role}{suffix}"


def folder_name(stem: str, index: int = 1) -> str:
    """What the tree folder holding one export's curves is called.

    The same shape as the curve names it holds, so a folder and its contents
    read as one thing: ``rib`` holds ``rib_airfoil``, ``rib_2`` holds
    ``rib_airfoil_2``.
    """
    if index < 1:
        raise InputError(f"Name index must be 1 or more (got {index}).")
    suffix = f"_{index}" if index > 1 else ""
    return f"{writer.sanitize(stem)}{suffix}"


def joinable(curves: Sequence[Curve]) -> Tuple[str, ...]:
    """The curves that should become one, or nothing.

    Only the *Close with TE line* pair qualifies. The camber is a separate
    thing and stays separate, and the split halves enclose no area, so joining
    them would say something untrue about the shape.
    """
    roles = {curve.role: curve.feature for curve in curves}
    if ROLE_AIRFOIL in roles and ROLE_TE in roles:
        return (roles[ROLE_AIRFOIL], roles[ROLE_TE])
    return ()


def plane_frame(spec: ExportSpec, section: Optional[geometry.FlatSection]) -> Tuple[Vec3, Vec3]:
    """The chord and up vectors the section is laid out along."""
    mode = spec.plane_mode
    kwargs: Dict[str, Any] = {
        "flip": spec.flip,
        "quarter_turns": spec.quarter_turns(),
        "pitch": spec.pitch_degrees(),
    }
    if mode in geometry.MAIN_PLANES:
        kwargs.update(chord_axis=spec.chord_axis, up_axis=spec.up_axis)
    elif mode == MODE_LOADED:
        if section is None:
            raise InputError("Load a curve file to export it on its own plane.")
        kwargs.update(frame=(section.u, section.v))
    elif mode == MODE_3POINTS:
        kwargs.update(p1=spec.vector("p1"), p2=spec.vector("p2"), p3=spec.vector("p3"))
    elif mode == MODE_2POINTS:
        kwargs.update(
            p1=spec.vector("p1"),
            p2=spec.vector("p2"),
            constraint=spec.constraint,
            main_plane=spec.main_plane,
        )
    elif mode == MODE_NORMAL:
        kwargs.update(p1=spec.vector("p1"), p2=spec.vector("p2"))
    return geometry.plane_frame(mode, **kwargs)


def build_sections(data: AirfoilData, spec: ExportSpec) -> List[Tuple[str, List[Point2], bool]]:
    """The 2D curves an export produces, as ``(role, points, closed)``."""
    curves: List[Tuple[str, List[Point2], bool]] = []
    factor = geometry.scale_factor(data.chord, spec.target_chord_mm())

    if spec.export_airfoil:
        surface = data.airfoil

        offset = spec.offset_mm()
        if offset:
            # The offset is quoted in finished millimetres, so it has to be
            # undone by the rescale that to_3d will apply to these points.
            surface = geometry.offset_airfoil(surface, offset / factor)

        # Blunting comes after the offset, so the cut lands on the curve that
        # actually gets exported. It too is quoted in finished millimetres.
        thickness = spec.te_thickness_mm()
        if thickness:
            surface = geometry.blunt_trailing_edge(
                surface, thickness / factor, keep_chord=spec.keep_chord
            )

        mode = spec.te_mode
        if mode == TE_OPEN:
            curves.append((ROLE_AIRFOIL, geometry.drop_duplicate(surface), False))
        elif mode == TE_CLOSE:
            curves.append((ROLE_AIRFOIL, geometry.auto_close(surface), True))
        elif mode == TE_SPLIT:
            upper, lower = geometry.split_surfaces(surface)
            curves.append((ROLE_UPPER, upper, False))
            curves.append((ROLE_LOWER, lower, False))
        elif mode == TE_LINE:
            # The gap is closed by its own two-point curve, which imports as a
            # straight line; closing it inside the spline would bulge it.
            curves.append((ROLE_AIRFOIL, geometry.drop_duplicate(surface), False))
            curves.append((ROLE_TE, geometry.trailing_edge_line(surface), False))
        else:
            raise InputError(f"Unknown TE handling mode {mode!r}.")

    if spec.export_camber:
        camber = data.camber
        if not camber:
            raise InputError("This CSV has no camber line to export.")
        curves.append((ROLE_CAMBER, list(camber), False))

    if not curves:
        raise InputError("Nothing selected to export.")
    return curves


def build_curves(
    data: AirfoilData,
    spec: ExportSpec,
    stem: str,
    section: Optional[geometry.FlatSection] = None,
    index: int = 1,
) -> List[Curve]:
    """Everything an export produces: named, placed in space, ready to write."""
    factor = geometry.scale_factor(data.chord, spec.target_chord_mm())
    u, v = plane_frame(spec, section)
    leading_edge = spec.vector("leading_edge")

    if spec.extension not in writer.EXTENSIONS:
        raise InputError(f"Unsupported extension {spec.extension!r}.")

    built: List[Curve] = []
    for role, points2d, closed in build_sections(data, spec):
        name = feature_name(stem, role, index)
        built.append(
            Curve(
                role=role,
                points=geometry.to_3d(points2d, leading_edge, u, v, factor),
                closed=closed,
                feature=name,
                filename=name + spec.extension,
            )
        )
    return built
