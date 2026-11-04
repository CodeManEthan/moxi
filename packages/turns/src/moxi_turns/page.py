"""turns' page: the routes its page server answers, declared as data.

    GET /api/sessions   every session turns can reach, now, with the server's `now`
    GET /api/events     the stream; its one topic, `sessions`, is the same list without
                        `now`, sent whole at each change

The page is read-only: it has no act. Putting input into a session from the page would be
a new way in, and turns offers none.

Both routes answer from the live list `moxi_turns.pagereader` keeps in the page server's
process; `pagehandlers` starts it on the page's first read or subscribe. Every route is a
read, and none is the gateway's. `cwd` is marked `path`; `trouble` and an agent's
`description`, which the harness writes and which can name a path, are marked `redact`.

The browser UI shell starts the page server on the first request for turns' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in turns' announcement, API,
CLI or console scripts does. turns' distribution doesn't require loopback, which comes
with the `ui` extra, so this module reaches it only through `moxi.modules.load` and raises
`ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"turns' page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
_FLAG = lb.Out("bool")
_WHEN = lb.Out("float")
_MAYBE_WHEN = lb.Out("float", nullable=True)

#: The last `session.ready` the endpoint raised: its `source` (`startup`, `resume`,
#: `clear`, `compact`, or null when the harness did not say) and `at`, wall-clock seconds.
READY = lb.Out("table", nullable=True, items={"source": _MAYBE, "at": _MAYBE_WHEN})

#: One observation of a session, `facts()`: null until the first read answers. `agents` is
#: the count of busy subagents, null when they could not be listed; `at` is the read's
#: wall-clock time.
FACTS = lb.Out("table", nullable=True, items={
    "turn_number": lb.Out("int"),
    "turn_open": _FLAG,
    "agents": lb.Out("int", nullable=True),
    "draft": _FLAG,
    "stale": _FLAG,
    "ready": READY,
    "at": _WHEN,
})

#: One subagent, teammate or background shell, as `agents()` gives it.
AGENT = lb.Out("table", items={
    "id": _TEXT,
    "kind": _TEXT,
    "state": _TEXT,
    "final": lb.Out("bool", nullable=True),
    "description": lb.Out("string", mark="redact", nullable=True),
    "parent_id": _MAYBE,
})

#: `agents()`: null until the first read answers, and always null on a session whose
#: capabilities lack `agents`.
AGENTS = lb.Out("table", nullable=True, items={
    "entries": lb.Out("list", items=AGENT),
    "complete": _FLAG,
})

#: One session. `key` is its endpoint's name, `<harness>-<nonce>`, fixed for the
#: endpoint's life. The fields from `harness` to `trouble` are its `session` answer and its
#: hello's capabilities, refreshed at each scan; `read_error` is a fixed sentence when its
#: watch or its last facts or agents read failed, null when none did.
SESSION = lb.Out("table", items={
    "key": _TEXT,
    "harness": _TEXT,
    "pid": lb.Out("int", nullable=True),
    "session_id": _MAYBE,
    "cwd": lb.Out("string", mark="path", nullable=True),
    "started": _MAYBE_WHEN,
    "capabilities": lb.Out("list", items=_TEXT),
    "endpoint_version": _MAYBE,
    "mod_version": _MAYBE,
    "trouble": lb.Out("string", mark="redact", nullable=True),
    "facts": FACTS,
    "agents": AGENTS,
    "read_error": _MAYBE,
})

#: The list. `listed` is false until the page server's first scan for sessions has
#: finished; `detail` is null unless the last scan failed; `sessions` is newest first.
LIST = {
    "listed": _FLAG,
    "detail": _MAYBE,
    "sessions": lb.Out("list", items=SESSION),
}

TOPIC = "sessions"


def _target(name: str) -> str:
    return f"moxi_turns.pagehandlers:{name}"


#: What the page server may hold before ui's doctor warns. Measured in a test shell on
#: Linux (Python 3.14), with the list read six times and the stream open while each fake
#: session ran a turn, then left idle for 15 s: RSS 37 MB and 9 open fds with no session,
#: 39 MB and 19 fds with 5, 41 MB and 49 fds with 20. Each session holds two connections
#: (its watch, and its facts and agents reads), so the fds grow by two a session. The RSS
#: is about twice the most seen; the fds leave room for about 55 sessions.
BUDGET = {"rss": "80M", "fds": 128}

PAGE = lb.Page(
    budget=BUDGET,
    routes=(
        lb.Route("/api/sessions", "read", _target("sessions"),
                 returns={"now": _WHEN, **LIST}),
        lb.Route("/api/events", "read", _target("events"), stream=True,
                 topics={TOPIC: lb.Out("table", items=LIST)}),
    ),
)

__all__ = ["AGENT", "AGENTS", "BUDGET", "FACTS", "LIST", "PAGE", "READY", "SESSION", "TOPIC"]
