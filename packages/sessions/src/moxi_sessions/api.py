"""sessions' interface 2, the module other Moxi modules import (`moxi_sessions.api`).

* `claude`: `parse_line`, `iter_entries`, `stream_entries`, `parse_timestamp` and
  `MALFORMED` (the one decoder); `probe_root`, `find_for_session`, `search_roots` and
  `RootProbe` (finding a session's transcript).
* `codex`: `find_rollout` (every rollout file of a thread) and `stream_rollout` (one
  rollout's entries, forward, through the same decoder), with `NAME_RE`.
* `read_back(path, *, block, limit, head=False)`: a record read backwards, its lines
  newest first with the unterminated flag, line 1 when asked, and how the read ended.
* `projects_root(environ)`: Claude Code's projects root in an environment; with None, the
  root for a session that stated none (config `claude.projects`).
* `find(harness, session_id, *, root=None)`: where a harness keeps a session's records,
  with every root asked and its outcome (`Found`, `Probe`, `Hit`).
* `sources(harness, *, root=None, modified_since=None, heads=True)`: every source under a
  root, with its reuse keys (`Listing`, `Listed`).
* `subagents(source)`: a session's subagents, `listed`, `none`, `unreadable` or
  `unsupported` (`SubagentListing`, `Subagent`).
* `Source`, `Header`, `Unreadable`; `root(harness, environ)` and `root_origin(harness)`,
  each harness's root and where it came from.

sessions decides nothing about a session: which lines count and what a miss or an ending
means are the caller's.
"""

from . import claude, codex
from .claude import projects_root
from .errors import Unreadable
from .find import (Found, Hit, Listed, Listing, Probe, Subagent, SubagentListing, find,
                   sources, subagents)
from .reader import read_back
from .source import Header, Source, root, root_origin

__all__ = ["claude", "codex", "read_back", "projects_root"]

__all__ += ["find", "sources", "subagents", "Found", "Probe", "Hit", "Listing", "Listed",
            "SubagentListing", "Subagent", "Source", "Header", "Unreadable", "root",
            "root_origin"]
