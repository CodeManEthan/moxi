"""sessions' page: the routes its page server answers, declared as data.

    GET  /api/window     one session's last turns, read backward (`window()`), with its
                         header; `before` pages back to the turns before an earlier answer
    GET  /api/subagents  a session's subagents (`subagents()`): `listed`, `none`,
                         `unreadable` or `unsupported`, with the reason

Both are reads, and neither writes anything. A session is named by `harness` and
`session`, the harness's own id, which `find()` resolves under the harness's root as the
page server's environment and sessions' config give it. `agent` names a Claude Code
subagent, which shares its parent's session id. An OpenCode child session and a Codex
subagent thread are sessions of their own, so each is named by its own id and takes no
`agent`.

`before` is a string because a position differs by harness: a byte offset in a Claude
Code transcript or a Codex rollout, a message id in OpenCode's database. One that does
not parse for its harness, or names no position of the session, is refused with the
reason. So is a session `find()` cannot find or read, and a source a read cannot open:
each answer says why, never a bare server error.

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
a mark, and no route here is the gateway's.

The page's assets are what the repository's web build writes from `packages/sessions/web/`
into this package's `web/`: `page.js`, the page, and `window.js`, the block other pages
may place.

The browser UI shell starts the page server on the first request for sessions' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in sessions' announcement, API
or CLI does. sessions' distribution doesn't require loopback, which comes with the `ui`
extra, so this module reaches it only through `moxi.modules.load` and raises
`ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

from .shape import HARNESSES

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"sessions' page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

#: The longest session id, agent id or `before` a request takes.
ID_MAX = 256
#: The most turns one answer holds, and how many it holds unless asked.
TURNS_MAX = 50
TURNS_DEFAULT = 5
#: The most of one message's text an answer carries, in UTF-8 bytes.
TEXT_MAX = 65536

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


def _target(name: str) -> str:
    return f"moxi_sessions.pagehandlers:{name}"


#: What the page server may hold before ui's doctor warns. Measured on Linux (Python
#: 3.14) with the routes served in process and the server's modules loaded, over a 54 MB
#: Claude Code transcript with 20 subagents, a 500-turn Codex rollout and a 500-turn
#: OpenCode session, each window read at 5 and 50 turns and each subagent listing taken
#: five times: RSS 35 MB before the first read and at most 43 MB after, and 7 open fds.
#: The budget is about twice that, with room for the listener's own sockets.
BUDGET = {"rss": "96M", "fds": 32}

PAGE = lb.Page(
    assets="web",
    budget=BUDGET,
    routes=(
        lb.Route("/api/window", "read", _target("window"), query=WINDOW_QUERY,
                 returns=WINDOW),
        lb.Route("/api/subagents", "read", _target("subagents"), query=SUBAGENTS_QUERY,
                 returns=SUBAGENTS),
    ),
)

__all__ = ["AT", "BUDGET", "HEADER", "ID_MAX", "LIMIT_WINDOW", "PAGE", "RECORD",
           "SUBAGENT", "SUBAGENTS", "SUBAGENTS_QUERY", "TEXT_MAX", "TURNS_DEFAULT",
           "TURNS_MAX", "UNREADABLE_PART", "WINDOW", "WINDOW_QUERY"]
