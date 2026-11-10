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
- `next` says the step a round is at, who does it and at which tier, what they read,
  the command that completes it and what it waits for; `next --schema` prints the empty
  data block of a report.
- A round whose flow has no `build` step closes itself, on the write that leaves every
  step done or skipped, no question open or parked and no stage in its plan. `show`
  says what an open round's close waits for, and `list --closed` lists a closed round
  with when it closed.
- The read API, interface 1: `rounds`, `round`, `waiting`, `questions`.
- Doctor rows for git, each project's rounds folder, each log and the index.
- Config: `defaults.dir`, `defaults.flow`, `defaults.trace`, `flows`, `tiers`, `models`
  and per-project tables. The keys of the parts not built yet are declared and checked,
  and nothing reads them.
- Not in this version: builds, checks, verifying and finishing a build, decisions, the
  retrospective and a round's numbers, prepared answers and parent rounds. A round
  whose flow has a `build` step does not close, and ends by `drop`.
- rounds needs moxi 0.1.7 or later and moxi-projects 0.1.4 or later. The `notes` extra
  gives a project that names no notes folder notes' default one.
