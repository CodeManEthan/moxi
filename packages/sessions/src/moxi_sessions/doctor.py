"""sessions' doctor checks, `rows()`, which the announcement's `DOCTOR` names: one row per
harness root, then `retention`, each harness's transcript retention as set.

* `ok`: the root is readable. For Codex the imports file beside the sessions root is
  readable too, or absent (an absent one means the user never imported). For OpenCode the
  database opens read only and holds every column sessions reads.
* `warn`, with the reason: the root, or the Codex imports file, exists but cannot be read
  or parsed; a root a config key names is missing; the config file cannot be read.
* `off`: the harness's root is absent and no config key names it, so the harness is not
  used here.

The `retention` row (`retention.retention()`) is `ok` with each harness's setting in its
detail, and `warn` when a harness's settings file can't be read, Codex's compression
switch is on, or a setting holds a value that isn't one. It never changes a setting.

The `guard` row is the transcript-read guard's (`moxi_sessions.readguard`):

* `off`: neither `guard.transcript_read` nor turns' `guards.on` turns it on.
* `ok`: it is on and turns is installed to enforce it; the detail names what turned it on
  and the roots it keeps agents out of.
* `warn`: it is on and turns is absent, so nothing enforces it; or turns' guard loader
  reports a problem with it; or the switch or the roots can't be read. The guard lets
  every call through on a fault, so a fault shows here, not as a refusal.

sessions writes nothing, so no check changes anything.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from moxi.config import ConfigError
from moxi.doctor import Row

from .shape import CLAUDE, CODEX, HARNESSES, OPENCODE

from .source import ROOT_KEYS, root_origin

MODULE = "sessions"

#: Each row's name and what it calls the root.
NAMES = {CLAUDE: ("claude", "Claude Code projects root"),
         CODEX: ("codex", "Codex sessions root"),
         OPENCODE: ("opencode", "OpenCode database")}


def _why(exc: OSError) -> str:
    return exc.strerror or str(exc)


def _absent(harness: str, path: Path, configured: bool) -> Row | None:
    """The row for a root that does not exist, or None when it exists."""
    name, what = NAMES[harness]
    try:
        os.stat(path)
        return None
    except FileNotFoundError:
        pass
    except OSError as exc:
        return Row(MODULE, name, "warn", f"the {what} {path} cannot be read: {_why(exc)}")
    if configured:
        return Row(MODULE, name, "warn",
                   f"{ROOT_KEYS[harness]} names {path}, which does not exist")
    return Row(MODULE, name, "off", f"no {what} at {path}, and no config key names one")


def _directory(harness: str, path: Path, detail: str) -> Row:
    name, what = NAMES[harness]
    if not path.is_dir():
        return Row(MODULE, name, "warn", f"the {what} {path} is not a directory")
    try:
        with os.scandir(path):
            pass
    except OSError as exc:
        return Row(MODULE, name, "warn", f"the {what} {path} cannot be listed: {_why(exc)}")
    return Row(MODULE, name, "ok", f"{path} is readable (from {detail})")


def _codex(path: Path, detail: str) -> Row:
    from . import codex

    row = _directory(CODEX, path, detail)
    if row.state != "ok":
        return row
    imports = codex.read_imports(codex.imports_path(path))
    if imports.reason is not None and imports.reason != codex.IMPORTS_NOT_FOUND:
        return Row(MODULE, "codex", "warn", f"{imports.reason} ({imports.path})")
    said = ("its imports file is absent: nothing was imported" if imports.ids is None
            else f"its imports file lists {len(imports.ids)} threads")
    return row._replace(detail=f"{row.detail}; {said}")


def _opencode(path: Path, detail: str) -> Row:
    from .errors import Unreadable
    from .opencode import TABLES, Snapshot

    try:
        with Snapshot(path) as snap:
            snap.require(*TABLES)
    except Unreadable as exc:
        return Row(MODULE, "opencode", "warn", exc.reason)
    return Row(MODULE, "opencode", "ok",
               f"{path} opens read only and holds every column sessions reads "
               f"(from {detail})")


def check(harness: str) -> Row:
    """The row for one harness's root."""
    name, what = NAMES[harness]
    try:
        origin = root_origin(harness)
    except ConfigError as exc:
        return Row(MODULE, name, "warn",
                   f"the {what} cannot be named: the config file cannot be read ({exc})",
                   "moxi config sessions")
    row = _absent(harness, origin.path, origin.configured)
    if row is not None:
        return row
    if harness == CLAUDE:
        return _directory(CLAUDE, origin.path, origin.detail)
    if harness == CODEX:
        return _codex(origin.path, origin.detail)
    return _opencode(origin.path, origin.detail)


def retention_row() -> Row:
    """Each harness's transcript retention as set, in one row."""
    from .retention import retention

    said = retention()
    detail = "; ".join(item.brief for item in said)
    if any(item.warn for item in said):
        return Row(MODULE, "retention", "warn", detail)
    return Row(MODULE, "retention", "ok", detail)


#: The fix the `guard` row's warning names when turns is absent.
GUARD_FIX = "moxi install sessions --with turns"


def guard_row(env=None) -> Row:
    """The transcript-read guard: `off`, `ok` when on and turns enforces it, `warn` when
    on and nothing does, or on a fault."""
    import moxi.modules

    from .readguard import GUARD_ID, SWITCH, transcript_roots
    from .settings import current

    try:
        switched = bool(current()[SWITCH])
    except ConfigError as exc:
        return Row(MODULE, "guard", "warn",
                   f"the transcript-read guard's switch {SWITCH} cannot be read: {exc}",
                   "moxi config sessions")
    turns = moxi.modules.available("turns")
    found, problem = _in_turns(GUARD_ID, env) if turns else (None, None)
    by = [f"sessions' {SWITCH}"] if switched else []
    if found is not None and "list" in found.by:
        by.append("turns' guards.on")
    if not by:
        return Row(MODULE, "guard", "off",
                   "the transcript-read guard is off; turn it on with: moxi config set "
                   f"sessions {SWITCH} true")
    on = " and ".join(by)
    if not turns:
        return Row(MODULE, "guard", "warn",
                   f"the transcript-read guard is on ({on}) but turns is not installed, "
                   "so nothing enforces it", GUARD_FIX)
    if problem is not None:
        return Row(MODULE, "guard", "warn",
                   f"the transcript-read guard is on ({on}) but turns' guard loader "
                   f"reports: {problem}")
    try:
        roots = sorted({r for _, r in transcript_roots(env)})
    except ConfigError as exc:
        return Row(MODULE, "guard", "warn",
                   f"the transcript-read guard is on ({on}) but its roots cannot be named: "
                   f"{exc}; it lets every call through", "moxi config sessions")
    return Row(MODULE, "guard", "ok",
               f"the transcript-read guard is on ({on}); turns refuses agents' reads under "
               + ", ".join(roots))


def _in_turns(guard_id: str, env) -> tuple[object | None, str | None]:
    """The guard as turns' guard loader reports it: `(On or None, problem or None)`.

    turns' public names offer no read of which guards are on, so this imports turns' guard
    loader, `moxi_turns.guards`, and reads `load(env=)`. A loader that can't be read is the
    problem."""
    import importlib

    try:
        loaded = importlib.import_module("moxi_turns.guards").load(env=env)
    except Exception as exc:  # noqa: BLE001 - a loader that can't be read is the problem
        return None, f"{type(exc).__name__}: {exc}"
    problems = [p.reason for p in loaded.problems
                if p.id in (guard_id, guard_id.partition(".")[0])]
    return loaded.get(guard_id), ("; ".join(problems) if problems else None)


def rows() -> Iterator[Row]:
    """One row per harness root, in the order Claude Code, Codex, OpenCode, then
    `retention` and `guard`."""
    for harness in HARNESSES:
        yield check(harness)
    yield retention_row()
    yield guard_row()


__all__ = ["rows", "check", "retention_row", "guard_row", "GUARD_FIX", "MODULE"]
