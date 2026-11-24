"""One config file per module, born declared, read through this module.

A program changes the file through this module (`set_value`, `unset`, `add_item`,
`remove_item`), which checks each change against the schema before it writes. A person
may also edit the file by hand; one that no longer reads raises `ConfigError`, naming the
file, when it is next read.

Every value a user may set lives in its module's one config file,
`<config root>/<module>/config.toml` (see `moxi.paths`), and every key in that file is
declared in the module's schema: a `settings.toml` shipped in the module's package and
named by its announcement's `SETTINGS`. The core's own settings are declared the same
way, in `moxi/settings.toml`, and read and set as module `moxi`.

The schema
----------

Each key is a TOML table whose dotted name is the key's place in the config file. A table
with a `type` (or a `retired`) is a key; any other table groups keys. So the schema

    [backend]
    type = "enum"
    values = ["local", "remote"]
    default = "local"
    doc = "Where messages are recorded."
    read = "start"
    env = "MOXI_COMMS_BACKEND"

    [local.retain_days]
    type = "int"
    default = 30
    doc = "How many days the local record keeps a message."
    read = "start"

declares `backend` and `local.retain_days`, which the user sets as `backend = "remote"`
and `[local] retain_days = 7`. A key's fields:

    type     bool, int, float, string, enum, path, list or table
    default  the value when neither the file nor `env` sets one; for a `path`, "" means
             no value (the setting reads None and the module applies its own rule)
    doc      one line saying what the key does
    read     "start" (read once, when the module starts) or "use" (read at each use)
    values   for an enum, the strings it may take
    env      optional: the one variable that overrides the key for one process; spelled
             with the module's prefix (`MOXI_<MODULE>_`), and not on a list or a table
    needs    optional: the module the key only works with; the doctor reports a key set
             while that module is absent
    items    optional, on a list or a table: the type of each item (or entry value),
             "str", "int", "bool" or "float"; without it the module checks the contents
    retired  on a key the module has dropped: what to do now. Setting it is an error.

A `path` expands `~` and is refused when relative. A key holds no keys: declaring one
under another key's path is refused when the schema is read.

Page fields
-----------

A live key may also carry page fields, which say how the settings page shows it. Nothing
else reads them: they never change what a key is or how it is read. Each is optional:

    label      the plain words the row leads with; one line, at most 60 characters
    line       the one sentence under the row; one line, at most 160 characters
    common     a bool: true shows the key before the rest; at most seven keys of a schema
    group      the sub-head the key sits under; one line, at most 40 characters
    unit       the word after an int's or a float's field: one word or two, at most 20
               characters
    choices    on an enum, a table from each of some or all of its values to the words
               shown for it
    pick       a bool, on an enum: true shows its values as a pick list
    hurts      a change that can hurt, on a bool or a list: `when` ("on" or "off" on a
               bool, "add" or "remove" on a list), `says` and `confirm` (one line each),
               and on a bool `stays`, true when holding the value `when` names is itself
               the harm, so the warning stays while the key holds it
    terminal   on a bool turned on and off at the terminal: `on` and `off`, each the one
               command that turns it so; the page shows the value and does not change it
    elsewhere  one line naming the other page the key is edited on; the page leaves the
               key out

A retired key carries none of them.

A switch table is a `table` key with `items = "bool"`, `read = "use"`, no `env` and
`default = {}`. Each of its entries is a switch of its own, named `<table key>.<entry>`
(the entry is lower-case letters, digits, `-` and `_`), and a missing entry reads false.

Reading
-------

`load(module, schema)` returns frozen `Settings`: each live key's value and where it came
from (`default`, `file` or `env`). An `env` override wins over the file, which wins over
the default. The file is parsed again only when its size or modification time changes. A
missing file means every default. A malformed file, an unknown key, a retired key or a
value of the wrong type raises `ConfigError` naming the file, the key and the fix.
`path=` reads that file in place of the module's own.

`check_text(module, schema, text)` is the same reader over a config file's text alone: no
path, no environment overrides, no cache. It raises `ConfigError`, or returns the
`Settings` the text gives (no path; each key the text sets, typed as `load` types it, else
its default), so a module's own check can read the same values its loader reads.

`Config(module, schema)` is the same reader for a running module: `start()` raises, and
`use(key)` reads one key at the point of use, keeping the last good settings when the file
turns bad and reporting the error once per change of the file.

Changing
--------

`set_value`, `unset`, `add_item` and `remove_item` change one key of a module's config file
(what `moxi config set` and `moxi config unset` run): the key must be declared and live,
the value is coerced to the key's type, and the file is edited through
`moxi.files.edit_toml` under its lock, keeping comments and order. Before anything is
written, the whole new text is checked twice, each check given the text alone: first by
`check_text`, then by the module's own loader when its announcement names a
`CONFIG_CHECK` (`fn(text: str) -> None`). A refusal from either leaves the file as it was,
so the file never turns invalid and never holds what the module would refuse. A
`ConfigError` the module's check raises keeps its key and reason; any other exception
becomes one whose reason is `<module> refuses the file: <its first line>`. Either way the
error names the module's config file. `moxi config <module> check` runs the same two
checks over the file as it is. Every change appends one line, `{module, key, op, by,
at}`, to the core's settings history (`history()` reads it); the line never holds a
value. Each refusal raises `ConfigError` with the problem and its fix.

`set_many(module, changes, by=, base=)` sets several keys in one write: each change is
coerced and checked as `set_value` checks it, all are applied in order, and the checks run once
over the whole result, so keys that must change together (a pair with a bound between
them) change together or not at all. `draft_text(module, changes, base=)` is the same step
with no lock and no write: it returns the text `set_many` would write and the stamp of the
file it was built over, so a preview shows what a save would write, byte for byte. Given
that stamp back as `base`, either one refuses with `ConfigChanged` when the file has
changed since.

Tables
------

A module may also keep settings in a file of its own, as one TOML table whose rows the
module alone reads and checks, and declare it in its announcement's `CONFIG_TABLES`:
`{name: "package.module:NAME"}`. `moxi config` shows the table's rows after the module's
own keys, each named `<table>.<row>`, the row being everything after the first dot. A
`set` or `unset` of such a key goes to the table's hook in place of the module's config
file, with the same exit codes, `--json` answer and history line as a config key; `--add`
and `--remove` are refused there, since a page and a terminal send a row's whole value.
A table's name may not be a top-level name of the module's own schema. The hook, `NAME`
in that module, is imported only by `moxi config`, and is an object with:

    title    one line naming the table
    line     one line said once above its rows
    applies  one line: when a change of a row reaches what reads it
    file(env) -> Path
             the file that holds the table
    keys(env) -> list[dict]
             one dict per row the hook offers, in `moxi config <module> show --json`'s
             shape and named by the row alone; a row may add `warning` and `refused`
             (one line each: the module's words for a value some or all of its uses
             refuse) and `clear_only` (true on a row that can only be removed). Raises
             `ConfigError` when the file can't be read.
    change(row, op, value, *, by, env) -> (changed, warnings)
             `op` is "set" (`value` as the command line spells it) or "unset"; returns
             whether the file changed and the module's warning lines for what it holds
             now. Raises `ConfigError` for a refusal, keyed by the row; a held lock
             (`moxi.files.LockTimeout`) and a file that is not TOML (a `ValueError` from
             `moxi.files.edit_toml`) pass through, and `change_table` answers them as a
             config file's.

`table_hooks(found)` imports a module's hooks, `table_of(module, key)` names the table a
key is a row of, and `change_table(module, key, op, value, by=)` makes one change.

This module imports only what is already loaded when Python starts; everything else it
needs is imported inside the function that needs it, so a hook may read config through it.
"""

from __future__ import annotations

import _thread
import os
import sys

TYPES = ("bool", "int", "float", "string", "enum", "path", "list", "table")
READS = ("start", "use")
#: The fields a key may carry for the settings page; nothing else reads them (see "Page
#: fields" above).
PAGE_FIELDS = ("label", "line", "common", "group", "unit", "choices", "pick", "hurts",
               "terminal", "elsewhere")
FIELDS = ("type", "default", "doc", "read", "values", "env", "retired", "needs",
          "items") + PAGE_FIELDS
#: The most keys one schema may mark `common`.
COMMON_MAX = 7
#: The item types a `list` or `table` key may declare as its `items`.
ITEMS = ("str", "int", "bool", "float")

DEFAULT = "default"
FILE = "file"
ENV = "env"

#: The history of every change `set_value`, `unset`, `add_item` and `remove_item` make, in the
#: core's state directory.
HISTORY = "settings.jsonl"
#: How long a change waits for the config file's lock, in seconds.
LOCK_TIMEOUT = 10.0

_MappingProxy = type(type.__dict__)
_DIGITS = "0123456789"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_KEY_CHARS = frozenset(_LOWER + _LOWER.upper() + _DIGITS + "_-")
_ENTRY_CHARS = frozenset(_LOWER + _DIGITS + "_-")
_ENV_CHARS = frozenset(_LOWER.upper() + _DIGITS + "_")
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


class ConfigError(Exception):
    """A config file, a schema or a change that cannot be made. Names where, the key and
    the fix.

    `exit_code` is what a command that stops on it exits with: 2 (a bad key or value)
    unless the refusal says otherwise, 3 for a module that is absent, broken or has no
    settings, 75 when the file stays locked or has changed since it was read (try again).
    """

    exit_code = 2

    def __init__(self, file, key, problem, fix):
        self.file = None if file is None else str(file)
        self.key = key
        self.problem = problem
        self.fix = fix
        where = self.file or "config"
        if key:
            where += f": key {key!r}"
        super().__init__(f"{where}: {problem}; fix: {fix}")


class SchemaError(ConfigError):
    """A module's schema that does not hold to the format. The module's own bug."""


class ConfigChanged(ConfigError):
    """A config file whose bytes are no longer those a draft was built over: someone
    changed it since it was read. Nothing was written; read it again and redo the change.
    Its `exit_code` is 75, as a held lock's is, and only this refusal is of this type."""

    exit_code = 75


class _Frozen:
    """A record whose attributes are set once, at construction."""

    __slots__ = ()

    def __setattr__(self, name, value):
        raise AttributeError(f"cannot assign to field {name!r} of a "
                             f"{type(self).__name__}")

    def __delattr__(self, name):
        raise AttributeError(f"cannot delete field {name!r} of a {type(self).__name__}")

    def _fields(self):
        return tuple(getattr(self, name) for name in self.__slots__)

    def __eq__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._fields() == other._fields()

    def __hash__(self):
        return hash(self._fields())

    def __repr__(self):
        inner = ", ".join(f"{name}={getattr(self, name)!r}" for name in self.__slots__)
        return f"{type(self).__name__}({inner})"

    def __reduce__(self):
        return (_rebuilt, (type(self), self._fields()))


def _rebuilt(cls, fields):
    """A `_Frozen` record of class `cls` holding `fields`, in its slots' order: how a copy
    or an unpickled record is made, whatever its constructor takes by keyword only."""
    record = cls.__new__(cls)
    for name, value in zip(cls.__slots__, fields):
        object.__setattr__(record, name, value)
    return record


class Key(_Frozen):
    """One declared key. Its page fields are None when the schema does not give them."""

    __slots__ = ("name", "type", "default", "doc", "read", "values", "env", "retired",
                 "needs", "items") + PAGE_FIELDS

    def __init__(self, name, value_type, default, doc, read, values=None, env=None,
                 retired=None, needs=None, items=None, *, label=None, line=None,
                 common=None, group=None, unit=None, choices=None, pick=None, hurts=None,
                 terminal=None, elsewhere=None):
        for field, value in zip(self.__slots__, (name, value_type, default, doc, read, values,
                                                 env, retired, needs, items, label, line,
                                                 common, group, unit, choices, pick, hurts,
                                                 terminal, elsewhere)):
            object.__setattr__(self, field, value)


def is_switch_table(key: Key) -> bool:
    """True when `key` is a switch table: a `table` of bools, read at use, with no `env`
    and an empty default. Each of its entries is a switch."""
    return (key.retired is None and key.type == "table" and key.items == "bool"
            and key.read == "use" and key.env is None and key.default == {})


def is_entry_name(entry: object) -> bool:
    """True when `entry` is spelled as a switch table's entry: lower-case ASCII letters,
    digits, `-` and `_`."""
    return isinstance(entry, str) and bool(entry) and _ENTRY_CHARS.issuperset(entry)


class Schema:
    """A module's declared keys, by dotted name, retired ones included."""

    def __init__(self, keys, source: str = "settings.toml"):
        self.keys = _MappingProxy(dict(keys))
        self.source = source
        # Every dotted prefix of a key is a group the file may hold as a table.
        self._groups = frozenset(".".join(k.split(".")[:i])
                                 for k in self.keys for i in range(1, k.count(".") + 1))

    def __repr__(self) -> str:
        return f"Schema({len(self.keys)} keys from {self.source})"

    def live(self):
        """Every key that is not retired, in declared order."""
        return (key for key in self.keys.values() if key.retired is None)

    @classmethod
    def parse(cls, text: str, *, source: str = "settings.toml") -> Schema:
        import tomllib

        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise SchemaError(source, None, f"is not valid TOML ({exc})",
                              "correct the schema's TOML") from None
        keys: dict = {}
        _walk_schema(data, "", source, keys)
        common = [key.name for key in keys.values() if key.common]
        if len(common) > COMMON_MAX:
            raise SchemaError(source, None, f"marks {len(common)} keys common "
                              f"({', '.join(common)}), more than {COMMON_MAX}",
                              f"mark at most {COMMON_MAX} keys common")
        return cls(keys, source)

    @classmethod
    def from_path(cls, path) -> Schema:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise SchemaError(path, None, f"cannot be read ({exc})",
                              "reinstall the module") from None
        return cls.parse(text, source=str(path))

    @classmethod
    def from_resource(cls, package: str, resource: str) -> Schema:
        from importlib.resources import files

        source = f"{package}/{resource}"
        try:
            text = files(package).joinpath(resource).read_text(encoding="utf-8")
        except (OSError, ModuleNotFoundError, UnicodeDecodeError) as exc:
            raise SchemaError(source, None, f"cannot be read ({exc})",
                              "reinstall the module") from None
        return cls.parse(text, source=source)

    def check_env(self, module: str) -> None:
        """Refuse an `env` override not spelled with `module`'s prefix, or one that would
        shadow a root locator or another key's override."""
        from . import names
        from .paths import LOCATORS

        prefix = names.env_prefix(module)
        locators = {f"{prefix}{suffix}" for suffix in LOCATORS}
        seen: dict = {}
        for key in self.live():
            if key.env is None:
                continue
            if not key.env.startswith(prefix) or key.env == prefix:
                raise SchemaError(self.source, key.name,
                                  f"env {key.env!r} is not spelled with the module's "
                                  f"prefix {prefix}", f"name it {prefix}<NAME>")
            if key.env in locators:
                raise SchemaError(self.source, key.name,
                                  f"env {key.env!r} is a root locator of moxi.paths",
                                  "choose another name")
            if key.env in seen:
                raise SchemaError(self.source, key.name,
                                  f"env {key.env!r} already overrides {seen[key.env]!r}",
                                  "give each key its own variable")
            seen[key.env] = key.name

    def is_group(self, dotted: str) -> bool:
        return dotted in self._groups

    def entry(self, name: str):
        """`(table key, entry)` when `name` is `<table key>.<entry>` for a switch table
        of this schema, else None. A name that is itself a key is never an entry."""
        if name in self.keys:
            return None
        table, dot, entry = name.rpartition(".")
        key = self.keys.get(table) if dot else None
        if key is None or not is_switch_table(key) or not is_entry_name(entry):
            return None
        return table, entry


def schema_for(found) -> Schema | None:
    """The schema a loaded module's announcement names, or None when it names none.

    `found` is a `moxi.modules.Found` in the `present` state.
    """
    if found.settings is None:
        return None
    if not found.package:
        raise SchemaError(found.target, None, "the announcement is not in a package, so "
                          "SETTINGS names nothing", "move the announcement into the "
                          "module's package")
    return Schema.from_resource(found.package, found.settings)


_core: list = []


def _core_schema() -> Schema:
    """The core's own schema, `moxi/settings.toml`, read once."""
    if not _core:
        _core.append(Schema.from_path(os.path.join(os.path.dirname(__file__),
                                                   "settings.toml")))
    return _core[0]


class Settings(_Frozen):
    """A module's settings at one reading: every live key's value and its source.

    A switch table's entry reads by its name, `settings["rows.backup"]`: its bool, or
    false when the table holds no such entry.
    """

    __slots__ = ("module", "file", "schema", "values", "sources")
    __hash__ = None

    def __init__(self, module, file, schema, values, sources):
        for field, value in zip(self.__slots__, (module, file, schema, values, sources)):
            object.__setattr__(self, field, value)

    def __getitem__(self, key: str):
        try:
            return self.values[key]
        except KeyError:
            pass
        hit = self.schema.entry(key)
        if hit is not None and hit[0] in self.values:
            return self.values[hit[0]].get(hit[1], False)
        raise KeyError(f"{self.module} declares no setting {key!r}") from None

    def __contains__(self, key: object) -> bool:
        if key in self.values:
            return True
        hit = self.schema.entry(key) if isinstance(key, str) else None
        return hit is not None and hit[0] in self.values

    def __iter__(self):
        return iter(self.values)

    def source(self, key: str) -> str:
        if key in self.values:
            return self.sources[key]
        self[key]
        table, entry = self.schema.entry(key)
        if self.sources[table] == FILE and entry in self.values[table]:
            return FILE
        return DEFAULT


#: The environment `check_text` reads paths with: none of its own, so no override applies
#: (a `~` still expands to the user's home).
_NO_ENV = _MappingProxy({})


def check_text(module: str, schema: Schema, text: str) -> Settings:
    """Check config text `text` of module `module` as `load` would read it, but from the
    text alone: no path, no environment overrides. Raises `ConfigError`; an error names no
    file. Returns the `Settings` the text gives: `file` None, each key the text sets as
    `load` types it (a path's `~` expanded to the user's home), else its default."""
    return _read_text(module, schema, text, None)


def _read_text(module, schema, text, path) -> Settings:
    """`check_text`, with each error naming `path`."""
    import tomllib

    schema.check_env(module)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(path, None, f"is not valid TOML ({exc})",
                          "correct the file's TOML syntax") from None
    return _settings(module, schema, path, data, _NO_ENV)


def load(module: str, schema: Schema, *, env=None, path=None) -> Settings:
    """Read module `module`'s config file against `schema`. Raises `ConfigError`.

    `path` names the file to read in place of the module's own (the same format, read
    the same way); `env` is the environment the overrides and roots come from.
    """
    from pathlib import Path

    effective_env = os.environ if env is None else env
    if path is None:
        from .paths import _layout

        path = _layout(module).config_file(effective_env)
    else:
        path = Path(path)
    return _settings(module, schema, path, _parsed(path), effective_env)


class Config:
    """The reader a running module keeps: start once, then read keys at their use."""

    def __init__(self, module: str, schema: Schema, *, env=None, path=None, report=None):
        from . import names

        self.module = names.check_module_name(module)
        self.schema = schema
        self._env = env
        self._path = path
        self._report = report or _report_to_stderr
        self._good = None
        self._reported = None
        self._lock = _thread.allocate_lock()

    def start(self) -> Settings:
        """Read the settings at start. A `ConfigError` here refuses the start."""
        settings = load(self.module, self.schema, env=self._env, path=self._path)
        with self._lock:
            self._good = settings
            self._reported = None
        return settings

    def current(self) -> Settings:
        """The settings now, or the last good ones while the file is in error."""
        with self._lock:
            if self._good is None:
                raise RuntimeError("Config.current() before start()")
            try:
                self._good = load(self.module, self.schema, env=self._env,
                                  path=self._path)
                self._reported = None
            except ConfigError as exc:
                stamp = _stamp(self._good.file)
                if stamp != self._reported:
                    self._reported = stamp
                    self._report(exc)
            return self._good

    def use(self, key: str):
        """One key's value at its point of use."""
        return self.current()[key]


# -- reading the file ------------------------------------------------------------------

_cache: dict = {}
_cache_lock = _thread.allocate_lock()


def _stamp(path) -> tuple | None:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return ("error", str(exc))
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def _parsed(path) -> dict | None:
    """The file's TOML, parsed again only when its size or modification time changed."""
    stamp = _stamp(path)
    if stamp is None:
        return None
    with _cache_lock:
        hit = _cache.get(path)
    if hit is not None and hit[0] == stamp:
        result = hit[1]
    else:
        import tomllib

        try:
            with open(path, "rb") as fh:
                result = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            result = ConfigError(path, None, f"is not valid TOML ({exc})",
                                 "correct the file's TOML syntax")
        except (OSError, UnicodeDecodeError) as exc:
            result = ConfigError(path, None, f"cannot be read ({exc})",
                                 "make the file a readable TOML file, or remove it")
        with _cache_lock:
            _cache[path] = (stamp, result)
    if isinstance(result, ConfigError):
        raise result
    return result


def _settings(module, schema, path, data, env) -> Settings:
    schema.check_env(module)
    from_file: dict = {}
    if data is not None:
        _walk_file(data, "", schema, path, from_file, env)
    values: dict = {}
    sources: dict = {}
    for key in schema.live():
        raw = env.get(key.env) if key.env else None
        if raw:
            values[key.name] = _from_env(key, raw, env)
            sources[key.name] = ENV
        elif key.name in from_file:
            values[key.name] = _copied(from_file[key.name])
            sources[key.name] = FILE
        else:
            values[key.name] = _default_value(key, env)
            sources[key.name] = DEFAULT
    return Settings(module, path, schema, _MappingProxy(values), _MappingProxy(sources))


def _copied(value):
    """A copy of a value read from TOML, so no two readings share a list or table."""
    if isinstance(value, list):
        return [_copied(v) for v in value]
    if isinstance(value, dict):
        return {k: _copied(v) for k, v in value.items()}
    return value


def _walk_file(table, prefix, schema, path, out, env) -> None:
    fix_unknown = "remove it, or check its spelling against the module's schema"
    for name, value in table.items():
        dotted = f"{prefix}{name}"
        key = schema.keys.get(dotted)
        if key is not None:
            if key.retired is not None:
                raise ConfigError(path, dotted, "is retired", key.retired)
            out[dotted] = _check_value(key, value, path, env)
        elif schema.is_group(dotted):
            if not isinstance(value, dict):
                raise ConfigError(path, dotted, "is a table of settings, not a value",
                                  f"write it as [{dotted}] with its keys under it")
            _walk_file(value, f"{dotted}.", schema, path, out, env)
        else:
            raise ConfigError(path, dotted, "is not a setting of this module", fix_unknown)


def _item_ok(kind, value) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, str)


def _check_value(key, value, path, env):
    """`value` as key `key`'s type, or `ConfigError`."""
    kind = key.type
    ok = True
    if kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "float":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        value = float(value) if ok else value
    elif kind in ("string", "enum", "path"):
        ok = isinstance(value, str)
    elif kind == "list":
        ok = isinstance(value, list)
    elif kind == "table":
        ok = isinstance(value, dict)
    if not ok:
        raise ConfigError(path, key.name, f"is {_toml_kind(value)}, not {_a(kind)}",
                          f"set it to {_a(kind)}{_enum_hint(key)}")
    if kind == "enum" and value not in key.values:
        raise ConfigError(path, key.name, f"is {value!r}, which it cannot be",
                          f"set it to one of {', '.join(map(repr, key.values))}")
    if kind == "path":
        return _path(key, value, path, env)
    if key.items is not None and kind in ("list", "table"):
        return _check_items(key, value, path)
    return value


def _check_items(key, value, path):
    kind = key.items
    if isinstance(value, list):
        for i, item in enumerate(value):
            if not _item_ok(kind, item):
                raise ConfigError(path, key.name, f"item {i + 1} is {_toml_kind(item)}, "
                                  f"not {_a(kind)}", f"make every item {_a(kind)}")
        return [float(v) for v in value] if kind == "float" else value
    for entry, item in value.items():
        if not _item_ok(kind, item):
            what = "switch" if is_switch_table(key) else "entry"
            raise ConfigError(path, key.name, f"{what} {entry!r} is {_toml_kind(item)}, "
                              f"not {_a(kind)}", f"set every entry to {_a(kind)}")
    return {k: float(v) for k, v in value.items()} if kind == "float" else value


def _path(key, value, source, env):
    from pathlib import Path

    from .paths import _expanduser

    if value == "":
        return None
    # `check_text` reads no environment of its own, but its `~` is still this process's
    # home, as `load` without an `env` expands it.
    expanded = Path(_expanduser(value, os.environ if env is _NO_ENV else env))
    if not expanded.is_absolute():
        raise ConfigError(source, key.name, f"is the relative path {value!r}",
                          "give an absolute path, or one starting with ~")
    return expanded


def _from_env(key, raw, env):
    where = f"environment variable {key.env}"
    kind = key.type
    if kind == "bool":
        if raw.lower() in _TRUE:
            return True
        if raw.lower() in _FALSE:
            return False
        raise ConfigError(where, key.name, f"is {raw!r}, not a bool",
                          "set it to 1, true, yes or on, or 0, false, no or off")
    if kind in ("int", "float"):
        try:
            return int(raw) if kind == "int" else float(raw)
        except ValueError:
            raise ConfigError(where, key.name, f"is {raw!r}, not {_a(kind)}",
                              f"set it to {_a(kind)}") from None
    return _check_value(key, raw, where, env)


def _default_value(key, env):
    if key.type == "path":
        return _path(key, key.default, "schema", env)
    if key.type in ("list", "table"):
        return _copied(key.default)
    return key.default


def _toml_kind(value) -> str:
    if isinstance(value, bool):
        return "a bool"
    if isinstance(value, int):
        return "an int"
    if isinstance(value, float):
        return "a float"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a table"
    return f"a {type(value).__name__}"


def _a(kind) -> str:
    return {"int": "an int", "enum": "an enum string",
            "str": "a string"}.get(kind or "", f"a {kind}")


def _enum_hint(key) -> str:
    return f" ({', '.join(map(repr, key.values))})" if key.values else ""


def _report_to_stderr(exc) -> None:
    print(f"moxi: {exc}; keeping the last good settings", file=sys.stderr)


# -- reading the schema ----------------------------------------------------------------

def _is_bare(name) -> bool:
    return bool(name) and _KEY_CHARS.issuperset(name)


def _walk_schema(table, prefix, source, out) -> None:
    for name, value in table.items():
        dotted = f"{prefix}{name}"
        if not _is_bare(name):
            raise SchemaError(source, dotted, "is not a bare TOML key",
                              "use letters, digits, - and _ only")
        if not isinstance(value, dict):
            raise SchemaError(source, dotted, "is a value, not a key's table",
                              "declare it as a table with type, default, doc and read")
        if isinstance(value.get("type"), str) or isinstance(value.get("retired"), str):
            out[dotted] = _key(dotted, value, source)
        else:
            if not value:
                raise SchemaError(source, dotted, "is an empty table",
                                  "declare a key in it, or remove it")
            _walk_schema(value, f"{dotted}.", source, out)


def _key(name, spec, source) -> Key:
    def bad(problem: str, fix: str) -> SchemaError:
        return SchemaError(source, name, problem, fix)

    under = sorted(k for k, v in spec.items() if k not in FIELDS and isinstance(v, dict))
    if under:
        what = ("a switch table, whose entries are set in the config file"
                if spec.get("type") == "table" and spec.get("items") == "bool"
                else "a key, which holds no keys")
        raise bad(f"declares {', '.join(f'{name}.{k}' for k in under)} under it, but it "
                  f"is {what}", "declare the inner key elsewhere, or remove it")
    unknown = sorted(frozenset(spec) - frozenset(FIELDS))
    if unknown:
        raise bad(f"has unknown field(s) {', '.join(unknown)}",
                  f"use only {', '.join(FIELDS)}")
    retired = spec.get("retired")
    if retired is not None:
        if not isinstance(retired, str) or not retired.strip():
            raise bad("has an empty retired", "say what to do now instead")
        shown = [field for field in PAGE_FIELDS if field in spec]
        if shown:
            raise bad(f"is retired but has {', '.join(shown)}",
                      "remove the page fields; a retired key is not shown")
        return Key(name=name, value_type=spec.get("type"), default=spec.get("default"),
                   doc=spec.get("doc", ""), read=spec.get("read"), retired=retired)

    for required in ("type", "default", "doc", "read"):
        if required not in spec:
            raise bad(f"lacks {required}", f"give it a {required}")
    kind, doc, read = spec["type"], spec["doc"], spec["read"]
    if kind not in TYPES:
        raise bad(f"has type {kind!r}", f"use one of {', '.join(TYPES)}")
    if not isinstance(doc, str) or not doc.strip() or "\n" in doc:
        raise bad("has no one-line doc", "say in one line what the key does")
    if read not in READS:
        raise bad(f"has read {read!r}", "use \"start\" or \"use\"")

    values = spec.get("values")
    if kind == "enum":
        if (not isinstance(values, list) or not values
                or not all(isinstance(v, str) for v in values)
                or len(frozenset(values)) != len(values)):
            raise bad("is an enum without values", "list the strings it may take")
        values = tuple(values)
    elif values is not None:
        raise bad("has values but is not an enum", "remove values")

    env = spec.get("env")
    if env is not None:
        if (not isinstance(env, str) or not env or not env[0].isupper()
                or not _ENV_CHARS.issuperset(env)):
            raise bad(f"has env {env!r}", "name one variable in upper case")
        if kind in ("list", "table"):
            raise bad(f"has env on a {kind}", "remove env; a list or table is set in "
                                              "the file")

    needs = spec.get("needs")
    if needs is not None:
        from . import names

        if not names.is_module_name(needs):
            raise bad(f"has needs {needs!r}", "name the one module the key works with")

    items = spec.get("items")
    if items is not None:
        if kind not in ("list", "table"):
            raise bad(f"has items but is {_a(kind)}", "remove items; only a list or a "
                                                      "table declares its items")
        if items not in ITEMS:
            raise bad(f"has items {items!r}", f"use one of {', '.join(ITEMS)}")

    key = Key(name=name, value_type=kind, default=spec["default"], doc=doc, read=read,
              values=values, env=env, needs=needs, items=items,
              **_page_fields(spec, kind, values, bad))
    try:
        _check_value(key, key.default, source, os.environ)
    except ConfigError as exc:
        raise bad(f"has a default that {exc.problem}", exc.fix) from None
    return key


#: The longest each one-line page field may be, in characters (None: no limit but one line).
_PAGE_TEXT = {"label": 60, "line": 160, "group": 40, "unit": 20, "elsewhere": None}
#: The direction of change a `hurts` may name, by the key's type.
_HURTS_WHEN = {"bool": ("on", "off"), "list": ("add", "remove")}


def _one_line(value) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and "\n" not in value
            and "\r" not in value)


def _page_fields(spec, kind, values, bad) -> dict:
    """The page fields `spec` gives a live key of type `kind`, checked; `bad(problem, fix)`
    makes the refusal."""
    found: dict = {}
    for field, most in _PAGE_TEXT.items():
        if field not in spec:
            continue
        text = spec[field]
        if not _one_line(text):
            raise bad(f"has {field} {text!r}, which is not one line of text",
                      f"give {field} as one line")
        if most is not None and len(text) > most:
            raise bad(f"has a {field} of {len(text)} characters, more than {most}",
                      f"shorten {field} to {most} characters or fewer")
        found[field] = text
    if "unit" in found:
        if kind not in ("int", "float"):
            raise bad(f"has unit but is {_a(kind)}",
                      "remove unit; only an int or a float has a unit")
        if len(found["unit"].split()) > 2:
            raise bad(f"has the unit {found['unit']!r}, more than two words",
                      "give the unit in one word or two")
    for field in ("common", "pick"):
        if field in spec:
            if not isinstance(spec[field], bool):
                raise bad(f"has {field} {spec[field]!r}, not a bool",
                          f"set {field} to true or false")
            found[field] = spec[field]
    if "pick" in found and kind != "enum":
        raise bad(f"has pick but is {_a(kind)}", "remove pick; only an enum has a pick list")
    if "choices" in spec:
        found["choices"] = _choices(spec["choices"], kind, values, bad)
    if "hurts" in spec:
        found["hurts"] = _hurts(spec["hurts"], kind, bad)
    if "terminal" in spec:
        found["terminal"] = _terminal(spec["terminal"], kind, bad)
    return found


def _choices(choices, kind, values, bad) -> dict:
    if kind != "enum":
        raise bad(f"has choices but is {_a(kind)}",
                  "remove choices; only an enum's values have words")
    if not isinstance(choices, dict) or not choices:
        raise bad("has choices that are not a table of words",
                  "give choices as a table from each value to its words")
    for value, words in choices.items():
        if value not in values:
            raise bad(f"has choices for {value!r}, which is not one of its values",
                      f"name only its values: {', '.join(map(repr, values))}")
        if not _one_line(words):
            raise bad(f"has choices {words!r} for {value!r}, which is not one line of "
                      "text", "give each value's words as one line")
    return dict(choices)


def _hurts(hurts, kind, bad) -> dict:
    if kind not in _HURTS_WHEN:
        raise bad(f"has hurts but is {_a(kind)}",
                  "remove hurts; only a change of a bool or a list can hurt")
    fields = ("when", "says", "confirm", "stays")
    if not isinstance(hurts, dict):
        raise bad("has hurts that is not a table", f"give hurts as a table of "
                  f"{', '.join(fields)}")
    unknown = sorted(frozenset(hurts) - frozenset(fields))
    if unknown:
        raise bad(f"has unknown hurts field(s) {', '.join(unknown)}",
                  f"use only {', '.join(fields)}")
    wanted = _HURTS_WHEN[kind]
    if hurts.get("when") not in wanted:
        raise bad(f"has hurts.when {hurts.get('when')!r}",
                  f"on {_a(kind)} give {' or '.join(map(repr, wanted))}")
    for field in ("says", "confirm"):
        if not _one_line(hurts.get(field)):
            raise bad(f"has hurts.{field} {hurts.get(field)!r}, which is not one line of "
                      "text", f"give hurts.{field} as one line")
    stays = hurts.get("stays", False)
    if not isinstance(stays, bool):
        raise bad(f"has hurts.stays {stays!r}, not a bool",
                  "set hurts.stays to true or false")
    if stays and kind != "bool":
        raise bad(f"has hurts.stays but is {_a(kind)}",
                  "remove hurts.stays; only a bool's value held can hurt")
    return {"when": hurts["when"], "says": hurts["says"], "confirm": hurts["confirm"],
            "stays": stays}


def _terminal(terminal, kind, bad) -> dict:
    if kind != "bool":
        raise bad(f"has terminal but is {_a(kind)}",
                  "remove terminal; only a bool is turned on and off at the terminal")
    if not isinstance(terminal, dict) or set(terminal) != {"on", "off"}:
        raise bad("has a terminal that is not a table of on and off",
                  "give terminal the command that turns it on and the one that turns "
                  "it off: { on = \"...\", off = \"...\" }")
    for state in ("on", "off"):
        if not _one_line(terminal[state]):
            raise bad(f"has terminal.{state} {terminal[state]!r}, which is not one line "
                      "of text", f"give terminal.{state} as one command on one line")
    return {"on": terminal["on"], "off": terminal["off"]}


# -- changing a module's settings ------------------------------------------------------

def set_value(module: str, key: str, value, *, by: str) -> list:
    """Set `key` of module `module`'s config file to `value`, coerced to the key's type.

    A switch table's entry is set by its name, `<table key>.<entry>`, to a bool. When
    `key` is a daemon's switch, the units of the daemons it switches are then planned and
    applied (`apply_units`), and their results returned; otherwise the list is empty.
    Raises `ConfigError`.
    """
    _change(module, key, "set", value, by=by)
    return apply_units(module, key)


def unset(module: str, key: str, *, by: str) -> list:
    """Remove `key` from module `module`'s config file, so it reads its default (a switch
    table's entry then reads false). Returns the units applied, as `set_value` does. A key the
    file does not hold changes nothing: no file is made or written and no history line is
    added, though the file is still checked as it is (a missing one as the empty text)."""
    _change(module, key, "unset", None, by=by)
    return apply_units(module, key)


def apply_units(module: str, key: str, *, say=print) -> list:
    """After a `set_value` or `unset` of `key`: when it is the switch of one or more of the
    module's daemons (a `DAEMONS` entry, or one `UNITS_FROM` returns), or the key a socket
    daemon's `start` names (`{"login": key}`), plan and apply those daemons' units and
    nothing else of the module, under the core lock, and return
    the `moxi.units.Applied` list. A key that is a switch table counts as each of its
    entries. Called after the config file's lock is let go, so that lock is never held
    while the core lock is awaited. The core's own settings switch no daemon."""
    from . import names

    if module == names.HARNESS:
        return []
    from . import units

    return units.apply_switch(module, key, say=say)


def add_item(module: str, key: str, item, *, by: str) -> None:
    """Append `item` to list key `key`, starting from the key's value now. An item the
    list already holds is not added again. Raises `ConfigError`."""
    _change(module, key, "add", item, by=by)


def remove_item(module: str, key: str, item, *, by: str) -> None:
    """Remove every item equal to `item` from list key `key`. Raises `ConfigError` when
    the list holds none."""
    _change(module, key, "remove", item, by=by)


def set_many(module: str, changes, *, by: str, base: str | None = None) -> list:
    """Set several keys of module `module`'s config file in one write.

    `changes` is a sequence of `(key, value)` pairs, each taken as `set_value` takes it and
    applied in order (a later change of a key wins). The checks `set_value` runs over the new
    text run once, over the text with every change made, so two keys bound to each other
    can be moved together where either alone would be refused. Under the file's lock,
    when `base` is given and the file's stamp (see `draft_text`) is no longer `base`, it
    raises `ConfigChanged`. Any refusal writes nothing; otherwise the text is written
    once, and one history line is added per key changed, each naming `by`. Returns the
    units applied for each key, one list, as `set_value` returns them. No changes write nothing
    and make no file, though the file is still read and checked as `draft_text` does.
    Raises `ConfigError`; a refusal of the whole text, not of one value, names no key
    unless the module's check names one.
    """
    _need_by(by, None)
    changes = _pairs(changes)
    if not changes:
        draft_text(module, changes, base=base)
        return []
    from . import files

    path = _config_file(module)
    schema, check, steps = _resolved(module, changes, path)
    named = _named(changes)
    try:
        with files.edit_toml(path, timeout_s=LOCK_TIMEOUT) as doc:
            # The lock is held, so the bytes read here are the bytes `doc` was parsed from.
            if base is not None and _stamp_of(_raw(path)) != base:
                raise _changed(path)
            _drafted(module, schema, check, steps, doc, path, named)
    except (files.LockTimeout, ValueError) as exc:
        raise _edit_refused(exc, path, named) from None
    keys = list(dict.fromkeys(key for key, _ in changes))
    _record_changes(module, keys, "set", by)
    return [applied for key in keys for applied in apply_units(module, key)]


def draft_text(module: str, changes, *, base: str | None = None) -> tuple:
    """The text `set_many(module, changes, base=base)` would write now, and the stamp of
    the file it was built over: `(text, stamp)`.

    The file is read as `set_many` reads it (a missing file is the empty text), the same
    changes are made and the same checks run, so for the same file the text is what
    `set_many` writes, byte for byte, and a draft it refuses is one `set_many` refuses.
    Nothing is written, no lock is taken and no file or folder is made. The stamp is the
    SHA-256 hex digest of the file's bytes (of no bytes for a missing file); with `base`
    given and the stamp now different, it raises `ConfigChanged`. Raises `ConfigError`.
    """
    from ._vendor import tomlkit

    changes = _pairs(changes)
    path = _config_file(module)
    schema, check, steps = _resolved(module, changes, path)
    named = _named(changes)
    raw = _raw(path)
    try:
        doc = tomlkit.document() if raw is None else tomlkit.parse(_decoded(raw))
        stamp = _stamp_of(raw)
        if base is not None and stamp != base:
            raise _changed(path)
        text = _drafted(module, schema, check, steps, doc, path, named)
    except ValueError as exc:
        raise _edit_refused(exc, path, named) from None
    return text, stamp


def history(module: str, key: str | None = None, *, tail: int | None = 200) -> list:
    """The last `tail` changes of module `module`'s settings (of `key` alone, when given),
    oldest first: `{module, key, op, by, at}` each. `tail=None` gives them all."""
    from . import files

    records = [r for r in files.read_records(_history_file())
               if r.get("module") == module and (key is None or r.get("key") == key)]
    return records if tail is None else records[-tail:] if tail > 0 else []


def _config_file(module: str):
    """The path of module `module`'s config file (the core's own for `moxi`)."""
    from .paths import _layout

    return _layout(module).config_file()


def _history_file():
    from .paths import core

    return core().state_dir() / HISTORY


def _schema_of(module: str) -> tuple:
    """The schema a change of module `module` is checked against, with the module's
    `CONFIG_CHECK` target or None: the core's schema for `moxi`, else the module's own,
    read through `moxi.modules.load`."""
    from . import modules, names

    if module == names.HARNESS:
        return _core_schema(), None
    if not names.is_module_name(module):
        raise _refused(module, f"{module!r} is not a module name",
                       "name a module that `moxi modules` lists", 2)
    found = modules.load(module)
    if found.state != modules.PRESENT:
        raise _refused(module, found.reason, f"install it: moxi install {module}"
                       if found.state == modules.ABSENT else "reinstall or repair the "
                       "module", 3)
    try:
        schema = schema_for(found)
    except SchemaError as exc:
        exc.exit_code = 3
        raise
    if schema is None:
        raise _refused(module, "declares no settings", "nothing to set", 3)
    return schema, found.config_check


def _refused(where, problem, fix, code, key=None) -> ConfigError:
    exc = ConfigError(where, key, problem, fix)
    exc.exit_code = code
    return exc


def _resolve(schema, module, key, path):
    """Step 2: the declared, live key `key` names, with the switch entry it names."""
    found = schema.keys.get(key)
    if found is not None:
        if found.retired is not None:
            raise ConfigError(path, key, "is retired", found.retired)
        return found, None
    hit = schema.entry(key)
    if hit is not None:
        return schema.keys[hit[0]], hit[1]
    import difflib

    live = [k.name for k in schema.live()]
    near = difflib.get_close_matches(key, live, n=1)
    fix = (f"did you mean {near[0]!r}?" if near
           else f"`moxi config {module}` lists its settings")
    raise ConfigError(path, key, f"is not a setting of {module}", fix)


def _scalar(kind, key, value, path, values=None):
    """`value` as one value of type `kind`, by the command line's rules: a string is read
    as the type (true/false, yes/no, on/off and 1/0 for a bool), a value already of the
    type is kept, a number is a string's text. Else `ConfigError`."""
    def refuse(fix):
        return ConfigError(path, key, f"cannot be {value!r}, which is not {_a(kind)}", fix)

    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in _TRUE + _FALSE:
            return value.strip().lower() in _TRUE
        raise refuse("give true or false (yes/no, on/off and 1/0 also do)")
    if kind in ("int", "float"):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if kind == "float":
                return float(value)
            if isinstance(value, int):
                return value
        elif isinstance(value, str):
            try:
                return int(value) if kind == "int" else float(value)
            except ValueError:
                pass
        raise refuse(f"give {_a(kind)}")
    if kind in ("string", "str"):
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        raise refuse("give a string")
    if kind == "enum":
        if isinstance(value, str) and value in values:
            return value
        raise ConfigError(path, key, f"cannot be {value!r}",
                          f"give one of {', '.join(map(repr, values))}")
    if kind == "path":
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if not isinstance(value, str):
            raise refuse("give an absolute path, or one starting with ~")
        if value and not os.path.isabs(os.path.expanduser(value)):
            raise ConfigError(path, key, f"cannot be the relative path {value!r}",
                              "give an absolute path, or one starting with ~")
        return value
    raise AssertionError(kind)


def _toml_value(text, path, key, kind):
    """One TOML value given on the command line."""
    import tomllib

    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        example = "'[\"a\", \"b\"]'" if kind == "list" else "'{name = \"value\"}'"
        raise ConfigError(path, key, f"cannot be {text!r}, which is not one TOML {kind}",
                          f"give the whole {kind} as one TOML value, such as "
                          f"{example}") from None


def _coerce(target, entry, name, value, path, text):
    """Step 3 for `set_value`: `value` as key `target`'s type (or as a switch entry's bool).
    `text` is True for a value given on the command line."""
    kind = target.type
    if entry is not None:
        return _scalar("bool", name, value, path)
    if kind in ("list", "table"):
        if text and isinstance(value, str):
            value = _toml_value(value, path, name, kind)
        if kind == "list" and isinstance(value, tuple):
            value = list(value)
        if kind == "table" and not isinstance(value, dict):
            from collections.abc import Mapping

            if isinstance(value, Mapping):
                value = dict(value)
        if not isinstance(value, list if kind == "list" else dict):
            raise ConfigError(path, name, f"cannot be {_toml_kind(value)}",
                              f"give {_a(kind)}")
        if target.items is not None:
            if kind == "list":
                value = [_scalar(target.items, name, v, path) for v in value]
            else:
                value = {k: _scalar(target.items, name, v, path) for k, v in value.items()}
        if is_switch_table(target):
            bad = [k for k in value if not is_entry_name(k)]
            if bad:
                raise ConfigError(path, name, f"cannot hold the switch {bad[0]!r}",
                                  "name each switch with lower-case letters, digits, "
                                  "- and _")
        return value
    return _scalar(kind, name, value, path, target.values)


def _coerce_item(target, name, item, path, text):
    """Step 3 for `add_item` and `remove_item`: one item of list key `target`."""
    if target.type != "list":
        raise ConfigError(path, name, f"is {_a(target.type)}, not a list",
                          "add or remove items only on a list key; set it whole instead")
    if target.items is not None:
        return _scalar(target.items, name, item, path)
    if text and isinstance(item, str):
        import tomllib

        try:
            return tomllib.loads(f"v = {item}")["v"]
        except tomllib.TOMLDecodeError:
            return item
    return item


class _Unchanged(Exception):
    """Raised inside `edit_toml`'s block when a change would change nothing."""


def _need_by(by, key) -> None:
    """Refuse a change that names no one as making it."""
    if not isinstance(by, str) or not by.strip():
        raise ConfigError(None, key, "has no one named as making the change",
                          "pass by=, who makes it (cli, ui, moxi install)")


def _prepared(schema, module, key, value, path, *, text) -> tuple:
    """Steps 2 and 3 of a set: the key `key` names, and `value` coerced and checked as its
    type. Returns `(target, entry, new)`; `entry` is the switch entry `key` names, or
    None."""
    target, entry = _resolve(schema, module, key, path)
    new = _coerce(target, entry, key, value, path, text)
    if entry is None:
        _check_value(target, new, path, os.environ)
    return target, entry, new


def _put(doc, target, entry, new, path) -> None:
    """Write the value `new` of `target` (or of its switch `entry`) into `doc`."""
    parts = target.name.split(".")
    if entry is not None:
        parts.append(entry)
    node = _container(doc, parts[:-1], path, create=True)
    node[parts[-1]] = _item(new)


def _pairs(changes) -> list:
    """`changes` as a list of `(key, value)` pairs."""
    return [(key, value) for key, value in changes]


def _named(changes) -> str | None:
    """The key a refusal of the whole text names: the one key changed, else none."""
    return changes[0][0] if len(changes) == 1 else None


def _resolved(module, changes, path) -> tuple:
    """The first half of the step `set_many` and `draft_text` share: the module's schema
    and `CONFIG_CHECK`, and each change prepared as `set_value` prepares it."""
    schema, check = _schema_of(module)
    steps = [_prepared(schema, module, key, value, path, text=False)
             for key, value in changes]
    return schema, check, steps


def _drafted(module, schema, check, steps, doc, path, key) -> str:
    """The second half: every prepared change put into `doc` in order, then the new text
    checked once, whole, as a change's text is. Returns the text."""
    from ._vendor import tomlkit

    for target, entry, new in steps:
        _put(doc, target, entry, new, path)
    text = tomlkit.dumps(doc)
    _check_file(module, schema, check, text, path, key)
    return text


def _raw(path) -> bytes | None:
    """The bytes of the file `path` points to, or None when there is none."""
    try:
        with open(os.path.realpath(path), "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _decoded(raw: bytes) -> str:
    """`raw` as text, read as `moxi.files.edit_toml` reads a file: UTF-8, with each line
    ending made a newline."""
    import io

    return io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8").read()


def _stamp_of(raw: bytes | None) -> str:
    """The stamp of a file's bytes `raw`: their SHA-256 hex digest (of no bytes for
    None)."""
    import hashlib

    return hashlib.sha256(raw or b"").hexdigest()


def _changed(path) -> ConfigChanged:
    return ConfigChanged(path, None, "has changed since it was read",
                         "read it again and make the change over what it holds now")


def _edit_refused(exc, path, key) -> ConfigError:
    """The refusal for an edit of `path` that failed with `exc`: a lock held past the
    wait, a file that is not valid TOML, or a value TOML cannot hold."""
    from . import files

    if isinstance(exc, files.LockTimeout):
        who = f"pid {exc.holder}" if exc.holder else "another process"
        return _refused(path, f"is locked by {who}", "try again once it is free", 75, key)
    from ._vendor.tomlkit.exceptions import ParseError

    if isinstance(exc, ParseError):
        return ConfigError(path, None, f"is not valid TOML ({exc})",
                           "correct the file's TOML syntax")
    return ConfigError(path, key, f"cannot be written as TOML ({exc})",
                       "give a value TOML can hold")


def _record_changes(module, keys, op, by) -> None:
    """One history line per key in `keys`."""
    import time

    from . import files

    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for key in keys:
        files.append_record(_history_file(), {
            "module": module, "key": key, "op": op, "by": by, "at": at})


def _change(module, key, op, value, *, by, text=False) -> bool:
    """Make one change; returns False when there was nothing to change."""
    _need_by(by, key)
    from . import files

    schema, check = _schema_of(module)
    path = _config_file(module)
    if op == "set":
        target, entry, new = _prepared(schema, module, key, value, path, text=text)
    else:
        target, entry = _resolve(schema, module, key, path)
    if op in ("add", "remove"):
        if entry is not None:
            raise ConfigError(path, key, "is a switch, not a list",
                              "set it to true or false")
        item = _coerce_item(target, key, value, path, text)
    if op == "unset" and not os.path.lexists(path):
        # Nothing to remove and no file to make: the text is still checked, as the empty
        # text `config check` reads for a missing file.
        _check_file(module, schema, check, "", path, key)
        return False
    try:
        with files.edit_toml(path, timeout_s=LOCK_TIMEOUT) as doc:
            if op == "set":
                _drafted(module, schema, check, [(target, entry, new)], doc, path, key)
            else:
                parts = target.name.split(".")
                if entry is not None:
                    parts.append(entry)
                if op == "unset":
                    dropped = _drop(doc, parts)
                else:
                    _edit_list(doc, parts, target, op, item, path, module, key)
                from ._vendor import tomlkit

                _check_file(module, schema, check, tomlkit.dumps(doc), path, key)
                if op == "unset" and not dropped:
                    raise _Unchanged
    except _Unchanged:
        return False
    except (files.LockTimeout, ValueError) as exc:
        raise _edit_refused(exc, path, key) from None
    _record_changes(module, [key], op, by)
    return True


def _container(doc, parts, path, *, create):
    """The table at dotted path `parts` in `doc`, made when `create` and missing."""
    from ._vendor import tomlkit

    node = doc
    for i, part in enumerate(parts):
        if part not in node:
            if not create:
                return None
            node[part] = tomlkit.table()
        node = node[part]
        if not isinstance(node, dict):
            raise ConfigError(path, ".".join(parts[:i + 1]),
                              "is a value in the file, not a table",
                              "correct the file by hand, then try again")
    return node


def _drop(doc, parts) -> bool:
    """Delete the dotted key `parts` from `doc`; False when `doc` does not hold it."""
    node = doc
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if isinstance(node, dict) and parts[-1] in node:
        del node[parts[-1]]
        return True
    return False


def _plain(value):
    unwrap = getattr(value, "unwrap", None)
    return unwrap() if unwrap is not None else value


def _same(a, b) -> bool:
    """Equal and of one type, so that the item 1 is not the item true."""
    return type(a) is type(b) and a == b


def _edit_list(doc, parts, target, op, item, path, module, key) -> None:
    node = _container(doc, parts[:-1], path, create=(op == "add"))
    leaf = parts[-1]
    current = _plain(node[leaf]) if node is not None and leaf in node else None
    if current is None:
        current = _copied(target.default)
    if not isinstance(current, list):
        raise ConfigError(path, key, f"is {_toml_kind(current)} in the file, not a list",
                          "correct the file by hand, then try again")
    held = any(_same(v, item) for v in current)
    if op == "add":
        if held:
            raise _Unchanged
        if node is not None and leaf in node:
            node[leaf].append(_item(item, inline=True))
        else:
            node[leaf] = _item(current + [item])
        return
    if not held:
        raise ConfigError(path, key, f"holds no item {item!r}",
                          f"`moxi config {module}` shows the list")
    if node is not None and leaf in node:
        array = node[leaf]
        for i in range(len(array) - 1, -1, -1):
            if _same(_plain(array[i]), item):
                del array[i]
    else:
        node = _container(doc, parts[:-1], path, create=True)
        node[leaf] = _item([v for v in current if not _same(v, item)])


def _item(value, *, inline=False):
    """A tomlkit item for `value`: a table inside a list is written inline."""
    from ._vendor import tomlkit

    if isinstance(value, list):
        array = tomlkit.array()
        for v in value:
            array.append(_item(v, inline=True))
        if any(isinstance(v, dict) for v in value):
            array.multiline(multiline=True)
        return array
    if isinstance(value, dict) and inline:
        table = tomlkit.inline_table()
        for k, v in value.items():
            table[k] = _item(v, inline=True)
        return table
    return tomlkit.item(value)


def _check_file(module, schema, check, text, path, key) -> None:
    """The two checks of a config file's text, each given the text alone: `check_text`'s
    reader, then the module's `CONFIG_CHECK` target `check` when it names one. Raises
    `ConfigError` naming `path`, the module's config file; `key` is the key being changed
    (None when the file is checked as it is). A schema the reader refuses is the module's
    own fault and exits 3, as a schema that cannot be read does."""
    try:
        _read_text(module, schema, text, path)
    except SchemaError as exc:
        exc.exit_code = 3
        raise
    if check is None:
        return
    import importlib

    target, _, name = check.partition(":")
    try:
        func = getattr(importlib.import_module(target), name)
    except Exception as exc:
        raise _refused(path, f"cannot run {module}'s CONFIG_CHECK {check} "
                       f"({_first_line(exc)})", "reinstall or repair the module", 3,
                       key) from None
    try:
        func(text)
    except ConfigError as exc:
        raise _naming(exc, path)
    except Exception as exc:
        raise ConfigError(path, key, f"{module} refuses the file: {_first_line(exc)}",
                          "give a value the module accepts") from None


def _first_line(exc: BaseException) -> str:
    """The first line of `exc`'s text, or its type's name when the text is empty."""
    text = str(exc).strip()
    return text.splitlines()[0].strip() if text else type(exc).__name__


def _naming(exc: ConfigError, path) -> ConfigError:
    """`exc` naming the file `path`, its key and reason kept."""
    if exc.file != str(path):
        exc.file = str(path)
        where = exc.file + (f": key {exc.key!r}" if exc.key else "")
        exc.args = (f"{where}: {exc.problem}; fix: {exc.fix}",)
    return exc


def _check_config(module: str, schema: Schema, check) -> None:
    """Module `module`'s config file as it is, through the same two checks a change runs
    (a missing file is the empty text). Raises `ConfigError` naming the file."""
    path = _config_file(module)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(path, None, f"cannot be read ({exc})",
                          "make the file a readable TOML file, or remove it") from None
    _check_file(module, schema, check, text, path, None)


# -- tables a module keeps in files of its own -----------------------------------------

def table_hooks(found) -> dict:
    """The hook of each table module `found` declares in `CONFIG_TABLES`, by name, in the
    order it declares them, each imported. `found` is a `moxi.modules.Found` in the
    `present` state. Raises `SchemaError` (exit 3) when a table's name is also a
    top-level name of the module's own schema, or a hook can't be imported."""
    if not found.config_tables:
        return {}
    schema = schema_for(found)
    top = set() if schema is None else {name.split(".")[0] for name in schema.keys}
    hooks = {}
    for table, target in found.config_tables.items():
        if table in top:
            exc = SchemaError(found.target, table, f"declares the table {table!r} in "
                              "CONFIG_TABLES, and its settings schema also uses that name",
                              "rename the table or the settings key")
            exc.exit_code = 3
            raise exc
        hooks[table] = _imported_hook(found.name, target, table)
    return hooks


def _imported_hook(module, target, table):
    import importlib

    where, _, name = target.partition(":")
    try:
        return getattr(importlib.import_module(where), name)
    except Exception as exc:
        raise _refused(module, f"cannot run {module}'s CONFIG_TABLES {target} "
                       f"({_first_line(exc)})", "reinstall or repair the module", 3,
                       table) from None


def table_of(module: str, key: str) -> str | None:
    """The table of module `module` that `key` names: its part before the first dot,
    when the module is present and declares that table in `CONFIG_TABLES`; else None.
    Reads the announcement alone."""
    from . import modules, names

    if not names.is_module_name(module) or module == names.HARNESS:
        return None
    found = modules.announced(module)
    table = key.partition(".")[0]
    if found.state != modules.PRESENT or table not in found.config_tables:
        return None
    return table


def change_table(module: str, key: str, op: str, value=None, *, by: str,
                 env=None) -> tuple[bool, list[str]]:
    """Make one change of the row `key` names, `<table>.<row>`, through its table's hook:
    `op` "set" with `value`, or "unset". Returns whether the file changed and the
    hook's warning lines. A change appends the same history line as a config key's, key
    `<table>.<row>`, and reads no config file. Raises `ConfigError` with the exit code a
    config key's change would take: 2 for a refusal (an `add` or `remove`, a key that
    names no row), 3 for a module that is absent or a hook that can't run, 75 for a held
    lock."""
    _need_by(by, key)
    from . import files, modules

    env = os.environ if env is None else env
    table, _, row = key.partition(".")
    found = modules.load(module)
    if found.state != modules.PRESENT or table not in found.config_tables:
        raise _refused(module, found.reason or f"declares no table {table!r}",
                       f"install it: moxi install {module}"
                       if found.state == modules.ABSENT else
                       f"`moxi config {module}` lists its settings", 3, key)
    hook = table_hooks(found)[table]
    path = hook.file(env)
    if op in ("add", "remove"):
        raise ConfigError(path, key, f"is a row of {module}'s {table} table, which takes "
                          "a whole value", "change the whole value")
    if not row:
        raise ConfigError(path, key, f"names {module}'s {table} table, not one of its "
                          "rows", f"name one row: {table}.<row>")
    try:
        changed, warnings = hook.change(row, op, value, by=by, env=env)
    except ConfigError as exc:
        raise _keyed(exc, row, key) from None
    except (files.LockTimeout, ValueError) as exc:
        raise _edit_refused(exc, path, key) from None
    if changed:
        _record_changes(module, [key], op, by)
    return bool(changed), list(warnings)


def _keyed(exc: ConfigError, row: str, key: str) -> ConfigError:
    """`exc`, raised by a table's hook for `row`, naming the whole key `key` instead."""
    if exc.key == row:
        exc.key = key
        where = (exc.file or "config") + f": key {key!r}"
        exc.args = (f"{where}: {exc.problem}; fix: {exc.fix}",)
    return exc


__all__ = ["TYPES", "READS", "FIELDS", "PAGE_FIELDS", "COMMON_MAX", "ITEMS", "DEFAULT",
           "FILE", "ENV", "HISTORY", "ConfigError", "SchemaError", "Key", "Schema", "Settings",
           "is_switch_table", "is_entry_name", "schema_for", "check_text", "load", "Config",
           "set_value", "unset", "add_item", "remove_item", "history", "set_many", "draft_text",
           "ConfigChanged", "table_hooks", "table_of", "change_table"]
