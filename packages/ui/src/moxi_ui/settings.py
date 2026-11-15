"""The settings view: every installed module's settings and the core's, read and written
only through `moxi config` in a fresh process.

    GET  /m/ui/api/settings[?module=m]   the sections, one per module with settings
    POST /m/ui/api/settings/set          `moxi config set <module> <key> ...`
    POST /m/ui/api/settings/unset        `moxi config unset <module> <key>`
    POST /m/ui/api/settings/save         one module's changes, each its own command

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
`<table key>.<entry>`. Each key also carries its page fields as `show --json` gives them
(`label`, `line`, `common`, `group`, `unit`, `choices`, `pick`, `hurts`, `terminal`,
`elsewhere`), its value as JSON text (`json`), and `changed`: true when the value is not
the default (a switch table: when any entry is on).

The read answers `listed`, the core and every present module with settings, from the
module snapshot alone. A section adds `file`, the module's config file with the home
folder written `~`, and `count`, its drawn keys: every key but one whose `needs` module is
absent, one edited on another page (`elsewhere`), and ui's `gateway.*` keys, which the
page counts itself while it shows the phone's group. A `module` the listing doesn't hold
is answered with no section, and no command runs for it.

Every write first reads the module's schema with its own `moxi config <module> show
--json`, and takes from that read alone, never from the request, whether the key is a
setting of the module, its type, its value now, and whether it is changed at the terminal
(its `terminal` page field): such a key is refused before any command, so no page turns it
on or off.

A save sends the module and four lists of equal length: `keys`, `values` (each new value
as JSON text), `resets` (true to unset the key) and `was` (the value the page read, as
JSON text). One schema read covers the save. The changes run in the order sent, each as
one `moxi config set --json` or `unset --json`, and nothing is rolled back. A change whose
`was` is not the value the read holds is `moved` and not written, with `problem` the value
now read, as JSON text. Each answers one result: `saved`, `unchanged`, `refused` (with the
command's `problem` and `fix`), `moved`, `busy` (the file's lock was held) or `failed`. A
`failed` result with `finished` false is a command stopped before it ended, which may or
may not have written; every other result has `finished` true. A `failed` result with a
`warning` was written and applying the module's units failed (`lines` are theirs, `fix`
is `moxi install <module>`); else `problem` says why nothing was written. When the schema
read fails, every change is `failed` with its error and nothing runs.

On `set` and `unset`, a write that `moxi config` refuses (exit 2 or 3) is a 422 with the
command's text and fix; a settings file that stays locked (exit 75) is a 409. Exit 1 means
the value was written and applying the module's units failed: the answer carries the
command's lines as a warning and the fix `moxi install <module>`.

Whoever starts the shell can name settings its pages may not change for that run, in
`MOXI_UI_LOCKED_SETTINGS`: full keys, `<module>.<key>`, separated by commas or spaces. An
entry locks the key it names and every key under it, and a key above it too: a write to
a table would replace the entry it holds. A write to a locked key is a 422 before any
command is built, and the read marks the key `locked`. Unset or empty, the variable locks
nothing. It guards these routes only: `moxi config set` typed at a
keyboard is not refused.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from moxi import proc
from moxi.names import HARNESS, is_module_name
from moxi.paths import Layout, core, home, python
from moxi_ui.loopback.api import BadRequest, Conflict, Field, Out, Refused, Route

__all__ = [
    "CHANGE_SHAPE",
    "CONFIG_TIMEOUT",
    "LOCKED_ENV",
    "MARK_NEXT_START",
    "MARK_NOW",
    "MARK_SWITCH",
    "ROUTES",
    "SAVE_SHAPE",
    "SETTINGS_SHAPE",
    "SchemaRead",
    "WRITE_TIMEOUT",
    "change",
    "change_argv",
    "descriptor",
    "file_of",
    "find_key",
    "is_changed",
    "is_drawn",
    "is_locked",
    "locked_entries",
    "logins_of",
    "read_schema",
    "read_section",
    "save_one",
    "section_modules",
    "show_argv",
    "spell",
    "switches_of",
]

#: Seconds one `moxi config` read may take.
CONFIG_TIMEOUT = 10.0
#: Seconds one `moxi config set` or `unset` may take: past the 10 s the command waits for
#: the file's lock, so a run waiting on it says busy before it is stopped.
WRITE_TIMEOUT = 20.0
#: How many `show` runs one read of the view runs at once.
READS_AT_ONCE = 2
BY = "ui"
#: The variable naming the settings the shell's pages may not change for this run.
LOCKED_ENV = "MOXI_UI_LOCKED_SETTINGS"

MARK_NOW = "applies now"
MARK_NEXT_START = "applies at next start"
MARK_SWITCH = "turns the daemon on or off now"
MARK_PORT = "applies after `moxi install ui`"
MARK_PORT_WINDOWS = "applies when the shell next starts"
MARK_LOGIN = "starts the shell at login"
MARK_LOGIN_NOW = {"login": "the shell starts at login now",
                  "connection": "the shell starts at the first connection now"}
MARK_LOGIN_DARWIN = "from the next login"
MARK_LOGIN_WINDOWS = ("the shell starts at every logon on Windows; this switch doesn't change "
                      "that")

#: ui's keys the page draws in its phone's group, not among the module's rows.
PHONE_KEYS = "gateway."

_HERE = "moxi_ui.settings"
_BARE_KEY = re.compile(r"\A[A-Za-z0-9_-]+\Z")


# --- the shapes -------------------------------------------------------------------------

_STRING = Out("string", nullable=True)
_STRINGS = Out("list", items=Out("string"))

_ENTRY = Out("table", items={
    "name": Out("string"), "key": Out("string"), "value": Out("bool"),
    "source": Out("string"), "named_by": _STRINGS, "mark": Out("string"),
    "locked": Out("bool"),
})

#: One key's field descriptor: `moxi.config.Key`'s fields, its value and source, and
#: what the view adds (the mark, a note, the switch table's entries, whether the key is
#: locked for this run).
KEY_SHAPE = Out("table", items={
    "name": Out("string"), "type": Out("string"), "default": _STRING,
    "doc": Out("string"), "values": Out("list", nullable=True, items=Out("string")),
    "read": Out("string"), "env": _STRING, "needs": _STRING, "items": _STRING,
    "value": _STRING, "value_items": Out("list", nullable=True, items=Out("string")),
    "source": Out("string"), "mark": Out("string"), "note": _STRING,
    "needs_absent": Out("bool"), "switch_table": Out("bool"),
    "entries": Out("list", nullable=True, items=_ENTRY),
    "declarations": _STRING, "reason": _STRING, "locked": Out("bool"),
    "label": _STRING, "line": _STRING, "common": Out("bool"), "group": _STRING,
    "unit": _STRING,
    "choices": Out("list", nullable=True, items=Out("table", items={
        "value": Out("string"), "words": Out("string")})),
    "pick": Out("bool"),
    "hurts": Out("table", nullable=True, items={
        "when": Out("string"), "says": Out("string"), "confirm": Out("string"),
        "stays": Out("bool")}),
    "terminal": Out("table", nullable=True, items={"on": Out("string"), "off": Out("string")}),
    "elsewhere": _STRING, "json": Out("string"), "changed": Out("bool"),
})

SECTION_SHAPE = Out("table", items={
    "module": Out("string"), "keys": Out("list", items=KEY_SHAPE), "error": _STRING,
    "fix": _STRING, "readonly": Out("bool"), "file": _STRING, "count": Out("int"),
})

SETTINGS_SHAPE = {"sections": Out("list", items=SECTION_SHAPE), "listed": _STRINGS,
                  "error": _STRING}

CHANGE_SHAPE = {"ok": Out("bool"), "lines": _STRINGS, "warning": _STRING, "fix": _STRING}

#: One change's result in a save: `outcome` is `saved`, `unchanged`, `refused`, `moved`,
#: `busy` or `failed`; `mark` the key's mark, when the module's schema names it;
#: `finished` false when the command was stopped before it ended.
RESULT_SHAPE = Out("table", items={
    "key": Out("string"), "outcome": Out("string"), "problem": _STRING, "fix": _STRING,
    "mark": _STRING, "warning": _STRING, "lines": _STRINGS, "finished": Out("bool"),
})

SAVE_SHAPE = {"saved_at": Out("float"), "results": Out("list", items=RESULT_SHAPE)}

_MODULE_FIELD = Field("string", max=64)
_KEY_FIELD = Field("string", max=200)
_VALUE_FIELD = Field("string", required=False, max=65536)
#: The most changes one save carries.
SAVE_MAX = 64
_JSON_FIELD = Field("list", max=SAVE_MAX, items=Field("string", max=65536))


# --- locked keys ------------------------------------------------------------------------

_SEPARATORS = re.compile(r"[,\s]+")


def locked_entries(text: str | None) -> frozenset[str]:
    """The entries of a `MOXI_UI_LOCKED_SETTINGS` value: full keys, `<module>.<key>`,
    separated by commas or spaces. An empty piece is no entry; None or "" gives none."""
    return frozenset(piece for piece in _SEPARATORS.split(text or "") if piece)


def is_locked(module: str, key: str, locked: Iterable[str]) -> bool:
    """Whether `locked` holds `<module>.<key>`, an entry it sits under, or an entry that
    sits under it: a write to a table would replace a locked entry of it. The text is
    compared exactly, since `moxi config` takes a setting only by its exact name."""
    name = f"{module}.{key}"
    return any(name == entry or name.startswith(f"{entry}.") or entry.startswith(f"{name}.")
               for entry in locked)


def _locked_now() -> frozenset[str]:
    return locked_entries(os.environ.get(LOCKED_ENV))


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


def logins_of(module: Mapping[str, Any]) -> dict[str, str | None]:
    """The keys a module's daemons name in a `socket.start` of `{"login": <key>}`, each with
    what starts that daemon now (`login`, `connection`, or None when unknown), from its
    entry of `moxi modules --json`."""
    daemons, starts = module.get("daemons"), module.get("starts")
    if not isinstance(daemons, Mapping):
        return {}
    starts = starts if isinstance(starts, Mapping) else {}
    out: dict[str, str | None] = {}
    for name, entry in daemons.items():
        socket = entry.get("socket") if isinstance(entry, Mapping) else None
        start = socket.get("start") if isinstance(socket, Mapping) else None
        key = start.get("login") if isinstance(start, Mapping) else None
        if isinstance(key, str):
            now = starts.get(name)
            out[key] = now if now in MARK_LOGIN_NOW else None
    return out


def _login_mark(now: str | None, platform: str) -> str:
    """The mark of a key that decides whether a daemon starts at login. Changing it
    restarts nothing, so it carries no restart warning. On Windows the logon task starts
    the shell at every logon whatever the key says, so what the plan resolves isn't read."""
    if platform == "win32":
        return MARK_LOGIN_WINDOWS
    if now is None:
        return MARK_LOGIN
    mark = f"{MARK_LOGIN}; {MARK_LOGIN_NOW[now]}"
    return f"{mark}, {MARK_LOGIN_DARWIN}" if platform == "darwin" else mark


def _mark(module: str, name: str, read: Any, switches: set[str], platform: str,
          logins: Mapping[str, str | None] | None = None) -> str:
    if logins and name in logins:
        return _login_mark(logins[name], platform)
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


def _entries(module: str, table: str, items: Any,
             locked: Iterable[str]) -> list[dict[str, Any]]:
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
            "locked": is_locked(module, f"{table}.{item['name']}", locked),
        })
    return entries


def _page_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    """The key's page fields from `show --json`, each in its declared shape; a field an
    older core doesn't print, or one of the wrong shape, reads as absent."""
    choices = item.get("choices")
    rows = None
    if isinstance(choices, list):
        rows = [{"value": row["value"], "words": row["words"]} for row in choices
                if isinstance(row, Mapping) and isinstance(row.get("value"), str)
                and isinstance(row.get("words"), str)]
    hurts, terminal = item.get("hurts"), item.get("terminal")
    if not (isinstance(hurts, Mapping) and all(isinstance(hurts.get(part), str)
                                               for part in ("when", "says", "confirm"))):
        hurts = None
    else:
        hurts = {"when": hurts["when"], "says": hurts["says"], "confirm": hurts["confirm"],
                 "stays": hurts.get("stays") is True}
    if not (isinstance(terminal, Mapping) and isinstance(terminal.get("on"), str)
            and isinstance(terminal.get("off"), str)):
        terminal = None
    else:
        terminal = {"on": terminal["on"], "off": terminal["off"]}
    return {
        "label": _text(item.get("label")), "line": _text(item.get("line")),
        "common": item.get("common") is True, "group": _text(item.get("group")),
        "unit": _text(item.get("unit")), "choices": rows, "pick": item.get("pick") is True,
        "hurts": hurts, "terminal": terminal, "elsewhere": _text(item.get("elsewhere")),
    }


def _json_text(value: Any) -> str:
    """`value` as JSON text; a float JSON can't hold (nan, inf) as `null`."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except ValueError:
        return "null"


def _path_default(default: Any, env: Mapping[str, str] | None) -> Any:
    """A `path` key's default as its value reads: none when empty, a leading `~` expanded
    to the home folder `env` places."""
    if not isinstance(default, str) or default == "":
        return None
    if default == "~" or default.startswith("~/"):
        try:
            return str(home(env)) + default[1:]
        except LookupError:
            return default
    return default


def is_changed(item: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> bool:
    """Whether the key `show --json` printed as `item` holds a value other than its
    default. A switch table is changed when any entry is on, since a missing entry reads
    off."""
    if item.get("switch_table") is True:
        entries = item.get("entries")
        return isinstance(entries, list) and any(
            isinstance(entry, Mapping) and entry.get("value") is True for entry in entries)
    default = item.get("default")
    if item.get("type") == "path":
        default = _path_default(default, env)
    return item.get("value") != default


def is_drawn(module: str, key: Mapping[str, Any]) -> bool:
    """Whether the page draws descriptor `key` among `module`'s rows: not when it needs an
    absent module, not when another page edits it, and not ui's `gateway.*` keys, which
    the phone's group draws."""
    if key.get("needs_absent") or key.get("elsewhere"):
        return False
    return not (module == "ui" and str(key.get("name")).startswith(PHONE_KEYS))


def descriptor(module: str, item: Mapping[str, Any], *, switches: Iterable[str] = (),
               present: Iterable[str] | None = None, platform: str | None = None,
               logins: Mapping[str, str | None] | None = None,
               locked: Iterable[str] = (),
               env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """One key of `moxi config <module> show --json` as the view's field descriptor.

    `switches` are the keys the module's daemons name as their `switch`; `logins` those
    they name in a login-switch `start`, with what starts the daemon now; `present` the
    modules installed and present (None: not known, so no key is marked as needing an
    absent one); `locked` the entries the shell's pages may not change; `env` the
    environment `moxi config` ran in, whose home a `path` key's `~` default reads."""
    platform = platform or sys.platform
    locked = frozenset(locked)
    name = str(item.get("name"))
    type_ = str(item.get("type"))
    source = item.get("source") if isinstance(item.get("source"), str) else "default"
    variable, needs = _text(item.get("env")), _text(item.get("needs"))
    note = None
    if source == "env" and variable:
        note = f"set by {variable} in the shell's environment, which wins over the file"
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
        "env": variable, "needs": needs, "items": _text(item.get("items")),
        "value": spell(item.get("value"), type_),
        "value_items": _strings(item.get("value")) if type_ == "list" else None,
        "source": source,
        "mark": _mark(module, name, item.get("read"), set(switches), platform, logins),
        "note": note,
        "needs_absent": needs_absent,
        "switch_table": switch_table,
        "entries": (_entries(module, name, item.get("entries"), locked)
                    if switch_table else None),
        "declarations": None,
        "reason": None,
        "locked": is_locked(module, name, locked),
        **_page_fields(item),
        "json": _json_text(item.get("value")),
        "changed": is_changed(item, env=env),
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
                remove: str | None = None, unset: bool = False,
                as_json: bool = False) -> list[str]:
    """`moxi config set` or `unset` of one key, by `ui`, in the spawn rule's form, with
    `--json` when `as_json`. The value follows `--` and an item rides on its flag, so one
    starting with `-` is never read as an option."""
    head = [str(python()), "-P", "-m", "moxi", "config"]
    by = ["--by", BY, *(["--json"] if as_json else [])]
    if unset:
        return [*head, "unset", *by, "--", module, key]
    given = [v is not None for v in (value, add, remove)]
    if sum(given) != 1:
        raise ValueError("give exactly one of value, add or remove")
    if add is not None:
        return [*head, "set", *by, f"--add={add}", "--", module, key]
    if remove is not None:
        return [*head, "set", *by, f"--remove={remove}", "--", module, key]
    return [*head, "set", *by, "--", module, key, value]


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _stderr_text(data: bytes | str | None) -> str:
    text = _decode(data).strip()
    return text.removeprefix("moxi: ") if text else ""


def file_of(module: str, env: Mapping[str, str] | None = None) -> str | None:
    """`module`'s config file as `env` places it, with the home folder written `~`; None
    for a name that is not a module's."""
    if not _valid_module(module):
        return None
    layout = core() if module == HARNESS else Layout(module)
    path = str(layout.config_file(env))
    try:
        root = str(home(env)).rstrip("/\\")
    except LookupError:
        return path
    if root and path.startswith(root) and path[len(root):len(root) + 1] in ("/", "\\"):
        return "~" + path[len(root):]
    return path


def _section(module: str, *, keys: Sequence[dict[str, Any]] = (), error: str | None = None,
             fix: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return {"module": module, "keys": list(keys), "error": error, "fix": fix,
            "readonly": error is not None, "file": file_of(module, env),
            "count": sum(1 for key in keys if is_drawn(module, key))}


def _valid_module(module: str) -> bool:
    return module == HARNESS or is_module_name(module)


class SchemaRead(NamedTuple):
    """One read of `moxi config <module> show --json`: its keys in order, or why it gave
    none. `ended` is false when the command did not run to its end."""

    keys: list[dict[str, Any]] | None
    error: str | None = None
    fix: str | None = None
    ended: bool = True


def read_schema(module: str, *, cwd: str, env: Mapping[str, str] | None,
                run: Callable[..., subprocess.CompletedProcess] = proc.run) -> SchemaRead:
    """`module`'s keys as `moxi config <module> show --json` prints them, run in a fresh
    process. Blocking; never raises."""
    if not _valid_module(module):
        return SchemaRead(None, f"{module!r} is not a module name")
    try:
        done = run(show_argv(module), timeout_s=CONFIG_TIMEOUT, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        return SchemaRead(None, f"moxi config {module} show ran past {CONFIG_TIMEOUT:g} s",
                      ended=False)
    except OSError as exc:
        return SchemaRead(None, f"moxi config could not run: {exc}", ended=False)
    try:
        data = json.loads(done.stdout)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, Mapping) and isinstance(data.get("error"), str):
        # The file won't parse.
        return SchemaRead(None, data["error"], _text(data.get("fix")))
    if not isinstance(data, list):
        text = _stderr_text(done.stderr) or f"moxi config {module} show exited " \
                                             f"{done.returncode} with no answer"
        return SchemaRead(None, text)
    return SchemaRead([item for item in data
                   if isinstance(item, Mapping) and isinstance(item.get("name"), str)])


def read_section(module: str, *, cwd: str, env: Mapping[str, str] | None,
                 listing: Mapping[str, Mapping[str, Any]] | None = None,
                 platform: str | None = None, locked: Iterable[str] = (),
                 run: Callable[..., subprocess.CompletedProcess] = proc.run) -> dict[str, Any]:
    """`module`'s section of the view, from `moxi config <module> show --json` run in a
    fresh process. `listing` is `moxi modules --json`'s entries by name, for the
    daemons' switches and which modules are present; `locked` the entries whose keys
    are marked locked. Blocking; never raises."""
    schema = read_schema(module, cwd=cwd, env=env, run=run)
    if schema.keys is None:
        # A file that won't parse makes the section read-only, saying why.
        return _section(module, env=env, error=schema.error, fix=schema.fix)
    listing = listing or {}
    own = listing.get(module) or {}
    present = ([name for name, entry in listing.items() if entry.get("state") == "present"]
               if listing else None)
    keys = [descriptor(module, item, switches=switches_of(own), present=present,
                       platform=platform, logins=logins_of(own), locked=locked, env=env)
            for item in schema.keys]
    return _section(module, keys=keys, env=env)


def find_key(keys: Iterable[Mapping[str, Any]], key: str) -> Mapping[str, Any] | None:
    """Key `key` in a schema read's keys, or None when the read doesn't list it. An entry
    of a switch table, `<table>.<entry>`, is found as a bool key of its own whose value is
    the entry's (off when the file doesn't hold it)."""
    keys = list(keys)
    for item in keys:
        if item.get("name") == key:
            return item
    table, dot, entry = key.rpartition(".")
    if not dot:
        return None
    for item in keys:
        if item.get("name") == table and item.get("switch_table") is True:
            found = [e for e in item.get("entries") or () if isinstance(e, Mapping)
                     and e.get("name") == entry]
            named_by = found[0].get("named_by") or () if found else ()
            return {"name": key, "type": "bool", "default": False, "read": "use",
                    "value": found[0].get("value") is True if found else False,
                    "terminal": None, "entry": True,
                    "daemon": any(isinstance(by, str) and by.startswith("daemon:")
                                  for by in named_by)}
    return None


def _terminal_problem(terminal: Mapping[str, Any], wanted: Any) -> str:
    """`is changed at the terminal: <command>`, the command for the value `wanted` (true,
    false, or, unknown, either)."""
    if wanted is True or wanted is False:
        return f"is changed at the terminal: {terminal['on' if wanted else 'off']}"
    return f"is changed at the terminal: {terminal['on']} or {terminal['off']}"


def _terminal(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    terminal = item.get("terminal")
    if isinstance(terminal, Mapping) and isinstance(terminal.get("on"), str) \
            and isinstance(terminal.get("off"), str):
        return terminal
    return None


def _schema_refusal(module: str, schema: SchemaRead) -> Exception:
    text = schema.error or f"moxi config {module} show gave no answer"
    if schema.fix:
        text = f"{text}; fix: {schema.fix}"
    return Refused(text) if schema.ended else Conflict(text)


def change(module: str, key: str, *, value: str | None = None, add: str | None = None,
           remove: str | None = None, unset: bool = False, cwd: str,
           env: Mapping[str, str] | None, locked: Iterable[str] = (),
           run: Callable[..., subprocess.CompletedProcess] = proc.run) -> dict[str, Any]:
    """One `moxi config set` or `unset` run in a fresh process: the change answer, or
    `Refused` (422: the command's text and fix) or `Conflict` (409: the file is busy, or
    the run did not end). A key that `locked` holds, or that holds a locked key, is refused
    before a command is built. Then the module's schema is read in its own run: a key it
    doesn't list, and a key changed at the terminal, are refused before the write.
    The set and unset routes call this. Blocking."""
    if not _valid_module(module):
        raise Refused(f"{module!r} is not a module name")
    if not key:
        raise Refused("name the key to change")
    if is_locked(module, key, locked):
        raise Refused(f"{module} {key} is locked for this run of the shell, so it can't be "
                      "changed from a page")
    try:
        argv = change_argv(module, key, value=value, add=add, remove=remove, unset=unset)
    except ValueError as exc:
        raise Refused(str(exc)) from None
    schema = read_schema(module, cwd=cwd, env=env, run=run)
    if schema.keys is None:
        raise _schema_refusal(module, schema)
    item = find_key(schema.keys, key)
    if item is None:
        raise Refused(f"{key} is not a setting of {module}")
    if (terminal := _terminal(item)) is not None:
        wanted = item.get("default") if unset else {"true": True, "false": False}.get(
            (value or "").strip().lower())
        raise Refused(f"{module} {key} {_terminal_problem(terminal, wanted)}")
    try:
        done = run(argv, timeout_s=WRITE_TIMEOUT, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        raise Conflict(f"moxi config ran past {WRITE_TIMEOUT:g} s; the change may not "
                       "be written") from None
    except OSError as exc:
        raise Conflict(f"moxi config could not run: {exc}") from None
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


def _result(key: str, outcome: str, *, problem: str | None = None, fix: str | None = None,
            mark: str | None = None, warning: str | None = None,
            lines: Sequence[str] = (), finished: bool = True) -> dict[str, Any]:
    return {"key": key, "outcome": outcome, "problem": problem, "fix": fix, "mark": mark,
            "warning": warning, "lines": list(lines), "finished": finished}


def _parsed(text: str) -> tuple[bool, Any]:
    try:
        return True, json.loads(text)
    except ValueError:
        return False, None


def _same(a: Any, b: Any) -> bool:
    """Whether two parsed JSON values are the same value: `true` is never `1`."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b


def _key_mark(module: str, item: Mapping[str, Any], own: Mapping[str, Any],
              platform: str | None) -> str:
    if item.get("entry"):
        return MARK_SWITCH if item.get("daemon") else MARK_NOW
    return _mark(module, str(item.get("name")), item.get("read"), switches_of(own),
                 platform or sys.platform, logins_of(own))


def save_one(module: str, key: str, *, value: str, reset: bool, was: str,
             keys: Sequence[Mapping[str, Any]], cwd: str, env: Mapping[str, str] | None,
             locked: Iterable[str] = (), own: Mapping[str, Any] | None = None,
             platform: str | None = None,
             run: Callable[..., subprocess.CompletedProcess] = proc.run) -> dict[str, Any]:
    """One change of a save: its result, after at most one `moxi config set --json` or
    `unset --json`. `keys` is the save's one schema read, which alone decides whether
    `key` is a setting, its type, its value now and whether it is changed at the terminal;
    `own` is the module's entry of `moxi modules --json`, for the mark. Blocking; never
    raises."""
    item = find_key(keys, key)
    if item is None:
        return _result(key, "refused", problem=f"is not a setting of {module}")
    mark = _key_mark(module, item, own or {}, platform)
    if is_locked(module, key, locked):
        return _result(key, "refused", mark=mark,
                       problem="is locked for this run of the shell, so it can't be "
                               "changed from a page")
    ok, wanted = (True, item.get("default")) if reset else _parsed(value)
    if not ok:
        return _result(key, "refused", mark=mark, problem="the value sent is not JSON text")
    if (terminal := _terminal(item)) is not None:
        return _result(key, "refused", mark=mark, problem=_terminal_problem(terminal, wanted))
    ok, read_as = _parsed(was)
    if not ok:
        return _result(key, "refused", mark=mark, problem="was is not JSON text")
    now = item.get("value")
    if not _same(now, read_as):
        return _result(key, "moved", mark=mark, problem=_json_text(now))
    type_ = str(item.get("type"))
    if reset:
        argv = change_argv(module, key, unset=True, as_json=True)
    else:
        if type_ == "path" and wanted is None:
            wanted = ""
        try:
            spelled = spell(wanted, type_)
        except TypeError:
            spelled = None
        if spelled is None:
            return _result(key, "refused", mark=mark,
                           problem=f"the value sent can't be a {type_}")
        argv = change_argv(module, key, value=spelled, as_json=True)
    try:
        done = run(argv, timeout_s=WRITE_TIMEOUT, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        # Stopped before it ended, so it may or may not have written.
        return _result(key, "failed", mark=mark, finished=False)
    except OSError as exc:
        return _result(key, "failed", mark=mark, problem=f"moxi config could not run: {exc}")
    ok, answer = _parsed(_decode(done.stdout).strip())
    if not ok or not isinstance(answer, Mapping):
        lines = [line for line in _stderr_text(done.stderr).splitlines() if line.strip()]
        return _result(key, "failed", mark=mark, problem=lines[-1] if lines else
                       f"moxi config exited {done.returncode} with no answer")
    problem, fix = _text(answer.get("problem")), _text(answer.get("fix"))
    units = [line for line in answer.get("units") or () if isinstance(line, str)]
    code = done.returncode
    if code == 0:
        return _result(key, "saved" if answer.get("changed") is not False else "unchanged",
                       mark=mark, lines=units)
    if code in (2, 3):
        return _result(key, "refused", problem=problem, fix=fix, mark=mark)
    if code == 75:
        return _result(key, "busy", problem=problem, mark=mark)
    if code == 1:
        return _result(key, "failed", mark=mark, lines=units, fix=f"moxi install {module}",
                       warning="the value is written, and applying the module's units failed")
    return _result(key, "failed", problem=problem or f"moxi config exited {code}", fix=fix,
                   mark=mark)


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
    """`GET /api/settings`: the listed modules and every section, or the one `module`
    names when it is listed; an unlisted `module` gets no section and runs nothing."""
    from moxi import aio

    from .page import current

    shell = current()
    listing, error = await _listing(shell)
    by_name = {entry["name"]: entry for entry in listing}
    listed = section_modules(listing)
    module = call.query.get("module")
    modules = listed if module is None else [name for name in listed if name == module]
    cwd, env = str(shell.layout.runtime_dir()), shell.starters.child_env()
    locked = _locked_now()
    gate = asyncio.Semaphore(READS_AT_ONCE)

    async def one(name: str) -> dict[str, Any]:
        async with gate:
            return await aio.offload(lambda: read_section(name, cwd=cwd, env=env,
                                                          listing=by_name, locked=locked))

    sections = await asyncio.gather(*(one(name) for name in modules))
    return {"sections": list(sections), "listed": listed, "error": error}


def _change(call: Any, *, unset: bool) -> dict[str, Any]:
    from .page import current

    shell = current()
    body = call.body
    return change(body["module"], body["key"], value=body.get("value"), add=body.get("add"),
                  remove=body.get("remove"), unset=unset, locked=_locked_now(),
                  cwd=str(shell.layout.runtime_dir()), env=shell.starters.child_env())


async def save(call: Any) -> dict[str, Any]:
    """`POST /api/settings/save`: one module's changes, each its own command, in the order
    sent, after one read of the module's schema. A module the listing doesn't hold runs
    nothing: each change is refused."""
    from moxi import aio

    from .page import current

    shell = current()
    body = call.body
    module, keys, values = body["module"], body["keys"], body["values"]
    resets, was = body["resets"], body["was"]
    if not len(keys) == len(values) == len(resets) == len(was):
        raise BadRequest("keys", "keys, values, resets and was differ in length")
    listing, _ = await _listing(shell)
    if module not in section_modules(listing):
        results = [_result(key, "refused", problem=f"is not a setting of {module}")
                   for key in keys]
        return {"saved_at": time.time(), "results": results}
    own = next((entry for entry in listing if entry.get("name") == module), {})
    cwd, env = str(shell.layout.runtime_dir()), shell.starters.child_env()
    locked = _locked_now()
    schema = await aio.offload(lambda: read_schema(module, cwd=cwd, env=env))
    results = []
    for key, value, reset, before in zip(keys, values, resets, was, strict=True):
        if schema.keys is None:
            results.append(_result(key, "failed", problem=schema.error, fix=schema.fix))
            continue
        results.append(await aio.offload(lambda key=key, value=value, reset=reset,
                                         before=before: save_one(
            module, key, value=value, reset=reset, was=before, keys=schema.keys, cwd=cwd,
            env=env, locked=locked, own=own)))
    return {"saved_at": time.time(), "results": results}


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
    Route("/api/settings/save", "act", f"{_HERE}:save",
          body={"module": _MODULE_FIELD,
                "keys": Field("list", max=SAVE_MAX, items=_KEY_FIELD),
                "values": _JSON_FIELD, "resets": Field("list", max=SAVE_MAX,
                                                       items=Field("bool")),
                "was": _JSON_FIELD},
          returns=SAVE_SHAPE, body_cap=256 * 1024),
)
