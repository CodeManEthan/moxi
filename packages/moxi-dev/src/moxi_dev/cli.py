"""The `moxi-dev` command: one verb per tool, each in a module of its own.

The command reads only the verb. Everything after it goes to that module's
`main(argv)`, which parses its own arguments and prints its own `--help`.
"""

from __future__ import annotations

import argparse
import sys


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moxi-dev",
        description="Moxi's contributor tools, run from a checkout of the repository. "
                    "`moxi-dev <verb> --help` shows a verb's own arguments.")
    verbs = parser.add_subparsers(dest="verb", metavar="<verb>", required=True)
    verbs.add_parser("check", add_help=False,
                     help="run the checks a change must pass before it merges")
    verbs.add_parser("new", add_help=False,
                     help="new <module>: start a new package under packages/")
    verbs.add_parser("stamp", add_help=False,
                     help="check that each web page's build stamp matches its sources")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Read the verb and hand the rest of the arguments to its module's `main`."""
    args = list(sys.argv[1:] if argv is None else argv)
    verb = _parser().parse_args(args[:1]).verb
    if verb == "check":
        from moxi_dev import check
        return check.main(args[1:])
    if verb == "stamp":
        from moxi_dev import stamp
        return stamp.main(args[1:])
    from moxi_dev import new
    return new.main(args[1:])


if __name__ == "__main__":
    sys.exit(main())
