"""turns' page: the routes its page server answers, declared as data.

    GET /api/sessions   every session turns can reach, now, with the server's `now`
    GET /api/events     the stream; its one topic, `sessions`, is the same list without
                        `now`, sent whole at each change

The page is read-only: it has no act. Putting input into a session from the page would be
a new way in, and turns offers none.

Both routes answer from the live list `moxi_turns.pagereader` keeps in the page server's
process; `pagehandlers` starts it on the page's first read or subscribe. Every route is a
read, and none is the gateway's. `cwd` and `home` are marked `path`; `trouble`, an
agent's `description` and the `typed_name`, which the harness or the person writes and
which can name a path, are marked `redact`.

The page's assets are what the repository's web build writes from `packages/turns/web/`
into this package's `web/`: `page.js`, the panel, and its build stamp.

The browser UI shell starts the page server on the first request for turns' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in turns' announcement, API,
CLI or console scripts does. turns' distribution doesn't require ui, which comes with the
`ui` extra, so this module reaches loopback, ui's page-server base, only through
`moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"turns' page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
_FLAG = lb.Out("bool")
_WHEN = lb.Out("float")
_MAYBE_WHEN = lb.Out("float", nullable=True)

#: The last `session.ready` the endpoint raised: its `source` (`startup`, `resume`,
#: `clear`, `compact`, or null when the harness did not say), the `session_id` it was
#: raised for, and `at`, wall-clock seconds.
READY = lb.Out("table", nullable=True, items={"source": _MAYBE, "session_id": _MAYBE,
                                              "at": _MAYBE_WHEN})

#: One observation of a session, `facts()`: null until the first read answers.
#: `session_id` is the id the reading was taken under; `turn_started_at` is the open
#: turn's start, null when no turn is open and while an open turn waits for its tie.
#: `agents` is the count of busy subagents, null when they could not be listed; `draft`
#: is true for a draft in the prompt box, false for an empty box, and null when the box
#: was not read; `at` is the read's wall-clock time. `ended_at` is when the session's last
#: turn ended, as its endpoint keeps it, null before the first end at this session id or
#: from an endpoint that does not keep it. `live_error` says why the live part did not
#: read: `timeout` (the session did not answer in time) or `failed`; null when it did.
FACTS = lb.Out("table", nullable=True, items={
    "session_id": _MAYBE,
    "turn_number": lb.Out("int"),
    "turn_open": _FLAG,
    "turn_started_at": _MAYBE_WHEN,
    "agents": lb.Out("int", nullable=True),
    "draft": lb.Out("bool", nullable=True),
    "stale": _FLAG,
    "ready": READY,
    "at": _WHEN,
    "ended_at": _MAYBE_WHEN,
    "live_error": _MAYBE,
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
#: hello's capabilities, refreshed at each scan; `facts` and `agents` are read again at
#: least every 30 s; `read_error` is a fixed sentence when its watch or its last facts or
#: agents read failed, null when none did: with a read failed, the reason it failed, one
#: per kind of failure. `ended_at` is when the session's last turn ended, sent only while
#: the latest facts show no turn open: the endpoint's own `facts.ended_at`, else the page
#: server's wall-clock time when its watch saw that turn end at that same session id and
#: turn number, else null. `typed_name` is the name the person typed for the session in
#: the harness (Claude Code's `--name` or `/rename`), null when there is none or it can't
#: be read. `has_box` is true when turns reads the harness's prompt box, so `facts.draft` can
#: be true for a draft; on the other harnesses `draft` is always false. `unread_since` is
#: the page server's wall-clock time when a run of unread readings began (a facts or
#: agents read failed, or the facts reading has `agents` null), null when none runs.
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
    "ended_at": _MAYBE_WHEN,
    "has_box": _FLAG,
    "unread_since": _MAYBE_WHEN,
    "typed_name": lb.Out("string", mark="redact", nullable=True),
})

#: The list. `listed` is false until the page server's first scan for sessions has
#: finished; `detail` is null unless the last scan failed; `home` is the page server's
#: home folder, so the page can write a folder in `~` form; `sessions` is newest first.
#: `reach` is what this machine can show, null until its first read answers: `claude` is
#: `start` (a Claude Code session started by hand runs with turns), `setup` (`moxi install
#: turns` would make it so), `absent` (Claude Code was not found when turns was
#: installed), or null when that can't be told or the install would not fix it; `codex`
#: says whether launched Codex sessions can run under turns' host.
REACH = lb.Out("table", nullable=True, items={"claude": _MAYBE, "codex": _FLAG})

LIST = {
    "listed": _FLAG,
    "detail": _MAYBE,
    "home": lb.Out("string", mark="path"),
    "reach": REACH,
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
    assets="web",
    budget=BUDGET,
    routes=(
        lb.Route("/api/sessions", "read", _target("sessions"),
                 returns={"now": _WHEN, **LIST}),
        lb.Route("/api/events", "read", _target("events"), stream=True,
                 topics={TOPIC: lb.Out("table", items=LIST)}),
    ),
)

__all__ = ["AGENT", "AGENTS", "BUDGET", "FACTS", "LIST", "PAGE", "REACH", "READY", "SESSION",
           "TOPIC"]
