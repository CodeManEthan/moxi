"""The terminal target's start, stood in for on every system.

A launch in this terminal execs the harness on Linux and macOS, and on Windows starts it
as a child and waits for it (`targets.Terminal.start`). A test that reads what a terminal
launch starts stands in for whichever of the two this system runs:

    starts.stand_in(monkeypatch, record)

`record(command, argv, env, cwd)` gets what the product handed the start: on Linux and
macOS the program, its argv and env given to exec, with the folder exec would run in; on
Windows the child's argv (the program's found path first, then the launch's words), its
env and its `cwd`. The stood-in child is this process, which runs, and ends with 0 at
once, as a harness that exec'd would leave this process's pid in the row.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from moxi_launcher import targets

Record = Callable[[str, list, dict, str], object]


def stand_in(monkeypatch, record: Record) -> None:
    """Stand in for the terminal target's start on this system; `record` gets each one."""

    def execve(command, argv, env):
        record(command, list(argv), env, os.getcwd())

    class Child:
        def __init__(self, argv, *, env, cwd):
            record(argv[0], list(argv), env, str(cwd))
            self.pid = os.getpid()

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(targets, "_execve", execve)
    if targets.WINDOWS:
        monkeypatch.setattr(targets, "_popen", Child)


def ignored(command, argv, env, cwd) -> None:
    """A `record` that keeps nothing."""
