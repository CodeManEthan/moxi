"""memory's setup and teardown: what `moxi install memory` runs, and what an uninstall runs.

`setup()` is the announcement's `SETUP`. In order:

1. **Roots.** Which harness roots sessions found, and how many sessions each holds. When
   it finds Codex sessions that turns didn't start, it says memory reads them only on a
   catch-up walk, and points at the `ingest` timer that runs one every hour: the install
   asks about the timer right after setup, and setup prints the line that turns it on.
2. **Retention.** memory indexes transcripts where each harness keeps them, so nothing
   outlives a harness's own cleanup. Each harness's setting is read through sessions'
   `retention()` and printed with what to raise. Setup changes no retention setting.
3. **Claude Code's auto memory.** When Claude Code's user settings don't set
   `autoMemoryEnabled` to false and `CLAUDE_CODE_DISABLE_AUTO_MEMORY` isn't set, setup
   says Moxi isn't meant to run beside a second memory tool and offers to turn auto memory
   off. It changes the file only on a yes, and only that one key: it keeps the file's
   mode, keeps a symbolic link a link by writing its target, and keeps every other key's
   value. The file is written back from its parsed form, so key order and indentation may
   change, and setup says so before it asks. It refuses a file it can't parse, and says
   why. With no yes, or nobody at the keyboard, it writes nothing and prints the key to
   set. When auto memory is already off it says nothing, and so it does on a machine with
   no Claude Code, where neither its settings file nor the folder that holds it exists.
4. **The first full ingest.** It offers to start it, detached, with the line to watch it.

Redaction speed: setup says nothing about secrets' engine. The first full ingest runs
fast enough on secrets' fallback, and where no ready wheel of the fast engine fits, its
install would build from source; `moxi doctor secrets` offers it where one fits.

`teardown()` is the announcement's `TEARDOWN`. It leaves Claude Code's settings as they
are and says that auto memory is not turned back on.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from typing import TextIO

MODULE = "memory"
#: Each harness's name in setup's lines.
NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
#: Claude Code's setting for auto memory, and the variable that turns it off.
AUTO_MEMORY_KEY = "autoMemoryEnabled"
AUTO_MEMORY_ENV = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"
#: The client name turns' Codex host gives Codex, which Codex writes as a rollout's
#: `originator`. A rollout with another originator was started outside turns.
TURNS_CODEX_CLIENT = "moxi-turns"
#: The line that turns the ingest timer on.
TIMER_LINE = "moxi config set memory ingest.timer true"
#: The line that starts a full ingest by hand, and the one that watches it.
INGEST_LINE = "moxi memory ingest --all"
STATUS_LINE = "moxi memory status"


def _say(out: TextIO, text: str) -> None:
    print(f"memory: {text}", file=out)


def _ask(question: str) -> str:
    try:
        return input(question)
    except EOFError:
        print()
        return ""


def _yes(answer: str) -> bool | None:
    text = answer.strip().lower()
    if text in ("y", "yes"):
        return True
    if text in ("", "n", "no"):
        return False
    return None


def _confirm(ask: Callable[[str], str], question: str) -> bool:
    """True only on a typed yes; three answers that are neither read as no."""
    for _ in range(3):
        answer = _yes(ask(question))
        if answer is not None:
            return answer
    return False


def _harnesses(env) -> tuple[str, ...]:
    from moxi.config import ConfigError

    from . import settings

    try:
        return tuple(settings.read(env)["harnesses"])
    except ConfigError:
        return settings.HARNESSES


# -- 1. the roots -----------------------------------------------------------------------

def _spawned(header, unknown) -> bool:
    spawn = getattr(header, "spawn", None)
    return spawn is not None and spawn is not unknown


def _outside_turns(header) -> bool:
    meta = getattr(header, "meta", None)
    originator = meta.get("originator") if isinstance(meta, Mapping) else None
    return originator != TURNS_CODEX_CLIENT


def roots(harnesses, out: TextIO) -> int:
    """Print each harness's root and how many sessions it holds; return how many Codex
    sessions turns didn't start."""
    from moxi_sessions import api

    from .ingest import imported

    outside = 0
    for harness in harnesses:
        name = NAMES.get(harness, harness)
        try:
            listing = api.sources(harness, heads=(harness == "codex"))
        except Exception as exc:  # noqa: BLE001 - a root that can't be read is said
            _say(out, f"{name}: its root can't be read: {exc}")
            continue
        if not listing.ok:
            _say(out, f"{name}: no sessions found ({listing.root}: {listing.reason})")
            continue
        count = 0
        for entry in listing:
            source = entry.source
            if source.agent_id is not None:
                continue
            header = entry.header
            if harness == "codex":
                if header is not None and (_spawned(header, api.UNKNOWN)
                                           or imported(header)):
                    continue
                if header is not None and _outside_turns(header):
                    outside += 1
            if harness == "opencode" and header is not None:
                if getattr(header, "parent", None) not in (None, api.UNKNOWN):
                    continue
            count += 1
        _say(out, f"{name}: {count} session{'s' if count != 1 else ''} in "
                  f"{listing.root}")
    return outside


def timer_lines(outside: int, timer_on: bool, out: TextIO) -> None:
    """The Codex sessions turns didn't start, and the timer that reads them."""
    if not outside:
        return
    _say(out, f"{outside} Codex session{'s' if outside != 1 else ''} here "
              f"{'were' if outside != 1 else 'was'} not started through turns. memory "
              "reads such a session only on a catch-up walk, which runs when some other "
              "session ends; if Codex compresses its rollout first, it is not read.")
    if not timer_on:
        _say(out, "the ingest timer runs that walk every hour. Turn it on when the "
                  f"install asks, or later with: {TIMER_LINE}")


# -- 2. retention -----------------------------------------------------------------------

def retention_lines(harnesses, out: TextIO, env=None) -> None:
    """memory's retention head, then each harness's lines from sessions."""
    from moxi_sessions import api

    _say(out, "memory indexes transcripts where each harness keeps them, so nothing "
              "outlives a harness's own cleanup. Setup changes no retention setting.")
    for item in api.retention(env):
        if item.harness in harnesses:
            for line in item.lines:
                _say(out, line)


# -- 3. Claude Code's auto memory -------------------------------------------------------

class Unreadable(Exception):
    """A settings file setup won't write, and why."""


def _settings_file(env) -> str:
    from moxi_sessions import api

    for item in api.retention(env):
        if item.harness == "claude" and item.file:
            return item.file
    raise Unreadable("Claude Code's settings file is not known")


def _load(path: str) -> dict | None:
    """The parsed settings, or None when the file doesn't exist. Raises `Unreadable`."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Unreadable(f"{path} can't be read: {exc.strerror or exc}") from None
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Unreadable(f"{path} can't be parsed: {exc}") from None
    if not isinstance(parsed, dict):
        raise Unreadable(f"{path} doesn't hold a JSON object")
    return parsed


def auto_memory_on(env: Mapping[str, str], data: dict | None) -> bool:
    """Whether Claude Code's auto memory is on for these settings and this environment."""
    if env.get(AUTO_MEMORY_ENV):
        return False
    return not (data is not None and data.get(AUTO_MEMORY_KEY) is False)


def write_off(path: str) -> str:
    """Set `autoMemoryEnabled` to false in the settings file at `path`, keeping its mode,
    its link and every other key's value. Returns the file written (a link's target).
    Raises `Unreadable` when the file can't be parsed, and OSError when it can't be
    written."""
    from moxi import files

    target = os.path.realpath(path)
    data = _load(target)
    try:
        mode = os.stat(target).st_mode & 0o7777
    except FileNotFoundError:
        mode = 0o600
    data = {} if data is None else data
    data[AUTO_MEMORY_KEY] = False
    files.atomic_write(target, json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                       mode=mode)
    return target


def _no_claude_code(path: str) -> bool:
    """Whether this machine shows no sign of Claude Code: neither its settings file nor
    the folder that holds it exists."""
    return not os.path.lexists(path) and not os.path.isdir(os.path.dirname(path))


def auto_memory(ask: Callable[[str], str] | None, out: TextIO, env) -> bool:
    """Step 3. Returns False only when a write failed."""
    try:
        path = _settings_file(env)
        if _no_claude_code(path):
            return True
        data = _load(os.path.realpath(path))
    except Unreadable as exc:
        if env.get(AUTO_MEMORY_ENV):
            return True
        _say(out, f"Claude Code's auto memory may be on, but {exc}, so setup won't change "
                  f"it. To turn it off, set \"{AUTO_MEMORY_KEY}\": false there.")
        return True
    if not auto_memory_on(env, data):
        return True
    _say(out, "Claude Code's auto memory is on. Moxi isn't meant to run beside a second "
              "memory tool. Bringing Claude Code's saved memories into Moxi may come later.")
    key_line = f"set \"{AUTO_MEMORY_KEY}\": false in {path}"
    if ask is None:
        _say(out, f"to turn auto memory off, {key_line}")
        return True
    _say(out, f"setup can turn it off by setting {AUTO_MEMORY_KEY} to false in {path}. "
              "The file is written back from its parsed form, so its key order and "
              "indentation may change; every other key keeps its value.")
    if not _confirm(ask, "Turn Claude Code's auto memory off? [y/N] "):
        _say(out, f"auto memory stays on; to turn it off later, {key_line}")
        return True
    try:
        written = write_off(path)
    except Unreadable as exc:
        _say(out, f"{exc}, so setup didn't change it; to turn auto memory off, {key_line}")
        return True
    except OSError as exc:
        _say(out, f"{path} was not written: {exc.strerror or exc}; to turn auto memory "
                  f"off, {key_line}")
        return False
    _say(out, f"auto memory is off: {AUTO_MEMORY_KEY} is false in {written}")
    return True


# -- 4. the first full ingest -----------------------------------------------------------

def first_ingest(ask: Callable[[str], str] | None, out: TextIO) -> None:
    """Step 4: offer the first full ingest, started detached."""
    if ask is None:
        _say(out, f"start the first full ingest with: {INGEST_LINE}; watch it with: "
                  f"{STATUS_LINE}")
        return
    if not _confirm(ask, "Start the first full ingest now, in the background? It reads "
                         "every transcript once. [y/N] "):
        _say(out, f"start it later with: {INGEST_LINE}")
        return
    from . import start

    try:
        pid = start.start_all()
    except Exception as exc:  # noqa: BLE001 - a child that can't start is said
        _say(out, f"the first full ingest didn't start: {exc}; start it with: "
                  f"{INGEST_LINE}")
        return
    _say(out, f"asked for the first full ingest in the background (process {pid}); it "
              "runs now, or, if an ingest holds memory's writer lock, as soon as that "
              f"ingest ends; watch it with: {STATUS_LINE}")


# -- SETUP and TEARDOWN -----------------------------------------------------------------

def _timer_on(env) -> bool:
    from moxi.config import ConfigError

    from . import settings

    try:
        return bool(settings.read(env)["ingest.timer"])
    except ConfigError:
        return False


def setup(*, ask: Callable[[str], str] | None = None, out: TextIO | None = None,
          env: Mapping[str, str] | None = None) -> int:
    """`SETUP`. `ask` stands in for the question at the keyboard and returns the answer
    typed; with no terminal nothing is asked. Returns 0, or 1 when a write failed."""
    out = sys.stdout if out is None else out
    env = os.environ if env is None else env
    if ask is None and sys.stdin is not None and sys.stdin.isatty():
        ask = _ask
    harnesses = _harnesses(env)
    outside = roots(harnesses, out)
    timer_lines(outside, _timer_on(env), out)
    retention_lines(harnesses, out, env)
    ok = True
    if "claude" in harnesses:
        ok = auto_memory(ask, out, env)
    first_ingest(ask, out)
    return 0 if ok else 1


def teardown(*, out: TextIO | None = None, env: Mapping[str, str] | None = None) -> int:
    """`TEARDOWN`: leave Claude Code's settings as they are, and say so. Returns 0."""
    out = sys.stdout if out is None else out
    env = os.environ if env is None else env
    try:
        where = f" ({_settings_file(env)})"
    except Unreadable:
        where = ""
    _say(out, f"Claude Code's settings{where} are left as they are. If setup turned auto "
              f"memory off, it stays off; set {AUTO_MEMORY_KEY} to true there to turn it "
              "back on.")
    return 0


__all__ = ["AUTO_MEMORY_ENV", "AUTO_MEMORY_KEY", "INGEST_LINE", "STATUS_LINE",
           "TIMER_LINE", "TURNS_CODEX_CLIENT", "Unreadable", "auto_memory",
           "auto_memory_on", "first_ingest", "retention_lines", "roots",
           "setup", "teardown", "timer_lines", "write_off"]
