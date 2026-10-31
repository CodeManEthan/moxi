"""Recycle: a launched session cleared or compacted on the launcher's own reads, once its
prompt cache has expired or its context has passed its mark.

    ask_expiry(session_id)       # moxi-launcher-job expiry SESSION, asked by cache's timer
    on_crossing(crossing)        # usage's crossing of the launch's limit_at mark
    start_compact(conn, run)     # a limit compact's job, spawned detached

**Recycle on expiry.** Cache's timer asks from the session's cache expiry on, every 120 s,
for a session whose launch stamped `MOXI_LAUNCHER_RECYCLE_ON_EXPIRY` (`cachetimers`). The
timer decides only when to ask; the ask acts only on its own reads, in this order:

1. The launch: launched, with `recycle_on_expiry` set to `clear` or `compact`.
2. No open run on the launch (a stale one, whose job is gone, is ended at the insert).
3. The session (`target.find`): claimed and live.
4. Cache's read of the session: `cold`.
5. The quiet read passes.
6. For a clear, no named hold of the launch chain is open: a hold says the session waits
   on work it handed out, and a clear would drop what it knows of that work. A compact
   keeps a summary, so a hold does not block one.

Then a run (`kind` clear or compact, `mode` expiry, this process its job) and turns'
`command`, with the turn number the quiet read saw.

**A clear stops the session's background shells once the session has been idle an
hour**: they never hold a recycle after that. turns gives no time for a turn's end, so
the idle time read is cache's age of the session's last request, which a turn's end
follows within its last answer. A shorter idle, and every compact, leave shells alone.

Each ask ends one way:

- quietly, writing nothing, for the next ask 120 s on: the cache not cold, a turn or a
  subagent at work, another run open, or turns' refusals that clear by themselves
  (`busy-turn`, `stale-turn`, `rate`, `in-flight`, `loading`);
- `blocked`, with its word: the quiet read's `person`, `draft` or `unreadable`; turns'
  `draft` or `busy-agents`; `held-work` (an open hold, a clear only);
- `failed`, with its word: turns' `not-allowed`, `unsupported`, `gone`, or `failed`;
- `unconfirmed`, when turns' answer did not come in time and the command may still run;
- `cleared` or `compacted`.

**A repeated `blocked` updates one run** (`store.end_blocked`), so a draft left in an idle
session is one run whose start is when the block began.

**Recycle at limit.** `on_crossing` is called in turns' endpoint once per rise of the main
session's context past the launch's `limit_at` mark, with at most 200 ms to answer. It
reads the store, writes at most one row, and returns; it never waits on the session.
`relaunch` writes an event and returns a line that tells the agent to recycle itself;
`compact` writes a compact run and spawns its job, which waits for the session to be idle
(`job.CompactJob`). A compact crossing that meets an open run leaves a pending compact,
which the run's end takes up (`store.end_run`): it is never dropped silently.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moxi import modules, paths, proc
from moxi.paths import Layout

from . import catalog, quiet, slots, store, target

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

RELAUNCH_LINE = ("This launch recycles at its limit: at your next pause point, if you are "
                 "mid-work with more to do, write a handoff and run `moxi launcher relaunch "
                 "--self --handoff <file>` (handoff skill, Recycling yourself).")
COMPACT_LINE = ("This launch compacts at its limit: the launcher compacts this session at "
                "its next idle.")

#: Stands for a seam looked up here.
LOOK: Any = object()


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
               settings: Mapping[str, Any] | None = None,
               environ: Mapping[str, str] | None = None) -> Verdict:
    """One expiry ask of the session `session_id`, to its end. Never raises for what it
    reads; a fault after its run was written ends the run `error`."""
    env = dict(os.environ if environ is None else environ)
    try:
        opened = conn if conn is not None else store.connect(create=False)
    except Exception as error:  # noqa: BLE001  (no launch log: nothing was launched)
        return Verdict(None, "not-launched", f"the launch log could not be opened: "
                                             f"{_words(error)}")
    try:
        return _expiry(opened, session_id, turns, slots_api, cache, settings, env)
    finally:
        if conn is None:
            opened.close()


def _expiry(conn: sqlite3.Connection, session_id: str, turns: Any, slots_api: Any,
            cache: Any, settings: Mapping[str, Any] | None,
            env: Mapping[str, str]) -> Verdict:
    from . import exits, job

    row = store.resolve(conn, session_id)
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
    try:
        found = target.find(session_id, conn=conn, turns=turns, slots_api=api)
    except catalog.Refused as refused:
        return Verdict(None, getattr(refused, "code", None) or "refused", refused.problem)
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
        in_slot = row.target == "slot" and bool(row.slot) and api is not None
        reading = quiet.read(found.session, slot=row.slot if in_slot else None,
                             slots_api=api if in_slot else None,
                             person_s=float(current_settings["relaunch.person_s"]))
        verdict = _held(conn, row, command, reading)
        if verdict is not None and verdict.outcome is None:
            return verdict
        _prune(conn, current_settings)
        try:
            run = store.insert_run(
                conn, kind=command, mode="expiry", request_id=row.request_id,
                session_id=sid, actor=store.LAUNCHER, state="acting", slot=row.slot,
                pane_id=row.pane_id, turn_number=reading.turn_number, job=exits.own_job(),
                gone=gone)
        except store.RunOpen as open_run:
            return Verdict(None, "run-open", str(open_run))
        if verdict is not None:
            return _blocked(conn, run, verdict)
        age = getattr(clock, "age_seconds", None)
        return _act(conn, run, found.session, command, reading.turn_number, age)
    finally:
        found.close()


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
        stop = (command == "clear" and isinstance(age, (int, float)) and age >= SHELLS_IDLE_S
                and STOP_SHELLS in session.capabilities)
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
        except Exception as error:  # noqa: BLE001  (turns' error is the verdict)
            result = error
        verdict = verdict_of(command, result)
        store.add_run_event(conn, run.run_id, "result", {"outcome": verdict.outcome,
                                                         "word": verdict.word,
                                                         "detail": verdict.detail})
        return end(conn, run, verdict)
    except BaseException as error:  # a run never stays open on a fault
        if store.end_run(conn, run.run_id, "error", word=type(error).__name__,
                         detail=_words(error)):
            store.add_run_event(conn, run.run_id, "ended", {"outcome": "error",
                                                            "detail": _words(error)})
        if not isinstance(error, Exception):
            raise
        log.exception("expiry run %s stopped on a fault", run.run_id)
        return Verdict("error", type(error).__name__, _words(error), run.run_id)


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
                env: Mapping[str, str]) -> Any | None:
    """Cache's read of the session, through cache's public API; None without cache."""
    if cache is LOOK:
        cache = None
        if modules.available("cache"):
            found = modules.load("cache", interface=1)
            cache = found.api if found.ok else None
    if cache is None:
        return None
    if row.harness == "codex":
        return cache.read(row.harness, session_id, cache_source=codex_sessions(row, env))
    return cache.read(row.harness, session_id)


def codex_sessions(row: store.LaunchRow, env: Mapping[str, str]) -> str:
    """The Codex home's sessions directory a Codex launch writes to: `CODEX_HOME` from the
    launch's recorded environment, else this process's, else `~/.codex`."""
    home = ((row.base_env or {}).get("CODEX_HOME") or row.env.get("CODEX_HOME")
            or env.get("CODEX_HOME"))
    root = Path(home) if home else Path(env.get("HOME") or Path.home()) / ".codex"
    return str(root / "sessions")


# -- recycle at limit --------------------------------------------------------------------


def on_crossing(crossing: Any) -> str | None:
    """usage's crossing of a session's `limit_at` mark: the line for the session, or None.
    Reads the store, writes at most one row, never waits on the session, and never
    raises."""
    try:
        conn = store.connect(create=False, timeout=CROSSING_BUSY_S)
    except Exception:  # noqa: BLE001  (no launch log: not a launched session)
        return None
    try:
        return crossing_line(conn, crossing, pid=os.getpid())
    except Exception as error:  # noqa: BLE001  (a busy store loses this crossing)
        log.info("a crossing was not taken: %s", _words(error))
        return None
    finally:
        conn.close()


def crossing_line(conn: sqlite3.Connection, crossing: Any, *, pid: int,
                  spawn: Any | None = None) -> str | None:
    """`on_crossing` over an open launch log, for the endpoint process `pid`."""
    session_id = str(getattr(crossing, "session_id", "") or "")
    if not session_id:
        return None
    row = store.resolve(conn, session_id)
    if row is None or not row.endpoint or not target.claims(row.endpoint, pid):
        return None
    value = row.values.get("recycle_at_limit")
    key = store.crossing_key(session_id, getattr(crossing, "mark", None),
                             getattr(crossing, "at", None))
    if value == "relaunch":
        if not _told(conn, row.request_id, key):
            try:
                store.add_event(conn, "recycle-told", {"crossing_key": key},
                                request_id=row.request_id, session_id=session_id)
            except store.Busy:
                pass
            return RELAUNCH_LINE
        return None
    if value != "compact" or store.crossing_known(conn, key):
        return None
    try:
        run = store.insert_run(conn, kind="compact", mode="limit", request_id=row.request_id,
                               session_id=session_id, actor=store.LAUNCHER, state="armed",
                               slot=row.slot, pane_id=row.pane_id, crossing_key=key)
    except store.RunOpen:
        store.pend_compact(conn, row.request_id, key)
        return COMPACT_LINE
    except sqlite3.IntegrityError:
        return None
    start_compact(conn, run, spawn=spawn)
    return COMPACT_LINE


def _told(conn: sqlite3.Connection, request_id: str, key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM events WHERE request_id = ? AND kind = 'recycle-told' "
        "AND json_extract(detail_json, '$.crossing_key') = ? LIMIT 1",
        (request_id, key)).fetchone() is not None


def start_compact(conn: sqlite3.Connection, run: store.Run, *, spawn: Any | None = None,
                  environ: Mapping[str, str] | None = None) -> bool:
    """Spawn a limit compact's job, detached, and record it on the run; say whether it
    started. One that does not start ends the run `failed`, word `not-started`."""
    from .compose import common

    env = dict(os.environ if environ is None else environ)
    spawn = spawn or proc.spawn
    try:
        argv = [str(paths.script("moxi-launcher-job")), "compact", run.run_id]
        cwd = Layout(store.MODULE).state_dir(env)
        pid = spawn(argv, detached=True, env=common.scrub(env, modules.session_env()),
                    cwd=str(cwd))
    except Exception as error:  # noqa: BLE001  (a job that did not start ends its run)
        detail = f"the compact's job did not start: {_words(error)}"
        if store.end_run(conn, run.run_id, "failed", word="not-started", detail=detail):
            store.add_run_event(conn, run.run_id, "ended", {"outcome": "failed",
                                                            "word": "not-started",
                                                            "detail": detail})
        return False
    ident = proc.identity(int(pid))
    job = f"proc:{ident.pid}@{ident.started:.6f}" if ident is not None else f"proc:{pid}"
    try:
        store.set_run_job(conn, run.run_id, job)
    except store.Busy:
        pass  # the job records itself when it starts
    return True


# -- helpers ----------------------------------------------------------------------------


def _prune(conn: sqlite3.Connection, current: Mapping[str, Any]) -> None:
    try:
        store.prune(conn, float(current["records.keep_days"]))
    except Exception as error:  # noqa: BLE001  (retention never stops a run)
        log.warning("the launcher's records were not pruned: %s", error)


def _settings() -> Mapping[str, Any]:
    from . import settings

    return settings.current()


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__


__all__ = ["BLOCKED_REFUSALS", "BUSY_WORDS", "COMPACT_LINE", "CROSSING_BUSY_S", "EXPIRY",
           "FAILED_REFUSALS", "QUIET_REFUSALS", "RAN", "RELAUNCH_LINE", "SHELLS_IDLE_S",
           "STOP_SHELLS", "UNCONFIRMED", "Verdict", "ask_expiry", "cache_read", "codex_sessions",
           "crossing_line", "end", "on_crossing", "start_compact", "verdict_of"]
