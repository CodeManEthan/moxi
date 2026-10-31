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
the slots module (Linux, macOS and WSL2). `--slot` names the slot; a launch with any other
target that names one is refused, exit 2.

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

## Acting on launched sessions

The launcher can act on a session it launched: relaunch it, exit it, resume it, recycle it
and keep it warm. Every act finds its session the same way, through the launch log and
the turns endpoint that claimed the launch, so it acts only on a session the launcher
started, and only on the one it started. turns does every act inside the session (the
exit, a clear, a compact, a delivered turn); slots does what lies outside it (the pane,
Claude Code's exit dialog, a job that outlives the session). Each act waits for the
session to be quiet first: no turn open, no subagent at work, no draft in the prompt,
and nobody using its pane or opening a turn in the last 2 minutes (`relaunch.person_s`).
A background shell never holds an act.

A launch has one act open at a time. A second one is refused (`run-open`, exit 3), naming
the open run. Each act is a run in the launch log, with its events and its outcome.

**Relaunch** puts a fresh session in the same slot. It needs turns and slots, and a
Claude Code launch in a slot; comms is needed only for a director's relaunch.

    moxi launcher relaunch --self --handoff FILE

An agent runs this in its own turn, with a handoff it has just written. The launcher
checks the handoff (a readable file of at most 64 KiB, written since this session
started) and that no subagent is running, starts a job outside the session and prints
what it waits for; the agent then ends its turn. Once the session is quiet the job exits
it, answers the exit dialog, and starts the new session in the same pane with the same
launch values, none of the old session's environment, and the handoff as its first turn.
A relaunch that can't go ahead (the session never goes quiet within
`relaunch.wait_s`, the slot can't be held) leaves the session alone and tells it so in
one turn.

    moxi launcher relaunch SESSION_ID
    moxi launcher relaunch IDENTITY

A director relaunches a session it directs, named by its session id or its comms name.
The caller's comms identity must be one of the directors the session was launched with;
a session whose cache is still warm is refused (`warm`: send it a message instead), and a
session that isn't quiet now is held (exit 75). The new session starts with the `with:`
posture of its director and no first turn. The call follows the run for
`relaunch.follow_s` and prints how it ended; past that it exits 75 and names the run.

    moxi launcher relaunch --status RUN
    moxi launcher relaunch --from-record RUN

`--status` shows a run and its events, what it waits on first. A relaunch that exited the
session and failed to start the new one tells the session's directors (or the comms
identity in `relaunch.fallback`) through comms, naming the command that repairs it:
`--from-record` starts the session again from the run's record, in its slot, or the
lowest free one when someone outside the session's own line of launches has taken it. It
refuses when the slot already runs the relaunched session.

**Exit-session** exits one launched session, once it is quiet: `moxi launcher
exit-session SESSION_ID`. Anyone with a shell may run it; the consent is the launch's own,
the commands it granted turns. In a slot the exit dialog is answered too; elsewhere the
session is left at the dialog for a person (`at-dialog`). Every exit tried writes a row to
the exit log, `moxi launcher exits`: `exited`, `held` (with what held it), `at-dialog`,
`not-exited` or `refused`.

**Resume.** `moxi launcher sessions` lists past launches, newest first, each with whether
it is live and whether it can be resumed, and why not: it is live (attach to its slot
instead), it ran headless, it never learned a session id (a Codex launch made without
turns' host: use `codex resume`), it ran Codex under a comms name (comms' Codex wrapper
starts a new thread each time), its harness is no longer launched, or a later launch
resumed it since. `moxi launcher resume SESSION_ID` launches it again from its launch's
values, resumed, keeping its session id: in its own slot when that can take it, else
the lowest that can (`--slot N` picks one), or in this terminal for a terminal launch.
It sends no first turn.

**Recycle.** Two launch options let a session be cleared or compacted on the launcher's
own reads:

- `--recycle-on-expiry clear|compact` (needs turns and cache): once the session's prompt
  cache has gone cold and the session is quiet, clear or compact it. A clear waits while
  a named hold is open (below). A session idle for an hour has its background shells
  stopped by the clear.
- `--recycle-at-limit relaunch|compact` with `--limit-at 1x|1.5x|2x` (needs turns and
  usage): once the context passes the mark, tell the agent to relaunch itself with a
  handoff (`relaunch` also needs slots and a slot launch), or compact it once it is idle,
  waiting up to `recycle.limit_wait_s`.

**Keep-warm.** `--keep-warm on` (needs turns and cache) nudges a session shortly before
its prompt cache would expire while it waits on work it handed out: running subagents, or
open named holds. The nudge is a short turn delivered only when the session is idle. A
run of nudges stops at its cap (`keep_warm.subagent_cap`, `keep_warm.director_cap`) and
shows `blocked`. A session that hands out work other than subagents names each piece
with a hold, from its own tool shell:

    moxi launcher keep-warm hold NAME
    moxi launcher keep-warm release NAME
    moxi launcher keep-warm holds

A hold stays open until it is released, across a relaunch or a resume of the session.
Keep-warm and recycle on expiry are offered where cache can read a session's
environment: Linux, macOS and WSL2, for Claude Code sessions.

**State.** `moxi launcher state [SESSION_ID]` shows a launched session's relaunch,
recycle and keep-warm state (this session's when none is named). It reads red, with
`failed` or `blocked` and why, when the latest run on the launch failed or was blocked
and nothing has gone well since; a session merely waiting is never red.

## Commands

| Command | Does |
|---|---|
| `moxi launcher run [--seat N \| --project-preset N] [flags] [-- extra]` | launch a session |
| `moxi launcher preview ... [--json]` | print the command and environment a launch would use |
| `moxi launcher log [--limit N] [--before TIME] [--json]` | the launch log, newest first; `--json` prints one row per line |
| `moxi launcher log --table runs\|exits\|holds [...]` | the runs (with their events), the exit log, or every hold, newest first |
| `moxi launcher presets [list \| show NAME \| check] [--json]` | list, show or check the presets |
| `moxi launcher presets --project KEY [--json]` | a project's agents, each beside the seat whose identity is its name (needs projects) |
| `moxi launcher catalog [HARNESS] [--json]` | the flag rows this install offers |
| `moxi launcher handoff dir [--json]` | this session's handoff folder |
| `moxi launcher handoffs [--key KEY] [--json]` | a key's handoffs, newest first, and which still wait |
| `moxi launcher deliver REQUEST_ID --pid PID` | internal: the detached process that sends a launch's first turn |
| `moxi launcher relaunch --self --handoff FILE [--dry-run] [--json]` | arm a relaunch of this session, with its handoff |
| `moxi launcher relaunch SESSION_ID \| IDENTITY [--dry-run] [--json]` | relaunch a session you direct |
| `moxi launcher relaunch --status RUN [--json]` | a run and its events |
| `moxi launcher relaunch --from-record RUN [--json]` | start again the session a failed relaunch exited |
| `moxi launcher exit-session SESSION_ID [--dry-run] [--json]` | exit a launched session |
| `moxi launcher exits [--limit N] [--before TIME] [--json]` | the exit log, newest first |
| `moxi launcher sessions [--limit N] [--before TIME] [--json]` | past launches, and which can be resumed |
| `moxi launcher resume SESSION_ID [--slot N]` | launch a past session again, resumed |
| `moxi launcher keep-warm hold NAME`, `release NAME`, `holds [--json]` | this session's named holds |
| `moxi launcher state [SESSION_ID] [--json]` | a launched session's state, and whether it needs a look |

`--dry-run` takes an act's checks and writes nothing. `--json` prints the verb's data, its
outcome word among it.

Exit codes: 0 done (a relaunch armed or relaunched, a session exited); 1 a start that
failed, or an act that ended in a failed outcome (`at-dialog` among them); 2 bad
arguments or a bad value; 3 refused (with nothing written when it was refused before the
launch's row, else the row says why); 75 a lock held past its wait (the launch log's,
slots' or projects'), comms' daemon not answering, an act held because the session is not
quiet now, or a run still going when the call stopped following it: trying again may
work.
A refusal prints one line ending in a short code, such as `[off-posture]`, and a hint.

## Optional links

| Module | With it | Without it |
|---|---|---|
| turns | the first turn delivered once the session is ready; Codex session ids learned; a Codex seat's instructions in its start context; the handoff pointer at a seat's start; every act on a launched session | the brief is the opening prompt; no readiness, no pointer; a Codex seat gets only its `AGENTS.md`; Codex session ids unknown; no relaunch, exit-session, recycle or keep-warm |
| comms | identities, scopes, directors and the `with:` start posture; a director's relaunch, and notices of a failed relaunch | plain harness launches; `with:` and `director` refused; relaunch with `--self` only, its failures recorded only |
| slots | `--target slot`, `--slot`, and answering the startup screens you turn on; relaunch, and answering the exit dialog | `terminal` and `headless` only; no relaunch, and an exit stops at the exit dialog |
| cache | the `keep_warm` and `recycle_on_expiry` rows, and a director's relaunch refused while the session's cache is warm | neither row is offered |
| projects | `project` keys, a project's agent listing, a directory's project key | `cwd` paths and scratch launches only |
| notes | the `notes_dir` row, and notes' handoff folder | the launcher's own handoff folder |
| usage | the `warn_at`, `recycle_at_limit` and `limit_at` rows | the rows are not offered |

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
- `relaunch.wait_s` (900): how long a self relaunch waits for its session to be quiet.
- `relaunch.person_s` (120): how recently a person's use of the pane, or a turn no
  delivery opened, holds an act.
- `relaunch.dialog_s` (60): how long a relaunch or an exit waits at the exit dialog.
- `relaunch.name_wait_s` (15): how long a relaunch waits for the old session's comms name
  to be let go.
- `relaunch.follow_s` (300): how long a director's relaunch, or a repair, follows its run.
- `relaunch.fallback` (empty): a comms identity told of a failed relaunch when the launch
  has no director.
- `relaunch.pass_env` (empty): variable names a relaunch or a resume carries from the
  environment of the process running it into the new session, such as an API key your
  harness reads.
- `relaunch.notice_retry_s` (30): how long a refused notice is tried again.
- `recycle.limit_wait_s` (1800): how long a compact at the limit waits for the session to
  be idle.
- `keep_warm.subagent_cap` (3) and `keep_warm.director_cap` (10): how many nudges in a row
  each case gets; `keep_warm.subagent_texts` and `keep_warm.director_texts` are their
  words.
- `records.keep_days` (90): how long ended runs, their events, exits and released holds
  are kept.
