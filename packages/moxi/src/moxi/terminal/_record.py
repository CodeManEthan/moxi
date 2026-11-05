"""The core's record of the environment the user's terminal ran with.

The record holds every variable of the core set that is set in the recording run's
environment, with its exact value, and nothing else. An empty value is kept as empty; a
name absent from the record was unset. Values compare as strings.

The core set is every name read by the resolvers a page's check may use: `Layout`'s
methods, `paths.core()`, `paths.core_lock`, `paths.home`, `place.folders` and
`paths.which`. `in_set(name)` matches it by name, so recording needs no module discovery:

- `MOXI_HOME`, `MOXI_DEVICE`, and `MOXI_<P>_<S>` for any prefix `<P>` and `<S>` one of
  `paths.LOCATORS` or `DEVICE`, but not `MOXI_ANCHOR_HOME`, which places the record
  itself and is no module's;
- `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`,
  `XDG_RUNTIME_DIR`, `HOME`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`,
  `WIN_PD_OVERRIDE_APPDATA` and `WIN_PD_OVERRIDE_LOCAL_APPDATA`;
- `CLAUDE_CONFIG_DIR` and `CODEX_HOME`;
- `PATH` and `PATHEXT`.

What the core reads for other purposes (the stray check's `UV_TOOL_DIR` and `APPDATA`)
is outside the set. The environment holds tokens and keys; the record never holds any
name outside the set, so it never holds them.

The file is `terminal.json` in the account's anchor (`paths.terminal_record()`), written
whole with `files.atomic_write`, mode 0600:

    {"format": 1, "moxi": "<the writing core's version>", "env": {"HOME": "...", ...}}

On Windows, names are upper-cased when recorded, as Windows compares them without case.

`read()` returns a `Record`, or what stands in its place: `NoRecord` (the file is missing,
won't parse, or isn't this shape at `format` 1), `Unreadable` (any other error reading
it, still there after the writer's own replace window of a second) or `NewerFormat` (a
newer moxi wrote it). `keyboard()` is the positive half of the rule for who records: a
terminal on this process's input.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .. import files, paths

_WINDOWS = sys.platform == "win32"

#: The record's format this core writes and reads.
FORMAT = 1

#: The names of the set that no prefix makes.
FIXED = frozenset({
    "MOXI_HOME", "MOXI_DEVICE",
    "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "WIN_PD_OVERRIDE_APPDATA", "WIN_PD_OVERRIDE_LOCAL_APPDATA",
    "CLAUDE_CONFIG_DIR", "CODEX_HOME",
    "PATH", "PATHEXT",
})

#: A module's (or any prefix's) locator or device variable.
_PREFIXED = re.compile(r"MOXI_[A-Z0-9_]+_(?:%s)" % "|".join((*paths.LOCATORS, "DEVICE")))

# The names a prefix would make that aren't a module's: the test kits' anchor variable.
_NOT_PREFIXED = frozenset({paths.ANCHOR_HOME})


def in_set(name: str, *, fold: bool = _WINDOWS) -> bool:
    """True when `name` is in the core set. With `fold` (the default on Windows, where
    names are the same whatever their case) any spelling of a name in the set counts."""
    if fold:
        name = name.upper()
    if name in FIXED:
        return True
    return name not in _NOT_PREFIXED and _PREFIXED.fullmatch(name) is not None


def of(env: Mapping[str, str] | None = None, *, fold: bool = _WINDOWS) -> dict[str, str]:
    """The record's `env` for `env` (None: this process's environment): each name of the
    core set it holds, with its exact value, sorted by name. With `fold` (the default on
    Windows) names are upper-cased."""
    source = os.environ if env is None else env
    held: dict[str, str] = {}
    for name, value in source.items():
        key = name.upper() if fold else name
        if in_set(key, fold=False):
            held[key] = value
    return dict(sorted(held.items()))


# -- what a read gives ----------------------------------------------------------------------

@dataclass(frozen=True)
class Record:
    """A record read at `path`: its `env`, the `moxi` version that wrote it, its `format`."""

    path: Path
    env: dict[str, str] = field(hash=False)
    moxi: str
    format: int = FORMAT


@dataclass(frozen=True)
class NoRecord:
    """No record at `path`: the file is missing, won't parse, or isn't a record's shape."""

    path: Path


@dataclass(frozen=True)
class Unreadable:
    """The file at `path` is there but can't be read; `error` is the last error."""

    path: Path
    error: OSError


@dataclass(frozen=True)
class NewerFormat:
    """A record at `path` in a newer `format` than this core reads, written by the moxi
    version `moxi` (None when the file names none). `reader` is this core's version and
    `reads` the format it reads."""

    path: Path
    format: int
    moxi: str | None
    reader: str
    reads: int = FORMAT


Read = Record | NoRecord | Unreadable | NewerFormat


def read(path: str | os.PathLike | None = None, *,
         retry: float = files.REPLACE_RETRY) -> Read:
    """Read the record at `path` (None: `paths.terminal_record()`, which raises
    `paths.NoAnchor` when the account's folder can't be placed).

    Only "not found" and "not this shape" read as no record. Any other error reading the
    file (Windows refuses an open while the writer replaces it) is tried again for up to
    `retry` seconds, the writer's own replace window, and gives `Unreadable` when it
    persists.
    """
    path = paths.terminal_record() if path is None else Path(path)
    deadline = time.monotonic() + retry
    while True:
        try:
            data = path.read_bytes()
            break
        except (FileNotFoundError, NotADirectoryError):
            return NoRecord(path)
        except OSError as error:
            if time.monotonic() >= deadline:
                return Unreadable(path, error)
            time.sleep(files.POLL)
    return parse(path, data)


def parse(path: Path, data: bytes) -> Record | NoRecord | NewerFormat:
    """What the bytes `data` of the file at `path` hold."""
    import json

    try:
        body = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return NoRecord(path)
    if not isinstance(body, dict):
        return NoRecord(path)
    form, writer = body.get("format"), body.get("moxi")
    if type(form) is not int:
        return NoRecord(path)
    if form > FORMAT:
        return NewerFormat(path, form, writer if isinstance(writer, str) else None,
                           version())
    env = body.get("env")
    if (form != FORMAT or not isinstance(writer, str) or not isinstance(env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())):
        return NoRecord(path)
    if _WINDOWS:
        env = {name.upper(): value for name, value in env.items()}
    return Record(path, dict(env), writer, form)


def text(env: Mapping[str, str], moxi: str) -> str:
    """The file's text for a record of `env` (already the record's names) by `moxi`."""
    import json

    body = {"format": FORMAT, "moxi": moxi, "env": dict(sorted(env.items()))}
    return json.dumps(body, indent=2) + "\n"


def write(env: Mapping[str, str] | None = None, *, path: str | os.PathLike | None = None,
          moxi: str | None = None) -> Path:
    """Write the record of `env` (None: this process's environment; only its core-set
    names are kept) to `path` (None: `paths.terminal_record()`), by core version `moxi`
    (None: this core's). It replaces whatever the file held, a record of an older or a
    newer format included. Returns the path.

    Raises `paths.NoAnchor` when the account's folder can't be placed, and `OSError`
    when the file can't be written. Only the front door's record step writes the real
    account's record; this is also the test kits' writer, under a test's anchor.
    """
    path = paths.terminal_record() if path is None else Path(path)
    files.atomic_write(path, text(of(env), moxi or version()), mode=0o600)
    return path


# -- the core's own version -----------------------------------------------------------------

_version: str | None = None


def version() -> str:
    """This core's version, as its installed distribution names it ("unknown" when no
    distribution is found).

    Read from the name of the first `moxi-<version>.dist-info` directory on `sys.path`,
    which is where `importlib.metadata` finds it too, without importing that (it costs
    more than the whole of an unchanged record step)."""
    global _version
    if _version is None:
        _version = _find_version()
    return _version


def _find_version() -> str:
    for entry in sys.path:
        if not entry:
            continue  # the working directory places no installed distribution
        try:
            names = os.listdir(entry)
        except OSError:
            continue
        for name in names:
            if name.startswith("moxi-") and name.endswith(".dist-info"):
                return name[len("moxi-"):-len(".dist-info")]
    from importlib import metadata

    try:
        return metadata.version("moxi")
    except metadata.PackageNotFoundError:
        return "unknown"


# -- the keyboard ---------------------------------------------------------------------------

# The pipe a Cygwin or MSYS pty (Git Bash's mintty) gives a native program as its input.
_PTY_PIPE = re.compile(r"(?:\\\\\.\\pipe)?\\(?:cygwin|msys)-[0-9a-f]+-pty[0-9]+-from-master",
                       re.IGNORECASE)


def keyboard(stdin=None) -> bool:
    """True when a terminal is on `stdin` (None: `sys.stdin`): it is a terminal
    (`isatty()`), or on Windows a pipe named as a Cygwin or MSYS pty's master side, which
    is what Git Bash's mintty gives a native program. Only the input is asked; a closed or
    missing input is no keyboard."""
    stdin = sys.stdin if stdin is None else stdin
    try:
        fd = stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return False
    try:
        if os.isatty(fd):
            return True
    except OSError:
        return False
    return _WINDOWS and pty_pipe(fd)


def pty_pipe(fd: int) -> bool:
    """True when descriptor `fd` is a Cygwin or MSYS pty's master-side pipe (Windows)."""
    try:
        import msvcrt

        from .. import _win

        name = _win.pipe_name(msvcrt.get_osfhandle(fd))
    except (ImportError, OSError):
        return False
    return name is not None and is_pty_pipe(name)


def is_pty_pipe(name: str) -> bool:
    """True when a pipe's name is a Cygwin or MSYS pty's master side as input:
    `\\\\.\\pipe\\cygwin-<hex>-pty<N>-from-master` or the `msys-` form, with or without
    the `\\\\.\\pipe` start (the system gives the name without it)."""
    return _PTY_PIPE.fullmatch(name) is not None


__all__ = ["FORMAT", "FIXED", "in_set", "of", "Record", "NoRecord", "Unreadable",
           "NewerFormat", "Read", "read", "parse", "text", "write", "version", "keyboard",
           "pty_pipe", "is_pty_pipe"]
