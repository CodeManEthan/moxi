"""Recycle: a launched session cleared once idle after its prompt cache has expired, or
compacted shortly before, once per idle stretch, while it is idle and its cache still
warm, each on the launcher's own reads; and told at its context mark that it may relaunch
itself.

    ask_expiry(session_id)       # moxi-launcher-job expiry SESSION, asked by cache's timer
    clear_expired(dry_run=...)   # moxi launcher clear-expired: the ask of each opted-in session
    on_crossing(crossing)        # usage's crossing of the launch's warn_at mark

**Recycle on expiry.** cache's timer asks for a session whose launch stamped
`MOXI_LAUNCHER_RECYCLE_ON_EXPIRY` (`cachetimers`): stamped `clear`, from the expiry on,
every 120 s; stamped `compact`, from 360 s before it until 60 s before, every 60 s. The
timer decides only when to ask, and **the ask branches on the launch row's own
`recycle_on_expiry`, never on which subscription asked**, so a clear and a compact never
both fire on one session. Each branch acts only on its own reads. First, for both: the
launch, launched, with `recycle_on_expiry` set to `clear` or `compact`, and no open run on
it (a stale one, whose job is gone, is ended at the insert).

A clear, in this order:

1. The session (`target.find`): claimed and live.
2. Cache's read of the session: `cold`.
3. The quiet read passes.
4. No named hold of the launch chain is open: a hold says the session waits on work it
   handed out, and a clear would drop what it knows of that work.

A compact, in this order, the cheap reads first:

1. **The window read**: cache's read of the session id asked about, before anything else
   (`compact_due`). It must read `warm`, with a lifetime over `COMPACT_OPEN_S` (360 s; a
   shorter one has no window, `no-window`), an age at least the lifetime less
   `COMPACT_OPEN_S`, where keep-warm's window shuts, and the age plus the last response's
   length (cache's `response_s`; none is `unreadable`) under the lifetime less
   `COMPACT_SHUT_S` (60 s), since cache's age runs from a response's end and the
   lifetime from its request's start. Else `outside`. And the stretch test: **at most one
   compaction in an idle stretch**, which runs from the session's last request that no
   launcher compaction made to its next such request. The fact (`stretch_fact`) is the
   newest compact run of any mode on the launch's chain that ended `compacted` (bound at
   its end), or `unconfirmed` or `error` after its `compact` event (bound at that event
   plus twice `COMPACT_WAIT_S`, turns' compact wait); a run that ended any other way
   compacted nothing and is passed over. A last request at or before the bound is
   `compacted-already`.
2. The session (`target.find`): claimed and live.
3. The quiet read passes.
4. keep-warm's one reading of work (`at_work`, `keepwarm.signal_of`), whether or not
   keep-warm is on for the launch: no live subagent, no open hold of the launch chain and
   no directed agent at work (`at-work`), on lists read whole (`unreadable`).
5. The run (`kind` compact, `mode` expiry, this process its job).
6. **The final read**: cache read again, of the session id `target.find` found, with the
   window, the margin and the stretch test made again on it. One that fails drops the
   run. The slow reads all come before it, so they never eat the margin.

Then turns' `command`, with the turn number the quiet read saw. A compaction's own request
writes no record cache's clock reads, so after one the clock stays at the session's last
request and every later ask on it ends `compacted-already`; the session then expires. A
window missed (busy, held or at work through every ask, or a last response that leaves the
window empty) is not made up after the expiry.

**A clear stops the session's background shells once the session has been idle an
hour**: they never hold a recycle after that. turns gives no time for a turn's end, so
the idle time read is cache's age of the session's last request, which a turn's end
follows within its last answer. A shorter idle, and every compact, leave shells alone.

Each ask ends one way:

- quietly, writing nothing, for the next ask: the cache not cold (a clear), outside the
  window, `no-window`, `compacted-already` or `at-work` (a compact), a turn or a subagent
  at work, another run open, or turns' refusals that clear by themselves (`busy-turn`,
  `stale-turn`, `rate`, `in-flight`, `loading`);
- `blocked`, with its word: the quiet read's `person`, `draft` or `unreadable`; turns'
  `draft` or `busy-agents`; `held-work` (an open hold, a clear only);
- `failed`, with its word: turns' `not-allowed`, `unsupported`, `gone`, or `failed`;
- `unconfirmed`, when turns' answer did not come in time and the command may still run;
- `cleared` or `compacted`.

**A repeated `blocked` updates one run** (`store.end_blocked`), so a draft left in an idle
session is one run whose start is when the block began.

**By hand.** `moxi launcher clear-expired` makes each live launch's own ask, now, for
every launch that recycles on expiry: a compact launch outside its window ends quietly.
With `--dry-run` each ask takes its reads and stops before its first write (a compact's
makes its final read): it ends as a live ask would, except that one that would act ends
quietly as `would-clear` or `would-compact` (saying whether the clear would stop
background shells), and one that would end `blocked` says so and writes no run. A stale
open run is not ended.

**Recycle at limit.** `on_crossing` is the launcher's entry on usage's `moxi.usage.crossings`
group. usage calls it in turns' endpoint once per rise of the main session's context past
the launch's crossing mark (its `warn_at`, stamped only on a launch set to `relaunch`), with
at most 200 ms to answer. The endpoint first takes its launch, claiming it or moving it to
itself by parent and learning the session's id, as its `load` does, so a session whose
endpoint was replaced is still found. Then it reads the store, writes at most one row more,
and returns; it never waits on the session.
It writes an event and returns a line that tells the agent its launch lets it
recycle itself, at its next pause point and only when it is mid-work (`RELAUNCH_LINE`),
once per crossing: each rise past the mark, after a compaction too, is told again, and a
retried call on one crossing gets no second line. It tells only a launch whose session can
recycle itself and warns at a mark (`selfrecycle.missing_of_row` with `told`, the self
arm's test and the mark), so never a scratch launch (`SCRATCH_RECYCLE`). usage appends
the line to its notice on the same call, so the agent reads one line. The launcher never
clears or compacts a session at its context limit: a row from before `compact` was
retired there gets no line and nothing is written.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moxi import exit as codes
from moxi import modules

from . import catalog, listeners, quiet, selfrecycle, slots, store, target

log = logging.getLogger("moxi.launcher.recycle")

#: The expiry row's values that act, each the turns command it gives.
EXPIRY = ("clear", "compact")
#: turns' refusals of a command that clear by themselves: the ask ends quietly.
QUIET_REFUSALS = ("busy-turn", "stale-turn", "rate", "in-flight", "loading")
#: turns' refusals that block the command: `blocked`.
BLOCKED_REFUSALS = ("draft", "busy-agents")
#: turns' refusals that fail it: `failed`.
FAILED_REFUSALS = ("not-allowed", "unsupported", "gone")
#: turns' `Failed` reasons after which the command may still run: `unconfirmed`.
UNCONFIRMED = ("timeout", "unanswered")
#: The quiet read's words that mean work is running: the ask ends quietly.
BUSY_WORDS = ("turn", "subagents")
#: The outcome of each command that ran.
RAN = {"clear": "cleared", "compact": "compacted"}
#: How long a session must have been idle for a clear to stop its background shells.
SHELLS_IDLE_S = 3600.0
#: The turns capability a command's `stop_shells` needs.
STOP_SHELLS = "stop-shells"
#: The crossing handler's wait for the store's write lock, in seconds: under usage's
#: 200 ms bound.
CROSSING_BUSY_S = 0.1
#: A hand-run sweep's wait for the launch log, in seconds.
SWEEP_BUSY_S = 5.0

#: How long before the cache expires the compact window shuts, in seconds, tested on
#: cache's age plus the last response's length (`response_s`). It opens `COMPACT_OPEN_S`
#: before, where keep-warm's window shuts (`keepwarm.SHUT_S`, read when first asked for).
COMPACT_SHUT_S = 60.0
#: turns' wait for a compact's answer, in seconds (turns' `COMMAND_WAIT_S["compact"]`). A
#: compaction whose outcome no one knows is bounded at twice it after its `compact` event.
COMPACT_WAIT_S = 330.0
#: The outcomes of a compact run that may have compacted without saying so, past its
#: `compact` event.
COMPACT_UNKNOWN = ("unconfirmed", "error")

#: The line a `relaunch` crossing returns. usage appends it to its notice, which names the
#: mark, so it names none itself.
RELAUNCH_LINE = ("This launch lets you recycle yourself. Only if you are mid-work with more "
                 "to do: at your next pause point (let running subagents finish, start "
                 "nothing new), write a handoff with the handoff skill, stop your background "
                 "shells, then run `moxi launcher relaunch --self --handoff <file>` as your "
                 "last act and end your turn. If your work is nearly done, finish it and "
                 "stop as usual; don't recycle at the end of a task.")

#: Why a scratch launch may not recycle itself, and what to do instead. A scratch folder
#: has no notes directory to keep a handoff in. A launch that works in one and is given
#: `recycle_at_limit = relaunch` by a flag, the page or its preset is refused wherever its
#: values are checked (`catalog.refuse_scratch_recycle`); one that has it from
#: `[defaults]` holds `off` with a warning (`presets.resolve`). An older such row is
#: refused the self arm and gets no line at its mark, and a replay of one recycles `off`.
SCRATCH_RECYCLE = catalog.SCRATCH_RECYCLE

#: Stands for a seam looked up here.
LOOK: Any = object()


def __getattr__(name: str) -> Any:
    # keepwarm imports this module before it sets its window, so the compact window's
    # opening is read from it when first asked for.
    if name == "COMPACT_OPEN_S":
        from . import keepwarm

        return keepwarm.SHUT_S
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass(frozen=True)
class Verdict:
    """How one ask, or one command, ended: `outcome` None when it ended quietly and wrote
    nothing; `run_id` the run that carries the verdict."""

    outcome: str | None
    word: str | None = None
    detail: str | None = None
    run_id: str | None = None

    @property
    def line(self) -> str:
        said = self.outcome or "nothing done"
        if self.word and self.word != self.outcome:
            said += f" ({self.word})"
        if self.detail:
            said += f": {self.detail}"
        return said


def verdict_of(command: str, result: Any) -> Verdict:
    """turns' answer to a `clear` or `compact` as the run's verdict. A turns error (raised,
    passed here as the exception) reads `failed`."""
    if isinstance(result, BaseException):
        return Verdict("failed", "failed", f"turns could not run {command}: {_words(result)}")
    kind = getattr(result, "KIND", type(result).__name__.lower())
    reason = getattr(result, "reason", None)
    if kind == "ran":
        return _ran(command, result)
    if kind == "refused":
        said = f"turns refused the {command}: {reason}"
        if reason in QUIET_REFUSALS:
            return Verdict(None, reason, said)
        if reason in BLOCKED_REFUSALS:
            return Verdict("blocked", reason, said)
        return Verdict("failed", reason if reason in FAILED_REFUSALS else "failed", said)
    if kind == "failed" and reason in UNCONFIRMED:
        return Verdict("unconfirmed", reason, f"turns' answer to the {command} did not come "
                                              f"({reason}); it may still run")
    return Verdict("failed", "failed", f"the {command} did not run: {reason or kind}")


def _ran(command: str, result: Any) -> Verdict:
    outcome = RAN[command]
    said = f"the session {'cleared' if command == 'clear' else 'compacted'}"
    new_id = getattr(result, "new_session_id", None)
    if new_id:
        said += f"; its new session id is {new_id}"
    shells = getattr(result, "shells", None)
    if shells is None:
        return Verdict(outcome, None, said)
    stopped = list(getattr(shells, "stopped", ()) or ())
    left = [f"{getattr(s, 'id', None) or 'unlisted'} ({getattr(s, 'why', '')})"
            for s in getattr(shells, "left", ()) or ()]
    said += f"; background shells stopped: {', '.join(stopped) or 'none'}"
    if left:
        said += f"; left running: {', '.join(left)}"
    if not getattr(shells, "complete", True):
        return Verdict(outcome, "shells-unvouched",
                       said + "; turns could not vouch for the list it stopped from")
    return Verdict(outcome, "shells-left" if left else None, said)


# -- recycle on expiry -------------------------------------------------------------------


def ask_expiry(session_id: str, *, conn: sqlite3.Connection | None = None,
               turns: Any | None = None, slots_api: Any = LOOK, cache: Any = LOOK,
               comms: Any = LOOK, settings: Mapping[str, Any] | None = None,
               caller_env: Mapping[str, str] | None = None,
               dry_run: bool = False) -> Verdict:
    """One expiry ask of the session `session_id`, to its end. Never raises for what it
    reads; a fault after its run was written ends the run `exc`. `comms` stands in for
    comms' API (None for comms not installed), loaded only when a compact's ask makes the
    directed join. With `dry_run` the ask writes nothing and ends, where it would act, as
    `would-clear` or `would-compact`."""
    env = dict(os.environ if caller_env is None else caller_env)
    try:
        opened = conn if conn is not None else store.connect(create=False)
    except Exception as exc:  # noqa: BLE001  (no launch log: nothing was launched)
        return Verdict(None, "not-launched", f"the launch log could not be opened: "
                                             f"{_words(exc)}")
    try:
        return _expiry(opened, session_id, turns, slots_api, cache, comms, settings, env,
                       dry_run=dry_run)
    finally:
        if conn is None:
            opened.close()


def _expiry(conn: sqlite3.Connection, session_id: str, turns: Any, slots_api: Any,
            cache: Any, comms: Any, settings: Mapping[str, Any] | None,
            env: Mapping[str, str], *, dry_run: bool) -> Verdict:
    from . import exits, job

    row = target.resolve(conn, session_id, turns=turns)
    if row is None:
        return Verdict(None, "not-launched", f"no launch holds session {session_id}")
    command = row.values.get("recycle_on_expiry")
    if command not in EXPIRY:
        return Verdict(None, "off", f"launch {row.request_id} does not recycle on expiry")
    api = slots.api() if slots_api is LOOK else slots_api
    gone = lambda j: job.gone(j, slots_api=api)  # noqa: E731
    current = store.open_run(conn, row.request_id)
    if current is not None and not store.run_stale(current, gone):
        return Verdict(None, "run-open", f"run {current.run_id} is open on the launch")
    if command == "compact":
        return _compact(conn, row, session_id, turns, api, gone, cache, comms, settings,
                        env, dry_run=dry_run)
    try:
        found = target.find(session_id, conn=conn, turns=turns, slots_api=api)
    except catalog.Refused as refused_exc:
        return Verdict(None, getattr(refused_exc, "code", None) or "refused", refused_exc.problem)
    try:
        if not found.live or found.session is None:
            return Verdict(None, "not-live", f"session {session_id} is not live")
        sid = found.session_id or session_id
        clock = cache_read(cache, row, sid, env)
        kind = getattr(clock, "kind", None) if clock is not None else None
        if kind != "cold":
            return Verdict(None, kind or "no-cache",
                           f"the session's cache is {kind or 'not read: cache is absent'}")
        current_settings = settings if settings is not None else _settings()
        reading = _quiet_read(found.session, row, api, current_settings)
        verdict = _held(conn, row, command, reading)
        if verdict is not None and verdict.outcome is None:
            return verdict
        if dry_run:
            return verdict or _would(command, found.session, getattr(clock, "age_s", None))
        _prune(conn, current_settings)
        try:
            run = store.insert_run(
                conn, kind=command, mode="expiry", request_id=row.request_id,
                session_id=sid, by=store.LAUNCHER, state="acting", slot=row.slot,
                pane_id=row.pane_id, turn_number=reading.turn_number, job=exits.own_job(),
                gone=gone)
        except store.RunOpen as open_run_exc:
            return Verdict(None, "run-open", str(open_run_exc))
        if verdict is not None:
            return _blocked(conn, run, verdict)
        age = getattr(clock, "age_s", None)
        return _act(conn, run, found.session, command, reading.turn_number, age)
    finally:
        found.close()


def _compact(conn: sqlite3.Connection, row: store.LaunchRow, session_id: str, turns: Any,
             api: Any, gone: Any, cache: Any, comms: Any,
             settings: Mapping[str, Any] | None, env: Mapping[str, str], *,
             dry_run: bool) -> Verdict:
    """The compact's ask past the launch's open run, in its order: the window read, the
    session, the quiet read, the signal, the run, the final read, the command."""
    from . import exits

    now = time.time()
    shut = compact_due(cache_read(cache, row, session_id, env, now=now), now=now,
                       fact=stretch_fact(conn, row.request_id))
    if shut is not None:
        return shut
    try:
        found = target.find(session_id, conn=conn, turns=turns, slots_api=api)
    except catalog.Refused as refused_exc:
        return Verdict(None, getattr(refused_exc, "code", None) or "refused", refused_exc.problem)
    try:
        if not found.live or found.session is None:
            return Verdict(None, "not-live", f"session {session_id} is not live")
        sid = found.session_id or session_id
        current_settings = settings if settings is not None else _settings()
        reading = _quiet_read(found.session, row, api, current_settings)
        verdict = _held(conn, row, "compact", reading)
        if verdict is None:
            verdict = at_work(conn, row, found.session, sid, turns=turns, comms=comms)
        if verdict is not None and verdict.outcome is None:
            return verdict
        if dry_run:
            if verdict is not None:
                return verdict
            now = time.time()
            clock = cache_read(cache, row, sid, env, now=now)
            return (compact_due(clock, now=now, fact=stretch_fact(conn, row.request_id))
                    or _would("compact", found.session, getattr(clock, "age_s", None)))
        _prune(conn, current_settings)
        try:
            run = store.insert_run(
                conn, kind="compact", mode="expiry", request_id=row.request_id,
                session_id=sid, by=store.LAUNCHER, state="acting", slot=row.slot,
                pane_id=row.pane_id, turn_number=reading.turn_number, job=exits.own_job(),
                gone=gone)
        except store.RunOpen as open_run_exc:
            return Verdict(None, "run-open", str(open_run_exc))
        if verdict is not None:
            return _blocked(conn, run, verdict)
        try:
            now = time.time()
            clock = cache_read(cache, row, sid, env, now=now)
            shut = compact_due(clock, now=now, fact=stretch_fact(conn, row.request_id))
        except BaseException as exc:  # a run never stays open on a fault
            return _faulted(conn, run, exc)
        if shut is not None:
            return end(conn, run, shut)
        return _act(conn, run, found.session, "compact", reading.turn_number,
                    getattr(clock, "age_s", None))
    finally:
        found.close()


def compact_due(clock: Any | None, *, now: float, fact: Stretch | None) -> Verdict | None:
    """None when cache's read `clock`, made at `now` (epoch seconds), lets a compact act;
    else the quiet end that says why not. It acts only on a `warm` read whose lifetime
    (`ttl_s`) is over `COMPACT_OPEN_S`, with its age at least `ttl_s - COMPACT_OPEN_S` and
    its age plus the last response's length (`response_s`) under `ttl_s - COMPACT_SHUT_S`,
    and whose last request (`now` minus its age) is after `fact`'s bound, the newest
    launcher compaction of the launch's chain (`stretch_fact`)."""
    if clock is None:
        return Verdict(None, "no-cache", "the session's cache was not read: cache is absent")
    kind = getattr(clock, "kind", None) or "unknown"
    if kind != "warm":
        return Verdict(None, kind, f"the session's cache is {kind}")
    ttl, age = getattr(clock, "ttl_s", None), getattr(clock, "age_s", None)
    if not isinstance(ttl, (int, float)) or not isinstance(age, (int, float)):
        return Verdict(None, "unknown", "the session's cache is warm, with no age and "
                                        "lifetime to time a compact by")
    from . import keepwarm

    open_s = keepwarm.SHUT_S
    if ttl <= open_s:
        return Verdict(None, "no-window", f"no window: lifetime {ttl:g} s")
    opens, shuts = ttl - open_s, ttl - COMPACT_SHUT_S
    if not opens <= age < shuts:
        return Verdict(None, "outside", f"the cache's age, {age:g} s, is outside "
                                        f"[{opens:g}, {shuts:g}) s")
    response = getattr(clock, "response_s", None)
    if not isinstance(response, (int, float)):
        return Verdict(None, "unreadable", "cache gave no length of the last response "
                                           "(response_s) to time the compact by")
    if age + response >= shuts:
        return Verdict(None, "outside", f"the cache's age, {age:g} s, with the last "
                                        f"response's {response:g} s, is past {shuts:g} s")
    if fact is not None and now - age <= fact.bound:
        return Verdict(None, "compacted-already", f"run {fact.run_id} ({fact.outcome}) "
                                                  "compacted the session, or may have, and "
                                                  "no request has come since")
    return None


@dataclass(frozen=True)
class Stretch:
    """The newest launcher compaction of a launch's chain that compacted, or whose outcome
    no one knows: its run, outcome, and `bound` (epoch seconds), the latest time its own
    request can carry. A later request starts a new idle stretch."""

    run_id: str
    outcome: str
    bound: float


def stretch_fact(conn: sqlite3.Connection, request_id: str) -> Stretch | None:
    """The fact the compact's ask reads for its idle stretch, from the runs of kind
    `compact`, any mode, on the launch's chain (`store.chain`): the newest one that ended
    `compacted`, bound at its end; or that ended in `COMPACT_UNKNOWN` after its `compact`
    event, bound at that event plus twice `COMPACT_WAIT_S`. A run that ended any other
    way, or before its `compact` event, compacted nothing and is passed over. None when
    there is none."""
    newest: tuple[str, Stretch] | None = None
    for rid in store.chain(conn, request_id):
        for run in store.runs(conn, request_id=rid):
            if run.kind != "compact" or run.open:
                continue
            if newest is not None and run.created_at <= newest[0]:
                break
            bound = _bound(conn, run)
            if bound is not None:
                newest = (run.created_at, Stretch(run.run_id, run.outcome or "", bound))
                break
    return newest[1] if newest is not None else None


def _bound(conn: sqlite3.Connection, run: store.Run) -> float | None:
    """The time a compact run's own request is at or before, or None when it compacted
    nothing."""
    if run.outcome == RAN["compact"]:
        return store.parse_time(run.updated_at).timestamp()
    if run.outcome not in COMPACT_UNKNOWN:
        return None
    sent = next((e for e in store.run_events(conn, run.run_id) if e.kind == "compact"), None)
    if sent is None:
        return None
    return store.parse_time(sent.at).timestamp() + 2 * COMPACT_WAIT_S


def at_work(conn: sqlite3.Connection, row: store.LaunchRow, session: Any, sid: str, *,
            turns: Any, comms: Any = LOOK) -> Verdict | None:
    """keep-warm's one reading of work (`keepwarm.signal_of`) for the session id `sid`: its
    own subagents, the open holds of the launch's chain and the agents it directs (the
    join, within `keepwarm.ASK_JOIN_BUDGET_S`), read whether or not keep-warm is on for
    the launch. A quiet `at-work` naming what it found; a quiet `unreadable` when a list
    it rests on was not read whole; None when nothing is at work."""
    from . import keepwarm

    try:
        listed = session.agents()
    except Exception as exc:  # noqa: BLE001  (a list not read is not read whole)
        listed, failed = None, _words(exc)
    else:
        failed = None
    joined = keepwarm._joiner(turns, comms, sid)()
    signal, _ = keepwarm.signal_of(listed, (), (), store.open_holds(conn, row.request_id),
                                   directed=joined)
    if signal.case is not None:
        return Verdict(None, "at-work", signal.detail)
    if not signal.sure:
        unread = keepwarm._unread(listed, failed, joined)
        return Verdict(None, "unreadable", "; ".join(unread) or signal.detail)
    return None


def _quiet_read(session: Any, row: store.LaunchRow, api: Any,
                current: Mapping[str, Any]) -> quiet.Reading:
    in_slot = row.target == "slot" and bool(row.slot) and api is not None
    return quiet.read(session, slot=row.slot if in_slot else None,
                      slots_api=api if in_slot else None,
                      person_s=float(current["relaunch.person_s"]))


def _held(conn: sqlite3.Connection, row: store.LaunchRow, command: str,
          reading: quiet.Reading) -> Verdict | None:
    """What holds the ask after the quiet read: a quiet end for work at hand, `blocked`
    with its word otherwise, or None when nothing holds."""
    first = reading.first
    if first is not None:
        if first.word in BUSY_WORDS:
            return Verdict(None, "busy", str(first))
        return Verdict("blocked", first.word, first.detail)
    if command == "clear":
        holds = store.open_holds(conn, row.request_id)
        if holds:
            return Verdict("blocked", "held-work",
                           f"open holds of the launch chain: {', '.join(holds)}; a clear "
                           "would drop what the session knows of that work")
    return None


def _blocked(conn: sqlite3.Connection, run: store.Run, verdict: Verdict) -> Verdict:
    kept = store.end_blocked(conn, run.run_id, verdict.word or "blocked", verdict.detail)
    if kept == run.run_id:
        store.add_run_event(conn, run.run_id, "ended", {"outcome": "blocked",
                                                        "word": verdict.word,
                                                        "detail": verdict.detail})
    return Verdict("blocked", verdict.word, verdict.detail, kept)


def _act(conn: sqlite3.Connection, run: store.Run, session: Any, command: str,
         number: int | None, age: float | None) -> Verdict:
    """The command, right after the run is seen still open; its verdict ends the run, or
    drops it when it ended quietly."""
    try:
        stop = stops_shells(command, session, age)
        if not store.advance_run(conn, run.run_id, "acting", leaving=("acting",)):
            ended = store.get_run(conn, run.run_id)
            return Verdict(None, "ended-under", f"run {run.run_id} was ended "
                           f"({ended.outcome if ended else 'gone'}) before it acted")
        store.add_run_event(conn, run.run_id, command, {"turn_number": number,
                                                        "stop_shells": stop, "age": age})
        try:
            if stop:
                result = session.command(command, if_turn_number=number, stop_shells=True)
            else:
                result = session.command(command, if_turn_number=number)
        except Exception as exc:  # noqa: BLE001  (turns' error is the verdict)
            result = exc
        verdict = verdict_of(command, result)
        store.add_run_event(conn, run.run_id, "result", {"outcome": verdict.outcome,
                                                         "word": verdict.word,
                                                         "detail": verdict.detail})
        return end(conn, run, verdict)
    except BaseException as exc:  # a run never stays open on a fault
        return _faulted(conn, run, exc)


def _faulted(conn: sqlite3.Connection, run: store.Run, exc: BaseException) -> Verdict:
    """End `run` `error` on the fault `exc`; raise it again unless it is an `Exception`."""
    if store.end_run(conn, run.run_id, "error", word=type(exc).__name__, detail=_words(exc)):
        store.add_run_event(conn, run.run_id, "ended", {"outcome": "error",
                                                        "detail": _words(exc)})
    if not isinstance(exc, Exception):
        raise exc
    log.exception("expiry run %s stopped on a fault", run.run_id)
    return Verdict("error", type(exc).__name__, _words(exc), run.run_id)


def stops_shells(command: str, session: Any, age: float | None) -> bool:
    """Whether `command` on `session` stops its background shells: a clear, after the
    session has been idle `SHELLS_IDLE_S` by cache's `age` (seconds), where turns offers
    the stop."""
    return (command == "clear" and isinstance(age, (int, float)) and age >= SHELLS_IDLE_S
            and STOP_SHELLS in session.capabilities)


def _would(command: str, session: Any, age: float | None) -> Verdict:
    """A dry run's end where a live ask would act."""
    said = f"a run would {command} the session now"
    if stops_shells(command, session, age):
        said += ", stopping its background shells"
    return Verdict(None, f"would-{command}", said)


def end(conn: sqlite3.Connection, run: store.Run, verdict: Verdict) -> Verdict:
    """End `run` with `verdict`: dropped when it ended quietly, one run for a repeated
    `blocked`, else ended with its outcome."""
    if verdict.outcome is None:
        store.drop_run(conn, run.run_id)
        return verdict
    if verdict.outcome == "blocked":
        return _blocked(conn, run, verdict)
    if store.end_run(conn, run.run_id, verdict.outcome, word=verdict.word,
                     detail=verdict.detail):
        store.add_run_event(conn, run.run_id, "ended", {"outcome": verdict.outcome,
                                                        "word": verdict.word,
                                                        "detail": verdict.detail})
    return Verdict(verdict.outcome, verdict.word, verdict.detail, run.run_id)


def cache_read(cache: Any, row: store.LaunchRow, session_id: str,
               env: Mapping[str, str], *, now: float | None = None) -> Any | None:
    """Cache's read of the session, through cache's public API; None without cache. `now`
    (epoch seconds) is the time cache reads the clock at, passed on as cache's own `now`;
    None leaves it to cache."""
    if cache is LOOK:
        cache = None
        if modules.available("cache"):
            found = modules.load("cache", interface=1)
            cache = found.api if found.ok else None
    if cache is None:
        return None
    at = {} if now is None else {"now": now}
    if row.harness == "codex":
        return cache.read(row.harness, session_id, cache_source=codex_sessions(row, env),
                          **at)
    if (root := claude_projects(cache, row)) is not None:
        return cache.read(row.harness, session_id, projects_root=root, **at)
    return cache.read(row.harness, session_id, **at)


def claude_projects(cache: Any, row: store.LaunchRow) -> str | None:
    """The projects root a Claude launch writes its transcripts under, by cache's
    `projects_root` over the launch's own `CLAUDE_CONFIG_DIR` (and `HOME`) as its row
    records them; None when the row records no `CLAUDE_CONFIG_DIR`, so cache reads the
    root it is configured with."""
    recorded = {name: value for name, value in (row.base_env or {}).items()
                if value is not None}
    config = recorded.get("CLAUDE_CONFIG_DIR") or row.env.get("CLAUDE_CONFIG_DIR")
    rule = getattr(cache, "projects_root", None)
    if not config or rule is None:
        return None
    env = {"CLAUDE_CONFIG_DIR": config}
    if recorded.get("HOME"):
        env["HOME"] = recorded["HOME"]
    return rule(env)


def codex_sessions(row: store.LaunchRow, env: Mapping[str, str]) -> str:
    """The Codex home's sessions directory a Codex launch writes to: `CODEX_HOME` from the
    launch's recorded environment, else this process's, else `~/.codex`."""
    home = ((row.base_env or {}).get("CODEX_HOME") or row.env.get("CODEX_HOME")
            or env.get("CODEX_HOME"))
    root = Path(home) if home else Path(env.get("HOME") or Path.home()) / ".codex"
    return str(root / "sessions")


# -- by hand ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Asked:
    """One session a hand-run sweep asked about: its launch, the session id asked by, and
    how the ask ended."""

    request_id: str
    session_id: str
    verdict: Verdict

    def as_json(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "session_id": self.session_id,
                "outcome": self.verdict.outcome, "word": self.verdict.word,
                "detail": self.verdict.detail, "run_id": self.verdict.run_id}


def clear_expired(session: str | None = None, *, dry_run: bool = False,
                  conn: sqlite3.Connection | None = None, turns: Any | None = None,
                  slots_api: Any = LOOK, cache: Any = LOOK, comms: Any = LOOK,
                  settings: Mapping[str, Any] | None = None,
                  caller_env: Mapping[str, str] | None = None) -> Any:
    """`moxi launcher clear-expired [--session ID] [--dry-run]`: the expiry ask
    (`ask_expiry`) of every live launch whose `recycle_on_expiry` acts, by the id it was
    last known by, newest launch first; with `session`, only of the launch last known by
    that id, and with none such a line saying so. Returns the verb's `relaunch.Result`:
    exit 1 when an ask ended other than quietly or done, else 0."""
    from . import live

    with sweep_log(conn) as opened:
        api = slots.api() if slots_api is LOOK and opened is not None else slots_api
        rows = [] if opened is None else [
            r for r in live.live(opened) if r.values.get("recycle_on_expiry") in EXPIRY
            and r.newest_session_id and session in (None, r.newest_session_id)]
        asked = [Asked(r.request_id, r.newest_session_id, ask_expiry(
            r.newest_session_id, conn=opened, turns=turns, slots_api=api, cache=cache,
            comms=comms, settings=settings, caller_env=caller_env, dry_run=dry_run))
            for r in rows]
    none = ("no live launch recycles on expiry" if session is None else
            f"no live launch that recycles on expiry has current session {session}")
    return swept(asked, dry_run=dry_run, none=none)


def swept(asked: Sequence[Asked], *, dry_run: bool, none: str) -> Any:
    """A hand-run sweep's `relaunch.Result`: a line per session, or `none` when there was
    none; exit 1 when an ask ended other than quietly or done (`job.DONE`), else 0."""
    from . import job, relaunch

    lines = tuple(f"session {a.session_id} (launch {a.request_id}): {a.verdict.line}"
                  for a in asked) or (none,)
    bad = any(a.verdict.outcome not in (None, *job.DONE) for a in asked)
    return relaunch.Result(codes.ERROR if bad else codes.OK, lines, data={
        "outcome": "dry-run" if dry_run else "asked", "dry_run": dry_run,
        "sessions": [a.as_json() for a in asked]})


@contextmanager
def sweep_log(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection | None]:
    """`conn`, or the launch log opened for the sweep and closed after it; None when there
    is no launch log."""
    if conn is not None:
        yield conn
        return
    with store.reading(timeout=SWEEP_BUSY_S) as opened:
        yield opened


# -- recycle at limit --------------------------------------------------------------------


def on_crossing(crossing: Any) -> str | None:
    """usage's crossing of a session's `warn_at` mark: the line for the session, or None.
    Reads the store, writes at most its claim, the session's id and one row, never waits
    on the session, and never raises."""
    try:
        conn = store.connect(create=False, timeout=CROSSING_BUSY_S)
    except Exception:  # noqa: BLE001  (no launch log: not a launched session)
        return None
    try:
        return crossing_line(conn, crossing, pid=os.getpid())
    except Exception as exc:  # noqa: BLE001  (a busy store loses this crossing)
        log.info("a crossing was not taken: %s", _words(exc))
        return None
    finally:
        conn.close()


def crossing_line(conn: sqlite3.Connection, crossing: Any, *, pid: int) -> str | None:
    """`on_crossing` over an open launch log, for the endpoint process `pid`.

    The endpoint takes its launch first: the launch its session's stamp names
    (`listeners.REQUEST_ENV`) is claimed, or moved to this endpoint by parent
    (`listeners.claim`), and the crossing's session id is learned for it with source
    `load`. So a session whose endpoint was replaced, and whose new endpoint had no session
    event to claim at, is still told at its mark. A session nested in the launched one
    carries the stamp but never takes the launch, and gets nothing."""
    session_id = str(getattr(crossing, "session_id", "") or "")
    if not session_id:
        return None
    _take_launch(conn, crossing, session_id)
    row = store.resolve(conn, session_id)
    if row is None or not row.endpoint or not target.claims(row.endpoint, pid):
        return None
    if row.values.get("recycle_at_limit") != selfrecycle.RELAUNCH:
        return None
    if selfrecycle.missing_of_row(row, told=True) is not None:
        return None
    key = store.crossing_key(session_id, getattr(crossing, "mark", None),
                             getattr(crossing, "at", None))
    if _told(conn, row.request_id, key):
        return None
    try:
        store.add_event(conn, "recycle-told", {"crossing_key": key},
                        request_id=row.request_id, session_id=session_id)
    except store.Busy:
        pass
    return RELAUNCH_LINE


def _take_launch(conn: sqlite3.Connection, crossing: Any, session_id: str) -> None:
    """Claim the launch the crossing's endpoint carries the stamp of, or move it here by
    parent, and learn `session_id` for it, as the endpoint's `load` does. Only the
    endpoint writes its claim, and this runs in it."""
    if not listeners.event_env(crossing).get(listeners.REQUEST_ENV):
        return
    row = listeners.claim(crossing, conn)
    if row is not None:
        store.learn_session(conn, row.request_id, session_id, "load")


def is_scratch_relaunch(values: Mapping[str, Any]) -> bool:
    """Whether a launch's `values` set a scratch launch to recycle itself
    (`SCRATCH_RECYCLE`)."""
    return bool(values.get("scratch")) and values.get("recycle_at_limit") == "relaunch"


def _told(conn: sqlite3.Connection, request_id: str, key: str) -> bool:
    """Whether the launch was told it may recycle itself at the crossing `key`."""
    return conn.execute(
        "SELECT 1 FROM events WHERE request_id = ? AND kind = 'recycle-told' "
        "AND json_extract(detail_json, '$.crossing_key') = ? LIMIT 1",
        (request_id, key)).fetchone() is not None


# -- helpers ----------------------------------------------------------------------------


def _prune(conn: sqlite3.Connection, current: Mapping[str, Any]) -> None:
    try:
        store.prune(conn, float(current["records.keep_days"]))
    except Exception as exc:  # noqa: BLE001  (retention never stops a run)
        log.warning("the launcher's records were not pruned: %s", exc)


def _settings() -> Mapping[str, Any]:
    from . import settings

    return settings.current()


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__


__all__ = ["BLOCKED_REFUSALS", "BUSY_WORDS", "COMPACT_OPEN_S", "COMPACT_SHUT_S",
           "COMPACT_UNKNOWN", "COMPACT_WAIT_S", "CROSSING_BUSY_S", "EXPIRY",
           "FAILED_REFUSALS", "QUIET_REFUSALS", "RAN", "RELAUNCH_LINE",
           "SCRATCH_RECYCLE", "SHELLS_IDLE_S", "STOP_SHELLS", "SWEEP_BUSY_S", "UNCONFIRMED",
           "Asked", "Stretch", "Verdict", "ask_expiry", "at_work", "cache_read",
           "claude_projects", "clear_expired", "codex_sessions", "compact_due",
           "crossing_line", "end", "is_scratch_relaunch", "on_crossing", "stops_shells",
           "stretch_fact", "sweep_log", "swept", "verdict_of"]
