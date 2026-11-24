"""A launched session's state: its relaunch, recycle and keep-warm, and whether it needs a
look.

    state(session_id) -> SessionState     # any launched session, found as an act finds it
    show(session_id=None) -> Result       # moxi launcher show [SESSION_ID] [--json]

Read from the launch log. **Its live reads are three.** An id no launch holds is asked of
turns, and its live session's `claude` process names the launch (`target.resolve`), so a
session's current id resolves before its launch has learned it. The job test of an open
run shows an open run whose job is gone as `stale`. For a launch that keeps warm, cache's
read of the session's lifetime says when a lifetime has no keep-warm window.

- `relaunch`: the open relaunch run's state (`armed`, `waiting: <word>`, `acting`,
  `stale`), or the last relaunch's outcome (the one that made this launch, when the
  launch has none of its own), with any notice that was refused and a note that was not
  taken.
- `recycle`: the options in force, the last expiry or limit run, a pending compact, and
  when the session was last told to recycle itself at its limit. A `relaunch` launch
  whose session can recycle itself but warns at no mark shows `relaunch (armed, no
  warning)`: its self relaunch is accepted and it is never told.
- `keep_warm`: on or off (`keepwarm.on`: launched with it on, or under cover; off
  wherever keep-warm can't act on this install, `keepwarm.can_act`), the open holds of
  the launch chain by name (wherever it is on, cover included), the last run of nudges,
  and the last verdict.

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
from moxi import modules

from . import keepwarm, relaunch, selfrecycle, slots, store, target
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
    """`on` (`keepwarm.on`: launched with keep-warm on, or under cover, where keep-warm
    can act on this install, `keepwarm.can_act`); the open holds of the launch chain by
    name, listed wherever it is on, cover included; `nudges`, the length of the last run
    of nudges; the last keep-warm run; and `window`, set to `no window: lifetime <n> s`
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
        """What `moxi launcher show` prints."""
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
          caller_env: Mapping[str, str] | None = None,
          turns: Any | None = None) -> SessionState:
    """The state of the launched session `session_id`, its launch found as an act finds
    it (`target.resolve`): by the launch log, else by the `claude` process serving the
    live session of that id. Raises `target.NotLaunched` when neither finds a launch.
    `turns` stands in for turns' API."""
    env = dict(os.environ if caller_env is None else caller_env)
    with relaunch._store(conn) as c:
        row = target.resolve(c, session_id, turns=turns)
        if row is None:
            raise target.NotLaunched()
        return of_launch(c, row, slots_api=slots_api, cache=cache, caller_env=env)


def of_launch(conn: sqlite3.Connection, row: store.LaunchRow, *, slots_api: Any = LOOK,
              cache: Any = LOOK, caller_env: Mapping[str, str] | None = None) -> SessionState:
    """The state of the launch `row` (`newest_session_id` set when it came from
    `target.resolve` or a `target.Target`)."""
    from . import job

    env = dict(os.environ if caller_env is None else caller_env)
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
    if at_limit == selfrecycle.RELAUNCH and _armed_untold(row):
        at_limit = ARMED_UNTOLD
    return RecycleState(
        on_expiry=str(values.get("recycle_on_expiry") or "off"), at_limit=at_limit,
        limit_at=_limit_mark(row, at_limit=at_limit), last=last,
        pending_compact=pending[1] if pending else None, told_at=told[-1] if told else None,
        pending_dropped_at=dropped[-1] if dropped else None)


#: The `at limit` of a `relaunch` launch whose session can recycle itself but warns at no
#: mark: its self relaunch is accepted, and nothing tells it when.
ARMED_UNTOLD = "relaunch (armed, no warning)"


def _armed_untold(row: store.LaunchRow) -> bool:
    """Whether the launch `row` is armed to relaunch itself and never told
    (`selfrecycle.missing_of_row` holds, but not with `told`)."""
    if selfrecycle.missing_of_row(row) is not None:
        return False
    told = selfrecycle.missing_of_row(row, told=True)
    return told is not None and told.item == selfrecycle.NO_MARK


def _limit_mark(row: store.LaunchRow, *, at_limit: str) -> str | None:
    """The mark a launch recycles at: for a compacting launch its `limit_at` when set;
    else, when it recycles at its limit, the crossing mark stamped on it at launch
    (usage's `CROSSING_AT_ENV`, so an unset `limit_at` shows the mark it followed, and a
    relaunch launch the mark it is told at); else None. None too when usage can't be
    loaded here to name the stamp."""
    limit_at = row.values.get("limit_at")
    if limit_at and at_limit == "compact":
        return str(limit_at)
    if at_limit in ("off", ARMED_UNTOLD):
        return None
    found = modules.load("usage", interface=1)
    name = getattr(found.api, "CROSSING_AT_ENV", None) if found.ok else None
    stamped = row.env.get(name) if name else None
    return str(stamped) if stamped else None


def _keep_warm(conn: sqlite3.Connection, row: store.LaunchRow, own: Sequence[store.Run],
               lines: Mapping[str, RunLine], sid: str, cache: Any,
               env: Mapping[str, str]) -> KeepWarmState:
    # On only where keep-warm can act: a launch on an install without turns or cache is
    # nudged by nothing, whatever its row says.
    if not (keepwarm.can_act(row) and keepwarm.on(conn, row, sid)):
        return KeepWarmState(on=False)
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
    # Holds count wherever keep-warm is on, under cover included.
    holds = tuple(store.open_holds(conn, row.request_id))
    return KeepWarmState(on=True, holds=holds, nudges=nudges,
                         last=lines[warm[0].run_id] if warm else None, window=window)


# -- the verb ---------------------------------------------------------------------------


def show(session_id: str | None = None, *, pid: int | None = None, turns: Any | None = None,
         slots_api: Any = LOOK, cache: Any = LOOK, conn: sqlite3.Connection | None = None,
         caller_env: Mapping[str, str] | None = None) -> relaunch.Result:
    """`moxi launcher show [SESSION_ID]`: the state of the session named, or with none,
    of the session this process runs in (found through turns). Either is found as an act
    finds it, so a live session's current id resolves before its launch has learned it.
    Exits 0 with the state; raises a refusal for a session no launch holds."""
    env = dict(os.environ if caller_env is None else caller_env)
    with relaunch._store(conn) as c:
        if session_id is None:
            found = target.caller(pid=pid, conn=c, turns=turns,
                                  slots_api=None if slots_api is LOOK else slots_api)
            found.close()
            got = of_launch(c, found.row, slots_api=slots_api, cache=cache, caller_env=env)
        else:
            got = state(session_id, conn=c, slots_api=slots_api, cache=cache,
                        caller_env=env, turns=turns)
    return relaunch.Result(codes.OK, got.lines(),
                           data={**got.as_json(), "outcome": got.word or "ok"})


__all__ = ["ARMED_UNTOLD", "FAILED", "NOTE_NOT_TAKEN", "RECYCLE_MODES", "KeepWarmState", "RelaunchState",
           "RecycleState", "RunLine", "SessionState", "line", "note_not_taken",
           "of_launch", "show", "state"]
