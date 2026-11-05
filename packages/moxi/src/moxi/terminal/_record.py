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

class _Value:
    """A small value: its fields, compared and shown by name. Not a dataclass, since
    importing `dataclasses` costs more than the whole of an unchanged record step."""

    __slots__ = ()

    def __init__(self, *args, **kwargs):
        fields = self.__slots__
        given = dict(zip(fields, args))
        given.update(kwargs)
        for name in fields:
            if name in given:
                object.__setattr__(self, name, given[name])
            elif name in self._defaults:
                object.__setattr__(self, name, self._defaults[name])
            else:
                raise TypeError(f"{type(self).__name__} needs {name}")

    _defaults: dict = {}

    def __setattr__(self, name, value):
        raise AttributeError(f"{type(self).__name__} can't be changed")

    def _fields(self) -> tuple:
        return tuple(getattr(self, name) for name in self.__slots__)

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self._fields() == other._fields()

    def __hash__(self):
        return hash((type(self).__name__, *(repr(v) for v in self._fields())))

    def __repr__(self) -> str:
        shown = ", ".join(f"{name}={getattr(self, name)!r}" for name in self.__slots__)
        return f"{type(self).__name__}({shown})"


class Record(_Value):
    """A record read at `path`: its `env`, the `moxi` version that wrote it, its `format`."""

    __slots__ = ("path", "env", "moxi", "format")
    _defaults = {"format": FORMAT}


class NoRecord(_Value):
    """No record at `path`: the file is missing, won't parse, or isn't a record's shape."""

    __slots__ = ("path",)


class Unreadable(_Value):
    """The file at `path` is there but can't be read; `error` is the last error."""

    __slots__ = ("path", "error")


class NewerFormat(_Value):
    """A record at `path` in a newer `format` than this core reads, written by the moxi
    version `moxi` (None when the file names none). `reader` is this core's version and
    `reads` the format it reads."""

    __slots__ = ("path", "format", "moxi", "reader", "reads")
    _defaults = {"reads": FORMAT}


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
    """The file's text for a record of `env` (already the record's names) by `moxi`: JSON,
    names sorted, one per line, every character outside printable ASCII escaped.

    Built here rather than by `json`, whose import costs several times an unchanged
    record step on Windows; the step compares the file's bytes with this text and so
    needs no parse. The same environment always gives the same text."""
    lines = [f'{{\n  "format": {FORMAT},\n  "moxi": {_quote(moxi)},\n  "env": {{']
    names = sorted(env)
    for i, name in enumerate(names):
        comma = "," if i < len(names) - 1 else ""
        lines.append(f"\n    {_quote(name)}: {_quote(env[name])}{comma}")
    lines.append("\n  }\n}\n" if names else "}\n}\n")
    return "".join(lines)


_SHORT = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b",
          "\f": "\\f"}


def _quote(value: str) -> str:
    """`value` as a JSON string in ASCII, as `json.dumps` writes one."""
    if value.isascii() and value.isprintable() and '"' not in value and "\\" not in value:
        return f'"{value}"'
    out = []
    for ch in value:
        code = ord(ch)
        if ch in _SHORT:
            out.append(_SHORT[ch])
        elif 0x20 <= code <= 0x7E:
            out.append(ch)
        elif code > 0xFFFF:
            code -= 0x10000
            out.append(f"\\u{0xD800 | (code >> 10):04x}\\u{0xDC00 | (code & 0x3FF):04x}")
        else:
            out.append(f"\\u{code:04x}")
    return '"' + "".join(out) + '"'


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
    # The folder holding the `moxi` package first (an ordinary install puts the dist-info
    # beside it), then each site-packages folder (an editable one), then the rest, so the
    # standard library's large folders are rarely listed.
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    entries = [e for e in sys.path if e]  # the working directory places no distribution
    order = sorted(entries, key=lambda e: (os.path.normcase(e) != os.path.normcase(here),
                                           os.path.basename(e) != "site-packages"))
    for entry in order:
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


# -- the front door's step -----------------------------------------------------------------

#: What a record step did (`Outcome.kind`).
SAME = "same"                # the file already held this environment: nothing walked
WRITTEN = "written"          # the rule held and the record was written
NO_KEYBOARD = "no keyboard"  # no terminal on the input
FOUND = "found"              # the walk found Moxi or an agent above the run
UNDECIDED = "undecided"      # the walk couldn't tell (N1, N2)
NO_PLACE = "no place"        # the rule held, but the account's folder can't be placed
FAILED = "failed"            # the rule held, but the file couldn't be written


class Outcome(_Value):
    """What a record step did: `kind` (one of the constants above), the record's `path`
    (None when it has no place), what the walk `found` (an `Evidence` or `Undecided`, for
    `FOUND` and `UNDECIDED`), and the `error` (`paths.NoAnchor` for `NO_PLACE`, the
    `OSError` for `FAILED`)."""

    __slots__ = ("kind", "path", "found", "error")
    _defaults = {"path": None, "found": None, "error": None}

    def line(self) -> str | None:
        """The one stderr line the step prints, or None: a note when the walk couldn't
        decide, a warning when the record couldn't be placed or written. A step stopped
        by evidence or by no keyboard prints nothing, so a pane Moxi opened, an agent's
        shell, a timer or a hook gets no extra output."""
        if self.kind == UNDECIDED:
            return f"moxi: note: this terminal's environment wasn't recorded: {row(self)}"
        if self.kind in (NO_PLACE, FAILED):
            return f"moxi: warning: this terminal's environment wasn't recorded: {row(self)}"
        return None


def step(env: Mapping[str, str] | None = None, *, stdin=None, view=None,
         run=None) -> Outcome:
    """Record the terminal's environment if it changed and the rule for who records holds.

    It builds the record of `env` (None: this process's environment) and reads the file
    once. When the file already holds it, from this core's version, it writes nothing and
    walks nothing. Otherwise it applies the rule: a keyboard on `stdin` (None:
    `sys.stdin`), then a walk up the process tree (`view` and `run` as `walk` takes them)
    that finds nothing Moxi or an agent started. It writes only when both hold. It holds
    no lock: two runs at once leave the later one's record.

    Nothing here raises for a record that can't be placed or written; the outcome says so.
    """
    source = os.environ if env is None else env
    held = of(source)
    path = None
    no_place = None
    try:
        path = paths.terminal_record(source)
    except paths.NoAnchor as error:
        no_place = error
    else:
        # One read, no retry, no parse: the file is this run's record byte for byte, or the
        # run applies the rule. A file that can't be read now is replaced if it holds.
        try:
            if path.read_bytes() == text(held, version()).encode("ascii"):
                return Outcome(SAME, path)
        except OSError:
            pass
    if not keyboard(stdin):
        return Outcome(NO_KEYBOARD, path)
    from ._walk import Evidence, walk

    found = walk(view, run)
    if isinstance(found, Evidence):
        return Outcome(FOUND, path, found)
    if found is not None:
        return Outcome(UNDECIDED, path, found)
    if path is None:
        return Outcome(NO_PLACE, None, error=no_place)
    try:
        files.atomic_write(path, text(held, version()), mode=0o600)
    except OSError as error:
        return Outcome(FAILED, path, error=error)
    return Outcome(WRITTEN, path)


# -- the doctor row's words for what stopped a record -----------------------------------------

def row(outcome: Outcome) -> str | None:
    """The doctor's terminal record row, in words, for an outcome the walk couldn't
    decide or a record that couldn't be placed or written; None for any other. Each names
    its cause and the one step that fixes it."""
    found, error = outcome.found, outcome.error
    if outcome.kind == UNDECIDED:
        return _undecided(found)
    if outcome.kind == NO_PLACE:
        return f"Moxi can't find where it keeps the record: {error.cause}; {error.step}"
    if outcome.kind == FAILED:
        folder = outcome.path.parent
        return (f"the record at {outcome.path} can't be written ({_reason(error)}); "
                f"make {folder} writable by you")
    return None


def _undecided(found) -> str:
    from . import _walk

    if found.kind == _walk.N2:
        return (f"this terminal is inside a tmux server (pid {found.pid}) that moxi can't "
                "tell from slots' own, because no service manager runs slots' server here; "
                "type the command in a terminal outside tmux")
    if found.reason == _walk.ENDED:
        return "a process above this one ended while moxi read it; run the command again"
    name = found.program or "a process"
    why = {_walk.DEEP: f"more than {_walk.LIMIT} processes deep",
           _walk.LOOP: "its parents loop"}.get(found.reason) or found.error or "unread"
    other = found.program or "that process"
    return (f"moxi can't read {name} (pid {found.pid}) above this terminal ({why}), so it "
            f"can't tell whether Moxi started this terminal; type the command in a "
            f"terminal that {other} didn't start")


def _reason(error: BaseException) -> str:
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error) or type(error).__name__


__all__ = ["FORMAT", "FIXED", "in_set", "of", "Record", "NoRecord", "Unreadable",
           "NewerFormat", "Read", "read", "parse", "text", "write", "version", "keyboard",
           "pty_pipe", "is_pty_pipe", "Outcome", "step", "row", "SAME", "WRITTEN",
           "NO_KEYBOARD", "FOUND", "UNDECIDED", "NO_PLACE", "FAILED"]
