"""The launchd backend: plists rendered and parsed with plistlib, the apply order and the acts
driven through a simulated `launchctl`, and the domain read from recorded `launchctl print`
text. Nothing here runs a real `launchctl`; the rows that need one are at the end and run
only on a Mac with `MOXI_TEST_REAL_SUPERVISOR=1`."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

import moxi.modules
import moxi.paths
from moxi import files, units
from moxi.units import _backend, _launchd, _plan, _state
from moxi.units._backend import Backend, RenderError, Schedule, Socket, Spec, UnitRef
from moxi.units._fence import Fence
from moxi.units._launchd import LaunchdBackend

UID = 501
DOMAIN = f"gui/{UID}"


class FakeLaunchctl:
    """A runner for the launchd fence that simulates the user's `gui/<uid>` domain.

    `loaded` maps a label to `[pid or None, last exit status or None]`; `disabled` holds
    the labels with a disabled override and `overrides` every label with one. `fail` maps
    `(verb, label)` to `(exit code, stderr)` for that command. `calls` lists each command's
    arguments after `launchctl`. `not_loaded_code` is the exit code of a bootout of a label
    that is not loaded.
    """

    def __init__(self):
        self.loaded: dict[str, list] = {}
        self.disabled: set[str] = set()
        self.overrides: set[str] = set()
        self.fail: dict[tuple[str, str | None], tuple[int, str]] = {}
        self.calls: list[tuple[str, ...]] = []
        self.not_loaded_code = 3
        self.pids = iter(range(4000, 5000))

    def __call__(self, cmd, *, env=None, timeout=None):
        assert cmd[0] == "launchctl" and env is None
        args = tuple(cmd[1:])
        self.calls.append(args)
        verb = args[0]
        label = args[-1].rsplit("/", 1)[1] if args[-1].startswith(f"{DOMAIN}/") else None
        if verb == "bootstrap":
            label = plistlib.loads(Path(args[2]).read_bytes())["Label"]
        if (verb, label) in self.fail:
            code, err = self.fail[(verb, label)]
            return subprocess.CompletedProcess(cmd, code, "", err)
        if verb == "print":
            return subprocess.CompletedProcess(cmd, 0, self.print(), "")
        if verb == "bootstrap":
            if label in self.disabled or label in self.loaded:
                return self.error(cmd, 5, "Bootstrap failed: 5: Input/output error")
            body = plistlib.loads(Path(args[2]).read_bytes())
            self.loaded[label] = [next(self.pids) if body.get("RunAtLoad") else None, None]
        elif verb == "bootout":
            if label not in self.loaded:
                return self.error(cmd, self.not_loaded_code,
                                  f"Boot-out failed: {self.not_loaded_code}: No such process")
            del self.loaded[label]
        elif verb in ("enable", "disable"):
            self.overrides.add(label)
            (self.disabled.add if verb == "disable" else self.disabled.discard)(label)
        elif verb == "kickstart":
            if label not in self.loaded:
                return self.error(cmd, 113, f'Could not find service "{label}" in domain '
                                            f"for user gui: {UID}")
            self.loaded[label][0] = next(self.pids)
        elif verb == "kill":
            if label not in self.loaded:
                return self.error(cmd, 113, f'Could not find service "{label}"')
            if not self.loaded[label][0]:
                return self.error(cmd, 3, "No such process")
            self.loaded[label] = [None, -15]
        return subprocess.CompletedProcess(cmd, 0, "", "")

    @staticmethod
    def error(cmd, code, err):
        return subprocess.CompletedProcess(cmd, code, "", err + "\n")

    def print(self) -> str:
        services = "".join(f"\t\t{pid or 0:>8} {'-' if code is None else code:>6}\t{label}\n"
                           for label, (pid, code) in sorted(self.loaded.items()))
        disabled = "".join(
            f'\t\t"{label}" => {"disabled" if label in self.disabled else "enabled"}\n'
            for label in sorted(self.overrides))
        return (f"{DOMAIN} = {{\n\ttype = login\n\thandle = 100004\n"
                f"\tsecurity context = {{\n\t\tuid unset\n\t}}\n\n"
                f"\tservices = {{\n{services}\t}}\n\n"
                f"\tunmanaged processes = {{\n\t}}\n\n"
                f"\tdisabled services = {{\n{disabled}\t}}\n}}\n")

    def verbs(self) -> list[tuple[str, ...]]:
        """The calls, without the domain reads."""
        return [c for c in self.calls if c[0] != "print"]


SETTINGS = """
[snapshot.enabled]
type = "bool"
default = false
doc = "Take snapshots."
read = "use"

[port]
type = "int"
default = 8470
doc = "The shell's port."
read = "start"
"""


@pytest.fixture
def mac(site, tmp_path, monkeypatch):
    """A fake module `demo`, a scripts directory with `moxi`, a LaunchAgents directory in
    tmp, and the launchd backend over a simulated `launchctl`."""
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    ctl = FakeLaunchctl()
    backend = LaunchdBackend(agents=agents, uid=UID, runner=ctl)
    lines: list[str] = []

    def script(name):
        path = scripts / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path

    script("moxi")

    def module(**names):
        site.write("moxi_demo/settings.toml", SETTINGS)
        names.setdefault("SETTINGS", "'settings.toml'")
        site.module("demo", **names)
        for name in [m for m in sys.modules if m.split(".")[0] == "moxi_demo"]:
            del sys.modules[name]
        moxi.modules.reset()

    def plan(**kw):
        return {(c.name, c.kind): c for c in units.plan("demo", **kw)}

    def apply(changes, **kw):
        return units.apply(changes, say=lines.append, **kw)

    def plist(label):
        return plistlib.loads((agents / f"{label}.plist").read_bytes())

    with _backend.use(backend):
        yield types.SimpleNamespace(site=site, scripts=scripts, agents=agents, ctl=ctl,
                                    backend=backend, script=script, module=module,
                                    plan=plan, apply=apply, lines=lines, plist=plist,
                                    fence=Fence("launchd", ctl), tmp=tmp_path)


DAEMON = "{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}}"
RELAY = "moxi.demo.relay"


def spec(kind="daemon", *, module="demo", name="relay", command=("/opt/bin/moxi-demo-relay",),
         entry=None, **kw) -> Spec:
    return Spec(module=module, name=name, kind=kind, command=tuple(command),
                doc=kw.pop("doc", name), entry=entry or {}, **kw)


# -- rendering ------------------------------------------------------------------------------------

GOLDEN_DAEMON = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- moxi unit: comms/daemon -->
<plist version="1.0">
<dict>
  <key>Label</key><string>moxi.comms.daemon</string>
  <key>ProgramArguments</key>
  <array><string>/Users/ana/.local/share/uv/tools/moxi/bin/moxi-comms-daemon</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>2</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>/Users/ana/.local/state/moxi/comms/logs/daemon.log</string>
  <key>StandardErrorPath</key><string>/Users/ana/.local/state/moxi/comms/logs/daemon.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>MOXI_LOG_FILE</key><string>/Users/ana/.local/state/moxi/comms/logs/daemon.log</string></dict>
  <key>AbandonProcessGroup</key><true/>
</dict>
</plist>
"""

GOLDEN_TIMER = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- moxi unit: notes/snapshot -->
<plist version="1.0">
<dict>
  <key>Label</key><string>moxi.notes.snapshot</string>
  <key>ProgramArguments</key>
  <array><string>/Users/ana/.local/share/uv/tools/moxi/bin/moxi</string><string>run</string><string>--if</string><string>notes.snapshot.enabled</string><string>--record</string><string>notes/snapshot</string><string>--</string><string>/Users/ana/.local/share/uv/tools/moxi/bin/moxi-notes-snapshot</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>/Users/ana/.local/state/moxi/notes/logs/snapshot.log</string>
  <key>StandardErrorPath</key><string>/Users/ana/.local/state/moxi/notes/logs/snapshot.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>MOXI_LOG_FILE</key><string>/Users/ana/.local/state/moxi/notes/logs/snapshot.log</string></dict>
</dict>
</plist>
"""

BIN = "/Users/ana/.local/share/uv/tools/moxi/bin"


@pytest.fixture
def ana(monkeypatch):
    """The default roots of a user `ana` on a Mac, for the golden texts."""
    for name in list(os.environ):
        if name.startswith(("MOXI_", "XDG_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", "/Users/ana")
    return LaunchdBackend(uid=UID, gate=f"{BIN}/moxi")


def test_a_daemon_plist_is_the_golden_text(ana):
    [group] = ana.render(spec(module="comms", name="daemon", stop="process",
                              command=(f"{BIN}/moxi-comms-daemon",)))
    assert group.kind == "daemon"
    [(path, text)] = group.files
    assert path == Path("/Users/ana/Library/LaunchAgents/moxi.comms.daemon.plist")
    assert text == GOLDEN_DAEMON
    log = "/Users/ana/.local/state/moxi/comms/logs/daemon.log"
    assert plistlib.loads(text.encode()) == {
        "Label": "moxi.comms.daemon",
        "ProgramArguments": [f"{BIN}/moxi-comms-daemon"],
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 2,
        "ProcessType": "Background", "StandardOutPath": log, "StandardErrorPath": log,
        "EnvironmentVariables": {"MOXI_LOG_FILE": log}, "AbandonProcessGroup": True}


def test_a_timer_plist_is_the_golden_text(ana):
    [group] = ana.render(spec("timer", module="notes", name="snapshot",
                              command=(f"{BIN}/moxi-notes-snapshot",),
                              switch="snapshot.enabled", schedule=Schedule(every=5)))
    assert group.kind == "timer"
    [(path, text)] = group.files
    assert path.name == "moxi.notes.snapshot.plist" and text == GOLDEN_TIMER
    body = plistlib.loads(text.encode())
    assert "RunAtLoad" not in body and "KeepAlive" not in body


def test_a_daemon_without_process_stop_does_not_abandon_its_group(mac):
    [group] = mac.backend.render(spec())
    body = plistlib.loads(group.files[0][1].encode())
    assert "AbandonProcessGroup" not in body and "Sockets" not in body
    assert body["StandardOutPath"].endswith("/demo/logs/relay.log")


GOLDEN_SOCKET = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- moxi unit: ui/shell -->
<plist version="1.0">
<dict>
  <key>Label</key><string>moxi.ui.shell</string>
  <key>ProgramArguments</key>
  <array><string>/Users/ana/.local/share/uv/tools/moxi/bin/moxi-ui-shell</string></array>
  <key>ThrottleInterval</key><integer>2</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>/Users/ana/.local/state/moxi/ui/logs/shell.log</string>
  <key>StandardErrorPath</key><string>/Users/ana/.local/state/moxi/ui/logs/shell.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>MOXI_LOG_FILE</key><string>/Users/ana/.local/state/moxi/ui/logs/shell.log</string></dict>
  <key>Sockets</key>
  <dict><key>Listeners</key><dict><key>SockNodeName</key><string>127.0.0.1</string><key>SockServiceName</key><string>8470</string><key>SockType</key><string>stream</string></dict><key>Listeners-1</key><dict><key>SockNodeName</key><string>::1</string><key>SockServiceName</key><string>8470</string><key>SockType</key><string>stream</string></dict></dict>
</dict>
</plist>
"""


def listener(address, port="8470"):
    return {"SockNodeName": address, "SockServiceName": port, "SockType": "stream"}


def test_a_socket_daemon_plist_is_the_golden_text(ana):
    """One key per address, `Listeners` then `Listeners-1`, one dict each; no RunAtLoad
    and no KeepAlive, so the sockets start it and a login does not."""
    [group] = ana.render(spec(module="ui", name="shell", command=(f"{BIN}/moxi-ui-shell",),
                              socket=Socket("port", ("127.0.0.1", "::1"), 8470)))
    assert group.kind == "daemon"
    [(path, text)] = group.files
    assert path.name == "moxi.ui.shell.plist" and text == GOLDEN_SOCKET
    body = plistlib.loads(text.encode())
    assert "RunAtLoad" not in body and "KeepAlive" not in body
    assert body["ThrottleInterval"] == 2
    assert list(body["Sockets"]) == ["Listeners", "Listeners-1"]
    assert body["Sockets"] == {"Listeners": listener("127.0.0.1"),
                               "Listeners-1": listener("::1")}


def test_a_socket_key_follows_its_address_place_when_one_is_left_out(mac):
    def keys(addresses, left_out=()):
        [group] = mac.backend.render(spec(socket=Socket("port", addresses, 8470, left_out)))
        return plistlib.loads(group.files[0][1].encode())["Sockets"]

    assert keys(("127.0.0.1", "::1"), ("::1",)) == {"Listeners": listener("127.0.0.1")}
    assert keys(("::1", "127.0.0.1", "10.0.0.2"), ("::1",)) == {
        "Listeners-1": listener("127.0.0.1"), "Listeners-2": listener("10.0.0.2")}
    with pytest.raises(RenderError) as refused:
        keys(("::1",), ("::1",))
    assert refused.value.cause == "socket"


def test_a_daemon_without_a_socket_keeps_run_at_load_and_keep_alive(mac):
    [group] = mac.backend.render(spec())
    body = plistlib.loads(group.files[0][1].encode())
    assert body["RunAtLoad"] is True and body["KeepAlive"] == {"SuccessfulExit": False}


def test_run_time_forms_cwd_and_enabled_false(mac):
    [group] = mac.backend.render(spec(cwd="/srv/rows", enabled=False,
                                      command=("/opt/tool", "--a b")))
    text = group.files[0][1]
    body = plistlib.loads(text.encode())
    assert body["WorkingDirectory"] == "/srv/rows"
    assert body["ProgramArguments"] == ["/opt/tool", "--a b"]
    assert text.splitlines()[2:4] == ["<!-- moxi unit: demo/relay -->",
                                      "<!-- moxi: enabled = false -->"]
    assert mac.backend.enabled(text) is False and mac.backend.mark(text) == "demo/relay"
    [plain] = mac.backend.render(spec())
    assert mac.backend.enabled(plain.files[0][1]) is True


@pytest.mark.parametrize("schedule, expected", [
    (Schedule(every=1440), {"StartInterval": 86400}),
    (Schedule(at=((9, 0), (17, 30))),
     {"StartCalendarInterval": [{"Hour": 9, "Minute": 0}, {"Hour": 17, "Minute": 30}]}),
    (Schedule(at=((9, 0),), days=("mon", "fri", "sun")),
     {"StartCalendarInterval": [{"Weekday": 1, "Hour": 9, "Minute": 0},
                                {"Weekday": 5, "Hour": 9, "Minute": 0},
                                {"Weekday": 0, "Hour": 9, "Minute": 0}]}),
])
def test_the_schedule_translation(mac, schedule, expected):
    [group] = mac.backend.render(spec("timer", name="nightly", schedule=schedule,
                                      switch="snapshot.enabled"))
    body = plistlib.loads(group.files[0][1].encode())
    for key in ("StartInterval", "StartCalendarInterval"):
        assert body.get(key) == expected.get(key)


def test_a_switchless_timer_runs_the_gate_without_if(mac):
    [group] = mac.backend.render(spec("timer", name="rows", command=("/opt/tool",),
                                      schedule=Schedule(every=5)))
    argv = plistlib.loads(group.files[0][1].encode())["ProgramArguments"]
    assert argv == [str(mac.scripts / "moxi"), "run", "--record", "demo/rows", "--",
                    "/opt/tool"]


def test_a_timer_with_no_moxi_script_is_refused_with_cause_command(mac):
    (mac.scripts / "moxi").unlink()
    with pytest.raises(RenderError) as caught:
        mac.backend.render(spec("timer", schedule=Schedule(every=5)))
    assert caught.value.cause == "command" and "no moxi" in caught.value.text


def test_strings_are_escaped_and_unwritable_characters_refused(mac):
    argv = ("/opt/a&b", "<x>", "it's \"q\"", "tab\there", "cr\rhere")
    [group] = mac.backend.render(spec(command=argv))
    assert plistlib.loads(group.files[0][1].encode())["ProgramArguments"] == list(argv)
    with pytest.raises(RenderError) as caught:
        mac.backend.render(spec(command=("/opt/tool", "bell\x07")))
    assert caught.value.cause == "command"


def test_a_mark_is_read_only_before_the_plist_element(mac):
    backend = mac.backend
    assert backend.mark(plistlib.dumps({"Label": "x"}).decode()) is None
    inside = ('<?xml version="1.0"?>\n<plist version="1.0"><dict><key>Label</key>'
              "<string><!-- moxi unit: demo/relay --></string></dict></plist>\n")
    assert backend.mark(inside) is None


# -- marked units --------------------------------------------------------------------------------

def test_marked_reads_this_modules_plists_and_their_kinds(mac):
    for group in (*mac.backend.render(spec()),
                  *mac.backend.render(spec("timer", name="snap", schedule=Schedule(every=5),
                                           enabled=False)),
                  *mac.backend.render(spec(module="other"))):
        for path, text in group.files:
            path.write_text(text)
    (mac.agents / "moxi.demo.stray.plist").write_bytes(plistlib.dumps({"Label": "x"}))
    refs = mac.backend.marked("demo")
    assert [(r.name, r.kind, r.ids, r.enabled) for r in refs] == [
        ("relay", "daemon", (RELAY,), True), ("snap", "timer", ("moxi.demo.snap",), False)]
    assert refs[0].paths == (mac.agents / f"{RELAY}.plist",)


# -- apply ----------------------------------------------------------------------------------------

def test_a_new_daemon_is_written_enabled_and_bootstrapped(mac):
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    change = mac.plan()[("relay", "daemon")]
    assert change.action == "add" and change.paths == (mac.agents / f"{RELAY}.plist",)
    [done] = mac.apply([change])
    assert done.ok, done.lines
    plist = str(mac.agents / f"{RELAY}.plist")
    assert mac.ctl.calls == [("enable", f"{DOMAIN}/{RELAY}"), ("bootstrap", DOMAIN, plist)]
    assert oct(os.stat(plist).st_mode & 0o777) == "0o644"
    logs = moxi.paths.Layout("demo").state_dir() / "logs"
    assert logs.is_dir() and oct(logs.stat().st_mode & 0o777) == "0o700"
    assert mac.ctl.loaded[RELAY][0]
    assert mac.plan()[("relay", "daemon")].action == "keep"


def test_keep_makes_no_call_and_a_change_is_booted_out_and_bootstrapped(mac):
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    mac.apply(units.plan("demo"))
    mac.ctl.calls.clear()
    mac.apply(units.plan("demo"))
    assert mac.ctl.calls == []
    mac.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'args': ['-v']}}")
    change = mac.plan()[("relay", "daemon")]
    assert change.action == "change" and change.enabled_before is True
    [done] = mac.apply([change])
    assert done.ok
    plist = str(mac.agents / f"{RELAY}.plist")
    assert mac.ctl.calls == [("bootout", f"{DOMAIN}/{RELAY}"),
                             ("enable", f"{DOMAIN}/{RELAY}"), ("bootstrap", DOMAIN, plist)]
    assert mac.plist(RELAY)["ProgramArguments"][1:] == ["-v"]


def test_a_change_of_a_job_that_is_not_loaded_is_not_a_failure(mac):
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    mac.apply(units.plan("demo"))
    del mac.ctl.loaded[RELAY]
    mac.ctl.not_loaded_code = 5  # a code the backend does not know: the domain read decides
    mac.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'args': ['-v']}}")
    mac.ctl.calls.clear()
    [done] = mac.apply(units.plan("demo"))
    assert done.ok, done.lines
    assert [c[0] for c in mac.ctl.calls] == ["bootout", "print", "enable", "bootstrap"]


def test_a_failed_bootout_leaves_the_plist_and_names_the_command(mac):
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    mac.apply(units.plan("demo"))
    before = (mac.agents / f"{RELAY}.plist").read_text()
    mac.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'args': ['-v']}}")
    mac.ctl.fail[("bootout", RELAY)] = (36, "Boot-out failed: 36: Operation now in progress")
    [done] = mac.apply(units.plan("demo"))
    assert not done.ok
    assert done.lines == (
        f"`launchctl bootout {DOMAIN}/{RELAY}` failed: Boot-out failed: 36: Operation now "
        "in progress",
        "the unit is left as it was; fix the cause and run `moxi install demo` again")
    assert (mac.agents / f"{RELAY}.plist").read_text() == before


def test_a_failed_bootstrap_keeps_the_supervisors_text_and_goes_on(mac):
    for name in ("moxi-demo-relay", "moxi-demo-other"):
        mac.script(name)
    mac.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                       "'other': {'command': 'moxi-demo-other'}}")
    mac.ctl.fail[("bootstrap", RELAY)] = (5, "Bootstrap failed: 5: Input/output error")
    done = {a.change.name: a for a in mac.apply(units.plan("demo"))}
    assert not done["relay"].ok and done["other"].ok
    assert done["relay"].lines[0].endswith("failed: Bootstrap failed: 5: Input/output error")
    assert done["relay"].lines[1] == ("the unit file is written; fix the cause and run "
                                      "`moxi install demo` again")
    assert "moxi.demo.other" in mac.ctl.loaded


def test_upgrade_keep_rewrites_a_loaded_job_without_a_bootstrap(mac):
    mac.script("moxi-demo-relay")
    keep = "{'relay': {'command': 'moxi-demo-relay', 'upgrade': 'keep'%s}}"
    mac.module(DAEMONS=keep % "")
    mac.apply(units.plan("demo"))
    mac.module(DAEMONS=keep % ", 'args': ['-v']")
    mac.ctl.calls.clear()
    [done] = mac.apply(units.plan("demo"))
    assert done.ok and mac.ctl.calls == [("print", DOMAIN)]
    assert done.lines == (f"{RELAY} rewritten; the new code runs from its next start",)
    assert mac.plist(RELAY)["ProgramArguments"][1:] == ["-v"]
    # Not loaded: written and bootstrapped as any change.
    del mac.ctl.loaded[RELAY]
    mac.module(DAEMONS=keep % ", 'args': ['--wider']")
    mac.ctl.calls.clear()
    [done] = mac.apply(units.plan("demo"))
    assert done.ok and [c[0] for c in mac.ctl.verbs()] == ["bootout", "enable", "bootstrap"]


def test_restart_after_an_upgrade_kickstarts_only_running_restart_daemons(mac):
    for name in ("moxi-demo-relay", "moxi-demo-kept", "moxi-demo-idle"):
        mac.script(name)
    mac.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                       "'kept': {'command': 'moxi-demo-kept', 'upgrade': 'keep'}, "
                       "'idle': {'command': 'moxi-demo-idle'}}")
    mac.apply(units.plan("demo"))
    mac.ctl.loaded["moxi.demo.idle"][0] = None
    mac.ctl.calls.clear()
    assert all(a.ok for a in mac.apply(units.plan("demo"), restart=True))
    assert mac.ctl.calls == [("print", DOMAIN), ("kickstart", "-k", f"{DOMAIN}/{RELAY}")]


def test_a_failed_domain_read_fails_a_restart_and_rewrites_a_keep_daemon(mac):
    mac.script("moxi-demo-relay")
    mac.script("moxi-demo-kept")
    kept = "'kept': {'command': 'moxi-demo-kept', 'upgrade': 'keep'%s}"
    mac.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, " + kept % "" + "}")
    mac.apply(units.plan("demo"))
    mac.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                       + kept % ", 'args': ['-v']" + "}")
    mac.ctl.fail[("print", None)] = (1, "Could not print domain")
    mac.ctl.calls.clear()
    done = {a.change.name: a for a in mac.apply(units.plan("demo"), restart=True)}
    assert mac.ctl.calls == [("print", DOMAIN)]
    assert not done["relay"].ok and done["relay"].lines == (
        f"`launchctl print {DOMAIN}` failed: Could not print domain",
        f"the daemon runs its old code until it restarts: `launchctl kickstart -k "
        f"{DOMAIN}/{RELAY}`")
    assert done["kept"].ok and mac.plist("moxi.demo.kept")["ProgramArguments"][1:] == ["-v"]


def test_a_removed_unit_is_booted_out_and_deleted_loaded_or_not(mac):
    mac.module(DAEMONS="{}")
    for name in ("old", "gone"):
        [group] = mac.backend.render(spec(name=name))
        group.files[0][0].write_text(group.files[0][1])
    mac.ctl.loaded["moxi.demo.old"] = [77, None]
    changes = units.plan("demo")
    assert {c.action for c in changes} == {"remove"}
    done = mac.apply(changes)
    assert all(a.ok for a in done), [a.lines for a in done]
    assert not list(mac.agents.glob("moxi.demo.*")) and not mac.ctl.loaded
    assert [c[0] for c in mac.ctl.calls] == ["bootout", "bootout"]


def test_a_remove_whose_bootout_fails_leaves_the_plist(mac):
    mac.module(DAEMONS="{}")
    [group] = mac.backend.render(spec(name="old"))
    group.files[0][0].write_text(group.files[0][1])
    mac.ctl.loaded["moxi.demo.old"] = [77, None]
    mac.ctl.fail[("bootout", "moxi.demo.old")] = (1, "Boot-out failed: 1: Operation not "
                                                     "permitted")
    [done] = mac.apply(units.plan("demo"))
    assert not done.ok and group.files[0][0].exists()
    assert done.lines[1].startswith("the unit is left in place")


def test_replace_moves_a_foreign_plist_aside_and_boots_its_job_out(mac):
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    old = mac.agents / f"{RELAY}.plist"
    old.write_bytes(plistlib.dumps({"Label": RELAY, "ProgramArguments": ["/old"]}))
    mac.ctl.loaded[RELAY] = [99, None]
    change = mac.plan()[("relay", "daemon")]
    assert (change.action, change.cause, change.foreign) == ("refuse", "foreign", old)
    [done] = mac.apply([change], replace=True)
    assert done.ok, done.lines
    assert [c[0] for c in mac.ctl.calls] == ["bootout", "enable", "bootstrap"]
    [aside] = (moxi.paths.core().data_dir() / "displaced").glob(f"*/{RELAY}.plist")
    assert plistlib.loads(aside.read_bytes())["ProgramArguments"] == ["/old"]
    assert mac.backend.mark(old.read_text()) == "demo/relay"


def run_time(mac, tool, flag):
    mac.site.write("moxi_demo/runtime.py", textwrap.dedent(f"""
        def units():
            return {{"daemons": {{"row": {{"argv": [{str(tool)!r}], "enabled": {flag}}}}}}}
        """))
    sys.modules.pop("moxi_demo.runtime", None)


def test_enabled_false_is_written_and_disabled_and_a_flip_takes_effect_now(mac, tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("")
    run_time(mac, tool, False)
    mac.module(UNITS_FROM="'moxi_demo.runtime:units'")
    [done] = mac.apply(units.plan("demo"))
    assert done.ok and mac.ctl.calls == [("disable", f"{DOMAIN}/moxi.demo.row")]
    run_time(mac, tool, True)
    [change] = units.plan("demo")
    assert change.action == "change" and change.enabled_before is False
    mac.ctl.calls.clear()
    assert mac.apply([change])[0].ok
    assert [c[0] for c in mac.ctl.verbs()] == ["bootout", "enable", "bootstrap"]
    assert "moxi.demo.row" in mac.ctl.loaded
    run_time(mac, tool, False)
    mac.ctl.calls.clear()
    assert mac.apply(units.plan("demo"))[0].ok
    assert mac.ctl.calls == [("bootout", f"{DOMAIN}/moxi.demo.row"),
                             ("disable", f"{DOMAIN}/moxi.demo.row")]
    assert "moxi.demo.row" not in mac.ctl.loaded and "moxi.demo.row" in mac.ctl.disabled


TIMER = ("{'snapshot': {'command': 'moxi-demo-snap', 'every': '5m', "
         "'switch': 'snapshot.enabled', 'doc': 'take a snapshot'}}")


def test_a_switched_timer_is_written_after_the_settle_step_records_it(mac):
    mac.script("moxi-demo-snap")
    mac.module(TIMERS=TIMER)
    change = mac.plan()[("snapshot", "timer")]
    [held] = mac.apply([change])
    assert held.ok and mac.ctl.calls == [] and not change.paths[0].exists()
    _state.record("demo", "snapshot", change.digest)
    [done] = mac.apply([change])
    assert done.ok
    body = mac.plist("moxi.demo.snapshot")
    assert body["StartInterval"] == 300 and "RunAtLoad" not in body
    assert body["ProgramArguments"][:4] == [str(mac.scripts / "moxi"), "run", "--if",
                                            "demo.snapshot.enabled"]
    assert [c[0] for c in mac.ctl.calls] == ["enable", "bootstrap"]
    assert mac.ctl.loaded["moxi.demo.snapshot"] == [None, None]
    assert mac.backend.marked("demo")[0].kind == "timer"


def shell(*addresses):
    return ("{'relay': {'command': 'moxi-demo-relay', "
            f"'socket': {{'setting': 'port', 'addresses': {list(addresses)!r}}}}}}}")


def test_a_socket_daemon_is_one_daemon_change_and_loads_idle(mac, monkeypatch):
    """The sockets are keys of the daemon's plist, so a plan has no `socket` change; the
    job is enabled and bootstrapped as any daemon, and with no RunAtLoad it is loaded
    with no pid until a connection starts it."""
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: True)
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=shell("127.0.0.1", "::1"))
    [change] = units.plan("demo")
    assert (change.kind, change.action) == ("daemon", "add")
    [done] = mac.apply([change])
    assert done.ok, done.lines
    plist = str(mac.agents / f"{RELAY}.plist")
    assert mac.ctl.calls == [("enable", f"{DOMAIN}/{RELAY}"), ("bootstrap", DOMAIN, plist)]
    assert mac.plist(RELAY)["Sockets"] == {"Listeners": listener("127.0.0.1"),
                                           "Listeners-1": listener("::1")}
    assert mac.ctl.loaded[RELAY] == [None, None]
    again = mac.plan()
    assert list(again) == [("relay", "daemon")] and again[("relay", "daemon")].action == "keep"


def test_no_ipv6_loopback_leaves_listeners_1_out_until_it_can_be_bound(mac, monkeypatch):
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: False)
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=shell("127.0.0.1", "::1"))
    [change] = units.plan("demo")
    assert "::1 left out: no IPv6 loopback on this system" in change.reason
    assert mac.apply([change])[0].ok
    assert mac.plist(RELAY)["Sockets"] == {"Listeners": listener("127.0.0.1")}
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: True)
    [change] = units.plan("demo")
    assert (change.kind, change.action) == ("daemon", "change")
    assert mac.apply([change])[0].ok
    assert list(mac.plist(RELAY)["Sockets"]) == ["Listeners", "Listeners-1"]


def test_a_daemon_that_gains_a_socket_is_booted_out_and_loads_idle(mac):
    """Booting out the old job ends its process; the new one has no RunAtLoad, so the
    daemon stays down until a connection, and its sockets hold the port meanwhile."""
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    mac.apply(units.plan("demo"))
    assert mac.ctl.loaded[RELAY][0]
    mac.module(DAEMONS=shell("127.0.0.1"))
    [change] = units.plan("demo")
    assert (change.kind, change.action) == ("daemon", "change")
    mac.ctl.calls.clear()
    assert mac.apply([change])[0].ok
    assert [c[0] for c in mac.ctl.calls] == ["bootout", "enable", "bootstrap"]
    assert mac.ctl.loaded[RELAY] == [None, None]
    body = mac.plist(RELAY)
    assert "RunAtLoad" not in body and "KeepAlive" not in body
    assert body["Sockets"]["Listeners"]["SockServiceName"] == "8470"


def test_remove_of_a_module_takes_its_plists_and_wraps_nothing(mac):
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    mac.apply(units.plan("demo"))
    done = units.remove("demo", say=mac.lines.append)
    assert [(a.change.name, a.ok) for a in done] == [("relay", True)]
    assert not (mac.agents / f"{RELAY}.plist").exists() and RELAY not in mac.ctl.loaded


# -- the acts --------------------------------------------------------------------------------------

def installed(mac, *, timer=False):
    """The relay daemon (and a switchless snapshot timer) written and loaded."""
    daemon = mac.backend.render(spec())[0]
    groups = [daemon] + ([mac.backend.render(spec("timer", name="snap",
                                                  schedule=Schedule(every=5)))[0]]
                         if timer else [])
    for group in groups:
        for path, text in group.files:
            path.write_text(text)
    mac.ctl.loaded[RELAY] = [321, None]
    if timer:
        mac.ctl.loaded["moxi.demo.snap"] = [None, 0]
    return {r.name: r for r in mac.backend.marked("demo")}


def act(mac, what, ref):
    mac.ctl.calls.clear()
    return mac.backend.act(what, ref, mac.fence)


def test_daemon_acts_by_the_verb_table(mac):
    ref = installed(mac)["relay"]
    plist = str(ref.paths[0])
    target = f"{DOMAIN}/{RELAY}"
    assert act(mac, "stop", ref).ok and mac.ctl.verbs() == [("bootout", target)]
    assert act(mac, "stop", ref).ok  # stopping a stopped daemon is not a failure
    assert act(mac, "start", ref).ok and mac.ctl.verbs() == [("bootstrap", DOMAIN, plist)]
    assert act(mac, "start", ref).ok  # nor is starting a running one
    pid = mac.ctl.loaded[RELAY][0]
    assert act(mac, "restart", ref).ok and mac.ctl.verbs() == [("kickstart", "-k", target)]
    assert mac.ctl.loaded[RELAY][0] != pid
    assert act(mac, "disable", ref).ok
    assert mac.ctl.verbs() == [("bootout", target), ("disable", target)]
    assert RELAY not in mac.ctl.loaded and RELAY in mac.ctl.disabled
    assert act(mac, "enable", ref).ok
    assert mac.ctl.verbs() == [("enable", target), ("bootstrap", DOMAIN, plist)]
    assert mac.ctl.loaded[RELAY][0] and RELAY not in mac.ctl.disabled


def test_restart_of_a_daemon_that_is_not_loaded_bootstraps_it(mac):
    ref = installed(mac)["relay"]
    del mac.ctl.loaded[RELAY]
    assert act(mac, "restart", ref).ok
    assert [c[0] for c in mac.ctl.verbs()] == ["kickstart", "bootstrap"]


def test_timer_acts_by_the_verb_table(mac):
    ref = installed(mac, timer=True)["snap"]
    target = f"{DOMAIN}/moxi.demo.snap"
    assert act(mac, "start", ref).ok and mac.ctl.verbs() == [("kickstart", "-k", target)]
    assert act(mac, "stop", ref).ok and mac.ctl.verbs() == [("kill", "SIGTERM", target)]
    assert act(mac, "stop", ref).ok  # no run in progress: done
    assert act(mac, "restart", ref).ok
    assert [c[0] for c in mac.ctl.verbs()] == ["kill", "kickstart"]
    assert act(mac, "disable", ref).ok
    assert mac.ctl.verbs() == [("bootout", target), ("disable", target)]
    assert act(mac, "enable", ref).ok
    assert [c[0] for c in mac.ctl.verbs()] == ["enable", "bootstrap"]
    assert mac.ctl.loaded["moxi.demo.snap"][0] is None  # armed, not run


def test_a_refused_act_carries_the_supervisors_text(mac):
    ref = installed(mac)["relay"]
    mac.ctl.fail[("kickstart", RELAY)] = (1, "Operation not permitted")
    done = act(mac, "restart", ref)
    assert not done.ok and done.lines == (
        f"`launchctl kickstart -k {DOMAIN}/{RELAY}` failed: Operation not permitted",)


def test_a_foreign_label_reads_its_plist_and_needs_one_to_start(mac):
    plist = mac.agents / "com.example.backup.plist"
    plist.write_bytes(plistlib.dumps({"Label": "com.example.backup",
                                      "StartCalendarInterval": {"Hour": 3},
                                      "StandardOutPath": "/tmp/backup.log"},
                                     fmt=plistlib.FMT_BINARY))
    ref = mac.backend.foreign("com.example.backup", mac.fence)
    assert (ref.module, ref.kind, ref.ids, ref.paths) == (None, "timer",
                                                          ("com.example.backup",), (plist,))
    assert mac.backend.log(ref) == "/tmp/backup.log"
    assert mac.ctl.calls == []
    gone = mac.backend.foreign("com.example.gone", mac.fence)
    assert (gone.kind, gone.paths) == ("daemon", ())
    for what in ("enable", "start"):
        done = act(mac, what, gone)
        assert not done.ok and "has no plist at" in done.lines[0] and mac.ctl.calls == []
    assert act(mac, "disable", gone).ok


def test_own_is_the_moxi_label_namespace(mac):
    assert mac.backend.own("moxi.comms.daemon") and mac.backend.own("moxi.anything")
    assert not mac.backend.own("com.example.moxi")


def test_stop_id_and_start_id_take_one_label(mac):
    installed(mac)
    assert mac.backend.running([RELAY, "moxi.demo.none"], mac.fence) == {RELAY}
    assert mac.backend.stop_id(RELAY, mac.fence).ok and RELAY not in mac.ctl.loaded
    assert mac.backend.stop_id(RELAY, mac.fence).ok
    ran = mac.backend.start_id(RELAY, mac.fence)
    assert ran.ok and ran.argv[1] == "bootstrap" and RELAY in mac.ctl.loaded
    assert mac.backend.start_id(RELAY, mac.fence).ok
    missing = mac.backend.start_id("moxi.demo.none", mac.fence)
    assert not missing.ok and "no plist at" in missing.failure()


def test_a_loaded_job_with_no_pid_counts_as_running_for_stop(mac):
    installed(mac)
    mac.ctl.loaded[RELAY] = [None, 0]
    assert mac.backend.running([RELAY], mac.fence) == {RELAY}


def test_wrap_is_systemd_only(mac):
    done = mac.backend.wrap("com.example.backup", module="demo", key="snapshot.enabled",
                            name="backup", fence=mac.fence)
    assert not done.ok and done.lines == ("wrap is systemd only; com.example.backup runs "
                                          "as it is",)
    assert mac.backend.unwrap("com.example.backup", module="demo", fence=mac.fence).ok
    assert mac.backend.unwrap_all("demo", mac.fence) == [] and mac.ctl.calls == []


# -- the domain read ------------------------------------------------------------------------------

# The shape of `launchctl print gui/<uid>`, cut down; neutral labels.
RECORDED = """\
gui/501 = {
	type = login
	handle = 100004
	active count = 4
	service count = 6
	creator = loginwindow[155]
	security context = {
		uid unset
		asid = 100004
	}

	bringup time = 31 ms
	subdomains = {
		pid/612
	}

	services = {
		     612      -	com.example.agent
		       0      -	moxi.demo.idle
		       0      1 	moxi.demo.broken
		       -     -9	moxi.demo.killed
		    4242      0 	moxi.demo.relay
		       0      0 	moxi.demo.snap
		       0      -	com.example.off
	}

	unmanaged processes = {
		com.apple.something = {
			services = {
				  1  -	moxi.demo.ghost
			}
		}
	}

	endpoints = {
		"com.example.agent.xpc" = {
			port = 0x1234
		}
	}

	disabled services = {
		"com.example.off" => disabled
		"moxi.demo.relay" => enabled
		"moxi.demo.oldform" => true
		"moxi.demo.snap" => disabled
	}

	properties = partial import | uses default session
}
"""


def test_the_domain_read_parses_services_and_disabled_blocks():
    services, disabled = _launchd._domain(RECORDED)
    assert services == {"com.example.agent": (612, None), "moxi.demo.idle": (None, None),
                        "moxi.demo.broken": (None, 1), "moxi.demo.killed": (None, -9),
                        "moxi.demo.relay": (4242, 0), "moxi.demo.snap": (None, 0),
                        "com.example.off": (None, None)}
    assert disabled == {"com.example.off": False, "moxi.demo.relay": True,
                        "moxi.demo.oldform": False, "moxi.demo.snap": False}


def recorded(text):
    def run(cmd, *, env=None, timeout=None):
        assert cmd == ["launchctl", "print", DOMAIN]
        return subprocess.CompletedProcess(cmd, 0, text, "")
    return Fence("launchd", run)


def test_status_from_recorded_print(mac, monkeypatch):
    monkeypatch.setattr(_launchd, "_started", lambda pid: 1700000000.0)
    for name in ("relay", "idle", "broken", "killed", "unloaded", "oldform"):
        [group] = mac.backend.render(spec(name=name))
        group.files[0][0].write_text(group.files[0][1])
    [snap] = mac.backend.render(spec("timer", name="snap", schedule=Schedule(every=5)))
    snap.files[0][0].write_text(snap.files[0][1])
    refs = list(mac.backend.marked("demo"))
    refs.append(UnitRef("demo", "absent", "daemon", ("moxi.demo.absent",)))
    refs.append(mac.backend.foreign("com.example.agent", mac.fence))
    calls = []
    fence = recorded(RECORDED)
    inner = fence.runner
    fence.runner = lambda cmd, **kw: calls.append(cmd) or inner(cmd, **kw)
    states = {s.name: s for s in mac.backend.status(refs, fence)}
    assert len(calls) == 1
    got = {n: (s.state, s.pid, s.enabled) for n, s in states.items()}
    assert got == {"relay": ("active", 4242, True), "idle": ("inactive", None, True),
                   "broken": ("failed", None, True), "killed": ("failed", None, True),
                   "unloaded": ("inactive", None, True), "oldform": ("inactive", None, False),
                   "snap": ("inactive", None, False), "absent": ("missing", None, None),
                   "com.example.agent": ("active", 612, True)}
    assert states["relay"].since == 1700000000.0 and states["idle"].since is None
    assert states["unloaded"].detail == "not loaded"
    assert states["snap"].last == {"status": 0} and states["relay"].last is None
    assert states["relay"].log.endswith("/demo/logs/relay.log")
    assert states["absent"].log is None
    assert states["com.example.agent"].module is None
    assert states["com.example.agent"].log is None  # a foreign job with no plist here
    assert all(s.backend == "launchd" and s.wrapped is None for s in states.values())


TIMERS_PRINTED = """\
gui/501 = {
	services = {
		       0      3 	moxi.demo.failing
		     777      0 	moxi.demo.running
		       0      0 	moxi.demo.off
	}

	disabled services = {
		"moxi.demo.off" => disabled
	}
}
"""


def test_a_timer_reads_its_schedule_never_its_last_run(mac, monkeypatch):
    """A loaded timer not listed disabled is `active` whatever its last exit, with a pid
    or not; loaded and listed disabled is `inactive`; its last exit is in `last`."""
    monkeypatch.setattr(_launchd, "_started", lambda pid: 1700000000.0)
    for name in ("failing", "running", "off"):
        [group] = mac.backend.render(spec("timer", name=name, schedule=Schedule(every=5)))
        group.files[0][0].write_text(group.files[0][1])
    refs = {r.name: r for r in mac.backend.marked("demo")}
    states = {s.name: s for s in mac.backend.status(list(refs.values()),
                                                    recorded(TIMERS_PRINTED))}
    got = {n: (s.state, s.enabled, s.last) for n, s in states.items()}
    assert got == {"failing": ("active", True, {"status": 3}),
                   "running": ("active", True, {"status": 0}),
                   "off": ("inactive", False, {"status": 0})}
    assert states["running"].pid == 777 and "disabled" in states["off"].detail


def test_status_when_the_print_fails_is_unknown(mac):
    """Every unit the one print covers, daemon, timer and foreign label alike."""
    def run(cmd, *, env=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 113, "", "Could not find domain for gui: 501")
    refs = [UnitRef("demo", "relay", "daemon", (RELAY,)),
            UnitRef("demo", "snap", "timer", ("moxi.demo.snap",)),
            mac.backend.foreign("com.example.agent", None)]
    states = mac.backend.status(refs, Fence("launchd", run))
    assert [s.state for s in states] == ["unknown"] * 3
    assert all("Could not find domain" in s.detail and s.enabled is None
               and s.last is None for s in states)


SOCKETS_PRINTED = """\
gui/501 = {
	services = {
		       0      -	moxi.demo.idle
		    4242      0 	moxi.demo.serving
		       0     -9	moxi.demo.killed
		       0      0 	moxi.demo.off
		       0      0 	moxi.demo.old
	}

	disabled services = {
		"moxi.demo.off" => disabled
	}
}
"""


def test_a_socket_reads_the_daemons_job_and_is_never_failed(mac, monkeypatch):
    """A socket ref carries the daemon's label and plist. Loaded is `active`, with a pid
    or not and whatever the last exit; not loaded with its plist is `inactive`; no plist,
    or a plist with no Sockets, is `missing`. A daemon killed non-zero reads `failed`
    beside its `active` socket until the next connection starts it."""
    monkeypatch.setattr(_launchd, "_started", lambda pid: 1700000000.0)
    sock = Socket("port", ("127.0.0.1",), 8470)
    for name in ("idle", "serving", "killed", "off", "unloaded"):
        [group] = mac.backend.render(spec(name=name, socket=sock))
        group.files[0][0].write_text(group.files[0][1])
    [old] = mac.backend.render(spec(name="old"))
    old.files[0][0].write_text(old.files[0][1])
    names = ("idle", "serving", "killed", "off", "unloaded", "old", "absent")
    refs = []
    for name in names:
        label = f"moxi.demo.{name}"
        paths = (mac.backend.plist(label),)
        refs += [UnitRef("demo", name, "daemon", (label,), paths),
                 UnitRef("demo", name, "socket", (label,), paths)]
    calls = []
    fence = recorded(SOCKETS_PRINTED)
    inner = fence.runner
    fence.runner = lambda cmd, **kw: calls.append(cmd) or inner(cmd, **kw)
    states = mac.backend.status(refs, fence)
    assert len(calls) == 1
    got = {(s.name, s.kind): (s.state, s.enabled) for s in states}
    assert got == {
        ("idle", "daemon"): ("inactive", True), ("idle", "socket"): ("active", True),
        ("serving", "daemon"): ("active", True), ("serving", "socket"): ("active", True),
        ("killed", "daemon"): ("failed", True), ("killed", "socket"): ("active", True),
        ("off", "daemon"): ("inactive", False), ("off", "socket"): ("active", False),
        ("unloaded", "daemon"): ("inactive", True),
        ("unloaded", "socket"): ("inactive", True),
        ("old", "daemon"): ("inactive", True), ("old", "socket"): ("missing", None),
        ("absent", "daemon"): ("missing", None), ("absent", "socket"): ("missing", None)}
    sockets = {s.name: s for s in states if s.kind == "socket"}
    assert all(s.pid is None and s.since is None and s.epoch is None and s.last is None
               and s.backend == "launchd" for s in sockets.values())
    assert sockets["idle"].detail == "loaded, listening"
    assert sockets["unloaded"].detail == "not loaded"
    assert "has no Sockets" in sockets["old"].detail
    assert "moxi install demo" in sockets["old"].detail
    assert "no plist at" in sockets["absent"].detail
    assert sockets["idle"].log.endswith("/demo/logs/idle.log")
    assert sockets["old"].log is None and sockets["absent"].log is None


def test_a_socket_is_unknown_when_the_print_fails(mac):
    [group] = mac.backend.render(spec(socket=Socket("port", ("127.0.0.1",), 8470)))
    group.files[0][0].write_text(group.files[0][1])
    ref = UnitRef("demo", "relay", "socket", (RELAY,), (group.files[0][0],))

    def run(cmd, *, env=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 113, "", "Could not find domain for gui: 501")
    [state] = mac.backend.status([ref], Fence("launchd", run))
    assert (state.state, state.enabled, state.epoch) == ("unknown", None, None)
    assert "Could not find domain" in state.detail


def test_status_through_the_core_gives_the_socket_after_its_daemon(mac):
    """An idle socket daemon: the daemon `inactive`, its socket `active` with no epoch,
    both from the one print; a connection's start gives the daemon its pid, and a kill
    leaves it `failed` while the socket still reads `active`."""
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=shell("127.0.0.1"))
    mac.apply(units.plan("demo"))
    mac.ctl.calls.clear()
    rows = units.status("demo")
    assert mac.ctl.calls == [("print", DOMAIN)]
    assert [(r.name, r.kind, r.state, r.enabled, r.epoch) for r in rows] == [
        ("relay", "daemon", "inactive", True, None),
        ("relay", "socket", "active", True, None)]
    assert rows[1].last is None
    mac.ctl.loaded[RELAY] = [4100, None]
    assert [r.state for r in units.status("demo")] == ["active", "active"]
    mac.ctl.loaded[RELAY] = [None, -9]
    assert [r.state for r in units.status("demo")] == ["failed", "active"]
    assert units.disable("demo", ["relay"])[0].ok
    rows = units.status("demo")
    assert [(r.state, r.enabled) for r in rows] == [("inactive", False), ("inactive", False)]


def test_since_reads_the_process_start_time():
    import psutil

    assert _launchd._started(os.getpid()) == psutil.Process(os.getpid()).create_time()


def test_the_backend_object_is_found_for_launchd(monkeypatch):
    monkeypatch.setattr(_backend, "backend", lambda: "launchd")
    impl = _backend.implementation()
    assert impl is _launchd.BACKEND and isinstance(impl, Backend)
    assert (impl.name, impl.supervisor, impl.encoding) == ("launchd", "launchd", "utf-8")
    assert not hasattr(impl, "runner")


def test_the_default_paths_are_the_users_launch_agents_and_uid(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/ana")
    backend = LaunchdBackend()
    assert backend.agents == Path("/Users/ana/Library/LaunchAgents")
    assert backend.domain == f"gui/{os.getuid()}"


# -- foreign with no fence, ids, wraps, and a read that fails -----------------------------------

def test_foreign_with_no_fence_reads_the_same_unit_and_makes_no_call(mac):
    plist = mac.agents / "com.example.backup.plist"
    plist.write_bytes(plistlib.dumps({"Label": "com.example.backup", "StartInterval": 60}))
    with_fence = mac.backend.foreign("com.example.backup", mac.fence)
    assert mac.backend.foreign("com.example.backup", None) == with_fence
    assert with_fence.kind == "timer" and with_fence.paths == (plist,)
    assert mac.backend.foreign("com.example.gone", None) == UnitRef(
        None, "com.example.gone", "daemon", ("com.example.gone",))
    assert mac.ctl.calls == []


def test_ids_is_the_label_whether_or_not_a_plist_is_there(mac):
    assert mac.backend.ids("demo", "relay", "daemon") == (RELAY,)
    assert mac.backend.ids("demo", "snap", "timer") == ("moxi.demo.snap",)
    refs = installed(mac, timer=True)
    assert refs["relay"].ids == mac.backend.ids("demo", "relay", "daemon")
    assert refs["snap"].ids == mac.backend.ids("demo", "snap", "timer")
    assert mac.ctl.calls == []


def test_wraps_is_empty_on_launchd(mac):
    assert mac.backend.wraps("demo") == () and mac.ctl.calls == []


def test_running_raises_when_the_domain_read_fails(mac):
    installed(mac)
    assert mac.backend.running([], mac.fence) == frozenset() and mac.ctl.calls == []
    mac.ctl.fail[("print", None)] = (113, "Could not find domain for gui: 501")
    with pytest.raises(_backend.ReadError) as raised:
        mac.backend.running([RELAY], mac.fence)
    assert raised.value.ran.argv == ("launchctl", "print", DOMAIN)
    assert "Could not find domain for gui: 501" in str(raised.value)


def test_a_failed_domain_read_stops_nothing_and_records_nothing(mac):
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    mac.apply(units.plan("demo"))
    mac.ctl.calls.clear()
    mac.ctl.fail[("print", None)] = (1, "Operation not permitted")
    record: list = []
    [done] = units.stop("demo", record=record)
    assert not done.ok and done.lines[0].startswith("cannot tell which units of demo run")
    assert "Operation not permitted" in done.lines[0]
    assert record == [] and RELAY in mac.ctl.loaded and mac.ctl.verbs() == []


def test_stop_with_replace_takes_an_unmarked_job_by_its_label_and_resume_starts_it(mac):
    mac.script("moxi-demo-relay")
    mac.module(DAEMONS=DAEMON)
    (mac.agents / f"{RELAY}.plist").write_bytes(plistlib.dumps(
        {"Label": RELAY, "ProgramArguments": ["/bin/true"], "RunAtLoad": True}))
    mac.ctl.loaded[RELAY] = [99, None]
    record: list = []
    [done] = units.stop("demo", replace=True, record=record)
    assert done.ok and done.lines == (f"{RELAY} stopped",)
    assert [(e.unit, e.marked, e.backend) for e in record] == [(RELAY, False, "launchd")]
    assert RELAY not in mac.ctl.loaded
    [started] = units.resume(record)
    assert started.ok and RELAY in mac.ctl.loaded


def test_status_gives_one_state_per_unit_in_order_with_only_launchds_result(mac):
    for name in ("relay", "idle"):
        [group] = mac.backend.render(spec(name=name))
        group.files[0][0].write_text(group.files[0][1])
    [snap] = mac.backend.render(spec("timer", name="snap", schedule=Schedule(every=5)))
    snap.files[0][0].write_text(snap.files[0][1])
    mac.ctl.loaded.update({RELAY: [321, None], "moxi.demo.snap": [None, 2],
                           "com.example.agent": [612, None]})
    marked = {r.name: r for r in mac.backend.marked("demo")}
    refs = [mac.backend.foreign("com.example.agent", None), marked["snap"], marked["idle"],
            marked["relay"], mac.backend.foreign("com.example.gone", None)]
    states = mac.backend.status(refs, mac.fence)
    assert mac.ctl.calls == [("print", DOMAIN)]
    assert [(s.module, s.name, s.kind) for s in states] == [
        (r.module, r.name, r.kind) for r in refs]
    assert [s.state for s in states] == ["active", "active", "inactive", "active", "missing"]
    assert states[1].last == {"status": 2}
    assert all(s.last is None for s in states if s.kind == "daemon")


def test_status_through_the_core_adds_the_run_line_beside_launchds_result(mac):
    mac.script("moxi-demo-relay")
    mac.script("moxi-demo-snap")
    mac.module(DAEMONS=DAEMON, TIMERS=TIMER)
    changes = units.plan("demo")
    for change in changes:
        if change.digest:
            _state.record("demo", change.name, change.digest)
    mac.apply(changes)
    mac.ctl.loaded["moxi.demo.snapshot"] = [None, 2]
    line = {"unit": "demo/snapshot", "at": "2026-01-01T00:00:00Z", "seconds": 0.5,
            "result": "ran", "exit": 2}
    files.append_record(moxi.paths.Layout("demo").state_dir() / "runs.jsonl", line)
    mac.ctl.calls.clear()
    rows = units.status("demo", foreign=["com.example.agent"])
    assert mac.ctl.calls == [("print", DOMAIN)]
    assert [(r.module, r.name, r.kind) for r in rows] == [
        ("demo", "relay", "daemon"), ("demo", "snapshot", "timer"),
        (None, "com.example.agent", "daemon")]
    assert rows[1].last == {**line, "supervisor": {"status": 2}}
    assert rows[0].last is None and rows[2].state == "missing"


# -- on a real Mac -------------------------------------------------------------------------------

darwin = pytest.mark.skipif(sys.platform != "darwin",
                            reason="needs macOS: run it on a Mac")


@darwin
def test_plutil_lints_every_rendered_plist(mac):
    if shutil.which("plutil") is None:
        pytest.skip("needs plutil")
    groups = [*mac.backend.render(spec(stop="process", cwd="/tmp",
                                       socket=Socket("port", ("127.0.0.1", "::1"), 8470))),
              *mac.backend.render(spec(enabled=False, command=("/opt/a&b", "<x>"))),
              *mac.backend.render(spec("timer", name="t1", schedule=Schedule(every=5),
                                       switch="snapshot.enabled")),
              *mac.backend.render(spec("timer", name="t2", schedule=Schedule(
                  at=((9, 0), (17, 30)), days=("mon", "sun"))))]
    for group in groups:
        for path, text in group.files:
            path.write_text(text)
            done = subprocess.run(["plutil", "-lint", str(path)], capture_output=True,
                                  text=True)
            assert done.returncode == 0, done.stdout + done.stderr


@darwin
def test_proof_the_real_launchctl_on_a_test_label(tmp_path, monkeypatch):
    """A throwaway daemon and timer under labels no one else uses, their plists in tmp
    (never in ~/Library/LaunchAgents), booted out in a `finally`."""
    if os.environ.get("MOXI_TEST_REAL_SUPERVISOR") != "1":
        pytest.skip("a proof row: set MOXI_TEST_REAL_SUPERVISOR=1 on a Mac")
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "home"))
    suffix = os.urandom(4).hex()
    backend = LaunchdBackend(agents=tmp_path / "agents", gate="/usr/bin/true")
    fence = Fence("launchd")
    module = f"proof{suffix}"
    daemon = spec(module=module, name="sleeper", command=("/bin/sleep", "600"))
    timer = spec("timer", module=module, name="tick", command=("/usr/bin/true",),
                 schedule=Schedule(every=60))
    refs = {}
    try:
        for s in (daemon, timer):
            [group] = backend.render(s)
            path, text = group.files[0]
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
        refs = {r.name: r for r in backend.marked(module)}
        for name in refs:
            assert backend.act("enable", refs[name], fence).ok
        states = {s.name: s for s in backend.status(list(refs.values()), fence)}
        assert states["sleeper"].state == "active" and states["sleeper"].pid
        assert states["sleeper"].enabled is True and states["tick"].state == "active"
        pid = states["sleeper"].pid
        assert backend.act("restart", refs["sleeper"], fence).ok
        [after] = backend.status([refs["sleeper"]], fence)
        assert after.pid and after.pid != pid
        assert backend.act("start", refs["sleeper"], fence).ok  # already loaded
        assert backend.act("start", refs["tick"], fence).ok
        assert backend.act("disable", refs["sleeper"], fence).ok
        [off] = backend.status([refs["sleeper"]], fence)
        assert (off.state, off.enabled) == ("inactive", False)
        assert backend.act("stop", refs["sleeper"], fence).ok  # not loaded
        assert backend.act("enable", refs["sleeper"], fence).ok
        [on] = backend.status([refs["sleeper"]], fence)
        assert (on.state, on.enabled) == ("active", True)
    finally:
        for ref in refs.values():
            backend.stop_id(ref.ids[0], fence)
            fence.run("enable", backend.target(ref.ids[0]))


# A socket daemon for the proof rows: it takes `Listeners` and `Listeners-1` by name,
# appends one line per start to the file in its first argument (its pid, then how many fds
# each key gave it), answers each connection `ok`, and exits 0 on a line `exit` or after
# 20 idle seconds.
SOCKET_DAEMON = '''\
import ctypes, os, select, socket, sys, time
lib = ctypes.CDLL(None)
lib.launch_activate_socket.argtypes = [
    ctypes.c_char_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_int)),
    ctypes.POINTER(ctypes.c_size_t)]
held = {}
for key in ("Listeners", "Listeners-1"):
    fds = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_size_t()
    err = lib.launch_activate_socket(key.encode(), ctypes.byref(fds), ctypes.byref(count))
    held[key] = [] if err else [fds[i] for i in range(count.value)]
with open(sys.argv[1], "a") as out:
    out.write(f"{os.getpid()} " + " ".join(f"{k}={len(v)}" for k, v in held.items()) + "\\n")
socks = [socket.socket(fileno=fd) for fds in held.values() for fd in fds]
deadline = time.monotonic() + 20
while socks and time.monotonic() < deadline:
    ready, _, _ = select.select(socks, [], [], 1)
    for sock in ready:
        conn, _ = sock.accept()
        with conn:
            line = conn.recv(64).strip()
            conn.sendall(b"ok\\n")
        if line == b"exit":
            sys.exit(0)
'''


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _ask(host: str, port: int, line: bytes) -> bytes:
    import socket

    with socket.create_connection((host, port), timeout=10) as conn:
        conn.sendall(line + b"\n")
        return conn.recv(64).strip()


def _until(read, check, seconds=15.0):
    import time

    deadline = time.monotonic() + seconds
    while True:
        value = read()
        if check(value) or time.monotonic() > deadline:
            return value
        time.sleep(0.2)


def _socket_label(tmp_path, monkeypatch, addresses):
    """A throwaway socket daemon under a test label, its plist in tmp."""
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "home"))
    module = f"proof{os.urandom(4).hex()}"
    script = tmp_path / "shell.py"
    script.write_text(SOCKET_DAEMON)
    starts = tmp_path / "starts.txt"
    port = _free_port()
    backend = LaunchdBackend(agents=tmp_path / "agents", gate="/usr/bin/true")
    [group] = backend.render(spec(module=module, name="shell",
                                  command=(sys.executable, "-I", str(script), str(starts)),
                                  socket=Socket("port", addresses, port)))
    path, text = group.files[0]
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    [ref] = backend.marked(module)
    sock = UnitRef(module, "shell", "socket", ref.ids, ref.paths)
    return backend, ref, sock, port, starts


@darwin
def test_proof_a_socket_daemon_starts_at_a_connection_on_either_address(tmp_path,
                                                                        monkeypatch):
    """Not started by its bootstrap; a connection to either address starts it and it holds
    both keys; after `kill -9` it reads `failed` and stays down until the next connection;
    its socket reads `active` throughout."""
    import signal
    import time

    if os.environ.get("MOXI_TEST_REAL_SUPERVISOR") != "1":
        pytest.skip("a proof row: set MOXI_TEST_REAL_SUPERVISOR=1 on a Mac")
    backend, ref, sock, port, starts = _socket_label(tmp_path, monkeypatch,
                                                     ("127.0.0.1", "::1"))
    fence = Fence("launchd")

    def read():
        return backend.status([ref, sock], fence)

    try:
        assert backend.act("enable", ref, fence).ok
        daemon, socket_state = read()
        assert (daemon.state, daemon.pid) == ("inactive", None)
        assert (socket_state.state, socket_state.epoch) == ("active", None)
        assert not starts.exists()
        assert _ask("127.0.0.1", port, b"hi") == b"ok"
        daemon, socket_state = _until(read, lambda s: s[0].pid)
        assert daemon.state == "active" and socket_state.state == "active"
        [line] = starts.read_text().splitlines()
        assert line.split()[1:] == ["Listeners=1", "Listeners-1=1"]
        os.kill(daemon.pid, signal.SIGKILL)
        daemon, socket_state = _until(read, lambda s: not s[0].pid)
        assert daemon.state == "failed" and socket_state.state == "active"
        time.sleep(5)  # past ThrottleInterval: launchd must not start it on its own
        daemon, _ = read()
        assert daemon.pid is None and len(starts.read_text().splitlines()) == 1
        assert _ask("::1", port, b"exit") == b"ok"
        daemon, socket_state = _until(read, lambda s: s[0].state == "inactive")
        assert len(starts.read_text().splitlines()) == 2
        assert (daemon.state, daemon.pid, socket_state.state) == ("inactive", None, "active")
    finally:
        backend.stop_id(ref.ids[0], fence)
        fence.run("enable", backend.target(ref.ids[0]))


@darwin
def test_proof_record_what_launchd_shows_for_an_address_it_cannot_bind(tmp_path,
                                                                     monkeypatch, capsys):
    """`Listeners-1` on 192.0.2.1, which the Mac doesn't have. A record row: it prints the
    bootstrap's result and `launchctl print` of the label for the report, and holds only
    that the socket read is never `failed`."""
    if os.environ.get("MOXI_TEST_REAL_SUPERVISOR") != "1":
        pytest.skip("a proof row: set MOXI_TEST_REAL_SUPERVISOR=1 on a Mac")
    backend, ref, sock, port, _ = _socket_label(tmp_path, monkeypatch,
                                                ("127.0.0.1", "192.0.2.1"))
    fence = Fence("launchd")
    try:
        acted = backend.act("enable", ref, fence)
        shown = fence.run("print", backend.domain)
        job = subprocess.run(["launchctl", "print", backend.target(ref.ids[0])],
                             capture_output=True, text=True)
        daemon, socket_state = backend.status([ref, sock], fence)
        try:
            answer = _ask("127.0.0.1", port, b"exit")
        except OSError as exc:
            answer = f"no answer: {exc}"
        with capsys.disabled():
            print(f"\nenable: ok={acted.ok} {acted.lines}")
            print(f"services line: {[l for l in shown.stdout.splitlines() if ref.ids[0] in l]}")
            print(f"print of the label (exit {job.returncode}):\n{job.stdout}{job.stderr}")
            print(f"status: daemon {daemon.state} {daemon.detail!r}; "
                  f"socket {socket_state.state} {socket_state.detail!r}")
            print(f"a connection to 127.0.0.1: {answer!r}")
        assert socket_state.state != "failed"
    finally:
        backend.stop_id(ref.ids[0], fence)
        fence.run("enable", backend.target(ref.ids[0]))
