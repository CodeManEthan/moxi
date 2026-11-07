"""comms' page: the routes its page server answers, declared as data.

    GET /api/roster   who can be messaged, one row per address, with the server's `now`
    GET /api/events   the stream; its one topic, `roster`, is the same rows without `now`

Both answer from `api.roster_rows()`, which `moxi comms agents --json` prints. Every route
is a read, and none is the gateway's.

The browser UI shell starts the page server on the first request for comms' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in comms' announcement, API,
CLI or console scripts does. comms' distribution doesn't require ui, which comes with the
`ui` extra, so this module reaches loopback, ui's page-server base, only through
`moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"comms' page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: One address as the page sends it: `api.RosterRow`'s fields. A standing row has no
#: binding, so its binding's fields are null, and a live row's session fields are null
#: when the daemon couldn't say.
ROW = lb.Out("table", items={
    "qualified": lb.Out("string"),
    "name": lb.Out("string"),
    "scope": lb.Out("string", nullable=True),
    "live": lb.Out("bool"),
    "kind": lb.Out("string", nullable=True),
    "module": lb.Out("string", nullable=True),
    "tier": lb.Out("string", nullable=True),
    "adapter": lb.Out("string", nullable=True),
    "directors": lb.Out("list", items=lb.Out("string")),
    "session_id": lb.Out("string", nullable=True),
    "bound_at": lb.Out("float", nullable=True),
    "awaiting": lb.Out("list", items=lb.Out("string")),
})

#: The rows and how they stand: `state` is `up`, `offline` or `error`; `detail` is null
#: unless `error`.
ROSTER = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
    "rows": lb.Out("list", items=ROW),
}

PAGE = lb.Page(
    routes=(
        lb.Route("/api/roster", "read", "moxi_comms.pagehandlers:roster",
                 returns={"now": lb.Out("float"), **ROSTER}),
        lb.Route("/api/events", "read", "moxi_comms.pagehandlers:events", stream=True,
                 topics={"roster": lb.Out("table", items=ROSTER)}),
    ),
    assets="web",
)

__all__ = ["PAGE", "ROSTER", "ROW"]
