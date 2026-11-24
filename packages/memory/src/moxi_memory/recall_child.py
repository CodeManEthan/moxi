"""`moxi-memory-subconscious`: the detached child that owns one recall from its start to
its end, or a taker of a session's lines whose owner is gone.

    moxi-memory-subconscious --session SESSION --recall RECALL
    moxi-memory-subconscious --take --session SESSION

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
   leaving out of the search's query and the brief's prompt each line that equals one
   memory sent to the session, when the prompt came from a recall sender of the session
   among others (the record's `recall_senders`); the brief carries those lines as what
   the main agent already has,
   noting the hit count and whether the search went past the project on the line, and
   the brief on the record.
4. It runs the recall's stream through its route (`moxi_memory.recall_stream`): the
   brief as the first round, later rounds while the stream lasts, each round settled
   on the line and acked to turns, and the record ended.

From its start, beside these steps, a thread takes over every session's lines whose
owner is gone (`moxi_memory.recall_take.take_all`) and then sweeps the records a day
old (`moxi_memory.records.sweep_old`), and the child waits for it before it exits.

With `--take`, it does the same for the one session and exits: the taker memory's
`gone` listener starts when a session with the subconscious on ends.

With `--detach` first, it is the detached starter: it starts `moxi-memory-subconscious`
with the rest of its arguments, detached, and exits, so the process it started has the
system's for its parent and runs outside every session (comms takes a send under a
scoped sender only from such a process). The listener, the on-demand command and the
`gone` listener start every child and taker through it. A recall's child it can't start
ends the recall `lost` with the reason `spawn`.

It exits 0 when the recall ran to an end or the taking is done, 2 for arguments or a
record it can't use.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from . import recalls, records

#: The reason on a recall whose child's read of memory's config was refused.
CONFIG = "config"


class Recall:
    """One recall as its owner holds it: the record's fixed parts and the log line it
    keeps writing. A child starts a new line; a taker passes the line it took (`line`)."""

    def __init__(self, record: dict, *, line: dict | None = None):
        self.session_id: str = record["session"]
        self.recall_id: str = record["recall"]
        self.record = record
        self.settings: dict = dict(record.get("settings") or {})
        self.line: dict = {
            "recall": self.recall_id, "harness": record.get("harness"),
            "state": recalls.OPEN, "start": record.get("start"),
            "head": recalls.head(record.get("text") or ""), "route": record.get("route"),
            "fork": None, "child": None, "ref": None, "kept": False,
            "prefetch_hits": None, "project_hits": None, "beyond_hits": None,
            "startup_s": None,
            "hurried": False, "past_limit": False, "rounds": [],
            "at": record.get("at")}
        if line is not None:
            self.line = dict(line)

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
    recalled = None
    if record.get("recall_senders"):
        text, recalled = recall_brief.split_recalled(
            text, recalls.sent_lines(recall.session_id, skip=recall.recall_id))
    hits = int(recall.settings["prefetch_hits"])
    found = recall_brief.prefetch(text, session_id=recall.session_id,
                                  cwd=record.get("cwd"), notes_dir=record.get("notes_dir"),
                                  scope=str(recall.settings["scope"]), hits=hits,
                                  search=search, places=places)
    text_brief = recall_brief.brief(start=str(record.get("start")), text=text,
                                    session_id=recall.session_id, found=found,
                                    settings=recall.settings, recalled=recalled)
    counts = found.counts()
    recall.put(record={"brief": text_brief}, prefetch_hits=found.count,
               project_hits=counts[recall_brief.PROJECT_PART],
               beyond_hits=counts[recall_brief.BEYOND_PART])
    return text_brief


def run(session_id: str, recall_id: str, *, route=None) -> int:
    """The child's whole run. See the module's docstring."""
    record = records.read(session_id, recall_id)
    if record is None:
        print(f"moxi-memory-subconscious: no record of recall {recall_id} in session "
              f"{session_id}", file=sys.stderr)
        return 2
    recall = Recall(record)
    open_line(recall)
    taking = threading.Thread(target=take_others, args=(recall_id,),
                              name="memory-recall-take", daemon=True)
    taking.start()
    try:
        error = read_config()
        if error is not None:
            lose(recall, CONFIG, error)
            return 0
        text_brief = write_brief(recall)
        from . import recall_stream

        if route is None:
            from . import recall_route

            route = recall_route.for_recall(recall)
        try:
            recall_stream.run(recall, route, text_brief)
        finally:
            close = getattr(route, "close", None)
            if close is not None:
                close()
        return 0
    finally:
        taking.join()


def take_others(recall_id: str | None = None, *, sessions: list[str] | None = None) -> None:
    """Take over the lines of `sessions` (every session's when None) whose owner is gone,
    leaving out recall `recall_id`, then sweep their records a day old. Never raises."""
    from . import recall_take

    try:
        recall_take.take_all(sessions=sessions, skip=recall_id)
    except Exception:  # noqa: BLE001 - a taking that fails leaves the lines for the next
        pass
    try:
        records.sweep_old(sessions=sessions)
    except Exception:  # noqa: BLE001 - a sweep that fails leaves the records for the next
        pass


def detach(argv: list[str], *, spawn=None) -> int:
    """The detached starter: start `moxi-memory-subconscious ARGV`, detached, and
    return 0 at once; 2 when it can't be started, after ending a recall it was to run
    `lost` (`spawn`)."""
    import moxi.paths
    import moxi.proc

    from . import recall_start

    try:
        if spawn is None:
            moxi.proc.spawn([str(moxi.paths.script(recall_start.SCRIPT)), *argv],
                            detached=True, output=os.devnull, cwd=os.sep)
        else:
            spawn(argv)
    except Exception as exc:  # noqa: BLE001 - a child that can't start loses its recall
        args = _parser().parse_known_args(argv)[0]
        if args.recall and recalls.is_safe(args.session) and recalls.is_safe(args.recall):
            recall_start.lose_spawn(args.session, args.recall, f"{type(exc).__name__}: {exc}")
        return 2
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moxi-memory-subconscious",
                                     description="Run one recall of memory's subconscious.")
    parser.add_argument("--session", required=True, help="the main session's id")
    parser.add_argument("--recall", help="the recall's id")
    parser.add_argument("--take", action="store_true",
                        help="take over the session's lines whose owner is gone, and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    from .recall_start import DETACH

    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == [DETACH]:
        return detach(argv[1:])
    parser = _parser()
    args = parser.parse_args(argv)
    if args.take == bool(args.recall):
        print("moxi-memory-subconscious: give --recall, or --take", file=sys.stderr)
        return 2
    if not (recalls.is_safe(args.session)
            and (args.take or recalls.is_safe(args.recall))):
        print("moxi-memory-subconscious: a session or recall id memory can't use",
              file=sys.stderr)
        return 2
    if args.take:
        take_others(sessions=[args.session])
        return 0
    return run(args.session, args.recall)


__all__ = ["CONFIG", "Recall", "open_line", "read_config", "lose", "write_brief", "run",
           "take_others", "detach", "main"]
