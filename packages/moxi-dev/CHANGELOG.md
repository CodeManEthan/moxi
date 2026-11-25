# Changelog

## Unreleased

- `moxi-dev check --package NAME` checks exactly the named packages, with no dependent
  added, and `--leave-out NAME` every package the check would otherwise take but the
  named ones; each is given once per package, by its folder or distribution name. The
  lock and the whole-tree static steps still run, and the summary's `packages:` names
  what was checked. A name that names no workspace package stops the check with exit 2
  before any step. The two refuse each other, `--static`, `--only` and `--pick`.
- `console_script` on Windows writes `name.exe`, a copy of the launcher uv writes for a
  console script, in place of a `.cmd` file. Its source gets the arguments exactly as
  the program that started it passed them (cmd.exe cut one at a newline and read `&`,
  `|`, `^`, `%`, `<` and `>`), a start by bare name finds it, and `os.getpid()` in it
  is the pid of the process that was started, not the Python child the launcher runs.
  `__file__` in it is the stand-in's own path, as on POSIX, so it finds files kept
  beside it.
- On Windows the test fence keeps `PROGRAMDATA`, the machine's shared settings folder,
  as it keeps `SYSTEMROOT`: Windows' own OpenSSH (`ssh-keygen`, and git's SSH signing
  through it) exited 255 without it.
- `toml_string(value)` gives a path, or any text, as a TOML basic string that reads back
  as it is. A test writes a path into a TOML file through it, so a Windows path's
  backslashes don't read as escapes.
- The test fence holds every process the tests start, from a fixture of any scope, to
  the run's own folders. When the test loop starts, the kit puts a run's environment in
  place (its homes in the run's temp root, its runtime and temp directory in one
  run-level short directory that holds every short directory of the run), so a
  module- or session-scoped fixture and its children no longer see the real homes. Every
  child gets uv's folders under the run's roots, one uv cache per run that its xdist
  workers share (tests that install third-party packages download into it), the
  suite's own Python with downloads off, and byte code off. A seam over
  `subprocess.Popen` adds what a child's own environment lacks, refuses one that points
  a home, temp directory or uv folder outside the run, and gives an isolated Python child
  a byte-code folder in the run. `uv_env` is now the fenced environment with these keys,
  not uv's real cache, and `started_env()` gives the environment pytest started with.
- `moxi-dev check` bounds each package's test run at 900 s of wall clock, or the
  seconds `MOXI_TEST_SUITE_BOUND_S` gives. A run past it is ended with every process
  under it (the process group on Linux and macOS, a job on Windows), and the check
  prints a line naming the package and the bound, fails that package and still runs the
  rest; the summary line marks it `ended at its bound`. Ten seconds before the bound,
  each process of the run still going writes its threads' stacks under
  `.moxi-dev/run/<package>.stacks/`, which the line names.
- The test kit writes each process's stacks at a set time when `MOXI_TEST_STACKS` and
  `MOXI_TEST_STACKS_AT` name a directory and a time (`arm_stacks`), a process that
  saw a failed or errored test too: the kit arms the dump again after pytest's
  faulthandler plugin cancels it.
- The run ends with the check: a check stopped by SIGTERM or SIGHUP (Ctrl-Break on
  Windows) ends the run's tree as Ctrl-C does, and exits 128 plus the signal's number
  (a signal the check was started ignoring, as `nohup` leaves SIGHUP, stays ignored);
  for a check killed outright, the test kit ends the run and every process under it
  once the process `MOXI_TEST_PARENT_PID` names is gone (`watch_parent`).
- moxi-dev requires moxi 0.1.7 or later, and psutil 5.9 or later, which the test kit's
  parent watch uses.
- `MOXI_TEST_TEMP` (`disk` or `ram`, unset meaning `disk`) decides where a test run keeps
  its temp: on Linux `/var/tmp` or `/dev/shm`, elsewhere the system's own, where `ram`
  runs as `disk` and says so. The kit's short directories leave `/tmp` for the same root,
  inside one recorded directory per pytest process, where a test's own has a
  one-character name to keep its socket paths short. A run in memory removes a passed
  test's files when it ends and the whole run's at the run's end, and goes to disk when
  RAM is short. Each top-level run sweeps what killed runs of the kit left, by the owner
  records they hold locked, and touches nothing else. A run nested in a test (the fence's
  new `MOXI_TEST_FENCED` mark, or a fence active in its process) chooses and sweeps
  nothing. `moxi-dev check` and `moxi-dev timed` keep their tests' temp on disk whatever
  is set, and the check's summary gains a `temp` field. `moxi-dev check --static`
  starts no test, so it reads no `MOXI_TEST_TEMP`, sweeps nothing and says `temp not run`.
- `moxi-dev check --pick [--base REV]` prints the packages the change picks as one JSON
  line, `{"packages": [...]}`, and runs no step, so a workflow can run only the picked
  packages' jobs.
- On Windows the test fence places each module's `MOXI_<MODULE>_HOME` at
  `moxi\<module>` under the test's own `LOCALAPPDATA`, inside the test's profile, so a
  file a module keeps only inside the user's profile (comms' token store) is inside the
  test's. It spells every directory it gives in its long form, its real path, so a
  runner whose `TEMP` has an 8.3 short name no longer hands tests a short spelling to
  compare with resolved paths. Elsewhere the module homes stay in the test's short
  directory.
- `Fence.profile_dir(name)` makes a new directory inside the test's own profile (its
  home, `USERPROFILE` on Windows) for a file that must live there.
- The naming check reads a file's path with `/` separators on every platform, so a
  Windows contributor's `# noqa: MX...` marks count there too.
- `moxi-dev check` has a pronoun step, after the release step among the static steps,
  so `--static` runs it too: it refuses he, his, him and himself, as whole words in any case, in each
  package's Python string literals and docstrings, its web sources' strings and comments,
  and its README, docs and bundled skills, and prints each as `path:line: word` with the
  fix. A genuine use stands with `noqa: MX5 -- <why>` on its line. The summary line says
  `pronouns pass`, `fail` or `not run`, and `--only pronouns` runs it alone.
- `moxi-dev check` has a machine-text step, the last of the static steps, so `--static`
  runs it too: it refuses a path into one user's home folder in shipped text, a name
  from the running contributor's own private-names list (secrets') in any file, and a
  package-manager command in any file, and prints each as `path:line: machine text: ...`
  with the fix, never printing a private name. A genuine use of a home folder or a
  command stands with `noqa: MX6 -- <why>` on its line. The summary line says
  `machine pass`, `fail` or `not run`, `--only machine` runs it alone, and a
  private-names list secrets can't read exits 2. moxi-dev now requires moxi-secrets.
- `moxi-dev check` picks fewer packages. Closing over dependents leaves out edges to
  `moxi-dev`, which every package lists in its dev group, so a change to a package
  `moxi-dev` requires no longer picks them all; other dev-group edges still count (a ui
  change picks connector). A change in `moxi-dev` still picks every package, except one
  to its rename table alone, which picks `moxi-dev`. A root path a package's tests read
  picks that package: `README.md` picks memory, `.github/workflows/publishing.yml`
  and `release.yml` pick publishing, and `.github/workflows/ci.yml` and `queue.yml`
  pick `moxi-dev` alone, whose tests read them. The rest of `.github/`, `docs/` and
  `CONTRIBUTING.md` pick no package; the lock, the root `pyproject.toml` and the other
  root files still pick every one.
- `moxi-dev check --static` runs only the whole-tree static steps (tags, comment
  references, naming, Ruff, renames, release), with the same lines, summary line and
  exit codes as a full check gives them, and no lock check, package picking, sync, lint
  or tests. It takes about 80 to 140 s on this repository. It refuses `--only`, `--full`,
  `--base` and `--finding`.
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
