"""What other modules call: a target's address, read from tunnel's record.

`address(module, name)` reads the record, one small file, with no lock and no subprocess.
Every record write is atomic, so a reader never sees half an entry. It returns the entry in
any record state; a caller uses the URL only while `state` is `on`.

`Address.change` is `"<record id>.<generation>"`, a value never given to two different
entry contents: a reader that keeps the last value it read for a target and compares it
with what it reads now never misses a change to that target's address. `None` means the
target has no address.

`Address.exposure` is who can actually reach the address: `internet` for every Cloudflare
entry and a Tailscale `public` one, `tailnet` only for a Tailscale `own-devices` entry. A
caller goes by it, not by the reach it declared.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import record


@dataclass(frozen=True)
class Address:
    """One target's address as the record holds it."""

    module: str
    name: str
    reach: str
    provider: str
    exposure: str
    url: str
    host: str
    port: int
    target: int
    state: str
    change: str
    applied_at: str | None


def url(host: str, port: int) -> str:
    """`https://<host>:<port>/`, with no port for 443."""
    return f"https://{host}/" if port == 443 else f"https://{host}:{port}/"


def from_entry(rec: record.Record, key: str) -> Address | None:
    """The address of the record's entry `key` (`<module>/<name>`), or None."""
    entry = rec.entries.get(key)
    if entry is None or not entry.known:
        return None
    module, _, name = key.partition("/")
    return Address(module=module, name=name, reach=entry.reach, provider=entry.provider,
                   exposure=entry.exposure, url=url(entry.host, entry.port),
                   host=entry.host, port=entry.port, target=entry.target,
                   state=str(entry.state), change=rec.token(key),
                   applied_at=entry.applied_at)


def address(module: str, name: str) -> Address | None:
    """Target `<module>/<name>`'s address, or None when the record holds none. Raises
    `moxi_tunnel.errors.Refused` for a record file that can't be read."""
    return from_entry(record.read(), f"{module}/{name}")


__all__ = ["Address", "address", "url", "from_entry"]
