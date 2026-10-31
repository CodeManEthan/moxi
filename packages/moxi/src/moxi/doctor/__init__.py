"""The doctor's light half: the row a check returns, and records a program leaves for it.

`Row` is what every check gives the doctor: a module's `DOCTOR` target and a
`moxi.doctor` entry return an iterable of rows, a daemon's `HEALTH` target returns one.
A row is in one of four states. Information that needs no action is an `ok` row whose
detail says it.

`record(module, text, key=...)` leaves a finding for the next `moxi doctor` run, from
code that can't print to a person when it finds it (a git hook, a timer). Each record
shows as a `fail` row until `clear(module, key)` or `moxi doctor --clear` removes it. A
newer record with the same key replaces the older.

`health(module, daemon)` runs one daemon's `HEALTH` check through the doctor's runner, in
a worker thread with five seconds, and returns the row `moxi doctor` would show for it.
`floor_ms()` is the median start time of `python -I -c pass`, the floor a `BUDGETS`
`hook_ms` is a multiple of, measured once per process.

This package is standard library only and imports in a few milliseconds, so a git hook
can call `record`. The doctor's runner and checks live in `moxi.doctor._run`, which the
`moxi doctor` command imports, and which `health` and `floor_ms` import inside the call.
"""

from __future__ import annotations

from collections import namedtuple

#: A row's states. `off` is a check that does not apply here, or a thing switched off.
STATES = ("ok", "warn", "fail", "off")
#: The file, in a module's state directory, that holds its records.
RECORDS_FILE = "doctor.json"


class Row(namedtuple("Row", ("module", "name", "state", "detail", "fix"), defaults=(None,))):
    """One line of the doctor's report: `state` is one of `STATES`; `fix` is what to run."""

    __slots__ = ()

    def __new__(cls, module: str, name: str, state: str, detail: str,
                fix: str | None = None):
        if state not in STATES:
            raise ValueError(f"a doctor row's state is one of {', '.join(STATES)}, "
                             f"not {state!r}")
        return super().__new__(cls, module, name, state, detail, fix)


def record(module: str, text: str, *, key: str, fix: str | None = None) -> None:
    """Leave a record, `text`, that the next doctor run shows as a `fail` row of `module`.

    `key` names the record: a newer record with the same key replaces it, and
    `clear(module, key)` removes it. `fix` is the command that resolves it, if any.
    """
    import time

    _check(module, key)
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {"text": str(text), "fix": fix, "at": at}

    def put(records: dict) -> dict:
        records[key] = entry
        return records

    _change(module, put)


def clear(module: str, key: str) -> None:
    """Remove `module`'s record `key`. A key with no record is no error."""
    _check(module, key)
    path = _path(module)
    if not path.exists():
        return

    def drop(records: dict) -> dict:
        records.pop(key, None)
        return records

    _change(module, drop)


def health(module: str, daemon: str) -> Row:
    """Run daemon `daemon`'s `HEALTH` target of `module` and return its row.

    The target runs in a worker thread with five seconds; one that overruns, raises or
    returns something other than a `Row` gives a `fail` row naming it. A daemon with no
    `HEALTH` entry gives `ok`, "no health check declared". Raises `ValueError` when
    `daemon` is not a declared daemon of `module` (its `DAEMONS` and `UNITS_FROM`), or the
    module is not present.
    """
    from . import _run

    return _run.health(module, daemon)


def floor_ms() -> float:
    """The median of five starts of `python -I -c pass`, in milliseconds, measured once
    per process."""
    from . import _run

    return _run.floor_ms()


def _check(module: str, key: str) -> None:
    from .. import names

    names.check_module_name(module)
    if not isinstance(key, str) or not key:
        raise ValueError(f"a doctor record's key is a non-empty string, not {key!r}")


def _path(module: str):
    from .. import paths

    # The core's own records (module `moxi`) go by the core's layout, not a module's.
    return paths._layout(module).state_dir() / RECORDS_FILE


def _change(module: str, fn) -> None:
    import json

    from .. import files

    def edit(old: bytes | None) -> bytes:
        records = {}
        if old:
            try:
                loaded = json.loads(old)
            except ValueError:
                loaded = None
            # A file that isn't a JSON object of records can't be read by the doctor
            # either; the change starts it again rather than failing the caller.
            if isinstance(loaded, dict):
                records = loaded
        return (json.dumps(fn(records), indent=2, sort_keys=True, ensure_ascii=False)
                + "\n").encode()

    files.update(_path(module), edit)


__all__ = ["STATES", "RECORDS_FILE", "Row", "record", "clear", "health", "floor_ms"]
