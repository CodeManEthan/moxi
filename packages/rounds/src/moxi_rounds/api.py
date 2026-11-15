"""The rounds read API, interface 1: what the rounds on this machine say, read in process.

A module that takes rounds as optional reaches this through the core, `available()`
first and `load()` only on yes:

    found = (moxi.modules.load("rounds", interface=1)
             if moxi.modules.available("rounds") else None)
    if found is None or not found.ok:
        ...  # the feature is off
    listing = found.api.rounds()

Five calls. The first four each give the data its command prints with `--json`,
computed by the same functions:

- `rounds(project=None, status="open")`: the rounds of every project, or of one, as
  `moxi rounds list [PROJECT] [--closed] --json` gives them.
- `round(name)`: one round's whole state, as `moxi rounds show ROUND --json` gives it.
- `waiting()`: what waits on the operator across every project, as `moxi rounds list
  --waiting --json` gives it.
- `questions(name, view=None)`: the questions of one round or one project written
  out, as `moxi rounds list ROUND|PROJECT --questions --json` gives them.

and the fifth has no command of its own:

- `builds(repo=None)`: the open builds of every project, or of one repo, each with its
  state read from git now.

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

from . import export, listing, open_builds, showing
from .errors import BadArgument

#: The two listings of rounds, as `status` names them: the rounds that are open, and
#: the ones that are open no longer, however each ended.
_OPEN = "open"
_CLOSED = "closed"


def rounds(project: str | None = None, status: str = _OPEN) -> dict:
    """The rounds of every project, or of `project`: with `status` `"open"` the open
    ones, with `"closed"` the ones that have ended.

    Gives `{"computed", "project", "ended", "rounds", "unreadable", "no_folder"}`.
    Each of `rounds` is `{"name", "project", "folder", "status", "flow", "step",
    "counts", "standing", "ended"}`, where `step` is the step the round is at as
    `{"step", "stage", "state"}`, or None for a round at no step. A round whose log
    can't be read is `{"name", "project", "folder", "unreadable": {"reason", "fix"}}`;
    such a round is in both listings, since nothing says which it belongs in. `unreadable` names each project whose own log can't be
    read, and `no_folder` each project whose rounds folder can't be worked out.

    Raises `BadArgument` for any other `status`, `Refused` when `project` names a
    round, and `UnknownProject` or `NoFolder` for a project with no rounds folder.
    """
    if status not in (_OPEN, _CLOSED):
        raise BadArgument(f"{status!r} is no listing of rounds",
                          f'say status="{_OPEN}" for the open rounds or '
                          f'status="{_CLOSED}" for the ones that have ended')
    return listing.rounds(project, ended=status == _CLOSED, refresh=False).data()


def round_data(name: str) -> dict:
    """The round `name`, `<project>/<slug>`: its head (`name`, `project`, `slug`,
    `folder`, `notice`, `status`, `ended`, `description`, `parent`, `flow`, `steps`,
    `step`, `close`, `skips`, `stages`, `versions`, `standing`, `trace`, `threads`,
    `register_unreadable`) and its records (`builds`, `waits`, `decisions`,
    `questions`, `rulings`, `verdicts`, `history`). `close` is None for a round that has ended;
    for an open one it is `{"waits"}`, what its close waits for, a line each. The
    record `waits` holds the round's waiting items, open and done, and `decisions`
    its decisions, open and ruled.

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
    "open", "parked", "waits", "decisions", "not_shown", "builds", "unreadable",
    "no_folder"}`.
    `open` and `parked` hold the questions, each as the entry a questions export
    gives it; `waits` each open waiting item of an open round, with its round;
    `decisions` each major decision of an open round not yet ruled, as its entry;
    `not_shown` holds, for each project that has any, its rulings not yet shown to
    him; `builds` each open build to look at. `unreadable` names each log that can't
    be read, whose rows are not here."""
    return listing.waiting(refresh=False).data()


def questions(name: str, view: str | None = None) -> dict:
    """The questions of the round or the project `name`, written out: `{"of",
    "project", "round", "view", "computed", "entries", "ratify", "covers", "later",
    "ended", "left", "unreadable", "read_back"}`, the open entries first, the parked
    ones after, then a round's decisions to ratify. For a round that
    has ended, `ended` says how and `left` holds its questions whose records still
    read as open or parked: they wait for a new round and are no entries. `view` is for a round, and is
    `"docket"`, `"ratify"` (the decisions not yet ruled of its finished builds, which
    `covers` names, as the project's `ratify` setting picks them, with the major ones
    of unfinished builds in `later`), or None for both.

    A project's export names the rounds it could not read in `unreadable` and holds
    the rest. Raises `Unreadable` for a round, or a project's own log, that can't be
    read: there is then nothing to write out. Raises `BadName` or `BadArgument` for a
    name or a view that can't be used, `Refused` for a view given with a project,
    `UnknownProject` or `NoFolder` for a project with no rounds folder, and `NoRound`
    for a round that was never opened.
    """
    return export.questions_of(name, view=view).data()


def builds(repo: str | None = None) -> dict:
    """The open builds of every project, or with `repo` (a project's key) those that
    build in that repo, each with its state read from git now: `{"computed", "repo",
    "builds", "unreadable", "no_folder"}`.

    Each of `builds` is `{"round", "stage", "repo", "branch", "target", "base",
    "state", "by", "why", "head", "target_head", "target_before", "merge_base",
    "own_commits", "hand_merge", "idle", "shares", "problem"}`. `state` is `gone`,
    `empty`, `building`, `merged` or `unclear`, and `by` and `why` say what gave it.
    `head` is the build's head, `target_head` the target's, `target_before` what the
    target was when the build reached it, `merge_base` the merge base of a head off
    the target, and `hand_merge` the merge that brought a head merged by hand.
    `own_commits` is true for an `empty` build that holds commits of its own. `idle`
    is true for a `building` build whose head is older than its round's `idle_days`.
    `shares` holds `{"round", "stage"}` for each open build of the same repo and
    target that shares commits with it. A build that can't be read has its
    `{"reason", "fix"}` in `problem`, and None for each field of its state.
    `unreadable` names each round whose log can't be read, whose builds are not here,
    and `no_folder` each project whose rounds folder can't be worked out.

    Raises `BadArgument` for a `repo` with no name and `UnknownProject` for one the
    projects list does not hold.
    """
    return open_builds.read(repo).data()


__all__ = ["rounds", "round_data", "waiting", "questions", "builds"]
