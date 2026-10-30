"""slots' doctor rows and the server's health check, on fakes."""

from __future__ import annotations

import os
import time

import pytest

from moxi import units
from moxi.doctor import Row
from moxi.units import UnitState
from moxi_slots import doctor, health, places, server, support, tmux

from slots_kit import record, write_tool

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/doctor.py",
                                 "packages/slots/src/moxi_slots/health.py")


@pytest.fixture(autouse=True)
def linux(monkeypatch):
    monkeypatch.setattr(support, "system", lambda *a: support.LINUX)


def by_name(rows) -> dict[str, Row]:
    return {row.name: row for row in rows}


def test_native_windows_shows_one_off_row(monkeypatch):
    monkeypatch.setattr(support, "system", lambda *a: support.WINDOWS)
    rows = list(doctor.rows())
    assert len(rows) == 1
    assert rows[0].name == "system" and rows[0].state == "off"
    assert "in-terminal and headless" in rows[0].detail


def test_the_rows_on_a_system_slots_runs_on(fake_tmux, monkeypatch):
    record("tmux", str(fake_tmux()))
    monkeypatch.setattr(units, "backend", lambda: None)
    names = [row.name for row in doctor.rows()]
    assert names == ["system", "tmux", "server mode", "options", "socket", "launch files",
                     "trampoline", "mismatches"]


def test_the_tmux_row(fake_tmux, fence):
    row = doctor.tmux_row()
    assert row.state == "fail" and "not recorded" in row.detail
    assert "moxi install slots" in row.fix and "install tmux" in row.fix
    record("tmux", str(fence.short / "gone"))
    assert "not an executable file" in doctor.tmux_row().detail
    record("tmux", str(fake_tmux("tmux 3.1")))
    row = doctor.tmux_row()
    assert row.state == "fail" and "tmux 3.1" in row.detail
    path = fake_tmux("tmux 3.5a", where="new")
    record("tmux", str(path))
    assert doctor.tmux_row() == Row("slots", "tmux", "ok", f"{path} (tmux 3.5a)")


def test_the_server_mode_row(monkeypatch):
    monkeypatch.setattr(units, "backend", lambda: "systemd")
    assert doctor.server_mode_row().state == "ok"
    monkeypatch.setattr(units, "backend", lambda: None)
    row = doctor.server_mode_row()
    assert row.state == "warn" and "first use" in row.detail
    monkeypatch.setattr(support, "system", lambda *a: support.WSL2)
    row = doctor.server_mode_row()
    assert "dies with the WSL VM" in row.detail and "systemd=true" in row.fix


@pytest.mark.parametrize("value, state", [("no", "warn"), ("yes", "ok")])
def test_the_linger_row_on_systemd(fence, monkeypatch, value, state):
    monkeypatch.setattr(units, "backend", lambda: "systemd")
    bin_dir = fence.short / "lbin"
    write_tool(bin_dir / "loginctl", f'echo "Linger={value}"\n')
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("USER", "someone")
    row = doctor.linger_row()
    assert row.state == state
    if value == "no":
        assert row.fix == "loginctl enable-linger someone"


def test_no_linger_row_without_systemd(monkeypatch):
    monkeypatch.setattr(units, "backend", lambda: "launchd")
    assert doctor.linger_row() is None


def test_the_options_row_reads_the_live_values(fake_tmux, monkeypatch):
    record("tmux", str(fake_tmux()))
    calls = []

    def call(*args, timeout):
        calls.append(args)
        return tmux.Result(args, 0, "off\non\noff\n", "")

    monkeypatch.setattr(tmux, "call", call)
    row = doctor.options_row()
    assert row.state == "warn" and "exit-unattached is on, not off" in row.detail
    assert calls[0] == ("show-options", "-s", "-v", "exit-empty", ";", "show-options", "-s",
                        "-v", "exit-unattached", ";", "show-options", "-g", "-v",
                        "destroy-unattached")
    monkeypatch.setattr(tmux, "call", lambda *a, timeout: tmux.Result(a, 0, "off\n" * 3,
                                                                      ""))
    assert doctor.options_row().state == "ok"


def test_the_socket_row_fails_on_an_open_runtime_directory():
    runtime = places.runtime_dir(create=True)
    assert doctor.socket_row().state == "ok"
    runtime.chmod(0o755)
    row = doctor.socket_row()
    assert row.state == "fail" and "0755" in row.detail and row.fix.startswith("chmod 700")


def test_the_launch_files_row_removes_stale_ones():
    directory = places.launch_dir()
    directory.mkdir(parents=True, mode=0o700)
    stale = directory / "a1"
    stale.write_text("{}")
    past = time.time() - 600
    os.utime(stale, (past, past))
    row = doctor.launch_files_row()
    assert row.state == "warn" and "removed 1" in row.detail
    assert not stale.exists()
    assert doctor.launch_files_row().state == "ok"


def test_the_trampoline_row_fails_without_its_script(monkeypatch):
    from moxi import paths

    def missing(name):
        raise FileNotFoundError(f"{name} is not installed in this environment")

    monkeypatch.setattr(paths, "script", missing)
    row = doctor.trampoline_row()
    assert row.state == "fail" and "moxi-slots-exec" in row.detail


def test_the_mismatches_row_counts():
    assert doctor.mismatches_row().detail.startswith("0 saved")
    directory = places.mismatches_dir()
    directory.mkdir(parents=True)
    (directory / "one.txt").write_text("")
    assert doctor.mismatches_row().detail.startswith("1 saved")


def test_further_checks_run_after_the_fixed_rows(fake_tmux, monkeypatch):
    record("tmux", str(fake_tmux()))
    monkeypatch.setattr(units, "backend", lambda: None)
    monkeypatch.setattr(doctor, "CHECKS", [lambda: [Row("slots", "registry", "ok", "x")]])
    assert [row.name for row in doctor.rows()][-1] == "registry"


# -- health ------------------------------------------------------------------------------

def test_health_fails_with_no_answer(monkeypatch):
    monkeypatch.setattr(tmux, "ping", lambda timeout: None)
    row = health.check()
    assert row.state == "fail" and "no tmux server answered" in row.detail


def test_health_compares_the_supervisors_pid(monkeypatch):
    monkeypatch.setattr(tmux, "ping", lambda timeout: 100)
    monkeypatch.setattr(server, "supervised", lambda: True)
    monkeypatch.setattr(server, "unit_state", lambda: UnitState(
        "slots", "server", "daemon", "systemd", "active", pid=100))
    assert health.check().state == "ok"
    monkeypatch.setattr(server, "unit_state", lambda: UnitState(
        "slots", "server", "daemon", "systemd", "active", pid=200))
    row = health.check()
    assert row.state == "fail" and "another tmux server" in row.detail


def test_health_without_a_supervisor_compares_the_recorded_pid(monkeypatch):
    monkeypatch.setattr(tmux, "ping", lambda timeout: 100)
    monkeypatch.setattr(server, "supervised", lambda: False)
    monkeypatch.setattr(server, "recorded_pid", lambda: 100)
    assert health.check().state == "ok"
    monkeypatch.setattr(server, "recorded_pid", lambda: None)
    assert health.check().state == "warn"


def test_health_is_off_on_windows(monkeypatch):
    monkeypatch.setattr(support, "system", lambda *a: support.WINDOWS)
    assert health.check().state == "off"
