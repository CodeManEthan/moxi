"""Keep-warm against fakes of turns, slots and cache: the timer's subscription, the ask's
reads in order and at each window edge, both cases and their texts, the run of nudges
across turn numbers and a clear's new session id, the cap, agents dropped for the rest of
a run, every delivery verdict, and the named holds across a launch chain."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from moxi import exit as codes
from moxi import modules, paths, proc

from moxi_launcher import api, cachetimers, job, keepwarm, recycle, relaunch, store, target

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
NEW = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
OTHER = "11111111-2222-4333-8444-555555555555"
SETTINGS = {"relaunch.person_s": 120, "records.keep_days": 90,
            "keep_warm.subagent_cap": 3, "keep_warm.director_cap": 10,
            "keep_warm.subagent_texts": ["check", "decide", "last"],
            "keep_warm.director_texts": ["reply warm", "recheck holds"]}
#: An age inside the window of a one-hour cache.
IN = 3000.0


def me() -> str:
    ident = proc.identity(os.getpid())
    return f"{ident.pid}@{ident.started:.6f}"


class Value:
    def __init__(self, kind, reason=None):
        self.KIND, self.reason = kind, reason


class FakeDelivery:
    def __init__(self, value):
        self.value = value

    def wait_started(self, timeout):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


def agent(id, kind="subagent", state="running", final=False, description=None):
    return SimpleNamespace(id=id, kind=kind, state=state, final=final,
                           description=description)


def facts(turn_open=False, number=4, session_id=SID):
    return SimpleNamespace(turn_open=turn_open, turn=None, turn_number=number, draft=False,
                           stale=False, session_id=session_id)


class FakeSession:
    def __init__(self, value=None, *, facts_seq=None, agents=(), complete=True,
                 session_id=SID):
        self.session_id, self.endpoint_pid, self.pid = session_id, os.getpid(), os.getppid()
        self.capabilities = frozenset({"deliver", "only-if-idle"})
        self.value = Value("started") if value is None else value
        self.facts_seq = list(facts_seq or [facts(session_id=session_id)])
        self.entries, self.complete = list(agents), complete
        self.delivered, self.closed = [], 0

    def facts(self):
        value = self.facts_seq[0] if len(self.facts_seq) == 1 else self.facts_seq.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def agents(self):
        if isinstance(self.entries, Exception):
            raise self.entries
        return SimpleNamespace(complete=self.complete, entries=self.entries)

    def deliver(self, text, **options):
        self.delivered.append((text, options))
        if isinstance(self.value, Exception) and not isinstance(self.value, TimeoutError):
            raise self.value
        return FakeDelivery(self.value)

    def close(self):
        self.closed += 1


class FakeTurns:
    def __init__(self, session):
        self.session = session

    def find(self, *, session_id=None, pid=None):
        return self.session


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
    def __init__(self, age=IN, ttl=3600.0, kind="warm"):
        self.age, self.ttl, self.kind, self.reads = age, ttl, kind, []

    def read(self, harness, session_id, **options):
        self.reads.append((harness, session_id, options))
        return SimpleNamespace(kind=self.kind, age_seconds=self.age, ttl_seconds=self.ttl)


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    launched(connection)
    yield connection
    connection.close()


def launched(conn, *, request_id="r1", values=None, session_id=SID, claimed=True,
             relaunch_of=None, resume_of=None):
    store.insert(conn, request_id=request_id, actor="ann", harness="claude", target="slot",
                 cwd="/work", values={"model": "opus", "keep_warm": "on", **(values or {})},
                 argv=["claude"], env={}, posture="operator", session_id=session_id,
                 slot="3", relaunch_of=relaunch_of, resume_of=resume_of)
    store.set_facts(conn, request_id, pane_id="%9")
    if claimed:
        conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = ?",
                     (me(), request_id))


def ask(conn, *, session=None, cache=None, which=SID, request_id="r1", **kw):
    session = session or FakeSession(agents=[agent("a1", description="tests")])
    verdict = keepwarm.ask(which, conn=conn, turns=FakeTurns(session),
                           slots_api=FakeSlots(), cache=cache or FakeCache(),
                           settings=kw.pop("settings", SETTINGS), **kw)
    return SimpleNamespace(verdict=verdict, session=session,
                           runs=store.runs(conn, request_id=request_id))


def events(conn, run_id):
    return [e.kind for e in store.run_events(conn, run_id)]


def nudged(conn, *, session_id=SID, number=4, agents=(), request_id="r1"):
    """A `nudged` run as an ask leaves it: the turn it opened is `number + 1`."""
    run = store.insert_run(conn, kind="keep-warm", mode="ask", request_id=request_id,
                           session_id=session_id, actor="launcher", state="acting",
                           turn_number=number, job=f"proc:{me()}")
    store.add_run_event(conn, run.run_id, "nudge", {"agents": list(agents)})
    store.end_run(conn, run.run_id, "nudged", word="subagents", turn_number=number + 1)
    return run


# -- the timer's subscription ------------------------------------------------------------


def keep_warm_sub():
    [sub] = [t for t in cachetimers.TIMERS if t["name"] == "launcher.keep-warm"]
    return sub


def test_the_subscription_asks_in_the_window_before_expiry_for_the_stamped_session():
    assert keep_warm_sub() == {
        "name": "launcher.keep-warm", "at": -900, "until": -360, "every": 120,
        "requires_env": {"MOXI_LAUNCHER_KEEP_WARM": "on"},
        "run": ["moxi-launcher-job", "keep-warm", "{session}"]}


def test_the_job_takes_the_keep_warm_kind_and_runs_the_ask(monkeypatch, capsys):
    asked = []
    monkeypatch.setattr(keepwarm, "ask",
                        lambda sid: asked.append(sid) or recycle.Verdict("nudged"))
    assert "keep-warm" in job.KINDS
    assert job.main(["keep-warm", SID]) == 0
    assert asked == [SID] and capsys.readouterr().out.startswith(f"session {SID}: ")


@pytest.mark.parametrize("verdict, code", [
    (recycle.Verdict(None, "idle"), 0), (recycle.Verdict("nudged"), 0),
    (recycle.Verdict("blocked", "capped"), 1), (recycle.Verdict("unconfirmed"), 1),
])
def test_a_keep_warm_ask_exits_zero_when_it_nudged_or_did_nothing(monkeypatch, verdict,
                                                                  code):
    monkeypatch.setattr(keepwarm, "ask", lambda sid: verdict)
    assert job.main(["keep-warm", SID]) == code


@pytest.mark.skipif(not modules.available("cache"), reason="cache is not installed")
def test_cache_s_own_timers_listing_takes_the_keep_warm_subscription():
    import json
    import subprocess

    done = subprocess.run([str(paths.script("moxi")), "cache", "timers", "--json"],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
    view = json.loads(done.stdout)
    ours = [s for s in view["subscriptions"] if s["name"] == "launcher.keep-warm"]
    assert len(ours) == 1 and ours[0]["argv0"] == str(paths.script("moxi-launcher-job"))
    assert not [r for r in view["refused"] if (r.get("name") or "").startswith("launcher.")]


# -- the ask: a nudge --------------------------------------------------------------------


def test_a_session_waiting_on_a_subagent_is_nudged_and_its_run_records_it(conn):
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "subagents")
    [(text, options)] = got.session.delivered
    assert options == {"only_if_idle": True, "if_turn_number": 4, "refuse_draft": True,
                       "ref": f"launcher-keep-warm:{got.runs[0].run_id}"}
    assert text.splitlines() == ["Keep-warm from launcher", "check", "- tests (a1)"]
    [run] = got.runs
    assert (run.kind, run.mode, run.state, run.outcome, run.word) == (
        "keep-warm", "ask", "ended", "nudged", "subagents")
    assert run.turn_number == 5 and run.session_id == SID and run.actor == "launcher"
    assert run.job == f"proc:{me()}"
    assert events(conn, run.run_id) == ["nudge", "result", "ended"]
    nudge = store.run_events(conn, run.run_id)[0].detail
    assert nudge["agents"] == ["a1"] and nudge["case"] == "subagents"
    assert got.session.closed == 1


def test_a_teammate_is_a_signal_too(conn):
    got = ask(conn, session=FakeSession(agents=[agent("t1", kind="teammate",
                                                      state="waiting")]))
    assert got.verdict.outcome == "nudged"


@pytest.mark.parametrize("entry", [
    agent("s1", kind="shell"), agent("a1", final=True), agent("a1", state="unknown"),
    agent("a1", state="done"),
])
def test_what_is_no_subagent_signal_leaves_the_session_idle(conn, entry):
    got = ask(conn, session=FakeSession(agents=[entry]))
    assert (got.verdict.outcome, got.verdict.word) == (None, "idle")
    assert got.runs == [] and got.session.delivered == []


def test_a_director_with_an_open_hold_gets_the_basic_nudge_then_the_recheck(conn):
    store.open_hold(conn, "r1", "port-cache")
    first = ask(conn, session=FakeSession())
    assert (first.verdict.outcome, first.verdict.word) == ("nudged", "director")
    assert first.session.delivered[0][0].splitlines() == ["Keep-warm from launcher",
                                                          "reply warm"]
    second = ask(conn, session=FakeSession(facts_seq=[facts(number=5)]))
    assert second.session.delivered[0][0].splitlines() == [
        "Keep-warm from launcher", "recheck holds", "Open holds: port-cache"]
    assert second.session.delivered[0][1]["if_turn_number"] == 5


def test_the_subagent_case_comes_before_the_director_s(conn):
    store.open_hold(conn, "r1", "port-cache")
    got = ask(conn)
    assert got.verdict.word == "subagents"


def test_neither_case_is_idle_and_writes_nothing(conn):
    got = ask(conn, session=FakeSession())
    assert (got.verdict.outcome, got.verdict.word) == (None, "idle")
    assert got.runs == []


def test_agents_turns_cannot_list_leave_the_director_case(conn):
    store.open_hold(conn, "r1", "port-cache")
    session = FakeSession()
    session.entries = RuntimeError("no list")
    got = ask(conn, session=session)
    assert got.verdict.word == "director"


# -- the ask: the window and the reads before it -----------------------------------------


@pytest.mark.parametrize("age, outcome", [
    (2699.9, None), (2700.0, "nudged"), (3239.9, "nudged"), (3240.0, None),
])
def test_the_window_runs_from_900_to_360_seconds_before_expiry(conn, age, outcome):
    got = ask(conn, cache=FakeCache(age=age))
    assert got.verdict.outcome == outcome
    if outcome is None:
        assert got.verdict.word == "outside" and got.runs == []


@pytest.mark.parametrize("ttl", [300.0, 360.0])
def test_a_cache_of_six_minutes_or_less_has_no_window(conn, ttl):
    got = ask(conn, cache=FakeCache(age=0.0, ttl=ttl))
    assert (got.verdict.outcome, got.verdict.word) == (None, "no-window")
    assert got.verdict.detail == f"no window: lifetime {ttl:g} s"
    assert keepwarm.no_window(ttl) and not keepwarm.no_window(3600.0)


def test_a_cache_read_with_no_lifetime_ends_quietly(conn):
    got = ask(conn, cache=FakeCache(age=None, ttl=None, kind="unknown"))
    assert (got.verdict.outcome, got.verdict.word) == (None, "unknown")


def test_no_cache_installed_ends_quietly(conn):
    verdict = keepwarm.ask(SID, conn=conn, turns=FakeTurns(FakeSession()),
                           slots_api=FakeSlots(), cache=None, settings=SETTINGS)
    assert (verdict.outcome, verdict.word) == (None, "no-cache")


def test_the_cache_read_names_the_harness_and_newest_id(conn):
    cache = FakeCache()
    ask(conn, cache=cache)
    assert cache.reads == [("claude", SID, {})]


def test_a_launch_that_does_not_keep_warm_ends_quietly(conn):
    conn.execute("UPDATE launches SET values_json = '{}' WHERE request_id = 'r1'")
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "off")


def test_a_session_no_launch_holds_ends_quietly(conn):
    assert ask(conn, which=NEW).verdict.word == "not-launched"


def test_an_unclaimed_launch_ends_quietly(conn):
    conn.execute("UPDATE launches SET endpoint = NULL WHERE request_id = 'r1'")
    assert ask(conn).verdict.word == "unclaimed"


def test_a_session_not_live_ends_quietly(conn):
    verdict = keepwarm.ask(SID, conn=conn, turns=FakeTurns(None), slots_api=FakeSlots(),
                           cache=FakeCache(), settings=SETTINGS)
    assert (verdict.outcome, verdict.word) == (None, "not-live")


def test_an_open_turn_ends_quietly(conn):
    got = ask(conn, session=FakeSession(facts_seq=[facts(turn_open=True)],
                                        agents=[agent("a1")]))
    assert (got.verdict.outcome, got.verdict.word) == (None, "busy")
    assert got.runs == [] and got.session.delivered == []


def test_facts_that_cannot_be_read_end_quietly(conn):
    got = ask(conn, session=FakeSession(facts_seq=[RuntimeError("down")],
                                        agents=[agent("a1")]))
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")


def test_a_turn_started_after_the_insert_drops_the_run(conn):
    session = FakeSession(facts_seq=[facts(), facts(number=5)], agents=[agent("a1")])
    got = ask(conn, session=session)
    assert (got.verdict.outcome, got.verdict.word) == (None, "busy")
    assert got.runs == [] and session.delivered == []


def test_another_open_run_makes_the_ask_lose(conn):
    exit_run = store.insert_run(conn, kind="exit", mode="verb", request_id="r1",
                                session_id=SID, actor="ann", state="acting",
                                job=f"proc:{me()}")
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "run-open")
    assert [r.run_id for r in got.runs] == [exit_run.run_id]
    assert got.session.delivered == []


def test_a_stale_open_run_is_ended_job_gone_and_the_ask_goes_on(conn):
    stale = store.insert_run(conn, kind="clear", mode="expiry", request_id="r1",
                             session_id=SID, actor="launcher", state="acting",
                             job="proc:999999999@1.0", at="2020-01-01T00:00:00.000000Z")
    got = ask(conn)
    assert got.verdict.outcome == "nudged"
    old = store.get_run(conn, stale.run_id)
    assert (old.outcome, old.word) == ("error", "job-gone")


# -- the run of nudges and the cap ------------------------------------------------------


def test_the_run_of_nudges_counts_nudges_at_consecutive_turns(conn):
    nudged(conn, number=4)
    nudged(conn, number=5)
    assert len(keepwarm.run_of_nudges(conn, "r1", SID, 6)) == 2
    got = ask(conn, session=FakeSession(facts_seq=[facts(number=6)],
                                        agents=[agent("a1")]))
    assert got.session.delivered[0][0].splitlines()[1] == "last"


def test_past_the_subagent_cap_the_ask_is_blocked_capped(conn):
    for number in (4, 5, 6):
        nudged(conn, number=number, agents=["a1"])
    got = ask(conn, session=FakeSession(facts_seq=[facts(number=7)], agents=[agent("a1")]))
    assert (got.verdict.outcome, got.verdict.word) == ("blocked", "capped")
    assert got.session.delivered == []
    assert "cap is 3" in got.runs[0].detail


def test_a_repeated_capped_block_updates_one_run(conn):
    for number in (4, 5, 6):
        nudged(conn, number=number)
    session = lambda: FakeSession(facts_seq=[facts(number=7)], agents=[agent("a1")])  # noqa: E731
    first = ask(conn, session=session())
    second = ask(conn, session=session())
    assert second.verdict.run_id == first.verdict.run_id
    blocked = [r for r in second.runs if r.outcome == "blocked"]
    assert len(blocked) == 1 and blocked[0].repeats == 1


def test_the_director_cap_is_its_own(conn):
    store.open_hold(conn, "r1", "port-cache")
    for number in range(4, 14):
        nudged(conn, number=number)
    got = ask(conn, session=FakeSession(facts_seq=[facts(number=14)]))
    assert (got.verdict.outcome, got.verdict.word) == ("blocked", "capped")
    assert "director cap is 10" in got.runs[0].detail


def test_a_turn_no_nudge_opened_starts_a_new_run(conn):
    for number in (4, 5, 6):
        nudged(conn, number=number)
    assert keepwarm.run_of_nudges(conn, "r1", SID, 8) == []
    got = ask(conn, session=FakeSession(facts_seq=[facts(number=8)], agents=[agent("a1")]))
    assert got.verdict.outcome == "nudged"
    assert got.session.delivered[0][0].splitlines()[1] == "check"


def test_a_clear_s_new_session_id_starts_a_new_run_though_its_turns_restart(conn):
    for number in (4, 5, 6):
        nudged(conn, number=number)
    store.learn_session(conn, "r1", NEW, "clear")
    session = FakeSession(facts_seq=[facts(number=0, session_id=NEW)],
                          agents=[agent("a1")], session_id=NEW)
    got = ask(conn, session=session, which=NEW)
    assert got.verdict.outcome == "nudged"
    assert got.runs[0].session_id == NEW and got.runs[0].turn_number == 1


def test_a_gap_between_two_nudges_ends_the_run_at_the_gap(conn):
    nudged(conn, number=2)
    nudged(conn, number=4)
    nudged(conn, number=5)
    assert len(keepwarm.run_of_nudges(conn, "r1", SID, 6)) == 2


# -- agents dropped for the rest of a run ------------------------------------------------


def test_an_agent_a_nudge_named_that_turns_stops_listing_is_dropped_for_the_run(conn):
    first = nudged(conn, number=4, agents=["a1"])
    gone = ask(conn, session=FakeSession(facts_seq=[facts(number=5)], agents=[]))
    assert gone.verdict.word == "idle"
    assert [e.detail for e in store.run_events(conn, first.run_id)
            if e.kind == "dropped"] == [{"agents": ["a1"]}]
    back = ask(conn, session=FakeSession(facts_seq=[facts(number=5)], agents=[agent("a1")]))
    assert back.verdict.word == "idle" and back.session.delivered == []


def test_an_agent_turns_reports_unknown_is_dropped_even_when_it_runs_again(conn):
    nudged(conn, number=4, agents=["a1"])
    ask(conn, session=FakeSession(facts_seq=[facts(number=5)],
                                  agents=[agent("a1", state="unknown")]))
    got = ask(conn, session=FakeSession(facts_seq=[facts(number=5)],
                                        agents=[agent("a1"), agent("a2")]))
    assert got.verdict.outcome == "nudged"
    assert got.session.delivered[0][0].splitlines()[2:] == ["- subagent (a2)"]


def test_a_new_run_of_nudges_forgets_what_the_last_dropped(conn):
    nudged(conn, number=4, agents=["a1"])
    ask(conn, session=FakeSession(facts_seq=[facts(number=5)], agents=[]))
    got = ask(conn, session=FakeSession(facts_seq=[facts(number=7)], agents=[agent("a1")]))
    assert got.verdict.outcome == "nudged"


def test_an_agent_missing_from_an_incomplete_list_is_not_dropped(conn):
    first = nudged(conn, number=4, agents=["a1"])
    ask(conn, session=FakeSession(facts_seq=[facts(number=5)], agents=[], complete=False))
    assert "dropped" not in events(conn, first.run_id)
    got = ask(conn, session=FakeSession(facts_seq=[facts(number=5)], agents=[agent("a1")]))
    assert got.verdict.outcome == "nudged"


# -- the delivery's verdicts -------------------------------------------------------------


@pytest.mark.parametrize("reason", ["draft", "busy-turn"])
def test_a_draft_or_a_busy_turn_at_delivery_blocks_the_nudge(conn, reason):
    got = ask(conn, session=FakeSession(Value("refused", reason), agents=[agent("a1")]))
    assert (got.verdict.outcome, got.verdict.word) == ("blocked", reason)
    assert (got.runs[0].outcome, got.runs[0].word) == ("blocked", reason)


def test_a_repeated_draft_block_updates_one_run(conn):
    for _ in range(2):
        got = ask(conn, session=FakeSession(Value("refused", "draft"),
                                            agents=[agent("a1")]))
    assert len(got.runs) == 1 and got.runs[0].repeats == 1


@pytest.mark.parametrize("reason, word", [
    ("not-allowed", "not-allowed"), ("unsupported", "unsupported"), ("gone", "gone"),
    ("dropped", "failed"),
])
def test_a_refusal_that_will_not_clear_fails_the_nudge(conn, reason, word):
    got = ask(conn, session=FakeSession(Value("refused", reason), agents=[agent("a1")]))
    assert (got.verdict.outcome, got.verdict.word) == ("failed", word)
    assert got.runs[0].outcome == "failed"


@pytest.mark.parametrize("reason", ["stale-turn", "rate", "in-flight", "loading"])
def test_a_refusal_that_clears_by_itself_ends_quietly_with_no_row(conn, reason):
    got = ask(conn, session=FakeSession(Value("refused", reason), agents=[agent("a1")]))
    assert (got.verdict.outcome, got.verdict.word) == (None, reason)
    assert got.runs == []


@pytest.mark.parametrize("value, word", [
    (TimeoutError("slow"), "timeout"), (Value("lost", "unanswered"), "lost"),
])
def test_a_nudge_whose_outcome_is_unknown_is_unconfirmed(conn, value, word):
    got = ask(conn, session=FakeSession(value, agents=[agent("a1")]))
    assert (got.verdict.outcome, got.verdict.word) == ("unconfirmed", word)
    assert got.runs[0].outcome == "unconfirmed"


def test_a_turns_error_at_delivery_fails_the_nudge(conn):
    got = ask(conn, session=FakeSession(RuntimeError("socket"), agents=[agent("a1")]))
    assert (got.verdict.outcome, got.verdict.word) == ("failed", "failed")
    assert "socket" in got.runs[0].detail


def test_a_fault_after_the_insert_ends_the_run_error(conn, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("broke")

    monkeypatch.setattr(keepwarm, "delivered", boom)
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == ("error", "RuntimeError")
    assert got.runs[0].outcome == "error" and not got.runs[0].open


def test_a_run_ended_under_the_ask_does_not_nudge(conn, monkeypatch):
    real = store.advance_run

    def ended_first(c, run_id, state, **kw):
        store.end_run(c, run_id, "error", word="job-gone")
        return real(c, run_id, state, **kw)

    monkeypatch.setattr(store, "advance_run", ended_first)
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "ended-under")
    assert got.session.delivered == []


# -- holds -------------------------------------------------------------------------------


def verb(fn, conn, *args, session=None):
    return fn(*args, conn=conn, turns=FakeTurns(session or FakeSession()),
              slots_api=FakeSlots())


def test_a_hold_opens_once_and_a_second_open_changes_nothing(conn):
    first = verb(keepwarm.hold, conn, "port-cache")
    assert first.exit_code == codes.OK and first.lines == ("hold port-cache opened",)
    assert first.data["outcome"] == "opened" and first.data["request_id"] == "r1"
    again = verb(keepwarm.hold, conn, "port-cache")
    assert again.data["outcome"] == "open" and "already open" in again.lines[0]
    assert store.open_holds(conn, "r1") == ["port-cache"]
    assert conn.execute("SELECT count(*) FROM warm_holds").fetchone()[0] == 1


def test_a_hold_is_released_and_a_second_release_says_it_was_not_open(conn):
    verb(keepwarm.hold, conn, "port-cache")
    done = verb(keepwarm.release, conn, "port-cache")
    assert done.lines == ("hold port-cache released",) and done.data["outcome"] == "released"
    assert store.open_holds(conn, "r1") == []
    again = verb(keepwarm.release, conn, "port-cache")
    assert again.exit_code == codes.OK and again.data["outcome"] == "not-open"
    assert "not open" in again.lines[0]


def test_holds_lists_the_open_holds(conn):
    assert verb(keepwarm.holds, conn).lines == ("no open holds",)
    verb(keepwarm.hold, conn, "b-work")
    verb(keepwarm.hold, conn, "a-work")
    listed = verb(keepwarm.holds, conn)
    assert [h["name"] for h in listed.data["holds"]] == ["a-work", "b-work"]
    assert listed.lines[0].startswith("a-work  opened ")


@pytest.mark.parametrize("fn, args", [
    (keepwarm.hold, ("x",)), (keepwarm.release, ("x",)), (keepwarm.holds, ()),
])
def test_every_hold_verb_is_refused_while_keep_warm_is_off(conn, fn, args):
    conn.execute("UPDATE launches SET values_json = '{}' WHERE request_id = 'r1'")
    with pytest.raises(relaunch.Refused) as refused:
        verb(fn, conn, *args)
    assert refused.value.code == "off" and refused.value.exit_code == codes.REFUSED
    assert conn.execute("SELECT count(*) FROM warm_holds").fetchone()[0] == 0


@pytest.mark.parametrize("name", ["", " lead", "trail ", "x" * 129, "tab\there"])
def test_a_bad_hold_name_is_a_usage_refusal(conn, name):
    with pytest.raises(relaunch.Refused) as refused:
        verb(keepwarm.hold, conn, name)
    assert refused.value.code == "bad-name" and refused.value.exit_code == codes.USAGE


def test_a_hold_verb_outside_a_launched_session_is_refused(conn):
    with pytest.raises(target.NotLaunched):
        verb(keepwarm.hold, conn, "x", session=FakeSession(session_id=OTHER))


def test_a_hold_opened_before_a_relaunch_keeps_the_relaunched_launch_warm(conn):
    verb(keepwarm.hold, conn, "port-cache")
    conn.execute("UPDATE launches SET endpoint = NULL WHERE request_id = 'r1'")
    launched(conn, request_id="r2", session_id=NEW, relaunch_of="r1")
    session = FakeSession(session_id=NEW, facts_seq=[facts(session_id=NEW)])
    got = ask(conn, session=session, which=NEW, request_id="r2")
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "director")
    listed = verb(keepwarm.holds, conn, session=FakeSession(session_id=NEW))
    assert listed.lines[0].endswith("by launch r1")
    reopened = verb(keepwarm.hold, conn, "port-cache", session=FakeSession(session_id=NEW))
    assert reopened.data["outcome"] == "open"
    verb(keepwarm.release, conn, "port-cache", session=FakeSession(session_id=NEW))
    assert store.open_holds(conn, "r2") == [] and store.open_holds(conn, "r1") == []


def test_a_resumed_launch_holds_its_chain_s_holds(conn):
    store.open_hold(conn, "r1", "port-cache")
    launched(conn, request_id="r2", session_id=NEW, resume_of="r1", claimed=False)
    assert store.open_holds(conn, "r2") == ["port-cache"]


def test_the_api_opens_and_releases_a_hold_by_request_id(conn):
    held = api.open_hold("r1", "port-cache", conn=conn)
    assert held.new and held.hold.name == "port-cache" and held.hold.request_id == "r1"
    assert not api.open_hold("r1", "port-cache", conn=conn).new
    assert [h.name for h in api.release_hold("r1", "port-cache", conn=conn)] == ["port-cache"]
    assert api.release_hold("r1", "port-cache", conn=conn) == []


def test_the_api_refuses_a_launch_unknown_or_not_keeping_warm(conn):
    with pytest.raises(target.NotLaunched):
        api.open_hold("nope", "x", conn=conn)
    launched(conn, request_id="r2", session_id=NEW, values={"keep_warm": "off"})
    with pytest.raises(relaunch.Refused):
        api.release_hold("r2", "x", conn=conn)


def test_a_released_hold_blocks_no_clear_and_no_keep_warm(conn):
    store.open_hold(conn, "r1", "port-cache")
    store.release_hold(conn, "r1", "port-cache")
    got = ask(conn, session=FakeSession())
    assert got.verdict.word == "idle"
