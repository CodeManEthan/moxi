"""The endpoint of comms' tools: MCP over HTTP on loopback, served by the daemon.

The daemon imports this module only at its start, and only when `[mcp] enabled` is true:
the MCP SDK and uvicorn take a third of a second to import, and no hook, verb, adapter or
launch path ever pays that.

What the endpoint is:

  * the SDK's own app, `MCPServer("comms", subscriptions=False).streamable_http_app(...)`,
    in stateless JSON mode: no MCP session lives between requests and no stream is held
    open, so a daemon restart loses nothing a client holds. Two requests would open a
    server-to-client stream, and the endpoint refuses both: a `GET` is answered 405, and
    `subscriptions/listen`, the 2026-07-28 protocol's notification stream, is left
    unserved, so the SDK answers it at once with a method-not-found error as JSON and
    advertises no `listChanged` and no resource `subscribe`. The tools send no
    notifications and their list never changes, so neither stream would carry anything;
    served, each would hold a connection per session, and the stop would wait on them;
  * uvicorn without its main loop. `uvicorn.Server.serve()` wakes ten times a second for
    its housekeeping; the endpoint instead takes the two steps `serve()` would
    (`config.load()`, then `server.lifespan = config.lifespan_class(config)`), calls
    `server.startup(sockets=[sock])` at the start and `server.shutdown()` at the stop, and
    never calls `serve()`, so it installs no signal handlers and never ticks. The date
    header, which the tick refreshes, is off;
  * on the daemon socket host's loop (`wire.Host`): it starts there before the host lets
    methods through, and the host's stop hook shuts it down before the daemon socket
    closes. Nothing here uses the loop's default executor; blocking work runs on the
    host's pool;
  * at `127.0.0.1` only. The SDK's DNS-rebinding check, on by itself for that host,
    answers a foreign `Host` 421 and a foreign `Origin` 403;
  * behind the SDK's bearer check with comms' verifier: the bearer value's hash is looked
    up in the daemon's registry of tool tokens (`tokens.Registry.verify`), and a token no
    active row has is answered 401 before any tool runs. The verifier is the whole check,
    so the SDK's own resource check is off (`validate_token_resource=False`);
  * bounded: 64 open requests (more get 503), keep-alives closed after 5 s, a request
    body of at most 256 kB (a message is at most 16,000 characters).

An endpoint that fails after it started is not restarted here: `on_failure` tells the
daemon, which logs it and exits nonzero, and its supervisor starts it again.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

log = logging.getLogger("comms.mcp.server")

#: The one address the endpoint serves on. Not a setting: the endpoint never faces
#: anything but this machine.
ADDRESS = "127.0.0.1"
#: The path of the endpoint on that address.
PATH = "/mcp"
#: The largest request body accepted.
MAX_BODY = 256 * 1024
#: Open requests past this many are answered 503.
LIMIT_CONCURRENCY = 64
#: Seconds an idle keep-alive connection stays open.
KEEP_ALIVE = 5
#: Seconds the stop gives requests in flight before it cancels them.
GRACE = 3
#: The backstop on the whole stop, uvicorn's grace and the app's own shutdown included.
STOP_WAIT = GRACE + 2.0
#: The listening socket's backlog.
BACKLOG = 128
#: Bearer checks run at once on the host's pool. A check is a hash and a lookup, plus
#: file reads when its session may have ended; a flood of requests holds at most this
#: many of the pool's threads.
VERIFY_AT_ONCE = 4
#: The scope every token carries.
SCOPE = "comms"
#: Loggers of the libraries, kept at warnings: at info they write a line per request.
_QUIET = ("mcp", "uvicorn", "uvicorn.error", "uvicorn.access", "sse_starlette", "httpx2")


def url_for(port: int) -> str:
    """The endpoint's URL on `port`: what the daemon answers and a launch stamps."""
    return f"http://{ADDRESS}:{port}{PATH}"


def bind(port: int) -> socket.socket:
    """A listening TCP socket on `127.0.0.1:port` (0: any free port), non-blocking.

    With `SO_REUSEADDR` on POSIX, so the TIME_WAIT sockets an earlier daemon's
    keep-alives left don't refuse the bind (on Linux it still refuses a port another
    socket listens on), and `SO_EXCLUSIVEADDRUSE` on Windows, so no other process binds
    the same address beside it. Raises OSError when the port can't be bound."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":                                    # pragma: no cover - Windows
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((ADDRESS, port))
        sock.listen(BACKLOG)
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


class Verifier:
    """comms' bearer check, as the SDK's `TokenVerifier`: the token's active row maps the
    call to one identity, or the call is answered 401.

    `verify(token)` is the registry's (`tokens.Registry.verify`): a row, or None. It may
    read claim files and process tables, and revoke a row whose session has ended, so it
    runs on `pool`, never on the loop. `opened()` waits until the daemon lets methods
    through, so no call is placed before the daemon has loaded what it routes from.

    The value stays in the request's memory: it is never logged or stored."""

    def __init__(self, verify: Callable[[str], Any], pool: concurrent.futures.Executor,
                 opened: Callable[[], Awaitable[object]] | None = None):
        self._verify = verify
        self._pool = pool
        self._opened = opened
        self._at_once: asyncio.Semaphore | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        if self._opened is not None:
            await self._opened()
        if self._at_once is None:
            self._at_once = asyncio.Semaphore(VERIFY_AT_ONCE)
        async with self._at_once:
            try:
                row = await asyncio.wrap_future(self._pool.submit(self._verify, token))
            except Exception as e:  # noqa: BLE001 - a check that fails lets nothing in
                log.warning("a tool token couldn't be checked (%s); the call is refused",
                            type(e).__name__)
                return None
        if row is None:
            return None
        return AccessToken(token=token, client_id=row.identity, scopes=[SCOPE],
                           claims={"claim": row.claim_key})


def build_app(verifier: Verifier, url: str, *,
              tools: Callable[[MCPServer], object] | None = None):
    """The ASGI app: the SDK's server named `comms`, its tools added by `tools(server)`,
    in stateless JSON mode behind comms' bearer check, with `GET` answered 405 and
    `subscriptions/listen` left unserved."""
    auth = AuthSettings(issuer_url=url, resource_server_url=url,
                        validate_token_resource=False)
    server = MCPServer("comms", token_verifier=verifier, auth=auth, subscriptions=False)
    if tools is not None:
        tools(server)
    app = server.streamable_http_app(streamable_http_path=PATH, host=ADDRESS,
                                     stateless_http=True, json_response=True,
                                     max_request_body_size=MAX_BODY)
    return _NoStreams(app)


class _NoStreams:
    """The app with `GET` on the endpoint answered 405, as the transport allows a server
    that offers no server-to-client stream: in stateless mode the SDK would hold such a
    stream open for as long as the client keeps it, an fd per session for nothing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if (scope["type"] == "http" and scope.get("method") == "GET"
                and scope.get("path") == PATH):
            await send({"type": "http.response.start", "status": 405,
                        "headers": [(b"allow", b"POST"), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)


def _config(app) -> uvicorn.Config:
    return uvicorn.Config(
        app, http="h11", ws="none", lifespan="on", log_config=None, access_log=False,
        proxy_headers=False, server_header=False, date_header=False,
        limit_concurrency=LIMIT_CONCURRENCY, timeout_keep_alive=KEEP_ALIVE,
        timeout_graceful_shutdown=GRACE, backlog=BACKLOG)


class StartFailed(Exception):
    """The app couldn't start; the endpoint serves nothing."""


class Endpoint:
    """One endpoint on one listening socket, run on the host's loop.

    `start()` and `stop()` are coroutines for that loop. `on_failure(why)` is called on
    the loop, once, when the endpoint stops serving after it started without `stop()`
    asking: the daemon then exits for its supervisor to restart it."""

    def __init__(self, sock: socket.socket, verifier: Verifier, *,
                 tools: Callable[[MCPServer], object] | None = None,
                 on_failure: Callable[[str], object] | None = None):
        self.sock = sock
        self.port = sock.getsockname()[1]
        self.url = url_for(self.port)
        self._verifier = verifier
        self._tools = tools
        self._on_failure = on_failure
        self._server: uvicorn.Server | None = None
        self._watch: asyncio.Task | None = None
        self._stopping = False
        self.started = False

    async def start(self) -> None:
        """Start serving on the socket. Raises StartFailed, with the socket closed,
        when the app can't start."""
        for name in _QUIET:
            logger = logging.getLogger(name)
            if logger.level == logging.NOTSET:
                logger.setLevel(logging.WARNING)
        try:
            config = _config(build_app(self._verifier, self.url, tools=self._tools))
            config.load()
            server = uvicorn.Server(config)
            server.lifespan = config.lifespan_class(config)
            # uvicorn exits the process (SystemExit) when the app's startup fails; caught
            # here, in this coroutine, so it never reaches the loop.
            await server.startup(sockets=[self.sock])
        except SystemExit:
            self.sock.close()
            raise StartFailed("the app's startup failed") from None
        except Exception as e:
            self.sock.close()
            raise StartFailed(_first_line(e)) from None
        if not server.started:
            self.sock.close()
            raise StartFailed("the HTTP server didn't start")
        self._server = server
        self.started = True
        self._watch = asyncio.get_running_loop().create_task(self._watch_app(server))

    async def _watch_app(self, server: uvicorn.Server) -> None:
        """Wait, without waking, for the app's lifespan to end. Ended while nobody asked
        it to stop, the endpoint has failed."""
        await server.lifespan.shutdown_event.wait()
        if self._stopping:
            return
        log.error("comms' tools endpoint at %s stopped serving: its app failed",
                  self.url)
        await self._close(server)
        if self._on_failure is not None:
            try:
                self._on_failure("failed")
            except Exception:  # noqa: BLE001 - the daemon's own reaction
                log.exception("the endpoint's failure couldn't be reported")

    async def stop(self) -> None:
        """Stop serving: no new connections, requests in flight given `GRACE` seconds,
        then the app's own shutdown, all within `STOP_WAIT`. Does nothing when the
        endpoint never started; once only."""
        server = self._server
        if server is None or self._stopping:
            return
        self._stopping = True
        if self._watch is not None:
            self._watch.cancel()
        try:
            await asyncio.wait_for(server.shutdown(), timeout=STOP_WAIT)
        except TimeoutError:
            log.warning("comms' tools endpoint didn't stop within %.0f s", STOP_WAIT)
        except Exception:  # noqa: BLE001 - the daemon's stop goes on
            log.exception("comms' tools endpoint failed while stopping")
        finally:
            await self._close(server)

    async def _close(self, server: uvicorn.Server) -> None:
        """The listener closed, whatever else happened: the port is free."""
        for listener in getattr(server, "servers", ()):
            listener.close()
        self.sock.close()


def _first_line(e: BaseException) -> str:
    text = str(e).strip().splitlines()
    return f"{type(e).__name__}: {text[0]}" if text else type(e).__name__


__all__ = ["ADDRESS", "Endpoint", "GRACE", "LIMIT_CONCURRENCY", "MAX_BODY", "PATH",
           "StartFailed", "Verifier", "bind", "build_app", "url_for"]
