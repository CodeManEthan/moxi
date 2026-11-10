"""Moving a question between a round and its project's list.

A question lives in one log. Two calls carry one to the other log of the same project:

- `adopt("<project>/<slug>", "P3", actor=...)` brings a question of the project's list
  into the round, as a new `Q<n>`.
- `move("<project>", "<project>/<slug>:Q2", actor=...)` puts a round's question on the
  project's list, as a new `P<n>`.

**What is carried.** The sentence, where it came from, who asked it and when, the
operator's pointer when the question is his own, the options it offers and the keys
retired from it, and its state as it reads now: open, or parked. A parked question that
waits for another round still waits for it; a wait for a stage of the round it leaves
is not carried, since the stage is not one of the log it arrives in. A ruled or
withdrawn question is not moved.

**What it holds up afterwards.** On the project's list, nothing: the record keeps, as
`held`, what it held up in the round it left. In a round, every stage unless the
adoption names a list. A question that was asked on the project's list never held a
stage and takes any list. One that came from a round arrives holding up every stage, so
a list for it narrows what it holds, and narrowing is the operator's: it needs his
pointer.

**Whose act.** Anyone adopts. A move out of a round ends what the question holds up
there, so it is the operator's and carries his pointer, with one exception: anyone may
move a question whose move ends nothing, now or later. That is a parked question that
holds up a list of stages, each of them finished or out of the plan.

**The two records.** The log the question arrives in gets one `question.asked`, which
names the record it was (`moved_from`). The log it leaves gets one event that makes the
question `moved` there and names the record it became (`moved_to`): a `question.changed`,
or, when the call carries the operator's pointer, a `ruling.recorded` that is an act on
the process and is the whole of the change, as every ruling of a question is. Both locks
are held throughout, the round's taken first.

**A call that was stopped.** The arrival is appended first. A call stopped between the
two appends has left the question in both logs: still asked where it was, and asked
again where it was going, so nothing it held up was let go. The same call run again
finds the arrival by the record it names, appends only what is missing, and returns the
arrival that is there: the question is never brought a second time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import corrections, ending, folders, pointers, questions, ruling, state, write
from . import settings as own
from .errors import BadArgument, NoLog, NoRound, Unmet

#: What starts the item of a ruling that is a move of a question; the question's id
#: follows.
MOVE = corrections.MOVE


@dataclass(frozen=True)
class Moved:
    """A question after its move: its new `id`, the `name` of the round or project
    whose log now holds it, the record it was (`source`: `P<n>`, or
    `<project>/<slug>:Q<n>`), the `question` as its new log has it, the id of the
    `ruling` the move was recorded as (None for a move that is not the operator's), and
    the `events` written. `finished` says the call completed a move that an earlier
    call was stopped in the middle of. `failed` pairs each after-append callback that
    raised with what it raised."""

    id: str
    name: str
    source: str
    question: state.Question
    ruling: str | None
    events: tuple[dict, ...]
    finished: bool = False
    failed: tuple[tuple[Callable, Exception], ...] = ()


@dataclass(frozen=True)
class Arrival:
    """One question of a round on its way to the project's list: the `question` it is
    in the round, the id it has on the list (`became`), and the `entry` to append to
    the project's log, None when the list already holds it."""

    question: str
    became: str
    entry: write.Entry | None


def adopt(target: str, question_id: str, *, actor: str, blocks: str | None = None,
          record: str | None = None, settings=None, wait_s: float = write.WAIT_S,
          at: str | None = None) -> Moved:
    """Bring the question `question_id` of the project's list into the round `target`,
    `<project>/<slug>`, and return it as the round has it.

    - `actor`: who does it, the events' actor. The question keeps its own asker.
    - `blocks`: the stages it holds up in the round, `B2,B3` or `all`; every stage when
      left out.
    - `record`: the operator's pointer for the list, which a list needs when the
      question came from a round.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for each lock; `at`: the events' time.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, and `Refused` when the
    list has no such question, the question is ruled, withdrawn or already moved, a
    stage named is not the round's, a list for a question that came from a round has
    no pointer, or the pointer has no shape a pointer has. A refusal leaves both logs
    as they were.
    """
    if not isinstance(target, str) or "/" not in target:
        raise BadArgument(f"{target!r} is not a round",
                          "name the round that adopts the question, as "
                          "<project>/<slug>")
    project, slug = folders.split(target)
    if not questions.PROJECT_ID.fullmatch(question_id or ""):
        raise BadArgument(f"{question_id!r} is not the id of a project's question",
                          "name the question to adopt as P<n>")
    if blocks == questions.NONE:
        raise BadArgument("an adopted question holds up stages of the round that "
                          "adopts it, and none names no stage",
                          f"leave --blocks out for every stage, or name stages as "
                          f"B2,B3; to ask a question that holds up no stage, ask it "
                          f"with --blocks {questions.NONE}")
    if record is not None and blocks is None:
        raise BadArgument("--record is the operator's pointer for the list of stages "
                          "an adopted question holds up, and no list is given",
                          "give the list with --blocks LIST, or leave --record out")
    wanted = questions.ALL if blocks is None else questions.parse_blocks(blocks)

    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    again = f"moxi rounds ask {name} --adopt {question_id}"
    listing = f"moxi rounds list {home.project} --questions"

    def found(view):
        return questions.find(view.project_state, question_id)

    def exists(view):
        if found(view) is not None:
            return None
        return Unmet(f"{home.project} has no question {question_id}", listing)

    def names_stages_of_the_round(view):
        missing = questions.unrecorded(wanted, view.state)
        if not missing:
            return None
        return Unmet(f"{', '.join(missing)} is not a stage of {name}: "
                     f"{questions.stages_said(view.state)}", f"{again} [--blocks LIST]")

    def a_list_that_narrows_is_his(view):
        with_pointer = f"{again} --blocks {questions.said(wanted)} --record PTR"
        if record is not None:
            return pointers.unmet(record, with_pointer)
        question = found(view)
        if question is None or question.moved_from is None or wanted == questions.ALL:
            return None
        return Unmet(
            f"{question_id} came from {question.moved_from}, so it arrives holding up "
            "every stage; a list for it narrows that, which only the operator does. "
            f"Leave --blocks out, or give his pointer; PTR below is one token: "
            f"{pointers.SHAPES}", with_pointer)

    def finishes_with_the_list_it_began_with(view):
        question, arrived = found(view), _arrived(view.state, question_id)
        if (question is None or arrived is None or arrived.blocks == wanted
                or question.state == questions.MOVED):
            return None
        had = questions.said(arrived.blocks)
        return Unmet(
            f"{question_id} is already in {name} as {arrived.id}, holding up {had}: "
            "the command that brought it was stopped before it was done, and is "
            "finished by giving what it holds up there now",
            f"{again} --blocks {had}")

    return _carry(
        home, slug, question_id, into_round=True, record=record, actor=actor,
        gates=[ending.takes_new_work(name, "a question"), exists,
               _still_asked(found, question_id, listing),
               names_stages_of_the_round, a_list_that_narrows_is_his,
               finishes_with_the_list_it_began_with],
        blocks=wanted, settings=settings, wait_s=wait_s, at=at)


def move(target: str, source: str, *, actor: str, record: str | None = None,
         settings=None, wait_s: float = write.WAIT_S, at: str | None = None) -> Moved:
    """Put the question `source`, `<project>/<slug>:Q<n>`, on the list of the project
    `target`, which is the round's own, and return it as the list has it.

    - `actor`: who does it, the events' actor. The question keeps its own asker.
    - `record`: the operator's pointer. Without one the move is accepted only when it
      ends nothing the question holds up, now or later: the question is parked, it
      holds up a list of stages, and each of them is finished or out of the plan.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for each lock; `at`: the events' time.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, and `Refused` when the
    round has no such question, the question is ruled, withdrawn or already moved, the
    pointer has no shape a pointer has, or there is no pointer and the move would end
    something the question holds up. A refusal leaves both logs as they were.
    """
    if not isinstance(target, str) or not target.strip() or "/" in target:
        raise BadArgument(f"{target!r} is not a project",
                          "name the project whose list takes the question, by its key")
    round_name, colon, question_id = (source.rpartition(":") if isinstance(source, str)
                                      else ("", "", ""))
    if not colon or not questions.ROUND_ID.fullmatch(question_id):
        raise BadArgument(f"{source!r} does not name a round's question",
                          "name the question to move as <project>/<slug>:Q<n>")
    project, slug = folders.split(round_name)

    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    if folders.home(target, settings).project != home.project:
        raise BadArgument(f"{name} is not a round of {target}: a round's question "
                          "moves to the list of the round's own project",
                          f"moxi rounds ask {home.project} --move {name}:{question_id}")
    again = (f"moxi rounds ask {home.project} --move {name}:{question_id} "
             "--record PTR")
    show = f"moxi rounds show {name}"

    def found(view):
        return questions.find(view.state, question_id)

    def exists(view):
        if found(view) is not None:
            return None
        return Unmet(f"{name} has no question {question_id}", show)

    def closed(other):
        return questions.is_closed(other, settings)

    def is_his_or_ends_nothing(view):
        if record is not None:
            return pointers.unmet(record, again)
        question = found(view)
        if question is None or question.state not in (questions.OPEN, questions.PARKED):
            return None
        short = _ends_something(question, view.state, closed)
        if not short:
            return None
        return Unmet(
            f"a move of {question_id} out of {name} would end what it holds up there, "
            "which only the operator does. Without his pointer a question is moved "
            "only when it is parked and holds up a list of stages that are each "
            f"finished or out of the plan, and {'; '.join(short)}. PTR below is one "
            f"token: {pointers.SHAPES}", again)

    return _carry(
        home, slug, question_id, into_round=False, record=record, actor=actor,
        gates=[ending.takes_new_work(name, "a move of one of its questions"), exists,
               _still_asked(found, question_id, show), is_his_or_ends_nothing],
        blocks=questions.NONE, settings=settings, wait_s=wait_s, at=at)


def arrivals(round_state: state.State, project_state: state.State, name: str, *,
             closed: Callable[[str], bool] | None = None) -> tuple[Arrival, ...]:
    """What puts every open and parked question of a round on its project's list, for
    a verb that ends the round and writes both logs itself: one `Arrival` for each, in
    the order asked.

    - `round_state`, `project_state`: the two logs' states, as read under both locks.
    - `name`: the round's name, `<project>/<slug>`, under the project's stored key.
    - `closed`: answers whether a round, by name, is closed, for a question parked
      until another round closes; `questions.is_closed` when left out.

    A question the list already holds, by the record it names, gets no entry and
    keeps the id it has there, so a verb run again after it was stopped brings none
    twice. What says, in the round's log, where each question went is the caller's.
    """
    found = []
    taken = int(questions.next_id("P", project_state)[1:])
    for question in round_state.questions:
        if question.state not in (questions.OPEN, questions.PARKED):
            continue
        source = f"{name}:{question.id}"
        arrived = _arrived(project_state, source)
        if arrived is not None:
            found.append(Arrival(question=question.id, became=arrived.id, entry=None))
            continue
        fields = _arrival(question, round_state, new_id=f"P{taken}", source=source,
                          blocks=questions.NONE, held_up=question.blocks,
                          closed=closed)
        found.append(Arrival(question=question.id, became=f"P{taken}",
                             entry=write.Entry("question.asked", fields,
                                               log=write.PROJECT)))
        taken += 1
    return tuple(found)


# -- the rules both share ----------------------------------------------------------------

def _still_asked(found, question_id: str, listing: str) -> write.Gate:
    """The gate that the question is one that can be moved: open or parked, and in
    this log still."""
    def gate(view):
        question = found(view)
        if question is None or question.state in (questions.OPEN, questions.PARKED):
            return None
        if question.state == questions.MOVED:
            return Unmet(f"{question_id} was moved already and is now "
                         f"{question.moved_to}", listing)
        return Unmet(f"{question_id} is {question.state}, and only a question that is "
                     "open or parked is moved", listing)
    return gate


def _ends_something(question: state.Question, held: state.State,
                    closed: Callable[[str], bool]) -> list[str]:
    """Why a move of a round's question would end something it holds up, now or later:
    each condition it falls short of, in words; none when the move ends nothing."""
    short = []
    if questions.current(question, held, closed=closed) != questions.PARKED:
        short.append(f"{question.id} is open" if question.state == questions.OPEN else
                     f"{question.id} reads as open, since what it was parked until "
                     "has come")
    if question.blocks == questions.ALL:
        short.append(f"{question.id} holds up every stage, any the round gains later "
                     "included")
    elif question.blocks == questions.NONE or not question.blocks:
        short.append(f"{question.id} holds up no stage, so no finished stage shows "
                     "that what it waits on is done")
    else:
        done = questions.finished_stages(held) | questions.left_stages(held)
        owed = [stage for stage in question.blocks if stage not in done]
        if owed:
            short.append(f"{', '.join(owed)} is neither finished nor out of the plan")
    return short


def _arrived(held: state.State, source: str) -> state.Question | None:
    """The question of `held` that was brought from the record `source`, or None."""
    for question in held.questions:
        if question.moved_from == source:
            return question
    return None


def _arrival(question: state.Question, held: state.State, *, new_id: str, source: str,
             blocks: questions.Blocks, held_up: questions.Blocks | None = None,
             closed: Callable[[str], bool] | None = None) -> dict[str, object]:
    """The fields of the `question.asked` that brings `question`, of the log whose
    state is `held`, into another log as `new_id`, from the record `source`. `blocks`
    is what it holds up there, and `held_up` what it held up in the round it leaves."""
    now = questions.current(question, held, closed=closed)
    fields: dict[str, object] = {
        "id": new_id, "text": question.text, "from": question.origin,
        "blocks": questions.stored(blocks), "state": now, "asked_by": question.by,
        "asked_at": question.at, "moved_from": source}
    if question.record is not None:
        fields["record"] = question.record
    if question.options:
        fields["options"] = dict(question.options)
    if question.retired:
        fields["retired"] = list(question.retired)
    # A wait for another round means the same in any log. A wait for a stage is for a
    # stage of the round the question leaves, and names nothing where it arrives.
    if now == questions.PARKED and (question.until or "").startswith(
            questions.UNTIL_ROUND):
        fields["until"] = question.until
    if held_up is not None:
        fields["held"] = questions.stored(held_up)
    return fields


def _carry(home: folders.Home, slug: str, question_id: str, *, into_round: bool,
           record: str | None, actor: str, gates, blocks: questions.Blocks, settings,
           wait_s: float, at: str | None) -> Moved:
    """Write a move: the arrival in one log, unless it is there, then the leaving in
    the other."""
    name = f"{home.project}/{slug}"
    source = question_id if into_round else f"{name}:{question_id}"
    here, there = ((write.PROJECT, write.ROUND) if into_round else
                   (write.ROUND, write.PROJECT))

    def sides(view):
        return ((view.project_state, view.state) if into_round else
                (view.state, view.project_state))

    def moved(view, result):
        leaves, takes = sides(view)
        question = questions.find(leaves, question_id)
        arrived = _arrived(takes, source)
        entries = []
        if arrived is None:
            new_id = questions.next_id("Q" if into_round else "P", takes)
            fields = _arrival(
                question, leaves, new_id=new_id, source=source, blocks=blocks,
                held_up=None if into_round else question.blocks,
                closed=lambda other: questions.is_closed(other, settings))
            # The arrival goes first: stopped after it, the question is asked in both
            # logs and nothing it held up was let go.
            entries.append(write.Entry("question.asked", fields, log=there))
        else:
            new_id = arrived.id
        change = {"state": questions.MOVED,
                  "moved_to": f"{name}:{new_id}" if into_round else new_id}
        if record is None:
            entries.append(write.Entry("question.changed",
                                       {"id": question_id, **change}, log=here))
        else:
            # The operator's move is a ruling, and one event is the whole of it.
            taken = ruling.next_id("S" if into_round else "C", leaves.rulings)
            entries.append(write.Entry(
                "ruling.recorded",
                {"id": taken, "record": record, "kind": ruling.PROCESS,
                 "rules": MOVE + question_id, "question": question_id, **change},
                log=here))
        return entries

    try:
        written = write.record(write.Where(home.project, home.folder, slug),
                               actor=actor, gates=gates, event=moved, with_project=True,
                               wait_s=wait_s, at=at)
    except NoLog:
        # Only a round's log can be missing here: a project's reads as empty.
        raise NoRound(name) from None
    left = written.events[-1]
    new_id = left["moved_to"].rpartition(":")[2]
    after = written.round_events if into_round else written.project_events
    return Moved(id=new_id, name=name if into_round else home.project, source=source,
                 question=questions.find(state.fold(after), new_id),
                 ruling=left["id"] if left["type"] == "ruling.recorded" else None,
                 events=written.events, finished=len(written.events) == 1,
                 failed=written.failed)


__all__ = ["MOVE", "Moved", "Arrival", "adopt", "move", "arrivals"]
