"""`moxi launcher relaunch`: exit a launched session and launch it again in its slot.

    arm_self(handoff)              # --self --handoff FILE: the session relaunches itself
    call_director(session)         # SESSION_ID | IDENTITY: a director relaunches it
    status(run)                    # --status RUN: the run and its events, exit by its end
    stop_run(run)                  # --stop RUN: stop a run's job
    from_record(run)               # --from-record RUN: launch it again from a run's record

Relaunch is offered with turns and slots installed and slots running here; the session
must be a slot launch, claimed, live, on a harness whose turns session offers `exit`.
comms is optional: without it there is no director mode, no name to wait for and no
notices. A self arm needs a launch made with `--recycle-at-limit relaunch`: a launch that
recycles by compacting, or not at all, is refused `no-self-recycle`, a dry run too, and
so is a scratch launch recorded with the option before a scratch was refused it. A
director's call and a repair need no such launch.

A launch that names no model comes back on the model its session last used (`pin`): the
arm and the call read it and put it in their `--json` (`model`, `model_how`), and the job
reads it again before the exit.

**The launch check.** The arm, the call and a repair check that the relaunch's own launch,
on its pin, would be accepted now (`job.launch_check`: the launch's live checks, its
composition, and its harness found on the child's `PATH`), and refuse `launch-check`
with its words when it would not, the session untouched. The session's own comms name
and slot are not counted against it. A dry run runs the check too and prints the model
and the verdict. The job checks again before the exit, and a repair before its launch.

Each verb refuses (exit 3) with nothing written until its last step, which writes the run
(`runs`, one open run per launch, so a second arm or call loses at insert, naming the
open run) with its job's name and then starts the job, `moxi-launcher-job`, as a slots
job (`relaunch-<run>`, `repair-<run>`). The job runs outside every session and does the
waiting, the exit and the respawn (`job`). A job that does not start ends the run
`not-started` (exit 1). The job is started with this process's environment less every
session's stamps; it takes nothing else of the caller's, and the new session's
environment is built from the launch row and the settings alone.

**A self arm** (in the session's own turn): the caller's session (turns walking up from
this process), the handoff (a regular, readable, non-empty file of at most 64 KiB,
modified after the session's current id was first seen, so a stale one is refused; with
notes installed, in notes' handoff folder for the launch), and no live subagent (a
background shell never refuses: the exit dialog stops it). Then it prints the armed text
and the agent ends its turn, making no further tool call; the job waits for the session
to be quiet. The armed text names the model the session comes back on.

**A director's call** needs comms: the caller's identity, proven by its session's claim
(never a variable it sets); the target by session id (anything shaped like a UUID,
looked up in the launch log only), or else by a bound comms name; the caller among the
launch's directors (`values_json`: a director named with a scope admits that qualified
name only, a plain one the name in the target launch's own scope); with
cache installed, a session whose cache is warm or fresh is refused `warm` (it can be
mailed); and the quiet read must pass now, or the call ends `held` (exit 75) with nothing
written. The call then follows the run for `relaunch.follow_s` and prints its outcome;
past that it exits 75 naming `--status`. A relaunch that came back says the identity, the
slot, the new session, the posture, the model and whether comms has bound the name. An
agent runs the call with a Bash timeout of at least `relaunch.follow_s` plus 30 s.
`--status` exits as the call would: by the run's end, 75 while it runs.

**A stop** (`--stop`) sends the job of an open relaunch or repair run SIGTERM: before the
exit the run ends `stopped` and a self run's session is told by a note; from the exit on,
the stop waits until the relaunch was tried. It reads the job's process from slots and
checks it still runs; a run that is not a relaunch or repair run is refused
`not-relaunch`, one that has ended `not-open`, and one whose job is gone `job-gone`. Anyone
who can run the launcher's CLI as this user may stop a run.

**A repair** (`--from-record`) is for a run that exited its session and did not relaunch
it (`relaunch-failed`, `gone`, `exit-pending` once a person has answered the exit dialog,
or `error` after the exit, and a repair that failed so),
including a run left open by a job that died after the exit, whose job is gone. It
writes a run of its own (`mode` `repair`, `repair_of` the run) and starts the job
`repair-<run>`, which launches the row again from the record under the original run's
hold. It is refused `occupied-by-chain` when the recorded slot already runs a session of
the launch's chain.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from moxi import exit as exits
from moxi import modules, paths
from moxi import store as core_store
from moxi.paths import Layout

from . import catalog, first, handoffs, job, notices, pin, quiet, recycle, slots, store, target
from .compose import common

#: The job script every relaunch run runs as.
JOB_SCRIPT = "moxi-launcher-job"
#: The largest handoff a self relaunch takes, in bytes.
HANDOFF_CAP = 64 * 1024
#: How often a call that follows its run reads it, in seconds.
FOLLOW_POLL_S = 1.0
#: The cache kinds of a session that can be mailed, so a director's call is refused.
REACHABLE = ("warm", "fresh")

ARMED = ("armed {run}: this session exits once its turn has ended and nobody has used its "
         "pane or\nsent it a message for {person}; then it comes back in slot {slot} with "
         "your handoff,\non {model} ({how}). `moxi launcher relaunch --status {run}` shows "
         "what it is waiting on.\nEnd your turn now and make no further tool call.")

#: Stands for a seam the verb looks up itself.
LOOK: Any = object()


class Refused(catalog.Refused):
    """A relaunch verb refused, with nothing written: its short `code`, words and hint."""

    def __init__(self, code: str, problem: str, hint: str, exit_code: int = exits.REFUSED):
        self.code = code
        self.exit_code = exit_code
        super().__init__(None, problem, hint)


@dataclass(frozen=True)
class Result:
    """What a verb did: the code it exits with, its lines, the run it wrote or read, and
    the JSON it prints with `--json` (`outcome` among them)."""

    exit_code: int
    lines: tuple[str, ...]
    run: store.Run | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


# -- the self arm -----------------------------------------------------------------------


def arm_self(handoff: str, *, dry_run: bool = False,
             caller_env: Mapping[str, str] | None = None, pid: int | None = None,
             turns: Any | None = None, slots_api: Any | None = None,
             settings: Mapping[str, Any] | None = None,
             conn: sqlite3.Connection | None = None, sessions: Any = LOOK,
             check: Callable[[Any], Any] | None = None) -> Result:
    """Arm a relaunch of the session this process runs in, with `handoff` as the new
    session's first turn. Raises a refusal with nothing written. `sessions` stands in for
    sessions' API, which the model pin reads through (None: not installed), and `check`
    for the launch check (`job.launch_check`)."""
    env = dict(os.environ if caller_env is None else caller_env)
    current = settings if settings is not None else _settings()
    with _store(conn) as c:
        found = target.caller(pid=pid, conn=c, turns=turns, slots_api=slots_api)
        try:
            api = offered(found, slots_api)
            row, sid = found.row, found.session_id
            may_recycle(row)
            _no_open_run(c, row, api)
            path = check_handoff(handoff, c, row, sid)
            number = _no_live_agents(found.session)
            chosen = _pin(row, sid, sessions, env)
            data = {"session_id": sid, "request_id": row.request_id, "slot": row.slot,
                    "handoff": str(path), "turn_number": number,
                    "model": chosen.model, "model_how": chosen.how}
            _launch_check(row, sid, chosen, check, mode="self", handoff=str(path),
                          by=first.actor(env), env=env)
            if dry_run:
                return Result(exits.OK, (f"would arm a relaunch of session {sid} in slot "
                                         f"{row.slot}, handoff {path}",
                                         _would(chosen)),
                              data={**data, "outcome": "dry-run", "launch_check": "passes"})
            run_id = store.new_run_id()
            run = store.insert_run(
                c, kind="relaunch", mode="self", request_id=row.request_id, session_id=sid,
                by=first.actor(env), handoff_path=str(path), slot=row.slot,
                pane_id=row.pane_id, turn_number=number,
                job=f"slots:{job.job_name('relaunch', run_id)}",
                gone=lambda j: job.gone(j, slots_api=api), run_id=run_id)
            failed = start_job(c, run, "relaunch", api, env)
            if failed is not None:
                return failed
            text = ARMED.format(run=run.run_id, slot=row.slot,
                                person=_span(float(current["relaunch.person_s"])),
                                model=chosen.model or "the harness's default model",
                                how=chosen.how)
            return Result(exits.OK, tuple(text.splitlines()), run,
                          {**data, "run_id": run.run_id, "outcome": "armed"})
        finally:
            found.close()


def may_recycle(row: store.LaunchRow) -> None:
    """Refuse `no-self-recycle` unless the launch was made with `--recycle-at-limit
    relaunch`, the option that lets its session recycle itself, and not in a scratch
    folder (`recycle.SCRATCH_RECYCLE`: a row recorded before a scratch launch was refused
    the option)."""
    value = row.values.get("recycle_at_limit")
    if recycle.scratch_relaunch(row.values):
        problem, hint = recycle.SCRATCH_RECYCLE
        raise Refused("no-self-recycle", f"launch {row.request_id} works in a scratch "
                                         f"folder, and {problem}", hint)
    if value == "relaunch":
        return
    hint = ("finish the work and stop; a launch with --recycle-at-limit relaunch can "
            "recycle itself")
    if value == "compact":
        raise Refused("no-self-recycle", f"launch {row.request_id} recycles at its limit by "
                                         "compacting, so it can't relaunch itself", hint)
    raise Refused("no-self-recycle", f"launch {row.request_id} was started without "
                                     "--recycle-at-limit relaunch, so it can't recycle "
                                     "itself", hint)


def check_handoff(handoff: str, conn: sqlite3.Connection, row: store.LaunchRow,
                  session_id: str | None, *, cwd: str | None = None) -> Path:
    """The handoff a self relaunch carries, checked: a regular file of at most
    `HANDOFF_CAP` bytes of UTF-8 text that is not blank, modified after the session's
    current id was first seen (after the launch was made, when that was not recorded);
    with notes installed, in notes' handoff folder for the launch. Returns its absolute
    path."""
    path = Path(handoff).expanduser()
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    hint = "write the handoff, then arm the relaunch again"
    try:
        info = path.stat()
    except OSError as exc:
        raise Refused("handoff", f"the handoff {path} can't be read: {exc.strerror or exc}",
                      hint) from None
    if not stat.S_ISREG(info.st_mode):
        raise Refused("handoff", f"the handoff {path} is not a regular file", hint)
    if info.st_size == 0:
        raise Refused("handoff", f"the handoff {path} is empty", hint)
    if info.st_size > HANDOFF_CAP:
        raise Refused("handoff", f"the handoff {path} is {info.st_size} bytes, past the "
                                 f"{HANDOFF_CAP} a relaunch carries",
                      "shorten it to the state of the work and its next step")
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8")
    except OSError as exc:
        raise Refused("handoff", f"the handoff {path} can't be read: "
                                 f"{exc.strerror or exc}", hint) from None
    except UnicodeDecodeError:
        raise Refused("handoff", f"the handoff {path} is not UTF-8 text", hint) from None
    if not text.strip():
        raise Refused("handoff", f"the handoff {path} is blank", hint)
    seen = store.seen_at(conn, row.request_id, session_id) if session_id else None
    seen = seen or row.created_at
    if seen and info.st_mtime <= store.parse_time(seen).timestamp():
        raise Refused("handoff", f"the handoff {path} was last written before this session "
                                 f"began ({seen}): it is stale",
                      "write this session's handoff, then arm the relaunch again")
    if modules.available("notes"):
        handoff_dir = Path(handoffs.handoff_dir_of_row(row, create=False)).resolve()
        if path.resolve().parent != handoff_dir:
            raise Refused("handoff", f"the handoff {path} is not in this launch's handoff "
                                     f"folder, {handoff_dir}",
                          "write it there (moxi launcher handoff dir prints the folder)")
    return path


def _no_live_agents(session: Any) -> int | None:
    """Refuse a session with a live subagent or teammate (a shell never refuses), and
    return its turn number."""
    try:
        agents = session.agents()
        facts = session.facts()
    except Exception as exc:
        raise target.TurnsFailed(exc) from exc
    if not agents.complete:
        raise Refused("subagents", "the session's agent list could not be read whole, so a "
                                   "live subagent can't be ruled out",
                      "arm the relaunch again in a moment")
    live = quiet.live_agents(agents)
    if live:
        kinds = ", ".join(sorted({a.kind for a in live}))
        raise Refused("subagents", f"{len(live)} still working in this session ({kinds})",
                      "wait for them to finish, or stop them, then arm the relaunch again")
    return getattr(facts, "turn_number", None)


# -- the director's call ----------------------------------------------------------------


def call_director(which: str, *, dry_run: bool = False, follow: bool = True,
                  caller_env: Mapping[str, str] | None = None, turns: Any | None = None,
                  slots_api: Any | None = None, comms: Any = LOOK, cache: Any = LOOK,
                  settings: Mapping[str, Any] | None = None,
                  conn: sqlite3.Connection | None = None,
                  clock: Callable[[], float] = time.monotonic,
                  sleep: Callable[[float], None] = time.sleep,
                  sessions: Any = LOOK, check: Callable[[Any], Any] | None = None) -> Result:
    """Relaunch the session `which` (a session id or a bound comms name) as one of its
    directors. Raises a refusal with nothing written; a hold now is a result, exit 75.
    `sessions` stands in for sessions' API, which the model pin reads through, and
    `check` for the launch check (`job.launch_check`)."""
    env = dict(os.environ if caller_env is None else caller_env)
    current = settings if settings is not None else _settings()
    if comms is LOOK:
        from . import presets

        comms = presets.comms_api()
    if comms is None:
        raise Refused("no-comms", "a director's relaunch needs comms, which is not installed",
                      "relaunch your own session with --self --handoff FILE")
    caller = _caller_identity(comms, env)
    if not caller:
        raise Refused("no-identity", "this process holds no comms identity that its "
                                     "session's claim proves",
                      "run it from a session launched with a comms name")
    with _store(conn) as c:
        sid = _session_of(c, which, comms)
        found = target.find(sid, conn=c, turns=turns, slots_api=slots_api)
        try:
            api = offered(found, slots_api)
            row, sid = found.row, found.session_id
            if _qualified(comms, row) == caller:
                raise Refused("self", f"session {sid} is your own",
                              "relaunch it with --self --handoff FILE")
            _check_director(comms, caller, row)
            not_warm(row, sid, cache)
            _no_open_run(c, row, api)
            reading = quiet.read(found.session, slot=row.slot, slots_api=api,
                                 person_s=float(current["relaunch.person_s"]))
            chosen = _pin(row, sid, sessions, env)
            data = {"session_id": sid, "request_id": row.request_id, "slot": row.slot,
                    "director": caller, "model": chosen.model, "model_how": chosen.how}
            if not reading.quiet:
                hold = reading.first
                return Result(exits.TEMPFAIL, (f"held: {hold}; nothing was done, try again "
                                               "later",),
                              data={**data, "outcome": "held", "word": hold.word,
                                    "detail": hold.detail})
            _launch_check(row, sid, chosen, check, mode="director", handoff=None, by=caller,
                          env=env)
            if dry_run:
                return Result(exits.OK, (f"would relaunch session {sid} in slot {row.slot} "
                                         f"for {caller}", _would(chosen)),
                              data={**data, "outcome": "dry-run", "launch_check": "passes"})
            run_id = store.new_run_id()
            run = store.insert_run(
                c, kind="relaunch", mode="director", request_id=row.request_id,
                session_id=sid, by=caller, slot=row.slot, pane_id=row.pane_id,
                turn_number=reading.turn_number,
                job=f"slots:{job.job_name('relaunch', run_id)}",
                gone=lambda j: job.gone(j, slots_api=api), run_id=run_id)
            failed = start_job(c, run, "relaunch", api, env)
            if failed is not None:
                return failed
        finally:
            found.close()
        if not follow:
            return Result(exits.OK, (f"run {run.run_id} started; moxi launcher relaunch "
                                     f"--status {run.run_id} follows it",), run,
                          {**data, "run_id": run.run_id, "outcome": "started"})
        return follow_run(c, run.run_id, float(current["relaunch.follow_s"]),
                          clock=clock, sleep=sleep)


def _caller_identity(comms: Any, env: Mapping[str, str]) -> str | None:
    try:
        return comms.caller_identity(env)
    except Exception:  # noqa: BLE001  (an identity that can't be proven is none)
        return None


def _session_of(conn: sqlite3.Connection, which: str, comms: Any) -> str:
    """`which` as a session id. Shaped like one (a UUID), it is looked up in the launch log
    only: one no launch holds is refused `not-launched`, and comms is never asked. Anything
    else is a comms identity: the live session behind it as a bound name."""
    if target.session_id_shaped(which):
        if store.resolve(conn, which) is None:
            raise target.NotLaunched(f"session {which} was not launched by the launcher")
        return which
    try:
        sid = comms.session_of(which)
    except Exception as exc:  # noqa: BLE001  (comms can't answer: a refusal, not a trace)
        offline = type(exc).__name__ == "DaemonOffline"
        raise Refused("comms-offline" if offline else "comms-failed",
                      f"comms could not say which session {which} is bound to: {exc}",
                      "start comms' daemon" if offline else "check moxi doctor",
                      exits.TEMPFAIL if offline else exits.ERROR) from None
    if not sid:
        raise Refused("not-bound", f"{which} is no comms name bound to a live session",
                      "give a session id, or the comms name of a live session")
    return sid


def _qualified(comms: Any, row: store.LaunchRow) -> str | None:
    if not row.identity:
        return None
    scope = row.scope or getattr(comms, "GLOBAL", "")
    try:
        return comms.qualify(row.identity, scope)
    except Exception:  # noqa: BLE001  (comms' own form unknown: the plain one)
        return f"{row.identity}@{row.scope}" if row.scope else row.identity


def _split(comms: Any, name: str) -> tuple[str, str | None]:
    try:
        return comms.split_tag(name)
    except Exception:  # noqa: BLE001  (comms' own form unknown: the plain one)
        bare, sep, scope = name.partition("@")
        return bare, scope if sep else None


def is_director(comms: Any, caller: str, named: list[str], scope: str | None = None) -> bool:
    """Whether the qualified identity `caller` is one of the directors `named`: a director
    named with a scope admits that qualified name only (the global scope spelled bare, as
    comms spells it); a plain name is read in `scope`, the target launch's own (the
    global scope when None), and admits that qualified name only."""
    for director in named:
        name, named_scope = _split(comms, director)
        if named_scope is None:
            named_scope = scope or getattr(comms, "GLOBAL", "global")
        try:
            qualified = comms.qualify(name, named_scope)
        except Exception:  # noqa: BLE001
            qualified = (name if named_scope == getattr(comms, "GLOBAL", "global")
                         else f"{name}@{named_scope}")
        if qualified == caller:
            return True
    return False


def _check_director(comms: Any, caller: str, row: store.LaunchRow) -> None:
    """Refuse `not-director` unless the caller is one of the directors the launch named."""
    if is_director(comms, caller, notices.directors(row), row.scope or None):
        return
    raise Refused("not-director", f"{caller} is not a director of launch {row.request_id}",
                  "only a director the launch named may relaunch it; mail its session")


def not_warm(row: store.LaunchRow, session_id: str, cache: Any) -> None:
    """With cache installed, refuse `warm` for a session whose cache is warm or fresh."""
    try:
        clock = recycle.cache_read(recycle.LOOK if cache is LOOK else cache, row,
                                   session_id, os.environ)
    except Exception:  # noqa: BLE001  (an unread cache leaves the director's call)
        return
    if clock is not None and clock.kind in REACHABLE:
        raise Refused("warm", f"session {session_id}'s cache is {clock.kind}: it can take a "
                              "message", "mail it")


# -- status ------------------------------------------------------------------------------


def status(run_id: str, *, conn: sqlite3.Connection | None = None) -> Result:
    """The run and its events, the latest `wait` first. It exits as a director's call
    that followed the run would: an ended run by its outcome (`outcome_result`), an open
    one 75."""
    with _store(conn) as c:
        run = store.get_run(c, run_id)
        if run is None:
            raise Refused("no-run", f"there is no run {run_id}", "check the run id")
        events = store.run_events(c, run_id)
    waits = [e for e in events if e.kind == "wait"]
    latest = waits[-1] if waits else None
    lines = [run_line(run, _ended(events))]
    if latest is not None:
        lines.append(f"waiting on: {latest.detail.get('word')}: "
                     f"{latest.detail.get('detail')} (since {latest.at})")
    lines += [f"{e.at} {e.kind}{_detail(e.detail)}" for e in events]
    data = {"run": run.as_json(), "outcome": run.outcome or run.state,
            "wait": None if latest is None else {"at": latest.at, **latest.detail},
            "events": [{"at": e.at, "kind": e.kind, "detail": e.detail} for e in events]}
    code = exits.TEMPFAIL if run.open else outcome_result(run).exit_code
    return Result(code, tuple(lines), run, data)


#: What a stop sends a run's job.
STOP_SIGNAL = signal.SIGTERM


def stop_run(run_id: str, *, conn: sqlite3.Connection | None = None,
             slots_api: Any | None = None,
             kill: Callable[[int, int], None] = os.kill) -> Result:
    """Send the job of the open relaunch or repair run `run_id` SIGTERM, and say what that
    does. Refused, with nothing sent: no such run (`no-run`), a run of another kind
    (`not-relaunch`), an ended run (`not-open`), and a run whose job is gone or can't be
    read (`job-gone`). A repair's session is down already, so its job only records the
    stop and ends after the launch is tried; the line says so. `kill` stands in for
    `os.kill`."""
    from moxi import proc

    with _store(conn) as c:
        run = store.get_run(c, run_id)
    if run is None:
        raise Refused("no-run", f"there is no run {run_id}", "check the run id")
    if run.kind != "relaunch":
        raise Refused("not-relaunch", f"run {run_id} is a {run.kind} run, not a relaunch "
                                      "or a repair", "only a relaunch or repair run is stopped")
    if not run.open:
        raise Refused("not-open", f"run {run_id} has ended: {run.outcome}",
                      f"moxi launcher relaunch --status {run_id} shows it")
    kind, _, name = (run.job or "").partition(":")
    api = slots_api if slots_api is not None else slots.api()
    found = None
    if kind == "slots" and name and api is not None:
        try:
            found = api.job(name)
        except Exception:  # noqa: BLE001  (a job that can't be read is not stopped)
            found = None
    pid = getattr(found, "pane_pid", None) if getattr(found, "running", False) else None
    if not isinstance(pid, int) or proc.identity(pid) is None:
        raise Refused("job-gone", f"run {run_id}'s job {run.job or '(none)'} is not running",
                      f"nothing was sent; moxi launcher relaunch --status {run_id} shows "
                      "the run, and --from-record repairs one that exited its session")
    try:
        kill(pid, STOP_SIGNAL)
    except ProcessLookupError:
        raise Refused("job-gone", f"run {run_id}'s job ended before the stop reached it",
                      f"moxi launcher relaunch --status {run_id} shows the run") from None
    said = ("its session is down already, so the stop is recorded and the run ends after "
            "the launch is tried" if run.mode == "repair"
            else "it ends stopped before the exit; from the exit on, it ends after the "
                 "relaunch is tried")
    return Result(exits.OK, (f"stop sent to run {run_id}: {said}",), run,
                  {"run_id": run_id, "outcome": "stop-sent", "pid": pid})


def run_line(run: store.Run, ended: Mapping[str, Any] | None = None) -> str:
    """One line for a run: what it is, and where it stands or how it ended. A director's
    run that relaunched its session, with its `ended` event's detail, says what came
    back: the identity, the slot, the new session, the posture, the model and whether
    comms has bound the name."""
    if run.mode == "director" and run.outcome == "relaunched" and ended:
        return _came_back_line(run, ended)
    what = f"run {run.run_id}: {run.kind} ({run.mode}) of session {run.session_id}"
    if run.slot:
        what += f" in slot {run.slot}"
    if run.open:
        return f"{what}: {run.state}"
    said = f"{what}: {run.outcome}"
    if run.word:
        said += f" ({run.word})"
    if run.new_request_id:
        said += f", new launch {run.new_request_id}"
    if run.detail:
        said += f": {run.detail}"
    return said


def _came_back_line(run: store.Run, ended: Mapping[str, Any]) -> str:
    who = ended.get("identity") or f"session {run.session_id}"
    slot = f" in slot {run.slot}" if run.slot else ""
    session = ended.get("session_id") or "not known yet"
    model = ended.get("model") or "the harness's default model"
    said = (f"run {run.run_id}: relaunched {who}{slot} as session {session}, posture "
            f"{ended.get('posture') or 'with:' + run.actor}, model {model} "
            f"({ended.get('model_how') or 'not read'})")
    state = ended.get("identity_state")
    if state:
        said += "; comms bound" if state == "held" else "; comms not yet bound"
    if run.word:
        said += f"; flagged {run.word}"
        if run.detail:
            said += f": {run.detail}"
    if run.new_request_id:
        said += f"; new launch {run.new_request_id}"
    return said


def _ended(events: Sequence[store.RunEvent]) -> Mapping[str, Any] | None:
    """The detail of a run's last `ended` event, or None."""
    ends = [e for e in events if e.kind == "ended"]
    return ends[-1].detail if ends else None


def _detail(detail: Mapping[str, Any]) -> str:
    shown = {k: v for k, v in detail.items() if v not in (None, "") and k != "text"}
    return (" " + ", ".join(f"{k} {v}" for k, v in shown.items())) if shown else ""


def follow_run(conn: sqlite3.Connection, run_id: str, follow_s: float, *,
               clock: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], None] = time.sleep) -> Result:
    """Follow a run until it ends or `follow_s` passes: its outcome (0 relaunched, 75
    held, 1 any other end), or 75 naming `--status` while it still runs."""
    deadline = clock() + follow_s
    while True:
        run = store.get_run(conn, run_id)
        if run is None:
            raise Refused("no-run", f"there is no run {run_id}", "check the run id")
        if not run.open:
            return outcome_result(run, _ended(store.run_events(conn, run_id)))
        if clock() >= deadline:
            return Result(exits.TEMPFAIL, (f"run {run_id} is still {run.state}; moxi launcher "
                                           f"relaunch --status {run_id} shows it",), run,
                          {"run_id": run_id, "outcome": run.state})
        sleep(FOLLOW_POLL_S)


def outcome_result(run: store.Run, ended: Mapping[str, Any] | None = None) -> Result:
    """An ended run as a verb's result: 0 relaunched, 75 held, 1 any other end. `ended`
    is its `ended` event's detail, for the line."""
    code = (exits.OK if run.outcome == "relaunched"
            else exits.TEMPFAIL if run.outcome == "held" else exits.ERROR)
    return Result(code, (run_line(run, ended),), run,
                  {"run_id": run.run_id, "outcome": run.outcome, "word": run.word,
                   "detail": run.detail, "new_request_id": run.new_request_id})


# -- repair -----------------------------------------------------------------------------


def _refuse_live(row: store.LaunchRow, session_id: str, turns: Any) -> None:
    """Refuse a repair of a session that is live, or whose liveness can't be read."""
    from . import resume

    api = first.turns_api(quiet=True) if turns is LOOK else turns
    live, unread = resume.liveness(row, session_id, api)
    if live is None:
        raise Refused("liveness-unread", f"whether session {session_id} is live can't be "
                                         f"read: {unread}",
                      "check `moxi doctor`, then try again; nothing was launched",
                      exits.ERROR)
    if live:
        raise Refused("live", f"session {session_id} is live: the run did not take it "
                              "down", "nothing was launched; relaunch it again if it still "
                                      "needs one")


def from_record(run_id: str, *, follow: bool = True,
                caller_env: Mapping[str, str] | None = None, slots_api: Any | None = None,
                turns: Any = LOOK, settings: Mapping[str, Any] | None = None,
                conn: sqlite3.Connection | None = None,
                clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep, sessions: Any = LOOK,
                check: Callable[[Any], Any] | None = None) -> Result:
    """Launch the session of a run that exited it and did not relaunch it, again, from
    the run's record, as the job `repair-<run>`. Raises a refusal with nothing written.

    A run still open whose job is gone is judged as ended `exc`, word `job-gone`, the
    end the repair's own insert writes for it. A run's session that is live after all (a
    refused exit, or a watch that broke while the session ran) is refused `live`, and one
    whose liveness can't be read `liveness-unread`: a repair never starts a second copy
    of the seat. Its launch, on the original run's model pin, is checked before anything
    is written, and refused `launch-check` when it would be refused. `turns` stands in
    for turns' API (None: not installed), `sessions` for sessions' and `check` for the
    launch check."""
    env = dict(os.environ if caller_env is None else caller_env)
    current = settings if settings is not None else _settings()
    with _store(conn) as c:
        run = store.get_run(c, run_id)
        if run is None:
            raise Refused("no-run", f"there is no run {run_id}", "check the run id")
        if run.kind != "relaunch":
            raise Refused("not-relaunch", f"run {run_id} is a {run.kind} run, not a relaunch",
                          "only a relaunch run is repaired")
        api = slots_api if slots_api is not None else slots.api()
        judged = run
        if run.open:
            # A run whose job is gone is judged as the repair's insert will end it.
            if not store.run_stale(run, lambda j: job.gone(j, slots_api=api)):
                raise Refused("run-open", f"run {run_id} is still {run.state}",
                              f"moxi launcher relaunch --status {run_id} follows it")
            outcome, word = store.JOB_GONE
            judged = replace(run, state="ended", outcome=outcome, word=word)
        if not job.repairable(judged, store.run_events(c, run_id)):
            raise Refused("not-repairable",
                          f"run {run_id} ended {judged.outcome}: only a run that exited its "
                          "session and did not relaunch it is repaired (relaunch-failed, "
                          "gone, exit-pending, or error after the exit)",
                          "relaunch the session again instead")
        root = job.root_run(c, run)
        for other in store.runs(c, request_id=run.request_id):
            if (other.mode == "repair" and other.outcome == "relaunched"
                    and job.root_run(c, other).run_id == root.run_id):
                raise Refused("repaired", f"run {root.run_id} was repaired already, by run "
                                          f"{other.run_id} (launch {other.new_request_id})",
                              "relaunch that launch instead")
        if api is None or not api.supported().ok:
            raise Refused("no-slots", "a repair needs slots running here", "install moxi-slots")
        row = store.get(c, run.request_id)
        if row is None:
            raise Refused("no-launch", f"the launch log holds no launch {run.request_id}",
                          "check the run id")
        _refuse_live(row, run.session_id, turns)
        chain = job.chain_of(c, row.request_id)
        recorded = root.slot or row.slot
        try:
            choice = job.choose_slot(api, recorded, root.run_id, chain)
        except api.TryAgain as exc:
            raise Refused("slots-busy", f"slots did not answer about slot {recorded}: "
                                        f"{exc}", "try again in a moment",
                          exits.TEMPFAIL) from None
        except api.SlotsError as exc:
            raise Refused("slots-failed", f"slots could not show slot {recorded}: {exc}",
                          "moxi slots list shows the slots", exits.ERROR) from None
        if choice.chain:
            raise Refused("occupied-by-chain", f"{choice.why}: the seat already runs there",
                          f"attach to slot {recorded}; nothing was launched")
        chosen = job.recorded_pin(c, root, row,
                                  sessions=job.LOOK if sessions is LOOK else sessions)
        request = job.replay_request(
            row, run_id, mode=root.mode, handoff=root.handoff_path, by=root.actor,
            slot=choice.slot, respawn=choice.respawn,
            hold=root.run_id if choice.recorded else None,
            model_pin=chosen.model if chosen.pinned else None)
        refused = (check(request) if check is not None
                   else job.launch_check(request, env=env))
        if refused is not None:
            raise Refused("launch-check", f"the repair's launch would be refused "
                                          f"({refused.code}): {refused.problem}",
                          f"{refused.hint or 'fix that'}; nothing was launched, and the "
                          "repair can be repeated")
        repair_id = store.new_run_id()
        repair = store.insert_run(
            c, kind="relaunch", mode="repair", request_id=run.request_id,
            session_id=run.session_id, by=first.actor(env),
            handoff_path=root.handoff_path, slot=recorded, pane_id=root.pane_id,
            repair_of=run.run_id, job=f"slots:{job.job_name('repair', repair_id)}",
            gone=lambda j: job.gone(j, slots_api=api), run_id=repair_id)
        failed = start_job(c, repair, "repair", api, env)
        if failed is not None:
            return failed
        if not follow:
            return Result(exits.OK, (f"repair {repair_id} of run {run_id} started; moxi "
                                     f"launcher relaunch --status {repair_id} follows it",),
                          repair, {"run_id": repair_id, "outcome": "started"})
        return follow_run(c, repair_id, float(current["relaunch.follow_s"]), clock=clock,
                          sleep=sleep)


# -- what the verbs share -------------------------------------------------------------------


def offered(found: target.Target, slots_api: Any | None = None) -> Any:
    """Refuse a target relaunch is not offered for: slots not installed or not running
    here, a launch not in a slot, a session not live, or one whose turns session offers
    no `exit`. Returns slots' API."""
    api = slots_api if slots_api is not None else slots.api()
    if api is None:
        raise Refused("no-slots", "relaunch needs slots, which is not installed",
                      "install moxi-slots")
    support = api.supported()
    if not support.ok:
        raise Refused("no-slots", f"relaunch needs slots, which does not run here: "
                                  f"{support.why or 'slots says so'}",
                      "relaunch runs on Linux, macOS and WSL2")
    row = found.row
    if row.target != "slot" or not row.slot:
        raise Refused("not-slot", f"launch {row.request_id} runs in its {row.target}; only a "
                                  "slot launch is relaunched",
                      "launch the seat in a slot (--target slot) to relaunch it")
    if not found.live or found.session is None:
        raise Refused("not-live", f"session {found.session_id} is not live",
                      "resume it instead")
    try:
        offers = "exit" in found.session.capabilities
    except Exception as exc:
        raise target.TurnsFailed(exc) from exc
    if not offers:
        raise Refused("no-exit", f"the {row.harness} session does not offer exit through "
                                 "turns", "relaunch needs a harness turns can exit")
    return api


def _pin(row: store.LaunchRow, session_id: str, sessions: Any,
         env: Mapping[str, str]) -> pin.Pin:
    """The model a relaunch of the session would come back on, as the job reads it."""
    return pin.choose(row, session_id, sessions=pin.LOOK if sessions is LOOK else sessions,
                      env=env)


def _launch_check(row: store.LaunchRow, session_id: str, chosen: pin.Pin,
                  check: Callable[[Any], Any] | None, *, mode: str, handoff: str | None,
                  by: str, env: Mapping[str, str]) -> None:
    """Refuse `launch-check` when the relaunch's own launch, on its pin, would be
    refused now; the session's own comms name and slot are not counted against it."""
    request = job.replay_request(
        row, "check", mode=mode, handoff=handoff, by=by, slot=row.slot, hold=None,
        respawn=True, director=mode == "director",
        model_pin=chosen.model if chosen.pinned else None)
    request = replace(request, replacing=session_id)
    refused = (check(request) if check is not None
               else job.launch_check(request, env=env))
    if refused is not None:
        raise Refused("launch-check", f"the relaunch's launch would be refused "
                                      f"({refused.code}): {refused.problem}",
                      str(refused.hint or "fix that, then try again"))


def _would(chosen: pin.Pin) -> str:
    """A dry run's line on the model and the launch check."""
    model = chosen.model or "the harness's default model"
    return f"it would come back on {model} ({chosen.how}); its launch check passes"


def _no_open_run(conn: sqlite3.Connection, row: store.LaunchRow, api: Any) -> None:
    """Refuse a launch with an open run whose job still runs, naming it; the insert
    itself is the rule, this says so before anything else is checked."""
    run = store.open_run(conn, row.request_id)
    if run is not None and not store.run_stale(run, lambda j: job.gone(j, slots_api=api)):
        raise store.RunOpen(run)


def job_argv(kind: str, run_id: str) -> list[str]:
    """The job's command: `moxi-launcher-job KIND RUN`, from this environment's scripts."""
    return [str(paths.script(JOB_SCRIPT)), kind, run_id]


def start_job(conn: sqlite3.Connection, run: store.Run, kind: str, api: Any,
              env: Mapping[str, str]) -> Result | None:
    """Start the run's job as a slots job, with `env` less every session's stamps, in the
    launcher's state folder. None when it started; a job that did not start ends the run
    `not-started`, and that is the result (exit 1)."""
    name = job.job_name(kind, run.run_id)
    try:
        argv = job_argv(kind, run.run_id)
        cwd = Layout(store.MODULE).state_dir(env)
        started = api.run_job(name, argv, env=common.scrub(env, modules.session_env()),
                              cwd=str(cwd))
    except FileNotFoundError as exc:
        why = str(exc)
    except api.SlotsError as exc:
        why = f"slots did not start it: {str(exc) or type(exc).__name__}"
    else:
        if started.exec in ("started", "unconfirmed"):
            return None
        why = started.exec_line or started.exec or "no verdict"
    detail = f"job {name} did not start: {why}"
    if store.end_run(conn, run.run_id, "not-started", word="not-started", detail=detail):
        store.add_run_event(conn, run.run_id, "ended", {"outcome": "not-started",
                                                        "detail": detail})
    ended = store.get_run(conn, run.run_id)
    return Result(exits.ERROR, (f"run {run.run_id} did not start: {why}",), ended,
                  {"run_id": run.run_id, "outcome": "not-started", "detail": detail})


@contextmanager
def _store(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
        return
    try:
        store_conn = store.connect(create=False)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        raise target.NotLaunched() from None
    try:
        yield store_conn
    finally:
        store_conn.close()


def _settings() -> Mapping[str, Any]:
    from . import settings

    return settings.current()


def _span(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds:g} seconds"


__all__ = ["ARMED", "HANDOFF_CAP", "JOB_SCRIPT", "LOOK", "REACHABLE", "Refused", "Result",
           "arm_self", "call_director", "check_handoff", "follow_run", "from_record",
           "is_director", "job_argv", "may_recycle", "not_warm", "offered", "outcome_result", "run_line",
           "start_job", "status", "stop_run"]
