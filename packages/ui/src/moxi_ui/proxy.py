"""The shell's proxy: `/m/<module>/<rest>` to `/<rest>` on that module's page server.

The proxy speaks HTTP/1.1 to the page server with h11 in client mode, over connections
the starter hands out, each proved by loopback's `open_connection` before any byte of the
request goes on it, the token included.

- **Mapping.** Only a request whose method and raw path the shell's current table holds
  is sent, so a path the module's export doesn't list never starts a server. The query
  string goes with it unchanged.
- **Headers.** Upstream gets only the browser's `Accept`, `Accept-Language`,
  `Content-Type`, `Content-Length`, `If-None-Match`, `Last-Event-ID` and
  `Idempotency-Key`, plus the server's token and the Host it admits (`127.0.0.1:<port>`
  on TCP, `localhost` on a Unix socket).
  Downstream gets the module's headers less the hop-by-hop ones and `Set-Cookie`, with any
  header of loopback's fixed set that is missing added.
- **Connections.** A read reuses a kept-alive connection from the server's pool (at most
  4). An act is sent within 0.25 s of its connection's proof, on a connection proved for
  it, never on a pooled one: a proof promises a stopping server will still take the next
  request on that connection only if it arrives within half a second.
- **Streaming.** Bodies stream both ways as they come. A stream (the page's event route)
  goes on a connection of its own with no read deadline; anything else gets 30 s to the
  first byte of its answer. A stream is counted on the server until either side ends it,
  and a drain cuts it, so the browser's client reconnects to the next server.
- **Errors.** A start that failed is a 502 naming the module, its exit and its log. A
  connect or proof that fails, or a server that is gone, with nothing sent: the server is
  dropped, one new start, and the request is sent again, whatever its method. A request
  sent that gets no answer byte: a GET is sent once more, anything else is a 502, since
  the module may have acted. An answer that breaks off cuts the browser's connection. No
  first byte in 30 s is a 504. The module's own answers, its 503s included, pass through.

`Client` is the part push.py uses too: `await client.request(...)` gives a `Response` once
the module's answer head is in, and its body comes from `chunks()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable
from typing import Any

import h11
from moxi_loopback.api import NotOurServer, Table, complete, response_headers

from .router import forwarded
from .starter import Conn, Server, ServerGone, StartFailed, Use

__all__ = [
    "BODY_CAP_GET",
    "FIRST_BYTE_WITHIN",
    "HOP_BY_HOP",
    "BrowserGone",
    "Client",
    "Proxy",
    "Response",
    "UpstreamBroken",
    "UpstreamError",
    "downstream",
    "upstream_headers",
]

#: Seconds an answer that is not a stream has to its first byte before the browser gets
#: a 504.
FIRST_BYTE_WITHIN = 30.0
#: The largest body a GET may carry. A GET's body is held so the GET can be sent again.
BODY_CAP_GET = 64 * 1024
#: Times an act's connection is proved again when it was not sent within 0.25 s of its
#: proof, before the act counts as not sent.
FRESH_TRIES = 3
#: Bytes read from a page server at once.
READ_SIZE = 64 * 1024

#: Headers that belong to one connection, never passed on in either direction.
HOP_BY_HOP = frozenset({
    b"connection", b"keep-alive", b"proxy-connection", b"proxy-authenticate",
    b"proxy-authorization", b"te", b"trailer", b"transfer-encoding", b"upgrade",
})

_log = logging.getLogger("moxi.ui.proxy")


class BrowserGone(Exception):
    """The browser went away before its request was all read. Nothing is answered."""


class UpstreamBroken(ConnectionError):
    """The module's answer broke off after its head: the browser's connection is cut."""


class UpstreamError(Exception):
    """The browser's answer when the module's server gave none: `status` and a JSON
    `body`. Nothing in it comes from the module's stderr."""

    def __init__(self, status: int, body: dict[str, Any]):
        super().__init__(f"{status} {body.get('error')}")
        self.status = status
        self.body = body

    def answer(self) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
        """`(status, headers, body)`, with loopback's fixed header set."""
        data = json.dumps(self.body, separators=(",", ":")).encode("ascii")
        return self.status, response_headers(extra=[
            ("Content-Type", "application/json"), ("Content-Length", str(len(data)))]), data


class _NotSent(Exception):
    """Nothing but a proof went to the server; the request may be sent elsewhere."""


class _NoAnswer(Exception):
    """The request went to the server and no answer head came back."""


def upstream_headers(headers: Iterable[tuple[bytes, bytes]], *, host: str, token: str
                     ) -> list[tuple[bytes, bytes]]:
    """The headers a page server gets: `Host` and the token first, then the browser's
    headers of the allowlist, in order. A browser's own `Host` or token never passes."""
    return [(b"host", host.encode("ascii")), (b"x-moxi-token", token.encode("ascii")),
            *forwarded(headers)]


def downstream(headers: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """A module's answer headers as the browser gets them: hop-by-hop headers, any header
    the module's `Connection` names, and `Set-Cookie` dropped, then whatever of loopback's
    fixed set is missing added."""
    pairs = [(name.lower(), value) for name, value in headers]
    named = {token.strip().lower() for name, value in pairs if name == b"connection"
             for token in value.split(b",")}
    kept = [(name, value) for name, value in pairs
            if name not in HOP_BY_HOP and name not in named and name != b"set-cookie"]
    return complete(kept)


def _host(server: Server) -> str:
    info = server.info
    if info.transport == "tcp":
        return f"127.0.0.1:{info.port}"
    return "localhost"


class Response:
    """One request on a module's server, from its connection to the end of its answer.

    Once `Client.request` returns it, `status` and `headers` (the module's own, names in
    lower case) are set and the body comes from `chunks()`. `aclose()` ends it: the
    request stops being counted on the server, and a connection whose exchange finished
    goes back to the pool. Use it as `async with`. `cut` is true once a drain ended it.
    """

    def __init__(self, module: str, server: Server, *, stream: bool):
        self.module = module
        self.server = server
        self.stream = stream
        self.status = 0
        self.headers: list[tuple[bytes, bytes]] = []
        self.cut = False
        self.use: Use | None = None
        self.conn: Conn | None = None
        self._h11: h11.Connection | None = None
        self._complete = False
        self._closed = False

    async def __aenter__(self) -> Response:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _cut(self) -> None:
        # Called by the starter when the server drains: the stream ends now, and its
        # reader sees the end as a cut, not a break.
        self.cut = True
        if self.conn is not None:
            self.conn.close()

    async def chunks(self) -> AsyncIterator[bytes]:
        """The answer's body, as it arrives. Ends at the answer's end, or when a drain
        cut it; raises `UpstreamBroken` when it broke off before its end."""
        while not self._complete:
            try:
                event = await self._event()
            except (OSError, h11.RemoteProtocolError) as err:
                if self.cut:
                    return
                raise UpstreamBroken(f"{self.module}'s answer broke off: {err!r}") from None
            if isinstance(event, h11.Data):
                if event.data:
                    yield bytes(event.data)
            elif isinstance(event, h11.EndOfMessage):
                self._complete = True
            else:
                if self.cut:
                    return
                raise UpstreamBroken(f"{self.module}'s answer broke off before its end")

    async def read(self) -> bytes:
        """The whole body. For short answers only."""
        return b"".join([chunk async for chunk in self.chunks()])

    async def aclose(self) -> None:
        """End the request: pool its connection if the exchange is done and it stays
        open, else close it; stop counting it on the server. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        conn, connection = self.conn, self._h11
        if conn is not None:
            if (self._complete and not self.stream and not self.cut and connection is not None
                    and connection.our_state is h11.DONE and connection.their_state is h11.DONE):
                connection.start_next_cycle()
                self.server.give(conn)
            else:
                conn.close()
        if self.use is not None:
            self.use.end()

    # -- the exchange --------------------------------------------------------------------

    async def _connect(self, *, pooled: bool, act: bool) -> None:
        server = self.server
        conn = server.take() if pooled else None
        if conn is None:
            conn = await server.connect()
            tries = 1
            while act and not conn.fresh():
                conn.close()
                if tries >= FRESH_TRIES:
                    raise _NotSent(f"no connection to {self.module} was sent on within "
                                   "0.25 s of its proof")
                conn = await server.connect()
                tries += 1
        self.conn = conn
        if conn.state is None:
            conn.state = h11.Connection(h11.CLIENT)
        self._h11 = conn.state
        if self.cut:
            raise ServerGone(f"{self.module}'s server is draining")

    async def _send(self, method: str, target: str, headers: list[tuple[bytes, bytes]],
                    body: bytes | AsyncIterable[bytes]) -> None:
        conn, connection = self.conn, self._h11
        assert conn is not None and connection is not None
        try:
            head = connection.send(h11.Request(method=method, target=target, headers=headers))
        except h11.LocalProtocolError as err:
            raise _NotSent(f"the request can't be framed: {err}") from None
        try:
            conn.writer.write(head)
            if isinstance(body, (bytes, bytearray)):
                if body:
                    conn.writer.write(connection.send(h11.Data(data=bytes(body))))
            else:
                async for chunk in body:
                    if chunk:
                        conn.writer.write(connection.send(h11.Data(data=chunk)))
                        await conn.writer.drain()
            conn.writer.write(connection.send(h11.EndOfMessage()))
            await conn.writer.drain()
        except h11.LocalProtocolError:
            # The browser's body did not match its own Content-Length.
            raise BrowserGone("the browser's body ended short of its length") from None
        except OSError as err:
            raise _NoAnswer(f"sending to {self.module} failed: {err!r}") from None

    async def _event(self) -> Any:
        conn, connection = self.conn, self._h11
        assert conn is not None and connection is not None
        while True:
            event = connection.next_event()
            if event is not h11.NEED_DATA:
                return event
            data = await conn.reader.read(READ_SIZE)
            connection.receive_data(data)

    async def _head(self) -> None:
        try:
            while True:
                event = await self._event()
                if isinstance(event, h11.InformationalResponse):
                    continue
                break
        except (OSError, h11.RemoteProtocolError) as err:
            raise _NoAnswer(f"{self.module} gave no answer: {err!r}") from None
        if not isinstance(event, h11.Response):
            raise _NoAnswer(f"{self.module} closed the connection with no answer")
        self.status = event.status_code
        self.headers = [(name.lower(), value) for name, value in event.headers]

    def _discard(self) -> None:
        self._complete = False
        self._closed = True
        if self.conn is not None:
            self.conn.close()
        if self.use is not None:
            self.use.end()


class Client:
    """Requests to modules' page servers, through `starters` (the shell's `Starters`)."""

    def __init__(self, starters: Any):
        self.starters = starters

    async def request(self, module: str, method: str, target: str, *,
                      headers: Iterable[tuple[bytes, bytes]] = (),
                      body: bytes | AsyncIterable[bytes] = b"", act: bool = False,
                      stream: bool = False) -> Response:
        """Send one request to `module`'s server and return its `Response` once the
        answer's head is in. `target` is the upstream path and query. `headers` are the
        browser's; only the allowlist of them is sent. `act` sends it on a newly proved
        connection within 0.25 s of the proof; `stream` gives it a connection of its own,
        no read deadline, and the drain's cut.

        Raises `UpstreamError` with the browser's answer when the server gave none, and
        `BrowserGone` when the body ran out from under it. The caller ends the returned
        `Response` with `aclose()`, so a drain can go on.
        """
        browser = list(headers)
        retry_unsent = True
        retry_sent = method == "GET" and isinstance(body, (bytes, bytearray))
        pooled = not act and not stream
        while True:
            server = await self._ensure(module)
            response = Response(module, server, stream=stream)
            try:
                response.use = server.use(stream=stream, close=response._cut)
                await response._connect(pooled=pooled, act=act)
            except (NotOurServer, OSError, _NotSent) as err:
                # Nothing but a proof went out: drop the server and start one more.
                response._discard()
                self.starters.lost(server)
                if retry_unsent:
                    _log.info("%s: %s; starting again and sending once more", module, err)
                    retry_unsent = False
                    continue
                raise UpstreamError(502, {"error": "unreachable", "module": module,
                                          "log": self.starters.log}) from None
            except BaseException:
                # Cancelled (or failed otherwise) while the connection was proved: end the
                # request's count, or a drain would wait for a request that is gone.
                response._discard()
                raise
            sent = _framed(upstream_headers(browser, host=_host(server), token=server.info.token),
                           method, body)
            try:
                await response._send(method, target, sent, body)
                if stream:
                    await response._head()
                else:
                    async with asyncio.timeout(FIRST_BYTE_WITHIN):
                        await response._head()
            except _NotSent as err:
                response._discard()
                raise UpstreamError(502, {"error": "unreachable", "module": module,
                                          "log": self.starters.log}) from err
            except _NoAnswer as err:
                response._discard()
                if retry_sent:
                    _log.info("%s: %s; sending the GET once more", module, err)
                    retry_sent = False
                    pooled = False
                    continue
                _log.warning("%s: %s; the module may have acted", module, err)
                raise UpstreamError(502, {"error": "upstream", "module": module,
                                          "log": self.starters.log}) from None
            except TimeoutError:
                response._discard()
                _log.warning("%s: no answer within %g s", module, FIRST_BYTE_WITHIN)
                raise UpstreamError(504, {"error": "timeout", "module": module}) from None
            except BaseException:
                response._discard()
                raise
            return response

    async def _ensure(self, module: str) -> Server:
        try:
            return await self.starters.ensure(module)
        except StartFailed as failure:
            raise UpstreamError(502, failure.body()) from None
        except ServerGone:
            raise UpstreamError(503, {"error": "stopping", "module": module}) from None


def _framed(headers: list[tuple[bytes, bytes]], method: str,
            body: bytes | AsyncIterable[bytes]) -> list[tuple[bytes, bytes]]:
    """`headers` with the body's framing: the browser's `Content-Length` when it gave one,
    else the length of a held body, else chunked for a POST's streamed one."""
    if any(name.lower() == b"content-length" for name, _ in headers):
        return headers
    if isinstance(body, (bytes, bytearray)):
        if body or method == "POST":
            return [*headers, (b"content-length", str(len(body)).encode("ascii"))]
        return headers
    return [*headers, (b"transfer-encoding", b"chunked")]


async def _read_all(receive: Callable[..., Any], cap: int) -> bytes | None:
    body = b""
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            raise BrowserGone("the browser went away")
        body += message.get("body", b"")
        if len(body) > cap:
            return None
        if not message.get("more_body"):
            return body


async def _streamed(receive: Callable[..., Any]) -> AsyncIterator[bytes]:
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            raise BrowserGone("the browser went away")
        chunk = message.get("body", b"")
        if chunk:
            yield chunk
        if not message.get("more_body"):
            return


async def _disconnected(receive: Callable[..., Any]) -> None:
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return


async def _plain(send: Callable[..., Any], status: int) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": response_headers(extra=[("Content-Length", "0")])})
    await send({"type": "http.response.body", "body": b""})


class Proxy:
    """The proxy as the router's ASGI app for `/m/<module>/<rest>`.

    `starters` is the shell's `Starters`; `table()` gives the shell's current route
    table, in which the request's entry says whether it is an act or a stream. A request
    the table no longer holds (the snapshot changed since `check`) is an empty 404.
    """

    def __init__(self, starters: Any, *, table: Callable[[], Table]):
        self.client = Client(starters)
        self.table = table

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any],
                       send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            return
        method = scope.get("method", "")
        raw = scope.get("raw_path") or b""
        parts = raw.split(b"/", 3)
        entry = self.table().find(method, raw)
        if entry is None or len(parts) < 4 or parts[1] != b"m":
            await _plain(send, 404)
            return
        module = parts[2].decode("ascii")
        target = b"/" + parts[3]
        query = scope.get("query_string") or b""
        if not query.isascii():
            await _plain(send, 400)
            return
        if query:
            target += b"?" + query
        headers = list(scope.get("headers") or ())
        try:
            if method == "GET":
                body: bytes | AsyncIterable[bytes] | None = await _read_all(receive, BODY_CAP_GET)
                if body is None:
                    await _plain(send, 413)
                    return
            else:
                body = _streamed(receive)
            response = await self.client.request(
                module, method, target.decode("ascii"), headers=headers, body=body,
                act=entry.kind == "act", stream=entry.stream)
        except BrowserGone:
            return
        except UpstreamError as err:
            status, pairs, data = err.answer()
            await send({"type": "http.response.start", "status": status, "headers": pairs})
            await send({"type": "http.response.body", "body": data})
            return
        async with response:
            await _relay(response, receive, send)


async def _relay(response: Response, receive: Callable[..., Any],
                 send: Callable[..., Any]) -> None:
    """Pass the answer to the browser as it arrives, until it ends, the browser goes away,
    or a drain cuts it. An answer that breaks off leaves the browser's response unfinished,
    which cuts its connection."""

    async def pump() -> bool:
        await send({"type": "http.response.start", "status": response.status,
                    "headers": downstream(response.headers)})
        try:
            async for chunk in response.chunks():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        except UpstreamBroken as err:
            _log.warning("%s", err)
            return False
        await send({"type": "http.response.body", "body": b""})
        return True

    pumping = asyncio.ensure_future(pump())
    watching = asyncio.ensure_future(_disconnected(receive))
    try:
        await asyncio.wait({pumping, watching}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (pumping, watching):
            if not task.done():
                task.cancel()
        for result in await asyncio.gather(pumping, watching, return_exceptions=True):
            if isinstance(result, Exception):
                _log.error("relaying %s's answer failed", response.module, exc_info=result)

