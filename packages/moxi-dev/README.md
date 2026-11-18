# moxi-dev

The contributor tools for the Moxi repository. It is a development package: the
repository's `uv sync` installs it, and no Moxi module depends on it at run time.

- `moxi-dev check` runs the checks a change must pass before it merges: the lock, the
  tag, comment-reference, naming and Ruff checks, the renames step (about 45 s here; see
  below), the release step (see below), the contract's import rules (import-linter and
  deptry) and the tests of the packages the change reaches. A test in the `slow`,
  `browser` or `build` tier runs when a path its `touches` mark names changed; `--full`
  runs every package and every tier but `live`; `--finding L<n>` runs only the tests
  marked with that finding (see "The tag check" below). It runs no `timed` test, in any
  form: it holds each one back and its last line counts them as `timed held <n>`;
  `moxi-dev timed` runs them. A finding whose tests are all timed counts as found, with
  a line that says so. Its last line also gives the tests' seconds, the slowest 5 %'s
  share of them and the change against the newest record on `main` (one record per
  commit, kept under git's own directory).
- `moxi_dev.testing` is the test kit every package loads from its root `conftest.py`:
  the fence around each test, the tier markers, the durations each run writes and the
  helpers tests share (`Seam` for a seam with two implementations among them).
- `moxi_dev.contract` holds the module contract check each package's
  `tests/test_contract.py` runs. `moxi-dev contract [--staged] [PATH]` runs it from a
  shell over any module's directory, inside the repository or not; `--staged` judges
  git's index, as the next commit lands.
- `moxi-dev new <module>` starts a new package under `packages/`.
- `moxi-dev stamp` checks that each web page's committed build matches its sources.
- `moxi-dev rename` applies the identifier changes in `renames.tsv` and reports old uses
  it cannot resolve.
- `moxi-dev timed` runs the timed tests on their own, each counted only when little other
  work ran on the machine during it, and keeps their results per commit and machine;
  `moxi-dev timed show` reads them, and `moxi-dev timed schedule` runs them each morning
  on Linux (see "Timed tests" below).

`moxi-dev <verb> --help` shows a verb's own arguments. From the repository root:

```sh
uv sync
uv run moxi-dev check
```

## The code checks

The comment reference step reads Python comments and docstrings and TypeScript and MJS
comments under `packages/`. It checks names and source files cited in backticks and every
`--flag` in that text. A name passes when its parts occur in code or tracked data, or its
first part is a builtin or standard-library module. A source file passes when a tracked
path ends with it or a code string names its base name. A flag passes when code or
tracked data contains it. Built JavaScript is not read. Absolute, home-relative and glob
file references are left alone.

Put an outside reference in the root `.refs-allow` as
`<path> TAB <exact text> TAB <why>`. It applies to that text in that file only. The check
also refuses an exception line no current comment uses.

The naming step reads every live numeric key in each `settings.toml`, each argparse flag
and CLI verbs table in `src/`, and the parameters of every exported module API. MX1
refuses numeric durations or sizes without the house unit suffix. MX4 refuses the verbs
and switch-key words listed in `CONTRIBUTING.md`, an `enabled` key that is not a bool,
and one key leaf used with different types. Retired keys are skipped.

MX2 reads `MOXI_` variable-name literals in `src/`, except docstrings and module
announcements. A literal in code must use a module-level constant. That constant ends
in `_ENV`, is the variable's only constant, and belongs to the package named by the
variable's module prefix. `MOXI_TEST_` belongs to moxi-dev; variables with no module
prefix belong to the core. Tests and announcements may write the literal directly.

MX3 reads Python bindings under every package's `src/`, `tests/` and other tracked
paths. It holds environment mappings to `env`, caught exceptions to `exc`, directories
to `<what>_dir`, database connections to `conn` or `<what>_conn`, open files to `fh` or
`<what>_fh`, print callbacks to `say`, and actor parameters to `by`. It does not treat
`urlopen` or `popen` results as files, non-database `connect` calls as database
connections, or stored `actor` fields as parameters.

Ruff runs from the repository root as `ruff check --no-cache .`. The root
`pyproject.toml` selects N, A and FBT, ignores N818, excludes the core's vendored code,
and declares MX as an external code so MX marks are valid. Ruff reports PEP 8 naming,
builtin shadowing and boolean traps in the same check summary as the other steps.

A valid MX mark with a reason suppresses that naming rule on its line. The naming step
refuses a bare `# noqa` and an MX, N, A or FBT mark without ` -- <why>`; put a parameter
exception on its function definition line and a settings exception on the key's table
line.

The renames step reads the tree as `moxi-dev rename --dry-run` reads it, in the check's
own process and with no `--main`: the check reads the tree it is given, and comparing
with main is for a branch taking main in. It fails on any row still to apply, any
leftover and any row the script refuses, and prints the first ten with their state;
`moxi-dev rename --dry-run` lists every one. It costs about 45 s on this repository's
table of about 16,400 rows, and reads the whole tree whatever the change picks. A tree
with no `packages/moxi-dev/renames.tsv` counts as `none`.

Run one step without the lock, release, package, lint or test steps with
`moxi-dev check --only refs [PATH...]`, `moxi-dev check --only names [PATH...]`,
`moxi-dev check --only ruff [PATH...]` or `moxi-dev check --only renames [PATH...]`.
Each step still reads the whole tree, but paths limit the findings it prints.

`moxi-dev rename [--table PATH] [--dry-run] [--main REF] [ROOT]` reads tab-separated
rows of `<package> TAB <path> TAB <scope> TAB <kind> TAB <old> TAB <new>` from
`renames.tsv`. The shipped table holds this repository's renames. A normal
scope is the qualified enclosing function or class, or `-` for module scope. Rows sort
by package and path.

The row kinds are:

- `name` renames a binding and each use the script resolves to it.
- `kwonly` makes a parameter keyword-only and names it at resolved positional calls.
- `use` changes one unresolved use. Its scope field is the exact stripped source line.
- `keep` covers one old token that means something else. Its scope field is also the
  exact stripped source line.

For a function removed on a branch, a keep row uses path `-` and a scope of
`removed:<module>:<qualified-name>`. This explicit removed-scope form is also how the
script distinguishes removal from a move when common qualified names occur in several
modules.

Before editing, the script classifies each row as `to apply`, `applied`, `not here` or
`dead`. A row is dead when its file is not one the script reads: a file the branch
deleted or renamed, or one that is vendored, is not a Python file under `packages/`, or
does not parse. A dead `name` or `kwonly` row has no definition to rename, and its old
name is no leftover anywhere; a dead `use` or `keep` row has no line to change or cover.
Every dead row is listed with its table row, file, kind and names and which of those
reasons holds, so a branch can drop it or point it at the file's new path; it does not
change the exit code. The removed-scope `keep` form names no file and is never dead.
A second run changes nothing. It resolves direct names, module aliases, from-imports,
re-exports through API modules, class constructors and methods, and a local assigned
from a resolved class constructor. It edits resolved tokens, matching `__all__` and
module `__getattr__` strings, and backticked citations of the old name where they mean
the row's definition: a parameter's or a function local's inside its function, a field's
inside its class, any other name's in the files the row edits. A parameter row leaves
`self.<name>` alone. A row whose edit would change what a token means is refused before
anything is written, naming its table line and the code line: its new name already bound
or read in the scope it edits, a nested function, lambda or comprehension with its own
binding of the old name, or a module-level name that is also a function's local. A
local that another row of the same scope renames away is no collision. A call resolved
to the standard library or a dependency is outside the repository, so its keyword is
left alone and is not a leftover. A callee that cannot be resolved is a leftover. For a
`kwonly` row on `Class.__init__`, resolved `Class(...)` calls reach the constructor and
the parameter position excludes `self`; an unresolved call named `Class` that passes
that position is a leftover.

The final list also includes unresolved old calls, attributes and keywords, string
lookups, old citations the script did not rewrite (a parameter's only in its own file),
and ambiguous moved scopes.
The command exits 1 while any remains. Settle a real unresolved use with a `use` row and
an unrelated token with a `keep` row. If several modules have the same qualified scope,
add a normal row at the definition's real path; that row clears the moved row. Use the
removed-scope keep form only when the function is truly gone.

Pass the merged main commit as `--main REF`. If it is an ancestor of `HEAD`, the script
checks rows absent from the branch against that tree and catches definitions the branch
moved. Without `--main`, or when REF is not an ancestor, it prints that the comparison
did not run. `--dry-run` performs every classification and leftover check without
writing files.

The script reads every Python file under `packages/`: a file outside a `src/` (tests,
conftest, scripts) is read for uses, and none of its definitions is a row's. It
resolves `from package import submodule`, imports inside functions, and a module's lazy
`__getattr__` table (a dict of names to the module that holds each). A field row on a
class renames the keyword at its resolved constructor calls; a `kwonly` row passes every
later positional argument by keyword and drops a star that stood later. A `use` row's old
text may be longer than the name, such as `clock.age_seconds` or `timeout=`, to pick one
token, and it changes every match on its line; `use` rows on one line apply together, and
a `keep` row on a line they change stays applied. A `keep` row also stops the cite
rewrite on its line. A keyword on a later line of a call is settled by a row on that
line. A run that would leave a file that does not parse writes nothing.

## The tag check

Code, tests and docs in this repository give their reasons in plain words. The tag
check, which `moxi-dev check` runs over every text file git doesn't ignore, refuses short
reference ids that point into notes a reader of the repository can't see: a capital
letter and a number in square brackets, a section sign with a number, a severity word
with a number, and a few more. Those are the shapes of moxi-publishing's `ruling-tag` and
`round-pointer` rules, read from `moxi_publishing.rules`, so this check and publishing's
scan of a public repo refuse the same text; to them it adds two shapes only this
repository's own records use, a D-number and a finding id (below). Its line names the
file, the line and the text it refused. Write the reason the id stood for instead.

Some text has to look like an id because it is data, such as the input a test feeds the
pattern that refuses it. `.residue-allow` at the repository's root lets such text stand
in one file. Each line is `<path> TAB <exact text> TAB <why>`, the why saying what data
the text is. A line lets that exact text through in that one file and nowhere else.

A finding is one reported issue: a bug, say, given a name of the form `L<n>`, an L and a
number. A test that covers it carries the mark `@pytest.mark.finding("L<n>")` with that
name, and `moxi-dev check --finding L<n>` runs only the tests carrying it in the
packages the check picks (every package with `--full`), so a fix can be checked against
just the tests written for it. A finding's name is let through inside the mark's
arguments and refused everywhere else, like any other id.

## The release step

Every package here is published to PyPI from this tree, so `moxi-dev check` checks what
publishing needs on every package, whatever the change touched:

- the core's catalogue (`packages/moxi/src/moxi/catalogue.toml`, the modules
  `moxi install` knows by name) lists exactly the packages that declare a `moxi.modules`
  entry point, each under its distribution name;
- every requirement one package places on another, in its dependencies or any extra,
  admits the other's version as it stands in the tree;
- each package declares `readme`, `license`, `requires-python`, `keywords`,
  `classifiers` and `[project.urls]`, has a `CHANGELOG.md` whose first version heading
  is its version and which its source distribution's `only-include`, when it sets one,
  lists, and its README links to nothing by a relative path (PyPI shows the README,
  where such a link leads nowhere; a link to a heading of the same page is fine);
- a README link to a file in the repository, `<Repository URL>/blob/<ref>/<path>`,
  names the package's own release tag at its current version as the ref
  (`moxi-comms-v0.3.0`), so the page the index shows for a version links to that
  version's files. The tag scheme and the CHANGELOG heading reader are
  moxi-publishing's, which moxi-dev requires.

Each problem's line names the package, the field and the fix. After `moxi-dev new`,
the catalogue line is the one thing left to add by hand.

## The web build's stamp

A module with a web page commits the files `npm run build` makes beside its sources, with
`build-stamp.txt`, a hash of the build's inputs. `moxi-dev stamp` recomputes that hash in
Python, with no Node, and fails a page whose sources changed without a rebuild; `moxi-dev
check` runs it on the packages a change picks, then, where Node and the root's
`node_modules` are present, `npm run check`, which compares the built files byte for byte
with a fresh build, so a hand-edited built file fails too. As a pre-commit hook it reads the index and
checks only the pages the commit touches, so a commit that touches no page is never
stopped by one. To install it, put this in `.git/hooks/pre-commit` and make it executable:

```sh
#!/bin/sh
exec uv run --locked moxi-dev stamp --staged
```

## Timed tests

A test marked `timed` asserts a wall-clock budget, which a busy machine breaks whatever
the test does, so `moxi-dev check` never runs one. `moxi-dev timed` does, from the
repository's root:

```sh
uv run --locked moxi-dev timed [--wait-s S] [--idle-line CPUS] [--package NAME]...
    [--finding L<n>]... [--machine NAME] [--record-dir DIR] [--no-record] [--scheduled]
```

It takes a run lock first (`.run-lock` in `<git common dir>/moxi-dev/timed/`; a second
run exits 2 and says another run is measuring), checks `uv.lock`, syncs its own
environment, `.moxi-dev/timed-env-3.12`, and runs each package's timed tests in one
session with no workers, every tier but `live`, writing under `.moxi-dev/timed-run/`. It
never touches the check's environment or files.

The idle gate (`moxi_dev.testing`, "The idle gate"): before each timed test the kit reads
how much other work ran on the machine, from the kernel's CPU counters less this run's
own CPU time, and again after it. A test counts only when other work over its own run
stayed under the line, 2.0 CPUs by default (`--idle-line`). The run waits for the line at
most `--wait-s` seconds in all (300 by default), spent once across every package. Each
test ends `passed`, `failed`, `skipped`, or `not measured` (the machine was busy during
it, whatever it read), which is neither a pass nor a failure. On macOS the gauge is the
one-minute load average; on Windows there is none yet, and a timed test there is not
measured.

The last line is the summary:
`moxi-dev timed: commit <sha> on <machine> | passed <n>, failed <n>, skipped <n>, not
measured <n> (...) | total <s> s | exit <code>`. Exit 0: every test passed or skipped; 1:
one failed, or a session broke; 3: none failed and some were not measured; 2: it couldn't
start (the lock held, a stale `uv.lock`, a failed sync, a refused option).

`MOXI_TEST_TIMED_SLACK=<factor>` passes through to the tests and multiplies every budget
(`moxi_dev.testing`, "Timed tests"). The run records the factor with each result.

The record. Each run is kept at `<record dir>/<machine>/<sha>.json`, the record dir being
`<git common dir>/moxi-dev/timed` (never tracked, shared by every worktree of the clone),
or `--record-dir`. `--machine` names the machine (letters, digits and `._-`; the short host
name by default). A file holds each run (when, the machine's system, CPU and Python, the
gauge, the line, the wait, the factor, the exit, `scheduled` or `hand`), each test's newest
result per factor, and, from the newest complete run (no `--package`, no `--finding`, every
session collected), the list of timed tests it collected. Within a factor, a pass or
failure replaces any earlier result, a skip replaces only `not measured` or a skip, and
`not measured` replaces only `not measured`. A tree with changes of its own keeps no
record, and neither does `--no-record`. A tree with no git (a host's run tree) measures
the commit its `.moxi-dev/timed/commit.json` names and needs `--record-dir`.

What settles a test. Each machine has one set factor: the factor of its newest scheduled
run, 1 when it has none. A pass counts when taken at or under that factor, a failure at
or over it, and a skip at any factor; any other result is kept and marked, and doesn't
count. `--scheduled` exits 0 with `already measured` when the record of this commit
already settles every collected test at this run's factor.

`moxi-dev timed show [REV] [--days N] [--machine NAME] [--json]` reads the record along
REV's first-parent line (`main`) back N days (7) from REV's time, each machine on its own,
the machine it runs on first. It prints each machine's set factor, its newest measured
commit and how many commits behind REV it is, its newest tick, and each test's newest
counted outcome with its reading and budget, a failure's range of commits, and the tests
with no counted outcome. A line before the machines says how many tests this machine's own
strict reading covers. Exit 1 when a test's newest counted outcome failed on any machine;
3 when some test of a machine's set has no counted outcome, or no machine has a complete
run in the window; 0 otherwise. A machine with no complete run and no counted failure is
listed and stays out of the exit.

`moxi-dev timed take DIR --machine NAME` merges another machine's record, `DIR` being that
machine's directory inside its results folder, into this clone's record under `NAME`, per
commit: a file missing here is added, one both hold is merged (runs united by start time,
results by the rule above, the collected list from the later complete run), and nothing
here is removed. Its tick files are added when missing and never changed. A file that
doesn't parse, names another machine or has a format this moxi-dev doesn't know is named
and left, and the exit is 2; `nothing to take` (exit 0) when the source is missing or
empty, and exit 2 when its results folder is gone.

### The scheduled run

`moxi-dev timed schedule` runs the timed tests every morning on Linux, as systemd user
units. Run it once on each machine that measures, from the tree it should measure (the
source), which is a uv workspace root:

```sh
uv run --locked moxi-dev timed schedule --machine NAME [--slack F] [--idle-line CPUS]
    [--record-dir DIR] [--stamp] [--take NAME=DIR]...
```

On macOS it refuses, since the timer is Linux only (run `moxi-dev timed` by hand there),
and on Windows too. On a git clone the run tree is a worktree of it, made with `git
worktree add --detach`, and the record goes to the clone's git directory unless
`--record-dir` says otherwise. On a tree with no git (a synced copy, below) the run tree is
a verified copy, `--record-dir` is required and `--stamp` refused. The run directory,
`<user cache>/moxi-dev/timed/<12 hex of the source's path>` (`~/.cache` unless
`XDG_CACHE_HOME` says otherwise), holds the run tree (`tree/`), the machine's settings
(`schedule.json`) and the tick script (`tick.py`). `--slack` is the machine's set factor,
1 by default, which every scheduled run passes as `MOXI_TEST_TIMED_SLACK`; `--idle-line`
is passed to each run.

It writes `moxi-dev-timed.timer`, which fires at 08:00, 09:00, 10:00 and 11:00 local time
and never runs a missed tick later, and `moxi-dev-timed.service`, which runs one tick (at
most 40 minutes; exit 3 counts as success) under the uv-managed Python 3.12 it found. When
a uv upgrade has removed that interpreter, the service prints `moxi-dev timed tick:
<machine>: interpreter missing: <python> (Python <version>); run schedule again` and exits
2. With `--stamp` it also writes `moxi-dev-timed-stamp.path` and its service, which write
the stamp (below) each time main moves.

Before the timer starts, `schedule` checks, in order, and exits 2 with no unit left when a
check fails: the source is a uv workspace root and its clone's top level; uv, Python 3.12
and, on a clone, git resolve to absolute paths; with `--stamp`, main's reflog exists, and
each `--take` folder exists; linger is on, or it says the timer fires only while you are
logged in (`loginctl enable-linger` keeps it firing) and goes on; the run tree can be made
and `uv sync --locked` in it succeeds; each unit passes `systemd-analyze --user verify`.
Its last lines name a one-tick test: `systemctl --user start --no-block
moxi-dev-timed.service`, which returns at once, then, once `systemctl --user is-active
moxi-dev-timed.service` no longer prints `activating`, `journalctl --user -u
moxi-dev-timed -n 5` for the tick's summary line.

`schedule` run again rewrites the settings, the tick script and the units (a new factor, a
take added) and never touches a unit's drop-in directory (`moxi-dev-timed.service.d/`), so
a drop-in of your own on the service, such as a note when a tick fails, stays. `moxi-dev
timed unschedule`, from the same source, stops the timer and the path unit, removes the
units and the run directory, and leaves the record, the source and every drop-in. When the
run tree exists, both take the run lock first, and when a run holds it they exit 2 with
"a timed run is measuring here" and change nothing.

The tick. `tick.py` uses the standard library alone, so a commit that breaks moxi-dev
can't stop the next tick from bringing in its fix; what measures is the commit's own
`moxi-dev timed`. Each tick, in order; a step that fails prints `moxi-dev timed tick:
<machine>: <step> failed: <reason>` and the tick goes to step 6:

1. Lock: the run lock, taken without waiting, on a descriptor the run inherits.
2. Stamp (with `--stamp`): the stamp written when it is missing, doesn't parse or names a
   commit other than main's.
3. Tree: on a clone, `git checkout --quiet --force --detach main` in the run tree; on a
   tree with no git, the verified copy. A copy that doesn't verify ends the tick `not
   measured: tree not settled: <cause>`, which is no failure.
4. Run: `moxi-dev timed --scheduled --machine <name>` in the run tree, with the settings'
   `--record-dir` and `--idle-line`, the machine's factor, and the lock's descriptor in
   `MOXI_TEST_TIMED_LOCK_FD`. Its output, the summary line included, goes to the unit's
   journal. It is cut at 35 minutes.
5. Take: for each `--take NAME=DIR`, `moxi-dev timed take DIR/NAME --machine NAME`,
   whatever the run did.
6. The tick file, then the exit: 2 when the lock, the stamp, the checkout, the copy or a
   take failed, or the run exited 2 or couldn't start; 3 for a tree not settled; 1 for a
   run cut at its bound; otherwise the run's own exit.

A change to the tick raises its `TICK_VERSION`, and then `schedule` must run again on each
machine to copy the new tick in. `show` prints each machine's newest tick's version beside
its own and marks an older one `run schedule again on <machine>`. `tick.py --hand [--slack
F]`, run with the run directory's Python, runs steps 1, 3 and 4 with a run that isn't
`--scheduled` and writes no tick file.

Tick files. Each scheduled tick writes `<record dir>/<machine>/ticks/YYYYMMDDTHHMMSSZ.json`,
named for its start in UTC, a JSON object with `format` (1), `machine`, `started`,
`tick_version`, `commit` (null when no tree was settled), `steps` (each of `lock`, `stamp`,
`tree`, `run` and `take`: `ok`, `failed` or `skipped`, and `tree` also `not settled`),
`failure` (the first failure line, or null), `not_settled` (the cause, or null), `summary`
(the run's summary line, or null), `exit`, and `invocation` (systemd's `INVOCATION_ID`). A
note of your own may read them, from a drop-in on `moxi-dev-timed.service`; moxi-dev ships
none.

### Measuring on another machine

A test host needs no clone and no git directory. A clone's working tree is synced to it
(by Syncthing, say) without `.git`, and its results are synced back:

- The stamp. `.moxi-dev-stamp.json` at the clone's root, which git ignores, names main's
  commit and lists every path of that commit's tree with its mode and blob hash.
  `schedule --stamp` on the clone writes it each time main moves (a path unit on main's
  reflog) and at each of that machine's ticks, under its own lock,
  `.moxi-dev/stamp.lock`. The synced folder should leave out `.git`, `.venv`,
  `.moxi-dev` and the other paths git ignores, and carry the stamp.
- The verified copy. On the host, each tick reads every path the stamp lists from the
  synced tree and checks its bytes against the stamp before it writes anything; then it
  removes from the run tree what the stamp doesn't list (leaving `.venv`, `.moxi-dev`,
  `__pycache__`, `.pytest_cache`, `.ruff_cache` and `node_modules`), writes what differs,
  and records the stamp's commit for the run. While the sync hasn't landed, or the
  clone's working tree isn't main's, the tick ends `tree not settled: <cause>` (`missing
  <path>`, `differs <path>`, `stamp missing` or `stamp unreadable`), exit 3, and measures
  nothing.
- The results folder. The host keeps its record in a folder synced back to the clone's
  machine, its `--record-dir`. The folder should leave out the record's `.lock` and
  `*.part` files. On the clone's machine, `schedule --take NAME=DIR` names the folder the
  host's results arrive in, and each of its ticks runs `moxi-dev timed take DIR/NAME
  --machine NAME` after its own run, so the host's runs and tick files join this clone's
  record, which `show` reads. `moxi-dev timed take` does the same by hand.

For example, with the host's results folder `~/moxi-timed` arriving on the clone's machine
as `~/moxi-timed-host-a`, on the clone's machine and then on the host:

```sh
uv run --locked moxi-dev timed schedule --machine alpha --stamp --take host-a=~/moxi-timed-host-a
uv run --locked moxi-dev timed schedule --machine host-a --record-dir ~/moxi-timed
```

The host needs uv with its Python 3.12, git on its PATH (some timed tests build temporary
repositories), and linger, so its timer fires while no one is logged in.

## Browser tests

A package whose tests drive a browser (its declarations name Playwright, or
`moxi-ui[testing]`) runs them in `moxi-dev check` in the same session as its other tests,
when Playwright's Chromium starts there. Install Chromium once with
`uv run playwright install chromium`. When it can't start, the check prints why and leaves
those tests out, even the ones `--full` or a changed path would put back, and the summary
line says so.

MIT licensed; see `LICENSE`.
