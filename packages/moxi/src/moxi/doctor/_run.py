"""The doctor's runner: every check `moxi doctor` makes, and the command itself.

    moxi doctor [MODULE...] [--json] [--costs]
    moxi doctor --clear MODULE KEY

`run(modules)` makes the checks in this order and returns their rows:

     1. modules: the full scan (two claimants of one name, a misnamed distribution, a
        broken announcement, a too-new `ANNOUNCES`) and each module's required modules
        that are not installed;
     2. settings: each config file against its schema, as `moxi config <m> check` reads
        it, and each key set in a file whose schema says it `needs` an absent module;
     3. extensions: each entry of a group `moxi.<m>` or `moxi.<m>.<part>` whose owner `m`
        is absent (`off` when the offering module lists `m` in `OPTIONAL`);
     4. units: `moxi.units.plan` against what is written, the supervisor's state from one
        `moxi.units.status` call, each timer's switch and newest run line, each active
        daemon's `HEALTH`, a `HEALTH` key that names no daemon, and each wrap the module
        holds on a foreign timer (a daemon with a `socket`: see `_daemon_seen`);
     5. stores: each declared store's existence, mode and size cap, and the core's own
        stores in the module's state directory (`runs.jsonl`, `logs/`, `doctor.json`);
     6. home: a state or cache directory still read in place under a host folder;
     7. strays: each module installed where this moxi can't see it;
     8. setup versions: a module whose installed version differs from the one its `SETUP`
        last ran at;
     9. records left by `moxi.doctor.record`;
    10. placements: each placement `moxi.place` holds that is `stale`, `modified` or
        `missing` (a gone source reads "source missing: <path>"), and each harness row and
        `skills.harnesses` name the placer skips, on module `moxi`;
    11. the terminal record, on module `moxi`: whether a command typed here records the
        terminal's environment in the account's record, or what stops it, with the one
        step that fixes it (`terminal_row`);
    12. each `moxi.doctor` extension entry's rows;
    13. each module's `DOCTOR` rows;
    14. with `costs=True`, each module's active daemons against its `BUDGETS`.

Module names restrict the rows to those modules; `moxi` names the core's own rows. A
`DOCTOR` target and a `moxi.doctor` entry run in a worker thread with `DOCTOR_SECONDS`
each, a `HEALTH` target with `HEALTH_SECONDS`; one that overruns, raises or returns
another type is a `fail` row naming it, and a thread that overran is left to finish on
its own (a daemon thread, its result dropped).

The checks only read (the placements too: the doctor never places or removes one), with one
exception: `--costs` appends one sample per measured
daemon to `STATE/moxi/costs.jsonl`, the history its idle CPU rate is taken against.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from . import RECORDS_FILE, Row

#: Seconds a `DOCTOR` target or a `moxi.doctor` entry gets.
DOCTOR_SECONDS = 10.0
#: Seconds a `HEALTH` target gets.
HEALTH_SECONDS = 5.0
#: The core's own extension groups, which row 3 never reports.
CORE_GROUPS = frozenset({"moxi.modules", "moxi.installs", "moxi.doctor"})
#: The gate's run lines and their cap, in a module's state directory.
RUNS_FILE = "runs.jsonl"
RUNS_CAP = 1_000_000
#: The gate's log files, in a module's state directory.
LOGS_DIR = "logs"
#: In the core's state directory: the cost samples. Each module's `SETUP` version is in
#: the settle step's file, `moxi._settle.setup_path()`.
COSTS_FILE = "costs.jsonl"
#: How old a cost sample must be to take a rate against, and the age preferred.
SAMPLE_MIN_AGE = 600.0
SAMPLE_AGE = 3600.0
#: How many interpreter starts the floor is the median of.
FLOOR_RUNS = 5

_RANK = {"ok": 0, "off": 1, "warn": 2, "fail": 3}
_SIZES = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}


# -- the run -----------------------------------------------------------------------------------

def run(modules: Iterable[str] | None = None, *, costs: bool = False,
        runner=None) -> list[Row]:
    """Every check, in order, for `modules` (every module and the core when None).

    `runner` is the supervisor runner `moxi.units.status` is given (tests pass a fake).
    """
    ctx = _Context(modules, runner)
    rows: list[Row] = []
    failed: list[Row] = []
    checks = [_check_modules, _check_settings, _check_extensions, _check_units,
              _check_stores, _check_home, _check_strays, _check_setup, _check_records,
              _check_placements, _check_terminal, _check_entries, _check_doctors]
    if costs:
        checks.append(_check_costs)
    for check in checks:
        try:
            rows += check(ctx)
        except Exception as exc:  # noqa: BLE001 - one broken check never hides the others
            name = check.__name__.removeprefix("_check_")
            failed.append(Row("moxi", f"check {name}", "fail",
                              f"the doctor's {name} check failed: {_describe(exc)}"))
    return [row for row in rows if ctx.wanted(row.module)] + failed


class _Context:
    """What the checks of one run share: the full scan, the selection, and the units'
    states once row 4 has read them."""

    def __init__(self, selected: Iterable[str] | None, runner):
        from .. import modules

        self.runner = runner
        self.selected = None if selected is None else list(dict.fromkeys(selected))
        self.scan = modules.scan()
        self.found = {found.name: found for found in self.scan}
        self.present = [found for found in self.scan
                        if found.state == modules.PRESENT and self.wanted(found.name)]
        self.states: list = []

    def wanted(self, module: str) -> bool:
        return self.selected is None or module in self.selected

    def is_present(self, name: str) -> bool:
        from .. import modules

        found = self.found.get(name)
        return found is not None and found.state == modules.PRESENT


# -- 1. modules --------------------------------------------------------------------------------

def _check_modules(ctx: _Context) -> list[Row]:
    from .. import cli, names

    rows = []
    for found in ctx.scan:
        if not ctx.wanted(found.name):
            continue
        if found.state == "present":
            rows.append(Row(found.name, "module", "ok",
                            f"present: {found.distribution} {found.version}"))
        else:
            rows.append(Row(found.name, "module", "fail", found.reason or found.state))
        if not found.distribution:
            continue
        for req in cli.requirements(found.distribution):
            dist = cli.normalize(req.name)
            module = dist.removeprefix(f"{names.HARNESS}-")
            if dist == module or not names.is_module_name(module):
                continue
            if cli._distribution(req.name) is None:
                rows.append(Row(found.name, f"requires {module}", "fail",
                                f"{found.name} requires {cli.requirement_text(req)}, "
                                "which is not installed", f"moxi install {module}"))
    if ctx.selected is not None:
        for name in ctx.selected:
            if name != names.HARNESS and name not in ctx.found:
                rows.append(Row(name, "module", "fail",
                                "not installed in this environment", _install_line(name)))
    return rows


def _install_line(module: str) -> str:
    from .. import cli

    return cli.install_hint(module).removeprefix("install it: ")


# -- 2. settings -------------------------------------------------------------------------------

def _check_settings(ctx: _Context) -> list[Row]:
    from .. import config, names

    targets = []
    if ctx.wanted(names.HARNESS):
        targets.append((names.HARNESS, config._core_schema, None))
    targets += [(found.name, (lambda f=found: config.schema_for(f)), found.config_check)
                for found in ctx.present]
    rows = []
    for module, schema_of, check in targets:
        try:
            schema = schema_of()
        except config.ConfigError as exc:
            rows.append(Row(module, "settings", "fail",
                            f"its settings schema cannot be read: {_problem(exc)}", exc.fix))
            continue
        if schema is None:
            continue
        try:
            config._check_config(module, schema, check)
            settings = config.load(module, schema)
        except config.ConfigError as exc:
            rows.append(Row(module, "settings", "fail", _problem(exc), exc.fix))
            continue
        path = settings.file
        rows.append(Row(module, "settings", "ok",
                        f"{path} reads clean" if path is not None and Path(path).exists()
                        else "no config file: every key at its default"))
        for key in schema.live():
            needs = key.needs
            if (needs and not ctx.is_present(needs)
                    and settings.source(key.name) == config.FILE):
                rows.append(Row(module, f"setting {key.name}", "warn",
                                f"{key.name} is set, and it works only with {needs}, "
                                "which is not installed",
                                f"{_install_line(needs)}, or: moxi config unset {module} "
                                f"{key.name}"))
    return rows


def _problem(exc) -> str:
    """A `ConfigError`'s text without its fix, which the row carries apart."""
    text = str(exc)
    return text.rsplit("; fix: ", 1)[0] if exc.fix else text


# -- 3. extensions -----------------------------------------------------------------------------

def _check_extensions(ctx: _Context) -> list[Row]:
    from importlib.metadata import entry_points

    from .. import cli, modules, names

    groups = sorted(g for g in entry_points().groups if g.startswith(f"{names.HARNESS}."))
    rows = []
    for group in groups:
        if group in CORE_GROUPS or not modules._GROUP.fullmatch(group):
            continue
        owner = group.split(".")[1]
        if owner == names.HARNESS or owner in ctx.found:
            continue
        for ext in modules.extensions(group):
            offering = ctx.found.get(ext.module)
            optional = offering is not None and any(
                cli.normalize(extra) == owner for extra in offering.optional)
            label = f"extension {group} {ext.name}"
            if optional:
                rows.append(Row(ext.module, label, "off",
                                f"an optional link to {owner}, which is not installed",
                                f"moxi install {ext.module} --with {owner}"))
            else:
                rows.append(Row(ext.module, label, "warn",
                                f"its owner {owner} is not installed, so nothing reads it",
                                _install_line(owner)))
    return rows


# -- 4. units ----------------------------------------------------------------------------------

def _check_units(ctx: _Context) -> list[Row]:
    from .. import units

    present = [found.name for found in ctx.present]
    if not present:
        return []
    wraps: dict[str, Mapping] = {}
    for module in present:
        try:
            wraps[module] = units.wraps(module)
        except Exception:  # noqa: BLE001 - the module's rows say what was read
            wraps[module] = {}
    foreign = sorted({ref for held in wraps.values() for ref in held})
    failure = None
    try:
        states = units.status(present, foreign=foreign, runner=ctx.runner)
    except Exception as exc:  # noqa: BLE001 - every unit row still shows its plan
        states, failure = [], _describe(exc)
    ctx.states = states
    by_unit = {(s.module, s.name, s.kind): s for s in states if s.module is not None}
    by_ref = {s.name: s for s in states if s.module is None}
    rows = []
    for found in ctx.present:
        module = found.name
        if failure is not None:
            rows.append(Row(module, "units", "fail",
                            f"the supervisor's state could not be read: {failure}"))
        rows += _module_units(found, by_unit)
        for ref, wrapped in sorted(wraps[module].items()):
            rows += _wrap_rows(module, ref, wrapped, by_ref.get(ref))
    return rows


def _module_units(found, by_unit: Mapping) -> list[Row]:
    from .. import units

    module = found.name
    rows = []
    try:
        decl = units.declarations(module)
    except units.DeclarationError as exc:
        decl = None
        rows.append(Row(module, "UNITS_FROM", "fail", f"UNITS_FROM failed: {exc.text}",
                        f"fix {found.units_from}, then: moxi install {module}"))
    if decl is not None:
        for (kind, key), reason in sorted(decl.rejected.items()):
            if kind == "health":
                rows.append(Row(module, f"health {key}", "warn", reason,
                                "name a daemon of DAEMONS or UNITS_FROM in HEALTH"))
    try:
        changes = units.plan(module)
    except Exception as exc:  # noqa: BLE001
        rows.append(Row(module, "units", "fail", f"its units cannot be planned: "
                                                 f"{_describe(exc)}"))
        return rows
    daemons = decl.daemons if decl is not None else found.daemons
    socket_rows = {change.name for change in changes if change.kind == "socket"}
    for change in changes:
        state = by_unit.get((module, change.name, change.kind))
        socket = (by_unit.get((module, change.name, "socket"))
                  if change.kind == "daemon" else None)
        rows.append(_unit_row(change, state, _shared_fix(found, change, changes),
                              socket=socket, socket_row=change.name in socket_rows))
        if change.kind == "timer" and change.action in ("keep", "change"):
            label = f"timer {change.name}"
            if change.switch is not None:
                rows.append(_switch_row(found, label, change.switch))
            rows.append(_last_row(module, label, _run_line(state, module, change.name),
                                  state.log if state is not None else None))
        # Only a running daemon's HEALTH runs, so a daemon idle under its listening socket
        # has no health row until a connection starts it.
        if (change.kind == "daemon" and state is not None and state.state == "active"
                and change.name in daemons):
            rows.append(_health_row(module, change.name, found.health.get(change.name)))
    return rows


def _shared_fix(found, change, changes) -> str | None:
    """The fix for a new or changed switched timer whose switch other units also use, when
    that switch reads on: an install holds it then, so the fix turns the key off for the
    install and on again after. None for every other unit."""
    from .. import config
    from ..units import _apply

    if change.kind != "timer" or change.action not in ("add", "change"):
        return None
    if not _apply.sharers(change, changes):
        return None
    module, switch = found.name, change.switch
    try:
        on = bool(config.load(module, config.schema_for(found))[switch])
    except (config.ConfigError, KeyError, TypeError):
        return None
    if not on:
        return None
    return (f"moxi config set {module} {switch} false, then: moxi install {module}, then: "
            f"moxi config set {module} {switch} true")


def _unit_row(change, state, install: str | None = None, *, socket=None,
              socket_row: bool = True) -> Row:
    """The row for one planned unit, with the supervisor's state folded in where a unit
    is written. `install` replaces the plain `moxi install <module>` fix of a unit that is
    not installed or stale. `socket` is a daemon's socket state, None for a daemon with
    no `socket` and for every other kind; `socket_row` is False where the plan gives the
    socket no change of its own (launchd), so its state shows in the daemon's row
    (`_daemon_seen`).

    A change whose IPv6 addresses `plan` left out keeps that note in its row, and a
    socket change names the units it drops."""
    module = change.module
    label = f"{change.kind} {change.name}"
    install = install or f"moxi install {module}"
    action, cause = change.action, change.cause
    note = _left_out(change)
    if action == "keep":
        planned = ("ok", "up to date" + note, None)
    elif action == "add":
        planned = ("warn", "not installed" + note, install)
    elif action == "change":
        drops = (f" (drops {', '.join(path.name for path in change.dropped)})"
                 if change.dropped else "")
        planned = ("warn", _stale(change) + drops + note, install)
    elif action == "remove":
        planned = ("warn", "switched off, unit still installed" if cause == "switch"
                   else "no longer declared", install)
    elif action == "refuse" and cause == "editable":
        planned = ("ok", "editable install: units are not written here", None)
    elif action == "refuse" and cause == "foreign":
        what = change.foreign.name if change.foreign is not None else "its file"
        planned = ("warn", f"{what} exists and was not written by moxi",
                   f"moxi install {module} --replace")
    elif action == "refuse":
        planned = ("fail", change.reason, _fix_in(change.reason))
    elif action == "skip" and cause == "declaration":
        planned = ("fail", change.reason, None)
    elif action == "skip" and cause == "switch":
        planned = ("off", "switched off", f"moxi config set {module} {change.switch} true")
    else:
        planned = ("off", change.reason, None)
    if state is None or action not in ("keep", "change", "remove"):
        return Row(module, label, *planned)
    if change.kind == "daemon" and socket is not None:
        seen = _daemon_seen(change, state, socket, socket_row)
    else:
        seen = _state_of(change, state)
    worse = seen if _RANK[seen[0]] > _RANK[planned[0]] else planned
    return Row(module, label, worse[0], f"{planned[1]}; {seen[1]}", worse[2])


#: The end of the note `plan` adds to each change of a daemon whose IPv6 addresses it left
#: out, after "; " and the addresses.
LEFT_OUT = "left out: no IPv6 loopback on this system"


def _left_out(change) -> str:
    """`plan`'s note on a change whose IPv6 addresses were left out, with its "; ", or
    the empty string."""
    _, sep, tail = change.reason.rpartition("; ")
    return f"; {tail}" if sep and tail.endswith(LEFT_OUT) else ""


def _daemon_seen(change, state, socket, socket_row: bool) -> tuple[str, str, str | None]:
    """A daemon with a `socket`, read beside its socket's state. Like a timer between
    runs, a daemon whose socket listens is waiting, not down: its process `inactive`
    under a socket `active` is `ok`, "idle, socket listening". Every other pair reads the
    daemon by `_state_of`, so a daemon `failed` under a listening socket is still `fail`,
    and one `inactive` under a socket that isn't `active` is still `warn`.

    Where the socket has a row of its own (systemd, which plans its socket units as one
    `socket` change), its state shows there: a `failed` socket is a `fail` row naming
    each failed unit, with its `journalctl` line. Where it has none (launchd, which holds
    the sockets in the daemon's own job), its state shows here, folded in when it isn't
    `active`. On launchd that state is the job's: `active` while the label is loaded,
    since launchd then holds its sockets whether the daemon runs or not. launchd says
    nothing of a socket it could not bind, so its socket is never `failed` and no row
    reads a failed socket there. A socket daemon's label that is loaded with no pid and a
    non-zero last exit (killed, or crashed) reads `failed`, so this row is `fail`, and it
    stays so until its next connection starts it again. On Task Scheduler the socket is
    `off` (the daemon binds its own), and the daemon's row is any daemon's.

    On systemd, `moxi install` starts a socket daemon's service each time it writes it (a
    new daemon, a set that gains or loses a unit), idle or not, and stops and starts a
    running one around a changed set, so after an install the daemon can read `active`
    until it exits; on launchd it stays idle until a connection. Only an active daemon's
    `HEALTH` runs, so one idle under its socket has no health row.
    """
    if state.state == "inactive" and socket.state == "active":
        return "ok", "idle, socket listening", None
    seen = _state_of(change, state)
    if socket_row or socket.state in ("active", "off", "nobackend"):
        return seen
    held = _state_of(change, socket)
    worse = held if _RANK[held[0]] > _RANK[seen[0]] else seen
    return worse[0], f"{seen[1]}; its socket: {held[1]}", worse[2]


def _state_of(change, state) -> tuple[str, str, str | None]:
    """The supervisor's state of a written unit as a row's state, detail and fix:
    `active` ok, `failed` fail, `inactive` warn (ok for a unit declared `enabled = false`),
    `missing` warn with the install line, `unknown` warn. A timer's state is its schedule
    on every backend, so a timer whose last run failed is `active` here; that run shows
    only in its run-line row. A systemd socket's `failed` and `inactive` name the units
    that are so, from its `detail`, and `failed` gives its `journalctl` line."""
    what = state.state
    if state.kind == "socket" and what in ("failed", "inactive") and state.detail:
        if what == "failed":
            return "fail", state.detail, state.log
        if not change.enabled:
            return "ok", f"inactive: declared enabled = false ({state.detail})", None
        detail = f"inactive ({state.detail})" + (
            ", not enabled" if state.enabled is False else "")
        return "warn", detail, state.log
    if what == "active":
        detail = "active"
        if state.since:
            detail += " since " + time.strftime("%Y-%m-%d %H:%M",
                                                time.localtime(state.since))
        if state.pid and change.kind == "daemon":
            detail += f", pid {state.pid}"
        return "ok", detail, None
    if what == "failed":
        return "fail", "failed" + (f" ({state.detail})" if state.detail else ""), state.log
    if what == "inactive":
        if not change.enabled:
            return "ok", "inactive: declared enabled = false", None
        detail = "inactive" + (", not enabled" if state.enabled is False else "")
        return "warn", detail, state.log
    if what == "missing":
        return ("warn", "written, and the supervisor has not loaded it",
                f"moxi install {change.module}")
    if what == "unknown":
        return "warn", f"its state is unknown: {state.detail}", None
    return "off", state.detail or what, None


def _fix_in(reason: str) -> str | None:
    """The fix a refusal's reason carries after `fix:`, if it does."""
    _, sep, fix = reason.rpartition("; fix: ")
    return fix.strip() or None if sep else None


def _stale(change) -> str:
    """"stale", or for a unit whose written port differs from its setting, which port is
    written and which the setting says."""
    socket = change.entry.get("socket") if change.entry else None
    if not socket:
        return "stale"
    try:
        from .. import config, modules

        found = modules.load(change.module)
        port = config.load(change.module, config.schema_for(found))[socket["setting"]]
    except Exception:  # noqa: BLE001 - the plain word is still true
        return "stale"
    written = _written_port(change, port)
    if written is None or written == str(port):
        return "stale"
    return f"port {written} written, setting says {port}"


def _written_port(change, port) -> str | None:
    """The port in the unit's files where the new text has `port`: the old line that
    matches the new one with the port's digits replaced."""
    import re

    digits = str(port)
    for path, new in zip(change.paths, change.texts):
        try:
            old = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        for old_line, new_line in zip(old.splitlines(), new.splitlines()):
            if old_line == new_line or digits not in new_line:
                continue
            pattern = re.escape(new_line).replace(digits, r"(\d+)")
            match = re.fullmatch(pattern, old_line)
            if match:
                return match.group(1)
    return None


def _switch_row(found, label: str, switch: str) -> Row:
    """A timer's (or a wrap's) switch: `ok` on, `off` with the line that turns it on."""
    from .. import config

    module = found.name
    turn_on = f"moxi config set {module} {switch} true"
    try:
        schema = config.schema_for(found)
        on = bool(config.load(module, schema)[switch]) if schema is not None else None
    except (config.ConfigError, KeyError) as exc:
        return Row(module, f"{label} switch", "fail",
                   f"its switch {switch} cannot be read: {_describe(exc)}")
    if on is None:
        return Row(module, f"{label} switch", "fail",
                   f"its switch {switch} names a setting, and {module} has no settings")
    if on:
        return Row(module, f"{label} switch", "ok", f"{switch} is on")
    return Row(module, f"{label} switch", "off", f"{switch} is off, so its runs do nothing",
               turn_on)


def _run_line(state, module: str, name: str) -> dict | None:
    """The newest run line of `<module>/<name>`: from the state `status` read, else from
    the gate's file."""
    if state is not None and state.last is not None:
        line = {k: v for k, v in state.last.items() if k != "supervisor"}
        return line or None
    from .. import units

    try:
        found = units.last_runs(module, name)
    except (OSError, ValueError):
        return None
    return found[-1] if found else None


def _last_row(module: str, label: str, line: Mapping | None, log: str | None) -> Row:
    """A timer's newest run line as a row."""
    name = f"{label} last run"
    if not line:
        return Row(module, name, "ok", "no run yet")
    at = line.get("at", "an unknown time")
    result, code = line.get("result"), line.get("exit")
    if result == "ran" and code == 0:
        seconds = line.get("seconds")
        took = f" in {seconds} s" if isinstance(seconds, (int, float)) else ""
        return Row(module, name, "ok", f"ran at {at}, exit 0{took}")
    if result == "ran":
        return Row(module, name, "warn", f"ran at {at}, exit {code}", log)
    if result == "off":
        return Row(module, name, "ok", f"at {at}: switched off, so it did not run")
    if result == "unreadable":
        return Row(module, name, "fail",
                   f"at {at}: its config file could not be read, so it did not run",
                   f"moxi config {module} check")
    if result == "not-started":
        return Row(module, name, "fail", f"at {at}: the command did not start", log)
    return Row(module, name, "warn", f"at {at}: result {result!r}, exit {code}", log)


def _wrap_rows(module: str, ref: str, wrapped, state) -> list[Row]:
    """A wrap on a foreign timer, as a written timer's rows: the timer's state, its key
    and the newest run line of the wrap's record name."""
    from .. import modules

    label = f"wrap {ref}"
    if state is None:
        rows = [Row(module, label, "warn", "its state was not read")]
    elif state.state == "active":
        rows = [Row(module, label, "ok", f"wraps {ref} as {module}/{wrapped.name}; "
                                         "the timer is active")]
    elif state.state == "failed":
        rows = [Row(module, label, "fail", f"{ref} failed", state.log)]
    elif state.state == "missing":
        rows = [Row(module, label, "warn",
                    f"{ref} is not loaded; its drop-in is still there",
                    "load the timer again, or remove the wrap with moxi.units.unwrap")]
    elif state.state == "inactive":
        rows = [Row(module, label, "warn", f"{ref} is inactive, so nothing runs it",
                    state.log)]
    else:
        rows = [Row(module, label, "warn", f"{ref}: {state.state} ({state.detail})")]
    rows.append(_switch_row(modules.load(module), label, wrapped.key))
    line = None
    if state is not None and state.last is not None:
        line = {k: v for k, v in state.last.items() if k != "supervisor"} or None
    else:
        line = _run_line(None, module, wrapped.name)
    rows.append(_last_row(module, label, line, state.log if state is not None else None))
    return rows


# -- HEALTH ------------------------------------------------------------------------------------

def health(module: str, daemon: str) -> Row:
    """Run daemon `daemon`'s `HEALTH` target of `module` (see `moxi.doctor.health`)."""
    from .. import modules, units

    found = modules.load(module)
    if found.state != modules.PRESENT:
        raise ValueError(f"{module} is {found.state}"
                         + (f": {found.reason}" if found.reason else ""))
    try:
        declared = units.declarations(module).daemons
    except units.DeclarationError as exc:
        if daemon not in found.daemons:
            raise ValueError(f"{module} declares no daemon {daemon!r} in DAEMONS, and its "
                             f"UNITS_FROM failed: {exc.text}") from None
        declared = found.daemons
    if daemon not in declared:
        raise ValueError(f"{module} declares no daemon {daemon!r}")
    return _health_row(module, daemon, found.health.get(daemon))


def _health_row(module: str, daemon: str, target: str | None) -> Row:
    """One daemon's `HEALTH` as the row the doctor shows, run in a worker thread."""
    label = f"daemon {daemon} health"
    if target is None:
        return Row(module, label, "ok", "no health check declared")

    def check():
        value = _load(target)()
        if not isinstance(value, Row):
            raise _WrongType(f"returned {type(value).__name__}, not a moxi.doctor.Row")
        return value

    outcome, value = _call(check, HEALTH_SECONDS, f"health {module}/{daemon}")
    if outcome != "ok":
        return Row(module, label, "fail", _failure(target, outcome, value, HEALTH_SECONDS))
    return Row(module, label, value.state, value.detail, value.fix)


# -- 5. stores ---------------------------------------------------------------------------------

def _check_stores(ctx: _Context) -> list[Row]:
    rows = []
    for found in ctx.present:
        rows += store_rows(found)
    return rows


def store_rows(found) -> list[Row]:
    """Module `found`'s declared stores, then the core's stores in its state directory
    that exist."""
    from .. import paths

    layout = paths.Layout(found.name)
    rows = []
    for name, entry in found.stores.items():
        path = _root_dir(layout, entry["root"]) / entry["path"]
        rows.append(_store_row(found.name, f"store {name}", path, kind=entry["kind"],
                               private=entry.get("private", True),
                               lazy=entry.get("lazy", False),
                               cap=_size_cap(entry.get("retention", "keep"))))
    state = layout.state_dir()
    for name, kind, cap in ((RUNS_FILE, "jsonl", RUNS_CAP), (LOGS_DIR, "dir", None),
                            (RECORDS_FILE, "file", None)):
        path = state / name
        if os.path.lexists(path):
            rows.append(_store_row(found.name, f"store {name}", path, kind=kind,
                                   private=True, lazy=False, cap=cap))
    return rows


def _root_dir(layout, root: str) -> Path:
    return {"data": layout.data_dir, "state": layout.state_dir, "cache": layout.cache_dir,
            "runtime": layout.runtime_dir}[root]()


def _size_cap(retention) -> int | None:
    """A size retention in bytes; None for `keep` and an age."""
    if not isinstance(retention, str) or not retention or retention == "keep":
        return None
    if retention.endswith("d"):
        return None
    return _size_bytes(retention)


def _size_bytes(size: str) -> int | None:
    unit = size[-1] if size[-1:] in ("K", "M", "G") else ""
    digits = size[:-1] if unit else size
    return int(digits) * _SIZES[unit] if digits.isdigit() else None


def _store_row(module: str, label: str, path: Path, *, kind: str, private: bool,
               lazy: bool, cap: int | None) -> Row:
    import shlex
    import stat

    from .. import cli

    try:
        st = os.stat(path)
    except FileNotFoundError:
        if lazy:
            return Row(module, label, "ok", f"{path}: not created yet")
        return Row(module, label, "warn", f"{path} is missing")
    except OSError as exc:
        return Row(module, label, "warn", f"{path} cannot be read: {exc}")
    size = st.st_size
    if kind == "dir" and stat.S_ISDIR(st.st_mode):
        size = _tree_size(path)
    problems, fix = [], None
    mode = stat.S_IMODE(st.st_mode)
    if private and os.name == "posix" and mode & 0o077:
        problems.append(f"others can read it (mode {mode:04o})")
        fix = f"chmod {700 if stat.S_ISDIR(st.st_mode) else 600} {shlex.quote(str(path))}"
    if cap is not None and size > cap:
        problems.append(f"{cli._size_text(size)} is past its cap of {cli._size_text(cap)}")
    if problems:
        return Row(module, label, "warn", f"{path}: " + "; ".join(problems), fix)
    return Row(module, label, "ok", f"{path}: {cli._size_text(size)}")


def _tree_size(path: Path) -> int:
    size = 0
    for top, _, files in os.walk(path):
        for name in files:
            try:
                size += os.lstat(os.path.join(top, name)).st_size
            except OSError:
                pass
    return size


# -- 6. home, 7. strays, 8. setup versions, 9. records -------------------------------------------

def _check_home(ctx: _Context) -> list[Row]:
    from .. import home, paths

    rows = []
    for found in ctx.present:
        layout = paths.Layout(found.name)
        for kind, root, where in (("state", layout.state_root(), layout.state_dir()),
                                  ("cache", layout.cache_root(), layout.cache_dir())):
            new = root / found.name
            if where == new:
                continue
            # The move refuses a link to a relative path, so its fix is the link's own.
            fix = (home.link_fix(found.name, where, new) if home.relative_link(where)
                   else f"moxi install {found.name}")
            rows.append(Row(found.name, f"home {kind}", "warn",
                            f"its {kind} is read in place under a host folder: {where}",
                            fix))
    return rows


def _check_strays(ctx: _Context) -> list[Row]:
    from .. import strays

    return [Row(stray.module, "stray", "warn",
                f"{stray.distribution} {stray.version} is installed outside this moxi "
                f"({stray.place()}), where moxi can't see it",
                f"moxi install {stray.module}, then: {stray.remove_command()}")
            for stray in strays.find()]


def _check_setup(ctx: _Context) -> list[Row]:
    import json

    from .._settle import setup_path

    path = setup_path()
    try:
        held = json.loads(path.read_bytes())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        return [Row("moxi", "setup versions", "warn", f"{path} cannot be read: {exc}")]
    if not isinstance(held, dict):
        return [Row("moxi", "setup versions", "warn", f"{path} is not a JSON object")]
    rows = []
    for found in ctx.present:
        entry = held.get(found.name)
        ran = entry.get("version") if isinstance(entry, dict) else entry
        if not isinstance(ran, str) or ran == found.version:
            continue
        rows.append(Row(found.name, "setup version", "warn",
                        f"{found.version} is installed, and its setup last ran at {ran}",
                        f"moxi install {found.name}"))
    return rows


def _check_records(ctx: _Context) -> list[Row]:
    import json
    import shlex

    from .. import names, paths

    rows = []
    for module in [names.HARNESS, *(f.name for f in ctx.scan)]:
        if not ctx.wanted(module):
            continue
        path = paths._layout(module).state_dir() / RECORDS_FILE
        try:
            held = json.loads(path.read_bytes())
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            rows.append(Row(module, "records", "warn", f"{path} cannot be read: {exc}"))
            continue
        if not isinstance(held, dict):
            rows.append(Row(module, "records", "warn", f"{path} is not a JSON object"))
            continue
        for key, record in sorted(held.items()):
            record = record if isinstance(record, dict) else {"text": str(record)}
            clear = f"moxi doctor --clear {module} {shlex.quote(key)}"
            fix = record.get("fix")
            at = record.get("at")
            rows.append(Row(module, f"record {key}", "fail",
                            str(record.get("text", "")) + (f" (at {at})" if at else ""),
                            f"{fix}, then: {clear}" if fix else clear))
    return rows


# -- 10. placements -------------------------------------------------------------------------------

def _check_placements(ctx: _Context) -> list[Row]:
    from .. import names

    rows = _harness_rows() if ctx.wanted(names.HARNESS) else []
    return rows + _placement_rows(ctx)


def _harness_rows() -> list[Row]:
    """A `warn` row on module `moxi` for each harness row the placer skips and each name in
    `skills.harnesses` that has no row. A core config that can't be read is row 2's."""
    import shlex

    from .. import config, names, place

    try:
        settings = place._core_settings()
        rows_by_name, skipped = place.harnesses(settings)
        _, unnamed = place.chosen(settings)
    except config.ConfigError:
        return []
    where = settings.file
    edit = (f"edit or remove that row of harnesses in {where}" if where is not None
            else "edit or remove that row of harnesses: moxi config moxi")
    rows = [Row(names.HARNESS, "harnesses", "warn", line, edit) for line in skipped]
    # chosen() says one line per name with no row, in the setting's order.
    missing = [name for name in settings["skills.harnesses"] if name not in rows_by_name]
    for name, line in zip(missing, unnamed):
        quoted = shlex.quote(str(name))
        rows.append(Row(names.HARNESS, f"skills.harnesses {name}", "warn", line,
                        f"moxi config set moxi skills.harnesses --remove {quoted}, or add "
                        f"its row: moxi config set moxi harnesses --add "
                        f"'{{name = \"{name}\", skills = \"...\", agents = \"...\"}}'"))
    return rows


def _placement_rows(ctx: _Context) -> list[Row]:
    """Each owner's placements read against the disk (`moxi.place.status`): a `warn` row for
    each that is `stale`, `modified` or `missing`, and one `ok` row per owner whose
    placements all read `ok`. A ledger that can't be read is one `fail` row, since every
    install and uninstall then fails to place or remove skills."""
    from .. import names, place

    path = place._ledger_path()
    try:
        ledger = place._read_ledger()
    except ValueError as exc:
        # The placer's text up to its advice, which the fix line gives in full.
        return [Row(names.HARNESS, "placements", "fail", str(exc).partition("; ")[0],
                    f"move {path} aside, then run moxi install <module> --replace for each "
                    f"module whose skills are links: a link the placer made reads as "
                    f"unmarked without its ledger entry (a copied folder is known by its "
                    f"mark)")]
    except OSError as exc:
        return [Row(names.HARNESS, "placements", "fail", f"{path} cannot be read: {exc}")]
    rows = []
    for owner in sorted(ledger):
        entries = ledger[owner].get("placed", {})
        if not entries or not ctx.wanted(owner):
            continue
        found = ctx.found.get(owner)
        bad = [placed for placed in place.status(owner) if placed.state != "ok"]
        for placed in bad:
            entry = entries.get(str(placed.path), {})
            rows.append(_placement_row(placed, entry, found, ctx.is_present(owner)))
        if not bad:
            count = len(entries)
            rows.append(Row(owner, "placements", "ok",
                            f"{count} placement{'s' if count != 1 else ''}, each as placed"))
    return rows


def _placement_row(placed, entry: Mapping, found, present: bool) -> Row:
    """One placement that isn't `ok`, as a `warn` row. Its fix is a `moxi install` line only
    when the owner is an installed module whose own package the source lies in, since only
    then is the placement one that `moxi install` makes; another owner's tool (a project
    manager placing a project's skills, say) manages the rest."""
    source = str(entry.get("source", ""))
    path = placed.path
    if placed.state == "missing":
        detail = f"{path} is gone"
    elif placed.state == "stale" and not os.path.exists(source):
        detail = f"source missing: {source} (placed at {path})"
    elif placed.state == "stale":
        detail = (f"{path} is stale: its source {source} changed after it was copied"
                  if placed.form == "copy"
                  else f"{path} is stale: it no longer resolves to its source {source}")
    elif placed.form == "copy":
        detail = f"{path} was edited after it was copied"
    else:
        detail = f"{path} no longer points at its source {source}"
    from .. import place

    fix = None
    if present and place.from_package(found, source):
        fix = f"moxi install {placed.owner}"
        if placed.state == "modified":
            from .. import paths

            fix += (" --replace, which moves it to "
                    f"{paths.core().data_dir() / 'displaced'}")
    return Row(placed.owner, f"placement {placed.harness} {placed.name}", "warn", detail,
               fix)


# -- 11. moxi.doctor entries, 12. DOCTOR ----------------------------------------------------------

def _check_entries(ctx: _Context) -> list[Row]:
    from .. import modules

    rows = []
    for ext in modules.extensions("moxi.doctor"):
        rows += _rows_of(ext.target, ext.load, ext.module, f"moxi.doctor {ext.name}")
    return rows


def _check_doctors(ctx: _Context) -> list[Row]:
    rows = []
    for found in ctx.present:
        if found.doctor is not None:
            rows += doctor_rows(found)
    return rows


def doctor_rows(found) -> list[Row]:
    """Module `found`'s `DOCTOR` rows, run in a worker thread."""
    target = found.doctor
    return _rows_of(target, lambda: _load(target), found.name, "DOCTOR")


def _rows_of(target: str, load: Callable[[], Callable], module: str,
             label: str) -> list[Row]:
    """The rows a `DOCTOR` target or a `moxi.doctor` entry returns, or one `fail` row
    naming it."""
    def check():
        value = load()()
        if (isinstance(value, (Row, str, bytes, Mapping))
                or not isinstance(value, Iterable)):
            raise _WrongType(f"returned {type(value).__name__}, not an iterable of "
                             "moxi.doctor.Row")
        out = list(value)
        for item in out:
            if not isinstance(item, Row):
                raise _WrongType(f"returned a {type(item).__name__} among its rows, not a "
                                 "moxi.doctor.Row")
        return out

    outcome, value = _call(check, DOCTOR_SECONDS, f"{label} {module}")
    if outcome != "ok":
        return [Row(module, label, "fail", _failure(target, outcome, value,
                                                     DOCTOR_SECONDS))]
    return value


class _WrongType(Exception):
    """A target that returned something other than what the doctor reads."""


def _call(fn: Callable[[], object], seconds: float, label: str) -> tuple[str, object]:
    """`fn()` in a daemon worker thread, waited on for `seconds`: `("ok", value)`,
    `("raised", exc)` or `("overran", None)`."""
    box: list = []

    def work():
        try:
            box.append(("ok", fn()))
        except BaseException as exc:  # the thread's whole outcome goes back to the caller
            box.append(("raised", exc))

    thread = threading.Thread(target=work, name=f"moxi-doctor {label}", daemon=True)
    thread.start()
    thread.join(seconds)
    if not box:
        return "overran", None
    return box[0]


def _failure(target: str, outcome: str, value, seconds: float) -> str:
    if outcome == "overran":
        return f"{target} did not finish in {seconds:g} s"
    if isinstance(value, _WrongType):
        return f"{target} {value}"
    return f"{target} raised {_describe(value)}"


def _load(target: str) -> Callable:
    """Import `package.module:function` and return the function."""
    import importlib

    module_name, _, attr = target.partition(":")
    obj = importlib.import_module(module_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _describe(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {lines[0]}" if lines else type(exc).__name__


# -- 13. costs ---------------------------------------------------------------------------------

_floor: list[float] = []


def floor_ms() -> float:
    """The median wall time of `FLOOR_RUNS` starts of `python -I -c pass`, in
    milliseconds, measured once per process."""
    if not _floor:
        import statistics
        import subprocess

        times = []
        for _ in range(FLOOR_RUNS):
            start = time.perf_counter()
            subprocess.run([sys.executable, "-I", "-c", "pass"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False, timeout=60)
            times.append(time.perf_counter() - start)
        _floor.append(statistics.median(times) * 1000)
    return _floor[0]


class Measure:
    """What one daemon costs now: CPU seconds since it started, memory plus swap in
    bytes, open files (or handles), and its start time when known."""

    __slots__ = ("cpu", "memory", "fds", "started")

    def __init__(self, cpu: float, memory: int, fds: int, started: float | None = None):
        self.cpu = cpu
        self.memory = memory
        self.fds = fds
        self.started = started


def _check_costs(ctx: _Context) -> list[Row]:
    from .. import files, names, paths

    rows = []
    if ctx.wanted(names.HARNESS):
        rows.append(Row(names.HARNESS, "floor", "ok",
                        f"python -I -c pass starts in {floor_ms():.1f} ms (median of "
                        f"{FLOOR_RUNS})"))
    path = paths.core().state_dir() / COSTS_FILE
    try:
        samples = files.read_records(path)
    except (OSError, ValueError):
        samples = []
    now = time.time()
    taken: list[dict] = []
    for found in ctx.present:
        active = [s for s in ctx.states if s.module == found.name and s.kind == "daemon"
                  and s.state == "active"]
        if active:
            rows += _module_costs(found, active, samples, now, taken)
    for sample in taken:
        try:
            files.append_record(path, sample)
        except (OSError, ValueError):
            break
    return rows


def _module_costs(found, active: list, samples: list[dict], now: float,
                  taken: list[dict]) -> list[Row]:
    """One module's active daemons, summed, against its `BUDGETS`."""
    module = found.name
    rows = []
    rate = memory = fds = 0
    rated = True
    hows = set()
    for state in active:
        unit = f"{module}/{state.name}"
        got = measure(state)
        if isinstance(got, str):
            rows.append(Row(module, f"cost {state.name}", "warn",
                            f"daemon {state.name} could not be measured: {got}"))
            rated = False
            continue
        since = state.since or got.started
        one, how = idle_rate(samples, unit, got.cpu, now, since)
        taken.append({"unit": unit, "at": _stamp(now), "cpu_seconds": round(got.cpu, 3)})
        memory += got.memory
        fds += got.fds
        if one is None:
            rated = False
        else:
            rate += one
            hows.add(how)
    budgets = found.budgets
    if rated and hows:
        how = " and ".join(sorted(hows))
        rows.append(_budget_row(module, "idle_cpu", rate > budgets.get("idle_cpu", 1e9),
                                f"{rate * 100:.2f}% of one core, {how}",
                                f"{budgets['idle_cpu'] * 100:g}%"
                                if "idle_cpu" in budgets else None))
    from .. import cli

    cap = _size_bytes(budgets["rss"]) if "rss" in budgets else None
    rows.append(_budget_row(module, "rss", cap is not None and memory > cap,
                            f"{cli._size_text(memory)} of memory and swap",
                            budgets.get("rss")))
    rows.append(_budget_row(module, "fds", "fds" in budgets and fds > budgets["fds"],
                            f"{fds} open files", budgets.get("fds")))
    return rows


def _budget_row(module: str, key: str, over: bool, measured: str, budget) -> Row:
    shown = f"budget {budget}" if budget is not None else "no budget"
    if over:
        return Row(module, f"cost {key}", "warn", f"{measured}, over its {shown}")
    return Row(module, f"cost {key}", "ok", f"{measured} ({shown})")


def idle_rate(samples: Iterable[Mapping], unit: str, cpu: float, now: float,
              since: float | None) -> tuple[float | None, str]:
    """A daemon's CPU rate, in cores, and how it was taken: against the stored sample
    nearest to an hour old that is at least ten minutes old and from this run of the
    daemon, else CPU since start over uptime."""
    best = None
    for sample in samples:
        if sample.get("unit") != unit:
            continue
        at = _parse_stamp(sample.get("at"))
        then = sample.get("cpu_seconds")
        if at is None or not isinstance(then, (int, float)) or isinstance(then, bool):
            continue
        age = now - at
        if age < SAMPLE_MIN_AGE or then > cpu or (since is not None and at < since):
            continue
        if best is None or abs(age - SAMPLE_AGE) < abs(best[0] - SAMPLE_AGE):
            best = (age, then)
    if best is not None:
        return (cpu - best[1]) / best[0], f"over the last {round(best[0] / 60)} min"
    if since is not None and now > since:
        return cpu / (now - since), "since it started (no sample ten minutes old yet)"
    return None, "not measured"


def measure(state) -> Measure | str:
    """What the daemon `state` names costs now, or why it could not be read. On systemd
    its cgroup counts every process the unit started; elsewhere psutil counts its main
    process and that process's children."""
    if not state.pid:
        return "the supervisor gave no main process id"
    if state.backend == "systemd":
        got = cgroup_measure(state.pid)
        if got is not None:
            return got
    return process_measure(state.pid)


#: Where `cgroup_measure` reads; tests point them into their tmp.
_PROC = Path("/proc")
_CGROUP = Path("/sys/fs/cgroup")


def cgroup_measure(pid: int) -> Measure | None:
    """The cgroup (v2) of process `pid`: `cpu.stat`'s `usage_usec`, `memory.current` plus
    `memory.swap.current`, and the open files of every process in `cgroup.procs`. None
    when the cgroup cannot be read."""
    try:
        text = (_PROC / str(pid) / "cgroup").read_text()
    except OSError:
        return None
    rel = next((line[3:] for line in text.splitlines() if line.startswith("0::")), None)
    if not rel:
        return None
    group = _CGROUP / rel.strip().lstrip("/")
    try:
        usage = next(int(line.split()[1])
                     for line in (group / "cpu.stat").read_text().splitlines()
                     if line.startswith("usage_usec "))
        memory = int((group / "memory.current").read_text())
        procs = (group / "cgroup.procs").read_text().split()
    except (OSError, ValueError, StopIteration, IndexError):
        return None
    try:
        memory += int((group / "memory.swap.current").read_text())
    except (OSError, ValueError):
        pass
    fds = 0
    for proc in procs:
        try:
            fds += len(os.listdir(_PROC / proc / "fd"))
        except OSError:
            pass
    return Measure(usage / 1e6, memory, fds, _started(pid))


def process_measure(pid: int) -> Measure | str:
    """psutil over process `pid` and its children: CPU times, resident memory (and swap
    where psutil gives it) and open files (handles on Windows)."""
    import psutil

    try:
        main = psutil.Process(pid)
        procs = [main, *main.children(recursive=True)]
        started = main.create_time()
    except psutil.Error as exc:
        return f"process {pid}: {_describe(exc)}"
    cpu = 0.0
    memory = fds = 0
    for proc in procs:
        try:
            with proc.oneshot():
                times = proc.cpu_times()
                cpu += times.user + times.system
                memory += proc.memory_info().rss
                fds += proc.num_handles() if os.name == "nt" else proc.num_fds()
        except psutil.Error:
            continue
        try:
            memory += getattr(proc.memory_full_info(), "swap", 0)
        except (psutil.Error, OSError):
            pass
    return Measure(cpu, memory, fds, started)


def _started(pid: int) -> float | None:
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001
        return None


def _stamp(when: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))


def _parse_stamp(text) -> float | None:
    import calendar

    if not isinstance(text, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


# -- 11. the terminal record -------------------------------------------------------------------

def _check_terminal(ctx: _Context) -> list[Row]:
    from .. import names

    return [terminal_row()] if ctx.wanted(names.HARNESS) else []


def terminal_row(*, keyboard: bool | None = None, view=None, run=None) -> Row:
    """The doctor's terminal record row: that a command typed here records (the record
    holds this run's environment), or the first thing that stops it, each with its step.

    It reads only: it places the record, compares it with what this run would record, and
    runs the front door's keyboard test (`keyboard`, for a test) and the walk (`view` and
    `run`, as `moxi.terminal.walk` takes them). A write the front door's record step
    tried and failed in this run (`moxi.terminal.last`) is named with its error; with no
    step in this run, a record that differs is checked for whether it could be written.
    A walk that finds Moxi or an agent, or can't decide, is a `warn`; a record that can't
    be read or written is a `fail`.
    """
    from .. import paths, terminal
    from ..terminal import _lines, _record, _walk

    name = "terminal record"

    def worded(state: str, words: tuple[str, str]) -> Row:
        return Row("moxi", name, state, words[0], words[1])

    def stopped(state: str, outcome) -> Row:
        # The record step's own words, "<cause>; <step>", shared with its stderr line.
        cause, _, step = _record.row(outcome).rpartition("; ")
        return Row("moxi", name, state, cause, step)

    try:
        record = paths.terminal_record()
    except paths.NoAnchor as no:
        return stopped("warn", _record.Outcome(_record.NO_PLACE, None, error=no))
    if terminal.anchor_set(os.environ):
        return worded("warn", _lines.row_anchor_set(str(record)))
    if not (_record.keyboard() if keyboard is None else keyboard):
        return worded("warn", _lines.NO_KEYBOARD)
    found = _walk.walk(view, run)
    if isinstance(found, _walk.Evidence):
        return worded("warn", _lines.found_words(found))
    if found is not None:
        return stopped("warn", _record.Outcome(_record.UNDECIDED, record, found))
    held = _record.read(record)
    if isinstance(held, _record.Unreadable):
        return Row("moxi", name, "fail",
                   f"the record at {record} can't be read ({_record._reason(held.error)})",
                   f"fix its permissions or remove it, then type {terminal.RECORD_COMMAND} "
                   "here")
    if isinstance(held, _record.Record) and held.env == _record.of():
        return Row("moxi", name, "ok", f"the record at {record} holds this terminal's "
                   "environment, so a page compares with it")
    last = terminal.last
    if last is not None and last.kind == _record.FAILED:
        return stopped("fail", last)
    if last is None and (error := _unwritable(record)) is not None:
        return stopped("fail", _record.Outcome(_record.FAILED, record, error=error))
    return Row("moxi", name, "warn",
               f"the record at {record} doesn't hold this terminal's environment, and "
               "this run made none: it wasn't started as the moxi command",
               f"type {terminal.RECORD_COMMAND} in this terminal")


def _unwritable(record: Path) -> OSError | None:
    """Why `record` can't be written in place, or None: the file, when it exists, and the
    nearest folder above it that exists must be writable by this account."""
    import errno

    folder = record.parent
    while not folder.exists() and folder.parent != folder:
        folder = folder.parent
    for path in ((record,) if record.exists() else ()) + (folder,):
        if not os.access(path, os.W_OK):
            return PermissionError(errno.EACCES, f"{path}: {os.strerror(errno.EACCES)}")
    return None


# -- the command -------------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    """`moxi doctor`. Exit 0 when no row is `fail`, else 1; 2 for a bad argument list."""
    import argparse
    import json

    from .. import names
    from . import clear

    parser = argparse.ArgumentParser(
        prog="moxi doctor",
        description="Check this environment's modules, settings, units, stores and "
                    "records. Module names restrict the rows to those modules; `moxi` "
                    "names the core's own rows. Exits 1 when a row fails.")
    parser.add_argument("modules", nargs="*", metavar="MODULE")
    parser.add_argument("--json", action="store_true", help="print the rows as one JSON list")
    parser.add_argument("--costs", action="store_true",
                        help="also measure each module's daemons against its BUDGETS")
    parser.add_argument("--clear", nargs=2, metavar=("MODULE", "KEY"),
                        help="remove the record KEY a program left for MODULE, and exit")
    args = parser.parse_args(argv)
    if args.clear is not None:
        if args.modules or args.json or args.costs:
            parser.error("--clear takes a module and a key and nothing else")
        module, key = args.clear
        try:
            clear(module, key)
        except ValueError as exc:
            print(f"moxi: {exc}", file=sys.stderr)
            return 2
        print(f"{module}: record {key} cleared")
        return 0
    for name in args.modules:
        if not names.is_module_name(name):
            parser.error(f"{name!r} is not a module name")
    rows = run(args.modules or None, costs=args.costs)
    if args.json:
        print(json.dumps([row._asdict() for row in rows], indent=2))
    else:
        for row in rows:
            print(line(row))
    return 1 if any(row.state == "fail" for row in rows) else 0


def line(row: Row) -> str:
    """One row as `moxi doctor` prints it: state, module, name, detail, then the fix."""
    text = f"{row.state:<4}  {row.module}  {row.name}  {row.detail}"
    return text + (f"  fix: {row.fix}" if row.fix else "")


__all__ = ["DOCTOR_SECONDS", "HEALTH_SECONDS", "Measure", "run", "health", "floor_ms",
           "terminal_row",
           "measure", "cgroup_measure", "process_measure", "idle_rate", "store_rows",
           "doctor_rows", "main", "line"]
