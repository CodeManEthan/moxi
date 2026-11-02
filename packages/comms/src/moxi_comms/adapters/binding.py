"""The daemon steps the Claude shim and the harness adapters share: the register, the
registration of the session's tool token, and the `owed` query.

One register is: arm `replaying`, open a `DaemonClient`, register, and, when the
answer came back on a connection that is still open and the owner has not stopped,
install the binding under the owner's lock. Each caller keeps its own register fields
(the `register` callable) and its own way of saying the bind was declined: the shim
returns False, the harness raises `DroppedAfterAnswer`.

The owner is read for `_lock`, `_stop`, `socket_path` and `_on_push`, and written for
`replaying`, `identity`, `tier`, `offline` and its claim holder (`claims`).

**The tool token.** A session launched with comms' tools carries its token in
`MOXI_COMMS_MCP_TOKEN`. The adapter reads it once (`ToolToken.from_environ`), keeps its
hash and drops the value. After every bind that took hold, the first and each rebind,
the hash is registered on the connection that holds the binding (`mcp_token`), so the
daemon maps the token to this binding again after a dropped connection or a daemon
restart. The first bind runs before the adapter gives its wrapper the ready word, so the
harness, and its first connect to comms' tools, normally start after the registration.
"""

from __future__ import annotations

import logging
import os
import time

from .. import constants, wire
from ..client import DaemonError, DaemonOffline

log = logging.getLogger("comms.binding")

#: Why `register_and_install` declined a bind the daemon answered.
STOPPED = "stopped"         # the owner's `stop()` came while the register was in flight
DROPPED = "dropped"         # the connection closed after the answer, before the install


class ToolToken:
    """This session's token for comms' tools, kept as its hash only.

    `register(client)` registers the hash for the binding `client` holds and answers
    whether the daemon serves the tools now. The daemon's `mcp-token-taken` and
    `mcp-token-revoked` are final for the launch: said once, and nothing is registered
    again (a relaunch mints a new token). A refusal the daemon marks "try again" is tried
    again at once, up to `TRIES` in all; any other failure waits for the next bind."""

    #: How many times one bind's registration is tried when the daemon says try again.
    TRIES = 3
    #: The pause between those tries, in seconds.
    PAUSE = 0.2

    def __init__(self, token_hash: str):
        self.hash = token_hash
        #: The daemon's final refusal (its code name), once given.
        self.refused: str | None = None
        #: What the last registration the daemon answered said of `served`.
        self.served: bool | None = None

    def __repr__(self) -> str:
        return f"ToolToken(refused={self.refused!r}, served={self.served!r})"

    @classmethod
    def from_environ(cls, environ=None) -> "ToolToken | None":
        """The token of a launch whose environment carries comms' entry (the stamped
        token and URL, both non-empty), or None. The value is taken out of `environ`
        (the process's own by default): only its hash stays."""
        from ..mcp import tokens
        from ..mcp.additions import TOKEN_ENV, carries

        env = os.environ if environ is None else environ
        if not carries(env):
            return None
        return cls(tokens.digest(env.pop(TOKEN_ENV)))

    def register(self, client) -> bool | None:
        """`mcp_token` on `client`, the connection that holds the binding. True or False
        is the daemon's `served`; None when nothing was registered: a daemon whose hello
        doesn't list `mcp`, a final refusal now or before, or a failure the next bind
        tries again."""
        if self.refused is not None:
            return None
        if "mcp" not in (getattr(client, "capabilities", None) or ()):
            return None
        for attempt in range(1, self.TRIES + 1):
            try:
                answer = client.mcp_token(self.hash)
            except DaemonError as e:
                if e.code in (wire.MCP_TOKEN_TAKEN, wire.MCP_TOKEN_REVOKED):
                    self.refused = wire.CODE_NAMES[e.code]
                    log.warning("comms' tools are off for this session: the daemon "
                                "refused its token (%s: %s); a relaunch gets a new one",
                                self.refused, e)
                    return None
                if e.try_again and attempt < self.TRIES:
                    time.sleep(self.PAUSE)
                    continue
                log.warning("this session's tool token was not registered (%s); the "
                            "next bind registers it", e)
                return None
            except DaemonOffline as e:
                log.info("this session's tool token was not registered (%s); the next "
                         "bind registers it", e)
                return None
            self.served = bool((answer or {}).get("served"))
            return self.served
        return None                                          # pragma: no cover


def register_and_install(owner, *, client_type, on_close, register, install, ledger,
                         accept=None, bound=None, disarm: bool = True, token=None,
                         registered=None):
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

    `token` (a `ToolToken`), when given, is registered on the new connection once the
    binding is installed, outside the owner's lock (its answer comes on the reader
    thread, which takes that lock). `registered(served)` is then told the daemon's
    answer, when there was one.

    A failure after the install (the claim file's write, `bound`) closes the connection
    and is raised again: nothing is left holding the name with no way to close it.
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
        try:
            if ledger.hwm is None and resp.get("high") is not None:
                # The first registration's catch-up boundary: a `re:` at or below it
                # was sent before this process could see payments.
                ledger.hwm = str(resp["high"])
            owner.claims.bound(resp)
            if bound is not None:
                bound(resp)
        except BaseException:
            # The binding is installed on a connection the owner now holds, but the
            # bind did not take hold here. Left open, that connection would keep the
            # name bound, and the next register would replace the owner's reference to
            # it, leaving nothing able to close it. Closing it releases the name; the
            # close handler sees `offline` still True and says nothing.
            client.close()
            if disarm:
                owner.replaying = False
            raise
        owner.identity = resp["identity"]["qualified"]
        owner.tier = resp["identity"]["tier"]
        owner.offline = False
    if token is not None:
        served = token.register(client)
        if served is not None and registered is not None:
            registered(served)
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


__all__ = ["DROPPED", "STOPPED", "ToolToken", "query_owed", "register_and_install"]
