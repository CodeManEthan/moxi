# Changelog

## Unreleased

- Question mode's prompt reminder leaves the agent to judge whether the questions are
  wanted now ("If he is asking now, ..."), and asks it to count any new questions at the
  end of its output once they are answered. "pros", as voice input spells "prose", now
  asks for prose too: the default of `question_mode.prose_words` is `["prose", "pros"]`.
  Question mode's judge may take up to 3000 ms on a call, still clamped by
  `guards.max_wait_ms`, before the call is refused.
- Question mode counts a prompt sent from the Claude apps through Remote Control
  (origin `bridge`) as the person's own: the default of `question_mode.origins` is now
  `["composer", "bridge"]`, so such a prompt that mentions a question lets
  `AskUserQuestion` run and gets the question-mode reminder. A `plugin` prompt and one
  with no origin are still refused. To count only prompts typed at the terminal again, set
  `question_mode.origins = ["composer"]`.
- The panel at `/m/turns/` is redrawn as groups of rows. Sessions fall under `problem`,
  `idle` and `working`, latest change first, each row with its name (a launched
  session's launch name with launcher installed, else its folder and start), how long it
  has been in its state, and what is busy or in trouble. `Open` opens one row at a time at
  `session/<key>`, with `Now`, `Agents`, `Prompt box`, `Session`, `Details` and `Copy id`.
  A reach line at the foot says which sessions show here and which don't, with `Copy` on
  `moxi install turns` where it applies, and `Settings › turns` beside it. The page says
  when a read failed, a scan failed, no session runs or an address names none; a row
  whose session's watch is down while its reads answer keeps its group and says
  `read every 30 s` on a dim line of its own. The page
  server sends each session's turn start time, session id, whether it has a prompt box,
  when it ended its last turn, since when it can't be read, and the `reach`; it reads
  every session again within 30 s, and reads `reach` on a thread of its own.
- The Claude runner now takes a session as headless only when Claude Code's own
  `session.start` says nobody is at the prompt (`isInteractive` false). It used to read
  the session's surfaces in whichever start hook ran first, and Claude Code usually runs
  SessionStart first, before the terminal is bound in a new config folder. Such an
  interactive session ran with no endpoint, no ready, no guards and no permission ask
  step, and turns could not find it. SessionStart now waits up to 1 s for the decision,
  except under a print or SDK start (`CLAUDE_CODE_ENTRYPOINT` starting with `sdk-`); when
  it doesn't get it, `session.start` makes the ready ask itself before the first prompt,
  and that ready's context is dropped. The runner plugin is version 0.2.1, so `moxi
  install turns` updates an install at user scope.
- Every setting carries page fields, which `moxi config turns show --json` prints and
  the settings page draws: a plain label, a line where the label can't carry it, the
  unit of each wait and of the start-context limit, six settings shown first under
  Guards (the three guards and the other guards that run) and Sessions (the start list
  and the session commands), the waits under Waits, and a can-hurt mark on turning a
  guard off and on removing a guard from `guards.run`. The schema now lists the guards
  first and the start list before the session commands; no key changed. turns needs
  moxi 0.1.9 or later.
- The Claude runner plugin took a new version, 0.2.0, for the leg it speaks now. Claude
  Code runs a user-scope plugin from a cache kept per version, so `moxi install turns` now
  updates a plugin installed at another version than the one it ships (`claude plugin
  update`) instead of skipping it. Before, an earlier install kept running the old
  runner, which the endpoint refuses, so every session it loaded in ran without turns.
  `moxi doctor turns` has a new row, `claude installed runner`, that reads the leg of the
  runner Claude Code runs at user scope and fails when it is not the endpoint's.
- `start.order`'s default is `["launcher", "comms", "notes", "memory"]`, the
  start-context modules in this repository. A config that sets the key keeps its own list.

## 0.2.4 (2026-11-08)

- Config keys `start.limit` and `guards.on` are now `start.limit_chars` and
  `guards.run`. An old key, set in the config file, is refused, naming the new one.
- `moxi-turns-ping --wait-seconds` is now `--wait-s`, with no alias. A Codex session's
  hook commands are built when it launches, so a session launched after the upgrade
  needs nothing. A Codex session already running keeps the old flag, and each of its
  hook calls fails until it is relaunched.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_turns.Agents.busy`: `shells` is now `include_shells`;
  `moxi_turns.codex_rate_limits`: `timeout` is now `timeout_s`;
  `moxi_turns.Delivery.wait_ended`: `timeout` is now `timeout_s`;
  `moxi_turns.Delivery.wait_started`: `timeout` is now `timeout_s`;
  `moxi_turns.Session`: `directory` is now `endpoint_dir`;
  `moxi_turns.Session.codex_limits`: `timeout` is now `timeout_s`;
  `moxi_turns.Session.command`: `timeout` is now `timeout_s`;
  `moxi_turns.Session.mcp_reload`: `timeout` is now `timeout_s`;
  `moxi_turns.wait_ready`: `timeout` is now `timeout_s`; `moxi_turns.Session`: `environ`
  is now `env`.
- `start.order`'s default is `["launcher", "comms", "notes"]`, the start-context modules
  in this repository. A config that sets the key keeps its own list.
- The README is written for a newcomer: what turns is, its three guards and what each
  refuses, every setting, the Claude Code version the plugins were run against, the Codex
  version it needs, and that it needs moxi 0.1.6 or later. It installs from PyPI and its
  links work from PyPI. The package declares keywords and classifiers.
- The OpenCode host is experimental: no real run has confirmed it.

## 0.2.3 (2026-11-08)

Not published.

- The open turn is shaded with the theme's selected colour.
- The `ui` extra needs moxi-ui 0.2.3 or later, which has that colour.

## 0.2.2 (2026-11-07)

Not published.

- Setup places a copy of the Claude marketplace in turns' data dir, writes
  `endpoint.json` there, and installs the plugins from it. It never writes into the
  package. An older user-scope install made from the package's folder is moved to the
  copy.
- Launches and the doctor's plugin rows use the copy when it is current, else the
  package's folders. A new doctor row, `claude placed copy`, compares it with the
  package.
- New `TEARDOWN`: `moxi uninstall turns` removes the marketplace and the copy.
- Setup without Claude Code on PATH skips its plugin with one line and succeeds.
- A fresh Codex thread is opened with one injected developer item instead of a priming
  turn. No model call is spent, and the window opens on an empty transcript.
- A Codex without `thread/inject_items` (before 0.153.0) is refused with one line naming
  the version that has it.
- A Codex thread reloaded before its first turn is treated as a start: its Ready reads
  `startup`, and the first turn's SessionStart `resume` reaches the listeners as
  `startup`. Each resumed load makes one more `thread/turns/list` call.
- `moxi-turns-codex-host` stopped once its first load is accepted, while the launch
  is still finishing, ends the session and exits 0. It used to exit 1, saying the
  launch was stopped before the session was ready.
- turns needs moxi 0.1.6 or later.

## 0.2.1 (2026-11-07)

Not published.

- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.

## 0.2.0 (2026-10-30)

Not published; summarised from the repository's history.

- Socket protocol version 2. Deliver and steer are answered before the harness acts, on
  one ordered worker per session. Callers of 0.1 must move to it, and comms needs turns
  0.2.
- Compact, and stopping a command's background shells, on Claude Code, Codex and
  OpenCode. A session's subagents are listed on every harness.
- The start list (`start.order`, `start.limit`): which modules add context at a
  session's start, in what order, and how much.
- `launch_additions()`: what a launcher adds to a launch's argv and environment, from
  turns and from modules that contribute.
- The Codex host runs as a process of its own, `moxi-turns-codex-host`, with the `codex`
  extra; `codex_host_available()` says whether it can start.
- Guards: checks that refuse a tool call before it runs, declared by any module or in a
  file of your own. turns ships three, each off until switched on: removal,
  no-nested-agents and question mode. psutil is now a dependency.
- `moxi doctor turns` rows for the config, the start list, the listeners, the guards, the
  endpoints, the hook times and each harness.
- With the new `ui` extra, a read-only panel of live sessions in Moxi's browser shell.
- Python 3.12 or later.

## 0.1.1 (2026-10-22)

- A README and the MIT license.
- Comments, docstrings and tests state each rule in plain words.
- The unused ping module is removed.
- The source distribution lists the paths it includes.

## 0.1.0 (2026-10-21)

The first public release.

- One turn API over Claude Code, Codex and OpenCode: deliver a turn, steer the running
  one, run a session command, and read the session's events, through an endpoint per
  session over a Unix socket.
- Claude Code sessions are reached through two Claude Code plugins shipped in the
  package; the `codex` extra brings what the Codex host needs.
- Python 3.11 or later.
