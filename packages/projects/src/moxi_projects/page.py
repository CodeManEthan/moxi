"""projects' page: the routes its page server answers, declared as data.

    GET  /api/projects       every project, by key; only those of `kind` when it is given
    GET  /api/project        one project, by `key` (matched ignoring case)
    GET  /api/kinds          the kinds a project may have, and every contribution or kind
                             that can't be used, with why
    GET  /api/where          the store file this server reads and writes, and the git it
                             would run for a create (or none)
    GET  /api/remove-status  what the remove request `job` came to: removed, or refused
                             with each sentence
    POST /api/add            `moxi projects add KEY [--root DIR] [--create] [--kind KIND]
                             [--lead NAME] [--notes-root DIR]`
    POST /api/set            `moxi projects set KEY [--root DIR] [--kind KIND]
                             [--lead NAME | --no-lead] [--notes-root DIR | --no-notes-root]`
    POST /api/agents-add     `moxi projects agents add KEY NAME`
    POST /api/agents-remove  `moxi projects agents remove KEY NAME`
    POST /api/remove         `moxi projects remove KEY`, confirmed (202, with the status
                             link)

**An act from the page does what the same verb does in the user's terminal, or refuses
before it makes anything.** Each handler calls the act the verb calls
(`moxi_projects.acts`), and each read the read API's function. The one difference is the
mint's bound: a create from the page gives `git init` 20 seconds, not the verb's 30, so
the answer, the failure message included, comes inside the shell's 30 second deadline for
it.

Every root is marked `path`, and every refusal sentence, note and broken reason, which can
name a path, `redact`. Only the gateway acts on a mark, and no route here is the
gateway's.

The browser UI shell starts the page server on the first request for projects' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in projects' announcement, read
API or CLI does. projects' distribution doesn't require loopback, which comes with the
`ui` extra, so this module reaches it only through `moxi.modules.load` and raises
`ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"projects' page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

#: Where a remove request's outcome is read.
STATUS_ROUTE = "/api/remove-status"
#: The longest key, kind or agent name a request takes.
NAME_MAX = 256
#: The longest path a request takes.
PATH_MAX = 4096
#: An idempotency key's length, which a status read's `job` never passes.
JOB_MAX = 64

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
_PATH = lb.Out("string", mark="path")
_MAYBE_PATH = lb.Out("string", mark="path", nullable=True)
#: A sentence, which can name a path.
_SAYS = lb.Out("string", mark="redact")
_MAYBE_SAYS = lb.Out("string", mark="redact", nullable=True)
_NAMES = lb.Out("list", items=_TEXT)

#: One project, as the read API's `Project` gives it: `agents` is the lead first, then
#: the associated set; `created` UTC, ISO 8601 with `Z`.
PROJECT = lb.Out("table", items={
    "key": _TEXT,
    "root": _PATH,
    "kind": _TEXT,
    "lead": _MAYBE,
    "associated": _NAMES,
    "agents": _NAMES,
    "notes_root": _MAYBE_PATH,
    "created": _TEXT,
})

#: One kind: `module` is `projects` for a built-in one; `mint` is how a create makes its
#: root, `git`, `dir` or `refuse` (the page hides create for `refuse`).
KIND = lb.Out("table", items={"name": _TEXT, "doc": _TEXT, "module": _TEXT, "mint": _TEXT})
#: A contribution that won't load, or a kind that is blocked, and why.
BROKEN = lb.Out("table", items={"module": _TEXT, "reason": _SAYS})

PROJECTS = {"projects": lb.Out("list", items=PROJECT)}
ONE = {"project": PROJECT}
KINDS = {"found": lb.Out("list", items=KIND), "broken": lb.Out("list", items=BROKEN)}
#: `store` is the store file; `git` the git a create would run, null when there is none
#: on this server's PATH.
WHERE = {"store": _PATH, "git": _MAYBE_PATH}
#: An add or a set: the project as it now stands, and notes that refuse nothing (a root
#: that doesn't exist yet, what a create made).
WRITTEN = {"project": PROJECT, "notes": lb.Out("list", items=_SAYS)}
#: A remove request: `state` is `removed` or `refused`; `key` the project's key as
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


def _target(name: str) -> str:
    return f"moxi_projects.pagehandlers:{name}"


PAGE = lb.Page(
    routes=(
        lb.Route("/api/projects", "read", _target("projects"),
                 query={"kind": _MAYBE_NAME}, returns=PROJECTS),
        lb.Route("/api/project", "read", _target("project"), query={"key": _KEY},
                 returns=ONE),
        lb.Route("/api/kinds", "read", _target("kinds"), returns=KINDS),
        lb.Route("/api/where", "read", _target("where"), returns=WHERE),
        lb.Route(STATUS_ROUTE, "read", _target("remove_status"),
                 query={"job": lb.Field("string", max=JOB_MAX)}, returns=STATUS),
        lb.Route("/api/add", "act", _target("add"), body=ADD_BODY, returns=WRITTEN),
        lb.Route("/api/set", "act", _target("set_fields"), body=SET_BODY, returns=WRITTEN),
        lb.Route("/api/agents-add", "act", _target("agents_add"), body=AGENT_BODY,
                 returns=ONE),
        lb.Route("/api/agents-remove", "act", _target("agents_remove"), body=AGENT_BODY,
                 returns=ONE),
        lb.Route("/api/remove", "act", _target("remove"), body={"key": _KEY}, returns={},
                 confirmed=True, status=STATUS_ROUTE),
    ),
)

__all__ = ["ADD_BODY", "AGENT_BODY", "BROKEN", "KIND", "KINDS", "ONE", "PAGE", "PROJECT",
           "PROJECTS", "SET_BODY", "STATUS", "STATUS_ROUTE", "WHERE", "WRITTEN"]
