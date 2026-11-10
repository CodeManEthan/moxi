"""Questions: what a question record says, read from a log's state.

A question is one sentence put to the operator. Its record is six things: an id (`Q<n>`
in a round's log, `P<n>` in a project's), the sentence, where it came from, what it holds
up (`blocks`), its state, and who asked it and when. The fold keeps each as a
`state.Question`; this module reads them.

**State.** `OPEN`, `PARKED`, `RULED` or `WITHDRAWN`; a question that was moved to another
log is `MOVED` in the one it left, where it is asked no longer and holds nothing. The
log records a parking with what
the question waits for, when it waits for something: a stage, or another round. Nothing
is written when that is reached, so the recorded state stays `parked`; `current` gives
the state as it reads now, which is `open` again once the stage is finished or the round
is closed.

**What it holds up.** `ALL` (every stage the round has or later gains), `NONE` (no
stage), or a tuple of stage ids. A question that did not say is `ALL`. A project's
question is `NONE`. `holds_every(new, old)` is the test of a widening and, with its
arguments swapped, of a narrowing.

Nothing here decides whether a question stops a step: `holds` does, from `current` and
`Question.blocks`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping

from . import folders, log
from . import state as states
from .errors import BadArgument, RoundsError

#: The four states of a question.
OPEN = "open"
PARKED = "parked"
RULED = "ruled"
WITHDRAWN = "withdrawn"
#: What a question's state is in a log it was moved out of. It is no state of a
#: question that is still asked: the record it became, in the other log, has one of
#: the four.
MOVED = "moved"
#: What a question is shown as when its record reads as open or parked in a round that
#: has ended. No log records it: a correction made after the end can leave a question
#: so, and nothing reopens the round or moves the question. It waits on nobody there;
#: asked again in a new round, it does.
LEFT = "left"

#: What `blocks` holds for a question that holds up every stage, and for one that holds
#: up none.
ALL = "all"
NONE = "none"

#: The id of a round's question, and of a project's.
ROUND_ID = re.compile(r"Q([1-9][0-9]*)")
PROJECT_ID = re.compile(r"P([1-9][0-9]*)")

#: A stage's id.
STAGE = re.compile(r"B[1-9][0-9]*")

#: What starts the name of the round a parked question waits for.
UNTIL_ROUND = "round:"

#: What starts the item of a ruling that takes a stage out of the plan; the stage's id
#: follows.
UNSTAGE = "unstage:"
#: What starts the item of a ruling that takes one stage off what another waits for:
#: the stage's id, `=`, and the id of the stage it waits for no longer.
UNAFTER = "unafter:"

Blocks = str | tuple[str, ...]


def find(state: states.State, question_id: str) -> states.Question | None:
    """The question of `state` with that id, or None."""
    for question in state.questions:
        if question.id == question_id:
            return question
    return None


def next_id(letter: str, state: states.State) -> str:
    """The next id of a log's questions, `letter` being `Q` for a round's and `P` for a
    project's: one past the highest the log holds, so an id is never given twice."""
    numbered = re.compile(rf"{letter}([1-9][0-9]*)")
    numbers = [int(found.group(1)) for found in
               (numbered.fullmatch(question.id or "") for question in state.questions)
               if found]
    return f"{letter}{max(numbers, default=0) + 1}"


# -- what a question holds up ------------------------------------------------------------

def parse_blocks(text: str, option: str = "--blocks") -> Blocks:
    """What `text` says a question holds up: `ALL`, `NONE`, or the stage ids of `B2,B3`
    in the order given. `option` is what the value was given with, for the error. Raises
    `BadArgument` for an empty list, an id that is not `B<n>`, or one named twice."""
    if text in (ALL, NONE):
        return text
    ids = tuple(part.strip() for part in text.split(","))
    wrong = [one for one in ids if not STAGE.fullmatch(one)]
    if wrong or len(set(ids)) != len(ids):
        problem = (f"{wrong[0]!r} is not a stage's id" if wrong else
                   "a stage is named twice")
        raise BadArgument(f"{option} {text!r} can't be read: {problem}",
                          f"name stages as B<n>, separated by commas, or say {ALL} or "
                          f"{NONE}")
    return ids


def said(blocks: Blocks | None) -> str:
    """`blocks` as it is typed and printed: `all`, `none`, or `B2,B3`."""
    return blocks if isinstance(blocks, str) else ",".join(blocks or ())


def stored(blocks: Blocks) -> str | list[str]:
    """`blocks` as an event holds it."""
    return blocks if isinstance(blocks, str) else list(blocks)


def holds_every(new: Blocks | None, old: Blocks | None) -> bool:
    """Whether `new` holds up every stage `old` does. `ALL` covers stages the round has
    not gained yet, so no list holds every stage `ALL` does."""
    if old == NONE or new == ALL:
        return True
    if old == ALL or new == NONE or old is None or new is None:
        return False
    return set(old) <= set(new)


# -- what a round's log says of its stages -----------------------------------------------
# The readers below take what they need from the stages the log declared (each by an
# event of its own or by the frozen design version that first carried it), the history
# and the rulings. They are the one place that says which stages a round has, which are
# finished and which left the plan.

def recorded_stages(state: states.State) -> tuple[str, ...]:
    """The ids of the stages the round's log declared, in the order it declared them."""
    return tuple(stage.id for stage in state.stages)


def finished_stages(state: states.State) -> frozenset[str]:
    """The ids of the stages whose finish the round's log records as done."""
    return frozenset(event["stage"] for event in state.history
                     if event["type"] == "finish.done"
                     and isinstance(event.get("stage"), str))


def left_stages(state: states.State) -> frozenset[str]:
    """The ids of the stages that left the round's plan: each one a verdict took out
    when it applied what a design's block left out, and each one a ruling takes out.
    Nothing else takes a stage out, and a stage that left does not come back."""
    removed = {one.id for verdict in state.verdicts for one in verdict.applied
               if one.what == states.STAGE}
    ruled_out = {ruling.rules[len(UNSTAGE):] for ruling in state.rulings
                 if (ruling.rules or "").startswith(UNSTAGE)
                 and STAGE.fullmatch(ruling.rules[len(UNSTAGE):])}
    return frozenset(removed | ruled_out)


def unrecorded(blocks: Blocks | None, state: states.State) -> tuple[str, ...]:
    """The stage ids `blocks` names that are not recorded stages of the round."""
    if blocks is None or isinstance(blocks, str):
        return ()
    have = recorded_stages(state)
    return tuple(one for one in blocks if one not in have)


def stages_said(state: states.State) -> str:
    """The round's stages in words, for a refusal."""
    have = recorded_stages(state)
    return f"its stages are {', '.join(have)}" if have else "it has no stage yet"


# -- the state as it reads now -----------------------------------------------------------

def is_closed(name: str, settings=None) -> bool:
    """Whether the round `name` is closed: its log holds its closing. A round that can't
    be found or read is not."""
    try:
        project, slug = folders.split(name)
        home = folders.home(project, settings)
        events = log.read_round(log.round_log(home.round_folder(slug)))
    except RoundsError:
        return False
    return any(event["type"] == "round.closed" for event in events)


def reached(question: states.Question, state: states.State, *,
            closed: Callable[[str], bool] | None = None) -> bool:
    """Whether what a parked question waits for has come: the stage is finished, or the
    round is closed. `state` is the state of the log that holds the question, and
    `closed` answers whether a round, by name, is closed (`is_closed` when left out).
    False for a question that waits for nothing."""
    until = question.until
    if question.state != PARKED or not until:
        return False
    if until.startswith(UNTIL_ROUND):
        return bool((closed or is_closed)(until[len(UNTIL_ROUND):]))
    return until in finished_stages(state)


def current(question: states.Question, state: states.State, *,
            closed: Callable[[str], bool] | None = None) -> str | None:
    """The question's state as it reads now: the recorded one, except that a parked
    question reads `OPEN` once what it waits for has come. The arguments are `reached`'s.
    """
    return OPEN if reached(question, state, closed=closed) else question.state


def shown_as(reads: str | None, *, ended: bool) -> str | None:
    """The state a question is shown as, given the state it reads as now and whether
    its round has ended: `LEFT` for one that reads as open or parked in a round that
    has, so that no listing gives it as waiting on the operator."""
    return LEFT if ended and reads in (OPEN, PARKED) else reads


def called(state: str | None) -> str:
    """What a state is called where questions are counted or headed."""
    return "waiting for a new round" if state == LEFT else str(state)


# -- what the index keeps ----------------------------------------------------------------

def waiting(state: states.State) -> Iterator[tuple[str, Mapping]]:
    """The questions of `state` that are recorded as open or parked, each as `(id, body)`
    for the index. The body holds the record as the log leaves it, at the depth it has:
    the sentence, where it came from, what it holds up, the recorded state, who asked it
    and when; then, each only when the question has it, what a parked one waits for,
    the options it offers now as `{"key", "text"}` in the order they were added, and
    the record it was moved here from with what it held up there.

    A parked question's state is the recorded one. When it waits for a stage of its own
    round and that stage is finished, the body says `reached`; whether another round it
    waits for is closed is not in this log, so whoever lists the row reads that
    (`listed`)."""
    for question in state.questions:
        if question.state not in (OPEN, PARKED) or question.id is None:
            continue
        body = {"text": question.text, "from": question.origin,
                "blocks": None if question.blocks is None else stored(question.blocks),
                "state": question.state, "by": question.by, "at": question.at}
        if question.until is not None:
            body["until"] = question.until
            if (not question.until.startswith(UNTIL_ROUND)
                    and reached(question, state)):
                body["reached"] = True
        if question.options:
            body["options"] = [{"key": key, "text": line}
                               for key, line in question.options]
        if question.moved_from is not None:
            body["moved_from"] = question.moved_from
        if question.held is not None:
            body["held"] = stored(question.held)
        yield question.id, body


def listed(body: Mapping, *, closed: Callable[[str], bool] | None = None) -> str | None:
    """The state a question reads as now, from the body the index keeps for it: what
    `current` gives from the question and its log. A parked question reads `OPEN` once
    the stage it waits for is finished, which the body says, or the round it waits for
    is closed, which `closed` answers by the round's name (`is_closed` when left out)."""
    until = body.get("until")
    if body.get("state") != PARKED or not until:
        return body.get("state")
    if until.startswith(UNTIL_ROUND):
        came = bool((closed or is_closed)(until[len(UNTIL_ROUND):]))
    else:
        came = body.get("reached") is True
    return OPEN if came else PARKED


__all__ = ["OPEN", "PARKED", "RULED", "WITHDRAWN", "MOVED", "LEFT", "ALL", "NONE", "ROUND_ID",
           "PROJECT_ID", "STAGE", "UNTIL_ROUND", "UNSTAGE", "find", "next_id",
           "parse_blocks", "said", "stored", "holds_every", "recorded_stages",
           "finished_stages", "left_stages", "unrecorded", "stages_said", "is_closed",
           "reached", "current", "shown_as", "called", "waiting", "listed"]
