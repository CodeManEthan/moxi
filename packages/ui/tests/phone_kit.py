"""Gateway-mode test shells over the fixture module, for the tests that reach the gateway
through the phone bridge."""

from __future__ import annotations

import socket
import time
from pathlib import Path

from moxi_ui.testing.phone import set_gateway, write_address
from moxi_ui.testing.shell import TestShell

#: Seconds a shell has to take the gateway's hold: two of its ticks and some.
HOLD_WAIT = 10.0


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


def gateway_shell(home: Path, site: Path, *, port: int | None = None, enabled: bool = True,
                  address: bool = True, start: bool = True, **options) -> TestShell:
    """A gateway-mode shell over the fixture: the switch as `enabled`, its gateway on
    `port` (a free one by default), and with `address` the tunnel entry `on` for that
    port. Started unless `start` is false; when it holds, the hold is waited for."""
    shell = TestShell(home, site=[site], gateway=True, leave_out=["tunnel"], **options)
    shell.gateway_port = port or free_port()
    set_gateway(shell, enabled=enabled, port=shell.gateway_port)
    if address:
        write_address(shell, target=shell.gateway_port)
    if start:
        shell.start()
        if enabled or address:
            wait_hold(shell, shell.gateway_port)
    return shell
