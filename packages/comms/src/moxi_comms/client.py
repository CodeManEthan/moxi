"""The daemon's client, used by the CLI, by adapters and by module code, over `moxi.ipc`.

`DaemonClient` is a thin wrapper over one `moxi.ipc` connection: `connect()` agrees a
comms wire version at the hello, `call(method, params)` asks and returns the daemon's
result, and `notify(method, params)` tells, stamping `seq` on the ordered notifications.
What the daemon pushes is handed to `on_push(method, params)` on the client's reader
thread. The errors are comms' own names, raised from `moxi.ipc`'s, so a caller branches
on the class:

- `DaemonOffline`: nothing was asked. The connect or the hello failed, or the client is
  not connected. `VersionMismatch` is the hello's version refusal, whose text says which
  side is older.
- `DaemonError`: the daemon answered with an error. `code` is its code and `data` what it
  named; `refused` and `try_again` read the code's range.
- `DaemonTimeout`: a call the daemon never answered in time. `DaemonNoAnswer`: a `send`
  that left this process and got no answer, so the message may have been sent.

Adapters reconnect with backoff and surface "comms offline" to their agent while
disconnected; the CLI takes a single shot and reports honestly when the daemon is gone.
"""

from __future__ import annotations

import itertools
import random
import threading
import time

from moxi import ipc

from . import constants, wire
from .paths import daemon_socket


class DaemonOffline(Exception):
    """The daemon socket is gone. Never dressed up as anything else."""


class VersionMismatch(DaemonOffline):
    """The daemon refused this comms' wire version at the hello. Its text says which
    side is older and the cure for that side. Nothing was sent.

    An adapter keeps retrying it as it would an absent daemon, since restarting the older
    side cures it; a verb exits `ERROR` for it, not "try again"."""


class DaemonError(Exception):
    """The daemon answered with an error: `code`, `data` (what the error names, with its
    `reason`), and the code's range."""

    def __init__(self, message, code: int | None = None, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = dict(data or {})

    @property
    def refused(self) -> bool:
        """The daemon said no, and asking again won't change it."""
        return self.code is not None and wire.is_refusal(self.code)

    @property
    def try_again(self) -> bool:
        """The daemon committed nothing for the request; the same request may work
        later."""
        return self.code is not None and wire.is_try_again(self.code)


class DaemonTimeout(DaemonError):
    """A request the daemon never answered inside the client's timeout.

    A plain `DaemonError` is a daemon that ANSWERED and said no. A timeout is a daemon
    that said nothing at all, which is an unreachable daemon wearing a different hat:
    nothing refused anything, and there is no holder to name. The shim's
    tolerate-at-launch path therefore treats it as offline (retry) rather than printing a
    refusal line for a claim nobody contested. A `send` raises `DaemonNoAnswer` instead,
    because once sent it may have landed even when its answer did not return.

    A SUBCLASS on purpose: every `except DaemonError` keeps catching it, and only code
    that wants the split says so.
    """


class DaemonNoAnswer(DaemonError):
    """A request left this process, but the daemon did not answer it."""


#: The notifications that carry `seq`, numbered from one counter per connection: hints
#: that feed `moxi comms explain`, and the only ones where a late or replayed copy can
#: mislead it. `seq` is ordering, not authentication.
ORDERED = ("activity", "turn_ended", "ready", "look", "injected")

#: How long a client with no pushes to read waits before it asks a busy daemon again,
#: once, on a new connection.
BUSY_RETRY = 0.5

#: The session reports a `bind` carries, as an adapter's `report` names them.
REPORT_FIELDS = ("session_id", "cache_source", "turned", "new_session", "agent_model")


def _error(exc: ipc.RpcError, cls=DaemonError) -> DaemonError:
    """A daemon error as comms' exception: its reason, its code and what it named."""
    data = exc.data if isinstance(exc.data, dict) else {}
    reason = data.get("reason") or exc.message
    return cls(reason, exc.code, data)


class DaemonClient:
    """One duplex connection. `on_push(method, params)` receives what the daemon pushes;
    `on_close()` runs once the connection has ended."""

    def __init__(self, socket_path=None, on_push=None, timeout: float = 10.0, on_close=None):
        self.socket_path = str(socket_path or daemon_socket())
        self.on_push = on_push
        self.on_close = on_close
        self.timeout = timeout
        self._client: ipc.Client | None = None
        #: The ordered notifications' counter. One per DaemonClient, and every adapter
        #: builds a fresh client on every (re)connect, so its source is exactly one
        #: adapter process's attachment to the daemon. Shared by every ordered
        #: notification on purpose: the daemon keeps ONE last-accepted number per
        #: source, so the kinds have to be numbered off the same strictly increasing
        #: count.
        self._notify_seq = itertools.count(1)
        self._reader: threading.Thread | None = None
        self._closed = False
        #: Whether this connection has asked the daemon anything yet: only its first
        #: method can be answered `busy`.
        self._asked = False
        self._retried = False
        #: The daemon's capabilities, as its answer to this connection's hello named
        #: them; empty until connected. An adapter tests a name here (`mcp`) before it
        #: calls the method that needs it.
        self.capabilities: frozenset = frozenset()

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> "DaemonClient":
        """Connect and agree a wire version. Raises `DaemonOffline` when nothing
        answers, and `VersionMismatch` when the daemon refuses this comms' version."""
        try:
            client = ipc.connect(self.socket_path, timeout_s=self.timeout)
        except (ipc.IpcError, OSError) as e:
            raise DaemonOffline(f"comms offline: {self.socket_path}: {e}") from e
        try:
            agreed = client.hello(wire.VERSIONS)
        except ipc.RpcError as e:
            client.close()
            if e.code == wire.VERSION_REFUSED:
                raise VersionMismatch(wire.version_text(e.data)) from None
            raise DaemonOffline(f"comms offline: the daemon refused the hello: {e}") from e
        except ipc.NotRpc as e:
            client.close()
            raise DaemonOffline(f"comms offline: {self.socket_path} does not speak this "
                                f"comms' wire: {e}") from e
        except (ipc.IpcError, OSError) as e:
            client.close()
            raise DaemonOffline(f"comms offline: {self.socket_path}: {e}") from e
        self._client = client
        self.capabilities = frozenset(agreed.capabilities or ())
        self._closed = False
        self._asked = False
        self._reader = threading.Thread(target=self._read_loop, args=(client,),
                                        daemon=True, name="comms-daemon-client")
        self._reader.start()
        return self

    def close(self) -> None:
        self._closed = True
        if self._client is not None:
            self._client.close()

    @property
    def connected(self) -> bool:
        return self._client is not None and not self._closed

    def _read_loop(self, client: ipc.Client) -> None:
        try:
            for method, params in client.notifications():
                if self.on_push:
                    self.on_push(method, params if isinstance(params, dict) else {})
        except Exception:  # noqa: BLE001 - a push handler's failure ends the connection
            pass
        finally:
            if self._client is client:     # not a connection made since in its place
                self._closed = True
                if self.on_close:
                    self.on_close()

    # ------------------------------------------------------------------ the wire
    def call(self, method: str, params: dict | None = None, *,
             timeout: float | None = None) -> dict:
        """Ask the daemon `method` and return its result. Raises `DaemonOffline` when
        not connected or when the connection fails, `DaemonTimeout` when no answer came
        in time, and `DaemonError` for the daemon's error."""
        return self._call(method, params, timeout=timeout)

    def _call(self, method: str, params: dict | None, *, timeout: float | None = None,
              no_answer=None) -> dict:
        if not self.connected:
            raise DaemonOffline("comms offline: not connected to the daemon")
        first, self._asked = not self._asked, True
        try:
            result = self._client.call(method, dict(params or {}),
                                       timeout_s=self.timeout if timeout is None else timeout)
        except ipc.RpcError as e:
            if e.code == wire.BUSY and first and self._may_retry():
                # The daemon had no room for this connection and closed it, having done
                # nothing for it: one more try on a new connection, after a moment.
                self._client.close()
                self._retried = True
                time.sleep(BUSY_RETRY)
                self.connect()
                return self._call(method, params, timeout=timeout, no_answer=no_answer)
            raise _error(e) from None
        except ipc.Timeout:
            raise (no_answer or DaemonTimeout)(f"timeout waiting for {method}") from None
        except (ipc.IpcError, OSError) as e:
            if no_answer is not None:
                raise no_answer(f"the daemon closed the connection after {method} was "
                                f"sent") from None
            raise DaemonOffline(f"comms offline: {e}") from e
        return result if isinstance(result, dict) else {}

    def _may_retry(self) -> bool:
        """A client that reads no pushes and runs nothing at its close (a verb's, module
        code's) may connect again under the caller: nothing of the old connection is
        held anywhere."""
        return self.on_push is None and self.on_close is None and not self._retried

    def notify(self, method: str, params: dict | None = None) -> None:
        """Fire-and-forget notification (`activity` and its kinds, `injected`,
        `unanswered`).

        The ordered ones get a `seq` stamped here so that every sender gets one without
        every site remembering to; anything else goes unnumbered, because nothing on the
        other side orders it. A notification on a closed connection is dropped.
        """
        if not self.connected:
            return
        params = dict(params or {})
        if method in ORDERED:
            params["seq"] = next(self._notify_seq)
        try:
            self._client.notify(method, params)
        except (ipc.IpcError, OSError):
            self._closed = True

    # ------------------------------------------------------------------ the methods
    def bind(self, *, name=None, scope=None, global_=False, project=None,
             adapter="claude-shim", session_id=None, projects_root=None,
             turned=None, report=None, endpoint=None, claim=None) -> dict:
        """`bind`: the result's `identity`, `token` and `high`.

        `session_id`, `projects_root` and `turned` are the claude-shim's: which live
        session is behind the name, the root its Claude Code writes transcripts under,
        and whether a prompt or a turn end has been seen in that session (cold-sends). `report` is a codex or opencode wrapper's
        evidence for its cache clock, sent as it stands (`REPORT_FIELDS`;
        `HarnessAdapter.session_report`). They are left off when there is nothing to
        say. `endpoint` is a Claude adapter's word on whether its session has a turns
        endpoint yet; None sends nothing. `claim` is the adapter's claim key, which the
        session's tool token maps to (`mcp_token`); None sends nothing."""
        params = {key: value for key, value in dict(report or {}).items()
                  if key in REPORT_FIELDS}
        if session_id:
            params["session_id"] = session_id
        if projects_root:
            params["projects_root"] = str(projects_root)
        if turned is not None:
            params["turned"] = bool(turned)
        if endpoint is not None:
            params["endpoint"] = bool(endpoint)
        if claim is not None:
            params["claim"] = claim
        params.update(name=name, scope=scope, project=project, adapter=adapter)
        params["global"] = global_
        return self.call("bind", params)

    def mcp_token(self, token_hash: str) -> dict:
        """`mcp_token`: register the hash of the session's tool token for the binding
        this connection holds. The result's `served` says whether the daemon serves
        comms' tools now. Call it only when the hello listed the `mcp` capability."""
        return self.call("mcp_token", {"hash": token_hash})

    def send(self, as_: str, token: str | None, content: str, *, user=None) -> dict:
        """`send` as `as_` with its `token`: the result's `message_id`. A request that
        left this process and got no answer raises `DaemonNoAnswer`, since the message
        may have been sent. `user` is the operator's user id, for `as_` "operator"."""
        params = {"as": as_, "token": token, "content": content}
        if user is not None:
            params["user"] = user
        return self._call("send", params, no_answer=DaemonNoAnswer)

    def delete(self, as_: str, token: str | None, message_id, *, user=None) -> dict:
        """`delete` as `as_` with its `token`: the result's `message_id`, `withdrawn`
        and `delivered` (names), and `expired`. A request that left this process and got
        no answer raises `DaemonNoAnswer`, since the message may have been withdrawn.
        `user` is the operator's user id, for `as_` "operator"."""
        params = {"as": as_, "token": token, "message_id": str(message_id)}
        if user is not None:
            params["user"] = user
        return self._call("delete", params, no_answer=DaemonNoAnswer)

    def attributed_message(self, message_id) -> dict | None:
        """One message with its author as the daemon attributes it, or None when the
        record holds no such id. The daemon's `attributed_message` read:
        `message_id`, `room`, `author` (`name`, `tier`, `attribution`), `recipients`,
        `resolved`, `text`, `timestamp`. A read; it changes nothing. Raises
        `DaemonOffline`, `DaemonTimeout`, or `DaemonError` for a refusal (an id that is
        not a message id)."""
        try:
            answer = self.call("attributed_message", {"message_id": str(message_id)})
        except DaemonError as e:
            if e.code == wire.UNKNOWN_MESSAGE:
                return None
            raise
        return {k: answer.get(k) for k in ATTRIBUTED_FIELDS}


#: The fields `attributed_message` hands back for a message the record holds.
ATTRIBUTED_FIELDS = ("message_id", "room", "author", "recipients", "resolved", "text",
                     "timestamp")


def attributed_message(message_id, *, socket_path=None, timeout: float = 10.0) -> dict | None:
    """`DaemonClient.attributed_message` on a connection of its own, closed after.

    The read for a program an agent hands a message to by its id, as when an agent
    registers an item with it: the program asks for the message by the id `moxi comms
    send` printed, and takes the item's sender and text from this answer, never from the
    registering process."""
    client = DaemonClient(socket_path, timeout=timeout).connect()
    try:
        return client.attributed_message(message_id)
    finally:
        client.close()


def ask(method: str, params: dict | None = None, *, socket_path=None,
        timeout: float = 10.0) -> dict:
    """`DaemonClient.call` on a connection of its own, closed after."""
    client = DaemonClient(socket_path, timeout=timeout).connect()
    try:
        return client.call(method, params)
    finally:
        client.close()


def backoff_delays(rng=None):
    """Reconnect backoff shape: 0.5s, x2, capped 30s, +/-20% jitter, forever."""
    rng = rng or random
    delay = constants.BACKOFF_START
    while True:
        jitter = 1.0 + rng.uniform(-constants.BACKOFF_JITTER, constants.BACKOFF_JITTER)
        yield min(delay, constants.BACKOFF_MAX) * jitter
        delay = min(delay * constants.BACKOFF_FACTOR, constants.BACKOFF_MAX)
