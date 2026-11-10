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
| `packages/moxi` | `moxi` | The core: names, paths, config, module discovery and the `moxi` command |
| `packages/cache` | `moxi-cache` | The prompt cache clock: when a session's cache goes cold, with a watcher and a status line segment |
| `packages/comms` | `moxi-comms` | Messages between agent sessions, delivered into their turns |
| `packages/connector` | `moxi-connector` | An MCP server for the Claude apps: its sign-in, its address, and the tools other modules provide |
| `packages/launcher` | `moxi-launcher` | Launch Claude Code and Codex sessions from presets: in this terminal, headless or in a tmux slot |
| `packages/loadout` | `moxi-loadout` | Your own skills, agents and settings, placed for Claude Code, Codex and OpenCode |
| `packages/notes` | `moxi-notes` | Where each session keeps its notes, snapshots of the notes repository, and the secret checks that guard it |
| `packages/projects` | `moxi-projects` | A list of named places: projects, their roots, kinds and agents |
| `packages/repo` | `moxi-repo` | Git hooks run as declared pieces, and one shared committer |
| `packages/rounds` | `moxi-rounds` | The record of a piece of work: its design and the verdicts on it, its questions, the operator's rulings and each project's standing rulings |
| `packages/secrets` | `moxi-secrets` | Named pattern sets for credentials and personal data, and one way to scan text for them |
| `packages/services` | `moxi-services` | One list of every daemon and timer on the machine, to start, stop, enable, disable and watch |
| `packages/sessions` | `moxi-sessions` | Reads a harness's own session records |
| `packages/slots` | `moxi-slots` | Numbered tmux sessions to launch into, on one supervised tmux server |
| `packages/tunnel` | `moxi-tunnel` | An address off the machine for a loopback service, through Tailscale or a Cloudflare tunnel |
| `packages/turns` | `moxi-turns` | One interface to a harness session's turns |
| `packages/ui` | `moxi-ui` | The browser shell: one local web address for every module's page |
| `packages/usage` | `moxi-usage` | How much of each plan window is used, how full a session's context is, and what the tokens cost |
| `packages/moxi-dev` | `moxi-dev` | The contributor tools and the module contract check; not a module, and nothing depends on it at run time |

Each package's README says how to install and use it. `packages/moxi/README.md` is the
place to start. In short, install the core, then each module you want by its name:

```sh
uv tool install moxi
moxi install comms
```

## Working on Moxi

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the comment and naming rules.

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/). From a clone:

```sh
uv sync
uv run moxi-dev check
```

`uv sync` sets up one environment at the root with every package installed from its
directory, plus the contributor tools. `moxi-dev check` runs the checks a change must
pass before it merges. A package's own tests also run from its directory with
`uv run pytest`.

A module's web page is TypeScript under `packages/<name>/web/`, built with Node 22 and
committed as built files, so installing or testing Moxi never needs Node. To change a
page, build it from the root and commit what the build writes:

```sh
npm ci
npm run build
```

`npm run check` type-checks every page and compares a fresh build with the committed
files, as CI does on Linux, macOS and Windows.

## License

MIT; see `LICENSE`. Each package carries the same license.
