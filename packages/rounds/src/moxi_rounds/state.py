"""The fold: a log's events, in order, made into its state.

`fold(events)` is a pure function. It reads nothing but the events it is given, changes
none of them, and gives equal states for equal logs. Each event type has one handler in
`_HANDLERS`, called with the state so far and the event; a type whose handler is `_kept`
changes nothing and is still in the history. A type this code does not know is kept the
same way, so a log with a later version's events still folds.

The state holds what the round's first event set (its description, its flow's name and
the steps recorded for it, its parent, and its status), the rulings recorded in the log,
its questions, each with every change made to it, the options it offers and the key its
answer chose, and the whole history. A ruling of a question is one event: the ruling's
own record names the question and says what it becomes, and the fold changes the
question from it. A question that was moved to another log stays in this one as moved,
naming the record it became; the one that arrived names the record it was.

A ruling is marked shown by an event that names it with the pointer to where the
operator was shown it; the mark stays whatever is recorded later.

A ruling may replace an earlier one or take it back, and names it. Nothing is rewritten:
the earlier ruling stays in the rulings, marked with the id of the one that replaced it
or took it back, and each question it changed reads as its other changes leave it
(`without`).

A round follows the flow its log recorded last: the one it was opened with, until an
event moves it to another, which holds the new name and its steps. That event is the
lead's move, or the operator's ruling of the flow, which is one event like any ruling.

A skip of the questions step for a stage lists the questions it lets the stage past.
One of them that is later made to hold up fewer stages, that stage not among them, is
let past no longer: the ruling is marked with it (`Ruling.let_go`), so when the
question is widened onto the stage again it holds the stage like any other.

A stage is in the plan from the event that declared it: its own, or the freezing of a
design version whose block first carried it. A frozen version is one event, and it is
the whole of what the freezing did: it holds the version's number and digest, what the
design's block declared, and what of that was new to the record, which the fold adds
from it (a question, a stage, a stage another must follow, a wider hold, options).
What the block took away is kept with the version as pending and changes nothing.

A report's verdict is one event, kept in `verdicts`. One that finds a version of the
design publishable also says what it takes out of the record, from what that version's
block took away, and the fold takes it out from that event: a question is withdrawn, a
question holds up fewer stages, a stage waits for fewer. A stage it takes out of the
plan keeps its record, as one the operator rules out does.

A round that was dropped is `DROPPED` from that event on, and the event is the whole of
the drop in the round's log: it says who dropped the round, and where each question
that was open or parked went, which the fold applies to the question as a change. A
round that closed is `CLOSED` from its closing event on. A log with no opening event, a project's for one, has a status of None; the rulings of a project's log
are its register, and its questions the project's list.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

#: The status of a round that was opened and has not ended.
OPEN = "open"
#: The status of a round that was withdrawn before its work was done.
DROPPED = "dropped"
#: The status of a round whose work is done: it closed itself.
CLOSED = "closed"

#: Where a drop says a question went when it was withdrawn with its round. A question
#: that went to the project's list has its id there in this place.
_WITHDRAWN = "withdrawn"
_MOVED = "moved"


@dataclass(frozen=True)
class Ended:
    """How a round ended, as the event that ended it recorded it."""

    #: The status the round has from then on.
    how: str
    #: Whose act it was, `lead` or `operator`, and the pointer it carried. Both are
    #: None for a round that closed: closing is nobody's act.
    by: str | None
    record: str | None
    #: Who recorded it, and when. For a round that closed, who ran the verb whose
    #: write closed it.
    actor: str
    at: str
    #: Where each question that was open or parked went, in the order asked: its id
    #: on the project's list, or `withdrawn`.
    questions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Ruling:
    """One ruling as its event recorded it."""

    #: `C<n>` in a round's log, `S<n>` in a project's.
    id: str | None
    #: The sentence, as given.
    text: str | None
    #: The pointer to where the operator said it.
    record: str | None
    #: Why, when a reason was given.
    reason: str | None
    #: Whether it says what is built or changes a record of the process.
    kind: str | None
    #: The item it rules, or None for a ruling that stands on its own.
    rules: str | None
    #: The stages of the round it applies to: `"all"`, or their ids. None in a
    #: project's log, which has no stages.
    stages: str | tuple[str, ...] | None
    #: The repos and paths a register entry applies to, each `{"repo": key}` with a
    #: `"path"` glob or without; empty when it names none.
    scope: tuple[dict, ...]
    #: Who recorded it, and when.
    actor: str
    at: str
    #: The key of the option an answer chose, or None.
    chose: str | None = None
    #: The question it changes, or None for a ruling that changes none.
    question: str | None = None
    #: The ids of the questions a skip of the questions step lets its stage past.
    covers: tuple[str, ...] = ()
    #: The ones among them it lets past no longer: each was made to hold up fewer
    #: stages after the skip, the skip's stage not among them.
    let_go: tuple[str, ...] = ()
    #: The earlier ruling this one replaces, and the one it takes back, or None.
    supersedes: str | None = None
    revokes: str | None = None
    #: The later ruling that replaced this one, and the one that took it back, or None
    #: while it stands. A ruling with either is read by nothing but a listing.
    replaced_by: str | None = None
    revoked_by: str | None = None
    #: The pointer to where the operator was shown it, from the first mark that named
    #: it, or None while no mark has. His read-back of the project's list also makes a
    #: ruling shown, and is a ruling of the project's log: `shown.is_shown` reads both.
    shown: str | None = None


@dataclass(frozen=True)
class Change:
    """One change to a question, as its event recorded it: a ruling of the question, or
    a change that is no ruling. A field the change did not touch is None."""

    #: The state the question moved to.
    state: str | None
    #: What it holds up from then on: `"all"`, `"none"`, or stage ids.
    blocks: str | tuple[str, ...] | None
    #: What a parked question waits for: a stage's id, or `round:<name>`.
    until: str | None
    #: The ruling that made the change, None for a change that is no ruling, and the
    #: operator's pointer when the change carries one.
    ruling: str | None
    record: str | None
    #: Who recorded the change, and when.
    actor: str
    at: str
    #: The options it added, each `(key, line)`, and the keys it retired.
    options: tuple[tuple[str, str], ...] = ()
    retired: tuple[str, ...] = ()
    #: The record the question became in another log, when the change moved it there.
    moved_to: str | None = None
    #: The later ruling that replaced or took back the ruling that made this change, or
    #: None. A change with one is kept as history and is no part of what the question
    #: reads as.
    undone_by: str | None = None
    #: Whether a verdict made the change, applying what a design's block took away.
    verdict: bool = False


@dataclass(frozen=True)
class Question:
    """One question put to the operator, as the log leaves it."""

    #: `Q<n>` in a round's log, `P<n>` in a project's.
    id: str | None
    #: The one sentence that asks it.
    text: str | None
    #: Where it came from, the event's `from`: a pointer.
    origin: str | None
    #: What it holds up: `"all"`, `"none"`, or stage ids.
    blocks: str | tuple[str, ...] | None
    #: `open`, `parked`, `ruled` or `withdrawn`, as last recorded, or `moved` for one
    #: that left this log for another. A parked question that waits for something reads
    #: as open once that is reached; `questions.current` gives the state as it reads now.
    state: str | None
    #: Who asked it, and when.
    by: str
    at: str
    #: The operator's pointer, when the question is his own.
    record: str | None = None
    #: What a parked question waits for, or None.
    until: str | None = None
    #: The ruling that last changed it, or None.
    ruling: str | None = None
    #: Every change since it was asked, oldest first.
    changes: tuple[Change, ...] = ()
    #: The options it offers now, each `(key, line)`, in the order they were added.
    options: tuple[tuple[str, str], ...] = ()
    #: The keys that were retired, oldest first: offered no longer, never used again.
    retired: tuple[str, ...] = ()
    #: The key of the option its answer chose, or None.
    chose: str | None = None
    #: The record it was in the log it was brought here from: `P<n>` of this project's
    #: list, or `<project>/<slug>:Q<n>` of a round. None for one asked here.
    moved_from: str | None = None
    #: What it held up in the round it left, kept as said then: it holds up nothing
    #: on a project's list. None for one that left no round.
    held: str | tuple[str, ...] | None = None
    #: The record it became in the log it was moved to, or None while it is here.
    moved_to: str | None = None


@dataclass(frozen=True)
class Stage:
    """A stage of the round's plan: a build in `repo` on `branch`, or the round `round`
    in place of one; the other two are None."""

    id: str
    repo: str | None
    branch: str | None
    round: str | None
    #: The stages that must be finished before this one starts.
    after: tuple[str, ...]
    #: Each worker's piece of it, as `{"id", "what", "minutes"}`.
    slices: tuple[dict, ...]
    #: Why it lands on its own, where it said.
    why: str | None
    #: The design version that first carried it, None for one declared on its own.
    version: int | None
    #: Who declared it and when.
    actor: str
    at: str


@dataclass(frozen=True)
class Pending:
    """Something a design version's block took away, which stands in the record until
    a verdict or a ruling applies it. `what` is `QUESTION` (the question `id` is gone
    from the block), `STAGE` (the stage is gone), `AFTER` (the stage waits for fewer:
    `gone` are the ones left out) or `BLOCKS` (the question holds up fewer: `blocks`
    is what the block says it holds up)."""

    what: str
    id: str
    gone: tuple[str, ...] = ()
    blocks: str | tuple[str, ...] | None = None


#: Where a version is frozen, from the round's folder: `n` is its number.
FROZEN_FILE = "versions/design-v{n}.md"

#: What a `Pending` is of.
QUESTION = "question"
STAGE = "stage"
AFTER = "after"
BLOCKS = "blocks"


@dataclass(frozen=True)
class Version:
    """A frozen version of the design."""

    #: Its number, from 1.
    n: int
    #: The SHA-256 of the frozen file.
    digest: str | None
    #: Whether it was frozen as a change of records only, and the pointer that says so.
    records_only: bool
    record: str | None
    #: What the check of its trace rows found when it was frozen, as the event holds
    #: it (`tracing.recorded` reads it); None for an event that holds no result.
    trace: object
    #: What the design's block declared, as the event holds it: `question`, `stage`,
    #: `trace` and `commitment`, each a list.
    declared: dict
    #: What it took away, which stands until a verdict or a ruling applies it.
    pending: tuple[Pending, ...]
    #: Who froze it and when.
    actor: str
    at: str
    #: The number of its event in the log, which places it among the verdicts.
    event: int | None = None


@dataclass(frozen=True)
class Decided:
    """The lead's decision on a thread of the design's verification: no further pass
    is run on it."""

    #: The number of its event in the log, which places it among the verdicts.
    n: int | None
    #: The thread's name, and what was decided of it: closed, or its risk accepted.
    thread: str | None
    decision: str | None
    #: The pointer to where the lead wrote why.
    record: str | None
    #: The version of the design that was current then, None when none was frozen.
    version: int | None
    #: Who recorded it and when.
    actor: str
    at: str


@dataclass(frozen=True)
class Verdict:
    """One report's verdict as its event recorded it. A field the report's kind does
    not carry is None."""

    #: The number of its event in the log, which names it there.
    n: int | None
    #: The kind of report, what the pass was, and the verdict's word.
    kind: str | None
    pass_: str | None
    verdict: str | None
    #: The version of the design it read, for a design report.
    design: int | None
    #: The model the report says wrote it, and who it says wrote it.
    model: str | None
    by: str | None
    #: The report's file as it was given, and the SHA-256 of its bytes then.
    report: str | None
    sha256: str | None
    #: Every finding of its block, each `{"id", "severity", "state", "text"}`.
    findings: tuple[dict, ...]
    #: What is noted of the report and refuses nothing.
    flags: tuple[str, ...]
    #: What it took out of the record, of what the version it read took away, and
    #: what of that it left, each with the reason.
    applied: tuple[Pending, ...]
    not_applied: tuple[tuple[Pending, str], ...]
    #: Who recorded it and when.
    actor: str
    at: str


@dataclass
class State:
    """What a log says, as of its last event."""

    #: What the round is for.
    description: str | None = None
    #: The name of the flow the round follows.
    flow: str | None = None
    #: That flow's step entries as the log recorded them, which the round follows
    #: whatever the config says since.
    steps: list[dict] = field(default_factory=list)
    #: The round this one is a child of, or None.
    parent: str | None = None
    #: The ids of the project's standing rulings the round starts from, as it was
    #: opened with them.
    inherits: list[str] = field(default_factory=list)
    #: `OPEN`, `CLOSED` or `DROPPED`, or None for a log that opened no round.
    status: str | None = None
    #: How the round ended, or None while it has not.
    ended: Ended | None = None
    #: The rulings the log recorded, oldest first. A project's are its register.
    rulings: list[Ruling] = field(default_factory=list)
    #: The questions the log holds, in the order they were asked. A project's are
    #: its list.
    questions: list[Question] = field(default_factory=list)
    #: The stages the log declared, in the order it declared them.
    stages: list[Stage] = field(default_factory=list)
    #: The design versions frozen, oldest first.
    versions: list[Version] = field(default_factory=list)
    #: The verdicts recorded, oldest first.
    verdicts: list[Verdict] = field(default_factory=list)
    #: The lead's decisions on threads of the design's verification, oldest first.
    threads: list[Decided] = field(default_factory=list)
    #: Every event, oldest first.
    history: list[dict] = field(default_factory=list)


def fold(events: Iterable[dict]) -> State:
    """The state the events of one log give, read oldest first."""
    state = State()
    for event in events:
        _HANDLERS.get(event["type"], _kept)(state, event)
        state.history.append(event)
    return state


def _kept(state: State, event: dict) -> None:
    """The event changes no field; it is in the history only."""


def _round_opened(state: State, event: dict) -> None:
    state.description = event["description"]
    state.flow = event["flow"]
    # A copy, so a later change to the state's steps never reaches the event.
    state.steps = copy.deepcopy(event["steps"])
    state.parent = event.get("parent")
    state.inherits = list(_ids(event.get("inherits")))
    state.status = OPEN


def _flow_changed(state: State, event: dict) -> None:
    """The round follows the flow the event names, with the steps it holds, from then
    on. An event that holds no name or no steps moves the round nowhere."""
    if isinstance(event.get("flow"), str) and isinstance(event.get("steps"), list):
        state.flow = event["flow"]
        # A copy, as at the opening.
        state.steps = copy.deepcopy(event["steps"])


def _round_dropped(state: State, event: dict) -> None:
    """The round is dropped, and each question the event names is where it says: on
    the project's list under the id given, or withdrawn. Nothing else is written for
    a drop in this log, so a verb stopped after this event has left the round whole."""
    went = event.get("questions")
    went = tuple((key, where) for key, where in went.items()
                 if isinstance(where, str)) if isinstance(went, dict) else ()
    state.status = DROPPED
    state.ended = Ended(how=DROPPED, by=event.get("by"), record=event.get("record"),
                        actor=event["actor"], at=event["at"], questions=went)
    for question_id, where in went:
        change = ({"state": _WITHDRAWN} if where == _WITHDRAWN else
                  {"state": _MOVED, "moved_to": where})
        _change(state, {**change, "record": event.get("record"),
                        "actor": event["actor"], "at": event["at"]},
                question_id=question_id, ruling=None)


def _round_closed(state: State, event: dict) -> None:
    """The round is closed, from this event on. A round that had ended already stays
    as it ended: only the first ending counts."""
    if state.ended is not None:
        return
    state.status = CLOSED
    state.ended = Ended(how=CLOSED, by=None, record=None, actor=event["actor"],
                        at=event["at"])


def _ruling_recorded(state: State, event: dict) -> None:
    stages = event.get("stages")
    state.rulings.append(Ruling(
        id=event.get("id"), text=event.get("text"), record=event.get("record"),
        reason=event.get("reason"), kind=event.get("kind"), rules=event.get("rules"),
        stages=tuple(stages) if isinstance(stages, list) else stages,
        # A copy, so the state's scope and the event's never share a dict.
        scope=tuple(copy.deepcopy(event.get("scope") or [])),
        actor=event["actor"], at=event["at"], chose=event.get("chose"),
        question=event.get("question"), covers=tuple(event.get("covers") or ()),
        supersedes=event.get("supersedes"), revokes=event.get("revokes")))
    # A ruling of a question is the whole of its change: nothing else is written for
    # it, so a verb stopped after this event has left the question ruled.
    if event.get("question") is not None:
        _change(state, event, question_id=event["question"], ruling=event.get("id"))
    # His ruling of the flow is the whole of the move, as the lead's event is of his.
    if (event.get("rules") or "").startswith(_FLOW):
        _flow_changed(state, event)
    # His ruling that a stage waits for one stage fewer is the whole of that change.
    if (event.get("rules") or "").startswith(_UNAFTER):
        stage_id, _equals, waited = event["rules"][len(_UNAFTER):].partition("=")
        _waits(state, stage_id, gone=(waited,))
    # The key an answer chose is kept in the ruling. The question reads it from its
    # newest answer, so an answer that names no key leaves none chosen.
    for place, question in enumerate(state.questions):
        if question.id is not None and question.id == event.get("rules"):
            state.questions[place] = replace(question, chose=event.get("chose"))
    _corrected(state, event)


def _ruling_shown(state: State, event: dict) -> None:
    """Mark each ruling the event names as shown, where its pointer says. Only a
    pointer makes a mark, a mark is never taken off, and the first one is the one
    kept."""
    ids, record = event.get("ids"), event.get("record")
    if not isinstance(ids, list) or not isinstance(record, str) or not record:
        return
    for place, ruling in enumerate(state.rulings):
        if ruling.id in ids and ruling.shown is None:
            state.rulings[place] = replace(ruling, shown=record)


def _corrected(state: State, event: dict) -> None:
    """Mark the ruling that `event`, a ruling just added, replaces or takes back, and
    read again each question that ruling changed. A ruling that is already replaced or
    taken back, or that the log does not hold, is left as it is."""
    touched = set()
    for names, mark in (("supersedes", "replaced_by"), ("revokes", "revoked_by")):
        target = event.get(names)
        for place, ruling in enumerate(state.rulings[:-1]):
            if (not isinstance(target, str) or ruling.id != target
                    or ruling.replaced_by is not None or ruling.revoked_by is not None):
                continue
            state.rulings[place] = replace(ruling, **{mark: event.get("id")})
            touched.add(ruling.rules)
            for at, question in enumerate(state.questions):
                if any(change.ruling == target for change in question.changes):
                    touched.add(question.id)
                    state.questions[at] = replace(question, changes=tuple(
                        replace(change, undone_by=event.get("id"))
                        if change.ruling == target and change.undone_by is None
                        else change for change in question.changes))
    for place, question in enumerate(state.questions):
        if question.id is not None and question.id in touched:
            answers = [ruling.chose for ruling in state.rulings
                       if ruling.rules == question.id and ruling.replaced_by is None
                       and ruling.revoked_by is None]
            state.questions[place] = replace(
                without(state, question), chose=answers[-1] if answers else None)
            _let_go(state, state.questions[place])


def without(state: State, question: Question, rulings: Iterable[str] = ()) -> Question:
    """`question`, one of `state`'s, as it reads with no change from a ruling that was
    replaced or taken back, nor from the `rulings` named here: its state, what a parked
    one waits for, what it holds up, the ruling that last changed it and the record it
    became. With no ruling named this is the question as the fold gives it.

    Each change that is left is applied in order from the question as it was asked.
    Leaving out a narrowing leaves what the question held up before it; a widening
    that anyone made while that narrowing stood then adds its stages to that, since it
    was a list of the narrowed question and said nothing of the stages already let go.
    """
    left_out = frozenset(rulings)
    asked = _asked(state, question.id)
    reads, until = asked.get("state"), asked.get("until")
    blocks = _blocks(asked.get("blocks", _ALL))
    ruling = moved_to = None
    # The rulings whose own change is still to come and that undid a narrowing above:
    # until then a list is one of the narrowed question.
    pending: set[str | None] = set()
    for change in question.changes:
        if change.undone_by is not None or change.ruling in left_out:
            if change.blocks is not None:
                pending.add(change.undone_by)
            continue
        if change.ruling is not None:
            pending.discard(change.ruling)
        if change.state is not None:
            reads, until = change.state, change.until
        if change.blocks is not None:
            # What a verdict applies is what the design's block says, like a ruling's
            # list; only a list anyone may give is one of the narrowed question.
            widens = change.ruling is None and not change.verdict and pending
            blocks = _widest(blocks, change.blocks) if widens else change.blocks
        ruling = ruling if change.ruling is None else change.ruling
        moved_to = moved_to if change.moved_to is None else change.moved_to
    return replace(question, state=reads, until=until, blocks=blocks, ruling=ruling,
                   moved_to=moved_to)


def _asked(state: State, question_id: str | None) -> dict:
    """The question as it was first asked: its own event, or for one a design
    declared its entry in the version that first carried it, which asked it open."""
    for event in state.history:
        if event["type"] == "question.asked" and event.get("id") == question_id:
            return event
        added = event.get("added") if event["type"] == "design.frozen" else None
        declared = event.get("declared")
        if (isinstance(added, dict) and question_id in _ids(added.get("questions"))
                and isinstance(declared, dict)):
            for one in declared.get(QUESTION) or ():
                if isinstance(one, dict) and one.get("id") == question_id:
                    return {"state": OPEN, "blocks": one.get("blocks", _ALL)}
    return {}


def _widest(one, other):
    """What holds up every stage that `one` or `other` does."""
    if _ALL in (one, other):
        return _ALL
    stages = tuple(dict.fromkeys(
        stage for blocks in (one, other) if not isinstance(blocks, str)
        for stage in blocks or ()))
    return stages or (one if other is None else other)


#: What `blocks` holds for a question that holds up every stage.
_ALL = "all"

#: What starts the item of a ruling that moves the round to another flow; the flow's
#: name follows.
_FLOW = "flow:"
#: What starts the item of a ruling that takes one stage off what another waits for.
_UNAFTER = "unafter:"
#: What starts the item of a ruling that skips the questions step for one stage; the
#: stage's id follows.
_SKIP_QUESTIONS = "skip:docket:"


def _let_go(state: State, question: Question) -> None:
    """Mark each skip of the questions step that lists `question` and whose stage the
    question, as it now reads, does not hold up: the skip lets that stage past it no
    longer. A mark stays, so a question widened onto the stage again holds it."""
    for place, ruling in enumerate(state.rulings):
        rules = ruling.rules or ""
        if (not rules.startswith(_SKIP_QUESTIONS) or question.id not in ruling.covers
                or question.id in ruling.let_go or question.blocks == _ALL):
            continue
        if (isinstance(question.blocks, str) or
                rules[len(_SKIP_QUESTIONS):] not in (question.blocks or ())):
            state.rulings[place] = replace(ruling, let_go=(*ruling.let_go, question.id))


def _blocks(value):
    return tuple(value) if isinstance(value, list) else value


def _offered(value, have: tuple[tuple[str, str], ...] = (),
             retired: tuple[str, ...] = ()) -> tuple[tuple[str, str], ...]:
    """The options an event adds, each `(key, line)`. A key keeps its text and a
    retired key is never used again, so one the question has, or retired, adds none."""
    taken = {key for key, _text in have} | set(retired)
    added = []
    for key, text in (value.items() if isinstance(value, dict) else ()):
        if key not in taken:
            taken.add(key)
            added.append((key, text))
    return tuple(added)


def _question_asked(state: State, event: dict) -> None:
    state.questions.append(Question(
        id=event.get("id"), text=event.get("text"), origin=event.get("from"),
        # A question that does not say what it holds up holds up every stage.
        blocks=_blocks(event.get("blocks", _ALL)), state=event.get("state"),
        # A question brought here from another log keeps who asked it and when.
        by=event.get("asked_by") or event["actor"],
        at=event.get("asked_at") or event["at"], record=event.get("record"),
        until=event.get("until"), options=_offered(event.get("options")),
        # A key retired where the question was before stays retired here.
        retired=tuple(event.get("retired") or ()),
        moved_from=event.get("moved_from"), held=_blocks(event.get("held"))))


def _question_changed(state: State, event: dict) -> None:
    _change(state, event, question_id=event.get("id"), ruling=None)


def _change(state: State, event: dict, *, question_id: str | None,
            ruling: str | None, verdict: bool = False) -> None:
    """Apply to the question `question_id` what `event` says of it, and add the change
    to its record. `ruling` is the id of the ruling the event is, None for an event
    that is no ruling; `verdict` says the change is one a verdict applied."""
    for place, question in enumerate(state.questions):
        if question.id == question_id:
            break
    else:
        # A change to a question the log never asked changes nothing.
        return
    change = Change(state=event.get("state"), blocks=_blocks(event.get("blocks")),
                    until=event.get("until"), ruling=ruling,
                    record=event.get("record"), actor=event["actor"], at=event["at"],
                    moved_to=event.get("moved_to"), verdict=verdict,
                    options=_offered(event.get("options"), question.options,
                                     question.retired),
                    # Only a key the question offers is retired, and only once.
                    retired=tuple(dict.fromkeys(
                        key for key in event.get("retired") or ()
                        if any(key == had for had, _text in question.options))))
    state.questions[place] = replace(
        question,
        state=question.state if change.state is None else change.state,
        blocks=question.blocks if change.blocks is None else change.blocks,
        # What a question waits for belongs to one parking: any later change of state
        # ends it.
        until=question.until if change.state is None else change.until,
        ruling=question.ruling if change.ruling is None else change.ruling,
        moved_to=question.moved_to if change.moved_to is None else change.moved_to,
        changes=(*question.changes, change),
        options=tuple(one for one in (*question.options, *change.options)
                      if one[0] not in change.retired),
        retired=(*question.retired, *change.retired))
    if change.blocks is not None:
        _let_go(state, state.questions[place])


def _ids(value) -> tuple[str, ...]:
    return tuple(one for one in value if isinstance(one, str)) if isinstance(
        value, list) else ()


def _stage_declared(state: State, event: dict, *, version: int | None = None) -> None:
    """The stage the event names joins the plan. An id is declared once: a second
    declaration of it changes nothing."""
    stage_id = event.get("id")
    if not isinstance(stage_id, str) or any(one.id == stage_id for one in state.stages):
        return
    slices = event.get("slices", event.get("slice"))
    state.stages.append(Stage(
        id=stage_id, repo=event.get("repo"), branch=event.get("branch"),
        round=event.get("round"), after=_ids(event.get("after")),
        slices=tuple(copy.deepcopy(slices)) if isinstance(slices, list) else (),
        why=event.get("why") or None, version=version,
        actor=event["actor"], at=event["at"]))


def _waits(state: State, stage_id, *, more: tuple[str, ...] = (),
           gone: tuple[str, ...] = ()) -> None:
    """The stage waits for `more` beside what it waits for, and no longer for `gone`.
    An id the log never declared changes nothing."""
    for place, stage in enumerate(state.stages):
        if stage.id == stage_id:
            state.stages[place] = replace(stage, after=tuple(
                one for one in dict.fromkeys((*stage.after, *more)) if one not in gone))


def _stage_changed(state: State, event: dict) -> None:
    """A stage waits for more stages, or for fewer. A stage that left the plan keeps
    its record: `questions.left_stages` says which did, from the history."""
    _waits(state, event.get("id"), more=_ids(event.get("after")),
           gone=_ids(event.get("gone")))


def _design_frozen(state: State, event: dict) -> None:
    """A version of the design is frozen, and what its block added to the record is
    added: the event is the whole of the freezing, so nothing else is written for it
    and a verb stopped after this event has left the version whole."""
    declared = event.get("declared")
    declared = copy.deepcopy(declared) if isinstance(declared, dict) else {}
    added = event.get("added")
    added = added if isinstance(added, dict) else {}
    version = event.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        # An event that numbers no version froze none.
        return
    who = {"actor": event["actor"], "at": event["at"]}
    state.versions.append(Version(
        n=version, digest=event.get("digest"),
        records_only=event.get("records_only") is True, record=event.get("record"),
        trace=copy.deepcopy(event.get("trace")), declared=declared,
        pending=_taken_away(event.get("pending")), event=event.get("n"), **who))
    said = {kind: {one.get("id"): one for one in declared.get(kind) or ()
                   if isinstance(one, dict)} for kind in (QUESTION, STAGE)}
    for stage_id in _ids(added.get("stages")):
        if stage_id in said[STAGE]:
            _stage_declared(state, {**said[STAGE][stage_id], **who}, version=version)
    for question_id in _ids(added.get("questions")):
        one = said[QUESTION].get(question_id)
        if one is None or any(had.id == question_id for had in state.questions):
            continue
        # A question a design declares was asked by the freezing that first carried
        # it: that version is where it came from, and the freezing says who and when.
        _question_asked(state, {
            "id": question_id, "text": one.get("text"),
            "from": f"file:{FROZEN_FILE.format(n=version)}#{question_id}",
            "blocks": one.get("blocks", _ALL), "state": OPEN,
            "options": one.get("options"), **who})
    for stage_id, more in (added.get("after") or {}).items():
        _waits(state, stage_id, more=_ids(more))
    for question_id, blocks in (added.get("blocks") or {}).items():
        _change(state, {"blocks": blocks, **who}, question_id=question_id, ruling=None)
    for question_id, change in (added.get("options") or {}).items():
        if isinstance(change, dict):
            _change(state, {"options": change.get("options"),
                            "retired": change.get("retired"), **who},
                    question_id=question_id, ruling=None)


def _taken_away(listed) -> tuple[Pending, ...]:
    """The entries of an event's list of what a block took away."""
    return tuple(
        Pending(what=one.get("what"), id=one.get("id"), gone=_ids(one.get("gone")),
                blocks=_blocks(one.get("blocks")))
        for one in (listed if isinstance(listed, list) else ())
        if isinstance(one, dict) and isinstance(one.get("id"), str))


def _verdict_recorded(state: State, event: dict) -> None:
    """A report's verdict joins the record, and what it applies is taken out: the
    event is the whole of the verdict, so nothing else is written for it and a verb
    stopped after this event has left nothing half applied."""
    who = {"actor": event["actor"], "at": event["at"]}
    applied = _taken_away(event.get("applied"))
    left = event.get("not_applied")
    left = [one for one in (left if isinstance(left, list) else ())
            if isinstance(one, dict)]
    state.verdicts.append(Verdict(
        n=event.get("n"), kind=event.get("kind"), pass_=event.get("pass"),
        verdict=event.get("verdict"), design=event.get("design"),
        model=event.get("model"), by=event.get("by"), report=event.get("report"),
        sha256=event.get("sha256"),
        findings=tuple(copy.deepcopy(one) for one in event.get("findings") or ()
                       if isinstance(one, dict)),
        flags=_ids(event.get("flags")), applied=applied,
        not_applied=tuple(zip(_taken_away(left),
                              (str(one.get("why") or "") for one in left
                               if isinstance(one.get("id"), str)))),
        **who))
    for one in applied:
        if one.what == QUESTION:
            _change(state, {"state": _WITHDRAWN, **who}, question_id=one.id, ruling=None,
                    verdict=True)
        elif one.what == BLOCKS and one.blocks is not None:
            _change(state, {"blocks": one.blocks, **who}, question_id=one.id,
                    ruling=None, verdict=True)
        elif one.what == AFTER:
            _waits(state, one.id, gone=one.gone)
        # A stage that left keeps its record: `questions.left_stages` says which
        # did, from the history.


def _thread_decided(state: State, event: dict) -> None:
    """The lead's decision on a thread joins the record, with the version of the
    design it was made at: the one that was current then."""
    state.threads.append(Decided(
        n=event.get("n"), thread=event.get("thread"), decision=event.get("decision"),
        record=event.get("record"),
        version=state.versions[-1].n if state.versions else None,
        actor=event["actor"], at=event["at"]))


#: The handler of each event type. Every type in `events.TYPES` has a row.
_HANDLERS: dict[str, Callable[[State, dict], None]] = {
    "round.opened": _round_opened,
    "flow.changed": _flow_changed,
    "design.frozen": _design_frozen,
    "verdict.recorded": _verdict_recorded,
    "question.asked": _question_asked,
    "question.changed": _question_changed,
    "stage.declared": _stage_declared,
    "stage.changed": _stage_changed,
    "ruling.recorded": _ruling_recorded,
    "ruling.shown": _ruling_shown,
    "build.commissioned": _kept,
    "check.ran": _kept,
    "finding.marked": _kept,
    "wait.marked": _kept,
    "brief.sized": _kept,
    "finish.action": _kept,
    "finish.done": _kept,
    "lock.cleared": _kept,
    "build.dropped": _kept,
    "round.dropped": _round_dropped,
    "thread.decided": _thread_decided,
    "repair": _kept,
    "round.closed": _round_closed,
}

__all__ = ["OPEN", "DROPPED", "CLOSED", "FROZEN_FILE", "QUESTION", "STAGE", "AFTER", "BLOCKS",
           "Ended", "Ruling", "Change", "Question", "Stage", "Pending", "Version",
           "Decided", "Verdict", "State", "fold", "without"]
