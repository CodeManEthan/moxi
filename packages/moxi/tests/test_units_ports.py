"""One port, one home: `plan` refuses a daemon's address only when it can show that what
holds it is not this home's own daemon. Another home's marked socket file naming it, or a
listener this home's daemon doesn't hold; never the port alone, and never this home's
daemon re-installed while it runs."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import types
from contextlib import closing

import pytest

import moxi.modules
import moxi.paths
from moxi import config, units
from moxi.units import _backend, _plan
from moxi.units._backend import Socket, Spec
from moxi.units._launchd import LaunchdBackend
from moxi.units._systemd import SystemdBackend
from moxi_dev.testing import console_script

from fake_backend import FakeBackend, FakeSupervisor

#: Every test starts in the default home; one that wants a tagged home sets `MOXI_HOME`.
pytestmark = pytest.mark.usefixtures("default_home")

SETTINGS = """
[port]
type = "int"
default = 8470
doc = "The shell's port."
read = "start"
"""

STEM = "moxi-demo-shell"


def shell(*addresses):
    return (f"{{'shell': {{'command': 'moxi-demo-shell', 'socket': {{'setting': 'port', "
            f"'addresses': {list(addresses)!r}}}}}}}")


@pytest.fixture
def w(site, tmp_path, monkeypatch):
    """A module `demo` whose daemon `shell` has a socket, the fake backend over one unit
    directory, `home(path)` to move to a tagged home (None: the default home), and a
    connect stand-in that records each connect and answers only for `listening`."""
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    for name in ("moxi-demo-shell", "moxi"):
        console_script(scripts, name)
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: True)
    root = tmp_path / "units"
    root.mkdir()
    supervisor = FakeSupervisor(root)
    backend = FakeBackend(root, runner=supervisor)
    connects: list[tuple[str, int]] = []
    listening: set[tuple[str, int]] = set()

    def answers(address, port, timeout=0.5):
        connects.append((address, port))
        return (address, port) in listening

    monkeypatch.setattr(_plan, "answers", answers)

    def module(*addresses):
        site.write("moxi_demo/settings.toml", SETTINGS)
        site.module("demo", SETTINGS="'settings.toml'",
                    DAEMONS=shell(*(addresses or ("127.0.0.1",))))
        moxi.modules.reset()
        sys.modules.pop("moxi_demo.module", None)

    def home(path):
        if path is None:
            monkeypatch.delenv("MOXI_HOME", raising=False)
        else:
            monkeypatch.setenv("MOXI_HOME", str(path))

    def plan(**kw):
        return {(c.name, c.kind): c for c in units.plan("demo", **kw)}

    def apply(**kw):
        done = units.apply(units.plan("demo"), say=lambda line: None, **kw)
        assert all(a.ok for a in done), done
        connects.clear()
        return done

    module()
    with _backend.use(backend):
        yield types.SimpleNamespace(root=root, sup=supervisor, backend=backend,
                                    module=module, home=home, plan=plan, apply=apply,
                                    connects=connects, listening=listening, tmp=tmp_path)


def refused(plan) -> str:
    """The one reason every change of `plan` is refused with, cause `socket`."""
    assert {(c.action, c.cause) for c in plan.values()} == {("refuse", "socket")}, plan
    [reason] = {c.reason for c in plan.values()}
    assert "fix: give this home's demo/shell another port: moxi config set demo port " \
           "<port>" in reason
    return reason


@pytest.fixture
def listener():
    """A socket of this process listening on a free loopback port."""
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield sock.getsockname()[1]


@pytest.fixture
def other_process():
    """A process of its own (not under this test's) listening on a free loopback port, as
    `(pid, port)`."""
    code = ("import socket, sys, time\n"
            "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen()\n"
            "print(s.getsockname()[1], flush=True); time.sleep(120)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        yield proc.pid, int(proc.stdout.readline())
    finally:
        proc.kill()
        proc.wait(10)
        proc.stdout.close()


# -- check 1: another home's socket file ---------------------------------------------------

def test_a_tagged_home_is_refused_by_the_default_homes_socket_file(w):
    w.apply()
    w.home(w.tmp / "scratch")
    reason = refused(w.plan())
    assert (f"127.0.0.1:8470 is named by {w.root / f'{STEM}.socket'}, the socket of "
            "demo/shell of another Moxi home") in reason
    assert "remove the file by hand" in reason
    assert w.connects == []


def test_the_default_home_is_refused_by_a_tagged_homes_socket_file(w):
    w.home(w.tmp / "scratch")
    w.apply()
    [tagged] = w.root.glob("moxi-h*-demo-shell.socket")
    w.home(None)
    assert f"is named by {tagged}, the socket of demo/shell of another Moxi home" in \
        refused(w.plan())


@pytest.mark.parametrize("tagged", [False, True])
def test_a_homes_own_socket_file_does_not_count(w, tagged):
    w.home(w.tmp / "scratch" if tagged else None)
    w.apply()
    assert {c.action for c in w.plan().values()} == {"keep"}


def test_an_unmarked_socket_file_or_another_port_does_not_count(w):
    (w.root / "someone.socket").write_text("listen = 127.0.0.1:8470\n")
    (w.root / "moxi-other-shell.socket").write_text(
        "# moxi unit: other/shell\nlisten = 127.0.0.1:8471\n")
    assert {c.action for c in w.plan().values()} == {"add"}


def test_another_module_of_this_home_on_the_port_is_named_as_this_homes(w):
    (w.root / "moxi-other-shell.socket").write_text(
        "# moxi unit: other/shell\nlisten = 127.0.0.1:8470\n")
    assert "the socket of other/shell of this Moxi home" in refused(w.plan())


def test_two_spellings_of_an_ipv6_address_are_one_address(w):
    w.module("::1")
    (w.root / "moxi-other-shell.socket").write_text(
        "# moxi unit: other/shell\nlisten = [0:0::1]:8470\n")
    assert "[::1]:8470 is named by" in refused(w.plan())


# -- check 2: a listener that isn't this home's own ----------------------------------------

def test_a_home_whose_own_daemon_listens_reinstalls_with_no_refusal(w, listener):
    w.backend.sockets = "none"
    w.sup.pid = os.getpid()
    w.home(w.tmp / "scratch")
    config.set("demo", "port", listener, by="test")
    w.apply()
    w.listening.add(("127.0.0.1", listener))
    assert w.plan()[("shell", "daemon")].action == "keep"
    assert w.connects == [("127.0.0.1", listener)]


def test_a_listener_of_another_process_is_refused_with_the_own_daemon_stopped(w, listener):
    w.backend.sockets = "none"
    w.sup.pid = os.getpid()
    w.home(w.tmp / "scratch")
    config.set("demo", "port", listener, by="test")
    w.apply()
    [stopped] = units.stop("demo", ["shell"])
    assert stopped.ok
    w.listening.add(("127.0.0.1", listener))
    assert (f"something that is not this home's demo/shell listens on "
            f"127.0.0.1:{listener}, and this home's demo/shell isn't running"
            ) in refused(w.plan())


def test_a_listener_of_another_process_is_refused_with_the_own_daemon_on_another_port(
        w, listener, other_process):
    """The daemon runs (its pid is a process listening on another port), and this
    process, which is not under it, holds the address."""
    pid, its_port = other_process
    w.backend.sockets = "none"
    w.sup.pid = pid
    w.home(w.tmp / "scratch")
    config.set("demo", "port", its_port, by="test")
    w.apply()
    w.listening.update({("127.0.0.1", its_port), ("127.0.0.1", listener)})
    assert w.plan()[("shell", "daemon")].action == "keep"
    config.set("demo", "port", listener, by="test")
    assert (f"listens on 127.0.0.1:{listener}, and the running daemon (pid {pid}) doesn't "
            "hold it") in refused(w.plan())


def test_an_unknown_read_gives_a_line_and_no_refusal(w, listener, capsys):
    w.backend.sockets = "none"
    w.home(w.tmp / "scratch")
    config.set("demo", "port", listener, by="test")
    w.apply()
    w.listening.add(("127.0.0.1", listener))
    w.sup.fail[("show", None)] = "Failed to connect to bus"
    capsys.readouterr()
    assert w.plan()[("shell", "daemon")].action == "keep"
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and err[0].startswith(
        f"demo/shell: something listens on 127.0.0.1:{listener}, and the supervisor read "
        "failed (")
    assert err[0].endswith("so the plan can't tell whether it is this home's daemon; "
                           "not refused")


def test_a_process_that_cannot_be_read_gives_a_line_and_no_refusal(w, listener, capsys,
                                                                    monkeypatch):
    import psutil

    w.backend.sockets = "none"
    w.sup.pid = os.getpid()
    config.set("demo", "port", listener, by="test")
    w.apply()
    w.listening.add(("127.0.0.1", listener))

    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", denied)
    capsys.readouterr()
    assert w.plan()[("shell", "daemon")].action == "keep"
    [line] = capsys.readouterr().err.splitlines()
    assert f"(pid {os.getpid()}) can't be read" in line and line.endswith("not refused")


def test_an_active_own_socket_naming_the_address_passes_without_a_connect(w):
    w.home(w.tmp / "scratch")
    w.apply()
    w.listening.add(("127.0.0.1", 8470))
    assert {c.action for c in w.plan().values()} == {"keep"}
    assert w.connects == []
    # Stopped, it holds nothing: the plan connects, and the own daemon isn't running.
    [stopped] = units.stop("demo", ["shell"])
    assert stopped.ok
    assert "isn't running" in refused(w.plan())
    assert w.connects == [("127.0.0.1", 8470)]


def test_a_free_address_is_planned_with_no_supervisor_read(w):
    w.sup.calls.clear()
    assert {c.action for c in w.plan().values()} == {"add"}
    assert w.connects == [("127.0.0.1", 8470)] and w.sup.calls == []


def test_an_unmarked_file_at_the_homes_name_is_foreign_and_no_listener_is_checked(w):
    (w.root / f"{STEM}.service").write_text("[Service]\nExecStart=/bin/true\n")
    w.listening.add(("127.0.0.1", 8470))
    plan = w.plan()
    assert (plan[("shell", "daemon")].action, plan[("shell", "daemon")].cause) == (
        "refuse", "foreign")
    assert w.connects == []
    done = units.apply(list(plan.values()), replace=True, say=lambda line: None)
    assert all(a.ok for a in done), done


# -- the pieces ----------------------------------------------------------------------------

def spec(*addresses, port=8450) -> Spec:
    return Spec(module="comms", name="daemon", kind="daemon",
                command=("/opt/env/bin/moxi-comms-daemon",), doc="the delivery daemon",
                entry={}, socket=Socket("mcp.port", tuple(addresses), port))


def test_systemd_reads_every_homes_marked_socket_units(tmp_path, monkeypatch):
    backend = SystemdBackend(tmp_path)
    for home in (None, tmp_path / "scratch"):
        if home is not None:
            monkeypatch.setenv("MOXI_HOME", str(home))
        for group in backend.render(spec("127.0.0.1", "::1")):
            for path, text in group.files:
                path.write_text(text)
    (tmp_path / "plain.socket").write_text("[Socket]\nListenStream=127.0.0.1:8450\n")
    found = {path.name: (mark, listens) for path, mark, listens in backend.socket_files()}
    tag = units.home_tag()
    assert found == {
        "moxi-comms-daemon.socket": ("comms/daemon", (("127.0.0.1", 8450),)),
        "moxi-comms-daemon-1.socket": ("comms/daemon", (("::1", 8450),)),
        f"moxi-{tag}-comms-daemon.socket": ("comms/daemon", (("127.0.0.1", 8450),)),
        f"moxi-{tag}-comms-daemon-1.socket": ("comms/daemon", (("::1", 8450),))}


def test_launchd_reads_every_homes_marked_plist_sockets(tmp_path, monkeypatch):
    backend = LaunchdBackend(agents=tmp_path, uid=501, gate="/opt/moxi/bin/moxi")
    for home in (None, tmp_path / "scratch"):
        if home is not None:
            monkeypatch.setenv("MOXI_HOME", str(home))
        [group] = backend.render(spec("127.0.0.1", "::1"))
        for path, text in group.files:
            path.write_text(text)
        [group] = backend.render(Spec(module="comms", name="relay", kind="daemon",
                                      command=("/bin/true",), doc="no socket", entry={}))
        for path, text in group.files:
            path.write_text(text)
    found = {path.name: (mark, listens) for path, mark, listens in backend.socket_files()}
    tag = units.home_tag()
    both = ("comms/daemon", (("127.0.0.1", 8450), ("::1", 8450)))
    assert found == {"moxi.comms.daemon.plist": both,
                     f"moxi.{tag}.comms.daemon.plist": both}


@pytest.mark.parametrize("bound, address, covers", [
    ("127.0.0.1", "127.0.0.1", True),
    ("0.0.0.0", "127.0.0.1", True),
    ("::", "127.0.0.1", True),
    ("::", "::1", True),
    ("0.0.0.0", "::1", False),
    ("::ffff:127.0.0.1", "127.0.0.1", True),
    ("127.0.0.2", "127.0.0.1", False),
    ("::1", "0:0::1", True),
])
def test_a_bound_address_covers_what_it_takes(bound, address, covers):
    assert _plan._covers(bound, address) is covers


def test_this_process_is_read_as_listening(listener):
    assert _plan.listening(os.getpid(), "127.0.0.1", listener) is True
    assert _plan.listening(os.getpid(), "127.0.0.2", listener) is False


@pytest.mark.honest_ports
def test_a_connect_is_answered_only_where_something_listens(listener):
    assert _plan.answers("127.0.0.1", listener) is True
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        free = sock.getsockname()[1]
    assert _plan.answers("127.0.0.1", free) is False
