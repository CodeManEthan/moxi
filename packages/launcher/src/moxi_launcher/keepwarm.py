"""Keep-warm: a launched session nudged before its prompt cache expires while it waits on
work it handed out, and the named holds that say a director has work out.

    ask(session_id)                  # moxi-launcher-job keep-warm SESSION, asked by cache
    keep_warm(session, dry_run=...)  # moxi launcher keep-warm: the ask, run by hand
    hold(name) / release(name)       # keep-warm hold|release NAME, from the session itself
    holds()                          # keep-warm holds: the open holds of the caller's launch
    open_hold(request_id, name)      # the same, for a caller that knows the launch
    release_hold(request_id, name)
    signal_of(listed, named, dropped, holds, *, directed)   # the signal, from values alone
    would_nudge(conn, row, sid, clock, ...)     # whether the next ask would nudge
    reading(conn, row, sid, clock, ...)         # the same, its first step not passed
    cover_on(conn, row, sid, ...) / cover_off(conn, row, sid, why)   # the press's record
    cover(conn, row, sid) / on(conn, row, sid)  # the record read; keep-warm on for a sid
    launch_on(row)                              # keep-warm on by the launch itself

**Keep-warm acts only on a launch that is on (`on`): every launch of a harness cache times
(Claude Code) that is not headless, with no value read; a headless one launched with
`keep_warm` on; or a session under cover.** A launch recorded with `keep_warm` off that
is not headless is on all the same. cache's timer asks about every session the launcher
started (the stamp `MOXI_LAUNCHER_REQUEST`), from 900 s before its cache expires until
360 s before, every 120 s (`cachetimers`), and an ask about a launch that is not on ends
at its first read. The timer decides only when to ask; each ask reads for itself, in this
order, and stops at the first that answers:

1. The launch: launched, on (`on`), no open run (a stale one, whose job is gone, is ended
   at the insert), and the session claimed and live.
2. Cache's read of the session: its age must lie in `[lifetime - 900, lifetime - 360)`.
   A lifetime of 360 s or less leaves no window, and keep-warm never nudges.
3. turns' facts: a turn is open, nothing (`busy`).
4. The signals. The subagent case: the session's own subagents at work
   (`waits.live_subagents`: `subagent` or `teammate` entries not final, pending, running
   or waiting, no fork of the main loop; a `shell` never counts). The director case: an
   open hold of the launch chain, counted wherever keep-warm is on (`on`, cover
   included), or an agent the session directs at work (`waits.directed`, the join, made
   on every ask that does not nudge for a subagent under its cap (`joins`), cover or
   not, within `ASK_JOIN_BUDGET_S`). Home's row and the press join by the same rule.
   Neither: nothing (`idle`). **An agent a nudge of the current run of nudges named,
   which turns then stops listing or reports `unknown`, is dropped for the rest of that
   run, even if it comes back.**
5. The case and the cap, counted per case (`run_counts`: a nudge's case is the word its
   run ended with), as `chosen` picks: the subagent case while under
   `keep_warm.subagent_cap`; else the director case while under `keep_warm.director_cap`;
   else, when a case held, `blocked`, word `capped`, naming each spent count
   (`subagents 3/3, director 10/10`). Under cover only the nudges of the run made after
   the cover's press count, so a press starts a new count.
6. The nudge: one delivery through turns, only if the session is idle, at the turn number
   step 3 read, refused over a draft. The run is written first (`acting`, this process
   its job), so a second ask, or any other act, loses at the insert; the facts are read
   again, and then the nudge goes.

**A run of nudges is the nudges since the last turn no nudge opened.** Each `nudged` run
keeps the session id and the number of the turn it opened; a later ask starts a new run
when the session's id differs from the last nudge's (a clear starts a new id, with turn
numbers from 0 again), or the id is the same and the turn number is past the nudge's.

**The subagent case comes first; one nudge per ask.** Its texts (`keep_warm.subagent_texts`)
go in order of the case's own count, the live subagents named after each. With directed
agents at work, the director's texts for them (`keep_warm.directing_texts`) go in order,
the last repeating, those at work named after each, and an `Open holds:` line follows
when holds are open. With holds alone, the director's first text (`keep_warm.director_texts`)
is a plain nudge and the second, used for every later one, asks for a recheck of the open
holds, named. Every nudge's first line is `Keep-warm from launcher`.

Each ask ends one way: quietly, writing nothing (none of the above holds, the lists it
rests on were not read whole with no case found (`unreadable`), another run is open, or
turns refused for a reason that clears by itself: `stale-turn`, `rate`, `in-flight`,
`loading`); `nudged`; `blocked` (`capped`, or turns' `draft` or `busy-turn` at the
delivery); `failed` (turns' `not-allowed`, `unsupported`, `gone`, or another refusal); or
`unconfirmed`, when turns could not say whether the nudge went in. **A repeated `blocked`
updates one run** (`store.end_blocked`).

**By hand.** `moxi launcher keep-warm --session ID` makes the same ask of one session, now.
With `--dry-run` it takes the ask's reads and stops before its first write: it ends as a
live ask would, except that one that would nudge ends quietly as `would-nudge`, one that
would end `blocked` at the cap says so and writes no run, and nothing is written for an
agent newly dropped or a cover that would end. A dry run with no `--session` asks about
every live launch keep-warm is on for. A stale open run is not ended.

**Cover.** A press records that keep-warm is on for one running session: cover is the
newest `keep-warm-cover` event of the launch, when it says `on` and names the session id
asked about. A `/clear`'s new id, a relaunch and a resume (each a new launch) start
without it, and an id the session comes back to reads its cover again. **Only the ask
ends a cover, and only on lists read whole**: an ask under cover that finds no live
subagent, no open hold and no directed agent at work writes the `off` event when the
session's own agents list and the directed join were both read whole (`Signal.sure`),
and then ends `idle`. When either was not read whole it writes nothing, keeps the cover,
and with no case ends `unreadable`. An open hold keeps a cover, as work out does.

**Holds.** `keep-warm hold NAME`, run from the director's own session, opens a hold on
its launch; `release NAME` releases it; `holds` lists them. Each is refused where
keep-warm is not on for the caller's session (`on`: the launch keeps warm by itself, or
the session is under cover), and a hold is a signal only there. **A
launch's holds are the open holds of its whole chain**: the launch and each launch it
replaced (`relaunch_of`, `resume_of`), so work handed out before a relaunch or a resume
still keeps the new launch warm and is released from it. Opening a name open anywhere in
the chain changes nothing; releasing one that is not open says so. A hold has no expiry:
it leaves only by release.

**What Home's Sessions row reads of keep-warm writes nothing.** `signal_of` is the signal
of step 4 on values the caller read, which the ask's own reading wraps with its writes:
the `dropped` event, and a cover's `off`. `would_nudge` reads the ask's steps in its order
without writing, and is true only when each ends in a nudge and an ask is still to come
before the window shuts; with `pressed` it reads them as if cover had just been set, which
is whether a press would cover the session. `reading` names the first step not passed.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from moxi import exit as codes

from . import cachetimers, catalog, first, relaunch, slots, store, target, waits
from .recycle import LOOK, Verdict, cache_read

log = logging.getLogger("moxi.launcher.keepwarm")

#: The run's kind and mode.
KIND, MODE = "keep-warm", "ask"
#: The `events` kind of the cover record.
COVER = "keep-warm-cover"
#: The window before the cache's expiry in which an ask may nudge, in seconds: from
#: `OPEN_S` before it to `SHUT_S` before it.
OPEN_S, SHUT_S = 900.0, 360.0
#: The agent state after which an agent a nudge named is dropped for the rest of the run.
UNKNOWN = "unknown"
#: The cases, each the settings of its texts and cap.
SUBAGENTS, DIRECTOR = "subagents", "director"
CASES = (SUBAGENTS, DIRECTOR)
TEXTS = {SUBAGENTS: "keep_warm.subagent_texts", DIRECTOR: "keep_warm.director_texts"}
CAPS = {SUBAGENTS: "keep_warm.subagent_cap", DIRECTOR: "keep_warm.director_cap"}
#: The texts of the director's nudge for the agents it directs, when no hold is open.
DIRECTING_TEXTS = "keep_warm.directing_texts"
#: How long the ask's read of the agents a session directs may take, in seconds.
ASK_JOIN_BUDGET_S = 20.0
#: Every nudge's first line.
FIRST_LINE = "Keep-warm from launcher"
#: turns' refusals of the nudge that clear by themselves: the ask ends quietly.
QUIET_REFUSALS = ("stale-turn", "rate", "in-flight", "loading")
#: turns' refusals of the nudge that block it.
BLOCKED_REFUSALS = ("draft", "busy-turn", "busy-agents")
#: turns' refusals that fail it by name; any other refusal fails it as `failed`.
FAILED_REFUSALS = ("not-allowed", "unsupported", "gone")
#: How long an ask waits for turns to say whether the nudge went in, in seconds: well
#: under the timer's 120 s between asks.
NUDGE_WAIT_S = 60.0
#: The longest hold name, in characters.
NAME_CAP = 128
#: Seconds between cache's timer's asks, as the subscription states them.
ASK_EVERY_S = float(next(t["every"] for t in cachetimers.TIMERS
                         if t["name"] == cachetimers.KEEP_WARM))


# -- the ask -----------------------------------------------------------------------------


def ask(session_id: str, *, conn: sqlite3.Connection | None = None, turns: Any | None = None,
        slots_api: Any = LOOK, cache: Any = LOOK, comms: Any = LOOK,
        settings: Mapping[str, Any] | None = None,
        caller_env: Mapping[str, str] | None = None, dry_run: bool = False) -> Verdict:
    """One keep-warm ask of the session `session_id`, to its end. Never raises for what it
    reads; a fault after its run was written ends the run `exc`. `comms` stands in for
    comms' API (None for comms not installed); it is loaded only when the ask makes the
    directed join. With `dry_run` the ask writes nothing and ends, where it would nudge,
    as `would-nudge`."""
    env = dict(os.environ if caller_env is None else caller_env)
    try:
        opened = conn if conn is not None else store.connect(create=False)
    except Exception as exc:  # noqa: BLE001  (no launch log: nothing was launched)
        return Verdict(None, "not-launched", f"the launch log could not be opened: "
                                             f"{_words(exc)}")
    try:
        return _ask(opened, session_id, turns, slots_api, cache, comms, settings, env,
                    dry_run=dry_run)
    finally:
        if conn is None:
            opened.close()


def _ask(conn: sqlite3.Connection, session_id: str, turns: Any, slots_api: Any, cache: Any,
         comms: Any, settings: Mapping[str, Any] | None, env: Mapping[str, str], *,
         dry_run: bool) -> Verdict:
    from . import exits, job, recycle

    row = store.resolve(conn, session_id)
    if row is None:
        return Verdict(None, "not-launched", f"no launch holds session {session_id}")
    if not on(conn, row, session_id):
        return Verdict(None, "off", f"launch {row.request_id} does not keep warm")
    api = slots.api() if slots_api is LOOK else slots_api
    gone = lambda j: job.gone(j, slots_api=api)  # noqa: E731
    current = store.open_run(conn, row.request_id)
    if current is not None and not store.run_stale(current, gone):
        return Verdict(None, "run-open", f"run {current.run_id} is open on the launch")
    try:
        found = target.find(session_id, conn=conn, turns=turns, slots_api=api)
    except catalog.Refused as refused_exc:
        return Verdict(None, getattr(refused_exc, "code", None) or "refused", refused_exc.problem)
    try:
        if not found.live or found.session is None:
            return Verdict(None, "not-live", f"session {session_id} is not live")
        session = found.session
        sid = found.session_id or session_id
        shut = window(cache_read(cache, row, sid, env))
        if shut is not None:
            return shut
        try:
            facts = session.facts()
        except Exception as exc:  # noqa: BLE001  (the next ask reads again)
            return Verdict(None, "unreadable", f"turns' facts could not be read: "
                                               f"{_words(exc)}")
        if getattr(facts, "turn_open", False):
            return Verdict(None, "busy", "a turn is open")
        sid = getattr(facts, "session_id", None) or sid
        held = cover(conn, row, sid)
        if held is None and not launch_on(row):
            return Verdict(None, "off", f"launch {row.request_id} does not keep session "
                                        f"{sid} warm")
        number = getattr(facts, "turn_number", None)
        current_settings = settings if settings is not None else _settings()
        caps = {case: int(current_settings[CAPS[case]]) for case in CASES}
        nudges = run_of_nudges(conn, row.request_id, sid, number)
        room = subagent_room(conn, row, sid, nudges, current_settings)
        signal, joined = _signal(conn, row, sid, session, nudges,
                                 _joiner(turns, comms, sid), subagent_room=room,
                                 write=not dry_run)
        nobody = not (signal.agents or signal.holds)
        if joined is not None and nobody and not joined.names:
            if signal.sure:
                if held is not None and not dry_run:
                    cover_off(conn, row, sid, "idle")
                held = None
            elif signal.case is None:
                kept = "; the cover is kept" if held is not None else ""
                return Verdict(None, "unreadable", f"{signal.detail}{kept}")
        if signal.case is None:
            return Verdict(None, "idle", signal.detail)
        counts = run_counts(_since(nudges, held))
        pick = chosen(signal, counts, caps)
        if dry_run:
            if pick is None:
                return _capped(signal, counts, caps, held)
            return Verdict(None, "would-nudge", f"a run would nudge for the {pick.case} "
                                                f"case now: nudge {counts[pick.case] + 1} of "
                                                f"{caps[pick.case]}")
        recycle._prune(conn, current_settings)
        try:
            run = store.insert_run(conn, kind=KIND, mode=MODE, request_id=row.request_id,
                                   session_id=sid, by=store.LAUNCHER, state="acting",
                                   slot=row.slot, pane_id=row.pane_id, turn_number=number,
                                   job=exits.own_job(), gone=gone)
        except store.RunOpen as open_run_exc:
            return Verdict(None, "run-open", str(open_run_exc))
        if pick is None:
            return recycle.end(conn, run, _capped(signal, counts, caps, held))
        text = nudge_text(pick, counts[pick.case], current_settings)
        return _nudge(conn, run, session, number, pick, text)
    finally:
        found.close()


def _since(nudges: Sequence[store.Run], held: Cover | None) -> list[store.Run]:
    """The nudges of the run that count toward the caps: under cover, those after the
    press; otherwise all of them."""
    if held is None:
        return list(nudges)
    return [r for r in nudges if r.created_at > held.at]


def run_counts(nudges: Iterable[store.Run]) -> dict[str, int]:
    """The nudges counted per case: a nudge's case is the word its run ended with."""
    nudges = list(nudges)
    return {case: sum(1 for r in nudges if r.word == case) for case in CASES}


def chosen(signal: Signal, counts: Mapping[str, int],
           caps: Mapping[str, int]) -> Signal | None:
    """The signal narrowed to the case a nudge is for: the subagent case while its count
    is under its cap, else the director case while its count is under its cap. None when
    no case holds, or every case that holds is spent."""
    if signal.agents and counts[SUBAGENTS] < caps[SUBAGENTS]:
        return replace(signal, case=SUBAGENTS, directed=())
    if (signal.holds or signal.directed) and counts[DIRECTOR] < caps[DIRECTOR]:
        return replace(signal, case=DIRECTOR, agents=())
    return None


def spent(signal: Signal, counts: Mapping[str, int], caps: Mapping[str, int]) -> str:
    """Each case that holds with its count and cap: `subagents 3/3, director 10/10`."""
    held = [case for case, holds in ((SUBAGENTS, bool(signal.agents)),
                                     (DIRECTOR, bool(signal.holds or signal.directed)))
            if holds]
    return ", ".join(f"{case} {counts[case]}/{caps[case]}" for case in held)


def _capped(signal: Signal, counts: Mapping[str, int], caps: Mapping[str, int],
            held: Cover | None) -> Verdict:
    since = "the press" if held is not None else "the last turn no nudge opened"
    return Verdict("blocked", "capped", f"the run's nudges since {since} are spent: "
                                        f"{spent(signal, counts, caps)}")


def keep_warm(session: str | None = None, *, dry_run: bool = False,
              conn: sqlite3.Connection | None = None, turns: Any | None = None,
              slots_api: Any = LOOK, cache: Any = LOOK, comms: Any = LOOK,
              settings: Mapping[str, Any] | None = None,
              caller_env: Mapping[str, str] | None = None) -> relaunch.Result:
    """`moxi launcher keep-warm [--session ID] [--dry-run]`: the ask (`ask`) of the session
    `session`; with none, the ask of every live launch keep-warm is on for (`on`), by the
    id it was last known by, newest launch first (the CLI takes that only with
    `--dry-run`). Exit 1 when an ask ended other than quietly or done, else 0."""
    from . import live, recycle

    options = {"turns": turns, "cache": cache, "comms": comms, "settings": settings,
               "caller_env": caller_env, "dry_run": dry_run}
    with recycle.sweep_log(conn) as opened:
        api = slots.api() if slots_api is LOOK and opened is not None else slots_api
        if session is not None:
            row = None if opened is None else store.resolve(opened, session)
            asked = [recycle.Asked(row.request_id if row else "-", session, ask(
                session, conn=opened, slots_api=api, **options))]
        else:
            rows = [] if opened is None else [
                r for r in live.live(opened)
                if r.newest_session_id and on(opened, r, r.newest_session_id)]
            asked = [recycle.Asked(r.request_id, r.newest_session_id, ask(
                r.newest_session_id, conn=opened, slots_api=api, **options)) for r in rows]
    return recycle.swept(asked, dry_run=dry_run, none="no live launch keeps warm")


def window(clock: Any | None) -> Verdict | None:
    """None when cache's read of the session lies in keep-warm's window, else the quiet
    end that says why not."""
    if clock is None:
        return Verdict(None, "no-cache", "the session's cache was not read: cache is absent")
    ttl, age = getattr(clock, "ttl_s", None), getattr(clock, "age_s", None)
    if not isinstance(ttl, (int, float)) or not isinstance(age, (int, float)):
        kind = getattr(clock, "kind", None) or "unknown"
        return Verdict(None, kind, f"the session's cache is {kind}, with no age and lifetime "
                                   "to time a nudge by")
    if ttl <= SHUT_S:
        return Verdict(None, "no-window", f"no window: lifetime {ttl:g} s")
    if not ttl - OPEN_S <= age < ttl - SHUT_S:
        return Verdict(None, "outside", f"the cache's age, {age:g} s, is outside "
                                        f"[{ttl - OPEN_S:g}, {ttl - SHUT_S:g}) s")
    return None


def no_window(ttl: float | None) -> bool:
    """Whether a cache lifetime of `ttl` seconds leaves keep-warm no window."""
    return isinstance(ttl, (int, float)) and ttl <= SHUT_S


@dataclass(frozen=True)
class Signal:
    """What an ask can nudge for: `case`, the first case that holds (`subagents`,
    `director`, or None for neither), the live subagents, the open holds it was given,
    the agents dropped for the rest of the run, words for the log, the qualified names of
    the directed agents at work, and `sure`: whether every list it rests on was read
    whole, the session's own subagents and, where the join was made, the agents it
    directs. The director case holds by an open hold or a directed agent at work.
    `chosen` narrows it to the one case a nudge is for."""

    case: str | None
    agents: tuple[Any, ...] = ()
    holds: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    detail: str = ""
    directed: tuple[str, ...] = ()
    sure: bool = False


def _signal(conn: sqlite3.Connection, row: store.LaunchRow, sid: str, session: Any,
            nudges: Sequence[store.Run], join: Callable[[], waits.Directed], *,
            subagent_room: bool, write: bool = True) -> tuple[Signal, waits.Directed | None]:
    """The ask's signal for the session id `sid`, and the directed join when it was made:
    by `join`, unless `joins` says the ask nudges for a live subagent under its cap
    (`subagent_room`). With `write`, writes the agents newly dropped."""
    named, dropped = _run_record(conn, nudges)
    try:
        listed = session.agents()
    except Exception as exc:  # noqa: BLE001  (no subagent case without a list)
        listed, failed = None, _words(exc)
    else:
        failed = None
    held = _holds(conn, row, sid)
    signal, newly = signal_of(listed, named, dropped, held)
    if newly and write:
        store.add_run_event(conn, nudges[0].run_id, "dropped", {"agents": list(newly)})
    joined = None
    if joins(signal, has_room=subagent_room):
        joined = join()
        signal, _ = signal_of(listed, named, dropped, held, directed=joined)
    if signal.case is None:
        unread = _unread(listed, failed, joined)
        if unread:
            signal = replace(signal, detail=f"{signal.detail} ({'; '.join(unread)})")
    return signal, joined


def _holds(conn: sqlite3.Connection, row: store.LaunchRow, sid: str | None, *,
           pressed: bool = False) -> list[str]:
    """The open holds of the launch's chain wherever keep-warm is on for the session id
    `sid` (`on`: the launch keeps warm by itself, or `sid` is under cover), or `pressed`
    reads it as if cover had just been set; none otherwise."""
    if not (pressed or launch_on(row) or (sid and cover(conn, row, sid) is not None)):
        return []
    return store.open_holds(conn, row.request_id)


def subagent_room(conn: sqlite3.Connection, row: store.LaunchRow, sid: str,
                  nudges: Sequence[store.Run], settings: Mapping[str, Any]) -> bool:
    """Whether the subagent case of the session id `sid` is under its cap
    (`keep_warm.subagent_cap` in `settings`), counting its nudges in the run of nudges
    `nudges` as the ask counts them before its join: under cover, only those since the
    press. A cap that can't be read is no room, so the join is made."""
    try:
        cap = int(settings[CAPS[SUBAGENTS]])
    except (KeyError, TypeError, ValueError):
        return False
    return run_counts(_since(nudges, cover(conn, row, sid)))[SUBAGENTS] < cap


def joins(signal: Signal, *, has_room: bool) -> bool:
    """Whether a reading of keep-warm makes the directed join: unless a subagent is live
    and its case is under its cap (`has_room`, from `subagent_room`), so the ask nudges for
    it. The ask, Home's row and the press each join by this one rule, so they read the
    same cases."""
    return not (signal.agents and has_room)


def _joiner(turns: Any, comms: Any, sid: str) -> Callable[[], waits.Directed]:
    """The ask's directed join of session `sid`, made when called: one `waits.Reads` with
    a deadline `ASK_JOIN_BUDGET_S` from then, comms loaded here when not given."""
    def join() -> waits.Directed:
        api = _comms() if comms is LOOK else comms
        found_turns = turns if turns is not None else first.turns_api(quiet=True)
        reads = waits.Reads(deadline=time.monotonic() + ASK_JOIN_BUDGET_S, comms=api,
                            turns=found_turns)
        return waits.directed(reads, sid)
    return join


def _unread(listed: Any | None, failed: str | None,
            joined: waits.Directed | None) -> list[str]:
    """Each list the signal rests on that was not read whole, in words."""
    parts = []
    if failed is not None:
        parts.append(f"turns' agents could not be read: {failed}")
    elif listed is not None and getattr(listed, "complete", True) is False:
        parts.append("turns' list of agents is incomplete")
    if joined is not None and joined.raised:
        parts.append("comms or turns could not be read for the agents it directs")
    if joined is not None and joined.late:
        parts.append(f"the agents it directs were not read within {ASK_JOIN_BUDGET_S:g} s")
    if joined is not None and joined.unlisted:
        parts.append("turns could not list a directed agent's subagents")
    return parts


def signal_of(listed: Any | None, named: Iterable[str], dropped: Iterable[str],
              holds: Iterable[str], *,
              directed: waits.Directed | None = None) -> tuple[Signal, tuple[str, ...]]:
    """The signal an ask reads, from values alone, writing nothing: `listed` is turns'
    agents value (None when it could not be read), `named` and `dropped` the agents the
    current run of nudges named and dropped, `holds` the open holds the caller counts (the
    chain's, wherever keep-warm is on, `on`), `directed` the directed join
    (None when it was not made). Its `case` is the first that holds: live subagents, the
    subagent case; an open hold or directed agents at work, the director case; else
    none. It carries all three, whichever case is first. Returns the signal and the agents newly
    dropped: named, not yet dropped, and now `unknown`, or missing from a list turns says
    is complete. A newly dropped agent is not counted."""
    named, dropped = set(named), set(dropped)
    entries = list(getattr(listed, "entries", ()) or ()) if listed is not None else []
    newly: tuple[str, ...] = ()
    if listed is not None and named:
        # An agent missing from a list turns says is incomplete may only be unread.
        missing = UNKNOWN if getattr(listed, "complete", True) is not False else None
        states = {a.id: a.state for a in entries}
        newly = tuple(sorted(i for i in named - dropped if states.get(i, missing) == UNKNOWN))
        dropped |= set(newly)
    live = tuple(a for a in waits.live_subagents(listed) if a.id not in dropped)
    holds = tuple(holds)
    kept = tuple(sorted(dropped))
    sure = (listed is not None and getattr(listed, "complete", True) is not False
            and (directed is None or directed.sure))
    names = tuple(directed.names) if directed is not None else ()
    found = [f"{len(live)} subagents at work" if live else "",
             f"open holds: {', '.join(holds)}" if holds else "",
             f"{len(names)} directed agents at work" if names else ""]
    detail = "; ".join(f for f in found if f)
    case = SUBAGENTS if live else DIRECTOR if holds or names else None
    if case is None:
        detail = ("no subagent at work, no open hold and no directed agent at work"
                  if directed is not None else "no subagent at work and no open hold")
    return Signal(case, live, holds, kept, detail, directed=names, sure=sure), newly


def signal_now(conn: sqlite3.Connection, row: store.LaunchRow, listed: Any | None,
               nudges: Sequence[store.Run], *, directed: waits.Directed | None = None,
               sid: str | None = None, pressed: bool = False) -> Signal:
    """The signal an ask would read now, with `listed` its agents value, `nudges` its run
    of nudges and `directed` its directed join (None when not made), from the launch log;
    writes nothing, so an agent the next ask will drop is already not counted. The holds
    are the chain's wherever keep-warm is on for the session id `sid` (`on`), or with
    `pressed`, as the ask's; with no `sid`, only for a launch on by itself."""
    named, dropped = _run_record(conn, nudges)
    holds = _holds(conn, row, sid, pressed=pressed)
    signal, _ = signal_of(listed, named, dropped, holds, directed=directed)
    return signal


def _run_record(conn: sqlite3.Connection,
                nudges: Sequence[store.Run]) -> tuple[set[str], set[str]]:
    """The agents the run of nudges named, and those it dropped."""
    named: set[str] = set()
    dropped: set[str] = set()
    for run in nudges:
        for event in store.run_events(conn, run.run_id):
            if event.kind == "nudge":
                named.update(event.detail.get("agents") or ())
            elif event.kind == "dropped":
                dropped.update(event.detail.get("agents") or ())
    return named, dropped


def run_of_nudges(conn: sqlite3.Connection, request_id: str, session_id: str | None,
                  turn_number: int | None) -> list[store.Run]:
    """The current run of nudges on a launch, newest first: empty when the session's id
    is not the last nudge's, or its turn number is past the turn that nudge opened."""
    nudged = [r for r in store.runs(conn, request_id=request_id)
              if r.kind == KIND and r.outcome == "nudged"]
    if not nudged:
        return []
    last = nudged[0]
    if last.session_id != session_id:
        return []
    if (isinstance(turn_number, int) and isinstance(last.turn_number, int)
            and turn_number > last.turn_number):
        return []
    found = [last]
    for before in nudged[1:]:
        newer = found[-1]
        if (before.session_id != newer.session_id or not isinstance(before.turn_number, int)
                or newer.turn_number != before.turn_number + 1):
            break
        found.append(before)
    return found


def nudge_text(signal: Signal, count: int, current: Mapping[str, Any]) -> str:
    """The nudge for the case `signal` names, the `count`-th of that case in its run of
    nudges: the subagents' texts with them named; with directed agents at work, the
    directing texts with them named and an `Open holds:` line when holds are open; or,
    with holds alone, the director's texts, the open holds named from the second on."""
    directing = signal.case == DIRECTOR and bool(signal.directed)
    key = DIRECTING_TEXTS if directing else TEXTS[signal.case]
    texts = [str(t) for t in current[key]] or ["Keep-warm."]
    body = texts[min(count, len(texts) - 1)]
    lines = [FIRST_LINE, body]
    if signal.case == SUBAGENTS:
        lines += [f"- {a.description or a.kind} ({a.id})" for a in signal.agents]
    elif directing:
        lines += [f"- {name}" for name in signal.directed]
        if signal.holds:
            lines.append(f"Open holds: {', '.join(signal.holds)}")
    elif count > 0 or len(texts) == 1:
        lines.append(f"Open holds: {', '.join(signal.holds)}")
    return "\n".join(lines)


def _nudge(conn: sqlite3.Connection, run: store.Run, session: Any, number: int | None,
           signal: Signal, text: str) -> Verdict:
    """Read the facts again, then deliver the nudge right after the run is seen still
    open; its verdict ends the run, or drops it when it ended quietly."""
    from . import recycle

    try:
        try:
            again = session.facts()
        except Exception as exc:  # noqa: BLE001  (the next ask reads again)
            return recycle.end(conn, run, Verdict(None, "unreadable", _words(exc)))
        if getattr(again, "turn_open", False) or getattr(again, "turn_number", None) != number:
            return recycle.end(conn, run, Verdict(None, "busy", "a turn started meanwhile"))
        if not store.advance_run(conn, run.run_id, "acting", leaving=("acting",)):
            ended = store.get_run(conn, run.run_id)
            return Verdict(None, "ended-under", f"run {run.run_id} was ended "
                           f"({ended.outcome if ended else 'gone'}) before it acted")
        store.add_run_event(conn, run.run_id, "nudge", {
            "case": signal.case, "turn_number": number,
            "agents": [a.id for a in signal.agents], "holds": list(signal.holds),
            "directed": list(signal.directed), "text": text})
        verdict = delivered(session, text, number, ref=f"launcher-keep-warm:{run.run_id}")
        store.add_run_event(conn, run.run_id, "result", {"outcome": verdict.outcome,
                                                         "word": verdict.word,
                                                         "detail": verdict.detail})
        if verdict.outcome == "nudged":
            return _nudged(conn, run, verdict, signal, number)
        return recycle.end(conn, run, verdict)
    except BaseException as exc:  # a run never stays open on a fault
        if store.end_run(conn, run.run_id, "error", word=type(exc).__name__,
                         detail=_words(exc)):
            store.add_run_event(conn, run.run_id, "ended", {"outcome": "error",
                                                            "detail": _words(exc)})
        if not isinstance(exc, Exception):
            raise
        log.exception("keep-warm run %s stopped on a fault", run.run_id)
        return Verdict("error", type(exc).__name__, _words(exc), run.run_id)


def _nudged(conn: sqlite3.Connection, run: store.Run, verdict: Verdict, signal: Signal,
            number: int | None) -> Verdict:
    """End the run `nudged`, keeping the number of the turn the nudge opened."""
    opened = number + 1 if isinstance(number, int) else None
    detail = signal.detail
    if store.end_run(conn, run.run_id, "nudged", word=signal.case, detail=detail,
                     turn_number=opened):
        store.add_run_event(conn, run.run_id, "ended", {"outcome": "nudged",
                                                        "word": signal.case,
                                                        "turn_number": opened})
    return Verdict("nudged", signal.case, detail, run.run_id)


def delivered(session: Any, text: str, number: int | None, *, ref: str,
              wait_s: float = NUDGE_WAIT_S) -> Verdict:
    """Deliver the nudge, only if the session is idle at turn `number` and has no draft,
    and read turns' settled answer: `nudged`, `blocked`, `failed`, `unconfirmed`, or
    None for a refusal that clears by itself."""
    try:
        delivery = session.deliver(text, only_if_idle=True, if_turn_number=number,
                                   refuse_draft=True, ref=ref)
        value = delivery.wait_started(wait_s)
    except TimeoutError:
        return Verdict("unconfirmed", "timeout", f"turns did not say within {wait_s:g} s "
                                                 "whether the nudge went in")
    except Exception as exc:  # noqa: BLE001  (turns' error is the verdict)
        return Verdict("failed", "failed", f"turns could not deliver the nudge: "
                                           f"{_words(exc)}")
    kind = getattr(value, "KIND", type(value).__name__.lower())
    reason = getattr(value, "reason", None)
    if kind == "started":
        return Verdict("nudged")
    if kind == "refused":
        said = f"turns refused the nudge: {reason}"
        if reason in QUIET_REFUSALS:
            return Verdict(None, reason, said)
        if reason in BLOCKED_REFUSALS:
            return Verdict("blocked", reason, said)
        return Verdict("failed", reason if reason in FAILED_REFUSALS else "failed", said)
    return Verdict("unconfirmed", "lost", f"turns could not say whether the nudge went in: "
                                          f"{reason or kind}")


# -- cover -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cover:
    """Keep-warm cover on one launch's session: the `on` event's launch, session id and
    time, as the store writes times."""

    request_id: str
    session_id: str
    at: str


def cover(conn: sqlite3.Connection, row: store.LaunchRow, sid: str) -> Cover | None:
    """The cover of the launch `row` for the session id `sid`: the launch's newest cover
    event when it says `on` and names `sid`, else None. Reads that launch alone, not the
    launches it replaced."""
    event = store.last_event(conn, row.request_id, COVER)
    if event is None or event.detail.get("state") != "on":
        return None
    if event.detail.get("session_id") != sid:
        return None
    return Cover(row.request_id, sid, event.at)


def cover_on(conn: sqlite3.Connection, row: store.LaunchRow, sid: str, *,
             turn_number: int | None, waits: Mapping[str, Any], by: str = "page") -> Cover:
    """Write cover on for the session id `sid` of the launch `row`, with the turn number
    and what the session waits on (`kind`, `count`) at the press, and return it."""
    at = store.now()
    store.add_event(conn, COVER, {"state": "on", "by": by, "session_id": sid,
                                  "turn_number": turn_number,
                                  "waits": {"kind": waits.get("kind"),
                                            "count": waits.get("count")}},
                    request_id=row.request_id, session_id=sid, at=at)
    return Cover(row.request_id, sid, at)


def cover_off(conn: sqlite3.Connection, row: store.LaunchRow, sid: str, why: str) -> bool:
    """End the cover of the session id `sid`, saying `why`. False, writing nothing, when
    that session has no cover."""
    if cover(conn, row, sid) is None:
        return False
    store.add_event(conn, COVER, {"state": "off", "why": why, "session_id": sid},
                    request_id=row.request_id, session_id=sid)
    return True


def on(conn: sqlite3.Connection, row: store.LaunchRow, sid: str) -> bool:
    """Whether keep-warm is on for the session id `sid` of the launch `row`: the launch
    keeps warm by itself (`launch_on`), or the session is under cover."""
    return launch_on(row) or cover(conn, row, sid) is not None


def launch_on(row: store.LaunchRow) -> bool:
    """Whether the launch `row` keeps its session warm by itself: every launch of a
    harness cache times (`catalog.CACHE_TIMED`, Claude Code) that is not headless, with
    no value read, and a headless one launched with `keep_warm` on. A row recorded with
    `keep_warm` off on any other target is on all the same (`catalog.keeps_warm`)."""
    return catalog.keeps_warm(row.harness, row.target, row.values.get(catalog.KEEP_WARM))


def can_act(row: store.LaunchRow) -> bool:
    """Whether keep-warm can act on the launch `row` on this install: its `keep_warm` row
    is offered for the launch's harness (turns and cache present, and cache's timer times
    that harness here), as a launch's stamp reads it when it is composed."""
    return catalog.launcher()[catalog.KEEP_WARM].offered(harness=row.harness)


# -- whether the next ask would nudge -----------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """What `reading` found: `step`, the first of `would_nudge`'s nine steps not passed
    (None when all pass), and `case`: past step 7, the case a nudge would be for
    (`chosen`); at step 7, the first case step 6 found; None when step 6 was not reached
    or found none."""

    step: int | None
    case: str | None = None


def would_nudge(conn: sqlite3.Connection, row: store.LaunchRow, session_id: str, clock: Any,
                facts: Any, listed: Any | None, settings: Mapping[str, Any],
                records: Sequence[Mapping[str, Any]], *, gone: Callable[[str], bool],
                now: float, tick_s: float | None, directed: waits.Directed | None = None,
                pressed: bool = False) -> bool:
    """Whether every step of the ask that can be read without writing ends in a nudge, read
    now in the ask's order, with an ask still to come before the cache goes cold. Reads
    the launch log through `conn` and writes nothing. A step that could not be read is
    not passed.

    For a launch found live with its claim checked: `session_id` is the id it was found
    by, `clock` cache's read of it at `now` (epoch seconds), `facts` and `listed` turns'
    facts and agents values (`listed` None when they could not be read), `settings` the
    launcher's, `records` cache's clock records (`clock_records()`), `gone` whether a run's
    job is gone, `tick_s` the cache watcher's tick (None when it could not be read),
    `directed` the directed join read for the session (None when it was not made), and
    `pressed` whether to read it as if cover had just been set. False at the first step
    not passed:

    1. the launch keeps warm by itself (`launch_on`) or the session is under cover, or
       `pressed`;
    2. no run of any kind is open on it, or the open one is stale by `gone` at `now`;
    3. cache times sessions of the launch's harness, and its watcher is timing this one:
       a record for it rewritten within its own `stale_after`, whose `asks` names the
       keep-warm subscription; a record without `asks`, from an older watcher, passes
       only for a launch on by itself;
    4. an ask is certain to come before the window shuts: a lifetime over `SHUT_S`, and an
       age under it by more than `SHUT_S` and the most two asks can lie apart (the
       timer's interval and a watcher tick);
    5. no turn is open;
    6. a signal (`signal_of`): a live subagent; an open hold of the chain, wherever
       keep-warm is on (cover, or `pressed`, included); or a directed agent at work in
       `directed`;
    7. a case that holds is under its cap, counted per case (`chosen`, `run_counts`).
       While the cover holds (a live subagent, an open hold, a directed agent at work, or
       a join not read whole) only the nudges since the cover's press count, and none when
       `pressed`; otherwise the whole run, as the ask counts it once it ends a cover;
    8. no draft in the prompt box (`draft` False; a box that could not be read is one);
    9. no keep-warm run ended other than `nudged` since the session's last request. A
       run ended `blocked` at the cap is left out when a press came after it: under
       cover, one that ended before the press; with `pressed`, every one.
    """
    return reading(conn, row, session_id, clock, facts, listed, settings, records,
                   gone=gone, now=now, tick_s=tick_s, directed=directed,
                   pressed=pressed).step is None


def reading(conn: sqlite3.Connection, row: store.LaunchRow, session_id: str, clock: Any,
            facts: Any, listed: Any | None, settings: Mapping[str, Any],
            records: Sequence[Mapping[str, Any]], *, gone: Callable[[str], bool],
            now: float, tick_s: float | None, directed: waits.Directed | None = None,
            pressed: bool = False) -> Reading:
    """`would_nudge`'s steps, with its arguments: the first step not passed and the case
    step 6 found. Writes nothing."""
    value_on = launch_on(row)
    sid = getattr(facts, "session_id", None) or session_id
    held = cover(conn, row, sid)
    if not pressed and not (value_on or held is not None):
        return Reading(1)
    current = store.open_run(conn, row.request_id)
    if current is not None and not store.run_stale(
            current, gone, datetime.fromtimestamp(now, timezone.utc)):
        return Reading(2)
    record = timed(row.harness, session_id, records, now)
    if record is None or not _asks(record, value_on=value_on):
        return Reading(3)
    ttl, age = getattr(clock, "ttl_s", None), getattr(clock, "age_s", None)
    if not asked_before_shut(ttl, age, tick_s):
        return Reading(4)
    if getattr(facts, "turn_open", None) is not False:
        return Reading(5)
    covering = held is not None or pressed
    nudges = run_of_nudges(conn, row.request_id, sid, getattr(facts, "turn_number", None))
    signal = signal_now(conn, row, listed, nudges, directed=directed, sid=sid,
                        pressed=pressed)
    if signal.case is None:
        return Reading(6)
    try:
        caps = {case: int(settings[CAPS[case]]) for case in CASES}
    except (KeyError, TypeError, ValueError):
        return Reading(7, signal.case)
    if covering and _kept(signal, directed):
        nudges = [] if pressed else _since(nudges, held)
    pick = chosen(signal, run_counts(nudges), caps)
    if pick is None:
        return Reading(7, signal.case)
    if getattr(facts, "draft", None) is not False:
        return Reading(8, pick.case)
    if _ended_since(conn, row.request_id, now - age, press=held, pressed=pressed):
        return Reading(9, pick.case)
    return Reading(None, pick.case)


def _kept(signal: Signal, joined: waits.Directed | None) -> bool:
    """Whether the next ask keeps a cover on this signal: a live subagent, an open hold, a
    directed agent at work, or a join not read whole. With none (or no join made) the ask
    ends the cover, and counts the whole run of nudges."""
    if signal.agents or signal.holds:
        return True
    return joined is not None and (bool(joined.names) or not signal.sure)


def _asks(record: Mapping[str, Any], *, value_on: bool) -> bool:
    """Whether the watcher that wrote the clock `record` asks keep-warm about its session:
    its `asks` names the subscription; a record without `asks`, written by a watcher
    that asks by the launch's stamp, only when `value_on` (the launch is on by itself)."""
    if "asks" not in record:
        return value_on
    return cachetimers.KEEP_WARM in (record.get("asks") or ())


def timed(harness: str, session_id: str, records: Sequence[Mapping[str, Any]],
          now: float) -> Mapping[str, Any] | None:
    """The clock record by which cache's timer asks about the session: None unless cache
    times sessions of `harness` and `records` holds one for `session_id` rewritten within
    its own `stale_after` at `now`."""
    if catalog._cache_timer(harness) is not None:
        return None
    for record in records:
        if record.get("session") != session_id:
            continue
        seen, stale = record.get("seen_at"), record.get("stale_after")
        if (isinstance(seen, (int, float)) and isinstance(stale, (int, float))
                and now - seen <= stale):
            return record
    return None


def asked_before_shut(ttl: Any, age: Any, tick_s: float | None) -> bool:
    """Whether an ask is certain to come while the window is open: the timer asks every
    `ASK_EVERY_S` until `SHUT_S` before expiry, and a watcher tick (`tick_s`) later at
    most, so a cache of age `age` and lifetime `ttl` must be younger than `ttl - SHUT_S`
    by that much."""
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in (ttl, age, tick_s)):
        return False
    return ttl > SHUT_S and age < ttl - SHUT_S - (ASK_EVERY_S + tick_s)


def _ended_since(conn: sqlite3.Connection, request_id: str, since: float, *,
                 press: Cover | None = None, pressed: bool = False) -> bool:
    """Whether the launch's newest keep-warm run ended other than `nudged` after `since`
    (epoch seconds). A run ended `blocked` at the cap is passed over when a press came
    after it: one that ended before `press`, or any with `pressed`, since a press starts
    a new count."""
    for run in store.runs(conn, request_id=request_id):
        if run.kind != KIND:
            continue
        if (run.outcome == "blocked" and run.word == "capped"
                and (pressed or (press is not None and run.updated_at < press.at))):
            continue
        if run.open or run.outcome == "nudged":
            return False
        return store.parse_time(run.updated_at).timestamp() > since
    return False


# -- holds -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Held:
    """A hold opened or met open: the hold, and whether this call opened it."""

    hold: store.Hold
    new: bool


def hold(name: str, *, pid: int | None = None, turns: Any | None = None,
         slots_api: Any | None = None,
         conn: sqlite3.Connection | None = None) -> relaunch.Result:
    """`keep-warm hold NAME`, from the session's own tool shell: open the hold on the
    caller's launch. Raises a refusal with nothing written."""
    name = check_name(name)
    with relaunch._store(conn) as c:
        row = _caller(c, pid, turns, slots_api)
        opened = _open(c, row, name)
    said = "opened" if opened.new else f"is already open (since {opened.hold.opened_at})"
    return relaunch.Result(codes.OK, (f"hold {name} {said}",),
                           data={"request_id": row.request_id, "outcome": "opened"
                                 if opened.new else "open", **opened.hold.as_json()})


def release(name: str, *, pid: int | None = None, turns: Any | None = None,
            slots_api: Any | None = None,
            conn: sqlite3.Connection | None = None) -> relaunch.Result:
    """`keep-warm release NAME`, from the session's own tool shell: release the hold
    wherever it is open in the caller's launch chain."""
    name = check_name(name)
    with relaunch._store(conn) as c:
        row = _caller(c, pid, turns, slots_api)
        released = store.release_hold(c, row.request_id, name)
    if not released:
        return relaunch.Result(codes.OK, (f"hold {name} is not open; nothing was released",),
                               data={"request_id": row.request_id, "name": name,
                                     "outcome": "not-open"})
    return relaunch.Result(codes.OK, (f"hold {name} released",),
                           data={"request_id": row.request_id, "name": name,
                                 "outcome": "released",
                                 "holds": [h.as_json() for h in released]})


def holds(*, pid: int | None = None, turns: Any | None = None, slots_api: Any | None = None,
          conn: sqlite3.Connection | None = None) -> relaunch.Result:
    """`keep-warm holds`: the open holds of the caller's launch chain."""
    with relaunch._store(conn) as c:
        row = _caller(c, pid, turns, slots_api)
        found = store.holds(c, row.request_id)
    lines = [f"{h.name}  opened {h.opened_at}"
             + ("" if h.request_id == row.request_id else f" by launch {h.request_id}")
             for h in found] or ["no open holds"]
    return relaunch.Result(codes.OK, tuple(lines),
                           data={"request_id": row.request_id, "outcome": "holds",
                                 "holds": [h.as_json() for h in found]})


def open_hold(request_id: str, name: str, *,
              conn: sqlite3.Connection | None = None) -> Held:
    """Open the hold `name` on the launch `request_id`, for a caller that knows the
    launch. Raises a refusal (not launched, keep-warm off, a bad name)."""
    name = check_name(name)
    with relaunch._store(conn) as c:
        return _open(c, _launch(c, request_id), name)


def release_hold(request_id: str, name: str, *,
                 conn: sqlite3.Connection | None = None) -> list[store.Hold]:
    """Release the hold `name` wherever it is open in the chain of the launch
    `request_id`; return the holds released, none when it was not open."""
    name = check_name(name)
    with relaunch._store(conn) as c:
        row = _launch(c, request_id)
        return store.release_hold(c, row.request_id, name)


def check_name(name: str) -> str:
    """A hold's name, checked: one to `NAME_CAP` printable characters, no space at either
    end. Raises a refusal (exit 2) otherwise."""
    text = name if isinstance(name, str) else ""
    if (not text or text != text.strip() or len(text) > NAME_CAP
            or not text.isprintable()):
        raise relaunch.Refused("bad-name", f"{name!r} is not a hold name",
                               f"name the work in 1 to {NAME_CAP} printable characters",
                               exit_code=codes.USAGE)
    return text


def _caller(conn: sqlite3.Connection, pid: int | None, turns: Any | None,
            slots_api: Any | None) -> store.LaunchRow:
    found = target.caller(pid=pid, conn=conn, turns=turns, slots_api=slots_api)
    found.close()
    _check_on(conn, found.row, found.session_id or found.row.session_id)
    return found.row


def _launch(conn: sqlite3.Connection, request_id: str) -> store.LaunchRow:
    row = store.get(conn, request_id)
    if row is None:
        raise target.NotLaunched(f"there is no launch {request_id}",
                                 "`moxi launcher log` lists the launches")
    _check_on(conn, row, None)
    return row


def _check_on(conn: sqlite3.Connection, row: store.LaunchRow, sid: str | None) -> None:
    """Refuse a hold verb unless keep-warm is on for the session id `sid` (`on`): the
    launch keeps warm by itself, or `sid` is under cover. With no `sid` (a caller that
    names the launch alone), cover of whichever session id the launch's newest cover
    event names."""
    if launch_on(row):
        return
    if sid:
        if cover(conn, row, sid) is not None:
            return
    else:
        event = store.last_event(conn, row.request_id, COVER)
        if event is not None and event.detail.get("state") == "on":
            return
    hint = ("launch it with keep_warm on to use holds" if row.target == "headless"
            and row.harness in catalog.CACHE_TIMED
            else f"keep-warm keeps only {', '.join(catalog.CACHE_TIMED)} sessions warm")
    raise relaunch.Refused("off", f"launch {row.request_id} does not keep its session "
                                  "warm, so it takes no holds", hint)


def _open(conn: sqlite3.Connection, row: store.LaunchRow, name: str) -> Held:
    found, new = store.open_hold(conn, row.request_id, name)
    return Held(found, new)


# -- helpers ----------------------------------------------------------------------------


def _settings() -> Mapping[str, Any]:
    from . import settings

    return settings.current()


def _comms() -> Any | None:
    """comms' interface 2; None when comms is not installed, `waits.UNLOADED` when it is
    installed and does not load."""
    from moxi import modules

    if not modules.available("comms"):
        return None
    found = modules.load("comms", interface=2)
    return found.api if found.ok else waits.UNLOADED


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__


__all__ = ["ASK_EVERY_S", "ASK_JOIN_BUDGET_S", "BLOCKED_REFUSALS", "CAPS", "CASES", "COVER",
           "DIRECTING_TEXTS", "DIRECTOR", "FAILED_REFUSALS", "FIRST_LINE", "KIND", "MODE",
           "NAME_CAP", "NUDGE_WAIT_S", "OPEN_S", "QUIET_REFUSALS", "SHUT_S", "SUBAGENTS", "TEXTS", "Cover", "Held", "Reading", "Signal", "ask",
           "cover", "cover_off", "cover_on", "on", "asked_before_shut", "check_name", "delivered",
           "hold", "holds", "keep_warm", "no_window", "nudge_text", "open_hold", "reading", "release",
           "release_hold", "run_counts", "run_of_nudges", "signal_now", "signal_of", "spent",
           "timed", "window", "chosen", "launch_on", "would_nudge", "subagent_room",
           "joins", "can_act"]
