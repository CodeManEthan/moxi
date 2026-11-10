"""The errors rounds raises.

Each is a `RoundsError`, carrying `reason`, `fix` and `exit_code`:

- `NoLog` (exit 3): the log file asked for does not exist.
- `Unreadable` (exit 3): a log holds a line that can't be read as its next event. `file`,
  `line` and `problem` say where and what.
- `TooNew` (exit 3), an `Unreadable`: the line was written in a log format this code does
  not know.
"""

from __future__ import annotations

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


__all__ = ["RoundsError", "NoLog", "Unreadable", "TooNew"]
