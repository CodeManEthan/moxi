"""The desktop's gateway routes, under `/m/ui/`, with the desktop session's cookie and key;
the gateway never reaches them.

    GET  /m/ui/api/gateway/state     whether the gateway is installed, its switch, whether
                                     it serves, its address and why it doesn't serve
    POST /m/ui/api/gateway/pair      mint a pairing code; answers the pairing URL and when
                                     the code ends
    GET  /m/ui/api/gateway/devices   the paired devices
    POST /m/ui/api/gateway/revoke    {"id"}: delete a device and end its sessions at once
    GET  /m/ui/api/gateway/log       the audit log's last rows and each device's sign-ins

The handlers reach the shell they serve through `moxi_ui.page.current()`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from moxi_ui.loopback.api import Field, Out, Refused, Route

from . import audit, pairing
from .devices import Device, Devices

__all__ = ["DEVICES_SHAPE", "DEVICE_ROW", "LOG_SHAPE", "ROUTES", "STATE_SHAPE", "device_row",
           "devices", "log", "pair", "revoke", "state", "state_of"]

_HERE = "moxi_ui.gateway.desk"

#: One device as the desktop lists it.
DEVICE_ROW = Out("table", items={
    "id": Out("string"), "name": Out("string"), "created": Out("float"),
    "used": Out("float"), "passkey": Out("bool"), "stale": Out("bool"),
    "host": Out("string"), "provider": Out("string")})
DEVICES_SHAPE = {"devices": Out("list", items=DEVICE_ROW)}
#: The audit log as the desktop shows it: rows newest first, `detail` their other fields as
#: `moxi ui gateway log` prints them, and each device's sign-in times, newest first.
LOG_SHAPE = {
    "rows": Out("list", items=Out("table", items={
        "at": Out("string"), "kind": Out("string"), "device": Out("string", nullable=True),
        "detail": Out("string")})),
    "signins": Out("list", items=Out("table", items={
        "device": Out("string"), "at": Out("list", items=Out("string"))})),
}


#: The gateway as the desk's settings show it. `exposure` is tunnel's word for who can
#: reach the address (`internet` or `tailnet`).
STATE_SHAPE = {
    "installed": Out("bool"), "switch": Out("bool"), "on": Out("bool"),
    "host": Out("string", nullable=True), "port": Out("int", nullable=True),
    "exposure": Out("string", nullable=True), "reads_only": Out("bool"),
    "problem": Out("string", nullable=True),
}

_EXPOSURES = ("internet", "tailnet")


def state_of(gateway: Any) -> dict[str, Any]:
    """The gateway's state from what the shell's `Gateway` holds, with no read of its own.

    `installed` is false when the shell runs no `Gateway` (every other field is then false
    or null), else whether the `gateway` extra is installed. `host`, `port` and `exposure`
    are the address's when one is read; a record that can't be read is no address.
    `reads_only` is true unless the phone's acts are mounted. `problem` is set only while
    the switch is on and the gateway doesn't serve: the first of the moved runtime
    directory, the hold's problem and the tunnel's reason, which can all be unset."""
    if gateway is None:
        return {"installed": False, "switch": False, "on": False, "host": None, "port": None,
                "exposure": None, "reads_only": False, "problem": None}
    address = None if gateway.refused else gateway.address
    host = getattr(address, "host", None)
    port = getattr(address, "port", None)
    exposure = getattr(address, "exposure", None)
    on = bool(gateway.on)
    problem = None
    if gateway.switch and not on:
        problem = gateway.moved or gateway.problem or gateway.tunnel_reason
    return {
        "installed": bool(gateway.installed), "switch": bool(gateway.switch), "on": on,
        "host": host if isinstance(host, str) else None,
        "port": port if isinstance(port, int) and not isinstance(port, bool) else None,
        "exposure": exposure if exposure in _EXPOSURES else None,
        "reads_only": not gateway.mount.mounted,
        "problem": problem if isinstance(problem, str) else None,
    }


def device_row(device: Device) -> dict[str, Any]:
    return {"id": device.id, "name": device.name, "created": device.created,
            "used": device.used, "passkey": device.passkey, "stale": device.stale,
            "host": device.host, "provider": device.provider}


def _shell() -> Any:
    from ..page import current

    return current()


def _devices(shell: Any) -> Devices:
    phone = getattr(shell, "phone", None)
    return phone.devices if phone is not None else Devices.of(shell.layout.state_dir())


async def state(call: Any) -> dict[str, Any]:
    """The gateway's state, as the shell's `Gateway` last read it."""
    return state_of(getattr(_shell(), "gateway", None))


async def pair(call: Any) -> dict[str, Any]:
    """Mint a pairing code at the gateway's address and answer its URL, and `until`, when
    the code ends (epoch seconds). Refused while the gateway has no address, or takes no
    hold because this shell's environment moves its runtime directory."""
    gateway = getattr(_shell(), "gateway", None)
    if gateway is None:
        raise Refused("this shell runs no gateway")
    if gateway.moved:
        raise Refused(gateway.moved)
    minted = time.time()
    try:
        url = await asyncio.to_thread(pairing.mint, gateway.runtime_dir, gateway.address,
                                      clock=lambda: minted)
    except pairing.NoAddress as exc:
        raise Refused(str(exc)) from None
    return {"url": url, "until": minted + pairing.PAIR_TTL}


async def devices(call: Any) -> dict[str, Any]:
    """Every paired device."""
    found = await asyncio.to_thread(_devices(_shell()).all)
    return {"devices": [device_row(d) for d in found]}


async def revoke(call: Any) -> dict[str, Any]:
    """Delete a device. Its sessions end at once in this shell. Revoking doesn't delete the
    passkey from the phone; the phone's own settings do that."""
    shell = _shell()
    device_id = call.body["id"]
    phone = getattr(shell, "phone", None)
    if phone is not None:
        done = await asyncio.to_thread(phone.revoke, device_id)
    else:
        done = await asyncio.to_thread(_devices(shell).revoke, device_id)
    if not done:
        raise Refused(f"no device {device_id}")
    return {"ok": True}


async def log(call: Any) -> dict[str, Any]:
    """The audit log's last rows, from today and yesterday, and each device's sign-ins in
    them."""
    rows, signins = await asyncio.to_thread(audit.latest, _shell().layout.state_dir())
    return {
        "rows": [{"at": str(row.get("at", "")), "kind": str(row.get("kind", "")),
                  "device": row["device"] if isinstance(row.get("device"), str) else None,
                  "detail": audit.detail(row)} for row in rows],
        "signins": [{"device": device, "at": at} for device, at in signins.items()],
    }


#: The routes, joined to the shell's page.
ROUTES = (
    Route("/api/gateway/state", "read", f"{_HERE}:state", returns=STATE_SHAPE),
    Route("/api/gateway/pair", "act", f"{_HERE}:pair",
          returns={"url": Out("string"), "until": Out("float")}),
    Route("/api/gateway/devices", "read", f"{_HERE}:devices", returns=DEVICES_SHAPE),
    Route("/api/gateway/revoke", "act", f"{_HERE}:revoke",
          body={"id": Field("string", max=64)}, returns={"ok": Out("bool")}),
    Route("/api/gateway/log", "read", f"{_HERE}:log", returns=LOG_SHAPE),
)
