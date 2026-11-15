"""comms' page: the routes its page server answers, declared as data.

    GET /api/roster   who can be messaged, one row per address, and the mail waiting per
                      name, with the server's `now` and local date
    GET /api/events   the stream; its one topic, `roster`, is the same without `now` and
                      the date
    GET /api/mail     Home's Comms row: the names with mail waiting that no session holds
    GET /api/messages one day's messages, newest first, each with its rows in the books,
                      and how many messages came before that day
    GET /api/mail-state  what the agent rows need beyond the roster: each address's
                      rows in the books, the asks that wait for the operator's reply,
                      the open asks between agents, and each address's newest message
    GET /api/queue    one address's messages in the books' queue, oldest first, each
                      with that address's row first
    GET /api/thread   the messages between two addresses, or by or to one, oldest
                      first, and on a refresh the newer ones and the rows in the books
                      of every recipient shown

The roster and its stream answer from `api.roster_rows()`, which `moxi comms agents
--json` prints, and the `Roster` it was joined from; the mail read from one
`api.roster()`. The message reads ask the daemon nothing: they read comms' record and
books in the page server's process on connections that can't write (`pagereads`), so
they answer with the daemon down. `/api/roster`'s `today` is the page server's local date, `YYYY-MM-DD`,
which the page words a day against; the topic leaves it out, so it publishes only when a
row or the mail waiting changes. Every route is a read. `/api/mail` is the one route the
gateway reaches: it holds names, counts and times, and no message text or path, so it
carries no mark. Every message's `text` is marked `redact`.

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
#: when the daemon couldn't say. `cache` is `api.CacheFact`'s `{kind, until}`: `warm` or
#: `cold`, and when it goes or went cold, epoch seconds, whole; null for a standing row,
#: any other read and a box without the cache module.
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
    "cache": lb.Out("table", nullable=True, items={
        "kind": lb.Out("string"),
        "until": lb.Out("float"),
    }),
})

#: The rows and how they stand: `state` is `up`, `offline` or `error`; `detail` is null
#: unless `error`. `waiting` is every name with mail queued, bound or not, oldest `since`
#: first: its count and its oldest message's time, epoch seconds. Both lists are empty
#: unless `state` is `up`.
ROSTER = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
    "rows": lb.Out("list", items=ROW),
    "waiting": lb.Out("list", items=lb.Out("table", items={
        "qualified": lb.Out("string"),
        "count": lb.Out("int"),
        "since": lb.Out("float"),
    })),
}

#: Home's Comms row: the names with mail waiting that no live session holds, oldest
#: `since` first. `name` is the part before `@`, or the qualified name when two names
#: here share it; `since` is the oldest waiting message's time, in epoch seconds.
#: `state` and `detail` are as `ROSTER`'s.
MAIL = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
    "waiting": lb.Out("list", items=lb.Out("table", items={
        "qualified": lb.Out("string"),
        "name": lb.Out("string"),
        "count": lb.Out("int"),
        "since": lb.Out("float"),
    })),
}

#: One recipient's row of a message in the books. `state` is the queue row's
#: `waiting` (nothing holds it yet), `handed` or `in_digest` (in its session, read when
#: the turn ends), or the reason the message left the queue undelivered: `withdrawn`,
#: `expired` or `reserved`. `held` is the queue row's hold for the next turn; `takeable`
#: says the row has never been in a digest, so its author can still take it back. A
#: recipient with no row has had the message.
DELIVERY = lb.Out("table", items={
    "qualified": lb.Out("string"),
    "state": lb.Out("string"),
    "held": lb.Out("bool"),
    "takeable": lb.Out("bool"),
})

#: One message as every message read sends it. `message_id` and `reply_to` are strings
#: of digits, which the browser never does arithmetic on; `at` is the record's time,
#: epoch seconds. `author` is the attribution's qualified name and `author_tier` its tier
#: (`operator` is the operator, whatever name the directory gives him). `to` is the
#: recipient tags as line zero writes them; `text` the body with line zero removed.
#: `withdrawn` says the record marks it withdrawn; `rows` are its rows in the books, by
#: qualified name.
MESSAGE = lb.Out("table", items={
    "message_id": lb.Out("string"),
    "at": lb.Out("float"),
    "author": lb.Out("string"),
    "author_tier": lb.Out("string"),
    "to": lb.Out("list", items=lb.Out("string")),
    "posture": lb.Out("string", nullable=True),
    "reply_to": lb.Out("string", nullable=True),
    "text": lb.Out("string", mark="redact"),
    "withdrawn": lb.Out("bool"),
    "rows": lb.Out("list", items=DELIVERY),
})

#: `/api/messages`: the messages of `day` (a local date, `YYYY-MM-DD`) below the query's
#: `before`, newest first, at most `limit`; `more`, how many of the day's are below the
#: last one sent; `earlier`, the messages before the day, or null when there are none:
#: how many, the local day of the newest, the oldest one's time, and `before`, the
#: cursor (a message id, as a string) whose read with that `day` lists them.
#: `today` is the page server's local date. `state` is `up` or `error`, `detail` null
#: unless `error`; an error answers no message.
MESSAGES = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
    "today": lb.Out("string"),
    "day": lb.Out("string"),
    "messages": lb.Out("list", items=MESSAGE),
    "more": lb.Out("int"),
    "earlier": lb.Out("table", nullable=True, items={
        "day": lb.Out("string"),
        "count": lb.Out("int"),
        "first_at": lb.Out("float"),
        "before": lb.Out("string"),
    }),
}

#: `/api/mail-state`, read from the books and the record's last `ASK_TTL` (a day).
#: `queued`: each address with a row in the books, by qualified name: how many rows wait,
#: how many are in its session (`handed` or `in_digest`), and the authors of the waiting
#: ones, oldest message first, each once. `owed`: the verified `[reply-once]` asks to the
#: operator that no later message of his answers, oldest first. `asks`: each open ask
#: between two agents, the newest per pair; `read` is false while the books hold the
#: awaited name's row for it, else null. `last`: each address's newest message, by it or
#: to it. `state` and `detail` are as `MESSAGES`'; an error answers empty lists.
MAIL_STATE = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
    "today": lb.Out("string"),
    "queued": lb.Out("list", items=lb.Out("table", items={
        "qualified": lb.Out("string"),
        "waiting": lb.Out("int"),
        "handed": lb.Out("int"),
        "from": lb.Out("list", items=lb.Out("string")),
    })),
    "owed": lb.Out("list", items=lb.Out("table", items={
        "message_id": lb.Out("string"),
        "author": lb.Out("string"),
        "at": lb.Out("float"),
        "text": lb.Out("string", mark="redact"),
    })),
    "asks": lb.Out("list", items=lb.Out("table", items={
        "asker": lb.Out("string"),
        "awaited": lb.Out("string"),
        "message_id": lb.Out("string"),
        "at": lb.Out("float"),
        "read": lb.Out("bool", nullable=True),
    })),
    "last": lb.Out("list", items=lb.Out("table", items={
        "qualified": lb.Out("string"),
        "at": lb.Out("float"),
    })),
}

#: `/api/queue?name=`: the messages the address `name` has a row for in the books'
#: `queue` (waiting, or in its session), oldest first, at most fifty, each with that
#: address's row first in `rows`; `more`, how many rows are past the last one sent.
#: `state` and `detail` are as `MESSAGES`'; an error answers no message.
QUEUE = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
    "messages": lb.Out("list", items=MESSAGE),
    "more": lb.Out("int"),
}

#: `/api/thread?a=&b=&before=&after=&limit=`: the messages between the addresses `a`
#: and `b` (by one, with a tag naming the other), or by or to `a` without `b`, oldest
#: first. Without `after`: the newest `limit` below `before` (or the newest of all), and
#: `earlier`, the pair's messages before the oldest sent, how many and the oldest one's
#: time, or null when there are none; `changed` is null. With `after` (the newest
#: message id the page holds), a refresh: every message of the pair above it, `earlier`
#: null, and `changed`, the read state of every recipient the view shows: between `a`
#: and `b`, each message at or below `after` with a row in the books for either, with
#: those rows, so a message the page holds that `changed` doesn't list has no row for
#: either; without `b`, each of `a`'s messages at or below `after` with a row for any
#: name, with all its rows, so a message not listed has none. `count_today` is the pair's messages today, `today` the page
#: server's local date. `state` and `detail` are as `MESSAGES`'; an error answers no
#: message.
THREAD = {
    "state": lb.Out("string"),
    "detail": lb.Out("string", nullable=True),
    "today": lb.Out("string"),
    "messages": lb.Out("list", items=MESSAGE),
    "count_today": lb.Out("int"),
    "earlier": lb.Out("table", nullable=True, items={
        "count": lb.Out("int"),
        "first_at": lb.Out("float"),
    }),
    "changed": lb.Out("list", nullable=True, items=lb.Out("table", items={
        "message_id": lb.Out("string"),
        "rows": lb.Out("list", items=DELIVERY),
    })),
}

PAGE = lb.Page(
    routes=(
        lb.Route("/api/roster", "read", "moxi_comms.pagehandlers:roster",
                 returns={"now": lb.Out("float"), "today": lb.Out("string"), **ROSTER}),
        lb.Route("/api/mail", "read", "moxi_comms.pagehandlers:mail", returns=MAIL,
                 gateway=True),
        lb.Route("/api/events", "read", "moxi_comms.pagehandlers:events", stream=True,
                 topics={"roster": lb.Out("table", items=ROSTER)}),
        lb.Route("/api/messages", "read", "moxi_comms.pagereads:messages",
                 query={"day": lb.Field("string", required=False, max=10),
                        "before": lb.Field("string", required=False, max=20),
                        "limit": lb.Field("int", required=False, max=500)},
                 returns=MESSAGES),
        lb.Route("/api/mail-state", "read", "moxi_comms.pagereads:mail_state",
                 returns=MAIL_STATE),
        lb.Route("/api/queue", "read", "moxi_comms.pagereads:queue",
                 query={"name": lb.Field("string", max=200)},
                 returns=QUEUE),
        lb.Route("/api/thread", "read", "moxi_comms.pagereads:thread",
                 query={"a": lb.Field("string", max=200),
                        "b": lb.Field("string", required=False, max=200),
                        "before": lb.Field("string", required=False, max=20),
                        "after": lb.Field("string", required=False, max=20),
                        "limit": lb.Field("int", required=False, max=100)},
                 returns=THREAD),
    ),
    assets="web",
)

__all__ = ["DELIVERY", "MAIL", "MAIL_STATE", "MESSAGE", "MESSAGES", "PAGE", "QUEUE", "ROSTER",
           "ROW", "THREAD"]
