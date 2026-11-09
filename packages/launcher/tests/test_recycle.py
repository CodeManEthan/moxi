"""Recycle against fakes of turns, slots and cache: the timer's subscription, the expiry
ask's reads in order and every verdict (quiet, blocked, failed, unconfirmed, cleared,
compacted), the background shells a clear stops after an idle hour, a repeated block as
one run, the crossing handler in both modes, a pending compact taken up by a run's end,
and the limit compact's job."""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest
from moxi import modules, paths, proc

from moxi_launcher import cachetimers, job, recycle, store

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
NEW = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
SETTINGS = {"relaunch.person_s": 120, "relaunch.wait_s": 900, "records.keep_days": 90,
            "recycle.limit_wait_s": 1800}


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


def shells(stopped=(), left=(), complete=True):
    return SimpleNamespace(stopped=tuple(stopped), complete=complete,
                           left=tuple(SimpleNamespace(id=i, why=w) for i, w in left))


def facts(turn_open=False, number=4, draft=False, stale=False):
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


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    launched(connection)
    yield connection
    connection.close()


def launched(conn, *, request_id="r1", values=None, session_id=SID, claimed=True,
             harness="claude", target="slot", relaunch_of=None, env=None, base_env=None):
    store.insert(conn, request_id=request_id, actor="ann", harness=harness, target=target,
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


def test_the_subscription_asks_from_expiry_every_two_minutes_for_the_stamped_session():
    sub = expiry_sub()
    assert sub == {"name": "launcher.recycle-on-expiry", "at": 0, "every": 120,
                   "requires_env": {"MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "*"},
                   "run": ["moxi-launcher-job", "expiry", "{session}"]}
    assert set(sub) <= {"name", "at", "until", "every", "requires_env", "run"}


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


def test_a_compact_on_expiry_never_stops_shells(conn):
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 ('{"recycle_on_expiry": "compact"}',))
    got = ask(conn, session=FakeSession([ran()]), cache=FakeCache(age=7200.0))
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


def test_a_codex_session_reads_its_cache_with_the_codex_sessions_directory(conn):
    launched(conn, request_id="c1", harness="codex", target="terminal", session_id=NEW,
             values={"recycle_on_expiry": "compact"}, env={"CODEX_HOME": "/home/x/.cx"})
    store.learn_session(conn, "c1", NEW, "sender")
    cache = FakeCache()
    session = FakeSession([ran()])
    session.session_id = NEW
    verdict = recycle.ask_expiry(NEW, conn=conn, turns=FakeTurns(session),
                                 slots_api=FakeSlots(), cache=cache, settings=SETTINGS)
    assert verdict.outcome == "compacted"
    assert cache.reads == [("codex", NEW, {"cache_source": "/home/x/.cx/sessions"})]


def test_a_claude_cache_read_names_the_harness_and_newest_id(conn):
    cache = FakeCache()
    ask(conn, cache=cache)
    assert cache.reads == [("claude", SID, {})]


class RootedCache(FakeCache):
    """A cache that also says where Claude writes transcripts, by cache's rule."""

    @staticmethod
    def projects_root(environ):
        home = environ.get("HOME", "/home/me")
        return environ.get("CLAUDE_CONFIG_DIR", f"{home}/.claude").replace("~", home) \
            + "/projects"


def test_a_claude_launch_with_its_own_config_dir_reads_its_cache_under_it(tmp_path):
    connection = store.connect(tmp_path / "rooted.db")
    try:
        launched(connection, base_env={"HOME": "/home/ann", "CLAUDE_CONFIG_DIR": "~/.seat",
                                       "CLAUDE_CODE_OAUTH_TOKEN": None})
        cache = RootedCache()
        ask(connection, cache=cache)
        assert cache.reads == [("claude", SID, {"projects_root": "/home/ann/.seat/projects"})]
    finally:
        connection.close()


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
                     actor="ann", state="acting", job=f"proc:{me()}",
                     at="2020-01-01T00:00:00.000000Z")
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "run-open")
    assert len(got.runs) == 1 and got.session.commands == []


def test_a_stale_open_run_is_ended_job_gone_and_the_ask_goes_on(conn):
    stale = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                             session_id=SID, actor="launcher", state="acting",
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


def test_an_open_hold_does_not_block_a_compact(conn):
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 ('{"recycle_on_expiry": "compact"}',))
    conn.execute("INSERT INTO warm_holds (request_id, name, opened_at) "
                 "VALUES ('r1', 'review', '2026-10-31T00:00:00.000000Z')")
    assert ask(conn, session=FakeSession([ran()])).verdict.outcome == "compacted"


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
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 (f'{{"recycle_at_limit": "{value}", "limit_at": "1x"}}',))


def test_a_relaunch_crossing_tells_the_agent_and_records_that_it_was_told(conn):
    limit(conn, "relaunch")
    line = recycle.crossing_line(conn, crossing(), pid=os.getpid())
    assert line == recycle.RELAUNCH_LINE and "relaunch --self --handoff" in line
    [told] = [e for e in store.events(conn, "r1") if e.kind == "recycle-told"]
    assert told.detail["crossing_key"] == f"{SID}|1x|2026-10-31T10:00:00Z"
    assert store.runs(conn, request_id="r1") == []


def test_the_same_crossing_twice_writes_once(conn):
    limit(conn, "relaunch")
    recycle.crossing_line(conn, crossing(), pid=os.getpid())
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) is None
    assert recycle.crossing_line(conn, crossing(at="later"), pid=os.getpid())
    assert len([e for e in store.events(conn, "r1") if e.kind == "recycle-told"]) == 2


def test_a_crossing_of_a_launch_off_unknown_or_claimed_elsewhere_is_none(conn):
    limit(conn, "off")
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) is None
    limit(conn, "relaunch")
    assert recycle.crossing_line(conn, crossing(session_id=NEW), pid=os.getpid()) is None
    assert recycle.crossing_line(conn, crossing(), pid=os.getppid()) is None


def test_a_compact_crossing_writes_its_run_and_spawns_its_job(conn, script, monkeypatch):
    # The handler runs in the session's endpoint, whose environment carries its stamps.
    monkeypatch.setenv("MOXI_LAUNCHER_REQUEST", "r1")
    limit(conn, "compact")
    spawn = Spawned()
    line = recycle.crossing_line(conn, crossing(), pid=os.getpid(), spawn=spawn)
    assert line == recycle.COMPACT_LINE
    [run] = store.runs(conn, request_id="r1")
    assert (run.kind, run.mode, run.state) == ("compact", "limit", "armed")
    assert run.crossing_key == f"{SID}|1x|2026-10-31T10:00:00Z"
    assert run.job == f"proc:{me()}"
    [(argv, options)] = spawn.calls
    assert argv == ["/venv/bin/moxi-launcher-job", "compact", run.run_id]
    assert options["detached"] is True
    assert "MOXI_LAUNCHER_REQUEST" not in options["env"]


def test_a_compact_crossing_twice_writes_one_run(conn, script):
    limit(conn, "compact")
    spawn = Spawned()
    recycle.crossing_line(conn, crossing(), pid=os.getpid(), spawn=spawn)
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid(), spawn=spawn) is None
    assert len(store.runs(conn, request_id="r1")) == 1 and len(spawn.calls) == 1


def test_a_compact_job_that_does_not_start_ends_its_run_failed(conn, script):
    limit(conn, "compact")
    recycle.crossing_line(conn, crossing(), pid=os.getpid(), spawn=Spawned(OSError("no")))
    [run] = store.runs(conn, request_id="r1")
    assert (run.state, run.outcome, run.word) == ("ended", "failed", "not-started")


def open_run(conn, kind="relaunch", mode="self"):
    return store.insert_run(conn, kind=kind, mode=mode, request_id="r1", session_id=SID,
                            actor="ann", job="slots:relaunch-x")


def test_a_compact_crossing_that_meets_an_open_run_leaves_a_pending_compact(conn, script):
    limit(conn, "compact")
    open_run(conn)
    spawn = Spawned()
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid(),
                                 spawn=spawn) == recycle.COMPACT_LINE
    assert recycle.crossing_line(conn, crossing(at="later"), pid=os.getpid(),
                                 spawn=spawn) == recycle.COMPACT_LINE
    assert store.pending_compact(conn, "r1")[0] == f"{SID}|1x|later"
    assert spawn.calls == [] and len(store.runs(conn, request_id="r1")) == 1
    assert recycle.crossing_line(conn, crossing(at="later"), pid=os.getpid()) is None


@pytest.fixture
def started(monkeypatch):
    seen = []
    monkeypatch.setattr(recycle, "start_compact", lambda conn, run, **kw: seen.append(run))
    return seen


def test_a_run_that_ends_right_after_the_crossing_meets_it_takes_its_compact(
        conn, monkeypatch, started):
    limit(conn, "compact")
    run = open_run(conn)
    insert = store.insert_run

    def then_the_run_ends(*args, **kwargs):
        try:
            return insert(*args, **kwargs)
        except store.RunOpen:
            store.end_run(conn, run.run_id, "not-exited", word="busy")
            raise

    monkeypatch.setattr(store, "insert_run", then_the_run_ends)
    assert recycle.crossing_line(conn, crossing(), pid=os.getpid()) == recycle.COMPACT_LINE
    assert store.pending_compact(conn, "r1") is None
    [picked] = started
    assert picked.crossing_key == store.crossing_key(SID, "1x", crossing().at)
    assert picked.kind == "compact"


def test_an_insert_that_pends_writes_the_pending_compact_and_no_run(conn):
    run = open_run(conn)
    with pytest.raises(store.RunOpen) as caught:
        store.insert_run(conn, kind="compact", mode="limit", request_id="r1",
                         session_id=SID, actor=store.LAUNCHER, crossing_key="k", pend=True)
    assert caught.value.run.run_id == run.run_id
    assert store.pending_compact(conn, "r1")[0] == "k"
    assert [r.run_id for r in store.runs(conn, request_id="r1")] == [run.run_id]



def test_a_run_s_end_takes_the_pending_compact_and_starts_it(conn, started):
    run = open_run(conn)
    store.pend_compact(conn, "r1", f"{SID}|1x|t")
    assert store.end_run(conn, run.run_id, "not-exited", word="busy")
    assert store.pending_compact(conn, "r1") is None
    [picked] = started
    assert (picked.kind, picked.mode, picked.state) == ("compact", "limit", "armed")
    assert picked.crossing_key == f"{SID}|1x|t" and picked.session_id == SID
    assert store.open_run(conn, "r1").run_id == picked.run_id
    assert [e.kind for e in store.run_events(conn, picked.run_id)] == ["picked"]


@pytest.mark.parametrize("outcome", ["relaunched", "cleared", "compacted", "exited"])
def test_an_end_that_left_the_context_fresh_drops_the_pending_compact(conn, started,
                                                                      outcome):
    run = open_run(conn)
    store.pend_compact(conn, "r1", f"{SID}|1x|t")
    store.end_run(conn, run.run_id, outcome)
    assert started == [] and store.pending_compact(conn, "r1") is None
    [dropped] = [e for e in store.events(conn, "r1") if e.kind == "pending-compact-dropped"]
    assert dropped.detail["crossing_key"] == f"{SID}|1x|t"
    assert store.open_run(conn, "r1") is None


@pytest.mark.parametrize("result, outcome, word", [
    ({"kind": "ran", "reason": None}, "exit-pending", "person"),
    ({"kind": "ran", "reason": None}, "relaunch-failed", "name-held"),
    ({"kind": "ran", "reason": None}, "error", "OSError"),
    ({"kind": "error", "reason": "gone"}, "relaunch-failed", "gone"),
    ({"kind": "refused", "reason": "gone"}, "relaunch-failed", "gone"),
    ({"kind": "ran", "reason": None}, "not-exited", "no-dialog"),
])
def test_an_end_after_the_exit_ran_drops_the_pending_compact(conn, started, result,
                                                            outcome, word):
    run = open_run(conn)
    store.add_run_event(conn, run.run_id, "exit", {"turn_number": 3})
    store.add_run_event(conn, run.run_id, "exit-result", result)
    store.pend_compact(conn, "r1", f"{SID}|1x|t")
    store.end_run(conn, run.run_id, outcome, word=word)
    assert started == [] and store.pending_compact(conn, "r1") is None
    assert [e for e in store.events(conn, "r1") if e.kind == "pending-compact-dropped"]
    assert store.open_run(conn, "r1") is None


@pytest.mark.parametrize("kind, mode, outcome, word", [
    ("relaunch", "self", "gone", "gone"),
    ("relaunch", "repair", "relaunch-failed", "slots-busy"),
    ("compact", "limit", "failed", "gone"),
])
def test_an_end_with_no_session_left_drops_the_pending_compact(conn, started, kind, mode,
                                                              outcome, word):
    run = open_run(conn, kind=kind, mode=mode)
    store.pend_compact(conn, "r1", f"{SID}|1x|t")
    store.end_run(conn, run.run_id, outcome, word=word)
    assert started == [] and store.pending_compact(conn, "r1") is None


def test_an_exit_refused_leaves_the_session_and_the_end_takes_the_pending_compact(
        conn, started):
    run = open_run(conn)
    store.add_run_event(conn, run.run_id, "exit-result", {"kind": "refused",
                                                          "reason": "busy"})
    store.pend_compact(conn, "r1", f"{SID}|1x|t")
    store.end_run(conn, run.run_id, "not-exited", word="busy")
    assert len(started) == 1


def test_an_end_that_made_a_new_launch_drops_the_pending_compact(conn, started):
    run = open_run(conn)
    store.pend_compact(conn, "r1", f"{SID}|1x|t")
    store.end_run(conn, run.run_id, "not-exited", word="busy", new_request_id="r2")
    assert started == [] and store.pending_compact(conn, "r1") is None


def test_a_dropped_run_takes_the_pending_compact_too(conn, started):
    run = open_run(conn, kind="clear", mode="expiry")
    store.pend_compact(conn, "r1", f"{SID}|1x|t")
    assert store.drop_run(conn, run.run_id)
    assert store.get_run(conn, run.run_id) is None and len(started) == 1


def test_a_stale_run_ended_by_an_insert_leaves_the_pending_compact(conn, started):
    open_run_ = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                                 session_id=SID, actor="launcher", job="proc:999999999@1.0",
                                 at="2020-01-01T00:00:00.000000Z")
    store.pend_compact(conn, "r1", f"{SID}|1x|t")
    store.insert_run(conn, kind="exit", mode="verb", request_id="r1", session_id=SID,
                     actor="ann", gone=lambda j: True)
    assert store.get_run(conn, open_run_.run_id).outcome == "error"
    assert started == [] and store.pending_compact(conn, "r1") is not None


def test_the_crossing_handler_gives_up_on_a_locked_store_within_its_bound(conn, tmp_path,
                                                                         monkeypatch):
    limit(conn, "compact")
    monkeypatch.setattr(store, "path", lambda env=None: tmp_path / "launcher.db")
    locker = store.connect(tmp_path / "launcher.db")
    locker.execute("BEGIN IMMEDIATE")
    try:
        began = time.monotonic()
        assert recycle.on_crossing(crossing()) is None
        assert time.monotonic() - began < 0.5
    finally:
        locker.execute("ROLLBACK")
        locker.close()


def test_a_relaunch_crossing_keeps_its_line_when_the_store_is_locked(conn, tmp_path,
                                                                    monkeypatch):
    limit(conn, "relaunch")
    monkeypatch.setattr(store, "path", lambda env=None: tmp_path / "launcher.db")
    locker = store.connect(tmp_path / "launcher.db")
    locker.execute("BEGIN IMMEDIATE")
    try:
        assert recycle.on_crossing(crossing()) == recycle.RELAUNCH_LINE
    finally:
        locker.execute("ROLLBACK")
        locker.close()


def test_the_crossing_handler_without_a_launch_log_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "path", lambda env=None: tmp_path / "none" / "launcher.db")
    assert recycle.on_crossing(crossing()) is None


# -- the limit compact's job -------------------------------------------------------------


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


def compact_run(conn, job_=None):
    return store.insert_run(conn, kind="compact", mode="limit", request_id="r1",
                            session_id=SID, actor="launcher", crossing_key=f"{SID}|1x|t",
                            job=job_, slot="3", pane_id="%9")


def run_compact(conn, run, session, settings=None):
    clock = Clock()
    runner = job.CompactJob(conn, run.run_id, turns=FakeTurns(session),
                            slots_api=FakeSlots(), comms=None,
                            settings={**SETTINGS, **(settings or {})}, clock=clock,
                            sleep=clock.sleep)
    outcome = runner.go()
    return SimpleNamespace(outcome=outcome, run=store.get_run(conn, run.run_id),
                           events=events(conn, run.run_id), clock=clock)


def test_the_compact_job_compacts_an_idle_session_without_stopping_shells(conn):
    session = FakeSession([ran()])
    got = run_compact(conn, compact_run(conn, "proc:1@1.0"), session)
    assert got.outcome == "compacted" and got.run.outcome == "compacted"
    assert session.commands == [("compact", 4, {})]
    assert got.events == ["compact", "result", "ended"]


def test_the_compact_job_records_itself_when_its_spawner_did_not(conn):
    got = run_compact(conn, compact_run(conn), FakeSession([ran()]))
    assert got.run.job == f"proc:{me()}"


def test_a_compact_held_past_its_wait_ends_blocked_with_the_word(conn):
    session = FakeSession(facts_seq=[facts(turn_open=True)])
    got = run_compact(conn, compact_run(conn, "proc:1@1.0"), session,
                      {"recycle.limit_wait_s": 0})
    assert (got.outcome, got.run.word) == ("blocked", "turn")
    assert session.commands == []


def test_a_refusal_that_may_clear_sends_the_compact_back_to_its_wait(conn):
    session = FakeSession([Result("refused", "rate"), ran()])
    got = run_compact(conn, compact_run(conn, "proc:1@1.0"), session)
    assert got.outcome == "compacted" and len(session.commands) == 2
    assert "again" in got.events and job.RETRY_S in got.clock.slept


def test_a_refusal_at_the_bound_ends_the_compact_blocked(conn):
    session = FakeSession([Result("refused", "draft")])
    got = run_compact(conn, compact_run(conn, "proc:1@1.0"), session,
                      {"recycle.limit_wait_s": 0})
    assert (got.outcome, got.run.word) == ("blocked", "draft")


@pytest.mark.parametrize("result, outcome, word", [
    (Result("refused", "not-allowed"), "failed", "not-allowed"),
    (Result("refused", "gone"), "failed", "gone"),
    (Result("failed", "timeout"), "unconfirmed", "timeout"),
])
def test_the_compact_job_maps_turns_answers(conn, result, outcome, word):
    got = run_compact(conn, compact_run(conn, "proc:1@1.0"), FakeSession([result]))
    assert (got.outcome, got.run.word) == (outcome, word)


def test_a_compact_of_a_session_not_live_fails(conn):
    run = compact_run(conn, "proc:1@1.0")
    got = run_compact(conn, run, None)
    assert (got.outcome, got.run.word) == ("failed", "gone")


def test_a_compact_ended_under_its_job_does_not_act(conn):
    run = compact_run(conn, "proc:1@1.0")
    store.end_run(conn, run.run_id, "error", word="job-gone")
    session = FakeSession([ran()])
    got = run_compact(conn, run, session)
    assert got.outcome == "error" and session.commands == []


def test_a_compact_inside_its_long_wait_is_not_stale(conn):
    run = store.insert_run(conn, kind="compact", mode="limit", request_id="r1",
                           session_id=SID, actor="launcher", job=f"proc:{me()}",
                           at="2020-01-01T00:00:00.000000Z")
    assert not store.run_stale(store.get_run(conn, run.run_id),
                               lambda j: job.gone(j))
    with pytest.raises(store.RunOpen):
        store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                         session_id=SID, actor="ann", gone=lambda j: job.gone(j))


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
