"""A launched session's state: its relaunch, recycle and keep-warm, and whether it needs a
look.

    state(session_id) -> SessionState     # any launched session, from the launch log
    show(session_id=None) -> Result       # moxi launcher state [SESSION_ID] [--json]

Read from the launch log. **Its one live read is the job test of an open run**, so an open
run whose job is gone shows `stale`; and for a launch that keeps warm, cache's read of
the session's lifetime, so a lifetime with no keep-warm window says so.

- `relaunch`: the open relaunch run's state (`armed`, `waiting: <word>`, `acting`,
  `stale`), or the last relaunch's outcome (the one that made this launch, when the
  launch has none of its own), with any notice that was refused and a note that was not
  taken.
- `recycle`: the options in force, the last expiry or limit run, a pending compact, and
  when the session was last told to recycle itself at its limit.
- `keep_warm`: on or off, the open holds of the launch chain by name, the last run of
  nudges, and the last verdict.

**`red` is true exactly when the latest ended run on the launch ended `blocked`, or in an
outcome that needs a look, and no later run on it ended well.** The outcomes that need a
look are the failed ones (`failed`, `not-exited`, `exit-pending` and an exit's
`at-dialog`, `relaunch-failed`, `first-turn-not-sent`, `gone`, `error`), `unconfirmed`
(the act may or may not have run), and a run whose note to its session was not taken
(`note-not-taken`; the run keeps its own outcome, and the note is its event). The run
that made the launch counts as the launch's first. The word shown is `blocked` or
`failed`, never `held`; `why` is the run's word and detail, and `since` when it began.

No status-line file is written: the page and its components read this.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from moxi import exit as codes

from . import keepwarm, relaunch, slots, store, target
from .recycle import LOOK, cache_read

#: The outcomes, beside `blocked`, after which a launch needs a look.
FAILED = ("failed", "not-exited", "exit-pending", "at-dialog", "relaunch-failed",
          "first-turn-not-sent", "gone", "error", "unconfirmed")
#: A run whose note to its session was not taken reads as this.
NOTE_NOT_TAKEN = "note-not-taken"
#: The run modes of recycle's runs.
RECYCLE_MODES = ("expiry", "limit")


@dataclass(frozen=True)
class RunLine:
    """One run as the state shows it: its id, kind and mode, where it stands (`state`:
    the open run's state, `stale`, or `ended`), its outcome, word and detail, and when it
    started and last moved."""

    run_id: str
    kind: str
    mode: str
    state: str
    outcome: str | None
    word: str | None
    detail: str | None
    created_at: str
    updated_at: str

    @property
    def said(self) -> str:
        """Where the run stands, in a few words."""
        said = self.state if self.state != "ended" else (self.outcome or "ended")
        if self.word and self.word != said:
            said += f" ({self.word})"
        return said


@dataclass(frozen=True)
class RelaunchState:
    """`state` is the open relaunch run's (`armed`, `waiting: <word>`, `acting`, `stale`)
    or the last relaunch's outcome, None when there is none; `run` that run; `made_by`
    the run that made this launch; `notices_refused` the recipients of each notice that
    was refused; `note_not_taken` whether its note to the session was not taken."""

    state: str | None
    run: RunLine | None = None
    made_by: str | None = None
    notices_refused: tuple[str, ...] = ()
    note_not_taken: bool = False


@dataclass(frozen=True)
class RecycleState:
    """The options in force; the last expiry or limit run; when a pending compact was
    left; and when the session was last told to recycle itself at its limit."""

    on_expiry: str
    at_limit: str
    limit_at: str | None
    last: RunLine | None = None
    pending_compact: str | None = None
    told_at: str | None = None
    pending_dropped_at: str | None = None


@dataclass(frozen=True)
class KeepWarmState:
    """`on`; the open holds of the launch chain by name; `nudges`, the length of the last
    run of nudges; the last keep-warm run; and `window`, set to `no window: lifetime <n> s`
    when the session's cache lifetime leaves keep-warm no window."""

    on: bool
    holds: tuple[str, ...] = ()
    nudges: int = 0
    last: RunLine | None = None
    window: str | None = None

    @property
    def said(self) -> str:
        if not self.on:
            return "off"
        return f"on ({self.window})" if self.window else "on"


@dataclass(frozen=True)
class SessionState:
    """A launched session's state. `red` says it needs a look; `word` is `blocked` or
    `failed` when it does; `why` is the run's word and detail and `since` when it began;
    `run_id` the run that made it red."""

    session_id: str
    request_id: str
    relaunch: RelaunchState
    recycle: RecycleState
    keep_warm: KeepWarmState
    red: bool
    since: str | None = None
    why: str | None = None
    word: str | None = None
    run_id: str | None = field(default=None)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)

    def lines(self) -> tuple[str, ...]:
        """What `moxi launcher state` prints."""
        head = f"session {self.session_id} (launch {self.request_id}): "
        head += (f"red, {self.word} since {self.since}: {self.why}" if self.red else "ok")
        relaunched = self.relaunch
        line = f"relaunch: {relaunched.state or 'none'}"
        run = relaunched.run
        if run is not None and run.detail and run.state == "ended":
            line += f": {run.detail}"
        if relaunched.note_not_taken:
            line += "; its note to the session was not taken"
        if relaunched.notices_refused:
            line += f"; notice refused by {', '.join(relaunched.notices_refused)}"
        recycled = self.recycle
        rec = f"recycle: on expiry {recycled.on_expiry}, at limit {recycled.at_limit}"
        if recycled.at_limit != "off" and recycled.limit_at:
            rec += f" ({recycled.limit_at})"
        last = recycled.last
        if last is not None:
            rec += f"; last: {last.kind} {last.said} at {last.updated_at}"
        if recycled.pending_compact:
            rec += f"; a compact pending since {recycled.pending_compact}"
        if recycled.told_at:
            rec += f"; told to recycle itself at {recycled.told_at}"
        warm = self.keep_warm
        kw = f"keep warm: {warm.said}"
        if warm.on:
            kw += f"; holds: {', '.join(warm.holds) or 'none'}"
            if warm.nudges:
                kw += f"; nudges in a row: {warm.nudges}"
            if warm.last is not None:
                kw += f"; last: {warm.last.said} at {warm.last.updated_at}"
        return (head, line, rec, kw)


# -- the read ---------------------------------------------------------------------------


def state(session_id: str, *, conn: sqlite3.Connection | None = None,
          slots_api: Any = LOOK, cache: Any = LOOK,
          environ: Mapping[str, str] | None = None) -> SessionState:
    """The state of the launched session `session_id`. Raises `target.NotLaunched` when
    no launch holds it."""
    env = dict(os.environ if environ is None else environ)
    with relaunch._store(conn) as c:
        row = store.resolve(c, session_id)
        if row is None:
            raise target.NotLaunched()
        return of_launch(c, row, slots_api=slots_api, cache=cache, environ=env)


def of_launch(conn: sqlite3.Connection, row: store.LaunchRow, *, slots_api: Any = LOOK,
              cache: Any = LOOK, environ: Mapping[str, str] | None = None) -> SessionState:
    """The state of the launch `row` (`newest_session_id` set when it came from
    `store.resolve`)."""
    from . import job

    env = dict(os.environ if environ is None else environ)
    api = slots.api() if slots_api is LOOK else slots_api
    gone = lambda j: job.gone(j, slots_api=api)  # noqa: E731
    own = store.runs(conn, request_id=row.request_id)
    made = store.made_by(conn, row.request_id)
    runs = own + ([made] if made is not None else [])
    lines = {r.run_id: line(r, gone) for r in runs}
    sid = row.newest_session_id or row.session_id or ""
    red = _red(conn, runs)
    return SessionState(
        session_id=sid, request_id=row.request_id,
        relaunch=_relaunch(conn, own, made, lines),
        recycle=_recycle(conn, row, own, lines),
        keep_warm=_keep_warm(conn, row, own, lines, sid, cache, env),
        red=red is not None, since=red[1] if red else None, why=red[2] if red else None,
        word=red[0] if red else None, run_id=red[3] if red else None)


def line(run: store.Run, gone: Any) -> RunLine:
    """`run` as the state shows it: an open run whose job is gone is `stale`."""
    shown = run.state
    if run.open and store.run_stale(run, gone):
        shown = "stale"
    return RunLine(run.run_id, run.kind, run.mode, shown, run.outcome, run.word, run.detail,
                   run.created_at, run.updated_at)


def note_not_taken(events: Sequence[store.RunEvent]) -> store.RunEvent | None:
    """The run's last `note` event when its note was not taken, else None."""
    notes = [e for e in events if e.kind == "note"]
    if notes and notes[-1].detail.get("verdict") != "taken":
        return notes[-1]
    return None


def _red(conn: sqlite3.Connection,
         runs: Sequence[store.Run]) -> tuple[str, str, str, str] | None:
    """`(word, since, why, run id)` when the latest ended run needs a look, else None.
    `runs` come newest first."""
    ended = [r for r in runs if not r.open]
    if not ended:
        return None
    run = ended[0]
    if run.outcome == "blocked":
        return "blocked", run.created_at, _why(run.word, run.detail), run.run_id
    if run.outcome in FAILED:
        return "failed", run.updated_at, _why(run.word or run.outcome, run.detail), run.run_id
    note = note_not_taken(store.run_events(conn, run.run_id))
    if note is not None:
        said = note.detail.get("why") or note.detail.get("verdict") or ""
        return "failed", note.at, _why(NOTE_NOT_TAKEN, said), run.run_id
    return None


def _why(word: str | None, detail: str | None) -> str:
    return f"{word}: {detail}" if word and detail else (word or detail or "")


def _relaunch(conn: sqlite3.Connection, own: Sequence[store.Run], made: store.Run | None,
              lines: Mapping[str, RunLine]) -> RelaunchState:
    relaunches = [r for r in own if r.kind == "relaunch"]
    run = relaunches[0] if relaunches else made
    if run is None:
        return RelaunchState(None)
    shown = lines[run.run_id]
    events = store.run_events(conn, run.run_id)
    if shown.state == "waiting":
        waits = [e for e in events if e.kind == "wait"]
        said = f"waiting: {waits[-1].detail.get('word')}" if waits else "waiting"
    elif shown.state == "ended":
        said = shown.said
    else:
        said = shown.state
    refused = tuple(", ".join(e.detail.get("to") or ()) or "?" for e in events
                    if e.kind == "notice-refused")
    return RelaunchState(said, shown, made.run_id if made is not None else None, refused,
                         note_not_taken(events) is not None)


def _recycle(conn: sqlite3.Connection, row: store.LaunchRow, own: Sequence[store.Run],
             lines: Mapping[str, RunLine]) -> RecycleState:
    values = row.values
    last = next((lines[r.run_id] for r in own if r.mode in RECYCLE_MODES), None)
    pending = store.pending_compact(conn, row.request_id)
    events = store.events(conn, row.request_id)
    told = [e.at for e in events if e.kind == "recycle-told"]
    dropped = [e.at for e in events if e.kind == "pending-compact-dropped"]
    at_limit = str(values.get("recycle_at_limit") or "off")
    return RecycleState(
        on_expiry=str(values.get("recycle_on_expiry") or "off"), at_limit=at_limit,
        limit_at=str(values["limit_at"]) if values.get("limit_at") else None, last=last,
        pending_compact=pending[1] if pending else None, told_at=told[-1] if told else None,
        pending_dropped_at=dropped[-1] if dropped else None)


def _keep_warm(conn: sqlite3.Connection, row: store.LaunchRow, own: Sequence[store.Run],
               lines: Mapping[str, RunLine], sid: str, cache: Any,
               env: Mapping[str, str]) -> KeepWarmState:
    if row.values.get("keep_warm") != "on":
        return KeepWarmState(False)
    warm = [r for r in own if r.kind == keepwarm.KIND]
    nudged = next((r for r in warm if r.outcome == "nudged"), None)
    nudges = (len(keepwarm.run_of_nudges(conn, row.request_id, nudged.session_id, None))
              if nudged is not None else 0)
    window = None
    if sid:
        clock = cache_read(cache, row, sid, env)
        ttl = getattr(clock, "ttl_s", None) if clock is not None else None
        if keepwarm.no_window(ttl):
            window = f"no window: lifetime {ttl:g} s"
    return KeepWarmState(True, tuple(store.open_holds(conn, row.request_id)), nudges,
                         lines[warm[0].run_id] if warm else None, window)


# -- the verb ---------------------------------------------------------------------------


def show(session_id: str | None = None, *, pid: int | None = None, turns: Any | None = None,
         slots_api: Any = LOOK, cache: Any = LOOK, conn: sqlite3.Connection | None = None,
         environ: Mapping[str, str] | None = None) -> relaunch.Result:
    """`moxi launcher state [SESSION_ID]`: the state of the session named, or with none,
    of the session this process runs in (found through turns). Exits 0 with the state;
    raises a refusal for a session no launch holds."""
    env = dict(os.environ if environ is None else environ)
    with relaunch._store(conn) as c:
        if session_id is None:
            found = target.caller(pid=pid, conn=c, turns=turns,
                                  slots_api=None if slots_api is LOOK else slots_api)
            found.close()
            session_id = found.session_id or found.row.session_id or ""
        got = state(session_id, conn=c, slots_api=slots_api, cache=cache, environ=env)
    return relaunch.Result(codes.OK, got.lines(),
                           data={**got.as_json(), "outcome": got.word or "ok"})


__all__ = ["FAILED", "NOTE_NOT_TAKEN", "RECYCLE_MODES", "KeepWarmState", "RelaunchState",
           "RecycleState", "RunLine", "SessionState", "line", "note_not_taken",
           "of_launch", "show", "state"]
