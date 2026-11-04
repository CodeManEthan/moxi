"""The door from outside: routes, the Host gate, transport, and the swap of the whole app
when the origin changes. Driven by the fake Claude client in `kit`."""

from __future__ import annotations

import asyncio

import pytest
from conftest import ORIGIN
from kit import (CLAUDE, HOST, HTTPX, Admit, bearer, call, initialize, modern, request,
                 rpc)

from moxi_connector.app import NO_ORIGIN, Front, build_app
from moxi_connector.daemon import Daemon
from moxi_connector.origin import Origin

OTHER = Origin("other.example.net", 8443)


def run(coro):
    return asyncio.run(coro)


# -- routes ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/mcp/", "/MCP", "/favicon.ico", "/authorize/x",
                                  "/.well-known/openid-configuration", "/state/"])
def test_any_other_path_is_an_empty_404(front, path):
    async def main():
        f = await front()
        for method in ("GET", "POST", "DELETE"):
            got = await request(f, method, path, None)
            assert (got.status, got.body) == (404, b""), (method, path)
        await f.close()
    run(main())


def test_get_and_any_other_method_on_mcp_is_405_allow_post(front):
    async def main():
        f = await front()
        for method in ("GET", "DELETE", "PUT"):
            got = await request(f, method, "/mcp", None, headers=bearer())
            assert got.status == 405 and got.header("allow") == "POST"
        await f.close()
    run(main())


def test_a_path_the_sign_in_mounts_is_served_by_it_alone(front):
    async def main():
        f = await front()
        got = await request(f, "GET", "/state")
        assert got.status == 200 and got.json()["origin"] == "https://door.example.net"
        await f.close()
    run(main())


def test_with_no_origin_every_request_is_503_with_the_reason(front):
    async def main():
        f = await front(origin=None)
        for path in ("/mcp", "/state", "/.well-known/oauth-authorization-server", "/x"):
            got = await request(f, "POST", path, initialize(), headers=bearer(), host="any")
            assert (got.status, got.body) == (503, NO_ORIGIN)
        await f.close()
    run(main())


def test_the_closed_sign_in_answers_401_with_the_challenge(make_host):
    from moxi_connector.signin import Closed

    async def main():
        host = await make_host(signin=Closed())
        app = build_app(ORIGIN, host)
        await app.start()
        f = Front(app, host)
        got = await request(f, "POST", "/mcp", initialize(), headers=bearer())
        assert got.status == 401
        assert got.header("www-authenticate") == ORIGIN.challenge()
        assert host.calllog.written[-1][2] == "mcp-401"
        await f.close()
    run(main())


# -- transport ------------------------------------------------------------------------

def test_initialize_first_then_the_tools_with_claudes_user_agent(front):
    async def main():
        f = await front()
        got = await request(f, "POST", "/mcp", initialize(), headers=bearer())
        assert got.status == 200 and got.header("content-type").startswith("application/json")
        assert "tools" in got.json()["result"]["capabilities"]
        tools = (await request(f, "POST", "/mcp", rpc("tools/list", rid=2),
                               headers=bearer())).json()["result"]["tools"]
        assert [t["name"] for t in tools] == ["echo"]
        assert tools[0]["annotations"] == {"readOnlyHint": True, "idempotentHint": True}
        for method, key in (("prompts/list", "prompts"), ("resources/list", "resources")):
            got = await request(f, "POST", "/mcp", rpc(method, rid=4), headers=bearer())
            assert got.json()["result"][key] == []
        got = await request(f, "POST", "/mcp", call("echo", {"say": "hi"}), headers=bearer())
        result = got.json()["result"]
        assert result["content"][0]["text"] == "echo hi"
        assert result["structuredContent"] == {"said": "hi"} and not result["isError"]
        await f.close()
    run(main())


def test_server_discover_first_then_the_tools(front):
    async def main():
        f = await front()
        body, headers = modern("server/discover")
        got = await request(f, "POST", "/mcp", body, headers=bearer() + headers)
        assert got.status == 200 and "tools" in got.json()["result"]["capabilities"]
        body, headers = modern("tools/list", rid=8)
        got = await request(f, "POST", "/mcp", body, headers=bearer() + headers)
        assert [t["name"] for t in got.json()["result"]["tools"]] == ["echo"]
        body, headers = modern("tools/call", rid=9, name="echo", arguments={"say": "yo"})
        got = await request(f, "POST", "/mcp", body, headers=bearer() + headers)
        assert got.json()["result"]["content"][0]["text"] == "echo yo"
        await f.close()
    run(main())


def test_subscriptions_listen_is_not_served(front):
    async def main():
        f = await front()
        body, headers = modern("subscriptions/listen", notifications={"toolsListChanged": True})
        got = await request(f, "POST", "/mcp", body, headers=bearer() + headers)
        assert got.json()["error"]["code"] == -32601
        await f.close()
    run(main())


def test_a_body_over_256_kb_is_refused(front):
    async def main():
        f = await front()
        big = call("echo", {"say": "x" * (256 * 1024)})
        got = await request(f, "POST", "/mcp", big, headers=bearer())
        assert got.status == 413
        await f.close()
    run(main())


# -- the Host gate --------------------------------------------------------------------

@pytest.mark.parametrize("host", ["evil.example.net", "127.0.0.1:8452", "localhost:8452",
                                  "door.example.net:8443", None])
def test_a_foreign_or_missing_host_is_421_on_every_route(front, host):
    async def main():
        f = await front()
        for path in ("/mcp", "/state", "/nowhere"):
            got = await request(f, "POST", path, initialize(), host=host, headers=bearer())
            assert got.status == 421, (host, path)
        await f.close()
    run(main())


@pytest.mark.parametrize("headers", [
    [("x-forwarded-host", HOST)], [("forwarded", f"host={HOST};proto=https")],
    [("x-forwarded-for", "203.0.113.9"), ("x-forwarded-proto", "https")],
    [("tailscale-user-login", "someone@example.com"), ("tailscale-funnel-request", "?1")],
])
def test_forwarded_headers_change_nothing(front, headers):
    async def main():
        f = await front()
        foreign = await request(f, "POST", "/mcp", initialize(), host="evil.example.net",
                                headers=bearer() + headers)
        assert foreign.status == 421
        own = await request(f, "POST", "/mcp", initialize(), headers=headers)
        assert own.status == 401
        await f.close()
    run(main())


@pytest.mark.parametrize("host", ["DOOR.Example.Net", "door.example.net:443"])
@pytest.mark.parametrize("agent", [CLAUDE, HTTPX, None])
def test_the_own_host_in_any_case_passes_for_every_user_agent_claude_sends(front, host, agent):
    async def main():
        f = await front()
        got = await request(f, "POST", "/mcp", initialize(), host=host, user_agent=agent,
                            headers=bearer())
        assert got.status == 200
        await f.close()
    run(main())


def test_the_sdk_allows_the_origins_host_and_origin_alone(front):
    async def main():
        f = await front()
        assert f.app.security.allowed_hosts == ["door.example.net", "door.example.net:443"]
        got = await request(f, "POST", "/mcp", initialize(),
                            headers=bearer() + [("origin", "https://evil.example.net")])
        assert got.status == 403
        got = await request(f, "POST", "/mcp", initialize(),
                            headers=bearer() + [("origin", ORIGIN.url)])
        assert got.status == 200
        await f.close()
    run(main())


def test_the_log_keeps_forwarded_for_only_as_an_untrusted_hint(front):
    async def main():
        f = await front()
        await request(f, "POST", "/mcp", initialize(),
                      headers=bearer() + [("x-forwarded-for", "203.0.113.9")])
        row = f.host.calllog.rows[-1]
        assert row["forwarded_for_untrusted"] == "203.0.113.9"
        assert (row["route"], row["rpc"], row["user_agent"]) == ("mcp", "initialize", CLAUDE)
        await f.close()
    run(main())


# -- the swap -------------------------------------------------------------------------

class Reads:
    """The origin each read of the daemon finds, in turn."""

    def __init__(self, *found):
        self.found = list(found)

    def __call__(self):
        return self.found.pop(0) if len(self.found) > 1 else self.found[0]


def _daemon(host, reads):
    import socket
    sock = socket.socket()  # never served: the tests drive the front directly
    d = Daemon(sock, host, read_origin=reads, switch=lambda: True, env={})
    return d


async def _start_without_listening(d):
    from moxi_connector import origin as origins

    await d.host.registry.start(d.env)
    found = d.read_origin()
    d.watch = origins.Watch(found)
    app = build_app(found, d.host)
    await app.start()
    d.front = Front(app, d.host)


def test_a_change_of_origin_swaps_every_derived_value_at_once(make_host):
    async def main():
        signin = Admit()
        host = await make_host(signin=signin)
        d = _daemon(host, Reads(ORIGIN, OTHER))
        await _start_without_listening(d)
        first = d.front.app
        assert (await request(d.front, "GET", "/state")).json()["mount"] == 0
        await d.tick()
        app = d.front.app
        assert app is not first and app.origin == OTHER
        assert signin.mounts == [ORIGIN, OTHER]
        # The Host gate, the SDK's allowed host and Origin, the challenge, the state the
        # sign-in made for the old origin: every one is the new origin's now.
        assert (await request(d.front, "GET", "/state")).status == 421
        got = await request(d.front, "GET", "/state", host="other.example.net:8443")
        assert got.json() == {"mount": 1, "origin": "https://other.example.net:8443"}
        assert app.security.allowed_hosts == list(OTHER.allowed_hosts)
        assert app.security.allowed_origins == [OTHER.url]
        got = await request(d.front, "POST", "/mcp", initialize(), host="other.example.net")
        assert got.header("www-authenticate") == OTHER.challenge()
        event = [r for r in host.calllog.rows if r["kind"] == "origin-changed"]
        assert event == [{**event[0], "old": ORIGIN.url, "new": OTHER.url}]
        assert host.calllog.flushes == 1
        await d.front.close()
    run(main())


@pytest.fixture
def broken_manager(monkeypatch):
    """`broken_manager()`: every app built from then on has an MCP session manager that
    fails at its start; `broken_manager(False)` mends it."""
    import contextlib

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    from moxi_connector import app as app_mod

    class Broken(StreamableHTTPSessionManager):
        @contextlib.asynccontextmanager
        async def run(self):
            raise RuntimeError("the session manager can't start")
            yield  # pragma: no cover

    def set_(broken=True):
        monkeypatch.setattr(app_mod, "StreamableHTTPSessionManager",
                            Broken if broken else StreamableHTTPSessionManager)

    return set_


def test_a_swap_whose_new_app_fails_to_start_leaves_the_sign_in_and_is_tried_again(
        make_host, clock, broken_manager):
    from signin_kit import CALLBACK, CALLBACKS, Door, pkce

    from moxi_connector import grants
    from moxi_connector.oauth import OAuth

    async def settled(oauth):
        await asyncio.sleep(0)
        for task in list(oauth._tasks):
            await task

    async def main():
        host = await make_host()
        oauth = OAuth(grants.Store(), host.calllog, redirect_uris=CALLBACKS, clock=clock)
        host.signin, host.redirect_uris = oauth, CALLBACKS
        d = _daemon(host, Reads(ORIGIN, OTHER))
        await _start_without_listening(d)
        door = Door(d.front, oauth, clock)
        client, tokens = await door.sign_in()
        serving, under = d.front.app, oauth.current
        verifier, query = door.page(client)              # a code not yet redeemed
        waiting = under.open(under.check({               # and a sign-in in progress
            "client_id": client["client_id"], "redirect_uri": CALLBACK,
            "response_type": "code", "code_challenge": pkce()[1],
            "code_challenge_method": "S256", "state": "s"}))
        await settled(oauth)

        broken_manager()
        await d.tick()
        await settled(oauth)
        # The serving app, its origin and its sign-in are as they were.
        assert d.front.app is serving and d.watch.current == ORIGIN
        assert oauth.current is under and under.waiting(waiting) is not None
        assert not [r for r in host.calllog.rows if r["kind"] in ("grant-stale",
                                                                    "origin-changed")]
        assert (await door.mcp(tokens["access_token"])).status == 200
        assert (await door.token(client, query["code"], verifier)).status == 200

        broken_manager(False)
        await d.tick()                                   # the next read swaps
        await settled(oauth)
        assert d.front.origin == OTHER and d.watch.current == OTHER
        assert oauth.current is not under
        assert [r["grant"] for r in host.calllog.rows if r["kind"] == "grant-stale"]
        await d.front.close()
    run(main())


def test_an_app_whose_mount_raises_fails_its_start(make_host):
    class Raising(Admit):
        def mount(self, origin):
            raise RuntimeError("the sign-in can't mount")

    async def main():
        host = await make_host(signin=Raising())
        app = build_app(ORIGIN, host)
        with pytest.raises(RuntimeError, match="can't mount"):
            await app.start()
        assert app.mount is None and app._task.done()
    run(main())


def test_one_read_with_no_origin_swaps_nothing_two_in_a_row_swap_to_503(make_host):
    async def main():
        host = await make_host()
        d = _daemon(host, Reads(ORIGIN, None, ORIGIN, None, None))
        await _start_without_listening(d)
        first = d.front.app
        await d.tick()
        assert d.front.app is first
        await d.tick()
        assert d.front.app is first
        await d.tick()
        assert d.front.app is first
        await d.tick()
        assert d.front.origin is None
        got = await request(d.front, "POST", "/mcp", initialize(), headers=bearer())
        assert (got.status, got.body) == (503, NO_ORIGIN)
        await d.front.close()
    run(main())


def test_a_request_started_before_a_swap_finishes_on_the_old_app(make_host, fake):
    from kit import provider

    from moxi_connector.provider import Result, Tool

    gate = asyncio.Event()

    async def slow(args, ctx):
        await gate.wait()
        return Result("done")

    async def main():
        host = await make_host(providers={"demo": provider(fake, Tool(
            "slow", "Wait.", {"type": "object"}, slow))})
        d = _daemon(host, Reads(ORIGIN, OTHER))
        await _start_without_listening(d)
        first = d.front.app
        pending = asyncio.ensure_future(request(d.front, "POST", "/mcp", call("slow"),
                                                headers=bearer()))
        while first.running == 0:
            await asyncio.sleep(0.01)
        await d.tick()
        assert d.front.app is not first
        gate.set()
        got = await pending
        assert got.json()["result"]["content"][0]["text"] == "done"
        await asyncio.sleep(0.05)
        assert first.running == 0
        await d.front.close()
    run(main())
