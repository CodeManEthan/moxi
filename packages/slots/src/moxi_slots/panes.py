"""Reading the slots: one `tmux list-panes -a` call, joined with the registry in-process.

`read()` runs the one call and returns a `Pane` per pane of the server (an empty list
when no server answers, so every registered slot reads `missing`). `join()` turns the
panes and the registry into a `Listing`: each slot's state, live hold, launchability and
idleness, the live `slot-<n>` sessions with no row, and the drift between the two.

Drift is reported, never repaired: a registered slot with no session, a `slot-<n>`
session with no row, a slot with two run windows.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import tmux
from .errors import TmuxError
from .model import (ENDED, FREE, MISSING, OCCUPIED, Drift, Hold, Listing, Slot,
                    launchable, number_of)
from .registry import Registry

#: The fields read for every pane, tab-separated. The one free-text field,
#: `pane_current_command`, is last, so a tab in it can't shift the others.
FIELDS = ("session_name", "session_id", "session_attached", "window_id", "window_index",
          "@moxi_slot_home", "@moxi_slot_run", "pane_id", "pane_index", "pane_dead",
          "pane_dead_status", "pane_pid", "default-shell", "pane_current_command")
FORMAT = "\t".join(f"#{{{name}}}" for name in FIELDS)


@dataclass(frozen=True)
class Pane:
    """One pane of the server, as `list-panes -a` gave it."""

    session: str
    session_id: str
    attached: int
    window_id: str
    window_index: int
    home: bool
    run: bool
    pane_id: str
    pane_index: int
    dead: bool
    dead_status: int | None
    pid: int | None
    shell: str
    command: str


def _int(text: str) -> int | None:
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        return None


def parse(out: str) -> list[Pane]:
    """The panes in `list-panes -a -F FORMAT` output; a line that doesn't parse is
    skipped."""
    panes = []
    for line in out.splitlines():
        parts = line.split("\t", len(FIELDS) - 1)
        if len(parts) != len(FIELDS):
            continue
        (session, session_id, attached, window_id, window_index, home, run, pane_id,
         pane_index, dead, dead_status, pid, shell, command) = parts
        panes.append(Pane(session, session_id, _int(attached) or 0, window_id,
                          _int(window_index) or 0, home == "on", run == "on", pane_id,
                          _int(pane_index) or 0, dead == "1", _int(dead_status),
                          _int(pid), shell, command))
    return panes


def read(timeout: float = tmux.TIMEOUT) -> list[Pane]:
    """Every pane of the server, in one call; [] when no server answers on the socket.

    Raises `TryAgain` when the call times out, `TmuxError` when it fails otherwise (and
    `settings.NotSetUp`, a `TmuxError`, when tmux's path is not recorded)."""
    result = tmux.attempt("list-panes", "-a", "-F", FORMAT, timeout=timeout)
    if result.ok:
        return parse(result.out)
    err = result.err.strip()
    if tmux.no_server(err):
        return []
    # A server with no session at all has no target for `list-panes -a`, which then
    # fails; only in that case is a second call made.
    sessions = tmux.attempt("list-sessions", "-F", "#{session_id}", timeout=timeout)
    if sessions.ok and not sessions.out.strip():
        return []
    raise TmuxError(f"tmux list-panes failed (exit {result.code}): {err or 'no message'}")


def _shell_name(path: str) -> str:
    return os.path.basename(path.strip())


def _runs_shell(pane: Pane) -> bool:
    """Whether the pane's foreground program is the shell the pane started with (a login
    shell's leading `-` aside), or the pane has died."""
    if pane.dead:
        return True
    return pane.command.lstrip("-") == _shell_name(pane.shell).lstrip("-")


@dataclass(frozen=True)
class Facts:
    """What tmux says about one `slot-<n>` session."""

    number: int
    state: str
    idle: bool
    attached: int
    run: Pane | None
    run_windows: int


def facts(number: int, panes: list[Pane]) -> Facts:
    """The state and idleness of slot `number` from its session's panes ([] when there is
    no session)."""
    if not panes:
        return Facts(number, MISSING, False, 0, None, 0)
    windows: dict[str, list[Pane]] = {}
    for pane in panes:
        windows.setdefault(pane.window_id, []).append(pane)
    for group in windows.values():
        group.sort(key=lambda p: p.pane_index)
    ordered = sorted(windows.values(), key=lambda g: g[0].window_index)
    runs = [g for g in ordered if g[0].run]
    homes = [g for g in ordered if g[0].home and not g[0].run]
    attached = panes[0].attached
    run = runs[0][0] if runs else None
    if not runs:
        state = FREE
    elif any(not p.dead for g in runs for p in g):
        state = OCCUPIED
        run = next(g[0] for g in runs if not all(p.dead for p in g))
    else:
        state = ENDED
    idle = (attached == 0
            and len(homes) == 1 and len(homes[0]) == 1 and _runs_shell(homes[0][0])
            and len(homes) + len(runs) == len(ordered)
            and all(p.dead for g in runs for p in g))
    return Facts(number, state, idle, attached, run, len(runs))


def sessions(panes: list[Pane]) -> dict[int, list[Pane]]:
    """The panes of each live `slot-<n>` session, by n."""
    found: dict[int, list[Pane]] = {}
    for pane in panes:
        number = number_of(pane.session)
        if number is not None:
            found.setdefault(number, []).append(pane)
    return found


def slot(number: int, f: Facts, row, now: float) -> Slot:
    """A `Slot` from tmux's facts and the registry row (None when unregistered)."""
    hold: Hold | None = row.live_hold(now) if row is not None else None
    last = row.last if row is not None else None
    run = f.run
    return Slot(number=number,
                name=row.name if row is not None else None,
                state=f.state,
                hold=hold,
                launchable=launchable(f.state, hold),
                idle=f.idle,
                attached=f.attached,
                label=last.label if last is not None and run is not None else None,
                pane_id=run.pane_id if run else None,
                window_id=run.window_id if run else None,
                pane_pid=run.pid if run and not run.dead else None,
                current_command=run.command if run and not run.dead else None,
                dead_status=run.dead_status if run and run.dead else None,
                last=last,
                created=row.created if row is not None else None)


def join(registry: Registry, panes: list[Pane], now: float | None = None) -> Listing:
    """The listing of `registry` against the server's `panes`."""
    now = time.time() if now is None else now
    live = sessions(panes)
    slots, unregistered, drift = [], [], []
    for number in sorted(set(registry.rows) | set(live)):
        f = facts(number, live.get(number, []))
        row = registry.rows.get(number)
        if row is None:
            unregistered.append(slot(number, f, None, now))
            drift.append(Drift(number, "no-row",
                               f"session slot-{number} runs with no registry row"))
            continue
        slots.append(slot(number, f, row, now))
        if f.state == MISSING:
            drift.append(Drift(number, "no-session",
                               f"slot {number} is registered but has no session; "
                               f"`moxi slots create {number}` makes it again"))
        if f.run_windows > 1:
            drift.append(Drift(number, "two-run-windows",
                               f"slot {number} has {f.run_windows} run windows"))
    return Listing(True, "", tuple(slots), tuple(unregistered), tuple(drift))


def listing(registry: Registry, now: float | None = None) -> Listing:
    """Read the server once and join it with `registry`. Raises as `read` does."""
    return join(registry, read(), now)


__all__ = ["FIELDS", "FORMAT", "Pane", "Facts", "parse", "read", "facts", "sessions",
           "slot", "join", "listing"]
