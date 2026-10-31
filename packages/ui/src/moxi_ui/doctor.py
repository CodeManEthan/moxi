"""The ui module's rows in `moxi doctor`: the shell, and each page server it started.

Every page server is the shell's child, so the shell reports them. The rows rest on
`health.probe()`, which tries `page.lock` before it reads `page.json`, and never connects
to a port only a socket holds, so a doctor run never starts the shell:

- `shell`: the probe's row (`health.shell_row`). A running shell is also compared with
  the `port` setting: one listening elsewhere is a warn, since the setting applies only
  when the shell next starts or the unit is rewritten. On Windows, when the port is held
  by another program, the logon task's state is its own `logon task` row, so a task that
  keeps failing its bind shows beside the held port.
- `page:<module>`: with the shell answering, from its `/servers` data, each sampled now;
  otherwise from `servers.json`, which the shell rewrites at each child exit. The last
  three starts failed is a fail, with the log's path; a crash in the last hour or a
  sample over the page's budget is a warn; a running server within its budget is ok; one
  not running is off, with its counts. One or two failed starts in a row are no warn of
  their own: the next start tries again, and a third makes the fail.

The UI module declares no `BUDGETS`: the core would count every page server as the
shell's, so the shell's own budget is compared here, on its own pid only.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from moxi.doctor import Row
from moxi.paths import Layout

from . import cli, config, health
from .starter import SERVERS_FILE, read_servers

__all__ = ["CRASH_WINDOW", "FAILED_IN_ROW", "page_row", "page_rows", "rows", "shell_rows"]

MODULE = "ui"
#: A crash this many seconds old or newer is a warn.
CRASH_WINDOW = 3600.0
#: This many failed starts in a row is a fail.
FAILED_IN_ROW = 3

_wall = time.time


def _setting_port() -> int | None:
    try:
        return config.port()
    except Exception:  # noqa: BLE001 - the core's settings row reports a bad file
        return None


def shell_rows(found: health.Probe) -> list[Row]:
    """The `shell` row for what `probe()` found, and on Windows the logon task's row when
    another program holds the port."""
    row = health.shell_row(found)
    rows = [row]
    if found.state == "running" and found.servers:
        listening = found.servers["shell"].get("port")
        setting = _setting_port()
        if setting is not None and isinstance(listening, int) and listening != setting:
            warn = (f"the shell listens on {listening} and the port setting says {setting}, "
                    "which applies when the shell next starts, or after `moxi install ui` "
                    "under a socket unit")
            detail = f"{row.detail}; {warn}" if row.detail else warn
            rows[0] = Row(MODULE, "shell", "fail" if row.state == "fail" else "warn", detail,
                          row.fix or "moxi ui stop; under a socket unit, moxi install ui")
    if health._platform() == "win32" and found.state in ("held", "error"):
        task = health.shell_row(health.Probe("down", task=found.task))
        rows.append(Row(MODULE, "logon task", task.state, task.detail, task.fix))
    return rows


def _at(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def _counts(data: Mapping[str, Any]) -> str:
    parts = [f"{data.get('starts', 0)} starts", f"{data.get('crashes', 0)} crashes"]
    if data.get("last_exit") is not None:
        parts.append(f"last exit {data['last_exit']} at {data.get('last_exit_at')}")
    return ", ".join(parts)


def page_row(data: Mapping[str, Any], *, now: float | None = None,
             log: str | None = None) -> Row:
    """One page server's row, from its `/servers` row or its `servers.json` record (with
    `module` added). `log` is the log path when the data has none."""
    now = _wall() if now is None else now
    module = str(data.get("module"))
    name = f"page:{module}"
    log = data.get("log") if isinstance(data.get("log"), str) else log
    reason = data.get("reason") if isinstance(data.get("reason"), str) else None
    in_row = data.get("failed_in_row") if isinstance(data.get("failed_in_row"), int) else 0
    if in_row >= FAILED_IN_ROW:
        detail = f"the last {in_row} starts failed" + (f": {reason}" if reason else "")
        if log:
            detail += f"; its log is {log}"
        return Row(MODULE, name, "fail", detail, f"read {log}" if log else None)
    warns = []
    crashed = _at(data.get("last_crash_at"))
    if crashed is not None and now - crashed <= CRASH_WINDOW:
        warns.append(f"crashed at {data['last_crash_at']}"
                     + (f" ({reason})" if reason and not in_row else ""))
    sample = data.get("sample") if isinstance(data.get("sample"), Mapping) else None
    warns += health.over_budget(sample, data.get("budget"))
    state = data.get("state")
    if state in ("running", "starting", "draining") and data.get("pid") is not None:
        detail = f"{state}: pid {data['pid']}"
        if isinstance(data.get("uptime_s"), (int, float)):
            detail += f", up {health._uptime(data['uptime_s'])}"
        if sample:
            detail += (f", RSS {sample['rss'] / 1024**2:.0f} MB, {sample['fds']} fds, "
                       f"CPU {sample['cpu_s']:.1f} s")
        opened = data.get("open")
        if isinstance(opened, Mapping):
            detail += (f", {opened.get('requests', 0)} open requests, "
                       f"{opened.get('streams', 0)} streams")
        ok = "ok"
    else:
        detail = f"not running (idle); {_counts(data)}"
        ok = "off"
    if warns:
        fix = f"read {log}" if log else None
        return Row(MODULE, name, "warn", f"{detail}; " + "; ".join(warns), fix)
    return Row(MODULE, name, ok, detail)


def page_rows(found: health.Probe, *, now: float | None = None) -> list[Row]:
    """A row per page server the shell started or recorded: from the `/servers` data when
    the shell answered, else from `servers.json`."""
    if found.state == "running" and found.servers:
        listed = [row for row in found.servers.get("servers") or ()
                  if isinstance(row, Mapping) and isinstance(row.get("module"), str)]
        return [page_row(row, now=now) for row in sorted(listed, key=lambda r: r["module"])]
    records = read_servers(Layout(MODULE).state_dir() / SERVERS_FILE)
    log = str(cli.log_path())
    return [page_row({"module": module, "state": "idle", **record}, now=now, log=log)
            for module, record in sorted(records.items())]


def rows() -> Iterator[Row]:
    """The `DOCTOR` target: the `shell` row (and the logon task's, where it applies),
    then one `page:<module>` row per page server."""
    found = health.probe(wait=health.WAIT)
    yield from shell_rows(found)
    yield from page_rows(found)
