"""Finding the session an act on a launched session acts on, and the caller's own.

    find(session_id) -> Target      # a session id, to its launch, its claim and its liveness
    caller() -> Target              # the session this process runs inside (a self act)

Every act of part 2 (relaunch, exit-session, resume, recycle, keep-warm) finds its session
this one way, from the launch log and turns:

1. `store.resolve` gives the launch row holding the id, with the id it was last known by.
   No row: refused `not-launched`.
2. The claim: the turns endpoint that claimed the launch. A row no endpoint claimed is
   refused `unclaimed`: turns never saw its session.
3. turns' `find(session_id=<the newest id>)` gives the live session, or None, and then the
   target is not live. A live session must be served by the endpoint process the claim
   names, else refused `changed`.
4. From the row, the slot and pane, and for a slot launch slots' view of the slot.

**The claim is compared with the process it recorded.** A claim names the endpoint
process, `<pid>@<start time>` (`listeners.endpoint_id`). For Claude that process is a child
of `claude`, so `Session.endpoint_pid` is compared, never `Session.pid` (the harness); for
turns' Codex host the two are one process. The pid must be the one the claim names, and
that process must still be the one that claimed it: `moxi.proc.alive` on the claim's pid
and start time, which allows for a start time read a little apart. Never string equality.

**A claim moves only at the new endpoint's next event.** Claude starts a new endpoint for a
session when its plugins reload, and the new endpoint takes the launch over at its next
session event (a start, a resume, a clear or a compaction), not before. Between the two the
held endpoint is gone and a live one serves the session; `find` refuses that `changed` too,
with `reclaim_pending` set and words that say so, and does not wait: the next event may be
far off. Nothing here writes the claim; only the endpoint itself does, from its own event.

turns and slots are optional links, reached as part 1 reaches them (`first.turns_api`,
`slots.api`), so a test gives fakes on the same seams.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from moxi import exit as exits
from moxi import proc
from moxi import store as core_store

from . import catalog, first, slots, store, targets

#: The shape of a session id: Claude session ids and Codex thread ids are UUIDs, written
#: as five groups of hex digits joined by hyphens.
SESSION_ID_SHAPE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def session_id_shaped(text: str) -> bool:
    """Whether `text` is shaped like a session id (a UUID), so it names a session and
    never a comms identity."""
    return SESSION_ID_SHAPE.fullmatch(text) is not None


class NotLaunched(catalog.Refused):
    """The session was not launched by the launcher, or no session was found."""

    code = "not-launched"

    def __init__(self, problem: str = "this session was not launched by the launcher",
                 hint: str = "act only on a session the launcher launched; "
                             "`moxi launcher log` lists them"):
        super().__init__(None, problem, hint)


class Unclaimed(catalog.Refused):
    """The launch's row was never claimed by a turns endpoint."""

    code = "unclaimed"

    def __init__(self, request_id: str):
        self.request_id = request_id
        super().__init__(None, f"launch {request_id} was never claimed by a turns endpoint; "
                               "turns never saw its session",
                         "a launch made without turns, or one whose session never started, "
                         "can't be acted on")


class Changed(catalog.Refused):
    """The live session is served by an endpoint other than the one the claim names.

    `reclaim_pending` is True when the claimed endpoint is gone and a live one serves the
    launch's session id: the new endpoint takes the launch at its next session event."""

    code = "changed"

    def __init__(self, request_id: str, claimed: str, serving: str | None,
                 reclaim_pending: bool):
        self.request_id, self.claimed, self.serving = request_id, claimed, serving
        self.reclaim_pending = reclaim_pending
        if reclaim_pending:
            problem = (f"the endpoint that claimed launch {request_id} ({claimed}) is gone, "
                       f"and a new one ({serving}) serves its session but has not claimed "
                       "it yet")
            hint = ("the new endpoint claims the launch at the session's next start, "
                    "clear or compaction; try again after one")
        else:
            problem = (f"launch {request_id} was claimed by endpoint {claimed}, but its "
                       f"session is served by {serving or 'an endpoint that names no process'}")
            hint = "the session is not the one the launcher started; nothing was done"
        super().__init__(None, problem, hint)


class NoTurns(catalog.Refused):
    """turns is not installed, or can't be loaded: no session can be found."""

    code = first.NO_TURNS

    def __init__(self):
        super().__init__(None, "finding a launched session needs turns, which is not "
                               "installed or can't be loaded", "install moxi-turns")


class TurnsFailed(catalog.Refused):
    """turns could not be asked for the session."""

    code = "turns-failed"
    exit_code = exits.ERROR

    def __init__(self, error: BaseException):
        self.error = error
        super().__init__(None, f"turns could not be asked for the session: {error}",
                         "check `moxi doctor`, then try again")


@dataclass
class Target:
    """The session an act acts on.

    `row` is the launch row (`newest_session_id` set); `session` is turns' live `Session`,
    or None when `live` is false; `slot` is slots' view of the slot for a slot launch
    (None for another target, or when slots is not installed or has no such slot);
    `pane_id` is the row's. The caller closes `session` (`close()`).
    """

    row: store.LaunchRow
    session: Any | None
    slot: Any | None
    pane_id: str | None
    live: bool

    @property
    def session_id(self) -> str | None:
        """The id the launch was last known by."""
        return self.row.newest_session_id

    def close(self) -> None:
        if self.session is not None:
            self.session.close()


def find(session_id: str, *, conn: sqlite3.Connection | None = None,
         turns: Any | None = None, slots_api: Any | None = None) -> Target:
    """The target of an act on `session_id`. Raises `NotLaunched`, `Unclaimed`, `Changed`,
    `NoTurns`, `TurnsFailed`, or slots' refusals (`targets.SlotsBusy`,
    `targets.SlotsFailed`) when the slot can't be read.

    `conn` is the launch log (opened and closed here when not given); `turns` and
    `slots_api` stand in for turns' and slots' APIs.
    """
    return _with_store(conn, lambda c: _find(c, session_id, None, turns, slots_api))


def caller(*, pid: int | None = None, conn: sqlite3.Connection | None = None,
           turns: Any | None = None, slots_api: Any | None = None) -> Target:
    """The target of a self act: the session this process runs inside, found by turns
    walking up from `pid` (this process by default), then checked as `find` checks it.

    A process inside no session turns serves, and a session nested inside a launched one
    (its id is in no launch's row), are refused `not-launched`.
    """
    turns = turns if turns is not None else _turns()
    try:
        session = turns.find(pid=os.getpid() if pid is None else pid)
    except Exception as error:
        raise TurnsFailed(error) from error
    if session is None:
        raise NotLaunched("no session turns serves was found above this process",
                          "run a self act from the session's own tool shell")
    sid = session.session_id
    if not sid:
        session.close()
        raise NotLaunched("the session above this process has no session id yet")
    try:
        return _with_store(conn, lambda c: _find(c, sid, session, turns, slots_api))
    except BaseException:
        session.close()
        raise


# -- the steps --------------------------------------------------------------------------


def _with_store(conn: sqlite3.Connection | None, run):
    if conn is not None:
        return run(conn)
    try:
        opened = store.connect(create=False)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        raise NotLaunched() from None
    try:
        return run(opened)
    finally:
        opened.close()


def _find(conn: sqlite3.Connection, session_id: str, session: Any | None, turns: Any | None,
          slots_api: Any | None) -> Target:
    row = store.resolve(conn, session_id)
    if row is None:
        raise NotLaunched()
    if not row.endpoint:
        raise Unclaimed(row.request_id)
    if session is None:
        turns = turns if turns is not None else _turns()
        try:
            session = turns.find(session_id=row.newest_session_id)
        except Exception as error:
            raise TurnsFailed(error) from error
    try:
        if session is not None:
            check_claim(row, session)
        slot = _slot(row, slots_api)
    except BaseException:
        if session is not None:
            session.close()
        raise
    return Target(row, session, slot, row.pane_id, session is not None)


def check_claim(row: store.LaunchRow, session: Any) -> None:
    """Refuse `changed` unless `session` is served by the endpoint process `row`'s claim
    names."""
    claimed = row.endpoint or ""
    serving = getattr(session, "endpoint_pid", None)
    if isinstance(serving, int) and claims(claimed, serving):
        return
    serving_id = proc.identity(serving) if isinstance(serving, int) else None
    pending = serving_id is not None and store.endpoint_gone(claimed)
    named = (f"{serving_id.pid}@{serving_id.started:.6f}" if serving_id is not None
             else (str(serving) if serving is not None else None))
    raise Changed(row.request_id, claimed, named, pending)


def claims(endpoint: str, pid: int) -> bool:
    """Whether the claim text `endpoint` names the running process `pid`: the same pid,
    and that process the one that claimed (its start time within `moxi.proc.alive`'s
    tolerance; for a claim named by its pid alone, any process of that pid)."""
    parsed = store.parse_endpoint(endpoint)
    if parsed is None or parsed[0] != pid:
        return False
    return not store.endpoint_gone(endpoint)


def _slot(row: store.LaunchRow, slots_api: Any | None) -> Any | None:
    if row.target != "slot" or not row.slot:
        return None
    api = slots_api if slots_api is not None else slots.api()
    if api is None:
        return None
    try:
        return api.show(row.slot)
    except api.TryAgain as error:
        raise targets.SlotsBusy("slot", f"slots did not answer about slot {row.slot}: "
                                f"{error}", "try again in a moment") from error
    except api.SlotsError as error:
        raise targets.SlotsFailed("slot", f"slots could not show slot {row.slot}: {error}",
                                  "moxi slots list shows the slots") from error


def _turns() -> Any:
    api = first.turns_api(quiet=True)
    if api is None:
        raise NoTurns()
    return api


__all__ = ["SESSION_ID_SHAPE", "Changed", "NoTurns", "NotLaunched", "Target", "TurnsFailed",
           "Unclaimed", "caller", "check_claim", "claims", "find", "session_id_shaped"]
