"""The systemd backend: unit text, marks, apply's order, acts, the status read and a foreign
timer's wrap, with a fake runner and recorded `systemctl show` output. No test here calls
the real user manager or writes under the real unit directory."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import types
from pathlib import Path

import pytest

import moxi.paths
from moxi import units
from moxi.units import _backend, _systemd
from moxi.units._backend import (
    Backend, ReadError, RenderError, Schedule, Socket, Spec, UnitRef)
from moxi.units._fake import FakeSupervisor
from moxi.units._fence import Fence
from moxi.units._systemd import SystemdBackend, quote
from moxi.units._types import Wrapped
from moxi_dev.testing import console_script

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
doc = "The shell's port."
read = "start"
"""


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    """A scripts directory in tmp holding `moxi`, the gate."""
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    console_script(scripts, "moxi")
    return scripts


@pytest.fixture
def unit_root(tmp_path, monkeypatch):
    """The unit directory the backend reads from the environment, in tmp."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    root = tmp_path / "cfg" / "systemd" / "user"
    root.mkdir(parents=True)
    return root


def _script(path: Path, body: str = "#!/bin/sh\n") -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


def spec(kind="daemon", **kw) -> Spec:
    base = dict(module="comms", name="daemon", kind=kind,
                command=("/opt/ana/bin/moxi-comms-daemon",), doc="the delivery daemon",
                entry={})
    return Spec(**{**base, **kw})


# -- the unit text -----------------------------------------------------------------------------

def test_a_daemon_unit_reads_as_the_design_gives_it(unit_root):
    [group] = SystemdBackend().render(spec(stop="process", memory_high="512M",
                                           memory_swap_max="0"))
    [(path, text)] = group.files
    assert (group.kind, path) == ("daemon", unit_root / "moxi-comms-daemon.service")
    assert text == textwrap.dedent("""\
        # moxi unit: comms/daemon
        [Unit]
        Description=Moxi comms: the delivery daemon

        [Service]
        Type=simple
        ExecStart=/opt/ana/bin/moxi-comms-daemon
        Restart=on-failure
        RestartSec=2
        KillMode=process
        MemoryHigh=512M
        MemorySwapMax=0

        [Install]
        WantedBy=default.target
        """)


def test_a_plain_daemon_has_no_kill_mode_and_no_memory_lines(unit_root):
    [group] = SystemdBackend().render(spec(command=("/opt/ana/bin/x", "--port", "1"),
                                           cwd="/srv/ana"))
    text = group.files[0][1]
    assert "KillMode" not in text and "Memory" not in text
    assert "ExecStart=/opt/ana/bin/x --port 1\nWorkingDirectory=/srv/ana\n" in text


@pytest.mark.skipif(os.name == "nt", reason="the golden text writes the gate's path "
                    "bare, as a POSIX path is; Windows never uses systemd")
def test_a_timer_is_a_service_through_the_gate_and_a_schedule(unit_root, bin_dir):
    [group] = SystemdBackend().render(spec(
        "timer", module="notes", name="snapshot", doc="commit a snapshot of the notes",
        command=("/opt/ana/bin/moxi-notes-snapshot",), switch="snapshot.enabled",
        schedule=Schedule(every=5)))
    (service, stext), (timer, ttext) = group.files
    assert (service.name, timer.name) == ("moxi-notes-snapshot.service",
                                          "moxi-notes-snapshot.timer")
    assert stext == textwrap.dedent(f"""\
        # moxi unit: notes/snapshot
        [Unit]
        Description=Moxi notes: commit a snapshot of the notes

        [Service]
        Type=oneshot
        ExecStart={bin_dir}/moxi run --if notes.snapshot.enabled --record notes/snapshot -- /opt/ana/bin/moxi-notes-snapshot
        """)
    assert ttext == textwrap.dedent("""\
        # moxi unit: notes/snapshot
        [Unit]
        Description=Moxi notes: commit a snapshot of the notes (schedule)

        [Timer]
        OnActiveSec=5min
        OnUnitActiveSec=5min
        AccuracySec=1s

        [Install]
        WantedBy=timers.target
        """)


@pytest.mark.parametrize("schedule, lines", [
    (Schedule(every=1), ["OnActiveSec=1min", "OnUnitActiveSec=1min"]),
    (Schedule(every=1440), ["OnActiveSec=1440min", "OnUnitActiveSec=1440min"]),
    (Schedule(at=((9, 0), (17, 30))),
     ["OnCalendar=*-*-* 09:00:00", "OnCalendar=*-*-* 17:30:00", "Persistent=false"]),
    (Schedule(at=((9, 0),), days=("mon", "fri")),
     ["OnCalendar=Mon,Fri *-*-* 09:00:00", "Persistent=false"]),
    (Schedule(at=((0, 5),), days=("sat", "sun")),
     ["OnCalendar=Sat,Sun *-*-* 00:05:00", "Persistent=false"]),
])
def test_the_schedule_translation(unit_root, bin_dir, schedule, lines):
    [group] = SystemdBackend().render(spec("timer", schedule=schedule))
    timer = group.files[1][1].splitlines()
    start = timer.index("[Timer]") + 1
    assert timer[start:start + len(lines)] == lines
    assert timer[start + len(lines)] == "AccuracySec=1s"


def test_a_switchless_timer_runs_its_gate_without_if(unit_root, bin_dir):
    [group] = SystemdBackend().render(spec("timer", schedule=Schedule(every=5),
                                           cwd="/srv/a"))
    assert "--if" not in group.files[0][1]
    assert "\nWorkingDirectory=/srv/a\n" in group.files[0][1]


def test_a_timer_without_the_gate_script_cannot_be_written(unit_root, tmp_path,
                                                           monkeypatch):
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: tmp_path / "empty")
    with pytest.raises(RenderError) as caught:
        SystemdBackend().render(spec("timer", schedule=Schedule(every=5)))
    assert caught.value.cause == "command" and "no moxi in this environment" in str(
        caught.value)
    assert SystemdBackend().render(spec())  # a daemon needs no gate


def test_a_socket_is_one_unit_per_address_and_its_service_has_no_install(unit_root):
    daemon, socket = SystemdBackend().render(spec(
        module="ui", name="shell", doc="the shell", command=("/opt/ana/bin/moxi-ui-shell",),
        socket=Socket("port", ("127.0.0.1", "::1"), 8470)))
    assert daemon.kind == "daemon" and socket.kind == "socket"
    (first, ftext), (second, stext) = socket.files
    assert (first, second) == (unit_root / "moxi-ui-shell.socket",
                               unit_root / "moxi-ui-shell-1.socket")
    assert ftext == textwrap.dedent("""\
        # moxi unit: ui/shell
        [Unit]
        Description=Moxi ui: the shell (socket 127.0.0.1)

        [Socket]
        ListenStream=127.0.0.1:8470
        Service=moxi-ui-shell.service

        [Install]
        WantedBy=sockets.target
        """)
    assert stext == textwrap.dedent("""\
        # moxi unit: ui/shell
        [Unit]
        Description=Moxi ui: the shell (socket ::1)

        [Socket]
        ListenStream=[::1]:8470
        BindIPv6Only=ipv6-only
        Service=moxi-ui-shell.service

        [Install]
        WantedBy=sockets.target
        """)
    [(path, text)] = daemon.files
    assert path == unit_root / "moxi-ui-shell.service"
    assert text == textwrap.dedent("""\
        # moxi unit: ui/shell
        [Unit]
        Description=Moxi ui: the shell

        [Service]
        Type=simple
        ExecStart=/opt/ana/bin/moxi-ui-shell
        Sockets=moxi-ui-shell.socket moxi-ui-shell-1.socket
        Restart=on-failure
        RestartSec=2
        """)


def test_an_address_left_out_keeps_the_others_names_and_leaves_the_service_its_set(
        unit_root):
    """`::1` left out (no IPv6 loopback) writes no `-1`, and an address after it keeps its
    place's name; the service names only what is written."""
    daemon, socket = SystemdBackend().render(spec(
        module="ui", name="shell", doc="the shell",
        socket=Socket("port", ("127.0.0.1", "::1", "10.0.0.7"), 8470, left_out=("::1",))))
    assert [p.name for p, _ in socket.files] == ["moxi-ui-shell.socket",
                                                 "moxi-ui-shell-2.socket"]
    assert "ListenStream=10.0.0.7:8470\nService=" in socket.files[1][1]
    assert "\nSockets=moxi-ui-shell.socket moxi-ui-shell-2.socket\n" in daemon.files[0][1]
    assert "[Install]" not in daemon.files[0][1]


def test_a_scoped_ipv6_address_has_its_percent_doubled(unit_root):
    _, socket = SystemdBackend().render(spec(
        socket=Socket("port", ("fe80::1%eth0",), 8470)))
    assert "ListenStream=[fe80::1%%eth0]:8470\nBindIPv6Only=ipv6-only\n" in (
        socket.files[0][1])


def test_a_unit_declared_disabled_says_so_under_the_mark_in_each_file(unit_root, bin_dir):
    backend = SystemdBackend()
    groups = backend.render(spec(enabled=False, socket=Socket("port", ("127.0.0.1",), 1)))
    groups += backend.render(spec("timer", enabled=False, schedule=Schedule(every=5)))
    texts = [text for group in groups for _, text in group.files]
    assert len(texts) == 4
    for text in texts:
        assert text.split("\n")[1] == "# moxi: enabled = false"
        assert backend.mark(text) == "comms/daemon" and not backend.enabled(text)
    assert backend.enabled(backend.render(spec())[0].files[0][1])


@pytest.mark.parametrize("arg, written", [
    ("/opt/ana/bin/x", "/opt/ana/bin/x"),
    ("--port=8470", "--port=8470"),
    ("a b", '"a b"'),
    ('say "hi"', '"say \\"hi\\""'),
    ("back\\slash", '"back\\\\slash"'),
    ("50%", "50%%"),
    ("$HOME", "$$HOME"),
    ("it's", '"it\'s"'),
    (";", '";"'),
    ("", '""'),
    ("two\nlines", '"two\\nlines"'),
    ("a b%$", '"a b%%$$"'),
])
def test_arguments_are_quoted_by_systemds_rules(arg, written):
    assert quote(arg) == written


def test_a_description_is_one_line_with_percent_doubled(unit_root):
    [group] = SystemdBackend().render(spec(doc="100% of it\nand more\\"))
    assert "\nDescription=Moxi comms: 100%% of it and more\n" in group.files[0][1]


def test_a_cwd_that_cannot_be_written_is_refused(unit_root):
    with pytest.raises(RenderError) as caught:
        SystemdBackend().render(spec(cwd="/srv/a\nExecStartPre=/bin/evil"))
    assert caught.value.cause == "declaration"


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="no systemd-analyze")
def test_systemd_analyze_verifies_every_generated_file(tmp_path, unit_root, bin_dir):
    """`systemd-analyze --user verify` on each kind of unit the backend writes, offline:
    its own runtime directory in tmp, never the user manager."""
    tool = _script(bin_dir / "moxi-demo-relay")
    backend = SystemdBackend()
    groups = backend.render(spec(module="demo", name="relay", command=(str(tool), "a b",
                                                                        "50%", "$x"),
                                 stop="process", memory_high="64M", memory_swap_max="0",
                                 cwd=str(tmp_path),
                                 socket=Socket("port", ("127.0.0.1", "::1"), 8470)))
    groups += backend.render(spec("timer", module="demo", name="every",
                                  command=(str(tool),), switch="every.enabled",
                                  schedule=Schedule(every=5)))
    groups += backend.render(spec("timer", module="demo", name="at", command=(str(tool),),
                                  enabled=False,
                                  schedule=Schedule(at=((9, 0), (17, 30)),
                                                    days=("mon", "fri"))))
    paths = []
    for group in groups:
        for path, text in group.files:
            path.write_text(text)
            paths.append(str(path))
    # A short runtime directory: the system's own user sockets put paths under it, and a
    # socket path has a short limit.
    runtime = tempfile.mkdtemp(prefix="moxi-sa-")
    try:
        env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
               "XDG_RUNTIME_DIR": runtime, "PATH": "/usr/bin:/bin",
               "SYSTEMD_LOG_LEVEL": "notice"}
        done = subprocess.run(["systemd-analyze", "--user", "verify", *paths], env=env,
                              capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(runtime, ignore_errors=True)
    assert len(paths) == 7
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


# -- marks and the marked units ---------------------------------------------------------------

def test_marked_reads_this_modules_units_from_their_files(unit_root, bin_dir):
    backend = SystemdBackend()
    for group in (backend.render(spec(module="demo", name="relay",
                                      socket=Socket("port", ("127.0.0.1",), 1)))
                  + backend.render(spec("timer", module="demo", name="snap",
                                        schedule=Schedule(every=5), enabled=False))
                  + backend.render(spec(module="demo-x", name="y"))):
        for path, text in group.files:
            path.write_text(text)
    (unit_root / "moxi-demo-hand.service").write_text("[Service]\nExecStart=/bin/true\n")
    (unit_root / "moxi-demo-copy.service").write_text("# moxi unit: demo/relay\n")
    (unit_root / "moxi-demo-relay.service.d").mkdir()
    (unit_root / "moxi-demo-relay.service.d" / "50-moxi-demo.conf").write_text(
        "# moxi wrap: demo/x\n")
    refs = {(r.name, r.kind): r for r in backend.marked("demo")}
    assert set(refs) == {("relay", "daemon"), ("relay", "socket"), ("snap", "timer")}
    assert refs[("relay", "daemon")].ids == ("moxi-demo-relay.service",)
    assert refs[("relay", "socket")].ids == ("moxi-demo-relay.socket",)
    snap = refs[("snap", "timer")]
    assert snap.ids == ("moxi-demo-snap.timer", "moxi-demo-snap.service")
    assert snap.paths == (unit_root / "moxi-demo-snap.service",
                          unit_root / "moxi-demo-snap.timer")
    assert snap.enabled is False and refs[("relay", "daemon")].enabled is True
    assert [r.name for r in backend.marked("demo-x")] == ["y"]


def test_the_mark_is_the_first_line_only(unit_root):
    backend = SystemdBackend()
    assert backend.mark("# moxi unit: a/b\n[Unit]\n") == "a/b"
    assert backend.mark("[Unit]\n# moxi unit: a/b\n") is None
    assert backend.mark("# moxi unit: \n") is None


def test_the_unit_directory_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    assert SystemdBackend().root == tmp_path / "x" / "systemd" / "user"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert SystemdBackend().root == tmp_path / "h" / ".config" / "systemd" / "user"


def test_the_backend_satisfies_the_protocol_and_is_found_for_systemd(monkeypatch):
    assert isinstance(_systemd.BACKEND, Backend)
    monkeypatch.setattr(_backend, "backend", lambda: "systemd")
    assert _backend.implementation() is _systemd.BACKEND


# -- plan and apply through the core, with the fake runner -------------------------------------

@pytest.fixture
def w(site, tmp_path, unit_root, bin_dir, monkeypatch):
    """A fake module `demo`, the systemd backend over a tmp unit directory, and a fake
    supervisor as the runner."""
    sup = FakeSupervisor(unit_root, linger=True)
    lines: list[str] = []
    monkeypatch.setattr(_systemd, "_user", lambda: "ana")

    def module(name="demo", **names):
        pkg = f"moxi_{name}"
        site.write(f"{pkg}/settings.toml", SETTINGS)
        names.setdefault("SETTINGS", "'settings.toml'")
        for mod in [m for m in __import__("sys").modules if m.startswith(pkg)]:
            del __import__("sys").modules[mod]
        site.module(name, **names)
        moxi.modules.reset()

    def apply(changes, **kw):
        sup.reload()
        return units.apply(changes, runner=sup, say=lines.append, **kw)

    def script(name):
        return console_script(bin_dir, name)

    with _backend.use(SystemdBackend()):
        yield types.SimpleNamespace(root=unit_root, sup=sup, lines=lines, module=module,
                                    apply=apply, script=script, tmp=tmp_path, site=site)


DAEMON = "{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}}"
TIMER = ("{'snap': {'command': 'moxi-demo-snap', 'every': '5m', 'doc': 'take a snapshot', "
         "'switch': 'snapshot.enabled'}}")


def test_a_new_daemon_is_written_reloaded_and_enabled_now(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    [done] = w.apply(units.plan("demo"))
    assert done.ok
    assert w.sup.calls == [("daemon-reload",), ("enable", "--now", "moxi-demo-relay.service"),
                           ("loginctl", "show-user", "ana", "-p", "Linger")]
    path = w.root / "moxi-demo-relay.service"
    assert path.read_text().startswith("# moxi unit: demo/relay\n")
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    assert units.plan("demo")[0].action == "keep"


def test_the_lingering_advice_is_printed_once_when_lingering_is_off(w):
    w.sup.linger = False
    for name in ("moxi-demo-relay", "moxi-demo-other"):
        w.script(name)
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                     "'other': {'command': 'moxi-demo-other'}}")
    done = w.apply(units.plan("demo"))
    assert [c for c in w.sup.calls if c[0] == "loginctl"] == [
        ("loginctl", "show-user", "ana", "-p", "Linger")]
    assert "    loginctl enable-linger ana" in w.lines
    assert sum("    loginctl enable-linger ana" in a.lines for a in done) == 1
    assert not any(c[0] == "enable-linger" for c in w.sup.calls)


def test_no_lingering_read_when_lingering_is_on_or_no_daemon_is_new(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    assert not any("enable-linger" in line for line in w.lines)
    (w.root / "moxi-demo-relay.service").write_text("# moxi unit: demo/relay\nold\n")
    w.sup.calls.clear()
    w.sup.linger = False
    w.apply(units.plan("demo"))
    assert not any(c[0] == "loginctl" for c in w.sup.calls)


def test_a_changed_daemon_is_enabled_then_restarted(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    (w.root / "moxi-demo-relay.service").write_text("# moxi unit: demo/relay\nold\n")
    w.sup.calls.clear()
    [done] = w.apply(units.plan("demo"))
    assert done.ok and w.sup.calls == [
        ("daemon-reload",), ("enable", "moxi-demo-relay.service"),
        ("restart", "moxi-demo-relay.service")]


def test_upgrade_keep_enables_only_and_says_so(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'upgrade': 'keep'}}")
    w.apply(units.plan("demo"))
    (w.root / "moxi-demo-relay.service").write_text("# moxi unit: demo/relay\nold\n")
    w.sup.calls.clear()
    [done] = w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("enable", "moxi-demo-relay.service")]
    assert done.lines == ("moxi-demo-relay.service rewritten; the new code runs from its "
                          "next start",)


def test_restart_after_an_upgrade_restarts_only_running_restartable_daemons(w):
    for name in ("moxi-demo-relay", "moxi-demo-kept", "moxi-demo-idle"):
        w.script(name)
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                     "'kept': {'command': 'moxi-demo-kept', 'upgrade': 'keep'}, "
                     "'idle': {'command': 'moxi-demo-idle'}}")
    w.apply(units.plan("demo"))
    w.sup.units["moxi-demo-idle.service"]["active"] = False
    w.sup.calls.clear()
    w.apply(units.plan("demo"), restart=True)
    assert w.sup.calls == [
        ("show", "--property=Id,ActiveState", "moxi-demo-relay.service",
         "moxi-demo-idle.service"),
        ("restart", "moxi-demo-relay.service")]


def test_a_timer_is_enabled_on_its_timer_and_a_changed_one_restarted(w):
    w.script("moxi-demo-snap")
    w.module(TIMERS=TIMER)
    [change] = units.plan("demo")
    units._state.record("demo", "snap", change.digest)
    w.apply([change])
    assert w.sup.calls == [("daemon-reload",), ("enable", "--now", "moxi-demo-snap.timer")]
    timer = w.root / "moxi-demo-snap.timer"
    timer.write_text(timer.read_text().replace("5min", "6min"))
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("enable", "moxi-demo-snap.timer"),
                           ("restart", "moxi-demo-snap.timer")]
    assert not any("moxi-demo-snap.service" in c for c in w.sup.calls)


def test_removing_a_timer_disables_it_and_ends_a_run_then_deletes_its_files(w):
    w.script("moxi-demo-snap")
    w.module(TIMERS=TIMER)
    [change] = units.plan("demo")
    units._state.record("demo", "snap", change.digest)
    w.apply([change])
    w.module(TIMERS="{}")
    w.sup.calls.clear()
    [done] = w.apply(units.plan("demo"))
    assert done.ok and done.change.action == "remove"
    assert w.sup.calls == [("disable", "--now", "moxi-demo-snap.timer"),
                           ("stop", "moxi-demo-snap.service"), ("daemon-reload",)]
    assert list(w.root.glob("moxi-*")) == []


def test_a_remove_the_supervisor_refuses_leaves_the_files(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    w.module(DAEMONS="{}")
    w.sup.fail[("disable", "moxi-demo-relay.service")] = "Access denied"
    [done] = w.apply(units.plan("demo"))
    assert not done.ok
    assert done.lines == (
        "`systemctl --user disable --now -- moxi-demo-relay.service` failed: Access denied",
        "the unit is left in place; fix the cause and run `moxi install demo` again")
    assert (w.root / "moxi-demo-relay.service").exists()
    assert ("daemon-reload",) not in w.sup.calls[-1:]


def test_a_failed_reload_fails_every_written_unit(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.sup.fail[("daemon-reload", None)] = "Failed to reload daemon: bus error"
    [done] = w.apply(units.plan("demo"))
    assert not done.ok and "bus error" in done.lines[0]
    assert w.sup.calls == [("daemon-reload",)]


def shell(*addresses, more=""):
    """The `relay` daemon with a socket on `addresses`."""
    return (f"{{'relay': {{'command': 'moxi-demo-relay', 'socket': {{'setting': 'port', "
            f"'addresses': {list(addresses)!r}}}{more}}}}}")


SOCK, SOCK_1, SOCK_2 = (
    "moxi-demo-relay.socket", "moxi-demo-relay-1.socket", "moxi-demo-relay-2.socket")
SERVICE = "moxi-demo-relay.service"
RUNNING = ("show", "--property=Id,ActiveState", SERVICE)


@pytest.fixture
def v6(monkeypatch):
    """This box may have no IPv6 loopback; the plan is told it has one."""
    monkeypatch.setattr(units._plan, "ipv6_loopback", lambda: True)


def _service_verbs(calls) -> list[tuple[str, ...]]:
    return [c for c in calls if c[0] in ("enable", "disable") and SERVICE in c]


def test_a_new_daemon_enables_each_socket_in_unit_order_then_starts_its_service(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    plan = units.plan("demo")
    assert [(c.kind, c.action) for c in plan] == [("daemon", "add"), ("socket", "add")]
    done = w.apply(plan)
    assert all(a.ok for a in done)
    assert w.sup.calls[:4] == [("daemon-reload",), ("enable", "--now", SOCK),
                               ("enable", "--now", SOCK_1), ("start", SERVICE)]
    assert _service_verbs(w.sup.calls) == []
    assert "ListenStream=127.0.0.1:8470" in (w.root / SOCK).read_text()
    assert "ListenStream=[::1]:8470" in (w.root / SOCK_1).read_text()
    assert {(c.kind, c.action) for c in units.plan("demo")} == {("daemon", "keep"),
                                                                 ("socket", "keep")}


def test_a_changed_port_stops_the_service_restarts_each_socket_and_starts_it_again(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    from moxi import config

    config.set("demo", "port", "9000", by="test")
    plan = {(c.kind, c.action) for c in units.plan("demo")}
    assert plan == {("daemon", "keep"), ("socket", "change")}
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("stop", SERVICE),
                           ("enable", SOCK), ("restart", SOCK), ("enable", SOCK_1), ("restart", SOCK_1),
                           ("start", SERVICE)]


KEEP_SOCKET = shell("127.0.0.1", more=", 'upgrade': 'keep'")


@pytest.mark.parametrize("service_too", [False, True])
def test_a_changed_socket_of_a_running_keep_daemon_ends_with_it_running(w, service_too):
    """A changed socket restarts its daemon whatever `upgrade` says: the service is
    stopped for the socket's restart and started again, never left for the socket's next
    connection, whether or not the service's own text changed too."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=KEEP_SOCKET)
    w.apply(units.plan("demo"))
    assert w.sup.units[SERVICE]["active"]
    from moxi import config

    config.set("demo", "port", "9000", by="test")
    if service_too:
        service = w.root / SERVICE
        service.write_text(service.read_text() + "# an older text\n")
    plan = {(c.kind, c.action) for c in units.plan("demo")}
    assert plan == {("daemon", "change" if service_too else "keep"), ("socket", "change")}
    w.sup.calls.clear()
    done = w.apply(units.plan("demo"))
    assert all(a.ok for a in done)
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("stop", SERVICE), ("enable", SOCK),
                           ("restart", SOCK), ("start", SERVICE)]
    assert w.sup.units[SERVICE]["active"]
    if service_too:
        [line] = [line for a in done if a.change.kind == "daemon" for line in a.lines]
        assert line == f"{SERVICE} rewritten and restarted, since its socket changed"


@pytest.mark.parametrize("running", [True, False])
def test_a_keep_daemon_rewritten_under_an_unchanged_socket_is_left_as_it_is(w, running):
    """`apply` starts nothing for it and reads nothing: a running service keeps its old
    code, and one that is down stays down until its next connection."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=KEEP_SOCKET)
    w.apply(units.plan("demo"))
    w.sup.units[SERVICE]["active"] = running
    service = w.root / SERVICE
    service.write_text(service.read_text() + "# an older text\n")
    w.sup.calls.clear()
    done = w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",)]
    assert w.sup.units[SERVICE]["active"] is running
    [line] = [line for a in done for line in a.lines]
    assert line == f"{SERVICE} rewritten; the new code runs from its next start"


def test_a_restart_daemon_rewritten_under_an_unchanged_socket_is_restarted_alone(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1"))
    w.apply(units.plan("demo"))
    service = w.root / SERVICE
    service.write_text(service.read_text() + "# an older text\n")
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("restart", SERVICE)]


def test_a_third_address_writes_its_unit_and_changes_the_service_under_one_stop(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.module(DAEMONS=shell("127.0.0.1", "::1", "10.0.0.7"))
    plan = {c.kind: c for c in units.plan("demo")}
    assert (plan["daemon"].action, plan["socket"].action) == ("change", "change")
    assert [p.name for p in plan["socket"].paths] == [SOCK, SOCK_1, SOCK_2]
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("stop", SERVICE),
                           ("enable", "--now", SOCK_2), ("start", SOCK), ("start", SOCK_1),
                           ("start", SERVICE)]
    assert f"Sockets={SOCK} {SOCK_1} {SOCK_2}\n" in (w.root / SERVICE).read_text()


def test_an_address_taken_out_is_disabled_with_the_removes_and_deleted(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.module(DAEMONS=shell("127.0.0.1"))
    plan = {c.kind: c for c in units.plan("demo")}
    assert plan["socket"].dropped == (w.root / SOCK_1,)
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [("disable", "--now", SOCK_1), RUNNING, ("daemon-reload",),
                           ("stop", SERVICE), ("start", SOCK), ("start", SERVICE)]
    assert not (w.root / SOCK_1).exists() and (w.root / SOCK).exists()
    assert {(c.kind, c.action) for c in units.plan("demo")} == {("daemon", "keep"),
                                                                 ("socket", "keep")}


def test_a_dropped_unit_the_supervisor_refuses_keeps_its_file(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.module(DAEMONS=shell("127.0.0.1"))
    w.sup.fail[("disable", SOCK_1)] = "Access denied"
    done = {a.change.kind: a for a in w.apply(units.plan("demo"))}
    assert not done["socket"].ok
    assert done["socket"].lines[0] == (f"`systemctl --user disable --now -- {SOCK_1}` failed: "
                                       "Access denied")
    assert (w.root / SOCK_1).exists()
    assert units.plan("demo")[1].dropped == (w.root / SOCK_1,)


def test_a_service_that_gains_a_socket_loses_its_login_link(w):
    """The service's old text had an install section, so `enable` linked it to the
    login; it now has none, so `disable` (without `--now`) takes the link away, and the
    socket's stop and start do the rest. It is never enabled again."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    assert w.sup.units[SERVICE] == {"active": True, "enabled": True}
    w.module(DAEMONS=shell("127.0.0.1"))
    plan = {(c.kind, c.action) for c in units.plan("demo")}
    assert plan == {("daemon", "change"), ("socket", "add")}
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [("disable", SERVICE), RUNNING, ("daemon-reload",),
                           ("stop", SERVICE), ("enable", "--now", SOCK), ("start", SERVICE)]
    assert w.sup.units[SERVICE] == {"active": True, "enabled": False}
    w.sup.calls.clear()
    service = w.root / SERVICE
    service.write_text(service.read_text() + "# an older text\n")
    w.apply(units.plan("demo"))
    assert _service_verbs(w.sup.calls) == []


def test_a_daemon_that_loses_its_socket_removes_the_set_and_is_enabled_again(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.module(DAEMONS=DAEMON)
    plan = {(c.kind, c.action) for c in units.plan("demo")}
    assert plan == {("daemon", "change"), ("socket", "remove")}
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [("disable", "--now", SOCK), ("disable", "--now", SOCK_1),
                           ("daemon-reload",), ("enable", SERVICE), ("restart", SERVICE)]
    assert not (w.root / SOCK).exists() and not (w.root / SOCK_1).exists()
    assert "[Install]" in (w.root / SERVICE).read_text()


def test_after_a_home_move_a_changed_daemon_starts_its_sockets_so_resume_can(w, v6):
    """The move stops every socket unit and the service; the unit step then starts the
    changed service, after its sockets; `resume` then finds each running and is done.
    Without the sockets' start first, systemd refuses `resume`'s start of a socket unit
    under the running service, and the install exits 1."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    record: list = []
    stopped = units.stop("demo", ["relay"], record=record, runner=w.sup)
    assert stopped[0].ok and [e.unit for e in record] == [SOCK, SOCK_1, SERVICE]
    assert not any(w.sup.units[u]["active"] for u in (SOCK, SOCK_1, SERVICE))
    service = w.root / SERVICE
    service.write_text(service.read_text() + "# an older text\n")
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("start", SOCK), ("start", SOCK_1),
                           ("start", SERVICE)]
    resumed = units.resume(record, runner=w.sup)
    assert [(a.ok, a.lines) for a in resumed] == [
        (True, (f"{u} started",)) for u in (SOCK, SOCK_1, SERVICE)]
    # What resume would have met had the sockets not started first.
    w.sup.units[SOCK_1]["active"] = False
    assert "already active, refusing" in w.sup(["systemctl", "--user", "start", SOCK_1]).stderr


# The start rule, in each order of the home move's stop, the unit step's apply and resume.

NO_BUS = "Failed to connect to bus: No such file or directory"


def _moved(w):
    """The home move's stop of the relay: every socket unit, then the service."""
    record: list = []
    assert all(a.ok for a in units.stop("demo", ["relay"], record=record, runner=w.sup))
    assert record[-1].unit == SERVICE and all(e.unit.endswith(".socket")
                                              for e in record[:-1])
    assert not any(w.sup.units[e.unit]["active"] for e in record)
    return record


def _resumed(w, record):
    """`resume` of the record: every start ok, none refused."""
    resumed = units.resume(record, runner=w.sup)
    assert [a.ok for a in resumed] == [True] * len(record), [a.lines for a in resumed]
    return resumed


def _rewrite_service(w):
    service = w.root / SERVICE
    service.write_text(service.read_text() + "# an older text\n")


def test_after_a_home_move_a_keep_daemon_with_its_set_unchanged_is_left_to_resume(w, v6):
    """`apply` starts nothing for it and reads nothing; `resume` then starts the socket
    units while the service is still down, then the service."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1", more=", 'upgrade': 'keep'"))
    w.apply(units.plan("demo"))
    record = _moved(w)
    _rewrite_service(w)
    w.sup.calls.clear()
    done = w.apply(units.plan("demo"))
    assert all(a.ok for a in done)
    assert w.sup.calls == [("daemon-reload",)]
    assert not any(w.sup.units[u]["active"] for u in (SOCK, SOCK_1, SERVICE))
    w.sup.calls.clear()
    _resumed(w, record)
    assert w.sup.calls == [("start", SOCK), ("start", SOCK_1), ("start", SERVICE)]
    assert all(w.sup.units[u]["active"] for u in (SOCK, SOCK_1, SERVICE))


def test_without_a_move_an_idle_daemon_is_started_with_its_units_left_listening(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.sup.units[SERVICE]["active"] = False          # idle under its listening units
    ids = (w.sup.invocations[SOCK], w.sup.invocations[SOCK_1])
    _rewrite_service(w)
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("start", SOCK), ("start", SOCK_1),
                           ("start", SERVICE)]
    assert (w.sup.invocations[SOCK], w.sup.invocations[SOCK_1]) == ids
    assert w.sup.units[SERVICE]["active"]


def test_a_failed_read_after_a_move_stops_starts_the_units_then_the_service(w, v6):
    """The read fails, so the service is counted running: `stop` (which does nothing, the
    move stopped it), `start` of each socket unit, then `start`, never a `restart`; and
    `resume`'s starts of the same ids are no-ops the supervisor refuses none of."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    record = _moved(w)
    _rewrite_service(w)
    w.sup.fail[("show", None)] = NO_BUS
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("stop", SERVICE), ("start", SOCK),
                           ("start", SOCK_1), ("start", SERVICE)]
    assert not any(c[0] == "restart" for c in w.sup.calls)
    resumed = _resumed(w, record)
    assert [a.lines for a in resumed] == [(f"{u} started",) for u in (SOCK, SOCK_1, SERVICE)]


def test_a_failed_read_before_a_changed_set_starts_its_unchanged_unit_too(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1"))
    w.apply(units.plan("demo"))
    record = _moved(w)
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.sup.fail[("show", None)] = NO_BUS
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("stop", SERVICE),
                           ("enable", "--now", SOCK_1), ("start", SOCK), ("start", SERVICE)]
    _resumed(w, record)
    assert all(w.sup.units[u]["active"] for u in (SOCK, SOCK_1, SERVICE))


@pytest.mark.parametrize("read", ["running", "down", "failed"])
def test_restart_after_an_upgrade_meets_a_socket_daemon_by_the_start_rule(w, v6, read):
    """An unchanged `upgrade = "restart"` daemon: `restart` alone when the read finds it
    running, nothing when it finds it down, and under a failed read `stop`, its units'
    starts and `start`."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    if read == "down":
        w.sup.units[SERVICE]["active"] = False
    if read == "failed":
        w.sup.fail[("show", None)] = NO_BUS
    w.sup.calls.clear()
    done = w.apply(units.plan("demo"), restart=True)
    assert all(a.ok for a in done)
    assert w.sup.calls == {
        "running": [RUNNING, ("restart", SERVICE)],
        "down": [RUNNING],
        "failed": [RUNNING, ("stop", SERVICE), ("start", SOCK), ("start", SOCK_1),
                   ("start", SERVICE)]}[read]


def test_a_failed_read_leaves_a_keep_daemon_with_its_set_unchanged_alone(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1", more=", 'upgrade': 'keep'"))
    w.apply(units.plan("demo"))
    record = _moved(w)
    _rewrite_service(w)
    w.sup.fail[("show", None)] = NO_BUS
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo"), restart=True))
    assert w.sup.calls == [("daemon-reload",)]
    del w.sup.fail[("show", None)]
    w.sup.calls.clear()
    _resumed(w, record)
    assert w.sup.calls == [("start", SOCK), ("start", SOCK_1), ("start", SERVICE)]


def test_a_stop_that_fails_starts_nothing_more_of_that_daemon(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    from moxi import config

    config.set("demo", "port", "9000", by="test")
    w.sup.fail[("stop", SERVICE)] = "Access denied"
    w.sup.calls.clear()
    done = {a.change.kind: a for a in w.apply(units.plan("demo"))}
    assert not done["socket"].ok
    assert done["socket"].lines == (
        f"`systemctl --user stop -- {SERVICE}` failed: Access denied",
        "the unit files are written, and `moxi install demo` again finds them up to date; "
        f"fix the cause, then run `systemctl --user stop {SERVICE}`, "
        f"`systemctl --user restart {SOCK} {SOCK_1}` and `systemctl --user start {SERVICE}`")
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("stop", SERVICE),
                           ("enable", SOCK), ("enable", SOCK_1)]
    assert w.sup.units[SERVICE]["active"]


def test_a_unit_start_that_fails_is_a_failed_step_and_the_service_still_starts(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    _moved(w)
    _rewrite_service(w)
    w.sup.fail[("start", SOCK_1)] = "Address already in use"
    w.sup.calls.clear()
    done = {a.change.kind: a for a in w.apply(units.plan("demo"))}
    assert not done["daemon"].ok
    assert done["daemon"].lines[0] == (f"`systemctl --user start -- {SOCK_1}` failed: "
                                       "Address already in use")
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("start", SOCK), ("start", SOCK_1),
                           ("start", SERVICE)]
    assert w.sup.units[SOCK]["active"] and w.sup.units[SERVICE]["active"]


def test_a_socket_set_declared_disabled_is_written_and_never_started(w, tmp_path, v6):
    tool = _script(tmp_path / "tool")
    source = """
             def units():
                 return {{"daemons": {{"row": {{"argv": [{tool!r}], "enabled": {flag},
                     "socket": {{"setting": "port", "addresses": ["127.0.0.1", "::1"]}}}}}}}}
             """

    def declare(flag):
        w.site.write("moxi_demo/runtime.py",
                     textwrap.dedent(source.format(tool=str(tool), flag=flag)))
        __import__("sys").modules.pop("moxi_demo.runtime", None)

    row0, row1, row = "moxi-demo-row.socket", "moxi-demo-row-1.socket", "moxi-demo-row.service"
    declare(False)
    w.module(UNITS_FROM="'moxi_demo.runtime:units'")
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",)]
    declare(True)
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("show", "--property=Id,ActiveState", row), ("daemon-reload",),
                           ("enable", "--now", row0), ("enable", "--now", row1),
                           ("start", row)]
    declare(False)
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("disable", "--now", row0),
                           ("disable", "--now", row1), ("stop", row)]


def test_uninstall_takes_every_socket_unit_then_stops_the_static_service(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.sup.calls.clear()
    applied = units.remove("demo", runner=w.sup, say=w.lines.append)
    assert all(a.ok for a in applied) and len(applied) == 2
    assert w.sup.calls == [("disable", "--now", SOCK), ("disable", "--now", SOCK_1),
                           ("stop", SERVICE), ("daemon-reload",)]
    assert list(w.root.glob("moxi-*")) == []


def test_an_enabled_flip_disables_or_enables_now(w, tmp_path):
    tool = _script(tmp_path / "tool")
    source = """
             def units():
                 return {{"daemons": {{"row": {{"argv": [{tool!r}], "enabled": {flag}}}}}}}
             """

    def declare(flag):
        w.site.write("moxi_demo/runtime.py",
                     textwrap.dedent(source.format(tool=str(tool), flag=flag)))
        __import__("sys").modules.pop("moxi_demo.runtime", None)

    declare(False)
    w.module(UNITS_FROM="'moxi_demo.runtime:units'")
    w.apply(units.plan("demo"))
    assert not any(c[0] == "enable" for c in w.sup.calls)
    assert "# moxi: enabled = false" in (w.root / "moxi-demo-row.service").read_text()
    declare(True)
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("enable", "--now", "moxi-demo-row.service")]
    declare(False)
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("disable", "--now", "moxi-demo-row.service")]


def test_replace_moves_an_unmarked_unit_aside_and_restarts_it_as_written(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    old = w.root / "moxi-demo-relay.service"
    old.write_text("[Service]\nExecStart=/old/daemon\n")
    [change] = units.plan("demo")
    assert (change.action, change.cause) == ("refuse", "foreign")
    [done] = w.apply([change], replace=True)
    assert done.ok and old.read_text().startswith("# moxi unit: demo/relay\n")
    assert w.sup.calls == [("daemon-reload",), ("enable", "moxi-demo-relay.service"),
                           ("restart", "moxi-demo-relay.service")]
    [aside] = (units._apply._displaced("x", old).parent.parent).glob("*/*")
    assert aside.read_text() == "[Service]\nExecStart=/old/daemon\n"


def test_uninstall_removes_every_marked_unit_with_one_reload(w):
    w.script("moxi-demo-relay")
    w.script("moxi-demo-snap")
    w.module(DAEMONS=DAEMON, TIMERS=TIMER)
    for change in units.plan("demo"):
        if change.kind == "timer":
            units._state.record("demo", "snap", change.digest)
    w.apply(units.plan("demo"))
    w.sup.calls.clear()
    applied = units.remove("demo", runner=w.sup, say=w.lines.append)
    assert all(a.ok for a in applied) and len(applied) == 2
    assert w.sup.calls.count(("daemon-reload",)) == 1
    assert list(w.root.glob("moxi-*")) == []


# A socket daemon that starts at login: its service keeps its install section.

LOGIN = ", 'socket': {'setting': 'port', 'addresses': %r, 'start': 'login'}"


def login(*addresses, more=""):
    """The `relay` daemon with a socket on `addresses` that a login starts."""
    return ("{'relay': {'command': 'moxi-demo-relay'" + LOGIN % (list(addresses),)
            + more + "}}")


def test_a_login_socket_keeps_its_service_install_and_the_same_socket_units(unit_root):
    def render(start):
        return SystemdBackend().render(spec(
            module="ui", name="shell", doc="the shell",
            command=("/opt/ana/bin/moxi-ui-shell",),
            socket=Socket("port", ("127.0.0.1", "::1"), 8470, start=start)))

    (daemon, sockets), (plain, connection) = render("login"), render("connection")
    assert sockets.files == connection.files
    [(_, text)] = daemon.files
    assert text == plain.files[0][1] + "\n[Install]\nWantedBy=default.target\n"
    assert "\nSockets=moxi-ui-shell.socket moxi-ui-shell-1.socket\n" in text


def test_a_new_login_daemon_enables_its_sockets_then_its_service_now(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1", "::1"))
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls[:4] == [("daemon-reload",), ("enable", "--now", SOCK),
                               ("enable", "--now", SOCK_1), ("enable", "--now", SERVICE)]
    assert w.sup.units[SERVICE] == {"active": True, "enabled": True}
    [daemon] = [r for r in units.status(["demo"], runner=w.sup) if r.kind == "daemon"]
    assert daemon.enabled is True
    # Its own enabled is its service's, not its sockets'.
    w.sup.units[SERVICE]["enabled"] = False
    rows = {r.kind: r for r in units.status(["demo"], runner=w.sup)}
    assert (rows["daemon"].enabled, rows["socket"].enabled) == (False, True)


@pytest.mark.parametrize("running", [True, False])
def test_a_changed_login_daemon_is_enabled_where_the_rule_starts_it(w, v6, running):
    """Found running with its set unchanged: `enable` then `restart`; found down: its
    units' starts, then `enable --now`."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.sup.units[SERVICE]["active"] = running
    _rewrite_service(w)
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    tail = ([("enable", SERVICE), ("restart", SERVICE)] if running else
            [("start", SOCK), ("start", SOCK_1), ("enable", "--now", SERVICE)])
    assert w.sup.calls == [RUNNING, ("daemon-reload",), *tail]


def test_a_failed_read_under_login_stops_starts_the_units_then_enables_now(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    _rewrite_service(w)
    w.sup.fail[("show", None)] = NO_BUS
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("stop", SERVICE), ("start", SOCK),
                           ("start", SOCK_1), ("enable", "--now", SERVICE)]


def test_a_keep_login_daemon_with_its_set_unchanged_is_enabled_alone_and_keeps_its_link(
        w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1", more=", 'upgrade': 'keep'"))
    w.apply(units.plan("demo"))
    _rewrite_service(w)
    w.sup.calls.clear()
    done = w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("enable", SERVICE)]
    assert w.sup.units[SERVICE]["enabled"]
    [line] = [line for a in done for line in a.lines]
    assert line == f"{SERVICE} rewritten; the new code runs from its next start"


@pytest.mark.parametrize("upgrade", ["restart", "keep"])
def test_a_reinstall_from_connection_to_login_enables_the_service(w, upgrade):
    w.script("moxi-demo-relay")
    more = f", 'upgrade': '{upgrade}'"
    w.module(DAEMONS=shell("127.0.0.1", more=more))
    w.apply(units.plan("demo"))
    assert not w.sup.units[SERVICE]["enabled"]
    w.module(DAEMONS=login("127.0.0.1", more=more))
    assert {(c.kind, c.action) for c in units.plan("demo")} == {("daemon", "change"),
                                                                 ("socket", "keep")}
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    # A change of `start` alone is enabled and never restarted, whatever `upgrade` says.
    assert w.sup.calls == [("daemon-reload",), ("enable", SERVICE)]
    assert w.sup.units[SERVICE] == {"active": True, "enabled": True}


def test_a_reinstall_from_login_to_connection_unlinks_the_service_and_keeps_its_units(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1"))
    w.apply(units.plan("demo"))
    w.module(DAEMONS=shell("127.0.0.1"))
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.calls == [("disable", SERVICE), ("daemon-reload",)]
    assert w.sup.units[SERVICE] == {"active": True, "enabled": False}
    assert w.sup.units[SOCK]["enabled"]


@pytest.mark.parametrize("start", ["login", "connection"])
def test_a_plain_daemon_that_gains_a_socket_keeps_its_link_only_under_login(w, start):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    w.module(DAEMONS=(login if start == "login" else shell)("127.0.0.1"))
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo")))
    steps = [RUNNING, ("daemon-reload",), ("stop", SERVICE), ("enable", "--now", SOCK)]
    assert w.sup.calls == {
        "login": steps + [("enable", "--now", SERVICE)],
        "connection": [("disable", SERVICE), *steps, ("start", SERVICE)]}[start]
    assert w.sup.units[SERVICE] == {"active": True, "enabled": start == "login"}


# A socket daemon whose start follows a setting: a login-only change restarts nothing.

def switched(*addresses, more=""):
    """The `relay` daemon with a socket on `addresses` whose `start` follows the setting
    `relay.enabled`."""
    return ("{'relay': {'command': 'moxi-demo-relay', 'socket': {'setting': 'port', "
            f"'addresses': {list(addresses)!r}, 'start': {{'login': 'relay.enabled'}}}}"
            + more + "}}")


def flip(w, monkeypatch, value):
    """Set or unset `relay.enabled` as `moxi config set` does, applying the units through
    `apply_switch` with the fake supervisor; the `Applied` list."""
    from moxi import config

    monkeypatch.setattr(config, "apply_units", lambda *a, **k: [])
    if value is None:
        config.unset("demo", "relay.enabled", by="test")
    else:
        config.set("demo", "relay.enabled", value, by="test")
    w.sup.reload()
    w.sup.calls.clear()
    return units.apply_switch("demo", "relay.enabled", runner=w.sup, say=w.lines.append)


@pytest.mark.parametrize("upgrade", ["restart", "keep"])
def test_a_login_only_change_enables_or_disables_the_running_service_and_restarts_nothing(
        w, v6, monkeypatch, upgrade):
    """Turning the key on enables the running service, turning it off disables it without
    `--now`; no `stop`, `restart` or `disable --now` runs, whatever `upgrade` says."""
    w.script("moxi-demo-relay")
    w.module(ANNOUNCES="2", DAEMONS=switched("127.0.0.1", "::1",
                                             more=f", 'upgrade': '{upgrade}'"))
    assert all(a.ok for a in w.apply(units.plan("demo")))
    assert w.sup.units[SERVICE] == {"active": True, "enabled": False}
    done = flip(w, monkeypatch, True)
    assert all(a.ok for a in done)
    assert w.sup.calls == [("daemon-reload",), ("enable", SERVICE)]
    assert w.sup.units[SERVICE] == {"active": True, "enabled": True}
    assert "\n[Install]\nWantedBy=default.target\n" in (w.root / SERVICE).read_text()
    assert [line for a in done for line in a.lines] == [
        f"{SERVICE} rewritten: a login starts it from now on; a running daemon is left as "
        "it is"]
    done = flip(w, monkeypatch, None)
    assert all(a.ok for a in done)
    assert w.sup.calls == [("disable", SERVICE), ("daemon-reload",)]
    assert w.sup.units[SERVICE] == {"active": True, "enabled": False}
    assert "[Install]" not in (w.root / SERVICE).read_text()
    assert w.sup.units[SOCK]["enabled"] and w.sup.units[SOCK_1]["enabled"]


def test_a_login_only_change_to_a_stopped_service_enables_it_and_starts_nothing(
        w, v6, monkeypatch):
    w.script("moxi-demo-relay")
    w.module(ANNOUNCES="2", DAEMONS=switched("127.0.0.1"))
    w.apply(units.plan("demo"))
    w.sup.units[SERVICE]["active"] = False
    assert all(a.ok for a in flip(w, monkeypatch, True))
    assert w.sup.calls == [("daemon-reload",), ("enable", SERVICE)]
    assert w.sup.units[SERVICE] == {"active": False, "enabled": True}


def test_a_change_with_another_line_beside_the_login_one_restarts_as_before(
        w, v6, monkeypatch):
    """The key turned on together with a changed command: not login-only, so `upgrade =
    "restart"` restarts the running service as it does today."""
    w.script("moxi-demo-relay")
    w.module(ANNOUNCES="2", DAEMONS=switched("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.module(ANNOUNCES="2", DAEMONS=switched("127.0.0.1", "::1", more=", 'args': ['-v']"))
    done = flip(w, monkeypatch, True)
    assert all(a.ok for a in done)
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("enable", SERVICE),
                           ("restart", SERVICE)]


def test_restart_after_an_upgrade_still_restarts_a_running_daemon_with_a_login_only_change(
        w, v6, monkeypatch):
    w.script("moxi-demo-relay")
    w.module(ANNOUNCES="2", DAEMONS=switched("127.0.0.1"))
    w.apply(units.plan("demo"))
    from moxi import config

    monkeypatch.setattr(config, "apply_units", lambda *a, **k: [])
    config.set("demo", "relay.enabled", True, by="test")
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo"), restart=True))
    assert w.sup.calls == [RUNNING, ("daemon-reload",), ("enable", SERVICE),
                           ("restart", SERVICE)]


def test_restart_after_an_upgrade_enables_then_restarts_a_running_login_daemon(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1"))
    w.apply(units.plan("demo"))
    w.sup.calls.clear()
    assert all(a.ok for a in w.apply(units.plan("demo"), restart=True))
    assert w.sup.calls == [RUNNING, ("enable", SERVICE), ("restart", SERVICE)]


def test_a_failed_stop_of_a_login_daemon_names_the_enable_left_undone(w, v6):
    from moxi import config

    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1"))
    w.apply(units.plan("demo"))
    config.set("demo", "port", "9000", by="test")
    w.sup.fail[("stop", SERVICE)] = "Access denied"
    w.sup.calls.clear()
    done = {a.change.kind: a for a in w.apply(units.plan("demo"))}
    assert done["socket"].lines[1] == (
        "the unit files are written, and `moxi install demo` again finds them up to date; "
        f"fix the cause, then run `systemctl --user stop {SERVICE}`, "
        f"`systemctl --user restart {SOCK}` and `systemctl --user enable --now {SERVICE}`; "
        f"{SERVICE} was not enabled, so the login link its text asks for is not made "
        "until that `enable` runs")
    assert not any(c[:1] == ("enable",) and SERVICE in c for c in w.sup.calls)


def test_a_login_daemon_declared_disabled_is_disabled_now_after_its_units(w, tmp_path, v6):
    tool = _script(tmp_path / "tool")
    source = """
             def units():
                 return {{"daemons": {{"row": {{"argv": [{tool!r}], "enabled": {flag},
                     "socket": {{"setting": "port", "addresses": ["127.0.0.1", "::1"],
                                 "start": "login"}}}}}}}}
             """

    def declare(flag):
        w.site.write("moxi_demo/runtime.py",
                     textwrap.dedent(source.format(tool=str(tool), flag=flag)))
        __import__("sys").modules.pop("moxi_demo.runtime", None)

    row0, row1, row = "moxi-demo-row.socket", "moxi-demo-row-1.socket", "moxi-demo-row.service"
    declare(True)
    w.module(UNITS_FROM="'moxi_demo.runtime:units'")
    w.apply(units.plan("demo"))
    assert w.sup.units[row] == {"active": True, "enabled": True}
    declare(False)
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("disable", "--now", row0),
                           ("disable", "--now", row1), ("disable", "--now", row)]
    assert w.sup.units[row] == {"active": False, "enabled": False}


def test_uninstall_of_a_login_daemon_disables_its_units_then_its_service(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.sup.calls.clear()
    assert all(a.ok for a in units.remove("demo", runner=w.sup, say=w.lines.append))
    assert w.sup.calls == [("disable", "--now", SOCK), ("disable", "--now", SOCK_1),
                           ("disable", "--now", SERVICE), ("daemon-reload",)]


@pytest.mark.parametrize("act, calls", [
    ("enable", [("enable", "--now", SOCK), ("enable", "--now", SERVICE)]),
    ("disable", [("disable", "--now", SOCK), ("disable", "--now", SERVICE)]),
    ("restart", [("restart", SERVICE)]),
])
def test_a_login_daemons_acts_reach_its_service_after_its_units(w, act, calls):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=login("127.0.0.1"))
    w.apply(units.plan("demo"))
    w.sup.calls.clear()
    [acted] = getattr(units, act)("demo", ["relay"], runner=w.sup)
    assert acted.ok and w.sup.calls == calls


# -- acts ---------------------------------------------------------------------------------------

class Recorder:
    """A runner that records each command after `systemctl --user`, without the `--` before
    the units, and answers from a
    table of (verb, unit) to (code, stdout, stderr)."""

    def __init__(self, answers=None, show=""):
        self.calls: list[tuple[str, ...]] = []
        self.answers = answers or {}
        self.show = show

    def __call__(self, cmd, *, env=None, timeout=None):
        args = (tuple(a for a in cmd[2:] if a != "--") if cmd[0] == "systemctl"
                else tuple(cmd))
        self.calls.append(args)
        if args[0] == "show":
            show = self.show(args) if callable(self.show) else self.show
            return subprocess.CompletedProcess(cmd, 0, show, "")
        code, out, err = self.answers.get((args[0], args[-1]), (0, "", ""))
        return subprocess.CompletedProcess(cmd, code, out, err)


def _marked_daemon(root: Path, socket: bool = False) -> UnitRef:
    (root / "moxi-demo-relay.service").write_text("# moxi unit: demo/relay\n")
    if socket:
        (root / "moxi-demo-relay.socket").write_text("# moxi unit: demo/relay\n")
    return UnitRef("demo", "relay", "daemon", ("moxi-demo-relay.service",))


@pytest.mark.parametrize("act, calls", [
    ("start", [("start", "moxi-demo-relay.socket"), ("start", "moxi-demo-relay.service")]),
    ("stop", [("stop", "moxi-demo-relay.socket"), ("stop", "moxi-demo-relay.service")]),
    ("enable", [("enable", "--now", "moxi-demo-relay.socket"),
                ("start", "moxi-demo-relay.service")]),
    ("disable", [("disable", "--now", "moxi-demo-relay.socket"),
                 ("stop", "moxi-demo-relay.service")]),
    ("restart", [("restart", "moxi-demo-relay.service")]),
])
def test_a_daemon_with_a_socket_is_acted_on_socket_first(unit_root, act, calls):
    run = Recorder()
    acted = SystemdBackend().act(act, _marked_daemon(unit_root, socket=True),
                                 Fence("systemd", run))
    assert acted.ok and (acted.module, acted.name, acted.act) == ("demo", "relay", act)
    assert run.calls == calls


def test_a_socket_marked_for_another_unit_is_not_taken_with_the_daemon(unit_root):
    unit = _marked_daemon(unit_root)
    (unit_root / "moxi-demo-relay.socket").write_text("[Socket]\n")
    run = Recorder()
    SystemdBackend().act("stop", unit, Fence("systemd", run))
    assert run.calls == [("stop", "moxi-demo-relay.service")]


def _socket_set(root: Path, places=(0, 1, 2)) -> UnitRef:
    """A marked daemon with a socket unit at each place, written as the backend writes
    them, plus files that are not of its set."""
    _, sockets = SystemdBackend(root).render(spec(
        module="demo", name="relay", socket=Socket("port", ("127.0.0.1", "::1", "10.0.0.7"),
                                                   8470)))
    (root / SERVICE).write_text("# moxi unit: demo/relay\n[Service]\n")
    for place, (path, text) in enumerate(sockets.files):
        if place in places:
            path.write_text(text)
    (root / "moxi-demo-relay-01.socket").write_text("# moxi unit: demo/relay\n")
    (root / "moxi-demo-relay-3.socket").write_text("[Socket]\n")
    (root / "moxi-demo-relay-4.socket").write_text("# moxi unit: demo/other\n")
    return UnitRef("demo", "relay", "daemon", (SERVICE,), (root / SERVICE,))


@pytest.mark.parametrize("act, calls", [
    ("start", [("start", SOCK), ("start", SOCK_1), ("start", SOCK_2), ("start", SERVICE)]),
    ("stop", [("stop", SOCK), ("stop", SOCK_1), ("stop", SOCK_2), ("stop", SERVICE)]),
    ("enable", [("enable", "--now", SOCK), ("enable", "--now", SOCK_1), ("enable", "--now", SOCK_2),
                ("start", SERVICE)]),
    ("disable", [("disable", "--now", SOCK), ("disable", "--now", SOCK_1),
                 ("disable", "--now", SOCK_2), ("stop", SERVICE)]),
    ("restart", [("restart", SERVICE)]),
])
def test_every_socket_unit_of_the_set_goes_first_in_unit_order(unit_root, act, calls):
    run = Recorder()
    acted = SystemdBackend().act(act, _socket_set(unit_root), Fence("systemd", run))
    assert acted.ok and run.calls == calls


def test_the_marked_set_is_the_files_at_its_socket_names_with_its_mark(unit_root):
    _socket_set(unit_root, places=(0, 2))
    refs = {(r.name, r.kind): r for r in SystemdBackend().marked("demo")}
    sockets = refs[("relay", "socket")]
    assert sockets.ids == (SOCK, SOCK_2)
    assert sockets.paths == (unit_root / SOCK, unit_root / SOCK_2)
    assert set(refs) == {("relay", "daemon"), ("relay", "socket")}


def test_a_set_whose_first_unit_is_gone_is_still_read_in_unit_order(unit_root):
    _socket_set(unit_root, places=(2, 1))
    [ref] = [r for r in SystemdBackend().marked("demo") if r.kind == "socket"]
    assert ref.ids == (SOCK_1, SOCK_2)


def test_ids_and_files_take_each_numbered_socket_unit_that_is_there(unit_root):
    _socket_set(unit_root, places=(0, 2))
    backend = SystemdBackend()
    assert backend.ids("demo", "relay", "daemon") == (SOCK, SOCK_2, "moxi-demo-relay-3.socket",
                                                      "moxi-demo-relay-4.socket", SERVICE)
    assert backend.files("demo", "relay", "daemon") == tuple(
        unit_root / u for u in (SERVICE, SOCK, SOCK_2, "moxi-demo-relay-3.socket",
                                "moxi-demo-relay-4.socket"))
    assert backend.ids("demo", "none", "daemon") == ("moxi-demo-none.socket",
                                                     "moxi-demo-none.service")


def test_the_stop_record_lists_every_socket_unit_first_in_unit_order(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1", "10.0.0.7"))
    w.apply(units.plan("demo"))
    record: list = []
    w.sup.calls.clear()
    [acted] = units.stop("demo", ["relay"], record=record, runner=w.sup)
    assert acted.ok and [(e.unit, e.marked) for e in record] == [
        (SOCK, True), (SOCK_1, True), (SOCK_2, True), (SERVICE, True)]
    assert w.sup.calls == [("show", "--property=Id,ActiveState", SOCK, SOCK_1, SOCK_2, SERVICE),
                           ("stop", SOCK), ("stop", SOCK_1), ("stop", SOCK_2), ("stop", SERVICE)]


@pytest.mark.parametrize("act, calls", [
    ("start", [("start", "moxi-demo-snap.service")]),
    ("stop", [("stop", "moxi-demo-snap.service")]),
    ("restart", [("stop", "moxi-demo-snap.service"), ("start", "moxi-demo-snap.service")]),
    ("enable", [("enable", "--now", "moxi-demo-snap.timer")]),
    ("disable", [("disable", "--now", "moxi-demo-snap.timer")]),
])
def test_a_timer_runs_through_its_service_and_is_enabled_on_its_timer(unit_root, act, calls):
    run = Recorder()
    unit = UnitRef("demo", "snap", "timer", ("moxi-demo-snap.timer", "moxi-demo-snap.service"))
    assert SystemdBackend().act(act, unit, Fence("systemd", run)).ok
    assert run.calls == calls


def test_a_foreign_timer_acts_on_the_unit_its_triggers_names(unit_root):
    run = Recorder(show="Id=backup.timer\nTriggers=backup-run.service\n")
    backend = SystemdBackend()
    unit = backend.foreign("backup.timer", Fence("systemd", run))
    assert unit == UnitRef(None, "backup.timer", "timer", ("backup.timer",
                                                          "backup-run.service"))
    for act in ("start", "stop", "restart"):
        assert backend.act(act, unit, Fence("systemd", run)).ok
    assert run.calls[1:] == [("start", "backup-run.service"), ("stop", "backup-run.service"),
                             ("stop", "backup-run.service"), ("start", "backup-run.service")]
    assert not any("backup.service" in c for c in run.calls)


def test_a_foreign_timer_with_no_fence_is_its_timer_alone(unit_root):
    assert SystemdBackend().foreign("backup.timer", None) == UnitRef(
        None, "backup.timer", "timer", ("backup.timer",))
    assert SystemdBackend().foreign("backup.service", None).ids == ("backup.service",)


def test_ids_are_the_writers_whether_or_not_a_file_is_there(unit_root):
    backend = SystemdBackend()
    assert backend.ids("comms", "daemon", "daemon") == ("moxi-comms-daemon.socket",
                                                        "moxi-comms-daemon.service")
    assert backend.ids("notes", "snap", "timer") == ("moxi-notes-snap.timer",
                                                     "moxi-notes-snap.service")


def test_a_failed_running_read_raises_and_is_never_nothing_runs(unit_root):
    def fail(cmd, *, env=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus\n")

    with pytest.raises(ReadError) as caught:
        SystemdBackend().running(["a.service"], Fence("systemd", fail))
    assert "Failed to connect to bus" in caught.value.ran.stderr


def test_restart_after_an_upgrade_fails_its_daemons_when_the_read_fails(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    real = w.sup.__call__

    def broken(cmd, **kw):
        if cmd[2:3] == ["show"]:
            return subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus\n")
        return real(cmd, **kw)

    w.sup.calls.clear()
    [done] = units.apply(units.plan("demo"), restart=True, runner=broken,
                         say=w.lines.append)
    assert not done.ok and "Failed to connect to bus" in done.lines[0]
    assert not any(c[0] == "restart" for c in w.sup.calls)


def test_a_foreign_timer_with_no_triggers_cannot_be_started(unit_root):
    run = Recorder(show="Id=lone.timer\nTriggers=\n")
    backend = SystemdBackend()
    unit = backend.foreign("lone.timer", Fence("systemd", run))
    acted = backend.act("start", unit, Fence("systemd", run))
    assert not acted.ok and acted.lines == ("lone.timer triggers no unit",)


def test_a_foreign_service_is_its_own_id_and_has_no_socket_taken(unit_root):
    (unit_root / "backup.socket").write_text("")
    run = Recorder()
    backend = SystemdBackend()
    unit = backend.foreign("backup.service", Fence("systemd", run))
    assert unit.ids == ("backup.service",) and run.calls == []
    backend.act("stop", unit, Fence("systemd", run))
    assert run.calls == [("stop", "backup.service")]


def test_stopping_a_unit_that_is_not_loaded_is_done_and_other_failures_are_not(unit_root):
    unit = _marked_daemon(unit_root)
    run = Recorder({("stop", "moxi-demo-relay.service"):
                    (5, "", "Failed to stop moxi-demo-relay.service: Unit "
                            "moxi-demo-relay.service not loaded.\n")})
    assert SystemdBackend().act("stop", unit, Fence("systemd", run)).ok
    run = Recorder({("start", "moxi-demo-relay.service"): (1, "", "Job failed.\n")})
    acted = SystemdBackend().act("start", unit, Fence("systemd", run))
    assert not acted.ok
    assert acted.lines == ("`systemctl --user start -- moxi-demo-relay.service` failed: "
                           "Job failed.",)


def test_own_names_are_moxis_namespace(unit_root):
    backend = SystemdBackend()
    assert backend.own("moxi-comms-daemon.service") and backend.own("moxi-x.timer")
    assert not backend.own("backup.timer") and not backend.own("moxitest-x.timer")


def test_running_reads_active_activating_and_reloading_in_one_call(unit_root):
    show = ("Id=a.service\nActiveState=active\n\nId=b.service\nActiveState=activating\n\n"
            "Id=c.socket\nActiveState=reloading\n\nId=d.service\nActiveState=inactive\n\n"
            "Id=e.service\nActiveState=failed\n")
    run = Recorder(show=show)
    ids = ["a.service", "b.service", "c.socket", "d.service", "e.service"]
    assert SystemdBackend().running(ids, Fence("systemd", run)) == {
        "a.service", "b.service", "c.socket"}
    assert run.calls == [("show", "--property=Id,ActiveState", *ids)]
    assert SystemdBackend().running([], Fence("systemd", run)) == frozenset()


def test_stop_id_and_start_id_use_only_stop_and_start(unit_root):
    run = Recorder()
    backend = SystemdBackend()
    assert backend.stop_id("moxi-a-b.socket", Fence("systemd", run)).ok
    assert backend.start_id("moxi-a-b.socket", Fence("systemd", run)).ok
    assert run.calls == [("stop", "moxi-a-b.socket"), ("start", "moxi-a-b.socket")]


# -- status -------------------------------------------------------------------------------------

def _block(**fields) -> str:
    return "".join(f"{k}={v}\n" for k, v in fields.items())


def _shown(*blocks) -> str:
    return "\n".join(blocks)


DAEMON_ACTIVE = _block(
    Id="moxi-demo-relay.service", LoadState="loaded", ActiveState="active",
    SubState="running", MainPID="4242", ActiveEnterTimestampMonotonic="{mono}",
    Result="success", ExecMainStatus="0", NRestarts="2",
    ControlGroup="/user.slice/x", UnitFileState="enabled", Triggers="", DropInPaths="")


def test_status_reads_a_daemon_from_recorded_show_output(unit_root):
    mono = int((time.monotonic() - 30) * 1e6)
    run = Recorder(show=DAEMON_ACTIVE.format(mono=mono))
    unit = UnitRef("demo", "relay", "daemon", ("moxi-demo-relay.service",))
    [state] = SystemdBackend().status([unit], Fence("systemd", run))
    assert run.calls == [("show", f"--property={_systemd.STATUS_PROPERTIES}",
                          "moxi-demo-relay.service")]
    assert (state.state, state.pid, state.enabled) == ("active", 4242, True)
    assert abs(state.since - (time.time() - 30)) < 2
    assert state.detail == "active (running), enabled, restarted 2 times"
    assert state.log == "journalctl --user -u moxi-demo-relay.service"
    assert state.backend == "systemd" and state.wrapped is None and state.last is None
    assert state.triggers is None


@pytest.mark.parametrize("active, raw, state, enabled", [
    ("active", "enabled", "active", True),
    ("activating", "disabled", "active", False),
    ("reloading", "static", "active", None),
    ("failed", "enabled", "failed", True),
    ("inactive", "indirect", "inactive", None),
    ("deactivating", "generated", "inactive", None),
])
def test_status_maps_active_state_and_unit_file_state(unit_root, active, raw, state,
                                                      enabled):
    run = Recorder(show=_block(Id="moxi-a-b.service", LoadState="loaded",
                               ActiveState=active, SubState="x", MainPID="0",
                               UnitFileState=raw))
    [read] = SystemdBackend().status([UnitRef("a", "b", "daemon", ("moxi-a-b.service",))],
                                     Fence("systemd", run))
    assert (read.state, read.enabled) == (state, enabled)
    assert raw in read.detail and read.pid is None
    assert (read.since is None) or state == "active"


def test_status_of_a_missing_unit(unit_root):
    run = Recorder(show=_block(Id="moxi-a-b.service", LoadState="not-found",
                               ActiveState="inactive", UnitFileState=""))
    [read] = SystemdBackend().status([UnitRef("a", "b", "daemon", ("moxi-a-b.service",))],
                                     Fence("systemd", run))
    assert (read.state, read.enabled, read.log, read.detail) == ("missing", None, None,
                                                                 "not-found")


def test_status_of_a_marked_timer_reads_its_service_result(unit_root):
    show = _shown(
        _block(Id="moxi-a-snap.service", LoadState="loaded", ActiveState="inactive",
               SubState="dead", MainPID="0", Result="exit-code", ExecMainStatus="3",
               UnitFileState="static", Triggers=""),
        _block(Id="moxi-a-snap.timer", LoadState="loaded", ActiveState="active",
               SubState="waiting", UnitFileState="enabled",
               Triggers="moxi-a-snap.service", ActiveEnterTimestampMonotonic="0"))
    run = Recorder(show=show)
    unit = UnitRef("a", "snap", "timer", ("moxi-a-snap.timer", "moxi-a-snap.service"))
    [read] = SystemdBackend().status([unit], Fence("systemd", run))
    assert (read.state, read.enabled) == ("active", True)
    assert read.last == {"result": "exit-code", "status": "3"}
    assert read.log == "journalctl --user -u moxi-a-snap.service"
    assert read.triggers == "moxi-a-snap.service"
    assert len(run.calls) == 1


def test_status_of_a_foreign_timer_reads_the_unit_its_triggers_names(unit_root):
    def show(args):
        if "backup.timer" in args:
            return _block(Id="backup.timer", LoadState="loaded", ActiveState="active",
                          SubState="waiting", UnitFileState="enabled",
                          Triggers="backup-run.service")
        assert args[2:] == ("backup-run.service",)
        return _block(Id="backup-run.service", LoadState="loaded", ActiveState="inactive",
                      SubState="dead", MainPID="0", Result="success", ExecMainStatus="0",
                      UnitFileState="static", DropInPaths="")

    run = Recorder(show=show)
    [read] = SystemdBackend().status([UnitRef(None, "backup.timer", "timer",
                                              ("backup.timer",))], Fence("systemd", run))
    assert read.module is None and read.last == {"result": "success", "status": "0"}
    assert read.log == "journalctl --user -u backup-run.service"
    assert read.triggers == "backup-run.service"
    assert len(run.calls) == 2 and not any("backup.service" in c for c in run.calls)


def test_a_timer_the_manager_does_not_hold_or_that_names_nothing_has_no_triggers(unit_root):
    """`not-found` shows an empty `Triggers`, and so does a masked timer; neither names a
    unit. A socket's `Triggers` names its service, but only a timer's is carried."""
    show = _shown(
        _block(Id="gone.timer", LoadState="not-found", ActiveState="inactive", Triggers=""),
        _block(Id="masked.timer", LoadState="masked", ActiveState="inactive",
               SubState="dead", UnitFileState="masked", Triggers=""),
        _socket_block(SOCK))
    run = Recorder(show=show)
    gone, masked, socket = SystemdBackend().status(
        [UnitRef(None, "gone.timer", "timer", ("gone.timer",)),
         UnitRef(None, "masked.timer", "timer", ("masked.timer",)),
         UnitRef("demo", "relay", "socket", (SOCK,))], Fence("systemd", run))
    assert (gone.state, gone.triggers) == ("missing", None)
    assert (masked.state, masked.triggers) == ("inactive", None)
    assert (socket.state, socket.triggers) == ("active", None)
    assert len(run.calls) == 1


def test_a_foreign_socket_read_as_a_daemon_carries_no_triggers(unit_root):
    """A foreign `.socket` ref is read as a daemon, and its block's `Triggers` names the
    service it starts. Only a timer's `Triggers` is carried, so a unit a `.socket` starts
    is never taken for one a timer runs."""
    show = _shown(_block(Id="watch.socket", LoadState="loaded", ActiveState="active",
                         SubState="listening", UnitFileState="enabled",
                         Triggers="watch-run.service"))
    run = Recorder(show=show)
    backend = SystemdBackend()
    ref = backend.foreign("watch.socket", None)
    [read] = backend.status([ref], Fence("systemd", run))
    assert (ref.kind, read.state) == ("daemon", "active")
    assert read.triggers is None


def test_status_reads_wrapped_from_the_drop_in_paths(unit_root):
    dropin = unit_root / "backup-run.service.d" / "50-moxi-services.conf"
    dropin.parent.mkdir()
    dropin.write_text("# moxi wrap: services/backup\n"
                      "# moxi: timer = backup.timer, if = services.rows.backup\n[Service]\n")
    show = _shown(
        _block(Id="backup.timer", LoadState="loaded", ActiveState="active",
               UnitFileState="enabled", Triggers="backup-run.service"),
        _block(Id="backup-run.service", LoadState="loaded", ActiveState="inactive",
               Result="success", ExecMainStatus="0", UnitFileState="static",
               DropInPaths=f"/usr/lib/systemd/user/service.d/10-timeout-abort.conf {dropin}"))
    run = Recorder(show=show)
    unit = UnitRef(None, "backup.timer", "timer", ("backup.timer", "backup-run.service"))
    [read] = SystemdBackend().status([unit], Fence("systemd", run))
    assert read.wrapped == Wrapped("services", "rows.backup", "backup")
    assert len(run.calls) == 1


def _socket_block(unit, active="active", file="enabled", invocation="", mono="0"):
    return _block(Id=unit, LoadState="loaded", ActiveState=active, SubState="listening",
                  MainPID="0", ActiveEnterTimestampMonotonic=mono, Result="success",
                  ExecMainStatus="0", NRestarts="0", ControlGroup="", UnitFileState=file,
                  Triggers=SERVICE, DropInPaths="", InvocationID=invocation)


SOCKETS = UnitRef("demo", "relay", "socket", (SOCK, SOCK_1))


def test_the_status_read_asks_for_each_units_invocation_id(unit_root):
    assert "InvocationID" in _systemd.STATUS_PROPERTIES.split(",")
    run = Recorder(show=_socket_block(SOCK))
    SystemdBackend().status([UnitRef("demo", "relay", "socket", (SOCK,))],
                            Fence("systemd", run))
    assert run.calls == [("show", f"--property={_systemd.STATUS_PROPERTIES}", SOCK)]


def test_a_set_whose_units_are_all_active_reads_active_with_its_epoch(unit_root):
    old, new = int((time.monotonic() - 60) * 1e6), int((time.monotonic() - 10) * 1e6)
    run = Recorder(show=_shown(_socket_block(SOCK, invocation="3f0c", mono=str(old)),
                               _socket_block(SOCK_1, invocation="9a41", mono=str(new))))
    [state] = SystemdBackend().status([SOCKETS], Fence("systemd", run))
    assert (state.kind, state.state, state.pid, state.enabled) == ("socket", "active",
                                                                    None, True)
    assert state.epoch == f"{SOCK}=3f0c,{SOCK_1}=9a41"
    assert state.triggers is None  # its units' Triggers name the service; only a timer's counts
    assert abs(state.since - (time.time() - 10)) < 2
    assert state.log == f"journalctl --user -u {SOCK} -u {SOCK_1}"


def test_a_failed_unit_fails_the_set_naming_it_with_no_epoch(unit_root):
    run = Recorder(show=_shown(_socket_block(SOCK, invocation="3f0c"),
                               _socket_block(SOCK_1, active="failed", invocation="9a41")))
    [state] = SystemdBackend().status([SOCKETS], Fence("systemd", run))
    assert (state.state, state.detail, state.epoch) == ("failed", f"failed: {SOCK_1}", None)


def test_a_stopped_unit_that_still_shows_its_last_id_gives_no_epoch(unit_root):
    run = Recorder(show=_shown(_socket_block(SOCK, invocation="3f0c"),
                               _socket_block(SOCK_1, active="inactive", file="disabled",
                                             invocation="9a41")))
    [state] = SystemdBackend().status([SOCKETS], Fence("systemd", run))
    assert (state.state, state.detail, state.epoch, state.enabled) == (
        "inactive", f"not active: {SOCK_1}", None, False)


def test_a_socket_unit_the_manager_does_not_hold_reads_missing_in_the_set(unit_root):
    run = Recorder(show=_shown(_socket_block(SOCK, invocation="3f0c"),
                               _block(Id=SOCK_1, LoadState="not-found", ActiveState="inactive")))
    [state] = SystemdBackend().status([SOCKETS], Fence("systemd", run))
    assert (state.state, state.detail, state.epoch) == ("inactive", f"not active: {SOCK_1}",
                                                        None)


def test_a_failed_read_makes_the_socket_unknown(unit_root):
    def fail(cmd, *, env=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus\n")

    [state] = SystemdBackend().status([SOCKETS], Fence("systemd", fail))
    assert state.state == "unknown" and "Failed to connect to bus" in state.detail


def test_status_gives_the_daemon_its_sockets_enabled_and_an_idle_service_inactive(w, v6):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.sup.units[SERVICE]["active"] = False
    daemon, socket = units.status(["demo"], runner=w.sup)
    assert (daemon.kind, daemon.state, daemon.enabled) == ("daemon", "inactive", True)
    assert "static" in daemon.detail
    assert (socket.kind, socket.state, socket.enabled) == ("socket", "active", True)
    assert socket.epoch == ",".join(f"{u}={w.sup.invocations[u]}" for u in (SOCK, SOCK_1))


def test_status_gives_each_timer_the_unit_it_triggers_and_no_other_kind_one(w, v6):
    """With the fake runner: a marked timer, a foreign timer, a daemon and its socket.
    The field comes out of the reads status makes anyway, one `show` and one more for
    the foreign timer's service."""
    w.script("moxi-demo-relay")
    w.script("moxi-demo-snap")
    w.module(DAEMONS=shell("127.0.0.1"), TIMERS=TIMER)
    changes = units.plan("demo")
    for change in changes:
        if change.kind == "timer":
            units._state.record("demo", change.name, change.digest)
    w.apply(changes)
    w.sup.extra |= {"backup.timer", "backup-run.service"}
    w.sup.triggers["backup.timer"] = "backup-run.service"
    w.sup.reload()
    w.sup.calls.clear()
    read = {(s.name, s.kind): s for s in units.status(["demo"], foreign=["backup.timer"],
                                                      runner=w.sup)}
    assert read[("snap", "timer")].triggers == "moxi-demo-snap.service"
    assert read[("backup.timer", "timer")].triggers == "backup-run.service"
    assert read[("relay", "daemon")].triggers is None
    assert read[("relay", "socket")].triggers is None
    shows = [c for c in w.sup.calls if c[0] == "show"]
    assert len(shows) == 2 and shows[1][2:] == ("backup-run.service",)


def test_a_failed_show_reads_unknown_with_the_supervisors_text(unit_root):
    def fail(cmd, *, env=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus\n")

    [read] = SystemdBackend().status([UnitRef("a", "b", "daemon", ("moxi-a-b.service",))],
                                     Fence("systemd", fail))
    assert read.state == "unknown" and "Failed to connect to bus" in read.detail
    assert read.enabled is None


def test_a_failed_show_makes_every_unit_it_covers_unknown(unit_root):
    """A daemon, its socket, a marked timer and a foreign timer: one call covers them all,
    so all are `unknown`, and no second call is made for the foreign timer's service."""
    calls = []

    def fail(cmd, *, env=None, timeout=None):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus\n")

    units_ = [UnitRef("a", "b", "daemon", ("moxi-a-b.service",)),
              UnitRef("a", "b", "socket", ("moxi-a-b.socket",)),
              UnitRef("a", "snap", "timer", ("moxi-a-snap.timer", "moxi-a-snap.service")),
              UnitRef(None, "backup.timer", "timer", ("backup.timer",))]
    states = SystemdBackend().status(units_, Fence("systemd", fail))
    assert [s.state for s in states] == ["unknown"] * 4 and len(calls) == 1
    assert all("Failed to connect to bus" in s.detail and s.enabled is None
               and s.last is None and s.triggers is None for s in states)


@pytest.mark.parametrize("service_state", ["failed", "inactive", "activating"])
def test_an_armed_timer_whose_service_failed_reads_active(unit_root, service_state):
    """The `.timer` decides: armed is `active` whatever its service's last run did; the
    run's result goes in `last`."""
    show = _shown(
        _block(Id="moxi-a-snap.service", LoadState="loaded", ActiveState=service_state,
               SubState="x", MainPID="0", Result="exit-code", ExecMainStatus="1",
               UnitFileState="static"),
        _block(Id="moxi-a-snap.timer", LoadState="loaded", ActiveState="active",
               SubState="waiting", UnitFileState="enabled", Triggers="moxi-a-snap.service"))
    unit = UnitRef("a", "snap", "timer", ("moxi-a-snap.timer", "moxi-a-snap.service"))
    [read] = SystemdBackend().status([unit], Fence("systemd", Recorder(show=show)))
    assert read.state == "active"
    assert read.last == {"result": "exit-code", "status": "1"}


@pytest.mark.parametrize("timer_state, state", [("inactive", "inactive"),
                                                ("failed", "failed")])
def test_a_timer_that_is_not_armed_reads_its_timers_state(unit_root, timer_state, state):
    show = _shown(
        _block(Id="moxi-a-snap.service", LoadState="loaded", ActiveState="inactive",
               Result="success", ExecMainStatus="0", UnitFileState="static"),
        _block(Id="moxi-a-snap.timer", LoadState="loaded", ActiveState=timer_state,
               SubState="dead", UnitFileState="disabled", Triggers="moxi-a-snap.service"))
    unit = UnitRef("a", "snap", "timer", ("moxi-a-snap.timer", "moxi-a-snap.service"))
    [read] = SystemdBackend().status([unit], Fence("systemd", Recorder(show=show)))
    assert (read.state, read.enabled) == (state, False)


def test_a_foreign_timer_whose_service_read_fails_keeps_its_timers_state(unit_root):
    def run(cmd, *, env=None, timeout=None):
        if "backup.timer" in cmd:
            return subprocess.CompletedProcess(cmd, 0, _block(
                Id="backup.timer", LoadState="loaded", ActiveState="active",
                SubState="waiting", UnitFileState="enabled",
                Triggers="backup-run.service"), "")
        return subprocess.CompletedProcess(cmd, 1, "", "Connection timed out\n")

    [read] = SystemdBackend().status([UnitRef(None, "backup.timer", "timer",
                                              ("backup.timer",))], Fence("systemd", run))
    assert read.state == "active" and read.last is None
    assert read.triggers == "backup-run.service"  # from the first show, which answered
    assert "its triggered unit could not be read" in read.detail
    assert "Connection timed out" in read.detail


# -- wrap ---------------------------------------------------------------------------------------

class Manager:
    """A runner for a wrap: the manager's version, one foreign timer and its service."""

    def __init__(self, root: Path, *, version="258.11-1.fc43", triggers="backup-run.service",
                 load="loaded", type_="oneshot", starts=1):
        self.root = root
        self.version = version
        self.triggers = triggers
        self.load = load
        self.type = type_
        self.starts = starts
        self.calls: list[tuple[str, ...]] = []
        self.reload_fails = ""

    def __call__(self, cmd, *, env=None, timeout=None):
        args = tuple(a for a in cmd[2:] if a != "--")
        self.calls.append(args)
        if args[0] == "daemon-reload":
            return subprocess.CompletedProcess(cmd, 1 if self.reload_fails else 0, "",
                                               self.reload_fails)
        if args == ("show", "--property=Version"):
            return subprocess.CompletedProcess(cmd, 0, f"Version={self.version}\n", "")
        unit = args[-1]
        if unit.endswith(".timer"):
            out = _block(Id=unit, LoadState=self.load, Triggers=self.triggers)
        else:
            dropins = " ".join(str(p) for p in sorted((self.root / f"{unit}.d").glob("*")))
            start = ("{ path=/opt/backup ; argv[]=/opt/backup ; ignore_errors=no ; "
                     "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; "
                     "status=0/0 }")
            out = _block(Id=unit, LoadState="loaded", Type=self.type)
            out += "".join(f"ExecStart={start}\n" for _ in range(self.starts))
            out += f"DropInPaths={dropins}\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")


def _wrap(root, manager=None, *, module="services", key="backup.enabled", name="backup",
          ref="backup.timer"):
    manager = manager or Manager(root)
    acted = SystemdBackend().wrap(ref, module=module, key=key, name=name,
                                  fence=Fence("systemd", manager))
    return acted, manager


@pytest.mark.skipif(os.name == "nt", reason="the golden text writes the gate's path "
                    "bare, as a POSIX path is; Windows never uses systemd")
def test_wrap_writes_the_marked_drop_in_and_reloads_once(unit_root, bin_dir):
    acted, manager = _wrap(unit_root)
    assert acted.ok and (acted.module, acted.name, acted.act) == (None, "backup.timer",
                                                                  "wrap")
    path = unit_root / "backup-run.service.d" / "50-moxi-services.conf"
    assert path.read_text() == textwrap.dedent(f"""\
        # moxi wrap: services/backup
        # moxi: timer = backup.timer, if = services.backup.enabled
        [Service]
        ExecCondition={bin_dir}/moxi run --if services.backup.enabled --record services/backup --condition
        ExecStopPost={bin_dir}/moxi run --record services/backup --result
        """)
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    assert manager.calls == [
        ("show", "--property=Version"), ("show", "--property=Id,LoadState,Triggers",
                                         "backup.timer"),
        ("show", "--property=Id,LoadState,Type,ExecStart,DropInPaths",
         "backup-run.service"),
        ("daemon-reload",)]
    assert acted.lines[-1] == "turn them on with: moxi config set services backup.enabled true"


def test_the_wrap_forms_in_the_drop_in_parse_in_the_gate(unit_root, bin_dir):
    import re
    import shlex

    from moxi import run

    _wrap(unit_root)
    text = (unit_root / "backup-run.service.d" / "50-moxi-services.conf").read_text()
    forms = {}
    for key in ("ExecCondition", "ExecStopPost"):
        words = shlex.split(re.search(rf"^{key}=(.*)$", text, re.M)[1])
        assert words[:2] == [str(moxi.paths.script("moxi")), "run"]
        forms[key] = run.parse(words[2:])
    condition, result = forms["ExecCondition"], forms["ExecStopPost"]
    assert (condition.condition, condition.switch, condition.module, condition.name) == (
        True, ("services", "backup.enabled"), "services", "backup")
    assert (result.result, result.switch, result.module, result.name) == (
        True, None, "services", "backup")


def test_a_second_wrap_with_the_same_key_and_name_rewrites_the_drop_in(unit_root, bin_dir):
    _wrap(unit_root)
    path = unit_root / "backup-run.service.d" / "50-moxi-services.conf"
    path.write_text(path.read_text() + "# stale\n")
    acted, _ = _wrap(unit_root)
    assert acted.ok and "# stale" not in path.read_text()


@pytest.mark.parametrize("manager, words", [
    (dict(version="242"), "systemd 242 has no ExecCondition= (it needs 243); not wrapped"),
    (dict(triggers=""), "backup.timer triggers 0 units, not one"),
    (dict(triggers="a.service b.service"), "backup.timer triggers 2 units, not one"),
    (dict(load="not-found"), "backup.timer is not loaded (not-found)"),
    (dict(starts=2), "backup-run.service has 2 ExecStart lines, not one"),
    (dict(starts=0), "backup-run.service has 0 ExecStart lines, not one"),
    (dict(type_="forking"), "backup-run.service is Type=forking"),
])
def test_wrap_refuses_what_it_cannot_wrap(unit_root, bin_dir, manager, words):
    acted, run = _wrap(unit_root, Manager(unit_root, **manager))
    assert not acted.ok and len(acted.lines) == 1 and words in acted.lines[0]
    assert ("daemon-reload",) not in run.calls
    assert not (unit_root / "backup-run.service.d").exists()


def test_wrap_refuses_a_service_another_module_wrapped(unit_root, bin_dir):
    _wrap(unit_root, module="tools", key="b.enabled")
    acted, _ = _wrap(unit_root)
    assert not acted.ok and acted.lines == ("backup-run.service is wrapped by tools",)
    assert not (unit_root / "backup-run.service.d" / "50-moxi-services.conf").exists()


def test_wrap_never_touches_an_unmarked_file_at_its_path(unit_root, bin_dir):
    path = unit_root / "backup-run.service.d" / "50-moxi-services.conf"
    path.parent.mkdir()
    path.write_text("[Service]\nEnvironment=A=1\n")
    acted, _ = _wrap(unit_root)
    assert not acted.ok and "was not written by moxi" in acted.lines[0]
    assert path.read_text() == "[Service]\nEnvironment=A=1\n"


def test_wrap_rewrites_this_modules_drop_in_whatever_it_held(unit_root, bin_dir):
    """The core refuses a conflicting wrap of the module first (from `wraps`); the
    backend rewrites a drop-in that carries the module's mark."""
    _wrap(unit_root)
    acted, _ = _wrap(unit_root, key="other.enabled", name="nightly")
    path = unit_root / "backup-run.service.d" / "50-moxi-services.conf"
    assert acted.ok and "if = services.other.enabled" in path.read_text()


def test_wraps_reads_every_drop_in_of_the_module_with_no_call(unit_root, bin_dir):
    _wrap(unit_root)
    _wrap(unit_root, ref="other.timer", manager=Manager(unit_root,
                                                        triggers="other-run.service"),
          key="o.enabled", name="other")
    _wrap(unit_root, module="tools", ref="third.timer",
          manager=Manager(unit_root, triggers="third-run.service"), key="t.enabled")
    assert SystemdBackend().wraps("services") == (
        ("backup.timer", "backup-run.service", Wrapped("services", "backup.enabled",
                                                       "backup")),
        ("other.timer", "other-run.service", Wrapped("services", "o.enabled", "other")))
    assert SystemdBackend().wraps("nobody") == ()


def test_wrap_refuses_moxis_own_names_and_non_timers(unit_root, bin_dir):
    acted, run = _wrap(unit_root, ref="moxi-demo-snap.timer")
    assert not acted.ok and "moxi's own unit names" in acted.lines[0] and run.calls == []
    acted, run = _wrap(unit_root, ref="backup.service")
    assert not acted.ok and "is not a timer" in acted.lines[0] and run.calls == []


def test_wrap_reports_a_failed_reload(unit_root, bin_dir):
    manager = Manager(unit_root)
    manager.reload_fails = "Failed to reload daemon: bus error"
    acted, _ = _wrap(unit_root, manager)
    assert not acted.ok and "bus error" in acted.lines[0]


def test_unwrap_finds_the_drop_in_by_its_mark_after_the_timer_is_gone(unit_root, bin_dir):
    _wrap(unit_root)
    _wrap(unit_root, module="tools", key="x.enabled", ref="other.timer",
          manager=Manager(unit_root, triggers="other-run.service"))
    run = Recorder()
    acted = SystemdBackend().unwrap("backup.timer", module="services",
                                    fence=Fence("systemd", run))
    assert acted.ok and run.calls == [("daemon-reload",)]
    assert not (unit_root / "backup-run.service.d").exists()
    assert (unit_root / "other-run.service.d" / "50-moxi-tools.conf").exists()


def test_unwrap_of_a_timer_not_wrapped_is_ok_and_reloads_nothing(unit_root):
    run = Recorder()
    acted = SystemdBackend().unwrap("backup.timer", module="services",
                                    fence=Fence("systemd", run))
    assert acted.ok and acted.lines == ("backup.timer is not wrapped by services",)
    assert run.calls == []


def test_unwrap_leaves_another_file_in_the_drop_in_folder(unit_root, bin_dir):
    _wrap(unit_root)
    other = unit_root / "backup-run.service.d" / "10-local.conf"
    other.write_text("[Service]\n")
    SystemdBackend().unwrap("backup.timer", module="services",
                            fence=Fence("systemd", Recorder()))
    assert other.exists() and not (other.parent / "50-moxi-services.conf").exists()


def test_unwrap_all_takes_every_drop_in_of_the_module_with_one_reload(unit_root, bin_dir):
    _wrap(unit_root)
    _wrap(unit_root, ref="other.timer", manager=Manager(unit_root,
                                                        triggers="other-run.service"),
          key="o.enabled", name="other")
    _wrap(unit_root, module="tools", ref="third.timer",
          manager=Manager(unit_root, triggers="third-run.service"), key="t.enabled")
    run = Recorder()
    acted = SystemdBackend().unwrap_all("services", Fence("systemd", run))
    assert sorted(a.name for a in acted) == ["backup.timer", "other.timer"]
    assert all(a.ok for a in acted) and run.calls == [("daemon-reload",)]
    assert (unit_root / "third-run.service.d" / "50-moxi-tools.conf").exists()
    assert SystemdBackend().unwrap_all("services", Fence("systemd", run)) == []


def test_the_marked_units_never_include_a_drop_in(unit_root, bin_dir):
    _wrap(unit_root, ref="backup.timer")
    (unit_root / "moxi-services-x.service.d").mkdir()
    (unit_root / "moxi-services-x.service.d" / "50-moxi-services.conf").write_text(
        "# moxi wrap: services/x\n# moxi: timer = x.timer, if = services.x.enabled\n")
    assert SystemdBackend().marked("services") == ()
