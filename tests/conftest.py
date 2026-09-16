"""Wings to test with, built from rib records the way the window would make them."""

import math
import os

import pytest

from airfoil_converter import export, store, writer

SAMPLE_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sd7037-il.csv")


class SyntheticWing:
    """Ribs on planes of constant X, span running along -X, nose toward +Z.

    ``le_z``/``le_y`` give the leading edge at a station, ``te_z`` the trailing
    edge, so every rib stands where the edge curves say it should.
    """

    def __init__(self, stations, le_z, te_z, le_y=lambda s: 0.0,
                 te_mode=export.TE_CLOSE, te_thickness="0", keep_chord=False,
                 lean=lambda s: 0.0):
        self.lean = lean
        self.stations = list(stations)
        self.le_z = le_z
        self.te_z = te_z
        self.le_y = le_y
        self.te_mode = te_mode
        self.te_thickness = te_thickness
        self.keep_chord = keep_chord

    def point(self, s, z):
        return (-s, self.le_y(s), z)

    def up(self, s):
        """Which way is up on the rib at ``s``: leaning with the dihedral by ``lean``."""
        lean = math.radians(self.lean(s))
        return (math.sin(lean), math.cos(lean), 0.0)

    def spec(self, s):
        chord = self.le_z(s) - self.te_z(s)
        le = self.point(s, self.le_z(s))
        common = dict(
            export_camber=False,
            target_chord=repr(chord),
            leading_edge=tuple(repr(c) for c in le),
            te_mode=self.te_mode,
            te_thickness=self.te_thickness,
            keep_chord=self.keep_chord,
        )
        if not self.lean(s):
            return export.ExportSpec(plane_mode="YZ", chord_axis="+Z", up_axis="+Y", **common)
        # A leaning rib stands on a plane through three points: the nose, the
        # tail straight aft of it, and a point straight up the leaning rib.
        up = self.up(s)
        tail = (le[0], le[1], le[2] - chord)
        top = tuple(c + 50.0 * u for c, u in zip(le, up))
        return export.ExportSpec(
            plane_mode="3points",
            p1=tuple(repr(c) for c in le),
            p2=tuple(repr(c) for c in tail),
            p3=tuple(repr(c) for c in top),
            **common,
        )

    def records(self):
        return [
            store.ExportRecord(
                export_id=f"exp-{k}",
                name_index=k + 1,
                stem="rib",
                source=SAMPLE_CSV,
                settings=self.spec(s).to_dict(),
            )
            for k, s in enumerate(self.stations)
        ]

    def sidecar(self):
        return store.Sidecar(exports=self.records())

    def edge_file(self, folder, which, count=201):
        z = self.le_z if which == "le" else self.te_z
        end = self.stations[-1]
        # Crowd the samples toward the tip, where a hooked edge turns.
        spans = [end * math.sin(0.5 * math.pi * k / (count - 1)) for k in range(count)]
        path = os.path.join(str(folder), f"{which}.sldcrv")
        writer.write_curve(path, [self.point(s, z(s)) for s in spans])
        return path

    def wing_spec(self, folder=None, **overrides):
        base = dict(ribs=tuple(r.export_id for r in self.records()))
        if folder is not None:
            base.update(le_source=self.edge_file(folder, "le"), te_source=self.edge_file(folder, "te"))
        base.update(overrides)
        return export.WingSpec(**base)


@pytest.fixture
def straight_wing():
    """Unswept, untapered: a flat offset is already the true one."""
    return SyntheticWing([0.0, 300.0], le_z=lambda s: 0.0, te_z=lambda s: -200.0)


@pytest.fixture
def swept_wing():
    """Swept 30° throughout, chord constant."""
    t = math.tan(math.radians(30.0))
    return SyntheticWing(
        [0.0, 200.0], le_z=lambda s: -s * t, te_z=lambda s: -s * t - 200.0
    )


def hooked_le(s):
    """A leading edge that runs nearly straight, then hooks back hard at the tip."""
    e = 1000.5
    return -140.0 * (1.0 - math.sqrt(max(0.0, 1.0 - (s / e) ** 2)))


@pytest.fixture
def hooked_wing():
    """Like the wing that prompted all this: 1000 mm, a straight TE, 2° dihedral."""
    return SyntheticWing(
        [0.0, 500.0, 900.0, 1000.0],
        le_z=hooked_le,
        te_z=lambda s: -275.0,
        le_y=lambda s: s * math.tan(math.radians(2.0)),
    )


DIHEDRAL = 5.0


@pytest.fixture
def dihedral_wing():
    """Straight and untapered, raised 5°: a vertical root, then ribs square to the dihedral.

    Beyond the root it is a prism along the dihedral line, so a rib square to
    that line is offset exactly as a flat section would be.
    """
    t = math.tan(math.radians(DIHEDRAL))
    return SyntheticWing(
        [0.0, 300.0, 600.0],
        le_z=lambda s: 0.0,
        te_z=lambda s: -200.0,
        le_y=lambda s: s * t,
        lean=lambda s: DIHEDRAL if s > 0.0 else 0.0,
    )


@pytest.fixture
def hooked_leaning_wing():
    """The hooked wing with its tip rib square to its 2° dihedral, and a vertical root."""
    return SyntheticWing(
        [0.0, 1000.0],
        le_z=hooked_le,
        te_z=lambda s: -275.0,
        le_y=lambda s: s * math.tan(math.radians(2.0)),
        lean=lambda s: 2.0 if s > 0.0 else 0.0,
    )
