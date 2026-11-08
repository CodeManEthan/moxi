# moxi-dev

The contributor tools for the Moxi repository. It is a development package: the
repository's `uv sync` installs it, and no Moxi module depends on it at run time.

- `moxi-dev check` runs the checks a change must pass before it merges: the lock, the
  tag check, the release step (see below), the contract's import rules (import-linter
  and deptry) and the tests of the packages the change reaches. A test in the `slow`, `browser` or `build` tier runs
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

`moxi-dev <verb> --help` shows a verb's own arguments. From the repository root:

```sh
uv sync
uv run moxi-dev check
```

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
