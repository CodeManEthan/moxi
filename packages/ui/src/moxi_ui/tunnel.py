"""The gateway's tunnel target, `ui/gateway`, and the ui's tunnel change listener.

The target is declared in the group `moxi.tunnel.targets`, which only a tunnel that reads
`upstream` and `retired` reads. A tunnel that predates them reads only `moxi.tunnel`, so it
never finds the target and never applies it as a plain loopback port without asking
`upstream()` first.

- `upstream()` runs in tunnel's process before every apply. It refuses with tunnel's
  `NotReady` while that process's environment would give the ui another runtime directory
  than a unit-started shell uses, so tunnel never applies a socket path or a port the shell
  doesn't hold. Otherwise it gives `unix:<socket path>` on a socket endpoint whose path
  passes its check, and `http://127.0.0.1:<port>` on TCP once `gateway.hold` names a
  running shell, so the provider never forwards to a port before the shell holds it.
- `retired()` gives the gateway's own copy of the origins a persistent loopback mapping of
  `ui/gateway` has served, kept in `<state>/gateway/retired.json` (0600). The file only
  grows; tunnel adds its origins to its own list before it places the target, so the list
  comes back after the tunnel's record is purged.
- `on_change(target, old, new)`: for `ui/gateway`, adds the origin of every persistent
  loopback address it is handed, old or new, to that copy; and when the new address has
  another host or provider than the old one, marks stale every device paired elsewhere.

Tunnel is reached only through `moxi.modules.load("tunnel", interface=2)`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from moxi import files
from moxi.paths import Layout

__all__ = ["GATEWAY", "RETIRED_FILE", "TARGET", "add_retired", "on_change", "read_retired",
           "retired", "upstream"]

MODULE = "ui"
#: The target's key in tunnel's record.
TARGET = "ui/gateway"
#: The gateway's copy of retired origins, under the ui's `gateway` store.
RETIRED_FILE = "retired.json"

GATEWAY = {"reach": "own-devices", "port_setting": "gateway.port",
           "upstream": "moxi_ui.tunnel:upstream", "retired": "moxi_ui.tunnel:retired",
           "doc": "the UI on your phone"}


def _not_ready(text: str) -> Exception:
    """Tunnel's `NotReady` with `text`; a plain error when tunnel can't be loaded, which
    tunnel refuses all the same."""
    from moxi import modules

    found = modules.load("tunnel", interface=2)
    kind = getattr(found.api, "NotReady", None) if found.state == modules.PRESENT else None
    return kind(text) if isinstance(kind, type) else RuntimeError(text)


def upstream(*, environ: Mapping[str, str] | None = None, kind: str | None = None,
             running: Callable[[int], bool] | None = None) -> str:
    """What the provider forwards `ui/gateway` to now. Raises tunnel's `NotReady` with the
    reason when there is nothing the shell holds to forward to."""
    from .gateway import endpoint

    moved = endpoint.runtime_problem(environ)
    if moved:
        raise _not_ready(moved)
    layout = Layout(MODULE)
    kind = kind or endpoint.resolve_kind(endpoint.system())
    if kind == "unix":
        path = endpoint.socket_path(layout, environ)
        try:
            endpoint.check_socket_path(path)
        except endpoint.PathRefused as err:
            raise _not_ready(str(err)) from None
        return f"unix:{path}"
    port = endpoint.hold_port(layout.runtime_dir(environ), running=running)
    if port is None:
        raise _not_ready("the shell doesn't hold the gateway's port yet; "
                         "run `moxi ui gateway enable`")
    return f"http://127.0.0.1:{port}"


# --- the copy of retired origins ----------------------------------------------------------


def retired_path(state_dir: str | os.PathLike | None = None) -> Path:
    from .gateway.devices import gateway_dir

    return gateway_dir(state_dir or Layout(MODULE).state_dir()) / RETIRED_FILE


def _origins(data: bytes | None) -> list[dict[str, Any]]:
    """The origins a file holds, in order; a file that can't be read raises."""
    if not data:
        return []
    value = json.loads(data)
    if not isinstance(value, list):
        raise ValueError(f"{RETIRED_FILE} holds no list")
    out = []
    for item in value:
        if (isinstance(item, Mapping) and isinstance(item.get("host"), str)
                and isinstance(item.get("port"), int) and not isinstance(item["port"], bool)):
            out.append({"host": item["host"], "port": item["port"]})
    return out


def read_retired(state_dir: str | os.PathLike | None = None) -> list[dict[str, Any]]:
    """The copy's origins, `{"host", "port"}` each; none when the file is missing."""
    try:
        data = retired_path(state_dir).read_bytes()
    except FileNotFoundError:
        return []
    return _origins(data)


def add_retired(origins: list[tuple[str, int]],
                state_dir: str | os.PathLike | None = None) -> None:
    """Add `origins` to the copy. Nothing is ever taken out, and a file that can't be read
    is never written over."""
    path = retired_path(state_dir)
    wanted = [(host.lower().rstrip("."), port) for host, port in origins]

    def grown(old: bytes | None) -> bytes:
        kept = _origins(old)
        seen = {(item["host"], item["port"]) for item in kept}
        for host, port in wanted:
            if (host, port) not in seen:
                seen.add((host, port))
                kept.append({"host": host, "port": port})
        return (json.dumps(kept, indent=1) + "\n").encode()

    files.private_dir(path.parent)
    files.update(path, grown, mode=0o600)


def retired() -> list[dict[str, Any]]:
    """The `retired` callable tunnel calls: the copy's origins."""
    return read_retired()


# --- the change listener -----------------------------------------------------------------


def _persistent(address: Any) -> bool:
    """Whether `address` is a persistent loopback mapping, one that forwards whether or not
    the shell runs."""
    return address is not None and getattr(address, "form", "http") == "http"


def on_change(target: str, old: Any, new: Any, *,
              state_dir: str | os.PathLike | None = None) -> None:
    """Tunnel's change listener: called once per changed address, in the acting process."""
    if target != TARGET:
        return
    state_dir = state_dir or Layout(MODULE).state_dir()
    origins = [(a.host, a.port) for a in (old, new) if _persistent(a)]
    if origins:
        add_retired(origins, state_dir)
    if new is not None and (old is None or old.host.lower() != new.host.lower()
                            or old.provider != new.provider):
        from .gateway.devices import Devices

        Devices.of(state_dir).stale_unless(new.host, new.provider)
