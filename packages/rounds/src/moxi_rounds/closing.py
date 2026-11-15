"""The close of a round: what it waits for, and the event that closes it.

A round closes itself. No verb closes one: after every write to a round the write path
asks `entry(state)` of the log as it then stands, and appends the event it gives. So
the write that leaves nothing waited for is the one that closes the round, and a close
that stopped short is made by the next write, whatever that write is.

`waits(state)` is the close condition, as what is still waited for. Each function in
`CONDITIONS` is one part of it and gives a line for each thing that part waits for:

- the plan is done: every step of the round's flow is done or skipped, and every stage
  of its plan is finished; a flow with a `build` step needs at least one finished stage;
- nothing is open: no build is open, no question holds the close (`holds.on_close`),
  none open or parked, and no waiting item is open (`waiting_items`);
- every major decision that reached the target is ruled, and every finished build has
  a report that declares its decisions, a build report or a majors report naming its
  stage; unless the operator skipped `ratify` or the project's `ratify` setting is
  `off`.

The `ratify` step is read in the third part and not as a step. Its row and that part
read the same decisions (`decisions.unruled_majors`); the part also waits for each
finished build's report that declares its decisions. The project's setting it reads
is passed in (`ratify`), since nothing here reads the config. A round with a `retro`
step waits for that step like any other; the step is due once the close waits for
nothing else (`waits_before_retro`), and done once a retro report is recorded.

Whether the round closes reads its own log alone: a question parked until another
round closes keeps this one open whether or not that round has closed. How such a
question is named in a line is another matter, since it reads as open once that round
has closed: whoever prints the lines beside its own reading of the questions passes
`on_closed`, the answer it read them with, so one question has one state in what it
prints.

A finished build reads as done in every step of its stage without a read of git, so
the close reads no git.

The closing event holds the round's numbers (`numbers.of`), counted from its log as the
close finds it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from . import builds as built
from . import decisions, findings, flows, folders, holds, questions, report_block, skips
from . import stages, steps, write
from . import numbers as counted
from . import settings as own
from . import waits as waiting_items
from . import state as states

#: The event that closes a round.
CLOSED = "round.closed"
#: The step whose presence in a flow means the round builds.
BUILD = "build"

#: The step that ratifies decisions, which the close reads as its third part.
RATIFY = steps.RATIFY
#: The project's `ratify` setting that puts no decision to the operator.
RATIFY_OFF = steps.RATIFY_OFF
#: The setting read when none is passed.
RATIFY_DEFAULT = "majors"


Closed = Callable[[str], bool]


def _never(_name: str) -> bool:
    # A question parked until another round closes keeps this round open whether or
    # not that round has closed: it then reads as open. So no other log is read.
    return False


def _plan_done(state: states.State, on_closed: Closed, ratify: str, *,
               but: tuple[str, ...] = (),
               seen: Mapping[str | None, steps.Seen] | None = None) -> Iterator[str]:
    """The plan is done: each step of the flow is done or skipped, each stage of the
    plan is finished, and a round that builds has finished at least one. The steps in
    `but` are not read; `seen` is what was read of the open builds from git, as
    `steps.of` takes it."""
    # The setting is passed to the steps as their rows read it, under no stage, unless
    # the caller read the settings there already.
    read = dict(seen or {})
    read.setdefault(None, steps.Seen(ratify=ratify))
    for step in steps.of(state, on_closed=on_closed, but=but, seen=read):
        # The questions step waits for exactly the open questions, and the ratify
        # step for exactly what the third part names; each is named one by one there.
        if step.step not in (steps.QUESTIONS, RATIFY) and not step.settled:
            yield f"step {steps.said(step.data())}"
    finished = questions.finished_stages(state)
    planned = stages.in_plan(state)
    for stage in planned:
        if stage.id not in finished:
            yield f"stage {stage.id}, not finished"
    if builds(state) and not planned:
        yield "a finished stage: the plan holds none"


def _nothing_open(state: states.State, on_closed: Closed, ratify: str) -> Iterator[str]:
    """Nothing is open: no build, no question that holds the close, and no waiting
    item. Which questions hold it is the hold rule's to say; this names each as it
    reads now."""
    for build in built.builds(state):
        if build.ended is None and not decisions.finished(state, build):
            yield f"build of {build.stage}, open"
    for question in holds.on_close(state):
        reads = questions.current(question, state, on_closed=on_closed)
        yield f"question {question.id}, {reads}"
    for wait in waiting_items.open_of(state):
        yield f"waiting item {wait.key}, open"


def _ratified(state: states.State, on_closed: Closed, ratify: str) -> Iterator[str]:
    """Every major decision that reached the target is ruled, and every finished
    build has a report that declares its decisions; or `ratify` is skipped or off."""
    if not flows.has(state.steps, RATIFY) or ratify == RATIFY_OFF:
        return
    if skips.is_skipped(state, RATIFY):
        return
    for build in decisions.finished_builds(state):
        if not _declared(state, build):
            yield (f"a report that declares the decisions of {build.stage}'s build: a "
                   f"build report, or a majors report naming {build.stage}")
        for decision in decisions.of_build(state, build):
            if decision.weight == report_block.MAJOR and decision.state == decisions.OPEN:
                yield f"decision {decision.key}, major and not yet ruled"

def _declared(state: states.State, build: built.Build) -> bool:
    """Whether a report recorded while `build` was its stage's declares its decisions:
    a build report of it, or a majors report whose stages name it."""
    for event in states.with_rereads(state):
        if (event.get("type") != "verdict.recorded"
                or not findings.within(state, build, event.get("n"))):
            continue
        if event.get("kind") == report_block.BUILD and event.get("stage") == build.stage:
            return True
        if (event.get("kind") == report_block.MAJORS
                and build.stage in (event.get("stages") or ())):
            return True
    return False


#: The parts of the close condition, in the order their lines are given. Each takes
#: the round's state, what answers whether another round is closed, and the project's
#: `ratify` setting, and gives a line for each thing it waits for. That answer may
#: change how a line reads, never whether the part gives one.
CONDITIONS: tuple[Callable[[states.State, Closed, str], Iterator[str]], ...] = (
    _plan_done, _nothing_open, _ratified)


def builds(state: states.State) -> bool:
    """Whether the round's flow has a `build` step."""
    return flows.has(state.steps, BUILD)


def waits(state: states.State, *, on_closed: Closed | None = None,
          ratify: str | None = None,
          seen: Mapping[str | None, steps.Seen] | None = None) -> tuple[str, ...]:
    """What the close of the round `state` is of waits for, a line each; empty when
    nothing is, which is when the round closes. It reads the state alone, unless `seen`
    gives what was read of the open builds from git (`checking.seen_of`): a caller that
    shows a round's step with that read gives it here too, so the close line reads each
    step as the step line does. Whether the round closes is decided without it: the
    close waits for every stage to be finished, and a finished stage's steps are done
    on the log.

    `on_closed` answers whether another round, by name, is closed, for how a question
    parked until one is reads in its line; left out, no round reads as closed and
    such a question is named as parked. The lines are as many either way. `ratify`
    is the project's `ratify` setting (`ratify_of`), `majors` when left out."""
    asked = _never if on_closed is None else on_closed
    setting = RATIFY_DEFAULT if ratify is None else ratify
    lines = list(_plan_done(state, asked, setting, seen=seen))
    for condition in CONDITIONS[1:]:
        lines += condition(state, asked, setting)
    return tuple(lines)


def waits_before_retro(state: states.State, *, ratify: str | None = None
                       ) -> tuple[str, ...]:
    """What the close of the round waits for apart from its retro step, a line each:
    the retrospective is due once this is empty. It reads the state alone, as the
    close does, so no other round reads as closed."""
    setting = RATIFY_DEFAULT if ratify is None else ratify
    lines = list(_plan_done(state, _never, setting, but=(steps.RETRO,)))
    for condition in CONDITIONS[1:]:
        lines += condition(state, _never, setting)
    return tuple(lines)


def ratify_of(settings, name: str) -> str:
    """The `ratify` setting of the project the round `name` is of."""
    return str(own.round_setting(settings, folders.split(name)[0], RATIFY))


def is_due(state: states.State, *, ratify: str | None = None) -> bool:
    """Whether the round is to be closed now: it is open and its close waits for
    nothing."""
    return state.status == states.OPEN and not waits(state, ratify=ratify)


def entry(state: states.State, *, ratify: str | None = None,
          at: str | None = None) -> write.Entry | None:
    """The event that closes the round `state` is of, or None while it is not to be
    closed: it has ended, or its close waits for something. `at` is the time the event
    is appended at, which the round's time in its last step runs to."""
    if not is_due(state, ratify=ratify):
        return None
    return write.Entry(CLOSED, {"numbers": counted.of(state, ended_at=at)})


def said(waited: tuple[str, ...] | list[str]) -> str:
    """What a close waits for, as the line `show` gives an open round."""
    if not waited:
        return "close waits for: nothing; the next write to the round closes it"
    return f"close waits for: {'; '.join(waited)}"


__all__ = ["CLOSED", "BUILD", "RATIFY", "RATIFY_OFF", "RATIFY_DEFAULT", "CONDITIONS",
           "builds", "waits", "waits_before_retro", "ratify_of", "is_due", "entry",
           "said"]
