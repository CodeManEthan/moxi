"""Spawn and supervise one ``codex app-server`` child.

The child is a foreign binary, so it holds no lifeline of its own: it runs under
``moxi.proc.spawn(lifeline="watch")``, whose watchdog ends it and its descendants when
the host goes, by any death. It stays in the host's process group and session. Its
stdin is ``/dev/null``: the terminal, when the host has one, belongs to the window.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import socket as socketlib
import subprocess
import time
from pathlib import Path

from moxi import proc as moxi_proc

from .codex_protocol import AppServerOffline
from .procs import TERM_GRACE, with_stdin

log = logging.getLogger("moxi.turns.codex.appserver")

BINARY = "codex"


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


__all__ = ["AppServer", "AppServerBusy", "BINARY", "listen_argv", "socket_answers"]
