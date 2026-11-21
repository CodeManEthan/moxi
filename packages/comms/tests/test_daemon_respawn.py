"""The daemon replaces a Claude session's adapter that died while `claude` runs, at once.

Each unit of `adapters.respawn`'s daemon side is driven here with stand-ins for the
starter: the drop's wait for a socket that stops answering, the session's environment
read from `claude`'s `/proc` entry and its refusals, the choice between forking the starter
and running it under `systemd-run`, the cap, the sweep at the daemon's start, and the
one-shot note the listener prints. The daemon's own trigger is driven through the rig: a
bound claim's drop reaches the respawner, a refused bind's does not, and a stopping
daemon's does not. The whole path, from a killed adapter to a new one bound with no prompt,
is in `test_claude_launch.py`.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time

import pytest
from conftest import FakeClock, wait_until

from moxi_comms import claimfile, listener, paths
from moxi_comms.adapters import respawn, word
from moxi_comms.claimhold import CLAIM_SOCKET_ENV
from moxi_comms.constants import NAME_ENV
from moxi_comms.membership import DEFAULT_PROC, TREE

KEY = "5e55a1d0-0000-4000-8000-0000000000c9"


def record_for(pid=None, *, kind=respawn.CLAUDE_KIND, sock=None, start=None) -> dict:
    """A claim file's record rooted at `pid` (this process by default)."""
    pid = os.getpid() if pid is None else pid
    start = DEFAULT_PROC.start_time(pid) if start is None else start
    return {"kind": kind, "socket": str(sock or paths.claim_socket(KEY)),
            "claim": {"qualified": "planner", "name": "planner"}, "state": "bound",
            "roots": [{"pid": pid, "start": start, "reach": TREE}]}


class Starts:
    """A stand-in for `respawn_adapter`: records each call, answers `BOUND`."""

    def __init__(self, answer=(word.BOUND, "")):
        self.calls: list = []
        self.answer = answer

    def __call__(self, env, key, record, *, wait_s, stopped_at, systemd_run):
        self.calls.append({"env": env, "key": key, "stopped_at": stopped_at,
                           "systemd_run": systemd_run})
        return self.answer


@pytest.fixture
def starts(monkeypatch) -> Starts:
    monkeypatch.setattr(respawn, "session_env", lambda record: {"stand-in": "1"})
    return Starts()


def respawner(starts, **kw) -> respawn.Respawner:
    kw.setdefault("gone_wait_s", 1.0)
    kw.setdefault("poll_s", 0.05)
    return respawn.Respawner(threading.Event(), systemd_run=None, start=starts, **kw)


class Listening:
    """A Unix socket that answers connects at `path` until closed."""

    def __init__(self, path):
        self.path = str(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(16)

    def close(self) -> None:
        self.sock.close()
        os.unlink(self.path)


# ------------------------------------------------------------------ the trigger

def test_a_dropped_claude_adapter_whose_socket_answers_no_one_is_replaced(starts):
    claimfile.write(KEY, record_for())
    said = respawner(starts).after_drop(KEY, "1700000000")
    assert said == (word.BOUND, "")
    (call,) = starts.calls
    assert call["key"] == KEY and call["stopped_at"] == "1700000000"
    assert call["env"] == {"stand-in": "1"} and call["systemd_run"] is None


def test_a_dying_adapter_that_still_answers_a_moment_is_waited_for(starts):
    """The connection's end can come before the dying adapter's claim socket closes:
    the drop reads the socket until it stops answering."""
    held = Listening(paths.claim_socket(KEY))
    claimfile.write(KEY, record_for())
    threading.Timer(0.3, held.close).start()
    began = time.monotonic()
    assert respawner(starts, gone_wait_s=5.0).after_drop(KEY, "-") == (word.BOUND, "")
    assert time.monotonic() - began >= 0.25
    assert len(starts.calls) == 1


def test_an_adapter_that_keeps_answering_is_left_to_bind_again_itself(starts):
    held = Listening(paths.claim_socket(KEY))
    try:
        claimfile.write(KEY, record_for())
        assert respawner(starts, gone_wait_s=0.3).after_drop(KEY, "-") is None
    finally:
        held.close()
    assert starts.calls == []


def test_a_drop_whose_claude_has_ended_starts_nothing(starts):
    claimfile.write(KEY, record_for(start="0"))
    assert respawner(starts).after_drop(KEY, "-") is None
    assert starts.calls == []


@pytest.mark.parametrize("kind", ["codex", "srv"])
def test_a_drop_of_any_other_kind_or_no_file_starts_nothing(starts, kind):
    assert respawner(starts).after_drop(KEY, "-") is None, "no claim file"
    claimfile.write(KEY, record_for(kind=kind))
    assert respawner(starts).after_drop(KEY, "-") is None
    assert starts.calls == []


def test_a_stopping_daemon_starts_nothing(starts):
    claimfile.write(KEY, record_for())
    r = respawner(starts)
    r.stopping.set()
    assert r.after_drop(KEY, "-") is None
    assert r.replace(KEY, record_for(), "-") is None
    assert starts.calls == []


# ------------------------------------------------------------------ the environment

@pytest.fixture
def stand_in_claude():
    """A process started with an environment of its own: `(proc, env)`."""
    sock = str(paths.claim_socket(KEY))
    env = {"PATH": os.environ.get("PATH", ""), NAME_ENV: "planner",
           CLAIM_SOCKET_ENV: sock, "MOXI_COMMS_MCP_TOKEN": "secret-token-value"}
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            env=env)
    yield proc, env
    proc.kill()
    proc.wait()


def test_the_sessions_env_is_its_claudes_own(stand_in_claude):
    proc, env = stand_in_claude
    assert wait_until(lambda: DEFAULT_PROC.start_time(proc.pid))
    got = respawn.session_env(record_for(proc.pid))
    assert {k: got.get(k) for k in env} == env


@pytest.mark.parametrize("change,says", [
    ({NAME_ENV: None}, "no comms identity stamp"),
    ({CLAIM_SOCKET_ENV: "/elsewhere/claim-x.sock"}, "another claim socket"),
    ({CLAIM_SOCKET_ENV: None}, "another claim socket"),
])
def test_an_env_without_the_sessions_stamps_is_refused(change, says, caplog):
    sock = str(paths.claim_socket(KEY))
    env = {"PATH": os.environ.get("PATH", ""), NAME_ENV: "planner",
           CLAIM_SOCKET_ENV: sock, "MOXI_COMMS_MCP_TOKEN": "secret-token-value"}
    env.update(change)
    env = {k: v for k, v in env.items() if v is not None}
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            env=env)
    try:
        assert wait_until(lambda: DEFAULT_PROC.start_time(proc.pid))
        with pytest.raises(respawn.CannotRespawn, match=says) as raised:
            respawn.session_env(record_for(proc.pid))
        assert "secret-token-value" not in str(raised.value)
        caplog.set_level(logging.DEBUG)
        claimfile.write(KEY, record_for(proc.pid))
        respawn.Respawner(threading.Event(), systemd_run=None,
                          start=Starts()).replace(KEY, record_for(proc.pid), "-")
        assert says in caplog.text
        assert "secret-token-value" not in caplog.text and sock not in caplog.text
    finally:
        proc.kill()
        proc.wait()


def test_an_env_whose_claude_has_ended_or_was_replaced_is_refused():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    start = DEFAULT_PROC.start_time(proc.pid) or "1"
    proc.wait()
    with pytest.raises(respawn.CannotRespawn, match="has ended"):
        respawn.session_env(record_for(proc.pid, start=start))
    with pytest.raises(respawn.CannotRespawn, match="has ended"):
        respawn.session_env(record_for(os.getpid(), start="0"))


# ------------------------------------------------------------------ systemd-run

def test_the_starter_is_forked_or_run_in_a_scope_of_its_own():
    from types import SimpleNamespace

    root = SimpleNamespace(pid=4242, start="99")
    plain = respawn.starter_argv(KEY, root, 7)
    assert plain == [sys.executable, "-I", "-m", "moxi_comms.adapters.respawn", KEY,
                     "4242", "99", "7"]
    assert respawn.starter_argv(KEY, root, 7, stopped_at="-") == [*plain, "-"]
    scoped = respawn.starter_argv(KEY, root, 7, stopped_at="17",
                                  systemd_run="/usr/bin/systemd-run")
    assert scoped == ["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect",
                      f"--unit=moxi-comms-adapter-{KEY}.scope", *plain, "17"]


@pytest.mark.parametrize("key,unit", [
    (KEY, f"moxi-comms-adapter-{KEY}.scope"),
    ("a b/c", "moxi-comms-adapter-a\\x20b\\x2fc.scope"),
    (".x.y", "moxi-comms-adapter-\\x2ex.y.scope"),
])
def test_the_scope_unit_is_named_for_the_claim_key_and_unit_safe(key, unit):
    assert respawn.unit_name(key) == unit


@pytest.mark.parametrize("cgroup,served", [
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
     "moxi-comms-daemon.service\n", True),
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/tmux-spawn-1.scope\n",
     False),
    ("12:pids:/x\n0::/\n", False),
])
def test_a_daemon_in_a_service_is_told_from_its_cgroup(tmp_path, cgroup, served):
    path = tmp_path / "cgroup"
    path.write_text(cgroup)
    assert respawn.in_service(str(path)) is served


def test_systemd_run_is_used_only_in_a_service_and_only_where_found(monkeypatch):
    monkeypatch.setattr(respawn.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(respawn, "in_service", lambda: True)
    assert respawn.Respawner(threading.Event()).systemd_run == "/bin/systemd-run"
    monkeypatch.setattr(respawn, "in_service", lambda: False)
    assert respawn.Respawner(threading.Event()).systemd_run is None
    monkeypatch.setattr(respawn, "in_service", lambda: True)
    monkeypatch.setattr(respawn.shutil, "which", lambda name: None)
    assert respawn.Respawner(threading.Event()).systemd_run is None


def test_the_scopes_env_reaches_the_user_manager(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/7")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/7/bus")
    assert respawn.with_user_manager({"A": "1"}) == {
        "A": "1", "XDG_RUNTIME_DIR": "/run/user/7",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/7/bus"}
    own = {"XDG_RUNTIME_DIR": "/run/user/8"}
    assert respawn.with_user_manager(own)["XDG_RUNTIME_DIR"] == "/run/user/8"


def test_a_scoped_respawn_runs_the_starter_under_systemd_run_with_the_session_env(
        monkeypatch, tmp_path):
    from types import SimpleNamespace

    seen = {}
    real = subprocess.Popen

    def popen(argv, **kwargs):
        seen["argv"], seen["env"], seen["cwd"] = argv, kwargs["env"], kwargs["cwd"]
        return real([sys.executable, "-c", "pass"], **kwargs)

    monkeypatch.setattr(respawn, "claude_root",
                        lambda record: SimpleNamespace(pid=os.getpid(), start="0"))
    monkeypatch.setattr(respawn, "session_dir", lambda key, env: tmp_path)
    monkeypatch.setattr(respawn.subprocess, "Popen", popen)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/7")
    said = respawn.respawn_adapter({"S": "1"}, "k", {"kind": respawn.CLAUDE_KIND},
                                   wait_s=2.0, stopped_at="-", systemd_run="/x/systemd-run")
    assert said == (word.FAILED, ""), "the stand-in leaves with no word"
    assert seen["argv"][:6] == ["/x/systemd-run", "--user", "--scope", "--quiet",
                                "--collect", "--unit=moxi-comms-adapter-k.scope"]
    assert seen["argv"][6:9] == [sys.executable, "-I", "-m"]
    assert seen["env"]["S"] == "1" and seen["env"]["XDG_RUNTIME_DIR"] == "/run/user/7"
    assert seen["cwd"] == tmp_path


def stand_in_popen(monkeypatch, tmp_path, scope_argv):
    """Popen with the scope's run replaced by `scope_argv` (or an OSError when None) and
    the plain starter by one that says `BOUND` on its ready fd: the argvs it was given."""
    from types import SimpleNamespace

    seen = []
    real = subprocess.Popen

    def popen(argv, **kwargs):
        seen.append(argv)
        if argv[0] == "/x/systemd-run":
            if scope_argv is None:
                raise FileNotFoundError(2, "No such file or directory")
            return real(scope_argv, **kwargs)
        fd = argv[7]
        return real([sys.executable, "-c",
                     f"import os; os.write({fd}, b'B'); os.close({fd})"], **kwargs)

    monkeypatch.setattr(respawn, "claude_root",
                        lambda record: SimpleNamespace(pid=os.getpid(), start="0"))
    monkeypatch.setattr(respawn, "session_dir", lambda key, env: tmp_path)
    monkeypatch.setattr(respawn.subprocess, "Popen", popen)
    return seen


def test_a_failed_systemd_run_is_logged_with_its_reason_and_the_starter_is_forked(
        monkeypatch, tmp_path, caplog):
    seen = stand_in_popen(monkeypatch, tmp_path, [
        "sh", "-c", "echo 'Failed to connect to user scope bus via local transport:' >&2;"
                    " echo '  No such file or directory' >&2; exit 1"])
    caplog.set_level(logging.INFO, logger="comms.claude.respawn")
    said = respawn.respawn_adapter({"S": "secret-token-value"}, "k",
                                   {"kind": respawn.CLAUDE_KIND}, wait_s=5.0,
                                   stopped_at="-", systemd_run="/x/systemd-run")
    assert said == (word.BOUND, ""), "the forked starter's word"
    assert [argv[0] for argv in seen] == ["/x/systemd-run", sys.executable]
    assert "-" == seen[1][-1], "the fork still asks for the note"
    assert ("(exit 1: Failed to connect to user scope bus via local transport: No such "
            "file or directory)") in caplog.text
    assert "forking it in this daemon's cgroup instead" in caplog.text
    assert "the sweep at the daemon's next start replaces it" in caplog.text
    assert "secret-token-value" not in caplog.text


def test_a_systemd_runs_long_stderr_is_cut(monkeypatch, tmp_path, caplog):
    stand_in_popen(monkeypatch, tmp_path, [
        "sh", "-c", "head -c 5000 /dev/zero | tr '\\0' x >&2; exit 3"])
    caplog.set_level(logging.INFO, logger="comms.claude.respawn")
    respawn.respawn_adapter({}, "k", {"kind": respawn.CLAUDE_KIND}, wait_s=5.0,
                            systemd_run="/x/systemd-run")
    (line,) = [r.getMessage() for r in caplog.records if "systemd-run" in r.getMessage()]
    assert "exit 3: " + "x" * (respawn.SYSTEMD_RUN_STDERR_CHARS - 3) + "...)" in line
    assert "x" * respawn.SYSTEMD_RUN_STDERR_CHARS not in line


def test_a_systemd_run_that_cannot_be_run_falls_back_to_a_fork(
        monkeypatch, tmp_path, caplog):
    seen = stand_in_popen(monkeypatch, tmp_path, None)
    caplog.set_level(logging.INFO, logger="comms.claude.respawn")
    said = respawn.respawn_adapter({}, "k", {"kind": respawn.CLAUDE_KIND}, wait_s=5.0,
                                   systemd_run="/x/systemd-run")
    assert said == (word.BOUND, "")
    assert len(seen) == 2
    assert "(it could not be run: No such file or directory)" in caplog.text


def test_a_scoped_starter_that_ran_is_not_forked_again(monkeypatch, tmp_path, caplog):
    """A starter that ran under the scope and said its word, or ended with a word
    unsaid but exit 0, is not a failed systemd-run."""
    seen = stand_in_popen(monkeypatch, tmp_path, ["sh", "-c", "exit 0"])
    caplog.set_level(logging.INFO, logger="comms.claude.respawn")
    said = respawn.respawn_adapter({}, "k", {"kind": respawn.CLAUDE_KIND}, wait_s=5.0,
                                   systemd_run="/x/systemd-run")
    assert said == (word.FAILED, "") and len(seen) == 1
    assert "systemd-run" not in caplog.text


# ------------------------------------------------------------------ the cap

def test_at_most_three_respawns_per_claim_in_ten_minutes_and_one_line_past_it(
        starts, caplog):
    clock = FakeClock(1000.0)
    r = respawner(starts, clock=clock)
    caplog.set_level(logging.INFO, logger="comms.claude.respawn")
    record = record_for()
    for _ in range(3):
        assert r.replace(KEY, record, "-") == (word.BOUND, "")
        clock.advance(60)
    assert r.replace(KEY, record, "-") is None
    assert r.replace(KEY, record, "-") is None
    assert len(starts.calls) == 3
    assert caplog.text.count("replaces it no more for now") == 1
    assert r.replace("other-key", record, "-") == (word.BOUND, ""), "the cap is per key"
    clock.advance(respawn.RESPAWN_WINDOW_S - 3 * 60 + 1)
    assert r.replace(KEY, record, "-") == (word.BOUND, ""), "the oldest left the window"
    assert len(starts.calls) == 5


# ------------------------------------------------------------------ the start sweep

def test_the_start_sweep_replaces_each_gone_claude_adapter_once(starts):
    claimfile.write(KEY, record_for())
    claimfile.write("codex-1", record_for(kind="codex"))
    claimfile.write("ended", record_for(start="0"))
    live = Listening(paths.claim_socket("live"))
    try:
        claimfile.write("live", record_for(sock=paths.claim_socket("live")))
        r = respawner(starts)
        r.sweep().join(10)
    finally:
        live.close()
    assert [(c["key"], c["stopped_at"]) for c in starts.calls] == [(KEY, "-")]


def test_a_started_daemon_sweeps_when_it_respawns_adapters(rig, monkeypatch):
    from moxi_comms.daemon import Daemon

    swept = []
    monkeypatch.setattr(respawn.Respawner, "sweep", lambda self: swept.append(self))
    rig.daemon.stop()
    rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False,
                        respawn_adapters=True).start()
    assert len(swept) == 1 and swept[0].stopping is rig.daemon._stop


# ------------------------------------------------------------------ the note

def session_env_for(key) -> dict:
    return {**os.environ, CLAIM_SOCKET_ENV: str(paths.claim_socket(key)),
            NAME_ENV: "planner"}


def test_the_new_adapters_note_is_printed_once_at_the_next_event():
    session_dir = respawn.session_dir(KEY, dict(os.environ))
    env = session_env_for(KEY)
    on_word = respawn.note_writer(KEY, session_dir, "1700000000")
    claimfile.write(KEY, record_for())
    on_word(word.BOUND, "")
    on_word(word.FAILED, "the adapter ended")
    at = time.strftime("%H:%M", time.localtime(1700000000))
    line = (f"comms: this session's comms adapter had stopped at {at}. A new one has "
            "bound planner again, and its mail resumes.")
    assert (session_dir / respawn.NOTE).read_text() == line + "\n"
    assert oct((session_dir / respawn.NOTE).stat().st_mode & 0o777) == "0o600"
    assert listener.take_note(env) == line
    assert listener.take_note(env) is None, "once"
    assert list(session_dir.iterdir()) == []


@pytest.mark.parametrize("said,starts_with", [
    ((word.REFUSED, "planner is already bound"),
     "comms: mail has stopped for planner. This session's comms adapter is gone, and a "
     "new one could not bind it: planner is already bound"),
    ((word.OFFLINE, ""), "comms: this session's comms adapter had stopped. A new one "
                         "serves it and binds planner when the comms daemon is back."),
])
def test_a_note_says_the_not_bound_lines_the_listener_says(said, starts_with):
    session_dir = respawn.session_dir(KEY, dict(os.environ))
    claimfile.write(KEY, record_for())
    respawn.note_writer(KEY, session_dir, "-")(*said)
    assert listener.take_note(session_env_for(KEY)).startswith(starts_with)


def test_a_new_adapter_that_finds_another_starting_leaves_no_note():
    session_dir = respawn.session_dir(KEY, dict(os.environ))
    respawn.note_writer(KEY, session_dir, "-")(word.STARTING, "")
    assert not (session_dir / respawn.NOTE).exists()


def test_the_listener_prints_the_note_before_the_prompts_deltas(monkeypatch):
    from types import SimpleNamespace

    session_dir = respawn.session_dir(KEY, dict(os.environ))
    respawn.write_note(session_dir, "comms: a note")
    monkeypatch.setattr(listener, "respawn_gone_adapter", lambda env, wait_s: None)
    from moxi_comms.adapters import shim_roster
    monkeypatch.setattr(shim_roster, "delta", lambda *a, **k: ("the deltas", True))
    event = SimpleNamespace(event="prompt", harness="claude", session_id="s",
                            env=session_env_for(KEY))
    assert listener.answer(event) == "comms: a note\nthe deltas"
    assert listener.answer(event) == "the deltas"


def test_a_line_of_the_events_own_respawn_replaces_the_note(monkeypatch):
    from types import SimpleNamespace

    session_dir = respawn.session_dir(KEY, dict(os.environ))
    respawn.write_note(session_dir, "comms: an old note")
    monkeypatch.setattr(listener, "respawn_gone_adapter",
                        lambda env, wait_s: "comms: this event's line")
    from moxi_comms.adapters import shim_roster
    monkeypatch.setattr(shim_roster, "delta", lambda *a, **k: (None, True))
    event = SimpleNamespace(event="prompt", harness="claude", session_id="s",
                            env=session_env_for(KEY))
    assert listener.answer(event) == "comms: this event's line"
    assert not (session_dir / respawn.NOTE).exists()


# ------------------------------------------------------------------ the daemon's trigger

class Drops:
    def __init__(self):
        self.keys: list = []

    def dropped(self, keys):
        self.keys.append(set(keys))

    def sweep(self):
        pass


def test_a_bound_claims_drop_reaches_the_respawner_and_a_refused_binds_does_not(rig):
    drops = Drops()
    rig.daemon.respawner = drops
    held = rig.shim(name="planner", global_=True, claim_key="claim-held")
    assert wait_until(lambda: rig.daemon.bindings.get("planner"))
    refused = rig.shim(name="planner", global_=True, claim_key="claim-refused",
                       start=False)
    refused.WAIT_WHEN_HELD = False
    with pytest.raises(Exception, match="already bound|held"):
        refused.start()
    refused.stop()
    time.sleep(0.3)
    assert drops.keys == [], "a refused bind made no binding, so no drop"
    held.stop()
    assert wait_until(lambda: drops.keys == [{"claim-held"}], timeout=5), drops.keys


def test_a_stopping_daemons_drops_reach_no_respawner(rig):
    drops = Drops()
    rig.daemon.respawner = drops
    rig.shim(name="planner", global_=True, claim_key="claim-held")
    assert wait_until(lambda: rig.daemon.bindings.get("planner"))
    rig.daemon.stop()
    assert drops.keys == []

