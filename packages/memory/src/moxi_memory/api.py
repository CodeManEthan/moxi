"""memory's interface 1, the module other Moxi modules reach (`moxi_memory.api`).

* `search(query, *, project=None, since=None, harness=None, results=False,
  subagents=False, scratch=False, forks=False, limit=10) -> Result`: search past
  sessions. A `Result` holds `hits` (each a `Hit`), `rows` (how many rows were
  searched), `filling` (a `Filling` while the first full read hasn't finished, else
  None), `rebuilding` (the start time of a running rebuild, else None) and `unread` (the
  archive folders no walk has listed whole yet, whose sessions are not in the results).
* `status() -> Status`: the index, its reads, a running rebuild, the session table, and
  each folder of each harness as the last run found it.
* `IndexMissing` and `IndexUnreadable`: what `search` raises when there is no index to
  read, or one that can't be read. A search never answers those with an empty list.
  `StateUnreadable`, a kind of `IndexUnreadable`, says the index reads but memory's
  state store, which holds the session table, doesn't.
* `cut(query)`: the tokens a query is searched for.
* `SUBCONSCIOUS_ENV`: the variable a launch stamps with a session's subconscious mode,
  `automatic` or `on-demand`.
* `SUBCONSCIOUS_ROUTE_ENV`: the variable a launch stamps with the route a session's
  recalls run on; a session with the mode's stamp and none of this runs `subagent`.

Reading a session is sessions' (`moxi sessions read`), not memory's: a hit names its
harness, session and time for that.
"""

from __future__ import annotations

from .index import IndexMissing, IndexUnreadable, StateUnreadable
from .search import Filling, Hit, Result, cut, search
from .recall_start import SUBCONSCIOUS_ENV, SUBCONSCIOUS_ROUTE_ENV
from .status import Status, status

__all__ = ["search", "Result", "Hit", "Filling", "status", "Status", "IndexMissing",
           "IndexUnreadable", "StateUnreadable", "cut", "SUBCONSCIOUS_ENV",
           "SUBCONSCIOUS_ROUTE_ENV"]
