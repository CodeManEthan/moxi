"""usage's page: the routes its page server answers, declared as data.

    GET  /api/limits    each plan window now (`limits()`), with the server's `now`
    GET  /api/daily     tokens and cost by day or month and model (`by=day|month`)
    GET  /api/sessions  tokens and cost by session, the `top` by cost, with how many more
    GET  /api/session   one session by agent and model (`session=ID`)
    GET  /api/projects  tokens and cost by project, or by `cwd` where there is none
    GET  /api/models    tokens and cost by model, with where each price came from
    GET  /api/status    the index: its lanes, held rows and last pass
    GET  /api/cost      today and the six days before it, by day and model
    POST /api/pass      start a pass over the index, unless one is running
    GET  /api/events    the stream: topics `limits` and `index`

Each read answers from `moxi_usage.api` and reads the index as it is: no read runs a
pass, so none waits on the index's lock. The one act starts a pass. A report route takes
the filters `since` and `until` (`YYYY-MM-DD`, both days included), `harness`, `project`,
`model` and `utc`; days are cut in the page server's local zone, or in UTC with `utc`.
Each route declares exactly the query keys it reads.

Where the API's answer holds a map with open keys, the route carries it as a list of
`{name, value}` rows or leaves it out, so every shape here is fixed. Every value that can
hold a path carries the mark `path`, and a session title the mark `redact`; only the
gateway acts on a mark, and no route is the gateway's.

The browser UI shell starts the page server on the first request for usage's page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in usage's announcement, API,
CLI, listeners or guard does. usage's distribution doesn't require loopback, which comes
with the `ui` extra, so this module reaches it only through `moxi.modules.load` and raises
`ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"usage's page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

#: The harnesses a report can be cut to.
HARNESSES = ("claude", "codex", "opencode")
#: The most rows `/api/sessions` sends, and how many it sends unless asked.
TOP_MAX = 1000
TOP_DEFAULT = 200

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
_MAYBE_PATH = lb.Out("string", mark="path", nullable=True)
_SAYS = lb.Out("string", mark="redact")
_MAYBE_SAYS = lb.Out("string", mark="redact", nullable=True)
_INT = lb.Out("int")
_MAYBE_INT = lb.Out("int", nullable=True)
_FLOAT = lb.Out("float")
_WHEN = lb.Out("float", nullable=True)
_BOOL = lb.Out("bool")
_WORDS = lb.Out("list", items=_TEXT)


def _named(value: lb.Out) -> lb.Out:
    return lb.Out("list", items=lb.Out("table", items={"name": _TEXT, "value": value}))


# --- query keys --------------------------------------------------------------------------

_DATE = lb.Field("string", required=False, max=10)
_ANY = lb.Field("string", required=False, max=4096)
#: The filters of every report route but `status` and `cost`.
FILTERS = {
    "since": _DATE,
    "until": _DATE,
    "harness": lb.Field("enum", required=False, values=HARNESSES),
    "project": _ANY,
    "model": _ANY,
    "utc": lb.Field("bool", required=False),
}

# --- shapes ------------------------------------------------------------------------------

#: The sums every report row and total carries. `cost` is null when nothing in the row
#: could be priced; `unknown` names each value the API could not say.
SUMS = {
    "requests": _INT,
    "input": _INT,
    "fresh_input": _INT,
    "cache_read": _INT,
    "cache_write": _INT,
    "cache_write_1h": _INT,
    "output": _INT,
    "tokens": _INT,
    "cost": _WHEN,
    "unpriced_tokens": _INT,
    "unknown_rows": _INT,
    "complete": _BOOL,
    "harness_cost": _WHEN,
    "unknown": _WORDS,
}
#: A row's sums and its own flags.
ROW_SUMS = {**SUMS, "unpriced_models": _WORDS, "rules": _WORDS}

#: What a report says of the index it read: `passed` is always false here, since no read
#: runs a pass; `note` says the read took the index as it is, or why there is none.
INDEX = lb.Out("table", items={"passed": _BOOL, "last_pass": _WHEN, "note": _MAYBE_SAYS})


def _report(row: dict[str, lb.Out], **extra: lb.Out) -> dict[str, lb.Out]:
    """A report's whole answer: its range (epoch seconds, null for an open end), zone,
    filters as given, rows, total, unpriced models and rules, and the index note."""
    return {
        "since": _WHEN,
        "until": _WHEN,
        "tz": _TEXT,
        "filters": _named(_SAYS),
        "rows": lb.Out("list", items=lb.Out("table", items=row)),
        "total": lb.Out("table", items=SUMS),
        "unpriced_models": _WORDS,
        "rules": _WORDS,
        "index": INDEX,
        **extra,
    }


#: One row of `/api/daily` and `/api/cost`: `period` is the day (`YYYY-MM-DD`) or the
#: month (`YYYY-MM`), null for requests with no time.
PERIOD_ROW = {"period": _MAYBE, "model": _MAYBE, **ROW_SUMS}
DAILY = _report(PERIOD_ROW, by=_TEXT)
COST = _report(PERIOD_ROW, today=_TEXT)

#: One session: its main file's title, `cwd` and project, its models, and its first and
#: last request in range. `session_id` is null for a file with no session id, which has
#: no session view.
SESSION_ROW = {
    "harness": _TEXT,
    "session_id": _MAYBE,
    "title": _MAYBE_SAYS,
    "cwd": _MAYBE_PATH,
    "project": _MAYBE,
    "models": _WORDS,
    "first_ts": _WHEN,
    "last_ts": _WHEN,
    **ROW_SUMS,
}
SESSIONS = _report(SESSION_ROW, top=_INT, more=_INT)

#: One agent and model of a session: `agent` is `main` for the main file, else the
#: agent's id; `kind` its file's kind.
AGENT_ROW = {
    "agent": _TEXT,
    "kind": _TEXT,
    "title": _MAYBE_SAYS,
    "model": _MAYBE,
    "first_ts": _WHEN,
    "last_ts": _WHEN,
    **ROW_SUMS,
}
SESSION = _report(AGENT_ROW)

#: One project key, or for files with none, one `cwd`.
PROJECT_ROW = {"project": _MAYBE, "cwd": _MAYBE_PATH, "models": _WORDS, **ROW_SUMS}
PROJECTS = _report(PROJECT_ROW)

#: Where one model's price came from (the API's provenance without its `source`).
PROVENANCE = lb.Out("table", items={
    "layer": _MAYBE,
    "step": _INT,
    "rule": _TEXT,
    "row": _MAYBE,
    "snapshot": _MAYBE,
    "overridden": _WORDS,
    "period": lb.Out("table", nullable=True, items={"from": _MAYBE, "to": _MAYBE}),
    "note": _MAYBE_SAYS,
    "filled": _WORDS,
    "variant": _MAYBE,
})
MODEL_ROW = {"model": _MAYBE, "provenance": lb.Out("list", items=PROVENANCE), **ROW_SUMS}
MODELS = _report(MODEL_ROW)

#: One lane of the index: what the last pass learnt of it and what the index holds for it.
LANE = lb.Out("table", items={
    "lane": _TEXT,
    "harness": _MAYBE,
    "state": _MAYBE,
    "reason": _MAYBE_SAYS,
    "root": _MAYBE_PATH,
    "listed": _MAYBE_INT,
    "compressed": _MAYBE_INT,
    "checked_at": _WHEN,
    "unreadable": lb.Out("list", items=lb.Out("table", items={
        "path": lb.Out("string", mark="path"), "reason": _SAYS})),
    "imports_path": _MAYBE_PATH,
    "imports_reason": _MAYBE_SAYS,
    "imported": _MAYBE_INT,
    "originator_only": _MAYBE_INT,
    "files": _INT,
    "excluded": _INT,
    "held": _INT,
    "held_reasons": _named(_INT),
    "requests": _INT,
    "duplicates": _INT,
})
_FILE = {"lane": _TEXT, "path": lb.Out("string", mark="path"), "part": _TEXT}
STATUS = {
    "lanes": lb.Out("list", items=LANE),
    "total": lb.Out("table", items={
        "files": _INT,
        "excluded": _INT,
        "held": _INT,
        "requests": _INT,
        "duplicates": _INT,
        "lanes": _INT,
        "held_rows": lb.Out("list", items=lb.Out("table", items={**_FILE, "reason": _SAYS})),
        "duplicate_files": _INT,
        "duplicate_rows": lb.Out("list", items=lb.Out("table", items={
            **_FILE,
            "session_id": _MAYBE,
            "requests": _INT,
            "of_lane": _TEXT,
            "of_path": lb.Out("string", mark="path"),
            "of_part": _TEXT,
        })),
        "last_pass": _WHEN,
        "index_version": _INT,
    }),
    "index": INDEX,
}

#: One plan window. Times are ISO 8601 UTC; `dollars` holds the dollar figures the
#: reading gave beside the window. The age of a reading is `now - observed_at`.
WINDOW = lb.Out("table", items={
    "harness": _TEXT,
    "limit_id": _TEXT,
    "window_minutes": _MAYBE_INT,
    "label": _TEXT,
    "state": _TEXT,
    "reason": _MAYBE_SAYS,
    "slot": _MAYBE,
    "used_percent": _WHEN,
    "resets_at": _MAYBE,
    "observed_at": _MAYBE,
    "source": _MAYBE,
    "session_id": _MAYBE,
    "plan_type": _MAYBE,
    "full_at": _MAYBE,
    "projection": _MAYBE,
    "fit": _INT,
    "note": _MAYBE_SAYS,
    "dollars": _named(_FLOAT),
    "unknown": _WORDS,
})
#: The windows, and what the last Codex pull did when one was wanted.
LIMITS = {
    "rows": lb.Out("list", items=WINDOW),
    "pull": lb.Out("table", nullable=True, items={
        "tried": _BOOL, "ok": _BOOL, "reason": _MAYBE_SAYS, "via": _MAYBE, "added": _INT}),
    "notes": lb.Out("list", items=_SAYS),
}

#: The index topic: whether a pass started here runs now, the last pass, and its note.
INDEX_TOPIC = {"running": _BOOL, "last_pass": _WHEN, "note": _MAYBE_SAYS}

PAGE = lb.Page(
    routes=(
        lb.Route("/api/limits", "read", "moxi_usage.pagehandlers:limits",
                 returns={"now": _FLOAT, **LIMITS}),
        lb.Route("/api/daily", "read", "moxi_usage.pagehandlers:daily",
                 query={"by": lb.Field("enum", required=False, values=("day", "month")),
                        **FILTERS},
                 returns=DAILY),
        lb.Route("/api/sessions", "read", "moxi_usage.pagehandlers:sessions",
                 query={"top": lb.Field("int", required=False, max=TOP_MAX), **FILTERS},
                 returns=SESSIONS),
        lb.Route("/api/session", "read", "moxi_usage.pagehandlers:session",
                 query={"session": lb.Field("string", max=512), **FILTERS},
                 returns=SESSION),
        lb.Route("/api/projects", "read", "moxi_usage.pagehandlers:projects",
                 query=FILTERS, returns=PROJECTS),
        lb.Route("/api/models", "read", "moxi_usage.pagehandlers:models",
                 query=FILTERS, returns=MODELS),
        lb.Route("/api/status", "read", "moxi_usage.pagehandlers:status", returns=STATUS),
        lb.Route("/api/cost", "read", "moxi_usage.pagehandlers:cost", returns=COST),
        lb.Route("/api/pass", "act", "moxi_usage.pagehandlers:start_pass",
                 returns={"started": _BOOL, "running": _BOOL}),
        lb.Route("/api/events", "read", "moxi_usage.pagehandlers:events", stream=True,
                 topics={"limits": lb.Out("table", items=LIMITS),
                         "index": lb.Out("table", items=INDEX_TOPIC)}),
    ),
)

__all__ = ["AGENT_ROW", "COST", "DAILY", "FILTERS", "HARNESSES", "INDEX", "INDEX_TOPIC",
           "LANE", "LIMITS", "MODELS", "MODEL_ROW", "PAGE", "PERIOD_ROW", "PROJECTS",
           "PROJECT_ROW", "PROVENANCE", "ROW_SUMS", "SESSION", "SESSIONS", "SESSION_ROW",
           "STATUS", "SUMS", "TOP_DEFAULT", "TOP_MAX", "WINDOW"]
