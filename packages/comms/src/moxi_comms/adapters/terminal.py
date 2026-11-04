"""The terminal a wrapper's window borrows, the children a wrapper spawns, and the pidfd a
wrapper takes on a process, shared by the wrappers.

A window is a CLI's own full-screen interface, run as the wrapper's child in the
wrapper's terminal. The wrapper gives the window the terminal's foreground, takes it
back when the window ends, and puts the screen back the way a person expects it.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import signal
import sys

log = logging.getLogger("comms.terminal")

#: Written after every window end: enter the alternate screen, leave it, show the
#: cursor. A window ended by a signal gives the terminal mode back but leaves the
#: terminal on the alternate screen; a window that quit cleanly has already left it,
#: and a bare leave would then move the cursor back over its last lines. Entering
#: first makes the leave land on the alternate screen in both cases.
ENTER_ALT_SCREEN = b"\x1b[?1049h"
LEAVE_ALT_SCREEN = b"\x1b[?1049l"
SHOW_CURSOR = b"\x1b[?25h"
RESTORE_SCREEN = ENTER_ALT_SCREEN + LEAVE_ALT_SCREEN + SHOW_CURSOR

#: The signals a window wrapper ignores in every phase, so Ctrl-Z never suspends it
#: and it can take the terminal's foreground back from a background group. Windows has
#: no job control, so there the tuple is empty.
JOB_CONTROL_SIGNALS = tuple(getattr(signal, name) for name in ("SIGTSTP", "SIGTTIN", "SIGTTOU")
                            if hasattr(signal, name))


#: How long a child (a CLI server, a window) gets after SIGTERM before SIGKILL.
TERM_GRACE = 5.0
PR_SET_PDEATHSIG = 1                  # linux/prctl.h


def die_with_parent(parent_pid: int):
    """preexec_fn: ask the kernel to SIGTERM the child when the spawning thread exits,
    and close the race where it already did before we asked. Linux only, by name;
    elsewhere it is None and the child outlives a wrapper that dies without stopping
    it."""
    if not sys.platform.startswith("linux"):
        return None

    def _pre():
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0)
        if os.getppid() != parent_pid:
            os._exit(0)
    return _pre


#: The `pidfd_open` system call's number: the same on x86_64 and aarch64 (asm-generic).
SYS_PIDFD_OPEN = 434


def pidfd_open(pid: int) -> int:
    """A pidfd on `pid`, close-on-exec: `os.pidfd_open` where this Python has it, else on
    Linux the system call itself (some Linux builds of Python lack the wrapper, such as
    uv's managed 3.12). Elsewhere no pidfd exists, and this raises the AttributeError
    `os.pidfd_open` would. A failed call raises OSError, as `os.pidfd_open` does."""
    opener = getattr(os, "pidfd_open", None)
    if opener is not None:
        return opener(pid)
    if not sys.platform.startswith("linux"):
        raise AttributeError("module 'os' has no attribute 'pidfd_open'")
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    fd = syscall(ctypes.c_long(SYS_PIDFD_OPEN), ctypes.c_int(pid), ctypes.c_uint(0))
    if fd == -1:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return fd


def terminal_fd():
    """The wrapper's controlling terminal, or None when it has none."""
    for fd in (0, 1, 2):
        with contextlib.suppress(OSError):
            if os.isatty(fd):
                return fd
    with contextlib.suppress(OSError):
        return os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    return None


def in_terminal_foreground(fd) -> bool:
    """True when the wrapper's process group is the terminal's foreground group."""
    if fd is None:
        return False
    try:
        return os.tcgetpgrp(fd) == os.getpgrp()
    except OSError:
        return False


class Terminal:
    """The terminal the window borrows: its foreground group and its mode."""

    #: Where a failure to take the terminal back is said; a wrapper sets its own.
    log = log

    def __init__(self, fd):
        self.fd = fd
        self.saved = None
        self.hung_up = False

    def save(self) -> None:
        import termios  # POSIX only; the module's other names import on any system

        try:
            self.saved = termios.tcgetattr(self.fd)
        except termios.error:
            self.saved = None

    def give(self, pgid: int) -> None:
        with contextlib.suppress(OSError):
            os.tcsetpgrp(self.fd, pgid)

    def take_back(self) -> None:
        """Foreground and mode back to the wrapper, then the main screen and a visible
        cursor. Skipped on a terminal that has hung up."""
        if self.hung_up:
            return
        import termios

        try:
            os.tcsetpgrp(self.fd, os.getpgrp())
            if self.saved is not None:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            os.write(self.fd, RESTORE_SCREEN)
        except (OSError, termios.error) as e:
            self.log.warning("could not take the terminal back: %s", e)


__all__ = ["ENTER_ALT_SCREEN", "LEAVE_ALT_SCREEN", "SHOW_CURSOR", "RESTORE_SCREEN",
           "JOB_CONTROL_SIGNALS", "PR_SET_PDEATHSIG", "SYS_PIDFD_OPEN", "TERM_GRACE",
           "Terminal", "die_with_parent", "in_terminal_foreground", "pidfd_open",
           "terminal_fd"]
