"""The control socket: served from the daemon's start, `rpc.hello` first, `status`, the
probe's nonces, more methods by name, and HEALTH against it."""

from __future__ import annotations

import asyncio

import pytest
from kit import Fake, Running, echo_tool, http, provider

from moxi_connector import checks
from moxi_connector.origin import Origin
from moxi_connector.provider import Gate

ORIGIN = Origin("door.example.net")


def started_host(make_host, **kw):
    return asyncio.run(make_host(**kw))


def test_health_fails_with_no_daemon_and_passes_once_it_serves(make_host):
    row = checks.health()
    assert row.state == "fail" and "doesn't answer" in row.detail
    with Running(started_host(make_host), ORIGIN):
        row = checks.health()
    assert row.state == "ok", row
    assert ORIGIN.url in row.detail
    assert checks.health().state == "fail"


def test_status_tells_the_origin_the_port_and_the_providers(make_host, fake):
    gate = Gate(peer=lambda p: None, shell=lambda env: None)
    host = started_host(make_host, providers={
        "demo": provider(fake, echo_tool(fake), gate=gate),
        "other": provider(Fake(), echo_tool(Fake()), rows=())})
    with Running(host, Origin("door.example.net", 8443)) as d:
        got = checks.ask("status")
        assert got["url"] == "https://door.example.net:8443"
        assert got["public_port"] == 8443
        assert "known to reach only ports 443" in got["port_warning"]
        assert got["port"] == d.port
        assert {p["module"]: p["state"] for p in got["providers"]} == {
            "demo": "started", "other": "started"}
        assert got["clashes"] == {"echo": ["demo", "other"]}
        assert got["tools"] == []
        assert ["demo bound", "ok", "the fake is up"] in got["rows"]
        assert ["tool echo", "fail", "declared by demo and other; served by neither"] \
            in got["rows"]
        assert got["gates"] == ["demo"]


def test_status_with_no_origin_names_none(make_host):
    with Running(started_host(make_host), None):
        got = checks.ask("status")
    assert got["url"] is None and got["port_warning"] is None


def test_a_call_before_the_hello_is_refused(make_host):
    from moxi import ipc

    with Running(started_host(make_host), ORIGIN):
        with ipc.connect(ipc.address("connector", "control"), timeout=2) as client:
            with pytest.raises(ipc.RpcError) as got:
                client.call("status", None, timeout=2)
    assert got.value.code == -32002


def test_an_unknown_method_is_method_not_found(make_host):
    from moxi import ipc

    with Running(started_host(make_host), ORIGIN):
        with pytest.raises(ipc.RpcError) as got:
            checks.ask("grants")
    assert got.value.code == -32601


def test_more_methods_join_by_name_and_see_the_peer(make_host):
    import os

    seen = []

    async def mint_code(peer, params):
        seen.append((peer.pid, peer.uid, params))
        return {"code": "not-a-real-one"}

    with Running(started_host(make_host), ORIGIN, methods={"mint_code": mint_code}) as d:
        assert checks.ask("mint_code", {"label": "phone"}) == {"code": "not-a-real-one"}
        with pytest.raises(ValueError):
            d.daemon.control.register("status", mint_code)
    assert seen == [(os.getpid(), os.getuid(), {"label": "phone"})]


def test_the_probe_sees_only_a_nonce_it_registered(make_host):
    with Running(started_host(make_host), ORIGIN) as d:
        nonce = checks.ask("probe_expect")["nonce"]
        assert checks.ask("probe_seen", {"nonce": nonce}) == {"seen": False}
        # A value nobody registered is ignored, and can't be asked about after.
        http(d.port, "GET", "/anything", headers={"X-Moxi-Probe": "planted"})
        assert checks.ask("probe_seen", {"nonce": "planted"}) == {"seen": False}
        # The registered one is seen even on a request the Host gate refuses: the
        # sighting says the request reached this process, nothing more.
        status, _, _ = http(d.port, "GET", "/.well-known/oauth-protected-resource/mcp",
                            host="elsewhere.example", headers={"X-Moxi-Probe": nonce})
        assert status == 421
        assert checks.ask("probe_seen", {"nonce": nonce}) == {"seen": True}


def test_probe_seen_refuses_bad_params(make_host):
    from moxi import ipc

    with Running(started_host(make_host), ORIGIN):
        with pytest.raises(ipc.RpcError) as got:
            checks.ask("probe_seen", {"nonce": 5})
    assert got.value.code == -32602


def test_the_probe_keeps_four_nonces_for_a_minute():
    from moxi_connector.probe import Probes

    now = [0.0]
    probes = Probes(clock=lambda: now[0])
    first = probes.expect()
    rest = [probes.expect() for _ in range(4)]
    assert not probes.saw(first)                # the fifth dropped the oldest
    assert probes.saw(rest[0]) and probes.seen(rest[0])
    now[0] = 61.0
    assert not probes.seen(rest[0])             # past its minute
    assert not probes.saw(rest[1])
    assert not probes.saw(None) and not probes.saw("")


def test_the_control_socket_closes_with_the_daemon(make_host):
    from moxi import ipc

    with Running(started_host(make_host), ORIGIN):
        pass
    with pytest.raises(ipc.IpcError):
        checks.ask("status", wait=0.5)
