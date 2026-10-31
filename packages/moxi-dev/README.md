# moxi-dev

The contributor tools for the Moxi repository. It is a development package: the
repository's `uv sync` installs it, and no Moxi module depends on it at run time.

- `moxi-dev check` runs the checks a change must pass before it merges: the lock, the
  tag check, the contract's import rules (import-linter and deptry) and the tests of the
  packages the change reaches. A test in the `slow`, `browser` or `build` tier runs
  when a path its `touches` mark names changed; `--full` runs every package and every
  tier but `live`; `--finding L<n>` runs only the tests marked with that finding. Its
  last line gives the tests' seconds, the slowest 5 %'s share of them and the change
  against the newest record on `main` (one record per commit, kept under git's own
  directory).
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
