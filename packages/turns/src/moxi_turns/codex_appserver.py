"""Spawn and supervise one ``codex app-server`` child.

The child is a foreign binary, so it holds no lifeline of its own: it runs under
``moxi.proc.spawn(lifeline="watch")``, whose watchdog ends it and its descendants when
the host goes, by any death. It stays in the host's process group and session. Its
stdin is ``/dev/null``: the terminal, when the host has one, belongs to the window.

``codex_rate_limits`` is the library's pull of the account's Codex rate limits: through a
live Codex host's ``usage.codex_limits``, else through a short-lived app-server of its own
(this module's ``AppServer`` and the WebSocket client), which starts no thread, so no
message reaches a model.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import socket as socketlib
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moxi import proc as moxi_proc

from .codex_protocol import AppServerOffline
from .procs import TERM_GRACE, with_stdin

log = logging.getLogger("moxi.turns.codex.appserver")

BINARY = "codex"
#: The app-server's read of the account's rate limits (no params), and its push.
LIMITS_READ = "account/rateLimits/read"
LIMITS_UPDATED = "account/rateLimits/updated"
#: How long a host's own `account/rateLimits/read` may take, in seconds.
LIMITS_READ_S = 2.5
#: `codex_rate_limits`'s default bound, in seconds.
PULL_S = 3.0


class AppServerBusy(AppServerOffline):
    """A live app-server already answers on the requested socket."""


def listen_argv(socket_path, binary: str = BINARY) -> list[str]:
    return [binary, "app-server", "--listen", f"unix://{socket_path}"]


def socket_answers(socket_path, timeout: float = 1.0) -> bool:
    sock = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(socket_path))
        return True
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            sock.close()


class AppServer:
    """One child, identified only by the ``Popen`` handle this object created."""

    def __init__(self, socket_path, *, env=None, cwd=None, binary: str = BINARY,
                 argv=None, log_path=None):
        self.socket_path = Path(socket_path)
        self.env = env
        self.cwd = cwd
        self.argv = list(argv) if argv else listen_argv(self.socket_path, binary)
        self.log_path = Path(log_path) if log_path else None
        self.process: subprocess.Popen | None = None
        self.starts = 0
        self._log = None

    def start(self) -> "AppServer":
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        if self._log is not None:
            with contextlib.suppress(OSError):
                self._log.close()
            self._log = None
        if self.socket_path.exists():
            if socket_answers(self.socket_path):
                raise AppServerBusy(
                    f"an app-server already listens on {self.socket_path}; refusing to "
                    "replace a live process"
                )
            with contextlib.suppress(OSError):
                self.socket_path.unlink()
        argv = with_stdin(os.devnull, self.argv, self.env)
        stderr = subprocess.DEVNULL
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open("ab")
            stderr = self._log
        self.process = moxi_proc.spawn(argv, lifeline="watch", cwd=self.cwd,
                                       env=self.env, stdout=stderr, stderr=stderr)
        self.starts += 1
        log.info("spawned app-server pid=%s socket=%s", self.process.pid,
                 self.socket_path)
        return self

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def returncode(self):
        return None if self.process is None else self.process.poll()

    def wait_for_socket(self, timeout: float = 30.0, interval: float = 0.05) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if socket_answers(self.socket_path, timeout=min(interval, 0.05)):
                return True
            if not self.alive:
                return False
            time.sleep(interval)
        return socket_answers(self.socket_path)

    def stop(self, grace: float = TERM_GRACE) -> None:
        """SIGTERM to the child, up to `grace` seconds for it to end, then its lifeline
        closed: the watchdog ends whatever of the child and its descendants is left
        (SIGTERM, then SIGKILL). Only the child's own pid is signalled here; it shares
        the host's process group."""
        proc, self.process = self.process, None
        if proc is not None:
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.send_signal(signal.SIGTERM)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=grace)
            lifeline = getattr(proc, "lifeline", None)
            if lifeline is not None:
                with contextlib.suppress(OSError):
                    lifeline.close()
            try:
                proc.wait(timeout=grace + moxi_proc.KILL_GRACE)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=grace)
        if self._log is not None:
            with contextlib.suppress(OSError):
                self._log.close()
            self._log = None
        with contextlib.suppress(OSError):
            self.socket_path.unlink()

    def restart(self) -> "AppServer":
        self.stop()
        return self.start()


@dataclass(frozen=True)
class CodexLimits:
    """One reading of the account's Codex rate limits.

    `payload` is `account/rateLimits/read`'s answer as given, unparsed (the limits, and
    `accountId` and `planType` beside them); `received_at` is when it arrived (Unix
    seconds); `via` is `host` (a live Codex host read it) or `app-server` (an app-server
    started for this read)."""

    payload: dict[str, Any]
    received_at: float
    via: str


def codex_rate_limits(*, timeout: float = PULL_S) -> CodexLimits | None:
    """The account's Codex rate limits now, or None, with the reason logged.

    First a live Codex host that offers `usage`, newest first: its `usage.codex_limits`
    reads them on the host's own app-server. Else, when `codex` is on `PATH`, an
    app-server of its own on a private socket: `initialize`, `account/rateLimits/read`,
    and it is stopped again. Neither starts a thread, so no message is sent. A host that
    errs or does not answer passes to the next way while time is left; past `timeout`
    seconds, None."""
    deadline = time.monotonic() + timeout
    reading = _from_hosts(deadline)
    if reading is not None:
        return reading
    if time.monotonic() >= deadline:
        log.info("no Codex rate limits within %g s", timeout)
        return None
    binary = shutil.which(BINARY)
    if binary is None:
        log.info("no Codex rate limits: no live Codex host answered and %s is not on "
                 "PATH", BINARY)
        return None
    try:
        return _from_app_server(binary, deadline)
    except Exception as exc:  # noqa: BLE001 - the pull answers None with the reason
        log.info("no Codex rate limits from an app-server of its own: %s",
                 str(exc).strip() or type(exc).__name__)
        return None


def _from_hosts(deadline: float) -> CodexLimits | None:
    from . import client

    try:
        live = client.sessions()
    except Exception as exc:  # noqa: BLE001 - no host is then the answer
        log.info("the live sessions could not be listed: %s", exc)
        return None
    try:
        for session in live:
            if session.harness != "codex" or "usage" not in session.capabilities:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                answer = session.codex_limits(timeout=remaining)
            except Exception as exc:  # noqa: BLE001 - the next way is tried
                log.info("the Codex host pid %s did not read the rate limits: %s",
                         session.pid, exc)
                continue
            payload, at = answer.get("payload"), answer.get("received_at")
            if isinstance(payload, dict) and isinstance(at, (int, float)) \
                    and not isinstance(at, bool):
                return CodexLimits(payload, float(at), "host")
            log.info("the Codex host pid %s could not read the rate limits: %s",
                     session.pid, answer.get("reason") or "no reading")
    finally:
        for session in live:
            with contextlib.suppress(Exception):
                session.close()
    return None


def _from_app_server(binary: str, deadline: float) -> CodexLimits:
    """`account/rateLimits/read` on an app-server started for it, under a private
    directory of its own; the app-server is stopped and the directory removed after."""
    from .codex_ws import AppServerClient

    def left() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("the time ran out")
        return remaining

    folder = Path(tempfile.mkdtemp(prefix="moxi-turns-limits-"))
    server = AppServer(folder / "app.sock", binary=binary)
    ws = None
    try:
        server.start()
        if not server.wait_for_socket(timeout=left()):
            raise AppServerOffline(f"the app-server did not open its socket "
                                   f"(exit {server.returncode})")
        ws = AppServerClient(server.socket_path, timeout=left())
        ws.connect(open_timeout=left())
        ws.initialize()
        payload = ws.call(LIMITS_READ, None, timeout=left())
        if not isinstance(payload, dict):
            raise AppServerOffline(f"{LIMITS_READ} answered {payload!r}")
        return CodexLimits(payload, time.time(), "app-server")
    finally:
        if ws is not None:
            ws.close()
        server.stop()
        shutil.rmtree(folder, ignore_errors=True)


__all__ = ["AppServer", "AppServerBusy", "BINARY", "CodexLimits", "LIMITS_READ",
           "LIMITS_UPDATED", "codex_rate_limits", "listen_argv", "socket_answers"]
