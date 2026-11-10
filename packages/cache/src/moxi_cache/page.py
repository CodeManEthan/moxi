"""cache's page: the routes its page server answers, declared as data.

    GET /api/clocks   every live session's clock record, the watcher's reading and the
                      server's local date, with the server's `now`
    GET /api/events   the stream; its one topic, `clocks`, is the same without `now`
    GET /api/names    what names each session with a clock record: its typed name, folder
                      and start, read through sessions' `session_facts`, with the server's
                      local date and the account's home folder
    GET /api/session  `?session=`: the model of one session with a clock record, read
                      through sessions' `session_opened`, for the row the page opens

The first two answer from `api.clock_records()`, the listing `moxi cache timers` reads,
and from the watcher's reading (`records.reading()`), cut to `tick_at`, `listed_at` and
`stale_after`: null with no reading file, or when `state` is `error`. `today` is the page
server's local date, `YYYY-MM-DD`, which the page words a day against; the topic carries
it too, so it publishes when the date changes. The last two read the clock records for
their keys and ask sessions, which cache requires, in this process; each is its own route
so the clocks read and the stream never wait on sessions. Every route is a read, and none
is the gateway's.

The browser UI shell starts the page server on the first request for cache's page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in cache's announcement, API,
CLI or console scripts does. cache's distribution doesn't require ui, which comes with the
`ui` extra, so this module reaches loopback, ui's page-server base, only through
`moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"cache's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: One clock record as the page sends it. The values a record may hold as null, or lack
#: (a record an older watcher wrote has no `warn_before` or `stale_after`), are nullable.
ROW = lb.Out("table", items={
    "session": lb.Out("string"),
    "harness": lb.Out("string"),
    "kind": lb.Out("string"),
    "last_request": lb.Out("float", nullable=True),
    "ttl": lb.Out("float", nullable=True),
    "warn_before": lb.Out("float", nullable=True),
    "seen_at": lb.Out("float"),
    "stale_after": lb.Out("float", nullable=True),
    "code": lb.Out("string", nullable=True),
})

#: The watcher's reading as the page sends it: when it last ticked, when it last listed
#: Claude Code's live sessions (null until it has), and the `stale_after` it writes into
#: its records. The whole table is null when there is no reading.
READING = lb.Out("table", nullable=True, items={
    "tick_at": lb.Out("float"),
    "listed_at": lb.Out("float", nullable=True),
    "stale_after": lb.Out("float"),
})

#: The list and how it stands: `state` is `up` or `error`, and `detail` is null when up;
#: the watcher's reading, null with none and when `state` is `error`; and the server's
#: local date, `YYYY-MM-DD`.
CLOCKS = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
    "today": lb.Out("string"),
    "reading": READING,
    "clocks": lb.Out("list", items=ROW),
}

#: One session with a clock record as `/api/names` names it, in the records' order: the
#: name typed for it, the folder it ran in and when it started, as sessions read them,
#: each null where sessions gave none. `known` is true when sessions holds the session,
#: false when it holds none and its harness's root was listed whole, and null when that
#: root was not listed whole or sessions was not asked.
NAME = lb.Out("table", items={
    "session": lb.Out("string"),
    "harness": lb.Out("string"),
    "name": lb.Out("string", mark="redact", nullable=True),
    "folder": lb.Out("string", mark="path", nullable=True),
    "started": lb.Out("float", nullable=True),
    "known": lb.Out("bool", nullable=True),
})

#: `/api/names`: `state` is `up`, or `error` with no sessions when a read raised; the
#: server's local date; the account's home folder, which the page words a folder against,
#: null when it cannot be found.
NAMES = {
    "state": lb.Out("string"),
    "today": lb.Out("string"),
    "home": lb.Out("string", mark="path", nullable=True),
    "sessions": lb.Out("list", items=NAME),
}

#: `/api/session`'s one key: the session's model, null when there is no record for it,
#: sessions holds no such session or the read raised.
SESSION = {"model": lb.Out("string", nullable=True)}

#: The longest session id `/api/session` takes, in characters.
ID_MAX = 200

PAGE = lb.Page(
    routes=(
        lb.Route("/api/clocks", "read", "moxi_cache.pagehandlers:clocks",
                 returns={"now": lb.Out("float"), **CLOCKS}),
        lb.Route("/api/events", "read", "moxi_cache.pagehandlers:events", stream=True,
                 topics={"clocks": lb.Out("table", items=CLOCKS)}),
        lb.Route("/api/names", "read", "moxi_cache.pagehandlers:names", returns=NAMES),
        lb.Route("/api/session", "read", "moxi_cache.pagehandlers:session",
                 query={"session": lb.Field("string", max=ID_MAX)}, returns=SESSION),
    ),
    assets="web",
)

__all__ = ["CLOCKS", "ID_MAX", "NAME", "NAMES", "PAGE", "READING", "ROW", "SESSION"]
