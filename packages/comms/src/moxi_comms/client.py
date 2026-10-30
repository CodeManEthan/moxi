"""The daemon's client, used by the CLI and by adapters, over `moxi.ipc`.

Adapters reconnect with backoff and surface "comms offline" to their agent while
disconnected; the CLI takes a single shot and reports honestly when the daemon
socket is gone.

`DaemonClient` keeps the shape its callers were written against -- `request(op)`,
`query(kind)`, `notify(op)`, answers as dicts with `ok`, a refusal's `response` with
`refused` or `transient` -- and speaks the daemon's JSON-RPC methods underneath: each old
op or query kind is sent as the method that replaced it, and each pushed notification is
handed to `on_push` as `{"op": method, **params}`.
"""

from __future__ import annotations

import itertools
import random
import threading

from moxi import ipc

from . import constants, wire
from .paths import daemon_socket


class DaemonOffline(Exception):
    """The daemon socket is gone. Never dressed up as anything else."""


class VersionMismatch(DaemonOffline):
    """The daemon refused this comms' wire version at the hello. Its text says which
    side is older and the cure for that side. Nothing was sent."""


class DaemonError(Exception):
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response or {}


class DaemonTimeout(DaemonError):
    """A request the daemon never answered inside `DaemonClient.timeout`.

    The refusal-class split. A plain `DaemonError` is a daemon that ANSWERED and said no
    -- someone else holds this name. A timeout is a daemon that said nothing at all,
    which is an unreachable daemon wearing a different hat: nothing refused anything,
    and there is no holder to name. The shim's tolerate-at-launch path therefore treats
    it as offline (retry) rather than printing a refusal line for a claim nobody
    contested. Register is the call that made this concrete -- it holds a record round
    trip in production. A post uses `DaemonNoAnswer` instead because, once sent, it may
    have landed even when its answer did not return.

    A SUBCLASS on purpose: every existing `except DaemonError` (the CLI's, the shim's
    refusal arm) keeps catching it, and only code that wants the split says so.
    """


class DaemonNoAnswer(DaemonError):
    """A request left this process, but the daemon did not answer it."""


#: The marker `_ping` queues behind the pushes it waits for.
_BARRIER = object()

#: Each old request op, and each old query kind, as the daemon method that replaced it.
_REQUESTS = {"register": "bind", "post": "send"}
_QUERIES = {"record_message": "message", "record_history": "history",
            "record_max_id": "max_id", "injected": "last_digest"}
#: The `activity` kinds that became methods of their own.
_ACTIVITY = {"turn": "turn_ended", "ready": "ready", "look": "look"}
#: The notifications that carry `seq`, numbered from one counter per connection: hints
#: that feed `moxi comms explain`, and the only ones where a late or replayed copy can
#: mislead it. `seq` is ordering, not authentication.
ORDERED = ("activity", "turn_ended", "ready", "look", "injected")


def _response(exc: ipc.RpcError) -> dict:
    """A daemon error as the answer dict callers read: `error` its reason, `code`,
    whatever its data names, and `refused` or `transient` by the code's range."""
    data = exc.data if isinstance(exc.data, dict) else {}
    reason = data.get("reason") or exc.message
    code = exc.code
    return {**data, "ok": False, "error": reason, "code": code,
            "refused": wire.is_refusal(code) or code in (wire.METHOD_NOT_FOUND,
                                                         wire.INVALID_PARAMS),
            "transient": wire.is_try_again(code) or code == wire.INTERNAL_ERROR}


class DaemonClient:
    """One duplex connection. `on_push` receives daemon-initiated frames."""

    def __init__(self, socket_path=None, on_push=None, timeout: float = 10.0, on_close=None):
        self.socket_path = str(socket_path or daemon_socket())
        self.on_push = on_push
        self.on_close = on_close
        self.timeout = timeout
        self._client: ipc.Client | None = None
        #: The per-source ping counter. One counter per DaemonClient, and every adapter
        #: builds a fresh client on every (re)connect, so "source" is exactly one
        #: adapter process's attachment to the daemon. It is shared by every ordered
        #: notification on purpose: the daemon keeps ONE last-accepted number per
        #: source, so the kinds have to be numbered off the same strictly increasing
        #: count.
        self._notify_seq = itertools.count(1)
        self._reader: threading.Thread | None = None
        self._closed = False

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> "DaemonClient":
        """Connect and agree a wire version. Raises `DaemonOffline` when nothing
        answers, and `VersionMismatch` when the daemon refuses this comms' version."""
        try:
            client = ipc.connect(self.socket_path, timeout=self.timeout)
        except (ipc.IpcError, OSError) as e:
            raise DaemonOffline(f"comms offline: {self.socket_path}: {e}") from e
        try:
            client.hello(wire.VERSIONS)
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
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="comms-daemon-client")
        self._reader.start()
        return self

    def close(self) -> None:
        self._closed = True
        if self._client is not None:
            self._client.close()

    @property
    def connected(self) -> bool:
        return self._client is not None and not self._closed

    def _read_loop(self) -> None:
        try:
            for method, params in self._client.notifications():
                if method is _BARRIER:
                    params.set()
                    continue
                if self.on_push:
                    self.on_push({"op": method, **(params if isinstance(params, dict)
                                                   else {})})
        except Exception:  # noqa: BLE001 - a push handler's failure ends the connection
            pass
        finally:
            self._closed = True
            if self.on_close:
                self.on_close()

    # ------------------------------------------------------------------ requests
    def request(self, op: str, **kwargs) -> dict:
        return self._request(op, **kwargs)

    def _request(self, op: str, *, no_answer=None, **kwargs) -> dict:
        if op == "ping":
            return self._ping()
        method, params = _outbound(op, kwargs)
        if not self.connected:
            raise DaemonOffline("comms offline: not connected to the daemon")
        try:
            result = self._client.call(method, params, timeout=self.timeout)
        except ipc.RpcError as e:
            response = _response(e)
            raise DaemonError(response["error"], response) from None
        except ipc.Timeout:
            raise (no_answer or DaemonTimeout)(f"timeout waiting for {op}") from None
        except (ipc.IpcError, OSError) as e:
            if no_answer is not None:
                raise no_answer(f"daemon closed the connection after {op} was sent") \
                    from None
            raise DaemonOffline(f"comms offline: {e}") from e
        answer = {"ok": True, **(result if isinstance(result, dict) else {})}
        if method == "bind" and "high" in answer:
            answer["hwm"] = answer["high"]
        return answer

    def _ping(self) -> dict:
        """A round trip, answered once the daemon has handled everything sent before
        it on this connection, and returned once every push the daemon sent before its
        answer has been handed to `on_push`. The hello at connect agreed the version, so
        it asks a read that changes nothing."""
        if not self.connected:
            raise DaemonOffline("comms offline: not connected to the daemon")
        try:
            self._client.call("max_id", {}, timeout=self.timeout)
        except ipc.RpcError as e:
            response = _response(e)
            raise DaemonError(response["error"], response) from None
        except ipc.Timeout:
            raise DaemonTimeout("timeout waiting for ping") from None
        except (ipc.IpcError, OSError) as e:
            raise DaemonOffline(f"comms offline: {e}") from e
        # Every push sent before the answer is queued by now; one more item behind them
        # says when the read loop has handed them all on. The queue is the client's own.
        passed = threading.Event()
        self._client._notes.put((_BARRIER, passed))
        if self._reader is not threading.current_thread():
            passed.wait(self.timeout)
        return {"ok": True}

    def notify(self, op: str, **kwargs) -> None:
        """Fire-and-forget notification (activity and its kinds, injected, unanswered).

        The ordered ones get a `seq` stamped here so that every sender gets one without
        every ping site remembering to; anything else goes unnumbered, because nothing on
        the other side orders it.
        """
        if not self.connected:
            return
        method, params = _outbound_note(op, kwargs)
        if method in ORDERED:
            params["seq"] = next(self._notify_seq)
        try:
            self._client.notify(method, params)
        except (ipc.IpcError, OSError):
            self._closed = True

    def register(self, *, name=None, scope=None, global_=False,
                 project=None, adapter="claude-shim", session_id=None,
                 holding=None, projects_root=None, turned=None, report=None,
                 endpoint=None) -> dict:
        """`session_id`, `holding`, `projects_root` and `turned` are the claude-shim's:
        which live session is behind the name, the ids it already holds, the root its
        Claude Code writes transcripts under, and whether a prompt or a turn end has been
        seen in that session (cold-sends). `report` is a codex or opencode wrapper's
        evidence for its cache clock, sent as it stands (`session_id`, `cache_source`,
        `turned`, `new_session`, `agent_model`; `HarnessAdapter.session_report`). They are
        left off when there is nothing to say. `endpoint` is a Claude adapter's word on
        whether its session has a turns endpoint yet; None sends nothing."""
        report_fields = {"session_id", "cache_source", "turned", "new_session",
                         "agent_model"}
        extra = {key: value for key, value in dict(report or {}).items()
                 if key in report_fields}
        if session_id:
            extra["session_id"] = session_id
        if holding:
            extra["holding"] = list(holding)
        if projects_root:
            extra["projects_root"] = str(projects_root)
        if turned is not None:
            extra["turned"] = bool(turned)
        if endpoint is not None:
            extra["endpoint"] = bool(endpoint)
        register = {**extra, "name": name, "scope": scope, "global": global_,
                    "project": project, "adapter": adapter}
        return self.request("register", **register)

    def post(self, identity: str, token: str, content: str) -> dict:
        """Post as a live binding, distinguishing a frame that never left from one
        whose answer was lost."""
        return self._request("post", no_answer=DaemonNoAnswer,
                             identity=identity, token=token, content=content)

    def query(self, kind: str, **kwargs) -> dict:
        return self.request("query", kind=kind, **kwargs)

    def attributed_message(self, message_id) -> dict | None:
        """One message with its author as the daemon attributes it, or None when the
        record holds no such id. The daemon's `attributed_message` read
        (`Daemon._attributed_message_query`): `message_id`, `room`, `author` (`name`,
        `tier`, `attribution`), `recipients`, `resolved`, `text`, `timestamp`. A read; it
        changes nothing. Raises `DaemonOffline`, `DaemonTimeout`, or `DaemonError` for a
        refusal (an id that is not a message id)."""
        answer = self.query("attributed_message", message_id=str(message_id))
        if not answer.get("found"):
            return None
        return {k: answer.get(k) for k in ATTRIBUTED_FIELDS}


def _outbound(op: str, kwargs: dict) -> tuple[str, dict]:
    """An old request op and its fields, as a daemon method and its params."""
    params = dict(kwargs)
    if op == "query":
        kind = params.pop("kind", None)
        return _QUERIES.get(kind, kind), params
    if op == "post":
        params["as"] = params.pop("identity", None)
    return _REQUESTS.get(op, op), params


def _outbound_note(op: str, kwargs: dict) -> tuple[str, dict]:
    """An old notification op and its fields, as a daemon method and its params."""
    params = dict(kwargs)
    if op == "activity":
        kind = params.pop("kind", None)
        return _ACTIVITY.get(kind, "activity"), params
    if op == "injected" and "digest" in params:
        params["text"] = params.pop("digest")
    return op, params


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


def backoff_delays(rng=None):
    """Reconnect backoff shape: 0.5s, x2, capped 30s, +/-20% jitter, forever."""
    rng = rng or random
    delay = constants.BACKOFF_START
    while True:
        jitter = 1.0 + rng.uniform(-constants.BACKOFF_JITTER, constants.BACKOFF_JITTER)
        yield min(delay, constants.BACKOFF_MAX) * jitter
        delay = min(delay * constants.BACKOFF_FACTOR, constants.BACKOFF_MAX)
