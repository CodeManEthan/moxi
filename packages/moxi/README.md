# Moxi

Moxi is a harness for running many AI coding-agent sessions as one system. Popular agent
CLIs such as Claude Code and Codex add little structure around a session. Moxi adds some,
and it makes opinionated choices to do it: agents have names and identities, sessions
message each other, and a clock tracks when a session's prompt cache goes cold. These
choices won't suit everyone.

Moxi is built as modules that plug together. A module installs with the modules it
requires and nothing more; the rest are optional, and a feature that needs a missing
module is off and quiet.

## Modules

| module | what it does | requires | optional | issues |
|---|---|---|---|---|
| `moxi` | The core: names, paths, one config file per module, module discovery, the module contract check, and the `moxi` command | nothing (standard library only) | | [moxihq/moxi/issues](https://github.com/moxihq/moxi/issues) |
| sessions | Where a harness keeps a session's record, and how its lines read | `moxi` | | [moxihq/sessions/issues](https://github.com/moxihq/sessions/issues) |
| turns | The one interface to a harness's session: a turn, mail, `/clear` and `/exit` in, session events out. A library with no command of its own | `moxi` | `codex`: the Codex host | [moxihq/turns/issues](https://github.com/moxihq/turns/issues) |
| cache | The prompt cache clock: when a session's cache goes cold, with a watcher and a status line segment | `moxi`, sessions | | [moxihq/cache/issues](https://github.com/moxihq/cache/issues) |
| comms | Messages between agent sessions on one machine, delivered into the recipient's session at the end of its current turn | `moxi`, turns | `cache`: hold a send while the recipient's cache is cold; `codex`: Codex sessions | [moxihq/comms/issues](https://github.com/moxihq/comms/issues) |

## The `moxi` command

Every module runs as `moxi <module> <verb>`, for example `moxi comms send`. The core's own
commands:

- `moxi modules` lists the modules this environment holds, their state and their extras.
- `moxi install <module>` installs a module with the modules it requires, runs its setup,
  and offers each optional extra with a line saying what it enables. `--with`,
  `--without`, `--all` and `--none` answer ahead.
- `moxi shortcut <module>` puts a bare `<module>` command on PATH. It is off by default
  and refused when the name is already taken; `--remove` takes it off.
- `moxi config <module>` shows a module's settings and its config file; `moxi config set
  <module> <key> <value>` changes one, keeping the file's comments, and `moxi config unset`
  removes one. `moxi config moxi` is the core's own.
- `moxi check` checks a module's package against the module contract.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install comms
```

Only the core provides the `moxi` command. `moxi install <module>` adds the module to the
`moxi` tool's environment and runs its setup. A module installed on its own, such as
`uv tool install moxi-comms`, brings no `moxi` and lands where `moxi` can't see it; `moxi`
says so once, with the commands that move it.

uv puts only the core's own scripts on PATH. A module's other scripts stay in the `moxi`
tool's environment, so run one by its path there, for example comms' daemon:

```sh
"$(uv tool dir)/moxi/bin/moxi-comms-daemon"
```

### Before the modules are on PyPI

Install the core from git, then give `moxi install` one `--from` for each Moxi
distribution the module needs that the environment lacks, in dependency order: `moxi`,
`moxi-sessions`, `moxi-turns`, `moxi-cache`, `moxi-comms`. One left out is looked for on
PyPI and the install fails. For comms:

```sh
uv tool install git+https://github.com/moxihq/moxi#subdirectory=packages/moxi
moxi install comms --none \
  --from 'moxi-turns @ git+https://github.com/moxihq/moxi#subdirectory=packages/turns' \
  --from 'moxi-comms @ git+https://github.com/moxihq/moxi#subdirectory=packages/comms'
```

comms with its `cache` extra needs cache and sessions too:

```sh
uv tool install git+https://github.com/moxihq/moxi#subdirectory=packages/moxi
moxi install comms --with cache \
  --from 'moxi-sessions @ git+https://github.com/moxihq/moxi#subdirectory=packages/sessions' \
  --from 'moxi-turns @ git+https://github.com/moxihq/moxi#subdirectory=packages/turns' \
  --from 'moxi-cache @ git+https://github.com/moxihq/moxi#subdirectory=packages/cache' \
  --from 'moxi-comms @ git+https://github.com/moxihq/moxi#subdirectory=packages/comms'
```

## Troubleshooting

**`moxi: command not found` after installing a module.** Only the core provides the
`moxi` command; a module installed on its own doesn't. Install the core, then add the
module to it:

```sh
uv tool install moxi
moxi install <module>
```

**A module's own command is not found.** `moxi install` adds a module to the `moxi` tool,
and uv puts none of a module's scripts on PATH. Run the script by its path:
`"$(uv tool dir)/moxi/bin/<script>"`.

**`moxi` says a module is installed outside this moxi.** That copy is in another
environment, where this `moxi` can't see it. Run the two commands the line names: `moxi
install <module>` puts the module in this `moxi`'s environment, and the other removes the
outside copy.

**A shortcut fails with `moxi: not found`.** Its environment was removed. Delete the
shortcut file, or reinstall.

## License

MIT. See [LICENSE](LICENSE).
