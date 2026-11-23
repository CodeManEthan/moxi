# moxi-turns

turns lets other Moxi modules drive a coding agent's session in Claude Code, Codex or
OpenCode: put a new turn, a steer or a session command into it, and hear back when the
session is ready, when a turn starts and ends, and when the session is gone. It also runs
guards, checks that refuse an agent's tool call before it runs.

Other modules call it to put input into a session (a new turn, a delivery held until the
running turn ends, a steer into the running turn, `/clear`, `/compact` and `/exit`,
optionally stopping the session's background shells) and to take session events out: the
session ready, a turn started, steered or ended, naming each delivery it took by the
caller's `ref`, and the session gone. A delivery can name the module that asked and the
names its text speaks for, and its end carries the turn's final text. A caller can also
read the session's subagents, teammates and Codex background terminals; the list says
`complete` false when turns could not read it whole. A holder can be asked at each Stop
before the turn ends, and a registered module can add context at a session's start or at
a prompt. A prompt says whether the person typed it, which delivery it is when turns
handed it in, and whether it opens a turn or joins one already running.

A caller can also fork a Claude Code session's main loop: `fork` starts an agent that
holds the conversation so far and runs a task beside the main loop's turns, and
`fork_continue` gives that agent its next round once it has answered. Each round is a
delivery whose end carries the round's answer; Claude Code tells the main loop of each
answer itself, and turns adds nothing to that. The caller sets how long each round may
run before it reads lost (`round_timeout_s`), 120 s when it sets none. The fork's tool
calls are judged by the guards like any agent's, and get no after-tool ask. A delivery or
fork asked with `keep` has its end kept in turns' state folder until the caller acks its
`ref`: a later wait reads the same end, even after the session's endpoint has ended, and
turns removes none by age. Each kept delivery needs a `ref` of its own: turns refuses a
`keep` under a `ref` whose kept end is not acked yet.

Each session turns can reach has an endpoint with a Unix socket and a ledger of the
session's turns (a small process of its own for a Claude Code session, and part of the
host turns runs for a Codex or OpenCode session), so whether a session is idle and whether
a delivered turn ran are read from the harness's own turn events. When a socket's path
under the runtime directory is too long for a Unix socket (a long `MOXI_HOME`, say), turns
listens at a short hashed path instead. Claude Code sessions are reached through two
Claude Code plugins that ship inside the package; Codex sessions run under a host turns
launches, in its caller or as a process of its own (the internal entry
`moxi-turns-codex-host`, installed with the `codex` extra); OpenCode sessions run under a
host that is experimental: no real run has confirmed it yet.

turns is a library with no command of its own; `moxi doctor turns` checks it. Its API is
the `moxi_turns` package, and the wire under it is described in
[contracts/socket-protocol.md](https://github.com/CodeManEthan/moxi/blob/moxi-turns-v0.2.6/packages/turns/contracts/socket-protocol.md).

## Install

```sh
uv tool install moxi
moxi install turns
```

`moxi install turns` installs turns with `moxi`, runs its setup, and offers the `codex`
extra. The setup copies the Claude plugin's folder into turns' data directory, writes
the plugin's `endpoint.json` into that copy, and installs both Claude Code plugins from
it at user scope with `claude plugin`. It writes nothing into turns' package. When
`claude` is not on PATH the setup skips that step with one line and the install still
succeeds; run `moxi install turns` again once Claude Code is installed. An older user-scope
install made from the package's own folder is moved to the copy. `moxi uninstall turns`
removes the marketplace and the copy.

Only a user-scope install reads `endpoint.json`. A launcher can instead load the plugins
for one launch with `--plugin-dir`: the argv `moxi_turns.launch_additions("claude", ...)`
returns carries both, and its environment names the endpoint command in
`MOXI_TURNS_ENDPOINT`. It names the copy's folders when the copy is current, and the
package's own otherwise. Such a launch writes nothing, so it works from a read-only
install with no setup run, and many can start at once.

## The guards

A guard refuses an agent's tool call before it runs, and the agent reads why. turns ships
three, each off until you turn it on. `moxi install turns` asks about each; afterwards set
its switch, for example `moxi config set turns guards.removal true`:

- **The removal guard** (`turns.removal`, switch `guards.removal`): refuses a Claude Code
  shell command that Claude Code's own dangerous-removal check would stop for your
  approval even in bypass mode, where an unattended agent would sit blocked. Its one
  rule is coarse and reads text: an `rm` or `rmdir` beside a `cd` and a `*`, where Claude
  Code can't tell which directory the `*` means. It skips the body of a quoted heredoc
  (`<<'EOF'`) fed to `cat`, `tee`, `python3 -` or `node -`, so file content can go there.
  Claude Code's other removal stops (your working directory or a directory above it, a
  path it can't read, a shell's `-c` script) can be refused by a guard of your own at the
  ask step, below.
- **The no-nested-agents guard** (`turns.no-nested-agents`, switch
  `guards.no_nested_agents`): refuses a subagent's call to start a subagent of its own
  (Claude Code's `Agent` and `Task`, Codex's `spawn_agent`), and tells it to report to
  its parent instead.
- **Question mode** (`turns.question-mode`, switch `guards.question_mode`): lets Claude
  Code's `AskUserQuestion` run only in a turn where you typed something that mentions a
  question, and never from a subagent. A prompt that asks to go through open questions
  gets a one-line reminder of this. Until the session's endpoint has sent its guards,
  Claude Code's runner can't tell whether question mode is on, so it refuses every
  `AskUserQuestion` call and tells the agent to ask in prose.

Which calls a guard judges on Claude Code: every agent's, a fork's that turns started
among them. Only the runner's own calls skip the guards, and a call counts as the runner's
only when it comes from turns' plugin and carries no agent id. The runner holds a fork's
agent id from the fork's start until the fork's own turn ends for its open round, or the
session ends. A round that runs past its bound reads lost at once, but the fork may still
be running, so its id stays held until that turn ends, and a `fork_continue` for it is
refused meanwhile. While the runner holds the id, that fork's calls get no after-tool ask. A call from an agent id the
runner does not hold is judged and asked after as any agent's.

Other modules ship guards too (sessions' `sessions.transcript-read`, usage's
`usage.ceiling`), and a guard can also be a file of your own. `guards.run` lists the
guards that run beyond the switches: guard ids or paths to guard files. A module's guard
can also name the sessions it runs in, by a function of the session's environment that
turns asks once, when the session starts; it then runs in those sessions with no switch
and no entry on `guards.run`. `moxi doctor turns` asks that function in its own
environment, so a guard on only in certain sessions reads off there.

A guard runs at the call step, before a tool call runs, unless it says `step = "ask"`:
then it runs when Claude Code is about to ask you to approve a call, and it can refuse
that ask (Claude Code denies the call and the agent reads why) but never approve one.
Only Claude Code has an ask step.

## Settings

`moxi config turns` shows every key and its value; `moxi config set turns <key> <value>`
sets one in turns' config file, `~/.config/moxi/turns/config.toml` by default
(`$MOXI_HOME/config/turns/config.toml` with `MOXI_HOME` set).

- `commands.allow`: the session commands (`clear`, `exit`, `compact`) every session may be
  asked to run; a launch may add more for itself. Default none.
- `claude.endpoint`: the endpoint command the Claude plugin spawns. Empty means this
  environment's `moxi-turns-endpoint`.
- `stop_handler.wait_ms`: how long a Stop waits for the session's stop handler, in
  milliseconds. Default 2000.
- `listen.wait_ms`: how long an event waits for the registered listeners, in
  milliseconds. Default 2000. A module's prompt listener can ask for a longer wait on a
  prompt in the sessions it serves; that session's prompt then waits the longer bound,
  on Claude Code and Codex alike.
- `start.order`: what adds context when a session starts, resumes, is cleared or is
  compacted, in order: module names, file paths and run entries. A module not installed
  is skipped. A run entry, `run:<path>`, runs that program at each start, with the
  start's source as its argument and the session's facts as JSON on stdin; what it prints
  on exit 0 is added. It has the listeners' `listen.wait_ms` to finish and is then
  killed, so a program with longer work starts it in a session of its own and exits.
  Default `["launcher", "comms", "notes", "memory"]`; setting it replaces the whole list.
  Add `turns` to the list to give each session its own id at every start, resume, clear
  and compaction.
- `start.limit_chars`: the most start context a session gets, in characters; 0 means the
  harness's own ceiling. Default 0.
- `guards.run`: the guards that run besides those switched on: guard ids and paths to
  guard files. Default none.
- `guards.removal`, `guards.no_nested_agents`, `guards.question_mode`: the switches of
  turns' three guards. Default off.
- `guards.max_wait_ms`: the most any guard may take on one tool call, in milliseconds.
  Default 3000.
- `after_tool.wait_ms`: how long the after-tool listeners may take on one tool call,
  together, in milliseconds. Default 300.
- `prompt.own_origins`: the prompt origins that count as yours, which question mode and
  every prompt listener read (a `prompt` event's `own`). Default `["composer", "bridge"]`:
  `composer` is a prompt typed at this terminal, `bridge` one sent from the Claude apps
  through Remote Control. On Codex a prompt typed in the window is `composer`, except
  while a delivery the host handed in is still out and the prompt's text matches none:
  then it carries no origin.
- `question_mode.prose_words`: words that ask for prose: a prompt holding one gets no
  question-mode reminder. Default `["prose", "pros"]`.

`hold.wait_ms` is retired: a file that still sets it is told to set
`stop_handler.wait_ms` instead. `question_mode.origins` is renamed: a file that still sets
it is told to set `prompt.own_origins` instead.

## The panel

With the `ui` extra (`moxi-ui`), turns has a panel in the browser UI shell at `/m/turns/`:
the live list of every running session turns can reach. It only reads: nothing on it puts
input into a session or acts on one. Its page server finds sessions by rescanning turns'
runtime directory every 2 s and holds one watch per session; turns adds no daemon for it.

- **Groups and order.** A session is in the first group that holds: `problem` (its link to
  Moxi has trouble, or Moxi can't read its live state), `idle` (read, no turn open) or
  `working` (read, a turn open). The labels are `1 problem` or `<n> problems`, `<n> idle`
  and `<n> working`; a group with no row isn't drawn. Within a group the latest change
  comes first. A session with no first read yet isn't drawn until it has one.
- **A row.** Line one is the session's name, a dim `where`, and at the right when it
  came into its state (`since 09:40`, with a day word when it isn't today); a problem
  row says its state there too (`idle since 09:40`). The name is the working directory's
  last segment and when the session started (`parser · started today 08:20`); a session
  with no directory is `Claude Code session` or its harness's name. Line two is what is
  busy: `1 subagent busy` and `1 teammate busy` with the entry's description,
  `<n> shells running`, `may hold a draft` for a Claude Code session whose prompt box may
  hold text. A problem row says its lead and reason instead (`Its link to Moxi has
  trouble`, `Moxi can't read its live state`, then why). A row with nothing to say is one
  line.
- **The opened row.** `Open` opens one row at a time and writes `session/<key>` to the
  address, so a link opens that row and Back leaves the page; the address survives a
  `/clear`. The opened row adds a line each for `Now` (`working on turn 4 since 09:40`),
  `Agents` (each subagent, teammate and shell with its state, busy ones in the text
  colour, and `not known` when the list could not be read whole), `Prompt box` (`empty`
  or `may hold a draft`), `Session` (harness, start, working directory) and `Details`
  (process id, ready time and source, turns' version and the runner's), and `Copy id`,
  which copies the session id.
- **The reach line.** Under the list, one dim line says which sessions show here and
  which don't, from what turns finds: an interactive Claude Code session shows from its
  start once its plugins are installed, a Claude Code session started by hand before
  `moxi install turns` can't show yet, and a Codex or OpenCode session shows only when
  Moxi launched it in a terminal or a slot, a Codex one only with turns' Codex host
  installed (the `codex` extra). With Claude Code missing it says to run
  `moxi install turns` once it is installed. Where the line names the command it carries
  `Copy`. `Settings › turns` sits at its right.
- **Launcher's join.** With `moxi-launcher` installed, a session Moxi launched in a
  terminal or a slot takes its launch's name (`kestrel-builder`) and its `where`
  (`slot 2 · kestrel-web`), paired by session id. A launch with no session id, or an id
  two rows share, names nothing.
- **States.** A read still out after 400 ms shows `Reading…`. When the page server stops
  answering, the first row says `Moxi can't read the live sessions` and what it answered,
  with when the list was last read, and the last rows stay dim with no `Open`. A scan that
  fails says `Moxi couldn't look for sessions` over the rows last read. With no session,
  `No session is running.` and the reach line; with Claude Code missing and no launcher,
  `No session can show here yet.` An address that names no listed session opens the list
  with `That session isn't running now.` While a session's watch is down but its reads
  answer, its row keeps its group and says `read every 30 s` on a dim line of its own:
  the page server reads every session again within 30 s.
- **Reading.** The list follows the shell's push channel as sessions start, turn and end,
  is read again when the tab is shown, and is redrawn once a minute for its day words.
  A Claude Code run with no window (headless) spawns no endpoint and is not listed.

## Requirements

- Linux and Python 3.12 or later.
- `moxi`, Moxi's core, 0.1.6 or later.
- The `ui` extra adds `moxi-ui`, the browser UI shell, for the panel.
- The `codex` extra adds `websockets`, which the Codex host needs; it also needs `codex`
  on PATH. The host's entry is installed either way; `turns.codex_host_available()` says
  whether the extra is there.
- Claude Code sessions need Claude Code with the two plugins loaded. The plugins are
  TypeScript hook modules (`hooks/register.ts`) that Claude Code loads itself. They have
  been run against Claude Code 2.1.296; `moxi doctor turns` says `ok` for that version
  and warns on any other.
- Codex sessions need Codex 0.153.0 or later, the first with `thread/inject_items`; an
  older Codex is refused with one line naming that version.
- OpenCode sessions (experimental) need `opencode` on PATH or in `~/.opencode/bin`.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-turns-v0.2.6/packages/turns/LICENSE).
