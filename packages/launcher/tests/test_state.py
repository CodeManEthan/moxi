"""A launched session's state, from the launch log: the red rule over every outcome, the
note not taken, the run that made a launch, stale open runs, and what the relaunch,
recycle and keep-warm parts show."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from moxi import exit as codes
from moxi import proc

from moxi_launcher import state, store, target

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
NEW = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
OLD = "2020-01-01T00:00:00.000000Z"


def me() -> str:
    ident = proc.identity(os.getpid())
    return f"proc:{ident.pid}@{ident.started:.6f}"


class FakeSlots:
    class SlotsError(Exception):
        pass

    class TryAgain(SlotsError):
        pass

    def show(self, slot):
        return SimpleNamespace(state="occupied", hold=None, created=1.0)

    def job(self, name):
        return None


class FakeCache:
    def __init__(self, ttl=3600.0):
        self.ttl, self.reads = ttl, []

    def read(self, harness, session_id, **options):
        self.reads.append((harness, session_id))
        return SimpleNamespace(kind="warm", age_seconds=10.0, ttl_seconds=self.ttl)


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    launched(connection)
    yield connection
    connection.close()


def launched(conn, *, request_id="r1", values=None, session_id=SID, relaunch_of=None):
    store.insert(conn, request_id=request_id, actor="ann", harness="claude", target="slot",
                 cwd="/work", values={"model": "opus", **(values or {})}, argv=["claude"],
                 env={}, posture="operator", session_id=session_id, slot="3",
                 relaunch_of=relaunch_of)


def ran(conn, outcome, *, kind="keep-warm", mode="ask", word=None, detail=None,
        request_id="r1", at=None, **values):
    run = store.insert_run(conn, kind=kind, mode=mode, request_id=request_id,
                           session_id=SID, actor="launcher", state="acting",
                           job=me(), at=at)
    store.end_run(conn, run.run_id, outcome, word=word, detail=detail, **values)
    return store.get_run(conn, run.run_id)


def read(conn, which=SID, **kw):
    kw.setdefault("cache", None)
    return state.state(which, conn=conn, slots_api=FakeSlots(), **kw)


# -- the red rule ------------------------------------------------------------------------


def test_a_session_no_launch_holds_is_refused(conn):
    with pytest.raises(target.NotLaunched):
        read(conn, NEW)


def test_a_launch_with_no_runs_is_not_red_and_shows_its_options(conn):
    got = read(conn)
    assert (got.red, got.word, got.since, got.why) == (False, None, None, None)
    assert got.session_id == SID and got.request_id == "r1"
    assert got.relaunch.state is None
    assert (got.recycle.on_expiry, got.recycle.at_limit) == ("off", "off")
    assert not got.keep_warm.on and got.keep_warm.said == "off"


def test_a_blocked_run_is_red_blocked_since_the_block_began(conn):
    run = ran(conn, "blocked", word="capped", detail="3 nudges")
    got = read(conn)
    assert (got.red, got.word, got.why) == (True, "blocked", "capped: 3 nudges")
    assert got.since == run.created_at and got.run_id == run.run_id


def test_a_repeated_block_keeps_its_first_start_as_since(conn):
    first = ran(conn, "blocked", kind="clear", mode="expiry", word="draft", at=OLD)
    run = store.insert_run(conn, kind="clear", mode="expiry", request_id="r1",
                           session_id=SID, actor="launcher", state="acting", job=me())
    assert store.end_blocked(conn, run.run_id, "draft") == first.run_id
    got = read(conn)
    assert got.red and got.since == OLD


@pytest.mark.parametrize("outcome", state.FAILED)
def test_every_outcome_that_needs_a_look_is_red_failed(conn, outcome):
    run = ran(conn, outcome, kind="relaunch", mode="director", word="w", detail="d")
    got = read(conn)
    assert (got.red, got.word, got.why) == (True, "failed", "w: d")
    assert got.since == run.updated_at


def test_an_unconfirmed_run_is_red_since_its_outcome_is_unknown(conn):
    ran(conn, "unconfirmed", word="timeout")
    assert read(conn).word == "failed"


@pytest.mark.parametrize("outcome", ["relaunched", "cleared", "compacted", "nudged",
                                     "exited", "held", "expired", "not-held", "stopped",
                                     "not-started"])
def test_an_outcome_that_needs_no_look_is_not_red(conn, outcome):
    ran(conn, outcome, kind="relaunch", mode="self")
    got = read(conn)
    assert not got.red and got.word is None


def test_a_later_run_that_ended_well_clears_red(conn):
    ran(conn, "failed", word="gone", at=OLD)
    ran(conn, "nudged")
    assert not read(conn).red


def test_a_later_run_that_ended_held_takes_the_place_of_red(conn):
    ran(conn, "blocked", word="draft", at=OLD)
    ran(conn, "held", kind="exit", mode="verb", word="turn")
    assert not read(conn).red


def test_an_open_run_after_a_failed_one_leaves_it_red(conn):
    ran(conn, "relaunch-failed", kind="relaunch", mode="self", at=OLD)
    store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1", session_id=SID,
                     actor="launcher", state="acting", job=me())
    got = read(conn)
    assert got.red and got.why == "relaunch-failed"


def test_a_note_the_session_did_not_take_is_red_though_the_run_keeps_its_outcome(conn):
    run = ran(conn, "expired", kind="relaunch", mode="self", word="person")
    store.add_run_event(conn, run.run_id, "note", {"verdict": "refused", "why": "draft",
                                                   "text": "note"})
    got = read(conn)
    assert (got.red, got.word, got.why) == (True, "failed", "note-not-taken: draft")
    assert got.relaunch.note_not_taken and got.relaunch.state == "expired (person)"
    assert store.get_run(conn, run.run_id).outcome == "expired"


def test_a_note_the_session_took_is_not_red(conn):
    run = ran(conn, "held", kind="relaunch", mode="self", word="turn")
    store.add_run_event(conn, run.run_id, "note", {"verdict": "taken", "why": ""})
    got = read(conn)
    assert not got.red and not got.relaunch.note_not_taken


def test_the_run_that_made_a_launch_counts_as_its_first(conn):
    launched(conn, request_id="r2", session_id=NEW, relaunch_of="r1")
    store.learn_session(conn, "r2", NEW, "minted")
    made = ran(conn, "first-turn-not-sent", kind="relaunch", mode="self",
               new_request_id="r2")
    got = read(conn, NEW)
    assert got.request_id == "r2" and got.red and got.run_id == made.run_id
    assert got.relaunch.made_by == made.run_id
    assert got.relaunch.state == "first-turn-not-sent"


def test_a_run_of_the_new_launch_that_ended_well_clears_the_making_run(conn):
    launched(conn, request_id="r2", session_id=NEW, relaunch_of="r1")
    store.learn_session(conn, "r2", NEW, "minted")
    ran(conn, "first-turn-not-sent", kind="relaunch", mode="self", new_request_id="r2",
        at=OLD)
    ran(conn, "compacted", kind="compact", mode="expiry", request_id="r2")
    assert not read(conn, NEW).red


# -- the relaunch part -------------------------------------------------------------------


def test_an_open_relaunch_shows_armed_then_its_latest_wait(conn):
    run = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                           session_id=SID, actor="ann", job="slots:relaunch-x")
    assert read(conn).relaunch.state == "armed"
    store.advance_run(conn, run.run_id, "waiting")
    store.add_run_event(conn, run.run_id, "wait", {"word": "turn", "detail": "open"})
    store.add_run_event(conn, run.run_id, "wait", {"word": "person", "detail": "typing"})
    assert read(conn).relaunch.state == "waiting: person"


def test_an_open_run_whose_job_is_gone_shows_stale(conn):
    store.insert_run(conn, kind="relaunch", mode="self", request_id="r1", session_id=SID,
                     actor="ann", job="proc:999999999@1.0", at=OLD)
    got = read(conn)
    assert got.relaunch.state == "stale" and got.relaunch.run.state == "stale"


def test_a_refused_notice_is_named_by_its_recipients(conn):
    run = ran(conn, "relaunch-failed", kind="relaunch", mode="director")
    store.add_run_event(conn, run.run_id, "notice-refused",
                        {"verdict": "refused", "to": ["boss"], "text": "t"})
    got = read(conn)
    assert got.relaunch.notices_refused == ("boss",)
    assert "notice refused by boss" in got.lines()[1]


# -- the recycle part --------------------------------------------------------------------


def test_recycle_shows_its_options_last_run_pending_compact_and_the_last_telling(conn):
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 ('{"recycle_on_expiry": "clear", "recycle_at_limit": "compact", '
                  '"limit_at": "1.5x"}',))
    ran(conn, "cleared", kind="clear", mode="expiry", at=OLD)
    ran(conn, "nudged")
    store.pend_compact(conn, "r1", f"{SID}|1.5x|1", at="2026-01-02T00:00:00.000000Z")
    store.add_event(conn, "recycle-told", {"crossing_key": "k"}, request_id="r1",
                    session_id=SID, at="2026-01-03T00:00:00.000000Z")
    got = read(conn).recycle
    assert (got.on_expiry, got.at_limit, got.limit_at) == ("clear", "compact", "1.5x")
    assert (got.last.kind, got.last.outcome) == ("clear", "cleared")
    assert got.pending_compact == "2026-01-02T00:00:00.000000Z"
    assert got.told_at == "2026-01-03T00:00:00.000000Z"
    line = read(conn).lines()[2]
    assert "on expiry clear, at limit compact (1.5x)" in line and "clear cleared" in line


# -- the keep-warm part ------------------------------------------------------------------


def test_keep_warm_shows_the_chain_s_holds_the_run_of_nudges_and_the_last_verdict(conn):
    conn.execute("UPDATE launches SET values_json = '{\"keep_warm\": \"on\"}' "
                 "WHERE request_id = 'r1'")
    store.open_hold(conn, "r1", "port-cache")
    ran(conn, "nudged", turn_number=5, at=OLD)
    ran(conn, "nudged", turn_number=6)
    got = read(conn).keep_warm
    assert got.on and got.holds == ("port-cache",) and got.nudges == 2
    assert got.last.outcome == "nudged" and got.window is None
    assert read(conn).lines()[3].startswith("keep warm: on; holds: port-cache; "
                                            "nudges in a row: 2")


def test_keep_warm_says_a_short_lifetime_leaves_no_window(conn):
    conn.execute("UPDATE launches SET values_json = '{\"keep_warm\": \"on\"}' "
                 "WHERE request_id = 'r1'")
    cache = FakeCache(ttl=300.0)
    got = read(conn, cache=cache).keep_warm
    assert got.window == "no window: lifetime 300 s"
    assert got.said == "keep warm: on (no window: lifetime 300 s)"[len("keep warm: "):]
    assert cache.reads == [("claude", SID)]
    assert read(conn, cache=FakeCache()).keep_warm.window is None


def test_keep_warm_off_reads_no_cache(conn):
    cache = FakeCache(ttl=300.0)
    assert read(conn, cache=cache).keep_warm.window is None and cache.reads == []


# -- the verb ----------------------------------------------------------------------------


def test_show_prints_the_state_and_its_json_carries_the_outcome(conn):
    ran(conn, "blocked", word="draft", detail="a draft")
    got = state.show(SID, conn=conn, slots_api=FakeSlots(), cache=None)
    assert got.exit_code == codes.OK
    assert got.lines[0].startswith(f"session {SID} (launch r1): red, blocked since ")
    assert got.data["outcome"] == "blocked" and got.data["red"] is True
    assert got.data["keep_warm"]["on"] is False


def test_show_with_no_session_reads_the_caller_s_own(conn):
    ident = proc.identity(os.getpid())
    conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = 'r1'",
                 (f"{ident.pid}@{ident.started:.6f}",))
    session = SimpleNamespace(session_id=SID, endpoint_pid=os.getpid(), close=lambda: None)
    turns = SimpleNamespace(find=lambda **kw: session)
    got = state.show(conn=conn, turns=turns, slots_api=FakeSlots(), cache=None)
    assert got.data["outcome"] == "ok" and got.lines[0].endswith("ok")
