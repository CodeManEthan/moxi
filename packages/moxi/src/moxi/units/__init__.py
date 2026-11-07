"""The writer: a module's daemons and timers as units of this system's supervisor.

A module declares `DAEMONS` and `TIMERS` in its announcement, and may name `UNITS_FROM`, a
target that returns more at run time. The core writes them as systemd user units, launchd
agents or Task Scheduler tasks, marks every file it writes as its own, and never touches a
file it did not mark unless told to move it aside (`replace=True`).

    backend()                      the supervisor here: "systemd", "launchd", "schtasks", None
    home_tag()                     the tag in this home's unit names (None: the default home)
    environment(module)            the variables the module's units are written with
    ids(module, name, kind="daemon")
                                   the supervisor's ids for one unit, with this home's tag
    declarations(module)           the module's merged declarations (`Declarations`)
    plan(module, only=None)        what writing them would change (`UnitChange` per unit)
    apply(changes, replace=False, restart=False)   carry a plan out (`Applied` per change)
    remove(module)                 remove every unit the module's mark is on (uninstall)
    start|stop|restart|enable|disable(module, names, foreign=False, kinds=None)
                                   act on named units now (`Acted` per name)
    stop(module, names, record=[]) and resume(record)
                                   stop what runs, with a record, and start exactly that
    status(modules, foreign=(), kinds=None)
                                   what the supervisor says (`UnitState` per unit)
    starts(found)                  what starts each socket daemon now: login or connection
    last_runs(module, timer, n=1)  the gate's newest run lines of a timer
    wrap(ref, module=, key=, name=) and unwrap(ref, module=)
                                   a foreign systemd timer's runs behind a switch
    wraps(module)                  the module's wraps, read back

`plan`, `status`, `last_runs` and `wraps` only read and take no lock. `apply`, `remove`,
the acts, `resume`, `wrap` and `unwrap` take the core lock with `nested=True`, so a module
or services may call them alone and the settle step calls them under its install. To plan
and apply as one step outside an install, hold
`moxi.files.lock(moxi.paths.core_lock(), nested=True)` around both, so a plan is never
applied after an install changed the files.

What `plan` decides for a unit (`UnitChange.action`, with `cause` for the last three):

    add       no file yet
    change    a file with this unit's mark whose text differs
    keep      a file with this unit's mark and the same text
    remove    a marked file the module no longer declares, or a daemon switched off
              (cause `switch`); never when `UNITS_FROM` failed
    refuse    editable (a development install), locator (this shell sets one of the
              module's own locators), foreign (an unmarked file is in the way), command
              (no such script), switch, socket (also: one of its addresses is held, or
              named by a socket file, by what isn't this home's own daemon), declaration
    skip      nobackend, platform, switch (a daemon switched off with no unit),
              declaration (`UNITS_FROM` failed: the units it named are left as they are)

Unit names belong to one Moxi home. The default home (no `MOXI_HOME`) names a unit
`moxi-<module>-<name>` (systemd), `moxi.<module>.<name>` (launchd) or
`\\Moxi\\<module>-<name>` (Task Scheduler); a home given by `MOXI_HOME` puts its tag,
`home_tag()`, after `moxi`, and its units run with that `MOXI_HOME`. A home reads as its
own only the names it gives itself, so an untagged unit is the default home's and a tagged
home never names one to stop or remove. Nothing outside this package builds a unit name.

A timer behind a switch whose declaration changed is written only by `moxi install`,
which resets the switch first; any other `apply` leaves it as it is with one line
(see `moxi.units._apply`).
"""

from __future__ import annotations

from collections.abc import Callable

from . import _backend
from ._act import disable, enable, restart, resume, start, stop
from ._apply import apply, apply_held, core_lock, remove
from ._backend import Backend, backend
from ._declare import UNITS_FROM_SECONDS, declarations, digest, switch_problem
from ._home import environment, home_tag
from ._plan import plan
from ._status import last_runs, status
from ._types import (
    TASK_STATUS_CODES, Acted, Applied, DeclarationError, Declarations, Stopped, UnitChange,
    UnitState, Wrapped)
from ._wrap import unwrap, wrap, wraps


def ids(module: str, name: str, kind: str = "daemon") -> tuple[str, ...]:
    """The ids the supervisor here knows unit `<module>/<name>` by, with this home's tag
    in them (`home_tag`), in the order `stop` takes them; the empty tuple when there is no
    supervisor. `kind` is `daemon` or `timer`. On systemd a daemon's are its first socket
    unit `<stem>.socket` (whether or not the daemon has a socket or its file is there),
    each further `<stem>-<k>.socket` whose file is there, then `<stem>.service`; a timer's
    are `<stem>.timer` then `<stem>.service`. On launchd it is the label, on Task Scheduler
    the task path. Reads no supervisor. The one way for code outside this package to
    learn a unit's name. A supervisor this moxi has no writer for gives the empty tuple
    too, since nothing here wrote a unit for it."""
    if kind not in ("daemon", "timer"):
        raise ValueError(f"kind is {kind!r}, not daemon or timer")
    try:
        impl = _backend.implementation()
    except NotImplementedError:
        return ()
    if impl is None:
        return ()
    return tuple(impl.ids(module, name, kind))


def switched_daemons(module: str, key: str) -> list[str] | DeclarationError:
    """The daemons of `module` whose switch setting `key` sets (the key itself, or the
    switch table it is an entry of, or the table whose entries it holds), read through
    `declarations`. A daemon whose socket `start` names the key in its switch form
    (`{"login": key}`) counts as well, since the key decides what starts it. When
    `UNITS_FROM` fails, the `DAEMONS` entries alone are read, and the error is returned if
    none of them names `key`."""
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
    from ..modules import login_switch
    from ._declare import is_switch_of

    return [name for name, entry in daemons.items()
            if any(switch and is_switch_of(key, switch)
                   for switch in (entry.get("switch"), login_switch(entry)))]


def starts(found, *, env=None) -> dict[str, str | None]:
    """What starts each daemon with a socket in `found` (a present `moxi.modules.Found`)
    now, as `plan` resolves it: `"login"` or `"connection"`, by its announcement's
    `DAEMONS` alone. A switch-form `start` whose key cannot be read, or is no bool
    setting read at use, is None. Reads the config only for a switch form; never runs
    `UNITS_FROM`. The value is what systemd and launchd do: on Windows the logon task
    starts every daemon at each logon whatever it says."""
    from .. import config
    from ..modules import login_switch
    from ._declare import switch_value

    out: dict[str, str | None] = {}
    schema: list = []
    for name, entry in found.daemons.items():
        socket = entry.get("socket")
        if not socket:
            continue
        key = login_switch(entry)
        if key is None:
            out[name] = socket.get("start", "connection")
            continue
        try:
            if not schema:
                schema.append(config.schema_for(found))
            if switch_problem(schema[0], key):
                out[name] = None
                continue
            on = switch_value(found.name, schema[0], key, env)
        except config.ConfigError:
            out[name] = None
            continue
        out[name] = "login" if on else "connection"
    return out


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
        applied = apply(plan(module, only=names), runner=runner, say=say)
    if any(not a.ok for a in applied):
        say(f"{module}: {key} stays set; `moxi install {module}` retries the units")
    return applied


__all__ = [
    "Acted", "Applied", "Backend", "DeclarationError", "Declarations", "Stopped",
    "TASK_STATUS_CODES", "UNITS_FROM_SECONDS", "UnitChange", "UnitState", "Wrapped",
    "apply", "apply_switch", "backend", "core_lock", "declarations", "digest", "disable",
    "enable", "environment", "home_tag", "ids", "last_runs", "plan", "remove", "restart",
    "resume", "start", "starts", "status", "stop", "switch_problem", "switched_daemons",
    "unwrap", "wrap", "wraps"]
