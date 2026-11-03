"""The address rule: what one read of the tunnel's address does to the gateway's profile.

The gateway keeps the `(change, host, port, provider)` it built its profile from, `Kept`,
and compares each read with that, never with the last value it read. So a `None` between a
remove and an add changes nothing by itself: a re-add on the same host, port and provider
keeps the sessions whether or not a tick saw the `None`.

`step(kept, read)` gives the first row that matches:

| read | does |
|---|---|
| the gateway off (switch off, extra missing, tunnel without interface 2) | drop the profile, end every session |
| the record can't be read | keep the profile and its token |
| no address | keep the profile and its token; the endpoint gives the off answer |
| `applying` or `removing` | keep the profile and its token |
| `on`, no profile | build one, find the provider's uid |
| `on`, the same change | nothing |
| `on`, a new change, the same host, port and provider | keep the profile, keep the new token, find the uid again |
| `on`, a new change, the same host and provider, another port | build a new profile, end every session |
| `on`, a new change, another host or provider | build a new profile, end every session, mark the old host's devices stale |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["OFF", "REFUSED", "Kept", "Step", "step"]

#: The two reads that aren't an address: the gateway off, and a record that can't be read.
OFF = "off"
REFUSED = "refused"


@dataclass(frozen=True)
class Kept:
    """The address a profile was built from."""

    change: str
    host: str
    port: int
    provider: str

    @classmethod
    def of(cls, address: Any) -> Kept:
        return cls(address.change, address.host, address.port, address.provider)


@dataclass(frozen=True)
class Step:
    """What a read does. `kept` is the tuple after it (None: no profile stands);
    `build` asks for a new profile from it; `ends` ends every gateway session; `stale`
    names the `Kept` whose devices go stale; `find_uid` finds the provider's uid again."""

    row: str
    kept: Kept | None
    build: bool = False
    ends: bool = False
    stale: Kept | None = None
    find_uid: bool = False


def step(kept: Kept | None, read: Any) -> Step:
    """The row of the table that `read` matches: `OFF`, `REFUSED`, None or an `Address`."""
    if read == OFF:
        return Step("off", None, ends=kept is not None)
    if read == REFUSED:
        return Step("refused", kept)
    if read is None:
        return Step("none", kept)
    if read.state != "on":
        return Step(read.state, kept)
    if kept is None:
        return Step("build", Kept.of(read), build=True, find_uid=True)
    if read.change == kept.change:
        return Step("same", kept)
    new = Kept.of(read)
    if (new.host, new.provider) != (kept.host, kept.provider):
        return Step("host", new, build=True, ends=True, stale=kept, find_uid=True)
    if new.port != kept.port:
        return Step("port", new, build=True, ends=True, find_uid=True)
    return Step("token", new, find_uid=True)
