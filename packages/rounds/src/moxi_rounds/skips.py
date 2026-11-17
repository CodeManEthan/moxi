"""Skips: the operator's ruling that a step is not done.

A skip is a ruling, an act on the process, whose item is `skip:<step>` for a step of
the round or `skip:<step>:<stage>` for a step of one stage. It is one event. While it
stands the step counts as done wherever something waits on it; taken back, the step is
due again. `is_skipped(state, step, stage)` is the one answer to "is this step
skipped", and `live(state)` lists the skips that stand.

**What may be skipped.** `verify-design`, `ratify` and `retro`, each for the round;
`check`, `verify-build` and a step of the user's own, each for one stage; and the
questions step, `docket`, for one stage. `design`, `commission`, `build` and `finish`
are never skipped. A step the round's flow does not have is not skipped either, with
one exception: the questions step is skipped for a stage in any flow, since a question
holds a stage in every flow.

**A skip of the questions step** for a stage lets that stage past the questions that
hold it when the skip is recorded, and no other. The ruling lists their ids
(`covers`); what that list lets past is `holds`' to say. A skip that replaces an
earlier one lists what holds the stage when it is recorded, read with the earlier one
left out. It does not keep what the first listed: a question the first let past that
holds the stage no longer then (one ruled since) is not on the new list, and holds
the stage again if it is reopened.

**A skip of a finish action**, `skip-action:<stage>=<id>`, says one finish action is not
run for one stage and counts as done. It skips no step: the stage's `finish` is still
a step that waits on its gates. `action_live` lists the ones that stand, and
`finish_actions` reads them.

`parse` reads an item, `gates` are what the ruling's form runs under the lock and
`fields` what its event holds beside the item; `parse_action` and `action_gates` are
the same for a skip of a finish action.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import corrections, flows, holds, questions, write
from . import settings as own
from . import state as states
from .errors import BadArgument, Unmet

#: What starts the item of a skip; the step's name follows, then for a step of one
#: stage `:` and the stage's id.
SKIP = corrections.SKIP

#: What starts the item of a skip of one finish action; the stage's id follows, then
#: `=` and the action's id.
SKIP_ACTION = corrections.SKIP_ACTION

#: The questions step, which is skipped for one stage though it is a step of the round.
QUESTIONS = "docket"

#: The steps that are never skipped.
NEVER = ("design", "commission", "build", "finish")

_MAY = ("verify-design, the questions step (docket) for one stage, check, verify-build, "
        "a step of your own, ratify and retro")


@dataclass(frozen=True)
class Skip:
    """One skip that stands: the `id` of its ruling, the `step`, the `stage` for a
    step of one stage, the pointer it carries and when it was recorded. `covers` are
    the questions a skip of the questions step lets its stage past now, and `let_go`
    the ones it listed and lets past no longer."""

    id: str | None
    step: str
    stage: str | None
    record: str | None
    at: str
    covers: tuple[str, ...] = ()
    let_go: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionSkip:
    """One skip of a finish action that stands: the `id` of its ruling, the `stage`,
    the `action`'s id, the pointer it carries and when it was recorded."""

    id: str | None
    stage: str
    action: str
    record: str | None
    at: str


def item(step: str, stage: str | None = None) -> str:
    """The item of the skip of `step`, for `stage` when it is a step of one stage."""
    return f"{SKIP}{step}" if stage is None else f"{SKIP}{step}:{stage}"


def of_a_stage(step: str) -> bool:
    """Whether `step` is skipped for one stage: a step done for each build, a step of
    the user's own, or the questions step."""
    return step == QUESTIONS or flows.STEPS.get(step, flows.STAGE) == flows.STAGE


def parse(text: str) -> tuple[str, str | None] | None:
    """The step and the stage the item `text` skips, the stage None for a step of the
    round; None for an item that is no skip. Raises `BadArgument` for a skip that
    can't be read, and for one of rounds' own steps given with a stage it does not
    take or without the stage it needs."""
    if not text.startswith(SKIP):
        return None
    step, colon, stage = text[len(SKIP):].partition(":")
    if not step or (colon and not questions.STAGE.fullmatch(stage)):
        raise BadArgument(f"{text!r} does not say what is skipped",
                          f"say {SKIP}STEP for a step of the round, or {SKIP}STEP:B<n> "
                          "for a step of one stage")
    stage = stage if colon else None
    if step in flows.STEPS and step not in NEVER:
        if of_a_stage(step) and stage is None:
            raise BadArgument(f"{step} is skipped for one stage",
                              f"name the stage: {SKIP}{step}:B<n>")
        if not of_a_stage(step) and stage is not None:
            raise BadArgument(f"{step} is a step of the round, not of one stage",
                              f"say {SKIP}{step}")
    return step, stage


def said(step: str, stage: str | None = None) -> str:
    """A skip in words: `check for B2`, `retro`."""
    return step if stage is None else f"{step} for {stage}"


def gates(step: str, stage: str | None, *, name: str,
          supersedes: str | None = None) -> list[write.Gate]:
    """The gates of the skip of `step` (for `stage`) on the round `name`: the step is
    one that may be skipped and that the round's flow has, the stage is one of the
    round's, and the step is not skipped already, unless this skip names that one in
    `supersedes` and replaces it."""
    show = f"moxi rounds show {name}"
    mine = item(step, stage)

    def may_be_skipped(view):
        if step in NEVER:
            return Unmet(f"{step} is never skipped; the steps that may be are {_MAY}",
                         show)
        if step != QUESTIONS and not flows.has(view.state.steps, step):
            listed = ", ".join(flows.names(view.state.steps)) or "no steps"
            return Unmet(f"the flow {view.state.flow} of {name} has no {step} step "
                         f"(it lists {listed}), so there is nothing to skip", show)
        if stage is None and of_a_stage(step):
            return Unmet(f"{step} is done for each build, and is skipped for one stage",
                         f"moxi rounds rule {name} {SKIP}{step}:B<n> --record PTR")
        return None

    def names_a_stage_of_the_round(view):
        if stage is None or not questions.unrecorded((stage,), view.state):
            return None
        return Unmet(f"{stage} is not a stage of {name}: "
                     f"{questions.stages_said(view.state)}", show)

    def is_not_skipped_already(view):
        first = corrections.standing(view.state, mine)
        if first is None or first.id == supersedes:
            return None
        return Unmet(f"{said(step, stage)} is skipped already, by {first.id}; a "
                     "second skip replaces the first and names it",
                     f"moxi rounds rule {name} {mine} --record PTR --supersedes "
                     f"{first.id}")

    return [may_be_skipped, names_a_stage_of_the_round, is_not_skipped_already]


def fields(step: str, stage: str | None, view: write.View,
           supersedes: str | None = None) -> dict[str, object]:
    """What the event of the skip holds beside its item: for the questions step, the
    ids of the questions that hold the stage now, which are the ones the skip lets it
    past. With `supersedes`, the skip it replaces is read as not there."""
    if step != QUESTIONS:
        return {}
    left_out = () if supersedes is None else (supersedes,)
    return {"covers": [question.id for question in
                       holds.on_stage(view.state, stage, leaving_out=left_out)]}


def action_item(stage: str, action: str) -> str:
    """The item of the skip of the finish action `action` for `stage`."""
    return f"{SKIP_ACTION}{stage}={action}"


def parse_action(text: str) -> tuple[str, str] | None:
    """The stage and the action's id the item `text` skips; None for an item that is
    no skip of a finish action. Raises `BadArgument` for one that can't be read."""
    if not text.startswith(SKIP_ACTION):
        return None
    stage, equals, action = text[len(SKIP_ACTION):].partition("=")
    if not equals or not questions.STAGE.fullmatch(stage) or not action.strip():
        raise BadArgument(f"{text!r} does not say which finish action is skipped",
                          f"say {SKIP_ACTION}B<n>=ID, ID the action's id in the repo's "
                          "finish list")
    return stage, action


def action_gates(stage: str, action: str, *, name: str, settings,
                 supersedes: str | None = None) -> list[write.Gate]:
    """The gates of the skip of the finish action `action` for `stage` on the round
    `name`: the stage is one of the round's, built in a repo whose finish list, as
    `settings` have it, holds the action; and the action is not skipped for it already,
    unless this skip names that one in `supersedes` and replaces it."""
    show = f"moxi rounds show {name}"
    mine = action_item(stage, action)

    def names_an_action_of_the_stage(view):
        if questions.unrecorded((stage,), view.state):
            return Unmet(f"{stage} is not a stage of {name}: "
                         f"{questions.stages_said(view.state)}", show)
        repo = next((one.repo for one in view.state.stages if one.id == stage), None)
        if repo is None:
            return Unmet(f"{stage} is not built in a repo of its own, so it has no "
                         "finish action", show)
        ids = [one["id"] for one in own.build_setting(settings, repo, "finish")]
        if action in ids:
            return None
        return Unmet(f"{repo}'s finish list has no action {action}; its actions are "
                     f"{', '.join(ids) or 'none'}",
                     f"name one of them: moxi rounds rule {name} "
                     f"{action_item(stage, 'ID')} --record PTR")

    def is_not_skipped_already(view):
        first = corrections.standing(view.state, mine)
        if first is None or first.id == supersedes:
            return None
        return Unmet(f"the finish action {action} of {stage} is skipped already, by "
                     f"{first.id}; a second skip replaces the first and names it",
                     f"moxi rounds rule {name} {mine} --record PTR --supersedes "
                     f"{first.id}")

    return [names_an_action_of_the_stage, is_not_skipped_already]


def action_live(state: states.State) -> tuple[ActionSkip, ...]:
    """The skips of finish actions of the round `state` is of that stand, oldest
    first."""
    found = []
    for ruling in corrections.live(state.rulings):
        if not (ruling.rules or "").startswith(SKIP_ACTION):
            continue
        stage, _equals, action = ruling.rules[len(SKIP_ACTION):].partition("=")
        found.append(ActionSkip(id=ruling.id, stage=stage, action=action,
                                record=ruling.record, at=ruling.at))
    return tuple(found)


def live(state: states.State) -> tuple[Skip, ...]:
    """The skips of the round `state` is of that stand, oldest first."""
    found = []
    for ruling in corrections.live(state.rulings):
        if not (ruling.rules or "").startswith(SKIP):
            continue
        step, _colon, stage = ruling.rules[len(SKIP):].partition(":")
        found.append(Skip(
            id=ruling.id, step=step, stage=stage or None, record=ruling.record,
            at=ruling.at, let_go=ruling.let_go,
            covers=tuple(one for one in ruling.covers if one not in ruling.let_go)))
    return tuple(found)


def is_skipped(state: states.State, step: str, stage: str | None = None) -> bool:
    """Whether a skip that stands says `step` is not done: for the round when `stage`
    is None, else for that stage. A step of one stage is skipped stage by stage, so
    asked with no stage it is not.

    For the questions step this says only that a skip for the stage stands. Which
    questions it lets the stage past is `holds.on_stage`'s to say: a question asked
    after the skip holds the stage all the same."""
    return any(skip.step == step and skip.stage == stage for skip in live(state))


__all__ = ["SKIP", "SKIP_ACTION", "QUESTIONS", "NEVER", "Skip", "ActionSkip", "item",
           "of_a_stage", "parse", "said", "gates", "fields", "action_item", "parse_action",
           "action_gates", "action_live", "live", "is_skipped"]
