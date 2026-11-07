"""Moxi's test kit: a pytest plugin that fences every test in, and the helpers tests share.

A package loads it from its root `conftest.py`:

    pytest_plugins = ["moxi_dev.testing"]

Installing moxi-dev arms nothing by itself (there is no `pytest11` entry point); only a
package that names the plugin is fenced.

The fence, around every test (the autouse fixture `fence`):

- The environment. Emptied to `PATH`, the locale (`LANG`, `LANGUAGE`, `LC_*`) and the
  variables pytest and xdist set for themselves (`PYTEST_CURRENT_TEST`, `PYTEST_VERSION`,
  `PYTEST_XDIST_WORKER`, `PYTEST_XDIST_WORKER_COUNT`, `PYTEST_XDIST_TESTRUNUID`). Then
  every harness home a package reads is pointed into the test's tmp: `HOME` and the XDG
  homes under `tmp_path / "home"`, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` inside it, and
  `XDG_RUNTIME_DIR` and each installed module's `MOXI_<MODULE>_HOME` in a short
  per-test directory (see `short_dir`), so a socket under them keeps a short path.
  `TMPDIR`, and `tempfile`'s own default with it, point into that short directory too,
  so a temp file the test or a child makes lands in the test's tmp and goes with it.
  OpenCode reads its homes through `XDG_*`; its content variables (`OPENCODE_*`) are
  cleared with the rest. A package that starts reading another home adds it to
  `HARNESS_HOMES`.
  On Windows the fence does the same for the user's own folders, so no test sees or
  writes the real profile: `USERPROFILE` is the test's home, `HOMEDRIVE` and `HOMEPATH`
  are that home split at its drive, `LOCALAPPDATA` and `APPDATA` are under it, and so
  is each per-user folder platformdirs reads through a `WIN_PD_OVERRIDE_*` variable
  (without one it asks Windows for the real folder, whatever `APPDATA` says); see
  `WINDOWS_HOMES`. `TEMP` and `TMP` point where `TMPDIR` does. The system's own
  variables a process needs (`SYSTEMROOT`, `PATHEXT`, `COMSPEC` and the rest of
  `WINDOWS_KEPT_ENV`) are kept. Off Windows none of these is set or kept.
- Byte code. `sys.dont_write_bytecode` is set for the test, so a module first imported
  inside it is compiled in memory and nothing is written next to its source.
- Writes. An audit hook (`sys.addaudithook`) refuses, with `FenceError`, a write whose
  path resolves outside the test's own tmp: the run's pytest temp root (`--basetemp`,
  else the `pytest-of-<user>/pytest-<n>` directory, or under xdist the worker's own
  directory in it), which holds the test's `tmp_path` and whatever `tmp_path_factory`
  makes, and the test's short directory, with every directory `short_dir` has made and
  not yet removed. The rest of the system temp directories (other runs, other
  processes' files) is outside. Besides those it lets through, and only these:
  the null device, `/dev/tty` and `/dev/pts/*` (terminals). The null device is
  `os.devnull`; on Windows it is either spelling a write to `nul` resolves to:
  `\\\\.\\nul`, and a drive or UNC path whose last part is `nul` (a relative `nul`
  resolves to one in the working directory), which Windows opens as the device. A
  `\\\\?\\` or `\\\\.\\` path to a file named `nul` creates a real file, so it is
  refused; so are `nul.txt` and `nul:`, which only some Windows versions open as the
  device. On Windows a long-form path (`\\\\?\\C:\\...`, which pytest uses to remove its
  temp directories) is judged as the plain path it names, so it is let through where
  that path is; one that names no plain path (a `..` past the prefix) is refused.
  A write is: `open` for writing or
  creating, `os.mkdir`, `os.rename`/`os.replace` (both ends), `os.remove`, `os.rmdir`,
  `os.symlink` and `os.link` (the new name), `os.truncate`, `os.chmod`, `os.chown`,
  `os.utime`, `os.setxattr`, `os.removexattr`, `shutil.rmtree`, `shutil.copyfile`,
  `shutil.copytree`, `shutil.move` (the destination), `sqlite3.connect` to a file
  (not `:memory:`, not a `mode=ro` or `mode=memory` URI), `tempfile.mkstemp`,
  `tempfile.mkdtemp`, and `socket.bind` to a filesystem `AF_UNIX` path.
- The network. The same hook refuses `socket.connect`, `socket.sendto` and
  `socket.sendmsg` to an `AF_INET`/`AF_INET6` address that is not loopback (`127.0.0.0/8`,
  `::1`, `::ffff:127.*`, the unspecified address, `localhost` and `*.localhost`; any
  other host name is refused, though Python has looked it up before the hook sees the
  connect, so a lookup alone is not fenced), and a connect to a filesystem `AF_UNIX`
  socket outside the allowed roots (a real daemon's socket, a tmux or X11 socket under
  `/tmp`). A `live` test lifts the network part, both the address check and the
  `AF_UNIX` connect check; its writes, a `bind` included, stay fenced.

The hook can't be removed, so it checks a flag the fixture raises for the test's setup,
call and teardown and lowers after. The fixture is function-scoped, so a fixture of any
wider scope (class, module, package or session) runs unfenced: it sees the real
environment and may write anywhere. A pytest run inside a test, in process
(`pytester.runpytest()`), fences its own tests and puts the outer test's fence back
when it ends. A refusal
raises `FenceError` (a `PermissionError`) where the write or connect was tried and is
also recorded, so a test that catches the error still fails. The hook sees this process
only: a subprocess gets the fenced environment but not the hook. Paths are judged after
symlinks resolve (a link inside tmp to a place outside is outside); a name relative to a
`dir_fd` is resolved through `/proc/self/fd` and let through where that can't be read.

uv in a child: a test whose subprocess runs uv asks for the `uv_env` fixture and passes
it as that child's environment. It is the fenced environment plus uv's real cache
(`UV_CACHE_DIR`) and installed Pythons (`UV_PYTHON_INSTALL_DIR`), read when pytest
starts, before any test empties the environment; where neither is set, uv's own default
under the real home (`$XDG_CACHE_HOME/uv` or `~/.cache/uv`, `$XDG_DATA_HOME/uv/python`
or `~/.local/share/uv/python`; on Windows `%LOCALAPPDATA%\\uv\\cache` and
`%APPDATA%\\uv\\python`, or `%APPDATA%\\uv\\data\\python` where uv's older `data` folder
is there). It
also sets `UV_PYTHON_DOWNLOADS=never`, so a child finds the Pythons already installed
and never adds one to the real directory. The test's own process stays fenced.

Symlinks: Windows lets an account make one only with the symlink privilege or with
Developer Mode on. `can_symlink()` tries a real one once per process and keeps the
answer; a test that needs one asks for the `needs_symlinks` fixture, or calls
`need_symlinks()` at the point it needs one, and is skipped where the account may not.

The fsync seam: `os.fsync` (and `os.fdatasync`) is a no-op in every test that is not
marked `honest_fsync`; the no-op still refuses a closed descriptor. Code must call
`os.fsync(...)` through the module for the seam to reach it.

The port seam: before the core's unit plan takes a daemon's socket address, it asks
whether anything listens there with a TCP connect (`moxi.units._plan.answers`). In every
test not marked `honest_ports` or `live` that connect is replaced by one that answers
"nothing listens", so what happens to listen on this machine (a daemon on its default
port, another run's server) never decides a test. A test that wants a listener seen
either says so through its own stand-in or is marked `honest_ports` and listens for real.

Markers registered here: `browser`, `live`, `slow`, `build`, `touches`, `finding`,
`timed`, `honest_fsync` and `honest_ports`. A package's `addopts` deselects the four tiers in `TIERS`; a
`live` test also needs `MOXI_TEST_LIVE=1` in the environment pytest starts with, else it
is skipped.

Timed tests. A test that asserts a wall-clock budget (a median under so many
milliseconds, an import under its share of a hook's ceiling) is marked `timed`, whatever
its tier. Beside other tests on a loaded machine such a test measures the machine, so
`moxi-dev check` never runs one beside another: its parallel pass holds the timed tests
back and a serial pass afterwards runs them alone. A test whose bound only tells a
deadline or timeout that fired from one that never did (a 0.3 s timeout that must return
in under 0.9 s) is not timed. A plain `pytest`, with neither of the values below, runs a
timed test where it falls, like any other.

Selection. Five variables, read from the environment pytest starts with, change what
runs; `moxi-dev check` sets them for each suite, and a contributor may set them by hand:

- `MOXI_TEST_TIERS=all` puts back every test of the `browser`, `slow` and `build` tiers
  that the package's `-m` deselected. A `live` test is never put back.
- `MOXI_TEST_CHANGED=<file>` names a file of repository paths that changed, separated by
  NUL bytes. A `browser`, `slow` or `build` test (never a `live` one) marked
  `@pytest.mark.touches("packages/<p>/<glob>")` is put back when one of its globs
  matches one of the paths (`touches_match`). A glob that does not start with
  `packages/` is refused when the tests are collected, since it could never match.
- `MOXI_TEST_FINDING=L<n>[,L<n>...]` runs only the tests marked `finding` with one of
  those arguments, a tier test among them put back as above; every other test is
  deselected.
- `MOXI_TEST_TIMED=hold` deselects every `timed` test the run would otherwise run, and
  the run's `durations.json` names them under `held` (under xdist the workers collect,
  and each hands its list to the controller). `MOXI_TEST_TIMED=only` deselects every
  test that is not `timed`. Both apply after the rest of the selection and the run's own
  `-m` and `-k`, so with the same other variables an `only` run runs exactly the tests a
  `hold` run held.
- `MOXI_TEST_LEAVE_OUT=<tier>[,<tier>...]` names tiers (of `browser`, `slow` and
  `build`) whose tests are never put back, whatever the variables above ask; the
  package's `-m` still deselects them. `moxi-dev check` sets `browser` for a package
  whose browser tests can't run because Playwright's Chromium can't start.

A test put back gets the marker `lifted`, and the run's `-m` expression becomes
`(<expression>) or lifted`, so pytest's own deselection keeps it and `-k` still applies.

Durations. Each run writes `durations.json`: every test's seconds by phase (`setup`,
`call`, `teardown`), its outcome and, for a test this run put back, `lifted` with its
tiers, from the reports the main process sees (under xdist, the controller's), and,
under `MOXI_TEST_TIMED=hold`, the `held` tests' ids. It goes to the path in
`MOXI_TEST_DURATIONS`, else to
`moxi-dev/durations.json` in pytest's cache directory; with neither (the cache plugin
off) nothing is written.

A seam with two implementations: `Seam` parametrizes a fixture over every
implementation for the contract suites and gives the rest the default one.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import ipaddress
import json
import ntpath
import os
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from enum import Enum
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

#: How often a served server looks for its shutdown request, in seconds.
SHUTDOWN_POLL = 0.05

#: The tiers a package's `addopts` deselects, so a plain `pytest` runs units and seams.
TIERS = ("browser", "slow", "build", "live")

#: Every marker the kit registers, with the line `pytest --markers` shows.
MARKERS = {
    "browser": "drives a browser; deselected by a plain pytest run",
    "live": "reaches real services; deselected by a plain pytest run, and runs only "
            "with MOXI_TEST_LIVE=1",
    "slow": "takes long; deselected by a plain pytest run",
    "build": "builds and installs distributions end to end, past what every run can "
             "afford (minutes, uv's warm cache); a quick offline build stays in the "
             "plain run; deselected by a plain pytest run",
    "touches(*globs)": "the repository paths whose change puts this test back in "
                       "moxi-dev check",
    "finding(tag)": "the finding this test holds",
    "timed": "asserts a wall-clock budget; moxi-dev check runs it alone, in a serial "
             "pass after the parallel one",
    "honest_fsync": "os.fsync is the real one in this test",
    "honest_ports": "the unit plan's listener check makes its real TCP connect in this "
                    "test",
}

#: The variable a `live` test needs, set to 1.
LIVE_ENV = "MOXI_TEST_LIVE"

#: `all`: every tier but `live` runs (module doc, Selection).
TIERS_ENV = "MOXI_TEST_TIERS"
#: A file of the repository paths that changed, NUL-separated (module doc, Selection).
CHANGED_ENV = "MOXI_TEST_CHANGED"
#: `L<n>[,L<n>...]`: only the tests that carry one of these findings run.
FINDING_ENV = "MOXI_TEST_FINDING"
#: Where `durations.json` goes.
DURATIONS_ENV = "MOXI_TEST_DURATIONS"
#: `hold` or `only`: what a run does with the `timed` tests (module doc, Selection).
TIMED_ENV = "MOXI_TEST_TIMED"
TIMED_MODES = ("hold", "only")
#: Tiers this run never puts back, comma-separated (module doc, Selection).
LEAVE_OUT_ENV = "MOXI_TEST_LEAVE_OUT"

#: The marker of a test that asserts a wall-clock budget (module doc, Timed tests).
TIMED = "timed"
#: The key under which an xdist worker hands the controller the tests it held.
_HELD_KEY = "moxi_dev_held"

#: Where every `touches` glob starts: the changed paths are the repository's, and each
#: package is under it.
TOUCHES_ROOT = "packages/"

#: The marker the kit sets on a tier test it puts back; not for tests to use.
LIFTED = "lifted"

#: The shape of a finding's name, as `finding(...)` and `MOXI_TEST_FINDING` take it.
FINDING_NAME = re.compile(r"L[0-9]+")

#: The tiers a selection can put back: every tier but `live`.
LIFTABLE = tuple(tier for tier in TIERS if tier != "live")

#: The harness homes the fence points under the test's home, each relative to it.
HARNESS_HOMES = {
    "HOME": ".",
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "XDG_STATE_HOME": ".local/state",
    "XDG_CACHE_HOME": ".cache",
    "CLAUDE_CONFIG_DIR": ".claude",
    "CODEX_HOME": ".codex",
}

#: Variables carried into the fence unchanged, when set.
KEPT_ENV = ("PATH", "LANG", "LANGUAGE", "PYTEST_CURRENT_TEST", "PYTEST_VERSION",
            "PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_TESTRUNUID")

#: Whether this is Windows, where the fence also moves the user's folders and keeps the
#: system's variables; tests replace it.
WINDOWS = os.name == "nt"

#: On Windows, the user's folders the fence also points under the test's home, each
#: relative to it, laid out as Windows lays out a profile. The `WIN_PD_OVERRIDE_*`
#: variables are platformdirs' per-user folders. `HOMEDRIVE` and `HOMEPATH` are the home
#: split at its drive, and `TEMP` and `TMP` are the test's short directory.
WINDOWS_HOMES = {
    "USERPROFILE": ".",
    "LOCALAPPDATA": "AppData/Local",
    "APPDATA": "AppData/Roaming",
    "WIN_PD_OVERRIDE_LOCAL_APPDATA": "AppData/Local",
    "WIN_PD_OVERRIDE_APPDATA": "AppData/Roaming",
    "WIN_PD_OVERRIDE_USER_PROGRAM_FILES": "AppData/Local/Programs",
    "WIN_PD_OVERRIDE_PROGRAMS": "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
    "WIN_PD_OVERRIDE_TEMPLATES": "AppData/Roaming/Microsoft/Windows/Templates",
    "WIN_PD_OVERRIDE_PERSONAL": "Documents",
    "WIN_PD_OVERRIDE_DOWNLOADS": "Downloads",
    "WIN_PD_OVERRIDE_DESKTOPDIRECTORY": "Desktop",
    "WIN_PD_OVERRIDE_MYPICTURES": "Pictures",
    "WIN_PD_OVERRIDE_MYVIDEO": "Videos",
    "WIN_PD_OVERRIDE_MYMUSIC": "Music",
}

#: On Windows, the system's variables carried into the fence unchanged, when set: what a
#: process needs to start and load its libraries (`SYSTEMROOT` above all: sockets and
#: OpenSSL need it), `PATHEXT` to find a program by its bare name, `COMSPEC` for a
#: shell, and `USERNAME`, the only place `getpass.getuser` finds the user's name there.
WINDOWS_KEPT_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "OS",
                    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "USERNAME")

#: What `uv_env` adds to the fenced environment, besides uv's two directories.
_UV_NO_DOWNLOADS = {"UV_PYTHON_DOWNLOADS": "never"}

_KEPT_PREFIXES = ("LC_",)
_GROUP = "moxi.modules"
_MODULE_NAME = re.compile(r"[a-z0-9]+")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_REAL_FSYNC = os.fsync
_REAL_FDATASYNC = getattr(os, "fdatasync", None)


class FenceError(PermissionError):
    """A write outside the test's tmp, or a connect off this machine, the fence refused."""


@dataclasses.dataclass(frozen=True)
class Fence:
    """What the fence set up for one test."""

    #: `HOME`, under the test's `tmp_path`.
    home: Path
    #: `XDG_RUNTIME_DIR`, in the test's short directory.
    runtime: Path
    #: The test's short directory, removed after the test.
    short: Path
    #: The environment as the fence left it.
    env: Mapping[str, str]
    #: Where a write may land.
    roots: tuple[Path, ...]

    def module_home(self, module: str) -> Path:
        """Where `MOXI_<MODULE>_HOME` points for `module`."""
        return self.short / module


# --- the audit hook --------------------------------------------------------------------

class _Guard:
    """The hook's state, shared by every thread of the process."""

    def __init__(self) -> None:
        self.active = False
        self.network = True
        self.roots: tuple[str, ...] = ()
        self.short: str | None = None          # the current test's short directory
        self.breaches: list[str] = []
        self.reported = 0

    def save(self) -> tuple:
        return (self.active, self.network, self.roots, self.short, list(self.breaches),
                self.reported)

    def restore(self, saved: tuple) -> None:
        (self.active, self.network, self.roots, self.short, breaches,
         self.reported) = saved
        self.breaches[:] = breaches


_GUARD = _Guard()
_LOCAL = threading.local()
_INSTALLED = False
#: The directories `short_dir` has made and not yet removed, by their real paths.
_SHORT_DIRS: set[str] = set()


def _fd_path(dir_fd: object) -> str | None:
    if dir_fd is None or dir_fd == -1:
        return os.getcwd()
    try:
        return os.readlink(f"/proc/self/fd/{int(dir_fd)}")  # type: ignore[call-overload]
    except (OSError, ValueError, TypeError):
        return None


def _resolve(path: object, dir_fd: object = None, *, follow: bool = True) -> str | None:
    """The real path a write to `path` lands on, or None when it can't be told."""
    if path is None or isinstance(path, int):
        return None
    try:
        text = os.fsdecode(path)  # type: ignore[arg-type]
    except TypeError:
        return None
    if not text:
        return None
    if not os.path.isabs(text):
        base = _fd_path(dir_fd)
        if base is None:
            return None
        text = os.path.join(base, text)
    if follow:
        return os.path.realpath(text)
    head, tail = os.path.split(os.path.normpath(text))
    return os.path.join(os.path.realpath(head), tail)


def _null_device(real: str) -> bool:
    """Whether a resolved path is the null device (module doc, Writes)."""
    if not WINDOWS:
        return real == os.devnull
    path = real.replace("/", "\\").lower()
    if path == "\\\\.\\nul":
        return True
    # Past a \\?\ or \\.\ prefix Windows takes the name as it is: a file named nul.
    if path.startswith(("\\\\?\\", "\\\\.\\")):
        return False
    return ntpath.isabs(path) and ntpath.basename(path) == "nul"


def _plain(real: str) -> str:
    """On Windows, a long-form path (`\\\\?\\C:\\...`, `\\\\?\\UNC\\server\\share\\...`) as
    the plain path it names; any other path as it is.

    Past the prefix Windows takes each name literally, so a long form whose plain path
    normalizes to something else (a `..`, a `.`, a `/`, a trailing separator) names no
    plain path and is kept long, which no root matches."""
    if not WINDOWS or not real.startswith("\\\\?\\"):
        return real
    rest = real[4:]
    if rest[:4].upper() == "UNC\\":
        plain = "\\\\" + rest[4:]
    elif rest[:1].isalpha() and rest[1:3] == ":\\":
        plain = rest
    else:
        return real
    return plain if ntpath.normpath(plain) == plain else real


def _under(real: str, roots: Iterable[str]) -> bool:
    """Whether a resolved path is one of `roots` or inside one, each judged as the plain
    path it names (`_plain`)."""
    real = _plain(real)
    sep = ntpath.sep if WINDOWS else os.sep
    return any(real == root or real.startswith(root + sep)
               for root in map(_plain, roots))


def _allowed(real: str) -> bool:
    if _null_device(real) or real == "/dev/tty" or real.startswith("/dev/pts/"):
        return True
    return _under(real, (*_GUARD.roots, *_SHORT_DIRS))


def _write(event: str, path: object, dir_fd: object = None, *,
           follow: bool = True) -> str | None:
    real = _resolve(path, dir_fd, follow=follow)
    if real is None or _allowed(real):
        return None
    return f"{event} {real}: outside the test's tmp"


def _on_open(args: tuple) -> str | None:
    path, mode, flags = (tuple(args) + (None, None, None))[:3]
    writes = ((isinstance(mode, str) and any(c in mode for c in "wax+"))
              or (isinstance(flags, int) and bool(flags & _WRITE_FLAGS)))
    return _write("open", path) if writes else None


def _on_sqlite(args: tuple) -> str | None:
    database = args[0] if args else None
    if isinstance(database, bytes):
        database = os.fsdecode(database)
    if isinstance(database, PurePath):
        database = str(database)
    if not isinstance(database, str) or database in ("", ":memory:"):
        return None
    if database.startswith("file:"):
        parts = urlsplit(database)
        if parse_qs(parts.query).get("mode", [""])[-1] in ("ro", "memory"):
            return None
        database = parts.path
        if not database:
            return None
    return _write("sqlite3.connect", database)


def _loopback(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    host = host.split("%", 1)[0].lower()
    if host in ("", "localhost") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_unspecified:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _unix_path(address: object) -> object:
    """A filesystem AF_UNIX address, or None (abstract or unnamed)."""
    if isinstance(address, (str, bytes)) and address and address[:1] not in ("\0", b"\0"):
        return address
    return None


def _on_reach(event: str) -> Callable[[tuple], str | None]:
    def check(args: tuple) -> str | None:
        sock, address = (tuple(args) + (None, None))[:2]
        family = getattr(sock, "family", None)
        if family == getattr(socket, "AF_UNIX", object()):
            path = _unix_path(address)
            return None if path is None or not _GUARD.network else _write(event, path)
        if family in (socket.AF_INET, socket.AF_INET6) and _GUARD.network:
            if isinstance(address, tuple) and address and not _loopback(address[0]):
                return f"{event} {address[0]}:{address[1] if len(address) > 1 else ''}: " \
                       "not a loopback address"
        return None
    return check


def _on_bind(args: tuple) -> str | None:
    sock, address = (tuple(args) + (None, None))[:2]
    if getattr(sock, "family", None) != getattr(socket, "AF_UNIX", object()):
        return None
    path = _unix_path(address)
    return None if path is None else _write("socket.bind", path, follow=False)


def _arg(index: int, event: str, *, dir_fd: int | None = None,
         follow: bool = True) -> Callable[[tuple], str | None]:
    def check(args: tuple) -> str | None:
        if len(args) <= index:
            return None
        fd = args[dir_fd] if dir_fd is not None and len(args) > dir_fd else None
        return _write(event, args[index], fd, follow=follow)
    return check


def _both(event: str) -> Callable[[tuple], str | None]:
    first = _arg(0, event, dir_fd=2, follow=False)
    second = _arg(1, event, dir_fd=3, follow=False)
    return lambda args: first(args) or second(args)


_HANDLERS: dict[str, Callable[[tuple], str | None]] = {
    "open": _on_open,
    "os.mkdir": _arg(0, "os.mkdir", dir_fd=2, follow=False),
    "os.rename": _both("os.rename"),
    "os.remove": _arg(0, "os.remove", dir_fd=1, follow=False),
    "os.rmdir": _arg(0, "os.rmdir", dir_fd=1, follow=False),
    "os.symlink": _arg(1, "os.symlink", dir_fd=2, follow=False),
    "os.link": _arg(1, "os.link", dir_fd=3, follow=False),
    "os.truncate": _arg(0, "os.truncate"),
    "os.chmod": _arg(0, "os.chmod", dir_fd=2),
    "os.chown": _arg(0, "os.chown", dir_fd=3),
    "os.utime": _arg(0, "os.utime", dir_fd=3),
    "os.setxattr": _arg(0, "os.setxattr"),
    "os.removexattr": _arg(0, "os.removexattr"),
    "shutil.rmtree": _arg(0, "shutil.rmtree", dir_fd=1, follow=False),
    "shutil.copyfile": _arg(1, "shutil.copyfile"),
    "shutil.copytree": _arg(1, "shutil.copytree"),
    "shutil.move": _arg(1, "shutil.move", follow=False),
    "sqlite3.connect": _on_sqlite,
    "tempfile.mkstemp": _arg(0, "tempfile.mkstemp"),
    "tempfile.mkdtemp": _arg(0, "tempfile.mkdtemp"),
    "socket.bind": _on_bind,
    "socket.connect": _on_reach("socket.connect"),
    "socket.sendto": _on_reach("socket.sendto"),
    "socket.sendmsg": _on_reach("socket.sendmsg"),
}


def _audit(event: str, args: tuple) -> None:
    if not _GUARD.active:
        return
    handler = _HANDLERS.get(event)
    if handler is None or getattr(_LOCAL, "busy", False):
        return
    _LOCAL.busy = True
    try:
        refusal = handler(args)
    finally:
        _LOCAL.busy = False
    if refusal:
        _GUARD.breaches.append(refusal)
        raise FenceError(f"the test fence refused {refusal}")


def _install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_audit)
        _INSTALLED = True


# --- the fixture -----------------------------------------------------------------------

_SESSION: dict[str, Any] = {}


def _short_root() -> str:
    """Where short directories go: inside the current test's short directory while a
    test is fenced, else /tmp where it is a directory, else the temp dir."""
    if _GUARD.active and _GUARD.short:
        return _GUARD.short
    return "/tmp" if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK) \
        else tempfile.gettempdir()


def _modules() -> list[str]:
    """The installed Moxi modules, by their `moxi.modules` entry points."""
    from importlib import metadata

    found = {ep.name for ep in metadata.entry_points(group=_GROUP)}
    return sorted(name for name in found if _MODULE_NAME.fullmatch(name))


def _fenced_env(source: Mapping[str, str], home: Path, runtime: Path, short: Path,
                modules: Iterable[str]) -> dict[str, str]:
    env = {key: value for key, value in source.items()
           if key in KEPT_ENV or key.startswith(_KEPT_PREFIXES)
           or (WINDOWS and key.upper() in WINDOWS_KEPT_ENV)}
    for key, rel in HARNESS_HOMES.items():
        env[key] = str(home if rel == "." else home / rel)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    for module in modules:
        env[f"MOXI_{module.upper()}_HOME"] = str(short / module)
    if WINDOWS:
        for key, rel in WINDOWS_HOMES.items():
            env[key] = str(home if rel == "." else home / rel)
        env["HOMEDRIVE"], env["HOMEPATH"] = ntpath.splitdrive(str(home))
        env["TEMP"] = env["TMP"] = str(short)
    return env


def _uv_dirs(environ: Mapping[str, str], home: str, *,
             windows: bool | None = None) -> dict[str, str]:
    """uv's cache and installed-Python directories, as uv finds them from `environ`;
    `windows` is `WINDOWS` unless given."""
    def given(key: str) -> str | None:
        return environ.get(key) or None

    if WINDOWS if windows is None else windows:
        local = given("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        roaming = given("APPDATA") or os.path.join(home, "AppData", "Roaming")
        cache = os.path.join(local, "uv", "cache")
        # uv keeps its Pythons in <Roaming>\uv\python, unless its older folder
        # <Roaming>\uv\data is there, which it goes on using.
        legacy = os.path.join(roaming, "uv", "data")
        pythons = os.path.join(legacy if os.path.exists(legacy)
                               else os.path.join(roaming, "uv"), "python")
    else:
        cache = os.path.join(given("XDG_CACHE_HOME") or os.path.join(home, ".cache"), "uv")
        pythons = os.path.join(given("XDG_DATA_HOME")
                               or os.path.join(home, ".local", "share"), "uv", "python")
    return {"UV_CACHE_DIR": given("UV_CACHE_DIR") or cache,
            "UV_PYTHON_INSTALL_DIR": given("UV_PYTHON_INSTALL_DIR") or pythons}


def _quiet_fsync(fd: Any) -> None:
    """The fsync seam's stand-in: refuses a closed descriptor, syncs nothing."""
    os.fstat(fd if isinstance(fd, int) else fd.fileno())


def _nothing_answers(address: str, port: int, timeout: float = 0.5) -> bool:
    """The port seam's stand-in: no connect, and nothing listens anywhere."""
    return False


def _quiet_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the port seam's stand-in in place of the unit plan's connect, where the core
    installed has one."""
    try:
        from moxi.units import _plan
    except ImportError:
        return
    if hasattr(_plan, "answers"):
        monkeypatch.setattr(_plan, "answers", _nothing_answers)


def pytest_configure(config: pytest.Config) -> None:
    for name, line in MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {line}")
    config.addinivalue_line("markers", f"{LIFTED}: set by the kit on a tier test this "
                                       "run puts back; not for tests to use")
    # Per run, from the environment this pytest started with; a pytest run inside a
    # fenced test sees the fenced one, so the outer run's selection never reaches it.
    config.stash[_SELECTION] = selection(os.environ)
    config.pluginmanager.register(_TimedSplit(), "moxi-dev-timed")
    if not hasattr(config, "workerinput"):          # an xdist worker reports to the main
        config.pluginmanager.register(_Durations(config), "moxi-dev-durations")
    # Read once, from the environment this process started with: a pytest run inside a
    # test (pytester's in-process run) already sees the fenced one.
    _SESSION.setdefault("live", os.environ.get(LIVE_ENV) == "1")
    _SESSION.setdefault("modules", _modules())
    _SESSION.setdefault("uv", _uv_dirs(os.environ, os.path.expanduser("~")))
    _install()


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("live") and not _SESSION.get("live"):
        pytest.skip(f"a live test runs only with {LIVE_ENV}=1")


# --- selection -------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Selection:
    """What a run puts back past the package's `-m`, and what it keeps (module doc)."""

    all_tiers: bool = False
    changed: tuple[str, ...] = ()
    findings: frozenset[str] = frozenset()
    timed: str = ""                     # "", "hold" or "only"
    left_out: frozenset[str] = frozenset()   # tiers never put back

    def lifts(self, item: pytest.Item) -> bool:
        """Whether this run puts `item` back: a `browser`, `slow` or `build` test, never
        a `live` one or one of a tier left out, that every tier, a changed path or a
        finding asks for."""
        if any(item.get_closest_marker(tier) for tier in ("live", *self.left_out)):
            return False
        if not any(item.get_closest_marker(tier) for tier in LIFTABLE):
            return False
        if self.all_tiers or (self.findings and findings_of(item) & self.findings):
            return True
        return any(touches_match(glob, path)
                   for mark in item.iter_markers("touches") for glob in mark.args
                   if isinstance(glob, str) for path in self.changed)


_SELECTION = pytest.StashKey[Selection]()


def selection(environ: Mapping[str, str]) -> Selection:
    """The selection the environment asks for; `pytest.UsageError` for a bad value."""
    tiers = environ.get(TIERS_ENV, "").strip()
    if tiers not in ("", "all"):
        raise pytest.UsageError(f"{TIERS_ENV} is 'all' or unset; it is {tiers!r}")
    changed: tuple[str, ...] = ()
    where = environ.get(CHANGED_ENV, "")
    if where:
        try:
            text = Path(where).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise pytest.UsageError(f"{CHANGED_ENV}: can't read {where} ({exc})") from exc
        changed = tuple(path for path in text.split("\0") if path)
    names = [name.strip() for name in environ.get(FINDING_ENV, "").split(",")]
    names = [name for name in names if name]
    bad = [name for name in names if not FINDING_NAME.fullmatch(name)]
    if bad:
        raise pytest.UsageError(f"{FINDING_ENV} names findings as L<n>, comma-separated; "
                                f"{', '.join(map(repr, bad))} is not one")
    timed = environ.get(TIMED_ENV, "").strip()
    if timed not in ("", *TIMED_MODES):
        raise pytest.UsageError(f"{TIMED_ENV} is {' or '.join(map(repr, TIMED_MODES))} or "
                                f"unset; it is {timed!r}")
    left_out = [tier.strip() for tier in environ.get(LEAVE_OUT_ENV, "").split(",")]
    left_out = [tier for tier in left_out if tier]
    bad = [tier for tier in left_out if tier not in LIFTABLE]
    if bad:
        raise pytest.UsageError(f"{LEAVE_OUT_ENV} names tiers of {', '.join(LIFTABLE)}, "
                                f"comma-separated; {', '.join(map(repr, bad))} is not one")
    return Selection(all_tiers=tiers == "all", changed=changed, findings=frozenset(names),
                     timed=timed, left_out=frozenset(left_out))


def findings_of(item: pytest.Item) -> set[str]:
    """The findings `item`'s `finding` marks name."""
    return {arg for mark in item.iter_markers("finding") for arg in mark.args
            if isinstance(arg, str)}


@functools.lru_cache(maxsize=256)
def _glob_regex(glob: str) -> re.Pattern[str]:
    out, i, n = [], 0, len(glob)
    while i < n:
        if glob.startswith("**/", i):
            out.append("(?:[^/]*/)*")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        elif glob[i] == "[" and (end := glob.find("]", i + 2)) != -1:
            body = glob[i + 1:end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out))


def touches_match(glob: str, path: str) -> bool:
    """Whether a repository path matches a `touches` glob, both relative to the
    repository's root with `/` between parts. `*` and `?` stay within one part, `[...]`
    is a set of characters, and `**` is any number of parts (`a/**` is everything
    under `a`). A glob that matches a directory the path is under matches the path."""
    glob = glob.strip().strip("/")
    if not glob:
        return False
    regex = _glob_regex(glob)
    parts = path.strip("/").split("/")
    return any(regex.fullmatch("/".join(parts[:end])) for end in range(len(parts), 0, -1))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep only a finding's tests when one is asked for, and put back the tier tests the
    run asks for, before pytest's own `-m` deselects (module doc, Selection)."""
    chosen = config.stash.get(_SELECTION, None)
    if chosen is None:
        return
    bad = [f"{item.nodeid}: {glob!r}" for item in items
           for mark in item.iter_markers("touches") for glob in mark.args
           if not (isinstance(glob, str)
                   and glob.strip().strip("/").startswith(TOUCHES_ROOT))]
    if bad:
        # A glob relative to the package would never match a changed path, and its test
        # would quietly run only under every tier.
        raise pytest.UsageError(
            f"a touches glob is a path from the repository's root, under {TOUCHES_ROOT} "
            f"(such as '{TOUCHES_ROOT}<package>/src/**'); these are not: " + "; ".join(bad))
    if chosen.findings:
        keep = [item for item in items if findings_of(item) & chosen.findings]
        dropped = [item for item in items if not findings_of(item) & chosen.findings]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = keep
    lifted = False
    for item in items:
        if chosen.lifts(item):
            item.add_marker(getattr(pytest.mark, LIFTED))
            lifted = True
    if lifted and config.option.markexpr:
        config.option.markexpr = f"({config.option.markexpr}) or {LIFTED}"


_HELD = pytest.StashKey[list[str]]()


class _TimedSplit:
    """Holds the `timed` tests back, or keeps only them (module doc, Selection)."""

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config,
                                      items: list[pytest.Item]) -> None:
        # trylast: after the kit's own selection and after pytest's -m and -k, so what is
        # held is what this run would otherwise have run.
        chosen = config.stash.get(_SELECTION, None)
        if chosen is None or not chosen.timed:
            return
        timed = [item for item in items if item.get_closest_marker(TIMED)]
        rest = [item for item in items if not item.get_closest_marker(TIMED)]
        keep, dropped = (rest, timed) if chosen.timed == "hold" else (timed, rest)
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = keep
        if chosen.timed == "hold":
            held = [item.nodeid for item in timed]
            config.stash[_HELD] = held
            if hasattr(config, "workeroutput"):
                config.workeroutput[_HELD_KEY] = held


# --- durations -------------------------------------------------------------------------

class _Durations:
    """Collects each test's seconds by phase and writes `durations.json` at the end."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.tests: dict[str, dict[str, Any]] = {}
        self.held: set[str] = set()

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: Any) -> None:
        """An xdist worker is done: keep the tests it held back."""
        output = getattr(node, "workeroutput", None) or {}
        self.held.update(output.get(_HELD_KEY) or ())

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        entry = self.tests.setdefault(report.nodeid, {})
        entry[report.when] = round(entry.get(report.when, 0.0) + report.duration, 6)
        if LIFTED in report.keywords:
            entry["lifted"] = [tier for tier in LIFTABLE if tier in report.keywords]
        if report.failed:
            entry["outcome"] = "failed"
        elif report.skipped and entry.get("outcome") != "failed":
            entry["outcome"] = "skipped"
        else:
            entry.setdefault("outcome", "passed")

    def path(self) -> Path | None:
        given = os.environ.get(DURATIONS_ENV)
        if given:
            return Path(given)
        cache = getattr(self.config, "cache", None)
        return cache.mkdir("moxi-dev") / "durations.json" if cache is not None else None

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        path = self.path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        body: dict[str, Any] = {"format": 1, "tests": self.tests}
        chosen = self.config.stash.get(_SELECTION, None)
        if chosen is not None and chosen.timed == "hold":
            body["held"] = sorted(self.held | set(self.config.stash.get(_HELD, [])))
        part.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
        os.replace(part, path)


# --- a seam with two implementations -------------------------------------------------------

class Seam:
    """A seam with two or more implementations, behind one fixture.

    The contract suites (`contract`, test file names such as `test_delivery.py`) run
    each of their tests once on every implementation; every other test that asks for
    the fixture gets `default`. In the package's `conftest.py`:

        BACKEND = Seam("backend", ["mock", "local"], default="mock",
                       contract=["test_delivery.py", "test_claims.py"])
        backend = BACKEND.fixture

        def pytest_generate_tests(metafunc):
            BACKEND.parametrize(metafunc)

    The fixture's value is the implementation (a name, usually), which the package's own
    fixtures build from. Test ids are `param_id` of each implementation, the same on
    every run. A test that parametrizes the fixture itself is left as it is."""

    def __init__(self, name: str, implementations: Iterable[Any], *, default: Any,
                 contract: Iterable[str]) -> None:
        values = tuple(implementations)
        if len(values) < 2:
            raise ValueError(f"seam {name!r}: a seam has two implementations or more")
        if len(set(map(param_id, values))) != len(values):
            raise ValueError(f"seam {name!r}: two implementations have the same id")
        if default not in values:
            raise ValueError(f"seam {name!r}: the default {default!r} is not one of "
                             f"its implementations")
        self.name = name
        self.implementations = values
        self.default = default
        self.contract = frozenset(contract)

        @pytest.fixture(name=name)
        def _fixture(request: pytest.FixtureRequest) -> Any:
            return getattr(request, "param", default)

        self.fixture = _fixture

    def is_contract(self, metafunc: pytest.Metafunc) -> bool:
        """Whether `metafunc`'s test is in one of the contract suites."""
        module = getattr(metafunc, "module", None)
        where = getattr(module, "__file__", None)
        return where is not None and Path(where).name in self.contract

    def parametrize(self, metafunc: pytest.Metafunc) -> None:
        """Call from `pytest_generate_tests`: a contract test that asks for the fixture
        runs on each implementation."""
        if self.name not in metafunc.fixturenames or not self.is_contract(metafunc):
            return
        for mark in metafunc.definition.iter_markers("parametrize"):
            names = mark.args[0] if mark.args else mark.kwargs.get("argnames", ())
            if isinstance(names, str):
                names = [part.strip() for part in names.split(",")]
            if self.name in names:
                return
        metafunc.parametrize(self.name, self.implementations, indirect=True, ids=param_id)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    start = len(_GUARD.breaches)
    try:
        result = yield
    except BaseException:
        _GUARD.reported = len(_GUARD.breaches)
        raise
    new = _GUARD.breaches[start:]
    if new:
        _GUARD.reported = len(_GUARD.breaches)
        pytest.fail("the test fence refused, and the test went on:\n  "
                    + "\n  ".join(new), pytrace=False)
    return result


@pytest.fixture(autouse=True)
def fence(request: pytest.FixtureRequest, tmp_path: Path,
          tmp_path_factory: pytest.TempPathFactory,
          monkeypatch: pytest.MonkeyPatch) -> Iterator[Fence]:
    """Fence the test in: its environment, its writes and its connects (module doc)."""
    _install()
    # A fence set up inside a fenced test (pytester's in-process run) puts the outer
    # one back when it ends; the outermost leaves the hook idle.
    outer = _GUARD.save()
    basetemp = tmp_path_factory.getbasetemp()
    short = Path(tempfile.mkdtemp(prefix="mx-", dir=_short_root()))
    home = tmp_path / "home"
    runtime = short / "run"
    home.mkdir()
    runtime.mkdir(mode=0o700)
    env = _fenced_env(os.environ, home, runtime, short,
                      _SESSION.get("modules") or _modules())
    env["TMPDIR"] = str(short)
    for key in [k for k in os.environ if k not in env]:
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(tempfile, "tempdir", str(short))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    if not request.node.get_closest_marker("honest_fsync"):
        monkeypatch.setattr(os, "fsync", _quiet_fsync)
        if _REAL_FDATASYNC is not None:
            monkeypatch.setattr(os, "fdatasync", _quiet_fsync)
    if not (request.node.get_closest_marker("honest_ports")
            or request.node.get_closest_marker("live")):
        _quiet_ports(monkeypatch)
    roots: list[str] = []
    for root in (basetemp, short):
        real = os.path.realpath(root)
        if real not in roots:
            roots.append(real)
    _GUARD.roots = tuple(roots)
    _GUARD.short = os.path.realpath(short)
    _GUARD.network = not request.node.get_closest_marker("live")
    _GUARD.reported = len(_GUARD.breaches)
    _GUARD.active = True
    try:
        yield Fence(home=home, runtime=runtime, short=short, env=dict(env),
                    roots=tuple(Path(r) for r in roots))
    finally:
        late = _GUARD.breaches[_GUARD.reported:]
        _GUARD.restore(outer)
        shutil.rmtree(short, ignore_errors=True)
    if late:
        pytest.fail("the test fence refused, outside the test's call:\n  "
                    + "\n  ".join(late), pytrace=False)


@pytest.fixture
def uv_env(fence: Fence) -> dict[str, str]:
    """An environment for a child process that runs uv (module doc): the fenced one, with
    uv's real cache and installed Pythons and no Python downloads. Pass it as the child's
    `env`; this process's own environment is left as the fence made it."""
    return {**os.environ, **_SESSION["uv"], **_UV_NO_DOWNLOADS}


# --- helpers ---------------------------------------------------------------------------

def wait_for(predicate: Callable[[], Any], timeout: float = 5.0, *, interval: float = 0.01,
             what: str | Callable[[], str] = "the condition") -> Any:
    """Call `predicate` until it returns something true and return that; fail with
    `AssertionError` naming `what` after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:g}s waiting for "
                                 f"{what() if callable(what) else what}")
        time.sleep(interval)


@contextlib.contextmanager
def short_dir(prefix: str = "mx-") -> Iterator[Path]:
    """A new directory with a short path (under /tmp where it can be), removed after.

    A Unix socket's path is limited to about 100 bytes; pytest's tmp_path is often
    longer than that. In a fenced test it is made inside the test's short directory;
    made elsewhere (by a fixture of wider scope), it is one of the fence's allowed roots
    until it is removed, so a test may reach a server whose socket is in it."""
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=_short_root()))
    real = os.path.realpath(path)
    _SHORT_DIRS.add(real)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        _SHORT_DIRS.discard(real)


#: Why a test that needs a symlink skips where `can_symlink()` is false.
NO_SYMLINKS = ("this account may not make symlinks (on Windows: it lacks the symlink "
               "privilege and Developer Mode is off)")

#: Windows' "a required privilege is not held", the error for a symlink made without it.
_ERROR_PRIVILEGE_NOT_HELD = 1314


@functools.cache
def can_symlink() -> bool:
    """Whether this account may make a symlink: tried once, on a real link in a new temp
    directory that is removed after, and the answer kept for the process.

    Only Windows' "a required privilege is not held" reads as no. Any other error is
    raised, `FenceError` among them, so a fence refusal is never taken for no."""
    with tempfile.TemporaryDirectory(prefix="mx-link-") as folder:
        try:
            os.symlink(os.path.join(folder, "target"), os.path.join(folder, "link"))
        except OSError as error:
            if getattr(error, "winerror", None) != _ERROR_PRIVILEGE_NOT_HELD:
                raise
            return False
    return True


def need_symlinks() -> None:
    """Skip the test from here on where this account may not make a symlink."""
    if not can_symlink():
        pytest.skip(NO_SYMLINKS)


@pytest.fixture
def needs_symlinks() -> None:
    """For a test that makes a symlink: skipped where this account may not make one."""
    need_symlinks()


@contextlib.contextmanager
def served(make: Callable[[Path], Any], *, timeout: float = 5.0) -> Iterator[Any]:
    """Serve a `socketserver`-style server on a thread for the `with` block.

    `make(socket_path)` builds the server; the path is `sock` in a fresh `short_dir`,
    and a TCP server simply ignores it. The server is served with
    `serve_forever(poll_interval=SHUTDOWN_POLL)`, so leaving the block shuts it down
    within that poll; then `server_close()` runs (when it has one) and the directory
    goes."""
    with short_dir("mxs-") as where:
        server = make(where / "sock")
        started = threading.Event()

        def run() -> None:
            started.set()
            server.serve_forever(poll_interval=SHUTDOWN_POLL)

        thread = threading.Thread(target=run, daemon=True,
                                  name=f"served-{type(server).__name__}")
        thread.start()
        try:
            wait_for(started.is_set, timeout, what=f"{type(server).__name__} to start")
            yield server
        finally:
            if thread.is_alive():
                server.shutdown()
            thread.join(timeout)
            close = getattr(server, "server_close", None)
            if close is not None:
                close()


def param_id(value: object) -> str:
    """A test id for a parameter that is the same on every run: never a memory address.

    Use it as `ids=param_id` in `pytest.mark.parametrize` or `pytest.fixture(params=...)`.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return str(value)
    if isinstance(value, str):
        return value or "empty"
    if isinstance(value, bytes):
        return value.decode("ascii", "backslashreplace") or "empty"
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, PurePath):
        return value.name or str(value)
    if isinstance(value, (tuple, list)):
        return "-".join(param_id(v) for v in value) or "empty"
    name = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)
    if isinstance(name, str):
        return name
    return type(value).__qualname__


def console_script(directory: str | os.PathLike[str], name: str,
                   source: str | None = None) -> Path:
    """Write a stand-in for console script `name` into `directory` and return its path.

    It has the name this system finds a console script by, so `moxi.paths.script(name)`
    finds it when the scripts directory is `directory`, and so does a PATH lookup
    (`shutil.which`) when `directory` is on PATH; and it runs. On POSIX it is `name`, a
    `/bin/sh` script with mode 0o755. On Windows it is `name.cmd`, a batch file: an
    installer writes `name.exe`, which a test can't write as text, and `.cmd` is on every
    `PATHEXT`. A test that needs the path uses the one returned, never `directory / name`.

    With no `source` it does nothing and exits 0. With `source`, Python code, it runs
    that code under this interpreter, with the stand-in's arguments in `sys.argv[1:]`,
    and exits with its status. On Windows the arguments pass through `cmd.exe`, so one
    holding `&`, `|`, `^`, `%`, `<` or `>` doesn't arrive as written.
    """
    python = sys.executable
    if WINDOWS:
        if source is not None and "%" in python:
            raise ValueError(f"can't quote {python!r} for cmd.exe")
        path = Path(directory, f"{name}.cmd")
        # `-x` makes Python skip the first line, the one cmd.exe runs; `call` reads
        # Python's exit status after it ran, not when cmd.exe read the line.
        head = ("@exit /b 0" if source is None
                else f'@"{python}" -x "%~f0" %* & call exit /b %%errorlevel%%')
        path.write_bytes(f"{head}\r\n{source or ''}".replace("\r\n", "\n")
                         .replace("\n", "\r\n").encode())
        return path
    path = Path(directory, name)
    if source is None:
        path.write_text("#!/bin/sh\n")
    else:
        if "'" in python or "\\" in python:
            raise ValueError(f"can't quote {python!r} for both sh and Python")
        # Line 2 runs Python on this file in sh, and is a bare string to Python.
        path.write_text(f"#!/bin/sh\n\"exec\" '{python}' \"$0\" \"$@\"\n{source}")
    path.chmod(0o755)
    return path


def write_sqlite(path: str | os.PathLike[str], schema: str = "",
                 rows: Mapping[str, Iterable[Sequence[Any]]] | None = None) -> Path:
    """Write a SQLite database for a test, fast: one transaction, no syncing.

    `schema` is SQL run as a script; `rows` maps a table to the rows inserted into it,
    each a sequence of column values in the table's order. All of it is one transaction
    with `PRAGMA synchronous=OFF` and the journal in memory, so either it all lands or
    nothing does. The file is an ordinary database afterwards."""
    path = Path(path)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("BEGIN")
        try:
            if schema.strip():
                for statement in _statements(schema):
                    conn.execute(statement)
            for table, values in (rows or {}).items():
                values = [tuple(v) for v in values]
                if not values:
                    continue
                marks = ", ".join("?" * len(values[0]))
                quoted = '"' + table.replace('"', '""') + '"'
                conn.executemany(f"INSERT INTO {quoted} VALUES ({marks})", values)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return path


def _statements(script: str) -> Iterator[str]:
    """Split a SQL script into complete statements, as SQLite reads them."""
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                yield pending
            pending = ""
    if pending.strip():
        yield pending


__all__ = ["CHANGED_ENV", "DURATIONS_ENV", "FINDING_ENV", "Fence", "FenceError",
           "HARNESS_HOMES", "KEPT_ENV", "LEAVE_OUT_ENV", "LIFTED", "LIVE_ENV", "MARKERS",
           "SHUTDOWN_POLL", "Seam", "Selection", "TIERS", "TIERS_ENV", "TIMED", "TIMED_ENV",
           "WINDOWS_HOMES", "WINDOWS_KEPT_ENV", "console_script", "fence", "findings_of",
           "param_id", "selection", "served", "short_dir", "touches_match", "uv_env",
           "wait_for", "write_sqlite"]
