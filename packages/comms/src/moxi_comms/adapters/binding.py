"""The daemon steps the Claude shim and the harness adapters share: the register and
the `owed` query.

One register is: arm `replaying`, open a `DaemonClient`, register, and, when the
answer came back on a connection that is still open and the owner has not stopped,
install the binding under the owner's lock. Each caller keeps its own register fields
(the `register` callable) and its own way of saying the bind was declined: the shim
returns False, the harness raises `DroppedAfterAnswer`.

The owner is read for `_lock`, `_stop`, `socket_path` and `_on_push`, and written for
`replaying`, `identity`, `tier`, `offline` and its claim holder (`claims`).
"""

from __future__ import annotations

from .. import constants

#: Why `register_and_install` declined a bind the daemon answered.
STOPPED = "stopped"         # the owner's `stop()` came while the register was in flight
DROPPED = "dropped"         # the connection closed after the answer, before the install


def register_and_install(owner, *, client_type, on_close, register, install, ledger,
                         accept=None, bound=None, disarm: bool = True):
    """Register once, and install the binding unless it must be declined.

    `client_type` builds the connection (the caller's module's `DaemonClient`, read at
    call time). `on_close` is the connection's close handler. `register(client)` sends
    the caller's `bind` and returns the answer. `accept(answer)`, when given,
    runs before anything is installed and may raise to refuse the answer. Any failure
    from connect to `accept` closes the connection and is raised again.

    Under the owner's lock, a bind is declined when `stop()` has been called (STOPPED)
    or when the connection has already closed (DROPPED); the connection is closed and
    the reason returned. Otherwise `install(client, answer)` takes the client (the
    owner's own attribute for it, and whatever else it keeps from the answer), the
    reply `ledger` takes the first registration's `high`, the claim holder is told it is
    bound, and `identity`, `tier` and `offline` are set. Returns the answer.

    `bound(answer)`, when given, runs right after the claim holder is told it is bound and
    before `identity` changes, so it sees the old name, and it never runs when that write
    raises. A caller whose bookkeeping must wait for a bind that took hold uses it.

    `disarm` sets `replaying` back to False on a failure or a declined bind; a caller
    that passes False leaves it armed until its next register arms it again.
    """
    with owner._lock:
        # Armed BEFORE the register round trip: `replay_complete` can land on the
        # reader thread before `register` returns, and a late flag would strand it.
        owner.replaying = True
    client = client_type(owner.socket_path, on_push=owner._on_push, on_close=on_close)
    try:
        client.connect()
        resp = register(client)
        if accept is not None:
            accept(resp)
    except BaseException:
        client.close()              # a refused claim never leaves a connection behind
        if disarm:
            with owner._lock:
                # Nothing landed, so nothing is replaying. A launch into a down daemon
                # would otherwise leave `replaying` True for the life of the session.
                owner.replaying = False
        raise
    with owner._lock:
        if owner._stop.is_set():
            # A bind must not outlive `stop()`. A session that ends while the daemon is
            # down has a supervisor running, and one already inside `register` would
            # otherwise hand the owner a live binding with nothing left to close it: the
            # daemon would hold it until the process exits, and the claim holder would
            # write back the claim file `stop()` removed. Closing here releases it now.
            client.close()
            if disarm:
                owner.replaying = False
            return STOPPED
        if not client.connected:
            # The daemon answered `register` and the connection closed before this
            # thread got here. The close handler runs on the reader thread and has
            # already run (the reader marks the client closed before calling it): it saw
            # `offline` still True, so it said nothing and will never run again for this
            # client. Taking the binding now would leave `offline` False over a dead
            # client, and the supervisor, which registers only while `offline` is True,
            # would never register again.
            #
            # Under the owner's lock, which the close handler takes too, the ordering is
            # total: the close landed first and this declines, or the handler runs after
            # the install below and reads the binding offline.
            client.close()
            if disarm:
                owner.replaying = False
            return DROPPED
        install(client, resp)
        if ledger.hwm is None and resp.get("high") is not None:
            # The first registration's catch-up boundary: a `re:` at or below it was
            # sent before this process could see payments.
            ledger.hwm = str(resp["high"])
        owner.claims.bound(resp)
        if bound is not None:
            bound(resp)
        owner.identity = resp["identity"]["qualified"]
        owner.tier = resp["identity"]["tier"]
        owner.offline = False
    return resp


def query_owed(socket_path, identity: str, authors: dict, referents: list, *,
               client_type) -> dict:
    """The daemon's `owed` query on a short connection of its own, with a 0.5 s timeout.
    Raises on any failure. `client_type` is the caller's module's `DaemonClient`."""
    client = client_type(socket_path, timeout=constants.OWED_QUERY_TIMEOUT)
    client.connect()
    try:
        return client.call("owed", {"identity": identity, "authors": authors,
                                    "referents": referents})
    finally:
        client.close()


__all__ = ["DROPPED", "STOPPED", "query_owed", "register_and_install"]
