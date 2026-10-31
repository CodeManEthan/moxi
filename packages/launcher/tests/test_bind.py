"""comms' wrapper's bind: a launch with an identity waits until the wrapper has bound the
name or ended, and an end before the bind ends the row `failed` with its cause, from the
exit status where the target can read it (a slot's pane) and from comms' state of the
name where it can't (this terminal, through the detached deliverer)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import exit as exits
from moxi import modules, proc

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from moxi_launcher import api, first, slots, store, targets
from moxi_launcher.api import LaunchRequest

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


class DaemonError(Exception):
    """Stands in for comms' DaemonError."""


def comms(*answers):
    """A comms whose `identity_state` gives `answers` in turn, the last repeated (a state
    name, a state, or an error it raises), and the asks it was given."""
    asked = []

    def identity_state(name, scope=None):
        asked.append((name, scope))
        answer = answers[min(len(asked), len(answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return state(answer) if isinstance(answer, str) else answer

    return SimpleNamespace(identity_state=identity_state, DaemonError=DaemonError,
                           asked=asked)


def state(name, **fields):
    return SimpleNamespace(**{"qualified": "coder@moxi", "state": name, "holder": None,
                              "module": None, "reason": None, **fields})


class Holder:
    def __init__(self, session_id):
        self.adapter, self.session_id = "claude", session_id

    def __str__(self):
        return f"claude session {self.session_id}"


def ended_pid() -> int:
    """The pid of a process that has ended."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def launch_row(conn, *, rid="r1", identity="coder", scope="moxi", pid=None,
               harness="claude", session_id=SID, brief=None):
    store.insert(conn, request_id=rid, actor="ann", harness=harness, target="terminal",
                 cwd="/work", values={}, argv=["moxi", "comms", harness], env={},
                 posture="", identity=identity, scope=scope, brief=brief,
                 session_id=session_id if harness == "claude" else None)
    pid = os.getpid() if pid is None else pid
    me = proc.identity(pid)
    store.set_state(conn, rid, "started", pid=pid,
                    pid_started=me.started if me is not None else 1.0)


def row(conn, rid="r1"):
    return store.get(conn, rid)


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


def settle(conn, fake_comms, monkeypatch, **kw):
    monkeypatch.setattr(targets, "_comms", lambda: fake_comms)
    kw.setdefault("sleep", lambda s: None)
    return targets.settle_bind(conn, "r1", **kw)


# -- the wait ------------------------------------------------------------------------------


def test_a_name_comms_says_is_held_by_this_session_is_bound(conn, monkeypatch):
    launch_row(conn)
    got = settle(conn, comms("free", state("held", holder=Holder(SID))), monkeypatch)
    assert got is None and row(conn).state == "started"


def test_a_name_held_before_its_holder_reports_an_id_is_bound(conn, monkeypatch):
    launch_row(conn)
    assert settle(conn, comms(state("held", holder=Holder(None))), monkeypatch) is None


def test_a_name_held_by_another_session_is_not_this_launch_bind(conn, monkeypatch):
    launch_row(conn)
    ticks = iter(range(100))
    slept = []
    fake_comms = comms(state("held", holder=Holder("someone-else")))
    got = settle(conn, fake_comms, monkeypatch, wait_s=3, clock=lambda: next(ticks),
                 sleep=slept.append)
    # Another session's hold is no bind, so the wait runs to its bound, asking at each
    # tick: the deadline is tick 3 and the clock is read at ticks 1, 2 and 3.
    assert len(fake_comms.asked) == 3 and slept == [targets.BIND_POLL_S] * 2
    # It runs out with the process alive: the wrapper starts the harness anyway.
    assert got is None and row(conn).state == "started"


def test_a_ready_session_ends_the_wait(conn, monkeypatch):
    launch_row(conn)
    store.set_state(conn, "r1", "ready")
    fake_comms = comms("free")
    assert settle(conn, fake_comms, monkeypatch) is None


def test_a_launch_without_an_identity_does_not_wait(conn, monkeypatch):
    launch_row(conn, identity=None, scope=None)
    fake_comms = comms("free")
    assert settle(conn, fake_comms, monkeypatch) is None and fake_comms.asked == []


# -- an end before the bind ----------------------------------------------------------------


@pytest.mark.parametrize("answer, reason, code, said", [
    (state("offline", reason="no daemon socket"), targets.COMMS_OFFLINE, exits.TEMPFAIL,
     "the comms daemon can't be asked: no daemon socket"),
    (state("held", holder=Holder("other")), targets.IDENTITY_TAKEN, exits.REFUSED,
     "identity coder@moxi is held by claude session other"),
    (state("reserved", module="launcher"), targets.IDENTITY_TAKEN, exits.REFUSED,
     "identity coder@moxi is reserved for launcher"),
    (state("free"), targets.COMMS_FAILED, exits.ERROR, None),
    (DaemonError("garbled"), targets.COMMS_FAILED, exits.ERROR,
     "comms could not say why: garbled")])
def test_an_end_in_the_terminal_takes_its_cause_from_comms(conn, monkeypatch, answer,
                                                           reason, code, said):
    launch_row(conn, pid=ended_pid())
    got = settle(conn, comms(answer), monkeypatch)
    assert got.state == "failed" and got.reason == reason and got.exit_code == code
    now = row(conn)
    assert now.state == "failed" and now.reason == reason
    assert "claude did not start" in got.line
    assert "comms' own words are in the terminal it ran in" in got.line
    if said:
        assert said in got.line
    [event] = [e for e in store.events(conn, "r1") if e.kind == "start-failed"]
    assert event.detail["reason"] == reason


@pytest.mark.parametrize("status, reason, code", [
    (75, targets.COMMS_OFFLINE, exits.TEMPFAIL), (3, targets.IDENTITY_TAKEN, exits.REFUSED),
    (1, targets.COMMS_FAILED, exits.ERROR), (2, targets.COMMS_FAILED, exits.ERROR)])
def test_an_end_with_an_exit_status_takes_its_cause_from_the_status(conn, monkeypatch,
                                                                    status, reason, code):
    launch_row(conn, pid=ended_pid())
    got = settle(conn, comms("free"), monkeypatch, status=lambda: status,
                 where="on slot 4's pane")
    assert (got.reason, got.exit_code) == (reason, code)
    assert f"(exit {status})" in got.line and "on slot 4's pane" in got.line
    assert targets.failed_exit(reason) == code


def test_a_codex_launch_names_codex(conn, monkeypatch):
    launch_row(conn, pid=ended_pid(), harness="codex")
    got = settle(conn, comms(state("offline")), monkeypatch)
    assert "codex did not start" in got.line and got.reason == targets.COMMS_OFFLINE


# -- the deliverer, for a launch in this terminal --------------------------------------------


def test_the_deliverer_records_the_wrapper_end_and_sends_nothing(conn, monkeypatch):
    launch_row(conn, pid=ended_pid(), brief="go")
    monkeypatch.setattr(targets, "_comms", lambda: comms(state("offline")))
    monkeypatch.setattr(first, "send", lambda *a, **k: pytest.fail("sent"))
    out = []
    code = first.deliver("r1", pid=row(conn).pid,
                         out=SimpleNamespace(write=out.append, flush=lambda: None))
    assert code == exits.TEMPFAIL and row(conn).reason == targets.COMMS_OFFLINE
    assert "the comms daemon is not running" in "".join(out)


def test_the_deliverer_goes_on_to_the_first_turn_once_bound(conn, monkeypatch):
    launch_row(conn, brief="go")
    monkeypatch.setattr(targets, "_comms",
                        lambda: comms(state("held", holder=Holder(SID))))
    sent = []
    monkeypatch.setattr(first, "send", lambda conn, rid, **k: sent.append(rid) or
                        first.Outcome(rid, "sent", "first-sent"))
    out = SimpleNamespace(write=lambda text: None, flush=lambda: None)
    assert first.deliver("r1", pid=os.getpid(), out=out) == exits.OK and sent == ["r1"]


def test_a_terminal_launch_with_an_identity_starts_the_deliverer(tmp_path, monkeypatch):
    # No first turn owed, but the deliverer watches the bind.
    if not modules.available("comms"):
        pytest.skip("comms is not installed")
    started = []
    monkeypatch.setattr(api, "_comms_api", lambda: comms("free"))
    monkeypatch.setattr(first, "start_deliverer",
                        lambda rid, pid=None, env=None: started.append((rid, pid)) or 1)
    monkeypatch.setattr(targets, "_execve", lambda *a: None)
    composition = api.preview(LaunchRequest(typed={"identity": "coder"},
                                            cwd=str(tmp_path), request_id="r1",
                                            session_id=SID, actor="ann"))
    assert composition.argv[1:3] == ("comms", "claude") and composition.first is None
    monkeypatch.setattr(targets, "find", lambda command, env: command)
    api.launch(LaunchRequest(typed={"identity": "coder"}, cwd=str(tmp_path),
                             request_id="r1", session_id=SID, actor="ann"))
    assert started == [("r1", os.getpid())]


# -- the slot target -----------------------------------------------------------------------


@pytest.fixture
def rig(tmp_path, monkeypatch):
    directory = tmp_path / "fake-slots"
    directory.mkdir()
    monkeypatch.setenv(slots.SEAM, "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(directory))
    monkeypatch.syspath_prepend(str(TESTS))
    fake.reset()
    script = directory / "script.json"
    return SimpleNamespace(plan=lambda **keys: script.write_text(json.dumps(keys)))


def slot_launch(tmp_path, monkeypatch, ask):
    if not modules.available("comms"):
        pytest.skip("comms is not installed")
    monkeypatch.setattr(api, "_comms_api", lambda: comms("free"))
    monkeypatch.setattr(targets, "_comms", lambda: ask)
    monkeypatch.setattr(targets, "find", lambda command, env: command)
    return api.launch(LaunchRequest(typed={"identity": "coder", "scope": "moxi",
                                           "target": "slot"},
                                    cwd=str(tmp_path), request_id="r1", session_id=SID,
                                    actor="ann"))


def test_a_slot_whose_wrapper_refused_the_name_fails_identity_taken(tmp_path, monkeypatch,
                                                                    rig):
    rig.plan(placed=4, pane_pid=ended_pid(), exec="started", dead_status=3)
    got = slot_launch(tmp_path, monkeypatch,
                      comms(state("held", holder=Holder("other"))))
    assert (got.state, got.reason, got.exit_code) == ("failed", targets.IDENTITY_TAKEN,
                                                      exits.REFUSED)
    assert "comms refused coder@moxi" in got.line and "(exit 3)" in got.line
    assert "held by claude session other" in got.line
    assert "on slot 4's pane" in got.line and got.slot == "4"


def test_a_slot_whose_wrapper_found_no_daemon_fails_comms_offline(tmp_path, monkeypatch,
                                                                  rig):
    rig.plan(placed=4, pane_pid=ended_pid(), exec="started", dead_status=75)
    got = slot_launch(tmp_path, monkeypatch, comms(state("offline")))
    assert (got.reason, got.exit_code) == (targets.COMMS_OFFLINE, exits.TEMPFAIL)


def test_a_slot_whose_wrapper_bound_the_name_is_started(tmp_path, monkeypatch, rig):
    rig.plan(placed=4, pane_pid=os.getpid(), exec="started")
    got = slot_launch(tmp_path, monkeypatch, comms(state("held", holder=Holder(SID))))
    assert got.state == "started" and got.reason is None


# -- the composed line against comms' wrapper as built --------------------------------------


def test_comms_codex_wrapper_takes_the_composed_line(tmp_path):
    # Every row a Codex launch with an identity can set, composed and handed to the real
    # `moxi comms codex --dry-run`, which checks the line with its own parser and turns'
    # and starts nothing. turns is hidden from the launcher here, not from the wrapper.
    if not all(importlib.util.find_spec(name) for name in ("moxi_comms", "moxi_turns")):
        pytest.skip("comms and turns are not both installed")
    from moxi import paths

    from moxi_launcher.compose import Context, codex

    try:
        moxi = str(paths.script("moxi"))
    except FileNotFoundError:
        pytest.skip("this environment has no moxi script")
    work = tmp_path / "my.work"
    work.mkdir()
    values = {"harness": "codex", "cwd": str(work), "identity": "coder", "scope": "moxi",
              "director": ["lead"], "model": "gpt-5", "sandbox": "workspace-write",
              "add_dir": [str(tmp_path)], "trust_cwd": True,
              "config": ['model_reasoning_effort="low"']}
    got = codex.compose(values, Context(request_id="r1", actor="ann", moxi=moxi,
                                        modules=frozenset({"turns", "comms"}),
                                        host="/env/bin/moxi-turns-codex-host",
                                        comms_codex=True, brief="b"))
    assert got.argv[1:3] == ("comms", "codex") and "--cwd" not in got.argv
    run = subprocess.run([*got.argv, "--dry-run"], cwd=work, capture_output=True,
                         text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert f"--cwd {work}" in run.stdout and "--model gpt-5" in run.stdout
    assert f'projects."{work}".trust_level="trusted"' in run.stdout
