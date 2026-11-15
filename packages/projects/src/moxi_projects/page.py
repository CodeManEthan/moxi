"""projects' page: the routes its page server answers, declared as data.

    GET  /api/projects       every project, by key, with whether its root is a directory
                             now; only those of `kind` when it is given; this server's
                             home folder, and whether notes is installed
    GET  /api/project        one project, by `key` (matched ignoring case)
    GET  /api/folder         what is at `path` as typed (`~` taken as this server's
                             home): whether it is a directory and a git repository, the
                             project it is in and how, and the key its last segment
                             would make, and whether that key is taken
    GET  /api/kinds          the kinds a project may have, and every contribution or kind
                             that can't be used, with why
    GET  /api/where          the store file this server reads and writes, the git it
                             would run for a create (or none), and the line a write
                             would be refused with now (or none)
    GET  /api/remove-check   what a remove of `key` would be refused with now, each
                             holder with its sentence, without removing
    GET  /api/remove-status  what the remove request `job` came to: removed, or refused
                             with each sentence
    POST /api/add            `moxi projects add KEY [--root DIR] [--create] [--kind KIND]
                             [--lead NAME] [--notes-root DIR]`
    POST /api/set            `moxi projects set KEY [--root DIR] [--kind KIND]
                             [--lead NAME | --no-lead] [--notes-root DIR | --no-notes-root]`
    POST /api/agents-add     `moxi projects agents add KEY NAME`
    POST /api/agents-remove  `moxi projects agents remove KEY NAME`
    POST /api/remove         `moxi projects remove KEY`, keyed by the `job` the page
                             mints per press, answered at once with the outcome

**An act from the page does what the same verb does in the user's terminal, or refuses
before it makes anything.** Each handler calls the act the verb calls
(`moxi_projects.acts`), and each read the read API's function. Every act first asks ui's
check whether this server would write where the user's terminal does
(`moxi_projects.pagecheck`), and refuses while it would not or can't tell. The other
difference is the mint's bound: a create from the page gives `git init` 20 seconds, not the verb's 30, so
the answer, the failure message included, comes inside the shell's 30 second deadline for
it.

Every root is marked `path`, and every refusal sentence, note and broken reason, which can
name a path, `redact`. Only the gateway acts on a mark, and no route here is the
gateway's.

The page's assets are what the repository's web build writes from `packages/projects/web/`
into this package's `web/`: `page.js`, the page, and `picker.js`, `card.js` and `add.js`,
the components other pages may place.

The browser UI shell starts the page server on the first request for projects' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in projects' announcement, read
API or CLI does. projects' distribution doesn't require ui, which comes with the `ui` extra,
so this module reaches loopback, ui's page-server base, only through
`moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"projects' page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: Where a remove request's outcome is read.
STATUS_ROUTE = "/api/remove-status"
#: The longest key, kind or agent name a request takes.
NAME_MAX = 256
#: The longest path a request takes.
PATH_MAX = 4096
#: The longest `job` a remove or a status read takes.
JOB_MAX = 64

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
_PATH = lb.Out("string", mark="path")
_MAYBE_PATH = lb.Out("string", mark="path", nullable=True)
#: A sentence, which can name a path.
_SAYS = lb.Out("string", mark="redact")
_MAYBE_SAYS = lb.Out("string", mark="redact", nullable=True)
_NAMES = lb.Out("list", items=_TEXT)
_MAYBE_BOOL = lb.Out("bool", nullable=True)

_PROJECT_FIELDS = {
    "key": _TEXT,
    "root": _PATH,
    "kind": _TEXT,
    "lead": _MAYBE,
    "associated": _NAMES,
    "agents": _NAMES,
    "notes_root": _MAYBE_PATH,
    "created": _TEXT,
}
#: One project, as the read API's `Project` gives it: `agents` is the lead first, then
#: the associated set; `created` UTC, ISO 8601 with `Z`.
PROJECT = lb.Out("table", items=_PROJECT_FIELDS)
#: One project in the list: a `PROJECT` and `exists`, true when its root is a directory
#: now, false when nothing is there or it is not a directory, null when this server
#: can't say (the `stat` failed another way, or did not answer within the list's bound).
LISTED = lb.Out("table", items={**_PROJECT_FIELDS, "exists": _MAYBE_BOOL})

#: One kind: `module` is `projects` for a built-in one; `mint` is how a create makes its
#: root, `git`, `dir` or `refuse` (the page hides create for `refuse`).
KIND = lb.Out("table", items={"name": _TEXT, "doc": _TEXT, "module": _TEXT, "mint": _TEXT})
#: A contribution that won't load, or a kind that is blocked, and why.
BROKEN = lb.Out("table", items={"module": _TEXT, "reason": _SAYS})

#: The list: every project, `home`, this server's home folder (null when nothing places
#: one), under which a root is written `~/...`, and `notes_installed`, whether notes is
#: installed, without which no notes root is stored.
PROJECTS = {"projects": lb.Out("list", items=LISTED), "home": _MAYBE_PATH,
            "notes_installed": lb.Out("bool")}
ONE = {"project": PROJECT}
KINDS = {"found": lb.Out("list", items=KIND), "broken": lb.Out("list", items=BROKEN)}
#: What the Add panel says under its folder field, for the `path` typed. `path` is that
#: path with a leading `~` taken as this server's home, made normal; null, with every
#: other field, for a relative path, `~user`, or a `~` path while nothing places the
#: home. `exists`: whether it is a directory now; `git`: whether `<path>/.git` exists,
#: null when `exists` is false; each null when its `stat` did not answer within the
#: bound. `project`: the key of the project the path is in, or null; `how` the path
#: stands to it: `root` (its root), `inside` (under its root) or `worktree` (matched
#: through a linked git worktree's main tree). `key`: the path's last segment when it is a
#: valid key, else null; `taken`: whether a project already has that key, ignoring case.
#: `project`, `how` and `taken` are null when the store can't be read; `project` and
#: `how` are null too when the path's own `stat` did not answer.
AT_PATH = {"path": _MAYBE_PATH, "exists": _MAYBE_BOOL, "git": _MAYBE_BOOL,
          "project": _MAYBE, "how": _MAYBE, "key": _MAYBE, "taken": _MAYBE_BOOL}
#: What a remove of `key` would meet now, without removing: `key` as stored, and one row
#: per refusal. `module` is `projects` for the associated agents (its `sentence` names
#: them), else the contribution's module, with its sentence, which carries its own fix;
#: `fix` is set only for a contribution that won't load, raises or answers out of shape,
#: whose `sentence` is the reason. Empty when nothing would refuse it.
REMOVE_CHECK = {"key": _TEXT, "refusals": lb.Out("list", items=lb.Out("table", items={
    "module": _TEXT, "sentence": _SAYS, "fix": _MAYBE_SAYS}))}
#: `store` is the store file; `git` the git a create would run, null when there is none
#: on this server's PATH; `note` the line an add, a set or a remove would be refused with
#: now, since this server would write elsewhere than the user's terminal or can't tell,
#: or null.
WHERE = {"store": _PATH, "git": _MAYBE_PATH, "note": _MAYBE_SAYS}
#: An add or a set: the project as it now stands, and notes that refuse nothing (a root
#: that doesn't exist yet, what a create made).
WRITTEN = {"project": PROJECT, "notes": lb.Out("list", items=_SAYS)}
#: An add: a write, and `warnings`, what the add went ahead without (a contribution
#: skipped since it won't load, with the fix); empty when it went without nothing.
ADDED = {**WRITTEN, "warnings": lb.Out("list", items=_SAYS)}
#: A remove's outcome, as `/api/remove` and `/api/remove-status` answer it: `job` the
#: press it was kept under; `state` is `removed` or `refused`; `key` the project's key as
#: stored (as asked when there was none); `project` the key the request was kept under.
#: A refused one has its `reason`, `fix`, `sentences` (each contribution's, named by its
#: module, or the reason alone) and the `exit_code` the verb exits with; a removed one has
#: them null and empty.
STATUS = {
    "job": _TEXT,
    "project": _TEXT,
    "created": _TEXT,
    "state": _TEXT,
    "key": _TEXT,
    "reason": _MAYBE_SAYS,
    "fix": _MAYBE_SAYS,
    "sentences": lb.Out("list", items=_SAYS),
    "exit_code": lb.Out("int", nullable=True),
}

_KEY = lb.Field("string", max=NAME_MAX)
_NAME = lb.Field("string", max=NAME_MAX)
_MAYBE_NAME = lb.Field("string", required=False, max=NAME_MAX)
_MAYBE_DIR = lb.Field("string", required=False, max=PATH_MAX)
_FLAG = lb.Field("bool", required=False)

ADD_BODY = {"key": _KEY, "root": _MAYBE_DIR, "kind": _MAYBE_NAME, "lead": _MAYBE_NAME,
            "notes_root": _MAYBE_DIR, "create": _FLAG}
SET_BODY = {"key": _KEY, "root": _MAYBE_DIR, "kind": _MAYBE_NAME, "lead": _MAYBE_NAME,
            "no_lead": _FLAG, "notes_root": _MAYBE_DIR, "no_notes_root": _FLAG}
AGENT_BODY = {"key": _KEY, "name": _NAME}
#: A remove: the project's key, and `job`, which the page mints for each press of its
#: remove button (32 hexadecimal characters; the handler refuses any other).
REMOVE_BODY = {"key": _KEY, "job": lb.Field("string", max=JOB_MAX)}


def _target(name: str) -> str:
    return f"moxi_projects.pagehandlers:{name}"


#: What the page server may hold before ui's doctor warns. Measured in a test shell on
#: Linux (Python 3.14), with the page opened, reloaded five times, then an edit, an
#: agent added and removed and an add, then left idle for 30 s: RSS 41-45 MB with 2, 20
#: or 200 projects, and 8 open fds idle, 12 to 14 just after the page's reads. The budget
#: is about twice the most that was seen.
BUDGET = {"rss": "90M", "fds": 32}

PAGE = lb.Page(
    assets="web",
    budget=BUDGET,
    routes=(
        lb.Route("/api/projects", "read", _target("projects"),
                 query={"kind": _MAYBE_NAME}, returns=PROJECTS),
        lb.Route("/api/project", "read", _target("project"), query={"key": _KEY},
                 returns=ONE),
        lb.Route("/api/folder", "read", _target("at_path"),
                 query={"path": lb.Field("string", max=PATH_MAX)}, returns=AT_PATH),
        lb.Route("/api/kinds", "read", _target("kinds"), returns=KINDS),
        lb.Route("/api/where", "read", _target("where"), returns=WHERE),
        lb.Route("/api/remove-check", "read", _target("remove_check"), query={"key": _KEY},
                 returns=REMOVE_CHECK),
        lb.Route(STATUS_ROUTE, "read", _target("remove_status"),
                 query={"job": lb.Field("string", max=JOB_MAX)}, returns=STATUS),
        lb.Route("/api/add", "act", _target("add"), body=ADD_BODY, returns=ADDED),
        lb.Route("/api/set", "act", _target("set_fields"), body=SET_BODY, returns=WRITTEN),
        lb.Route("/api/agents-add", "act", _target("agents_add"), body=AGENT_BODY,
                 returns=ONE),
        lb.Route("/api/agents-remove", "act", _target("agents_remove"), body=AGENT_BODY,
                 returns=ONE),
        lb.Route("/api/remove", "act", _target("remove"), body=REMOVE_BODY,
                 returns=STATUS),
    ),
)

__all__ = ["ADD_BODY", "ADDED", "AGENT_BODY", "AT_PATH", "BROKEN", "BUDGET", "KIND", "KINDS",
           "LISTED", "ONE", "PAGE", "PROJECT", "PROJECTS", "REMOVE_BODY", "REMOVE_CHECK",
           "SET_BODY", "STATUS", "STATUS_ROUTE", "WHERE", "WRITTEN"]
