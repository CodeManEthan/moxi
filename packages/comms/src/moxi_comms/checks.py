"""comms' own doctor checks (the announcement's `DOCTOR`) and the daemon's health (its
`HEALTH`).

The rows of comms' tools read the running daemon's own answer (`mcp_endpoint`) and
comms.db's `mcp_tokens`; they never try the port themselves, and import nothing of the
MCP libraries.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping

MODULE = "comms"
#: comms' daemon, as its announcement declares it among its `DAEMONS`.
DAEMON = "daemon"
#: Seconds the daemon gets to answer a check.
ASK_WAIT = 2.0
#: The window the `mcp tokens` row counts revocations over, in seconds.
REVOKED_WINDOW = 24 * 3600
#: How to restart comms' daemon when the services module is installed.
RESTART = "moxi services restart comms/daemon"
#: How to restart it without services where the core names no unit for it here (no
#: supervisor): the unit by what it is, since unit names are the core's to build.
SUPERVISOR_RESTART = ("restart the unit `moxi install comms` wrote for comms/daemon with "
                      "its supervisor (systemctl --user restart on systemd)")
#: The fix of a live session that has lost its tool token.
RELAUNCH = "relaunch that session to give it comms' tools"
#: The fix when comms' socket unit holds the port and this daemon runs outside it.
SUPERVISED = "stop this daemon and start it through its service"

#: `ask()` gives the daemon's `mcp_endpoint` answer, or None when no daemon answers.
Ask = Callable[[], Mapping | None]


def doctor_rows(env=None) -> list:
    """The move of comms 0.2's files and what it left, the reserved names, then comms'
    tools."""
    from . import reserved
    from .store import upgrade_0_2

    return (upgrade_0_2.doctor_rows(env) + reserved.doctor_rows(env)
            + mcp_rows(env=env))


def restart() -> str:
    """The fix of a row that a restart of the daemon cures: services' restart when services
    is installed, else the supervisor's own."""
    from moxi import modules

    present = modules.load("services").state == modules.PRESENT
    return RESTART if present else supervisor_restart()


def supervisor_restart() -> str:
    """The supervisor's own restart of the daemon's unit, by the id the core gives that unit
    in this home (`moxi.units.ids`); `SUPERVISOR_RESTART` where it gives none."""
    from moxi import units

    found = units.ids(MODULE, DAEMON)
    backend = units.backend()
    if not found:
        return SUPERVISOR_RESTART
    if backend == "systemd":
        return f"systemctl --user restart {found[-1]}"
    if backend == "launchd":
        return f"launchctl kickstart -k gui/{os.getuid()}/{found[0]}"
    if backend == "schtasks":
        return f'schtasks /End /TN "{found[0]}", then schtasks /Run /TN "{found[0]}"'
    return SUPERVISOR_RESTART


def health():
    """The daemon's `HEALTH` row: it answers on its socket, and whether it serves
    comms' tools (the `mcp endpoint` row says how)."""
    from moxi.doctor import Row

    from .paths import daemon_socket

    where = daemon_socket()
    answer = ask_endpoint()
    if answer is None:
        return Row(MODULE, "daemon", "fail",
                   f"the daemon doesn't answer on {where} within {ASK_WAIT:g} s", restart())
    tools = (f"comms' tools served at {answer.get('url')}" if answer.get("served")
             else f"comms' tools not served ({answer.get('why')})")
    return Row(MODULE, "daemon", "ok", f"answers on {where}; {tools}")


def mcp_rows(*, env=None, ask: Ask | None = None, configured=None,
             now: Callable[[], float] = time.time) -> list:
    """The rows of comms' tools: `mcp endpoint` and `mcp port` from the running daemon,
    when one answers, `mcp tokens` from comms.db, then an `mcp tokens: <identity>` row
    for each live session the daemon names whose token was revoked.

    `configured` is `[mcp]` as the config file reads now (`config.load().mcp`), given by
    a test; `ask` the daemon's answer."""
    rows = []
    answer = ask() if ask is not None else ask_endpoint(env)
    if configured is None:
        configured = _configured(env)
    if answer is not None:
        rows.append(endpoint_row(answer, configured))
        port = port_row(answer, configured)
        if port is not None:
            rows.append(port)
    tokens = tokens_row(env=env, now=now)
    if tokens is not None:
        rows.append(tokens)
    if answer is not None:
        rows.extend(revoked_rows(answer))
    return rows


def revoked_rows(answer: Mapping) -> list:
    """An `mcp tokens: <identity>` warn row for each live session the daemon names
    whose tool token was revoked: it runs on without comms' tools until relaunched."""
    from moxi.doctor import Row

    out = []
    for entry in answer.get("revoked") or ():
        if not isinstance(entry, Mapping) or not entry.get("identity"):
            continue
        out.append(Row(MODULE, f"mcp tokens: {entry['identity']}", "warn",
                       f"a live session whose tool token was revoked "
                       f"({entry.get('why') or 'no reason recorded'}): it has no comms' "
                       "tools", RELAUNCH))
    return out


def endpoint_row(answer: Mapping, configured=None):
    """The `mcp endpoint` row from the daemon's `mcp_endpoint` answer."""
    from moxi.doctor import Row

    name = "mcp endpoint"
    detail = answer.get("detail") or ""
    if answer.get("served"):
        url = answer.get("url")
        if answer.get("held"):
            return Row(MODULE, name, "ok", f"served at {url}; the supervisor holds the port")
        return Row(MODULE, name, "warn",
                   f"served at {url} on a port the daemon bound itself, not one the "
                   "supervisor holds: another local user could take the port while the "
                   "daemon is down")
    why = answer.get("why")
    if why == "disabled":
        if configured is not None and not getattr(configured, "enabled", True):
            return Row(MODULE, name, "off", "[mcp] enabled = false")
        return Row(MODULE, name, "off", "the daemon serves no tools")
    if why == "port-taken":
        return Row(MODULE, name, "fail",
                   f"{detail or 'another process listens on the port'}; every live tool "
                   "token was revoked, so sessions lose comms' tools until relaunched",
                   port_taken_fix(answer.get("unit")))
    if why == "supervised":
        return Row(MODULE, name, "fail",
                   (detail or "comms' socket unit holds the port")
                   + "; no token was revoked",
                   SUPERVISED)
    if why == "bind-failed":
        return Row(MODULE, name, "fail",
                   f"{detail or 'the port could not be bound'}; no token was revoked",
                   restart())
    first = detail.splitlines()[0] if detail else "the endpoint didn't start"
    return Row(MODULE, name, "fail", first, restart())


def port_taken_fix(unit: str | None) -> str:
    """The `port-taken` row's fix, by the state comms' socket unit read after the failed
    bind. `moxi config set` leaves an installed socket unit on its old port, so a new
    port needs `moxi install comms` beside it; with no unit written, it needs none."""
    if unit in ("inactive", "failed"):
        move = ("free the port, or moxi config set comms mcp.port N and moxi install "
                "comms, which moves the socket unit to that port")
    elif unit in ("missing", "off"):
        move = "free the port or moxi config set comms mcp.port N"
    else:
        move = ("free the port, or moxi config set comms mcp.port N (and moxi install "
                "comms, if comms' socket unit is installed)")
    return f"{move}; then: {restart()}, then relaunch the sessions"


def port_row(answer: Mapping, configured):
    """The `mcp port` row when the daemon serves on another port than `[mcp] port`
    says now, else None."""
    from moxi.doctor import Row

    if not answer.get("served") or configured is None:
        return None
    served = _port_of(answer.get("url"))
    want = getattr(configured, "port", None)
    if served is None or want is None or served == want:
        return None
    if answer.get("held"):
        return Row(MODULE, "mcp port", "warn",
                   f"the socket unit holds port {served}, [mcp] port is {want}: the daemon "
                   f"serves on {served}, and launches get that address",
                   "moxi install comms")
    return Row(MODULE, "mcp port", "warn",
               f"the daemon serves on port {served}, [mcp] port is now {want}: it reads "
               "the port at its start", restart())


def tokens_row(*, env=None, now: Callable[[], float] = time.time):
    """The `mcp tokens` row: how many sessions hold a live tool token, and the
    revocations of the last 24 hours by reason. None when comms.db can't be read."""
    import sqlite3

    from moxi.doctor import Row

    from .bindings import TIMESTAMP_FMT
    from .store import StoreRefused
    from .store.open import open_read

    since = time.strftime(TIMESTAMP_FMT, time.gmtime(now() - REVOKED_WINDOW))
    try:
        conn = open_read(env=env)
    except (StoreRefused, sqlite3.Error):
        return None
    try:
        live = conn.execute(
            "SELECT COUNT(DISTINCT claim_key) FROM mcp_tokens WHERE revoked_at IS NULL"
        ).fetchone()[0]
        revoked = conn.execute(
            "SELECT revoked_why, COUNT(*) FROM mcp_tokens WHERE revoked_at >= ? "
            "GROUP BY revoked_why ORDER BY revoked_why", (since,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    sessions = f"{live} session{'' if live == 1 else 's'} hold{'s' if live == 1 else ''}"
    text = f"{sessions} a live tool token"
    if revoked:
        text += "; revoked in the last 24 hours: " + ", ".join(
            f"{n} {why}" for why, n in revoked)
    return Row(MODULE, "mcp tokens", "ok", text)


def ask_endpoint(env=None) -> Mapping | None:
    """The running daemon's `mcp_endpoint` answer, or None when none answers."""
    from .client import DaemonClient
    from .paths import daemon_socket

    try:
        client = DaemonClient(daemon_socket(env), timeout=ASK_WAIT).connect()
    except Exception:                                            # noqa: BLE001
        return None
    try:
        return client.call("mcp_endpoint", {})
    except Exception:                                            # noqa: BLE001
        return None
    finally:
        client.close()


def _configured(env):
    from . import config

    try:
        return config.load(env).mcp
    except Exception:                                            # noqa: BLE001
        return None


def _port_of(url) -> int | None:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).port if isinstance(url, str) else None
    except ValueError:
        return None


__all__ = ["ASK_WAIT", "MODULE", "RELAUNCH", "REVOKED_WINDOW", "SUPERVISED", "doctor_rows",
           "endpoint_row", "health", "mcp_rows", "port_row", "port_taken_fix", "revoked_rows",
           "tokens_row"]
