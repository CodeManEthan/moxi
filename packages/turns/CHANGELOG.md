# Changelog

## 0.2.8 (unreleased)

- Before a Claude Code session's first guard table, the runner refuses what the last
  table's guards could have refused, with a reason that says the table didn't load: a
  subagent's `Agent` or `Task` call when no-nested-agents was on, a `Bash` command the
  removal guard's pre-filter matches when the removal guard was on, and a bypass-mode
  permission ask (not `ExitPlanMode` or `EnterPlanMode`) when an ask-step guard was on.
  It reads the record once, at the first session start; with no usable record it refuses
  all three. Headless sessions and sessions with a table are unchanged. The runner is
  version 0.3.3, so `moxi install turns` updates an install at user scope.
- The Claude endpoint records the guard table it hands the runner, at its first line and
  at each ready, in `STATE/turns/guards-seen.json`: the time, and whether the table held
  the removal guard, the no-nested-agents guard and any ask-step guard (all false for an
  empty table). A table made while turns' config doesn't read is not recorded, so the
  record before it stands. A launch names the path in `MOXI_TURNS_GUARDS_SEEN` (in `INTERNAL_ENV`
  and `SESSION_ENV`), setup writes it as `guards_seen` in `endpoint.json`, and the store
  is declared as `guards-seen`. `moxi_turns.guards.seen` reads and writes it.
- Setup still replaces its own placed Claude plugin copy whatever it holds, moving the
  old copy to the core's displaced folder: it passes the placer's new `accept_diff`, since
  the copy is turns' own and setup runs with no one to ask. Needs `moxi>=0.1.11`.
- A `project-facts` component for the Projects page. It reads turns' own `/api/sessions`
  and `sessions` topic, and reports, by session claim, whether each session turns can read
  is in a turn (`live`, `{in_turn, state}`), so the page can mark a running line
  `working`. It names no project. A session turns can't read now, or one whose claim two
  sessions share, is left out; a failed read reports nothing.
- A new capability, `stale-agents`, which Claude Code sessions offer: the endpoint judges
  each `waiting` subagent Claude Code lists, and one whose transcript's last timestamped
  line is at least an hour old, whose last turn record is an assistant `end_turn`, and
  whose own agents and background-task output files wrote nothing within the hour reads
  `idle`. A transcript the endpoint cannot find or read, a record shape it does not know,
  and an entry it has no time for within the list's 0.3 s deadline (the search of its
  whole transcript for output files included, read a piece at a time) keep Claude Code's
  status. Teammates and forks of the main loop are never judged.
- The Claude runner's `/live` answer and the gate's readings send the subagent list's
  entries instead of a count, so a delivery's `refuse_agents`, a session command's
  subagent check and `facts.agents` count the judged list. The endpoint still reads an
  older runner's or gate's integer count, unjudged. The gate is version 0.1.1 and the
  runner 0.3.2, so `moxi install turns` updates an install at user scope.
- `agents` entries gain `is_fork` (a fork of the main loop, which Claude Code lists as a
  subagent) and `last_write_at` (the newest write the judgment read).
- `STALE_AFTER_S` is exported from `moxi_turns`: the seconds a `waiting` subagent must be
  silent to read stale, so a caller can say when an entry's `last_write_at` would make
  it stale.
- The guard API gains `touched_files(call)`, exported from `moxi_turns`: the files a
  write call touches, in the call's order, each a `TouchedFile(path, kind, text)`.
  Claude Code's `Write` and `Edit` and OpenCode's `write` and `edit` give one `write`
  entry; a Codex `apply_patch` gives one entry per section, `add`, `update` or `delete`,
  and an update with `*** Move to:` gives `moved-from` and `moved-to`. `path` is as the
  call names it, `text` what the call puts in the file. A tool it does not know, or
  input of the wrong shape, gives `()`; it never raises.
- On Claude Code, a steer that lands while the model writes its last answer no longer
  waits for the next prompt. Claude Code holds such a note for the session's next
  request when the turn makes no further one; the runner now reads that at the turn's end
  and reports the steer held, and the steer opens a turn of its own, ahead of every queued
  delivery. That turn's prompt is a short line saying a note came with it, since Claude
  Code sends the held note beside it, so the model reads the note once; the session's
  `prompt` listeners read the steer's text as the prompt's `text`. The steer's
  `turn.steered` names the first turn, whose `turn.ended` no longer names it; the new
  turn's `turn.started` and `turn.ended` name it as the delivery that opened it, and its
  `ended` is that turn's end. When another turn's request carried the held note first
  (the person's prompt, or a delivery that went ahead), the steer opens no turn: it joins
  that turn as a steer while it runs, or ends with it once it has ended. When a /clear
  or a compaction dropped the held note before the steer's turn, the steer's own text is
  that turn's prompt, so the message is not lost. When no request of the steer's own
  turn carried the held note (a compaction inside that turn's first request dropped it),
  the runner reads that at the turn's end, and the steer opens one more turn, once, with
  its own text as the prompt where the note is gone; the first turn's `turn.ended` then
  does not name it. A steer a request carried ends with its turn as before. The runner is version 0.3.1, so
  `moxi install turns` updates an install at user scope.

## 0.2.7

- A new request, `fork {text, by, ref?}` (the client's `fork(text, by=, ref=)`), and a new
  capability, `fork`: start a fork of the session's main loop with `text` as its task. It
  runs beside the main loop's turns and holds no slot. Its `started` value carries the
  fork's `agent_id` (a new field of `started`, null for every other delivery), its
  `ended` the fork's answer, and a fork the harness refused or that went away reads
  `lost` with a reason starting `denied` or `gone`.
- Claude Code sessions offer `fork`. The runner spawns a background fork of the main
  loop with a one-word warm-up, then sends the task as the fork's first resumed round,
  whose answer Claude Code announces to the main loop as it announces any finished
  subagent; the runner leaves that notice as Claude Code sends it. The delivery's answer
  is that round's. A refused spawn reads `lost` `denied`; a warm-up that does not
  complete within 30 s or ends other than with an answer, a task Claude Code does not
  deliver, and a round with no end within its bound (120 s when the asker sets none) read
  `lost` `gone`.
- The Claude runner tells a fork's tool calls from its own by the call's `agentId`. A
  call counts as the runner's own, judged by no guard, only when it carries no `agentId`;
  every call of a fork the runner started is judged by the guards at the call step and
  the ask step, and gets no after-tool ask. The runner holds a fork's id until the fork's
  own turn.complete for its open round; a /clear, a compaction or a resume lets go of
  nothing, and only the end of the Claude Code process drops it. A round its bound ends
  reads `lost` `gone` at once, but the fork may still be running, so its id stays held
  until that late turn.complete, which lets it go and reports nothing; its calls
  meanwhile are still the fork's, and a `fork.continue` for it is refused. A call with
  an id the runner does not hold is judged and asked after as any agent's. The runner
  speaks leg 5 and is version 0.3.0, so `moxi install turns` updates an install at user
  scope.
- A new request, `fork.continue {delivery, text, ref?}` (the client's
  `fork_continue(delivery, text, ref=)`): give a fork that has answered its next round, a
  new delivery whose `ended` carries the round's answer. It reads `lost` `gone` when the
  endpoint knows no such fork, the fork never started or its round was lost, and `denied`
  while a round of the fork is still open. On Claude Code the runner sends the text to the
  finished fork, which answers again in its own conversation.
- `fork` and `fork.continue` take `round_timeout_ms` (the client's `round_timeout_s=`): how
  long the harness's half waits for the round's answer before the delivery reads `lost`
  `gone`. With none, Claude Code's runner waits 120 s. An endpoint of an older turns
  ignores it and waits its default.
- A new option, `keep` (with a `ref`), on `deliver`, `fork` and `fork.continue`, a new
  request, `ack {ref}`, and a new capability, `keep`, which every session of this version
  offers. A kept delivery's end is written to turns' state folder when it settles, the
  endpoint's own end included, and stays out of the settled deliveries the endpoint drops
  for space, so `wait` reads the same end at every read, after the endpoint has ended too
  (the client reads the state folder then, and a later endpoint of the session answers a
  `wait` by that `ref` from it). `ack` removes it; an ack of a `ref` with no kept end does
  nothing. Nothing removes a kept end by age: `moxi doctor turns` lists each one older than
  a day with its `ref` and asker. The client gains `keep=`, `Session.ack(ref)`, and
  `turns.wait_ref(ref, session_id=)` and `turns.ack(ref, session_id=)` for a caller that
  holds no `Delivery`. An endpoint of an older turns does not offer `keep`, and the client
  refuses to send `keep` to it. One `ref` holds one kept end: a `keep` under a `ref` whose
  kept end in the session is not acked yet is answered invalid params, so no kept end
  replaces another.

## 0.2.6 (unreleased)

- The Codex host takes `--fork THREAD_ID` (`codex.launch(fork=...)`), beside and never
  with `--resume`: its first load forks that thread with `thread/fork`, live or not, into a
  new thread that holds a copy of its conversation, and hosts the new one. The new thread
  is loaded as a resumed one, with no item injected, and its id names the session from
  the fork on, so a later load resumes it and the thread is never forked twice. Ready is
  `resume`, or `startup` when the forked thread has had no turn.
- The page's `Reading…` line, while its first read is out, shows once that read has
  been out 400 ms in every browser. In Firefox it could stay blank until the page's
  next redraw, up to a minute later, since the page checked the wait against
  `Date.now()`, which Firefox coarsens. It now takes the wait from moxi-ui's
  `WaitClock`, so the `ui` extra asks for moxi-ui 0.2.6 or later.
- With `moxi-sessions` installed, `Live sessions` leaves the browser UI shell's band of
  pages: the Sessions page shows the running sessions with turns' live state. `/m/turns/`
  still opens the panel. It needs ui 0.2.5 or later, which reads `band_unless`; an older
  ui keeps the entry.

## 0.2.5 (2026-11-22)

- A new listener event, `load`, observe only like `ready`: a Claude Code endpoint raises
  it once per endpoint process, the first time it learns its session's id. That is
  normally the runner's `session` line at the endpoint's own start, so a module hears of
  an endpoint that replaced another (Claude Code ends and restarts every endpoint when
  its plugins reload) without waiting for the session's next start, resume, clear or
  compaction. When that line carries no id, `load` comes just before the first `ready`.
  Every row naming it is called with the session's id, harness and environment, and its
  answer is ignored. A turns older than this refuses a registration that names `load`.
- `question_mode.origins` is retired and replaced by `prompt.own_origins`, default still
  `["composer", "bridge"]`: the prompt origins that count as the person's own. Each
  `prompt` event carries `own`, true when its origin is on that list, and question mode's
  reminder reads it. The old key is not read as an alias: while a turns config file still
  sets `question_mode.origins`, every turns endpoint refuses to start (a session then has
  no guards and takes no deliveries) and names the key to set. Move the value to
  `prompt.own_origins`, or, when it is the default `["composer", "bridge"]`, delete the
  old key.
  On Codex a prompt typed in the window now carries the origin `composer`, and one the
  host handed in for a delivery carries none. The host places a prompt by its text: one
  that matches no hand-in of its own while any is still out carries no origin either.
- `deliver` and `steer` take `by`, the module that asked, and `senders`, the names the
  text speaks for (the client's `deliver(..., by=, senders=)` and `steer(...)` too). A
  `prompt` event carries `delivery_by` and `delivery_senders` from the delivery that is
  the prompt, both None for a prompt turns did not hand in, and `opens`, false when the
  prompt joins a main-loop turn already running.
- A delivery's `ended` value carries `answer`, the final text of the turn that took it:
  on Claude Code the assistant's final visible text, on Codex the turn's last agent
  message; null when the turn had no text, on both, and where the harness gave none.
- A listener row takes the option `wait_ms`, a function of the session's environment that
  returns the row's own bound on `prompt` for that session, in milliseconds. The session's
  prompt bound, decided at its start, is the longest of `listen.wait_ms` and those: the
  endpoint asks the prompt listeners within it, the Claude runner waits it plus its margin
  (the endpoint's first line carries `prompt_wait_ms`), and a Codex launch bakes it into
  its UserPromptSubmit hook command. The runner plugin is version 0.2.2, so `moxi install
  turns` updates an install at user scope.
- A guard takes `sessions`, `"package.module:function"`: a function of the session's
  environment that answers True to turn the guard on in that session, beside
  `guards.run` and its switch. An endpoint asks it once, at the session's start, and keeps
  the answer through every `ready`. One that does not import, raises or answers something
  other than a bool reads off, is logged, and fails its `moxi doctor turns` row. A custom
  guard file can name none, since the list already turns its guards on.
- Question mode's prompt reminder leaves the agent to judge whether the questions are
  wanted now ("If they are asking now, ..."), and asks it to count any new questions at the
  end of its output once they are answered. "pros", as voice input spells "prose", now
  asks for prose too: the default of `question_mode.prose_words` is `["prose", "pros"]`.
  Question mode's judge may take up to 3000 ms on a call, still clamped by
  `guards.max_wait_ms`, before the call is refused.
- Until a Claude Code session's endpoint has sent its guard table, the runner refuses
  every `AskUserQuestion` call, a subagent's too, with "turns' guards aren't loaded, so
  question mode can't check this call; ask in prose", since it can't tell whether
  question mode is on. Other tools pass as before, and a headless session is not refused.
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
