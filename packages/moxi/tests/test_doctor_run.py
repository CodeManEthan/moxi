"""The doctor's runner: `moxi doctor`'s checks, `moxi.doctor.health` and the costs, on fake
modules, the fake backend and its simulated supervisor."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
import time
import types

import pytest

import moxi.paths
from moxi import cli, doctor, files, modules, strays, units
from moxi.doctor import Row, _run
from moxi.units import _backend, _state
from moxi.units._fake import FakeBackend, FakeSupervisor
from moxi.units._types import UnitChange, UnitState, Wrapped

SETTINGS = """
[relay.enabled]
type = "bool"
default = false
doc = "Run the relay."
read = "use"

[snapshot.enabled]
type = "bool"
default = false
doc = "Take snapshots."
read = "use"

[port]
type = "int"
default = 8470
doc = "The relay's port."
read = "start"

[rows]
type = "table"
items = "bool"
default = {}
doc = "One switch per row."
read = "use"

[peer]
type = "string"
default = ""
doc = "A peer, through the other module."
read = "use"
needs = "other"
"""

DAEMON = "{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}}"
SOCKET = ("{'relay': {'command': 'moxi-demo-relay', "
          "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
TIMER = ("{'snapshot': {'command': 'moxi-demo-snap', 'every': '5m', "
         "'switch': 'snapshot.enabled'}}")
EDITABLE = {"url": "file:///src", "dir_info": {"editable": True}}


@pytest.fixture
def d(site, tmp_path, monkeypatch):
    """A fake module `demo` with settings, a scripts directory, the fake backend, and no
    strays."""
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    monkeypatch.setattr(strays, "find", lambda *a, **k: [])
    root = tmp_path / "units"
    root.mkdir()
    supervisor = FakeSupervisor(root)
    backend = FakeBackend(root, runner=supervisor)

    def script(name):
        path = scripts / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path

    def module(name="demo", *, settings=SETTINGS, files_=None, **names):
        pkg = f"moxi_{name}"
        if settings is not None:
            site.write(f"{pkg}/settings.toml", settings)
            names.setdefault("SETTINGS", "'settings.toml'")
        for rel, text in (files_ or {}).items():
            site.write(f"{pkg}/{rel}", textwrap.dedent(text))
        site.module(name, **names)
        modules.reset()
        for loaded in [m for m in sys.modules if m.split(".")[0] == pkg]:
            del sys.modules[loaded]

    def installed(name="demo", **names):
        for entry in ("moxi-demo-relay", "moxi-demo-snap"):
            script(entry)
        module(name, **names)
        for change in units.plan(name):
            if change.digest is not None:
                _state.record(name, change.name, change.digest)
        assert all(a.ok for a in units.apply(units.plan(name), say=lambda line: None))
        supervisor.calls.clear()

    def run(*names, **kw):
        return _run.run(list(names) or None, **kw)

    def rows(*names, **kw):
        return {(r.module, r.name): r for r in run(*names, **kw)}

    def state_dir(name="demo"):
        return moxi.paths.Layout(name).state_dir()

    with _backend.use(backend):
        yield types.SimpleNamespace(site=site, scripts=scripts, root=root, sup=supervisor,
                                    script=script, module=module, installed=installed,
                                    run=run, rows=rows, state_dir=state_dir, tmp=tmp_path)


def states(rows):
    return {key: row.state for key, row in rows.items()}


# -- 1. modules --------------------------------------------------------------------------------

def test_present_broken_and_missing_required_modules(d):
    d.module("demo", requires=("moxi-gone>=0.1", "moxi", "requests"))
    d.site.module("bad", dist="other-dist")
    modules.reset()
    got = d.rows()
    assert got[("demo", "module")].state == "ok"
    assert got[("bad", "module")].state == "fail"
    assert "its distribution must be moxi-bad" in got[("bad", "module")].detail
    gone = got[("demo", "requires gone")]
    assert (gone.state, gone.fix) == ("fail", "moxi install gone")
    assert "moxi-gone>=0.1" in gone.detail
    assert not any(name.startswith("requires") and name != "requires gone"
                   for _, name in got)


def test_a_named_module_that_is_not_installed_fails(d):
    got = d.rows("nothere")
    assert got[("nothere", "module")].state == "fail"
    assert got[("nothere", "module")].fix == "moxi install nothere"


# -- 2. settings -------------------------------------------------------------------------------

def test_a_config_file_that_fails_its_schema_fails(d):
    d.module()
    path = moxi.paths.Layout("demo").config_file()
    path.parent.mkdir(parents=True)
    path.write_text("port = 'x'\n")
    row = d.rows()[("demo", "settings")]
    assert row.state == "fail" and "port" in row.detail and row.fix


def test_a_key_set_for_an_absent_module_warns(d):
    d.module()
    path = moxi.paths.Layout("demo").config_file()
    path.parent.mkdir(parents=True)
    path.write_text("peer = 'x'\n")
    got = d.rows()
    assert got[("demo", "settings")].state == "ok"
    row = got[("demo", "setting peer")]
    assert row.state == "warn" and "other" in row.detail
    assert row.fix.startswith("moxi install other")
    d.module("other", settings=None)
    assert ("demo", "setting peer") not in d.rows()


def test_the_core_s_own_settings_are_checked(d):
    got = d.rows("moxi")
    assert got[("moxi", "settings")].state == "ok"


# -- 3. extensions -----------------------------------------------------------------------------

def entry_points(site, dist, text):
    site.write(f"{dist.replace('-', '_')}-0.1.0.dist-info/entry_points.txt",
               textwrap.dedent(text))


def test_an_entry_whose_owner_is_absent_warns_or_is_off_when_optional(d):
    d.module("demo", settings=None, OPTIONAL="{'turns': 'listen to turns'}")
    d.module("plain", settings=None)
    entry_points(d.site, "moxi-demo", """\
        [moxi.modules]
        demo = moxi_demo.module
        [moxi.turns]
        demo = moxi_demo.module:x
        [moxi.cache.timers]
        demo = moxi_demo.module:y
        [moxi.doctor]
        demo = moxi_demo.module:z
        [moxi.git_hooks]
        demo = moxi_demo.module:w
        [moxi.plain]
        demo = moxi_demo.module:v
        """)
    modules.reset()
    got = {k: r for k, r in d.rows().items() if k[1].startswith("extension")}
    assert got[("demo", "extension moxi.turns demo")].state == "off"
    cache = got[("demo", "extension moxi.cache.timers demo")]
    assert (cache.state, cache.fix) == ("warn", "moxi install cache")
    # The core's groups, a group named by no module, and a present owner: no row.
    assert len(got) == 2


# -- 4. units ----------------------------------------------------------------------------------

def test_written_running_units_are_ok_with_the_switch_and_no_run_yet(d):
    d.installed(DAEMONS=DAEMON, TIMERS=TIMER)
    got = d.rows("demo")
    relay = got[("demo", "daemon relay")]
    assert relay.state == "ok" and relay.detail.startswith("up to date; active")
    assert "pid 4242" in relay.detail
    assert got[("demo", "timer snapshot")].state == "ok"
    switch = got[("demo", "timer snapshot switch")]
    assert (switch.state, switch.fix) == ("off", "moxi config set demo snapshot.enabled true")
    assert got[("demo", "timer snapshot last run")].detail == "no run yet"
    # No HEALTH declared: the active daemon's health row says so.
    assert got[("demo", "daemon relay health")].detail == "no health check declared"


def test_an_installed_and_running_module_exits_0_and_a_fail_row_exits_1(d, capsys):
    d.installed(DAEMONS=DAEMON)
    assert cli.main(["doctor", "demo"]) == 0
    out = capsys.readouterr().out
    assert "ok    demo  daemon relay  up to date; active" in out
    doctor.record("demo", "the committer failed", key="commit", fix="repo check")
    assert cli.main(["doctor", "demo"]) == 1
    out = capsys.readouterr().out
    assert ("fail  demo  record commit  the committer failed (at " in out
            and "fix: repo check, then: moxi doctor --clear demo commit" in out)
    assert cli.main(["doctor", "--clear", "demo", "commit"]) == 0
    assert cli.main(["doctor", "demo"]) == 0


def test_json_prints_the_rows_as_a_list_of_objects(d, capsys):
    d.installed(DAEMONS=DAEMON)
    assert cli.main(["doctor", "demo", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {"module": "demo", "name": "module", "state": "ok",
            "detail": "present: moxi-demo 0.1.0", "fix": None} in listed
    assert all(set(item) == {"module", "name", "state", "detail", "fix"} for item in listed)


def test_module_names_restrict_the_rows(d):
    d.installed(DAEMONS=DAEMON)
    d.module("other", settings=None)
    assert {r.module for r in d.run("other")} == {"other"}
    assert {r.module for r in d.run("moxi")} == {"moxi"}
    assert {r.module for r in d.run()} >= {"demo", "other", "moxi"}


def test_the_bad_argument_lists_exit_2(d, capsys):
    assert cli.main(["doctor", "Not_A_Module"]) == 2
    assert cli.main(["doctor", "--clear", "demo"]) == 2
    assert cli.main(["doctor", "--clear", "demo", "k", "--json"]) == 2
    assert cli.main(["doctor", "--clear", "Bad!", "k"]) == 2


def test_plan_actions_map_to_rows(d):
    d.module(DAEMONS=DAEMON, TIMERS=TIMER)
    got = d.rows("demo")
    # Not written: no script for either.
    assert got[("demo", "daemon relay")].state == "fail"
    assert "no moxi-demo-relay" in got[("demo", "daemon relay")].detail
    d.installed(DAEMONS=DAEMON, TIMERS=TIMER)
    (d.root / "moxi-demo-snapshot.timer").unlink()
    (d.root / "moxi-demo-snapshot.service").unlink()
    (d.root / "moxi-demo-relay.service").write_text("[Service]\nExecStart=/bin/other\n")
    got = d.rows("demo")
    assert (got[("demo", "timer snapshot")].state, got[("demo", "timer snapshot")].detail,
            got[("demo", "timer snapshot")].fix) == ("warn", "not installed",
                                                     "moxi install demo")
    foreign = got[("demo", "daemon relay")]
    assert (foreign.state, foreign.fix) == ("warn", "moxi install demo --replace")
    assert foreign.detail == "moxi-demo-relay.service exists and was not written by moxi"
    # The timer is not written, so it has no switch or last-run row.
    assert ("demo", "timer snapshot switch") not in got


def test_a_unit_no_longer_declared_and_a_stale_one_warn(d):
    d.installed(DAEMONS=DAEMON, TIMERS=TIMER)
    path = d.root / "moxi-demo-relay.service"
    path.write_text(path.read_text() + "extra = 1\n")
    d.module(DAEMONS=DAEMON)
    got = d.rows("demo")
    assert got[("demo", "daemon relay")].detail.startswith("stale; active")
    assert got[("demo", "daemon relay")].state == "warn"
    gone = got[("demo", "timer snapshot")]
    assert (gone.state, gone.fix) == ("warn", "moxi install demo")
    assert gone.detail.startswith("no longer declared")


def test_a_stale_socket_names_the_written_port_and_the_setting(d):
    d.installed(DAEMONS=SOCKET)
    path = moxi.paths.Layout("demo").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("port = 8471\n")
    got = d.rows("demo")
    assert got[("demo", "socket relay")].detail.startswith(
        "port 8470 written, setting says 8471")
    assert got[("demo", "socket relay")].fix == "moxi install demo"


def test_a_daemon_switched_off_is_off_and_still_installed_warns(d):
    switched = ("{'relay': {'command': 'moxi-demo-relay', 'switch': 'relay.enabled'}}")
    d.script("moxi-demo-relay")
    d.module(DAEMONS=switched)
    row = d.rows("demo")[("demo", "daemon relay")]
    assert (row.state, row.detail, row.fix) == (
        "off", "switched off", "moxi config set demo relay.enabled true")
    moxi.config.set("demo", "relay.enabled", True, by="test")
    d.installed(DAEMONS=switched)
    moxi.config._change("demo", "relay.enabled", "set", False, by="test")
    row = d.rows("demo")[("demo", "daemon relay")]
    assert row.state == "warn" and row.detail.startswith("switched off, unit still installed")


def test_an_editable_install_s_units_are_information_and_exit_0(d, capsys):
    d.script("moxi-demo-relay")
    d.module(DAEMONS=DAEMON, direct_url=EDITABLE)
    row = d.rows("demo")[("demo", "daemon relay")]
    assert (row.state, row.detail) == ("ok", "editable install: units are not written here")
    assert cli.main(["doctor", "demo"]) == 0


def test_an_off_platform_unit_and_no_supervisor_are_off(d, monkeypatch):
    d.script("moxi-demo-relay")
    d.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'platforms': ['win32']}}")
    row = d.rows("demo")[("demo", "daemon relay")]
    assert row.state == "off" and "not on this system" in row.detail
    d.module(DAEMONS=DAEMON)
    monkeypatch.setattr(_backend, "implementation", lambda: None)
    row = d.rows("demo")[("demo", "daemon relay")]
    assert row.state == "off" and "no supervisor" in row.detail


def test_health_runs_only_for_an_active_daemon(d, monkeypatch):
    d.installed(DAEMONS=DAEMON, HEALTH="{'relay': 'moxi_demo.health:counted'}",
                files_={"health.py": """\
                    import os
                    from moxi.doctor import Row
                    def counted():
                        with open(os.environ["HEALTH_CALLS"], "a") as handle:
                            handle.write("called\\n")
                        return Row("demo", "x", "ok", "fine")
                    """})
    calls = d.tmp / "health-calls"
    monkeypatch.setenv("HEALTH_CALLS", str(calls))
    assert d.rows("demo")[("demo", "daemon relay health")].detail == "fine"
    assert calls.read_text() == "called\n"
    d.sup.state("moxi-demo-relay.service")["active"] = False
    got = d.rows("demo")
    assert got[("demo", "daemon relay")].detail.startswith("up to date; inactive")
    assert ("demo", "daemon relay health") not in got
    assert calls.read_text() == "called\n"


def test_a_failing_units_from_and_a_bad_health_key_are_reported(d):
    d.installed(DAEMONS=DAEMON, HEALTH="{'relay': 'moxi_demo.health:ok', 'typo': 'x.y:z'}",
                UNITS_FROM="'moxi_demo.runtime:units'",
                files_={"health.py": """\
                    from moxi.doctor import Row
                    def ok():
                        return Row("demo", "x", "ok", "fine")
                    """, "runtime.py": "def units():\n    return {}\n"})
    got = d.rows("demo")
    bad = got[("demo", "health typo")]
    assert bad.state == "warn" and "'typo'" in bad.detail
    assert got[("demo", "daemon relay health")].detail == "fine"
    d.module(DAEMONS=DAEMON, UNITS_FROM="'moxi_demo.runtime:units'",
             files_={"runtime.py": "def units():\n    raise RuntimeError('no rows')\n"})
    row = d.rows("demo")[("demo", "UNITS_FROM")]
    assert row.state == "fail" and "RuntimeError: no rows" in row.detail


def test_a_timer_s_newest_run_line_is_its_row(d):
    d.installed(TIMERS=TIMER)
    runs = d.state_dir() / "runs.jsonl"
    for result, code in (("ran", 0), ("ran", 2)):
        files.append_record(runs, {"unit": "demo/snapshot", "at": "2026-10-30T10:00:00Z",
                                   "seconds": 1.5, "result": result, "exit": code})
    row = d.rows("demo")[("demo", "timer snapshot last run")]
    assert (row.state, row.detail) == ("warn", "ran at 2026-10-30T10:00:00Z, exit 2")
    assert row.fix == "journalctl --user -u moxi-demo-snapshot.timer"
    files.append_record(runs, {"unit": "demo/snapshot", "at": "2026-10-30T11:00:00Z",
                               "seconds": 0.0, "result": "unreadable", "exit": 3})
    row = d.rows("demo")[("demo", "timer snapshot last run")]
    assert (row.state, row.fix) == ("fail", "moxi config demo check")


@pytest.mark.parametrize("line, state", [
    ({"result": "ran", "exit": 0, "at": "t", "seconds": 1.2}, "ok"),
    ({"result": "off", "exit": 0, "at": "t"}, "ok"),
    ({"result": "not-started", "exit": 127, "at": "t"}, "fail"),
    (None, "ok"),
])
def test_run_line_states(line, state):
    assert _run._last_row("demo", "timer t", line, None).state == state


def unit_change(action="keep", cause=None, kind="daemon", **kw):
    return UnitChange("demo", "relay", kind, action, cause, (), None, "r", **kw)


@pytest.mark.parametrize("seen, state, fix", [
    ("failed", "fail", "journalctl x"),
    ("inactive", "warn", "journalctl x"),
    ("missing", "warn", "moxi install demo"),
    ("unknown", "warn", None),
    ("active", "ok", None),
])
def test_the_supervisor_s_state_folds_into_a_written_unit_s_row(seen, state, fix):
    read = UnitState("demo", "relay", "daemon", "systemd", seen, log="journalctl x")
    row = _run._unit_row(unit_change(), read)
    assert (row.state, row.fix) == (state, fix)
    assert row.detail.startswith("up to date; ")


def test_a_daemon_declared_not_to_start_is_ok_inactive():
    read = UnitState("demo", "relay", "daemon", "systemd", "inactive")
    row = _run._unit_row(unit_change(entry={"argv": ["/x"], "enabled": False}), read)
    assert (row.state, row.detail) == ("ok", "up to date; inactive: declared enabled = false")


@pytest.mark.parametrize("action, cause, reason, state", [
    ("refuse", "command", "no x here", "fail"),
    ("refuse", "switch", "demo/relay: bad switch", "fail"),
    ("refuse", "socket", "bad socket", "fail"),
    ("refuse", "declaration", "rejected", "fail"),
    ("skip", "declaration", "UNITS_FROM failed: x; left as they are", "fail"),
    ("skip", "platform", "not on this system", "off"),
    ("skip", "nobackend", "no supervisor", "off"),
])
def test_refusals_and_skips_map_to_rows(action, cause, reason, state):
    change = UnitChange("demo", "relay", "daemon", action, cause, (), None, reason)
    row = _run._unit_row(change, None)
    assert (row.state, row.detail) == (state, reason)


def test_a_failed_supervisor_read_is_a_fail_row_and_the_plan_rows_stay(d):
    d.installed(DAEMONS=DAEMON)
    d.sup.fail[("show", None)] = "Failed to connect to bus"
    got = d.rows("demo")
    assert got[("demo", "daemon relay")].state == "warn"
    assert "unknown" in got[("demo", "daemon relay")].detail


# -- HEALTH ------------------------------------------------------------------------------------

HEALTHS = """\
import time
from moxi.doctor import Row
def good():
    return Row("demo", "anything", "warn", "slow replies", "restart it")
def raises():
    raise RuntimeError("socket refused")
def slow():
    time.sleep(2)
def wrong():
    return ["not a row"]
"""


@pytest.mark.parametrize("target, state, words", [
    ("good", "warn", "slow replies"),
    ("raises", "fail", "moxi_demo.health:raises raised RuntimeError: socket refused"),
    ("slow", "fail", "moxi_demo.health:slow did not finish in 0.2 s"),
    ("wrong", "fail", "returned list, not a moxi.doctor.Row"),
])
def test_health_runs_one_target_in_a_worker_thread(d, monkeypatch, target, state, words):
    monkeypatch.setattr(_run, "HEALTH_SECONDS", 0.2)
    d.module(DAEMONS=DAEMON, HEALTH=f"{{'relay': 'moxi_demo.health:{target}'}}",
             files_={"health.py": HEALTHS})
    row = doctor.health("demo", "relay")
    assert (row.module, row.name, row.state) == ("demo", "daemon relay health", state)
    assert words in row.detail
    if target == "good":
        assert row.fix == "restart it"


def test_health_with_no_target_and_an_unknown_daemon(d):
    d.module(DAEMONS=DAEMON)
    assert doctor.health("demo", "relay") == Row("demo", "daemon relay health", "ok",
                                                 "no health check declared")
    with pytest.raises(ValueError, match="no daemon 'other'"):
        doctor.health("demo", "other")
    with pytest.raises(ValueError):
        doctor.health("absent", "relay")


def test_health_reaches_a_daemon_units_from_declares(d, tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("")
    d.module(HEALTH="{'extra': 'moxi_demo.health:good'}", UNITS_FROM="'moxi_demo.rt:units'",
             files_={"health.py": HEALTHS,
                     "rt.py": f"def units():\n    return {{'daemons': {{'extra': "
                              f"{{'argv': [{str(tool)!r}]}}}}}}\n"})
    assert doctor.health("demo", "extra").detail == "slow replies"


# -- the wrap of a foreign timer ---------------------------------------------------------------

def test_a_wrap_shows_the_timer_s_state_its_key_and_its_last_run(d):
    d.installed(DAEMONS=DAEMON)
    d.sup.extra |= {"backup.timer", "backup-run.service"}
    d.sup.triggers["backup.timer"] = "backup-run.service"
    d.sup.reload()
    d.sup.state("backup.timer").update(active=True, enabled=True)
    assert units.wrap("backup.timer", module="demo", key="rows.backup", name="backup").ok
    got = d.rows("demo")
    assert got[("demo", "wrap backup.timer")].state == "ok"
    key = got[("demo", "wrap backup.timer switch")]
    assert (key.state, key.fix) == ("off", "moxi config set demo rows.backup true")
    assert got[("demo", "wrap backup.timer last run")].detail == "no run yet"
    files.append_record(d.state_dir() / "runs.jsonl",
                        {"unit": "demo/backup", "at": "2026-10-30T10:00:00Z",
                         "seconds": 3, "result": "ran", "exit": 0})
    moxi.config.set("demo", "rows.backup", True, by="test")
    got = d.rows("demo")
    assert got[("demo", "wrap backup.timer switch")].state == "ok"
    assert got[("demo", "wrap backup.timer last run")].detail == (
        "ran at 2026-10-30T10:00:00Z, exit 0 in 3 s")
    d.sup.state("backup.timer").update(active=False)
    assert d.rows("demo")[("demo", "wrap backup.timer")].state == "warn"


def test_a_hand_edited_wrap_ref_costs_only_its_own_row(d):
    d.installed(DAEMONS=DAEMON)
    d.sup.state("moxi-demo-relay.service")["active"] = True
    dropin = d.root / "own.service.d" / "50-moxi-demo.conf"
    dropin.parent.mkdir()
    dropin.write_text("# moxi wrap: demo/backup\n"
                      "# moxi: timer = moxi-demo-relay.timer, if = demo.rows.backup\n")
    got = d.rows("demo")
    assert ("demo", "units") not in got
    assert got[("demo", "daemon relay")].state == "ok"
    wrap = got[("demo", "wrap moxi-demo-relay.timer")]
    assert wrap.state == "warn" and "moxi's own unit namespace" in wrap.detail


@pytest.mark.parametrize("seen, state", [("missing", "warn"), ("failed", "fail"),
                                          ("unknown", "warn")])
def test_a_wrap_whose_timer_is_gone_or_failed(d, seen, state):
    d.module()
    wrapped = Wrapped("demo", "rows.backup", "backup")
    read = UnitState(None, "backup.timer", "timer", "systemd", seen, wrapped=wrapped)
    rows = _run._wrap_rows("demo", "backup.timer", wrapped, read)
    assert [r.name for r in rows] == ["wrap backup.timer", "wrap backup.timer switch",
                                      "wrap backup.timer last run"]
    assert rows[0].state == state


# -- 5. stores ---------------------------------------------------------------------------------

STORES = ("{'record': {'root': 'state', 'path': 'record.db', 'kind': 'sqlite', "
          "'private': True, 'retention': 'keep'}, "
          "'feed': {'root': 'state', 'path': 'feed.jsonl', 'kind': 'jsonl', 'lazy': True, "
          "'retention': '1K'}, "
          "'blobs': {'root': 'cache', 'path': 'blobs', 'kind': 'dir'}}")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_store_existence_mode_and_size(d):
    d.module(STORES=STORES, settings=None)
    got = d.rows("demo")
    assert got[("demo", "store record")].state == "warn"
    assert got[("demo", "store record")].detail.endswith("record.db is missing")
    assert got[("demo", "store feed")].detail.endswith("not created yet")
    assert got[("demo", "store feed")].state == "ok"
    state = d.state_dir()
    state.mkdir(parents=True)
    record = state / "record.db"
    record.write_bytes(b"x")
    record.chmod(0o644)
    (state / "feed.jsonl").write_bytes(b"y" * 2048)
    blobs = moxi.paths.Layout("demo").cache_dir() / "blobs"
    blobs.mkdir(parents=True)
    blobs.chmod(0o755)
    got = d.rows("demo")
    row = got[("demo", "store record")]
    assert row.state == "warn" and "others can read it (mode 0644)" in row.detail
    assert row.fix == f"chmod 600 {record}"
    feed = got[("demo", "store feed")]
    assert feed.state == "warn" and "past its cap of 1.0 KB" in feed.detail
    assert got[("demo", "store blobs")].fix == f"chmod 700 {blobs}"
    record.chmod(0o600)
    assert d.rows("demo")[("demo", "store record")].state == "ok"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_core_s_stores_in_a_module_s_state_directory(d):
    d.module(settings=None)
    assert not any(name.startswith("store") for _, name in d.rows("demo"))
    state = d.state_dir()
    state.mkdir(parents=True)
    (state / "runs.jsonl").write_bytes(b"{}\n" * 400_000)
    (state / "logs").mkdir(mode=0o700)
    doctor.record("demo", "x", key="k")
    got = d.rows("demo")
    assert got[("demo", "store runs.jsonl")].state == "warn"
    assert "past its cap" in got[("demo", "store runs.jsonl")].detail
    assert got[("demo", "store logs")].state == "ok"
    assert got[("demo", "store doctor.json")].state == "ok"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_comms_world_readable_record_warns_with_its_chmod(monkeypatch, tmp_path):
    """comms' own STORES line: a record others can read is a warn row with its fix."""
    monkeypatch.setenv("MOXI_COMMS_STATE_HOME", str(tmp_path / "state"))
    found = modules.load("comms")
    assert found.ok
    record = tmp_path / "state" / "comms" / "record.db"
    record.parent.mkdir(parents=True)
    record.write_bytes(b"")
    record.chmod(0o644)
    row = {r.name: r for r in _run.store_rows(found)}["store record"]
    assert (row.state, row.fix) == ("warn", f"chmod 600 {record}")


def test_cache_s_doctor_runs_through_the_runner_with_no_fail_row():
    found = modules.load("cache")
    assert found.ok and found.doctor
    assert not [r for r in _run.doctor_rows(found) if r.state == "fail"]


# -- 6 to 9: home, strays, setup versions, records ---------------------------------------------

def test_a_state_directory_read_in_place_under_a_host_folder_warns(d, monkeypatch):
    d.module(settings=None)
    monkeypatch.setenv("MOXI_DEMO_DEVICE", "oldhost")
    legacy = moxi.paths.Layout("demo").state_root() / "oldhost" / "demo"
    legacy.mkdir(parents=True)
    row = d.rows("demo")[("demo", "home state")]
    assert (row.state, row.fix) == ("warn", "moxi install demo")
    assert str(legacy) in row.detail


def test_a_stray_warns_with_both_commands(d, monkeypatch):
    stray = strays.Stray("demo", "0.0.9", "/tools/moxi-demo", "tool", "moxi-demo")
    monkeypatch.setattr(strays, "find", lambda *a, **k: [stray])
    row = d.rows()[("demo", "stray")]
    assert row.state == "warn" and 'uv tool "moxi-demo"' in row.detail
    assert row.fix == "moxi install demo, then: uv tool uninstall moxi-demo"


@pytest.mark.parametrize("held", [{"demo": "0.0.9"}, {"demo": {"version": "0.0.9"}}])
def test_a_setup_version_that_differs_warns(d, held):
    d.module(settings=None)
    path = moxi.paths.core().state_dir() / "setup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(held))
    row = d.rows("demo")[("demo", "setup version")]
    assert (row.state, row.fix) == ("warn", "moxi install demo")
    path.write_text(json.dumps({"demo": "0.1.0"}))
    assert ("demo", "setup version") not in d.rows("demo")


def test_the_doctor_reads_the_setup_version_the_settle_step_writes(d, monkeypatch):
    from moxi import _settle

    d.module(settings=None)
    _settle.record_setup("demo", "0.0.9")
    assert d.rows("demo")[("demo", "setup version")].state == "warn"
    moved = d.tmp / "elsewhere" / "setup.json"
    moved.parent.mkdir()
    monkeypatch.setattr(_settle, "setup_path", lambda: moved)
    assert ("demo", "setup version") not in d.rows("demo")
    _settle.record_setup("demo", "0.0.8")
    assert "0.0.8" in d.rows("demo")[("demo", "setup version")].detail


def test_records_are_fail_rows_until_cleared(d):
    d.module(settings=None)
    doctor.record("demo", "resume failed", key="resume:moxi-demo-relay.service")
    doctor.record("moxi", "core note", key="k")
    got = d.rows()
    row = got[("demo", "record resume:moxi-demo-relay.service")]
    assert row.state == "fail"
    assert row.fix == "moxi doctor --clear demo resume:moxi-demo-relay.service"
    assert got[("moxi", "record k")].state == "fail"
    doctor.clear("moxi", "k")
    assert ("moxi", "record k") not in d.rows()


# -- 10, 11: moxi.doctor entries and DOCTOR --------------------------------------------------------

CHECKS = """\
import time
from moxi.doctor import Row
def rows():
    yield Row("demo", "mine", "ok", "fine")
    yield Row("other", "about other", "warn", "other needs a look")
def raises():
    raise ValueError("bad state")
def slow():
    time.sleep(2)
    return []
def one_row():
    return Row("demo", "x", "ok", "y")
def mixed():
    return [Row("demo", "x", "ok", "y"), "z"]
"""


@pytest.mark.parametrize("target, words", [
    ("raises", "moxi_demo.checks:raises raised ValueError: bad state"),
    ("slow", "moxi_demo.checks:slow did not finish in 0.2 s"),
    ("one_row", "returned Row, not an iterable of moxi.doctor.Row"),
    ("mixed", "returned a str among its rows"),
])
def test_a_doctor_target_that_fails_is_one_fail_row(d, monkeypatch, target, words):
    monkeypatch.setattr(_run, "DOCTOR_SECONDS", 0.2)
    d.module(settings=None, DOCTOR=f"'moxi_demo.checks:{target}'",
             files_={"checks.py": CHECKS})
    row = d.rows("demo")[("demo", "DOCTOR")]
    assert row.state == "fail" and words in row.detail


def test_doctor_rows_and_entries_keep_their_own_modules(d):
    d.module(settings=None, DOCTOR="'moxi_demo.checks:rows'", files_={"checks.py": CHECKS})
    d.module("other", settings=None)
    entry_points(d.site, "moxi-other", """\
        [moxi.modules]
        other = moxi_other.module
        [moxi.doctor]
        other = moxi_demo.checks:rows
        other.broken = moxi_demo.checks:raises
        """)
    modules.reset()
    run = d.run()
    assert run.count(Row("demo", "mine", "ok", "fine")) == 2
    assert Row("other", "about other", "warn", "other needs a look") in run
    failed = [r for r in run if r.name == "moxi.doctor other.broken"]
    assert failed and failed[0].module == "other" and failed[0].state == "fail"
    assert all(r.module == "other" for r in d.run("other"))


def test_a_check_that_breaks_is_one_fail_row_and_the_rest_still_run(d, monkeypatch):
    d.module(settings=None)

    def _check_home(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(_run, "_check_home", _check_home)
    got = d.rows("demo")
    assert got[("moxi", "check home")].state == "fail"
    assert got[("demo", "module")].state == "ok"


# -- 12. costs ---------------------------------------------------------------------------------

def test_the_idle_rate_takes_the_sample_nearest_an_hour_old():
    now = 1_000_000.0
    stamp = _run._stamp
    samples = [{"unit": "demo/relay", "at": stamp(now - 300), "cpu_seconds": 9.9},
               {"unit": "demo/relay", "at": stamp(now - 3000), "cpu_seconds": 4.0},
               {"unit": "demo/relay", "at": stamp(now - 7200), "cpu_seconds": 1.0},
               {"unit": "demo/other", "at": stamp(now - 3600), "cpu_seconds": 0.0}]
    rate, how = _run.idle_rate(samples, "demo/relay", 10.0, now, None)
    assert rate == pytest.approx(6.0 / 3000) and how == "over the last 50 min"


def test_the_idle_rate_falls_back_to_uptime_after_a_restart_or_without_samples():
    now = 1_000_000.0
    old = [{"unit": "demo/relay", "at": _run._stamp(now - 3600), "cpu_seconds": 50.0}]
    # The daemon started after the sample: the sample is another run's.
    rate, how = _run.idle_rate(old, "demo/relay", 2.0, now, now - 1000)
    assert rate == pytest.approx(2.0 / 1000) and how.startswith("since it started")
    assert _run.idle_rate([], "demo/relay", 2.0, now, None) == (None, "not measured")


def test_the_cgroup_read(tmp_path, monkeypatch):
    proc, cgroup = tmp_path / "proc", tmp_path / "cgroup"
    (proc / "77" / "fd").mkdir(parents=True)
    (proc / "78" / "fd").mkdir(parents=True)
    for n in range(3):
        (proc / "77" / "fd" / str(n)).write_text("")
    (proc / "78" / "fd" / "0").write_text("")
    (proc / "77" / "cgroup").write_text(
        "0::/user.slice/app.slice/moxi-demo-relay.service\n")
    group = cgroup / "user.slice" / "app.slice" / "moxi-demo-relay.service"
    group.mkdir(parents=True)
    (group / "cpu.stat").write_text("usage_usec 2500000\nuser_usec 2000000\n")
    (group / "memory.current").write_text("1048576\n")
    (group / "memory.swap.current").write_text("4096\n")
    (group / "cgroup.procs").write_text("77\n78\n")
    monkeypatch.setattr(_run, "_PROC", proc)
    monkeypatch.setattr(_run, "_CGROUP", cgroup)
    got = _run.cgroup_measure(77)
    assert (got.cpu, got.memory, got.fds) == (2.5, 1048576 + 4096, 4)
    assert _run.cgroup_measure(99) is None


def test_the_process_read_counts_a_real_child():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        got = _run.process_measure(child.pid)
        assert got.memory > 0 and got.fds >= 0 and got.cpu >= 0
        assert got.started == pytest.approx(time.time(), abs=60)
    finally:
        child.kill()
        child.wait()
    assert isinstance(_run.process_measure(child.pid), str)


def test_costs_rows_against_budgets_and_the_samples_kept(d, monkeypatch):
    d.installed(DAEMONS=DAEMON, BUDGETS="{'idle_cpu': 0.01, 'rss': '1M', 'fds': 64}")
    monkeypatch.setattr(_run, "floor_ms", lambda: 12.5)
    monkeypatch.setattr(_run, "measure",
                        lambda state: _run.Measure(18.0, 2 * 1024 * 1024, 10,
                                                   time.time() - 3600))
    got = d.rows("demo", "moxi", costs=True)
    assert got[("moxi", "floor")].detail == "python -I -c pass starts in 12.5 ms (median of 5)"
    cpu = got[("demo", "cost idle_cpu")]
    assert cpu.state == "ok" and cpu.detail.startswith("0.50% of one core, since it started")
    rss = got[("demo", "cost rss")]
    assert rss.state == "warn" and "over its budget 1M" in rss.detail
    assert got[("demo", "cost fds")].state == "ok"
    samples = files.read_records(moxi.paths.core().state_dir() / "costs.jsonl")
    assert [(s["unit"], s["cpu_seconds"]) for s in samples] == [("demo/relay", 18.0)]
    # Without --costs nothing is measured or kept.
    assert not any(name.startswith("cost") for _, name in d.rows("demo"))


def test_floor_ms_is_measured_once(monkeypatch):
    monkeypatch.setattr(_run, "_floor", [])
    calls = []
    real = subprocess.run

    def counted(*a, **k):
        calls.append(a[0])
        return real(*a, **k)

    monkeypatch.setattr(subprocess, "run", counted)
    first = doctor.floor_ms()
    assert first > 0 and doctor.floor_ms() == first
    assert len(calls) == 5 and all(c[1:] == ["-I", "-c", "pass"] for c in calls)


def test_importing_moxi_doctor_does_not_import_the_runner():
    code = "import sys, moxi.doctor; print('moxi.doctor._run' in sys.modules)"
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == "False"


def test_a_supervisor_read_that_raises_is_a_fail_row_per_module(d, monkeypatch):
    d.installed(DAEMONS=DAEMON)

    def refused(*a, **k):
        raise RuntimeError("the bus is gone")

    monkeypatch.setattr(units, "status", refused)
    got = d.rows("demo")
    row = got[("demo", "units")]
    assert row.state == "fail" and "RuntimeError: the bus is gone" in row.detail
    assert got[("demo", "daemon relay")] == Row("demo", "daemon relay", "ok", "up to date")
