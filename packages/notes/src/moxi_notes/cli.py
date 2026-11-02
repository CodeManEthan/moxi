"""`moxi notes`: where a session's notes live, from the command line.

    moxi notes dir [PATH] [--json]
    moxi notes handoffs dir [PATH]
    moxi notes snapshot
    moxi notes scan [--json]
    moxi notes setup
    moxi notes inbox list [--json]
    moxi notes inbox add SLUG
    moxi notes inbox rename SLUG NEW

`dir` prints the notes directory for PATH (default: the working directory) and exits 1
when there is none; a warning goes to stderr. `snapshot` runs one pass of notes'
snapshot spec now and prints repo's line. `scan` prints what a snapshot would leave out
now and changes nothing; it exits 1 when that is any file, and its `--json` shape is
`moxi_notes.scan.JSON_SHAPE`, which its help prints. `setup` asks setup's questions
again and writes what they change, as `moxi install notes` does. `MOXI_NOTES_DIR` in this
command's own environment is passed as the override, as the launcher sets it for a launch. A refusal
prints its reason and the fix to stderr, and exits 1 (an error), 2 (usage or a bad
setting) or 3 (refused).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from moxi import exit as codes
from moxi.config import ConfigError

from . import api
from .errors import NotesError

#: The variable the launcher sets for one launch; read here, never by notes' API.
OVERRIDE = "MOXI_NOTES_DIR"

_PARSERS: dict[str, argparse.ArgumentParser] = {}


def _parser(prog: str) -> argparse.ArgumentParser:
    if prog in _PARSERS:
        return _PARSERS[prog]
    from .scan import JSON_SHAPE

    parser = argparse.ArgumentParser(
        prog=prog, description="Where each session keeps its notes. With the projects "
                               "module (moxi install projects), a project's own notes "
                               "folder wins.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    p = verbs.add_parser("dir", help="print the notes directory for a folder",
                         description="Print the notes directory for a folder; exit 1 "
                                     "when it has none.")
    p.add_argument("path", nargs="?", metavar="PATH",
                   help="the folder (default: the working directory)")
    p.add_argument("--json", action="store_true", help="print JSON")

    handoffs = verbs.add_parser("handoffs", help="the notes directory's handoffs folder",
                                description="The notes directory's handoffs folder.")
    actions = handoffs.add_subparsers(dest="action", metavar="ACTION")
    actions.required = True
    p = actions.add_parser("dir", help="print the handoffs folder for a folder",
                           description="Print the handoffs folder for a folder; exit 1 "
                                       "when it has no notes directory.")
    p.add_argument("path", nargs="?", metavar="PATH",
                   help="the folder (default: the working directory)")

    verbs.add_parser("snapshot", help="commit a snapshot of the notes repo now",
                     description="Run one pass of notes' snapshot spec through repo's "
                                 "committer now and print its line. It takes no settle "
                                 "duty: an unpushed commit waits for the next pass.")

    p = verbs.add_parser(
        "scan", help="what a snapshot would leave out now, with rules and lines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Judge the notes root's files as a snapshot would and print each one\n"
                    "it would leave out, with its rules and lines, and each one it would\n"
                    "commit redacted. Changes nothing. Exits 0 when no file would be left\n"
                    "out, 1 when one would or nothing could be judged, 2 on a bad setting.",
        epilog=JSON_SHAPE)
    p.add_argument("--json", action="store_true", help="print JSON (shape below)")

    verbs.add_parser("setup", help="ask setup's questions again and write the answers",
                     description="Ask where the notes are, what a commit does with a "
                                 "secret and where snapshots are pushed; preview what "
                                 "snapshots would leave out; then register the git hook "
                                 "gates, the clean filter and the snapshot spec in the "
                                 "notes repo. With nobody at the keyboard it keeps the "
                                 "current values and prints how to change each.")

    inbox = verbs.add_parser("inbox", help="inbox one-offs: list, add, rename",
                             description="Inbox one-offs: folders of notes for work that "
                                         "has no project.")
    actions = inbox.add_subparsers(dest="action", metavar="ACTION")
    actions.required = True
    p = actions.add_parser("list", help="list the one-offs, by slug",
                           description="List the one-offs, by slug.")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = actions.add_parser("add", help="make a one-off's folder",
                           description="Make a one-off's folder and print it.")
    p.add_argument("slug", metavar="SLUG")
    p = actions.add_parser("rename", help="rename a one-off",
                           description="Rename a one-off and print its new folder.")
    p.add_argument("slug", metavar="SLUG")
    p.add_argument("new", metavar="NEW")

    _PARSERS[prog] = parser
    return parser


def _path(text: str | None) -> str:
    if text is None:
        return os.getcwd()
    return os.path.abspath(os.path.expanduser(text))


def _override() -> str | None:
    return os.environ.get(OVERRIDE) or None


def _dir(args, out) -> int:
    found = api.notes_dir(_path(args.path), override=_override())
    if args.json:
        print(json.dumps(found.as_dict()), file=out)
    elif found.dir is not None:
        print(found.dir, file=out)
    if found.warning:
        print(f"warning: {found.warning}", file=sys.stderr)
    return codes.OK if found.dir is not None else codes.ERROR


def _handoffs(args, out) -> int:
    folder = api.handoffs_dir(_path(args.path), override=_override())
    if folder is None:
        print("no notes directory for this folder, so no handoffs folder", file=sys.stderr)
        return codes.ERROR
    print(folder, file=out)
    return codes.OK


def _inbox(args, out) -> int:
    if args.action == "list":
        found = api.inbox_list()
        if args.json:
            print(json.dumps([one.as_dict() for one in found]), file=out)
        else:
            for one in found:
                print(f"{one.slug}  {one.path}  {one.modified}", file=out)
        return codes.OK
    if args.action == "add":
        print(api.make_inbox(args.slug), file=out)
        return codes.OK
    print(api.rename_inbox(args.slug, args.new), file=out)
    return codes.OK


def _snapshot(args, out) -> int:
    from .snapshot import one_pass

    result, _, _ = one_pass()
    print(result.line(), file=out if result.exit_code == 0 else sys.stderr)
    return result.exit_code


def _scan(args, out) -> int:
    from .scan import print_scan, scan

    found = scan()
    if args.json:
        print(json.dumps(found.as_dict()), file=out)
    elif not found.error:
        print_scan(found, out)
    if found.error:
        print(f"notes: nothing could be judged: {found.error}", file=sys.stderr)
        return codes.ERROR
    return codes.ERROR if found.left_out else codes.OK


def _setup(args, out) -> int:
    from .setup import setup

    return setup(out=out)


_VERBS = {"dir": _dir, "handoffs": _handoffs, "snapshot": _snapshot, "scan": _scan,
          "setup": _setup, "inbox": _inbox}


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi notes") -> int:
    try:
        args = _parser(prog).parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    name = f"{prog} {args.verb}" + (f" {args.action}" if getattr(args, "action", None)
                                    else "")
    try:
        return _VERBS[args.verb](args, sys.stdout)
    except NotesError as exc:
        print(f"{name}: {exc.reason}", file=sys.stderr)
        if exc.fix:
            print(f"  fix: {exc.fix}", file=sys.stderr)
        return exc.exit_code
    except ConfigError as exc:
        print(f"{name}: {exc}", file=sys.stderr)
        return exc.exit_code
    except ValueError as exc:
        print(f"{name}: {exc}", file=sys.stderr)
        return codes.USAGE
