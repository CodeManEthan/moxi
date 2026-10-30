# Moxi

Moxi is a harness for running many AI coding-agent sessions as one system. Agent CLIs
such as Claude Code, Codex and OpenCode add little structure around a session; Moxi adds
some. Agents have names and identities, sessions message each other, and a clock tracks
when a session's prompt cache goes cold.

Moxi is built as modules that plug together. Each one is its own package with its own
dependencies, and a module installs with the modules it requires and nothing more. This
repository holds all of them.

## Packages

| directory | PyPI name | what it is |
|---|---|---|
| `packages/moxi` | `moxi` | The core: names, paths, config, module discovery, the module contract and the `moxi` command |
| `packages/sessions` | `moxi-sessions` | Reads a harness's own session records |
| `packages/turns` | `moxi-turns` | One interface to a harness session's turns |
| `packages/cache` | `moxi-cache` | The prompt cache clock, its watcher and timer |
| `packages/comms` | `moxi-comms` | Messages between agent sessions, delivered into their turns |
| `packages/moxi-dev` | `moxi-dev` | The contributor tools; not a module, and nothing depends on it at run time |

Each package's README says how to install and use it. `packages/moxi/README.md` is the
place to start.

## Working on Moxi

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/). From a clone:

```sh
uv sync
uv run moxi-dev check
```

`uv sync` sets up one environment at the root with every package installed from its
directory, plus the contributor tools. `moxi-dev check` runs the checks a change must
pass before it merges. A package's own tests also run from its directory with
`uv run pytest`.

## License

MIT; see `LICENSE`. Each package carries the same license.
