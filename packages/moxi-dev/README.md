# moxi-dev

The contributor tools for the Moxi repository. It is a development package: the
repository's `uv sync` installs it, and no Moxi module depends on it at run time.

- `moxi-dev check` runs the checks a change must pass before it merges: the lock, the
  tag check, the contract's import rules (import-linter and deptry) and the tests.
- `moxi_dev.contract` holds the module contract check each package's
  `tests/test_contract.py` runs.
- `moxi-dev new <module>` starts a new package under `packages/`.

`moxi-dev <verb> --help` shows a verb's own arguments. From the repository root:

```sh
uv sync
uv run moxi-dev check
```

MIT licensed; see `LICENSE`.
