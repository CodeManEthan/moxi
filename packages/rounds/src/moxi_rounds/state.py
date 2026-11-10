"""The fold: a log's events, in order, made into its state.

`fold(events)` is a pure function. It reads nothing but the events it is given, changes
none of them, and gives equal states for equal logs. Each event type has one handler in
`_HANDLERS`, called with the state so far and the event; a type whose handler is `_kept`
changes nothing and is still in the history. A type this code does not know is kept the
same way, so a log with a later version's events still folds.

The state holds what the round's first event set (its description, its flow's name and
the steps recorded for it, its parent, and its status) and the whole history. A log with
no such event, a project's for one, has a status of None.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

#: The status of a round that was opened and has not ended.
OPEN = "open"


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
    "ruling.recorded": _kept,
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

__all__ = ["OPEN", "State", "fold"]
