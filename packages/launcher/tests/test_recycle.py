"""Recycle against fakes of turns, slots and cache: the timer's subscription, the expiry
ask's reads in order and every verdict (quiet, blocked, failed, unconfirmed, cleared,
compacted), the background shells a clear stops after an idle hour, a repeated block as
one run, the crossing handler reached directly and through usage's crossing group, and the
retired compact at the limit: no line, no run, and its job a stub."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import modules, paths, proc

from moxi_launcher import cachetimers, catalog, job, recycle, store

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
NEW = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
OTHER = "11111111-2222-4333-8444-555555555555"
SETTINGS = {"relaunch.person_s": 120, "relaunch.wait_s": 900, "records.keep_days": 90}


def me() -> str:
    ident = proc.identity(os.getpid())
    return f"{ident.pid}@{ident.started:.6f}"


class Result:
    def __init__(self, kind, reason=None, **fields):
        self.KIND, self.reason = kind, reason
        for name, value in fields.items():
            setattr(self, name, value)


def ran(new_session_id=None, shells=None):
    return Result("ran", new_session_id=new_session_id, shells=shells)


def shells(stopped=(), left=(), *, complete=True):
    return SimpleNamespace(stopped=tuple(stopped), complete=complete,
                           left=tuple(SimpleNamespace(id=i, why=w) for i, w in left))


def facts(*, turn_open=False, number=4, draft=False, stale=False):
    return SimpleNamespace(turn_open=turn_open, turn=None, turn_number=number, draft=draft,
                           stale=stale)


class FakeWatch:
    def __init__(self, facts):
        self.facts = facts
        self._closed = threading.Event()

    def __iter__(self):
        self._closed.wait(5)
        return iter(())

    def close(self):
        self._closed.set()


class FakeSession:
    def __init__(self, results=None, *, facts_seq=None, agents=(), complete=True,
                 capabilities=("clear", "compact", "stop-shells")):
        self.session_id, self.endpoint_pid, self.pid = SID, os.getpid(), os.getppid()
        self.capabilities = frozenset(capabilities)
        self.results = list(results or [ran(NEW)])
        self.facts_seq = list(facts_seq or [facts()])
        self.entries, self.complete = list(agents), complete
        self.commands, self.closed = [], 0

    def facts(self):
        value = self.facts_seq[0] if len(self.facts_seq) == 1 else self.facts_seq.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def agents(self):
        return SimpleNamespace(complete=self.complete, entries=self.entries)

    def watch(self):
        return FakeWatch(self.facts())

    def command(self, name, *, if_turn_number=None, **options):
        self.commands.append((name, if_turn_number, options))
        result = self.results[0] if len(self.results) == 1 else self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

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

    def __init__(self, pane=None):
        self.pane = pane or SimpleNamespace(pane_id="%9", pane_in_mode=False,
                                            client_activity=None, pane_activity=None)

    def supported(self):
        return SimpleNamespace(ok=True, why="")

    def show(self, slot):
        return SimpleNamespace(state="occupied", hold=None, created=1.0)

    def pane_use(self, slot):
        return self.pane

    def job(self, name):
        return None


class FakeCache:
    def __init__(self, kind="cold", age=4000.0):
        self.kind, self.age, self.reads = kind, age, []

    def read(self, harness, session_id, **options):
        self.reads.append((harness, session_id, options))
        return SimpleNamespace(kind=self.kind, age_s=self.age, ttl_s=3600)


#: An age inside the compact window of a one-hour cache.
IN = 3300.0


class WarmCache:
    """cache's read of a compact session: `kind` (warm), each read the next of `ages` (the
    last repeating), or with `last_request` (epoch seconds) the age from the read's own
    `now`; `response` its `response_s`, left off the clock with `has_response` false, as
    an older cache gives it. `on_read` is called at each read."""

    def __init__(self, *ages, ttl=3600.0, response=0.0, kind="warm", last_request=None,
                 has_response=True, on_read=None):
        self.ages = list(ages) or [IN]
        self.ttl, self.response, self.kind = ttl, response, kind
        self.last_request, self.has_response, self.on_read = last_request, has_response, on_read
        self.reads = []

    def read(self, harness, session_id, **options):
        self.reads.append((harness, session_id, options))
        if self.on_read is not None:
            self.on_read()
        if self.last_request is not None:
            age = options["now"] - self.last_request
        else:
            age = self.ages[0] if len(self.ages) == 1 else self.ages.pop(0)
        clock = SimpleNamespace(kind=self.kind, age_s=age, ttl_s=self.ttl)
        if self.has_response:
            clock.response_s = self.response
        return clock


class CountingTurns(FakeTurns):
    def __init__(self, session):
        super().__init__(session)
        self.finds = 0

    def find(self, *, session_id=None, pid=None):
        self.finds += 1
        return self.session


def compacting(conn, request_id="r1"):
    """Set the launch `request_id` to compact before expiry."""
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = ?",
                 ('{"recycle_on_expiry": "compact"}', request_id))


@pytest.fixture(autouse=True)
def recyclable_here(monkeypatch):
    """slots runs here and notes is not installed, so a slot launch in a folder has the
    launcher's own handoff folder and can recycle itself."""
    monkeypatch.setattr(catalog, "slots_here", lambda: True)
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "notes" and real(name))


@pytest.fixture
def conn(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    launched(conn)
    yield conn
    conn.close()


def launched(conn, *, request_id="r1", values=None, session_id=SID, claimed=True,
             harness="claude", target="slot", relaunch_of=None, env=None, base_env=None):
    store.insert(conn, request_id=request_id, by="ann", harness=harness, target=target,
                 base_env=base_env,
                 cwd="/work", values={"model": "opus", "recycle_on_expiry": "clear",
                                      **(values or {})},
                 argv=[harness], env=env or {}, posture="operator", session_id=session_id,
                 slot="3" if target == "slot" else None, relaunch_of=relaunch_of)
    store.set_facts(conn, request_id, pane_id="%9")
    if claimed:
        conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = ?",
                     (me(), request_id))


def ask(conn, *, session=None, cache=None, slots_api=None, which=SID, **kw):
    session = session or FakeSession()
    kw.setdefault("comms", None)
    verdict = recycle.ask_expiry(which, conn=conn, turns=FakeTurns(session),
                                 slots_api=slots_api or FakeSlots(),
                                 cache=cache or FakeCache(), settings=SETTINGS, **kw)
    return SimpleNamespace(verdict=verdict, session=session,
                           runs=store.runs(conn, request_id="r1"))


def events(conn, run_id):
    return [e.kind for e in store.run_events(conn, run_id)]


# -- the timer's subscription ------------------------------------------------------------


def test_the_timer_subscription_is_found_through_cache_s_entry_point_group():
    found = [e for e in modules.extensions("moxi.cache.timers") if e.module == "launcher"]
    assert [e.target for e in found] == ["moxi_launcher.cachetimers"]
    assert found[0].load().TIMERS is cachetimers.TIMERS


def expiry_sub():
    [sub] = [t for t in cachetimers.TIMERS if t["name"] == "launcher.recycle-on-expiry"]
    return sub


def test_the_subscription_asks_from_expiry_every_two_minutes_for_a_session_set_to_clear():
    sub = expiry_sub()
    assert sub == {"name": "launcher.recycle-on-expiry", "at": 0, "every": 120,
                   "requires_env": {"MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "clear"},
                   "run": ["moxi-launcher-job", "expiry", "{session}"]}
    assert set(sub) <= {"name", "at", "until", "every", "requires_env", "run"}


def test_the_compact_subscription_asks_in_its_window_every_minute_for_a_compact_session():
    [sub] = [t for t in cachetimers.TIMERS if t["name"] == cachetimers.COMPACT_BEFORE_EXPIRY]
    assert sub == {"name": "launcher.compact-before-expiry", "at": -360, "until": -60,
                   "every": 60,
                   "requires_env": {"MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "compact"},
                   "run": ["moxi-launcher-job", "expiry", "{session}"]}
    assert [t["name"] for t in cachetimers.TIMERS] == [
        "launcher.recycle-on-expiry", "launcher.compact-before-expiry", "launcher.keep-warm"]


def test_the_compact_subscription_s_literals_are_the_launcher_s_window():
    from moxi_launcher import keepwarm

    [sub] = [t for t in cachetimers.TIMERS if t["name"] == cachetimers.COMPACT_BEFORE_EXPIRY]
    assert -sub["at"] == recycle.COMPACT_OPEN_S == keepwarm.SHUT_S
    assert -sub["until"] == recycle.COMPACT_SHUT_S
    assert "COMPACT_OPEN_S" in recycle.__all__


def test_turns_compact_wait_is_the_one_the_stretch_rule_doubles():
    results = pytest.importorskip("moxi_turns.results")
    assert recycle.COMPACT_WAIT_S == results.COMMAND_WAIT_S["compact"]


def test_the_subscription_runs_the_launcher_s_own_console_script_by_bare_name():
    for sub in cachetimers.TIMERS:
        assert "/" not in sub["run"][0]
        found = paths.script(sub["run"][0])
        assert os.access(found, os.X_OK)


def test_the_subscription_module_imports_nothing_outside_the_standard_library():
    text = open(cachetimers.__file__, encoding="utf-8").read()
    imports = [line for line in text.splitlines() if line.startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations"]


def test_the_job_takes_the_expiry_and_compact_kinds():
    assert {"expiry", "compact"} <= set(job.KINDS)
    assert job.JOBS["compact"] is job.CompactJob


# -- the expiry ask: acts ----------------------------------------------------------------


def test_a_cold_idle_session_is_cleared_and_its_run_records_it(conn):
    got = ask(conn)
    assert got.verdict.outcome == "cleared"
    assert got.session.commands == [("clear", 4, {"stop_shells": True})]
    [run] = got.runs
    assert (run.kind, run.mode, run.state, run.outcome) == ("clear", "expiry", "ended",
                                                            "cleared")
    assert run.turn_number == 4 and run.actor == "launcher" and run.job == f"proc:{me()}"
    assert run.session_id == SID and NEW in run.detail
    assert events(conn, run.run_id) == ["clear", "result", "ended"]
    assert got.session.closed == 1


def test_a_clear_after_less_than_an_idle_hour_leaves_background_shells(conn):
    got = ask(conn, cache=FakeCache(age=3599.0))
    assert got.verdict.outcome == "cleared"
    assert got.session.commands == [("clear", 4, {})]


def test_a_clear_does_not_stop_shells_where_turns_offers_no_stop(conn):
    got = ask(conn, session=FakeSession(capabilities=("clear",)))
    assert got.session.commands == [("clear", 4, {})]


def test_a_compact_never_stops_shells(conn):
    compacting(conn)
    got = ask(conn, session=FakeSession([ran()]), cache=WarmCache())
    assert got.verdict.outcome == "compacted"
    assert got.session.commands == [("compact", 4, {})]
    assert got.runs[0].kind == "compact"


def test_shells_turns_cannot_vouch_for_are_recorded_so(conn):
    result = ran(NEW, shells(["s1"], [(None, "unlisted")], complete=False))
    got = ask(conn, session=FakeSession([result]))
    run = got.runs[0]
    assert (run.outcome, run.word) == ("cleared", "shells-unvouched")
    assert "s1" in run.detail and "unlisted" in run.detail


def test_shells_left_running_are_named(conn):
    got = ask(conn, session=FakeSession([ran(NEW, shells(["s1"], [("s2", "timeout")]))]))
    assert (got.runs[0].word, "s2 (timeout)" in got.runs[0].detail) == ("shells-left", True)


def test_shells_all_stopped_leave_no_word(conn):
    got = ask(conn, session=FakeSession([ran(NEW, shells(["s1"]))]))
    assert got.runs[0].word is None and "s1" in got.runs[0].detail


def test_a_codex_session_reads_its_cache_with_the_codex_sessions_dir(conn):
    launched(conn, request_id="c1", harness="codex", target="terminal", session_id=NEW,
             values={"recycle_on_expiry": "clear"}, env={"CODEX_HOME": "/home/x/.cx"})
    store.learn_session(conn, "c1", NEW, "sender")
    cache = FakeCache()
    session = FakeSession([ran()])
    session.session_id = NEW
    verdict = recycle.ask_expiry(NEW, conn=conn, turns=FakeTurns(session),
                                 slots_api=FakeSlots(), cache=cache, settings=SETTINGS)
    assert verdict.outcome == "cleared"
    sessions = str(Path("/home/x/.cx") / "sessions")
    assert cache.reads == [("codex", NEW, {"cache_source": sessions})]


def test_a_claude_cache_read_names_the_harness_and_newest_id(conn):
    cache = FakeCache()
    ask(conn, cache=cache)
    assert cache.reads == [("claude", SID, {})]


class RootedCache(FakeCache):
    """A cache that also says where Claude writes transcripts, by cache's rule."""

    @staticmethod
    def projects_root(env):
        home = env.get("HOME", "/users/me")
        return env.get("CLAUDE_CONFIG_DIR", f"{home}/.claude").replace("~", home) \
            + "/projects"


def test_a_claude_launch_with_its_own_config_dir_reads_its_cache_under_it(tmp_path):
    conn = store.connect(tmp_path / "rooted.db")
    try:
        launched(conn, base_env={"HOME": "/users/ann", "CLAUDE_CONFIG_DIR": "~/.seat",
                                       "CLAUDE_CODE_OAUTH_TOKEN": None})
        cache = RootedCache()
        ask(conn, cache=cache)
        assert cache.reads == [("claude", SID, {"projects_root": "/users/ann/.seat/projects"})]
    finally:
        conn.close()


def test_a_claude_launch_without_its_own_config_dir_reads_caches_own_root(conn):
    cache = RootedCache()
    ask(conn, cache=cache)
    assert cache.reads == [("claude", SID, {})]


# -- the expiry ask: ends quietly --------------------------------------------------------


@pytest.mark.parametrize("kind", ["warm", "fresh", "unknown", "no-cache"])
def test_a_cache_not_cold_ends_quietly(conn, kind):
    got = ask(conn, cache=FakeCache(kind))
    assert (got.verdict.outcome, got.verdict.word) == (None, kind)
    assert got.runs == [] and got.session.commands == []


def test_no_cache_installed_ends_quietly(conn):
    got = recycle.ask_expiry(SID, conn=conn, turns=FakeTurns(FakeSession()),
                             slots_api=FakeSlots(), cache=None, settings=SETTINGS)
    assert (got.outcome, got.word) == (None, "no-cache")


def test_a_launch_that_does_not_recycle_on_expiry_ends_quietly(conn):
    conn.execute("UPDATE launches SET values_json = '{}' WHERE request_id = 'r1'")
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "off")


def test_a_session_no_launch_holds_ends_quietly(conn):
    got = ask(conn, which=NEW)
    assert (got.verdict.outcome, got.verdict.word) == (None, "not-launched")


def test_an_unclaimed_launch_ends_quietly(conn):
    conn.execute("UPDATE launches SET endpoint = NULL WHERE request_id = 'r1'")
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "unclaimed")


def test_a_session_not_live_ends_quietly(conn):
    verdict = recycle.ask_expiry(SID, conn=conn, turns=FakeTurns(None), slots_api=FakeSlots(),
                                 cache=FakeCache(), settings=SETTINGS)
    assert (verdict.outcome, verdict.word) == (None, "not-live")


@pytest.mark.parametrize("held", [
    dict(facts_seq=[facts(turn_open=True)]),
    dict(agents=[SimpleNamespace(id="a1", kind="subagent", state="running", final=False)]),
    dict(complete=False),
])
def test_work_at_hand_ends_quietly(conn, held):
    got = ask(conn, session=FakeSession(**held))
    assert (got.verdict.outcome, got.verdict.word) == (None, "busy")
    assert got.runs == []


def test_a_shell_entry_holds_nothing(conn):
    shell = SimpleNamespace(id="b1", kind="shell", state="running", final=False)
    assert ask(conn, session=FakeSession(agents=[shell])).verdict.outcome == "cleared"


def test_an_open_run_whose_job_lives_ends_quietly_and_writes_nothing(conn):
    store.insert_run(conn, kind="exit", mode="verb", request_id="r1", session_id=SID,
                     by="ann", state="acting", job=f"proc:{me()}",
                     at="2020-01-01T00:00:00.000000Z")
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "run-open")
    assert len(got.runs) == 1 and got.session.commands == []


def test_a_stale_open_run_is_ended_job_gone_and_the_ask_goes_on(conn):
    stale = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                             session_id=SID, by="launcher", state="acting",
                             job="proc:999999999@1.0", at="2020-01-01T00:00:00.000000Z")
    got = ask(conn)
    assert got.verdict.outcome == "cleared"
    old = store.get_run(conn, stale.run_id)
    assert (old.outcome, old.word) == store.JOB_GONE


@pytest.mark.parametrize("reason", recycle.QUIET_REFUSALS)
def test_a_refusal_that_clears_by_itself_leaves_no_row(conn, reason):
    got = ask(conn, session=FakeSession([Result("refused", reason)]))
    assert (got.verdict.outcome, got.verdict.word) == (None, reason)
    assert got.runs == []
    assert conn.execute("SELECT count(*) FROM run_events").fetchone()[0] == 0


# -- the expiry ask: blocked -------------------------------------------------------------


@pytest.mark.parametrize("session, word", [
    (dict(facts_seq=[facts(draft=True)]), "draft"),
    (dict(facts_seq=[facts(stale=True)]), "unreadable"),
    (dict(facts_seq=[RuntimeError("no answer")]), "unreadable"),
])
def test_the_quiet_read_s_blocks_end_blocked_with_their_word(conn, session, word):
    got = ask(conn, session=FakeSession(**session))
    assert (got.verdict.outcome, got.verdict.word) == ("blocked", word)
    [run] = got.runs
    assert (run.outcome, run.word, run.repeats) == ("blocked", word, 0)
    assert got.session.commands == []


def test_a_person_at_the_slot_s_pane_blocks(conn):
    pane = SimpleNamespace(pane_id="%9", pane_in_mode=True, client_activity=None,
                           pane_activity=None)
    got = ask(conn, slots_api=FakeSlots(pane))
    assert (got.verdict.outcome, got.verdict.word) == ("blocked", "person")


@pytest.mark.parametrize("reason", recycle.BLOCKED_REFUSALS)
def test_turns_draft_and_busy_agents_block(conn, reason):
    got = ask(conn, session=FakeSession([Result("refused", reason)]))
    assert (got.runs[0].outcome, got.runs[0].word) == ("blocked", reason)


def test_an_open_hold_of_the_launch_chain_blocks_a_clear(conn):
    launched(conn, request_id="r0", session_id=NEW, claimed=False)
    conn.execute("UPDATE launches SET relaunch_of = 'r0' WHERE request_id = 'r1'")
    conn.execute("INSERT INTO warm_holds (request_id, name, opened_at) "
                 "VALUES ('r0', 'review', '2026-10-31T00:00:00.000000Z')")
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == ("blocked", "held-work")
    assert "review" in got.runs[0].detail and got.session.commands == []


def test_a_released_hold_does_not_block(conn):
    conn.execute("INSERT INTO warm_holds (request_id, name, opened_at, released_at) "
                 "VALUES ('r1', 'review', '2026-10-31T00:00:00Z', '2026-10-31T01:00:00Z')")
    assert ask(conn).verdict.outcome == "cleared"


def test_a_repeated_block_updates_one_run(conn):
    first = ask(conn, session=FakeSession(facts_seq=[facts(draft=True)]))
    [run] = first.runs
    time.sleep(0.002)
    again = ask(conn, session=FakeSession(facts_seq=[facts(draft=True)]))
    [same] = again.runs
    assert same.run_id == run.run_id == again.verdict.run_id
    assert same.repeats == 1 and same.created_at == run.created_at
    assert same.updated_at > run.updated_at


def test_a_block_with_another_word_is_a_new_run(conn):
    ask(conn, session=FakeSession(facts_seq=[facts(draft=True)]))
    got = ask(conn, session=FakeSession(facts_seq=[facts(stale=True)]))
    assert [r.word for r in got.runs] == ["unreadable", "draft"]


def test_a_block_after_a_run_that_acted_is_a_new_run(conn):
    ask(conn, session=FakeSession(facts_seq=[facts(draft=True)]))
    ask(conn)
    got = ask(conn, session=FakeSession(facts_seq=[facts(draft=True)]))
    assert [r.outcome for r in got.runs] == ["blocked", "cleared", "blocked"]


# -- the expiry ask: failed and unconfirmed ----------------------------------------------


@pytest.mark.parametrize("result, word", [
    (Result("refused", "not-allowed"), "not-allowed"),
    (Result("refused", "unsupported"), "unsupported"),
    (Result("refused", "gone"), "gone"),
    (Result("failed", "expired: 30 s"), "failed"),
    (RuntimeError("endpoint broke"), "failed"),
])
def test_turns_failures_end_failed_with_their_word(conn, result, word):
    got = ask(conn, session=FakeSession([result]))
    assert (got.runs[0].outcome, got.runs[0].word) == ("failed", word)


@pytest.mark.parametrize("reason", recycle.UNCONFIRMED)
def test_an_answer_that_did_not_come_is_unconfirmed(conn, reason):
    got = ask(conn, session=FakeSession([Result("failed", reason)]))
    assert (got.runs[0].outcome, got.runs[0].word) == ("unconfirmed", reason)


def test_a_run_ended_under_the_ask_does_not_act(conn):
    class Ending(FakeSession):
        @property
        def capabilities(self):
            run = store.open_run(conn, "r1")
            store.end_run(conn, run.run_id, "error", word="job-gone")
            return frozenset({"clear", "stop-shells"})

        @capabilities.setter
        def capabilities(self, value):
            pass

    got = ask(conn, session=Ending())
    assert (got.verdict.outcome, got.verdict.word) == (None, "ended-under")
    assert got.session.commands == []


def test_a_fault_after_the_run_was_written_ends_it_error(conn):
    class Broken(FakeSession):
        def command(self, name, **options):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ask(conn, session=Broken())
    [run] = store.runs(conn, request_id="r1")
    assert (run.state, run.outcome) == ("ended", "error")


# -- by hand: clear-expired and its dry run -----------------------------------------------


def test_a_dry_run_says_a_clear_would_run_and_writes_nothing(conn):
    got = ask(conn, dry_run=True)
    assert (got.verdict.outcome, got.verdict.word) == (None, "would-clear")
    assert "stopping its background shells" in got.verdict.detail
    assert got.runs == [] and got.session.commands == []


def test_a_dry_run_of_a_compact_or_a_short_idle_names_no_shells(conn):
    got = ask(conn, cache=FakeCache(age=3599.0), dry_run=True)
    assert got.verdict.word == "would-clear" and "shells" not in got.verdict.detail
    compacting(conn)
    got = ask(conn, cache=WarmCache(), dry_run=True)
    assert got.verdict.word == "would-compact" and "shells" not in got.verdict.detail


def test_a_dry_run_says_blocked_with_its_word_and_writes_no_run(conn):
    got = ask(conn, session=FakeSession(facts_seq=[facts(draft=True)]), dry_run=True)
    assert (got.verdict.outcome, got.verdict.word, got.verdict.run_id) == (
        "blocked", "draft", None)
    assert got.runs == []


def test_a_dry_run_ends_quietly_where_a_run_would(conn):
    got = ask(conn, cache=FakeCache(kind="warm"), dry_run=True)
    assert (got.verdict.outcome, got.verdict.word) == (None, "warm")


def sweep(conn, session, *, which=None, **kw):
    return recycle.clear_expired(which, conn=conn, turns=FakeTurns(session),
                                 slots_api=FakeSlots(), cache=FakeCache(),
                                 settings=SETTINGS, **kw)


def test_clear_expired_asks_every_live_launch_that_recycles_on_expiry(conn):
    launched(conn, request_id="r2", session_id=NEW, values={"recycle_on_expiry": "off"})
    launched(conn, request_id="r3", session_id=OTHER, claimed=False)
    session = FakeSession()
    got = sweep(conn, session)
    assert got.exit_code == 0
    assert [(s["request_id"], s["session_id"], s["outcome"]) for s in got.data["sessions"]] \
        == [("r1", SID, "cleared")]
    assert got.data["dry_run"] is False and session.commands == [("clear", 4,
                                                                  {"stop_shells": True})]
    assert got.lines == (f"session {SID} (launch r1): cleared: the session cleared; its "
                         f"new session id is {NEW}",)


def test_clear_expired_dry_run_prints_each_verdict_and_writes_nothing(conn):
    session = FakeSession()
    got = sweep(conn, session, dry_run=True)
    assert got.exit_code == 0 and got.data["outcome"] == "dry-run"
    [line] = got.lines
    assert line.startswith(f"session {SID} (launch r1): nothing done (would-clear): a run "
                           "would clear the session now")
    assert store.runs(conn) == [] and session.commands == []


def test_clear_expired_exits_1_on_a_verdict_someone_should_see(conn):
    got = sweep(conn, FakeSession(facts_seq=[facts(draft=True)]), dry_run=True)
    assert got.exit_code == 1 and got.data["sessions"][0]["word"] == "draft"


def test_clear_expired_with_no_launch_that_recycles_says_so(conn):
    conn.execute("UPDATE launches SET values_json = '{}' WHERE request_id = 'r1'")
    got = sweep(conn, FakeSession())
    assert (got.exit_code, got.lines, got.data["sessions"]) == (
        0, ("no live launch recycles on expiry",), [])


def test_clear_expired_with_a_session_asks_only_the_launch_last_known_by_it(conn):
    launched(conn, request_id="r2", session_id=NEW, values={"recycle_on_expiry": "clear"})
    got = sweep(conn, FakeSession(), which=NEW, dry_run=True)
    assert [(s["request_id"], s["session_id"]) for s in got.data["sessions"]] == [
        ("r2", NEW)]


def test_clear_expired_with_a_session_no_launch_has_says_so_and_exits_0(conn):
    session = FakeSession()
    got = sweep(conn, session, which=OTHER)
    assert (got.exit_code, got.lines, got.data["sessions"]) == (
        0, (f"no live launch that recycles on expiry has current session {OTHER}",), [])
    assert store.runs(conn) == [] and session.commands == []


# -- compact before expiry ---------------------------------------------------------------


def ask_compact(conn, *, session=None, cache=None, turns=None, comms=None, **kw):
    """The ask of a compact launch, with no comms unless given (nobody directed)."""
    compacting(conn)
    session = session or FakeSession([ran()])
    turns = turns or FakeTurns(session)
    verdict = recycle.ask_expiry(SID, conn=conn, turns=turns, slots_api=FakeSlots(),
                                 cache=cache or WarmCache(), comms=comms, settings=SETTINGS,
                                 **kw)
    return SimpleNamespace(verdict=verdict, session=session, turns=turns,
                           runs=store.runs(conn, request_id="r1"))


def test_a_compact_in_its_window_compacts_and_its_run_records_it(conn):
    got = ask_compact(conn)
    assert got.verdict.outcome == "compacted"
    assert got.session.commands == [("compact", 4, {})]
    [run] = got.runs
    assert (run.kind, run.mode, run.outcome, run.turn_number) == (
        "compact", "expiry", "compacted", 4)
    assert events(conn, run.run_id) == ["compact", "result", "ended"]
    assert got.session.closed == 1


@pytest.mark.parametrize("age, response, word", [
    (3239.0, 0.0, "outside"),         # TTL - 361
    (3240.0, 0.0, None),              # TTL - 360
    (3539.0, 0.0, None),              # TTL - 61
    (3540.0, 0.0, "outside"),         # TTL - 60
    (3400.0, 130.0, None),            # TTL - 200, ending 70 s before the expiry
    (3400.0, 150.0, "outside"),       # TTL - 200, ending 50 s before it
    (3239.0, None, "outside"),        # TTL - 361 with any response_s
    (3239.0, 500.0, "outside"),
])
def test_the_window_opens_360_s_and_shuts_60_s_before_expiry_on_age_and_response(
        conn, age, response, word):
    got = ask_compact(conn, cache=WarmCache(age, response=response))
    if word is None:
        assert got.verdict.outcome == "compacted"
    else:
        assert (got.verdict.outcome, got.verdict.word) == (None, word)
        assert got.runs == [] and got.session.commands == []


def test_a_window_read_with_no_response_s_ends_quietly_unreadable(conn):
    got = ask_compact(conn, cache=WarmCache(response=None))
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")
    assert "response_s" in got.verdict.detail and got.runs == []


def test_an_older_cache_whose_clock_has_no_response_s_ends_quietly_unreadable(conn):
    got = ask_compact(conn, cache=WarmCache(has_response=False))
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")
    assert got.turns.session.commands == []


@pytest.mark.parametrize("kind", ["cold", "fresh", "unknown", "no-cache"])
def test_a_cache_not_warm_never_compacts(conn, kind):
    turns = CountingTurns(FakeSession([ran()]))
    got = ask_compact(conn, cache=WarmCache(kind=kind), turns=turns)
    assert (got.verdict.outcome, got.verdict.word) == (None, kind)
    assert got.runs == [] and turns.finds == 0


def test_no_cache_installed_never_compacts(conn):
    compacting(conn)
    verdict = recycle.ask_expiry(SID, conn=conn, turns=FakeTurns(FakeSession()),
                                 slots_api=FakeSlots(), cache=None, settings=SETTINGS)
    assert (verdict.outcome, verdict.word) == (None, "no-cache")


@pytest.mark.parametrize("ttl", [300.0, 360.0])
def test_a_lifetime_of_six_minutes_or_less_has_no_window_and_no_other_read(conn, ttl):
    turns = CountingTurns(FakeSession([ran()]))
    got = ask_compact(conn, cache=WarmCache(ttl / 2, ttl=ttl), turns=turns)
    assert (got.verdict.outcome, got.verdict.word) == (None, "no-window")
    assert turns.finds == 0 and got.runs == []
    assert conn.execute("SELECT count(*) FROM run_events").fetchone()[0] == 0


def test_an_age_outside_the_window_makes_no_target_find(conn):
    turns = CountingTurns(FakeSession([ran()]))
    got = ask_compact(conn, cache=WarmCache(3000.0), turns=turns)
    assert got.verdict.word == "outside" and turns.finds == 0


def test_the_window_is_read_on_the_session_id_asked_about_before_the_session(conn):
    cache = WarmCache(3000.0)
    ask_compact(conn, cache=cache)
    [(harness, sid, options)] = cache.reads
    assert (harness, sid) == ("claude", SID) and isinstance(options["now"], float)


class Arriving(FakeSession):
    """A session whose subagent starts between the quiet read and the signal."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.listed = 0

    def agents(self):
        self.listed += 1
        entries = [] if self.listed == 1 else [
            SimpleNamespace(id="a1", kind="subagent", state="running", final=False,
                            description="tests")]
        return SimpleNamespace(complete=True, entries=entries)


def test_a_live_subagent_the_signal_finds_ends_quietly_at_work(conn):
    got = ask_compact(conn, session=Arriving([ran()]))
    assert (got.verdict.outcome, got.verdict.word) == (None, "at-work")
    assert "1 subagents at work" in got.verdict.detail
    assert got.runs == [] and got.session.commands == []


def test_a_live_subagent_at_the_quiet_read_ends_quietly_busy(conn):
    live = SimpleNamespace(id="a1", kind="subagent", state="running", final=False)
    got = ask_compact(conn, session=FakeSession([ran()], agents=[live]))
    assert (got.verdict.outcome, got.verdict.word) == (None, "busy")


def test_an_open_hold_stops_a_compact_whether_or_not_keep_warm_is_on(conn):
    launched(conn, request_id="r0", session_id=NEW, claimed=False)
    conn.execute("UPDATE launches SET relaunch_of = 'r0' WHERE request_id = 'r1'")
    conn.execute("INSERT INTO warm_holds (request_id, name, opened_at) "
                 "VALUES ('r0', 'review', '2026-10-31T00:00:00.000000Z')")
    got = ask_compact(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "at-work")
    assert "open holds: review" in got.verdict.detail
    assert got.runs == [] and got.session.commands == []


def test_a_directed_agent_at_work_ends_quietly_at_work(conn, monkeypatch):
    from moxi_launcher import waits

    asked = []
    monkeypatch.setattr(waits, "directed", lambda reads, sid: asked.append(
        (reads.comms, reads.turns, sid)) or waits.Directed(("coder@proj-a",)))
    comms = object()
    got = ask_compact(conn, comms=comms)
    assert (got.verdict.outcome, got.verdict.word) == (None, "at-work")
    assert "1 directed agents at work" in got.verdict.detail
    assert asked == [(comms, got.turns, SID)]


@pytest.mark.parametrize("short", [dict(late=True), dict(raised=True), dict(unlisted=True)])
def test_a_join_not_read_whole_ends_quietly_unreadable(conn, monkeypatch, short):
    from moxi_launcher import waits

    monkeypatch.setattr(waits, "directed", lambda reads, sid: waits.Directed(**short))
    got = ask_compact(conn, comms=object())
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")
    assert got.runs == [] and got.session.commands == []


def test_a_clock_past_the_shut_at_the_final_read_drops_the_run(conn):
    """The window read at TTL - 200; a slow join; the final read at TTL - 55."""
    got = ask_compact(conn, cache=WarmCache(3400.0, 3545.0))
    assert (got.verdict.outcome, got.verdict.word) == (None, "outside")
    assert got.runs == [] and got.session.commands == []
    assert conn.execute("SELECT count(*) FROM run_events").fetchone()[0] == 0


def test_the_final_read_is_made_after_the_run_is_written(conn):
    seen = []
    cache = WarmCache(on_read=lambda: seen.append(store.open_run(conn, "r1")))
    got = ask_compact(conn, cache=cache)
    assert got.verdict.outcome == "compacted"
    assert seen[0] is None and seen[1] is not None and len(seen) == 2
    assert (seen[1].kind, seen[1].state) == ("compact", "acting")


def test_the_final_read_is_of_the_session_id_target_find_found(conn):
    store.learn_session(conn, "r1", NEW, "sender")
    moved = FakeSession([ran()])
    moved.session_id = NEW
    cache = WarmCache()
    assert ask_compact(conn, session=moved, cache=cache).verdict.outcome == "compacted"
    assert [sid for _, sid, _ in cache.reads] == [SID, NEW]


def test_a_draft_at_the_quiet_read_ends_blocked_and_reads_no_signal(conn, monkeypatch):
    from moxi_launcher import waits

    monkeypatch.setattr(waits, "directed", lambda *a: pytest.fail("joined"))
    got = ask_compact(conn, session=FakeSession(facts_seq=[facts(draft=True)]),
                      comms=object())
    assert (got.verdict.outcome, got.verdict.word) == ("blocked", "draft")
    [run] = got.runs
    assert (run.kind, run.outcome) == ("compact", "blocked")


def test_a_compact_dry_run_makes_the_final_read_and_writes_nothing(conn):
    cache = WarmCache()
    got = ask_compact(conn, cache=cache, dry_run=True)
    assert (got.verdict.outcome, got.verdict.word) == (None, "would-compact")
    assert len(cache.reads) == 2 and got.runs == [] and got.session.commands == []


def test_a_compact_ask_while_a_keep_warm_run_is_open_ends_run_open(conn):
    store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1", session_id=SID,
                     by="launcher", state="acting", job=f"proc:{me()}")
    got = ask_compact(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "run-open")
    assert got.session.commands == []


def test_a_compact_whose_quiet_read_came_before_a_nudge_is_refused_stale_turn(conn):
    """The quiet read saw turn 4; a nudge opened turn 5 before the command went, so
    turns refuses the command sent at turn 4."""
    got = ask_compact(conn, session=FakeSession([Result("refused", "stale-turn")]))
    assert got.session.commands == [("compact", 4, {})]
    assert (got.verdict.outcome, got.verdict.word) == (None, "stale-turn")
    assert got.runs == []


def test_a_clear_row_in_the_compact_window_ends_quietly(conn):
    got = ask(conn, cache=WarmCache())
    assert (got.verdict.outcome, got.verdict.word) == (None, "warm")
    assert got.runs == []


def test_a_compact_row_after_expiry_ends_quietly_cold(conn):
    got = ask_compact(conn, cache=FakeCache())
    assert (got.verdict.outcome, got.verdict.word) == (None, "cold")
    assert got.runs == []


def test_clear_expired_asks_a_compact_launch_its_own_ask(conn):
    compacting(conn)
    session = FakeSession([ran()])
    outside = recycle.clear_expired(conn=conn, turns=FakeTurns(session),
                                    slots_api=FakeSlots(), cache=WarmCache(3000.0),
                                    comms=None, settings=SETTINGS)
    assert [s["word"] for s in outside.data["sessions"]] == ["outside"]
    inside = recycle.clear_expired(conn=conn, turns=FakeTurns(session),
                                   slots_api=FakeSlots(), cache=WarmCache(), comms=None,
                                   settings=SETTINGS, dry_run=True)
    assert [s["word"] for s in inside.data["sessions"]] == ["would-compact"]
    assert store.runs(conn) == [] and session.commands == []


# -- once per idle stretch ----------------------------------------------------------------


def at(epoch):
    from datetime import datetime, timezone

    return store.stamp(datetime.fromtimestamp(epoch, timezone.utc))


def compact_ended(conn, outcome, *, ended, sent=None, created=None, mode="expiry",
                  request_id="r1", word=None):
    """A compact run of the launch `request_id` that ended `outcome` at `ended` (epoch
    seconds), its `compact` event at `sent` when given."""
    created = created if created is not None else (sent if sent is not None else ended) - 5
    run = store.insert_run(conn, kind="compact", mode=mode, request_id=request_id,
                           session_id=SID, by="launcher", state="acting",
                           job=f"proc:{me()}", at=at(created))
    if sent is not None:
        store.add_run_event(conn, run.run_id, "compact", {"turn_number": 4}, at=at(sent))
    if outcome == "blocked":
        store.end_blocked(conn, run.run_id, word or "draft", at=at(ended))
    else:
        store.end_run(conn, run.run_id, outcome, word=word, at=at(ended))
    return run


def stretch_ask(conn, last_request, **kw):
    turns = CountingTurns(FakeSession([ran()]))
    got = ask_compact(conn, cache=WarmCache(last_request=last_request), turns=turns, **kw)
    return got


@pytest.mark.parametrize("between", [
    dict(outcome="blocked"),
    dict(outcome="failed", word="not-allowed"),
    dict(outcome="error", word="job-gone"),
])
def test_a_run_that_compacted_nothing_does_not_hide_the_compaction_before_it(conn,
                                                                               between):
    """A compaction, then a run that compacted nothing, then an ask with no request since
    the compaction: compacted-already at the window read, as v8 compacted again."""
    last = time.time() - IN
    done = compact_ended(conn, "compacted", sent=last + 2, ended=last + 10)
    compact_ended(conn, between["outcome"], ended=last + 60, word=between.get("word"))
    got = stretch_ask(conn, last)
    assert (got.verdict.outcome, got.verdict.word) == (None, "compacted-already")
    assert done.run_id in got.verdict.detail
    assert got.turns.finds == 0 and got.session.commands == []


def test_a_last_request_after_a_compaction_s_end_starts_a_new_stretch(conn):
    last = time.time() - IN
    compact_ended(conn, "compacted", sent=last - 20, ended=last - 10)
    assert stretch_ask(conn, last).verdict.outcome == "compacted"


def test_a_last_request_just_before_a_compaction_s_end_is_the_compaction_s_own(conn):
    last = time.time() - IN
    compact_ended(conn, "compacted", sent=last - 5, ended=last + 0.001)
    assert stretch_ask(conn, last).verdict.word == "compacted-already"


@pytest.mark.parametrize("outcome, word", [("unconfirmed", "timeout"),
                                           ("error", "job-gone")])
@pytest.mark.parametrize("after_s, compacts", [(600.0, False), (700.0, True)])
def test_a_compaction_of_unknown_outcome_is_bound_twice_turns_wait_past_its_event(
        conn, outcome, word, after_s, compacts):
    last = time.time() - IN
    compact_ended(conn, outcome, sent=last - after_s, ended=last - after_s + 330, word=word)
    got = stretch_ask(conn, last)
    if compacts:
        assert got.verdict.outcome == "compacted"
    else:
        assert got.verdict.word == "compacted-already"


def test_an_error_before_the_compact_event_compacted_nothing(conn):
    last = time.time() - IN
    compact_ended(conn, "error", ended=last + 5, word="job-gone")
    assert stretch_ask(conn, last).verdict.outcome == "compacted"


def test_a_compaction_at_the_limit_written_before_its_retirement_counts(conn):
    last = time.time() - IN
    compact_ended(conn, "compacted", sent=last - 5, ended=last + 5, mode="limit")
    assert stretch_ask(conn, last).verdict.word == "compacted-already"


def test_a_compaction_on_the_chain_s_older_launch_counts(conn):
    launched(conn, request_id="r0", session_id=NEW, claimed=False)
    conn.execute("UPDATE launches SET relaunch_of = 'r0' WHERE request_id = 'r1'")
    last = time.time() - IN
    compact_ended(conn, "compacted", sent=last - 5, ended=last + 5, request_id="r0")
    assert stretch_ask(conn, last).verdict.word == "compacted-already"


def test_a_compaction_on_another_launch_does_not_count(conn):
    launched(conn, request_id="r2", session_id=NEW, claimed=False)
    last = time.time() - IN
    compact_ended(conn, "compacted", sent=last - 5, ended=last + 5, request_id="r2")
    assert stretch_ask(conn, last).verdict.outcome == "compacted"


def test_the_stretch_test_is_made_again_at_the_final_read(conn):
    """A compaction that ends while the ask is at its slow reads is seen at the final
    read, and the ask's own run is dropped."""
    last = time.time() - IN

    class Compacted(FakeSession):
        def agents(self):
            listed = super().agents()
            if not store.runs(conn, request_id="r0"):
                compact_ended(conn, "compacted", sent=last - 5, ended=last + 5,
                              request_id="r0")
            return listed

    launched(conn, request_id="r0", session_id=NEW, claimed=False)
    conn.execute("UPDATE launches SET relaunch_of = 'r0' WHERE request_id = 'r1'")
    turns = CountingTurns(Compacted([ran()]))
    got = ask_compact(conn, cache=WarmCache(last_request=last), turns=turns)
    assert (got.verdict.outcome, got.verdict.word) == (None, "compacted-already")
    assert got.runs == [] and got.session.commands == []


def test_a_dry_run_in_a_new_stretch_would_compact(conn):
    last = time.time() - IN
    compact_ended(conn, "compacted", sent=last - 20, ended=last - 10)
    got = stretch_ask(conn, last, dry_run=True)
    assert got.verdict.word == "would-compact"


def test_the_stretch_fact_is_the_newest_compaction_and_none_without_one(conn):
    assert recycle.stretch_fact(conn, "r1") is None
    last = time.time() - IN
    compact_ended(conn, "compacted", sent=last - 100, ended=last - 90)
    newer = compact_ended(conn, "unconfirmed", sent=last - 50, ended=last - 20,
                          word="timeout")
    compact_ended(conn, "blocked", ended=last - 10)
    fact = recycle.stretch_fact(conn, "r1")
    assert (fact.run_id, fact.outcome) == (newer.run_id, "unconfirmed")
    assert fact.bound == pytest.approx(last - 50 + 660, abs=0.001)


# -- the crossing handler ----------------------------------------------------------------


def crossing(mark="1x", at="2026-10-31T10:00:00Z", session_id=SID):
    return SimpleNamespace(harness="claude", session_id=session_id, mark=mark, tokens=1,
                           threshold=1, model="opus", at=at)


class Spawned:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, argv, **options):
        self.calls.append((argv, options))
        if self.error is not None:
            raise self.error
        return os.getpid()


@pytest.fixture
def script(monkeypatch):
    monkeypatch.setattr(paths, "script", lambda name: f"/venv/bin/{name}")


def limit(conn, value):
    """Set r1 to recycle at its limit: a relaunch launch told at its warn_at, a `compact`
    one recorded with its limit_at, as a row from before those were retired."""
    mark = "warn_at" if value == "relaunch" else "limit_at"
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 (f'{{"recycle_at_limit": "{value}", "{mark}": "1x"}}',))


def test_a_relaunch_crossing_tells_the_agent_and_records_that_it_was_told(conn):
    limit(conn, "relaunch")
    line = recycle.crossing_line(conn, crossing(), pid=os.getpid())
    assert line == recycle.RELAUNCH_LINE and "relaunch --self --handoff" in line
    [told] = [e for e in store.events(conn, "r1") if e.kind == "recycle-told"]
    assert told.detail["crossing_key"] == f"{SID}|1x|2026-10-31T10:00:00Z"
    assert store.runs(conn, request_id="r1") == []


def test_a_relaunch_launch_is_told_at_each_rise_in_one_session_id(conn):
    limit(conn, "relaunch")
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) == recycle.RELAUNCH_LINE
    # A compaction brought the context below the mark; it climbed back past it.
    assert recycle.crossing_line(conn, crossing(at="2026-10-31T11:00:00Z"),
                                 pid=os.getpid()) == recycle.RELAUNCH_LINE
    told = [e for e in store.events(conn, "r1") if e.kind == "recycle-told"]
    assert [(e.session_id, e.detail["crossing_key"]) for e in told] == [
        (SID, f"{SID}|1x|2026-10-31T10:00:00Z"), (SID, f"{SID}|1x|2026-10-31T11:00:00Z")]


def test_a_retried_call_on_one_crossing_is_told_once(conn):
    limit(conn, "relaunch")
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) == recycle.RELAUNCH_LINE
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) is None
    assert len([e for e in store.events(conn, "r1") if e.kind == "recycle-told"]) == 1


def test_a_crossing_on_a_launch_held_by_a_gone_endpoint_moves_the_claim_and_is_told(
        conn, monkeypatch):
    # The endpoint was replaced on a reload and the session took an id the launch never
    # learned; the new endpoint (this process, a child of the launch's claude) had no
    # session event to claim at. The crossing claims, learns the id and tells it.
    from moxi_launcher import listeners

    gone = "4194999@1761728401.500000"
    ident = proc.identity(os.getppid())
    conn.execute("UPDATE launches SET endpoint = ?, pid = ?, pid_started = ? "
                 "WHERE request_id = 'r1'", (gone, ident.pid, ident.started))
    limit(conn, "relaunch")
    monkeypatch.setenv("MOXI_LAUNCHER_REQUEST", "r1")
    line = recycle.crossing_line(conn, crossing(session_id=NEW), pid=os.getpid())
    assert line == recycle.RELAUNCH_LINE
    row = store.get(conn, "r1")
    assert row.endpoint == listeners.endpoint_id()
    [moved] = [e for e in store.events(conn, "r1") if e.kind == store.RECLAIMED]
    assert moved.detail == {"endpoint": row.endpoint, "was": gone, "by": "parent"}
    assert (NEW, "load") in store.session_ids(conn, "r1")
    assert store.resolve(conn, NEW).request_id == "r1"


def test_a_crossing_of_a_nested_session_takes_nothing(conn, monkeypatch):
    # It carries the launch's stamp, but its endpoint is no child of the launch's claude.
    gone = "4194999@1761728401.500000"
    ident = proc.identity(os.getpid())
    conn.execute("UPDATE launches SET endpoint = ?, pid = ?, pid_started = ? "
                 "WHERE request_id = 'r1'", (gone, ident.pid, ident.started))
    limit(conn, "relaunch")
    monkeypatch.setenv("MOXI_LAUNCHER_REQUEST", "r1")
    assert recycle.crossing_line(conn, crossing(session_id=NEW), pid=os.getpid()) is None
    assert store.get(conn, "r1").endpoint == gone
    assert NEW not in [sid for sid, _ in store.session_ids(conn, "r1")]


def test_a_crossing_of_a_launch_off_unknown_or_claimed_elsewhere_is_none(conn):
    limit(conn, "off")
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) is None
    limit(conn, "relaunch")
    assert recycle.crossing_line(conn, crossing(session_id=NEW), pid=os.getpid()) is None
    assert recycle.crossing_line(conn, crossing(), pid=os.getppid()) is None


@pytest.mark.parametrize("values", [
    '{"recycle_at_limit": "relaunch"}',
    '{"recycle_at_limit": "relaunch", "warn_at": "off"}',
    '{"recycle_at_limit": "off", "warn_at": "1x", "sources": {"recycle_at_limit": "typed"}}'])
def test_a_crossing_of_a_launch_that_is_not_told_gets_no_line(conn, values):
    # The handler tells exactly the launches whose self arm would be accepted and that
    # warn at a mark; a relaunch launch with no mark is armed but never told.
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'", (values,))
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) is None
    assert [e for e in store.events(conn, "r1") if e.kind == "recycle-told"] == []


def test_a_relaunch_crossing_where_slots_do_not_run_gets_no_line(conn, monkeypatch):
    monkeypatch.setattr(catalog, "slots_here", lambda: False)
    limit(conn, "relaunch")
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) is None


def test_an_older_scratch_row_set_to_relaunch_gets_no_line(conn, script):
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 ('{"scratch": true, "recycle_at_limit": "relaunch", "limit_at": "1x"}',))
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) is None
    assert [e for e in store.events(conn, "r1") if e.kind == "recycle-told"] == []


def test_a_compact_row_from_before_its_retirement_gets_no_line_and_writes_nothing(
        conn, script, monkeypatch):
    monkeypatch.setenv("MOXI_LAUNCHER_REQUEST", "r1")
    limit(conn, "compact")
    spawn = Spawned()
    monkeypatch.setattr(proc, "spawn", spawn)
    before = len(store.events(conn, "r1"))
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) is None
    assert recycle.crossing_line(conn, crossing(at="2026-10-31T11:00:00Z"),
                                 pid=os.getpid()) is None
    assert store.runs(conn, request_id="r1") == [] and spawn.calls == []
    assert [e.kind for e in store.events(conn, "r1")][before:] == []
    assert conn.execute("SELECT COUNT(*) FROM pending_compacts").fetchone() == (0,)


def test_the_crossing_handler_is_found_through_usage_s_entry_point_group():
    found = [e for e in modules.extensions("moxi.usage.crossings") if e.module == "launcher"]
    assert [e.target for e in found] == ["moxi_launcher.recycle:on_crossing"]
    assert found[0].load() is recycle.on_crossing


@pytest.fixture
def usage_group(tmp_path, monkeypatch):
    """usage's crossing group read afresh, over a launch log in its default place under
    a home of the test's own, holding launch r1 claimed by this process."""
    mark = pytest.importorskip("moxi_usage.mark", reason="usage is not installed")
    monkeypatch.delenv("MOXI_LAUNCHER_HOME", raising=False)
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "home"))
    assert store.path().is_relative_to(tmp_path)
    conn = store.connect()
    launched(conn)
    mark.reset()
    yield SimpleNamespace(mark=mark, conn=conn)
    mark.reset()
    conn.close()


def through_usage(group, at):
    crossing = group.mark.Crossing("claude", SID, "1x", 900, 800, "opus", at)
    return group.mark.run_handlers(crossing, wait=5.0)


def test_the_relaunch_line_says_to_hand_off_stop_shells_and_relaunch(conn):
    limit(conn, "relaunch")
    line = recycle.crossing_line(conn, crossing(mark="1.5x"), pid=os.getpid())
    assert line == (
        "This launch lets you recycle yourself. Only if you are mid-work with more to do: at "
        "your next pause point (let running subagents finish, start nothing new), write a "
        "handoff with the handoff skill, stop your background shells, then run "
        "`moxi launcher relaunch --self --handoff <file>` as your last act and end your "
        "turn. If your work is nearly done, finish it and stop as usual; don't recycle at "
        "the end of a task.")


def test_a_relaunch_crossing_reaches_the_launcher_through_usage_s_group_once_a_rise(
        usage_group):
    limit(usage_group.conn, "relaunch")
    assert recycle.RELAUNCH_LINE in through_usage(usage_group, 1761904800.0)
    assert recycle.RELAUNCH_LINE not in through_usage(usage_group, 1761904800.0)
    assert recycle.RELAUNCH_LINE in through_usage(usage_group, 1761908400.0)
    told = [e for e in store.events(usage_group.conn, "r1") if e.kind == "recycle-told"]
    assert len(told) == 2


def test_a_compact_row_reached_through_usage_s_group_gets_no_line_and_no_run(
        usage_group, script, monkeypatch):
    limit(usage_group.conn, "compact")
    spawn = Spawned()
    monkeypatch.setattr(proc, "spawn", spawn)
    assert recycle.RELAUNCH_LINE not in through_usage(usage_group, 1761904800.0)
    assert store.runs(usage_group.conn, request_id="r1") == [] and spawn.calls == []


def test_the_crossing_handler_gives_up_on_a_locked_store_within_its_bound(conn, tmp_path,
                                                                         monkeypatch):
    limit(conn, "compact")
    monkeypatch.setattr(store, "path", lambda env=None: tmp_path / "launcher.db")
    lock_conn = store.connect(tmp_path / "launcher.db")
    lock_conn.execute("BEGIN IMMEDIATE")
    try:
        began = time.monotonic()
        assert recycle.on_crossing(crossing()) is None
        assert time.monotonic() - began < 0.5
    finally:
        lock_conn.execute("ROLLBACK")
        lock_conn.close()


def test_a_relaunch_crossing_keeps_its_line_when_the_store_is_locked(conn, tmp_path,
                                                                    monkeypatch):
    limit(conn, "relaunch")
    monkeypatch.setattr(store, "path", lambda env=None: tmp_path / "launcher.db")
    lock_conn = store.connect(tmp_path / "launcher.db")
    lock_conn.execute("BEGIN IMMEDIATE")
    try:
        assert recycle.on_crossing(crossing()) == recycle.RELAUNCH_LINE
    finally:
        lock_conn.execute("ROLLBACK")
        lock_conn.close()


def test_the_crossing_handler_without_a_launch_log_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "path", lambda env=None: tmp_path / "none" / "launcher.db")
    assert recycle.on_crossing(crossing()) is None


# -- the retired compact at the limit ----------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def compact_run(conn, job_=None, state="armed"):
    """A compact run of mode `limit`, as a launcher process from before the retirement
    writes one at a crossing."""
    return store.insert_run(conn, kind="compact", mode="limit", request_id="r1",
                            session_id=SID, by="launcher", crossing_key=f"{SID}|1x|t",
                            job=job_, slot="3", pane_id="%9", state=state)


def run_job(conn, run, session, kind=None):
    clock = Clock()
    runner = (kind or job.CompactJob)(conn, run.run_id, turns=FakeTurns(session),
                                      slots_api=FakeSlots(), comms=None,
                                      settings=dict(SETTINGS), clock=clock,
                                      sleep=clock.sleep)
    outcome = runner.go()
    return SimpleNamespace(outcome=outcome, run=store.get_run(conn, run.run_id),
                           events=events(conn, run.run_id), clock=clock)


def test_the_compact_job_ends_its_run_retired_and_sends_nothing_to_turns(conn):
    session = FakeSession([ran()])
    got = run_job(conn, compact_run(conn, "proc:1@1.0"), session)
    assert got.outcome == "failed"
    assert (got.run.outcome, got.run.word, got.run.detail) == (
        "failed", "retired", "the launcher no longer compacts a session at a context limit")
    assert session.commands == [] and "compact" not in got.events


def test_the_compact_job_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(job, "run", lambda kind, arg, signals: "failed")
    assert job.main(["compact", "x1"]) == 0
    assert capsys.readouterr().out.startswith("run x1: failed")


class PreRetirementCompactJob(job.RelaunchJob):
    """The steps of the compact job a launcher process started before the retirement still
    runs from memory: its own prune (which ended nothing then), a wait during which
    another launcher process prunes, then `guard` right before turns' `compact`."""

    kind = "compact"

    def _prune(self):
        pass

    def steps(self):
        self.target = self._find()
        store.prune(self.conn, 90)
        self.guard()
        self.target.session.command("compact", if_turn_number=4)
        self.end("compacted")


def test_a_waiting_compact_job_whose_run_prune_ended_stops_at_its_guard(conn):
    session = FakeSession([ran()])
    got = run_job(conn, compact_run(conn, "proc:1@1.0", state="waiting"), session,
                  PreRetirementCompactJob)
    assert (got.outcome, got.run.word) == ("failed", "retired")
    assert session.commands == []


# -- the job's command line and the chain ------------------------------------------------


@pytest.mark.parametrize("verdict, code", [
    (recycle.Verdict(None, "warm"), 0), (recycle.Verdict("cleared"), 0),
    (recycle.Verdict("blocked", "draft"), 1), (recycle.Verdict("failed", "gone"), 1),
])
def test_an_expiry_ask_exits_zero_unless_it_ended_blocked_or_failed(monkeypatch, capsys,
                                                                   verdict, code):
    asked = []
    monkeypatch.setattr(recycle, "ask_expiry", lambda sid: asked.append(sid) or verdict)
    assert job.main(["expiry", SID]) == code
    assert asked == [SID] and capsys.readouterr().out.startswith(f"session {SID}: ")


def test_a_launch_chain_follows_relaunches_and_resumes_back(conn):
    launched(conn, request_id="r0", session_id=NEW, claimed=False)
    launched(conn, request_id="r2", session_id="11111111-2222-4333-8444-555555555555",
             claimed=False)
    conn.execute("UPDATE launches SET relaunch_of = 'r0' WHERE request_id = 'r1'")
    conn.execute("UPDATE launches SET resume_of = 'r1' WHERE request_id = 'r2'")
    assert sorted(store.chain(conn, "r2")) == ["r0", "r1", "r2"]
    assert sorted(store.chain(conn, "r1")) == ["r0", "r1"]
    conn.execute("UPDATE launches SET resume_of = 'r2' WHERE request_id = 'r0'")
    assert sorted(store.chain(conn, "r2")) == ["r0", "r1", "r2"]


@pytest.mark.skipif(not modules.available("cache"), reason="cache is not installed")
def test_cache_s_own_timers_listing_takes_the_subscription():
    import json
    import subprocess

    done = subprocess.run([str(paths.script("moxi")), "cache", "timers", "--json"],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
    view = json.loads(done.stdout)
    ours = [s for s in view["subscriptions"] if s["name"] == "launcher.recycle-on-expiry"]
    assert len(ours) == 1 and ours[0]["argv0"] == str(paths.script("moxi-launcher-job"))
    assert not [r for r in view["refused"] if (r.get("name") or "").startswith("launcher.")]
