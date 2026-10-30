"""The shell's health: whether a shell of this user runs, and how it is, without ever
starting one.

`probe()` is the one read, in this order, and the doctor's rows are built on it:

1. One try at `page.lock`, before `page.json` is read and whatever it says. A held lock
   means a shell of this user is running, starting or stopping: the runtime file is read
   and `GET /m/ui/api/servers` called through loopback's `call`, which proves the shell
   first; with no file, no answer or a failed proof, both are tried again for up to the
   wait. An answer is `running`, with the `/servers` data. A failed proof at the end is
   `held` (another program on the port); anything else at the end is `starting` (a shell
   that holds the lock and has not written its file yet, or removed it on the way out, is
   this user's own). A free lock means no shell runs; a runtime file then is a crash's or
   a reboot's leftover and counts as none.
2. The units: a socket that is `active` is `idle` (it holds the port, and the first
   browser connection starts the shell), with no connect; a socket installed and not
   `active` is `socket`; a supervisor that could not be read is `unknown`, also with no
   connect, since a socket may hold the port. On Windows the logon task's state is read
   for the row, and the probe goes on.
3. With no socket, one connect to `127.0.0.1:<port>`, which wakes nothing: one that
   succeeds is `held`, since the lock is free; else `down`.

No pid is looked up and no clock compared. `check()` is the `HEALTH` target, the `shell`
row; the doctor builds the same row from the same probe.
"""

from __future__ import annotations

import socket
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from moxi.doctor import Row
from moxi_loopback.api import NotOurServer, RuntimeInfo, call, read_runtime

from . import cli, config
from .session import SERVERS_PATH

__all__ = [
    "HEALTH_WAIT",
    "SHELL_BUDGET",
    "WAIT",
    "Probe",
    "check",
    "over_budget",
    "parse_size",
    "probe",
    "shell_row",
]

MODULE = "ui"
#: What the shell itself may cost, compared on its own process only: its page servers
#: are its children and have budgets of their own.
SHELL_BUDGET = MappingProxyType({"rss": "80M", "fds": 64})
#: Seconds the doctor waits for a shell that holds the lock to answer.
WAIT = 5.0
#: The same wait for `check()`: the doctor gives a `HEALTH` target 5 s in all, so its own
#: wait ends first and the row is its own rather than a time-out.
HEALTH_WAIT = 4.0
#: Seconds one `/servers` call may take.
CALL_WAIT = 2.0
#: Seconds the port's connect may take.
CONNECT_WAIT = 0.5
POLL = 0.1

_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}


@dataclass(frozen=True)
class Probe:
    """What `probe()` found.

    `state` is one of `running`, `starting`, `held`, `idle`, `socket`, `unknown`,
    `down` or `error` (see the module docstring). `info` is the runtime file read under a
    held lock; `servers` the `/servers` data of a running shell; `socket` and `task` the
    units' `UnitState`s (the socket, the Windows logon task) when installed; `port` the
    port the probe used; `detail` a line for a person.
    """

    state: str
    info: RuntimeInfo | None = None
    servers: Mapping[str, Any] | None = None
    socket: Any = None
    task: Any = None
    port: int | None = None
    detail: str = ""


# --- the seams tests replace ------------------------------------------------------------


def _units_status() -> list[Any] | None:
    """The ui module's units, or None when the supervisor can't be asked."""
    from moxi import units

    try:
        return list(units.status([MODULE]))
    except Exception:  # noqa: BLE001 - the probe says it could not read them
        return None


def _accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=CONNECT_WAIT):
            return True
    except OSError:
        return False


_sleep = time.sleep
_clock = time.monotonic


def _platform() -> str:
    return sys.platform


# --- the probe --------------------------------------------------------------------------


def _servers(info: RuntimeInfo, timeout: float) -> Mapping[str, Any] | None:
    status, data = call(info, "GET", SERVERS_PATH, timeout=timeout)
    if status == 200 and isinstance(data, Mapping) and isinstance(data.get("shell"), Mapping):
        return data
    return None


def _held(wait: float) -> Probe:
    deadline = _clock() + wait
    foreign: RuntimeInfo | None = None
    info: RuntimeInfo | None = None
    while True:
        info = read_runtime(MODULE)
        if info is not None:
            left = max(deadline - _clock(), 0.5)
            try:
                data = _servers(info, min(CALL_WAIT, left))
            except NotOurServer:
                foreign = info
            except (OSError, TimeoutError, ValueError):
                foreign = None
            else:
                if data is not None:
                    return Probe("running", info=info, servers=data, port=info.port)
                foreign = None
        if _clock() >= deadline:
            break
        _sleep(POLL)
    if foreign is not None:
        return Probe("held", info=foreign, port=foreign.port,
                     detail=f"port {foreign.port} is held by another program")
    return Probe("starting", info=info, port=info.port if info else None,
                 detail="starting or stopping: page.lock is held and no shell answered "
                        f"within {wait:g} s")


def _task(rows: Sequence[Any]) -> Any:
    for row in rows:
        if (getattr(row, "module", None) == MODULE and getattr(row, "name", None) == cli.DAEMON
                and getattr(row, "kind", None) == "daemon"
                and getattr(row, "backend", None) == "schtasks"):
            return row
    return None


def probe(*, wait: float = WAIT) -> Probe:
    """Whether a shell of this user runs, and how it is, in the order the module
    docstring gives. Never starts a shell, and never connects to a port a socket of this
    user may hold."""
    # 1. The lock, before anything else is read.
    if cli.lock_held():
        return _held(wait)
    # 2. The units.
    rows = _units_status()
    if rows is None:
        return Probe("unknown", detail="the supervisor could not be read")
    ours = [row for row in rows if getattr(row, "module", None) == MODULE]
    # A socket may hold the port where the supervisor can't be read, so no connect then.
    # Windows has no socket: its probe goes on to the connect whatever the task read.
    unknown = [row for row in ours if row.state == "unknown"]
    if unknown and _platform() != "win32":
        return Probe("unknown", detail="the supervisor could not be read"
                     + (f": {unknown[0].detail}" if unknown[0].detail else ""))
    sock = cli.socket_row(ours)
    if sock is not None:
        if sock.state == "active":
            return Probe("idle", socket=sock, detail="idle, socket listening")
        detail = f"the shell's socket is {sock.state}" + (f": {sock.detail}" if sock.detail else "")
        return Probe("socket", socket=sock, detail=detail)
    task = _task(ours) if _platform() == "win32" else None
    # 3. No socket: one connect, which wakes nothing.
    try:
        port = config.port()
    except Exception as err:  # noqa: BLE001 - a bad setting is the row's detail
        return Probe("error", task=task, detail=f"the port can't be read: {err}")
    if _accepts(port):
        return Probe("held", task=task, port=port,
                     detail=f"port {port} is held by another program")
    return Probe("down", task=task, port=port, detail="not running")


# --- the row ----------------------------------------------------------------------------


def parse_size(text: str) -> int:
    """A size in bytes: digits with an optional K, M or G, powers of 1024. Raises
    `ValueError`."""
    if not isinstance(text, str) or not text:
        raise ValueError(f"not a size: {text!r}")
    unit = text[-1] if text[-1] in "KMG" else ""
    digits = text[:-1] if unit else text
    if not digits.isdigit():
        raise ValueError(f"not a size: {text!r}")
    return int(digits) * _SIZE_UNITS[unit]


def over_budget(sample: Mapping[str, Any] | None, budget: Mapping[str, Any] | None) -> list[str]:
    """What of `sample` (`{"rss", "fds", ...}`) is over `budget` (`{"rss": <size>,
    "fds": <int>}`), one phrase each; empty when within it or when either is missing.
    CPU is reported, never compared."""
    if not sample or not budget:
        return []
    over = []
    try:
        limit = parse_size(budget["rss"])
    except (KeyError, ValueError):
        limit = None
    rss = sample.get("rss")
    if limit is not None and isinstance(rss, int) and rss > limit:
        over.append(f"RSS {_mb(rss)} over its {budget['rss']}")
    fds, cap = sample.get("fds"), budget.get("fds")
    if isinstance(fds, int) and isinstance(cap, int) and fds > cap:
        over.append(f"{fds} open fds over its {cap}")
    return over


def _mb(size: int) -> str:
    return f"{size / 1024**2:.0f} MB"


def _uptime(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


def _running_row(found: Probe) -> Row:
    own = found.servers["shell"] if found.servers else {}
    sample = own.get("sample") if isinstance(own.get("sample"), Mapping) else None
    parts = [f"running: pid {own.get('pid')}, port {own.get('port')}"]
    if isinstance(own.get("uptime_s"), (int, float)):
        parts.append(f"up {_uptime(own['uptime_s'])}")
    if sample:
        parts.append(f"RSS {_mb(sample['rss'])}, {sample['fds']} fds, "
                     f"CPU {sample['cpu_s']:.1f} s")
    detail = ", ".join(parts)
    warns = over_budget(sample, own.get("budget") or SHELL_BUDGET)
    if own.get("localhost") is False:
        warns.append("localhost is refused, since the shell does not hold [::1] on its "
                     "port; use 127.0.0.1")
    if warns:
        return Row(MODULE, "shell", "warn", f"{detail}; " + "; ".join(warns))
    return Row(MODULE, "shell", "ok", detail)


def _down_row(found: Probe) -> Row:
    task = found.task
    if _platform() != "win32":
        return Row(MODULE, "shell", "off", "not running")
    if task is None or task.state in ("missing", "nobackend"):
        return Row(MODULE, "shell", "warn",
                   "not running, and no logon task: the port is free whenever no shell runs",
                   "moxi install ui")
    if task.enabled is False:
        return Row(MODULE, "shell", "warn",
                   "not running, and the logon task is disabled: the port is free whenever "
                   "no shell runs", "moxi install ui")
    if task.state == "failed":
        last = task.last if isinstance(task.last, Mapping) else {}
        code = last.get("exit")
        return Row(MODULE, "shell", "warn",
                   f"the shell last exited with {code if code is not None else 'an error'}; "
                   "the logon task restarts it within a minute while it runs, or "
                   "`moxi ui open` starts it", "moxi ui open")
    return Row(MODULE, "shell", "off", "not running; starts at the next logon or `moxi ui open`")


def shell_row(found: Probe) -> Row:
    """The doctor's `shell` row for what `probe()` found."""
    state = found.state
    if state == "running":
        return _running_row(found)
    if state == "starting":
        return Row(MODULE, "shell", "warn", found.detail)
    if state == "held":
        return Row(MODULE, "shell", "fail", found.detail, "moxi config set ui port N")
    if state == "idle":
        return Row(MODULE, "shell", "off", found.detail)
    if state == "socket":
        return Row(MODULE, "shell", "fail", found.detail, "moxi install ui")
    if state == "unknown":
        return Row(MODULE, "shell", "warn", found.detail)
    if state == "error":
        return Row(MODULE, "shell", "fail", found.detail, "moxi config set ui port N")
    return _down_row(found)


def check() -> Row:
    """The `HEALTH` target of the daemon `shell`: its `shell` row."""
    return shell_row(probe(wait=HEALTH_WAIT))
