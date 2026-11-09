"""The tmux server that holds the slots: `moxi-slots-server`, and starting it on demand.

`moxi-slots-server` is the module's daemon, which the core's writer installs as a unit.
It runs in the foreground, so the supervisor owns the server's life, restarts and logs,
and no slots call starts a server inside a caller's cgroup. In order it:

1. reads the recorded tmux path and checks tmux's version against the floor;
2. makes the runtime directory (0700, owner-checked);
3. removes every launch file left there: no pane outlives a server, so each one is
   stale, and each holds a launch environment;
4. writes `server.conf` (see `config_text`) and its own pid to `server.pid`;
5. execs `<tmux> -D -S <socket> -f <runtime>/server.conf` with a clean environment
   (`clean_env`), so tmux keeps this pid.

A server already answering on the socket is left alone: the wrapper says so and exits 1.

Where the system has no supervisor (WSL2 without systemd, a container), nothing starts
the server ahead of use: `ensure()`, which every call that needs the server makes first,
starts it detached with `moxi.proc.spawn(..., detached=True)`, its output appended to the
state directory's `server.log`. A server started so dies with the WSL VM; the doctor says
so. Where a supervisor runs, `ensure()` never starts one: it raises `TryAgain` naming
the unit's state.

The rule for the server's options: every tmux option the slot model relies on is set by
the module after the user's config, at the scope that holds it. Here that is the server
options `exit-empty` and `exit-unattached` and the global `destroy-unattached`, all off;
`create` sets `destroy-unattached` on each session and each window gets `remain-on-exit`
in the call that makes it. `OPTIONS` lists the ones set here, for the doctor.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from moxi import exit as codes

from . import places, support, tmux
from .errors import TmuxError, TryAgain

#: The variables the server is started with, besides every `LC_*`.
KEEP = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "XDG_RUNTIME_DIR")
#: The options `server.conf` sets after the user's config: (scope flag, name, value).
OPTIONS = (("-s", "exit-empty", "off"), ("-s", "exit-unattached", "off"),
           ("-g", "destroy-unattached", "off"))
#: The system config tmux reads by default, then the user's: the first that exists.
SYSTEM_CONF = "/etc/tmux.conf"
#: Seconds `ensure` waits for a server it started to answer.
START_WAIT = 5.0
#: Seconds `ensure` waits for another caller that is starting the server.
LOCK_WAIT = 10.0
#: The daemon's name in `DAEMONS`.
DAEMON = "server"
#: The console script.
SCRIPT = "moxi-slots-server"


def clean_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The server's environment: `KEEP` and every `LC_*` from `environ`, nothing else.
    Every pane starts from it, so nothing of the caller's reaches one."""
    return {k: v for k, v in environ.items() if k in KEEP or k.startswith("LC_")}


def user_conf(environ: Mapping[str, str]) -> str | None:
    """The user's usual tmux config, as tmux finds it: the first of `~/.tmux.conf`,
    `$XDG_CONFIG_HOME/tmux/tmux.conf` and `~/.config/tmux/tmux.conf` that exists."""
    home = environ.get("HOME") or ""
    candidates = []
    if home:
        candidates.append(os.path.join(home, ".tmux.conf"))
    if xdg := environ.get("XDG_CONFIG_HOME"):
        candidates.append(os.path.join(xdg, "tmux", "tmux.conf"))
    if home:
        candidates.append(os.path.join(home, ".config", "tmux", "tmux.conf"))
    return next((c for c in candidates if os.path.isfile(c)), None)


def quote(text: str) -> str:
    """`text` as one word of tmux's config language."""
    if "'" not in text:
        return f"'{text}'"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def config_text(conf: str | None, environ: Mapping[str, str]) -> str:
    """`server.conf`: the user's config (or `conf`), then the options slots relies on."""
    lines = ["# Written by moxi-slots-server at each start; an edit here is lost.",
             "# Put your own settings in the file the slots setting tmux_conf names, or",
             "# in your usual tmux config when tmux_conf is empty."]
    if conf:
        sources = [conf]
    else:
        sources = [SYSTEM_CONF]
        if found := user_conf(environ):
            sources.append(found)
    lines += [f"source-file -q {quote(path)}" for path in sources]
    lines += [f"set {scope} {name} {value}" for scope, name, value in OPTIONS]
    return "\n".join(lines) + "\n"


def sweep_launch_files(directory: Path | None = None,
                       older_than: float | None = None) -> list[Path]:
    """Remove the launch files in `directory` (the runtime `launch/`), those older than
    `older_than` seconds when given; return what was removed. A launch file holds a
    launch's environment, so none is kept past its use."""
    directory = places.launch_dir() if directory is None else directory
    removed = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return removed
    now = time.time()
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            if older_than is not None and now - entry.stat().st_mtime < older_than:
                continue
            os.remove(entry.path)
            removed.append(Path(entry.path))
        except OSError:
            continue
    return removed


def main(argv: list[str] | None = None) -> int:
    """`moxi-slots-server`: become the tmux server, in the foreground."""
    from moxi import files, log
    from moxi.config import ConfigError

    from .settings import NotSetUp, tmux_conf, tmux_path

    logger = log.setup(places.MODULE, "info")
    args = sys.argv[1:] if argv is None else argv
    if args:
        logger.error("moxi-slots-server takes no arguments, got %s", " ".join(args))
        return codes.USAGE
    if not support.system_ok():
        logger.error("%s", support.unsupported_words())
        return codes.REFUSED
    try:
        path = tmux_path()
        conf = tmux_conf()
    except NotSetUp as exc:
        logger.error("%s", exc)
        return codes.ERROR
    except ConfigError as exc:
        logger.error("slots' config cannot be read: %s", exc)
        return codes.ERROR
    found = tmux.version(path)
    if not found.ok:
        logger.error("%s is %s; slots needs tmux %s or newer", path, found.text,
                     tmux.FLOOR_TEXT)
        return codes.REFUSED
    places.runtime_dir(create=True)
    socket = places.socket()
    if (pid := tmux.ping(path=path, socket=socket)) is not None:
        logger.error("a tmux server (pid %d) already answers on %s; not starting another",
                     pid, socket)
        return codes.ERROR
    for gone in sweep_launch_files():
        logger.info("removed stale launch file %s", gone.name)
    conf_path = places.server_conf()
    files.atomic_write(conf_path, config_text(conf, os.environ))
    files.atomic_write(places.server_pid(), f"{os.getpid()}\n")
    logger.info("starting %s (%s) on %s", path, found.text, socket)
    os.execve(path, [path, "-D", "-S", socket, "-f", str(conf_path)],
              clean_env(os.environ))
    return codes.ERROR  # not reached: execve replaces this process or raises


def recorded_pid() -> int | None:
    """The pid the wrapper last wrote, or None."""
    try:
        text = places.server_pid().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def supervised() -> bool:
    """Whether a supervisor runs here (`moxi.units.backend()` names one). Where none does,
    the server is started on demand."""
    from moxi import units

    return units.backend() is not None


def ensure(*, wait: float = START_WAIT) -> int:
    """The pid of the server answering on the socket, starting it first where no
    supervisor runs. Raises `TryAgain` when no server answers in time (or a supervisor
    should run it and none answers), `TmuxError` when tmux can't be run or a server
    started here exits."""
    if (pid := tmux.ping()) is not None:
        return pid
    if supervised():
        raise TryAgain(_unit_words())
    from moxi import files

    places.runtime_dir(create=True)
    try:
        with files.lock(places.server_lock(), timeout_s=LOCK_WAIT):
            if (pid := tmux.ping()) is not None:
                return pid
            return _start(wait)
    except files.LockTimeout:
        raise TryAgain(f"another caller is starting the slots server; it held "
                       f"{places.server_lock()} past {LOCK_WAIT:g} s") from None


def _start(wait: float) -> int:
    from moxi import paths, proc

    try:
        script = str(paths.script(SCRIPT))
    except FileNotFoundError as exc:
        raise TmuxError(f"the slots server cannot be started: {exc}") from None
    env = tmux.client_env()
    env["MOXI_LOG_FILE"] = str(places.server_log())
    started = proc.spawn([script], detached=True, output=places.server_log(), env=env)
    deadline = time.monotonic() + wait
    while True:
        if (pid := tmux.ping(timeout=1.0)) is not None:
            return pid
        try:
            code = proc.status(started)
        except LookupError:
            code = None
        if code is not None:
            raise TmuxError(f"the slots server exited {code} as it started; its output is "
                            f"in {places.server_log()}")
        if time.monotonic() >= deadline:
            raise TryAgain(f"the slots server started (pid {started}) but did not answer "
                           f"on {places.socket()} within {wait:g} s")
        time.sleep(0.05)


def _unit_words() -> str:
    """Why a supervised server is not answering, from the supervisor's own state."""
    state = unit_state()
    socket = places.socket()
    if state is None:
        return f"no slots server answers on {socket}; `moxi doctor slots` says why"
    if state.state == "missing":
        if editable():
            return (f"no slots server answers on {socket}; this is an editable install, "
                    f"where no unit is written, so start `{SCRIPT}` by hand (it runs in "
                    "the foreground)")
        return (f"no slots server answers on {socket}, and its unit is not installed; run "
                "`moxi install slots`")
    hint = f"; its log: {state.log}" if state.log else ""
    return (f"no slots server answers on {socket}; its unit is {state.state}{hint}; "
            "`moxi doctor slots` says more")


def editable() -> bool:
    """Whether the core refuses to write the server's unit here because slots is an
    editable install (a workspace's), where `moxi install slots` can't help."""
    from moxi import units

    try:
        changes = units.plan(places.MODULE, only=[DAEMON])
    except Exception:
        return False
    return any(c.cause == "editable" for c in changes)


def unit_state():
    """The supervisor's `UnitState` for the server, or None when it can't be read."""
    from moxi import units

    try:
        states = units.status(places.MODULE)
    except Exception:
        return None
    return next((s for s in states if s.kind == "daemon" and s.name == DAEMON), None)


__all__ = ["KEEP", "OPTIONS", "SYSTEM_CONF", "START_WAIT", "DAEMON", "SCRIPT", "clean_env",
           "user_conf", "quote", "config_text", "sweep_launch_files", "main",
           "recorded_pid", "supervised", "ensure", "editable", "unit_state"]
