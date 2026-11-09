"""What the supervisor says of each row, a row's on/off, its health and its run lines.

Every read here goes through the core's writer, `moxi.units`, and only reads: the
supervisor is asked once per call to `read`, whatever the rows. A module row and a
written row are read as their module's units (`status([module])`; a written row's units
are module `services`'), an adopted row as a foreign unit (`status(foreign=[REF])`). A
row added on another system is not read at all: no unit of it can be here.

A row's on/off has three homes:

- a module row's switch, the settings key its declaration names (on when it names none);
- an adopted Linux timer's entry in services' own `adopted` switch table, since the core's
  drop-in puts its runs behind that key (`is_wrapped`);
- the store's flag for every other user row.

Health runs only when asked, and only for a running daemon: a module daemon's declared
`HEALTH` through the core's doctor, a user row's own command through `moxi.proc.run`
with five seconds. Its exit code is the verdict and the first line of its output is
shown as text; nothing else of the output is read.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable, Mapping

import moxi.config
import moxi.doctor
import moxi.modules
import moxi.proc
from moxi import units

from .rows import MODULE, ModuleRow, UserRow

#: services' switch table: one bool entry per adopted Linux timer, by its row's name.
TABLE = "adopted"
#: How long a user row's health command may run, in seconds.
HEALTH_SECONDS = 5

Row = UserRow | ModuleRow

_schema: list[moxi.config.Schema] = []


def key(name: str) -> str:
    """The settings key that turns adopted Linux timer `name`'s runs on and off."""
    return f"{TABLE}.{name}"


def is_wrapped(row: Row) -> bool:
    """True for an adopted Linux timer. Such a timer is wrapped by the core's drop-in,
    which runs it through the gate behind `key(name)`, or it is not adopted at all."""
    return (isinstance(row, UserRow) and row.form == "adopted" and row.kind == "timer"
            and row.platform == "linux")


def schema() -> moxi.config.Schema:
    """services' own config schema."""
    if not _schema:
        _schema.append(moxi.config.Schema.from_resource("moxi_services", "settings.toml"))
    return _schema[0]


def key_on(name: str) -> bool:
    """Whether adopted Linux timer `name`'s key is on (a missing entry is off). Raises
    `moxi.config.ConfigError` when services' config file can't be read."""
    return bool(moxi.config.load(MODULE, schema())[key(name)])


def switch_on(module: str, switch: str | None) -> bool | None:
    """Whether a module unit's `switch` is on: True when it declares none, None when the
    module's settings can't be read."""
    if switch is None:
        return True
    found = moxi.modules.load(module)
    if found.state != moxi.modules.PRESENT:
        return None
    try:
        settings_schema = moxi.config.schema_for(found)
        if settings_schema is None:
            return None
        return bool(moxi.config.load(module, settings_schema)[switch])
    except (moxi.config.ConfigError, KeyError):
        return None


def read(rows: Iterable[Row]) -> dict[str, units.UnitState | None]:
    """What the supervisor says of each row, by row id, in one `moxi.units.status` call.

    A row of another system, and a row the read did not cover, map to None. With no
    supervisor here every row read is `nobackend`."""
    return read_with_sockets(rows)[0]


def read_with_sockets(rows: Iterable[Row]
                      ) -> tuple[dict[str, units.UnitState | None],
                                 dict[str, units.UnitState]]:
    """`read`, and beside it the listening-socket state of each module daemon that
    declares a socket, by row id. Such a daemon reads `inactive` while it is idle under
    its socket, so its verdict needs both."""
    rows = list(rows)
    modules: set[str] = set()
    foreign: list[str] = []
    kinds: dict[str, str] = {}
    for row in rows:
        if isinstance(row, ModuleRow):
            modules.add(row.module)
        elif not row.here:
            continue
        elif row.form == "written":
            modules.add(MODULE)
        elif row.unit not in foreign:
            foreign.append(row.unit)
            kinds[row.unit] = row.kind
    if not modules and not foreign:
        return {row.id: None for row in rows}, {}
    # Each adopted unit is read as its row's kind; only Task Scheduler reads the hint.
    states = units.status(sorted(modules), foreign=foreign, kinds=kinds)
    by_unit: dict[tuple[str | None, str], units.UnitState] = {}
    sockets: dict[tuple[str | None, str], units.UnitState] = {}
    for state in states:
        if state.kind == "socket":
            sockets.setdefault((state.module, state.name), state)
            continue
        by_unit.setdefault((state.module, state.name), state)
    out: dict[str, units.UnitState | None] = {}
    for row in rows:
        if isinstance(row, ModuleRow):
            out[row.id] = by_unit.get((row.module, row.name))
        elif not row.here:
            out[row.id] = None
        elif row.form == "written":
            out[row.id] = by_unit.get((MODULE, row.name))
        else:
            out[row.id] = by_unit.get((None, row.unit))
    found = {row.id: sockets[(row.module, row.name)] for row in rows
             if isinstance(row, ModuleRow) and (row.module, row.name) in sockets}
    return out, found


def read_one(row: Row) -> units.UnitState | None:
    """`read` for one row."""
    return read([row])[row.id]


def health(row: Row, state: units.UnitState | None) -> tuple[str, str] | None:
    """`(verdict, line)` of a row's health check, `ok` or `fail` (a module daemon's
    `HEALTH` may also give `warn` or `off`), or None when there is nothing to run: not a
    daemon, not `active`, or no check declared."""
    if row.kind != "daemon" or state is None or state.state != "active":
        return None
    if isinstance(row, ModuleRow):
        found = moxi.modules.load(row.module)
        if found.state != moxi.modules.PRESENT or row.name not in found.health:
            return None
        try:
            checked = moxi.doctor.health(row.module, row.name)
        except ValueError as exc:
            return "fail", str(exc)
        return checked.state, checked.detail
    if not row.health:
        return None
    return run_health(row.health)


def run_health(argv) -> tuple[str, str]:
    """Run a health command: exit 0 is `ok`, anything else `fail`, each with the first
    line of its output; past `HEALTH_SECONDS` it is `fail`, "did not finish in 5 s"."""
    try:
        done = moxi.proc.run(list(argv), timeout_s=HEALTH_SECONDS)
    except subprocess.TimeoutExpired:
        return "fail", f"did not finish in {HEALTH_SECONDS} s"
    except OSError as exc:
        return "fail", f"could not run {argv[0]}: {exc.strerror or exc}"
    line = _first_line(done.stdout) or _first_line(done.stderr)
    if done.returncode == 0:
        return "ok", line or "ok"
    return "fail", line or f"exit {done.returncode}"


def _first_line(output) -> str:
    if not output:
        return ""
    if isinstance(output, bytes):
        output = output.decode(sys.getdefaultencoding(), errors="replace")
    for line in output.splitlines():
        if line.strip():
            return line.strip()
    return ""


def last_runs(row: Row, n: int = 5) -> list[dict]:
    """A timer's newest `n` run lines from the gate, oldest first: a module timer's, a
    written timer's and an adopted Linux timer's (recorded as `services/<name>`). Every
    other row has none."""
    if row.kind != "timer":
        return []
    if isinstance(row, ModuleRow):
        return units.last_runs(row.module, row.name, n)
    if row.form == "written" or is_wrapped(row):
        return units.last_runs(MODULE, row.name, n)
    return []


def flag(row: Row, settings: Mapping[str, bool] | None = None) -> bool | None:
    """A row's on/off: a module row's switch, an adopted Linux timer's key, else the
    store's flag. None when the setting can't be read. `settings` may hold keys already
    read, by key."""
    if isinstance(row, ModuleRow):
        return switch_on(row.module, row.switch)
    if is_wrapped(row):
        if settings is not None and key(row.name) in settings:
            return settings[key(row.name)]
        try:
            return key_on(row.name)
        except moxi.config.ConfigError:
            return None
    return row.enabled


__all__ = [
    "TABLE", "HEALTH_SECONDS", "key", "is_wrapped", "schema", "key_on", "switch_on",
    "read", "read_with_sockets", "read_one", "health", "run_health", "last_runs", "flag"]
