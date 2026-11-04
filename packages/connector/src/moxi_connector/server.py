"""The door's HTTP server: uvicorn on the listening socket `listen.take` gave, serving the
`Front` (`moxi_connector.app`).

uvicorn runs without its main loop, as comms' tools do: `serve()` would wake ten times a
second for its housekeeping, so the endpoint takes the steps `serve()` would take, calls
`startup(sockets=[sock])` at the start and `shutdown()` at the stop, and never calls
`serve()`. It installs no signal handlers and never ticks; the date header, which the tick
refreshes, is off.

The bounds: at most `LIMIT_CONCURRENCY` open requests, past which uvicorn answers 503;
keep-alives closed after `KEEP_ALIVE` seconds. uvicorn reads no proxy header
(`proxy_headers=False`), so no forwarded header changes the client address either.
"""

from __future__ import annotations

import asyncio
import logging
import socket

import uvicorn

from . import listen

log = logging.getLogger("moxi.connector.server")

#: Open requests past this many are answered 503.
LIMIT_CONCURRENCY = 32
#: Seconds an idle keep-alive connection stays open.
KEEP_ALIVE = 5
#: Seconds the stop gives requests in flight before it cancels them.
GRACE = 3
#: The backstop on the whole stop.
STOP_WAIT = GRACE + 2.0
#: Loggers of the libraries, kept at warnings: at info they write a line per request.
_QUIET = ("mcp", "uvicorn", "uvicorn.error", "uvicorn.access", "sse_starlette", "httpx2")


class StartFailed(Exception):
    """The HTTP server couldn't start; the door serves nothing."""


def config(app) -> uvicorn.Config:
    return uvicorn.Config(
        app, http="h11", ws="none", lifespan="off", log_config=None, access_log=False,
        proxy_headers=False, server_header=False, date_header=False,
        limit_concurrency=LIMIT_CONCURRENCY, timeout_keep_alive=KEEP_ALIVE,
        timeout_graceful_shutdown=GRACE, backlog=listen.BACKLOG)


class Endpoint:
    """uvicorn serving `app` on `sock`, on the running loop."""

    def __init__(self, sock: socket.socket, app):
        self.sock = sock
        self.port = sock.getsockname()[1]
        self.app = app
        self._server: uvicorn.Server | None = None
        self._stopping = False

    async def start(self) -> None:
        """Start serving. Raises StartFailed, with the socket closed, when it can't."""
        for name in _QUIET:
            logger = logging.getLogger(name)
            if logger.level == logging.NOTSET:
                logger.setLevel(logging.WARNING)
        try:
            cfg = config(self.app)
            cfg.load()
            server = uvicorn.Server(cfg)
            server.lifespan = cfg.lifespan_class(cfg)
            await server.startup(sockets=[self.sock])
        except SystemExit:
            self.sock.close()
            raise StartFailed("the HTTP server's startup failed") from None
        except Exception as e:
            self.sock.close()
            raise StartFailed(f"{type(e).__name__}: {e}") from None
        if not server.started:
            self.sock.close()
            raise StartFailed("the HTTP server didn't start")
        self._server = server

    async def stop(self) -> None:
        """Stop serving: no new connections, requests in flight given `GRACE` seconds, all
        within `STOP_WAIT`; then the listener is closed. Once only."""
        server = self._server
        if server is None or self._stopping:
            return
        self._stopping = True
        try:
            await asyncio.wait_for(server.shutdown(), timeout=STOP_WAIT)
        except TimeoutError:
            log.warning("the door didn't stop within %.0f s", STOP_WAIT)
        except Exception:  # noqa: BLE001 - the process's stop goes on
            log.exception("the door failed while stopping")
        finally:
            for listener in getattr(server, "servers", ()):
                listener.close()
            self.sock.close()


__all__ = ["Endpoint", "GRACE", "KEEP_ALIVE", "LIMIT_CONCURRENCY", "StartFailed", "config"]
