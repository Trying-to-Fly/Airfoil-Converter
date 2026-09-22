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


class FakeManager:
    """The feature manager, which is where the rollback bar lives.

    ``lies`` is SolidWorks 2026 as observed: ``EditRollback`` answering True
    for a position it did not move the bar to.
    """

    def __init__(self, doc, refuse=(), lies=()):
        self.doc = doc
        self.refuse = set(refuse)
        self.lies = set(lies)

    def EditRollback(self, position, name):
        self.doc.calls.append(("EditRollback", position, name))
        if position in self.refuse:
            return False
        if position not in self.lies:
            self.doc.rolled_back = position != swcom.ROLLBACK_TO_END
        return True


class FakeFeature:
    def __init__(self, doc):
        self.doc = doc

    @property
    def IsRolledBack(self):
        self.doc.calls.append(("IsRolledBack",))
        return self.doc.rolled_back


class FakeDoc:
    """A document that records what was asked of it, as late binding would.

    A member taking no arguments is reached as an attribute, so ``EditRebuild3``
    and ``FeatureManager`` have to be properties here and ``ForceRebuild3`` a
    method — which is the distinction :func:`swcom.call` exists to make.
    """

    def __init__(self, refuse=(), lies=()):
        self.calls = []
        self.rolled_back = False
        self.manager = FakeManager(self, refuse, lies)

    @property
    def FeatureManager(self):
        return self.manager

    def FeatureByPositionReverse(self, number):
        self.calls.append(("FeatureByPositionReverse", number))
        return FakeFeature(self)

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


def rebuilding_session(refuse=(), lies=()):
    doc = FakeDoc(refuse, lies)
    return swcom.Session(FakeApp(doc), (34, 0, 0), 1000), doc


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


def test_a_tree_that_answers_yes_and_stays_rolled_back_is_an_error():
    """The one state worse than a slow export: half a part, handed back."""
    session, doc = rebuilding_session(lies=[swcom.ROLLBACK_TO_END])
    session.roll_back_to("wing_root_upper")
    with pytest.raises(SolidWorksError, match="could not be rolled forward"):
        session.roll_forward()
    assert doc.rolled_back
