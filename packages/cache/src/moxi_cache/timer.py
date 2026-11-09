"""The timer: one owner for "this session's cache reaches age T".

**The timer decides only when to ask. The subscriber decides whether to act, on
its own reads. A timer that asks early, late, twice or not at all can waste a run or miss
one, and cannot cause an act.**

A subscription is a table of these fields:

    name          the subscriber's name, in the timer log (letters, digits, `.`, `_`, `-`)
    at            seconds relative to the session's expiry on its clock; -900 is fifteen
                  minutes before, 0 is the expiry itself
    until         optional, also relative to the expiry: no ask at or after it
    every         optional, seconds between repeat asks while the age stays in [at, until)
    requires_env  optional table: variables the session's process must carry, each a value
                  it must equal or "*" for any value that is not empty
    run           the argv to start; `{session}` and `{pid}` in an argument are replaced by
                  the session's id and its process id. No shell, so nothing else expands

Subscriptions come from two places:

1. **Modules**, by an entry point in group `moxi.cache.timers` whose target is a
   stdlib-only module holding a `TIMERS` tuple of such tables. A module's `run[0]` is one of
   its own console scripts by bare name, resolved by `moxi.paths.script` in the
   environment the watcher runs in.
2. **The user's cache config**, `[[timer.subscriber]]` tables, for a tool outside Moxi's
   environment. Their `run[0]` is an absolute path, with no `~`.

Each subscription is checked by `load`. One whose fields do not hold, or whose resolved
`run[0]` is not an executable file, is refused, and the others are kept.

**RULE: the watcher and `moxi cache timers`/`doctor` judge a subscription by the same
check (`load`) at the same inputs: the installed entry points, the config's tables and
the files `run[0]` names, as they are now.** So the watcher runs `load` on every tick, and
the CLI runs it when asked. A refusal lasts only while its cause does: a `run[0]` that
becomes executable (a venv rebuilt mid-sync), a module installed or removed, or a config
table changed is taken up at the next tick, and what `timers` and `doctor` list is what
the watcher runs. Each refusal gets one log line when it first appears, not one per tick.
One input is not re-read: a module's `TIMERS` target already imported in the watcher's
process keeps the tables it had when imported, as the rest of the watcher's code does, so
an upgrade that changes them is read when the watcher restarts.

**How it fires.** At each tick the watcher hands the timer the clock records it wrote. For
each live session whose record has a last request and a lifetime, the timer compares the
session's age with each subscription. On the first tick at or past `at`, then each
`every` after the last ask, until `until`, it checks `requires_env` against the
environment the session's process started with (`moxi.proc.environ`) and starts `run`
detached. On Linux and macOS that is a session of its own (`setsid`), stdin and stdout on
`/dev/null`, stderr where the watcher's goes. On Windows it is `moxi.proc.spawn`'s detached
child, a process outside any job the watcher runs in, with no console, and its stdout and
stderr go to a file of its own (below). It does not wait. The started process outlives the
watcher: a stop or restart of the watcher's unit ends the watcher only (`KillMode=process`,
see `service`), and the asks it started keep running.

A process's environment can't be read on Windows (`env_checkable` says so), so there a
subscription with `requires_env` never asks: the requirement counts as not met, as it
does for any process whose environment can't be read. It stops
asking a session when its clock moves (a new request, which opens a new window), when the
session leaves the live list, or when `until` passes. It keeps no memory across a restart:
after one it asks on the next tick inside a window, and not for a crossing whose window has
closed. A tick on which the live list could not be read changes nothing.

**The timer log**, `STATE/cache/timer.jsonl`: one line per ask, written when the ask's
outcome is known, which is when the started process exits (reaped at a later tick,
without waiting), when it could not be started, or when the watcher stops while it still
runs (`exit` null). Each line holds `at` (when it was asked), `subscriber`, `source`,
`session`, `pid`, `age` (seconds since the last request, at the ask), `last_request`,
`child` (the started process's id), `exit` (its exit status; minus a signal's number when
a signal ended it), `ended`, `error` (why it could not be started, or why there is no
status), and `output` (the file holding a Windows ask's output, else null). Every ask's
line carries its real exit status, on Windows too (`moxi.proc.status`). Past `LOG_LIMIT`
bytes the log moves to `timer.jsonl.1`, replacing the one before.

**A Windows ask's output**, `STATE/cache/asks/<at in ms>-<n>-<subscriber>.log`: each
Windows ask writes its stdout and stderr to a file of its own, `<n>` counting the asks this
watcher has started, so two asks never share one. Three bounds hold the directory small:

- When the ask is reaped, an empty file is deleted and its line's `output` is null, so
  only asks that wrote something keep a file; a file past `ASK_OUTPUT_LIMIT` (256 KiB)
  keeps its last 256 KiB.
- Before each Windows ask, the oldest files past the newest `ASK_FILES_KEPT - 1` are
  deleted, so with the new one `ASK_FILES_KEPT` (32) are kept. A file whose ask is still
  pending is never deleted, so while more than 31 asks run at once more files are kept. A
  delete that fails (on Windows, a file an ask from before a watcher restart still holds
  open) is left and tried again at the next ask.
- A running ask's file is not capped while it runs, as a POSIX ask's stderr into the
  watcher's log is not.

An ask still running when the watcher stops keeps its file, and its line names it. An ask
that could not be started has its file deleted and `output` null.

Linux and macOS asks keep no file: their stderr goes where the watcher's does.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from moxi import proc as _proc
from moxi.files import atomic_write
from moxi.paths import Layout, script

from .settings import MODULE

log = logging.getLogger("moxi.cache")

#: The entry point group a module's subscriptions are declared in.
GROUP = "moxi.cache.timers"
#: The attribute of the entry point's module that holds its subscriptions.
ATTRIBUTE = "TIMERS"
#: The config key of the user's own subscriptions, `[[timer.subscriber]]`.
CONFIG_KEY = "timer.subscriber"
#: Where a subscription comes from: the user's config, or a module (`module <name>`).
CONFIG = "config"

#: The fields a subscription may hold, and the ones it must.
FIELDS = ("name", "at", "until", "every", "requires_env", "run")
REQUIRED = ("name", "at", "run")
#: The slots `run` may hold.
SESSION_SLOT, PID_SLOT = "{session}", "{pid}"
#: `requires_env`'s value for "carries the variable, with any value that is not empty".
ANY = "*"

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: The timer log's name under cache's state directory, and the size at which it moves.
LOG_NAME = "timer.jsonl"
LOG_LIMIT = 4 * 1024 * 1024
#: The log's format version.
LOG_VERSION = 1

#: Where Windows asks' output files go, under cache's state directory beside the log; how
#: much of an ended ask's file is kept; and how many files are kept (with the new one).
ASKS_DIR = "asks"
ASK_OUTPUT_LIMIT = 256 * 1024
ASK_FILES_KEPT = 32
_ASK_FILE_RE = re.compile(r"(\d+)-(\d+)-[A-Za-z0-9._-]+\.log")

#: Two reads of a session's last request this close are the same clock. The watcher
#: computes a last request as `read_at - age`, which can differ from one read to the next
#: by a rounding error.
SAME_CLOCK = 1.0


def log_path(env=None) -> Path:
    """`STATE/cache/timer.jsonl`."""
    return Layout(MODULE).state_dir(env) / LOG_NAME


# --- subscriptions --------------------------------------------------------------------------

@dataclass(frozen=True)
class Subscription:
    """One checked subscription. `run` is as written; `argv0` is `run[0]` resolved."""

    name: str
    source: str
    at: float
    run: tuple[str, ...]
    argv0: str
    until: float | None = None
    every: float | None = None
    requires_env: tuple[tuple[str, str], ...] = ()

    def argv(self, session: str, pid: int) -> list[str]:
        """The argv to start for one session: `run` with its slots filled."""
        return [self.argv0] + [arg.replace(SESSION_SLOT, session).replace(PID_SLOT, str(pid))
                               for arg in self.run[1:]]

    def opens(self, last_request: float, ttl: float) -> float:
        """When the window opens on a clock."""
        return last_request + ttl + self.at

    def closes(self, last_request: float, ttl: float) -> float | None:
        """When the window closes on a clock, or None when it never does."""
        return None if self.until is None else last_request + ttl + self.until

    def as_dict(self) -> dict:
        return {"name": self.name, "source": self.source, "at": self.at,
                "until": self.until, "every": self.every,
                "requires_env": dict(self.requires_env), "run": list(self.run),
                "argv0": self.argv0}


@dataclass(frozen=True)
class Refusal:
    """A subscription the timer will not run, and why."""

    name: str | None
    source: str
    reason: str

    def label(self) -> str:
        return f"{self.name or '(unnamed)'} ({self.source})"

    def as_dict(self) -> dict:
        return {"name": self.name, "source": self.source, "reason": self.reason}


class _Refused(Exception):
    pass


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise _Refused(f"{what} is {value!r}, not a number of seconds")
    return float(value)


def _executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def resolve_config_argv0(argv0: str) -> str:
    """A config subscription's `run[0]`: an absolute path to an executable file."""
    if argv0.startswith("~"):
        raise _Refused(f"run[0] {argv0!r} starts with ~, which nothing expands: write the "
                       "absolute path")
    if not os.path.isabs(argv0):
        raise _Refused(f"run[0] {argv0!r} is not an absolute path")
    if not _executable(argv0):
        raise _Refused(f"run[0] {argv0} is not an executable file")
    return argv0


def module_argv0_resolver(scripts: Iterable[str],
                          find: Callable[[str], Path] | None = None) -> Callable[[str], str]:
    """A module subscription's `run[0]` rule: a bare name, one of the module's own console
    scripts (`scripts`), resolved by `moxi.paths.script` to an executable file."""
    own = frozenset(scripts)
    find = find or script

    def resolve(argv0: str) -> str:
        if not argv0 or "/" in argv0 or argv0 in (".", ".."):
            raise _Refused(f"run[0] {argv0!r} is not a console script's bare name")
        if argv0 not in own:
            raise _Refused(f"run[0] {argv0!r} is not one of the module's own console "
                           f"scripts ({', '.join(sorted(own)) or 'it declares none'})")
        try:
            path = str(find(argv0))
        except FileNotFoundError as e:
            raise _Refused(str(e)) from None
        if not _executable(path):
            raise _Refused(f"run[0] {argv0} resolves to {path}, which is not an "
                           "executable file")
        return path

    return resolve


def check(raw, *, source: str, resolve: Callable[[str], str]) -> Subscription | Refusal:
    """One subscription as written, checked. `resolve` turns `run[0]` into the path to
    start, or refuses it (the rule differs between a module's and the config's)."""
    name = raw.get("name") if isinstance(raw, Mapping) else None
    name = name if isinstance(name, str) else None
    try:
        if not isinstance(raw, Mapping):
            raise _Refused(f"is a {type(raw).__name__}, not a table")
        unknown = sorted(set(map(str, raw)) - set(FIELDS))
        if unknown:
            raise _Refused(f"has fields a subscription does not take ({', '.join(unknown)}); "
                           f"it takes {', '.join(FIELDS)}")
        missing = [f for f in REQUIRED if f not in raw]
        if missing:
            raise _Refused(f"lacks {', '.join(missing)}")
        if name is None or not NAME_RE.fullmatch(name):
            raise _Refused(f"name {raw.get('name')!r} is not 1 to 64 letters, digits, "
                           "'.', '_' or '-'")
        at = _number(raw["at"], "at")
        until = _number(raw["until"], "until") if "until" in raw else None
        if until is not None and until <= at:
            raise _Refused(f"until ({until:g}) is not after at ({at:g}), so it never asks")
        every = _number(raw["every"], "every") if "every" in raw else None
        if every is not None and every <= 0:
            raise _Refused(f"every ({every:g}) is not a positive number of seconds")
        env = raw.get("requires_env", {})
        if not isinstance(env, Mapping):
            raise _Refused("requires_env is not a table of variable names")
        pairs = []
        for var, value in env.items():
            if not isinstance(var, str) or not ENV_NAME_RE.fullmatch(var):
                raise _Refused(f"requires_env names {var!r}, which is not a variable name")
            if not isinstance(value, str) or "\0" in value:
                raise _Refused(f"requires_env's {var} is {value!r}, not a string "
                               f"(a value, or {ANY!r} for any)")
            pairs.append((var, value))
        run = raw["run"]
        if not isinstance(run, (list, tuple)) or not run \
                or not all(isinstance(a, str) and "\0" not in a for a in run):
            raise _Refused("run is not a list of strings with run[0] the program")
        argv0 = resolve(run[0])
    except _Refused as e:
        return Refusal(name, source, str(e))
    return Subscription(name=name, source=source, at=at, run=tuple(run), argv0=argv0,
                        until=until, every=every, requires_env=tuple(sorted(pairs)))


def from_config(entries) -> list[Subscription | Refusal]:
    """The user's `[[timer.subscriber]]` tables, each checked."""
    if not isinstance(entries, list):
        return [Refusal(None, CONFIG, f"{CONFIG_KEY} is not a list of tables")]
    return [check(raw, source=CONFIG, resolve=resolve_config_argv0) for raw in entries]


def _console_scripts(ep) -> list[str]:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return []
    return [s.name for s in dist.entry_points if s.group == "console_scripts"]


def from_modules(entry_points: Iterable | None = None,
                 find: Callable[[str], Path] | None = None) -> list[Subscription | Refusal]:
    """Every subscription the installed modules declare, each checked. An entry point that
    does not load, or holds no `TIMERS` tuple, is one refusal named by the entry point."""
    if entry_points is None:
        entry_points = metadata.entry_points(group=GROUP)
    out: list[Subscription | Refusal] = []
    for ep in sorted(entry_points, key=lambda e: (e.name, e.value)):
        source = f"module {ep.name}"
        try:
            target = ep.load()
        except Exception as e:                                    # noqa: BLE001
            out.append(Refusal(None, source, f"entry point {ep.value} does not load "
                                             f"({type(e).__name__}: {e})"))
            continue
        timers = getattr(target, ATTRIBUTE, None)
        if not isinstance(timers, (tuple, list)):
            out.append(Refusal(None, source, f"entry point {ep.value} holds no {ATTRIBUTE} "
                                             "tuple"))
            continue
        resolve = module_argv0_resolver(_console_scripts(ep), find)
        out.extend(check(raw, source=source, resolve=resolve) for raw in timers)
    return out


@dataclass(frozen=True)
class Loaded:
    """The subscriptions that run, and the ones refused."""

    subscriptions: tuple[Subscription, ...] = ()
    refused: tuple[Refusal, ...] = ()


def combine(checked: Iterable[Subscription | Refusal]) -> Loaded:
    """Split checked subscriptions into those that run and those refused. A name already
    taken by an earlier subscription is refused, so each name in the log is one
    subscriber."""
    subs: list[Subscription] = []
    refused: list[Refusal] = []
    taken: dict[str, str] = {}
    for item in checked:
        if isinstance(item, Refusal):
            refused.append(item)
        elif item.name in taken:
            refused.append(Refusal(item.name, item.source,
                                   f"the name is already taken by a subscription from "
                                   f"{taken[item.name]}"))
        else:
            taken[item.name] = item.source
            subs.append(item)
    return Loaded(tuple(subs), tuple(refused))


def load(config_entries, *, modules: Iterable[Subscription | Refusal] | None = None
         ) -> Loaded:
    """Every subscription, checked: the modules' first, then the config's. The one check
    the watcher's timer runs at each tick and `moxi cache timers` and `doctor` run when
    asked (the module doc's RULE)."""
    if modules is None:
        modules = from_modules()
    return combine([*modules, *from_config(config_entries)])


# --- the process's environment -----------------------------------------------------------

def proc_environ(pid: int) -> dict[str, str] | None:
    """A process's environment as it was started, or None when it cannot be read: always
    on Windows, and for a process that is gone or another user's (`moxi.proc.environ`)."""
    return _proc.process_env(pid)


def env_checkable() -> bool:
    """Whether a process's environment can be read on this system, so a subscription's
    `requires_env` can be checked: True on Linux and macOS, False on Windows.

    It asks the platform, not a process, so it costs no read. Where it is False, a
    subscription with `requires_env` never asks.
    """
    return sys.platform.startswith("linux") or sys.platform == "darwin"


def env_met(sub: Subscription, environ: Mapping[str, str] | None) -> bool:
    """Whether a process's environment carries what `requires_env` asks."""
    if not sub.requires_env:
        return True
    if environ is None:
        return False
    for var, want in sub.requires_env:
        have = environ.get(var)
        if have is None or (want == ANY and have == "") or (want != ANY and have != want):
            return False
    return True


def missing_env(sub: Subscription, environ: Mapping[str, str] | None) -> list[str]:
    """The `requires_env` entries a process's environment does not meet, in words."""
    if environ is None:
        return ["the process's environment cannot be read"]
    out = []
    for var, want in sub.requires_env:
        have = environ.get(var)
        if have is None or (want == ANY and have == "") or (want != ANY and have != want):
            out.append(f"{var}={want}" if want != ANY else f"{var} set")
    return out


# --- the log -------------------------------------------------------------------------------

def append_log(path: Path, entry: dict, limit: int = LOG_LIMIT) -> None:
    """Append one line. Past `limit` bytes the log first moves to `<name>.1`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size >= limit:
            os.replace(path, path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass
    data = (json.dumps(entry) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def read_log(path: Path, tail: int = 256 * 1024) -> list[dict]:
    """The log's last lines that parse, oldest first (at most `tail` bytes of it)."""
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - tail))
            data = fh.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if len(data) == tail and lines:
        lines = lines[1:]                 # the first line may be cut
    out = []
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


# --- firing ----------------------------------------------------------------------------------

@dataclass
class Track:
    """What the timer holds of one subscription for one session: the clock it is asking
    on, and when it last asked on it."""

    last_request: float
    asked_at: float | None = None


@dataclass
class Pending:
    """A started process not yet reaped, and its log line so far."""

    proc: subprocess.Popen | Detached
    entry: dict = field(default_factory=dict)


class Detached:
    """An ask started by `moxi.proc.spawn(..., detached=True)`, which gives back a pid:
    `poll` reads how it ended through `moxi.proc.status`."""

    def __init__(self, pid: int):
        self.pid = pid

    def poll(self) -> int | None:
        """None while the process runs, else its exit status. Raises `LookupError` when
        `moxi.proc` no longer holds it (its code was dropped past `moxi.proc`'s bound,
        which a reap at every tick never reaches)."""
        return _proc.status(self.pid)


def spawn(argv: list[str], output: Path | None = None) -> subprocess.Popen | Detached:
    """Start `argv` detached, so it outlives the watcher.

    Linux and macOS: its own session, stdin and stdout on /dev/null, stderr where the
    watcher's goes; the `Popen` is returned, so its exit status is logged. `output` is not
    used there. Windows: a detached process outside any job the watcher runs in
    (`moxi.proc.spawn(..., detached=True)`), so a stop of the watcher's job does not end
    it; its stdout and stderr are appended to `output` (the null device when None), and
    its exit status is read by pid through `moxi.proc.status`.
    """
    if sys.platform == "win32":
        return Detached(_proc.spawn(argv, detached=True, output=output))
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            start_new_session=True, close_fds=True)


def due(sub: Subscription, track: Track | None, rel: float, now: float) -> bool:
    """Whether a subscription asks now: inside its window and not asked since it should."""
    if rel < sub.at or (sub.until is not None and rel >= sub.until):
        return False
    if track is None or track.asked_at is None:
        return True
    return sub.every is not None and now - track.asked_at >= sub.every


def _clock(record: Mapping) -> tuple[float, float] | None:
    lr, ttl = record.get("last_request"), record.get("ttl")
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lr, ttl))
    return (float(lr), float(ttl)) if ok else None


class Timer:
    """The timer's state and its tick. The outside effects can be replaced for tests:
    `config_entries` (the user's tables now), `modules` (the modules' checked
    subscriptions now: a callable, run at each tick, or a fixed list), `process_env` (a
    process's environment), `spawn` and `log_file`. `spawn` is called with the argv alone,
    and on Windows with the ask's output file as `output=` too."""

    def __init__(self, *, config_entries: Callable[[], object] | None = None,
                 modules: Callable[[], Iterable[Subscription | Refusal]]
                 | Iterable[Subscription | Refusal] | None = None,
                 environ: Callable[[int], Mapping[str, str] | None] = proc_environ,
                 spawn: Callable[[list[str]], subprocess.Popen | Detached] = spawn,
                 log_file: Path | None = None, now: Callable[[], float] = time.time):
        self.config_entries = config_entries or (lambda: [])
        if modules is None:
            modules = from_modules
        if not callable(modules):
            fixed = list(modules)
            modules = lambda: fixed                               # noqa: E731
        self.modules = modules
        self.environ = environ
        self.spawn = spawn
        self.log_file = log_path() if log_file is None else Path(log_file)
        self.asks_dir = self.log_file.parent / ASKS_DIR
        self.now = now
        self.loaded = Loaded()
        self._loaded_once = False
        self.tracks: dict[tuple[str, str], Track] = {}
        self.pending: list[Pending] = []
        self.asked = 0

    # -- loading ----------------------------------------------------------------------------

    def reload(self) -> Loaded:
        """Check every subscription again, at each tick, by the check `moxi cache timers`
        and `doctor` run (`load`, at the inputs as they are now: the module doc's RULE).
        When the outcome differs from the last tick's, each refusal not refused before is
        logged once."""
        loaded = load(self.config_entries(), modules=self.modules())
        if self._loaded_once and loaded == self.loaded:
            return self.loaded
        before = set(self.loaded.refused)
        self._loaded_once = True
        self.loaded = loaded
        for refusal in loaded.refused:
            if refusal not in before:
                log.warning("timer: refused subscription %s: %s", refusal.label(),
                            refusal.reason)
        names = {s.name for s in self.loaded.subscriptions}
        self.tracks = {k: v for k, v in self.tracks.items() if k[0] in names}
        log.info("timer: %d subscription(s)%s", len(self.loaded.subscriptions),
                 f", {len(self.loaded.refused)} refused" if self.loaded.refused else "")
        return self.loaded

    # -- the tick ---------------------------------------------------------------------------

    def tick(self, live: Mapping[str, Mapping] | None, now: float | None = None) -> list[dict]:
        """One tick over the watcher's records of the live sessions (`live`, keyed by
        session id), or None when the watcher could not read the live list. Returns the
        asks it started."""
        now = self.now() if now is None else now
        self.reap(now)
        loaded = self.reload()
        if live is None:
            return []
        self.tracks = {k: v for k, v in self.tracks.items() if k[1] in live}
        started = []
        for session, record in live.items():
            clock = _clock(record)
            if clock is None:
                for sub in loaded.subscriptions:
                    self.tracks.pop((sub.name, session), None)
                continue
            last_request, ttl = clock
            rel = now - last_request - ttl
            environ = None
            for sub in loaded.subscriptions:
                key = (sub.name, session)
                track = self.tracks.get(key)
                if track is not None and abs(track.last_request - last_request) > SAME_CLOCK:
                    track = None              # the clock moved: a new window
                    del self.tracks[key]
                if not due(sub, track, rel, now):
                    continue
                pid = record.get("pid")
                if not isinstance(pid, int) or isinstance(pid, bool):
                    continue
                if sub.requires_env:
                    if environ is None:
                        environ = self.environ(pid)
                    if not env_met(sub, environ):
                        continue
                if track is None:
                    track = self.tracks[key] = Track(last_request)
                track.asked_at = now
                started.append(self.ask(sub, session, pid, now, last_request))
        return started

    def ask(self, sub: Subscription, session: str, pid: int, now: float,
            last_request: float) -> dict:
        """Start one subscription's run for one session, and do not wait."""
        entry = {"v": LOG_VERSION, "at": now, "subscriber": sub.name, "source": sub.source,
                 "session": session, "pid": pid, "age": round(now - last_request, 3),
                 "last_request": last_request, "child": None, "exit": None, "ended": None,
                 "error": None, "output": None}
        self.asked += 1
        argv = sub.argv(session, pid)
        output = None
        if sys.platform == "win32":
            output = self.asks_dir / f"{int(now * 1000)}-{self.asked}-{sub.name}.log"
            self.trim_outputs()
        try:
            proc = self.spawn(argv) if output is None else self.spawn(argv, output=output)
        except OSError as e:
            entry["error"] = f"could not start {sub.argv0}: {e.strerror or e}"
            if output is not None:
                _remove(output)  # the start may have made it before it failed
            self._write(entry)
            return entry
        entry["child"] = proc.pid
        entry["output"] = None if output is None else str(output)
        self.pending.append(Pending(proc, entry))
        return entry

    def reap(self, now: float | None = None) -> None:
        """Log each started process that has exited, without waiting for the others."""
        now = self.now() if now is None else now
        still = []
        for p in self.pending:  # oldest first, as `moxi.proc.status` needs for a reused pid
            try:
                code = p.proc.poll()
            except LookupError:
                code = None
                p.entry["error"] = "its exit status is not known: it was no longer held"
            else:
                if code is None:
                    still.append(p)
                    continue
            self._end_output(p.entry)
            p.entry["exit"], p.entry["ended"] = code, now
            self._write(p.entry)
        self.pending = still

    def _end_output(self, entry: dict) -> None:
        """An ended Windows ask's file: deleted when empty (its `output` then null), cut
        to its last `ASK_OUTPUT_LIMIT` bytes when longer."""
        if entry.get("output") is None:
            return
        path = Path(entry["output"])
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            entry["output"] = None
            return
        except OSError as e:
            log.warning("timer: cannot read %s: %s", path, e)
            return
        if size == 0:
            if _remove(path):
                entry["output"] = None
            return
        if size > ASK_OUTPUT_LIMIT:
            try:
                with open(path, "rb") as fh:
                    fh.seek(-ASK_OUTPUT_LIMIT, os.SEEK_END)
                    tail = fh.read()
                atomic_write(path, tail)
            except OSError as e:
                log.warning("timer: cannot cut %s to its last %d bytes: %s", path,
                            ASK_OUTPUT_LIMIT, e)

    def trim_outputs(self) -> None:
        """Delete the oldest Windows ask files past the newest `ASK_FILES_KEPT - 1`, so
        with the ask about to start `ASK_FILES_KEPT` are kept. A pending ask's file is never
        deleted; one whose delete fails is left for the next ask to try."""
        try:
            names = os.listdir(self.asks_dir)
        except OSError:
            return
        held = {Path(p.entry["output"]).name for p in self.pending if p.entry.get("output")}
        files = sorted((int(m[1]), int(m[2]), name) for name in names
                       if (m := _ASK_FILE_RE.fullmatch(name)))
        for _, _, name in files[:max(len(files) - (ASK_FILES_KEPT - 1), 0)]:
            if name not in held:
                _remove(self.asks_dir / name)

    def close(self, now: float | None = None) -> None:
        """At the watcher's stop: log the asks whose processes still run, with no status.
        They are in sessions of their own and keep running; a Windows ask's file is kept
        and its line names it."""
        now = self.now() if now is None else now
        self.reap(now)
        for p in self.pending:
            p.entry["error"] = "still running when the watcher stopped"
            self._write(p.entry)
        self.pending = []

    def _write(self, entry: dict) -> None:
        try:
            append_log(self.log_file, entry)
        except OSError as e:
            log.warning("timer: cannot write %s: %s", self.log_file, e)


def _remove(path: Path) -> bool:
    """Delete one file; True when it is gone. A delete that fails is left as it is."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True
    except OSError as e:
        log.info("timer: cannot delete %s yet: %s", path, e)
        return False
    return True


# --- what `moxi cache timers` shows -----------------------------------------------------------

def plan(sub: Subscription, record: Mapping, now: float, environ, asks: list[dict]) -> dict:
    """When a subscription next asks about one session, from the record, the process's
    environment and the logged asks. A display, as the records are: the watcher's own
    memory decides. `asks` are the log's lines for this subscriber and session."""
    out = {"subscriber": sub.name, "state": None, "next_at": None, "opens_at": None,
           "closes_at": None, "last_ask": None, "missing_env": []}
    clock = _clock(record)
    if clock is None:
        out["state"] = "no-clock"
        return out
    last_request, ttl = clock
    opens, closes = sub.opens(last_request, ttl), sub.closes(last_request, ttl)
    out["opens_at"], out["closes_at"] = opens, closes
    on_clock = [a for a in asks if isinstance(a.get("last_request"), (int, float))
                and abs(a["last_request"] - last_request) <= SAME_CLOCK
                and isinstance(a.get("at"), (int, float))]
    last = max((a["at"] for a in on_clock), default=None)
    out["last_ask"] = last
    if sub.requires_env and not env_met(sub, environ):
        out["state"] = "env-not-met"
        out["missing_env"] = missing_env(sub, environ)
        return out
    if closes is not None and now >= closes:
        out["state"] = "closed"
    elif now < opens:
        out["state"], out["next_at"] = "waiting", opens
    elif last is None:
        out["state"], out["next_at"] = "due", now
    elif sub.every is None:
        out["state"] = "asked"
    else:
        nxt = max(last + sub.every, now)
        if closes is not None and nxt >= closes:
            out["state"] = "asked"
        else:
            out["state"], out["next_at"] = "repeating", nxt
    return out


__all__ = ["GROUP", "ATTRIBUTE", "CONFIG_KEY", "CONFIG", "FIELDS", "LOG_NAME", "LOG_LIMIT",
           "Subscription", "Refusal", "Loaded", "Timer", "Track", "check", "from_config",
           "from_modules", "combine", "load", "proc_environ", "env_checkable", "env_met",
           "missing_env", "Detached", "ASKS_DIR", "ASK_OUTPUT_LIMIT", "ASK_FILES_KEPT",
           "append_log", "read_log", "log_path", "spawn", "due", "plan",
           "resolve_config_argv0", "module_argv0_resolver"]
