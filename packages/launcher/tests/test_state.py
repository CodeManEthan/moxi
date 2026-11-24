"""A launched session's state, from the launch log: the red rule over every outcome, the
note not taken, the run that made a launch, stale open runs, and what the relaunch,
recycle and keep-warm parts show."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from moxi import exit as codes
from moxi import modules, proc

from moxi_launcher import catalog, keepwarm, slots, state, store, target

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
        return SimpleNamespace(kind="warm", age_s=10.0, ttl_s=self.ttl)


@pytest.fixture(autouse=True)
def env_readable(monkeypatch):
    """cache reads a session's environment here, as on Linux and macOS, so keep-warm can
    act on a Claude launch whatever system the tests run on."""
    monkeypatch.setattr(catalog, "session_env_readable", lambda: True)


@pytest.fixture
def conn(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    launched(conn)
    yield conn
    conn.close()


def launched(conn, *, request_id="r1", values=None, session_id=SID, relaunch_of=None):
    store.insert(conn, request_id=request_id, by="ann", harness="claude", target="slot",
                 cwd="/work", values={"model": "opus", **(values or {})}, argv=["claude"],
                 env={}, posture="operator", session_id=session_id, slot="3",
                 relaunch_of=relaunch_of)


def ran(conn, outcome, *, kind="keep-warm", mode="ask", word=None, detail=None,
        request_id="r1", at=None, **values):
    run = store.insert_run(conn, kind=kind, mode=mode, request_id=request_id,
                           session_id=SID, by="launcher", state="acting",
                           job=me(), at=at)
    store.end_run(conn, run.run_id, outcome, word=word, detail=detail, **values)
    return store.get_run(conn, run.run_id)


def read(conn, which=SID, **kw):
    kw.setdefault("cache", None)
    return state.state(which, conn=conn, slots_api=FakeSlots(), **kw)


# -- the red rule ------------------------------------------------------------------------


def test_a_session_no_launch_holds_is_refused(conn):
    with pytest.raises(target.NotLaunched):
        read(conn, NEW, turns=SimpleNamespace(find=lambda **kw: None))


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
                           session_id=SID, by="launcher", state="acting", job=me())
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
                     by="launcher", state="acting", job=me())
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
                           session_id=SID, by="ann", job="slots:relaunch-x")
    assert read(conn).relaunch.state == "armed"
    store.advance_run(conn, run.run_id, "waiting")
    store.add_run_event(conn, run.run_id, "wait", {"word": "turn", "detail": "open"})
    store.add_run_event(conn, run.run_id, "wait", {"word": "person", "detail": "typing"})
    assert read(conn).relaunch.state == "waiting: person"


def test_an_open_run_whose_job_is_gone_shows_stale(conn):
    store.insert_run(conn, kind="relaunch", mode="self", request_id="r1", session_id=SID,
                     by="ann", job="proc:999999999@1.0", at=OLD)
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


def test_recycle_shows_its_options_last_run_and_the_last_telling(conn):
    conn.execute("UPDATE launches SET values_json = ?, env_json = ? WHERE request_id = 'r1'",
                 ('{"recycle_on_expiry": "clear", "recycle_at_limit": "relaunch"}',
                  '{"MOXI_USAGE_CROSSING_AT": "2x"}'))
    ran(conn, "cleared", kind="clear", mode="expiry", at=OLD)
    ran(conn, "nudged")
    store.add_event(conn, "recycle-told", {"crossing_key": "k"}, request_id="r1",
                    session_id=SID, at="2026-01-03T00:00:00.000000Z")
    got = read(conn).recycle
    assert (got.on_expiry, got.at_limit) == ("clear", "relaunch")
    assert (got.last.kind, got.last.outcome) == ("clear", "cleared")
    assert got.told_at == "2026-01-03T00:00:00.000000Z"
    line = read(conn).lines()[2]
    assert "on expiry clear, at limit relaunch;" in line and "clear cleared" in line
    assert "2x" not in line


def test_an_old_compact_row_shows_it_no_longer_acts_and_no_mark(conn):
    conn.execute("UPDATE launches SET values_json = ?, env_json = ? WHERE request_id = 'r1'",
                 ('{"recycle_at_limit": "compact", "limit_at": "1.5x"}',
                  '{"MOXI_USAGE_CROSSING_AT": "1.5x"}'))
    store.insert_run(conn, kind="compact", mode="limit", request_id="r1", session_id=SID,
                     by="launcher", job="proc:1@1.0")
    store.prune(conn, 90)
    got = read(conn)
    assert got.recycle.at_limit == "compact (no longer acts)" == state.RETIRED_AT_LIMIT
    assert (got.recycle.last.mode, got.recycle.last.outcome, got.recycle.last.word) == (
        "limit", "failed", "retired")
    assert "at limit compact (no longer acts); last: compact failed (retired)" in (
        got.lines()[2])
    assert "1.5x" not in got.lines()[2]


@pytest.mark.parametrize("sources", [None, {"recycle_at_limit": "shipped"},
                                     {"recycle_at_limit": "typed"},
                                     {"recycle_at_limit": "left-out"}])
def test_a_launch_at_off_shows_off_whatever_its_source(conn, monkeypatch, sources):
    # recycle_at_limit ships off: show names the row's own value, never a shipped
    # relaunch, even on a launch that could recycle itself had it been set to.
    monkeypatch.setattr(catalog, "slots_here", lambda: True)
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "notes" and real(name))
    values = {"recycle_at_limit": "off", "warn_at": "2x"}
    if sources is not None:
        values["sources"] = sources
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 (json.dumps(values),))
    got = read(conn)
    assert got.recycle.at_limit == "off"
    assert "at limit off" in got.lines()[2] and "relaunch" not in got.lines()[2]


@pytest.mark.parametrize("mark, shown", [
    ("off", "relaunch (armed, no warning)"), ("", "relaunch (armed, no warning)"),
    (None, "relaunch (armed, no warning)"), ("2x", "relaunch")])
def test_a_relaunch_launch_with_no_mark_shows_it_is_armed_with_no_warning(
        conn, monkeypatch, mark, shown):
    monkeypatch.setattr(catalog, "slots_here", lambda: True)
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "notes" and real(name))
    values = {"recycle_at_limit": "relaunch"}
    if mark is not None:
        values["warn_at"] = mark
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 (json.dumps(values),))
    got = read(conn)
    assert got.recycle.at_limit == shown
    assert f"at limit {shown}" in got.lines()[2]


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


def test_keep_warm_under_cover_alone_is_on_with_the_chain_s_holds_listed(conn):
    store.open_hold(conn, "r1", "port-cache")
    launched(conn, request_id="r2", session_id=NEW, relaunch_of="r1")
    keepwarm.cover_on(conn, store.get(conn, "r2"), NEW, turn_number=3,
                      waits={"kind": "subagents", "count": 1})
    got = read(conn, NEW).keep_warm
    assert got.on and got.holds == ("port-cache",)
    assert read(conn, NEW).lines()[3].startswith("keep warm: on; holds: port-cache")


def test_cover_for_another_session_id_leaves_keep_warm_off(conn):
    keepwarm.cover_on(conn, store.get(conn, "r1"), NEW, turn_number=3,
                      waits={"kind": "subagents", "count": 1})
    assert not read(conn).keep_warm.on


@pytest.mark.parametrize("missing", ["turns", "cache", "session environments"])
def test_keep_warm_reads_off_where_keep_warm_can_t_act_on_this_install(conn, monkeypatch,
                                                                       missing):
    """Without turns or cache, or where cache can't read a session's environment, the
    `keep_warm` row isn't offered and nothing nudges the session, so it reads off, as
    its launch was stamped; holds and cache aren't read."""
    conn.execute("UPDATE launches SET values_json = json_set(values_json, '$.keep_warm', "
                 "'on') WHERE request_id = 'r1'")
    assert read(conn).keep_warm.on
    store.open_hold(conn, "r1", "port-cache")
    if missing == "session environments":
        monkeypatch.setattr(catalog, "session_env_readable", lambda: False)
    else:
        real = slots.links
        monkeypatch.setattr(slots, "links", lambda name: name != missing and real(name))
    cache = FakeCache()
    got = read(conn, cache=cache)
    assert got.keep_warm.on is False and got.keep_warm.holds == ()
    assert got.lines()[3] == "keep warm: off" and cache.reads == []


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


def owned_by_parent(conn):
    """The launch's own `claude` is this process's parent, as the launcher records it."""
    ident = proc.identity(os.getppid())
    conn.execute("UPDATE launches SET pid = ?, pid_started = ? WHERE request_id = 'r1'",
                 (ident.pid, ident.started))


def live(session_id):
    """turns serving `session_id` from this process, a child of the launch's `claude`."""
    session = SimpleNamespace(session_id=session_id, endpoint_pid=os.getpid(),
                              pid=os.getppid(), close=lambda: None)
    asked = []
    return SimpleNamespace(find=lambda **kw: asked.append(kw) or session, asked=asked)


def test_show_finds_an_id_the_launch_never_learned_by_its_claude_process(conn):
    owned_by_parent(conn)
    before = store.session_ids(conn, "r1")
    turns = live(NEW)
    got = state.show(NEW, conn=conn, turns=turns, slots_api=FakeSlots(), cache=None)
    assert got.lines[0] == f"session {NEW} (launch r1): ok"
    assert turns.asked == [{"session_id": NEW}]
    assert store.session_ids(conn, "r1") == before


def test_show_with_no_session_reads_the_caller_s_own_by_its_claude_process(conn):
    owned_by_parent(conn)
    ident = proc.identity(os.getpid())
    conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = 'r1'",
                 (f"{ident.pid}@{ident.started:.6f}",))
    got = state.show(conn=conn, turns=live(NEW), slots_api=FakeSlots(), cache=None)
    assert got.lines[0] == f"session {NEW} (launch r1): ok"


def test_show_of_an_id_whose_session_runs_in_another_claude_is_refused(conn):
    # The row records no process of the launch, as a nested session's `claude` is none.
    with pytest.raises(target.NotLaunched):
        state.show(NEW, conn=conn, turns=live(NEW), slots_api=FakeSlots(), cache=None)
