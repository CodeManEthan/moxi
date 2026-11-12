"""What Home's Sessions row reads of keep-warm, writing nothing: `signal_of`, the count the
ask and the page share, and `would_nudge`, each of its nine steps false alone with the
other eight passing. For each step that is the ask's, the same store and fakes given to
`keepwarm.ask` end without a nudge and with that step's word; in the true case the ask
nudges."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from test_keepwarm import (IN, SETTINGS, SID, FakeCache, FakeSession, Value, agent, ask,
                           conn, facts, me, nudged)

from moxi_launcher import catalog, joiner, keepwarm, slots, store

__all__ = ["conn"]

#: The watcher's tick at cache's default.
TICK_S = 10.0


@pytest.fixture(autouse=True)
def timed_here(monkeypatch):
    """cache's timer can read a session's environment on this machine."""
    monkeypatch.setattr(catalog, "session_env_readable", lambda: True)


def listed(*entries, complete=True):
    return SimpleNamespace(complete=complete, entries=list(entries))


class Case:
    """One reading of a launch with every step passing, a field at a time changed."""

    def __init__(self, conn):
        self.conn = conn
        self.now = time.time()
        self.age, self.ttl = IN, 3600.0
        self.turn_open, self.number, self.draft = False, 4, False
        self.agents = [agent("a1", description="tests")]
        self.complete = True
        self.records = [{"session": SID, "harness": "claude", "seen_at": self.now - 5,
                         "stale_after": 3 * TICK_S}]
        self.settings = dict(SETTINGS)
        self.tick_s: float | None = TICK_S
        self.gone = joiner.proc_gone

    def facts(self):
        value = facts(turn_open=self.turn_open, number=self.number)
        value.draft = self.draft
        return value

    def would(self) -> bool:
        row = store.resolve(self.conn, SID)
        clock = SimpleNamespace(kind="warm", age_s=self.age, ttl_s=self.ttl)
        return keepwarm.would_nudge(
            self.conn, row, SID, clock, self.facts(), listed(*self.agents,
                                                             complete=self.complete),
            self.settings, self.records, gone=self.gone, now=self.now, tick_s=self.tick_s)

    def asked(self):
        """The ask, on the same store and the same reads; a draft is refused at the
        delivery, as turns' gate refuses it."""
        session = FakeSession(Value("refused", "draft") if self.draft else None,
                              facts_seq=[self.facts()], agents=self.agents,
                              complete=self.complete)
        return ask(self.conn, session=session, cache=FakeCache(age=self.age, ttl=self.ttl),
                   settings=self.settings).verdict


@pytest.fixture
def case(conn):
    return Case(conn)


def words(verdict) -> tuple:
    return verdict.outcome, verdict.word


# -- the one true case ------------------------------------------------------------------


def test_with_every_step_passing_the_page_reads_covered_and_the_ask_nudges(case):
    total = case.conn.total_changes
    assert case.would() is True
    assert case.conn.total_changes == total
    assert words(case.asked()) == ("nudged", "subagents")


# -- each step false alone --------------------------------------------------------------


def test_step_one_a_launch_that_does_not_keep_warm(case):
    case.conn.execute("UPDATE launches SET values_json = json_set(values_json, "
                      "'$.keep_warm', 'off') WHERE request_id = 'r1'")
    assert case.would() is False
    assert words(case.asked()) == (None, "off")


def test_step_two_an_armed_recycle_run_on_the_launch(case):
    store.insert_run(case.conn, kind="clear", mode="expiry", request_id="r1",
                     session_id=SID, actor="launcher", state="armed", job=f"proc:{me()}")
    assert case.would() is False
    assert words(case.asked()) == (None, "run-open")


def test_step_two_an_open_keep_warm_run(case):
    store.insert_run(case.conn, kind="keep-warm", mode="ask", request_id="r1",
                     session_id=SID, actor="launcher", state="acting", job=f"proc:{me()}")
    assert case.would() is False
    assert words(case.asked()) == (None, "run-open")


def test_step_two_a_run_past_its_grace_whose_process_is_gone_passes(case):
    store.insert_run(case.conn, kind="clear", mode="expiry", request_id="r1",
                     session_id=SID, actor="launcher", state="armed",
                     job="proc:999999999@1.0", at="2020-01-01T00:00:00.000000Z")
    assert case.would() is True
    assert words(case.asked()) == ("nudged", "subagents")


def test_step_two_a_slots_job_past_its_grace_reads_open_and_slots_is_not_asked(
        case, monkeypatch):
    asked = []

    class Raising:
        def job(self, name):
            asked.append(name)
            raise RuntimeError("slots must not be asked")

    monkeypatch.setattr(slots, "api", lambda: Raising())
    store.insert_run(case.conn, kind="relaunch", mode="verb", request_id="r1",
                     session_id=SID, actor="launcher", state="armed",
                     job="slots:relaunch-1", at="2020-01-01T00:00:00.000000Z")
    assert case.would() is False
    assert asked == []


@pytest.mark.parametrize("change", ["no record", "stale record", "other session",
                                    "harness not timed"])
def test_step_three_the_timer_is_not_timing_the_session(case, monkeypatch, change):
    if change == "no record":
        case.records = []
    elif change == "stale record":
        case.records[0]["seen_at"] = case.now - 3 * TICK_S - 1
    elif change == "other session":
        case.records[0]["session"] = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
    else:
        monkeypatch.setattr(catalog, "_cache_timer", lambda harness: "not timed")
    assert case.would() is False


def test_step_three_a_record_without_its_stale_after_is_not_passed(case):
    del case.records[0]["stale_after"]
    assert case.would() is False


@pytest.mark.parametrize("age, ttl, word", [
    (3600.0 - 360 + 10, 3600.0, "outside"),
    (100.0, 360.0, "no-window"),
    (100.0, 300.0, "no-window"),
])
def test_step_four_no_ask_is_certain_to_come(case, age, ttl, word):
    case.age, case.ttl = age, ttl
    assert case.would() is False
    assert words(case.asked()) == (None, word)


def test_step_four_needs_a_margin_of_the_timer_s_interval_and_a_tick(case):
    shut = case.ttl - keepwarm.SHUT_S
    case.age = shut - (keepwarm.ASK_EVERY_S + TICK_S)
    assert case.would() is False
    case.age -= 1
    assert case.would() is True
    case.tick_s = None
    assert case.would() is False


def test_step_five_an_open_turn(case):
    case.turn_open = True
    assert case.would() is False
    assert words(case.asked()) == (None, "busy")


def test_step_six_no_live_subagent_and_no_open_hold(case):
    case.agents = [agent("a1", state="done"), agent("s1", kind="shell")]
    assert case.would() is False
    assert words(case.asked()) == (None, "idle")


def test_step_six_an_open_hold_passes_for_a_director(case):
    case.agents = []
    store.open_hold(case.conn, "r1", "review")
    assert case.would() is True
    assert words(case.asked()) == ("nudged", "director")


def test_step_seven_at_the_cap(case):
    for number in (1, 2, 3):
        nudged(case.conn, number=number, agents=["a1"])
    assert case.would() is False
    assert words(case.asked()) == ("blocked", "capped")


def test_step_eight_a_draft_in_the_box(case):
    case.draft = True
    assert case.would() is False
    assert words(case.asked()) == ("blocked", "draft")


def test_step_eight_a_box_that_could_not_be_read_is_not_passed(case):
    case.draft = None
    assert case.would() is False


def ended(conn, outcome, *, updated_at, number=None):
    run = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                           session_id=SID, actor="launcher", state="acting",
                           job=f"proc:{me()}", turn_number=number)
    store.end_run(conn, run.run_id, outcome, word=outcome,
                  **({"turn_number": number + 1} if number is not None else {}))
    conn.execute("UPDATE runs SET created_at = ?, updated_at = ? WHERE run_id = ?",
                 (updated_at, updated_at, run.run_id))
    return run


def at(epoch: float) -> str:
    from datetime import datetime, timezone

    return store.stamp(datetime.fromtimestamp(epoch, timezone.utc))


def test_step_nine_an_ask_that_failed_since_the_last_request(case):
    ended(case.conn, "failed", updated_at=at(case.now - 10))
    assert case.would() is False


def test_step_nine_a_failed_ask_before_the_last_request_passes(case):
    ended(case.conn, "failed", updated_at=at(case.now - case.age - 60))
    assert case.would() is True


def test_step_nine_a_nudge_newer_than_a_failed_ask_passes(case):
    ended(case.conn, "failed", updated_at=at(case.now - 60))
    ended(case.conn, "nudged", updated_at=at(case.now - 30), number=3)
    assert case.would() is True


# -- the uncovered cases keep-warm's rule names ------------------------------------------


@pytest.mark.parametrize("change", ["director with no hold", "at the cap",
                                    "lifetime of six minutes", "last 490 seconds"])
def test_each_case_keep_warm_does_not_cover_reads_uncovered(case, change):
    if change == "director with no hold":
        case.agents = []
    elif change == "at the cap":
        for number in (1, 2, 3):
            nudged(case.conn, number=number, agents=["a1"])
    elif change == "lifetime of six minutes":
        case.ttl, case.age = 360.0, 10.0
    else:
        case.age = case.ttl - 489
    assert case.would() is False


# -- signal_of ------------------------------------------------------------------------------


def test_signal_of_counts_live_subagents_and_writes_nothing(conn):
    total = conn.total_changes
    row = store.resolve(conn, SID)
    signal = keepwarm.signal_now(conn, row, listed(agent("a1"), agent("t1", kind="teammate"),
                                                   agent("x", state="done")), [])
    assert signal.case == keepwarm.SUBAGENTS
    assert [a.id for a in signal.agents] == ["a1", "t1"]
    assert conn.total_changes == total


def test_an_agent_named_and_now_unknown_is_newly_dropped_and_not_counted():
    signal, newly = keepwarm.signal_of(
        listed(agent("a1", state="unknown"), agent("a2")), {"a1", "a2"}, set(), ())
    assert newly == ("a1",)
    assert [a.id for a in signal.agents] == ["a2"] and signal.dropped == ("a1",)


def test_an_agent_named_and_gone_from_a_complete_list_is_newly_dropped():
    signal, newly = keepwarm.signal_of(listed(), {"a1"}, set(), ("review",))
    assert newly == ("a1",) and signal.case == keepwarm.DIRECTOR


def test_an_agent_missing_from_an_incomplete_list_is_kept():
    signal, newly = keepwarm.signal_of(listed(complete=False), {"a1"}, set(), ())
    assert newly == () and signal.case is None


def test_an_agent_already_dropped_is_not_counted_even_when_it_runs():
    signal, newly = keepwarm.signal_of(listed(agent("a1")), {"a1"}, {"a1"}, ())
    assert newly == () and signal.case is None


def test_no_list_read_counts_no_subagent():
    signal, newly = keepwarm.signal_of(None, {"a1"}, set(), ())
    assert newly == () and signal.case is None


def test_the_ask_still_writes_the_dropped_event_the_page_does_not(conn):
    first = nudged(conn, number=3, agents=["a1"])
    row = store.resolve(conn, SID)
    nudges = keepwarm.run_of_nudges(conn, "r1", SID, 4)
    gone = listed(agent("a1", state="unknown"))
    total = conn.total_changes
    assert keepwarm.signal_now(conn, row, gone, nudges).case is None
    assert conn.total_changes == total
    assert not [e for e in store.run_events(conn, first.run_id) if e.kind == "dropped"]
    got = ask(conn, session=FakeSession(agents=gone.entries))
    assert words(got.verdict) == (None, "idle")
    assert [e.detail for e in store.run_events(conn, first.run_id)
            if e.kind == "dropped"] == [{"agents": ["a1"]}]
