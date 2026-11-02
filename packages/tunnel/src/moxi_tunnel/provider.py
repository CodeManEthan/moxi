"""The seam every provider sits behind, and the providers this version has.

A provider gives a recorded entry its live config. Each one answers:

- `ready(reach)`: the prerequisites an act checks before it runs a command, read from the
  provider's own JSON, as `Problem`s each naming its fix; `[]` when ready;
- `host(entry)`: the host an entry gets now, in the one host form (lower-case, no
  trailing dot);
- `apply(entry)`: puts the entry's config live; idempotent; raises `ApplyError` with the
  provider's stderr;
- `remove(entry)`: takes the entry's config out, only when the live config matches it;
- `live()`: what the provider runs now, keyed by port or hostname;
- `live_state(entry)`: how the live config stands against the entry, for `check`:
  `renamed`, `missing`, `changed`, or None when it matches.

Tailscale is the default for both reaches. The Cloudflare provider is not in this version
yet: `get("cloudflare")` refuses (exit 3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .errors import Refused
from .record import Entry

#: The provider an add takes when none is named, for either reach.
DEFAULT = "tailscale"


@dataclass(frozen=True)
class Problem:
    """A prerequisite that is missing, and the step that fixes it."""

    reason: str
    fix: str


class Provider(Protocol):
    name: str

    def ready(self, reach: str) -> list[Problem]: ...

    def host(self, entry: Entry | None = None) -> str: ...

    def apply(self, entry: Entry) -> None: ...

    def remove(self, entry: Entry) -> None: ...

    def live(self) -> dict: ...

    def live_state(self, entry: Entry) -> str | None: ...


def _tailscale(**kw) -> Provider:
    from .tailscale import Tailscale

    return Tailscale(**kw)


#: Each provider by name: a callable that makes one for one act or check.
FACTORIES: dict[str, Callable[..., Provider]] = {"tailscale": _tailscale}


def get(name: str, **kw) -> Provider:
    """A provider `name` for one act, reading its state afresh. Refuses (exit 3) a
    provider this version doesn't have."""
    factory = FACTORIES.get(name)
    if factory is None:
        if name == "cloudflare":
            raise Refused("the Cloudflare provider is not in this version of moxi-tunnel",
                          "use the Tailscale provider (the default), or upgrade moxi-tunnel")
        raise Refused(f"no provider {name!r}", "use tailscale or cloudflare")
    return factory(**kw)


__all__ = ["DEFAULT", "Problem", "Provider", "FACTORIES", "get"]
