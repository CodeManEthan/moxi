"""The doctor's rows, each state: quiet while off but for the address warn, the port row
against REACHED_PORTS, the edge with the last check, the providers and the mint gate."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from kit import Running, echo_tool, provider

from moxi_connector import doctor
from moxi_connector.doctor import Grants, Snapshot
from moxi_connector.origin import REACHED_PORTS, Origin
from moxi_connector.provider import Gate

O = Origin("door.example.net")


def tunnel_entry(state="on", provider="tailscale", port=443, exposure="internet"):
    return SimpleNamespace(state=state, exposure=exposure, host="door.example.net",
                           port=port, provider=provider)


ANSWER = {"pid": 4242, "url": O.url,
          "providers": [{"module": "comms", "state": "started", "why": None,
                         "tools": ["agents", "send", "check_messages", "send_and_wait"]}],
          "clashes": {}, "gates": ["comms"],
          "tools": [{"name": "agents", "provider": "comms"}],
          "rows": [["comms identity", "ok", "claude-app is bound"]]}


def snap(**kw) -> Snapshot:
    base = dict(switch=True, port=8452, public_url="", address=tunnel_entry(), origin=O,
                answer=ANSWER, error=None, last_check=None, last_call=None, grants=None)
    base.update(kw)
    return Snapshot(**base)


def by_name(rows) -> dict:
    return {r.name: r for r in rows}


# -- while off ----------------------------------------------------------------------------

@pytest.mark.parametrize("address", [None, tunnel_entry(state="off"),
                                     tunnel_entry(state="pending")])
def test_off_shows_only_the_switch(address):
    rows = doctor.build(snap(switch=False, address=address, origin=None, answer=None,
                             error="the connector is off",
                             grants=Grants(3, stale=1)))
    assert [(r.name, r.state) for r in rows] == [("switch", "off")]


def test_off_with_a_tunnel_entry_on_warns_on_the_address_alone():
    rows = doctor.build(snap(switch=False, answer=None, error="the connector is off"))
    assert [(r.name, r.state) for r in rows] == [("switch", "off"), ("address", "warn")]
    assert rows[1].detail == doctor.ADDRESS_FORWARDS
    assert rows[1].fix == "moxi tunnel remove connector/mcp"


# -- while on -----------------------------------------------------------------------------

def test_on_and_well_every_row_is_ok():
    rows = doctor.build(snap(grants=Grants(2)))
    states = {r.name: r.state for r in rows}
    assert states == {"switch": "ok", "process": "ok", "address": "ok", "port": "ok",
                      "edge": "ok", "providers": "ok", "mint gate": "ok",
                      "comms identity": "ok", "grants": "ok"}
    got = by_name(rows)
    assert "pid 4242" in got["process"].detail
    assert got["address"].detail == "https://door.example.net, from tunnel"
    assert got["providers"].detail == "comms, 4 tools"
    assert got["grants"].detail == "2 grants"


def test_no_answer_while_on_fails_the_process_and_hides_the_providers():
    rows = by_name(doctor.build(snap(answer=None, error="nothing serves the socket")))
    assert rows["process"].state == "fail"
    assert "nothing serves the socket" in rows["process"].detail
    assert rows["process"].fix == "moxi install connector"
    assert not {"providers", "mint gate", "comms identity"} & set(rows)


def test_no_address_while_on_fails():
    rows = by_name(doctor.build(snap(origin=None, address=None)))
    assert rows["address"].state == "fail" and "503" in rows["address"].detail
    assert rows["address"].fix == "moxi tunnel add connector/mcp"
    assert "port" not in rows and "edge" not in rows


def test_a_typed_url_names_its_source():
    rows = by_name(doctor.build(snap(public_url="https://door.example.net", address=None)))
    assert rows["address"].detail.endswith("from public_url")


@pytest.mark.parametrize("port", sorted(REACHED_PORTS) + [8443, 10000, 8452])
def test_the_port_row_against_the_reached_ports(port):
    row = by_name(doctor.build(snap(origin=Origin("door.example.net", port))))["port"]
    if port in REACHED_PORTS:
        assert row.state == "ok"
    else:
        assert row.state == "warn"
        assert "known to reach only ports 443" in row.detail
        assert "Claude may say it couldn't reach the server" in row.detail


def test_the_edge_warns_off_tailscale_with_the_last_check():
    cf = snap(address=tunnel_entry(provider="cloudflare"))
    row = by_name(doctor.build(cf))["edge"]
    assert row.state == "warn" and row.detail.startswith("Cloudflare.")
    assert doctor.EDGE_WARN in row.detail and "No check has run." in row.detail
    failed = {"kind": "check", "at": "2026-11-04T10:44:00.000Z", "passed": False,
              "failed": "reach"}
    row = by_name(doctor.build(snap(public_url="https://door.example.net", address=None,
                                    last_check=failed)))["edge"]
    assert row.detail.startswith("a typed URL.")
    assert "failed at its reach step" in row.detail
    passed = {**failed, "passed": True, "failed": None}
    row = by_name(doctor.build(snap(address=tunnel_entry(provider="cloudflare"),
                                    last_check=passed)))["edge"]
    assert "passed" in row.detail


def test_no_provider_warns_and_no_gate_fails():
    answer = {**ANSWER, "providers": [], "gates": [], "rows": []}
    rows = by_name(doctor.build(snap(answer=answer)))
    assert rows["providers"].state == "warn" and rows["providers"].detail == doctor.NO_PROVIDER
    assert rows["mint gate"].state == "fail" and rows["mint gate"].detail == doctor.NO_GATE


def test_a_failed_start_or_a_clash_fails_the_providers_row_once():
    answer = {**ANSWER,
              "providers": [*ANSWER["providers"],
                            {"module": "notes", "state": "failed",
                             "why": "its start failed: RuntimeError", "tools": ["read"]}],
              "clashes": {"send": ["comms", "other"]},
              "rows": [["notes", "fail", "its start failed: RuntimeError"],
                       ["tool send", "fail", "declared by comms and other; served by neither"],
                       ["comms identity", "fail", "claude-app is held by pid 7"]]}
    rows = doctor.build(snap(answer=answer))
    got = by_name(rows)
    assert got["providers"].state == "fail"
    assert "notes: its start failed" in got["providers"].detail
    assert "the tool send is declared by comms and other" in got["providers"].detail
    # The provider's own row passes through; the failed entry and the clash don't repeat.
    assert got["comms identity"].state == "fail"
    assert "notes" not in got and "tool send" not in got


def test_a_stale_grant_warns_with_the_readd_text():
    row = by_name(doctor.build(snap(grants=Grants(3, stale=1))))["grants"]
    assert row.state == "warn" and doctor.STALE in row.detail


# -- the whole run, against a real process ------------------------------------------------

def test_rows_read_the_running_process(make_host, fake, config_file, monkeypatch):
    from moxi_connector import origin as origins

    config_file("enabled = true\n")
    monkeypatch.setattr(origins, "tunnel_address", lambda: tunnel_entry())
    gate = Gate(peer=lambda p: None, shell=lambda env: None)
    host = asyncio.run(make_host(providers={"demo": provider(fake, echo_tool(fake),
                                                              gate=gate)}))
    with Running(host, O):
        rows = by_name(doctor.rows())
    assert {n: r.state for n, r in rows.items()} == {
        "switch": "ok", "process": "ok", "address": "ok", "port": "ok", "edge": "ok",
        "providers": "ok", "mint gate": "ok", "demo bound": "ok"}
    assert rows["providers"].detail == "demo, 1 tool"
    rows = by_name(doctor.rows())
    assert rows["process"].state == "fail"


def test_rows_while_off_ask_nothing(config_file, monkeypatch):
    config_file("")
    asked = []
    snap_ = doctor.take(ask=lambda *a: asked.append(a), tunnel=lambda: None)
    assert asked == [] and snap_.answer is None
    assert [(r.name, r.state) for r in doctor.build(snap_)] == [("switch", "off")]
