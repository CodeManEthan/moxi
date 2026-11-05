"""The account's terminal: whether a run was typed in a terminal of the person's own.

- `walk(view=None, run=None)` goes up a run's process tree and returns `Evidence` (Moxi or
  an agent started it), `Undecided` (it can't tell), or None (nothing of Moxi's or an
  agent's): see `moxi.terminal._walk`.
- `marked(env=None)` is a copy of an environment carrying the start mark, `MARK`
  (`MOXI_STARTED_BY`), for a program that builds a child's environment from scratch.
"""

from __future__ import annotations

from .._mark import NAME as MARK
from .._mark import marked
from ._walk import Evidence, Found, Run, Undecided, walk

__all__ = ["MARK", "marked", "walk", "Evidence", "Undecided", "Found", "Run"]
