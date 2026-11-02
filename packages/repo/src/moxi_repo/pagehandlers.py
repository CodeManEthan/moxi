"""The handler of repo's page.

`repos` answers from `api.status()` (what `moxi repo git-hooks list --json` prints) and,
for each repo, `api.specs(root, sweep=False)` (what `moxi repo specs --json` prints, with
nothing written). It cuts each to the route's declared keys and adds the command lines the
page offers to copy, each one of `moxi repo`'s own commands with the repo's path quoted as
a person pastes it.

It never raises. A failure of repo's own (`api.Failure`, such as an index that can't be
read) is a 200 with `state` `error` and that failure's text, which is what the command
would print. Any other failure is `state` `error` with a fixed `detail` naming only the
exception's class; the class and its traceback are logged once. A repo whose specs alone
can't be listed, for any reason, keeps its git hooks, with `specs_problem` saying why (for
a failure that isn't repo's own, a fixed text naming only the exception's class, logged
once), and the other repos are listed as usual.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import api, projects
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


def _event(name: str, info: dict) -> dict[str, Any]:
    return {"event": name, "via": info.get("via"), "pieces": list(info.get("pieces") or []),
            "existing": info.get("existing"), "chain_line": info.get("chain_line"),
            "hook": info.get("hook"), "line": info.get("line")}


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
            "fix": getattr(problem, "fix", None)}


def _spec(root: str, item: dict) -> dict[str, Any]:
    last = item.get("last") or {}
    commit = item.get("last_commit") or {}
    name = item["name"]
    failed = item.get("push") or {}
    return {
        "name": name, "saved": bool(item.get("saved")), "problem": item.get("problem"),
        "status": last.get("status"), "line": last.get("line"), "time": last.get("time"),
        "refused_since": item.get("refused_since"),
        "refused_passes": item.get("refused_passes"),
        "last_commit": commit.get("sha"), "last_commit_time": commit.get("time"),
        "remote": (item.get("spec") or {}).get("push"),
        "pushed": [{"branch": branch, "sha": sha}
                   for branch, sha in sorted((item.get("pushed") or {}).items())],
        "push_failed": [{"branch": branch, "error": (info or {}).get("error"),
                         "failed_at": (info or {}).get("failed_at")}
                        for branch, info in sorted(failed.items())],
        "run": _command("Run", "commit", quote_arg(root), "--spec", quote_arg(name)),
        "forget": _command("Forget", "specs", "remove", quote_arg(root), quote_arg(name)),
    }


def _record(record: dict) -> dict[str, Any]:
    return {key: record.get(key) for key in
            ("kind", "piece", "spec", "branch", "text", "fix", "at")}


def _repo(listed: dict, found: Any) -> dict[str, Any]:
    root = listed["root"]
    project = None
    if found is not None:
        project, _ = projects.key_for(found, root)
    specs: list[dict] = []
    specs_problem = None
    if listed.get("hooks_dir") is not None:
        try:
            for repo in api.specs(root, sweep=False):
                specs += [_spec(root, item) for item in repo["specs"]]
        except api.Failure as exc:
            specs_problem = str(exc)
        except Exception as err:
            _log_once(err, "listing a repo's specs failed")
            specs_problem = (f"an unexpected error ({type(err).__name__}); the page "
                             "server's log has the details")
    path = quote_arg(root)
    return {
        "root": root, "project": project, "git_version": listed.get("git_version"),
        "mode": listed.get("mode"), "pinned": listed.get("mode_set", "auto") != "auto",
        "events": [_event(name, info) for name, info in sorted(listed["events"].items())],
        "pieces": [_piece(root, piece) for piece in listed["pieces"]],
        "problems": [_problem(problem) for problem in listed["problems"]],
        "specs": specs, "specs_problem": specs_problem,
        "records": [_record(record) for record in listed.get("records") or []],
        "commands": [_command("List", "git-hooks", "list", path),
                     _command("Refresh", "git-hooks", "refresh", path),
                     _command("Specs", "specs", path)],
    }


def gather() -> dict[str, Any]:
    """The route's value without `now`: `state`, `detail` and the repos. Never raises."""
    try:
        found = projects.api()
        repos = [_repo(listed, found) for listed in api.status()]
    except api.Failure as exc:
        return {"state": "error", "detail": str(exc), "repos": []}
    except Exception as err:
        _log_once(err, "listing the repos failed")
        return {"state": "error",
                "detail": f"repo's page server hit an unexpected error "
                          f"({type(err).__name__}); its log has the details",
                "repos": []}
    return {"state": "up", "detail": None, "repos": repos}


def repos(call: Any) -> dict[str, Any]:
    """`GET /api/repos`: every indexed repo now, with the server's `now`. A failure is a
    200 with `state` `error`, never a 500."""
    value = gather()
    return {"now": time.time(), **value}


__all__ = ["gather", "repos"]
