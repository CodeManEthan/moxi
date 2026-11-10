"""The hold rule: which questions hold a stage of a round, and which hold its close.

A question holds stages, never builds. `on_stage(state, "B2")` gives the questions that
hold the stage `B2` of the round whose log folded to `state`, and `on_close(state)` the
ones that hold the round's close. Both are pure reads of that state. Every gate that a
question can stop asks one of these two and reads no question's `blocks` itself, so the
rule is in one place: a stage may be commissioned or finished, or a child round opened
or closed, only while `on_stage` gives nothing for it, and a round may close only while
`on_close` gives nothing.

**A stage.** A question holds a stage when all of these are so:

- it reads as open now. A parked question holds nothing, until what it waits for has
  come: from then on it reads as open, and holds what it held. A ruled or a withdrawn
  question holds nothing.
- it holds up every stage, or names this one. One that holds up every stage holds each
  stage the round has and each it gains later. One that holds up none holds no stage.
- no skip of the questions step for this stage lists it. Such a skip lets the stage
  past the questions it lists, the ones that held the stage when it was recorded, and
  no other: a question asked afterwards holds the stage like any other.

A stage that is finished, or that left the plan, has nothing left to stop: no question
holds it.

**The close.** Every question the log records as open or parked holds the round's
close, whatever it holds up: one that holds up no stage, and a parked one, included.

What is read: `State.questions` (each one's `blocks`, `state` and `until`, the last two
through `questions.current`), `State.rulings` (a skip's `rules` and `covers`), and the
stages the log finished or took out of the plan, through `questions.finished_stages`
and `questions.left_stages`.
"""

from __future__ import annotations

from collections.abc import Callable

from . import questions
from . import state as states

#: What starts the item of a ruling that skips the questions step for one stage; the
#: stage's id follows.
SKIP = "skip:docket:"


def on_stage(state: states.State, stage: str, *,
             closed: Callable[[str], bool] | None = None
             ) -> tuple[states.Question, ...]:
    """The questions that hold the stage `stage` of the round `state` is of, in the
    order they were asked; none when nothing holds it.

    - `state`: the round's state, as its log folds.
    - `stage`: the stage's id, `B2`.
    - `closed`: answers whether a round, by name, is closed, for a question parked
      until another round closes; `questions.is_closed`, which reads that round's
      log, when left out.
    """
    if stage in questions.finished_stages(state) or stage in questions.left_stages(state):
        return ()
    let_past = skipped(state, stage)
    return tuple(
        question for question in state.questions
        if question.id not in let_past
        and (question.blocks == questions.ALL
             or (not isinstance(question.blocks, str) and stage in (question.blocks or ())))
        and questions.current(question, state, closed=closed) == questions.OPEN)


def on_close(state: states.State) -> tuple[states.Question, ...]:
    """The questions that hold the close of the round `state` is of, in the order they
    were asked: each one recorded as open or parked."""
    return tuple(question for question in state.questions
                 if question.state in (questions.OPEN, questions.PARKED))


def skipped(state: states.State, stage: str) -> frozenset[str]:
    """The ids of the questions that the skips of the questions step for `stage` let
    it past: what each such skip lists. This is the one place that says which skips
    count."""
    return frozenset(question_id for ruling in state.rulings
                     if ruling.rules == SKIP + stage for question_id in ruling.covers)


__all__ = ["SKIP", "on_stage", "on_close", "skipped"]
