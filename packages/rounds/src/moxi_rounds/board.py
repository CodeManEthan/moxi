"""The board: what the rounds page draws, derived from what rounds already says.

The page decides nothing about a step. This module hands it every step's state as a
cell, the short word under its marker and who owes it, each round's lines and group,
the "where it is" phrase, the strip of builds last acted on, and each marker's
record. It reads nothing itself but a question's passage (`passage`): a round's part
(`Part`) is made from what the caller read, the round's show and `seen`, and the
rest is derived from it.

**A part** (`part`) holds one round's show, `seen`, its steps as `steps.of` read them
once and its groups as `next_step.groups` makes them from those steps, without any
step's lines. A record fills one step's lines when it is asked for (`record`).

**Columns** (`columns`): the round's own steps (`design`, `verify-design`, `docket`),
each stage's steps (`commission`, `build`, `check`, any steps of the user's own in
the order the flows list them, `verify-build`, `finish`), then `ratify`, `retro` and
`close`. `close` is not a step: its cell says what holds the round's close.

**Cells** take the first state of `STATES` that fits:

- `yours`: the docket with open questions or rulings of the round not shown; `ratify`
  with major decisions not ruled; `close` with open jobs for the operator. `count`
  says how many.
- `none`: the step is skipped, or it is the docket of a round that never had a
  question.
- `done`: the step is done.
- `failed`: not settled, and the design's newest verdict for its current version sent
  it back; the check's newest run at the build's tip failed, or CI's newest read
  there is red; the build's newest verdict sent it back; a command step's newest run
  at the tip failed.
- `owed`: the step is ready, or `close` while a rejected decision's fix is owed.
- `waiting`: the step is waiting.
- `child`: the step is not built and its stage stands for a child round.
- `todo`: the rest, `close` with nothing above among them.

A cell is None where `steps.of` gives no step: a column the round's flow lacks, and
the check of a stage whose repo declares none.

**Who** does a step is `BY`'s for the docket, ratify, commission and check, and for
every other step the `by` of its entry in the round's recorded flow.

**Lines.** The round line holds the round's own cells, its lead stage's stage cells
and the end cells; then a line for each other stage with an open build; then the
other stages, folded. **Groups**, first that fits: `yours`, `stuck` (a failed cell; a
stage whose first step not settled is `finish` and waiting; a design changed since
its version was frozen), `wrapping` (`retro` ready, or every stage of the plan
finished), `moving`.

**The strip** (`strip`): a build's act is the newest event of its round's log that
names its stage, from its commission on; the strip is the ten newest acts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (builds, checking, ci, closing, decisions, drift, findings, finish_actions,
               flows, holds, next_step, pointers, questions, report_block, showing, skips, stages,
               steps, versions, waits)
from . import state as states

# -- the columns -------------------------------------------------------------------------

ROUND_PART, STAGE_PART, END_PART = "round", "stage", "end"
CLOSE = "close"
ROUND_STEPS = (steps.DESIGN, steps.VERIFIES, steps.QUESTIONS)
_STAGE_HEAD = (steps.COMMISSION, steps.BUILD, steps.CHECK)
_STAGE_TAIL = (steps.VERIFY_BUILD, steps.FINISH)
END_STEPS = (steps.RATIFY, steps.RETRO, CLOSE)
_LABELS = {steps.VERIFIES: "verify", steps.VERIFY_BUILD: "verify"}

# -- the cell states ---------------------------------------------------------------------

YOURS, NONE, DONE, FAILED = "yours", "none", "done", "failed"
OWED, WAITING, CHILD, TODO = "owed", "waiting", "child", "todo"
#: The eight states of a cell, in the order a cell takes the first that fits.
STATES = (YOURS, NONE, DONE, FAILED, OWED, WAITING, CHILD, TODO)
_SETTLED = (DONE, NONE)

# -- who does a step ---------------------------------------------------------------------

OPERATOR, LEAD, BUILDER = next_step.OPERATOR, "lead", "builder"
#: Who does the steps whose flow entry may say no one: the docket and the ratifying
#: are the operator's, a commission the lead's and a check the builder's.
BY = {steps.QUESTIONS: OPERATOR, steps.RATIFY: OPERATOR, steps.COMMISSION: LEAD,
      steps.CHECK: BUILDER}

# -- the groups --------------------------------------------------------------------------

STUCK, WRAPPING, MOVING = "stuck", "wrapping", "moving"
#: The groups, in the order a round takes the first that fits and the board shows them.
GROUPS = (YOURS, STUCK, WRAPPING, MOVING)

#: The phrase's weights: his items, what went wrong, what waits, what moves.
BAD, WAITS, MOVES, QUIET = "bad", "waits", "moves", "quiet"

#: How many acts the strip holds.
STRIP_SIZE = 10

#: How many stages besides the lead one and the ones building unfold as one `+N`.
FOLD_MORE = 4

#: The line kinds.
ROUND_LINE, BUILD_LINE, STAGE_LINE = "round", "build", "stage"
FOLD_MORE_WORD, FOLD_LANDED, FOLD_NOT_STARTED = "more", "landed", "not-started"


def columns(flows_of: Iterable[Sequence[Mapping]]) -> list[dict]:
    """The board's columns, the same for every round: `flows_of` holds each open
    round's recorded flow steps. A step of the user's own gets a column only when one
    of those flows has it, before `verify-build`, in the order the flows list them."""
    own: list[str] = []
    for entries in flows_of:
        for entry in entries:
            name = str(entry.get("step"))
            if flows.is_user_step(entry) and name not in own:
                own.append(name)
    found = [{"id": one, "label": _LABELS.get(one, one), "part": ROUND_PART}
             for one in ROUND_STEPS]
    found += [{"id": one, "label": _LABELS.get(one, one), "part": STAGE_PART}
              for one in (*_STAGE_HEAD, *own, *_STAGE_TAIL)]
    found += [{"id": one, "label": one, "part": END_PART} for one in END_STEPS]
    return found


# -- a round's part ----------------------------------------------------------------------

@dataclass(frozen=True)
class Part:
    """One round as the board reads it: the show (`found`), what was read of its open
    builds (`seen`), its steps read once (`every`) and its groups made from them
    without any step's lines (`groups`)."""

    found: showing.Shown
    seen: Mapping
    every: tuple[steps.Step, ...]
    groups: tuple[dict, ...]
    models: Mapping = field(default_factory=dict)

    @property
    def state(self) -> states.State:
        return self.found.state

    @property
    def name(self) -> str:
        return self.found.name

    def step(self, column: str, stage: str | None) -> steps.Step | None:
        """The step of the column for the stage; None where the round has none."""
        return next((one for one in self.every
                     if one.step == column and one.stage == stage), None)

    def at(self) -> steps.Step | None:
        """The step the round is at: the first not settled, for an open round."""
        if self.state.status != states.OPEN:
            return None
        return next((one for one in self.every if not one.settled), None)


def step_of(part_: Part) -> dict | None:
    """The step the round is at as data, `{"step", "stage", "state"}`, as `next` reads
    it, with git and the design read; None for a round at no step and one that ended.
    On a round whose `design.md` changed since its version was frozen, `moxi rounds
    list` keeps the step its log alone gives, and this one says the commission waits."""
    at = part_.at()
    return None if at is None else {"step": at.step, "stage": at.stage, "state": at.state}


def part(found: showing.Shown, seen: Mapping, *, models: Mapping | None = None) -> Part:
    """The part of the round `found`, readable, with `seen` as `checking.seen_of`
    read it: its steps, read once, and its groups made from them without any step's
    lines. Reads nothing."""
    every = steps.of(found.state, name=found.name, on_closed=found.on_closed,
                     register=found.register, seen=seen)
    models = dict(models or {})
    made = next_step.groups(found, every, models, seen, fill=False)
    return Part(found, seen, every, tuple(made), models)


# -- his items ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Items:
    """What of the operator's a round holds, as what waits on him gives it: open
    `questions`, rulings `not_shown`, major `decisions` not ruled and open `jobs`, each
    a tuple of ids."""

    questions: tuple[str, ...] = ()
    not_shown: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return (len(self.questions) + len(self.not_shown) + len(self.decisions)
                + len(self.jobs))


def is_job(item_id: str) -> bool:
    """Whether a waiting item's id is a job for the operator (`B1.W1`), not the fix a
    rejected decision owes (`B1.M1`), which is the lead's."""
    found = waits.KEY.fullmatch(str(item_id))
    return found is not None and found.group(2).startswith("W")


def items_of(waiting: Mapping, name: str) -> Items:
    """The round `name`'s items in `waiting`, the board's value of what waits on him."""
    return Items(
        questions=tuple(one["id"] for one in waiting.get("questions", ())
                        if one.get("holder") == name),
        not_shown=tuple(row["id"] for back in waiting.get("not_shown", ())
                        for row in back.get("rows", ()) if row.get("holder") == name),
        decisions=tuple(one["id"] for one in waiting.get("decisions", ())
                        if one.get("holder") == name),
        jobs=tuple(one["id"] for one in waiting.get("waits", ())
                   if one.get("round") == name and is_job(one["id"])))


def fixes_owed(state: states.State) -> tuple[waits.Wait, ...]:
    """The fixes a rejected decision owes that are not marked done, the lead's."""
    return tuple(one for one in waits.open_of(state) if not is_job(one.key))


# -- the cells ---------------------------------------------------------------------------

def _build(part_: Part, stage: str | None) -> builds.Build | None:
    if stage is None:
        return None
    one = builds.current(part_.state, stage)
    return None if one is None or one.ended is not None else one


def _head(part_: Part, stage: str | None) -> str | None:
    seen = (part_.seen or {}).get(stage) if stage is not None else None
    if seen is None or seen.problem is not None:
        return None
    return seen.head


def _run_failed(part_: Part, stage: str, step: str) -> bool:
    build, head = _build(part_, stage), _head(part_, stage)
    if build is None or head is None:
        return False
    run = showing.check_run(part_.state, build, step)
    return run is not None and run["commit"] == head and run["exit_code"] != 0


def _ci_at_head(part_: Part, stage: str) -> str | None:
    """CI's newest reading of the stage's build, where it was read at its head."""
    head = _head(part_, stage)
    if _build(part_, stage) is None or head is None:
        return None
    newest = ci.newest(part_.state, stage)
    if newest is None or newest.get("commit") != head:
        return None
    return newest.get("reading")


def _build_sent_back(part_: Part, stage: str) -> bool:
    build = _build(part_, stage)
    newest = None if build is None else drift.newest_verdict(part_.state, build)
    return newest is not None and newest.verdict == report_block.FOLD_FIRST


def _design_sent_back(state: states.State) -> bool:
    version = versions.current(state)
    read = () if version is None else versions.design_verdicts(state, version.n)
    return bool(read) and read[-1].verdict == report_block.FOLD_FIRST


def _drift_owed(part_: Part, stage: str) -> bool:
    seen = (part_.seen or {}).get(stage)
    moved = None if seen is None else seen.drift
    return bool(getattr(moved, "owed", False))


def _failed(part_: Part, step: steps.Step) -> str | None:
    """The word of a step that failed, or None."""
    if step.step == steps.VERIFIES and _design_sent_back(part_.state):
        return "back"
    if step.stage is None:
        return None
    if step.step == steps.CHECK:
        if _run_failed(part_, step.stage, checking.CHECK):
            return "failed"
        if _ci_at_head(part_, step.stage) == ci.RED:
            return "red"
    if step.step == steps.VERIFY_BUILD and _build_sent_back(part_, step.stage):
        return "back"
    entry = step.entry
    if (entry is not None and flows.is_user_step(entry)
            and entry.get("does") == flows.COMMAND
            and _run_failed(part_, step.stage, step.step)):
        return "failed"
    return None


def _done_word(part_: Part, step: steps.Step) -> str | None:
    if step.step == steps.DESIGN:
        version = versions.current(part_.state)
        return None if version is None else f"v{version.n}"
    if step.step in (steps.VERIFIES, steps.VERIFY_BUILD):
        return "pub"
    if step.step == steps.CHECK and step.stage is not None:
        build = builds.current(part_.state, step.stage)
        newest = None if build is None else ci.newest(part_.state, step.stage)
        return "green" if newest is not None and newest.get("reading") == ci.GREEN else None
    if step.step == steps.FINISH:
        return "landed"
    return None


def _owed_word(part_: Part, step: steps.Step) -> str | None:
    if step.step == steps.BUILD:
        return "building"
    if step.step == steps.CHECK:
        return "pending" if _ci_at_head(part_, step.stage) == ci.PENDING else "owed"
    if step.step == steps.VERIFY_BUILD:
        return "drift" if _drift_owed(part_, step.stage) else "owed"
    if step.step == steps.COMMISSION:
        return "ready"
    if step.step == steps.RETRO:
        return "owed"
    return None


def by_of(step: steps.Step) -> str | None:
    """Who does `step`: `BY`'s, else its entry's in the round's recorded flow."""
    return BY.get(step.step) or (step.entry or {}).get("by")


def _who(part_: Part, step: steps.Step) -> str | None:
    """The name rounds knows for who does the step: the builder of the stage's build,
    from its commission."""
    build = _build(part_, step.stage)
    if build is None or step.step in BY:
        return None
    return checking.commission_of(part_.state, build).get("builder")


def _cell(state_word: str, *, word=None, count=None, step=None, by=None, who=None) -> dict:
    return {"state": state_word, "word": word, "count": count,
            "settled": state_word in _SETTLED, "here": False,
            "step_state": None if step is None else step.state, "by": by, "who": who}


def cell(part_: Part, step: steps.Step, items: Items) -> dict:
    """The cell of `step`: the first of `STATES` that fits it."""
    by, who = by_of(step), _who(part_, step)
    made = {"step": step, "by": by, "who": who}
    if step.step == steps.QUESTIONS and step.stage is None:
        count = len(items.questions) + len(items.not_shown)
        if count:
            return _cell(YOURS, count=count, **made)
    if step.step == steps.RATIFY and items.decisions:
        return _cell(YOURS, count=len(items.decisions), **made)
    if step.state == steps.SKIPPED:
        return _cell(NONE, word="skipped", **made)
    if step.step == steps.QUESTIONS and not part_.state.questions:
        return _cell(NONE, **made)
    if step.state == steps.DONE:
        return _cell(DONE, word=_done_word(part_, step), **made)
    failed = _failed(part_, step)
    if failed is not None:
        return _cell(FAILED, word=failed, **made)
    if step.state == steps.READY:
        return _cell(OWED, word=_owed_word(part_, step), **made)
    if step.state == steps.WAITING:
        held = (step.step in steps.HELD and step.stage is not None
                and holds.on_stage(part_.state, step.stage,
                                   on_closed=part_.found.on_closed))
        return _cell(WAITING, word="held" if held else "waits", **made)
    planned = None if step.stage is None else stages.find(part_.state, step.stage)
    if step.state == steps.NOT_BUILT and planned is not None and planned.round is not None:
        return _cell(CHILD, word="child", **made)
    return _cell(TODO, word="not built" if step.state == steps.NOT_BUILT else None, **made)


def close_cell(part_: Part, items: Items) -> dict:
    """The close's cell: what holds the round's close."""
    if items.jobs:
        return _cell(YOURS, count=len(items.jobs), by=OPERATOR)
    if fixes_owed(part_.state):
        return _cell(OWED, word="fix", by=LEAD)
    return _cell(TODO)


def cells(part_: Part, items: Items) -> dict[tuple[str, str | None], dict]:
    """Every cell of the round, by `(column, stage)`: one for each step `steps.of`
    gives, and the close's under `(close, None)`."""
    found = {(one.step, one.stage): cell(part_, one, items) for one in part_.every}
    found[(CLOSE, None)] = close_cell(part_, items)
    return found


# -- the lines ---------------------------------------------------------------------------

def _first_unsettled(part_: Part, stage: str) -> steps.Step | None:
    return next((one for one in part_.every if one.stage == stage and not one.settled),
                None)


def lead_stage(part_: Part) -> str | None:
    """The stage the round line carries, the first that fits: the stage of the round's
    step; the first with an open build; the only one; the first with no build and no
    child round, while the round has no build at all; the last. None with no plan."""
    plan = stages.in_plan(part_.state)
    if not plan:
        return None
    at = part_.at()
    if at is not None and at.stage is not None:
        return at.stage
    building = [one.id for one in plan
                if stages.build_of(part_.state, one.id) == stages.BUILDING]
    if building:
        return building[0]
    if len(plan) == 1:
        return plan[0].id
    if not builds.builds(part_.state):
        fresh = [one.id for one in plan if one.round is None]
        if fresh:
            return fresh[0]
    return plan[-1].id


def _row(columns_: Sequence[Mapping], found: Mapping, *, parts: Sequence[str],
         stage: str | None, here: Iterable[tuple[str, str | None]]) -> list[dict | None]:
    here = set(here)
    row = []
    for column in columns_:
        if column["part"] not in parts:
            row.append(None)
            continue
        key = (column["id"], stage if column["part"] == STAGE_PART else None)
        one = found.get(key)
        if one is not None:
            one = {**one, "here": key in here}
        row.append(one)
    return row


def _key(step: steps.Step | None) -> tuple[str, str | None] | None:
    return None if step is None else (step.step, step.stage)


def lines(part_: Part, columns_: Sequence[Mapping], items: Items,
          found: Mapping | None = None) -> tuple[list[dict], dict]:
    """The round's lines, each `{kind, stage, branch, fold, phrase, cells}`, and its
    `folds`: the round line, a line for each other stage with an open build, then the
    rest of the stages folded (`more` with `FOLD_MORE` or fewer of them, else the
    finished ones `landed` and the others `not-started`)."""
    found = cells(part_, items) if found is None else found
    state = part_.state
    plan = stages.in_plan(state)
    lead = lead_stage(part_)
    by_id = {one.id: one for one in plan}
    at = part_.at()

    def stage_line(kind: str, stage: str, fold: str | None) -> dict:
        first = _first_unsettled(part_, stage)
        return {"kind": kind, "stage": stage, "branch": by_id[stage].branch, "fold": fold,
                "phrase": phrase(part_, found, items, stage=stage),
                "cells": _row(columns_, found, parts=(STAGE_PART,), stage=stage,
                              here=[k for k in (_key(first),) if k])}

    here = [k for k in (_key(at), _key(_first_unsettled(part_, lead) if lead else None))
            if k]
    first = {"kind": ROUND_LINE, "stage": lead,
             "branch": None if lead is None else by_id[lead].branch, "fold": None,
             "phrase": phrase(part_, found, items),
             "cells": _row(columns_, found, parts=(ROUND_PART, STAGE_PART, END_PART),
                           stage=lead, here=here)}
    made = [first]
    others = [one.id for one in plan if one.id != lead]
    building = [one for one in others
                if stages.build_of(state, one) == stages.BUILDING]
    made += [stage_line(BUILD_LINE, one, None) for one in building]
    rest = [one for one in others if one not in building]
    finished = questions.finished_stages(state)
    folds = {"more": 0, "landed": 0,
             "not_started": {"n": 0, "ready": 0, "held": 0, "child": 0}}
    for one in rest:
        if len(rest) <= FOLD_MORE:
            fold = FOLD_MORE_WORD
            folds["more"] += 1
        elif one in finished:
            fold = FOLD_LANDED
            folds["landed"] += 1
        else:
            fold = FOLD_NOT_STARTED
            counted = folds["not_started"]
            counted["n"] += 1
            commission = found.get((steps.COMMISSION, one))
            counted["ready"] += commission is not None and commission["state"] == OWED
            counted["held"] += bool(holds.on_stage(state, one,
                                                   on_closed=part_.found.on_closed))
            counted["child"] += by_id[one].round is not None
        made.append(stage_line(STAGE_LINE, one, fold))
    return made, folds


# -- the group and the phrase ------------------------------------------------------------

def design_moved(part_: Part):
    """What `commission` refuses on while the design changed since its version was
    frozen, as `seen` read it; None when it did not change or was not read."""
    seen = (part_.seen or {}).get(None)
    return None if seen is None else seen.design


def _held_at_finish(part_: Part) -> list[steps.Step]:
    found = []
    for one in stages.in_plan(part_.state):
        first = _first_unsettled(part_, one.id)
        if (first is not None and first.step == steps.FINISH
                and first.state == steps.WAITING):
            found.append(first)
    return found


def group(part_: Part, found: Mapping, items: Items) -> str:
    """The round's group: `yours`, `stuck`, `wrapping` or `moving`, the first that
    fits."""
    if items.count:
        return YOURS
    if (any(one["state"] == FAILED for one in found.values()) or _held_at_finish(part_)
            or design_moved(part_) is not None):
        return STUCK
    retro = part_.step(steps.RETRO, None)
    plan = stages.in_plan(part_.state)
    finished = questions.finished_stages(part_.state)
    if ((retro is not None and retro.state == steps.READY)
            or (plan and all(one.id in finished for one in plan))):
        return WRAPPING
    return MOVING


def _plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word if count == 1 else many or word + 's'}"


def _part(text: str, weight: str) -> dict:
    return {"text": text, "weight": weight}


def _ci_red_name(part_: Part, stage: str) -> str:
    newest = ci.newest(part_.state, stage) or {}
    for flow in newest.get("workflows") or ():
        for job in flow.get("jobs") or ():
            if job.get("reading") == ci.RED:
                return str(job.get("name"))
        if flow.get("reading") == ci.RED:
            return Path(str(flow.get("path"))).stem
    return ""


def phrase(part_: Part, found: Mapping, items: Items, *,
           stage: str | None = None) -> list[dict]:
    """The "where it is" words, as parts in order, each with its weight: his items,
    what went wrong, what waits, what moves; `nothing owed` with none. With `stage`,
    made from that stage alone."""
    made: list[dict] = []
    if stage is None:
        for count, word, many in ((len(items.questions), "question", None),
                                  (len(items.not_shown), "to read back", "to read back"),
                                  (len(items.decisions), "decision", None),
                                  (len(items.jobs), "job for you", "jobs for you")):
            if count:
                made.append(_part(_plural(count, word, many), YOURS))
    stage_ids = ([stage] if stage is not None
                 else [one.id for one in stages.in_plan(part_.state)])
    if stage is None and found.get((steps.VERIFIES, None), {}).get("state") == FAILED:
        made.append(_part("design sent back", BAD))
    for one in stage_ids:
        for column in [one_step.step for one_step in part_.every if one_step.stage == one]:
            got = found.get((column, one))
            if got is None or got["state"] != FAILED:
                continue
            if got["word"] == "red":
                name = _ci_red_name(part_, one)
                made.append(_part(f"CI {name + ' ' if name else ''}red on {one}", BAD))
            elif got["word"] == "back":
                made.append(_part(f"{one} sent back", BAD))
            else:
                made.append(_part(f"{column} failed on {one}", BAD))
    for step in _held_at_finish(part_):
        if step.stage in stage_ids and step.waits:
            made.append(_part(f"{step.step} of {step.stage} waits: "
                              f"{step.waits[0].reason}", WAITS))
    moved = design_moved(part_)
    if moved is not None and stage is None:
        made.append(_part(moved.reason, WAITS))
    made += _moving(part_, found, stage_ids, round_too=stage is None)
    return made or [_part("nothing owed", QUIET)]


def _moving(part_: Part, found: Mapping, stage_ids: Sequence[str], *,
            round_too: bool) -> list[dict]:
    owed: list[tuple[steps.Step, dict]] = []
    at = part_.at()
    keys = [_key(_first_unsettled(part_, one)) for one in stage_ids]
    if round_too and at is not None and at.stage is None:
        keys.insert(0, _key(at))
    if round_too:
        keys += [(one.step, None) for one in part_.every
                 if one.stage is None and one.state == steps.READY]
    for key in dict.fromkeys(k for k in keys if k):
        got = found.get(key)
        step = part_.step(*key)
        if got is not None and step is not None and got["state"] == OWED \
                and got["by"] != OPERATOR:
            owed.append((step, got))
    made = []
    for name in dict.fromkeys(step.step for step, _got in owed):
        these = [(step, got) for step, got in owed if step.step == name]
        role = these[0][1]["by"]
        said = f" ({role})" if role else ""
        if name == steps.COMMISSION:
            text = (f"{these[0][0].stage} ready to start{said}" if len(these) == 1
                    else f"{len(these)} stages ready to start{said}")
        elif len(these) > 1:
            text = f"{len(these)} {name}s owed"
        elif these[0][0].stage is not None and name != steps.CHECK:
            text = f"{name} of {these[0][0].stage} owed{said}"
        else:
            text = f"{name} owed{said}"
        made.append(_part(text, MOVES))
    return made


# -- the round as the board shows it -----------------------------------------------------

def last_at(part_: Part) -> str | None:
    """When the round's newest event was written."""
    history = part_.state.history
    return history[-1].get("at") if history else None


def rev(value: Mapping) -> str:
    """A short hash of a value, so the page knows when an open record is stale."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def round_value(part_: Part, columns_: Sequence[Mapping], items: Items, *,
                step: Mapping | None, counts: Mapping) -> dict:
    """The round as the board shows it: what its show says of it, `step` and `counts`
    as the rounds listing gives them, its group, phrase, lines and folds."""
    state = part_.state
    found = cells(part_, items)
    made_lines, folds = lines(part_, columns_, items, found)
    version = versions.current(state)
    value = {"name": part_.name, "project": part_.found.project, "slug": part_.found.slug,
             "description": state.description, "flow": state.flow,
             "standing": str(versions.standing(state)),
             "version": None if version is None else version.n,
             "step": None if step is None else dict(step), "counts": dict(counts),
             "group": group(part_, found, items),
             "phrase": phrase(part_, found, items), "mine": items.count,
             "last_at": last_at(part_), "lines": made_lines, "folds": folds}
    value["rev"] = rev(value)
    return value


def ordered(values: Iterable[Mapping]) -> list[Mapping]:
    """The rounds in board order: by group; within `yours` by his items, most first,
    then oldest event first; within the others oldest event first."""
    def key(one: Mapping):
        mine = -one["mine"] if one["group"] == YOURS else 0
        return (GROUPS.index(one["group"]), mine, one["last_at"] or "", one["name"])
    return sorted(values, key=key)


# -- the strip ---------------------------------------------------------------------------

def _names_stage(event: Mapping, stage: str) -> bool:
    if event.get("stage") == stage:
        return True
    for key in ("id", "rules"):
        value = event.get(key)
        if isinstance(value, str) and value.startswith(f"{stage}."):
            return True
    return False


def acts(part_: Part) -> list[dict]:
    """Each build of the round with its act: the newest event of the log that names its
    stage, from its commission up to the next commission of that stage."""
    history = part_.state.history
    made = []
    every = builds.builds(part_.state)
    for one in every:
        later = [other.n for other in every if other.stage == one.stage and other.n > one.n]
        end = min(later) if later else None
        newest = None
        for event in history:
            n = event.get("n", 0)
            if n < one.n or (end is not None and n >= end):
                continue
            if _names_stage(event, one.stage):
                newest = event
        if newest is not None:
            made.append({"build": one, "event": newest})
    return made


#: Each type of event in a few words, as the strip says it when the type's fields add
#: nothing; `act_words` adds them for the types whose fields say more. A card's act is
#: always words, never the event's name.
ACT_WORDS: dict[str, str] = {
    "round.opened": "round opened", "flow.changed": "flow changed",
    "design.frozen": "design frozen", "verdict.recorded": "verdict recorded",
    "question.asked": "question asked", "question.changed": "question changed",
    "stage.declared": "stage declared", "stage.changed": "stage changed",
    "ruling.recorded": "ruling recorded", "ruling.shown": "rulings shown",
    "build.commissioned": "commissioned", "check.ran": "check ran", "ci.read": "CI read",
    "finding.marked": "finding marked", "wait.marked": "job marked",
    "brief.sized": "brief sized", "finish.action": "finish step run",
    "finish.done": "landed", "lock.cleared": "finish lock cleared",
    "build.dropped": "dropped", "round.dropped": "round dropped",
    "thread.decided": "thread decided", "repair": "record repaired",
    "round.closed": "round closed"}


def act_words(event: Mapping) -> str:
    """An act in words: `landed`, `CI red`, `local check passed`, `verify passed`,
    `re-check passed`, `drift re-check passed`, `sent back`, `<id> marked fixed`,
    `job marked done`, `finish: merge ok`, `brief sized`, `ruling recorded`, and so on
    from `ACT_WORDS`; a type it has no words for reads as its name in words."""
    kind = str(event.get("type"))
    if kind == ci.READ and event.get("reading"):
        return f"CI {event.get('reading')}"
    if kind == checking.RAN:
        step = event.get("step") or checking.CHECK
        name = "local check" if step == ci.LOCAL else step
        code = event.get("exit_code")
        return f"{name} passed" if code == 0 else f"{name} failed"
    if kind == "verdict.recorded" and event.get("stage") is not None:
        if event.get("verdict") == report_block.FOLD_FIRST:
            return "sent back"
        if event.get("verdict") == report_block.PUBLISHABLE:
            if event.get("pass") == report_block.RECHECK:
                return "re-check passed"
            if event.get("pass") == report_block.DRIFT:
                return "drift re-check passed"
            return "verify passed"
    if kind == findings.MARKED and event.get("id") and event.get("state"):
        return f"{event.get('id')} marked {event.get('state')}"
    if kind == "wait.marked" and event.get("state"):
        return f"job marked {event.get('state')}"
    if kind == finish_actions.ACTION and event.get("kind") and event.get("state"):
        return f"finish: {event.get('kind')} {event.get('state')}"
    words = ACT_WORDS.get(kind)
    return words if words is not None else kind.replace(".", " ").replace("_", " ")


def strip(parts: Iterable[Part], *, size: int = STRIP_SIZE) -> list[dict]:
    """The builds last acted on, newest first, `size` of them, ties to the later event
    in its log: each with its round, stage, whether its round has closed, its act and
    when, the builder and branch, and its stage's cells as they read now."""
    found = []
    for one in parts:
        for act in acts(one):
            found.append((one, act))
    found.sort(key=lambda pair: (pair[1]["event"].get("at") or "",
                                 pair[1]["event"].get("n", 0)), reverse=True)
    made = []
    for one, act in found[:size]:
        build, event = act["build"], act["event"]
        stage_steps = [step for step in one.every if step.stage == build.stage]
        made.append({"round": one.name, "project": one.found.project,
                     "stage": build.stage,
                     "closed": one.state.status != states.OPEN,
                     "act_at": event.get("at"), "act": act_words(event),
                     "builder": checking.commission_of(one.state, build).get("builder"),
                     "branch": build.branch,
                     "steps": [step.step for step in stage_steps],
                     "cells": [cell(one, step, Items()) for step in stage_steps]})
    return made


def tenth_act(parts: Iterable[Part], *, size: int = STRIP_SIZE) -> str | None:
    """When the `size`th newest act of `parts` was written; None with fewer acts."""
    times = sorted((act["event"].get("at") or "" for one in parts for act in acts(one)),
                   reverse=True)
    return times[size - 1] if len(times) >= size else None


# -- which step an event belongs to ------------------------------------------------------

#: The step each type of event belongs to, for a record's events; None for one that
#: belongs to the round's card alone. `verdict.recorded`, `ruling.recorded`,
#: `check.ran` and `brief.sized` are read by `event_step`, since their fields say.
EVENT_STEPS: dict[str, str | None] = {
    "round.opened": steps.DESIGN, "flow.changed": None, "design.frozen": steps.DESIGN,
    "thread.decided": steps.VERIFIES, "question.asked": steps.QUESTIONS,
    "question.changed": steps.QUESTIONS, "ruling.shown": steps.QUESTIONS,
    "stage.declared": steps.COMMISSION, "stage.changed": steps.COMMISSION,
    "build.commissioned": steps.COMMISSION, "build.dropped": steps.COMMISSION,
    "ci.read": steps.CHECK, "finding.marked": steps.VERIFY_BUILD,
    "wait.marked": CLOSE, "finish.action": steps.FINISH, "finish.done": steps.FINISH,
    "lock.cleared": steps.FINISH, "repair": None, "round.closed": CLOSE,
    "round.dropped": CLOSE, "verdict.recorded": None, "ruling.recorded": None,
    "check.ran": None, "brief.sized": None}


def event_step(event: Mapping) -> tuple[str | None, str | None]:
    """The `(step, stage)` an event belongs to; `(None, None)` for the card alone."""
    kind = event.get("type")
    stage = event.get("stage")
    for key in ("id", "rules"):
        value = event.get(key)
        found = re.match(r"(B[1-9][0-9]*)\.", value) if isinstance(value, str) else None
        if stage is None and found:
            stage = found.group(1)
    if kind == "verdict.recorded":
        made = {report_block.DESIGN: steps.VERIFIES,
                report_block.BUILD: steps.VERIFY_BUILD,
                report_block.STEP: event.get("step"), report_block.MAJORS: steps.RATIFY,
                report_block.RETRO: steps.RETRO}.get(event.get("kind"))
        return made, None if made in (steps.VERIFIES, steps.RATIFY, steps.RETRO) else stage
    if kind == "ruling.recorded":
        rules = event.get("rules")
        if isinstance(rules, str) and decisions.KEY.fullmatch(rules):
            return steps.RATIFY, None
        return steps.QUESTIONS, None
    if kind == checking.RAN:
        step = event.get("step")
        return (steps.CHECK if step in (None, checking.CHECK, ci.LOCAL) else step), stage
    if kind == "brief.sized":
        return (steps.VERIFY_BUILD if event.get("verify") else steps.BUILD), stage
    if kind == "wait.marked" and isinstance(event.get("id"), str) \
            and not is_job(event["id"]):
        return CLOSE, None
    made = EVENT_STEPS.get(str(kind))
    if made in (CLOSE, steps.DESIGN, steps.VERIFIES, steps.QUESTIONS, None):
        return made, None
    return made, stage


def _event(event: Mapping) -> dict:
    return {"n": event.get("n"), "at": event.get("at"), "by": event.get("actor"),
            "type": event.get("type"), "of": showing.history_of(dict(event))}


def events_of(part_: Part, column: str | None, stage: str | None) -> list[dict]:
    """The events of the step, oldest first; with `column` None, every event."""
    return [_event(one) for one in part_.state.history
            if column is None or event_step(one) == (column, stage)]


# -- records -----------------------------------------------------------------------------

STEP, CARD, QUESTION, DECISION, WAIT, SHOWN = (
    "step", "card", "question", "decision", "wait", "shown")
#: What a record can be of.
WHATS = (STEP, CARD, QUESTION, DECISION, WAIT, SHOWN)


def _by_severity(listed: Iterable[Mapping]) -> dict:
    counted = {one: 0 for one in report_block.SEVERITIES}
    for one in listed:
        counted[one.get("severity")] = counted.get(one.get("severity"), 0) + 1
    return counted


def _facts(part_: Part, column: str, stage: str | None,
           now: Mapping | None) -> list[dict]:
    state = part_.state
    facts: list[list] = []
    if column == steps.DESIGN:
        opened = next((one for one in state.history if one.get("type") == "round.opened"),
                      {})
        facts += [["description", state.description],
                  ["versions", ", ".join(f"v{one.n}" + (" (records only)"
                                                         if one.records_only else "")
                                         for one in state.versions) or "none"],
                  ["standing", str(versions.standing(state))], ["flow", state.flow],
                  ["opened", opened.get("at")]]
    elif column == steps.COMMISSION and stage is not None:
        build = builds.current(state, stage)
        if build is not None:
            said = checking.commission_of(state, build)
            facts += [["builder", said.get("builder")], ["host", said.get("host")],
                      ["branch", build.branch], ["target", build.target],
                      ["worktree", said.get("worktree")],
                      ["commissioned", said.get("at")]]
        else:
            planned = stages.find(state, stage)
            held = holds.on_stage(state, stage, on_closed=part_.found.on_closed)
            facts += [["why", None if planned is None else planned.why],
                      ["after", ", ".join(planned.after) if planned else None],
                      ["held by", ", ".join(one.id for one in held) or None],
                      ["child round", None if planned is None else planned.round]]
    elif column == steps.BUILD and stage is not None:
        read = (now or {}).get(stage)
        data = {} if read is None else read.data()
        facts += [["state", data.get("state")], ["why", data.get("why")],
                  ["head", data.get("head")], ["idle", data.get("idle")],
                  ["shares", ", ".join(f"{one['round']} {one['stage']}"
                                       for one in data.get("shares", ())) or None],
                  ["problem", (data.get("problem") or {}).get("reason")]]
    return [{"key": key, "value": _fact_word(value)} for key, value in facts
            if value is not None]


def _fact_word(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _verdicts(part_: Part, column: str, stage: str | None) -> list[dict]:
    state = part_.state
    if column == steps.VERIFIES:
        return [{"version": one.design, "verdict": one.verdict, "pass": one.pass_,
                 "findings": _by_severity(one.findings or ())}
                for one in versions.design_verdicts(state)]
    build = None if stage is None else builds.current(state, stage)
    if column != steps.VERIFY_BUILD or build is None:
        return []
    newest = showing.build_verdict(state, build)
    if newest is None:
        return []
    listed = [one.data() for one in findings.of_build(state, build)]
    return [{**{key: newest[key] for key in ("verdict", "pass", "at", "by", "model",
                                             "report")},
             "findings": _by_severity(listed),
             "open": sum(one["state"] == report_block.OPEN for one in listed)}]


def _findings(part_: Part, column: str, stage: str | None) -> dict | None:
    build = None if stage is None else builds.current(part_.state, stage)
    if column != steps.VERIFY_BUILD or build is None:
        return None
    listed = [one.data() for one in findings.of_build(part_.state, build)]
    open_ = sorted((one for one in listed if one["state"] == report_block.OPEN),
                   key=lambda one: report_block.SEVERITIES.index(one["severity"])
                   if one["severity"] in report_block.SEVERITIES else 9)
    return {"open": open_, "rest": len(listed) - len(open_)}


_CI_ORDER = (ci.RED, ci.ABSENT, ci.PENDING, ci.GREEN)


def _ci(part_: Part, column: str, stage: str | None) -> dict | None:
    if column != steps.CHECK or stage is None:
        return None
    newest = ci.newest(part_.state, stage)
    if newest is None:
        return None
    jobs = []
    for flow in newest.get("workflows") or ():
        for job in flow.get("jobs") or [{"name": flow.get("path"),
                                         "reading": flow.get("reading"),
                                         "url": flow.get("url")}]:
            jobs.append({**job, "workflow": flow.get("path")})
    jobs.sort(key=lambda one: _CI_ORDER.index(one.get("reading"))
              if one.get("reading") in _CI_ORDER else len(_CI_ORDER))
    return {"commit": newest.get("commit"), "at": newest.get("at"),
            "reading": newest.get("reading"), "jobs": jobs}


def _runs(part_: Part, column: str, stage: str | None) -> list[dict]:
    build = None if stage is None else builds.current(part_.state, stage)
    if build is None or column == steps.FINISH:
        return []
    state = part_.state
    names = [checking.CHECK, *showing.command_steps(state), ci.LOCAL]
    if column != steps.CHECK:
        names = [column] if column in names else []
    later = [one.n for one in builds.builds(state)
             if one.stage == build.stage and one.n > build.n]
    end = min(later) if later else None
    return [{key: event.get(key) for key in ("step", "exit_code", "duration_s", "commit",
                                            "at", "output")}
            for event in state.history
            if event.get("type") == checking.RAN and event.get("stage") == stage
            and event["n"] > build.n and (end is None or event["n"] < end)
            and (event.get("step") or checking.CHECK) in names]


def _drift(part_: Part, column: str, stage: str | None) -> dict | None:
    seen = None if stage is None else (part_.seen or {}).get(stage)
    moved = None if seen is None else seen.drift
    if column != steps.VERIFY_BUILD or moved is None:
        return None
    return {"why": moved.why, "owed": moved.owed, "moved": moved.moved,
            "meeting": list(moved.meeting), "shared": list(moved.shared)}


def _gates(part_: Part, step: steps.Step | None, now: Mapping | None) -> dict | None:
    if step is None or step.step != steps.FINISH or step.stage is None:
        return None
    unmet = [{"number": number, "reason": wait.reason, "fix": wait.fix}
             for number, wait in zip(step.gates, step.waits, strict=False)]
    build = _build(part_, step.stage)
    read = (now or {}).get(step.stage)
    head = None if read is None or read.status is None else read.status.head
    actions = [] if build is None else showing.finish_actions_of(part_.state, build, head)
    return {"unmet": unmet, "actions": actions}


def _decisions(part_: Part, stage: str | None) -> dict | None:
    if stage is None:
        return None
    listed = [one for one in decisions.of_round(part_.state) if one.stage == stage]
    return {"n": len(listed),
            "majors": sum(one.weight == report_block.MAJOR for one in listed),
            "unruled": sum(one.state == decisions.OPEN for one in listed)}


def _next(part_: Part, step: steps.Step | None) -> dict | None:
    """The `next` group of the step, its lines filled now for this one step, with no
    numbers of a retrospective."""
    if step is None:
        return None
    if not any((one["step"], one["stage"]) == (step.step, step.stage)
               for one in part_.groups):
        return None
    made = next_step.group(part_.found, step, part_.models, part_.seen,
                           count_numbers=False)
    return {"state": made["state"], "by": by_of(step), "who": _who(part_, step),
            "waits": made["waits"], "then": made["then"], "reads": made["reads"],
            "write": made["write"], "questions": made["questions"]}


def record(part_: Part, column: str, stage: str | None, items: Items, *,
           now: Mapping | None = None, settings=None) -> dict:
    """The record of the marker at `column` and `stage`: its head (the step, stage,
    round and the cell's state), and each section, empty or None when it has nothing.
    `now` holds the round's open builds as the newest gather read them, by stage."""
    step = part_.step(column, stage) if column != CLOSE else None
    # Only this marker's cell is made: a round of many stages has many cells.
    own = part_.step(column, None if column in (*ROUND_STEPS, *END_STEPS) else stage)
    found = (close_cell(part_, items) if column == CLOSE
             else None if own is None else cell(part_, own, items))
    state = part_.state
    made = {"round": part_.name, "column": column, "stage": stage,
            "state": None if found is None else found["state"],
            "word": None if found is None else found["word"],
            "next": _next(part_, step),
            "facts": _facts(part_, column, stage, now),
            "verdicts": _verdicts(part_, column, stage),
            "findings": _findings(part_, column, stage),
            "ci": _ci(part_, column, stage), "runs": _runs(part_, column, stage),
            "drift": _drift(part_, column, stage),
            "gates": _gates(part_, step, now),
            "decisions": _decisions(part_, stage) if column == steps.RATIFY or stage
            else None,
            "items": [], "settled": [], "close": [], "fixes": [],
            "events": events_of(part_, column, None if column in (*ROUND_STEPS, *END_STEPS)
                                else stage)}
    if column == steps.QUESTIONS:
        made["items"] = [*items.questions, *items.not_shown]
        made["settled"] = [
            {"id": one.id, "text": one.text, "chose": one.chose, "ruling": one.ruling}
            for one in state.questions
            if questions.current(one, state, on_closed=part_.found.on_closed)
            == questions.RULED]
    elif column == steps.RATIFY:
        made["items"] = list(items.decisions)
    elif column == CLOSE:
        made["items"] = list(items.jobs)
        ratify = (closing.RATIFY_DEFAULT if settings is None
                  else closing.ratify_of(settings, part_.name))
        # The part's steps were read with this `seen`, which holds the ratify setting
        # wherever the flow has a step that reads it, so the close reads them again
        # only for a part read without the settings.
        every = (part_.every if None in part_.seen or not flows.has(state.steps, steps.RATIFY)
                 else None)
        made["close"] = list(closing.waits(state, on_closed=part_.found.on_closed,
                                           ratify=ratify, seen=part_.seen, every=every))
        made["fixes"] = [{"id": one.key, "since": one.at} for one in fixes_owed(state)]
    return made


def answer_line(what: str, name: str, entry: Mapping) -> str:
    """The command that records an answer today, with `PTR` for the pointer."""
    if what == QUESTION:
        how = "--chose K" if entry.get("options") else "--text TEXT"
        return f"moxi rounds rule {name} {entry['id']} {how} --record PTR"
    if what == DECISION:
        return decisions.rule_line(name, entry["id"])
    if what == WAIT:
        return waits.mark_line(name, entry["id"])
    return f"moxi rounds mark {name} {entry['id']} --shown --record PTR"


def item_record(what: str, entry: Mapping, *, round_dir: Path | None,
                max_chars: int) -> dict:
    """The record of one waiting item: the item as what waits on him has it, the
    command that records an answer, and for a question its passage."""
    holder = entry.get("holder") or entry.get("round")
    made = {"what": what, "item": dict(entry), "answer": answer_line(what, holder, entry),
            "passage": None}
    if what == QUESTION and round_dir is not None:
        made["passage"] = passage(round_dir, entry.get("from"), entry["id"],
                                  text=entry.get("text"), max_chars=max_chars)
    return made


def card(part_: Part, items: Items, found: Mapping | None = None) -> dict:
    """A round's card: what it is for, its project, flow, when it opened, its
    standing, its stages counted, its rulings counted and the three newest, its phrase
    and its events; for a round that has ended, how and when, and how each stage
    ended."""
    state = part_.state
    found = cells(part_, items) if found is None else found
    plan = stages.in_plan(state)
    finished = questions.finished_stages(state)
    building = [one.id for one in plan if stages.build_of(state, one.id) == stages.BUILDING]
    opened = next((one for one in state.history if one.get("type") == "round.opened"), {})
    made = {"name": part_.name, "project": part_.found.project,
            "description": state.description, "flow": state.flow,
            "opened": opened.get("at"), "standing": str(versions.standing(state)),
            "stages": {"finished": sum(one.id in finished for one in plan),
                       "building": len(building),
                       "not_started": sum(one.id not in finished and one.id not in building
                                          for one in plan)},
            "rulings": len(state.rulings),
            "newest_rulings": [{"id": one.id, "text": one.text or one.rules,
                                "at": one.at} for one in list(state.rulings)[-3:][::-1]],
            "phrase": phrase(part_, found, items),
            "events": events_of(part_, None, None), "ended": None, "ends": []}
    if state.ended is not None:
        made["ended"] = {"how": state.ended.how, "at": state.ended.at}
        left = questions.left_stages(state)
        dropped = {one.stage for one in builds.builds(state)
                   if one.ended == builds.DROPPED}
        made["ends"] = [{"stage": one.id,
                         "how": ("landed" if one.id in finished else
                                 "left the plan" if one.id in left else
                                 "dropped" if one.id in dropped else "not built")}
                        for one in state.stages]
    return made


# -- a question's passage ----------------------------------------------------------------

_FRONT = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_BLOCK = re.compile(r"^```toml rounds\n.*?^```\n?", re.DOTALL | re.MULTILINE)


def passage(round_dir: Path | str, pointer: str | None, question_id: str, *,
            text: str | None = None, max_chars: int) -> str | None:
    """The paragraphs of the file a question's `file:` pointer names that mention its
    id, outside the front matter and the data block, the one quoting its `text` first,
    cut to `max_chars`. None where the pointer names no file of the round's folder,
    the file is not there, or no paragraph mentions the id."""
    if not isinstance(pointer, str) or pointers.shape(pointer) != pointers.FILE:
        return None
    relative = pointer[len("file:"):].partition("#")[0]
    root = Path(round_dir).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        return None
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    body = _BLOCK.sub("", _FRONT.sub("", body))
    mention = re.compile(rf"(?<![A-Za-z0-9]){re.escape(question_id)}(?![0-9])")
    found = [one.strip() for one in re.split(r"\n\s*\n", body) if mention.search(one)]
    if not found:
        return None
    if text:
        found.sort(key=lambda one: text not in one)
    joined = "\n\n".join(found)
    return joined if len(joined) <= max_chars else joined[:max_chars - 1] + "…"


__all__ = ["ROUND_PART", "STAGE_PART", "END_PART", "CLOSE", "STATES", "YOURS", "NONE",
           "DONE", "FAILED", "OWED", "WAITING", "CHILD", "TODO", "BY", "GROUPS", "STUCK",
           "WRAPPING", "MOVING", "Part", "part", "step_of", "Items", "items_of", "is_job",
           "fixes_owed", "columns", "cell", "close_cell", "cells", "lead_stage", "lines",
           "group", "phrase", "round_value", "ordered", "acts", "ACT_WORDS", "act_words",
           "strip", "tenth_act", "EVENT_STEPS", "event_step", "events_of", "record",
           "item_record", "answer_line", "card", "passage", "by_of", "design_moved", "last_at", "rev",
           "WHATS"]
