"""`moxi rounds`: the rounds command.

    moxi rounds open ROUND --description TEXT [--flow NAME] [--record PTR] [--as NAME]
    moxi rounds ask ROUND|PROJECT TEXT --from PTR [--blocks LIST|none] [--record PTR]
                    [--as NAME]
    moxi rounds rule ROUND|PROJECT [ITEM] --record PTR [--text TEXT] [--reason TEXT]
                     [--standing] [--scope SPEC] [--stages LIST] [--until WHAT]
                     [--as NAME]
    moxi rounds mark ROUND Q<n> --blocks LIST|all | --reopen [--as NAME]

`ROUND` is `<project>/<slug>` and `PROJECT` a project's key. `PTR` is a pointer: to
where the operator said something, or to where a question came from. It is
`comms:<digits>`, `<session>@<UTC time>`, an `https://` URL or `file:<path>#<anchor>`.
`ITEM` is what a ruling is on: a question's id for its answer, `park:Q<n>`,
`narrow:Q<n>=LIST|none` or `withdraw:Q<n>`; with none the ruling stands on its own. A verb that writes takes `--as NAME`, who is doing it;
without it that is the user the command runs as.

Exit codes: 0 done; 1 an error; 2 bad arguments or a bad setting; 3 refused. A refusal
says what stopped the verb and the command that fixes it, and has written nothing.

Each verb is three things here: a function that declares its arguments on the verb
group (`_declare_<verb>`, listed in `_DECLARE`), a function that runs it as
`fn(args, out) -> int` (listed in `_VERBS`), and the module that does its work, which
the second calls.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence

from moxi import exit as codes
from moxi.config import ConfigError

from .errors import BadArgument, Refused, RoundsError


# -- what every writing verb shares ------------------------------------------------------

def _writes(parser: argparse.ArgumentParser) -> None:
    """Give a writing verb the option that says who is doing it."""
    parser.add_argument("--as", dest="actor", metavar="NAME",
                        help="who is doing this, kept with what is recorded (default: "
                             "the user the command runs as)")


def _actor(args) -> str:
    """Who the verb is done by: the name given with `--as`, else the OS user."""
    if args.actor is not None:
        if not args.actor.strip():
            raise BadArgument("--as names nobody", "give a name: --as NAME")
        return args.actor
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        raise BadArgument("the user this command runs as has no name",
                          "say who is doing this with --as NAME") from None


def _warn_of(failed, prog: str, verb: str) -> None:
    """Say which work after the record failed. What was recorded stands, and that work
    is redone by the next verb that writes, so the verb itself is done."""
    for _callback, error in failed:
        print(f"{prog} {verb}: warning: recorded, but what is kept from the record was "
              f"not brought up to date ({error}); the next command that writes does it "
              "again", file=sys.stderr)


# -- open --------------------------------------------------------------------------------

def _declare_open(verbs) -> None:
    said = ("Open a round: make its folder in the project's rounds folder and record "
            "what it is for and the flow it follows from then on.")
    parser = verbs.add_parser("open", help=said, description=said)
    parser.add_argument("round", metavar="ROUND",
                        help="the new round's name, <project>/<slug>: a project's key "
                             "and a slug of lower-case letters, digits and -")
    parser.add_argument("--description", metavar="TEXT", required=True,
                        help="what the round is for, in a line")
    parser.add_argument("--flow", metavar="NAME",
                        help="the flow the round follows (default: the project's flow "
                             "setting)")
    parser.add_argument("--record", metavar="PTR",
                        help="a pointer to where the operator gave the go")
    _writes(parser)


def _open(args, out) -> int:
    from . import opening

    opened = opening.open_round(args.round, description=args.description,
                                actor=_actor(args), flow=args.flow, record=args.record)
    steps = ", ".join(step["step"] for step in opened.steps) or "no steps"
    print(f"opened {opened.name}   flow {opened.flow} ({steps})", file=out)
    print(f"folder: {opened.folder}", file=out)
    _warn_of(opened.failed, args.prog, args.verb)
    return codes.OK


# -- rule --------------------------------------------------------------------------------

def _declare_rule(verbs) -> None:
    said = ("Record a ruling of the operator's: one plain sentence, with a pointer to "
            "where he said it. On a round it is the round's own; with --standing, or on "
            "a project, it goes in the project's register and outlasts the round. With "
            "an ITEM it rules a question: answers it, parks it, narrows what it holds "
            "up, or withdraws it.")
    parser = verbs.add_parser("rule", help=said, description=said)
    parser.add_argument("target", metavar="ROUND|PROJECT",
                        help="the round the ruling is on, <project>/<slug>, or a "
                             "project's key for a standing ruling with no round")
    parser.add_argument("item", metavar="ITEM", nargs="?",
                        help="what is ruled, when it is a question: Q<n> (P<n> on a "
                             "project) for its answer, given with --text; park:Q<n>; "
                             "narrow:Q<n>=LIST or narrow:Q<n>=none, the stages it "
                             "holds up from then on; withdraw:Q<n>")
    parser.add_argument("--record", metavar="PTR",
                        help="a pointer to where the operator said it, one token: "
                             "comms:<digits>, <session>@<UTC time>, an https:// URL, or "
                             "file:<path in the round's folder>#<anchor>; a ruling "
                             "without one is refused")
    parser.add_argument("--text", metavar="TEXT",
                        help="the ruling, in a sentence or a few that read on their own")
    parser.add_argument("--reason", metavar="TEXT", help="why, in short")
    parser.add_argument("--standing", action="store_true",
                        help="record a round's ruling in the project's register")
    parser.add_argument("--scope", metavar="SPEC",
                        help="the repos and paths a standing ruling applies to: KEY or "
                             "KEY:GLOB, several separated by commas")
    parser.add_argument("--stages", metavar="LIST",
                        help="the stages a round's ruling applies to, as B2,B3 "
                             "(default: every stage)")
    parser.add_argument("--until", metavar="WHAT",
                        help="with park:Q<n>, what the question waits for: a stage "
                             "of the round, B<n>, or another round, "
                             "round:<project>/<slug>; it reads as open again once "
                             "that stage is finished or that round is closed")
    _writes(parser)


def _rule(args, out) -> int:
    from . import ruling

    ruled = ruling.rule(args.target, actor=_actor(args), record=args.record,
                        text=args.text, reason=args.reason, standing=args.standing,
                        scope=args.scope, stages=args.stages, item=args.item,
                        until=args.until)
    for line in _recorded(ruled):
        print(line, file=out)
    if ruled.long:
        print(f"{args.prog} {args.verb}: note: {ruled.id} is {len(ruled.text)} "
              f"characters long and is recorded as given; a ruling is meant to be "
              f"short, {ruling.LONG} characters or fewer", file=sys.stderr)
    _warn_of(ruled.failed, args.prog, args.verb)
    return codes.OK


def _recorded(ruled) -> list[str]:
    """What is printed for a ruling just recorded: its id and its sentence, as the log
    now holds it, or what it rules when it has no sentence. What the one who recorded
    it does next with the sentence goes here."""
    return [f"{ruled.id}: {ruled.event['rules'] if ruled.text is None else ruled.text}"]


# -- ask ---------------------------------------------------------------------------------

def _declare_ask(verbs) -> None:
    said = ("Put a question to the operator: one sentence, with a pointer to where it "
            "came from. On a round it holds up every stage unless --blocks says "
            "otherwise; on a project, with no round yet, it goes on the project's list.")
    parser = verbs.add_parser("ask", help=said, description=said)
    parser.add_argument("target", metavar="ROUND|PROJECT",
                        help="the round the question is asked in, <project>/<slug>, or "
                             "a project's key")
    parser.add_argument("text", metavar="TEXT",
                        help="the question, one sentence on one line")
    parser.add_argument("--from", dest="origin", metavar="PTR",
                        help="a pointer to where the question came from, one token: "
                             "comms:<digits>, <session>@<UTC time>, an https:// URL, or "
                             "file:<path in the round's folder>#<anchor>, as "
                             "file:design.md#<anchor> for a section of the design; a "
                             "question with no origin is refused")
    parser.add_argument("--record", metavar="PTR",
                        help="in place of --from, for a question that is the "
                             "operator's own: a pointer to where he asked it")
    parser.add_argument("--blocks", metavar="LIST|none",
                        help="the stages the question holds up, as B2,B3, or none "
                             "(default: all, every stage the round has or gains)")
    _writes(parser)


def _ask(args, out) -> int:
    from . import asking, questions

    asked = asking.ask(args.target, args.text, actor=_actor(args), origin=args.origin,
                       record=args.record, blocks=args.blocks)
    print(f"{asked.id}: {asked.text}", file=out)
    print(f"blocks: {questions.said(asked.blocks)}   from: {asked.origin}", file=out)
    _warn_of(asked.failed, args.prog, args.verb)
    return codes.OK


# -- mark --------------------------------------------------------------------------------

def _declare_mark(verbs) -> None:
    said = ("Change a record of a round in a way anyone may: make a question hold up "
            "more stages, or reopen a parked one.")
    parser = verbs.add_parser("mark", help=said, description=said)
    parser.add_argument("round", metavar="ROUND", help="the round, <project>/<slug>")
    parser.add_argument("id", metavar="ID", help="what is marked: a question, Q<n>")
    form = parser.add_mutually_exclusive_group(required=True)
    form.add_argument("--blocks", metavar="LIST|all",
                      help="the stages the question holds up from now on, as B2,B3, "
                           "or all; it must hold up every stage it did before, since "
                           "only the operator narrows it (rule narrow:Q<n>=LIST)")
    form.add_argument("--reopen", action="store_true",
                      help="make a parked question open again")
    _writes(parser)


def _mark(args, out) -> int:
    from . import marking, questions

    if args.reopen:
        marked = marking.reopen(args.round, args.id, actor=_actor(args))
    else:
        marked = marking.widen(args.round, args.id, args.blocks, actor=_actor(args))
    found = marked.question
    print(f"{found.id}: {found.state}   blocks: {questions.said(found.blocks)}",
          file=out)
    _warn_of(marked.failed, args.prog, args.verb)
    return codes.OK


# -- the command -------------------------------------------------------------------------

#: The function that declares each verb's arguments, in the order `--help` lists them.
_DECLARE: tuple[Callable, ...] = (_declare_open, _declare_ask, _declare_rule,
                                  _declare_mark)

#: Each verb's name -> the function that runs it, as `fn(args, out) -> int`.
_VERBS: dict[str, Callable] = {"open": _open, "ask": _ask, "rule": _rule,
                               "mark": _mark}


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog, description="Track a piece of work from design to landing: its "
                               "questions, rulings, builds and gates.")
    parser.set_defaults(prog=prog)
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    for declare in _DECLARE:
        declare(verbs)
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
    try:
        return _VERBS[args.verb](args, sys.stdout)
    except Refused as exc:
        print(f"{prog} {args.verb}: {exc}", file=sys.stderr)
        return exc.exit_code
    except RoundsError as exc:
        print(f"{prog} {args.verb}: {exc.reason}", file=sys.stderr)
        if exc.fix:
            print(f"  fix: {exc.fix}", file=sys.stderr)
        return exc.exit_code
    except ConfigError as exc:
        print(f"{prog} {args.verb}: {exc}", file=sys.stderr)
        return exc.exit_code
