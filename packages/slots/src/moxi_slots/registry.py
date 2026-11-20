"""The registry, `slots.json` in the state directory, and the module lock.

The registry holds, per slot, only what tmux can't know: `created`, `name`, `hold` and
`last` (`at`, `label`, `meta`, the run's token `run`, the `server` it was placed under,
`status_trusted`, and `ended`, how the run ended or why it was settled). Everything else
is read from tmux.

    {"version": 2,
     "slots": {"3": {"created": 1793392266.1, "name": "review",
                     "hold": {"holder": "run-7", "until": 1793395866.0},
                     "last": {"at": 1793392300.5, "label": "agent", "meta": {...},
                              "run": "9f2c...", "server": "<boot id>:<tmux pid>",
                              "status_trusted": true,
                              "ended": {"at": 1793399000.0, "status": 0,
                                        "signal": null}}}}}

A `last` written by an older slots has no `run`, `server`, `status_trusted` or `ended`;
it reads with None, None, true and None.

A hold whose `until` is null lasts until its holder releases it. Version 2 is the first
that may hold one, so a slots that reads only version 1 refuses the file by its version
rather than misreading it; this code reads version 1 files as well and writes version 2.

Every change (create, remove, rename, hold, release, settle, a launch, and the
`pane-died` hook's write of a run's end) runs under the one module lock, `locked()`: read
the registry, read tmux, act, write with `write()`, which replaces the file in one step.
The lock is held for the critical section only, never across a wait. Reads take no
lock: the atomic replace always hands them a whole file.

A write drops holds that have run out, which read as absent anyway, and keeps every hold
with a null `until`.
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
from .model import HIGHEST, LOWEST, Hold, Last, RunEnd

VERSION = 2
#: The versions `parse` reads.
READS = (1, 2)
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
def locked(timeout: float = LOCK_WAIT, path: Path | None = None) -> Iterator[None]:
    """Hold the module lock (or the lock file at `path`); `TryAgain` when it is not had in
    `timeout` seconds."""
    from moxi import files

    try:
        with files.lock(places.module_lock() if path is None else path,
                        timeout_s=timeout):
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
    if isinstance(version, bool) or version not in READS:
        raise ValueError(f"version {version!r}, and this code reads versions "
                         f"{', '.join(map(str, READS))}")
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
        until = hold.get("until", False) if isinstance(hold, dict) else False
        if not (isinstance(hold, dict) and isinstance(hold.get("holder"), str)
                and (until is None or (isinstance(until, (int, float))
                                       and not isinstance(until, bool)))):
            raise ValueError(f"slot {key}'s hold has no holder or until")
        hold = Hold(hold["holder"], None if until is None else float(until))
    last = value.get("last")
    if last is not None:
        if not (isinstance(last, dict) and isinstance(last.get("at"), (int, float))
                and isinstance(last.get("label"), str)):
            raise ValueError(f"slot {key}'s last launch has no time or label")
        meta = last.get("meta")
        if meta is not None and not isinstance(meta, dict):
            raise ValueError(f"slot {key}'s last meta is not an object")
        run, server = last.get("run"), last.get("server")
        trusted = last.get("status_trusted", True)
        if not all(v is None or isinstance(v, str) for v in (run, server)) \
                or not isinstance(trusted, bool):
            raise ValueError(f"slot {key}'s last run, server or status_trusted is not one "
                             "slots wrote")
        last = Last(float(last["at"]), last["label"], meta, run, server, trusted,
                    _ended(key, last.get("ended")))
    return Row(float(created), name, hold, last)


def _int_or_none(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _ended(key: str, value: object) -> RunEnd | None:
    if value is None:
        return None
    if not (isinstance(value, dict) and isinstance(value.get("at"), (int, float))
            and not isinstance(value.get("at"), bool)
            and _int_or_none(value.get("status")) and _int_or_none(value.get("signal"))
            and (value.get("settled") is None or isinstance(value.get("settled"), str))):
        raise ValueError(f"slot {key}'s last run's end is not one slots wrote")
    return RunEnd(float(value["at"]), value.get("status"), value.get("signal"),
                  value.get("settled"))


def _ended_json(ended: RunEnd | None) -> dict | None:
    if ended is None:
        return None
    out = {"at": ended.at, "status": ended.status, "signal": ended.signal}
    if ended.settled is not None:
        out["settled"] = ended.settled
    return out


def _last_json(last: Last | None) -> dict | None:
    if last is None:
        return None
    return {"at": last.at, "label": last.label, "meta": last.meta, "run": last.run,
            "server": last.server, "status_trusted": last.status_trusted,
            "ended": _ended_json(last.ended)}


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
            "last": _last_json(row.last)}
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


__all__ = ["VERSION", "READS", "LOCK_WAIT", "Row", "Registry", "locked", "read", "parse", "dump",
           "write", "expired_holds"]
