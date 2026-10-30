"""slots' rows in `moxi doctor`, the announcement's `DOCTOR`.

The core's doctor already reads the server's unit, runs its `HEALTH` and checks the
declared stores. These rows add what only slots knows, and write no doctor record:

    system        off on native Windows, where this is the only row
    tmux          the recorded path the server uses: fail when unset, not absolute,
                  missing or below 3.2
    server mode   warn where no supervisor runs (the server starts on demand)
    linger        on systemd, warn when linger is off: the server and every slot stop at
                  the user's last logout
    options       warn when a live value of an option slots sets differs
    socket        fail when the runtime directory is not 0700 and the user's
    launch files  removes launch files older than a minute (each holds a launch's
                  environment) and warns that it did
    trampoline    fail when `moxi-slots-exec` is not installed
    mismatches    ok, with the count and the path

Further rows come from `CHECKS`, each a function returning rows, run after these; the
registry's rows are added there.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable, Iterator

from moxi.doctor import Row

from . import places, server, support, tmux
from .errors import TmuxError, TryAgain
from .settings import REINSTALL, NotSetUp

MODULE = places.MODULE
#: Seconds after which a launch file is stale: `start` gives the trampoline 5 s.
STALE_LAUNCH = 60.0
#: The trampoline's console script.
TRAMPOLINE = "moxi-slots-exec"
#: Seconds the linger read gets.
LINGER_TIMEOUT = 3.0


def _row(name: str, state: str, detail: str, fix: str | None = None) -> Row:
    return Row(MODULE, name, state, detail, fix)


def system_row() -> Row:
    kind = support.system()
    if not support.system_ok(kind):
        return _row("system", "off", support.unsupported_words(kind))
    return _row("system", "ok", support.label(kind))


def tmux_row() -> Row:
    from moxi.config import ConfigError

    from .settings import current
    from .setup import install_hint

    try:
        value = current()["tmux"]
    except ConfigError as exc:
        return _row("tmux", "fail", f"slots' config cannot be read: {exc}")
    fix = f"install tmux if it is missing ({install_hint()}), then {REINSTALL}"
    if not os.path.isabs(value):
        return _row("tmux", "fail", f"tmux's path is not recorded (the setting reads "
                                    f"{value!r})", fix)
    if not (os.path.isfile(value) and os.access(value, os.X_OK)):
        return _row("tmux", "fail", f"the recorded tmux, {value}, is not an executable "
                                    "file (moved or removed)", fix)
    found = tmux.version(value)
    if not found.ok:
        return _row("tmux", "fail", f"{value} is {found.text}; slots needs tmux "
                                    f"{tmux.FLOOR_TEXT} or newer",
                    f"install a newer tmux ({install_hint()}), then {REINSTALL}")
    return _row("tmux", "ok", f"{value} ({found.text})")


def server_mode_row() -> Row:
    from moxi import units

    backend = units.backend()
    if backend is not None:
        return _row("server mode", "ok", f"supervised by {backend}")
    if support.system() == support.WSL2:
        return _row("server mode", "warn",
                    "no supervisor (WSL2 without systemd): the server starts on first use "
                    "and dies with the WSL VM, ending every slot's session",
                    "turn systemd on: put `[boot]` and `systemd=true` in /etc/wsl.conf, "
                    "run `wsl --shutdown` from Windows, then " + REINSTALL)
    return _row("server mode", "warn",
                "no supervisor here: the server starts on first use, and nothing restarts "
                "it if it stops")


def linger_row() -> Row | None:
    """On systemd only; None elsewhere."""
    import getpass
    import shutil
    import subprocess

    from moxi import proc, units

    if units.backend() != "systemd":
        return None
    user = os.environ.get("USER") or getpass.getuser()
    loginctl = shutil.which("loginctl")
    if loginctl is None:
        return _row("linger", "warn", "loginctl is not on PATH, so linger can't be read")
    try:
        done = proc.run([loginctl, "show-user", user, "--property=Linger"],
                        timeout=LINGER_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _row("linger", "warn", f"loginctl could not be read: {exc}")
    text = (done.stdout or b"").decode("utf-8", "replace").strip()
    if done.returncode != 0 or "=" not in text:
        return _row("linger", "warn", f"loginctl show-user {user} gave no Linger value")
    value = text.split("=", 1)[1].strip()
    if value == "no":
        return _row("linger", "warn",
                    "linger is off: the server and every slot stop at your last logout",
                    f"loginctl enable-linger {user}")
    return _row("linger", "ok", f"linger is {value}")


def options_row() -> Row:
    args: list[str] = []
    for scope, name, _ in server.OPTIONS:
        if args:
            args.append(";")
        args += ["show-options", scope, "-v", name]
    try:
        result = tmux.call(*args, timeout=tmux.PING_TIMEOUT)
    except TryAgain:
        return _row("options", "off", "not checked: no server answers")
    except NotSetUp:
        return _row("options", "off", "not checked: tmux's path is not recorded")
    except TmuxError as exc:
        return _row("options", "warn", f"could not be read: {exc}")
    values = result.out.split("\n")
    wrong = [f"{name} is {values[i].strip() if i < len(values) else '(unread)'}, "
             f"not {want}"
             for i, (_, name, want) in enumerate(server.OPTIONS)
             if i >= len(values) or values[i].strip() != want]
    if wrong:
        return _row("options", "warn",
                    "; ".join(wrong) + ": a slot may end when it should not",
                    "remove the setting from your tmux config, or set it back by hand "
                    "with tmux -S " + places.socket() + " set-option")
    return _row("options", "ok", ", ".join(f"{name} {want}"
                                            for _, name, want in server.OPTIONS))


def _private(path: str) -> str | None:
    """Why directory `path` is not private to this user, or None when it is."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"{path} cannot be read: {exc}"
    problems = []
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o700:
        problems.append(f"mode {mode:04o}, not 0700")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        problems.append(f"owned by uid {st.st_uid}, not you")
    return f"{path}: {', '.join(problems)}" if problems else None


def socket_row() -> Row:
    runtime = str(places.runtime_dir())
    socket = places.socket()
    dirs = [runtime]
    if os.path.dirname(socket) != runtime:
        dirs.append(os.path.dirname(socket))
    problems = [p for p in (_private(d) for d in dirs) if p]
    if problems:
        return _row("socket", "fail", "; ".join(problems),
                    "chmod 700 " + " ".join(dirs))
    if not os.path.isdir(runtime):
        return _row("socket", "ok", f"{runtime} is made when the server first starts")
    return _row("socket", "ok", f"{socket}, in a private directory")


def launch_files_row() -> Row:
    directory = places.launch_dir()
    removed = server.sweep_launch_files(directory, older_than=STALE_LAUNCH)
    problem = _private(str(directory))
    if problem:
        return _row("launch files", "fail", problem, f"chmod 700 {directory}")
    if removed:
        return _row("launch files", "warn",
                    f"removed {len(removed)} launch file(s) older than a minute from "
                    f"{directory}; each held a launch's environment, and a start removes "
                    "its own, so one was left by a start that did not finish")
    return _row("launch files", "ok", "none older than a minute")


def trampoline_row() -> Row:
    from moxi import paths

    try:
        found = paths.script(TRAMPOLINE)
    except FileNotFoundError as exc:
        return _row("trampoline", "fail", str(exc), f"reinstall: {REINSTALL}")
    return _row("trampoline", "ok", str(found))


def mismatches_row() -> Row:
    directory = places.mismatches_dir()
    try:
        count = sum(1 for e in os.scandir(directory) if e.is_file(follow_symlinks=False))
    except FileNotFoundError:
        count = 0
    except OSError as exc:
        return _row("mismatches", "warn", f"{directory} cannot be read: {exc}")
    return _row("mismatches", "ok", f"{count} saved in {directory}")


#: The checks after the fixed rows, in order; each returns rows. The registry's checks
#: are added here.
CHECKS: list[Callable[[], Iterable[Row]]] = []


def rows() -> Iterator[Row]:
    """slots' doctor rows; on a system slots does not run on, the one `system` row."""
    first = system_row()
    yield first
    if first.state == "off":
        return
    yield tmux_row()
    yield server_mode_row()
    if (linger := linger_row()) is not None:
        yield linger
    yield options_row()
    yield socket_row()
    yield launch_files_row()
    yield trampoline_row()
    yield mismatches_row()
    for check in CHECKS:
        yield from check()


__all__ = ["STALE_LAUNCH", "TRAMPOLINE", "CHECKS", "system_row", "tmux_row",
           "server_mode_row", "linger_row", "options_row", "socket_row",
           "launch_files_row", "trampoline_row", "mismatches_row", "rows"]
