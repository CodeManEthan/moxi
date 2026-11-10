"""sessions' interfaces 2, 3 and 4, the module other Moxi modules import
(`moxi_sessions.api`).

Interface 3 is interface 2 plus `retention()` and `Retention`; interface 4 is interface 3
plus the list (`sessions_list` and the names with it). A caller loads sessions with the
newest interface whose names it uses.

The one shape (`moxi_sessions.shape`):

* `SHAPE`, the shape version written at the top of every JSON document sessions writes.
* The record kinds: `Record`, `Request`, `Message`, `ToolCall`, `ToolResult`, `Marker`
  (with `MARKERS`), `Limits` (with `LimitWindow`), `Malformed` and `Other`, by name in
  `KINDS`; `UNKNOWN` and `known`; `to_json`, `from_json` and `document`.

Finding sessions:

* `find(harness, session_id, *, root=None)`: where a harness keeps a session's records,
  with every root asked and its outcome (`Found`, `Probe`, `Hit`).
* `sources(harness, *, root=None, modified_since=None, heads=True)`: every source under a
  root, with its reuse keys (`Listing`, `Listed`).
* `subagents(source)`: a session's subagents, `listed`, `none`, `unreadable` or
  `unsupported` (`SubagentListing`, `Subagent`).
* `Source`, `Header`, `Unreadable`; `root(harness, environ)` and `root_origin(harness)`,
  each harness's root and where it came from.

Reading one source (`Read`, `Back`, `LastRequest`, `Batch`, `Window`):

* `records(source, *, kinds=None, keep_raw=False)`: every record, forward.
* `records_back(source, *, kinds=None, block=BLOCK, limit=BACK_LIMIT, read_head=False)`: the
  records newest first.
* `last_request(source, *, synthetic=False, limit=BACK_LIMIT)`: the newest request on the
  source's own chain.
* `records_since(source, cursor=None, *, kinds=None, max_bytes=None)`: the records
  written since a cursor (`CURSOR_VERSION`), and the cursor to hand back.
* `window(source, *, turns=5, before=None, limit=WINDOW_LIMIT)`: the last turns, with a
  position to page back.

Each harness's transcript retention (interface 3):

* `retention(env=None)`: how long each harness keeps its transcripts, as set: Claude
  Code's `cleanupPeriodDays`, Codex's compression switch and its archive folder, and that
  sessions reads no setting for OpenCode (`Retention`, with the lines a setup prints).
  It reads and changes nothing else.

The list of sessions (interface 4; `moxi_sessions.lists` says what each answers):

* `sessions_list(cat=None, *, recent_s, now=None, harness=None, since=None, until=None,
  folder=None, find=None)`: the main sessions of the three harnesses as plain data: the
  counts, and the groups a list is cut into (the sessions active recently, one group per
  day for seven days, the rest as a count) with the first rows of each.
* `sessions_more(cat=None, *, group, recent_s, kind=None, before=None, now=None, ...)`:
  the next rows of one group, from the place an answer gave.
* `sessions_newest(cat=None, *, recent_s, now=None, limit=30, ...)`: the same sessions
  as one run, newest change first, with no groups.
* `sessions_folders(cat=None, *, harness=None, since=None, until=None, find=None,
  limit=200)`: the folders those sessions ran in, with how many each holds.
* `session_opened(cat=None, *, harness, session, recent_s, now=None)`: one session's row
  with its last reply, its newest subagents and the command that picks it up again.
* `list_recent_s()`: the setting `list.recent_s`, the `recent_s` a caller with no
  number of its own passes.
* `Catalog`, what the list reads through and keeps its reads in. With `cat` None each
  function reads through the process's own; a caller that wants a list over other roots,
  or one that forgets with it, makes a `Catalog` and passes it.

Writing records for tests:

* `testing`: the builders other modules' tests write harness records with
  (`ClaudeTranscript`, `CodexRollout`, `OpenCodeDb`; see `moxi_sessions.testing`). A test
  reaches them through this module, as it reaches every other name sessions offers.

Each harness's mapping, and the line reads under the records:

* `claude`: `parse_line`, `iter_entries`, `stream_entries`, `parse_timestamp` and
  `MALFORMED` (the one decoder); `SAFE_ID_RE`, the session ids a search accepts, and
  `MISS`, a probe's outcome when a root holds no such session.
* `codex`: the lists a rollout is recognised by (`TOP_KINDS`, `EVENT_KINDS`,
  `RESPONSE_KINDS`, `META_KEYS`, `HISTORY_MODES`) and the versions the mapping was checked
  against (`CHECKED_VERSIONS`).
* `opencode`: OpenCode's database reader, with the errors a failed read raises
  (`MissingSession`, `UnknownSchema`, `QueryFailed`, each an `Unreadable`), the versions
  the mapping was checked against (`CHECKED_VERSIONS`) and the session row's totals
  (`TOTALS`).
* `read_back(path, *, block, limit, read_head=False)`: a record read backwards, its lines
  newest first with the unterminated flag, line 1 when asked, and how the read ended.
* `projects_root(environ)`: Claude Code's projects root in an environment; with None, the
  root for a session that stated none (config `claude.projects`).

Every name is imported on first use, so importing this module costs next to nothing, and a
caller that never reads OpenCode never imports `sqlite3`.

sessions decides nothing about a session: which lines count and what a miss or an ending
means are the caller's.
"""

from __future__ import annotations

import importlib
from typing import Any

#: Each name's home module, under this package. A name whose home is itself a module
#: (`claude`, `codex`, `opencode`, `testing`) is that module.
_HOMES: dict[str, str] = {
    **dict.fromkeys(("claude", "codex", "opencode", "testing"), ""),
    **dict.fromkeys(("projects_root",), "claude"),
    **dict.fromkeys(("retention", "Retention"), "retention"),
    **dict.fromkeys(("read_back",), "reader"),
    **dict.fromkeys(("Unreadable",), "errors"),
    **dict.fromkeys(("Source", "Header", "root", "root_origin"), "source"),
    **dict.fromkeys(("find", "sources", "subagents", "Found", "Probe", "Hit", "Listing",
                     "Listed", "SubagentListing", "Subagent"), "find"),
    **dict.fromkeys(("records", "records_back", "last_request", "records_since",
                     "window", "Read", "Back", "LastRequest", "Batch", "Window",
                     "CURSOR_VERSION", "BLOCK", "BACK_LIMIT", "WINDOW_LIMIT"), "read"),
    **dict.fromkeys(("SHAPE", "UNKNOWN", "known", "Record", "Request", "Message",
                     "ToolCall", "ToolResult", "Marker", "MARKERS", "LimitWindow",
                     "Limits", "Malformed", "Other", "KINDS", "to_json", "from_json",
                     "document"), "shape"),
}

#: The names the API spells differently from their home: the home module and the name
#: there.
_RENAMED: dict[str, tuple[str, str]] = {
    "sessions_list": ("lists", "list_sessions"),
    "sessions_more": ("lists", "more_sessions"),
    "sessions_newest": ("lists", "newest_sessions"),
    "sessions_folders": ("lists", "list_folders"),
    "session_opened": ("lists", "open_session"),
    "list_recent_s": ("settings", "recent_s"),
    "Catalog": ("catalog", "Catalog"),
}

__all__ = [*_HOMES, *_RENAMED]


def __getattr__(name: str) -> Any:
    home, there = _RENAMED.get(name, (_HOMES.get(name), name))
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if home == "":
        value = importlib.import_module(f"{__package__}.{name}")
    else:
        value = getattr(importlib.import_module(f"{__package__}.{home}"), there)
    globals()[name] = value              # later lookups find it without coming here
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
