# Changelog

## Unreleased

- Stacked builds: of two open builds where one's commits are strictly part of the
  other's, `finish` lets the one whose work reaches the target first go first and holds
  the other until that work is on the target, in place of refusing both for sharing
  commits. Normally the inner build goes first; an outer build already merged by hand
  goes first and records the landing. Builds that overlap, or hold the same commits, are
  refused as before. The rule holds under the finish lock too. `next` gives a held
  finish as waiting with the build to finish first; `show`, `list` and the drop refusal
  word each share as `inside`, `contains` or `shares with`, and each share in `--json`
  gains `relation`.
- `commission --existing` takes a branch strictly inside or around another open build's,
  and refuses one that overlaps or holds the same commits, naming the build and the
  relation, and one a recorded landing of another build already brought onto the
  target.
- A frozen copy edited after its freeze, as `design.md` was, is said to be so, with the
  new repair `mark ROUND --restamp --record PTR`, in place of asking for a fold. It
  records the copy's digest as the version's re-stamped one in a `repair` event,
  changes no standing, and an ended round takes it. `show` lists it under Repairs and
  on the versions line.
- The numbers: the step times are labelled as overlapping, and the numbers gain
  `round_time_s`, open to close, and `longest_gap`, the longest stretch with nothing
  logged with the steps due across it. `show` computes both for a round that ended
  before them; `list --numbers` sums `round_time_s` and says how many rounds hold it.
- One rule says whose an open decision is: the operator's exactly when the close waits
  for the operator's ruling of it, that is when the round's flow has a `ratify` step
  that is not skipped, the project's `ratify` setting names its weight, its stage is
  finished, and the build that finished it declared it. The close, the `ratify` step,
  the labels, the ratify export and `list --waiting` all read it. Under each open
  decision `show`, the note, `verdict` and `list --waiting` say `the operator's to
  rule`, `not yet put to the operator` while only its stage is unfinished, `not put to
  the operator (<why>)` for a major one, or `the lead's to settle` for a minor one, and
  in an ended round that it is left open. Under `majors+minors` the minor decisions
  of a finished build hold the close and the `ratify` step until the operator rules
  them. A round whose flow has no `ratify` step, or whose `ratify` is skipped, puts no
  decision in its export, which says why in `not_ratified`. The write that closes a
  round lists each decision of a finished build it left open, and `show` of an ended
  round keeps the list.
- The lead settles a minor decision that is not the operator's:
  `mark ROUND B<n>.M<k> --accept|--reject --record PTR`, one `decision.settled` event.
  The close waits for every such one, in every flow, and a reject owes a fix as the
  operator's does. `mark ROUND B<n>.M<k> --major --record PTR` raises a decision to
  major, one `decision.raised` event; a settlement lapses once the weight rises. The
  operator's ruling of a settled decision replaces it and names its line,
  `settlement`. The numbers gain `settled`, read as zero in numbers written before it.
- User-facing wording names the operator neutrally: output, refusals, help, the export,
  the README and the shipped skill say "the operator" in place of a pronoun.
- `commission --existing` refused on a branch another build's landing brought onto the
  target now gives a second way that works: delete the branch, then commission without
  `--existing`.
- Tests: the rounds tests fail at setup when the test fence is not loaded, and a config
  path outside the test's own tmp fails the test.

## 0.1.2 (2026-11-21)

- The rounds page, with the `ui` extra (`moxi-ui` 0.2.4 or later): at `/m/rounds/` in the
  browser UI shell, every open round as one line of its steps, the steps in columns lined
  up across rounds, with a line for each build in flight. It shows what waits on the
  operator with `Go through in order`, the ten builds last acted on, the rounds grouped
  as `Your turn`, `Stuck or red`, `Moving` and `Wrapping up`, the closed rounds folded,
  and the key. A marker opens its step's record, a question, decision or job in place,
  and the address keeps what is open. It reads only and writes nothing. At 720 px and
  under it is a column of rounds, each opening a sheet with a vertical rail. Its routes
  are `/api/rounds`, `/api/record` and the stream `/api/events`, none open to the phone
  gateway.

## 0.1.1 (2026-11-21)

- A round's lead: `open --lead NAME` names the agent who leads the round, and `mark
  ROUND --lead NAME` names a new one, written as `lead.changed` with the one before. A
  name is checked for its shape only. A round with no lead of its own reads its
  project's. `show` gives it in a line and in its `--json` head, each row of `list
  --json` and of the read API's `rounds()` carries it, and the round note has a line for
  it. The index gains a column for it, so rows written before read as stale and are
  answered from the logs.
- A page's act as a pointer: `page:<page>/<act id>@<UTC time>`, taken wherever a pointer
  is. `mark --shown` refuses a page's act from before a ruling it marks was recorded.
- `moxi_rounds.actors.default()`: the OS user an act is recorded as done by when no
  name is given, one answer for the command line and for a page that calls the verbs'
  functions in process.

## 0.1.0 (unreleased)

The first version. It keeps the record of a round and takes it from its design through
its builds, their checks, verification and finish, to the ratification of their
decisions and the round's close.

- Every setting carries page fields, which `moxi config rounds show --json` prints
  and the settings page draws: a plain label, a line where the label can't carry it,
  the unit of each number, and words for each value of `defaults.ratify` and
  `defaults.check_on`. rounds needs moxi 0.1.9 or later.
- A round's log writes a path under the home folder as `~/<rest>`, and the home folder
  itself as `~`: a build's worktree and scratch folder, a check run's argv words and
  output file, the argvs of a read of CI, the argv words of a command step in the
  round's flow, and a report or brief kept outside the round's folder. A path read from
  the log is expanded before it is used, so a log whose paths were written with `~` is
  checked, finished and swept, and an older log that holds the whole paths reads as
  before. Only `~` and a path that starts with `~` and a separator are expanded; `~user`
  is read as written. A check run recorded with `~` counts against the command that
  names the whole path, and the other way round. A word of a command step's argv in the
  round's flow that is `~` or starts with `~/` runs as the path under the home folder.
  A rounds without this change reads a log written with `~` paths as relative paths, so
  it shows builds as not clean or not there and keeps their folders rather than
  removing them; update rounds first on every machine that reads the same notes.
- `moxi rounds finish` is refused while a process uses the build's scratch folder,
  naming the process. `finish` and `drop` don't move a scratch folder a process uses:
  it stays for the sweep of leftover folders, named with the process, and a drop goes
  through.
- `moxi rounds check` holds the machine's check lock while its command runs, so one
  check runs at a time on the machine; another run waits for it, or with `--no-wait`
  is refused. A run with no `timeout_s` from the call, the check or the step stops at
  3600 s, and a run at its limit gets SIGINT and 30 s later SIGKILL. rounds needs moxi
  0.1.11 or later.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_rounds.api`: `round` is now `round_data`.
- `moxi rounds open`, `fold`, `verdict`, `commission`, `check`, `finish`, `ask`, `rule`,
  `mark`, `next`, `show`, `list` and `drop`.
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
- The `ratify` step: ready while a major decision of a finished build is unruled, each
  named with the command that rules it; done once none is left and the plan is
  finished, or when the project's `ratify` is `off`.
- A round's numbers, counted from its log, in the `round.closed` or `round.dropped`
  event that ends it, with the time it spent at each step; `show` gives them, `retro.md`
  is written beside the round's note from them, and `list --numbers` sums those of the
  closed rounds.
- The retro step: due once a round's close waits for nothing else, with the numbers as
  they stand in `next`; a retro report recorded with `verdict` makes it done, and the
  round closes on it.
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
  is not the lead's. `show` and `list --waiting` name who drops each build to look at.
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
- Doctor rows for git, each project's rounds folder, each log, the index, CI in each
  repo that declares it, finish locks and the sweep's dry run.
- One shipped skill, `moxi-rounds`, placed for each harness at install: the judgment the
  gates leave to the lead and the checkers, with no command manual.
- Config: `defaults.dir`, `defaults.flow`, `defaults.trace`, `defaults.idle_days`, `flows`,
  `tiers`, `models` and per-project tables. `defaults.dormant_days` and
  `defaults.sitting_at`, for parts not built yet, are declared and checked, and nothing
  reads them.
- Not in this version: prepared answers, the suggestion of a sitting, parent and child
  rounds, and `list --dormant`.
- rounds needs moxi 0.1.7 or later and moxi-projects 0.1.4 or later. The `notes` extra
  gives a project that names no notes folder notes' default one.
