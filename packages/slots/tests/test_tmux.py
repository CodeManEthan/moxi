"""The wrapper over moxi.proc: the argv, the client environment, failures and versions."""

from __future__ import annotations

import subprocess

import pytest

from moxi_slots import places, tmux
from moxi_slots.errors import TmuxError, TryAgain
from moxi_slots.settings import NotSetUp

from slots_kit import record

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/tmux.py",
                                 "packages/slots/src/moxi_slots/settings*")


@pytest.mark.parametrize("text, ok", [
    ("tmux 3.2", True), ("tmux 3.2a", True), ("tmux 3.7c", True), ("tmux 10.0", True),
    ("tmux 3.1c", False), ("tmux 2.9", False), ("tmux next-3.8", True),
    ("tmux master", True), ("tmux openbsd-7.5", False), ("", False), ("bash", False),
])
def test_versions_are_judged_against_the_floor(text, ok):
    assert tmux.judge(text).ok is ok


def test_a_version_that_cannot_be_run_fails_with_why(fence):
    seen = tmux.version(str(fence.short / "no-such-tmux"))
    assert not seen.ok and "could not be run" in seen.text


def test_the_client_environment_drops_the_tmux_variables():
    env = tmux.client_env({"TMUX": "/x,1,0", "TMUX_PANE": "%3", "PATH": "/bin", "A": "1"})
    assert env == {"PATH": "/bin", "A": "1"}


def test_every_call_names_the_socket_and_the_recorded_path(fake_tmux, monkeypatch):
    path = str(fake_tmux())
    record("tmux", path)
    got = []

    def run(argv, timeout, env):
        got.append((argv, timeout, env))
        return subprocess.CompletedProcess(argv, 0, b"ok\n", b"")

    monkeypatch.setattr(tmux, "_run", run)
    monkeypatch.setenv("TMUX", "/elsewhere,1,0")
    result = tmux.call("list-sessions", "-F", "#{session_name}", timeout=3)
    argv, timeout, env = got[0]
    assert argv == [path, "-S", places.socket(), "list-sessions", "-F", "#{session_name}"]
    assert timeout == 3 and "TMUX" not in env
    assert result.ok and result.out == "ok\n"


def test_no_recorded_path_names_the_install(monkeypatch):
    with pytest.raises(NotSetUp, match="moxi install slots"):
        tmux.call("list-sessions")


def test_a_path_that_is_no_longer_there_names_the_install(fence):
    record("tmux", str(fence.short / "gone" / "tmux"))
    with pytest.raises(NotSetUp, match="moxi install slots"):
        tmux.call("list-sessions")


@pytest.mark.parametrize("err", [
    b"no server running on /x/tmux.sock\n",
    b"error connecting to /x/tmux.sock (Connection refused)\n",
])
def test_no_server_is_try_again(fake_tmux, monkeypatch, err):
    record("tmux", str(fake_tmux()))
    monkeypatch.setattr(tmux, "_run", lambda argv, timeout, env:
                        subprocess.CompletedProcess(argv, 1, b"", err))
    with pytest.raises(TryAgain):
        tmux.call("list-sessions")


def test_another_failure_is_a_tmux_error(fake_tmux, monkeypatch):
    record("tmux", str(fake_tmux()))
    monkeypatch.setattr(tmux, "_run", lambda argv, timeout, env:
                        subprocess.CompletedProcess(argv, 1, b"", b"can't find session\n"))
    with pytest.raises(TmuxError, match="can't find session"):
        tmux.call("has-session", "-t", "=slot-1")
    result = tmux.attempt("has-session", "-t", "=slot-1")
    assert result.code == 1 and not result.ok


def test_a_timeout_is_try_again(fake_tmux, monkeypatch):
    record("tmux", str(fake_tmux()))

    def slow(argv, timeout, env):
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(tmux, "_run", slow)
    with pytest.raises(TryAgain, match="within 1 s"):
        tmux.call("list-sessions", timeout=1)
    assert tmux.ping(timeout=1) is None


def test_ping_reads_the_pid(fake_tmux, monkeypatch):
    record("tmux", str(fake_tmux()))
    monkeypatch.setattr(tmux, "_run", lambda argv, timeout, env:
                        subprocess.CompletedProcess(argv, 0, b"4242\n", b""))
    assert tmux.ping() == 4242
    monkeypatch.setattr(tmux, "_run", lambda argv, timeout, env:
                        subprocess.CompletedProcess(argv, 1, b"", b"no server running\n"))
    assert tmux.ping() is None
