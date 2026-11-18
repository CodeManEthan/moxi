"""repo's page: the routes its page server answers, declared as data.

    GET  /api/repos     each indexed repo's git hooks and committer specs, the pieces
                        installed modules offer, the projects with no hooks yet, and the
                        server's `now` and `today`
    GET  /api/plan      what an add, a turn on or a refresh would write in one repo
    POST /api/add       add pieces to a repo, or the repo alone to the list
    POST /api/enable    turn a registered piece on
    POST /api/refresh   refresh one repo

`/api/repos` answers from `api.status()` and `api.specs(sweep=False)`, what `moxi repo
git-hooks list --json` and `moxi repo specs --json` print, so the page shows what those
commands show, and from `api.available()`. With projects installed it lists project repos
as `git-hooks list --projects` does. It is a read and writes nothing: the listing of specs
runs without the record sweep, and a repo's rules are read without the commit-proof line,
whose `git write-tree` writes. `/api/plan` reads too (`api.plan_add`, `plan_enable`,
`plan_refresh`).

The three acts are plain acts: each sends the digest of the plan its confirm drew, and the
verb writes only when the plan it makes under the repo's lock has that digest, so nothing
is written that the confirm didn't show and a repeated send writes nothing. In a copy of
Moxi (`MOXI_ANCHOR_HOME` set) a plan or act on a repo outside the copy's folder is
refused; an act is then checked with ui, as every page that writes is, so it writes no
store the user's terminal wouldn't find. None of them disables, removes or changes a
setting: those are lines to copy and run in a terminal. No route is the gateway's.

Every value that can hold a path carries a mark: `root`, `hook` and `existing_hook` are
`path`, and each sentence, rule or command line that can name a path or a remote is
`redact`. Only the gateway
acts on a mark, so today the page gets each value as it is; if the route is ever opened to
the gateway, those values are cut or swapped for a handle there. The chain line names no
path, so it carries none.

The browser UI shell starts the page server on the first request for repo's page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in repo's announcement, API,
CLI or git hook runner does. repo's distribution doesn't require ui, which comes with the
`ui` extra, so this module reaches loopback, ui's page-server base, only through
`moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"repo's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

_TEXT = lb.Out("string")
_MAYBE = lb.Out("string", nullable=True)
_PATH = lb.Out("string", mark="path")
#: A sentence or command line that can name a path or a remote.
_SAYS = lb.Out("string", mark="redact")
_MAYBE_SAYS = lb.Out("string", mark="redact", nullable=True)
_WHEN = lb.Out("float", nullable=True)
_COUNT = lb.Out("int", nullable=True)
_FLAG = lb.Out("bool")
_COMMAND = lb.Out("table", items={"label": _TEXT, "command": _SAYS})

#: One event that has pieces: how git reaches them (`config entry`, `shim`, `launcher`,
#: or null when every piece of it is disabled), the existing git hook (`existing`, a
#: sentence; `existing_hook`, the path of the user's own git hook that runs after the
#: pieces, or alone while each is disabled), and in launcher mode whether the chain line
#: is `present` or `needed`, the hook file it goes in, and the line.
EVENT = lb.Out("table", items={
    "event": _TEXT,
    "via": _MAYBE,
    "pieces": lb.Out("list", items=_TEXT),
    "existing": _MAYBE_SAYS,
    "existing_hook": lb.Out("string", mark="path", nullable=True),
    "chain_line": _MAYBE,
    "hook": lb.Out("string", mark="path", nullable=True),
    "line": _MAYBE,
})

#: One registered piece. `toggle` disables an enabled piece and enables a disabled one.
PIECE = lb.Out("table", items={
    "id": _TEXT,
    "event": _MAYBE,
    "enabled": lb.Out("bool"),
    "fail": _TEXT,
    "order": lb.Out("int"),
    "settings": lb.Out("list", items=lb.Out("table", items={
        "key": _TEXT, "values": lb.Out("list", items=_SAYS)})),
    "declared": lb.Out("bool"),
    "recorded": lb.Out("bool"),
    "toggle": _COMMAND,
    "remove": _COMMAND,
})

#: One problem the listing names: `state` is `fail`, `warn` or `ok`, `fix` the command;
#: `code` its kind, `piece` and `event` its subject, and `refresh` true when its fix is a
#: refresh of this repo.
PROBLEM = lb.Out("table", items={
    "state": _TEXT,
    "detail": _SAYS,
    "fix": _MAYBE_SAYS,
    "code": _MAYBE,
    "piece": _MAYBE,
    "event": _MAYBE,
    "refresh": _FLAG,
})

#: One committer spec: its last pass (`status` null when none has run), the runs of
#: refused, error and index-busy passes it is in (each null when it isn't), its author's
#: last commit, each branch's last pushed commit, each branch whose last push failed, and
#: each branch with commits its remote lacks (`ahead` null when never pushed).
SPEC = lb.Out("table", items={
    "name": _TEXT,
    "saved": lb.Out("bool"),
    "problem": _MAYBE_SAYS,
    "status": _MAYBE,
    "line": _MAYBE_SAYS,
    "time": _WHEN,
    "refused_since": _WHEN,
    "refused_passes": _COUNT,
    "error_since": _WHEN,
    "error_passes": _COUNT,
    "index_busy_since": _WHEN,
    "index_stale": _FLAG,
    "last_commit": _MAYBE,
    "last_commit_time": _WHEN,
    "remote": _MAYBE_SAYS,
    "pushed": lb.Out("list", items=lb.Out("table", items={"branch": _TEXT, "sha": _TEXT})),
    "push_failed": lb.Out("list", items=lb.Out("table", items={
        "branch": _TEXT, "error": _MAYBE_SAYS, "failed_at": _WHEN})),
    "unpushed": lb.Out("list", items=lb.Out("table", items={
        "branch": _TEXT, "ahead": _COUNT})),
    "run": _COMMAND,
    "forget": _COMMAND,
})

#: One doctor record repo has open for the repo: what it is about, and what it says.
RECORD = lb.Out("table", items={
    "kind": _TEXT,
    "piece": _MAYBE,
    "spec": _MAYBE,
    "branch": _MAYBE,
    "text": _SAYS,
    "fix": _MAYBE_SAYS,
    "at": _MAYBE,
})

#: One repo, indexed or (a project's, with pieces) not. `own_hooks` names the events with
#: no piece that have a git hook of the user's own; `rules` the lines `moxi repo rules`
#: prints under its heading, less the commit-proof line (null when they can't be built),
#: and `proof` whether the commit-proof piece is registered. `specs_problem` says why its
#: specs could not be listed.
REPO = lb.Out("table", items={
    "root": _PATH,
    "project": _MAYBE,
    "indexed": _FLAG,
    "git_version": _MAYBE,
    "mode": _MAYBE,
    "pinned": lb.Out("bool"),
    "events": lb.Out("list", items=EVENT),
    "own_hooks": lb.Out("list", items=_TEXT),
    "pieces": lb.Out("list", items=PIECE),
    "problems": lb.Out("list", items=PROBLEM),
    "specs": lb.Out("list", items=SPEC),
    "specs_problem": _MAYBE_SAYS,
    "records": lb.Out("list", items=RECORD),
    "rules": lb.Out("list", items=_SAYS, nullable=True),
    "proof": _FLAG,
    "commands": lb.Out("list", items=_COMMAND),
})

#: One piece an installed module declares: `name` the part of its id after `/`, and
#: `settings` the names of its settings.
OFFERED = lb.Out("table", items={
    "id": _TEXT,
    "module": _TEXT,
    "name": _TEXT,
    "event": _TEXT,
    "fail": _TEXT,
    "order": lb.Out("int"),
    "doc": _SAYS,
    "settings": lb.Out("list", items=_TEXT),
})

#: One declaration that was refused, and why.
REFUSED = lb.Out("table", items={"id": _TEXT, "why": _SAYS})

#: A project whose root is a git repo with no piece of repo's, not indexed.
UNINDEXED = lb.Out("table", items={"project": _TEXT, "root": _PATH})

#: The list and how it stands: `state` is `up` or `error`, and `detail` is null when up;
#: the server's local date (`today`); the pieces installed modules offer and the
#: declarations refused; and `unindexed`, null without projects.
REPOS = {
    "now": lb.Out("float"),
    "today": _TEXT,
    "state": _TEXT,
    "detail": _MAYBE_SAYS,
    "repos": lb.Out("list", items=REPO),
    "offered": lb.Out("list", items=OFFERED),
    "refused": lb.Out("list", items=REFUSED),
    "unindexed": lb.Out("list", items=UNINDEXED, nullable=True),
}

#: One item an act writes: `place` is `config`, `hooks`, `chain` or `index`; a `config`
#: item's `key` with its value before and after (`to` null for a seeded value drawn as
#: it is written, `seed` true), a `hooks` item's `key` (`shim`, `hook`, `launcher`) and
#: the file it writes or moves, a `chain` item's hook file and line, an `index` item's
#: root there before.
WRITE = lb.Out("table", items={
    "place": _TEXT,
    "piece": _MAYBE,
    "event": _MAYBE,
    "key": _MAYBE,
    "from": _MAYBE_SAYS,
    "to": _MAYBE_SAYS,
    "seed": _FLAG,
})

#: What an act would write in one repo (`api.plan_add`'s shape): `refusal` `{code, text}`
#: when it would be refused, with no `writes` and no `digest`.
PLAN = lb.Out("table", items={
    "root": _PATH,
    "name": _TEXT,
    "is_repo": _FLAG,
    "git_version": _MAYBE,
    "mode": _MAYBE,
    "indexed": _FLAG,
    "pieces": lb.Out("list", items=lb.Out("table", items={
        "id": _TEXT, "event": _MAYBE, "new": _FLAG, "implied_by": _MAYBE,
        "seeds": lb.Out("list", items=_TEXT)})),
    "mode_change": lb.Out("table", items={"from": _TEXT, "to": _TEXT}, nullable=True),
    "own_hooks": lb.Out("list", items=_TEXT),
    "in_progress": _MAYBE,
    "writes": lb.Out("list", items=WRITE),
    "refusal": lb.Out("table", items={"code": _MAYBE, "text": _SAYS}, nullable=True),
    "digest": _MAYBE,
}, nullable=True)

#: `/api/plan`: `state` `up` with the plan, or `error` with `detail` and no plan.
PLANNED = {"state": _TEXT, "detail": _MAYBE_SAYS, "plan": PLAN}

#: An act's answer: `outcome` is `done`, `nothing`, `changed` (with the new `plan`),
#: `refused` (`refusal` its code and text), `partial` (`refusal` the text of a failure
#: after the first write) or `failed` (`refusal` a fixed text); `lines`, `problems` and
#: `chain` are the verb's report.
ANSWER = {
    "outcome": _TEXT,
    "lines": lb.Out("list", items=_SAYS),
    "problems": lb.Out("list", items=_SAYS),
    "chain": lb.Out("list", items=lb.Out("table", items={
        "event": _TEXT, "hook": _PATH, "line": _TEXT, "present": _FLAG})),
    "plan": PLAN,
    "refusal": lb.Out("table", items={"code": _MAYBE, "text": _SAYS}, nullable=True),
}

#: The longest path and piece id a request may carry, and the most pieces in one add.
PATH_MAX = 4096
ID_MAX = 256
PIECES_MAX = 64
_ROOT = lb.Field("string", max=PATH_MAX)
_ID = lb.Field("string", max=ID_MAX)
_DIGEST = lb.Field("string", max=64)

PAGE = lb.Page(
    routes=(
        lb.Route("/api/repos", "read", "moxi_repo.pagehandlers:repos", returns=REPOS),
        lb.Route("/api/plan", "read", "moxi_repo.pagehandlers:plan",
                 query={"act": lb.Field("enum", values=("add", "enable", "refresh")),
                        "root": _ROOT,
                        "pieces": lb.Field("string", required=False,
                                           max=PIECES_MAX * (ID_MAX + 1))},
                 returns=PLANNED),
        lb.Route("/api/add", "act", "moxi_repo.pagehandlers:add",
                 body={"root": _ROOT, "digest": _DIGEST,
                       "pieces": lb.Field("list", required=False, max=PIECES_MAX,
                                          items=_ID)},
                 returns=ANSWER),
        lb.Route("/api/enable", "act", "moxi_repo.pagehandlers:enable",
                 body={"root": _ROOT, "piece": _ID, "digest": _DIGEST}, returns=ANSWER),
        lb.Route("/api/refresh", "act", "moxi_repo.pagehandlers:refresh",
                 body={"root": _ROOT, "digest": _DIGEST}, returns=ANSWER),
    ),
    assets="web",
)

__all__ = ["ANSWER", "EVENT", "OFFERED", "PAGE", "PIECE", "PLAN", "PLANNED", "PROBLEM",
           "RECORD", "REFUSED", "REPO", "REPOS", "SPEC", "UNINDEXED", "WRITE"]
