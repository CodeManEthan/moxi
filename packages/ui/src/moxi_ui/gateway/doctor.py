"""The gateway's rows in `moxi doctor ui`: `gateway`, `gateway login` and `gateway audit`.

Each is read from files, settings and the tunnel's record, never by calling the shell, so a
doctor run wakes nothing. What the running shell decided comes from what it writes in its
runtime directory: `gateway.acts` (whether the phone's acts are mounted, and why not) and
`gateway.hold` (the TCP port it holds); a hold it couldn't take is the latest `held` row of
its audit log.

`read_facts()` gathers everything once; the rows are pure functions of the `Facts`, so a
test states the facts it means.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moxi.doctor import Row
from moxi.paths import Layout

from . import endpoint
from .address import REFUSED

__all__ = ["ACTS_LINES", "Facts", "audit_row", "gateway_row", "login_row", "raise_tunnel",
           "read_facts", "rows", "tunnel_state"]

MODULE = "ui"
#: The phone's acts, as the doctor says them for each reason they aren't mounted.
ACTS_LINES = {
    "off": "the gateway is off",
    "record": "the tunnel's record can't be read",
    "tcp": "the gateway listens on a port another program could take while Moxi isn't "
           "running",
    "target": "the tunnel doesn't forward to this Moxi",
    "origin": "this address once served a tunnel entry that outlived Moxi",
    "gap": "the tunnel keeps forwarding for more than a second after Moxi stops",
    "persist": "the tunnel entry outlives Moxi",
    "tether": "the shell is tethering again",
}
_NOT_INSTALLED = ("missing", "nobackend")


@dataclass
class Facts:
    """What the doctor read. `switch` is None when the setting can't be read; `address`
    is tunnel's `Address`, None, or `REFUSED`; `acts` is `gateway.acts` when its pid
    runs; `held` the current `held` row."""

    system: str
    kind: str
    runtime_dir: str
    state_dir: str
    switch: bool | None = False
    port: int | None = None
    gateway_port: int | None = None
    moved: str | None = None
    extra: bool = False
    tunnel: str = "absent"
    tunnel_reason: str | None = None
    address: Any = None
    path_problem: str | None = None
    retired_problem: str | None = None
    shell_running: bool = False
    acts: dict[str, Any] | None = None
    hold: int | None = None
    held: dict[str, Any] | None = None
    devices: list[Any] = field(default_factory=list)
    uid_found: bool = True
    starts: str | None = None
    unit: Any = None

    @property
    def endpoint(self) -> str:
        if self.kind == "unix":
            return f"the socket {endpoint.socket_path(Layout(MODULE))}"
        return f"127.0.0.1:{self.gateway_port}"


# --- reading ------------------------------------------------------------------------------


def tunnel_state(load: Callable[..., Any] | None = None) -> tuple[str, str | None]:
    """`("absent", why)`, `("old", why)` or `("current", None)`: whether the installed
    tunnel offers interface 2."""
    from moxi import modules

    load = load or modules.load
    found = load("tunnel", interface=2)
    if found.state == modules.PRESENT and found.api is not None:
        return "current", None
    base = load("tunnel")
    if base.state == modules.ABSENT:
        return "absent", "tunnel isn't installed"
    return "old", found.reason or "the installed tunnel can't be loaded"


def raise_tunnel(prefix: str | None = None, which: Callable[[str], str | None] | None = None,
                 python: str | None = None) -> str:
    """The command that raises an installed tunnel to the `tunnel` extra's floor. `moxi
    install ui --with tunnel` counts the extra installed once any tunnel is, whatever its
    version, so it never raises one."""
    import shutil

    from moxi import cli
    from moxi.paths import python as moxi_python

    wanted = next((cli.requirement_text(req) for req in cli.requirements("moxi-ui", "tunnel")),
                  "moxi-tunnel>=0.2,<0.3")
    prefix = sys.prefix if prefix is None else prefix
    if (Path(prefix) / "uv-receipt.toml").is_file():
        return "uv tool upgrade moxi"
    which = which or shutil.which
    python = python or str(moxi_python())
    if which("uv"):
        return f"uv pip install --python {python} '{wanted}'"
    return f"{python} -m pip install '{wanted}'"


def _at(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    for shape in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, shape).replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
    return None


def _running(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool):
        return False
    import psutil

    return psutil.pid_exists(pid)


def _shell() -> tuple[bool, float | None]:
    """Whether the shell runs, by its runtime file's pid, and when it started."""
    from moxi_loopback.api import read_runtime

    info = read_runtime(MODULE)
    if info is None or not _running(info.pid):
        return False, None
    return True, _at(info.started_at)


def _unit(system: str) -> Any:
    """The shell's daemon unit row (the logon task's on Windows), or None."""
    from .. import cli

    for row in cli._units_status() or ():
        if getattr(row, "module", None) == MODULE and row.kind == "daemon":
            return row
    return None


def _starts() -> str | None:
    from moxi import modules, units

    found = modules.load(MODULE)
    if found.state != modules.PRESENT:
        return None
    return units.starts(found).get("shell")


def read_facts(*, environ: Any = None) -> Facts:
    """Read what the rows need, from files and settings only."""
    from .. import config
    from .audit import last_held
    from .devices import Devices
    from .listener import extra_installed, read_tunnel
    from .mount import read_acts
    from .provider import checks, provider_uid
    from ..tunnel import read_retired, retired_path

    layout = Layout(MODULE)
    system = endpoint.system()
    facts = Facts(system=system, kind=endpoint.resolve_kind(system),
                  runtime_dir=str(layout.runtime_dir(environ)),
                  state_dir=str(layout.state_dir(environ)))
    try:
        settings = config.current()
        facts.switch = settings["gateway.enabled"] is True
        facts.port, facts.gateway_port = settings["port"], settings["gateway.port"]
    except Exception:  # noqa: BLE001 - the core's settings row reports a bad file
        facts.switch = None
    facts.moved = endpoint.runtime_problem(environ)
    facts.extra = extra_installed()
    facts.tunnel, facts.tunnel_reason = tunnel_state()
    if facts.tunnel != "absent":
        read = read_tunnel()
        facts.address = read.address
        if read.address == REFUSED:
            facts.tunnel_reason = read.reason
    if facts.kind == "unix":
        try:
            endpoint.check_socket_path(endpoint.socket_path(layout, environ))
        except endpoint.PathRefused as err:
            facts.path_problem = str(err)
    try:
        read_retired(facts.state_dir)
    except (OSError, ValueError) as err:
        facts.retired_problem = f"{retired_path(facts.state_dir)} can't be read ({err})"
    facts.shell_running, started = _shell()
    acts = read_acts(facts.runtime_dir)
    facts.acts = acts if acts is not None and _running(acts.get("pid")) else None
    facts.hold = endpoint.hold_port(facts.runtime_dir)
    held = last_held(facts.state_dir)
    if (held is not None and facts.shell_running and started is not None
            and (_at(held.get("at")) or 0) >= started
            and (facts.kind != "tcp" or facts.hold is None)):
        facts.held = held
    facts.devices = Devices.of(facts.state_dir).all()
    address = facts.address
    if checks(system) and address is not None and address != REFUSED:
        facts.uid_found = provider_uid(address.provider) is not None
    if facts.switch:
        facts.starts = _starts()
        facts.unit = _unit(system)
    return facts


# --- the rows -----------------------------------------------------------------------------


def _acts_text(facts: Facts) -> str:
    if not facts.shell_running:
        return "off: Moxi isn't running"
    acts = facts.acts or {}
    if acts.get("acts") is True:
        return "on"
    reason = acts.get("reason")
    return f"off: {ACTS_LINES.get(reason, reason or 'the shell has not said why')}"


def _origin_fix(address: Any) -> str:
    if address.provider == "cloudflare":
        return ("route a new hostname to the tunnel, then `moxi tunnel remove ui/gateway` and "
                "`moxi tunnel add ui/gateway --provider cloudflare --hostname <new>`")
    return "moxi tunnel remove ui/gateway, then moxi ui gateway on"


def _fail(facts: Facts) -> tuple[str, str | None] | None:
    """The first failure, as (detail, fix)."""
    address = facts.address
    live = address is not None and address != REFUSED
    if facts.moved:
        return facts.moved, None
    if facts.switch and not facts.extra:
        return "the gateway extra isn't installed", "moxi install ui --with gateway"
    if facts.switch and facts.tunnel == "absent":
        return "tunnel isn't installed", "moxi install ui --with tunnel"
    if facts.tunnel == "old":
        return (f"the installed tunnel is too old for the gateway: {facts.tunnel_reason}",
                raise_tunnel())
    if facts.kind == "tcp" and facts.gateway_port is not None \
            and facts.gateway_port == facts.port:
        return (f"gateway.port is {facts.gateway_port}, the shell's own port",
                "moxi config set ui gateway.port <another port>")
    if facts.kind == "unix" and facts.path_problem:
        return facts.path_problem, None
    if facts.held is not None:
        if facts.kind == "tcp":
            removed = ("the tunnel entry was removed" if facts.held.get("removed", True)
                       else "there was no tunnel entry to remove")
            return (f"port {facts.gateway_port} was held by another program; {removed}",
                    "free the port and run `moxi ui gateway on`")
        return (f"the gateway couldn't hold its socket ({facts.held.get('reason')}); the "
                "tunnel entry was removed", "moxi ui gateway on")
    if facts.retired_problem:
        return (f"the gateway's copy of its retired origins, {facts.retired_problem}, so "
                "tunnel refuses every ui/gateway add",
                "move the file aside and run `moxi ui gateway on`; the copy refills from "
                "tunnel's record, unless tunnel was purged")
    if facts.switch and address is None:
        return "there is no ui/gateway tunnel entry", "moxi ui gateway on"
    if live and address.state != "on":
        return f"the ui/gateway tunnel entry is {address.state}", "moxi tunnel repair"
    reason = (facts.acts or {}).get("reason") if facts.shell_running else None
    if live and reason in ("target", "persist"):
        return f"the phone's acts are off: {ACTS_LINES[reason]}", "moxi tunnel repair"
    if live and not facts.uid_found:
        detail = "can't tell which user tailscaled runs as"
        if facts.system == "wsl":
            detail += ("; a Tailscale that runs in Windows, outside this VM, can't serve the "
                       "gateway")
        return detail, None
    if live and (facts.acts or {}).get("uid_found") is False:
        return ("the running Moxi hasn't yet found which user tailscaled runs as, so the "
                "phone is refused; it looks again every 2 s"), None
    return None


def _warns(facts: Facts) -> list[tuple[str, str | None]]:
    address = facts.address
    out: list[tuple[str, str | None]] = []
    if address == REFUSED:
        out.append((f"the tunnel's record can't be read ({facts.tunnel_reason}); the phone's "
                    "acts are off", "moxi doctor tunnel"))
        return out
    if address is None:
        return out
    if facts.switch is False:
        out.append(("gateway.enabled is off while the ui/gateway tunnel entry exists",
                    "moxi tunnel remove ui/gateway"))
    if address.exposure == "internet":
        out.append(("the address is reachable from the internet", None))
    stale = [d for d in facts.devices if d.stale]
    if stale:
        out.append((f"{len(stale)} paired device{'s' if len(stale) != 1 else ''} stale "
                    f"({', '.join(d.name for d in stale)}); pair again, or revoke",
                    "moxi ui gateway revoke <id>"))
    reason = (facts.acts or {}).get("reason") if facts.shell_running else None
    if reason == "origin":
        out.append((f"the phone's acts are off: {ACTS_LINES['origin']}", _origin_fix(address)))
    elif reason == "tether":
        out.append((f"the phone's acts are off: {ACTS_LINES['tether']}", None))
    return out


def gateway_row(facts: Facts) -> Row:
    """The `gateway` row."""
    address = facts.address
    if not facts.switch and address is None:
        return Row(MODULE, "gateway", "off",
                   "gateway.enabled is off and there is no ui/gateway tunnel entry")
    failed = _fail(facts)
    if failed is not None:
        return Row(MODULE, "gateway", "fail", failed[0], failed[1])
    warns = _warns(facts)
    if address is not None and address != REFUSED:
        count = len(facts.devices)
        detail = (f"on at {address.url} (outside port {address.port}), endpoint "
                  f"{facts.endpoint}, {count} device{'s' if count != 1 else ''}, the "
                  f"phone's acts {_acts_text(facts)}")
    else:
        detail = f"endpoint {facts.endpoint}"
    if warns:
        fixes = [fix for _, fix in warns if fix]
        return Row(MODULE, "gateway", "warn",
                   "; ".join([*(text for text, _ in warns), detail]),
                   "; ".join(fixes) or None)
    return Row(MODULE, "gateway", "ok", detail)


def login_row(facts: Facts) -> Row:
    """The `gateway login` row: whether the shell's unit starts at login while the
    gateway is on."""
    if not facts.switch:
        return Row(MODULE, "gateway login", "off", "the gateway is off")
    unit = facts.unit
    installed = unit is not None and unit.state not in _NOT_INSTALLED \
        and getattr(unit, "enabled", None) is not False
    if facts.system == "win32":
        if installed:
            return Row(MODULE, "gateway login", "ok", "the logon task starts the shell at "
                                                      "every logon")
    elif installed and facts.starts == "login":
        detail = "the shell starts at login"
        if facts.system == "darwin":
            detail += ", from the next login"
        return Row(MODULE, "gateway login", "ok", detail)
    return Row(MODULE, "gateway login", "warn",
               "the gateway is on and the shell's unit doesn't start at login",
               "moxi install ui")


def _bad(path: Path, want: int, uid: int) -> str | None:
    try:
        st = path.stat()
    except OSError as err:
        return f"{path} can't be read: {err.strerror}"
    if st.st_uid != uid:
        return f"{path} isn't yours (uid {st.st_uid})"
    if stat.S_IMODE(st.st_mode) & 0o077:
        return f"{path} is mode {stat.S_IMODE(st.st_mode):o}, not {want:o}"
    return None


def audit_row(facts: Facts, *, uid: int | None = None) -> Row:
    """The `gateway audit` row: the audit directory 0700 and its files 0600, this
    user's."""
    from .audit import audit_dir

    folder = audit_dir(facts.state_dir)
    found = sorted(folder.iterdir()) if folder.is_dir() else []
    if not found and not folder.is_dir():
        if not facts.switch:
            return Row(MODULE, "gateway audit", "off", "the gateway is off and has no audit log")
        return Row(MODULE, "gateway audit", "ok", f"{folder}: no rows written yet")
    if facts.system == "win32":
        return Row(MODULE, "gateway audit", "ok", f"{folder}, {len(found)} files")
    uid = os.getuid() if uid is None else uid
    problem = _bad(folder, 0o700, uid)
    if problem is None:
        problem = next((p for p in (_bad(f, 0o600, uid) for f in found if f.is_file()) if p),
                       None)
    if problem is not None:
        return Row(MODULE, "gateway audit", "fail", problem,
                   f"chmod 700 {folder}, and chmod 600 each file in it")
    return Row(MODULE, "gateway audit", "ok", f"{folder}, {len(found)} files, private")


def rows(facts: Facts | None = None) -> Iterable[Row]:
    """The three rows, in order."""
    facts = facts or read_facts()
    return [gateway_row(facts), login_row(facts), audit_row(facts)]
