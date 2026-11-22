"""The ready recorder: the session resolver's feed, for Claude and Codex alike.

turns calls it at every `ready` (a session's start, resume, clear or compaction), at
`load` (a Claude endpoint first learned its session id) and at `gone`, in the session's
endpoint process, and ignores its answer. For a launch this endpoint holds
(`listeners.claim`):

- at `ready`, the event's session id joins the launch's ids, with the event's source, and
  becomes the row's id when it has none; the row moves to `ready` when it is still
  `spawning` or `started`. So a clear, a resume or a compaction in the launched session
  adds its new id, and a nested session's ids never join;
- at `load`, the claim is the point: an endpoint that replaced the launch's own takes the
  launch at its start. The event's id joins the launch's ids with source `load` (an id
  already known is left as it is), and nothing else is written. A later `ready` of this
  endpoint naming the id its `load` added gives the id the `ready`'s source;
- at `gone`, an event `gone` is added, with turns' reason.

**The overlap.** Claude Code may start the replacing endpoint before the one it replaces
has ended, so the claim can't move yet. The launch learns the ids of the seat's own
endpoint in the order this endpoint's events arrived, and none is dropped because the old
endpoint was still ending:

- a `load` or `ready` whose claim fails because the held endpoint is a live sibling (both
  children of the launch's own `claude`, `store.held_by_sibling`) is queued. An endpoint
  whose parent is not the launch's `claude` (a nested session's) never queues or waits;
- while events are queued, every later `load` or `ready` of this endpoint joins the queue
  behind them, even one whose claim could move at once;
- one daemon thread drains the queue, so the call returns to turns at once. It checks
  every `OVERLAP_STEP_S` whether the held endpoint has ended, for up to `OVERLAP_WAIT_S`,
  then claims once. On success it handles each queued event in arrival order as it would
  have been handled live; else the queue is dropped and the next event's claim moves it.

turns calls each listener on its own thread, so this endpoint's `load` and its next
`ready` may run at once. Arrival order is the order the calls take `_lock`: each holds it
from its look at the queue through its claim and its writes, and the drain holds it while
it claims and writes the queue. So events are handled one at a time, in that order, and no
path assumes one call finished before another began. The claim is one conditional update,
so only one of them moves it. A `start` (the handoff pointer, the Codex seat) answers at
once and never queues.
"""

from __future__ import annotations

import threading
import time

from . import listeners, store

#: How long the drain waits in all for the held endpoint to end, in seconds.
OVERLAP_WAIT_S = 5.0
#: How often the drain checks whether the held endpoint has ended, in seconds.
OVERLAP_STEP_S = 0.1

_lock = threading.Lock()
_queue: list = []
_drainer: threading.Thread | None = None
#: The id this endpoint's `load` added to a launch: (endpoint, request id, session id).
_load_added: tuple[str, str, str] | None = None


def observe(event) -> None:
    """Record a `ready`, `load` or `gone` of the launched session. Answers nothing."""
    if event.event == "gone":
        _gone(event)
    elif event.event in ("ready", "load"):
        with _lock:
            if _queue:
                _queue.append(event)
            else:
                _observe(event)
    return None


def _observe(event) -> None:
    """Claim and record one `ready` or `load`, or queue it when the held endpoint is a live
    sibling of this one. Runs under `_lock`."""
    global _drainer
    conn = listeners.open_store()
    if conn is None:
        return
    try:
        row = listeners.claim(event, conn)
        if row is not None:
            _record(conn, row, event)
            return
        held = _held_by_sibling(event, conn)
    finally:
        conn.close()
    if held is not None:
        _queue.append(event)
        _drainer = threading.Thread(target=_drain, args=(held,), daemon=True,
                                    name="launcher-claim-wait")
        _drainer.start()


def _held_by_sibling(event, conn) -> str | None:
    """The endpoint holding the event's launch when it is a live sibling of this one."""
    request_id = listeners.event_env(event).get(listeners.REQUEST_ENV)
    row = store.get(conn, request_id) if request_id else None
    if row is None or row.harness != event.harness:
        return None
    return store.held_by_sibling(row, listeners.endpoint_id(), listeners.endpoint_parent())


def _drain(held: str) -> None:
    """Wait for the held endpoint `held` to end, then claim once and record the queue."""
    deadline = time.monotonic() + OVERLAP_WAIT_S
    while not store.endpoint_gone(held) and time.monotonic() < deadline:
        time.sleep(OVERLAP_STEP_S)
    with _lock:
        try:
            conn = listeners.open_store()
            if conn is None:
                return
            try:
                row = listeners.claim(_queue[0], conn)
                if row is not None:
                    for event in _queue:
                        _record(conn, row, event)
            finally:
                conn.close()
        finally:
            _queue.clear()


def _record(conn, row: store.LaunchRow, event) -> None:
    """Record a `ready` or `load` of the launch `row`, which this endpoint holds."""
    global _load_added
    session_id = getattr(event, "session_id", None)
    learned = (listeners.endpoint_id(), row.request_id, session_id)
    if event.event == "load":
        if session_id and store.learn_session(conn, row.request_id, session_id, "load"):
            _load_added = learned
        return
    if session_id:
        source = getattr(event, "source", None)
        store.learn_session(conn, row.request_id, session_id,
                            source if source in store.SOURCES else "startup",
                            replace_load=learned == _load_added)
    store.set_state(conn, row.request_id, "ready")


def _gone(event) -> None:
    conn = listeners.open_store()
    if conn is None:
        return
    try:
        row = listeners.claim(event, conn)
        if row is not None:
            store.add_event(conn, "gone", {"reason": getattr(event, "reason", None)},
                            request_id=row.request_id,
                            session_id=getattr(event, "session_id", None))
    finally:
        conn.close()


__all__ = ["OVERLAP_STEP_S", "OVERLAP_WAIT_S", "observe"]
