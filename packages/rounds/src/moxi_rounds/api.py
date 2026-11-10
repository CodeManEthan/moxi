"""The rounds read API, interface 1: what the rounds on this machine say, read in process.

A module that takes rounds as optional reaches this through the core, `available()`
first and `load()` only on yes:

    found = (moxi.modules.load("rounds", interface=1)
             if moxi.modules.available("rounds") else None)
    if found is None or not found.ok:
        ...  # the feature is off
    listing = found.api.rounds()

Four calls, each giving the data its command prints with `--json`, computed by the
same functions:

- `rounds(project=None, status="open")`: the rounds of every project, or of one, as
  `moxi rounds list [PROJECT] [--closed] --json` gives them.
- `round(name)`: one round's whole state, as `moxi rounds show ROUND --json` gives it.
- `waiting()`: what waits on the operator across every project, as `moxi rounds list
  --waiting --json` gives it.
- `questions(name, view=None)`: the questions of one round or one project written
  out, as `moxi rounds list ROUND|PROJECT --questions --json` gives them.

A read never writes: no log, no generated file, and not the index either. Where the
index is behind a log, or there is none, the read works the answer out from the log
itself and leaves the index as it found it.

A round whose log can't be read is returned as unreadable, with the file and the
line, wherever it would be one row among others; it is never left out and never given
as empty. What a command refuses, its call raises: every error is a
`moxi_rounds.errors.RoundsError`, with `reason`, `fix` and the `exit_code` the
command exits with.
"""

from __future__ import annotations

from . import export, listing, showing
from .errors import BadArgument

#: The two listings of rounds, as `status` names them: the rounds that are open, and
#: the ones that are open no longer, however each ended.
_OPEN = "open"
_CLOSED = "closed"


def rounds(project: str | None = None, status: str = _OPEN) -> dict:
    """The rounds of every project, or of `project`: with `status` `"open"` the open
    ones, with `"closed"` the ones that have ended.

    Gives `{"computed", "project", "ended", "rounds", "unreadable", "no_folder"}`.
    Each of `rounds` is `{"name", "project", "folder", "status", "flow", "counts",
    "ended"}`, or for a round whose log can't be read `{"name", "project", "folder",
    "unreadable": {"reason", "fix"}}`; such a round is in both listings, since nothing
    says which it belongs in. `unreadable` names each project whose own log can't be
    read, and `no_folder` each project whose rounds folder can't be worked out.

    Raises `BadArgument` for any other `status`, `Refused` when `project` names a
    round, and `UnknownProject` or `NoFolder` for a project with no rounds folder.
    """
    if status not in (_OPEN, _CLOSED):
        raise BadArgument(f"{status!r} is no listing of rounds",
                          f'say status="{_OPEN}" for the open rounds or '
                          f'status="{_CLOSED}" for the ones that have ended')
    # What step each round is at joins the rows where the listing builds them, once a
    # function says it; nothing is added here.
    return listing.rounds(project, ended=status == _CLOSED, refresh=False).data()


def round(name: str) -> dict:
    """The round `name`, `<project>/<slug>`: its head (`name`, `project`, `slug`,
    `folder`, `notice`, `status`, `ended`, `description`, `parent`, `flow`, `steps`,
    `register_unreadable`) and its records (`questions`, `rulings`, `history`).

    A round whose log can't be read is `{"name", "project", "slug", "folder",
    "notice", "unreadable": {"file", "line", "problem", "reason", "fix"}}` and nothing
    else.

    Raises `Refused` for a project's key, `BadName` or `BadArgument` for a name that
    is neither, `UnknownProject` or `NoFolder` for a project with no rounds folder,
    and `NoRound` for a round that was never opened.
    """
    return showing.data(showing.show(name, refresh=False))


def waiting() -> dict:
    """What waits on the operator, across every project: `{"computed", "project",
    "open", "parked", "not_shown", "unreadable", "no_folder"}`. `open` and `parked`
    hold the questions, each as the entry a questions export gives it; `not_shown`
    holds, for each project that has any, its rulings not yet shown to him.
    `unreadable` names each log that can't be read, whose rows are not here."""
    return listing.waiting(refresh=False).data()


def questions(name: str, view: str | None = None) -> dict:
    """The questions of the round or the project `name`, written out: `{"of",
    "project", "round", "view", "computed", "entries", "unreadable", "read_back"}`,
    the open entries first and the parked ones after. `view` is for a round, and is
    `"docket"` or None, which is the docket.

    A project's export names the rounds it could not read in `unreadable` and holds
    the rest. Raises `Unreadable` for a round, or a project's own log, that can't be
    read: there is then nothing to write out. Raises `BadName` or `BadArgument` for a
    name or a view that can't be used, `Refused` for a view given with a project,
    `UnknownProject` or `NoFolder` for a project with no rounds folder, and `NoRound`
    for a round that was never opened.
    """
    return export.questions_of(name, view=view).data()


__all__ = ["rounds", "round", "waiting", "questions"]
