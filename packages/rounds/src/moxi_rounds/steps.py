"""The state of each step of a round: the one place that says whether a step is done.

A round follows the steps its log recorded. `of(state)` gives each of them in order with
its state, `one(state, step, stage)` a single one, and `current(state)` the step the
round is at: the first that is neither done nor skipped. Everything that needs to know
whether a step is done asks here, so what refuses a verb and what explains the refusal
are the same answer, and a skip that lets one of them past lets all of them past.

**The states.** A step is `NOT_DUE` (nothing asks for it yet), `WAITING` (it can't be
done until what its `waits` name is met; each names what is waited for and the command
that meets it), `READY` (it can be done now), `DONE` or `SKIPPED`. A step whose row is
not in `ROWS` reads `NOT_BUILT`: nothing is known of it, and nothing reads it as done.

**The order.** The steps a flow lists, in its order, with the ones it has without
listing them put beside the step each comes with: the questions step after the step that
verifies the design, `commission` before `build`, `check` after it, and `ratify` after
`finish`. A step done once for each build is given once for each stage of the plan, in
the order the stages were declared; with no stage in the plan it is given once, for no
stage.

**The rows.** `ROWS` maps a step to the function that says its state. A row is given the
round's state and an `Asked` (the round's name for the commands it prints, the stage
for a step of one stage, and how to tell whether another round is closed) and returns a
`Step`. A step is added by adding its row; nothing else changes.

- `design`: done once a version is frozen; ready until then.
- `verify-design`: skipped by a skip that stands; done while the design's standing at
  its current version is one that counts as verified. It waits for a frozen version,
  for that version to pass the check of its trace rows where the check was asked for,
  and for whatever must happen before a pass can read the design. `Step.asked` says
  which pass is asked for.
- the questions step: ready while a question of the round reads as open, with the
  questions in `Step.questions`; done when none does. Asked of one stage, it reads the
  questions that hold that stage: skipped where a skip for the stage stands and nothing
  holds it.

**A hold.** `held_by(state, stage)` gives what the questions that hold a stage make it
wait for. A stage's `commission` and `finish` carry these as their `waits` while their
rows are not built, so a round whose flow has no questions step still shows the hold, on
the stage it stops.

Nothing here reads a file, the config or git: a round's state is the whole input.
Whether `design.md` still has the bytes that were frozen is not a step's state; a verb
that needs the design as it was verified asks `versions.unchanged` beside this.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

from . import brake, flows, holds, questions, skips, stages, tracing, versions
from . import state as states
from .errors import Unmet

NOT_DUE = "not-due"
WAITING = "waiting"
READY = "ready"
DONE = "done"
SKIPPED = "skipped"
#: The five states a step has.
STATES = (NOT_DUE, WAITING, READY, DONE, SKIPPED)
#: What a step reads as while no row says its state. It is none of the five, and is
#: never read as done.
NOT_BUILT = "not-built"
#: The states that let a round past a step.
SETTLED = (DONE, SKIPPED)

DESIGN = "design"
VERIFIES = "verify-design"
QUESTIONS = skips.QUESTIONS
#: The steps of a stage that a question holding the stage stops.
HELD = ("commission", "finish")

#: Where a step a flow has without listing it goes: `(the step, before or after, the
#: step it comes with)`.
_BEFORE, _AFTER = "before", "after"
_UNLISTED = ((QUESTIONS, _AFTER, VERIFIES), ("commission", _BEFORE, "build"),
             ("check", _AFTER, "build"), ("ratify", _AFTER, "finish"))


@dataclass(frozen=True)
class Asked:
    """What a row is asked about: `name`, the round's name, for the commands it
    prints; `stage`, for a step of one stage; `closed`, which answers whether a round,
    by name, is closed, for a question parked until one is (`questions.is_closed` when
    None)."""

    name: str = "ROUND"
    stage: str | None = None
    closed: Callable[[str], bool] | None = None


@dataclass(frozen=True)
class Step:
    """One step of a round and its state.

    - `step`: its name; `stage`: the stage, for a step of one stage.
    - `state`: one of `STATES`, or `NOT_BUILT`.
    - `waits`: for a step that waits, each thing waited for with the command that
      meets it. A step of a stage whose row is not built carries the holds on its
      stage here, whatever else it would wait for.
    - `questions`: for the questions step, the ids of the questions that make it due.
    - `asked`: for the step that verifies the design, which pass is asked for.
    - `entry`: the step's entry in the flow, which says who does it, at which tier
      and what they read; None for a step the flow has without listing it.
    """

    step: str
    state: str
    stage: str | None = None
    waits: tuple[Unmet, ...] = ()
    questions: tuple[str, ...] = ()
    asked: brake.Asked | None = None
    entry: Mapping | None = None

    @property
    def settled(self) -> bool:
        """Whether the round is past this step: it is done or skipped."""
        return self.state in SETTLED

    def data(self) -> dict:
        """The step as data: its name, stage and state, and what it waits for."""
        return {"step": self.step, "stage": self.stage, "state": self.state,
                "waits": [{"reason": one.reason, "fix": one.fix} for one in self.waits],
                "questions": list(self.questions)}


Row = Callable[[states.State, Asked], Step]


# -- the rows ----------------------------------------------------------------------------

def _design(state: states.State, about: Asked) -> Step:
    return Step(DESIGN, READY if versions.current(state) is None else DONE)


def _verify_design(state: states.State, about: Asked) -> Step:
    if skips.is_skipped(state, VERIFIES):
        return Step(VERIFIES, SKIPPED)
    asked = brake.asked(state, name=about.name)
    if versions.standing(state).word in versions.VERIFIED:
        return Step(VERIFIES, DONE, asked=asked)
    fold = f"moxi rounds fold {about.name}"
    version = versions.current(state)
    if version is None:
        return Step(VERIFIES, WAITING, asked=asked, waits=(
            Unmet("no version of the design is frozen", fold),))
    waits = []
    if not tracing.passes(state):
        found = tracing.recorded(version)
        said = tracing.lines({**found.stored(), "version": version.n, "stale_now": ()})
        waits.append(Unmet(f"{said[0]}; a version whose rows pass is waited for",
                           f"give {versions.DESIGN_FILE} the rows, then {fold}"))
    if asked is not None and asked.instead:
        # No pass can read the design until this is done.
        waits.append(Unmet(asked.why, "; ".join(asked.instead)))
    return Step(VERIFIES, WAITING if waits else READY, waits=tuple(waits), asked=asked)


def _docket(state: states.State, about: Asked) -> Step:
    if about.stage is not None:
        holding = holds.on_stage(state, about.stage, closed=about.closed)
        if holding:
            return Step(QUESTIONS, READY, stage=about.stage,
                        questions=tuple(one.id for one in holding))
        skipped = skips.is_skipped(state, QUESTIONS, about.stage)
        return Step(QUESTIONS, SKIPPED if skipped else DONE, stage=about.stage)
    asked = tuple(
        one.id for one in state.questions
        if questions.current(one, state, closed=about.closed) == questions.OPEN)
    return Step(QUESTIONS, READY if asked else DONE, questions=asked)


#: The function that says each step's state. A step with no row reads `NOT_BUILT`.
ROWS: dict[str, Row] = {DESIGN: _design, VERIFIES: _verify_design, QUESTIONS: _docket}


# -- a hold on a stage -------------------------------------------------------------------

def held_by(state: states.State, stage: str, *, name: str = "ROUND",
            closed: Callable[[str], bool] | None = None) -> tuple[Unmet, ...]:
    """What the questions that hold the stage `stage` make it wait for, in the order
    they were asked: each names the question and the command that records the
    operator's answer. Empty when nothing holds the stage."""
    return tuple(
        Unmet(f"{one.id} holds {stage}: {one.text}",
              f"moxi rounds rule {name} {one.id} --record PTR")
        for one in holds.on_stage(state, stage, closed=closed))


# -- the steps of a round ----------------------------------------------------------------

def order(steps) -> tuple[tuple[str, Mapping | None], ...]:
    """The steps a flow whose entries are `steps` has, in order, each with its entry:
    the listed ones as listed, and the ones it has without listing them beside the
    step each comes with."""
    found: list[tuple[str, Mapping | None]] = [
        (str(entry.get("step")), entry) for entry in steps]
    for step, side, beside in _UNLISTED:
        names = [name for name, _entry in found]
        if step in names or beside not in names or not flows.has(steps, step):
            continue
        found.insert(names.index(beside) + (side == _AFTER), (step, None))
    return tuple(found)


def _is_of_a_stage(step: str) -> bool:
    # A step of the user's own is done for each build, like the stage steps.
    return flows.STEPS.get(step, flows.STAGE) == flows.STAGE


def _read(state: states.State, step: str, entry: Mapping | None, about: Asked) -> Step:
    row = ROWS.get(step)
    if row is not None:
        return dataclasses.replace(row(state, about), entry=entry)
    waits = ()
    if step in HELD and about.stage is not None:
        waits = held_by(state, about.stage, name=about.name, closed=about.closed)
    return Step(step, NOT_BUILT, stage=about.stage, waits=waits, entry=entry)


def _each(state: states.State, name: str,
          closed: Callable[[str], bool] | None) -> Iterator[Step]:
    planned = [stage.id for stage in stages.in_plan(state)]
    for step, entry in order(state.steps):
        of_stages = planned if _is_of_a_stage(step) and planned else [None]
        for stage in of_stages:
            yield _read(state, step, entry, Asked(name, stage, closed))


def of(state: states.State, *, name: str = "ROUND",
       closed: Callable[[str], bool] | None = None) -> tuple[Step, ...]:
    """Every step of the round `state` is of, in order, each with its state. `name`
    is the round's name, for the commands a wait prints, and `closed` answers whether
    a round, by name, is closed (`questions.is_closed` when left out)."""
    return tuple(_each(state, name, closed))


def one(state: states.State, step: str, stage: str | None = None, *,
        name: str = "ROUND",
        closed: Callable[[str], bool] | None = None) -> Step | None:
    """The step `step` of the round, for the stage `stage` when it is a step of one
    stage; None when the round's flow has no such step. The questions step asked of
    a stage reads the questions that hold that stage."""
    for found, entry in order(state.steps):
        if found == step:
            return _read(state, step, entry, Asked(name, stage, closed))
    return None


def is_settled(state: states.State, step: str, stage: str | None = None, *,
               closed: Callable[[str], bool] | None = None) -> bool:
    """Whether nothing waits on the step any longer: it is done or skipped, or the
    round's flow has no such step."""
    found = one(state, step, stage, closed=closed)
    return found is None or found.settled


def current(state: states.State, *, name: str = "ROUND",
            closed: Callable[[str], bool] | None = None) -> Step | None:
    """The step the round is at: the first, in order, that is neither done nor
    skipped. None when every step is, and for a round that has ended."""
    if state.status != states.OPEN:
        return None
    return next((step for step in _each(state, name, closed) if not step.settled), None)


# -- what the index keeps ----------------------------------------------------------------

def _never(_name: str) -> bool:
    return False


def at(state: states.State):
    """The step the round is at, as one `(id, body)` for the index: the step, its
    stage and its state, or nothing for a round at no step.

    Only the round's own log is read, so a question parked until another round
    closes is read as parked. The body then names those rounds in `until`: once one
    of them is closed, whoever lists the row reads the step from the log again."""
    found = current(state, closed=_never)
    until = sorted({one.until[len(questions.UNTIL_ROUND):] for one in state.questions
                    if one.state == questions.PARKED
                    and (one.until or "").startswith(questions.UNTIL_ROUND)})
    if found is None and not (until and state.status == states.OPEN):
        return
    body = {"step": None, "stage": None, "state": None}
    if found is not None:
        body = {"step": found.step, "stage": found.stage, "state": found.state}
    if until:
        body["until"] = until
    yield "step", body


def said(step: Mapping | None) -> str:
    """A step as data, in words: `verify-design, waiting` or `commission of B2,
    not-built`; `none` for a round at no step."""
    if step is None or step.get("step") is None:
        return "none"
    stage = "" if step.get("stage") is None else f" of {step['stage']}"
    return f"{step['step']}{stage}, {step['state']}"


__all__ = ["NOT_DUE", "WAITING", "READY", "DONE", "SKIPPED", "STATES", "NOT_BUILT",
           "SETTLED", "HELD", "Asked", "Step", "Row", "ROWS", "held_by", "order", "of",
           "one", "is_settled", "current", "at", "said"]
