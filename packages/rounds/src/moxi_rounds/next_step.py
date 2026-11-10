"""What is done next in a round: the step it is at, who does it, at which tier, what
they read, the command that completes it and what it waits for.

`read(name)` reads the round's log and its project's and writes nothing, the index
included. Nothing here decides a step's state or which pass is asked for: the state is
what `steps` gives, and the pass what `brake` gives. This module only says them.

What is read is one dict (`read`), printed as lines (`as_text`) or as JSON
(`as_json`); the lines are made from the dict and hold nothing it lacks.

- `groups`: one for the step the round is at, then one for each stage of the plan
  whose own first step that is not done is another step. A group holds the step, its
  stage and state, `by`, `tier` and `model`, `reads`, `write`, `then` (each a command
  that completes the step), `waits` (each with the command that meets it; the lines
  leave out the one `instead` already says, and say `waits: nothing` only for a built
  step that is not waiting), and for the
  step that verifies the design the `pass` asked for, `why`, the two frozen `files` a
  re-check compares, and `instead`, what is done in place of a pass.
- `close`: for an open round, `waits`, what its close waits for (`closing.waits`). It
  is printed for a round at no step, where nothing else says why it is still open.
- `warnings`: what the lead should know and no step waits for.

A step with no row in `steps.ROWS` is printed as not built in this version, with the
holds on its stage. **A later step's lines** are a row of `LINES`: the step's name ->
a function `(found, step) -> dict` that gives the group's `reads`, `write` and `then`,
and any key of its own; the head of the group and its waits are filled in for it.

A tier's model is what the `models` setting names for the tier, as `model_of` gives
it: the one name where the tier has one harness or every harness names the same
model, else every harness with its model in the order the setting writes them, since
nothing recorded says which harness the step runs under. A tier the setting does not
name is printed alone. This holds wherever a tier is printed: a step's own, and the
tier a re-check is held to.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from . import (brake, closing, model_tiers, report_block, showing, stages, steps,
               tracing, versions)
from . import settings as own
from . import shown as showings
from . import state as states
from .errors import BadArgument

#: Who does a step that takes no `by`: the questions and the ratifying are his.
OPERATOR = "operator"
_HIS = (steps.QUESTIONS, "ratify")

#: The kind of prepared block that has no schema in this version.
ANSWER = "answer"
SCHEMAS = (*report_block.KINDS, ANSWER)

#: The kinds of warning.
DESIGN_MOVED = "design-changed"
BELOW_TIER = model_tiers.BELOW_TIER
NOT_SHOWN = "not-shown"
STALE_ROW = "stale-row"
HOLDS_STAGE = "holds-stage"
READ_BACK_UNKNOWN = "read-back-unknown"


# -- the schema --------------------------------------------------------------------------

def schema(kind: str) -> str:
    """The empty data block of `kind`, one of `SCHEMAS`. Raises `BadArgument` for a
    word that is no kind, and for the prepared answer, whose block this version does
    not have."""
    if kind == ANSWER:
        raise BadArgument("the block of a prepared answer is not built in this version",
                          f"name one of {', '.join(report_block.KINDS)}")
    return report_block.empty(kind)


# -- the model of a tier -----------------------------------------------------------------

def model_of(models, tier: str | None) -> str | None:
    """What the table `models` names for `tier`, as it is printed beside the tier:
    the one model's name when the tier has one harness or each harness names the same
    model, else `harness: model` for every harness, in the order the table writes
    them. None when the table names no model for the tier."""
    named = models.get(tier) if isinstance(models, dict) and tier else None
    if isinstance(named, str):
        return named or None
    if not isinstance(named, dict):
        return None
    each = [(harness, name) for harness, name in named.items()
            if isinstance(name, str) and name]
    if len({name for _harness, name in each}) <= 1:
        return each[0][1] if each else None
    return ", ".join(f"{harness}: {name}" for harness, name in each)


# -- each step's lines -------------------------------------------------------------------

def _named(step: steps.Step) -> list[str]:
    reads = (step.entry or {}).get("reads") or ()
    return [str(line) for line in ([reads] if isinstance(reads, str) else reads)]


def _design(found: showing.Shown, step: steps.Step) -> dict:
    return {"reads": _named(step),
            "write": f"{versions.DESIGN_FILE} in {found.folder}",
            "then": [f"moxi rounds fold {found.name}"]}


def _verify_design(found: showing.Shown, step: steps.Step) -> dict:
    asked = step.asked
    said = {"pass": asked.pass_, "why": asked.why, "version": asked.version,
            "since": asked.since, "instead": list(asked.instead), "tier": asked.tier}
    if asked.pass_ is None or asked.instead:
        return said
    pair = brake.files(found.folder, asked)
    if pair is None:
        reads = [str(versions.file(found.folder, asked.version))]
    else:
        said["files"] = [str(one) for one in pair]
        reads = [f"the diff from {pair[0]} to {pair[1]}, and the sections that "
                 "depend on it"]
    return {**said, "reads": reads + _named(step),
            "write": (f"a report in {found.folder} with the block from: moxi rounds "
                      f"next --schema {report_block.DESIGN}"),
            "then": [f"moxi rounds verdict {found.name} <your report>"]}


def _docket(found: showing.Shown, step: steps.Step) -> dict:
    asked = {one.id: one for one in found.state.questions}
    return {"export": f"moxi rounds list {found.name} --questions",
            "then": [steps.answer(found.name, asked[question_id])
                     for question_id in step.questions]}


#: Each step's own lines: the step's name -> what gives the group's `reads`, `write`
#: and `then`, and any key of its own.
LINES: dict[str, Callable[[showing.Shown, steps.Step], dict]] = {
    steps.DESIGN: _design, steps.VERIFIES: _verify_design, steps.QUESTIONS: _docket}


def _waits(step: steps.Step) -> list[dict]:
    return [{"reason": one.reason, "fix": one.fix} for one in step.waits]


def _told(group: dict) -> dict | None:
    """The wait of a group that its `pass` and `instead` already say line by line:
    what the brake says to do in place of a pass. None where it has none."""
    if not group.get("instead"):
        return None
    return {"reason": group["why"], "fix": "; ".join(group["instead"])}


def _group(found: showing.Shown, step: steps.Step, models) -> dict:
    entry = step.entry or {}
    group = {"step": step.step, "stage": step.stage, "state": step.state,
             "built": step.state != steps.NOT_BUILT,
             "by": OPERATOR if step.step in _HIS else entry.get("by"),
             "tier": entry.get("model"), "model": None, "reads": [], "write": None,
             "then": [], "questions": list(step.questions), "waits": _waits(step)}
    lines = LINES.get(step.step)
    if group["built"] and lines is not None:
        group.update(lines(found, step))
    group["model"] = model_of(models, group["tier"])
    return group


def _groups(found: showing.Shown, models) -> list[dict]:
    every = steps.of(found.state, name=found.name, closed=found.closed)
    at = steps.current(found.state, name=found.name, closed=found.closed)
    if at is None:
        return []
    groups = [_group(found, at, models)]
    for stage in stages.in_plan(found.state):
        first = next((step for step in every
                      if step.stage == stage.id and not step.settled), None)
        if first is not None and (first.step, first.stage) != (at.step, at.stage):
            groups.append(_group(found, first, models))
    return groups


# -- the warnings ------------------------------------------------------------------------

def _warning(what: str, text: str, fix: str | None = None) -> dict:
    return {"what": what, "text": text, "fix": fix}


def _warnings(found: showing.Shown) -> list[dict]:
    state, name = found.state, found.name
    fold = f"moxi rounds fold {name}"
    warnings = []
    if versions.current(state) is not None:
        moved = versions.unchanged(state, found.folder, name)
        if moved is not None:
            warnings.append(_warning(DESIGN_MOVED, moved.reason, moved.fix))
    read = versions.design_verdicts(state)
    if read and BELOW_TIER in read[-1].flags:
        last = read[-1]
        warnings.append(_warning(
            BELOW_TIER, f"the newest report of the design, of v{last.design}, says "
                        f"{last.model} wrote it, a model below the tier its pass is "
                        "held to"))
    if found.register is None:
        warnings.append(_warning(
            READ_BACK_UNKNOWN, "what he has read back is not known: "
                               f"{found.register_unreadable.reason}"))
    else:
        unseen = [one for one in state.rulings
                  if not showings.is_shown(one, found.register)]
        if unseen:
            said = "; ".join(f"{name} {one.id} {one.rules if one.text is None else one.text}"
                             for one in unseen)
            ids = ",".join(one.id for one in unseen)
            warnings.append(_warning(
                NOT_SHOWN, f"not yet shown to the operator: {said}",
                f"moxi rounds mark {name} {ids} --shown --record PTR"))
    for one in tracing.stale(state, found.register):
        warnings.append(_warning(
            STALE_ROW, "the design's row cites a ruling that stands no longer: "
                       f"{tracing.stale_line(tracing.stale_data(one))}",
            f"give {versions.DESIGN_FILE} the row of the ruling that stands, then "
            f"{fold}"))
    for stage in stages.in_plan(state):
        if stages.build_of(state, stage.id) is not None:
            continue
        for one in tracing.holding(state, stage.id):
            warnings.append(_warning(
                HOLDS_STAGE, f"{one.id} holds {stage.id} until a version of the "
                             f"design has a row for it: {one.text}",
                f"give {versions.DESIGN_FILE} a row for {one.id}, then {fold}"))
    return warnings


# -- the reading -------------------------------------------------------------------------

def read(name: str, *, settings=None) -> dict:
    """What is next in the round `name`, `<project>/<slug>`, as data. `settings` is
    what `settings.load()` gives, read now when left out. Nothing is written.

    Raises `BadArgument` for no name and for a project's key, and what
    `showing.show` raises for a round that can't be found. A log that can't be read
    raises nothing: the round is given as unreadable."""
    if not isinstance(name, str) or not name.strip():
        raise BadArgument("nothing says which round's next step to print",
                          "name a round: moxi rounds next <project>/<slug>")
    if "/" not in name:
        raise BadArgument(f"{name} is a project, and a step is of one round",
                          f"name a round, <project>/<slug>; moxi rounds list {name} "
                          "lists the project's")
    settings = own.load() if settings is None else settings
    found = showing.show(name, settings=settings, refresh=False)
    said = {"round": found.name, "folder": str(found.folder), "notice": found.notice}
    if found.unreadable is not None:
        error = found.unreadable
        return {**said, "unreadable": {"file": str(error.file), "line": error.line,
                                       "problem": error.problem, "reason": error.reason,
                                       "fix": error.fix}}
    return {**said, "status": found.state.status, "flow": found.state.flow,
            "groups": _groups(found, settings["models"]),
            "close": (None if found.state.status != states.OPEN
                      else {"waits": list(closing.waits(found.state))}),
            "warnings": _warnings(found)}


# -- as lines ----------------------------------------------------------------------------

def _head(said: dict, group: dict) -> str:
    parts = [f"round {said['round']}", f"flow {said['flow']}"]
    if group["stage"] is not None:
        parts.append(f"stage {group['stage']}")
    parts.append(f"step {group['step']}")
    if not group["built"]:
        return "   ".join([*parts, "not built in this version"])
    who = []
    if group["by"] is not None:
        who.append(f"by {group['by']}")
    if group["tier"] is not None:
        model = "" if group["model"] is None else f" ({group['model']})"
        who.append(f"tier {group['tier']}{model}")
    if who:
        parts.append(", ".join(who))
    return "   ".join(parts)


def _pass_line(group: dict) -> str:
    if group["pass"] is None:
        return f"pass:  none: {group['why']}"
    if group["version"] is None:
        return f"pass:  {group['pass']}: {group['why']}"
    return f"pass:  {group['pass']} of v{group['version']}: {group['why']}"


def _group_lines(said: dict, group: dict) -> list[str]:
    lines = [_head(said, group)]
    if group["built"]:
        if "pass" in group:
            lines.append(_pass_line(group))
            lines += [f"do:    {line}" for line in group["instead"]]
        if group["questions"]:
            lines.append(f"questions: {', '.join(group['questions'])}")
        if group.get("export") is not None:
            lines.append(f"export: {group['export']}")
        if group["write"] is not None or group["reads"]:
            lines.append(f"reads: {'; '.join(group['reads']) or 'nothing named'}")
        if group["write"] is not None:
            lines.append(f"write: {group['write']}")
        lines += [f"then:  {line}" for line in group["then"]]
    told = _told(group)
    for wait in group["waits"]:
        if wait != told:
            lines += [f"waits: {wait['reason']}", f"  fix: {wait['fix']}"]
    # Only a step known to wait for nothing says so: a step that waits has said what
    # for above, and what a step that is not built waits for is not known.
    if not group["waits"] and group["built"] and group["state"] != steps.WAITING:
        lines.append("waits: nothing")
    return lines


def as_text(said: dict) -> str:
    """What `read` gave, as lines, ending in a line break."""
    if "unreadable" in said:
        lines = [f"round {said['round']}   unreadable", f"folder: {said['folder']}",
                 said["unreadable"]["reason"]]
        if said["unreadable"]["fix"]:
            lines.append(f"  fix: {said['unreadable']['fix']}")
    elif not said["groups"]:
        if said["status"] == states.OPEN:
            rest = "step none: every step of the flow is done or skipped"
        else:
            rest = f"step none: the round is {said['status']}"
        lines = [f"round {said['round']}   flow {said['flow']}   {rest}"]
        if said["close"] is not None:
            # Nothing is left to do at a step, so what keeps the round open is said.
            lines.append(closing.said(said["close"]["waits"]))
    else:
        lines = []
        for group in said["groups"]:
            lines += [*([""] if lines else []), *_group_lines(said, group)]
    for warning in said.get("warnings", ()):
        lines.append(f"warning: {warning['text']}")
        if warning["fix"] is not None:
            lines.append(f"  fix: {warning['fix']}")
    if said["notice"] is not None:
        lines.append(said["notice"])
    return "\n".join(lines) + "\n"


def as_json(said: dict) -> str:
    """What `read` gave, as JSON text, ending in a line break."""
    return json.dumps(said, indent=2, ensure_ascii=False) + "\n"


__all__ = ["ANSWER", "SCHEMAS", "LINES", "OPERATOR", "schema", "model_of", "read",
           "as_text", "as_json", "DESIGN_MOVED", "BELOW_TIER", "NOT_SHOWN", "STALE_ROW",
           "HOLDS_STAGE", "READ_BACK_UNKNOWN"]
