"""The Codex window attached to a hosted thread through ``--remote``.

The window is a foreign binary that keeps the terminal, so it runs under
``moxi.proc.spawn(lifeline="watch")``: it stays in the host's process group and session
and has the terminal exactly as the host has it (the terminal's foreground group is
never moved), and the watchdog ends it and its descendants when the host goes. So the
host must hold the terminal's foreground when it opens a window. If its app-server
generation ends, the host closes the old window and opens another after the resumed
thread is ready. If the operator closes a live window, the host ends too.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import termios
import threading
import time

from moxi import proc as moxi_proc

from .procs import TERM_GRACE, with_stdin

log = logging.getLogger("moxi.turns.codex.window")

ENTER_ALT_SCREEN = b"\x1b[?1049h"
LEAVE_ALT_SCREEN = b"\x1b[?1049l"
SHOW_CURSOR = b"\x1b[?25h"
RESTORE_SCREEN = ENTER_ALT_SCREEN + LEAVE_ALT_SCREEN + SHOW_CURSOR
JOB_CONTROL_SIGNALS = (signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU)

READY_WAIT = 180.0
POLL = 0.1
GENERATION_CHECK_WAIT = 2.0
WAITING = "waiting for the Codex thread to be ready (up to {wait:.0f}s; Ctrl-C stops)"


def terminal_fd():
    for fd in (0, 1, 2):
        with contextlib.suppress(OSError):
            if os.isatty(fd):
                return fd
    with contextlib.suppress(OSError):
        return os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    return None


def in_terminal_foreground(fd) -> bool:
    if fd is None:
        return False
    try:
        return os.tcgetpgrp(fd) == os.getpgrp()
    except OSError:
        return False


def window_argv(binary: str, thread_id: str, socket_path) -> list[str]:
    return [binary, "-c", "check_for_update_on_startup=false", "resume",
            str(thread_id), "--remote", f"unix://{socket_path}"]


class Terminal:
    """The terminal mode one window borrows, put back when it ends."""

    def __init__(self, fd):
        self.fd = fd
        self.saved = None
        self.hung_up = False

    def save(self) -> None:
        try:
            self.saved = termios.tcgetattr(self.fd)
        except termios.error:
            self.saved = None

    def take_back(self) -> None:
        if self.hung_up:
            return
        try:
            if self.saved is not None:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            os.write(self.fd, RESTORE_SCREEN)
        except (OSError, termios.error) as exc:
            log.warning("could not take the terminal back: %s", exc)


class Window:
    """One window process, a watched child: `proc.lifeline` closed ends it and its
    descendants."""

    def __init__(self, proc: subprocess.Popen, generation):
        self.proc = proc
        self.generation = generation
        self.status: int | None = None

    @property
    def exited(self) -> bool:
        return self.status is not None

    @property
    def exit_code(self) -> int | None:
        return None if self.status is None else os.waitstatus_to_exitcode(self.status)


class WindowSession:
    """Background owner for windows opened on successive app-server generations."""

    def __init__(self, host, *, binary: str, cwd: str, env, ready_wait=READY_WAIT,
                 fd=None, say=None):
        self.host = host
        self.binary = binary
        self.cwd = cwd
        self.env = env
        self.ready_wait = ready_wait
        self.fd = terminal_fd() if fd is None else fd
        self.say = say or (lambda message: print(message, file=sys.stderr, flush=True))
        if self.fd is None:
            raise RuntimeError("window=True needs a controlling terminal")
        self.terminal = Terminal(self.fd)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.windows: list[Window] = []

    def start(self) -> "WindowSession":
        self._thread = threading.Thread(
            target=self.run, daemon=True, name="turns-codex-window",
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for window in list(self.windows):
            if not window.exited:
                self.end(window)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=TERM_GRACE * 2)

    def run(self) -> None:
        while not self._stop.is_set():
            generation = self._wait_ready()
            if generation is None:
                return
            window = self.spawn(generation)
            while not self._stop.is_set():
                if generation.ended.is_set():
                    self.end(window)
                    self.terminal.take_back()
                    break
                if self.poll(window):
                    self.reap(window)
                    self.terminal.take_back()
                    if not generation.ended.is_set() and not self._stop.is_set():
                        if not self.host.generation_answers(
                                generation, GENERATION_CHECK_WAIT):
                            self.host.drop_generation(generation)
                            break
                        self.host.window_closed(window.exit_code)
                    return
                self._stop.wait(POLL)
            else:  # pragma: no cover - the condition exits through the loop body
                continue
            if self._stop.is_set():
                self.end(window)
                self.terminal.take_back()
                return

    def _wait_ready(self):
        deadline = time.monotonic() + self.ready_wait
        said = False
        while not self._stop.is_set() and time.monotonic() < deadline:
            generation = self.host.generation
            if (generation is not None and generation.ready.is_set()
                    and not generation.ended.is_set() and generation.thread_id):
                return generation
            if not said:
                self.say(WAITING.format(wait=self.ready_wait))
                said = True
            self._stop.wait(POLL)
        if not self._stop.is_set():
            self.host.window_failed(self.host.give_up_reason())
        return None

    def spawn(self, generation) -> Window:
        argv = window_argv(self.binary, generation.thread_id,
                           self.host.app_socket)
        self.terminal.save()
        fd = self.fd
        if fd != 0:
            # The window inherits the host's stdin, which is not this terminal.
            argv = with_stdin(os.ttyname(fd), argv, self.env)
        proc = moxi_proc.spawn(argv, lifeline="watch", cwd=self.cwd, env=self.env,
                               stdout=fd, stderr=fd)
        window = Window(proc, generation)
        self.windows.append(window)
        log.info("window pid %s opened on thread %s", proc.pid, generation.thread_id)
        return window

    def poll(self, window: Window) -> bool:
        if window.exited:
            return True
        try:
            pid, status = os.waitpid(window.proc.pid, os.WNOHANG | os.WUNTRACED)
        except ChildProcessError:
            window.status = 0
            window.proc.returncode = 0
            return True
        if pid == 0:
            return False
        if os.WIFSTOPPED(status):
            with contextlib.suppress(OSError):
                os.kill(window.proc.pid, signal.SIGCONT)
            return False
        window.status = status
        window.proc.returncode = os.waitstatus_to_exitcode(status)
        return True

    def end(self, window: Window) -> None:
        """SIGTERM to the window (and SIGCONT, should it be stopped), up to `TERM_GRACE`
        for it to end, then its lifeline closed, which ends what is left of it and its
        descendants. Only the window's own pid is signalled here: it shares the host's
        process group."""
        if not self.poll(window):
            for sig in (signal.SIGTERM, signal.SIGCONT):
                with contextlib.suppress(OSError):
                    os.kill(window.proc.pid, sig)
            self._wait(window, TERM_GRACE)
        self.reap(window)
        self._wait(window, TERM_GRACE + moxi_proc.KILL_GRACE)

    def reap(self, window: Window) -> None:
        """Close the window's lifeline: the watchdog ends the window, if it still runs,
        and every descendant it started."""
        lifeline = getattr(window.proc, "lifeline", None)
        if lifeline is not None:
            with contextlib.suppress(OSError):
                lifeline.close()

    def _wait(self, window: Window, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.poll(window):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
        return True


__all__ = [
    "READY_WAIT", "TERM_GRACE", "ENTER_ALT_SCREEN", "LEAVE_ALT_SCREEN", "SHOW_CURSOR",
    "RESTORE_SCREEN", "JOB_CONTROL_SIGNALS", "Terminal", "Window", "WindowSession",
    "in_terminal_foreground", "terminal_fd", "window_argv",
]
