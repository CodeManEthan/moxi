"""Whether the phone's confirmed acts are mounted, and the reason when they aren't.

The acts mount only where no process but the shell can ever have served the gateway's
origin: the endpoint is the shell's socket, the address's target is that socket, and the
origin is not one a persistent mapping to a loopback port ever served. On a TCP endpoint
they aren't mounted, so the phone reads and every act stays at the desk.

The inputs are only what the shell holds or reads itself: the kind it resolved at start,
its own endpoint, and the tunnel's address as the last tick read it. Nothing a request
carries takes part. The reasons, the first that applies:

- `off`: the gateway is off, so the endpoint gives the off answer and nothing mounts;
- `record`: the last read of the tunnel's record failed, so the rest can't be read;
- `target`: the address's target isn't this shell's endpoint (`moxi tunnel repair` moves it);
- `tcp`: the endpoint is TCP;
- `origin`: the address's origin is retired.

`gateway.acts` in the runtime directory (0600) records the shell's pid, the value and the
reason for the doctor, and, where the provider check runs and a profile stands, whether the
shell has found the provider's uid (`uid_found`), and, when there are any, the modules whose
export the gateway refuses, module to why (`refusals`); the shell rewrites it whenever one
changes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from moxi import files

__all__ = ["ACTS_FILE", "REASONS", "Mount", "decide", "read_acts", "write_acts"]

ACTS_FILE = "gateway.acts"
REASONS = ("off", "record", "target", "tcp", "origin")


@dataclass(frozen=True)
class Mount:
    """The phone's acts: mounted or not, and the reason when not (None when mounted)."""

    mounted: bool
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"acts": self.mounted, "reason": self.reason}


def _targets(address: Any, kind: str, endpoint: str | int) -> bool:
    form = getattr(address, "form", "http")
    if kind == "unix":
        return form == "unix" and address.target == endpoint
    return form == "http" and address.target == endpoint


def decide(*, on: bool, kind: str, endpoint: str | int, address: Any,
           refused: bool) -> Mount:
    """The mount for a gateway that is `on` (or not), with the resolved `kind`, its own
    `endpoint` (the socket path, or the TCP port), the last good `address` read and whether
    the last read was `refused`."""
    if not on:
        return Mount(False, "off")
    if refused:
        return Mount(False, "record")
    if address is None or not _targets(address, kind, endpoint):
        return Mount(False, "target")
    if kind != "unix":
        return Mount(False, "tcp")
    if getattr(address, "retired", False):
        return Mount(False, "origin")
    return Mount(True, None)


def write_acts(runtime_dir: str | os.PathLike, mount: Mount, *, pid: int | None = None,
               uid_found: bool | None = None,
               refusals: Mapping[str, str] | None = None) -> None:
    """Rewrite `gateway.acts`: this process's pid, the value and the reason, `uid_found`
    unless it is None, and `refusals` unless there are none."""
    body: dict[str, Any] = {"pid": os.getpid() if pid is None else pid, **mount.as_dict()}
    if uid_found is not None:
        body["uid_found"] = uid_found
    if refusals:
        body["refusals"] = dict(refusals)
    files.atomic_write(Path(runtime_dir) / ACTS_FILE, json.dumps(body) + "\n")


def read_acts(runtime_dir: str | os.PathLike) -> dict[str, Any] | None:
    """`gateway.acts` as written, or None when it is missing or can't be read."""
    try:
        value = json.loads((Path(runtime_dir) / ACTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
