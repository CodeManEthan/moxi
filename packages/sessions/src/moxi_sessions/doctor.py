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


def rows() -> Iterator[Row]:
    """One row per harness root, in the order Claude Code, Codex, OpenCode, then
    `retention`."""
    for harness in HARNESSES:
        yield check(harness)
    yield retention_row()


__all__ = ["rows", "check", "retention_row", "MODULE"]
