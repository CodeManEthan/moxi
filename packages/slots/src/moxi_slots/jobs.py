"""Detached jobs: commands that outlive their caller, in session `moxi-jobs`.

`run_job(name, argv, env=, cwd=)` runs a command in a window of its own in session
`moxi-jobs` (made when absent, with a home shell so it outlives its jobs, and
`destroy-unattached off`), under the module lock and through the same trampoline and exec
verdict as `start`. The window is marked `@moxi_job=<name>` and has `remain-on-exit off`,
so it goes when the job ends. A name a live job has is refused `job-running`. The job's
record (what it is for, how it ended) is its caller's.

`job(name)` says whether a job runs; `jobs()` lists them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from os import PathLike

from . import launchfile, registry, server, tmux
from .errors import Refused, TmuxError
from .launch import tmux_text
from .model import HOME_MARK, Job, check_job_name

#: The session the jobs run in.
SESSION = "moxi-jobs"
#: The mark on a job's window: its value is the job's name.
JOB_MARK = "@moxi_job"
_FIELDS = ("window_id", "window_index", "@moxi_job", "pane_id", "pane_dead", "pane_pid")


@dataclass(frozen=True)
class _Window:
    window_id: str
    index: int
    name: str
    pane_id: str
    dead: bool
    pid: int | None


def _read() -> list[_Window] | None:
    """The windows of session `moxi-jobs`, one per pane; None when it doesn't exist (or no
    server answers)."""
    result = tmux.attempt("list-panes", "-s", "-t", f"={SESSION}", "-F",
                          "\t".join(f"#{{{f}}}" for f in _FIELDS))
    if not result.ok:
        err = result.err.strip()
        # No such session, or (on a server with no session at all) no target.
        if tmux.no_server(err) or "can't find" in err or "no current target" in err:
            return None
        raise TmuxError(f"tmux list-panes failed (exit {result.code}): "
                        f"{err or 'no message'}")
    found = []
    for line in result.out.splitlines():
        parts = line.split("\t")
        if len(parts) != len(_FIELDS):
            continue
        window_id, index, name, pane_id, dead, pid = parts
        found.append(_Window(window_id, int(index) if index.isdigit() else 0, name,
                             pane_id, dead == "1", int(pid) if pid.isdigit() else None))
    return found


def _job(window: _Window) -> Job:
    return Job(window.name, window.window_id, window.pane_id, not window.dead,
               None if window.dead else window.pid)


def jobs() -> list[Job]:
    """Every job in session `moxi-jobs`, by window order. Raises `TmuxError` or
    `TryAgain` when the server can't be read."""
    return [_job(w) for w in sorted(_read() or [], key=lambda w: w.index) if w.name]


def job(name: str) -> Job | None:
    """The job named `name`, or None when none runs by that name."""
    check_job_name(name)
    return next((j for j in jobs() if j.name == name), None)


def _make_session() -> None:
    home = os.environ.get("HOME") or os.path.expanduser("~")
    target = f"={SESSION}:"
    tmux.call("new-session", "-d", "-E", "-s", SESSION, "-c", home,
              ";", "set-option", "-t", target, "destroy-unattached", "off",
              ";", "set-option", "-w", "-t", target, HOME_MARK, "on",
              ";", "set-option", "-w", "-t", target, "remain-on-exit", "on")


def run_job(name: str, argv: Sequence[str], *, env: Mapping[str, str],
            cwd: str | PathLike) -> Job:
    """Run `argv` with exactly `env` (and the pane's terminal names) in `cwd`, as job
    `name`, detached from the caller; return once it has started, or has not
    (`Job.exec`, as `start`'s). Refuses `job-running` when a live job has the name."""
    check_job_name(name)
    argv, env, cwd = launchfile.check(argv, env, cwd)
    script = launchfile.trampoline()
    server.ensure()
    with registry.locked():
        launchfile.sweep()
        windows = _read()
        live = next((w for w in windows or [] if w.name == name and not w.dead), None)
        if live is not None:
            raise Refused("job-running", f"job {name} is running (pane {live.pane_id}); "
                                         "wait for it to end, or pick another name")
        if windows is None:
            _make_session()
            windows = _read() or []
        index = max((w.index for w in windows), default=0) + 1
        target = f"={SESSION}:{index}"
        path = launchfile.write(argv, env, cwd)
        try:
            made = tmux.call("new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}",
                             "-t", target, "-n", tmux_text(name), script, str(path),
                             ";", "set-option", "-w", "-t", target, JOB_MARK, name,
                             ";", "set-option", "-w", "-t", target, "remain-on-exit",
                             "off")
        except BaseException:
            launchfile.remove(path)
            raise
        window_id, pane_id = made.out.split()[:2]
    verdict = launchfile.wait(path, pane_id)
    seen = launchfile.read_pane(pane_id)
    running = seen.exists and not seen.dead
    return Job(name, window_id, pane_id, running, seen.pid if running else None,
               verdict.exec, verdict.line, verdict.dead_status)


__all__ = ["SESSION", "JOB_MARK", "jobs", "job", "run_job"]
