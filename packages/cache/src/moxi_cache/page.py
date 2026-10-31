"""cache's page: the routes its page server answers, declared as data.

    GET /api/clocks   every live session's clock record, with the server's `now`
    GET /api/events   the stream; its one topic, `clocks`, is the same list without `now`

Both answer from `api.clock_records()`, the listing `moxi cache timers` reads. Every
route is a read, and none is the gateway's.

The browser UI shell starts the page server on the first request for cache's page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in cache's announcement, API,
CLI or console scripts does. cache's distribution doesn't require loopback, which comes
with the `ui` extra, so this module reaches it only through `moxi.modules.load` and raises
`ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"cache's page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

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

#: The list and how it stands: `state` is `up` or `error`, and `detail` is null when up.
CLOCKS = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
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

__all__ = ["CLOCKS", "PAGE", "ROW"]
