"""`moxi rounds`: the rounds command.

    moxi rounds VERB ...

Exit codes: 0 done; 1 an error; 2 bad arguments or a bad setting; 3 refused.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from moxi import exit as codes

#: Each verb's name -> the function that runs it, as `fn(args, out) -> int`.
_VERBS: dict[str, Callable] = {}


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog, description="Track a piece of work from design to landing: its "
                               "questions, rulings, builds and gates.")
    parser.add_subparsers(dest="verb", metavar="VERB")
    return parser


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi rounds") -> int:
    parser = _parser(prog)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    if args.verb is None:
        parser.print_usage(sys.stderr)
        print(f"{prog}: name a verb", file=sys.stderr)
        return codes.USAGE
    return _VERBS[args.verb](args, sys.stdout)
