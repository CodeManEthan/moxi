# Changelog

## Unreleased

- The comms daemon replaces a Claude Code session's adapter that died while the session
  runs at once, with no prompt: it sees the adapter's connection drop, waits up to 10 s
  for its claim socket to stop answering, and starts a new adapter with the environment
  `claude` started with, so mail that waited is delivered and starts a turn in an idle
  session. At its start the daemon does the same for adapters that died while it was
  down. Run as a systemd service, it starts the adapter in a scope of its own
  (`systemd-run --user --scope`), outside the service's cgroup. It replaces one session's
  adapter at most 3 times in 10 minutes. The session's next start or prompt says once
  that its adapter had stopped, at what time, and that a new one bound it; the listener's
  own replacement at a start or prompt stays as the fallback.
- A Claude Code session whose comms adapter dies while the session runs gets a new one
  with no relaunch: at its next start or prompt, comms' listener starts an adapter that
  watches the same `claude`, reads the claim file and binds the same name, and says so
  in one line before the start's layer one and roster or the prompt's roster changes,
  which the session still gets. When none can be started or its bind is refused, the
  line is `comms: mail has stopped for <name>` with why. `moxi comms doctor` prints an `adapter` line and
  `moxi doctor` a fail row for each session whose adapter is gone. An adapter whose
  watch on `claude` fails keeps its claim file, so it is replaced too.
- `[archive] record` and `[archive] identities` name an older comms' record and
  identities files, which comms reads in place and never writes. `moxi comms history`
  and a session's `history` tool list the older record's messages before comms.db's, the
  tool with the state `archived` and a `skip` to page back past the newest messages it
  reads at once; `explain <name> <id>` of an older message says whether the older queue
  still held it for that name and whether that name's older cursor file stood at or past
  it, and that the rest of its delivery is not read; `reply` and every read by id find
  an older message by its own id, because the daemon moves comms.db's counter past the
  older ids once at its start (an older id comms.db had already used stays comms.db's
  message by id, and the older message under it is reached through `history`); and every
  name the older identities file retired is refused at bind and as a recipient again.
- `moxi comms opencode <identity>` launches an OpenCode session with comms mail, as
  `moxi comms claude` and `moxi comms codex` launch theirs. It runs the OpenCode wrapper
  `moxi-comms-opencode`, whose script is not on PATH, and names itself as typed.
- comms declares the `speech` adapter kind again, as a person kind: a binding of it (voice
  binds the operator's name as `speech`) has its mail delivered live, never held for a
  cold cache, and its cache is never read.
- The connector's `send` and `send_and_wait` are no longer refused while an agent waits on
  a single reply from the Claude app. The message posts, and the answer ends with a note
  listing the asks still open from its recipients: newest first, at most three and a
  count of the rest, each with its age and its first line. A reply through the connector
  lists the others the same way, leaving out the ask it answers. Both tools now speak every
  note of their send, and `send_and_wait` returns a `notes` field. The connector's refusals
  name only its own tools and parameters (`sending.CURES_CONNECTOR`). Agent sessions'
  tools and `moxi comms send` keep the refusal and its text.
- A connector `send` or `send_and_wait` to a name whose session has no turns endpoint now
  says in chat `NOTE: <name>'s session can't be reached right now, so the message is held
  until it is reachable.`, in its text and its `notes` field. It no longer names `moxi
  comms doctor`, which the person in the chat can't run. `moxi comms send` and agent
  sessions' tools keep their note, with how long the session has had no endpoint and where
  to look.
- With turns installed, comms' page reads turns' `/api/sessions` while a send box is
  open, with each check and every 30 s, and joins each session whose turn is open to its
  address by session id. A message the dry run would deliver to that session now reads
  `Delivered. <name> is working; it reads this when its turn ends.` Without turns, or
  for a name whose turn isn't open, the line is as before.
- The Comms page says what the connector's name is: the row of the name that comms' tools
  in the Claude apps bind reads `Claude's apps, through the connector` after the name. Each live row of
  `api.roster_rows()`, `agents --json` and the page's roster carries `via`, comms' words
  for a binding of the connector's kind while the connector module is installed, and null
  for every other row.
- Any program or service, not only a Moxi module, can hold a send-only name: `moxi comms
  send-only add NAME` registers it, `moxi comms send --as NAME` and
  `moxi_comms.api.send_under` send under it from outside every session, and `send-only
  renew` and `remove` replace its token or free it. Nobody mails it and no session binds
  it, as with a module's name. From inside a session only a plain `add` of an unused name
  goes ahead; `add --take`, `renew` and `remove` are for the user's own shell. `moxi
  comms agents` lists it as `send-only, registered`, a name an older comms kept now
  reads `send-only, nothing holds it`, and each send-only
  entry of `agents --json`, `api.roster()` and `api.roster_rows()` carries `holder`.
  `api.send_as` refuses a program's name (`not-reserved`), and `moxi comms reserved
  renew` refuses one. comms.db moves to schema step 4 at its next write; a comms older
  than this one can't open it afterwards, so a downgrade needs the store's backup.
- A Codex session can run headless: `moxi comms codex --name N --headless --brief TEXT`
  (or `--brief-file F`) starts it with no window under its name, hands it the brief as
  its first turn, and ends it by its exit rule, `--exit done|cold|idle=<D>|after=<D>`
  (repeatable; `done` when none is given). Mail sent to it starts turns while its cache
  is warm. It also ends when its brief fails, when it isn't ready within five minutes,
  and when its brief's turn hasn't started five minutes after it is; its host ends when
  its adapter does, by a lifeline only the adapter holds. The adapter's log names the
  cause in one line. comms needs moxi 0.1.11 or later.
- `moxi comms exit-session NAME [--scope S | --global]` ends a headless session by its
  name, through its claim socket's new `end` method, and waits for the name to be
  released. A claim file of a headless session records `headless` and `exit`.
- comms' API names the harnesses whose wrapper takes a headless launch, `HEADLESS`
  (`{"codex"}`), and the one parser of an exit rule, `parse_exit`.
- Every setting carries page fields, which `moxi config comms show --json` prints and
  the settings page draws: a plain label, a line where the label can't carry it, and
  the unit of each wait, age and replay limit. No setting is shown first and none is
  grouped, so the page lists them all. comms needs moxi 0.1.9 or later.
- `moxi_comms.api.send`, `check`, `send_as` and `delete` close the record they opened
  before they return; before, its connection stayed open until the garbage collector
  found it.
- Waiting mail is no longer skipped when an OpenCode or Codex adapter reconnects to the
  daemon. The daemon hands mail that waited for the agent as soon as it answers the
  reconnect, and the adapter could try to deliver it before it had switched to the new
  connection. It then dropped the message, which did not arrive until the agent's next
  reconnect or relaunch while newer mail went through. The adapter now holds delivery
  until the new connection is in place. The same applies to interrupt mail that reaches
  a Codex agent mid turn while it reconnects: it is no longer dropped, and goes into the
  running turn once the new connection is in place.
- An OpenCode or Codex adapter no longer gives up its own name while it reconnects to
  the daemon. When an attempt failed after connecting, that attempt's connection could
  report its close after the next attempt had bound. The adapter then said comms was
  offline, tried to bind the name it already held, was refused, and stopped delivering
  mail to its agent. A close from a connection the adapter has already replaced now
  changes nothing.
- An OpenCode or Codex adapter that registers again in place (`reregister`) no longer
  races its own reconnect loop. While it waited for the daemon to drop its old
  connection, the loop could bind the name first, and the register then failed after
  five seconds as "already bound" though the adapter was bound. Or the loop was refused
  by the old connection, and the adapter then counted the name as lost and dropped every
  message the daemon handed it until it registered again. The reconnect loop now waits
  until the register is done.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_comms.api.Sender`: `environ` is now `sender_env`.
- comms' page in the browser UI shell, `/m/comms/`, is rebuilt around the agents: one
  row per agent name, ordered by what it needs from you (an ask for your reply, mail no
  session takes, a warm session nothing keeps warm, then the rest by their newest
  message), each opened in place with its mail in the books, what it waits on and a
  send box that checks how a message would go before `Send` and can take a message back;
  under them today's messages, earlier days on `Show`, and threads opened in place; and
  `Other names` for the names with nothing waiting. Opening something writes the page's
  address (`agent/`, `reply/`, `thread/`, `mail/`, `others`). The README's "In the
  browser" says what each part shows and what launcher, cache and sessions add.
- comms has a row on Home in the browser UI shell, the `mail` component
  (`<moxi-comms-mail>`, order 200). When mail waits for names no live session holds it
  says so on one line, `3 messages waiting · curator and scout aren't running`, the count
  heavy, with `since 14:02` (the oldest message's time, with its weekday or date when it
  came on an earlier day) at the right, and the row needs the user (20 on Home's scale).
  Past three names it reads `curator, scout and 3 more aren't running`. With nothing
  waiting, or the daemon down, there is no row. It reads `/api/mail` when it is shown
  and every 30 s while the window is visible, and is a link to comms' page. It shows
  no message text.
- `moxi_comms.api.roster()` now carries the mail waiting per name: `Roster.waiting`, one
  `Waiting(qualified, count, oldest)` for each name with mail queued, as the daemon's
  `agents` answer holds it (`oldest` in epoch seconds).
- comms' page server has a new read, `GET /api/mail`: `{state, detail, waiting}`, where
  `waiting` lists `{qualified, name, count, since}` for each name with mail waiting that
  no live session holds, newest `since` last. `name` is the part before `@`, or the
  qualified name when two names listed share it. `state` and `detail` are as
  `/api/roster`'s. It is the one route of comms the phone's gateway serves; it carries
  names, counts and times, and no message text.
- `moxi_comms.api.RosterRow` has a new field, `cache`: a live name's cache as the
  daemon's `session` answer reads it, a `CacheFact(kind, until)` with `kind` `warm` or
  `cold` and `until` when it goes cold or went cold (epoch seconds, rounded to the whole
  second); None for a standing row, any other read, and every row on a box without the
  cache module. `moxi comms agents --json` prints it on each row, `{kind, until}` or
  null.
- `moxi_comms.api.roster_rows()` takes `with_roster=True` and then returns `(rows,
  roster)`, the `Roster` the rows were joined from, from the one `agents` call.
- comms' page server's `/api/roster` carries `today`, the server's local date
  (`YYYY-MM-DD`), and, with its `roster` topic, `waiting`: `{qualified, count, since}`
  for every name with mail queued, bound or not, oldest `since` first. Each row carries
  `cache`, `{kind, until}` or null, as `RosterRow`'s.
- comms' page server has a new read, `GET /api/messages?day=&before=&limit=`: one day's
  messages (today by default; an earlier day is read back from the message id `before`),
  newest first, at most `limit` (12 by default, at most 500), each with its author and
  tier, recipients, posture, reply, text, whether it was withdrawn, and each recipient's
  row in the delivery books (`waiting`, `handed`, `in_digest`, or why it left:
  `withdrawn`, `expired`, `reserved`); `more`, how many of the day's are left; and
  `earlier`, how many messages came before the day and the cursor that reads the newest
  of them. Message ids are strings. It reads comms' record and books on connections that
  can't write and asks the daemon nothing, so it answers with the daemon down; a store
  comms' daemon has not yet moved or migrated answers `state` `error`. The phone's
  gateway does not reach it.
- comms' page server has a new read, `GET /api/mail-state`, from the delivery books and
  the record's last day, asking the daemon nothing: `queued`, each name's waiting and
  in-session rows with the authors of the waiting ones (each `{qualified, is_operator}`,
  `is_operator` true for the operator, by the name `operator` or one the directory gives
  him); `owed`, the verified `[reply-once]` asks to the operator that no later message
  of his answers; `asks`, the open `[reply-once]` asks between agents, with whether the
  awaited name has read it; `last`, each name's newest message; and `operator_names`,
  the names that are the operator (`operator` and each the directory gives him), which
  the page makes no agent row of. An ask is answered by the rule the daemon's
  `awaiting` uses, now one shared function: a later message to the asker threaded to the
  ask, or with any posture but `[no reply]`.
- comms' page server has a new read, `GET /api/queue?name=`: the messages one name has
  a row for in the delivery books' queue, waiting or in its session, oldest first, at
  most 50, each in `/api/messages`' shape with that name's row first; `more`, how many
  rows are left. It asks the daemon nothing.
- comms' page server has a new read, `GET /api/thread?a=&b=&before=&after=&oldest=&limit=`:
  the messages between two names (by one, with a recipient tag naming the other), or
  every message by or to one name without `b`, oldest first, in `/api/messages`' shape:
  the newest `limit` (6 by default, at most 100) below the message id `before`, with
  `earlier`, how many of theirs came before and the oldest one's time, and
  `count_today`. With `after`, the newest message id the page holds, it reads only the
  messages above it and adds `changed`: for the messages from `oldest` (the oldest id
  the page holds; from the first when it is left out) to `after`, the two names' rows
  in the delivery books, or, with one name, every row of that name's messages. The page
  sends `oldest` on every refresh, so a refresh reads only the rows of the messages it
  shows, however large the books grow. `oldest` without `after`, or above it, is
  refused. It asks the daemon nothing.
- comms' page server has two acts, `POST /api/check` and `POST /api/send`, which take
  `{to, text, posture, held, reply_to}` (one address, at most 16,000 characters, a
  posture always given) and run `moxi comms send --check` and `moxi comms send` as the
  operator's lane, with `--new-name`. The operator is `MOXI_COMMS_OPERATOR` in the page
  server's environment, else the directory's one operator; with neither the act refuses
  with `operator-unknown`. Each answers `{outcome, message_id, recipients, refusal}`:
  each recipient's `delivery` and `cold_at`, and the refusal's `code`, never the verb's
  text. Neither is reachable through the phone's gateway.
- comms' page server has a third act, `POST /api/take-back` with `{message_id}`, which
  runs `moxi comms remove` as the operator: `{outcome, withdrawn, delivered, expired,
  refusal}`, the refusal its `code` only; an id that is not all digits is refused with
  `message-id-shape`. It is not reachable through the phone's gateway.
- The connector's status and tools no longer say `offline` while another binding holds
  its identity: the close of a bind the daemon refused could land after the link had
  noted the name held, and turned the state to `offline` until the next try. Only the
  binding's own connection now changes the link's state when it closes.
- Waiting mail is no longer skipped when a Claude session's comms shim reconnects to the
  daemon. The daemon hands mail that waited for the session as soon as it answers the
  reconnect, and a turn ending, the idle tick, or interrupt mail steered into a running
  turn could make the shim inject it before it had switched to the new connection. The
  shim then dropped the message, which did not arrive until the session's next
  reconnect or relaunch while newer mail went through. The shim now holds that mail
  until the new connection is in place, and injects it at the next idle tick or turn
  end.
- A Claude session's comms shim no longer gives up its own name while it reconnects to
  the daemon. When an attempt failed after connecting, that attempt's connection could
  report its close after the next attempt had bound. The shim then said comms was
  offline, tried to bind the name it already held, read the refusal as another session
  holding it, and stayed offline for the rest of the session. A close from a connection
  the shim has already replaced now changes nothing.
- The Comms page says where an agent works when launcher doesn't: with sessions installed,
  a bound name's row reads its session's folder, when it started and its harness
  (`atlas-api · started today 12:45 · claude`), and its opened row's `Session` line names
  the session and links to it on the Sessions page; opening the row asks sessions again,
  so a name typed in the harness since shows there. Launcher's where still wins for a
  session it runs. Each row of the page's roster carries `harness`, the harness its
  binding's adapter kind is a session of, or null.
- The Comms page says which project a name leads: with projects installed, a standing
  name in `Other names` that leads a project reads `leads <key>` on its shown row
  (`leads harbor-infra`), from projects' list of projects. A name that leads nothing, or
  an install without projects, shows nothing of it.

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
