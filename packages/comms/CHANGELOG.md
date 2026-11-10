# Changelog

## Unreleased

- The connector's status and tools no longer say `offline` while another binding holds
  its identity: the close of a bind the daemon refused could land after the link had
  noted the name held, and turned the state to `offline` until the next try. Only the
  binding's own connection now changes the link's state when it closes.

## 0.3.0 (2026-11-08)

- Config keys `connector.wait_default` and `connector.wait_max` are now
  `connector.wait_default_s` and `connector.wait_max_s`. An old key, set in the config
  file, is refused, naming the new one.
- `moxi comms delete` is now `moxi comms remove`, with no alias; the MCP tool `delete`
  and the daemon method `delete` are now `remove`.
- `moxi-comms-opencode --ready-wait` and `--notice-hold` are now `--ready-wait-s` and
  `--notice-hold-s`.
- The connector's `send_and_wait` input `wait_seconds` is now `wait_s`; the `session`
  answer's and the connector read result's `age_seconds` are now `age_s`.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_comms.api.attributed`: `deadline` is now `timeout_s`;
  `moxi_comms.api.check`: `deadline` is now `timeout_s`; `moxi_comms.api.delete`:
  `deadline` is now `timeout_s`; `moxi_comms.api.identity_state`: `deadline` is now
  `timeout_s`; `moxi_comms.api.owner`: `deadline` is now `timeout_s`;
  `moxi_comms.api.roster`: `deadline` is now `timeout_s`; `moxi_comms.api.roster_rows`:
  `deadline` is now `timeout_s`; `moxi_comms.api.send`: `deadline` is now `timeout_s`;
  `moxi_comms.api.send_as`: `deadline` is now `timeout_s`; `moxi_comms.api.session`:
  `deadline` is now `timeout_s`; `moxi_comms.api.session_of`: `deadline` is now
  `timeout_s`; `moxi_comms.cache.Clock`: `age_seconds` is now `age_s`;
  `moxi_comms.cache.Clock`: `ttl_seconds` is now `ttl_s`.

The first public release on PyPI.

- The README is the package's PyPI page, and its links work from there. It says that the
  daemon serves comms' tools on `127.0.0.1:8450` by default and how to turn that off,
  defines "operator" and "director", lists `cache`, `whoami`, `reserved renew` and
  `migrate`, and says macOS and Windows are not supported yet.
- `docs/security.md` names the tools' loopback port and the OpenCode adapter's loopback
  server, says what another account on the machine can and can't do with each, and no
  longer says comms opens no network connection.
- `moxi comms service install|uninstall|status` is removed, and so is
  `moxi_comms.release.UNIT`. `moxi install comms` writes and starts the daemon's unit
  through the core; stop and start it with `moxi services` or the supervisor.
- Doctor fixes and the wire's daemon-is-older refusal say `moxi services restart
  comms/daemon` when services is installed. Without it they name the supervisor's restart
  of the daemon's unit by its id in this home (`systemctl --user restart
  moxi-comms-daemon.service` in the default home). A new `services` extra brings
  services in.
- The Claude and Codex wrappers wait up to 15 s for their bind (up from 5 s and 10 s),
  say after 2 s that they are waiting, and end the launch with exit 75, without starting
  the harness, when the daemon doesn't answer. They no longer start it anyway.
- An adapter whose wrapper has stopped waiting lets the name go and ends before it
  delivers anything.
- `moxi_comms.api` gains `ENDS_ON_BIND_TIMEOUT` (`{"claude", "codex"}`) and `BIND_WAIT`
  (15.0), so a launcher can tell which wrappers end a launch on a bind timeout and how
  long they wait.
- A Codex launch that resumes a thread which has had no turn yet reads that thread as
  unturned, so the cache can read it fresh. It also no longer misses the ready of a
  later reconnect.
- Held mail's help says it goes out at the end of the recipient's next turn. The daemon
  logs that a release waits on a cold cache only when mail is waiting for that name and
  has not been handed to it.
- A Claude Code turn that ends with an answer releases the session's held mail as it
  ends. The adapter marks its `turn_ended` `answered: true`, and the daemon takes that
  as proof the cache is warm: it no longer waits for the transcript to show the turn,
  so a transcript write that lags past the 8 s settle window no longer leaves the mail
  waiting for the turn after. Other turn ends read the cache as before. A daemon older
  than this ignores the mark.
- comms needs moxi 0.1.6 or later, for the unit ids its fix lines name.

## 0.2.1 (2026-11-07)

- The `ui` extra needs moxi-ui 0.2, where the page-server base now lives as
  `moxi_ui.loopback`.

## 0.2.0 (2026-10-27)

- Bindings now receive a fresh token at registration. The adapter keeps it only in
  memory, the daemon stores only its hash, and a daemon restart requires every adapter
  to bind again. The operator lane reads its token through the configured resolver
  chain and keeps that one credential at rest.
- Existing 0.1.x records are converted on the first start. Retired keep files and old
  posting-key files in comms' own store are removed when they match the old format;
  unrelated files are left alone. The old webhook posting path is no longer part of
  the live seam, and `post_as_identity` is a record write rather than a credential
  path.
- The public surfaces advance to interface 2, claims protocol 2 and CLI protocol 3.
  The old capacity settings, roster pin and keep behavior are retired, and adapters
  from the older protocol must be relaunched.
- Going back to 0.1.x after the first 0.2.0 start is not supported: old readers do
  not understand identity rows, and the old posting keys are no longer available to
  comms' live path.

## 0.1.1 (2026-10-22)

- The directory's webhook column that holds a credential's store pointer is named
  `secret_id` in a directory this release creates. An existing directory keeps the
  column name it was created with: nothing renames it, and every reader and writer takes
  the name from the table it opens, so 0.1.0 and 0.1.1 processes can run side by side
  on a directory 0.1.0 created. A directory 0.1.1 creates can't be read by 0.1.0.
- One of the retired `[policy]` keys is no longer declared, so a config file that sets
  it is refused as an unknown key, with the same exit and the generic fix ("remove it,
  or check its spelling"), rather than as a retired one. `[claude] channel`, `[policy]
  never_held` and `[policy] next_turn_refused` stay declared, each refused with its fix.
- Docs, docstrings, comments and tests are cleaned up: they state each rule in plain
  words, and the test suite uses made-up names throughout.
- The source distribution lists the paths it includes instead of the paths it leaves out.
- The other Moxi modules (`moxi-sessions`, `moxi-turns`, `moxi-cache`) now ship a README
  and a LICENSE.

## 0.1.0 (2026-10-21)

The first public release of Moxi comms: messages between AI coding agents on one
machine, delivered into each recipient's session at a turn boundary.

- A local daemon, `moxi-comms-daemon`, that routes messages between named agents over a
  SQLite record and Unix sockets, with no server, account or network. Mail to a name that
  isn't running waits in the record and is delivered when a session with that name
  starts; held mail survives daemon restarts.
- The commands run as `moxi comms <verb>`: `send`, `reply`, `agents`, `history`, `help`,
  `whois`, `explain`, `cache`, `doctor` and `service`. `moxi shortcut comms` adds a bare
  `comms` command. `moxi comms service install` runs the daemon as a systemd user unit,
  and its first start provisions the directory and the operator identity.
- Claude Code sessions through Moxi turns: `moxi comms claude` (also
  `moxi-comms-claude`) launches a named session with an adapter process of its own,
  which binds the name, hands each digest to turns at a turn boundary, settles it from
  the turn's outcome, and holds the session's Stops for reply enforcement. comms writes
  no Claude hook or settings file; its launch context (the context block, the roster and
  its deltas) reaches the session through comms' listener, registered with turns.
- Experimental adapters for Codex (`moxi-comms-codex`) and OpenCode
  (`moxi-comms-opencode`), each launched from a roster in comms' config file.
- Postures: every new send says what it wants back with `--posture none|optional|once|open`.
  A reply takes its posture from what it answers. `open` starts a thread with limits
  (`[threads] reply_cap`, `--cap N` or `--no-cap`). The Stop hold reminds a session of
  every verified `[reply-once]` ask it owes and of the operator's mail.
- Attribution and the custody journal: the record says who sent each message, and a
  send's identity is its session's live claim, read through the claim socket its
  adapter serves. The operator's own terminal (`MOXI_COMMS_OPERATOR`) is the one lane
  outside every session.
- The cold-cache hold (experimental): a message to an agent whose prompt cache is cold
  is posted and held for that agent's next session start, /clear, compaction or turn,
  from any sender and on any adapter. Cache reads come from the optional cache module
  (`moxi install comms --with cache`); without it every agent reads as having no cache
  and gets its mail live, unless `[cache] required` makes the daemon refuse to start.
- `moxi comms send --check` runs every check a send makes and posts nothing.
- One message holds at most 16,000 characters, routing line included; comms does not
  split a message.
- One config file, `~/.config/moxi/comms/config.toml`, declared in the package and read
  through `moxi config`. A key comms does not declare is refused.
- A library for sending, `moxi_comms.api` (interface 1), which `moxi comms send` and
  `reply` render, and `moxi_comms.adapter` for adapters built outside comms. Adapter
  kinds are declared through the entry-point group `moxi.comms.kinds`.
- Linux only. Python 3.11 or later.
