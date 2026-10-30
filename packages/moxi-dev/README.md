# moxi-dev

The contributor tools for the Moxi repository. It is a development package: the
repository's `uv sync` installs it, and no Moxi module depends on it at run time.

- `moxi-dev check` runs the checks a change must pass before it merges.
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
check` runs it on the packages a change picks. As a pre-commit hook it reads the index and
checks only the pages the commit touches, so a commit that touches no page is never
stopped by one. To install it, put this in `.git/hooks/pre-commit` and make it executable:

```sh
#!/bin/sh
exec uv run --locked moxi-dev stamp --staged
```

MIT licensed; see `LICENSE`.
