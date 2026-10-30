# moxi-sessions

Moxi's sessions module reads the records Claude Code and Codex keep of their own
sessions. It finds a session's file: a Claude Code transcript under the projects root
(`${CLAUDE_CONFIG_DIR:-~/.claude}/projects`), or every rollout file of a Codex thread
under `${CODEX_HOME:-~/.codex}/sessions`. It decodes a transcript's lines, yielding a line
that will not decode as a marked entry rather than dropping it, and reads a record
backwards from the end in blocks up to a byte limit, saying how the read ended. It only
reads: it writes nothing to a record and leaves what a line or a miss means to the
caller. Other Moxi modules import it through `moxi_sessions.api` (cache reads its
transcripts this way), and it adds two commands:

- `moxi sessions find SESSION [--harness claude|codex] [--root PATH] [--json]` prints
  where a session's record is, or every root it looked in and why each held none, and
  exits 1 then.
- `moxi sessions root [--json]` prints the Claude Code projects root read for a session
  that stated none, and where that root came from.

Its one setting, `claude.projects`, names that root when it is not the usual one;
`moxi config sessions` shows it.

## Install

Linux, Python 3.11 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install sessions
```

Before the modules are on PyPI, install the core from git and give `moxi install` the
module's git source:

```sh
uv tool install git+https://github.com/moxihq/moxi
moxi install sessions --from 'moxi-sessions @ git+https://github.com/moxihq/sessions'
```

The front door's README, at [moxihq/moxi](https://github.com/moxihq/moxi), covers the
`moxi` command and the other modules.

## Dependencies

`moxi`, Moxi's core, and nothing else. sessions has no optional extras.

## License

MIT. See [LICENSE](LICENSE).
