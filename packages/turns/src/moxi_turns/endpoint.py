"""The endpoint core: one per session turns can reach, one caller address, one turn ledger.

An endpoint is a small turns process (the Claude Code endpoint the runner spawns, or the
Codex or OpenCode host). This module is its harness-free core; an adapter
(`harness.Harness`) supplies the harness half. The core:

- makes the endpoint's directory under version 2's root,
  `RUNTIME/turns/v2/<harness>-<nonce>/` (0700), serves turns' methods at its address
  `moxi.ipc.address("turns", "<harness>-<nonce>")` on `moxi.ipc.serve`, and writes
  `hello.json` once it listens. The key is the launch's nonce, not the session id, so a
  `/clear` keeps the endpoint and `hello.json` follows the id;
- answers the socket protocol (`contracts/socket-protocol.md`) through `rpc.Rpc`:
  `session`, `facts`, `agents`, `deliver`, `steer`, `wait`, `command`, `watch`,
  `stops.handle` and `stops.answer`;
- keeps the turn ledger the adapter feeds, ties deliveries to turns, holds a
  delivery made while a turn runs until that turn ends, and answers `wait` from the
  ledger alone;
- checks a session command's consent and its idle snapshot;
- asks the session's stop handler at each Stop, bounded by its wait;
- asks the registered listeners at `ready` and `prompt` and passes back what they
  answer, and tells them of `turn` and `gone`.

turns decides nothing about content or timing: it delivers what it is handed, when it is
handed it, under the preconditions the caller names.

`open()` opens the endpoint; `stop()` (or the adapter's `gone()`) ends it. The core's
caller-side methods (`session`, `facts`, `start`, `deliver`, `steer`, `wait`, `command`)
take plain values, so a test can drive the core without the wire.

The address is served by an asyncio loop on a thread of its own (`moxi.aio.run`). The
core's lock is a thread lock; the loop takes it only for steps that never wait on the
harness, and every notification leaves through `Connection.notify_threadsafe`, called
under that lock, so each watch gets its notifications in the order the ledger saw them.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import itertools
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from moxi.paths import Layout

from . import config as turns_config
from . import listeners as turns_listeners
from . import protocol
from .harness import CommandRequest, Delivery, Harness, Live
from .ledger import Reading, Turn, TurnLedger
from .results import (COMMANDS, FAILED_TIMEOUT, LOST_UNKNOWN, OUTCOMES, Agents,
                      DeliveryRef, Ended, Facts, Failed, Gone, Lost, OpenTurn, Passed, Ran,
                      Ready, Refused, SessionEvent, Started, StopFacts, TurnEnded,
                      TurnStarted)

log = logging.getLogger("moxi.turns.endpoint")

#: One session command accepted per this many seconds.
COMMAND_RATE_S = 60.0
#: How long a `command` waits for the harness to settle it, unless the caller says.
COMMAND_WAIT_S = 30.0
#: How many settled deliveries an endpoint remembers for a late `wait`.
SETTLED_KEPT = 256
#: How long a delivery or command handed to the adapter may stay unsettled. Past it the
#: endpoint settles it (a delivery `Lost`, a command `Failed`) and frees its slot, so an
#: adapter that drops one cannot block the endpoint for its life.
PENDING_TTL_S = 300.0
#: The reason an expired delivery or command settles with, and `entering` returns.
EXPIRED = "expired"
#: How long `open()` waits for the address to be served, and `stop()` for its loop to end.
OPEN_WAIT_S = 10.0

_QUEUED, _INFLIGHT, _STARTED, _DONE = "queued", "in-flight", "started", "done"


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("moxi-turns")
    except Exception:  # noqa: BLE001
        return "0+unknown"


@dataclass
class Handed:
    """One delivery as the endpoint keeps it."""

    spec: Delivery
    state: str
    version_seen: int | None = None
    turn: Turn | None = None
    started: Started | Refused | Lost | None = None
    ended: Ended | Refused | Lost | None = None
    #: When it went in flight (monotonic), for the expiry.
    handed_at: float | None = None
    expired: bool = False
    #: The caller's `ref`, echoed in every value of the delivery.
    ref: str | None = None
    #: The refusal the endpoint decided from its own record before it handed the
    #: delivery on, which the `deliver` or `steer` answer carries.
    early: Refused | None = None

    @property
    def id(self) -> str:
        return self.spec.id

    def stamp(self, value):
        """`value` naming this delivery and its `ref`."""
        return replace(value, delivery=self.id, ref=self.ref)

    def value(self, until: str):
        """The delivery's value until `started` or `ended`, or None while it is open."""
        value = self.started if until == "started" else self.ended
        return None if value is None else self.stamp(value)


@dataclass
class _Command:
    request: CommandRequest
    version_seen: int
    result: Ran | Failed | Refused | Passed | None = None
    handed_at: float = field(default_factory=time.monotonic)
    expired: bool = False


@dataclass
class _Holder:
    """The session's stop handler: a connection, and the asks it has not answered."""

    conn: Any
    asks: dict[int, list] = field(default_factory=dict)


class Endpoint:
    """The core of one endpoint."""

    def __init__(self, harness: Harness, *, env: Mapping[str, str] | None = None,
                 registry: turns_listeners.Registry | None = None,
                 session_id: str | None = None):
        self.harness = harness
        #: The session's environment: the endpoint inherits it, and listeners get it.
        self.env: dict[str, str] = dict(os.environ if env is None else env)
        self.layout = Layout(turns_config.MODULE)
        self._config = turns_config.reader(self.env)
        settings = self._config.start()          # a ConfigError here refuses the start
        self.hold_wait_s = max(0, int(settings["hold.wait_ms"])) / 1000
        self.listen_wait_s = max(0, int(settings["listen.wait_ms"])) / 1000
        #: The bound on an unsettled delivery or command (`PENDING_TTL_S`).
        self.pending_ttl_s = PENDING_TTL_S
        self.registry = registry if registry is not None else turns_listeners.load()
        self.nonce = secrets.token_hex(6)
        self.version = package_version()
        self.started_at = time.time()
        #: The endpoint's name, its directory under version 2's root, and its address.
        self.name = protocol.endpoint_name(harness.name, self.nonce)
        self.dir = protocol.v2_root(self.layout.runtime_dir(self.env)) / self.name
        self.hello_path = self.dir / protocol.HELLO
        self.address = protocol.address(self.name)

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.ledger = TurnLedger(session_id or getattr(harness, "session_id", None))
        self._ids = itertools.count(1)
        self._deliveries: dict[str, Handed] = {}
        self._settled: deque[str] = deque()
        self._queue: deque[str] = deque()
        self._inflight: str | None = None
        self._commands: dict[str, _Command] = {}
        self._command_inflight: str | None = None
        self._command_accepted_at: float | None = None
        #: The connections that watch, each a `moxi.ipc.Connection`.
        self._watchers: list[Any] = []
        #: The last `Ready` raised, with the time it was raised.
        self._ready: Ready | None = None
        #: Turns whose events wait for the tie, oldest first.
        self._held: list[Turn] = []
        self._attached = False
        self._holder: _Holder | None = None
        self._asks = itertools.count(1)
        self._gone: str | None = None
        self._closed = threading.Event()
        self._stopping = threading.Event()
        #: The loop that serves the address, its server and its thread, once open.
        self._loop: Any = None
        self._server: Any = None
        self._serving: threading.Thread | None = None

    # -- life --------------------------------------------------------------------------

    def prepare(self) -> "Endpoint":
        """Attach the adapter and make the endpoint's directory (0700), without opening
        the address. An adapter that needs the directory before callers may find the
        endpoint (the Claude half's `mod.sock`) calls this first; `open()` calls it too.
        Safe to repeat."""
        if not self._attached:
            self.harness.attach(self)
            self._attached = True
        self.layout.runtime_dir(self.env, create=True)
        root = self.dir.parent
        root.mkdir(mode=0o700, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(root, 0o700)
        self.dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        return self

    def open(self) -> "Endpoint":
        """Make the directory, serve the address, write `hello.json`, start answering.
        Raises `OSError` when the address cannot be served."""
        self.prepare()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        thread = threading.Thread(target=self._serve, args=(ready,), daemon=True,
                                  name="turns-endpoint-loop")
        self._serving = thread
        thread.start()
        try:
            ready.result(timeout=OPEN_WAIT_S)
        except BaseException as exc:
            self._close_server()
            self._remove_files()
            if isinstance(exc, OSError):
                raise
            raise OSError(f"cannot serve {self.address}: {exc}") from exc
        # hello.json is written once the address is served, so a finder that reads it
        # reaches a server that answers, or a dead one `moxi.ipc.sweep` may clear.
        self._write_hello()
        threading.Thread(target=self._expiry_loop, daemon=True,
                         name="turns-endpoint-expiry").start()
        log.info("endpoint %s for %s pid %s at %s", self.nonce, self.harness.name,
                 self.harness.pid, self.address)
        return self

    def _serve(self, ready: concurrent.futures.Future) -> None:
        """The serving thread: an asyncio loop that serves the address until `stop()`
        closes the server."""
        from moxi import aio, ipc

        from . import rpc

        handler = rpc.Rpc(self)

        async def main() -> None:
            import asyncio

            try:
                server = await ipc.serve(self.address, handler,
                                         hello=ipc.Hello(protocol.VERSIONS,
                                                         self.capabilities),
                                         on_close=handler.closed)
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
                log.exception("the endpoint's loop failed")

    def _call_soon(self, fn: Callable[..., Any], *args: Any) -> None:
        """Run `fn(*args)` on the serving loop, after what is already queued there."""
        loop = self._loop
        if loop is None:
            return
        with contextlib.suppress(RuntimeError):      # the loop has closed
            loop.call_soon_threadsafe(fn, *args)

    def _close_server(self) -> bool:
        """Close the server (its connections end once their queued frames are written,
        its socket and then its lock go) and wait for the loop's thread. True when the
        thread has ended."""
        if self._server is not None:
            self._call_soon(self._server.close)
        thread = self._serving
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(timeout=OPEN_WAIT_S)
        return not thread.is_alive()

    def stop(self, reason: str = "the endpoint stopped") -> None:
        """End the endpoint: every open delivery answers `Lost`, watchers get
        `session.gone`, the listeners are told `gone`, the server closes and the
        directory is removed. Safe to repeat."""
        with self._lock:
            if self._gone is not None:
                return
            # A held turn's window fails with the endpoint (the tie's third end):
            # its events go out as the person's before `session.gone`.
            for turn in list(self._held):
                self._decide(turn, None)
            self._gone = reason
            self._stopping.set()
            lost = Lost(reason)
            for d in list(self._deliveries.values()):
                if d.state != _DONE:
                    self._settle(d, started=lost if d.started is None else None,
                                 ended=lost)
            for c in self._commands.values():
                if c.result is None:
                    c.result = Failed(reason)
            self._command_inflight = None
            self._queue.clear()
            self._inflight = None
            self._emit(Gone(reason))
            watchers, self._watchers = self._watchers, []
            for conn in watchers:
                self._call_soon(conn.close)      # after its session.gone
            holder, self._holder = self._holder, None
            if holder is not None:
                for slot in holder.asks.values():
                    slot[0].set()
            self._cond.notify_all()
        turns_listeners.ask(self.registry, self._event("gone", reason=reason),
                            self.listen_wait_s, wait=True)
        closed = self._close_server()
        self._remove_files()
        if closed:
            protocol.remove_lock(self.address)
        with contextlib.suppress(Exception):
            self.harness.close()
        self._closed.set()
        log.info("endpoint %s ended: %s", self.nonce, reason)

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    @property
    def is_gone(self) -> bool:
        """True once the endpoint is ending: every method then answers the `gone`
        error."""
        return self._gone is not None

    @property
    def gone_reason(self) -> str | None:
        return self._gone

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What the session offers, as the hello answers it: the adapter's `can`, in the
        contract's order."""
        from .rpc import capabilities

        return capabilities(self.harness.can)

    def session(self) -> dict[str, Any]:
        """The `session` method's answer. `pid` is the harness process, the root of the
        session's tree; `endpoint_pid` is this process, which serves the endpoint."""
        with self._lock:
            answer = {
                "harness": self.harness.name,
                "pid": self.harness.pid,
                "endpoint_pid": os.getpid(),
                "session_id": self.ledger.session_id,
                "earlier": list(self.ledger.earlier),
                "cwd": self.harness.cwd,
                "nonce": self.nonce,
                "started": self.started_at,
                "endpoint_version": self.version,
                "mod_version": self.harness.mod_version,
            }
            trouble = getattr(self.harness, "trouble", None)
            if trouble:
                answer["trouble"] = trouble
            return answer

    def _write_hello(self) -> None:
        hello = {"address": self.address, "protocol": protocol.VERSION, **self.session()}
        data = json.dumps(hello, indent=1, sort_keys=True) + "\n"
        tmp = self.dir / protocol.HELLO_TMP
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, self.hello_path)

    def _remove_files(self) -> None:
        for name in protocol.ENDPOINT_FILES:
            with contextlib.suppress(OSError):
                (self.dir / name).unlink()
        with contextlib.suppress(OSError):
            self.dir.rmdir()

    # -- the adapter's side ------------------------------------------------------------

    def identify(self, session_id: str | None, *, ready: str | None = None) -> None:
        """Update the launch's session id. With `ready`, also raise ``Ready`` with that
        source, and ask no listener: a host's accepted load.

        A host can learn its id before the harness fires SessionStart. Addressing must
        follow that id at once, while listener context still belongs to the later hook.
        """
        with self._lock:
            if self._gone is not None:
                return
            if self.ledger.new_session(session_id):
                self._rewrite_hello()
            if ready is not None:
                self._emit(Ready(ready, self.ledger.session_id, time.time()))

    def load_changed(self, *, release: bool = True) -> None:
        """Publish changed load health and release delivery queued at the load gate."""
        self._rewrite_hello()
        if release:
            self._pump()

    def ready(self, source: str | None, session_id: str | None, *,
              announce: bool = True) -> str | None:
        """A session started, resumed, was cleared or compacted. Returns the listeners'
        added context for the event. `announce=False` asks the listeners without raising
        ``Ready``, for an event whose ``Ready`` the host already raised."""
        with self._lock:
            if self._gone is not None:
                return None
            if self.ledger.new_session(session_id):
                self._rewrite_hello()
            if announce:
                self._emit(Ready(source, self.ledger.session_id, time.time()))
            event = self._event("ready", source=source)
        return turns_listeners.ask(self.registry, event, self.listen_wait_s)

    def prompt(self) -> str | None:
        """A prompt entered (UserPromptSubmit). Returns the listeners' added context."""
        if self._gone is not None:
            return None
        return turns_listeners.ask(self.registry, self._event("prompt"), self.listen_wait_s)

    def turn_started(self, turn_id: str | None, delivery_id: str | None = None, *,
                     held: bool = False) -> None:
        """A main-loop turn started. `delivery_id` is the adapter's tie: the
        delivery that took this turn, or None for the person's own turn.

        `held`: which delivery took the turn is decided only after it started (Claude
        Code's tie). The ledger opens the turn now, so `facts()` and a
        start's re-check see it, but its `TurnStarted` (and its `TurnEnded`, if it ends
        first) wait for `turn_tied` or `turn_released`."""
        if held:
            with self._lock:
                if self._gone is not None:
                    return
                previous, turn = self.ledger.started(turn_id, None, held=True)
                if previous is not None:
                    log.warning("turn %s started while turn %s was open; that one ends as "
                                "error", turn_id, previous.turn_id)
                    self._turn_closed(previous)
                self._held.append(turn)
                self._cond.notify_all()
            return
        with self._lock:
            if self._gone is not None:
                return
            d = self._deliveries.get(delivery_id) if delivery_id else None
            if delivery_id and (d is None or d.state not in (_QUEUED, _INFLIGHT)):
                log.warning("turn %s names delivery %s, which is not waiting to start; "
                            "it counts as the person's turn", turn_id, delivery_id)
                delivery_id, d = None, None
            previous, turn = self.ledger.started(turn_id, delivery_id)
            if previous is not None:
                log.warning("turn %s started while turn %s was open; that one ends as "
                            "error", turn_id, previous.turn_id)
                self._turn_closed(previous)
            if d is not None:
                d.state = _STARTED
                d.turn = turn
                d.started = Started(turn_id)
                if self._inflight == d.id:
                    self._inflight = None
                with contextlib.suppress(ValueError):
                    self._queue.remove(d.id)
            self._emit(self._turn_started(turn))
            self._cond.notify_all()

    def turn_ended(self, turn_id: str | None, outcome: str) -> None:
        """The open main-loop turn ended with `outcome` (one of `results.OUTCOMES`)."""
        if outcome not in OUTCOMES:
            log.warning("turn %s ended with outcome %r, not one of %s; it counts as "
                        "error", turn_id, outcome, ", ".join(OUTCOMES))
            outcome = "error"
        with self._lock:
            if self._gone is not None:
                return
            turn = self.ledger.ended(outcome)
            if turn is None:
                log.warning("turn %s ended while no turn was open; ignored", turn_id)
                return
            if turn_id is not None and turn.turn_id is not None and turn_id != turn.turn_id:
                log.warning("turn %s ended while the ledger held turn %s open; the open "
                            "one ends", turn_id, turn.turn_id)
            self._turn_closed(turn)
            self._cond.notify_all()
        self._pump()

    def turn_ending(self, turn_id: str | None) -> None:
        """The harness was told the open turn may end (on Codex, an unblocked Stop
        answer). The turn stays open until `turn_ended`, so `turn_open` holds and a start
        is still refused, but it takes no more steers: a `deliver(steer=True)` waits and
        starts a new turn after it, and a plain `steer` reads `idle`. Codex can write a
        text it takes in that gap into the turn and complete the turn without answering
        it."""
        with self._lock:
            turn = self.ledger.open
            if self._gone is not None or turn is None:
                return
            if turn_id is not None and turn.turn_id is not None and turn_id != turn.turn_id:
                log.warning("turn %s is ending while the ledger holds turn %s open; the "
                            "open one takes steers still", turn_id, turn.turn_id)
                return
            self.ledger.ending()

    def turn_tied(self, turn_id: str | None, delivery_id: str) -> bool:
        """The held turn `turn_id` took `delivery_id` (the tie decided): its events go out
        in order, naming the delivery, and the delivery reads `Started` (and `Ended`, if
        the turn has ended). True when the tie took; False when there was no such held
        turn or the delivery was no longer waiting to start, and then the turn, if held,
        is released as the person's."""
        with self._lock:
            turn = self._held_turn(turn_id)
            if turn is None:
                log.warning("tie of delivery %s to turn %s: no such held turn",
                            delivery_id, turn_id)
                return False
            d = self._deliveries.get(delivery_id)
            if d is None or d.state not in (_QUEUED, _INFLIGHT):
                log.warning("tie of turn %s to delivery %s, which is not waiting to "
                            "start; the turn counts as the person's", turn_id, delivery_id)
                d = None
            self._decide(turn, d)
        self._pump()
        return d is not None

    def turn_released(self, turn_id: str | None) -> None:
        """The held turn `turn_id` took no delivery: its events go out as the person's."""
        with self._lock:
            turn = self._held_turn(turn_id)
            if turn is not None:
                self._decide(turn, None)

    def lost(self, delivery_id: str, reason: str) -> None:
        """The adapter cannot say which turn, if any, took a delivery it was handed (on
        Claude Code a submit's window closed with no turn tied; on Codex a request Codex
        was sent, a `turn/start` or `turn/steer`, that the host could not tie to a turn:
        no answer came, or no turn it had not already reported was in progress; the
        reason starts `untied`). It answers `Lost` and frees its slot. A steer is first
        taken off the turn it was steered into, as a refused one is."""
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is None or d.state == _DONE:
                return
            if d.turn is not None and delivery_id in d.turn.steered:
                d.turn.steered.remove(delivery_id)
                d.turn = None
                d.started = None
            lost = Lost(reason)
            self._settle(d, started=lost if d.started is None else None, ended=lost)
            if self._inflight == d.id:
                self._inflight = None
            with contextlib.suppress(ValueError):
                self._queue.remove(d.id)
        self._pump()

    def is_waiting(self, delivery_id: str) -> bool:
        """Whether a delivery handed to the adapter still waits to start (not tied,
        refused, lost or expired)."""
        with self._lock:
            d = self._deliveries.get(delivery_id)
            return d is not None and d.state in (_QUEUED, _INFLIGHT) and not d.expired

    def learn_session(self, session_id: str | None) -> bool:
        """The adapter learned the session's id (on Claude Code the runner sends it on
        every load). True when it changed; `hello.json` follows it."""
        with self._lock:
            if self._gone is not None or not self.ledger.new_session(session_id):
                return False
            self._rewrite_hello()
            return True

    def entering(self, delivery_id: str) -> str | None:
        """A start is about to take effect: re-check it against the ledger.
        A reason refuses it (the delivery answers `Refused`); None lets it in. Any other
        delivery passes untouched."""
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is not None and d.expired:
                return EXPIRED
            if d is None or d.state != _INFLIGHT:
                return "gone" if self._gone is not None else None
            if d.spec.kind != "start":
                return None
            reason = _moved(self.ledger.read(), d.version_seen)
            if reason is not None:
                self._refuse(d, reason)
        if reason is not None:
            self._pump()
        return reason

    def refused(self, delivery_id: str, reason: str) -> None:
        """The adapter refused a delivery it was handed (a failed check where the start
        took effect, a harness that would not take the text)."""
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is None or d.state == _DONE:
                return
            if d.turn is not None and delivery_id in d.turn.steered:
                d.turn.steered.remove(delivery_id)
                d.turn = None
                d.started = None
            self._refuse(d, reason)
        self._pump()

    def steer_missed(self, delivery_id: str) -> bool:
        """Move a steer that atomically missed its turn onto the deliver path.

        The adapter calls this before it reports the racing turn's end. The delivery is
        detached from that turn, then waits behind it like an ordinary delivery. False
        means the delivery no longer names that open turn and nothing changed.
        """
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if (d is None or not d.spec.fallback or d.state != _STARTED or d.turn is None
                    or delivery_id not in d.turn.steered):
                return False
            d.turn.steered.remove(delivery_id)
            d.turn = None
            d.started = None
            d.ended = None
            d.spec = replace(d.spec, kind="deliver", fallback=False)
            d.state = _QUEUED
            call = self._admit(d)
            self._cond.notify_all()
        if call is not None:
            self._hand(d, call)
        return True

    def on_stop(self, facts: StopFacts) -> str | None:
        """A Stop of the main loop: ask the stop handler. A string holds the turn open
        with that text as the reason; None (no handler, no answer in time) lets it end."""
        with self._lock:
            holder = self._holder
            if holder is None or self._gone is not None:
                return None
            ask_id = next(self._asks)
            slot: list = [threading.Event(), None]
            holder.asks[ask_id] = slot
            holder.conn.notify_threadsafe(protocol.STOP_ASKED,
                                          {"ask": ask_id, "facts": facts.to_wire()})
        slot[0].wait(self.hold_wait_s)
        with self._lock:
            # An answer counts only while its ask is open: once it is taken off here,
            # `stop_answer` reads the ask as unknown and answers `taken: false`.
            holder.asks.pop(ask_id, None)
            answered, answer = slot[0].is_set(), slot[1]
        if not answered:
            log.info("the stop handler did not answer Stop %s within %d ms; the turn ends",
                     ask_id, int(self.hold_wait_s * 1000))
            return None
        return answer if isinstance(answer, str) and answer else None

    def command_entering(self, request_id: str) -> str | None:
        """A session command is about to take effect: the re-check. A reason
        refuses it."""
        with self._lock:
            c = self._commands.get(request_id)
            if c is not None and c.expired:
                return EXPIRED
            if c is None or c.result is not None:
                return "gone" if self._gone is not None else None
            reason = _moved(self.ledger.read(), c.version_seen)
            if reason is not None:
                self._settle_command(c, Refused(reason))
            return reason

    def command_settled(self, request_id: str, result: Ran | Failed | Refused) -> None:
        """A command the adapter was handed ran, failed or was refused."""
        with self._lock:
            c = self._commands.get(request_id)
            if c is not None and c.result is None:
                self._settle_command(c, result)

    def gone(self, reason: str = "the session ended") -> None:
        """The session ended."""
        self.stop(reason)

    # -- the caller's side -------------------------------------------------------------

    def facts(self) -> Facts:
        """The ledger, the live facts, the ledger again: one observation, `stale` when the
        ledger moved between."""
        with self._lock:
            first = self.ledger.read()
        live = self._live()
        with self._lock:
            return self._facts(first, live)

    def _facts(self, first: Reading, live: Live | None) -> Facts:
        """Under the lock: the facts from a first reading taken before the live read, and
        the ledger as it is now."""
        second = self.ledger.read()
        return Facts(session_id=second.session_id, turn_number=second.turn_number,
                     turn_open=second.turn_open, turn=self._open_turn(),
                     agents=live.agents if live else None,
                     draft=live.draft if live else True,
                     at=time.time(), stale=first.version != second.version,
                     ready=self._ready)

    def agents(self) -> Agents:
        """The session's subagents as the harness records them. Until the adapter can
        list them, the list is not complete, which counts as busy."""
        return Agents((), False, time.time())

    def watch_read(self) -> tuple[Reading, Live | None]:
        """A watch's first half, off the loop: the ledger, then the live facts."""
        with self._lock:
            first = self.ledger.read()
        return first, self._live()

    def watch(self, conn, first: Reading, live: Live | None) -> Facts | None:
        """A watch's second half, on the loop: under one hold of the lock, the facts as of
        now and `conn` among the watchers, so every notification after these facts reaches
        it and none before them does. None when the endpoint is ending."""
        with self._lock:
            if self._gone is not None or conn.closed:
                return None
            facts = self._facts(first, live)
            self._watchers.append(conn)
            return facts

    def handle_stops(self, conn) -> bool | None:
        """Make `conn` the session's stop handler. False when another connection is;
        None when the endpoint is ending."""
        with self._lock:
            if self._gone is not None:
                return None
            if self._holder is not None and self._holder.conn is not conn:
                return False
            if self._holder is None:
                self._holder = _Holder(conn)
            return True

    def stop_answer(self, ask: int, reason: str | None) -> bool:
        """The stop handler's answer to Stop `ask`. True when it was taken; False for an
        ask that is unknown or no longer open (its wait passed and the turn ended)."""
        with self._lock:
            holder = self._holder
            slot = holder.asks.get(ask) if holder is not None else None
            if slot is None or slot[0].is_set():
                return False
            slot[1] = reason
            slot[0].set()
            return True

    def drop_connection(self, conn) -> None:
        """A connection closed: it no longer watches, and no longer handles stops."""
        with self._lock:
            with contextlib.suppress(ValueError):
                self._watchers.remove(conn)
            holder = self._holder
            if holder is not None and holder.conn is conn:
                self._holder = None
                for slot in holder.asks.values():
                    slot[0].set()

    def start(self, text: str, *, if_turn_number: int | None = None,
              refuse_draft: bool = False, refuse_agents: bool = False,
              ref: str | None = None) -> Handed:
        """A new turn, only if none is open (and the caller's other preconditions hold):
        a `deliver` with `only_if_idle`."""
        d = self._new("start", text, ref, refuse_draft=refuse_draft,
                      refuse_agents=refuse_agents)
        reason = self._unable("only-if-idle")
        early = reason is not None
        if reason is None:
            first, live, second = self._snapshot()
            with self._lock:
                reason = self._idle_refusal(first, second, if_turn_number)
                early = reason is not None
                if reason is None and refuse_agents and _busy(live):
                    reason = "busy-agents"
                if reason is None and refuse_draft and (live is None or live.draft):
                    reason = "draft"
                if reason is None:
                    d.version_seen = second.version
                    d.state = _INFLIGHT
                    d.handed_at = time.monotonic()
                    self._inflight = d.id
        if reason is not None:
            with self._lock:
                self._refuse(d, reason, early=early)
            return d
        self._hand(d, self.harness.submit)
        return d

    def deliver(self, text: str, *, steer: bool = False, ref: str | None = None) -> Handed:
        """A new turn now if the session is idle, else when the running turn ends. With
        `steer` on a harness that can, a running turn takes the text at once."""
        if self.harness.loading():
            # Held at the load gate. A steer-first deliver keeps that intent, so the
            # accepted load's running turn takes it.
            d = self._new("deliver", text, ref,
                          fallback=steer and "steer" in self.harness.can)
            with self._lock:
                call = self._admit(d)
            if call is not None:
                self._hand(d, call)
            return d
        if steer and "steer" in self.harness.can:
            # The steer and its fallback are decided under one hold of the lock, so a
            # turn that ends just before cannot bounce the text as `idle`: no open turn
            # means this is a plain deliver, a new turn now or after the one in flight.
            return self._steer(self._new("steer", text, ref), or_deliver=True)
        d = self._new("deliver", text, ref)
        with self._lock:
            call = self._admit(d)
        if call is not None:
            self._hand(d, call)
        return d

    def steer(self, text: str, *, ref: str | None = None) -> Handed:
        """The text into the running turn; `idle` when none runs."""
        d = self._new("steer", text, ref)
        with self._lock:
            reason = self._unable("steer")
            if reason is None and not self.ledger.read().turn_open:
                reason = "idle"
            if reason is not None:
                self._refuse(d, reason, early=True)
                return d
        return self._steer(d)

    def wait(self, delivery_id: str, until: str, timeout: float | None):
        """`Started | Refused | Lost` (until "started") or `Ended | Refused | Lost`
        (until "ended"), or None when `timeout` passes first."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                d = self._deliveries.get(delivery_id)
                if d is None:
                    return Lost(f"{LOST_UNKNOWN}: it was not made at this endpoint, or "
                                "it settled long ago", delivery_id)
                result = d.value(until)
                if result is not None:
                    return result
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def wait_ref(self, ref: str, until: str, timeout: float | None):
        """`wait` for the newest delivery carrying `ref`: `(delivery id, value)`, the
        value None when `timeout` passes first. With no delivery carrying it, `(None,
        Lost)` with a reason starting `unknown delivery`."""
        with self._lock:
            newest = next((d for d in reversed(self._deliveries.values())
                           if d.ref == ref), None)
        if newest is None:
            return None, Lost(f"{LOST_UNKNOWN}: no delivery at this endpoint carries this "
                              "ref", None, ref)
        return newest.id, self.wait(newest.id, until, timeout)

    def command(self, name: str, *, if_turn_number: int | None = None,
                timeout: float | None = COMMAND_WAIT_S,
                stop_shells: bool = False) -> Ran | Failed | Refused:
        """Run `/clear`, `/exit` or a compaction: consented, one in flight, one accepted
        per 60 s, from an idle snapshot with no open turn, no live subagent and no draft.
        `stop_shells` is refused `unsupported` until the adapter can stop shells: nothing
        runs."""
        if name not in COMMANDS:
            return Refused("unsupported")
        reason = self._unable(name)
        if reason is None and stop_shells:
            reason = "unsupported"
        if reason is None:
            allow = turns_config.allowed(self._config.use("commands.allow"), self.env)
            if name not in allow:
                reason = "not-allowed"
        if reason is None:
            reason = self._command_slot_refusal()
        if reason is None:
            first, live, second = self._snapshot()
            with self._lock:
                reason = self._idle_refusal(first, second, if_turn_number)
                if reason is None and (live is None or live.draft):
                    reason = "draft"         # an unreadable box holds
                if reason is None and _busy(live):
                    reason = "busy-agents"   # an unreadable list counts as busy
                if reason is None:
                    reason = self._command_slot_refusal()
                if reason is None:
                    request = CommandRequest(f"{self.nonce}-c{next(self._ids)}", name)
                    c = self._commands[request.id] = _Command(request, second.version)
                    self._command_inflight = request.id
                    self._command_accepted_at = time.monotonic()
        if reason is not None:
            return Refused(reason)
        try:
            self.harness.command(request)
        except Exception as exc:  # noqa: BLE001
            log.warning("the adapter failed to run /%s: %s", name, exc)
            with self._lock:
                self._settle_command(c, Failed(f"the adapter failed: {exc}"))
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._cond:
            while c.result is None:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return Failed(FAILED_TIMEOUT)
                self._cond.wait(remaining)
            return c.result

    # -- internals ---------------------------------------------------------------------

    def _new(self, kind: str, text: str, ref: str | None = None, **checks) -> Handed:
        d = Handed(Delivery(f"{self.nonce}-{next(self._ids)}", text, kind, **checks),
                   _QUEUED, ref=ref)
        with self._lock:
            self._deliveries[d.id] = d
        return d

    def _unable(self, capability: str) -> str | None:
        if self._gone is not None:
            return "gone"
        if capability not in self.harness.can:
            return "unsupported"
        if capability in ("only-if-idle", "steer") and self.harness.loading():
            return "loading"
        return None

    def _idle_refusal(self, first: Reading, second: Reading,
                      if_turn_number: int | None) -> str | None:
        """The ledger half of an idle snapshot, under the lock."""
        if self._gone is not None:
            return "gone"
        if first.version != second.version:
            return "stale-turn"
        # A delivery in flight or held is a turn about to open.
        if second.turn_open or self._inflight or self._queue:
            return "busy-turn"
        if if_turn_number is not None and if_turn_number != second.turn_number:
            return "stale-turn"
        return None

    def _command_slot_refusal(self) -> str | None:
        with self._lock:
            if self._command_inflight is not None:
                return "in-flight"
            if (self._command_accepted_at is not None
                    and time.monotonic() - self._command_accepted_at < COMMAND_RATE_S):
                return "rate"
            return None

    def _live(self) -> Live | None:
        try:
            return self.harness.live()
        except Exception as exc:  # noqa: BLE001 - an unreadable fact refuses, never passes
            log.warning("reading the live facts failed: %s", exc)
            return None

    def _snapshot(self) -> tuple[Reading, Live | None, Reading]:
        """The ledger, the live facts, the ledger again: one observation, or two readings
        that differ."""
        with self._lock:
            first = self.ledger.read()
        live = self._live()
        with self._lock:
            second = self.ledger.read()
        return first, live, second

    def _admit(self, d: Handed) -> Callable[[Delivery], None] | None:
        """A deliver, under the lock: refused, held behind the running turn or the one in
        flight, or put in flight now (returns the adapter call to hand it to)."""
        if self._gone is None and "deliver" in self.harness.can and self.harness.loading():
            self._queue.append(d.id)
            return None
        reason = self._unable("deliver")
        if reason is not None:
            self._refuse(d, reason, early=True)
            return None
        if self.ledger.read().turn_open or self._inflight or self._queue:
            self._queue.append(d.id)
            return None
        d.state = _INFLIGHT
        d.handed_at = time.monotonic()
        self._inflight = d.id
        return self.harness.submit

    def _steer(self, d: Handed, *, or_deliver: bool = False) -> Handed:
        """The text into the open turn. With no open turn: `idle`, or with `or_deliver`
        (a `deliver(steer=True)`) the deliver path, decided under the same lock."""
        with self._lock:
            turn = self.ledger.steered_into(d.id) if self._gone is None else None
            if turn is None:
                if not or_deliver:
                    self._refuse(d, "gone" if self._gone is not None else "idle",
                                 early=True)
                    return d
                d.spec = replace(d.spec, kind="deliver")
                call = self._admit(d)
            else:
                if or_deliver:
                    d.spec = replace(d.spec, fallback=True)
                d.state = _STARTED
                d.turn = turn
                d.started = Started(turn.turn_id)
                self._cond.notify_all()
                call = self.harness.append
        if call is not None:
            self._hand(d, call)
        return d

    def _hand(self, d: Handed, call: Callable[[Delivery], None]) -> None:
        try:
            call(d.spec)
        except Exception as exc:  # noqa: BLE001
            log.warning("the adapter could not take delivery %s: %s", d.id, exc)
            self.refused(d.id, "gone")

    def _expiry_loop(self) -> None:
        while not self._stopping.wait(min(1.0, max(0.01, self.pending_ttl_s / 4))):
            self._expire(time.monotonic())

    def _expire(self, now: float) -> None:
        """Settle the delivery or command the adapter has held unsettled past
        `pending_ttl_s`, and free its slot. A late word from the adapter about it is
        ignored, and its `entering` answers `expired`."""
        ttl = self.pending_ttl_s
        freed: str | None = None
        with self._lock:
            if self._gone is not None:
                return
            d = self._deliveries.get(self._inflight) if self._inflight else None
            if (d is not None and d.state == _INFLIGHT and d.handed_at is not None
                    and now - d.handed_at >= ttl):
                log.warning("the harness neither started nor refused delivery %s within "
                            "%d s; it answers lost", d.id, int(ttl))
                d.expired = True
                lost = Lost(f"{EXPIRED}: the harness neither started nor refused it "
                            f"within {ttl:g} s")
                self._settle(d, started=lost, ended=lost)
                self._inflight = None
                freed = d.id
            c = self._commands.get(self._command_inflight) if self._command_inflight else None
            if c is not None and c.result is None and now - c.handed_at >= ttl:
                log.warning("the harness did not settle /%s (%s) within %d s; it answers "
                            "failed", c.request.name, c.request.id, int(ttl))
                c.expired = True
                self._settle_command(c, Failed(f"{EXPIRED}: the harness did not settle it "
                                               f"within {ttl:g} s"))
        if freed is not None:
            try:
                self.harness.expired(freed)
            except Exception:  # noqa: BLE001 - the slot is freed either way
                log.exception("the adapter failed at the expiry of %s", freed)
            self._pump()

    def _pump(self) -> None:
        """Hand the next held delivery to the harness, once no turn runs and none is in
        flight. While a turn runs, a steer-first deliver held at the load gate at the head
        of the queue steers into it, unless that turn is ending: then it waits
        for the turn's end like any other."""
        while True:
            with self._lock:
                if (self._gone is not None or self._inflight or not self._queue
                        or self.harness.loading()):
                    return
                d = self._deliveries[self._queue[0]]
                turn = None
                if self.ledger.read().turn_open:
                    if not d.spec.fallback:
                        return
                    turn = self.ledger.steered_into(d.id)
                    if turn is None:
                        return   # the open turn is ending: no submit while it is open
                self._queue.popleft()
                if turn is None:
                    d.state = _INFLIGHT
                    d.handed_at = time.monotonic()
                    self._inflight = d.id
                    call = self.harness.submit
                else:
                    d.state = _STARTED
                    d.turn = turn
                    d.started = Started(turn.turn_id)
                    self._cond.notify_all()
                    call = self.harness.append
            self._hand(d, call)
            if turn is None:
                return

    def _refuse(self, d: Handed, reason: str, *, early: bool = False) -> None:
        """Settle `d` refused. `early`: the endpoint decided it from its own record
        before handing the delivery on, so the `deliver` or `steer` answer carries it."""
        refused = Refused(reason)
        if early and d.started is None:
            d.early = refused
        self._settle(d, started=refused if d.started is None else None, ended=refused)
        if self._inflight == d.id:
            self._inflight = None
        with contextlib.suppress(ValueError):
            self._queue.remove(d.id)

    def _settle(self, d: Handed, *, started=None, ended=None) -> None:
        if started is not None:
            d.started = started
        if ended is not None:
            d.ended = ended
        if d.ended is not None and d.state != _DONE:
            d.state = _DONE
            self._settled.append(d.id)
            while len(self._settled) > SETTLED_KEPT:
                self._deliveries.pop(self._settled.popleft(), None)
        self._cond.notify_all()

    def _settle_command(self, c: _Command, result) -> None:
        c.result = result
        if self._command_inflight == c.request.id:
            self._command_inflight = None
        self._cond.notify_all()

    def _held_turn(self, turn_id: str | None) -> Turn | None:
        for turn in self._held:
            if turn.turn_id == turn_id:
                return turn
        return None

    def _decide(self, turn: Turn, d: Handed | None) -> None:
        """Under the lock: a held turn's tie is decided. Raise its held events in order,
        naming `d` (or none: the person's turn), and settle `d` from them."""
        with contextlib.suppress(ValueError):
            self._held.remove(turn)
        turn.held = False
        if d is not None:
            turn.delivery_id = d.id
            d.state = _STARTED
            d.turn = turn
            d.started = Started(turn.turn_id)
            if self._inflight == d.id:
                self._inflight = None
            with contextlib.suppress(ValueError):
                self._queue.remove(d.id)
        self._emit(self._turn_started(turn))
        if turn.ended_held:
            self._turn_closed(turn)
        self._cond.notify_all()

    def _turn_closed(self, turn: Turn) -> None:
        if turn.held:
            turn.ended_held = True   # its TurnEnded waits with its TurnStarted
            return
        outcome = turn.outcome or "error"
        ended = Ended(turn.turn_id, outcome)
        for delivery_id in [turn.delivery_id, *turn.steered]:
            d = self._deliveries.get(delivery_id) if delivery_id else None
            if d is not None and d.state != _DONE:
                self._settle(d, ended=ended)
        self._emit(TurnEnded(turn.turn_id, turn.session_id, turn.turn_number, outcome,
                             self._turn_deliveries(turn), turn.command))
        turns_listeners.ask(self.registry,
                            self._event("turn", turn_id=turn.turn_id, outcome=outcome),
                            self.listen_wait_s)

    def _turn_started(self, turn: Turn) -> TurnStarted:
        return TurnStarted(turn.turn_id, turn.session_id, turn.turn_number,
                           self._turn_deliveries(turn)[:1] if turn.delivery_id else (),
                           turn.command)

    def _turn_deliveries(self, turn: Turn) -> tuple[DeliveryRef, ...]:
        """Every delivery `turn` took, the opening one first, then each steered one in
        the order taken."""
        out = []
        if turn.delivery_id:
            out.append(self._delivery_ref(turn.delivery_id, "opened"))
        out.extend(self._delivery_ref(i, "steered") for i in turn.steered)
        return tuple(out)

    def _delivery_ref(self, delivery_id: str, how: str | None = None) -> DeliveryRef:
        d = self._deliveries.get(delivery_id)
        return DeliveryRef(delivery_id, d.ref if d is not None else None, how)

    def _open_turn(self) -> OpenTurn | None:
        """Under the lock: the open turn as its `turn.started` named it, or None (no turn
        open, or one held for its tie, whose `turn.started` has not gone out)."""
        turn = self.ledger.open
        if turn is None or turn.held:
            return None
        opening = self._delivery_ref(turn.delivery_id) if turn.delivery_id else None
        return OpenTurn(turn.turn_id, opening, turn.command, turn.started_at)

    def _emit(self, event) -> None:
        """Under the lock: send a notification to every watch, in ledger order."""
        if isinstance(event, Ready):
            self._ready = event
        method, params = event.METHOD, event.to_params()
        for conn in self._watchers:
            conn.notify_threadsafe(method, params)

    def _event(self, name: str, **extra):
        return SessionEvent(event=name, harness=self.harness.name,
                            session_id=self.ledger.session_id, env=self.env, **extra)

    def _rewrite_hello(self) -> None:
        if self._server is None or self._gone is not None:
            return                  # not open yet (open() writes it), or ending
        try:
            self._write_hello()
        except OSError as exc:
            log.warning("rewriting %s failed: %s", self.hello_path, exc)


def _busy(live: Live | None) -> bool:
    """A subagent pending, running or waiting, or a list that could not be read: unknown
    counts as busy, never as 0."""
    return live is None or live.agents is None or live.agents > 0


def _moved(reading: Reading, version_seen: int | None) -> str | None:
    """Where a start or command takes effect: a turn opened, or the ledger moved, since
    the snapshot it passed."""
    if reading.turn_open:
        return "busy-turn"
    if reading.version != version_seen:
        return "stale-turn"
    return None


__all__ = ["COMMAND_RATE_S", "COMMAND_WAIT_S", "SETTLED_KEPT", "PENDING_TTL_S", "Endpoint",
           "Handed", "package_version"]
