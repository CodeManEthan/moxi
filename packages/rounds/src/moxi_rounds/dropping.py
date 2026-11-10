"""Dropping a round: ending it short of its work.

`drop("<project>/<slug>", by=..., record=..., actor=...)` withdraws a round. From then
on the round is dropped, which is not closed: it stays on record as it was, takes no
new work, and a correction of one of its rulings is still recorded.

**Whose act.** `by` is `OPERATOR` or `LEAD`. The operator drops any round, and `record`
is the pointer to where he said so. The lead drops only a round that holds none of the
operator's acts, and `record` points to why. What counts as his (`his_acts`): a ruling,
a skip among them; a question asked with his pointer; an opening on his go; a list of
stages he gave when a question was adopted; a build that reached its target or may
have; a decision on such a build; a waiting item that is still open. A round that holds
any of these is his to drop, and the lead's drop is refused with the list.

**Its questions.** Every question that is open or parked goes to the project's list in
the state it has, with where it came from and what it held up kept, and holds up
nothing there: the stages it held ended with the round. The operator, and only he, may
have them withdrawn instead (`withdraw_questions`).

**What is written.** The project's log gets one `question.asked` for each question
that goes to its list, exactly as a move of the question writes it. The round's log
gets one event, `round.dropped`, which is the whole of the drop there: who, the
pointer, and where each question went. The questions are appended first, and both
locks are held throughout, the round's taken first.

**A call that was stopped.** Stopped before `round.dropped`, the round is still open
and its questions are asked in both logs, so nothing was let go. The same call run
again finds each question already on the list by the record it names, appends what is
missing and then the drop: no question is brought twice.

`left_behind` gives the questions on a project's list that came from a round since
dropped, for the opening of a later round to print: whoever takes that work up adopts
them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import ending, folders, index, moving, pointers, questions, write
from . import settings as own
from . import state as states
from .errors import BadArgument, BadName, NoLog, NoRound, Unmet

#: Who a drop is by.
LEAD = "lead"
OPERATOR = "operator"

#: Where a drop says a question went when it was withdrawn with its round.
WITHDRAWN = questions.WITHDRAWN

#: The states of a finish action that may have put a build on its target: only one
#: that failed is known not to have.
_MAY_HAVE_LANDED = ("started", "ok", "found")
#: The state a waiting item is marked with when it is done.
_DONE = "done"


@dataclass(frozen=True)
class Dropped:
    """A round as it was dropped: its `name`, who it was `by` and the `record` given,
    where each of its open and parked questions `went` (`(Q<n>, P<n>)`, or
    `(Q<n>, WITHDRAWN)`), the `event` that dropped it and every one of the `events`
    written. `finished` says the call completed a drop that an earlier call was stopped
    in the middle of. `failed` pairs each after-append callback that raised with what
    it raised."""

    name: str
    by: str
    record: str
    went: tuple[tuple[str, str], ...]
    event: dict
    events: tuple[dict, ...]
    finished: bool = False
    failed: tuple[tuple[Callable, Exception], ...] = ()


@dataclass(frozen=True)
class Left:
    """One question on a project's list that came from a round since dropped: the
    `question` as the list has it, and the `state` it reads as now."""

    question: states.Question
    state: str


def drop(name: str, *, by: str, record: str | None, actor: str,
         withdraw_questions: bool = False, settings=None,
         wait_s: float = write.WAIT_S, at: str | None = None) -> Dropped:
    """Drop the round `name`, `<project>/<slug>`, and return it as dropped.

    - `by`: `OPERATOR` or `LEAD`.
    - `record`: the operator's pointer to where he dropped it; for the lead's drop, a
      pointer to why.
    - `actor`: who runs it, the events' actor.
    - `withdraw_questions`: withdraw the round's open and parked questions, where they
      would go to the project's list. The operator's alone.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for each lock; `at`: the events' time.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, and `Refused` when the
    round has ended already, there is no pointer or one of no known shape, the lead
    drops a round that holds an act of the operator's or asks for its questions to be
    withdrawn, or a drop that was stopped while it moved the questions is run again to
    withdraw them. A refusal leaves both logs as they were.
    """
    if not isinstance(name, str) or "/" not in name:
        raise BadArgument(f"{name!r} is not a round",
                          "name the round to drop, as <project>/<slug>")
    project, slug = folders.split(name)
    if by not in (LEAD, OPERATOR):
        raise BadArgument(f"--by {by!r} names neither who may drop a round",
                          f"say --by {LEAD} or --by {OPERATOR}")
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    his = f"moxi rounds drop {name} --by {OPERATOR} --record PTR"
    again = (f"moxi rounds drop {name} --by {by} --record PTR"
             f"{' --withdraw-questions' if withdraw_questions else ''}")

    def closed(other):
        return questions.is_closed(other, settings)

    def has_not_ended(view):
        how = ending.how(view.state)
        if how is None:
            return None
        return Unmet(f"{name} was {how} already, and a round ends once",
                     f"moxi rounds show {name}")

    def has_a_pointer(view):
        if record is None:
            whose = ("to where the operator dropped it" if by == OPERATOR else
                     "to why it is dropped")
            return Unmet(f"a drop carries a pointer {whose}; PTR below is one token: "
                         f"{pointers.SHAPES}", again)
        return pointers.unmet(record, again)

    def is_the_leads_to_drop(view):
        if by != LEAD:
            return None
        held = his_acts(view.state, view.project_state, name)
        if not held:
            return None
        return Unmet(f"{name} holds acts of the operator's, so it is his to drop and "
                     f"not the lead's. It holds: {'; '.join(held)}. PTR below is the "
                     "pointer to where he dropped it", his)

    def withdrawing_is_his(view):
        if by != LEAD or not withdraw_questions:
            return None
        return Unmet("withdrawing a round's questions is the operator's: dropped by "
                     "the lead, they go to the project's list as they are",
                     f"moxi rounds drop {name} --by {LEAD} --record PTR")

    def finishes_as_it_began(view):
        if not withdraw_questions or ending.how(view.state) is not None:
            return None
        there = [f"{one.question} as {one.became}" for one in
                 moving.arrivals(view.state, view.project_state, name, closed=closed)
                 if one.entry is None]
        if not there:
            return None
        return Unmet(f"a drop of {name} was stopped after it put {', '.join(there)} on "
                     f"the list of {home.project}; it is finished as it began, the "
                     "questions moved and not withdrawn",
                     f"moxi rounds drop {name} --by {by} --record PTR")

    finished = [False]

    def dropped(view, result):
        entries = []
        if withdraw_questions:
            went = {question.id: WITHDRAWN for question in view.state.questions
                    if question.state in (questions.OPEN, questions.PARKED)}
        else:
            found = moving.arrivals(view.state, view.project_state, name, closed=closed)
            finished[0] = any(one.entry is None for one in found)
            # The arrivals go first: stopped after any of them, the round is still
            # open, its questions are asked in both logs, and nothing was let go.
            entries += [one.entry for one in found if one.entry is not None]
            went = {one.question: one.became for one in found}
        # One event is the whole of the drop in the round's log.
        entries.append(write.Entry("round.dropped", {"by": by, "record": record,
                                                     "questions": went}))
        return entries

    try:
        written = write.record(
            write.Where(home.project, home.folder, slug), actor=actor,
            gates=[has_not_ended, has_a_pointer, is_the_leads_to_drop,
                   withdrawing_is_his, finishes_as_it_began],
            event=dropped, with_project=True, wait_s=wait_s, at=at)
    except NoLog:
        # Only a round's log can be missing here: a project's reads as empty.
        raise NoRound(name) from None
    event = written.events[-1]
    return Dropped(name=name, by=by, record=record,
                   went=tuple(event["questions"].items()), event=event,
                   events=written.events, finished=finished[0], failed=written.failed)


# -- what makes a round the operator's to drop ---------------------------------------------

def his_acts(round_state: states.State, project_state: states.State,
             name: str) -> list[str]:
    """What the round `name` holds that is the operator's, each in words; none for a
    round the lead may drop. `round_state` and `project_state` are the two logs'
    states. This is the one place that says what makes a round his to drop."""
    held = []
    for event in round_state.history:
        if event["type"] == "round.opened" and event.get("record") is not None:
            held.append(f"its opening, on his go ({event['record']})")
    skips = [ruling.id for ruling in round_state.rulings
             if (ruling.rules or "").startswith(_SKIP)]
    rulings = [ruling.id for ruling in round_state.rulings if ruling.id not in skips]
    if rulings:
        held.append(f"his ruling{_s(rulings)} {', '.join(rulings)}")
    if skips:
        held.append(f"his skip{_s(skips)} {', '.join(skips)}")
    asked = [question.id for question in round_state.questions
             if question.record is not None]
    if asked:
        held.append(f"{', '.join(asked)}, asked with his pointer")
    # The list he gave for an adopted question is his ruling in the project's log,
    # which names the question it became here.
    for question in project_state.questions:
        for change in question.changes:
            if change.ruling is not None and (change.moved_to or "").startswith(
                    f"{name}:"):
                held.append(f"{change.moved_to.rpartition(':')[2]}, adopted with the "
                            f"list he gave ({change.ruling} of the project's register)")
    return [*held, *_from_builds(round_state)]


def _from_builds(round_state: states.State) -> list[str]:
    """What a round's builds make his: a build that reached its target or may have, a
    decision on such a build, and a waiting item that is still open."""
    held = []
    finished = questions.finished_stages(round_state)
    may_have = {event.get("stage") for event in round_state.history
                if event["type"] == "finish.action" and event.get("kind") == "merge"
                and event.get("state") in _MAY_HAVE_LANDED} - finished - {None}
    held += [f"the build of {stage}, which is finished" for stage in sorted(finished)]
    held += [f"the build of {stage}, whose merge may have reached its target"
             for stage in sorted(may_have)]
    reached = finished | may_have
    waits: dict[str, bool] = {}
    for event in round_state.history:
        if event["type"] == "verdict.recorded":
            for decision in event.get("decisions") or ():
                stage = decision.get("stage") or event.get("stage")
                if stage in reached:
                    held.append(f"the decision {stage}.{decision.get('id')}, on a "
                                "build that reached its target")
            for wait in event.get("waits") or ():
                waits.setdefault(f"{event.get('stage')}.{wait.get('id')}", True)
        elif event["type"] == "wait.marked" and event.get("id") in waits:
            waits[event["id"]] = event.get("state") != _DONE
    still = [wait for wait, is_open in waits.items() if is_open]
    if still:
        held.append(f"the open waiting item{_s(still)} {', '.join(still)}")
    return held


#: What starts the item of a ruling that is a skip of a step.
_SKIP = "skip:"


def _s(ids: list) -> str:
    return "" if len(ids) == 1 else "s"


# -- what a dropped round left on the project's list ---------------------------------------

def left_behind(project_state: states.State, home: folders.Home, *,
                settings=None, env=None) -> tuple[Left, ...]:
    """The questions on a project's list that read as open or parked and came from a
    round of the project that was dropped, in the order the list has them.
    `project_state` is the state of the project's log and `home` its rounds folder. A
    round that can't be found or read is not known to be dropped, and its questions
    are left out. Nothing is written."""
    dropped: dict[str, bool] = {}

    def was_dropped(slug: str) -> bool:
        if slug not in dropped:
            try:
                found = index.read(home.project, home.folder, slug, env=env)
            except NoLog:
                found = None
            dropped[slug] = found is not None and found.status == states.DROPPED
        return dropped[slug]

    left = []
    for question in project_state.questions:
        source, colon, _id = (question.moved_from or "").rpartition(":")
        if not colon or question.state not in (questions.OPEN, questions.PARKED):
            continue
        try:
            project, slug = folders.split(source)
        except BadName:
            continue
        if project == home.project and was_dropped(slug):
            left.append(Left(question, questions.current(
                question, project_state,
                closed=lambda other: questions.is_closed(other, settings))))
    return tuple(left)


__all__ = ["LEAD", "OPERATOR", "WITHDRAWN", "Dropped", "Left", "drop", "his_acts",
           "left_behind"]
