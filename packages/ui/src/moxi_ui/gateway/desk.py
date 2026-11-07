"""The desktop's gateway routes, under `/m/ui/`, with the desktop session's cookie and key;
the gateway never reaches them.

    POST /m/ui/api/gateway/pair      mint a pairing code; answers the pairing URL
    GET  /m/ui/api/gateway/devices   the paired devices
    POST /m/ui/api/gateway/revoke    {"id"}: delete a device and end its sessions at once
    GET  /m/ui/api/gateway/log       the audit log's last rows and each device's sign-ins

The handlers reach the shell they serve through `moxi_ui.page.current()`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from moxi_ui.loopback.api import Field, Out, Refused, Route

from . import audit, pairing
from .devices import Device, Devices

__all__ = ["DEVICES_SHAPE", "DEVICE_ROW", "LOG_SHAPE", "ROUTES", "device_row", "devices", "log",
           "pair", "revoke"]

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


async def pair(call: Any) -> dict[str, Any]:
    """Mint a pairing code at the gateway's address and answer its URL. Refused while the
    gateway has no address, or takes no hold because this shell's environment moves its
    runtime directory."""
    gateway = getattr(_shell(), "gateway", None)
    if gateway is None:
        raise Refused("this shell runs no gateway")
    if gateway.moved:
        raise Refused(gateway.moved)
    try:
        url = await asyncio.to_thread(pairing.mint, gateway.runtime_dir, gateway.address)
    except pairing.NoAddress as err:
        raise Refused(str(err)) from None
    return {"url": url}


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
    Route("/api/gateway/pair", "act", f"{_HERE}:pair", returns={"url": Out("string")}),
    Route("/api/gateway/devices", "read", f"{_HERE}:devices", returns=DEVICES_SHAPE),
    Route("/api/gateway/revoke", "act", f"{_HERE}:revoke",
          body={"id": Field("string", max=64)}, returns={"ok": Out("bool")}),
    Route("/api/gateway/log", "read", f"{_HERE}:log", returns=LOG_SHAPE),
)
