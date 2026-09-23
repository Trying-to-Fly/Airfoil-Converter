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


# -- reaching a member ------------------------------------------------------


def test_a_member_that_comes_back_as_a_method_is_called():
    """GetEdges and ReleaseSelectionAccess come back uncalled through late
    binding; a value never does, so a method object is the one to call."""

    class Body:
        @property
        def Name(self):
            return "Surface-Loft1"

        def GetEdges(self):
            return ["e1", "e2"]

        def Select4(self, append, data):
            return (append, data)

    assert swcom.call(Body(), "Name") == "Surface-Loft1"
    assert swcom.call(Body(), "GetEdges") == ["e1", "e2"]
    assert swcom.call(Body(), "Select4", True, "data") == (True, "data")


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
    def __init__(self, body, area=0.0):
        self.body = body
        self.area = area          # square millimetres, as the tests think

    @property
    def GetBody(self):
        return self.body

    @property
    def GetArea(self):
        return self.area / 1e6    # square metres, as SolidWorks answers


class FakeDefinition:
    def __init__(self, points):
        # Metres, flat, as a curve feature hands them back.
        self.PointArray = [c / 1000.0 for p in points for c in p]


class FakeFeature:
    """A feature in the tree. Its name is a property both ways, as COM's is."""

    def __init__(self, name, type_name="RefCurve", body=None, points=None, area=0.0):
        self._name = name
        self._type = type_name
        self.body = body
        self.points = points
        self.area = area
        self.suppressed = False
        self.doc = None
        self.next = None

    @property
    def GetFaces(self):
        return [FakeFace(self.body, self.area)] if self.body else None

    @property
    def IsSuppressed(self):
        return self.suppressed

    def SetSuppression2(self, action, configuration, names):
        self.doc.calls.append(("SetSuppression2", self._name, action))
        if self._name in self.doc.wont_unsuppress and action == swcom.UNSUPPRESS:
            return False
        self.suppressed = action == swcom.SUPPRESS
        # What a body feature carries with it, and does not bring back.
        if action == swcom.SUPPRESS:
            for child in self.doc.children.get(self._name, ()):
                child.suppressed = True
        return True

    def Select2(self, append, mark):
        self.doc.calls.append(("Select2", self._name))
        self.doc.selected = self
        return True

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


class FakeLastFeature:
    """The last feature in the tree, which is the one worth asking whether any
    of the tree is still rolled back."""

    def __init__(self, doc):
        self.doc = doc

    @property
    def IsRolledBack(self):
        self.doc.calls.append(("IsRolledBack",))
        return self.doc.rolled_back


class FakeCompositeData:
    """A composite curve's definition: reading it rolls the model back, and
    the release is a Sub — no arguments, no result — that rolls it forward
    again, unless ``release_restores`` says this SolidWorks does not."""

    def __init__(self, doc, sources, release_restores=True):
        self.doc = doc
        self.sources = sources
        self.release_restores = release_restores

    def AccessSelections(self, top_doc, component):
        self.doc.calls.append(("AccessSelections",))
        self.doc.rolled_back = True
        return True

    def GetEntitiesToJoin(self, kinds):
        self.doc.calls.append(("GetEntitiesToJoin",))
        return [FakeEntity(name) for name in self.sources]

    def ReleaseSelectionAccess(self):
        self.doc.calls.append(("ReleaseSelectionAccess",))
        if self.release_restores:
            self.doc.rolled_back = False


class FakeEntity:
    def __init__(self, name):
        self.Name = name


class FakeCompositeFeature:
    """A composite in the tree walk ``_curve_feature`` does, carrying the
    definition that takes and releases the selection access."""

    def __init__(self, doc, name, sources, release_restores=True):
        self._name = name
        self.next = None
        self.doc = doc
        self.suppressed = False
        self.GetDefinition = FakeCompositeData(doc, sources, release_restores)

    @property
    def IsSuppressed(self):
        return self.suppressed

    @property
    def Name(self):
        return self._name

    @property
    def GetTypeName2(self):
        return swcom.COMPOSITE_TYPE_NAME

    @property
    def GetNextFeature(self):
        return self.next


class FakeManager:
    """The feature manager: the rollback bar, the tree listing, and the call
    that knits a capped loft into a solid.

    ``lies`` is SolidWorks 2026 as observed: ``EditRollback`` answering True
    for a position it did not move the bar to.
    """

    def __init__(self, doc, refuse=(), lies=()):
        self.doc = doc
        self.calls = doc.calls
        self.refuse = set(refuse)
        self.lies = set(lies)
        self.refuse_knit = False

    def EditRollback(self, position, name):
        self.doc.calls.append(("EditRollback", position, name))
        if position in self.refuse:
            return False
        if position not in self.lies:
            self.doc.rolled_back = position != swcom.ROLLBACK_TO_END
        return True

    def GetFeatures(self, top_only):
        return list(self.doc.features)

    def InsertSewRefSurface(self, gap_filters, form_solid, merge, tolerance, gap_range):
        self.calls.append(
            ("InsertSewRefSurface", gap_filters, form_solid, merge, tolerance, gap_range)
        )
        if self.refuse_knit:
            return None
        if form_solid and self.doc.knit_makes_solid:
            self.doc.solids += 1
        return self.doc.add("Surface-Knit1", "SewRefSurface",
                            body=FakeBody("Surface-Knit1", []))


class FakeExtension:
    def __init__(self, doc):
        self.doc = doc
        self.calls = doc.calls
        self.refuse = set()

    def SelectByID2(self, name, kind, x, y, z, append, mark, callout, options):
        self.calls.append(("SelectByID2", name, kind, append, mark))
        return name not in self.refuse

    def DeleteSelection2(self, options):
        gone = self.doc.selected
        self.calls.append(("DeleteSelection2", gone._name if gone else None))
        if gone is None:
            return False
        self.doc.features = [f for f in self.doc.features if f is not gone]
        for a, b in zip(self.doc.features, self.doc.features[1:] + [None]):
            a.next = b
        self.doc.selected = None
        return True


class FakeDoc:
    """A document that records what was asked of it, as late binding would.

    A member taking no arguments is reached as an attribute, so ``EditRebuild3``
    and ``FeatureManager`` have to be properties here and ``ForceRebuild3`` a
    method — which is the distinction :func:`swcom.call` exists to make.
    """

    def __init__(self, refuse=(), lies=(), features=()):
        self.calls = []
        self.features = []
        self.rolled_back = False
        self.refuse_cap = False
        self.cap_area = 4046.8       # square millimetres the next cap comes out at
        self.solids = 16             # what a real part already holds
        self.knit_makes_solid = True
        self.selected = None
        self.children = {}           # what a feature's suppression carries with it
        self.wont_unsuppress = set()
        self.manager = FakeManager(self, refuse, lies)
        self.extension = FakeExtension(self)
        self.GetTitle = "Wing.SLDPRT"
        for name in features:
            self.add(name)

    def add(self, name, type_name="RefCurve", body=None, points=None, area=0.0):
        return self._append(FakeFeature(name, type_name, body, points, area))

    def GetBodies2(self, kind, visible_only):
        self.calls.append(("GetBodies2", kind, visible_only))
        return [object()] * (self.solids if kind == swcom.SOLID_BODY else 0)

    def add_composite(self, name, sources, release_restores=True):
        """A composite curve in the tree, whose definition is the thing that
        takes the selection access and gives it back."""
        return self._append(FakeCompositeFeature(self, name, sources, release_restores))

    def _append(self, made):
        made.doc = self
        if self.features:
            self.features[-1].next = made
        self.features.append(made)
        return made

    @property
    def FirstFeature(self):
        return self.features[0] if self.features else None

    def FeatureByPositionReverse(self, number):
        self.calls.append(("FeatureByPositionReverse", number))
        return FakeLastFeature(self)

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
        self.add("Surface-Plane1", "RefSurface",
                 body=FakeBody("Surface-Plane1", []), area=self.cap_area)
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


def rebuilding_session(refuse=(), lies=(), features=()):
    doc = FakeDoc(refuse, lies, features)
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


def test_rolling_forward_goes_to_the_end_and_checks_it_got_there():
    """Never "previous position": on SolidWorks 2026 it answers True and moves
    nothing, and the return of the call that does move it is not trusted
    either — the last feature is asked."""
    session, doc = rebuilding_session()
    session.roll_back_to("wing_root_upper")
    assert doc.rolled_back
    session.roll_forward()
    assert not doc.rolled_back
    assert doc.calls[1:] == [
        ("EditRollback", swcom.ROLLBACK_TO_END, ""),
        ("FeatureByPositionReverse", 0),
        ("IsRolledBack",),
    ]
    assert not any(c[0] == "EditRollback" and c[1] == swcom.ROLLBACK_TO_PREVIOUS
                   for c in doc.calls)


@pytest.fixture
def win32(monkeypatch):
    """pywin32's typed nulls and by-ref variants, which the read needs to
    build even though nothing here marshals them: this suite runs on Linux."""
    import types

    stub = types.SimpleNamespace(VT_DISPATCH=9, VT_BYREF=0x4000, VT_VARIANT=12, VT_I4=3, VT_BOOL=11)
    monkeypatch.setattr(swcom, "pythoncom", stub, raising=False)
    monkeypatch.setattr(swcom, "VARIANT", lambda kind, value: (kind, value), raising=False)


def test_reading_what_a_composite_joins_releases_the_access_it_took(win32):
    """AccessSelections rolls the model back to just before the feature, and
    ReleaseSelectionAccess is the Sub that puts it forward. Reached as an
    attribute it never ran, and every push left the part rolled back."""
    session, doc = rebuilding_session()
    doc.add_composite("rib_joined", ["rib_airfoil", "rib_airfoil_te"])
    assert session.composite_sources("rib_joined") == ["rib_airfoil", "rib_airfoil_te"]
    assert ("ReleaseSelectionAccess",) in doc.calls
    assert not doc.rolled_back
    assert not any(c[0] == "EditRollback" for c in doc.calls)   # the release was enough


def test_a_release_that_leaves_the_tree_rolled_back_is_followed_by_a_roll_forward(win32):
    session, doc = rebuilding_session()
    doc.add_composite("rib_joined", ["a", "b"], release_restores=False)
    assert session.composite_sources("rib_joined") == ["a", "b"]
    assert not doc.rolled_back
    assert ("EditRollback", swcom.ROLLBACK_TO_END, "") in doc.calls


def test_a_tree_the_user_had_rolled_back_is_left_rolled_back_by_a_read(win32):
    session, doc = rebuilding_session()
    doc.add_composite("rib_joined", ["a", "b"], release_restores=False)
    doc.rolled_back = True
    session.composite_sources("rib_joined")
    assert doc.rolled_back
    assert not any(c[0] == "EditRollback" for c in doc.calls)


def test_a_tree_that_answers_yes_and_stays_rolled_back_is_an_error():
    """The one state worse than a slow export: half a part, handed back."""
    session, doc = rebuilding_session(lies=[swcom.ROLLBACK_TO_END])
    session.roll_back_to("wing_root_upper")
    with pytest.raises(SolidWorksError, match="could not be rolled forward"):
        session.roll_forward()
    assert doc.rolled_back


# -- capping a surface loft into a solid ------------------------------------
#
# The construction these pin was run on SolidWorks 2026 on 2026-09-22; what is
# pinned here is what the app sends it.


def wing_body(tip_edges=3):
    """A surface loft's body: a loop at each end and the seams between them.

    Metres. The root loop stands in the plane x = 0 and the tip loop in
    x = -0.998, which is where a wing a metre long puts them. A fourth edge at
    the tip is the sliver face the real loft grew.
    """
    root = [FakeEdge([], f"root{k}", (0.0, 0.0, -0.05 * k), (0.0, 0.0, -0.05 * (k + 1)))
            for k in range(3)]
    tip = [FakeEdge([], f"tip{k}", (-0.998, 0.0, -0.05 * k), (-0.998, 0.0, -0.05 * (k + 1)))
           for k in range(tip_edges)]
    seams = [FakeEdge([], f"seam{k}", (0.0, 0.0, -0.05 * k), (-0.998, 0.0, -0.05 * k))
             for k in range(3)]
    return root, tip, seams


# A wing's end profile is a composite of three curves, and the loop they make
# encloses 1,500 mm²: a triangle 20 mm deep over a 150 mm chord.
def profile_pieces(x):
    return (
        ("upper", [(x, 0.0, 0.0), (x, 20.0, -30.0)]),
        ("lower", [(x, 20.0, -30.0), (x, 0.0, -150.0)]),
        ("te", [(x, 0.0, -150.0), (x, 0.0, 0.0)]),
    )


PROFILE_AREA = 1500.0


def capping_session(refuse_cap=False, tip_edges=3, cap_area=4046.8):
    root, tip, seams = wing_body(tip_edges)
    session, doc = rebuilding_session()
    for edge in root + tip + seams:
        edge.calls = doc.calls
    doc.add("wing_loft_surface", "BlendRefSurface",
            body=FakeBody("Surface-Loft1", root + tip + seams))
    for end, x in (("root", 0.0), ("tip", -998.0)):
        for tag, points in profile_pieces(x):
            doc.add(f"{end}_{tag}", "CurveInFile", points=points)
        doc.add_composite(f"{end}_joined", [f"{end}_{tag}" for tag, _ in profile_pieces(x)])
    doc.refuse_cap = refuse_cap
    doc.cap_area = cap_area
    return session, doc


def test_a_cap_takes_the_end_edges_that_lie_in_the_profile_s_plane(win32):
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


def test_a_cap_at_the_other_end_takes_the_other_loop(win32):
    session, doc = capping_session()
    session.cap_end("wing_loft_surface", "tip_joined", "tip_cap")
    assert [c[1] for c in doc.calls if c[0] == "Select4"] == ["tip0", "tip1", "tip2"]


def test_a_composite_profile_is_read_through_the_curves_it_joins(win32):
    """A wing's end profile is a composite of three curves, and a composite has
    no points of its own to read."""
    session, doc = capping_session()
    assert len(session.profile_pieces("root_joined")) == 3
    assert ("GetEntitiesToJoin",) in doc.calls
    assert ("ReleaseSelectionAccess",) in doc.calls
    assert session.profile_points("root_joined")[0] == (0.0, 0.0, 0.0)


def test_an_end_with_no_edges_in_that_plane_says_so(win32):
    session, doc = capping_session()
    doc.add("nowhere_only", "CurveInFile",
            points=[(500.0, 0.0, 0.0), (500.0, 20.0, -30.0), (500.0, 0.0, -150.0)])
    doc.add_composite("nowhere", ["nowhere_only"])
    with pytest.raises(SolidWorksError, match="No edge of wing_loft_surface lies in the plane"):
        session.cap_end("wing_loft_surface", "nowhere", "root_cap")


def test_an_end_with_more_edges_than_the_profile_has_pieces_is_the_sliver(win32):
    """What a fourth edge at the tip means: the loft has grown a sliver face
    there, and the surface SolidWorks puts across its little loop is not the
    end of the wing. Refused before anything is made."""
    session, doc = capping_session(tip_edges=4)
    with pytest.raises(SolidWorksError, match="has 4 edges where the profile has 3 pieces"):
        session.cap_end("wing_loft_surface", "tip_joined", "tip_cap")
    assert ("InsertPlanarRefSurface",) not in doc.calls


def test_a_cap_that_does_not_span_the_end_is_taken_out_again(win32):
    """0.078 mm² against 4,046.8 on the real part: a face across a sliver's own
    loop, which SolidWorks made and answered True to."""
    session, doc = capping_session(cap_area=0.078)
    with pytest.raises(SolidWorksError, match=r"0\.078 mm² against the section's 1500\.0 mm²"):
        session.cap_end("wing_loft_surface", "root_joined", "root_cap")
    assert ("DeleteSelection2", "Surface-Plane1") in doc.calls
    assert "Surface-Plane1" not in [f.Name for f in doc.features]


def test_a_cap_just_under_the_section_is_taken_for_the_section(win32):
    """The face is bounded by splines and the area compared with it is the
    polygon through the profile's points, so they are not equal; the line is
    half, which nothing real comes near."""
    session, _ = capping_session(cap_area=PROFILE_AREA * 0.99)
    assert session.cap_end("wing_loft_surface", "root_joined", "root_cap") == "root_cap"


def test_a_cap_solidworks_refuses_says_how_many_edges_it_was_given(win32):
    """What a sliver face at the tip used to look like from here: the loop is
    there, and nothing will put a surface across it."""
    session, _ = capping_session(refuse_cap=True)
    with pytest.raises(SolidWorksError, match="would not put a planar surface across the 3 edges"):
        session.cap_end("wing_loft_surface", "root_joined", "root_cap")


def knitting_session(makes_solid=True):
    session, doc = rebuilding_session()
    for feature, body in (("wing_loft_surface", "Surface-Loft1"),
                          ("root_cap", "Surface-Plane1"),
                          ("tip_cap", "Surface-Plane2")):
        doc.add(feature, "RefSurface", body=FakeBody(body, []))
    doc.knit_makes_solid = makes_solid
    return session, doc


def test_a_knit_selects_the_bodies_by_name_and_asks_for_a_solid(no_variants):
    """Bodies, not features: IBody2 has no Select4 and Select2 raises through
    pywin32, so each one is picked out by the name it carries."""
    session, doc = knitting_session()

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
    # Counted either side, because the call answers the same way whichever it did.
    assert [c for c in doc.calls if c[0] == "GetBodies2"] == [
        ("GetBodies2", swcom.SOLID_BODY, False), ("GetBodies2", swcom.SOLID_BODY, False),
    ]


def test_a_knit_that_sews_a_sheet_is_not_a_solid_and_says_so(no_variants):
    """The 1.30 set at every guide: three sheets went in, one sheet came out,
    the feature was there and the call answered True."""
    session, doc = knitting_session(makes_solid=False)
    before = doc.solids

    with pytest.raises(swcom.NotASolid, match="sewed them into a sheet rather than a solid"):
        session.knit_to_solid(["wing_loft_surface", "root_cap", "tip_cap"], "wing_loft")
    assert doc.solids == before
    assert ("DeleteSelection2", "Surface-Knit1") in doc.calls
    assert "Surface-Knit1" not in [f.Name for f in doc.features]


def test_a_sewn_sheet_is_still_a_solidworks_error_to_anything_that_cares():
    """So that every handler that already catches one keeps working."""
    assert issubclass(swcom.NotASolid, SolidWorksError)


def test_a_knit_can_be_asked_for_a_plain_surface_instead(no_variants):
    session, doc = knitting_session(makes_solid=False)
    session.knit_to_solid(["wing_loft_surface", "root_cap"], "knitted", solid=False)
    assert [c[2] for c in doc.calls if c[0] == "InsertSewRefSurface"] == [False]
    # Nothing to count: a surface knit was never going to make a body.
    assert not [c for c in doc.calls if c[0] == "GetBodies2"]


def test_a_knit_that_will_not_form_a_solid_says_what_it_was_given(no_variants):
    session, doc = knitting_session()
    doc.manager.refuse_knit = True
    with pytest.raises(SolidWorksError, match="would not knit wing_loft_surface and root_cap"):
        session.knit_to_solid(["wing_loft_surface", "root_cap"], "knitted")


def test_a_knit_of_one_surface_is_refused_before_solidworks_sees_it():
    session, _ = rebuilding_session(features=["a"])
    with pytest.raises(SolidWorksError, match="at least two surfaces"):
        session.knit_to_solid(["a"], "knitted")


# -- putting a suppression cascade back -------------------------------------


def suppression_session():
    """A body feature with what was built on it, in tree order."""
    session, doc = rebuilding_session()
    for name in ("wing_loft", "Split1", "Sketch9<3>", "Insert1", "Plane7", "other_loft"):
        doc.add(name)
    doc.children["wing_loft"] = [f for f in doc.features if f.Name != "wing_loft"][:4]
    return session, doc


def test_the_state_of_every_feature_is_read_in_tree_order():
    session, doc = suppression_session()
    state = session.suppression_state()
    assert [item.name for item in state] == [
        "wing_loft", "Split1", "Sketch9<3>", "Insert1", "Plane7", "other_loft"]
    assert not any(item.suppressed for item in state)


def test_suppressing_a_body_feature_carries_what_was_built_on_it():
    """Which is the whole difficulty: unsuppressing the parent brings none of
    them back, and one of them is named ``Sketch9<3>``, which cannot be looked
    up again."""
    session, doc = suppression_session()
    state = session.suppression_state()
    session.set_suppressed("wing_loft", True)
    assert [f.Name for f in doc.features if f.suppressed] == [
        "wing_loft", "Split1", "Sketch9<3>", "Insert1", "Plane7"]

    session.set_suppressed("wing_loft", False)
    assert [f.Name for f in doc.features if f.suppressed] == [
        "Split1", "Sketch9<3>", "Insert1", "Plane7"]

    assert session.restore_suppression(state) == []
    assert not [f.Name for f in doc.features if f.suppressed]


def test_what_was_already_suppressed_is_left_where_it_was():
    session, doc = suppression_session()
    doc.features[-1].suppressed = True          # the user had this one off
    state = session.suppression_state()
    session.set_suppressed("wing_loft", True)
    assert session.restore_suppression(state) == []
    assert [f.Name for f in doc.features if f.suppressed] == ["other_loft"]
    # Nothing was said to the feature that had not moved.
    assert not [c for c in doc.calls if c[0] == "SetSuppression2" and c[1] == "other_loft"]


def test_the_restore_works_down_the_tree_from_the_parent():
    session, doc = suppression_session()
    state = session.suppression_state()
    session.set_suppressed("wing_loft", True)
    doc.calls.clear()
    session.restore_suppression(state)
    assert [c[1] for c in doc.calls if c[0] == "SetSuppression2"] == [
        "wing_loft", "Split1", "Sketch9<3>", "Insert1", "Plane7"]


def test_a_feature_that_will_not_come_back_is_named():
    """A part handed back with features suppressed is worth saying out loud."""
    session, doc = suppression_session()
    state = session.suppression_state()
    session.set_suppressed("wing_loft", True)
    doc.wont_unsuppress = {"Sketch9<3>"}
    assert session.restore_suppression(state) == ["Sketch9<3>"]
    assert [f.Name for f in doc.features if f.suppressed] == ["Sketch9<3>"]
