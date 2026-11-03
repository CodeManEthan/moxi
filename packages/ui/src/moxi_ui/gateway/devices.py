"""The paired devices: `<state_dir>/gateway/devices.json`, 0600, changed only through
`moxi.files.update`.

Each device has a random id, its name, its created and last-used times, the passkey's
credential id and public key (both None for a device paired without a passkey), the
authenticator's sign count, and the host (the passkey's `rp.id`), provider and change token
it was paired under. Only public keys and hashes rest here, never a secret.

A device is stale when its host or provider differs from the gateway's current address. A
stale device signs in to nothing and stays listed until it is revoked, so the user sees which
phones to pair again. Revoking deletes the device from the file; the shell's tick then ends
its sessions. At most `LIMIT` devices are kept.

The shell, the CLI and the tunnel's change listener each change the file in their own
process; the file's lock makes them take turns.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from moxi import files

__all__ = ["DEVICES_FILE", "GATEWAY_DIR", "LIMIT", "Device", "Devices", "Full", "devices_path",
           "gateway_dir"]

#: The gateway's directory under the ui's state directory.
GATEWAY_DIR = "gateway"
DEVICES_FILE = "devices.json"
#: At most this many devices are kept.
LIMIT = 20
_FORMAT = 1


class Full(Exception):
    """The file already holds `LIMIT` devices."""


@dataclass(frozen=True)
class Device:
    """One paired device. `credential` and `public_key` are base64url text, or None for a
    device paired without a passkey; `host` is the passkey's `rp.id`."""

    id: str
    name: str
    created: float
    used: float
    credential: str | None
    public_key: str | None
    sign_count: int
    host: str
    provider: str
    change: str
    stale: bool = False

    @property
    def passkey(self) -> bool:
        return self.credential is not None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def gateway_dir(state_dir: str | os.PathLike) -> Path:
    return Path(state_dir) / GATEWAY_DIR


def devices_path(state_dir: str | os.PathLike) -> Path:
    return gateway_dir(state_dir) / DEVICES_FILE


def _device(item: object) -> Device | None:
    if not isinstance(item, dict):
        return None
    try:
        device = Device(**{name: item[name] for name in Device.__dataclass_fields__
                           if name in item})
    except TypeError:
        return None
    texts = (device.id, device.name, device.host, device.provider, device.change)
    if not all(isinstance(text, str) and text for text in texts):
        return None
    if any(isinstance(t, bool) or not isinstance(t, (int, float))
           for t in (device.created, device.used)):
        return None
    if isinstance(device.sign_count, bool) or not isinstance(device.sign_count, int):
        return None
    if (device.credential is None) != (device.public_key is None):
        return None
    if device.credential is not None and not (isinstance(device.credential, str)
                                              and isinstance(device.public_key, str)):
        return None
    if not isinstance(device.stale, bool):
        return None
    return device


def _parse(data: bytes | None) -> list[Device]:
    """The devices in the file's bytes. Raises `ValueError` for a file that isn't one, so
    a change never writes over a file it can't read."""
    if data is None:
        return []
    value = json.loads(data)
    if not isinstance(value, dict) or value.get("format") != _FORMAT:
        raise ValueError("devices.json is not a devices file of a known format")
    items = value.get("devices")
    if not isinstance(items, list):
        raise ValueError("devices.json has no device list")
    found = []
    for item in items:
        device = _device(item)
        if device is not None:
            found.append(device)
    return found


def _dump(devices: list[Device]) -> bytes:
    data = {"format": _FORMAT, "devices": [d.as_dict() for d in devices]}
    return json.dumps(data, indent=1, sort_keys=True).encode("utf-8") + b"\n"


class Devices:
    """The device file at `path`, read fresh for each question."""

    def __init__(self, path: str | os.PathLike, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock

    @classmethod
    def of(cls, state_dir: str | os.PathLike, **kwargs: Any) -> Devices:
        return cls(devices_path(state_dir), **kwargs)

    def all(self) -> list[Device]:
        """Every device; none when the file is missing or can't be read."""
        try:
            return _parse(self.path.read_bytes())
        except (OSError, ValueError):
            return []

    def get(self, device_id: str) -> Device | None:
        return next((d for d in self.all() if d.id == device_id), None)

    def by_credential(self, credential: str) -> Device | None:
        """The device whose passkey has the credential id `credential` (base64url)."""
        return next((d for d in self.all() if d.credential == credential), None)

    def _change(self, fn: Callable[[list[Device]], list[Device]]) -> None:
        files.private_dir(self.path.parent)
        files.update(self.path, lambda old: _dump(fn(_parse(old))), mode=0o600)

    def add(self, *, name: str, credential: str | None, public_key: str | None,
            sign_count: int, host: str, provider: str, change: str,
            replaces: str | None = None) -> Device:
        """Write a new device and return it. `replaces` names a device it takes the place
        of, deleted in the same write. Raises `Full` when the file holds `LIMIT` devices
        and none is replaced."""
        now = self.clock()
        device = Device(id=secrets.token_hex(8), name=name, created=now, used=now,
                        credential=credential, public_key=public_key, sign_count=sign_count,
                        host=host, provider=provider, change=change)

        def added(devices: list[Device]) -> list[Device]:
            kept = [d for d in devices if d.id != replaces]
            if len(kept) >= LIMIT:
                raise Full(f"the gateway keeps at most {LIMIT} devices; revoke one first")
            return [*kept, device]

        self._change(added)
        return device

    def revoke(self, device_id: str) -> bool:
        """Delete the device; False when the file doesn't hold it."""
        found = []

        def without(devices: list[Device]) -> list[Device]:
            kept = [d for d in devices if d.id != device_id]
            found.append(len(kept) != len(devices))
            return kept

        if not self.path.exists():
            return False
        self._change(without)
        return found[-1]

    def used(self, device_id: str, sign_count: int | None = None) -> None:
        """Note a sign-in: the last-used time, and the new sign count when given."""
        now = self.clock()

        def touched(devices: list[Device]) -> list[Device]:
            return [replace(d, used=now, sign_count=d.sign_count if sign_count is None
                            else sign_count) if d.id == device_id else d for d in devices]

        self._change(touched)

    def mark_stale(self, which: Callable[[Device], bool]) -> list[str]:
        """Mark stale every device `which` picks that isn't already; returns their ids."""
        marked: list[str] = []

        def marking(devices: list[Device]) -> list[Device]:
            marked.clear()
            out = []
            for d in devices:
                if not d.stale and which(d):
                    marked.append(d.id)
                    d = replace(d, stale=True)
                out.append(d)
            return out

        if not self.path.exists():
            return []
        if not any(not d.stale and which(d) for d in self.all()):
            return []
        self._change(marking)
        return list(marked)

    def stale_unless(self, host: str, provider: str) -> list[str]:
        """Mark stale every device paired under another host or provider than these: the
        rule for the gateway's current address, and for the tunnel's change listener."""
        host = host.lower()
        return self.mark_stale(lambda d: d.host.lower() != host or d.provider != provider)

    def stale_of(self, host: str, provider: str) -> list[str]:
        """Mark stale every device paired under this host and provider: those of an
        address the gateway just left."""
        host = host.lower()
        return self.mark_stale(lambda d: d.host.lower() == host and d.provider == provider)
