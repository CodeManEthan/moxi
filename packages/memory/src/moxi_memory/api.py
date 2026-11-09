"""memory's interface 1, the module other Moxi modules reach (`moxi_memory.api`).

* `search(query, *, project=None, since=None, harness=None, results=False,
  subagents=False, scratch=False, limit=10) -> Result`: search past sessions. A `Result`
  holds `hits` (each a `Hit`), `rows` (how many rows were searched), `filling` (a
  `Filling` while the first full read hasn't finished, else None) and `rebuilding` (the
  start time of a running rebuild, else None).
* `status() -> Status`: the index, its reads, a running rebuild and the session table.
* `IndexMissing` and `IndexUnreadable`: what `search` raises when there is no index to
  read, or one that can't be read. A search never answers those with an empty list.
* `cut(query)`: the tokens a query is searched for.

Reading a session is sessions' (`moxi sessions read`), not memory's: a hit names its
harness, session and time for that.
"""

from __future__ import annotations

from .index import IndexMissing, IndexUnreadable
from .search import Filling, Hit, Result, cut, search
from .status import Status, status

__all__ = ["search", "Result", "Hit", "Filling", "status", "Status", "IndexMissing",
           "IndexUnreadable", "cut"]
