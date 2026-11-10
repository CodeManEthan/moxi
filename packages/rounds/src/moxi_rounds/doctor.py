"""rounds' doctor checks: `rows()`, which the announcement's `DOCTOR` names.

The core's own checks cover the index as a declared store: that it is where it should
be, with the right mode and size. These look at what rounds needs and keeps:

- git: `fail` when there is no git on PATH, when it won't say its version, or when it
  is older than `GIT_FLOOR`.
- rounds folders: `warn` for each project whose rounds folder can't be worked out, with
  the setting or the command that gives it one. A project nobody opens a round in is
  no fault, so this is not a failure; every verb that names such a project refuses.
- log: `fail` for each log that can't be read, a round's or a project's own, with the
  file, the line and the way back. Every verb on that log refuses until then.
- index: `warn` when the index says something else than the logs do, naming each log
  it differs on. Every read still answers right, from the log itself; `moxi rounds
  list` brings the index up to the logs.

The doctor only reads. It writes nothing in a rounds folder and nothing in the index:
an index that is behind is reported, and left for the command that brings it up.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from collections.abc import Iterator

from moxi import config, proc, store
from moxi.doctor import Row

from . import folders, index, paths
from .errors import RoundsError

MODULE = "rounds"
#: The oldest git rounds runs with: `git worktree remove` is the newest call it makes,
#: and this is the first git that has it.
GIT_FLOOR = (2, 17)
#: How long `git version` gets to answer.
GIT_TIMEOUT_S = 5.0
#: The command that brings the index up to the logs.
_REBUILD = "moxi rounds list"
#: How many logs one row names before it counts the rest.
_NAMED = 10


def rows() -> Iterator[Row]:
    """Every check's row."""
    yield _git()
    try:
        homes, unresolved = folders.homes()
    except (RoundsError, config.ConfigError) as error:
        # With no settings or no projects list, no folder is known to look in.
        yield Row(MODULE, "rounds folders", "fail",
                  f"the projects and their rounds folders can't be read: {error}",
                  getattr(error, "fix", None))
        return
    yield from _folders(len(homes), unresolved)
    yield from _logs(homes)
    yield _index(homes)


# -- git -----------------------------------------------------------------------------------

def _floor() -> str:
    return ".".join(map(str, GIT_FLOOR))


def git_version() -> tuple[tuple[int, ...] | None, str]:
    """The version of the git on PATH as numbers, and in words what it said: the
    numbers are None when there is no git, or it gave no version."""
    # git gets the caller's environment less every GIT_ variable, as every git rounds
    # starts does.
    env = {name: value for name, value in os.environ.items()
           if not name.startswith("GIT_")}
    try:
        done = proc.run(["git", "version"], timeout_s=GIT_TIMEOUT_S, stdin_data="",
                        env=env)
    except FileNotFoundError:
        return None, "no git is on PATH"
    except OSError as error:
        return None, f"the git on PATH can't be run: {error}"
    except subprocess.TimeoutExpired:
        return None, f"`git version` gave no answer in {GIT_TIMEOUT_S:g} seconds"
    said = " ".join((done.stdout or "").split())
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", said)
    if done.returncode != 0 or found is None:
        return None, (f"`git version` exited {done.returncode} and gave no version"
                      + (f": {said[:80]}" if said else ""))
    return tuple(int(part) for part in found.groups() if part is not None), said


def _git() -> Row:
    version, said = git_version()
    install = f"install git {_floor()} or later, and put it first on PATH"
    if version is None:
        return Row(MODULE, "git", "fail",
                   f"{said}; rounds needs git {_floor()} or later", install)
    if version[:2] < GIT_FLOOR:
        return Row(MODULE, "git", "fail",
                   f"{said}, which is older than {_floor()}: rounds removes a build's "
                   "worktree with `git worktree remove`, which that git does not have",
                   install)
    return Row(MODULE, "git", "ok", f"{said}, which is {_floor()} or later")


# -- the rounds folders ----------------------------------------------------------------------

def _folders(resolved: int, unresolved: list) -> Iterator[Row]:
    if not unresolved:
        yield Row(MODULE, "rounds folders", "ok",
                  f"the rounds folder of each of {resolved} "
                  f"project{'' if resolved == 1 else 's'} can be worked out")
        return
    for error in unresolved:
        yield Row(MODULE, f"rounds folder {error.project}", "warn", error.reason,
                  error.fix)


# -- the logs --------------------------------------------------------------------------------

def _logs(homes: list[folders.Home]) -> Iterator[Row]:
    count, bad = 0, False
    for home in homes:
        try:
            found = [index.read(home.project, home.folder),
                     *index.rounds(home.project, home.folder)]
        except OSError as error:
            bad = True
            yield Row(MODULE, f"log {home.project}", "fail",
                      f"the logs of {home.project} in {home.folder} can't be read: "
                      f"{error}", "give your user read access to the folder and its "
                      "files")
            continue
        for summary in found:
            count += 1
            if summary.unreadable is not None:
                bad = True
                yield Row(MODULE, f"log {summary.name}", "fail",
                          summary.unreadable.reason, summary.unreadable.fix)
    if not bad:
        yield Row(MODULE, "logs", "ok",
                  f"every log of {len(homes)} project{'' if len(homes) == 1 else 's'} "
                  "can be read")


# -- the index -------------------------------------------------------------------------------

def _index(homes: list[folders.Home]) -> Row:
    where = paths.index_file()
    try:
        size = os.path.getsize(where)
    except OSError:
        size = 0
    if size == 0:
        return Row(MODULE, "index", "ok",
                   f"no index yet at {where}: the first write makes it, and until "
                   "then every read answers from the logs")
    rebuilt = (f"`{_REBUILD}` brings the index up to the logs; it is a cache, and "
               "every read answers right meanwhile, from the log itself")
    if not index.usable():
        return Row(MODULE, "index", "warn",
                   f"{where} is not an index this version of moxi-rounds reads, so "
                   "every read answers from the logs",
                   f"`{_REBUILD}` brings an older index up to this version; for one "
                   "a newer moxi-rounds wrote, upgrade moxi-rounds here")
    try:
        differ = index.disagreements(homes)
    except (OSError, sqlite3.Error, store.StoreError) as error:
        return Row(MODULE, "index", "warn",
                   f"the index can't be held against the logs: {error}", rebuilt)
    if not differ:
        return Row(MODULE, "index", "ok", f"{where} says what the logs say")
    named = "; ".join(f"{one.name} {one.problem}" for one in differ[:_NAMED])
    more = f"; and {len(differ) - _NAMED} more" if len(differ) > _NAMED else ""
    return Row(MODULE, "index", "warn",
               f"the index differs from the logs on {len(differ)} "
               f"log{'' if len(differ) == 1 else 's'}: {named}{more}", rebuilt)


__all__ = ["MODULE", "GIT_FLOOR", "GIT_TIMEOUT_S", "rows", "git_version"]
