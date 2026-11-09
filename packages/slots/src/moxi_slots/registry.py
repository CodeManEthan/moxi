"""The registry, `slots.json` in the state directory, and the module lock.

The registry holds, per slot, only what tmux can't know: `created`, `name`, `hold` and
`last` (`at`, `label`, `meta`). Everything else is read from tmux.

    {"version": 1,
     "slots": {"3": {"created": 1793392266.1, "name": "review",
                     "hold": {"holder": "run-7", "until": 1793395866.0},
                     "last": {"at": 1793392300.5, "label": "agent", "meta": {...}}}}}

Every change (create, remove, rename, hold, release, a launch) runs under the one module
lock, `locked()`: read the registry, read tmux, act, write with `write()`, which replaces
the file in one step. The lock is held for the critical section only, never across a
wait. Reads take no lock: the atomic replace always hands them a whole file.

A write drops holds that have run out, which read as absent anyway.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import places
from .errors import RegistryError, TryAgain
from .model import HIGHEST, LOWEST, Hold, Last

VERSION = 1
#: Seconds a change waits for the module lock before `TryAgain`.
LOCK_WAIT = 10.0


@dataclass
class Row:
    """One slot's row: what tmux can't know."""

    created: float
    name: str | None = None
    hold: Hold | None = None
    last: Last | None = None

    def live_hold(self, now: float | None = None) -> Hold | None:
        now = time.time() if now is None else now
        return self.hold if self.hold is not None and self.hold.live(now) else None


@dataclass
class Registry:
    """The rows by slot number."""

    rows: dict[int, Row] = field(default_factory=dict)

    def by_name(self, name: str) -> int | None:
        return next((n for n, row in self.rows.items() if row.name == name), None)


@contextmanager
def locked(timeout: float = LOCK_WAIT) -> Iterator[None]:
    """Hold the module lock; `TryAgain` when it is not had in `timeout` seconds."""
    from moxi import files

    try:
        with files.lock(places.module_lock(), timeout_s=timeout):
            yield
    except files.LockTimeout as exc:
        raise TryAgain(f"the slots lock was not had in {timeout:g} s ({exc}); try "
                       "again") from None


def read(path: Path | None = None) -> Registry:
    """The registry; empty when the file does not exist. Raises `RegistryError` when it
    cannot be read or is not a registry this code knows."""
    path = places.registry() if path is None else path
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Registry()
    except OSError as exc:
        raise RegistryError(f"the registry {path} cannot be read: {exc}") from None
    try:
        return parse(json.loads(text))
    except (ValueError, TypeError, KeyError) as exc:
        raise RegistryError(f"the registry {path} is not one slots can read: {exc}; "
                            "move it aside and recreate the slots with `moxi slots "
                            "create`") from None


def parse(data: object) -> Registry:
    """A registry from its JSON value. Raises `ValueError` on anything else."""
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    version = data.get("version")
    if version != VERSION:
        raise ValueError(f"version {version!r}, and this code reads version {VERSION}")
    slots = data.get("slots")
    if not isinstance(slots, dict):
        raise ValueError("no slots object")
    rows = {}
    for key, value in slots.items():
        if not (isinstance(key, str) and key.isdigit()
                and LOWEST <= int(key) <= HIGHEST):
            raise ValueError(f"slot key {key!r} is not a number from {LOWEST} to "
                             f"{HIGHEST}")
        rows[int(key)] = _row(key, value)
    return Registry(rows)


def _row(key: str, value: object) -> Row:
    if not isinstance(value, dict):
        raise ValueError(f"slot {key} is not an object")
    created = value.get("created")
    if not isinstance(created, (int, float)) or isinstance(created, bool):
        raise ValueError(f"slot {key} has no created time")
    name = value.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError(f"slot {key}'s name is not a string")
    hold = value.get("hold")
    if hold is not None:
        if not (isinstance(hold, dict) and isinstance(hold.get("holder"), str)
                and isinstance(hold.get("until"), (int, float))):
            raise ValueError(f"slot {key}'s hold has no holder or until")
        hold = Hold(hold["holder"], float(hold["until"]))
    last = value.get("last")
    if last is not None:
        if not (isinstance(last, dict) and isinstance(last.get("at"), (int, float))
                and isinstance(last.get("label"), str)):
            raise ValueError(f"slot {key}'s last launch has no time or label")
        meta = last.get("meta")
        if meta is not None and not isinstance(meta, dict):
            raise ValueError(f"slot {key}'s last meta is not an object")
        last = Last(float(last["at"]), last["label"], meta)
    return Row(float(created), name, hold, last)


def dump(registry: Registry, now: float | None = None) -> dict:
    """The JSON value of `registry`, holds that have run out left out."""
    now = time.time() if now is None else now
    slots = {}
    for number in sorted(registry.rows):
        row = registry.rows[number]
        hold = row.live_hold(now)
        slots[str(number)] = {
            "created": row.created,
            "name": row.name,
            "hold": None if hold is None else {"holder": hold.holder, "until": hold.until},
            "last": None if row.last is None else {"at": row.last.at,
                                                   "label": row.last.label,
                                                   "meta": row.last.meta}}
    return {"version": VERSION, "slots": slots}


def write(registry: Registry, path: Path | None = None) -> None:
    """Replace the registry file in one step (0600). Call it under `locked()`."""
    from moxi import files

    path = places.registry() if path is None else path
    text = json.dumps(dump(registry), indent=1, sort_keys=True) + "\n"
    try:
        files.atomic_write(path, text)
    except OSError as exc:
        raise RegistryError(f"the registry {path} cannot be written: {exc}") from None


def expired_holds(registry: Registry, now: float | None = None) -> list[tuple[int, Hold]]:
    """The holds in the file that have run out."""
    now = time.time() if now is None else now
    return [(n, row.hold) for n, row in sorted(registry.rows.items())
            if row.hold is not None and not row.hold.live(now)]


__all__ = ["VERSION", "LOCK_WAIT", "Row", "Registry", "locked", "read", "parse", "dump",
           "write", "expired_holds"]
