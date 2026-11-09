"""`moxi statusline`: the status line's verbs.

    moxi statusline use
    moxi statusline restore [--force]
    moxi statusline status [--json]
    moxi statusline pieces
    moxi statusline preview [--show a,b,c] [--no-color]

`use` sets Claude Code's status line to Moxi's. Over Moxi's line it points it at this
environment's render; with no line it asks first, and with nobody at the keyboard the
command is the yes. Over a line of your own it asks to run that line once with test input,
says what it shows, and offers to switch to Moxi's line set up to show the same, or to
keep yours and print the commands for Moxi's pieces; with nobody at the keyboard it prints
both and changes nothing. The line it replaces is kept for `restore`.

`restore` puts back the line `use` replaced, only while Claude Code's line is still
Moxi's: a line set since is the user's, and `--force` is the one way over it.

`status` says whether Claude Code's line is Moxi's, its command, the line kept to put
back, the items shown and which show nothing, and a project's own line in this folder.

`pieces` prints the commands that add Moxi's pieces to a status line of your own, one per
installed module with a segment, cache's timer first, and what each took, measured just
now as Claude Code would run it.

`preview` renders the sample input with the real command, as Claude would run it, and
prints the line and the time the render took. `--show` renders these items in place of
the config's `show`, held to the config's rules. It writes nothing.

Each verb is one `Verb` in `VERBS`: its name, its help, a function that adds its
arguments to its parser, and a function that runs it with the parsed arguments and
returns the exit code. A new verb is a new entry.
"""

import argparse
import sys
from collections.abc import Callable, Sequence


class Verb:
    """One verb of `moxi statusline`."""

    __slots__ = ("name", "help", "description", "arguments", "run")

    def __init__(self, name: str, help: str, description: str,
                 arguments: Callable[[argparse.ArgumentParser], None],
                 run: Callable[[argparse.Namespace], int]):
        self.name = name
        self.help = help
        self.description = description
        self.arguments = arguments
        self.run = run


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi statusline") -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="A Claude Code status line made of base fields and the "
                               "segments installed modules add.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True
    for verb in VERBS:
        sub = verbs.add_parser(verb.name, help=verb.help, description=verb.description)
        verb.arguments(sub)
        sub.set_defaults(run=verb.run)
    args = parser.parse_args(argv)
    return args.run(args)


def _items(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


# -- use, restore, status ------------------------------------------------------------------

def _no_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def _use(args: argparse.Namespace) -> int:
    from .setup import use

    return use()


def _restore_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action="store_true",
                        help="put the saved line back even over a line that isn't Moxi's")


def _restore(args: argparse.Namespace) -> int:
    from .switch import restore

    act = restore(force=args.force)
    for line in act.lines:
        print(line, file=sys.stdout if act.ok else sys.stderr)
    return act.code


def _status_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print it as JSON")


def _status(args: argparse.Namespace) -> int:
    import json

    from .status import lines, report

    data = report()
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        for line in lines(data):
            print(line)
    return 0


# -- pieces ----------------------------------------------------------------------------

def _pieces(args: argparse.Namespace) -> int:
    from . import pieces

    try:
        found = pieces.pieces()
    except FileNotFoundError as exc:
        print(f"moxi statusline: {exc}", file=sys.stderr)
        return 1
    for line in pieces.lines(found, pieces.measured(found) if found else None):
        print(line)
    return 0


# -- preview ---------------------------------------------------------------------------

def _preview_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--show", metavar="a,b,c", type=_items,
                        help="render these items in place of the config's show")
    parser.add_argument("--no-color", action="store_true", help="no colour")


def _preview(args: argparse.Namespace) -> int:
    from . import sample
    from .preview import PreviewError, render
    from .settings import show_problem

    if args.show is not None:
        problem = show_problem(args.show)
        if problem is not None:
            print(f"moxi statusline: --show {problem[0]}; {problem[1]}", file=sys.stderr)
            return 2
    try:
        done = render(sample.payload(), show=args.show, color=not args.no_color)
    except PreviewError as exc:
        print(f"moxi statusline: {exc}", file=sys.stderr)
        return 1
    for line in done.lines:
        print(line)
    if done.stderr:
        sys.stderr.write(done.stderr)
    print(f"rendered in {done.ms:.1f} ms")
    return 0


VERBS: list[Verb] = [
    Verb("use", "use Moxi's status line in Claude Code",
         "Set Claude Code's status line to Moxi's, keeping the line it replaces for "
         "restore. Over Moxi's line it points it at this environment's render.",
         _no_arguments, _use),
    Verb("restore", "put back the status line use replaced",
         "Put back the status line that use replaced, or remove the setting when there "
         "was none. Refused when Claude Code's line is no longer Moxi's, unless --force.",
         _restore_arguments, _restore),
    Verb("status", "say whether Claude Code's status line is Moxi's, and what it shows",
         "Whether Claude Code's status line is Moxi's, its command, the line kept to put "
         "back, the items shown and which show nothing, modules named like a base field, "
         "and a project's own status line in this folder. Writes nothing.",
         _status_arguments, _status),
    Verb("pieces", "print the commands that add Moxi's pieces to your own status line",
         "Print one command per installed module with a segment, cache's timer first, to "
         "call from your own status line script, and what each took, measured just now "
         "through sh -c with the sample input. Writes nothing.",
         _no_arguments, _pieces),
    Verb("preview", "render the sample input and print the line and its time",
         "Render the sample input with the real command, as Claude runs it, and print "
         "the line and how long the render took. Writes nothing.",
         _preview_arguments, _preview),
]
