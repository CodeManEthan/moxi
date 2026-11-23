"""The lander: the one process per repo that lands a queue project's queued builds.

**One per repo.** The lander is `moxi rounds land --repo KEY` (`run`). It holds the
repo's lander lock, a lock on `lander.lock` in the repo's queue folder
(`queue_file.queue_dir`), for as long as it works. A second lander finds the lock held
and exits at once; a dead lander's lock frees itself, since the system frees a lock with
the process that held it. A lander waits up to `TAKE_S` for the lock, so a command that
only looks whether one runs (`held`, which takes the lock and frees it at once) never
makes a starting lander exit.

**Who starts it.** `finish` starts one every time a build joins, and the extra ones exit
on the lock. `moxi rounds next` and `moxi rounds queue` start one when something keeps a
lander (`settled`) and no lander holds the lock, and say they did (`ensure`), so a lander that died (a crash, a
reboot) is replaced by the next of these commands, not only by the next `finish`. Neither
records anything for it. The doctor starts none: it only reads, and names `moxi rounds
queue --repo KEY` as the fix for a member that waits with no lander.

**How it is started** (`start`): in a transient user unit (`systemd-run --user
--collect`), so it leaves the starting session's process group and cgroup and does not
end with the builder's session; where that can't be run, in a new session of its own
(`setsid`). Either way it runs the interpreter that started it, `python -m moxi rounds
land --repo KEY`, in the repo's root, with the starter's `PATH`, `HOME`, git's and gh's
credentials (`SSH_AUTH_SOCK`, `GH_TOKEN`, `GITHUB_TOKEN`, `GH_CONFIG_DIR`), the XDG
roots and every `MOXI_` variable (`ENV_KEPT`, `KEPT_PREFIX`), since a user unit starts
from the user manager's environment and not the caller's. Its output goes to
`lander.log` in the repo's queue folder.

**When it exits** (`run`): when `settled` says nothing keeps it: no member waits, no
message comms could not take is still being tried, and the target's head has a full run
that has ended and been read (or one the lander gave up on, pushed twice with no run). To exit it releases the lock, then reads the queue again: if a member waits and the
lock is free, it takes the lock again and goes on. A `finish` that appends after that
read starts its own lander, which finds the lock free. So while a member waits, a lander
runs, or the next command that reads the queue starts one.

Each pass of its loop is `step`, run every `POLL_S` while it holds the lock.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from moxi import files

from . import batching, folders, queue_file, telling
from . import settings as own

#: The lander lock's file and the lander's log, in the repo's queue folder.
LOCK_FILE = "lander.lock"
LOG_FILE = "lander.log"
#: How long a lander waits for its lock before it takes another lander to hold it.
TAKE_S = 2.0
#: How long a lander waits between two passes, in seconds.
POLL_S = 60.0
#: The variables a lander is started with besides every `MOXI_` one: what finds the
#: tools, the account's folders, and git's and gh's credentials.
ENV_KEPT = ("PATH", "HOME", "USER", "LANG", "SSH_AUTH_SOCK", "GH_TOKEN", "GITHUB_TOKEN",
            "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
            "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "VIRTUAL_ENV")
KEPT_PREFIX = "MOXI_"


def lock_file(repo: str, env=None) -> Path:
    return queue_file.queue_dir(repo, env) / LOCK_FILE


def log_file(repo: str, env=None) -> Path:
    return queue_file.queue_dir(repo, env) / LOG_FILE


@contextmanager
def holding(repo: str, *, timeout_s: float = TAKE_S, env=None):
    """Hold the repo's lander lock for the block; yields False, holding nothing, when
    another process holds it past `timeout_s`."""
    path = lock_file(repo, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with files.lock(path, timeout_s=timeout_s):
            yield True
            return
    except files.LockTimeout:
        pass
    yield False


def held(repo: str, env=None) -> bool:
    """Whether a lander holds the repo's lock now. Takes the lock and frees it at once
    when it is free, which a starting lander waits out (`TAKE_S`)."""
    with holding(repo, timeout_s=0, env=env) as taken:
        return not taken


def seen_running(repo: str, env=None) -> bool:
    """Whether a lander seems to run, read without taking the lock: the pid the lock
    file holds is a live process running `moxi rounds land`. The doctor reads this."""
    import psutil

    try:
        first = lock_file(repo, env).read_text(encoding="utf-8").split()
    except OSError:
        return False
    if not first or not first[0].isdigit():
        return False
    try:
        return "land" in psutil.Process(int(first[0])).cmdline()
    except (psutil.Error, OSError):
        return False


def lander_env(env=None) -> dict[str, str]:
    """The environment a lander is started with: `ENV_KEPT` and every `MOXI_` variable
    of `env` (the process's own when left out)."""
    source = os.environ if env is None else env
    return {key: value for key, value in source.items()
            if key in ENV_KEPT or key.startswith(KEPT_PREFIX)}


def argv(repo: str) -> list[str]:
    """What runs the lander for `repo`, under the interpreter running now."""
    return [sys.executable, "-m", "moxi", "rounds", "land", "--repo", repo]


@dataclass(frozen=True)
class Started:
    """How a lander was started: `how` is "unit" (a transient user unit, `unit` its
    name) or "session" (a new session), and `log` the file it writes to."""

    how: str
    log: Path
    unit: str | None = None


def start(repo: str, *, env=None, spawn: Callable[[list[str], dict], None] | None = None,
          ) -> Started:
    """Start a lander for `repo`, which exits at once when another holds the lock. `env`
    is the environment the lander's is drawn from (`lander_env`), the process's own when
    left out. `spawn(argv, env)` stands in for the start of the process, for a test."""
    _key, root = folders.root(repo)
    run_env = lander_env(env)
    log = log_file(repo)
    log.parent.mkdir(parents=True, exist_ok=True)
    command = argv(repo)
    if spawn is not None:
        spawn(command, run_env)
        return Started("session", log)
    unit = f"moxi-rounds-lander-{repo}-{os.getpid()}-{int(time.time())}"
    systemd_run = shutil.which("systemd-run")
    if systemd_run is not None:
        unit_argv = [systemd_run, "--user", "--collect", "--quiet", f"--unit={unit}",
                     f"--working-directory={root}",
                     f"--property=StandardOutput=append:{log}",
                     f"--property=StandardError=append:{log}",
                     *(f"--setenv={key}={value}" for key, value in run_env.items()),
                     *command]
        try:
            done = subprocess.run(unit_argv, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is not None and done.returncode == 0:
            return Started("unit", log, unit)
    # The shell appends the lander's output to its log, so no file is opened here.
    subprocess.Popen(["sh", "-c", 'exec "$@" >>"$0" 2>&1', str(log), *command], cwd=root,
                     env=run_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return Started("session", log)


def ensure(repo: str, *, env=None, spawn=None) -> Started | None:
    """Start a lander for `repo` when something keeps one (`settled`) and none holds
    the lock; what was started, None when nothing was."""
    if settled(repo, queue_file.read(repo, env)) or held(repo):
        return None
    return start(repo, env=env, spawn=spawn)


def queue_repos(settings=None) -> list[str]:
    """Every project the settings make a queue project, by its key."""
    settings = own.load() if settings is None else settings
    return [name for name in settings["projects"]
            if own.build_setting(settings, name, "land") == own.LAND_QUEUE]


# -- the run -----------------------------------------------------------------------------

def settled(repo: str, queue: queue_file.Queue, *, env=None) -> bool:
    """Whether nothing keeps the lander: no member waits, no batch runs (one whose
    members all left runs until the lander ends it), no message comms could not take is
    still being tried (`telling.trying`, bounded by `telling.UNSENT_S`), and the
    target's head has a full run that has ended and been read, or one given up on
    (`batching.main_owed`)."""
    return (not queue.queued() and not queue.running_batches()
            and not telling.trying(queue) and not batching.main_owed(repo, queue))


def run(repo: str, *, say: Callable[[str], None] = print,
        step: Callable[[str], None] | None = None, poll_s: float = POLL_S,
        sleep: Callable[[float], None] = time.sleep, env=None) -> str:
    """Run the lander for `repo` until nothing keeps it; why it stopped, in words.
    `step(repo)` is one pass of its work, run every `poll_s` while it holds the lock."""
    step = step or (lambda _repo: None)
    with holding(repo, env=env) as taken:
        if not taken:
            say(f"another lander holds {repo}'s lock: nothing to do")
            return "held"
        say(f"lander of {repo}: started, pid {os.getpid()}")
        _work(repo, step=step, poll_s=poll_s, sleep=sleep, env=env)
    while True:
        # Read again after the release: a member that joined while this lander was on
        # its way out is taken now, or by the lander its finish started.
        if settled(repo, queue_file.read(repo, env), env=env):
            say(f"lander of {repo}: nothing waits, exiting")
            return "settled"
        with holding(repo, timeout_s=0, env=env) as taken:
            if not taken:
                say(f"lander of {repo}: another lander took the lock, exiting")
                return "handed"
            say(f"lander of {repo}: a member joined as it exited; going on")
            _work(repo, step=step, poll_s=poll_s, sleep=sleep, env=env)


def _work(repo, *, step, poll_s, sleep, env) -> None:
    while True:
        step(repo)
        if settled(repo, queue_file.read(repo, env), env=env):
            return
        sleep(poll_s)


__all__ = ["LOCK_FILE", "LOG_FILE", "TAKE_S", "POLL_S", "ENV_KEPT", "KEPT_PREFIX",
           "lock_file", "log_file", "holding", "held", "seen_running", "lander_env", "argv", "Started",
           "start", "ensure", "queue_repos", "settled", "run"]
