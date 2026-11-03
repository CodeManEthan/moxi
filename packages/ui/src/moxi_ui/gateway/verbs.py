"""`moxi ui gateway`: pair, devices, revoke, log, on, off.

Each verb works on files and runs other commands; none calls a route. The gateway's
listener has no token class, and the desktop's gateway routes need a browser session and
key, which a command doesn't hold.

- `pair` mints a pairing code in the ui's runtime directory, as the desktop does, and
  prints the URL; it works whether or not the shell runs, since a shell started later reads
  the same directory. It refuses first when this environment would put that directory
  somewhere a unit-started shell doesn't look.
- `devices` lists the device file; `revoke <id>` deletes one, and the running shell ends
  its sessions at its next tick.
- `log` prints the audit log's rows.
- `on` checks what the gateway needs, sets `gateway.enabled`, starts the shell, waits for it
  to hold a TCP endpoint, and adds the tunnel entry when there is none, by running `moxi
  tunnel add ui/gateway` in this terminal so tunnel's own warnings and question show.
- `off` clears `gateway.enabled`, which ends every phone session within 2 s, and asks
  whether to remove the tunnel entry too.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from moxi.paths import Layout, python

from .. import cli, config
from . import endpoint

__all__ = ["HOLD_WAIT", "cmd_devices", "cmd_log", "cmd_off", "cmd_on",
           "cmd_pair", "cmd_revoke", "dispatch"]

MODULE = "ui"
#: Seconds `on` waits for the shell to hold the TCP endpoint.
HOLD_WAIT = 5.0
POLL = 0.1
OFF_QUESTION = "Remove the tunnel entry too? The phone can't use the gateway while it is off."
OFF_PERSIST = ("While the entry stays, another program could answer the phone whenever Moxi "
               "isn't running.")

Refused = cli.Refused


# --- the seams tests replace ------------------------------------------------------------


def _run(argv: Sequence[str]) -> int:
    """Run a command in this terminal, its output and questions shown as they come."""
    return subprocess.run(list(argv), check=False).returncode


def _tty() -> bool:
    return sys.stdin is not None and sys.stdin.isatty()


def _ask(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


_sleep = time.sleep


def _moxi(*words: str) -> list[str]:
    return [str(python()), "-P", "-m", "moxi", *words]


def _layout() -> Layout:
    return Layout(MODULE)


# --- reading ------------------------------------------------------------------------------


def _check_runtime(environ: Mapping[str, str] | None) -> None:
    moved = endpoint.runtime_problem(environ)
    if moved:
        raise Refused(moved)


def _address() -> Any:
    """The tunnel's address for `ui/gateway` (None for none). Refuses when the tunnel is
    missing or too old, or its record can't be read."""
    from .address import REFUSED
    from .doctor import raise_tunnel, tunnel_state
    from .listener import read_tunnel

    state, reason = tunnel_state()
    if state == "absent":
        raise Refused("tunnel isn't installed; run `moxi install ui --with tunnel`")
    if state == "old":
        raise Refused(f"the installed tunnel is too old for the gateway: {reason}. `moxi "
                      "install ui --with tunnel` leaves an installed tunnel as it is, so raise "
                      f"it with `{raise_tunnel()}`")
    read = read_tunnel()
    if read.address == REFUSED:
        raise Refused(f"the tunnel's record can't be read: {read.reason}; see "
                      "`moxi doctor tunnel`")
    return read.address


# --- pair, devices, revoke, log -------------------------------------------------------------


def cmd_pair(args: argparse.Namespace, say: Callable[[str], Any], *,
             environ: Mapping[str, str] | None = None) -> int:
    from .pairing import NoAddress, mint

    _check_runtime(environ)
    address = _address()
    try:
        url = mint(_layout().runtime_dir(environ, create=True), address)
    except NoAddress as err:
        raise Refused(str(err)) from None
    say("Open this on the phone within 2 minutes, or scan the desktop's QR code:")
    say(url)
    return 0


def _when(stamp: Any) -> str:
    if not isinstance(stamp, (int, float)):
        return "?"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


def cmd_devices(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    from .desk import device_row
    from .devices import Devices

    found = [device_row(d) for d in Devices.of(_layout().state_dir()).all()]
    if not found:
        say("no paired devices")
        return 0
    for row in found:
        marks = [] if row["passkey"] else ["no passkey"]
        if row["stale"]:
            marks.append("stale: pair again")
        tail = f" ({', '.join(marks)})" if marks else ""
        say(f"{row['id']}  {row['name']}  paired {_when(row['created'])}, last used "
            f"{_when(row['used'])}, at {row['host']} by {row['provider']}{tail}")
    return 0


def cmd_revoke(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    from .audit import Audit
    from .devices import Devices

    state_dir = _layout().state_dir()
    if not Devices.of(state_dir).revoke(args.id):
        raise Refused(f"no paired device {args.id}; `moxi ui gateway devices` lists them")
    audit = Audit(state_dir)
    try:
        audit("revoke", {"device": args.id})
    finally:
        audit.close()
    say(f"revoked {args.id}; a running shell ends its sessions within 2 s")
    return 0


def cmd_log(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    from .audit import detail, read_rows

    if args.days < 1:
        raise Refused("--days takes a whole number of days, 1 or more")
    for row in read_rows(_layout().state_dir(), days=args.days):
        if args.json:
            say(json.dumps(row, sort_keys=True))
            continue
        say(f"{row.get('at', '?')} {row.get('kind', '?')} {detail(row)}".rstrip())
    return 0


# --- on, off --------------------------------------------------------------------------------


def _set_switch(on: bool) -> None:
    words = ("set", "--", MODULE, "gateway.enabled", "true") if on \
        else ("unset", "--", MODULE, "gateway.enabled")
    if _run(_moxi("config", *words)) != 0:
        raise Refused(f"`moxi config {' '.join(w for w in words if w != '--')}` failed")


def _start_shell(say: Callable[[str], Any]) -> None:
    """Start the shell when none runs. A socket that holds the shell's port starts it only
    at a connection, so the shell's unit is started outright."""
    info = cli.ensure_shell(config.port(), say=say)
    if info is not None:
        return
    try:
        acted = cli._units_start()
    except Exception as err:  # noqa: BLE001 - said, and the wait below decides
        say(f"the shell's unit did not start: {err}")
    else:
        for line in (line for a in acted for line in a.lines):
            say(line)
    if cli._wait_up(cli.UP_WAIT) is None:
        raise cli._failed_to_start(f"the shell did not answer within {cli.UP_WAIT:g} s")


def _wait_hold(runtime_dir: Any) -> int | None:
    deadline = time.monotonic() + HOLD_WAIT
    while True:
        port = endpoint.hold_port(runtime_dir)
        if port is not None or time.monotonic() >= deadline:
            return port
        _sleep(POLL)


def _doctor_text() -> str:
    from .doctor import gateway_row, read_facts

    row = gateway_row(read_facts())
    return row.detail + (f"; {row.fix}" if row.fix else "")


def cmd_on(args: argparse.Namespace, say: Callable[[str], Any], *,
           environ: Mapping[str, str] | None = None) -> int:
    from .listener import extra_installed

    _check_runtime(environ)
    if not extra_installed():
        raise Refused("the gateway extra isn't installed; run `moxi install ui --with gateway`")
    address = _address()
    kind = endpoint.resolve_kind(endpoint.system())
    if kind == "unix":
        try:
            endpoint.check_socket_path(endpoint.socket_path(_layout(), environ))
        except endpoint.PathRefused as err:
            raise Refused(str(err)) from None
    _set_switch(True)
    _start_shell(say)
    if kind == "tcp" and _wait_hold(_layout().runtime_dir(environ)) is None:
        raise Refused(f"the shell didn't take the gateway's port within {HOLD_WAIT:g} s: "
                      f"{_doctor_text()}")
    if address is None:
        say("adding the tunnel entry ui/gateway:")
        return _run(_moxi("tunnel", "add", "ui/gateway"))
    say(f"the gateway is on at {address.url}")
    return 0


def cmd_off(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    from .address import REFUSED
    from .listener import read_tunnel

    _set_switch(False)
    say("the gateway is off; every phone session ends within 2 s")
    address = read_tunnel().address
    if address is None or address == REFUSED:
        return 0
    question = OFF_QUESTION
    if endpoint.resolve_kind(endpoint.system()) == "tcp" and getattr(address, "form",
                                                                      "http") == "http":
        question += f" {OFF_PERSIST}"
    if not _tty():
        say(f"{question} To remove it: moxi tunnel remove ui/gateway")
        return 0
    if _ask(question):
        return _run(_moxi("tunnel", "remove", "ui/gateway"))
    say("the tunnel entry stays; the shell keeps its endpoint and answers that the gateway "
        "is off")
    return 0


# --- dispatch ---------------------------------------------------------------------------


HANDLERS = {"pair": cmd_pair, "devices": cmd_devices, "revoke": cmd_revoke, "log": cmd_log,
            "on": cmd_on, "off": cmd_off}


def dispatch(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    return HANDLERS[args.gateway_verb](args, say)
