"""turns' checks for the front door's doctor: the announcement's `DOCTOR`.

`check()` yields `moxi.doctor.Row`s of module `turns`: its config, the start list, the
registered listeners, the live endpoints (dead ones are swept on the way) and any
version 1 endpoint directory left behind, the hook times, and each harness adapter's
rows (`ADAPTER_ROWS`: the Claude plugins, `endpoint.json` and the Claude Code version;
Codex; the OpenCode host's binary). turns has no doctor command of its own.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator

from moxi import doctor as moxi_doctor
from moxi.config import ConfigError

from . import client, config, hooktimes, listeners, protocol

MODULE = "turns"

#: `"package.module:function"` of each adapter's rows, in the order they are shown.
ADAPTER_ROWS: tuple[str, ...] = (
    "moxi_turns.claude_setup:rows", "moxi_turns.codex:rows", "moxi_turns.opencode:rows",
)


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
    yield from _endpoint_rows()
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
    for n in registry.notices:
        yield moxi_doctor.Row(MODULE, f"listener {n.module}", "warn", n.reason)


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


def _hook_rows() -> list[moxi_doctor.Row]:
    from . import module

    return hooktimes.rows(hooktimes.read(), budget=module.BUDGETS["hook_ms"])


__all__ = ["ADAPTER_ROWS", "check"]
