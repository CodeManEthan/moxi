"""Placing a launch in a slot: `start`, `respawn`, and `wait_ended`.

Under the module lock, `start` reads the registry and tmux, checks the slot takes the
launch, writes the launch file, and then either makes the run window or, when the run
pane has ended, runs `respawn-pane` on it; it records the launch's label and meta as the
slot's last launch, and ends the caller's hold, in that same critical section. So two
starts into one slot cannot both pass the check. It then releases the lock and waits for
the exec verdict (see `launchfile`).

The run window comes from one tmux call: `new-window` at a fresh index, both of its
options (the run mark, and `remain-on-exit on` so the pane stays, dead, when its command
ends), and `select-window`, so an attached person sees it. The window's name is the
label. `respawn` is the strict form relaunch uses: the run pane must exist and have
ended, and its id is kept.

The rule: no slots call kills a live run pane. `respawn` refuses a live one
(`live-pane`), and `respawn-pane` runs without `-k`: a session ends through its own exit.

Neither call needs an attached terminal, so a timer can launch into a slot.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from os import PathLike

from . import launchfile, panes, registry, server, tmux
from .errors import Refused, SlotsError, TmuxError, TryAgain, UsageError
from .model import (ENDED, FREE, MISSING, OCCUPIED, RUN_MARK, Ended, Last, Placed, Slot,
                    check_holder, check_label, check_meta, launchable, session_name)
from .registry import Row
from .slots import _holder_of, _lowest_free, _when, make_session, resolve

#: The `pane-died` hook's index on a run window; the hook signals `wait_ended`.
HOOK = "pane-died[64]"
#: The `wait-for` channel a pane's death signals.
CHANNEL = "moxi-slots-ended-"


def tmux_text(text: str) -> str:
    """`text` for a tmux argument that expands formats (a window's name)."""
    return text.replace("#", "##")


def _checked(argv, env, cwd, label, meta, hold):
    argv, env, cwd = launchfile.check(argv, env, cwd)
    check_label(label)
    meta = check_meta(meta)
    if hold is not None:
        check_holder(hold)
    return argv, env, cwd, meta


def _taken(found: Slot, number: int, hold: str | None, *, occupied: str) -> None:
    """Refuse a slot that is missing, occupied or held by another, naming who has it."""
    if found.state == MISSING:
        raise Refused("missing", f"slot {number} has no session (after a reboot, or with "
                                 f"the server stopped); `moxi slots create {number}` makes "
                                 "it again")
    if found.state == OCCUPIED:
        label = f" ({found.last.label})" if found.last is not None else ""
        raise Refused(occupied, f"slot {number} runs a launch{label}; it ends through its "
                                "own session", holder=_holder_of(found, "occupied"))
    if found.hold is not None and found.hold.holder != hold:
        raise Refused("held", f"slot {number} is held by {found.hold.holder} until "
                              f"{_when(found.hold.until)}",
                      holder=_holder_of(found, "held"))


def _registered(found: Slot | None, number: int, reg) -> Slot:
    if found is None or number not in reg.rows:
        extra = " (a session of that name runs with no registry row)" if found else ""
        raise Refused("no-slot", f"there is no slot {number}{extra}; `moxi slots create "
                                 f"{number}` makes it")
    return found


def _new_window(number: int, seen: list[panes.Pane], script: str, path,
                label: str) -> tuple[str, str]:
    """Make slot `number`'s run window at a fresh index, in one tmux call; return its
    window and pane ids."""
    name = session_name(number)
    index = max((p.window_index for p in seen if p.session == name), default=0) + 1
    target = f"={name}:{index}"
    made = tmux.call("new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}",
                     "-t", target, "-n", tmux_text(label), script, str(path),
                     ";", "set-option", "-w", "-t", target, RUN_MARK, "on",
                     ";", "set-option", "-w", "-t", target, "remain-on-exit", "on",
                     ";", "select-window", "-t", target)
    window_id, pane_id = made.out.split()[:2]
    return window_id, pane_id


def _respawn(number: int, found: Slot, script: str, path, label: str) -> None:
    """Run the trampoline in slot `number`'s ended run pane, keeping its id. A pane that
    is live is refused (`live-pane`); tmux refuses it too, since no `-k` is given."""
    result = tmux.attempt("respawn-pane", "-t", found.pane_id, script, str(path),
                          ";", "rename-window", "-t", found.window_id, tmux_text(label),
                          ";", "select-window", "-t", found.window_id)
    if result.ok:
        return
    err = result.err.strip()
    if "still active" in err:
        raise Refused("live-pane", f"slot {number}'s run pane {found.pane_id} is live; "
                                   "it ends through its own session")
    if tmux.no_server(err):
        raise TryAgain(f"no slots server answers: {err}")
    raise TmuxError(f"tmux respawn-pane failed (exit {result.code}): "
                         f"{err or 'no message'}")


def _place(number: int, found: Slot | None, seen: list[panes.Pane], reg, script: str,
           argv, env, cwd, label: str, meta, hold: str | None) -> tuple:
    """Write the launch file and make (or respawn) the run pane; record the launch and
    end the caller's hold. Runs under the module lock. Returns (path, window, pane)."""
    path = launchfile.write(argv, env, cwd)
    try:
        if found is None or found.state == FREE:
            window_id, pane_id = _new_window(number, seen, script, path, label)
        else:
            _respawn(number, found, script, path, label)
            window_id, pane_id = found.window_id, found.pane_id
    except BaseException:
        launchfile.remove(path)
        raise
    row = reg.rows[number]
    row.last = Last(time.time(), label, meta)
    if hold is not None and row.hold is not None and row.hold.holder == hold:
        row.hold = None
    return path, window_id, pane_id


def _record(reg) -> SlotsError | None:
    """Write the registry once the pane is made; a failure is returned, not raised, so
    the launch file is still seen through its verdict (which removes it) first."""
    try:
        registry.write(reg)
    except SlotsError as exc:
        return exc
    return None


def _verdict(number: int, path, window_id: str, pane_id: str,
             failure: SlotsError | None) -> Placed:
    """Wait for the exec verdict, outside the module lock; then raise a registry
    failure from the critical section, or return where the launch went."""
    verdict = launchfile.wait(path, pane_id)
    if failure is not None:
        raise failure
    return Placed(number, pane_id, window_id, verdict.exec, verdict.line,
                  verdict.dead_status)


def start(slot: int | str | None, argv: Sequence[str], *, env: Mapping[str, str],
          cwd: str | PathLike, label: str, meta: dict | None = None,
          hold: str | None = None, create: bool = False) -> Placed:
    """Run `argv` in a slot with exactly `env` (and the pane's terminal names) in `cwd`;
    return once it has started, or has not.

    `slot=None, create=True`: the lowest launchable slot, or a new one when none is.
    `slot=None` alone: the lowest launchable slot, else refused `no-free-slot`. A number
    or name: that slot only, never created here; refused `no-slot`, `missing`,
    `occupied` (holder: the last launch's label and meta) or `held` (holder: the hold's
    holder and until). `create=True` with a named slot is a usage error: create it
    first. `hold` names the caller's hold, which lets it start in the slot it holds and
    ends with this start. `label` (64 printable characters) names the run window and the
    last launch; `meta` (a JSON object, 4 KiB) is kept with it."""
    if slot is not None and create:
        raise UsageError("create=True makes a slot only for a start that names none; a "
                         "start into a named slot never creates it: create it first "
                         "(`moxi slots create N`)")
    argv, env, cwd, meta = _checked(argv, env, cwd, label, meta, hold)
    script = launchfile.trampoline()
    server.ensure()
    with registry.locked():
        launchfile.sweep()
        reg = registry.read()
        seen = panes.read()
        listing = panes.join(reg, seen)
        made = False
        if slot is None:
            found = next((s for s in listing.slots
                          if launchable(s.state, s.hold, hold)), None)
            if found is not None:
                number = found.number
            elif create:
                number = _lowest_free(reg, listing)
                if number is None:
                    raise Refused("no-free-slot", "every number from 1 to 999 is a slot "
                                                  "and none takes a launch")
                make_session(number)
                reg.rows[number] = Row(time.time())
                made = True
                seen = panes.read()
            else:
                raise Refused("no-free-slot", "no slot is free; `moxi slots create` makes "
                                              "one, or start with create=True")
        else:
            number = resolve(slot, reg)
            found = _registered(listing.find(number), number, reg)
            _taken(found, number, hold, occupied="occupied")
        try:
            path, window_id, pane_id = _place(number, found, seen, reg, script, argv, env,
                                              cwd, label, meta, hold)
        except BaseException:
            if made:
                registry.write(reg)
            raise
        failure = _record(reg)
    return _verdict(number, path, window_id, pane_id, failure)


def respawn(slot: int | str, argv: Sequence[str], *, env: Mapping[str, str],
            cwd: str | PathLike, label: str, meta: dict | None = None,
            hold: str | None = None) -> Placed:
    """Run the next launch in a slot's ended run pane, keeping its pane id (relaunch's
    call). Refuses `no-slot`, `missing`, `live-pane` (the run pane is live; nothing kills
    it) and `held` (another holder's live hold). The rest is as `start`.

    `missing` from `respawn` has two causes, told apart by `Slot.state` (from `show`):
    `missing`, the slot has no session, and `create(n)` makes it again; `free`, the
    session is there but has no run pane to reuse, and `start` places a first launch."""
    if slot is None:
        raise UsageError("respawn names the slot whose run pane it reuses")
    argv, env, cwd, meta = _checked(argv, env, cwd, label, meta, hold)
    script = launchfile.trampoline()
    server.ensure()
    with registry.locked():
        launchfile.sweep()
        reg = registry.read()
        seen = panes.read()
        listing = panes.join(reg, seen)
        number = resolve(slot, reg)
        found = _registered(listing.find(number), number, reg)
        _taken(found, number, hold, occupied="live-pane")
        if found.state != ENDED:
            raise Refused("missing", f"slot {number} has no run pane to reuse; `start` "
                                     "places a launch there")
        path, window_id, pane_id = _place(number, found, seen, reg, script, argv, env,
                                          cwd, label, meta, hold)
        failure = _record(reg)
    return _verdict(number, path, window_id, pane_id, failure)


def wait_ended(slot: int | str, *, pane_id: str, timeout: float) -> Ended:
    """Wait at most `timeout` seconds for slot `slot`'s pane `pane_id` to end.

    The run window's `pane-died` hook signals a `wait-for` channel named for the pane;
    the wait wakes on it, or once a second, and then reads `pane_dead`. The hook only
    wakes it; the read decides. A pane tmux no longer has counts as ended (`gone`).
    Refuses (`UsageError`) a pane that is not in that slot's session."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        raise UsageError(f"timeout is a number of seconds, not {timeout!r}")
    number = resolve(slot, registry.read())
    deadline = time.monotonic() + timeout
    seen = launchfile.read_pane(pane_id)
    if seen.exists and seen.session != session_name(number):
        raise UsageError(f"pane {pane_id} is not slot {number}'s (it is in "
                         f"{seen.session})")
    channel = CHANNEL + pane_id.lstrip("%")
    if seen.exists and not seen.dead:
        tmux.call("set-hook", "-w", "-t", seen.window_id, HOOK, f"wait-for -S {channel}")
    while True:
        seen = launchfile.read_pane(pane_id)
        if not seen.exists:
            return Ended(number, pane_id, True, None, gone=True)
        if seen.dead:
            return Ended(number, pane_id, True, seen.dead_status)
        left = deadline - time.monotonic()
        if left <= 0:
            return Ended(number, pane_id, False)
        try:
            woke = tmux.attempt("wait-for", channel, timeout=min(1.0, left))
        except TryAgain:
            continue
        if not woke.ok:
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


__all__ = ["HOOK", "CHANNEL", "tmux_text", "start", "respawn", "wait_ended"]
