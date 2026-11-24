"""`moxi-memory-subconscious`: the detached child that owns one recall from its start to
its end.

    moxi-memory-subconscious --session SESSION --recall RECALL

The listener or the on-demand command wrote the recall's record and started this child
outside the session (`moxi_memory.recall_start`). The child runs on the settings the
record holds, never on a fresh read of memory's config:

1. It writes the recall's log line, state `open`, with its own process identity (pid
   and start time) and its start-up time, so a recall whose child dies from here on is
   still found.
2. It reads memory's config itself, as the first search reads it. When that read is
   refused, it starts no fork: it sets the record and the line `lost` with the reason
   `config` and the error, and exits.
3. It runs the first search and writes the fork's brief (`moxi_memory.recall_brief`),
   noting the hit count and whether the search went past the project on the line, and
   the brief on the record.

It exits 0 when the recall ran to an end, 2 for arguments or a record it can't use.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import recalls, records

#: The reason on a recall whose child's read of memory's config was refused.
CONFIG = "config"


class Recall:
    """One recall as its child holds it: the record's fixed parts and the log line it
    keeps writing."""

    def __init__(self, record: dict):
        self.session_id: str = record["session"]
        self.recall_id: str = record["recall"]
        self.record = record
        self.settings: dict = dict(record.get("settings") or {})
        self.line: dict = {
            "recall": self.recall_id, "harness": record.get("harness"),
            "state": recalls.OPEN, "start": record.get("start"),
            "head": recalls.head(record.get("text") or ""), "route": record.get("route"),
            "fork": None, "child": None, "ref": None, "kept": False,
            "prefetch_hits": None, "widened": False, "startup_s": None,
            "hurried": False, "past_limit": False, "rounds": [],
            "at": record.get("at")}

    def put(self, *, record: dict | None = None, **line: object) -> None:
        """Write the line with `line`'s fields, and the record with `record`'s, under
        the session's lock."""
        with recalls.session_lock(self.session_id):
            self.write(record=record, **line)

    def write(self, *, record: dict | None = None, **line: object) -> None:
        """As `put`, for a caller already holding the session's lock."""
        if record:
            self.record = records.update(self.session_id, self.recall_id,
                                         **record) or self.record
        self.line.update(line)
        self.line = recalls.put_line(self.session_id, self.line)


def open_line(recall: Recall, *, now: float | None = None) -> None:
    """Step 1: the line, `open`, with this process's identity."""
    import moxi.proc

    now = time.time() if now is None else now
    me = moxi.proc.identity(os.getpid())
    child = {"pid": me.pid, "started": me.started} if me is not None else None
    started_at = recall.record.get("at")
    startup_s = round(now - started_at, 3) if isinstance(started_at, (int, float)) else None
    recall.put(child=child, startup_s=startup_s)


def read_config() -> str | None:
    """Step 2: None when memory's config reads, else the error."""
    from moxi.config import ConfigError

    from . import settings

    try:
        settings.read()
    except ConfigError as exc:
        return str(exc)
    return None


def lose(recall: Recall, reason: str, error: str | None = None) -> None:
    """End the recall `lost` for `reason`, record and line together."""
    recall.put(record={"state": records.LOST, "reason": reason, "error": error},
               state=recalls.LOST, reason=reason, error=error)


def write_brief(recall: Recall, *, search=None, places=None) -> str:
    """Step 3: the first search and the brief, noted on the line and the record."""
    from . import recall_brief

    record = recall.record
    text = record.get("text") or ""
    hits = int(recall.settings["prefetch_hits"])
    found = recall_brief.prefetch(text, session_id=recall.session_id,
                                  cwd=record.get("cwd"), notes_dir=record.get("notes_dir"),
                                  scope=str(recall.settings["scope"]), hits=hits,
                                  search=search, places=places)
    text_brief = recall_brief.brief(start=str(record.get("start")), text=text,
                                    session_id=recall.session_id, found=found,
                                    settings=recall.settings)
    recall.put(record={"brief": text_brief}, prefetch_hits=found.count,
               widened=found.widened)
    return text_brief


def run(session_id: str, recall_id: str) -> int:
    """The child's whole run. See the module's docstring."""
    record = records.read(session_id, recall_id)
    if record is None:
        print(f"moxi-memory-subconscious: no record of recall {recall_id} in session "
              f"{session_id}", file=sys.stderr)
        return 2
    recall = Recall(record)
    open_line(recall)
    error = read_config()
    if error is not None:
        lose(recall, CONFIG, error)
        return 0
    write_brief(recall)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moxi-memory-subconscious",
                                     description="Run one recall of memory's subconscious.")
    parser.add_argument("--session", required=True, help="the main session's id")
    parser.add_argument("--recall", required=True, help="the recall's id")
    args = parser.parse_args(argv)
    if not (recalls.is_safe(args.session) and recalls.is_safe(args.recall)):
        print("moxi-memory-subconscious: a session or recall id memory can't use",
              file=sys.stderr)
        return 2
    return run(args.session, args.recall)


__all__ = ["CONFIG", "Recall", "open_line", "read_config", "lose", "write_brief", "run",
           "main"]
