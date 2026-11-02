"""Finding the session an act acts on: the launch row, the claim, liveness through turns,
the claim compared with the endpoint process it recorded (never the harness), a claim
waiting to move after an endpoint was replaced, the slot, and the caller of a self act."""

from __future__ import annotations

import os

import pytest
from moxi import proc

from moxi_launcher import store, target, targets

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
CLEARED = "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d"
#: A pid past any system's pid limit: never a running process.
DEAD = 4194999


def me() -> str:
    """This process, as a claim names an endpoint."""
    ident = proc.identity(os.getpid())
    return f"{ident.pid}@{ident.started:.6f}"


class FakeSession:
    def __init__(self, session_id=SID, endpoint_pid=None, pid=None):
        self.session_id = session_id
        self.endpoint_pid = os.getpid() if endpoint_pid is None else endpoint_pid
        # The harness: for Claude, the endpoint's parent, never the claimed process.
        self.pid = os.getppid() if pid is None else pid
        self.closed = 0

    def close(self):
        self.closed += 1


class FakeTurns:
    def __init__(self, session=None, error=None):
        self.session = session
        self.error = error
        self.asked = []

    def find(self, *, session_id=None, pid=None):
        self.asked.append({"session_id": session_id, "pid": pid})
        if self.error is not None:
            raise self.error
        return self.session


class FakeSlots:
    class SlotsError(Exception):
        pass

    class TryAgain(SlotsError):
        pass

    def __init__(self, shown=None, error=None):
        self.shown, self.error, self.asked = shown, error, []

    def show(self, slot):
        self.asked.append(slot)
        if self.error is not None:
            raise self.error
        return self.shown


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    yield connection
    connection.close()


def launched(conn, *, endpoint=None, target_name="slot", harness="claude",
             session_id=SID, request_id="r1"):
    store.insert(conn, request_id=request_id, actor="someone", harness=harness,
                 target=target_name, cwd="/work", values={"model": "opus"},
                 argv=["claude"], env={}, posture="operator", session_id=session_id,
                 slot="3" if target_name == "slot" else None)
    store.set_facts(conn, request_id, pane_id="%9")
    if endpoint is not None:
        conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = ?",
                     (endpoint, request_id))
    return store.get(conn, request_id)


# -- the launch and the claim -------------------------------------------------------------


def test_a_session_the_launcher_did_not_launch_is_refused(conn):
    with pytest.raises(target.NotLaunched) as caught:
        target.find("unknown", conn=conn, turns=FakeTurns())
    assert caught.value.code == "not-launched" and caught.value.exit_code == 3
    assert "was not launched by the launcher" in caught.value.problem


def test_an_unclaimed_launch_is_refused_before_turns_is_asked(conn):
    launched(conn)
    turns = FakeTurns(FakeSession())
    with pytest.raises(target.Unclaimed) as caught:
        target.find(SID, conn=conn, turns=turns)
    assert caught.value.code == "unclaimed" and caught.value.exit_code == 3
    assert turns.asked == []


def test_a_claimed_claude_session_is_found_by_its_endpoint_not_its_harness(conn):
    launched(conn, endpoint=me())
    session = FakeSession()
    assert session.pid != session.endpoint_pid
    found = target.find(SID, conn=conn, turns=FakeTurns(session), slots_api=FakeSlots())
    assert found.live and found.session is session
    assert found.row.request_id == "r1" and found.session_id == SID
    assert found.pane_id == "%9"
    found.close()
    assert session.closed == 1


def test_the_claim_is_compared_by_process_not_by_text(conn):
    ident = proc.identity(os.getpid())
    # Another spelling of the same start time, and one read a little apart, both match.
    for text in (f"{ident.pid}@{ident.started:.2f}", f"{ident.pid}@{ident.started + 0.4}"):
        conn.execute("DELETE FROM session_ids")
        conn.execute("DELETE FROM launches")
        launched(conn, endpoint=text)
        assert target.find(SID, conn=conn, turns=FakeTurns(FakeSession()),
                           slots_api=FakeSlots()).live


def test_a_claim_named_by_its_pid_alone_matches_that_pid(conn):
    launched(conn, endpoint=str(os.getpid()))
    assert target.find(SID, conn=conn, turns=FakeTurns(FakeSession()),
                       slots_api=FakeSlots()).live


def test_a_session_served_by_another_endpoint_is_changed(conn):
    launched(conn, endpoint=me())
    session = FakeSession(endpoint_pid=os.getppid())
    with pytest.raises(target.Changed) as caught:
        target.find(SID, conn=conn, turns=FakeTurns(session))
    assert caught.value.code == "changed" and caught.value.exit_code == 3
    assert not caught.value.reclaim_pending
    assert session.closed == 1


def test_a_claim_on_the_same_pid_with_another_start_is_changed(conn):
    ident = proc.identity(os.getpid())
    launched(conn, endpoint=f"{ident.pid}@{ident.started - 5:.6f}")
    with pytest.raises(target.Changed) as caught:
        target.find(SID, conn=conn, turns=FakeTurns(FakeSession()))
    # The claimed process is gone and a live one serves the session: a re-claim pending.
    assert caught.value.reclaim_pending


def test_a_replaced_endpoint_not_yet_reclaimed_is_changed_and_says_so(conn):
    launched(conn, endpoint=f"{DEAD}@1761728401.500000")
    turns = FakeTurns(FakeSession())
    with pytest.raises(target.Changed) as caught:
        target.find(SID, conn=conn, turns=turns)
    assert caught.value.reclaim_pending
    assert caught.value.serving == me()
    assert "has not claimed it yet" in caught.value.problem
    assert "next start, clear or compaction" in caught.value.hint
    assert len(turns.asked) == 1  # read once, never waited on
    # Nothing here moves the claim.
    assert store.get(conn, "r1").endpoint == f"{DEAD}@1761728401.500000"


def test_an_endpoint_that_names_no_process_is_changed(conn):
    launched(conn, endpoint=me())
    session = FakeSession()
    session.endpoint_pid = None
    with pytest.raises(target.Changed) as caught:
        target.find(SID, conn=conn, turns=FakeTurns(session))
    assert not caught.value.reclaim_pending


def test_a_session_turns_does_not_find_is_not_live(conn):
    launched(conn, endpoint=f"{DEAD}@1.0")
    found = target.find(SID, conn=conn, turns=FakeTurns(None), slots_api=FakeSlots("slot"))
    assert not found.live and found.session is None
    assert found.slot == "slot" and found.row.request_id == "r1"
    found.close()


def test_turns_is_asked_for_the_newest_id(conn):
    launched(conn, endpoint=me())
    store.learn_session(conn, "r1", CLEARED, "clear")
    turns = FakeTurns(FakeSession(session_id=CLEARED))
    found = target.find(SID, conn=conn, turns=turns, slots_api=FakeSlots())
    assert turns.asked == [{"session_id": CLEARED, "pid": None}]
    assert found.session_id == CLEARED


def test_a_codex_host_endpoint_is_its_harness(conn):
    launched(conn, endpoint=me(), harness="codex", session_id=None, target_name="terminal")
    store.learn_session(conn, "r1", "thread-1", "sender")
    session = FakeSession(session_id="thread-1", pid=os.getpid())
    found = target.find("thread-1", conn=conn, turns=FakeTurns(session))
    assert found.live and found.slot is None


def test_turns_that_fails_or_is_missing_is_refused(conn, monkeypatch):
    launched(conn, endpoint=me())
    with pytest.raises(target.TurnsFailed) as caught:
        target.find(SID, conn=conn, turns=FakeTurns(error=RuntimeError("socket broke")))
    assert caught.value.exit_code == 1 and "socket broke" in caught.value.problem
    monkeypatch.setattr(target.first, "turns_api", lambda quiet=False: None)
    with pytest.raises(target.NoTurns) as caught:
        target.find(SID, conn=conn)
    assert caught.value.code == "no-turns" and caught.value.exit_code == 3


# -- the slot -----------------------------------------------------------------------------


def test_a_slot_launch_carries_slots_view_of_its_slot(conn):
    launched(conn, endpoint=me())
    slots = FakeSlots(shown="slot 3")
    found = target.find(SID, conn=conn, turns=FakeTurns(FakeSession()), slots_api=slots)
    assert found.slot == "slot 3" and slots.asked == ["3"]


def test_another_target_has_no_slot(conn):
    launched(conn, endpoint=me(), target_name="terminal")
    slots = FakeSlots(shown="never")
    found = target.find(SID, conn=conn, turns=FakeTurns(FakeSession()), slots_api=slots)
    assert found.slot is None and slots.asked == []


def test_without_slots_a_slot_launch_has_no_slot(conn, monkeypatch):
    launched(conn, endpoint=me())
    monkeypatch.setattr(target.slots, "api", lambda: None)
    assert target.find(SID, conn=conn, turns=FakeTurns(FakeSession())).slot is None


@pytest.mark.parametrize("error, refusal, code", [
    (FakeSlots.TryAgain("lock held"), targets.SlotsBusy, 75),
    (FakeSlots.SlotsError("registry unreadable"), targets.SlotsFailed, 1),
])
def test_a_slot_that_cannot_be_read_is_refused_and_the_session_closed(conn, error, refusal,
                                                                      code):
    launched(conn, endpoint=me())
    session = FakeSession()
    with pytest.raises(refusal) as caught:
        target.find(SID, conn=conn, turns=FakeTurns(session), slots_api=FakeSlots(error=error))
    assert caught.value.exit_code == code
    assert session.closed == 1


# -- the store ----------------------------------------------------------------------------


def test_with_no_launch_log_yet_nothing_was_launched():
    with pytest.raises(target.NotLaunched):
        target.find(SID, turns=FakeTurns())
    assert not store.path().exists()


def test_the_launch_log_is_opened_when_none_is_given():
    conn = store.connect()
    try:
        launched(conn, endpoint=me())
    finally:
        conn.close()
    assert target.find(SID, turns=FakeTurns(FakeSession()), slots_api=FakeSlots()).live


# -- the caller ---------------------------------------------------------------------------


def test_the_caller_is_the_session_above_this_process(conn):
    launched(conn, endpoint=me())
    session = FakeSession()
    turns = FakeTurns(session)
    found = target.caller(conn=conn, turns=turns, slots_api=FakeSlots())
    assert found.live and found.session is session
    assert turns.asked == [{"session_id": None, "pid": os.getpid()}]


def test_a_nested_session_is_not_launched(conn):
    launched(conn, endpoint=me())
    nested = FakeSession(session_id="9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b")
    with pytest.raises(target.NotLaunched):
        target.caller(conn=conn, turns=FakeTurns(nested))
    assert nested.closed >= 1


def test_a_process_in_no_session_is_refused(conn):
    with pytest.raises(target.NotLaunched) as caught:
        target.caller(pid=1, conn=conn, turns=FakeTurns(None))
    assert "no session" in caught.value.problem
    session = FakeSession(session_id=None)
    with pytest.raises(target.NotLaunched):
        target.caller(conn=conn, turns=FakeTurns(session))
    assert session.closed == 1


def test_the_caller_check_refuses_another_endpoint(conn):
    launched(conn, endpoint=me())
    session = FakeSession(endpoint_pid=os.getppid())
    with pytest.raises(target.Changed):
        target.caller(conn=conn, turns=FakeTurns(session))
    assert session.closed >= 1
