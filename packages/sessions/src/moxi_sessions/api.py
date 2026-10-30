"""sessions' interface 1, the module other Moxi modules import (`moxi_sessions.api`).

* `claude`: `parse_line`, `iter_entries`, `stream_entries`, `parse_timestamp` and
  `MALFORMED` (the one decoder); `probe_root`, `find_for_session`, `search_roots` and
  `RootProbe` (finding a session's transcript).
* `codex`: `find_rollout` (every rollout file of a thread) and `stream_rollout` (one
  rollout's entries, forward, through the same decoder), with `NAME_RE`.
* `read_back(path, *, block, limit, head=False)`: a record read backwards, its lines
  newest first with the unterminated flag, line 1 when asked, and how the read ended.
* `projects_root(environ)`: Claude Code's projects root in an environment; with None, the
  root for a session that stated none (config `claude.projects`).

sessions decides nothing about a session: which lines count and what a miss or an ending
means are the caller's.
"""

from . import claude, codex
from .claude import projects_root
from .reader import read_back

__all__ = ["claude", "codex", "read_back", "projects_root"]
