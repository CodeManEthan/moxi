"""Freezing a design: `fold`, which makes the design's file a numbered version and adds
what its block declares to the round's record.

`freeze("<project>/<slug>", actor=...)` reads the data block of `design.md` in the
round's folder, copies the file to `versions/design-v<N>.md` and appends one event,
`design.frozen`. That event is the whole of the freezing: it holds the version's number,
the SHA-256 of the file, what the block declared, what of that is new to the record,
and what the block took away. The fold of the log adds the new part from it, so a verb
stopped right after its append has left nothing half done.

**A freezing adds, and takes nothing away.** Added at once: a question the record does
not have (open, asked by this freezing, its origin the frozen file), a stage it does
not have, a stage another now waits for, and a question's wider hold. Recorded as
pending, and changing nothing: a question an earlier version carried and this block
leaves out, a recorded stage it leaves out, a stage another waits for no longer, and a
question that holds up fewer stages in the block than on record. `plan` is the function
that says which is which.

Three things a block may not say of a stage, each refused: a recorded stage's id with
another repo, branch or child round; the id of a stage that left the plan; and, in a
round whose flow does not verify its design, a second stage in one repo.

Options are no declaration. A block that gives a question options adds the keys the
question does not have and retires each key it leaves out; a block that gives it none
changes none. A key that was retired is never used again: the block's entry for it is
left out, the freezing says which key that was, and the rest of the block is read.

The copy is the verb's act outside the log, done before the append. A verb stopped
between the two is run again: the file it left is kept when it has the bytes of the
design, and written again when the design changed meanwhile, since no event had made
it a version.

**A change of records only.** A version may be frozen as one, with a pointer to the
record of the change: no pass is asked for over it, and the design stands at what the
version before it gave (`versions.standing`). It is one only when what the gates read
is as the version before declared it; `brake.differences` says what is not, and each
difference refuses the freezing. Options are no such difference.

A sentence in the block that differs from the one on record for the same id changes
nothing: a question on record keeps its sentence, and the freezing says which ids
differ.

**The trace check.** Where the round's project asks for it, the version's trace rows
are checked as it is frozen (`tracing.check`) and the event holds what was found;
where it does not, the event says the check was not asked for. A version that fails
is frozen all the same: the result is a record, and what waits on it reads it there.
The setting is read at the freezing, so a change to it reaches the next version and
no version already frozen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from moxi import files

from . import (brake, design_block, ending, folders, options, pointers, questions,
               stages, stepping, tracing, versions, write)
from . import settings as own
from . import state as states
from .errors import BadArgument, BadBlock, NoLog, NoRound, Problem, RoundsError, Unmet

#: The step a round's flow must have for its design to be frozen.
DESIGN = "design"

#: The states in which a question is still asked, so that its record can change.
_ASKED = (questions.OPEN, questions.PARKED)


@dataclass(frozen=True)
class Plan:
    """What freezing a block would do to a record.

    - `declared`: the block as the event holds it.
    - `added`: what is new to the record, as the event holds it: `questions` and
      `stages` (ids), `after` (stage id -> the stages it now also waits for), `blocks`
      (question id -> what it now holds up) and `options` (question id -> `options`
      added and keys `retired`); a key is there only when it has something.
    - `pending`: what the block took away.
    - `problems`: why the block can't be frozen onto this record; with any, nothing
      is frozen.
    - `refused`: each `(question id, key)` the block lists that was retired from the
      question and is not used again.
    - `reworded`: the ids of the questions whose sentence in the block is not the one
      on record, which the record keeps.
    """

    declared: dict
    added: dict
    pending: tuple[states.Pending, ...]
    problems: tuple[Problem, ...] = ()
    refused: tuple[tuple[str, str], ...] = ()
    reworded: tuple[str, ...] = ()


#: More checks of a block against the record, each `check(state, block) -> problems`:
#: what a block may not say of a stage, and whatever a later check adds here.
CHECKS: list[Callable[[states.State, design_block.DesignBlock], list[Problem]]] = [
    *stages.CHECKS]


@dataclass(frozen=True)
class Frozen:
    """A design as it was frozen: the round's `name`, the `version`'s number, the
    frozen `file` and its `digest`, the `plan` that was carried out, the `event`
    written, the design's `standing` after it, and `trace`, what the check of its
    trace rows found, as `tracing.shown` gives it. `failed` pairs each after-append
    callback that raised with what it raised."""

    name: str
    version: int
    file: Path
    digest: str
    plan: Plan
    event: dict
    standing: versions.Standing | None = None
    trace: dict | None = None
    failed: tuple[tuple[Callable, Exception], ...] = ()


# -- what a block declares ---------------------------------------------------------------

def _declared(block: design_block.DesignBlock) -> dict:
    """The block as the event holds it: every entry, with what the block left out
    filled in (a question that does not say holds up every stage)."""
    asked = []
    for one in block.questions:
        entry: dict[str, object] = {"id": one.id, "text": one.text,
                                    "blocks": questions.stored(one.blocks)}
        if one.options is not None:
            entry["options"] = dict(one.options)
        asked.append(entry)
    planned = []
    for stage in block.stages:
        entry = {"id": stage.id, "after": list(stage.after)}
        if stage.round is not None:
            entry["round"] = stage.round
        else:
            entry.update(repo=stage.repo, branch=stage.branch,
                         slices=[{"id": piece.id, "what": piece.what,
                                  "minutes": piece.minutes} for piece in stage.slices])
            if stage.why:
                entry["why"] = stage.why
        planned.append(entry)
    traced = []
    for row in block.traces:
        entry = {"id": row.id, "as": row.how, "where": row.where, "record": row.record,
                 "stages": list(row.stages)}
        if row.question is not None:
            entry["question"] = row.question
        traced.append(entry)
    committed = [{"id": one.id, "text": one.text, "where": one.where}
                 for one in block.commitments]
    return {"question": asked, "stage": planned, "trace": traced,
            "commitment": committed}


def _same(one, other) -> bool:
    """Whether two values of `blocks` hold up the same stages."""
    if isinstance(one, str) or isinstance(other, str):
        return one == other
    return set(one) == set(other)


def _taken_off(have, asked, known) -> tuple[str, ...]:
    """The stages a block's entry takes off a hold: of the ones the hold `have` covers
    among the stages `known` now, those the entry `asked` does not name."""
    held = dict.fromkeys(known) if have == questions.ALL else (
        () if isinstance(have, str) else have)
    keeps = () if isinstance(asked, str) else asked
    return tuple(one for one in held if one not in keeps)


def plan(state: states.State, block: design_block.DesignBlock) -> Plan:
    """What freezing `block` would do to the round whose log folded to `state`."""
    problems: list[Problem] = []
    added: dict[str, object] = {}
    pending: list[states.Pending] = []
    refused: list[tuple[str, str]] = []
    reworded: list[str] = []
    recorded = {stage.id: stage for stage in state.stages}
    known = set(recorded) | {stage.id for stage in block.stages}

    def add(kind: str, key: str | None, value) -> None:
        if key is None:
            added.setdefault(kind, []).append(value)
        else:
            added.setdefault(kind, {})[key] = value

    def unknown(entry, key: str, named, what: str) -> None:
        for stage_id in named:
            if stage_id not in known:
                problems.append(Problem(
                    entry.line_of(key),
                    f"{what} names {stage_id}, which is no stage of the round and "
                    "none this block declares",
                    f"declare {stage_id} as a [[stage]], or take it out of {key}"))

    for stage in block.stages:
        unknown(stage, "after", stage.after, f"the stage {stage.id}")
        have = recorded.get(stage.id)
        if have is None:
            add("stages", None, stage.id)
            continue
        more = [one for one in stage.after if one not in have.after]
        gone = tuple(one for one in have.after if one not in stage.after)
        if more:
            add("after", stage.id, more)
        if gone:
            pending.append(states.Pending(states.AFTER, stage.id, gone=gone))
    declared_now = {stage.id for stage in block.stages}
    done = questions.finished_stages(state) | questions.left_stages(state)
    pending += [states.Pending(states.STAGE, stage_id) for stage_id in recorded
                if stage_id not in declared_now and stage_id not in done]

    for asked in block.questions:
        if asked.prepared is not None:
            problems.append(Problem(
                asked.line_of("prepared"),
                f"{asked.id} carries a prepared answer, and an answer prepared in a "
                "design's block is not recorded yet",
                "take [question.prepared] and its options out of the block"))
        if not isinstance(asked.blocks, str):
            unknown(asked, "blocks", asked.blocks, f"the question {asked.id}")
        have = questions.find(state, asked.id)
        if have is None:
            add("questions", None, asked.id)
            continue
        if have.state not in _ASKED:
            by = f", by {have.ruling}" if have.ruling else ""
            problems.append(Problem(
                asked.line_of("id"),
                f"the round already used {asked.id}: it is {have.state}{by}, and what "
                "was said of it must not answer another question",
                f"take {asked.id} out of the block, or give a new question an id "
                "the round has not used"))
            continue
        if have.text != asked.text:
            reworded.append(asked.id)
        if not _same(asked.blocks, have.blocks):
            if questions.holds_every(asked.blocks, have.blocks):
                add("blocks", asked.id, questions.stored(asked.blocks))
            else:
                # The block's entry holds up fewer stages, which waits. Any stage it
                # names that the record does not is added all the same.
                if not isinstance(asked.blocks, str) and not isinstance(have.blocks, str):
                    wider = tuple(dict.fromkeys((*have.blocks, *asked.blocks)))
                    if set(wider) != set(have.blocks):
                        add("blocks", asked.id, list(wider))
                pending.append(states.Pending(
                    states.BLOCKS, asked.id, blocks=asked.blocks,
                    gone=_taken_off(have.blocks, asked.blocks,
                                    (*recorded, *(stage.id for stage in block.stages)))))
        if asked.options is not None:
            _options(asked, have, add, problems, refused)
    carried = {one.get("id") for version in state.versions
               for one in version.declared.get(states.QUESTION) or ()}
    asked_now = {asked.id for asked in block.questions}
    pending += [states.Pending(states.QUESTION, one.id) for one in state.questions
                if one.id in carried and one.id not in asked_now
                and one.state in _ASKED]

    for check in CHECKS:
        problems += check(state, block)
    return Plan(declared=_declared(block), added=added, pending=tuple(pending),
                problems=tuple(problems), refused=tuple(refused),
                reworded=tuple(reworded))


def _options(asked: design_block.Question, have: states.Question, add,
             problems: list[Problem], refused: list[tuple[str, str]]) -> None:
    """What the options the block gives `asked` change of the ones the question
    `have` offers: keys added, keys retired, and what stands in the way."""
    line = asked.line_of("options")
    new = {}
    for key, text in asked.options:
        if key in have.retired:
            refused.append((asked.id, key))
        elif options.text_of(have, key) is None:
            new[key] = text
        elif options.text_of(have, key) != text:
            problems.append(Problem(
                line, f"{asked.id} offers {options.text_of(have, key)!r} under {key}, "
                      "and a key keeps its text",
                f"leave {key} out to retire it and give the right line under a new "
                "key"))
    listed = {key for key, _text in asked.options}
    retired = [key for key, _text in have.options if key not in listed]
    left = len(have.options) + len(new) - len(retired)
    fix = (f"give {asked.id} {options.FEWEST} to {options.MOST} options, none of "
           "them under a retired key")
    wrong = options.how_many(left, fix)
    if wrong is not None:
        problems.append(Problem(line, f"{asked.id}: {wrong.reason}", fix))
    change: dict[str, object] = {}
    if new:
        change["options"] = new
    if retired:
        change["retired"] = retired
    if change:
        add("options", asked.id, change)


def _pending(one: states.Pending) -> dict:
    entry: dict[str, object] = {"what": one.what, "id": one.id}
    if one.gone:
        entry["gone"] = list(one.gone)
    if one.blocks is not None:
        entry["blocks"] = questions.stored(one.blocks)
    return entry


# -- the verb ----------------------------------------------------------------------------

@dataclass
class _Read:
    """The design as the verb read it under the lock: its `block`, or the `error` that
    stopped the reading, or neither when the round has no design file."""

    block: design_block.DesignBlock | None = None
    error: BadBlock | None = None
    plans: dict = field(default_factory=dict)


def freeze(name: str, *, actor: str, records_only: bool = False,
           record: str | None = None, settings=None, wait_s: float = write.WAIT_S,
           at: str | None = None) -> Frozen:
    """Freeze the design of the round `name`, `<project>/<slug>`, as its next version.

    - `actor`: who freezes it, the event's actor, and who asked each question the
      block adds.
    - `records_only`: the lead says this version changes records only, so no pass is
      asked for over it; `record` is then the pointer to the record of the change,
      and is given with nothing else.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the round's lock; `at`: the event's time.

    Raises `BadName` for a name that can't be used, `UnknownProject`, `NoFolder` or
    `NoRound` for a round that can't be found, `Refused` when the round has ended, its
    flow has no design step or its folder holds no design, and `BadBlock` when the
    design's block can't be read or can't be frozen onto the record: a question's id
    that the round already used for one since ruled or withdrawn, a stage named that
    is none, options that break a question's rules, a prepared answer, a recorded
    stage given another repo, branch or child round, a stage that left the plan, or a
    second stage in one repo where the flow does not verify the design. A version
    frozen as a change of records only is also `Refused` when its pointer is missing
    or has no known shape, and when its block's questions, its stages, what a
    question holds up or what a stage waits for are not as the version before
    declared them. A refusal leaves the log and the versions as they were.
    """
    if record is not None and not records_only:
        raise BadArgument("a pointer is given only with a version that changes "
                          "records only", "leave --record out, or add --records-only")
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    folder = home.round_folder(slug)
    design = folder / versions.DESIGN_FILE
    again = f"moxi rounds fold {name}"
    as_records = f"{again} --records-only --record PTR"

    def read(where) -> _Read:
        if not design.is_file():
            return _Read()
        try:
            return _Read(block=design_block.read(design))
        except BadBlock as error:
            return _Read(error=error)

    def planned(view) -> Plan:
        # One plan for one reading of the log: the gates, the act and the event all
        # speak of the same one.
        made = view.facts.plans
        if id(view) not in made:
            made[id(view)] = plan(view.state, view.facts.block)
        return made[id(view)]

    def has_a_design_step(view):
        if stepping.has_step(view.state, DESIGN):
            return None
        flow = stepping.current(view.state).name
        return Unmet(f"the flow {flow} of {name} has no {DESIGN} step, so the round "
                     "has no design to freeze; a flow that has one is the lead's to "
                     "move it to",
                     f"moxi rounds mark {name} --flow NAME")

    def has_a_design(view):
        if view.facts.block is not None or view.facts.error is not None:
            return None
        return Unmet(f"{name} has no design: there is no {design}; write it, with "
                     "its data block, and freeze it then", again)

    def has_a_pointer(view):
        if not records_only:
            return None
        if record is None:
            return Unmet("a version that changes records only says where the record "
                         f"of the change is; PTR below is one token: {pointers.SHAPES}",
                         as_records)
        return pointers.unmet(record, as_records,
                              names="the lead's record of the change")

    def changes_records_only(view):
        if not records_only or view.facts.block is None:
            return None
        before = versions.declarations(view.state)
        return [Unmet(f"this is no change of records only: {one}; a change to what "
                      "the gates read is frozen as a plain version, which a "
                      "re-check then reads", again)
                for one in brake.differences(before, planned(view).declared)]

    def freeze_file(view) -> tuple[int, Path]:
        if view.facts.error is not None:
            raise view.facts.error
        block = view.facts.block
        found = planned(view)
        if found.problems:
            raise BadBlock(block.file, design_block.WHAT, found.problems)
        number = len(view.state.versions) + 1
        target = versions.file(folder, number)
        try:
            data = design.read_bytes()
            if hashlib.sha256(data).hexdigest() != block.sha256:
                raise RoundsError(f"{design} changed while it was being frozen",
                                  f"run it again: {again}")
            # A file left by a freezing that was stopped is kept when it holds these
            # bytes. No event made it a version, so one that differs is written again.
            if not (target.is_file() and
                    hashlib.sha256(target.read_bytes()).hexdigest() == block.sha256):
                target.parent.mkdir(exist_ok=True)
                files.atomic_write(target, data, mode=0o644)
        except OSError as error:
            raise RoundsError(f"the design can't be frozen as {target}: {error}",
                              f"make the round's folder one that can be written to, "
                              f"then: {again}") from None
        return number, target

    def frozen(view, result):
        number, _target = result
        found = planned(view)
        # The check reads the record as this event leaves it: a question the block
        # adds is open from here on, so a row may name it.
        traced = (tracing.check(view.state, view.project_state, found.declared,
                                asked=found.added.get("questions", ()))
                  if own.round_setting(settings, home.project, "trace")
                  else tracing.NOT_ASKED)
        fields = {"version": number, "digest": view.facts.block.sha256,
                  "records_only": records_only, "trace": traced.stored(),
                  "declared": found.declared, "added": found.added,
                  "pending": [_pending(one) for one in found.pending]}
        if records_only:
            fields["record"] = record
        return write.Entry("design.frozen", fields)

    try:
        written = write.record(
            write.Where(home.project, home.folder, slug), actor=actor,
            gates=[ending.takes_new_work(name, "a new version of its design"),
                   has_a_design_step, has_a_design, has_a_pointer,
                   changes_records_only],
            act=freeze_file, event=frozen, facts=read, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    number, target = written.result
    after = states.fold(written.round_events)
    return Frozen(name=name, version=number, file=target,
                  digest=written.events[0]["digest"], plan=planned(written.view),
                  event=written.events[0],
                  standing=versions.standing(after),
                  trace=tracing.shown(after, written.view.project_state),
                  failed=written.failed)


__all__ = ["DESIGN", "CHECKS", "Plan", "Frozen", "plan", "freeze"]
