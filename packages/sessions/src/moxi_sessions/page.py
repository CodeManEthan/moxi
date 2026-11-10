"""sessions' page: the routes its page server answers, declared as data.

    GET  /api/window     one session's last turns, read backward (`window()`), with its
                         header; `before` pages back to the turns before an earlier answer
    GET  /api/subagents  a session's subagents (`subagents()`): `listed`, `none`,
                         `unreadable` or `unsupported`, with the reason

    GET  /api/list       the sessions of the three harnesses, counted and cut into groups
    GET  /api/more       the next rows of one group of that list
    GET  /api/session    one session of the list, opened
    GET  /api/folders    the folders the listed sessions ran in

All are reads, and none writes anything. A session is named by `harness` and
`session`, the harness's own id, which `find()` resolves under the harness's root as the
page server's environment and sessions' config give it. `agent` names a Claude Code
subagent, which shares its parent's session id. An OpenCode child session and a Codex
subagent thread are sessions of their own, so each is named by its own id and takes no
`agent`.

`before` is a string because a position differs by harness: a byte offset in a Claude
Code transcript or a Codex rollout, a message id in OpenCode's database. One that does
not parse for its harness (a whole-number offset, or a non-empty OpenCode message id) is
refused with the reason. A well-formed one is not checked against the session: an offset
past the end reads from the end, and one inside a line answers that line as undecodable.
The page sends back only the `before` an answer gave it. A session `find()` cannot find or
read, and a source a read cannot open, are refused too: each answer says why, never a bare
server error.

**Every shape is fixed.** A record goes out as one view row whose keys are those its kind
carries (`RECORD`), never as the record's JSON: no `extra`, no `raw`, no tool call's
input and no tool output, since a declared shape has no union and no map with open keys.
A tool result is its size and error flag. A message's text is sent whole up to
`TEXT_MAX` bytes, cut there with `truncated` set, so the page can show the first part and
expand the rest without asking again. A value the harness did not say is left out of the
row and named in its `unknown` list, as the shape's own JSON does; so is a value of a type
the row does not carry. `"shape"` at the top is the shape version.

Message text, tool call summaries, titles, subagent descriptions and every reason are
marked `redact`; the cwd and every source path are marked `path`. Only the gateway acts on
a mark, and no route here is the gateway's. A refusal's reason is not marked: it can name
a root or a file, which is useful at the desk, so a route opened to the gateway would need
its reasons kept free of paths first.

**The list.** The four list routes read through one catalog the page server keeps for
its life (`moxi_sessions.catalog.shared()`), so what one request read the next reuses.
They list main sessions only: a subagent is counted on its session's row. Their answers
are stated here, once. `null` stands for a fact that was not read, never for zero or an
empty text; a time is epoch seconds; each answer starts with `shape`.

The filters, each optional, are the same on `/api/list` and `/api/more`: `harness`;
`since` (changed at or after it) and `until` (changed before it), epoch seconds as a
number; `folder`, an absolute path a session's own record names exactly; `find`, text of
2 characters or more that a session's id starts with or the last segment of its folder
contains, without regard to case. `/api/folders` takes them all but `folder`. `folder`,
`find` and `/api/folders` read the head of every session not read yet, once for the
server's life; no other request does.

*A row* (`LIST_ROW`): `harness`, `session`, `path`, `changed` (the last change, null for
an OpenCode row without one), `size` (bytes, null for OpenCode), `subagents` (how many;
null where the session's subagents folder could not be listed),
`folder`, `started`, `by_program` (true where the record says a program started the
session, else null), `name` (one typed for the session in the harness, else null),
`model`, `activity` (`{at, who, text}`: the newest thing said, `who` being `you`, `agent`
or null, `text` cut at 300 bytes; or null), `prompt` (`{at, text, truncated}`: the newest
prompt a person typed, `text` cut at `TEXT_MAX` bytes; or null) and `recent` (true for a
session not started by a program that changed within the setting `list.recent_s` of
`now`).

*`/api/list`*: `now` (the time the answer was cut at), `today` (the server's local date,
`YYYY-MM-DD`), `all` (the sessions before the filters) and `total` (after them), both of
both kinds; `unreadable` (compressed Codex rollouts left out, null when Codex's root was
not listed); `roots` (`{harness, root, reason, parts}` per harness whose root was not
read whole, below); `harnesses` (`{harness, count}` for each harness whose root was
listed, under every filter but `harness`); and `groups`, in order.

An item of `roots` is one of two. For a root that could not be listed, `reason` says why
and `parts` is null; `root` is null when the config naming it could not be read. For a
root that was listed but holds folders or files that could not be, `reason` is null and
`parts` is how many: their sessions are in no count, so `all`, `total` and that harness's
count are short by them, and the item is how the answer says its counts are not of every
session.

A harness that is not on the machine is told apart from one that could not be read. A
harness whose root is its default one (no key of sessions' config names it) and does not
exist is in neither `roots` nor `harnesses`. A root the config names that does not exist,
and any root that exists and cannot be listed, is in `roots` with the reason, and that
harness has no item in `harnesses`: no count is given for what was not read. With no
harness on the machine and nothing failing, `all` is 0 and `roots` is empty.

The groups, without `find`:

* `{key: "recent", count, rows, before}`: the sessions with `recent` true, the first 30 as
  rows. Always there, with `count` 0 when it is empty.
* `{key: "YYYY-MM-DD", count, rows, before, program: {count}}` for each of the newest
  seven local calendar days that hold a session not in `recent`: `count` and `rows` (the
  first 8) are the sessions not started by a program, `program.count` the ones that were.
* `{key: "earlier", count, oldest, before}`, only when a session is left: `count` of both
  kinds, `oldest` the oldest change among them (null when none has a change time), no
  rows.

With `find`, the one group is `{key: "found", count, rows, before}`: the matches of both
kinds, the first 30 as rows. A `before` is the place to ask `/api/more` from, null when
the rows hold the whole group; `earlier`'s is where its first day starts. `total` is the
sum of every `count` and every `program.count`.

*`/api/more`* takes `group` (`recent`, `found`, a day's key, `undated` or `earlier`),
`kind` (`person`, the default, or `program`, which only a day has), `before` (a place an
answer gave; left out, the group's start), `now`, and the filters. `now` is the `now` of
the list answer the page is paging from, so the groups are cut where that list cut them;
left out, the server's clock is used. It answers one of:

* `recent`, a day, `found`: `{group, kind, rows, before}`, the next 30 rows; `kind` is
  null for `found`.
* `earlier`: `{groups, before, left}`: the next older days, as many as hold 30 sessions
  between them, each in the list's day shape; `left` is how many sessions remain after
  them. Sessions with no change time come last, as the day `undated`.

A session that does not change between two calls is answered once and never skipped; one
that changes moves to the front and is in the next `/api/list`.

*`/api/session`* takes `harness` and `session` and answers the row's keys plus `reply`
(`{at, text, truncated}`: the model's newest message when no user message of any kind
follows it, `text` cut at `TEXT_MAX` bytes; else null), `subagents` (up to 8, each
`{description, agent_type, started, last_at, last}`, `last` being its newest message cut
at 300 bytes; null when they could not be listed), `more` (how many were not listed; null
with `subagents`, and when some of them could not be listed) and `resume` (`{command, folder}`: the harness's command for picking the
session up again and the folder to run it in, null where it runs anywhere; null for a
harness with none). It says nothing of whether the session is working. An id that is not
a main session's (a subagent's, a spawned Codex thread's, an unknown one) is refused.

*`/api/folders`*: `folders` (`{folder, count, changed}`, newest change first, at most 200,
`count` of both kinds) and `more`, how many folders were left off.

A filter, group, kind or `before` that is not one, and a config that cannot be read, are
refused with the reason. Text a person or model wrote, a typed name, a subagent's
description and a root's reason are marked `redact`; folders, paths and roots are marked
`path`.

The page's assets are what the repository's web build writes from `packages/sessions/web/`
into this package's `web/`: `page.js`, the page, and `window.js`, the block other pages
may place.

The browser UI shell starts the page server on the first request for sessions' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in sessions' announcement, API
or CLI does. sessions' distribution doesn't require ui, which comes with the `ui` extra, so
this module reaches loopback, ui's page-server base, only through `moxi.modules.load("ui",
...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

from .lists import PERSON, PROGRAM
from .shape import HARNESSES
from .view import TEXT_MAX  # the most of one message's text an answer carries, in bytes

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"sessions' page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: The longest session id, agent id or `before` a request takes.
ID_MAX = 256
#: The most turns one answer holds, and how many it holds unless asked.
TURNS_MAX = 50
TURNS_DEFAULT = 5
#: The longest `since`, `until` or `now`, `find`, `folder`, group key and list place a
#: request takes. A place is a change time, a harness and a session id.
SECONDS_MAX = 32
FIND_MAX = 256
FOLDER_MAX = 4096
GROUP_MAX = 32
PLACE_MAX = ID_MAX + 64

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
_PATH = lb.Out("string", mark="path")
_MAYBE_PATH = lb.Out("string", mark="path", nullable=True)
#: Content: what a session's user or model wrote, or a sentence that can name a path.
_SAYS = lb.Out("string", mark="redact")
_MAYBE_SAYS = lb.Out("string", mark="redact", nullable=True)
_INT = lb.Out("int")
_MAYBE_INT = lb.Out("int", nullable=True)
_MAYBE_FLOAT = lb.Out("float", nullable=True)
_BOOL = lb.Out("bool")
_MAYBE_BOOL = lb.Out("bool", nullable=True)
_WORDS = lb.Out("list", items=_TEXT)

#: Where a record came from: `line` and `offset` in a file, `table` and `id` in
#: OpenCode's database. `line` is null in a window, which reads a file backward and does
#: not count its lines.
AT = lb.Out("table", items={"line": _MAYBE_INT, "offset": _INT, "table": _TEXT,
                            "id": _TEXT})

#: One window of a limits reading.
LIMIT_WINDOW = lb.Out("table", items={
    "name": _TEXT,
    "used_percent": _MAYBE_FLOAT,
    "window_minutes": _MAYBE_INT,
    "resets_at": _MAYBE_FLOAT,
    "unknown": _WORDS,
})

#: One record of a window. Every row has `kind`, `raw_kind`, `at` and `unknown`; the other
#: keys are those of its kind, and a key the record has no value for is absent.
#:
#: * every kind: `time` (epoch seconds), `turn_id`, `agent_id`.
#: * `message`: `role`, `text` (cut at `TEXT_MAX` bytes), `text_bytes` (the whole text's
#:   size), `truncated`, `reasoning`, `flags` (the names of the harness's own flags that
#:   are true, such as `isMeta`), `model`, `synthetic`.
#: * `tool_call`: `call_id`, `name`, `summary`, `server`.
#: * `tool_result`: `call_id`, `is_error`, `size_bytes`.
#: * `marker`: `marker`, and its fields: `trigger` and `pre_tokens` (compaction),
#:   `duration_ms` (turn_end), `title` and `title_source` (title), `model`, `provider` and
#:   `service_tier` (settings).
#: * `request`: `request_id`, `model`, `response_model`, `provider`, `service_tier`, the
#:   token counts, `cost_usd`, `context_window`, `sidechain`, `synthetic`, `final`.
#: * `limits`: `limit_id`, `limit_name`, `plan_type`, `windows`.
#: * `malformed`: `why`, `unterminated`.
#: * `other`: `recognised`.
RECORD = lb.Out("table", items={
    "kind": _TEXT,
    "raw_kind": _TEXT,
    "at": AT,
    "time": _MAYBE_FLOAT,
    "turn_id": _MAYBE,
    "agent_id": _MAYBE,
    # message
    "role": _MAYBE,
    "text": _SAYS,
    "text_bytes": _INT,
    "truncated": _BOOL,
    "reasoning": _BOOL,
    "flags": _WORDS,
    "synthetic": _MAYBE_BOOL,
    # tool_call and tool_result
    "call_id": _MAYBE,
    "name": _MAYBE,
    "summary": _SAYS,
    "server": _MAYBE,
    "is_error": _MAYBE_BOOL,
    "size_bytes": _INT,
    # marker
    "marker": _TEXT,
    "trigger": _MAYBE,
    "pre_tokens": _MAYBE_INT,
    "duration_ms": _MAYBE_INT,
    "title": _MAYBE_SAYS,
    "title_source": _MAYBE,
    # request (and a marker's settings, and a user message's model)
    "request_id": _TEXT,
    "model": _MAYBE,
    "response_model": _MAYBE,
    "provider": _MAYBE,
    "service_tier": _MAYBE,
    "input_tokens": _MAYBE_INT,
    "cache_read_input_tokens": _MAYBE_INT,
    "cache_creation_input_tokens": _MAYBE_INT,
    "output_tokens": _MAYBE_INT,
    "reasoning_output_tokens": _MAYBE_INT,
    "cost_usd": _MAYBE_FLOAT,
    "context_window": _MAYBE_INT,
    "sidechain": _MAYBE_BOOL,
    "final": _BOOL,
    # limits
    "limit_id": _MAYBE,
    "limit_name": _MAYBE,
    "plan_type": _MAYBE,
    "windows": lb.Out("list", items=LIMIT_WINDOW),
    # malformed and other
    "why": _TEXT,
    "unterminated": _BOOL,
    "recognised": _BOOL,
    "unknown": _WORDS,
})

#: The session facts the read found beside its records. `title` is the header's, else the
#: newest title the window's records set.
HEADER = lb.Out("table", items={
    "cwd": _MAYBE_PATH,
    "title": _MAYBE_SAYS,
    "version": _MAYBE,
    "parent": _MAYBE,
    "entrypoint": _MAYBE,
    "unknown": _WORDS,
})

#: `/api/window`: the session as asked (`agent` null for a main session), the source read
#: (`path`), the header, `last_activity` (the newest record time in the window, null when
#: none is known), the turns asked for, `has_earlier` with `before` (the position to send
#: back for the turns before these, null when there are none), `ended` (how the backward
#: read ended: `start`, `limit` or null) and the records, oldest first.
WINDOW = {
    "shape": _INT,
    "harness": _TEXT,
    "session": _TEXT,
    "agent": _MAYBE,
    "path": _PATH,
    "header": HEADER,
    "last_activity": _MAYBE_FLOAT,
    "turns": _INT,
    "has_earlier": _BOOL,
    "before": _MAYBE,
    "ended": _MAYBE,
    "records": lb.Out("list", items=RECORD),
}

#: One subagent. `session` and `agent` are what to ask `/api/window` for (and the
#: `window` block's attributes), and `route` the page's route for it:
#: `claude/<session>/<agent>` for a Claude Code subagent, `<harness>/<its own id>` for an
#: OpenCode child session or a Codex subagent thread. A label the harness did not say is
#: absent and named in `unknown`.
SUBAGENT = lb.Out("table", items={
    "agent_id": _TEXT,
    "session": _TEXT,
    "agent": _MAYBE,
    "route": _TEXT,
    "path": _PATH,
    "size": _MAYBE_INT,
    "agent_type": _MAYBE,
    "description": _MAYBE_SAYS,
    "tool_use_id": _MAYBE,
    "spawn_depth": _MAYBE_INT,
    "parent_agent_id": _MAYBE,
    "workflow_id": _MAYBE,
    "unknown": _WORDS,
})

#: A part of the place that could not be read while the rest was: a directory, or a
#: Codex child thread named but not found (its thread id in place of a path).
UNREADABLE_PART = lb.Out("table", items={"where": _PATH, "reason": _SAYS})

#: `/api/subagents`: the listing's `state` (`listed`, `none`, `unreadable` or
#: `unsupported`), its `reason` (null for `listed` and `none`), its entries and the parts
#: it could not read.
SUBAGENTS = {
    "shape": _INT,
    "harness": _TEXT,
    "session": _TEXT,
    "state": _TEXT,
    "reason": _MAYBE_SAYS,
    "entries": lb.Out("list", items=SUBAGENT),
    "unreadable_parts": lb.Out("list", items=UNREADABLE_PART),
}

#: `{at, who, text}`: the newest thing said in a session, or null.
SAID = lb.Out("table", nullable=True,
              items={"at": _MAYBE_FLOAT, "who": _MAYBE, "text": _SAYS})

#: `{at, text, truncated}`: one message, its text cut at `TEXT_MAX` bytes, or null.
MESSAGE = lb.Out("table", nullable=True,
                 items={"at": _MAYBE_FLOAT, "text": _SAYS, "truncated": _BOOL})

_ROW = {
    "harness": _TEXT,
    "session": _TEXT,
    "path": _PATH,
    "changed": _MAYBE_FLOAT,
    "size": _MAYBE_INT,
    "subagents": _MAYBE_INT,
    "folder": _MAYBE_PATH,
    "started": _MAYBE_FLOAT,
    "by_program": _MAYBE_BOOL,
    "name": _MAYBE_SAYS,
    "model": _MAYBE,
    "activity": SAID,
    "prompt": MESSAGE,
    "recent": _BOOL,
}

#: One session of a list (see the module's "A row").
LIST_ROW = lb.Out("table", items=_ROW)
_ROWS = lb.Out("list", items=LIST_ROW)

#: One group of a list. Every group has `key`, `count` and `before`; `recent`, a day and
#: `found` have `rows`; a day has `program`; `earlier` has `oldest`.
GROUP = lb.Out("table", items={
    "key": _TEXT,
    "count": _INT,
    "rows": _ROWS,
    "before": _MAYBE,
    "program": lb.Out("table", items={"count": _INT}),
    "oldest": _MAYBE_FLOAT,
})
_GROUPS = lb.Out("list", items=GROUP)

#: `/api/list` (see the module).
LIST = {
    "shape": _INT,
    "now": lb.Out("float"),
    "today": _TEXT,
    "all": _INT,
    "total": _INT,
    "unreadable": _MAYBE_INT,
    "roots": lb.Out("list", items=lb.Out("table", items={
        "harness": _TEXT, "root": _MAYBE_PATH, "reason": _MAYBE_SAYS,
        "parts": _MAYBE_INT})),
    "harnesses": lb.Out("list", items=lb.Out("table", items={
        "harness": _TEXT, "count": _INT})),
    "groups": _GROUPS,
}

#: `/api/more`: `group`, `kind`, `rows` and `before` for `recent`, a day and `found`;
#: `groups`, `before` and `left` for `earlier`.
MORE = {
    "shape": _INT,
    "group": _TEXT,
    "kind": _MAYBE,
    "rows": _ROWS,
    "groups": _GROUPS,
    "before": _MAYBE,
    "left": _INT,
}

#: One subagent of an opened session.
SESSION_SUBAGENT = lb.Out("table", items={
    "description": _MAYBE_SAYS,
    "agent_type": _MAYBE,
    "started": _MAYBE_FLOAT,
    "last_at": _MAYBE_FLOAT,
    "last": _MAYBE_SAYS,
})

#: `/api/session`: a row, and what an opened session shows under it.
SESSION = {
    "shape": _INT,
    **_ROW,
    "reply": MESSAGE,
    "subagents": lb.Out("list", items=SESSION_SUBAGENT, nullable=True),
    "more": _MAYBE_INT,
    "resume": lb.Out("table", nullable=True,
                     items={"command": _TEXT, "folder": _MAYBE_PATH}),
}

#: `/api/folders`.
FOLDERS = {
    "shape": _INT,
    "folders": lb.Out("list", items=lb.Out("table", items={
        "folder": _PATH, "count": _INT, "changed": _MAYBE_FLOAT})),
    "more": _INT,
}

_HARNESS = lb.Field("enum", values=HARNESSES)
_ID = lb.Field("string", max=ID_MAX)
_MAYBE_ID = lb.Field("string", required=False, max=ID_MAX)

WINDOW_QUERY = {
    "harness": _HARNESS,
    "session": _ID,
    "agent": _MAYBE_ID,
    "turns": lb.Field("int", required=False, max=TURNS_MAX),
    "before": _MAYBE_ID,
}
SUBAGENTS_QUERY = {"harness": _HARNESS, "session": _ID}

#: A time in a query: epoch seconds, written as a number. A query has no float, so it is
#: a string the handler reads.
_SECONDS = lb.Field("string", required=False, max=SECONDS_MAX)
#: The list's filters but `folder`.
_FILTERS = {
    "harness": lb.Field("enum", required=False, values=HARNESSES),
    "since": _SECONDS,
    "until": _SECONDS,
    "find": lb.Field("string", required=False, max=FIND_MAX),
}
LIST_QUERY = {**_FILTERS, "folder": lb.Field("string", required=False, max=FOLDER_MAX)}
MORE_QUERY = {
    "group": lb.Field("string", max=GROUP_MAX),
    "kind": lb.Field("enum", required=False, values=(PERSON, PROGRAM)),
    "before": lb.Field("string", required=False, max=PLACE_MAX),
    "now": _SECONDS,
    **LIST_QUERY,
}
SESSION_QUERY = {"harness": _HARNESS, "session": _ID}
FOLDERS_QUERY = dict(_FILTERS)


def _target(name: str) -> str:
    return f"moxi_sessions.pagehandlers:{name}"


#: What the page server may hold before ui's doctor warns. Measured on Linux (Python
#: 3.14) with the routes served in process and the server's modules loaded: RSS 28 MB
#: before the first read. Then, in one process: a 54 MB Claude Code transcript with 20
#: subagents, a 500-turn Codex rollout and a 500-turn OpenCode session, each window read
#: at 5 and 50 turns and each subagent listing taken five times (at most 43 MB); a list,
#: a list by folder and the folders over a root of 5,000 small Claude Code sessions, 300
#: of them changed in the last seven days, and twenty of those sessions opened (47 MB);
#: every earlier day of that list paged to its end, so the last turn of each of the 5,000
#: sessions is kept (52 MB). 7 open fds throughout. The budget is about twice that, with
#: room for the listener's own sockets. A kept last turn holds the session's newest
#: typed prompt whole, so a root of long prompts holds more than this one did.
BUDGET = {"rss": "104M", "fds": 32}

PAGE = lb.Page(
    assets="web",
    budget=BUDGET,
    routes=(
        lb.Route("/api/window", "read", _target("window"), query=WINDOW_QUERY,
                 returns=WINDOW),
        lb.Route("/api/subagents", "read", _target("subagents"), query=SUBAGENTS_QUERY,
                 returns=SUBAGENTS),
        lb.Route("/api/list", "read", _target("listing"), query=LIST_QUERY,
                 returns=LIST),
        lb.Route("/api/more", "read", _target("more"), query=MORE_QUERY, returns=MORE),
        lb.Route("/api/session", "read", _target("session"), query=SESSION_QUERY,
                 returns=SESSION),
        lb.Route("/api/folders", "read", _target("folders"), query=FOLDERS_QUERY,
                 returns=FOLDERS),
    ),
)

__all__ = ["AT", "BUDGET", "FIND_MAX", "FOLDERS", "FOLDERS_QUERY", "FOLDER_MAX", "GROUP",
           "GROUP_MAX", "HEADER", "ID_MAX", "LIMIT_WINDOW", "LIST", "LIST_QUERY",
           "LIST_ROW", "MESSAGE", "MORE", "MORE_QUERY", "PAGE", "PLACE_MAX", "RECORD",
           "SAID", "SECONDS_MAX", "SESSION", "SESSION_QUERY", "SESSION_SUBAGENT",
           "SUBAGENT", "SUBAGENTS", "SUBAGENTS_QUERY", "TEXT_MAX", "TURNS_DEFAULT",
           "TURNS_MAX", "UNREADABLE_PART", "WINDOW", "WINDOW_QUERY"]
