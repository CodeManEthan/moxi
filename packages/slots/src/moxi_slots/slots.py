"""The slot calls: listing, create, remove, rename, hold, release, attach.

Every change runs under the module lock (`registry.locked()`): read the registry, read
tmux, act, write the registry. Reads take no lock.

The rule for `remove`: it kills only slots that are idle, unoccupied and unheld, unless
`force` names the slot, since a "free" slot can hold your own work. `force` still
refuses an occupied or held slot. `all_free` takes no `force`; it skips and lists every
slot it would not remove.
"""

from __future__ import annotations

import os
import shlex
import time
from collections.abc import Iterable

from . import panes, places, registry, server, tmux
from .errors import Refused, UsageError
from .model import (ENDED, FREE, HIGHEST, HOLD_MAX, HOME_MARK, LOWEST, MISSING, OCCUPIED,
                    Hold, Listing, Removed, Slot, check_holder, check_name, check_number,
                    session_name, session_target, window_target)
from .registry import Registry, Row


def _words_attach(number: int) -> str:
    return f"moxi slots attach {number}"


def resolve(slot: int | str, reg: Registry) -> int:
    """The number a slot argument means: a number (or its digits), or a registered name.
    Raises `UsageError` for a malformed one and `Refused("no-slot")` for an unknown
    name."""
    if isinstance(slot, str) and slot.isdigit():
        slot = int(slot)
    if isinstance(slot, int) and not isinstance(slot, bool):
        return check_number(slot)
    name = check_name(slot)
    number = reg.by_name(name)
    if number is None:
        raise Refused("no-slot", f"no slot is named {name}; `moxi slots list` shows the "
                                 "slots")
    return number


def list_slots() -> Listing:
    """Every slot, from one tmux call joined with the registry. Never raises: when the
    slots can't be read the listing says so (`ok` False, `why`)."""
    from .errors import SlotsError

    try:
        reg = registry.read()
        return panes.listing(reg)
    except SlotsError as exc:
        return Listing(False, str(exc))


def show(slot: int | str) -> Slot | None:
    """One slot, or None when it is neither registered nor a live session. Raises
    `RegistryError`, `TryAgain` or `TmuxError` when the slots can't be read."""
    reg = registry.read()
    try:
        number = resolve(slot, reg)
    except Refused:
        return None
    return panes.listing(reg).find(number)


def _snapshot() -> tuple[Registry, Listing]:
    reg = registry.read()
    return reg, panes.listing(reg)


def _lowest_free(reg: Registry, listing: Listing, taken: Iterable[int] = ()) -> int | None:
    """The lowest number in neither the registry nor live tmux."""
    used = set(reg.rows) | {s.number for s in listing.unregistered} | set(taken)
    return next((n for n in range(LOWEST, HIGHEST + 1) if n not in used), None)


def make_session(number: int) -> None:
    """Make session `slot-<number>` and its home window, in one tmux call: the session
    keeps living with no client (`destroy-unattached off`), and its home window is marked
    and keeps a dead shell (`remain-on-exit on`). Call it under the lock."""
    target = window_target(number)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    tmux.call("new-session", "-d", "-E", "-s", session_name(number), "-c", home,
              ";", "set-option", "-t", target, "destroy-unattached", "off",
              ";", "set-option", "-w", "-t", target, HOME_MARK, "on",
              ";", "set-option", "-w", "-t", target, "remain-on-exit", "on")


def create(numbers: Iterable[int] | None = None, *, count: int = 1,
           name: str | None = None) -> list[int]:
    """Make slots: the given `numbers` (a missing one is made again), or the `count`
    lowest free numbers. `name` names the one slot made. Returns the numbers made.

    Refuses `in-use` for a number whose session is live, `name-taken` for a name another
    slot has, and `no-free-slot` when no number from 1 to 999 is free."""
    wanted = None if numbers is None else sorted({check_number(n) for n in numbers})
    if wanted == []:
        wanted = None
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise UsageError(f"count is a whole number of at least 1, not {count!r}")
    if wanted is not None and count != 1:
        raise UsageError("give slot numbers or a count, not both")
    if name is not None:
        check_name(name)
        if (len(wanted) if wanted else count) != 1:
            raise UsageError("a name names one slot; create one slot to name it")
    server.ensure()
    made: list[int] = []
    with registry.locked():
        reg, listing = _snapshot()
        if name is not None:
            other = reg.by_name(name)
            if other is not None and (wanted is None or other not in wanted):
                raise Refused("name-taken", f"slot {other} is named {name}; pick another "
                                            "name, or `moxi slots rename "
                                            f"{other} --clear` first")
        if wanted is None:
            wanted = []
            for _ in range(count):
                number = _lowest_free(reg, listing, wanted)
                if number is None:
                    raise Refused("no-free-slot",
                                  f"every number from {LOWEST} to {HIGHEST} is a slot; "
                                  "remove one with `moxi slots remove N`")
                wanted.append(number)
        for number in wanted:
            found = listing.find(number)
            if found is not None and found.state != MISSING:
                raise Refused("in-use", f"slot {number} already exists; attach with "
                                        f"`{_words_attach(number)}`")
        try:
            for number in wanted:
                make_session(number)
                made.append(number)
                row = reg.rows.get(number)
                if row is None:
                    reg.rows[number] = Row(time.time())
                else:
                    row.created = time.time()
            if name is not None:
                reg.rows[made[0]].name = name
        finally:
            if made:
                registry.write(reg)
    return made


def _remove_why(found: Slot | None, number: int, *, force: bool,
                holder: str | None) -> tuple[str, str] | None:
    """Why `number` is not removed, as (reason, words), or None when it may be."""
    if found is None:
        return "no-slot", f"there is no slot {number}"
    if found.state == OCCUPIED:
        label = f" ({found.label})" if found.label else ""
        return "occupied", (f"slot {number} runs a launch{label}; end it through its "
                            "session first")
    if found.hold is not None and found.hold.holder != holder:
        return "held", (f"slot {number} is held by {found.hold.holder} until "
                        f"{_when(found.hold.until)}; `moxi slots release {number} "
                        f"--holder {found.hold.holder}` ends the hold")
    if found.state == MISSING or found.idle or force:
        return None
    return "in-use", (f"slot {number} is not idle (a client attached, a window of yours, "
                      f"or a program in its shell); `moxi slots remove {number} --force` "
                      "removes it anyway")


def _when(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def remove(numbers: Iterable[int | str] | None = None, *, all_free: bool = False,
           force: bool = False, holder: str | None = None) -> Removed:
    """Kill slots' sessions and drop their rows.

    With `numbers`, every one must be removable or nothing is removed: the first refusal
    is raised (`no-slot`, `occupied`, `held`, `in-use`). A missing slot only loses its
    row. With `all_free`, every registered slot that is free or ended, idle and unheld is
    removed and the rest are listed in `skipped`. `holder` names a hold the caller has."""
    if all_free and (numbers or force):
        raise UsageError("all_free takes no slot numbers and no force")
    if not all_free and not numbers:
        raise UsageError("name the slots to remove, or all_free")
    if holder is not None:
        check_holder(holder)
    removed: list[int] = []
    skipped: list[tuple[int, str]] = []
    with registry.locked():
        reg, listing = _snapshot()
        if all_free:
            targets = []
            for found in listing.slots:
                if found.state not in (FREE, ENDED):
                    skipped.append((found.number, found.state))
                    continue
                why = _remove_why(found, found.number, force=False, holder=holder)
                if why is not None:
                    skipped.append((found.number, why[0]))
                    continue
                targets.append(found)
        else:
            targets = []
            for slot in numbers:
                number = resolve(slot, reg)
                found = listing.find(number)
                why = _remove_why(found, number, force=force, holder=holder)
                if why is not None:
                    raise Refused(why[0], why[1],
                                  holder=_holder_of(found) if why[0] in ("held",
                                                                         "occupied")
                                  else None)
                if found not in targets:
                    targets.append(found)
        try:
            for found in targets:
                if found.state != MISSING:
                    tmux.call("kill-session", "-t", session_target(found.number))
                reg.rows.pop(found.number, None)
                removed.append(found.number)
        finally:
            if removed:
                registry.write(reg)
    return Removed(tuple(removed), tuple(skipped))


def _holder_of(found: Slot | None):
    """Who has a taken slot: the hold's holder and until, or the last launch's label and
    meta."""
    if found is None:
        return None
    if found.hold is not None:
        return {"holder": found.hold.holder, "until": found.hold.until}
    if found.last is not None:
        return {"label": found.last.label, "meta": found.last.meta}
    return None


def rename(slot: int | str, name: str | None) -> None:
    """Give a registered slot a name, or clear it with None. Refuses `no-slot` for an
    unregistered slot and `name-taken` for a name another slot has."""
    if name is not None:
        check_name(name)
    with registry.locked():
        reg = registry.read()
        number = resolve(slot, reg)
        row = reg.rows.get(number)
        if row is None:
            raise Refused("no-slot", f"there is no slot {number}; `moxi slots create "
                                     f"{number}` makes it")
        if name is not None:
            other = reg.by_name(name)
            if other is not None and other != number:
                raise Refused("name-taken", f"slot {other} is named {name}")
        row.name = name
        registry.write(reg)


def hold(slot: int | str, holder: str, seconds: float) -> Hold:
    """Reserve a slot for `holder` for `seconds` (at most 24 h). A free, ended or occupied
    slot may be held; a missing one is refused (`missing`), and one another holder holds
    is refused (`held`, naming them). The same holder holding again moves `until`."""
    check_holder(holder)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
            or not 0 < seconds <= HOLD_MAX:
        raise UsageError(f"a hold lasts more than 0 and at most {HOLD_MAX} seconds, not "
                         f"{seconds!r}")
    with registry.locked():
        reg, listing = _snapshot()
        number = resolve(slot, reg)
        row = reg.rows.get(number)
        found = listing.find(number)
        if row is None or found is None:
            raise Refused("no-slot", f"there is no slot {number}; `moxi slots create "
                                     f"{number}` makes it")
        if found.state == MISSING:
            raise Refused("missing", f"slot {number} has no session; `moxi slots create "
                                     f"{number}` makes it again")
        now = time.time()
        current = row.live_hold(now)
        if current is not None and current.holder != holder:
            raise Refused("held", f"slot {number} is held by {current.holder} until "
                                  f"{_when(current.until)}",
                          holder={"holder": current.holder, "until": current.until})
        row.hold = Hold(holder, now + float(seconds))
        registry.write(reg)
        return row.hold


def release(slot: int | str, holder: str) -> bool:
    """End `holder`'s hold on a slot. True when a live hold of theirs ended; False when
    they had none (another holder's hold is left alone)."""
    check_holder(holder)
    with registry.locked():
        reg = registry.read()
        number = resolve(slot, reg)
        row = reg.rows.get(number)
        if row is None or row.hold is None or row.hold.holder != holder:
            return False
        live = row.hold.live(time.time())
        row.hold = None
        registry.write(reg)
        return live


def inside_this_server(environ=None) -> bool:
    """Whether this process runs in a pane of this module's server (its `TMUX` names
    the same socket)."""
    environ = os.environ if environ is None else environ
    value = environ.get("TMUX", "")
    if not value or not environ.get("TMUX_PANE"):
        return False
    socket = value.split(",", 1)[0]
    try:
        return os.path.samefile(socket, places.socket())
    except OSError:
        return False


def attach_argv(slot: int | str) -> list[str]:
    """The command that attaches a terminal to a slot: `tmux -S <socket> attach-session
    -t =slot-<n>`, or `switch-client` from a pane of this same server. A slot with no
    session (missing, or a number not yet a slot) is made first; an unknown name is
    refused (`no-slot`)."""
    from .settings import tmux_path

    path = tmux_path()
    reg = registry.read()
    number = resolve(slot, reg)
    found = panes.listing(reg).find(number)
    if found is None or found.state == MISSING:
        try:
            create([number])
        except Refused as exc:
            if exc.reason != "in-use":
                raise
    verb = "switch-client" if inside_this_server() else "attach-session"
    return [path, "-S", places.socket(), verb, "-t", session_target(number)]


def attach_env(argv: list[str], environ=None) -> dict[str, str]:
    """The environment to exec `argv` in: a `switch-client` keeps `TMUX` (it names this
    server, and tells tmux which client to switch); an attach drops it."""
    environ = dict(os.environ if environ is None else environ)
    if "switch-client" in argv:
        return environ
    return tmux.client_env(environ)


def busy() -> list[str]:
    """Why the server can't be stopped now: one line per slot that is occupied or not
    idle. [] when none is, or no server answers. Raises when the slots can't be read."""
    reg = registry.read()
    listing = panes.listing(reg)
    lines = []
    for found in (*listing.slots, *listing.unregistered):
        if found.state == MISSING:
            continue
        if found.state == OCCUPIED:
            label = f" ({found.label})" if found.label else ""
            lines.append(f"slot {found.number} is occupied{label}")
        elif not found.idle:
            lines.append(f"slot {found.number} is not idle")
    return lines


def quoted(argv: list[str]) -> str:
    return shlex.join(argv)


__all__ = ["resolve", "list_slots", "show", "make_session", "create", "remove", "rename",
           "hold", "release", "inside_this_server", "attach_argv", "attach_env", "busy",
           "quoted"]
