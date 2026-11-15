# moxi-rounds

Moxi's record of a piece of work: its design and the verdicts on it, its questions, the
operator's rulings on it, and each project's standing rulings.

A round is one proposal, from its design to the step that puts what was built where it
goes. rounds is for anyone who directs agents that write code in git repos: one person,
here called the operator, with a lead agent that runs the round and workers that build
and check. Everything the operator decides is recorded as his, with a pointer to where he
said it, and is shown back to him as it was recorded.

```sh
moxi projects add alpha --root ~/code/alpha --notes-root ~/notes/alpha
moxi rounds open alpha/search --description "Search over the notes folder"
moxi rounds ask alpha/search "Should search read archived notes too?" --from comms:12 \
    --option a="Yes, all notes" --option b="No, live notes only"
moxi rounds list --waiting                                  # lists alpha/search Q1
moxi rounds rule alpha/search Q1 --chose b --record comms:14     # Recorded as C1
moxi rounds mark alpha/search C1 --shown --record comms:17
moxi rounds next alpha/search                               # the step it is at
moxi rounds show alpha/search
```

rounds starts no agent, registers no git hook and draws no page. It writes the open
questions out as markdown and as data, and whatever shows them to the operator is outside
it.

## What this version does

This version keeps the record of a round and takes it through its design: a round's log,
its questions and rulings, the frozen versions of its design, the verdicts of the
reports that verify it, the stages the design plans, a project's own list of questions
and its register of standing rulings, and the files generated from them. Its verbs are
`open`, `fold`, `verdict`, `commission`, `check`, `finish`, `ask`, `rule`, `mark`,
`next`, `show`, `list` and `drop`.

It does not yet do the rest of a round:

- removing a finished build's worktree, scratch folder and branch;
- decisions a build records for the operator to ratify;
- the retrospective, and the numbers of a round that ended;
- answers prepared ahead for a sitting with the operator;
- parent rounds.

So a round moves through its design, the design's verification and its questions, has
each stage commissioned, built and checked, and stops there: `next` prints each later
step as not built. A stage's `build` step is ready once it is commissioned and done once
git reads its branch as holding work (`building`) or merged; `next`, `show` and `list`
read git for it. A round whose flow has no `build` step closes itself (see Closing); any
other ends only by `drop`. Config keys that belong to the parts
above are declared and checked, and nothing reads them yet (see Config).

## Commands

A round's name is `<project>/<slug>`: a project's key from `moxi projects`, and a slug of
lower-case letters, digits and `-`.

| Command | Does |
|---|---|
| `moxi rounds open ROUND --description TEXT [--flow NAME] [--record PTR] [--inherits S<n>[,...]]` | open a round: make its folder and record what it is for and its flow; `--record` points to where the operator gave the go, and `--inherits` names the standing rulings it starts from |
| `moxi rounds next [ROUND] [--json]` | what is done next: the step the round is at, who does it, what they read, the command that completes it and what it waits for; with no round, in a build's worktree, the round of the open build on the branch checked out there |
| `moxi rounds next --schema design\|build\|step\|majors\|retro` | the empty data block of a report of that kind, a comment on each key |
| `moxi rounds fold ROUND [--records-only --record PTR]` | freeze `design.md` as the next version and record what its data block declares |
| `moxi rounds verdict ROUND REPORT.md` | record the verdict of a report on the design, on a build or on a build for a report step of your own, run by whoever wrote the report |
| `moxi rounds ask ROUND TEXT --from PTR [--blocks B<n>[,...]\|none] [--option K=TEXT ...]` | put a question to the operator in a round, as `Q<n>`; it holds up every stage unless `--blocks` names the ones it holds up, or `none` |
| `moxi rounds ask ROUND TEXT --build B<n> [--blocks B<n>[,...]\|none]` | put a question that came up in the open build of a stage: a builder tells you, and you ask it; it comes from that build at its branch's tip, and is listed as asked by you for the builder |
| `moxi rounds ask PROJECT TEXT --from PTR [--option K=TEXT ...]` | put a question on the project's list, as `P<n>`, where it holds up nothing |
| `moxi rounds ask ROUND\|PROJECT ID [--option K=TEXT ...] [--retire K ...]` | add options to a question on record, or retire them |
| `moxi rounds ask ROUND --adopt P<n> [--record PTR]` | bring a question of the project's list into the round |
| `moxi rounds ask PROJECT --move ROUND:Q<n> [--record PTR]` | put a round's question on the project's list |
| `moxi rounds rule ROUND --text TEXT --record PTR [--reason TEXT] [--stages B<n>[,...]]` | record a ruling of the round, as `C<n>`; `--stages` names the stages it applies to, when not all |
| `moxi rounds rule ROUND --standing --text TEXT --record PTR [--scope SPEC]`, `rule PROJECT --text TEXT --record PTR [--scope SPEC]` | record a standing ruling in the project's register, as `S<n>` |
| `moxi rounds rule ROUND\|PROJECT Q<n>\|P<n> (--text TEXT \| --chose K) --record PTR` | answer a question |
| `moxi rounds rule ROUND\|PROJECT park:Q<n> --record PTR [--until round:ROUND]` | park a question, so it is not put to him until it is reopened; `--until` names another round it waits for |
| `moxi rounds rule ROUND park:Q<n> --record PTR --until B<n>` | park a question until a stage is finished |
| `moxi rounds rule ROUND narrow:Q<n>=B<n>[,...]\|none --record PTR` | make a question hold up fewer stages, or nothing |
| `moxi rounds rule ROUND\|PROJECT withdraw:Q<n> --record PTR` | withdraw a question |
| `moxi rounds rule ROUND\|PROJECT ... --supersedes C<n>\|S<n>` | replace an earlier ruling of the same thing |
| `moxi rounds rule ROUND\|PROJECT revoke:C<n>\|S<n> --record PTR` | take a ruling back and put nothing in its place |
| `moxi rounds rule PROJECT readback=TIME --record PTR` | record that he read the list of rulings not yet shown to him through TIME |
| `moxi rounds rule ROUND skip:STEP[:B<n>] --record PTR` | say that a step is not done, for the round or for one stage |
| `moxi rounds rule ROUND skip-action:B<n>=ID --record PTR` | say that the finish action `ID` is not run for one stage: `finish` treats it as done and writes no record of it; the way out of a command that ran but whose result was lost, or that can't pass again |
| `moxi rounds rule ROUND flow:NAME --record PTR` | move the round to another flow, whichever it is |
| `moxi rounds rule ROUND credit:B<n> --record PTR` | credit to a stage's open build what its target gained by a fast-forward no reading of the build's explains: the build then reads `merged`, until `revoke:` takes the credit back |
| `moxi rounds rule ROUND unstage:B<n> --record PTR` | take a stage out of the plan |
| `moxi rounds rule ROUND unafter:B<n>=B<m> --record PTR` | make a stage wait for one stage fewer |
| `moxi rounds mark ROUND Q<n> --blocks B<n>[,...]\|all`, `mark ROUND Q<n> --reopen` | make a question hold up more stages, or reopen a parked one |
| `moxi rounds mark ROUND --flow NAME` | move the round up to a heavier flow, or to its own flow's steps as the config has them now |
| `moxi rounds mark ROUND B<n> --after B<m>[,...]` | make a stage wait for more stages |
| `moxi rounds mark ROUND B<n> --brief FILE` | size a worker's brief before the worker gets it: one slice of the stage within `slice_minutes`, or one verification, or the whole of a stage that declares no slices |
| `moxi rounds mark ROUND B<n>.L<k> --fixed --commit SHA... --no-behaviour-change` | the lead's mark of a minor or note finding fixed, naming every commit of the fix |
| `moxi rounds mark ROUND B<n>.W<k> --done --record PTR` | a waiting item is done, with a pointer to the report, commit or message that shows it |
| `moxi rounds mark ROUND thread:NAME --close\|--accept-risk --record PTR` | the lead's decision on a design that was sent back twice in a row |
| `moxi rounds mark ROUND\|PROJECT C<n>\|S<n>[,...] --shown --record PTR` | say that rulings were shown to the operator as recorded |
| `moxi rounds mark ROUND B<n> --resync` | read a build's base again from git: moved only after the target's history was rewritten under it |
| `moxi rounds mark ROUND REPORT.md --reread` | read a recorded report again and add the findings, waiting items and decisions its verdict's record lacks; never removes or changes one |
| `moxi rounds commission ROUND --repo KEY --branch NAME --builder NAME [--slice ID=TEXT --minutes N]...` | in a flow that does not verify its design: declare a stage under the lowest `B<n>` not used, and commission it as below |
| `moxi rounds commission ROUND B<n> --builder NAME [--record PTR] [--existing]` | build a stage: cut its branch from the target, add its worktree under `worktree_dir` as `<repo>/<branch>` and its scratch folder in rounds' cache directory, each with a marker naming the build, and record the build; `--existing` builds on a branch that is there. It prints the commission block, which goes first in the builder's brief, then tells you which of the block's rulings the operator has not been shown |
| `moxi rounds check [ROUND B<n>] [--step NAME \| --full] [--timeout-s N]` | run the repo's declared check for a build at its tip, in its worktree, and record the run: its commit, that commit's merge-base with the target, the argv, its exit code and duration. The worktree must be clean on the build's branch and the build must have work. Its output goes to `check-<n>.log` in the build's scratch folder; its whole process tree is ended at the check's `timeout_s`, or `--timeout-s`. Nothing is recorded when the tip or the worktree changed while it ran. With no round, in a build's worktree, it checks that build; it exits 1 when the check failed. With `--step NAME` it runs the command step `NAME` of the round's flow, a step of your own, the same way, and records the run under that name. In a repo that declares `ci` it checks by CI instead: it pushes the tip to `ci.remote` when set, runs the local part (recorded under `check.local`) unless it passed at the tip, and reads CI, recording the reading; `--full` runs the full check there |
| `moxi rounds finish [ROUND B<n>] [--again ID]` | finish a build at its branch's tip once every gate of finish holds there: take the repo's finish lock, the ref `refs/rounds/finishing/<into>` or the repo's `lock_ref`; read again the gates a move of the target can change; run the repo's finish actions in order, each recorded under its `id` before the next starts, an action recorded `ok` or `found` at the tip not run again; a merge already on the target (made by hand, by a fast-forward of the target, or by a finish that stopped before recording it) is recorded `found` with where it landed and is not made again, and a hand merge is printed as `found hand merge <commit>:` with what it put on the target; and record the finish when the gates read on the log still hold at the tip it merged, though the branch moved since. The `merge` action merges the tip, by its commit id, into the target with a merge commit, in the checkout that has the target, or in one it adds under `worktree_dir` and removes after. A conflict is aborted and refused with the way through; a failed action is recorded, undoes nothing, and exits 1. A lock left by a process on this machine that has ended is cleared and recorded; any other holder is named, with the git command that clears it. A `push` pushes the target at the merge's landing, or with no merge before it the build's branch at the tip, both by commit id. A `command` runs its argv with no shell in the build's worktree, placeholders filled as for a check, its output yours, with `started` recorded before it runs. A command that started and has no result may have done its work: it is run again, saying so, when its `repeat` is true, or once with `--again ID` after you have looked at what it did; otherwise it is refused with both ways on, that and the operator's `skip-action`. `--again` is refused for any other action. An action the operator skipped for the stage is not run and counts as done. Then it cleans up: after a merge, and only while the branch is still at the tip it finished, it removes the build's worktree and scratch folder and deletes the branch with `git branch -d` (not with `keep_branch`); with no merge it removes only the scratch folder. Each removal obeys the removal rule below, and a folder the rule keeps is named with the check that kept it. With no round, in a build's worktree, it finishes that build |
| `moxi rounds show ROUND [--json]` | one round: status, flow, the step it is at, what its close waits for, its design and stages, questions by state, rulings, verdicts and history |
| `moxi rounds list [PROJECT] [--closed] [--json]` | the open rounds, each with its step and its counts, or with `--closed` the ended ones, each with how and when it ended |
| `moxi rounds list [PROJECT] --waiting [--json]` | what waits on the operator: open questions, parked ones, open waiting items, rulings not yet shown, builds to look at |
| `moxi rounds list ROUND\|PROJECT --questions [--view docket\|ratify] [--json] [--out DIR]` | the questions written out as markdown; `--out` writes `questions.md` and `questions.json` into DIR; a round's two views are both given when `--view` is left out |
| `moxi rounds drop ROUND --by operator\|lead --record PTR [--withdraw-questions]` | end a round short of its work |
| `moxi rounds drop ROUND B<n> --by operator\|lead --record PTR` | drop the open build of one stage; the stage stays owed |
| `moxi rounds drop --leftovers [--remove] [--json]` | sweep the worktrees and scratch folders ended builds left on this machine: what would go, and what stays and why; `--remove` takes what may go, once `sweep.armed` is true |

No command closes a round: a round closes itself (see Closing).

Every writing verb takes `--as NAME`, who is doing it; the default is the user the command
runs as. `moxi rounds VERB --help` has each option in full.

Exit codes: 0 done; 1 an error; 2 asked wrongly, such as a name that is no round's name or
a config value of the wrong type; 3 refused, such as a condition that is not met, no such
project or round, a log that can't be read, or a lock another process held for 30 seconds.
A refusal names every condition that is not met, each with the command that meets it, and
writes nothing.

## Pointers

A question says where it came from and a ruling says where the operator said it. Each is
one token:

- `comms:<digits>`, a message;
- `<session>@<UTC time>`, a session id of 8 or more hex digits and a time such as
  `2026-01-02T03:04Z`;
- an `https://` URL;
- `file:<path in the round's folder>#<anchor>`, as `file:design.md#index` for a section
  of the design.

A question with no `--from` and a ruling with no `--record` are refused. A question that
is the operator's own takes `--record` in place of `--from`. `mark --shown` takes a
message, a session or a URL, and never a file, the pointer of one of the rulings it
marks, or a message from before one of them was said.

## Flows and steps

A flow is the named list of steps a round follows. Three are built in:

| Flow | Steps |
|---|---|
| `full` | `design`, `verify-design`, `build`, `verify-build`, `finish`, `retro` |
| `small` | `design`, `build`, `verify-build`, `finish`, `retro` |
| `fix` | `build`, `verify-build`, `finish` |

A flow with a `verify-design` step also has the questions step, `docket`, after it: the
step is ready while a question of the round is open. A flow with a `build` step also has
`commission`, `check` and `ratify`. A round that builds nothing follows a flow of your
own with no `build` step:

```toml
[flows.design]
steps = [
  { step = "design",        by = "lead",    model = "second" },
  { step = "verify-design", by = "checker", model = "second", recheck_model = "third" },
]
```

A flow may also list steps of your own, done for each stage before `finish`. One that
runs a command is written `{ step = "staging", does = "command", before = "finish", by =
"builder", argv = ["./stage.sh", "{tip}"], timeout_s = 900 }`: `moxi rounds check
--step staging` runs its `argv`, with the same placeholders as a check, and the step is
done at a build's tip once the newest run recorded under its name passed there with the
`argv` the round's flow recorded. A run of one step never counts for another, nor for
the repo's check.

`open` records the flow's steps in the round, and the round follows what was recorded,
whatever the config says since. `mark ROUND --flow NAME` moves a round up: to a flow
later in `tiers` that has every step the round follows, or to its own flow's steps as
the config has them now when those keep every step. Any other move is the operator's,
`rule ROUND flow:NAME --record PTR`.

The operator skips a step with `rule ROUND skip:STEP --record PTR`, or
`skip:STEP:B<n>` for a step of one stage. `verify-design`, `ratify` and `retro` are
skipped for the round; `check`, `verify-build` and a step of your own for one stage.
`design`, `commission`, `build` and `finish` are never skipped. `skip:docket:B<n>`
lets one stage past the questions that hold it at that moment; a question asked or
widened onto the stage later holds it again.

`next` says where a round is. Each step is `ready`, `waiting`, `done` or `skipped`, and
a round is at the first one that is neither done nor skipped. The examples from here on
follow one round on the `design` flow above, in a config whose `models` table gives the
tier `second` the one name `model-two`:

```text
$ moxi rounds open alpha/ranking --description "Ranking of search results" --flow design
opened alpha/ranking   flow design (design, verify-design)
folder: ~/notes/alpha/rounds/ranking
$ moxi rounds next alpha/ranking
round alpha/ranking   flow design   step design   by lead, tier second (model-two)
reads: nothing named
write: design.md in ~/notes/alpha/rounds/ranking
then:  moxi rounds fold alpha/ranking
waits: nothing
```

`by` and the tier are the step's entry in the flow, and `reads` lists what the entry
says to read first. The model's name is printed beside the tier as the `models`
setting gives it: the one name, or each harness with its model where they differ. A step that waits prints what it waits for and the
command that meets it. After the step come warnings, which hold up nothing: a design
edited since it was frozen, a ruling not yet shown to the operator, a trace row gone
stale. `next` records nothing, and `next --json` gives the same as data.
A builder runs `moxi rounds next` with no round from the build's worktree: the open
build on the branch checked out there, in the project the folder is in, names the
round.

## The design

The lead writes `design.md` in the round's folder. rounds reads one thing in it, the
data block: the first fenced block whose info string is `toml rounds`. It declares the
round's questions, its stages, and where the project asks for them its trace rows.

````markdown
# Ranking of search results

A match in a note's title ranks above a match in its body.

```toml rounds
[[question]]
id = "Q1"
text = "Should a title match always rank above a body match?"
blocks = "none"
options = { a = "Yes, always", b = "Only an exact title match" }
```
````

`fold` freezes the design: it copies the file to `versions/design-v<N>.md` and records
that version with what the block declares.

```text
$ moxi rounds fold alpha/ranking
froze the design of alpha/ranking as v1
file: ~/notes/alpha/rounds/ranking/versions/design-v1.md
sha256: bcaf142cc4c23e2f0cec4679239f626c1cf8681954c11d9a9f874b68916bc0d3
added: questions Q1
```

A freezing adds and takes nothing away. A new question, a new stage, a stage another
now waits for and a question that holds up more are added to the round at once. What a
block leaves out or narrows is recorded as pending, and changes nothing until a verdict
that finds that version publishable applies it, or the operator rules it. A block that
can't be read is refused with the file, the line and the fix for every problem in it.

A freezing adds what the edit added, stage by stage: of the stages a question holds up
and the stages a stage waits for, the ones the block's list gained since the version
before. A hold on every stage counts as the stages that block declares. So a stage the
operator's ruling took off (`narrow:`, `unafter:`) stays off while the block lists it
as it did before, whatever else of the entry changed; `fold` prints a `kept off:` line
for each, with the ruling. `mark` holds it again, with `--blocks` or `--after`.

### Verifying it

A checker reads the frozen version, writes a report in the round's folder and records
its verdict itself. `next` names the pass, the file to read and the command, and
`next --schema design` prints the empty block of a design report:

```text
$ moxi rounds next alpha/ranking
round alpha/ranking   flow design   step verify-design   by checker, tier second (model-two)
pass:  full of v1: no full pass over the design is recorded
reads: ~/notes/alpha/rounds/ranking/versions/design-v1.md
write: a report in ~/notes/alpha/rounds/ranking with the block from: moxi rounds next --schema design
then:  moxi rounds verdict alpha/ranking <your report>
waits: nothing
```

````markdown
# Design check, full pass

The design holds together.

```toml rounds
kind = "design"
verdict = "publishable"
pass = "full"
by = "checker-1"
model = "model-two"
design = 1
```
````

```text
$ moxi rounds verdict alpha/ranking ~/notes/alpha/rounds/ranking/report-1.md
recorded for alpha/ranking: publishable for v1 of its design, full pass by checker-1, model model-two
report: report-1.md
sha256: 996de960feab33b9184294cfc4d33f2db74d1fb783723ba74e56f79ca7110594
standing: publishable@v1
```

A verdict is `publishable` or `fold-first`, which sends the design back with findings.
It is recorded against the version the report says its pass read, whatever was frozen
or edited since. The design's standing is the newest verdict of its current version,
and `none` once a changed design is frozen again. A design frozen again with its text
unchanged keeps the standing it had, whatever that was, and a verdict for a version
whose text is the current one's gives the standing: it belongs to the text a pass
read. Only the version before counts, so the text of an older version is a change.

### How many passes

Verification is bounded, so that passes do not feed themselves:

1. One full pass reads a design. After it, `next` asks only for re-checks, at the
   step's `recheck_model` tier. A second report of a full pass is still recorded, with
   the flag `extra-full-pass`.
2. `publishable` is final: no further pass is asked for, and a later report that sends
   the same version back is recorded and shown and changes no standing. The same holds
   for a version that stands at `lead-decision`.
3. A design frozen again with its text changed has no standing until a re-check reads
   what changed. `next` names the two frozen files to compare. One frozen again
   unchanged costs no pass, and one that was sent back stays sent back.
4. A version that changes records only needs no pass: `fold --records-only --record
   PTR`, with a pointer to where the lead wrote what changed. It is refused when the
   block's questions, its stages, what a question holds up or what a stage waits for
   differ from the version before. A question since ruled or withdrawn that the
   block leaves out is no difference: no gate reads it. The design keeps the standing
   it had.
5. After two verdicts in a row that send the design back, no pass is asked for. `next`
   says what to do instead: state the rule the findings break, write the section again
   from it and freeze the design; then `mark ROUND thread:NAME --close --record PTR`,
   or `--accept-risk`, records the lead's decision, and the design stands at
   `lead-decision`.
   The two are counted from the lead's last decision, so a design frozen again after
   one is asked for a re-check, and a second thread is decided only after two more.
   A verdict counts when it gave the design its standing: one for a version that was
   no longer current, or that already stood at `publishable` or `lead-decision`, is
   recorded and not counted. One text is counted once: a second report that sends
   back the same version, or one frozen again unchanged, is recorded and shown and
   not counted. The freezing that follows the two must change the
   design's text. The same decision run again is found, not recorded twice.

A flag is shown with its verdict and refuses nothing.

## Stages

A stage is one planned build, `B<n>`: a repo, which is a project's key, a branch, the
stages it waits for and the slices it is cut into. A design declares them:

```toml
[[stage]]
id = "B1"
repo = "alpha"
branch = "search-index"
after = []

  [[stage.slice]]
  id = "1a"
  what = "the index and its reader"
  minutes = 40
```

A stage's repo and branch never change: different work takes a new id. A stage leaves
the plan by the operator's ruling, `unstage:B<n>`, or by a publishable verdict on a
design that left it out; it does not come back, and its id is not used again.
`mark ROUND B<n> --after B<m>` makes a stage wait for more stages, which anyone may
do; only the operator takes one off, with `unafter:B<n>=B<m>`. A wait is added only
while it can still be met: `mark --after` and `fold` both refuse one for a stage that
left the plan, and one that closes a circle with the waits on record; `fold` also
refuses one for a stage its own block leaves out, unless that stage is finished.

A round whose flow does not verify its design builds one stage in a repo: `fold`
refuses a second stage in one repo, and `commission` refuses a second build in a repo
where the round has one open or finished. Such a round may also leave its stages out
of the design and declare each as it commissions it:

```text
$ moxi rounds commission alpha/typo --repo alpha --branch fix-typo --builder coder \
      --slice 1a="the heading" --minutes 10
```

The stage takes the lowest `B<n>` the round has not used. This is refused while the
repo has a stage with no open or finished build (commission that one by its id), and
while an open question holds every stage, since it would hold the new one the moment
it is declared: the operator rules, withdraws or parks the question, or narrows it.

A dropped build counts for nothing. Its stage is commissioned again under the same id,
with `--existing` on the branch the drop kept, or on a fresh branch once that one is
deleted; a worktree the drop kept is removed first. Whatever held the stage still
holds it. A repo's branch has one open build at a time, across every round: a
commission on a branch another round's open build is on is refused, naming that round.
`next` warns once a round plans five or more stages that are builds, and refuses
nothing for it: a part of the work that stands on its own can be a child round.

A worker gets one slice: one mechanism or one file group, well under an hour. `fold`,
and `commission` where it declares the stage's slices, warn of a slice given more
minutes than the project's `slice_minutes` (45 unless set), and refuse nothing for it. Before a brief goes to a worker, the lead sizes it:

```text
$ moxi rounds mark alpha/search B1 --brief briefs/1a.md
```

The brief carries a data block naming its stage and one piece of work:

```toml
stage = "B1"
slice = "1a"      # or, for a checker's brief: verify = "build" | "design" | "retro"
```

It passes when the slice is one the stage declares and its minutes are within
`slice_minutes`, or when it names one verification, and the pass is recorded with the
brief's SHA-256. A stage that declares no slices is one slice: its brief leaves out
`slice` and `verify`, and has no minutes to check. A brief that names two slices, or
both a slice and a verification, a slice the stage does not declare, one over the
limit, or neither for a stage that declares slices is refused, and nothing is
written. `show` and the round note say which slices of each open build have
a sized brief, and `next` names the ones that have none. A sizing counts for the build
of its stage that is open, or for the stage's next build when none is: a dropped
build's briefs are not its successor's.

Once a round has stages, a question may hold up some of them (`ask --blocks B1,B2`,
`narrow:Q<n>=B2`), be parked until one is finished (`park:Q<n> --until B2`), and a
ruling may apply to some (`rule --stages B1`). `next` prints a stage's steps this
version does not build as not built, with the questions that hold the stage.

### Verifying a build

A checker reads a build and records a report of `kind = "build"` with `verdict`. Its
block names the `stage` and the `head` it read, the tip of the build's branch or an
ancestor of it, so a fix committed while the report was written does not make it
unrecordable. A report by the build's builder is refused. Each `[[finding]]` becomes a
finding of the build, `B1.L1` outside the report, and the newest report that lists a
finding sets its state: `open`, `fixed` or `no-action`. A report lists every finding
the build holds as open, so none is dropped by being left out, and a finding new to the
build takes an id no earlier report of the stage used, a dropped build's included.
`verdict` prints the build's findings and what its finish still waits for; `show` and
the round note give each build's newest verdict and its findings.

A build's `finish` waits for ten gates, read at the tip being finished, and names each
one that does not hold with its number and the command that meets it:

1. its verification counts at the tip, or the operator skipped it, or the flow has none;
2. no finding of the build is open;
3. the repo's check passed at the tip, or CI's form of it is met there, or the operator
   skipped it;
4. no question holds the stage;
5. the target moved under the build in no way that owes a re-check;
6. the tip holds the target, where the repo's `check_on` is `merged` (the default) and
   wherever the check was met by CI alone;
7. the worktree, while it is there, holds no change and no untracked path (ignored ones
   are left), and the branch is still at the tip;
8. each step of your own is done at the tip, or the operator skipped it;
9. the build has work: it reads `building` or `merged`;
10. it shares no commits with another build.

No skip meets gates 9 and 10, and `finish` itself is never skipped.

The lead may mark a `minor` or `note` finding fixed without a re-check: `mark ROUND
B1.L3 --fixed --commit SHA [--commit SHA]... --no-behaviour-change`, naming every
commit of the fix, each on the build's branch. The flag is the lead's statement that
the fix changes nothing a command, hook or page reads, decides, writes, refuses or
accepts; without it, and for a `blocker` or `major`, the mark is refused and the fix
gets a re-check of its diff. The build's `verify-build` step is done once its newest
report is `publishable` and every commit since the head it read is named by a mark or
is a merge of the target that the drift rule below accepts. Otherwise `next` says a
re-check is owed, with the range it reads, at the step's re-check tier.

A report step of your own (`does = "report"` in the flow) takes a report of `kind =
"step"` that names the step in `step`, and reads one build by the same rules of
`stage`, `head` and author. Its findings are findings of the build, each kept with the
step that raised it, and hold the finish like any other. A step's report lists every
open finding its own step raised and none another report raised; the build's own
report lists and sets every open finding, a step's included. The step is done once its
newest report is `publishable` and counts at the tip by the rule above, read on that
step's reports alone; a report of the build does not do a report step, nor a step's
report the build's verification.

Work that can't be done without the operator, such as a check on his phone, is a
`[[wait]]` in the report. When the report is recorded each is an open waiting item of
the round, `B1.W1`, at once. It does not hold the build's finish; it holds the round's
close, and `list --waiting` lists it, until `mark ROUND B1.W1 --done --record PTR` names
the report, commit or message that shows it done; PTR is a pointer, or the id of a commit
the build's repo holds, recorded whole. A later report of the same build may list an item
again with the same sentence, and it is the same item; a new item takes an id no earlier
report of the stage used.

An as-built choice the operator would notice is a `[[decision]]`, with a `weight`
(`major` or `minor`) and whether it `forecloses` a capability (then it is major). Each
is a record of the round, `B1.M1`, with the options `accept` and `reject`; it holds no
stage. A later report may list it again as heavy or heavier, never lighter. He rules it
with `rule ROUND B1.M1=accept|reject --record PTR`; a rejected one is also a waiting
item, `B1.M1`, until `mark ROUND B1.M1 --done --record PTR` names the fix. A report of
`kind = "majors"`, by a checker who built none of the round, declares decisions no
build report carries, each with its `stage`, and names the builds it covers in
`stages`. `list --waiting` and a project's export list each unruled major.

A round's export has two views. The docket (`--view docket`) is its open questions and,
after them, the parked ones. The ratification (`--view ratify`) is the unruled decisions
of the builds that are finished when it runs, named under `covers`; what it puts to the
operator is the project's `ratify` setting: `majors` (the default) each major decision,
`majors+minors` the minor ones too, drawn as one group that lists their ids and is
still ruled one id at a time, and `off` none. The major decisions of builds not yet
finished are listed under `later`, and come in an export run once their build is
finished. A build answers only for the decisions its own reports declared: a dropped
build counts for nothing, so a decision only a dropped build declared is in neither
list until a report of a later build of its stage lists it again. A decision that
forecloses a capability is an entry of its own everywhere:
`alone` in the data, its own heading in the markdown, never in a group.

A verdict records the head it read and that head's merge-base with the target. When the
target moves on, `show` reads whether the move touches the build: `target moved 4
commits under B2; 0 paths meet`, or `...; 2 paths meet: re-check owed`. A re-check is
owed when the target changed a path the build's branch changes against it, or a shared
path: the common lockfiles (`uv.lock`, `package-lock.json` and the like, in any folder)
and the globs of the repo's `shared_paths`. A glob with no `/` matches a file's name in
any folder, one with a `/` the whole path. The target merged into the branch after the
verdict needs no re-check when the branch gained nothing else since and the merge
changes no path of the build and no shared path.

## Questions

A question is one sentence on one line. It may offer two to four options, each a line
under a short key; a key keeps its line, and a retired key is never used again, so to
correct a line retire its key and add the right one under a new key. A question is open,
parked, ruled, withdrawn or moved.

A round's question holds up every stage of the round, or the stages its `blocks`
names, until the operator rules on it. One that holds up no stage still keeps the round
from closing. Only he lets go of it: his answer, `park:`, `narrow:` and `withdraw:` are
rulings. `mark` changes a question only towards holding up more, and reopens a parked
one. A question comes from `ask` or from the design's block, where `fold` adds it.

A project's list holds questions that have no round yet, and they hold up nothing.
`--adopt` brings one into a round, where it holds up every stage. `--move` puts a
round's question on the list, which ends what it holds up, so it needs the operator's
pointer.

## Rulings

A ruling is a plain sentence or a few that read on their own. A round's rulings are
`C<n>`. A standing ruling, `S<n>`, goes in the project's register and outlasts the round;
`--scope` names the repos and paths it applies to, as `KEY` or `KEY:GLOB`, several
separated by commas.

A second ruling of one thing is refused without `--supersedes`, and `revoke:` takes a
ruling back. A question whose answer was revoked is open again.

Every ruling is printed as it was recorded:

```text
Recorded as C1: No, live notes only
in answer to Q1: Should search read archived notes too?
show him this under "Recorded as" in your next message, then:
  moxi rounds mark alpha/search C1 --shown --record <pointer to that message>
```

Until `mark --shown` names it, a ruling is listed by `list --waiting` among the rulings
not yet shown to him, with the project's own and its rounds' together. `rule PROJECT
readback=TIME` records instead that he read that list through TIME, the time of the last
ruling on it, and covers every ruling of the project recorded up to then.

## The trace check

A project that sets `trace = true` has each design say what became of every ruling it
follows. The design's block carries one row for each: a standing ruling the round was
opened with (`open --inherits S1,S2`), a ruling of the round that says what is built,
and each commitment the block makes.

```toml
[[trace]]
id = "S1"
as = "carried"          # carried | amended | cut
where = "the index"
```

A `carried` row says where in the design; an `amended` or `cut` row carries a pointer
to where the operator said so, or names an open question of the round. `fold` checks
the rows as it freezes the version and records the result. A version whose rows fail
is frozen all the same, and `verify-design` waits for one whose rows pass. A row that
cites a ruling since replaced or taken back is stale: `next` warns of it, and it holds
up nothing. A standing ruling the round was opened with stands for the ruling as it is
now: once it is replaced, the ruling that stands in its place is owed the row, and
once it is taken back it is owed none. The setting is read at each freezing, so turning it on or off reaches a
round at its next version.

## Closing

No command closes a round. After every write to a round, rounds asks whether its close
still waits for anything, and the write that leaves nothing closes it. In a flow with
no `build` step the close waits for:

- every step of the flow to be done or skipped;
- every question to be ruled or withdrawn, the parked ones included;
- every stage to have left the plan, since this version builds none.

```text
$ moxi rounds rule alpha/ranking Q1 --chose b --record comms:21
Recorded as C1: Only an exact title match
in answer to Q1: Should a title match always rank above a body match?
show him this under "Recorded as" in your next message, then:
  moxi rounds mark alpha/ranking C1 --shown --record <pointer to that message>
closed alpha/ranking: nothing is left for its close to wait for
$ moxi rounds mark alpha/ranking C1 --shown --record comms:22
alpha/ranking C1: shown   record: comms:22
$ moxi rounds list --closed
alpha/ranking   closed at 2026-11-10T10:00:33Z   flow design   design: publishable@v1   open questions: 0   parked: 0   rulings not shown: 0
```

The ruling above answered the round's last open question, so its write closed the
round. Marking the ruling as shown is still recorded on the closed round, and until it
is, the list counts it under `rulings not shown`. `show` gives an open round's `close waits for:` line, and `next` gives it for a round
at no step. The verb whose write closes a round says so in its last line, `closed
ROUND: ...`, whichever verb it is: `open` too, for a round on a flow with no steps. A
closed round is listed by `list --closed`, and its note says when it closed. Like a dropped
one it takes no new question and no new ruling; a correction of one of its rulings is
still recorded, and its rulings are still marked as shown.

A round whose flow has a `build` step does not close itself in this version, even once
its stages are finished: `show` says so, and such a round ends by `drop`.

## Dropping a round

`drop --by operator` ends any round, with a pointer to where he said so. `drop --by lead`
ends only a round that holds none of the operator's acts: no ruling, no question asked
with his pointer, no opening on his go. The round's open and parked questions go to the
project's list as they are, or with `--withdraw-questions`, which is the operator's
alone, are withdrawn. Its open builds end with it, each branch's tip recorded, and
the lead's drop is refused while one of them is not his to drop (below).

A dropped round takes no new question and no new ruling. A correction of one of its
rulings is still recorded, and its rulings are still marked as shown.

`drop ROUND B<n>` drops the open build of one stage, and the round goes on. Who may
drop it is read from git at the drop. The lead drops a build with no work a drop could
lose: its branch is gone, or it is `empty` and holds no commit of its own (nothing but
merges of the target), and it shares no commit with another build. Every other build,
and one git can't read, is the operator's. `show` and `list --waiting` name, for each
build to look at, who drops it and the command. The drop records the branch's tip and
deletes no branch, so the work can be commissioned again. The build's worktree and
scratch folder are removed under the removal rule below, and one it keeps is named. No
question or hold changes, and the stage stays owed.

**The removal rule.** rounds removes a build's folder only when both halves hold, read
as it removes. Ownership: the build was made on this machine; the folder is at the place
rounds builds for it from the roots as config stands now, with no link below the root;
and it carries the marker naming the build, which for a worktree git must also list on
the build's branch, its `.git` file naming the repo's own worktree folder. No lost
work: git, run with no `GIT_` variable and with the settings that hide work overridden,
shows no change, untracked path or hidden path in the worktree; every ignored path in it
has a part one of the repo's `disposable` globs matches (by default only environments
and caches, such as `.venv`, `__pycache__` and `node_modules`); every commit its `HEAD`
and its own refs name is on a branch, tag or remote ref that stays; no submodule is
checked out in it; it is not locked; and no other filesystem is mounted inside either
folder. The scratch folder is disposable whole. The second half is read again just
before the removal. A worktree goes by `git worktree remove`, never forced; a scratch
folder is moved into rounds' trash and deleted there. A folder either half keeps is
left, named with the check that kept it, for a later sweep. Finish's worktree gate reads
the worktree with the same git read, so an untracked file stays a change whatever the
user's git config says.

**The sweep of leftover folders.** `moxi rounds drop --leftovers` finds the folders that
ended builds left on this machine: each worktree and scratch folder that the record of
a finished or dropped build names and that is still there. A folder no record names is
never looked at. It reads the removal rule for each, and then three rules of its own,
since it runs later and unattended: no log of any round is unreadable, as one might
record an open build at the place; no process of yours has its working folder, an open
file, a mapped file, an argument or an environment value in it (read through psutil);
and its marker was written, and nothing in it changed, more than `sweep.grace_hours`
(2) ago. A worktree that a finish with no merge kept stays until its branch's tip is on
the target. It prints each folder with whether it would go or the check that keeps it,
and for a worktree kept for its work, the paths that are work.

Every sweep is a dry run until `sweep.armed` is true in rounds' config. `--remove`
while unarmed prints the dry run and is refused; once you have read it, arm it, and
`--remove` takes each folder every rule lets go, reading the rules again just before
each removal. A worktree goes by `git worktree remove` and its branch stays; a scratch
folder is moved into the trash and deleted there. A run starts no removal after 60
seconds, and one run goes at a time. The sweep writes no round's log; each removal,
trash move and trash delete is a line of `sweep.jsonl` in rounds' state directory. A
trash entry the sweep moved and did not delete, because a run stopped, is deleted by
the next run; anything else in the trash is reported and left. The doctor's
`leftovers` row is the same dry run.

With turns installed, `sweep.at_start = true` and the sweep armed, each session's start
(and resume, clear or compaction) starts the sweep in a detached process of its own,
unless the last removing run began less than ten minutes ago; the session waits on
nothing. Without turns the sweep runs only by hand and in the doctor.

A build is `unclear` when its target was fast-forwarded onto its work and nothing on
record says the branch held that work first. The operator either drops it or credits
it: `rule ROUND credit:B<n> --record PTR` records the head as the build's work, and the
build reads `merged`, owing its check, verification and ratification like any build
merged by hand. A credit is taken only for that kind of `unclear`, and only when the
head changes something against the build's base. `revoke:` takes it back.

## The store

Each project has one rounds folder: its `dir` setting, by default `rounds` in the
project's notes folder. The logs live there, beside the documents they describe, so they
are backed up and pushed with them.

```text
<dir>/project.jsonl          the project's log: standing rulings and the project's questions
<dir>/rulings.md             generated: the register
<dir>/questions.md           generated: the project's questions
<dir>/<slug>/round.jsonl     the round's log
<dir>/<slug>/round-note.md   generated: the round's state and history
<dir>/<slug>/design.md       written by the lead; rounds reads its data block
<dir>/<slug>/versions/design-v<N>.md   the design as `fold` froze it
<dir>/<slug>/*.md            reports, written by checkers; rounds reads their data block
```

A log is only appended to. The three generated files are written again whole after every
write to their log, so an edit to one is lost; keep your own notes on a round in another
file of its folder. A folder of the rounds folder that holds a `round.jsonl` is a round,
however it got there: one that arrived by git is listed as soon as it is there.

A rounds folder belongs to one project. Two projects whose folders are one folder, once
every link on the way is followed, are both refused until one is given a folder of its
own.

`open` refuses a folder that holds a `round-note.md` and no log: the round there belongs
to another tool, and this one takes another slug.

rounds keeps two things outside the rounds folder (`moxi paths rounds` names the
directories):

- `index.db` in its data directory, a cache of what the logs say so that a listing need
  not read every log. Deleting it loses nothing: `moxi rounds list` builds it again from
  the logs.
- Lock files in its state directory, `locks/projects/<key>.lock` for a project's log and
  `locks/rounds/<key>/<slug>.lock` for a round's, and `locks/sweep.lock` for the sweep.
  They hold nothing but the pid of the process writing, and are never in a rounds
  folder, so none is backed up or pushed.
- `sweep.jsonl` in its state directory: a line for each folder the sweep of leftover
  folders removed and each trash entry it moved or deleted, kept to about a megabyte and
  one older file. It is this machine's alone, and `sweep.started` beside it holds when
  the last removing run began.

A log that two machines both appended to, or one with a line this version can't read, is
not used at all: every verb on it refuses and names the file, the line and the way back.
When a log's earlier lines changed outside this machine, it is read as it stands and
`show` says since when.

## Config

`moxi config rounds` shows every key, and `moxi config rounds init` writes the file,
`config.toml` in rounds' config directory. This version reads:

| Key | Default | What it does |
|---|---|---|
| `defaults.dir` | `"rounds"` | a project's rounds folder: absolute, or relative to the project's notes folder |
| `defaults.flow` | `"full"` | the flow a round opens with when `--flow` names none |
| `flows` | `{}` | your own flows by name, each a list of steps; one named like a built-in flow replaces it |
| `tiers` | `["fix", "small", "full"]` | the flows from lightest to heaviest; each must be a flow |
| `models` | `{}` | the model each tier names, per harness or one for all; see below |
| `defaults.trace` | `false` | run the trace check when a design is frozen |
| `defaults.idle_days` | `14` | days a build's head may go without a commit before the read API's `builds` gives the build as idle |
| `projects` | `{}` | per-project tables, `[projects.<key>]`, which may set `dir`, `flow`, `trace` and `idle_days` for that project |

```toml
[projects.alpha]
dir = "~/work/alpha-rounds"
flow = "small"
```

The built-in flows are `fix`, `small` and `full`. `open` records the flow's steps in the
round, so a later edit of the flow does not change a round already open until `mark
--flow` moves it. Changing `dir`
moves no folder: rounds looks in the new place, and the rounds stay where they were until
you move them.

The file also takes `worktree_dir`, where `commission` puts a build's worktree, and in
a project's table `into`, the branch its builds land on, `brief`, lines added to every
commission block for a build in it, and `check`, `{ command = "...", timeout_s = N }`,
the command `moxi rounds check` runs at a build's tip. The command is split as a shell
would split it and run with no shell; its words may hold `{round}`, `{stage}`,
`{repo}`, `{root}`, `{branch}`, `{tip}` and `{into}`, filled in for the build. With no
`timeout_s` the run has no time limit.

A project whose build branches run CI may also declare `ci`, so that a green CI run at
the tip, with a short local run of what CI can't do, stands in for the full check:

```toml
[projects.alpha]
check = { command = "make check", timeout_s = 3600 }
ci = { workflows = [".github/workflows/ci.yml"], remote = "origin",
       local = { command = "./scripts/check-local", timeout_s = 900 } }
```

`workflows` names each workflow by its file's path in the repo. `local` is required:
the command, or `"none"` written out where CI does all of it. `remote`, when set, is
where `check` pushes the branch before it reads. CI is read with `status` and `ids`,
two commands printing JSON, by default `gh run list --commit {tip} --json ...` and `gh
workflow list --all --json id,path`, each bounded by `status_timeout_s` (60). A
workflow reads `green`, `pending`, `red` or `absent` at the tip; the check step is
done once every workflow reads green there, the workflows named are the ones the
setting names now, and the local run passed at the tip with the command it declares
now. The full check, run with `check --full`, meets the step whatever CI reads. A
`ci` in a project with no `check` is refused. `defaults.ratify` (and a project's
`ratify`) picks what a round's ratification view puts to the operator. It also takes
`defaults.check_on`, `defaults.dormant_days`, `defaults.slice_minutes` and
`defaults.sitting_at`, `defaults.disposable` (the name globs of ignored paths a
worktree's removal may lose; an entry that is empty, holds a `/`, or would match every
name is refused), and in a project's table `finish`, `keep_branch` and `lock_ref`. They are checked when the file is read, and nothing in this version acts on
them. The `[sweep]` table is this machine's and set in no project's table: `armed`
(false: every sweep of leftover folders is a dry run), `at_start` (false) and
`grace_hours` (2, at least 1). A project's `shared_paths` lists globs of paths every build of its repo shares,
beside the common lockfiles: a change to one on the target owes a build a re-check.

`models` maps each tier a step names to a model per harness. The order the tiers are
written in is their order, strongest first. `next` prints beside a tier every
harness's model in the order written, as `tier second (claude: model-two, codex:
model-b)`; the bare name, `tier second (model-two)`, when the tier has one harness or
all its harnesses name the same model; and the tier alone when the table has no entry
for it:

```toml
[models]
first  = { claude = "model-one", codex = "model-a" }
second = { claude = "model-two", codex = "model-b" }
```

A tier may also be written as one model, `third = "model-three"`. It names that model
for every harness: `next` prints `tier third (model-three)`, and a report by
`model-three` is placed in `third`, as with `third = { claude = "model-three" }`.

A report names the model that wrote it. `verdict` looks that name up under every
harness and places it in the strongest tier that lists it. When that tier is written
after the one the step names for the pass (`model` for a full pass, `recheck_model`
for a re-check, or `model` when the step has none), the verdict is recorded with the
flag `model-below-tier`. The flag is shown with the verdict and refuses nothing. A
model in no tier gets no flag.

rounds reads no environment variable but the ones that place its directories,
`MOXI_ROUNDS_HOME` and the rest, and `MOXI_HOME`.

## The read API

Other modules read rounds in process through interface 1, reached with
`moxi.modules.load("rounds", interface=1)`. Each of the first four calls gives the data
its command prints with `--json`:

| Call | Gives |
|---|---|
| `rounds(project=None, status="open")` | the rounds of every project or of one, open or `"closed"`: `moxi rounds list --json` |
| `round(name)` | one round's whole state: `moxi rounds show ROUND --json` |
| `waiting()` | what waits on the operator: `moxi rounds list --waiting --json` |
| `questions(name, view=None)` | the questions of a round or a project: `moxi rounds list NAME --questions --json` |
| `builds(repo=None)` | the open builds of every project or of one repo, each with its state read from git now, whether it is idle, and the builds it shares commits with; no command prints it |

A call never writes, the index included. A round whose log can't be read is returned as
unreadable, with the file and the line, and is never left out. What a command refuses,
its call raises as a `moxi_rounds.errors.RoundsError`, with `reason`, `fix` and
`exit_code`.

## Doctor

`moxi doctor rounds` checks, besides the module, its settings and the index file:

| Row | Says |
|---|---|
| `git` | fails when git is missing, won't say its version, or is older than 2.17 |
| `rounds folders` | warns for each project whose rounds folder can't be worked out or is another project's too, with the setting that fixes it |
| `logs` | fails for each log that can't be read, with the file, the line and the way back |
| `index` | warns when the index says something else than the logs do; `moxi rounds list` brings it up to them |
| `finish locks` | warns for each finish lock older than an hour in a project's repo, with its holder and the one git command that clears it, for you to run after asking the holder |
| `leftovers` | the sweep's dry run: warns with each folder an ended build left on this machine, whether it would go or the check that keeps it, and the command that removes what would go |

The doctor only reads.

## Notes

rounds works without the notes module. A project's rounds folder is then its `dir` when
that is absolute, or `dir` inside the notes folder the project's own record names
(`moxi projects set KEY --notes-root DIR`); a project with neither is refused, with both
fixes. With the `notes` extra (`moxi-notes`), a project that names no notes folder gets
the one notes gives it by default.

rounds works without the turns module too. With turns, rounds' listener on a session's
`ready` event runs the sweep of leftover folders when `sweep.at_start` is on; rounds
imports nothing of turns, and turns finds the listener by its entry point.

## Install

Linux, Python 3.12 or later, [uv](https://docs.astral.sh/uv/) and git 2.17 or later.

```sh
uv tool install moxi
moxi install rounds
```

The first line installs `moxi`, Moxi's core, which provides the `moxi` command; the
second adds rounds beside it, with the projects module it needs, and offers its optional
extra, `notes`. The repository's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi` command and
lists the other modules.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/rounds/LICENSE).
