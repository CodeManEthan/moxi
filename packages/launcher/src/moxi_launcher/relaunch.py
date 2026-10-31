"""`moxi launcher relaunch`: exit a launched session and launch it again in its slot.

    arm_self(handoff)              # --self --handoff FILE: the session relaunches itself
    call_director(session)         # SESSION_ID | IDENTITY: a director relaunches it
    status(run)                    # --status RUN: the run and its events
    from_record(run)               # --from-record RUN: launch it again from a run's record

Relaunch is offered with turns and slots installed and slots running here; the session
must be a slot launch, claimed, live, on a harness whose turns session offers `exit`.
comms is optional: without it there is no director mode, no name to wait for and no
notices. No launch option gates the verb.

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
and the agent ends its turn; the job waits for the session to be quiet.

**A director's call** needs comms: the caller's identity, proven by its session's claim
(never a variable it sets); the target by session id, or by a bound comms name; the
caller's name, without its scope, among the launch's directors (`values_json`); with
cache installed, a session whose cache is warm or fresh is refused `warm` (it can be
mailed); and the quiet read must pass now, or the call ends `held` (exit 75) with nothing
written. The call then follows the run for `relaunch.follow_s` and prints its outcome;
past that it exits 75 naming `--status`.

**A repair** (`--from-record`) is for a run that exited its session and did not relaunch
it (`relaunch-failed`, `gone`, or `error` after the exit, and a repair that failed so). It
writes a run of its own (`mode` `repair`, `repair_of` the run) and starts the job
`repair-<run>`, which launches the row again from the record under the original run's
hold. It is refused `occupied-by-chain` when the recorded slot already runs a session of
the launch's chain.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from moxi import exit as exits
from moxi import modules, paths
from moxi import store as core_store
from moxi.paths import Layout

from . import catalog, first, handoffs, job, notices, quiet, slots, store, target
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
         "your handoff.\n`moxi launcher relaunch --status {run}` shows what it is waiting "
         "on. End your turn now.")

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
             environ: Mapping[str, str] | None = None, pid: int | None = None,
             turns: Any | None = None, slots_api: Any | None = None,
             settings: Mapping[str, Any] | None = None,
             conn: sqlite3.Connection | None = None) -> Result:
    """Arm a relaunch of the session this process runs in, with `handoff` as the new
    session's first turn. Raises a refusal with nothing written."""
    env = dict(os.environ if environ is None else environ)
    current = settings if settings is not None else _settings()
    with _store(conn) as c:
        found = target.caller(pid=pid, conn=c, turns=turns, slots_api=slots_api)
        try:
            api = offered(found, slots_api)
            row, sid = found.row, found.session_id
            _no_open_run(c, row, api)
            path = check_handoff(handoff, c, row, sid)
            number = _no_live_agents(found.session)
            data = {"session_id": sid, "request_id": row.request_id, "slot": row.slot,
                    "handoff": str(path), "turn_number": number}
            if dry_run:
                return Result(exits.OK, (f"would arm a relaunch of session {sid} in slot "
                                         f"{row.slot}, handoff {path}",),
                              data={**data, "outcome": "dry-run"})
            run_id = store.new_run_id()
            run = store.insert_run(
                c, kind="relaunch", mode="self", request_id=row.request_id, session_id=sid,
                actor=first.actor(env), handoff_path=str(path), slot=row.slot,
                pane_id=row.pane_id, turn_number=number,
                job=f"slots:{job.job_name('relaunch', run_id)}",
                gone=lambda j: job.gone(j, slots_api=api), run_id=run_id)
            failed = start_job(c, run, "relaunch", api, env)
            if failed is not None:
                return failed
            text = ARMED.format(run=run.run_id, slot=row.slot,
                                person=_span(float(current["relaunch.person_s"])))
            return Result(exits.OK, tuple(text.splitlines()), run,
                          {**data, "run_id": run.run_id, "outcome": "armed"})
        finally:
            found.close()


def check_handoff(handoff: str, conn: sqlite3.Connection, row: store.LaunchRow,
                  session_id: str | None, *, cwd: str | None = None) -> Path:
    """The handoff a self relaunch carries, checked: a regular, readable, non-empty file of
    at most `HANDOFF_CAP` bytes, modified after the session's current id was first seen;
    with notes installed, in notes' handoff folder for the launch. Returns its absolute
    path."""
    path = Path(handoff).expanduser()
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    hint = "write the handoff, then arm the relaunch again"
    try:
        info = path.stat()
    except OSError as error:
        raise Refused("handoff", f"the handoff {path} can't be read: {error.strerror or error}",
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
        with open(path, "rb") as handle:
            handle.read(1)
    except OSError as error:
        raise Refused("handoff", f"the handoff {path} can't be read: "
                                 f"{error.strerror or error}", hint) from None
    seen = store.seen_at(conn, row.request_id, session_id) if session_id else None
    if seen is not None and info.st_mtime <= store.parse_time(seen).timestamp():
        raise Refused("handoff", f"the handoff {path} was last written before this session "
                                 f"began ({seen}): it is stale",
                      "write this session's handoff, then arm the relaunch again")
    if modules.available("notes"):
        folder = Path(handoffs.folder_of_row(row, create=False)).resolve()
        if path.resolve().parent != folder:
            raise Refused("handoff", f"the handoff {path} is not in this launch's handoff "
                                     f"folder, {folder}",
                          "write it there (moxi launcher handoff dir prints the folder)")
    return path


def _no_live_agents(session: Any) -> int | None:
    """Refuse a session with a live subagent or teammate (a shell never refuses), and
    return its turn number."""
    try:
        agents = session.agents()
        facts = session.facts()
    except Exception as error:
        raise target.TurnsFailed(error) from error
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
                  environ: Mapping[str, str] | None = None, turns: Any | None = None,
                  slots_api: Any | None = None, comms: Any = LOOK, cache: Any = LOOK,
                  settings: Mapping[str, Any] | None = None,
                  conn: sqlite3.Connection | None = None,
                  clock: Callable[[], float] = time.monotonic,
                  sleep: Callable[[float], None] = time.sleep) -> Result:
    """Relaunch the session `which` (a session id or a bound comms name) as one of its
    directors. Raises a refusal with nothing written; a hold now is a result, exit 75."""
    env = dict(os.environ if environ is None else environ)
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
            _not_warm(row, sid, cache)
            _no_open_run(c, row, api)
            reading = quiet.read(found.session, slot=row.slot, slots_api=api,
                                 person_s=float(current["relaunch.person_s"]))
            data = {"session_id": sid, "request_id": row.request_id, "slot": row.slot,
                    "director": caller}
            if not reading.quiet:
                hold = reading.first
                return Result(exits.TEMPFAIL, (f"held: {hold}; nothing was done, try again "
                                               "later",),
                              data={**data, "outcome": "held", "word": hold.word,
                                    "detail": hold.detail})
            if dry_run:
                return Result(exits.OK, (f"would relaunch session {sid} in slot {row.slot} "
                                         f"for {caller}",),
                              data={**data, "outcome": "dry-run"})
            run_id = store.new_run_id()
            run = store.insert_run(
                c, kind="relaunch", mode="director", request_id=row.request_id,
                session_id=sid, actor=caller, slot=row.slot, pane_id=row.pane_id,
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
    """`which` as a session id: itself when a launch holds it, else the live session
    behind it as a bound comms name."""
    if store.resolve(conn, which) is not None:
        return which
    try:
        sid = comms.session_of(which)
    except Exception as error:  # noqa: BLE001  (comms can't answer: a refusal, not a trace)
        offline = type(error).__name__ == "DaemonOffline"
        raise Refused("comms-offline" if offline else "comms-failed",
                      f"{which} is no session the launcher launched, and comms could not "
                      f"say whose name it is: {error}",
                      "start comms' daemon" if offline else "check moxi doctor",
                      exits.TEMPFAIL if offline else exits.ERROR) from None
    if not sid:
        raise Refused("not-bound", f"{which} is no session the launcher launched, and no "
                                   "comms name bound to a live session",
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


def _bare(comms: Any, name: str) -> str:
    try:
        return comms.split_tag(name)[0]
    except Exception:  # noqa: BLE001
        return name.partition("@")[0]


def _check_director(comms: Any, caller: str, row: store.LaunchRow) -> None:
    """Refuse `not-director` unless the caller's name, without its scope, is one of the
    directors the launch named."""
    name = _bare(comms, caller)
    named = notices.directors(row)
    if name in named or name in {_bare(comms, d) for d in named}:
        return
    raise Refused("not-director", f"{caller} is not a director of launch {row.request_id}",
                  "only a director the launch named may relaunch it; mail its session")


def _not_warm(row: store.LaunchRow, session_id: str, cache: Any) -> None:
    """With cache installed, refuse `warm` for a session whose cache is warm or fresh."""
    if cache is LOOK:
        cache = None
        if modules.available("cache"):
            found = modules.load("cache", interface=1)
            cache = found.api if found.ok else None
    if cache is None:
        return
    try:
        kind = cache.read(row.harness, session_id).kind
    except Exception:  # noqa: BLE001  (an unread cache leaves the director's call)
        return
    if kind in REACHABLE:
        raise Refused("warm", f"session {session_id}'s cache is {kind}: it can take a "
                              "message", "mail it")


# -- status ------------------------------------------------------------------------------


def status(run_id: str, *, conn: sqlite3.Connection | None = None) -> Result:
    """The run and its events, the latest `wait` first."""
    with _store(conn) as c:
        run = store.get_run(c, run_id)
        if run is None:
            raise Refused("no-run", f"there is no run {run_id}", "check the run id")
        events = store.run_events(c, run_id)
    waits = [e for e in events if e.kind == "wait"]
    latest = waits[-1] if waits else None
    lines = [run_line(run)]
    if latest is not None:
        lines.append(f"waiting on: {latest.detail.get('word')}: "
                     f"{latest.detail.get('detail')} (since {latest.at})")
    lines += [f"{e.at} {e.kind}{_detail(e.detail)}" for e in events]
    data = {"run": run.as_json(), "outcome": run.outcome or run.state,
            "wait": None if latest is None else {"at": latest.at, **latest.detail},
            "events": [{"at": e.at, "kind": e.kind, "detail": e.detail} for e in events]}
    return Result(exits.OK, tuple(lines), run, data)


def run_line(run: store.Run) -> str:
    """One line for a run: what it is, and where it stands or how it ended."""
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
            return outcome_result(run)
        if clock() >= deadline:
            return Result(exits.TEMPFAIL, (f"run {run_id} is still {run.state}; moxi launcher "
                                           f"relaunch --status {run_id} shows it",), run,
                          {"run_id": run_id, "outcome": run.state})
        sleep(FOLLOW_POLL_S)


def outcome_result(run: store.Run) -> Result:
    """An ended run as a verb's result."""
    code = (exits.OK if run.outcome == "relaunched"
            else exits.TEMPFAIL if run.outcome == "held" else exits.ERROR)
    return Result(code, (run_line(run),), run,
                  {"run_id": run.run_id, "outcome": run.outcome, "word": run.word,
                   "detail": run.detail, "new_request_id": run.new_request_id})


# -- repair -----------------------------------------------------------------------------


def from_record(run_id: str, *, follow: bool = True,
                environ: Mapping[str, str] | None = None, slots_api: Any | None = None,
                settings: Mapping[str, Any] | None = None,
                conn: sqlite3.Connection | None = None,
                clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep) -> Result:
    """Launch the session of a run that exited it and did not relaunch it, again, from
    the run's record, as the job `repair-<run>`. Raises a refusal with nothing written."""
    env = dict(os.environ if environ is None else environ)
    current = settings if settings is not None else _settings()
    with _store(conn) as c:
        run = store.get_run(c, run_id)
        if run is None:
            raise Refused("no-run", f"there is no run {run_id}", "check the run id")
        if run.kind != "relaunch":
            raise Refused("not-relaunch", f"run {run_id} is a {run.kind} run, not a relaunch",
                          "only a relaunch run is repaired")
        if run.open:
            raise Refused("run-open", f"run {run_id} is still {run.state}",
                          f"moxi launcher relaunch --status {run_id} follows it")
        if not job.repairable(run, store.run_events(c, run_id)):
            raise Refused("not-repairable",
                          f"run {run_id} ended {run.outcome}: only a run that exited its "
                          "session and did not relaunch it is repaired (relaunch-failed, "
                          "gone, or error after the exit)",
                          "relaunch the session again instead")
        root = job.root_run(c, run)
        for other in store.runs(c, request_id=run.request_id):
            if (other.mode == "repair" and other.outcome == "relaunched"
                    and job.root_run(c, other).run_id == root.run_id):
                raise Refused("repaired", f"run {root.run_id} was repaired already, by run "
                                          f"{other.run_id} (launch {other.new_request_id})",
                              "relaunch that launch instead")
        api = slots_api if slots_api is not None else slots.api()
        if api is None or not api.supported().ok:
            raise Refused("no-slots", "a repair needs slots running here", "install moxi-slots")
        row = store.get(c, run.request_id)
        if row is None:
            raise Refused("no-launch", f"the launch log holds no launch {run.request_id}",
                          "check the run id")
        chain = job.chain_of(c, row.request_id)
        recorded = root.slot or row.slot
        try:
            choice = job.choose_slot(api, recorded, root.run_id, chain)
        except api.TryAgain as error:
            raise Refused("slots-busy", f"slots did not answer about slot {recorded}: "
                                        f"{error}", "try again in a moment",
                          exits.TEMPFAIL) from None
        except api.SlotsError as error:
            raise Refused("slots-failed", f"slots could not show slot {recorded}: {error}",
                          "moxi slots list shows the slots", exits.ERROR) from None
        if choice.chain:
            raise Refused("occupied-by-chain", f"{choice.why}: the seat already runs there",
                          f"attach to slot {recorded}; nothing was launched")
        repair_id = store.new_run_id()
        repair = store.insert_run(
            c, kind="relaunch", mode="repair", request_id=run.request_id,
            session_id=run.session_id, actor=first.actor(env),
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
    except Exception as error:
        raise target.TurnsFailed(error) from error
    if not offers:
        raise Refused("no-exit", f"the {row.harness} session does not offer exit through "
                                 "turns", "relaunch needs a harness turns can exit")
    return api


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
    except FileNotFoundError as error:
        why = str(error)
    except api.SlotsError as error:
        why = f"slots did not start it: {str(error) or type(error).__name__}"
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
        opened = store.connect(create=False)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        raise target.NotLaunched() from None
    try:
        yield opened
    finally:
        opened.close()


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
           "job_argv", "offered", "outcome_result", "run_line", "start_job", "status"]
