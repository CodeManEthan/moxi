"""The host against comms' provider, as the workspace installs both: comms' four tools and
its mint gate loaded through `moxi.connector.providers`, over comms' own in-process test
daemon.

These are the checks neither package can make alone. The host's code imports nothing of
comms, and comms reaches the host only through `moxi.modules.load`. This test owns both
sides: it takes comms' provider from the entry point, as the daemon does, and it takes
comms' test rig (its `rig` fixture and the fixtures that put comms' roots under tmp) from
comms' test kit, loaded by path at run time. The fakes of the gate's process walk are set
on comms' modules by their dotted names. Outside a workspace checkout with comms' tests
beside this package, the module skips.

The rig runs on comms' `local` record: the provider loaded from the entry point sends
through comms' send path with no record handed in, which opens the configured record, the
`comms.db` under the test's comms home, where the rig's daemon reads it.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from conftest import ORIGIN
from kit import CLAUDE, HTTPX, Running, call, http, initialize, request, rpc, wait_for
from signin_kit import CALLBACK, CALLBACKS, Clock, Door, pending_of, pkce, query_of

from moxi import ipc, modules
from moxi_connector import checks, cli, codes, doctor, grants
from moxi_connector.app import Front, Host, build_app
from moxi_connector.calllog import MemoryLog
from moxi_connector.codes import mint_method
from moxi_connector.oauth import OAuth

COMMS_TESTS = Path(__file__).resolve().parents[2] / "comms" / "tests"

if not modules.load("comms").ok or not (COMMS_TESTS / "conftest.py").is_file():
    pytest.skip("comms and its tests aren't beside this package; the joins run in the "
                "workspace", allow_module_level=True)


def _comms_rig():
    """comms' test kit, its `conftest.py`, as a module of its own name."""
    spec = importlib.util.spec_from_file_location("comms_test_rig",
                                                  COMMS_TESTS / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RIG = _comms_rig()
# comms' fixtures, for this module: its roots under tmp, its reserve step scoped, its
# socket unit read as unknown, every thread a test starts ended at its teardown, the rig.
env, installs, unit_read, threads_end, rig = (RIG.env, RIG.installs, RIG.unit_read,
                                              RIG.threads_end, RIG.rig)


@pytest.fixture
def backend():
    """The rig's record: comms' `local` one, which the provider's sends open too."""
    return "local"


def comms(name: str):
    """One of comms' modules, by its name under `moxi_comms`."""
    return importlib.import_module(f"moxi_comms.{name}")


ME = "claude-app"
NAMES = ["agents", "send", "check_messages", "send_and_wait"]
CODE_SHAPE = re.compile(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}")
PID = os.getpid()
SHELL_VARS = ("MOXI_COMMS_OPERATOR", "MOXI_COMMS_NAME", "MOXI_COMMS_CLAIM_SOCKET")


def kinds(rows, kind):
    return [r for r in rows if r.get("kind") == kind]


# -- the host over the providers installed ----------------------------------------------------

@pytest.fixture
def registry(rig):
    """The providers this environment installs, loaded from `moxi.connector.providers` and
    started on the test's roots, comms' among them; stopped at the end."""
    from moxi_connector.providers import Registry

    found = Registry.from_group()
    asyncio.run(found.start(dict(os.environ)))
    yield found
    asyncio.run(found.stop())


def comms_entry(registry):
    [entry] = [e for e in registry.entries if e.module == "comms"]
    return entry


def bound(registry):
    """Wait for comms' identity to be bound, as its own status row says."""
    wait_for(lambda: any(r.name == "comms identity" and r.level == "ok"
                         for r in registry.status_rows()))


def pending() -> list[str]:
    """The ids comms' provider holds pushed and unread for its identity."""
    return [str(p["message_id"]) for p in comms("connector").COMMS.link.pending()]


def unread(rig) -> set[str]:
    """The ids the rig's daemon handed comms' identity and has not had acked: pushed,
    held in a digest not yet settled, or put back after a failed write."""
    b = rig.daemon.bindings.get(ME)
    return set() if b is None else {str(i) for i in b.delivered - b.handled}


def post(rig, text, *, author="planner@proj-a"):
    return str(rig.post_as(author, [ME], text).id)


@pytest.fixture
def running(registry):
    """`with running as d`: a daemon on loopback over the installed providers, with the real
    sign-in and `mint_code` on its control socket."""
    host = Host(registry, calllog=MemoryLog())
    oauth = OAuth(grants.Store(), host.calllog, redirect_uris=CALLBACKS)
    host.signin, host.redirect_uris = oauth, CALLBACKS
    return Running(host, ORIGIN, methods={"mint_code": mint_method(oauth, registry)})


@pytest.fixture
def door(registry):
    """`await door()`: the door in process over the installed providers, with the real
    sign-in. A test closes it (`await d.close()`)."""

    async def make():
        host = Host(registry, calllog=MemoryLog())
        clock = Clock()
        oauth = OAuth(grants.Store(), host.calllog, redirect_uris=CALLBACKS, clock=clock)
        host.signin, host.redirect_uris = oauth, CALLBACKS
        app = build_app(ORIGIN, host)
        await app.start()
        return Door(Front(app, host), oauth, clock)

    return make


# -- comms' process walk, faked ----------------------------------------------------------------

#: The fake table's other processes, numbered from this one's so none is this one: a
#: terminal, a session's harness, and a parent that can't be read.
TERMINAL, HARNESS, GONE = PID + 1, PID + 2, PID + 3
#: The one live claim: a session whose harness is HARNESS, as comms' gate tests have it.
SESSION = [("claude-planner", {"roots": [{"pid": HARNESS, "start": "s-harness",
                                          "reach": "tree"}]})]
#: Where this process sits in the fake table: under a terminal, under the session's
#: harness, or under a parent the walk can't read.
WALKS = {
    "outside": {1: (0, "s1"), TERMINAL: (1, "s-terminal"), PID: (TERMINAL, "s-me")},
    "inside": {1: (0, "s1"), HARNESS: (1, "s-harness"), PID: (HARNESS, "s-me")},
    "unplaced": {1: (0, "s1"), PID: (GONE, "s-me")},
}


@pytest.fixture
def walk(monkeypatch):
    """`walk(where)`: comms' gate walks a fake process table in which this process sits
    `where`, against the one live claim `SESSION`."""
    view = comms("membership").ProcView

    class Table(view):
        def __init__(self, rows):
            super().__init__(root="")
            self.rows = rows

        def stat(self, pid):
            return self.rows.get(int(pid))

    def place(where):
        monkeypatch.setattr("moxi_comms.membership.DEFAULT_PROC", Table(WALKS[where]))
        monkeypatch.setattr("moxi_comms.claimfile.live_records",
                            lambda proc=None: list(SESSION))

    return place


@pytest.fixture
def shell(monkeypatch, config_file):
    """`shell(**env)`: the switch on, and the verb's shell carrying exactly `env` of comms'
    stamps."""

    def make(**stamps):
        config_file("enabled = true\n")
        for key in SHELL_VARS:
            monkeypatch.delenv(key, raising=False)
        for key, value in stamps.items():
            monkeypatch.setenv(key, value)

    return make


def verb(capsys, label="phone"):
    code = cli.main(["code", "--label", label])
    out = capsys.readouterr()
    return code, out.out, out.err


# -- the mint gate: comms' gate on the control socket and in the verb ------------------------

def test_the_registry_loads_comms_with_its_gate_from_the_entry_point(registry):
    entry = comms_entry(registry)
    assert entry.state == "started" and entry.distribution == "moxi-comms"
    gates = dict(registry.gates())
    assert gates["comms"] is entry.provider.gate
    gate = comms("connector.gate")
    assert (gates["comms"].peer, gates["comms"].shell) == (gate.peer, gate.shell)
    assert list(registry.tools()) == NAMES


@pytest.mark.parametrize("where", ["inside", "unplaced"])
def test_comms_gate_refuses_a_mint_from_inside_a_session_or_from_where_it_cant_tell(
        running, walk, where):
    walk(where)
    with running as d:
        with pytest.raises(ipc.RpcError) as e:
            checks.ask("mint_code", {"label": "phone"})
        assert e.value.code == codes.REFUSED
        assert e.value.message == comms("connector.gate").PEER_REFUSED
        assert e.value.data == {"check": "peer", "module": "comms"}
        assert d.host.signin.codes.count() == 0
    [row] = kinds(d.host.calllog.rows, "code-refused")
    assert (row["check"], row["module"], row["pid"]) == ("peer", "comms", PID)
    assert kinds(d.host.calllog.rows, "code-minted") == []


def test_comms_gate_lets_a_mint_through_from_outside_every_session(running, walk):
    walk("outside")
    with running as d:
        got = checks.ask("mint_code", {"label": "desk"})
        assert CODE_SHAPE.fullmatch(got["code"]) and got["label"] == "desk"
        assert d.host.signin.codes.count() == 1
    [row] = kinds(d.host.calllog.rows, "code-minted")
    assert row["pid"] == PID and got["code"] not in json.dumps(d.host.calllog.rows)


def test_the_verb_mints_in_the_operators_own_shell_outside_every_session(
        running, walk, shell, capsys):
    walk("outside")
    shell(MOXI_COMMS_OPERATOR="900001")
    with running as d:
        code, out, err = verb(capsys)
        assert code == 0, err
        assert CODE_SHAPE.fullmatch(out.splitlines()[0])
        assert d.host.signin.codes.count() == 1


def test_the_verb_passes_on_comms_socket_refusal_for_a_shell_inside_a_session(
        running, walk, shell, capsys):
    """The shell check is early honesty; the socket's walk is the gate. A shell that looks
    like the operator's but sits in a session's tree is refused by the walk."""
    walk("inside")
    shell(MOXI_COMMS_OPERATOR="900001")
    with running as d:
        code, out, err = verb(capsys)
        assert code == cli.NOPERM and out == ""
        assert comms("connector.gate").PEER_REFUSED in err
        assert d.host.signin.codes.count() == 0


@pytest.mark.parametrize("stamps, says", [
    ({"MOXI_COMMS_OPERATOR": "900001", "MOXI_COMMS_NAME": "planner"},
     "This shell is inside the agent session planner (MOXI_COMMS_NAME or a claim socket "
     "is set)."),
    ({"MOXI_COMMS_OPERATOR": "900001", "MOXI_COMMS_CLAIM_SOCKET": "/nowhere/claim.sock"},
     "This shell is inside the agent session that holds this shell's claim socket "
     "(MOXI_COMMS_NAME or a claim socket is set)."),
    ({}, "This shell carries no operator stamp: MOXI_COMMS_OPERATOR is not set."),
], ids=["session-name", "claim-socket", "no-operator"])
def test_the_verb_refuses_a_shell_comms_gate_refuses_naming_the_check(
        running, walk, shell, capsys, stamps, says):
    walk("outside")
    shell(**stamps)
    with running as d:
        code, out, err = verb(capsys)
        assert code == cli.NOPERM and out == ""
        assert says in err
        assert d.host.signin.codes.count() == 0
    # Refused before the socket was asked: the daemon wrote no refusal of its own.
    assert kinds(d.host.calllog.rows, "code-refused") == []


# -- the whole sign-in, then comms' tools through /mcp ------------------------------------------

def mcp_over(port: int, access: str, body: dict) -> dict:
    """One `POST /mcp` to the door on loopback as Claude-User: its JSON-RPC result."""
    status, _, raw = http(port, "POST", "/mcp", headers={
        "User-Agent": CLAUDE, "Authorization": f"Bearer {access}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"}, body=json.dumps(body).encode())
    assert status == 200, raw
    return json.loads(raw)["result"]


def sign_in_over(port: int) -> str:
    """The whole sign-in on loopback: registration, the page, a code from the control
    socket, the form, the 303 and `/token`. Its access token."""
    status, _, body = http(port, "POST", "/register", headers={
        "User-Agent": HTTPX, "Content-Type": "application/json"},
        body=json.dumps({"redirect_uris": [CALLBACK], "client_name": "Claude",
                         "token_endpoint_auth_method": "none"}).encode())
    assert status == 201, body
    client = json.loads(body)
    verifier, challenge = pkce()
    query = urlencode({"client_id": client["client_id"], "redirect_uri": CALLBACK,
                       "response_type": "code", "code_challenge": challenge,
                       "code_challenge_method": "S256", "state": "s-1"})
    status, _, body = http(port, "GET", "/authorize?" + query)
    assert status == 200, body
    typed = checks.ask("mint_code", {"label": "phone"})["code"]
    form = urlencode({"pending": pending_of(body), "code": typed}).encode()
    status, heads, body = http(port, "POST", "/authorize", body=form, headers={
        "Origin": ORIGIN.page_origin, "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/x-www-form-urlencoded"})
    assert status == 303, body
    back = query_of(heads["location"])
    assert back["state"] == "s-1" and back["iss"] == ORIGIN.issuer
    started = time.perf_counter()
    status, _, body = http(port, "POST", "/token", headers={
        "User-Agent": HTTPX, "Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode({"grant_type": "authorization_code", "code": back["code"],
                        "redirect_uri": CALLBACK, "client_id": client["client_id"],
                        "code_verifier": verifier}).encode())
    assert time.perf_counter() - started < 1.0
    assert status == 200, body
    return json.loads(body)["access_token"]


def test_the_full_sign_in_then_comms_tools_reach_the_daemon(running, walk, registry, rig):
    shim = rig.shim(name="planner", scope="proj-a", project="proj-a")
    bound(registry)
    walk("outside")
    with running as d:
        status, heads, _ = http(d.port, "POST", "/mcp", body=json.dumps(initialize()).encode(),
                                headers={"User-Agent": CLAUDE,
                                         "Content-Type": "application/json"})
        assert status == 401 and "resource_metadata=" in heads["www-authenticate"]
        access = sign_in_over(d.port)
        assert "tools" in mcp_over(d.port, access, initialize())["capabilities"]

        # tools/list: comms' four tools, each with its title and truthful annotations.
        listed = mcp_over(d.port, access, rpc("tools/list", rid=2))["tools"]
        assert [t["name"] for t in listed] == NAMES
        hints = {t["name"]: t["annotations"] for t in listed}
        assert {n: h["readOnlyHint"] for n, h in hints.items()} == {
            "agents": True, "send": False, "check_messages": False, "send_and_wait": False}
        assert all(h["destructiveHint"] is False and h["openWorldHint"] is False
                   for h in hints.values())
        assert all(t["title"] and t["inputSchema"]["type"] == "object" for t in listed)

        # agents: who can be messaged, as the rig's daemon knows them.
        got = mcp_over(d.port, access, call("agents", rid=3))
        assert not got["isError"], got
        names = {r["name"] for r in got["structuredContent"]["agents"]}
        assert {"planner@proj-a", ME} <= names

        # send: posted as the connector's identity and delivered to the planner now.
        got = mcp_over(d.port, access, call("send", {
            "to": "planner@proj-a", "text": "from the phone", "posture": "none"}, rid=4))
        assert not got["isError"], got
        sent = got["structuredContent"]
        assert [(r["name"], r["delivery"]) for r in sent["recipients"]] == [
            ("planner@proj-a", "live")]
        assert "planner@proj-a: delivered now" in got["content"][0]["text"]
        mid = sent["message_id"]
        RIG.wait_for_payload(shim, mid)
        assert rig.record.get_message(mid).author_id == ME

        # check_messages: read means written. The answer reaches the client, the door
        # runs comms' settle with True, and the daemon has nothing unread for the name.
        ids = [post(rig, f"m{i}") for i in range(2)]
        wait_for(lambda: pending() == ids)
        wait_for(lambda: unread(rig) == set(ids))
        got = mcp_over(d.port, access, call("check_messages", rid=5))
        assert [m["id"] for m in got["structuredContent"]["messages"]] == ids
        wait_for(lambda: not unread(rig))
        got = mcp_over(d.port, access, call("check_messages", rid=6))
        assert got["structuredContent"]["messages"] == []
        assert got["content"][0]["text"] == "No new messages."
    calls = [r for r in d.host.calllog.rows if r.get("kind") == "request"
             and r.get("route") == "mcp" and r.get("tool")]
    assert {r["tool"] for r in calls} == {"agents", "send", "check_messages"}
    assert all(r["user_agent"] == CLAUDE for r in calls if "user_agent" in r)


def test_a_failed_write_leaves_comms_messages_unread(door, registry, rig):
    bound(registry)
    ids = [post(rig, f"m{i}") for i in range(2)]
    wait_for(lambda: pending() == ids)

    async def main():
        d = await door()
        _, tokens = await d.page_sign_in()
        access = [("authorization", f"Bearer {tokens['access_token']}")]
        # The client went away before the final body: comms' settle runs with False.
        await request(d.front, "POST", "/mcp", call("check_messages"), headers=access,
                      send_fails=True)
        assert unread(rig) == set(ids)
        deadline = time.monotonic() + 5
        while pending() != ids:
            assert time.monotonic() < deadline, pending()
            await asyncio.sleep(0.01)
        got = await request(d.front, "POST", "/mcp", call("check_messages"), headers=access)
        result = got.json()["result"]
        assert [m["id"] for m in result["structuredContent"]["messages"]] == ids
        await d.close()

    asyncio.run(main())
    wait_for(lambda: not unread(rig))


# -- the host's own checks, against comms' provider --------------------------------------------

def test_arguments_outside_comms_schema_never_reach_comms(door, registry, rig):
    bound(registry)
    ids = [post(rig, "m0")]
    wait_for(lambda: pending() == ids)

    async def main():
        d = await door()
        _, tokens = await d.page_sign_in()
        access = [("authorization", f"Bearer {tokens['access_token']}")]
        for name, args in (("check_messages", {"limit": 99}),
                           ("check_messages", {"limit": 0}),
                           ("check_messages", {"since": "yesterday"}),
                           ("send", {"to": "planner@proj-a"}),
                           ("send_and_wait", {"to": "planner@proj-a", "text": "x",
                                              "wait_seconds": 500})):
            got = await request(d.front, "POST", "/mcp", call(name, args), headers=access)
            result = got.json()["result"]
            assert result["isError"], (name, args)
            assert result["structuredContent"]["refusal"] == "invalid-arguments", (name, args)
        await d.close()
        return d

    d = asyncio.run(main())
    # Nothing was read or sent: the message is still pushed and unread, no tool ran.
    assert pending() == ids
    assert unread(rig) == set(ids)
    assert not [r for r in d.host.calllog.rows if r.get("kind") == "tool-error"]


def test_the_doctor_names_comms_provider_its_gate_and_its_identity(
        running, registry, config_file, monkeypatch):
    from moxi_connector import origin as origins

    config_file("enabled = true\n")
    monkeypatch.setattr(origins, "tunnel_address", lambda: SimpleNamespace(
        state="on", exposure="internet", host=ORIGIN.host, port=443, provider="tailscale"))
    bound(registry)
    with running:
        rows = {r.name: r for r in doctor.rows()}
    assert (rows["providers"].state, rows["providers"].detail) == ("ok", "comms, 4 tools")
    assert (rows["mint gate"].state, rows["mint gate"].detail) == ("ok", "from comms")
    identity = rows["comms identity"]
    assert identity.state == "ok" and ME in identity.detail
