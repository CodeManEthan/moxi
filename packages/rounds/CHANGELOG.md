# Changelog

## 0.1.0 (unreleased)

The first version. It keeps the record of a round and takes it through its design; it
does not yet build.

- `moxi rounds open`, `fold`, `verdict`, `ask`, `rule`, `mark`, `next`, `show`, `list`
  and `drop`.
- A round's log and a project's log, in the project's rounds folder, with the generated
  `round-note.md`, `rulings.md` and `questions.md` beside them.
- Questions with options, in a round or on a project's list, adopted into a round and
  moved out of one.
- Rulings of a round and standing rulings of a project, each with a pointer to where the
  operator said it; a later ruling replaces or revokes an earlier one.
- Each ruling is printed as recorded, marked once it was shown to the operator, and
  listed until then; a read-back covers a project's whole list.
- `list --waiting` across projects, and the questions of a round or a project written
  out as markdown and as data.
- Flows: a round follows the steps its log recorded. `mark --flow` moves it up to a
  heavier flow, the ruling `flow:NAME` moves it anywhere, and the ruling `skip:STEP`
  says a step is not done. `skip:docket:B<n>` lets one stage past the questions that
  hold it.
- `fold` freezes `design.md` as a numbered version and records what its data block
  declares. It adds questions, stages and wider holds at once, and keeps what the block
  takes away as pending until a verdict or a ruling applies it.
- `verdict` records a design report's verdict against the version its pass read. The
  design's standing is the newest verdict of its current version.
- Verification is bounded: one full pass, then re-checks of what changed; `publishable`
  is final; `fold --records-only` needs no pass; after two verdicts in a row that send
  the design back, the lead decides with `mark thread:NAME --close` or `--accept-risk`.
  A second full pass and a report by a model below its pass's tier are recorded with a
  flag and refused nothing.
- Stages: a design plans its builds, each with a repo, a branch and the stages it waits
  for. `mark B<n> --after` adds a wait, and the rulings `unafter:` and `unstage:` take
  a wait off and take a stage out of the plan. A question holds up the stages it names,
  and may be parked until one is finished.
- The trace check, for a project that sets `trace`: a design carries a row for every
  ruling it follows and every commitment it makes, checked when the version is frozen.
  `open --inherits` names the standing rulings a round starts from.
- `commission` builds a stage once its step waits for nothing: the branch cut from the
  target, its worktree and scratch folder each with a marker, the build recorded with
  its id and host; a rerun keeps only its own stopped run's worktree and the base its
  branch was cut from. `show`, the round note and `list --waiting` give each build with
  its state read from git.
- `commission` prints the commission block for the builder's brief, with the rulings the
  design cites for the stage as they stand now, then tells the lead which of them the
  operator has not been shown and which standing rulings in the repo's scope no row
  cites. `next` tells an open build each ruling recorded since its block that reaches
  its stage.
- `commission ROUND --repo KEY --branch NAME`, in a flow that does not verify its
  design, declares the stage as it commissions it, with `--slice ID=TEXT --minutes N`.
  Such a flow builds one stage in a repo. A repo's branch has one open build across
  every round. A stage whose build was dropped is commissioned again under its id.
  `next` points a plan of five or more builds at child rounds.
- `check` runs the repo's declared check for a build at its tip, in its clean worktree,
  with no shell, stdin closed and no `GIT_` variable, its output in the build's scratch
  folder and its process tree ended at its time limit, and records the run with its
  commit, merge-base, argv, exit code and duration. A run whose tip moved or whose
  worktree changed meanwhile records nothing. A check counts only at the tip it ran at
  and with the command the repo declares now; `show` and the round note give each
  build's newest run.
- A project may declare `ci`: in its repo `check` pushes the tip to `ci.remote`, runs
  the local part under `check.local` unless it passed at the tip, and reads CI through
  two commands printing JSON, by workflow file path, recording `ci.read`. A green
  reading with the local part passed meets the check step; `check --full` runs the full
  check, which meets it whatever CI reads. `next` and `show` give each build's newest
  reading, `next` says what each form still owes, and the doctor's `ci` row says
  whether CI can be read.
- `check --step NAME` runs a command step of the user's own from the round's flow the
  way `check` runs the repo's check, and records the run under the step's name. The
  step is done once its newest run passed at the build's tip; `next` prints the command,
  and `show` and the round note give each such step's newest run.
- A stage's `build` step is not due until it is commissioned, ready until git reads its
  build as holding work or merged, and done from then on. `next`, `show`, `list` and the
  read API read git for it, so a round moves from `commission` through `build` to
  `check`.
- `verdict` records a build report against the head it read, the tip of the build's
  branch or an ancestor of it, and refuses the builder's own. Its findings become the
  build's, and the newest report that lists one sets its state; a report lists every
  open finding, and a new one takes an id no earlier report of the stage used. The
  verb prints what the build's finish still waits for, and `show` and the round note
  give each build's newest verdict and findings.
- A user command step waits, as `check` does, for a clean worktree.
- `mark ROUND B<n>.L<k> --fixed --commit SHA... --no-behaviour-change`: the lead marks
  a minor or note finding fixed without a re-check, naming every commit of the fix. A
  build's `verify-build` step is done once its newest report is publishable and every
  commit since the head it read is named by such a mark or is a merge of the target
  that brings no change to the build's paths; otherwise `next` says a re-check is owed
  and the range it reads.
- `show` and the round note say how far the target moved under each open build since
  its newest verdict, and whether that owes a re-check: one is owed when the target
  changed a path the build changes, or a lockfile or a path of the repo's
  `shared_paths`, or when a merge of the target into the branch after the verdict
  brought such a change or came with commits of the build's own.
- Waiting items: each `[[wait]]` of a build's report, work that can't be done without
  the operator, is an open item of the round, `B<n>.W<k>`, from the moment the report
  is recorded. It holds the round's close and no finish, shows in `show`, the round
  note and `list --waiting`, and ends with `mark ROUND B<n>.W<k> --done --record PTR`,
  PTR a pointer or a commit of the build's repo. A later report of the same build may
  list the item again with the same sentence.
- Decisions: each `[[decision]]` of a build's report, and of a majors report for the
  ones no build report carries, is a record of the round in a question's basic form,
  `B<n>.M<k>`, with the options `accept` and `reject`. A later report may list one again
  as heavy or heavier, never lighter. `rule ROUND B<n>.M<k>=accept|reject --record PTR`
  rules it, a later ruling replaces it and `revoke:` takes it back; a rejected one is a
  waiting item until its fix is marked done. Unruled majors show in `list --waiting` and
  a project's export; every decision shows in `show` and the round note.
- `verdict` records a report of a report step of your own (`kind = "step"`): its
  findings are the build's, kept with the step that raised them; a step's report lists
  and sets only its own step's findings; the step is done once its own newest report
  is `publishable` and counts at the tip.
- The lead's two repairs, each one `repair` event, shown in `show` and the round note:
  `mark ROUND B<n> --resync` reads git again for the base of the stage's open build
  and, only once the target's history was rewritten under it, moves it to the newest
  commit of it the target still has; `mark ROUND REPORT.md --reread` reads a recorded
  report again at the bytes its verdict recorded and adds the findings, waiting items
  and decisions the record lacks, refused when it would take away or change anything.
  Neither names a ruling, a skip or a verdict word.
- The `finish` step of a stage reads its ten gates at the build's tip: verification that
  counts there, no open finding, the check (or CI's form of it), no question holding
  the stage, no drift re-check owed, the tip holding the target where `check_on` is
  `merged` or the check was met by CI alone, a clean worktree and a branch still at the
  tip, every step of your own done, a build with work, and no commits shared with
  another build. `next`, `show` and `list` read it, and `verdict` prints each gate that
  does not hold, by number, with the command that meets it.
- `moxi rounds finish`: once a build's gates hold at its tip, it takes the finish lock,
  reads again the gates a move of the target can change, merges the tip by its commit
  id into the target with a merge commit, recording the action under its id with where
  it landed and the target before it, and records the build finished when the gates
  read on the log still hold at the tip it merged, even if the branch has moved since. A conflict is aborted and refused; a failed action is
  recorded and undoes nothing, and the next finish runs it again; an action recorded
  `ok` at the tip is not run again. A merge already on the target, made by hand, by a
  fast-forward of the target or by a finish that stopped before recording it, is
  recorded `found` with where it landed and the target before it, and is not made
  again; a hand merge is printed as `found hand merge <commit>:` with what it put on
  the target. A `push` action pushes the target at the merge's landing, or with no
  merge before it the build's branch at the tip, both by commit id; a `command` action
  runs in the build's worktree with its placeholders filled and its output the
  caller's, `started` recorded before it runs. A command that started and has no
  result is run again when its `repeat` is true, or once with `finish --again ID`, and
  is refused otherwise, naming both ways on; the finish row in `next` and `show` waits
  where `finish` would refuse. The ruling `skip-action:B<n>=ID` is the operator's
  skip of one finish action for one stage: `finish` passes it as done, `show` lists
  it with the other skips, and it is replaced or taken back like any skip.
- The removal rule: a build's worktree or scratch folder is removed only when rounds
  made it on this machine (its place under rounds' roots, no link below them, its
  marker naming the build) and no work is lost (git read with the settings that hide
  work overridden: no change, untracked or hidden path, ignored path outside the
  `disposable` globs, unreachable commit, checked-out submodule, lock or mount inside).
  `finish` removes the folders and deletes the branch after a merge while the branch is
  at the tip it finished, and only the scratch folder with no merge; both drops remove
  the folders of the builds they end. A folder kept is named with the check that kept
  it. `defaults.disposable` sets the globs. The finish gate on the worktree reads git
  the same way.
- `drop --leftovers [--remove] [--json]`, the sweep of the folders ended builds left on
  this machine: candidates only from finished and dropped builds' records, the removal
  rule's two halves, then every log read, no process using the folder (through psutil,
  now a dependency) and nothing in it changed within `sweep.grace_hours`. A dry run
  until `sweep.armed` is true; one run at a time, starting no removal after 60 seconds;
  each removal and trash move and delete is a line of `sweep.jsonl`, the store `sweeps`.
  `finish` and `drop` write a line there for each trash move and delete of theirs, so a
  trash entry one of them left when stopped is deleted by the next sweep. The doctor's
  `leftovers` row is its dry run.
- A round whose flow has a `build` step closes itself once every stage in its plan is
  finished, no build is open, every major decision of a finished build is ruled and
  every finished build has a report that declares its decisions, unless `ratify` is
  skipped or the project's `ratify` is `off`.
- The sweep at a session's start: with turns installed, a listener on `ready` starts the
  sweep in a detached process when `sweep.at_start` and `sweep.armed` are both on and
  the last removing run began more than ten minutes ago, and returns at once.
- `mark ROUND B<n> --brief FILE` sizes a worker's brief: its data block names one
  slice of the stage within `slice_minutes`, or one verification, or neither for a
  stage that declares no slices, which is one slice; the pass is recorded as
  `brief.sized` with the brief's SHA-256. `fold`, and `commission` where it declares
  the stage's slices, warn of a slice over the limit; `show` and the round note say which slices of an open build have a sized
  brief, and `next` names the ones that have none.
- `drop ROUND B<n>` drops one open build: the lead's when git reads it gone, or empty
  with no commit of its own and sharing with none, else the operator's. It records the
  branch's tip, deletes nothing and keeps the build's folders; the stage stays owed. A
  round's drop ends its open builds the same way, and the lead's is refused while one
  is not his. `show` and `list --waiting` name who drops each build to look at.
- `rule ROUND credit:B<n>` credits to a build what its target gained by a fast-forward
  with no reading of the build's: the build reads `merged` until `revoke:` takes it
  back.
- The finish lock: one finish at a time per repo and target, held as the ref
  `refs/rounds/finishing/<into>` (or the repo's `lock_ref`) pointing at a blob with the
  holder's line. A holder on this machine whose process has ended is cleared in one
  swap that git lets only one finish make; any other holder is named with the git
  command that clears it. The doctor names a lock older than an hour.
- A round's export has two views, both when `--view` is left out: `docket`, its open
  and parked questions, and `ratify`, the unruled decisions of its finished builds,
  which it names under `covers`, as the project's `ratify` setting picks them (`majors`,
  `majors+minors` with the minor ones as one group of ids, or `off`). The major
  decisions of builds not yet finished are listed under `later`. A build answers only
  for the decisions its own reports declared, so one only a dropped build declared is
  in neither list. A decision that forecloses a capability is always an entry of its
  own, marked `alone`.
- `ask ROUND TEXT --build B<n>`: the lead asks a question that came up in a build; it
  comes from the build at its branch's tip, and is listed as asked by the lead for the
  builder.
- `next` says the step a round is at, who does it and at which tier, what they read,
  the command that completes it and what it waits for; `next --schema` prints the empty
  data block of a report.
- `next` with no round, run in a build's worktree, finds the open build by its repo
  and branch and says its round's next step.
- A round whose flow has no `build` step closes itself, on the write that leaves every
  step done or skipped, no question open or parked and no stage in its plan. The verb
  whose write closes it says so in its last line. `show` says what an open round's
  close waits for, and `list --closed` lists a closed round with when it closed.
- The read API, interface 1: `rounds`, `round`, `waiting`, `questions`, and `builds`,
  each open build with its state read from git.
- Doctor rows for git, each project's rounds folder, each log and the index.
- Config: `defaults.dir`, `defaults.flow`, `defaults.trace`, `defaults.idle_days`, `flows`,
  `tiers`, `models` and per-project tables. The keys of the parts not built yet are declared and checked,
  and nothing reads them.
- Not in this version: builds, checks, verifying and finishing a build, decisions, the
  retrospective and a round's numbers, prepared answers and parent rounds.
- rounds needs moxi 0.1.7 or later and moxi-projects 0.1.4 or later. The `notes` extra
  gives a project that names no notes folder notes' default one.
