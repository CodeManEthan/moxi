"""The reads: each row's state from one status call, its on/off, health and run lines."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import moxi.config
from moxi.units import UnitState
from moxi_services import rows, state, store
from moxi_services.rows import ModuleRow

from fake_units import Unit
from services_kit import add, adopted, written

PY = sys.executable


def row(name):
    return store.get(name)


def test_read_asks_the_supervisor_once_for_every_form(fake):
    add(**written("web"))
    add(**adopted("backup"))
    add(**adopted("site", kind="daemon", unit="site.service"))
    fake.written["web"] = Unit("daemon", True, active=True)
    fake.foreign["backup.timer"] = Unit("timer", True)
    fake.modules[("notes", "sync")] = Unit("daemon", False)
    module_row = ModuleRow(module="notes", name="sync", kind="daemon", switch=None,
                           doc="", platforms=None)
    read = state.read([row("web"), row("backup"), row("site"), module_row])
    assert [c for c in fake.calls if c[0] == "status"] == [
        ("status", ("notes", "services"), ("backup.timer", "site.service"))]
    assert read["services/web"].state == "active"
    assert read["services/backup"].state == "active" and read["services/backup"].enabled
    assert read["services/site"].state == "missing"
    assert read["notes/sync"].state == "inactive"


def test_a_row_of_another_system_is_not_read(fake, monkeypatch):
    add(**written("web"))
    monkeypatch.setattr(rows, "sys", SimpleNamespace(platform="darwin"))
    assert state.read([row("web")]) == {"services/web": None}
    assert fake.calls == []


def test_a_written_row_with_no_unit_reads_none(fake):
    add(**written("web"))
    assert state.read_one(row("web")) is None


def test_no_supervisor_reads_nobackend(fake):
    add(**written("web"))
    fake.written["web"] = Unit("daemon")
    fake.supervisor = None
    assert state.read_one(row("web")).state == "nobackend"


def test_sockets_are_not_rows(fake, monkeypatch):
    add(**written("web"))
    socket = UnitState("services", "web", "socket", "systemd", "active")
    daemon = UnitState("services", "web", "daemon", "systemd", "inactive")
    monkeypatch.setattr(fake, "status", lambda modules, foreign=(): [socket, daemon])
    assert state.read_one(row("web")) is daemon


# --- health -----------------------------------------------------------------------------

ACTIVE = UnitState("services", "web", "daemon", "systemd", "active")


def test_health_runs_only_for_an_active_daemon_with_a_check(fake):
    add(**written("web", health=[PY, "-c", "print('fine')"]))
    add(**written("bare"))
    add(**written("tick", kind="timer", every="5m", health=[PY, "-c", "pass"]))
    idle = UnitState("services", "web", "daemon", "systemd", "inactive")
    assert state.health(row("web"), ACTIVE) == ("ok", "fine")
    assert state.health(row("web"), idle) is None
    assert state.health(row("web"), None) is None
    assert state.health(row("bare"), ACTIVE) is None
    assert state.health(row("tick"), ACTIVE) is None


def test_health_non_zero_is_fail_with_its_first_line():
    argv = [PY, "-c", "import sys; print('\\n  db down\\nmore'); sys.exit(2)"]
    assert state.run_health(argv) == ("fail", "db down")
    assert state.run_health([PY, "-c", "import sys; sys.exit(3)"]) == ("fail", "exit 3")


def test_health_reads_stderr_when_stdout_is_empty():
    argv = [PY, "-c", "import sys; sys.stderr.write('no route\\n'); sys.exit(1)"]
    assert state.run_health(argv) == ("fail", "no route")


def test_health_past_its_limit_is_fail(monkeypatch):
    monkeypatch.setattr(state, "HEALTH_SECONDS", 0.3)
    verdict, line = state.run_health([PY, "-c", "import time; time.sleep(30)"])
    assert verdict == "fail" and line.startswith("did not finish in")


def test_health_that_cannot_start_is_fail(tmp_path):
    verdict, line = state.run_health([str(tmp_path / "missing")])
    assert verdict == "fail" and "could not run" in line


MODULE_HEALTH = '''
SUMMARY = "pulse"
DAEMONS = {"beat": {"command": "moxi-pulse-beat"}, "quiet": {"command": "moxi-pulse-quiet"}}
HEALTH = {"beat": "moxi_pulse.checks:beat"}
'''

CHECKS = '''
from moxi.doctor import Row

def beat():
    return Row("pulse", "beat", "warn", "slow beat")
'''


def test_a_module_daemons_health_goes_through_the_doctor(site):
    site.module("pulse", MODULE_HEALTH)
    site.write("moxi_pulse/checks.py", CHECKS)
    beat = ModuleRow(module="pulse", name="beat", kind="daemon", switch=None, doc="",
                     platforms=None)
    quiet = ModuleRow(module="pulse", name="quiet", kind="daemon", switch=None, doc="",
                      platforms=None)
    assert state.health(beat, ACTIVE) == ("warn", "slow beat")
    assert state.health(quiet, ACTIVE) is None


# --- run lines and on/off ---------------------------------------------------------------

def test_last_runs_per_form(fake):
    add(**written("tick", kind="timer", every="5m"))
    add(**written("web"))
    add(**adopted("backup", platform="linux"))
    add(**adopted("nightly", unit="nightly-task", platform="win32"))
    timer = ModuleRow(module="notes", name="snapshot", kind="timer", switch=None, doc="",
                      platforms=None)
    state.last_runs(row("tick"), 3)
    state.last_runs(timer)
    state.last_runs(row("backup"))
    assert state.last_runs(row("web")) == []
    assert state.last_runs(row("nightly")) == []
    assert [c for c in fake.calls if c[0] == "last_runs"] == [
        ("last_runs", "services", "tick", 3), ("last_runs", "notes", "snapshot", 5),
        ("last_runs", "services", "backup", 5)]


def test_flag_per_form():
    add(**written("web", enabled=True))
    add(**adopted("backup", platform="linux", enabled=True))
    add(**adopted("nightly", unit="\\Backups\\nightly", platform="win32", enabled=True))
    assert state.flag(row("web")) is True
    assert state.flag(row("nightly")) is True
    # An adopted Linux timer's on/off is its key, not the store's flag.
    assert state.flag(row("backup")) is False
    moxi.config.set("services", "adopted.backup", True, by="test")
    assert state.flag(row("backup")) is True
    assert state.flag(row("backup"), {"adopted.backup": False}) is False
    assert state.is_wrapped(row("backup")) and not state.is_wrapped(row("nightly"))


def test_a_module_rows_flag_is_its_switch(site):
    site.module("plain", 'SUMMARY = "plain"\n')
    unswitched = ModuleRow(module="plain", name="x", kind="timer", switch=None, doc="",
                           platforms=None)
    switched = ModuleRow(module="plain", name="x", kind="timer", switch="x.on", doc="",
                         platforms=None)
    assert state.flag(unswitched) is True
    assert state.flag(switched) is None  # no settings to read it from


def test_the_adopted_table_is_a_switch_table():
    key = state.schema().keys["adopted"]
    assert moxi.config.is_switch_table(key)
    assert state.schema().entry("adopted.my-timer") == ("adopted", "my-timer")


@pytest.mark.parametrize("value, expect", [("true", True), ("false", False)])
def test_the_key_is_set_and_read_through_the_cores_config(value, expect):
    moxi.config.set("services", "adopted.backup", value, by="test")
    assert state.key_on("backup") is expect
    moxi.config.unset("services", "adopted.backup", by="test")
    assert state.key_on("backup") is False
