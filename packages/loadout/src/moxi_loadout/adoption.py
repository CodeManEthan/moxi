"""Module skills held by loadout: taking a module's placements over, and placing one the
manifest lists as its module.

Three rules decide every case:

- **One owner.** Every placement loadout holds of a module's skill is one that module
  handed over (`moxi.place.adopt`), so the module's own skills step finds the target in
  its `handed` map and skips it.
- **A hand-over is one skill's choice.** A skill placed because the manifest lists it is
  handed over *seeded*: it never stands in for a yes to the module's skills question.
- **Disabled stays off.** A skill the manifest disables is not left placed.

`take(modules)` hands each module's own placements (made after a yes at its question) to
loadout, unseeded, and lists them in the manifest's `[user] skills` with the harnesses they
were placed for. It writes the manifest and places nothing.

`place_seeded(module, item)` places a skill nobody has placed (a second machine, the
cloud): planned and applied as the module, its `add` step alone, then handed over seeded.
If the hand-over fails, the one placement is undone in the same run. The span from the
placement to the hand-over defers `SIGINT` and `SIGTERM` until it ends.

Both run under the core lock the caller holds.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass

OWNER = "loadout"


@dataclass(frozen=True)
class Taken:
    """One placement handed to loadout: its module and the `Placed` loadout now holds."""

    module: str
    placed: object


def take(modules: Iterable[str] | None = None, *, folder=None,
         say: Callable[[str], object] = print) -> list[Taken]:
    """Hand every placement of each module in `modules` (every installed module but
    loadout when None) to loadout, unseeded, and list each in the manifest with the
    harnesses taken. Returns what was taken."""
    from moxi import place

    from moxi_loadout import writes

    if modules is None:
        from moxi.modules import installed

        modules = sorted({e.name for e in installed()} - {OWNER})
    taken: list[Taken] = []
    for module in modules:
        if module == OWNER:
            continue
        mine = place.status(module)
        if not mine:
            continue
        for p in place.adopt(module, OWNER, mine, say=say):
            taken.append(Taken(module, p))
    skills: dict[str, list[str]] = {}
    for t in taken:
        skills.setdefault(f"{t.module}/{t.placed.name}", []).append(t.placed.harness)
    if skills:
        writes.adopt(skills, folder=folder)
    return taken


@dataclass(frozen=True)
class Seeded:
    """What placing a module skill from the manifest came to: `placed` (loadout's now),
    else the module's `step` that was refused, or `line` saying what failed."""

    placed: object = None
    step: object = None
    line: str = ""


def place_seeded(module: str, item, *, say: Callable[[str], object] = print) -> Seeded:
    """Place `item` (a skill of `module`) as `module`, then hand it to loadout seeded.

    The module's plan for the item is applied for its own step only, never the `remove`
    steps the plan yields for the module's other placements. A refused step is returned
    and nothing is adopted. When the placement is made but the hand-over fails, the
    placement is undone: the `remove` step for that target out of `plan(module, [])`,
    applied alone. If that fails too, the line names the target."""
    from moxi import place

    step = place.plan(module, [item])[0]
    if step.action != "add":
        return Seeded(step=step)
    target = str(step.target)
    with deferred_signals():
        done = place.apply([step], say=say)
        if not any(str(p.path) == target for p in done):
            return Seeded(line=f"could not place {item.name} at {target}")
        try:
            moved = place.adopt(module, OWNER, done, seeded=True, say=say)
        except Exception as exc:  # noqa: BLE001 - any failure leaves the module's placement
            moved, why = [], f"{type(exc).__name__}: {exc}"
        else:
            why = "the hand-over was refused"
        mine = next((p for p in moved if str(p.path) == target), None)
        if mine is not None:
            return Seeded(placed=mine)
        return Seeded(line=_undo(module, target, why, say))


def _undo(module: str, target: str, why: str, say) -> str:
    from moxi import place

    try:
        steps = [s for s in place.plan(module, [])
                 if s.action == "remove" and str(s.target) == target]
        if steps:
            place.apply(steps, say=say)
        left = any(str(p.path) == target for p in place.status(module))
    except Exception as exc:  # noqa: BLE001
        left, why = True, f"{why}; the undo failed: {exc}"
    if left:
        return (f"placed {target} as {module} but could not hand it to loadout ({why}), "
                f"and it is still placed there; remove it, or run install again")
    return f"could not hand {target} to loadout ({why}); the placement was undone"


@contextmanager
def deferred_signals():
    """Hold `SIGINT` and `SIGTERM` for the span: one that comes in is delivered when the
    span ends. Off the main thread, where handlers can't be set, the span holds nothing."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    wanted = [signal.SIGINT] + ([signal.SIGTERM] if hasattr(signal, "SIGTERM") else [])
    old = {}
    got: list[int] = []
    for signum in wanted:
        handler = signal.getsignal(signum)
        if handler in (signal.SIG_IGN, None):
            continue
        old[signum] = handler
        signal.signal(signum, lambda n, _frame: got.append(n))
    try:
        yield
    finally:
        for signum, handler in old.items():
            signal.signal(signum, handler)
        if got:
            signal.raise_signal(got[0])


__all__ = ["OWNER", "Taken", "Seeded", "take", "place_seeded", "deferred_signals"]
