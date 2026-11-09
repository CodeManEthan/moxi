"""`moxi statusline`: the status line's verbs.

    moxi statusline preview [--show a,b,c] [--no-color]

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
    Verb("preview", "render the sample input and print the line and its time",
         "Render the sample input with the real command, as Claude runs it, and print "
         "the line and how long the render took. Writes nothing.",
         _preview_arguments, _preview),
]
