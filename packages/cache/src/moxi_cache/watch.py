"""The watcher, `moxi-cache-watch`: it keeps a clock record for every live Claude session.

**The watcher writes what the clock read, for display and for the timer. No
verdict is made from its records; a caller that decides about a cache calls `api.read`.**
A record can lag the transcript by a tick, so it is not evidence.

At each tick (`watch.tick_s`, 10 s by default):

1. Every third tick, and whenever a file in Claude Code's sessions folder
   (`${CLAUDE_CONFIG_DIR:-~/.claude}/sessions`) is added, removed or rewritten, it runs
   `claude agents --json` for the live interactive sessions (pid and session id). Claude
   Code rewrites a session's file there at a `/clear`, a resume and each change of status,
   so a new id is seen within a tick. The list and the folder are Claude Code's own records
   of its sessions, read as files are read.
2. For each live session it finds the transcript through sessions and stats it. When its
   size or modification time changed, or the last read was `unknown`, it reads the clock
   (`api.read`).
3. It writes `RUNTIME/cache/clock/<session>.json` by atomic replace (`records`), with
   `seen_at` at this tick whether it read or not, and removes the record of a session that
   left the live list. Every record it writes carries `warn_before` (the effective
   `segment.warn_before_s`) and `stale_after` (three times the effective tick), both in
   seconds, from the config as read at this tick, so a config change reaches every record
   within one tick (`records`). The tick that sets `stale_after` is the one the watcher
   then waits, so a fresh record never shows dim. Every record also carries `asks`, the
   timer's subscriptions this watcher will ask about the session (`Timer.asking`), or
   null while the timer has loaded nothing.
4. After every tick, whatever it wrote, it replaces its reading, `RUNTIME/cache/watch.json`
   (`records`), through `moxi.files.atomic_write`: `tick_at` is this tick, and `listed_at`
   moves to it only at a tick that held the live list and was not halted, one with no live
   session included. A write that fails is logged once per change and the tick goes on.
   The reading is not under `clock/`, so no listing of the records sees it.
5. It runs the timer's work for the tick (`timer`) over the records it wrote.

Before the first tick, `run` loads the timer's subscriptions once (`Timer.reload`), so the
first tick's records carry `asks` too.

A session id is used in a path only after the reader's own check (`records.record_path`):
an id it refuses, such as one holding `/` or `..`, gets no record and one log line.

A shape of `claude agents --json` it does not know stops its writes, with one log line
naming the shape, until the shape is one it knows again; the records then stand still and
show dim. When the list cannot be read at all (no `claude`, an exit, a timeout), the tick
writes no record and the next tick asks again. Either way the reading's `tick_at` moves
and its `listed_at` stands still, so a reader can tell a watcher that runs and cannot list
from one that stopped. Each log line is written once per change of
what it says. Codex and OpenCode sessions get no records.

The watcher runs in the foreground (`moxi cache watch`, or this script), or as a unit of
the system's supervisor, which `moxi install cache` writes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from moxi.config import Config, ConfigError
from moxi.files import atomic_write
from moxi_sessions.api import find

from . import api, records
from . import timer as _timer
from .clock import UNKNOWN, Clock
from .settings import MODULE, default, schema

log = logging.getLogger("moxi.cache")

#: `claude agents --json` runs at least every this many ticks.
AGENTS_EVERY = 3
#: How long one `claude agents --json` may take.
AGENTS_TIMEOUT = 20
#: The kind of session the watcher keeps records for.
INTERACTIVE = "interactive"
#: The shortest tick the watcher keeps, whatever the config says.
MIN_TICK = 1


class ShapeError(Exception):
    """`claude agents --json` printed a shape the watcher does not know."""


class AgentsUnreadable(Exception):
    """`claude agents --json` could not be run or printed no JSON."""


@dataclass(frozen=True)
class Live:
    """One live interactive session, as `claude agents --json` lists it."""

    pid: int
    session: str


def _keys(obj: dict) -> str:
    return "[" + ", ".join(sorted(map(str, obj))) + "]"


def parse_agents(doc) -> list[Live]:
    """The live interactive sessions in `claude agents --json`'s output.

    The list is of objects, each with an integer `pid` and a string `sessionId`; one whose
    `kind` is present and not `interactive` is skipped, and so is one whose `sessionId` is
    null or empty. Anything else raises `ShapeError` naming the shape."""
    if not isinstance(doc, list):
        raise ShapeError(f"a {type(doc).__name__}, not a list")
    live: list[Live] = []
    for item in doc:
        if not isinstance(item, dict):
            raise ShapeError(f"a list holding a {type(item).__name__}, not objects")
        pid, session, kind = item.get("pid"), item.get("sessionId"), item.get("kind")
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise ShapeError(f"an entry with keys {_keys(item)} and no integer pid")
        if kind is not None and not isinstance(kind, str):
            raise ShapeError(f"an entry whose kind is a {type(kind).__name__}")
        if session is not None and not isinstance(session, str):
            raise ShapeError(f"an entry whose sessionId is a {type(session).__name__}")
        if kind not in (None, INTERACTIVE) or not session:
            continue
        live.append(Live(pid, session))
    return live


def claude_binary(env=None) -> str | None:
    """`claude` on PATH, else at the installer's place, `~/.local/bin/claude`. A user
    service's PATH often lacks the second."""
    env = os.environ if env is None else env
    found = shutil.which("claude", path=env.get("PATH"))
    if found:
        return found
    home = env.get("HOME") or str(Path.home())
    fallback = Path(home) / ".local" / "bin" / "claude"
    return str(fallback) if os.access(fallback, os.X_OK) else None


def run_agents(timeout: float = AGENTS_TIMEOUT):
    """`claude agents --json`, parsed. Raises `AgentsUnreadable`."""
    exe = claude_binary()
    if exe is None:
        raise AgentsUnreadable("no claude on PATH or at ~/.local/bin/claude")
    try:
        proc = subprocess.run([exe, "agents", "--json"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AgentsUnreadable(f"`claude agents --json` took over {timeout:g}s") from None
    except OSError as exc:
        raise AgentsUnreadable(f"`claude agents --json` could not run ({exc})") from None
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise AgentsUnreadable(f"`claude agents --json` exited {proc.returncode}"
                               + (f": {detail[-1]}" if detail else ""))
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise AgentsUnreadable(f"`claude agents --json` printed no JSON ({exc})") from None


def sessions_dir(env=None) -> Path:
    """Claude Code's sessions folder, `${CLAUDE_CONFIG_DIR:-~/.claude}/sessions`."""
    env = os.environ if env is None else env
    config = env.get("CLAUDE_CONFIG_DIR")
    if config:
        return Path(config) / "sessions"
    home = env.get("HOME") or str(Path.home())
    return Path(home) / ".claude" / "sessions"


def sessions_stamp(sessions_dir: Path):
    """What the sessions folder holds now: each `.json` file's name, size and
    modification time. None when the folder cannot be read."""
    try:
        with os.scandir(sessions_dir) as entries:
            found = []
            for entry in entries:
                if not entry.name.endswith(".json"):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                found.append((entry.name, st.st_size, st.st_mtime_ns))
    except OSError:
        return None
    return frozenset(found)


@dataclass
class Seen:
    """What the watcher holds of one live session between ticks."""

    pid: int
    path: Path | None = None
    stat: tuple | None = None
    clock: Clock | None = None
    read_at: float | None = None
    last_request: float | None = None


def make_record(session: str, seen: Seen, now: float, *, warn_before: float,
                stale_after: float, asks: Sequence[str] | None) -> dict:
    clock = seen.clock
    return {"v": records.VERSION, "session": session, "harness": "claude", "pid": seen.pid,
            "kind": clock.kind, "last_request": seen.last_request,
            "ttl": clock.ttl_s, "read_at": seen.read_at, "seen_at": now,
            "code": clock.code, "warn_before": warn_before, "stale_after": stale_after,
            "asks": None if asks is None else list(asks)}


def write_record(records_dir: Path, record: dict) -> Path:
    """Replace one record whole: a reader sees the old file or the new, never part.
    Raises `ValueError` for a session id the reader would refuse (`records.record_path`),
    so no id names a path outside `directory`."""
    path = records.record_path(record["session"], records_dir)
    if path is None:
        raise ValueError(f"session id {record['session']!r} cannot name a record file")
    tmp = records_dir / f".{record['session']}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
        fh.write("\n")
    os.replace(tmp, path)
    return path


class Watcher:
    """The watcher's state and its tick. Every outside effect can be replaced for tests:
    `agents` (the live list), `read` (the clock), `now`, `projects_root` (where
    transcripts are found), `records_dir` (where records go), `session_files_dir` (the folder whose
    changes ask for the list again), `reading` (the reading's file; by default
    `watch.json` beside `records_dir`, which is `records.reading_path()` for the default
    directory) and `asking` (what the timer asks about a session and its pid, written as
    the record's `asks`; `run` sets it to its timer's `Timer.asking`, and by default it
    gives None).

    `warn_before` and `stale_after` are what the next tick writes into every record;
    `run` sets them from the config before each tick. They start at the keys' defaults."""

    def __init__(self, *, agents: Callable[[], object] = run_agents,
                 read: Callable[..., Clock] = api.read,
                 now: Callable[[], float] = time.time,
                 projects_root: Callable[[], str] | None = None,
                 records_dir: Path | None = None, session_files_dir: Path | None = None,
                 reading: Path | None = None,
                 asking: Callable[[str, int], Sequence[str] | None] | None = None):
        self.agents = agents
        self.asking = asking or (lambda session, pid: None)
        self.read = read
        self.now = now
        self.projects_root = projects_root or (lambda: api.projects_root(None))
        self.records_dir = records.record_dir() if records_dir is None else Path(records_dir)
        self.session_files_dir = sessions_dir() if session_files_dir is None else Path(session_files_dir)
        self.reading = (self.records_dir.parent / records.READING_NAME if reading is None
                        else Path(reading))
        self.warn_before = default("segment.warn_before_s")
        self.stale_after = records.STALE_TICKS * max(MIN_TICK, default("watch.tick_s"))
        self.ticks = 0
        #: The records this tick wrote, by session id, for the timer; None when the tick
        #: wrote nothing because the live list could not be read or has a shape it does
        #: not know.
        self.current: dict[str, dict] | None = None
        self.tick_at: float | None = None
        #: The newest tick that held the live list and was not halted; None until one.
        self.listed_at: float | None = None
        self.live: list[Live] | None = None
        self.seen: dict[str, Seen] = {}
        self._stamp = None
        self._ask = True                  # ask for the list at the next tick
        self._halted = False              # the list's shape is not one the watcher knows
        self._said: dict[str, str] = {}

    # -- logging, once per change --------------------------------------------------------

    def say(self, topic: str, message: str | None) -> None:
        """Log `message` under `topic` once, until it changes; None clears the topic."""
        if message is None:
            self._said.pop(topic, None)
            return
        if self._said.get(topic) != message:
            self._said[topic] = message
            log.warning(message)

    # -- the tick ------------------------------------------------------------------------

    def tick(self) -> list[Live]:
        """One tick, then the reading. Returns the live sessions it wrote records for."""
        try:
            return self._tick()
        finally:
            self._write_reading()

    def _tick(self) -> list[Live]:
        now = self.tick_at = self.now()
        self.current = None
        stamp = sessions_stamp(self.session_files_dir)
        if self._ask or self.ticks % AGENTS_EVERY == 0 or stamp != self._stamp:
            self._stamp = stamp
            if not self._refresh():
                self.ticks += 1
                return []
        self.ticks += 1
        if self._halted or self.live is None:
            return []
        self.records_dir.mkdir(parents=True, exist_ok=True)
        current: dict[str, dict] = {}
        for live in self.live:
            seen = self._observe(live, now)
            record = current[live.session] = make_record(
                live.session, seen, now, warn_before=self.warn_before,
                stale_after=self.stale_after, asks=self.asking(live.session, live.pid))
            try:
                write_record(self.records_dir, record)
                self.say("write", None)
            except OSError as exc:
                self.say("write", f"cannot write the clock record of {live.session}: {exc}")
        self.current = current
        self.listed_at = now
        return list(self.live)

    def _write_reading(self) -> None:
        """Replace the reading whole with this tick's. Nothing to write before a first
        tick time."""
        if self.tick_at is None:
            return
        doc = {"v": records.READING_VERSION, "pid": os.getpid(), "tick_at": self.tick_at,
               "listed_at": self.listed_at, "stale_after": self.stale_after}
        try:
            atomic_write(self.reading, json.dumps(doc) + "\n")
            self.say("reading", None)
        except OSError as exc:
            self.say("reading", f"cannot write the watcher's reading {self.reading}: {exc}")

    def _refresh(self) -> bool:
        """Ask for the live list. False when this tick writes nothing."""
        try:
            listed = parse_agents(self.agents())
        except AgentsUnreadable as exc:
            self._ask = True
            self.say("agents", f"{exc}; no clock record is written until it can be read")
            return False
        except ShapeError as exc:
            self._ask = True
            self._halted = True
            self.say("agents", f"`claude agents --json` printed {exc}, a shape the watcher "
                               "does not know; it writes no clock record until it does")
            return False
        self._ask = False
        self._halted = False
        self.say("agents", None)
        unique: dict[str, Live] = {}
        unsafe: list[str] = []
        for live in listed:
            if records.record_path(live.session, self.records_dir) is None:
                unsafe.append(live.session)
                continue
            unique.setdefault(live.session, live)
        self.say("unsafe", None if not unsafe else
                 "`claude agents --json` listed session ids that cannot name a record file "
                 f"({', '.join(map(repr, sorted(set(unsafe))))}); they get no clock record")
        self.live = list(unique.values())
        for gone in set(self.seen) - set(unique):
            del self.seen[gone]
        self._remove_others(set(unique))
        return True

    def _remove_others(self, keep: set[str]) -> None:
        """Remove the record of every session not in `keep`, and stray temporary files."""
        try:
            entries = list(self.records_dir.iterdir())
        except FileNotFoundError:
            return
        except OSError as exc:
            self.say("remove", f"cannot list {self.records_dir}: {exc}")
            return
        for path in entries:
            stray = path.name.startswith(".") and path.name.endswith(".tmp")
            if not stray and (path.suffix != ".json" or path.stem in keep):
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.say("remove", f"cannot remove {path}: {exc}")

    def _observe(self, live: Live, now: float) -> Seen:
        """Find and stat one session's transcript; read its clock when it changed."""
        seen = self.seen.get(live.session)
        if seen is None:
            seen = self.seen[live.session] = Seen(live.pid)
        seen.pid = live.pid
        if seen.path is None or not seen.path.is_file():
            try:
                found = find("claude", live.session, root=self.projects_root())
                seen.path = Path(found.hits[0].source.path) if found.found else None
            except (ConfigError, OSError) as exc:
                self.say("root", f"cannot find transcripts: {exc}")
                seen.path = None
        stat = None
        if seen.path is not None:
            try:
                st = seen.path.stat()
                stat = (str(seen.path), st.st_size, st.st_mtime_ns)
            except OSError:
                stat = None
        if seen.clock is None or stat != seen.stat or seen.clock.kind == UNKNOWN:
            clock = self.read("claude", live.session, now=now)
            seen.clock, seen.stat, seen.read_at = clock, stat, now
            seen.last_request = (now - clock.age_s
                                 if clock.age_s is not None else None)
        return seen


# --- running it ---------------------------------------------------------------------------

def _interval(config: Config, watcher: Watcher) -> int:
    """The effective tick: `watch.tick_s`, at least `MIN_TICK`."""
    tick = config.use("watch.tick_s")
    if tick < MIN_TICK:
        watcher.say("tick", f"watch.tick_s is {tick}; the watcher ticks every {MIN_TICK}s")
        return MIN_TICK
    watcher.say("tick", None)
    return tick


def _set_thresholds(config: Config, watcher: Watcher) -> int:
    """Read the config once for this tick: set what the tick writes into every record
    and return the tick it then waits, the same read that set `stale_after`."""
    interval = _interval(config, watcher)
    watcher.warn_before = config.use("segment.warn_before_s")
    watcher.stale_after = records.STALE_TICKS * interval
    return interval


def run(*, watcher: Watcher | None = None, stop: threading.Event | None = None,
        config: Config | None = None, timer: _timer.Timer | None = None) -> int:
    """Tick until `stop` is set (SIGTERM or SIGINT in the foreground), with the timer's
    work after each tick. The timer loads its subscriptions once before the first tick and
    the watcher writes `Timer.asking` into its records. Returns the exit code: 0 on a stop,
    2 when the config refuses the start."""
    config = config or Config(MODULE, schema(),
                              report=lambda config_exc: log.warning("cache's config: %s", config_exc))
    try:
        config.start()
    except ConfigError as exc:
        print(f"moxi-cache-watch: cache's config refuses the start: {exc}", file=sys.stderr)
        return 2
    watcher = watcher or Watcher()
    timer = timer or _timer.Timer(config_entries=lambda: config.use(_timer.CONFIG_KEY))
    stop = stop or threading.Event()
    log.info("watching live Claude sessions; records under %s", watcher.records_dir)
    watcher.asking = timer.asking
    try:
        try:
            timer.reload()
        except Exception as exc:                                    # noqa: BLE001
            watcher.say("timer-error", f"the timer's first load failed "
                                       f"({type(exc).__name__}: {exc})")
        while not stop.is_set():
            interval = _set_thresholds(config, watcher)
            try:
                watcher.tick()
                watcher.say("tick-error", None)
            except Exception as exc:  # noqa: BLE001 -- one failed tick must not stop the watcher
                watcher.current = None
                watcher.say("tick-error", f"a tick failed ({type(exc).__name__}: {exc})")
            try:
                timer.tick(watcher.current, watcher.tick_at)
                watcher.say("timer-error", None)
            except Exception as exc:  # noqa: BLE001 -- one timer failure must not stop the watcher
                watcher.say("timer-error", f"the timer's tick failed "
                                           f"({type(exc).__name__}: {exc})")
            stop.wait(interval)
    finally:
        timer.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """`moxi-cache-watch`: the watcher in the foreground, until SIGTERM or Ctrl-C."""
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("usage: moxi-cache-watch   (no arguments; stop it with SIGTERM or Ctrl-C)")
        return 0
    if argv:
        print(f"moxi-cache-watch: takes no arguments (got {' '.join(argv)})", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="moxi-cache-watch: %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return run(stop=stop)


__all__ = ["Watcher", "Live", "Seen", "ShapeError", "AgentsUnreadable", "parse_agents",
           "run_agents", "claude_binary", "sessions_dir", "sessions_stamp", "make_record",
           "write_record", "run", "main", "AGENTS_EVERY", "AGENTS_TIMEOUT"]
