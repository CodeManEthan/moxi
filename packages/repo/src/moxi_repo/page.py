"""repo's page: the one route its page server answers, declared as data.

    GET /api/repos   each indexed repo's git hooks and committer specs, with the
                     server's `now`

It answers from `api.status()` and `api.specs(sweep=False)`, what `moxi repo git-hooks
list --json` and `moxi repo specs --json` print, so the page shows what those commands
show. The route is a read and writes nothing: the listing of specs runs without the
record sweep. It is not the gateway's. Every command the page offers is a line to copy
and run in a terminal; the page runs none.

Every value that can hold a path carries a mark: `root` and `hook` are `path`, and each
sentence or command line that can name a path or a remote is `redact`. Only the gateway
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
_COMMAND = lb.Out("table", items={"label": _TEXT, "command": _SAYS})

#: One event that has pieces: how git reaches them (`config entry`, `shim`, `launcher`,
#: or null when every piece of it is disabled), the existing git hook, and in launcher mode
#: whether the chain line is `present` or `needed`, the hook file it goes in, and the line.
EVENT = lb.Out("table", items={
    "event": _TEXT,
    "via": _MAYBE,
    "pieces": lb.Out("list", items=_TEXT),
    "existing": _MAYBE_SAYS,
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

#: One problem the listing names: `state` is `fail`, `warn` or `ok`, `fix` the command.
PROBLEM = lb.Out("table", items={"state": _TEXT, "detail": _SAYS, "fix": _MAYBE_SAYS})

#: One committer spec: its last pass (`status` null when none has run), its author's last
#: commit, each branch's last pushed commit and each branch whose last push failed.
SPEC = lb.Out("table", items={
    "name": _TEXT,
    "saved": lb.Out("bool"),
    "problem": _MAYBE_SAYS,
    "status": _MAYBE,
    "line": _MAYBE_SAYS,
    "time": _WHEN,
    "refused_since": _WHEN,
    "refused_passes": lb.Out("int", nullable=True),
    "last_commit": _MAYBE,
    "last_commit_time": _WHEN,
    "remote": _MAYBE_SAYS,
    "pushed": lb.Out("list", items=lb.Out("table", items={"branch": _TEXT, "sha": _TEXT})),
    "push_failed": lb.Out("list", items=lb.Out("table", items={
        "branch": _TEXT, "error": _MAYBE_SAYS, "failed_at": _WHEN})),
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

#: One indexed repo. `specs_problem` says why its specs could not be listed.
REPO = lb.Out("table", items={
    "root": _PATH,
    "project": _MAYBE,
    "git_version": _MAYBE,
    "mode": _MAYBE,
    "pinned": lb.Out("bool"),
    "events": lb.Out("list", items=EVENT),
    "pieces": lb.Out("list", items=PIECE),
    "problems": lb.Out("list", items=PROBLEM),
    "specs": lb.Out("list", items=SPEC),
    "specs_problem": _MAYBE_SAYS,
    "records": lb.Out("list", items=RECORD),
    "commands": lb.Out("list", items=_COMMAND),
})

#: The list and how it stands: `state` is `up` or `error`, and `detail` is null when up.
REPOS = {
    "now": lb.Out("float"),
    "state": _TEXT,
    "detail": _MAYBE_SAYS,
    "repos": lb.Out("list", items=REPO),
}

PAGE = lb.Page(
    routes=(
        lb.Route("/api/repos", "read", "moxi_repo.pagehandlers:repos", returns=REPOS),
    ),
    assets="web",
)

__all__ = ["EVENT", "PAGE", "PIECE", "PROBLEM", "RECORD", "REPO", "REPOS", "SPEC"]
