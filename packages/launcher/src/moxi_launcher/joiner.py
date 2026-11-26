"""The joiner: what Home's Sessions row shows of each running launch beyond the launch
log, read off the page server's poller (`pagehandlers`, `/api/running` and the stream's
topic `running`).

**A pass** takes the newest `JOIN_MAX` live launches (`api.running`) and reads each one's
facts on a pool of `JOIN_THREADS` threads, with a deadline `JOIN_BUDGET_S` seconds after
the pass starts. A launch's worker runs each read of `READS` in turn, and every call a
read makes goes through `Pass.call`, which looks at the deadline first and raises `Late`
past it: a worker makes no new call once the deadline has passed, so it ends within the
deadline and the one call it was in. At the deadline the pass publishes what finished. A
launch whose worker had not finished keeps the facts last read for it, for `JOIN_KEEP_S`
seconds from when they were read, and has none after that; so does a launch whose
session id is still the one they were read for. A launch past the newest `JOIN_MAX` has
none. `run` returns only when every worker thread of the pass has ended, so one pass
runs at a time.

What a pass reads, each only when its module is installed:

* `cache`: cache's read of the session's clock (`recycle.cache_read`), local file reads.
  `state` is `warm` or `cold` (any other kind gives no fact), `until` when the cache goes
  cold, and `estimated` whether that rests on a lifetime no request named. `until`
  changes only when the session made a new request: a value within a second of the one
  last published for the launch and session is that one.
* `activity`: the session's latest message, through sessions' `find` and `window`, the
  window read again only when the transcript's size or modification time changed since
  the last pass. `at` is when it was written, `by` is `you` for a message the person at
  the prompt sent and `agent` for the model's own, and `text` its first line.
* `waits`, `covered` and `coverable` (`read_waits`), with turns, only while the launch's
  own turn is closed. The session is found as an act finds it (`target.find`), but slots is never
  asked. `waits` counts its live subagents as keep-warm counts them (`keepwarm.signal_of`);
  with none, and comms installed, the agents its comms name directs that work; with
  neither, the open holds of its chain, read as a press reads them. The pass
  joins where keep-warm's ask would (`keepwarm.joins`): unless a live subagent's case is
  under its cap, so with the subagent case spent the directed agents still count toward
  `covered`. It reads them as
  `waits.directed` does, through the pass: comms' `owner`, one `roster` for the
  pass, `session_of` for each directed agent, then turns' facts of that agent's session.
  Each comms read may take `COMMS_TIMEOUT_S`, and once one finds the daemon down the pass
  asks comms nothing more. A join that raised, or one through a comms that is installed
  and does not load, counts no agents (only open holds, if any, are waited on), and one that met the deadline leaves the launch
  unpublished in this pass. `covered` is `keepwarm.would_nudge` over what the pass read,
  the directed join included, and is false without cache. `coverable`, set only on a launch that waits and is not covered, is the
  cap of the case a press would nudge for, when `keepwarm.reading` with `pressed` passes
  every step; else null. None of them writes anything.

After its reads a launch's worker sets `next_keep_alive_at` (`next_keep_alive`): when
`covered` is true and `cache.until` is set, the moment keep-warm's window opens on that
expiry, `cache.until` less `keepwarm.OPEN_S` (`keepwarm.window_opens`), written as `until`
is; else null. Keep-warm's first ask at or after it nudges, so a time already past means
the nudge goes at the next ask. It reads nothing of its own: it moves only when `until` or
`covered` does.

No fact holds the time it was read, so a pass over sessions that did nothing gives the
same facts as the last one, and the topic publishes nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from moxi import aio, config, modules

from . import api, catalog, first, keepwarm, recycle, settings, store, target, waits
from .waits import COMMS_TIMEOUT_S, Late

#: Seconds after a pass ended before the next, when the set of running launches and
#: their session ids did not change before then.
JOIN_EVERY = 15.0
#: The most launches a pass reads, newest first.
JOIN_MAX = 24
#: The threads a pass reads on.
JOIN_THREADS = 4
#: Seconds from a pass's start after which its workers make no new call.
JOIN_BUDGET_S = 8.0
#: Seconds a launch keeps the facts last read for it when a pass did not finish its read.
JOIN_KEEP_S = 45.0
#: The bytes the activity read takes from the end of a transcript at most.
ACTIVITY_LIMIT = 256 * 1024
#: The characters of a message's first line `activity.text` keeps.
ACTIVITY_TEXT_CHARS = 200
#: Seconds within which a newly read `until` is the one last published.
UNTIL_SAME_S = 1.0
#: The cache kinds a launch's `cache` fact is given for.
CACHE_KINDS = ("warm", "cold")

log = logging.getLogger("moxi.launcher.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


@dataclass(frozen=True)
class Launch:
    """One launch a pass reads: `api.running`'s value and the launch's row."""

    running: api.Running
    row: store.LaunchRow

    @property
    def request_id(self) -> str:
        return self.running.request_id

    @property
    def session_id(self) -> str | None:
        return self.running.session_id


@dataclass(frozen=True)
class Kept:
    """The facts last read for a launch: the session id they were read for, the facts (the
    keys of `pagehandlers.UNJOINED` that were read), and when, on the joiner's clock."""

    session_id: str | None
    facts: Mapping[str, Any]
    read_at: float


def cache_api() -> Any | None:
    """cache's interface 1, or None when cache is not installed or does not load."""
    return _optional("cache", 1)


def sessions_api() -> Any | None:
    """sessions' interface 2, or None when sessions is not installed or does not load."""
    return _optional("sessions", 2)


def comms_api() -> Any | None:
    """comms' interface 2; None when comms is not installed, `waits.UNLOADED` when it is
    installed and does not load, so a join through it reads as one that raised."""
    if not modules.available("comms"):
        return None
    found = modules.load("comms", interface=2)
    return found.api if found.ok else waits.UNLOADED


def cache_tick_s() -> float | None:
    """The cache watcher's tick, `watch.tick_s` in cache's settings; None when cache is not
    installed or its settings can't be read."""
    if not modules.available("cache"):
        return None
    found = modules.load("cache", interface=1)
    if not found.ok:
        return None
    tick = config.load("cache", config.schema_for(found))["watch.tick_s"]
    return float(tick) if isinstance(tick, (int, float)) else None


def _optional(name: str, interface: int) -> Any | None:
    if not modules.available(name):
        return None
    found = modules.load(name, interface=interface)
    return found.api if found.ok else None


def take(limit: int) -> list[Launch]:
    """The newest `limit` live launches, each with its row; `[]` with no launch log."""
    found = api.running(limit=limit)
    if not found:
        return []
    with store.reading() as conn:
        if conn is None:
            return []
        rows = {each.request_id: store.get(conn, each.request_id) for each in found}
    return [Launch(each, row) for each in found
            if (row := rows[each.request_id]) is not None]


class Pass(waits.Reads):
    """What one pass's workers share: the `waits.Reads` that bounds every call they make,
    with the pass's deadline on the joiner's clock, and besides it the APIs of the modules
    it reads (None for one not installed), the environment the reads resolve roots in, and
    the joiner whose memory the reads consult.

    It also holds what one pass reads once for every launch (`shared`), the clock each
    launch's cache read gave (`clocks`, by request id, with the time it was read at), and
    whether comms' daemon was found down, after which the pass asks comms nothing more."""

    def __init__(self, joiner: Joiner, *, deadline: float, cache: Any | None,
                 sessions: Any | None, env: Mapping[str, str], turns: Any | None = None,
                 comms: Any | None = None):
        super().__init__(deadline=deadline, clock=joiner.clock, comms=comms, turns=turns)
        self.joiner = joiner
        self.cache = cache
        self.sessions = sessions
        self.env = env
        self.clocks: dict[str, tuple[Any, float]] = {}


# -- the reads --------------------------------------------------------------------------------


def _iso(epoch: float, *, whole: bool = False) -> str:
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return moment.isoformat(timespec="seconds" if whole else "milliseconds").replace(
        "+00:00", "Z")


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def cache_fact(harness: str, clock: Any, now: float,
               kept: float | None = None) -> dict[str, Any] | None:
    """The `cache` fact of a cache read made at `now` (epoch seconds), or None for a kind
    but `warm` or `cold`. `until` is `now − age + lifetime` to the nearest second, or
    `kept` (the `until` last published, epoch seconds) when within `UNTIL_SAME_S` of it;
    None for a cold cache with no lifetime. `estimated`: a Claude lifetime from the
    setting, no request having named a tier, or a Codex lifetime measured, not
    documented."""
    if clock is None or clock.kind not in CACHE_KINDS:
        return None
    until = None
    if clock.ttl_s is not None and clock.age_s is not None:
        raw = now - clock.age_s + clock.ttl_s
        until = kept if kept is not None and abs(raw - kept) <= UNTIL_SAME_S else round(raw)
    if harness == "codex":
        estimated = clock.basis == "measured"
    else:
        estimated = clock.ttl_source == "config"
    return {"state": clock.kind, "until": None if until is None else _iso(until, whole=True),
            "estimated": estimated}


def read_cache(each: Pass, launch: Launch) -> dict[str, Any]:
    """`cache`, from cache's read of the session's clock."""
    if each.cache is None:
        return {}
    now = time.time()
    clock = each.call(recycle.cache_read, each.cache, launch.row, launch.session_id,
                      each.env, now=now)
    each.clocks[launch.request_id] = (clock, now)
    kept = each.joiner.until_of(launch.request_id, launch.session_id)
    return {"cache": cache_fact(launch.row.harness, clock, now, kept)}


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:ACTIVITY_TEXT_CHARS]
    return ""


def activity_of(records: Any) -> dict[str, Any] | None:
    """The `activity` fact of a window's records, oldest first: the newest message that is
    not reasoning, holds text, has a readable time and is the user's or the assistant's;
    None when there is none."""
    for record in reversed(tuple(records)):
        if getattr(record, "kind", None) != "message" or record.reasoning:
            continue
        if record.role not in ("user", "assistant") or not record.text.strip():
            continue
        if isinstance(record.time, bool) or not isinstance(record.time, (int, float)):
            continue
        if record.role == "assistant":
            by = "agent"
        else:
            by = "you" if record.injected is False else None
        return {"at": _iso(record.time), "by": by, "text": _first_line(record.text)}
    return None


def transcripts_root(sessions: Any, row: store.LaunchRow,
                     env: Mapping[str, str]) -> Any | None:
    """The root a launch's transcripts are under, as cache reads it for the same row:
    for Claude, sessions' projects root over the row's recorded `CLAUDE_CONFIG_DIR` (None,
    sessions' own root, when it recorded none); for Codex, its sessions directory."""
    if row.harness == "codex":
        return recycle.codex_sessions(row, env)
    return recycle.claude_projects(sessions, row)


def read_activity(each: Pass, launch: Launch) -> dict[str, Any]:
    """`activity`, from the newest turn of the session's transcript."""
    if each.sessions is None:
        return {}
    root = transcripts_root(each.sessions, launch.row, each.env)
    found = each.call(each.sessions.find, launch.row.harness, launch.session_id, root=root)
    source = next((hit.source for hit in found.hits if not hit.compressed), None)
    if source is None:
        return {"activity": None}
    seen = each.call(os.stat, source.path)
    stamp = (source.path, seen.st_size, seen.st_mtime_ns)
    key = (launch.request_id, launch.session_id)
    last = each.joiner.windows.get(key)
    if last is not None and last[0] == stamp:
        return {"activity": last[1]}
    window = each.call(each.sessions.window, source, turns=1, limit=ACTIVITY_LIMIT)
    fact = activity_of(window.records)
    each.joiner.windows[key] = (stamp, fact)
    return {"activity": fact}


def read_waits(each: Pass, launch: Launch) -> dict[str, Any]:
    """`waits`, `covered` and `coverable`, while the launch's own turn is closed: the
    session found as an act finds it but without asking slots, its facts, then its live
    subagents counted as keep-warm counts them, else the agents it directs that work
    (`waits.directed`), else its chain's open holds; whether keep-warm's next ask would nudge it, on the launch or its
    cover (`keepwarm.would_nudge`); and, for a launch that waits and is not covered,
    whether a press would cover it (`coverable`). Nothing without turns; no `waits`,
    `covered` false and `coverable` null for a session not found live, or whose turn is
    open."""
    if each.turns is None:
        return {}
    with each.call(store.reading) as conn:
        if conn is None:
            return {}
        try:
            found = each.call(target.find, launch.session_id, conn=conn, turns=each.turns,
                              slot=False)
        except catalog.Refused:
            return {"waits": None, **UNCOVERED}
        try:
            if not found.live or found.session is None:
                return {"waits": None, **UNCOVERED}
            return _waits_and_cover(each, launch, conn, found)
        finally:
            found.close()


def _waits_and_cover(each: Pass, launch: Launch, conn: Any, found: Any) -> dict[str, Any]:
    session = found.session
    sid = found.session_id or launch.session_id
    facts = each.call(session.facts)
    if getattr(facts, "turn_open", None) is not False:
        return {"waits": None, **UNCOVERED}
    try:
        listed = each.call(session.agents)
    except Late:
        raise
    except Exception:  # noqa: BLE001  (an unread list counts no subagent)
        listed = None
    number = getattr(facts, "turn_number", None)
    nudges = each.call(keepwarm.run_of_nudges, conn, found.row.request_id,
                       getattr(facts, "session_id", None) or sid, number)
    now_sid = getattr(facts, "session_id", None) or sid
    # The holds are read as a press reads them, so a launch a press would cover reads the
    # open holds of its chain as what it waits on.
    signal = each.call(keepwarm.signal_now, conn, found.row, listed, nudges, sid=now_sid,
                       pressed=True)
    joined = None
    if each.comms is not None and keepwarm.joins(
            signal, has_room=_subagent_room(each, conn, found.row, now_sid, nudges)):
        joined = waits.directed(each, sid)
        if joined.late:
            raise Late()
    fact = None
    if signal.agents:
        fact = {"kind": "subagents", "count": len(signal.agents)}
    elif joined is not None and not joined.raised and joined.names:
        fact = {"kind": "agents", "count": len(joined.names)}
    elif signal.holds:
        fact = {"kind": "holds", "count": len(signal.holds)}
    return {"waits": fact, **_covered(each, launch, conn, found, sid, facts, listed, fact,
                                      joined)}


def _subagent_room(each: Pass, conn: Any, row: Any, sid: str, nudges: Any) -> bool:
    """`keepwarm.subagent_room` read through the pass, so Home's row joins exactly where
    the ask would; settings that can't be read are no room, and the join is made."""
    try:
        current = each.shared("settings", lambda: each.call(settings.current))
    except Late:
        raise
    except Exception:  # noqa: BLE001  (read more rather than less)
        return False
    return keepwarm.subagent_room(conn, row, sid, nudges, current)


#: `covered` and `coverable` where keep-warm's next ask would not nudge and a press would
#: not change that.
UNCOVERED: Mapping[str, Any] = MappingProxyType({"covered": False, "coverable": None})


def proc_gone(job: str) -> bool:
    """Whether a run's job is gone, asking slots nothing: `job.gone` for a process job,
    False for any other, so an open run slots runs reads as open."""
    from . import job as jobs

    return job.startswith("proc:") and jobs.gone(job)


def _covered(each: Pass, launch: Launch, conn: Any, found: Any, sid: str, facts: Any,
             listed: Any | None, fact: Mapping[str, Any] | None,
             joined: waits.Directed | None) -> dict[str, Any]:
    """`covered`, `keepwarm.would_nudge` over what the pass read, and `coverable`: for a
    launch that waits (`fact`) and is not covered, `{"cap": n}` when the same reading
    with `pressed` passes every step, `n` the cap of the case it found; else None.
    `joined` is the directed join the pass read for `waits`, None when it was not made."""
    read = each.clocks.get(launch.request_id)
    if each.cache is None or read is None:
        return dict(UNCOVERED)
    clock, now = read
    try:
        current = each.shared("settings", lambda: each.call(settings.current))
        records = each.shared("records", lambda: each.call(each.cache.clock_records))
        tick_s = each.shared("tick", lambda: each.call(cache_tick_s))
    except Late:
        raise
    except Exception:  # noqa: BLE001  (a step that could not be read is not passed)
        return dict(UNCOVERED)
    reads = (conn, found.row, sid, clock, facts, listed, current, records)
    options = {"gone": proc_gone, "now": now, "tick_s": tick_s, "directed": joined}
    if each.call(keepwarm.would_nudge, *reads, **options):
        return {"covered": True, "coverable": None}
    if fact is None:
        return dict(UNCOVERED)
    pressed = each.call(keepwarm.reading, *reads, **options, pressed=True)
    if pressed.step is not None:
        return dict(UNCOVERED)
    return {"covered": False,
            "coverable": {"cap": int(current[keepwarm.CAPS[pressed.case]])}}


#: A launch's reads, in the order a worker makes them. Each takes the pass and the launch
#: and returns the facts it read, a key of `pagehandlers.UNJOINED` each; every call it
#: makes goes through `Pass.call`. A read that raises (but `Late`) gives nothing for this
#: pass and the worker goes on to the next.
READS: tuple[Callable[[Pass, Launch], Mapping[str, Any]], ...] = (
    read_cache, read_activity, read_waits)


def next_keep_alive(facts: Mapping[str, Any]) -> str | None:
    """The `next_keep_alive_at` of a launch's facts: when `covered` is true and its cache
    has an `until`, that time less `keepwarm.OPEN_S`, in whole seconds with `Z`; else
    None."""
    cache = facts.get("cache")
    until = cache.get("until") if isinstance(cache, Mapping) else None
    if facts.get("covered") is not True or until is None:
        return None
    return _iso(keepwarm.window_opens(_epoch(until)), whole=True)


def work(each: Pass, launch: Launch) -> dict[str, Any]:
    """One launch's facts: every read of `READS`, in order, then `next_keep_alive_at` from
    what they read. Raises `Late` when a read asks for a call past the deadline. A launch
    with no session id has no fact."""
    facts: dict[str, Any] = {}
    if launch.session_id is None:
        return facts
    for read in READS:
        try:
            facts.update(read(each, launch))
        except Late:
            raise
        except Exception as exc:
            _log_once(exc, f"reading a running launch's {read.__name__} failed")
    facts["next_keep_alive_at"] = next_keep_alive(facts)
    return facts


class Joiner:
    """The facts kept for each running launch, and the passes that read them.

    `clock` is the monotonic clock deadlines and keeps are measured on. `kept` maps a
    request id to its `Kept`; `windows` maps `(request id, session id)` to the transcript's
    `(path, size, mtime)` at its last activity read and the fact read then. `passes`
    counts the passes started."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.kept: dict[str, Kept] = {}
        self.windows: dict[tuple[str, str | None], tuple[tuple, Any]] = {}
        self._untils: dict[tuple[str, str | None], float] = {}
        self.passes = 0

    def joined(self) -> dict[str, dict[str, Any]]:
        """The facts to lay over each running launch, by request id, with its session id
        under `session_id`: those read less than `JOIN_KEEP_S` seconds ago."""
        now = self.clock()
        return {request_id: {**kept.facts, "session_id": kept.session_id}
                for request_id, kept in self.kept.items()
                if now - kept.read_at < JOIN_KEEP_S}

    def until_of(self, request_id: str, session_id: str | None) -> float | None:
        """The `until` last published for a launch's session, epoch seconds, or None."""
        return self._untils.get((request_id, session_id))

    async def run(self, publish: Callable[[], Awaitable[Any]]) -> None:
        """One pass. Awaits `publish` once the finished facts are kept, at the deadline
        at the latest, and returns when every worker thread has ended."""
        self.passes += 1
        deadline = self.clock() + JOIN_BUDGET_S
        launches, cache, sessions, turns, comms = await aio.offload(_prepared)
        each = Pass(self, deadline=deadline, cache=cache, sessions=sessions,
                    env=dict(os.environ), turns=turns, comms=comms)
        pool = ThreadPoolExecutor(max_workers=JOIN_THREADS,
                                  thread_name_prefix="launcher join")
        try:
            futures = {asyncio.wrap_future(pool.submit(work, each, launch)): launch
                       for launch in launches}
            pending: set[asyncio.Future[Any]] = set(futures)
            finished: dict[str, dict[str, Any]] = {}
            if futures:
                done, pending = await asyncio.wait(
                    futures, timeout=max(0.0, deadline - self.clock()))
                for future in done:
                    if future.exception() is None:
                        finished[futures[future].request_id] = future.result()
                    elif not isinstance(future.exception(), Late):
                        _log_once(future.exception(), "a running launch's read failed")
            self._keep(launches, finished)
            await publish()
            if pending:
                await asyncio.wait(pending)
                for future in pending:
                    if future.exception() is not None and not isinstance(
                            future.exception(), Late):
                        _log_once(future.exception(), "a running launch's read failed")
        finally:
            await aio.offload(pool.shutdown)
        self._forget(launches)

    def _keep(self, launches: list[Launch], finished: Mapping[str, dict[str, Any]]) -> None:
        now = self.clock()
        kept: dict[str, Kept] = {}
        for launch in launches:
            facts = finished.get(launch.request_id)
            if facts is not None:
                kept[launch.request_id] = Kept(launch.session_id, facts, now)
                cache = facts.get("cache")
                if cache is not None and cache.get("until") is not None:
                    self._untils[(launch.request_id, launch.session_id)] = _epoch(
                        cache["until"])
                continue
            last = self.kept.get(launch.request_id)
            if (last is not None and last.session_id == launch.session_id
                    and now - last.read_at < JOIN_KEEP_S):
                kept[launch.request_id] = last
        self.kept = kept

    def _forget(self, launches: list[Launch]) -> None:
        keys = {(launch.request_id, launch.session_id) for launch in launches}
        self._untils = {key: value for key, value in self._untils.items() if key in keys}
        self.windows = {key: value for key, value in self.windows.items() if key in keys}


def _prepared() -> tuple[list[Launch], Any | None, Any | None, Any | None, Any | None]:
    return (take(JOIN_MAX), cache_api(), sessions_api(), first.turns_api(quiet=True),
            comms_api())


__all__ = ["ACTIVITY_LIMIT", "ACTIVITY_TEXT_CHARS", "CACHE_KINDS", "COMMS_TIMEOUT_S",
           "JOIN_BUDGET_S", "JOIN_EVERY", "JOIN_KEEP_S", "JOIN_MAX", "JOIN_THREADS", "READS",
           "UNCOVERED", "UNTIL_SAME_S", "Joiner", "Kept", "Late", "Launch", "Pass", "activity_of",
           "cache_api", "cache_fact", "cache_tick_s", "comms_api", "next_keep_alive",
           "proc_gone", "read_activity", "read_cache", "read_waits", "sessions_api", "take",
           "transcripts_root", "work"]
