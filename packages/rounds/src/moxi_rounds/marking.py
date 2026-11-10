"""Marking: the changes to a round's records that anyone may make.

Two are here, both to a question of a round and neither the operator's, so neither
carries a pointer:

- `widen(round, id, blocks, actor=...)` makes the question hold up more: every stage, or
  a longer list. The result must hold up every stage the old value did. Anything that
  would let a stage go is a narrowing, which only the operator rules.
- `reopen(round, id, actor=...)` makes a parked question open again.

Each appends one `question.changed` event through the write path and returns the
question as the log then has it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import folders, questions, state, write
from . import settings as own
from .errors import BadArgument, NoLog, NoRound, Unmet


@dataclass(frozen=True)
class Marked:
    """A question after a mark: the `name` of its round, the `question` as the log now
    has it, and the `event` written. `failed` pairs each after-append callback that
    raised with what it raised."""

    name: str
    question: state.Question
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


def widen(target: str, question_id: str, blocks: str, *, actor: str, settings=None,
          wait_s: float = write.WAIT_S, at: str | None = None) -> Marked:
    """Make the question `question_id` of the round `target` hold up `blocks`: `all`, or
    stage ids as `B2,B3`.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, and `Refused` when the round
    has no such question, the question is ruled or withdrawn, a stage named is not the
    round's, or the result would not hold up every stage the old value did.
    """
    wanted = questions.parse_blocks(blocks)

    def gates(name):
        show = f"moxi rounds show {name}"

        def still_asked(view):
            question = questions.find(view.state, question_id)
            if question is None or question.state not in (
                    questions.RULED, questions.WITHDRAWN, questions.MOVED):
                return None
            return Unmet(f"{question_id} is {question.state} and holds nothing up, so "
                         "there is nothing to widen", show)

        def names_stages_of_the_round(view):
            missing = questions.unrecorded(wanted, view.state)
            if not missing:
                return None
            return Unmet(f"{', '.join(missing)} is not a stage of {name}: "
                         f"{questions.stages_said(view.state)}", show)

        def holds_what_it_held(view):
            question = questions.find(view.state, question_id)
            if question is None or questions.holds_every(wanted, question.blocks):
                return None
            return Unmet(
                f"{question_id} holds up {questions.said(question.blocks)}, and "
                f"{questions.said(wanted)} would let some of that go; anyone may widen "
                "what a question holds up, and only the operator narrows it",
                f"moxi rounds rule {name} narrow:{question_id}="
                f"{questions.said(wanted)} --record PTR")

        return [still_asked, names_stages_of_the_round, holds_what_it_held]

    return _mark(target, question_id, {"blocks": questions.stored(wanted)}, gates,
                 actor=actor, settings=settings, wait_s=wait_s, at=at)


def reopen(target: str, question_id: str, *, actor: str, settings=None,
           wait_s: float = write.WAIT_S, at: str | None = None) -> Marked:
    """Make the parked question `question_id` of the round `target` open again.

    Raises as `widen` does for a round that can't be found, and `Refused` when the round
    has no such question or the question is not parked: one that already reads as open,
    because what it waited for has come, is not.
    """
    def gates(name):
        def is_parked(view):
            question = questions.find(view.state, question_id)
            if question is None:
                return None
            if question.state != questions.PARKED:
                return Unmet(f"{question_id} is {question.state}, and only a parked "
                             "question is reopened", f"moxi rounds show {name}")
            if questions.reached(question, view.state):
                return Unmet(f"{question_id} reads as open already: it was parked until "
                             f"{question.until}, and that has come",
                             f"moxi rounds show {name}")
            return None

        return [is_parked]

    return _mark(target, question_id, {"state": questions.OPEN}, gates, actor=actor,
                 settings=settings, wait_s=wait_s, at=at)


def _mark(target: str, question_id: str, change: dict, gates, *, actor: str, settings,
          wait_s: float, at: str | None) -> Marked:
    """Append one change to a question of a round. `gates(name)` gives the form's own
    gates, which run after the one that the question exists."""
    if not isinstance(target, str) or "/" not in target:
        raise BadArgument(f"{target!r} is not a round",
                          "name the round the question is in, as <project>/<slug>")
    project, slug = folders.split(target)
    if not questions.ROUND_ID.fullmatch(question_id or ""):
        raise BadArgument(f"{question_id!r} is not the id of a round's question",
                          "name the question as Q<n>")
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"

    def exists(view):
        if questions.find(view.state, question_id) is not None:
            return None
        return Unmet(f"{name} has no question {question_id}", f"moxi rounds show {name}")

    def changed(view, result):
        return write.Entry("question.changed", {"id": question_id, **change})

    try:
        written = write.record(write.Where(home.project, home.folder, slug), actor=actor,
                               gates=[exists, *gates(name)], event=changed,
                               wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    after = questions.find(state.fold(written.round_events), question_id)
    return Marked(name=name, question=after, event=written.events[0],
                  failed=written.failed)


__all__ = ["Marked", "widen", "reopen"]
