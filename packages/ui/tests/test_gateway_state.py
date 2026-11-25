"""The desk's read of the gateway for the Settings page's phone group, the pairing code's
end time, and the shell's export, which marks none of the settings or gateway routes as
the phone's."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from gateway_kit import windows_profile_env
from moxi.paths import Layout
from moxi_ui import page, settings
from moxi_ui.gateway import Gateway, desk, pairing
from moxi_ui.gateway.address import REFUSED
from moxi_ui.gateway.listener import Reading, TunnelRead
from moxi_ui.gateway.mount import Mount
from moxi_ui.loopback.routes import Out, export, project


@dataclass(frozen=True)
class Address:
    """A stand-in for tunnel's `Address`."""

    host: str
    port: int
    provider: str
    change: str
    target: Any
    reach: str = "public"
    state: str = "on"
    form: str = "http"
    retired: bool = False

    @property
    def exposure(self) -> str:
        if self.provider == "tailscale" and self.reach == "own-devices":
            return "tailnet"
        return "internet"


@pytest.fixture
def env(tmp_path_factory):
    root = tempfile.mkdtemp(prefix="gw", dir=tmp_path_factory.getbasetemp())
    shell_env = {"HOME": os.path.join(root, "h"), "XDG_RUNTIME_DIR": os.path.join(root, "r"),
                 "XDG_STATE_HOME": os.path.join(root, "s"),
                 **windows_profile_env(os.path.join(root, "h"))}
    os.makedirs(shell_env["XDG_RUNTIME_DIR"], mode=0o700)
    Layout("ui").runtime_dir(shell_env, create=True)
    return shell_env


def _gateway(env, *, extra=True):
    return Gateway(Layout("ui"), kind="tcp", system="linux",
                   settings=lambda: {"gateway.enabled": True, "gateway.port": 8461, "port": 1},
                   extra=lambda: extra, tunnel=lambda: TunnelRead(None, current=True),
                   find_uid=lambda name: os.getuid(), remove=lambda: None, env=env)


def _read(gw, *, switch=True, extra=True, address=None, current=True, reason=None):
    gw.apply(Reading(switch, extra, TunnelRead(address, current=current, reason=reason)))
    return _state(gw)


def _state(gw):
    shell = SimpleNamespace(gateway=gw)
    token = page._shell.set(shell)
    try:
        answer = asyncio.run(desk.state(SimpleNamespace(body={}, query={})))
    finally:
        page._shell.reset(token)
    project(answer, Out("table", items=desk.STATE_SHAPE), strict=True)
    return answer


def test_with_no_gateway_nothing_is_installed_and_every_field_is_off_or_null():
    assert _state(None) == {"installed": False, "switch": False, "on": False, "host": None,
                            "port": None, "exposure": None, "reads_only": False,
                            "problem": None}


def test_without_the_extra_the_gateway_is_not_installed_before_and_after_a_reading(env):
    gw = _gateway(env, extra=False)
    assert _state(gw)["installed"] is False
    assert _read(gw, extra=False)["installed"] is False
    assert _state(_gateway(env))["installed"] is True


def test_the_switch_off_says_no_problem(env):
    gw = _gateway(env)
    state = _read(gw, switch=False, address=Address("gw.example.com", 443, "cloudflare",
                                                    "r1.1", 8461))
    assert state["switch"] is False and state["on"] is False and state["problem"] is None
    # The address is still the one read.
    assert state["host"] == "gw.example.com" and state["port"] == 443


def test_on_with_an_address_serves_it_and_reads_only_while_no_act_is_mounted(env):
    gw = _gateway(env)
    state = _read(gw, address=Address("gw.example.com", 443, "cloudflare", "r1.1", 8461))
    assert state == {"installed": True, "switch": True, "on": True, "host": "gw.example.com",
                     "port": 443, "exposure": "internet", "reads_only": True,
                     "problem": None}
    gw.mount = Mount(mounted=True, reason=None)
    assert _state(gw)["reads_only"] is False


def test_the_exposure_is_who_can_reach_the_address_not_the_provider(env):
    gw = _gateway(env)
    own = Address("pc.tail.ts.net", 443, "tailscale", "r1.1", 8461, reach="own-devices")
    assert _read(gw, address=own)["exposure"] == "tailnet"
    public = Address("pc.tail.ts.net", 443, "tailscale", "r1.2", 8461, reach="public")
    assert _read(gw, address=public)["exposure"] == "internet"


def test_on_without_an_address_says_why_and_the_reason_may_be_none(env):
    gw = _gateway(env)
    state = _read(gw, address=None, current=False, reason="tunnel isn't installed")
    assert state["on"] is False and state["host"] is None and state["port"] is None
    assert state["problem"] == "tunnel isn't installed"
    state = _read(gw, address=None)
    assert state["on"] is False and state["problem"] is None
    # The moved runtime directory comes first.
    gw.moved = "XDG_RUNTIME_DIR moves the runtime directory"
    assert _state(gw)["problem"] == gw.moved


def test_a_record_that_cant_be_read_is_no_address(env):
    gw = _gateway(env)
    _read(gw, address=Address("gw.example.com", 443, "cloudflare", "r1.1", 8461))
    state = _read(gw, address=REFUSED, reason="the record is not JSON")
    assert state["host"] is None and state["port"] is None and state["exposure"] is None


def test_pairing_answers_when_its_code_ends(tmp_path):
    gateway = SimpleNamespace(moved=None, runtime_dir=str(tmp_path),
                              address=Address("gw.example.com", 443, "cloudflare", "r1.1",
                                              8461))
    shell = SimpleNamespace(gateway=gateway)
    token = page._shell.set(shell)
    try:
        before = time.time()
        answer = asyncio.run(desk.pair(SimpleNamespace(body={}, query={})))
    finally:
        page._shell.reset(token)
    assert answer["url"].startswith("https://gw.example.com/pair?n=")
    assert before + pairing.PAIR_TTL <= answer["until"] <= time.time() + pairing.PAIR_TTL


def test_the_new_routes_are_the_desks_and_never_the_gateways():
    routes = {route.path: route for route in page.PAGE.routes}
    assert routes["/api/gateway/state"].kind == "read"
    assert routes["/api/settings/save"].kind == "act"
    assert routes["/api/gateway/pair"].returns["until"].type == "float"
    exported = {route["path"]: route for route in export(page.PAGE)["routes"]}
    for route in (*settings.ROUTES, *desk.ROUTES):
        assert route.gateway is False and exported[route.path]["gateway"] is False
