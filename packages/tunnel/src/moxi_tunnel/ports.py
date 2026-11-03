"""Choosing a target's outside port.

Each outside port holds at most one address, and Serve and Funnel never share a port: a
port can't be both, and Funnel has only three, 443, 8443 and 10000.

- public (Funnel): with no `--port`, the first free port of the three, narrowed by the
  tailnet's funnel-ports grant. With `--port`, that port if it is one of them.
- own-devices (Serve): with no `--port`, the target's own loopback port when it is free and
  not a Funnel port; else the first free port above it that is not a Funnel port, up to
  65535; else the first such port from 1024 up. The default is total: it names a port or,
  with every port taken, refuses. `--port N` takes any free port, with one line when N is a
  Funnel port.
- Cloudflare: every target is on 443 under its own hostname, and two targets may not share
  a hostname.
- A fresh placement, the default choice for an entry on a Unix socket, also skips every
  retired port of the machine's host (an origin a persistent loopback mapping once
  served), starting from the port the declaration's port setting holds. An explicit
  `--port` is the user's, retired or not.

Free means no Tailscale entry of the record holds the port and the live config has no
`TCP` key for it. The old Tailscale entry a switch onto Cloudflare has not yet removed (the
entry's `from`) holds its port until the switch settles. A port held by another Moxi target is refused (exit 3) naming the holder.
An entry a newer tunnel wrote holds its port too, whatever its form, unless it is a
Cloudflare one: its port is refused, never read as foreign or free.
A port held by config Moxi did not apply is refused naming that config, unless the user
adds `--replace`: the foreign config then counts as free, and the add takes it over on
the port it would choose (or the one `--port` names). Only an add chooses a port: every
other re-apply keeps the record's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .errors import Refused
from .record import NEWER, Record, normal_host, old_tailscale

FUNNEL_PORTS = (443, 8443, 10000)
#: The one port of every Cloudflare target.
CLOUDFLARE_PORT = 443
LOWEST = 1024
HIGHEST = 65535


@dataclass(frozen=True)
class Choice:
    """A chosen port, whether the add takes it over from foreign config, and the lines the
    add prints about it."""

    port: int
    replace: bool = False
    lines: tuple[str, ...] = ()


def moxi_ports(record: Record, *, exclude: str | None = None) -> dict[int, str]:
    """The outside ports the record's Tailscale entries hold, each with its target. An
    entry a newer tunnel wrote, of any provider but Cloudflare, holds its port too, its
    target named as `<target> (written by a newer tunnel)`; and so does the old Tailscale
    entry of a switch onto Cloudflare that has not settled, the one its `from` names."""
    out: dict[int, str] = {}
    for name, entry in record.entries.items():
        if name == exclude:
            continue
        old = old_tailscale(entry)
        if old is not None:
            out.setdefault(old.port, name)
        if entry.provider == "cloudflare":
            continue
        if entry.known:
            out[entry.port] = name
        elif _is_port(entry.port):
            out.setdefault(entry.port, f"{name} ({NEWER})")
    return out


def _is_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= HIGHEST


def choose(reach: str, *, target: int, port: int | None = None,
           moxi: Mapping[int, str], foreign: Mapping[int, str],
           funnel_ports: Iterable[int] | None = None, replace: bool = False,
           retired: Iterable[int] = ()) -> Choice:
    """The outside port for an add of a target whose loopback port is `target` (for a
    fresh placement, the port its port setting holds).

    `moxi` maps each port another Moxi target holds to that target (`moxi_ports`);
    `foreign` each port the live config holds that no record entry does to a short
    description of what is there. `funnel_ports` are the ports the tailnet's grant lists,
    or None when it lists none. With `replace`, a foreign port counts as free and the
    `Choice` says when the port chosen is one. With no `port`, an own-devices choice
    skips `retired` too. Raises `Refused` (exit 3) when no port can be taken.
    """
    if reach == "public":
        return _public(port, moxi, foreign, funnel_ports, replace)
    if port is not None:
        _check_range(port)
        choice = _take(port, moxi, foreign, replace)
        if port in FUNNEL_PORTS:
            line = (f"port {port} is one of Funnel's three ports, which a public target "
                    "then can't use")
            return Choice(choice.port, choice.replace, (*choice.lines, line))
        return choice
    taken = {} if replace else foreign
    chosen = _serve_default(target, moxi, taken, frozenset(retired))
    return Choice(chosen, replace=chosen in foreign)


def _check_range(port: int) -> None:
    if not 0 < port <= HIGHEST:
        raise Refused(f"{port} is not a port", "give a port from 1 to 65535")


def _take(port: int, moxi: Mapping[int, str], foreign: Mapping[int, str],
          replace: bool) -> Choice:
    if port in moxi:
        holder = moxi[port]
        fix = ("upgrade moxi-tunnel, or choose another port" if holder.endswith(f"({NEWER})")
               else f"moxi tunnel remove {holder}, or choose another port")
        raise Refused(f"port {port} is held by {holder}", fix)
    if port in foreign:
        if replace:
            return Choice(port, replace=True)
        raise Refused(f"port {port} holds config Moxi did not apply: {foreign[port]}",
                      "choose another port, or add --replace to take it over (a copy "
                      "is kept)")
    return Choice(port)


def _public(port: int | None, moxi: Mapping[int, str], foreign: Mapping[int, str],
            funnel_ports: Iterable[int] | None, replace: bool) -> Choice:
    granted = set(FUNNEL_PORTS if funnel_ports is None else funnel_ports)
    allowed = [p for p in FUNNEL_PORTS if p in granted]
    if not allowed:
        raise Refused("the tailnet's funnel-ports grant allows none of 443, 8443 and 10000",
                      "add one of them to the funnel node attribute's ports")
    if port is not None:
        if port not in FUNNEL_PORTS:
            raise Refused(f"{port} is not a Funnel port", "give 443, 8443 or 10000")
        if port not in allowed:
            raise Refused(f"the tailnet's funnel-ports grant doesn't allow port {port}",
                          "give " + " or ".join(map(str, allowed)) + ", or add the port "
                          "to the grant")
        return _take(port, moxi, foreign, replace)
    for p in allowed:
        if p not in moxi and (replace or p not in foreign):
            return Choice(p, replace=p in foreign)
    held = [f"{p}: {moxi.get(p) or foreign.get(p)}" for p in allowed]
    raise Refused("every Funnel port is taken (" + "; ".join(held) + ")",
                  "remove one of those targets, or take one over with --replace")


def _serve_default(target: int, moxi: Mapping[int, str], foreign: Mapping[int, str],
                   retired: frozenset[int] = frozenset()) -> int:
    def free(p: int) -> bool:
        return (p not in moxi and p not in foreign and p not in FUNNEL_PORTS
                and p not in retired)

    if 0 < target <= HIGHEST and free(target):
        return target
    for p in range(max(target + 1, 1), HIGHEST + 1):
        if free(p):
            return p
    for p in range(LOWEST, HIGHEST + 1):
        if free(p):
            return p
    raise Refused("every port is taken", "remove a target")


def hostname_holder(hostname: str, record: Record, *, exclude: str | None = None) -> str | None:
    """The Cloudflare target of the record that already has `hostname`, besides `exclude`;
    an add of a second target on one hostname is refused."""
    want = normal_host(hostname)
    for name, entry in record.entries.items():
        if (name != exclude and entry.provider == "cloudflare"
                and isinstance(entry.host, str) and normal_host(entry.host) == want):
            return name
    return None


__all__ = ["FUNNEL_PORTS", "CLOUDFLARE_PORT", "Choice", "moxi_ports", "choose",
           "hostname_holder"]
