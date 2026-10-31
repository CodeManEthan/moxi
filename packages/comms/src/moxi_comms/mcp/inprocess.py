"""How comms' tools reach the routing core: in the daemon's own process, never back
through its socket.

A tool call runs `sending.run` (or `sending.delete`, or a read) on one thread of the
daemon socket host's pool, the thread the tool's body was handed to. The pieces here
keep it on that one thread:

  * `InProcessClient`: the daemon client `sending.Context(daemon=)` takes. Its `call`
    runs `Daemon._call` directly on the calling thread, the same dispatch the host's pool
    runs for a socket request, under a connection with no peer. A request through it and
    the same request over the socket get the same answer and the same `DaemonError`
    classes from the same codes, so `sending.run` branches as it does for the verbs. It
    serves the reads, `send` and `delete`; an adapter's methods (`bind` among them) are
    refused, since nothing here holds a binding. It refuses, before dispatch, a request
    whose `as` names a reserved name or that names the operator's `user`: no tool sends
    under a reserved name or on the operator's lane.
  * `bound_sender`: the `sending.Sender.bound` a tool sends as, from the daemon's live
    binding for the claim its token's row names. Its credential is the binding token's
    hash, which the daemon keeps and accepts in place of the token only from a
    connection in its own process; it keeps no binding's token.
  * `context`: a `sending.Context` on the in-process client and the daemon's own record.
  * `Calls`: where a tool's synchronous body runs: the host's pool, at most
    `TOOLS_AT_ONCE` bodies at once, so at least `wire.WORKERS - TOOLS_AT_ONCE` of the
    pool's threads stay free for adapters and the verbs whatever the tools do. A call
    past the limit waits on the loop and holds no thread.

Nothing here imports the MCP SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from moxi import ipc

from .. import directory, reserved, sending, wire
from ..client import DaemonClient, DaemonError, DaemonOffline, _error
from ..daemon import READS
from ..scoping import identity_of

log = logging.getLogger("comms.mcp.inprocess")

#: Tool bodies that run at once on the host's pool. The pool has `wire.WORKERS` (32)
#: threads; the rest stay for adapters' binds, acks and turn ends and the verbs.
TOOLS_AT_ONCE = 8

#: The daemon methods a tool reaches: the reads, `send` and `delete`.
SERVED = frozenset({*READS, "send", "delete", "mcp_endpoint"})


class InProcessConnection:
    """The connection an in-process request runs under: no peer process to place, and no
    binding. A method that asks for its connection's binding finds none."""

    peer = None
    in_process = True

    def close(self) -> None:
        """Nothing to close: no socket stands behind it."""

    def __repr__(self) -> str:
        return "<in-process connection>"


class InProcessClient(DaemonClient):
    """A daemon client that runs each request in this process, on the calling thread.

    It is always connected while the daemon is: `connect` and `close` do nothing, so one
    client serves every call a `sending.Context` makes. Params and answers pass through
    JSON as they would over the socket, so a caller sees the same shapes. `timeout` is
    ignored: the call runs on the caller's own thread and returns when it is done."""

    def __init__(self, daemon):
        super().__init__(socket_path=str(daemon.socket_path))
        self._daemon = daemon
        self._conn = InProcessConnection()
        self.capabilities = frozenset(wire.CAPABILITIES)

    def connect(self) -> "InProcessClient":
        """This client, while the daemon runs. Raises `DaemonOffline` once it has
        stopped, as a connect to its socket would."""
        if not self.connected:
            raise DaemonOffline("comms offline: the daemon has stopped")
        return self

    def close(self) -> None:
        """Nothing to close: the next call runs as the first did."""

    @property
    def connected(self) -> bool:
        return self._daemon.host is not None

    def notify(self, method: str, params: dict | None = None) -> None:
        """No notification is served in process: they are an adapter's, and nothing
        here holds a binding."""
        raise DaemonError(f"{method} isn't served in comms' own process",
                          wire.METHOD_NOT_FOUND, {"method": method})

    def _call(self, method: str, params: dict | None, *, timeout: float | None = None,
              no_answer=None) -> dict:
        if method not in SERVED:
            raise DaemonError(f"{method} isn't served in comms' own process",
                              wire.METHOD_NOT_FOUND, {"method": method})
        params = _wire_copy(dict(params or {}))
        self._refuse_lanes(params)
        if not self.connected:
            raise DaemonOffline("comms offline: the daemon has stopped")
        try:
            answer = self._daemon._call(self._conn, method, params)
        except ipc.RpcError as e:
            raise _error(ipc.RpcError(e.code, e.message, _wire_copy(e.data))) from None
        if isinstance(answer, wire.Then):
            # Only `bind` answers one, and it isn't served here.
            raise DaemonError(f"{method} answered work for a connection; nothing here "
                              "holds one", wire.FAILED, {"method": method})
        answer = _wire_copy(answer)
        return answer if isinstance(answer, dict) else {}

    def _refuse_lanes(self, params: dict) -> None:
        """Refuse, before the daemon sees it, a request on a lane no tool takes: `as` a
        reserved name (the daemon refuses it too, since this connection has no peer to
        place), or the operator's `user`."""
        if "user" in params:
            reason = "the operator's lane isn't reachable from comms' tools"
            raise DaemonError(reason, wire.CREDENTIAL, {"reason": reason})
        who = params.get("as")
        if not isinstance(who, str) or not who:
            return
        names = {who, identity_of(who)}
        try:
            conn = directory.connect(self._daemon.db_path, read_only=True)
            try:
                held = [name for name in names if reserved.lookup(conn, name) is not None]
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 - a read that fails sends nothing
            log.warning("the reserved names couldn't be read (%s); the request is refused",
                        type(e).__name__)
            raise DaemonError("comms couldn't read its reserved names; try again in a "
                              "moment", wire.FAILED, {}) from None
        if held:
            reason = (f"{held[0]} is a reserved name: nothing sends or deletes under it "
                      "from comms' tools")
            raise DaemonError(reason, wire.NAME_RESERVED,
                              {"reason": reason, "name": held[0]})


def _wire_copy(value):
    """`value` as it would arrive over the socket: through JSON and back."""
    return None if value is None else json.loads(json.dumps(value))


def bound_sender(daemon, claim_key: str, identity: str | None = None):
    """The sender a tool sends as: the identity the active row of `claim_key` maps to,
    with the credential of that identity's live binding (`Daemon.live_credential`),
    read again once if the daemon refuses it. None when the row's binding isn't live (the daemon restarted and the
    adapter hasn't bound again, or its connection dropped), or the claim has no active
    row for `identity`."""
    def live():
        return daemon.live_credential(claim_key, identity)

    current = live()
    if current is None:
        return None
    return sending.Sender.bound(current[0], current[1], reread=live)


def context(daemon, *, deadline: float | None = None, env=None) -> sending.Context:
    """A `sending.Context` for one tool call: every daemon call in process, the
    daemon's own record and directory, the daemon's environment unless `env` is given."""
    return sending.Context(record=daemon.record, db_path=daemon.db_path,
                           socket_path=daemon.socket_path,
                           env=env if env is not None else os.environ,
                           deadline=deadline, daemon=InProcessClient(daemon))


class Calls:
    """Where tool bodies run: the host's pool, at most `at_once` at a time.

    `await calls.run(body, *args)` waits until the host lets methods through, takes a
    place, and runs `body(*args)` on one pool thread. A call that waits for a place holds
    no thread. A place is given back only when its body has returned, even when the
    awaiting call was cancelled first, so the bodies on the pool never number more than
    `at_once`. Never the loop's default executor."""

    def __init__(self, host, at_once: int = TOOLS_AT_ONCE):
        self._host = host
        self.at_once = at_once
        self._places = asyncio.Semaphore(at_once)
        #: Bodies running now, for the doctor and the tests.
        self.running = 0

    async def run(self, body, *args):
        await self._host.opened()
        await self._places.acquire()
        loop = asyncio.get_running_loop()
        try:
            future = self._host.pool.submit(body, *args)
        except BaseException:
            self._places.release()
            raise
        self.running += 1

        def done(_future) -> None:
            try:
                loop.call_soon_threadsafe(self._give_back)
            except RuntimeError:
                pass  # the loop has closed; nothing waits for a place

        future.add_done_callback(done)
        waiting = asyncio.wrap_future(future)
        # A result nobody awaits any more (the call was cancelled) is read here, so it
        # is never reported as lost.
        waiting.add_done_callback(lambda f: f.cancelled() or f.exception())
        return await asyncio.shield(waiting)

    def _give_back(self) -> None:
        self.running -= 1
        self._places.release()


__all__ = ["Calls", "InProcessClient", "InProcessConnection", "SERVED", "TOOLS_AT_ONCE",
           "bound_sender", "context"]
