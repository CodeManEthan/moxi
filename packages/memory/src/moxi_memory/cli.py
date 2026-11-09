"""`moxi memory`: search past sessions, start an ingest or a rebuild, and see the index.

    moxi memory search "WORDS" [--project P] [--since TIME] [--harness H] [--results]
                               [--subagents] [--scratch] [--limit N] [--json]
    moxi memory ingest [--all]
    moxi memory reindex [--relabel]
    moxi memory status [--json]

`search` prints each hit (harness, session id, time, project, row kind, then a snippet),
and under them one `moxi sessions read` command that opens the first hit's session
around its time. A search that ran exits 0, also with no hit ("no match in N rows"), and
adds one line for each state it can see that leaves its answer incomplete: the first
full read still filling, or a rebuild running. A missing or unreadable index exits 1
with what to run; so does a state store that can't be read, naming the store.

`ingest` and `reindex` only start the detached ingest child and exit 0 at once, with the
`moxi memory status` line to watch it by; no rebuild ever runs inside this command.
`ingest` asks for a catch-up walk, `--all` for a read of every source from its start.
What either asks for is recorded, so another ingest holding memory's writer lock delays
it but doesn't lose it. Neither command touches that lock, so neither says the work
started: each says it is queued behind a running rebuild, or that it runs now or once the
ingest holding the lock ends. `reindex` builds a new index from every transcript the
harnesses still keep, and `--relabel` derives each session's project label again where
its folder still exists.

`status` prints the index's rows, reads and backlog, whether the first full read has
finished, whether a rebuild is running, a request waiting, the session table's counts
and what the last rebuild dropped. It exits 1 when there is no index, or one that can't
be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Sequence
from datetime import datetime

from moxi import exit as codes

from . import search as searching
from .clock import show
from .settings import HARNESSES

#: The command to watch an ingest or a rebuild by.
WATCH = "moxi memory status"
#: The command that reads a session: sessions' own.
READ = "moxi sessions read"
#: The harness whose hits get a read line with no `--harness`.
READ_DEFAULT_HARNESS = "claude"

_RELATIVE = re.compile(r"(\d+(?:\.\d+)?)\s*([smhdw])")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def parse_since(text: str, *, now: float | None = None) -> float:
    """`--since`'s TIME as seconds since the epoch: an age (`30m`, `12h`, `7d`, `2w`),
    ISO 8601 (no zone is local time), or seconds since the epoch. A bare whole number of
    four digits or fewer is refused: it reads like a year, which as epoch seconds would
    be a moment in 1970 and keep every row."""
    value = text.strip()
    now = time.time() if now is None else now
    if m := _RELATIVE.fullmatch(value):
        return now - float(m.group(1)) * _UNITS[m.group(2)]
    if value.isdigit() and len(value) <= 4:
        raise ValueError(f"{text!r} is not a time: for a year give ISO 8601 "
                         f"({value}-01-01); epoch seconds are ten digits today")
    try:
        if value.replace(".", "", 1).isdigit():
            return float(value)
        moment = datetime.fromisoformat(value)
        return (moment if moment.tzinfo else moment.astimezone()).timestamp()
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"{text!r} is not a time: give an age (7d, 12h), ISO 8601 "
                         "(2026-11-06 or 2026-11-06T14:03:22Z) or epoch seconds") from None


def read_line(hit) -> str:
    """The command that opens `hit`'s session around its time. For a hit that isn't
    Claude Code's it names `--harness`, so the read needn't look the session up in
    every harness."""
    parts = [READ, hit.session_id]
    if hit.harness != READ_DEFAULT_HARNESS:
        parts += ["--harness", hit.harness]
    if hit.time is not None:
        parts += ["--around", searching.stamp(hit.time)]
    return " ".join(parts)


def _kind(hit) -> str:
    kind = hit.kind
    if hit.failed:
        kind += ", failed"
    if hit.agent_id:
        kind += f", subagent {hit.agent_id}"
    return kind


def incomplete(result) -> list[str]:
    """One line for each state the search saw that leaves its answer incomplete."""
    lines = []
    if result.filling is not None:
        fill = result.filling
        if fill.listed is None:
            lines.append(f"index still filling: {fill.read} sources read, the rest not "
                         "listed yet")
        else:
            lines.append(f"index still filling: {fill.read} of {fill.listed} sources read")
    if result.rebuilding is not None:
        since = (show(result.rebuilding) if result.rebuilding
                 else "a moment ago")
        lines.append(f"rebuild running since {since}; sessions since then are not in "
                     "these results")
    return lines


def _missing_message(exc, rebuilding: float | None) -> str:
    if isinstance(exc, searching.StateUnreadable):
        return (f"memory's state store can't be read ({exc}). A rebuild doesn't repair "
                "it. Moved aside, it is made again empty at the next ingest, without "
                "the session table's identities and launch kinds.")
    what = ("no index to search" if isinstance(exc, searching.IndexMissing)
            else f"the index can't be read ({exc})")
    if rebuilding is not None:
        since = show(rebuilding) if rebuilding else "a moment ago"
        return (f"{what}; a rebuild is running since {since}. "
                f"See `{WATCH}` for how far it is.")
    return (f"{what}. See `{WATCH}`. `moxi memory reindex` starts a detached rebuild; "
            "this search can't wait for it.")


# -- the verbs -------------------------------------------------------------------------

def _search(args, out, prog: str) -> int:
    try:
        since = parse_since(args.since) if args.since is not None else None
    except ValueError as exc:
        print(f"{prog} search: {exc}", file=sys.stderr)
        return codes.USAGE
    try:
        result = searching.search(args.words, project=args.project, since=since,
                                  harness=args.harness, results=args.results,
                                  subagents=args.subagents, scratch=args.scratch,
                                  limit=args.limit)
    except (searching.IndexMissing, searching.IndexUnreadable) as exc:
        message = _missing_message(exc, searching.rebuilding())
        state = "missing" if isinstance(exc, searching.IndexMissing) else "unreadable"
        if args.json:
            print(json.dumps({"error": state, "message": message}), file=out)
        print(f"{prog} search: {message}", file=sys.stderr)
        return codes.ERROR
    notes = incomplete(result)
    first = read_line(result.hits[0]) if result.hits else None
    if args.json:
        print(json.dumps({
            "tokens": list(result.tokens), "rows": result.rows,
            "hits": [{"harness": h.harness, "session_id": h.session_id,
                      "agent_id": h.agent_id, "time": h.time,
                      "at": None if h.time is None else searching.stamp(h.time),
                      "project": h.project, "kind": h.kind, "tier": h.tier,
                      "failed": h.failed, "snippet": h.snippet, "score": h.score,
                      "read": read_line(h)} for h in result.hits],
            "filling": (None if result.filling is None else
                        {"read": result.filling.read, "listed": result.filling.listed}),
            "rebuilding": result.rebuilding, "read": first, "notes": notes,
        }), file=out)
        return codes.OK
    if not result.hits:
        print(f"no match in {result.rows} rows", file=out)
    for n, hit in enumerate(result.hits, 1):
        print(f"{n}. {hit.harness}  {hit.session_id}  {show(hit.time)}  "
              f"{hit.project or '-'}  {_kind(hit)}", file=out)
        print(f"   {hit.snippet}", file=out)
    if first is not None:
        print(f"\n{first}", file=out)
    for line in notes:
        print(line, file=out)
    return codes.OK


def _asked(what: str, go, out) -> None:
    """Start the child that asks for `what` (`go()`, which returns its process id), and
    say what can be known without touching the writer lock: a running rebuild it is
    queued behind, or else a line true whether the child runs it now or waits for the
    ingest holding the lock. The rebuild is looked for before the child starts, so a
    rebuild the child itself starts is never taken for one it waits behind."""
    running = searching.rebuilding()
    pid = go()
    print(f"asked for {what} in the background (process {pid})", file=out)
    if running is not None:
        at = f"at {show(running)}" if running else "a moment ago"
        print(f"it is queued behind the rebuild that started {at}, and runs when that "
              "rebuild ends", file=out)
    else:
        print("it runs now, or, if an ingest holds memory's writer lock, as soon as that "
              f"ingest ends; `{WATCH}` shows which", file=out)
    print(f"watch it with: {WATCH}", file=out)


def _ingest(args, out, prog: str) -> int:
    from . import start

    if args.all:
        _asked("a read of every source from its start", start.start_all, out)
    else:
        _asked("a catch-up walk", lambda: start.start_walk(asked=True), out)
    return codes.OK


def _reindex(args, out, prog: str) -> int:
    from . import start

    print("A rebuild drops every session whose transcript the harness has deleted since "
          "memory read it; until the rebuild ends, those sessions stay searchable.",
          file=out)
    if args.relabel:
        print("--relabel derives each session's project label again where its folder "
              "still exists; a session whose folder is gone keeps its label.", file=out)
    _asked("a relabel" if args.relabel else "a rebuild",
           lambda: start.start_rebuild(relabel=args.relabel), out)
    return codes.OK


def _counts(by: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "none"


def _status(args, out, prog: str) -> int:
    from . import status as stat

    s = stat.status()
    if args.json:
        print(json.dumps(s.to_json()), file=out)
        return codes.OK if s.index == stat.OK else codes.ERROR
    if s.index == stat.MISSING:
        print(f"index: none ({s.reason})", file=out)
    elif s.index == stat.UNREADABLE:
        print(f"index: can't be read ({s.reason})", file=out)
    else:
        print(f"index: generation {s.generation}, tokenizer {s.tokenizer}", file=out)
        tiers = s.rows
        print(f"rows: tier 1 {_counts(tiers.get('1', {}))}; tier 2 (tool results) "
              f"{_counts(tiers.get('2', {}))}; subagents {_counts(tiers.get('subagents', {}))}",
              file=out)
        print(f"sources: {s.sources_read} read, {s.sources_not_read} not read "
              "(compressed Codex rollouts, which sessions can't read)", file=out)
        walks = ", ".join(f"{h} {show(t)}" for h, t in sorted(s.last_walk.items()))
        print(f"last ingest: {show(s.last_ingest)}; last walk: "
              f"{walks or 'none yet'}", file=out)
        if s.backlog is None:
            print("backlog: unknown (memory's config can't be read)", file=out)
        else:
            known = {h: n for h, n in s.backlog.items() if n is not None}
            unknown = sorted(h for h, n in s.backlog.items() if n is None)
            line = (f"backlog: {sum(known.values())} sources changed since the last walk"
                    f" ({_counts(known)})")
            if unknown:
                line += f"; root not listable: {', '.join(unknown)}"
            print(line, file=out)
        if s.first_read:
            print("first full read: finished", file=out)
        else:
            fill = s.filling
            of = f" of {fill.listed}" if fill and fill.listed is not None else ""
            print(f"first full read: still filling, {fill.read if fill else 0}{of} "
                  "sources read", file=out)
    if s.rebuilding is None:
        print("rebuild: none running", file=out)
    else:
        since = show(s.rebuilding) if s.rebuilding else "a moment ago"
        pid = f" (process {s.rebuild_pid})" if s.rebuild_pid else ""
        print(f"rebuild: running since {since}{pid}", file=out)
    if s.waiting is None:
        print("request waiting: none", file=out)
    else:
        print(f"request waiting: {s.waiting['kind']}, asked {show(s.waiting['asked'])}",
              file=out)
    if s.index == stat.OK:
        if s.rebuilt is None:
            print("last rebuild: none yet", file=out)
        else:
            print(f"last rebuild: started {show(s.rebuilt)}, dropped "
                  f"{s.dropped or 0} sessions whose transcript was gone", file=out)
    sessions = s.sessions
    if s.sessions_unreadable is not None:
        print(f"sessions: the state store can't be read ({s.sessions_unreadable})",
              file=out)
    elif sessions:
        print(f"sessions: {sessions['total']} (by launch kind: "
              f"{_counts(sessions['by_kind'])}); {sessions['with_identity']} with an "
              f"identity, {sessions['without_identity']} without", file=out)
    else:
        print("sessions: none recorded yet", file=out)
    if s.index != stat.OK and s.rebuilding is None:
        print("run `moxi memory reindex` to build the index; it runs in the background",
              file=out)
    return codes.OK if s.index == stat.OK else codes.ERROR


_VERBS = {"search": _search, "ingest": _ingest, "reindex": _reindex, "status": _status}


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog, description="Search past sessions of every harness, and keep the "
                               "index behind the search.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    p = verbs.add_parser(
        "search", help="search past sessions",
        description="Search past sessions for any of WORDS, best match first. Each word "
                    "is searched as written, stemmed (running finds runs); punctuation "
                    "only separates words.")
    p.add_argument("words", metavar="WORDS", help="what to search for")
    p.add_argument("--project", metavar="P", help="only sessions of project label P")
    p.add_argument("--since", metavar="TIME",
                   help="only rows at or after TIME: an age (7d, 12h), ISO 8601, or "
                        "epoch seconds")
    p.add_argument("--harness", choices=HARNESSES, help="only this harness's sessions")
    p.add_argument("--results", action="store_true",
                   help="search the ends of tool results too")
    p.add_argument("--subagents", action="store_true",
                   help="search subagent sessions too (indexed only when config "
                        "index.subagents is on)")
    p.add_argument("--scratch", action="store_true",
                   help="search sessions launched as scratch too")
    p.add_argument("--limit", type=_positive, default=10, metavar="N",
                   help="how many hits (default 10)")
    p.add_argument("--json", action="store_true", help="print one JSON object")

    p = verbs.add_parser("ingest", help="ask for new transcript lines to be read, and "
                                        "return",
                         description="Ask for a catch-up read of every harness root in the "
                                     "background, and return at once. Another ingest "
                                     "holding the writer lock delays it; it is not lost.")
    p.add_argument("--all", action="store_true",
                   help="read every source from its start instead")

    p = verbs.add_parser("reindex", help="ask for a rebuild of the index, and return",
                         description="Ask for a rebuild of the index from every transcript "
                                     "the harnesses still keep, in the background, and "
                                     "return at once. Another ingest holding the writer "
                                     "lock delays it; it is not lost.")
    p.add_argument("--relabel", action="store_true",
                   help="also derive each session's project label again from its folder")

    p = verbs.add_parser("status", help="print the index's state",
                         description="Print the index's rows and reads, the backlog, "
                                     "whether a rebuild is running, and the session "
                                     "table's counts.")
    p.add_argument("--json", action="store_true", help="print one JSON object")
    return parser


def _positive(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"{n} is below 1")
    return n


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi memory") -> int:
    from moxi.config import ConfigError

    try:
        args = _parser(prog).parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    try:
        return _VERBS[args.verb](args, sys.stdout, prog)
    except ConfigError as exc:
        print(f"{prog} {args.verb}: {exc}", file=sys.stderr)
        return codes.USAGE


__all__ = ["WATCH", "READ", "parse_since", "read_line", "incomplete", "main"]
