"""Moving a module's live data out of the old host folder, with its daemons stopped.

Before the one home, a module's state and cache directories sat under a host folder:
`<root>/<label>/<module>`, where `<label>` is the module's device variable or the host
name. `moxi.paths` reads such a directory in place until it is moved, so nothing breaks
before then. `move` is the move itself. `moxi install <module>` runs it first in the
module's settle step (`moxi._settle`), and nothing else calls it.

The rule the move keeps, with the settle step around it:

1. Before it stops anything, it knows every daemon unit of the module that could be
   running over the data: the daemons of the merged declarations (`DAEMONS` plus what
   `UNITS_FROM` returns, read by `moxi.units.declarations`, the read `plan` makes), each
   with its socket unit. When those can't be read, it refuses: it can't tell what to stop.
2. A unit file among them that moxi did not write for the module makes it refuse, naming
   `--replace`, unless `replace=True`.
3. Whatever it stops, marked or not, it records as it stops it, one unit at a time,
   before it stops the next (`moxi.units.stop(..., record=...)`).
4. On every way out of the settle step, the settle step starts exactly the recorded units
   again (`moxi.units.resume`), and nothing else.
5. An interrupt never ends the settle step before that start has run (see `moxi._settle`).

Every refusal comes before any stop, so a refusal leaves an empty record. A stop that
fails or doesn't finish is no refusal: the units it recorded are started again.

Three things are left. Timers are not stopped: a timer's run is a short process, and one
that overlaps the rename may fail or make the old folder again, which the doctor's home
row then shows. A marked unit the module no longer declares is not stopped either (the
unit step removes it later); it can only be running when an old folder came back after an
earlier install. And a settle child killed outright (SIGKILL, power loss) starts nothing
again: the record lives only in its memory.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

#: The roots whose module directories sat under a host folder.
KINDS = ("state", "cache")


class MoveError(Exception):
    """The move refused, or did not finish. `line` is the one line that says why, naming
    the module; `refused` is True when nothing was stopped (a refusal), False when units
    may have been stopped (a stop that did not finish, a rename that failed).
    `only_move` is True for the one refusal that leaves the rest of the install to run:
    an old directory that is a link to a relative path, which stays read in place."""

    def __init__(self, line: str, *, refused: bool, only_move: bool = False):
        super().__init__(line)
        self.line = line
        self.refused = refused
        self.only_move = only_move


def legacy(module: str) -> list[tuple[str, Path, Path]]:
    """Each old directory of `module` under a host folder, as `(kind, old, new)`, found by
    the test `moxi.paths` reads in place by: a directory, or a symbolic link to one. Reads
    the file system only."""
    from . import paths

    layout = paths.Layout(module)
    out = []
    for kind in KINDS:
        root = getattr(layout, f"{kind}_root")()
        old = layout._legacy(root, os.environ)
        if old is not None:
            out.append((kind, old, root / module))
    return out


def relative_link(path: Path) -> bool:
    """Whether `path` is a symbolic link whose target is a relative path: renaming it one
    folder up would point it elsewhere."""
    try:
        return os.path.islink(path) and not os.path.isabs(os.readlink(path))
    except OSError:
        return False


def link_fix(module: str, old: Path, new: Path) -> str:
    """What to do about an old directory that is a link to a relative path."""
    return (f"make {old} an absolute link, or move the folder to {new} by hand, then "
            f"run `moxi install {module}` again")


def move(module: str, record: list, *, replace: bool = False,
         say: Callable[[str], object] = print) -> list:
    """Move `module`'s old state and cache directories to their new paths, with the
    module's running daemons stopped, and return `record`.

    `record` is a list the caller owns and passes in empty. Each unit the move stops is
    appended to it (a `moxi.units.Stopped`) before it is stopped, so what it holds
    survives the move raising; the caller starts exactly those again with
    `moxi.units.resume(record)` however the move ends. With nothing to move, nothing is
    read or stopped and `record` comes back empty.

    An old directory that is a symbolic link to an absolute path is moved as the link:
    the new path becomes the link, and the data stays where the user put it.

    Raises `MoveError`: a refusal (an old directory that is a link to a relative path,
    both directories hold data, the rename would cross file systems, the declarations
    can't be read, an unmarked unit without `replace=True`) before anything is stopped; a
    stop that did not finish, or a failed rename, after.
    Takes the core lock with `nested=True`, so under an install it yields.
    """
    from . import files, paths

    with files.lock(paths.core_lock(), nested=True):
        moves = _what_moves(module, say)
        if not moves:
            return record
        names, impl = _daemons(module)
        if impl is not None and names:
            _unmarked(module, names, impl, replace)
            if not replace:
                # No unit file of these names is unmarked (`_unmarked`), so a name with
                # no marked unit has no unit at all, and nothing of it can run.
                marked = {ref.name for ref in impl.marked(module)}
                names = [name for name in names if name in marked]
            if names:
                _stop(module, names, replace, record)
        for _, old, new in moves:
            _rename(module, old, new, say)
        return record


# -- the steps ---------------------------------------------------------------------------------

def _what_moves(module: str, say) -> list[tuple[str, Path, Path]]:
    """Step 1: the directories to rename, or a refusal. An empty old directory beside a
    new one is removed, with nothing to stop."""
    found = legacy(module)
    for _, old, new in found:
        if relative_link(old):
            raise MoveError(f"{module}: {old} is a link to a relative path; "
                            f"{link_fix(module, old, new)}", refused=True, only_move=True)
    moves = []
    for kind, old, new in found:
        if os.path.lexists(new):
            if _empty(old):
                _tidy(old)
                continue
            if not (new.is_dir() and _empty(new)):
                raise MoveError(f"{module}: both {old} and {new} hold its {kind} data; "
                                f"merge them by hand and remove {old}; nothing moved",
                                refused=True)
        try:
            # The old path itself: a link is renamed as the link, wherever it points.
            crosses = os.lstat(old).st_dev != os.stat(new.parent).st_dev
        except OSError as exc:
            raise MoveError(f"{module}: can't read {old} or {new.parent} ({exc}); "
                            "nothing moved", refused=True) from None
        if crosses:
            raise MoveError(f"{module}: {old} and {new} are on different file systems, "
                            f"so the move can't rename one to the other; move it by hand; "
                            "nothing moved", refused=True)
        moves.append((kind, old, new))
    return moves


def _daemons(module: str):
    """Step 2: every daemon name that could be running, and the backend; a refusal when
    the declarations can't be read. `(names, None)` when there is no supervisor."""
    from . import units
    from .units import _backend

    try:
        decl = units.declarations(module)
    except units.DeclarationError as exc:
        raise MoveError(f"{module}: can't tell what to stop (UNITS_FROM failed: "
                        f"{exc.text}); nothing moved", refused=True) from None
    except ValueError as exc:
        raise MoveError(f"{module}: can't tell what to stop ({exc}); nothing moved",
                        refused=True) from None
    names = list(decl.daemons)
    names += [name for kind, name in decl.rejected
              if kind == "daemon" and name not in names]
    try:
        impl = _backend.implementation()
    except NotImplementedError as exc:
        raise MoveError(f"{module}: can't tell what to stop ({exc}); nothing moved",
                        refused=True) from None
    return names, impl


def _unmarked(module: str, names: list[str], impl, replace: bool) -> None:
    """Step 3: refuse when a unit file of one of `names` is not this module's, unless
    `replace`."""
    if replace:
        return
    for name in names:
        unit = f"{module}/{name}"
        for path in impl.files(module, name, "daemon"):
            try:
                text = path.read_bytes().decode(impl.encoding, errors="replace")
            except FileNotFoundError:
                continue
            except OSError:
                text = ""
            if impl.mark(text) != unit:
                raise MoveError(f"{path.name} was not written by moxi; `moxi install "
                                f"{module} --replace` moves it to DATA/moxi/displaced/",
                                refused=True)


def _stop(module: str, names: list[str], replace: bool, record: list) -> None:
    """Step 4: stop what runs, recorded; raise before the rename when a stop failed."""
    from . import units

    before = len(record)
    acted = units.stop(module, names, replace=replace, stopped=record)
    failed = [a for a in acted if not a.ok]
    if not failed:
        return
    stopped_now = record[before:]
    if not stopped_now:
        # The running read failed: nothing was stopped, so this is a refusal.
        line = failed[0].lines[0] if failed[0].lines else f"{failed[0].name} did not stop"
        raise MoveError(f"{module}: {line}; nothing moved", refused=True)
    bad = [entry.unit for entry in stopped_now
           if any(line.startswith(f"{entry.unit}: ") for a in failed for line in a.lines)]
    which = ", ".join(bad) or ", ".join(a.name for a in failed)
    raise MoveError(f"{module}: {which} did not stop; nothing moved", refused=False)


def _rename(module: str, old: Path, new: Path, say) -> None:
    """Step 5: rename, then remove the host folder when it is left empty. A link (to an
    absolute path, step 1) is renamed as the link: the new path becomes the link, and the
    data stays where it points."""
    try:
        if new.is_dir() and not new.is_symlink() and _empty(new):
            new.rmdir()
        os.rename(old, new)
    except OSError as exc:
        raise MoveError(f"{module}: could not move {old} to {new}: {exc}",
                        refused=False) from None
    say(f"{module}: moved {old} to {new}")
    _tidy_label(old.parent)


def _empty(path: Path) -> bool:
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def _tidy(old: Path) -> None:
    try:
        if old.is_symlink():
            old.unlink()  # a link to an empty folder: the folder stays where it is
        else:
            old.rmdir()
    except OSError:
        return
    _tidy_label(old.parent)


def _tidy_label(folder: Path) -> None:
    try:
        folder.rmdir()
    except OSError:
        pass  # not empty: another module's data is still there


__all__ = ["KINDS", "MoveError", "legacy", "link_fix", "move", "relative_link"]
