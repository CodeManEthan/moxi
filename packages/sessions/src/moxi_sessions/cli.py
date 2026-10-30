"""`moxi sessions`: where a session's record is, and which projects root is read.

    moxi sessions find SESSION [--harness claude|codex] [--root PATH] [--json]
    moxi sessions root [--json]

`find` prints the path of the session's record, or every root it probed and why each
found none, and exits 1 then. `root` prints the Claude projects root a session that
stated none is read under, and where that root came from.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from moxi.config import ConfigError

from . import claude, codex
from .settings import current


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi sessions") -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="Find a harness's own record of a session.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    find = verbs.add_parser("find", help="print where a session's record is",
                            description="Print where a session's record is, or the roots "
                                        "probed and why none held it.")
    find.add_argument("session", metavar="SESSION", help="the session or thread id")
    find.add_argument("--harness", choices=("claude", "codex"), default="claude",
                      help="whose record to look for (default: claude)")
    find.add_argument("--root", action="append", default=[], metavar="PATH",
                      help="a further root to probe after the usual one; may repeat")
    find.add_argument("--json", action="store_true", help="print one JSON object")

    root = verbs.add_parser("root", help="print the Claude projects root and its source",
                            description="Print the Claude projects root read for a "
                                        "session that stated none, and where it came "
                                        "from.")
    root.add_argument("--json", action="store_true", help="print one JSON object")

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
    if args.harness == "claude":
        live = claude.projects_root(None)
        probes, paths = _find_claude(claude.search_roots(live=live, extra=args.root),
                                     session)
    else:
        live = _codex_sessions_root(os.environ)
        probes, paths = _find_codex(claude.search_roots(live=live, extra=args.root),
                                    session)
    if args.json:
        print(json.dumps({"harness": args.harness, "session": session,
                          "found": bool(paths), "paths": paths, "probes": probes},
                         indent=2))
    elif paths:
        for path in paths:
            print(path)
    else:
        what = "transcript" if args.harness == "claude" else "rollout"
        print(f"no {what} for {session}; probed:")
        for probe in probes:
            print(f"  {probe['root']}: {probe['outcome']}")
    return 0 if paths else 1


def _find_claude(roots: list[Path], session: str) -> tuple[list[dict], list[str]]:
    probes = []
    for root in roots:
        probe = claude.probe_root(root, session)
        probes.append({"root": probe.root, "outcome": probe.outcome,
                       "paths": [probe.path] if probe.path else []})
        if probe.hit:
            return probes, [probe.path]
    return probes, []


def _find_codex(roots: list[Path], thread: str) -> tuple[list[dict], list[str]]:
    probes = []
    for root in roots:
        try:
            found = [str(p) for p in codex.find_rollout(root, thread)]
        except OSError as exc:
            probes.append({"root": str(root), "outcome": f"unreadable: {exc}", "paths": []})
            continue
        if found:
            probes.append({"root": str(root), "outcome": "hit", "paths": found})
            return probes, found
        probes.append({"root": str(root), "outcome": "miss", "paths": []})
    return probes, []


def _codex_sessions_root(environ) -> Path:
    """Codex's own rule: `${CODEX_HOME:-~/.codex}/sessions`."""
    home = environ.get("CODEX_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".codex"
    return base / "sessions"


# --- root -----------------------------------------------------------------------------

def _root(args) -> int:
    settings = current()
    path = claude.projects_root(None)
    if settings["claude.projects"] is not None:
        source, detail = "config", f"claude.projects in {settings.file}"
    elif os.environ.get("CLAUDE_CONFIG_DIR"):
        source, detail = "environment", "CLAUDE_CONFIG_DIR/projects"
    else:
        source, detail = "default", "~/.claude/projects (CLAUDE_CONFIG_DIR is unset)"
    if args.json:
        print(json.dumps({"root": str(path), "source": source, "detail": detail},
                         indent=2))
    else:
        print(path)
        print(f"  from: {detail}")
    return 0
