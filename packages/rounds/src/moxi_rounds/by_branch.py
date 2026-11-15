"""Finding a build from inside its worktree, so a builder runs a verb with no argument.

A build is found by its repo and branch. In a folder, the top of its git work tree is
read with the branch checked out there, the project is the one the projects list says
that folder is in (a linked worktree is in the project of the work tree it was added
from), and the build is the one open build in the index whose repo is that project and
whose branch is that branch.

Each way this finds no single build is refused with what was read and the command that
names the round instead: a folder in no git work tree, a detached head, a folder in no
project, no open build of that repo and branch, and more than one. Nothing is written.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import folders, index
from .errors import BadArgument
from .gitreads import Git


@dataclass(frozen=True)
class Found:
    """The open build a worktree holds: the `round` it is in, its `stage`, and the
    `repo` and `branch` it was found by."""

    round: str
    stage: str
    repo: str
    branch: str


def find(path: str | os.PathLike, *, verb: str, settings=None) -> Found:
    """The one open build whose repo and branch are those checked out at the folder
    `path`. `verb` is the command being run, named in a refusal's fix with the round
    it takes instead. `settings` is what `settings.load()` gives, read now when left
    out.

    Raises `BadArgument` when no single open build is found there, and what
    `folders.homes` raises."""
    instead = f"name the round: moxi rounds {verb} <project>/<slug>"
    top = Git(path).top() if os.path.isdir(path) else None
    if top is None:
        raise BadArgument(f"no round is named and {path} is in no git work tree, so no "
                          "build is found by its branch", instead)
    branch = Git(top).checked_out()
    if branch is None:
        raise BadArgument(f"no round is named and the work tree at {top} has a detached "
                          "head, so no build is found by its branch",
                          f"check out the build's branch there, or {instead}")
    repo = folders.project_at(top)
    if repo is None:
        raise BadArgument(f"no round is named and {top} is in no project of the "
                          "projects list, so no build is found by its branch", instead)
    found, unread = open_on(repo, branch, settings=settings)
    if len(found) == 1:
        return found[0]
    if not found:
        unreadable = ("" if not unread else
                      f"; {len(unread)} round(s) can't be read and are not looked in: "
                      + "; ".join(unread))
        raise BadArgument(f"no round is named and no open build has repo {repo} and "
                          f"branch {branch}{unreadable}", instead)
    named = ", ".join(f"{one.round} {one.stage}" for one in found)
    raise BadArgument(f"no round is named and more than one open build has repo {repo} "
                      f"and branch {branch}: {named}", instead)


def open_on(repo: str, branch: str, *,
            settings=None) -> tuple[list[Found], list[str]]:
    """Each open build of the repo `repo` (its key, in any case) on the branch
    `branch`, in any round of any project, as the index has them, and a line for each
    round whose log can't be read. Nothing is read from git."""
    homes, _unresolved = folders.homes(settings)
    found, unread = [], []
    for home in homes:
        for summary in index.rounds(home.project, home.folder):
            if summary.unreadable is not None:
                unread.append(summary.unreadable.reason)
                continue
            for item in summary.of(index.BUILD):
                body = item.body
                if body["repo"].casefold() == repo.casefold() and body["branch"] == branch:
                    found.append(Found(summary.name, body["stage"], body["repo"], branch))
    return found, unread


__all__ = ["Found", "find", "open_on"]
