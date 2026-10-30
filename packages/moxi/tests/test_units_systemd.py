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
    _script(scripts / "moxi")
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


def test_a_socket_is_a_unit_of_its_own_with_one_listen_line_per_address(unit_root):
    daemon, socket = SystemdBackend().render(spec(
        module="ui", name="shell", doc="the shell",
        socket=Socket("port", ("127.0.0.1", "::1"), 8470)))
    assert daemon.kind == "daemon" and socket.kind == "socket"
    [(path, text)] = socket.files
    assert path == unit_root / "moxi-ui-shell.socket"
    assert text == textwrap.dedent("""\
        # moxi unit: ui/shell
        [Unit]
        Description=Moxi ui: the shell (socket)

        [Socket]
        ListenStream=127.0.0.1:8470
        ListenStream=[::1]:8470

        [Install]
        WantedBy=sockets.target
        """)


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
    assert len(paths) == 6
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
        return _script(bin_dir / name)

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


def test_a_new_daemon_with_a_socket_starts_the_socket_first(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', "
                     "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
    w.apply(units.plan("demo"))
    assert w.sup.calls[:3] == [("daemon-reload",),
                               ("enable", "--now", "moxi-demo-relay.socket"),
                               ("enable", "--now", "moxi-demo-relay.service")]
    assert "ListenStream=127.0.0.1:8470" in (w.root / "moxi-demo-relay.socket").read_text()


def test_a_changed_port_stops_the_service_restarts_the_socket_and_starts_it_again(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', "
                     "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
    w.apply(units.plan("demo"))
    from moxi import config

    config.set("demo", "port", "9000", by="test")
    plan = {(c.kind, c.action) for c in units.plan("demo")}
    assert plan == {("daemon", "keep"), ("socket", "change")}
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("stop", "moxi-demo-relay.service"),
                           ("enable", "moxi-demo-relay.socket"),
                           ("restart", "moxi-demo-relay.socket"),
                           ("start", "moxi-demo-relay.service")]


KEEP_SOCKET = ("{'relay': {'command': 'moxi-demo-relay', 'upgrade': 'keep', "
               "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")


@pytest.mark.parametrize("service_too", [False, True])
def test_a_changed_socket_of_a_running_keep_daemon_ends_with_it_running(w, service_too):
    """A changed socket restarts its daemon whatever `upgrade` says: the service is
    stopped for the socket's restart and started again, never left for the socket's next
    connection, whether or not the service's own text changed too."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=KEEP_SOCKET)
    w.apply(units.plan("demo"))
    assert w.sup.units["moxi-demo-relay.service"]["active"]
    from moxi import config

    config.set("demo", "port", "9000", by="test")
    if service_too:
        (w.root / "moxi-demo-relay.service").write_text("# moxi unit: demo/relay\nold\n")
    plan = {(c.kind, c.action) for c in units.plan("demo")}
    assert plan == {("daemon", "change" if service_too else "keep"), ("socket", "change")}
    w.sup.calls.clear()
    done = w.apply(units.plan("demo"))
    assert all(a.ok for a in done)
    assert w.sup.calls[:5] == [("daemon-reload",), ("stop", "moxi-demo-relay.service"),
                               ("enable", "moxi-demo-relay.socket"),
                               ("restart", "moxi-demo-relay.socket"),
                               ("start", "moxi-demo-relay.service")]
    assert w.sup.calls[5:] == ([("enable", "moxi-demo-relay.service")] if service_too
                               else [])
    assert w.sup.units["moxi-demo-relay.service"]["active"]
    if service_too:
        [line] = [line for a in done if a.change.kind == "daemon" for line in a.lines]
        assert line == "moxi-demo-relay.service rewritten and restarted, since its socket changed"


def test_a_new_socket_of_a_restart_daemon_is_started_by_the_services_own_restart(w):
    """With `upgrade = "restart"` the service's own change starts it after the socket, so
    the socket step does not start it as well."""
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', "
                     "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls == [("daemon-reload",), ("stop", "moxi-demo-relay.service"),
                           ("enable", "--now", "moxi-demo-relay.socket"),
                           ("enable", "moxi-demo-relay.service"),
                           ("restart", "moxi-demo-relay.service")]
    assert w.sup.units["moxi-demo-relay.service"]["active"]


def test_dropping_a_socket_disables_it_before_the_service_is_restarted(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', "
                     "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
    w.apply(units.plan("demo"))
    w.module(DAEMONS=DAEMON)
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert w.sup.calls[0] == ("disable", "--now", "moxi-demo-relay.socket")
    assert not (w.root / "moxi-demo-relay.socket").exists()


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
                ("enable", "--now", "moxi-demo-relay.service")]),
    ("disable", [("disable", "--now", "moxi-demo-relay.socket"),
                 ("disable", "--now", "moxi-demo-relay.service")]),
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
    assert len(run.calls) == 2 and not any("backup.service" in c for c in run.calls)


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
               and s.last is None for s in states)


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
        assert words[:2] == [str(bin_dir / "moxi"), "run"]
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
