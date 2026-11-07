"""The check a page makes before it writes: would it write where the user's terminal does?

    check(places, *, programs=None, page=True) -> str | None

`places(env)` returns `{label: path}`: every folder or file the act reads or writes while
deciding or doing its write, each resolved from the mapping `env` alone by the resolver
the code doing that read or write uses. A label is the words a person reads ("the
projects store"). `places` reads the mapping only by name (`get`, `[]`, `in`), never
iterates it, and reads only names the core records; a read of any other name raises
`ValueError`, so a contract bug refuses the write rather than letting it through.

`check` reads the core's record of the environment the user's last run in a terminal had
(`moxi.terminal.read`), calls `places` twice, once with the record's variables and once
with this process's environment, and returns None when every label resolves to the same
place under both. Otherwise it returns the one line to refuse with:

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

`where()` is where a person sets a variable for the browser shell on this platform, and the
step that makes the shell's next process take it.

The check reads only the core's public record names, and imports them when it runs: this
module imports nothing but the standard library, so loading ui's API starts nothing.
"""

from __future__ import annotations

import os
import sys
from collections import namedtuple
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import PurePath

Places = Callable[[Mapping[str, str]], Mapping[str, "str | os.PathLike[str]"]]


class Where(namedtuple("Where", ("place", "step"))):
    """Where to set a variable for the browser shell (`place`, words that follow "set X"),
    and the step after it that makes the shell's next process see the new value (`step`,
    words that follow "then")."""

    __slots__ = ()


def where(platform: str | None = None) -> Where:
    """Where the browser shell takes its environment from on `platform` (`sys.platform`
    without one), and the step that makes it take a change.

    Linux: a file under `~/.config/environment.d/`, which the user's service manager reads
    only when it starts or reloads, so the step reloads it and then stops the shell, which
    its socket starts again with the new environment. macOS and Windows: the LaunchAgent
    environment or the user environment variables, which the service manager takes at
    login, so the step is to log out and back in.
    """
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return Where("in your LaunchAgent environment", "log out and back in")
    if platform == "win32":
        return Where("in your user environment variables", "log out and back in")
    return Where("in a file under ~/.config/environment.d/",
                 "run systemctl --user daemon-reload and moxi ui stop")


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
    from moxi import paths, terminal

    windows = _windows()
    here_env = dict(os.environ)
    anchor = terminal.anchor_set(here_env, fold=windows)
    try:
        record = paths.terminal_record()
    except paths.NoAnchor as no:
        return _with_anchor(no_place(no.cause, no.step, page=page), anchor, page)
    outcome = terminal.read(record)
    if isinstance(outcome, terminal.NoRecord):
        return _with_anchor(no_record(str(record), page=page), anchor, page)
    if isinstance(outcome, terminal.Unreadable):
        return _with_anchor(unreadable(str(record), _error(outcome.error), page=page),
                            anchor, page)
    if isinstance(outcome, terminal.NewerFormat):
        return _with_anchor(newer(str(record), outcome.format, outcome.moxi,
                                  outcome.reader, page=page), anchor, page)
    there_env = Noting(outcome.env, windows=windows)
    here = Noting(here_env, windows=windows)
    then = _resolve(places, there_env)
    now = _resolve(places, here)
    for env in (there_env, here):
        _refuse_outside(env, windows)
    problems: list[str] = []
    givers: set[str] = set()
    for label in dict.fromkeys([*then, *now]):
        there, mine = then.get(label), now.get(label)
        if there is None:
            problems.append(placed_only_here(label, mine))
            continue
        if mine is None:
            problems.append(placed_only_there(label, there))
            continue
        relative = False
        for side, path, env in ((True, mine, here), (False, there, there_env)):
            if not _absolute(path, windows):
                relative = True
                name = _giver(path, env, windows)
                if name is not None:
                    givers.add(name)
                problems.append(not_full(label, side, name,
                                         None if name is None else env.value(name)))
        if not relative and _norm(mine, windows) != _norm(there, windows):
            problems.append(differs(label, mine, there))
    read = list(dict.fromkeys([*there_env.read, *here.read]))
    missing = []
    for name, why in (programs or {}).items():
        if paths.which(name, there_env) is not None and paths.which(name, here) is None:
            missing.append(name)
            problems.append(program_missing(name, why))
    if not problems:
        return None
    sets, takes = _fix(read, there_env, here, givers, missing, windows)
    return _with_anchor(fix(problems, sets, takes, page=page), anchor, page)


# -- the comparison --------------------------------------------------------------------------

def _resolve(places: Places, env: Noting) -> dict[str, str]:
    found = places(env)
    return {str(label): os.fspath(path) for label, path in found.items()}


def _refuse_outside(env: Noting, windows: bool) -> None:
    from moxi import terminal

    outside = [name for name in env.read if not terminal.in_set(name, fold=windows)]
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
            sets.append(to_full_path(name))
        elif there == mine:
            continue
        elif there is None:
            takes.append(name)
        elif there == "":
            sets.append(to_empty(name))
        else:
            sets.append(to_value(name, there))
    sets += [path_with(program) for program in missing]
    return sets, takes


def _windows() -> bool:
    return sys.platform == "win32"


def _error(error: OSError) -> str:
    return error.strerror or str(error) or type(error).__name__


def _with_anchor(line: str, anchor: bool, page: bool) -> str:
    return f"{line} {anchor_clause(page=page)}" if anchor else line


# -- the lines -------------------------------------------------------------------------------
#
# Every command a line names is a verb of the core itself, `moxi.terminal.RECORD_COMMAND`
# and `DOCTOR_COMMAND`, the same the doctor's terminal record row names, so the step works
# with ui alone beside the core; `where()`'s step names `moxi ui stop`, ui's own verb.

def differs(label: str, here: str, there: str) -> str:
    return f"{label} here is {here}, but the last run in a terminal used {there}"


def placed_only_here(label: str, here: str) -> str:
    return f"{label} here is {here}, but the last run in a terminal placed none"


def placed_only_there(label: str, there: str) -> str:
    return f"{label} isn't placed here, but the last run in a terminal used {there}"


def not_full(label: str, here: bool, name: str | None, value: str | None) -> str:
    side = "here" if here else "in the last run in a terminal"
    text = f"{label} isn't a full path {side}"
    if name is not None:
        text += f' ({name} is "{value}")'
    return text


def program_missing(name: str, why: str) -> str:
    return (f"{name} isn't on PATH here, but the last run in a terminal found it, "
            f"so {why}")


def to_value(name: str, value: str) -> str:
    return f"{name} to {value}"


def to_empty(name: str) -> str:
    return f"{name} to an empty value"


def to_full_path(name: str) -> str:
    return f"{name} to a full path"


def path_with(program: str) -> str:
    return f"PATH, with {program}'s folder on it,"


def fix(problems: Iterable[str], sets: list[str], takes: list[str], *, page: bool,
        platform: str | None = None) -> str:
    """The difference line: `problems` joined, then the fix that sets every name in `sets`
    (already worded) and takes every name in `takes` out of the browser shell's
    environment. With neither, no variable places it differently."""
    said = "; ".join(problems) + ". "
    if not sets and not takes:
        if page:
            return said + ("No variable places it differently here, so run the command "
                           "in a terminal.")
        return said + ("No variable places it differently; the browser page refuses to "
                       "write while it resolves another place than the last run in a "
                       "terminal.")
    place, step = where(platform)
    action = _action(sets, takes, place)
    if page:
        return said + f"{_capital(action)}, then {step}; or run the command in a terminal."
    return (said + "The browser page refuses to write while its shell differs from the "
            f"last run in a terminal; to give the browser shell the terminal's values at "
            f"every login, {action}, then {step}.")


def _action(sets: list[str], takes: list[str], place: str) -> str:
    parts = []
    if sets:
        parts.append(f"set {_and(sets)} {place}")
    if takes:
        named = _and(takes)
        parts.append(f"take {named} out of the browser shell's environment"
                     + ("" if sets else f" ({place.removeprefix('in ')})"))
    return ", and ".join(parts)


def no_record(record: str, *, page: bool) -> str:
    """No record at `record`: the step that makes one, and where to look when it doesn't."""
    record_command, doctor_command = _commands()
    if page:
        return ("Moxi has no record of the environment your terminal uses (it is kept at "
                f"{record}), so this page can't tell whether it would write where your "
                f"terminal does. Type {record_command} in a terminal of your own, not one "
                "Moxi opened such as a slot's window or an agent's shell, then try again. "
                f"If this line comes back, run {doctor_command} in that terminal: its "
                "terminal record row says what stops it.")
    return ("Moxi has no record of the environment your terminal uses (it is kept at "
            f"{record}), so the browser page refuses to write until one is made. Type "
            f"{record_command} in a terminal of your own, not one Moxi opened such as a "
            f"slot's window or an agent's shell. If the browser page still says so, run "
            f"{doctor_command} in that terminal: its terminal record row says what stops "
            "it.")


def no_place(cause: str, step: str, *, page: bool) -> str:
    """The account's folder can't be placed (`moxi.paths.NoAnchor`'s `cause` and `step`)."""
    who = "this page" if page else "the browser page"
    return (f"Moxi can't find where it keeps its record of your terminal: {cause}, so "
            f"{who} refuses to write; {step}, or run the command in a terminal.")


def unreadable(record: str, error: str, *, page: bool) -> str:
    who = "this page" if page else "the browser page"
    record_command, _ = _commands()
    return (f"Moxi's record of your terminal's environment at {record} can't be read "
            f"({error}), so {who} refuses to write; fix its permissions or remove it, "
            f"then type {record_command} in a terminal of your own.")


def newer(record: str, found_format: object, writer: object, mine: str, *,
          page: bool) -> str:
    who = "this page" if page else "the browser page"
    by = "a newer moxi" if writer is None else f"moxi {writer}"
    return (f"Moxi's record of your terminal's environment at {record} was written by "
            f"{by} in format {found_format}, newer than the moxi {mine} {who} "
            f"runs, so {who} refuses to write; restart {who} on the new code (run moxi ui "
            "stop); if the browser shell runs another Moxi environment that is older, run "
            "the command in a terminal.")


def anchor_clause(*, page: bool) -> str:
    """The clause a line ends with when its own environment sets `MOXI_ANCHOR_HOME`."""
    from moxi import paths

    whose = "This page's environment" if page else "This environment"
    return (f"{whose} sets {paths.ANCHOR_HOME}, which only test kits set; take it out of "
            "the browser shell's environment.")


def _commands() -> tuple[str, str]:
    """The core's command that makes a record, and the one that says what stops it."""
    from moxi import terminal

    return terminal.RECORD_COMMAND, terminal.DOCTOR_COMMAND


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _and(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


__all__ = ["check", "where", "Where", "Noting"]
