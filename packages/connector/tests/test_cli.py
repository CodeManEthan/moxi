"""`moxi connector`: on, off (revoking before the switch goes false), status, check with
its nonce, and log."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from kit import Admit, Running, echo_tool, http, provider

from moxi_connector import calllog, cli
from moxi_connector import origin as origins
from moxi_connector.origin import PRM_PATH, Origin
from moxi_connector.probe import Reply
from moxi_connector.signin import Mount

O = Origin("door.example.net")


@pytest.fixture
def applied(monkeypatch):
    """The core's unit writer, stood in: each (module, key) it was asked to apply."""
    import moxi.config

    got = []
    monkeypatch.setattr(moxi.config, "apply_units",
                        lambda module, key, **kw: got.append((module, key)) or [])
    return got


@pytest.fixture
def no_tunnel(monkeypatch):
    monkeypatch.setattr(origins, "tunnel_address", lambda: None)


def enabled() -> bool:
    from moxi_connector import settings

    return settings.enabled()


# -- on, off ------------------------------------------------------------------------------

def test_on_sets_the_switch_and_says_to_add_the_tunnel_entry(config_file, applied, no_tunnel,
                                                           capsys):
    config_file("")
    assert cli.main(["on"]) == 0
    assert enabled() and applied == [("connector", "enabled")]
    out = capsys.readouterr().out
    assert "moxi tunnel add connector/mcp" in out


def test_on_with_an_address_prints_the_url_to_paste(config_file, applied, no_tunnel, capsys):
    config_file('public_url = "https://door.example.net:8443"\n')
    assert cli.main(["on"]) == 0
    out = capsys.readouterr().out
    assert "https://door.example.net:8443/mcp" in out
    assert "Add custom connector" in out
    assert "known to reach only ports 443" in out


def test_off_revokes_every_grant_before_the_switch_goes_false(config_file, applied,
                                                              monkeypatch, capsys):
    config_file("enabled = true\n")
    order = []
    monkeypatch.setattr(origins, "tunnel_address", lambda: SimpleNamespace(
        state="on", exposure="internet", host="door.example.net", port=443,
        provider="tailscale"))

    def revoke():
        order.append(("revoke", enabled()))
        return "Every grant is revoked."

    monkeypatch.setattr(cli, "revoke_every_grant", revoke)
    applied_at_set = applied
    assert cli.main(["off"]) == 0
    assert order == [("revoke", True)]
    assert applied_at_set == [("connector", "enabled")] and not enabled()
    out = capsys.readouterr().out
    assert out.index("Every grant is revoked.") < out.index("The connector is off.")
    assert "stays until moxi tunnel remove connector/mcp" in out


def test_off_turns_off_even_when_the_revoke_fails(config_file, applied, no_tunnel,
                                                  monkeypatch, capsys):
    config_file("enabled = true\n")

    def revoke():
        raise OSError("the grants file is locked")

    monkeypatch.setattr(cli, "revoke_every_grant", revoke)
    assert cli.main(["off"]) == 1
    assert not enabled()
    err = capsys.readouterr().err
    assert "couldn't be revoked" in err and "moxi connector revoke --all" in err


def test_the_off_hook_is_a_no_op_until_the_sign_in_fills_it():
    assert cli.revoke_every_grant() is None


# -- status -------------------------------------------------------------------------------

def test_status_while_off(config_file, no_tunnel, capsys):
    config_file("")
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "The connector is off" in out and "loopback port: 8452" in out
    assert "warn" not in out and "fail" not in out


def test_status_while_on_with_no_process_names_the_failures(config_file, no_tunnel, capsys):
    config_file("enabled = true\n")
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "process: doesn't answer" in out
    assert "fail: process:" in out and "fail: address:" in out


def test_status_json_of_a_running_process(make_host, fake, config_file, no_tunnel, capsys):
    config_file('enabled = true\npublic_url = "https://door.example.net"\n')
    host = asyncio.run(make_host())
    with Running(host, O) as d:
        http(d.port, "POST", "/mcp", body=b"{}")
        host.calllog.flush()
        calllog.CallLog().write(host.calllog.rows[-1], durable=False, collapse=None)
        assert cli.main(["status", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["enabled"] is True and got["url"] == "https://door.example.net/mcp"
    assert got["process"]["port"] == d.port
    assert got["process"]["tools"] == [{"name": "echo", "provider": "demo"}]
    assert got["last_call"]["status"] == 401
    names = {r["name"]: r["state"] for r in got["rows"]}
    assert names["process"] == "ok" and names["mint gate"] == "fail"


def test_status_text_lists_providers_tools_and_warnings(make_host, fake, config_file,
                                                         no_tunnel, capsys):
    config_file('enabled = true\npublic_url = "https://door.example.net"\n')
    with Running(asyncio.run(make_host()), O):
        assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "URL: https://door.example.net/mcp" in out
    assert "provider demo: started" in out and "tools: echo" in out
    assert "demo bound: ok: the fake is up" in out
    assert "warn: edge:" in out and "fail: mint gate:" in out


# -- check --------------------------------------------------------------------------------

class Metadata(Admit):
    """The stand-in sign-in, serving the protected-resource document too."""

    def mount(self, origin):
        base = super().mount(origin)

        async def prm(scope, receive, send):
            body = json.dumps({"resource": origin.resource}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        return Mount(routes={**base.routes, PRM_PATH: prm}, guard=base.guard)


def loopback(port: int):
    """A fetch that takes the public URL to the door on loopback, as the edge would."""
    def fetch(method, url, headers, body):
        parts = urlsplit(url)
        status, got, data = http(port, method, parts.path, host=parts.netloc,
                                 headers=headers, body=body)
        return Reply(status, got, data)
    return fetch


def test_check_passes_when_the_probe_reaches_this_door(make_host, config_file, no_tunnel,
                                                       capsys):
    config_file('enabled = true\npublic_url = "https://door.example.net"\n')
    host = asyncio.run(make_host(signin=Metadata()))
    with Running(host, O) as d:
        assert cli._check(SimpleNamespace(json=True), __import__("sys").stdout,
                          fetch=loopback(d.port)) == 0
    cap = capsys.readouterr()
    assert cap.err == ""
    got = json.loads(cap.out)
    assert got["passed"] and [s["name"] for s in got["steps"]] == [
        "register", "reach", "unsigned"]
    row = calllog.last({"check"})
    assert row["passed"] is True and row["failed"] is None
    assert row["steps"] == {"register": True, "reach": True, "unsigned": True}


def test_check_fails_when_an_edge_answers_in_the_doors_place(make_host, config_file,
                                                             no_tunnel, capsys):
    config_file('enabled = true\npublic_url = "https://door.example.net"\n')
    host = asyncio.run(make_host(signin=Metadata()))

    def edge(method, url, headers, body):
        # An edge page that copies the document, but never forwards the request.
        if method == "GET":
            return Reply(200, {}, json.dumps({"resource": O.resource}).encode())
        return Reply(403, {}, b"blocked")

    with Running(host, O):
        assert cli._check(SimpleNamespace(json=False), __import__("sys").stdout,
                          fetch=edge) == 1
    out = capsys.readouterr().out
    assert "never reached this connector" in out and "The check failed." in out
    assert calllog.last({"check"})["failed"] == "reach"


def test_check_before_the_metadata_is_served_says_what_answered(make_host, config_file,
                                                                no_tunnel, capsys):
    config_file('enabled = true\npublic_url = "https://door.example.net"\n')
    with Running(asyncio.run(make_host()), O) as d:
        assert cli._check(SimpleNamespace(json=False), __import__("sys").stdout,
                          fetch=loopback(d.port)) == 1
    out = capsys.readouterr().out
    assert "reached this connector and answered 404" in out
    assert "pass: POST https://door.example.net/mcp without a token answered 401" in out


def test_check_is_refused_while_off_or_with_no_process(config_file, no_tunnel, capsys):
    config_file("")
    assert cli.main(["check"]) == 3
    assert "the connector is off" in capsys.readouterr().err
    config_file("enabled = true\n")
    assert cli.main(["check", "--json"]) == 3
    got = json.loads(capsys.readouterr().out)
    assert "doesn't answer" in got["error"] and got["fix"] == "moxi install connector"


def test_check_notes_the_tailnet_through_tailscale(make_host, config_file, monkeypatch,
                                                   capsys):
    config_file("enabled = true\n")
    monkeypatch.setattr(origins, "tunnel_address", lambda: SimpleNamespace(
        state="on", exposure="internet", host="door.example.net", port=443,
        provider="tailscale"))
    host = asyncio.run(make_host(signin=Metadata()))
    with Running(host, O) as d:
        assert cli._check(SimpleNamespace(json=False), __import__("sys").stdout,
                          fetch=loopback(d.port)) == 0
    assert "the real test is a phone off the tailnet" in capsys.readouterr().out


# -- log ----------------------------------------------------------------------------------

def test_log_prints_the_rows_of_the_days_asked(config_file, capsys):
    log = calllog.CallLog()
    calllog.event(log, "register", client="c1")
    log.write({"at": calllog.now(), "kind": "request", "status": 401, "grant": None},
              durable=False, collapse="401")
    assert cli.main(["log"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("register client=c1")
    assert lines[1].endswith("request status=401")
    assert cli.main(["log", "--days", "3", "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["kind"] for r in rows] == ["register", "request"]
    assert cli.main(["log", "--days", "0"]) == 3


def test_an_unknown_verb_is_usage():
    assert cli.main(["frobnicate"]) == 2
