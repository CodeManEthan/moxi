# Changelog

## 0.1.0 (unreleased)

The first version. It keeps the record of a round and does not yet run one.

- `moxi rounds open`, `ask`, `rule`, `mark`, `show`, `list` and `drop`.
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
- The read API, interface 1: `rounds`, `round`, `waiting`, `questions`.
- Doctor rows for git, each project's rounds folder, each log and the index.
- Config: `defaults.dir`, `defaults.flow`, `flows`, `tiers` and per-project tables. The
  keys of the parts not built yet are declared and checked, and nothing reads them.
- Not in this version: design versions and verification, the next-step command, builds,
  checks, finishing and closing, decisions, prepared answers and parent rounds.
- rounds needs moxi 0.1.7 or later and moxi-projects 0.1.4 or later. The `notes` extra
  gives a project that names no notes folder notes' default one.
