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

**It adds what the edit added, stage by stage.** Of the stages a question holds up
and the stages a stage waits for, a freezing adds the ones the block's list gained
since the version before; for a first version, and for an entry the version before
did not have, that is the whole list. A list that holds up every stage counts as the
stages its own block declares, and one that holds up none as no stage. So a stage
the list carries unchanged, which the record no longer has because a ruling took it
off, stays off: the ruling outlasts a block nobody edited there, however else the
entry changed. `Plan.kept_off` names each such stage with its ruling. Holding it
again is an act of its own, by the verbs that widen a hold or add a wait.

Three things a block may not say of a stage, each refused: a recorded stage's id with
another repo, branch or child round; the id of a stage that left the plan; and, in a
round whose flow does not verify its design, a second stage in one repo. And a wait is
added only while it can still be met: one for a stage that left the plan, or one that
closes a circle with the waits on record, is refused as the verb that adds a wait by
hand refuses it.

Options are no declaration. A block that gives a question options adds the keys the
question does not have and retires each key it leaves out; a block that gives it none
changes none. A key that was retired is never used again: the block's entry for it is
left out, the freezing says which key that was, and the rest of the block is read.

A prepared answer is no declaration either. A question's `[question.prepared]` is
recorded on the question as `ask --prepared` records one, and checked the same way
against the options the question offers once the block's own are applied; a question
that offers none takes them from the answer. One that is the newest on record and
current adds nothing.

The copy is the verb's act outside the log, done before the append. A verb stopped
between the two is run again: the file it left is kept when it has the bytes of the
design, and written again when the design changed meanwhile, since no event had made
it a version.

**A change of records only.** A version may be frozen as one, with a pointer to the
record of the change: no pass is asked for over it, and the design stands at what the
version before it gave (`versions.standing`). It is one only when what the gates read
is as the version before declared it; `brake.differences` says what is not, and each
difference refuses the freezing. Options and a prepared answer are no such
difference.

A sentence in the block that differs from the one on record for the same id changes
nothing: a question on record keeps its sentence, and the freezing says which ids
differ.

**The trace check.** Where the round's project asks for it, the version's trace rows
are checked as it is frozen (`tracing.check`) and the event holds what was found;
where it does not, the event says the check was not asked for. A version that fails
is frozen all the same: the result is a record, and what waits on it reads it there.
The setting is read at the freezing, so a change to it reaches the next version and
no version already frozen.

**Slices over the limit.** A freezing records each stage's slices as the block gives
them, and says which of the block's slices are given more minutes than the round's
project allows one (`slice_minutes`, read at the freezing): `Frozen.oversized`. That is
a warning and refuses nothing: whether each slice is one piece is the design pass's
judgment, and a brief for a slice over the limit is refused when it is sized.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from moxi import files

from . import (brake, corrections, design_block, ending, folders, options, pointers,
               preparing, questions, stages, stepping, tracing, versions, write)
from . import checklist
from . import settings as own
from . import state as states
from .errors import BadArgument, BadBlock, NoLog, NoRound, Problem, RoundsError, Unmet

#: The step a round's flow must have for its design to be frozen.
DESIGN = "design"

#: The states in which a question is still asked, so that its record can change.
_ASKED = (questions.OPEN, questions.PARKED)


@dataclass(frozen=True)
class KeptOff:
    """One stage a block lists that a freezing does not add back: `what` is
    `state.BLOCKS` (the question `id` does not hold up `stage` again) or `state.AFTER`
    (the stage `id` does not wait for `stage` again), and `ruling` the id of the
    ruling that took it off and stands, None when none is found."""

    what: str
    id: str
    stage: str
    ruling: str | None = None


@dataclass(frozen=True)
class Plan:
    """What freezing a block would do to a record.

    - `declared`: the block as the event holds it.
    - `added`: what is new to the record, as the event holds it: `questions` and
      `stages` (ids), `after` (stage id -> the stages it now also waits for), `blocks`
      (question id -> what it now holds up), `options` (question id -> `options`
      added and keys `retired`) and `prepared` (question id -> the answer the block
      prepares, as an event stores it); a key is there only when it has something.
    - `pending`: what the block took away.
    - `problems`: why the block can't be frozen onto this record; with any, nothing
      is frozen.
    - `refused`: each `(question id, key)` the block lists that was retired from the
      question and is not used again.
    - `reworded`: the ids of the questions whose sentence in the block is not the one
      on record, which the record keeps.
    - `kept_off`: each stage a list of the block carries from the version before
      that the record no longer has there, and that is not added back.
    """

    declared: dict
    added: dict
    pending: tuple[states.Pending, ...]
    problems: tuple[Problem, ...] = ()
    refused: tuple[tuple[str, str], ...] = ()
    reworded: tuple[str, ...] = ()
    kept_off: tuple[KeptOff, ...] = ()


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
    #: Each slice of the block over the limit, as `{"stage", "slice", "minutes"}`.
    oversized: tuple[dict, ...] = ()
    #: The limit of one slice's minutes, as the freezing read it.
    slice_minutes: int | None = None


def oversized(declared: dict, limit: int) -> tuple[dict, ...]:
    """Each slice of a block as an event declares it whose minutes are over `limit`,
    in the block's order."""
    return tuple({"stage": stage.get("id"), "slice": piece.get("id"),
                  "minutes": piece.get("minutes")}
                 for stage in declared.get("stage") or ()
                 for piece in stage.get("slices") or ()
                 if isinstance(piece.get("minutes"), int) and piece["minutes"] > limit)


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
            "commitment": committed, checklist.KEY: list(block.checklist or ())}


def mark_problems(block: design_block.DesignBlock, home: str, settings) -> list[Problem]:
    """What is wrong with the checklist marks of `block`, for a round whose home is
    `home`, against the checklists of the home project and of each repo its stages name:
    a mark of no item, of another project, or given twice, and a block that says nothing
    while one of those checklists is not empty."""
    projects = checklist.weighed(home, (stage.repo for stage in block.stages))
    if block.checklist is None:
        if any(checklist.items(settings, project) for project in projects):
            return [Problem(block.line, "the block has no `checklist` key, and the "
                            "checklist of " + " or ".join(
                                project for project in projects
                                if checklist.items(settings, project))
                            + " is not empty, so the design says which items apply",
                            "mark the items that apply: checklist = [...], or "
                            "checklist = [] when none apply")]
        return []
    return [Problem(block.checklist_line, f"`checklist` {problem}", fix)
            for problem, fix in checklist.problems(block.checklist, home, settings,
                                                   projects=projects)]


def _same(one, other) -> bool:
    """Whether two values of `blocks` hold up the same stages."""
    if isinstance(one, str) or isinstance(other, str):
        return one == other
    return set(one) == set(other)


def _blocks_of(stored):
    """A hold as an event stores it, as a block's entry gives it."""
    return stored if isinstance(stored, str) else tuple(stored or ())


def _taken_off(have, asked, known) -> tuple[str, ...]:
    """The stages a block's entry takes off a hold: of the ones the hold `have` covers
    among the stages `known` now, those the entry `asked` does not name."""
    held = dict.fromkeys(known) if have == questions.ALL else (
        () if isinstance(have, str) else have)
    keeps = () if isinstance(asked, str) else asked
    return tuple(one for one in held if one not in keeps)


def _named(blocks, declared) -> tuple[str, ...]:
    """The stages a block's entry holds up, one by one: every stage `declared` by its
    own block for an entry that holds up all, none for one that holds up none."""
    if blocks == questions.ALL:
        return tuple(declared)
    return () if isinstance(blocks, str) or blocks is None else tuple(blocks)


def _by_id(entries) -> dict[str, dict]:
    return {one["id"]: one for one in entries or ()
            if isinstance(one, dict) and isinstance(one.get("id"), str)}


def _narrowed_by(state: states.State, question_id: str, stage_id: str) -> str | None:
    """The id of the ruling that stands and narrowed the question off the stage."""
    ruling = corrections.standing(state, corrections.NARROW + question_id)
    if ruling is None:
        return None
    left = (ruling.rules or "").partition("=")[2].split(",")
    return None if stage_id in left else ruling.id


def _unwaited_by(state: states.State, stage_id: str, waited: str) -> str | None:
    """The id of the ruling that stands and took the wait for `waited` off the stage."""
    ruling = corrections.standing(state, f"{corrections.UNAFTER}{stage_id}={waited}")
    return None if ruling is None else ruling.id


def plan(state: states.State, block: design_block.DesignBlock) -> Plan:
    """What freezing `block` would do to the round whose log folded to `state`."""
    problems: list[Problem] = []
    added: dict[str, object] = {}
    pending: list[states.Pending] = []
    refused: list[tuple[str, str]] = []
    reworded: list[str] = []
    kept_off: list[KeptOff] = []
    # The waits this freezing would add, by stage.
    adds: dict[str, Sequence[str]] = {}
    recorded = {stage.id: stage for stage in state.stages}
    # What the version before declared: a list adds only what it gained since.
    before = versions.declarations(state)
    stages_before = _by_id(before.get(states.STAGE))
    questions_before = _by_id(before.get(states.QUESTION))
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
            adds[stage.id] = stage.after
            continue
        listed = (stages_before.get(stage.id) or {}).get("after") or ()
        missing = [one for one in stage.after if one not in have.after]
        more = [one for one in missing if one not in listed]
        kept_off += [KeptOff(states.AFTER, stage.id, one,
                             _unwaited_by(state, stage.id, one))
                     for one in missing if one in listed]
        gone = tuple(one for one in have.after if one not in stage.after)
        if more:
            add("after", stage.id, more)
            adds[stage.id] = more
        if gone:
            pending.append(states.Pending(states.AFTER, stage.id, gone=gone))
    declared_now = tuple(stage.id for stage in block.stages)
    done = questions.finished_stages(state) | questions.left_stages(state)
    pending += [states.Pending(states.STAGE, stage_id) for stage_id in recorded
                if stage_id not in declared_now and stage_id not in done]

    for asked in block.questions:
        if not isinstance(asked.blocks, str):
            unknown(asked, "blocks", asked.blocks, f"the question {asked.id}")
        have = questions.find(state, asked.id)
        if have is None:
            add("questions", None, asked.id)
            _prepared(asked, None, add, problems)
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
            was = questions_before.get(asked.id)
            listed = () if was is None else _named(
                was.get("blocks", questions.ALL), stages_before)
            held = _named(have.blocks, ())
            missing = [] if have.blocks == questions.ALL else [
                one for one in _named(asked.blocks, declared_now)
                if one not in held]
            more = [one for one in missing if one not in listed]
            kept = [one for one in missing if one in listed]
            kept_off += [KeptOff(states.BLOCKS, asked.id, one,
                                 _narrowed_by(state, asked.id, one)) for one in kept]
            edited = was is None or not _same(
                asked.blocks, _blocks_of(was.get("blocks", questions.ALL)))
            if questions.holds_every(asked.blocks, have.blocks):
                if not kept and (more or edited):
                    add("blocks", asked.id, questions.stored(asked.blocks))
                elif more:
                    add("blocks", asked.id, [*held, *more])
            else:
                # The block's entry holds up fewer stages, which waits. Any stage it
                # gained that the record does not hold is added all the same.
                if more:
                    add("blocks", asked.id, [*held, *more])
                pending.append(states.Pending(
                    states.BLOCKS, asked.id, blocks=asked.blocks,
                    gone=_taken_off(have.blocks, asked.blocks,
                                    (*recorded, *(stage.id for stage in block.stages)))))
        if asked.options is not None:
            _options(asked, have, add, problems, refused)
        _prepared(asked, have, add, problems)
    carried = {one.get("id") for version in state.versions
               for one in version.declared.get(states.QUESTION) or ()}
    asked_now = {asked.id for asked in block.questions}
    pending += [states.Pending(states.QUESTION, one.id) for one in state.questions
                if one.id in carried and one.id not in asked_now
                and one.state in _ASKED]

    problems += stages.unmeetable(state, block, adds)
    for check in CHECKS:
        problems += check(state, block)
    return Plan(declared=_declared(block), added=added, pending=tuple(pending),
                problems=tuple(problems), refused=tuple(refused),
                reworded=tuple(reworded), kept_off=tuple(kept_off))


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


#: How a design's block writes the options of a prepared answer.
_PREPARED_OPTION = "[[question.prepared.option]]"


def _prepared(asked: design_block.Question, have: states.Question | None, add,
              problems: list[Problem]) -> None:
    """What the answer the block prepares for `asked` adds to the question `have` (None
    for a question the block adds): the answer, unless it is the one on record and
    current, and for a question left with no options the options it gives. What
    stands in the way is checked as `ask --prepared` checks it."""
    prepared = asked.prepared
    if prepared is None:
        return
    retired = () if have is None else have.retired
    if asked.options is not None:
        # The block gives the options the question offers after this freezing.
        offered = tuple((key, text) for key, text in asked.options if key not in retired)
    else:
        offered = () if have is None else have.options
    body = preparing.stored(prepared)
    newest = have.prepared[-1] if have is not None and have.prepared else None
    if (newest is not None and offered
            and _without_offered(newest.body(), offered) == _without_offered(body, offered)
            and {one.key for one in newest.options} == {key for key, _text in offered}):
        return
    wrong = preparing.problems(prepared, asked.id, offered=offered, retired=retired,
                               header=_PREPARED_OPTION)
    if wrong:
        problems += wrong
        return
    if not offered:
        add("options", asked.id, {"options": preparing.given_options(prepared)})
    add("prepared", asked.id, body)


def _without_offered(body: dict, offered: tuple[tuple[str, str], ...]) -> dict:
    """The stored answer `body` less each option's `text` where it is the line the
    question offers under that key, so an answer that gives the line and one that
    leaves it out read the same."""
    lines = dict(offered)
    return {**body, "option": [{key: value for key, value in one.items()
                                if not (key == "text" and value == lines.get(one["key"]))}
                               for one in body["option"]]}


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


def freeze(name: str, *, by: str, records_only: bool = False,
           record: str | None = None, settings=None, wait_s: float = write.WAIT_S,
           at: str | None = None) -> Frozen:
    """Freeze the design of the round `name`, `<project>/<slug>`, as its next version.

    - `by`: who freezes it, the event's actor, and who asked each question the
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
    is none, options that break a question's rules, a prepared answer that does not
    speak of exactly the options its question offers, a recorded stage given
    another repo, branch or child round, a stage that left the plan, a wait added for
    a stage that left it or that closes a circle of waits, or a second stage in one
    repo where the flow does not verify the design. A version
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
    round_dir = home.round_dir(slug)
    design = round_dir / versions.DESIGN_FILE
    again = f"moxi rounds fold {name}"
    as_records = f"{again} --records-only --record PTR"

    def read(where) -> _Read:
        if not design.is_file():
            return _Read()
        try:
            return _Read(block=design_block.read(design))
        except BadBlock as exc:
            return _Read(error=exc)

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
                for one in brake.differences(before, planned(view).declared,
                                             settled=brake.settled(view.state))]

    def freeze_file(view) -> tuple[int, Path]:
        if view.facts.error is not None:
            raise view.facts.error
        block = view.facts.block
        found = planned(view)
        wrong = [*found.problems, *mark_problems(block, home.project, settings)]
        if wrong:
            raise BadBlock(block.file, design_block.WHAT, wrong)
        number = len(view.state.versions) + 1
        target = versions.file(round_dir, number)
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
        except OSError as exc:
            raise RoundsError(f"the design can't be frozen as {target}: {exc}",
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
            write.Where(home.project, home.rounds_dir, slug), by=by,
            gates=[ending.takes_new_work(name, "a new version of its design"),
                   has_a_design_step, has_a_design, has_a_pointer,
                   changes_records_only],
            act=freeze_file, event=frozen, facts=read, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    number, target = written.result
    after = states.fold(written.round_events)
    limit = own.round_setting(settings, home.project, "slice_minutes")
    return Frozen(name=name, version=number, file=target,
                  digest=written.events[0]["digest"], plan=planned(written.view),
                  event=written.events[0],
                  standing=versions.standing(after),
                  trace=tracing.shown(after, written.view.project_state),
                  failed=written.failed,
                  oversized=oversized(written.events[0].get("declared") or {}, limit),
                  slice_minutes=limit)


__all__ = ["DESIGN", "CHECKS", "KeptOff", "Plan", "Frozen", "oversized", "plan",
           "mark_problems", "freeze"]
