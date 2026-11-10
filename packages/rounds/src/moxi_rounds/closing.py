"""The close of a round: what it waits for, and the event that closes it.

A round closes itself. No verb closes one: after every write to a round the write path
asks `entry(state)` of the log as it then stands, and appends the event it gives. So
the write that leaves nothing waited for is the one that closes the round, and a close
that stopped short is made by the next write, whatever that write is.

`waits(state)` is the close condition, as what is still waited for. Each function in
`CONDITIONS` is one part of it and gives a line for each thing that part waits for:

- every step of the round's flow is done or skipped, and every stage of its plan is
  finished;
- no question holds the close (`holds.on_close`): none is open or parked.

Whether the round closes reads its own log alone: a question parked until another
round closes keeps this one open whether or not that round has closed. How such a
question is named in a line is another matter, since it reads as open once that round
has closed: whoever prints the lines beside its own reading of the questions passes
`closed`, the answer it read them with, so one question has one state in what it
prints.

A round whose flow has a `build` step also waits for a finished stage. This version
finishes none, so such a round does not close in it, and `waits` says only that. It
ends by being dropped.

The closing event holds nothing yet: `entry` is where what a round is closed with is
added.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from . import flows, holds, questions, stages, steps, write
from . import state as states

#: The event that closes a round.
CLOSED = "round.closed"
#: The step whose presence in a flow means the round builds.
BUILD = "build"

#: What a round whose flow has a `build` step waits for, in this version.
NOT_IN_THIS_VERSION = ("a finished stage, which this version does not build, so the "
                       "round does not close in it")


Closed = Callable[[str], bool]


def _never(_name: str) -> bool:
    # A question parked until another round closes keeps this round open whether or
    # not that round has closed: it then reads as open. So no other log is read.
    return False


def _plan_done(state: states.State, closed: Closed) -> Iterator[str]:
    """The plan is done: each step of the flow is done or skipped, and each stage of
    the plan is finished."""
    for step in steps.of(state, closed=closed):
        # The questions step waits for exactly the open questions, which are named
        # one by one below.
        if step.step != steps.QUESTIONS and not step.settled:
            yield f"step {steps.said(step.data())}"
    finished = questions.finished_stages(state)
    for stage in stages.in_plan(state):
        if stage.id not in finished:
            yield f"stage {stage.id}, not finished"


def _nothing_open(state: states.State, closed: Closed) -> Iterator[str]:
    """Nothing is open: no question holds the close. Which ones do is the hold
    rule's to say; this names each as it reads now."""
    for question in holds.on_close(state):
        reads = questions.current(question, state, closed=closed)
        yield f"question {question.id}, {reads}"


#: The parts of the close condition, in the order their lines are given. Each takes
#: the round's state and what answers whether another round is closed, and gives a
#: line for each thing it waits for. That answer may change how a line reads, never
#: whether the part gives one.
CONDITIONS: tuple[Callable[[states.State, Closed], Iterator[str]], ...] = (
    _plan_done, _nothing_open)


def builds(state: states.State) -> bool:
    """Whether the round's flow has a `build` step."""
    return flows.has(state.steps, BUILD)


def waits(state: states.State, *, closed: Closed | None = None) -> tuple[str, ...]:
    """What the close of the round `state` is of waits for, a line each; empty when
    nothing is, which is when the round closes. It reads the state alone.

    `closed` answers whether another round, by name, is closed, for how a question
    parked until one is reads in its line; left out, no round reads as closed and
    such a question is named as parked. The lines are as many either way."""
    if builds(state):
        return (NOT_IN_THIS_VERSION,)
    asked = _never if closed is None else closed
    return tuple(line for condition in CONDITIONS for line in condition(state, asked))


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
