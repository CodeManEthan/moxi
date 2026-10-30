"""The writer: a module's daemons and timers as units of this system's supervisor.

A module declares `DAEMONS` and `TIMERS` in its announcement, and may name `UNITS_FROM`, a
target that returns more at run time. The core writes them as systemd user units, launchd
agents or Task Scheduler tasks, marks every file it writes as its own, and never touches a
file it did not mark unless told to move it aside (`replace=True`).

    backend()                      the supervisor here: "systemd", "launchd", "schtasks", None
    declarations(module)           the module's merged declarations (`Declarations`)
    plan(module, only=None)        what writing them would change (`UnitChange` per unit)
    apply(changes, replace=False, restart=False)   carry a plan out (`Applied` per change)
    remove(module)                 remove every unit the module's mark is on (uninstall)

`plan` reads and takes no lock. `apply` and `remove` take the core lock, so a module or
services may call them alone. To plan and apply as one step outside an install, hold the
core lock around both, so a plan is never applied after an install changed the files.

What `plan` decides for a unit (`UnitChange.action`, with `cause` for the last three):

    add       no file yet
    change    a file with this unit's mark whose text differs
    keep      a file with this unit's mark and the same text
    remove    a marked file the module no longer declares, or a daemon switched off
              (cause `switch`); never when `UNITS_FROM` failed
    refuse    editable (a development install), foreign (an unmarked file is in the
              way), command (no such script), switch, socket, declaration
    skip      nobackend, platform, switch (a daemon switched off with no unit),
              declaration (`UNITS_FROM` failed: the units it named are left as they are)

A timer behind a switch whose declaration changed is written only by `moxi install`,
which resets the switch first; any other `apply` leaves it as it is with one line
(see `moxi.units._apply`).
"""

from __future__ import annotations

from collections.abc import Callable

from ._apply import apply, apply_held, core_lock, remove
from ._backend import Backend, backend
from ._declare import UNITS_FROM_SECONDS, declarations, digest, switch_problem
from ._plan import plan
from ._types import (
    Acted, Applied, DeclarationError, Declarations, Stopped, UnitChange, UnitState,
    Wrapped)


def switched_daemons(module: str, key: str) -> list[str] | DeclarationError:
    """The daemons of `module` whose switch setting `key` sets (the key itself, or the
    switch table it is an entry of, or the table whose entries it holds), read through
    `declarations`. When `UNITS_FROM` fails, the `DAEMONS` entries alone are read, and the
    error is returned if none of them names `key`."""
    from .. import modules
    from ._declare import merge

    found = modules.load(module)
    if found.state != modules.PRESENT:
        return []
    try:
        daemons = merge(found).daemons
    except DeclarationError as exc:
        names = _naming(found.daemons, key)
        return names or exc
    return _naming(daemons, key)


def _naming(daemons, key: str) -> list[str]:
    from ._declare import is_switch_of

    return [name for name, entry in daemons.items()
            if entry.get("switch") and is_switch_of(key, entry["switch"])]


def apply_switch(module: str, key: str, *, runner=None,
                 say: Callable[[str], object] = print) -> list[Applied]:
    """After `moxi config set` or `unset` of `key`: plan and apply the daemons whose switch
    it is, and nothing else of the module, under the core lock. The empty list when `key`
    is no daemon's switch."""
    names = switched_daemons(module, key)
    if isinstance(names, DeclarationError):
        say(f"{module}: UNITS_FROM failed ({names.text}), so {key} was not checked as a "
            f"daemon switch; `moxi install {module}` applies the units once it is fixed")
        return []
    if not names:
        return []
    say(f"{module}: applying units for {key}")
    with core_lock():
        applied = apply_held(plan(module, only=names), runner=runner, say=say)
    if any(not a.ok for a in applied):
        say(f"{module}: {key} stays set; `moxi install {module}` retries the units")
    return applied


__all__ = [
    "Acted", "Applied", "Backend", "DeclarationError", "Declarations", "Stopped",
    "UNITS_FROM_SECONDS", "UnitChange", "UnitState", "Wrapped", "apply", "apply_switch",
    "backend", "core_lock", "declarations", "digest", "plan", "remove", "switch_problem",
    "switched_daemons"]
