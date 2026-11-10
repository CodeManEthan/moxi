"""Stepping: the flow a round follows, and moving a round to another.

**The flow a round follows** is the one its log recorded last, with the steps recorded
beside the name: `current(state)` gives both, and `has_step(state, step)` says whether
that flow has a step. Neither reads the config. A round's log holds its flow's steps
from the opening on, so a flow edited in the config reaches no round until someone
moves the round, and that move is an event of its own.

**Moving a round.** `judge(state, flow, settings)` says what a move of the round to
the flow `flow` would be, with that flow's steps as the config has them now:

- a **step up** is a move to a flow later in the `tiers` setting that has every step
  the round's recorded list has. Naming the round's own flow takes the config's
  current steps for it, and is a step up when they have every recorded step.
- anything else is the operator's: a move to a flow earlier in `tiers`, a move to or
  from a flow `tiers` does not list, and any move that would lose a recorded step.

The lead makes a step up alone: `step_up(round, flow, actor=...)` appends one
`flow.changed` event with the new name and steps. Asked for any other move it refuses
and names the ruling that makes it. The operator's move is a ruling, recorded by
`ruling.rule` with the item `flow:<name>`; `gates` and `fields` here are what that
form runs and records.

A move changes the steps the round follows and nothing else: every question holds
what it held, every stage keeps its record, and every skip stands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import corrections, ending, flows, folders, write
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound, Unmet

#: What starts the item of a ruling that moves a round to another flow; the flow's
#: name follows.
FLOW = corrections.FLOW

#: Who a `flow.changed` event says moved the round.
LEAD = "lead"


@dataclass(frozen=True)
class Flow:
    """The flow a round follows: its `name`, and its step entries as the round's log
    recorded them."""

    name: str | None
    steps: tuple[dict, ...]


@dataclass(frozen=True)
class Move:
    """What moving a round to the flow `flow` would be.

    - `was`: the name of the flow the round follows now.
    - `steps`: the new flow's entries as the config has them, None when the config
      has no such flow; `known` then names the flows it has.
    - `same`: the round already follows that flow with exactly those steps.
    - `lost`: the steps the round's recorded list has and the new one lacks.
    - `up`: the move is a step up, which the lead makes alone.
    - `why`: for a move that is not, the reason it is the operator's.
    """

    was: str | None
    flow: str
    steps: list[dict] | None
    known: tuple[str, ...] = ()
    same: bool = False
    lost: tuple[str, ...] = ()
    up: bool = False
    why: str | None = None


@dataclass(frozen=True)
class Stepped:
    """A round after a step up: its `name`, the flow it followed (`was`), the `flow`
    it follows now with its `steps`, and the `event` written. `failed` pairs each
    after-append callback that raised with what it raised."""

    name: str
    was: str | None
    flow: str
    steps: tuple[dict, ...]
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


def current(state: states.State) -> Flow:
    """The flow the round `state` is of follows now, as its log recorded it."""
    return Flow(name=state.flow, steps=tuple(state.steps))


def has_step(state: states.State, step: str) -> bool:
    """Whether the flow the round follows now has the step `step`, listed or not."""
    return flows.has(state.steps, step)


def judge(state: states.State, flow: str, settings) -> Move:
    """What moving the round `state` is of to the flow `flow` would be, with the steps
    and the tiers `settings` has now."""
    known = flows.read(settings)
    steps = known.get(flow)
    if steps is None:
        return Move(was=state.flow, flow=flow, steps=None, known=tuple(known))
    have = flows.names(steps)
    lost = tuple(step for step in flows.names(state.steps) if step not in have)
    same = flow == state.flow and steps == state.steps
    tiers = list(settings["tiers"])
    why = None
    if lost:
        why = (f"the flow {flow} has no {' and no '.join(lost)} step, which the round "
               "follows now, and a move that loses a step")
    elif flow != state.flow:
        outside = [name for name in (state.flow, flow) if name not in tiers]
        if outside:
            why = (f"{' and '.join(str(name) for name in outside)} "
                   f"{'is' if len(outside) == 1 else 'are'} not among the tiers "
                   f"({', '.join(tiers)}), and a move to or from such a flow")
        elif tiers.index(flow) < tiers.index(state.flow):
            why = (f"{flow} comes before {state.flow} in the tiers "
                   f"({', '.join(tiers)}), and a step down")
    return Move(was=state.flow, flow=flow, steps=steps, known=tuple(known), same=same,
                lost=lost, up=why is None and not same, why=why)


def gates(flow: str, *, name: str, settings, again: str) -> list[write.Gate]:
    """The gates of any move of the round `name` to the flow `flow`: the config has
    that flow, and the move changes something. `again` is the command that makes the
    move, with `NAME` where the flow's name goes."""
    def is_a_flow(view):
        move = judge(view.state, flow, settings)
        if move.steps is not None:
            return None
        return Unmet(f"there is no flow {flow!r}; NAME below is one of "
                     f"{', '.join(move.known)}", again)

    def changes_something(view):
        if not judge(view.state, flow, settings).same:
            return None
        return Unmet(f"{name} follows the flow {flow} already, with the steps the "
                     "config has for it now, so there is nothing to move",
                     f"moxi rounds show {name}")

    return [is_a_flow, changes_something]


def fields(flow: str, view: write.View, settings) -> dict[str, object]:
    """What the event of a move to `flow` holds: the name the round's flow had, the
    new name, and its steps as the config has them now."""
    return {"was": view.state.flow, "flow": flow,
            "steps": judge(view.state, flow, settings).steps}


def step_up(target: str, flow: str, *, actor: str, settings=None,
            wait_s: float = write.WAIT_S, at: str | None = None) -> Stepped:
    """Move the round `target`, `<project>/<slug>`, up to the flow `flow`, and return
    it as moved. This is the lead's move and carries no pointer.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, and `Refused` when the
    round has ended, the config has no such flow, the round follows it already with
    the same steps, or the move is no step up: that move is the operator's, and the
    refusal names the ruling that makes it. A refusal leaves the log as it was.
    """
    if not isinstance(target, str) or "/" not in target:
        raise BadArgument(f"{target!r} is not a round",
                          "name the round to move, as <project>/<slug>")
    if not isinstance(flow, str) or not flow.strip():
        raise BadArgument("--flow names no flow", "name the flow: --flow NAME")
    project, slug = folders.split(target)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"

    def is_a_step_up(view):
        move = judge(view.state, flow, settings)
        if move.steps is None or move.same or move.up:
            return None
        return Unmet(f"{move.why} is the operator's: the lead moves a round only up, "
                     "to a later tier that has every step the round follows",
                     f"moxi rounds rule {name} {FLOW}{flow} --record PTR")

    def changed(view, result):
        return write.Entry("flow.changed", {**fields(flow, view, settings), "by": LEAD})

    try:
        written = write.record(
            write.Where(home.project, home.folder, slug), actor=actor,
            gates=[ending.takes_new_work(name, "a change of flow"),
                   *gates(flow, name=name, settings=settings,
                          again=f"moxi rounds mark {name} --flow NAME"),
                   is_a_step_up],
            event=changed, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    event = written.events[0]
    return Stepped(name=name, was=event["was"], flow=event["flow"],
                   steps=tuple(event["steps"]), event=event, failed=written.failed)


__all__ = ["FLOW", "LEAD", "Flow", "Move", "Stepped", "current", "has_step", "judge",
           "gates", "fields", "step_up"]
