# moxi-rounds

Moxi's record of a piece of work: its design and the verdicts on it, its questions, the
operator's rulings on it, and each project's standing rulings.

A round is one proposal, from its design to the step that puts what was built where it
goes. rounds is for anyone who directs agents that write code in git repos: one person,
here called the operator, with a lead agent that runs the round and workers that build
and check. Everything the operator decides is recorded as the operator's, with a pointer
to where it was said, and is shown back to the operator as it was recorded.

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

rounds starts no agent and registers no git hook. It writes the open questions out as
markdown and as data. With the `ui` extra it also has a page in the browser UI shell,
which shows every open round and acts on nothing (see The page).

## What this version does

This version takes a round from its design to its close: the round's log, its questions
and rulings, the frozen versions of its design and the verdicts of the reports that
verify it, and the stages the design plans. Each stage is built on a branch of its own,
with a worktree and a scratch folder; checked by the repo's check or by CI; verified,
with findings, waiting items and decisions; and finished, where the repo's finish
actions merge or push it and `finish` then removes what the build left. The operator ratifies the decisions that
reached the target, and the round closes itself with its numbers and, where its flow
has one, a retrospective. Beside the rounds are a project's own list of questions, its
register of standing rulings, the files generated from them, and the sweep of folders
ended builds left. Its verbs are `open`, `fold`, `verdict`, `commission`, `check`,
`finish`, `ask`, `rule`, `mark`, `next`, `show`, `list` and `drop`.

A stage's `build` step is ready once it is commissioned and done once git reads its
branch as holding work (`building`) or merged; `next`, `show` and `list` read git for
it, and for every other state of a build.

rounds ships one skill, `moxi-rounds`, which `moxi install rounds` offers to place for
Claude Code and Codex. It holds the judgment the gates leave to the lead and the
checkers: when a design needs another pass, which tier does which step, when a decision
is major, how to cut slices, how to write a ruling or a question that reads cold, and
what a re-check reads. The commands themselves come from `next`, the refusals and
`--help`.

This version does not yet do:

- answers prepared ahead for a question, and the suggestion of a sitting with the
  operator;
- parent and child rounds: a design may plan a stage that stands for a child round,
  and `next` prints that stage's steps as not built;
- `list --dormant`, the rounds that have gone quiet;
- acting from the page: answering a question, a decision or a job there, or dropping a
  build. The page reads only, and the operator still answers through the round's lead.

`defaults.dormant_days` and `defaults.sitting_at` belong to those parts: they are
declared and checked, and nothing reads them yet (see Config).

## Commands

A round's name is `<project>/<slug>`: a project's key from `moxi projects`, and a slug of
lower-case letters, digits and `-`.

| Command | Does |
|---|---|
| `moxi rounds open ROUND --description TEXT [--flow NAME] [--record PTR] [--inherits S<n>[,...]] [--lead NAME]` | open a round: make its folder and record what it is for and its flow; `--record` points to where the operator gave the go, `--inherits` names the standing rulings it starts from, and `--lead` the agent who leads it (see A round's lead) |
| `moxi rounds next [ROUND] [--json]` | what is done next: the step the round is at, then the first step not done of each other stage and each other step of the round's own that is ready, each with who does it, what they read, the command that completes it and what it waits for; with no round, in a build's worktree, the round of the open build on the branch checked out there |
| `moxi rounds queue [--repo KEY] [--resume]` | show each queue project's queue, its lander, a pause, the running batch and the members in the order they are taken; start a lander where a member waits and none runs; with `--resume`, lift a pause |
| `moxi rounds land --repo KEY` | run a queue project's lander; `finish`, `next` and `queue` start it, and it exits at once when another holds the repo's lock |
| `moxi rounds next --schema design\|build\|step\|majors\|retro` | the empty data block of a report of that kind, a comment on each key |
| `moxi rounds fold ROUND [--records-only --record PTR]` | freeze `design.md` as the next version and record what its data block declares |
| `moxi rounds verdict ROUND REPORT.md` | record the verdict of a report on the design, on a build or on a build for a report step of your own, or a majors or retro report, run by whoever wrote the report |
| `moxi rounds ask ROUND TEXT --from PTR [--blocks B<n>[,...]\|none] [--option K=TEXT ...]` | put a question to the operator in a round, as `Q<n>`; it holds up every stage unless `--blocks` names the ones it holds up, or `none` |
| `moxi rounds ask ROUND TEXT --build B<n> [--blocks B<n>[,...]\|none]` | put a question that came up in the open build of a stage: a builder tells you, and you ask it; it comes from that build at its branch's tip, and is listed as asked by you for the builder |
| `moxi rounds ask PROJECT TEXT --from PTR [--option K=TEXT ...]` | put a question on the project's list, as `P<n>`, where it holds up nothing |
| `moxi rounds ask ROUND\|PROJECT ID [--option K=TEXT ...] [--retire K ...]` | add options to a question on record, or retire them |
| `moxi rounds ask ROUND --adopt P<n> [--record PTR]` | bring a question of the project's list into the round |
| `moxi rounds ask PROJECT --move ROUND:Q<n> [--record PTR]` | put a round's question on the project's list |
| `moxi rounds rule ROUND --text TEXT --record PTR [--reason TEXT] [--stages B<n>[,...]]` | record a ruling of the round, as `C<n>`; `--stages` names the stages it applies to, when not all |
| `moxi rounds rule ROUND --standing --text TEXT --record PTR [--scope SPEC]`, `rule PROJECT --text TEXT --record PTR [--scope SPEC]` | record a standing ruling in the project's register, as `S<n>` |
| `moxi rounds rule ROUND\|PROJECT Q<n>\|P<n> (--text TEXT \| --chose K) --record PTR` | answer a question |
| `moxi rounds rule ROUND\|PROJECT park:Q<n> --record PTR [--until round:ROUND]` | park a question, so it is not put to the operator until it is reopened; `--until` names another round it waits for |
| `moxi rounds rule ROUND park:Q<n> --record PTR --until B<n>` | park a question until a stage is finished |
| `moxi rounds rule ROUND narrow:Q<n>=B<n>[,...]\|none --record PTR` | make a question hold up fewer stages, or nothing |
| `moxi rounds rule ROUND\|PROJECT withdraw:Q<n> --record PTR` | withdraw a question |
| `moxi rounds rule ROUND\|PROJECT ... --supersedes C<n>\|S<n>` | replace an earlier ruling of the same thing |
| `moxi rounds rule ROUND\|PROJECT revoke:C<n>\|S<n> --record PTR` | take a ruling back and put nothing in its place |
| `moxi rounds rule PROJECT readback=TIME --record PTR` | record that the operator read the list of rulings not yet shown to the operator through TIME |
| `moxi rounds rule ROUND skip:STEP[:B<n>] --record PTR` | say that a step is not done, for the round or for one stage |
| `moxi rounds rule ROUND skip-action:B<n>=ID --record PTR` | say that the finish action `ID` is not run for one stage: `finish` treats it as done and writes no record of it; the way out of a command that ran but whose result was lost, or that can't pass again |
| `moxi rounds rule ROUND flow:NAME --record PTR` | move the round to another flow, whichever it is |
| `moxi rounds rule ROUND credit:B<n> --record PTR` | credit to a stage's open build what its target gained by a fast-forward no reading of the build's explains: the build then reads `merged`, until `revoke:` takes the credit back |
| `moxi rounds rule ROUND B<n>.M<k>=accept\|reject --record PTR [--text TEXT]` | rule a decision a build's report declared; a rejected one is a waiting item until its fix is marked done |
| `moxi rounds rule ROUND unstage:B<n> --record PTR` | take a stage out of the plan |
| `moxi rounds rule ROUND unafter:B<n>=B<m> --record PTR` | make a stage wait for one stage fewer |
| `moxi rounds mark ROUND Q<n> --blocks B<n>[,...]\|all`, `mark ROUND Q<n> --reopen` | make a question hold up more stages, or reopen a parked one |
| `moxi rounds mark ROUND --flow NAME` | move the round up to a heavier flow, or to its own flow's steps as the config has them now |
| `moxi rounds mark ROUND --lead NAME` | record the agent who leads the round now; naming the lead it has already writes nothing |
| `moxi rounds mark ROUND B<n> --after B<m>[,...]` | make a stage wait for more stages |
| `moxi rounds mark ROUND B<n> --brief FILE` | size a worker's brief before the worker gets it: one slice of the stage within `slice_minutes`, or one verification, or the whole of a stage that declares no slices |
| `moxi rounds mark ROUND B<n>.L<k> --fixed --commit SHA... --no-behaviour-change` | the lead's mark of a minor or note finding fixed, naming every commit of the fix; run again with the same commits, it writes nothing |
| `moxi rounds mark ROUND B<n>.W<k> --done --record PTR` | a waiting item is done, with a pointer to the report, commit or message that shows it |
| `moxi rounds mark ROUND B<n>.M<k> --accept\|--reject --record PTR` | the lead's settlement of a minor decision that is not the operator's to rule; a reject owes a fix, a waiting item until `--done` names it |
| `moxi rounds mark ROUND B<n>.M<k> --major --record PTR` | the lead's raise of a decision to major; a settlement of it lapses |
| `moxi rounds mark ROUND thread:NAME --close\|--accept-risk --record PTR` | the lead's decision on a design that was sent back twice in a row |
| `moxi rounds mark ROUND\|PROJECT C<n>\|S<n>[,...] --shown --record PTR` | say that rulings were shown to the operator as recorded |
| `moxi rounds mark ROUND B<n> --resync` | read a build's base again from git: moved only after the target's history was rewritten under it |
| `moxi rounds mark ROUND REPORT.md --reread` | read a recorded report again and add the findings, waiting items and decisions its verdict's record lacks; never removes or changes one |
| `moxi rounds mark ROUND --restamp --record PTR` | re-stamp the design's current version with the digest of its frozen copy, when an edit outside the round rewrote the copy and `design.md` alike; changes no standing, and an ended round takes it too |
| `moxi rounds commission ROUND --repo KEY --branch NAME --builder NAME [--slice ID=TEXT --minutes N]...` | in a flow that does not verify its design: declare a stage under the lowest `B<n>` not used, and commission it as below |
| `moxi rounds commission ROUND B<n> --builder NAME [--record PTR] [--existing]` | build a stage: cut its branch from the target, add its worktree under `worktree_dir` as `<repo>/<branch>` and its scratch folder in rounds' cache directory, each with a marker naming the build, copy the repo's `private_files` into the worktree (see Config), and record the build; `--existing` builds on a branch that is there, one strictly inside or around another open build's branch included, and refuses one that overlaps another open build's or that a recorded landing already brought onto the target. It prints the commission block, which goes first in the builder's brief, then tells you which of the block's rulings the operator has not been shown |
| `moxi rounds check [ROUND B<n>] [--step NAME \| --full] [--timeout-s N] [--no-wait]` | run the repo's declared check for a build at its tip, in its worktree, and record the run: its commit, that commit's merge-base with the target, the argv, its exit code and duration. The worktree must be clean on the build's branch and the build must have work. Its output goes to `check-<n>.log` in the build's scratch folder; at the check's `timeout_s`, or `--timeout-s`, else 3600 s, its whole process group gets SIGINT and 30 s later SIGKILL. One check runs at a time on the machine: a run waits for the check lock (`check.lock` in rounds' folder of lock files) while another holds it, or with `--no-wait` is refused. Nothing is recorded when the tip or the worktree changed while it ran. With no round, in a build's worktree, it checks that build; it exits 1 when the check failed. With `--step NAME` it runs the command step `NAME` of the round's flow, a step of your own, the same way, and records the run under that name. In a repo that declares `ci` it checks by CI instead: it pushes the tip to `ci.remote` when set (forced, to the branch `ci.ref` gives, when that is set), runs the local part (recorded under `check.local`) unless it passed at the tip, and reads CI, recording the reading; `--full` runs the full check there |
| `moxi rounds finish [ROUND B<n>] [--again ID]` | finish a build at its branch's tip once every gate of finish holds there: take the repo's finish lock, the ref `refs/rounds/finishing/<into>` or the repo's `lock_ref`; read again the gates a move of the target can change; run the repo's finish actions in order, each recorded under its `id` before the next starts, an action recorded `ok` or `found` at the tip not run again; a merge already on the target (made by hand, by a fast-forward of the target, or by a finish that stopped before recording it) is recorded `found` with where it landed and is not made again, and a hand merge is printed as `found hand merge <commit>:` with what it put on the target; and record the finish when the gates read on the log still hold at the tip it merged, though the branch moved since. The `merge` action merges the tip, by its commit id, into the target with a merge commit, in the checkout that has the target, or in one it adds under `worktree_dir` and removes after. A conflict is aborted and refused with the way through; a failed action is recorded, undoes nothing, and exits 1. A refusal or failure after anything was recorded names each thing done and recorded, and says the build reads merged, not finished, when its merge is on the target. A lock left by a process on this machine that has ended is cleared and recorded; any other holder is named, with the git command that clears it. A `push` pushes the target at the merge's landing, or with no merge before it the build's branch at the tip, both by commit id. A push of the target git refuses still passes when the remote's target already holds the landing, so a rerun does not fail because the target moved on there; nothing is forced, so the remote's target never moves back. To read that, finish fetches the remote's target when the repo lacks its commit, which refreshes the repo's remote-tracking branch for it (`refs/remotes/<remote>/<into>`), as a push that succeeds would; no other ref changes. A `command` runs its argv with no shell in the build's worktree, placeholders filled as for a check, its output yours, with `started` recorded before it runs. A command that started and has no result may have done its work: it is run again, saying so, when its `repeat` is true, or once with `--again ID` after you have looked at what it did; otherwise it is refused with both ways on, that and the operator's `skip-action`. `--again` is refused for any other action. An action the operator skipped for the stage is not run and counts as done. Then it cleans up: after a merge, and only while the branch is still at the tip it finished, it removes the build's worktree and scratch folder and deletes the branch with `git branch -d` (not with `keep_branch`); with no merge it removes only the scratch folder. Each removal obeys the removal rule below, and a folder the rule keeps is named with the check that kept it. Before anything is written, finish is refused while a process of yours uses the build's scratch folder (its working folder, an open file, an argument or an environment value), naming the process: end it or leave the folder, then finish again. A scratch folder a process came to use after that read is not moved and is left for the sweep, named with the process. Where the processes can't be read, finish says the scratch gate was not run and goes on. Last, it removes from the remote CI's own refs of the build (see `ref` and `full_ref` under Config), each named with what became of it. With no round, in a build's worktree, it finishes that build |
| `moxi rounds show ROUND [--json]` | one round: status, lead, flow, the step it is at, what its close waits for, its design and stages, its builds (for a build not finished, the newest record of each finish action at its head, with its state and landing), questions by state, rulings, verdicts and history |
| `moxi rounds list [PROJECT] [--closed] [--json]` | the open rounds, each with its step and its counts, or with `--closed` the ended ones, each with how and when it ended; each row of `--json` also has the round's `lead` |
| `moxi rounds list [PROJECT] --waiting [--json]` | what waits on the operator: open questions, parked ones, open waiting items, rulings not yet shown, builds to look at |
| `moxi rounds list [PROJECT] --numbers [--json]` | the numbers of the closed rounds, summed key by key; a dropped round's are not summed |
| `moxi rounds list ROUND\|PROJECT --questions [--view docket\|ratify] [--json] [--out DIR]` | the questions written out as markdown; `--out` writes `questions.md` and `questions.json` into DIR; a round's two views are both given when `--view` is left out |
| `moxi rounds drop ROUND --by operator\|lead --record PTR [--withdraw-questions]` | end a round short of its work |
| `moxi rounds drop ROUND B<n> --by operator\|lead --record PTR` | drop the open build of one stage; the stage stays owed |
| `moxi rounds drop --leftovers [--refs] [--remove] [--json]` | sweep the worktrees and scratch folders ended builds left on this machine: what would go, and what stays and why; `--remove` takes what may go, once `sweep.armed` is true. `--refs` adds the account of CI's own refs on each project's remote that no open build owns, which reaches the network |

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
  of the design;
- `page:<page>/<act id>@<UTC time>`, one act of the operator's on a module's page, as
  `page:rounds/k3J9xQ2aZf1T@2026-11-21T04:18:05Z`: the module whose page it was
  (lower-case letters, digits and `-`, from a letter), the id the page gave that one act
  (8 to 64 letters, digits, `_` and `-`) and the time to the second. A page on this
  machine has no `https://` address, so what it records points at the act itself, and
  the act id keeps two acts in one second apart.

Only the shape is checked. A question with no `--from` and a ruling with no `--record`
are refused. A question that is the operator's own takes `--record` in place of
`--from`. `mark --shown` takes a message, a session, a URL or a page's act, and never a
file, the pointer of one of the rulings it marks, a message from before one of them was
said, or a page's act whose time is before one of them was recorded: a page can only
show a ruling on record.

## A round's lead

A round's lead is the agent who runs it, the one a page tells what the operator did and
asks to explain. `open --lead NAME` names it, and `mark ROUND --lead NAME` names a new one
later; the log keeps the one before, and `show`'s history gives the change as `WAS to
NEW`. Anyone may name it: it is a record the leads keep, not a ruling of the operator's,
so it takes no pointer. A name is letters, digits, `.`, `_` and `-`, with at most one
`@scope` of the same letters; only that shape is checked, so a lead may be named before
its session starts. A round that has ended refuses a new lead.

A round with no lead of its own reads its project's, the lead its record in `moxi
projects` names, and a round opened before rounds kept a lead reads it that way too.
`show` gives it in one line under the description, one of:

```text
lead: search-lead
lead: alpha-lead (the project's; moxi rounds mark alpha/search --lead NAME records the round's own)
lead: none
```

The round note has the same under its status, one of `- lead: NAME`, `- lead: NAME (the
project's)`, `- lead: none`, or `- lead: not known (REASON)` when the project's record
can't be read. The note gives the project's lead as it was when the note was last
written: after `moxi projects set PROJECT --lead NAME`, `show` and `list` give the new
lead at once, and the note gives it at the round's next write. In `show --json` and each row of `list --json`, `lead` is `{"name": NAME,
"from": "round"}` or `"from": "project"`, or null when neither names one. `list` prints
no lead in its text.

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

`next`, `commission` and the check read whether `design.md` still holds the current
version's digest. When it does not, they say `design.md changed since v<N> was frozen`,
and the fix is the fold. When `design.md` and the frozen copy hold the same bytes and
neither is what the version was frozen from, an edit outside the round rewrote both:
they say the frozen copy was edited after the freeze, as `design.md` was, and offer the
repair `mark ROUND --restamp --record PTR`, the pointer being where the lead named what
edited them. The re-stamp records the copy's SHA-256 as the version's re-stamped digest
in one `repair` event (`what = "digest"`, `version`, `record`, the digest was and now,
and the copy it read). It refuses a round with no frozen version, a copy that can't be
read, and a `design.md` that differs from the copy (the fold is the fix), and writes
nothing when `design.md` holds the digest already. A version keeps the digest recorded
at its freeze beside the re-stamped one, and each comparison of a version with the one
before it reads the earlier one's digest as it stood when the later was frozen, so a
re-stamp changes no standing, no run of sent-back versions and no brake answer; a
freeze of the same text after it reads as the same text. It is the one repair that
takes `--record`, and the one an ended round takes, since it corrects a record and adds
no work. `show` lists it under Repairs, `digest of v8: 1a2b3c4d5e6f to 0f9e8d7c6b5a,
record ...`, and its versions line gives the re-stamped digest with `(re-stamped from
1a2b3c4d5e6f)`.

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
`--existing` takes a branch whose span is strictly inside, or strictly around, another
open build's, so a stage stacked on another can be commissioned on purpose and gate 10
of `finish` orders the two. It refuses a branch that overlaps another open build's, or
holds the same commits, naming the build and the relation, and a branch a recorded
landing of another build already brought onto the target, naming that build: drop the
round, or, for work still to do, delete the branch (`git branch -d`, as the target holds
its commits) and commission without `--existing`, which cuts a new one.
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
ruling may apply to some (`rule --stages B1`). `next` gives each stage's steps with
what each waits for, the questions that hold the stage among them.

### Verifying a build

A checker reads a build and records a report of `kind = "build"` with `verdict`. Its
block names the `stage` and the `head` it read, the tip of the build's branch or an
ancestor of it, so a fix committed while the report was written does not make it
unrecordable. A report by the build's builder is refused. Each `[[finding]]` becomes a
finding of the build, `B<n>.L<n>` outside the report, and the newest report that lists a
finding sets its state: `open`, `fixed` or `no-action`. A report lists every finding
the build holds as open, so none is dropped by being left out, and a finding new to the
build takes an id no earlier report of the stage used, a dropped build's included.
`verdict` prints the build's findings and what its finish still waits for; `show` and
the round note give each build's newest verdict and its findings.

A build's `finish` waits for eleven gates, read at the tip being finished, and names each
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
10. it shares no commits with another build, unless one of the two is stacked inside
    the other and this one goes first (below);
11. no commit it would bring into the target holds a private file of the repo (below).

No skip meets gates 9, 10 and 11, and `finish` itself is never skipped.

Gate 10 reads each other open build of the repo and target that shares commits with
this one by the two spans, each the commits in `target_before..head`. This build is
**inside** the other when its span is strictly part of the other's, **contains** it when
the other's is strictly part of its own, and otherwise the two **overlap**: each has a
commit the other lacks, or the spans are equal. Of two stacked builds, the one whose
work reaches the target first goes first:

| this build is | the other reads | gate 10 |
|---|---|---|
| inside it | not merged | passes |
| inside it | merged | held until the other's finish records the landing that brought this build's work onto the target |
| around it | anything, this one not merged | held until the other's work is on the target |
| around it | anything, this one merged | passes: a merged build holds no other |
| overlapping it | anything | refused: a build finishes only with work of its own |

A held build's line has the fix `moxi rounds next ROUND` of the build to finish first.
The rule is per pair, so a chain finishes from the inside out, and two builds inside
one outer build that overlap each other refuse each other. Once the inner build's work
is on the target it leaves the outer build's span, so the outer passes on its next
read with nothing written. An outer build merged by hand, or fast-forwarded by hand,
finishes first and records the landing; the inner build then reads `empty` with
nothing of its own, does not finish, and ends by a drop, its stage still owed. Two
builds with the same work are refused both, and one is dropped. `finish` reads gate 10
again under the finish lock, so of two finishes run at once the second reads the
target the first left. `next` gives a held finish as waiting with the held line;
`show`, `list` and the drop refusal word each share by its relation (`inside ROUND B1
(finishes first)`, `contains ROUND B2 (held until its work is on the target)`, `shares
with ROUND B3` for an overlap), and `--json` gives each share a `relation`: `inside`,
`contains` or `overlaps`.

Gate 11 reads every commit reachable from the tip and not from the target's head, over
every parent of every merge, so a commit a later one undid, or one a merge dropped, is
still read. A build's private files are the paths its commission copied in and the
entries of the repo's `private_files` now. A commit holds one when its tree has that
path, a path under it, or a file where a folder the path lies under would be; names
compare by case folding where the repo's `core.ignorecase` is true. A commit that holds
one is refused, as finish's merge at the root would overwrite the private file there
and the commit would land in the target's history. The refusal names each path, the
entry that matched and the commits that hold it, and the fix rewrites the branch so no
commit holds it: `git rm --cached -- PATH` if the tip holds it, then `git reset --soft
$(git merge-base TARGET HEAD)` and `git commit`. That changes the tip, so the
verification and the check are owed again. Where the target itself holds the path, the
fix is to take it out of `private_files` or out of the target. `moxi rounds check`
refuses on the same read before it runs, and `finish` reads the gate again under the
finish lock, against the target's head as it is then.

The lead may mark a `minor` or `note` finding fixed without a re-check: `mark ROUND
B<n>.L<n> --fixed --commit SHA [--commit SHA]... --no-behaviour-change`, naming every
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

Work that can't be done without the operator, such as a check on the operator's phone, is a
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
stage. A later report may list it again with the same sentence, as heavy or heavier,
never lighter; another sentence under its id is refused. The operator rules it with
`rule ROUND B1.M1=accept|reject --record PTR`; a rejected one is also a waiting item,
`B1.M1`, until `mark ROUND B1.M1 --done --record PTR` names the fix. A report of
`kind = "majors"`, by a checker who built none of the stages it names, declares
decisions no build report carries, each with its `stage`, and names the builds it covers in
`stages`. `list --waiting` and a project's export list each unruled major, and each
minor one that is the operator's.

**Whose a decision is.** One rule says it, and the close, the `ratify` step, the
labels, the export and `list --waiting` all read it. An open decision is the
operator's exactly when the close waits for the operator's ruling of it: the round's
flow has a `ratify` step (every flow with a `build` step has one) and it is not
skipped, the project's `ratify` setting names its weight (below), its stage is finished,
and the build that finished it declared it. Under each open decision `show`, the round
note, `verdict`'s output and `list --waiting` print:

| Open decision | Its line |
|---|---|
| the operator's (major or minor) | `the operator's to rule: <rule command>` |
| its stage not finished, the rest of the rule holding | `not yet put to the operator (stage B1 is not finished): <rule command>` |
| major, not the operator's | `not put to the operator (<why>); recorded only if the operator rules it: <rule command>` |
| minor, not the operator's | `the lead's to settle: moxi rounds mark ROUND B1.M1 --accept, --reject or --major --record PTR` |

The why is the first part of the rule that fails: `the flow has no ratify step`,
`ratify is skipped`, `ratify is off` (or `ratify is majors`, for a minor one), `stage B1
is not finished`, or `the build that finished B1 did not declare it`. A settled one
reads `settled by the lead: accept, record PTR`. In a round that has ended none takes a
ruling: each open one reads `left open, put to the operator and not ruled` or `left
open, not put to the operator (<why>)`, with no command. `show --json` gives an open
decision `put`, `not_put` and the line as `open_as`; a row of `list --waiting --json`
that is not put has `not_put`.

**The lead's settlement.** Every open minor decision of a finished build that is not the
operator's holds the close until the lead settles it, in every flow:
`mark ROUND B1.M1 --accept --record PTR`, or `--reject`, PTR a pointer to where the lead
wrote why. Where the minor decision promises a follow-up, the pointer names the task
filed for it; where nothing was filed, the lead rejects it. A settlement is one
`decision.settled` event with `chose`, `record` and `by = "lead"`; the decision reads
`settled`, never `ruled`. A reject owes a fix, the waiting item `B1.M1 rejected: fix
owed`, until `mark ROUND B1.M1 --done --record PTR` names it. The settlement is refused
for a key that is no decision of the round, a major decision, one ruled or settled
already, a minor one that is the operator's or will be once its stage finishes, a
missing or malformed pointer, and a round that has ended. The operator's ruling of a
settled decision replaces the settlement and names its line (`settlement` in the
ruling's event). `mark ROUND B1.M1 --major --record PTR` makes a decision major, one
`decision.raised` event, as a later report listing it heavier does; it is refused for a
major one, one the operator ruled, a missing pointer and an ended round. A settlement
covers a minor decision only: once its weight rises, by `--major` or a later report, the
decision is open again, as a major one, and a reject's fix is no longer owed (a `--done`
already marked stays in the log). The index keeps a settled decision with the state
`settled`, which no export draws.

A round's export has two views. The docket (`--view docket`) is its open questions and,
after them, the parked ones. The ratification (`--view ratify`) is the unruled decisions
of the builds that are finished when it runs, named under `covers`, each the operator's
by the rule above; what it puts to the operator is the project's `ratify` setting:
`majors` (the default) each major decision, `majors+minors` the minor ones too, drawn as
one group that lists their ids and is still ruled one id at a time, and `off` none. A
round whose flow has no `ratify` step, or whose `ratify` is skipped, puts none either,
and its export says why under `not_ratified`, which it learns from the round's
decisions: with none to draw, the markdown says what it puts only where the flow
ratifies. The major decisions of builds not yet
finished are listed under `later`, and come in an export run once their build is
finished. A build answers only for the decisions its own reports declared: a dropped
build counts for nothing, so a decision only a dropped build declared is in neither
list until a report of a later build of its stage lists it again. A decision that
forecloses a capability is an entry of its own everywhere:
`alone` in the data, its own heading in the markdown, never in a group, a minor one
from a log written before reports refused that pairing included.

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
from closing. Only the operator lets go of it: the operator's answer, `park:`, `narrow:` and
`withdraw:` are rulings. `mark` changes a question only towards holding up more, and
reopens a parked one. A question comes from `ask` or from the design's block, where `fold` adds it.

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
show the operator this under "Recorded as" in your next message, then:
  moxi rounds mark alpha/search C1 --shown --record <pointer to that message>
```

Until `mark --shown` names it, a ruling is listed by `list --waiting` among the rulings
not yet shown to the operator, with the project's own and its rounds' together. `rule
PROJECT readback=TIME` records instead that the operator read that list through TIME, the
time of the last ruling on it, and covers every ruling of the project recorded up to then.

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

The rows are also what a build is given. The commission block prints, as each stands
now, the rulings the rows cite for the stage, whether `trace` is on or off; in a flow
with no design step, or with the design skipped, it prints the round's own rulings that
say what is built and name every stage or that one, and the project's standing rulings
reach the lead only as `not cited, check:` lines. `next` tells an open build each
ruling of that same set recorded, replaced or taken back after its block was printed.

## Closing

No command closes a round. After every write to a round, rounds asks whether its close
still waits for anything, and the write that leaves nothing closes it. In a flow with
no `build` step the close waits for:

- every step of the flow to be done or skipped;
- every question to be ruled or withdrawn, the parked ones included;
- every stage to have left the plan, since a flow with no `build` step builds none.

```text
$ moxi rounds rule alpha/ranking Q1 --chose b --record comms:21
Recorded as C1: Only an exact title match
in answer to Q1: Should a title match always rank above a body match?
show the operator this under "Recorded as" in your next message, then:
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

A round whose flow has a `build` step closes once every stage in its plan is finished,
no build is open, and its decisions are done with: every decision of a finished build
that is the operator's (the rule under Verifying a build) is ruled, every finished build
has a report that declares its decisions (a build report, or a majors report naming
its stage), and every other minor decision of a finished build is settled by the lead,
each open one read as `decision B1.M1, minor, not yet settled by the lead` (the command
is under the decision in `show`). The operator's skip of `ratify`, the project's
`ratify = "off"`, or a flow with no `ratify` step puts no decision to the operator; the
lead's settlements are waited for in every flow. While a decision that is the
operator's is unruled, `next`, `show` and `list` give the round's `ratify` step as
ready and the operator's, with the command that rules each.

The write that closes a round prints, after its `closed ROUND: ...` line, a line for each
decision of a finished build still open, worded by the rule under Verifying a build:
`left open, not put to the operator: decision B1.M1, major: <text>`. Since the close
waits for every decision that is the operator's and for the lead's settlement of every
other minor one, the list holds only major decisions the operator was not put (`ratify =
"off"`, a skip of `ratify`, or no `ratify` step). `left open, put to the operator and not
ruled` is said only of a round dropped with one open. A decision only a dropped build
declared is not among them. `show` of an ended round gives the same list under the line
that says how it ended, with the `ratify` setting as it is now, and `--json` has it as
the ending's `left_open`. A round that ended before the close waited for the lead's
settlements is read as it is: its open minor decisions keep their `left open` lines.

The event that ends a round, its close or its drop, holds the round's numbers, counted
from its log: design passes, `fold-first` verdicts of any kind, folds and records-only
folds, the lead's decisions (on a thread, or a records-only fold that made a design
sent back stand at `lead-decision`), the time spent at each step, findings by
severity, re-checks and drift re-checks, failed check runs, questions by where they
came from, decisions the operator accepted and rejected, the minor ones the lead
settled (`settled`, accept and reject; numbers written before it read it as zero),
skips, step-ups, repairs, corrections,
dropped builds, cleared locks, and reports flagged `extra-full-pass` or
`model-below-tier`. The time at each step is in whole seconds, under the step's name
or `<step> B<n>` for a stage's: the log is read once at the end, and the time from
each event to the next goes to every step that was ready or waiting then, so steps
due side by side each get it, and the line says so: `time in steps, overlapping (steps
due side by side each count the same time): ...`. Beside them, `round_time_s` is the
round's own time, from its first event to the one that ended it (`time open: 2h 5m,
open to close`), and `longest_gap` the longest stretch with no event, the stretch to
the end included, as `since`, `until` and `due`, the steps due across it, the earliest
of equal ones (`longest stretch with nothing logged: 96h 0m 12s, since ..., while
finish B1 was due`): that is where a build that sat idle shows. The retro step's
numbers in `next` run these two to now. For a round that ended before they were
counted, `show` computes both from its log up to the ending event and writes nothing,
reading `due` with the `ratify` setting as it is now. The steps are read from the log alone: a build has work
once a record names a commit of it, its check is done when its newest run passed at
the newest commit the log names, and its verification when a publishable report, or
a lead's mark of what came after it, covers the commits named since. `show` gives them under the line that says how the
round ended, `retro.md` beside the round's note holds them, and `list --numbers` sums
those of the closed rounds (a dropped round's stay with it): `round_time_s` over the
closings that hold it, saying how many rounds that is when it is fewer than all
(`timed` in `--json`), and never `longest_gap`, which does not add across rounds.

A flow with a `retro` step (`full` and `small` as shipped) waits for it too. The step
is due once the close waits for nothing else, and `next` then gives it with the
numbers as they stand, the note and the reports to read. The worker writes what went
wrong and what to change, with the block from `next --schema retro` (`kind`, `by` and
`model`, no verdict word), and records it with `verdict`; the round closes on that
record. A retro report is refused in a flow with no `retro` step, which gets the
numbers only, and the operator's `skip:retro` lets the round close without one.

## The merge queue

A project whose table sets `land = "queue"` lands its builds through one queue per
repo instead of each by its own finish. A builder's last step is still `moxi rounds
finish`, which now joins the queue and returns; the repo's lander merges the queued
builds onto the target together and runs CI once on the result.

**The check.** In a queue project `moxi rounds check` runs the check's `scoped` command
in the build's worktree, `{base}` filled in with the merge-base of the tip and the
target: the check of the packages the build touched and their dependents. It pushes
nothing and reads no CI, and `--full` is refused, since a queued build's full run is its
candidate's CI. The merge-base of the tip and the target does not change as the target
moves forward, so the record stays good while the build waits.

**Joining.** `finish` reads the gates of finish at the branch's tip as a direct finish
does, but for gate 6, the target, which is off: the lander's candidate holds the target
by construction. Gate 3 is met by the scoped check at the tip, and gate 5 (drift) is
read against the target's head now, once. A repo whose finish actions are anything but
one merge is refused, since the lander only merges. When the gates hold, finish appends
`join` to the repo's queue file and `queue.join` (the tip, the target's head, whether it
is urgent) to the round's log, prints the build's position, and returns. A build already
queued at its tip joins nothing new; one queued at an older tip leaves as moved and
joins again at the back. `finish --urgent` joins ahead of every member that is not
urgent; urgent members are batched only with each other, and even while the queue is
paused. `finish --leave` takes the build out at once, and says so when it was inside a
running batch, which the lander builds again without it. A direct finish in a queue
project is refused: `land is queue: finish enqueues`.

**The queue file** is `queue/<repo>/queue.jsonl` in rounds' state directory, one event a
line, appended under its lock. The queue's state is read from it and nowhere else. Each
event about a member is mirrored into the member's round log (`queue.join`,
`queue.batch`, `queue.landed`, `queue.ejected`, `queue.left`, and `queue.requeued` when
its batch ends without it), which `next` and the gates read: while the build is queued at its tip, its finish step waits on the lander,
and `next` gives its position, its batch and a pause; once it was ejected or left, the
reason is owed beside the step.

**The lander** is `moxi rounds land --repo KEY`, one per repo: it holds a lock on
`lander.lock` in the repo's queue folder, so a second one exits at once, and the system
frees the lock when a lander dies. `finish` starts one each time a build joins; `moxi
rounds next` and `moxi rounds queue` start one when a member waits and none runs, and
say so, and record nothing for it; they start one too while a message is still being
tried. The doctor starts none: it warns of a member waiting
with no lander, and of a paused queue, read from the pid in the lock file without
taking the lock. A lander is started in a transient user unit (`systemd-run --user
--collect`), else in a session of its own, so it outlives the session that started it.
Either way it runs the interpreter of its starter in the repo's root, with its starter's
`PATH`, `HOME`, `SSH_AUTH_SOCK`, `GH_TOKEN`, `GITHUB_TOKEN`, `GH_CONFIG_DIR`, the XDG
roots and every `MOXI_` variable, and appends its output to `lander.log` beside the
lock. It exits once nothing keeps it (no member waits, no message is still being
tried, the target's head's full run read): it releases the lock, reads the queue again, and
goes on if a member joined meanwhile and no other lander took the lock.

**A batch.** Each minute the lander takes the members that wait (the urgent ones alone
when there are any) and merges each one's joined tip, in turn and with `--no-ff`, onto
the target's head on a branch `queue-<M12>-<n>` in its own worktree, `<worktree_dir>/
<repo>/.queue`. A member that conflicts is ejected with the member before it that it
conflicts with, one a commit hook refuses with the hook's output, and one whose branch
moved or whose build ended leaves. It pushes the candidate to its own ref (the `ci.ref`
template filled with that branch), runs `ci.local` at it, and reads its CI each minute.
A push refusal that names a member's tip or path ejects it; one that names none is tried
again once, then pauses the queue. A cancelled run is rerun once and an absent one
pushed again once under the next number; a second of either pauses the queue, as do
five failed reads in a row. A repo with no `ci` setting pauses its queue. When the run is
green and `ci.local` passed, the lander takes the repo's finish lock and reads again:
the target still at the candidate's base, the checkout that has it clean, and each
member still in the batch at its tip with every finish gate but the check, drift and
target. A target that moved rebuilds the batch on it; a member that fails leaves with
the gate, and the rest are rebuilt; a checkout not clean is waited on. Then it
fast-forwards that checkout to the candidate (`git merge --ff-only`), writes each
member's `ci.read` and `check.local` records at its joined tip naming the candidate,
which meet the check gate, and finishes each one with its own merge found as its
landing. A lander that dies after the fast-forward is finished by the next one from the
queue file and git.

**A red candidate.** A red run's failed jobs are rerun once (`gh run rerun --failed`),
and a failed `ci.local` is run again; green then lands. Still red, the lander lists its
failures: each test a failed job's log names in pytest's `FAILED` lines, or the job
alone. It compares them with the target's own at the candidate's base: that commit's
full run, read once it has ended and, when red, after one rerun of its failed jobs, and
`ci.local` run there once when `local` failed. A test counts as the target's own when
that run fails it in any job, or fails its job naming no test; a job named alone, when
that run fails a job of that name. Until the target's run is known the batch waits; if
the target moves meanwhile, the batch is rebuilt on it. With no failure the target
lacks, the candidate lands, each member's `ci.read` and `queue.landed` naming the
failures as `excused`. A new failure with one member ejects it with the failures. With
two or more, the batch splits in two by join order, each half built on the target's
head and run side by side by the same rule; the earlier half lands first, and a later
half whose target moved is rebuilt on it. Members that join meanwhile wait until the
split is done.

**Main afterwards.** The target's head always gets a full run, and the queue cancels
none. Each pass the lander reads the full run at the head; a head whose candidate's run
already covers it needs nothing more. A head with no run is pushed, forced, to the
target's own `ci` ref (`ci/main` for moxi), but not while a run pushed there earlier is
still going: when that ends it is read, and then the newest head is pushed. A push that
shows no run after ten minutes is made once more; after a second the head is given up,
the director told, and `queue` says so. A red run's failed jobs are rerun once before
main counts as red, and the lander records `red` or `green` at the head, with the
landings since the run read before (`covers`), which a red candidate's comparison reads
too. A red run after green (or after none) starts a red spell: main's owner is the lead
of the earliest covered landing whose round has a lead, every covered lead is told, and
a spell no landing with a lead covers goes to the director. Before each message to the
owner the lander reads comms' `identity_state`: a name a live session holds gets it;
any other state (or a read that raises) still gets it, kept by comms, and the director
gets a notice of what happened, who owns it, and how to bring that lead back (`moxi
launcher sessions`, `moxi launcher resume SESSION_ID --brief-file FILE`). Red after red
keeps the owner and tells it only when the failures differ; a member that lands past
main's own failures is told to the owner; green ends the spell and tells the owner. The
lander does not exit while the head's full run has not ended and been read. `queue`,
and `next` for each queued member, say the spell and its owner, or that neither the
owner nor the director has a running session and the notice waits.

**Telling.** With comms installed, the lander sends its news under the send-only name
`rounds`, which rounds reserves, and only to names something records: a member's end
(landed, ejected, left) to its builder, the name its commission recorded, and to its
round's lead (the round's own, else the project's); a member ejected for a conflict, once
the member it conflicts with lands, is ejected or leaves, with what to do now; a pause or
a wait on the target's checkout to the project's `director`. Each message is recorded in
the queue file as `told`, with what comms did with it. One comms could not take (its
daemon down) is tried again each minute for an hour, and the lander does not exit while
one is; a refusal (a name comms has never seen bound) or an error is recorded and not
tried again. Where no director is set, or a round has no lead, that is recorded and
said. Without comms nothing is sent, and `next` and `queue` are where the news shows.

`moxi rounds queue [--repo KEY]` shows each queue project's queue: whether a lander
runs, a pause and its reason, each running batch and its CI reading, the members still in
the queue in the order they are taken, the target's last full run, and the messages that
reached nobody. `--resume` lifts a pause.

## Dropping a round

`drop --by operator` ends any round, with a pointer to where the operator said so. `drop
--by lead` ends only a round that holds none of the operator's acts: no ruling, no
question asked with the operator's pointer, no opening on the operator's go. The round's
open and parked questions go to the project's list as they are, or with
`--withdraw-questions`, which is the operator's alone, are withdrawn. Its open builds
end with it, each branch's tip recorded, and the lead's drop is refused while one of
them is not the lead's to drop (below).

A dropped round takes no new question and no new ruling. A correction of one of its
rulings is still recorded, and its rulings are still marked as shown.

`drop ROUND B<n>` drops the open build of one stage, and the round goes on. Who may
drop it is read from git at the drop. The lead drops a build with no work a drop could
lose: its branch is gone, or it is `empty` and holds no commit of its own (nothing but
merges of the target), and it shares no commit with another build. Every other build,
and one git can't read, is the operator's. `show` and `list --waiting` name, for each
build to look at, who drops it and the command. The drop records the branch's tip and
deletes no branch, so the work can be commissioned again. The build's worktree and
scratch folder are removed under the removal rule below, and one it keeps is named.
A drop is never refused for a process that uses a scratch folder: a scratch folder a
process of yours uses is not moved, and stays for the sweep, named with the process.
CI's own refs on the remote of each ended build are removed too, as at a finish (see
`ref` and `full_ref` under Config). No question or hold changes, and the stage stays
owed.

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
folder is moved into rounds' trash and deleted there, each step a line of the sweep's
log (below), so an entry a stopped `finish` or `drop` left is the sweep's to delete. A
folder either half keeps is left, named with the check that kept it, for a later sweep.
Finish's worktree gate reads the worktree with the same git read, so an untracked file
stays a change whatever the user's git config says.

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
trash entry the sweep, a finish or a drop moved and did not delete, because a run
stopped, is deleted by the next run; anything else in the trash is reported and left.
The doctor's `leftovers` row is the same dry run.

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
<dir>/<slug>/retro.md        generated once the round has ended: its numbers
<dir>/<slug>/design.md       written by the lead; rounds reads its data block
<dir>/<slug>/versions/design-v<N>.md   the design as `fold` froze it
<dir>/<slug>/*.md            reports, written by checkers; rounds reads their data block
```

A log is only appended to. The generated files are written again whole after every
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
  folders removed and each trash entry it, a finish or a drop moved or deleted, kept to
  about a megabyte and one older file. It is this machine's alone, and `sweep.started`
  beside it holds when the last removing run began.

A log that two machines both appended to, or one with a line this version can't read, is
not used at all: every verb on it refuses and names the file, the line and the way back.
When a log's earlier lines changed outside this machine, it is read as it stands and
`show` says since when.

### Under a test

While a test runs (`PYTEST_CURRENT_TEST` or `PYTEST_VERSION` is set, or pytest is
imported), rounds won't use a directory of its own that no Moxi variable places:
`MOXI_ROUNDS_<KIND>_HOME` (`MOXI_ROUNDS_RUNTIME_DIR`), `MOXI_ROUNDS_HOME` or `MOXI_HOME`.
Without one, the directory is the real one, wherever `HOME` or the XDG and Windows
folders point. Nor will it take a project's folders from a projects registry that no
Moxi variable places (`MOXI_PROJECTS_DATA_HOME`, `MOXI_PROJECTS_HOME` or `MOXI_HOME`).
Either way it raises `UnfencedTest` before anything is written, naming the directory,
what shows a test is running and the fix; a command prints it and exits 1. moxi-dev's
test fence sets both homes, so rounds' own tests pass. Only the process that shows a
test is running, and the processes it starts, are refused: another process writing the
same notes folder is not. There is no switch to turn it off; a test that means to use
real folders sets the variables to them.

Every fixture of rounds' `tests/conftest.py` that a test file elsewhere can borrow
checks at setup that the fence's homes are in the test's own tmp, so a borrowed fixture
fails before it writes: put a test in `packages/rounds/tests` and run pytest from
`packages/rounds`.

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
ci = { workflows = [{ path = ".github/workflows/ci.yml", jobs = ["linux", "web"] },
                    ".github/workflows/lint.yml"],
       remote = "origin", ref = "ci/{branch}", full_ref = "ci-full/{branch}",
       local = { command = "./scripts/check-local", timeout_s = 900 } }
```

`workflows` names each workflow by its file's path in the repo, read whole, or as a
table of the `path` and the `jobs` of its run that are read, by the names the run gives
them. `local` is required: the command, or `"none"` written out where CI does all of it.
`remote`, when set, is where `check` pushes the tip before it reads, as a plain push to
the build's own branch name; where `git ls-remote` shows the branch already at the tip
at every push URL of the remote (`git remote get-url --push --all`), it pushes nothing,
since git would still run the pre-push hook with no ref to push, and a push guard may
refuse that. `ref` gives the branch to push to instead: a fixed prefix
ending in `/`, then `{branch}`, which the build's branch name fills as it is, slashes
kept (`ci/{branch}` sends build `a/b` to `ci/a/b`); it needs `remote`. That branch is
CI's own: `check` pushes the tip there forced, so a rewritten build branch goes out
with no hand push, and refuses a build whose branch is the target, whose ref is the
lead's. Each record of the check names the remote and ref it pushed. Once the build
finishes or is dropped, by its own drop or its round's, rounds removes from the remote
each ref the build's records name and the one `ref` gives its branch now, so a push
that went out unrecorded goes too. A ref is kept while it is the one `ref` gives the
branch of a build of the repo that has not ended, in any round, or one such a build
recorded pushing, or the one `ref` gives a target: the project's (its `into`, else the
branch checked out at the repo's root, as a new build would take) or such a build's.
Every ref is kept while any round's log, or the project's target, can't be read. In a
queue project the ref of a candidate the queue shows running is kept too, since removing
it would cancel its run, and every candidate's ref is kept while the queue file can't be
read; the lander removes a candidate's ref when its batch ends, and the sweep removes one
a stopped lander left. The lander's own worktree, `<repo>/.queue`, is never swept. A
removal that fails leaves the finish or drop standing and names the ref; `moxi rounds
drop --leftovers --refs` lists the branches under `ref`'s prefix on each project's
remote that the same rule does not keep, and with `--remove`, once the sweep is armed,
removes them. The session-start sweep never runs it. Every push and removal goes through
the repo's hooks, so a push guard's refusal is printed with its words and nothing is
recorded; fix a hit in your own commits and check again, and give any other to your
lead. Where `ref` was never set, nothing is removed; once it was, the refs a build's
records name are still removed at its end after `ref` is changed or unset.

`full_ref`, which needs `ref`, is a second template under `ref`'s rules, for a ref of a
build's branch that someone else pushes, such as a run on more systems that a lead
starts by hand. Its prefix and `ref`'s may not start with each other (`ci/` and
`ci-full/` don't), so each ref name belongs to one template. `check` never pushes the
name `full_ref` gives, and nothing else in rounds does. Rounds can't stop anyone else
pushing it, though: who may push under that prefix is your remote's rule and your push
guard's, so where a builder is told not to, its brief is what holds it. Past the push,
the name `full_ref` gives is CI's own as `ref`'s is: a build's end removes the name
each template gives its branch now, besides the refs its records name, so a hand push
for a build goes when the build ends; the keep rule keeps the name either template
gives the branch of a build that has not ended, or a target (with `into` unset too, so
`ci-full/main` stays as `ci/main` does); and `drop --leftovers --refs` lists the
branches under both prefixes. Two things follow from how CI runs and how rounds reads
it. CI runs the workflow file of the commit pushed, so a push under `full_ref` runs
what that commit's workflow gives the prefix: a commit cut before the workflow named
it runs as any other branch does. And rounds reads a commit's runs, not a branch's: a
run at a build's tip from a push under `full_ref` is read like any run at that tip,
and when it is the newest run of a workflow named there, it is the reading.

Some refs no account names, and you remove them by hand (`git push <remote> --delete
refs/heads/<name>`) if you find them. The sweep lists only the prefixes `ref` and
`full_ref` give now, and lists nothing for a project with no `ref`, so a push that went
out unrecorded (the check stopped, its read failed, or it was a hand push) under a prefix
neither gives any more is never listed and never removed. And the keep rule reads the
builds of the project key itself, in the rounds folders it can find: an open build in a
project whose rounds folder can't be worked out, or under another project key whose
remote is the same repository, is not seen, so its ref is not kept for it.

CI is read with `status`, `ids` and `jobs`, commands printing JSON, by default `gh run
list --commit {tip} --json ...`, `gh workflow list --all --json id,path` and `gh run
view {run} --json jobs --jq .jobs` (`{run}` is the run's id), each bounded by
`status_timeout_s` (60). A workflow reads `green`, `pending`, `red` or `absent` at the
tip; one read by its jobs reads each job it names, and is `green` only when each is.
A job the run lacks reads `pending` until the run completes and `absent` after, and
with no run at the tip each job it names reads `absent`. The check step is done once
every workflow reads green there, the workflows and jobs named are the ones the setting
names now, and the local run passed at the tip with the command it declares now. The
full check, run with `check --full`, meets the step whatever CI reads. A `ci` in a
project with no `check` is refused, and so is a `ref` or `full_ref` that is not one
prefix then `{branch}`, a `full_ref` with no `ref`, and two whose prefixes start with
each other. `defaults.ratify` (and a project's `ratify`) picks what a round's
ratification view puts to the operator. It also takes
`defaults.check_on` (`merged`: a build's tip must hold the target to finish; `tip`:
only where CI alone met the check), `defaults.slice_minutes` (45: over it, `fold` and
`commission` warn of a slice and `mark --brief` refuses its brief),
`defaults.disposable` (the name globs of ignored paths a worktree's removal may lose;
an entry that is empty, holds a `/`, or would match every name is refused), and in a
project's table `finish`, the list of finish actions, `keep_branch`, which keeps a
finished build's branch, and `lock_ref`, the ref of the repo's finish lock.
`defaults.dormant_days` and `defaults.sitting_at` are checked when the file is read,
and nothing in this version reads them. The `[sweep]` table is this machine's and set in no project's table: `armed`
(false: every sweep of leftover folders is a dry run), `at_start` (false) and
`grace_hours` (2, at least 1). A project's `shared_paths` lists globs of paths every build of its repo shares,
beside the common lockfiles: a change to one on the target owes a build a re-check.

A project's `private_files` lists files its repo neither tracks nor shows, such as an
`AGENTS.md` the repo's `.git/info/exclude` hides, each a literal path relative to the
project's root, written with `/`: `private_files = ["AGENTS.md", "notes/local.md"]`. It
is empty by default and has no `defaults` entry. The config check refuses an entry that
is empty, absolute, holds a `\`, an empty, `.`, `..` or `.git` part, `*`, `?` or `[`,
a leading `:` or a control character, or is given twice. `commission` copies each file
into the new worktree, the same way on every system, and never links it: it refuses,
having made nothing, while one is missing or not a file at the root, is held by the
commit the worktree checks out, or reads as not ignored to git at the root, and stops
with nothing copied while git in the new worktree does not read each one as ignored
(the fix names the exclude file, or the `.gitignore` line that un-ignores it). Each
file must read as ignored, so `git add -A` in the worktree does not pick it up, and
finish's gate 11 keeps any commit that holds one out of the target. A copy is the source
as it was at the commission. An edit of it changes only the worktree's copy, and keeps
the worktree when the build ends, as work, until the file is restored from its source
or deleted; an unchanged copy goes with the worktree. `moxi rounds doctor` warns of an
entry `commission` would refuse.

A project's table may set `land`: `"finish"` (the default), where each build lands by
its own finish, or `"queue"`, where a finish joins the repo's merge queue and a lander
merges the queued builds (see The merge queue). A queue project's `check` also names
`scoped`, the command line `moxi rounds check` runs for a build, with `{base}` filled in
as the merge-base of the build's tip and its target, as in `scoped = "uv run moxi-dev
check --base {base}"`; `land = "queue"` with no `scoped` is refused. `defaults.director`
(and a project's `director`) is a comms name told about a queue project's queue as a
whole and about a red main whose owner has no running session. The module ships it
empty, naming nobody, and `moxi config rounds check` refuses a value that is not a
name: letters, digits, `.`, `_` and `-`, with at most one `@scope`.

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

## The page

With the `ui` extra (`moxi-ui` 0.2.4 or later, Moxi's browser UI shell), rounds has a
page in the shell, at `/m/rounds/`, listed as Rounds among the daily pages. It shows
every open round as one line of its steps, the steps in columns lined up across rounds,
with a line under it for each build in flight. It only reads: it writes no log and no
index, and nothing on it acts on a round. Top to bottom:

- **Waiting on you.** The questions, decisions, jobs and rulings to read back that wait
  on the operator, counted with the number of rounds they are in, or `Nothing waits on
  you.` `Go through in order` opens each item in turn at its marker: rounds in the
  board's order, and in each its questions, rulings, decisions and jobs, then the
  project-level items. The arrow keys and `j`/`k` move, Esc closes. A decision that
  `list --waiting` says is not put to the operator is not counted, here or in a round's
  line.
- **Last acted on.** The ten builds with the newest recorded step, across projects and
  closed rounds, each with a small rail of its stage and the act in words (`landed`,
  `CI green`). A card opens its build's current step, with the stage's five steps as
  tabs, and `Show its line on the board`.
- **Open rounds**, with chips for `Your turn`, `Stuck`, `Moving` and `Wrapping up`, a
  project picker and `Key`. The rounds are grouped in that order. A round's line holds
  its slug, its markers, a phrase for where it is and the age of its last act. Landed
  stages and stages not started are folded under their counts, and the closed rounds
  under one line at the foot.

Each step is a marker of one of eight states, drawn by the shell's rail element. Three
of them have a colour, each with a shape of its own so it also reads in greyscale:
green for done, amber for waiting on the operator, red for failed or sent back. `Key`
lists them all. A marker opens its step's record in place: a build's CI, the checks run
and their output files, verdicts and their open findings, finish gates, what the step
waits on and the command that clears it, and the newest events. A marker that waits on
the operator opens the question, decision or job, which reads only and ends with the
command the round's lead records the answer with. A round's name opens its card.
Whatever is open is in the page's address, so a link reopens it and Back closes it. An
address that names something no longer open says so at the foot.

At 720 px wide and under the page is a column of rounds; a round opens a sheet with its
steps as one vertical rail and its questions as text, to be answered at a desk.

With turns installed, a builder's name in a build's record links to its row on Live
sessions when exactly one live session runs in the build's worktree. The page needs only
ui, rounds and projects.

The page server reads rounds in process and never runs the `moxi rounds` command. It has
three routes, none open to the phone gateway:

| Route | Gives |
|---|---|
| `GET /api/rounds` | the whole board, with the server's clock |
| `GET /api/record` | one marker's record, a waiting item's or a round's card; `{"gone": true}` for a round no longer open |
| `GET /api/events` | the stream, topic `rounds`: the board, published when it changes |

The server reads again every 5 seconds only what changed since its last read: each
round's log, its project's log, its `design.md` and the config file. Git can change with
no file written, so once a minute it also reads git again for every round with an open
build. A question's text, and every other free text, is cut to a fixed length before it
is sent.

## The read API

Other modules read rounds in process through interface 1, reached with
`moxi.modules.load("rounds", interface=1)`. Each of the first four calls gives the data
its command prints with `--json`:

| Call | Gives |
|---|---|
| `rounds(project=None, status="open")` | the rounds of every project or of one, open or `"closed"`: `moxi rounds list --json` |
| `round_data(name)` | one round's whole state: `moxi rounds show ROUND --json` |
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
| `ci <repo>` | for each repo that declares `ci`: warns when the commands that read CI are not on PATH, or, with the default `gh` commands, when `gh auth status` does not exit 0, with `check --full` as the way through; says so when the repo meets the check gate on CI alone |
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
second adds rounds beside it, with the projects module it needs, offers its optional
extras, `notes`, `turns`, `ui` and `comms`, and offers to place its skill. The repository's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi` command and
lists the other modules.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-rounds-v0.1.2/packages/rounds/LICENSE).
