"""A press of `Keep warm` (`POST /api/keep-warm`): the session read now as a join pass
reads it, through one `waits.Reads` whose deadline is `joiner.JOIN_BUDGET_S` from the
press; each refusal, in the order of keep-warm's steps; an ask's end a press does not
change refused `ask-failed`; one cover event written by a press, none for a covered
session or a refusal, and nothing delivered; the joiner woken for a pass, which reads the
session covered; and a route the gateway does not reach."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from test_keepwarm import agent, me
from test_page import _send, loopback, post, served
from test_page_joiner import fresh, stop, until
from test_page_waits import (ASKS, Cache, Turns, agents_case, joined, live, pressed, seeded,
                             wired)

from moxi import aio
from moxi_launcher import joiner, keepwarm, page, pagehandlers, settings, store, waits

__all__ = ["fresh", "wired"]

ROUTE = "/api/keep-warm"


def press(rid: str = "r1") -> dict | None:
    status, body = post(ROUTE, {"request_id": rid})
    assert status == 200, body
    return body["refused"]


def code(rid: str = "r1") -> str | None:
    refused = press(rid)
    return None if refused is None else refused["code"]


def covers(rid: str = "r1") -> list[store.Event]:
    conn = store.connect()
    try:
        return [e for e in store.events(conn, rid) if e.kind == keepwarm.COVER]
    finally:
        conn.close()


def writing(run) -> None:
    conn = store.connect()
    try:
        run(conn)
    finally:
        conn.close()


class Clock(Cache):
    """cache's read of every session, of the kind given, and the watcher timing the
    sessions given."""

    def __init__(self, timing, *, kind="warm", **options):
        super().__init__(timing, **options)
        self.kind = kind

    def read(self, harness, session_id, **options):
        return SimpleNamespace(kind=self.kind, age_s=self.age_s, ttl_s=self.ttl_s,
                               ttl_source="session", basis=None)


@pytest.fixture
def waiting(wired):
    """A launch made with `keep_warm` off whose live session waits on one subagent, its
    turn closed, in a warm cache the watcher asks keep-warm about: a press covers it."""
    sid = seeded()
    session = live(sid, entries=[agent("a1")])
    wired.turns = Turns({sid: session})
    wired.cache = Clock([sid], asks=ASKS)
    return SimpleNamespace(sid=sid, session=session, wired=wired)


@pytest.fixture
def woken(monkeypatch):
    """The feed's wakes, recorded."""
    calls = []
    monkeypatch.setattr(pagehandlers, "_current", SimpleNamespace(wake=lambda: calls.append(1)))
    return calls


# -- a press -------------------------------------------------------------------------------


def test_a_press_writes_one_cover_event_delivers_nothing_and_wakes_the_joiner(waiting, woken):
    assert press() is None
    [event] = covers()
    assert event.session_id == waiting.sid
    assert event.detail == {"state": "on", "by": "page", "session_id": waiting.sid,
                            "turn_number": 4, "waits": {"kind": "subagents", "count": 1}}
    assert waiting.session.delivered == []
    assert woken == [1]
    got = joined()
    assert got["covered"] is True and got["coverable"] is None


def test_a_press_on_a_covered_session_writes_nothing(waiting, woken):
    pressed(waiting.sid)
    assert press() is None
    assert len(covers()) == 1
    assert woken == []


def test_a_press_on_a_launch_keeping_warm_by_its_value_writes_nothing(wired, woken):
    sid = seeded(values={"keep_warm": "on"})
    wired.turns = Turns({sid: live(sid, entries=[agent("a1")])})
    wired.cache = Clock([sid], asks=ASKS)
    assert press() is None
    assert covers() == [] and woken == []


def test_a_press_on_a_director_covers_it_on_the_agents_it_directs(wired):
    director(wired, ("coder@proj-a", live("x", turn_open=True)), ("critic@proj-a", live("x")))
    assert press() is None
    [event] = covers()
    assert event.detail["waits"] == {"kind": "agents", "count": 1}
    assert joined()["covered"] is True


def test_a_capped_end_does_not_stop_a_press(waiting):
    writing(lambda conn: ended(conn, waiting.sid, "blocked", "capped"))
    assert press() is None
    assert len(covers()) == 1


# -- the refusals, each alone ----------------------------------------------------------------


def test_a_request_id_no_launch_has_is_not_launched(wired):
    assert code("nobody") == "not-launched"
    assert covers("nobody") == []


def test_a_launch_with_no_session_id_has_no_session(waiting):
    def unlearned(conn):
        conn.execute("UPDATE launches SET session_id = NULL WHERE request_id = 'r1'")
        conn.execute("DELETE FROM session_ids WHERE request_id = 'r1'")
        conn.commit()

    writing(unlearned)
    assert code() == "no-session"


def test_an_unclaimed_launch_is_refused_as_find_raises(wired):
    sid = seeded(claimed=False)
    wired.turns = Turns({sid: live(sid, entries=[agent("a1")])})
    wired.cache = Clock([sid], asks=ASKS)
    refused = press()
    assert refused["code"] == "unclaimed" and refused["row"] is None


def test_a_session_another_endpoint_serves_is_changed(waiting):
    waiting.session.endpoint_pid = 1
    assert code() == "changed"


def test_without_turns_the_press_is_no_turns(waiting):
    waiting.wired.turns = None
    assert code() == "no-turns"


def test_a_turns_find_that_fails_is_turns_failed(waiting):
    waiting.wired.turns = Turns({waiting.sid: OSError("the endpoint did not answer")})
    assert code() == "turns-failed"


def test_a_session_turns_does_not_find_is_not_live(waiting):
    waiting.wired.turns = Turns({})
    assert code() == "not-live"


def test_an_open_run_is_run_open(waiting):
    writing(lambda conn: open_run(conn, waiting.sid))
    refused = press()
    assert refused["code"] == "run-open"
    assert refused["hint"] == "another act is running on this session"


@pytest.mark.parametrize("cache", ["not timed", "asks lacks it", "no cache"])
def test_a_session_the_watcher_does_not_ask_about_is_no_timer(waiting, cache):
    waiting.wired.cache = {"not timed": Clock([], asks=ASKS),
                           "asks lacks it": Clock([waiting.sid], asks=[]),
                           "no cache": None}[cache]
    refused = press()
    assert refused["code"] == "no-timer"
    assert "keep-warm.timer" in refused["hint"]


def test_a_cold_codex_session_is_no_timer(waiting):
    writing(lambda conn: (conn.execute(
        "UPDATE launches SET harness = 'codex' WHERE request_id = 'r1'"), conn.commit()))
    waiting.wired.cache = Clock([waiting.sid], asks=ASKS, kind="cold", age_s=4000.0)
    assert code() == "no-timer"


def test_a_cold_cache_is_cold(waiting):
    waiting.wired.cache = Clock([waiting.sid], asks=ASKS, kind="cold", age_s=4000.0)
    refused = press()
    assert refused["code"] == "cold"
    assert refused["hint"] == "nothing is sent to a session whose cache is cold"


def test_a_lifetime_too_short_for_an_ask_is_no_window(waiting):
    waiting.wired.cache = Clock([waiting.sid], asks=ASKS, age_s=10.0, ttl_s=480.0)
    assert code() == "no-window"


def test_a_cache_too_near_its_cold_time_is_too_late(waiting):
    waiting.wired.cache = Clock([waiting.sid], asks=ASKS, age_s=3200.0)
    refused = press()
    assert refused["code"] == "too-late"
    assert refused["hint"] == "the cold time is too near for a nudge to be sent"


def test_an_open_own_turn_is_busy(waiting):
    waiting.session.facts_seq[0].turn_open = True
    refused = press()
    assert refused["code"] == "busy" and refused["hint"] == "it is working, not waiting"


def test_a_session_nobody_works_for_is_not_waiting(waiting):
    waiting.session.entries = [agent("a1", state="done")]
    assert code() == "not-waiting"


def test_an_open_hold_alone_is_not_waiting(wired):
    sid = seeded(values={"keep_warm": "on"})
    wired.turns = Turns({sid: live(sid)})
    wired.cache = Clock([sid], asks=ASKS)
    writing(lambda conn: (store.open_hold(conn, "r1", "review"), nudges(conn, sid, 10)))
    assert code() == "not-waiting"


@pytest.mark.parametrize("unread", ["agents raise", "agents incomplete", "comms down",
                                    "directed unlisted"])
def test_nobody_at_work_on_a_list_not_read_whole_is_unreadable(wired, unread):
    if unread == "directed unlisted":
        director(wired, ("coder@proj-a", live("x", agents=None)))
    else:
        sid = director(wired)
        session = wired.turns.sessions[sid]
        if unread == "agents raise":
            session.entries = OSError("turns did not answer")
        elif unread == "agents incomplete":
            session.complete = False
        else:
            wired.comms.offline = True
    refused = press()
    assert refused["code"] == "unreadable" and refused["hint"] == "try again"
    assert covers() == []


def test_an_agents_list_that_fails_with_a_directed_agent_at_work_goes_on(wired):
    sid = director(wired, ("coder@proj-a", live("x", turn_open=True)))
    wired.turns.sessions[sid].entries = OSError("turns did not answer")
    assert press() is None
    assert len(covers()) == 1


def test_a_draft_is_draft(waiting):
    waiting.session.facts_seq[0].draft = True
    refused = press()
    assert refused["code"] == "draft"
    assert refused["hint"] == "clear the text typed in the session"


def capped_at(monkeypatch, cap) -> None:
    shipped = settings.current()

    class Capped:
        def __getitem__(self, key):
            return cap if key == "keep_warm.subagent_cap" else shipped[key]

    monkeypatch.setattr(settings, "current", Capped)


def test_a_cap_of_none_is_capped(waiting, monkeypatch):
    capped_at(monkeypatch, 0)
    refused = press()
    assert refused["code"] == "capped"
    assert refused["problem"] == ("keep-warm's cap of nudges in a row leaves a press none "
                                  "to send to this session")


def test_a_cap_setting_that_can_t_be_read_is_capped_and_named(waiting, monkeypatch):
    capped_at(monkeypatch, "many")
    refused = press()
    assert refused["code"] == "capped" and covers() == []
    assert refused["problem"] == ("keep-warm's cap setting keep_warm.subagent_cap could not "
                                  "be read, so a press has no nudge to send to this session")


# -- the refusals, in order ------------------------------------------------------------------


def test_each_refusal_is_answered_only_when_every_one_before_it_passes(waiting, woken):
    """Every step but the launch's own fails at once; each fix lets the next one answer."""
    sid, session, wired = waiting.sid, waiting.session, waiting.wired
    holder = {}
    writing(lambda conn: ended(conn, sid, "failed", "unsupported"))
    writing(lambda conn: holder.setdefault("run", open_run(conn, sid)))
    wired.cache = Clock([], asks=ASKS, kind="cold", age_s=3200.0)
    session.facts_seq[0].turn_open = True
    session.facts_seq[0].draft = True
    session.entries = []

    def end_open(conn):
        store.end_run(conn, holder["run"].run_id, "done")

    def drop_failed(conn):
        conn.execute("DELETE FROM runs WHERE kind = 'keep-warm'")
        conn.commit()

    fixes = [
        ("run-open", lambda: writing(end_open)),
        ("no-timer", lambda: setattr(wired.cache, "timing", [sid])),
        ("cold", lambda: setattr(wired.cache, "kind", "warm")),
        ("too-late", lambda: setattr(wired.cache, "age_s", 3000.0)),
        ("busy", lambda: setattr(session.facts_seq[0], "turn_open", False)),
        ("not-waiting", lambda: setattr(session, "entries", [agent("a1")])),
        ("draft", lambda: setattr(session.facts_seq[0], "draft", False)),
        ("ask-failed", lambda: writing(drop_failed)),
    ]
    for expected, fix in fixes:
        assert code() == expected
        assert covers() == [] and woken == []
        fix()
    assert press() is None
    assert len(covers()) == 1 and woken == [1]


# -- step 9 ----------------------------------------------------------------------------------


#: Each end of an ask a press does not change: outcome, word.
UNCHANGED = [("failed", "unsupported"), ("failed", "failed"), ("unconfirmed", "timeout"),
             ("error", "error"), store.JOB_GONE, ("blocked", "draft")]


@pytest.mark.parametrize("outcome, word", UNCHANGED)
def test_an_end_a_press_does_not_change_is_ask_failed_and_writes_nothing(waiting, woken,
                                                                         outcome, word):
    writing(lambda conn: ended(conn, waiting.sid, outcome, word))
    refused = press()
    assert refused["code"] == "ask-failed"
    assert refused["problem"].endswith(f"ended {outcome}, {word}")
    assert refused["hint"] == ("a press does not change it; moxi launcher show "
                               f"{waiting.sid} has the run")
    assert covers() == [] and woken == []


# -- the reads' deadline ---------------------------------------------------------------------


class Made:
    """The `waits.Reads` a press makes, on a clock the test moves: `after` calls through it
    each move the clock past the deadline."""

    def __init__(self, monkeypatch, *, after=None, comms_after=None):
        self.made, self.calls, self.comms_calls = [], 0, 0
        self.after, self.comms_after = after, comms_after
        made = self

        class Reads(waits.Reads):
            def __init__(self, *, deadline, clock=time.monotonic, comms=None, turns=None):
                made.made.append((deadline, time.monotonic()))
                self.now = 0.0
                super().__init__(deadline=joiner.JOIN_BUDGET_S, clock=lambda: self.now,
                                 comms=comms, turns=turns)

            def call(self, fn, /, *args, **kwargs):
                made.calls += 1
                if made.after is not None and made.calls > made.after:
                    self.now = self.deadline
                return super().call(fn, *args, **kwargs)

            def comms_call(self, fn, /, *args, **kwargs):
                made.comms_calls += 1
                if made.comms_after is not None and made.comms_calls > made.comms_after:
                    self.now = self.deadline
                return super().comms_call(fn, *args, **kwargs)

        monkeypatch.setattr(waits, "Reads", Reads)


def test_the_press_reads_through_one_reads_with_a_deadline_of_the_join_budget(waiting,
                                                                             monkeypatch):
    made = Made(monkeypatch)
    assert press() is None
    [(deadline, at)] = made.made
    assert joiner.JOIN_BUDGET_S == 8.0
    assert deadline - at == pytest.approx(joiner.JOIN_BUDGET_S, abs=1.0)


@pytest.mark.parametrize("after", range(8))
def test_a_read_of_the_session_cut_short_by_the_deadline_is_unreadable(waiting, monkeypatch,
                                                                       after):
    """In order: the cache read, facts, agents, settings, the clock records, the watcher's
    tick, the run of nudges and the signal."""
    Made(monkeypatch, after=after)
    refused = press()
    assert refused["code"] == "unreadable" and refused["hint"] == "try again"
    assert covers() == []


def director(wired, *workers):
    agents_case(wired, *workers)
    sid = next(s for s in wired.turns.sessions if wired.comms.owners.get(s))
    wired.cache = Clock([sid], asks=ASKS)
    return sid


def test_a_join_cut_short_with_nobody_found_is_unreadable(wired, monkeypatch, woken):
    director(wired, ("coder@proj-a", live("x", turn_open=True)))
    Made(monkeypatch, comms_after=1)
    refused = press()
    assert refused["code"] == "unreadable"
    assert "not read within 8 s" in refused["problem"]
    assert covers() == [] and woken == []


def test_a_join_cut_short_with_an_agent_found_goes_on(wired, monkeypatch):
    director(wired, ("coder@proj-a", live("x", turn_open=True)),
             ("critic@proj-a", live("x", turn_open=True)))
    # owner, roster, the first session_of; the second meets the deadline.
    Made(monkeypatch, comms_after=3)
    assert press() is None
    [event] = covers()
    assert event.detail["waits"] == {"kind": "agents", "count": 1}


# -- the joiner's wake, and the route ---------------------------------------------------------


def test_a_press_wakes_the_running_feed_whose_next_pass_reads_it_covered(waiting,
                                                                         monkeypatch):
    async def go():
        feed = pagehandlers._feed()
        try:
            await until(lambda: feed.joiner.passes >= 1 and feed.joined_once.is_set())
            assert feed.joiner.kept["r1"].facts["covered"] is False
            passes = feed.joiner.passes
            data = json.dumps({"request_id": "r1"}).encode()
            status, raw = await _send(served(), "POST", ROUTE, b"", data)
            assert status == 200 and json.loads(raw) == {"refused": None}
            await until(lambda: feed.joiner.passes > passes
                        and feed.joiner.kept["r1"].facts["covered"] is True)
        finally:
            stop(feed)

    monkeypatch.setattr(joiner, "JOIN_EVERY", 60.0)
    asyncio.run(go())


def test_wake_from_another_thread_starts_a_pass(monkeypatch):
    monkeypatch.setattr(joiner, "JOIN_EVERY", 60.0)

    async def go():
        feed = pagehandlers._feed()
        try:
            await until(lambda: feed.joiner.passes >= 1 and feed.joined_once.is_set())
            passes = feed.joiner.passes
            await aio.offload(feed.wake)
            await until(lambda: feed.joiner.passes > passes, timeout_s=3.0)
        finally:
            stop(feed)

    asyncio.run(go())


def test_the_route_is_a_desk_act_the_gateway_does_not_reach():
    exported = loopback().export(page.PAGE)
    [route] = [r for r in exported["routes"] if r["path"] == ROUTE]
    assert route["kind"] == "act" and route["gateway"] is False
    assert list(route["body"]) == ["request_id"]


def test_a_press_without_a_request_id_is_refused_by_the_route(waiting):
    status, _ = post(ROUTE, {})
    assert status == 400
    assert covers() == []


# -- helpers ---------------------------------------------------------------------------------


def ended(conn, sid, outcome, word):
    """A keep-warm run ended `outcome`, `word` now, after the session's last request."""
    run = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                           session_id=sid, actor="launcher", state="acting",
                           job=f"proc:{me()}", turn_number=4)
    store.end_run(conn, run.run_id, outcome, word=word)
    return run


def open_run(conn, sid):
    """A run open on the launch, its job this test's own process."""
    return store.insert_run(conn, kind="relaunch", mode="verb", request_id="r1",
                            session_id=sid, actor="launcher", state="armed",
                            job=f"proc:{me()}")


def nudges(conn, sid, count):
    """`count` nudged keep-warm runs in a row, the newest opening turn 4."""
    for number in range(4 - count, 4):
        run = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                               session_id=sid, actor="launcher", state="acting",
                               job=f"proc:{me()}", turn_number=number)
        store.end_run(conn, run.run_id, "nudged", word="nudged", turn_number=number + 1)
