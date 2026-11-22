"""Reading the slots: one `tmux list-panes -a` call, joined with the registry in-process.

`read()` runs the one call and returns a `Pane` per pane of the server (an empty list
when no server answers, so every registered slot reads `missing`). `join()` turns the
panes and the registry into a `Listing`: each slot's state, live hold, launchability,
idleness and crashed reading, the live `slot-<n>` sessions with no row, and the drift
between the two.

Each pane also carries the server's pid and start time, so the one call says which server
answers; a server with no session at all is asked with one more call (`read_server`).
With no server answering there is no current server, and no slot reads crashed.

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
          "pane_dead_status", "pane_pid", "default-shell", "pid", "start_time",
          "pane_current_command")
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
    server_pid: int | None = None
    server_start: int | None = None


@dataclass(frozen=True)
class ServerNow:
    """The tmux server that answers now: its pid and start time (epoch seconds, None
    when tmux gave none)."""

    pid: int
    start_time: float | None


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
         pane_index, dead, dead_status, pid, shell, server_pid, server_start,
         command) = parts
        panes.append(Pane(session, session_id, _int(attached) or 0, window_id,
                          _int(window_index) or 0, home == "on", run == "on", pane_id,
                          _int(pane_index) or 0, dead == "1", _int(dead_status),
                          _int(pid), shell, command, _int(server_pid),
                          _int(server_start)))
    return panes


def read(timeout: float = tmux.TIMEOUT) -> list[Pane]:
    """Every pane of the server, in one call; [] when no server answers on the socket.

    Raises `TryAgain` when the call times out, `TmuxError` when it fails otherwise (and
    `settings.NotSetUp`, a `TmuxError`, when tmux's path is not recorded)."""
    return _read(timeout, with_server=False)[0]


def read_server(timeout: float = tmux.TIMEOUT) -> tuple[list[Pane], ServerNow | None]:
    """Every pane of the server and the server that answers, or ([], None) when none
    does. Raises as `read` does."""
    return _read(timeout, with_server=True)


def _read(timeout: float, *, with_server: bool) -> tuple[list[Pane], ServerNow | None]:
    result = tmux.attempt("list-panes", "-a", "-F", FORMAT, timeout=timeout)
    if result.ok:
        found = parse(result.out)
        return found, server_of(found)
    err = result.err.strip()
    if tmux.no_server(err):
        return [], None
    # A server with no session at all has no target for `list-panes -a`, which then
    # fails; only in that case is a second call made, which also says which server it is.
    sessions = tmux.attempt("list-sessions", "-F", "#{session_id}", timeout=timeout)
    if sessions.ok and not sessions.out.strip():
        if not with_server:
            return [], None
        said = tmux.attempt("display-message", "-p", "#{pid} #{start_time}",
                            timeout=timeout)
        words = said.out.split() if said.ok else []
        pid = _int(words[0]) if words else None
        start = _int(words[1]) if len(words) > 1 else None
        return [], None if pid is None else ServerNow(pid, start)
    raise TmuxError(f"tmux list-panes failed (exit {result.code}): {err or 'no message'}")


def server_of(found: list[Pane]) -> ServerNow | None:
    """The server the panes were read from, or None with no pane to say."""
    pane = next((p for p in found if p.server_pid is not None), None)
    if pane is None:
        return None
    return ServerNow(pane.server_pid, pane.server_start)


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
        return Facts(number, MISSING, idle=False, attached=0, run=None, run_windows=0)
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


def slot(number: int, f: Facts, row, now: float,
         server: ServerNow | None = None) -> Slot:
    """A `Slot` from tmux's facts and the registry row (None when unregistered), its
    crashed reading taken against `server`, the server answering now."""
    from . import runs

    hold: Hold | None = row.live_hold(now) if row is not None else None
    last = row.last if row is not None else None
    run = f.run
    return Slot(number=number,
                name=row.name if row is not None else None,
                state=f.state,
                hold=hold,
                launchable=row is not None and launchable(f.state, hold),
                idle=f.idle,
                attached=f.attached,
                label=last.label if last is not None and run is not None else None,
                pane_id=run.pane_id if run else None,
                window_id=run.window_id if run else None,
                pane_pid=run.pid if run and not run.dead else None,
                current_command=run.command if run and not run.dead else None,
                dead_status=run.dead_status if run and run.dead else None,
                last=last,
                created=row.created if row is not None else None,
                crashed=row is not None and runs.crashed(f.state, last, server))


def join(registry: Registry, panes: list[Pane], now: float | None = None,
         server: ServerNow | None = None) -> Listing:
    """The listing of `registry` against the server's `panes`. `server` is the server
    answering now, read from the panes when not given."""
    now = time.time() if now is None else now
    server = server_of(panes) if server is None else server
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
        slots.append(slot(number, f, row, now, server))
        if f.state == MISSING:
            drift.append(Drift(number, "no-session",
                               f"slot {number} is registered but has no session; "
                               f"`moxi slots create {number}` makes it again"))
        if f.run_windows > 1:
            drift.append(Drift(number, "two-run-windows",
                               f"slot {number} has {f.run_windows} run windows"))
    return Listing(ok=True, why="", slots=tuple(slots),
                   unregistered=tuple(unregistered), drift=tuple(drift))


def listing(registry: Registry, now: float | None = None) -> Listing:
    """Read the server once and join it with `registry`. Raises as `read` does."""
    found, server = read_server()
    return join(registry, found, now, server)


__all__ = ["FIELDS", "FORMAT", "Pane", "ServerNow", "Facts", "parse", "read",
           "read_server", "server_of", "facts", "sessions", "slot", "join", "listing"]
