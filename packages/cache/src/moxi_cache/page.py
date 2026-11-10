"""cache's page: the routes its page server answers, declared as data.

    GET /api/clocks   every live session's clock record, the watcher's reading and the
                      server's local date, with the server's `now`
    GET /api/events   the stream; its one topic, `clocks`, is the same without `now`

Both answer from `api.clock_records()`, the listing `moxi cache timers` reads, and from
the watcher's reading (`records.reading()`), cut to `tick_at`, `listed_at` and
`stale_after`: null with no reading file, or when `state` is `error`. `today` is the page
server's local date, `YYYY-MM-DD`, which the page words a day against; the topic carries
it too, so it publishes when the date changes. Every route is a read, and none is the
gateway's.

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

PAGE = lb.Page(
    routes=(
        lb.Route("/api/clocks", "read", "moxi_cache.pagehandlers:clocks",
                 returns={"now": lb.Out("float"), **CLOCKS}),
        lb.Route("/api/events", "read", "moxi_cache.pagehandlers:events", stream=True,
                 topics={"clocks": lb.Out("table", items=CLOCKS)}),
    ),
    assets="web",
)

__all__ = ["CLOCKS", "PAGE", "READING", "ROW"]
