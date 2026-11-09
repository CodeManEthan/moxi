# moxi-dev

The contributor tools for the Moxi repository. It is a development package: the
repository's `uv sync` installs it, and no Moxi module depends on it at run time.

- `moxi-dev check` runs the checks a change must pass before it merges: the lock, the
  tag, comment-reference and public-name checks, the release step (see below), the
  contract's import rules (import-linter and deptry) and the tests of the packages the
  change reaches. A test in the `slow`, `browser` or `build` tier runs
  when a path its `touches` mark names changed; `--full` runs every package and every
  tier but `live`; `--finding L<n>` runs only the tests marked with that finding (see
  "The tag check" below). Its last line gives the tests' seconds, the slowest 5 %'s
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
and one key leaf used with different types. Retired keys are skipped. A valid MX mark
with a reason suppresses that rule on its line. The step refuses a bare `# noqa` and an
MX mark without ` -- <why>`; put a parameter exception on its function definition line
and a settings exception on the key's table line.

Run one step without the lock, release, package, lint or test steps with
`moxi-dev check --only refs [PATH...]` or `moxi-dev check --only names [PATH...]`. Each
step still reads the whole tree, but paths limit the findings it prints.

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

Before editing, the script classifies each row as `to apply`, `applied` or `not here`.
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
with a number, and a few more shapes listed in `moxi_dev/tags.py`. Its line names the
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
  where such a link leads nowhere; a link to a heading of the same page is fine).

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

## Browser tests

A package whose tests drive a browser (its declarations name Playwright, or
`moxi-ui[testing]`) runs them in `moxi-dev check` in the same session as its other tests,
when Playwright's Chromium starts there. Install Chromium once with
`uv run playwright install chromium`. When it can't start, the check prints why and leaves
those tests out, even the ones `--full` or a changed path would put back, and the summary
line says so.

MIT licensed; see `LICENSE`.
