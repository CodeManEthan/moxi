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

This package is the core: names, paths, one config file per module, module discovery,
and the `moxi` command. Every other module is its own package, installed with
`moxi install <module>`. The repository's README lists every package with its PyPI name
and what it does:
[github.com/CodeManEthan/moxi](https://github.com/CodeManEthan/moxi#packages).
`moxi modules --available` prints the same list of modules from the command line.

Every module's code is a folder under `packages/` in one repository,
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), and its issues go to that
repository's tracker: [CodeManEthan/moxi/issues](https://github.com/CodeManEthan/moxi/issues).

## What it needs

- Linux. The core also has macOS and Windows code (launchd and Task Scheduler for
  daemons, the Windows folders and consoles), and the repository's CI runs its tests on
  both, but those runs gate nothing and nobody runs Moxi there yet: treat macOS and
  Windows as untested.
- Python 3.12 or later, and [uv](https://docs.astral.sh/uv/), which `moxi install` uses
  to add modules.
- One third-party package, psutil. The core also carries copies of two released
  libraries, tomlkit 0.15.2 and packaging 26.3, so it doesn't depend on them.

## The `moxi` command

Every module runs as `moxi <module> <verb>`, for example `moxi comms send`. The core's own
commands:

- `moxi modules` lists the modules this environment holds, their state and their extras.
  `moxi modules --json` prints the same as one JSON object. In it each module's `starts`
  names, for each daemon that listens on a socket, what starts it now: `login`, or
  `connection` for the first connection to its socket, or `null` when the setting that
  chooses can't be read. It is what systemd and launchd do; on Windows every daemon starts
  at each logon. Python code gets the same value from `moxi.units.starts(found)`.
  `moxi modules --available` lists the first-party modules, installed or not, and
  `--refresh` first fetches the newest list from the repository: the one command that
  uses the network.
- `moxi install <module>` installs a module with the modules it requires, runs its setup,
  and offers each optional extra with a line saying what it enables. `--with`,
  `--without`, `--all` and `--none` answer ahead.
- `moxi uninstall <module>` runs the module's teardown, then removes its daemons and
  timers, its skills and its package. Its config file is kept, and so are its stores
  unless `--purge` is given (`--yes` answers its questions).
- `moxi shortcut <module>` puts a bare `<module>` command on PATH. It is off by default
  and refused when the name is already taken; `--remove` takes it off.
- `moxi config <module>` shows a module's settings and its config file; `moxi config set
  <module> <key> <value>` changes one, keeping the file's comments, and `moxi config unset`
  removes one. `moxi config moxi` is the core's own.
- `moxi paths <module>` prints where a module keeps its files: its data, state, cache,
  config and runtime directories, its config file and each store it declares.
- `moxi doctor [<module>...]` checks the modules, their settings, daemons, timers and
  stores, and exits 1 when a row fails. `--json` prints the rows as JSON, and `--costs`
  also measures each module's daemons.
- `moxi help [<command>|<module>]` prints the command list, or one command's or module's
  help.
- `moxi version` prints the versions of `moxi` and the modules it sees.

## What it does outside its own folders

The core keeps its own files in the usual places for this system (`moxi paths moxi`
names them). Beyond those, `moxi install <module>` may write in three places on your
behalf, and `moxi uninstall` removes what it wrote:

- Skills and agent files a module ships, placed where each agent reads them: Claude Code's
  `~/.claude/skills` and `~/.claude/agents` (under `$CLAUDE_CONFIG_DIR` when that is
  set), and Codex's `~/.agents/skills` and `~/.codex/agents` (under `$CODEX_HOME` when
  set). `moxi install` asks before it places a module's skills; `--skills` and
  `--no-skills` answer ahead. They are links to the installed package on Linux and macOS
  and copies on Windows (the core setting `skills.form`), and the core settings
  `skills.harnesses` and `harnesses` choose the agents and folders.
- A module's daemons and timers, written as systemd user units in
  `~/.config/systemd/user` (launchd agents on macOS, Task Scheduler tasks on Windows).
  Each file is marked as Moxi's, and a file Moxi didn't write is never touched unless
  `--replace` moves it aside.
- Whatever the module's own setup does, which its README describes.

A shortcut (`moxi shortcut`) is a small script written beside the `moxi` on PATH.

## Install

```sh
uv tool install moxi
moxi install <module>
```

`moxi install comms`, for example, installs comms with turns, which it requires, runs
their setup, and asks about comms' optional extras.

Only the core provides the `moxi` command. `moxi install <module>` adds the module to the
`moxi` tool's environment and runs its setup. A module installed on its own, such as
`uv tool install moxi-comms`, brings no `moxi` and lands where `moxi` can't see it; `moxi`
says so once, with the commands that move it.

uv puts only the core's own scripts on PATH. A module's other scripts stay in the `moxi`
tool's environment, so run one by its path there, for example comms' daemon:

```sh
"$(uv tool dir)/moxi/bin/moxi-comms-daemon"
```

On Windows the script is `Scripts\moxi-comms-daemon.exe` in the same tool directory; in
PowerShell, `& "$(uv tool dir)\moxi\Scripts\moxi-comms-daemon.exe"`.

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
`"$(uv tool dir)/moxi/bin/<script>"`. On Windows the script is `Scripts\<script>.exe` in
the same tool directory; in PowerShell, `& "$(uv tool dir)\moxi\Scripts\<script>.exe"`.

**`moxi` says a module is installed outside this moxi.** That copy is in another
environment, where this `moxi` can't see it. Run the two commands the line names: `moxi
install <module>` puts the module in this `moxi`'s environment, and the other removes the
outside copy.

**A shortcut fails with `moxi: not found`.** Its environment was removed. Delete the
shortcut file, or reinstall.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/moxi/LICENSE).
