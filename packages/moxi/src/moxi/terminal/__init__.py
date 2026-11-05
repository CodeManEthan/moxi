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
"""

from __future__ import annotations

from .._mark import NAME as MARK
from .._mark import marked
from ._record import (FORMAT, NewerFormat, NoRecord, Read, Record, Unreadable, in_set,
                      keyboard, of, read, version, write)
from ._walk import Evidence, Found, Run, Undecided, walk

__all__ = ["MARK", "marked", "walk", "Evidence", "Undecided", "Found", "Run",
           "FORMAT", "in_set", "of", "read", "write", "version", "keyboard", "Record",
           "NoRecord", "Unreadable", "NewerFormat", "Read"]

# The check a page makes before it writes, and where the browser shell takes a variable.
from ._check import check  # noqa: E402
from ._lines import Where, where  # noqa: E402

__all__ += ["check", "where", "Where"]
