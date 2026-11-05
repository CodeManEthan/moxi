"""The check a page makes before it writes: would it write where the user's terminal does?

    check(places, *, programs=None, page=True) -> str | None

`places(env)` returns `{label: path}`: every folder or file the act reads or writes while
deciding or doing its write, each resolved from the mapping `env` alone by the resolver
the code doing that read or write uses. A label is the words a person reads ("the
projects store"). `places` reads the mapping only by name (`get`, `[]`, `in`), never
iterates it, and reads only names the core records; a read of any other name raises
`ValueError`, so a contract bug refuses the write rather than letting it through.

`check` reads the core's record of the environment the user's last run in a terminal had,
calls `places` twice, once with the record's variables and once with this process's
environment, and returns None when every label resolves to the same place under both.
Otherwise it returns the one line to refuse with:

- a label whose two paths differ, compared after `abspath` and `normcase` (two spellings
  of one folder, such as a symbolic link, differ: the check errs toward refusing);
- a path that isn't absolute under either mapping, before anything makes it so: that
  side resolved it against a folder the other can't know, so it is a difference, never a
  match, and the line names the variable that gave it;
- a program of `programs` (`{name: why}`, programs whose absence would let the act do what
  the terminal's run refuses) that the record's `PATH` finds and this process's doesn't.
  A program found here and not by the terminal is let through: the act then runs a check
  the terminal skipped, so it refuses more, never less.

The line's fix names every variable that `places` read under either mapping whose value
differs between them, with the terminal's value, so setting all of them in one step makes
the next check pass. `page` words the line for the browser page (the default) or for a
terminal. With no record, a record that can't be read, one in a newer format, or an
account whose folder can't be placed, the line says so and names the step that makes a
record. On Windows names compare without case, as Windows compares them.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import PurePath

from . import _lines, _record

Places = Callable[[Mapping[str, str]], Mapping[str, "str | os.PathLike[str]"]]


class Noting(Mapping):
    """An environment that notes each name read, in the order first read, and refuses to
    be iterated or measured: a resolver reads names, it doesn't list the environment.

    On Windows a name is looked up without case, as Windows looks it up.
    """

    def __init__(self, data: Mapping[str, str], *, windows: bool | None = None):
        self.windows = _windows() if windows is None else windows
        self.data = {self._key(k): v for k, v in dict(data).items()}
        self.read: dict[str, None] = {}

    def _key(self, name: object) -> object:
        return name.upper() if self.windows and isinstance(name, str) else name

    def _note(self, name: object) -> object:
        key = self._key(name)
        if isinstance(key, str):
            self.read.setdefault(key)
        return key

    def __getitem__(self, name: str) -> str:
        return self.data[self._note(name)]

    def get(self, name, default=None):
        return self.data.get(self._note(name), default)

    def __contains__(self, name) -> bool:
        return self._note(name) in self.data

    def __iter__(self) -> Iterator[str]:
        raise TypeError("a place resolver iterated the environment; it may read names only")

    def __len__(self) -> int:
        raise TypeError("a place resolver measured the environment; it may read names only")

    def value(self, name: str) -> str | None:
        """The value of `name` without noting the read."""
        return self.data.get(self._key(name))


def check(places: Places, *, programs: Mapping[str, str] | None = None,
          page: bool = True) -> str | None:
    """None when this process's places agree with the last terminal run's, else the one
    line to refuse with (module doc). Raises `ValueError` when `places` reads a name
    outside the core's set."""
    from .. import paths

    windows = _windows()
    here_env = dict(os.environ)
    anchor = _anchor_set(here_env, windows)
    try:
        record = paths.terminal_record()
    except paths.NoAnchor as no:
        return _with_anchor(_lines.no_place(no.cause, no.step, page=page), anchor, page)
    outcome = _record.read(record)
    if isinstance(outcome, _record.NoRecord):
        return _with_anchor(_lines.no_record(str(record), page=page), anchor, page)
    if isinstance(outcome, _record.Unreadable):
        return _with_anchor(_lines.unreadable(str(record), _error(outcome.error),
                                              page=page), anchor, page)
    if isinstance(outcome, _record.NewerFormat):
        return _with_anchor(_lines.newer(str(record), outcome.format, outcome.moxi,
                                         outcome.reader, page=page), anchor, page)
    terminal = Noting(outcome.env, windows=windows)
    here = Noting(here_env, windows=windows)
    then = _resolve(places, terminal)
    now = _resolve(places, here)
    for env in (terminal, here):
        _refuse_outside(env, windows)
    problems: list[str] = []
    givers: set[str] = set()
    for label in dict.fromkeys([*then, *now]):
        there, mine = then.get(label), now.get(label)
        if there is None:
            problems.append(_lines.placed_only_here(label, mine))
            continue
        if mine is None:
            problems.append(_lines.placed_only_there(label, there))
            continue
        relative = False
        for side, path, env in ((True, mine, here), (False, there, terminal)):
            if not _absolute(path, windows):
                relative = True
                name = _giver(path, env, windows)
                if name is not None:
                    givers.add(name)
                problems.append(_lines.not_full(label, side, name,
                                                None if name is None else env.value(name)))
        if not relative and _norm(mine, windows) != _norm(there, windows):
            problems.append(_lines.differs(label, mine, there))
    read = list(dict.fromkeys([*terminal.read, *here.read]))
    missing = []
    for name, why in (programs or {}).items():
        if paths.which(name, terminal) is not None and paths.which(name, here) is None:
            missing.append(name)
            problems.append(_lines.program_missing(name, why))
    if not problems:
        return None
    sets, takes = _fix(read, terminal, here, givers, missing, windows)
    from .. import modules

    line = _lines.fix(problems, sets, takes, page=page,
                      ui=page or modules.available("ui"))
    return _with_anchor(line, anchor, page)


# -- the comparison --------------------------------------------------------------------------

def _resolve(places: Places, env: Noting) -> dict[str, str]:
    found = places(env)
    return {str(label): os.fspath(path) for label, path in found.items()}


def _refuse_outside(env: Noting, windows: bool) -> None:
    outside = [name for name in env.read if not _record.in_set(name, fold=windows)]
    if outside:
        raise ValueError("a page's places read " + ", ".join(outside) + ", which the "
                         "core doesn't record from a terminal; a place resolver may read "
                         "only the variables that place folders or find programs")


def _absolute(path: str, windows: bool) -> bool:
    import ntpath
    import posixpath

    return (ntpath if windows else posixpath).isabs(path)


def _norm(path: str, windows: bool) -> str:
    import ntpath
    import posixpath

    flavour = ntpath if windows else posixpath
    return flavour.normcase(flavour.abspath(path))


def _giver(path: str, env: Noting, windows: bool) -> str | None:
    """The variable read under `env` whose value gave the relative `path`: the first read
    whose value isn't a full path and starts `path`, else the first read whose value isn't
    a full path, else None."""
    loose = [name for name in env.read
             if (value := env.value(name)) and not _absolute(value, windows)
             and name not in ("PATH", "PATHEXT")]
    parts = PurePath(path).parts
    for name in loose:
        given = PurePath(env.value(name)).parts
        if parts[:len(given)] == given:
            return name
    return loose[0] if loose else None


def _fix(read: list[str], terminal: Noting, here: Noting, givers: set[str],
         missing: list[str], windows: bool) -> tuple[list[str], list[str]]:
    """The fix's words: each name read whose value differs, set to the terminal's value or
    taken out; a name that gave a relative path, set to a full path when the terminal's
    own value is relative (even when the two agree); `PATH` with each missing program's
    folder on it, last."""
    sets, takes = [], []
    for name in read:
        if name in ("PATH", "PATHEXT") and missing:
            continue
        there, mine = terminal.value(name), here.value(name)
        if name in givers and there and not _absolute(there, windows):
            sets.append(_lines.to_full_path(name))
        elif there == mine:
            continue
        elif there is None:
            takes.append(name)
        elif there == "":
            sets.append(_lines.to_empty(name))
        else:
            sets.append(_lines.to_value(name, there))
    sets += [_lines.path_with(program) for program in missing]
    return sets, takes


# -- helpers ---------------------------------------------------------------------------------

def _windows() -> bool:
    return sys.platform == "win32"


def _anchor_set(env: Mapping[str, str], windows: bool) -> bool:
    from .. import paths

    if windows:
        return any(k.upper() == paths.ANCHOR_HOME and v for k, v in env.items())
    return bool(env.get(paths.ANCHOR_HOME))


def _with_anchor(line: str, anchor: bool, page: bool) -> str:
    return f"{line} {_lines.anchor_set(page=page)}" if anchor else line


def _error(error: OSError) -> str:
    return error.strerror or str(error) or type(error).__name__


__all__ = ["check", "Noting"]
