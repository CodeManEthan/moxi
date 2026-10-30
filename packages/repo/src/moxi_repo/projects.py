"""The optional link to the projects module, for `moxi repo`'s command line only.

repo keys everything by path. With projects installed, a project key works where a
command takes PATH, and listings show each repo's key. repo reaches projects only through
its read API, interface 1, asked for at the point of use: `moxi.modules.available` first,
which reads no metadata, then `moxi.modules.load` only on yes. When either says no, or the
module is broken, these features are off and paths work as before. The git hook runner
never imports this module.

Of the API repo uses `get(key)`, `for_path(path)` and `list_projects()`, and of a project
only its `key` and `root`.
"""

from __future__ import annotations

import os

MODULE = "projects"
INTERFACE = 1
INSTALL = "moxi install projects"


class Unavailable(Exception):
    """A projects read that failed: `text` says why and what fixes it, `code` is the exit
    code a command stops with."""

    def __init__(self, text: str, code: int):
        super().__init__(text)
        self.text = text
        self.code = code


def installed() -> bool:
    """True when projects' package can be imported; reads no metadata."""
    from moxi import modules

    return modules.available(MODULE)


def api():
    """projects' read API module, or None when projects is absent, broken or doesn't offer
    interface 1."""
    from moxi import modules

    if not modules.available(MODULE):
        return None
    found = modules.load(MODULE, interface=INTERFACE)
    if not found.ok or found.api is None:
        return None
    return found.api


def looks_like_key(arg: str) -> bool:
    """An argument that may name a project: it doesn't exist as a path and has no path
    separator in it."""
    if os.path.lexists(arg):
        return False
    seps = {os.sep, os.altsep} - {None}
    return bool(arg) and not any(s in arg for s in seps) and not arg.startswith("~")


def resolve(arg: str) -> str:
    """The path `arg` names: `arg` itself when it is a path or projects is off, else the
    root of the project whose key it is (matched ignoring case). Raises `Unavailable`:
    no such project (2), or projects' store can't be read (its own reason and fix; 3 for a
    store newer than this projects, 75 when it stays locked, else 1)."""
    if not looks_like_key(arg):
        return arg
    found = api()
    if found is None:
        return arg
    try:
        return str(found.get(arg).root)
    except Exception as exc:  # noqa: BLE001 - sorted by projects' own classes below
        raise _unavailable(found, exc, arg) from None


def key_for(found, root: str) -> tuple[str | None, str | None]:
    """(the key of the project `root` is in, or None; a line saying why projects couldn't
    be read, or None)."""
    try:
        project = found.for_path(str(root))
    except Exception as exc:  # noqa: BLE001 - a listing never fails on projects
        return None, _reason(exc)
    return (str(project.key) if project is not None else None), None


def project_roots(found) -> tuple[list[tuple[str, str]], str | None]:
    """([(key, root)] of every project, by key; a line saying why projects couldn't be
    read, or None)."""
    try:
        listed = found.list_projects()
    except Exception as exc:  # noqa: BLE001 - a listing never fails on projects
        return [], _reason(exc)
    return [(str(p.key), str(p.root)) for p in listed], None


def _reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None) or str(exc) or type(exc).__name__
    fix = getattr(exc, "fix", None)
    return f"projects can't be read: {reason}" + (f"; {fix}" if fix else "")


def _unavailable(found, exc: Exception, arg: str) -> Unavailable:
    not_found = getattr(found, "NotFound", None)
    if not_found is not None and isinstance(exc, not_found):
        return Unavailable(f"no project or path named {arg}", 2)
    for name, code in (("TooNew", 3), ("Busy", 75)):
        kind = getattr(found, name, None)
        if kind is not None and isinstance(exc, kind):
            return Unavailable(_reason(exc), code)
    return Unavailable(_reason(exc), 1)


__all__ = ["INSTALL", "Unavailable", "api", "installed", "key_for", "looks_like_key",
           "project_roots", "resolve"]
