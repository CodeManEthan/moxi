"""The close of a round: what it waits for, and the event that closes it.

A round closes itself. No verb closes one: after every write to a round the write path
asks `entry(state)` of the log as it then stands, and appends the event it gives. So
the write that leaves nothing waited for is the one that closes the round, and a close
that stopped short is made by the next write, whatever that write is.

`waits(state)` is the close condition, as what is still waited for. Each function in
`CONDITIONS` is one part of it and gives a line for each thing that part waits for:

- every step of the round's flow is done or skipped, and every stage of its plan is
  finished;
- no question is open or parked.

A round whose flow has a `build` step also waits for a finished stage. This version
finishes none, so such a round does not close in it, and `waits` says only that. It
ends by being dropped.

The closing event holds nothing yet: `entry` is where what a round is closed with is
added.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from . import flows, questions, stages, steps, write
from . import state as states

#: The event that closes a round.
CLOSED = "round.closed"
#: The step whose presence in a flow means the round builds.
BUILD = "build"

#: What a round whose flow has a `build` step waits for, in this version.
NOT_IN_THIS_VERSION = ("a finished stage, which this version does not build, so the "
                       "round does not close in it")


def _never(_name: str) -> bool:
    # A question parked until another round closes keeps this round open whether or
    # not that round has closed: it then reads as open. So no other log is read.
    return False


def _plan_done(state: states.State) -> Iterator[str]:
    """The plan is done: each step of the flow is done or skipped, and each stage of
    the plan is finished."""
    for step in steps.of(state, closed=_never):
        # The questions step waits for exactly the open questions, which are named
        # one by one below.
        if step.step != steps.QUESTIONS and not step.settled:
            yield f"step {steps.said(step.data())}"
    finished = questions.finished_stages(state)
    for stage in stages.in_plan(state):
        if stage.id not in finished:
            yield f"stage {stage.id}, not finished"


def _nothing_open(state: states.State) -> Iterator[str]:
    """Nothing is open: no question is open or parked."""
    for question in state.questions:
        reads = questions.current(question, state, closed=_never)
        if reads in (questions.OPEN, questions.PARKED):
            yield f"question {question.id}, {reads}"


#: The parts of the close condition, in the order their lines are given. Each takes
#: the round's state and gives a line for each thing it waits for.
CONDITIONS: tuple[Callable[[states.State], Iterator[str]], ...] = (
    _plan_done, _nothing_open)


def builds(state: states.State) -> bool:
    """Whether the round's flow has a `build` step."""
    return flows.has(state.steps, BUILD)


def waits(state: states.State) -> tuple[str, ...]:
    """What the close of the round `state` is of waits for, a line each; empty when
    nothing is, which is when the round closes. It reads the state alone."""
    if builds(state):
        return (NOT_IN_THIS_VERSION,)
    return tuple(line for condition in CONDITIONS for line in condition(state))


def is_due(state: states.State) -> bool:
    """Whether the round is to be closed now: it is open and its close waits for
    nothing."""
    return state.status == states.OPEN and not waits(state)


def entry(state: states.State) -> write.Entry | None:
    """The event that closes the round `state` is of, or None while it is not to be
    closed: it has ended, or its close waits for something."""
    return write.Entry(CLOSED, {}) if is_due(state) else None


def said(waited: tuple[str, ...] | list[str]) -> str:
    """What a close waits for, as the line `show` gives an open round."""
    if not waited:
        return "close waits for: nothing; the next write to the round closes it"
    return f"close waits for: {'; '.join(waited)}"


__all__ = ["CLOSED", "BUILD", "NOT_IN_THIS_VERSION", "CONDITIONS", "builds", "waits",
           "is_due", "entry", "said"]
