"""The rows services lists, in their three forms, and the one identifier that names them.

A row is a daemon or a timer. It comes in one of three forms:

- module: a daemon or timer an installed module declares (`DAEMONS`, `TIMERS` or its
  run-time declarations). The core's installer writes its unit; services never stores
  one. `module_rows()` reads them.
- written: a command the user added (`moxi services add NAME -- ARGV...`). services keeps
  the row in its store, and the core's writer makes its unit from the row
  (`moxi_services.units.declared`).
- adopted: a unit already on the system that the user took into the list
  (`moxi services add NAME --unit REF`). Its own file is never written.

Every row has one identifier, `<module>/<name>`. A user row, written or adopted, is
`services/<name>`, and a bare `<name>` names one too. Any other `<module>/<name>` names a
module row. A user row's name is lower-case letters and digits in words joined by single
hyphens, the rule the core holds unit names to, and it is unique among user rows whatever
their project, since it becomes the unit's name (`moxi-services-<name>`).

This module checks names, ids, foreign unit names and the fields of a user row; it reads
no store and calls no supervisor. `module_rows` reads the modules' declarations only.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping

from .errors import Refused, Usage

#: The module whose units are the user's rows.
MODULE = "services"
#: The forms a stored row takes; module rows are never stored.
FORMS = ("written", "adopted")
#: What a row runs as.
KINDS = ("daemon", "timer")
#: The systems a row may be added on, as `sys.platform` names them.
PLATFORMS = ("linux", "darwin", "win32")
#: The days a timer's `at` may be limited to.
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_MODULE = re.compile(r"[a-z0-9]+")
_EVERY = re.compile(r"([0-9]+)([mh])")
_AT = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
#: The prefixes of moxi's own unit names: systemd `moxi-*`, launchd `moxi.*`, and the
#: Task Scheduler folder `\Moxi\`.
_OWN_PREFIXES = ("moxi-", "moxi.")
_OWN_FOLDER = "\\moxi\\"


# --- names and ids ----------------------------------------------------------------------

def is_name(name: object) -> bool:
    """True when `name` is a valid user row name."""
    return isinstance(name, str) and _NAME.fullmatch(name) is not None


def check_name(name: object) -> str:
    """`name` when it is a valid user row name; else `Usage` (exit 2) saying the rule."""
    if not is_name(name):
        raise Usage(f"{name!r} is not a service name: a name is lower-case letters and "
                    "digits, in words joined by single hyphens (such as portfolio-web)",
                    None)
    return name


def row_id(module: str, name: str) -> str:
    """The identifier of a row: `<module>/<name>`."""
    return f"{module}/{name}"


def parse_id(text: object) -> tuple[str, str]:
    """`(module, name)` for an id: `<module>/<name>`, `services/<name>` or a bare
    `<name>` (a user row). Anything else is `Refused` (exit 3), as an unknown id is."""
    if isinstance(text, str):
        module, slash, name = text.rpartition("/")
        if not slash:
            module = MODULE
        if _MODULE.fullmatch(module) and _NAME.fullmatch(name):
            return module, name
    raise Refused(f"{text!r} is not a service id: an id is <module>/<name>, "
                  f"{MODULE}/<name> or a bare <name>",
                  "`moxi services list` shows every row's id")


def is_user(module: str) -> bool:
    """True when an id's module half names a user row."""
    return module == MODULE


# --- foreign units ----------------------------------------------------------------------

def is_own_ref(ref: str) -> bool:
    """True when `ref` is in moxi's own unit namespace on any system: a systemd unit
    named `moxi-*`, a launchd label `moxi.*`, or a task under `\\Moxi\\`."""
    path = ref if ref.startswith("\\") else "\\" + ref
    return ref.startswith(_OWN_PREFIXES) or path.casefold().startswith(_OWN_FOLDER)


def check_ref(ref: object, backend: str, *, timer: bool = False) -> str:
    """The kind, `daemon` or `timer`, of a foreign unit `ref` an adopted row would name,
    on the supervisor `backend` ("systemd", "launchd" or "schtasks").

    A systemd unit takes its kind from its suffix, `.service` or `.timer`; `timer`
    (the `--timer` flag) must agree with it. On launchd a label is a daemon unless
    `timer` says it is a timer. A Task Scheduler task is always a timer. A ref in moxi's
    own namespace is refused (exit 3): those units are the core's, marked or not."""
    if not isinstance(ref, str) or not ref.strip() or ref != ref.strip() or "\n" in ref:
        raise Usage(f"{ref!r} is not a unit name", None)
    if is_own_ref(ref):
        raise Refused(f"{ref} is in moxi's own unit namespace; adopt only a unit moxi did "
                      "not write", "a module's own units are listed as its rows already")
    if backend == "systemd":
        if ref.endswith(".service"):
            kind = "daemon"
        elif ref.endswith(".timer"):
            kind = "timer"
        else:
            raise Refused(f"{ref} is not a .service or a .timer; services adopts those two",
                          None)
        if timer and kind != "timer":
            raise Usage(f"{ref} is a service, not a timer; leave out --timer", None)
        return kind
    if backend == "launchd":
        return "timer" if timer else "daemon"
    if backend == "schtasks":
        return "timer"
    raise Refused(f"no supervisor here ({backend!r}) to adopt {ref} from", None)


# --- a user row's fields ----------------------------------------------------------------

def _str_list(value: object) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)


def _absolute(path: str) -> bool:
    import ntpath
    import posixpath

    return posixpath.isabs(path) or (ntpath.isabs(path) and bool(ntpath.splitdrive(path)[0]))


def _one_line(value: object) -> bool:
    return isinstance(value, str) and "\n" not in value and "\r" not in value


def check_schedule(every: object = None, at: object = None, days: object = None) -> None:
    """A written timer's schedule in the core's form, or `Usage` (exit 2): exactly one of
    `every` (`<n>m` or `<n>h`, from 1m to 24h) and `at` (a list of `HH:MM`), and `days`
    (a list drawn from mon to sun) only with `at`."""
    if (every is None) == (at is None):
        raise Usage("a timer takes exactly one of --every and --at", None)
    if every is not None:
        match = _EVERY.fullmatch(every) if isinstance(every, str) else None
        minutes = (int(match.group(1)) * (60 if match.group(2) == "h" else 1)
                   if match else 0)
        if not 1 <= minutes <= 24 * 60:
            raise Usage(f"--every {every!r}: give <n>m or <n>h, from 1m to 24h", None)
        if days is not None:
            raise Usage("--days goes with --at, not --every", None)
        return
    if not _str_list(at) or not at or not all(_AT.fullmatch(t) for t in at):
        raise Usage(f"--at {at!r}: give one or more 24-hour times, HH:MM[,HH:MM...]", None)
    if days is not None and (not _str_list(days) or not days or not set(days) <= set(DAYS)):
        raise Usage(f"--days {days!r}: give days drawn from {','.join(DAYS)}", None)


def check_fields(fields: Mapping[str, object]) -> None:
    """Check a user row's fields as the store holds them (lists as lists, flags as
    bools) before it is written. Raises `Usage` (exit 2) naming the first field at
    fault.

    A written row has `argv`, a non-empty list whose first item is an absolute path, an
    optional absolute `cwd`, and for a timer its schedule. An adopted row has `unit` and
    none of those. Either may have `health`, a non-empty list, `project`, `doc` (one
    line) and `launches_session`; `platform` is one of `PLATFORMS`."""
    get = fields.get
    check_name(get("name"))
    form, kind = get("form"), get("kind")
    if form not in FORMS:
        raise Usage(f"form {form!r}: a stored row is written or adopted", None)
    if kind not in KINDS:
        raise Usage(f"kind {kind!r}: a row is a daemon or a timer", None)
    schedule = {k: get(k) for k in ("every", "at", "days")}
    if form == "written":
        argv = get("argv")
        if not _str_list(argv) or not argv or not argv[0] or not _absolute(argv[0]):
            raise Usage(f"argv {argv!r}: a written row's command is a list whose first "
                        "item is an absolute path", None)
        cwd = get("cwd")
        if cwd is not None and not (isinstance(cwd, str) and _absolute(cwd)):
            raise Usage(f"cwd {cwd!r}: a working directory is an absolute path", None)
        if get("unit") is not None:
            raise Usage("a written row names no unit; --unit adopts one", None)
        if kind == "timer":
            check_schedule(**schedule)
        elif any(v is not None for v in schedule.values()):
            raise Usage("a daemon takes no schedule; add --timer for a timer", None)
    else:
        unit = get("unit")
        if not isinstance(unit, str) or not unit or not _one_line(unit):
            raise Usage(f"unit {unit!r}: an adopted row names its unit", None)
        for key in ("argv", "cwd", *schedule):
            if get(key) is not None:
                raise Usage(f"an adopted row has no {key}: the unit keeps its own", None)
    health = get("health")
    if health is not None and (not _str_list(health) or not health or not health[0]):
        raise Usage(f"health {health!r}: a health command is a non-empty list", None)
    project = get("project")
    if project is not None and not (isinstance(project, str) and project
                                    and _one_line(project)):
        raise Usage(f"project {project!r}: a project key is one line", None)
    if not _one_line(get("doc", "")):
        raise Usage("doc: a row's doc is one line", None)
    if get("platform") not in PLATFORMS:
        raise Usage(f"platform {get('platform')!r}: one of {', '.join(PLATFORMS)}", None)


# --- the records ------------------------------------------------------------------------

class _Record:
    """A plain read-only record: fields set once, compared and shown by value."""

    __slots__ = ()
    _fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        values = dict(zip(self._fields, args))
        values.update(kwargs)
        for name in self._fields:
            object.__setattr__(self, name, values[name])

    def __setattr__(self, name, value):
        raise AttributeError(f"{type(self).__name__} is read-only")

    def __delattr__(self, name):
        raise AttributeError(f"{type(self).__name__} is read-only")

    def _values(self) -> tuple:
        return tuple(getattr(self, name) for name in self._fields)

    def __eq__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._values() == other._values()

    def __hash__(self):
        return hash(self._values())

    def __repr__(self):
        inner = ", ".join(f"{name}={getattr(self, name)!r}" for name in self._fields)
        return f"{type(self).__name__}({inner})"

    def as_dict(self) -> dict[str, object]:
        """The fields, lists as lists, for `--json`."""
        return {name: (list(v) if isinstance(v, tuple) else v)
                for name, v in zip(self._fields, self._values())}


class UserRow(_Record):
    """A written or adopted row as the store holds it.

    `argv`, `at`, `days` and `health` are tuples (or None); `enabled`, `removing` and
    `launches_session` are bools; `stopped`, `created` and `changed` are UTC times
    (`YYYY-MM-DDTHH:MM:SSZ`), `stopped` None unless the row was stopped by hand."""

    __slots__ = _fields = (
        "name", "form", "kind", "argv", "cwd", "unit", "every", "at", "days", "enabled",
        "removing", "stopped", "health", "project", "launches_session", "platform", "doc",
        "created", "changed", "changed_by")

    @property
    def module(self) -> str:
        return MODULE

    @property
    def id(self) -> str:
        return row_id(MODULE, self.name)

    @property
    def here(self) -> bool:
        """True when the row was added on this system."""
        return self.platform == sys.platform

    def as_dict(self) -> dict[str, object]:
        out = super().as_dict()
        out["id"] = self.id
        return out


class ModuleRow(_Record):
    """A daemon or timer an installed module declares. `switch` is its settings key, or
    None when it declares none; `platforms` the systems it is limited to, or None."""

    __slots__ = _fields = ("module", "name", "kind", "switch", "doc", "platforms")

    form = "module"

    @property
    def id(self) -> str:
        return row_id(self.module, self.name)

    def as_dict(self) -> dict[str, object]:
        out = super().as_dict()
        out["id"] = self.id
        out["form"] = self.form
        return out


def _tuple(text: str | None) -> tuple[str, ...] | None:
    return None if text is None else tuple(json.loads(text))


def from_sql(row: tuple) -> UserRow:
    """A `UserRow` from a `SELECT` of `COLUMNS` in their order."""
    values = dict(zip(UserRow._fields, row))
    for key in ("argv", "at", "days", "health"):
        values[key] = _tuple(values[key])
    for key in ("enabled", "removing", "launches_session"):
        values[key] = bool(values[key])
    return UserRow(**values)


#: The store's columns in `UserRow`'s order.
COLUMNS = UserRow._fields


# --- module rows ------------------------------------------------------------------------

def _entries(module: str, daemons: Mapping, timers: Mapping) -> list[ModuleRow]:
    rows = []
    for kind, table in (("daemon", daemons), ("timer", timers)):
        for name, entry in table.items():
            platforms = entry.get("platforms")
            rows.append(ModuleRow(module=module, name=name, kind=kind,
                                  switch=entry.get("switch"), doc=entry.get("doc", ""),
                                  platforms=tuple(platforms) if platforms else None))
    return rows


def module_rows(modules: Iterable[str] | None = None
                ) -> tuple[list[ModuleRow], list[tuple[str, str]]]:
    """Every daemon and timer the installed modules declare (or those of `modules`), by
    id, and the problems met as `(module, reason)` pairs.

    services' own declarations are the user's written rows, so they are never module
    rows. A module that won't load is skipped (the core's doctor reports it). When a
    module's `UNITS_FROM` fails, its `DAEMONS` and `TIMERS` are still listed and the
    failure is a problem; a run-time entry the core rejected is left out (the core's
    doctor shows why)."""
    import moxi.modules
    import moxi.units

    names = sorted({e.name for e in moxi.modules.installed()} if modules is None
                   else set(modules))
    rows: list[ModuleRow] = []
    problems: list[tuple[str, str]] = []
    for module in names:
        if module == MODULE:
            continue
        found = moxi.modules.load(module)
        if found.state != moxi.modules.PRESENT:
            continue
        if not found.daemons and not found.timers and found.units_from is None:
            continue
        try:
            declared = moxi.units.declarations(module)
        except moxi.units.DeclarationError as exc:
            problems.append((module, str(exc)))
            rows += _entries(module, found.daemons, found.timers)
            continue
        except ValueError:
            continue
        rows += _entries(module, declared.daemons, declared.timers)
    rows.sort(key=lambda r: (r.module, r.name))
    return rows, problems


__all__ = [
    "MODULE", "FORMS", "KINDS", "PLATFORMS", "DAYS", "COLUMNS", "UserRow", "ModuleRow",
    "is_name", "check_name", "row_id", "parse_id", "is_user", "is_own_ref", "check_ref",
    "check_schedule", "check_fields", "from_sql", "module_rows"]
