"""loadout's page: the routes its page server answers, declared as data.

    GET  /api/status      every item's state for each harness, as `moxi loadout status
                          --json` prints it
    GET  /api/list        every item each section lists, and the sections, as `moxi
                          loadout list --json` gives the items
    GET  /api/global      the global mode, and what the next install makes of each global
                          file, as `moxi loadout global show --json` prints it
    GET  /api/projects    the projects a project section can name by key
    GET  /api/drift       how many items aren't `ok` or `off`
    GET  /api/job         one page job: running, done (with its outcome) or interrupted
    POST /api/add         `moxi loadout add KIND NAME [--project P]`, as a job (202)
    POST /api/remove      `moxi loadout remove KIND NAME [--project P]`, as a job (202)
    POST /api/enable      `moxi loadout enable ID [--user | --project P]`, as a job (202)
    POST /api/disable     `moxi loadout disable ID [--user | --project P]`, as a job (202)
    POST /api/global-mode `moxi loadout global mode MODE`, managed or if-absent, as a job
                          (202)
    POST /api/install     `moxi loadout install [--user] [--project P]...`, as a job (202)
    POST /api/replace     `moxi loadout install --replace ID...`, confirmed, as a job (202)
    POST /api/global-off  `moxi loadout global off`, confirmed, as a job (202)

**An act from the page does what the same verb does in the user's terminal, or refuses
before writing.** Every act starts a page job (`moxi_loadout.pagejob`), which runs the
verb through the CLI's own code path in a process of its own, and answers 202 with the
job's status path, `/api/job?job=<id>`. The page server's threads never install. The
reads run here, take no lock and bound every git call as the doctor does.

`global mode off` is the same command as `global off`, so the page reaches it only
through the confirmed `/api/global-off`.

Every value that can hold a path carries a mark: a file or a target is `path`, and a
sentence, a section's name or an item's source that can name a path is `redact`. Only the
gateway acts on a mark, and no route here is the gateway's.

The page's assets are what the repository's web build writes from `packages/loadout/web/`
into this package's `web/`: `page.js`, the page, and `drift.js`, the drift block.

The browser UI shell starts the page server on the first request for loadout's page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in loadout's announcement, CLI,
install listener or git hook pieces does. loadout's distribution doesn't require
loopback, which comes with the `ui` extra, so this module reaches it only through
`moxi.modules.load` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"loadout's page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

#: The item kinds, the global modes and the states, as the manifest and the planner name
#: them (a test holds them equal), written out so this module imports none of loadout's.
KINDS = ("skill", "agent", "hook", "permissions", "mcp")
MODES = ("off", "managed", "if-absent")
STATES = ("ok", "missing", "stale", "modified", "blocked", "would-commit", "bad-source",
          "off")
#: Where an act's job is read.
JOB_ROUTE = "/api/job"
#: The longest name, id or section name a body takes.
NAME_MAX = 256
#: The longest path a body takes.
PATH_MAX = 4096
#: The most sections, or replace values, one act names.
LIST_MAX = 64

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
#: A sentence, or a name that can be a path.
_SAYS = lb.Out("string", mark="redact")
_MAYBE_SAYS = lb.Out("string", mark="redact", nullable=True)
_PATH = lb.Out("string", mark="path")
_MAYBE_PATH = lb.Out("string", mark="path", nullable=True)
_LINES = lb.Out("list", items=_SAYS)
#: Why a read has nothing to show (a manifest, ledger or config that can't be read), or
#: null when it reads.
_PROBLEM = _MAYBE_SAYS

#: One item for one harness, or a placement no longer wanted: `state` one of `ok`,
#: `missing`, `stale`, `modified`, `blocked`, `would-commit`, `bad-source` and `off`.
#: `scope` is `user` or a project section's name, null for a placement whose record
#: names none; `name` can be a path (an ignore file's row). `replaceable` says that
#: `install --replace` places over what stands in the row's way: an occupant, one git
#: tracks among them, or a file edited by hand.
ROW = lb.Out("table", items={
    "scope": _MAYBE_SAYS,
    "kind": _MAYBE,
    "name": _MAYBE_SAYS,
    "harness": _MAYBE,
    "state": _TEXT,
    "reason": _SAYS,
    "target": _MAYBE_PATH,
    "replaceable": lb.Out("bool"),
})

#: How many rows are in each state; a state no row is in is left out.
COUNTS = lb.Out("table", items={state: lb.Out("int") for state in STATES})

#: What a run came to, as `--json` prints it: its rows, the lines said on the way, notes,
#: the counts and the exit code.
OUTCOME = {
    "rows": lb.Out("list", items=ROW),
    "lines": _LINES,
    "notes": _LINES,
    "counts": COUNTS,
    "exit": lb.Out("int"),
}

#: One item a section lists: `harnesses` null for every harness; `source` a path, `module
#: <name>` for a module's skill, or `held by the project`; `disabled` the harnesses
#: switched off there, `*` for all.
ITEM = lb.Out("table", items={
    "kind": _TEXT,
    "name": _TEXT,
    "scope": _SAYS,
    "harnesses": lb.Out("list", items=_TEXT, nullable=True),
    "source": _SAYS,
    "disabled": lb.Out("list", items=_TEXT),
})

#: One section of the manifest: `where` is `user`, `path` or `key`; `root` a path
#: section's absolute root, else null. A section is named to an act by `name`.
SECTION = lb.Out("table", items={
    "name": _SAYS,
    "where": _TEXT,
    "root": _MAYBE_PATH,
})

#: One global file: `changes` whether the next install changes it; `diff` a unified diff
#: of that change. A file `if-absent` places has `placed` true, its `state`, and in
#: `diff` the reason that state isn't `ok`.
GLOBAL_FILE = lb.Out("table", items={
    "file": _PATH,
    "changes": lb.Out("bool"),
    "diff": _SAYS,
    "placed": lb.Out("bool"),
    "state": _MAYBE,
})

#: One project: its key and root.
PROJECT = lb.Out("table", items={"key": _TEXT, "root": _PATH})

STATUS = {"problem": _PROBLEM, **OUTCOME}
LIST = {
    "problem": _PROBLEM,
    "manifest": _MAYBE_PATH,
    "sections": lb.Out("list", items=SECTION),
    "items": lb.Out("list", items=ITEM),
}
#: `mode` is the mode in force; `chosen` false while the manifest sets none (it reads
#: `off` then).
GLOBAL = {
    "problem": _PROBLEM,
    "mode": _TEXT,
    "chosen": lb.Out("bool"),
    "files": lb.Out("list", items=GLOBAL_FILE),
}
#: `reason` says why there is no list: projects absent, busy or unreadable.
PROJECTS = {"projects": lb.Out("list", items=PROJECT), "reason": _MAYBE_SAYS}
#: `count` is the items not `ok` or `off`, each once however many of its harnesses
#: aren't; `note` the line that the page would write elsewhere than the user's last run
#: in a terminal, or that it can't tell, when so.
DRIFT = {"problem": _PROBLEM, "count": lb.Out("int"), "note": _MAYBE_SAYS}
#: A page job: `state` is `running`, `done` or `interrupted`; `argv` the verb it runs;
#: `exit`, `outcome` and `lines` as the job left them once done (`outcome` null when the
#: verb printed no outcome); an interrupted job's `lines` say to run it again.
JOB = {
    "job": _TEXT,
    "argv": lb.Out("list", items=_SAYS, nullable=True),
    "state": _TEXT,
    "exit": lb.Out("int", nullable=True),
    "outcome": lb.Out("table", items=OUTCOME, nullable=True),
    "lines": _LINES,
}

_NAME = lb.Field("string", max=NAME_MAX)
#: A project section: its name exactly as the manifest has it, a project key, or an
#: absolute path.
_SECTION = lb.Field("string", required=False, max=PATH_MAX)
_SECTIONS = lb.Field("list", required=False, max=LIST_MAX,
                     items=lb.Field("string", max=PATH_MAX))
_USER = lb.Field("bool", required=False)
_ITEM_BODY = {"kind": lb.Field("enum", values=KINDS), "name": _NAME, "project": _SECTION}
_ID_BODY = {"id": _NAME, "user": _USER, "project": _SECTION}


def _target(name: str) -> str:
    return f"moxi_loadout.pagehandlers:{name}"


def _act(path: str, handler: str, body: dict) -> lb.Route:
    return lb.Route(path, "act", _target(handler), body=body, returns={})


#: What the page server may hold before ui's doctor warns. Measured in a test shell on
#: Linux (Python 3.12), with the page loaded, then reloaded five times, then left idle:
#: RSS 37-40 MB with 2 to 40 skills and 45 MB with 200, and 8 open fds idle, 12 to 20
#: just after the page's reads. A job is a process of its own and isn't counted here.
#: The budget is about twice the most that was seen.
BUDGET = {"rss": "80M", "fds": 40}

PAGE = lb.Page(
    assets="web",
    budget=BUDGET,
    routes=(
        lb.Route("/api/status", "read", _target("status"), returns=STATUS),
        lb.Route("/api/list", "read", _target("listing"), returns=LIST),
        lb.Route("/api/global", "read", _target("global_preview"), returns=GLOBAL),
        lb.Route("/api/projects", "read", _target("projects"), returns=PROJECTS),
        lb.Route("/api/drift", "read", _target("drift"), returns=DRIFT),
        lb.Route(JOB_ROUTE, "read", _target("job"),
                 query={"job": lb.Field("string", max=64)}, returns=JOB),
        _act("/api/add", "add", _ITEM_BODY),
        _act("/api/remove", "remove", _ITEM_BODY),
        _act("/api/enable", "enable", _ID_BODY),
        _act("/api/disable", "disable", _ID_BODY),
        _act("/api/global-mode", "global_mode",
             {"mode": lb.Field("enum", values=tuple(m for m in MODES if m != "off"))}),
        _act("/api/install", "install", {"user": _USER, "projects": _SECTIONS}),
        lb.Route("/api/replace", "act", _target("replace"), returns={},
                 body={"replace": lb.Field("list", max=LIST_MAX,
                                           items=lb.Field("string", max=PATH_MAX)),
                       "user": _USER, "projects": _SECTIONS},
                 confirmed=True, status=JOB_ROUTE),
        lb.Route("/api/global-off", "act", _target("global_off"), returns={},
                 confirmed=True, status=JOB_ROUTE),
    ),
)

__all__ = ["BUDGET", "COUNTS", "DRIFT", "GLOBAL", "GLOBAL_FILE", "ITEM", "JOB", "JOB_ROUTE",
           "LIST", "OUTCOME", "PAGE", "PROJECT", "PROJECTS", "ROW", "SECTION", "STATUS"]
