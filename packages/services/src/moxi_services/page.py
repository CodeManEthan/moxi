"""services' page: the routes its page server answers, declared as data.

    GET  /api/rows      every row, each with its verdict, its state and the last health
                        result, and the modules whose run-time declarations failed
    GET  /api/row       one row, by `id`, with a timer's newest runs and the log hint
    GET  /api/projects  every project, by key, for the add form's picker
    GET  /api/status    what the act `job` came to: running, done, failed, refused, or
                        unknown for a job this server holds no record of
    GET  /api/events    the stream; its one topic, `rows`, is what `/api/rows` answers
    POST /api/start     `moxi services start ID`, a daemon; confirmed (202)
    POST /api/stop      `moxi services stop ID`; confirmed (202)
    POST /api/restart   `moxi services restart ID`, a daemon; confirmed (202)
    POST /api/run       a timer's run now (`start`), or with `restart` its run ended and
                        run once; confirmed (202)
    POST /api/enable    `moxi services enable ID`; confirmed (202)
    POST /api/disable   `moxi services disable ID`; confirmed (202)
    POST /api/remove    `moxi services remove ID`; confirmed (202)
    POST /api/add       `moxi services add`, a command to write or a unit to adopt; one
                        step, no confirm (202 with the same status link)

**No act runs inside its request.** An act may wait 30 seconds for the core lock and then
up to 30 seconds on each supervisor call, and the browser shell answers 504 when a page
server gives no first byte inside 30 seconds. So every act's handler checks the body and
the row, records a job as `running`, hands the act to services' one job thread and
answers 202 with `/api/status?job=<id>` at once (`moxi_services.pagehandlers`).

**The phone reaches the reads, the stream, the status route and the six confirmed acts on
a row that exists** (start, stop, restart, run, enable and disable), and never add, remove
or the project list: from the phone a service is acted on, never added, edited or
removed, since a command sent from there would run on the desktop at every login and the
phone could not add back what it removed. The gateway acts on these only while the shell
mounts its acts; elsewhere the phone's sheet points to the desk.

**Marks.** Every string that is not a name, an id, a time or a word of a fixed set is
marked: a command line and a health command `redact` (either may carry a token), a
working directory `path`, and `redact` on every text that passes the supervisor's or the
core's words through (a verdict's reason and fix, the supervisor's detail, a timer's last
run, a health check's line, an act's lines and fix, the log hint) and on a row's doc. The
gateway acts on the marks; the desk gets every value.

The browser UI shell starts the page server on the first request for services' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in services' announcement, CLI,
doctor or projects link does. services' distribution doesn't require ui, which comes with
the `ui` extra, so this module reaches loopback, ui's page-server base, only through
`moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"services' page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: Where an act's job is read.
STATUS_ROUTE = "/api/status"
#: The stream's one topic.
TOPIC = "rows"
#: The longest row id, name, unit or project key a request takes.
NAME_MAX = 256
#: The longest path a request takes.
PATH_MAX = 4096
#: The longest command line or health command a request takes.
COMMAND_MAX = 8192
#: The longest doc a request takes.
DOC_MAX = 1024
#: A job id's length, which a status read's `job` never passes.
JOB_MAX = 64
#: The kinds and days a request may name, as services' rows spell them.
KINDS = ("daemon", "timer")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
_FLAG = lb.Out("bool")
_MAYBE_FLAG = lb.Out("bool", nullable=True)
_MAYBE_INT = lb.Out("int", nullable=True)
_MAYBE_PATH = lb.Out("string", mark="path", nullable=True)
#: Text that can carry the supervisor's or the core's words: a path, a log file, a
#: command.
_SAYS = lb.Out("string", mark="redact")
_MAYBE_SAYS = lb.Out("string", mark="redact", nullable=True)
#: A command line, which may carry a token.
_COMMAND = lb.Out("list", items=_SAYS, mark="redact", nullable=True)

#: A row's verdict: `state` is `ok`, `warn`, `fail` or `off`; `unknown` is True when it
#: is `warn` because a value could not be read.
VERDICT = lb.Out("table", items={
    "state": _TEXT,
    "reason": _SAYS,
    "fix": _MAYBE_SAYS,
    "unknown": _FLAG,
})

#: A timer's last run: `failed`, a few words (`ok`, `exit 1`, ...), and when.
LAST_RUN = lb.Out("table", nullable=True, items={
    "failed": _FLAG,
    "text": _SAYS,
    "at": _MAYBE,
})

#: A running daemon's last health check: `state` `ok` or `fail` (a module daemon's own
#: check may also give `warn` or `off`), and the check's first line.
CHECKED = lb.Out("table", nullable=True, items={
    "state": _TEXT,
    "line": _SAYS,
})

#: One row as the page shows it.
#:
#: - `id` is `<module>/<name>`; `form` is `module`, `written` or `adopted`; `kind`
#:   `daemon` or `timer`.
#: - `flag` is its on/off (null when it can't be read); `switch` the settings key that is
#:   its on/off, null for the store's flag; `has_switch` False for a module unit that
#:   declares none, which can be stopped but not turned off.
#: - `state` is the supervisor's word (`active`, `inactive`, `failed`, `missing`,
#:   `nobackend`, `off`, `unknown`), `enabled` whether it starts at login or its schedule
#:   is armed, `pid`, `since` (UTC, ISO 8601 with `Z`) and `detail` as the supervisor
#:   gives them, null when it was not read.
#: - `last_run` a timer's last run, `checked` a running daemon's last health result.
#: - A user row's `argv` (written), `cwd`, `unit` (adopted), `schedule` (`every 5m`, `at
#:   09:00 on mon,fri`), `health` (its health command), `project`, `stopped` (a daemon
#:   stopped by hand, UTC), `removing` and `platform` (the system it was added on); null
#:   or False for a module row.
#: - `launches_session` True for a row whose runs launch an agent session, so its runs
#:   spend usage.
ROW = lb.Out("table", items={
    "id": _TEXT,
    "module": _TEXT,
    "name": _TEXT,
    "form": _TEXT,
    "kind": _TEXT,
    "doc": _SAYS,
    "project": _MAYBE,
    "launches_session": _FLAG,
    "flag": _MAYBE_FLAG,
    "switch": _MAYBE,
    "has_switch": _FLAG,
    "verdict": VERDICT,
    "state": _MAYBE,
    "enabled": _MAYBE_FLAG,
    "pid": _MAYBE_INT,
    "since": _MAYBE,
    "detail": _MAYBE_SAYS,
    "last_run": LAST_RUN,
    "checked": CHECKED,
    "argv": _COMMAND,
    "cwd": _MAYBE_PATH,
    "unit": _MAYBE,
    "schedule": _MAYBE,
    "health": _COMMAND,
    "stopped": _MAYBE,
    "removing": _FLAG,
    "platform": _MAYBE,
})

#: A module whose run-time declarations failed: its static daemons and timers are listed.
PROBLEM = lb.Out("table", items={"module": _TEXT, "reason": _SAYS})

#: Every row and how the list stands: `state` is `up` or `error`, `detail` null when up
#: and the reason (with its fix) when the store can't be read. `rows` is the user's rows,
#: then every installed module's daemons and timers; `problems` the modules whose
#: run-time declarations failed. `/api/rows` answers it, and each `rows` event carries it.
ROWS = {
    "state": _TEXT,
    "detail": _MAYBE_SAYS,
    "rows": lb.Out("list", items=ROW),
    "problems": lb.Out("list", items=PROBLEM),
}

#: One run line from the gate: when, `ran`, `off`, `not-started` or `unreadable`, the
#: exit code and how long it took.
RUN = lb.Out("table", items={
    "at": _MAYBE,
    "result": _MAYBE,
    "exit": _MAYBE_INT,
    "seconds": lb.Out("float", nullable=True),
})

#: One row with what only its own read gives: a timer's newest runs, oldest first, and
#: the hint for reading its output (a `journalctl` command or a log file).
ONE = {"row": ROW, "runs": lb.Out("list", items=RUN), "log": _MAYBE_SAYS}

#: A project for the add form's picker. `reason` says why there is no list: projects not
#: installed, busy or unreadable.
PROJECT = lb.Out("table", items={"key": _TEXT, "root": lb.Out("string", mark="path")})
PROJECTS = {"projects": lb.Out("list", items=PROJECT), "reason": _MAYBE_SAYS}

#: An act's job. `state` is `running`, `done`, `failed`, `refused` or `unknown` (no
#: record of this job here: the row's state shows the outcome). `act` is `start`,
#: `stop`, `restart`, `run`, `enable`, `disable`, `remove` or `add`, and `id` the row's
#: id; both null when unknown. Once it ends, `code` is the exit code the verb exits with,
#: `lines` what it printed and `fix` the one step that fixes a failure.
STATUS = {
    "job": _TEXT,
    "act": _MAYBE,
    "id": _MAYBE,
    "state": _TEXT,
    "code": _MAYBE_INT,
    "lines": lb.Out("list", items=_SAYS),
    "fix": _MAYBE_SAYS,
}

_ID = lb.Field("string", max=NAME_MAX)
_MAYBE_TEXT = lb.Field("string", required=False, max=NAME_MAX)
_MAYBE_BOOL = lb.Field("bool", required=False)

#: An act on one row: its id, `<module>/<name>` or a user row's bare name.
ROW_BODY = {"id": _ID}
#: A timer's run now; with `restart` a run in progress is ended first.
RUN_BODY = {"id": _ID, "restart": _MAYBE_BOOL}
#: An add. `command` is the command line of a written row, split as this system's shell
#: splits it, its first word a full path; `unit` the unit an adopted row takes on, with
#: `enable` saying whether it is on now. `every`, or `at` with `days`, is a written
#: timer's schedule. `health` is a health command line.
ADD_BODY = {
    "name": _ID,
    "kind": lb.Field("enum", values=KINDS),
    "command": lb.Field("string", required=False, max=COMMAND_MAX),
    "unit": _MAYBE_TEXT,
    "enable": _MAYBE_BOOL,
    "every": lb.Field("string", required=False, max=16),
    "at": lb.Field("list", required=False, max=48,
                   items=lb.Field("string", max=5)),
    "days": lb.Field("list", required=False, max=7,
                     items=lb.Field("enum", values=DAYS)),
    "cwd": lb.Field("string", required=False, max=PATH_MAX),
    "health": lb.Field("string", required=False, max=COMMAND_MAX),
    "project": _MAYBE_TEXT,
    "launches_session": _MAYBE_BOOL,
    "doc": lb.Field("string", required=False, max=DOC_MAX),
}


def _target(name: str) -> str:
    return f"moxi_services.pagehandlers:{name}"


def _confirmed(path: str, name: str, body: dict, *, gateway: bool) -> lb.Route:
    return lb.Route(path, "act", _target(name), body=body, returns={}, confirmed=True,
                    status=STATUS_ROUTE, gateway=gateway)


#: What the page server may hold before ui's doctor warns. Measured in a test shell on
#: Linux (Python 3.14) over a fake supervisor, with 5 and with 50 running rows, each with
#: a health command that sleeps 2 s: the page open for 40 s (its stream held, the poller
#: reading every 5 s, two health passes), sampled every 20 ms. RSS 52-54 MB at both sizes.
#: Open fds: 9 between passes; at the peak of a pass, 21 with 5 checks and 30 with 50.
#: Each running check holds its pipes, so the poller runs at most `HEALTH_AT_ONCE` (8)
#: checks at a time, and the peak stays about 30 however many rows have one; without
#: the bound, 50 checks at once peaked at 111. A real supervisor read adds a child's
#: pipes while it runs. `fds` is about one and a half times the peak; `rss` about twice.
BUDGET = {"rss": "110M", "fds": 48}

PAGE = lb.Page(
    assets="web",
    budget=BUDGET,
    routes=(
        lb.Route("/api/rows", "read", _target("listing"), returns=ROWS, gateway=True),
        lb.Route("/api/row", "read", _target("row"), query={"id": _ID}, returns=ONE,
                 gateway=True),
        lb.Route("/api/projects", "read", _target("projects"), returns=PROJECTS),
        lb.Route(STATUS_ROUTE, "read", _target("status"),
                 query={"job": lb.Field("string", max=JOB_MAX)}, returns=STATUS,
                 gateway=True),
        lb.Route("/api/events", "read", _target("events"), stream=True,
                 topics={TOPIC: lb.Out("table", items=ROWS)}, gateway=True),
        _confirmed("/api/start", "start", ROW_BODY, gateway=True),
        _confirmed("/api/stop", "stop", ROW_BODY, gateway=True),
        _confirmed("/api/restart", "restart", ROW_BODY, gateway=True),
        _confirmed("/api/run", "run", RUN_BODY, gateway=True),
        _confirmed("/api/enable", "enable", ROW_BODY, gateway=True),
        _confirmed("/api/disable", "disable", ROW_BODY, gateway=True),
        _confirmed("/api/remove", "remove", ROW_BODY, gateway=False),
        lb.Route("/api/add", "act", _target("add"), body=ADD_BODY, returns={}),
    ),
)

__all__ = ["ADD_BODY", "BUDGET", "CHECKED", "DAYS", "KINDS", "LAST_RUN", "ONE", "PAGE",
           "PROBLEM", "PROJECT", "PROJECTS", "ROW", "ROWS", "ROW_BODY", "RUN", "RUN_BODY",
           "STATUS", "STATUS_ROUTE", "TOPIC", "VERDICT"]
