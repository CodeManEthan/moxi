"""The subconscious's log: one line per recall, and one per stretch a session's
subconscious could not run, in memory's state directory.

Everything lives under `subconscious/` in memory's state directory:

- `log/<session>.jsonl`, the session's log, one JSON object per line, oldest first. A
  recall's line is written when its child starts and rewritten in place as the recall
  goes on; an `off` line says the session's subconscious could not run, and why.
- `locks/<session>.lock`, the session's subconscious lock. Every write of a session's
  log is made under it (`session_lock`), by whichever process makes it, so two writers
  never lose each other's line. A reader takes no lock: each write replaces the file
  whole (`moxi.files.atomic_write`), so a read sees one version or the next.

A recall's line holds (`LINE_FIELDS`): its id (`recall`), the session and its harness,
its state, the reason for a `lost` or `off` state and the error behind it, how it started (`start`: `prompt` or
`asked`), the first `HEAD_CHARS` characters of its prompt or topic (`head`), its route,
the fork's id, the child's process identity (`child`: pid and start time) and that of
the process that took the line over when its owner was gone (`taker`), the current
round's ref and whether turns still keeps that round's end for memory (`kept`), the
prefetch's hit count and whether it searched past the project, the child's start-up
time, whether the recall was hurried or ran past its limit, each round's text and when
it came back (`rounds`), and when the line was first and last written.

A line's state says where the recall's answer is (`LINE_STATES`): `open` while its child
runs a round and holds no answer for it yet, `back` once an answer came back, `lost`
when none did, `discarded` when a person ended it, and `off` for a session whose
subconscious could not run. A person may discard only a line in a state of
`DISCARDABLE`, an answer memory still holds to send; on Claude Code's subagent route the
harness brings every answer back itself, so no line of it is ever in one. A discarded
line names who discarded it and when (`discarded_by`, `discarded_at`).

Standard library at import; the core is imported at the first read or write.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

#: The name of the subconscious's directory under memory's state directory.
DIR_NAME = "subconscious"
#: The names of the log's directory in it, and the locks'.
LOG = "log"
LOCKS = "locks"

#: A line's states.
OPEN = "open"
SENDING = "sending"
BACK = "back"
LOST = "lost"
DISCARDED = "discarded"
OFF = "off"
LINE_STATES = (OPEN, SENDING, BACK, LOST, DISCARDED, OFF)
#: The states a person may discard a line in: an answer memory still holds to send.
DISCARDABLE: tuple[str, ...] = (SENDING,)
#: How a recall started: a prompt, or the agent's own request.
PROMPT = "prompt"
ASKED = "asked"
#: How many characters of a prompt or topic a line keeps as its head.
HEAD_CHARS = 80
#: The fields of a recall's line.
LINE_FIELDS = ("recall", "session", "harness", "state", "reason", "error", "start", "head",
               "route", "fork", "child", "taker", "ref", "kept", "prefetch_hits", "widened",
               "startup_s", "hurried", "past_limit", "past_bound", "rounds", "sending_at",
               "send_tries", "send_reason", "sent_at", "message_id", "discarded_by",
               "discarded_at", "at", "updated")
#: A session id the log may name a file after.
_SAFE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}")

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def is_safe(session_id: object) -> bool:
    """Whether `session_id` is an id the log can name a file after."""
    return isinstance(session_id, str) and bool(_SAFE.fullmatch(session_id))


def _checked(session_id: str) -> str:
    if not is_safe(session_id):
        raise ValueError(f"{session_id!r} is not a session id memory keeps a log for")
    return session_id


def subconscious_dir(env: Mapping[str, str] | None = None) -> Path:
    """The subconscious's directory in memory's state directory."""
    from .settings import state_dir

    return state_dir(env) / DIR_NAME


def log_path(session_id: str, env: Mapping[str, str] | None = None) -> Path:
    """The session's log file."""
    return subconscious_dir(env) / LOG / f"{_checked(session_id)}.jsonl"


@contextmanager
def session_lock(session_id: str, *, timeout_s: float | None = None) -> Iterator[None]:
    """Hold the session's subconscious lock for the block. Threads of this process queue
    for it first, so a listener thread and another in the same endpoint take turns.
    `timeout_s` bounds the whole wait, for this process's threads and for another
    process together (`moxi.files.LockTimeout`)."""
    from moxi import files

    path = subconscious_dir() / LOCKS / f"{_checked(session_id)}.lock"
    files.private_dir(path.parent)
    with _thread_locks_guard:
        gate = _thread_locks.setdefault(str(path), threading.Lock())
    deadline = None if timeout_s is None else time.monotonic() + max(timeout_s, 0.0)
    if not gate.acquire(timeout=-1 if timeout_s is None else max(timeout_s, 0.0)):
        raise files.LockTimeout(path, os.getpid())  # another thread of this process
    try:
        left = None if deadline is None else max(deadline - time.monotonic(), 0.0)
        with files.lock(path, timeout_s=left):
            yield
    finally:
        gate.release()


def read_lines(session_id: str) -> list[dict]:
    """The session's log, oldest first. A line that does not read as a JSON object is
    skipped."""
    try:
        text = log_path(session_id).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return []
    out = []
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict):
            out.append(line)
    return out


def logged_sessions() -> list[str]:
    """Every session that has a log."""
    try:
        names = sorted(p.stem for p in (subconscious_dir() / LOG).glob("*.jsonl"))
    except OSError:
        return []
    return [n for n in names if is_safe(n)]


def all_lines(session_id: str | None = None) -> list[dict]:
    """Every line of the log, or of one session's, oldest first by when it was written."""
    sessions = [session_id] if session_id is not None else logged_sessions()
    lines = [line for s in sessions if is_safe(s) for line in read_lines(s)]
    return sorted(lines, key=lambda line: line.get("at") or 0)


def fork_ids() -> frozenset[tuple[str, str]]:
    """The `(harness, fork id)` of every recall fork the log names: the agent ids of
    the subagent forks, whose transcripts memory's index leaves out. The log is never
    swept, and this read is why: a sweep that dropped a fork's id would let its
    transcript back into the index at the next full read, so a sweep must keep them."""
    return frozenset((str(line.get("harness") or ""), str(line["fork"]))
                     for line in all_lines() if line.get("fork"))


def _write(session_id: str, lines: list[dict]) -> None:
    from moxi import files

    path = log_path(session_id)
    files.private_dir(path.parent)
    text = "".join(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n"
                   for line in lines)
    files.atomic_write(path, text)


def find_line(session_id: str, recall_id: str) -> dict | None:
    """The line of recall `recall_id` in the session's log, or None."""
    for line in read_lines(session_id):
        if line.get("recall") == recall_id:
            return line
    return None


def put_line(session_id: str, line: Mapping[str, object], *,
             now: float | None = None) -> dict:
    """Write recall line `line` to the session's log, in place of the line with the same
    `recall`, or after the others when it is new, and return it as written. The caller
    holds the session's lock."""
    now = time.time() if now is None else now
    written = dict(line)
    written["session"] = session_id
    written.setdefault("at", now)
    written["updated"] = now
    if written.get("state") not in LINE_STATES:
        raise ValueError(f"a line's state is one of {', '.join(LINE_STATES)}, not "
                         f"{written.get('state')!r}")
    lines = read_lines(session_id)
    recall_id = written.get("recall")
    for i, old in enumerate(lines):
        if recall_id is not None and old.get("recall") == recall_id:
            lines[i] = written
            break
    else:
        lines.append(written)
    _write(session_id, lines)
    return written


def note_off(session_id: str, reason: str, *, harness: str | None = None,
             now: float | None = None) -> bool:
    """Log that the session's subconscious can't run, for `reason`, unless the session's
    newest line already says so for the same reason. Takes the session's lock. True when
    it wrote a line."""
    with session_lock(session_id):
        lines = read_lines(session_id)
        newest = lines[-1] if lines else None
        if newest is not None and newest.get("state") == OFF and newest.get("reason") == reason:
            return False
        put_line(session_id, {"recall": None, "state": OFF, "reason": reason,
                              "harness": harness}, now=now)
        return True


def head(text: str) -> str:
    """The first `HEAD_CHARS` characters of a prompt or topic, on one line."""
    return " ".join(str(text).split())[:HEAD_CHARS]


__all__ = ["DIR_NAME", "LOG", "LOCKS", "OPEN", "SENDING", "BACK", "LOST", "DISCARDED", "OFF",
           "LINE_STATES", "DISCARDABLE", "PROMPT", "ASKED", "HEAD_CHARS", "LINE_FIELDS", "is_safe",
           "subconscious_dir", "log_path", "session_lock", "read_lines", "logged_sessions",
           "all_lines", "fork_ids", "find_line", "put_line", "note_off", "head"]
