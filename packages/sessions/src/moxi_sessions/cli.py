"""`moxi sessions`: where a session's records are, which root each harness is read from,
the newest sessions, and one session's turns.

    moxi sessions find SESSION [--harness claude|codex|opencode] [--root PATH] [--json]
    moxi sessions root [--json]
    moxi sessions list [--harness H] [--since TIME] [--folder PATH] [--limit N] [--text]
                       [--json]
    moxi sessions read SESSION [--harness H] [--around TIME] [--turns N] [--all] [--json]

`find` prints where the session's records are (a Claude Code transcript, every rollout
file of a Codex thread, or the OpenCode database that holds the session), or every root it
probed and why each held none, and exits 1 then. It asks the live root, then the archive
folders sessions' config names for the harness (`claude.archives`, `codex.archives`), then
each `--root`. Asking for a session this way is asking the archives: when the live root
doesn't hold it, the archive folders are tested together first, waiting at most
`archives.answer_wait_s`. When no folder holds the session and one didn't answer, the session is
unknown, not absent: it says so, names that folder, and exits 1. `root` prints the root
each harness is read from when a caller names none, where that root came from, and its
archive folders, testing none. With `--json`, each
prints one JSON object whose first key is `shape`, the shape version, as every JSON
document sessions writes has.

`list` prints the main sessions of the three harnesses, newest change first, 30 unless
`--limit` asks for another number: one line a session, with when it started, when it last
changed, its harness, the name typed for it (else its folder) and its id; `-` stands for
a fact that was not read. `--harness`, `--since TIME` (changed at or after it, in the
forms `read --around` takes) and `--folder PATH` (an absolute path the session's record
names) narrow it. It prints nothing a person or a model said unless `--text` is given;
then each session's line is followed by the newest thing said in it, on one line, cut at
300 bytes. Names, and with `--text` the messages, are redacted as `read` redacts when the
secrets module is installed; with `--text` and no secrets it says so once, on stderr.
`--json` prints the list as one JSON object: the counts, the roots that could not be
listed, and `rows`, whose `activity` and `prompt` carry their `text` only with `--text`.
A root that could not be listed is named on stderr, and the other roots are listed; so
is a root with folders or files under it that could not be listed, with how many. A
harness that is not on the machine (its root is the default one and does not exist) is
not a failure: nothing is printed for it.

`read` prints part of one session the way sessions' page shows it (`moxi_sessions.view`):
the last `--turns` turns (5 unless asked, at most 50), or with `--around TIME` the turns
around that time, or with `--all` the whole session. A tool call prints as its one-line
summary and a tool result as its size and whether it failed, with every flag: no flag
prints a tool's input past its summary, or its output. With the secrets module each
message, tool call line and title is redacted with secrets' `credentials` and `broad` sets
before it is cut; without it the read prints as written and says so once, on stderr.
`--json` prints the same rows as one JSON object. Without `--harness`, `read` asks each
harness's live root for the session, the way `find` does, and reads the one that holds it;
only when no live root holds it does it test every harness's archive folders together (one
wait) and ask them. With `--harness`, it asks that harness's live root, then its archive
folders. When more than one harness holds the session, it names them and exits 2 for
`--harness` to choose. When one harness holds it and another harness's archive folder
didn't answer, it reads the one it found and names the folder it couldn't ask. A session
no folder holds, or a source that can't be read, exits 1 with the reason; a session no
folder holds while one didn't answer is unknown, and the read says so, names the folder,
and exits 1.
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
from .archives import archive_answers, archive_dirs
from .errors import Unreadable
from .find import Found, find
from .shape import CLAUDE, CODEX, HARNESSES, document
from .source import root, root_origin

#: The turns `read` shows unless asked, and the most it shows without `--all`.
TURNS_DEFAULT = 5
TURNS_MAX = 50

#: The sessions `list` shows unless asked.
LIST_DEFAULT = 30

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

    lister = verbs.add_parser(
        "list", help="print the newest sessions",
        description="Print the main sessions of the three harnesses, newest change "
                    "first: when each started and last changed, its harness, its name "
                    "or folder and its id. No message text without --text.")
    lister.add_argument("--harness", choices=HARNESSES,
                        help="only this harness's sessions")
    lister.add_argument("--since", metavar="TIME",
                        help="only sessions changed at or after TIME: ISO 8601 "
                             "(2026-11-06T14:03:22Z; no zone is local time) or epoch "
                             "seconds")
    lister.add_argument("--folder", metavar="PATH",
                        help="only sessions whose record names this folder, an absolute "
                             "path")
    lister.add_argument("--limit", type=int, default=LIST_DEFAULT, metavar="N",
                        help=f"how many sessions (default {LIST_DEFAULT})")
    lister.add_argument("--text", action="store_true",
                        help="also print the newest thing said in each session")
    lister.add_argument("--json", action="store_true", help="print one JSON object")

    reader = verbs.add_parser(
        "read", help="print part of one session",
        description="Print a session's last turns, the turns around a time, or the whole "
                    "session, the way sessions' page shows them: a tool call as its "
                    "summary line and a tool result as its size, never a tool's input or "
                    "output. Redacted with secrets' credentials and broad sets when the "
                    "secrets module is installed.")
    reader.add_argument("session", metavar="SESSION", help="the session or thread id")
    reader.add_argument("--harness", choices=HARNESSES,
                        help="whose session it is (default: the harness whose root holds "
                             "it)")
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
        if args.verb == "list":
            return _list(args, prog)
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
    found = find(args.harness, session, include_archives=True)
    asked = {probe.root for probe in found.probes}
    extra = [each for each in claude.search_roots(live=root(args.harness),
                                                  extra=args.root)[1:]
             if str(each) not in asked]
    if not found.found and extra:
        more = find(args.harness, session, root=extra)
        found = Found(args.harness, session, found.probes + more.probes)
    paths = [hit.source.path for hit in found.hits]
    if args.json:
        probes = [{"root": probe.root, "outcome": probe.outcome,
                   "paths": [hit.source.path for hit in probe.hits]}
                  for probe in found.probes]
        print(json.dumps(document(harness=args.harness, session=session,
                                  found=found.found, unknown=found.unknown, paths=paths,
                                  compressed=[hit.source.path for hit in found.hits
                                              if hit.compressed],
                                  probes=probes), indent=2))
    elif paths:
        for path in paths:
            print(path)
    elif found.unknown:
        print(f"{session} is unknown: no folder asked holds its {RECORD[args.harness]}, "
              "and a folder that didn't answer may; probed:")
        for probe in found.probes:
            print(f"  {probe.root}: {probe.outcome}")
    else:
        print(f"no {RECORD[args.harness]} for {session}; probed:")
        for probe in found.probes:
            print(f"  {probe.root}: {probe.outcome}")
    return 0 if found.found else 1


# --- root -----------------------------------------------------------------------------

def _root(args) -> int:
    origins = [root_origin(harness) for harness in HARNESSES]
    kept = {origin.harness: list(archive_dirs(origin.harness)) for origin in origins}
    if args.json:
        print(json.dumps(document(**{origin.harness: {"root": str(origin.path),
                                                      "source": origin.source,
                                                      "detail": origin.detail,
                                                      "archives": kept[origin.harness]}
                                     for origin in origins}), indent=2))
        return 0
    for origin in origins:
        print(f"{origin.harness}: {origin.path}")
        print(f"  from: {origin.detail}")
        for archive_dir in kept[origin.harness]:
            print(f"  archive: {archive_dir}")
    return 0


# --- list -----------------------------------------------------------------------------

def _when(seconds) -> str:
    return view.stamp(seconds) if isinstance(seconds, (int, float)) else "-"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _list(args, prog: str) -> int:
    from . import lists, settings           # the list's modules, only for this verb

    since = None
    if args.since is not None:
        try:
            since = parse_time(args.since)
        except ValueError as exc:
            print(f"{prog} list: {exc}", file=sys.stderr)
            return 2
    try:
        answer = lists.newest_sessions(recent_s=settings.recent_s(), limit=args.limit,
                                       harness=args.harness, since=since,
                                       work_dir=args.folder)
    except ValueError as exc:
        print(f"{prog} list: {exc}", file=sys.stderr)
        return 2
    redactor = view.redactor()
    redact = redactor.redact
    if args.text and redact is None:
        print(f"{prog} list: not redacted: {redactor.reason}; install the secrets module "
              "to redact what this prints", file=sys.stderr)
    rows = [_list_row(row, redact, text=args.text) for row in answer.pop("rows")]
    if args.json:
        print(json.dumps(document(**answer, limit=args.limit, text=args.text,
                                  redacted=redact is not None, rows=rows), indent=2))
        return 0
    for unread in answer["roots"]:
        where = unread["root"] or "its root"
        if unread["reason"] is not None:
            print(f"{prog} list: {unread['harness']}: {where} could not be listed: "
                  f"{unread['reason']}", file=sys.stderr)
        else:
            print(f"{prog} list: {unread['harness']}: {unread['parts']} of the folders "
                  f"and files under {where} could not be listed; the sessions in them "
                  "are not counted", file=sys.stderr)
    for row in rows:
        print("  ".join((_when(row["started"]), _when(row["changed"]), row["harness"],
                         row["name"] or row["folder"] or "-", row["session"])))
        said = row["activity"]
        if args.text and said is not None:
            who = f"{said['who']}: " if said["who"] else ""
            print(f"    {who}{_one_line(said['text'])}")
    if answer["total"] > len(rows):
        print(f"{prog} list: the newest {len(rows)} of {answer['total']} sessions "
              "(--limit for more)", file=sys.stderr)
    return 0


def _list_row(row: dict, redact, *, text: bool) -> dict:
    """A list's row as the verb prints it: the name redacted; the newest thing said and
    the prompt redacted and with their text only when `text` is asked."""
    out = dict(row)
    if redact is not None and isinstance(row["name"], str):
        out["name"] = redact(row["name"])
    said = row["activity"]
    if said is not None:
        out["activity"] = {"at": said["at"], "who": said["who"]}
        if text:
            out["activity"]["text"] = redact(said["text"]) if redact else said["text"]
    prompt = view.cut_message(row["prompt"], redact)
    if prompt is not None and not text:
        prompt = {"at": prompt["at"]}
    out["prompt"] = prompt
    return out


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


def _probed(found) -> str:
    return "; ".join(f"{probe.root}: {probe.outcome}" for probe in found.probes)


def _pick(found):
    """The source to read of what `find` found: the first that isn't compressed, else the
    first, whose read then says why it can't be read."""
    hits = found.hits
    return next((h for h in hits if not h.compressed), hits[0]).source


def _offline(asked, asked_dirs) -> list[str]:
    """`folder (why)` for each archive folder in `folders` that was asked and didn't
    answer."""
    return [f"{probe.root} ({probe.outcome.partition(': ')[2] or probe.outcome})"
            for found in asked for probe in found.probes
            if probe.root in asked_dirs and probe.outcome.startswith(claude.UNREADABLE)]


def _unknown(prog: str, session: str, offline: list[str]) -> str:
    return (f"{prog} read: session {session} is unknown: no folder asked holds it, and "
            f"{'; '.join(offline)} didn't answer, so it may be there")


def _source(harness: str | None, session: str, prog: str):
    """The harness and source to read, or the reason and the exit code when there is none.

    With `harness` named, that harness's live root is asked, then its archive folders.
    Without it, every harness's live root is asked in turn with `find`, which looks at
    names and, for OpenCode, one row, never a whole transcript; only when none holds the
    session are the archive folders of every harness tested together, once, and asked.
    The one harness that holds the session is read, and more than one is refused."""
    if harness is not None:
        found = find(harness, session, include_archives=True)
        offline = _offline([found], set(archive_dirs(harness)))
        if not found.found and offline:
            return None, None, _unknown(prog, session, offline), 1
        if not found.found:
            return None, None, (f"{prog} read: no {harness} session {session} was found "
                                f"({_probed(found)})"), 1
        return harness, _pick(found), None, 0
    asked = [find(each, session) for each in HARNESSES]
    held = [found for found in asked if found.found]
    asked_dirs: list[str] = []
    if not held:
        asked_dirs = [archive_dir for each in HARNESSES for archive_dir in archive_dirs(each)]
        if asked_dirs:
            archive_answers(asked_dirs)
            asked = [find(each, session, include_archives=True) for each in HARNESSES]
            held = [found for found in asked if found.found]
    if len(held) == 1:
        offline = _offline([found for found in asked if not found.found], set(asked_dirs))
        if offline:
            print(f"{prog} read: {held[0].harness} holds session {session}; "
                  f"{'; '.join(offline)} didn't answer, so whether another harness holds "
                  "it too is not known", file=sys.stderr)
        return held[0].harness, _pick(held[0]), None, 0
    if held:
        names = " and ".join(found.harness for found in held)
        return None, None, (f"{prog} read: session {session} is held by {names}; give "
                            "--harness to say which to read"), 2
    offline = _offline(asked, set(asked_dirs))
    if offline:
        return None, None, _unknown(prog, session, offline), 1
    probed = "; ".join(f"{found.harness}: {_probed(found)}" for found in asked)
    return None, None, (f"{prog} read: no session {session} was found in any harness "
                        f"({probed})"), 1


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
    harness, source, why, code = _source(args.harness, session, prog)
    if source is None:
        print(why, file=sys.stderr)
        return code
    args.harness = harness
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

