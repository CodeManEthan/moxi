"""The proxy: first against stand-in page servers on 127.0.0.1 that prove themselves (or
fail to) and answer as each test needs; then through the shell's whole request path to
real page servers, spawned over test-local page modules in a site directory of the
session's own.

Each test runs on one fresh loop and stops every child it started before it returns.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import socket
import textwrap
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import h11
import psutil
import pytest
from moxi.paths import Layout
from moxi_loopback.api import RuntimeInfo, proof

from moxi_ui import listen
from moxi_ui import proxy as proxy_mod
from moxi_ui.proxy import Client, Proxy, UpstreamError, downstream, upstream_headers
from moxi_ui.router import shell_profile, shell_table
from moxi_ui.serve import Shell
from moxi_ui.starter import Server, StartFailed, Starters, export_tables
from shell_kit import ACT, FETCH, HOST, Harness, scope

FAKE_EXPORT = {
    "format": 1,
    "routes": [
        {"path": "/api/read", "kind": "read"},
        {"path": "/api/act", "kind": "act"},
        {"path": "/api/events", "kind": "read", "stream": True},
    ],
    "assets": [],
}
LOG = "/the/shell.log"


# --- a stand-in browser ---------------------------------------------------------------


@dataclass
class Answer:
    status: int = 0
    headers: list = field(default_factory=list)
    body: bytes = b""
    complete: bool = False

    def header(self, name: str):
        found = [v.decode() for k, v in self.headers if k.lower() == name.lower().encode()]
        return found[0] if len(found) == 1 else (found or None)

    def json(self):
        return json.loads(self.body)


class Browser:
    """One request to an ASGI app, as uvicorn would make it: the body first, then a
    receive that waits until `leave()` and says the browser went away."""

    def __init__(self, asgi, sc, body: bytes = b""):
        self.asgi, self.scope = asgi, sc
        self.messages: asyncio.Queue = asyncio.Queue()
        self._left = asyncio.Event()
        self._pending = [{"type": "http.request", "body": body, "more_body": False}]

    async def _receive(self):
        if self._pending:
            return self._pending.pop(0)
        await self._left.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        self.messages.put_nowait(message)

    def run(self):
        """The request, run inline: it goes as far as it can before the caller yields."""
        return self.asgi(self.scope, self._receive, self._send)

    def start(self) -> Browser:
        self.task = asyncio.ensure_future(self.run())
        return self

    def leave(self) -> None:
        self._left.set()

    async def next(self, timeout: float = 5.0):
        return await asyncio.wait_for(self.messages.get(), timeout)

    def collected(self) -> Answer:
        answer = Answer()
        while not self.messages.empty():
            message = self.messages.get_nowait()
            if message["type"] == "http.response.start":
                answer.status, answer.headers = message["status"], list(message["headers"])
            else:
                answer.body += message.get("body", b"")
                if not message.get("more_body"):
                    answer.complete = True
        return answer

    async def answer(self, timeout: float = 15.0) -> Answer:
        await asyncio.wait_for(self.task, timeout)
        return self.collected()


async def until(predicate, timeout: float = 5.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")
        await asyncio.sleep(0.01)


# --- stand-in page servers --------------------------------------------------------------


@dataclass
class Seen:
    conn: int
    method: str
    target: str
    headers: list
    body: bytes


class Upstream:
    """A page server stand-in on 127.0.0.1. It answers `/api/prove` with the right proof
    (or, with `prove=False`, a wrong one) and every other request with `answer(seen,
    reply)`; `reply(status, headers, body)` writes a whole answer. `answer` returning
    False closes the connection."""

    def __init__(self, answer=None, *, prove: bool = True):
        self.token = secrets.token_urlsafe(32)
        self.server_id = secrets.token_hex(16)
        self.answer = answer or self.ok
        self.prove = prove
        self.requests: list[Seen] = []
        self.proofs: list[list] = []
        self.connections = 0
        self.closed = 0

    @staticmethod
    async def ok(seen, reply):
        await reply(200, [(b"content-type", b"application/json")], b'{"ok":true}')

    async def __aenter__(self):
        self.listener = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.listener.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.listener.close()

    def info(self) -> RuntimeInfo:
        return RuntimeInfo(module="pxfake", server_id=self.server_id, pid=os.getpid(),
                           transport="tcp", address="127.0.0.1", port=self.port,
                           token=self.token, version="0.1.0",
                           started_at="2026-10-30T00:00:00Z")

    async def _serve(self, reader, writer):
        number = self.connections
        self.connections += 1
        conn = h11.Connection(h11.SERVER)

        async def event():
            while True:
                found = conn.next_event()
                if found is not h11.NEED_DATA:
                    return found
                conn.receive_data(await reader.read(65536))

        async def reply(status, headers, body=b"", *, length=True):
            pairs = list(headers)
            if length:
                pairs.append((b"content-length", str(len(body)).encode()))
            writer.write(conn.send(h11.Response(status_code=status, headers=pairs)))
            if body:
                writer.write(conn.send(h11.Data(data=body)))
            writer.write(conn.send(h11.EndOfMessage()))
            await writer.drain()

        reply.conn, reply.reader, reply.writer = conn, reader, writer
        try:
            while True:
                request = await event()
                if not isinstance(request, h11.Request):
                    return
                body = b""
                while True:
                    part = await event()
                    if isinstance(part, h11.Data):
                        body += part.data
                    else:
                        break
                headers = [(k.lower(), v) for k, v in request.headers]
                if request.target == b"/api/prove":
                    self.proofs.append(headers)
                    nonce = dict(headers)[b"x-moxi-nonce"].decode()
                    given = proof(self.token if self.prove else secrets.token_urlsafe(32),
                                  self.server_id, nonce)
                    await reply(204, [(b"x-moxi-proof", given.encode())], length=False)
                else:
                    seen = Seen(number, request.method.decode(), request.target.decode(),
                                headers, body)
                    self.requests.append(seen)
                    if await self.answer(seen, reply) is False:
                        return
                if conn.our_state is not h11.DONE or conn.their_state is not h11.DONE:
                    return
                conn.start_next_cycle()
        except (OSError, h11.ProtocolError):
            pass
        finally:
            writer.close()
            self.closed += 1


class FakeStarters:
    """The starter's surface the proxy uses, over stand-in servers: each `ensure` with no
    current server takes the next one."""

    log = LOG

    def __init__(self, *upstreams: Upstream, fail: StartFailed | None = None):
        self.upstreams = list(upstreams)
        self.fail = fail
        self.current: Server | None = None
        self.ensures = 0
        self.lost_servers: list[Server] = []

    async def ensure(self, module):
        self.ensures += 1
        if self.fail is not None:
            raise self.fail
        if self.current is None:
            self.current = Server(module, self.upstreams.pop(0).info(), None, started=0.0)
        return self.current

    def lost(self, server):
        self.lost_servers.append(server)
        if server is self.current:
            server._drop()
            self.current = None


def fake_proxy(starters) -> Proxy:
    table = shell_table(["pxfake"], {"pxfake": FAKE_EXPORT})
    table.freeze()
    return Proxy(starters, table=lambda: table)


def fake_scope(method: str, path: str, headers=(), query: bytes = b""):
    return scope(method, path, headers=list(headers), query=query)


BROWSER_HEADERS = [
    (b"cookie", b"moxi-ui=" + b"A" * 43), (b"x-moxi-key", b"K" * 43),
    (b"x-moxi-token", b"F" * 43), (b"origin", b"http://" + HOST),
    (b"referer", b"http://127.0.0.1/m/pxfake/"), (b"sec-fetch-site", b"same-origin"),
    (b"sec-fetch-mode", b"cors"), (b"sec-fetch-dest", b"empty"),
    (b"user-agent", b"Test/1"), (b"accept", b"application/json"),
    (b"accept-language", b"en"), (b"if-none-match", b'"v1"'), (b"last-event-id", b"7"),
    (b"connection", b"keep-alive"), (b"accept-encoding", b"gzip"),
]


def run(body, timeout: float = 30.0):
    asyncio.run(asyncio.wait_for(body(), timeout))


# --- against stand-ins ------------------------------------------------------------------


def test_upstream_gets_only_the_allowlist_its_token_and_its_host():
    async def answer(seen, reply):
        await reply(200, [(b"content-type", b"application/json"),
                          (b"set-cookie", b"a=b"), (b"connection", b"x-private"),
                          (b"x-private", b"1"), (b"keep-alive", b"timeout=5"),
                          (b"x-kept", b"yes"), (b"x-content-type-options", b"nosniff")],
                    b'{"ok":true}')

    async def body():
        async with Upstream(answer) as up:
            px = fake_proxy(FakeStarters(up))
            got = await Browser(px, fake_scope("GET", "/m/pxfake/api/read", BROWSER_HEADERS,
                                               query=b"n=1")).start().answer()
            seen, = up.requests
            assert seen.target == "/api/read?n=1"
            assert [name for name, _ in seen.headers] == [
                b"host", b"x-moxi-token", b"accept", b"accept-language", b"if-none-match",
                b"last-event-id"]
            values = dict(seen.headers)
            assert values[b"host"] == b"127.0.0.1:%d" % up.port
            assert values[b"x-moxi-token"] == up.token.encode()
            assert [name for name, _ in up.proofs[0]] == [b"host", b"x-moxi-nonce"]
            assert got.status == 200 and got.body == b'{"ok":true}' and got.complete
            names = [name for name, _ in got.headers]
            for gone in (b"set-cookie", b"connection", b"x-private", b"keep-alive"):
                assert gone not in names
            assert names.count(b"x-content-type-options") == 1
            assert got.header("x-kept") == "yes"
            assert got.header("content-security-policy") and got.header("cache-control")

    run(body)


def test_the_header_helpers():
    assert upstream_headers([(b"Accept", b"*/*"), (b"Host", b"evil"), (b"Cookie", b"c")],
                            host="localhost", token="T" * 43) == [
        (b"host", b"localhost"), (b"x-moxi-token", b"T" * 43), (b"Accept", b"*/*")]
    out = downstream([(b"Content-Type", b"text/plain"), (b"Set-Cookie", b"x=1"),
                      (b"Transfer-Encoding", b"chunked"), (b"Cache-Control", b"no-cache")])
    names = [name for name, _ in out]
    assert b"set-cookie" not in names and b"transfer-encoding" not in names
    assert (b"cache-control", b"no-cache") in out and names.count(b"cache-control") == 1
    assert b"content-security-policy" in names


def test_a_failed_proof_drops_the_server_and_one_new_start_sends_the_act():
    async def body():
        async with Upstream(prove=False) as bad, Upstream() as good:
            starters = FakeStarters(bad, good)
            got = await Browser(fake_proxy(starters), fake_scope(
                "POST", "/m/pxfake/api/act", [(b"content-type", b"application/json")]),
                b"{}").start().answer()
            assert got.status == 200
            assert bad.requests == [] and all(
                b"x-moxi-token" not in dict(h) for h in bad.proofs), "no token, no byte"
            seen, = good.requests
            assert seen.method == "POST" and seen.body == b"{}"
            assert dict(seen.headers)[b"content-type"] == b"application/json"
            assert len(starters.lost_servers) == 1 and starters.ensures == 2

    run(body)


def test_two_unreachable_servers_give_a_502():
    async def body():
        async with Upstream(prove=False) as one, Upstream(prove=False) as two:
            starters = FakeStarters(one, two)
            got = await Browser(fake_proxy(starters), fake_scope(
                "GET", "/m/pxfake/api/read")).start().answer()
            assert got.status == 502
            assert got.json() == {"error": "unreachable", "module": "pxfake", "log": LOG}
            assert got.header("content-security-policy")
            assert one.requests == two.requests == []

    run(body)


def test_a_post_sent_with_no_answer_is_a_502_and_never_sent_again():
    async def body():
        async with Upstream(lambda seen, reply: _false()) as up:
            starters = FakeStarters(up)
            got = await Browser(fake_proxy(starters), fake_scope(
                "POST", "/m/pxfake/api/act", [(b"content-type", b"application/json")]),
                b'{"n":1}').start().answer()
            assert got.status == 502
            assert got.json() == {"error": "upstream", "module": "pxfake", "log": LOG}
            assert len(up.requests) == 1 and starters.lost_servers == []

    run(body)


async def _false():
    return False


def test_a_get_sent_with_no_answer_is_sent_once_more_on_a_new_connection():
    async def body():
        async def first_fails(seen, reply):
            if len(up.requests) == 1:
                return False
            await Upstream.ok(seen, reply)

        async with Upstream(first_fails) as up:
            got = await Browser(fake_proxy(FakeStarters(up)), fake_scope(
                "GET", "/m/pxfake/api/read")).start().answer()
            assert got.status == 200
            assert [seen.conn for seen in up.requests] == [0, 1]

        async with Upstream(lambda seen, reply: _false()) as up:
            got = await Browser(fake_proxy(FakeStarters(up)), fake_scope(
                "GET", "/m/pxfake/api/read")).start().answer()
            assert got.status == 502 and got.json()["error"] == "upstream"
            assert len(up.requests) == 2

    run(body)


def test_no_first_byte_in_time_is_a_504_and_a_stream_has_no_deadline(monkeypatch):
    monkeypatch.setattr(proxy_mod, "FIRST_BYTE_WITHIN", 0.3)

    async def slow(seen, reply):
        await asyncio.sleep(0.6)
        await Upstream.ok(seen, reply)

    async def body():
        async with Upstream(slow) as up:
            starters = FakeStarters(up)
            got = await Browser(fake_proxy(starters), fake_scope(
                "GET", "/m/pxfake/api/read")).start().answer()
            assert got.status == 504
            assert got.json() == {"error": "timeout", "module": "pxfake"}
            assert len(up.requests) == 1, "a GET that timed out is not sent again"
            got = await Browser(fake_proxy(starters), fake_scope(
                "GET", "/m/pxfake/api/events")).start().answer()
            assert got.status == 200

    run(body)


def test_a_modules_503_passes_through():
    async def busy(seen, reply):
        await reply(503, [(b"retry-after", b"1")])

    async def body():
        async with Upstream(busy) as up:
            got = await Browser(fake_proxy(FakeStarters(up)), fake_scope(
                "GET", "/m/pxfake/api/read")).start().answer()
            assert got.status == 503 and got.header("retry-after") == "1"
            assert len(up.requests) == 1

    run(body)


def test_reads_reuse_a_pooled_connection_and_each_act_gets_a_new_one():
    async def body():
        async with Upstream() as up:
            px = fake_proxy(FakeStarters(up))
            for method, path in (("GET", "read"), ("GET", "read"), ("POST", "act"),
                                 ("POST", "act"), ("GET", "read")):
                headers = [(b"content-type", b"application/json")] if method == "POST" else []
                got = await Browser(px, fake_scope(method, f"/m/pxfake/api/{path}", headers),
                                    b"{}" if method == "POST" else b"").start().answer()
                assert got.status == 200
            conns = [seen.conn for seen in up.requests]
            assert conns[0] == conns[1], "the second read reuses the first's connection"
            assert len({conns[0], conns[2], conns[3]}) == 3, "each act is on a new one"
            assert conns[4] in conns[:4] and up.connections == 3

    run(body)


def test_an_act_on_a_connection_proved_over_a_quarter_second_before_goes_on_a_new_one():
    async def body():
        async with Upstream() as up:
            starters = FakeStarters(up)
            server = await starters.ensure("pxfake")
            connect, made = server.connect, []

            async def late_connect(**kwargs):
                conn = await connect(**kwargs)
                made.append(conn)
                if len(made) == 1:
                    conn.proved_at -= 0.3  # as if the loop had stalled after the proof
                return conn

            server.connect = late_connect
            got = await Browser(fake_proxy(starters), fake_scope(
                "POST", "/m/pxfake/api/act", [(b"content-type", b"application/json")]),
                b"{}").start().answer()
            assert got.status == 200 and len(made) == 2
            assert made[0].writer.is_closing()
            seen, = up.requests
            assert seen.conn == 1, "the act went on the second connection only"
            await until(lambda: up.closed >= 1, what="the stale connection to close")

    run(body)


def _stream_upstream(release: asyncio.Event):
    async def answer(seen, reply):
        conn, writer = reply.conn, reply.writer
        writer.write(conn.send(h11.Response(status_code=200, headers=[
            (b"content-type", b"text/event-stream"), (b"transfer-encoding", b"chunked")])))
        writer.write(conn.send(h11.Data(data=b"event: tick\ndata: {\"n\":1}\n\n")))
        await writer.drain()
        # Held open until released, or until the proxy closes its end.
        released = asyncio.ensure_future(release.wait())
        closed = asyncio.ensure_future(reply.reader.read())
        await asyncio.wait({released, closed}, return_when=asyncio.FIRST_COMPLETED)
        released.cancel()
        closed.cancel()
        if release.is_set():
            writer.write(conn.send(h11.EndOfMessage()))
            await writer.drain()
        return False
    return answer


def test_a_stream_reaches_the_browser_before_it_ends_and_ends_when_the_browser_leaves():
    async def body():
        release = asyncio.Event()
        async with Upstream(_stream_upstream(release)) as up:
            starters = FakeStarters(up)
            browser = Browser(fake_proxy(starters), fake_scope(
                "GET", "/m/pxfake/api/events", [(b"accept", b"text/event-stream")])).start()
            start = await browser.next()
            assert start["status"] == 200
            chunk = await browser.next()
            assert b"event: tick" in chunk["body"] and chunk["more_body"]
            server = starters.current
            assert server.streams == 1 and up.closed == 0
            browser.leave()
            await browser.answer()
            assert server.streams == 0
            await until(lambda: up.closed == 1, what="the upstream stream to close")
            release.set()

    run(body)


def test_a_drain_cuts_a_stream_and_the_browsers_answer_ends():
    async def body():
        release = asyncio.Event()
        async with Upstream(_stream_upstream(release)) as up:
            starters = FakeStarters(up)
            browser = Browser(fake_proxy(starters), fake_scope(
                "GET", "/m/pxfake/api/events")).start()
            await browser.next()
            await browser.next()
            server = starters.current
            use, = server._uses
            use.cut()  # what the starter's drain does to every open stream
            got = await browser.answer()
            assert got.complete, "the browser's stream ends, so its client reconnects"
            assert server.streams == 0
            release.set()

    run(body)


def test_an_answer_that_breaks_off_cuts_the_browsers_connection():
    async def broken(seen, reply):
        conn, writer = reply.conn, reply.writer
        writer.write(conn.send(h11.Response(status_code=200,
                                            headers=[(b"content-length", b"100")])))
        writer.write(conn.send(h11.Data(data=b"0123456789")))
        await writer.drain()
        return False

    async def body():
        async with Upstream(broken) as up:
            got = await Browser(fake_proxy(FakeStarters(up)), fake_scope(
                "GET", "/m/pxfake/api/read")).start().answer()
            assert got.status == 200 and got.body == b"0123456789"
            assert not got.complete, "the response is left unfinished, so it is cut"
            assert len(up.requests) == 1

    run(body)


def test_a_failed_start_is_a_502_naming_the_module_its_exit_and_log():
    async def body():
        failure = StartFailed("pxfake", "the page server exited 3 at start", exit=3, log=LOG)
        got = await Browser(fake_proxy(FakeStarters(fail=failure)), fake_scope(
            "GET", "/m/pxfake/api/read")).start().answer()
        assert got.status == 502
        assert got.json() == {"error": "start", "module": "pxfake", "exit": 3, "log": LOG}
        assert "exited" not in got.body.decode()

    run(body)


def test_a_path_the_table_does_not_hold_is_a_404_and_starts_nothing():
    async def body():
        starters = FakeStarters()
        for method, path in (("GET", "/m/pxfake/api/nothing"), ("POST", "/m/pxfake/api/read"),
                             ("GET", "/m/other/api/read")):
            got = await Browser(fake_proxy(starters), fake_scope(method, path)).start().answer()
            assert got.status == 404 and got.body == b""
        assert starters.ensures == 0

    run(body)


def test_the_client_alone_gives_a_stream_that_a_drain_ends():
    async def body():
        release = asyncio.Event()
        async with Upstream(_stream_upstream(release)) as up:
            starters = FakeStarters(up)
            client = Client(starters)
            response = await client.request("pxfake", "GET", "/api/events?topics=tick",
                                            stream=True)
            async with response:
                chunks = response.chunks()
                assert b"tick" in await anext(chunks)
                for use in list(starters.current._uses):
                    use.cut()
                assert [chunk async for chunk in chunks] == []
                assert response.cut
            assert starters.current.streams == 0
            release.set()
            with pytest.raises(UpstreamError) as failed:
                await Client(FakeStarters(fail=StartFailed("pxfake", "x", exit=1, log=LOG))
                             ).request("pxfake", "GET", "/api/events", stream=True)
            assert failed.value.status == 502

    run(body)


def test_a_stream_cancelled_while_its_connection_is_proved_is_not_counted():
    async def body():
        async with Upstream(_stream_upstream(asyncio.Event())) as up:
            starters = FakeStarters(up)
            server = await starters.ensure("pxfake")
            connect = server.connect

            async def slow(**kwargs):
                await asyncio.sleep(0.5)
                return await connect(**kwargs)

            server.connect = slow
            request = asyncio.ensure_future(Client(starters).request(
                "pxfake", "GET", "/api/events?topics=tick", stream=True))
            await until(lambda: server.streams == 1, what="the stream's count")
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            assert server.streams == 0 and server._quiet.is_set()

    run(body)


# --- through the shell to real page servers ---------------------------------------------


PAGE = '''
import json
import os

from moxi_loopback import server as _server
from moxi_loopback.routes import Field, Out, Page, Route

# Record the header names of every request the server checks, so a test can see what
# reached the module.
_check = _server.check


def _record(view, profile):
    with open(os.path.join(os.environ["PX_DIR"], "seen.jsonl"), "a") as out:
        out.write(json.dumps({"path": view.raw_path.decode(),
                              "names": [name.decode() for name, _ in view.headers]}) + "\\n")
    return _check(view, profile)


_server.check = _record

H = "moxi_NAME.handlers"
PAGE = Page(
    routes=(
        Route("/api/echo", "read", H + ":echo", query={"n": Field("int", required=False)},
              returns={"n": Out("int")}),
        Route("/api/crash", "read", H + ":crash", returns={"n": Out("int")}),
        Route("/api/act", "act", H + ":act", body={"n": Field("int", required=False)},
              returns={"count": Out("int")}),
        Route("/api/slowact", "act", H + ":slowact", returns={"count": Out("int")}),
        Route("/api/events", "read", H + ":events", stream=True,
              topics={"tick": Out("table", items={"n": Out("int")})}),
    ),
    health=H + ":health",
)
'''

HANDLERS = '''
import asyncio
import os

from moxi_loopback.server import Topics

TOPICS = Topics("tick")


def _path(name):
    return os.path.join(os.environ["PX_DIR"], name)


def _acted():
    with open(_path("acts"), "a") as out:
        out.write("act\\n")
    with open(_path("acts")) as read:
        return len(read.read().splitlines())


def echo(call):
    return {"n": call.query.get("n", 0)}


def crash(call):
    if not os.path.exists(_path("crashed")):
        open(_path("crashed"), "w").close()
        os._exit(1)
    return {"n": 1}


def act(call):
    return {"count": _acted()}


async def slowact(call):
    count = _acted()
    await asyncio.sleep(10)
    return {"count": count}


def events(call):
    TOPICS.publish("tick", {"n": 1})
    return call.stream(TOPICS)


def health():
    return (True, "fine")
'''

ANNOUNCEMENT = {
    "INTERFACES": "frozenset({1})", "API": "None", "CLI": "None",
    "SUMMARY": "'A module made for a test.'", "OPTIONAL": "{}", "GATED": "{}",
    "INTERNAL_ENV": "{}", "SETTINGS": "None", "SETUP": "None", "SEGMENT": "None",
}
MODULES = ("pxpage", "pxbad")


def _module(root: Path, name: str, *, handlers: bool = True) -> None:
    package = root / f"moxi_{name}"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "page.py").write_text(textwrap.dedent(PAGE).replace("NAME", name))
    if handlers:
        (package / "handlers.py").write_text(HANDLERS)
    fields = {**ANNOUNCEMENT, "PAGE": repr(f"moxi_{name}.page:PAGE")}
    (package / "module.py").write_text("".join(f"{k} = {v}\n" for k, v in fields.items()))
    info = root / f"moxi_{name}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.1.0\n")
    (info / "entry_points.txt").write_text(f"[moxi.modules]\n{name} = moxi_{name}.module\n")


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("proxy-site")
    _module(root, "pxpage")
    _module(root, "pxbad", handlers=False)  # its handlers don't import: exit 3
    return root


@pytest.fixture
def shell(site, tmp_path, monkeypatch):
    """The page modules on the import path, a files dir the pages write to, and the
    shell's runtime and state dirs."""
    monkeypatch.setenv("PYTHONPATH", str(site))
    files = tmp_path / "px"
    files.mkdir()
    monkeypatch.setenv("PX_DIR", str(files))
    layout = Layout("ui")
    runtime_dir = layout.runtime_dir(create=True)
    mounts = export_tables(list(MODULES), cwd=runtime_dir).mounts
    assert set(mounts) == set(MODULES)
    return runtime_dir, layout.state_dir(), files, mounts


class Rig:
    """The shell's request path, `worker_layer(guard(prove(router)))` with its real profile,
    over real starters, and one signed-in browser."""

    def __init__(self, shell, tmp_path, **kwargs):
        runtime_dir, state_dir, self.files, mounts = shell
        self.starters = Starters(runtime_dir=runtime_dir, state_dir=state_dir,
                                 log=state_dir / "logs" / "shell.log", **kwargs)
        self.harness = Harness(tmp_path / "state")
        self.harness.profile = shell_profile(shell_table(MODULES, mounts),
                                             self.harness.sessions, ("127.0.0.1", "localhost"))
        self.proxy = Proxy(self.starters, table=lambda: self.harness.profile.table)
        self.harness.router.proxy = self.proxy
        self.cookie, self.key = self.harness.sign_in()

    def browser(self, method: str, path: str, *, headers=(), body: bytes = b"",
                query: bytes = b"") -> Browser:
        extra = list(ACT) if method == "POST" else list(FETCH)
        if method == "POST":
            extra.append((b"content-length", str(len(body)).encode()))
        sc = scope(method, path, headers=[self.cookie, self.key, *extra, *headers],
                   query=query)
        return Browser(self.harness.asgi, sc, body)

    async def send(self, method: str, path: str, **kwargs) -> Answer:
        return await self.browser(method, path, **kwargs).start().answer()

    def acts(self) -> int:
        try:
            return len((self.files / "acts").read_text().splitlines())
        except FileNotFoundError:
            return 0

    def seen(self) -> list[dict]:
        try:
            lines = (self.files / "seen.jsonl").read_text().splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines]


def drive(rig: Rig, body, timeout: float = 40.0) -> None:
    async def main():
        try:
            await body(rig)
        finally:
            await rig.starters.aclose()

    asyncio.run(asyncio.wait_for(main(), timeout))


def _reaped_or_zombie(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _kill_and_wait(pid: int) -> None:
    """SIGKILL `pid` and block, without yielding to the loop, until it is dead, so the
    shell has not yet seen the exit when the next request runs."""
    os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while not _reaped_or_zombie(pid):
        assert time.monotonic() < deadline, "the child did not die"
        time.sleep(0.005)


def test_a_module_gets_only_the_allowlisted_headers_and_the_token(shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        got = await rig.send("GET", "/m/pxpage/api/echo", query=b"n=4", headers=[
            (b"accept", b"application/json"), (b"accept-language", b"en"),
            (b"if-none-match", b'"x"'), (b"referer", b"http://127.0.0.1/"),
            (b"x-moxi-token", b"F" * 43), (b"user-agent", b"Test/1")])
        assert got.status == 200 and got.json() == {"n": 4}
        seen = rig.seen()
        proofs = [s for s in seen if s["path"] == "/api/prove"]
        echo, = [s for s in seen if s["path"] == "/api/echo"]
        assert proofs and all(set(p["names"]) == {"host", "x-moxi-nonce"} for p in proofs)
        assert sorted(echo["names"]) == sorted(
            ["host", "x-moxi-token", "accept", "accept-language", "if-none-match"])

    drive(rig, body)


def test_a_path_missing_from_the_export_is_a_404_and_starts_nothing(shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        for method, path in (("GET", "/m/pxpage/api/health"), ("GET", "/m/pxpage/api/nothing"),
                             ("GET", "/m/pxpage/api/act")):
            got = await rig.send(method, path)
            assert got.status == 404 and got.body == b""
        assert rig.starters.get("pxpage").record.starts == 0 and rig.seen() == []

    drive(rig, body)


def test_a_post_that_meets_a_closed_listener_is_started_again_and_acts_once(shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        old = await rig.starters.ensure("pxpage")
        lost = []
        real_lost = rig.starters.lost
        rig.starters.lost = lambda server: (lost.append(server), real_lost(server))
        _kill_and_wait(old.child.pid)
        assert not old.gone, "the shell has not seen the exit yet"
        browser = rig.browser("POST", "/m/pxpage/api/act", body=b"{}")
        await browser.run()
        got = browser.collected()
        assert got.status == 200 and got.json() == {"count": 1}
        assert lost == [old] and rig.acts() == 1
        assert rig.starters.get("pxpage").record.starts == 2

    drive(rig, body)


def test_a_post_cut_after_sending_is_a_502_and_acted_once(shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        server = await rig.starters.ensure("pxpage")
        browser = rig.browser("POST", "/m/pxpage/api/slowact", body=b"{}").start()
        await until(lambda: rig.acts() == 1, what="the act to run")
        server.child.popen.kill()
        got = await browser.answer()
        assert got.status == 502 and got.json()["error"] == "upstream"
        await asyncio.sleep(0.3)
        assert rig.acts() == 1, "a POST that may have acted is never sent again"

    drive(rig, body)


def test_a_get_whose_server_dies_after_sending_is_sent_once_more(shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        got = await rig.send("GET", "/m/pxpage/api/crash")
        assert got.status == 200 and got.json() == {"n": 1}
        assert (rig.files / "crashed").exists()
        assert rig.starters.get("pxpage").record.starts == 2

    drive(rig, body)


def test_an_act_sent_just_after_its_proof_as_the_idle_time_runs_out_is_served_once(
        shell, tmp_path):
    rig = Rig(shell, tmp_path, child_args=("--idle-s", "0.5"))

    async def body(rig):
        for n, delay in enumerate((0.4, 0.5, 0.6), 1):
            assert (await rig.send("GET", "/m/pxpage/api/echo")).status == 200
            await asyncio.sleep(delay)
            got = await rig.send("POST", "/m/pxpage/api/act", body=b"{}")
            assert got.status == 200 and got.json() == {"count": n}
            assert rig.acts() == n

    drive(rig, body)


def test_a_stream_reaches_the_browser_and_a_drain_ends_it_for_the_next_server(
        shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        starter = rig.starters.get("pxpage")
        browser = rig.browser("GET", "/m/pxpage/api/events",
                              headers=[(b"accept", b"text/event-stream")]).start()
        start = await browser.next()
        assert start["status"] == 200
        first = await browser.next()
        assert b"tick" in first["body"] and first["more_body"]
        old = starter.server
        assert old.streams == 1
        drain = starter.drain()
        got = await browser.answer()
        assert got.complete and old.streams == 0
        after = await rig.send("GET", "/m/pxpage/api/echo", query=b"n=2")
        assert after.status == 200 and after.json() == {"n": 2}
        await drain
        assert starter.server is not old and old.gone

    drive(rig, body)


def test_a_page_that_fails_to_start_is_a_502_and_the_same_one_within_ten_seconds(
        shell, tmp_path):
    rig = Rig(shell, tmp_path)

    async def body(rig):
        first = await rig.send("GET", "/m/pxbad/api/echo")
        assert first.status == 502
        assert first.json() == {"error": "start", "module": "pxbad", "exit": 3,
                                "log": rig.starters.log}
        again = await rig.send("POST", "/m/pxbad/api/act", body=b"{}")
        assert again.status == 502 and again.json() == first.json()
        assert rig.starters.get("pxbad").record.starts == 1

    drive(rig, body)


def test_the_shell_proxies_on_its_real_listener_and_stops_its_children_at_its_stop(shell):
    runtime_dir, state_dir, files, mounts = shell
    running = Shell(listen.bind(0), epoch=None, modules=MODULES, mounts=mounts,
                    child_args=("--idle-s", "30"))
    assert running.starters.child_args == ("--idle-s", "30")
    running.start()
    assert isinstance(running.router.proxy, Proxy)
    thread = threading.Thread(target=lambda: asyncio.run(running.run()), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while running._should_exit is None and time.monotonic() < deadline:
            time.sleep(0.01)
        session_id, key = running.sessions.store.mint()
        port = running.listeners.port
        head = (f"GET /m/pxpage/api/echo?n=9 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Cookie: moxi-ui={session_id}\r\nX-Moxi-Key: {key}\r\n"
                "Sec-Fetch-Site: same-origin\r\nSec-Fetch-Mode: cors\r\n"
                "Sec-Fetch-Dest: empty\r\nConnection: close\r\n\r\n")
        with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
            sock.sendall(head.encode())
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        assert data.startswith(b"HTTP/1.1 200") and data.endswith(b'{"n":9}')
        assert running.starters.children() == 1
    finally:
        running.stop()
        thread.join(15)
        running.close()
    assert running.starters.closing and running.starters.children() == 0
