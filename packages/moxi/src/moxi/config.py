"""One config file per module, born declared, read through this module.

A program changes the file through this module (`set`, `unset`, `add_item`,
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

`set`, `unset`, `add_item` and `remove_item` change one key of a module's config file
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

This module imports only what is already loaded when Python starts; everything else it
needs is imported inside the function that needs it, so a hook may read config through it.
"""

from __future__ import annotations

import _thread
import os
import sys

TYPES = ("bool", "int", "float", "string", "enum", "path", "list", "table")
READS = ("start", "use")
FIELDS = ("type", "default", "doc", "read", "values", "env", "retired", "needs", "items")
#: The item types a `list` or `table` key may declare as its `items`.
ITEMS = ("str", "int", "bool", "float")

DEFAULT = "default"
FILE = "file"
ENV = "env"

#: The history of every change `set`, `unset`, `add_item` and `remove_item` make, in the
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
    settings, 75 when the file stays locked.
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
        return (type(self), self._fields())


class Key(_Frozen):
    """One declared key."""

    __slots__ = ("name", "type", "default", "doc", "read", "values", "env", "retired",
                 "needs", "items")

    def __init__(self, name, type, default, doc, read, values=None, env=None,
                 retired=None, needs=None, items=None):
        for field, value in zip(self.__slots__, (name, type, default, doc, read, values,
                                                 env, retired, needs, items)):
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

    environ = os.environ if env is None else env
    if path is None:
        from .paths import _layout

        path = _layout(module).config_file(environ)
    else:
        path = Path(path)
    return _settings(module, schema, path, _parsed(path), environ)


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


def _settings(module, schema, path, data, environ) -> Settings:
    schema.check_env(module)
    from_file: dict = {}
    if data is not None:
        _walk_file(data, "", schema, path, from_file, environ)
    values: dict = {}
    sources: dict = {}
    for key in schema.live():
        raw = environ.get(key.env) if key.env else None
        if raw:
            values[key.name] = _from_env(key, raw, environ)
            sources[key.name] = ENV
        elif key.name in from_file:
            values[key.name] = _copied(from_file[key.name])
            sources[key.name] = FILE
        else:
            values[key.name] = _default_value(key, environ)
            sources[key.name] = DEFAULT
    return Settings(module, path, schema, _MappingProxy(values), _MappingProxy(sources))


def _copied(value):
    """A copy of a value read from TOML, so no two readings share a list or table."""
    if isinstance(value, list):
        return [_copied(v) for v in value]
    if isinstance(value, dict):
        return {k: _copied(v) for k, v in value.items()}
    return value


def _walk_file(table, prefix, schema, path, out, environ) -> None:
    fix_unknown = "remove it, or check its spelling against the module's schema"
    for name, value in table.items():
        dotted = f"{prefix}{name}"
        key = schema.keys.get(dotted)
        if key is not None:
            if key.retired is not None:
                raise ConfigError(path, dotted, "is retired", key.retired)
            out[dotted] = _check_value(key, value, path, environ)
        elif schema.is_group(dotted):
            if not isinstance(value, dict):
                raise ConfigError(path, dotted, "is a table of settings, not a value",
                                  f"write it as [{dotted}] with its keys under it")
            _walk_file(value, f"{dotted}.", schema, path, out, environ)
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


def _check_value(key, value, path, environ):
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
        return _path(key, value, path, environ)
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


def _path(key, value, source, environ):
    from pathlib import Path

    from .paths import _expanduser

    if value == "":
        return None
    expanded = Path(_expanduser(value, environ))
    if not expanded.is_absolute():
        raise ConfigError(source, key.name, f"is the relative path {value!r}",
                          "give an absolute path, or one starting with ~")
    return expanded


def _from_env(key, raw, environ):
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
    return _check_value(key, raw, where, environ)


def _default_value(key, environ):
    if key.type == "path":
        return _path(key, key.default, "schema", environ)
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
        return Key(name=name, type=spec.get("type"), default=spec.get("default"),
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

    key = Key(name=name, type=kind, default=spec["default"], doc=doc, read=read,
              values=values, env=env, needs=needs, items=items)
    try:
        _check_value(key, key.default, source, os.environ)
    except ConfigError as exc:
        raise bad(f"has a default that {exc.problem}", exc.fix) from None
    return key


# -- changing a module's settings ------------------------------------------------------

def set(module: str, key: str, value, *, by: str) -> list:
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
    table's entry then reads false). Returns the units applied, as `set` does. A key the
    file does not hold changes nothing: no file is made or written and no history line is
    added, though the file is still checked as it is (a missing one as the empty text)."""
    _change(module, key, "unset", None, by=by)
    return apply_units(module, key)


def apply_units(module: str, key: str, *, say=print) -> list:
    """After a `set` or `unset` of `key`: when it is the switch of one or more of the
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
    except ConfigError as exc:
        raise _refused(exc.file, exc.problem, exc.fix, 3) from None
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
    """Step 3 for `set`: `value` as key `target`'s type (or as a switch entry's bool).
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


def _change(module, key, op, value, *, by, text=False) -> bool:
    """Make one change; returns False when there was nothing to change."""
    if not isinstance(by, str) or not by.strip():
        raise ConfigError(None, key, "has no one named as making the change",
                          "pass by=, who makes it (cli, ui, moxi install)")
    from . import files

    schema, check = _schema_of(module)
    path = _config_file(module)
    target, entry = _resolve(schema, module, key, path)
    if op == "set":
        new = _coerce(target, entry, key, value, path, text)
        if entry is None:
            _check_value(target, new, path, os.environ)
    elif op in ("add", "remove"):
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
        with files.edit_toml(path, timeout=LOCK_TIMEOUT) as doc:
            parts = target.name.split(".")
            if entry is not None:
                parts.append(entry)
            if op == "set":
                node = _container(doc, parts[:-1], path, create=True)
                node[parts[-1]] = _item(new)
            elif op == "unset":
                dropped = _drop(doc, parts)
            else:
                _edit_list(doc, parts, target, op, item, path, module, key)
            from ._vendor import tomlkit

            _check_file(module, schema, check, tomlkit.dumps(doc), path, key)
            if op == "unset" and not dropped:
                raise _Unchanged
    except _Unchanged:
        return False
    except files.LockTimeout as exc:
        who = f"pid {exc.holder}" if exc.holder else "another process"
        raise _refused(path, f"is locked by {who}", "try again once it is free", 75,
                       key) from None
    except ValueError as exc:
        from ._vendor.tomlkit.exceptions import ParseError

        if isinstance(exc, ParseError):
            raise ConfigError(path, None, f"is not valid TOML ({exc})",
                              "correct the file's TOML syntax") from None
        raise ConfigError(path, key, f"cannot be written as TOML ({exc})",
                          "give a value TOML can hold") from None
    import time

    files.append_record(_history_file(), {
        "module": module, "key": key, "op": op, "by": by,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
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
            array.multiline(True)
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
        raise _refused(exc.file, exc.problem, exc.fix, 3, exc.key) from None
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


__all__ = ["TYPES", "READS", "FIELDS", "ITEMS", "DEFAULT", "FILE", "ENV", "HISTORY",
           "ConfigError", "SchemaError", "Key", "Schema", "Settings", "is_switch_table",
           "is_entry_name", "schema_for", "check_text", "load", "Config", "set", "unset",
           "add_item", "remove_item", "history"]
