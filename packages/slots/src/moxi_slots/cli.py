"""`moxi slots`: the slots command.

Exit codes are `moxi.exit`'s: 0 done; 1 error (tmux failed, the registry unreadable);
2 usage; 3 refused (occupied, held, in use, missing, name taken, a system slots does not
run on); 75 the lock not had in 10 s or the server not yet answering. On native Windows
every verb says where slots runs and exits 3.

Each verb is added to `VERBS` as `name -> (add, run)`: `add(subparsers)` declares its
parser, `run(args) -> int` runs it. A refusal prints its reason word and the command
that fixes it.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from moxi import exit as codes

from . import support
from .errors import Refused, TmuxError, TryAgain

#: The verbs, in the order `--help` lists them.
VERBS: dict[str, tuple[Callable[[argparse._SubParsersAction], None],
                       Callable[[argparse.Namespace], int]]] = {}


def parser(prog: str) -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog=prog, description="Numbered tmux sessions to launch into, on one supervised "
                               "tmux server.")
    verbs = top.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True
    for add, _ in VERBS.values():
        add(verbs)
    return top


def refused(error: Refused) -> int:
    print(f"{error.reason}: {error.words}", file=sys.stderr)
    return codes.REFUSED


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi slots") -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not support.system_ok():
        print(f"unsupported: {support.unsupported_words()}", file=sys.stderr)
        return codes.REFUSED
    try:
        args = parser(prog).parse_args(args_list)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    _, run = VERBS[args.verb]
    try:
        return run(args)
    except Refused as exc:
        return refused(exc)
    except TryAgain as exc:
        print(f"try again: {exc}", file=sys.stderr)
        return codes.TEMPFAIL
    except TmuxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return codes.ERROR


__all__ = ["VERBS", "parser", "refused", "main"]
