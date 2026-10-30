"""`moxi sessions`: where a session's records are, and which root each harness is read
from.

    moxi sessions find SESSION [--harness claude|codex|opencode] [--root PATH] [--json]
    moxi sessions root [--json]

`find` prints where the session's records are (a Claude Code transcript, every rollout
file of a Codex thread, or the OpenCode database that holds the session), or every root it
probed and why each held none, and exits 1 then. `root` prints the root each harness is
read from when a caller names none, and where that root came from. With `--json`, each
prints one JSON object whose first key is `shape`, the shape version, as every JSON
document sessions writes has.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from moxi.config import ConfigError

from . import claude
from .find import find
from .shape import CLAUDE, CODEX, HARNESSES, document
from .source import root, root_origin

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

    args = parser.parse_args(argv)
    try:
        if args.verb == "find":
            return _find(args, prog)
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
