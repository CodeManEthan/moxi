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
- hands every delivery and command to the harness on one ordered worker (below);
- checks a session command's consent and its idle snapshot;
- asks the session's stop handler at each Stop, bounded by its wait;
- asks the registered listeners at `ready` and `prompt` and passes back what they
  answer, and tells them of `turn` and `gone`.

turns decides nothing about content or timing: it delivers what it is handed, when it is
handed it, under the preconditions the caller names.

`open()` opens the endpoint; `stop()` (or the adapter's `gone()`) ends it. The core's
caller-side methods (`session`, `facts`, `agents`, `deliver`, `steer`, `wait`, `command`)
take plain values, so a test can drive the core without the wire.

The address is served by an asyncio loop on a thread of its own (`moxi.aio.run`). The
core's lock is a thread lock; the loop takes it only for steps that never wait on the
harness, and every notification leaves through `Connection.notify_threadsafe`, called
under that lock, so each watch gets its notifications in the order the ledger saw them.

The rule: no handler on the loop waits on the harness or on a blocking write.

- `deliver` and `steer` run on the loop. Under the lock each records the delivery, takes
  the checks the ledger alone decides (`busy-turn`, `stale-turn`, `idle`, `loading`), and
  puts the hand-off on the worker in the same locked step, then answers. A check that
  needs the harness (a draft, a live subagent) is part of the delivery's settled value,
  made by the adapter where the delivery takes effect.
- The worker is one thread per endpoint (an endpoint serves one session). It runs the
  adapter's `submit`, `append` and `command` one at a time, in the order the endpoint
  recorded them, so hand-offs reach the harness in that order. It skips a hand-off whose
  delivery or command settled while it waited, and passes a skipped steer to the
  adapter's `expired`. A hand-off may block the worker (a line write the runner does not
  read, a request the harness is slow to answer); callers still see every delivery and
  command settle, by `PENDING_TTL_S` at the latest, and each one queued behind it
  settles the same way, so nothing reaches the harness after its caller was told `lost`
  or `failed`. Only the hand-off already under way may still reach the harness late: its
  caller read `lost`, the harness may run the text anyway, and the harness's late word
  about it is ignored. That duplicate is the accepted cost of the bound.
- Reads a caller asks for (`facts`, `agents`, and `command`'s idle check) hand nothing to
  the harness and run beside the worker, on reader threads of their own (one per kind of
  read, never the threads open waits use). Each answers within the harness's live bound
  (`Harness.live_wait_s`): past it the live part reads unreadable (`draft` true, `agents`
  null, an incomplete list), which refuses and never passes. A read still out is joined,
  not started again, so a stalled harness ties up one thread per kind, not one per call.
"""

from __future__ import annotations

import asyncio
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
from .results import (COMMANDS, DELIVERY_REFUSALS, FAILED_TIMEOUT, LOST_UNKNOWN, OUTCOMES,
                      Agents, DeliveryRef, Ended, Facts, Failed, Gone, Lost, OpenTurn,
                      Passed, Ran, Ready, Refused, SessionEvent, Started, StopFacts,
                      TurnEnded, TurnStarted, TurnSteered)

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

#: A delivery's states: held behind a turn or the load gate; handed to `submit` and not
#: yet tied; a steer handed to `append` and not yet confirmed; taken into a turn; settled.
_QUEUED, _INFLIGHT, _STEERING = "queued", "in-flight", "steering"
_STARTED, _DONE = "started", "done"


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


class _Worker:
    """The session's one ordered worker: a thread that runs hand-offs one at a time, in
    the order they were put. Its thread starts at the first hand-off."""

    def __init__(self, name: str):
        self._name = name
        self._jobs: deque[Callable[[], None]] = deque()
        self._cond = threading.Condition()
        self._closed = False
        self._thread: threading.Thread | None = None

    def put(self, job: Callable[[], None]) -> None:
        with self._cond:
            if self._closed:
                return
            self._jobs.append(job)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name=self._name)
                self._thread.start()
            self._cond.notify()

    def close(self) -> None:
        """Run nothing more: the hand-offs still queued are dropped."""
        with self._cond:
            self._closed = True
            self._jobs.clear()
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._jobs and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                job = self._jobs.popleft()
            try:
                job()
            except Exception:  # noqa: BLE001 - one hand-off never ends the worker
                log.exception("a hand-off to the harness failed")


class _Joined:
    """One kind of live read: at most one out at a time, on a thread of its own. A caller
    that asks while a read is out joins it, so a stalled harness ties up one thread."""

    def __init__(self, name: str, read: Callable[[], Any]):
        self._name = name
        self._read = read
        self._lock = threading.Lock()
        self._future: concurrent.futures.Future | None = None

    def future(self) -> concurrent.futures.Future:
        """The read out now, or a new one."""
        with self._lock:
            future = self._future
            if future is None or future.done():
                future = self._future = concurrent.futures.Future()
                threading.Thread(target=self._run, args=(future,), daemon=True,
                                 name=self._name).start()
            return future

    def _run(self, future: concurrent.futures.Future) -> None:
        try:
            future.set_result(self._read())
        except BaseException as exc:  # noqa: BLE001 - handed to the reader
            future.set_exception(exc)

    def wait(self, timeout: float) -> Any:
        """The read's result, or None when it is not done within `timeout` or failed."""
        try:
            return self.future().result(timeout=max(0.0, timeout))
        except concurrent.futures.TimeoutError:
            return None
        except Exception:  # noqa: BLE001 - unreadable
            return None

    async def wait_async(self, timeout: float) -> Any:
        """`wait` on the loop: never blocks it."""
        future = asyncio.wrap_future(self.future())
        done, _ = await asyncio.wait({future}, timeout=max(0.0, timeout))
        if not done or future.cancelled() or future.exception() is not None:
            return None
        return future.result()


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
        #: The one ordered worker every hand-off to the harness runs on.
        self._worker = _Worker("turns-endpoint-worker")
        #: The live reads a caller asks for, each bounded and joined while out.
        self._live_read = _Joined("turns-live-read", self._live)
        self._agents_read = _Joined("turns-agents-read", self._agents_now)
        #: A test's hook: when set, `await answer_hook(method, handed)` runs after a
        #: `deliver` or `steer` is recorded and before its answer is written, so a test
        #: can hold the answer past a caller's read bound. Never set outside tests.
        self.answer_hook: Callable[[str, Handed], Any] | None = None

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
            self._worker.close()
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
        answer). The turn stays open until `turn_ended`, so `turn_open` holds and an
        only-if-idle delivery is still refused, but it takes no more steers: a `steer`
        reads `idle`, and a plain `deliver` waits and starts a new turn after it. Codex can
        write a text it takes in that gap into the turn and complete the turn without
        answering it."""
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
        reason starts `untied`; a steer Codex did not answer). It answers `Lost` and frees
        its slot."""
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is None or d.state == _DONE:
                return
            self._unsteer(d)
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
        """A delivery is about to take effect: an only-if-idle one is re-checked against
        the ledger version its answer saw (`busy-turn`, `stale-turn`). A reason refuses it
        (the delivery answers `Refused`); None lets it in. Any other delivery passes
        untouched."""
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is not None and d.expired:
                return EXPIRED
            if d is None or d.state != _INFLIGHT:
                return "gone" if self._gone is not None else None
            if not d.spec.only_if_idle:
                return None
            reason = _moved(self.ledger.read(), d.version_seen)
            if reason is not None:
                self._refuse(d, reason)
        if reason is not None:
            self._pump()
        return reason

    def refused(self, delivery_id: str, reason: str) -> None:
        """The adapter refused a delivery it was handed (a failed check where it took
        effect, a steer the harness had no turn for, a harness that would not take the
        text). The text never ran. A reason the contract does not name for a delivery
        (an adapter's own word, such as `gone` for a hand-off that failed) reads
        `unsupported`: the harness turned it down after the endpoint offered it."""
        if reason not in DELIVERY_REFUSALS and reason != EXPIRED:
            log.info("delivery %s refused by the adapter (%s); it reads unsupported",
                     delivery_id, reason)
            reason = "unsupported"
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is None or d.state == _DONE:
                return
            self._unsteer(d)
            self._refuse(d, reason)
        self._pump()

    def steered(self, delivery_id: str) -> None:
        """The harness took a steer into the running turn (Claude: `$.session.append`
        resolved without a deny; Codex: `turn/steer` answered). The delivery reads
        `Started` on the turn it was recorded against, the turn lists it, and watchers get
        `turn.steered`. A word on a steer that has settled meanwhile (expired, the endpoint
        ending) changes nothing."""
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is None or d.state != _STEERING or d.turn is None:
                log.info("steer %s confirmed after it settled; it changes nothing",
                         delivery_id)
                return
            turn = d.turn
            d.state = _STARTED
            d.started = Started(turn.turn_id)
            if turn is self.ledger.open or turn in self._held:
                turn.steered.append(d.id)
                if not turn.held:
                    self._emit(self._turn_steered(turn, d.id))
            else:
                # The harness reported the turn's end before its word on the steer: the
                # text went into a turn whose `turn.ended` has gone out without it.
                log.warning("steer %s confirmed after its turn %s ended; it ends with "
                            "that turn", d.id, turn.turn_id)
                self._settle(d, ended=Ended(turn.turn_id, turn.outcome or "error"))
            self._cond.notify_all()

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
        ledger moved between. The live read is bounded by the harness's live bound; past
        it the live part reads unreadable."""
        first = self.reading()
        live = self.read_live()
        return self.facts_from(first, live)

    async def facts_async(self) -> Facts:
        """`facts` on the loop: the live read awaited within its bound, never blocking."""
        first = self.reading()
        live = await self.live_async()
        return self.facts_from(first, live)

    def reading(self) -> Reading:
        with self._lock:
            return self.ledger.read()

    async def live_async(self) -> Live | None:
        """`read_live` on the loop, never blocking it."""
        return await self._live_read.wait_async(self.harness.live_wait_s)

    def read_live(self) -> Live | None:
        """The live facts within the harness's live bound, or None (unreadable)."""
        return self._live_read.wait(self.harness.live_wait_s)

    def facts_from(self, first: Reading, live: Live | None) -> Facts:
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
        """The session's subagents as the harness records them, within the harness's live
        bound; past it, or unreadable, the list is not complete, which counts as busy."""
        agents = self._agents_read.wait(self.harness.live_wait_s)
        return agents if agents is not None else Agents((), False, time.time())

    async def agents_async(self) -> Agents:
        """`agents` on the loop, never blocking it."""
        agents = await self._agents_read.wait_async(self.harness.live_wait_s)
        return agents if agents is not None else Agents((), False, time.time())

    def _agents_now(self) -> Agents:
        """The adapter's list. Until the adapter can list them, the list is not complete,
        which counts as busy."""
        return Agents((), False, time.time())

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

    def deliver(self, text: str, *, ref: str | None = None, only_if_idle: bool = False,
                if_turn_number: int | None = None, refuse_draft: bool = False,
                refuse_agents: bool = False) -> Handed:
        """A new turn now if the session is idle, else when the running turn ends; held at
        a load gate until a load is accepted.

        `only_if_idle`: a new turn only if none is open, and only if the caller's other
        preconditions hold. The answer takes the checks the ledger alone decides:
        `busy-turn` when a turn is open or a delivery is in flight or held, `stale-turn`
        when `if_turn_number` differs from the ledger's, `loading` with no accepted load.
        `refuse_draft` and `refuse_agents` need the harness's live read, which the adapter
        makes where the delivery takes effect, so their refusal is the settled value.

        Never waits on the harness: the delivery is recorded and its hand-off put on the
        worker under one hold of the lock (`early` holds a refusal decided here)."""
        with self._lock:
            d = self._new("deliver", text, ref, only_if_idle=only_if_idle,
                          refuse_draft=refuse_draft, refuse_agents=refuse_agents)
            if not only_if_idle:
                self._admit(d)
                return d
            reason = self._unable("only-if-idle")
            reading = self.ledger.read()
            if reason is None:
                reason = self._busy_refusal(reading, if_turn_number)
            if reason is not None:
                self._refuse(d, reason, early=True)
                return d
            d.version_seen = reading.version
            self._hand_submit(d)
            return d

    def steer(self, text: str, *, ref: str | None = None) -> Handed:
        """The text into the running turn: `idle` when none runs (or the open one is
        ending), `loading` with no accepted load. Never waits on the harness: the steer is
        recorded against the open turn and its `append` put on the worker under one hold
        of the lock. It reads `Started`, and watchers get `turn.steered`, once the adapter
        confirms the harness took it (`steered`); the harness's refusal settles it
        `refused` `idle`. It never becomes a turn of its own."""
        with self._lock:
            d = self._new("steer", text, ref)
            reason = self._unable("steer")
            turn = self.ledger.open
            if reason is None and (turn is None or turn.ending):
                reason = "idle"
            if reason is not None:
                self._refuse(d, reason, early=True)
                return d
            d.state = _STEERING
            d.turn = turn
            d.handed_at = time.monotonic()
            try:
                self.harness.steering(d.id)
            except Exception:  # noqa: BLE001 - the adapter's own state; logged
                log.exception("the adapter failed to note steer %s", d.id)
            self._worker.put(self._handoff(d, self.harness.append))
            return d

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
        runs. The snapshot's live read is bounded (`read_live`); the command is recorded
        and its hand-off put on the worker under one hold of the lock, after any hand-off
        recorded before it, and this call waits for its settle."""
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
                    self._worker.put(self._command_handoff(c))
        if reason is not None:
            return Refused(reason)
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
        """Under the lock: record a new delivery."""
        d = Handed(Delivery(f"{self.nonce}-{next(self._ids)}", text, kind, ref=ref,
                            **checks), _QUEUED, ref=ref)
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
        """The ledger half of a command's idle snapshot, under the lock."""
        if self._gone is not None:
            return "gone"
        if first.version != second.version:
            return "stale-turn"
        return self._busy_refusal(second, if_turn_number)

    def _busy_refusal(self, reading: Reading, if_turn_number: int | None) -> str | None:
        """Under the lock: `busy-turn` when a turn is open or a delivery is in flight or
        held (a turn about to open), `stale-turn` when `if_turn_number` is given and
        differs from the ledger's."""
        if self._gone is not None:
            return "gone"
        if reading.turn_open or self._inflight or self._queue:
            return "busy-turn"
        if if_turn_number is not None and if_turn_number != reading.turn_number:
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
        """The ledger, the live facts (bounded), the ledger again: one observation, or two
        readings that differ."""
        first = self.reading()
        live = self.read_live()
        return first, live, self.reading()

    def _admit(self, d: Handed) -> None:
        """A plain deliver, under the lock: refused, held behind the running turn, the one
        in flight or the load gate, or put in flight now."""
        if self._gone is None and "deliver" in self.harness.can and self.harness.loading():
            self._queue.append(d.id)
            return
        reason = self._unable("deliver")
        if reason is not None:
            self._refuse(d, reason, early=True)
            return
        if self.ledger.read().turn_open or self._inflight or self._queue:
            self._queue.append(d.id)
            return
        self._hand_submit(d)

    def _hand_submit(self, d: Handed) -> None:
        """Under the lock: `d` takes the in-flight slot and its `submit` goes on the
        worker."""
        d.state = _INFLIGHT
        d.handed_at = time.monotonic()
        self._inflight = d.id
        self._worker.put(self._handoff(d, self.harness.submit))

    def _handoff(self, d: Handed, call: Callable[[Delivery], None]) -> Callable[[], None]:
        """The worker's job for delivery `d`: hand it to the adapter unless it settled
        while it waited. A skipped steer goes to the adapter's `expired`, so it lets go of
        it (on Codex, a held `turn/completed`)."""

        def run() -> None:
            with self._lock:
                skip = d.state not in (_INFLIGHT, _STEERING) or d.expired
                gone = self._gone is not None
            if skip:
                log.info("delivery %s settled before its hand-off; skipped", d.id)
                if d.spec.kind == "steer" and not gone:
                    self._tell_expired([d.id])
                return
            try:
                call(d.spec)
            except Exception as exc:  # noqa: BLE001
                log.warning("the adapter could not take delivery %s: %s", d.id, exc)
                self.refused(d.id, "unsupported")

        return run

    def _command_handoff(self, c: _Command) -> Callable[[], None]:
        """The worker's job for command `c`: hand it to the adapter unless it settled
        (expired) while it waited."""

        def run() -> None:
            with self._lock:
                skip = c.result is not None
            if skip:
                log.info("/%s (%s) settled before its hand-off; skipped",
                         c.request.name, c.request.id)
                return
            try:
                self.harness.command(c.request)
            except Exception as exc:  # noqa: BLE001
                log.warning("the adapter failed to run /%s: %s", c.request.name, exc)
                with self._lock:
                    if c.result is None:
                        self._settle_command(c, Failed(f"the adapter failed: {exc}"))

        return run

    def _tell_expired(self, delivery_ids: list[str]) -> None:
        for delivery_id in delivery_ids:
            try:
                self.harness.expired(delivery_id)
            except Exception:  # noqa: BLE001 - the delivery has settled either way
                log.exception("the adapter failed at the expiry of %s", delivery_id)

    def _expiry_loop(self) -> None:
        while not self._stopping.wait(min(1.0, max(0.01, self.pending_ttl_s / 4))):
            self._expire(time.monotonic())

    def _expire(self, now: float) -> None:
        """Settle every delivery or command the adapter has held unsettled past
        `pending_ttl_s` (the submit in flight, each steer not yet confirmed, the command),
        and free its slot. A late word from the adapter about it is ignored, and its
        `entering` answers `expired`. The adapter's `expired` runs on a thread of its own:
        an adapter stuck in a hand-off never holds up this sweep."""
        ttl = self.pending_ttl_s
        freed: list[str] = []
        with self._lock:
            if self._gone is not None:
                return
            d = self._deliveries.get(self._inflight) if self._inflight else None
            if (d is not None and d.state == _INFLIGHT and d.handed_at is not None
                    and now - d.handed_at >= ttl):
                log.warning("the harness neither started nor refused delivery %s within "
                            "%d s; it answers lost", d.id, int(ttl))
                self._expire_delivery(d, "neither started nor refused it")
                self._inflight = None
                freed.append(d.id)
            for d in list(self._deliveries.values()):
                if (d.state == _STEERING and d.handed_at is not None
                        and now - d.handed_at >= ttl):
                    log.warning("the harness neither took nor refused steer %s within "
                                "%d s; it answers lost", d.id, int(ttl))
                    self._expire_delivery(d, "neither took nor refused it")
                    freed.append(d.id)
            c = self._commands.get(self._command_inflight) if self._command_inflight else None
            if c is not None and c.result is None and now - c.handed_at >= ttl:
                log.warning("the harness did not settle /%s (%s) within %d s; it answers "
                            "failed", c.request.name, c.request.id, int(ttl))
                c.expired = True
                self._settle_command(c, Failed(f"{EXPIRED}: the harness did not settle it "
                                               f"within {ttl:g} s"))
        if freed:
            self._pump()
            threading.Thread(target=self._tell_expired, args=(freed,), daemon=True,
                             name="turns-endpoint-expired").start()

    def _expire_delivery(self, d: Handed, what: str) -> None:
        """Under the lock: `d` reads `Lost` `expired`."""
        d.expired = True
        lost = Lost(f"{EXPIRED}: the harness {what} within {self.pending_ttl_s:g} s")
        d.turn = None
        self._settle(d, started=lost, ended=lost)

    def _pump(self) -> None:
        """Put the next held delivery in flight, once no turn runs, none is in flight and
        a load is accepted."""
        with self._lock:
            if (self._gone is not None or self._inflight or not self._queue
                    or self.harness.loading() or self.ledger.read().turn_open):
                return
            self._hand_submit(self._deliveries[self._queue.popleft()])

    def _unsteer(self, d: Handed) -> None:
        """Under the lock: a steer settling without the harness taking it leaves the turn
        it was recorded against."""
        if d.turn is not None and d.id in d.turn.steered:
            d.turn.steered.remove(d.id)
            d.started = None
        if d.state == _STEERING:
            d.turn = None

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
        for steered in turn.steered:         # taken while the turn was held
            self._emit(self._turn_steered(turn, steered))
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

    def _turn_steered(self, turn: Turn, delivery_id: str) -> TurnSteered:
        return TurnSteered(turn.turn_id, turn.session_id, self._delivery_ref(delivery_id))

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
