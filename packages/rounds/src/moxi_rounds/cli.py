"""`moxi rounds`: the rounds command.

    moxi rounds open ROUND --description TEXT [--flow NAME] [--record PTR] [--lead NAME]
                     [--as NAME]
    moxi rounds fold ROUND [--records-only --record PTR] [--as NAME]
    moxi rounds verdict ROUND REPORT.md [--as NAME]
    moxi rounds commission ROUND STAGE --builder NAME [--record PTR] [--existing]
    moxi rounds commission ROUND --repo KEY --branch NAME --builder NAME
                           [--slice ID=TEXT --minutes N]... [--record PTR] [--existing]
                           [--as NAME]
    moxi rounds check [ROUND STAGE] [--step NAME | --full] [--timeout-s N] [--no-wait]
                      [--as NAME]
    moxi rounds finish [ROUND STAGE] [--again ID] [--as NAME]
    moxi rounds ask ROUND|PROJECT TEXT --from PTR [--blocks LIST|none] [--record PTR]
                    [--option K=TEXT]... [--as NAME]
    moxi rounds ask ROUND TEXT --build B<n> [--blocks LIST|none] [--option K=TEXT]...
                    [--as NAME]
    moxi rounds ask ROUND|PROJECT ID [--option K=TEXT]... [--retire K]... [--as NAME]
    moxi rounds ask ROUND --adopt P<n> [--blocks LIST [--record PTR]] [--as NAME]
    moxi rounds ask PROJECT --move ROUND:Q<n> [--record PTR] [--as NAME]
    moxi rounds rule ROUND|PROJECT [ITEM] --record PTR [--text TEXT] [--chose K]
                     [--reason TEXT] [--standing] [--scope SPEC] [--stages LIST]
                     [--until WHAT] [--supersedes C<n>|S<n>] [--as NAME]
    moxi rounds rule PROJECT readback=TIME --record PTR [--as NAME]
    moxi rounds mark ROUND Q<n> --blocks LIST|all | --reopen [--as NAME]
    moxi rounds mark ROUND --flow NAME [--as NAME]
    moxi rounds mark ROUND --lead NAME [--as NAME]
    moxi rounds mark ROUND B<n> --after LIST [--as NAME]
    moxi rounds mark ROUND B<n> --brief FILE [--as NAME]
    moxi rounds mark ROUND B<n>.L<k> --fixed --commit SHA [--commit SHA]...
                     --no-behaviour-change [--as NAME]
    moxi rounds mark ROUND B<n>.W<k>|B<n>.M<k> --done --record PTR [--as NAME]
    moxi rounds mark ROUND thread:NAME --close|--accept-risk --record PTR [--as NAME]
    moxi rounds mark ROUND C<n>[,C<n>...] --shown --record PTR [--as NAME]
    moxi rounds mark ROUND B<n> --resync [--as NAME]
    moxi rounds mark ROUND REPORT.md --reread [--as NAME]
    moxi rounds mark ROUND --restamp --record PTR [--as NAME]
    moxi rounds mark PROJECT S<n>[,S<n>...] --shown --record PTR [--as NAME]
    moxi rounds drop ROUND --by lead|operator --record PTR [--withdraw-questions]
                     [--as NAME]
    moxi rounds drop ROUND B<n> --by lead|operator --record PTR [--as NAME]
    moxi rounds drop --leftovers [--refs] [--remove] [--json]
    moxi rounds next [ROUND] [--json]
    moxi rounds next --schema design|build|step|majors|retro
    moxi rounds show ROUND [--json]
    moxi rounds list [PROJECT] [--closed] [--json]
    moxi rounds list [PROJECT] --waiting [--json]
    moxi rounds list [PROJECT] --numbers [--json]
    moxi rounds list ROUND --questions [--view docket|ratify] [--json] [--out DIR]
    moxi rounds list PROJECT --questions [--json] [--out DIR]

`ROUND` is `<project>/<slug>` and `PROJECT` a project's key. `PTR` is a pointer: to
where the operator said something, or to where a question came from. It is
`comms:<digits>`, `<session>@<UTC time>`, an `https://` URL, `file:<path>#<anchor>` or
`page:<page>/<act id>@<UTC time>`, his act on a page.
`ITEM` is what a ruling is on: a question's id for its answer, `park:Q<n>`,
`narrow:Q<n>=LIST|none` or `withdraw:Q<n>`; with none the ruling stands on its own.
`skip:STEP`, or `skip:STEP:B<n>` for a step of one stage, says a step is not done; a
skip of the questions step, `skip:docket:B<n>`, lets that stage past the questions that
hold it then, and a question asked or widened onto the stage later holds it. A round
follows the flow its log recorded, whatever the config says since. `mark --flow NAME`
moves it up: to a later tier that has every step it follows, or to the config's
current steps of its own flow when those have every step it follows. Any other move is
the operator's, `flow:NAME`, and a change of flow is corrected by another.
A stage is out of the plan by the item `unstage:B<n>`, while it has no open or finished
build; it does not come back, and the same work planned again takes a new id.
`mark ROUND B<n> --after LIST` makes a stage wait for more stages, which anyone may do,
and the item `unafter:B<n>=B<m>` takes one off. Neither of those two rulings is taken
back. `mark ROUND B<n> --brief FILE` sizes a worker's brief before the worker gets it:
its data block names the stage and one slice the stage declares, given no more than
the project's `slice_minutes`, or one verification (`verify = "build"`, `"design"` or
`"retro"`), or, for a stage that declares no slices and so is one, neither; a pass is
recorded with the brief's SHA-256.
A ruling is corrected by a later one: `--supersedes` names the earlier ruling of the
same thing that this one replaces, and the item `revoke:C<n>` (`revoke:S<n>` on a
project) takes a ruling back with nothing in its place.
`rule` prints each ruling as it was recorded, for whoever recorded it to show the
operator, and `mark --shown` records, with a pointer to the message, session or page
that showed him, that he saw it; a ruling is named by the round or project that holds
it and its id. On a project, the item `readback=TIME` is his own word that he read the
list of rulings not yet shown through `TIME`, the time that list was headed with.
`fold` freezes the round's design: it copies `design.md` in the round's folder to
`versions/design-v<N>.md` and records that version with what the design's data block
declares. A question, a stage, a stage another waits for and a wider hold are added to
the round at once; what the block takes away is recorded as pending and changes
nothing until a verdict or the operator's ruling applies it. Freezing a design again
leaves it with no standing until a re-check reads what changed. `fold --records-only
--record PTR` is the lead's call that the new version changes records only: no pass is
asked for, and the design keeps the standing it had, a design that was sent back
standing at the lead's decision from then on. It is refused when the block's
questions, its stages, what a question holds up or what a stage waits for differ from
the version before. A question since ruled or withdrawn that the block leaves out is
no difference.
`verdict` records the verdict of a report, run by whoever wrote it: it reads the
report's data block and records it against the version of the design the report says
its pass read, whatever was frozen or edited since. A report of a design, of a build,
of a report step of your own, a majors report and a retro report are the kinds
recorded. A verdict that finds its version publishable takes
out of the round what that version's block took away; one that sends the design back
takes out nothing. The design's standing is the newest verdict of its current version,
and `none` after a design is frozen again. One full pass reads a design; a second
report of a full pass is recorded with the flag `extra-full-pass`. A report whose model is in a weaker tier
of the `models` setting than its pass is held to is recorded with the flag
`model-below-tier`. A report of a build is recorded against the head it read, the tip
of the build's branch or an ancestor of it, and not from the build's builder. It lists
every finding the build holds as open, in some state, and the newest report that lists
a finding sets its state: `open`, `fixed` or `no-action`. A finding new to the build
takes an id no earlier report of the stage used. The lead may mark a `minor` or `note`
finding fixed without a re-check, `mark ROUND B<n>.L<k> --fixed --commit SHA...
--no-behaviour-change`, naming every commit of the fix and stating that it changes no
behaviour. A build's verification counts at its tip when its newest report is
`publishable` and every commit since the head it read is named by such a mark or is a
merge of the target that brought no change to the build's paths; otherwise a re-check
of the commits since is owed. Each `[[wait]]` of a build's report, work that waits on
the operator, is an open waiting item of the round, `B<n>.W<k>`, from the moment the
report is recorded. It holds the round's close and is listed by `list --waiting` until
`mark ROUND B<n>.W<k> --done --record PTR` names what shows it done, PTR a pointer
or the id of a commit the build's repo holds. A later report of the same build may list
the item again with the same sentence; it is the same item. Each
`[[decision]]`, an as-built choice the operator would notice, is a record of the round,
`B<n>.M<k>`, with the options `accept` and `reject`, until he rules it with `rule ROUND
B<n>.M<k>=accept|reject --record PTR`; a later report may list it again as heavy or
heavier, never lighter. A rejected decision owes a fix, a waiting item `B<n>.M<k>`
until `mark ... --done --record PTR` names it. A report of `kind = "majors"`, by a
checker who built none of the stages it names, declares the decisions no build report
carries.
The lead repairs a record by deriving it again, never by giving a value: `mark ROUND
B<n> --resync` moves the base of the stage's open build to the newest commit of it the
target still has, only after the target's history was rewritten under it; `mark ROUND
REPORT.md --reread` reads a recorded report again at the bytes its verdict recorded
and adds the findings, waiting items and decisions its record lacks, and is refused
when it would take away or change anything. Neither names a ruling, a skip or a
verdict word. `mark ROUND --restamp --record PTR` re-stamps the design's current
version with the digest of its frozen copy, when an edit outside the round rewrote
the copy and `design.md` alike; `--record` names where the lead said what edited them.
It changes no standing, and is the one repair an ended round takes.
`commission` authorizes the build of a stage of the plan once its step waits for
nothing: it reads the target (the repo's `into` setting, else the branch checked out
at the project's root) and takes the target's head as the build's base, cuts the
stage's branch from it and adds its worktree under `worktree_dir` as
`<repo>/<branch>`, makes its scratch folder in rounds' cache directory, writes a
marker naming the build in each, and records the build. A `/` or `%` in a branch is
written `%2F` or `%25` in a path. `--existing` builds on a branch that is there: one
strictly inside or around another open build's branch is taken, and `finish` orders the
two; one that overlaps another open build's, or that came onto the target by another
build's finish, is refused. A repo's branch has one
open build at a time, across every round. In a flow that does not verify its design,
a round builds one stage in a repo, and `commission ROUND --repo KEY --branch NAME`
declares the stage as it commissions it, under the lowest `B<n>` the round has not
used, with the slices `--slice ID=TEXT --minutes N` give; it is refused while the repo
has a stage with no open or finished build, which is commissioned by its id, and
while a question open on every stage would hold the new one. A stage whose build was
dropped is commissioned again under its id. A commission stopped before
it recorded the build is run again: it keeps the worktree whose marker names its own
stopped run, and refuses any other folder at either place, naming it. It prints the
commission block, which goes first in the builder's brief: the build's folders, the
commands the builder runs, who finishes and after what, what the build step reads, the
rulings the design cites for the stage as they stand now, the slices, the operator's
skips and the repo's `brief` lines. Then it tells the lead which of those rulings the
operator has not been shown, with the `mark --shown` line, and which standing rulings
name the repo in their scope and are cited by no row. `next` prints, with a stage whose
build is open, each ruling recorded since its block that reaches the stage.
`check` runs the repo's declared check for a build at its tip and records the run: in
the build's worktree, which must be clean with the build's branch checked out, on a
build that has work, with no shell, stdin closed, no `GIT_` variable, its output in
`check-<n>.log` in the build's scratch folder, and its whole process tree ended at the
check's `timeout_s` (or `--timeout-s`, else 3600 s): SIGINT, then SIGKILL 30 s later.
One check runs at a time on the machine: a run waits for the check lock another holds,
or with `--no-wait` is refused. The run is recorded with its commit, that
commit's merge-base with the target, the argv as it ran, its exit code and its
duration; when the tip moved or the worktree changed while it ran, nothing is
recorded. Run with no round in a build's worktree, it checks that build. A check
counts for a gate only at the tip being finished, with the command the repo declares
then.
`finish` finishes a build at its branch's tip once every gate of finish holds there:
it takes the repo's finish lock (a ref, `refs/rounds/finishing/<into>` or the repo's
`lock_ref`), reads the gates the target's moves can change again, runs the repo's
finish actions in order, each recorded under its id before the next starts, and records
the finish when the gates read on the log still hold. The merge action merges the tip,
by its commit id, into the target with a merge commit. A failed action is recorded and
undoes nothing; a conflict is aborted and refused with the way through. After a merge
it removes the build's worktree, scratch folder and branch where rounds made them and
nothing would be lost, and with no merge only the scratch folder. Run with no round in
a build's worktree, it finishes that build.
`mark ROUND thread:NAME --close` or `--accept-risk`, with `--record` naming where the
lead wrote why, is the lead's decision on a thread of the design's verification, made
once the last two verdicts each sent the design back and it was frozen since: no
further pass is run, and the design stands at `lead-decision`.
No verb closes a round. A round closes itself on the write that leaves its close
nothing to wait for: in a flow with no `build` step, every step done or skipped, no
question open or parked, and no stage left in its plan. `show` says what the close
of an open round waits for. A round whose flow has a `build` step closes once every
stage is finished, no build is open, and every major decision of a finished build is
ruled, unless `ratify` is skipped or off; in a flow with a `retro` step, it also
waits for the retro report.
`drop` ends a round short of its work: the operator drops any round, and the lead one
that holds none of the operator's acts. Its open and parked questions go to the
project's list, where they hold up nothing, and `open` of a later round of the project
prints them, to be adopted by the round that takes the work up. A round that was dropped
or closed takes no new question, no change to a question and no new ruling; a
correction of one of its rulings is still recorded, and `mark --shown` still marks
its rulings. The builds still open end with it, each branch's tip recorded.
`drop ROUND B<n>` drops the open build of one stage: the lead's only when git reads
it `gone`, or `empty` with no commit of its own, sharing with no other build; any
other is the operator's. The stage stays owed. A drop deletes no branch; it removes
the folders of the builds it ends where the removal rule proves they may go, and
`drop --leftovers` sweeps what that kept once it may: a dry run until `sweep.armed` is
true, and with `--remove` the removal. `rule ROUND credit:B<n>` is the operator's word that what the target
gained under the head of a build `unclear` by a fast-forward is that build's work:
it then reads `merged`, owing its check, verification and ratification, until
`revoke:` takes the credit back.
A builder with a question for the operator tells its lead, and the lead asks it with
`--build B<n>` in place of `--from`: its origin is that stage's open build at its
branch's tip, and every listing says the lead asked it for the build's builder.
`ID` is a question on record, `Q<n>` in a round and `P<n>` on a project: `ask` with an
id adds options to it or retires them. `ask --adopt` brings a question of the project's
list into a round, and `ask --move` puts a round's question on its project's list; the
operator's pointer goes with a move that ends what the question holds up, and with a
list of stages for a question that came from a round; a move or an adoption with
his pointer is a ruling, and is printed as `rule` prints one. `list --questions` writes out
the questions that wait on the operator, of one round or of one project with its open
rounds, and after them the rulings not yet shown to him: as markdown, with `--json` as
data, and with `--out DIR` as both, in the files `questions.md` and `questions.json`.
A round's questions have two views, both with no `--view`: the docket, its open and
parked questions; and the ratification, the decisions not yet ruled of its finished
builds, which it names, as the project's `ratify` setting picks them (`majors`, or
`majors+minors` with the minor ones as one group of ids, or `off`), with the major
decisions of unfinished builds listed as later. A decision that forecloses a
capability is always an entry of its own.
`next` says what is done next in a round: the step it is at, who does it and at which
tier, with the model the `models` setting names for that tier, what they read, the
command that completes the step and what the step waits for; then what the lead
should know and nothing waits for. For the step that verifies the design it says
which pass is asked for, a full pass or a re-check with the two frozen files to
compare, or what is done in place of a pass; for the questions step, the command that
writes the questions out and the `rule` line of each open one. A step this version
does not have is printed as not built. `next --schema KIND` prints the empty data
block of a report of that kind, a comment on each key. Run with no round in a
build's worktree, `next` finds the open build on the branch checked out there and
prints its round's next step. `next` records nothing.
`show` shows one round: what it is for, its flow, the step it is at, what its close
waits for, its stages and builds with their checks, verdicts, findings, waiting items
and decisions, its questions by state, its rulings and whether each was shown to him,
its history and, once it has ended, its numbers. `list` with no flag lists the open
rounds, each with its flow, its step and its counts, and with `--closed` the rounds
that have ended; `list --waiting` lists what waits on the operator: the open and the
parked questions, the open waiting items, the unruled major decisions, the builds to
look at and the rulings not yet shown to him; `list --numbers` sums the numbers of
the closed rounds. Each is of every project, or of the
one named, and `--json` gives it as data. `show` and `list` record nothing. A verb
that writes takes `--as NAME`, who is doing it; without it that is the user the
command runs as.

Exit codes: 0 done; 1 an error; 2 bad arguments or a bad setting; 3 refused. A refusal
says what stopped the verb and the command that fixes it, and has written nothing.

Each verb is three things here: a function that declares its arguments on the verb
group (`_declare_<verb>`, listed in `_DECLARE`), a function that runs it as
`fn(args, out) -> int` (listed in `_VERBS`), and the module that does its work, which
the second calls.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from collections.abc import Callable, Sequence

from moxi import exit as codes
from moxi.config import ConfigError

from .errors import BadArgument, Refused, RoundsError, Unmet


# -- what every writing verb shares ------------------------------------------------------

def _writes(parser: argparse.ArgumentParser) -> None:
    """Give a writing verb the option that says who is doing it."""
    parser.add_argument("--as", dest="actor", metavar="NAME",
                        help="who is doing this, kept with what is recorded (default: "
                             "the user the command runs as)")


def _actor(args) -> str:
    """Who the verb is done by: the name given with `--as`, else the OS user, as
    `actors.default` gives it."""
    from . import actors

    if args.actor is not None:
        if not args.actor.strip():
            raise BadArgument("--as names nobody", "give a name: --as NAME")
        return args.actor
    try:
        return actors.default()
    except BadArgument:
        raise BadArgument("the user this command runs as has no name",
                          "say who is doing this with --as NAME") from None


def _warn_of(failed, prog: str, verb: str) -> None:
    """Say which work after the record failed. What was recorded stands, and that work
    is redone by the next verb that writes, so the verb itself is done."""
    for _callback, error in failed:
        print(f"{prog} {verb}: warning: recorded, but what is kept from the record was "
              f"not brought up to date ({error}); the next command that writes does it "
              "again", file=sys.stderr)


def _say_closed(writes, out) -> None:
    """Say, in a line, each round a write of the verb closed, then a line for each
    decision of a finished build it left open, with whether the project's `ratify`
    setting puts it to him. No verb closes a round, so the verb's own lines never say
    it: whichever write left the close nothing to wait for did."""
    from . import closing, decisions
    from . import settings as own
    from . import state as states

    for written in writes:
        if written.closed is None:
            continue
        name = f"{written.where.project}/{written.where.slug}"
        print(f"closed {name}: nothing is left for its close to wait for", file=out)
        try:
            setting = closing.ratify_of(own.load(), name)
        except ConfigError:
            # The close read it a moment ago; what it leaves open is in show.
            continue
        for one in decisions.left_open(states.fold(written.round_events), setting):
            print(f"  {decisions.left_open_line(one)}", file=out)


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
    parser.add_argument("--inherits", metavar="LIST",
                        help="the standing rulings of the project the round starts "
                             "from, as S1,S2: each one that says what is built and "
                             "still stands")
    parser.add_argument("--lead", metavar="NAME",
                        help="the agent who leads the round: letters, digits, . _ -, "
                             "and at most one @scope (default: none of its own, so the "
                             "round reads its project's lead)")
    _writes(parser)


def _open(args, out) -> int:
    from . import opening

    opened = opening.open_round(args.round, description=args.description,
                                by=_actor(args), flow=args.flow, record=args.record,
                                inherits=_listed(args.inherits), lead=args.lead)
    steps = ", ".join(step["step"] for step in opened.steps) or "no steps"
    print(f"opened {opened.name}   flow {opened.flow} ({steps})", file=out)
    print(f"folder: {opened.round_dir}", file=out)
    if args.lead is not None:
        print(f"lead: {args.lead}", file=out)
    if opened.inherits:
        print(f"inherits: {', '.join(opened.inherits)}", file=out)
    for line in _left(opened):
        print(line, file=out)
    _warn_of(opened.failed, args.prog, args.verb)
    return codes.OK


def _listed(text: str | None) -> list[str] | None:
    """The ids of a list given as one argument, `S1,S2`; None when none was given."""
    if text is None:
        return None
    return [one.strip() for one in text.split(",") if one.strip()]


def _left(opened) -> list[str]:
    """What an opening prints of the questions a dropped round left on the project's
    list: each with where it came from, who asked it and when, and the command that
    brings one into the round just opened."""
    from . import questions

    if not opened.left:
        return []
    project = opened.name.partition("/")[0]
    lines = ["", f"questions on the list of {project} that came from a dropped round; "
                 "adopt the ones whose work this round takes up:",
             f"  moxi rounds ask {opened.name} --adopt P<n>"]
    for one in opened.left:
        found = one.question
        said = [f"from: {found.origin}", f"blocks: {questions.said(found.blocks)}",
                f"state: {one.state}", f"by: {questions.asker(found.by, found.builder)}",
                f"at: {found.at}"]
        if one.state == questions.PARKED and found.until is not None:
            said.append(f"until: {found.until}")
        said.append(f"moved from: {found.moved_from}")
        if found.held is not None:
            said.append(f"held up there: {questions.said(found.held)}")
        lines += [f"  {found.id}: {found.text}", f"    {'   '.join(said)}"]
    return lines


# -- rule --------------------------------------------------------------------------------

def _declare_rule(verbs) -> None:
    said = ("Record a ruling of the operator's: one plain sentence, with a pointer to "
            "where he said it. On a round it is the round's own; with --standing, or on "
            "a project, it goes in the project's register and outlasts the round. With "
            "an ITEM it rules a question: answers it, parks it, narrows what it holds "
            "up, or withdraws it. A later ruling corrects an earlier one: with "
            "--supersedes it replaces it, and the ITEM revoke:C<n> takes it back. "
            "The ITEM skip:STEP says a step is not done, skip-action:B<n>=ID that "
            "finish does not run one finish action for a stage and treats it as done, "
            "and flow:NAME moves the round to another flow. credit:B<n> credits to "
            "a build what its target gained by a fast-forward. unstage:B<n> takes a "
            "stage out of the plan, and "
            "unafter:B<n>=B<m> takes one stage off what another waits for. "
            "B<n>.M<k>=accept or B<n>.M<k>=reject rules a decision a build's report "
            "declared; a rejected one is a waiting item until its fix is marked done. "
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
                             "and puts nothing in its place; skip:STEP for a "
                             "step of the round (verify-design, ratify, retro) or "
                             "skip:STEP:B<n> for a step of one stage (docket, "
                             "check, verify-build, a step of your own); "
                             "skip-action:B<n>=ID, the finish action ID that finish "
                             "does not run for B<n> and treats as done; flow:NAME, "
                             "the flow the round follows from then on; "
                             "credit:B<n>, the head of B<n>'s open build credited "
                             "as its work, for a build unclear because its target "
                             "was fast-forwarded with no reading; "
                             "unstage:B<n>, a stage with no open or finished build "
                             "that leaves the plan for good; unafter:B<n>=B<m>, a "
                             "stage B<n> that waits for B<m> no longer; or, on a "
                             "project, "
                             "readback=TIME, the time the list of rulings he read "
                             "was shown through, as 2026-01-02T03:04:05Z")
    parser.add_argument("--record", metavar="PTR",
                        help="a pointer to where the operator said it, one token: "
                             "comms:<digits>, <session>@<UTC time>, an https:// URL, "
                             "file:<path in the round's folder>#<anchor>, or "
                             "page:<page>/<act id>@<UTC time>, his act on a page; a "
                             "ruling without one is refused")
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

    ruled = ruling.rule(args.target, by=_actor(args), record=args.record,
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
    from . import stages

    lines = [f"Recorded as {ruled.id}: "
             f"{ruled.event['rules'] if ruled.text is None else ruled.text}"]
    if ruled.read_back is not None:
        return [*lines, f"it covers every ruling of {ruled.holder} recorded at or "
                        f"before {ruled.read_back}; a read-back is his own word, "
                        "and is not itself shown to him"]
    if ruled.answers is not None:
        lines.append(f"in answer to {ruled.answers[0]}: {ruled.answers[1]}")
    if "covers" in ruled.event:
        stage = ruled.event["rules"].rpartition(":")[2]
        past = ", ".join(ruled.event["covers"]) or "no question, since none holds it"
        lines.append(f"it lets {stage} past {past}; a question asked later, or made "
                     f"to hold up {stage} later, holds it")
    if "steps" in ruled.event:
        steps = ", ".join(str(step.get("step")) for step in ruled.event["steps"])
        lines.append(f"{ruled.name} follows the flow {ruled.event['flow']} "
                     f"({steps or 'no steps'}) from now on; it followed "
                     f"{ruled.event.get('was')}")
    rules = ruled.event.get("rules") or ""
    if rules.startswith(stages.UNSTAGE):
        lines.append(f"{rules[len(stages.UNSTAGE):]} left the plan of {ruled.name} "
                     "and does not come back; the same work planned again takes a "
                     "new id")
    if rules.startswith(stages.UNAFTER):
        stage, _equals, waited = rules[len(stages.UNAFTER):].partition("=")
        lines.append(f"{stage} waits for {waited} no longer; anyone adds the wait "
                     f"again: moxi rounds mark {ruled.name} {stage} --after {waited}")
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
                             "comms:<digits>, <session>@<UTC time>, an https:// URL, "
                             "page:<page>/<act id>@<UTC time>, or file:<path in the "
                             "round's folder>#<anchor>, as file:design.md#<anchor> for "
                             "a section of the design; a question with no origin is "
                             "refused")
    parser.add_argument("--record", metavar="PTR",
                        help="in place of --from, for a question that is the "
                             "operator's own: a pointer to where he asked it; with "
                             "--move, or with --adopt and a list, a pointer to where "
                             "he said so")
    parser.add_argument("--build", metavar="B<n>",
                        help="in place of --from, for a question that came up in the "
                             "open build of this stage: its builder told you, and you "
                             "ask it; it is recorded as from that build at its branch's "
                             "tip now, asked by you for the builder")
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
    asking.not_a_decision(args.text, args.target)
    if args.retire:
        raise BadArgument("--retire takes an option out of a question on record",
                          "name the question by its id in place of a sentence: "
                          f"{args.prog} ask {args.target} ID --retire K")
    asked = asking.ask(args.target, args.text, by=_actor(args), origin=args.origin,
                       record=args.record, build=args.build, blocks=args.blocks,
                       options=args.options)
    print(f"{asked.id}: {asked.text}", file=out)
    print(f"blocks: {questions.said(asked.blocks)}   from: {asked.origin}", file=out)
    if asked.builder is not None:
        print(f"by: {questions.asker(asked.by, asked.builder)}", file=out)
    for key, line in asked.options:
        print(f"{key}: {line}", file=out)
    _warn_of(asked.failed, args.prog, args.verb)
    return codes.OK


def _fill(args, out) -> int:
    """`ask` with the id of a question on record: change the options it offers."""
    from . import options as choices

    for flag, given in (("--from", args.origin), ("--record", args.record),
                        ("--build", args.build), ("--blocks", args.blocks)):
        if given is not None:
            raise BadArgument(f"{flag} is given when a question is asked, and "
                              f"{args.text} is a question on record",
                              f"leave {flag} out to change its options")
    filled = choices.fill(args.target, args.text, by=_actor(args),
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
    from . import moving, questions, ruling

    flag = "--adopt" if args.adopt is not None else "--move"
    if args.adopt is not None and args.move is not None:
        raise BadArgument("--adopt brings a question into a round and --move takes "
                          "one out", "give one of the two")
    for other, given in (("the sentence", args.text), ("--from", args.origin),
                         ("--build", args.build),
                         ("--option", args.options or None),
                         ("--retire", args.retire or None),
                         ("--blocks", args.blocks if args.move is not None else None)):
        if given is not None:
            raise BadArgument(f"{flag} carries a question as it is on record, and "
                              f"{other} is not for it", f"leave {other} out")
    if args.adopt is not None:
        moved = moving.adopt(args.target, args.adopt, by=_actor(args),
                             blocks=args.blocks, record=args.record)
    else:
        moved = moving.move(args.target, args.move, by=_actor(args),
                            record=args.record)
    found = moved.question
    print(f"{found.id}: {found.text}", file=out)
    print(f"blocks: {questions.said(found.blocks)}   from: {found.origin}", file=out)
    was = (f", where it held up {questions.said(found.held)}"
           if found.held is not None else "")
    print(f"moved from: {moved.source}{was}", file=out)
    if moved.ruling is not None:
        # With his pointer the move is his ruling, in the log the question left: it
        # is printed as `rule` prints one, since its id is what a mark names.
        left = moved.events[-1]
        adopted = args.adopt is not None
        ruled = ruling.Ruled(
            id=moved.ruling, standing=adopted, text=left.get("text"), event=left,
            name=moved.name if adopted else moved.source.rpartition(":")[0])
        for line in _recorded(ruled):
            print(line, file=out)
    _warn_of(moved.failed, args.prog, args.verb)
    return codes.OK


# -- mark --------------------------------------------------------------------------------

def _declare_mark(verbs) -> None:
    said = ("Change a record in a way that is not the operator's ruling: make a "
            "question of a round hold up more stages, reopen a parked one, make a "
            "stage wait for more stages, mark a minor finding of a build fixed, mark "
            "a waiting item done, move a round up to a heavier flow, decide a thread of the design's "
            "verification, size a worker's brief, say that rulings were shown to the "
            "operator as recorded, with a pointer to where, or repair a record: "
            "read a build's base again from git, a recorded report again, or the "
            "frozen copy of the design's current version for its digest.")
    parser = verbs.add_parser("mark", help=said, description=said)
    parser.add_argument("target", metavar="ROUND|PROJECT",
                        help="the round, <project>/<slug>; with --shown, the round "
                             "that holds the rulings, or the project's key for "
                             "rulings of its register")
    parser.add_argument("id", metavar="ID", nargs="?",
                        help="what is marked: a question, Q<n>; with --after, a "
                             "stage, B<n>; with --fixed, a build's finding, "
                             "B<n>.L<k>; with --done, a waiting item, "
                             "B<n>.W<k>, or a rejected decision's fix, "
                             "B<n>.M<k>; with --close or --accept-risk, a thread "
                             "of the design's verification, thread:NAME, the name "
                             "in letters, digits and . _ -; with --shown, the "
                             "rulings, C<n> of a round or S<n> of a project, "
                             "several separated by commas; with --resync, a stage, "
                             "B<n>; with --brief, the stage the brief is of, B<n>; "
                             "with --reread, a recorded report's file; left "
                             "out with --flow, --lead and --restamp, which mark the "
                             "round")
    form = parser.add_mutually_exclusive_group(required=True)
    form.add_argument("--blocks", metavar="LIST|all",
                      help="the stages the question holds up from now on, as B2,B3, "
                           "or all; it must hold up every stage it did before, since "
                           "only the operator narrows it (rule narrow:Q<n>=LIST)")
    form.add_argument("--reopen", action="store_true",
                      help="make a parked question open again")
    form.add_argument("--after", metavar="LIST",
                      help="the stages the stage B<n> waits for from now on, as "
                           "B1,B3, beside the ones it waits for already; each must "
                           "be a stage of the round, and none may wait for B<n>; "
                           "only the operator takes one off (rule "
                           "unafter:B<n>=B<m>)")
    form.add_argument("--brief", metavar="FILE",
                      help="size a worker's brief for the stage B<n> before the "
                           "worker gets it: its data block names the stage and one "
                           "slice the stage declares, within the slice_minutes "
                           "setting, or one verification (verify = build, design "
                           "or retro), or neither for a stage that declares no "
                           "slices; a pass is recorded with the brief's SHA-256")
    form.add_argument("--fixed", action="store_true",
                      help="the lead's mark that a minor or note finding of a build "
                           "is fixed, with no re-check; needs every commit of the fix "
                           "as --commit and --no-behaviour-change")
    form.add_argument("--done", action="store_true",
                      help="a waiting item is done: the work a build's report said "
                           "waits on the operator; needs --record, a pointer or "
                           "the id of a commit the build's repo holds")
    form.add_argument("--flow", metavar="NAME",
                      help="move the round up to this flow: one later in the tiers "
                           "that has every step the round follows, or the round's "
                           "own flow, to take the steps the config has for it now "
                           "when they have every step it follows; any other move "
                           "is the operator's (rule flow:NAME)")
    form.add_argument("--lead", metavar="NAME",
                      help="the agent who leads the round from now on: letters, "
                           "digits, . _ -, and at most one @scope; anyone may name "
                           "it, and it carries no pointer")
    form.add_argument("--close", action="store_true",
                      help="the lead's decision that the thread thread:NAME is "
                           "closed: no further pass is run on it; only after the "
                           "last two verdicts each sent the design back and it was "
                           "frozen since; needs --record")
    form.add_argument("--accept-risk", action="store_true",
                      help="the lead's decision that what is left of the thread "
                           "thread:NAME is a risk taken; as --close otherwise")
    form.add_argument("--shown", action="store_true",
                      help="the rulings were put in front of the operator as "
                           "recorded; needs --record")
    form.add_argument("--resync", action="store_true",
                      help="read git again for the base of the open build of the "
                           "stage B<n>: when the target's history was rewritten so "
                           "the base is no longer on it, move the base to the newest "
                           "commit of it the target still has; else change nothing "
                           "and say so")
    form.add_argument("--reread", action="store_true",
                      help="parse the recorded report REPORT.md again, at the bytes "
                           "its verdict recorded, and add to that verdict's record "
                           "the findings, waiting items and decisions its block "
                           "holds and the record lacks; refused when it would take "
                           "away or change anything")
    form.add_argument("--restamp", action="store_true",
                      help="re-stamp the design's current version: when design.md "
                           "and its frozen copy hold the same bytes and neither holds "
                           "the digest recorded at its freeze, record the copy's "
                           "digest as the version's; needs --record; changes no "
                           "standing, and an ended round takes it")
    parser.add_argument("--commit", metavar="SHA", action="append", default=[],
                        help="with --fixed, a commit of the fix, on the build's "
                             "branch; once for each")
    parser.add_argument("--no-behaviour-change", action="store_true",
                        help="with --fixed, the lead's statement that the fix "
                             "changes nothing a command, hook or page reads, "
                             "decides, writes, refuses or accepts; a fix that does "
                             "gets a re-check of its diff")
    parser.add_argument("--record", metavar="PTR",
                        help="with --done, a pointer to the report, commit or "
                             "message that shows the waiting item done; "
                             "with --close or --accept-risk, a pointer to where the "
                             "lead wrote why, one token: comms:<digits>, "
                             "<session>@<UTC time>, an https:// URL, file:<path "
                             "in the round's folder>#<anchor>, or page:<page>/<act "
                             "id>@<UTC time>; "
                             "with --restamp, a pointer to where the lead named what "
                             "edited the design, of those shapes; "
                             "with --shown, a pointer to where he was shown them, one "
                             "token: comms:<digits> for the message, <session>@<UTC "
                             "time>, the https:// URL of a page that listed them, or "
                             "page:<page>/<act id>@<UTC time> for his act on a page "
                             "that showed them; never a file, the pointer of one of "
                             "the rulings, a message from before one of them was "
                             "said, or a page's act from before one was recorded")
    _writes(parser)


def _mark(args, out) -> int:
    from . import marking, questions

    if args.flow is not None:
        return _mark_flow(args, out)
    if args.lead is not None:
        return _mark_lead(args, out)
    if args.restamp:
        return _mark_restamp(args, out)
    if args.id is None:
        raise BadArgument("nothing says what is marked",
                          "name the question, Q<n>, with --after the stage, B<n>, "
                          "with --fixed the finding, B<n>.L<k>, with --done the "
                          "waiting item, B<n>.W<k>, or with --shown the rulings")
    if not args.fixed and (args.commit or args.no_behaviour_change):
        raise BadArgument("--commit and --no-behaviour-change go with --fixed, the "
                          "lead's mark of a finding", "leave them out, or mark the "
                          "finding with --fixed")
    if args.resync or args.reread:
        return _mark_repair(args, out)
    if args.fixed:
        return _mark_fixed(args, out)
    if args.done:
        return _mark_done(args, out)
    if args.shown:
        return _mark_shown(args, out)
    if args.close or args.accept_risk:
        return _mark_thread(args, out)
    if args.after is not None:
        return _mark_after(args, out)
    if args.brief is not None:
        return _mark_brief(args, out)
    if args.record is not None:
        raise BadArgument("--record is the pointer to where rulings were shown, and "
                          "a change to a question that anyone may make carries none",
                          "leave --record out")
    if args.reopen:
        marked = marking.reopen(args.target, args.id, by=_actor(args))
    else:
        marked = marking.widen(args.target, args.id, args.blocks, by=_actor(args))
    found = marked.question
    print(f"{found.id}: {found.state}   blocks: {questions.said(found.blocks)}",
          file=out)
    _warn_of(marked.failed, args.prog, args.verb)
    return codes.OK


def _mark_repair(args, out) -> int:
    """`mark B<n> --resync` and `mark REPORT.md --reread`: the lead's repairs."""
    from . import repairs

    if args.record is not None:
        raise BadArgument("a repair derives a record again from git or from a report, "
                          "and carries no pointer", "leave --record out")
    if args.resync:
        done = repairs.resync(args.target, args.id, by=_actor(args))
    else:
        done = repairs.reread(args.target, args.id, by=_actor(args))
    print(f"{done.name}: {done.said}", file=out)
    _warn_of(done.failed, args.prog, args.verb)
    return codes.OK


def _mark_restamp(args, out) -> int:
    """`mark ROUND --restamp`: the lead's re-stamp of the design's current version."""
    from . import repairs

    if args.id is not None:
        raise BadArgument("--restamp re-stamps the round's current version of its "
                          "design, and names nothing else", "leave the ID out")
    done = repairs.restamp(args.target, record=args.record, by=_actor(args))
    print(f"{done.name}: {done.said}", file=out)
    _warn_of(done.failed, args.prog, args.verb)
    return codes.OK


def _mark_fixed(args, out) -> int:
    """`mark B<n>.L<k> --fixed`: the lead's mark of a minor finding fixed."""
    from . import fixes

    if args.record is not None:
        raise BadArgument("the lead's mark of a finding carries no pointer: it names "
                          "the commits of the fix", "leave --record out")
    done = fixes.fix(args.target, args.id, args.commit,
                     no_behaviour_change=args.no_behaviour_change, by=_actor(args))
    found = done.finding
    already = (f"   (fixed already by the mark on line {done.event['n']}: nothing was "
               "written)" if done.found else "")
    print(f"finding {found.key}: {found.severity}, {found.state}   commits: "
          f"{', '.join(one[:12] for one in done.event['commits'])}{already}", file=out)
    _warn_of(done.failed, args.prog, args.verb)
    return codes.OK


def _mark_done(args, out) -> int:
    """`mark B<n>.W<k> --done`: a waiting item is done."""
    from . import waits

    done = waits.done(args.target, args.id, record=args.record, by=_actor(args))
    one = done.wait
    already = (f"   (done already, {one.done_at} by {one.done_by}: nothing was written)"
               if done.found else "")
    print(f"{done.name} {one.key}: {one.state}   record: {one.record}{already}",
          file=out)
    _warn_of(done.failed, args.prog, args.verb)
    return codes.OK


def _mark_thread(args, out) -> int:
    """`mark thread:NAME`: the lead's decision on a thread of the design's
    verification."""
    from . import brake

    if not args.id.startswith(brake.PREFIX):
        raise BadArgument(f"{args.id!r} names no thread",
                          f"name the thread as {brake.PREFIX}NAME, the name in "
                          "letters, digits and . _ -")
    done = brake.decide(args.target, args.id[len(brake.PREFIX):],
                        brake.CLOSE if args.close else brake.ACCEPT_RISK,
                        record=args.record, by=_actor(args))
    one = done.decision
    already = (f"   (recorded already, {one.at} by {one.actor}: nothing was written)"
               if done.found else "")
    print(f"{done.name} {brake.PREFIX}{one.thread}: {one.decision}   record: "
          f"{one.record}{already}", file=out)
    print(f"standing: {done.standing}", file=out)
    _warn_of(done.failed, args.prog, args.verb)
    return codes.OK


def _mark_after(args, out) -> int:
    """`mark --after`: make a stage wait for more stages, which anyone may do."""
    from . import stages

    if args.record is not None:
        raise BadArgument("--record is a pointer to the operator's word, and a wait "
                          "added to a stage is anyone's and carries none",
                          "leave --record out")
    marked = stages.wait(args.target, args.id, args.after, by=_actor(args))
    print(f"{marked.stage.id}   after: {', '.join(marked.stage.after)}   added: "
          f"{', '.join(marked.added)}", file=out)
    _warn_of(marked.failed, args.prog, args.verb)
    return codes.OK


def _mark_brief(args, out) -> int:
    """`mark B<n> --brief FILE`: size a worker's brief before the worker gets it."""
    from . import sizing

    if args.record is not None:
        raise BadArgument("a sized brief is the lead's check of a file, and carries no "
                          "pointer", "leave --record out")
    done = sizing.size(args.target, args.id, args.brief, by=_actor(args))
    one = done.sized
    within = ("" if done.limit is None
              else f" ({done.minutes} of {done.limit} minutes)")
    print(f"{done.name} {one.stage} {one.piece}{within}: sized   brief: {one.brief}   "
          f"sha256: {one.sha256[:12]}", file=out)
    if done.found:
        print(f"sized already, {one.at} by {one.actor}: nothing was written", file=out)
    _warn_of(done.failed, args.prog, args.verb)
    return codes.OK


def _mark_flow(args, out) -> int:
    """`mark --flow`: move a round up to a heavier flow, which is the lead's to do."""
    from . import stepping

    if args.id is not None:
        raise BadArgument("--flow moves the round itself and names nothing in it",
                          f"leave {args.id} out: {args.prog} mark ROUND --flow NAME")
    if args.record is not None:
        raise BadArgument("--record is a pointer to the operator's word, and a step "
                          "up is the lead's and carries none", "leave --record out")
    stepped = stepping.step_up(args.target, args.flow, by=_actor(args))
    steps = ", ".join(str(step.get("step")) for step in stepped.steps) or "no steps"
    print(f"{stepped.name}   flow {stepped.flow} ({steps})   was: {stepped.was}",
          file=out)
    _warn_of(stepped.failed, args.prog, args.verb)
    return codes.OK


def _mark_lead(args, out) -> int:
    """`mark --lead`: name the agent who leads the round, which anyone may do."""
    from . import leads

    again = f"{args.prog} mark ROUND --lead NAME"
    if args.id is not None:
        raise BadArgument("--lead marks the round, and takes no ID", again)
    if args.record is not None:
        raise BadArgument("--record is a pointer to the operator's word, and a round's "
                          "lead is the leads' record and carries none",
                          "leave --record out")
    led = leads.mark(args.target, args.lead, by=_actor(args))
    if led.found:
        print(f"{led.lead} leads {led.name} already; nothing was written", file=out)
    else:
        print(f"{led.name}   lead: {led.lead}   was: {led.was or 'none'}", file=out)
    _warn_of(led.failed, args.prog, args.verb)
    return codes.OK


def _mark_shown(args, out) -> int:
    """`mark --shown`: say that rulings were shown to the operator, and where."""
    from . import shown

    marked = shown.mark(args.target, args.id, by=_actor(args), record=args.record)
    if marked.marked:
        print(f"{marked.name} {', '.join(marked.marked)}: shown   record: "
              f"{marked.record}", file=out)
    if marked.already:
        print(f"{marked.name} {', '.join(marked.already)}: shown already; nothing is "
              f"written{' for it' if marked.marked else ''}", file=out)
    _warn_of(marked.failed, args.prog, args.verb)
    return codes.OK


# -- fold --------------------------------------------------------------------------------

def _declare_fold(verbs) -> None:
    said = ("Freeze the round's design as its next version: copy design.md to "
            "versions/design-v<N>.md and record the version with what its data block "
            "declares. What the block adds is added at once; what it takes away "
            "waits for a verdict or the operator's ruling. Where the project's "
            "trace setting is on, the version's trace rows are checked and what "
            "the check found is recorded with it.")
    parser = verbs.add_parser("fold", help=said, description=said)
    parser.add_argument("round", metavar="ROUND",
                        help="the round's name, <project>/<slug>")
    parser.add_argument("--records-only", action="store_true",
                        help="the lead's call that this version changes records "
                             "only: no pass is asked for over it, and the design "
                             "keeps the standing it had; refused when the block's "
                             "questions, its stages, what a question holds up or "
                             "what a stage waits for differ from the version "
                             "before; needs --record")
    parser.add_argument("--record", metavar="PTR",
                        help="with --records-only, a pointer to the record of the "
                             "change, one token: file:<path in the round's "
                             "folder>#<anchor> as a rule, or comms:<digits>, "
                             "<session>@<UTC time>, an https:// URL, "
                             "page:<page>/<act id>@<UTC time>")
    _writes(parser)


def _fold(args, out) -> int:
    from . import freezing, sizing, tracing

    frozen = freezing.freeze(args.round, by=_actor(args),
                             records_only=args.records_only, record=args.record)
    print(f"froze the design of {frozen.name} as v{frozen.version}", file=out)
    print(f"file: {frozen.file}", file=out)
    print(f"sha256: {frozen.digest}", file=out)
    if args.records_only:
        print(f"records only: {args.record}", file=out)
        print(f"standing: {frozen.standing}", file=out)
    for line in _frozen(frozen.plan):
        print(line, file=out)
    for line in tracing.lines(frozen.trace):
        print(line, file=out)
    for one in frozen.oversized:
        print(sizing.oversized_line(one, frozen.slice_minutes), file=out)
    _warn_of(frozen.failed, args.prog, args.verb)
    return codes.OK


def _frozen(plan) -> list[str]:
    """What a freezing prints of what it did to the record: what it added, what waits,
    and what of the block it left as the record has it."""
    from . import state as states
    from . import versions

    added = plan.added
    lines = [f"added: {what} {', '.join(added[key])}"
             for key, what in (("questions", "questions"), ("stages", "stages"))
             if added.get(key)]
    lines += [f"added: {stage} now waits for {', '.join(more)} too"
              for stage, more in added.get("after", {}).items()]
    lines += [f"added: {question} now holds up "
              f"{blocks if isinstance(blocks, str) else ', '.join(blocks)}"
              for question, blocks in added.get("blocks", {}).items()]
    for question, change in added.get("options", {}).items():
        parts = []
        if change.get("options"):
            parts.append(f"gains {', '.join(change['options'])}")
        if change.get("retired"):
            parts.append(f"retires {', '.join(change['retired'])}")
        lines.append(f"options: {question} {' and '.join(parts)}")
    lines += [f"pending: {versions.said(one)}" for one in plan.pending]
    if plan.pending:
        lines.append("what is pending changes nothing until a verdict finds this "
                     "version publishable or the operator rules it")
    lines += [f"not used: the key {key} of {question} was retired and is never used "
              "again" for question, key in plan.refused]
    lines += [f"kept: {question} keeps the sentence on record, which is not the "
              "block's" for question in plan.reworded]
    for one in plan.kept_off:
        does = ("does not hold up" if one.what == states.BLOCKS
                else "does not wait for")
        why = (f"{one.ruling} took it off" if one.ruling is not None
               else "the block listed it before this edit")
        lines.append(f"kept off: {one.id} still {does} {one.stage}: {why}")
    if plan.kept_off:
        lines.append("what is kept off is held again by mark, with --blocks for a "
                     "question and --after for a stage")
    return lines


# -- verdict -----------------------------------------------------------------------------

def _declare_verdict(verbs) -> None:
    said = ("Record the verdict of a report, from its data block: for a report of a "
            "design, against the frozen version it says its pass read. A verdict "
            "that finds that version publishable takes out of the round what the "
            "version's block took away. For a report of a build, against the head it "
            "read, with its findings; it prints what the build's finish still waits "
            "for. The report's file is not changed.")
    parser = verbs.add_parser("verdict", help=said, description=said)
    parser.add_argument("round", metavar="ROUND",
                        help="the round's name, <project>/<slug>")
    parser.add_argument("report", metavar="REPORT.md",
                        help="the report's file, with its data block")
    _writes(parser)


def _verdict(args, out) -> int:
    from . import decisions, verdicts, versions, waits

    done = verdicts.record(args.round, args.report, by=_actor(args))
    one = done.verdict
    if one.kind == "majors":
        stages = ", ".join(done.event.get("stages") or ())
        print(f"recorded for {done.name}: the major decisions of {stages}, by "
              f"{one.by}, model {one.model}", file=out)
    elif one.kind == "retro":
        print(f"recorded for {done.name}: the retrospective, by {one.by}, model "
              f"{one.model}", file=out)
    elif one.stage is not None:
        base = "none" if one.merge_base is None else one.merge_base[:12]
        print(f"recorded for {done.name}: {one.verdict} for {one.stage} at "
              f"{one.head[:12]}, {one.pass_} pass by {one.by}, model {one.model}",
              file=out)
        print(f"merge-base with the target: {base}", file=out)
    else:
        print(f"recorded for {done.name}: {one.verdict} for v{one.design} of its "
              f"design, {one.pass_} pass by {one.by}, model {one.model}", file=out)
    print(f"report: {one.report}", file=out)
    print(f"sha256: {one.sha256}", file=out)
    if one.flags:
        print(f"flags: {', '.join(one.flags)}", file=out)
    for decision in done.decisions:
        foreclosing = ", forecloses" if decision.forecloses else ""
        print(f"decision {decision.key}: {decision.weight}{foreclosing}, "
              f"{decision.state}: {decision.text}", file=out)
        if decision.key in done.open_as:
            print(f"  {done.open_as[decision.key]}", file=out)
    if one.kind in ("majors", "retro"):
        # Neither names a design version or a build: no standing or gates to print.
        _warn_of(done.failed, args.prog, args.verb)
        return codes.OK
    if one.stage is not None:
        for finding in done.findings:
            print(f"finding {finding.key}: {finding.severity}, {finding.state}: "
                  f"{finding.text}", file=out)
        for wait in done.opened:
            print(f"waiting item {wait.key}, open: {wait.text}", file=out)
            print(f"  waits on the operator; once done: "
                  f"{waits.mark_line(done.name, wait.key)}", file=out)
        if done.waits:
            print(f"finish of {one.stage} waits for:", file=out)
            for line in done.waits:
                print(f"  {line}", file=out)
        else:
            print(f"finish of {one.stage} waits for nothing: its gates hold",
                  file=out)
        _warn_of(done.failed, args.prog, args.verb)
        return codes.OK
    for taken in one.applied:
        print(f"applied: {versions.said(taken)}", file=out)
    for taken, why in one.not_applied:
        print(f"not applied: {versions.said(taken)}: {why}", file=out)
    print(f"standing: {done.standing}", file=out)
    _warn_of(done.failed, args.prog, args.verb)
    return codes.OK


# -- commission --------------------------------------------------------------------------

def _declare_commission(verbs) -> None:
    said = ("Commission a stage of the round's plan: cut its branch from the target, "
            "add its worktree and make its scratch folder, each with a marker naming "
            "the build, and record the build. With --repo and --branch in place of a "
            "stage, in a flow that does not verify its design, the stage is declared "
            "as it is commissioned, under the lowest B<n> the round has not used.")
    parser = verbs.add_parser("commission", help=said, description=said)
    parser.add_argument("round", metavar="ROUND",
                        help="the round's name, <project>/<slug>")
    parser.add_argument("stage", metavar="STAGE", nargs="?",
                        help="the stage to build, B<n>; left out with --repo and "
                             "--branch")
    parser.add_argument("--repo", metavar="KEY",
                        help="with no stage: the project whose repo the stage is "
                             "built in, by key")
    parser.add_argument("--branch", metavar="NAME",
                        help="with no stage: the stage's branch")
    parser.add_argument("--slice", metavar="ID=TEXT", action="append", default=[],
                        dest="slices",
                        help="with no stage: one worker's piece of it, its id and "
                             "what it builds; each is followed by its --minutes")
    parser.add_argument("--minutes", metavar="N", action="append", default=[],
                        type=int, dest="minutes",
                        help="how many minutes the --slice before it is given")
    parser.add_argument("--builder", metavar="NAME", required=True,
                        help="who builds it")
    parser.add_argument("--record", metavar="PTR",
                        help="a pointer to where the operator gave the go, when he did")
    parser.add_argument("--existing", action="store_true",
                        help="build on the stage's branch that is already there, in "
                             "place of cutting it; refused when it overlaps another "
                             "open build (a branch inside or around one is taken), or "
                             "came onto the target by another build's finish")
    _writes(parser)


def _commission(args, out) -> int:
    from . import commissioning, sizing

    if len(args.slices) != len(args.minutes):
        raise BadArgument(f"{len(args.slices)} --slice and {len(args.minutes)} "
                          "--minutes are given, and each slice has its minutes",
                          "follow each --slice ID=TEXT with its --minutes N")
    slices = []
    for given, minutes in zip(args.slices, args.minutes, strict=True):
        slice_id, equals, what = given.partition("=")
        if not equals:
            raise BadArgument(f"--slice {given!r} gives no text after an id and '='",
                              "--slice ID=TEXT, as --slice 1a=the log")
        slices.append((slice_id.strip(), what, minutes))
    made = commissioning.commission(args.round, args.stage, builder=args.builder,
                                    by=_actor(args), repo=args.repo,
                                    branch=args.branch, slices=slices,
                                    record=args.record, existing=args.existing)
    for line in made.block.lines:
        print(line, file=out)
    print("", file=out)
    print(f"commissioned for {made.builder}   build {made.build}   host {made.host}",
          file=out)
    if made.adopted:
        print("kept: the worktree a stopped run of this commission left", file=out)
    if made.record is not None:
        print(f"go: {made.record}", file=out)
    for line in made.block.lead_lines():
        print(line, file=out)
    for one in made.oversized:
        print(sizing.oversized_line(one, made.slice_minutes), file=out)
    _warn_of(made.failed, args.prog, args.verb)
    return codes.OK


# -- check -------------------------------------------------------------------------------

def _declare_check(verbs) -> None:
    said = ("Run the repo's declared check for a build at its tip, in the build's "
            "worktree, and record the run: its commit, that commit's merge-base with "
            "the target, the argv, its exit code and duration. With --step, run a "
            "command step of the round's flow the same way, recorded under its name. "
            "Its output goes to check-<n>.log in the build's scratch folder. Nothing is "
            "recorded when the tip or the worktree changed while it ran. In a repo that "
            "declares ci, it pushes the tip to ci.remote when set (forced, to the "
            "branch name ci.ref gives, where it names one, never to ci.full_ref's), runs "
            "the local part unless it passed at the tip, and reads CI; --full runs the "
            "full check instead.")
    parser = verbs.add_parser("check", help=said, description=said)
    parser.add_argument("round", metavar="ROUND", nargs="?",
                        help="the round, <project>/<slug>; left out in a build's "
                             "worktree, the round of the open build on the branch "
                             "checked out there")
    parser.add_argument("stage", metavar="STAGE", nargs="?",
                        help="the stage whose build is checked, B<n>; given with ROUND")
    form = parser.add_mutually_exclusive_group()
    form.add_argument("--step", metavar="NAME",
                      help="run the command step NAME of the round's flow, a step of "
                           "your own, in place of the repo's check")
    form.add_argument("--full", action="store_true",
                      help="in a repo that declares ci, run the repo's declared check in "
                           "place of reading CI")
    parser.add_argument("--timeout-s", metavar="N", type=float, dest="timeout_s",
                        help="end the run's whole process tree after N seconds, in place "
                             "of the check's or the step's own timeout_s (3600 when "
                             "neither is set)")
    parser.add_argument("--no-wait", action="store_true", dest="no_wait",
                        help="refuse at once when another check on this machine holds "
                             "the check lock, in place of waiting for it")
    _writes(parser)


def _check(args, out) -> int:
    from . import checking

    name, stage = args.round, args.stage
    if name is None:
        from . import by_branch

        found = by_branch.find(os.getcwd(), verb="check")
        name, stage = found.round, found.stage
    elif stage is None:
        raise BadArgument(f"{name} is named with no stage, and a check is of one build",
                          f"moxi rounds check {name} B<n>, or moxi rounds check in the "
                          "build's worktree")
    ran = checking.check(name, stage, by=_actor(args), step=args.step,
                         full=args.full, timeout_s=args.timeout_s, wait=not args.no_wait,
                         say=lambda line: print(line, file=sys.stderr, flush=True))
    if isinstance(ran, checking.CiChecked):
        return _ci_checked(ran, args, out)
    code = ran.exit_code
    how = ("passed" if ran.passed else "was ended at its time limit" if code is None
           else f"failed: exit {code}")
    print(f"{ran.step} {ran.name} {ran.stage} at {ran.commit[:12]}: {how}, in "
          f"{ran.duration_s:g} s", file=out)
    print(f"ran: {shlex.join(ran.argv)}", file=out)
    print(f"output: {ran.output}", file=out)
    _warn_of(ran.failed, args.prog, args.verb)
    return codes.OK if ran.passed else codes.ERROR


# -- finish ------------------------------------------------------------------------------

def _declare_finish(verbs) -> None:
    said = ("Finish a build at its branch's tip, once every gate of finish holds there: "
            "take the repo's finish lock, run the repo's finish actions in order, each "
            "recorded under its id before the next starts, and record the finish. The "
            "merge action merges the tip into the target with a merge commit. A failed "
            "action is recorded and undoes nothing; run finish again once it is fixed. "
            "After a merge, the build's worktree, scratch folder and branch are "
            "removed where rounds made them and nothing would be lost; with no merge, "
            "only its scratch folder.")
    parser = verbs.add_parser("finish", help=said, description=said)
    parser.add_argument("round", metavar="ROUND", nargs="?",
                        help="the round, <project>/<slug>; left out in a build's "
                             "worktree, the round of the open build on the branch "
                             "checked out there")
    parser.add_argument("stage", metavar="STAGE", nargs="?",
                        help="the stage whose build is finished, B<n>; given with ROUND")
    parser.add_argument("--again", metavar="ID",
                        help="run once more the command action ID, which started and "
                             "has no result at the tip, once you have looked at what "
                             "it did; taken for no other action")
    _writes(parser)


def _finish(args, out) -> int:
    from . import finishing

    name, stage = args.round, args.stage
    if name is None:
        from . import by_branch

        found = by_branch.find(os.getcwd(), verb="finish")
        name, stage = found.round, found.stage
    elif stage is None:
        raise BadArgument(f"{name} is named with no stage, and a finish is of one build",
                          f"moxi rounds finish {name} B<n>, or moxi rounds finish in the "
                          "build's worktree")
    def on_action(one) -> None:
        # Said as it is recorded, so it is seen even when the finish is refused after.
        if one.hand_merge:
            print(f"found hand merge {str(one.landing)[:12]}: {one.output}", file=out)
        elif one.state == finishing.STARTED and one.again is not None:
            why = ("its repeat is set" if one.again == finishing.REPEAT
                   else "--again asked for it")
            # Said before it runs, since what it prints follows.
            print(f"{one.id}: started at {one.started_at} and has no result; run "
                  f"again, as {why}", file=out, flush=True)

    try:
        done = finishing.finish(name, stage, by=_actor(args), on_action=on_action,
                                again=args.again)
    except finishing.ActionFailed as exc:
        if exc.output:
            print(exc.output, file=out)
        raise
    print(f"finish {done.name} {done.stage} at {done.tip[:12]}", file=out)
    if done.cleared is not None:
        print(f"cleared the finish lock of a holder that had ended: {done.cleared}",
              file=out)
    for one in done.actions:
        how = (f"skipped by the operator, {one.skipped_by}; not run"
               if one.skipped_by is not None
               else one.state if one.ran else f"{one.state} before, not run again")
        landed = ("" if one.landing is None else
                  f": {one.landing[:12]} on the target, which was at "
                  f"{str(one.target_before)[:12]}")
        print(f"{one.id}: {how}{landed}", file=out)
        if one.kept is not None:
            print(f"{one.id}: kept the checkout it added, {one.kept}", file=out)
    print(f"finished {done.stage}" if done.event is not None
          else f"{done.stage} was already recorded as finished", file=out)
    _cleanup_lines(done.removed, done.kept, out, done.unscanned)
    if done.branch is not None:
        print(f"branch: {done.branch}", file=out)
    _ref_lines(done.refs, out)
    if not done.released:
        print(f"{args.prog} finish: warning: the finish lock no longer pointed at this "
              "finish's own and was left as it is; moxi doctor rounds names it if it "
              "stays", file=sys.stderr)
    _warn_of(done.failed, args.prog, args.verb)
    return codes.OK


def _ci_checked(ran, args, out) -> int:
    from . import ci

    print(f"check {ran.name} {ran.stage} at {ran.commit[:12]}, by CI", file=out)
    if ran.pushed is not None and ran.already:
        print(f"pushed: nothing, {ran.pushed} {ran.ref or 'the branch'} is already at "
              "the tip", file=out)
    elif ran.pushed is not None:
        print(f"pushed: {ran.pushed}" + ("" if ran.ref is None else f" {ran.ref}, forced"),
              file=out)
    local = ran.local
    if local is not None:
        code = local["exit_code"]
        how = ("passed" if code == 0 else "was ended at its time limit" if code is None
               else f"failed: exit {code}")
        print(f"{ci.LOCAL}: {how}, in {local['duration_s']:g} s; ran: "
              f"{shlex.join(local['argv'])}; output: {local['output']}", file=out)
    else:
        print(f"{ci.LOCAL}: not run; it passed at this tip before, or none is declared",
              file=out)
    if ran.read_failed is not None:
        print(ran.read_failed, file=out)
        print(f"  fix: moxi rounds check {ran.name} {ran.stage} --full meets the gate "
              "without CI", file=out)
    reading = ran.reading
    if reading is not None:
        print(f"ci: {reading['reading']}", file=out)
        for one in reading["workflows"]:
            url = "" if one["url"] is None else f"   {one['url']}"
            print(f"  {one['path']}: {one['reading']}{url}", file=out)
            for job in one.get("jobs") or ():
                if job["reading"] != ci.GREEN:
                    url = "" if job["url"] is None else f"   {job['url']}"
                    print(f"    {job['name']}: {job['reading']}{url}", file=out)
        for line in _ci_way_on(ran.name, ran.stage, reading["reading"]):
            print(f"  fix: {line}", file=out)
    _warn_of(ran.failed, args.prog, args.verb)
    return codes.OK if ran.passed else codes.ERROR


def _ci_way_on(name: str, stage: str, whole: str) -> list[str]:
    from . import ci

    again, full = f"moxi rounds check {name} {stage}", f"moxi rounds check {name} {stage} --full"
    return {ci.PENDING: [f"once CI ends, {again} reads again; the local part is not run "
                         "again at this tip"],
            ci.ABSENT: [f"push the branch or set ci.remote, see that the workflow runs on "
                        f"a branch push, then {again}"],
            ci.RED: [f"fix and commit, then {again}", f"or {full}"]}.get(whole, [])


# -- drop --------------------------------------------------------------------------------

def _declare_drop(verbs) -> None:
    said = ("Drop a round, or with a stage the open build of that stage: end it short "
            "of its work. The operator drops any round or build, with a pointer to "
            "where he said so; the lead drops only a round that holds none of the "
            "operator's acts, or a build that is gone or empty with no commit of its "
            "own, as git reads it now, with a pointer to why. A round's open and "
            "parked questions go to the project's list as they are, where they hold "
            "up nothing, or with --withdraw-questions are withdrawn. A dropped round "
            "takes no new work; a correction of one of its rulings is still recorded, "
            "and its rulings are still marked as shown. A dropped build's stage stays "
            "owed. No branch is deleted; the ended builds' folders are removed where "
            "the removal rule proves they may go. With --leftovers: the sweep of the "
            "folders ended builds left on this machine, a dry run until sweep.armed is "
            "true.")
    parser = verbs.add_parser("drop", help=said, description=said)
    parser.add_argument("round", metavar="ROUND", nargs="?",
                        help="the round, <project>/<slug>")
    parser.add_argument("stage", metavar="STAGE", nargs="?",
                        help="the stage, B<n>, whose open build is dropped; left out, "
                             "the whole round is")
    parser.add_argument("--by", choices=("lead", "operator"),
                        help="whose drop it is: the operator's, of any round or "
                             "build, or the lead's, of a round that holds no ruling, "
                             "no skip, no question asked with the operator's "
                             "pointer, no opening on his go, no build that reached "
                             "its target or may have, no decision on such a build, "
                             "no open waiting item and no open build he may not "
                             "drop; or of a build git reads as gone, or empty with "
                             "no commit of its own, that shares with no other")
    parser.add_argument("--record", metavar="PTR",
                        help="with --by operator, a pointer to where he dropped it; "
                             "with --by lead, a pointer to why; one token: "
                             "comms:<digits>, <session>@<UTC time>, an https:// URL, "
                             "file:<path in the round's folder>#<anchor>, or "
                             "page:<page>/<act id>@<UTC time>, his act on a page; a "
                             "drop without one is refused")
    parser.add_argument("--withdraw-questions", action="store_true",
                        help="withdraw the round's open and parked questions, where "
                             "they would go to the project's list; the operator's "
                             "alone")
    parser.add_argument("--leftovers", action="store_true",
                        help="sweep the worktrees and scratch folders that ended builds "
                             "left on this machine: say what would go, and what stays "
                             "and why; it takes no round, --by or --record")
    parser.add_argument("--refs", action="store_true",
                        help="with --leftovers, also give the account of CI's own refs "
                             "on the remote of each project whose ci names a ref: each "
                             "branch under the prefix of ref, or of full_ref where set, "
                             "that no build that has not ended, nor the target, owns; "
                             "it reaches the network")
    parser.add_argument("--remove", action="store_true",
                        help="with --leftovers, remove what would go; refused, after "
                             "the dry run, until sweep.armed is true")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="with --leftovers, print the account as JSON")
    _writes(parser)


def _drop(args, out) -> int:
    from . import dropping

    if args.leftovers:
        return _leftovers(args, out)
    if args.remove or args.as_json or args.refs:
        raise BadArgument("--remove, --refs and --json are for the sweep of leftover "
                          "folders", "add --leftovers, or leave them out")
    if args.round is None or args.by is None:
        raise BadArgument("a drop names the round and whose drop it is",
                          "moxi rounds drop ROUND [B<n>] --by lead|operator --record PTR")
    if args.stage is not None:
        return _drop_build(args, out)
    dropped = dropping.drop(args.round, by=args.by, record=args.record,
                            run_by=_actor(args),
                            withdraw_questions=args.withdraw_questions)
    project = dropped.name.partition("/")[0]
    print(f"dropped {dropped.name}   by: {dropped.by}   record: {dropped.record}",
          file=out)
    withdrawn = [one for one, where in dropped.went if where == dropping.WITHDRAWN]
    moved = [f"{one} as {where}" for one, where in dropped.went
             if where != dropping.WITHDRAWN]
    if moved:
        print(f"questions now on the list of {project}: {', '.join(moved)}", file=out)
    if withdrawn:
        print(f"questions withdrawn: {', '.join(withdrawn)}", file=out)
    if not dropped.went:
        print("it had no open or parked question", file=out)
    for one in dropped.event.get("tips") or ():
        print(f"ended {one['stage']}: {one['branch']} at "
              f"{'no branch' if one['tip'] is None else one['tip'][:12]}, kept in git",
              file=out)
    _cleanup_lines(dropped.removed, dropped.kept, out, dropped.unscanned)
    _ref_lines(dropped.refs, out)
    _warn_of(dropped.failed, args.prog, args.verb)
    return codes.OK


def _drop_build(args, out) -> int:
    """`drop ROUND B<n>`: drop the open build of one stage."""
    from . import dropping

    if args.withdraw_questions:
        raise BadArgument("a build's drop changes no question: --withdraw-questions is "
                          "for a round's drop", "leave --withdraw-questions out")
    dropped = dropping.drop_build(args.round, args.stage, by=args.by,
                                  record=args.record, run_by=_actor(args))
    [tip] = dropped.event["tips"]
    print(f"dropped {dropped.name} {dropped.stage}   by: {dropped.by}   record: "
          f"{dropped.record}", file=out)
    print(f"branch: {tip['branch']} at "
          f"{'no branch' if tip['tip'] is None else tip['tip'][:12]}, kept in git; "
          f"the stage stays owed: commission it again, or take it out of the plan",
          file=out)
    _cleanup_lines(dropped.removed, dropped.kept, out, dropped.unscanned)
    _ref_lines(dropped.refs, out)
    _warn_of(dropped.failed, args.prog, args.verb)
    return codes.OK


def _leftovers(args, out) -> int:
    """`drop --leftovers [--remove]`: the sweep of ended builds' folders."""
    import json

    from . import sweeping

    given = [flag for flag, value in (("ROUND", args.round), ("--by", args.by),
                                      ("--record", args.record), ("--as", args.actor))
             if value is not None]
    if args.withdraw_questions:
        given.append("--withdraw-questions")
    if given:
        raise BadArgument(f"the sweep changes no record of any round, so it takes no "
                          f"{', '.join(given)}", f"{sweeping.DRY_RUN} [--remove]")
    swept = sweeping.run(remove=args.remove)
    refs = None
    if args.refs:
        from . import ci_refs
        from . import settings as own

        refs = ci_refs.account(own.load(), removing=swept.removing)
    if args.as_json:
        found = swept.as_json()
        if refs is not None:
            found["refs"] = refs.as_json()
        print(json.dumps(found, indent=2), file=out)
    else:
        _account_lines(swept, out)
        if refs is not None:
            _ref_account_lines(refs, swept, out)
    if args.remove and not swept.armed:
        raise Refused([Unmet("the sweep is not armed (sweep.armed is false), so it "
                             "removes nothing; above is what it would remove",
                             "read the dry run, then set armed = true under [sweep] in "
                             "rounds' config (moxi config rounds shows the file), and "
                             f"run {sweeping.REMOVE}")])
    return codes.OK


def _account_lines(swept, out) -> None:
    """The sweep's account: each leftover folder and trash entry, what became of it, and
    the command that removes what would go."""
    from . import sweeping

    if swept.removing:
        print("leftover folders of ended builds on this machine", file=out)
    else:
        why = "" if swept.armed else ": sweep.armed is false"
        print(f"leftover folders of ended builds on this machine (dry run{why})",
              file=out)
    for line in swept.lines():
        print(line, file=out)
    if swept.clear:
        print("no folder of an ended build is left on this machine", file=out)
    if swept.timed_out:
        print("the run stopped starting removals after "
              f"{sweeping.TIME_LIMIT_S:g} seconds; run it again for the rest", file=out)
    goes = swept.would_go or [one for one in swept.trash
                              if one.fate == sweeping.WOULD_DELETE]
    if goes and not swept.removing:
        arm = "" if swept.armed else ("set armed = true under [sweep] in rounds' config, "
                                      "then: ")
        print(f"then: {arm}{sweeping.REMOVE}", file=out)


def _ref_lines(refs, out) -> None:
    """Say what became of each of CI's own refs of an ended build."""
    for one in refs:
        print(one.line(), file=out)


def _ref_account_lines(refs, swept, out) -> None:
    """The account of CI's own refs on the remotes, and the command that removes what
    would go."""
    from . import ci_refs

    why = "" if swept.removing else (" (dry run)" if swept.armed
                                     else " (dry run: sweep.armed is false)")
    print(f"CI's own refs on the remotes{why}", file=out)
    for line in refs.lines():
        print(line, file=out)
    if not refs.refs and not refs.unlisted:
        print("no ref of CI's own is on a remote", file=out)
    if refs.would_go and not swept.removing:
        arm = "" if swept.armed else ("set armed = true under [sweep] in rounds' config, "
                                      "then: ")
        print(f"then: {arm}{ci_refs.REMOVE}", file=out)


def _cleanup_lines(removed, kept, out, unscanned=()) -> None:
    """Say where the scratch gate was not run, which of an ended build's folders were
    removed, and which the removal rule kept, with the check that kept each."""
    for line in unscanned:
        print(line, file=out)
    for removed_dir in removed:
        print(f"removed {removed_dir}", file=out)
    for one in kept:
        print(f"kept {one.place}: {one.line()}; it is left for the sweep of leftover "
              "folders", file=out)


# -- next --------------------------------------------------------------------------------

def _declare_next(verbs) -> None:
    from . import next_step

    said = ("Say what is done next in a round: the step it is at, who does it, at "
            "which tier, what they read, the command that completes it and what it "
            "waits for, then what the lead should know. With --schema, print the "
            "empty data block of a report of that kind. Nothing is recorded.")
    parser = verbs.add_parser("next", help=said, description=said)
    parser.add_argument("round", metavar="ROUND", nargs="?",
                        help="the round, <project>/<slug>; left out in a build's "
                             "worktree, the round of the open build on the branch "
                             "checked out there")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="print the same as data, not as lines")
    parser.add_argument("--schema", metavar="KIND",
                        help="print the empty data block of this kind of report: "
                             f"{', '.join(next_step.SCHEMAS[:-1])}")


def _next(args, out) -> int:
    from . import next_step

    if args.schema is not None:
        out.write(next_step.schema(args.schema))
        return codes.OK
    name = args.round
    if name is None:
        from . import by_branch

        name = by_branch.find(os.getcwd(), verb="next").round
    found = next_step.read(name)
    out.write(next_step.as_json(found) if args.as_json else next_step.as_text(found))
    return codes.OK


# -- show --------------------------------------------------------------------------------

def _declare_show(verbs) -> None:
    said = ("Show one round: what it is for, its status, its flow and the steps "
            "recorded for it, the step it is at and what its close waits for, its "
            "design, its stages and builds with their checks, verdicts, findings, "
            "waiting items and decisions, its questions by state, its rulings with "
            "whether each was shown to the operator, its history and, once it has "
            "ended, its numbers. Nothing is recorded. A project's rounds are listed "
            "with list.")
    parser = verbs.add_parser("show", help=said, description=said)
    parser.add_argument("round", metavar="ROUND", help="the round, <project>/<slug>")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="print the round as data, not as lines")


def _show(args, out) -> int:
    from . import showing

    found = showing.show(args.round)
    out.write(showing.as_json(found) if args.as_json else showing.as_text(found))
    return codes.OK


# -- list --------------------------------------------------------------------------------

def _declare_list(verbs) -> None:
    said = ("List rounds, or what waits on the operator. With no option: the open "
            "rounds of every project, or of the one named, each with its flow, the "
            "step it is at, how many questions it has open and parked and how many of "
            "its rulings were not yet shown to him; with --closed, the rounds that "
            "have ended. With --waiting: every open question, every parked one, the "
            "open waiting items, the unruled major decisions, the builds to look at "
            "and the rulings not yet shown to him. With --numbers: the numbers of the "
            "closed rounds, summed. With --questions: the questions of one round or "
            "one project written out, the open ones, then the parked ones, then the "
            "rulings not yet shown; a round's in two views, its docket and its "
            "ratification. Nothing is recorded.")
    parser = verbs.add_parser("list", help=said, description=said)
    parser.add_argument("name", metavar="ROUND|PROJECT", nargs="?",
                        help="a project's key, to keep the listing to that project; "
                             "with --questions, the project or the round, "
                             "<project>/<slug>, whose questions are listed")
    parser.add_argument("--waiting", action="store_true",
                        help="list what waits on the operator")
    parser.add_argument("--closed", action="store_true",
                        help="list the rounds that have ended, not the open ones")
    parser.add_argument("--numbers", action="store_true",
                        help="sum the numbers of the closed rounds: passes, folds, "
                             "time in each step, findings, questions, decisions, "
                             "skips and the rest")
    parser.add_argument("--questions", action="store_true",
                        help="list the questions of the round or the project")
    parser.add_argument("--view", metavar="docket|ratify", choices=("docket", "ratify"),
                        help="with --questions, for a round, which of its questions: "
                             "docket, every open one and after them the parked ones; "
                             "ratify, the decisions of its finished builds; both "
                             "when left out; a project's questions have no view")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="print the same rows as data")
    parser.add_argument("--out", metavar="DIR",
                        help="with --questions, write both forms into this folder, as "
                             "questions.md and questions.json, and print the two paths")


def _list(args, out) -> int:
    from . import listing

    kinds = [flag for flag, given in (("--waiting", args.waiting),
                                      ("--closed", args.closed),
                                      ("--numbers", args.numbers),
                                      ("--questions", args.questions)) if given]
    if len(kinds) > 1:
        raise BadArgument(f"{' and '.join(kinds)} are each a listing of their own",
                          "give one of them")
    if args.questions:
        return _list_questions(args, out)
    for flag, given in (("--view", args.view), ("--out", args.out)):
        if given is not None:
            raise BadArgument(f"{flag} is for the questions of one round or one "
                              "project", f"add --questions, or leave {flag} out")
    if args.waiting:
        found = listing.waiting(args.name)
        as_text = listing.waiting_as_text
    elif args.numbers:
        found = listing.numbers(args.name)
        as_text = listing.numbers_as_text
    else:
        found = listing.rounds(args.name, ended=args.closed)
        as_text = listing.rounds_as_text
    out.write(listing.as_json(found) if args.as_json else as_text(found))
    return codes.OK


def _list_questions(args, out) -> int:
    """`list --questions`: the questions of one round or one project, written out."""
    from . import export

    found = export.questions_of(args.name, view=args.view)
    if args.out is not None:
        for path in export.write(found, args.out):
            print(path, file=out)
    else:
        out.write(export.as_json(found) if args.as_json else export.as_markdown(found))
    return codes.OK


# -- the command -------------------------------------------------------------------------

#: The function that declares each verb's arguments, in the order `--help` lists them.
_DECLARE: tuple[Callable, ...] = (_declare_open, _declare_fold, _declare_verdict,
                                  _declare_commission, _declare_check, _declare_finish,
                                  _declare_ask,
                                  _declare_rule,
                                  _declare_mark, _declare_drop, _declare_next,
                                  _declare_show,
                                  _declare_list)

#: Each verb's name -> the function that runs it, as `fn(args, out) -> int`.
_VERBS: dict[str, Callable] = {"open": _open, "fold": _fold, "verdict": _verdict,
                               "commission": _commission, "check": _check,
                               "finish": _finish,
                               "ask": _ask, "rule": _rule,
                               "mark": _mark, "drop": _drop, "next": _next,
                               "show": _show,
                               "list": _list}


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
    from . import write

    try:
        with write.watching() as writes:
            code = _VERBS[args.verb](args, sys.stdout)
        _say_closed(writes, sys.stdout)
        return code
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
