"""The agents a session directs that are at work, read from comms and turns.

`directed` is the one read of them: Home's Sessions row counts them, and keep-warm's ask
and the keep-warm route decide on them, so the row and the ask can't disagree. Every call
it makes goes through one `Reads`, whose deadline and offline stop bound it whoever
calls it. It reports what it found and each way its read fell short, as separate facts
of a `Directed`; each caller derives its own answer from them.

The read: comms' `owner` of the session, comms' `roster` (once per `Reads`), its bound
agents whose `directors` name that owner, comms' `session_of` for each, then turns' `find`
of that session and its `facts`. An agent is at work as `works` reads it. Each comms
read may take `COMMS_TIMEOUT_S`. A comms that is installed and does not load stands as
`UNLOADED`, and a read through it is one that raised.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: Seconds each of comms' reads may take.
COMMS_TIMEOUT_S = 2.0

#: Stands for comms' API where comms is installed but does not load: `directed` reads
#: through it as a read that raised, not as comms not installed.
UNLOADED: Any = object()

log = logging.getLogger("moxi.launcher.waits")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


class Late(Exception):
    """A call asked for at or past a `Reads`'s deadline: the reader stops there."""


class Reads:
    """What bounds a set of reads: a deadline on `clock` after which no call starts, and
    comms' offline stop. `comms` and `turns` are their modules' APIs (None for one not
    installed; `comms` is `UNLOADED` for a comms installed that does not load).

    `shared` holds what is read once for every reader of this `Reads`; `offline` is
    whether comms' daemon was found down, after which no comms call is made through it."""

    def __init__(self, *, deadline: float, clock: Callable[[], float] = time.monotonic,
                 comms: Any | None = None, turns: Any | None = None):
        self.deadline = deadline
        self.clock = clock
        self.comms = comms
        self.turns = turns
        self.offline = False
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._shared: dict[str, tuple[bool, Any]] = {}

    def call(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """`fn(*args, **kwargs)`, or `Late` when the deadline has passed."""
        if self.clock() >= self.deadline:
            raise Late()
        return fn(*args, **kwargs)

    def comms_call(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """`call` of one of comms' reads, unless an earlier one found the daemon down:
        then, and when this one does, comms' `DaemonOffline`."""
        offline = self.comms.DaemonOffline
        if self.offline:
            raise offline("comms' daemon was found down earlier in these reads")
        try:
            return self.call(fn, *args, **kwargs)
        except offline:
            self.offline = True
            raise

    def shared(self, name: str, fn: Callable[[], Any]) -> Any:
        """What `fn()` gives, called once whichever reader asks first; an exception it
        raised is raised again to every reader that asks. `Late` is not kept. Each name
        has its own lock, so a reader waits only on a call of the same name; `fn` makes
        its calls through `call`."""
        with self._lock:
            lock = self._locks.setdefault(name, threading.Lock())
        with lock:
            if name not in self._shared:
                try:
                    self._shared[name] = (True, fn())
                except Late:
                    raise
                except Exception as exc:
                    self._shared[name] = (False, exc)
            ok, value = self._shared[name]
        if not ok:
            raise value
        return value


@dataclass(frozen=True)
class Directed:
    """What `directed` found. `names`: the qualified names of the agents found at work
    before it stopped, in roster order. `raised`: a comms or turns read raised. `late`: a
    call met the deadline. `unlisted`: a directed Claude session's turn is closed and turns
    could not list its subagents, so that agent was not counted."""

    names: tuple[str, ...] = ()
    raised: bool = False
    late: bool = False
    unlisted: bool = False

    @property
    def sure(self) -> bool:
        """Whether the read was whole: nothing raised, met the deadline or went unlisted."""
        return not (self.raised or self.late or self.unlisted)


def directed(reads: Reads, sid: str) -> Directed:
    """The agents session `sid`'s comms name directs that are at work. Stops at the first
    read that raises or meets the deadline, and raises nothing of its own reads. A read
    that fails other than with comms' `DaemonOffline` or `DaemonError` is logged once per
    kind of failure. Without comms installed, nobody is directed; with comms `UNLOADED`,
    the read raised."""
    comms = reads.comms
    if comms is None:
        return Directed()
    if comms is UNLOADED:
        return Directed(raised=True)
    names: list[str] = []
    unlisted = False
    try:
        owner = reads.comms_call(comms.owner, sid, timeout_s=COMMS_TIMEOUT_S).identity
        if not owner:
            return Directed()
        roster = reads.shared("roster", lambda: reads.comms_call(
            comms.roster, timeout_s=COMMS_TIMEOUT_S))
        for agent in roster.bound:
            if owner not in tuple(agent.directors or ()):
                continue
            agent_sid = reads.comms_call(comms.session_of, agent.qualified,
                                         timeout_s=COMMS_TIMEOUT_S)
            if agent_sid is None:
                continue
            session = reads.call(reads.turns.find, session_id=agent_sid)
            if session is None:
                continue
            try:
                facts = reads.call(session.facts)
            finally:
                session.close()
            harness = getattr(session, "harness", None)
            if works(facts, harness):
                names.append(agent.qualified)
            elif _unlisted(facts, harness):
                unlisted = True
    except Late:
        return Directed(tuple(names), late=True, unlisted=unlisted)
    except Exception as exc:  # noqa: BLE001  (each failure is a fact the caller reads)
        if not isinstance(exc, (comms.DaemonOffline, comms.DaemonError)):
            _log_once(exc, "reading the agents a session directs failed")
        return Directed(tuple(names), raised=True, unlisted=unlisted)
    return Directed(tuple(names), unlisted=unlisted)


def works(facts: Any, harness: str | None) -> bool:
    """Whether a directed agent's session works: its turn is open, or it is a Claude
    session whose closed turn waits on subagents of its own (`facts.agents` above zero;
    for Codex that count holds background terminals too, so it is not read)."""
    if getattr(facts, "turn_open", None) is True:
        return True
    if harness == "codex":
        return False
    busy = getattr(facts, "agents", None)
    return isinstance(busy, int) and not isinstance(busy, bool) and busy > 0


def _unlisted(facts: Any, harness: str | None) -> bool:
    return (harness != "codex" and getattr(facts, "turn_open", None) is not True
            and getattr(facts, "agents", None) is None)


__all__ = ["COMMS_TIMEOUT_S", "UNLOADED", "Directed", "Late", "Reads", "directed", "works"]
