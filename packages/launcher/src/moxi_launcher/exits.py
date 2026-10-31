"""`moxi launcher exit-session` and the exit log.

    exit_session(session_id)       # exit-session SESSION_ID: exit one launched session
    exits()                        # exits: the exit log, newest first

Anyone with a shell may exit a session the launcher launched; the consent is the launch's
own, the commands it granted turns (`MOXI_TURNS_COMMANDS`), which turns checks at the exit.
Only a harness whose turns session offers `exit` (Claude Code today) is exited.

The steps, in this process (no job: the exit is over before the verb returns):

1. The session (`target.find`): launched, claimed, live, and offering `exit`.
2. The run (`runs`, kind `exit`, mode `verb`), written at once with this process as its
   job. One open run per launch, so the exit loses at insert to any other act on the
   launch, and every other act loses to it while it lasts.
3. The quiet read. Anything holding (a turn, a subagent, a draft, a person, a read that
   failed) ends the run `held` with its word: nothing was done.
4. The exit, through turns, with the turn number the quiet read saw. turns checks the
   consent, idleness and the number again. A refusal that clears by itself ends the run
   `held`; `not-allowed` and `unsupported` end it `not-exited`.
5. For a launch in a slot, Claude's exit dialog, answered through slots after its last
   checks, as a relaunch answers it; a check that fails there leaves the dialog for a
   person (`at-dialog`). Outside a slot the dialog can't be answered: unless turns saw the
   harness exit, the run ends `at-dialog` and a person answers it.

**Every exit writes one row in the exit log**, whatever its verdict: `exited`, `held`,
`at-dialog`, `not-exited`, or `refused`, the last for a refusal met once the launch was
found (unclaimed, changed, not live, no exit offered, another run open), which has no run.
A session no launch holds is refused `not-launched` and logged nowhere. Exit codes: 0
exited; 1 at-dialog, not-exited, or a fault; 3 refused; 75 held.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from moxi import exit as codes
from moxi import proc

from . import catalog, first, job, quiet, relaunch, slots, store, target

#: What each verdict exits with.
EXIT_CODES = {"exited": codes.OK, "held": codes.TEMPFAIL, "at-dialog": codes.ERROR,
              "not-exited": codes.ERROR, "refused": codes.REFUSED}
#: The relaunch steps' outcomes as an exit's: a check that failed at the dialog left it
#: for a person; a slot or pane that went away is not a seen exit.
OUTCOMES = {"exit-pending": "at-dialog", "relaunch-failed": "not-exited"}
#: The verdict an exit's run outcome is logged as; a fault is `not-exited`.
VERDICT_OF = {"error": "not-exited"}

LOOK = relaunch.LOOK
Refused = relaunch.Refused
Result = relaunch.Result


def exit_session(session_id: str, *, dry_run: bool = False,
                 environ: Mapping[str, str] | None = None, turns: Any | None = None,
                 slots_api: Any = LOOK, settings: Mapping[str, Any] | None = None,
                 conn: sqlite3.Connection | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> Result:
    """Exit the launched session `session_id`. Raises a refusal (logged as `refused` once
    the launch is found); returns the verdict otherwise. A dry run takes the checks and
    the quiet read and writes nothing."""
    env = dict(os.environ if environ is None else environ)
    current = settings if settings is not None else relaunch._settings()
    with relaunch._store(conn) as c:
        row = store.resolve(c, session_id)
        if row is None:
            raise target.NotLaunched()
        try:
            api = _slots(slots_api)
            found = target.find(session_id, conn=c, turns=turns, slots_api=api)
        except (catalog.Refused, store.RunOpen) as refused:
            _refused(c, row, refused, dry_run)
            raise
        try:
            return _exit(c, found, api, env, current, dry_run, clock, wall, sleep)
        except (catalog.Refused, store.RunOpen) as refused:
            _refused(c, found.row, refused, dry_run)
            raise
        finally:
            found.close()


def _exit(conn: sqlite3.Connection, found: target.Target, api: Any, env: Mapping[str, str],
          current: Mapping[str, Any], dry_run: bool, clock, wall, sleep) -> Result:
    row, sid = found.row, found.session_id
    if not found.live or found.session is None:
        raise Refused("not-live", f"session {sid} is not live", "nothing to exit")
    try:
        offers = "exit" in found.session.capabilities
    except Exception as error:
        raise target.TurnsFailed(error) from error
    if not offers:
        raise Refused("no-exit", f"the {row.harness} session does not offer exit through "
                                 "turns", "exit it in its own terminal")
    in_slot = row.target == "slot" and bool(row.slot) and _slots_here(api)
    data = {"session_id": sid, "request_id": row.request_id, "slot": row.slot}
    person_s = float(current["relaunch.person_s"])
    if dry_run:
        relaunch._no_open_run(conn, row, api)
        reading = quiet.read(found.session, slot=row.slot if in_slot else None,
                             slots_api=api if in_slot else None, person_s=person_s)
        if not reading.quiet:
            hold = reading.first
            return Result(codes.TEMPFAIL, (f"held: {hold}; nothing would be done now",),
                          data={**data, "outcome": "held", "word": hold.word,
                                "detail": hold.detail})
        how = (f"through its exit dialog in slot {row.slot}" if in_slot
               else "through turns")
        return Result(codes.OK, (f"would exit session {sid} {how}",),
                      data={**data, "outcome": "dry-run"})
    run = store.insert_run(
        conn, kind="exit", mode="verb", request_id=row.request_id, session_id=sid,
        actor=first.actor(env), state="acting", slot=row.slot, pane_id=row.pane_id,
        job=own_job(), gone=lambda j: job.gone(j, slots_api=api))
    ran = ExitRun(conn, run, found, in_slot=in_slot, slots_api=api, settings=current,
                  clock=clock, wall=wall, sleep=sleep)
    ran.act()
    ended = store.get_run(conn, run.run_id)
    verdict = ran.verdict
    lines = (_line(ended, verdict),)
    return Result(EXIT_CODES.get(verdict, codes.ERROR), lines, ended,
                  {**data, "run_id": run.run_id, "outcome": verdict, "word": ended.word,
                   "detail": ended.detail})


class ExitRun(job.RelaunchJob):
    """One exit-session run: the quiet read, then the relaunch's exit and dialog steps,
    in this process. Its outcome is its verdict."""

    kind = "exit"

    def __init__(self, conn: sqlite3.Connection, run: store.Run, found: target.Target, *,
                 in_slot: bool, **seams: Any):
        super().__init__(conn, run.run_id, **seams)
        self.run, self.target, self.row = run, found, found.row
        self.in_slot = in_slot
        # The exit starts at once: there is no wait for a stop to end.
        self.acting = True
        self.verdict = "not-exited"

    def check_stop(self) -> None:
        """An exit in this process has no stop signal to defer: Ctrl-C ends it."""

    def end(self, outcome: str, word: str | None = None, detail: str | None = None,
            **fields: Any) -> None:
        mapped = OUTCOMES.get(outcome, outcome)
        if mapped != outcome and word is None:
            word = outcome
        super().end(mapped, word, detail, **fields)

    def act(self) -> str:
        """Take the run's steps to its end and log its verdict; return the verdict."""
        try:
            self._prune()
            self.steps()
        except job.Ended:
            pass
        except BaseException as error:  # a run never stays open on a fault or a Ctrl-C
            try:
                self.end("error", type(error).__name__, job._words(error))
            except job.Ended:
                pass
            if not isinstance(error, Exception):
                self._log()
                raise
        self._log()
        return self.verdict

    def steps(self) -> None:
        session, row = self.target.session, self.row
        reading = quiet.read(session, slot=row.slot if self.in_slot else None,
                             slots_api=self.slots_api if self.in_slot else None,
                             person_s=float(self.setting("relaunch.person_s")))
        if not reading.quiet:
            hold = reading.first
            self.end("held", hold.word, hold.detail)
        number = reading.turn_number
        if not store.advance_run(self.conn, self.run_id, "acting", leaving=("acting",),
                                 turn_number=number):
            self._ended_under()
        self.guard()
        kind, reason = self.exit(number)
        if self.in_slot:
            self.dialog(number)
            self.end("exited", None, f"session {self.run.session_id} exited")
        if kind == "ran":
            self.end("exited", None, f"session {self.run.session_id} exited")
        said = f"{kind}: {reason}" if reason else kind
        self.end("at-dialog", reason or kind,
                 f"turns asked the session to exit and said {said}; the launch is not in "
                 "a slot, so if Claude's exit dialog is up, a person answers it")

    def _log(self) -> None:
        ended = store.get_run(self.conn, self.run_id)
        if ended is None or ended.open:
            return
        verdict = VERDICT_OF.get(ended.outcome or "", ended.outcome)
        self.verdict = verdict if verdict in store.VERDICTS else "not-exited"
        store.add_exit(self.conn, run_id=self.run_id, request_id=ended.request_id,
                       session_id=ended.session_id, verdict=self.verdict, word=ended.word,
                       detail=ended.detail, slot=ended.slot, pane_id=ended.pane_id)


def own_job() -> str:
    """This process as a run's job: `proc:<pid>@<start time>`, tested with
    `moxi.proc.alive`."""
    me = proc.identity(os.getpid())
    if me is None:
        return f"proc:{os.getpid()}"
    return f"proc:{me.pid}@{me.started:.6f}"


def _refused(conn: sqlite3.Connection, row: store.LaunchRow, refused: BaseException,
             dry_run: bool) -> None:
    """Log a refusal met once the launch was found, with no run."""
    if dry_run:
        return
    code = getattr(refused, "code", None) or "run-open"
    problem = getattr(refused, "problem", None) or str(refused)
    store.add_exit(conn, request_id=row.request_id,
                   session_id=row.newest_session_id or row.session_id or "",
                   verdict="refused", word=code, detail=problem, slot=row.slot,
                   pane_id=row.pane_id)


def _slots(given: Any) -> Any:
    return slots.api() if given is LOOK else given


def _slots_here(api: Any) -> bool:
    if api is None:
        return False
    try:
        return bool(api.supported().ok)
    except Exception:  # noqa: BLE001  (slots that can't say: the dialog can't be answered)
        return False


def _line(run: store.Run, verdict: str) -> str:
    said = f"session {run.session_id}: {verdict}"
    if run.word and run.word != verdict:
        said += f" ({run.word})"
    if run.detail and verdict != "exited":
        said += f": {run.detail}"
    return said


# -- the exit log ---------------------------------------------------------------------------


def exits(*, limit: int | None = None, before: str | datetime | None = None,
          conn: sqlite3.Connection | None = None) -> Result:
    """The exit log, newest first: one line per exit, and `--json`'s rows."""
    try:
        with relaunch._store(conn) as c:
            rows = store.exits_log(c, limit=limit, before=before)
    except target.NotLaunched:
        rows = []
    lines = tuple(exit_line(r) for r in rows) or ("no exits",)
    return Result(codes.OK, lines, data={"exits": [r.as_json() for r in rows]})


def exit_line(row: store.ExitRow) -> str:
    """One line for an exit-log row."""
    said = f"{row.at} {row.verdict}"
    if row.word:
        said += f" ({row.word})"
    said += f" session {row.session_id}, launch {row.request_id}"
    if row.slot:
        said += f", slot {row.slot}"
    if row.run_id:
        said += f", run {row.run_id}"
    if row.detail and row.verdict != "exited":
        said += f": {row.detail}"
    return said


__all__ = ["EXIT_CODES", "OUTCOMES", "VERDICT_OF", "ExitRun", "Refused", "Result",
           "exit_line", "exit_session", "exits", "own_job"]
