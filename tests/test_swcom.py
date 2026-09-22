"""The parts of the SolidWorks link that are decidable without SolidWorks.

The version gate and the session choice are pure functions over plain data
precisely so they can be tested here, on a machine with no CAD package at all.
"""

import threading
import time

import pytest

from airfoil_converter import swcom
from airfoil_converter.swcom import (
    Candidate,
    NotRunning,
    SolidWorksError,
    WrongVersion,
    choose,
    is_solidworks_moniker,
    parse_revision,
    pid_from_moniker,
    release_year,
)


def session(major, minor=0, patch=0, pid=1000, has_doc=False):
    return Candidate(
        moniker=f"SolidWorks_PID_{pid}",
        pid=pid,
        revision=(major, minor, patch),
        has_active_doc=has_doc,
    )


# -- reading a version ------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [("34.0.0", (34, 0, 0)), ("34", (34,)), ("34.1.0", (34, 1, 0)), (" 33.3.0 ", (33, 3, 0))],
)
def test_parse_revision(text, expected):
    assert parse_revision(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "twenty-six", "34.x.0"])
def test_parse_revision_refuses_what_is_not_a_version(text):
    with pytest.raises(SolidWorksError, match="Could not read a version"):
        parse_revision(text)


@pytest.mark.parametrize("major,year", [(32, 2024), (33, 2025), (34, 2026), (35, 2027)])
def test_release_year_follows_the_yearly_convention(major, year):
    assert release_year(major) == year


# -- recognising a session --------------------------------------------------


@pytest.mark.parametrize("name", ["SolidWorks_PID_31392", "solidworks_pid_7"])
def test_a_session_moniker_is_recognised(name):
    assert is_solidworks_moniker(name)


@pytest.mark.parametrize(
    "name", ["", "!SolidWorks", "SolidWorksDocMgr", "SolidWorks_PID_", "SolidWorks_PID_x"]
)
def test_anything_else_is_not_a_session(name):
    assert not is_solidworks_moniker(name)


def test_the_pid_comes_out_of_the_moniker():
    """No round-trip to a session we may be about to discard."""
    assert pid_from_moniker("SolidWorks_PID_31392") == 31392


# -- choosing which session to drive ----------------------------------------


def test_nothing_running_says_so():
    with pytest.raises(NotRunning, match="No running SolidWorks"):
        choose([])


def test_only_older_versions_are_refused_by_name():
    with pytest.raises(WrongVersion) as raised:
        choose([session(32), session(33)])
    message = str(raised.value)
    assert "SolidWorks 2024" in message
    assert "SolidWorks 2025" in message
    assert "2026" in message


def test_the_supported_version_is_taken():
    assert choose([session(34)]).revision == (34, 0, 0)


def test_an_older_session_alongside_a_supported_one_is_ignored():
    assert choose([session(33), session(34)]).revision == (34, 0, 0)


def test_a_newer_release_is_used_not_refused():
    """The whole point of a floor rather than an equality: 2027 must just work."""
    assert choose([session(34), session(35)]).revision == (35, 0, 0)


def test_a_service_pack_beats_the_base_release():
    assert choose([session(34, 0, 0), session(34, 1, 0)]).revision == (34, 1, 0)


def test_a_session_with_a_document_open_wins_a_tie():
    """Almost certainly the window the user is looking at."""
    quiet = session(34, pid=100, has_doc=False)
    busy = session(34, pid=200, has_doc=True)
    assert choose([quiet, busy]).pid == 200


def test_the_choice_is_deterministic_when_nothing_separates_them():
    assert choose([session(34, pid=300), session(34, pid=100)]).pid == 100


def test_a_release_newer_than_tested_is_flagged_but_still_chosen():
    chosen = choose([session(36)])
    assert chosen.revision == (36, 0, 0)
    assert swcom.is_newer_than_tested(chosen.revision)
    assert not swcom.is_newer_than_tested((34, 0, 0))


@pytest.mark.parametrize("revision,ok", [((33, 9, 9), False), ((34, 0, 0), True), ((40, 0), True)])
def test_the_floor_is_a_floor(revision, ok):
    assert swcom.meets_minimum(revision) is ok


# -- degrading without pywin32 ----------------------------------------------


def test_finding_sessions_without_pywin32_says_why(monkeypatch):
    monkeypatch.setattr(swcom, "pythoncom", None)
    assert swcom.is_available() is False
    assert swcom.unavailable_reason()
    with pytest.raises(swcom.NotAvailable):
        swcom.find_candidates()


def test_the_diagnostic_reports_a_missing_pywin32_rather_than_crashing(monkeypatch, capsys):
    monkeypatch.setattr(swcom, "pythoncom", None)
    assert swcom.main([]) == swcom.EXIT_NOT_AVAILABLE
    assert "pip install pywin32" in capsys.readouterr().out


# -- sketch coordinates -----------------------------------------------------


IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]
# A quarter turn about Z, then 10 along X. Stored by columns, so the first
# three values are where local X ends up and the next three where local Y does.
TURNED = [0, 1, 0, -1, 0, 0, 0, 0, 1, 10, 0, 0, 1]
# What SolidWorks 2026 really reported for the Top and Right planes, with the
# normals they are known to have.
TOP_PLANE = [1, 0, 0, 0, 0, -1, 0, 1, 0, 0, 0, 0, 1]
RIGHT_PLANE = [0, 0, -1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 1]


def test_an_identity_transform_leaves_a_point_where_it_was():
    assert swcom.transform_point(IDENTITY, (1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)


def test_a_transform_turns_and_then_moves():
    """Rotation first, translation second. The other order puts a sketch point
    somewhere plausible but wrong, which is the whole risk of a pick."""
    assert swcom.transform_point(TURNED, (1.0, 0.0, 0.0)) == (10.0, 1.0, 0.0)
    assert swcom.transform_point(TURNED, (0.0, 1.0, 0.0)) == (9.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "data,normal",
    [(IDENTITY, (0.0, 0.0, 1.0)), (TOP_PLANE, (0.0, 1.0, 0.0)), (RIGHT_PLANE, (1.0, 0.0, 0.0))],
)
def test_the_rotation_is_stored_by_columns_not_rows(data, normal):
    """The three reference planes, as SolidWorks 2026 actually reported them.

    Reading the rotation as rows transposes it, which is silent on the Front
    plane because its transform is the identity, and turns the other two
    normals round. That is exactly what the first probe found."""
    assert swcom.transform_point(data, (0.0, 0.0, 1.0)) == normal


def test_the_translation_alone_places_the_sketch_origin():
    assert swcom.transform_point(TURNED, (0.0, 0.0, 0.0)) == (10.0, 0.0, 0.0)


def test_the_scale_at_the_end_is_applied():
    doubled = list(IDENTITY)
    doubled[12] = 2.0
    assert swcom.transform_point(doubled, (1.0, 2.0, 3.0)) == (2.0, 4.0, 6.0)


def test_a_transform_without_a_scale_is_still_read():
    """Twelve values is a valid answer; the scale is only the thirteenth."""
    assert swcom.transform_point(IDENTITY[:12], (1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)


# -- the worker thread ------------------------------------------------------


class FakeSession:
    def __init__(self):
        self.calls = []


def test_the_worker_runs_work_on_its_own_thread_and_returns_plain_data():
    worker = swcom.Worker(connect_fn=FakeSession)
    try:
        call = worker.submit(lambda s: "answer")
        value, error = call.wait(timeout=5)
        assert (value, error) == ("answer", None)
    finally:
        worker.shutdown()


def test_a_failure_comes_back_as_a_value_not_a_dead_thread():
    def explode(_session):
        raise RuntimeError("SolidWorks said no")

    worker = swcom.Worker(connect_fn=FakeSession)
    try:
        value, error = worker.submit(explode).wait(timeout=5)
        assert value is None
        assert isinstance(error, RuntimeError)

        # The thread must still be alive and usable afterwards.
        assert worker.submit(lambda s: 1).wait(timeout=5) == (1, None)
    finally:
        worker.shutdown()


def test_a_failed_call_drops_the_session_so_the_next_one_reconnects():
    made = []

    def connect_fn():
        made.append(object())
        return made[-1]

    def explode(_session):
        raise RuntimeError("connection lost")

    worker = swcom.Worker(connect_fn=connect_fn)
    try:
        worker.submit(lambda s: None).wait(timeout=5)
        assert len(made) == 1
        worker.submit(explode).wait(timeout=5)
        worker.submit(lambda s: None).wait(timeout=5)
        assert len(made) == 2
    finally:
        worker.shutdown()


def test_a_connection_failure_is_reported_not_raised_on_the_thread():
    def refuse():
        raise swcom.NotRunning("nothing there")

    worker = swcom.Worker(connect_fn=refuse)
    try:
        value, error = worker.submit(lambda s: 1).wait(timeout=5)
        assert value is None
        assert isinstance(error, swcom.NotRunning)
    finally:
        worker.shutdown()


def test_polling_returns_at_once_whether_or_not_the_answer_is_ready():
    """The interface polls this on a timer, so it must never wait for anything."""
    started = threading.Event()
    release = threading.Event()

    def slow(_session):
        started.set()
        release.wait(5)
        return 42

    worker = swcom.Worker(connect_fn=FakeSession)
    try:
        call = worker.submit(slow)
        assert started.wait(5)

        # The job is definitely still running, so poll must say "not yet"
        # rather than block until it finishes.
        began = time.monotonic()
        assert call.poll() is None
        assert time.monotonic() - began < 0.5

        release.set()
        assert call.wait(timeout=5) == (42, None)
        assert call.poll() == (42, None)
    finally:
        release.set()
        worker.shutdown()


def test_the_worker_reports_being_busy_while_a_call_is_in_flight():
    """Two interleaved refresh sequences would corrupt a part, so the window
    disables Export on this rather than queueing a second one."""
    started = threading.Event()
    release = threading.Event()

    def slow(_session):
        started.set()
        release.wait(5)
        return None

    worker = swcom.Worker(connect_fn=FakeSession)
    try:
        call = worker.submit(slow)
        assert started.wait(5)
        assert worker.busy() is True
        release.set()
        call.wait(timeout=5)
        for _ in range(100):
            if not worker.busy():
                break
            time.sleep(0.01)
        assert worker.busy() is False
    finally:
        release.set()
        worker.shutdown()


# -- rebuilding -------------------------------------------------------------


class FakeEdge:
    """One edge of a body. Its two ends are what tells the loops apart."""

    def __init__(self, calls, tag, start, end):
        self.calls = calls
        self.tag = tag
        self.start = start        # in metres, as SolidWorks holds them
        self.end = end

    @property
    def GetCurve(self):
        # Reading the parameters without this first is what the help warns of.
        self.calls.append(("GetCurve", self.tag))
        return object()

    @property
    def GetCurveParams2(self):
        return list(self.start) + list(self.end) + [0.0, 1.0, 0.0, 0.0, 0.0]

    def Select4(self, append, data):
        self.calls.append(("Select4", self.tag, append))
        return True


class FakeBody:
    def __init__(self, name, edges):
        self.Name = name
        self.edges = edges

    @property
    def GetEdges(self):
        return list(self.edges)


class FakeFace:
    def __init__(self, body):
        self.body = body

    @property
    def GetBody(self):
        return self.body


class FakeDefinition:
    def __init__(self, points):
        # Metres, flat, as a curve feature hands them back.
        self.PointArray = [c / 1000.0 for p in points for c in p]


class FakeFeature:
    """A feature in the tree. Its name is a property both ways, as COM's is."""

    def __init__(self, name, type_name="RefCurve", body=None, points=None):
        self._name = name
        self._type = type_name
        self.body = body
        self.points = points
        self.next = None

    @property
    def GetFaces(self):
        return [FakeFace(self.body)] if self.body else None

    @property
    def GetDefinition(self):
        return FakeDefinition(self.points) if self.points else None

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        self._name = value

    @property
    def GetTypeName2(self):
        return self._type

    @property
    def GetNextFeature(self):
        return self.next


class FakeManager:
    """The feature manager: the rollback bar, the tree listing, and the two
    calls that cap a surface loft into a solid."""

    def __init__(self, calls, doc, refuse=()):
        self.calls = calls
        self.doc = doc
        self.refuse = set(refuse)
        self.refuse_knit = False

    def EditRollback(self, position, name):
        self.calls.append(("EditRollback", position, name))
        return position not in self.refuse

    def GetFeatures(self, top_only):
        return list(self.doc.features)

    def InsertSewRefSurface(self, gap_filters, form_solid, merge, tolerance, gap_range):
        self.calls.append(
            ("InsertSewRefSurface", gap_filters, form_solid, merge, tolerance, gap_range)
        )
        return None if self.refuse_knit else self.doc.add("Surface-Knit1", "SewRefSurface")


class FakeExtension:
    def __init__(self, calls):
        self.calls = calls
        self.refuse = set()

    def SelectByID2(self, name, kind, x, y, z, append, mark, callout, options):
        self.calls.append(("SelectByID2", name, kind, append, mark))
        return name not in self.refuse


class FakeDoc:
    """A document that records what was asked of it, as late binding would.

    A member taking no arguments is reached as an attribute, so ``EditRebuild3``
    and ``FeatureManager`` have to be properties here and ``ForceRebuild3`` a
    method — which is the distinction :func:`swcom.call` exists to make.
    """

    def __init__(self, refuse=(), features=()):
        self.calls = []
        self.features = []
        self.refuse_cap = False
        self.manager = FakeManager(self.calls, self, refuse)
        self.extension = FakeExtension(self.calls)
        for name in features:
            self.add(name)

    def add(self, name, type_name="RefCurve", body=None, points=None):
        made = FakeFeature(name, type_name, body, points)
        if self.features:
            self.features[-1].next = made
        self.features.append(made)
        return made

    @property
    def FirstFeature(self):
        return self.features[0] if self.features else None

    @property
    def SelectionManager(self):
        return self

    @property
    def CreateSelectData(self):
        return "select-data"

    @property
    def InsertPlanarRefSurface(self):
        self.calls.append(("InsertPlanarRefSurface",))
        if self.refuse_cap:
            return False
        self.add("Surface-Plane1", "RefSurface", body=FakeBody("Surface-Plane1", []))
        return True

    @property
    def Extension(self):
        return self.extension

    def ClearSelection2(self, whole):
        self.calls.append(("ClearSelection2", whole))
        return True

    @property
    def FeatureManager(self):
        return self.manager

    @property
    def EditRebuild3(self):
        self.calls.append(("EditRebuild3",))
        return True

    def ForceRebuild3(self, top_only):
        self.calls.append(("ForceRebuild3", top_only))
        return True


class FakeApp:
    def __init__(self, doc):
        self.ActiveDoc = doc


def rebuilding_session(refuse=(), features=()):
    doc = FakeDoc(refuse, features)
    return swcom.Session(FakeApp(doc), (34, 0, 0), 1000), doc


@pytest.fixture
def no_variants(monkeypatch):
    """A typed COM null needs pythoncom, which is not here. Everything else in
    the two calls below is the real thing."""
    monkeypatch.setattr(swcom, "_null_dispatch", lambda: None)


def test_a_rebuild_regenerates_only_what_changed():
    """ForceRebuild3 redoes all 397 features of the real part, 25 seconds'
    worth; EditRebuild3 redoes the ones a reloaded curve feeds, in 0.8, and
    the geometry it left was identical."""
    session, doc = rebuilding_session()
    assert session.rebuild() is True
    assert doc.calls == [("EditRebuild3",)]


def test_a_forced_rebuild_is_still_reachable():
    session, doc = rebuilding_session()
    assert session.rebuild(force=True) is True
    assert doc.calls == [("ForceRebuild3", False)]


# -- the rollback bar -------------------------------------------------------


def test_the_bar_is_rolled_back_to_just_after_a_feature():
    """Reloading a curve on a live tree cost 25 seconds a curve on the real
    part, and half a second with the bar sitting just after the curves."""
    session, doc = rebuilding_session()
    session.roll_back_to("wing_root_upper")
    assert doc.calls == [("EditRollback", swcom.ROLLBACK_AFTER_FEATURE, "wing_root_upper")]


def test_a_bar_that_will_not_move_is_an_error_not_a_silence():
    session, doc = rebuilding_session(refuse=[swcom.ROLLBACK_AFTER_FEATURE])
    with pytest.raises(SolidWorksError, match="could not be rolled back"):
        session.roll_back_to("wing_root_upper")


def test_rolling_forward_asks_for_the_previous_position():
    """Where the bar was, not the end: the user may have put it somewhere."""
    session, doc = rebuilding_session()
    session.roll_forward()
    assert doc.calls == [("EditRollback", swcom.ROLLBACK_TO_PREVIOUS, "")]


def test_rolling_forward_falls_back_to_the_end():
    session, doc = rebuilding_session(refuse=[swcom.ROLLBACK_TO_PREVIOUS])
    session.roll_forward()
    assert doc.calls == [
        ("EditRollback", swcom.ROLLBACK_TO_PREVIOUS, ""),
        ("EditRollback", swcom.ROLLBACK_TO_END, ""),
    ]


def test_a_tree_that_will_not_roll_forward_at_all_says_so():
    """The one state worse than a slow export: half a part, handed back."""
    session, _ = rebuilding_session(
        refuse=[swcom.ROLLBACK_TO_PREVIOUS, swcom.ROLLBACK_TO_END]
    )
    with pytest.raises(SolidWorksError, match="could not be rolled forward"):
        session.roll_forward()


# -- capping a surface loft into a solid ------------------------------------
#
# The construction these pin was run on SolidWorks 2026 on 2026-09-22; what is
# pinned here is what the app sends it.


def wing_body():
    """A surface loft's body: a loop at each end and the seams between them.

    Metres. The root loop stands in the plane x = 0 and the tip loop in
    x = -0.998, which is where a wing a metre long puts them.
    """
    root = [FakeEdge([], f"root{k}", (0.0, 0.0, -0.05 * k), (0.0, 0.0, -0.05 * (k + 1)))
            for k in range(3)]
    tip = [FakeEdge([], f"tip{k}", (-0.998, 0.0, -0.05 * k), (-0.998, 0.0, -0.05 * (k + 1)))
           for k in range(3)]
    seams = [FakeEdge([], f"seam{k}", (0.0, 0.0, -0.05 * k), (-0.998, 0.0, -0.05 * k))
             for k in range(3)]
    return root, tip, seams


def capping_session(refuse_cap=False):
    root, tip, seams = wing_body()
    session, doc = rebuilding_session()
    for edge in root + tip + seams:
        edge.calls = doc.calls
    doc.add("wing_loft_surface", "BlendRefSurface",
            body=FakeBody("Surface-Loft1", root + tip + seams))
    # The profile the loft ran through at the root: a loop in the plane x = 0.
    doc.add("root_joined", "CurveInFile",
            points=[(0.0, 0.0, 0.0), (0.0, 20.0, -30.0), (0.0, 0.0, -150.0), (0.0, -10.0, -30.0)])
    doc.refuse_cap = refuse_cap
    return session, doc


def test_a_cap_takes_the_end_edges_that_lie_in_the_profile_s_plane(no_variants):
    """The loft's own end edges, told apart by the plane the profile lies in.
    Neither a composite curve nor the curves it joins will do as a boundary —
    SolidWorks refuses those outright, which is what sent this here."""
    session, doc = capping_session()
    assert session.cap_end("wing_loft_surface", "root_joined", "root_cap") == "root_cap"

    assert [c for c in doc.calls if c[0] == "Select4"] == [
        ("Select4", "root0", False), ("Select4", "root1", True), ("Select4", "root2", True),
    ]
    assert ("InsertPlanarRefSurface",) in doc.calls
    # The curve has to be asked for before its parameters can be read.
    assert [c[0] for c in doc.calls].index("GetCurve") < [c[0] for c in doc.calls].index("Select4")
    assert [f.Name for f in doc.features][-1] == "root_cap"


def test_a_cap_at_the_other_end_takes_the_other_loop(no_variants):
    session, doc = capping_session()
    doc.add("tip_joined", "CurveInFile",
            points=[(-998.0, 0.0, 0.0), (-998.0, 10.0, -30.0), (-998.0, 0.0, -150.0)])
    session.cap_end("wing_loft_surface", "tip_joined", "tip_cap")
    assert [c[1] for c in doc.calls if c[0] == "Select4"] == ["tip0", "tip1", "tip2"]


def test_a_composite_profile_is_read_through_the_curves_it_joins(no_variants, monkeypatch):
    """A wing's end profile is a composite of three curves, and a composite has
    no points of its own to read."""
    session, doc = capping_session()
    doc.add("root_composite", "CompositeCurve")
    doc.add("root_upper", "CurveInFile", points=[(0.0, 0.0, 0.0), (0.0, 20.0, -30.0)])
    doc.add("root_lower", "CurveInFile", points=[(0.0, 0.0, -150.0), (0.0, -10.0, -30.0)])
    monkeypatch.setattr(
        swcom.Session, "composite_sources", lambda self, name: ["root_upper", "root_lower"]
    )
    session.cap_end("wing_loft_surface", "root_composite", "root_cap")
    assert [c[1] for c in doc.calls if c[0] == "Select4"] == ["root0", "root1", "root2"]


def test_an_end_with_no_edges_in_that_plane_says_so(no_variants):
    session, doc = capping_session()
    doc.add("nowhere", "CurveInFile",
            points=[(500.0, 0.0, 0.0), (500.0, 20.0, -30.0), (500.0, 0.0, -150.0)])
    with pytest.raises(SolidWorksError, match="No edge of wing_loft_surface lies in the plane"):
        session.cap_end("wing_loft_surface", "nowhere", "root_cap")


def test_a_cap_solidworks_refuses_says_how_many_edges_it_was_given(no_variants):
    """What a sliver face at the tip looks like from here: the loop is there,
    and nothing will put a surface across it."""
    session, _ = capping_session(refuse_cap=True)
    with pytest.raises(SolidWorksError, match="would not put a planar surface across the 3 edges"):
        session.cap_end("wing_loft_surface", "root_joined", "root_cap")


def test_a_knit_selects_the_bodies_by_name_and_asks_for_a_solid(no_variants):
    """Bodies, not features: IBody2 has no Select4 and Select2 raises through
    pywin32, so each one is picked out by the name it carries."""
    session, doc = rebuilding_session()
    for feature, body in (("wing_loft_surface", "Surface-Loft1"),
                          ("root_cap", "Surface-Plane1"),
                          ("tip_cap", "Surface-Plane2")):
        doc.add(feature, "RefSurface", body=FakeBody(body, []))

    assert session.knit_to_solid(
        ["wing_loft_surface", "root_cap", "tip_cap"], "wing_loft") == "wing_loft"
    assert [c for c in doc.calls if c[0] == "SelectByID2"] == [
        ("SelectByID2", "Surface-Loft1", "SURFACEBODY", False, 1),
        ("SelectByID2", "Surface-Plane1", "SURFACEBODY", True, 1),
        ("SelectByID2", "Surface-Plane2", "SURFACEBODY", True, 1),
    ]
    assert [c for c in doc.calls if c[0] == "InsertSewRefSurface"] == [
        ("InsertSewRefSurface", True, True, False, swcom.KNIT_TOLERANCE, swcom.KNIT_GAP_RANGE)
    ]


def test_a_knit_can_be_asked_for_a_plain_surface_instead(no_variants):
    session, doc = rebuilding_session()
    for feature in ("a", "b"):
        doc.add(feature, "RefSurface", body=FakeBody("Body-" + feature, []))
    session.knit_to_solid(["a", "b"], "knitted", solid=False)
    assert [c[2] for c in doc.calls if c[0] == "InsertSewRefSurface"] == [False]


def test_a_knit_that_will_not_form_a_solid_says_what_it_was_given(no_variants):
    session, doc = rebuilding_session()
    for feature in ("a", "b"):
        doc.add(feature, "RefSurface", body=FakeBody("Body-" + feature, []))
    doc.manager.refuse_knit = True
    with pytest.raises(SolidWorksError, match="would not knit a and b into a solid"):
        session.knit_to_solid(["a", "b"], "knitted")


def test_a_knit_of_one_surface_is_refused_before_solidworks_sees_it():
    session, _ = rebuilding_session(features=["a"])
    with pytest.raises(SolidWorksError, match="at least two surfaces"):
        session.knit_to_solid(["a"], "knitted")
