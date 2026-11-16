"""loadout's page: the routes its page server answers, declared as data.

    GET  /api/status      every item's state for each harness, as `moxi loadout status
                          --json` prints it
    GET  /api/view        everything the page's items draw, from the one plan `status`
                          builds: its rows, the sections, the items and the global file
    GET  /api/list        every item each section lists, and the sections, as `moxi
                          loadout list --json` gives the items
    GET  /api/global      the global mode, and what the next install makes of each global
                          file, as `moxi loadout global show --json` prints it
    GET  /api/folder      the loadout folder: its files git hasn't committed, the skills
                          and agents in it no section lists, and whether a commit installs
                          it again
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
    POST /api/global-remove  `moxi loadout global remove`, confirmed, as a job (202)

**An act from the page does what the same verb does in the user's terminal, or refuses
before writing.** Every act starts a page job (`moxi_loadout.pagejob`), which runs the
verb through the CLI's own code path in a process of its own, and answers 202 with the
job's status path, `/api/job?job=<id>`. The page server's threads never install. The
reads run here, take no lock and bound every git call as the doctor does.

`global mode off` is the same command as `global remove`, so the page reaches it only
through the confirmed `/api/global-remove`.

Every value that can hold a path carries a mark: a file or a target is `path`, and a
sentence, a section's name or an item's source that can name a path is `redact`. Only the
gateway acts on a mark, and no route here is the gateway's.

The page's assets are what the repository's web build writes from `packages/loadout/web/`
into this package's `web/`: `page.js`, the page, and `drift.js`, the drift block.

The browser UI shell starts the page server on the first request for loadout's page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in loadout's announcement, CLI,
install listener or git hook pieces does. loadout's distribution doesn't require ui, which
comes with the `ui` extra, so this module reaches loopback, ui's page-server base, only
through `moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"loadout's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

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

#: A row as the view gives it: `ROW`, and how loadout places the item (`form`: `link`,
#: `junction`, `copy`, `generated`, `entry`, or null), whether a `blocked` row's
#: placement of loadout's was edited where it stands (`edited`), the items the Codex
#: rules file holds on its row (`covers`), whether the entry was the user's before
#: loadout's (`prior`), the sections under which `install` applies the row (`applies`:
#: `section`, `whole`, or null when no run does), and whether install covers a
#: `would-commit` row with its ignore file (`ignores`).
VIEW_ROW = lb.Out("table", items={
    **ROW.items,
    "form": _MAYBE,
    "edited": lb.Out("bool"),
    "covers": lb.Out("list", items=_TEXT, nullable=True),
    "prior": lb.Out("bool"),
    "applies": _MAYBE,
    "ignores": lb.Out("bool"),
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

#: One global file: the `harness` that reads it and the `parts` it reads through loadout
#: (the shared file, then the harness's part while in use); `changes` whether the next
#: install changes it; `diff` a unified diff
#: of that change. A file `if-absent` places has `placed` true, its `state`, and in
#: `diff` the reason that state isn't `ok`.
GLOBAL_FILE = lb.Out("table", items={
    "file": _PATH,
    "harness": _TEXT,
    "parts": lb.Out("list", items=_PATH),
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
#: A section as the view gives it: `SECTION`, and `refused`, why a key section can't
#: be used this run (null when it resolved): its `cause` (`no-projects`, `not-found`,
#: `unreadable`, `busy`, `no-root`, `unloadable`), the refusal's `line`, and `last_root`,
#: the root loadout's ledger recorded when it last placed the section, when its records
#: agree on one.
VIEW_SECTION = lb.Out("table", items={
    **SECTION.items,
    "refused": lb.Out("table", nullable=True, items={
        "cause": _TEXT, "line": _SAYS, "last_root": _MAYBE_PATH}),
})

#: An item as the view gives it: `ITEM`, the `module` of a module's skill (else null),
#: and `what` it writes (null for a skill or an agent): a hook's `event`, `matcher` and
#: `command`; a permissions item's `allow`, `ask` and `deny` shell prefixes; an MCP
#: server's `command` or `url`. A key the kind has no value for is null.
_MAYBE_TEXTS = lb.Out("list", items=_SAYS, nullable=True)
VIEW_ITEM = lb.Out("table", items={
    **ITEM.items,
    "module": _MAYBE,
    "what": lb.Out("table", nullable=True, items={
        "event": _MAYBE_SAYS, "matcher": _MAYBE_SAYS, "command": _MAYBE_SAYS,
        "url": _MAYBE_SAYS, "allow": _MAYBE_TEXTS, "ask": _MAYBE_TEXTS,
        "deny": _MAYBE_TEXTS}),
})

#: A note on one item for one harness: `code` one of `trust` (Codex runs a new hook only
#: once trusted), `unconfirmed` (no run has confirmed the harness reads it as loadout
#: writes it), `no-hooks` (OpenCode takes no hooks), `writes-nothing` (loadout writes
#: nothing for that harness).
ITEM_NOTE = lb.Out("table", items={"kind": _TEXT, "name": _TEXT, "harness": _TEXT,
                                   "code": _TEXT})

#: The global file as the view gives it: `/api/global`'s, with `source`, the folder's
#: global text (null when the manifest names none), and each file's `form`, what loadout
#: adds there: `line`, `block`, `instructions`, `placed`, or `left` where the harness's
#: own file is there.
VIEW_GLOBAL = lb.Out("table", items={
    "source": _MAYBE_PATH,
    "mode": _TEXT,
    "chosen": lb.Out("bool"),
    "files": lb.Out("list", items=lb.Out("table", items={
        **GLOBAL_FILE.items, "form": _MAYBE})),
})

#: Everything the page's items draw, from the one plan `status` builds: `status`'s
#: `rows`, `lines`, `notes` and `counts`; `note`, the drift block's; the `home` folder,
#: the loadout `folder` and its `manifest`; `harnesses`, the harness table's names in its
#: order; `resolved_all`, whether every section resolved; the `sections` and `items` as
#: `/api/list` gives them, with what the view adds; the notes on each item for each
#: harness (`item_notes`); and `global` as `/api/global` gives it, with its source and
#: forms. The paths are null only with `problem` set, and `home` when nothing places a
#: home folder.
VIEW = {
    "problem": _PROBLEM,
    "lines": _LINES,
    "notes": _LINES,
    "counts": COUNTS,
    "note": _MAYBE_SAYS,
    "home": _MAYBE_PATH,
    "folder": _MAYBE_PATH,
    "manifest": _MAYBE_PATH,
    "harnesses": lb.Out("list", items=_TEXT),
    "resolved_all": lb.Out("bool"),
    "sections": lb.Out("list", items=VIEW_SECTION),
    "items": lb.Out("list", items=VIEW_ITEM),
    "rows": lb.Out("list", items=VIEW_ROW),
    "item_notes": lb.Out("list", items=ITEM_NOTE),
    "global": VIEW_GLOBAL,
}
#: The loadout folder: `uncommitted`, the files under it git hasn't committed, relative
#: to it (at most 20, `more` counting the rest), null when it is in no
#: git work tree; `unlisted`, each skill and agent file in it no section lists, with its
#: path; `reinstalls`, whether a commit installs it again through repo's git hook
#: pieces. `state` is `ok`, or `error` when git failed or didn't answer in its time, or
#: the settings can't be read, and `detail` says which (null when `ok`).
SOURCE_DIR = {
    "state": _TEXT,
    "detail": _MAYBE_SAYS,
    "uncommitted": lb.Out("list", items=_SAYS, nullable=True),
    "more": lb.Out("int"),
    "unlisted": lb.Out("list", items=lb.Out("table", items={
        "kind": _TEXT, "name": _SAYS, "source": _PATH})),
    "reinstalls": lb.Out("bool"),
}
#: `reason` says why there is no list: projects absent, busy or unreadable; `cause` names
#: it: `absent`, `busy`, `unreadable`, `error`, or null when projects listed.
PROJECTS = {"projects": lb.Out("list", items=PROJECT), "reason": _MAYBE_SAYS,
            "cause": _MAYBE}
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
        lb.Route("/api/view", "read", _target("view"), returns=VIEW),
        lb.Route("/api/list", "read", _target("listing"), returns=LIST),
        lb.Route("/api/global", "read", _target("global_preview"), returns=GLOBAL),
        lb.Route("/api/folder", "read", _target("source_dir"), returns=SOURCE_DIR),
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
        lb.Route("/api/global-remove", "act", _target("global_remove"), returns={},
                 confirmed=True, status=JOB_ROUTE),
    ),
)

__all__ = ["BUDGET", "COUNTS", "DRIFT", "SOURCE_DIR", "GLOBAL", "GLOBAL_FILE", "ITEM", "JOB", "JOB_ROUTE",
           "LIST", "OUTCOME", "PAGE", "PROJECT", "PROJECTS", "ROW", "SECTION", "STATUS",
           "ITEM_NOTE", "VIEW", "VIEW_GLOBAL", "VIEW_ITEM", "VIEW_ROW", "VIEW_SECTION"]
