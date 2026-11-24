"""Whether a launch's session can recycle itself: the one test the launcher's three places
read, so what a session is told and what its arm accepts can't drift apart.

    missing(harness=..., target=..., ...)   # the first item that fails, or None
    missing_of_row(row)                     # the same, read from a launch row

A launch can finish a self relaunch when it is:

1. **set to relaunch**: `recycle_at_limit = relaunch`, or `off` where that value came
   from the shipped default or no source was recorded (`opted_in`: a launch made before
   `relaunch` became the default, which never chose `off`). `off` or `compact` from any
   other layer opts out.
2. **a Claude launch**: the relaunch exits the session through turns' `exit`, which only
   Claude's turns session offers.
3. **a slot launch**, with slots running here.
4. **not in a scratch folder** (`catalog.SCRATCH_RECYCLE`).
5. **one with a handoff folder** (`handoffs.handoff_dir`): with notes installed, notes
   must give the launch's folder a notes directory.
6. **warned at a mark**: its `warn_at` is `1x`, `1.5x` or `2x`. The relaunch line rides
   on usage's notice at that mark.

The composer stamps the relaunch crossing only where this holds (`common.option_stamps`),
the crossing handler answers only where it holds (`recycle.crossing_line`), and the self
arm accepts only where it holds (`relaunch.may_recycle`). `presets.resolve` sets the
shipped default `relaunch` to `off`, quietly, on a launch where it fails.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from . import catalog

#: The row that lets a launch recycle at its limit, and the value that recycles it itself.
RECYCLE_AT_LIMIT = "recycle_at_limit"
RELAUNCH = "relaunch"
#: The row holding the mark usage warns the session at, and the marks it takes.
WARN_AT = "warn_at"
MARKS = ("1x", "1.5x", "2x")
#: Where a value came from when the shipped default gave it (`presets.SHIPPED`), and when
#: the launcher left the value out (`presets.LEFT_OUT`).
SHIPPED, LEFT_OUT = "shipped", "left-out"

#: The items, in the order they are tested.
OPTED_OUT, HARNESS, SLOT, SLOTS, SCRATCH, HANDOFFS, NO_MARK = (
    "opted-out", "harness", "slot", "slots", "scratch", "handoffs", "no-mark")


@dataclass(frozen=True)
class Missing:
    """The item a launch fails (`OPTED_OUT`, `HARNESS`, `SLOT`, `SLOTS`, `SCRATCH`,
    `HANDOFFS` or `NO_MARK`), and words for it: what is wrong and what to do."""

    item: str
    problem: str
    hint: str


def opted_in(value: object, source: object) -> bool:
    """Whether `recycle_at_limit`'s `value`, from `source` (a `presets` source, None when
    none was recorded), lets the launch recycle itself: `relaunch`, or `off` from the
    shipped default or from no recorded source."""
    if value == RELAUNCH:
        return True
    return value == "off" and source in (SHIPPED, None)


def source_of(values: Mapping[str, Any], name: str = RECYCLE_AT_LIMIT) -> str | None:
    """Where a launch row's value `name` came from, by the `sources` its values record;
    None when they record none for it."""
    sources = values.get("sources")
    found = sources.get(name) if isinstance(sources, Mapping) else None
    return str(found) if found else None


def missing(*, harness: str, target: str | None, in_slot: bool, cwd: str | None,
            values: Mapping[str, Any], source: str | None,
            slots_here: Callable[[], bool] | None = None,
            env: Mapping[str, str] | None = None) -> Missing | None:
    """The first item a launch fails, or None when its session can recycle itself.
    `in_slot` says the launch runs in a slot (a launch not yet placed: that its target is
    one). `values` are the launch's values; `source` is where `recycle_at_limit` came from
    (None when that was not recorded). `slots_here` stands in for `catalog.slots_here`.
    An `off` the launcher set itself (source `left-out`, where the default `relaunch`
    couldn't act) is named by the item that made it so, when one still fails."""
    value = values.get(RECYCLE_AT_LIMIT)
    if opted_in(value, source):
        return _cannot(harness=harness, target=target, in_slot=in_slot, cwd=cwd,
                       values=values, slots_here=slots_here, env=env)
    if value == "off" and source == LEFT_OUT:
        found = _cannot(harness=harness, target=target, in_slot=in_slot, cwd=cwd,
                        values=values, slots_here=slots_here, env=env)
        if found is not None:
            return found
        said = "holds recycle_at_limit off"
    elif value == "compact":
        said = "recycles at its limit by compacting"
    elif value == "off":
        said = "is set recycle_at_limit off"
    else:
        said = "has no recycle_at_limit"
    return Missing(OPTED_OUT, f"{said}, so it can't relaunch itself",
                   "a launch with recycle_at_limit relaunch can recycle itself")


def _cannot(*, harness: str, target: str | None, in_slot: bool, cwd: str | None,
            values: Mapping[str, Any], slots_here: Callable[[], bool] | None,
            env: Mapping[str, str] | None) -> Missing | None:
    """The first of items 2 to 6 the launch fails, or None."""
    if harness != "claude":
        return Missing(HARNESS, f"is a {harness} launch, and only a Claude session can be "
                                "exited and relaunched", "launch the seat on Claude")
    if not in_slot:
        return Missing(SLOT, f"runs in its {target or 'terminal'}, and only a slot launch "
                             "is relaunched", "launch it in a slot (--target slot)")
    if not (slots_here or catalog.slots_here)():
        return Missing(SLOTS, "runs here, and slots does not run here",
                       "relaunch runs where tmux slots run: Linux, macOS and WSL2")
    if values.get("scratch"):
        problem, hint = catalog.SCRATCH_RECYCLE
        return Missing(SCRATCH, f"works in a scratch folder, and {problem}", hint)
    from . import handoffs

    try:
        handoffs.handoff_dir(str(cwd or "."), notes_dir=values.get("notes_dir") or None,
                             env=env, create=False, create_own=False)
    except handoffs.NoNotesDir as exc:
        return Missing(HANDOFFS, exc.problem.removeprefix("this launch "), exc.hint)
    mark = values.get(WARN_AT)
    if mark not in MARKS:
        return Missing(NO_MARK, f"warns at no mark (warn_at {mark or 'unset'}), so nothing "
                                "tells it when to recycle", "set warn_at to 1x, 1.5x or 2x")
    return None


def missing_of_row(row: Any, *, slots_here: Callable[[], bool] | None = None,
                   env: Mapping[str, str] | None = None) -> Missing | None:
    """`missing` for the launch row `row` (a `store.LaunchRow`)."""
    return missing(harness=row.harness, target=row.target,
                   in_slot=row.target == "slot" and bool(row.slot), cwd=row.cwd,
                   values=row.values, source=source_of(row.values), slots_here=slots_here,
                   env=env)


def holds_by_default(row: Any, *, slots_here: Callable[[], bool] | None = None,
                     env: Mapping[str, str] | None = None) -> bool:
    """Whether the row recycles itself on the `off` its launch never chose (`opted_in`'s
    second clause): `show` says `relaunch (default)` for it."""
    return (row.values.get(RECYCLE_AT_LIMIT) == "off"
            and missing_of_row(row, slots_here=slots_here, env=env) is None)


__all__ = ["HANDOFFS", "HARNESS", "LEFT_OUT", "MARKS", "Missing", "NO_MARK", "OPTED_OUT", "RECYCLE_AT_LIMIT",
           "RELAUNCH", "SCRATCH", "SHIPPED", "SLOT", "SLOTS", "WARN_AT", "holds_by_default",
           "missing", "missing_of_row", "opted_in", "source_of"]
