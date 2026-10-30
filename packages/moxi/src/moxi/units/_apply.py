"""`apply` and `remove`: carrying a plan out under the core lock.

The core's half is the same on every backend: the core lock; what a `refuse`, a `skip`
and a `keep` mean; the hold on a changed switched timer; moving a foreign file aside with
`replace=True`; and one `Applied` per change. The backend's `apply` does the rest in its
own order (see `moxi.units._backend.Backend.apply`).

The hold: a timer `add` or `change` whose timer has a `switch` is written only when
`units.json` already holds that change's declaration digest. Only the settle step writes
that entry, after it has set the switch, so no other caller makes a changed timer fire
behind a switch that was on for its old declaration. Any other caller gets
`Applied(ok=True)` with a line naming `moxi install <module>`, and the unit is left as it
is. A switchless timer has no entry and is written by any caller.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import replace as _replaced
from pathlib import Path

from . import _backend, _state
from ._backend import ApplyIO
from ._fence import Fence
from ._types import Applied, UnitChange


@contextmanager
def core_lock():
    """The core lock, held for `apply`, `remove`, the acts, `wrap` and `unwrap`.

    Every unit call that writes or acts takes it here, and only here, so the one place
    to make it yield inside an install is this function.
    """
    from .. import files, paths

    with files.lock(paths.core_lock()):
        yield


def apply(changes: Iterable[UnitChange], *, replace: bool = False, restart: bool = False,
          runner=None, say: Callable[[str], object] = print) -> list[Applied]:
    """Carry out a plan; one `Applied` per change, in order. Takes the core lock.

    `replace=True` writes a unit planned `refuse` for a foreign file, moving that file to
    `DATA/moxi/displaced/<UTC stamp>/<file>` first. `restart=True` (after a module
    upgrade) restarts running daemons whose entry says `upgrade = "restart"` even when
    their text is unchanged. `runner` replaces the supervisor runner (tests); `say`
    prints each line.

    A `keep` is ok with no line; a `skip` is ok with its reason; a `refuse` prints its
    reason and is `ok=False`, except for cause `editable`, which is how a development
    install reads and is ok. A failed step prints the command and the supervisor's text
    and one remedy line, makes that change `ok=False`, and the rest go on.
    """
    changes = list(changes)
    with core_lock():
        return apply_held(changes, replace=replace, restart=restart, runner=runner,
                          say=say)


def apply_held(changes: Sequence[UnitChange], *, replace: bool = False,
               restart: bool = False, runner=None,
               say: Callable[[str], object] = print) -> list[Applied]:
    """`apply` for a caller that already holds the core lock."""
    done: dict[int, Applied] = {}
    order: list[UnitChange] = []
    act: list[UnitChange] = []
    recorded = _state.read()
    for change in changes:
        if change.action == "refuse" and change.cause == "foreign" and replace \
                and change.texts:
            change = _replaced(change, action="change", cause=None)
        order.append(change)
        if change.action == "refuse":
            say(change.reason)
            done[id(change)] = Applied(change, change.cause == "editable", (change.reason,))
        elif change.action == "skip":
            say(change.reason)
            done[id(change)] = Applied(change, True, (change.reason,))
        elif change.action == "keep" and not restart:
            done[id(change)] = Applied(change, True)
        elif change.action in ("add", "change") and _held(change, recorded):
            line = _held_line(change)
            say(line)
            done[id(change)] = Applied(change, True, (line,))
        elif change.action in ("add", "change", "remove", "keep"):
            act.append(change)
        else:
            raise ValueError(f"{change.unit}: no such action {change.action!r}")
    if act:
        impl = _backend.implementation()
        if impl is None:
            for change in act:
                line = f"{change.unit}: no supervisor on this system; nothing was written"
                say(line)
                done[id(change)] = Applied(change, False, (line,))
        else:
            io = _io(impl, runner, say, replace)
            impl.apply(act, restart=restart, io=io)
            for change in act:
                done[id(change)] = io.result(change)
    return [done[id(change)] for change in order]


def remove(module: str, *, runner=None, say: Callable[[str], object] = print
           ) -> list[Applied]:
    """Remove every unit carrying `module`'s mark, unwrap every foreign timer it wrapped,
    and drop its `units.json` entries (uninstall). Reads no declaration, so it works for a
    module whose package is broken. Takes the core lock."""
    with core_lock():
        impl = _backend.implementation()
        if impl is None:
            _state.forget(module)
            return []
        changes = [UnitChange(module, ref.name, ref.kind, "remove", None, ref.paths, None,
                              "uninstalled", enabled_before=ref.enabled)
                   for ref in impl.marked(module)]
        out: list[Applied] = []
        if changes:
            io = _io(impl, runner, say, False)
            impl.apply(changes, restart=False, io=io)
            out = [io.result(change) for change in changes]
        for acted in impl.unwrap_all(module, fence(impl, runner)):
            change = UnitChange(module, acted.name, "wrap", "remove", None, (), None,
                                "unwrapped")
            out.append(Applied(change, acted.ok, acted.lines))
        _state.forget(module)
        return out


def fence(impl, runner=None) -> Fence:
    """The fence for backend `impl`: through `runner` when given, else the backend's own
    `runner` attribute when it has one (a test backend), else the real supervisor."""
    return Fence(impl.supervisor, runner or getattr(impl, "runner", None))


def _io(impl, runner, say, replace: bool) -> ApplyIO:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    io = ApplyIO(fence(impl, runner), say=say, replace=replace,
                 displaced=lambda path: _displaced(stamp, path))
    io.encoding = impl.encoding
    return io


def _displaced(stamp: str, path: Path) -> Path:
    from .. import paths

    return paths.core().data_dir() / "displaced" / stamp / path.name


def _held(change: UnitChange, recorded: dict) -> bool:
    """True when a switched timer's change waits for the settle step."""
    if change.kind != "timer" or change.switch is None:
        return False
    entry = recorded.get(change.unit)
    return not (isinstance(entry, dict) and entry.get("digest") == change.digest)


def _held_line(change: UnitChange) -> str:
    what = "is new" if change.action == "add" else "changed"
    return (f"{change.unit} {what}; `moxi install {change.module}` applies it and asks "
            "about its switch")
