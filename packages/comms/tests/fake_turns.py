"""A stand-in for one turns session, shaped as `moxi_turns.Session` is on turns' protocol
version 2: `capabilities`, `facts`, `watch` (with `.facts`), `handle_stops`, `deliver`
and `steer` with a `ref`, and the values turns hands back. The test drives the session's
turns by hand, as turns' Codex host would report them.

It imports nothing of turns: comms reaches turns only through its public API, and the
suite's end-to-end cases run turns' real library against `turns_endpoint.FakeEndpoint`.

How the next hand-in is answered is `answer` (every hand-in) or one `script` entry (the
next one), each one of:

    "start"             taken: a delivery opens a turn when idle, else waits for the
                        running turn's end; a steer joins the running turn
    "refuse"            refused `busy-turn` in the answer
    ("refuse", r)       refused `r` in the answer
    ("refuse-later", r) answered with an id; `wait_started` reads refused `r`
    "lose"              lost: the endpoint went away
    ("lose", r)         lost `r`
    "gone"              never written: `Refused("gone")`, no id
    "unknown"           written, no answer: no id, no value; the waits block until the
                        test calls `resolve(ref, value)` (or `take(ref)` for a steer)
    "hang"              answered with an id and no value ever (a wait times out)

A steer while no turn runs is refused `idle` in its answer, as the endpoint does.
"""

from __future__ import annotations

import itertools
import queue
import threading
from dataclasses import dataclass
from typing import NamedTuple

CODEX = frozenset({"deliver", "only-if-idle", "steer", "stop-handler", "compact",
                   "agents", "stop-shells"})
#: A Codex host that offers `mcp.reload` (turns' capability `mcp-token`).
CODEX_RELOAD = CODEX | {"mcp-token"}


@dataclass(frozen=True)
class DeliveryRef:
    id: str
    ref: str | None = None
    how: str | None = None


@dataclass(frozen=True)
class Started:
    turn_id: str | None
    delivery: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class Refused:
    reason: str
    delivery: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class Lost:
    reason: str
    delivery: str | None = None
    ref: str | None = None

    @property
    def untied(self) -> bool:
        return self.reason.startswith(("untied", "expired"))


@dataclass(frozen=True)
class Ended:
    turn_id: str | None
    outcome: str
    delivery: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class Facts:
    session_id: str | None
    turn_number: int
    turn_open: bool
    turn: object = None
    agents: int | None = 0
    draft: bool = False
    at: float = 0.0
    stale: bool = False
    ready: object = None


@dataclass(frozen=True)
class Ready:
    source: str | None
    session_id: str | None
    at: float | None = None


@dataclass(frozen=True)
class TurnStarted:
    turn_id: str | None
    session_id: str | None
    turn_number: int
    deliveries: tuple = ()
    command: str | None = None


@dataclass(frozen=True)
class TurnSteered:
    turn_id: str | None
    session_id: str | None
    delivery: DeliveryRef


@dataclass(frozen=True)
class TurnEnded:
    turn_id: str | None
    session_id: str | None
    turn_number: int
    outcome: str
    deliveries: tuple = ()
    command: str | None = None


@dataclass(frozen=True)
class Gone:
    reason: str


@dataclass(frozen=True)
class StopFacts:
    turn_id: str | None = None
    session_id: str | None = None
    stop_hook_active: bool | None = None
    task_types: tuple | None = None
    crons: int | None = None


class Handed(NamedTuple):
    """One hand-in, as the session recorded it. `steer` says it came by `steer`."""

    id: str | None
    text: str
    steer: bool
    ref: str | None


class FakeDelivery:
    """A `moxi_turns.Delivery`: `id` (None for an unknown outcome or a hand-in never
    written), `ref`, and the two waits. A wait with no value yet blocks until the test
    settles it, or raises `TimeoutError` when `timeout` passes first."""

    def __init__(self, delivery_id, ref=None, *, started=None, ended=None):
        self.id = delivery_id
        self.ref = ref
        self._values = {"started": started, "ended": ended}
        self._cond = threading.Condition()

    def __repr__(self) -> str:
        return f"FakeDelivery({self.id!r}, ref={self.ref!r})"

    def set(self, *, started=None, ended=None, delivery_id=None) -> None:
        with self._cond:
            if delivery_id is not None:
                self.id = delivery_id
            if started is not None and self._values["started"] is None:
                self._values["started"] = started
            if ended is not None:
                self._values["ended"] = ended
                if self._values["started"] is None:
                    self._values["started"] = ended
            self._cond.notify_all()

    def _wait(self, key, timeout):
        with self._cond:
            if not self._cond.wait_for(lambda: self._values[key] is not None, timeout):
                raise TimeoutError(f"delivery {self.id or self.ref!r} has not {key}")
            return self._values[key]

    def wait_started(self, timeout):
        return self._wait("started", timeout)

    def wait_ended(self, timeout):
        return self._wait("ended", timeout)


class FakeStops:
    def __init__(self, session, on_stop):
        self.session = session
        self.on_stop = on_stop
        self.closed = False

    def close(self):
        self.closed = True



class FakeWatch:
    """`watch()`'s value: `.facts` at the subscription, then the events."""

    def __init__(self, session, facts):
        self.facts = facts
        self._session = session
        self.closed = False

    def __iter__(self):
        while not self.closed:
            event = self._session._events.get()
            yield event
            if isinstance(event, Gone):
                return

    def close(self):
        self.closed = True
        self._session._events.put(Gone("the watch was closed"))


class FakeSession:
    """One hosted thread. See the module's docstring for `answer` and `script`."""

    harness = "codex"

    def __init__(self, session_id="thread-1", *, turn_open=False, capabilities=CODEX):
        self.session_id = session_id
        self.capabilities = frozenset(capabilities)
        self.answer = "start"
        self.script: list = []
        self.delivered: list[Handed] = []
        self.holder: FakeStops | None = None
        self.open_turn: str | None = None
        self.turn_deliveries: dict = {}    # turn id -> [DeliveryRef]
        self.handed: dict = {}             # delivery id -> FakeDelivery
        self.waiting: list = []            # delivery ids waiting for the turn's end
        self.unknown: dict = {}            # ref -> FakeDelivery of an unknown outcome
        self.closed = False
        #: Each `mcp_reload` call's `timeout`, and what it answers (or raises).
        self.reload_calls: list = []
        self.reload_answer = {"reloaded": False, "servers": {"comms": "connected"}}
        self._events: queue.Queue = queue.Queue()
        self._ids = itertools.count(1)
        self._turns = itertools.count(1)
        self._lock = threading.RLock()
        self._turn_number = 0
        if turn_open:
            self.start_turn()

    # -- what comms calls ---------------------------------------------------------------
    def facts(self):
        with self._lock:
            return Facts(self.session_id, self._turn_number, self.open_turn is not None)

    def watch(self):
        return FakeWatch(self, self.facts())

    def handle_stops(self, on_stop):
        self.holder = FakeStops(self, on_stop)
        return self.holder

    def close(self):
        self.closed = True

    def mcp_reload(self, *, timeout=22.0):
        if "mcp-token" not in self.capabilities:
            raise RuntimeError("the session does not offer mcp-token (the capability "
                               "error)")
        with self._lock:
            self.reload_calls.append(timeout)
            answer = self.reload_answer
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer)

    def deliver(self, text, *, ref=None, only_if_idle=False, if_turn_number=None,
                refuse_draft=False, refuse_agents=False):
        return self._hand(text, ref, "deliver")

    def steer(self, text, *, ref=None):
        if "steer" not in self.capabilities:
            raise RuntimeError("the session does not offer steer (the capability error)")
        return self._hand(text, ref, "steer")

    def _hand(self, text, ref, method):
        with self._lock:
            step = self.script.pop(0) if self.script else self.answer
            kind, reason = (step, None) if isinstance(step, str) else step
            if kind == "gone":
                self.delivered.append(Handed(None, text, method != "deliver", ref))
                return FakeDelivery(None, ref, started=Refused("gone"),
                                    ended=Refused("gone"))
            did = f"d{next(self._ids)}"
            self.delivered.append(Handed(did, text, method != "deliver", ref))
            if kind == "refuse":
                value = Refused(reason or "busy-turn", did, ref)
                return FakeDelivery(did, ref, started=value, ended=value)
            d = FakeDelivery(did, ref)
            self.handed[did] = d
            if kind == "refuse-later":
                d.set(ended=Refused(reason, did, ref))
                return d
            if kind == "lose":
                d.set(ended=Lost(reason or "the endpoint went away", did, ref))
                return d
            if kind == "hang":
                return d
            if method == "steer" and self.open_turn is None:
                value = Refused("idle", did, ref)
                d.set(ended=value)
                return d
            if kind == "unknown":
                self.unknown[ref] = d
                d.id = None
                d._pending = (did, method)
                return d
            self._take(d, did, method)
            return d

    def _take(self, d, did, method):
        """The harness takes a hand-in: a steer joins the running turn; a delivery opens
        a turn when idle and waits for the turn's end otherwise."""
        turn = self.open_turn
        if method == "steer" and turn is not None:
            ref = DeliveryRef(did, d.ref, "steered")
            self.turn_deliveries.setdefault(turn, []).append(ref)
            d.set(started=Started(turn, did, d.ref))
            self._events.put(TurnSteered(turn, self.session_id, DeliveryRef(did, d.ref)))
        elif turn is None:
            self.start_turn(did)
        else:
            self.waiting.append(did)

    # -- what the test drives -----------------------------------------------------------
    def resolve(self, ref, value=None) -> None:
        """An unknown hand-in's outcome arrives: `value` (a `Refused` or `Lost`), or,
        with None, the harness takes it as it would have."""
        with self._lock:
            d = self.unknown.pop(ref)
            did, method = d._pending
            if value is None:
                d.id = did
                self._take(d, did, method)
            else:
                d.set(ended=value, delivery_id=did)

    def start_turn(self, delivery_id=None) -> str:
        with self._lock:
            turn = f"turn-{next(self._turns)}"
            self.open_turn = turn
            self._turn_number += 1
            refs = []
            if delivery_id is not None:
                d = self.handed.get(delivery_id)
                ref = DeliveryRef(delivery_id, d.ref if d else None, "opened")
                refs.append(ref)
                if d is not None:
                    d.set(started=Started(turn, delivery_id, ref.ref))
            self.turn_deliveries[turn] = list(refs)
            self._events.put(TurnStarted(turn, self.session_id, self._turn_number,
                                         tuple(refs)))
            return turn

    def end_turn(self, outcome="answer") -> str:
        with self._lock:
            turn, self.open_turn = self.open_turn, None
            refs = tuple(self.turn_deliveries.pop(turn, []))
            for r in refs:
                d = self.handed.get(r.id)
                if d is not None:
                    d.set(ended=Ended(turn, outcome, r.id, r.ref))
            self._events.put(TurnEnded(turn, self.session_id, self._turn_number, outcome,
                                       refs))
            if self.waiting:
                self.start_turn(self.waiting.pop(0))
            return turn

    def stop(self, **facts):
        """A Stop of the running turn: what the stop handler answers."""
        return self.holder.on_stop(StopFacts(turn_id=self.open_turn,
                                             session_id=self.session_id, **facts))

    def ready(self, source):
        self._events.put(Ready(source, self.session_id))

    def gone(self, reason="the Codex window closed with status 0"):
        with self._lock:
            for d in self.handed.values():
                d.set(ended=Lost(reason, d.id, d.ref))
            for d in self.unknown.values():
                d.set(ended=Lost(reason, None, d.ref))
        self._events.put(Gone(reason))


def fake_find(session: FakeSession):
    """A `find` stand-in (turns' `wait_ready(pid=)`) that records its call and hands
    back `session`."""
    calls: list = []

    def find(pid, timeout):
        calls.append({"pid": pid, "timeout": timeout})
        return session

    find.calls = calls
    return find
