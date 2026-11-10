"""comms' provider's link to the daemon: the identity's one binding, as a client on the
daemon's socket, like the CLI and every adapter.

`Link` holds one binding of the provider's identity (`[connector] identity`, a global
standing name), bound as the adapter kind `connector`, on a connection of its own:

* **At start** it provisions the identity as a standing name when the directory has no
  row for it (the code behind `moxi-comms-provision --standing`), then binds. Standing,
  the name's mail waits while the connector is down, and no agent needs `new_name` to
  reach it.
* **Mail** the daemon pushes (`deliver`, at most its window of 64 not yet in a digest) is
  kept in memory, oldest first. A `person` kind has no turns: the rows stay in the
  daemon's queue until a tool's digest is settled `handled` (`settle`). A daemon restart
  or a dropped connection puts every row the binding held back to waiting; the buffer
  is emptied and the next bind gets them again.
* **Reconnects** follow comms' adapter backoff (`client.backoff_delays`). A name another
  binding holds is tried again every `HELD_RETRY` seconds, and the status row names the
  holder.

Reads (`agents`, `replies_to`) go on short connections of their own,
so a slow read never costs the binding its connection. The binding's own connection
carries only the bind and the digest's two methods.

The link never binds the operator's name: the identity is checked against it when the
config loads, and again here.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable

from .. import directory
from ..client import DaemonClient, DaemonError, DaemonOffline, backoff_delays
from ..paths import comms_db, daemon_socket
from ..provision import add_standing
from ..scoping import GLOBAL

#: The daemon capability that offers the `replies_to` read.
CAPABILITY = "connector"

log = logging.getLogger("comms.connector.link")

#: The adapter kind the identity binds as.
KIND = "connector"
#: Seconds between tries while another binding holds the name, or the daemon refused it.
HELD_RETRY = 30.0
#: Seconds a read on a short connection may take.
READ_TIMEOUT = 10.0
#: Seconds `start` waits for the first bind before it lets the connector go on.
FIRST_WAIT = 5.0

#: The link's states, as its status row says them.
STARTING, BOUND, OFFLINE, HELD, REFUSED, STOPPED = (
    "starting", "bound", "offline", "held", "refused", "stopped")


class Link:
    """One binding of `identity`, kept up by a supervisor thread until `stop()`.

    `socket_path` and `db_path` default to comms' own; `backoff` is an iterator factory
    of reconnect delays (default comms' adapter backoff); `held_retry` the wait while the
    name is held or refused. `operator` is the operator's identity name, which the link
    refuses to bind."""

    def __init__(self, identity: str, *, operator: str = "operator", socket_path=None,
                 db_path=None, backoff: Callable | None = None,
                 held_retry: float = HELD_RETRY, client_type=DaemonClient):
        if identity == operator:
            raise ValueError("comms' tools never bind the operator's name")
        self.identity = identity
        self.socket_path = str(socket_path or daemon_socket())
        self.db_path = db_path
        self._backoff = backoff or backoff_delays
        self.held_retry = held_retry
        self._client_type = client_type
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: DaemonClient | None = None
        self.token: str | None = None
        self.state = STARTING
        #: What the status row says beside the state: the holder, the daemon's words.
        self.detail: str | None = None
        self.holder: str | None = None
        self.provisioned: bool | None = None
        #: The daemon's pushes not yet taken into a digest, by message id, oldest first.
        self.buffer: OrderedDict[str, dict] = OrderedDict()
        #: Bumped on every push and every bind, so a waiter can tell something moved.
        self.generation = 0
        self._waiters: set = set()
        self._first = threading.Event()

    # ---------------------------------------------------------------- lifecycle
    def start(self, wait: float = FIRST_WAIT) -> None:
        """Start the supervisor and wait up to `wait` seconds for its first bind's answer.
        Never raises for the daemon's answer: a held name or a daemon that is down is a
        status row, tried again."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, daemon=True,
                                        name="comms-connector-link")
        self._thread.start()
        self._first.wait(wait)

    def stop(self, wait: float = 5.0) -> None:
        """Close the binding and end the supervisor. The daemon puts every row the binding
        held back to waiting."""
        with self._lock:
            self._stop.set()
            self._wake.set()
            client, self._client = self._client, None
            self.token = None
            self.state = STOPPED
            self.buffer.clear()
        if client is not None:
            client.close()
        if self._thread is not None:
            self._thread.join(wait)
        self._wakeup()

    @property
    def bound(self) -> bool:
        return self.state == BOUND and self.token is not None

    def _supervise(self) -> None:
        delays = self._backoff()
        while not self._stop.is_set():
            with self._lock:
                live = self._client is not None and self._client.connected
            if live:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            outcome = self._attempt()
            self._first.set()
            if outcome == BOUND:
                delays = self._backoff()
                continue
            pause = self.held_retry if outcome in (HELD, REFUSED) else next(delays)
            self._stop.wait(pause)

    def _attempt(self) -> str:
        """Provision when missing, then bind once. Returns the state it left."""
        self._provision()
        client = self._client_type(self.socket_path, on_push=self._on_push,
                                   on_close=lambda: self._on_close(client))
        try:
            client.connect()
            answer = client.bind(name=self.identity, global_=True, adapter=KIND)
        except DaemonOffline as e:
            client.close()
            return self._set(OFFLINE, f"comms' daemon isn't answering ({e}); trying again")
        except DaemonError as e:
            client.close()
            holder = (e.data.get("holder") or {})
            if holder.get("qualified") == self.identity:
                self.holder = holder.get("adapter") or "another process"
                return self._set(HELD, f"{self.identity} is held by another binding "
                                       f"(adapter {self.holder}); trying again every "
                                       f"{self.held_retry:g} s")
            return self._set(REFUSED, f"comms refused {self.identity}: {e}; trying again "
                                      f"every {self.held_retry:g} s")
        with self._lock:
            if self._stop.is_set() or not client.connected:
                client.close()
                return self._set(OFFLINE, "the binding's connection closed as it bound")
            self._client = client
            self.token = answer.get("token")
            self.holder = None
            self.generation += 1
            state = self._set(BOUND, f"bound as {self.identity}, adapter {KIND}")
        log.info("comms' tools for the Claude apps are bound as %s", self.identity)
        self._wakeup()
        return state

    def _set(self, state: str, detail: str) -> str:
        with self._lock:
            if self.state != state or self.detail != detail:
                level = logging.INFO if state == BOUND else logging.WARNING
                log.log(level, "%s", detail)
            self.state, self.detail = state, detail
        return state

    def _provision(self) -> None:
        """The identity as a standing name, when the directory has none. A failure is
        logged and the bind goes on: the name binds as a chosen one meanwhile."""
        if self.provisioned:
            return
        try:
            db = directory.connect(self.db_path or comms_db(), read_only=True)
            try:
                found = directory.standing_row(db, self.identity, GLOBAL) is not None
            finally:
                db.close()
            if found:
                self.provisioned = True
                return
        except Exception:  # noqa: BLE001 - a store that can't be read: try to write it
            log.debug("the directory couldn't be read for %s", self.identity, exc_info=True)
        try:
            conn = directory.init(self.db_path or comms_db())
            try:
                add_standing(None, conn, self.identity, GLOBAL)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 - ProvisionError, a store refusal, a lock
            self.provisioned = False
            log.warning("%s couldn't be provisioned as a standing name: %s", self.identity, e)
            return
        self.provisioned = True
        log.info("provisioned %s as a standing name", self.identity)

    # ---------------------------------------------------------------- the daemon's pushes
    def _on_push(self, method: str, params: dict) -> None:
        if method != "deliver":
            return
        payload = params.get("payload")
        if not isinstance(payload, dict) or payload.get("message_id") is None:
            return
        with self._lock:
            self.buffer[str(payload["message_id"])] = payload
            self.generation += 1
        self._wakeup()

    def _on_close(self, client) -> None:
        """`client`'s connection ended. Only the binding's own connection says anything
        about the link: an attempt's connection that the daemon refused (the name held)
        closes after the state is set, and must not turn HELD into OFFLINE."""
        with self._lock:
            if client is not self._client:
                return
            self._client = None
            self.token = None
            self.buffer.clear()
            if not self._stop.is_set():
                self.state, self.detail = OFFLINE, "the daemon connection closed; reconnecting"
        self._wake.set()
        self._wakeup()

    # ---------------------------------------------------------------- waiting on pushes
    def waiter(self) -> asyncio.Event:
        """An event set, on its loop, at the next push, bind or close."""
        event = asyncio.Event()
        loop = asyncio.get_running_loop()
        with self._lock:
            self._waiters.add((loop, event))
        return event

    def forget(self, event: asyncio.Event) -> None:
        with self._lock:
            self._waiters = {w for w in self._waiters if w[1] is not event}

    def _wakeup(self) -> None:
        with self._lock:
            waiters = list(self._waiters)
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:                       # its loop has closed
                self.forget(event)

    # ---------------------------------------------------------------- the binding's methods
    def pending(self) -> list[dict]:
        """The pushed mail not yet in a digest, oldest first by message id."""
        with self._lock:
            payloads = list(self.buffer.values())
        return sorted(payloads, key=lambda p: _as_id(p["message_id"]))

    def open_digest(self, ids) -> dict:
        """`open_digest` on the binding's connection: the rows still deliverable go in a
        new digest, withdrawn and expired ones are dropped. The ids leave the buffer."""
        client = self._require()
        answer = client.call("open_digest", {"ids": [str(i) for i in ids]})
        with self._lock:
            for mid in ids:
                self.buffer.pop(str(mid), None)
        return answer

    def settle(self, digest, handled: bool) -> None:
        """`turn_ended` for one digest: `handled` deletes its rows and moves the name's
        cursor; unhandled puts them back to waiting, and a second, empty turn end is the
        release point that hands them to the binding again."""
        if digest is None:
            return
        client = self._require()
        key = "handled" if handled else "unhandled"
        client.call("turn_ended", {key: [digest], "turn_id": f"connector-{digest}"})
        if not handled:
            client.call("turn_ended", {"turn_id": f"connector-{digest}-again"})

    def _require(self) -> DaemonClient:
        with self._lock:
            client = self._client
        if client is None or not client.connected:
            raise DaemonOffline("comms offline: the connector's binding isn't connected")
        return client

    # ---------------------------------------------------------------- reads
    def read(self, method: str, params: dict | None = None) -> dict:
        """A read any client may make, on a short connection of its own."""
        client = self._client_type(self.socket_path, timeout=READ_TIMEOUT).connect()
        try:
            return client.call(method, params or {})
        finally:
            client.close()

    def replies(self, reply_to: str, author: str) -> list[dict]:
        """The replies to `reply_to` by `author` addressed to the identity, from the
        daemon's `replies_to` read: nothing addressed only to another name is read. A daemon
        whose hello doesn't list `connector` has no such read, and the answer is empty."""
        client = self._client_type(self.socket_path, timeout=READ_TIMEOUT).connect()
        try:
            if CAPABILITY not in client.capabilities:
                return []
            got = client.call("replies_to", {"identity": self.identity,
                                        "reply_to": str(reply_to), "author": author})
        finally:
            client.close()
        return list(got.get("messages") or ())

    # ---------------------------------------------------------------- status
    def status(self) -> list[tuple[str, str, str]]:
        """The provider's rows. The connector shows each under the provider's module
        name, so a row is named without it: `identity` reads as `comms identity`."""
        with self._lock:
            state, detail = self.state, self.detail
            provisioned = self.provisioned
        level = {BOUND: "ok", STARTING: "warn", OFFLINE: "warn", HELD: "fail",
                 REFUSED: "fail", STOPPED: "warn"}[state]
        rows = [("identity", level, detail or f"{self.identity}: {state}")]
        if provisioned is False:
            rows.append(("standing name", "warn",
                         f"{self.identity} isn't provisioned as a standing name, so its "
                         "mail doesn't wait while the connector is down; see the log"))
        return rows


def _as_id(mid) -> tuple:
    text = str(mid)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


__all__ = ["BOUND", "HELD", "HELD_RETRY", "KIND", "Link", "OFFLINE", "REFUSED", "STARTING",
           "STOPPED"]
