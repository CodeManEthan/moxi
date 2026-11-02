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

from collections.abc import Callable
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


@dataclass(frozen=True)
class Target:
    """A declared target, `<module>/<name>`, with its address or None."""

    module: str
    name: str
    reach: str
    port_setting: str
    doc: str
    address: Address | None


def targets() -> list[Target]:
    """Every declared target, with its address or None."""
    from . import declare

    rec = record.read()
    return [Target(t.module, t.name, t.reach, t.port_setting, t.doc, from_entry(rec, key))
            for key, t in declare.read().targets.items()]


def add(module: str, name: str, *, provider: str | None = None, port: int | None = None,
        hostname: str | None = None, replace: bool = False,
        confirm: Callable[[list[str]], bool]) -> Address:
    """Turn target `<module>/<name>` on, after `confirm(lines)` says yes to the warning
    lines, and return its address. A target already on is returned as it is. Raises
    `moxi_tunnel.errors.TunnelError` (its `exit_code` says which) or the core's
    `moxi.files.LockTimeout`."""
    from . import acts

    return acts.add(f"{module}/{name}", provider_name=provider, port=port,
                    hostname=hostname, replace_=replace, ask=confirm).address


def remove(module: str, name: str, *, confirm: Callable[[list[str]], bool],
           forget: bool = False) -> None:
    """Turn target `<module>/<name>` off, or with `forget` drop it from the record and
    leave its live config alone."""
    from . import acts

    acts.remove(f"{module}/{name}", ask=confirm, forget=forget)


def check(module: str | None = None, name: str | None = None, *,
          deadline: float | None = None):
    """The live comparison of one recorded target, or of all: a list of
    `moxi_tunnel.acts.EntryState`. `deadline` is a `time.monotonic()` value every read
    is cut to."""
    from . import acts

    return acts.check(module, name, deadline=deadline)


__all__ = ["Address", "Target", "address", "targets", "add", "remove", "check", "url",
           "from_entry"]
