"""Starting the ingest child, `moxi-memory-ingest`, detached, and returning at once.

No ingest work runs in a caller: a command, a turns listener or a timer only starts a
child and returns. The child skips rather than waits (it exits at once when another child
holds the writer lock), so starting one is always safe.

- `start_turn(harness, session_id)`: a turn of the session ended. Reads that session
  since its cursor. At most one is started per session per minute; a later one in the
  same minute starts nothing, and the session's `gone`, or the next walk, reads what it
  skipped.
- `start_gone(harness, session_id)`: the session's endpoint is ending. Reads the session
  to its end, then walks.
- `start_walk()`: one catch-up walk.
- `start_all()`: one read of every source from its start.
- `start_rebuild(relabel=False)`: the rebuild, which ends by starting a walk.
- `fire(event)`: a turns `turn` or `gone` event, passed to `start_turn` or `start_gone`.

Each returns the child's process id, or None when it started nothing. Standard library
at import; the core is imported at the first start.
"""

from __future__ import annotations

import os
import re
import time

#: The child's console script.
SCRIPT = "moxi-memory-ingest"
#: A session's `turn` within this many seconds of its last started one starts nothing.
DEBOUNCE = 60.0
#: The runtime folder holding one file per session whose `turn` started a child; its
#: time is when.
TURNS = "turns"
#: A harness or session id a marker file may be named after.
_SAFE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}")


def spawn(args: list[str]) -> int:
    """Start `moxi-memory-ingest` with `args`, detached from this process, its output
    discarded, and return its process id without waiting for it."""
    import moxi.paths
    import moxi.proc

    return moxi.proc.spawn([str(moxi.paths.script(SCRIPT)), *args], detached=True,
                           output=os.devnull, cwd=os.sep)


def _marker(harness: str, session_id: str):
    from moxi import paths

    from .settings import MODULE

    folder = paths.Layout(MODULE).runtime_dir(create=True) / TURNS
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder / f"{harness}.{session_id}"


def _safe(harness: str, session_id: str) -> bool:
    return (isinstance(harness, str) and isinstance(session_id, str)
            and bool(_SAFE.fullmatch(harness)) and bool(_SAFE.fullmatch(session_id)))


def start_turn(harness: str, session_id: str, *, now: float | None = None) -> int | None:
    """Start a read of one session after a turn, unless one was started for it less
    than `DEBOUNCE` seconds ago."""
    if not _safe(harness, session_id):
        return None
    from moxi import files

    marker = _marker(harness, session_id)
    now = time.time() if now is None else now
    try:
        age = now - marker.stat().st_mtime
    except OSError:
        age = None
    if age is not None and 0 <= age < DEBOUNCE:
        return None
    files.atomic_write(marker, b"")
    os.utime(marker, (now, now))
    return spawn(["--turn", harness, session_id])


def start_gone(harness: str, session_id: str) -> int | None:
    """Start a read of one session to its end, then a walk."""
    if not _safe(harness, session_id):
        return None
    try:
        _marker(harness, session_id).unlink(missing_ok=True)
    except OSError:
        pass
    return spawn(["--gone", harness, session_id])


def start_walk() -> int:
    """Start one catch-up walk."""
    return spawn(["--walk"])


def start_all() -> int:
    """Start one read of every source from its start."""
    return spawn(["--all"])


def start_rebuild(*, relabel: bool = False) -> int:
    """Start the rebuild; with `relabel`, one that also derives each session's label
    again where its folder still exists."""
    return spawn(["--relabel"] if relabel else ["--rebuild"])


def fire(event) -> None:
    """A turns `turn` or `gone` event: start the matching child. Anything else starts
    nothing."""
    kind = getattr(event, "event", None)
    harness = getattr(event, "harness", None)
    session_id = getattr(event, "session_id", None)
    if kind == "turn":
        start_turn(harness, session_id)
    elif kind == "gone":
        start_gone(harness, session_id)
    return None


__all__ = ["SCRIPT", "DEBOUNCE", "spawn", "start_turn", "start_gone", "start_walk",
           "start_all", "start_rebuild", "fire"]
