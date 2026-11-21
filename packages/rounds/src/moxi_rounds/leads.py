"""A round's lead: the agent who runs it, whom a page tells and asks.

**The round's own lead** is named when the round is opened (`open --lead NAME`) or
later by `mark(round, name, by=...)`, which appends one `lead.changed` with the new name
and the round's own lead before it (`was`, None when it had none). Anyone may set it:
a lead is a record the leads keep, not the operator's ruling, so it carries no pointer.
Naming the lead the round has already writes nothing.

**The lead to use** is `of(state, project_lead)`: the round's own when its log names one,
`{"name": NAME, "from": "round"}`; else the project's lead from its projects record,
`{"name": NAME, "from": "project"}`; else None. A round opened before rounds kept a lead
reads its project's until someone marks its own.

**A lead's name** is checked for its shape only (`check`): letters, digits, `.`, `_`
and `-`, with at most one `@scope` in the same letters. It is not looked up anywhere,
so a name may be recorded before the agent's session starts.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from . import ending, folders, write
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound

#: The letters a lead's name, and its scope, are made of.
NAME = re.compile(r"[A-Za-z0-9._-]+")

#: Where the lead to use came from: the round's own log, or the project's record.
ROUND = "round"
PROJECT = "project"

#: The event that sets or changes a round's own lead.
CHANGED = "lead.changed"


def is_name(text: object) -> bool:
    """Whether `text` has a lead's name's shape: a name, and at most one `@scope`."""
    if not isinstance(text, str):
        return False
    base, at, scope = text.partition("@")
    return NAME.fullmatch(base) is not None and (
        not at or NAME.fullmatch(scope) is not None)


def check(name: object, again: str) -> str:
    """`name` when it has a lead's name's shape. Raises `BadArgument` when it has not,
    with `again`, the command that names the lead, as the fix."""
    if not is_name(name):
        raise BadArgument(f"{name!r} is not a lead's name: letters, digits, . _ -, and "
                          "at most one @scope", again)
    return name


def of(state: states.State, project_lead: str | None) -> dict | None:
    """The lead of the round `state` is of, to use: `{"name", "from"}`, from the
    round's own log, else the project's record (`project_lead`), else None."""
    if state.lead:
        return {"name": state.lead, "from": ROUND}
    if project_lead:
        return {"name": project_lead, "from": PROJECT}
    return None


@dataclass(frozen=True)
class Led:
    """A round after its lead was named: its `name`, the round's own `lead` now, the
    one it had before (`was`), and the `event` written, None when the round had that
    lead already and nothing was written (`found`). `failed` pairs each after-append
    callback that raised with what it raised."""

    name: str
    lead: str
    was: str | None
    event: dict | None
    found: bool = False
    failed: tuple[tuple[Callable, Exception], ...] = ()


def mark(target: str, lead: str, *, by: str, settings=None,
         wait_s: float = write.WAIT_S, at: str | None = None) -> Led:
    """Make `lead` the own lead of the round `target`, `<project>/<slug>`.

    Raises `BadName` or `BadArgument` for a round's name or a lead's name that can't
    be used, `UnknownProject`, `NoFolder` or `NoRound` for a round that can't be found,
    `Locked` for a lock held past `wait_s`, and `Refused` when the round has ended. A
    round that has the lead already is returned with `found` set, and nothing is
    written.
    """
    if not isinstance(target, str) or "/" not in target:
        raise BadArgument(f"{target!r} is not a round",
                          "name the round, as <project>/<slug>")
    project, slug = folders.split(target)
    check(lead, f"moxi rounds mark {target} --lead NAME")
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"

    def changed(view, result):
        if view.state.lead == lead:
            return None
        return write.Entry(CHANGED, {"lead": lead, "was": view.state.lead})

    try:
        written = write.record(write.Where(home.project, home.rounds_dir, slug), by=by,
                               gates=[ending.takes_new_work(name, "a lead")],
                               event=changed, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    if not written.events:
        return Led(name=name, lead=lead, was=lead, event=None, found=True,
                   failed=written.failed)
    event = written.events[0]
    return Led(name=name, lead=lead, was=event.get("was"), event=event,
               failed=written.failed)


__all__ = ["NAME", "ROUND", "PROJECT", "CHANGED", "is_name", "check", "of", "Led",
           "mark"]
