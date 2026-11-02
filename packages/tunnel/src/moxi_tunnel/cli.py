"""`moxi tunnel`: give a loopback service an address, from the command line.

    moxi tunnel status [--json] [--check]
    moxi tunnel add TARGET [--provider tailscale|cloudflare] [--port N] [--hostname H]
                           [--replace] [--yes] [--json]
    moxi tunnel remove TARGET [--forget] [--yes] [--json]
    moxi tunnel repair [--yes] [--json]

TARGET is `<module>/<name>`, or the bare name when only one module declares it. Every act
that changes a provider's config prints its warning lines and asks once, `[y/N]`; `--yes`
answers yes and the lines are still printed. With no terminal and no `--yes` it refuses.

Exit codes: 0 ok; 1 a provider command failed, its stderr shown unchanged; 2 usage; 3
refused (a prerequisite is missing, the port is taken, no confirmation, a bad declaration,
or for `repair` something left for the user); 75 a lock is busy, or what the act would do
changed while it waited. With `--json` a failure prints `{"error": ..., "fix": ...}`.

`status --json` prints `{"targets": [...], "displaced": [...]}`: one object per declared
or recorded target with `module`, `name`, `reach`, `exposure`, `provider`, `state`, `url`,
`host`, `port`, `target`, `change`, `displaced`, `applied_at` and `detail`, and with
`--check` a `check` field holding the live comparison's state; then one object per copy in
`displaced/`, a pending one marked with what settles it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict

from moxi import exit as codes
from moxi import files
from moxi.config import ConfigError

from . import acts
from .errors import Refused, TunnelError

_PROVIDERS = ("tailscale", "cloudflare")


def _port(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a port number") from None
    if not 0 < value < 65536:
        raise argparse.ArgumentTypeError("must be a port from 1 to 65535")
    return value


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog, description="Give a module's loopback service an address something off "
                               "the box can reach: Tailscale Serve for your own devices, "
                               "Tailscale Funnel for public callers.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    p = verbs.add_parser("status", help="every target, its address and state",
                         description="Every declared or recorded target, with reach, "
                                     "exposure, provider, state, URL and loopback port, "
                                     "then the copies of foreign config in displaced/.")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--check", action="store_true",
                   help="also compare each entry with the live config")

    p = verbs.add_parser("add", help="turn a target on",
                         description="Turn a target on. On a recorded target with another "
                                     "provider, switch it.")
    p.add_argument("target", metavar="TARGET")
    p.add_argument("--provider", choices=_PROVIDERS, help="the provider (default tailscale)")
    p.add_argument("--port", type=_port, help="the outside port")
    p.add_argument("--hostname", metavar="H", help="the hostname (Cloudflare only)")
    p.add_argument("--replace", action="store_true",
                   help="take over the port from config Moxi did not apply; a copy is kept")
    p.add_argument("--yes", action="store_true", help="answer yes; the lines still print")
    p.add_argument("--json", action="store_true", help="print JSON")

    p = verbs.add_parser("remove", help="turn a target off",
                         description="Turn a target off. It also removes an orphan, and "
                                     "undoes a replace that has not settled.")
    p.add_argument("target", metavar="TARGET")
    p.add_argument("--forget", action="store_true",
                   help="drop it from the record and leave the live config alone")
    p.add_argument("--yes", action="store_true", help="answer yes; the lines still print")
    p.add_argument("--json", action="store_true", help="print JSON")

    p = verbs.add_parser("repair", help="put every entry right that tunnel can",
                         description="Settle every pending replace, then re-apply missing, "
                                     "moved and half-done entries and move renamed ones to "
                                     "the machine's new name.")
    p.add_argument("--yes", action="store_true", help="answer yes; the lines still print")
    p.add_argument("--json", action="store_true", help="print JSON")
    return parser


def _asker(args):
    out = sys.stderr if getattr(args, "json", False) else sys.stdout

    def ask(lines: list[str]) -> bool:
        for line in lines:
            print(line, file=out)
        if args.yes:
            return True
        if not (sys.stdin and sys.stdin.isatty()):
            raise Refused("there is no terminal to confirm on", "add --yes")
        print("Go ahead? [y/N] ", end="", file=out, flush=True)
        return sys.stdin.readline().strip().lower() in ("y", "yes")

    return ask


def _status(args, out) -> int:
    got = acts.status(live=args.check)
    if args.json:
        print(json.dumps(got, indent=2), file=out)
        return codes.OK
    if not got["targets"]:
        print("no target is declared or recorded", file=out)
    for row in got["targets"]:
        key = f"{row['module']}/{row['name']}"
        if row["state"] is None:
            print(f"{key}  {row['reach']}  off", file=out)
            continue
        line = (f"{key}  {row['reach']}  {row['exposure']}  {row['provider']}  "
                f"{row['state']}  {row['url']}  -> 127.0.0.1:{row['target']}")
        if args.check:
            line += f"  [{row['check']}]"
        print(line, file=out)
        for extra in (row.get("check_detail"), row["detail"]):
            if extra:
                print(f"    {extra}", file=out)
        if row.get("fix"):
            print(f"    fix: {row['fix']}", file=out)
    for copy in got["displaced"]:
        mark = "pending, settled by moxi tunnel repair or remove" if copy["pending"] else "kept"
        print(f"displaced: {copy['path']} (port {copy['port']}, {mark})", file=out)
    return codes.OK


def _show(outcome: acts.Outcome, args, out) -> int:
    if args.json:
        address = asdict(outcome.address) if outcome.address else None
        print(json.dumps({"address": address, "notes": outcome.notes,
                          "code": outcome.code}, indent=2), file=out)
    else:
        for note in outcome.notes:
            print(note, file=out)
    return outcome.code


def _add(args, out) -> int:
    return _show(acts.add(args.target, provider_name=args.provider, port=args.port,
                          hostname=args.hostname, replace_=args.replace,
                          ask=_asker(args)), args, out)


def _remove(args, out) -> int:
    return _show(acts.remove(args.target, ask=_asker(args), forget=args.forget), args, out)


def _repair(args, out) -> int:
    return _show(acts.repair(ask=_asker(args)), args, out)


_VERBS = {"status": _status, "add": _add, "remove": _remove, "repair": _repair}


def _fail(args, name: str, reason: str, fix: str | None, code: int) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"error": reason, "fix": fix}), file=sys.stdout)
    else:
        print(f"{name}: {reason}", file=sys.stderr)
        if fix:
            print(f"  fix: {fix}", file=sys.stderr)
    return code


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi tunnel") -> int:
    try:
        args = _parser(prog).parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    name = f"{prog} {args.verb}"
    try:
        return _VERBS[args.verb](args, sys.stdout)
    except TunnelError as exc:
        return _fail(args, name, exc.reason, exc.fix, exc.exit_code)
    except files.LockTimeout as exc:
        return _fail(args, name, str(exc), "try again once it is done", exc.exit_code)
    except ConfigError as exc:
        return _fail(args, name, str(exc), exc.fix, exc.exit_code)


__all__ = ["main"]
