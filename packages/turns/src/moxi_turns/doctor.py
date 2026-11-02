"""turns' checks for the front door's doctor: the announcement's `DOCTOR`.

`check()` yields `moxi.doctor.Row`s of module `turns`: its config, the start list, the
registered listeners (an after-tool row's floor that does not import fails its module's
row), the guards, the launch-additions contributors, the live endpoints (dead ones
are swept on the way) and any version 1 endpoint directory left behind, when a live
Codex host last saw the account's rate limits (`codex limits`), the hook times,
and each harness adapter's rows (`ADAPTER_ROWS`: the Claude plugins, `endpoint.json`
and the Claude Code version; Codex; the OpenCode host's binary). turns has no doctor
command of its own.

The guard rows read the list as an endpoint would, in the doctor's own process: `guards`
names each guard on, what turned it on and where it runs on each harness; `guard <id>`
is `off` for an available guard nobody turned on, `fail` for one on the list that does
not load, whose judge does not import or whose `match` does not compile, and for a
switch that fails the core's check, listed or not, and `warn` for one on the list whose
module is not installed, whose import took over `IMPORT_WARN_MS`, or (the removal guard)
when the installed Claude Code is newer than the one its rules were checked against;
`guard <id>: <harness>` says, as information, that a guard on covers no call there or
that the harness has no pre-tool point. A guard's breaks are records the core shows.
The doctor adds readings and denies nothing.
"""

from __future__ import annotations

import importlib
import os
import re
import time
from collections.abc import Iterator

from moxi import doctor as moxi_doctor
from moxi.config import ConfigError

from . import client, config, hooktimes, listeners, protocol

MODULE = "turns"

#: `"package.module:function"` of each adapter's rows, in the order they are shown.
ADAPTER_ROWS: tuple[str, ...] = (
    "moxi_turns.claude_setup:rows", "moxi_turns.codex:rows", "moxi_turns.opencode:rows",
)
#: `"package.module:Class"` of each harness's adapter, whose `tool_points` say where a
#: guard can run there.
HARNESS_CLASSES: dict[str, str] = {
    "claude": "moxi_turns.claude_endpoint:ClaudeHarness",
    "codex": "moxi_turns.codex:CodexHarness",
    "opencode": "moxi_turns.opencode:OpenCodeHarness",
}
#: Where a guard runs on a harness with a pre-tool point.
GUARD_PLACES = {"claude": "in the runner", "codex": "behind the hook"}
#: A guard's import (its module's guards, then its judge) slower than this, in
#: milliseconds, is a warn.
IMPORT_WARN_MS = 10.0


def Row(name: str, ok: bool, detail: str, fix: str | None = None) -> moxi_doctor.Row:  # noqa: N802
    """An adapter's row in its old spelling: `ok` or not, read as `ok` or `fail`."""
    return moxi_doctor.Row(MODULE, name, "ok" if ok else "fail", detail, fix)


def check() -> Iterator[moxi_doctor.Row]:
    settings, row = _config()
    yield row
    # A config that does not read leaves the start list at its default.
    registry = listeners.load()
    yield from _start_list(registry)
    yield from _listener_rows(registry)
    yield from _guard_rows(settings)
    yield from _additions_rows()
    yield from _endpoint_rows()
    yield _codex_limits_row()
    yield from _hook_rows()
    for target in ADAPTER_ROWS:
        module_name, _, attr = target.partition(":")
        try:
            yield from getattr(importlib.import_module(module_name), attr)()
        except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not raise
            yield Row(target, False, f"could not be checked: {type(exc).__name__}: {exc}")


def _config():
    try:
        settings = config.load()
    except ConfigError as exc:
        return None, Row("config", False, str(exc))
    allow = list(settings["commands.allow"])
    detail = (f"{settings.file}: {config.STOP_WAIT} {settings[config.STOP_WAIT]}, "
              f"listen.wait_ms {settings['listen.wait_ms']}, "
              f"commands.allow {', '.join(allow) or 'none'}, "
              f"start.limit {settings['start.limit'] or 'the ceiling'}")
    unknown = config.unknown_commands(allow)
    if unknown:
        return settings, moxi_doctor.Row(
            MODULE, "config", "warn",
            f"{detail}; commands.allow names {', '.join(unknown)}, which are not session "
            "commands and grant nothing",
            "moxi config set turns commands.allow with only clear, exit and compact")
    return settings, Row("config", True, detail)


def _start_list(registry: listeners.Registry) -> Iterator[moxi_doctor.Row]:
    from moxi import modules

    plan = listeners.plan(registry, present=modules.present)
    asked = ", ".join(e.entry for e in plan.entries) or "nothing"
    skipped = "; ".join(f"{entry}: {why}" for entry, why in plan.skipped)
    yield Row("start list", True, f"asks {asked}" + (f"; skipped {skipped}" if skipped
                                                      else ""))
    for module in plan.off_list:
        yield moxi_doctor.Row(MODULE, f"start list: {module}", "warn",
                              f"{module} registers a start row but is not on start.order, "
                              "so it adds no start context", "add it to start.order")
    for entry, why in plan.skipped:
        if listeners.is_file_entry(entry):
            yield moxi_doctor.Row(MODULE, f"start list: {entry}", "warn",
                                  f"this file entry adds nothing: {why}",
                                  "correct the path in start.order, or remove it")


def _listener_rows(registry: listeners.Registry) -> Iterator[moxi_doctor.Row]:
    modules: dict[str, set[str]] = {}
    for listener in registry.listeners:
        modules.setdefault(listener.module, set()).update(listener.events)
    for name, events in sorted(modules.items()):
        yield Row(f"listener {name}", True, "registered for " + ", ".join(
            e for e in listeners.EVENTS if e in events))
    for b in registry.broken:
        yield Row(f"listener {b.module}", False, b.reason)
    for listener in registry.listeners:
        # A floor is read only for a row naming `after-tool`.
        if listener.floor is None or listeners.AFTER_TOOL not in listener.events:
            continue
        try:
            listener.floor_function()
        except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not raise
            yield Row(f"listener {listener.module}", False,
                      f"the floor {listener.floor} of its row {listener.target} does not "
                      f"import: {type(exc).__name__}: {exc}; the row reads floor 0, "
                      "so it is asked after every call")
    for n in registry.notices:
        yield moxi_doctor.Row(MODULE, f"listener {n.module}", "warn", n.reason)


def _guard_rows(settings) -> list[moxi_doctor.Row]:
    try:
        return list(_guards(settings))
    except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not raise
        return [Row("guards", False, f"could not be checked: {type(exc).__name__}: {exc}")]


def _guards(settings) -> Iterator[moxi_doctor.Row]:
    from . import guards
    from .guards import removal, run

    loaded = guards.load(env=os.environ)
    max_wait = guards.MAX_WAIT_MS if settings is None else settings[guards.MAX_WAIT_KEY]
    points = _tool_points()
    fails: dict[str, list[str]] = {}
    warns: dict[str, list[str]] = {}
    fixes: dict[str, str] = {}
    for p in loaded.problems:
        if p.kind == "switch":
            fails.setdefault(p.id, []).append(p.reason)
        elif p.kind == "missing" and p.on:
            warns.setdefault(p.id, []).append(p.reason)
            fixes[p.id] = f"moxi config set turns {guards.ON_KEY} --remove {p.id}"
        elif p.on:
            fails.setdefault(p.id, []).append(p.reason)
            fixes[p.id] = f"moxi config set turns {guards.ON_KEY} --remove {p.id}"

    places: dict[str, list[str]] = {}
    info: list[moxi_doctor.Row] = []
    for o in loaded.on:
        ms = loaded.import_ms.get(o.found.module, 0.0) if o.found.module else 0.0
        if o.guard.judge is not None:
            t0 = time.perf_counter()
            try:
                guards.judge_of(o.found)
            except Exception as exc:  # noqa: BLE001 - the guard's own fault, reported
                fails.setdefault(o.id, []).append(
                    f"its judge {o.guard.judge} does not import: "
                    f"{type(exc).__name__}: {exc}; each call it covers is a break")
            ms += (time.perf_counter() - t0) * 1000
        if ms > IMPORT_WARN_MS:
            warns.setdefault(o.id, []).append(
                f"its import took {ms:.0f} ms, over {IMPORT_WARN_MS:g} ms")
        where = places.setdefault(o.id, [])
        for harness in guards.HARNESSES:
            if not points[harness].get("pre"):
                info.append(moxi_doctor.Row(
                    MODULE, f"guard {o.id}: {harness}", "ok",
                    f"on, but {harness} has no pre-tool point, so it runs nowhere there"))
                continue
            try:
                entry = run.entry_for(o, harness, max_wait)
            except re.error as exc:
                fails.setdefault(o.id, []).append(f"a match regex does not compile: {exc}")
                continue
            if entry is None:
                info.append(moxi_doctor.Row(MODULE, f"guard {o.id}: {harness}", "ok",
                                            f"on, but it covers no call on {harness}"))
            else:
                where.append(f"{harness} {GUARD_PLACES.get(harness, '')}".rstrip())
        if o.id == "turns.removal" and o.guard.patterns("claude"):
            newer = _claude_newer_than(removal.CHECKED_CLAUDE)
            if newer:
                warns.setdefault(o.id, []).append(
                    f"the installed Claude Code is {newer}, newer than "
                    f"{removal.CHECKED_CLAUDE}, which its rules were checked against; "
                    "Claude Code's own removal handling may have moved")

    if loaded.on:
        yield Row("guards", True, "; ".join(
            f"{o.id} (on by {' and '.join(_BY.get(b, b) for b in o.by)}): runs "
            f"{', '.join(places.get(o.id) or ['nowhere'])}" for o in loaded.on))
    else:
        yield Row("guards", True, "no guard is on")
    for f in loaded.off:
        if f.id in fails:
            continue
        fix = (f"moxi config set {f.module} {f.guard.switch} true"
               if f.module and f.guard.switch
               else f"moxi config set turns {guards.ON_KEY} --add {f.id}")
        yield moxi_doctor.Row(MODULE, f"guard {f.id}", "off",
                              f.guard.doc or "available and off", fix)
    on_ids = {o.id: o for o in loaded.on}
    for gid in dict.fromkeys([*fails, *warns]):
        reasons = fails.get(gid, []) + warns.get(gid, [])
        fix = fixes.get(gid)
        if fix is None and gid in on_ids:
            fix = _off_fix(on_ids[gid])
        yield moxi_doctor.Row(MODULE, f"guard {gid}", "fail" if gid in fails else "warn",
                              "; ".join(reasons), fix)
    yield from info


def _off_fix(on) -> str | None:
    """The command that turns guard `on` off: off the list, else its switch off."""
    found = on.found
    if "list" in on.by:
        return f"moxi config set turns guards.on --remove {found.path or found.id}"
    if found.module and found.guard.switch:
        return f"moxi config set {found.module} {found.guard.switch} false"
    return None


#: How the `guards` row says what turned a guard on.
_BY = {"list": "guards.on", "switch": "its switch"}


def _tool_points() -> dict[str, dict[str, bool]]:
    """Each harness's `tool_points`, read off its adapter's class; an adapter that does
    not import reads as having neither point."""
    out = {}
    for harness, target in HARNESS_CLASSES.items():
        module_name, _, attr = target.partition(":")
        try:
            out[harness] = dict(getattr(importlib.import_module(module_name),
                                        attr).tool_points())
        except Exception:  # noqa: BLE001 - a doctor reports, it does not raise
            out[harness] = {"pre": False, "after": False}
    return out


def _version(text: str) -> tuple[int, ...] | None:
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def _claude_newer_than(checked: str) -> str | None:
    """The installed Claude Code's version when it is newer than `checked`; None when it
    is not, or `claude --version` cannot say."""
    from . import claude_setup

    claude = claude_setup._claude()
    if claude is None:
        return None
    try:
        done = claude_setup._run([claude, "--version"])
    except Exception:  # noqa: BLE001 - the Claude rows report a failing claude
        return None
    installed = (done.stdout.split() or ["?"])[0]
    have, want = _version(installed), _version(checked)
    return installed if have is not None and want is not None and have > want else None


def _additions_rows() -> list[moxi_doctor.Row]:
    from . import additions

    try:
        return additions.rows()
    except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not raise
        return [Row("additions", False, f"could not be checked: {type(exc).__name__}: {exc}")]


def _endpoint_rows() -> Iterator[moxi_doctor.Row]:
    live = client.sessions()
    yield Row("endpoints", True, f"{len(live)} live under {client.runtime_dir()}")
    for s in live:
        info = s.info
        mod = f", mod {info['mod_version']}" if info.get("mod_version") else ""
        trouble = info.get("trouble")
        detail = (f"session {s.session_id or '(not yet known)'}; offers "
                  f"{', '.join(sorted(s.capabilities)) or 'nothing'}; endpoint "
                  f"{info.get('endpoint_version')}{mod}")
        if trouble:
            detail += f"; {trouble}"
        yield moxi_doctor.Row(MODULE, f"endpoint {s.harness} pid {s.pid}",
                              "warn" if trouble else "ok", detail)
    for directory in protocol.v1_directories(client.runtime_dir()):
        yield moxi_doctor.Row(
            MODULE, f"endpoints: v1 {directory}", "warn",
            "an endpoint directory of turns 0.1, which this turns neither reads nor "
            "removes",
            "reload that session's plugins or restart it; once no turns 0.1 session "
            "runs, remove the directory")


def _codex_limits_row() -> moxi_doctor.Row:
    """`codex limits`, information: when a live Codex host last saw the account's rate
    limits (its read after a load, a push from Codex, or a pull), the newest across the
    live hosts; or that no Codex session is live. Each host answers from its own record
    (`usage.codex_limits` with `cached`), so no app-server is asked."""
    live = client.sessions()
    newest = None
    hosts = unread = 0
    try:
        for s in live:
            if s.harness != "codex" or "usage" not in s.capabilities:
                continue
            hosts += 1
            try:
                answer = s.codex_limits(cached=True)
            except Exception:  # noqa: BLE001 - a doctor reports, it does not raise
                unread += 1
                continue
            at = answer.get("received_at")
            if isinstance(at, (int, float)) and not isinstance(at, bool) \
                    and (newest is None or at > newest[0]):
                newest = (at, answer.get("kind"), s.pid)
    finally:
        for s in live:
            s.close()
    if not hosts:
        return Row("codex limits", True, "no live Codex session")
    if newest is None:
        detail = f"no reading yet on {hosts} live Codex host{'s' if hosts > 1 else ''}"
    else:
        at, kind, pid = newest
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(at))
        detail = f"last reading {when} ({kind or 'read'}), Codex host pid {pid}"
    if unread:
        detail += f"; {unread} host{'s' if unread > 1 else ''} did not answer"
    return Row("codex limits", True, detail)


def _hook_rows() -> list[moxi_doctor.Row]:
    from . import module

    return hooktimes.rows(hooktimes.read(), budget=module.BUDGETS["hook_ms"])


__all__ = ["ADAPTER_ROWS", "check"]
