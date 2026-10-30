"""comms' wire: its version, capabilities and error codes, and the daemon socket's host.

Both comms sockets speak newline-delimited JSON-RPC 2.0 through `moxi.ipc`. Every
connection opens with `rpc.hello`, which `moxi.ipc.serve` answers itself: the client
offers a range of comms wire versions, the server answers the highest common one and its
own capabilities, or refuses with -32001 naming both ranges. comms owns its method names,
its error codes and its capability names; the framing is the core's.

A version is raised only for a breaking change. An addition is a new capability, so a
session launched before an upgrade keeps working with a restarted daemon when the change
was only an addition.

The error codes sit in the application range, outside JSON-RPC's reserved block:
refusals 1101-1149 and try-again 1150-1199. A client branches on the range, and on the
code only where it cares which refusal. An error's `data` carries `reason` (the text a
verb prints) and, where a refusal names something, that thing.

`Host` serves the daemon socket: an asyncio loop on a thread of its own runs
`moxi.ipc.serve`, and each comms method runs on a bounded pool of threads, so the
daemon's routing core and its locks run as they did under one thread per connection.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from moxi import ipc

log = logging.getLogger("comms.wire")

#: The comms wire versions this build speaks, both ends included. It covers the daemon
#: socket and the claim socket, which ship in one package and change together.
VERSIONS = (1, 1)

#: The daemon's capabilities at version 1. They are all present in version 1; they are
#: listed so a later client tests a name, not a version.
CAPABILITIES = frozenset({"delete", "expiry", "interrupt", "reserved-names"})

# -- error codes -------------------------------------------------------------------------
NAME_HELD = 1101
NAME_RESERVED = 1102
NAME_RETIRED = 1103
NAME_UNKNOWN = 1104
NOT_MAILABLE = 1105
CREDENTIAL = 1106
TOO_LONG = 1107
NOT_SENDER = 1108
ALREADY_DELIVERED = 1109
UNKNOWN_MESSAGE = 1110
UNKNOWN_ADAPTER = 1111
NOT_BOUND = 1112
STOPPING = 1150
FAILED = 1151
BUSY = 1152
STORE_BUSY = 1153

#: Each code's name, as docs and logs write it.
CODE_NAMES = {
    NAME_HELD: "name-held", NAME_RESERVED: "name-reserved", NAME_RETIRED: "name-retired",
    NAME_UNKNOWN: "name-unknown", NOT_MAILABLE: "not-mailable", CREDENTIAL: "credential",
    TOO_LONG: "too-long", NOT_SENDER: "not-sender", ALREADY_DELIVERED: "already-delivered",
    UNKNOWN_MESSAGE: "unknown-message", UNKNOWN_ADAPTER: "unknown-adapter",
    NOT_BOUND: "not-bound", STOPPING: "stopping", FAILED: "failed", BUSY: "busy",
    STORE_BUSY: "store-busy",
}

#: JSON-RPC's own codes that comms answers with, and the core's hello refusal.
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
VERSION_REFUSED = -32001


def is_refusal(code: int) -> bool:
    """A refusal: the daemon answered and said no, and asking again won't change it."""
    return 1101 <= code <= 1149


def is_try_again(code: int) -> bool:
    """The daemon committed nothing for the request; the same request may work later."""
    return 1150 <= code <= 1199


def error(code: int, reason: str, **data: Any) -> ipc.RpcError:
    """The `RpcError` for comms code `code`: its message is `reason`, and its `data`
    carries `reason` and whatever the refusal names."""
    return ipc.RpcError(code, reason, {"reason": reason, **data})


# -- the version refusal, in the client's words ---------------------------------------------

def version_text(data: object, ours: tuple[int, int] = VERSIONS) -> str:
    """What a client says when the daemon refused its hello with -32001: which side is
    older, and the cure for that side. `data` is the refusal's data,
    `{"client": [lo, hi], "server": [lo, hi]}`."""
    client = _pair(data, "client") or ours
    server = _pair(data, "server")
    if server is None:
        return (f"the comms daemon refused this comms' wire {client[0]}-{client[1]}. "
                "Nothing was sent.")
    if client[0] > server[1]:
        return (f"the comms daemon running speaks comms wire {server[0]}-{server[1]} and "
                f"this comms speaks {client[0]}-{client[1]}: the daemon is older. Restart "
                "it (`moxi comms service restart`), then run this again. Nothing was sent.")
    return (f"this comms speaks comms wire {client[0]}-{client[1]} and its daemon speaks "
            f"{server[0]}-{server[1]}: this program is older than the daemon. Relaunch the "
            "session (an adapter) or update this install (a verb). Nothing was sent.")


def _pair(data: object, key: str) -> tuple[int, int] | None:
    value = data.get(key) if isinstance(data, dict) else None
    if (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in value)):
        return value[0], value[1]
    return None


# -- the daemon socket's host -----------------------------------------------------------------

#: Past this many counted connections, a connection's first comms method is answered
#: `busy` and the connection closed. A connection is counted at its first comms method,
#: since the hello never reaches comms, and uncounted when it closes.
MAX_CONNECTIONS = 256
#: Frames one connection may have waiting to be written, and the seconds one write may
#: take, before `moxi.ipc` drops the peer.
QUEUE = 256
WRITE_DEADLINE = 5.0
#: The threads comms' methods run on.
WORKERS = 32
#: How long `Host.start` waits for the socket to be served, and `Host.close` for every
#: connection to end: the write deadline, and room for the releases that follow.
START_WAIT = 10.0
CLOSE_WAIT = WRITE_DEADLINE + 3.0


class Then:
    """A method's answer and the work that follows it on the same connection. The answer
    is written first; `after` runs next on the pool, and the connection's next message
    waits for it. A bind answers with its token, then hands the name its catch-up."""

    def __init__(self, answer: object, after: Callable[[], object]):
        self.answer = answer
        self.after = after


class Host:
    """Serves one address with `moxi.ipc.serve` on a loop of its own thread.

    `call(conn, method, params)` runs on a pool thread for every request and notification
    once the host is open, and returns the answer (or a `Then`) or raises `RpcError`.
    `closed(conn)` runs on a pool thread once a connection has closed. `start` serves the
    address and takes its lock, so a second daemon refuses at once; methods wait until
    `open`, so nothing is answered before the daemon has loaded what it routes from.
    """

    def __init__(self, address: str, call: Callable[[ipc.Connection, str, object], object],
                 closed: Callable[[ipc.Connection], object], *,
                 max_connections: int = MAX_CONNECTIONS, workers: int = WORKERS):
        self.address = address
        self._call = call
        self._closed_cb = closed
        self.max_connections = max_connections
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="comms-wire")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._gate: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        #: Loop-only state: the connections counted toward the cap, and each one's work
        #: still running after its last answer.
        self._counted: set = set()
        self._tails: dict = {}
        #: Each closed connection's release, from when it is handed to the pool until it
        #: ends. `close` waits for them: `moxi.ipc` stops waiting for a connection once
        #: its own work is done, and a release still running then must not outlive the
        #: daemon's stop.
        self._releases: set = set()
        self._releases_lock = threading.Lock()

    # -- life ---------------------------------------------------------------------------

    def start(self) -> None:
        """Serve the address. Raises `moxi.ipc.AlreadyServing` when another server holds
        it, and `OSError` when it can't be served."""
        ready: concurrent.futures.Future = concurrent.futures.Future()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True,
                                        name="comms-daemon-loop")
        self._thread.start()
        try:
            ready.result(timeout=START_WAIT)
        except BaseException:
            self.close()
            raise

    def open(self) -> None:
        """Let methods through: everything they read has been loaded."""
        self._soon(lambda: self._gate.set())

    def close(self, timeout: float = CLOSE_WAIT) -> bool:
        """Stop serving: no new connections, every open one ended without waiting on a
        peer past the write deadline, each one's `closed` run, the socket removed. True
        when it finished within `timeout`."""
        deadline = time.monotonic() + timeout
        if self._server is not None:
            self._soon(self._server.close)
        done = True
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
            done = not self._thread.is_alive()
        with self._releases_lock:
            releases = list(self._releases)
        if releases:
            _, waiting = concurrent.futures.wait(
                releases, timeout=max(deadline - time.monotonic(), 0.0))
            done = done and not waiting
        self._pool.shutdown(wait=False, cancel_futures=True)
        return done

    @property
    def connections(self) -> int:
        """The connections counted toward the cap now."""
        return len(self._counted)

    def _soon(self, fn: Callable[[], object]) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(fn)
        except RuntimeError:
            pass  # the loop has closed

    def _run(self, ready: concurrent.futures.Future) -> None:
        from moxi import aio

        async def main() -> None:
            self._gate = asyncio.Event()
            try:
                server = await ipc.serve(
                    self.address, self._handle, hello=ipc.Hello(VERSIONS, CAPABILITIES),
                    write_deadline=WRITE_DEADLINE, queue=QUEUE, on_close=self._on_close)
            except BaseException as exc:
                ready.set_exception(exc)
                return
            self._loop = asyncio.get_running_loop()
            self._server = server
            ready.set_result(None)
            await server.serve_forever()

        try:
            aio.run(main)
        except BaseException as exc:  # noqa: BLE001 - the thread's end, logged
            if not ready.done():
                ready.set_exception(exc)
            else:
                log.exception("the daemon socket's loop failed")

    # -- one connection -----------------------------------------------------------------

    async def _handle(self, conn: ipc.Connection, method: str, params: object) -> object:
        if not self._gate.is_set():
            await self._gate.wait()
        if conn not in self._counted:
            if len(self._counted) >= self.max_connections:
                log.warning("refused a connection from pid %s: %d connections are open",
                            conn.peer.pid, len(self._counted))
                # After this answer is queued: closing first would drop it.
                asyncio.get_running_loop().call_soon(conn.close)
                raise error(BUSY, f"the comms daemon has {self.max_connections} connections "
                                  "open; try again in a moment")
            self._counted.add(conn)
        await self._settle_tail(conn)
        answer = await asyncio.wrap_future(self._pool.submit(self._call, conn, method,
                                                             params))
        if isinstance(answer, Then):
            self._tails[conn] = self._pool.submit(answer.after)
            return answer.answer
        return answer

    async def _settle_tail(self, conn: ipc.Connection) -> None:
        tail = self._tails.pop(conn, None)
        if tail is None:
            return
        try:
            await asyncio.wrap_future(tail)
        except Exception:  # noqa: BLE001 - the work logs its own failure
            log.exception("the work after an answer failed")

    async def _on_close(self, conn: ipc.Connection) -> None:
        self._counted.discard(conn)
        tail = self._tails.pop(conn, None)

        def release() -> None:
            # The work after the connection's last answer first, then its release.
            if tail is not None:
                try:
                    tail.result()
                except Exception:  # noqa: BLE001 - the work logs its own failure
                    log.exception("the work after an answer failed")
            self._closed_cb(conn)

        # Handed to the pool before the first wait, so a cancelled close still releases.
        try:
            future = self._pool.submit(release)
        except RuntimeError:
            # The pool is already shut down: run it here rather than lose the release.
            release()
            return
        with self._releases_lock:
            self._releases.add(future)
        future.add_done_callback(self._released)
        # Shielded: cancelling this wait must not cancel a release not yet started.
        await asyncio.shield(asyncio.wrap_future(future))

    def _released(self, future: concurrent.futures.Future) -> None:
        with self._releases_lock:
            self._releases.discard(future)


__all__ = ["VERSIONS", "CAPABILITIES", "CODE_NAMES", "Host", "Then", "error",
           "is_refusal", "is_try_again", "version_text", "MAX_CONNECTIONS", "QUEUE",
           "WRITE_DEADLINE"]
