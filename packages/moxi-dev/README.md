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
  `tests/test_contract.py` runs.
- `moxi-dev new <module>` starts a new package under `packages/`.

`moxi-dev <verb> --help` shows a verb's own arguments. From the repository root:

```sh
uv sync
uv run moxi-dev check
```

MIT licensed; see `LICENSE`.
