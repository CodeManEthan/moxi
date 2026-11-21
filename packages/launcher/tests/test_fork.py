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
    assert (got["name"], got["posture"], got["brief"], got["identity"], got["director"],
            got["warn_at"], got["keep_warm"], got["remote_control"], got["native_name"]) == \
        ("", "", "", "", [], "", "off", False, "")
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


def test_a_forks_environment_holds_nothing_of_the_asking_session(work, harness, no_exec,
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
