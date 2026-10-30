"""`moxi-turns-codex-host`: a hosted Codex session as a process of its own.

    moxi-turns-codex-host --cwd DIR [--model M] [--sandbox MODE | --permissions PROFILE]
                          [--option KEY=VALUE]... [--options-file F]
                          [--instructions-file F] [--resume THREAD_ID] [--no-window]

An internal entry, spawned and never typed: a launcher spawns it (or execs it in a
terminal pane), and comms' Codex wrapper execs it once it has bound its name and forked
its adapter, so every Codex launch takes this one path. It runs `turns.codex.launch()`
with the keywords `launch_args` reads from the flags, and the session is the host
process itself: a caller finds it with `turns.wait_ready(pid=<the host's pid>)`, which
returns the `Session` once the first load is accepted, with Codex's thread id as its
`session_id`.

The flags build the thread parameters each `thread/start` and `thread/resume` sends:
top-level `sandbox` or `permissions` (one or the other) and `model`, and a nested
`config` of Codex config overrides. `--option KEY=VALUE` sets one entry of `config`:
`KEY` is a TOML dotted key, each dot outside quotes a nesting level, so a quoted part
keeps its dots and loses its quotes (`projects."/home/x/my.dir".trust_level`); `VALUE`
is read as TOML, as `codex -c` reads it (text that is not TOML is a string). `--options-file` holds those parameters whole
as one JSON object; flags beside it replace its keys, and `--sandbox` or
`--permissions` replaces the file's choice of either. The sandbox has one spelling,
`--sandbox` (or the file's `sandbox`), so `sandbox_mode` in `config` is refused; a
thread that names a sandbox has its load answers checked by
`turns.codex.verify_options`. A key turns owns is refused before anything starts.

The host never reads its stdin. It ends on a lifeline only when it was given one: with
`MOXI_LIFELINE_FD` set (a Moxi parent spawned it with `lifeline="fd"`), the end of that
pipe ends it. It also ends when its window's `codex --remote` exits, when its endpoint
is gone, and on SIGTERM, SIGINT or SIGHUP (CTRL_BREAK on Windows). Its environment
passes through unchanged to the app-server, and so to the agent's shell.

Exit status: 0 once the session has ended (the endpoint stopped), 1 when the launch
failed (the reason on stderr), 2 for a bad flag. With a window, the host's log goes to
`codex-host.log` in turns' state folder, since the terminal belongs to the window;
without one, to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import tomllib
from pathlib import Path

PROG = "moxi-turns-codex-host"
#: The sandbox modes `--sandbox` takes.
SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")
#: The Codex config key that would name the sandbox a second way.
SANDBOX_CONFIG_KEY = "sandbox_mode"
#: The host's log file in turns' state folder, when it runs a window.
LOG_FILE = "codex-host.log"
# How often the host looks whether its endpoint has ended, in seconds.
_POLL = 0.5
# The core's lifeline variable (`moxi.proc.LIFELINE_ENV`): set only when a Moxi parent
# spawned the host with `lifeline="fd"`.
_LIFELINE_ENV = "MOXI_LIFELINE_FD"

log = logging.getLogger("moxi.turns.codex.host")


class UsageError(ValueError):
    """A flag the host refuses: exit 2, before anything starts."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise UsageError(message)


def _parser() -> _Parser:
    parser = _Parser(prog=PROG, allow_abbrev=False,
                     description="Run a hosted Codex session as a process of its own.")
    parser.add_argument("--cwd", required=True, help="the thread's working directory")
    parser.add_argument("--model", help="the model the thread runs")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--sandbox", choices=SANDBOX_MODES, help="the sandbox mode")
    choice.add_argument("--permissions", metavar="PROFILE",
                        help="a named permission profile, in place of a sandbox mode")
    parser.add_argument("--option", action="append", default=[], metavar="KEY=VALUE",
                        help="one Codex config override (KEY a TOML dotted key, VALUE TOML)")
    parser.add_argument("--options-file", metavar="F",
                        help="a JSON object of thread parameters")
    parser.add_argument("--instructions-file", metavar="F",
                        help="the thread's developer instructions")
    parser.add_argument("--resume", metavar="THREAD_ID",
                        help="resume this thread instead of starting one")
    parser.add_argument("--no-window", dest="window", action="store_false",
                        help="run no Codex window in this terminal")
    return parser


def launch_args(argv) -> dict:
    """The keywords of `turns.codex.launch()` from the host's flags: `cwd`, `options`,
    `instructions`, `resume` and `window`. Reads the options and instructions files.
    Raises `UsageError` for any flag the host refuses."""
    from .codex import OWNED_OPTIONS

    if isinstance(argv, (str, bytes)):
        raise TypeError("argv is a sequence of arguments, not one string")
    args = _parser().parse_args(list(argv))
    cwd = Path(args.cwd)
    if not cwd.is_dir():
        raise UsageError(f"--cwd {args.cwd} is not a directory")

    options = _options_file(args.options_file) if args.options_file else {}
    config = options.pop("config", None)
    if config is None:
        config = {}
    elif not isinstance(config, dict):
        raise UsageError(f"{args.options_file}: config is a JSON object of Codex "
                         "config overrides")
    if args.sandbox or args.permissions:
        # The sandbox mode and a permission profile are one choice.
        options.pop("sandbox", None)
        options.pop("permissions", None)
    if args.sandbox:
        options["sandbox"] = args.sandbox
    if args.permissions:
        options["permissions"] = args.permissions
    if args.model:
        options["model"] = args.model
    for item in args.option:
        key, value = _option(item)
        _put(config, key, value)
    if config or args.option:
        options["config"] = config

    owned = sorted(OWNED_OPTIONS.intersection(options))
    if owned:
        raise UsageError(f"the options name keys turns owns: {', '.join(owned)}")
    if "sandbox" in options and "permissions" in options:
        raise UsageError("sandbox and permissions are one choice; name one")
    if "sandbox" in options and options["sandbox"] not in SANDBOX_MODES:
        raise UsageError(f"sandbox {options['sandbox']!r} is not one of "
                         f"{', '.join(SANDBOX_MODES)}")
    if SANDBOX_CONFIG_KEY in config:
        raise UsageError(f"{SANDBOX_CONFIG_KEY} is not set as an option; "
                         "name the sandbox with --sandbox")

    instructions = None
    if args.instructions_file:
        try:
            instructions = Path(args.instructions_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError(f"--instructions-file {args.instructions_file}: "
                             f"{_why(exc)}") from None
    if args.resume is not None and not args.resume.strip():
        raise UsageError("--resume names a thread id")
    return {"cwd": str(cwd), "options": options, "instructions": instructions,
            "resume": args.resume, "window": args.window}


def _options_file(name: str) -> dict:
    try:
        value = json.loads(Path(name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise UsageError(f"--options-file {name}: {_why(exc)}") from None
    if not isinstance(value, dict):
        raise UsageError(f"--options-file {name}: not a JSON object")
    return value


def _option(item: str) -> tuple[list[str], object]:
    # The key ends at the first `=` outside quotes: the first `=` whose text before it
    # reads as a whole dotted key.
    at = item.find("=")
    while at != -1:
        parts = _key_parts(item[:at])
        if parts is not None:
            return parts, _toml_value(item[at + 1:], item)
        at = item.find("=", at + 1)
    raise UsageError(f"--option {item!r}: expected KEY=VALUE, KEY a TOML dotted key")


def _key_parts(key: str) -> list[str] | None:
    """The parts of `key` read as a TOML dotted key, quotes taken off, or None when it
    is not one."""
    if "\n" in key or "\r" in key:
        return None
    try:
        table = tomllib.loads(f"{key} = 0")
    except tomllib.TOMLDecodeError:
        return None
    parts = []
    node = table
    while isinstance(node, dict):
        if len(node) != 1:
            return None
        (part, node), = node.items()
        parts.append(part)
    # One chain of single-key tables ending at the placeholder, or not a bare key.
    if node != 0 or isinstance(node, bool) or not parts:
        return None
    return parts


def _toml_value(text: str, item: str):
    """`text` read as a TOML value, or, when it is not one, as a string with its
    surrounding quotes taken off (what `codex -c` does)."""
    try:
        value = tomllib.loads(f"value = {text}")["value"]
    except tomllib.TOMLDecodeError:
        return text.strip().strip("\"'")
    try:
        json.dumps(value)
    except TypeError:
        raise UsageError(f"--option {item!r}: a date or time is not a thread "
                         "parameter") from None
    return value


def _put(config: dict, key: list[str], value) -> None:
    for part in key[:-1]:
        inner = config.get(part)
        if not isinstance(inner, dict):
            inner = config[part] = {}
        config = inner
    config[key[-1]] = value


def _why(exc: BaseException) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__


# -- the process -------------------------------------------------------------------------

class _Stopped(BaseException):
    """A signal or the lifeline asked the host to end."""


class _Life:
    """How the host is asked to end. The first ask raises `_Stopped` in the main
    thread; later ones only keep the first reason."""

    def __init__(self):
        self.reason: str | None = None
        self.armed = True
        self.lifeline_closed = False

    def install(self) -> None:
        names = ["SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK"]
        for name in names:
            number = getattr(signal, name, None)
            if number is not None:
                signal.signal(number, self._on_signal)

    def _on_signal(self, signum, _frame) -> None:
        if self.lifeline_closed:
            why = "its lifeline closed"
        else:
            why = signal.Signals(signum).name
        if self.reason is None:
            self.reason = why
        if self.armed:
            self.armed = False
            raise _Stopped(self.reason)

    def on_lifeline(self) -> None:
        """The lifeline's end, on the lifeline's thread: ask the main thread to stop."""
        import _thread

        self.lifeline_closed = True
        _thread.interrupt_main(signal.SIGTERM)


def _log_to(window: bool) -> None:
    fmt = f"{PROG}[%(process)d] %(asctime)s %(levelname)s %(name)s %(message)s"
    if not window:
        logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=fmt)
        return
    from moxi.paths import Layout

    from .doctor import MODULE

    try:
        folder = Layout(MODULE).state_dir()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        logging.basicConfig(filename=str(folder / LOG_FILE), level=logging.INFO,
                            format=fmt)
    except OSError:
        # No log is better than one written over the window.
        logging.basicConfig(handlers=[logging.NullHandler()], level=logging.INFO)


def main(argv: list[str] | None = None) -> int:
    """`moxi-turns-codex-host`. See the module's docstring."""
    from moxi import exit as moxi_exit
    from moxi import proc as moxi_proc

    try:
        kwargs = launch_args(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr, flush=True)
        return moxi_exit.USAGE

    _log_to(kwargs["window"])
    life = _Life()
    life.install()
    if kwargs["window"] and hasattr(signal, "SIGTSTP"):
        from .codex_window import JOB_CONTROL_SIGNALS

        # The window inherits what the host ignores; give it the defaults, so its own
        # job control (Ctrl-Z) works whatever the host's parent set.
        for sig in JOB_CONTROL_SIGNALS:
            signal.signal(sig, signal.SIG_DFL)
    if os.environ.get(_LIFELINE_ENV):
        moxi_proc.lifeline(on_close=life.on_lifeline)

    from . import codex

    endpoint = None
    try:
        _, endpoint = codex._launch(**kwargs)
        log.info("hosting Codex thread %s", endpoint.harness.session_id)
        while not endpoint.wait_closed(_POLL):
            pass
    except _Stopped:
        if endpoint is None:
            print(f"{PROG}: the launch was stopped before the session was ready "
                  f"({life.reason})", file=sys.stderr, flush=True)
            return moxi_exit.ERROR
        life.armed = False
        endpoint.stop(f"the Codex host was stopped ({life.reason})")
    except Exception as exc:  # noqa: BLE001 - the launch failed; say why and exit 1
        life.armed = False
        if endpoint is not None:
            endpoint.stop(f"the Codex host failed: {exc}")
        print(f"{PROG}: the Codex launch failed: {_why(exc)}", file=sys.stderr,
              flush=True)
        return moxi_exit.ERROR
    life.armed = False
    log.info("the session ended: %s", endpoint.gone_reason)
    return moxi_exit.OK


__all__ = ["PROG", "SANDBOX_MODES", "UsageError", "launch_args", "main"]


if __name__ == "__main__":
    sys.exit(main())
