"""`moxi rounds`: the rounds command.

    moxi rounds open ROUND --description TEXT [--flow NAME] [--record PTR] [--as NAME]
    moxi rounds ask ROUND|PROJECT TEXT --from PTR [--blocks LIST|none] [--record PTR]
                    [--option K=TEXT]... [--as NAME]
    moxi rounds ask ROUND|PROJECT ID [--option K=TEXT]... [--retire K]... [--as NAME]
    moxi rounds ask ROUND --adopt P<n> [--blocks LIST [--record PTR]] [--as NAME]
    moxi rounds ask PROJECT --move ROUND:Q<n> [--record PTR] [--as NAME]
    moxi rounds rule ROUND|PROJECT [ITEM] --record PTR [--text TEXT] [--chose K]
                     [--reason TEXT] [--standing] [--scope SPEC] [--stages LIST]
                     [--until WHAT] [--supersedes C<n>|S<n>] [--as NAME]
    moxi rounds rule PROJECT readback=TIME --record PTR [--as NAME]
    moxi rounds mark ROUND Q<n> --blocks LIST|all | --reopen [--as NAME]
    moxi rounds mark ROUND C<n>[,C<n>...] --shown --record PTR [--as NAME]
    moxi rounds mark PROJECT S<n>[,S<n>...] --shown --record PTR [--as NAME]

`ROUND` is `<project>/<slug>` and `PROJECT` a project's key. `PTR` is a pointer: to
where the operator said something, or to where a question came from. It is
`comms:<digits>`, `<session>@<UTC time>`, an `https://` URL or `file:<path>#<anchor>`.
`ITEM` is what a ruling is on: a question's id for its answer, `park:Q<n>`,
`narrow:Q<n>=LIST|none` or `withdraw:Q<n>`; with none the ruling stands on its own.
A ruling is corrected by a later one: `--supersedes` names the earlier ruling of the
same thing that this one replaces, and the item `revoke:C<n>` (`revoke:S<n>` on a
project) takes a ruling back with nothing in its place.
`rule` prints each ruling as it was recorded, for whoever recorded it to show the
operator, and `mark --shown` records, with a pointer to the message, session or page
that showed him, that he saw it; a ruling is named by the round or project that holds
it and its id. On a project, the item `readback=TIME` is his own word that he read the
list of rulings not yet shown through `TIME`, the time that list was headed with.
`ID` is a question on record, `Q<n>` in a round and `P<n>` on a project: `ask` with an
id adds options to it or retires them. `ask --adopt` brings a question of the project's
list into a round, and `ask --move` puts a round's question on its project's list; the
operator's pointer goes with a move that ends what the question holds up, and with a
list of stages for a question that came from a round. A verb that writes takes `--as NAME`, who is
doing it; without it that is the user the command runs as.

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
            "up, or withdraws it. A later ruling corrects an earlier one: with "
            "--supersedes it replaces it, and the ITEM revoke:C<n> takes it back. "
            "The ruling is printed as it was recorded, to show him. On a project, "
            "the ITEM readback=TIME records that he read the list of rulings not yet "
            "shown to him through TIME.")
    parser = verbs.add_parser("rule", help=said, description=said)
    parser.add_argument("target", metavar="ROUND|PROJECT",
                        help="the round the ruling is on, <project>/<slug>, or a "
                             "project's key for a standing ruling with no round")
    parser.add_argument("item", metavar="ITEM", nargs="?",
                        help="what is ruled, when it is a question: Q<n> (P<n> on a "
                             "project) for its answer, given with --text; park:Q<n>; "
                             "narrow:Q<n>=LIST or narrow:Q<n>=none, the stages it "
                             "holds up from then on; withdraw:Q<n>; or revoke:C<n> "
                             "(revoke:S<n> on a project), which takes that ruling back "
                             "and puts nothing in its place; or, on a project, "
                             "readback=TIME, the time the list of rulings he read "
                             "was shown through, as 2026-01-02T03:04:05Z")
    parser.add_argument("--record", metavar="PTR",
                        help="a pointer to where the operator said it, one token: "
                             "comms:<digits>, <session>@<UTC time>, an https:// URL, or "
                             "file:<path in the round's folder>#<anchor>; a ruling "
                             "without one is refused")
    parser.add_argument("--text", metavar="TEXT",
                        help="the ruling, in a sentence or a few that read on their own")
    parser.add_argument("--chose", metavar="K",
                        help="with Q<n>, the key of the option he chose, one the "
                             "question offers now; with no --text the option's line "
                             "is the sentence recorded")
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
    parser.add_argument("--supersedes", metavar="C<n>|S<n>",
                        help="the earlier ruling of the same thing that this one "
                             "replaces: C<n> of the round, S<n> of the project's "
                             "register; a second ruling of a thing is refused "
                             "without it")
    _writes(parser)


def _rule(args, out) -> int:
    from . import ruling

    ruled = ruling.rule(args.target, actor=_actor(args), record=args.record,
                        text=args.text, reason=args.reason, standing=args.standing,
                        scope=args.scope, stages=args.stages, item=args.item,
                        until=args.until, chose=args.chose,
                        supersedes=args.supersedes)
    for line in _recorded(ruled):
        print(line, file=out)
    if ruled.long:
        print(f"{args.prog} {args.verb}: note: {ruled.id} is {len(ruled.text)} "
              f"characters long and is recorded as given; a ruling is meant to be "
              f"short, {ruling.LONG} characters or fewer", file=sys.stderr)
    if ruled.ended is not None:
        print(f"{args.prog} {args.verb}: note: {ruled.name} was {ruled.ended} and "
              f"stays as it ended; {ruled.id} is recorded, and what follows from it "
              "is new work, in a new round", file=sys.stderr)
    _warn_of(ruled.failed, args.prog, args.verb)
    return codes.OK


def _recorded(ruled) -> list[str]:
    """What is printed for a ruling just recorded: its id and its sentence, exactly as
    the log now holds it, or what it rules when it has no sentence; the sentence of
    the question it answers; and what whoever recorded it does next, which is to show
    him the sentence and then mark the ruling as shown. A read-back is his own word
    and is not shown to him."""
    lines = [f"Recorded as {ruled.id}: "
             f"{ruled.event['rules'] if ruled.text is None else ruled.text}"]
    if ruled.read_back is not None:
        return [*lines, f"it covers every ruling of {ruled.holder} recorded at or "
                        f"before {ruled.read_back}; a read-back is his own word, "
                        "and is not itself shown to him"]
    if ruled.answers is not None:
        lines.append(f"in answer to {ruled.answers[0]}: {ruled.answers[1]}")
    return [*lines,
            'show him this under "Recorded as" in your next message, then:',
            f"  moxi rounds mark {ruled.holder} {ruled.id} --shown --record "
            "<pointer to that message>"]


# -- ask ---------------------------------------------------------------------------------

def _declare_ask(verbs) -> None:
    said = ("Put a question to the operator: one sentence, with a pointer to where it "
            "came from. On a round it holds up every stage unless --blocks says "
            "otherwise; on a project, with no round yet, it goes on the project's "
            "list. A question may offer options, two to four, a line each under a "
            "short key. With the id of a question on record in place of the sentence, "
            "add options to it or retire them: a key keeps its line, and a retired key "
            "is never used again. With --adopt, bring a question of the project's list "
            "into the round; with --move, put a round's question on the project's "
            "list, where it holds up nothing.")
    parser = verbs.add_parser("ask", help=said, description=said)
    parser.add_argument("target", metavar="ROUND|PROJECT",
                        help="the round the question is asked in, <project>/<slug>, or "
                             "a project's key")
    parser.add_argument("text", metavar="TEXT|ID", nargs="?",
                        help="the question, one sentence on one line; or the id of a "
                             "question on record, Q<n> or P<n>, to change its options; "
                             "left out with --adopt and --move")
    parser.add_argument("--from", dest="origin", metavar="PTR",
                        help="a pointer to where the question came from, one token: "
                             "comms:<digits>, <session>@<UTC time>, an https:// URL, or "
                             "file:<path in the round's folder>#<anchor>, as "
                             "file:design.md#<anchor> for a section of the design; a "
                             "question with no origin is refused")
    parser.add_argument("--record", metavar="PTR",
                        help="in place of --from, for a question that is the "
                             "operator's own: a pointer to where he asked it; with "
                             "--move, or with --adopt and a list, a pointer to where "
                             "he said so")
    parser.add_argument("--blocks", metavar="LIST|none",
                        help="the stages the question holds up, as B2,B3, or none "
                             "(default: all, every stage the round has or gains); an "
                             "adopted question takes no none")
    parser.add_argument("--option", dest="options", metavar="K=TEXT", action="append",
                        default=[],
                        help="an option the question offers: a short key and its one "
                             "line; give it once for each option, two to four in all")
    parser.add_argument("--retire", metavar="K", action="append", default=[],
                        help="with an ID, take the option under this key out; to "
                             "correct a line, retire its key and add the right line "
                             "under a new one")
    parser.add_argument("--adopt", metavar="P<n>",
                        help="on a round, in place of a sentence: bring this question "
                             "of the project's list into the round, in the state it "
                             "has. It holds up every stage unless --blocks names "
                             "some; for a question that came from a round a list "
                             "needs the operator's pointer, --record PTR")
    parser.add_argument("--move", metavar="ROUND:Q<n>",
                        help="on a project, in place of a sentence: put this question "
                             "of one of its rounds on the project's list. That ends "
                             "what it holds up, so it needs the operator's pointer, "
                             "--record PTR, unless the question is parked and every "
                             "stage it holds up is finished or out of the plan")
    _writes(parser)


def _ask(args, out) -> int:
    from . import asking, questions
    from . import options as choices

    if args.adopt is not None or args.move is not None:
        return _carry(args, out)
    if args.text is None:
        raise BadArgument("nothing is asked",
                          f"give the question as one sentence: {args.prog} ask "
                          f"{args.target} TEXT --from PTR")
    if choices.QUESTION_ID.fullmatch(args.text):
        return _fill(args, out)
    if args.retire:
        raise BadArgument("--retire takes an option out of a question on record",
                          "name the question by its id in place of a sentence: "
                          f"{args.prog} ask {args.target} ID --retire K")
    asked = asking.ask(args.target, args.text, actor=_actor(args), origin=args.origin,
                       record=args.record, blocks=args.blocks, options=args.options)
    print(f"{asked.id}: {asked.text}", file=out)
    print(f"blocks: {questions.said(asked.blocks)}   from: {asked.origin}", file=out)
    for key, line in asked.options:
        print(f"{key}: {line}", file=out)
    _warn_of(asked.failed, args.prog, args.verb)
    return codes.OK


def _fill(args, out) -> int:
    """`ask` with the id of a question on record: change the options it offers."""
    from . import options as choices

    for flag, given in (("--from", args.origin), ("--record", args.record),
                        ("--blocks", args.blocks)):
        if given is not None:
            raise BadArgument(f"{flag} is given when a question is asked, and "
                              f"{args.text} is a question on record",
                              f"leave {flag} out to change its options")
    filled = choices.fill(args.target, args.text, actor=_actor(args),
                          options=args.options, retire=args.retire)
    found = filled.question
    print(f"{found.id}: {found.text}", file=out)
    for key, line in found.options:
        print(f"{key}: {line}", file=out)
    if found.retired:
        print(f"retired: {', '.join(found.retired)}", file=out)
    _warn_of(filled.failed, args.prog, args.verb)
    return codes.OK


def _carry(args, out) -> int:
    """`ask --adopt` and `ask --move`: carry a question on record to the other log of
    its project."""
    from . import moving, questions

    flag = "--adopt" if args.adopt is not None else "--move"
    if args.adopt is not None and args.move is not None:
        raise BadArgument("--adopt brings a question into a round and --move takes "
                          "one out", "give one of the two")
    for other, given in (("the sentence", args.text), ("--from", args.origin),
                         ("--option", args.options or None),
                         ("--retire", args.retire or None),
                         ("--blocks", args.blocks if args.move is not None else None)):
        if given is not None:
            raise BadArgument(f"{flag} carries a question as it is on record, and "
                              f"{other} is not for it", f"leave {other} out")
    if args.adopt is not None:
        moved = moving.adopt(args.target, args.adopt, actor=_actor(args),
                             blocks=args.blocks, record=args.record)
    else:
        moved = moving.move(args.target, args.move, actor=_actor(args),
                            record=args.record)
    found = moved.question
    print(f"{found.id}: {found.text}", file=out)
    print(f"blocks: {questions.said(found.blocks)}   from: {found.origin}", file=out)
    was = (f", where it held up {questions.said(found.held)}"
           if found.held is not None else "")
    print(f"moved from: {moved.source}{was}", file=out)
    _warn_of(moved.failed, args.prog, args.verb)
    return codes.OK


# -- mark --------------------------------------------------------------------------------

def _declare_mark(verbs) -> None:
    said = ("Change a record in a way that is not the operator's ruling: make a "
            "question of a round hold up more stages, reopen a parked one, or say "
            "that rulings were shown to the operator as recorded, with a pointer to "
            "where.")
    parser = verbs.add_parser("mark", help=said, description=said)
    parser.add_argument("target", metavar="ROUND|PROJECT",
                        help="the round, <project>/<slug>; with --shown, the round "
                             "that holds the rulings, or the project's key for "
                             "rulings of its register")
    parser.add_argument("id", metavar="ID",
                        help="what is marked: a question, Q<n>; with --shown, the "
                             "rulings, C<n> of a round or S<n> of a project, "
                             "several separated by commas")
    form = parser.add_mutually_exclusive_group(required=True)
    form.add_argument("--blocks", metavar="LIST|all",
                      help="the stages the question holds up from now on, as B2,B3, "
                           "or all; it must hold up every stage it did before, since "
                           "only the operator narrows it (rule narrow:Q<n>=LIST)")
    form.add_argument("--reopen", action="store_true",
                      help="make a parked question open again")
    form.add_argument("--shown", action="store_true",
                      help="the rulings were put in front of the operator as "
                           "recorded; needs --record")
    parser.add_argument("--record", metavar="PTR",
                        help="with --shown, a pointer to where he was shown them, one "
                             "token: comms:<digits> for the message, <session>@<UTC "
                             "time>, or the https:// URL of a page that listed them; "
                             "never a file, the pointer of one of the rulings, or a "
                             "message from before one of them was said")
    _writes(parser)


def _mark(args, out) -> int:
    from . import marking, questions

    if args.shown:
        return _mark_shown(args, out)
    if args.record is not None:
        raise BadArgument("--record is the pointer to where rulings were shown, and "
                          "a change to a question that anyone may make carries none",
                          "leave --record out")
    if args.reopen:
        marked = marking.reopen(args.target, args.id, actor=_actor(args))
    else:
        marked = marking.widen(args.target, args.id, args.blocks, actor=_actor(args))
    found = marked.question
    print(f"{found.id}: {found.state}   blocks: {questions.said(found.blocks)}",
          file=out)
    _warn_of(marked.failed, args.prog, args.verb)
    return codes.OK


def _mark_shown(args, out) -> int:
    """`mark --shown`: say that rulings were shown to the operator, and where."""
    from . import shown

    marked = shown.mark(args.target, args.id, actor=_actor(args), record=args.record)
    if marked.marked:
        print(f"{marked.name} {', '.join(marked.marked)}: shown   record: "
              f"{marked.record}", file=out)
    if marked.already:
        print(f"{marked.name} {', '.join(marked.already)}: shown already; nothing is "
              f"written{' for it' if marked.marked else ''}", file=out)
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
