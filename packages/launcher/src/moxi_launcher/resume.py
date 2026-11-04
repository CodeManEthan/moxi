"""`moxi launcher sessions` and `moxi launcher resume`: past sessions, and one launched
again where it left off.

    sessions()                     # sessions: one entry per launch, newest first
    resume(session_id)             # resume SESSION_ID [--slot N]: launch it again, resumed

**The list** is the launch log's, one entry per launch, newest first, each with the id the
launch was last known by (after a clear or a compaction it is not the one it started
with), whether its session is live, and whether it can be resumed, with the reason when it
can't. Live is read from turns first (`find` by that id), then from the launch's own
process (`moxi.proc.alive` on the pid and start time the row recorded). A read that fails
marks only its own entry: its liveness is unread, and it is not offered for resume.

**A resume** launches the row again from its stored values (`api.ReplayRequest`, no
preset read), with the row's own environment rule, `resume_of` naming the launch it
resumes, and the harness told to resume the session: Claude's `--resume <id>`, turns'
Codex host's `--resume <thread id>`. It sends no first turn. It keeps the session id it
resumes, and that id then belongs to the new launch, so every later act on the id finds
the new launch. A Codex launch with a comms name resumes like any other: its replay runs
comms' Codex wrapper with the wrapper's own flags and then `--resume <thread id>`, and the
wrapper binds the name to that thread and continues it. Only a resume passes `--resume`: a
relaunch, a repair and a recycle start a new thread.

It is refused, with nothing written, when:

- no launch holds the id (`not-launched`);
- the launch ran headless (`headless`): there is nothing to resume it into;
- the launch has no session id (`no-session-id`): a Codex launch made without turns'
  host never learned its thread id, so `codex resume` resumes it outside the launcher;
- its harness is no longer one the launcher launches (`not-catalogued`);
- it is a Codex launch with a comms name, and comms' Codex wrapper here can't resume a
  thread (`not-yet`): an older comms starts a new thread at every launch; upgrading
  moxi-comms lets it resume. Whether comms can is read once per list, not once per entry;
- its session is live now (`live`): attach to it instead; liveness is read again at the
  call, whatever the list said;
- its liveness can't be read (`liveness-unread`, exit 1).

The check that the session is not live is made again as the new launch's row is written,
in the same store transaction: while another launch of the session is still starting, or
its process runs, the resume is refused `live` and writes nothing. So two resumes of one
session at once launch it once.

**Where it goes is the original's target.** A launch in this terminal resumes in this
terminal (on Linux and macOS the process becomes the harness, so `resume` does not
return). A slot launch goes back to its slot when that slot is launchable, else to the
lowest launchable one; a slot that went missing is made again first (`create`), and a
slot slots won't make again falls back to the lowest too. `slot` names the slot to use
in place of the original's: a missing one is made first, and any other is passed as it
is, so a slot that is taken is refused by the launch's own check.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import cache
from typing import Any

from moxi import exit as codes
from moxi import proc

from . import catalog, first, presets, relaunch, slots, store, target

LOOK = relaunch.LOOK
Refused = relaunch.Refused
Result = relaunch.Result

#: The live session's hint: where to reach it instead of resuming it.
ATTACH_SLOT = "attach to it instead: moxi slots attach {slot}"
ATTACH_TERMINAL = "it runs in its own terminal; go there instead"


@dataclass(frozen=True)
class Past:
    """One launch in the past-sessions list.

    `session_id` is the id the launch was last known by. `live` is True or False, or None
    when it could not be read (`unread` says why). `resumable` is whether `resume` would
    take it now, `why` the reason when it would not, and `hint` what to do instead.
    `held_by` names a later launch that holds the same session id (a resume of it), which
    is the one a resume of that id acts on."""

    request_id: str
    created_at: str
    harness: str
    target: str
    slot: str | None
    cwd: str
    identity: str | None
    state: str
    session_id: str | None
    live: bool | None
    resumable: bool
    why: str | None = None
    hint: str | None = None
    unread: str | None = None
    held_by: str | None = None
    resume_of: str | None = None
    relaunch_of: str | None = None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


# -- the list ---------------------------------------------------------------------------


def sessions(*, limit: int | None = None, before: str | datetime | None = None,
             turns: Any = LOOK, conn: sqlite3.Connection | None = None) -> Result:
    """The past-sessions list, newest first: one line per launch, and `--json`'s
    entries (`data["sessions"]`). `turns` stands in for turns' API (None: no turns)."""
    api = _turns(turns)
    codex_resume = cache(_codex_resume)
    try:
        with relaunch._store(conn) as c:
            entries = [entry(c, row, api, codex_resume=codex_resume)
                       for row in store.log(c, limit=limit, before=before)]
    except target.NotLaunched:
        entries = []
    lines = tuple(session_line(e) for e in entries) or ("no sessions",)
    return Result(codes.OK, lines, data={"sessions": [e.as_json() for e in entries]})


def entry(conn: sqlite3.Connection, row: store.LaunchRow, turns: Any | None, *,
          codex_resume: Callable[[], bool] | None = None) -> Past:
    """`row`'s entry in the list, its liveness read now. `codex_resume` is as
    `static_refusal`'s."""
    known = store.session_ids(conn, row.request_id)
    newest = known[-1][0] if known else row.session_id
    holder = store.resolve(conn, newest) if newest else None
    held_by = (holder.request_id if holder is not None
               and holder.request_id != row.request_id else None)
    # A later launch's session is not this launch's to read: only its own process is.
    live, unread = liveness(row, None if held_by else newest, turns)
    refused = static_refusal(row, newest, codex_resume=codex_resume)
    if refused is None and held_by:
        refused = Refused("held-by", f"launch {held_by} resumed session {newest} since",
                          f"resume it as that launch's: moxi launcher resume {newest}")
    if refused is None:
        refused = live_refusal(row, newest, live, unread)
    return Past(
        request_id=row.request_id, created_at=row.created_at, harness=row.harness,
        target=row.target, slot=row.slot, cwd=row.cwd, identity=row.identity,
        state=row.state, session_id=newest, live=live, resumable=refused is None,
        why=None if refused is None else refused.problem,
        hint=None if refused is None else refused.hint, unread=unread, held_by=held_by,
        resume_of=row.resume_of, relaunch_of=row.relaunch_of)


def session_line(past: Past) -> str:
    """One line for a past-sessions entry."""
    where = f"slot {past.slot}" if past.target == "slot" and past.slot else past.target
    said = (f"{past.created_at} {past.harness} in {where}, launch {past.request_id}, "
            f"session {past.session_id or '(none)'}")
    said += {True: ", live", False: ", ended", None: ", liveness unread"}[past.live]
    said += ": resumable" if past.resumable else f": not resumable, {past.why}"
    return said


# -- liveness and the refusals -----------------------------------------------------------


def liveness(row: store.LaunchRow, session_id: str | None,
             turns: Any | None) -> tuple[bool | None, str | None]:
    """Whether `row`'s session is live, and, when that can't be read, why: turns' `find`
    by `session_id` first, then the row's own process. A turns read that fails falls back
    to the process; with no process recorded either, liveness is unread (None)."""
    failed = None
    if turns is not None and session_id:
        try:
            session = turns.find(session_id=session_id)
        except Exception as error:  # noqa: BLE001  (a failed read marks only this row)
            failed = f"turns could not be asked: {error}"
        else:
            if session is not None:
                _close(session)
                return True, None
    if row.pid:
        if row.pid_started is None:
            return proc.identity(row.pid) is not None, None
        return proc.alive(proc.ProcId(row.pid, row.pid_started)), None
    if failed is not None:
        return None, failed
    return False, None


def static_refusal(row: store.LaunchRow, session_id: str | None, *,
                   codex_resume: Callable[[], bool] | None = None) -> Refused | None:
    """The refusal a resume of `row` meets whatever its session's state, or None.

    `codex_resume` answers whether comms' Codex wrapper resumes a thread, asked only for
    a Codex launch with a comms name; the list passes one that reads comms once for all
    its entries. None reads it now (`presets.comms_codex_resume`)."""
    if row.harness not in catalog.HARNESSES:
        return Refused("not-catalogued", f"the launcher no longer launches {row.harness}",
                       f"resume it with {row.harness} itself")
    if row.target == "headless":
        return Refused("headless", f"launch {row.request_id} ran headless, so there is "
                                   "nothing to resume it into",
                       "launch it again in a terminal or a slot")
    if not session_id:
        return Refused("no-session-id", f"launch {row.request_id} has no session id to "
                                        "resume",
                       "a Codex launch made without turns' host never learned its thread "
                       "id: use codex resume")
    if (row.harness == "codex" and row.identity and slots.links("comms")
            and not (codex_resume or _codex_resume)()):
        return Refused("not-yet", f"launch {row.request_id} runs Codex as comms name "
                                  f"{row.identity}, and comms' Codex wrapper here can't "
                                  "resume a thread", "upgrade moxi-comms")
    return None


def live_refusal(row: store.LaunchRow, session_id: str | None, live: bool | None,
                 unread: str | None) -> Refused | None:
    """The refusal a live session, or one whose liveness is unread, meets, or None."""
    if live is None:
        return Refused("liveness-unread", f"whether session {session_id} is live can't be "
                                          f"read: {unread}",
                       "check `moxi doctor`, then try again", codes.ERROR)
    if live:
        hint = (ATTACH_SLOT.format(slot=row.slot) if row.target == "slot" and row.slot
                else ATTACH_TERMINAL)
        return Refused("live", f"session {session_id} is live", hint)
    return None


# -- resume -----------------------------------------------------------------------------


def resume(session_id: str, *, slot: int | str | None = None,
           request_id: str | None = None, environ: Mapping[str, str] | None = None,
           turns: Any = LOOK, slots_api: Any = LOOK,
           launch: Callable[..., Any] | None = None,
           warn: Callable[[str], None] | None = None,
           conn: sqlite3.Connection | None = None) -> Result:
    """Launch the session `session_id` again, resumed. Raises a refusal with nothing
    written (`store.RunOpen` while a run on the launch is open and its job runs); returns the new launch's outcome. A launch in this terminal does not return
    on Linux and macOS.

    `slot` is the slot to use in place of the original's; `request_id` the new launch's
    (minted when None), its idempotency key. `turns`, `slots_api` and `launch` stand in
    for turns' and slots' APIs (None: not installed) and `api.launch`."""
    env = dict(os.environ if environ is None else environ)
    with relaunch._store(conn) as c:
        row = store.resolve(c, session_id)
        if row is None:
            raise target.NotLaunched()
        newest = row.newest_session_id or row.session_id
        refused = static_refusal(row, newest)
        if refused is None:
            refused = live_refusal(row, newest, *liveness(row, newest, _turns(turns)))
        if refused is not None:
            raise refused
        # A relaunch, exit or recycle under way owns the session: between its exit and
        # its respawn the session reads not live, and a resume then would run a second.
        slots_found = _slots(slots_api)
        relaunch._no_open_run(c, row, slots_found)
    # The store is closed before the launch: in this terminal the launch never returns.
    chosen = slot
    if row.target == "slot":
        chosen = choose_slot(slots_found, row, slot)
    from . import api

    request = api.ReplayRequest(row, resume_of=row.request_id,
                                request_id=request_id or str(uuid.uuid4()),
                                session_id=newest, actor=first.actor(env), slot=chosen)
    try:
        if launch is None:
            result = api.launch(request, environ=environ, warn=warn)
        else:
            result = launch(request, environ=environ, warn=warn)
    except store.SessionLive as met:
        raise met_refusal(met) from None
    lines = (result.line,)
    if result.outcome is not None and result.outcome.line() != result.line:
        lines += (result.outcome.line(),)
    return Result(result.exit_code, lines, data={
        "outcome": "resumed" if result.exit_code == codes.OK else "failed",
        "state": result.state, "reason": result.reason, "request_id": result.request_id,
        "session_id": result.session_id, "resume_of": row.request_id,
        "target": result.target, "slot": result.slot, "duplicate": result.duplicate})


def met_refusal(met: store.SessionLive) -> Refused:
    """The `live` refusal of a resume that met, as it wrote its row, another launch of
    the session still starting or running (two resumes at once, say)."""
    holder = met.holder
    if met.how == "starting":
        return Refused("live", f"session {met.session_id} is being launched again by launch "
                               f"{holder.request_id}, which is still starting",
                       "wait for it to start, then attach to it")
    hint = (ATTACH_SLOT.format(slot=holder.slot) if holder.target == "slot" and holder.slot
            else ATTACH_TERMINAL)
    return Refused("live", f"session {met.session_id} is live, run by launch "
                           f"{holder.request_id}", hint)


def choose_slot(api: Any | None, row: store.LaunchRow,
                asked: int | str | None) -> int | str | None:
    """The slot a resume of the slot launch `row` asks for: `asked` when given (a
    missing one made first), else the row's own when launchable (a missing one made
    first), else "" (the lowest launchable). With slots not installed or not running
    here, `asked` or the row's own, which the launch then refuses with its words.
    Raises a refusal when slots can't be asked."""
    if api is None or not _running(api):
        return asked if asked is not None else row.slot
    wanted = asked if asked is not None else row.slot
    if wanted is None or not str(wanted).strip():
        return ""
    try:
        found = api.show(wanted)
        if found is None or found.created is None or found.state == "missing":
            number = _number(found, wanted)
            if number is not None and _create(api, number):
                return wanted
            return wanted if asked is not None else ""
    except api.TryAgain as error:
        raise Refused("slots-busy", f"slots did not answer about slot {wanted}: {error}",
                      "try again in a moment", codes.TEMPFAIL) from None
    except api.SlotsError as error:
        if asked is not None:  # the launch's own check gives slots' words for it
            return asked
        raise Refused("slots-failed", f"slots could not show slot {wanted}: {error}",
                      "moxi slots list shows the slots", codes.ERROR) from None
    if asked is not None or found.launchable:
        return wanted
    return ""


def _create(api: Any, number: int) -> bool:
    """Make the missing slot `number` again; False when slots refuses it (taken by a
    session with no registry row, say), which falls back."""
    try:
        api.create([number])
    except api.TryAgain:
        raise
    except api.SlotsError:
        return False
    return True


def _number(found: Any | None, wanted: int | str) -> int | None:
    if found is not None and getattr(found, "number", None) is not None:
        return int(found.number)
    text = str(wanted).strip()
    return int(text) if text.isdigit() else None


def _running(api: Any) -> bool:
    try:
        return bool(api.supported().ok)
    except Exception:  # noqa: BLE001  (slots that can't say: the launch's check says)
        return False


def _codex_resume() -> bool:
    return presets.comms_codex_resume()


def _turns(given: Any) -> Any | None:
    return first.turns_api(quiet=True) if given is LOOK else given


def _slots(given: Any) -> Any | None:
    return slots.api() if given is LOOK else given


def _close(session: Any) -> None:
    try:
        session.close()
    except Exception:  # noqa: BLE001  (closing a read's connection never fails the read)
        pass


__all__ = ["ATTACH_SLOT", "ATTACH_TERMINAL", "Past", "Refused", "Result", "choose_slot",
           "entry", "live_refusal", "liveness", "met_refusal", "resume", "session_line",
           "sessions", "static_refusal"]
