"""Pairing and sign-in through the gateway's real listener, on its TCP endpoint in process:
the provider check before them, the 2 s tick ending sessions of revoked and stale devices,
the address rule ending sessions, a pairing code minted while no shell runs, and the
desktop's gateway routes."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("webauthn")
pytest.importorskip("cryptography")

from gateway_kit import HOST, ORIGIN, Authenticator  # noqa: E402
from moxi.paths import Layout  # noqa: E402
from moxi_ui.loopback.api import Refused, Table  # noqa: E402
from moxi_ui import page  # noqa: E402
from moxi_ui.gateway import Gateway, Parts, audit, desk, pairing  # noqa: E402
from moxi_ui.gateway.access import COOKIE, Access  # noqa: E402
from moxi_ui.gateway.devices import Devices  # noqa: E402
from moxi_ui.gateway.listener import TunnelRead  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the provider check's /proc")


@dataclass(frozen=True)
class Address:
    """A stand-in for tunnel's `Address` (the tests reach no optional module directly)."""

    host: str
    port: int
    provider: str
    change: str
    target: Any
    state: str = "on"
    form: str = "http"
    retired: bool = False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class World:
    port: int = field(default_factory=_free_port)
    address: Any = None
    audit: list = field(default_factory=list)

    def on(self, host=HOST, change="r1.1", port=443):
        self.address = Address(host, port, "cloudflare", change, self.port)

    def settings(self):
        return {"gateway.enabled": True, "gateway.port": self.port, "port": 1}

    def tunnel(self):
        return TunnelRead(self.address, True)


async def _route(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-length", b"5")]})
    await send({"type": "http.response.body", "body": b"route"})


class Loop:
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
def env(tmp_path_factory):
    root = tempfile.mkdtemp(prefix="gw", dir=tmp_path_factory.getbasetemp())
    environ = {"HOME": os.path.join(root, "h"), "XDG_RUNTIME_DIR": os.path.join(root, "r"),
               "XDG_STATE_HOME": os.path.join(root, "s")}
    os.makedirs(environ["XDG_RUNTIME_DIR"], mode=0o700)
    Layout("ui").runtime_dir(environ, create=True)
    return environ


@pytest.fixture
def served(env):
    """A gateway on its TCP endpoint with the sign-in layer plugged in, held and on."""
    world, loop = World(), Loop()
    world.on()
    layout = Layout("ui")
    holder: dict[str, Gateway] = {}
    access = Access(state_dir=layout.state_dir(env), runtime_dir=layout.runtime_dir(env),
                    kept=lambda: holder["gw"].kept, audit=lambda c, f: world.audit.append(c),
                    pages={"pair.html": b"<!doctype html>"}.get)

    def table(mount):
        built = Table()
        built.add("GET", "/m/fixture/api/read", "read")
        return built

    gw = Gateway(layout, parts=access.parts(Parts(table=table, router=_route)), kind="tcp",
                 system="linux", settings=world.settings, extra=lambda: True,
                 tunnel=world.tunnel, find_uid=lambda name: os.getuid(),
                 remove=lambda: None, environ=env, tick_s=0.05)
    holder["gw"] = gw
    gw.start()
    loop.run(gw.tick())
    assert gw.holding and gw.on
    yield SimpleNamespace(world=world, gw=gw, access=access, loop=loop, env=env)
    loop.run(gw.close())
    loop.close()


class Client:
    """The phone's browser, over a real connection to the gateway's port."""

    def __init__(self, port: int):
        self.port = port
        self.cookie: str | None = None
        self.key: str | None = None

    def send(self, method: str, path: str, body: Any = None, host: str = HOST):
        data = b"" if body is None else json.dumps(body).encode()
        lines = [f"{method} {path} HTTP/1.1", f"Host: {host}", "Sec-Fetch-Site: same-origin",
                 "Connection: close", f"Content-Length: {len(data)}"]
        if method != "GET":
            lines += [f"Origin: {ORIGIN}", "Content-Type: application/json"]
        if self.cookie:
            lines.append(f"Cookie: {COOKIE}={self.cookie}")
        if self.key:
            lines.append(f"X-Moxi-Key: {self.key}")
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall("\r\n".join([*lines, "", ""]).encode() + data)
            raw = b""
            while chunk := sock.recv(65536):
                raw += chunk
        head, _, payload = raw.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        return status, head.decode("latin-1"), payload

    def keep(self, head: str, payload: bytes) -> None:
        cookie = next(line for line in head.split("\r\n")
                      if line.lower().startswith("set-cookie:"))
        self.cookie = cookie.split(":", 1)[1].strip().split(";", 1)[0].split("=", 1)[1]
        self.key = json.loads(payload)["key"]

    def pair(self, url: str, authenticator: Authenticator | None) -> int:
        """Pair from the URL the desktop shows: the code is its fragment."""
        code = url.split("#c=", 1)[1]
        status, _, payload = self.send("POST", "/api/pair/begin",
                                       {"code": code, "name": "Pixel"})
        assert status == 200, payload
        data = json.loads(payload)
        credential = authenticator.create(data["options"]) if authenticator else None
        status, head, payload = self.send("POST", "/api/pair/finish",
                                          {"pairing": data["pairing"],
                                           "credential": credential})
        if status == 200:
            self.keep(head, payload)
        return status

    def sign_in(self, authenticator: Authenticator) -> int:
        status, _, payload = self.send("POST", "/api/signin/begin", {})
        assert status == 200
        assertion = authenticator.get(json.loads(payload)["options"])
        status, head, payload = self.send("POST", "/api/signin/finish",
                                          {"credential": assertion})
        if status == 200:
            self.keep(head, payload)
        return status


def test_pairing_and_signing_in_through_the_listener(served):
    phone, authenticator = Client(served.world.port), Authenticator()
    url = pairing.mint(served.gw.runtime_dir, served.world.address)
    assert phone.pair(url, authenticator) == 200
    assert phone.send("GET", "/m/fixture/api/read")[0] == 200
    phone.cookie = phone.key = None
    assert phone.sign_in(authenticator) == 200
    assert phone.send("GET", "/m/fixture/api/read")[0] == 200
    # The provider check runs first: a request from a uid that isn't the provider's is a
    # 403 before any sign-in route.
    served.gw.uid = os.getuid() + 1
    assert phone.send("POST", "/api/signin/begin", {})[0] == 403
    assert served.world.audit[-1] == "provider"


def test_a_code_minted_while_no_shell_runs_is_redeemed_by_the_started_shell(env):
    layout, world = Layout("ui"), World()
    world.on()
    url = pairing.mint(layout.runtime_dir(env), world.address)
    code = url.split("#c=", 1)[1]
    access = Access(state_dir=layout.state_dir(env), runtime_dir=layout.runtime_dir(env),
                    kept=lambda: None)
    assert access.tickets.redeem(code) and not access.tickets.redeem(code)


def test_a_revoke_from_the_cli_ends_the_sessions_within_one_tick(served):
    phone = Client(served.world.port)
    phone.pair(pairing.mint(served.gw.runtime_dir, served.world.address), None)
    (device,) = served.access.devices.all()
    Devices.of(Layout("ui").state_dir(served.env)).revoke(device.id)
    served.loop.run(served.gw.tick())
    assert phone.send("GET", "/m/fixture/api/read")[0] == 403


def test_a_new_host_ends_the_sessions_and_its_devices_go_stale(served):
    phone = Client(served.world.port)
    phone.pair(pairing.mint(served.gw.runtime_dir, served.world.address), None)
    served.world.on(change="r1.2")  # The same host, port and provider: kept.
    served.loop.run(served.gw.tick())
    assert phone.send("GET", "/m/fixture/api/read")[0] == 200
    served.world.on(host="gw2.example.test", change="r2.1")
    served.loop.run(served.gw.tick())
    (device,) = served.access.devices.all()
    assert device.stale and "mark-stale" in served.world.audit
    assert phone.send("GET", "/m/fixture/api/read", host="gw2.example.test")[0] == 403


def test_a_new_port_ends_the_sessions_and_marks_no_device_stale(served):
    phone = Client(served.world.port)
    phone.pair(pairing.mint(served.gw.runtime_dir, served.world.address), None)
    served.world.on(change="r1.2", port=8443)
    served.loop.run(served.gw.tick())
    (device,) = served.access.devices.all()
    assert not device.stale and served.access.store.live() == 0


# --- the desktop's routes ----------------------------------------------------------------


def _at_desk(shell, handler, body=None):
    token = page._shell.set(shell)
    try:
        return asyncio.run(handler(SimpleNamespace(body=body or {}, query={})))
    finally:
        page._shell.reset(token)


def test_the_desktop_mints_a_pairing_url_lists_and_revokes_devices(served):
    shell = SimpleNamespace(gateway=served.gw, phone=served.access, layout=Layout("ui"))
    url = _at_desk(shell, desk.pair)["url"]
    assert url.startswith(f"https://{HOST}/pair?n=")
    phone = Client(served.world.port)
    assert phone.pair(url, None) == 200
    (row,) = _at_desk(shell, desk.devices)["devices"]
    assert row["name"] == "Pixel" and row["passkey"] is False and row["stale"] is False
    assert _at_desk(shell, desk.revoke, {"id": row["id"]}) == {"ok": True}
    assert phone.send("GET", "/m/fixture/api/read")[0] == 403
    with pytest.raises(Refused, match="no device"):
        _at_desk(shell, desk.revoke, {"id": row["id"]})


def test_the_desktop_pairing_refuses_with_no_address_or_no_gateway(served):
    served.gw.address = None
    shell = SimpleNamespace(gateway=served.gw, phone=served.access, layout=Layout("ui"))
    with pytest.raises(Refused, match="moxi ui gateway enable"):
        _at_desk(shell, desk.pair)
    with pytest.raises(Refused, match="no gateway"):
        _at_desk(SimpleNamespace(gateway=None, phone=None), desk.pair)


def test_the_desktop_routes_are_the_shells_and_never_the_gateways():
    routes = {route.path: route for route in page.PAGE.routes}
    for path in ("/api/gateway/pair", "/api/gateway/devices", "/api/gateway/revoke",
                 "/api/gateway/log"):
        assert routes[path].gateway is False
    assert routes["/api/gateway/pair"].kind == "act"
    assert routes["/api/gateway/devices"].kind == "read"
    assert routes["/api/gateway/log"].kind == "read"


def test_the_desktop_log_shows_the_last_rows_and_each_devices_sign_ins(tmp_path):
    now = time.time()
    old = audit.Audit(tmp_path, clock=lambda: now - 3 * 86400)
    old("signin", {"device": "d-old", "session": "s" * 12})
    old.close()
    stamps = iter(now - 100 + i for i in range(100))
    writer = audit.Audit(tmp_path, clock=lambda: next(stamps))
    for _ in range(12):
        writer("signin", {"device": "d1", "session": "a" * 12})
    writer("signin", {"device": "d2", "session": "b" * 12})
    for i in range(50):
        writer.request({"id": f"r{i}", "method": "GET", "path": "/m/ui/api/read",
                        "status": 200, "device": "d1"}, signed_in=True)
    writer.close()
    shell = SimpleNamespace(layout=SimpleNamespace(state_dir=lambda: tmp_path))
    got = _at_desk(shell, desk.log)
    rows = got["rows"]
    assert len(rows) == audit.DESK_ROWS
    assert rows[0]["kind"] == "request" and "id=r49" in rows[0]["detail"]
    assert rows[-1]["detail"].startswith("device=d1 id=r0 method=GET")
    assert rows[0]["at"] > rows[-1]["at"] and rows[0]["device"] == "d1"
    signins = {entry["device"]: entry["at"] for entry in got["signins"]}
    assert set(signins) == {"d1", "d2"}, "a sign-in older than the days read isn't shown"
    assert len(signins["d1"]) == audit.DESK_SIGNINS and signins["d1"] == sorted(
        signins["d1"], reverse=True)
    assert len(signins["d2"]) == 1
    # The shape the route declares keeps every field the handler gives.
    route = {r.path: r for r in page.PAGE.routes}["/api/gateway/log"]
    assert set(route.returns) == set(got) == {"rows", "signins"}
    assert set(route.returns["rows"].items.items) == set(rows[0])
