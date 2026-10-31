"""The settings view: every installed module's settings and the core's, read and written
only through `moxi config` in a fresh process.

    GET  /m/ui/api/settings[?module=m]   the sections, one per module with settings
    POST /m/ui/api/settings/set          `moxi config set <module> <key> ...`
    POST /m/ui/api/settings/unset        `moxi config unset <module> <key>`

The shell imports no module's code, so it never reads a schema itself. A section is what
`moxi config <module> show --json` printed, run as `[python(), "-P", "-m", "moxi", ...]`
from the shell's runtime dir with the children's environment, so it is never older than
the installed module. A write is the same kind of run of `moxi config set` or `unset`
with `--by ui`, and the command checks the value against the schema.

Each key comes back as a field descriptor in the shape of `moxi.config.Key` with its
value, its source, and a mark saying when a change applies. A value travels as the
command line spells it (`true`, `8460`, the plain string, or one TOML value for a list or
table), since a route's answer is cut to one declared type per field; the form sends the
same spelling back. A switch table carries one toggle per entry, each named
`<table key>.<entry>`.

A write that `moxi config` refuses (exit 2 or 3) is a 422 with the command's text and fix;
a settings file that stays locked (exit 75) is a 409. Exit 1 means the value was written
and applying the module's units failed: the answer carries the command's lines as a
warning and the fix `moxi install <module>`.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from moxi import proc
from moxi.names import HARNESS, is_module_name
from moxi.paths import python
from moxi_loopback.api import Conflict, Field, Out, Refused, Route

__all__ = [
    "CHANGE_SHAPE",
    "CONFIG_TIMEOUT",
    "MARK_NEXT_START",
    "MARK_NOW",
    "MARK_SWITCH",
    "ROUTES",
    "SETTINGS_SHAPE",
    "change",
    "change_argv",
    "descriptor",
    "read_section",
    "section_modules",
    "show_argv",
    "spell",
    "switches_of",
]

#: Seconds one `moxi config` run may take.
CONFIG_TIMEOUT = 10.0
#: How many `show` runs one read of the view runs at once.
READS_AT_ONCE = 2
BY = "ui"

MARK_NOW = "applies now"
MARK_NEXT_START = "applies at next start"
MARK_SWITCH = "turns the daemon on or off now"
MARK_PORT = "applies after `moxi install ui`"
MARK_PORT_WINDOWS = "applies when the shell next starts"

_HERE = "moxi_ui.settings"
_BARE_KEY = re.compile(r"\A[A-Za-z0-9_-]+\Z")


# --- the shapes -------------------------------------------------------------------------

_STRING = Out("string", nullable=True)
_STRINGS = Out("list", items=Out("string"))

_ENTRY = Out("table", items={
    "name": Out("string"), "key": Out("string"), "value": Out("bool"),
    "source": Out("string"), "named_by": _STRINGS, "mark": Out("string"),
})

#: One key's field descriptor: `moxi.config.Key`'s fields, its value and source, and
#: what the view adds (the mark, a note, the switch table's entries).
KEY_SHAPE = Out("table", items={
    "name": Out("string"), "type": Out("string"), "default": _STRING,
    "doc": Out("string"), "values": Out("list", nullable=True, items=Out("string")),
    "read": Out("string"), "env": _STRING, "needs": _STRING, "items": _STRING,
    "value": _STRING, "value_items": Out("list", nullable=True, items=Out("string")),
    "source": Out("string"), "mark": Out("string"), "note": _STRING,
    "needs_absent": Out("bool"), "switch_table": Out("bool"),
    "entries": Out("list", nullable=True, items=_ENTRY),
    "declarations": _STRING, "reason": _STRING,
})

SECTION_SHAPE = Out("table", items={
    "module": Out("string"), "keys": Out("list", items=KEY_SHAPE), "error": _STRING,
    "fix": _STRING, "readonly": Out("bool"),
})

SETTINGS_SHAPE = {"sections": Out("list", items=SECTION_SHAPE), "error": _STRING}

CHANGE_SHAPE = {"ok": Out("bool"), "lines": _STRINGS, "warning": _STRING, "fix": _STRING}

_MODULE_FIELD = Field("string", max=64)
_KEY_FIELD = Field("string", max=200)
_VALUE_FIELD = Field("string", required=False, max=65536)


# --- spelling a value -------------------------------------------------------------------


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        # A JSON string's escapes are all TOML basic-string escapes.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        parts = [f"{key if _BARE_KEY.match(str(key)) else _toml(str(key))} = {_toml(item)}"
                 for key, item in value.items()]
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    raise TypeError(f"no TOML spelling for a {type(value).__name__}")


def spell(value: Any, type_: str) -> str | None:
    """`value` as `moxi config set` takes it for a key of type `type_`: `true` or `false`,
    a number, a `string`, `path` or `enum` as it is, and one TOML value for anything
    else. None stays None."""
    if value is None:
        return None
    if type_ in ("string", "path", "enum") and isinstance(value, str):
        return value
    return _toml(value)


# --- descriptors ------------------------------------------------------------------------


def switches_of(module: Mapping[str, Any]) -> set[str]:
    """The keys a module's declared daemons name as their `switch`, from its entry of
    `moxi modules --json`."""
    daemons = module.get("daemons")
    if not isinstance(daemons, Mapping):
        return set()
    return {entry["switch"] for entry in daemons.values()
            if isinstance(entry, Mapping) and isinstance(entry.get("switch"), str)}


def _mark(module: str, name: str, read: Any, switches: set[str], platform: str) -> str:
    if name in switches:
        return MARK_SWITCH
    if read == "use":
        return MARK_NOW
    if module == "ui" and name == "port":
        # Under a socket unit the port is in the unit file; Windows has no socket.
        return MARK_PORT_WINDOWS if platform == "win32" else MARK_PORT
    return MARK_NEXT_START


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return [item if isinstance(item, str) else _toml(item) for item in value]


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _entries(table: str, items: Any) -> list[dict[str, Any]]:
    entries = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            continue
        named_by = [by for by in item.get("named_by") or () if isinstance(by, str)]
        daemon = any(by.startswith("daemon:") for by in named_by)
        entries.append({
            "name": item["name"],
            "key": f"{table}.{item['name']}",
            "value": item.get("value") is True,
            "source": item.get("source") if isinstance(item.get("source"), str) else "default",
            "named_by": named_by,
            "mark": MARK_SWITCH if daemon else MARK_NOW,
        })
    return entries


def descriptor(module: str, item: Mapping[str, Any], *, switches: Iterable[str] = (),
               present: Iterable[str] | None = None,
               platform: str | None = None) -> dict[str, Any]:
    """One key of `moxi config <module> show --json` as the view's field descriptor.

    `switches` are the keys the module's daemons name as their `switch`; `present` the
    modules installed and present (None: not known, so no key is marked as needing an
    absent one)."""
    platform = platform or sys.platform
    name = str(item.get("name"))
    type_ = str(item.get("type"))
    source = item.get("source") if isinstance(item.get("source"), str) else "default"
    env, needs = _text(item.get("env")), _text(item.get("needs"))
    note = None
    if source == "env" and env:
        note = f"set by {env} in the shell's environment, which wins over the file"
    needs_absent = bool(needs and present is not None and needs not in set(present))
    if needs_absent:
        note = f"works only with the {needs} module, which is not installed"
    switch_table = item.get("switch_table") is True
    out: dict[str, Any] = {
        "name": name, "type": type_,
        "default": spell(item.get("default"), type_),
        "doc": item.get("doc") if isinstance(item.get("doc"), str) else "",
        "values": _strings(item.get("values")),
        "read": item.get("read") if isinstance(item.get("read"), str) else "start",
        "env": env, "needs": needs, "items": _text(item.get("items")),
        "value": spell(item.get("value"), type_),
        "value_items": _strings(item.get("value")) if type_ == "list" else None,
        "source": source,
        "mark": _mark(module, name, item.get("read"), set(switches), platform),
        "note": note,
        "needs_absent": needs_absent,
        "switch_table": switch_table,
        "entries": _entries(name, item.get("entries")) if switch_table else None,
        "declarations": None,
        "reason": None,
    }
    if switch_table and item.get("declarations") == "unavailable":
        out["declarations"] = "unavailable"
        reason = _text(item.get("reason")) or "the module's declarations could not be read"
        out["reason"] = (f"{reason}. Entries not yet switched on can't be listed until "
                         "this clears.")
    return out


# --- the commands -----------------------------------------------------------------------


def show_argv(module: str) -> list[str]:
    """`moxi config <module> show --json`, in the spawn rule's form."""
    return [str(python()), "-P", "-m", "moxi", "config", module, "show", "--json"]


def change_argv(module: str, key: str, *, value: str | None = None, add: str | None = None,
                remove: str | None = None, unset: bool = False) -> list[str]:
    """`moxi config set` or `unset` of one key, by `ui`, in the spawn rule's form. The
    value follows `--` and an item rides on its flag, so one starting with `-` is never
    read as an option."""
    head = [str(python()), "-P", "-m", "moxi", "config"]
    if unset:
        return [*head, "unset", "--by", BY, "--", module, key]
    given = [v is not None for v in (value, add, remove)]
    if sum(given) != 1:
        raise ValueError("give exactly one of value, add or remove")
    if add is not None:
        return [*head, "set", "--by", BY, f"--add={add}", "--", module, key]
    if remove is not None:
        return [*head, "set", "--by", BY, f"--remove={remove}", "--", module, key]
    return [*head, "set", "--by", BY, "--", module, key, value]


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _stderr_text(data: bytes | str | None) -> str:
    text = _decode(data).strip()
    return text.removeprefix("moxi: ") if text else ""


def _section(module: str, *, keys: Sequence[dict[str, Any]] = (), error: str | None = None,
             fix: str | None = None) -> dict[str, Any]:
    return {"module": module, "keys": list(keys), "error": error, "fix": fix,
            "readonly": error is not None}


def _valid_module(module: str) -> bool:
    return module == HARNESS or is_module_name(module)


def read_section(module: str, *, cwd: str, env: Mapping[str, str] | None,
                 listing: Mapping[str, Mapping[str, Any]] | None = None,
                 platform: str | None = None,
                 run: Callable[..., subprocess.CompletedProcess] = proc.run) -> dict[str, Any]:
    """`module`'s section of the view, from `moxi config <module> show --json` run in a
    fresh process. `listing` is `moxi modules --json`'s entries by name, for the
    daemons' switches and which modules are present. Blocking; never raises."""
    if not _valid_module(module):
        return _section(module, error=f"{module!r} is not a module name")
    try:
        done = run(show_argv(module), timeout=CONFIG_TIMEOUT, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        return _section(module, error=f"moxi config {module} show ran past "
                                      f"{CONFIG_TIMEOUT:g} s")
    except OSError as err:
        return _section(module, error=f"moxi config could not run: {err}")
    try:
        data = json.loads(done.stdout)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, Mapping) and isinstance(data.get("error"), str):
        # The file won't parse: the section is read-only and says why.
        return _section(module, error=data["error"], fix=_text(data.get("fix")))
    if not isinstance(data, list):
        text = _stderr_text(done.stderr) or f"moxi config {module} show exited " \
                                             f"{done.returncode} with no answer"
        return _section(module, error=text)
    listing = listing or {}
    own = listing.get(module) or {}
    present = ([name for name, entry in listing.items() if entry.get("state") == "present"]
               if listing else None)
    keys = [descriptor(module, item, switches=switches_of(own), present=present,
                       platform=platform)
            for item in data if isinstance(item, Mapping) and isinstance(item.get("name"), str)]
    return _section(module, keys=keys)


def change(module: str, key: str, *, value: str | None = None, add: str | None = None,
           remove: str | None = None, unset: bool = False, cwd: str,
           env: Mapping[str, str] | None,
           run: Callable[..., subprocess.CompletedProcess] = proc.run) -> dict[str, Any]:
    """One `moxi config set` or `unset` run in a fresh process: the change answer, or
    `Refused` (422: the command's text and fix) or `Conflict` (409: the file is busy, or
    the run did not end). Blocking."""
    if not _valid_module(module):
        raise Refused(f"{module!r} is not a module name")
    if not key:
        raise Refused("name the key to change")
    try:
        argv = change_argv(module, key, value=value, add=add, remove=remove, unset=unset)
    except ValueError as err:
        raise Refused(str(err)) from None
    try:
        done = run(argv, timeout=CONFIG_TIMEOUT, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        raise Conflict(f"moxi config ran past {CONFIG_TIMEOUT:g} s; the change may not "
                       "be written") from None
    except OSError as err:
        raise Conflict(f"moxi config could not run: {err}") from None
    lines = [line for line in _decode(done.stdout).splitlines() if line.strip()]
    code = done.returncode
    if code == 0:
        return {"ok": True, "lines": lines, "warning": None, "fix": None}
    text = _stderr_text(done.stderr)
    if code in (2, 3):
        raise Refused(text or f"moxi config refused the change (exit {code})")
    if code == 75:
        raise Conflict("settings file busy" + (f": {text}" if text else ""))
    if code == 1:
        # The value is written; applying the module's units failed.
        lines += [line for line in text.splitlines() if line.strip()]
        return {"ok": True, "lines": lines,
                "warning": "the value is written, and applying the module's units failed",
                "fix": f"moxi install {module}"}
    raise Conflict(text or f"moxi config exited {code}")


def section_modules(listing: Iterable[Mapping[str, Any]]) -> list[str]:
    """The core, then every present module that declares settings, by name."""
    names = sorted(entry["name"] for entry in listing
                   if entry.get("state") == "present" and entry.get("settings")
                   and isinstance(entry.get("name"), str))
    return [HARNESS, *names]


# --- the handlers -----------------------------------------------------------------------


async def _listing(shell: Any) -> tuple[list[dict[str, Any]], str | None]:
    snapshot = shell.snapshot
    if snapshot is None:
        return [], "the module snapshot is off"
    snapshot.touch()
    await snapshot.ready()
    view = snapshot.current
    if view is None:
        return [], "the module snapshot is not ready yet"
    return list(view.listing), view.error


async def read(call: Any) -> dict[str, Any]:
    """`GET /api/settings`: every section, or the one `module` names."""
    from moxi import aio

    from .page import current

    shell = current()
    listed, error = await _listing(shell)
    by_name = {entry["name"]: entry for entry in listed}
    module = call.query.get("module")
    modules = [module] if module is not None else section_modules(listed)
    cwd, env = str(shell.layout.runtime_dir()), shell.starters.child_env()
    gate = asyncio.Semaphore(READS_AT_ONCE)

    async def one(name: str) -> dict[str, Any]:
        async with gate:
            return await aio.offload(lambda: read_section(name, cwd=cwd, env=env,
                                                          listing=by_name))

    sections = await asyncio.gather(*(one(name) for name in modules))
    return {"sections": list(sections), "error": error}


def _change(call: Any, *, unset: bool) -> dict[str, Any]:
    from .page import current

    shell = current()
    body = call.body
    return change(body["module"], body["key"], value=body.get("value"), add=body.get("add"),
                  remove=body.get("remove"), unset=unset,
                  cwd=str(shell.layout.runtime_dir()), env=shell.starters.child_env())


def set_key(call: Any) -> dict[str, Any]:
    """`POST /api/settings/set`: `moxi config set`, with one of `value`, `add` or
    `remove`. Blocking: the base runs it on a worker thread."""
    return _change(call, unset=False)


def unset_key(call: Any) -> dict[str, Any]:
    """`POST /api/settings/unset`: `moxi config unset`, so the key reads its default.
    Blocking: the base runs it on a worker thread."""
    return _change(call, unset=True)


#: The settings view's routes, joined to the shell's page. Every one takes the session's
#: cookie and key.
ROUTES = (
    Route("/api/settings", "read", f"{_HERE}:read",
          query={"module": Field("string", required=False, max=64)}, returns=SETTINGS_SHAPE),
    Route("/api/settings/set", "act", f"{_HERE}:set_key",
          body={"module": _MODULE_FIELD, "key": _KEY_FIELD, "value": _VALUE_FIELD,
                "add": _VALUE_FIELD, "remove": _VALUE_FIELD},
          returns=CHANGE_SHAPE, body_cap=256 * 1024),
    Route("/api/settings/unset", "act", f"{_HERE}:unset_key",
          body={"module": _MODULE_FIELD, "key": _KEY_FIELD}, returns=CHANGE_SHAPE),
)
