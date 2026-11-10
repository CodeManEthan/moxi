"""usage's page: the routes its page server answers, declared as data.

    GET  /api/limits    each plan window now (`limits()`, with no pull), with the
                        server's `now`
    GET  /api/window    one plan window opened (`harness=&limit_id=&minutes=`): when it
                        started, its period's readings, and the sessions it was used by
    GET  /api/daily     tokens and cost by day or month and model (`by=day|month`)
    GET  /api/sessions  tokens and cost by session, dearest first: the `top` after `skip`,
                        how many there are and how many come after, each with its context
    GET  /api/session   one session by agent and model (`session=ID`), with its context
    GET  /api/projects  tokens and cost by project, or by `cwd` where there is none; by
                        `cwd` alone with `by=folder`; with the account's `home` folder
    GET  /api/models    tokens and cost by model, with where each price came from
    GET  /api/terms     every term of each cost, by model, day, month or a session's agent,
                        and what caching saved over the answer
    GET  /api/requests  one row per request of a session's agent (`session=ID&agent=ID`)
    GET  /api/prices    the model table's rates: some models (`models=`) or a search (`q=`)
    GET  /api/status    the index: its lanes, held rows and last pass
    GET  /api/cost      today and the six days before it, by day and model
    POST /api/pass      start a pass over the index, unless one is running
    GET  /api/events    the stream: topics `limits` and `index`

Each read answers from `moxi_usage.api` and reads the index as it is: no read runs a
pass, so none waits on the index's lock, and `/api/prices` reads no index at all.
`/api/limits` makes no Codex pull, so no read waits on another module: the stream's
poller pulls and publishes the `limits` topic. `/api/window` reads the readings store
only when it exists, and creates, prunes and pulls nothing. The one act starts a pass.

A report route takes the filters `since` and `until` (`YYYY-MM-DD`, both days included),
`harness`, `project`, `folder` (a folder's whole path: the files
whose `cwd` it is, whatever project key they carry), `model` and `utc`; days are cut in
the page server's local zone, or in UTC with `utc`. Each route declares exactly the query
keys it reads.

Every report's answer, `/api/status`, `/api/window` and `/api/limits` with its topic
carry `today`, the page server's local date (`YYYY-MM-DD`), so the page words a time's
day against the server's day and not the browser's.

Where the API's answer holds a map with open keys, the route carries it as a list of
`{name, value}` rows or leaves it out, so every shape here is fixed. Every value that can
hold a path carries the mark `path` (a `cwd`, the `home` folder), and a session title the
mark `redact`; only the gateway acts on a mark, and no route is the gateway's.

The page's assets are the built files in `web/` beside this module: the `page`, `limits`
and `cost` components' scripts and the build's stamp.

The browser UI shell starts the page server on the first request for usage's page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in usage's announcement, API,
CLI, listeners or guard does. usage's distribution doesn't require ui, which comes with the
`ui` extra, so this module reaches loopback, ui's page-server base, only through
`moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"usage's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: The harnesses a report can be cut to.
HARNESSES = ("claude", "codex", "opencode")
#: The harnesses that have plan windows.
LIMIT_HARNESSES = ("claude", "codex")
#: The most sessions `/api/window` lists under a window's total.
USED_MAX = 10
#: The most rows `/api/sessions` sends, and how many it sends unless asked.
TOP_MAX = 200
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
    "folder": _ANY,
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


def _report(row: dict[str, lb.Out], *, total: dict[str, lb.Out] | None = None,
            **extra: lb.Out) -> dict[str, lb.Out]:
    """A report's whole answer: the page server's local date, its range (epoch seconds,
    null for an open end), zone, filters as given, rows, total (`SUMS` unless the report
    adds to it), unpriced models and rules, and the index note."""
    return {
        "today": _TEXT,
        "since": _WHEN,
        "until": _WHEN,
        "tz": _TEXT,
        "filters": _named(_SAYS),
        "rows": lb.Out("list", items=lb.Out("table", items=row)),
        "total": lb.Out("table", items=SUMS if total is None else total),
        "unpriced_models": _WORDS,
        "rules": _WORDS,
        "index": INDEX,
        **extra,
    }


#: One row of `/api/daily` and `/api/cost`: `period` is the day (`YYYY-MM-DD`) or the
#: month (`YYYY-MM`), null for requests with no time.
PERIOD_ROW = {"period": _MAYBE, "model": _MAYBE, **ROW_SUMS}
DAILY = _report(PERIOD_ROW, by=_TEXT)
COST = _report(PERIOD_ROW)

#: How full a session's context was at its newest request: `tokens` is the whole prompt
#: of the newest claimed request of the session's main file, `window` the one it ran
#: against (the harness's own, else the model table's, else null). The whole value is
#: null when the session has no such request or its prompt size is unknown.
CONTEXT = lb.Out("table", nullable=True, items={"tokens": _INT, "window": _MAYBE_INT})

#: One session: its main file's `cwd` and project, its models, its first and last
#: request in range, and its context. `session_id` is null for a file with no session
#: id: no route reads such a row by id, so it has no session view and a null `context`.
SESSION_ROW = {
    "harness": _TEXT,
    "session_id": _MAYBE,
    "cwd": _MAYBE_PATH,
    "project": _MAYBE,
    "models": _WORDS,
    "first_ts": _WHEN,
    "last_ts": _WHEN,
    "context": CONTEXT,
    **ROW_SUMS,
}
#: `rows` is `[skip, skip + top)` of the sessions in order; `count` is all of them and
#: `more` those after the rows sent.
SESSIONS = _report(SESSION_ROW, top=_INT, skip=_INT, count=_INT, more=_INT)

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
SESSION = _report(AGENT_ROW, context=CONTEXT)

#: One project key, or for files with none, one `cwd`; by folder, one `cwd` and no key.
PROJECT_ROW = {"project": _MAYBE, "cwd": _MAYBE_PATH, "models": _WORDS, **ROW_SUMS}
#: `by` is the grouping asked for; `home` the account's home folder, null when the page
#: server cannot say it, so the page can write a folder under it from `~`.
PROJECTS = _report(PROJECT_ROW, by=_TEXT, home=_MAYBE_PATH)

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

#: Dollars per million tokens, null where the table doesn't know the price.
_PRICE = lb.Out("float", nullable=True)
_PRICES = lb.Out("table", items={name: _PRICE for name in (
    "input", "output", "cache_read", "cache_write_5m", "cache_write_1h")})

#: A cost's split by token class and price tier: `tier` null for the row's own prices.
SPLIT = lb.Out("list", items=lb.Out("table", items={
    "class": _TEXT, "tier": _MAYBE, "tokens": _INT, "cost": _FLOAT}))
#: One term of a price group's cost: its class's tokens, the price they paid (null when
#: unknown), the dollars (null in a group that can't be priced) and where the price came
#: from (`row`, `tier` or `tier-input`; null for a model the table doesn't know).
TERM = lb.Out("table", items={
    "class": _TEXT, "tokens": _INT, "price": _PRICE, "cost": _WHEN, "price_from": _MAYBE,
    "unknown": _WORDS})
#: One price group of a cost-terms row, a line of the Reconcile table: `provider` is the
#: requests' own (null where the harness gives none), `above` the tier's prompt-size bound
#: (0 for none), `period` the dated `[[model]]` row in force.
GROUP = lb.Out("table", items={
    "model": _MAYBE,
    "provider": _MAYBE,
    "tier": _MAYBE,
    "above": _INT,
    "period": lb.Out("table", nullable=True, items={"from": _MAYBE, "to": _MAYBE}),
    "provenance": PROVENANCE,
    "requests": _INT,
    "terms": lb.Out("list", items=TERM),
    "cost": _WHEN,
    "unpriced_tokens": _INT,
    "unknown_rows": _INT,
    "rule": _MAYBE,
    "why": _MAYBE,
    "unknown": _WORDS,
})
#: One row of `/api/terms`: its key is `model`, `period` (a day or a month) or `agent`
#: and `kind`, as `by` says; the others are null.
TERMS_ROW = {"model": _MAYBE, "period": _MAYBE, "agent": _MAYBE, "kind": _MAYBE,
             **ROW_SUMS, "groups": lb.Out("list", items=GROUP), "split": SPLIT}
#: What caching saved over a terms answer, an estimate: the same tokens with no cache,
#: at the same prices. `read_tokens` of `input_tokens` (the whole prompts) were read
#: from cache; `reads_at_input` is those reads at each price group's own input price,
#: `reads_cost` what they did cost, `write_extra` what the cache writes cost over the
#: input price, and `saved` the first less the other two, 0 or less where caching cost
#: more than it saved. Null when the answer holds no cache read, and when a group that
#: holds cache reads or writes is unpriced or lacks a price the sums need.
SAVING = lb.Out("table", nullable=True, items={
    "input_tokens": _INT, "read_tokens": _INT, "reads_at_input": _FLOAT,
    "reads_cost": _FLOAT, "write_extra": _FLOAT, "saved": _FLOAT})
TERMS = _report(TERMS_ROW, total={**SUMS, "split": SPLIT, "saving": SAVING}, by=_TEXT)

#: One request of `/api/requests`: `n` is its place on the request axis, `context` its
#: whole prompt (null when unknown), `window` the one it ran against (null when unknown).
REQUEST_ROW = {
    "n": _INT,
    "ts": _WHEN,
    "model": _MAYBE,
    "tier": _MAYBE,
    "turn": _MAYBE_INT,
    "turn_start": _BOOL,
    "context": _MAYBE_INT,
    "window": _MAYBE_INT,
    "fresh_input": _INT,
    "cache_read": _INT,
    "cache_write_5m": _INT,
    "cache_write_1h": _INT,
    "output": _INT,
    "unknown": _WORDS,
    "synthetic": _BOOL,
    "cost": _WHEN,
    "split": lb.Out("list", items=lb.Out("table", items={"class": _TEXT, "cost": _WHEN})),
}
#: The requests' total: their sums, the compactions between them (`after` rows come
#: before each), how many turns, and what the report can't show.
REQUESTS = _report(REQUEST_ROW, total={
    **SUMS,
    "marks": lb.Out("list", items=lb.Out("table", items={
        "after": _INT, "ts": _WHEN, "trigger": _MAYBE, "pre_tokens": _MAYBE_INT})),
    "turns": _MAYBE_INT,
    "claimed_elsewhere": _INT,
    "copied_marks": _INT,
    "marks_known": _BOOL,
})

_CONDITIONS = lb.Out("table", items={"above_tokens": _MAYBE_INT, "service_tier": _MAYBE,
                                     "speed": _MAYBE})
#: One model's rates now: `asked_provider` is the provider it was looked up under (null
#: for none); each tier carries the prices a request under it pays and which of them came
#: from the tier's input price or the model's own row; an unknown fact is null and named
#: in `unknown`.
RATE = lb.Out("table", items={
    "model": _MAYBE,
    "asked_provider": _MAYBE,
    "input": _PRICE,
    "output": _PRICE,
    "cache_read": _PRICE,
    "cache_write_5m": _PRICE,
    "cache_write_1h": _PRICE,
    "window": _MAYBE_INT,
    "max_output": _MAYBE_INT,
    "provider": _MAYBE,
    "tiers": lb.Out("list", items=lb.Out("table", items={
        "name": _TEXT, "conditions": _CONDITIONS, "prices": _PRICES,
        "from_tier_input": _WORDS, "from_row": _WORDS})),
    "provenance": PROVENANCE,
    "unknown": _WORDS,
})
#: One `[[model]]` row of usage's config, with its dated period and note.
OVERRIDE = lb.Out("table", items={
    "id": _TEXT,
    "match": _TEXT,
    "prices": _PRICES,
    "window": _MAYBE_INT,
    "max_output": _MAYBE_INT,
    "tiers": lb.Out("list", items=lb.Out("table", items={
        "name": _TEXT, "conditions": _CONDITIONS, "prices": _PRICES})),
    "provider": _MAYBE,
    "from": _MAYBE,
    "to": _MAYBE,
    "note": _MAYBE,
})
PRICES = {
    "effective_date": _MAYBE,
    "rows": _INT,
    "layers": lb.Out("list", items=lb.Out("table", items={
        "layer": _TEXT, "date": _MAYBE, "rows": _INT})),
    "overrides": lb.Out("list", items=OVERRIDE),
    "problems": _WORDS,
    "models": lb.Out("list", items=RATE),
    "matched": _INT,
}

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
    "today": _TEXT,
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
#: reading gave beside the window. The age of a reading is `now - observed_at`. `opened`
#: is when the window started, null when the readings cannot say.
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
    "opened": _MAYBE,
    "note": _MAYBE_SAYS,
    "dollars": _named(_FLOAT),
    "unknown": _WORDS,
})
#: The windows, the page server's local date, and what the last Codex pull did when one
#: was wanted: always null on `/api/limits`, which makes none.
LIMITS = {
    "today": _TEXT,
    "rows": lb.Out("list", items=WINDOW),
    "pull": lb.Out("table", nullable=True, items={
        "tried": _BOOL, "ok": _BOOL, "reason": _MAYBE_SAYS, "via": _MAYBE, "added": _INT}),
    "notes": lb.Out("list", items=_SAYS),
}

#: One reading of a window's period: when it was read (ISO 8601 UTC) and its percent,
#: null when the reading gave none.
_READING = {"at": _TEXT, "used_percent": _WHEN}
#: One window opened: the row `/api/limits` shows for it, when it started, the readings
#: of its period and the sessions it was used by. Times are ISO 8601 UTC, as a limits
#: row's are, but `now`. `readings.last` is the reading the row is from and `first` the
#: period's earliest, null when that is the same one. `used` is this machine's sessions
#: of the harness with requests since the window opened: how many (`count`), their
#: requests and cost since then, and the ten dearest; null for a limit that is not the
#: harness's main one, with a null `opened`, and when there is no index to read.
OPENED_WINDOW = {
    "now": _FLOAT,
    "today": _TEXT,
    "harness": _TEXT,
    "limit_id": _TEXT,
    "window_minutes": _INT,
    "resets_at": _TEXT,
    "used_percent": _FLOAT,
    "opened": _MAYBE,
    "readings": lb.Out("table", nullable=True, items={
        "count": _INT,
        "first": lb.Out("table", nullable=True, items=_READING),
        "last": lb.Out("table", items=_READING)}),
    "used": lb.Out("table", nullable=True, items={
        "since": _TEXT,
        "count": _INT,
        "requests": _INT,
        "cost": _WHEN,
        "complete": _BOOL,
        "rows": lb.Out("list", items=lb.Out("table", items={
            "harness": _TEXT, "session_id": _MAYBE, "requests": _INT, "cost": _WHEN}))}),
}

#: The index topic: whether a pass started here runs now, the last pass, and its note.
INDEX_TOPIC = {"running": _BOOL, "last_pass": _WHEN, "note": _MAYBE_SAYS}

PAGE = lb.Page(
    routes=(
        lb.Route("/api/limits", "read", "moxi_usage.pagehandlers:limits",
                 returns={"now": _FLOAT, **LIMITS}),
        lb.Route("/api/window", "read", "moxi_usage.pagehandlers:window",
                 query={"harness": lb.Field("enum", values=LIMIT_HARNESSES),
                        "limit_id": lb.Field("string", max=512),
                        "minutes": lb.Field("int")},
                 returns=OPENED_WINDOW),
        lb.Route("/api/daily", "read", "moxi_usage.pagehandlers:daily",
                 query={"by": lb.Field("enum", required=False, values=("day", "month")),
                        **FILTERS},
                 returns=DAILY),
        lb.Route("/api/sessions", "read", "moxi_usage.pagehandlers:sessions",
                 query={"top": lb.Field("int", required=False, max=TOP_MAX),
                        "skip": lb.Field("int", required=False), **FILTERS},
                 returns=SESSIONS),
        lb.Route("/api/session", "read", "moxi_usage.pagehandlers:session",
                 query={"session": lb.Field("string", max=512), **FILTERS},
                 returns=SESSION),
        lb.Route("/api/projects", "read", "moxi_usage.pagehandlers:projects",
                 query={"by": lb.Field("enum", required=False,
                                       values=("project", "folder")), **FILTERS},
                 returns=PROJECTS),
        lb.Route("/api/models", "read", "moxi_usage.pagehandlers:models",
                 query=FILTERS, returns=MODELS),
        lb.Route("/api/terms", "read", "moxi_usage.pagehandlers:terms",
                 query={"by": lb.Field("enum", required=False,
                                       values=("model", "day", "month", "agent")),
                        "session": lb.Field("string", required=False, max=512), **FILTERS},
                 returns=TERMS),
        lb.Route("/api/requests", "read", "moxi_usage.pagehandlers:requests",
                 query={"session": lb.Field("string", max=512),
                        "agent": lb.Field("string", required=False, max=512), **FILTERS},
                 returns=REQUESTS),
        lb.Route("/api/prices", "read", "moxi_usage.pagehandlers:prices",
                 query={"models": lb.Field("string", required=False, max=16384),
                        "providers": lb.Field("string", required=False, max=4096),
                        "q": lb.Field("string", required=False, max=200)},
                 returns=PRICES),
        lb.Route("/api/status", "read", "moxi_usage.pagehandlers:status", returns=STATUS),
        lb.Route("/api/cost", "read", "moxi_usage.pagehandlers:cost", returns=COST),
        lb.Route("/api/pass", "act", "moxi_usage.pagehandlers:start_pass",
                 returns={"started": _BOOL, "running": _BOOL}),
        lb.Route("/api/events", "read", "moxi_usage.pagehandlers:events", stream=True,
                 topics={"limits": lb.Out("table", items=LIMITS),
                         "index": lb.Out("table", items=INDEX_TOPIC)}),
    ),
    assets="web",
)

__all__ = ["AGENT_ROW", "CONTEXT", "COST", "DAILY", "FILTERS", "GROUP", "HARNESSES", "INDEX",
           "INDEX_TOPIC", "LANE", "LIMITS", "LIMIT_HARNESSES", "MODELS", "MODEL_ROW",
           "OPENED_WINDOW", "OVERRIDE", "PAGE",
           "PERIOD_ROW", "PRICES", "PROJECTS", "PROJECT_ROW", "PROVENANCE", "RATE",
           "REQUESTS", "REQUEST_ROW", "ROW_SUMS", "SAVING", "SESSION", "SESSIONS", "SESSION_ROW",
           "SPLIT", "STATUS", "SUMS", "TERM", "TERMS", "TERMS_ROW", "TOP_DEFAULT", "TOP_MAX",
           "USED_MAX", "WINDOW"]
