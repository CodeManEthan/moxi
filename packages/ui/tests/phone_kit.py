"""Gateway-mode test shells over the fixture module, for the tests that reach the gateway
through the phone bridge."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from moxi_ui.testing.phone import set_gateway, write_address
from moxi_ui.testing.shell import TestShell

#: Seconds a shell has to take the gateway's hold: two of its ticks and some.
HOLD_WAIT = 10.0
#: Ports `gateway_shell` tries when another program takes the one it picked.
PORT_TRIES = 3


def free_port() -> int:
    """A loopback port nothing holds now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def hold_port(shell: TestShell) -> int | None:
    """The port the shell's `gateway.hold` names while its pid runs, else None."""
    from moxi_ui.gateway import endpoint

    return endpoint.hold_port(shell.runtime_dir("ui"))


def wait_hold(shell: TestShell, port: int, *, timeout: float = HOLD_WAIT) -> None:
    """Wait for the shell's `gateway.hold` to name `port`."""
    deadline = time.monotonic() + timeout
    while hold_port(shell) != port:
        if time.monotonic() > deadline:
            raise AssertionError(f"the shell took no gateway hold on {port} in {timeout:g}s:\n"
                                 + "\n".join(shell.stderr()[-20:]))
        time.sleep(0.1)


def _has_entry(shell: TestShell) -> bool:
    """Whether the shell's tunnel record still holds `ui/gateway`."""
    record = shell.state_dir("tunnel") / "applied.json"
    try:
        return "ui/gateway" in json.loads(record.read_text(encoding="utf-8"))["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _hold_with_entry(shell: TestShell, port: int, *, timeout: float = HOLD_WAIT) -> bool:
    """Wait for the shell's hold on `port`: True once it holds it with its tunnel entry,
    False once the entry is gone, which the shell does when another program holds the
    port at a tick."""
    deadline = time.monotonic() + timeout
    while True:
        if hold_port(shell) == port:
            return _has_entry(shell)
        if not _has_entry(shell):
            return False
        if time.monotonic() > deadline:
            raise AssertionError(f"the shell took no gateway hold on {port} in {timeout:g}s:\n"
                                 + "\n".join(shell.stderr()[-20:]))
        time.sleep(0.1)


def gateway_shell(home: Path, site: Path, *, port: int | None = None, enabled: bool = True,
                  address: bool = True, start: bool = True, **options) -> TestShell:
    """A gateway-mode shell over the fixture: the switch as `enabled`, its gateway on
    `port` (a free one by default), and with `address` the tunnel entry `on` for that
    port. Started unless `start` is false; when it holds, the hold is waited for.

    A port picked here is free when picked, but the shell binds it later, and on a busy
    machine another program can take it in between. The shell then removes its tunnel
    entry, as it must, and holds the port at a later tick with no address. So a shell that
    lost the port picked here is stopped and started again on another one."""
    shell = TestShell(home, site=[site], gateway=True, leave_out=["tunnel"], **options)
    tries = PORT_TRIES if port is None else 1
    while True:
        shell.gateway_port = port or free_port()
        set_gateway(shell, enabled=enabled, port=shell.gateway_port)
        if address:
            write_address(shell, target=shell.gateway_port)
        if not start:
            return shell
        shell.start()
        if not address:
            if enabled:
                wait_hold(shell, shell.gateway_port)
            return shell
        if _hold_with_entry(shell, shell.gateway_port):
            return shell
        lost = (f"another program held the gateway's port {shell.gateway_port} when the "
                "shell tried to bind it, so the shell removed its tunnel entry:\n"
                + "\n".join(shell.stderr()[-20:]))
        shell.stop()
        tries -= 1
        if tries == 0:
            raise AssertionError(lost)
