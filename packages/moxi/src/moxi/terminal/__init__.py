"""The account's terminal: the core's record of the environment the user's terminal runs
with, and whether a run was typed in a terminal of the person's own.

The record (`moxi.terminal._record`):

- `in_set(name)` says whether a variable is in the core set, the names the resolvers a
  page's check may use read; `of(env)` is the record's `env` for an environment.
- `read(path=None)` gives a `Record` (`.env`, `.moxi`, `.format`), or `NoRecord`,
  `Unreadable` (`.error`) or `NewerFormat` (`.format`, `.moxi`, `.reader`).
- `write(env=None)` writes the record of an environment, mode 0600. Only the front
  door's record step writes the real account's record, and test kits their test's.
- `keyboard(stdin=None)` is the positive half of the rule for who records: a terminal
  on the input.

The walk (`moxi.terminal._walk`):

- `walk(view=None, run=None)` goes up a run's process tree and returns `Evidence` (Moxi or
  an agent started it), `Undecided` (it can't tell), or None (nothing of Moxi's or an
  agent's).
- `marked(env=None)` is a copy of an environment carrying the start mark, `MARK`
  (`MOXI_STARTED_BY`), for a program that builds a child's environment from scratch.

The front door's record step (`step()`, run by the `moxi` console script before it
dispatches) leaves its `Outcome` in `last`: None when no step ran in this process.

The walk's names are imported when first used, so the front door's step, which walks
only when a write is pending, doesn't pay for them on an unchanged environment.
"""

from __future__ import annotations

from .._mark import NAME as MARK
from .._mark import marked
from ._record import (FAILED, FORMAT, FOUND, NO_KEYBOARD, NO_PLACE, SAME, UNDECIDED,
                      WRITTEN, NewerFormat, NoRecord, Outcome, Read, Record, Unreadable,
                      in_set, keyboard, of, read, row, step, version, write)

#: The `Outcome` of this process's front-door record step; None when none ran.
last: Outcome | None = None

_WALK = ("walk", "Evidence", "Undecided", "Found", "Run")


def __getattr__(name: str):
    if name in _WALK:
        from . import _walk

        return getattr(_walk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["MARK", "marked", "walk", "Evidence", "Undecided", "Found", "Run",
           "FORMAT", "in_set", "of", "read", "write", "version", "keyboard", "Record",
           "NoRecord", "Unreadable", "NewerFormat", "Read", "Outcome", "step", "row",
           "last", "SAME", "WRITTEN", "NO_KEYBOARD", "FOUND", "UNDECIDED", "NO_PLACE",
           "FAILED"]

# The check a page makes before it writes, and where the browser shell takes a variable;
# imported when first used, as the walk's names are.
_CHECK = {"check": "_check", "where": "_lines", "Where": "_lines"}
_walk_attr = __getattr__


def __getattr__(name: str):  # noqa: F811 - adds the check's names to the walk's
    if name in _CHECK:
        import importlib

        return getattr(importlib.import_module(f".{_CHECK[name]}", __name__), name)
    return _walk_attr(name)


__all__ += ["check", "where", "Where"]
