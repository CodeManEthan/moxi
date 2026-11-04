"""The connector's doctor rows, `DOCTOR = "moxi_connector.doctor:rows"`, and the local
snapshot `moxi connector status` shows.

Every row reads local state only: the config file, tunnel's record, the control socket's
`status`, the call log's last check, and the grants file. None makes a network call.

The connector is optional and off by default, so while its switch is off its rows are
quiet: the switch row says `off`, and nothing else shows but the address row's warn. That
one needs a tunnel entry the user added, so an install that never set the connector up
can't raise it, and it names a real exposure: a public address forwarding to a port
nothing of this user holds.

| row | ok | warn | fail |
|---|---|---|---|
| switch | on | | |
| process | the control socket answers | | no answer while the switch is on |
| address | a URL from tunnel or `public_url` | tunnel's entry on while the switch is off | the switch on and no URL |
| port | a port Claude is known to reach | any other port | |
| edge | Tailscale | Cloudflare or a typed URL, with the last check | |
| providers | each started, with its tool count | none loaded | a failed start, a refused entry, a clashing tool name |
| mint gate | a gate is loaded | | none loaded |
| grants | the count | a stale grant | |
| each provider's own | from its `status()` | | |

A provider's rows come through the control socket, so they show only while the process
answers; the process row covers the rest.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from moxi.doctor import Row

from . import calllog, checks
from . import origin as origins
from . import settings as connector_settings

log = logging.getLogger("moxi.connector.doctor")

MODULE = "connector"
#: The tunnel target, as its verbs name it.
TARGET = "connector/mcp"

ADDRESS_FORWARDS = ("A public address forwards to a port nothing serves. Remove it with moxi "
                    f"tunnel remove {TARGET}.")
NO_ADDRESS = ("The switch is on and there is no public address, so the connector answers 503 "
              "to every request.")
EDGE_WARN = ("Your edge must let User-Agent Claude-User through; quick tunnels can't. Run moxi "
             "connector check.")
NO_PROVIDER = "No module provides tools, so the connector serves none."
NO_GATE = ("No code can be minted: no installed module can tell an agent session from your "
           "terminal. comms provides that check.")
STALE = ("The connector's address changed. In Claude, remove the connector and add it again "
         "with the new URL.")


@dataclass
class Grants:
    """The grants as their file holds them: how many, how many are stale, and one mapping
    per grant for `status`."""

    count: int
    stale: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)


def grants_view(origin: origins.Origin | None = None) -> Grants | None:
    """The grants file, read for the grants row and `status`: the live grants, and how
    many of them are stale under `origin` (O as the process would read it now). None
    while there is no grants file, so a connector that never signed anyone in shows no
    grants row."""
    import time

    from . import grants

    store = grants.Store(days=int(connector_settings.load()["refresh_days"]))
    if not store.exists():
        return None
    rows = grants.listing(store.view(), resource=origin.resource if origin else None,
                          now=time.time(), days=store.days)
    live = [r for r in rows if r["state"] == "live"]
    return Grants(len(live), sum(1 for r in live if r["stale"]), rows)


@dataclass
class Snapshot:
    """What the doctor and `status` read, once."""

    switch: bool
    port: int
    public_url: str
    #: tunnel's record of the target, whatever its state, or None.
    address: Any
    #: O as the process would read it now.
    origin: origins.Origin | None
    #: The control socket's `status`, or None with `error` saying why.
    answer: dict[str, Any] | None
    error: str | None
    last_check: dict[str, Any] | None
    last_call: dict[str, Any] | None
    grants: Grants | None

    @property
    def source(self) -> str | None:
        """Where O comes from: `public_url`, `tunnel`, or None."""
        if self.origin is None:
            return None
        return "public_url" if self.public_url.strip() else "tunnel"

    @property
    def edge(self) -> str | None:
        """The edge in front of O: tunnel's provider, `typed` for `public_url`, or None."""
        if self.origin is None:
            return None
        if self.source == "public_url":
            return "typed"
        return getattr(self.address, "provider", None)


def take(*, ask: Callable[..., object] | None = None,
         tunnel: Callable[[], object] | None = None) -> Snapshot:
    """Read everything the rows need; the control socket only while the switch is on.
    Never raises for a part it can't read but the config file: that part reads as
    absent."""
    from moxi import ipc

    ask = ask or checks.ask
    tunnel = tunnel or origins.tunnel_address
    values = connector_settings.load()
    try:
        address = tunnel()
    except Exception:  # noqa: BLE001 - an unreadable record is no address
        log.warning("tunnel's record couldn't be read", exc_info=True)
        address = None
    found = origins.read(values["public_url"], tunnel=lambda: address)
    answer: dict[str, Any] | None = None
    error: str | None = None
    if not values["enabled"]:
        error = "the connector is off"
    else:
        try:
            got = ask("status")
            answer = got if isinstance(got, dict) else None
            if answer is None:
                error = "the control socket answered something other than a status"
        except ipc.IpcError as e:
            error = str(e) or type(e).__name__
        except OSError as e:
            error = str(e)
    rows = [r for r in _safe_rows(days=2)]
    last_check = next((r for r in reversed(rows) if r.get("kind") == "check"), None)
    last_call = next((r for r in reversed(rows) if r.get("kind") == "request"), None)
    try:
        grants = grants_view(found)
    except Exception:  # noqa: BLE001
        log.warning("the grants couldn't be read", exc_info=True)
        grants = None
    return Snapshot(bool(values["enabled"]), int(values["port"]), str(values["public_url"]),
                    address, found, answer, error, last_check, last_call, grants)


def _safe_rows(days: int) -> list[dict[str, Any]]:
    try:
        return list(calllog.read_rows(days=days))
    except OSError:
        return []


def check_line(row: dict[str, Any] | None) -> str:
    """The last `moxi connector check`, in a line."""
    if row is None:
        return "No check has run."
    if row.get("passed"):
        return f"The last check, at {row.get('at')}, passed."
    failed = row.get("failed")
    return (f"The last check, at {row.get('at')}, failed"
            + (f" at its {failed} step." if failed else "."))


def build(snap: Snapshot) -> list[Row]:
    """The rows for `snap`."""
    if not snap.switch:
        rows = [Row(MODULE, "switch", "off", "the connector is off",
                    "moxi connector on")]
        if getattr(snap.address, "state", None) == "on":
            rows.append(Row(MODULE, "address", "warn", ADDRESS_FORWARDS,
                            f"moxi tunnel remove {TARGET}"))
        return rows

    rows = [Row(MODULE, "switch", "ok", "on")]
    if snap.answer is not None:
        pid = snap.answer.get("pid")
        rows.append(Row(MODULE, "process", "ok", "the control socket answers"
                        + (f" (pid {pid})" if pid else "")))
    else:
        rows.append(Row(MODULE, "process", "fail",
                        f"the switch is on and the control socket doesn't answer: "
                        f"{snap.error}", checks.RESTART))

    if snap.origin is None:
        rows.append(Row(MODULE, "address", "fail", NO_ADDRESS,
                        f"moxi tunnel add {TARGET}"))
    else:
        where = "public_url" if snap.source == "public_url" else "tunnel"
        rows.append(Row(MODULE, "address", "ok", f"{snap.origin.url}, from {where}"))
        warning = snap.origin.port_warning()
        if warning is None:
            rows.append(Row(MODULE, "port", "ok",
                            f"{snap.origin.port}, a port Claude is known to reach"))
        else:
            rows.append(Row(MODULE, "port", "warn", f"{snap.origin.port}: {warning}"))
        if snap.edge == "tailscale":
            rows.append(Row(MODULE, "edge", "ok", "Tailscale Funnel"))
        else:
            name = "Cloudflare" if snap.edge == "cloudflare" else "a typed URL"
            rows.append(Row(MODULE, "edge", "warn",
                            f"{name}. {EDGE_WARN} {check_line(snap.last_check)}",
                            "moxi connector check"))

    if snap.answer is not None:
        rows.extend(_provider_rows(snap.answer))

    if snap.grants is not None:
        if snap.grants.stale:
            rows.append(Row(MODULE, "grants", "warn",
                            f"{snap.grants.stale} of {snap.grants.count} stale. {STALE}"))
        else:
            rows.append(Row(MODULE, "grants", "ok", _count(snap.grants.count, "grant")))
    return rows


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _provider_rows(answer: dict[str, Any]) -> list[Row]:
    rows: list[Row] = []
    entries = [e for e in answer.get("providers") or () if isinstance(e, dict)]
    clashes = answer.get("clashes") or {}
    bad = [e for e in entries if e.get("state") in ("failed", "refused")]
    started = [e for e in entries if e.get("state") == "started"]
    if bad or clashes:
        parts = [f"{e.get('module')}: {e.get('why') or e.get('state')}" for e in bad]
        parts += [f"the tool {name} is declared by {' and '.join(mods)}, so neither serves it"
                  for name, mods in sorted(clashes.items())]
        rows.append(Row(MODULE, "providers", "fail", "; ".join(parts)))
    elif not started:
        rows.append(Row(MODULE, "providers", "warn", NO_PROVIDER))
    else:
        rows.append(Row(MODULE, "providers", "ok", "; ".join(
            f"{e.get('module')}, {_count(len(e.get('tools') or ()), 'tool')}"
            for e in started)))

    gates = answer.get("gates") or []
    if gates:
        rows.append(Row(MODULE, "mint gate", "ok", f"from {', '.join(gates)}"))
    else:
        rows.append(Row(MODULE, "mint gate", "fail", NO_GATE,
                        "moxi install comms --with connector"))

    skip = {e.get("module") for e in bad}
    for row in answer.get("rows") or ():
        if not (isinstance(row, (list, tuple)) and len(row) == 3):
            continue
        name, level, text = (str(x) for x in row)
        if name in skip or name.startswith("tool "):
            continue                    # already in the providers row
        state = level if level in ("ok", "warn", "fail") else "fail"
        rows.append(Row(MODULE, name, state, text))
    return rows


def rows() -> list[Row]:
    """The doctor's rows, the announcement's `DOCTOR`."""
    return build(take())


__all__ = ["ADDRESS_FORWARDS", "EDGE_WARN", "Grants", "NO_ADDRESS", "NO_GATE", "NO_PROVIDER",
           "STALE", "Snapshot", "build", "check_line", "grants_view", "rows", "take"]
