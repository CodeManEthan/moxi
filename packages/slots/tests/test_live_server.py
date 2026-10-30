"""The server against a real tmux: each test its own server on its own socket.

Skipped, with the reason, where tmux 3.2 or newer is not on PATH.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from moxi import units
from moxi_slots import doctor, health, places, tmux
from moxi_slots import server as wrapper

from slots_kit import kill, start_server, wait_answer

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/server.py",
                                 "packages/slots/src/moxi_slots/tmux.py",
                                 "packages/slots/src/moxi_slots/health.py",
                                 "packages/slots/src/moxi_slots/doctor.py")


def test_the_wrapper_becomes_tmux_and_keeps_its_pid(server):
    assert tmux.ping() == server.pid
    assert server_pid_file() == server.pid
    assert oct(os.stat(places.runtime_dir()).st_mode & 0o777) == "0o700"
    conf = places.server_conf().read_text()
    assert "source-file -q '/dev/null'" in conf and "/etc/tmux.conf" not in conf


def server_pid_file() -> int | None:
    return int(places.server_pid().read_text())


def test_the_options_slots_relies_on_are_off(server):
    assert doctor.options_row().state == "ok"
    out = tmux.call("show-options", "-g", "-v", "destroy-unattached").out.strip()
    assert out == "off"


def test_the_server_starts_with_a_clean_environment(server):
    out = tmux.call("show-environment", "-g").out.splitlines()
    names = {line.split("=", 1)[0].lstrip("-") for line in out}
    assert "MOXI_HOME" not in names and "TMPDIR" not in names
    # tmux adds PWD, its working directory, itself.
    assert {n for n in names if not n.startswith("LC_")} <= {*wrapper.KEEP, "PWD"}


def test_a_call_from_inside_another_tmux_reaches_this_server(server, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/elsewhere/default,1,0")
    monkeypatch.setenv("TMUX_PANE", "%9")
    assert tmux.ping() == server.pid


def test_health_is_ok_against_the_recorded_pid(server, monkeypatch):
    monkeypatch.setattr(units, "backend", lambda: None)
    assert health.check().state == "ok"


def test_a_second_wrapper_leaves_the_running_server_alone(server):
    second = start_server()
    assert second.wait(timeout=10) == 1
    assert "already answers" in second.stderr.read().decode()
    assert tmux.ping() == server.pid


def test_the_wrapper_removes_launch_files_left_from_before(tmux_path):
    directory = places.launch_dir()
    directory.mkdir(parents=True, mode=0o700)
    (directory / "left").write_text("SECRET=1")
    proc = start_server()
    try:
        wait_answer()
        assert list(directory.iterdir()) == []
    finally:
        kill(tmux_path, places.socket(), proc)


def test_a_stale_socket_file_does_not_stop_a_start(tmux_path):
    proc = start_server()
    wait_answer()
    kill(tmux_path, places.socket(), None)
    proc.wait(timeout=10)
    socket = places.socket()
    assert tmux.ping(timeout=1) is None
    if not os.path.exists(socket):
        open(socket, "w").close()
    proc = start_server()
    try:
        assert wait_answer() == proc.pid
    finally:
        kill(tmux_path, socket, proc)


def test_ensure_starts_the_server_where_no_supervisor_runs(tmux_path, stop_after,
                                                           monkeypatch):
    monkeypatch.setattr(wrapper, "supervised", lambda: False)
    assert tmux.ping(timeout=1) is None
    pid = wrapper.ensure()
    assert tmux.ping() == pid == wrapper.recorded_pid()
    assert wrapper.ensure() == pid


def test_a_server_started_on_demand_outlives_its_caller(tmux_path, stop_after):
    code = ("from moxi_slots import server\n"
            "server.supervised = lambda: False\n"
            "print(server.ensure())\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=30)
    assert done.returncode == 0, done.stderr
    pid = int(done.stdout.strip())
    time.sleep(0.2)
    assert tmux.ping() == pid
