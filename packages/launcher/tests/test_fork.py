"""A fork (`moxi launcher fork`): its values by each row's fork class, the harness flags
that fork, its row's `fork_of` and launch kind, its environment, its refusals, and a
resume of a fork."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, catalog, cli, fork, store, targets
from moxi_launcher.api import LaunchRequest, ReplayRequest
from moxi_launcher.compose import Context, claude, codex
from moxi_launcher.target import NotLaunched

SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
NEW = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def no_exec(monkeypatch):
    """exec stood in for: records what it was handed."""
    seen = []

    def execve(command, argv, env):
        seen.append({"command": command, "argv": argv, "env": env})

    monkeypatch.setattr(targets, "_execve", execve)
    here = os.getcwd()
    yield seen
    os.chdir(here)


def row(rid: str) -> store.LaunchRow:
    conn = store.connect(create=False)
    try:
        found = store.get(conn, rid)
    finally:
        conn.close()
    assert found is not None
    return found


def parent(work: Path, **typed) -> api.LaunchResult:
    """A launch to fork: a named session with a model, a brief, a posture and options."""
    typed = {"model": "opus", "effort": "high", "name": "lead", "posture": "go",
             "brief": "Start the build.", "add_dir": [str(work)], **typed}
    return api.launch(LaunchRequest(typed=typed, cwd=str(work), request_id="r1",
                                    session_id=SID, actor="ann"))


# -- the values -----------------------------------------------------------------------


def test_a_fork_copies_the_request_rows_unsets_the_dropped_and_leaves_its_own_out():
    values = {"harness": "claude", "cwd": "/work", "model": "opus", "effort": "high",
              "permission_mode": "plan", "extra_flags": ["--verbose"], "name": "lead",
              "posture": "go", "brief": "b", "identity": "planner", "director": ["lead"],
              "warn_at": "2x", "keep_warm": "on", "remote_control": "x",
              "native_name": "lead", "target": "slot", "slot": "3", "trust": {"case": "x"}}
    got = catalog.forked("claude", values)
    assert {k: got[k] for k in ("harness", "cwd", "model", "effort", "permission_mode",
                                "extra_flags", "trust")} == \
        {k: values[k] for k in ("harness", "cwd", "model", "effort", "permission_mode",
                                "extra_flags", "trust")}
    dropped = catalog.unset(catalog.launcher()["keep_warm"])
    assert (got["name"], got["posture"], got["brief"], got["identity"], got["director"],
            got["warn_at"], got["keep_warm"], got["remote_control"], got["native_name"]) == \
        ("", "", "", "", [], "", dropped, False, "")
    assert "target" not in got and "slot" not in got


# -- the composers --------------------------------------------------------------------


def ctx(**fields) -> Context:
    base = {"request_id": "r2", "session_id": NEW, "actor": "ann",
            "runs_dir": "/state/runs", "moxi": "moxi"}
    return Context(**{**base, **fields})


def test_a_claude_fork_mints_its_id_and_resumes_the_parent_as_a_fork():
    got = claude.compose({"harness": "claude", "cwd": "/work", "model": "opus"},
                         ctx(fork_of=SID))
    argv = list(got.argv)
    assert argv[1:6] == ["--session-id", NEW, "--resume", SID, "--fork-session"]


def test_a_codex_fork_under_the_host_passes_the_parent_thread():
    got = codex.compose({"harness": "codex", "cwd": "/work"},
                        ctx(session_id=None, fork_of="thread-7", mode="host",
                            host="/bin/moxi-turns-codex-host",
                            modules=frozenset({"turns"})))
    assert got.argv[-2:] == ("--fork", "thread-7")


def test_a_codex_fork_outside_the_host_is_refused():
    with pytest.raises(catalog.Refused, match="only turns' Codex host forks"):
        codex.compose({"harness": "codex", "cwd": "/work"},
                      ctx(session_id=None, fork_of="thread-7", mode="interactive"))


def test_a_codex_launch_never_passes_the_hosts_fork_on():
    with pytest.raises(catalog.Owned, match="moxi launcher fork"):
        catalog.refuse_passthrough("codex", ["--fork", "thread-7"], "host")


# -- a fork, end to end ------------------------------------------------------------------


def test_a_fork_of_a_live_session_records_fork_of_and_reads_as_a_fork(work, harness,
                                                                     no_exec):
    parent(work)
    got = fork.fork(SID, request_id="r2")
    assert got.exit_code == 0 and got.data["outcome"] == "forked"
    new_id = got.data["session_id"]
    assert new_id not in (None, SID) and got.data["fork_of"] == SID
    assert got.lines[-1] == f"session {new_id}, a fork of session {SID}"
    argv = no_exec[-1]["argv"]
    at = argv.index("--resume")
    assert argv[at:at + 3] == ["--resume", SID, "--fork-session"]
    assert argv[argv.index("--session-id") + 1] == new_id
    assert argv[argv.index("--model") + 1] == "opus"
    assert "-n" not in argv and "Start the build." not in " ".join(argv)
    new = row("r2")
    assert (new.fork_of, new.resume_of, new.relaunch_of, new.kind) == (SID, None, None,
                                                                      "fork")
    assert (new.target, new.posture, new.brief, new.preset_kind) == ("terminal", "", None,
                                                                      None)
    assert (new.values["name"], new.values["model"], new.values["add_dir"]) == (
        "", "opus", [str(work)])
    assert api.launch_kind(new_id) == "fork" and api.launch_kind(SID) == "one-off"


def test_a_forks_env_holds_nothing_of_the_asking_session(work, harness, no_exec,
                                                         monkeypatch):
    parent(work)
    monkeypatch.setenv("MOXI_COMMS_NAME", "asker")
    monkeypatch.setenv("ASKERS_OWN", "1")
    fork.fork(SID, request_id="r2")
    env = no_exec[-1]["env"]
    assert "MOXI_COMMS_NAME" not in env and "ASKERS_OWN" not in env
    assert env["MOXI_LAUNCHER_REQUEST"] == "r2"


def test_a_resume_of_a_fork_is_a_fork(work, harness, no_exec):
    parent(work)
    forked = fork.fork(SID, request_id="r2").data["session_id"]
    conn = store.connect()
    try:
        conn.execute("UPDATE launches SET pid_started = 1.0 WHERE request_id = 'r2'")
        found = store.resolve(conn, forked)
    finally:
        conn.close()
    api.launch(ReplayRequest(found, resume_of="r2", request_id="r3", actor="ann"))
    assert (row("r3").fork_of, row("r3").kind) == (SID, "fork")


@pytest.mark.parametrize("given, code", [
    ({"target": "headless"}, "bad-value"),
    ({"target": "terminal", "slot": "2"}, "bad-value"),
])
def test_a_fork_in_a_target_it_cant_take_is_refused(work, harness, no_exec, given, code):
    parent(work)
    with pytest.raises(catalog.Refused) as raised:
        fork.fork(SID, request_id="r2", **given)
    assert raised.value.code == code
    conn = store.connect()
    try:
        assert store.get(conn, "r2") is None
    finally:
        conn.close()


def test_a_session_no_launch_holds_is_refused(work, harness, no_exec):
    parent(work)
    with pytest.raises(NotLaunched):
        fork.fork(NEW)


def test_a_codex_launch_with_no_thread_id_is_refused():
    found = fork.refusal(store.LaunchRow(**{**dict.fromkeys(store.COLUMNS),
                                            "request_id": "r1", "harness": "codex"}), None)
    assert found is not None and found.code == "no-session-id"


def test_the_verb_prints_the_new_session_as_json(work, harness, no_exec, capsys):
    parent(work)
    assert cli.main(["fork", SID, "--json"]) == 0
    said = json.loads(capsys.readouterr().out)
    assert said["outcome"] == "forked" and said["fork_of"] == SID
    assert said["session_id"] not in (None, SID) and said["target"] == "terminal"



def test_a_fork_is_pointed_to_no_handoff_and_marks_none_picked(work, harness, no_exec,
                                                               monkeypatch):
    from moxi_launcher import handoffs, listen_handoff, listeners

    parent(work, project="")
    fork.fork(SID, request_id="r2")
    monkeypatch.setattr(listeners, "claim", lambda event, conn: row("r2"))
    monkeypatch.setattr(handoffs, "waiting", lambda *a, **k: pytest.fail("looked"))
    assert listen_handoff._launched(object(), None) == []


# -- the model --------------------------------------------------------------------------

PINNED = "claude-fable-5-2-20261101"


class Transcripts:
    """Stands in for sessions' API over one transcript whose newest request names `model`;
    `asked` keeps each session id looked up."""

    def __init__(self, model: str):
        from types import SimpleNamespace

        self.request = SimpleNamespace(model=model, response_model=None, time=1.0,
                                       synthetic=None, sidechain=None)
        self.hit = SimpleNamespace(source="s0", compressed=False)
        self.asked: list[str] = []

    def find(self, harness, session_id, *, root=None):
        from types import SimpleNamespace

        self.asked.append(session_id)
        return SimpleNamespace(hits=(self.hit,))

    def records_back(self, source, *, kinds=None):
        return iter([self.request])


def model_pins(rid: str) -> list[dict]:
    conn = store.connect(create=False)
    try:
        return [e.detail for e in store.events(conn, rid) if e.kind == api.MODEL_PIN]
    finally:
        conn.close()


def test_a_fork_of_a_row_naming_no_model_runs_the_model_the_session_ran(work, harness,
                                                                        no_exec):
    parent(work, model="")
    assert not row("r1").values.get("model")
    seen = Transcripts(PINNED)
    fork.fork(SID, request_id="r2", sessions=seen)
    argv = no_exec[-1]["argv"]
    assert argv[argv.index("--model") + 1] == PINNED and seen.asked == [SID]
    assert not row("r2").values.get("model")
    assert model_pins("r2") == [{"model": PINNED, "how": "pinned"}]


def test_a_fork_reads_the_session_its_launch_was_last_known_by(work, harness, no_exec):
    parent(work, model="")
    conn = store.connect()
    try:
        store.learn_session(conn, "r1", NEW, "clear")
    finally:
        conn.close()
    seen = Transcripts(PINNED)
    fork.fork(SID, request_id="r2", sessions=seen)
    assert seen.asked == [NEW]
    assert model_pins("r2") == [{"model": PINNED, "how": "pinned"}]


def test_a_fork_of_a_row_naming_a_model_takes_it_and_reads_nothing(work, harness,
                                                                    no_exec):
    parent(work)
    seen = Transcripts(PINNED)
    fork.fork(SID, request_id="r2", sessions=seen)
    argv = no_exec[-1]["argv"]
    assert argv[argv.index("--model") + 1] == "opus" and PINNED not in argv
    assert seen.asked == [] and model_pins("r2") == []


@pytest.mark.parametrize("sessions", [None, Transcripts("not a model id")])
def test_a_fork_whose_model_cant_be_read_runs_the_default(work, harness, no_exec,
                                                          sessions):
    parent(work, model="")
    fork.fork(SID, request_id="r2", sessions=sessions)
    assert "--model" not in no_exec[-1]["argv"]
    assert model_pins("r2") == []


def test_a_resume_of_a_pinned_fork_runs_its_rows_own_model(work, harness, no_exec):
    parent(work, model="")
    forked = fork.fork(SID, request_id="r2",
                       sessions=Transcripts(PINNED)).data["session_id"]
    conn = store.connect()
    try:
        conn.execute("UPDATE launches SET pid_started = 1.0 WHERE request_id = 'r2'")
        found = store.resolve(conn, forked)
    finally:
        conn.close()
    resumed = api.launch(ReplayRequest(found, resume_of="r2", request_id="r3", actor="ann"))
    assert "--model" not in resumed.argv and PINNED not in resumed.argv


# -- stamps and interface 3 ----------------------------------------------------------------

FORK_ENV = "MOXI_MEMORY_SUBCONSCIOUS_FORK"


@pytest.fixture
def memory_lists(monkeypatch):
    """A present memory module whose `SESSION_ENV` lists `FORK_ENV`, beside what is
    installed here."""
    from types import SimpleNamespace

    from moxi import modules

    real_installed, real_load = modules.installed, modules.load
    fake = SimpleNamespace(ok=True, state=modules.PRESENT,
                           session_env={FORK_ENV: "a subconscious fork"})
    monkeypatch.setattr(modules, "installed", lambda: [
        *(e for e in real_installed() if e.name != "memory"),
        SimpleNamespace(name="memory")])
    monkeypatch.setattr(modules, "load", lambda name, **kw: (
        fake if name == "memory" else real_load(name, **kw)))


def test_a_forks_stamps_land_after_the_scrub(work, harness, no_exec, memory_lists,
                                             monkeypatch):
    parent(work)
    monkeypatch.setenv(FORK_ENV, "the asker's")
    fork.fork(SID, request_id="r2", stamps={FORK_ENV: "recall-1"})
    assert no_exec[-1]["env"][FORK_ENV] == "recall-1"


def test_the_api_forks_with_stamps(work, harness, no_exec, memory_lists):
    parent(work)
    got = api.fork(SID, stamps={FORK_ENV: "recall-1"}, request_id="r2")
    assert got.session_id not in (None, SID)
    assert no_exec[-1]["env"][FORK_ENV] == "recall-1"
    assert (row("r2").fork_of, row("r2").kind) == (SID, "fork")


@pytest.mark.parametrize("name, said", [
    ("MOXI_LAUNCHER_REQUEST", "the launch stamps itself"),
    ("MOXI_TURNS_COMMANDS", "the launch stamps itself"),
    ("OTHER_NAME", "no module's variable"),
    ("MOXI_MEMORY_SOMETHING_ELSE", "not in memory's SESSION_ENV"),
])
def test_a_stamp_no_module_lists_is_refused_with_nothing_written(
        work, harness, no_exec, memory_lists, name, said):
    parent(work)
    with pytest.raises(catalog.BadValue, match=said) as raised:
        api.fork(SID, stamps={name: "1"}, request_id="r2")
    assert raised.value.row == "stamps"
    conn = store.connect()
    try:
        assert store.get(conn, "r2") is None
    finally:
        conn.close()


def test_the_api_exits_a_session_through_the_exit_verb(monkeypatch):
    from moxi_launcher import exits

    calls = []
    monkeypatch.setattr(exits, "exit_session",
                        lambda *args, **kw: calls.append((args, kw)) or "done")
    assert api.exit_session(SID, dry_run=True, env={"A": "1"}) == "done"
    assert calls == [((SID,), {"dry_run": True, "caller_env": {"A": "1"}})]


@pytest.mark.parametrize("harness, version, refused", [
    ("codex", "0.2.4", True),
    ("codex", "0.2.5", True),
    ("codex", "0.2.6", False),
    ("codex", "0.3.0.dev1", False),
    ("claude", "0.2.4", False),
])
def test_a_codex_fork_needs_a_turns_whose_host_forks(monkeypatch, harness, version, refused):
    from types import SimpleNamespace

    from moxi import modules

    monkeypatch.setattr(modules, "load",
                        lambda name, **kw: SimpleNamespace(ok=True, version=version))
    got = fork.host_refusal(harness)
    assert (got is not None) == refused
    if refused:
        assert got.code == "not-yet" and "moxi-turns 0.2.6 or newer" in str(got)


def test_a_codex_fork_with_an_older_turns_is_refused_with_nothing_written(
        work, harness, no_exec, monkeypatch):
    from types import SimpleNamespace

    from moxi import modules

    conn = store.connect()
    try:
        store.insert(conn, request_id="r1", by="ann", harness="codex", target="terminal",
                     cwd=str(work), values={"harness": "codex", "cwd": str(work),
                                            "mode": "host"},
                     argv=["host"], env={}, posture="", session_id=None)
        store.learn_session(conn, "r1", "thread-7", "startup")
    finally:
        conn.close()
    real = modules.load
    monkeypatch.setattr(modules, "load", lambda name, **kw: (
        SimpleNamespace(ok=True, version="0.2.5") if name == "turns" else real(name, **kw)))
    with pytest.raises(catalog.Refused, match="needs moxi-turns 0.2.6") as raised:
        fork.fork("thread-7", request_id="r2")
    assert raised.value.code == "not-yet" and row_absent("r2")


def row_absent(rid: str) -> bool:
    conn = store.connect()
    try:
        return store.get(conn, rid) is None
    finally:
        conn.close()
