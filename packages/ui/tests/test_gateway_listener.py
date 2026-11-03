"""The gateway's listener in process: the hold on both endpoints, the off answer, the
provider check on real connections, the address rule and the mount at each tick, the
signal owner and the idle exit."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from moxi.paths import Layout
from moxi_loopback.api import Table
from moxi_ui import listen
from moxi_ui.gateway import Gateway, Parts, endpoint
from moxi_ui.gateway.address import REFUSED
from moxi_ui.gateway.listener import OFF_BODY, TunnelRead
from moxi_ui.gateway.mount import Mount, read_acts
from moxi_ui.serve import Shell

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX endpoints and /proc")

HOST = "gw.example.test"


@dataclass(frozen=True)
class Address:
    """A stand-in for tunnel's `Address`, with the fields the gateway reads (the tests reach
    no optional module directly)."""

    module: str
    name: str
    reach: str
    provider: str
    exposure: str
    url: str
    host: str
    port: int
    target: int | str
    state: str
    change: str
    applied_at: str | None
    form: str = "http"
    retired: bool = False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class World:
    """What the gateway reads, set by the test."""

    switch: bool = False
    extra: bool = True
    current: bool = True
    address: Any = None
    port: int = field(default_factory=_free_port)
    shell_port: int = 1
    uid: int | None = field(default_factory=os.getuid)
    removed: int = 0
    remove_fails: bool = False

    def settings(self):
        return {"gateway.enabled": self.switch, "gateway.port": self.port,
                "port": self.shell_port}

    def tunnel(self):
        return TunnelRead(self.address, self.current, None if self.current else "too old")

    def remove(self):
        self.removed += 1
        if self.remove_fails:
            raise RuntimeError("the tunnel's lock is held")
        self.address = None


@dataclass
class Hooks:
    """What the gateway's later parts would do, recorded."""

    audit: list = field(default_factory=list)
    ended: int = 0
    stale: list = field(default_factory=list)
    tables: list = field(default_factory=list)

    def parts(self) -> Parts:
        def table(mount):
            self.tables.append(mount)
            built = Table()
            built.add("GET", "/m/ui/api/read")
            return built

        async def router(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-length", b"2")]})
            await send({"type": "http.response.body", "body": b"ok"})

        return Parts(table=table, session=lambda view, entry: True, router=router,
                     end_sessions=self._end, stale=self.stale.append,
                     audit=lambda code, fields: self.audit.append((code, fields)))

    def _end(self):
        self.ended += 1

    def codes(self):
        return [code for code, _ in self.audit]


class Loop:
    """An event loop on a thread, for a gateway that serves while the test connects."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro, timeout=15):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@pytest.fixture
def loop():
    running = Loop()
    yield running
    running.close()


@pytest.fixture
def env(tmp_path_factory):
    """An environment with no Moxi locator, whose runtime directory is short and private."""
    root = tempfile.mkdtemp(prefix="gw", dir=tmp_path_factory.getbasetemp())
    environ = {"HOME": os.path.join(root, "h"), "XDG_RUNTIME_DIR": os.path.join(root, "r")}
    os.makedirs(environ["XDG_RUNTIME_DIR"], mode=0o700)
    Layout("ui").runtime_dir(environ, create=True)
    return environ


def address(world, *, state="on", change="r1.1", host=HOST, port=443, provider="cloudflare",
            form=None, target=None, retired=False, gw=None):
    if form is None:
        form = "unix" if gw is not None and gw.kind == "unix" else "http"
    if target is None:
        target = gw.path if form == "unix" and gw is not None else world.port
    return Address(module="ui", name="gateway", reach="own-devices", provider=provider,
                   exposure="internet", url=f"https://{host}/", host=host, port=port,
                   target=target, state=state, change=change, applied_at=None, form=form,
                   retired=retired)


def _gateway(world, hooks, env, **kwargs) -> Gateway:
    kwargs.setdefault("kind", "tcp")
    kwargs.setdefault("system", "linux")
    gw = Gateway(Layout("ui"), parts=hooks.parts(), settings=world.settings,
                 extra=lambda: world.extra, tunnel=world.tunnel,
                 find_uid=lambda name: world.uid, remove=world.remove, environ=env,
                 tick_s=0.05, **kwargs)
    gw.start()
    return gw


def _request(gw, path="/m/ui/api/read", lines=(), host=HOST):
    head = "\r\n".join([f"GET {path} HTTP/1.1", f"Host: {host}", *lines,
                        "Sec-Fetch-Site: same-origin", "Sec-Fetch-Mode: cors",
                        "Sec-Fetch-Dest: empty", "Connection: close", "", ""]).encode()
    if gw.kind == "unix":
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(10)
        sock.connect(gw.path)
    else:
        sock = socket.create_connection(("127.0.0.1", gw.port), timeout=10)
    with sock:
        sock.sendall(head)
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    status = int(data.split(b" ", 2)[1])
    headers, _, body = data.partition(b"\r\n\r\n")
    return status, headers.decode("latin-1").lower(), body


def _port_free(port) -> bool:
    """Whether another program could bind the port now, as loopback's own bind does (a
    closed connection's TIME_WAIT doesn't count)."""
    try:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


# --- the hold ---------------------------------------------------------------------------


def test_the_tcp_hold_follows_the_switch_and_names_the_shell(env, loop):
    world, hooks = World(), Hooks()
    gw = _gateway(world, hooks, env)
    try:
        loop.run(gw.tick())
        assert not gw.holding and _port_free(world.port)
        world.switch = True
        loop.run(gw.tick())
        assert gw.holding and not _port_free(world.port)
        assert endpoint.hold_port(gw.runtime_dir) == world.port
        # No address: the off answer, a 503 that is never cached, under the gateway policy.
        status, headers, body = _request(gw)
        assert status == 503 and body == OFF_BODY
        assert "cache-control: no-store" in headers and "manifest-src 'self'" in headers
        world.switch = False
        loop.run(gw.tick())
        assert not gw.holding and _port_free(world.port)
        assert endpoint.hold_port(gw.runtime_dir) is None
    finally:
        loop.run(gw.close())


@pytest.mark.parametrize("state", ["applying", "on", "removing"])
def test_an_entry_left_with_the_switch_off_keeps_the_hold_and_the_off_answer(env, loop, state):
    world, hooks = World(), Hooks()
    world.address = address(world, state=state)
    world.extra = False  # The hold doesn't need the extra.
    gw = _gateway(world, hooks, env)
    try:
        loop.run(gw.tick())
        assert gw.holding and _request(gw)[0] == 503
        world.address = None
        loop.run(gw.tick())
        assert not gw.holding and _port_free(world.port)
    finally:
        loop.run(gw.close())


def test_a_tcp_port_another_socket_holds_removes_the_entry_and_writes_a_held_row(env, loop):
    world, hooks = World(switch=True, remove_fails=True), Hooks()
    world.address = address(world)
    squatter = socket.socket()
    squatter.bind(("127.0.0.1", world.port))
    squatter.listen()
    gw = _gateway(world, hooks, env)
    try:
        loop.run(gw.tick())
        assert not gw.holding and world.removed == 1
        assert hooks.codes().count("held") == 1
        assert f"port {world.port} was held by another program" in gw.problem
        loop.run(gw.tick())  # The removal failed, so it is tried again; one row is enough.
        assert world.removed == 2 and hooks.codes().count("held") == 1
        world.remove_fails = False
        loop.run(gw.tick())
        assert world.removed == 3 and world.address is None
        squatter.close()
        loop.run(gw.tick())
        assert gw.holding and gw.problem is None
    finally:
        squatter.close()
        loop.run(gw.close())


def test_a_gateway_port_equal_to_the_shells_takes_no_hold(env, loop):
    world, hooks = World(switch=True), Hooks()
    world.shell_port = world.port
    gw = _gateway(world, hooks, env)
    try:
        loop.run(gw.tick())
        assert not gw.holding and "gateway.port" in gw.problem and world.removed == 0
        assert "held" not in hooks.codes()
    finally:
        loop.run(gw.close())


def test_a_shell_whose_environment_moves_its_runtime_dir_takes_no_hold(env, loop, tmp_path):
    world, hooks = World(switch=True), Hooks()
    world.address = address(world)
    gw = _gateway(world, hooks, {**env, "MOXI_HOME": str(tmp_path / "moved")})
    try:
        assert gw.moved and "MOXI_HOME" in gw.moved
        loop.run(gw.tick())
        assert not gw.holding and gw.profile is None and _port_free(world.port)
    finally:
        loop.run(gw.close())


def test_an_unreadable_record_at_start_holds_by_the_switch_alone(env, loop):
    world, hooks = World(address=REFUSED), Hooks()
    gw = _gateway(world, hooks, env)
    try:
        loop.run(gw.tick())
        assert not gw.holding, "switch off and no read yet: no hold"
        world.switch = True
        loop.run(gw.tick())
        assert gw.holding and gw.profile is None and _request(gw)[0] == 503
        world.switch = False
        loop.run(gw.tick())
        assert not gw.holding
        world.address = address(world)
        loop.run(gw.tick())
        assert gw.holding, "the first good read finds the entry"
    finally:
        loop.run(gw.close())


def test_the_hold_on_a_socket_and_a_path_that_fails_at_a_tick(env, loop, monkeypatch):
    monkeypatch.setattr(endpoint, "check_socket_path", lambda path: None)
    world, hooks = World(switch=True), Hooks()
    gw = _gateway(world, hooks, env, kind="unix")
    world.address = address(world, gw=gw)
    try:
        loop.run(gw.tick())
        assert gw.holding and os.path.exists(gw.path)
        assert not (gw.runtime_dir / endpoint.HOLD_FILE).exists(), "a socket needs no hold file"
        assert _request(gw)[:1] == (200,)

        def refused(path):
            raise endpoint.PathRefused(f"g+w on {os.path.dirname(path)}", os.path.dirname(path))

        monkeypatch.setattr(endpoint, "check_socket_path", refused)
        loop.run(gw.tick())
        assert not gw.holding and not os.path.exists(gw.path)
        assert world.removed == 1 and "held" in hooks.codes()
    finally:
        loop.run(gw.close())


def test_the_idle_exit_never_fires_while_the_hold_is_held(env):
    bound = listen.bind(0)
    held = listen.Listeners(bound.sockets, bound.port, bound.names, inherited=True)
    shell = Shell(held, epoch=None, idle_s=0.01, layout=Layout("ui"),
                  gateway=_gateway(World(), Hooks(), env))
    try:
        time.sleep(0.02)
        assert shell.idle()
        shell.gateway._held = object()
        assert not shell.idle()
    finally:
        shell.gateway._held = None
        held.close()


# --- serving and the provider check ------------------------------------------------------


def _on(world, gw, loop, **kwargs):
    world.switch = True
    world.address = address(world, gw=gw, **kwargs)
    loop.run(gw.tick())
    assert gw.holding and gw.on


def test_the_provider_check_admits_only_the_providers_uid_on_tcp(env, loop):
    world, hooks = World(), Hooks()
    gw = _gateway(world, hooks, env)
    try:
        _on(world, gw, loop)
        assert _request(gw) == (200, _request(gw)[1], b"ok")  # /proc gives this user's uid
        world.uid = os.getuid() + 1  # tailscaled as root, say: this user is refused
        world.address = address(world, change="r1.2")
        loop.run(gw.tick())
        for path, lines in (("/m/ui/api/read", ()), ("/sw.js", ("Service-Worker: script",)),
                            ("/pair", ())):
            assert _request(gw, path, lines)[0] == 403
        assert hooks.codes().count("provider") == 3
        world.uid = None  # The provider's uid can't be found: every request is refused.
        world.address = address(world, change="r1.3")
        loop.run(gw.tick())
        assert _request(gw)[0] == 403
    finally:
        loop.run(gw.close())


def test_the_provider_check_reads_the_sockets_peer(env, loop, monkeypatch):
    monkeypatch.setattr(endpoint, "check_socket_path", lambda path: None)
    world, hooks = World(), Hooks()
    gw = _gateway(world, hooks, env, kind="unix")
    try:
        _on(world, gw, loop)
        assert _request(gw)[0] == 200
        world.uid = 0
        world.address = address(world, gw=gw, change="r1.2")
        loop.run(gw.tick())
        assert _request(gw)[0] == 403 and hooks.audit[-1][0] == "provider"
        # A header that claims a peer changes nothing.
        assert _request(gw, lines=("moxi.peer: 0", "X-Forwarded-For: 127.0.0.1"))[0] == 403
    finally:
        loop.run(gw.close())


@pytest.mark.parametrize("system", ["darwin", "win32"])
def test_macos_and_windows_run_no_provider_check(env, loop, system):
    world, hooks = World(uid=None), Hooks()
    gw = _gateway(world, hooks, env, system=system)
    try:
        _on(world, gw, loop)
        assert _request(gw)[0] == 200
    finally:
        loop.run(gw.close())


def test_the_host_check_follows_the_profile(env, loop):
    world, hooks = World(), Hooks()
    gw = _gateway(world, hooks, env)
    try:
        _on(world, gw, loop)
        assert _request(gw, host="127.0.0.1")[0] == 403
        assert _request(gw, host="GW.Example.Test")[0] == 200
        assert _request(gw, lines=(f"X-Moxi-Token: {'a' * 43}",))[0] == 403
    finally:
        loop.run(gw.close())


# --- the address rule at each tick -------------------------------------------------------


def test_the_address_rule_at_each_tick(env, loop):
    world, hooks = World(switch=True), Hooks()
    gw = _gateway(world, hooks, env)
    try:
        loop.run(gw.tick())
        assert gw.profile is None
        _on(world, gw, loop)
        first = gw.profile
        for state in ("applying", "removing"):
            world.address = address(world, state=state, change="r1.5")
            loop.run(gw.tick())
            assert gw.profile is first and _request(gw)[0] == 200, state
        world.address = None
        loop.run(gw.tick())
        assert gw.profile is first and _request(gw)[0] == 503
        world.address = address(world, change="r2.1")  # A re-add: same host, port, provider.
        loop.run(gw.tick())
        assert gw.profile is first and hooks.ended == 0 and gw.kept.change == "r2.1"
        world.address = address(world, change="r2.2")  # The same, with no None between.
        loop.run(gw.tick())
        assert gw.profile is first and hooks.ended == 0
        world.address = address(world, change="r2.3", port=8443)
        loop.run(gw.tick())
        assert gw.profile is not first and hooks.ended == 1 and hooks.stale == []
        assert _request(gw, host=f"{HOST}:8443")[0] == 200
        world.address = address(world, change="r2.4", port=8443, host="gw2.example.test")
        loop.run(gw.tick())
        assert hooks.ended == 2 and [k.host for k in hooks.stale] == [HOST]
        world.switch = False
        loop.run(gw.tick())
        assert gw.profile is None and hooks.ended == 3 and gw.holding
        assert _request(gw, host="gw2.example.test:8443")[0] == 503
    finally:
        loop.run(gw.close())


def test_a_tunnel_without_interface_2_keeps_the_gateway_off(env, loop):
    world, hooks = World(switch=True, current=False), Hooks()
    world.address = address(world)
    gw = _gateway(world, hooks, env)
    try:
        loop.run(gw.tick())
        assert gw.profile is None and gw.holding and gw.tunnel_reason == "too old"
        assert _request(gw)[0] == 503
    finally:
        loop.run(gw.close())


# --- the mount ---------------------------------------------------------------------------


def test_the_mount_on_a_socket_and_its_reasons_at_each_tick(env, loop, monkeypatch):
    monkeypatch.setattr(endpoint, "check_socket_path", lambda path: None)
    world, hooks = World(), Hooks()
    gw = _gateway(world, hooks, env, kind="unix")
    try:
        _on(world, gw, loop)
        assert gw.mount == Mount(True, None) and hooks.tables[-1] == Mount(True, None)
        assert read_acts(gw.runtime_dir) == {"pid": os.getpid(), "acts": True, "reason": None}
        assert hooks.audit[-1] == ("acts", {"acts": True, "reason": None})
        # Nothing a request carries changes it.
        _request(gw, "/gw/act/prepare", ("Cookie: a=b", "X-Moxi-Key: k"))
        assert gw.acts_now() == gw.mount
        rows = len(hooks.audit)
        world.address = address(world, gw=gw, change="r1.2", target="/elsewhere/gateway.sock")
        loop.run(gw.tick())
        assert gw.mount == Mount(False, "target") and len(hooks.audit) == rows + 1
        assert read_acts(gw.runtime_dir)["reason"] == "target"
        assert hooks.tables[-1] == Mount(False, "target") and hooks.ended == 0
        world.address = address(world, gw=gw, change="r1.3", form="http", target=world.port)
        loop.run(gw.tick())
        assert gw.mount.reason == "target"
        world.address = address(world, gw=gw, change="r1.4", retired=True)
        loop.run(gw.tick())
        assert gw.mount == Mount(False, "origin")
        world.address = address(world, gw=gw, change="r1.5")
        loop.run(gw.tick())
        assert gw.mount.mounted
        # An unreadable record keeps the profile, the sessions and the hold, and unmounts.
        kept = gw.kept
        world.address = REFUSED
        loop.run(gw.tick())
        assert gw.mount == Mount(False, "record") and gw.holding and hooks.ended == 0
        assert gw.profile is not None and gw.kept == kept
        assert _request(gw)[0] == 200 and read_acts(gw.runtime_dir)["reason"] == "record"
        world.address = address(world, gw=gw, change="r1.5")
        loop.run(gw.tick())
        assert gw.mount.mounted, "the next good read mounts them again"
    finally:
        loop.run(gw.close())
    assert read_acts(gw.runtime_dir) is None


def test_on_tcp_the_acts_are_never_mounted(env, loop):
    world, hooks = World(), Hooks()
    gw = _gateway(world, hooks, env)
    try:
        _on(world, gw, loop)
        assert gw.mount == Mount(False, "tcp") and read_acts(gw.runtime_dir)["reason"] == "tcp"
        assert gw.kind == "tcp"
    finally:
        loop.run(gw.close())


# --- the signal owner --------------------------------------------------------------------


@pytest.mark.skipif(threading.current_thread() is not threading.main_thread(),
                    reason="signals need the main thread")
def test_sigterm_stops_the_shell_after_the_gateway_listener_opened_and_closed_twice(env):
    world, hooks = World(), Hooks()
    gw = _gateway(world, hooks, env)
    Layout("ui").runtime_dir(create=True)
    shell = Shell(listen.bind(0), epoch=None, gateway=gw)
    shell.start()
    before = signal.getsignal(signal.SIGTERM)

    async def drive():
        task = asyncio.ensure_future(shell.run())

        async def until(check):
            for _ in range(400):
                if check():
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("timed out")

        await until(lambda: signal.getsignal(signal.SIGTERM) is not before)
        owner = signal.getsignal(signal.SIGTERM)
        for _ in range(2):
            world.switch = True
            await until(lambda: gw.holding)
            assert signal.getsignal(signal.SIGTERM) is owner
            world.switch = False
            await until(lambda: not gw.holding)
            assert signal.getsignal(signal.SIGTERM) is owner
        world.switch = True
        await until(lambda: gw.holding)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, 10)

    try:
        asyncio.run(drive())
        assert not gw.holding and _port_free(world.port)
        assert signal.getsignal(signal.SIGTERM) is before
    finally:
        shell.close()


# --- the tunnel read ---------------------------------------------------------------------


def test_the_tunnel_is_read_through_interface_2_else_1(monkeypatch):
    from types import SimpleNamespace

    from moxi import modules
    from moxi_ui.gateway import listener

    found = {}

    def load(name, *, interface=None):
        assert name == "tunnel"
        return found.get(interface, SimpleNamespace(state=modules.ABSENT, api=None,
                                                     reason=""))

    monkeypatch.setattr(modules, "load", load)
    assert listener.read_tunnel() == TunnelRead(None, False, "tunnel isn't installed")

    def api(value):
        def address(module, name):
            assert (module, name) == ("ui", "gateway")
            if isinstance(value, Exception):
                raise value
            return value

        return SimpleNamespace(address=address)

    found[1] = SimpleNamespace(state=modules.PRESENT, api=api("tcp-entry"), reason="")
    found[2] = SimpleNamespace(state=modules.BROKEN, api=None,
                               reason="moxi-tunnel 0.1.0 offers interfaces {1}; the caller speaks 2")
    read = listener.read_tunnel()
    assert read.address == "tcp-entry" and not read.current and "offers interfaces" in read.reason
    found[2] = SimpleNamespace(state=modules.PRESENT, api=api(RuntimeError("bad record")),
                               reason="")
    assert listener.read_tunnel() == TunnelRead(REFUSED, True, "bad record")
