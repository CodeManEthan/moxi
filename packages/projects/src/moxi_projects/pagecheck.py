"""Whether a write from projects' page would land where the user's terminal writes.

The page server runs in the browser shell's environment, which can differ from the
user's terminal: another `MOXI_HOME`, `MOXI_PROJECTS_*`, `XDG_DATA_HOME` or `HOME` puts
the store, or a folder a contribution reads, somewhere the terminal never looks. Before a
write route does anything, its handler asks `line()`. It loads ui's check
(`moxi.modules.load("ui", interface=2)`), which resolves the route's `places` once from
the core's record of the environment the user's last run in a terminal had and once from
this process's, and returns the one line to refuse with when any place differs, or when
there is no record to compare with. projects itself records nothing: the core's front
door does, when a person types a `moxi` command in a terminal of their own.

A route's places are every folder or file it reads or writes while deciding or doing the
write, each placed by the resolver the code that reads or writes it uses:

- "the projects store", the store file (`store.path(env)`);
- "the home folder" (`moxi.paths.home(env)`), under which a root typed with `~` expands;
- for each contribution the route consults, that module's data, state and config folders
  ("services' data folder" and so on), since a contribution's `prepare`, kind check and
  `refuse_remove` read what its module keeps there. An add, a set and a remove consult
  every contribution that loads; adding or removing an agent consults none.

Without ui at interface 2 the page can't check, so `line()` gives the refusal that says
why and how to fix it: a page write never goes ahead unchecked. A place resolver that
reads a variable the core doesn't record is a bug here, and `line()` refuses with it too.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import moxi.modules
import moxi.names
import moxi.paths

from . import store

#: The labels of the places every write route has.
STORE = "the projects store"
HOME = "the home folder"
#: The interface of ui that offers the check.
UI_INTERFACE = 2
#: The step after a fix that makes the page's next server load the new ui.
RESTART = "then run moxi ui stop, and the page starts again on it"


def places(env: Mapping[str, str], modules: Iterable[str] = ()) -> dict[str, str]:
    """`{label: path}` for a write route, placed from `env` alone: the store, the home
    folder, and each of `modules`' data, state and config folders. `env` is read by name
    only. A home that nothing places is left out, so it differs only when the other
    environment places one."""
    found = {STORE: store.path(env)}
    try:
        found[HOME] = str(moxi.paths.home(env))
    except LookupError:
        pass
    for module in dict.fromkeys(modules):
        if not moxi.names.is_module_name(module):
            continue
        layout = moxi.paths.Layout(module)
        whose = _possessive(module)
        found[f"{whose} data folder"] = str(layout.data_dir(env))
        found[f"{whose} state folder"] = str(layout.state_dir(env))
        found[f"{whose} config folder"] = str(layout.config_dir(env))
    return found


def line(modules: Iterable[str] = ()) -> str | None:
    """None when a write that consults `modules`' contributions may go ahead here; else
    the one line to refuse it with (the module doc)."""
    found = moxi.modules.load("ui", interface=UI_INTERFACE)
    if not found.ok:
        return unchecked(found)
    consulted = tuple(modules)
    try:
        return found.api.check(lambda env: places(env, consulted))
    except ValueError as exc:
        return (f"this page can't compare where it writes with your terminal ({exc}), so "
                "it refuses to write; fix: run the command in a terminal, and report this "
                "to moxi-projects")


def unchecked(found: moxi.modules.Found) -> str:
    """The refusal of a page that can't load ui's check, with the step that fixes it."""
    reason = found.reason or "it can't be loaded"
    if found.state == moxi.modules.ABSENT:
        fix = f"install it with moxi install ui, {RESTART}"
    elif found.interfaces and UI_INTERFACE not in found.interfaces:
        fix = f"update moxi-ui to a release that offers interface {UI_INTERFACE}, {RESTART}"
    else:
        fix = f"repair or reinstall moxi-ui, {RESTART}"
    return (f"this page checks with ui that it writes where your terminal does, and can't "
            f"load ui's check ({reason}), so it refuses to write; fix: {fix}; or run the "
            "command in a terminal")


def _possessive(module: str) -> str:
    return f"{module}'" if module.endswith("s") else f"{module}'s"


__all__ = ["STORE", "HOME", "UI_INTERFACE", "places", "line", "unchecked"]
