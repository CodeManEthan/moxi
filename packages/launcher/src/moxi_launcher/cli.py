"""`moxi launcher`: launch a session, and read what was launched.

    moxi launcher run [--seat NAME | --project-preset NAME] [catalog flags] [-- extra]
    moxi launcher preview [--seat NAME | --project-preset NAME] [catalog flags] [--json]
    moxi launcher log [--limit N] [--before TIME] [--json]
    moxi launcher presets [list | show NAME | check] [--project KEY] [--json]
    moxi launcher catalog [HARNESS] [--json]
    moxi launcher handoff dir [--json]
    moxi launcher handoffs [--key KEY] [--json]
    moxi launcher deliver REQUEST_ID --pid PID

`run` launches; `preview` prints the argv and environment a launch would use and starts
nothing. `log` lists launches, newest first. `presets` lists, shows or checks the seats
and project presets. `catalog` lists the flag rows this install offers. `handoff dir`
prints the handoff folder for this session; `handoffs` lists the handoffs waiting.
`deliver` is internal: the detached process that sends a terminal launch's first turn.

The CLI calls the library in its own process. Exit codes are `moxi.exit`'s: 0 done, 1
failed, 2 bad arguments, 3 refused, 75 a lock held past its wait. A verb whose part is
not built yet says so and exits 1.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from moxi import exit as codes

#: The verbs that take the catalog's flags. Their parser is the launcher's rows plus the
#: chosen harness's catalog, read with `parse_known_args`, so an unknown flag passes on.
LAUNCH_VERBS = ("run", "preview")
PRESET_ACTIONS = ("list", "show", "check")


def _not_built(args: argparse.Namespace, prog: str) -> int:
    print(f"{prog} {args.verb}: not built yet", file=sys.stderr)
    return codes.ERROR


def _run(args: argparse.Namespace, prog: str) -> int:
    return _not_built(args, prog)


def _preview(args: argparse.Namespace, prog: str) -> int:
    return _not_built(args, prog)


def _log(args: argparse.Namespace, prog: str) -> int:
    return _not_built(args, prog)


def _presets(args: argparse.Namespace, prog: str) -> int:
    return _not_built(args, prog)


def _catalog(args: argparse.Namespace, prog: str) -> int:
    return _not_built(args, prog)


def _handoff(args: argparse.Namespace, prog: str) -> int:
    return _not_built(args, prog)


def _handoffs(args: argparse.Namespace, prog: str) -> int:
    return _not_built(args, prog)


def _deliver(args: argparse.Namespace, prog: str) -> int:
    return _not_built(args, prog)


VERBS: dict[str, Callable[[argparse.Namespace, str], int]] = {
    "run": _run,
    "preview": _preview,
    "log": _log,
    "presets": _presets,
    "catalog": _catalog,
    "handoff": _handoff,
    "handoffs": _handoffs,
    "deliver": _deliver,
}


def _launch_flags(parser: argparse.ArgumentParser) -> None:
    """The flags `run` and `preview` share: at most one preset, and the posture override."""
    preset = parser.add_mutually_exclusive_group()
    preset.add_argument("--seat", metavar="NAME", help="launch the seat NAME")
    preset.add_argument("--project-preset", metavar="NAME",
                        help="launch the project preset NAME")
    parser.add_argument("--off-posture", action="store_true",
                        help="allow a permission row that differs from your default")


def build_parser(prog: str = "moxi launcher") -> argparse.ArgumentParser:
    """The parser of every verb. `run` and `preview` carry only the launcher's fixed flags
    here; the catalog's rows join them when the catalog is read."""
    parser = argparse.ArgumentParser(
        prog=prog, description="Launch a coding-agent session, and read what was launched.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    run = verbs.add_parser("run", help="launch a session",
                           description="Launch a session from at most one preset and the "
                                       "flags typed. Flags after -- go to the harness last.")
    _launch_flags(run)

    preview = verbs.add_parser("preview", help="print what a launch would run",
                               description="Print the argv and environment a launch would "
                                           "use; starts nothing.")
    _launch_flags(preview)
    preview.add_argument("--json", action="store_true", help="print one JSON object")

    log = verbs.add_parser("log", help="list launches, newest first",
                           description="List launches from the launch log, newest first.")
    log.add_argument("--limit", type=int, metavar="N", help="list at most N launches")
    log.add_argument("--before", metavar="TIME",
                     help="list launches made before TIME (UTC ISO 8601)")
    log.add_argument("--json", action="store_true", help="print one JSON object per line")

    presets = verbs.add_parser("presets", help="list, show or check the presets",
                               description="List, show or check the seats and project "
                                           "presets in presets.toml.")
    presets.add_argument("action", nargs="?", choices=PRESET_ACTIONS, default="list",
                         help="list (the default), show NAME, or check")
    presets.add_argument("name", nargs="?", metavar="NAME", help="the preset to show")
    presets.add_argument("--project", metavar="KEY",
                         help="list the project's agents and the seat matching each")
    presets.add_argument("--json", action="store_true", help="print JSON")

    catalog = verbs.add_parser("catalog", help="list the flag rows this install offers",
                               description="List the flag rows offered on this install.")
    catalog.add_argument("harness", nargs="?", metavar="HARNESS",
                         help="list only this harness's rows")
    catalog.add_argument("--json", action="store_true", help="print JSON")

    handoff = verbs.add_parser("handoff", help="print this session's handoff folder",
                               description="Print the handoff folder for this session's "
                                           "key.")
    handoff.add_argument("what", choices=("dir",), help="dir")
    handoff.add_argument("--json", action="store_true", help="print JSON")

    handoffs = verbs.add_parser("handoffs", help="list the handoffs waiting",
                                description="List the handoffs waiting, newest first.")
    handoffs.add_argument("--key", metavar="KEY", help="list only this key's handoffs")
    handoffs.add_argument("--json", action="store_true", help="print JSON")

    deliver = verbs.add_parser("deliver", help="internal: send a launch's first turn",
                               description="Internal: wait for a launched session and send "
                                           "its first turn.")
    deliver.add_argument("request_id", metavar="REQUEST_ID", help="the launch's request id")
    deliver.add_argument("--pid", type=int, required=True, metavar="PID",
                         help="the pid the session runs as")
    return parser


def parse(argv: Sequence[str] | None = None, *, prog: str = "moxi launcher"
          ) -> argparse.Namespace:
    """Parse `argv`. For `run` and `preview` what the parser does not know is kept, in
    order, as `rest`; any other verb refuses it (exit 2)."""
    parser = build_parser(prog)
    args, rest = parser.parse_known_args(argv)
    if args.verb not in LAUNCH_VERBS and rest:
        parser.error(f"unrecognized arguments: {' '.join(rest)}")
    if args.verb == "presets" and args.name is not None and args.action != "show":
        parser.error(f"presets {args.action} takes no NAME")
    if args.verb == "presets" and args.action == "show" and args.name is None:
        parser.error("presets show needs a NAME")
    args.rest = rest
    return args


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi launcher") -> int:
    args = parse(argv, prog=prog)
    return VERBS[args.verb](args, prog)
