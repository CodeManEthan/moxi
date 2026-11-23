# Changelog

## Unreleased

- `moxi-dev check` picks fewer packages. Closing over dependents leaves out edges to
  `moxi-dev`, which every package lists in its dev group, so a change to a package
  `moxi-dev` requires no longer picks them all; other dev-group edges still count (a ui
  change picks connector). A change in `moxi-dev` still picks every package, except one
  to its rename table alone, which picks `moxi-dev`. A root path a package's tests read
  picks that package: `README.md` picks memory, and `.github/workflows/publishing.yml`
  and `release.yml` pick publishing. The rest of `.github/`, `docs/` and
  `CONTRIBUTING.md` pick no package; the lock, the root `pyproject.toml` and the other
  root files still pick every one.
- The test fence keeps `MOXI_TEST_REAL_SUPERVISOR` as pytest started with it, and leaves
  it unset when it was. Before, a fenced test lost it at call time, so every proof row on
  a real supervisor failed with `SupervisorRefused` (or skipped, where the row read it in
  its body) even with the variable set.
- `moxi-dev timed` runs the timed tests on their own: a run lock first, its own
  environment (`.moxi-dev/timed-env-3.12`), one session per package with no workers and
  the idle gate, and a summary line with exit 0, 1, 3 (some test not measured) or 2. It
  keeps each run per machine and commit under `<git common dir>/moxi-dev/timed/` (or
  `--record-dir`), with the slack factor of each result, and `--scheduled` leaves a commit
  whose tests are all settled. `MOXI_TEST_TIMED_LOCK_FD` hands it a lock its parent holds.
- `moxi-dev timed show` reads that record along a first-parent walk, each machine at its
  own set factor, with its own exit codes; `moxi-dev timed take` merges another
  machine's record into this clone's, per commit, never removing anything.
- `moxi-dev timed schedule` (Linux) writes a machine's timer as systemd user units: a
  tick at 08:00, 09:00, 10:00 and 11:00 local time that measures the commit in a run tree
  of its own (a worktree of a clone, or a verified copy of a tree with no git) at the
  machine's set factor (`--slack`), with `--record-dir`, `--idle-line`, `--stamp` and
  `--take`. It checks the source, the tools, linger and the units before the timer
  starts. `moxi-dev timed unschedule` removes the units and the run directory and keeps
  the record. Both take the run lock first when the run tree exists, and never touch a
  drop-in.
- The tick, `tick.py`, uses the standard library alone: it locks, writes the stamp
  (`--stamp`), brings the run tree to one commit, runs `moxi-dev timed --scheduled`,
  takes each host's results, and writes a tick file under `<machine>/ticks/` in the
  record. A host with no git measures the commit its synced tree's stamp names, once every
  path matches, and reads as `tree not settled` (exit 3) until then. A new
  `TICK_VERSION` needs `schedule` run again on each machine.
- `moxi-dev check` has a renames step, after the Ruff check: it fails when the tree is
  not settled under `renames.tsv` (a row still to apply, a leftover or a row the script
  refuses), read in its own process with no `--main`, and prints the first of them with
  their state. The summary line says `renames pass`, `fail`, or `none` for a tree with no
  table. `--only renames` runs it alone. It costs about 45 s on this repository.
- `moxi-dev check` runs no `timed` test, in any form: the serial pass after the parallel
  one is gone, and the summary line says `timed held <n>` (or `timed none`) for the
  tests the suites held back. A `--finding` whose tests are all timed no longer fails
  for want of a test: it prints that they are timed and that `moxi-dev timed` runs them.
  The durations record holds the parallel pass only, so the first delta against a
  record that still holds timed tests reads lower once.
- `moxi-dev rename` reads a row whose file is not in the tree as dead: it applies nothing,
  its old name is no leftover anywhere (a `name` or `kwonly` row there used to list every
  string, attribute, keyword and cite of that spelling across the repository), and it is
  listed on a `dead:` line with its table row, file, kind and names and why the script
  reads no file there. The summary counts dead rows apart from `not here` ones, and the
  comparison with `--main` takes both.
- `MOXI_TEST_TIMED_SLACK=<factor>`, read once when pytest starts, is how much slower than
  the machine that set the budgets this one may be; `timed_slack()` returns it (1.0 when
  unset), inside a fenced test too. Every upper bound on a wall-clock measurement in a
  `timed` test is multiplied by it; lower bounds, ratios and counts are not. A value that
  is not a finite number of at least 1 stops the run.
- The tag check reads its shapes from moxi-publishing's `ruling-tag` and `round-pointer`
  rules, adding only the D-number and finding-id shapes, so it refuses what publishing's
  scan refuses. It now refuses more: among others, `round` and any home and slug joined by
  a slash, plain words included, and a memory key.
- The release step wants a README link to a file in the repository to name the
  package's own release tag at its current version (`.../blob/moxi-comms-v0.3.0/...`),
  not `main`; a relative link's fix gives that form. moxi-dev now requires
  moxi-publishing, whose tag scheme and CHANGELOG heading reader it uses.
- The release step refuses a workspace member on neither or both of the root's
  `[tool.moxi-publishing]` `not-ready` and `ready` lists, when the root keeps a `ready`
  list, so a member added later never starts ready.
- `moxi-dev timed show --package NAME` reads one package's timed tests, by its directory
  or distribution name in the tree it runs in, as a release reads its own: every machine's
  set, failures, exit and strict line count that package's results alone, and a package
  with none collected reads `<name> has no timed test collected by a complete run on the
  walk to <commit>; nothing to read`.

## 0.1.1 (2026-11-08)

- `moxi-dev check` now runs Ruff's N, A and FBT rules over the repository, reports Ruff
  in its summary, and accepts `--only ruff`. moxi-dev depends on Ruff 0.17, and the root
  configuration excludes vendored code, ignores N818 and permits MX exception marks.
- MX2 checks environment-variable constants for the `_ENV` suffix, one constant per
  variable, the owning package and direct literals in source code.
- MX3 checks the house words for environment mappings, caught exceptions, directories,
  database connections, open files, print callbacks and actor parameters.
- Exception marks for N, A and FBT now need ` -- <why>`, as MX marks already did.

- `moxi-dev rename` can apply a real table: it is fast; reads tests, conftest and
  scripts; resolves submodule imports, lazy `__getattr__` re-export tables and imports
  inside functions; renames dataclass field keywords; makes `kwonly` rows work with an
  existing star; rewrites `__all__` only in its list and backticked cites only where
  they mean the renamed definition; refuses to write a file that would not parse. A
  `use` row's old text may be longer than the name and changes every match on its line;
  a `keep` row also guards a cite on its line; a keyword on a later line of a call is
  covered by a row on that line; a keep row on a line a use row changed stays applied.
  A relative import in a package's `__init__` resolves from the package; a parameter row
  leaves `self.<name>` alone and reads its state from the signature; a parameter's cite
  left unrewritten is listed only in its own file.
- `moxi-dev rename` refuses, before writing, a row whose edit would change what a token
  means (its new name already in the scope, a nested scope with its own old name, a
  module-level name that is also a function local), naming the table line and the code
  line. A row may rename a function's local out of a parameter's way.
- `moxi-dev check`'s naming check names the house suffix in each unit hit, reads a mark
  on a decorated function's def line, and reports an API module that does not import;
  the refs check refuses a repeated `.refs-allow` line.
- `renames.tsv` holds this release's renames.

The first public release.

- `moxi-dev check` now runs whole-tree comment-reference and public-name steps, reports
  `refs` and `names` in its summary, and can run either step alone with `--only`.
- Contract check 7 holds daemon and timer console scripts to their announcement keys.
- `moxi-dev rename` applies the tab-separated identifier rename table, preserves
  resolved imports and calls, and reports unresolved or moved old uses.
- The README explains the tag check in plain words: what it refuses, what
  `.residue-allow` is, and what a finding, its `finding` mark and `--finding` are.
- `moxi-dev new` writes a core requirement of `moxi>=0.1.6,<0.2`, the oldest core a
  module has been run against from wheels.
- The contract check's names rule and `moxi-dev new` refuse `test` and say why each kept
  name is refused, reading `moxi.names.reserved_why`.
- The contract check gains check 9: a module reached by `moxi.modules.load` or
  `available` with a literal or constant name must come with the package, a required
  dependency, an extra, or a module an extra requires.
- The contract check counts the core's `render` and `harnessfile` among the core modules
  another module may import.
- The test fence answers the unit plan's listener connect with "nothing listens" in
  every test, so what listens on the machine's ports never decides a test. A test marked
  `honest_ports` (a new marker), or `live`, makes the real connect.
- moxi-dev needs moxi 0.1.6 or later, and its dependency now says so.
- `moxi-dev check` runs a release step over every package: the catalogue lists exactly
  the module packages, every requirement between packages admits the other's version,
  and each package has `readme`, a licence, `requires-python`, keywords, classifiers,
  `[project.urls]`, a CHANGELOG whose first entry is its version and which its sdist
  includes, and a README with no relative link. moxi-dev now depends on `packaging`,
  which reads the requirements.
- `moxi-dev new` writes `readme`, keywords, Linux-only classifiers and a `CHANGELOG.md`,
  so a new package passes the release step once the catalogue lists it.
