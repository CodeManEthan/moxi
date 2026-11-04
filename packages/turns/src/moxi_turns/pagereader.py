"""The live list behind turns' page: every session turns can reach, kept current in the page
server's own process.

turns adds no daemon for it. The page server's handlers start one `Reader` on the page's
first read or subscribe, and it runs until the page server stops:

- one scanner thread calls `sessions()` every `RESCAN_S` seconds, since no central registry
  of endpoints exists; each scan also refreshes what every held session's `session` answer
  says (its session id after a `/clear`, its `trouble`);
- per session, one thread holds its `watch()` and one reads its `facts()` and `agents()`:
  once when the session is first seen, again once the watch has started, and again after
  each `session.ready`, `turn.started`, `turn.steered` and `turn.ended`. Events that come
  while a read runs share the one read after it.

The library is blocking, and each of its calls is bounded by the library's own timeouts.
Each session's calls run on that session's own threads, so a slow or dead endpoint holds up
only its own row: never the scan, the other sessions, the page's read or its topic.

A session leaves the list when its watch ends (its `session.gone`, or the connection
closing), or when a scan no longer finds it while no watch of it is open. A session whose
watch or reads fail stays listed, with `read_error` saying what could not be read, and its
watch is tried again every `RETRY_S` seconds. A session dropped after its watch ended is not
listed again by a scan for `HOLD_S` seconds, so the scan that runs while an endpoint is
still stopping does not bring it back.

The value (`value()`) holds only what the page's declared shapes carry, each value of its
declared type: what an endpoint sends of the wrong type is read as null (in its `session`
answer) or as a failed read (in its facts or agents), never passed on. No error text
reaches the value, since the library's messages name the endpoint's socket path; the log
has them.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("moxi.turns.page")

#: Seconds between the scans for endpoints.
RESCAN_S = 2.0
#: Seconds between tries at a watch that could not start.
RETRY_S = 2.0
#: Seconds a session dropped after its watch ended stays out of the list.
HOLD_S = 5.0

#: The reads that can fail, in the order `read_error` names them.
_READS = ("watch", "facts", "agents")


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _need(value: Any, kind: Callable[[Any], Any], what: str) -> Any:
    """`value` as `kind` reads it; raise `ValueError` naming `what` when it is not one."""
    out = kind(value)
    if out is None:
        raise ValueError(f"{what} is not of its type")
    return out


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def facts_row(facts: Any) -> dict[str, Any]:
    """A `Facts` as the page sends it. Raises `ValueError` for a value of the wrong type."""
    ready = facts.ready
    ready_row = None
    if ready is not None:
        ready_row = {"source": _text(ready.source), "at": _number(ready.at)}
    agents = facts.agents
    if agents is not None:
        agents = _need(agents, _int, "facts.agents")
    return {
        "turn_number": _need(facts.turn_number, _int, "facts.turn_number"),
        "turn_open": _need(facts.turn_open, _flag, "facts.turn_open"),
        "agents": agents,
        "draft": _need(facts.draft, _flag, "facts.draft"),
        "stale": _need(facts.stale, _flag, "facts.stale"),
        "ready": ready_row,
        "at": _need(facts.at, _number, "facts.at"),
    }


def agents_row(agents: Any) -> dict[str, Any]:
    """An `Agents` as the page sends it. Raises `ValueError` for a value of the wrong
    type."""
    entries = []
    for agent in agents.entries:
        final = agent.final
        if final is not None:
            final = _need(final, _flag, "agent.final")
        entries.append({
            "id": _need(agent.id, _text, "agent.id"),
            "kind": _need(agent.kind, _text, "agent.kind"),
            "state": _need(agent.state, _text, "agent.state"),
            "final": final,
            "description": _text(agent.description),
            "parent_id": _text(agent.parent_id),
        })
    return {"entries": entries, "complete": _need(agents.complete, _flag, "agents.complete")}


def info_row(key: str, info: dict[str, Any], capabilities: frozenset[str]) -> dict[str, Any]:
    """What a session's `session` answer and hello say, as the page sends it: a value of the
    wrong type reads as null, and a `trouble` that is not text as a fixed sentence."""
    trouble = info.get("trouble")
    if trouble is not None and not isinstance(trouble, str):
        trouble = "the endpoint reports trouble turns cannot read"
    return {
        "key": key,
        "harness": _text(info.get("harness")) or "",
        "pid": _int(info.get("pid")),
        "session_id": _text(info.get("session_id")),
        "cwd": _text(info.get("cwd")),
        "started": _number(info.get("started")),
        "capabilities": sorted(c for c in capabilities if isinstance(c, str)),
        "endpoint_version": _text(info.get("endpoint_version")),
        "mod_version": _text(info.get("mod_version")),
        "trouble": trouble or None,
    }


def read_error(failed: set[str]) -> str | None:
    """The sentence the page shows for the reads that failed, or None when none did."""
    names = [name for name in _READS if name in failed]
    if not names:
        return None
    if names == ["watch"]:
        return "turns could not watch this session; it tries again"
    what = " and ".join(name for name in names if name != "watch")
    if "watch" in names:
        return f"turns could not watch this session or read its {what}; it tries again"
    return f"turns could not read this session's {what}; what is shown may be old"


class _Held:
    """One session the reader holds: its `Session`, its two threads and what was last read.
    Every field below the threads is guarded by the reader's lock."""

    def __init__(self, reader: Reader, key: str, session: Any):
        self.reader = reader
        self.key = key
        self.session = session
        self.info = session.info
        self.capabilities = _capabilities(session)
        self.facts: dict[str, Any] | None = None
        self.agents: dict[str, Any] | None = None
        self.failed: set[str] = set()
        self.watch: Any = None
        self.watching = False
        #: Set once the first read of facts and agents has finished, read or not.
        self.first = threading.Event()
        self.dirty = threading.Event()
        self.stopped = threading.Event()
        self.dirty.set()
        name = f"turns page {key}"
        self._watcher = threading.Thread(target=self._watching, name=f"{name} watch",
                                         daemon=True)
        self._reader = threading.Thread(target=self._reading, name=f"{name} reads",
                                        daemon=True)

    def start(self) -> None:
        self._watcher.start()
        self._reader.start()

    def stop(self) -> None:
        """Stop both threads and close the watch and the session. Waits at most the
        library's connect bound: closing a connection is local, but the session's close
        waits for a reads thread that is opening its connection."""
        self.stopped.set()
        self.dirty.set()
        with self.reader._lock:
            watch, self.watch = self.watch, None
        if watch is not None:
            watch.close()
        self.session.close()

    def row(self) -> dict[str, Any]:
        out = info_row(self.key, self.info, self.capabilities)
        out["facts"] = self.facts
        out["agents"] = self.agents
        out["read_error"] = read_error(self.failed)
        return out

    # -- the watch thread ----------------------------------------------------------------

    def _watching(self) -> None:
        from .results import Gone

        while not self.stopped.is_set():
            try:
                watch = self.session.watch()
            except Exception as err:  # noqa: BLE001 - any failure is the endpoint's
                self.reader._failed(self, "watch", err)
                if self.stopped.wait(self.reader.retry_s):
                    return
                continue
            with self.reader._lock:
                if self.stopped.is_set():
                    watch.close()
                    return
                self.watch = watch
                self.watching = True
            self.reader._read_ok(self, "watch")
            self.dirty.set()
            gone = False
            try:
                for event in watch:
                    if isinstance(event, Gone):
                        gone = True
                        break
                    self.dirty.set()
            except Exception as err:  # noqa: BLE001 - a broken stream ends the watch
                log.info("the watch of %s failed: %s", self.key, err)
            finally:
                watch.close()
                with self.reader._lock:
                    self.watching = False
                    self.watch = None
            if not self.stopped.is_set():
                self.reader._gone(self, gone)
            return

    # -- the reads thread ----------------------------------------------------------------

    def _reading(self) -> None:
        while True:
            self.dirty.wait()
            if self.stopped.is_set():
                self.first.set()
                return
            self.dirty.clear()
            self._read("facts", self.session.facts, facts_row)
            if "agents" in self.capabilities:
                self._read("agents", self.session.agents, agents_row)
            self.first.set()

    def _read(self, name: str, call: Callable[[], Any], row: Callable[[Any], dict]) -> None:
        try:
            value = row(call())
        except Exception as err:  # noqa: BLE001 - any failure is the endpoint's
            if not self.stopped.is_set():
                self.reader._failed(self, name, err)
            return
        self.reader._read_ok(self, name, value)


def _capabilities(session: Any) -> frozenset[str]:
    # A session `sessions()` found knows its capabilities from its probe's hello; reading
    # the private field keeps a session that somehow lacks them from opening a connection
    # on the scanner's thread.
    found = getattr(session, "_capabilities", None)
    return frozenset(found) if found else frozenset()


class Reader:
    """The live list. `start()` begins the scans; `value()` is the list now; `listen(fn)`
    calls `fn()` from a reader thread after each change; `stop()` ends every thread and
    closes every watch."""

    def __init__(self, *, rescan_s: float | None = None, retry_s: float | None = None,
                 hold_s: float | None = None):
        self.rescan_s = RESCAN_S if rescan_s is None else rescan_s
        self.retry_s = RETRY_S if retry_s is None else retry_s
        self.hold_s = HOLD_S if hold_s is None else hold_s
        self._lock = threading.Lock()
        self._held: dict[str, _Held] = {}
        self._dropped: dict[str, float] = {}
        self._listed = threading.Event()
        self._detail: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._stop = threading.Event()
        self._started = False
        self._scanner = threading.Thread(target=self._scanning, name="turns page scan",
                                         daemon=True)

    # -- the outside ------------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self._scanner.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            held, self._held = list(self._held.values()), {}
            self._listeners.clear()
        for one in held:
            one.stop()

    @property
    def listed(self) -> bool:
        """True once the first scan has finished."""
        return self._listed.is_set()

    def listen(self, fn: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def unlisten(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def wait_first(self, timeout: float) -> bool:
        """Wait, at most `timeout` seconds, for the first scan and for the first read of
        every session it found. True when both came in time."""
        deadline = time.monotonic() + timeout
        if not self._listed.wait(timeout):
            return False
        with self._lock:
            held = list(self._held.values())
        for one in held:
            if not one.first.wait(max(0.0, deadline - time.monotonic())):
                return False
        return True

    def value(self) -> dict[str, Any]:
        """The list now: `listed`, `detail` and `sessions`, newest first."""
        with self._lock:
            rows = [one.row() for one in self._held.values()]
            listed, detail = self._listed.is_set(), self._detail
        rows.sort(key=lambda row: (-(row["started"] or 0.0), row["key"]))
        return {"listed": listed, "detail": detail, "sessions": rows}

    # -- the scanner ---------------------------------------------------------------------

    def _scanning(self) -> None:
        while not self._stop.is_set():
            self._scan()
            if self._stop.wait(self.rescan_s):
                return

    def _scan(self) -> None:
        from . import client

        try:
            found = client.sessions()
        except Exception as err:  # noqa: BLE001 - the scan runs again next period
            log.warning("listing turns' sessions failed: %s", err, exc_info=err)
            detail = (f"turns' page server could not list the sessions "
                      f"({type(err).__name__}); its log has the details")
            with self._lock:
                changed = detail != self._detail
                self._detail = detail
            first = not self._listed.is_set()
            self._listed.set()
            if changed or first:
                self._notify()
            return
        changed = self._listed.is_set() is False
        start: list[_Held] = []
        spare: list[Any] = []
        now = time.monotonic()
        with self._lock:
            if self._stop.is_set():
                spare.extend(found)
                found = []
            if self._detail is not None:
                self._detail = None
                changed = True
            seen = {}
            for session in found:
                key = session.dir.name if session.dir is not None else session.address
                if key in seen:
                    spare.append(session)
                    continue
                seen[key] = session
            self._dropped = {k: t for k, t in self._dropped.items() if t > now}
            for key, session in seen.items():
                held = self._held.get(key)
                if held is not None:
                    capabilities = _capabilities(session) or held.capabilities
                    if session.info != held.info or capabilities != held.capabilities:
                        held.info, held.capabilities = session.info, capabilities
                        changed = True
                    spare.append(session)
                elif key in self._dropped:
                    spare.append(session)
                else:
                    held = self._held[key] = _Held(self, key, session)
                    start.append(held)
                    changed = True
            gone = [held for key, held in self._held.items()
                    if key not in seen and not held.watching]
            for held in gone:
                del self._held[held.key]
                changed = True
            if not self._stop.is_set():
                self._listed.set()
        for session in spare:
            session.close()
        for held in gone:
            held.stop()
        for held in start:
            held.start()
        if changed:
            self._notify()

    # -- what the session threads report --------------------------------------------------

    def _failed(self, held: _Held, name: str, err: BaseException) -> None:
        with self._lock:
            if self._held.get(held.key) is not held:
                return
            first = name not in held.failed
            held.failed.add(name)
        if first:
            log.info("turns' page could not read the %s of %s: %s", name, held.key, err)
            self._notify()

    def _read_ok(self, held: _Held, name: str, value: dict[str, Any] | None = None) -> None:
        with self._lock:
            if self._held.get(held.key) is not held:
                return
            changed = name in held.failed
            held.failed.discard(name)
            if name != "watch" and getattr(held, name) != value:
                setattr(held, name, value)
                changed = True
        if changed:
            self._notify()

    def _gone(self, held: _Held, said: bool) -> None:
        with self._lock:
            if self._held.get(held.key) is not held:
                return
            del self._held[held.key]
            self._dropped[held.key] = time.monotonic() + self.hold_s
        log.info("%s left the list: %s", held.key,
                 "it said it is gone" if said else "its watch ended")
        held.stop()
        self._notify()

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn()
            except Exception as err:  # noqa: BLE001 - one listener never stops the rest
                log.warning("a listener of turns' page failed: %s", err, exc_info=err)


__all__ = ["HOLD_S", "RESCAN_S", "RETRY_S", "Reader", "agents_row", "facts_row", "info_row",
           "read_error"]
