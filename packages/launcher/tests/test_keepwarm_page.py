"""What Home's Sessions row reads of keep-warm, writing nothing: `signal_of`, the count the
ask and the page share, and `would_nudge`, each of its nine steps false alone with the
other eight passing. For each step that is the ask's, the same store and fakes given to
`keepwarm.ask` end without a nudge and with that step's word; in the true case the ask
nudges. Then the same reading under cover and as if pressed (`reading`): each session a
press would not cover, the watcher's `asks`, the directed agents, the cap a press starts
again, and each end of an earlier ask that a press does or does not change."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from test_keepwarm import (IN, SETTINGS, SID, FakeCache, FakeSession, Value, agent, ask,
                           conn, facts, me, nudged)

from moxi_launcher import cachetimers, catalog, joiner, keepwarm, slots, store, waits

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
        self.directed: waits.Directed | None = None

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
            self.settings, self.records, gone=self.gone, now=self.now, tick_s=self.tick_s,
            directed=self.directed)

    def reading(self, *, pressed: bool = False) -> keepwarm.Reading:
        row = store.resolve(self.conn, SID)
        clock = SimpleNamespace(kind="warm", age_s=self.age, ttl_s=self.ttl)
        return keepwarm.reading(
            self.conn, row, SID, clock, self.facts(), listed(*self.agents,
                                                             complete=self.complete),
            self.settings, self.records, gone=self.gone, now=self.now, tick_s=self.tick_s,
            directed=self.directed, pressed=pressed)

    def coverable(self) -> bool:
        return self.reading(pressed=True).step is None

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


#: The launch log's change that makes launch r1 headless with keep_warm off, the one kind
#: of Claude launch keep-warm is off for by its launch.
HEADLESS_OFF = ("UPDATE launches SET values_json = json_set(values_json, '$.keep_warm', "
                "'off'), target = 'headless', slot = NULL WHERE request_id = 'r1'")


def test_step_one_a_launch_that_does_not_keep_warm(case):
    case.conn.execute(HEADLESS_OFF)
    assert case.would() is False
    assert words(case.asked()) == (None, "off")


def test_step_two_an_armed_recycle_run_on_the_launch(case):
    store.insert_run(case.conn, kind="clear", mode="expiry", request_id="r1",
                     session_id=SID, by="launcher", state="armed", job=f"proc:{me()}")
    assert case.would() is False
    assert words(case.asked()) == (None, "run-open")


def test_step_two_an_open_keep_warm_run(case):
    store.insert_run(case.conn, kind="keep-warm", mode="ask", request_id="r1",
                     session_id=SID, by="launcher", state="acting", job=f"proc:{me()}")
    assert case.would() is False
    assert words(case.asked()) == (None, "run-open")


def test_step_two_a_run_past_its_grace_whose_process_is_gone_passes(case):
    store.insert_run(case.conn, kind="clear", mode="expiry", request_id="r1",
                     session_id=SID, by="launcher", state="armed",
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
                     session_id=SID, by="launcher", state="armed",
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


def ended(conn, outcome, *, updated_at, number=None, word=None):
    run = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                           session_id=SID, by="launcher", state="acting",
                           job=f"proc:{me()}", turn_number=number)
    store.end_run(conn, run.run_id, outcome, word=word or outcome,
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


# -- under cover, and as if pressed ---------------------------------------------------------

#: The agents a session directs, found at work by the directed join.
AT_WORK = waits.Directed(("coder@proj-a",))
#: A directed join read whole that found nobody at work.
NOBODY = waits.Directed()


def asking(case, *, value="off"):
    """The launch made headless with its launch value `value`, its clock record written by
    a watcher that asks keep-warm about it."""
    case.conn.execute("UPDATE launches SET values_json = json_set(values_json, "
                      "'$.keep_warm', ?), target = 'headless', slot = NULL "
                      "WHERE request_id = 'r1'", (value,))
    case.records[0]["asks"] = [cachetimers.KEEP_WARM]
    return case


def press(case):
    row = store.resolve(case.conn, SID)
    return keepwarm.cover_on(case.conn, row, SID, turn_number=case.number,
                             waits={"kind": "subagents", "count": 1})


@pytest.fixture
def off(case):
    """A headless launch made with keep-warm off, waiting on a subagent, in the window,
    timed by a watcher that asks about it."""
    return asking(case)


def test_reading_names_the_first_step_not_passed_and_would_nudge_is_true_only_with_none(
        case):
    changes = [
        (lambda: None, None),
        (lambda: setattr(case, "draft", True), 8),
        (lambda: setattr(case, "agents", []), 6),
        (lambda: setattr(case, "turn_open", True), 5),
        (lambda: setattr(case, "age", case.ttl), 4),
        (lambda: setattr(case, "records", []), 3),
        (lambda: case.conn.execute(HEADLESS_OFF), 1),
    ]
    for change, step in changes:
        change()
        got = case.reading()
        assert got.step == step
        assert case.would() is (step is None)
    assert case.reading().case is None


def test_reading_gives_the_case_step_six_found(case):
    assert case.reading() == keepwarm.Reading(None, keepwarm.SUBAGENTS)
    case.agents = []
    store.open_hold(case.conn, "r1", "review")
    assert case.reading() == keepwarm.Reading(None, keepwarm.DIRECTOR)
    case.draft = True
    assert case.reading() == keepwarm.Reading(8, keepwarm.DIRECTOR)


def test_a_launch_made_with_keep_warm_off_is_coverable_and_covered_once_pressed(off):
    assert off.would() is False and off.reading().step == 1
    assert off.reading(pressed=True) == keepwarm.Reading(None, keepwarm.SUBAGENTS)
    total = off.conn.total_changes
    off.coverable()
    assert off.conn.total_changes == total
    press(off)
    assert off.would() is True
    assert words(off.asked()) == ("nudged", "subagents")


def test_cover_for_another_session_id_is_not_cover_for_this_one(off):
    row = store.resolve(off.conn, SID)
    keepwarm.cover_on(off.conn, row, "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
                      turn_number=1, waits={"kind": "subagents", "count": 1})
    assert off.reading().step == 1
    assert off.coverable() is True


# -- step 3, the watcher's `asks` -----------------------------------------------------------


def test_a_record_whose_asks_names_the_subscription_and_cover_on_gives_covered(off):
    press(off)
    assert off.would() is True


@pytest.mark.parametrize("value", ["on", "off"])
def test_a_record_without_asks_gives_covered_only_with_the_launch_value_on(off, value):
    asking(off, value=value)
    del off.records[0]["asks"]
    press(off)
    assert off.would() is (value == "on")
    assert off.coverable() is (value == "on")


@pytest.mark.parametrize("asks", [[], ["cache.something-else"], None])
@pytest.mark.parametrize("value", ["on", "off"])
def test_a_record_whose_asks_lacks_the_subscription_gives_neither(off, asks, value):
    asking(off, value=value)
    off.records[0]["asks"] = asks
    assert off.coverable() is False and off.reading(pressed=True).step == 3
    if value == "off":
        press(off)
    assert off.would() is False and off.reading().step == 3


# -- each session a press would not cover ---------------------------------------------------


def test_a_codex_session_is_not_coverable(off):
    off.conn.execute("UPDATE launches SET harness = 'codex' WHERE request_id = 'r1'")
    assert off.reading(pressed=True).step == 3


def test_a_headless_session_the_watcher_does_not_list_is_not_coverable(off):
    off.conn.execute("UPDATE launches SET target = 'headless', slot = NULL "
                     "WHERE request_id = 'r1'")
    off.records = []
    assert off.reading(pressed=True).step == 3


@pytest.mark.parametrize("ttl, age", [(300.0, 10.0), (490.0, 0.0), (360.0, 0.0)])
def test_a_lifetime_no_ask_is_certain_in_is_not_coverable(off, ttl, age):
    off.ttl, off.age = ttl, age
    assert off.reading(pressed=True).step == 4


def test_an_hour_lifetime_is_coverable_until_age_3110_seconds(off):
    off.age = 3109.0
    assert off.coverable() is True
    off.age = 3110.0
    assert off.reading(pressed=True).step == 4


def test_where_a_session_s_env_can_t_be_read_asks_is_empty_and_not_coverable(off):
    off.records[0]["asks"] = []
    assert off.reading(pressed=True).step == 3


@pytest.mark.parametrize("record", ["none", "stale", "no asks"])
def test_a_watcher_stopped_or_from_before_asks_gives_no_cover_to_a_launch_off(off, record):
    if record == "none":
        off.records = []
    elif record == "stale":
        off.records[0]["seen_at"] = off.now - 3 * TICK_S - 1
    else:
        del off.records[0]["asks"]
    assert off.reading(pressed=True).step == 3


def test_a_subscription_the_watcher_refused_is_not_coverable(off):
    off.records[0]["asks"] = ["cache.other"]
    assert off.reading(pressed=True).step == 3


@pytest.mark.parametrize("draft", [True, None])
def test_a_draft_or_a_box_not_read_gives_neither_covered_nor_coverable(off, draft):
    off.draft = draft
    assert off.reading(pressed=True).step == 8
    press(off)
    assert off.would() is False and off.reading().step == 8


def test_a_run_open_on_the_launch_gives_neither_covered_nor_coverable(off):
    store.insert_run(off.conn, kind="clear", mode="expiry", request_id="r1",
                     session_id=SID, by="launcher", state="armed", job=f"proc:{me()}")
    assert off.reading(pressed=True).step == 2
    press(off)
    assert off.would() is False and off.reading().step == 2


# -- the signals under cover -----------------------------------------------------------------


def test_under_cover_a_directed_agent_at_work_is_a_signal(off):
    off.agents = []
    off.directed = AT_WORK
    assert off.reading(pressed=True) == keepwarm.Reading(None, keepwarm.DIRECTOR)
    press(off)
    assert off.would() is True


def test_without_cover_or_a_press_a_directed_agent_at_work_is_a_signal(case):
    case.agents = []
    case.directed = AT_WORK
    assert case.reading() == keepwarm.Reading(None, keepwarm.DIRECTOR)
    case.directed = NOBODY
    assert case.reading().step == 6


def test_past_the_subagent_cap_a_directed_agent_at_work_is_the_director_case(case):
    for number in (1, 2, 3):
        nudged(case.conn, number=number, agents=["a1"])
    case.number = 4
    assert case.reading() == keepwarm.Reading(7, keepwarm.SUBAGENTS)
    case.directed = AT_WORK
    assert case.reading() == keepwarm.Reading(None, keepwarm.DIRECTOR)


@pytest.mark.parametrize("join", [None, NOBODY, waits.Directed(raised=True),
                                  waits.Directed(unlisted=True)])
def test_under_cover_nobody_directed_at_work_is_no_signal(off, join):
    off.agents = []
    off.directed = join
    press(off)
    assert off.reading().step == 6
    assert off.reading(pressed=True).step == 6


def test_a_hold_in_the_chain_with_the_launch_value_off_and_cover_on_is_a_signal(off):
    off.agents = []
    store.open_hold(off.conn, "r1", "review")
    assert off.reading().step == 1
    press(off)
    assert off.reading() == keepwarm.Reading(None, keepwarm.DIRECTOR)
    assert off.reading(pressed=True) == keepwarm.Reading(None, keepwarm.DIRECTOR)


# -- the cap ---------------------------------------------------------------------------------


def test_a_session_at_its_cap_under_cover_is_not_covered_and_is_coverable(off):
    press(off)
    for number in (1, 2, 3):
        nudged(off.conn, number=number, agents=["a1"])
    assert off.reading() == keepwarm.Reading(7, keepwarm.SUBAGENTS)
    assert off.coverable() is True


def test_under_cover_only_the_nudges_since_the_press_count(off):
    for number in (1, 2):
        nudged(off.conn, number=number, agents=["a1"])
    press(off)
    nudged(off.conn, number=3, agents=["a1"])
    assert off.would() is True


def test_a_director_on_its_holds_at_its_cap_reads_as_a_cover_would_start_its_count(case):
    # The launch value is on and a hold is open: a cover would hold (an open hold keeps
    # it) and start the hold's count again. Home offers the press only where someone is
    # at work (`waits`), which the page's tests read.
    asking(case, value="on")
    case.agents = []
    store.open_hold(case.conn, "r1", "review")
    for number in range(1, 11):
        nudged(case.conn, number=number, word="director")
    case.number = 11
    for join in (NOBODY, None):
        case.directed = join
        assert case.reading() == keepwarm.Reading(7, keepwarm.DIRECTOR)
        assert case.reading(pressed=True) == keepwarm.Reading(None, keepwarm.DIRECTOR)
    case.directed = AT_WORK
    assert case.coverable() is True


# -- step 9: each end of an earlier ask -------------------------------------------------------


def test_a_capped_end_at_the_cap_is_coverable_and_covered_after_the_press(off):
    press(off)
    for number in (1, 2, 3):
        nudged(off.conn, number=number, agents=["a1"])
    ended(off.conn, "blocked", word="capped", updated_at=store.now())
    assert off.reading() == keepwarm.Reading(7, keepwarm.SUBAGENTS)
    assert off.coverable() is True
    press(off)
    assert off.would() is True


def test_a_capped_end_with_the_launch_value_on_and_no_cover_reads_as_before(case):
    for number in (1, 2, 3):
        nudged(case.conn, number=number, agents=["a1"])
    ended(case.conn, "blocked", word="capped", updated_at=store.now())
    assert case.would() is False and case.reading().step == 7


def test_a_capped_end_alone_counts_until_a_press_comes_after_it(off):
    asking(off, value="on")
    ended(off.conn, "blocked", word="capped", updated_at=at(off.now - 10))
    assert off.reading().step == 9
    assert off.coverable() is True


#: Each end of an ask that a press does not change: outcome, word.
UNCHANGED = [("failed", "unsupported"), ("failed", "failed"), ("unconfirmed", "timeout"),
             ("error", "error"), store.JOB_GONE, ("blocked", "draft")]


@pytest.mark.parametrize("outcome, word", UNCHANGED)
def test_an_end_a_press_does_not_change_gives_neither_before_or_after_a_press(
        off, outcome, word):
    ended(off.conn, outcome, word=word, updated_at=store.now())
    assert off.would() is False
    assert off.reading(pressed=True) == keepwarm.Reading(9, keepwarm.SUBAGENTS)
    press(off)
    assert off.would() is False and off.reading().step == 9
    assert off.reading(pressed=True).step == 9


def test_a_capped_end_before_the_press_and_a_failed_end_after_it_give_neither(off):
    ended(off.conn, "blocked", word="capped", updated_at=store.now())
    press(off)
    ended(off.conn, "failed", word="unsupported", updated_at=store.now())
    assert off.reading().step == 9
    assert off.reading(pressed=True).step == 9


def test_a_capped_end_after_the_press_still_counts_under_cover(off):
    press(off)
    for number in (1, 2, 3):
        nudged(off.conn, number=number, agents=["a1"])
    ended(off.conn, "blocked", word="capped", updated_at=store.now())
    off.settings = {**off.settings, "keep_warm.subagent_cap": 5}
    assert off.reading().step == 9
    assert off.coverable() is True


def test_a_newer_nudge_after_a_failed_end_passes_step_nine_under_cover(off):
    ended(off.conn, "failed", word="unsupported", updated_at=at(off.now - 60))
    ended(off.conn, "nudged", updated_at=at(off.now - 30), number=3)
    assert off.coverable() is True
    press(off)
    assert off.would() is True


def test_a_failed_end_before_the_last_request_passes_step_nine_under_cover(off):
    ended(off.conn, "failed", word="unsupported", updated_at=at(off.now - off.age - 60))
    assert off.coverable() is True
    press(off)
    assert off.would() is True
