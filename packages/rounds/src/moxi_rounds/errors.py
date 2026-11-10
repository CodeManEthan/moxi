"""The errors rounds raises.

Each is a `RoundsError`, carrying `reason`, `fix` and `exit_code`:

- `NoLog` (exit 3): the log file asked for does not exist.
- `Unreadable` (exit 3): a log holds a line that can't be read as its next event. `file`,
  `line` and `problem` say where and what.
- `TooNew` (exit 3), an `Unreadable`: the line was written in a log format this code does
  not know.
- `Refused` (exit 3): a verb's conditions are not all met. `unmet` holds every one that
  is not, each an `Unmet` with the command that meets it. Nothing was written.
- `Locked` (exit 3): another process held a lock past the wait. `file` is the lock file
  and `holder` the pid written in it.
- `BadName` (exit 2): a round's name is not `<project>/<slug>`.
- `BadArgument` (exit 2): a verb was given a value it can't take.
- `UnknownProject` (exit 3): the projects list holds no project of that key.
- `NoFolder` (exit 3): a project's rounds folder can't be worked out. `problem` says why,
  and the fix names the setting that gives it one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from moxi import exit as exits


class RoundsError(Exception):
    """Something rounds can't do as asked. `reason` says what, `fix` the command or step
    that fixes it (or None), and `exit_code` is what a command exits with."""

    exit_code = exits.ERROR

    def __init__(self, reason: str, fix: str | None = None):
        self.reason = reason
        self.fix = fix
        super().__init__(f"{reason}; {fix}" if fix else reason)


class NoLog(RoundsError):
    """No log is at `file`. Nothing was ever recorded there, which is not the same as a
    log that can't be read."""

    exit_code = exits.REFUSED

    def __init__(self, file: Path):
        self.file = Path(file)
        super().__init__(f"{self.file} does not exist: nothing is recorded there")


class Unreadable(RoundsError):
    """Line `line` of the log `file` (counted from 1) can't be read as its next event;
    `problem` says why. Nothing of the log is used: not the lines before it, and not an
    empty state."""

    exit_code = exits.REFUSED

    #: The way back from a log two machines both appended to.
    WAY_BACK = ("restore one machine's copy of this file whole from git, then run again "
                "the verbs the other machine ran; the extra lines of the refused file "
                "name them")

    def __init__(self, file: Path, line: int, problem: str, fix: str | None = None):
        self.file = Path(file)
        self.line = line
        self.problem = problem
        super().__init__(f"{self.file} line {line} {problem}, so this log can't be read",
                         self.WAY_BACK if fix is None else fix)


class TooNew(Unreadable):
    """Line `line` of the log `file` was written in a format this code does not know."""

    def __init__(self, file: Path, line: int, problem: str):
        super().__init__(file, line, problem,
                         "upgrade moxi-rounds on this machine to the version that wrote it")


@dataclass(frozen=True)
class Unmet:
    """One condition of a verb that does not hold: `reason` says which, and `fix` is the
    command that makes it hold."""

    reason: str
    fix: str

    def __post_init__(self):
        if not self.reason or not self.fix:
            raise ValueError("an unmet condition says what it is and the command that "
                             "meets it")


class Refused(RoundsError):
    """A verb was refused because the conditions in `unmet` do not hold. Every one that
    does not hold is there, not only the first, and the verb wrote nothing."""

    exit_code = exits.REFUSED

    def __init__(self, unmet: Iterable[Unmet]):
        self.unmet = tuple(unmet)
        if not self.unmet:
            raise ValueError("a refusal names at least one unmet condition")
        lines = ["refused, with nothing written:"]
        for one in self.unmet:
            lines += [f"  - {one.reason}", f"    run: {one.fix}"]
        super().__init__("\n".join(lines))


class Locked(RoundsError):
    """Another process held the lock file `file` for the whole wait of `waited_s`
    seconds. `holder` is the pid written in the file, or None when it holds none."""

    exit_code = exits.REFUSED

    def __init__(self, file: Path, holder: int | None, waited_s: float):
        self.file = Path(file)
        self.holder = holder
        self.waited_s = waited_s
        who = f"pid {holder}" if holder else "another process"
        super().__init__(f"{self.file} was still locked by {who} after {waited_s:g} "
                         "seconds, so nothing was written",
                         f"run the command again once {who} is done")


class BadName(RoundsError):
    """`name` is not a round's name: a project's key, `/`, and a slug of lower-case
    letters, digits and `-`."""

    exit_code = exits.USAGE

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"{name!r} is not a round's name",
                         "name a round as <project>/<slug>, the slug in lower-case "
                         "letters, digits and -")


class BadArgument(RoundsError):
    """A verb was given a value it can't take; `reason` says which and `fix` what to
    give instead."""

    exit_code = exits.USAGE


class UnknownProject(RoundsError):
    """The projects list holds no project whose key is `project`."""

    exit_code = exits.REFUSED

    def __init__(self, project: str):
        self.project = project
        super().__init__(f"there is no project {project!r}",
                         f"add it with `moxi projects add {project} --root DIR`, or see "
                         "the keys with `moxi projects list`")


class NoFolder(RoundsError):
    """The rounds folder of the project `project` can't be worked out; `problem` says
    why. `known` is False when the notes module was there to ask and the asking failed,
    so whether the project has a notes folder is not known."""

    exit_code = exits.REFUSED

    def __init__(self, project: str, problem: str, *, known: bool = True):
        self.project = project
        self.problem = problem
        self.known = known
        super().__init__(
            f"the rounds folder of the project {project!r} can't be worked out: {problem}",
            f'set dir = "/an/absolute/folder" under [projects.{project}] in rounds\' '
            "config, or give the project a notes folder with `moxi projects set "
            f"{project} --notes-root DIR`")


__all__ = ["RoundsError", "NoLog", "Unreadable", "TooNew", "Unmet", "Refused", "Locked",
           "BadName", "BadArgument", "UnknownProject", "NoFolder"]
