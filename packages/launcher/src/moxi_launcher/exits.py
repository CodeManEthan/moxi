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
4. The background shells. A background shell or Monitor the session runs (`claude`'s
   child shell, `background_shells`) holds the exit, word `shells`, unless the session
   has been idle an hour or more: cache's age of its last request, as a clear on expiry
   reads it (`recycle.SHELLS_IDLE_S`). With cache absent or unread the age is unknown and
   the shells hold. Once the session is that idle, its shells no longer hold, and
   Claude's exit dialog, answered with "Exit and stop tasks", stops them. A child list
   that can't be read holds `unreadable`. Park's exit skips this step (below).
5. The exit, through turns, with the turn number the quiet read saw. turns checks the
   consent, idleness and the number again. A refusal that clears by itself ends the run
   `held`; `not-allowed` and `unsupported` end it `not-exited`.
6. For a launch in a slot, Claude's exit dialog, answered through slots after its last
   checks, as a relaunch answers it; a check that fails there leaves the dialog for a
   person (`at-dialog`). Outside a slot the dialog can't be answered: unless turns saw the
   harness exit, the run ends `at-dialog` and a person answers it.

**Every exit writes one row in the exit log**, whatever its verdict: `exited`, `held`,
`at-dialog`, `not-exited`, or `refused`, the last for a refusal met once the launch was
found (unclaimed, changed, not live, no exit offered, another run open), which has no run.
A session no launch holds is refused `not-launched` and logged nowhere. Exit codes: 0
exited; 1 at-dialog, not-exited, or a fault; 3 refused; 75 held.

**Park's exit** (`park_exit`) is the same run with mode `park` and three differences. It
waits for quiet, reading it every `PARK_POLL_S` until a deadline, instead of taking one
read; a refusal of turns' that clears by itself sends it back to that wait, and a
`run-open` refusal (another act on the launch) is tried again within the same wait, each
try logged. A client's activity does not hold it, at the quiet read or at the exit
dialog: copy mode still does, as do a turn, subagents, a draft and a read that failed.
And it does not read background shells at all: they never hold it, however recently
the session worked, and Claude's exit dialog stops them, since park comes before a
reboot that ends them anyway.
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

from . import catalog, first, job, quiet, recycle, relaunch, slots, store, target

#: What each verdict exits with.
EXIT_CODES = {"exited": codes.OK, "held": codes.TEMPFAIL, "at-dialog": codes.ERROR,
              "not-exited": codes.ERROR, "refused": codes.REFUSED}
#: The relaunch steps' outcomes as an exit's: a check that failed at the dialog left it
#: for a person; a slot or pane that went away is not a seen exit.
OUTCOMES = {"exit-pending": "at-dialog", "relaunch-failed": "not-exited"}
#: The verdict an exit's run outcome is logged as; a fault is `not-exited`.
VERDICT_OF = {"error": "not-exited"}

#: How often park's exit reads quiet again, and tries again a launch another run holds,
#: in seconds.
PARK_POLL_S = 3.0

LOOK = relaunch.LOOK
#: The folder whose snapshot file a Claude Code tool shell sources first: a background
#: shell and a Monitor each run as `<shell> -c "source <...>/shell-snapshots/<file> ..."`.
SHELL_SNAPSHOTS = "/shell-snapshots/"
Refused = relaunch.Refused
Result = relaunch.Result


def exit_session(session_id: str, *, dry_run: bool = False,
                 caller_env: Mapping[str, str] | None = None, turns: Any | None = None,
                 slots_api: Any = LOOK, settings: Mapping[str, Any] | None = None,
                 conn: sqlite3.Connection | None = None, cache: Any = LOOK,
                 read_shells: Callable[[int], list[int]] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, mode: str = "verb",
                 deadline: float | None = None) -> Result:
    """Exit the launched session `session_id`. Raises a refusal (logged as `exc` once
    the launch is found); returns the verdict otherwise. A dry run takes the checks and
    the quiet read and writes nothing. `mode` is the run's (`verb`, or `park` for park's
    exit, which waits for quiet until `deadline` on `clock`). `cache` stands in for
    cache's API and `read_shells` for `background_shells`."""
    env = dict(os.environ if caller_env is None else caller_env)
    current = settings if settings is not None else relaunch._settings()
    with relaunch._store(conn) as c:
        row = store.resolve(c, session_id)
        if row is None:
            raise target.NotLaunched()
        try:
            api = _slots(slots_api)
            found = target.find(session_id, conn=c, turns=turns, slots_api=api)
        except (catalog.Refused, store.RunOpen) as exc:
            _on_refused(c, row, exc, dry_run=dry_run)
            raise
        try:
            shells = Shells(row, env, cache, read_shells or background_shells)
            return _exit(c, found, api, env, current, shells, dry_run=dry_run, clock=clock,
                         wall=wall, sleep=sleep, mode=mode, deadline=deadline)
        except (catalog.Refused, store.RunOpen) as exc:
            _on_refused(c, found.row, exc, dry_run=dry_run)
            raise
        finally:
            found.close()


def _exit(conn: sqlite3.Connection, found: target.Target, api: Any, env: Mapping[str, str],
          current: Mapping[str, Any], shells: Shells, *, dry_run: bool, clock, wall,
          sleep, mode: str = "verb", deadline: float | None = None) -> Result:
    row, sid = found.row, found.session_id
    if not found.live or found.session is None:
        raise Refused("not-live", f"session {sid} is not live", "nothing to exit")
    try:
        offers = "exit" in found.session.capabilities
    except Exception as exc:
        raise target.TurnsFailed(exc) from exc
    if not offers:
        raise Refused("no-exit", f"the {row.harness} session does not offer exit through "
                                 "turns", "exit it in its own terminal")
    in_slot = row.target == "slot" and bool(row.slot) and _slots_here(api)
    data = {"session_id": sid, "request_id": row.request_id, "slot": row.slot}
    person_s = float(current["relaunch.person_s"])
    if dry_run:
        relaunch._no_open_run(conn, row, api)
        reading = quiet.read(found.session, slot=row.slot if in_slot else None,
                             slots_api=api if in_slot else None, person_s=person_s,
                             count_activity=mode != "park")
        hold = _held(reading, shells, found.session, count_shells=mode != "park")
        if hold is not None:
            return Result(codes.TEMPFAIL, (f"held: {hold}; nothing would be done now",),
                          data={**data, "outcome": "held", "word": hold.word,
                                "detail": hold.detail})
        how = (f"through its exit dialog in slot {row.slot}" if in_slot
               else "through turns")
        stop = (f", stopping its {_count(shells.found)} (idle an hour or more)"
                if shells.found else "")
        return Result(codes.OK, (f"would exit session {sid} {how}{stop}",),
                      data={**data, "outcome": "dry-run", "shells": list(shells.found)})
    run = store.insert_run(
        conn, kind="exit", mode=mode, request_id=row.request_id, session_id=sid,
        by=first.actor(env), state="acting", slot=row.slot, pane_id=row.pane_id,
        job=own_job(), gone=lambda j: job.gone(j, slots_api=api))
    ran = ExitRun(conn, run, found, in_slot=in_slot, shells=shells, slots_api=api,
                  settings=current, clock=clock, wall=wall, sleep=sleep, deadline=deadline)
    ran.act()
    ended = store.get_run(conn, run.run_id)
    verdict = ran.verdict
    lines = (_line(ended, verdict),)
    return Result(EXIT_CODES.get(verdict, codes.ERROR), lines, ended,
                  {**data, "run_id": run.run_id, "outcome": verdict, "word": ended.word,
                   "detail": ended.detail})


def _held(reading: quiet.Reading, shells: Shells, session: Any, *,
          count_shells: bool) -> quiet.Hold | None:
    """What holds the exit: the quiet read's first hold, then, when it is quiet and
    `count_shells`, the background shells'. Park leaves the shells out."""
    if not reading.quiet:
        return reading.first
    return shells.hold(session) if count_shells else None


class Shells:
    """The background shells step of one exit: `hold(session)` reads the session's
    shells and says whether they hold the exit; `found` is the pids it read."""

    def __init__(self, row: store.LaunchRow, env: Mapping[str, str], cache: Any,
                 read_shells: Callable[[int], list[int]]):
        self.row, self.env, self.cache, self.read_shells = row, env, cache, read_shells
        self.found: tuple[int, ...] = ()
        self.idle_s: float | None = None

    def hold(self, session: Any) -> quiet.Hold | None:
        """What holds the exit for the session's background shells, or None."""
        pid = getattr(session, "pid", None)
        try:
            if not isinstance(pid, int) or isinstance(pid, bool):
                raise OSError("turns gave no harness pid")
            self.found = tuple(self.read_shells(pid))
        except OSError as exc:
            return quiet.Hold("unreadable", f"the session's background shells can't be "
                                            f"read: {exc}")
        if not self.found:
            return None
        self.idle_s = self._idle_s(session)
        if self.idle_s is not None and self.idle_s >= recycle.SHELLS_IDLE_S:
            return None
        idle = ("for an unknown time (cache did not read it)" if self.idle_s is None
                else f"{int(self.idle_s)} s")
        return quiet.Hold("shells", f"{_count(self.found)} run (pid "
                                    f"{', '.join(map(str, self.found))}) and the session "
                                    f"has been idle {idle}; they stop with an exit only "
                                    "once it has been idle an hour")

    def _idle_s(self, session: Any) -> float | None:
        sid = getattr(session, "session_id", None) or self.row.newest_session_id \
            or self.row.session_id
        try:
            clock = recycle.cache_read(recycle.LOOK if self.cache is LOOK else self.cache,
                                       self.row, sid, self.env)
        except Exception:  # noqa: BLE001  (an unread cache is an unknown idle time)
            return None
        age = getattr(clock, "age_s", None)
        return float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) \
            else None


def background_shells(pid: int) -> list[int]:
    """The pids of `pid`'s children that are Claude Code tool shells: each background
    shell and Monitor of the session `claude` process `pid` runs. Read between turns,
    when no tool call runs, every one is background work. Raises `OSError` when the
    children can't be read."""
    import psutil

    try:
        children = psutil.Process(pid).children()
    except psutil.Error as exc:
        raise OSError(f"process {pid}'s children: {exc}") from exc
    found = []
    for child in children:
        try:
            argv = child.cmdline()
        except psutil.Error:
            continue  # a child that ended while it was read
        if is_tool_shell(argv):
            found.append(child.pid)
    return found


def is_tool_shell(argv: list[str]) -> bool:
    """Whether `argv` is a Claude Code tool shell's: `<shell> -c "source
    <...>/shell-snapshots/<file> ..."`."""
    return (len(argv) >= 3 and argv[1] == "-c" and argv[2].startswith("source ")
            and SHELL_SNAPSHOTS in argv[2])


def _count(pids: tuple[int, ...]) -> str:
    return (f"{len(pids)} background shell or Monitor" if len(pids) == 1
            else f"{len(pids)} background shells or Monitors")


class ExitRun(job.RelaunchJob):
    """One exit-session run: the quiet read and the background shells, then the
    relaunch's exit and dialog steps, in this process. Its outcome is its verdict."""

    kind = "exit"

    def __init__(self, conn: sqlite3.Connection, run: store.Run, found: target.Target, *,
                 in_slot: bool, shells: Shells, deadline: float | None = None,
                 **seams: Any):
        super().__init__(conn, run.run_id, **seams)
        self.run, self.target, self.row = run, found, found.row
        self.in_slot = in_slot
        self.shells = shells
        #: Park's exit waits for quiet until this time on `clock`; the verb's reads once.
        self.parking = run.mode == "park"
        self.deadline = deadline
        self.count_activity = not self.parking
        self.count_shells = not self.parking
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
        except BaseException as exc:  # a run never stays open on a fault or a Ctrl-C
            try:
                self.end("error", type(exc).__name__, job._words(exc))
            except job.Ended:
                pass
            if not isinstance(exc, Exception):
                self._log()
                raise
        self._log()
        return self.verdict

    def steps(self) -> None:
        while True:
            number = self._quiet()
            if not store.advance_run(self.conn, self.run_id, "acting", leaving=("acting",),
                                     turn_number=number):
                self._ended_under()
            self.guard()
            try:
                kind, reason = self.exit(number)
                break
            except job.Again as exc:  # park's exit: back to the wait
                self.event("again", {"word": exc.word, "detail": exc.detail})
                self.sleep(PARK_POLL_S)
        if self.in_slot:
            self.dialog(number)
            self.end("exited", None, f"session {self.run.session_id} exited")
        if kind == "ran":
            self.end("exited", None, f"session {self.run.session_id} exited")
        said = f"{kind}: {reason}" if reason else kind
        self.end("at-dialog", reason or kind,
                 f"turns asked the session to exit and said {said}; the launch is not in "
                 "a slot, so if Claude's exit dialog is up, a person answers it")

    def _quiet(self) -> int | None:
        """The quiet read and the background shells, once for the verb; for park, the
        quiet read alone, again every `PARK_POLL_S` until nothing holds or the deadline.
        Ends the run `held` with what still holds."""
        session, row = self.target.session, self.row
        said: str | None = None
        while True:
            reading = quiet.read(session, slot=row.slot if self.in_slot else None,
                                 slots_api=self.slots_api if self.in_slot else None,
                                 person_s=float(self.setting("relaunch.person_s")),
                                 count_activity=self.count_activity)
            hold = _held(reading, self.shells, session, count_shells=self.count_shells)
            if hold is None:
                if self.shells.found:
                    self.event("shells", {"pids": list(self.shells.found),
                                          "idle_s": self.shells.idle_s})
                return reading.turn_number
            if not self._time_left():
                self.end("held", hold.word, hold.detail)
            if hold.word != said:
                said = hold.word
                self.event("wait", {"word": hold.word, "detail": hold.detail})
            self.sleep(PARK_POLL_S)

    def _time_left(self) -> bool:
        return (self.parking and self.deadline is not None
                and self.clock() + PARK_POLL_S < self.deadline)

    def _transient(self, word: str, detail: str) -> None:
        if self._time_left():
            raise job.Again(word, detail)
        self.end("held", word, detail)

    def _log(self) -> None:
        ended = store.get_run(self.conn, self.run_id)
        if ended is None or ended.open:
            return
        verdict = VERDICT_OF.get(ended.outcome or "", ended.outcome)
        self.verdict = verdict if verdict in store.VERDICTS else "not-exited"
        store.add_exit(self.conn, run_id=self.run_id, request_id=ended.request_id,
                       session_id=ended.session_id, verdict=self.verdict, word=ended.word,
                       detail=ended.detail, slot=ended.slot, pane_id=ended.pane_id)


def park_exit(session_id: str, *, deadline: float,
              clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], None] = time.sleep, **given: Any) -> Result:
    """Exit `session_id` for park: `exit_session` with mode `park`, which waits for quiet
    until `deadline` on `clock`. A `run-open` refusal is tried again every `PARK_POLL_S`
    within the same wait, then raised. `given` is `exit_session`'s other keywords."""
    while True:
        try:
            return exit_session(session_id, mode="park", deadline=deadline, clock=clock,
                                sleep=sleep, **given)
        except store.RunOpen:
            if clock() + PARK_POLL_S >= deadline:
                raise
            sleep(PARK_POLL_S)


def own_job() -> str:
    """This process as a run's job: `proc:<pid>@<start time>`, tested with
    `moxi.proc.alive`."""
    me = proc.identity(os.getpid())
    if me is None:
        return f"proc:{os.getpid()}"
    return f"proc:{me.pid}@{me.started:.6f}"


def _on_refused(conn: sqlite3.Connection, row: store.LaunchRow, refused: BaseException,
             *, dry_run: bool) -> None:
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


__all__ = ["EXIT_CODES", "OUTCOMES", "PARK_POLL_S", "SHELL_SNAPSHOTS", "VERDICT_OF", "ExitRun",
           "Refused", "Result", "Shells", "background_shells", "exit_line", "exit_session",
           "exits", "is_tool_shell", "own_job", "park_exit"]
