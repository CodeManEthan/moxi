"""The stale-subagent judgment: which of Claude Code's `waiting` subagents is stale.

Claude Code lists a background subagent `waiting` while background work it owns is
running, even after that work has written nothing for hours. The Claude endpoint judges
each such entry here, and every reader of its agent list reads the judged entries. A
subagent entry is stale only when all four hold:

1. The last line of its own transcript that carries a timestamp is at least
   `STALE_AFTER_S` old, read off that line, never the file's mtime.
2. Claude Code lists it `waiting`. A `pending` or `running` entry is live whatever its
   transcript says.
3. Its last `user` or `assistant` record is an assistant record whose `stop_reason` is
   `end_turn`. Records of every other type are skipped.
4. Nothing it owns has been written within `STALE_AFTER_S`: the transcripts of the agents
   it started (the entries whose `parent_id` chain leads to it) and the output file of
   each background task its records name ("Output is being written to: PATH", or a task
   notice's `<output-file>`).

A stale entry reads `idle`, with `final` False. Each condition can only take an entry out
of the stale set, so a doubt keeps Claude Code's status: a transcript that is missing or
cannot be read, a record whose shape the reader does not know, and an entry the judgment
had no time for. Teammates and forks of the main loop are never judged.

Where the files are: under the session's record (Claude Code's own `transcript_path`)
without its `.jsonl`, in `subagents/`: `agent-<id>.jsonl`, or, for an agent a workflow
started, `workflows/<run>/agent-<id>.jsonl`. The meta file's `finished` flag is never
read: Claude Code writes it when the subagent ends its turn, while it still lists the
entry `waiting`.

What it costs: one deadline, `JUDGE_BUDGET_S` from the first `stat`, bounds the judgment
of the whole list. Every `waiting` entry first gets one `stat` of its transcript; a file
written inside `STALE_AFTER_S` is live and nothing more is read. Only then, while time is
left, does each silent entry get its tail read and its owned files looked at. The search
for its output files reads the whole transcript, a piece of `TAIL_BYTES` at a time, and
an entry the deadline passes in that read keeps Claude Code's status.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .results import Agent, Agents

#: How long a subagent's transcript and everything it owns must be silent before its
#: `waiting` entry can be stale, in seconds.
STALE_AFTER_S = 3600.0
#: The deadline over the judgment of one list, in seconds from its first `stat`.
JUDGE_BUDGET_S = 0.3
#: How much of a transcript's end the judgment reads for its last records, in bytes.
TAIL_BYTES = 256 * 1024

#: The two ways a transcript names a background task's output file, as JSON-escaped text.
_OUTPUTS = (re.compile(rb'Output is being written to: ([^"]{1,1024}?\.output)'),
            re.compile(rb'<output-file>([^"<]{1,1024})</output-file>'))
#: How much of the piece before each piece of a transcript the output search reads again,
#: in bytes: more than the longest name `_OUTPUTS` matches, with its words around it.
_OVERLAP_BYTES = 2048
#: The record types that are a turn's.
_TURN_TYPES = ("user", "assistant")


class _Unknown(Exception):
    """The judgment cannot tell: the entry keeps Claude Code's status."""


def subagents_dir(record_path: str) -> Path:
    """Where Claude Code writes the subagent transcripts of the session `record_path`
    records."""
    path = Path(record_path)
    return path.with_suffix("") / "subagents" if path.suffix == ".jsonl" \
        else path / "subagents"


def transcript_of(sub_dir: Path, agent_id: str) -> Path | None:
    """The transcript of subagent `agent_id` under `sub_dir`: `agent-<id>.jsonl`, else the
    first `workflows/<run>/agent-<id>.jsonl`; None when neither is there."""
    name = f"agent-{agent_id}.jsonl"
    direct = sub_dir / name
    if direct.is_file():
        return direct
    try:
        runs = sorted((sub_dir / "workflows").iterdir())
    except OSError:
        return None
    for run in runs:
        found = run / name
        if found.is_file():
            return found
    return None


def judge(listed: Agents, record_path: str | None, *, now: float | None = None,
          clock: Callable[[], float] = time.monotonic) -> Agents:
    """`listed` with each stale `waiting` subagent read `idle`, and `last_write_at` set on
    each `waiting` subagent judged. `now` is the wall-clock time the ages are read
    against (Unix seconds); `clock` the monotonic clock the deadline runs on. With no
    record path, nothing is judged."""
    entries = list(listed.entries)
    waiting = [i for i, e in enumerate(entries)
               if e.kind == "subagent" and e.state == "waiting" and not e.is_fork]
    if record_path is None or not waiting:
        return listed
    now = time.time() if now is None else now
    deadline = clock() + JUDGE_BUDGET_S
    sub_dir = subagents_dir(record_path)
    silent: list[tuple[int, Path]] = []
    for i in waiting:
        if clock() >= deadline:
            break
        path = transcript_of(sub_dir, entries[i].id)
        if path is None:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime < STALE_AFTER_S:
            entries[i] = replace(entries[i], last_write_at=mtime)
        else:
            silent.append((i, path))
    children: dict[str, list[str]] = {}
    for e in entries:
        if e.parent_id is not None:
            children.setdefault(e.parent_id, []).append(e.id)
    for i, path in silent:
        if clock() >= deadline:
            break
        owned = _descendants(entries[i].id, children)
        try:
            stale, newest = _silent(path, owned, sub_dir, now,
                                    lambda: clock() >= deadline)
        except _Unknown:
            continue
        entries[i] = replace(entries[i], state="idle" if stale else entries[i].state,
                             last_write_at=newest)
    return replace(listed, entries=tuple(entries))


def _descendants(agent_id: str, children: dict[str, list[str]]) -> list[str]:
    """Every agent whose `parent_id` chain leads to `agent_id`."""
    out: list[str] = []
    todo = list(children.get(agent_id, ()))
    while todo:
        child = todo.pop()
        if child in out or child == agent_id:
            continue
        out.append(child)
        todo.extend(children.get(child, ()))
    return out


def _silent(path: Path, owned: list[str], sub_dir: Path, now: float,
            late: Callable[[], bool]) -> tuple[bool, float]:
    """Judge one entry whose transcript's mtime is old: (stale, newest write read).
    Raises `_Unknown` when it cannot tell or `late()` turns true."""
    last_at, last_turn = _tail(path)
    newest = last_at
    if now - last_at < STALE_AFTER_S:
        return False, newest
    message = last_turn.get("message")
    if not isinstance(message, dict):
        raise _Unknown
    if last_turn.get("type") != "assistant" or message.get("stop_reason") != "end_turn":
        return False, newest
    for agent_id in owned:
        if late():
            raise _Unknown
        found = transcript_of(sub_dir, agent_id)
        written = None if found is None else _mtime(found)
        if written is None:
            raise _Unknown
        newest = max(newest, written)
    if late():
        raise _Unknown
    for output in _outputs(path, late):
        if late():
            raise _Unknown
        written = _mtime(output)
        if written is not None:         # a file gone has nothing written to it
            newest = max(newest, written)
    if late():
        raise _Unknown
    return now - newest >= STALE_AFTER_S, newest


def _mtime(path: Path) -> float | None:
    """The file's mtime, or None when there is no such file."""
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None
    except OSError:
        raise _Unknown from None


def _tail(path: Path) -> tuple[float, dict[str, Any]]:
    """The time on the transcript's last timestamped line, and its last `user` or
    `assistant` record, both from its last `TAIL_BYTES`."""
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, 2)
            start = max(0, size - TAIL_BYTES)
            fh.seek(start)
            tail = fh.read()
    except OSError:
        raise _Unknown from None
    lines = tail.split(b"\n")
    if start > 0:
        lines = lines[1:]               # the first is cut
    last_at: float | None = None
    last_turn: dict[str, Any] | None = None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            raise _Unknown from None
        if not isinstance(record, dict):
            raise _Unknown
        if last_at is None and "timestamp" in record:
            last_at = _time(record["timestamp"])
        if last_turn is None and record.get("type") in _TURN_TYPES:
            last_turn = record
        if last_at is not None and last_turn is not None:
            return last_at, last_turn
    raise _Unknown


def _time(value: Any) -> float:
    """A record's ISO 8601 `timestamp` as Unix seconds; one with no zone is unknown."""
    if not isinstance(value, str):
        raise _Unknown
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _Unknown from None
    if at.tzinfo is None:
        raise _Unknown
    return at.timestamp()


def _outputs(path: Path, late: Callable[[], bool]) -> list[Path]:
    """The background-task output files the transcript names, read whole (a task's line
    can sit anywhere in it) in pieces of `TAIL_BYTES`, each overlapping the last by
    `_OVERLAP_BYTES` so no name is cut. Raises `_Unknown` when `late()` turns true before
    a piece after the first, so the read keeps inside the judgment's deadline; the caller
    asks before the first."""
    found: list[Path] = []
    try:
        with path.open("rb") as fh:
            kept = b""
            while True:
                piece = fh.read(TAIL_BYTES)
                data = kept + piece
                _named_outputs(data, found)
                if len(piece) < TAIL_BYTES:
                    return found
                kept = data[-_OVERLAP_BYTES:]
                if late():
                    raise _Unknown
    except OSError:
        raise _Unknown from None


def _named_outputs(data: bytes, found: list[Path]) -> None:
    """Add to `found` each output file `data` names that it doesn't hold yet."""
    for pattern in _OUTPUTS:
        for raw in pattern.findall(data):
            try:
                text = json.loads(b'"' + raw + b'"')
            except ValueError:
                continue
            output = Path(text.strip())
            if output not in found:
                found.append(output)


__all__ = ["JUDGE_BUDGET_S", "STALE_AFTER_S", "TAIL_BYTES", "judge", "subagents_dir",
           "transcript_of"]
