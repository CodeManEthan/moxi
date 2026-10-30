"""A module's declared daemons and timers: the one read, and the checks they share.

`declarations(module)` is the only read of a module's merged declarations: `DAEMONS` and
`TIMERS` from its announcement and, when it names `UNITS_FROM`, what that target returns.
`plan`, the home move and `moxi config set`'s apply after a daemon switch all read through
it, so they never disagree on what a module declares.

The target is imported and called in a worker thread with `UNITS_FROM_SECONDS`. A target
that raises, overruns or returns anything but a table of `daemons` and `timers` raises
`DeclarationError`; a thread that overran is left to finish on its own (a daemon thread,
its result dropped). Each entry it returns is checked as an announcement's entry is, with
four forms allowed only here: `argv` in place of `command`, `cwd`, `enabled` and a timer
with no `switch`. An entry that fails, or whose name is also in `DAEMONS` or `TIMERS`, is
kept out of the merged tables and listed in `Declarations.rejected` with the reason.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from types import MappingProxyType

from ._types import DeclarationError, Declarations

#: Seconds a `UNITS_FROM` target gets.
UNITS_FROM_SECONDS = 10.0

#: The declaration keys a switched timer's digest covers.
DIGEST_KEYS = ("command", "args", "argv", "cwd", "enabled", "every", "at", "days", "switch")

_KINDS = (("daemons", "daemon"), ("timers", "timer"))


def declarations(module: str, *, env: Mapping[str, str] | None = None) -> Declarations:
    """Module `module`'s merged declarations. Raises `DeclarationError` when its
    `UNITS_FROM` target raises, overruns or returns another type, and `ValueError` when
    the module is not present (absent or broken). `env` is accepted for the callers that
    read in another environment; the target runs in this process's own."""
    found = _found(module)
    return merge(found)


def merge(found) -> Declarations:
    """`declarations` for a module already loaded (`moxi.modules.Found`, present)."""
    module = found.name
    daemons = dict(found.daemons)
    timers = dict(found.timers)
    rejected: dict[tuple[str, str], str] = {}
    if found.units_from is not None:
        returned = call_units_from(module, found.units_from)
        from .. import modules

        declared = set(daemons) | set(timers)
        seen: dict[str, str] = {}
        for key, kind in _KINDS:
            table = returned.get(key, {})
            for name, entry in table.items():
                where = f"UNITS_FROM ({found.units_from}) {kind} {name!r}"
                if not isinstance(name, str):
                    rejected[(kind, repr(name))] = f"{where}: a name is a string"
                    continue
                if name in declared:
                    rejected[(kind, name)] = (f"{where} has the name of a unit the "
                                              "announcement declares")
                    continue
                if name in seen:
                    rejected[(kind, name)] = (f"{where} is also returned as a "
                                              f"{seen[name]}")
                    rejected.setdefault((seen[name], name), rejected[(kind, name)])
                    (daemons if seen[name] == "daemon" else timers).pop(name, None)
                    continue
                seen[name] = kind
                try:
                    modules._unit(kind, name, entry, runtime=True, label=key)
                except modules._Bad as exc:
                    rejected[(kind, name)] = f"UNITS_FROM ({found.units_from}) {exc}"
                    continue
                (daemons if kind == "daemon" else timers)[name] = modules._freeze(entry)
        merged_daemons = set(daemons) | {n for k, n in rejected if k == "daemon"}
        for key in sorted(found.health):
            if key not in merged_daemons:
                rejected[("health", key)] = (f"HEALTH names {key!r}, which is no daemon "
                                             "of DAEMONS or UNITS_FROM")
    return Declarations(module, MappingProxyType(daemons), MappingProxyType(timers),
                        MappingProxyType(rejected))


def call_units_from(module: str, target: str) -> Mapping:
    """Import and call `target` in a worker thread; the table it returned, or
    `DeclarationError`."""
    box: list = []

    def run():
        try:
            box.append(("ok", _call(target)))
        except BaseException as exc:  # the thread's whole outcome goes back to the caller
            box.append(("raised", exc))

    seconds = UNITS_FROM_SECONDS
    thread = threading.Thread(target=run, name=f"moxi-units-from-{module}", daemon=True)
    thread.start()
    thread.join(seconds)
    if not box:
        raise DeclarationError(module, f"did not finish in {seconds:g} s")
    outcome, value = box[0]
    if outcome == "raised":
        text = str(value).strip().splitlines()
        raise DeclarationError(module, f"{type(value).__name__}: {text[0]}" if text
                               else type(value).__name__)
    if not isinstance(value, Mapping):
        raise DeclarationError(module, f"returned {type(value).__name__}, not a table of "
                                       "daemons and timers")
    extra = sorted(str(k) for k in value if k not in ("daemons", "timers"))
    if extra:
        raise DeclarationError(module, f"returned the key {extra[0]!r}; the keys are "
                                       "daemons and timers")
    for key, _ in _KINDS:
        if key in value and not isinstance(value[key], Mapping):
            raise DeclarationError(module, f"returned {key} as "
                                           f"{type(value[key]).__name__}, not a table")
    return value


def _call(target: str):
    import importlib

    module_name, _, fn = target.partition(":")
    return getattr(importlib.import_module(module_name), fn)()


def _found(module: str):
    from .. import modules

    found = modules.load(module)
    if found.state != modules.PRESENT:
        raise ValueError(f"{module} is {found.state}" + (f": {found.reason}"
                                                         if found.reason else ""))
    return found


# -- digests ---------------------------------------------------------------------------------

def digest(entry: Mapping) -> str:
    """A timer declaration's digest: the sha256 of the canonical JSON of the keys in
    `DIGEST_KEYS` the entry sets (a key it leaves out is left out)."""
    body = {k: _plain(entry[k]) for k in DIGEST_KEYS if k in entry}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


# -- switches --------------------------------------------------------------------------------

def switch_problem(schema, key: str) -> str | None:
    """Why `key` cannot be a switch in `schema` (a `moxi.config.Schema`, or None for a
    module with no settings), or None when it can.

    A switch is a `bool` key read at use, with no `env` and `default = false`; or
    `<table key>.<entry>` of a switch table (a `table` of bools read at use, no `env`,
    `default = {}`). The name resolves one way only: the key itself when the schema
    declares it, else the name less its last segment, which must be a switch table.
    """
    from .. import config

    if schema is None:
        return f"switch {key} names no setting: the module declares no SETTINGS"
    spec = schema.keys.get(key)
    if spec is not None:
        if spec.retired is not None:
            return f"switch {key} is a retired setting"
        if (spec.type == "bool" and spec.read == "use" and spec.env is None
                and spec.default is False):
            return None
        return (f"switch {key} must be a bool setting read at use, with no env and "
                "default false")
    table, dot, entry = key.rpartition(".")
    spec = schema.keys.get(table) if dot else None
    if spec is None:
        return f"switch {key} is not a setting the module declares"
    if not config.is_switch_table(spec):
        return (f"switch {key} names an entry of {table}, which is not a switch table (a "
                "table of bools read at use, with no env and default {})")
    if not config.is_entry_name(entry):
        return (f"switch {key}: an entry name is lower-case letters, digits, '-' and '_'")
    return None


def switch_value(module: str, schema, key: str, env: Mapping[str, str] | None) -> bool:
    """A switch's value as `moxi.config` reads it. Raises `moxi.config.ConfigError`."""
    from .. import config

    return bool(config.load(module, schema, env=env)[key])


def is_switch_of(key: str, switch: str) -> bool:
    """True when setting `key` (as `moxi config set` names it) sets `switch`: the same
    name, or `key` is the switch table that `switch` is an entry of."""
    return switch == key or switch.rpartition(".")[0] == key
