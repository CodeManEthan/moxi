"""A daemon's sockets on the plan and status side, on the fake backend: one socket unit per
address, the set as one change with its dropped units, the IPv6 leave-out, and the socket
state with its epoch on every backend."""

from __future__ import annotations

import errno
import socket as _socket
import types

import pytest

import moxi.modules
import moxi.paths
from moxi import config, units
from moxi.units import _backend, _plan
from moxi.units._backend import UnitRef, socket_name, socket_place, socket_state
from moxi.units._fake import FakeBackend, FakeSupervisor

SETTINGS = """
[port]
type = "int"
default = 8470
doc = "The shell's port."
read = "start"

[shell.enabled]
type = "bool"
default = false
doc = "Run the shell."
read = "use"
"""

STEM = "moxi-demo-shell"
#: The real probe and socket, before a test patches them.
PROBE = _plan.ipv6_loopback
REAL_SOCKET = _socket.socket


def shell(*addresses, name="shell", more=""):
    return (f"'{name}': {{'command': 'moxi-demo-shell', 'socket': {{'setting': 'port', "
            f"'addresses': {list(addresses)!r}}}{more}}}")


@pytest.fixture
def w(site, tmp_path, monkeypatch):
    scripts = tmp_path / "bin"
    scripts.mkdir()
    (scripts / "moxi-demo-shell").write_text("#!/bin/sh\n")
    (scripts / "moxi-demo-shell").chmod(0o755)
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: True)
    root = tmp_path / "units"
    root.mkdir()
    supervisor = FakeSupervisor(root)
    backend = FakeBackend(root, runner=supervisor)
    lines: list[str] = []

    def module(*daemons):
        site.write("moxi_demo/settings.toml", SETTINGS)
        site.module("demo", SETTINGS="'settings.toml'", DAEMONS="{" + ", ".join(daemons) + "}")
        moxi.modules.reset()
        __import__("sys").modules.pop("moxi_demo.module", None)

    def plan(**kw):
        return {(c.name, c.kind): c for c in units.plan("demo", **kw)}

    def apply(changes):
        done = units.apply(list(changes), say=lines.append)
        assert all(a.ok for a in done), (done, lines)
        return done

    with _backend.use(backend):
        yield types.SimpleNamespace(root=root, sup=supervisor, backend=backend,
                                    module=module, plan=plan, apply=apply, lines=lines)


# -- names -----------------------------------------------------------------------------------

def test_a_socket_is_named_by_its_address_place():
    assert [socket_name(STEM, k) for k in range(3)] == [STEM, f"{STEM}-1", f"{STEM}-2"]
    assert socket_name("Listeners", 1) == "Listeners-1"
    assert [socket_place(STEM, n) for n in (STEM, f"{STEM}-1", f"{STEM}-12")] == [0, 1, 12]
    for other in (f"{STEM}-01", f"{STEM}-0", f"{STEM}-x", f"{STEM}-", "moxi-demo-shellx",
                  f"{STEM}-1-1", f"{STEM}-١"):
        assert socket_place(STEM, other) is None, other


# -- plan ------------------------------------------------------------------------------------

def test_a_new_set_is_one_add_then_keep(w):
    w.module(shell("127.0.0.1", "::1"))
    plan = w.plan()
    sock = plan[("shell", "socket")]
    assert sock.action == "add" and sock.dropped == ()
    assert [p.name for p in sock.paths] == [f"{STEM}.socket", f"{STEM}-1.socket"]
    assert "listen = 127.0.0.1:8470" in sock.texts[0]
    assert "listen = [::1]:8470" in sock.texts[1]
    assert f"sockets = {STEM}.socket {STEM}-1.socket" in plan[("shell", "daemon")].text
    w.apply(plan.values())
    plan = w.plan()
    assert (plan[("shell", "socket")].action, plan[("shell", "daemon")].action) == (
        "keep", "keep")
    refs = {(r.name, r.kind): r for r in w.backend.marked("demo")}
    assert refs[("shell", "socket")].ids == (f"{STEM}.socket", f"{STEM}-1.socket")


def test_a_changed_port_changes_the_set_and_not_the_service(w):
    w.module(shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    config.set("demo", "port", 8471, by="test")
    plan = w.plan()
    assert plan[("shell", "socket")].action == "change"
    assert plan[("shell", "socket")].dropped == ()
    assert plan[("shell", "daemon")].action == "keep"


def test_an_address_added_writes_a_new_unit_and_changes_the_service(w):
    w.module(shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.module(shell("127.0.0.1", "::1", "10.0.0.1"))
    plan = w.plan()
    sock = plan[("shell", "socket")]
    assert sock.action == "change" and sock.dropped == ()
    assert sock.paths[-1].name == f"{STEM}-2.socket"
    assert plan[("shell", "daemon")].action == "change"
    w.sup.calls.clear()
    w.apply(plan.values())
    assert ("enable", "--now", f"{STEM}-2.socket") in w.sup.calls
    assert not any(c[0] in ("enable", "disable") and f"{STEM}.service" in c
                   for c in w.sup.calls)


def test_an_address_taken_out_is_dropped_disabled_and_deleted(w):
    w.module(shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.module(shell("127.0.0.1"))
    plan = w.plan()
    sock = plan[("shell", "socket")]
    assert sock.action == "change"
    assert [p.name for p in sock.dropped] == [f"{STEM}-1.socket"]
    assert f"drops {STEM}-1.socket" in sock.reason
    assert plan[("shell", "daemon")].action == "change"
    w.sup.calls.clear()
    w.apply(plan.values())
    assert ("disable", "--now", f"{STEM}-1.socket") in w.sup.calls
    assert not (w.root / f"{STEM}-1.socket").exists()
    assert w.plan()[("shell", "socket")].action == "keep"


def test_names_follow_places_so_a_first_address_taken_out_renames_none(w):
    w.module(shell("::1", "127.0.0.1"))
    w.apply(units.plan("demo"))
    assert "127.0.0.1" in (w.root / f"{STEM}-1.socket").read_text()


def test_socket_taken_off_the_entry_removes_the_whole_set(w):
    w.module(shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.module("'shell': {'command': 'moxi-demo-shell'}")
    plan = w.plan()
    assert plan[("shell", "socket")].action == "remove"
    assert [p.name for p in plan[("shell", "socket")].paths] == [
        f"{STEM}.socket", f"{STEM}-1.socket"]
    w.apply(plan.values())
    assert not list(w.root.glob("*.socket"))


def test_a_set_changed_under_a_running_daemon_is_applied_between_one_stop_and_start(w):
    w.module(shell("127.0.0.1", "::1", more=", 'upgrade': 'keep'"))
    w.apply(units.plan("demo"))
    assert ("stop", f"{STEM}.service") not in w.sup.calls  # a new daemon gets no stop
    w.module(shell("127.0.0.1", more=", 'upgrade': 'keep'"))
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    service = [c for c in w.sup.calls if f"{STEM}.service" in c and c[0] != "show"]
    assert service == [("stop", f"{STEM}.service"), ("start", f"{STEM}.service")]
    assert w.sup.units[f"{STEM}.service"]["active"]


def test_the_stop_record_takes_every_socket_unit_first_in_unit_order(w):
    w.module(shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    record: list = []
    units.stop("demo", ["shell"], record=record)
    assert [e.unit for e in record] == [f"{STEM}.socket", f"{STEM}-1.socket",
                                        f"{STEM}.service"]


# -- the IPv6 probe --------------------------------------------------------------------------

class _Probe:
    """`socket.socket` that fails its create or its bind with `code`, counting binds."""

    def __init__(self, code, *, at="bind"):
        self.code, self.at, self.binds = code, at, 0

    def __call__(self, family, kind):
        if self.at == "create":
            self.binds += 1
            raise OSError(self.code, "patched")
        probe = self

        class Sock:
            def bind(self, address):
                probe.binds += 1
                assert address == ("::1", 0)
                raise OSError(probe.code, "patched")

            def close(self):
                pass
        return Sock()


@pytest.mark.parametrize("code, at", [(errno.EADDRNOTAVAIL, "bind"),
                                      (errno.EAFNOSUPPORT, "create")])
def test_no_ipv6_loopback_leaves_ipv6_out_once_per_plan(w, monkeypatch, code, at):
    monkeypatch.setattr(_plan, "ipv6_loopback", PROBE)
    w.module(shell("127.0.0.1", "::1"), shell("::1", name="other"),
             shell("127.0.0.1", name="v4"))
    probe = _Probe(code, at=at)
    monkeypatch.setattr(_socket, "socket", probe)
    plan = w.plan()
    assert probe.binds == 1
    sock = plan[("shell", "socket")]
    assert [p.name for p in sock.paths] == [f"{STEM}.socket"]
    note = "::1 left out: no IPv6 loopback on this system"
    assert note in sock.reason and note in plan[("shell", "daemon")].reason
    assert note not in plan[("v4", "socket")].reason
    other = plan[("other", "daemon")]
    assert (other.action, other.cause) == ("refuse", "socket") and note in other.reason
    w.apply([c for c in plan.values() if c.action != "refuse"])
    monkeypatch.setattr(_socket, "socket", REAL_SOCKET)
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: True)
    plan = w.plan()
    assert plan[("shell", "socket")].action == "change"
    assert plan[("shell", "socket")].paths[-1].name == f"{STEM}-1.socket"
    assert plan[("shell", "daemon")].action == "change"


def test_another_bind_error_keeps_ipv6(w, monkeypatch):
    w.module(shell("127.0.0.1", "::1"))
    monkeypatch.setattr(_plan, "ipv6_loopback", PROBE)
    monkeypatch.setattr(_socket, "socket", _Probe(errno.EACCES))
    assert len(w.plan()[("shell", "socket")].paths) == 2


def test_a_v4_only_plan_and_a_backend_without_sockets_never_probe(w, monkeypatch):
    calls = []
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: calls.append(1) or False)
    w.module(shell("127.0.0.1"))
    w.plan()
    assert calls == []
    w.module(shell("127.0.0.1", "::1"))
    monkeypatch.setattr(w.backend, "sockets", "none", raising=False)
    assert len(w.plan()[("shell", "socket")].paths) == 2
    assert calls == []


# -- status ----------------------------------------------------------------------------------

def states(**kw):
    return {(s.name, s.kind): s for s in units.status(["demo"], **kw)}


def test_an_active_set_reads_active_with_its_epoch_and_gives_the_daemon_its_enabled(w):
    w.module(shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    ids = [w.sup.invocations[f"{STEM}.socket"], w.sup.invocations[f"{STEM}-1.socket"]]
    read = states()
    sock, daemon = read[("shell", "socket")], read[("shell", "daemon")]
    assert list(read) == [("shell", "daemon"), ("shell", "socket")]
    assert sock.state == "active" and sock.enabled is True and sock.pid is None
    assert sock.epoch == f"{STEM}.socket={ids[0]},{STEM}-1.socket={ids[1]}"
    assert sock.log == f"journalctl --user -u {STEM}.socket -u {STEM}-1.socket"
    assert sock.last is None
    assert daemon.enabled is True and daemon.epoch is None
    # a restart of one socket unit changes its id in the epoch
    w.sup.units[f"{STEM}.service"]["active"] = False
    w.sup(["systemctl", "--user", "restart", f"{STEM}-1.socket"])
    assert states()[("shell", "socket")].epoch.endswith(
        f"={w.sup.invocations[f'{STEM}-1.socket']}") and \
        w.sup.invocations[f"{STEM}-1.socket"] != ids[1]


def test_a_failed_or_stopped_unit_gives_no_epoch(w):
    w.module(shell("127.0.0.1", "::1"))
    w.apply(units.plan("demo"))
    w.sup.props[f"{STEM}-1.socket"] = {"ActiveState": "failed"}
    sock = states()[("shell", "socket")]
    assert (sock.state, sock.epoch) == ("failed", None)
    assert sock.detail == f"failed: {STEM}-1.socket"
    del w.sup.props[f"{STEM}-1.socket"]
    w.sup(["systemctl", "--user", "stop", f"{STEM}-1.socket"])
    assert w.sup.invocations[f"{STEM}-1.socket"]  # its last id still shows
    sock = states()[("shell", "socket")]
    assert (sock.state, sock.epoch) == ("inactive", None)
    assert sock.detail == f"not active: {STEM}-1.socket"
    w.sup(["systemctl", "--user", "disable", f"{STEM}-1.socket"])
    read = states()
    assert read[("shell", "socket")].enabled is False
    assert read[("shell", "daemon")].enabled is False


def test_a_socket_with_nothing_written_is_missing_and_off_with_its_daemon(w):
    w.module(shell("127.0.0.1"))
    sock = states()[("shell", "socket")]
    assert (sock.state, sock.epoch, sock.enabled) == ("missing", None, None)
    w.module(shell("127.0.0.1", more=", 'switch': 'shell.enabled'"))
    read = states()
    assert read[("shell", "daemon")].state == read[("shell", "socket")].state == "off"


def test_with_no_backend_the_socket_is_nobackend(w, monkeypatch):
    w.module(shell("127.0.0.1"))
    monkeypatch.setattr(_backend, "implementation", lambda: None)
    read = states()
    assert read[("shell", "socket")].state == "nobackend"


def test_where_sockets_are_skipped_the_socket_is_off_with_no_epoch(w, monkeypatch):
    w.module(shell("127.0.0.1"))
    w.apply(units.plan("demo"))
    monkeypatch.setattr(w.backend, "sockets", "none", raising=False)
    sock = states()[("shell", "socket")]
    assert (sock.state, sock.epoch) == ("off", None)
    assert "skipped on Windows" in sock.detail


def test_where_sockets_are_the_jobs_the_backend_reads_the_daemons_job(w, monkeypatch):
    w.module(shell("127.0.0.1"))
    w.apply(units.plan("demo"))
    monkeypatch.setattr(w.backend, "sockets", "job", raising=False)
    seen = []
    real = w.backend.status

    def status(refs, fence):
        seen.extend(refs)
        return real([r if r.kind != "socket" else UnitRef(r.module, r.name, "daemon",
                                                         r.ids, r.paths) for r in refs],
                    fence)
    monkeypatch.setattr(w.backend, "status", status)
    read = states()
    [sock] = [r for r in seen if r.kind == "socket"]
    assert sock.ids == (f"{STEM}.service",)
    assert read[("shell", "socket")].kind == "socket"


def test_socket_state_reads_the_set_by_the_one_rule():
    ref = UnitRef("demo", "shell", "socket", ("a.socket", "b.socket"))
    up = [("a.socket", "active", True, 5.0, "11"), ("b.socket", "active", True, 7.0, "22")]
    state = socket_state(ref, "systemd", up, "log")
    assert (state.state, state.epoch, state.since, state.enabled) == (
        "active", "a.socket=11,b.socket=22", 7.0, True)
    state = socket_state(ref, "systemd", [up[0], ("b.socket", "active", None, None, "")],
                         None)
    assert (state.state, state.epoch, state.enabled) == ("active", None, None)
    state = socket_state(ref, "systemd", [up[0], ("b.socket", "missing", None, None, "")],
                         None)
    assert (state.state, state.detail) == ("inactive", "not active: b.socket")
    assert socket_state(UnitRef("demo", "shell", "socket", ()), "systemd", [],
                        None).state == "missing"
