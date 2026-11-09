# Changelog

## 0.2.4 (2026-11-08)

- Config keys `start.limit` and `guards.on` are now `start.limit_chars` and
  `guards.run`. An old key, set in the config file, is refused, naming the new one.
- `moxi-turns-ping --wait-seconds` is now `--wait-s`. Run `moxi install turns` so the
  setup writes the new flag into the Codex hook commands; until then every Codex hook
  call fails on the old flag.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_turns.client.Delivery.wait_ended`: `timeout` is now
  `timeout_s`; `moxi_turns.client.Delivery.wait_started`: `timeout` is now `timeout_s`;
  `moxi_turns.client.Session`: `directory` is now `endpoint_dir`;
  `moxi_turns.client.Session.codex_limits`: `timeout` is now `timeout_s`;
  `moxi_turns.client.Session.command`: `timeout` is now `timeout_s`;
  `moxi_turns.client.Session.mcp_reload`: `timeout` is now `timeout_s`;
  `moxi_turns.client.wait_ready`: `timeout` is now `timeout_s`;
  `moxi_turns.codex_appserver.codex_rate_limits`: `timeout` is now `timeout_s`;
  `moxi_turns.results.Agents.busy`: `shells` is now `include_shells`.
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
