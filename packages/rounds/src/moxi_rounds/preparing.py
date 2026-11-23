"""Prepared answers: what an agent that looked into a question recommends, recorded
on the question.

`prepare("<project>/<slug>", "Q4", "answer.md", by=...)` reads the file's one data
block (`answer_block`), checks it against the question as the log has it, and appends
one `question.changed` that carries the answer as `prepared`: the block's keys less
`kind` and `question`. It returns the question as the log then has it.

**What a prepared answer changes.** What is shown, and nothing else: not the
question's state, not what it holds up, no step and no hold. Anyone may record one,
and it carries no pointer of the operator's.

**Which one is shown.** The newest. It is current while its options are exactly the
ones the question offers, retired ones not counted (`questions.prepared_now`). Once an
option is added or retired it is prepared for other options and counts as none, and a
new one is recorded. Earlier ones stay in the log.

**A question with no options yet** takes its options from the answer: each
`[[option]]` then carries its line in `text`, two to four of them, and the same event
records them as the question's options, as `ask --option` would.

**When.** While the question is open or parked, and its round has not ended.

`problems(...)` is the check against the question, which `fold` makes of a
`[question.prepared]` in a design's block too, with the same words.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import answer_block, ending, folders, options, questions, state, write
from . import settings as own
from .design_block import Prepared
from .errors import BadArgument, BadBlock, NoLog, NoRound, Problem, Unmet

#: The fixes the refusals give, as the answer's own file writes its options.
_EACH = "one {header} for each option the question offers"
_LINES = (f"give each option its line in text; {options.FEWEST} to {options.MOST} "
          "options")


@dataclass(frozen=True)
class Recorded:
    """A prepared answer as it was recorded: the `name` of the round or project whose
    log holds the question, the `question` as the log now has it, and the `event`
    written. `failed` pairs each after-append callback that raised with what it
    raised."""

    name: str
    question: state.Question
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


def stored(prepared: Prepared) -> dict:
    """A prepared answer as an event stores it, as `state.Prepared.body` gives it
    back: the keys of its block, less `kind` and `question`."""
    return {"by": prepared.by, "model": prepared.model, "recommend": prepared.recommend,
            "sure": prepared.sure, "why": prepared.why, "flips": prepared.flips,
            "read": list(prepared.read),
            "option": [{"key": one.key, **({"text": one.text} if one.text else {}),
                        "costs": one.costs, "forecloses": one.forecloses}
                       for one in prepared.options]}


def problems(prepared: Prepared, question_id: str, *,
             offered: Sequence[tuple[str, str]], retired: Sequence[str] = (),
             header: str = answer_block.OPTION_HEADER) -> list[Problem]:
    """What stands in the way of recording `prepared` on the question `question_id`,
    which offers `offered` (each `(key, line)`) and had `retired` taken out; empty
    when nothing does. `header` is how the answer's file writes an option.

    A question that offers options needs one option of the answer for each of them
    and none for another key, a retired one included; a line given for a key that
    offers another is refused, since a key keeps its line. A question with no options
    needs a line for each of the answer's options, two to four of them, none under a
    retired key."""
    each = _EACH.format(header=header)
    found: list[Problem] = []
    lines = dict(offered)
    given = {one.key for one in prepared.options}
    for one in prepared.options:
        if one.key in retired:
            found.append(Problem(
                one.line_of("key"),
                f"the key {one.key} was retired from {question_id} and is never used "
                "again", each if lines else _LINES))
        elif lines and one.key not in lines:
            found.append(Problem(
                one.line_of("key"),
                f"{question_id} offers no option {one.key}; its options are "
                f"{', '.join(lines)}", each))
        elif lines and one.text is not None and one.text != lines[one.key]:
            found.append(Problem(
                one.line_of("text"),
                f"{question_id} offers {lines[one.key]!r} under {one.key}, and a key "
                "keeps its line", "leave text out where the question has options"))
        elif not lines and one.text is None:
            found.append(Problem(
                one.line_of("key"),
                f"{question_id} has no options yet, so the answer gives them, and the "
                f"option {one.key} has no text", _LINES))
    if lines:
        found += [Problem(prepared.line,
                          f"{question_id} offers {key}, and the answer has no {header} "
                          "for it", each)
                  for key in lines if key not in given]
    elif not options.FEWEST <= len(prepared.options) <= options.MOST:
        found.append(Problem(
            prepared.line,
            f"{question_id} has no options yet, so the answer gives them, and it gives "
            f"{len(prepared.options)}: a question that has options has "
            f"{options.FEWEST} to {options.MOST}", _LINES))
    return found


def given_options(prepared: Prepared) -> dict[str, str]:
    """The options an answer gives a question that has none: each key with its
    line."""
    return {one.key: one.text for one in prepared.options if one.text}


def prepare(target: str, question_id: str, file: Path | str, *, by: str,
            settings=None, wait_s: float = write.WAIT_S, at: str | None = None
            ) -> Recorded:
    """Record the prepared answer in `file` on the question `question_id` of `target`,
    a round `<project>/<slug>` or a project's key.

    - `file`: a file whose one data block is an `answer` block (`answer_block`).
    - `by`: who records it, the event's actor; the block's own `by` says who prepared
      it.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the lock; `at`: the event's time.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round or project that can't be found, `Refused` when
    the log has no such question, the question is ruled, withdrawn or moved, or its
    round has ended, and `BadBlock` when the file has no answer block, the block does
    not fit, it answers another question, or it does not speak of exactly the options
    the question offers (`problems`). A refusal leaves the log as it was.
    """
    if not isinstance(target, str) or not target.strip():
        raise BadArgument("nothing says whose question it is",
                          "name a round as <project>/<slug>, or a project by its key")
    project, slug = folders.split(target) if "/" in target else (target, None)
    if not options.QUESTION_ID.fullmatch(question_id or ""):
        raise BadArgument(f"{question_id!r} is not the id of a question",
                          "name a round's question as Q<n>, a project's as P<n>")
    block = answer_block.read(file)
    if block.question != question_id:
        raise BadBlock(block.file, answer_block.WHAT, [Problem(
            block.question_line,
            f"the answer is for {block.question}, and it is recorded on {question_id}",
            f"name the question the file answers: {question_id}, or record it on "
            f"{block.question}")])

    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = home.project if slug is None else f"{home.project}/{slug}"
    listing = (f"moxi rounds show {name}" if slug is not None else
               f"moxi rounds list {name} --questions")

    def held(view):
        return view.state if slug is not None else view.project_state

    def found(view):
        return questions.find(held(view), question_id)

    def exists(view):
        if found(view) is not None:
            return None
        return Unmet(f"{name} has no question {question_id}", listing)

    def still_asked(view):
        question = found(view)
        if question is None or question.state in (questions.OPEN, questions.PARKED):
            return None
        return Unmet(f"{question_id} is {question.state}; an answer is prepared only "
                     "for a question that is open or parked", listing)

    def fits(view):
        question = found(view)
        wrong = problems(block.prepared, question_id, offered=question.options,
                         retired=question.retired)
        if wrong:
            raise BadBlock(block.file, answer_block.WHAT, wrong)

    def changed(view, _result):
        question = found(view)
        fields: dict[str, object] = {"id": question_id}
        if not question.options:
            fields["options"] = given_options(block.prepared)
        fields["prepared"] = stored(block.prepared)
        return write.Entry("question.changed", fields,
                           log=write.ROUND if slug is not None else write.PROJECT)

    try:
        written = write.record(
            write.Where(home.project, home.rounds_dir, slug), by=by,
            gates=[ending.takes_new_work(name, "a prepared answer"), exists,
                   still_asked],
            act=fits, event=changed, wait_s=wait_s, at=at)
    except NoLog:
        # Only a round's log can be missing here: a project's reads as empty.
        raise NoRound(name) from None
    events = written.round_events if slug is not None else written.project_events
    after = questions.find(state.fold(events), question_id)
    return Recorded(name=name, question=after, event=written.events[0],
                    failed=written.failed)


__all__ = ["Recorded", "stored", "problems", "given_options", "prepare"]
