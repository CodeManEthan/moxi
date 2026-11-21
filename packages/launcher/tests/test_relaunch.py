"""The relaunch verbs against fakes of turns, slots, comms and cache: each refusal with
nothing written, the handoff checks, the armed text, the run row and its job, a job that
does not start, the director's held call, following a run, status, and repair."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import modules, proc
from moxi.paths import Layout

from moxi_launcher import job as job_module
from moxi_launcher import relaunch, store, target

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
CLEARED = "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d"
SCRIPT = Path("/opt/env/bin/moxi-launcher-job")
SETTINGS = {"relaunch.person_s": 120, "relaunch.follow_s": 300}
LONG_AGO = "2026-01-01T00:00:00.000000Z"


def me() -> str:
    ident = proc.identity(os.getpid())
    return f"{ident.pid}@{ident.started:.6f}"


def agent(kind="subagent", state="running", *, final=False):
    return SimpleNamespace(kind=kind, state=state, final=final)


class FakeSession:
    def __init__(self, *, session_id=SID, capabilities=("exit",), turn_open=False,
                 number=4, agents=(), complete=True):
        self.session_id, self.endpoint_pid, self.pid = session_id, os.getpid(), os.getppid()
        self.capabilities = frozenset(capabilities)
        self.turn_open, self.number = turn_open, number
        self.entries, self.complete = list(agents), complete
        self.closed = 0

    def facts(self):
        return SimpleNamespace(turn_open=self.turn_open, turn=None, turn_number=self.number,
                               draft=False, stale=False)

    def agents(self):
        return SimpleNamespace(complete=self.complete, entries=self.entries)

    def close(self):
        self.closed += 1


class FakeTurns:
    def __init__(self, session):
        self.session, self.asked = session, []

    def find(self, *, session_id=None, pid=None):
        self.asked.append({"session_id": session_id, "pid": pid})
        return self.session


def slot_view(state="ended", hold=None, chain=None):
    last = None if chain is None else SimpleNamespace(meta={"chain": chain,
                                                            "request_id": "r9"})
    return SimpleNamespace(created=1.0, state=state, hold=hold, last=last)


class FakeSlots:
    class SlotsError(Exception):
        pass

    class TryAgain(SlotsError):
        pass

    def __init__(self, *, ok=True, why="", exec_state="started", exec_line="", start_error=None,
                 alive=(), shown=None, show_error=None, pane=None):
        self.ok, self.why = ok, why
        self.exec, self.exec_line, self.start_error = exec_state, exec_line, start_error
        self.alive, self.shown, self.show_error = set(alive), shown, show_error
        self.pane = pane or SimpleNamespace(pane_id="%9", pane_in_mode=False,
                                            client_activity=None, pane_activity=None)
        self.started, self.shows = [], []

    def supported(self):
        return SimpleNamespace(ok=self.ok, why=self.why)

    def show(self, slot):
        self.shows.append(slot)
        if self.show_error is not None:
            raise self.show_error
        return self.shown

    def pane_use(self, slot):
        return self.pane

    def run_job(self, name, argv, *, env, cwd):
        self.started.append(SimpleNamespace(name=name, argv=argv, env=env, cwd=cwd))
        if self.start_error is not None:
            raise self.start_error
        return SimpleNamespace(exec=self.exec, exec_line=self.exec_line)

    def job(self, name):
        return object() if name in self.alive else None


class DaemonOffline(Exception):
    pass


class FakeComms:
    GLOBAL = "global"

    def __init__(self, caller="boss", bound=None, error=None):
        self.caller, self.bound, self.error = caller, dict(bound or {}), error

    def caller_identity(self, env=None):
        if isinstance(self.caller, Exception):
            raise self.caller
        return self.caller

    def session_of(self, identity):
        if self.error is not None:
            raise self.error
        return self.bound.get(identity)

    def qualify(self, name, scope):
        return name if scope == self.GLOBAL else f"{name}@{scope}"

    def split_tag(self, tag):
        name, sep, scope = tag.partition("@")
        return name, scope if sep else None


class FakeCache:
    def __init__(self, kind="cold", error=None):
        self.kind, self.error, self.asked = kind, error, []

    def read(self, harness, session_id):
        self.asked.append((harness, session_id))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(kind=self.kind)


class Clock:
    """A monotonic clock a second apart on each read; `on_sleep` runs at each poll."""

    def __init__(self, on_sleep=None):
        self.now, self.slept, self.on_sleep = 0.0, [], on_sleep

    def __call__(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(len(self.slept))


@pytest.fixture(autouse=True)
def without_notes(monkeypatch):
    """notes not installed, whatever this environment holds, so a handoff may sit anywhere;
    the test that means notes installed says so itself."""
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "notes" and real(name))


class Checks:
    """The launch check stood in for: every request it is asked about, and the refusal
    it answers (None: the launch would be accepted)."""

    def __init__(self):
        self.asked, self.refusal = [], None

    def __call__(self, request, env=None):
        self.asked.append(request)
        return self.refusal


@pytest.fixture(autouse=True)
def checks(monkeypatch):
    """The launch check passes, unless a test says otherwise."""
    found = Checks()
    monkeypatch.setattr(job_module, "launch_check", found)
    return found


@pytest.fixture(autouse=True)
def script(monkeypatch):
    monkeypatch.setattr(relaunch.paths, "script", lambda name: SCRIPT.with_name(name))


@pytest.fixture
def conn(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    launched(conn)
    yield conn
    conn.close()


def launched(conn, *, request_id="r1", target_name="slot", session_id=SID, scope=None,
             directors=("boss",), relaunch_of=None, base_env=None, recycle="relaunch"):
    values = {"model": "opus", "director": list(directors)}
    if recycle is not None:
        values["recycle_at_limit"] = recycle
    store.insert(conn, request_id=request_id, by="ann", harness="claude",
                 base_env=base_env,
                 target=target_name, cwd="/work",
                 values=values, argv=["claude"],
                 env={}, posture="operator", session_id=session_id,
                 slot="3" if target_name == "slot" else None, identity="planner",
                 scope=scope, relaunch_of=relaunch_of)
    store.set_facts(conn, request_id, pane_id="%9")
    conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = ?", (me(), request_id))
    conn.execute("UPDATE session_ids SET seen_at = ? WHERE request_id = ?",
                 (LONG_AGO, request_id))


def handoff(tmp_path, text="state of the work; next step", name="handoff.md"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_env(**extra):
    return {**os.environ, **extra}


def arm(conn, path, *, session=None, slots_api=None, settings=None, **kw):
    session = session or FakeSession()
    slots_api = slots_api if slots_api is not None else FakeSlots()
    result = relaunch.arm_self(str(path), conn=conn, turns=FakeTurns(session),
                               slots_api=slots_api, settings={**SETTINGS, **(settings or {})},
                               caller_env=kw.pop("env", test_env()), **kw)
    return result


def call(conn, which=SID, *, session=None, slots_api=None, comms=None, cache=None,
         settings=None, turns=None, **kw):
    return relaunch.call_director(
        which, conn=conn, turns=turns or FakeTurns(session or FakeSession()),
        slots_api=slots_api if slots_api is not None else FakeSlots(),
        comms=comms if comms is not None else FakeComms(), cache=cache,
        settings={**SETTINGS, **(settings or {})}, caller_env=test_env(), **kw)


def runs(conn):
    return store.runs(conn)


def nothing_written(conn, slots_api, before=0):
    assert len(runs(conn)) == before
    assert slots_api.started == []


def ended(conn, outcome, *, mode="self", word=None, events=(), request_id="r1",
          repair_of=None, slot="3", handoff_path="/work/h.md"):
    run = store.insert_run(conn, kind="relaunch", mode=mode, request_id=request_id,
                           session_id=SID, by="planner", handoff_path=handoff_path,
                           slot=slot, pane_id="%9", turn_number=4, repair_of=repair_of,
                           job="slots:relaunch-x")
    for kind in events:
        store.add_run_event(conn, run.run_id, kind, {})
    store.end_run(conn, run.run_id, outcome, word=word or outcome)
    return store.get_run(conn, run.run_id)


# -- the self arm -------------------------------------------------------------------------


def test_a_self_arm_writes_its_run_starts_its_job_and_prints_the_armed_text(conn, tmp_path):
    path = handoff(tmp_path)
    session, slots_api = FakeSession(), FakeSlots()
    env = test_env(CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli", AI_AGENT="claude",
                  CLAUDE_CONFIG_DIR="/cfg", PATH="/usr/bin", MOXI_LAUNCHER_REQUEST="r0",
                  MOXI_TURNS_COMMANDS="exit")
    result = arm(conn, path, session=session, slots_api=slots_api, env=env)
    assert result.exit_code == 0 and result.data["outcome"] == "armed"
    run = store.get_run(conn, result.data["run_id"])
    assert result.run == run
    assert (run.kind, run.mode, run.state) == ("relaunch", "self", "armed")
    assert run.session_id == SID and run.request_id == "r1"
    assert run.handoff_path == str(path) and run.slot == "3" and run.pane_id == "%9"
    assert run.turn_number == 4 and run.actor
    # The run is written with its job's name, the id the job is started with.
    assert run.job == f"slots:relaunch-{run.run_id}"
    [job] = slots_api.started
    assert job.name == f"relaunch-{run.run_id}"
    assert job.argv == [str(SCRIPT), "relaunch", run.run_id]
    assert job.cwd == str(Layout(store.MODULE).state_dir(env))
    # The job takes this process's environment less every session's stamps.
    assert "CLAUDECODE" not in job.env and "CLAUDE_CODE_ENTRYPOINT" not in job.env
    assert "AI_AGENT" not in job.env
    assert "MOXI_LAUNCHER_REQUEST" not in job.env and "MOXI_TURNS_COMMANDS" not in job.env
    assert job.env["CLAUDE_CONFIG_DIR"] == "/cfg" and job.env["PATH"] == "/usr/bin"
    assert result.lines == tuple(relaunch.ARMED.format(
        run=run.run_id, slot="3", person="2 minutes", model="opus", how="the row's").splitlines())
    assert result.lines[0].startswith(f"armed {run.run_id}: this session exits")
    assert "on opus (the row's)." in result.lines[2]
    assert result.lines[-1] == "End your turn now and make no further tool call."
    assert session.closed == 1


def test_the_self_arm_finds_the_session_above_this_process(conn, tmp_path):
    turns = FakeTurns(FakeSession())
    relaunch.arm_self(str(handoff(tmp_path)), conn=conn, turns=turns, slots_api=FakeSlots(),
                      settings=SETTINGS, caller_env=test_env(), pid=4321)
    assert turns.asked == [{"session_id": None, "pid": 4321}]


@pytest.mark.parametrize("seconds, words", [(120, "2 minutes"), (60, "1 minute"),
                                            (90, "90 seconds")])
def test_the_armed_text_names_the_person_window(conn, tmp_path, seconds, words):
    result = arm(conn, handoff(tmp_path), settings={"relaunch.person_s": seconds})
    assert f"sent it a message for {words}; then it comes back in slot 3" in \
        " ".join(result.lines)


def test_a_dry_run_arm_writes_nothing(conn, tmp_path):
    slots_api = FakeSlots()
    result = arm(conn, handoff(tmp_path), slots_api=slots_api, dry_run=True)
    assert result.exit_code == 0 and result.data["outcome"] == "dry-run"
    assert "would arm a relaunch of session" in result.lines[0]
    nothing_written(conn, slots_api)


# -- the self-recycle gate ----------------------------------------------------------------


def unset_recycle(conn, value):
    row = store.get(conn, "r1")
    values = {k: v for k, v in row.values.items() if k != "recycle_at_limit"}
    if value is not None:
        values["recycle_at_limit"] = value
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 (json.dumps(values),))


@pytest.mark.parametrize("value, words", [
    (None, "launch r1 was started without --recycle-at-limit relaunch, so it can't recycle "
           "itself"),
    ("off", "launch r1 was started without --recycle-at-limit relaunch, so it can't recycle "
            "itself"),
    ("compact", "launch r1 recycles at its limit by compacting, so it can't relaunch "
                "itself")])
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_launch_without_recycle_at_limit_relaunch_refuses_the_self_arm(
        conn, tmp_path, value, words, dry_run):
    unset_recycle(conn, value)
    slots_api = FakeSlots()
    with pytest.raises(relaunch.Refused) as refused:
        arm(conn, handoff(tmp_path), slots_api=slots_api, dry_run=dry_run)
    assert refused.value.code == "no-self-recycle" and refused.value.exit_code == 3
    assert refused.value.problem == words
    assert "--recycle-at-limit relaunch can recycle itself" in refused.value.hint
    nothing_written(conn, slots_api)


@pytest.mark.parametrize("dry_run", [False, True])
def test_an_older_scratch_row_set_to_relaunch_refuses_the_self_arm(conn, tmp_path,
                                                                    dry_run):
    # Recorded before a scratch launch was refused recycle_at_limit = relaunch.
    row = store.get(conn, "r1")
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 (json.dumps({**row.values, "scratch": True}),))
    slots_api = FakeSlots()
    with pytest.raises(relaunch.Refused) as refused:
        arm(conn, handoff(tmp_path), slots_api=slots_api, dry_run=dry_run)
    assert refused.value.code == "no-self-recycle" and refused.value.exit_code == 3
    assert refused.value.problem == ("launch r1 works in a scratch folder, and a scratch "
                                     "launch can't recycle itself")
    assert refused.value.hint == "recycle with compact, or launch in a directory"
    nothing_written(conn, slots_api)


def test_a_slot_check_comes_before_the_self_recycle_gate(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    launched(conn, target_name="terminal", recycle=None)
    with pytest.raises(relaunch.Refused) as refused:
        arm(conn, handoff(tmp_path))
    assert refused.value.code == "not-slot"


def test_a_directors_call_needs_no_self_recycle(conn):
    unset_recycle(conn, None)
    result = call(conn, follow=False)
    assert result.data["outcome"] == "started"


# -- the model pin ------------------------------------------------------------------------


class PinSessions:
    """sessions' `find` and `records_back`, the newest request naming `model`."""

    def __init__(self, model):
        self.model, self.asked = model, []

    def find(self, harness, session_id, *, root=None):
        self.asked.append(session_id)
        return SimpleNamespace(hits=(SimpleNamespace(source="s0", compressed=False),))

    def records_back(self, source, *, kinds=None):
        return iter([SimpleNamespace(model=self.model, response_model=None, time=1.0)])


def drop_model(conn):
    row = store.get(conn, "r1")
    values = {k: v for k, v in row.values.items() if k != "model"}
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 (json.dumps(values),))


@pytest.mark.parametrize("dry_run", [False, True])
def test_the_arm_says_the_model_the_relaunch_comes_back_on(conn, tmp_path, dry_run):
    drop_model(conn)
    seen = PinSessions("claude-fable-5-2")
    result = arm(conn, handoff(tmp_path), sessions=seen, dry_run=dry_run)
    assert (result.data["model"], result.data["model_how"]) == ("claude-fable-5-2", "pinned")
    assert seen.asked == [SID]
    if not dry_run:
        assert "on claude-fable-5-2 (pinned from your transcript)." in " ".join(result.lines)


def test_the_armed_text_names_the_default_model_when_none_is_read(conn, tmp_path):
    drop_model(conn)
    result = arm(conn, handoff(tmp_path), sessions=None)
    assert ("on the harness's default model (default: no-sessions)."
            in " ".join(result.lines))


def test_the_arm_of_a_row_naming_a_model_reads_no_transcript(conn, tmp_path):
    seen = PinSessions("claude-fable-5-2")
    result = arm(conn, handoff(tmp_path), sessions=seen)
    assert (result.data["model"], result.data["model_how"]) == ("opus", "row")
    assert seen.asked == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_the_call_says_the_model_the_relaunch_comes_back_on(conn, dry_run):
    drop_model(conn)
    result = call(conn, follow=False, dry_run=dry_run, sessions=None)
    assert (result.data["model"], result.data["model_how"]) == (None, "default:no-sessions")


# -- the launch check ---------------------------------------------------------------------


def refusing(checks):
    checks.refusal = SimpleNamespace(code="identity-taken", problem="identity planner is "
                                     "held by claude session x", hint="pick another")


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_self_arm_whose_launch_would_be_refused_touches_nothing(conn, tmp_path, checks,
                                                                  dry_run):
    refusing(checks)
    slots_api = FakeSlots()
    with pytest.raises(relaunch.Refused) as refused:
        arm(conn, handoff(tmp_path), slots_api=slots_api, dry_run=dry_run)
    assert refused.value.code == "launch-check" and refused.value.exit_code == 3
    assert "(identity-taken): identity planner is held" in refused.value.problem
    nothing_written(conn, slots_api)


def test_the_arm_checks_its_own_launch_replacing_its_session_on_its_pin(conn, tmp_path,
                                                                        checks):
    drop_model(conn)
    arm(conn, handoff(tmp_path), sessions=PinSessions("claude-fable-5-2"))
    [asked] = checks.asked
    assert asked.replacing == SID and asked.model_pin == "claude-fable-5-2"
    assert asked.respawn and asked.slot == "3" and asked.relaunch_of == "r1"
    assert asked.handoff == str(handoff(tmp_path))


def test_a_dry_run_prints_the_model_and_the_checks_verdict(conn, tmp_path):
    result = arm(conn, handoff(tmp_path), dry_run=True)
    assert result.lines[1] == "it would come back on opus (the row's); its launch check passes"
    assert result.data["launch_check"] == "passes"
    called = call(conn, dry_run=True)
    assert called.lines[1] == "it would come back on opus (the row's); its launch check passes"


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_directors_call_whose_launch_would_be_refused_touches_nothing(conn, checks,
                                                                        dry_run):
    refusing(checks)
    slots_api = FakeSlots()
    with pytest.raises(relaunch.Refused) as refused:
        call(conn, slots_api=slots_api, dry_run=dry_run)
    assert refused.value.code == "launch-check"
    assert checks.asked[0].director and checks.asked[0].posture == "with:boss"
    nothing_written(conn, slots_api)


def test_a_repair_whose_launch_would_be_refused_writes_nothing(conn, checks):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    refusing(checks)
    slots_api = FakeSlots(shown=slot_view())
    with pytest.raises(relaunch.Refused) as refused:
        repair(conn, failed.run_id, slots_api=slots_api)
    assert refused.value.code == "launch-check"
    assert "repair can be repeated" in refused.value.hint
    assert len(runs(conn)) == 1 and slots_api.started == []
    [asked] = checks.asked
    assert asked.replacing is None and asked.hold == failed.run_id


def test_a_repair_checks_its_launch_on_the_original_runs_pin(conn, checks):
    drop_model(conn)
    failed = ended(conn, "relaunch-failed", events=("exit",))
    store.add_run_event(conn, failed.run_id, "model", {"model": "claude-fable-5-2",
                                                       "how": "pinned", "read": None})
    repair(conn, failed.run_id)
    assert checks.asked[0].model_pin == "claude-fable-5-2"


# -- the handoff --------------------------------------------------------------------------


def _missing(tmp_path):
    return tmp_path / "nowhere.md"


def _handoff_dir(tmp_path):
    (tmp_path / "dir").mkdir()
    return tmp_path / "dir"


def _empty(tmp_path):
    return handoff(tmp_path, "")


def _blank(tmp_path):
    return handoff(tmp_path, " \n\t\n")


def _not_utf8(tmp_path):
    path = tmp_path / "handoff.md"
    path.write_bytes(b"state of the work \xff\xfe next step")
    return path


def _too_big(tmp_path):
    return handoff(tmp_path, "x" * (relaunch.HANDOFF_CAP + 1))


def _fifo(tmp_path):
    path = tmp_path / "pipe"
    os.mkfifo(path)
    return path


@pytest.mark.parametrize("make, words", [
    (_missing, "can't be read"),
    (_handoff_dir, "is not a regular file"),
    (_fifo, "is not a regular file"),
    (_empty, "is empty"),
    (_blank, "is blank"),
    (_not_utf8, "is not UTF-8 text"),
    (_too_big, f"bytes, past the {64 * 1024} a relaunch carries"),
])
def test_a_handoff_that_is_not_a_readable_nonempty_small_file_is_refused(conn, tmp_path,
                                                                         make, words):
    slots_api = FakeSlots()
    with pytest.raises(relaunch.Refused) as caught:
        arm(conn, make(tmp_path), slots_api=slots_api)
    assert caught.value.code == "handoff" and caught.value.exit_code == 3
    assert words in caught.value.problem
    nothing_written(conn, slots_api)


def test_a_handoff_at_the_cap_is_taken(conn, tmp_path):
    path = handoff(tmp_path, "x" * relaunch.HANDOFF_CAP)
    assert arm(conn, path).data["outcome"] == "armed"


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a file mode "
                    "that denies reading")
def test_a_handoff_that_cannot_be_opened_is_refused(conn, tmp_path):
    path = handoff(tmp_path)
    path.chmod(0)
    try:
        with pytest.raises(relaunch.Refused) as caught:
            arm(conn, path)
    finally:
        path.chmod(0o600)
    assert caught.value.code == "handoff" and "can't be read" in caught.value.problem


def test_a_handoff_written_before_the_session_began_is_stale(conn, tmp_path):
    path = handoff(tmp_path)
    seen = store.now()
    conn.execute("UPDATE session_ids SET seen_at = ?", (seen,))
    before = store.parse_time(seen).timestamp() - 30
    os.utime(path, (before, before))
    slots_api = FakeSlots()
    with pytest.raises(relaunch.Refused) as caught:
        arm(conn, path, slots_api=slots_api)
    assert caught.value.code == "handoff" and "it is stale" in caught.value.problem
    nothing_written(conn, slots_api)


@pytest.mark.parametrize("session_id", [None, "never-seen"])
def test_with_no_seen_time_the_stale_check_reads_the_launchs_own_time(conn, tmp_path,
                                                                     session_id):
    path = handoff(tmp_path)
    made = store.now()
    conn.execute("UPDATE launches SET created_at = ? WHERE request_id = 'r1'", (made,))
    row = store.get(conn, "r1")
    before = store.parse_time(made).timestamp() - 30
    os.utime(path, (before, before))
    with pytest.raises(relaunch.Refused) as caught:
        relaunch.check_handoff(str(path), conn, row, session_id)
    assert caught.value.code == "handoff" and "it is stale" in caught.value.problem
    after = store.parse_time(made).timestamp() + 30
    os.utime(path, (after, after))
    assert relaunch.check_handoff(str(path), conn, row, session_id) == path


def test_the_stale_check_reads_the_sessions_current_id(conn, tmp_path):
    # Written after the launch's first id but before a clear gave it a new one.
    path = handoff(tmp_path)
    written = path.stat().st_mtime
    store.learn_session(conn, "r1", CLEARED, "clear")
    later = store.stamp(datetime.fromtimestamp(written + 60, timezone.utc))
    conn.execute("UPDATE session_ids SET seen_at = ? WHERE session_id = ?", (later, CLEARED))
    with pytest.raises(relaunch.Refused) as caught:
        arm(conn, path, session=FakeSession(session_id=CLEARED))
    assert "stale" in caught.value.problem


def test_a_relative_handoff_is_read_from_the_working_dir(conn, tmp_path):
    handoff(tmp_path)
    row = store.get(conn, "r1")
    found = relaunch.check_handoff("handoff.md", conn, row, SID, cwd=str(tmp_path))
    assert found == tmp_path / "handoff.md"


def test_with_notes_installed_the_handoff_must_be_in_the_launchs_dir(conn, tmp_path,
                                                                        monkeypatch):
    handoff_dir = tmp_path / "handoffs"
    handoff_dir.mkdir()
    available = relaunch.modules.available
    monkeypatch.setattr(relaunch.modules, "available",
                        lambda name: name == "notes" or available(name))
    monkeypatch.setattr(relaunch.handoffs, "handoff_dir_of_row",
                        lambda row, create=True: handoff_dir)
    with pytest.raises(relaunch.Refused) as caught:
        arm(conn, handoff(tmp_path))
    assert caught.value.code == "handoff"
    assert "not in this launch's handoff folder" in caught.value.problem
    assert arm(conn, handoff(handoff_dir)).data["outcome"] == "armed"


# -- subagents ----------------------------------------------------------------------------


@pytest.mark.parametrize("entries, complete", [
    ([agent()], True),
    ([agent("teammate", "waiting")], True),
    ([agent(state="unknown")], True),
    ([], False),
])
def test_a_live_subagent_or_an_unread_agent_list_refuses_the_arm(conn, tmp_path, entries,
                                                                 complete):
    slots_api = FakeSlots()
    with pytest.raises(relaunch.Refused) as caught:
        arm(conn, handoff(tmp_path), slots_api=slots_api,
            session=FakeSession(agents=entries, complete=complete))
    assert caught.value.code == "subagents" and caught.value.exit_code == 3
    nothing_written(conn, slots_api)


@pytest.mark.parametrize("entry", [agent("shell"), agent(final=True),
                                   agent(state="done")])
def test_a_shell_or_a_finished_agent_never_refuses(conn, tmp_path, entry):
    result = arm(conn, handoff(tmp_path), session=FakeSession(agents=[entry]))
    assert result.data["outcome"] == "armed"


# -- what relaunch is offered for -----------------------------------------------------------


def test_without_slots_relaunch_is_not_offered(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(relaunch.slots, "api", lambda: None)
    monkeypatch.setattr(target.slots, "api", lambda: None)
    with pytest.raises(relaunch.Refused) as caught:
        relaunch.arm_self(str(handoff(tmp_path)), conn=conn, turns=FakeTurns(FakeSession()),
                          settings=SETTINGS, caller_env=test_env())
    assert caught.value.code == "no-slots" and "not installed" in caught.value.problem


def test_slots_that_do_not_run_here_refuse(conn, tmp_path):
    slots_api = FakeSlots(ok=False, why="no tmux")
    with pytest.raises(relaunch.Refused) as caught:
        arm(conn, handoff(tmp_path), slots_api=slots_api)
    assert caught.value.code == "no-slots" and "no tmux" in caught.value.problem
    nothing_written(conn, slots_api)


def test_a_launch_not_in_a_slot_is_refused(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    launched(conn, target_name="terminal")
    with pytest.raises(relaunch.Refused) as caught:
        arm(conn, handoff(tmp_path))
    conn.close()
    assert caught.value.code == "not-slot" and "runs in its terminal" in caught.value.problem


def test_a_harness_turns_cannot_exit_is_refused(conn, tmp_path):
    session = FakeSession(capabilities=())
    with pytest.raises(relaunch.Refused) as caught:
        arm(conn, handoff(tmp_path), session=session)
    assert caught.value.code == "no-exit"
    assert session.closed == 1


def test_a_session_that_is_not_live_is_refused(conn):
    with pytest.raises(relaunch.Refused) as caught:
        call(conn, turns=FakeTurns(None))
    assert caught.value.code == "not-live" and "resume it" in caught.value.hint


def test_the_target_finders_refusals_pass_through(conn, tmp_path):
    with pytest.raises(target.NotLaunched):
        arm(conn, handoff(tmp_path), session=FakeSession(session_id="someone-else"))


# -- one open run -------------------------------------------------------------------------


def test_an_open_run_whose_job_runs_refuses_naming_it(conn, tmp_path):
    other = store.insert_run(conn, kind="relaunch", mode="director", request_id="r1",
                             session_id=SID, by="boss", job="slots:relaunch-live")
    slots_api = FakeSlots(alive={"relaunch-live"})
    with pytest.raises(store.RunOpen) as caught:
        arm(conn, handoff(tmp_path), slots_api=slots_api)
    assert caught.value.run.run_id == other.run_id
    assert caught.value.exit_code == 3 and other.run_id in str(caught.value)
    nothing_written(conn, slots_api, before=1)


def test_an_open_run_whose_job_is_gone_is_ended_and_the_arm_goes_on(conn, tmp_path):
    stale = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                             session_id=SID, by="planner", job="slots:relaunch-dead",
                             at=LONG_AGO)
    result = arm(conn, handoff(tmp_path))
    assert result.data["outcome"] == "armed"
    old = store.get_run(conn, stale.run_id)
    assert (old.outcome, old.word) == ("error", "job-gone")


def test_a_run_opened_after_the_early_check_still_loses_at_insert(conn, tmp_path,
                                                                 monkeypatch):
    store.insert_run(conn, kind="exit", mode="director", request_id="r1", session_id=SID,
                     by="boss", job="slots:exit-live")
    monkeypatch.setattr(relaunch, "_no_open_run", lambda *a: None)
    slots_api = FakeSlots(alive={"exit-live"})
    with pytest.raises(store.RunOpen):
        arm(conn, handoff(tmp_path), slots_api=slots_api)
    nothing_written(conn, slots_api, before=1)


# -- a job that does not start -----------------------------------------------------------------


@pytest.mark.parametrize("slots_api, words", [
    (FakeSlots(exec_state="failed", exec_line="failed: cwd: no such folder"), "failed: cwd"),
    (FakeSlots(start_error=FakeSlots.SlotsError("job-running")), "job-running"),
])
def test_a_job_that_does_not_start_ends_the_run_not_started(conn, tmp_path, slots_api,
                                                            words):
    result = arm(conn, handoff(tmp_path), slots_api=slots_api)
    assert result.exit_code == 1 and result.data["outcome"] == "not-started"
    run = result.run
    assert not run.open and run.outcome == "not-started" and words in run.detail
    assert [e.kind for e in store.run_events(conn, run.run_id)] == ["ended"]
    assert words in result.lines[0]
    # The run is not left open: the session can arm again at once.
    assert arm(conn, handoff(tmp_path)).data["outcome"] == "armed"


def test_a_job_script_not_installed_ends_the_run_not_started(conn, tmp_path, monkeypatch):
    def missing(name):
        raise FileNotFoundError(f"{name} is not installed in this environment")

    monkeypatch.setattr(relaunch.paths, "script", missing)
    slots_api = FakeSlots()
    result = arm(conn, handoff(tmp_path), slots_api=slots_api)
    assert result.exit_code == 1 and "is not installed" in result.run.detail
    assert slots_api.started == []


def test_an_unconfirmed_start_counts_as_started(conn, tmp_path):
    result = arm(conn, handoff(tmp_path), slots_api=FakeSlots(exec_state="unconfirmed"))
    assert result.exit_code == 0 and result.run.open


# -- the director's call --------------------------------------------------------------------


def test_a_directors_call_writes_its_run_and_starts_its_job(conn):
    slots_api = FakeSlots()
    result = call(conn, slots_api=slots_api, session=FakeSession(number=7), follow=False)
    assert result.exit_code == 0 and result.data["outcome"] == "started"
    run = result.run
    assert (run.mode, run.actor, run.handoff_path) == ("director", "boss", None)
    assert run.turn_number == 7 and run.job == f"slots:relaunch-{run.run_id}"
    [job] = slots_api.started
    assert job.argv == [str(SCRIPT), "relaunch", run.run_id]
    assert f"--status {run.run_id}" in result.lines[0]


def test_a_director_names_the_session_by_its_bound_comms_name(conn):
    result = call(conn, "planner@work", comms=FakeComms(bound={"planner@work": SID}),
                  follow=False)
    assert result.run.session_id == SID


@pytest.mark.parametrize("comms, code, exit_code", [
    (FakeComms(), "not-bound", 3),
    (FakeComms(error=DaemonOffline("daemon down")), "comms-offline", 75),
    (FakeComms(error=RuntimeError("bad answer")), "comms-failed", 1),
])
def test_a_name_comms_cannot_turn_into_a_session_is_refused(conn, comms, code, exit_code):
    slots_api = FakeSlots()
    with pytest.raises(relaunch.Refused) as caught:
        call(conn, "nobody", comms=comms, slots_api=slots_api)
    assert caught.value.code == code and caught.value.exit_code == exit_code
    nothing_written(conn, slots_api)


@pytest.mark.parametrize("which", ["0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
                                   "0A1B2C3D-4E5F-4A6B-8C7D-9E0F1A2B3C4D"])
def test_an_unknown_session_id_is_not_launched_and_comms_is_never_asked(conn, which):
    class Unasked(FakeComms):
        def session_of(self, identity):
            raise AssertionError("comms was asked about a session id")

    slots_api = FakeSlots()
    with pytest.raises(target.NotLaunched):
        call(conn, which, comms=Unasked(), slots_api=slots_api)
    nothing_written(conn, slots_api)


def test_a_name_that_is_not_a_session_id_goes_to_comms_even_when_a_launch_holds_it(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    launched(conn, request_id="r1", session_id="planner")
    launched(conn, request_id="r2", session_id=SID)
    try:
        result = call(conn, "planner", comms=FakeComms(bound={"planner": SID}),
                      follow=False)
    finally:
        conn.close()
    assert result.run.request_id == "r2" and result.run.session_id == SID


@pytest.mark.parametrize("text, shaped", [
    (SID, True), (SID.upper(), True), ("planner", False), ("planner@work", False),
    (SID.replace("-", ""), False), ("{" + SID + "}", False), (SID + "x", False),
    ("urn:uuid:" + SID, False), ("", False),
])
def test_what_reads_as_a_session_id(text, shaped):
    assert target.session_id_shaped(text) is shaped


def test_without_comms_there_is_no_directors_call(conn):
    with pytest.raises(relaunch.Refused) as caught:
        relaunch.call_director(SID, conn=conn, comms=None, settings=SETTINGS)
    assert caught.value.code == "no-comms" and "--self" in caught.value.hint


@pytest.mark.parametrize("caller", [None, "", RuntimeError("socket gone")])
def test_a_caller_with_no_proven_identity_is_refused(conn, caller):
    with pytest.raises(relaunch.Refused) as caught:
        call(conn, comms=FakeComms(caller=caller))
    assert caught.value.code == "no-identity"


@pytest.mark.parametrize("scope, caller, code", [
    (None, "planner", "self"),
    ("work", "planner@work", "self"),
    # The global planner is not the planner of scope work: it is no director either.
    ("work", "planner", "not-director"),
])
def test_the_caller_as_its_own_target_is_refused(tmp_path, scope, caller, code):
    conn = store.connect(tmp_path / "launcher.db")
    launched(conn, scope=scope)
    with pytest.raises(relaunch.Refused) as caught:
        call(conn, comms=FakeComms(caller=caller))
    conn.close()
    assert caught.value.code == code


@pytest.mark.parametrize("directors, scope, caller, allowed", [
    (("boss",), None, "boss", True),
    # A plain director name is read in the target launch's own scope.
    (("boss",), None, "boss@work", False),
    (("boss",), "work", "boss@work", True),
    (("boss",), "work", "boss", False),
    (("boss",), "work", "boss@home", False),
    # A director named with a scope admits that qualified name only.
    (("boss@work",), None, "boss@work", True),
    (("boss@work",), None, "boss", False),
    (("boss@work",), None, "boss@home", False),
    (("boss@work",), "home", "boss@home", False),
    (("boss@global",), None, "boss", True),
    (("boss@global",), "work", "boss", True),
    (("boss@global",), None, "boss@work", False),
    (("boss@work", "boss"), None, "boss@home", False),
    (("boss@work", "boss"), "home", "boss@home", True),
    (("boss",), None, "intruder", False),
    (("boss",), None, "intruder@boss", False),
    ((), None, "boss", False),
])
def test_only_a_director_the_launch_named_may_call(tmp_path, directors, scope, caller,
                                                    allowed):
    conn = store.connect(tmp_path / "launcher.db")
    launched(conn, directors=directors, scope=scope)
    slots_api = FakeSlots()
    try:
        if allowed:
            assert call(conn, comms=FakeComms(caller=caller), slots_api=slots_api,
                        follow=False).exit_code == 0
        else:
            with pytest.raises(relaunch.Refused) as caught:
                call(conn, comms=FakeComms(caller=caller), slots_api=slots_api,
                     follow=False)
            assert caught.value.code == "not-director"
            nothing_written(conn, slots_api)
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["warm", "fresh"])
def test_a_session_whose_cache_is_warm_is_refused_mail_it(conn, kind):
    cache, slots_api = FakeCache(kind), FakeSlots()
    with pytest.raises(relaunch.Refused) as caught:
        call(conn, cache=cache, slots_api=slots_api)
    assert caught.value.code == "warm" and caught.value.hint == "mail it"
    assert cache.asked == [("claude", SID)]
    nothing_written(conn, slots_api)


def test_the_warm_check_reads_the_cache_under_the_launchs_own_config_dir(tmp_path):
    class Rooted(FakeCache):
        def __init__(self):
            super().__init__("warm")
            self.roots = []

        def read(self, harness, session_id, **options):
            self.roots.append(options)
            return super().read(harness, session_id)

        @staticmethod
        def projects_root(env):
            return env["CLAUDE_CONFIG_DIR"] + "/projects"

    conn = store.connect(tmp_path / "rooted.db")
    try:
        launched(conn, base_env={"CLAUDE_CONFIG_DIR": "/seat"})
        cache = Rooted()
        with pytest.raises(relaunch.Refused) as caught:
            call(conn, cache=cache, slots_api=FakeSlots())
        assert caught.value.code == "warm"
        assert cache.roots == [{"projects_root": "/seat/projects"}]
    finally:
        conn.close()


@pytest.mark.parametrize("cache", [FakeCache("cold"), FakeCache(error=OSError("no store"))])
def test_a_cold_or_unread_cache_leaves_the_call(conn, cache):
    assert call(conn, cache=cache, follow=False).exit_code == 0


@pytest.mark.parametrize("session, slots_api, word", [
    (FakeSession(turn_open=True), FakeSlots(), "turn"),
    (FakeSession(agents=[agent()]), FakeSlots(), "subagents"),
    (FakeSession(), FakeSlots(pane=SimpleNamespace(pane_id="%9", pane_in_mode=True,
                                                   client_activity=None,
                                                   pane_activity=None)), "person"),
])
def test_a_hold_now_ends_the_call_held_with_nothing_written(conn, session, slots_api, word):
    result = call(conn, session=session, slots_api=slots_api)
    assert result.exit_code == 75 and result.run is None
    assert result.data["outcome"] == "held" and result.data["word"] == word
    assert result.lines[0].startswith(f"held: {word}: ")
    nothing_written(conn, slots_api)


def test_a_dry_run_call_writes_nothing(conn):
    slots_api = FakeSlots()
    result = call(conn, slots_api=slots_api, dry_run=True)
    assert result.exit_code == 0 and result.data["outcome"] == "dry-run"
    nothing_written(conn, slots_api)


# -- following the run ------------------------------------------------------------------------


def finishing(conn, outcome, at=2, **values):
    """A poll hook that ends the only open run with `outcome` at poll `at`."""

    def hook(polls):
        if polls == at:
            [run] = [r for r in runs(conn) if r.open]
            store.end_run(conn, run.run_id, outcome, word=outcome, **values)

    return hook


@pytest.mark.parametrize("outcome, code", [("relaunched", 0), ("held", 75),
                                           ("not-exited", 1), ("relaunch-failed", 1)])
def test_the_directors_call_follows_its_run_to_its_outcome(conn, outcome, code):
    values = {"new_request_id": "r2"} if outcome == "relaunched" else {}
    clock = Clock(finishing(conn, outcome, **values))
    result = call(conn, clock=clock, sleep=clock.sleep)
    assert result.exit_code == code and result.data["outcome"] == outcome
    assert len(clock.slept) == 2
    if outcome == "relaunched":
        assert result.data["new_request_id"] == "r2"
        assert "new launch r2" in result.lines[0]


def test_a_run_still_going_past_the_follow_bound_exits_75_naming_status(conn):
    clock = Clock()
    result = call(conn, clock=clock, sleep=clock.sleep, settings={"relaunch.follow_s": 10})
    run_id = result.run.run_id
    assert result.exit_code == 75 and result.data["outcome"] == "armed"
    assert f"--status {run_id}" in result.lines[0]
    assert 0 < len(clock.slept) <= 10


def test_a_job_that_did_not_start_is_not_followed(conn):
    clock = Clock()
    result = call(conn, slots_api=FakeSlots(exec_state="failed"), clock=clock, sleep=clock.sleep)
    assert result.exit_code == 1 and result.data["outcome"] == "not-started"
    assert clock.slept == []


# -- status -----------------------------------------------------------------------------------


def test_status_shows_the_run_the_latest_wait_first_then_every_event(conn):
    run = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                           session_id=SID, by="planner", slot="3", job="slots:x")
    store.add_run_event(conn, run.run_id, "wait", {"word": "turn", "detail": "turn 4 is open"})
    store.add_run_event(conn, run.run_id, "wait", {"word": "person",
                                                   "detail": "a client was active 40 s ago"})
    result = relaunch.status(run.run_id, conn=conn)
    # An open run exits 75, as a call still following it would.
    assert result.exit_code == 75
    assert result.lines[0] == f"run {run.run_id}: relaunch (self) of session {SID} in slot 3: " \
                              "armed"
    assert result.lines[1].startswith("waiting on: person: a client was active 40 s ago")
    assert [line.split(" ", 1)[1] for line in result.lines[2:]] == [
        "wait word turn, detail turn 4 is open",
        "wait word person, detail a client was active 40 s ago"]
    assert result.data["outcome"] == "armed" and result.data["wait"]["word"] == "person"
    assert [e["kind"] for e in result.data["events"]] == ["wait", "wait"]


def test_status_of_an_ended_run_names_its_outcome_and_new_launch(conn):
    run = store.insert_run(conn, kind="relaunch", mode="director", request_id="r1",
                           session_id=SID, by="boss", slot="3", job="slots:x")
    store.end_run(conn, run.run_id, "relaunched", word="relaunched", new_request_id="r2")
    result = relaunch.status(run.run_id, conn=conn)
    assert result.lines[0].endswith(": relaunched (relaunched), new launch r2")
    assert not any(line.startswith("waiting on") for line in result.lines)
    assert result.data["outcome"] == "relaunched" and result.data["wait"] is None


@pytest.mark.parametrize("outcome, code", [
    ("relaunched", 0), ("held", 75), ("not-exited", 1), ("refused", 1), ("stopped", 1),
    ("relaunch-failed", 1)])
def test_status_of_an_ended_run_exits_by_its_outcome(conn, outcome, code):
    run = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                           session_id=SID, by="planner", slot="3", job="slots:x")
    store.end_run(conn, run.run_id, outcome, word="w")
    assert relaunch.status(run.run_id, conn=conn).exit_code == code


def came_back(conn, *, state="held", word=None, detail=None, model="claude-opus-5-5",
              how="pinned", identity="planner", new_session="new-sid"):
    run = store.insert_run(conn, kind="relaunch", mode="director", request_id="r1",
                           session_id=SID, by="boss", slot="3", job="slots:x")
    store.end_run(conn, run.run_id, "relaunched", word=word, detail=detail,
                  new_request_id="r2")
    store.add_run_event(conn, run.run_id, "ended", {
        "outcome": "relaunched", "word": word, "detail": detail, "new_request_id": "r2",
        "session_id": new_session, "identity": identity, "posture": "with:boss",
        "model": model, "model_how": how, "identity_state": state})
    return run


def test_a_directors_relaunch_says_what_came_back(conn):
    run = came_back(conn)
    line = (f"run {run.run_id}: relaunched planner in slot 3 as session new-sid, posture "
            "with:boss, model claude-opus-5-5 (pinned from its transcript); comms bound; "
            "new launch r2")
    assert relaunch.status(run.run_id, conn=conn).lines[0] == line
    followed = relaunch.follow_run(conn, run.run_id, 0)
    assert followed.exit_code == 0 and followed.lines == (line,)


def test_a_directors_relaunch_line_says_when_comms_has_not_bound_the_name(conn):
    run = came_back(conn, state="free", word="no-ready", detail="no ready in 60 s",
                    model=None, how="default:no-model-line", identity=None,
                    new_session=None)
    assert relaunch.status(run.run_id, conn=conn).lines[0] == (
        f"run {run.run_id}: relaunched session {SID} in slot 3 as session not known yet, "
        "posture with:boss, model the harness's default model (default: no-model-line); "
        "comms not yet bound; flagged no-ready: no ready in 60 s; new launch r2")


def test_a_directors_relaunch_line_says_when_comms_could_not_be_read(conn):
    run = came_back(conn, state="unreadable: the daemon is down", how="row")
    line = relaunch.status(run.run_id, conn=conn).lines[0]
    assert "model claude-opus-5-5 (the row's); comms unreadable (the daemon is down);" in line
    assert "not yet bound" not in line


def test_a_directors_relaunch_without_comms_names_no_bind(conn):
    run = came_back(conn, state=None)
    assert "comms" not in relaunch.status(run.run_id, conn=conn).lines[0]


def stale_run(conn, *, mode="self", exited=True):
    run = store.insert_run(conn, kind="relaunch", mode=mode, request_id="r1",
                           session_id=SID, by="planner", slot="3", job="slots:relaunch-x",
                           at="2026-10-31T10:00:00.000000Z")
    store.add_run_event(conn, run.run_id, "wait", {"word": "turn", "detail": "a turn"})
    if exited:
        store.add_run_event(conn, run.run_id, "exit", {"turn_number": 4})
    return run


def test_status_of_an_open_run_whose_job_is_gone_says_it_has_no_end(conn):
    run = stale_run(conn)
    result = relaunch.status(run.run_id, conn=conn, slots_api=JobSlots(None))
    assert result.exit_code == 1 and result.data["job_gone"] is True
    assert result.lines[1:3] == (
        "the run has no end and its job slots:relaunch-x is not running",
        f"its record shows an exit: moxi launcher relaunch --from-record {run.run_id} "
        "launches the session again once it has exited")
    assert not any(line.startswith("waiting on") for line in result.lines)


def test_status_of_a_stale_run_that_never_exited_names_no_repair(conn):
    run = stale_run(conn, exited=False)
    result = relaunch.status(run.run_id, conn=conn, slots_api=JobSlots(None))
    assert result.exit_code == 1
    assert result.lines[1] == "the run has no end and its job slots:relaunch-x is not running"
    assert not any("--from-record" in line for line in result.lines)


def test_status_of_an_open_run_whose_job_runs_still_exits_75(conn):
    run = stale_run(conn)
    result = relaunch.status(run.run_id, conn=conn,
                             slots_api=JobSlots(running_job(os.getpid())))
    assert result.exit_code == 75 and result.data["job_gone"] is False
    assert result.lines[1].startswith("waiting on: turn")


def test_status_of_an_exit_pending_run_names_the_repair(conn):
    run = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                           session_id=SID, by="planner", slot="3", job="slots:x")
    store.end_run(conn, run.run_id, "exit-pending", word="person")
    result = relaunch.status(run.run_id, conn=conn)
    assert result.exit_code == 1
    assert result.lines[1] == (
        f"the session was left at its exit dialog: moxi launcher relaunch --from-record "
        f"{run.run_id} launches it again once a person has answered the dialog and the "
        "session has exited")


def test_status_of_no_such_run_is_refused(conn):
    with pytest.raises(relaunch.Refused) as caught:
        relaunch.status("feedfacecafebeef", conn=conn)
    assert caught.value.code == "no-run" and caught.value.exit_code == 3


# -- stop -------------------------------------------------------------------------------------


class JobSlots:
    """slots' `job`, answering one job by name."""

    def __init__(self, found=None, error=None):
        self.found, self.error, self.asked = found, error, []

    def job(self, name):
        self.asked.append(name)
        if self.error is not None:
            raise self.error
        return self.found


def running_job(pid, *, running=True):
    return SimpleNamespace(name="relaunch-x", running=running, pane_pid=pid)


@pytest.fixture
def child():
    import subprocess
    import sys

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    yield process
    if process.poll() is None:
        process.kill()
    process.wait(5)


def open_relaunch(conn, *, kind="relaunch", mode="self", job="slots:relaunch-x"):
    return store.insert_run(conn, kind=kind, mode=mode, request_id="r1", session_id=SID,
                            by="planner", slot="3", job=job)


def test_a_stop_sends_the_runs_job_sigterm_and_says_what_it_does(conn, child):
    run = open_relaunch(conn)
    api = JobSlots(running_job(child.pid))
    result = relaunch.stop_run(run.run_id, conn=conn, slots_api=api)
    assert result.exit_code == 0 and api.asked == ["relaunch-x"]
    assert result.lines == (f"stop sent to run {run.run_id}: it ends stopped before the "
                            "exit; from the exit on, it ends after the relaunch is tried",)
    assert result.data == {"run_id": run.run_id, "outcome": "stop-sent", "pid": child.pid}
    assert child.wait(5) == -relaunch.STOP_SIGNAL


def test_a_repair_run_is_stopped_too(conn, child):
    run = open_relaunch(conn, mode="repair", job="slots:repair-x")
    sent = []
    result = relaunch.stop_run(run.run_id, conn=conn,
                               slots_api=JobSlots(running_job(child.pid)),
                               kill=lambda pid, sig: sent.append((pid, sig)))
    assert sent == [(child.pid, relaunch.STOP_SIGNAL)]
    assert result.lines == (f"stop sent to run {run.run_id}: its session is down already, "
                            "so the stop is recorded and the run ends after the launch is "
                            "tried",)


def test_a_stop_of_no_run_another_kind_or_an_ended_run_is_refused(conn):
    def refused(run_id):
        with pytest.raises(relaunch.Refused) as caught:
            relaunch.stop_run(run_id, conn=conn, slots_api=JobSlots(),
                              kill=lambda *a: pytest.fail("nothing is sent"))
        assert caught.value.exit_code == 3
        return caught.value.code

    assert refused("feedfacecafebeef") == "no-run"
    compact = open_relaunch(conn, kind="compact", mode="self", job="proc:1@1.0")
    assert refused(compact.run_id) == "not-relaunch"
    store.end_run(conn, compact.run_id, "compacted")
    run = open_relaunch(conn)
    store.end_run(conn, run.run_id, "relaunched")
    assert refused(run.run_id) == "not-open"


@pytest.mark.parametrize("api", [
    JobSlots(None),
    JobSlots(running_job(None)),
    JobSlots(running_job(os.getpid(), running=False)),
    JobSlots(error=RuntimeError("slots broke")),
    None,
], ids=["no-job", "no-pid", "pane-dead", "unreadable", "no-slots"])
def test_a_stop_whose_job_is_gone_is_refused_and_sends_nothing(conn, api, monkeypatch):
    monkeypatch.setattr(relaunch.slots, "api", lambda: None)
    run = open_relaunch(conn)
    with pytest.raises(relaunch.Refused) as caught:
        relaunch.stop_run(run.run_id, conn=conn, slots_api=api,
                          kill=lambda *a: pytest.fail("nothing is sent"))
    assert caught.value.code == "job-gone" and store.get_run(conn, run.run_id).open


def test_a_stop_whose_pid_has_ended_is_refused(conn, child):
    child.kill()
    child.wait(5)
    run = open_relaunch(conn)
    with pytest.raises(relaunch.Refused) as caught:
        relaunch.stop_run(run.run_id, conn=conn, slots_api=JobSlots(running_job(child.pid)))
    assert caught.value.code == "job-gone"


# -- repair -----------------------------------------------------------------------------------


def repair(conn, run_id, *, slots_api=None, **kw):
    kw.setdefault("follow", False)
    kw.setdefault("turns", None)
    return relaunch.from_record(run_id, conn=conn,
                                slots_api=slots_api if slots_api is not None else FakeSlots(
                                    shown=slot_view()),
                                settings=SETTINGS, caller_env=test_env(), **kw)


def test_a_repair_writes_its_own_run_and_starts_its_job(conn):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    slots_api = FakeSlots(shown=slot_view())
    result = repair(conn, failed.run_id, slots_api=slots_api)
    assert result.exit_code == 0 and result.data["outcome"] == "started"
    run = result.run
    assert (run.kind, run.mode, run.repair_of) == ("relaunch", "repair", failed.run_id)
    assert run.request_id == "r1" and run.session_id == SID and run.slot == "3"
    assert run.handoff_path == "/work/h.md" and run.pane_id == "%9"
    assert run.job == f"slots:repair-{run.run_id}"
    [job] = slots_api.started
    assert job.name == f"repair-{run.run_id}"
    assert job.argv == [str(SCRIPT), "repair", run.run_id]
    assert slots_api.shows == ["3"]


@pytest.mark.parametrize("outcome, events", [("relaunch-failed", ()), ("gone", ()),
                                             ("error", ("exit",)), ("error", ("respawn",)),
                                             ("exit-pending", ("exit",))])
def test_each_run_that_left_its_session_down_is_repairable(conn, outcome, events):
    failed = ended(conn, outcome, events=events)
    assert repair(conn, failed.run_id).exit_code == 0


class FindingTurns:
    def __init__(self, found=None, error=None):
        self.found, self.error, self.asked = found, error, []

    def find(self, *, session_id=None, pid=None):
        self.asked.append(session_id)
        if self.error is not None:
            raise self.error
        return self.found


def test_a_repair_of_a_session_still_live_is_refused(conn):
    # A refused exit sent the self run back to its wait, then its job died: the session
    # still runs, and a repair would start a second copy of the seat.
    failed = ended(conn, "relaunch-failed", events=("exit",))
    slots_api = FakeSlots(shown=slot_view())
    turns = FindingTurns(found=SimpleNamespace(close=lambda: None))
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, failed.run_id, slots_api=slots_api, turns=turns)
    assert caught.value.code == "live" and turns.asked == [SID]
    assert slots_api.started == [] and store.runs(conn, request_id="r1")[0].run_id == \
        failed.run_id


def test_a_run_whose_exit_dialog_waits_for_a_person_is_refused_while_its_session_runs(conn):
    # exit-pending is repairable once a person answered the dialog; until then the
    # session is live, and the repair says so.
    failed = ended(conn, "exit-pending", events=("exit",))
    turns = FindingTurns(found=SimpleNamespace(close=lambda: None))
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, failed.run_id, slots_api=FakeSlots(shown=slot_view()), turns=turns)
    assert caught.value.code == "live"


def test_a_repair_whose_liveness_cant_be_read_is_refused(conn):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, failed.run_id, turns=FindingTurns(error=RuntimeError("no answer")))
    assert caught.value.code == "liveness-unread" and caught.value.exit_code == 1


def test_a_repair_of_a_repair_acts_as_the_original_run(conn):
    root = ended(conn, "relaunch-failed", events=("exit",), slot="3")
    first = ended(conn, "relaunch-failed", mode="repair", repair_of=root.run_id, slot="5",
                  handoff_path=None)
    hold = SimpleNamespace(holder=root.run_id)
    slots_api = FakeSlots(shown=slot_view(state="free", hold=hold))
    result = repair(conn, first.run_id, slots_api=slots_api)
    assert result.run.repair_of == first.run_id
    assert result.run.slot == "3" and result.run.handoff_path == "/work/h.md"
    assert slots_api.shows == ["3"]


def test_a_run_not_started_by_a_repair_leaves_the_original_repairable(conn):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    result = repair(conn, failed.run_id, slots_api=FakeSlots(shown=slot_view(),
                                                             exec_state="failed"))
    assert result.exit_code == 1 and result.run.outcome == "not-started"
    assert repair(conn, failed.run_id).exit_code == 0


def test_a_repair_follows_its_run(conn):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    clock = Clock(finishing(conn, "relaunched", at=1, new_request_id="r2"))
    result = repair(conn, failed.run_id, follow=True, clock=clock, sleep=clock.sleep)
    assert result.exit_code == 0 and result.data["new_request_id"] == "r2"


def test_repairing_no_such_run_is_refused(conn):
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, "feedfacecafebeef")
    assert caught.value.code == "no-run"


def test_a_run_of_another_kind_is_not_repaired(conn):
    run = store.insert_run(conn, kind="exit", mode="director", request_id="r1",
                           session_id=SID, by="boss", job="slots:x")
    store.end_run(conn, run.run_id, "error")
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id)
    assert caught.value.code == "not-relaunch"


def test_an_open_run_is_not_repaired(conn):
    run = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                           session_id=SID, by="planner", job="slots:x")
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id)
    assert caught.value.code == "run-open" and f"--status {run.run_id}" in caught.value.hint


@pytest.mark.parametrize("outcome, events", [("relaunched", ()), ("expired", ()),
                                             ("not-exited", ("exit",)), ("error", ()),
                                             ("held", ())])
def test_a_run_that_did_not_leave_its_session_down_is_not_repairable(conn, outcome, events):
    run = ended(conn, outcome, events=events)
    slots_api = FakeSlots(shown=slot_view())
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id, slots_api=slots_api)
    assert caught.value.code == "not-repairable"
    nothing_written(conn, slots_api, before=1)


def test_a_run_repaired_already_is_refused(conn):
    root = ended(conn, "relaunch-failed", events=("exit",))
    done = ended(conn, "relaunched", mode="repair", repair_of=root.run_id)
    store.end_run(conn, done.run_id, "relaunched")  # already ended: a no-op
    conn.execute("UPDATE runs SET new_request_id = 'r2' WHERE run_id = ?", (done.run_id,))
    failed_repair = ended(conn, "relaunch-failed", mode="repair", repair_of=root.run_id)
    for run in (root, failed_repair):
        with pytest.raises(relaunch.Refused) as caught:
            repair(conn, run.run_id)
        assert caught.value.code == "repaired" and done.run_id in caught.value.problem


def test_a_repair_without_slots_running_is_refused(conn):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, failed.run_id, slots_api=FakeSlots(ok=False))
    assert caught.value.code == "no-slots"


def test_a_repair_of_a_launch_the_log_no_longer_holds_is_refused(conn, monkeypatch):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    monkeypatch.setattr(relaunch.store, "get", lambda c, request_id: None)
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, failed.run_id)
    assert caught.value.code == "no-launch"


@pytest.mark.parametrize("error, code, exit_code", [
    (FakeSlots.TryAgain("lock held"), "slots-busy", 75),
    (FakeSlots.SlotsError("registry unreadable"), "slots-failed", 1),
])
def test_a_slot_that_cannot_be_read_refuses_the_repair(conn, error, code, exit_code):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    slots_api = FakeSlots(show_error=error)
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, failed.run_id, slots_api=slots_api)
    assert caught.value.code == code and caught.value.exit_code == exit_code
    nothing_written(conn, slots_api, before=1)


def test_a_slot_running_a_session_of_the_chain_refuses_the_repair(conn):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    slots_api = FakeSlots(shown=slot_view(state="occupied", chain="r1"))
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, failed.run_id, slots_api=slots_api)
    assert caught.value.code == "occupied-by-chain" and caught.value.exit_code == 3
    assert "attach to slot 3" in caught.value.hint
    nothing_written(conn, slots_api, before=1)


def test_the_chain_is_the_first_launch_of_the_relaunches(conn):
    launched(conn, request_id="r2", session_id=CLEARED, relaunch_of="r1")
    failed = ended(conn, "relaunch-failed", events=("exit",), request_id="r2")
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, failed.run_id, slots_api=FakeSlots(
            shown=slot_view(state="occupied", chain="r1")))
    assert caught.value.code == "occupied-by-chain"


@pytest.mark.parametrize("view", [slot_view(state="occupied", chain="other"),
                                  slot_view(hold=SimpleNamespace(holder="someone")),
                                  None])
def test_a_slot_taken_outside_the_chain_or_missing_still_repairs(conn, view):
    failed = ended(conn, "relaunch-failed", events=("exit",))
    result = repair(conn, failed.run_id, slots_api=FakeSlots(shown=view))
    assert result.exit_code == 0 and result.run.slot == "3"


def died(conn, *, events=("exit",)):
    """A run whose job died: still open, its job gone, past the insert's grace."""
    run = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                           session_id=SID, by="planner", handoff_path="/work/h.md",
                           slot="3", pane_id="%9", turn_number=4, job="slots:relaunch-dead",
                           at=LONG_AGO)
    for kind in events:
        store.add_run_event(conn, run.run_id, kind, {})
    return run


def test_a_run_whose_job_died_after_the_exit_is_repaired_and_ended_job_gone(conn):
    run = died(conn)
    result = repair(conn, run.run_id)
    assert result.exit_code == 0 and result.run.repair_of == run.run_id
    old = store.get_run(conn, run.run_id)
    assert (old.outcome, old.word) == ("error", "job-gone")


def test_a_run_whose_job_died_after_respawn_meets_its_chain_in_the_slot(conn):
    run = died(conn, events=("exit", "respawn"))
    slots_api = FakeSlots(shown=slot_view(state="occupied", chain="r1"))
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id, slots_api=slots_api)
    assert caught.value.code == "occupied-by-chain"
    # Refused with nothing written: the run is left for the next insert to end.
    assert store.get_run(conn, run.run_id).open
    nothing_written(conn, slots_api, before=1)


def test_a_run_whose_job_died_before_acting_is_not_repairable(conn):
    run = died(conn, events=())
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id)
    assert caught.value.code == "not-repairable"
    assert store.get_run(conn, run.run_id).open


def test_an_open_run_whose_job_still_runs_is_not_repaired(conn):
    run = died(conn)
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id, slots_api=FakeSlots(shown=slot_view(),
                                                     alive={"relaunch-dead"}))
    assert caught.value.code == "run-open"


def died(conn, *, events=("exit",)):
    """A run whose job died: still open, its job gone, past the insert's grace."""
    run = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                           session_id=SID, by="planner", handoff_path="/work/h.md",
                           slot="3", pane_id="%9", turn_number=4, job="slots:relaunch-dead",
                           at=LONG_AGO)
    for kind in events:
        store.add_run_event(conn, run.run_id, kind, {})
    return run


def test_a_run_whose_job_died_after_the_exit_is_repaired_and_ended_job_gone(conn):
    run = died(conn)
    result = repair(conn, run.run_id)
    assert result.exit_code == 0 and result.run.repair_of == run.run_id
    old = store.get_run(conn, run.run_id)
    assert (old.outcome, old.word) == ("error", "job-gone")


def test_a_run_whose_job_died_after_respawn_meets_its_chain_in_the_slot(conn):
    run = died(conn, events=("exit", "respawn"))
    slots_api = FakeSlots(shown=slot_view(state="occupied", chain="r1"))
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id, slots_api=slots_api)
    assert caught.value.code == "occupied-by-chain"
    # Refused with nothing written: the run is left for the next insert to end.
    assert store.get_run(conn, run.run_id).open
    nothing_written(conn, slots_api, before=1)


def test_a_run_whose_job_died_before_acting_is_not_repairable(conn):
    run = died(conn, events=())
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id)
    assert caught.value.code == "not-repairable"
    assert store.get_run(conn, run.run_id).open


def test_an_open_run_whose_job_still_runs_is_not_repaired(conn):
    run = died(conn)
    with pytest.raises(relaunch.Refused) as caught:
        repair(conn, run.run_id, slots_api=FakeSlots(shown=slot_view(),
                                                     alive={"relaunch-dead"}))
    assert caught.value.code == "run-open"
