"""The quiet read: whether a launched session may be acted on now, and if not, why.

Every act on a launched session (a relaunch, an exit, a clear or compact) takes this one
read before it acts, from turns and slots only. A keep-warm nudge reads its own signals
instead (`keepwarm`): it goes exactly while subagents work or holds are open. Each thing
that holds the act is a word, and the words come in this order:

| word | read | holds when |
|---|---|---|
| `turn` | `facts().turn_open` | a turn is open |
| `subagents` | `agents()`: `subagent` or `teammate` entries not final, in `pending`, `running`, `waiting` or `unknown` | any, or the list is incomplete |
| `draft` | `facts().draft` | a draft is in the prompt box |
| `person` | slot: `pane_use` (copy mode, or an attached client active within `person_s`); every target: a turn no delivery opened, started within `person_s` | either |
| `unreadable` | any read failed, or `facts().stale` | always |

**The `subagents` words name each live entry**: its description (else its id), kind and
state, and, where turns' stale judgment read one (`last_write_at`), how long ago it last
wrote and when it would read stale (that time plus turns' `STALE_AFTER_S`). They then say
that messaging the session or ending its shells would wake it, and, where the caller
says whether keep-warm is on for the session (`keep_warm`), that keep-warm should have
kept it warm, or that keep-warm is off for it.

**A `shell` entry never counts as live work** in this read, whichever harness reports
it: background shells stop at Claude's exit dialog, or with a clear or compact's stop.
Exit-session reads them itself after this read (`exits.background_shells`), and holds
for them until the session has been idle an hour; park's exit doesn't read them.

**The pane's own output never holds.** `pane_use` also gives the pane's activity, which
any output moves: Claude Code redrawing its spinner and status line, or its exit dialog
drawing itself. It says nothing of a person, so the read takes no account of it.

A caller may leave out the client's activity (`count_activity=False`): park does, since
the person who parks is the one at the panes. Copy mode still holds then, and so does a
turn no delivery opened.

**A turn no delivery opened holds as a person's, whatever way it came.** turns marks such
a turn with `opening` and `command` both None, in `facts().turn` and as a `turn.started`
with no deliveries and no command. That covers a turn typed in the terminal and one sent
by Remote Control, and also a harness wake (a subagent's or shell's completion notice),
which turns does not tell apart; so the words say "a turn no delivery opened", never that
a person was seen. A one-off read sees only the open turn; a `Watch` also keeps the start
of the latest such turn from its events (`turn.started` carries no time, so the time it
arrived), and for the turn open when the watch began, `facts.turn.started_at`.

While `turn_open` is true and `turn` is None, turns has not yet tied the turn to its
opener: `turn` holds anyway, and the watch learns whose turn it was from its
`turn.started`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import slots

log = logging.getLogger("moxi.launcher.quiet")

#: The words, in the order a reading lists them.
WORDS = ("turn", "subagents", "draft", "person", "unreadable")
#: The agent kinds that count as live work; a `shell` never does.
LIVE_KINDS = ("subagent", "teammate")
#: The agent states that count as live work while the agent's notice is not final.
LIVE_STATES = ("pending", "running", "waiting", "unknown")
#: How recent a person's sign must be to hold, in seconds, unless the caller says.
PERSON_S = 120.0
#: Stands for a value `judge` looks up itself.
LOOK: Any = object()


@dataclass(frozen=True)
class Hold:
    """One thing that holds the act: its `word` and the words for a person."""

    word: str
    detail: str

    def __str__(self) -> str:
        return f"{self.word}: {self.detail}"


@dataclass(frozen=True)
class Reading:
    """One quiet read: what was read, and what holds.

    `facts`, `agents` and `pane` are turns' and slots' answers (None where the read
    failed, or for `pane` where there is no slot); `failures` maps each read that failed
    (`facts`, `agents`, `pane`) to its error's words. `person_turn_at` is the start of the
    latest turn no delivery opened that the read knew of. `at` is the time of the read.
    """

    holds: tuple[Hold, ...]
    facts: Any | None
    agents: Any | None
    pane: Any | None
    failures: Mapping[str, str] = field(default_factory=dict)
    person_turn_at: float | None = None
    at: float = 0.0

    @property
    def quiet(self) -> bool:
        """Whether nothing holds."""
        return not self.holds

    @property
    def first(self) -> Hold | None:
        """The first thing that holds, in `WORDS` order, or None."""
        return self.holds[0] if self.holds else None

    @property
    def words(self) -> tuple[str, ...]:
        return tuple(hold.word for hold in self.holds)

    @property
    def turn_number(self) -> int | None:
        """The turn number the facts read, or None when they were not read."""
        return getattr(self.facts, "turn_number", None)

    def hold(self, word: str) -> Hold | None:
        return next((h for h in self.holds if h.word == word), None)


def person_turn(turn: Any | None) -> bool:
    """Whether an open turn (`facts().turn`, an `OpenTurn`) is one no delivery opened."""
    return (turn is not None and getattr(turn, "opening", None) is None
            and getattr(turn, "command", None) is None)


def person_started(event: Any) -> bool:
    """Whether a `turn.started` event is a turn no delivery opened: no deliveries and no
    command."""
    return not getattr(event, "deliveries", ()) and getattr(event, "command", None) is None


def live_agents(agents: Any) -> list[Any]:
    """The entries of turns' `Agents` that count as live work."""
    return [a for a in agents.entries if a.kind in LIVE_KINDS and a.final is not True
            and a.state in LIVE_STATES]


# -- reading ----------------------------------------------------------------------------


def read(session: Any, *, slot: str | int | None = None, slots_api: Any | None = None,
         person_s: float = PERSON_S, person_turn_at: float | None = None,
         clock: Callable[[], float] = time.time, count_activity: bool = True,
         keep_warm: bool | None = None) -> Reading:
    """Take the whole quiet read of `session` (turns' `Session`) now.

    `slot` is the launch's slot, whose run pane `pane_use` reads (`slots_api` stands in
    for slots' API; with slots not installed the pane read fails and holds `unreadable`).
    `person_turn_at` is the start of the latest turn no delivery opened that the caller's
    watch saw. With `count_activity` false a client's activity holds nothing. `keep_warm`
    says whether keep-warm is on for the session, for the `subagents` words (None: not
    said). A read that fails holds `unreadable`; it never raises.
    """
    failures: dict[str, str] = {}
    facts = _try(failures, "facts", session.facts)
    agents = _try(failures, "agents", session.agents)
    pane = _pane(failures, slot, slots_api)
    return judge(facts, agents, pane, failures=failures, person_turn_at=person_turn_at,
                 now=clock(), person_s=person_s, count_activity=count_activity,
                 keep_warm=keep_warm)


def reread_person(last: Reading, *, slot: str | int | None = None,
                  slots_api: Any | None = None, person_s: float = PERSON_S,
                  person_turn_at: float | None = None,
                  clock: Callable[[], float] = time.time) -> Reading:
    """`last` with only the person's signs read again: `pane_use` and the person window,
    the two holds that change with no turns event. The facts and agents are `last`'s."""
    failures = {k: v for k, v in last.failures.items() if k != "pane"}
    pane = _pane(failures, slot, slots_api)
    latest = _latest(last.person_turn_at, person_turn_at)
    return judge(last.facts, last.agents, pane, failures=failures, person_turn_at=latest,
                 now=clock(), person_s=person_s)


def judge(facts: Any | None, agents: Any | None, pane: Any | None, *,
          failures: Mapping[str, str] | None = None, person_turn_at: float | None = None,
          now: float, person_s: float = PERSON_S, count_activity: bool = True,
          keep_warm: bool | None = None, stale_after_s: Any = LOOK) -> Reading:
    """What holds, from what was read: the words in `WORDS` order, each with its words for
    a person. With `count_activity` false a client's activity does not hold; copy mode
    still does. `keep_warm` and `stale_after_s` (turns' `STALE_AFTER_S`, looked up when
    not given; None: not known) are for the `subagents` words (`agents_words`)."""
    failures = dict(failures or {})
    holds: list[Hold] = []
    if facts is not None and facts.turn_open:
        if facts.turn is None:
            holds.append(Hold("turn", f"turn {facts.turn_number} is open, not yet tied to "
                                      "what opened it"))
        else:
            holds.append(Hold("turn", f"turn {facts.turn_number} is open"))
    if agents is not None:
        if not agents.complete:
            holds.append(Hold("subagents", "the agent list could not be read whole"))
        elif live := live_agents(agents):
            holds.append(Hold("subagents", agents_words(
                live, now=now, keep_warm=keep_warm, stale_after_s=stale_after_s)))
    if facts is not None and facts.draft:
        holds.append(Hold("draft", "a draft is in the prompt box"))
    if facts is not None and person_turn(facts.turn):
        person_turn_at = _latest(person_turn_at, facts.turn.started_at)
    if person := _person(pane, person_turn_at, now, person_s,
                         count_activity=count_activity):
        holds.append(Hold("person", person))
    if facts is not None and facts.stale:
        failures.setdefault("facts", "the facts moved while they were read (stale)")
    if failures:
        holds.append(Hold("unreadable", "; ".join(f"{k}: {v}" for k, v in failures.items())))
    return Reading(tuple(holds), facts, agents, pane, failures, person_turn_at, now)


def agents_words(live: Sequence[Any], *, now: float, keep_warm: bool | None = None,
                 stale_after_s: Any = LOOK) -> str:
    """The `subagents` words for the live entries `live`: how many, then each by its
    description (else its id), kind and state, with the age of its last write and the
    time it would read stale where turns' judgment read a write; then that a message or
    ending its shells would wake the session, and what `keep_warm` says (None: nothing).
    `stale_after_s` is turns' `STALE_AFTER_S`, looked up when not given; None leaves the
    stale time out."""
    if stale_after_s is LOOK:
        stale_after_s = (turns_stale_after_s()
                         if any(_last_write(a) is not None for a in live) else None)
    named = "; ".join(_entry_words(a, now, stale_after_s) for a in live)
    said = (f"{len(live)} still working: {named}. Messaging the session or ending its "
            "shells would wake it")
    if keep_warm is True:
        said += ", and keep-warm should have kept it warm"
    elif keep_warm is False:
        said += "; keep-warm is off for this session, so nothing kept it warm"
    return said


def _entry_words(entry: Any, now: float, stale_after_s: float | None) -> str:
    name = getattr(entry, "description", None) or getattr(entry, "id", None) or "(unnamed)"
    said = f'"{name}" ({entry.kind}, {entry.state}'
    wrote = _last_write(entry)
    if wrote is not None:
        said += f", last wrote {_span(now - wrote)} ago"
        if stale_after_s is not None:
            said += f", reads stale at {_utc(wrote + float(stale_after_s))}"
    return said + ")"


def _last_write(entry: Any) -> float | None:
    wrote = getattr(entry, "last_write_at", None)
    if isinstance(wrote, bool) or not isinstance(wrote, (int, float)):
        return None
    return float(wrote)


def turns_stale_after_s() -> float | None:
    """turns' `STALE_AFTER_S`, or None when turns can't be loaded or doesn't export it."""
    from . import first

    try:
        found = getattr(first.turns_api(quiet=True), "STALE_AFTER_S", None)
    except Exception:  # noqa: BLE001  (a turns that can't be read leaves the time out)
        return None
    return float(found) if isinstance(found, (int, float)) else None


def _span(seconds: float) -> str:
    """An age in words: seconds under two minutes, minutes under two hours, else hours
    and minutes."""
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def _utc(at: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(at))


def _person(pane: Any | None, turn_at: float | None, now: float, person_s: float, *,
            count_activity: bool = True) -> str | None:
    if pane is not None and getattr(pane, "pane_id", None) is not None:
        if pane.pane_in_mode:
            return "the pane is in copy mode"
        if (count_activity and pane.client_activity is not None
                and now - pane.client_activity < person_s):
            return f"a client was active {_ago(now, pane.client_activity)} ago"
    if turn_at is not None and now - turn_at < person_s:
        return f"a turn no delivery opened, {_ago(now, turn_at)} ago"
    return None


def _ago(now: float, then: float) -> str:
    return f"{max(0, int(now - then))} s"


def _latest(a: float | None, b: float | None) -> float | None:
    return b if a is None else a if b is None else max(a, b)


def _try(failures: dict[str, str], name: str, call: Callable[[], Any]) -> Any | None:
    try:
        return call()
    except Exception as exc:
        failures[name] = _words(exc)
        return None


def _pane(failures: dict[str, str], slot: str | int | None, slots_api: Any | None) -> Any:
    if slot is None:
        return None
    api = slots_api if slots_api is not None else slots.api()
    if api is None:
        failures["pane"] = "slots is not installed"
        return None
    return _try(failures, "pane", lambda: api.pane_use(slot))


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__


# -- watching ---------------------------------------------------------------------------


class Watch:
    """A session watched for the quiet read.

    Opens turns' `watch()` on the session and reads its events on a thread of its own, so
    a caller can re-read the pane between events (turns' iteration blocks with no bound).
    It keeps the latest turn number, the start of the latest turn no delivery opened, and
    `gone` (the reason, once the session or the watch ended). `wait(seen, timeout)` blocks
    until an event past the count `seen` arrives, or the timeout. Raises turns' error when
    the watch can't start. Close it, or use it as a context manager.
    """

    def __init__(self, session: Any, *, clock: Callable[[], float] = time.time):
        self.session = session
        self._clock = clock
        self._watch = session.watch()
        facts = self._watch.facts
        self.facts = facts
        self.turn_number: int | None = getattr(facts, "turn_number", None)
        self.person_turn_at: float | None = (
            facts.turn.started_at if person_turn(getattr(facts, "turn", None)) else None)
        self.gone: str | None = None
        self.events = 0
        self._changed = threading.Condition()
        self._thread = threading.Thread(target=self._run, name="launcher-quiet-watch",
                                        daemon=True)
        self._thread.start()

    def __enter__(self) -> Watch:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _run(self) -> None:
        try:
            for event in self._watch:
                self._take(event)
        except Exception as exc:
            log.info("the quiet read's watch ended: %s", exc)
            with self._changed:
                self.gone = self.gone or f"the watch broke: {_words(exc)}"
                self.events += 1
                self._changed.notify_all()
            return
        with self._changed:
            self.gone = self.gone or "the watch ended"
            self._changed.notify_all()

    def _take(self, event: Any) -> None:
        kind = type(event).__name__
        with self._changed:
            if kind == "TurnStarted":
                self.turn_number = event.turn_number
                if person_started(event):
                    self.person_turn_at = self._clock()
            elif kind == "TurnEnded":
                self.turn_number = event.turn_number
            elif kind == "Gone":
                self.gone = getattr(event, "reason", None) or "gone"
            self.events += 1
            self._changed.notify_all()

    def wait(self, seen: int, timeout: float | None) -> int:
        """Block until more than `seen` events have arrived, the watch ended, or `timeout`
        seconds passed; return the count of events now."""
        with self._changed:
            self._changed.wait_for(lambda: self.events > seen or self.gone is not None,
                                   timeout)
            return self.events

    def read(self, *, slot: str | int | None = None, slots_api: Any | None = None,
             person_s: float = PERSON_S) -> Reading:
        """The whole quiet read now, with the person turn this watch saw."""
        return read(self.session, slot=slot, slots_api=slots_api, person_s=person_s,
                    person_turn_at=self.person_turn_at, clock=self._clock)

    def reread_person(self, last: Reading, *, slot: str | int | None = None,
                      slots_api: Any | None = None, person_s: float = PERSON_S) -> Reading:
        """`last` with the pane and the person window read again (`reread_person`)."""
        return reread_person(last, slot=slot, slots_api=slots_api, person_s=person_s,
                             person_turn_at=self.person_turn_at, clock=self._clock)

    def close(self) -> None:
        self._watch.close()
        self._thread.join(timeout=2)


__all__ = ["LIVE_KINDS", "LIVE_STATES", "LOOK", "PERSON_S", "WORDS", "Hold", "Reading",
           "Watch", "agents_words", "judge", "live_agents", "person_started", "person_turn",
           "read", "reread_person", "turns_stale_after_s"]
