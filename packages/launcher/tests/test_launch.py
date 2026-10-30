"""The launch: `launch` and `check` end to end against a fake harness on PATH, in this
terminal and headless, the write order, a duplicate request, every refusal's exit code,
the scratch directory and the run-log sweep."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import exit as exits
from moxi import modules, proc
from moxi.config import ConfigError
from moxi_dev.testing import wait_for

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness, run_child
from moxi_launcher import api, catalog, first, presets, store, targets
from moxi_launcher.api import LaunchRequest
from moxi_launcher.compose import common

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
BRIEF = "Read the design and start the build."
POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="the fake harness is a script run by its #! line")

@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def turns(tmp_path, monkeypatch):
    """The fake turns behind the launcher's seam."""
    directory = tmp_path / "fake-turns"
    directory.mkdir()
    monkeypatch.setenv(first.TURNS_SEAM, "launcher_fake_turns")
    monkeypatch.setenv(fake_turns.DIR, str(directory))
    monkeypatch.syspath_prepend(str(TESTS))
    return SimpleNamespace(dir=directory,
                           calls=lambda name=None: [c for c in fake_turns.calls(directory)
                                                    if name is None or c["call"] == name])


def request(work: Path, *, rid: str = "r1", sid: str | None = SID,
            **typed) -> LaunchRequest:
    return LaunchRequest(typed=typed, cwd=str(work), request_id=rid, session_id=sid,
                         actor="ann")


def row(rid: str = "r1") -> store.LaunchRow | None:
    if not store.path().exists():
        return None
    conn = store.connect(create=False)
    try:
        return store.get(conn, rid)
    finally:
        conn.close()


def nothing_written(rid: str = "r1") -> bool:
    return row(rid) is None and not targets.runs_dir().exists()


# -- this terminal, end to end, in a process of its own ------------------------------------


@POSIX
def test_a_terminal_launch_becomes_the_harness(tmp_path, work, harness, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("LAUNCHER_FAKE_EXIT", "7")
    req = {"typed": {"brief": BRIEF, "model": "opus"}, "cwd": str(work),
           "request_id": "r1", "session_id": SID, "actor": "ann"}
    code, seen = run_child(tmp_path, req)
    run = harness.one()
    # exec: the harness has the launcher's pid, its exit code is the launch's, and it
    # runs in the launch's directory with the composed argv.
    assert code == 7, seen["stderr"]
    assert Path(run["argv"][0]) == harness.bin / "claude"
    assert run["argv"][1:] == seen["preview"][1:] and seen["preview"][0] == "claude"
    assert run["cwd"] == str(work)
    assert run["argv"][1].startswith("Brief from launcher, launched by ann")
    # The environment: scrubbed, then stamped.
    assert run["env"]["MOXI_LAUNCHER_REQUEST"] == "r1" and "CLAUDECODE" not in run["env"]
    # The launch log is not left open in the harness.
    assert not [p for p in run["fds"].values() if p.endswith("launcher.db")]
    got = row()
    assert (got.state, got.pid, got.target) == ("started", run["pid"], "terminal")
    assert got.pid_started is not None and got.session_id == SID
    assert got.argv == seen["preview"]
    assert got.env == {"MOXI_LAUNCHER_REQUEST": "r1"}
    prompt = common.prompt_path(targets.runs_dir(), "r1")
    assert Path(prompt).exists() and "--append-system-prompt-file" in run["argv"]
    assert stat.S_IMODE(Path(prompt).stat().st_mode) == 0o600


@POSIX
def test_a_terminal_launch_with_turns_starts_the_deliverer_before_it_execs(
        tmp_path, work, harness, turns):
    req = {"typed": {"brief": BRIEF}, "cwd": str(work), "request_id": "r2",
           "session_id": SID, "actor": "ann"}
    code, seen = run_child(tmp_path, req)
    run = harness.one()
    assert code == 0, seen["stderr"]
    # With turns the brief is the first turn, not on the argv, and the additions are.
    assert run["argv"][1:] == seen["preview"][1:]
    assert not any(word.startswith("Brief from launcher") for word in run["argv"])
    assert list(fake_turns.ADDITIONS) == run["argv"][5:7]
    assert run["env"]["MOXI_TURNS_COMMANDS"] == "exit"
    # The deliverer waited on the session by its minted id, given the harness's pid,
    # which is the launcher's own, and sent the brief once.
    wait_for(lambda: (row("r2").state == "first-sent"), timeout=60)
    [ready] = turns.calls("wait_ready")
    assert ready["session_id"] == SID
    [given] = turns.calls("deliver")
    assert given["ref"] == "launcher:r2" and BRIEF in given["text"]
    assert row("r2").pid == run["pid"]
    log = targets.run_log("r2")
    wait_for(lambda: log.exists() and "delivered" in log.read_text(), timeout=30)


@POSIX
def test_a_terminal_launch_with_an_identity_runs_comms_wrapper(tmp_path, work, harness):
    if not modules.available("comms"):
        pytest.skip("comms is not installed")
    req = {"typed": {"identity": "planner", "brief": BRIEF}, "cwd": str(work),
           "request_id": "r3", "session_id": SID, "actor": "ann"}
    code, seen = run_child(tmp_path, req, moxi="moxi-comms-fake")
    run = harness.one()
    assert code == 0, seen["stderr"]
    assert seen["preview"][:5] == ["moxi-comms-fake", "comms", "claude", "--name",
                                   "planner"]
    assert run["argv"][1:] == seen["preview"][1:]
    assert row("r3").identity == "planner"


@POSIX
def test_a_terminal_launch_whose_harness_is_missing_writes_nothing(tmp_path, work):
    req = {"typed": {"brief": BRIEF}, "cwd": str(work), "request_id": "r4",
           "session_id": SID, "actor": "ann"}
    code, seen = run_child(tmp_path, req, env={"PATH": str(tmp_path / "empty")})
    assert code == exits.ERROR, seen["stderr"]
    assert seen["refused"]["type"] == "NoCommand"
    assert nothing_written("r4")


# -- this terminal, in process, with exec stood in for -------------------------------------


@pytest.fixture
def no_exec(monkeypatch):
    """exec stood in for: records what it was handed, and the row as it was then."""
    seen = []

    def execve(command, argv, env):
        seen.append({"command": command, "argv": argv, "env": env, "cwd": os.getcwd(),
                     "row": row(env["MOXI_LAUNCHER_REQUEST"]), "order": "exec"})

    monkeypatch.setattr(targets, "_execve", execve)
    here = os.getcwd()
    yield seen
    os.chdir(here)


@pytest.fixture
def deliverers(monkeypatch):
    started = []

    def start(rid, *, pid=None, env=None):
        started.append({"rid": rid, "pid": pid, "row": row(rid)})
        return 4321

    monkeypatch.setattr(first, "start_deliverer", start)
    return started


def test_the_terminal_writes_its_pid_and_started_before_it_execs(work, harness, no_exec,
                                                                 deliverers):
    result = api.launch(request(work, brief=BRIEF))
    [handed] = no_exec
    assert handed["command"] == str(harness.bin / "claude")
    assert tuple(handed["argv"]) == result.argv and handed["cwd"] == str(work)
    assert handed["row"].state == "started" and handed["row"].pid == os.getpid()
    assert handed["row"].pid_started == pytest.approx(proc.identity(os.getpid()).started)
    # Without turns the brief is on the argv: no deliverer.
    assert deliverers == []
    assert result.state == "started" and result.exit_code == exits.OK


def test_the_deliverer_starts_after_started_and_before_exec(work, harness, no_exec,
                                                            deliverers, turns):
    api.launch(request(work, brief=BRIEF))
    [deliverer] = deliverers
    assert deliverer["pid"] == os.getpid() and deliverer["row"].state == "started"
    [handed] = no_exec
    assert handed["row"].state == "started"


def test_a_deliverer_that_fails_to_start_fails_the_launch_and_execs_nothing(
        work, harness, no_exec, turns, monkeypatch):
    def broken(rid, *, pid=None, env=None):
        raise OSError("no such file")

    monkeypatch.setattr(first, "start_deliverer", broken)
    result = api.launch(request(work, brief=BRIEF))
    assert no_exec == []
    assert (result.state, result.reason, result.exit_code) == (
        "failed", targets.NO_DELIVERER, exits.ERROR)
    assert row().state == "failed" and row().reason == targets.NO_DELIVERER


def test_an_exec_that_fails_ends_the_row_failed(work, harness, monkeypatch):
    def execve(command, argv, env):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(targets, "_execve", execve)
    here = os.getcwd()
    result = api.launch(request(work, brief=BRIEF))
    assert os.getcwd() == here
    assert (result.state, result.reason, result.exit_code) == (
        "failed", targets.SPAWN_FAILED, exits.ERROR)
    got = row()
    assert got.state == "failed" and got.reason == targets.SPAWN_FAILED
    [event] = [e for e in store.events(store.connect(), "r1") if e.kind == "start-failed"]
    assert "Permission denied" in event.detail["why"]


def test_on_windows_the_terminal_waits_for_its_child(work, harness, monkeypatch,
                                                     deliverers, turns):
    class Child:
        pid = os.getpid()

        def __init__(self, argv, env, cwd):
            Child.seen = {"argv": argv, "env": env, "cwd": cwd, "row": row()}

        def wait(self):
            return 5

    monkeypatch.setattr(targets, "WINDOWS", True)
    monkeypatch.setattr(targets, "_popen", Child)
    result = api.launch(request(work, brief=BRIEF))
    assert Child.seen["argv"][0] == str(harness.bin / "claude")
    assert Child.seen["argv"][1:] == list(result.argv[1:])
    assert Child.seen["cwd"] == str(work) and Child.seen["row"].state == "spawning"
    # Its pid recorded, then the deliverer given that pid, then the child's exit code.
    assert deliverers[0]["pid"] == Child.pid and deliverers[0]["row"].state == "started"
    assert result.exit_code == 5 and row().pid == Child.pid


# -- headless, end to end ------------------------------------------------------------------


@POSIX
@pytest.mark.parametrize("harness_name, head", [("claude", ["claude", "-p"]),
                                                ("codex", ["codex", "exec"])])
def test_a_headless_launch_starts_detached_and_returns(work, harness, harness_name, head,
                                                       monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    req = request(work, sid=None if harness_name == "codex" else SID,
                  harness=harness_name, target="headless", brief=BRIEF)
    shown = api.preview(req)
    result = api.launch(req)
    assert list(result.argv[:2]) == head and result.argv == shown.argv
    assert result.state == "started" and result.exit_code == exits.OK
    run = harness.one()
    assert run["pid"] == result.pid and run["cwd"] == str(work)
    assert Path(run["argv"][0]).name == harness_name and run["argv"][1:] == list(
        shown.argv[1:])
    assert run["env"]["MOXI_LAUNCHER_REQUEST"] == "r1" and "CLAUDECODE" not in run["env"]
    got = row()
    assert (got.state, got.pid, got.target, got.brief) == ("started", run["pid"],
                                                          "headless", BRIEF)
    assert got.pid_started is not None
    assert got.session_id == (SID if harness_name == "claude" else None)
    # Its output is appended to the run log, readable by its owner only.
    log = targets.run_log("r1")
    wait_for(lambda: log.exists() and "the fake harness ran" in log.read_text(), timeout=30)
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert str(log) in result.line


def test_a_headless_launch_spawns_from_spawning_and_records_its_process(work, harness,
                                                                        monkeypatch):
    seen = []

    def spawn(argv, **kw):
        seen.append({"argv": argv, **kw, "row": row()})
        return os.getpid()

    monkeypatch.setattr(targets, "_spawn", spawn)
    result = api.launch(request(work, target="headless", brief=BRIEF))
    [handed] = seen
    assert handed["detached"] is True and handed["cwd"] == str(work)
    assert handed["output"] == targets.run_log("r1")
    assert tuple(handed["argv"]) == result.argv
    assert handed["env"]["MOXI_LAUNCHER_REQUEST"] == "r1"
    assert handed["row"].state == "spawning" and handed["row"].pid is None
    got = row()
    assert got.state == "started" and got.pid == os.getpid()
    assert got.pid_started == pytest.approx(proc.identity(os.getpid()).started)


def test_a_headless_spawn_that_fails_ends_the_row_failed(work, harness, monkeypatch):
    def spawn(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(targets, "_spawn", spawn)
    result = api.launch(request(work, target="headless", brief=BRIEF))
    assert (result.state, result.reason, result.exit_code) == (
        "failed", targets.SPAWN_FAILED, exits.ERROR)
    assert row().state == "failed"


# -- the write order and the duplicate request ---------------------------------------------


def test_the_row_is_inserted_with_full_sync_before_the_start(work, harness, monkeypatch):
    """The insert is written with `synchronous = FULL`, and every later write is back at
    NORMAL; nothing starts before the insert."""
    real = store._writing
    writes = []

    def writing(conn):
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        writes.append(("write", sync))
        return real(conn)

    def spawn(argv, **kw):
        writes.append(("spawn", None))
        return os.getpid()

    monkeypatch.setattr(store, "_writing", writing)
    monkeypatch.setattr(targets, "_spawn", spawn)
    api.launch(request(work, target="headless", brief=BRIEF))
    full, normal = 2, 1
    assert writes[0] == ("write", full)
    assert writes[1] == ("spawn", None)
    assert writes[2:] and all(w == ("write", normal) for w in writes[2:])


def test_a_duplicate_request_starts_nothing_and_gives_the_stored_outcome(work, harness,
                                                                         monkeypatch):
    spawned = []
    monkeypatch.setattr(targets, "_spawn", lambda argv, **kw: spawned.append(argv) or 99)
    first_ = api.launch(request(work, target="headless", brief=BRIEF))
    again = api.launch(request(work, target="headless", brief="another brief"))
    assert len(spawned) == 1
    assert not first_.duplicate and again.duplicate
    assert (again.state, again.exit_code, again.pid) == ("started", exits.OK, 99)
    assert again.argv == first_.argv and "nothing was started again" in again.line
    assert row().brief == BRIEF


def test_a_duplicate_of_a_failed_launch_exits_as_it_failed(work, harness, monkeypatch):
    def spawn(argv, **kw):
        raise OSError("boom")

    monkeypatch.setattr(targets, "_spawn", spawn)
    api.launch(request(work, target="headless", brief=BRIEF))
    again = api.launch(request(work, target="headless", brief=BRIEF))
    assert again.duplicate and again.exit_code == exits.ERROR
    assert again.reason == targets.SPAWN_FAILED


@pytest.mark.parametrize("reason, code", [
    (targets.SPAWN_FAILED, exits.ERROR), (first.NO_READY, exits.ERROR),
    ("exited", exits.ERROR), (targets.TRY_AGAIN, exits.TEMPFAIL),
    ("dropped", exits.REFUSED), ("occupied", exits.REFUSED)])
def test_a_stored_failed_row_exits_by_its_reason(reason, code):
    got = SimpleNamespace(state="failed", reason=reason)
    assert targets.exit_of(got) == code
    assert targets.exit_of(SimpleNamespace(state="first-sent", reason="unconfirmed")) == 0


# -- refusals and their exit codes ---------------------------------------------------------


def write_presets(text: str) -> None:
    file = presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


@pytest.mark.parametrize("make, code, kind", [
    (lambda w: LaunchRequest(seat="nobody", cwd=str(w)), 3, presets.UnknownPreset),
    (lambda w: request(w, nothing=1), 2, catalog.BadValue),
    (lambda w: request(w, permission_mode="bypassPermissions"), 3, presets.OffPosture),
    (lambda w: request(w / "missing"), 3, api.NoDirectory),
    (lambda w: request(w, target="headless"), 3, common.NoBrief),
    (lambda w: request(w, target="slot"), 3, targets.NotBuilt),
    (lambda w: request(w, brief="x" * (store.CAP + 1)), 3, store.TooLarge),
    (lambda w: request(w, brief_file=str(w / "no-brief.md")), 3, common.Unreadable),
    (lambda w: request(w, harness="codex", sid=None, target="headless", brief="b",
                       approval="on-request"), 3, catalog.NotTaken),
    (lambda w: request(w, harness="codex", sid=None, extra_flags=["--permissions", "p"]),
     3, catalog.PostureFlag),
    (lambda w: LaunchRequest(typed={"extra_flags": ["--session-id", "x"]}, cwd=str(w)), 3,
     catalog.Owned),
    (lambda w: request(w, project="moxi"), 3, presets.NeedsProjects),
])
def test_a_refusal_before_the_row_writes_nothing_and_exits_with_its_code(
        work, harness, make, code, kind):
    with pytest.raises(kind) as raised:
        api.launch(make(work))
    assert api.exit_code(raised.value) == code
    assert nothing_written()
    assert harness.runs() == []


def test_a_harness_not_on_path_is_refused_before_the_row(work, monkeypatch):
    monkeypatch.setenv("PATH", str(work))
    with pytest.raises(targets.NoCommand) as raised:
        api.launch(request(work, brief=BRIEF))
    assert api.exit_code(raised.value) == exits.ERROR and raised.value.code == "not-found"
    assert nothing_written()


def test_a_presets_file_that_cant_be_read_exits_2(work, harness):
    write_presets("[seat.a\n")
    with pytest.raises(ConfigError) as raised:
        api.launch(request(work, brief=BRIEF))
    assert api.exit_code(raised.value) == exits.USAGE and nothing_written()


def test_a_session_id_another_launch_holds_is_refused(work, harness, monkeypatch):
    monkeypatch.setattr(targets, "_spawn", lambda argv, **kw: 99)
    api.launch(request(work, target="headless", brief=BRIEF))
    with pytest.raises(store.SessionTaken) as raised:
        api.launch(request(work, rid="r2", target="headless", brief=BRIEF))
    assert api.exit_code(raised.value) == exits.REFUSED and row("r2") is None


def test_a_busy_launch_log_exits_75_and_writes_nothing(work, harness, monkeypatch):
    holder = store.connect()
    holder.execute("BEGIN IMMEDIATE")
    real = store.connect
    monkeypatch.setattr(store, "connect",
                        lambda *a, **kw: real(*a, **{**kw, "timeout": 0.1}))
    try:
        with pytest.raises(store.Busy) as raised:
            api.launch(request(work, target="headless", brief=BRIEF))
    finally:
        holder.rollback()
        holder.close()
    assert api.exit_code(raised.value) == exits.TEMPFAIL
    assert row() is None and harness.runs() == []


def test_a_passed_on_permissions_profile_is_refused_from_a_preset_too(work, harness):
    write_presets('[seat.coder]\nharness = "codex"\nextra_flags = ["--permissions", "p"]\n')
    with pytest.raises(catalog.Refused) as raised:
        api.launch(LaunchRequest(seat="coder", cwd=str(work), request_id="r1"))
    assert api.exit_code(raised.value) == exits.REFUSED and nothing_written()


# -- projects: a lookup that fails ---------------------------------------------------------


class ProjectsError(Exception):
    def __init__(self, reason, fix=None):
        self.reason, self.fix = reason, fix
        super().__init__(reason)


class NotFound(ProjectsError):
    def __init__(self, key):
        self.key = key
        super().__init__(f"no project is named {key!r}", f"moxi projects add {key}")


class Unreadable(ProjectsError):
    pass


class Busy(Unreadable):
    pass


@pytest.fixture
def projects(monkeypatch, work):
    """projects installed, holding `moxi` at `work`; `fake.fail` makes calls raise."""
    fake = SimpleNamespace(fail=None)

    def get(key):
        if fake.fail:
            raise fake.fail
        if key == "moxi":
            return SimpleNamespace(key="moxi", root=str(work), agents=("planner",))
        raise NotFound(key)

    def for_path(path):
        if fake.fail:
            raise fake.fail
        return get("moxi") if path == str(work) else None

    found = SimpleNamespace(ok=True, state=modules.PRESENT, reason="", api=SimpleNamespace(
        get=get, for_path=for_path, ProjectsError=ProjectsError, NotFound=NotFound,
        Unreadable=Unreadable, Busy=Busy))
    real_available, real_load = modules.available, modules.load
    monkeypatch.setattr(modules, "available",
                        lambda name: name == "projects" or real_available(name))
    monkeypatch.setattr(modules, "load", lambda name, interface=None: found
                        if name == "projects" else real_load(name, interface=interface))
    return fake


@pytest.mark.parametrize("fail, kind, code", [
    (None, presets.UnknownProject, exits.REFUSED),
    (Unreadable("the store is damaged", "moxi doctor"), presets.ProjectsUnreadable,
     exits.REFUSED),
    (Busy("the store stayed locked", "try again"), presets.ProjectsBusy, exits.TEMPFAIL),
])
def test_a_project_lookup_that_fails_refuses_the_launch(work, harness, projects, fail,
                                                        kind, code):
    projects.fail = fail
    with pytest.raises(kind) as raised:
        api.launch(request(work, project="nope", brief=BRIEF))
    assert api.exit_code(raised.value) == code
    assert raised.value.hint and nothing_written()
    # check lists the same refusal, and writes nothing either.
    [listed] = api.check(request(work, project="nope", brief=BRIEF))
    assert type(listed) is kind and nothing_written()


def test_a_project_launch_records_its_key(work, harness, projects, monkeypatch):
    monkeypatch.setattr(targets, "_spawn", lambda argv, **kw: 99)
    api.launch(request(work, project="moxi", target="headless", brief=BRIEF))
    assert row().project_key == "moxi" and row().cwd == str(work)


# -- check ---------------------------------------------------------------------------------


def test_check_lists_nothing_for_a_launch_that_can_go(work, harness):
    assert api.check(request(work, brief=BRIEF)) == []
    assert nothing_written()


def test_check_lists_the_live_refusals_and_writes_nothing(work):
    [missing] = api.check(request(work / "gone"))
    assert isinstance(missing, api.NoDirectory) and missing.exit_code == exits.REFUSED
    [slot] = api.check(request(work, target="slot"))
    assert isinstance(slot, targets.NotBuilt) and "not built" in slot.problem
    assert "terminal" in slot.hint and "headless" in slot.hint
    assert nothing_written()


@pytest.mark.parametrize("state, holder, refused", [
    ("free", None, False), ("offline", None, False),
    ("held", "claude 1234", True), ("standing", None, True),
    ("reserved", "launcher", True), ("retired", None, True)])
def test_check_refuses_an_identity_that_is_not_free(work, monkeypatch, state, holder,
                                                    refused):
    if not modules.available("comms"):
        pytest.skip("comms is not installed")
    asked = []

    def identity_state(name, scope=None):
        asked.append((name, scope))
        return SimpleNamespace(state=state, holder=holder, module=None)

    monkeypatch.setattr(api, "_comms_api",
                        lambda: SimpleNamespace(identity_state=identity_state))
    got = api.check(request(work, identity="planner", scope="moxi"))
    assert asked == [("planner", "moxi")]
    if not refused:
        assert got == []
        return
    [taken] = got
    assert isinstance(taken, api.IdentityTaken) and taken.exit_code == exits.REFUSED
    assert f"identity planner is {state}" in taken.problem
    if holder:
        assert holder in taken.problem
    with pytest.raises(api.IdentityTaken):
        api.launch(request(work, identity="planner", scope="moxi"))
    assert nothing_written()


def test_a_comms_without_the_identity_check_lets_the_wrapper_decide(work, monkeypatch):
    monkeypatch.setattr(api, "_comms_api", lambda: SimpleNamespace())
    assert api._identity_taken("planner", None) is None


# -- scratch -------------------------------------------------------------------------------


@POSIX
def test_a_scratch_launch_makes_its_folder_and_never_removes_it(harness, tmp_path):
    req = LaunchRequest(typed={"harness": "codex", "scratch": True, "target": "headless",
                               "brief": BRIEF}, cwd=str(tmp_path), request_id="r9")
    assert not common.scratch_dir("r9").exists()
    api.preview(req)
    assert not common.scratch_dir("r9").exists()
    result = api.launch(req)
    where = common.scratch_dir("r9")
    assert where.is_dir() and stat.S_IMODE(where.stat().st_mode) == 0o700
    run = harness.one()
    assert run["cwd"] == str(where) and "--skip-git-repo-check" in run["argv"]
    assert row("r9").cwd == str(where) and result.exit_code == exits.OK
    targets.sweep(store.connect(), keep_days=0, now=time.time() + 10)
    assert where.is_dir()


# -- the run-log sweep ---------------------------------------------------------------------


def runs_file(name: str, data: bytes | str = "x\n", *, age_days: float = 0) -> Path:
    folder = targets.runs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    if age_days:
        then = time.time() - age_days * 86400
        os.utime(path, (then, then))
    return path


def running_row(conn, rid: str) -> None:
    store.insert(conn, request_id=rid, actor="ann", harness="codex", target="headless",
                 cwd="/work", values={}, argv=["codex"], env={}, posture="")
    me = proc.identity(os.getpid())
    store.set_state(conn, rid, "started", pid=me.pid, pid_started=me.started)


def test_the_sweep_removes_old_run_files_and_keeps_the_rest():
    old_log = runs_file("old.log", age_days=31)
    old_prompt = runs_file("old.prompt.md", age_days=31)
    new_log = runs_file("new.log", age_days=29)
    other = runs_file("notes.txt", age_days=90)
    conn = store.connect()
    running_row(conn, "live")
    live = runs_file("live.log", age_days=31)
    swept = targets.sweep(conn, keep_days=30)
    assert sorted(swept.removed) == ["old.log", "old.prompt.md"]
    assert not old_log.exists() and not old_prompt.exists()
    assert new_log.exists() and other.exists() and live.exists()


def test_the_sweep_reads_how_long_to_keep_from_the_settings():
    runs_file("a.log", age_days=31)
    assert targets.sweep().removed == ("a.log",)


def test_the_sweep_cuts_a_long_run_log_from_the_front():
    lines = b"".join(b"line %06d\n" % i for i in range(300))
    path = runs_file("big.log", lines)
    swept = targets.sweep(cap=1000, keep_days=30)
    assert swept.cut == ("big.log",)
    kept = path.read_bytes()
    assert len(kept) <= 1000
    head, _, rest = kept.partition(b"\n")
    assert head.startswith(b"[the launcher cut ")
    assert head.endswith(b" bytes from the front of this log]")
    assert rest.endswith(b"line 000299\n") and rest.startswith(b"line ")
    assert lines.endswith(rest)
    assert int(head.split()[3]) == len(lines) - len(rest)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # A log at or under the cap, and a prompt file of any size, are left as they are.
    small = runs_file("small.log", b"y" * 1000)
    prompt = runs_file("p.prompt.md", b"z" * 5000)
    assert targets.sweep(cap=1000, keep_days=30).cut == ()
    assert small.stat().st_size == 1000 and prompt.stat().st_size == 5000


def test_the_sweep_leaves_the_log_of_a_launch_still_running():
    conn = store.connect()
    running_row(conn, "live")
    path = runs_file("live.log", b"x" * 5000)
    assert targets.sweep(conn, cap=1000, keep_days=30).cut == ()
    assert path.stat().st_size == 5000
    # Once its process is gone, the next sweep cuts it.
    store.set_facts(conn, "live", pid_started=1.0)
    assert targets.sweep(conn, cap=1000, keep_days=30).cut == ("live.log",)


def test_the_sweep_with_no_runs_folder_does_nothing():
    assert targets.sweep() == targets.Swept()
    assert not targets.runs_dir().exists()


def test_a_launch_runs_the_sweep_and_caps_a_run_log_at_8_mib(work, harness, monkeypatch):
    monkeypatch.setattr(targets, "_spawn", lambda argv, **kw: 99)
    big = runs_file("earlier.log", b"o" * 100 + b"\n" + b"x" * (9 * 1024 * 1024))
    stale = runs_file("earlier.prompt.md", age_days=40)
    api.launch(request(work, target="headless", brief=BRIEF))
    assert big.stat().st_size <= targets.LOG_CAP == 8 * 1024 * 1024
    assert not stale.exists()


def test_a_refused_launch_runs_no_sweep(work):
    stale = runs_file("earlier.log", age_days=40)
    with pytest.raises(common.NoBrief):
        api.launch(request(work, target="headless"))
    assert stale.exists()


# -- the announcement ----------------------------------------------------------------------


def test_the_api_module_is_the_announced_one():
    found = modules.load("launcher")
    assert found.state == modules.PRESENT, found.reason
    assert found.api is api
    for name in ("launch", "check", "preview", "LaunchRequest", "LaunchResult"):
        assert hasattr(found.api, name)
