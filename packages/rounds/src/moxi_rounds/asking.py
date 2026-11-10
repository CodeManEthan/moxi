"""Asking a question: one sentence put to the operator, recorded with where it came from.

`ask("<project>/<slug>", "one sentence", origin=..., actor=...)` appends one
`question.asked` event through the write path and returns the question as recorded. A
round's question is `Q<n>` in the round's log; one asked of a project, with no round yet,
is `P<n>` in the project's log. Each log numbers its own from 1.

**The origin.** Every question says where it came from: `origin`, a pointer of a shape
`pointers` knows (a message, a moment in a session, a page, or a place in a file of the
round's folder, such as a section of its design). A question that is the operator's own
is given with `record`, the pointer to where he asked it, and that pointer is its origin.
A question with neither is refused: nothing else can stand in for an origin.

**What it holds up.** A round's question holds up every stage unless it says otherwise:
the stages it names, which the round must have, or none. A project's question holds up
no stage.

**Who and when.** The one who runs the verb asked it, at the time it is recorded. Both
are fields of the question, apart from the event's own, so they stay with it when it is
later carried to another log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import folders, log, pointers, questions, write
from . import settings as own
from .errors import BadArgument, NoLog, NoRound, Unmet


@dataclass(frozen=True)
class Asked:
    """A question as recorded: its `id`, the `name` of the round or project whose log
    holds it, its `text`, what it `blocks` and its `origin`, read back from the `event`.
    `failed` pairs each after-append callback that raised with what it raised."""

    id: str
    name: str
    text: str
    blocks: questions.Blocks
    origin: str
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


def ask(target: str, text: str, *, actor: str, origin: str | None = None,
        record: str | None = None, blocks: str | None = None, settings=None,
        wait_s: float = write.WAIT_S, at: str | None = None) -> Asked:
    """Record one question on `target`, a round `<project>/<slug>` or a project's key,
    and return it as recorded.

    - `text`: the question, one sentence on one line.
    - `actor`: who asks it; the event's actor and the question's asker.
    - `origin`: the pointer to where the question came from.
    - `record`: the pointer to where the operator asked it, for a question that is his
      own, in place of `origin`.
    - `blocks`: what a round's question holds up, `B2,B3`, `all` or `none`; every stage
      when left out. A project's question takes only `none`.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the lock; `at`: the event's time.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject` or
    `NoFolder` for a project with no rounds folder, `NoRound` for a round that was never
    opened, and `Refused` when the question has no origin, its pointer has no shape a
    pointer has, or it names a stage the round does not have. A refusal leaves the log
    as it was.
    """
    if not isinstance(target, str) or not target.strip():
        raise BadArgument("nothing says what the question is asked of",
                          "name a round as <project>/<slug>, or a project by its key")
    project, slug = folders.split(target) if "/" in target else (target, None)
    if not isinstance(text, str) or not text.strip():
        raise BadArgument("a question needs its sentence",
                          "give the question as one sentence, in quotes")
    if "\n" in text or "\r" in text:
        raise BadArgument("a question is one sentence on one line, and this one has a "
                          "line break", "give it on one line; ask a second question "
                                        "with a second command")
    if origin is not None and record is not None:
        raise BadArgument("--from and --record each say where the question came from",
                          "give --record alone for a question that is the operator's "
                          "own, and --from alone for any other")
    held = None if blocks is None else questions.parse_blocks(blocks)
    if slug is None:
        if held not in (None, questions.NONE):
            raise BadArgument("a project's question holds up no stage: a project has "
                              "none", f"leave --blocks out, or say --blocks "
                                      f"{questions.NONE}")
        held = questions.NONE
    elif held is None:
        held = questions.ALL

    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = home.project if slug is None else f"{home.project}/{slug}"
    again = f"moxi rounds ask {name} TEXT --from PTR"
    # One time for the event and for the question, so the two never differ by a second.
    at = log.stamp() if at is None else at

    def has_an_origin(view):
        if record is not None:
            return pointers.unmet(record, f"moxi rounds ask {name} TEXT --record PTR")
        if origin is None:
            return Unmet(
                "a question says where it came from; PTR below is one token: "
                f"{pointers.SHAPES}. A section of the round's design is "
                "file:design.md#<anchor>. For a question that is the operator's own, "
                "give --record PTR in place of --from", again)
        if pointers.shape(origin) is None:
            return Unmet(f"{origin!r} is not a pointer to where the question came "
                         f"from; PTR below is one token: {pointers.SHAPES}", again)
        return None

    def names_stages_of_the_round(view):
        missing = questions.unrecorded(held, view.state)
        if not missing:
            return None
        return Unmet(f"{', '.join(missing)} is not a stage of {name}: "
                     f"{questions.stages_said(view.state)}",
                     f"{again} --blocks LIST|{questions.NONE}")

    def asked(view, result):
        mine = view.state if slug is not None else view.project_state
        fields = {"id": questions.next_id("Q" if slug is not None else "P", mine),
                  "text": text, "from": record if record is not None else origin,
                  "blocks": questions.stored(held), "state": questions.OPEN,
                  "asked_by": actor, "asked_at": at}
        if record is not None:
            fields["record"] = record
        return write.Entry("question.asked", fields,
                           log=write.ROUND if slug is not None else write.PROJECT)

    try:
        written = write.record(write.Where(home.project, home.folder, slug), actor=actor,
                               gates=[has_an_origin, names_stages_of_the_round],
                               event=asked, wait_s=wait_s, at=at)
    except NoLog:
        # Only a round's log can be missing here: a project's reads as empty.
        raise NoRound(name) from None
    event = written.events[0]
    return Asked(id=event["id"], name=name, text=event["text"], blocks=held,
                 origin=event["from"], event=event, failed=written.failed)


__all__ = ["Asked", "ask"]
