"""Shared fixtures: a fake tmux, a recorded tmux path, the socket fence and a private
server.

The socket fence is on for every test: any tmux call slots makes must name, with `-S`,
the exact socket `moxi.paths.socket_path` gives under the test's own `MOXI_HOME`, or the
test fails. A `-V` call, which reaches no server, is the one call without `-S`. So no test
reaches a live server.

`server` starts a private server the way the supervisor would: tmux's path recorded,
`tmux_conf` set to /dev/null, `moxi-slots-server` run as a child. It needs tmux 3.2 or
newer on PATH and is skipped, with the reason, where there is none. At teardown it kills
that server and removes its socket.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from moxi import paths
from moxi_slots import places, tmux
from moxi_slots.settings import SETUP_BY
from slots_kit import Server, kill, real_tmux, record, start_server, wait_answer, write_tool


@pytest.fixture(autouse=True)
def home(fence, monkeypatch) -> Path:
    """`MOXI_HOME` in the test's short directory, and no tmux variables."""
    root = fence.short / "moxi"
    root.mkdir()
    monkeypatch.setenv("MOXI_HOME", str(root))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(fence.short))
    return root


@pytest.fixture(autouse=True)
def socket_fence(home, monkeypatch) -> list[list[str]]:
    """Every tmux argv slots runs, in order; a call naming another socket fails."""
    expected = paths.socket_path(places.MODULE, places.SOCKET_NAME)
    real = tmux._run
    seen: list[list[str]] = []

    def fenced(argv, timeout, env):
        seen.append(list(argv))
        if argv[1:2] == ["-V"]:
            return real(argv, timeout, env)
        if argv[1:3] != ["-S", expected] or "-L" in argv:
            pytest.fail(f"a tmux call outside the test's socket: {argv}")
        if "TMUX" in env or "TMUX_PANE" in env:
            pytest.fail(f"a tmux call with the caller's TMUX variables: {argv}")
        return real(argv, timeout, env)

    monkeypatch.setattr(tmux, "_run", fenced)
    return seen


@pytest.fixture
def fake_tmux(fence) -> callable:
    """`fake_tmux(version="tmux 3.4", name="tmux")`: a script that prints `version` for
    `-V`, in a bin directory of its own; returns its path."""
    def make(version: str = "tmux 3.4", name: str = "tmux", where: str = "bin") -> Path:
        return write_tool(fence.short / where / name,
                          f'if [ "$1" = "-V" ]; then echo "{version}"; exit 0; fi\nexit 1\n')
    return make


@pytest.fixture
def tmux_path() -> str:
    """tmux's absolute path recorded as `SETUP` records it; skips without tmux 3.2+."""
    found, why = real_tmux()
    if found is None:
        pytest.skip(why)
    path = os.path.abspath(found)
    record("tmux", path, by=SETUP_BY)
    record("tmux_conf", "/dev/null")
    return path


@pytest.fixture
def server(tmux_path) -> Server:
    """A private server on the test's socket, started as the supervisor would start it."""
    socket = places.socket()
    proc = start_server()
    try:
        wait_answer()
        yield Server(tmux_path, socket, proc)
    finally:
        kill(tmux_path, socket, proc)


@pytest.fixture
def stop_after(tmux_path):
    """Kill whatever server answers on the test's socket at teardown (a server the test
    started some other way)."""
    yield
    pid = tmux.ping(timeout=1.0)
    kill(tmux_path, places.socket(), None)
    if pid is not None:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
