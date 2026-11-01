"""comms' own doctor checks (the announcement's `DOCTOR`) and the daemon's health (its
`HEALTH`).

The rows of comms' tools read the running daemon's own answer (`mcp_endpoint`) and
comms.db's `mcp_tokens`; they never try the port themselves, and import nothing of the
MCP libraries.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

MODULE = "comms"
#: Seconds the daemon gets to answer a check.
ASK_WAIT = 2.0
#: The window the `mcp tokens` row counts revocations over, in seconds.
REVOKED_WINDOW = 24 * 3600
#: The fix of a row that a restart of the daemon cures.
RESTART = "moxi comms service restart"

#: `ask()` gives the daemon's `mcp_endpoint` answer, or None when no daemon answers.
Ask = Callable[[], Mapping | None]


def doctor_rows(env=None) -> list:
    """The move of comms 0.2's files and what it left, the reserved names, then comms'
    tools."""
    from . import reserved
    from .store import upgrade_0_2

    return (upgrade_0_2.doctor_rows(env) + reserved.doctor_rows(env)
            + mcp_rows(env=env))


def health():
    """The daemon's `HEALTH` row: it answers on its socket, and whether it serves
    comms' tools (the `mcp endpoint` row says how)."""
    from moxi.doctor import Row

    from .paths import daemon_socket

    where = daemon_socket()
    answer = ask_endpoint()
    if answer is None:
        return Row(MODULE, "daemon", "fail",
                   f"the daemon doesn't answer on {where} within {ASK_WAIT:g} s", RESTART)
    tools = (f"comms' tools served at {answer.get('url')}" if answer.get("served")
             else f"comms' tools not served ({answer.get('why')})")
    return Row(MODULE, "daemon", "ok", f"answers on {where}; {tools}")


def mcp_rows(*, env=None, ask: Ask | None = None, configured=None,
             now: Callable[[], float] = time.time) -> list:
    """The rows of comms' tools: `mcp endpoint` and `mcp port` from the running daemon,
    when one answers, and `mcp tokens` from comms.db.

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
    return rows


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
                   "free the port or moxi config set comms mcp.port N, then: "
                   f"{RESTART}, then relaunch the sessions")
    if why == "bind-failed":
        return Row(MODULE, name, "fail",
                   f"{detail or 'the port could not be bound'}; no token was revoked",
                   RESTART)
    first = detail.splitlines()[0] if detail else "the endpoint didn't start"
    return Row(MODULE, name, "fail", first, RESTART)


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
               "the port at its start", RESTART)


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


__all__ = ["ASK_WAIT", "MODULE", "REVOKED_WINDOW", "doctor_rows", "endpoint_row", "health",
           "mcp_rows", "port_row", "tokens_row"]
