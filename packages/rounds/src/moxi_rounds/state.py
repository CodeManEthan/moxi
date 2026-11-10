"""The fold: a log's events, in order, made into its state.

`fold(events)` is a pure function. It reads nothing but the events it is given, changes
none of them, and gives equal states for equal logs. Each event type has one handler in
`_HANDLERS`, called with the state so far and the event; a type whose handler is `_kept`
changes nothing and is still in the history. A type this code does not know is kept the
same way, so a log with a later version's events still folds.

The state holds what the round's first event set (its description, its flow's name and
the steps recorded for it, its parent, and its status), the rulings recorded in the log,
and the whole history. A log with no opening event, a project's for one, has a status of
None; the rulings of a project's log are its register.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

#: The status of a round that was opened and has not ended.
OPEN = "open"


@dataclass(frozen=True)
class Ruling:
    """One ruling as its event recorded it."""

    #: `C<n>` in a round's log, `S<n>` in a project's.
    id: str | None
    #: The sentence, as given.
    text: str | None
    #: The pointer to where the operator said it.
    record: str | None
    #: Why, when a reason was given.
    reason: str | None
    #: Whether it says what is built or changes a record of the process.
    kind: str | None
    #: The item it rules, or None for a ruling that stands on its own.
    rules: str | None
    #: The stages of the round it applies to: `"all"`, or their ids. None in a
    #: project's log, which has no stages.
    stages: str | tuple[str, ...] | None
    #: The repos and paths a register entry applies to, each `{"repo": key}` with a
    #: `"path"` glob or without; empty when it names none.
    scope: tuple[dict, ...]
    #: Who recorded it, and when.
    actor: str
    at: str


@dataclass
class State:
    """What a log says, as of its last event."""

    #: What the round is for.
    description: str | None = None
    #: The name of the flow the round follows.
    flow: str | None = None
    #: That flow's step entries as the log recorded them, which the round follows
    #: whatever the config says since.
    steps: list[dict] = field(default_factory=list)
    #: The round this one is a child of, or None.
    parent: str | None = None
    #: `OPEN`, or None for a log that opened no round.
    status: str | None = None
    #: The rulings the log recorded, oldest first. A project's are its register.
    rulings: list[Ruling] = field(default_factory=list)
    #: Every event, oldest first.
    history: list[dict] = field(default_factory=list)


def fold(events: Iterable[dict]) -> State:
    """The state the events of one log give, read oldest first."""
    state = State()
    for event in events:
        _HANDLERS.get(event["type"], _kept)(state, event)
        state.history.append(event)
    return state


def _kept(state: State, event: dict) -> None:
    """The event changes no field; it is in the history only."""


def _round_opened(state: State, event: dict) -> None:
    state.description = event["description"]
    state.flow = event["flow"]
    # A copy, so a later change to the state's steps never reaches the event.
    state.steps = copy.deepcopy(event["steps"])
    state.parent = event.get("parent")
    state.status = OPEN


def _ruling_recorded(state: State, event: dict) -> None:
    stages = event.get("stages")
    state.rulings.append(Ruling(
        id=event.get("id"), text=event.get("text"), record=event.get("record"),
        reason=event.get("reason"), kind=event.get("kind"), rules=event.get("rules"),
        stages=tuple(stages) if isinstance(stages, list) else stages,
        # A copy, so the state's scope and the event's never share a dict.
        scope=tuple(copy.deepcopy(event.get("scope") or [])),
        actor=event["actor"], at=event["at"]))


#: The handler of each event type. Every type in `events.TYPES` has a row.
_HANDLERS: dict[str, Callable[[State, dict], None]] = {
    "round.opened": _round_opened,
    "flow.changed": _kept,
    "design.frozen": _kept,
    "verdict.recorded": _kept,
    "question.asked": _kept,
    "question.changed": _kept,
    "stage.declared": _kept,
    "stage.changed": _kept,
    "ruling.recorded": _ruling_recorded,
    "ruling.shown": _kept,
    "build.commissioned": _kept,
    "check.ran": _kept,
    "finding.marked": _kept,
    "wait.marked": _kept,
    "brief.sized": _kept,
    "finish.action": _kept,
    "finish.done": _kept,
    "lock.cleared": _kept,
    "build.dropped": _kept,
    "round.dropped": _kept,
    "thread.decided": _kept,
    "repair": _kept,
    "round.closed": _kept,
}

__all__ = ["OPEN", "Ruling", "State", "fold"]
