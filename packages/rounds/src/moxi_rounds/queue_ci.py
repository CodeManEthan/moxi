"""A candidate's CI as the lander drives it: the push of its ref, the repo's `ci.local`
run at it, the read of its run, a rerun, and the ref's removal.

`QueueCi` does it with the repo's `ci` setting (`ci.declared`), git and gh. The lander
takes any object with the same methods, so a test gives it a stand-in.

- `push(place, commit, ref)` pushes `commit` from the checkout at `place` to `ref` on
  the setting's `remote`, forced, so the repo's pre-push hooks (the publishing scan)
  run on it. It gives git's exit code and what it printed.
- `local(place, values)` runs the setting's `local` command at the candidate checked out
  at `place`, placeholders filled from `values`, for at most its `timeout_s`; None when
  `local` is `none`. Its output goes to `local_log`. The command holds its own locks, as
  a check's does (moxi's runs under `gate.lock` and `check.lock`).
- `read(place, commit)` reads a candidate's CI at `commit` as `check` reads it
  (`ci.read_runs`), by the workflows the setting gives candidates
  (`ci.Declared.candidate_workflows`), as one `Read`: the whole reading, `pending`
  while any run it read has not completed (its ids in `running`), so a failed job of a
  run that goes on reads no red, or else `cancelled` when a run it read was cancelled.
  `read_full(place, commit, not_runs=...)` reads the target's full run there the same
  way, by the full run's workflows (`ci.Declared.full_workflows`), so a candidate's
  scoped run at the same commit never stands in for it, and leaves out the runs
  `not_runs` names, the runs candidates were read by.
- `rerun(place, runs, failed=...)` reruns each run, or only its failed jobs.
- `failures(place, commit, read)` lists the failures of a red read's runs at `commit`
  (`failures.of_run`): each run's failed jobs by the setting's `jobs` command, of the
  jobs the setting names where every workflow is read by its jobs, and the tests their
  logs name (`LOG_FAILED`), each failure of a job the read's workflows give a
  `stands_for` holding the job it stands for. Raises `ci.ReadFailed` when either
  command fails, and, running nothing, for a read with a run still running.

The two `gh` commands the setting does not give, the rerun and `LOG_FAILED`, take the
repository the setting's own commands name (`ci.gh_repo`: the first `--repo` or `-R`
of `status`, `ids` or `jobs`), so they read the runs those commands read.
- `remove(place, ref)` deletes `ref` on the remote; what git said is not read, since the
  sweep removes a ref left behind.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from moxi import proc

from . import checking, ci, gitreads
from . import failures as failing

#: How long a push or a rerun may take, in seconds.
PUSH_TIMEOUT_S = checking.PUSH_TIMEOUT_S
#: How long `ci.local` may run when its setting names no limit, in seconds.
LOCAL_TIMEOUT_S = 4 * 3600.0
#: The reading of a run that was cancelled.
CANCELLED = "cancelled"
#: The command that prints the logs of a run's failed jobs, `{run}` its id.
LOG_FAILED = ("gh", "run", "view", ci.RUN, "--log-failed")


@dataclass(frozen=True)
class Said:
    """What a command did: its exit code (1 when it could not run) and what it printed."""

    code: int
    said: str


@dataclass(frozen=True)
class Local:
    """A run of `ci.local` at a candidate: the argv, its exit code (None when it ran past
    its limit), how long it took and the file its output went to."""

    argv: tuple[str, ...]
    exit_code: int | None
    duration_s: float
    output: str


@dataclass(frozen=True)
class Read:
    """A read of a candidate's CI: the whole reading (`ci.GREEN`, `ci.RED`, `ci.PENDING`,
    `ci.ABSENT` or `CANCELLED`), the ids of the runs read, each workflow as `ci.reading`
    gives it, the argvs that ran, and the ids of the runs read that have not completed
    (`running`)."""

    word: str
    runs: tuple[str, ...] = ()
    workflows: tuple[dict, ...] = ()
    status_argv: tuple[str, ...] = ()
    ids_argv: tuple[str, ...] = ()
    jobs_argvs: tuple[tuple[str, ...], ...] = ()
    running: tuple[str, ...] = ()


#: The `--repo` argument the setting's own commands give gh.
gh_repo = ci.gh_repo


def _said(argv: Sequence[str], *, cwd, timeout_s: float | None) -> Said:
    try:
        done = proc.run(list(argv), timeout_s=timeout_s, env=gitreads.git_env(), cwd=cwd,
                        stdin_data="")
    except subprocess.TimeoutExpired:
        return Said(1, f"{shlex.join(argv)} gave no answer in {timeout_s:g} seconds")
    except OSError as exc:
        return Said(1, f"{shlex.join(argv)} could not run: {exc}")
    said = "\n".join(part.strip() for part in (done.stdout or "", done.stderr or "")
                     if part.strip())
    return Said(done.returncode, said)


class QueueCi:
    """A queue project's candidate CI from its `ci` setting `found`, whose `local` output
    goes to `local_log`."""

    def __init__(self, found: ci.Declared, *, local_log: Path):
        self.found = found
        self.local_log = local_log

    def push(self, place: Path, commit: str, ref: str) -> Said:
        return _said(["git", "push", str(self.found.remote), f"+{commit}:refs/heads/{ref}"],
                     cwd=place, timeout_s=PUSH_TIMEOUT_S)

    def remove(self, place: Path, ref: str) -> Said:
        return _said(["git", "push", str(self.found.remote), "--delete",
                      f"refs/heads/{ref}"], cwd=place, timeout_s=PUSH_TIMEOUT_S)

    def local(self, place: Path, values: Mapping[str, str]) -> Local | None:
        if self.found.local is None:
            return None
        argv = tuple(checking.fill(self.found.local, values))
        began = time.monotonic()
        try:
            done = proc.run(list(argv), timeout_s=self.found.local_timeout_s or LOCAL_TIMEOUT_S,
                            env=gitreads.git_env(), cwd=place, output=self.local_log)
            code: int | None = done.returncode
        except subprocess.TimeoutExpired:
            code = None
        except OSError:
            code = 127
        return Local(argv, code, round(time.monotonic() - began, 1), str(self.local_log))

    def read(self, place: Path, commit: str) -> Read:
        return self._read(place, commit, is_candidate=True)

    def read_full(self, place: Path, commit: str, *,
                  not_runs: Sequence[str] = ()) -> Read:
        return self._read(place, commit, is_candidate=False, not_runs=not_runs)

    def _read(self, place: Path, commit: str, *, is_candidate: bool,
              not_runs: Sequence[str] = ()) -> Read:
        reading, runs = ci.read_runs(self.found, values={"tip": commit}, cwd=place,
                                     is_candidate=is_candidate, not_runs=not_runs)
        # A run with no id is still named, so it still holds the read at pending.
        running = tuple(str(run.get("databaseId", "unknown")) for run in runs
                        if run.get("status") != "completed")
        word = reading.whole
        if running:
            word = ci.PENDING
        elif any(run.get("conclusion") == CANCELLED for run in runs):
            word = CANCELLED
        return Read(word, tuple(str(run.get("databaseId")) for run in runs
                                if run.get("databaseId") is not None),
                    reading.workflows, reading.status_argv, reading.ids_argv,
                    reading.jobs_argvs, running)

    def failures(self, place: Path, commit: str, read: Read) -> list[dict]:
        if read.running:
            raise ci.ReadFailed(LOG_FAILED, "the run " + ", ".join(read.running)
                                + " has not completed, so its failures are not final")
        names = None
        if read.workflows and all("jobs" in one for one in read.workflows):
            names = [str(job.get("name")) for one in read.workflows for job in one["jobs"]]
        stands_for = {str(job): str(stood) for one in read.workflows
                      for job, stood in (one.get("stands_for") or {}).items()}
        found: list[dict] = []
        for run in read.runs:
            _argv, jobs = ci.run_jobs(self.found, run, values={"tip": commit}, cwd=place)
            failed = failing.failed_jobs(jobs, names)
            if not failed:
                continue
            argv = [*(word.replace(ci.RUN, run) for word in LOG_FAILED),
                    *gh_repo(self.found)]
            log = _said(argv, cwd=place, timeout_s=PUSH_TIMEOUT_S)
            if log.code != 0:
                raise ci.ReadFailed(argv, f"exited {log.code}")
            found.extend(failing.of_run(failed, failing.tests_of_log(log.said),
                                        stands_for))
        return found

    def rerun(self, place: Path, runs: Sequence[str], *, failed: bool) -> Said:
        said = Said(0, "")
        for run in runs:
            said = _said(["gh", "run", "rerun", run, *(["--failed"] if failed else []),
                          *gh_repo(self.found)], cwd=place, timeout_s=PUSH_TIMEOUT_S)
            if said.code != 0:
                return said
        return said


__all__ = ["PUSH_TIMEOUT_S", "LOCAL_TIMEOUT_S", "CANCELLED", "LOG_FAILED", "Said", "Local",
           "Read", "QueueCi", "gh_repo"]
