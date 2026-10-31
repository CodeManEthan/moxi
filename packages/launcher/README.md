# moxi-launcher

Moxi's launcher starts Claude Code and Codex sessions. A launch is one call: it takes at
most one preset, lays the flags you type over it, checks the result against a catalog of
each harness's flags, composes the command, writes a row to the launch log, and starts the
session in this terminal, headless, or (with slots) in a tmux slot. The CLI calls the same
library a script can import, in its own process; there is no service and no socket.

    pip install moxi-launcher
    moxi install launcher

The launcher needs only `moxi`. Everything else is an optional link (see below). You
need Claude Code or Codex installed and signed in, with their own accounts; the launcher
has no account and no cloud service of its own.

## A launch

    moxi launcher run --model opus --brief "Read the design and start the build."
    moxi launcher run --seat planner
    moxi launcher run --project-preset docs --target headless --brief-file task.md
    moxi launcher run --harness codex --scratch -- --some-new-codex-flag

Each value of a launch comes from, each later one winning: the catalog's shipped default;
your `[defaults]`; the one preset you name; the flags you type. A launch names at most one
preset, a seat (`--seat NAME`) or a project preset (`--project-preset NAME`), and never
draws values from a second.

The flags are the catalog's rows: the launcher's own (`--harness`, `--cwd`, `--project`,
`--scratch`, `--brief`, `--brief-file`, `--handoff`, `--target`, `--slot`,
`--instructions`, `--identity` and the rest) and the harness's (`--model`,
`--permission-mode`, `--sandbox`, `--add-dir` and so on). `moxi launcher catalog` lists
every row this install offers, and marks the rows the launcher sets itself and the rows
whose module is not installed. A flag the catalog does not know is passed on to the
harness, last on its argv, after one warning line; the words after `--` are passed on as
given. A passed-on flag that would set a row the launcher owns (the session id, a resume)
or a permission row is refused, so the permission check always sees it. So is one that
sets what a permission row sets another way: Claude's `--dangerously-skip-permissions`
and a `--settings` JSON string with a `permissions` mode, allow or deny list; Codex's
`--dangerously-bypass-approvals-and-sandbox` and `--approve-for-me` (and their hidden
aliases `--yolo` and `--not-so-yolo`), and a config override (passed on, or a `--config`
item) keyed `sandbox_mode` or `approval_policy`. A settings file, or a Codex profile, is
not read: what it sets is up to you.

**Permission rows.** A row that sets how much the session may do without asking (Claude's
permission mode and tool lists, Codex's sandbox and approval) is a posture row. A launch
that sets one to something other than your default (your `[defaults]` value, else the
shipped one) is refused unless you add `--off-posture`. Put the posture you work with in
`[defaults]`.

**Where it runs.** `--target terminal` (the default) becomes the harness in this terminal:
on Linux and macOS the launcher execs it, on Windows it starts the harness and waits for it.
`--target headless` starts the harness's non-interactive mode (`claude -p`, `codex exec`)
detached, with the brief as its prompt, its output going to the launch's run log; a
headless launch needs a brief. `--target slot` starts the session in a tmux slot, and needs
the slots module (Linux, macOS and WSL2).

**The brief.** Without turns, the brief (or the handoff) is the harness's opening prompt.
With turns, a launch in this terminal or in a slot waits until the session is ready and
sends the brief as its first turn, once. If turns cannot say whether it went in, the
launch log says the brief is unconfirmed; it is never sent twice.

`moxi launcher preview` takes the same words as `run` and prints the command and the
environment variables the launch would set, starting nothing.

## Presets

Presets live in `presets.toml` in the launcher's config directory. Moxi ships none.

```toml
[defaults]                     # your defaults for every launch
permission_mode = "acceptEdits"

[seat.planner]                 # a seat: a saved setup for a standing agent
harness = "claude"
model = "opus"
instructions_file = "~/agents/planner.md"
home = "~/work/site"           # a path, or a project key with projects installed

[project.docs]                 # a project preset: where to work
cwd = "~/work/docs"
harness = "codex"
sandbox = "workspace-write"
```

Every key is a catalog row of the preset's harness or one of the launcher's rows. A seat's
standing text is `instructions` or `instructions_file`, never both, and a preset names at
most one place (`cwd`, `project`, `scratch`, or a seat's `home`). `moxi launcher presets
check` checks the whole file: types and values, rows whose module is missing, project keys,
that an instructions file can be read, and that an agent role a preset names is found in
the harness's agent folders.

## The launch log

Every launch writes one row to a SQLite file in the launcher's state directory before the
session starts: who launched what, where, with which argv and stamped environment, and how
far it got (`spawning`, `started`, `ready`, `sending`, `first-sent`, or `failed` with a
reason). A launch refused before its row is written writes nothing. Each session the
launcher starts carries `MOXI_LAUNCHER_REQUEST`, its launch's request id, and a Claude
launch's session id is set at launch, so the session can always be found from its row.
A launch made from inside a session drops that session's stamps from the environment it
passes on: every `CLAUDE*` variable except your own Claude Code configuration and
sign-in (`CLAUDE_CONFIG_DIR`, the provider switches such as `CLAUDE_CODE_USE_BEDROCK`,
`CLAUDE_CODE_OAUTH_TOKEN` and the others in the launcher's keep-list), and each
module's own session stamps. A scratch launch works in a folder of its own under
`moxi-scratch-<uid>` in the system's temporary folder, which only you may use.

## Handoffs

A handoff is a short file a session writes for the next session of its seat. Its key is
the session's comms name without its scope, else the seat's name, else the project key; it
is filed as `<YYYY-MM-DD>-<key>.md` (then `-2`, `-3`) in the handoff folder. With turns,
a seat's next start is pointed to its newest waiting handoff. A handoff counts as used only
when the launcher gave it to a launch of its seat or showed it to one, so a session started
by hand never hides a seat's handoff. The launcher ships a `handoff` skill, which
`moxi install launcher` offers to place for Claude Code and Codex.

## Commands

| Command | Does |
|---|---|
| `moxi launcher run [--seat N \| --project-preset N] [flags] [-- extra]` | launch a session |
| `moxi launcher preview ... [--json]` | print the command and environment a launch would use |
| `moxi launcher log [--limit N] [--before TIME] [--json]` | the launch log, newest first; `--json` prints one row per line |
| `moxi launcher presets [list \| show NAME \| check] [--json]` | list, show or check the presets |
| `moxi launcher presets --project KEY [--json]` | a project's agents, each beside the seat whose identity is its name (needs projects) |
| `moxi launcher catalog [HARNESS] [--json]` | the flag rows this install offers |
| `moxi launcher handoff dir [--json]` | this session's handoff folder |
| `moxi launcher handoffs [--key KEY] [--json]` | a key's handoffs, newest first, and which still wait |
| `moxi launcher deliver REQUEST_ID --pid PID` | internal: the detached process that sends a launch's first turn |

Exit codes: 0 done; 1 a start that failed; 2 bad arguments or a bad value; 3 refused (with
nothing written when it was refused before the launch's row, else the row says why); 75 a
lock held past its wait (the launch log's, slots' or projects'), or comms' daemon not
answering, when trying again may work.
A refusal prints one line ending in a short code, such as `[off-posture]`, and a hint.

## Optional links

| Module | With it | Without it |
|---|---|---|
| turns | the first turn delivered once the session is ready; Codex session ids learned; a Codex seat's instructions in its start context; the handoff pointer at a seat's start | the brief is the opening prompt; no readiness, no pointer; a Codex seat gets only its `AGENTS.md`; Codex session ids unknown |
| comms | identities, scopes, directors and the `with:` start posture | plain harness launches; `with:` and `director` refused |
| slots | `--target slot`, `--slot`, and answering the startup screens you turn on | `terminal` and `headless` only |
| projects | `project` keys, a project's agent listing, a directory's project key | `cwd` paths and scratch launches only |
| notes | the `notes_dir` row, and notes' handoff folder | the launcher's own handoff folder |
| usage | the `warn_at` row | the row is not offered |

An agent role a preset names is looked for in the harness's own agent folders, the user's
and the project's.

With comms installed, comms keeps the name `console` for the launcher's own notices, so
no session can take it.

A launch with an identity runs under comms' wrapper, `moxi comms claude` or `moxi comms
codex` (which needs turns' Codex host), in this terminal or a slot; a headless one is
refused for now. Before anything is written the launcher asks comms about the name: one
held by another session, reserved for a module or retired is refused (3), naming who or
why. When comms can't reach its daemon, or gets an answer from it that it can't read, the
launch is a try-again (75), since the wrapper would stop before the harness starts. A
Codex launch with an identity takes no extra flags, since comms' Codex wrapper takes only
its own. When the wrapper ends before the harness starts (comms' daemon stopped after the
check, say), its words show where it ran and the row ends `failed` with `comms-offline`
(75), `identity-taken` (3) or `comms-failed` (1).

## Settings

Set with `moxi config set launcher <key> <value>`:

- `ready.wait_s` (60): how long the first-turn sender waits for the session to be ready.
- `first.wait_s` (120): how long it then waits for the first turn to start.
- `startup.trust_dialog` and `startup.dev_channels_dialog` (both off): whether a slot
  launch answers Claude's folder-trust and development-channels screens. The launcher
  presses a key only on a screen it has just seen.
- `startup.wait_s` (20): how long a slot launch watches for those screens.
- `runs.keep_days` (30): how long run logs and prompt files are kept.
