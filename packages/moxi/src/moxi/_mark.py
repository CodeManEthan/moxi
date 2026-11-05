"""The start mark: a variable every process a Moxi program starts carries.

Importing the `moxi` package calls `note()` once: it keeps whether `MOXI_STARTED_BY` was
in the environment this process started with (`started_by`), then sets it in
`os.environ` when it wasn't, naming this program. So every child that inherits or copies
a Moxi program's environment carries it, and `marked` adds it to an environment built
from scratch. The mark can stop this account's terminal record from being written, never
cause one: a process that started with it is one Moxi started.

Standard library only, and nothing imported beyond `os` and `sys`, since every import of
the core runs this.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

#: The variable. Its value names the program that set it; only its presence is read.
NAME = "MOXI_STARTED_BY"

#: The mark's value in the environment this process started with; None when it had none.
#: Read once, by `note()`, before this process sets the mark itself.
started_by: str | None = None
#: The value this process gives the mark: its program's name.
program: str = "python"
_noted = False


def note() -> None:
    """Keep whether this process started with the mark, then set it if it didn't. Only
    the first call does anything, so reloading the package notes nothing new."""
    global started_by, program, _noted
    if _noted:
        return
    _noted = True
    started_by = os.environ.get(NAME)
    program = _program()
    if started_by is None:
        os.environ[NAME] = program


def marked(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of `env` (None: this process's environment) carrying the mark: a value it
    already has is kept, else this program's name is set. On Windows, where names are the
    same whatever their case, any spelling of the name counts as the mark."""
    child = dict(os.environ if env is None else env)
    if sys.platform == "win32":
        if not any(name.upper() == NAME for name in child):
            child[NAME] = program
    else:
        child.setdefault(NAME, program)
    return child


def _program() -> str:
    """This program's name, for the mark's value: `python -m <module>` for a module run
    with `-m`, else the script's base name, else the interpreter's."""
    orig = getattr(sys, "orig_argv", None) or []
    for at, part in enumerate(orig[1:], start=1):
        if not part.startswith("-") or part.startswith("--") or "c" in part:
            break
        if part.endswith("m"):
            return f"python -m {orig[at + 1]}" if at + 1 < len(orig) else "python"
        if part.startswith("-m"):
            return f"python -m {part[2:]}"
    argv = getattr(sys, "argv", None) or [""]
    first = argv[0] or ""
    if first not in ("", "-c", "-m"):
        return os.path.basename(first) or first
    return os.path.basename(sys.executable or "") or "python"
