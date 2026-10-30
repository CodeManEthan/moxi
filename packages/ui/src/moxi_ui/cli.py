"""`moxi ui`: open, serve, status, stop, signout.

Every call `moxi ui` makes to the shell goes through loopback's `call` on the runtime
file it read, which proves the shell before it sends the token. A shell that fails the
proof is another program on the port, and `moxi ui` exits 1 with the fix.

`open` makes sure a shell of this user holds the port, then mints a one-use ticket and
hands its sign-in URL to the browser:

1. When a socket unit or the Windows logon task is installed, the runtime dir the unit's
   shell will use must be the one this shell computes; a locator variable that moves it
   is refused, naming the variable.
2. A shell that answers its health route is up. A held `page.lock` means one is starting:
   wait for it. A runtime file whose server fails the proof may be stale; wait briefly for
   a new one, then refuse.
3. A socket of this user that is listening holds the port; the first browser connection
   starts the shell.
4. Otherwise, if anything accepts a connection on the port, another program holds it.
   Else start the shell: through the logon task on Windows where it is installed and
   enabled, otherwise as a detached child, and wait for it to answer.
5. Mint the ticket and pass the URL on: by `webbrowser` on Linux (the owner check guards
   the URL in argv), through a 0600 redirect file on macOS and Windows (so no argv holds
   it), printed on WSL and with `--print`.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import time
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moxi import files
from moxi.config import ConfigError
from moxi.paths import Layout, python
from moxi_loopback.api import NotOurServer, RuntimeInfo, call, read_runtime

from . import config, session
from .session import HEALTH_PATH, SERVERS_PATH, SIGNOUT_API, STOP_PATH, TICKETS_DIR

MODULE = "ui"
DAEMON = "shell"
LOCK_FILE = "page.lock"
LOG_FILE = "shell.log"
#: Seconds `open` waits for a shell that is starting, or that it started.
UP_WAIT = 5.0
#: Seconds `open` waits for the logon task's gate to restart a shell that failed.
RESTART_WAIT = 65.0
#: Seconds `open` waits for a fresh runtime file after a proof fails.
STALE_WAIT = 2.0
#: How often a wait looks again.
POLL = 0.1
#: How often the restart wait reads the task's state.
STATUS_POLL = 2.0
#: Redirect files older than this are swept.
REDIRECT_AGE = 60.0
#: Lines of the shell's log printed when it fails to start.
LOG_TAIL = 20

_NOT_INSTALLED = ("missing", "nobackend")


class Refused(Exception):
    """`moxi ui` stops here: the message, and the exit code."""

    def __init__(self, text: str, code: int = 1):
        super().__init__(text)
        self.code = code


def _held_text(port: int | None) -> str:
    where = f"port {port}" if port else "the shell's port"
    return (f"{where} is held by another program; choose another with "
            "`moxi config set ui port N`")


# --- the seams tests replace ------------------------------------------------------------


def _platform() -> str:
    return sys.platform


def _wsl() -> bool:
    return sys.platform == "linux" and session.is_wsl()


def _units_status() -> list[Any]:
    from moxi import units

    try:
        return list(units.status([MODULE]))
    except Exception:  # noqa: BLE001 - no supervisor read means no unit to account for
        return []


def _units_start() -> list[Any]:
    from moxi import units

    return list(units.start(MODULE, [DAEMON]))


def _spawn(argv: list[str], *, cwd: Path, output: Path, env: Mapping[str, str]) -> Any:
    from moxi import proc

    return proc.spawn(argv, detached=True, cwd=cwd, output=output, env=env)


def _open_browser(url: str) -> bool:
    return webbrowser.open(url)


def _stored_environment() -> dict[str, str]:
    """The user's stored environment on Windows (`HKCU\\Environment`), which a logon task
    starts with."""
    import winreg

    found: dict[str, str] = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        index = 0
        while True:
            try:
                name, value, kind = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if isinstance(value, str):
                if kind == winreg.REG_EXPAND_SZ:
                    value = winreg.ExpandEnvironmentStrings(value)
                found[name] = value
    return found


_sleep = time.sleep
_clock = time.time


# --- reading the shell ------------------------------------------------------------------


def layout() -> Layout:
    return Layout(MODULE)


def lock_held() -> bool:
    """Whether a shell of this user is running or starting: one try at `page.lock`,
    released at once. No pid or clock is trusted."""
    directory = layout().runtime_dir()
    if not directory.is_dir():
        return False
    try:
        with files.lock(directory / LOCK_FILE, timeout=0):
            return False
    except files.LockTimeout:
        return True
    except RuntimeError:  # This process holds it: a shell running in process.
        return True


def probe() -> tuple[str, RuntimeInfo | None]:
    """`("up", info)` when the runtime file's shell answers its health route with its own
    `server_id`; `("foreign", info)` when the server on the file's port fails the proof;
    `("down", info or None)` otherwise."""
    info = read_runtime(MODULE)
    if info is None:
        return "down", None
    try:
        status, data = call(info, "GET", HEALTH_PATH, timeout=2.0)
    except NotOurServer:
        return "foreign", info
    except (OSError, TimeoutError, ValueError):
        return "down", info
    if status == 200 and isinstance(data, dict) and data.get("server_id") == info.server_id:
        return "up", info
    return "down", info


def _wait_up(seconds: float) -> RuntimeInfo | None:
    deadline = time.monotonic() + seconds
    while True:
        state, info = probe()
        if state == "up":
            return info
        if time.monotonic() >= deadline:
            return None
        _sleep(POLL)


def log_path() -> Path:
    return layout().state_dir() / "logs" / LOG_FILE


def log_tail(lines: int = LOG_TAIL) -> list[str]:
    try:
        text = log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def _failed_to_start(text: str) -> Refused:
    tail = log_tail()
    if tail:
        text += f"; the last lines of {log_path()}:\n" + "\n".join(tail)
    return Refused(text)


def _port_taken(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


# --- the units --------------------------------------------------------------------------


def _row(rows: Sequence[Any], kind: str) -> Any | None:
    for row in rows:
        if (getattr(row, "module", None) == MODULE and getattr(row, "name", None) == DAEMON
                and getattr(row, "kind", None) == kind):
            return row
    return None


def socket_row(rows: Sequence[Any]) -> Any | None:
    """The shell's socket, when one is installed."""
    row = _row(rows, "socket")
    return row if row is not None and row.state not in _NOT_INSTALLED else None


def task_row(rows: Sequence[Any]) -> Any | None:
    """The shell's Windows logon task, when one is installed."""
    row = _row(rows, "daemon")
    if row is None or row.backend != "schtasks" or row.state in _NOT_INSTALLED:
        return None
    return row


def locator_problem(environ: Mapping[str, str], unit_environ: Mapping[str, str]) -> list[str]:
    """The locator variables whose values differ between `environ` and the environment a
    unit's shell starts with, when the runtime dir they give differs; else []."""
    here = layout()
    if here.runtime_dir(environ) == here.runtime_dir(unit_environ):
        return []
    return [name for name in here.locators() if environ.get(name) != unit_environ.get(name)]


def _unit_environ(environ: Mapping[str, str], stored: Mapping[str, str] | None) -> dict[str, str]:
    names = set(layout().locators())
    out = {key: value for key, value in environ.items() if key not in names}
    if stored is not None:
        # A logon task starts with the user's stored environment, so its locators count.
        folded = {key.upper(): value for key, value in stored.items()}
        for name in names:
            if name.upper() in folded:
                out[name] = folded[name.upper()]
    return out


def _check_locators(rows: Sequence[Any], environ: Mapping[str, str]) -> None:
    sock, task = socket_row(rows), task_row(rows)
    if sock is None and task is None:
        return
    stored = None
    if task is not None:
        try:
            stored = _stored_environment()
        except OSError:
            stored = {}
    named = locator_problem(environ, _unit_environ(environ, stored))
    if not named:
        return
    variables = ", ".join(named)
    if task is not None:
        fix = (f"unset {variables} here or set it the same in your stored environment, "
               "or run `moxi ui stop` and then `moxi ui serve` in this shell")
        where = "the logon task's shell"
    else:
        stop = ("launchctl bootout the shell's agent" if _platform() == "darwin"
                else "systemctl --user stop moxi-ui-shell.socket")
        fix = f"unset {variables}, or run `moxi ui serve` in this shell after `{stop}`"
        where = "the socket-started shell"
    raise Refused(f"{variables} moves the shell's runtime directory, so {where} would read "
                  f"its sign-in tickets elsewhere; {fix}")


# --- open -------------------------------------------------------------------------------


def _shell_argv() -> list[str]:
    return [str(python()), "-P", "-m", "moxi_ui.serve"]


def spawn_shell() -> None:
    """Start the shell as a detached child, from its own runtime dir, appending to its
    log. The log is named to the child in `MOXI_LOG_FILE`, so the core's logging rolls it
    to `.1` past 5 MiB."""
    cwd = layout().runtime_dir(create=True)
    log = log_path()
    files.private_dir(log.parent)
    env = {**os.environ, "MOXI_LOG_FILE": str(log)}
    _spawn(_shell_argv(), cwd=cwd, output=log, env=env)


def _run_line_at(last: Any) -> float | None:
    at = last.get("at") if isinstance(last, Mapping) else None
    if not isinstance(at, str):
        return None
    try:
        return datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def _wait_restart(first_last: Any, started: float, say: Callable[[str], Any]) -> RuntimeInfo:
    """Wait for the logon task's gate to restart a shell that exited non-zero. Stops early
    when the task's newest run line is a newer run that also failed."""
    say("the logon task restarts the shell within a minute")
    deadline = time.monotonic() + RESTART_WAIT
    next_status = time.monotonic() + STATUS_POLL
    while True:
        state, info = probe()
        if state == "up":
            return info
        now = time.monotonic()
        if now >= deadline:
            raise _failed_to_start("the logon task did not restart the shell")
        if now >= next_status:
            next_status = now + STATUS_POLL
            row = task_row(_units_status())
            last = getattr(row, "last", None) if row is not None else None
            at = _run_line_at(last)
            if (last and last != first_last and at is not None and at >= int(started)
                    and last.get("exit") not in (0, None)):
                raise _failed_to_start(f"the shell failed again (exit {last.get('exit')})")
        _sleep(POLL)


def ensure_shell(port: int, *, environ: Mapping[str, str] | None = None,
                 say: Callable[[str], Any] = print) -> RuntimeInfo | None:
    """Make sure a shell of this user holds the port. Returns the shell's runtime info, or
    None when this user's socket holds the port and no shell runs yet. Raises `Refused`."""
    started = _clock()
    environ = os.environ if environ is None else environ
    rows = _units_status()
    _check_locators(rows, environ)

    state, info = probe()
    if state == "up":
        return info
    if lock_held():
        found = _wait_up(UP_WAIT)
        if found is None:
            raise _failed_to_start("a shell holds page.lock but did not answer within "
                                   f"{UP_WAIT:g} s")
        return found
    if state == "foreign":
        old = info.server_id
        deadline = time.monotonic() + STALE_WAIT
        while True:
            fresh = read_runtime(MODULE)
            if fresh is not None and fresh.server_id != old:
                again, info = probe()
                if again == "up":
                    return info
                raise Refused(_held_text(port))
            if time.monotonic() >= deadline:
                raise Refused(_held_text(info.port))
            _sleep(POLL)

    sock = socket_row(rows)
    if sock is not None and sock.state == "active":
        return None
    if sock is not None:
        detail = f" ({sock.detail})" if getattr(sock, "detail", "") else ""
        say(f"the shell's socket unit is {sock.state}{detail}; `moxi install ui` writes "
            "and starts it again")

    if _port_taken(port):
        raise Refused(_held_text(port))

    task = task_row(rows)
    if task is not None:
        if task.enabled is False:
            say("the logon task is disabled; starting a shell outside it")
        else:
            try:
                acted = _units_start()
            except Exception as err:  # noqa: BLE001 - any failure takes the detached start
                acted, reason = [], str(err)
            else:
                reason = "; ".join(line for a in acted for line in a.lines) or "no detail"
            if acted and all(a.ok for a in acted):
                if task.state == "failed":
                    return _wait_restart(task.last, started, say)
                found = _wait_up(UP_WAIT)
                if found is None:
                    raise _failed_to_start(f"the shell did not answer within {UP_WAIT:g} s")
                return found
            say(f"the logon task did not start ({reason}); starting a shell outside it")

    spawn_shell()
    found = _wait_up(UP_WAIT)
    if found is None:
        raise _failed_to_start(f"the shell did not answer within {UP_WAIT:g} s")
    return found


def _sweep_redirects(directory: Path) -> None:
    now = _clock()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        path = directory / name
        try:
            if now - path.stat().st_mtime > REDIRECT_AGE:
                path.unlink()
        except OSError:
            pass


_REDIRECT = ('<!doctype html>\n<html><head><meta charset="utf-8">'
             '<meta http-equiv="refresh" content="0; url={url}"><title>Moxi</title></head>'
             "<body></body></html>\n")


def write_redirect(url: str) -> Path:
    """A page in a user-only directory that sends the browser to `url`, for systems where
    the URL must not appear in any process's argv. Old pages there are swept first."""
    directory = layout().state_dir() / "open"
    files.private_dir(directory)
    _sweep_redirects(directory)
    path = directory / f"{secrets.token_hex(8)}.html"
    files.atomic_write(path, _REDIRECT.format(url=url), mode=0o600)
    return path


def hand_over(url: str, *, printed: bool, say: Callable[[str], Any] = print) -> None:
    """Pass the sign-in URL to the browser, or print it."""
    if printed:
        say(url)
        return
    if _wsl():
        say("Paste this into your browser to sign in (it works once, within 30 s):")
        say(url)
        return
    if _platform() in ("darwin", "win32"):
        target = write_redirect(url).as_uri()
    else:
        target = url
    if not _open_browser(target):
        say("no browser could be started; open this URL to sign in (once, within 30 s):")
        say(url)


def cmd_open(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    port = config.port()
    info = ensure_shell(port, say=say)
    if info is not None and info.port:
        port = info.port
    if args.no_browser and not args.print:
        say(f"the shell holds http://127.0.0.1:{port}/")
        return 0
    tickets = session.Tickets(layout().runtime_dir(create=True) / TICKETS_DIR)
    hand_over(session.signin_url(port, tickets.mint()), printed=args.print, say=say)
    return 0


# --- status, stop, signout --------------------------------------------------------------


def _running() -> RuntimeInfo | None:
    """The running shell's runtime info, proved; None when no shell answers. Raises
    `Refused` when another program answers on its port."""
    state, info = probe()
    if state == "up":
        return info
    if state == "foreign":
        raise Refused(_held_text(info.port))
    return None


def cmd_status(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    held = lock_held()
    info = _running()
    report: dict[str, Any] = {"running": info is not None, "lock_held": held}
    if info is not None:
        report.update(pid=info.pid, port=info.port, server_id=info.server_id,
                      version=info.version, started_at=info.started_at,
                      url=f"http://127.0.0.1:{info.port}/")
        try:
            status, data = call(info, "GET", SERVERS_PATH, timeout=2.0)
        except (OSError, TimeoutError, ValueError):
            status, data = 0, None
        if status == 200:
            report["servers"] = data
    report["units"] = [{"kind": row.kind, "state": row.state, "detail": row.detail}
                       for row in _units_status()
                       if getattr(row, "module", None) == MODULE]
    if args.json:
        say(json.dumps(report, sort_keys=True))
        return 0
    if info is not None:
        say(f"shell: running, pid {info.pid}, {report['url']}, since {info.started_at}")
    elif held:
        say("shell: starting or stopping")
    else:
        say("shell: not running")
    for unit in report["units"]:
        detail = f" ({unit['detail']})" if unit["detail"] else ""
        say(f"{unit['kind']}: {unit['state']}{detail}")
    servers = report.get("servers")
    if isinstance(servers, Mapping):
        for name, item in sorted(servers.items()):
            say(f"page {name}: {item.get('state', '?') if isinstance(item, Mapping) else item}")
    return 0


def cmd_stop(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    info = _running()
    if info is None:
        say("no shell is running")
        return 0
    status, _ = call(info, "POST", STOP_PATH, {})
    if status not in (200, 202, 204):
        raise Refused(f"the shell refused the stop ({status})")
    say("the shell is stopping")
    return 0


def cmd_signout(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    info = _running()
    if info is not None:
        status, _ = call(info, "POST", SIGNOUT_API, {})
        if status != 200:
            raise Refused(f"the shell refused the signout ({status})")
    elif lock_held():
        raise Refused("a shell is starting or stopping; try again in a moment", 75)
    else:
        session.clear_stored(layout().state_dir())
    say("every browser is signed out")
    return 0


def cmd_serve(args: argparse.Namespace, say: Callable[[str], Any]) -> int:
    from . import serve

    return serve.main([])


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi ui") -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="Moxi's browser shell: one origin for every module's page.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True
    opening = verbs.add_parser(
        "open", help="make sure the shell runs, and sign this browser in",
        description="Make sure a shell of yours holds the port, then sign in with a one-use "
                    "link, opened in your browser.")
    opening.add_argument("--print", action="store_true",
                         help="print the sign-in URL instead of opening a browser, for an "
                              "SSH forward or a browser elsewhere")
    opening.add_argument("--no-browser", action="store_true",
                         help="only make sure the shell runs; with --print, print the URL")
    verbs.add_parser("serve", help="run the shell in the foreground",
                     description="Run the shell in the foreground until stopped.")
    status = verbs.add_parser("status", help="say whether the shell runs, and its page servers",
                              description="The runtime file, a health call, the listener and "
                                          "each page server's state.")
    status.add_argument("--json", action="store_true", help="print one JSON object")
    verbs.add_parser("stop", help="stop the shell",
                     description="Stop the shell. Under a socket unit the next connection "
                                 "starts it again.")
    verbs.add_parser("signout", help="sign every browser out",
                     description="End every browser session the shell holds.")
    args = parser.parse_args(argv)
    say = print
    handlers = {"open": cmd_open, "serve": cmd_serve, "status": cmd_status, "stop": cmd_stop,
                "signout": cmd_signout}
    try:
        return handlers[args.verb](args, say)
    except Refused as err:
        print(f"{prog}: {err}", file=sys.stderr)
        return err.code
    except NotOurServer:
        print(f"{prog}: {_held_text(None)}", file=sys.stderr)
        return 1
    except (ValueError, ConfigError) as err:
        print(f"{prog}: {err}", file=sys.stderr)
        return 1
