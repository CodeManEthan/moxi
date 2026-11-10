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

A round that was dropped is `DROPPED` from that event on, and the event is the whole of
the drop in the round's log: it says who dropped the round, and where each question
that was open or parked went, which the fold applies to the question as a change. A log
with no
opening event, a project's for one, has a status of None; the rulings of a project's log
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

#: Where a drop says a question went when it was withdrawn with its round. A question
#: that went to the project's list has its id there in this place.
_WITHDRAWN = "withdrawn"
_MOVED = "moved"


@dataclass(frozen=True)
class Ended:
    """How a round ended, as the event that ended it recorded it."""

    #: The status the round has from then on.
    how: str
    #: Whose act it was, `lead` or `operator`, and the pointer it carried.
    by: str | None
    record: str | None
    #: Who recorded it, and when.
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
    #: `OPEN` or `DROPPED`, or None for a log that opened no round.
    status: str | None = None
    #: How the round ended, or None while it has not.
    ended: Ended | None = None
    #: The rulings the log recorded, oldest first. A project's are its register.
    rulings: list[Ruling] = field(default_factory=list)
    #: The questions the log holds, in the order they were asked. A project's are
    #: its list.
    questions: list[Question] = field(default_factory=list)
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
    state.status = OPEN


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
    asked = next((event for event in state.history if event["type"] == "question.asked"
                  and event.get("id") == question.id), {})
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
            blocks = (_widest(blocks, change.blocks)
                      if change.ruling is None and pending else change.blocks)
        ruling = ruling if change.ruling is None else change.ruling
        moved_to = moved_to if change.moved_to is None else change.moved_to
    return replace(question, state=reads, until=until, blocks=blocks, ruling=ruling,
                   moved_to=moved_to)


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
            ruling: str | None) -> None:
    """Apply to the question `question_id` what `event` says of it, and add the change
    to its record. `ruling` is the id of the ruling the event is, None for an event
    that is no ruling."""
    for place, question in enumerate(state.questions):
        if question.id == question_id:
            break
    else:
        # A change to a question the log never asked changes nothing.
        return
    change = Change(state=event.get("state"), blocks=_blocks(event.get("blocks")),
                    until=event.get("until"), ruling=ruling,
                    record=event.get("record"), actor=event["actor"], at=event["at"],
                    moved_to=event.get("moved_to"),
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


#: The handler of each event type. Every type in `events.TYPES` has a row.
_HANDLERS: dict[str, Callable[[State, dict], None]] = {
    "round.opened": _round_opened,
    "flow.changed": _kept,
    "design.frozen": _kept,
    "verdict.recorded": _kept,
    "question.asked": _question_asked,
    "question.changed": _question_changed,
    "stage.declared": _kept,
    "stage.changed": _kept,
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
    "thread.decided": _kept,
    "repair": _kept,
    "round.closed": _kept,
}

__all__ = ["OPEN", "DROPPED", "Ended", "Ruling", "Change", "Question", "State", "fold",
           "without"]
