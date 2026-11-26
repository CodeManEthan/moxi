"""`moxi memory subconscious`: the subconscious's verbs.

    moxi memory subconscious list [--session ID] [--json]
    moxi memory subconscious recall TOPIC [--json]
    moxi memory subconscious discard RECALL_ID

`list` prints the subconscious's log (`moxi_memory.recalls`), oldest first: one line per
recall, with its id, state, when it started, its session, how it started, the head of its
prompt or topic, its route, its rounds and the reason of a `lost` state, and for a line
still `sending` how long it has waited for comms and comms' last refusal; and one line
per stretch a session's subconscious could not run (`off`), with the reason. `--session`
keeps one session's lines, and prints each round's answer under its recall. `--json`
prints the lines as stored, one JSON array.

`recall`, run by the agent with its shell tool in a session whose launch turned the
subconscious on on demand, starts a recall on TOPIC and returns at once
(`moxi_memory.recall_start.ask`); its points come back to the agent as the recall finds
them, never through this command. It exits 3 with the reason when it refuses.

`discard`, run by a person, ends a recall's line whose answer memory still holds to send
and never will (`moxi_memory.recall_discard`). It stops no process. It exits 3 with the
reason when it refuses.
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
    r = sub.add_parser(
        "recall", help="start a recall on a topic (on-demand sessions)",
        description="Start a second agent, a fork of this session, that searches past "
                    "sessions and notes for TOPIC and sends you what it finds, in rounds, "
                    "as it finds it. Returns at once; keep working. Runs only in a "
                    "session launched with the subconscious on demand, and at most "
                    "subconscious.on_demand_per_prompt times between two prompts of new "
                    "work. A newer recall hurries one still running.")
    r.add_argument("topic", metavar="TOPIC", help="what to recall")
    r.add_argument("--json", action="store_true", help="print the started recall as JSON")
    d = sub.add_parser(
        "discard", help="end a recall whose answer can never be sent",
        description="End a recall's line whose answer memory still holds to send and "
                    "never will. It stops no process; the recall gets no further round.")
    d.add_argument("recall_id", metavar="RECALL_ID", help="the recall's id, as list shows it")


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


def _recall(args, out, prog: str) -> int:
    from . import recall_start

    try:
        record = recall_start.ask(args.topic)
    except recall_start.Refused as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return codes.REFUSED
    topic = record.get("text") or ""
    if args.json:
        print(json.dumps({"recall": record.get("recall"), "session": record.get("session"),
                          "state": record.get("state"), "topic": topic},
                         sort_keys=True, ensure_ascii=False), file=out)
    else:
        print(recall_start.STARTED.format(topic=topic), file=out)
    return codes.OK


def _discard(args, out, prog: str) -> int:
    from . import recall_discard

    if not recalls.is_safe(args.recall_id):
        print(f"{prog}: {args.recall_id!r} is not a recall id", file=sys.stderr)
        return codes.USAGE
    try:
        line = recall_discard.discard(args.recall_id)
    except recall_discard.Refused as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return codes.REFUSED
    print(format_line(line), file=out)
    return codes.OK


def format_line(line: dict, *, now: float | None = None) -> str:
    """One log line as one line of text; a `lost` line ends with its reason and error,
    a `sending` one with how long it has waited for comms and comms' last refusal."""
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
    flags = [name for name in ("hurried", "past_limit", "past_bound") if line.get(name)]
    if flags:
        parts.append(", ".join(f.replace("_", " ") for f in flags))
    if state == recalls.SENDING:
        parts.append(sending_text(line, now=now))
    reason = str(line.get("reason") or "")
    error = " ".join(str(line.get("error") or "").split())
    if error and state == recalls.LOST:
        parts.append(error if error.split(":")[0].split()[0] == reason
                     else f"{reason}: {error}" if reason else error)
    elif reason:
        parts.append(reason)
    return "  ".join(parts)


def sending_text(line: dict, *, now: float | None = None) -> str:
    """How long a `sending` line has waited for comms, and comms' last refusal."""
    import time

    now = time.time() if now is None else now
    since = line.get("sending_at")
    waited = (f"{max(0, int(now - since))}s" if isinstance(since, (int, float))
              else "an unknown time")
    tries = int(line.get("send_tries") or 0)
    said = f"sending for {waited}"
    if tries:
        said += f", {tries} tries refused, the last: {line.get('send_reason') or 'no reason'}"
    return said


def round_lines(line: dict) -> list[str]:
    """Each round of a recall's line as one line of text: its number, when its answer
    came back, and the answer's start, or why it has none."""
    out = []
    for r in line.get("rounds") or []:
        if not isinstance(r, dict):
            continue
        back = r.get("at")
        when = show(back) if isinstance(back, (int, float)) else "-"
        text = r.get("text")
        if isinstance(text, str):
            said = " ".join(text.split())
            if len(said) > ANSWER_CHARS:
                said = said[:ANSWER_CHARS - 3] + "..."
        else:
            said = f"(no answer: {r.get('lost') or 'none yet'})"
        out.append(f"round {r.get('round', '?')}  {when}  {said}")
    return out


_VERBS = {"list": _list, "recall": _recall, "discard": _discard}

__all__ = ["ANSWER_CHARS", "add_parser", "run", "format_line", "sending_text", "round_lines"]
