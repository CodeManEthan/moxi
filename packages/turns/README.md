# turns

turns is the Moxi module that drives a coding agent's session. Other modules call it to
put input into a session (a new turn, a delivery held until the running turn ends, a
steer into the running turn, `/clear`, `/compact` and `/exit`, optionally stopping the
session's background shells) and to take session events out: the session ready, a turn
started, steered or ended, naming each delivery it took by the caller's `ref`, and the
session gone. A caller can also read the session's subagents, teammates and Codex
background terminals; the list says `complete` false when turns could not read it whole. A holder can be asked at each
Stop before the turn ends, and a registered module can add context at a session's start
or at a prompt. Each session turns can reach has an endpoint with a Unix socket and a
ledger of the session's turns (a small process of its own for a Claude Code session, and
part of the host turns runs for a Codex or OpenCode session), so whether a session is idle
and whether a delivered turn ran are read from the harness's own turn events. When a
socket's path under the runtime directory is too long for a Unix socket (a long
`MOXI_HOME`, say), turns listens at a short hashed path instead. Claude Code sessions are
reached through two Claude Code plugins that ship inside the package; Codex sessions run
under a host turns launches, in its caller or as a process of its own (the internal entry
`moxi-turns-codex-host`, installed with the `codex` extra); OpenCode sessions run under an
experimental host. turns is
a library with no command of its own. Its API is the `moxi_turns` package, and the wire
under it is described in [contracts/socket-protocol.md](contracts/socket-protocol.md).

## Install

```sh
uv tool install moxi
moxi install turns
```

`moxi install turns` installs turns with `moxi`, runs its setup, and offers the `codex`
extra. The setup writes the Claude plugin's `endpoint.json` and installs both Claude Code
plugins at user scope with `claude plugin`, so it needs `claude` on PATH for that step.
Only a user-scope install reads `endpoint.json`. A launcher can instead load the plugins
for one launch with `--plugin-dir`: the argv `moxi_turns.launch_additions("claude", ...)`
returns carries both, and its environment names the endpoint command in
`MOXI_TURNS_ENDPOINT`. Such a launch writes nothing into turns' package, so it works from
a read-only install and many can start at once.

### Before turns is on PyPI

Install the core from git, then name turns' repo with `--from`:

```sh
uv tool install git+https://github.com/CodeManEthan/moxi#subdirectory=packages/moxi
moxi install turns --from 'moxi-turns @ git+https://github.com/CodeManEthan/moxi#subdirectory=packages/turns'
```

## Requirements

- Linux and Python 3.12 or later.
- `moxi` (0.1).
- The `codex` extra adds `websockets`, which the Codex host needs; it also needs `codex`
  on PATH. The host's entry is installed either way; `turns.codex_host_available()` says
  whether the extra is there.
- Claude Code sessions need Claude Code with the two plugins loaded; OpenCode sessions
  need `opencode` on PATH or in `~/.opencode/bin`.

## License

MIT. See [LICENSE](LICENSE).
