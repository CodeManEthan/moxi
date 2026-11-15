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
`open`, `fold`, `verdict`, `ask`, `rule`, `mark`, `next`, `show`, `list` and `drop`.

It does not yet do the rest of a round:

- builds, their worktrees and their checks;
- verifying a build and finishing it, which puts it where it goes;
- decisions a build records for the operator to ratify;
- the retrospective, and the numbers of a round that ended;
- answers prepared ahead for a sitting with the operator;
- parent rounds.

So a round moves through its design, the design's verification and its questions, and
stops where its first build would start: `next` prints each later step as not built. A
stage is planned and never built. A round whose flow has no `build` step closes itself
(see Closing); any other ends only by `drop`. Config keys that belong to the parts
above are declared and checked, and nothing reads them yet (see Config).

## Commands

A round's name is `<project>/<slug>`: a project's key from `moxi projects`, and a slug of
lower-case letters, digits and `-`.

| Command | Does |
|---|---|
| `moxi rounds open ROUND --description TEXT [--flow NAME] [--record PTR] [--inherits S<n>[,...]]` | open a round: make its folder and record what it is for and its flow; `--record` points to where the operator gave the go, and `--inherits` names the standing rulings it starts from |
| `moxi rounds next ROUND [--json]` | what is done next: the step the round is at, who does it, what they read, the command that completes it and what it waits for |
| `moxi rounds next --schema design\|build\|step\|majors\|retro` | the empty data block of a report of that kind, a comment on each key |
| `moxi rounds fold ROUND [--records-only --record PTR]` | freeze `design.md` as the next version and record what its data block declares |
| `moxi rounds verdict ROUND REPORT.md` | record the verdict of a report on the design, run by whoever wrote the report |
| `moxi rounds ask ROUND TEXT --from PTR [--blocks B<n>[,...]\|none] [--option K=TEXT ...]` | put a question to the operator in a round, as `Q<n>`; it holds up every stage unless `--blocks` names the ones it holds up, or `none` |
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
| `moxi rounds rule ROUND flow:NAME --record PTR` | move the round to another flow, whichever it is |
| `moxi rounds rule ROUND unstage:B<n> --record PTR` | take a stage out of the plan |
| `moxi rounds rule ROUND unafter:B<n>=B<m> --record PTR` | make a stage wait for one stage fewer |
| `moxi rounds mark ROUND Q<n> --blocks B<n>[,...]\|all`, `mark ROUND Q<n> --reopen` | make a question hold up more stages, or reopen a parked one |
| `moxi rounds mark ROUND --flow NAME` | move the round up to a heavier flow, or to its own flow's steps as the config has them now |
| `moxi rounds mark ROUND B<n> --after B<m>[,...]` | make a stage wait for more stages |
| `moxi rounds mark ROUND thread:NAME --close\|--accept-risk --record PTR` | the lead's decision on a design that was sent back twice in a row |
| `moxi rounds mark ROUND\|PROJECT C<n>\|S<n>[,...] --shown --record PTR` | say that rulings were shown to the operator as recorded |
| `moxi rounds show ROUND [--json]` | one round: status, flow, the step it is at, what its close waits for, its design and stages, questions by state, rulings, verdicts and history |
| `moxi rounds list [PROJECT] [--closed] [--json]` | the open rounds, each with its step and its counts, or with `--closed` the ended ones, each with how and when it ended |
| `moxi rounds list [PROJECT] --waiting [--json]` | what waits on the operator: open questions, parked ones, rulings not yet shown |
| `moxi rounds list ROUND\|PROJECT --questions [--view docket] [--json] [--out DIR]` | the questions written out as markdown; `--out` writes `questions.md` and `questions.json` into DIR |
| `moxi rounds drop ROUND --by operator\|lead --record PTR [--withdraw-questions]` | end a round short of its work |

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
refuses one for a stage its own block leaves out, unless that stage is finished. In a
round whose
flow does not verify its design, a second stage in one repo is refused.

Once a round has stages, a question may hold up some of them (`ask --blocks B1,B2`,
`narrow:Q<n>=B2`), be parked until one is finished (`park:Q<n> --until B2`), and a
ruling may apply to some (`rule --stages B1`). This version builds no stage: `next`
prints a stage's steps as not built, with the questions that hold the stage.

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

A round whose flow has a `build` step waits for a finished stage, which this version
does not build, so it does not close: `show` says so, and such a round ends by `drop`.

## Dropping a round

`drop --by operator` ends any round, with a pointer to where he said so. `drop --by lead`
ends only a round that holds none of the operator's acts: no ruling, no question asked
with his pointer, no opening on his go. The round's open and parked questions go to the
project's list as they are, or with `--withdraw-questions`, which is the operator's
alone, are withdrawn.

A dropped round takes no new question and no new ruling. A correction of one of its
rulings is still recorded, and its rulings are still marked as shown.

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
  `locks/rounds/<key>/<slug>.lock` for a round's. They hold nothing but the pid of the
  process writing, and are never in a rounds folder, so none is backed up or pushed.

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
| `projects` | `{}` | per-project tables, `[projects.<key>]`, which may set `dir`, `flow` and `trace` for that project |

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

The file also takes `worktree_dir`, `defaults.ratify`, `defaults.check_on`,
`defaults.idle_days`, `defaults.dormant_days`, `defaults.slice_minutes` and
`defaults.sitting_at`, and in a project's table `into`,
`check`, `finish`, `shared_paths`, `keep_branch`, `lock_ref` and `brief`. They are
checked when the file is read, and nothing in this version acts on them.

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
`moxi.modules.load("rounds", interface=1)`. Each call gives the data its command prints
with `--json`:

| Call | Gives |
|---|---|
| `rounds(project=None, status="open")` | the rounds of every project or of one, open or `"closed"`: `moxi rounds list --json` |
| `round(name)` | one round's whole state: `moxi rounds show ROUND --json` |
| `waiting()` | what waits on the operator: `moxi rounds list --waiting --json` |
| `questions(name, view=None)` | the questions of a round or a project: `moxi rounds list NAME --questions --json` |

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

The doctor only reads.

## Notes

rounds works without the notes module. A project's rounds folder is then its `dir` when
that is absolute, or `dir` inside the notes folder the project's own record names
(`moxi projects set KEY --notes-root DIR`); a project with neither is refused, with both
fixes. With the `notes` extra (`moxi-notes`), a project that names no notes folder gets
the one notes gives it by default.

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
