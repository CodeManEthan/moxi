# moxi-sessions

Moxi's sessions module reads the records Claude Code, Codex and OpenCode keep of their
own sessions, and turns each into one set of record kinds with one set of field names. It
only reads: it writes nothing to a record, keeps no state of its own, and leaves what a
record or a miss means to the caller.

It reads from three roots, one per harness:

- Claude Code's projects root, `${CLAUDE_CONFIG_DIR:-~/.claude}/projects`, where each
  session is a transcript and each subagent a file beside it;
- Codex's sessions root, `${CODEX_HOME:-~/.codex}/sessions`, where each thread is one or
  more rollout files (a compressed `.jsonl.zst` one is found and marked, but not read);
- OpenCode's database, `${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db`, which it
  opens read only and reads only the session, message and part tables of.

It finds a session's records, asking each root in turn and saying for each whether it held
the session, held none, could not be read, or was not asked because the id is not one; only
a root that was read and held none counts as a miss. It lists every source under a root with
the keys a caller needs to tell what changed, and lists a session's subagents: Claude Code's
subagent files, OpenCode's child sessions. It cannot yet find a Codex thread's subagents,
and says so rather than reporting none.

Other Moxi modules use it through `moxi_sessions.api` (cache reads its transcripts this
way), and it adds two commands:

- `moxi sessions find SESSION [--harness claude|codex|opencode] [--root PATH] [--json]`
  prints where a session's records are, or every root it looked in and why each held none,
  and exits 1 then. `--root` adds a root to look in after the usual one (for OpenCode, a
  database).
- `moxi sessions root [--json]` prints the root each harness is read from and where that
  root came from: a config key, an environment variable, or the harness's default.

Three settings name a root when it is not the usual one: `claude.projects`,
`codex.sessions` and `opencode.database`. `moxi config sessions` shows them.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install sessions
```

Before the modules are on PyPI, install the core from git and give `moxi install` the
module's git source:

```sh
uv tool install git+https://github.com/moxihq/moxi#subdirectory=packages/moxi
moxi install sessions --from 'moxi-sessions @ git+https://github.com/moxihq/moxi#subdirectory=packages/sessions'
```

The front door's README, at [moxihq/moxi](https://github.com/moxihq/moxi), covers the
`moxi` command and the other modules.

## Dependencies

`moxi`, Moxi's core, and nothing else. sessions has no optional extras.

## License

MIT. See [LICENSE](LICENSE).
