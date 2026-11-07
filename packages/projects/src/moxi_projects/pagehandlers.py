"""The handlers of projects' page (`moxi_projects.page` declares the routes).

Each is a plain function, which loopback's base runs off the event loop on its threads,
so every store call, contribution and `git init` here runs off the loop. None calls
`moxi.aio.offload` itself: a worker thread has no loop to offload from.

**Reads** call the read API's functions (`list_projects`, `get`, `kinds`) and the remove
status read, and never write. `/api/where` names the store file this server reads and the
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
Each refuses before it makes anything, as the verb does. Paths are taken as sent and must
be absolute: a relative one, or one starting with `~`, is refused, because this server's
working directory and home are not the user's terminal's. Besides the terminal
comparison, the one change from the verb is a create's bound: `git init` gets
`GIT_TIMEOUT`, 20 seconds, so a mint that hangs answers, with its message naming what is
left and how to finish, inside the shell's 30 second deadline for an act.

**A refusal** is answered as the verb's exit code says: exit 1, 2 and 3 are a 422 whose
text is the reason and the fix, and a store locked past the wait (exit 75) is a 409, try
again. A create refused for want of git also names the lasting fix, since the shell's
PATH is the service manager's.

**Remove** is a confirmed act: its route takes an idempotency key and answers 202 with a
status link, `/api/remove-status?job=<key>`, which gives the outcome. What a remove comes
to is kept in the store under its key for a day (`acts.record_status`): removed, or
refused for a reason about the project (associated agents, a contribution's sentences, a
contribution that won't load or raises, the key not in the store). A repeat of the key
finds that row first after the terminal comparison, after a restart too: for the same
project (compared ignoring case) it answers 202 with the same link, for another a 422. A refusal that is the store's own
(newer than this code, unreadable, locked, or no store file yet) can't be kept there, so
it is answered at once by the map above, and nothing is written.
"""

from __future__ import annotations

import shutil
from typing import Any

from moxi_projects import acts, api, contributions, pagecheck, store
from moxi_projects.api import DEFAULT_KIND, Busy, Project, ProjectsError
from moxi_projects.errors import NoGit
from moxi_projects.page import STATUS_ROUTE, lb

#: How long a create from the page lets `git init` run, in seconds. With the mkdir, the
#: kill and reap after a timeout and the row's write, the answer still comes inside the
#: shell's 30 second deadline for an act. The verb keeps `acts.GIT_TIMEOUT`.
GIT_TIMEOUT = 20

#: What a repeat of a remove's key for another project is told, as loopback says it for
#: another body within one server's life.
KEY_REUSED = "this Idempotency-Key was used for another request"


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

def projects(call: Any) -> dict[str, Any]:
    """`GET /api/projects[?kind=K]`: every project, by key, as `moxi projects list`."""
    try:
        found = api.list_projects(call.query.get("kind"))
    except ProjectsError as exc:
        raise _answer(exc) from None
    return {"projects": [_project(p) for p in found]}


def project(call: Any) -> dict[str, Any]:
    """`GET /api/project?key=K`: one project, as `moxi projects show K`; 422 for no such
    key."""
    try:
        return {"project": _project(api.get(call.query["key"]))}
    except ProjectsError as exc:
        raise _answer(exc) from None


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


def remove_status(call: Any) -> dict[str, Any]:
    """`GET /api/remove-status?job=<key>`: what the remove request came to; 422 for a key
    no request is kept under (an outcome is kept for a day)."""
    job = call.query["job"]
    try:
        kept = acts.remove_status(job)
    except ProjectsError as exc:
        raise _answer(exc) from None
    if kept is None:
        raise lb.Refused("no remove request is kept under this key; an outcome is kept "
                         "for a day")
    return {"job": kept["job"], "project": kept["project"], "created": kept["created"],
            "state": kept["state"], "key": kept["key"], "reason": kept.get("reason"),
            "fix": kept.get("fix"), "sentences": list(kept.get("sentences") or []),
            "exit_code": kept.get("exit_code")}


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


def add(call: Any) -> dict[str, Any]:
    """`POST /api/add`: `moxi projects add`, with `create` its `--create`, under the
    page's `git init` bound. `warnings` holds what the add went ahead without: a
    contribution skipped since it won't load, and the fix."""
    found = contributions.load()
    _compare(found)
    body = call.body
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
    body = call.body
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


def remove(call: Any) -> Any:
    """`POST /api/remove`, confirmed: `moxi projects remove KEY` under the request's
    idempotency key. 202 with the status link for a removal and for every refusal the
    store keeps; the store's own refusals answered at once, keeping nothing.

    Within one server's life loopback answers a repeat of the key itself, comparing the
    raw body, so the project's key is compared ignoring case only after a restart, when
    the kept row answers; the page always resends the same bytes.

    The terminal comparison comes first, before the kept row is looked up: while it
    refuses, a repeat is refused too, though the status link still reads the kept
    outcome."""
    found = contributions.load()
    _compare(found)
    key, job = call.body["key"], call.key
    link = lb.Accepted(f"{STATUS_ROUTE}?job={job}")
    try:
        kept = acts.remove_status(job)
    except ProjectsError as exc:
        raise _answer(exc) from None
    if kept is not None:
        if kept["project"].casefold() == key.casefold():
            return link
        raise lb.Refused(KEY_REUSED)
    try:
        acts.remove(key, contributions=found, record=acts.record_status(job))
    except ProjectsError as exc:
        # A refusal the store kept has its row; any other is answered here.
        try:
            kept = acts.remove_status(job)
        except ProjectsError as again:
            raise _answer(again) from None
        if kept is None:
            raise _answer(exc) from None
    return link


__all__ = ["GIT_TIMEOUT", "KEY_REUSED", "projects", "project", "kinds", "where",
           "remove_status", "add", "set_fields", "agents_add", "agents_remove", "remove"]
