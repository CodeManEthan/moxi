"""The handler of repo's page.

`repos` answers from `api.status(with_rules=True)` (what `moxi repo git-hooks list --json`
prints, with each repo's rules read without the commit-proof line, so nothing is written)
and, for each repo, `api.specs(root, sweep=False)` (what `moxi repo specs --json` prints,
with nothing written) and the branches each pushing spec has not pushed
(`committer.unpushed`, as the doctor reads them). With projects installed it adds each
project's repo as `git-hooks list --projects` does (`projects.with_projects`): one with
pieces is a repo with `indexed` false, one without is in `unindexed`. It cuts each to the
route's declared keys and adds the command lines the page offers to copy, each one of `moxi
repo`'s own commands with the repo's path quoted as a person pastes it, the pieces
installed modules offer and the declarations refused (`api.available()`), and the server's
local date.

It never raises. A failure of repo's own (`api.Failure`, such as an index that can't be
read) is a 200 with `state` `error` and that failure's text, which is what the command
would print. Any other failure is `state` `error` with a fixed `detail` naming only the
exception's class; the class and its traceback are logged once. A repo whose specs alone
can't be listed, for any reason, keeps its git hooks, with `specs_problem` saying why (for
a failure that isn't repo's own, a fixed text naming only the exception's class, logged
once), and the other repos are listed as usual. The offered pieces, the project repos and
each repo's rules also fail alone: the pieces as empty lists, the project repos as an
empty `unindexed` with only the indexed repos, the rules as null, each failure that isn't
repo's own logged once.

`within` reads the folder a copy of Moxi keeps the page's plans and acts to, from
`MOXI_ANCHOR_HOME`.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from collections.abc import Mapping
from typing import Any

from . import api, committer, projects
from .run import quote_arg

log = logging.getLogger("moxi.repo.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


def _command(label: str, *words: str) -> dict[str, str]:
    return {"label": label, "command": "moxi repo " + " ".join(words)}


def _today() -> str:
    """The page server's local date, `YYYY-MM-DD`."""
    return dt.date.today().isoformat()


def _event(name: str, info: dict) -> dict[str, Any]:
    return {"event": name, "via": info.get("via"), "pieces": list(info.get("pieces") or []),
            "existing": info.get("existing"), "existing_hook": info.get("existing_hook"),
            "chain_line": info.get("chain_line"), "hook": info.get("hook"),
            "line": info.get("line")}


def _piece(root: str, piece: dict) -> dict[str, Any]:
    pid = piece["id"]
    flip = "disable" if piece["enabled"] else "enable"
    return {"id": pid, "event": piece.get("event"), "enabled": bool(piece["enabled"]),
            "fail": piece["fail"], "order": int(piece["order"]),
            "settings": [{"key": key, "values": list(values)}
                         for key, values in sorted(piece.get("settings", {}).items())],
            "declared": bool(piece.get("declared")), "recorded": bool(piece.get("recorded")),
            "toggle": _command(flip.capitalize(), "git-hooks", flip, quote_arg(root),
                               quote_arg(pid)),
            "remove": _command("Remove", "git-hooks", "remove", quote_arg(root),
                               quote_arg(pid))}


def _problem(problem: Any) -> dict[str, Any]:
    return {"state": getattr(problem, "state", "fail"),
            "detail": getattr(problem, "detail", str(problem)),
            "fix": getattr(problem, "fix", None), "code": getattr(problem, "code", None),
            "piece": getattr(problem, "piece", None), "event": getattr(problem, "event", None),
            "refresh": bool(getattr(problem, "refresh", False))}


def _spec(root: str, item: dict, head: Any) -> dict[str, Any]:
    """One spec of the repo at `root`; `head()` is the branch its work tree has checked
    out, asked for only by a spec that pushes."""
    last = item.get("last") or {}
    commit = item.get("last_commit") or {}
    name = item["name"]
    failed = item.get("push") or {}
    remote = (item.get("spec") or {}).get("push")
    return {
        "name": name, "saved": bool(item.get("saved")), "problem": item.get("problem"),
        "status": last.get("status"), "line": last.get("line"), "time": last.get("time"),
        "refused_since": item.get("refused_since"),
        "refused_passes": item.get("refused_passes"),
        "error_since": item.get("error_since"), "error_passes": item.get("error_passes"),
        "index_busy_since": item.get("index_busy_since"),
        "index_stale": bool(item.get("index_stale")),
        "last_commit": commit.get("sha"), "last_commit_time": commit.get("time"),
        "remote": remote,
        "pushed": [{"branch": branch, "sha": sha}
                   for branch, sha in sorted((item.get("pushed") or {}).items())],
        "push_failed": [{"branch": branch, "error": (info or {}).get("error"),
                         "failed_at": (info or {}).get("failed_at")}
                        for branch, info in sorted(failed.items())],
        "unpushed": ([{"branch": gap["branch"], "ahead": gap["ahead"]}
                      for gap in committer.unpushed(root, remote, failed, head=head())]
                     if remote else []),
        "run": _command("Run", "commit", quote_arg(root), "--spec", quote_arg(name)),
        "forget": _command("Forget", "specs", "remove", quote_arg(root), quote_arg(name)),
    }


def _record(record: dict) -> dict[str, Any]:
    return {key: record.get(key) for key in
            ("kind", "piece", "spec", "branch", "text", "fix", "at")}


def _repo(listed: dict) -> dict[str, Any]:
    root = listed["root"]
    specs: list[dict] = []
    specs_problem = None
    branch: list[str | None] = []

    def head() -> str | None:
        if not branch:
            branch.append(committer.head_branch(root))
        return branch[0]

    if listed.get("hooks_dir") is not None:
        try:
            for repo in api.specs(root, sweep=False):
                specs += [_spec(root, item, head) for item in repo["specs"]]
        except api.Failure as exc:
            specs_problem = str(exc)
        except Exception as unexpected_exc:
            _log_once(unexpected_exc, "listing a repo's specs failed")
            specs_problem = (f"an unexpected error ({type(unexpected_exc).__name__}); the page "
                             "server's log has the details")
    path = quote_arg(root)
    return {
        "root": root, "project": listed.get("project"),
        "indexed": bool(listed.get("indexed", True)), "git_version": listed.get("git_version"),
        "mode": listed.get("mode"), "pinned": listed.get("mode_set", "auto") != "auto",
        "events": [_event(name, info) for name, info in sorted(listed["events"].items())],
        "own_hooks": list(listed.get("own_hooks") or []),
        "pieces": [_piece(root, piece) for piece in listed["pieces"]],
        "problems": [_problem(problem) for problem in listed["problems"]],
        "specs": specs, "specs_problem": specs_problem,
        "records": [_record(record) for record in listed.get("records") or []],
        "rules": None if listed.get("rules") is None else list(listed["rules"]),
        "proof": bool(listed.get("proof")),
        "commands": [_command("List", "git-hooks", "list", path),
                     _command("Refresh", "git-hooks", "refresh", path),
                     _command("Specs", "specs", path)],
    }


def _offered() -> tuple[list[dict], list[dict]]:
    """The pieces installed modules declare and the declarations refused; both empty when
    they can't be read."""
    try:
        found = api.available()
        offered = [{"id": piece["id"], "module": piece["module"],
                    "name": piece["id"].partition("/")[2], "event": piece["event"],
                    "fail": piece["fail"], "order": int(piece["order"]),
                    "doc": piece["doc"], "settings": list(piece["settings"])}
                   for piece in found["pieces"]]
        refused = [{"id": pid, "why": why} for pid, why in found["refused"].items()]
    except Exception as unexpected_exc:
        _log_once(unexpected_exc, "listing the offered pieces failed")
        return [], []
    return offered, refused


def _listed(found: Any) -> tuple[list[dict], list[dict] | None]:
    """Each repo to show, as `api.status` lists it with its project key, and `unindexed`:
    null without projects; each project repo with no piece, else. Raises what `status`
    raises; a failure in adding the project repos leaves only the indexed repos, keyed,
    and `unindexed` empty."""
    listed = api.status(with_rules=True)
    if found is None:
        return projects.with_projects(listed, None)[0], None
    indexed = len(listed)
    try:
        listed, _ = projects.with_projects(listed, found, add=True, with_rules=True)
    except Exception as exc:
        if not isinstance(exc, api.Failure):
            _log_once(exc, "listing the project repos failed")
        return listed[:indexed], []
    added = listed[indexed:]
    unindexed = [{"project": repo["project"], "root": repo["root"]}
                 for repo in added if not repo["pieces"]]
    return listed[:indexed] + [repo for repo in added if repo["pieces"]], unindexed


def gather() -> dict[str, Any]:
    """The route's value without `now`: `state`, `detail`, the repos, `today`, `offered`,
    `refused` and `unindexed`. Never raises."""
    offered, refused = _offered()
    value = {"today": _today(), "offered": offered, "refused": refused, "unindexed": None}
    try:
        found = projects.api()
        value["unindexed"] = None if found is None else []
        listed, value["unindexed"] = _listed(found)
        repos = [_repo(repo) for repo in listed]
    except api.Failure as exc:
        return {**value, "state": "error", "detail": str(exc), "repos": []}
    except Exception as unexpected_exc:
        _log_once(unexpected_exc, "listing the repos failed")
        return {**value, "state": "error",
                "detail": f"repo's page server hit an unexpected error "
                          f"({type(unexpected_exc).__name__}); its log has the details",
                "repos": []}
    return {**value, "state": "up", "detail": None, "repos": repos}


def within(env: Mapping[str, str] | None = None) -> str | None:
    """The folder the page's plans and acts keep to, from `env` (None: this process's
    environment): when it sets `MOXI_ANCHOR_HOME` (a copy of Moxi, as the core's
    `anchor_set` reads it), that folder's real path, or the value as it is when it isn't a
    full path, so every plan and act refuses; else None, which keeps to nothing."""
    from moxi import paths, terminal

    source = os.environ if env is None else env
    if not terminal.anchor_set(source):
        return None
    value = next((v for k, v in source.items()
                  if v and (k == paths.ANCHOR_HOME_ENV or (
                      os.name == "nt" and k.upper() == paths.ANCHOR_HOME_ENV))), "")
    return os.path.realpath(value) if os.path.isabs(value) else value


def repos(call: Any) -> dict[str, Any]:
    """`GET /api/repos`: every indexed repo now, with the server's `now` and `today`. A
    failure is a 200 with `state` `error`, never a 500."""
    value = gather()
    return {"now": time.time(), **value}


__all__ = ["gather", "repos", "within"]
