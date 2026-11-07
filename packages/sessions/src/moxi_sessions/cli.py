"""`moxi sessions`: where a session's records are, which root each harness is read from,
and one session's turns.

    moxi sessions find SESSION [--harness claude|codex|opencode] [--root PATH] [--json]
    moxi sessions root [--json]
    moxi sessions read SESSION [--harness H] [--around TIME] [--turns N] [--all] [--json]

`find` prints where the session's records are (a Claude Code transcript, every rollout
file of a Codex thread, or the OpenCode database that holds the session), or every root it
probed and why each held none, and exits 1 then. `root` prints the root each harness is
read from when a caller names none, and where that root came from. With `--json`, each
prints one JSON object whose first key is `shape`, the shape version, as every JSON
document sessions writes has.

`read` prints part of one session the way sessions' page shows it (`moxi_sessions.view`):
the last `--turns` turns (5 unless asked, at most 50), or with `--around TIME` the turns
around that time, or with `--all` the whole session. A tool call prints as its one-line
summary and a tool result as its size and whether it failed, with every flag: no flag
prints a tool's input past its summary, or its output. With the secrets module each
message, tool call line and title is redacted with secrets' `credentials` and `broad` sets
before it is cut; without it the read prints as written and says so once, on stderr.
`--json` prints the same rows as one JSON object. A session no root holds, or a source
that can't be read, exits 1 with the reason.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from datetime import datetime

from moxi.config import ConfigError

from . import claude, view
from .errors import Unreadable
from .find import find
from .shape import CLAUDE, CODEX, HARNESSES, document
from .source import root, root_origin

#: The turns `read` shows unless asked, and the most it shows without `--all`.
TURNS_DEFAULT = 5
TURNS_MAX = 50

#: What `find` calls a harness's record when it finds none.
RECORD = {CLAUDE: "transcript", CODEX: "rollout", "opencode": "session row"}


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi sessions") -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="Find a harness's own record of a session.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    finder = verbs.add_parser("find", help="print where a session's records are",
                              description="Print where a session's records are, or the "
                                          "roots probed and why none held them.")
    finder.add_argument("session", metavar="SESSION", help="the session or thread id")
    finder.add_argument("--harness", choices=HARNESSES, default=CLAUDE,
                        help="whose record to look for (default: claude)")
    finder.add_argument("--root", action="append", default=[], metavar="PATH",
                        help="a further root to probe after the usual one (for OpenCode, "
                             "a database); may repeat")
    finder.add_argument("--json", action="store_true", help="print one JSON object")

    roots = verbs.add_parser("root", help="print each harness's root and its source",
                             description="Print the root each harness is read from when "
                                         "a caller names none, and where it came from.")
    roots.add_argument("--json", action="store_true", help="print one JSON object")

    reader = verbs.add_parser(
        "read", help="print part of one session",
        description="Print a session's last turns, the turns around a time, or the whole "
                    "session, the way sessions' page shows them: a tool call as its "
                    "summary line and a tool result as its size, never a tool's input or "
                    "output. Redacted with secrets' credentials and broad sets when the "
                    "secrets module is installed.")
    reader.add_argument("session", metavar="SESSION", help="the session or thread id")
    reader.add_argument("--harness", choices=HARNESSES, default=CLAUDE,
                        help="whose session it is (default: claude)")
    which = reader.add_mutually_exclusive_group()
    which.add_argument("--around", metavar="TIME",
                       help="the turns around TIME: ISO 8601 (2026-11-06T14:03:22Z; no "
                            "zone is local time) or epoch seconds")
    which.add_argument("--all", action="store_true", help="the whole session")
    reader.add_argument("--turns", type=int, metavar="N",
                        help=f"how many turns (default {TURNS_DEFAULT}, at most {TURNS_MAX})")
    reader.add_argument("--json", action="store_true", help="print one JSON object")

    args = parser.parse_args(argv)
    try:
        if args.verb == "find":
            return _find(args, prog)
        if args.verb == "read":
            return _read(args, prog)
        return _root(args)
    except ConfigError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 2


# --- find -----------------------------------------------------------------------------

def _find(args, prog: str) -> int:
    session = args.session
    if not claude.SAFE_ID_RE.match(session):
        print(f"{prog} find: {session!r} is not a session id", file=sys.stderr)
        return 2
    roots = claude.search_roots(live=root(args.harness), extra=args.root)
    found = find(args.harness, session, root=roots)
    paths = [hit.source.path for hit in found.hits]
    if args.json:
        probes = [{"root": probe.root, "outcome": probe.outcome,
                   "paths": [hit.source.path for hit in probe.hits]}
                  for probe in found.probes]
        print(json.dumps(document(harness=args.harness, session=session,
                                  found=found.found, paths=paths,
                                  compressed=[hit.source.path for hit in found.hits
                                              if hit.compressed],
                                  probes=probes), indent=2))
    elif paths:
        for path in paths:
            print(path)
    else:
        print(f"no {RECORD[args.harness]} for {session}; probed:")
        for probe in found.probes:
            print(f"  {probe.root}: {probe.outcome}")
    return 0 if found.found else 1


# --- root -----------------------------------------------------------------------------

def _root(args) -> int:
    origins = [root_origin(harness) for harness in HARNESSES]
    if args.json:
        print(json.dumps(document(**{origin.harness: {"root": str(origin.path),
                                                      "source": origin.source,
                                                      "detail": origin.detail}
                                     for origin in origins}), indent=2))
        return 0
    for origin in origins:
        print(f"{origin.harness}: {origin.path}")
        print(f"  from: {origin.detail}")
    return 0


# --- read -----------------------------------------------------------------------------

def parse_time(text: str) -> float:
    """`--around`'s TIME as epoch seconds: ISO 8601, a time with no zone read as local
    time, or a number of epoch seconds. Raises ValueError for anything else, and for a
    time past the end of year 9999, which the read could not print back as ISO 8601."""
    value = text.strip()
    try:
        seconds = float(value) if value.replace(".", "", 1).isdigit() else _iso(value)
        if time.gmtime(seconds).tm_year > 9999:
            raise ValueError(value)
        return seconds
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"{text!r} is not a time: give ISO 8601 "
                         "(2026-11-06T14:03:22Z) or epoch seconds") from None


def _iso(value: str) -> float:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.timestamp()


def _source(harness: str, session: str, prog: str):
    """The session's source, or the reason there is none."""
    found = find(harness, session)
    hits = found.hits
    if not hits:
        said = "; ".join(f"{probe.root}: {probe.outcome}" for probe in found.probes)
        return None, f"{prog} read: no {harness} session {session} was found ({said})"
    hit = next((h for h in hits if not h.compressed), hits[0])
    return hit.source, None


def _shown_line(shown: view.Shown, count: int) -> str:
    if shown.mode == "all":
        return f"the whole session: {count} records"
    turns = f"{shown.turns} turn{'s' if shown.turns != 1 else ''}"
    if shown.mode == "around":
        where = f"up to {turns} around {view.stamp(shown.around)}"
    else:
        where = f"the last {turns}"
    more = " and ".join(word for word, flag in (("earlier", shown.earlier),
                                                 ("later", shown.later)) if flag)
    if more:
        return (f"{where}; {more} turns are not shown (--turns for more, --all for the "
                "whole session)")
    if shown.mode == "around" or shown.ended == "start":
        return f"{where}; that is the whole session"
    return where


def _read(args, prog: str) -> int:
    session = args.session
    if not claude.SAFE_ID_RE.match(session):
        print(f"{prog} read: {session!r} is not a session id", file=sys.stderr)
        return 2
    turns = TURNS_DEFAULT if args.turns is None else args.turns
    if args.all and args.turns is not None:
        print(f"{prog} read: --turns and --all can't be given together: --all is the "
              "whole session", file=sys.stderr)
        return 2
    if not 1 <= turns <= TURNS_MAX:
        print(f"{prog} read: --turns is 1 to {TURNS_MAX}; --all reads the whole session",
              file=sys.stderr)
        return 2
    at = None
    if args.around is not None:
        try:
            at = parse_time(args.around)
        except ValueError as exc:
            print(f"{prog} read: {exc}", file=sys.stderr)
            return 2
    source, why = _source(args.harness, session, prog)
    if source is None:
        print(why, file=sys.stderr)
        return 1
    redactor = view.redactor()
    if redactor.redact is None:
        print(f"{prog} read: not redacted: {redactor.reason}; install the secrets module "
              "to redact what this prints", file=sys.stderr)
    try:
        if args.all:
            shown = view.whole(source, redactor.redact)
        elif at is not None:
            shown = view.around(source, at, turns, redactor.redact)
        else:
            shown = view.last(source, turns, redactor.redact)
        if args.json:
            _read_json(args, session, source, shown, redactor)
        else:
            _read_text(args, session, source, shown, redactor)
    except Unreadable as exc:
        print(f"{prog} read: {exc.reason}", file=sys.stderr)
        return 1
    return 0


def _read_json(args, session: str, source, shown: view.Shown, redactor) -> None:
    rows = list(shown.rows)
    header = view.header_row(shown.header_of(), rows, redact=redactor.redact)
    print(json.dumps(document(
        harness=args.harness, session=session, path=str(source.path), header=header,
        redacted=redactor.redact is not None, mode=shown.mode, turns=shown.turns,
        around=shown.around, earlier=shown.earlier, later=shown.later, ended=shown.ended,
        records=rows), indent=2))


def _read_text(args, session: str, source, shown: view.Shown, redactor) -> None:
    out = sys.stdout
    if shown.mode == "all":
        header = view.header_row(shown.header_of(), [], redact=redactor.redact)
        _header_lines(out, args.harness, session, source, header)
        count = 0
        for row in shown.rows:
            count += 1
            out.write("\n".join(view.lines(row)) + "\n")
        out.write(f"\n{_shown_line(shown, count)}\n")
        return
    rows = list(shown.rows)
    header = view.header_row(shown.header_of(), rows, redact=redactor.redact)
    _header_lines(out, args.harness, session, source, header)
    out.write(f"showing: {_shown_line(shown, len(rows))}\n")
    if shown.ended == "limit":
        out.write("The read stopped at its size limit, so the oldest turn shown may be "
                  "cut short.\n")
    out.write("\n")
    if not rows:
        out.write("No records in these turns.\n")
    for row in rows:
        out.write("\n".join(view.lines(row)) + "\n")


def _header_lines(out, harness: str, session: str, source, header: dict) -> None:
    title = header.get("title")
    out.write(f"{view.HARNESS_LABELS.get(harness, harness)} session {session}"
              + (f": {title}" if isinstance(title, str) and title else "") + "\n")
    if isinstance(header.get("cwd"), str):
        out.write(f"directory: {header['cwd']}\n")
    if isinstance(header.get("parent"), str):
        out.write(f"parent session: {header['parent']}\n")
    out.write(f"read from: {source.path}\n")

