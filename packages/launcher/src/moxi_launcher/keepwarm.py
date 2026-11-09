"""Keep-warm: a launched session nudged before its prompt cache expires while it waits on
work it handed out, and the named holds that say a director has work out.

    ask(session_id)                  # moxi-launcher-job keep-warm SESSION, asked by cache
    hold(name) / release(name)       # keep-warm hold|release NAME, from the session itself
    holds()                          # keep-warm holds: the open holds of the caller's launch
    open_hold(request_id, name)      # the same, for a caller that knows the launch
    release_hold(request_id, name)

**Keep-warm acts only on a session the launcher launched with `keep_warm` on.** The row
stamps `MOXI_LAUNCHER_KEEP_WARM=on`, and cache's timer asks about a session carrying it,
from 900 s before its cache expires until 360 s before, every 120 s (`cachetimers`). The
timer decides only when to ask; each ask reads for itself, in this order, and stops at
the first that answers:

1. The launch: launched, `keep_warm` on, no open run (a stale one, whose job is gone, is
   ended at the insert), and the session claimed and live.
2. Cache's read of the session: its age must lie in `[lifetime - 900, lifetime - 360)`.
   A lifetime of 360 s or less leaves no window, and keep-warm never nudges.
3. turns' facts: a turn is open, nothing (`busy`).
4. The signals. The subagent case: `subagent` or `teammate` entries not final, pending,
   running or waiting (a `shell` never counts). The director case: any open hold of the
   launch chain. Neither: nothing (`idle`). **An agent a nudge of the current run of
   nudges named, which turns then stops listing or reports `unknown`, is dropped for the
   rest of that run, even if it comes back.**
5. The cap: past the case's cap of nudges in a row, `blocked`, word `capped`.
6. The nudge: one delivery through turns, only if the session is idle, at the turn number
   step 3 read, refused over a draft. The run is written first (`acting`, this process
   its job), so a second ask, or any other act, loses at the insert; the facts are read
   again, and then the nudge goes.

**A run of nudges is the nudges since the last turn no nudge opened.** Each `nudged` run
keeps the session id and the number of the turn it opened; a later ask starts a new run
when the session's id differs from the last nudge's (a clear starts a new id, with turn
numbers from 0 again), or the id is the same and the turn number is past the nudge's.

**The subagent case comes first; one nudge per ask.** Its texts (`keep_warm.subagent_texts`)
go in order, the live subagents named after each, capped at `keep_warm.subagent_cap`. The
director's first text (`keep_warm.director_texts`) is a plain nudge and the second, used
for every later one, asks for a recheck of the open holds, named; capped at
`keep_warm.director_cap`. Every nudge's first line is `Keep-warm from launcher`.

Each ask ends one way: quietly, writing nothing (none of the above holds, another run is
open, or turns refused for a reason that clears by itself: `stale-turn`, `rate`,
`in-flight`, `loading`); `nudged`; `blocked` (`capped`, or turns' `draft` or `busy-turn`
at the delivery); `failed` (turns' `not-allowed`, `unsupported`, `gone`, or another
refusal); or `unconfirmed`, when turns could not say whether the nudge went in. **A
repeated `blocked` updates one run** (`store.end_blocked`).

**Holds.** `keep-warm hold NAME`, run from the director's own session, opens a hold on
its launch; `release NAME` releases it; `holds` lists them. Each is refused while the
launch's `keep_warm` is off. **A launch's holds are the open holds of its whole chain**:
the launch and each launch it replaced (`relaunch_of`, `resume_of`), so work handed out
before a relaunch or a resume still keeps the new launch warm and is released from it.
Opening a name open anywhere in the chain changes nothing; releasing one that is not open
says so. A hold has no expiry: it leaves only by release.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from moxi import exit as codes

from . import catalog, relaunch, slots, store, target
from .recycle import LOOK, Verdict, cache_read

log = logging.getLogger("moxi.launcher.keepwarm")

#: The run's kind and mode.
KIND, MODE = "keep-warm", "ask"
#: The window before the cache's expiry in which an ask may nudge, in seconds: from
#: `OPEN_S` before it to `SHUT_S` before it.
OPEN_S, SHUT_S = 900.0, 360.0
#: The agent kinds and states that are the subagent case's signal; a `shell` never is.
SIGNAL_KINDS = ("subagent", "teammate")
SIGNAL_STATES = ("pending", "running", "waiting")
#: The agent state after which an agent a nudge named is dropped for the rest of the run.
UNKNOWN = "unknown"
#: The cases, each the settings of its texts and cap.
SUBAGENTS, DIRECTOR = "subagents", "director"
TEXTS = {SUBAGENTS: "keep_warm.subagent_texts", DIRECTOR: "keep_warm.director_texts"}
CAPS = {SUBAGENTS: "keep_warm.subagent_cap", DIRECTOR: "keep_warm.director_cap"}
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


# -- the ask -----------------------------------------------------------------------------


def ask(session_id: str, *, conn: sqlite3.Connection | None = None, turns: Any | None = None,
        slots_api: Any = LOOK, cache: Any = LOOK, settings: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None) -> Verdict:
    """One keep-warm ask of the session `session_id`, to its end. Never raises for what it
    reads; a fault after its run was written ends the run `error`."""
    env = dict(os.environ if environ is None else environ)
    try:
        opened = conn if conn is not None else store.connect(create=False)
    except Exception as error:  # noqa: BLE001  (no launch log: nothing was launched)
        return Verdict(None, "not-launched", f"the launch log could not be opened: "
                                             f"{_words(error)}")
    try:
        return _ask(opened, session_id, turns, slots_api, cache, settings, env)
    finally:
        if conn is None:
            opened.close()


def _ask(conn: sqlite3.Connection, session_id: str, turns: Any, slots_api: Any, cache: Any,
         settings: Mapping[str, Any] | None, env: Mapping[str, str]) -> Verdict:
    from . import exits, job, recycle

    row = store.resolve(conn, session_id)
    if row is None:
        return Verdict(None, "not-launched", f"no launch holds session {session_id}")
    if row.values.get("keep_warm") != "on":
        return Verdict(None, "off", f"launch {row.request_id} does not keep warm")
    api = slots.api() if slots_api is LOOK else slots_api
    gone = lambda j: job.gone(j, slots_api=api)  # noqa: E731
    current = store.open_run(conn, row.request_id)
    if current is not None and not store.run_stale(current, gone):
        return Verdict(None, "run-open", f"run {current.run_id} is open on the launch")
    try:
        found = target.find(session_id, conn=conn, turns=turns, slots_api=api)
    except catalog.Refused as refused:
        return Verdict(None, getattr(refused, "code", None) or "refused", refused.problem)
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
        except Exception as error:  # noqa: BLE001  (the next ask reads again)
            return Verdict(None, "unreadable", f"turns' facts could not be read: "
                                               f"{_words(error)}")
        if getattr(facts, "turn_open", False):
            return Verdict(None, "busy", "a turn is open")
        sid = getattr(facts, "session_id", None) or sid
        number = getattr(facts, "turn_number", None)
        current_settings = settings if settings is not None else _settings()
        nudges = run_of_nudges(conn, row.request_id, sid, number)
        signal = _signal(conn, row, session, nudges)
        if signal.case is None:
            return Verdict(None, "idle", signal.detail)
        cap = int(current_settings[CAPS[signal.case]])
        recycle._prune(conn, current_settings)
        try:
            run = store.insert_run(conn, kind=KIND, mode=MODE, request_id=row.request_id,
                                   session_id=sid, actor=store.LAUNCHER, state="acting",
                                   slot=row.slot, pane_id=row.pane_id, turn_number=number,
                                   job=exits.own_job(), gone=gone)
        except store.RunOpen as open_run:
            return Verdict(None, "run-open", str(open_run))
        if len(nudges) >= cap:
            return recycle.end(conn, run, Verdict(
                "blocked", "capped", f"{len(nudges)} nudges in a row since the last turn no "
                                     f"nudge opened; the {signal.case} cap is {cap}"))
        text = nudge_text(signal, len(nudges), current_settings)
        return _nudge(conn, run, session, number, signal, text)
    finally:
        found.close()


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
    """What an ask nudges for: `case` (`subagents`, `director`, or None for neither), the
    live subagents it names, the open holds of the chain, the agents dropped for the rest
    of the run, and words for the log."""

    case: str | None
    agents: tuple[Any, ...] = ()
    holds: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    detail: str = ""


def _signal(conn: sqlite3.Connection, row: store.LaunchRow, session: Any,
            nudges: Sequence[store.Run]) -> Signal:
    named, dropped = _run_record(conn, nudges)
    try:
        listed = session.agents()
    except Exception as error:  # noqa: BLE001  (no subagent case without a list)
        listed, failed = None, _words(error)
    else:
        failed = None
    entries = list(getattr(listed, "entries", ()) or ()) if listed is not None else []
    if listed is not None and named:
        # An agent missing from a list turns says is incomplete may only be unread.
        missing = UNKNOWN if getattr(listed, "complete", True) is not False else None
        states = {a.id: a.state for a in entries}
        newly = sorted(i for i in named - dropped if states.get(i, missing) == UNKNOWN)
        if newly:
            store.add_run_event(conn, nudges[0].run_id, "dropped", {"agents": newly})
            dropped |= set(newly)
    live = tuple(a for a in entries if a.kind in SIGNAL_KINDS and a.final is not True
                 and a.state in SIGNAL_STATES and a.id not in dropped)
    holds = tuple(store.open_holds(conn, row.request_id))
    kept = tuple(sorted(dropped))
    if live:
        return Signal(SUBAGENTS, live, holds, kept, f"{len(live)} subagents at work")
    if holds:
        return Signal(DIRECTOR, (), holds, kept, f"open holds: {', '.join(holds)}")
    said = "no subagent at work and no open hold"
    if failed is not None:
        said += f" (turns' agents could not be read: {failed})"
    return Signal(None, (), (), kept, said)


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
    """The nudge for the case `signal` names, the `count`-th of its run of nudges."""
    texts = [str(t) for t in current[TEXTS[signal.case]]] or ["Keep-warm."]
    body = texts[min(count, len(texts) - 1)]
    lines = [FIRST_LINE, body]
    if signal.case == SUBAGENTS:
        lines += [f"- {a.description or a.kind} ({a.id})" for a in signal.agents]
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
        except Exception as error:  # noqa: BLE001  (the next ask reads again)
            return recycle.end(conn, run, Verdict(None, "unreadable", _words(error)))
        if getattr(again, "turn_open", False) or getattr(again, "turn_number", None) != number:
            return recycle.end(conn, run, Verdict(None, "busy", "a turn started meanwhile"))
        if not store.advance_run(conn, run.run_id, "acting", leaving=("acting",)):
            ended = store.get_run(conn, run.run_id)
            return Verdict(None, "ended-under", f"run {run.run_id} was ended "
                           f"({ended.outcome if ended else 'gone'}) before it acted")
        store.add_run_event(conn, run.run_id, "nudge", {
            "case": signal.case, "turn_number": number,
            "agents": [a.id for a in signal.agents], "holds": list(signal.holds),
            "text": text})
        verdict = delivered(session, text, number, ref=f"launcher-keep-warm:{run.run_id}")
        store.add_run_event(conn, run.run_id, "result", {"outcome": verdict.outcome,
                                                         "word": verdict.word,
                                                         "detail": verdict.detail})
        if verdict.outcome == "nudged":
            return _nudged(conn, run, verdict, signal, number)
        return recycle.end(conn, run, verdict)
    except BaseException as error:  # a run never stays open on a fault
        if store.end_run(conn, run.run_id, "error", word=type(error).__name__,
                         detail=_words(error)):
            store.add_run_event(conn, run.run_id, "ended", {"outcome": "error",
                                                            "detail": _words(error)})
        if not isinstance(error, Exception):
            raise
        log.exception("keep-warm run %s stopped on a fault", run.run_id)
        return Verdict("error", type(error).__name__, _words(error), run.run_id)


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
    except Exception as error:  # noqa: BLE001  (turns' error is the verdict)
        return Verdict("failed", "failed", f"turns could not deliver the nudge: "
                                           f"{_words(error)}")
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
    _check_on(found.row)
    return found.row


def _launch(conn: sqlite3.Connection, request_id: str) -> store.LaunchRow:
    row = store.get(conn, request_id)
    if row is None:
        raise target.NotLaunched(f"there is no launch {request_id}",
                                 "`moxi launcher log` lists the launches")
    _check_on(row)
    return row


def _check_on(row: store.LaunchRow) -> None:
    if row.values.get("keep_warm") != "on":
        raise relaunch.Refused("off", f"launch {row.request_id} does not keep its session "
                                      "warm, so it takes no holds",
                               "launch with keep_warm on to use holds")


def _open(conn: sqlite3.Connection, row: store.LaunchRow, name: str) -> Held:
    found, new = store.open_hold(conn, row.request_id, name)
    return Held(found, new)


# -- helpers ----------------------------------------------------------------------------


def _settings() -> Mapping[str, Any]:
    from . import settings

    return settings.current()


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__


__all__ = ["BLOCKED_REFUSALS", "CAPS", "DIRECTOR", "FAILED_REFUSALS", "FIRST_LINE", "KIND",
           "MODE", "NAME_CAP", "NUDGE_WAIT_S", "OPEN_S", "QUIET_REFUSALS", "SHUT_S",
           "SIGNAL_KINDS", "SIGNAL_STATES", "SUBAGENTS", "TEXTS", "Held", "Signal", "ask",
           "check_name", "delivered", "hold", "holds", "no_window", "nudge_text",
           "open_hold", "release", "release_hold", "run_of_nudges", "window"]
