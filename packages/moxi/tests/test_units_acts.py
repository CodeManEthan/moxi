"""The writer's acts on named units, the stop record and `resume`, `status`, `last_runs`
and the wrap of a foreign timer, on the fake backend and its simulated supervisor; and
`moxi config <module> show --json`."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import types

import pytest

import moxi.paths
from moxi import doctor, files, units
from moxi.units import _act, _backend, _state
from moxi.units._fake import FakeBackend, FakeSupervisor
from moxi.units._types import Stopped
from moxi_dev.testing import console_script

#: A platform this one isn't, for a unit declared for another system only.
OTHER_PLATFORM = "linux" if sys.platform == "win32" else "win32"

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

[loud]
type = "bool"
default = true
doc = "Not a switch: its default is true."
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

[plain]
type = "table"
default = {}
doc = "A table, not a switch table."
read = "use"
"""

DAEMON = "{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}}"
SOCKET = ("{'relay': {'command': 'moxi-demo-relay', "
          "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
TIMER = ("{'snapshot': {'command': 'moxi-demo-snap', 'every': '5m', "
         "'switch': 'snapshot.enabled'}}")


@pytest.fixture
def w(site, tmp_path, monkeypatch):
    """A fake module `demo` with its settings, a scripts directory and the fake backend."""
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    root = tmp_path / "units"
    root.mkdir()
    supervisor = FakeSupervisor(root)
    backend = FakeBackend(root, runner=supervisor)
    lines: list[str] = []

    def script(name):
        return console_script(scripts, name)

    def module(name="demo", *, units_from=None, settings=SETTINGS, **names):
        pkg = f"moxi_{name}"
        if settings is not None:
            site.write(f"{pkg}/settings.toml", settings)
            names.setdefault("SETTINGS", "'settings.toml'")
        if units_from is not None:
            site.write(f"{pkg}/runtime.py", textwrap.dedent(units_from))
            names["UNITS_FROM"] = f"'{pkg}.runtime:units'"
        site.module(name, **names)
        __import__("moxi.modules", fromlist=["reset"]).reset()

    def installed(name="demo", **names):
        """`module`, with its units written and running."""
        for entry in ("moxi-demo-relay", "moxi-demo-snap"):
            script(entry)
        module(name, **names)
        for change in units.plan(name):
            if change.digest is not None:
                _state.record(name, change.name, change.digest)
        done = units.apply(units.plan(name), say=lines.append)
        assert all(a.ok for a in done), done
        supervisor.calls.clear()

    def foreign_timer(timer="backup.timer", service="backup-run.service"):
        supervisor.extra |= {timer, service}
        supervisor.triggers[timer] = service
        supervisor.reload()
        supervisor.state(timer).update(active=True, enabled=True)

    def calls(verb):
        return [c for c in supervisor.calls if c[0] == verb]

    with _backend.use(backend):
        yield types.SimpleNamespace(site=site, scripts=scripts, root=root, sup=supervisor,
                                    backend=backend, script=script, module=module,
                                    installed=installed, foreign_timer=foreign_timer,
                                    calls=calls, lines=lines, tmp=tmp_path)


# -- the acts --------------------------------------------------------------------------------

def test_each_act_on_a_daemon_runs_the_backends_verbs(w):
    w.installed(DAEMONS=DAEMON)
    sup, unit = w.sup, "moxi-demo-relay.service"
    [acted] = units.stop("demo", ["relay"])
    assert (acted.module, acted.name, acted.act, acted.ok) == ("demo", "relay", "stop", True)
    assert sup.units[unit]["active"] is False
    assert units.start("demo", "relay")[0].ok and sup.units[unit]["active"]
    assert units.restart("demo", ["relay"])[0].ok
    assert units.disable("demo", ["relay"])[0].ok
    assert sup.units[unit] == {"active": False, "enabled": False}
    assert units.enable("demo", ["relay"])[0].ok
    assert sup.units[unit] == {"active": True, "enabled": True}
    assert sup.calls == [("stop", unit), ("start", unit), ("restart", unit),
                         ("disable", "--now", unit), ("enable", "--now", unit)]


def test_a_daemons_socket_goes_first_with_it(w):
    w.installed(DAEMONS=SOCKET)
    units.stop("demo", ["relay"])
    units.disable("demo", ["relay"])
    units.enable("demo", ["relay"])
    # The service has no install section: the socket carries the enable, and the
    # service is only stopped and started.
    assert w.sup.calls == [
        ("stop", "moxi-demo-relay.socket", "moxi-demo-relay.service"),
        ("disable", "--now", "moxi-demo-relay.socket"), ("stop", "moxi-demo-relay.service"),
        ("enable", "--now", "moxi-demo-relay.socket"), ("start", "moxi-demo-relay.service")]


def test_a_timers_start_and_stop_act_on_its_service_and_enable_on_its_timer(w):
    w.installed(TIMERS=TIMER)
    for act in (units.start, units.stop, units.restart, units.disable, units.enable):
        assert act("demo", ["snapshot"])[0].ok
    service, timer = "moxi-demo-snapshot.service", "moxi-demo-snapshot.timer"
    assert w.sup.calls == [("start", service), ("stop", service), ("stop", service),
                           ("start", service), ("disable", "--now", timer),
                           ("enable", "--now", timer)]
    assert w.sup.units[timer] == {"active": True, "enabled": True}


def test_a_name_with_no_marked_unit_is_refused(w):
    w.installed(DAEMONS=DAEMON)
    (w.root / "moxi-demo-other.service").write_text("[Service]\n")
    w.sup.reload()
    acted = units.start("demo", ["other", "relay"])
    assert [(a.name, a.ok) for a in acted] == [("other", False), ("relay", True)]
    assert "demo/other has no unit written by moxi" in acted[0].lines[0]
    assert ("start", "moxi-demo-other.service") not in w.sup.calls


def test_an_act_with_no_supervisor_does_nothing(w, monkeypatch):
    w.installed(DAEMONS=DAEMON)
    monkeypatch.setattr(_backend, "implementation", lambda: None)
    [acted] = units.start("demo", ["relay"])
    assert not acted.ok and "no supervisor on this system" in acted.lines[0]
    assert w.sup.calls == []


def test_a_failed_act_carries_the_supervisors_text(w):
    w.installed(DAEMONS=DAEMON)
    w.sup.fail[("restart", "moxi-demo-relay.service")] = "Job failed. See journal."
    [acted] = units.restart("demo", ["relay"])
    assert not acted.ok
    assert acted.lines == ("`systemctl --user restart -- moxi-demo-relay.service` failed: "
                           "Job failed. See journal.",)


# -- foreign units ---------------------------------------------------------------------------

def test_a_foreign_timers_start_stop_and_restart_act_on_its_triggers_unit(w):
    w.module(DAEMONS="{}")
    w.foreign_timer()
    for act in (units.start, units.stop, units.restart):
        [acted] = act("services", ["backup.timer"], foreign=True)
        assert acted.ok and acted.module is None and acted.name == "backup.timer"
    acting = [c for c in w.sup.calls if c[0] != "show"]
    assert acting == [("start", "backup-run.service"), ("stop", "backup-run.service"),
                      ("stop", "backup-run.service"), ("start", "backup-run.service")]
    assert not any("backup.service" in c for c in w.sup.calls)


def test_a_foreign_timers_disable_and_enable_take_effect_now(w):
    w.module(DAEMONS="{}")
    w.foreign_timer()
    assert units.disable("services", ["backup.timer"], foreign=True)[0].ok
    assert w.sup.units["backup.timer"] == {"active": False, "enabled": False}
    assert units.enable("services", ["backup.timer"], foreign=True)[0].ok
    assert w.sup.units["backup.timer"] == {"active": True, "enabled": True}


@pytest.mark.parametrize("ref", ["moxi-demo-relay.service", "moxi-anything.timer"])
def test_foreign_refuses_a_name_in_moxis_own_namespace(w, ref):
    w.installed(DAEMONS=DAEMON)
    for act in (units.start, units.stop, units.enable, units.disable, units.restart):
        [acted] = act("services", [ref], foreign=True)
        assert not acted.ok and "moxi's own unit namespace" in acted.lines[0]
    assert w.sup.calls == []


@pytest.mark.parametrize("ref", ["-Hhost.service", "--host=x.timer"])
def test_foreign_refuses_a_name_led_by_a_dash_without_a_supervisor_call(w, ref):
    w.installed(DAEMONS=DAEMON)
    for act in (units.start, units.stop, units.enable, units.disable, units.restart):
        [acted] = act("services", [ref], foreign=True)
        assert not acted.ok and acted.lines[0].startswith(f"{ref}: ")
        assert "none starting with -" in acted.lines[0]
    acted = units.wrap("-Hhost.timer", module="demo", key="rows.backup", name="backup")
    assert not acted.ok and "none starting with -" in acted.lines[0]
    assert w.sup.calls == []


def test_foreign_refuses_a_unit_whose_file_carries_a_mark(w):
    class Reading(FakeBackend):
        def foreign(self, ref, fence, kind=None):
            unit = super().foreign(ref, fence, kind)
            return _backend.UnitRef(None, ref, unit.kind, unit.ids, (self.root / ref,))

    w.module(DAEMONS="{}")
    (w.root / "odd.service").write_text("# moxi unit: demo/relay\n")
    with _backend.use(Reading(w.root, runner=w.sup)):
        [acted] = units.start("services", ["odd.service"], foreign=True)
    assert not acted.ok
    assert acted.lines == ("odd.service carries the mark of demo/relay; it is demo's own "
                           "unit, not a foreign one",)
    assert w.sup.calls == []


# -- the stop record and resume --------------------------------------------------------------

def test_stop_records_only_what_runs_socket_first_and_resume_starts_exactly_that(w):
    w.installed(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'socket': {'setting': "
                        "'port', 'addresses': ['127.0.0.1']}}, 'idle': {'command': "
                        "'moxi-demo-relay'}}")
    w.sup.units["moxi-demo-idle.service"]["active"] = False
    record: list = []
    acted = units.stop("demo", record=record)
    assert [(a.name, a.ok) for a in acted] == [("relay", True), ("idle", True)]
    assert acted[1].lines == ("demo/idle is not running",)
    assert [(s.module, s.name, s.unit, s.backend, s.marked) for s in record] == [
        ("demo", "relay", "moxi-demo-relay.socket", "fake", True),
        ("demo", "relay", "moxi-demo-relay.service", "fake", True)]
    assert w.calls("show") == [("show", "--property=Id,ActiveState",
                                "moxi-demo-relay.socket", "moxi-demo-relay.service",
                                "moxi-demo-idle.service")]
    assert w.calls("stop") == [("stop", "moxi-demo-relay.socket"),
                               ("stop", "moxi-demo-relay.service")]
    w.sup.calls.clear()
    acted = units.resume(record)
    assert [(a.name, a.act, a.ok) for a in acted] == [("relay", "start", True)] * 2
    assert w.sup.calls == [("start", "moxi-demo-relay.socket"),
                           ("start", "moxi-demo-relay.service")]
    assert w.sup.units["moxi-demo-idle.service"]["active"] is False


def test_resume_never_starts_an_entry_twice_or_one_built_by_hand(w):
    w.installed(DAEMONS=DAEMON)
    record: list = []
    units.stop("demo", ["relay"], record=record)
    assert all(a.ok for a in units.resume(record))
    w.sup.calls.clear()
    again = units.resume(record)
    forged = units.resume([Stopped("demo", "relay", "moxi-demo-relay.service", "fake",
                                   True)])
    copied = units.resume([Stopped("demo", "relay", "moxi-demo-relay.service", "fake",
                                   True, token=object())])
    for [acted] in (again, forged, copied):
        assert not acted.ok
        assert acted.lines == ("moxi-demo-relay.service: not stopped by this process; "
                               "not started",)
    assert w.sup.calls == []


def test_stop_names_none_takes_the_declared_and_rejected_daemons(w, tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("")
    w.script("moxi-demo-relay")
    w.script("moxi-demo-snap")
    w.module(DAEMONS=DAEMON, TIMERS=TIMER, units_from="""
             def units():
                 return {"daemons": {"late": {"argv": ["relative"]}}}
             """)
    units.apply(units.plan("demo"), say=w.lines.append)
    # An older unit of the run-time daemon whose entry now fails its checks.
    (w.root / "moxi-demo-late.service").write_text("# moxi unit: demo/late\n")
    w.sup.reload()
    w.sup.state("moxi-demo-late.service")["active"] = True
    w.sup.calls.clear()
    record: list = []
    acted = units.stop("demo", record=record)
    assert [a.name for a in acted] == ["relay", "late"]
    assert [s.unit for s in record] == ["moxi-demo-relay.service", "moxi-demo-late.service"]
    assert ("stop", "moxi-demo-snapshot.service") not in w.sup.calls


def test_stop_names_none_raises_when_units_from_fails(w):
    w.installed(DAEMONS=DAEMON, units_from="def units():\n    raise OSError('gone')\n")
    with pytest.raises(units.DeclarationError):
        units.stop("demo", record=[])
    assert w.sup.calls == []


def test_replace_without_a_record_raises(w):
    w.installed(DAEMONS=DAEMON)
    with pytest.raises(ValueError, match="needs a record"):
        units.stop("demo", ["relay"], replace=True)
    assert w.sup.calls == []


def test_replace_stops_and_records_the_unmarked_unit_of_a_declared_daemon(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    (w.root / "moxi-demo-relay.service").write_text("[Service]\nExecStart=/old\n")
    w.sup.reload()
    w.sup.state("moxi-demo-relay.service").update(active=True, enabled=True)
    record: list = []
    [plain] = units.stop("demo", ["relay"])
    assert not plain.ok and w.sup.units["moxi-demo-relay.service"]["active"]
    [acted] = units.stop("demo", ["relay"], replace=True, record=record)
    assert acted.ok
    assert [(s.unit, s.marked) for s in record] == [("moxi-demo-relay.service", False)]
    assert not w.sup.units["moxi-demo-relay.service"]["active"]
    assert units.resume(record)[0].ok
    assert w.sup.units["moxi-demo-relay.service"]["active"]


def test_a_failed_running_read_stops_and_records_nothing(w):
    w.installed(DAEMONS=DAEMON)
    w.sup.fail[("show", None)] = "Failed to connect to bus"
    record: list = []
    [acted] = units.stop("demo", ["relay"], record=record)
    assert not acted.ok and "nothing was stopped" in acted.lines[0]
    assert "Failed to connect to bus" in acted.lines[0]
    assert record == [] and w.calls("stop") == []


def test_a_stop_that_fails_is_recorded_first_and_resumed(w):
    w.installed(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                        "'other': {'command': 'moxi-demo-relay'}}")
    w.sup.fail[("stop", "moxi-demo-other.service")] = "Job timed out."
    record: list = []
    acted = units.stop("demo", record=record)
    assert [(a.name, a.ok) for a in acted] == [("relay", True), ("other", False)]
    assert "Job timed out." in acted[1].lines[0]
    assert [s.unit for s in record] == ["moxi-demo-relay.service",
                                        "moxi-demo-other.service"]
    w.sup.calls.clear()
    assert all(a.ok for a in units.resume(record))
    assert w.sup.calls == [("start", "moxi-demo-relay.service"),
                           ("start", "moxi-demo-other.service")]


def test_a_stop_that_raises_leaves_its_entry_recorded_for_resume(w, monkeypatch):
    w.installed(DAEMONS=DAEMON)

    def runner(cmd, **kw):
        if cmd[2:] == ["stop", "--", "moxi-demo-relay.service"]:
            raise RuntimeError("the runner broke")
        return w.sup(cmd, **kw)

    monkeypatch.setattr(w.backend, "runner", runner)
    record: list = []
    with pytest.raises(RuntimeError, match="the runner broke"):
        units.stop("demo", record=record)
    assert [s.unit for s in record] == ["moxi-demo-relay.service"]
    monkeypatch.setattr(w.backend, "runner", w.sup)
    [started] = units.resume(record)
    assert started.ok


def test_a_refused_start_is_reported_never_retried_and_the_next_is_started(w):
    w.installed(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                        "'other': {'command': 'moxi-demo-relay'}}")
    record: list = []
    units.stop("demo", record=record)
    w.sup.fail[("start", "moxi-demo-relay.service")] = "Unit is masked."
    w.sup.calls.clear()
    first, second = units.resume(record)
    assert not first.ok and second.ok
    assert first.lines == (
        "moxi-demo-relay.service: `systemctl --user start -- moxi-demo-relay.service` "
        "failed: Unit is masked.",
        "start it by hand: systemctl --user start -- moxi-demo-relay.service")
    assert w.sup.calls == [("start", "moxi-demo-relay.service"),
                           ("start", "moxi-demo-other.service")]


def test_a_start_that_raises_is_caught_into_its_entry(w, monkeypatch):
    w.installed(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                        "'other': {'command': 'moxi-demo-relay'}}")
    record: list = []
    units.stop("demo", record=record)

    def runner(cmd, **kw):
        if cmd[2:] == ["start", "--", "moxi-demo-relay.service"]:
            raise RuntimeError("the runner broke")
        return w.sup(cmd, **kw)

    monkeypatch.setattr(w.backend, "runner", runner)
    first, second = units.resume(record)
    assert not first.ok and first.lines == (
        "moxi-demo-relay.service: RuntimeError: the runner broke",
        "start it by hand: systemctl --user start -- moxi-demo-relay.service")
    assert second.ok


def test_a_start_that_raises_before_naming_its_command_has_no_by_hand_line(w, monkeypatch):
    w.installed(DAEMONS=DAEMON)
    record: list = []
    units.stop("demo", record=record)

    def start_id(unit, fence):
        raise RuntimeError("the backend broke")

    monkeypatch.setattr(w.backend, "start_id", start_id)
    [acted] = units.resume(record)
    assert not acted.ok and acted.lines == (
        "moxi-demo-relay.service: RuntimeError: the backend broke",)


def test_resume_starts_a_unit_the_user_disabled_and_leaves_it_disabled(w):
    w.installed(DAEMONS=DAEMON)
    w.sup.units["moxi-demo-relay.service"]["enabled"] = False
    record: list = []
    units.stop("demo", record=record)
    units.resume(record)
    assert w.sup.units["moxi-demo-relay.service"] == {"active": True, "enabled": False}
    assert w.calls("enable") == []


def test_resume_clears_the_record_of_a_unit_it_started_and_keeps_the_others(w):
    w.installed(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                        "'other': {'command': 'moxi-demo-relay'}}")
    for unit in ("moxi-demo-relay.service", "moxi-demo-other.service"):
        doctor.record("demo", f"{unit} was not started", key=f"resume:{unit}",
                      fix=f"systemctl --user start -- {unit}")
    doctor.record("demo", "unrelated", key="other")
    record: list = []
    units.stop("demo", record=record)
    w.sup.fail[("start", "moxi-demo-other.service")] = "Unit is masked."
    units.resume(record)
    path = moxi.paths._layout("demo").state_dir() / "doctor.json"
    assert set(json.loads(path.read_text())) == {"resume:moxi-demo-other.service", "other"}
    assert _act.RESUME_KEY.format(unit="x.service") == "resume:x.service"


# -- the core lock ---------------------------------------------------------------------------

HOLDER = textwrap.dedent("""
    import sys, time
    from moxi import files, paths
    with files.lock(paths.core_lock()):
        print("held", flush=True)
        time.sleep(30)
""")


def test_every_act_takes_the_core_lock_nested(w, monkeypatch):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    taken = []
    real = files.lock

    def lock(path, **kw):
        taken.append((path, kw.get("nested")))
        return real(path, **kw)

    monkeypatch.setattr(files, "lock", lock)
    record: list = []
    units.start("demo", ["relay"])
    units.stop("demo", ["relay"], record=record)
    units.resume(record)
    units.wrap("backup.timer", module="demo", key="rows.backup", name="backup")
    units.unwrap("backup.timer", module="demo")
    units.status("demo")
    units.last_runs("demo", "snapshot")
    assert taken == [(moxi.paths.core_lock(), True)] * 5


def test_a_standalone_act_waits_while_an_install_holds_the_lock(w):
    w.installed(DAEMONS=DAEMON)
    proc = subprocess.Popen([sys.executable, "-c", HOLDER], stdout=subprocess.PIPE,
                            text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        done: list = []
        thread = threading.Thread(target=lambda: done.append(
            units.stop("demo", ["relay"])), daemon=True)
        thread.start()
        thread.join(0.5)
        assert thread.is_alive() and w.calls("stop") == []
    finally:
        proc.kill()
        proc.wait()
    thread.join(10)
    assert not thread.is_alive() and done[0][0].ok


def test_acts_resume_and_wraps_yield_where_this_process_holds_the_lock(w):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    results: list = []

    def calls():
        record: list = []
        results.append(units.stop("demo", ["relay"], record=record)[0].ok)
        results.append(units.resume(record)[0].ok)
        results.append(units.wrap("backup.timer", module="demo", key="rows.backup",
                                  name="backup").ok)
        results.append(units.unwrap("backup.timer", module="demo").ok)

    with files.lock(moxi.paths.core_lock(), timeout=0):
        calls()
        thread = threading.Thread(target=calls)
        thread.start()
        thread.join(10)
        assert not thread.is_alive()
    assert results == [True] * 8


# -- status ----------------------------------------------------------------------------------

def run_line(module, name, **fields):
    files.append_record(moxi.paths._layout(module).state_dir() / "runs.jsonl",
                        {"unit": f"{module}/{name}", "at": "2026-10-30T10:00:00Z",
                         "seconds": 1.0, "result": "ran", "exit": 0, **fields})


def test_status_reads_each_unit_in_one_call(w):
    w.installed(DAEMONS=SOCKET, TIMERS=TIMER)
    run_line("demo", "snapshot", exit=3)
    run_line("demo", "snapshot", exit=0, at="2026-10-30T11:00:00Z")
    run_line("demo", "relay", exit=1)
    states = {(s.name, s.kind): s for s in units.status("demo")}
    assert len(w.calls("show")) == 1
    relay = states[("relay", "daemon")]
    assert (relay.module, relay.backend, relay.state, relay.pid, relay.enabled) == (
        "demo", "fake", "active", 4242, True)
    assert relay.log == "journalctl --user -u moxi-demo-relay.service"
    assert relay.last == {**relay.last, "unit": "demo/relay", "exit": 1}
    assert states[("relay", "socket")].state == "active"
    assert states[("relay", "socket")].last is None
    snap = states[("snapshot", "timer")]
    assert snap.state == "active" and snap.enabled is True
    assert snap.last["at"] == "2026-10-30T11:00:00Z" and snap.last["exit"] == 0
    assert snap.last["supervisor"] == {"result": "success", "status": "0"}
    assert snap.wrapped is None


def test_status_without_unit_files(w, monkeypatch):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                     "'gated': {'command': 'moxi-demo-relay', 'switch': 'relay.enabled'}, "
                     "'mac': {'command': 'moxi-demo-relay', 'platforms': ['darwin']}}")
    (w.root / "moxi-demo-gone.service").write_text("# moxi unit: demo/gone\n")
    w.sup.reload()
    states = {s.name: s for s in units.status(["demo"])}
    assert states["relay"].state == "missing" and states["relay"].enabled is None
    assert "not installed" in states["relay"].detail
    assert states["gated"].state == "off"
    assert states["gated"].detail.endswith("moxi config set demo relay.enabled true")
    assert states["mac"].state == "off" and "not on this system" in states["mac"].detail
    assert states["gone"].state == "inactive"
    assert list(states) == ["relay", "gated", "mac", "gone"]
    monkeypatch.setattr(_backend, "implementation", lambda: None)
    nothing = units.status("demo", foreign=["backup.timer"])
    assert {(s.name, s.state, s.enabled, s.backend) for s in nothing} == {
        ("relay", "nobackend", None, None), ("gated", "nobackend", None, None),
        ("mac", "nobackend", None, None), ("backup.timer", "nobackend", None, None)}


def test_status_of_every_module_by_default(w):
    w.installed(DAEMONS=DAEMON)
    assert [(s.module, s.name) for s in units.status()] == [("demo", "relay")]


def test_a_foreign_timer_is_read_by_its_triggers_unit_in_two_calls(w):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    w.sup.units["backup-run.service"] = {"active": False, "enabled": False}
    assert units.wrap("backup.timer", module="demo", key="rows.backup", name="backup").ok
    run_line("demo", "backup", exit=4)
    w.sup.calls.clear()
    states = units.status("demo", foreign=["backup.timer"])
    assert len(w.calls("show")) == 2
    [backup] = [s for s in states if s.module is None]
    assert (backup.name, backup.kind, backup.state, backup.enabled) == (
        "backup.timer", "timer", "active", True)
    assert backup.wrapped == units.Wrapped("demo", "rows.backup", "backup")
    assert backup.last["unit"] == "demo/backup" and backup.last["exit"] == 4
    assert backup.last["supervisor"] == {"result": "success", "status": "0"}
    assert "backup.service" not in " ".join(" ".join(c) for c in w.sup.calls)


def test_a_timers_state_names_the_unit_it_triggers_at_no_extra_call(w):
    """A marked timer and a foreign one carry the unit their `Triggers` names; a daemon,
    its socket and a foreign timer the manager does not hold carry none. The reads are
    the ones status makes anyway: one `show`, and one more for the foreign timers."""
    w.installed(DAEMONS=SOCKET, TIMERS=TIMER)
    w.foreign_timer()
    states = {(s.name, s.kind): s for s in units.status("demo")}
    assert len(w.calls("show")) == 1
    assert states[("snapshot", "timer")].triggers == "moxi-demo-snapshot.service"
    assert states[("relay", "daemon")].triggers is None
    assert states[("relay", "socket")].triggers is None
    w.sup.calls.clear()
    states = {s.name: s for s in units.status("demo", foreign=["backup.timer",
                                                                "gone.timer"])}
    assert len(w.calls("show")) == 2
    assert states["backup.timer"].triggers == "backup-run.service"
    assert states["snapshot"].triggers == "moxi-demo-snapshot.service"
    assert (states["gone.timer"].state, states["gone.timer"].triggers) == ("missing", None)


def test_a_timer_that_was_not_read_names_no_unit(w, monkeypatch):
    """`off` (here, not on this system) and `nobackend` make no read, and a failed `show`
    reads nothing: none of them carries `triggers`."""
    w.installed(TIMERS=TIMER[:-1] + ", 'win': {'command': 'moxi-demo-snap', 'every': '5m', "
                                    "'switch': 'snapshot.enabled', 'platforms': "
                                    f"['{OTHER_PLATFORM}']}}}}")
    w.foreign_timer()
    states = {s.name: s for s in units.status("demo", foreign=["backup.timer"])}
    assert (states["win"].kind, states["win"].state, states["win"].triggers) == (
        "timer", "off", None)
    assert states["snapshot"].triggers == "moxi-demo-snapshot.service"
    w.sup.fail[("show", None)] = "Failed to connect to bus"
    states = units.status("demo", foreign=["backup.timer"])
    assert [(s.name, s.state, s.triggers) for s in states] == [
        ("snapshot", "unknown", None), ("win", "off", None), ("backup.timer", "unknown", None)]
    monkeypatch.setattr(_backend, "implementation", lambda: None)
    states = units.status("demo", foreign=["backup.timer"])
    assert [(s.state, s.triggers) for s in states] == [("nobackend", None)] * 3


def test_status_refuses_a_bad_foreign_name_alone_and_reads_the_rest(w):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    states = units.status("demo", foreign=["moxi-demo-relay.service", "backup.timer", "",
                                           "-Hhost.timer", "plain"])
    by_name = {(s.module, s.name): s for s in states}
    assert by_name[("demo", "relay")].state == "active"
    assert by_name[(None, "backup.timer")].state == "active"
    own = by_name[(None, "moxi-demo-relay.service")]
    assert (own.state, own.kind) == ("unknown", "daemon")
    assert "moxi's own unit namespace" in own.detail
    assert by_name[(None, "")].state == "unknown"
    for ref in ("-Hhost.timer", "plain"):
        bad = by_name[(None, ref)]
        assert bad.state == "unknown" and bad.detail.startswith(f"{ref}: ")
        assert "the fence takes" in bad.detail
    assert [s.name for s in states] == ["relay", "moxi-demo-relay.service", "backup.timer",
                                        "", "-Hhost.timer", "plain"]
    assert not any("-Hhost" in " ".join(c) or "plain" in c for c in w.sup.calls)


def test_last_runs_gives_the_newest_lines_of_one_timer_oldest_first(w):
    for i in range(5):
        run_line("demo", "snapshot", exit=i)
        run_line("demo", "other", exit=10 + i)
    assert [r["exit"] for r in units.last_runs("demo", "snapshot")] == [4]
    assert [r["exit"] for r in units.last_runs("demo", "snapshot", 3)] == [2, 3, 4]
    assert [r["exit"] for r in units.last_runs("demo", "snapshot", 50)] == [0, 1, 2, 3, 4]
    assert units.last_runs("demo", "snapshot", 0) == []
    assert units.last_runs("demo", "never") == []
    assert units.last_runs("absent", "snapshot") == []
    with pytest.raises(ValueError):
        units.last_runs("demo", "snapshot", -1)


def test_last_runs_reads_the_older_generation_too(w):
    log = moxi.paths._layout("demo").state_dir() / "runs.jsonl"
    files.append_record(log, {"unit": "demo/snapshot", "exit": 1})
    log.rename(log.with_name("runs.jsonl.1"))
    files.append_record(log, {"unit": "demo/snapshot", "exit": 2})
    assert [r["exit"] for r in units.last_runs("demo", "snapshot", 2)] == [1, 2]


# -- wrap ------------------------------------------------------------------------------------

DROPIN = "backup-run.service.d/50-moxi-demo.conf"


def test_wrap_writes_the_marked_dropin_and_reloads(w):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    acted = units.wrap("backup.timer", module="demo", key="rows.backup", name="backup")
    assert (acted.module, acted.name, acted.act, acted.ok) == (
        "demo", "backup.timer", "wrap", True)
    assert acted.lines[-1] == ("backup.timer wrapped; its runs are off until: moxi config "
                               "set demo rows.backup true")
    assert (w.root / DROPIN).read_text() == textwrap.dedent("""\
        # moxi wrap: demo/backup
        # moxi: timer = backup.timer, if = demo.rows.backup
        [Service]
        ExecCondition=/opt/moxi/bin/moxi run --if demo.rows.backup --record demo/backup --condition
        ExecStopPost=/opt/moxi/bin/moxi run --record demo/backup --result
        """)
    assert w.calls("daemon-reload") == [("daemon-reload",)]
    assert units.wraps("demo") == {"backup.timer": units.Wrapped("demo", "rows.backup",
                                                                  "backup")}
    assert not (w.root / "backup.timer").exists()


def test_a_second_wrap_with_the_same_key_and_name_replaces_the_first(w):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    from moxi import config

    config.set("demo", "rows.backup", True, by="test")
    for _ in range(2):
        acted = units.wrap("backup.timer", module="demo", key="rows.backup",
                           name="backup")
        assert acted.ok
    assert acted.lines[-1] == "backup.timer wrapped; demo rows.backup is on, so its runs go on"
    assert len(list(w.root.glob("*.d/*.conf"))) == 1


@pytest.mark.parametrize("ref, key, name, line", [
    ("backup.timer", "rows.other", "backup",
     "backup.timer is wrapped by demo with key rows.backup and record backup; unwrap it "
     "first to change them"),
    ("backup.timer", "rows.backup", "nightly",
     "backup.timer is wrapped by demo with key rows.backup and record backup; unwrap it "
     "first to change them"),
    ("again.timer", "rows.backup", "again",
     "backup-run.service is wrapped by demo for backup.timer; unwrap it first"),
    ("solo.timer", "rows.solo", "backup",
     "the record name backup is taken by demo's wrap of backup.timer; choose another"),
])
def test_a_wrap_that_conflicts_with_the_modules_other_wrap_is_refused(w, ref, key, name,
                                                                      line):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    w.foreign_timer("again.timer", "backup-run.service")
    w.foreign_timer("solo.timer", "solo-run.service")
    assert units.wrap("backup.timer", module="demo", key="rows.backup", name="backup").ok
    before = (w.root / DROPIN).read_text()
    w.sup.calls.clear()
    acted = units.wrap(ref, module="demo", key=key, name=name)
    assert not acted.ok and acted.lines == (line,)
    assert (w.root / DROPIN).read_text() == before
    assert w.calls("daemon-reload") == []


@pytest.mark.parametrize("setup, ref, key, name, line", [
    (None, "moxi-demo-x.timer", "rows.backup", "backup",
     "moxi-demo-x.timer is in moxi's own unit namespace; wrap takes only a timer moxi did "
     "not write"),
    (None, "backup.service", "rows.backup", "backup",
     "backup.service is not a timer; wrap takes a foreign .timer unit"),
    (None, "backup.timer", "loud", "backup",
     "demo: switch loud must be a bool setting read at use, with no env and default false"),
    (None, "backup.timer", "plain.backup", "backup",
     "demo: switch plain.backup names an entry of plain, which is not a switch table (a "
     "table of bools read at use, with no env and default {})"),
    (None, "backup.timer", "rows.backup", "snapshot",
     "snapshot is a declared timer of demo, and their run lines share "
     "STATE/demo/runs.jsonl; choose another record name"),
    (None, "backup.timer", "rows.backup", "Back_up",
     "'Back_up' is not a run record name: lower-case letters and digits, joined by "
     "single '-'"),
    ("old", "backup.timer", "rows.backup", "backup",
     "systemd 242 has no ExecCondition= (it needs 243); not wrapped"),
    ("none", "backup.timer", "rows.backup", "backup",
     "backup.timer triggers nothing; wrap needs exactly one service"),
    ("two", "backup.timer", "rows.backup", "backup",
     "backup.timer triggers backup-run.service other.service; wrap needs exactly one "
     "service"),
    ("execs", "backup.timer", "rows.backup", "backup",
     "backup-run.service has 2 ExecStart lines; wrap needs exactly one"),
    ("forking", "backup.timer", "rows.backup", "backup",
     "backup-run.service is Type=forking; wrap needs oneshot, simple or exec"),
    ("tools", "backup.timer", "rows.backup", "backup",
     "backup-run.service is wrapped by tools"),
    ("unmarked", "backup.timer", "rows.backup", "backup", None),
    ("gone", "backup.timer", "rows.backup", "backup",
     "backup.timer is not loaded (not-found); not wrapped"),
])
def test_wrap_refuses(w, setup, ref, key, name, line):
    w.installed(DAEMONS=DAEMON, TIMERS=TIMER)
    w.foreign_timer()
    service = w.root / "backup-run.service.d"
    if setup == "old":
        w.sup.version = "242"
    elif setup == "none":
        w.sup.triggers["backup.timer"] = ""
    elif setup == "two":
        w.sup.triggers["backup.timer"] = "backup-run.service other.service"
    elif setup == "execs":
        w.sup.props["backup-run.service"] = {"ExecStart": 2}
    elif setup == "forking":
        w.sup.props["backup-run.service"] = {"Type": "forking"}
    elif setup == "tools":
        service.mkdir()
        (service / "50-moxi-tools.conf").write_text(
            "# moxi wrap: tools/backup\n# moxi: timer = backup.timer, if = tools.x.y\n")
    elif setup == "unmarked":
        service.mkdir()
        (service / "50-moxi-demo.conf").write_text("[Service]\nExecStartPre=/bin/true\n")
        line = f"{service / '50-moxi-demo.conf'} exists and was not written by moxi; not wrapped"
    elif setup == "gone":
        w.sup.extra.discard("backup.timer")
        w.sup.reload()
    before = sorted(p.read_text() for p in w.root.glob("*.d/*.conf"))
    acted = units.wrap(ref, module="demo", key=key, name=name)
    assert not acted.ok and acted.lines == (line,)
    assert sorted(p.read_text() for p in w.root.glob("*.d/*.conf")) == before
    assert w.calls("daemon-reload") == []


def test_wrap_is_systemd_only(w):
    class Launchd(FakeBackend):
        name = supervisor = "launchd"

    w.installed(DAEMONS=DAEMON)
    with _backend.use(Launchd(w.root, runner=w.sup)):
        acted = units.wrap("backup.timer", module="demo", key="rows.backup", name="backup")
        assert units.wraps("demo") == {}
        assert units.unwrap("backup.timer", module="demo").ok
    assert not acted.ok and acted.lines == ("wrap is systemd only; backup runs as it is",)
    assert w.sup.calls == []


def test_unwrap_finds_the_dropin_by_its_mark_after_the_timer_is_gone(w):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    assert units.wrap("backup.timer", module="demo", key="rows.backup", name="backup").ok
    other = w.root / "backup-run.service.d" / "50-moxi-tools.conf"
    w.sup.extra -= {"backup.timer", "backup-run.service"}
    w.sup.reload()
    w.sup.calls.clear()
    acted = units.unwrap("backup.timer", module="demo")
    assert acted.ok and w.calls("daemon-reload") == [("daemon-reload",)]
    assert not (w.root / "backup-run.service.d").exists()
    acted = units.unwrap("backup.timer", module="demo")
    assert acted.ok and acted.lines == ("backup.timer is not wrapped by demo",)
    # Another module's drop-in is never touched.
    other.parent.mkdir()
    other.write_text("# moxi wrap: tools/backup\n# moxi: timer = backup.timer, if = tools.a\n")
    assert units.unwrap("backup.timer", module="demo").ok and other.exists()


def test_remove_unwraps_every_dropin_of_the_module_with_one_reload(w):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    w.foreign_timer("solo.timer", "solo-run.service")
    assert units.wrap("backup.timer", module="demo", key="rows.backup", name="backup").ok
    assert units.wrap("solo.timer", module="demo", key="rows.solo", name="solo").ok
    w.sup.calls.clear()
    applied = units.remove("demo", say=w.lines.append)
    assert {a.change.name for a in applied if a.change.kind == "wrap"} == {
        "backup.timer", "solo.timer"}
    assert len(w.calls("daemon-reload")) == 2  # the units' reload, then the wraps' one
    assert units.wraps("demo") == {}


def test_plan_never_reads_a_dropin(w):
    w.installed(DAEMONS=DAEMON)
    w.foreign_timer()
    before = units.plan("demo")
    assert units.wrap("backup.timer", module="demo", key="rows.backup", name="backup").ok
    assert units.plan("demo") == before


# -- config show --json ----------------------------------------------------------------------

def show_json(module, capsys, code=0):
    from moxi import cli

    capsys.readouterr()
    assert cli.main(["config", module, "show", "--json"]) == code
    return json.loads(capsys.readouterr().out)


def test_config_show_json_lists_every_key(w, capsys):
    from moxi import config

    w.module(DAEMONS="{}")
    config.set("demo", "port", 9000, by="test")
    keys = {k["name"]: k for k in show_json("demo", capsys)}
    assert list(keys) == ["relay.enabled", "snapshot.enabled", "loud", "port", "rows",
                          "plain"]
    assert keys["port"] == {"name": "port", "type": "int", "default": 8470,
                            "doc": "The relay's port.", "values": None, "read": "start",
                            "env": None, "needs": None, "items": None, "value": 9000,
                            "source": "file"}
    assert keys["loud"]["value"] is True and keys["loud"]["source"] == "default"
    assert "switch_table" not in keys["plain"] and "entries" not in keys["plain"]
    assert keys["rows"]["switch_table"] is True and keys["rows"]["entries"] == []


def test_config_show_json_lists_switch_table_entries_in_the_file_and_named(w, capsys):
    from moxi import config

    w.installed(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'switch': 'rows.relay'}}",
                TIMERS="{'snap': {'command': 'moxi-demo-snap', 'every': '5m', "
                       "'switch': 'rows.snap'}}",
                QUESTIONS="{'rows.asked': 'Ask?', 'rows.snap': 'Snapshots?'}",
                units_from="""
                import sys

                def units():
                    return {"timers": {"late": {"argv": [sys.executable], "every": "1h",
                                                "switch": "rows.late"}}}
                """)
    w.foreign_timer()
    assert units.wrap("backup.timer", module="demo", key="rows.backup", name="backup").ok
    config.set("demo", "rows", {"relay": True, "stray": False}, by="test")
    [rows] = [k for k in show_json("demo", capsys) if k["name"] == "rows"]
    assert rows["switch_table"] is True and "declarations" not in rows
    assert rows["entries"] == [
        {"name": "asked", "value": False, "source": "default", "named_by": ["question"]},
        {"name": "backup", "value": False, "source": "default",
         "named_by": ["wrap:backup.timer"]},
        {"name": "late", "value": False, "source": "default", "named_by": ["timer:late"]},
        {"name": "relay", "value": True, "source": "file", "named_by": ["daemon:relay"]},
        {"name": "snap", "value": False, "source": "default",
         "named_by": ["timer:snap", "question"]},
        {"name": "stray", "value": False, "source": "file", "named_by": []},
    ]


def test_config_show_json_with_units_from_failing_lists_the_file_entries(w, capsys):
    from moxi import config

    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'switch': 'rows.relay'}}",
             units_from="def units():\n    raise OSError('rows gone')\n")
    config.set("demo", "rows.kept", True, by="test")
    [rows] = [k for k in show_json("demo", capsys) if k["name"] == "rows"]
    assert rows["entries"] == [{"name": "kept", "value": True, "source": "file",
                                "named_by": []}]
    assert rows["declarations"] == "unavailable"
    assert rows["reason"] == "UNITS_FROM failed: OSError: rows gone"


def test_config_show_json_of_a_file_that_wont_parse_is_one_error(w, capsys):
    w.module(DAEMONS="{}")
    path = moxi.paths._layout("demo").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("port = [\n")
    shown = show_json("demo", capsys, code=1)
    assert set(shown) == {"error", "fix"} and "TOML" in shown["error"]


def test_config_show_json_of_the_core_and_of_a_module_without_settings(w, capsys):
    w.module("bare", settings=None, DAEMONS="{}")
    assert show_json("bare", capsys) == []
    core = show_json("moxi", capsys)
    assert isinstance(core, list) and all("value" in k and "source" in k for k in core)


def test_config_json_goes_only_with_show(w, capsys):
    from moxi import cli

    w.module(DAEMONS="{}")
    assert cli.main(["config", "demo", "path", "--json"]) == 2
    assert "--json goes with show" in capsys.readouterr().err



def test_the_fence_reads_the_managers_version_with_no_unit():
    from moxi.units import _fence

    assert _fence.argv("systemd", "show", "--property=Version") == [
        "systemctl", "--user", "show", "--property=Version"]
    with pytest.raises(_fence.FenceError):
        _fence.argv("systemd", "show", "--property=Id")
