"""Handoffs: the key a handoff is filed under, the folder it goes in, its file name, and
which handoffs are still waiting for their seat.

A handoff is a short file a session writes for the next session of its seat. The launcher
points a seat to its waiting handoff at the seat's next start (`listen_handoff`) and gives
a handoff to a launch as its first turn.

**The key.** For a launch, from its row: the comms identity without its `@scope`; else the
seat's name; else the project key. For a session the launcher did not start: the comms name
in its environment, without any `@scope`. No key, no pointer. The project a session's
folder belongs to is not a key: working in a project's folder doesn't make a session that
project's seat.

**The folder.** With notes installed, notes' `handoffs/` folder for the session's folder,
the launch's `notes_dir` passed as notes' override. Else the launcher's own `handoffs`
folder in its data directory.

**The file name.** `<YYYY-MM-DD>-<key>.md`, then `-2`, `-3` before `.md` when the day's name
is taken. Files sort by their date, then that count.

**A handoff is used up only by the seat it was written for.** It counts as picked when the
launcher gave it to a launch, or showed it at startup to a session that claimed a launch of
the same key. A handoff waits while it is newer than the newest handoff of its key a pick
names.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from moxi import exit as exits
from moxi import files, modules
from moxi.paths import Layout

log = logging.getLogger("moxi.launcher.handoffs")

MODULE = "launcher"
#: The launcher's own handoff folder, under its data directory (the `handoffs` store).
FOLDER = "handoffs"
#: comms' identity stamp in a session's environment.
COMMS_NAME = "MOXI_COMMS_NAME"
#: What a key may be: it is part of a file name.
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DATE = r"(\d{4}-\d{2}-\d{2})"


class NoKey(Exception):
    """The session has no key, so it has no handoff folder of its own."""

    exit_code = exits.REFUSED


@dataclass(frozen=True)
class Handoff:
    """One handoff file of a key."""

    path: Path
    date: str
    count: int

    @property
    def order(self) -> tuple[str, int]:
        return (self.date, self.count)


# -- the key -----------------------------------------------------------------------------


def check_key(key: str | None) -> str | None:
    """`key` when it can be part of a file name, else None (and one log line)."""
    if not key:
        return None
    if _KEY.fullmatch(key):
        return key
    log.warning("handoff key %r can't be part of a file name; no handoff is kept for it",
                key)
    return None


def key_of_row(row) -> str | None:
    """A launch's key: its identity without the `@scope`, else its seat's name, else its
    project key."""
    if row.identity:
        return check_key(row.identity.split("@", 1)[0])
    if row.preset_kind == "seat" and row.preset_name:
        return check_key(row.preset_name)
    return check_key(row.project_key)


def key_of_env(env: Mapping[str, str]) -> str | None:
    """The key of a session the launcher did not start: its comms name without any
    `@scope`."""
    name = env.get(COMMS_NAME)
    return check_key(name.split("@", 1)[0]) if name else None


# -- the folder --------------------------------------------------------------------------


def own_folder(env: Mapping[str, str] | None = None, *, create: bool = True) -> Path:
    """The launcher's own handoff folder, used when notes is absent."""
    folder = Layout(MODULE).data_dir(env) / FOLDER
    if create:
        files.private_dir(folder)
    return folder


def folder(cwd: str | Path, *, notes_dir: str | None = None,
           env: Mapping[str, str] | None = None, create: bool = True) -> Path:
    """The handoff folder for a session working in `cwd`.

    With notes installed, notes' `handoffs/` folder for `cwd`, `notes_dir` (a launch's
    `notes_dir` value) passed as its override. Else, or when notes has no folder for `cwd`,
    the launcher's own.
    """
    notes_folder = _notes_folder(cwd, notes_dir, create)
    if notes_folder is not None:
        return Path(notes_folder)
    return own_folder(env, create=create)


def folder_of_row(row, *, env: Mapping[str, str] | None = None,
                  create: bool = True) -> Path:
    """A launch's handoff folder, for its cwd and its `notes_dir` value."""
    notes_dir = row.values.get("notes_dir")
    return folder(row.cwd, notes_dir=notes_dir or None, env=env, create=create)


def _notes_folder(cwd, notes_dir, create):
    if not modules.available("notes"):
        return None
    found = modules.load("notes", interface=1)
    handoffs_dir = getattr(found.api, "handoffs_dir", None) if found.api else None
    if found.state != modules.PRESENT or handoffs_dir is None:
        log.warning("notes is installed but its handoff folder can't be read (%s); the "
                    "launcher's own folder is used", found.reason or "no handoffs_dir")
        return None
    try:
        return handoffs_dir(str(cwd), create=create, override=notes_dir)
    except Exception as exc:  # noqa: BLE001 - a broken link costs notes' folder only
        log.warning("notes' handoff folder for %s failed: %s: %s; the launcher's own "
                    "folder is used", cwd, type(exc).__name__, exc)
        return None


# -- the files ---------------------------------------------------------------------------


def file_name(key: str, date: str, count: int = 1) -> str:
    """The file name of the `count`th handoff of `key` on `date` (`YYYY-MM-DD`)."""
    return f"{date}-{key}.md" if count <= 1 else f"{date}-{key}-{count}.md"


def parse(path: str | Path, key: str) -> Handoff | None:
    """The handoff `path` names when its file name is one of `key`'s, else None."""
    match = re.fullmatch(_DATE + "-" + re.escape(key) + r"(?:-([2-9]|[1-9]\d+))?\.md",
                         Path(path).name)
    if match is None:
        return None
    return Handoff(Path(path), match.group(1), int(match.group(2) or 1))


def handoffs(where: str | Path, key: str) -> list[Handoff]:
    """The handoff files of `key` in folder `where`, newest first."""
    try:
        entries = list(Path(where).iterdir())
    except OSError:
        return []
    found = [h for p in entries if p.is_file() and (h := parse(p, key)) is not None]
    return sorted(found, key=lambda h: h.order, reverse=True)


def unpicked(where: str | Path, key: str, picked: Iterable[str]) -> list[Handoff]:
    """The handoffs of `key` in `where` newer than the newest one a pick names, newest
    first. `picked` is the paths `store.picked(conn, key)` returns."""
    named = [h.order for p in picked if (h := parse(p, key)) is not None]
    newest = max(named) if named else None
    return [h for h in handoffs(where, key) if newest is None or h.order > newest]


def waiting(conn, where: str | Path, key: str) -> Handoff | None:
    """The newest handoff of `key` still waiting for its seat, or None. `conn` is the
    launch log, or None when it was never made (nothing was picked)."""
    from . import store

    left = unpicked(where, key, store.picked(conn, key) if conn is not None else [])
    return left[0] if left else None


def pointer(handoff: Handoff) -> str:
    """The line a seat's start shows for its waiting handoff."""
    return f"Handoff waiting: {handoff.path} ({handoff.date}). Read it before other work."


# -- the current session -----------------------------------------------------------------


@dataclass(frozen=True)
class Current:
    """The current session's key and handoff folder."""

    key: str
    folder: Path
    request_id: str | None


def current(env: Mapping[str, str] | None = None, *, cwd: str | Path | None = None,
            create: bool = True) -> Current:
    """The key and handoff folder of the session whose environment is `env` (this
    process's by default), for `moxi launcher handoff dir`.

    A session the launcher started is read through its launch stamp; any other through
    its comms name, in `cwd` (this directory by default). Raises `NoKey` when the session
    has no key.
    """
    from . import listeners, store

    env = os.environ if env is None else env
    request_id = env.get(listeners.REQUEST)
    if request_id:
        conn = listeners.open_store()
        row = None
        if conn is not None:
            try:
                row = store.get(conn, request_id)
            finally:
                conn.close()
        if row is not None:
            key = key_of_row(row)
            if key is None:
                raise NoKey(f"launch {request_id} has no identity, seat or project key, "
                            "so it has no handoff folder")
            return Current(key, folder_of_row(row, create=create), request_id)
    key = key_of_env(env)
    if key is None:
        raise NoKey("this session has no comms name and no launch of the launcher's, so "
                    "it has no handoff folder")
    return Current(key, folder(cwd if cwd is not None else Path.cwd(), create=create), None)


__all__ = ["COMMS_NAME", "FOLDER", "Current", "Handoff", "NoKey", "check_key", "current",
           "file_name", "folder", "folder_of_row", "handoffs", "key_of_env", "key_of_row",
           "own_folder", "parse", "pointer", "unpicked", "waiting"]
