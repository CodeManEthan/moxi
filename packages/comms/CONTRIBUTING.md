# Contributing

Thanks for looking. Issues are welcome at https://github.com/CodeManEthan/moxi/issues,
and pull requests at https://github.com/CodeManEthan/moxi, the repository that holds
every Moxi package; comms is `packages/comms` there.

## Setting up

You need Linux, Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/CodeManEthan/moxi
cd moxi
uv sync
cd packages/comms
uv run pytest
```

Bare `pytest` won't find the package; use `uv run`.

## The tests

The suite runs every test in a temporary home, against fakes: no real daemon, no real
Claude Code, no network. Moxi's test kit fences each test in (`conftest.py` at the
package root loads `moxi_dev.testing`): an emptied environment with every home in the
test's tmp, and a write outside tmp or a connect off the machine fails the test. A plain
run leaves out the slow, browser, build and live tiers; name one with `-m` to run it
(a `live` test also needs `MOXI_TEST_LIVE=1` in the environment, or it is skipped).
`moxi-dev check --full`, from the repository root, runs every tier but live.

```sh
uv run pytest
uv run pytest -m build tests/test_clean_home.py
```

- **Settings.** A test that needs settings writes its own config file with
  `write_config(env, text)` (`tests/conftest.py`), under the test's temporary home.
- **Both backends.** The suites listed in `BOTH_BACKENDS` in `tests/conftest.py` run
  each test against the in-memory mock and against a real local `record.db`.
- **The clean-home test** builds comms' wheel and the core's (`moxi`, from its directory
  beside this one), installs both into a venv under a temporary `HOME`, follows the README's install steps, and checks that the daemon provisions
  itself on its first start, that two OpenCode agents (on a stub `opencode`) can talk,
  that everything stops cleanly and that nothing is written outside that home. It needs
  `uv`, and it is a `build` test, so it runs only when the `build` tier is asked for
  (`-m build`, or `moxi-dev check --full`).

Write the failing test first, then the change.

Some scripts run real processes against a throwaway home and a fake harness:

```sh
uv run python scripts/smoke.py
uv run python scripts/codex-smoke.py --dry-run
```

## The name

comms is a module of Moxi. The harness and the owner are spelled by the core
(`moxi.names`); `moxi_comms.release` builds the distribution and the repository
from them and the module's name, and `tests/test_release.py` holds `pyproject.toml` to
them. The import package is `moxi_comms`, the commands run as `moxi comms <verb>`, and
the internal entry points are `moxi-comms-<part>`.

## Code

- The daemon, the CLI and the Claude path use the standard library and the core
  (`moxi`) only. The one third-party dependency is `websockets`, used by the Codex adapter (the `codex` extra).
- Comments and docstrings say what the code does and why. They don't cite issue
  threads, meetings or dates; that history belongs in the commit message or the pull
  request.
- Logs carry message ids, never message bodies or credentials.
- Anything an agent is shown carries no slot numbers.

## Commit messages

- The subject says what changed in the code, in plain words: "Default the backend to
  local", not a ticket number or a quote.
- The body says why, if the subject doesn't.
- Leave out quoted conversations, message ids, session links, people's names other than
  co-author trailers, and dates of discussions.

## Reporting a security problem

Report it privately, as [SECURITY.md](https://github.com/CodeManEthan/moxi/blob/main/SECURITY.md) says, rather than opening a public issue.
See [docs/security.md](docs/security.md) for what comms does and doesn't protect against.
