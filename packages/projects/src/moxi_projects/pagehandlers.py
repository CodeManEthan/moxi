"""The handlers of projects' page (`moxi_projects.page` declares the routes).

Each is a plain function, which loopback's base runs off the event loop on its threads,
so every store call, contribution and `git init` here runs off the loop. None calls
`moxi.aio.offload` itself: a worker thread has no loop to offload from.

**Reads** call the read API's functions (`list_projects`, `get`, `kinds`) and the remove
status read, and never write. The list adds whether each root is a directory now, asked
through `pagestat`, which never lets a read wait on a hung mount past its bound, the home
folder this server places and whether notes is installed. `/api/folder` says what is at
a typed path for the Add panel: whether it is a directory and a git repository (through
`pagestat` too), the project it is in by `for_path` and how, and the key it would make. `/api/remove-check` gathers what a remove would be refused with now, as the remove
asks it, without removing. `/api/where` names the store file this server reads and the
git a create would run, since the page server takes the browser shell's environment and
not the terminal's, and the two can differ. Its `note` is the line a write would be
refused with now (below), or null.

**The terminal comparison.** Before anything else, the key lookup included, every write
route (add, with or without create, set, agents add, agents remove and remove) asks
`pagecheck.line()`: ui's check, over the store file, the home folder and the folders of
every contribution the route consults, placed once from the core's record of the user's
last run in a terminal and once from this server's environment. A difference, no record,
or no ui check to ask is a 422 at once, whose text says what differs and the fix; nothing
is made or written, a remove keeps no row for it, and a repeat is answered the same. An
add, a set and a remove load the contributions once and hand the act the same ones they
compared. This server never records.

**Acts** call the acts the verbs call, with the body's values as the verb's flags:
`acts.add`, `acts.set_fields`, `acts.add_agent`, `acts.remove_agent` and `acts.remove`.
Each refuses before it makes anything, as the verb does. Paths must be absolute: a
relative one is refused, because this server's working directory is not the user's
terminal's. A leading `~` or `~/` (on Windows `~\\` too) in a root or a notes root is
expanded, only once the terminal comparison has passed and only with the home folder
`moxi.paths.home()` places, the resolver the comparison itself placed the home with; the
comparison refuses while that home differs from the terminal's, so the expansion is the
terminal's own. `~user`, and a `~` path while nothing places the home, are left as sent
and refused as not absolute. Besides the terminal
comparison, the one change from the verb is a create's bound: `git init` gets
`GIT_TIMEOUT`, 20 seconds, so a mint that hangs answers, with its message naming what is
left and how to finish, inside the shell's 30 second deadline for an act.

**A refusal** is answered as the verb's exit code says: exit 1, 2 and 3 are a 422 whose
text is the reason and the fix, and a store locked past the wait (exit 75) is a 409, try
again. A create refused for want of git also names the lasting fix, since the shell's
PATH is the service manager's.

**Remove** is a plain act: the page's own step in place is the confirmation, and the
body carries `job`, 32 hexadecimal characters the page mints for each press. It answers
the outcome at once. What a remove comes to is kept in the store under its `job` for a
day (`acts.record_status`): removed, or refused for a reason about the project
(associated agents, a contribution's sentences, a contribution that won't load or raises,
the key not in the store). The store keeps the first outcome under a `job` and ignores a
second, and the handler always answers the kept row, so a repeat of a `job`, presses sent
at once and a restart all answer the first outcome; a `job` kept for another project is a
422. A refusal that is the store's own (newer than this code, unreadable, locked, or no
store file yet) can't be kept there, so it is answered at once by the map above, and
nothing is written. `/api/remove-status?job=` reads a kept outcome again, for a page
whose answer was lost.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Any

import moxi.paths

from moxi_projects import acts, api, contributions, pagecheck, pagestat, rules, store
from moxi_projects.api import DEFAULT_KIND, Busy, Project, ProjectsError
from moxi_projects.errors import NoGit
from moxi_projects.page import lb

#: How long a create from the page lets `git init` run, in seconds. With the mkdir, the
#: kill and reap after a timeout and the row's write, the answer still comes inside the
#: shell's 30 second deadline for an act. The verb keeps `acts.GIT_TIMEOUT`.
GIT_TIMEOUT = 20

#: What a remove under a `job` already kept for another project is told.
KEY_REUSED = "this job was used for another request"
#: A remove's `job`: hexadecimal characters, this many, which the page mints per press.
JOB_CHARS = 32
_JOB = re.compile(f"[0-9a-f]{{{JOB_CHARS}}}")


def _answer(exc: ProjectsError) -> Exception:
    """The page's answer to a refusal: 409 for a store locked past the wait (exit 75),
    else 422, with the reason and the fix."""
    text = f"{exc.reason}; fix: {exc.fix}" if exc.fix else exc.reason
    if isinstance(exc, Busy):
        return lb.Conflict(text)
    return lb.Refused(text)


def _compare(found: contributions.Contributions | None = None) -> None:
    """Refuse the write, 422, while ui's check finds this server would write elsewhere
    than the user's terminal, or can't tell. `found` is the contributions the route
    consults (none for an agent's add or remove)."""
    said = pagecheck.line(() if found is None else (c.module for c in found.loaded))
    if said is not None:
        raise lb.Refused(said)


def _project(project: Project) -> dict[str, Any]:
    return project.as_dict()


# -- reads ----------------------------------------------------------------------------------

#: A root's `exists`, from what `pagestat.look` found there.
_EXISTS = {pagestat.DIR: True, pagestat.OTHER: False, pagestat.MISSING: False, None: None}


def _home() -> str | None:
    """This server's home folder as `moxi.paths.home()` places it, or None when nothing
    places one."""
    try:
        return str(moxi.paths.home())
    except LookupError:
        return None


def projects(call: Any) -> dict[str, Any]:
    """`GET /api/projects[?kind=K]`: every project, by key, as `moxi projects list`, each
    with `exists`; `home` and `notes_installed`. Each root's `stat` goes through
    `pagestat.look`, so the read never waits on the filesystem past its bound."""
    try:
        found = api.list_projects(call.query.get("kind"))
    except ProjectsError as exc:
        raise _answer(exc) from None
    seen = pagestat.look(p.root for p in found)
    return {"projects": [{**_project(p), "exists": _EXISTS[seen[p.root]]} for p in found],
            "home": _home(), "notes_installed": contributions.notes_present()}


def project(call: Any) -> dict[str, Any]:
    """`GET /api/project?key=K`: one project, as `moxi projects show K`; 422 for no such
    key."""
    try:
        return {"project": _project(api.get(call.query["key"]))}
    except ProjectsError as exc:
        raise _answer(exc) from None


#: The separators that may follow a leading `~`: `/` everywhere, and a backslash too on Windows.
_TILDE_SEPS = ("/", "\\") if os.name == "nt" else ("/",)


def expand_tilde(path: str, home: str | None) -> str | None:
    """`path` with a leading `~` or `~<separator>` taken as `home`; `path` itself when it
    has no leading `~`; None for `~user`, or for a `~` path while `home` is None."""
    if not path.startswith("~"):
        return path
    rest = path[1:]
    if rest and not rest.startswith(_TILDE_SEPS):
        return None
    if home is None:
        return None
    return os.path.join(home, rest.lstrip("".join(_TILDE_SEPS))) if rest else home


def _same(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)


def _under(path: str, root: str) -> bool:
    base = os.path.normcase(root)
    return os.path.normcase(path).startswith(base.rstrip(os.sep) + os.sep)


def _how(path: str, root: str) -> str:
    """How `path` stands to the project `for_path` found it in, whose root is `root`:
    `root`, `inside`, else `worktree`. Each is compared as written and as resolved, since
    `for_path` matches a symlinked spelling too."""
    root = os.path.normpath(root)
    spellings = [(path, root), (os.path.realpath(path), os.path.realpath(root))]
    if any(_same(a, b) for a, b in spellings):
        return "root"
    if any(_under(a, b) for a, b in spellings):
        return "inside"
    return "worktree"


_AT_PATH_NONE = dict.fromkeys(("path", "exists", "has_git", "project", "how", "key",
                               "taken"))
#: `<path>/.git` exists, as a directory or a worktree's file.
_GIT = {pagestat.DIR: True, pagestat.OTHER: True, pagestat.MISSING: False, None: None}


def at_path(call: Any) -> dict[str, Any]:
    """`GET /api/folder?path=P`: what the Add panel says under its folder field (the
    route's shape says each field). A read: no write, no `mkdir`, no git process. The two
    `stat`s go through `pagestat`, under the list's bound. Finding the project the path is
    in and how touches the path again, unbounded, so it runs only once the path's own
    `stat` has answered: one that hangs (a mount that doesn't answer) leaves `project` and
    `how` null rather than hold this server's thread."""
    typed = expand_tilde(call.query["path"], _home())
    if typed is None or "\0" in typed or not os.path.isabs(typed):
        return dict(_AT_PATH_NONE)
    path = os.path.normpath(typed)
    git_path = os.path.join(path, ".git")
    seen = pagestat.look([path, git_path])
    exists = _EXISTS[seen[path]]
    try:
        key = rules.check_key(os.path.basename(path))
    except ProjectsError:
        key = None
    try:
        found = api.for_path(path) if seen[path] is not None else None
        taken = (None if key is None else
                 any(p.key.casefold() == key.casefold() for p in api.list_projects()))
    except ProjectsError:
        found = taken = None
    return {"path": path, "exists": exists,
            "has_git": None if exists is False else _GIT[seen[git_path]],
            "project": found.key if found else None,
            "how": _how(path, found.root) if found else None,
            "key": key, "taken": taken}


def kinds(call: Any) -> dict[str, Any]:
    """`GET /api/kinds`: the kinds and what can't be used, as `moxi projects kinds`."""
    found = api.kinds()
    return {"found": [{"name": k.name, "doc": k.doc, "module": k.module, "mint": k.mint}
                      for k in found.found],
            "broken": [{"module": module, "reason": reason}
                       for module, reason in found.broken]}


def where(call: Any) -> dict[str, Any]:
    """`GET /api/where`: the store file this server acts on, the git a create would run
    here, or null, and `note`, the line an add, a set or a remove would be refused with
    now, or null."""
    found = contributions.load()
    return {"store": store.path(), "git": shutil.which("git"),
            "note": pagecheck.line(c.module for c in found.loaded)}


def remove_check(call: Any) -> dict[str, Any]:
    """`GET /api/remove-check?key=K`: what a remove of K would be refused with now, by the
    tests `acts.remove` makes, every one gathered rather than the first: the associated
    agents, then each contribution's refusal, or its fault. 422 for no such key. Each
    contribution reads locally only, by its contract, so this writes nothing."""
    try:
        found = api.get(call.query["key"])
    except ProjectsError as exc:
        raise _answer(exc) from None
    refusals = []
    if found.associated:
        refusals.append({"module": "projects", "fix": None,
                         "sentence": store.refuse_held_by_agents(found).reason})
    refusals += [{"module": module, "sentence": sentence, "fix": fix}
                 for module, sentence, fix in contributions.load().remove_check(found)]
    return {"key": found.key, "refusals": refusals}


def remove_status(call: Any) -> dict[str, Any]:
    """`GET /api/remove-status?job=<job>`: what the remove pressed under `job` came to,
    for a page whose answer to the remove was lost; 422 for a `job` no remove is kept
    under (an outcome is kept for a day)."""
    try:
        kept = acts.remove_status(call.query["job"])
    except ProjectsError as exc:
        raise _answer(exc) from None
    if kept is None:
        raise lb.Refused("no remove request is kept under this job; an outcome is kept "
                         "for a day")
    return _status(kept)


# -- acts -----------------------------------------------------------------------------------

def _no_git_fix(body: dict) -> str:
    """The lasting fix for a create refused for want of git on this server's PATH: the
    terminal command for the same add, with every flag the body sent, each value quoted
    for the shell."""
    words = ["moxi projects add", acts._quote(body["key"]), "--root",
             acts._quote(body["root"]) if body.get("root") is not None else "DIR"]
    if body.get("kind") not in (None, DEFAULT_KIND):
        words += ["--kind", acts._quote(body["kind"])]
    if body.get("lead") is not None:
        words += ["--lead", acts._quote(body["lead"])]
    if body.get("notes_root") is not None:
        words += ["--notes-root", acts._quote(body["notes_root"])]
    return ("this page's server looks git up on the PATH the browser shell was started "
            "with, not your terminal's: set PATH in the service manager's environment and "
            "restart the shell with `moxi ui stop`, or run "
            f"`{' '.join(words)} --create` in a terminal")


#: The fields of an add or a set whose leading `~` is expanded.
_PATH_FIELDS = ("root", "notes_root")


def _expand_homes(body: dict) -> dict:
    """`body` with a leading `~` in its root and notes root expanded by `expand_tilde`,
    with the home `moxi.paths.home()` places. Call it only after `_compare` passed. A
    value it can't expand (`~user`, or no home) is left as sent, for the act to refuse."""
    out = dict(body)
    for name in _PATH_FIELDS:
        value = body.get(name)
        if not isinstance(value, str) or not value.startswith("~"):
            continue
        expanded = expand_tilde(value, _home())
        if expanded is not None:
            out[name] = expanded
    return out


def add(call: Any) -> dict[str, Any]:
    """`POST /api/add`: `moxi projects add`, with `create` its `--create`, under the
    page's `git init` bound. `warnings` holds what the add went ahead without: a
    contribution skipped since it won't load, and the fix."""
    found = contributions.load()
    _compare(found)
    body = _expand_homes(call.body)
    try:
        added = acts.add(body["key"], root=body.get("root"),
                         kind=body.get("kind", DEFAULT_KIND), lead=body.get("lead"),
                         notes_root=body.get("notes_root"),
                         create=bool(body.get("create", False)), contributions=found,
                         git_timeout=GIT_TIMEOUT)
    except NoGit as exc:
        raise lb.Refused(f"{exc.reason}; fix: {exc.fix}; or, for good, "
                         f"{_no_git_fix(body)}") from None
    except ProjectsError as exc:
        raise _answer(exc) from None
    return {"project": _project(added.project), "notes": list(added.notes),
            "warnings": list(added.warnings)}


def set_fields(call: Any) -> dict[str, Any]:
    """`POST /api/set`: `moxi projects set`; `no_lead` and `no_notes_root` are its
    `--no-lead` and `--no-notes-root`."""
    found = contributions.load()
    _compare(found)
    body = _expand_homes(call.body)
    changes: dict[str, str | None] = {}
    for name in ("root", "kind"):
        if body.get(name) is not None:
            changes[name] = body[name]
    for name in ("lead", "notes_root"):
        clear = bool(body.get(f"no_{name}", False))
        if body.get(name) is not None and clear:
            raise lb.Refused(f"name a {name.replace('_', ' ')} or clear it, not both; "
                             f"fix: send {name} or no_{name}")
        if body.get(name) is not None:
            changes[name] = body[name]
        elif clear:
            changes[name] = None
    try:
        project = acts.set_fields(body["key"], contributions=found, **changes)
    except ProjectsError as exc:
        raise _answer(exc) from None
    note = acts.root_note(project.root) if "root" in changes else None
    return {"project": _project(project), "notes": [note] if note else []}


def agents_add(call: Any) -> dict[str, Any]:
    """`POST /api/agents-add`: `moxi projects agents add KEY NAME`."""
    _compare()
    try:
        return {"project": _project(acts.add_agent(call.body["key"], call.body["name"]))}
    except ProjectsError as exc:
        raise _answer(exc) from None


def agents_remove(call: Any) -> dict[str, Any]:
    """`POST /api/agents-remove`: `moxi projects agents remove KEY NAME`."""
    _compare()
    try:
        return {"project": _project(acts.remove_agent(call.body["key"], call.body["name"]))}
    except ProjectsError as exc:
        raise _answer(exc) from None


def _status(kept: dict) -> dict[str, Any]:
    """A kept remove outcome as the `STATUS` shape gives it."""
    return {"job": kept["job"], "project": kept["project"], "created": kept["created"],
            "state": kept["state"], "key": kept["key"], "reason": kept.get("reason"),
            "fix": kept.get("fix"), "sentences": list(kept.get("sentences") or []),
            "exit_code": kept.get("exit_code")}


def _kept(job: str, key: str) -> dict[str, Any] | None:
    """The outcome kept under `job`, answered as `STATUS`; None when none is kept. 422
    when it was kept for another project (compared ignoring case)."""
    try:
        kept = acts.remove_status(job)
    except ProjectsError as exc:
        raise _answer(exc) from None
    if kept is None:
        return None
    if kept["project"].casefold() != key.casefold():
        raise lb.Refused(KEY_REUSED)
    return _status(kept)


def remove(call: Any) -> dict[str, Any]:
    """`POST /api/remove`: `moxi projects remove KEY`, keyed by `job`, which the page
    mints for each press of its remove button; answered at once, as `STATUS`.

    A plain act: the page's own step is the confirmation. What the remove came to is kept
    under `job` (`acts.record_status`), and the store keeps only the first outcome under
    a `job`. So the handler answers the row kept under `job`, never its own result: a
    repeat of a `job` finds that row and acts no more; two presses sent at once under one
    `job` both reach `acts.remove`, the second meets the first's delete and records
    nothing over its row, and both answer the first outcome. A `job` kept for another
    project is a 422. A refusal the store keeps is a 200 whose `state` is `refused`; one
    that is the store's own (newer, unreadable, locked, no store yet) is answered as the
    map above says, and keeps nothing.

    The terminal comparison comes first, before the kept row is looked up: while it
    refuses, a repeat is refused too, though `/api/remove-status` still reads the kept
    outcome."""
    found = contributions.load()
    _compare(found)
    key, job = call.body["key"], call.body["job"]
    if not _JOB.fullmatch(job):
        raise lb.Refused(f"a remove's job is {JOB_CHARS} hexadecimal characters; fix: mint "
                         "one per press")
    kept = _kept(job, key)
    if kept is not None:
        return kept
    try:
        acts.remove(key, contributions=found, record=acts.record_status(job))
    except ProjectsError as exc:
        # A refusal the store kept has its row; any other is answered here.
        kept = _kept(job, key)
        if kept is None:
            raise _answer(exc) from None
        return kept
    kept = _kept(job, key)
    if kept is None:
        raise lb.Refused("the remove was made, but its outcome can't be read back; fix: "
                         "read the list again")
    return kept


__all__ = ["GIT_TIMEOUT", "JOB_CHARS", "KEY_REUSED", "expand_tilde", "projects", "project", "at_path",
           "kinds", "where", "remove_check", "remove_status", "add", "set_fields",
           "agents_add", "agents_remove", "remove"]
