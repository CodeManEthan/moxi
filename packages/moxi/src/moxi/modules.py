"""Module discovery: which Moxi modules this Python environment holds, and loading one.

Each module declares one entry point in the group `moxi.modules`, named by the module and
pointing at a small announcement module of its own, which imports only the standard
library:

    [project.entry-points."moxi.modules"]
    cache = "moxi_cache.module"

The announcement may define any name in `KNOWN`. Every name is optional: a missing name
takes its default, a name this moxi does not know is ignored, and a name that is present
is checked. A failed check makes the module `broken`, with the reason.

    ANNOUNCES       int, default 1: the announcement schema the module is written to. A
                    schema newer than `ANNOUNCES` here makes the module broken.
    INTERFACES      frozenset of int: the versions of what the module offers others
    API             dotted name of the module other modules call, or None
    CLI             "package.module:function" that `moxi <module>` runs, or None
    SUMMARY         one line, for `moxi modules`
    OPTIONAL        {extra: one sentence saying what installing it enables}
    GATED           {console script: the extra it needs}
    INTERNAL_ENV    {variable: why the module reads it}; a name ending in `_` is a prefix
    SETTINGS        resource name of the config schema in the announcement's package
    SETUP           "package.module:function" the installer runs after installing
    SEGMENT         dotted name of a module whose `segment` renders a status line segment
    DAEMONS         {name: entry}: long-running processes the core installs as units
    TIMERS          {name: entry}: scheduled commands the core installs, each behind a switch
    UNITS_FROM      target returning run-time declarations; kept as a string, never called
                    here
    HEALTH          {daemon: target}: one health check per declared daemon; with UNITS_FROM
                    a key may name a daemon declared only at run time
    DOCTOR          target: the module's own doctor checks
    STORES          {name: entry}: the stores the module keeps
    BUDGETS         {key: limit}: what the module may cost at idle
    SKILLS          a folder in the announcement's package, one subfolder per skill
    PAGE            "package.module:NAME": the module's page value, checked as syntax only
    COMPONENTS      {name: table}: components the module offers
    RESERVED_NAMES  [str]: send-only names the module needs reserved
    TEARDOWN        target: SETUP's reverse, run before an uninstall removes anything
    SESSION_ENV     {variable: why}: per-session variables the module stamps; a name
                    ending in `_` is a prefix
    CONFIG_CHECK    target, `fn(text: str) -> None`: the module's whole loader over its
                    config file's text alone, raising when it refuses; run by `moxi config
                    set`, `unset` and `check` (see `moxi.config`)
    QUESTIONS       {settings key: one line}: install questions

A "target" is "package.module:function". An inner key of a `DAEMONS`, `TIMERS` or
`STORES` entry that this moxi does not know makes the module broken: a new inner key comes
with a raised `ANNOUNCES`, so a typo is never read as "not set".

`installed`, `present` and `scan` read every distribution's entry-point metadata through
`importlib.metadata`. `load` reads only the distribution `moxi-<name>`, imports the
announcement and the `API` module, and returns a frozen `Found` whose state is `present`,
`absent` or `broken`; it never raises. It reads that distribution's `.dist-info` directory
straight off the `sys.path` directories, and finds there what
`importlib.metadata.distribution("moxi-<name>")` would; a distribution installed any other
way (an `.egg-info`, a zip, a custom finder) is `absent` to it. `scan` is the full scan,
which also finds two distributions claiming one name, a module declared by a distribution
not named `moxi-<name>`, and one `load` cannot see; `moxi modules`, the doctor and the
installer use it. A feature that needs an optional module asks `available` (a
cheap import check) or `load` where it needs it, and is off when the answer is not
`present`.

`extensions(group)` reads the behaviour one module offers another, an entry-point group
named `moxi.<module>` or `moxi.<module>.<part>`. `session_env()` collects every present
module's `SESSION_ENV`. A `TEARDOWN` raises `Refused` to stop an uninstall.

A hook may import this module and call `available` and `load`: neither they nor the
import load `importlib.metadata`, `dataclasses`, `inspect`, `typing`, `zipfile` or `json`
(beyond what the announcement's own package imports). So the record types are plain
frozen classes, not dataclasses, and `importlib.metadata` is imported only inside the
functions off that path.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Mapping
from types import MappingProxyType, ModuleType

from . import names

#: The entry-point group every module declares itself in.
GROUP = "moxi.modules"

PRESENT = "present"
ABSENT = "absent"
BROKEN = "broken"

#: The newest announcement schema this moxi reads.
ANNOUNCES = 1

_EMPTY: Mapping = MappingProxyType({})

#: Every name an announcement may define, with the default a missing one takes.
KNOWN: Mapping[str, object] = MappingProxyType({
    "ANNOUNCES": 1,
    "INTERFACES": frozenset(),
    "API": None,
    "CLI": None,
    "SUMMARY": "",
    "OPTIONAL": _EMPTY,
    "GATED": _EMPTY,
    "INTERNAL_ENV": _EMPTY,
    "SETTINGS": None,
    "SETUP": None,
    "SEGMENT": None,
    "DAEMONS": _EMPTY,
    "TIMERS": _EMPTY,
    "UNITS_FROM": None,
    "HEALTH": _EMPTY,
    "DOCTOR": None,
    "STORES": _EMPTY,
    "BUDGETS": _EMPTY,
    "SKILLS": None,
    "PAGE": None,
    "COMPONENTS": _EMPTY,
    "RESERVED_NAMES": (),
    "TEARDOWN": None,
    "SESSION_ENV": _EMPTY,
    "CONFIG_CHECK": None,
    "QUESTIONS": _EMPTY,
})

#: The `Found` field each announcement name is read into.
_FIELD = {name: name.lower() for name in KNOWN}
_FIELD["API"] = "api_name"

_DOTTED = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")
_TARGET = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*")
_GROUP = re.compile(r"moxi\.[a-z0-9]+(\.[a-z0-9_]+)?")
#: A daemon, timer or command name: lower-case words joined by single hyphens.
_UNIT_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
#: A store's name: lower-case words joined by single hyphens or underscores.
_STORE_NAME = re.compile(r"[a-z0-9]+([-_][a-z0-9]+)*")
#: A settings key, dotted for a key inside a table.
_KEY = re.compile(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SIZE = re.compile(r"[0-9]+[KMG]?")
_AGE = re.compile(r"[0-9]+d")
_EVERY = re.compile(r"([0-9]+)([mh])")
_AT = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")

_PLATFORMS = ("linux", "darwin", "win32")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAEMON_KEYS = frozenset({"command", "args", "doc", "stop", "upgrade", "memory_high",
                          "memory_swap_max", "platforms", "switch", "socket"})
_TIMER_KEYS = frozenset({"command", "args", "doc", "every", "at", "days", "switch",
                         "platforms"})
#: Keys a run-time declaration (from `UNITS_FROM`) may carry beside the others.
_RUNTIME_KEYS = frozenset({"argv", "cwd", "enabled"})
_STORE_KEYS = frozenset({"root", "path", "kind", "private", "retention", "rebuildable",
                         "lazy"})
_STORE_ROOTS = ("data", "state", "cache", "runtime")
_STORE_KINDS = ("sqlite", "jsonl", "file", "dir")
#: The core's own stores in a module's state directory: the gate's run record (with its
#: rolled generation) and logs (`moxi.run`), and the doctor's records (`moxi.doctor`). No
#: module declares one of them, or a path under `logs/`, in its `STORES`.
CORE_STATE_FILES = ("runs.jsonl", "runs.jsonl.1", "doctor.json")
CORE_STATE_DIRS = ("logs",)
_BUDGET_KEYS = ("idle_cpu", "rss", "fds", "hook_ms")


class _Frozen:
    """A record whose fields are set once, at construction.

    It gives what a frozen dataclass gives (the same attributes, keyword or positional
    construction with defaults, equality, hash and repr; setting or deleting a field
    raises) without importing `dataclasses`, which a hook should not pay for. Each subclass
    names its fields once, as `__slots__` and `_fields`, gives defaults in `_defaults`, and
    names in `_hidden` the fields left out of equality, hash and repr. `_replace` returns a
    copy with some fields changed.
    """

    __slots__ = ()
    _fields: tuple[str, ...] = ()
    _defaults: Mapping[str, object] = _EMPTY
    _hidden: frozenset[str] = frozenset()

    def __init__(self, *args, **kwargs):
        cls = type(self)
        if len(args) > len(cls._fields):
            raise TypeError(f"{cls.__name__}() takes {len(cls._fields)} positional "
                            f"arguments but {len(args)} were given")
        values = dict(zip(cls._fields, args))
        for key, value in kwargs.items():
            if key not in cls._fields:
                raise TypeError(f"{cls.__name__}() got an unexpected keyword argument "
                                f"{key!r}")
            if key in values:
                raise TypeError(f"{cls.__name__}() got multiple values for argument "
                                f"{key!r}")
            values[key] = value
        for name in cls._fields:
            if name in values:
                value = values[name]
            elif name in cls._defaults:
                value = cls._defaults[name]
            else:
                raise TypeError(f"{cls.__name__}() missing required argument: {name!r}")
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError(f"cannot assign to field {name!r}")

    def __delattr__(self, name):
        raise AttributeError(f"cannot delete field {name!r}")

    def _compared(self) -> tuple:
        return tuple(getattr(self, name) for name in self._fields
                     if name not in self._hidden)

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self._compared() == other._compared()

    def __hash__(self):
        return hash(self._compared())

    def __repr__(self):
        inner = ", ".join(f"{name}={getattr(self, name)!r}" for name in self._fields
                          if name not in self._hidden)
        return f"{type(self).__qualname__}({inner})"

    def __reduce__(self):
        return (type(self), tuple(getattr(self, name) for name in self._fields))

    def _replace(self, **changes):
        """A copy with the fields in `changes` replaced."""
        values = {name: getattr(self, name) for name in self._fields}
        for key in changes:
            if key not in values:
                raise TypeError(f"{type(self).__name__} has no field {key!r}")
        values.update(changes)
        return type(self)(**values)

    __replace__ = _replace


class Entry(_Frozen):
    """One `moxi.modules` entry point, as the environment's metadata declares it."""

    __slots__ = _fields = ("name", "distribution", "version", "target")

    name: str
    distribution: str
    version: str
    target: str


class Found(_Frozen):
    """What `load` found for one module name.

    Every announcement name is a field (`API` is `api_name`); a name the announcement
    leaves out holds its default. Tables are read-only mappings and lists are tuples.
    `api`, the imported `API` module, is left out of equality and repr.
    """

    __slots__ = _fields = (
        "name", "state", "reason", "distribution", "version", "target", "package",
        "announces", "interfaces", "api_name", "api", "cli", "summary", "optional", "gated",
        "internal_env", "settings", "setup", "segment", "daemons", "timers", "units_from",
        "health", "doctor", "stores", "budgets", "skills", "page", "components",
        "reserved_names", "teardown", "session_env", "config_check", "questions")
    _defaults = MappingProxyType({
        "reason": "", "distribution": None, "version": None, "target": None,
        "package": None, "api": None,
        **{_FIELD[name]: default for name, default in KNOWN.items()},
    })
    _hidden = frozenset({"api"})

    name: str
    state: str
    reason: str
    distribution: str | None
    version: str | None
    target: str | None
    #: The package the announcement lives in; `SETTINGS` and `SKILLS` name resources in it.
    package: str | None
    announces: int
    interfaces: frozenset[int]
    api_name: str | None
    #: The imported `API` module, when the state is `present` and `API` is not None.
    api: ModuleType | None
    cli: str | None
    summary: str
    optional: Mapping[str, str]
    gated: Mapping[str, str]
    internal_env: Mapping[str, str]
    settings: str | None
    setup: str | None
    segment: str | None
    daemons: Mapping[str, Mapping]
    timers: Mapping[str, Mapping]
    units_from: str | None
    health: Mapping[str, str]
    doctor: str | None
    stores: Mapping[str, Mapping]
    budgets: Mapping[str, object]
    skills: str | None
    #: The raw `"package.module:NAME"`; never imported here.
    page: str | None
    components: Mapping[str, Mapping]
    reserved_names: tuple[str, ...]
    teardown: str | None
    session_env: Mapping[str, str]
    #: The `CONFIG_CHECK` target, `fn(text: str) -> None`; imported only by `moxi.config`.
    config_check: str | None
    questions: Mapping[str, str]

    @property
    def ok(self) -> bool:
        return self.state == PRESENT


class Refused(Exception):
    """What a `TEARDOWN` raises to stop an uninstall; `reason` says why, for the user."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ExtensionError(Exception):
    """An extension entry whose target could not be loaded."""

    def __init__(self, name: str, reason: str):
        super().__init__(f"{name}: {reason}")
        self.name = name
        self.reason = reason


class Extension(_Frozen):
    """One entry of an extension group: behaviour one module offers another."""

    __slots__ = _fields = ("module", "name", "target", "distribution")

    #: The offering module: the entry's name up to its first ".".
    module: str
    #: The entry's full name, such as "comms" or "comms.kinds".
    name: str
    #: "package.module:attr" or "package.module".
    target: str
    distribution: str

    def load(self) -> object:
        """Import the target and return it; raises `ExtensionError` when that fails."""
        import importlib

        module_name, _, attr = self.target.partition(":")
        try:
            obj = importlib.import_module(module_name.strip())
            for part in (p.strip() for p in attr.split(".")) if attr else ():
                obj = getattr(obj, part)
        except Exception as exc:  # noqa: BLE001 - every failure is the entry's
            raise ExtensionError(self.name, f"loading {self.target} failed: "
                                            f"{_describe(exc)}") from exc
        return obj


def installed() -> list[Entry]:
    """Every `moxi.modules` entry point in this environment, sorted by name. Imports none."""
    from importlib.metadata import entry_points

    found = []
    for ep in entry_points(group=GROUP):
        dist = getattr(ep, "dist", None)
        found.append(Entry(name=ep.name,
                           distribution=_dist_name(dist),
                           version=getattr(dist, "version", None) or "",
                           target=ep.value))
    return sorted(found, key=lambda e: (e.name, e.distribution))


def present(name: str) -> bool:
    """True when at least one distribution declares module `name`. Imports nothing.

    It promises nothing about loading: a feature asks `load`.
    """
    return any(entry.name == name for entry in installed())


def available(name: str) -> bool:
    """True when module `name`'s package can be imported. Reads no metadata and imports
    nothing, so a hook can ask it before paying for `load`."""
    if not names.is_module_name(name):
        return False
    from importlib.util import find_spec

    try:
        return find_spec(names.import_name(name)) is not None
    except (ImportError, ValueError):
        return False


# One record per name per process: the announcement is imported once, the API module at
# most once. The interface check runs on every call against the record.
_records: dict[str, "_Record"] = {}
_lock = threading.RLock()


def load(name: str, *, interface: int | None = None) -> Found:
    """Load module `name` and, when `interface` is given, check that it offers it.

    Reads only the distribution `moxi-<name>`: that distribution missing, or declaring no
    `moxi.modules` entry named `name`, is `absent`. Never raises: anything that goes
    wrong is a `broken` `Found` with the reason.
    """
    with _lock:
        record = _records.get(name)
        if record is None:
            record = _records[name] = _Record.read(name)
        return record.found(interface)


def scan() -> list[Found]:
    """Every module this environment declares, by the full scan, sorted by name.

    Besides what `load` finds, a name two distributions claim is `broken` naming both,
    and so is a module declared by a distribution not named `moxi-<name>`, under a name
    that is not a module name, or by a distribution `load` cannot see (one not installed as
    a `.dist-info` directory on a `sys.path` directory). Never raises.
    """
    try:
        from importlib.metadata import entry_points

        claims: dict[str, list] = {}
        for ep in entry_points(group=GROUP):
            claims.setdefault(ep.name, []).append(ep)
    except Exception as exc:  # noqa: BLE001 - scan never raises
        _debug(f"reading the environment's modules failed: {_describe(exc)}")
        return []
    out = []
    for name in sorted(claims):
        eps = claims[name]
        if len(eps) > 1:
            who = ", ".join(sorted(_dist_label(ep) for ep in eps))
            out.append(Found(name=name, state=BROKEN,
                             reason=f"{len(eps)} distributions declare the Moxi module "
                                    f"{name!r} ({who}); uninstall all but one"))
            continue
        ep = eps[0]
        dist = getattr(ep, "dist", None)
        where = dict(name=name, distribution=_dist_name(dist) or None,
                     version=getattr(dist, "version", None), target=ep.value)
        if not names.is_module_name(name):
            out.append(Found(state=BROKEN, **where, reason=_not_a_name(name)))
            continue
        wanted = names.distribution(name)
        if _normalize(where["distribution"] or "") != wanted:
            out.append(Found(state=BROKEN, **where,
                             reason=f"{_dist_label(ep)} declares the Moxi module {name!r}; "
                                    f"its distribution must be {wanted}"))
            continue
        if not _seen_by_load(dist):
            out.append(Found(state=BROKEN, **where,
                             reason="not installed as a .dist-info directory on a sys.path "
                                    "directory; reinstall with pip or uv"))
            continue
        out.append(load(name))
    return out


def extensions(group: str) -> list[Extension]:
    """The entries of extension group `group`, sorted by name. Imports none of them.

    A group is named `moxi.<module>` or `moxi.<module>.<part>`, owned by its second
    segment; any other name has no entries. Never raises: a metadata read that fails
    returns what was read before it and logs one line at debug level.
    """
    if not isinstance(group, str) or not _GROUP.fullmatch(group):
        _debug(f"{group!r} is not an extension group name: moxi.<module> or "
               "moxi.<module>.<part>")
        return []
    out: list[Extension] = []
    try:
        from importlib.metadata import distributions

        seen: set[str] = set()
        for dist in distributions():
            dist_name = _dist_name(dist)
            key = _normalize(dist_name)
            if key in seen:
                continue
            seen.add(key)
            for ep in dist.entry_points.select(group=group):
                out.append(Extension(module=ep.name.split(".")[0], name=ep.name,
                                     target=ep.value, distribution=dist_name))
    except Exception as exc:  # noqa: BLE001 - extensions never raises
        _debug(f"reading the extension group {group} failed: {_describe(exc)}")
    return sorted(out, key=lambda e: (e.name, e.distribution))


def session_env() -> frozenset[str]:
    """The `SESSION_ENV` keys of every present module, read through `load`.

    A broken module contributes nothing. Never raises.
    """
    try:
        found_names = sorted({entry.name for entry in installed()})
    except Exception as exc:  # noqa: BLE001
        _debug(f"reading the environment's modules failed: {_describe(exc)}")
        return frozenset()
    keys: set[str] = set()
    for name in found_names:
        found = load(name)
        if found.state == PRESENT:
            keys.update(found.session_env)
    return frozenset(keys)


def reset() -> None:
    """Forget every record, so the next `load` reads the environment again (for tests)."""
    with _lock:
        _records.clear()


class _Record:
    """What one name resolved to in this process."""

    def __init__(self, base: Found, api_name: str | None = None):
        self.base = base
        self.api_name = api_name
        self.api: ModuleType | None = None
        self.api_error: str | None = None
        self.api_tried = False

    @classmethod
    def read(cls, name: str) -> _Record:
        try:
            return cls._read(name)
        except Exception as exc:  # noqa: BLE001 - load never raises
            return cls(Found(name=name, state=BROKEN,
                             reason=f"reading the environment's modules failed: "
                                    f"{_describe(exc)}"))

    @classmethod
    def _read(cls, name: str) -> _Record:
        if not names.is_module_name(name):
            return cls(Found(name=name, state=ABSENT, reason=_not_a_name(name)))
        wanted = names.distribution(name)
        dist = _DistInfo.find(name)
        if dist is None:
            return cls(Found(name=name, state=ABSENT,
                             reason=f"no distribution in this environment declares the "
                                    f"Moxi module {name!r} ({wanted} is not installed)"))
        value = dist.entry(name)
        if value is None:
            return cls(Found(name=name, state=ABSENT,
                             reason=f"{wanted} is installed but declares no {GROUP} entry "
                                    f"point named {name!r}"))
        where = dict(name=name, distribution=dist.name, version=dist.version, target=value)
        if name in names.VERBS:
            return cls(Found(state=BROKEN, **where,
                             reason=f"{name!r} is one of the umbrella's own verbs, so "
                                    f"`moxi {name}` can never reach this module; the "
                                    "module needs another name"))
        if name in names.RESERVED:
            return cls(Found(state=BROKEN, **where,
                             reason=f"{name!r} is a name the core keeps for itself; the "
                                    "module needs another name"))
        if ":" in value:
            return cls(Found(state=BROKEN, **where,
                             reason=f"the entry point {value!r} names an attribute; "
                                    "it must name the announcement module alone"))
        try:
            module = _import_entry(value)
        except Exception as exc:  # noqa: BLE001
            return cls(Found(state=BROKEN, **where,
                             reason=f"importing its announcement {value} failed: "
                                    f"{_describe(exc)}"))
        try:
            fields, problems = _check(module.__dict__, name)
        except _TooNew as exc:
            return cls(Found(state=BROKEN, **where, reason=str(exc)))
        except Exception as exc:  # noqa: BLE001
            return cls(Found(state=BROKEN, **where,
                             reason=f"reading its announcement {value} failed: "
                                    f"{_describe(exc)}"))
        if problems:
            return cls(Found(state=BROKEN, **where,
                             reason=f"its announcement {value} " + "; ".join(problems)))
        package = module.__name__ if hasattr(module, "__path__") else module.__package__
        return cls(Found(state=PRESENT, **where, package=package or None, **fields),
                   fields["api_name"])

    def found(self, interface: int | None) -> Found:
        base = self.base
        if base.state != PRESENT:
            return base
        if interface is not None and interface not in base.interfaces:
            offered = ", ".join(map(str, sorted(base.interfaces))) or "none"
            return base._replace(state=BROKEN,
                                 reason=f"{base.distribution} {base.version} offers "
                                        f"interfaces {{{offered}}}; the caller speaks "
                                        f"{interface}")
        if self.api_name is None:
            return base
        if not self.api_tried:
            self.api_tried = True
            try:
                import importlib

                self.api = importlib.import_module(self.api_name)
            except Exception as exc:  # noqa: BLE001
                self.api_error = (f"importing its API module {self.api_name} failed: "
                                  f"{_describe(exc)}")
        if self.api_error is not None:
            return base._replace(state=BROKEN, reason=self.api_error)
        return base._replace(api=self.api)


# --- load's direct read of one distribution -----------------------------------------------
#
# `load` reads the `moxi-<name>` distribution's own `.dist-info` directory rather than
# importing `importlib.metadata`, whose import costs a hook more than the rest of its path.
# It looks where `importlib.metadata` looks, in the same order, so for a distribution
# installed as a `.dist-info` directory on a `sys.path` directory it finds the same
# distribution, version and entry. One installed any other way is absent here, and the full
# scan reads it broken.

#: A run of "-", "_" and "." in a distribution's name, which name normalization makes one.
_RUNS = re.compile(r"[-_.]+")
#: An entry point's value: a module, an optional ":attr" and optional "[extras]".
_ENTRY = re.compile(r"(?P<module>[\w.]+)\s*(:\s*(?P<attr>[\w.]+)\s*)?"
                    r"((?P<extras>\[.*\])\s*)?$")


class _DistInfo:
    """One distribution's `.dist-info` directory: its path, name and version."""

    __slots__ = ("path", "name", "version")

    def __init__(self, path: str, name: str, version: str | None):
        self.path = path
        self.name = name
        self.version = version

    @classmethod
    def find(cls, module: str) -> _DistInfo | None:
        """The `moxi-<module>` distribution `importlib.metadata.distribution` would find,
        when it is a `.dist-info` directory on a `sys.path` directory; otherwise None.

        Each `sys.path` entry that is a directory is listed once, in order, `""` as the
        current directory, and the first metadata directory there whose name, normalized,
        is the distribution's is the one. The suffix matches in any case. An `.egg-info`
        found first is None, as is a distribution only a zip or a custom finder holds.
        """
        wanted = "moxi_" + module
        for entry in sys.path:
            try:
                root = os.fspath(entry) or "."
                children = os.listdir(root) if isinstance(root, str) else ()
            except Exception:  # noqa: BLE001 - not a directory: a zip, or nothing there
                continue
            for child in children:
                low = child.lower()
                if not low.endswith((".dist-info", ".egg-info")):
                    continue
                stem = child[:low.rindex(".")]
                dist, _, version = stem.partition("-")
                if _RUNS.sub("_", dist).lower() != wanted:
                    continue
                if low.endswith(".egg-info"):
                    return None
                return cls._read(os.path.join(root, child), dist, version)
        return None

    @classmethod
    def _read(cls, path: str, dist: str, version: str) -> _DistInfo:
        """Name and version from METADATA, as `importlib.metadata` reads them; the
        directory's own name gives either one that METADATA lacks."""
        text = _read_text(path, "METADATA") or _read_text(path, "PKG-INFO") or ""
        headers: dict[str, str] = {}
        for line in text.splitlines():
            if not line:
                break
            key, sep, value = line.partition(":")
            key = key.lower()
            if sep and key in ("name", "version") and key not in headers:
                headers[key] = value.lstrip(" \t")
        return cls(path, headers.get("name") or dist,
                   headers.get("version") or version or None)

    def entry(self, name: str) -> str | None:
        """The value of this distribution's `moxi.modules` entry point `name`, or None."""
        text = _read_text(self.path, "entry_points.txt") or ""
        section = None
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line.strip("[]")
                continue
            key, sep, value = line.partition("=")
            if section == GROUP and sep and key.strip() == name:
                return value.strip()
        return None


def _read_text(directory: str, name: str) -> str | None:
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as file:
            return file.read()
    except OSError:
        return None


def _import_entry(value: str) -> ModuleType:
    """Import an entry point's module, as `EntryPoint.load` does for one with no attr."""
    import importlib

    match = _ENTRY.match(value)
    if match is None:
        raise ValueError(f"{value!r} is not an entry point's value")
    return importlib.import_module(match.group("module"))


def _seen_by_load(dist) -> bool:
    """True when `dist`'s metadata is a `.dist-info` directory directly in a `sys.path`
    directory, so `load`'s direct read sees it: not an `.egg-info`, a zip member, or one
    a finder supplies with no path."""
    from pathlib import Path

    # A distribution found on a path keeps its metadata directory here; the class has no
    # public name for it, so one without it is looked for beside its files.
    path = getattr(dist, "_path", None)
    if path is None:
        path = _metadata_dir(dist)
    if (not isinstance(path, Path) or not path.name.lower().endswith(".dist-info")
            or not path.is_dir()):
        return False
    for entry in sys.path:
        try:
            if Path(os.fspath(entry) or ".") == path.parent:
                return True
        except TypeError:
            continue
    return False


def _metadata_dir(dist):
    """`dist`'s `.dist-info` directory found through public names alone: in the directory
    `locate_file("")` gives, the one whose name, normalized as `_DistInfo.find` normalizes
    it, is the distribution's and whose version is its own. None when there is none."""
    from pathlib import Path

    try:
        where = dist.locate_file("")
        name, version = dist.metadata["Name"], dist.version
    except Exception:  # noqa: BLE001 - a distribution that can't say where it is
        return None
    if not isinstance(where, Path) or not name:
        return None
    wanted = _RUNS.sub("_", name).lower()
    try:
        children = os.listdir(where)
    except OSError:
        return None
    for child in children:
        low = child.lower()
        if not low.endswith(".dist-info"):
            continue
        dist_name, _, dist_version = child[:low.rindex(".")].partition("-")
        if _RUNS.sub("_", dist_name).lower() == wanted and dist_version == version:
            return where / child
    return None


# --- the announcement's checks ------------------------------------------------------------
#
# The contract check runs these same checks over the literals it reads from an
# announcement's source, so a module is held to one set of rules whether it is loaded or
# checked.

class _Bad(Exception):
    """An announcement name that does not hold to the contract."""


class _TooNew(Exception):
    """An announcement written to a newer schema than this moxi reads."""


def _check(values: Mapping[str, object], module: str | None = None
           ) -> tuple[dict[str, object], list[str]]:
    """Read the announcement names in `values`: the `Found` fields, and the problems.

    Each name in `KNOWN` that `values` holds is checked; a missing one takes its default
    and a name not in `KNOWN` is ignored. `module` is the module's name, for the rules that
    depend on its environment prefix. Each problem reads after "its announcement" or "the
    announcement". Raises `_TooNew` when `ANNOUNCES` is newer than this moxi reads, since
    the rest of such an announcement may use names and keys this moxi does not know.
    """
    announces = values.get("ANNOUNCES", KNOWN["ANNOUNCES"])
    if (isinstance(announces, int) and not isinstance(announces, bool)
            and announces > ANNOUNCES):
        raise _TooNew(f"announces schema {announces}; this moxi knows up to {ANNOUNCES}; "
                      "upgrade moxi")
    fields: dict[str, object] = {}
    problems: list[str] = []
    for name, default in KNOWN.items():
        if name not in values:
            fields[_FIELD[name]] = default
            continue
        try:
            fields[_FIELD[name]] = _CHECKS[name](values[name], name)
        except _Bad as exc:
            problems.append(str(exc))
            fields[_FIELD[name]] = default
    problems += _across(values, fields, module)
    return fields, problems


def _across(values: Mapping[str, object], fields: dict, module: str | None) -> list[str]:
    """The rules that tie one name to another."""
    problems = []
    daemons, timers = fields["daemons"], fields["timers"]
    for both in sorted(set(daemons) & set(timers)):
        problems.append(f"declares {both!r} as both a daemon and a timer; a module's "
                        "daemons and timers each need their own name")
    for daemon, kind, other in stem_clashes(daemons, timers):
        problems.append(clash_line(daemon, kind, other))
    # With UNITS_FROM, a HEALTH key may name a daemon declared only at run time; those
    # are read through `moxi.units.declarations`, never here, so the key is judged there.
    if fields["units_from"] is None:
        for key in sorted(set(fields["health"]) - set(daemons)):
            problems.append(f"has HEALTH for {key!r}, which is no daemon in DAEMONS")
    if module and names.is_module_name(module):
        prefix = names.env_prefix(module)
        internal = fields["internal_env"]
        for key in sorted(fields["session_env"]):
            if not key.startswith(prefix):
                continue
            if not any(have == key or (have.endswith("_") and key.startswith(have))
                       for have in internal):
                problems.append(f"has SESSION_ENV {key!r}, under the module's own prefix "
                                f"{prefix}, and INTERNAL_ENV does not declare it")
    return problems


def _shape(name: str, value: object, what: str) -> _Bad:
    return _Bad(f"has {name} = {value!r}; it must be {what}")


def _announces(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise _shape(name, value, "an integer of 1 or more")
    return value


def _interfaces(value, name):
    if not isinstance(value, frozenset) or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in value):
        raise _shape(name, value, "a frozenset of integers")
    return value


def _summary(value, name):
    if not _one_line(value):
        raise _shape(name, value, "one non-empty line")
    return value


def _pattern(pattern: re.Pattern | None, what: str):
    def check(value, name):
        if value is None:
            return None
        if isinstance(value, str) and value and (pattern is None
                                                 or pattern.fullmatch(value)):
            return value
        raise _shape(name, value, f"{what} or None")
    return check


def _strings(value, name):
    if not isinstance(value, Mapping) or not all(
            isinstance(k, str) and k and isinstance(v, str) for k, v in value.items()):
        raise _Bad(f"has {name} = {value!r}; it must map names to strings")
    return MappingProxyType(dict(value))


def _skills(value, name):
    if value is None:
        return None
    if not _relative(value):
        raise _shape(name, value, "a relative folder in the announcement's package, with "
                                  "no '..', or None")
    return value


def _reserved(value, name):
    if not isinstance(value, (list, tuple)) or not all(
            isinstance(v, str) and v for v in value):
        raise _shape(name, value, "a list of non-empty strings")
    if len(set(value)) != len(value):
        raise _shape(name, value, "a list with no name twice")
    return tuple(value)


def _health(value, name):
    if not isinstance(value, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) and _TARGET.fullmatch(v)
            for k, v in value.items()):
        raise _shape(name, value, 'a table of daemon names to "package.module:function"')
    return MappingProxyType(dict(value))


def _components(value, name):
    if not isinstance(value, Mapping) or not all(
            isinstance(k, str) and k and isinstance(v, Mapping) for k, v in value.items()):
        raise _shape(name, value, "a table of tables")
    return _freeze(value)


def _session_env(value, name):
    if not isinstance(value, Mapping) or not all(
            isinstance(k, str) and _ENV_NAME.fullmatch(k) and isinstance(v, str)
            for k, v in value.items()):
        raise _shape(name, value, "a table of variable names (or prefixes ending in _) to "
                                  "the reason each is stamped")
    return MappingProxyType(dict(value))


def _questions(value, name):
    if not isinstance(value, Mapping) or not all(
            isinstance(k, str) and _KEY.fullmatch(k) and _one_line(v)
            for k, v in value.items()):
        raise _shape(name, value, "a table of settings keys to one non-empty line each")
    return MappingProxyType(dict(value))


def _budgets(value, name):
    if not isinstance(value, Mapping):
        raise _shape(name, value, "a table")
    for key, limit in value.items():
        if key not in _BUDGET_KEYS:
            raise _Bad(f"has BUDGETS key {key!r}; the keys are "
                       f"{', '.join(_BUDGET_KEYS)}")
        ok = {"idle_cpu": _number(limit) and 0 < limit <= 1,
              "rss": _size(limit),
              "fds": isinstance(limit, int) and not isinstance(limit, bool) and limit > 0,
              "hook_ms": _number(limit) and limit > 0}[key]
        if not ok:
            what = {"idle_cpu": "a number above 0 and at most 1",
                    "rss": "a size: digits with an optional K, M or G",
                    "fds": "an integer above 0",
                    "hook_ms": "a number above 0"}[key]
            raise _Bad(f"has BUDGETS[{key!r}] = {limit!r}; it must be {what}")
    return MappingProxyType(dict(value))


def _units(kind: str):
    def check(value, name):
        if not isinstance(value, Mapping):
            raise _shape(name, value, f"a table of {kind} names to entries")
        for unit, entry in value.items():
            _unit(kind, unit, entry, label=name)
        return _freeze(value)
    return check


def _unit(kind: str, name: object, entry: object, *, runtime: bool = False,
          label: str | None = None) -> None:
    """Check one daemon or timer entry; raises `_Bad`. `kind` is "daemon" or "timer".

    `runtime` allows the forms only a run-time declaration may use: `argv` and `cwd`,
    `enabled`, and a timer with no `switch`.
    """
    label = label or ("DAEMONS" if kind == "daemon" else "TIMERS")
    if not isinstance(name, str) or not _UNIT_NAME.fullmatch(name):
        raise _Bad(f"has {label} name {name!r}; a name is lower-case letters and digits, "
                   "in words joined by single hyphens")
    where = f"{label}[{name!r}]"
    if not isinstance(entry, Mapping):
        raise _Bad(f"has {where} = {entry!r}; an entry is a table")
    known = _DAEMON_KEYS if kind == "daemon" else _TIMER_KEYS
    if runtime:
        known = known | _RUNTIME_KEYS
    for key in entry:
        if key not in known:
            raise _Bad(f"has {where} key {key!r}, which this moxi does not know; a new key "
                       "comes with a raised ANNOUNCES")
    get = entry.get
    if runtime and "argv" in entry:
        if "command" in entry:
            raise _Bad(f"has {where} with both command and argv; it takes one of the two")
        argv = get("argv")
        if not _str_list(argv) or not argv or not _absolute(argv[0]):
            raise _Bad(f"has {where} argv = {argv!r}; it must be a list of strings whose "
                       "first is an absolute path")
    elif not isinstance(get("command"), str) or not _UNIT_NAME.fullmatch(get("command")):
        raise _Bad(f"has {where} command = {get('command')!r}; it must be a console script's "
                   "name, not a path")
    if "cwd" in entry and not (isinstance(get("cwd"), str) and _absolute(get("cwd"))):
        raise _Bad(f"has {where} cwd = {get('cwd')!r}; it must be an absolute directory")
    if "enabled" in entry and not isinstance(get("enabled"), bool):
        raise _Bad(f"has {where} enabled = {get('enabled')!r}; it must be true or false")
    if "args" in entry and not _str_list(get("args")):
        raise _Bad(f"has {where} args = {get('args')!r}; it must be a list of strings")
    if "doc" in entry and not _one_line(get("doc")):
        raise _Bad(f"has {where} doc = {get('doc')!r}; it must be one non-empty line")
    if "platforms" in entry and not (_str_list(get("platforms"))
                                     and set(get("platforms")) <= set(_PLATFORMS)):
        raise _Bad(f"has {where} platforms = {get('platforms')!r}; it must be a list drawn "
                   f"from {', '.join(_PLATFORMS)}")
    if "switch" in entry and not (isinstance(get("switch"), str)
                                  and _KEY.fullmatch(get("switch"))):
        raise _Bad(f"has {where} switch = {get('switch')!r}; it must be a settings key")
    if kind == "daemon":
        _daemon(where, entry)
    else:
        _timer(where, entry, runtime=runtime)


def _daemon(where: str, entry: Mapping) -> None:
    get = entry.get
    if "stop" in entry and get("stop") not in ("group", "process"):
        raise _Bad(f"has {where} stop = {get('stop')!r}; it must be 'group' or 'process'")
    if "upgrade" in entry and get("upgrade") not in ("restart", "keep"):
        raise _Bad(f"has {where} upgrade = {get('upgrade')!r}; it must be 'restart' or "
                   "'keep'")
    for key in ("memory_high", "memory_swap_max"):
        if key in entry and not _size(get(key)):
            raise _Bad(f"has {where} {key} = {get(key)!r}; it must be a size: digits with "
                       "an optional K, M or G")
    if "socket" in entry:
        socket = get("socket")
        if (not isinstance(socket, Mapping) or set(socket) != {"setting", "addresses"}
                or not isinstance(socket["setting"], str)
                or not _KEY.fullmatch(socket["setting"])
                or not _str_list(socket["addresses"]) or not socket["addresses"]
                or not all(_ip(a) for a in socket["addresses"])):
            raise _Bad(f"has {where} socket = {socket!r}; it must be a table of exactly "
                       "setting (a settings key) and addresses (a non-empty list of IP "
                       "addresses)")
        # One socket is written per address, so the same address twice (in any spelling
        # of it) would be two sockets on one address.
        seen: dict[object, str] = {}
        for address in socket["addresses"]:
            ip = _ip_of(address)
            if ip in seen:
                same = seen[ip]
                twice = address if same == address else f"{same} and {address}"
                raise _Bad(f"has {where} socket addresses {list(socket['addresses'])!r} "
                           f"with {twice} given twice; give each address once")
            seen[ip] = address


def stem_clashes(daemons: Mapping, timers: Mapping) -> list[tuple[str, str, str]]:
    """The pairs of units whose systemd files would share a name, as `(daemon, kind,
    other)`: a daemon with a `socket` of n addresses writes a socket unit per address,
    named for the daemon and for `<daemon>-1` to `<daemon>-<n-1>`, so another daemon or
    timer named `<daemon>-<k>`, k below n, would write the same socket unit (with its
    own socket) or the service systemd pairs by name with it (without one). `kind` is
    `daemon` or `timer`, the other unit's. Sorted, so callers refuse them in one order."""
    out = []
    for name, entry in daemons.items():
        socket = entry.get("socket") if isinstance(entry, Mapping) else None
        if not isinstance(socket, Mapping):
            continue
        addresses = socket.get("addresses")
        count = len(addresses) if isinstance(addresses, (list, tuple)) else 0
        for k in range(1, count):
            other = f"{name}-{k}"
            if other in daemons:
                out.append((name, "daemon", other))
            if other in timers:
                out.append((name, "timer", other))
    return sorted(out)


def clash_line(daemon: str, kind: str, other: str) -> str:
    """The reason a unit pair of `stem_clashes` is refused, for a line."""
    k = int(other.rpartition("-")[2])
    return (f"declares {kind} {other!r} beside daemon {daemon!r}, whose socket for its "
            f"address {k + 1} is a unit named for {other!r}; the two would share that "
            "name, so give one of them another name")


def _timer(where: str, entry: Mapping, *, runtime: bool) -> None:
    get = entry.get
    if "switch" not in entry and not runtime:
        raise _Bad(f"has {where} with no switch; a timer starts behind a switch")
    if ("every" in entry) == ("at" in entry):
        raise _Bad(f"has {where} with {'both' if 'every' in entry else 'neither'} every "
                   "and at; a timer takes exactly one")
    if "every" in entry:
        every = get("every")
        match = _EVERY.fullmatch(every) if isinstance(every, str) else None
        minutes = (int(match.group(1)) * (60 if match.group(2) == "h" else 1)
                   if match else 0)
        if not 1 <= minutes <= 24 * 60:
            raise _Bad(f"has {where} every = {every!r}; it must be '<n>m' or '<n>h', from "
                       "1m to 24h")
        if "days" in entry:
            raise _Bad(f"has {where} with every and days; days goes with at")
    else:
        at = get("at")
        if not _str_list(at) or not at or not all(_AT.fullmatch(t) for t in at):
            raise _Bad(f"has {where} at = {at!r}; it must be a non-empty list of 'HH:MM' "
                       "times (24-hour)")
    if "days" in entry:
        days = get("days")
        if not _str_list(days) or not days or not set(days) <= set(_DAYS):
            raise _Bad(f"has {where} days = {days!r}; it must be a non-empty list drawn "
                       f"from {', '.join(_DAYS)}")


def _stores(value, name):
    if not isinstance(value, Mapping):
        raise _shape(name, value, "a table of store names to entries")
    for store, entry in value.items():
        if not isinstance(store, str) or not _STORE_NAME.fullmatch(store):
            raise _Bad(f"has STORES name {store!r}; a name is lower-case letters and "
                       "digits, in words joined by single hyphens or underscores")
        where = f"STORES[{store!r}]"
        if not isinstance(entry, Mapping):
            raise _Bad(f"has {where} = {entry!r}; an entry is a table")
        for key in entry:
            if key not in _STORE_KEYS:
                raise _Bad(f"has {where} key {key!r}, which this moxi does not know; a new "
                           "key comes with a raised ANNOUNCES")
        get = entry.get
        if get("root") not in _STORE_ROOTS:
            raise _Bad(f"has {where} root = {get('root')!r}; it must be one of "
                       f"{', '.join(_STORE_ROOTS)}")
        if not _relative(get("path")):
            raise _Bad(f"has {where} path = {get('path')!r}; it must be a relative path "
                       "with no '..'")
        if get("root") == "state" and _core_store(get("path")):
            raise _Bad(f"has {where} path = {get('path')!r} in the state root; that path "
                       "is the core's (the gate's runs.jsonl and logs/, the doctor's "
                       "doctor.json), and no module declares it")
        if get("kind") not in _STORE_KINDS:
            raise _Bad(f"has {where} kind = {get('kind')!r}; it must be one of "
                       f"{', '.join(_STORE_KINDS)}")
        for key in ("private", "rebuildable", "lazy"):
            if key in entry and not isinstance(get(key), bool):
                raise _Bad(f"has {where} {key} = {get(key)!r}; it must be true or false")
        if "retention" in entry:
            retention = get("retention")
            if not (retention == "keep" or _size(retention)
                    or (isinstance(retention, str) and _AGE.fullmatch(retention))):
                raise _Bad(f"has {where} retention = {retention!r}; it must be 'keep', a "
                           "size (digits with an optional K, M or G) or an age '<n>d'")
    return _freeze(value)


_target = _pattern(_TARGET, '"package.module:function"')

_CHECKS = {
    "ANNOUNCES": _announces,
    "INTERFACES": _interfaces,
    "API": _pattern(_DOTTED, "a dotted module name"),
    "CLI": _target,
    "SUMMARY": _summary,
    "OPTIONAL": _strings,
    "GATED": _strings,
    "INTERNAL_ENV": _strings,
    "SETTINGS": _pattern(None, "a resource name"),
    "SETUP": _target,
    "SEGMENT": _pattern(_DOTTED, "a dotted module name"),
    "DAEMONS": _units("daemon"),
    "TIMERS": _units("timer"),
    "UNITS_FROM": _target,
    "HEALTH": _health,
    "DOCTOR": _target,
    "STORES": _stores,
    "BUDGETS": _budgets,
    "SKILLS": _skills,
    "PAGE": _pattern(_TARGET, '"package.module:NAME"'),
    "COMPONENTS": _components,
    "RESERVED_NAMES": _reserved,
    "TEARDOWN": _target,
    "SESSION_ENV": _session_env,
    "CONFIG_CHECK": _target,
    "QUESTIONS": _questions,
}


def _one_line(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _size(value) -> bool:
    return isinstance(value, str) and _SIZE.fullmatch(value) is not None


def _str_list(value) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)


def _core_store(path: str) -> bool:
    """Whether a relative store path names one of the core's stores in a state directory.

    Read with either separator and without `.` segments, and compared case-folded, since
    on macOS and Windows two spellings that differ only in case name one file.
    """
    parts = [p.casefold() for p in re.split(r"[\\/]", path) if p not in ("", ".")]
    if not parts:
        return False
    if parts[0] in CORE_STATE_DIRS:
        return True
    return len(parts) == 1 and parts[0] in CORE_STATE_FILES


def _relative(value) -> bool:
    """A non-empty relative path, read with either separator, with no '..' and no drive."""
    if not isinstance(value, str) or not value.strip():
        return False
    if value.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", value):
        return False
    return ".." not in re.split(r"[\\/]", value)


def _absolute(value: str) -> bool:
    import os.path

    return os.path.isabs(value)


def _ip(value: str) -> bool:
    return _ip_of(value) is not None


def _ip_of(value: str):
    """`value` as an `ipaddress` address, or None when it is not an IP literal."""
    import ipaddress

    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _freeze(value):
    """Tables as read-only mappings and lists as tuples, all the way down."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


# --- small helpers ------------------------------------------------------------------------

def _not_a_name(name: object) -> str:
    return (f"{name!r} is not a Moxi module name: lower-case ASCII letters and digits "
            "only")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dist_name(dist) -> str:
    if dist is None:
        return ""
    try:
        return dist.metadata["Name"] or ""
    except Exception:  # noqa: BLE001
        return getattr(dist, "name", "") or ""


def _dist_label(ep) -> str:
    dist = getattr(ep, "dist", None)
    name = _dist_name(dist) or "an unnamed distribution"
    version = getattr(dist, "version", None)
    return f"{name} {version}" if version else name


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _debug(line: str) -> None:
    import logging

    logging.getLogger(__name__).debug("%s", line)


__all__ = ["GROUP", "PRESENT", "ABSENT", "BROKEN", "ANNOUNCES", "KNOWN", "Entry", "Found",
           "Refused", "Extension", "ExtensionError", "installed", "present", "available",
           "load", "scan", "extensions", "session_env", "reset"]
