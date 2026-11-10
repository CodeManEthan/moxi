"""Stages: what a round's plan holds, and how a stage changes before it is built.

A stage is a piece of the round's work that is built and landed on its own: a build in
one repo on one branch, or a child round in place of one. Its record is in the log and
does not depend on the flow the round follows, so a change of flow keeps it.

**What never changes.** A stage's repo, branch and child round are fixed when it is
declared. A design that gives a recorded id another of them is refused: different work
is a new id.

**Leaving the plan.** A stage leaves only while it has no build that is open or
finished, and in one of two ways: the operator rules it out (`unstage:B<n>`), or a
design verdict applies a removal the design's block made (the verdict's own event
says so; `cannot_leave` is the reason a stage stays). A stage that left does not come back: its id
is refused from then on, and the same work planned again takes a new id. No ruling
takes an `unstage` back.

**What a stage waits for.** `after` lists the stages finished before it starts. Anyone
adds to the list (`wait`), and a stage added must be a recorded one and must not wait,
directly or through others, for the stage it is added to. Only the operator takes one
off (`unafter:B<n>=B<m>`), or a design verdict that applies what a block left out,
in its own event. `waits_for` gives the ones not finished yet.

**One stage to a repo** in a round whose flow does not verify its design: a design
that would add a second stage in a repo is refused there, since the second could never
be commissioned. The way on is a flow that verifies the design.

`CHECKS` are the three refusals a freezing makes of a design's block.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import design_block, ending, flows, folders, questions, write
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound, Problem, Unmet

#: What starts the item of the ruling that takes a stage out of the plan; the stage's
#: id follows.
UNSTAGE = questions.UNSTAGE
#: What starts the item of the ruling that takes one stage off what another waits for:
#: the stage, `=`, and the stage it waits for no longer.
UNAFTER = questions.UNAFTER

#: A stage's states: in the plan, and out of it for good.
PLANNED = "planned"
LEFT = "left"

#: What a stage's build is, when it has one that counts.
BUILDING = "open"
FINISHED = "finished"

#: The step a flow has when it verifies its design. A flow without it holds a round to
#: one stage in each repo.
VERIFIES = "verify-design"

_ID = questions.STAGE


@dataclass(frozen=True)
class Waiting:
    """A stage after a mark that made it wait for more: the `name` of its round, the
    `stage` as the log now has it, the stages `added`, and the `event` written.
    `failed` pairs each after-append callback that raised with what it raised."""

    name: str
    stage: states.Stage
    added: tuple[str, ...]
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


# -- what the log says of a stage --------------------------------------------------------

def find(state: states.State, stage_id: str) -> states.Stage | None:
    """The stage of `state` with that id, or None."""
    for stage in state.stages:
        if stage.id == stage_id:
            return stage
    return None


def state_of(state: states.State, stage_id: str) -> str | None:
    """`PLANNED` for a stage in the plan, `LEFT` for one that left it, None for an id
    the round never declared."""
    if find(state, stage_id) is None:
        return None
    return LEFT if stage_id in questions.left_stages(state) else PLANNED


def in_plan(state: states.State) -> tuple[states.Stage, ...]:
    """The stages of the round's plan, in the order declared: the ones that did not
    leave it."""
    out = questions.left_stages(state)
    return tuple(stage for stage in state.stages if stage.id not in out)


def build_of(state: states.State, stage_id: str) -> str | None:
    """What the build of the stage is: `BUILDING` while one was commissioned and
    neither finished nor dropped, `FINISHED` once its finish is done, None for a stage
    with no build that counts. A dropped build counts for nothing."""
    found = None
    for event in state.history:
        if event.get("stage") != stage_id:
            continue
        if event["type"] == "build.commissioned":
            found = BUILDING
        elif event["type"] == "build.dropped":
            found = None
        elif event["type"] == "finish.done":
            found = FINISHED
    return found


def waits_for(state: states.State, stage_id: str) -> tuple[str, ...]:
    """The stages the stage `stage_id` waits for that are not finished, in the order
    its record lists them; `()` when nothing keeps it, and for an id the round never
    declared."""
    stage = find(state, stage_id)
    if stage is None:
        return ()
    finished = questions.finished_stages(state)
    return tuple(one for one in stage.after if one not in finished)


def cannot_leave(state: states.State, stage_id: str, *,
                 name: str = "the round") -> str | None:
    """Why the stage `stage_id` can't leave the plan now, or None when it can: it must
    be a stage of the plan with no build that is open or finished. `name` is what the
    round is called in the reason."""
    if find(state, stage_id) is None:
        return f"{stage_id} is not a stage of {name}: {questions.stages_said(state)}"
    if stage_id in questions.left_stages(state):
        return f"{stage_id} left the plan already, and a stage leaves once"
    built = build_of(state, stage_id)
    if built == FINISHED or stage_id in questions.finished_stages(state):
        return (f"{stage_id} is finished: its work is met, and a finished stage has "
                "nothing to leave")
    if built == BUILDING:
        return (f"{stage_id} has an open build, and a stage with one leaves only "
                "after that build is dropped")
    return None


# -- what a design's block may not say ---------------------------------------------------

def _built_on(stage) -> str:
    """What a stage is of, in words."""
    if stage.round is not None:
        return f"the round {stage.round}"
    return f"{stage.repo} on {stage.branch}"


def keeps_what_it_is_of(state: states.State,
                        block: design_block.DesignBlock) -> list[Problem]:
    """A recorded stage's repo, branch and child round never change: a block that
    gives its id another of them is refused."""
    left = questions.left_stages(state)
    problems = []
    for stage in block.stages:
        have = find(state, stage.id)
        if have is None or stage.id in left:
            continue
        for key in ("repo", "branch", "round"):
            if getattr(have, key) != getattr(stage, key):
                problems.append(Problem(
                    stage.line_of(key),
                    f"{stage.id} is recorded as {_built_on(have)}, and the block "
                    f"declares it as {_built_on(stage)}: a stage's {key} never "
                    "changes, and different work is a new id",
                    f"declare {stage.id} as it is recorded, and the other work as a "
                    "[[stage]] under an id the round has not used"))
                break
    return problems


def one_to_a_repo(state: states.State, block: design_block.DesignBlock) -> list[Problem]:
    """In a round whose flow does not verify its design, a block that would add a
    second stage in one repo is refused: only one build is commissioned in a repo
    there, so the second stage could never be built."""
    if flows.has(state.steps, VERIFIES):
        return []
    taken = {stage.repo: stage.id for stage in in_plan(state)
             if stage.repo is not None}
    recorded = {stage.id for stage in state.stages}
    problems = []
    for stage in block.stages:
        if stage.repo is None or stage.id in recorded:
            continue
        first = taken.setdefault(stage.repo, stage.id)
        if first != stage.id:
            problems.append(Problem(
                stage.line_of("repo"),
                f"{stage.id} would be a second stage in {stage.repo}, which has "
                f"{first}: the flow {state.flow} does not verify its design, and such "
                "a round builds one stage in a repo",
                f"keep one stage for {stage.repo}, or move the round up to a flow "
                f"that has a {VERIFIES} step (moxi rounds mark ROUND --flow NAME) "
                "and freeze the design then"))
    return problems


def stays_out(state: states.State, block: design_block.DesignBlock) -> list[Problem]:
    """A stage that left the plan does not come back: a block that declares its id is
    refused."""
    left = questions.left_stages(state)
    return [Problem(
        stage.line_of("id"),
        f"{stage.id} left the plan of the round, and a stage that left does not come "
        "back: the same work planned again is a new stage",
        f"give the stage an id the round has not used, or take {stage.id} out of "
        "the block")
        for stage in block.stages if stage.id in left]


#: What a freezing refuses of the stages a design's block declares, each
#: `check(state, block) -> problems`.
CHECKS: tuple[Callable[[states.State, design_block.DesignBlock], list[Problem]], ...] = (
    stays_out, keeps_what_it_is_of, one_to_a_repo)


# -- the operator's two rulings ----------------------------------------------------------

def parse_unstage(item: str) -> str | None:
    """The stage the item `item` takes out of the plan; None for an item that is no
    such ruling. Raises `BadArgument` for one that names no stage."""
    if not item.startswith(UNSTAGE):
        return None
    stage_id = item[len(UNSTAGE):]
    if not _ID.fullmatch(stage_id):
        raise BadArgument(f"{item!r} does not name a stage",
                          f"say {UNSTAGE}B<n>, the stage that leaves the plan")
    return stage_id


def parse_unafter(item: str) -> tuple[str, str] | None:
    """The stage the item `item` is of and the stage it waits for no longer; None for
    an item that is no such ruling. Raises `BadArgument` for one that can't be read."""
    if not item.startswith(UNAFTER):
        return None
    stage_id, _equals, waited = item[len(UNAFTER):].partition("=")
    if not _ID.fullmatch(stage_id) or not _ID.fullmatch(waited):
        raise BadArgument(f"{item!r} does not say which wait is taken off",
                          f"say {UNAFTER}B<n>=B<m>: the stage, and the one stage it "
                          "waits for no longer")
    return stage_id, waited


def unstage_gates(stage_id: str, *, name: str) -> list[write.Gate]:
    """The gate of the ruling that takes `stage_id` out of the plan of the round
    `name`: it is a stage of the plan, with no build that is open or finished."""
    def can_leave(view):
        why = cannot_leave(view.state, stage_id, name=name)
        return None if why is None else Unmet(why, f"moxi rounds show {name}")

    return [can_leave]


def unafter_gates(stage_id: str, waited: str, *, name: str) -> list[write.Gate]:
    """The gate of the ruling that takes `waited` off what `stage_id` waits for in
    the round `name`: the stage is recorded and waits for it now."""
    def waits_for_it(view):
        stage = find(view.state, stage_id)
        if stage is None:
            return Unmet(f"{stage_id} is not a stage of {name}: "
                         f"{questions.stages_said(view.state)}",
                         f"moxi rounds show {name}")
        if waited in stage.after:
            return None
        has = (f"it waits for {', '.join(stage.after)}" if stage.after
               else "it waits for no stage")
        return Unmet(f"{stage_id} does not wait for {waited}: {has}",
                     f"moxi rounds show {name}")

    return [waits_for_it]


# -- the mark anyone may make ------------------------------------------------------------

def parse_after(text: str) -> tuple[str, ...]:
    """The stage ids of `B1,B3`, in the order given. Raises `BadArgument` for an empty
    list, an id that is not `B<n>`, or one named twice."""
    ids = tuple(part.strip() for part in (text or "").split(","))
    wrong = [one for one in ids if not _ID.fullmatch(one)]
    if wrong or len(set(ids)) != len(ids):
        problem = (f"{wrong[0]!r} is not a stage's id" if wrong else
                   "a stage is named twice")
        raise BadArgument(f"--after {text!r} can't be read: {problem}",
                          "name the stages it waits for as B<n>, separated by commas")
    return ids


def _reaches(state: states.State, start: str, goal: str) -> bool:
    """Whether the stage `start` waits for `goal`, directly or through other stages."""
    after = {stage.id: stage.after for stage in state.stages}
    seen, todo = set(), [start]
    while todo:
        one = todo.pop()
        if one == goal:
            return True
        if one not in seen:
            seen.add(one)
            todo += after.get(one, ())
    return False


def wait(target: str, stage_id: str, after: str, *, actor: str, settings=None,
         wait_s: float = write.WAIT_S, at: str | None = None) -> Waiting:
    """Make the stage `stage_id` of the round `target` wait for the stages `after`,
    `B1,B3`, beside the ones it waits for already. Anyone may: it only adds.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, and `Refused` when the
    round has ended, the stage or one it is to wait for is no recorded stage of the
    round, a stage named waits for this one already, directly or through others, or
    the stage waits for every one named already. A refusal leaves the log as it was.
    """
    if not isinstance(target, str) or "/" not in target:
        raise BadArgument(f"{target!r} is not a round",
                          "name the round the stage is in, as <project>/<slug>")
    project, slug = folders.split(target)
    if not _ID.fullmatch(stage_id or ""):
        raise BadArgument(f"{stage_id!r} is not a stage's id",
                          "name the stage as B<n>: mark ROUND B<n> --after LIST")
    wanted = parse_after(after)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    show = f"moxi rounds show {name}"

    def are_stages_of_the_round(view):
        missing = questions.unrecorded((stage_id, *wanted), view.state)
        if not missing:
            return None
        return Unmet(f"{', '.join(missing)} is not a stage of {name}: "
                     f"{questions.stages_said(view.state)}", show)

    def makes_no_circle(view):
        for one in wanted:
            if one == stage_id:
                return Unmet(f"{stage_id} can't wait for itself", show)
            if _reaches(view.state, one, stage_id):
                return Unmet(f"{one} waits for {stage_id} already, directly or "
                             f"through other stages, so {stage_id} waiting for {one} "
                             "would leave neither able to start; the operator takes "
                             "a wait off",
                             f"moxi rounds rule {name} {UNAFTER}B<n>=B<m> --record PTR")
        return None

    def adds_something(view):
        stage = find(view.state, stage_id)
        if stage is None or any(one not in stage.after for one in wanted):
            return None
        return Unmet(f"{stage_id} waits for {', '.join(wanted)} already, so there is "
                     "nothing to add", show)

    def changed(view, result):
        have = find(view.state, stage_id).after
        return write.Entry("stage.changed", {
            "id": stage_id, "after": [one for one in wanted if one not in have]})

    try:
        written = write.record(
            write.Where(home.project, home.folder, slug), actor=actor,
            gates=[ending.takes_new_work(name, "a mark"), are_stages_of_the_round,
                   makes_no_circle, adds_something],
            event=changed, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    event = written.events[0]
    return Waiting(name=name, stage=find(states.fold(written.round_events), stage_id),
                   added=tuple(event["after"]), event=event, failed=written.failed)


__all__ = ["UNSTAGE", "UNAFTER", "PLANNED", "LEFT", "BUILDING", "FINISHED", "VERIFIES",
           "CHECKS", "Waiting", "find", "state_of", "in_plan", "build_of", "waits_for",
           "cannot_leave", "keeps_what_it_is_of",
           "one_to_a_repo", "stays_out", "parse_unstage", "parse_unafter",
           "unstage_gates", "unafter_gates", "parse_after", "wait"]
