"""services' link with projects, which is optional: the remove refusal and project keys.

projects reads this module through its `moxi.projects` entry-point group (interface 1)
and asks `refuse_remove(project)` before it removes a project. services answers with one
sentence while any user row still names that project, so a project never goes while its
services stay. The answer reads services' store and nothing else: no write and no
subprocess. It is None only when the store does not exist or holds no row of that
project; a read that fails raises, and projects then refuses the remove.

The other way, services checks a `--project KEY` with `lookup(key)` at the point of use,
through the core's module loader, and keeps the key projects stores. `known_keys()`
gives the keys projects has, for the doctor's rows. This package never imports projects:
it is reached only through `moxi.modules`.
"""

from __future__ import annotations

from . import store
from .errors import Busy, Refused, Unreadable

#: The version of projects' contribution contract this module implements.
INTERFACE = 1

#: The module this links to, and the interface of its read API services uses.
PROJECTS = "projects"
PROJECTS_INTERFACE = 1


def refuse_remove(project) -> str | None:
    """One sentence naming the services that hold `project` (projects' `Project`) and
    the command that frees the first, or None when none does. Raises when the store
    can't be read."""
    held = store.of_project(project.key)
    if not held:
        return None
    named = ", ".join(row.name + (" (remove not finished)" if row.removing else "")
                      for row in held)
    count = f"{len(held)} service{'s' if len(held) != 1 else ''}"
    them = "them" if len(held) != 1 else "it"
    return (f"project {project.key} has {count} ({named}); remove {them} first: "
            f"moxi services remove {held[0].name}")


def _projects():
    """projects' read API, or `Refused` (exit 3) naming how to install it."""
    import moxi.modules

    if not moxi.modules.available(PROJECTS):
        raise Refused("--project needs the projects module, which is not installed",
                      _install_hint())
    found = moxi.modules.load(PROJECTS, interface=PROJECTS_INTERFACE)
    if not found.ok:
        raise Refused(f"--project needs the projects module, which is {found.state}: "
                      f"{found.reason}", _install_hint())
    return found.api


def _install_hint() -> str:
    import moxi.cli

    return moxi.cli.install_hint(PROJECTS)


def _translate(exc: Exception) -> Exception:
    """projects' error as services' own, by its exit code: 3 refused, 75 busy, else
    unreadable."""
    reason = getattr(exc, "reason", None) or str(exc)
    fix = getattr(exc, "fix", None)
    code = getattr(exc, "exit_code", None)
    if code == Refused.exit_code:
        return Refused(reason, fix)
    if code == Busy.exit_code:
        return Busy(reason)
    return Unreadable(reason, fix)


def lookup(key: str) -> str:
    """The project key as projects stores it, for a `--project KEY` given in any case.

    Raises `Refused` (exit 3) when projects is not installed or has no such project,
    `Busy` (exit 75) when its store stayed locked, and `Unreadable` (exit 1) when it
    can't be read."""
    api = _projects()
    try:
        return api.get(key).key
    except Exception as exc:  # noqa: BLE001 - projects' errors carry their exit code
        if getattr(exc, "exit_code", None) is None:
            raise
        raise _translate(exc) from exc


def known_keys() -> frozenset[str] | None:
    """Every project key projects has, folded to compare ignoring case, or None while
    projects is not installed or won't load (rows keep their keys meanwhile). Raises as
    `lookup` when projects' store can't be read."""
    try:
        api = _projects()
    except Refused:
        return None
    try:
        return frozenset(p.key.casefold() for p in api.list_projects())
    except Exception as exc:  # noqa: BLE001 - projects' errors carry their exit code
        if getattr(exc, "exit_code", None) is None:
            raise
        raise _translate(exc) from exc


__all__ = ["INTERFACE", "refuse_remove", "lookup", "known_keys"]
