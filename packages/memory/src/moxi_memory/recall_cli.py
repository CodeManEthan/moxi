"""`moxi memory subconscious`: the subconscious's verbs.

    moxi memory subconscious list [--session ID] [--json]

`list` prints the subconscious's log (`moxi_memory.recalls`), oldest first: one line per
recall, with its id, state, when it started, its session, how it started, the head of its
prompt or topic, its route, its rounds and the reason of a `lost` state; and one line
per stretch a session's subconscious could not run (`off`), with the reason. `--session`
keeps one session's lines, and prints each round's answer under its recall. `--json`
prints the lines as stored, one JSON array.
"""

from __future__ import annotations

import json
import sys

from moxi import exit as codes

from . import recalls
from .clock import show

#: How many characters of a round's answer `list --session` shows on its line.
ANSWER_CHARS = 200


def add_parser(verbs) -> None:
    """Add `subconscious` and its own verbs to `moxi memory`'s verbs."""
    p = verbs.add_parser("subconscious", help="see the subconscious's recalls",
                         description="The subconscious: a second agent, a fork of the "
                                     "session, that recalls past sessions and notes for "
                                     "the main agent.")
    sub = p.add_subparsers(dest="subverb", metavar="VERB")
    sub.required = True
    q = sub.add_parser("list", help="show the log of recalls",
                       description="Show every recall in memory's log, and each stretch "
                                   "a session's subconscious could not run, oldest first.")
    q.add_argument("--session", metavar="ID", help="only this session's lines, each "
                                                   "round's answer included")
    q.add_argument("--json", action="store_true", help="print the lines as one JSON array")


def run(args, out, prog: str) -> int:
    """Run `moxi memory subconscious <verb>`."""
    return _VERBS[args.subverb](args, out, f"{prog} subconscious {args.subverb}")


def _list(args, out, prog: str) -> int:
    if args.session is not None and not recalls.is_safe(args.session):
        print(f"{prog}: {args.session!r} is not a session id", file=sys.stderr)
        return codes.USAGE
    lines = recalls.all_lines(args.session)
    if args.json:
        print(json.dumps(lines, indent=2, sort_keys=True, ensure_ascii=False), file=out)
        return codes.OK
    if not lines:
        where = f" for session {args.session}" if args.session else ""
        print(f"no recalls logged{where}", file=out)
        return codes.OK
    for line in lines:
        print(format_line(line), file=out)
        if args.session is not None:
            for text in round_lines(line):
                print(f"    {text}", file=out)
    return codes.OK


def format_line(line: dict) -> str:
    """One log line as one line of text."""
    when = show(line.get("at") if isinstance(line.get("at"), (int, float)) else None)
    session = line.get("session") or "-"
    state = line.get("state") or "-"
    if state == recalls.OFF:
        return f"-  {state}  {when}  session {session}  {line.get('reason') or ''}".rstrip()
    rounds = line.get("rounds") or []
    parts = [str(line.get("recall") or "-"), state, when, f"session {session}",
             str(line.get("start") or "-"), json.dumps(line.get("head") or "",
                                                       ensure_ascii=False),
             str(line.get("route") or "-"), f"rounds {len(rounds)}"]
    flags = [name for name in ("hurried", "past_limit") if line.get(name)]
    if flags:
        parts.append(", ".join(f.replace("_", " ") for f in flags))
    if line.get("reason"):
        parts.append(str(line["reason"]))
    return "  ".join(parts)


def round_lines(line: dict) -> list[str]:
    """Each round of a recall's line as one line of text: its number, when its answer
    came back, and the answer's start, or why it has none."""
    out = []
    for r in line.get("rounds") or []:
        if not isinstance(r, dict):
            continue
        back = r.get("back_at")
        when = show(back) if isinstance(back, (int, float)) else "-"
        text = r.get("text")
        if isinstance(text, str):
            said = " ".join(text.split())
            if len(said) > ANSWER_CHARS:
                said = said[:ANSWER_CHARS - 3] + "..."
        else:
            said = f"(no answer: {r.get('reason') or 'none yet'})"
        out.append(f"round {r.get('n', '?')}  {when}  {said}")
    return out


_VERBS = {"list": _list}

__all__ = ["ANSWER_CHARS", "add_parser", "run", "format_line", "round_lines"]
