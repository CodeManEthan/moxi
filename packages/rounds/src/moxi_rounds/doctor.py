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
- ci: for each repo that declares `ci`, `warn` when the first word of its `status` or
  `ids` command, or of its `jobs` command where a workflow is read by its jobs, is not
  on PATH, or, where they are the default `gh` commands, when
  `gh auth status` does not exit 0 (only its exit code is read), naming `moxi rounds
  check --full` as the way through; else `ok`. A repo whose `ci.local` is `"none"` is
  named as meeting the check gate on CI alone.
- queues: for each queue project (`land = "queue"`), `warn` while its queue is paused,
  with the reason and `moxi rounds queue --repo KEY --resume`, and while a member waits
  with no lander running, read from the pid in the lander's lock file without taking
  the lock, with `moxi rounds queue --repo KEY`, which starts one; else `ok`.
- finish locks: `warn` for each finish lock older than an hour, in the repo of any
  project, with its holder's line and the one git command that clears it, for the lead
  to run after asking the holder; else one `ok` row. Only a lock in the format `finish`
  writes has a holder and a time to read; whatever else a repo's `lock_ref` points at
  is another tool's, and not judged here. A project whose root git can't read has no
  locks to look at.
- leftovers: the sweep's dry run (`sweeping.account`). `ok` when no folder of an ended
  build and no trash entry is left on this machine; else `warn` with each folder,
  whether it would go or the check that keeps it, and the command that removes what
  would go. Like every row it only reads: it takes no lock and removes nothing.

The doctor only reads. It writes nothing in a rounds folder and nothing in the index:
an index that is behind is reported, and left for the command that brings it up.
"""

from __future__ import annotations

import os
import re
import shutil
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
    except (RoundsError, config.ConfigError) as exc:
        # With no settings or no projects list, no folder is known to look in.
        yield Row(MODULE, "rounds folders", "fail",
                  f"the projects and their rounds folders can't be read: {exc}",
                  getattr(exc, "fix", None))
        return
    yield from _round_dirs(len(homes), unresolved)
    yield from _logs(homes)
    yield _index(homes)
    yield from _ci()
    yield from _queues()
    yield from _locks()
    yield from _leftovers()


# -- queues ------------------------------------------------------------------------------

def _queues() -> Iterator[Row]:
    from . import lander, queue_file

    try:
        repos = lander.queue_repos()
    except config.ConfigError:
        return
    for repo in repos:
        queue = queue_file.read(repo)
        waiting = queue.queued()
        name = f"queue {repo}"
        if queue.paused is not None:
            yield Row(MODULE, name, "warn", f"the queue is paused: {queue.paused}",
                      f"moxi rounds queue --repo {repo} --resume, once that is dealt "
                      "with")
        elif waiting and not lander.seen_running(repo):
            yield Row(MODULE, name, "warn",
                      f"{len(waiting)} member(s) wait and no lander runs",
                      f"moxi rounds queue --repo {repo}, which starts one")
        else:
            yield Row(MODULE, name, "ok",
                      f"{len(waiting)} member(s) wait" + (", a lander runs" if waiting
                                                         else ""))


# -- ci ----------------------------------------------------------------------------------

#: How long `gh auth status` gets to answer.
AUTH_TIMEOUT_S = 10.0


def _ci() -> Iterator[Row]:
    from . import ci
    from . import settings as own

    try:
        settings = own.load()
    except config.ConfigError:
        # The settings' own row says what is wrong with them.
        return
    for repo, table in settings["projects"].items():
        if not isinstance(table, dict) or "ci" not in table:
            continue
        found = ci.declared(settings, repo)
        name, way = f"ci {repo}", "moxi rounds check ROUND B<n> --full, in place of CI"
        # Each such repo is named at any level of its row: its check rests on CI.
        alone = (" It meets the check gate on CI alone: its ci.local is \"none\"."
                 if found.local is None else "")
        commands = (found.status, found.ids, *((found.jobs,) if found.reads_jobs else ()))
        missing = [argv[0] for argv in commands if shutil.which(argv[0]) is None]
        if missing:
            yield Row(MODULE, name, "warn",
                      f"{', '.join(dict.fromkeys(missing))} is not on PATH, so CI can't be "
                      f"read for a build in it.{alone}", way)
            continue
        if (found.status, found.ids) == (ci.STATUS, ci.IDS) and not _gh_logged_in():
            yield Row(MODULE, name, "warn",
                      "gh auth status does not exit 0: gh is not logged in to the repo's "
                      f"host, so CI can't be read.{alone}", f"gh auth login, or {way}")
            continue
        yield Row(MODULE, name, "ok", f"the commands that read CI are there.{alone}")


def _gh_logged_in() -> bool:
    from .gitreads import git_env

    # gh gets the caller's environment less every GIT_ variable, as every process
    # rounds starts does.
    try:
        done = proc.run(["gh", "auth", "status"], timeout_s=AUTH_TIMEOUT_S, stdin_data="",
                        env=git_env())
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


# -- finish locks --------------------------------------------------------------------------

def _locks() -> Iterator[Row]:
    from . import finish_lock
    from . import settings as own
    from .gitreads import GitError

    try:
        settings = own.load()
    except config.ConfigError:
        settings = None
    old, read = [], 0
    for key, root in folders.roots():
        if not root.is_dir():
            continue
        named = [] if settings is None else [own.build_setting(settings, key, "lock_ref")]
        try:
            found = finish_lock.stale(root, [one for one in named if one])
        except GitError:
            continue
        read += 1
        old += [(key, one) for one in found]
    for key, one in old:
        hours = one.holder.age_s() / 3600
        yield Row(MODULE, f"finish lock {key} {one.ref}", "warn",
                  f"held for {hours:.1f} hours, longer than finish takes, by "
                  f"{one.holder.line}",
                  f"ask the holder; once no finish of theirs runs: {one.clear}")
    if not old:
        yield Row(MODULE, "finish locks", "ok",
                  f"no finish lock older than an hour in the {read} "
                  f"repo{'' if read == 1 else 's'} read")


# -- leftovers -------------------------------------------------------------------------------

def _leftovers() -> Iterator[Row]:
    from . import settings as own
    from . import sweeping

    try:
        settings = own.load()
    except config.ConfigError:
        # The settings' own row says what is wrong with them.
        return
    try:
        swept = sweeping.account(settings=settings)
    except (RoundsError, OSError) as exc:
        yield Row(MODULE, "leftovers", "warn", "the sweep's dry run could not read what "
                  f"ended builds left: {getattr(exc, 'reason', exc)}")
        return
    if swept.clear:
        yield Row(MODULE, "leftovers", "ok",
                  "no folder of an ended build is left on this machine")
        return
    lines = swept.lines()
    more = len(lines) - _NAMED
    detail = "; ".join(lines[:_NAMED]) + (f"; and {more} more" if more > 0 else "")
    goes = swept.would_go or any(one.fate == sweeping.WOULD_DELETE
                                 for one in swept.trash)
    fix = None
    if goes:
        fix = (sweeping.REMOVE if swept.armed else "read the dry run with "
               f"{sweeping.DRY_RUN}, set armed = true under [sweep] in rounds' config, "
               f"then: {sweeping.REMOVE}")
    yield Row(MODULE, "leftovers", "warn", detail, fix)


# -- git -----------------------------------------------------------------------------------

def _floor() -> str:
    return ".".join(map(str, GIT_FLOOR))


def git_version() -> tuple[tuple[int, ...] | None, str]:
    """The version of the git on PATH as numbers, and in words what it said: the
    numbers are None when there is no git, or it gave no version."""
    from .gitreads import git_env

    try:
        done = proc.run(["git", "version"], timeout_s=GIT_TIMEOUT_S, stdin_data="",
                        env=git_env())
    except FileNotFoundError:
        return None, "no git is on PATH"
    except OSError as exc:
        return None, f"the git on PATH can't be run: {exc}"
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

def _round_dirs(resolved: int, unresolved: list) -> Iterator[Row]:
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
            found = [index.read(home.project, home.rounds_dir),
                     *index.rounds(home.project, home.rounds_dir)]
        except OSError as exc:
            bad = True
            yield Row(MODULE, f"log {home.project}", "fail",
                      f"the logs of {home.project} in {home.rounds_dir} can't be read: "
                      f"{exc}", "give your user read access to the folder and its "
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
    except (OSError, sqlite3.Error, store.StoreError) as exc:
        return Row(MODULE, "index", "warn",
                   f"the index can't be held against the logs: {exc}", rebuilt)
    if not differ:
        return Row(MODULE, "index", "ok", f"{where} says what the logs say")
    named = "; ".join(f"{one.name} {one.problem}" for one in differ[:_NAMED])
    more = f"; and {len(differ) - _NAMED} more" if len(differ) > _NAMED else ""
    return Row(MODULE, "index", "warn",
               f"the index differs from the logs on {len(differ)} "
               f"log{'' if len(differ) == 1 else 's'}: {named}{more}", rebuilt)


__all__ = ["MODULE", "GIT_FLOOR", "GIT_TIMEOUT_S", "rows", "git_version"]
