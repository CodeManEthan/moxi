"""Page jobs: an act from the browser page, run as the same verb in the user's terminal.

The page server never runs an install on its own threads. Each act that writes the
manifest or installs (`add`, `remove`, `enable`, `disable`, `global mode`, `global remove`,
`install`) starts a process of its own, the runner:

    python -P -m moxi_loadout.pagejob <job> <verb> [<argument>...]

The runner first removes job records older than a day. Then, before anything is written,
it asks ui's check whether the job would read and write where the user's last run in a
terminal did (`moxi_loadout.homes`), and refuses (exit 3) with its one line when it
wouldn't, when nothing is recorded, or when ui's check can't be loaded. Then it runs the
verb through the CLI's own code path, `moxi_loadout.cli.main` with `--json`, on its main
thread, so the CLI's deferred signals and lock waits hold as they do in a terminal. It
sets loadout's marker, so what the run says is worded for the browser page.

A job's record is two files under `STATE/loadout/jobs/`, each field with one writer:

- `<job>.json`, created by the page server (`start`) before the spawn, exclusively, so a
  job id is used once: `{"job", "argv", "created"}`. Right after the spawn the page
  server adds the job's `pid` and `started` (its start time, `moxi.proc.identity`). A
  spawn that fails removes the file; a pid that can't be added after the spawn is
  logged, and the act still answers that the job started.
- `<job>.end.json`, written by the runner in one write as its last act: `{"exit",
  "outcome", "lines"}`. `outcome` is what the verb printed with `--json`, or None when
  it printed nothing that reads; `lines` is what was said outside it: the check's
  refusal, the CLI's own error lines, or an error outside the CLI, the log's setup
  included, written as exit 1.

The runner's own stdout and stderr go to `<job>.log` beside them, which holds anything
only when the runner fails before it can write its end: when loadout's package itself
can't be imported, or the end can't be written.

`read` gives a job in one of three states: `done` once its end is written; `running`
while it has no pid and is a few seconds old, or while its pid is alive with that start
time; otherwise `interrupted`, with the line to run it again. A stop of the browser
shell, a kill or a restart each end a job this way; the CLI's deferred signals keep the
harness files and the ledger consistent with each other.

The job runs from `STATE/loadout`, with stdin closed and the page server's environment
unchanged, so the check sees what the install will use. The page server sends
exact section names and absolute paths, a section named by a path as its absolute root,
never a path relative to a directory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from moxi_loadout.homes import MARKER, PAGE_JOB

#: The folder of job records, in loadout's state directory.
JOBS = "jobs"
#: How old a job record gets, in seconds, before the next job's runner removes it.
KEEP_SECONDS = 24 * 3600
#: How long a record with no pid yet reads as running, in seconds.
START_GRACE = 10.0
#: The verbs a page job runs: each that writes the manifest or installs.
VERBS = frozenset({"install", "add", "remove", "enable", "disable", "global"})
#: The line of a job that stopped before it wrote its end.
INTERRUPTED = ("the job stopped before it finished (the browser shell stopped, the "
               "process was killed, or the machine restarted); run it again")
#: The value of loadout's marker a page job sets.
CALLER = PAGE_JOB

log = logging.getLogger("moxi.loadout.pagejob")

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_END = ".end.json"
_LOG = ".log"
_TMP = ".tmp"


class Clash(Exception):
    """A job id already names a job with other arguments."""


@dataclass(frozen=True)
class Started:
    """A job `start` was asked for: its id, and whether this call started it (False when
    a record of that id and those arguments was there already)."""

    job: str
    new: bool


# -- where records live ---------------------------------------------------------------------

def jobs_dir(*, create: bool = False) -> Path:
    """`STATE/loadout/jobs/`; with `create`, made private when missing."""
    from moxi_loadout import stores

    path = stores.state_dir(create=create) / JOBS
    if create:
        from moxi.files import private_dir

        private_dir(path)
    return path


def valid(job: object) -> bool:
    """Whether `job` can name a job: 1 to 64 letters, digits, `-` and `_`. An
    idempotency key (43 base64url characters) is one."""
    return isinstance(job, str) and _ID.fullmatch(job) is not None


def new_id() -> str:
    """A fresh random job id."""
    import secrets

    return secrets.token_urlsafe(18)


def record_path(job: str) -> Path:
    return jobs_dir() / f"{_checked(job)}.json"


def end_path(job: str) -> Path:
    return jobs_dir() / f"{_checked(job)}{_END}"


def log_path(job: str) -> Path:
    """Where the runner's own output goes, should it fail before it can write its end."""
    return jobs_dir() / f"{_checked(job)}{_LOG}"


def _checked(job: str) -> str:
    if not valid(job):
        raise ValueError(f"{job!r} is not a job id")
    return job


def command(job: str, argv: Sequence[str]) -> list[str]:
    """The runner's command line for job `job` running verb `argv`."""
    from moxi import paths

    return [os.fspath(paths.python()), "-P", "-m", "moxi_loadout.pagejob", job, *argv]


# -- the page server's side -----------------------------------------------------------------

# The call the runner is started through, named here so a test can stand in for it.
def _spawn(argv: list[str], *, cwd: str, output: str) -> int:
    from moxi import proc

    return proc.spawn(argv, detached=True, cwd=cwd, output=output)


def start(argv: Sequence[str], *, job: str | None = None) -> Started:
    """Start a job running verb `argv` (no `--json`; the runner adds it), under id `job`
    (a fresh one when None). The record is created exclusively before the spawn and
    gets the job's pid and start time right after it. A record of `job` with the same
    arguments is found, not started again (`Started.new` False), so a confirmed act's
    retry after a restart of the page server reaches the job it started; one with other
    arguments raises `Clash`. A spawn that fails removes the record and raises. Once the
    spawn returns the job is running, so a pid that can't be recorded is logged, never
    raised: the job then reads `interrupted` from the grace period until its end lands."""
    from moxi import proc
    from moxi.files import atomic_write

    from moxi_loadout import stores

    argv = [str(a) for a in argv]
    if not argv or argv[0] not in VERBS:
        raise ValueError(f"{' '.join(argv) or 'nothing'} is not a verb a page job runs")
    job = new_id() if job is None else _checked(job)
    jobs_dir(create=True)
    path = record_path(job)
    record = {"job": job, "argv": argv, "created": time.time()}
    try:
        _create(path, json.dumps(record) + "\n")
    except FileExistsError:
        found = _load(path)
        if found is not None and found.get("argv") == argv:
            return Started(job, False)
        raise Clash(f"job {job} was started already with other arguments") from None
    try:
        pid = _spawn(command(job, argv), cwd=os.fspath(stores.state_dir(create=True)),
                     output=os.fspath(log_path(job)))
    except BaseException:
        path.unlink(missing_ok=True)
        log_path(job).unlink(missing_ok=True)
        raise
    # The job runs from here on, so nothing below may answer as if it didn't start.
    try:
        ident = proc.identity(pid)
        record.update(pid=pid, started=None if ident is None else ident.started)
        atomic_write(path, json.dumps(record) + "\n", mode=0o600)
    except Exception:  # noqa: BLE001 - the job reads interrupted until its end lands
        log.exception("page job %s started (pid %s), but its pid wasn't recorded", job, pid)
    return Started(job, True)


def _create(path: Path, text: str) -> None:
    """Make file `path` holding `text`, 0600, only if nothing has that name: it is written
    whole beside its name and then linked to it, which fails when the name is taken, so
    no reader ever sees it half written. Raises `FileExistsError` when it is there."""
    import secrets

    from moxi.files import atomic_write

    written = path.with_name(f".{path.name}.{secrets.token_hex(8)}{_TMP}")
    atomic_write(written, text, mode=0o600)
    try:
        os.link(written, path)
    finally:
        written.unlink(missing_ok=True)


def read(job: str) -> dict | None:
    """Job `job` as the page shows it, or None when there is no such job:
    `{"job", "argv", "state", "exit", "outcome", "lines"}`, `state` one of `done`,
    `running` and `interrupted`; `exit`, `outcome` and `lines` as the runner wrote them
    once done, an interrupted job's `lines` saying to run it again."""
    if not valid(job):
        return None
    end = _load(end_path(job))
    record = _load(record_path(job))
    if end is None and record is None:
        return None
    out = {"job": job, "argv": (record or {}).get("argv"), "state": "running",
           "exit": None, "outcome": None, "lines": []}
    if end is not None:
        return _done(out, end)
    if _running(record):
        return out
    # The process may have written its end between the two reads.
    end = _load(end_path(job))
    if end is not None:
        return _done(out, end)
    out.update(state="interrupted", lines=[INTERRUPTED])
    return out


def _done(out: dict, end: dict) -> dict:
    code = end.get("exit")
    lines = end.get("lines")
    out.update(state="done", exit=code if isinstance(code, int) else 1,
               outcome=end.get("outcome"),
               lines=[str(x) for x in lines] if isinstance(lines, list) else [])
    return out


def _running(record: dict) -> bool:
    from moxi import proc

    pid = record.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        created = record.get("created")
        return isinstance(created, (int, float)) and time.time() - created < START_GRACE
    started = record.get("started")
    if not isinstance(started, (int, float)):
        return False
    return proc.alive(proc.ProcId(pid, float(started)))


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# -- the runner -----------------------------------------------------------------------------

def prune(now: float | None = None) -> int:
    """Remove the job records (and their logs) older than a day; returns how many files
    went."""
    folder = jobs_dir()
    cutoff = (time.time() if now is None else now) - KEEP_SECONDS
    gone = 0
    try:
        found = list(os.scandir(folder))
    except FileNotFoundError:
        return 0
    for entry in found:
        if not entry.name.endswith((".json", _LOG, _TMP)):
            continue
        try:
            if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                os.unlink(entry.path)
                gone += 1
        except OSError:
            continue
    return gone


def run(job: str, argv: Sequence[str]) -> int:
    """Run job `job`: prune, check its places against the terminal's, run verb `argv` through `cli.main` with
    `--json`, and write the end in one write. Returns the exit code written. An error
    anywhere before the end, the log's setup included, is written as exit 1 with its
    line."""
    os.environ[MARKER] = CALLER
    try:
        code, outcome, lines = _steps(list(argv))
    except Exception as exc:  # noqa: BLE001 - an error outside the CLI is the job's end
        log.exception("page job %s failed", job)
        code, outcome, lines = 1, None, [f"loadout: the page job failed: {exc}"]
    _end(job, code, outcome, lines)
    return code


def _steps(argv: list[str]) -> tuple[int, object, list[str]]:
    from moxi import log as moxi_log

    # Set up before the CLI's output is taken, so the log's handler keeps the real stderr.
    moxi_log.setup("loadout")
    try:
        prune()
    except OSError as exc:
        log.warning("old page jobs weren't removed: %s", exc)
    return _run(argv)


def _run(argv: list[str]) -> tuple[int, object, list[str]]:
    import contextlib
    import io

    from moxi.exit import REFUSED, USAGE

    from moxi_loadout import cli, homes

    if not argv or argv[0] not in VERBS:
        return USAGE, None, [f"loadout: {' '.join(argv) or 'nothing'} is not a verb a "
                             "page job runs"]
    refused = homes.check(argv)
    if refused is not None:
        return REFUSED, None, [f"loadout: {refused}"]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([*argv, "--json"])
    lines: list[str] = []
    try:
        outcome = json.loads(out.getvalue())
    except ValueError:
        outcome = None
        lines += [x for x in out.getvalue().splitlines() if x.strip()]
    lines += [x for x in err.getvalue().splitlines() if x.strip()]
    return code, outcome, lines


def _end(job: str, code: int, outcome: object, lines: list[str]) -> None:
    from moxi.files import atomic_write

    jobs_dir(create=True)
    atomic_write(end_path(job), json.dumps({"exit": code, "outcome": outcome,
                                            "lines": lines}) + "\n", mode=0o600)


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m moxi_loadout.pagejob <job> <verb> [<argument>...]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or not valid(args[0]):
        print("usage: python -m moxi_loadout.pagejob JOB VERB [ARGUMENT...]",
              file=sys.stderr)
        return 2
    return run(args[0], args[1:])


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["JOBS", "KEEP_SECONDS", "START_GRACE", "VERBS", "INTERRUPTED", "CALLER", "Clash",
           "Started", "jobs_dir", "valid", "new_id", "record_path", "end_path", "log_path",
           "command",
           "start", "read", "prune", "run", "main"]
