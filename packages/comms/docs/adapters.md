# Adapters

How comms puts mail in front of a model, written from the code in
`src/moxi_comms/adapters/`. The message, routing and the daemon socket are in
[protocol.md](protocol.md); this page is about the other end of that socket.

## What an adapter is

An adapter is a daemon-socket client that binds one name, puts that name's mail in front
of a model at a turn boundary, and reports each turn's end to the daemon, which keeps the
books. One adapter process serves
one name. It registers with the daemon as one of the daemon's adapter **kinds**:

| kind | wrapper | what it serves |
|---|---|---|
| `claude-shim` | `moxi-comms-claude` | a Claude Code session |
| `codex` | `moxi-comms-codex` | a Codex thread, through `codex app-server` |
| `opencode` | `moxi comms opencode` (also `moxi-comms-opencode`) | an OpenCode session, through `opencode serve` (see [OpenCode](#opencode)) |
| `connector` | none: comms' provider runs in the connector | the Claude apps, through the connector; a person kind |
| `speech` | none: voice binds it | a person speaking and listening through voice, which binds the operator's name as it; a person kind |

A live row of `api.roster_rows()` and `agents --json` bound as the `connector` kind
carries `via`, `Claude's apps, through the connector`, while the connector module is
installed; every other row's `via` is null. The Comms page shows it after the name.

`moxi install comms` puts the wrappers in the `moxi` tool's environment, and uv puts none
of them on PATH, so run one by its path: `"$(uv tool dir)/moxi/bin/moxi-comms-codex" <identity>`.
The Claude wrapper also runs as `moxi comms claude`.

A kind decides two things. One is the **wire**: how the adapter talks to its CLI, and so
how mail gets into a turn and how the adapter learns the turn ended. The other is the
**cache clock keyed to the kind's name**: how `moxi comms send` and the daemon read the
recipient's prompt cache, or that the kind has none, and then its mail goes live. Every
kind takes held mail, `--held` included. A new kind brings its own clock, or none.

The four above are comms' own. Another package adds a kind by an entry point in the
group `moxi.comms.kinds`, named by the kind, whose target is a plain table: `{"cache":
"person"}` for a person at the other end (its cache reads `no-cache` and its mail is never
held), or `{"cache": "harness", "harness": "claude", "evidence":
"package.module:function"}` for a session of one of the harnesses comms has a clock for,
with the function that says which session to read. The daemon calls it at each read as
`evidence(qualified, session_id)`; it answers `(session_id, new_session)` or
`"unreadable"`, and comms hands that to the cache module's read, so the kind decides no
verdict itself (`moxi_comms/kinds.py` states the contract). comms declares its own four
the same way. The daemon reads the group when it starts and refuses a register of any other
kind; a declaration that does not hold is left out and logged
(`moxi_comms/kinds.py`). An adapter built outside comms uses `moxi_comms.adapter`: the
daemon client, the backoff, the digest and frame line, the attribution tiers
and the machine line's parse.

## What a harness is

A harness is a terminal session a person can sit in, which comms reaches as one of its
agents. Four things make it one:

- **A person can sit in it.** It runs in a terminal window, and the person types into it
  as they would into any session of that CLI.
- **Mail arrives as model turns in that same live session**, idle or busy, by a way in
  of its own. Comms never types into the person's input box, so what they type and what
  another agent sends never interrupt each other.
- **A digest is acked only on the CLI's own evidence** that the turn carrying it
  completed: an in-band completion event, never a guess from output or timing.
- **The agent sends as its identity** from that session: its shell inherits the
  session's claim socket (`MOXI_COMMS_CLAIM_SOCKET`), and `moxi comms send` and `moxi comms reply` read
  the identity from it. A process outside the session, or one with only the launch
  stamp, sends under no identity.

A CLI that can't take mail into a live session, or can't be sat in, isn't a harness.
Running it once per message with no session anyone sits in is headless automation, not
a harness.

## The rules every adapter keeps

1. **Its connection is its liveness.** An adapter is an ordinary client of the daemon
   socket. When its process dies the connection closes and the daemon unbinds the name.
   The daemon spawns and supervises nothing.
2. **A roster names the agents a wrapper runs.** `moxi-comms-codex` and `moxi-comms-opencode`
   read theirs from the `[codex]` and `[opencode]` tables of comms' config file: a
   closed key set, and a key that looks like a token is
   refused. `--config PATH` names another comms config file, checked whole, whose table
   that one launch reads as its roster instead (a wrapper checks a candidate file with
   `--config PATH --dry-run` before it replaces the box's); every other setting still
   comes from the box's file. A register never starts a process.
3. **One environment stamp.** The agent's process gets its stamp from
   `stamped_env`, which scrubs every comms variable and sets them again, so authority
   can't leak in from whoever started the wrapper. The adapter is its session's claim
   holder and stamps its claim socket into everything it spawns; that socket, not the
   stamp, is what a send speaks as.
4. **A live binding token.** Registration mints a fresh token for the binding. The
   adapter keeps it in memory outside the claim record and hands it to the daemon only
   through the claim socket inside its session. A send needs that live binding, so a
   session whose adapter is not connected cannot send. A daemon restart invalidates
   the old token; the adapter rebinds with backoff, up to 30 seconds plus 20% jitter,
   and sends wait for that rebind. An adapter whose comms wire version the daemon doesn't
   speak is refused at its hello, and the session must be relaunched.
5. **One renderer.** Every adapter renders the digest with `render_digest`, so every
   model reads the same frame lines. A frame line ends with the message's marks, each
   only when present: the thread mark (`[1 reply left]` or `[last reply]`) and the posture (`[no reply]`, `[reply-optional]`, `[reply-once]`, or `[open]` on the
   message that opens a thread with limits). An open message with no posture token shows
   none. Layer one, the block a session is shown at start, is `context.layer_one_block`
   for every harness: the context block, with the director sentence after it only for
   an agent with a declared director (`--director` for Claude, the roster's `directors`
   for Codex and OpenCode). After the block comes one line, the same on every harness,
   naming which comms to run by absolute path: the launching install's own `moxi`
   script, since the agent's shell may find another install's `moxi` first. The Codex
   and OpenCode wrappers write it into layer one themselves; the Claude wrapper stamps
   it into the session's environment as `MOXI_COMMS_COMMAND`, and comms' listener writes
   the line from it (from its own install for a session an older wrapper started).
   With no `moxi` script to name, every harness's line names the bare `moxi comms`.
   Codex's thread record hashes the director sentence and that line with the rest, so a
   thread whose director changes is handed the new layer one.
6. **One flush path.** Nothing is delivered while catch-up is still arriving. A backlog
   coalesces into one digest, sorted by message id; live mail arrives one message at a
   time and takes the same path.
   - **When a batch goes back.** The flush puts a batch back on the queue, ahead of
     anything newer and with its catch-up notice, only when the adapter's delivery
     reports that nothing reached a live turn, so that trying again at once can't put
     the same digest into a turn it already reached. Any other outcome is a delivery:
     the digest is recorded, and whether it is acked is decided by the completion
     evidence. An error the adapter didn't expect drops the batch, which
     replays when the name next binds.
   - **The wait before a retry.** Every batch that goes back waits before the next try,
     one second by default, so an adapter whose delivery keeps failing doesn't spin.
7. **The adapter reports completion; the daemon keeps the books.** The adapter opens each
   digest with the daemon (`open_digest`), hands it to turns with the digest's id as its
   ref, and reports each turn's end (`turn_ended`) with the digests it handled and the
   ones it didn't. The daemon advances the cursor and deletes the handled rows in one
   transaction. A digest whose turn can't be identified is reported unhandled and
   replays. Replay is the safe direction; a cursor that moved too far is not. No adapter
   writes comms state of its own but its thread or session ids.
8. **A recorded session is resumed, never replaced.** Where an adapter resumes a session
   (Codex and OpenCode), the session or thread id lives in STATE, written by
   the adapter. A resume that fails is said out loud and retried; the
   adapter never starts a fresh session under the same name, because that would be an
   agent with no memory answering as the old one.

   *Codex and OpenCode:* a Codex or OpenCode launch starts a new
   thread or session, as a Claude launch starts a new session, unless it is told to
   resume one: `moxi comms codex --name <name> --resume THREAD_ID` resumes that Codex
   thread and reports the launch as a resume of it. No launch resumes or loads an id it
   reads from the record. A launch is one `moxi-comms-codex` or
   `moxi-comms-opencode` process, and no code path in it reads an id from the record that it
   did not itself start or was not told to resume. The rule above holds inside one launch: when the launch's own
   thread or session fails to resume or is gone, that is said out loud and never
   replaced. Codex retries the resume; OpenCode stops posting for the rest of the
   launch.
9. **Supervision retries.** A lost daemon connection, or a lost CLI server, is retried
   with backoff rather than ending the process, since an adapter that exits leaves its
   name silent. An unexpected error is logged with its traceback, said once per
   exception type, and retried. A daemon refusal of the name at start is fatal.
   `moxi-comms-opencode` gives up and exits 3 if its session is never ready to show.
10. **The daemon hears about deliveries.** The adapter reports `injected`, with the
   digest's text, and `activity`, with the digest's id when a turn takes it, so
   `moxi comms explain` works for every kind.

## The base

`codex` and `opencode` share one class, `HarnessAdapter` in
`adapters/harness.py`. It owns
the daemon binding, the queue and the catch-up hold, the flush path (which opens each
digest with the daemon before it renders it), the turn-end report, the daemon side of
supervision and the notifications. An adapter for a CLI is a subclass, its **port**,
which fills in these hooks:

| hook | what the port says | default |
|---|---|---|
| `KIND` | the kind it registers as | |
| `deliver(text, high, identity, count)` | puts the digest in front of the model; returns `REQUEUE` or a receipt | required |
| `delivered(..., receipt)` | what to do after the base recorded a delivered digest | nothing |
| `WAIT_WHEN_HELD` | wait for a name another binding holds, rather than exit refused | true |
| `retry_delay()` | seconds to wait after a `REQUEUE`, read each time | the base module's `FLUSH_RETRY`, 1.0 |
| `can_inject()` | whether a digest can go in now, such as "the CLI server is connected" | true |
| `start_port()` | brings the CLI side up after the bind | nothing |
| `supervise_port()` | one supervision pass on the CLI side; false if it already waited its own backoff | true |
| `stop_before_unbind()`, `stop_after_unbind()` | what `stop()` closes around closing the daemon connection | nothing |
| `ONE_AT_A_TIME` | hold one delivery in flight per name | false |
| `QUIET_OFFLINE_ON_STOP`, `STATE_NOUN`, `registered_detail()` | wording of the offline line, the bind error and the "registered as" log line | |

The flush path, in order:

1. Under the lock, return if catch-up is still arriving, the queue is empty,
   `can_inject()` is false, or the port holds one delivery at a time and one is running.
   Otherwise take the queue sorted by message id, and the catch-up notice.
2. Open a digest with the daemon for those ids, keep the ids its answer keeps (a
   withdrawn or expired one is dropped), render the digest and call `deliver`.
3. On `REQUEUE`, put the batch back ahead of the queue with its notice, wait
   `retry_delay()`, and wake the flush loop.
4. On any other value, record the digest and call `delivered` with the receipt. The base never
   reads a receipt's truth: `None` is a delivery too.

Each port maps its own outcomes to `REQUEUE`:

- **Codex** never returns `REQUEUE`. Its receipt is the digest's id, which is also the
  `ref` it hands turns, and the digest settles from turns' own outcome: at the end of
  the turn that names the ref, or at once as `unhandled` when turns refuses it or says it
  is lost, so the daemon hands it again. A hand-in whose answer never came is waited
  on, not put back: the text may already be in a turn.
- **OpenCode** returns `REQUEUE` when nothing was admitted: the status read before the
  post doesn't say idle, the post can't connect, or the server answers it with a 5xx, a
  429 or any refusal. Its receipt is the message id comms chose, also when a post got no
  answer at all, since the server may have admitted it; the session's message list then
  says whether it did.

## Adding a harness

A harness is a port on the base, in a kind of its own, plus the cache clock for that
kind's name, if it has one. Every kind's cache is checked: the daemon delivers nothing
live to a binding whose cache reads cold, and holds that mail for its next session
start, /clear, compaction or turn. A kind comms has no cache clock for reads
`no-cache`, and its mail goes live. Before writing
one, answer these for your CLI:

1. **Does the CLI's shell inherit the environment it was launched with, and can it reach
   a Unix socket from inside its sandbox?** If not, the agent can't speak as its name or
   send, and there is no adapter.
2. **Can something other than the person's keyboard put text into the running,
   interactive session as a model turn, an idle session included, and does the CLI then
   say, in its own events, that the turn completed?** Typing into the terminal doesn't
   count. If not, it isn't a harness.
3. **Can a person sit in the session that receives the mail?** If not, it isn't a
   harness.
4. **Does it have a place for comms' instructions at session start?** If not, they go in
   a first turn, as Codex's do.
5. **How does it load a recorded session, and does that carry the same settings the
   session started with?**

Then write down which of the port's failures return `REQUEUE`, against the rule in
"When a batch goes back", give `retry_delay()` a positive answer, and, if the CLI runs a
server, make `can_inject()` false while it is down so the flush doesn't fail and wait in
a loop while supervision brings it back.

## What each harness supports

| harness | kind | how a person sits in the session | what ends a turn | status |
|---|---|---|---|---|
| Claude Code | `claude-shim` | `moxi comms claude --name <name>`: the session is the terminal it runs in; the wrapper forks the session's adapter process | the turn's end as Moxi turns reports it: a digest is handled when its turn ends with an answer, and handed again when it was interrupted (Esc), ended in an error or refusal, refused, or lost | supported through Moxi turns; the session must load turns' Claude plugin |
| Codex | `codex` | `moxi comms codex --name <name>` runs Moxi turns' Codex host in the terminal, with its window, on a new thread for each launch, or on the thread `--resume THREAD_ID` names | the turn's end as the host reports it: handled on an answer, handed again otherwise | experimental; through Moxi turns |
| OpenCode | `opencode` | `moxi comms opencode <identity>` opens `opencode attach` on a new session for each launch | the first idle after the model saw the digest, when the turn ended with `finish=stop` | experimental |

comms reads a cache clock for both. A Codex thread's clock is the end of its last
request in its rollout, under the sessions root its wrapper reports; an OpenCode
session's is the start of its last request in `opencode.db`, at the path the wrapper
asks OpenCode for. Each counts the lifetime its harness, provider and model have in the
cache module's lifetime table (comms reads both clocks through the cache module; without
it every agent's mail goes live); a model or provider with no
row has no clock to count by, and its mail goes live. A message to a cold one is posted
and held, as for a cold Claude session. A Codex agent is reached while its cache counts
warm: 30 minutes after its last request on `gpt-5.6-terra` and `gpt-5.6-luna`, and on
the models a longer probe measured, the measured number, lowered by the daemon when it
sees a cache go cold sooner. An OpenCode agent whose next request runs on one of Zen's
free models is reached at any age. A model change reads cold until the next request on
the new model, so mail to it is held. A send to anything comms cannot read is refused
(exit 6, nothing posted, "Nothing was sent." first and last): a wrapper built before
these reports, a reverted or forked Codex thread, an OpenCode session with a revert
pending, and a rollout or row comms does not recognise.

## A launch binds before its harness starts

The Claude and Codex wrappers start no harness under a name they don't hold. Each forks
its session's adapter, which binds the name with the daemon and listens on the claim
socket, and waits for the adapter's word before it starts `claude` or turns' Codex host.
Both wait at most 15 seconds. After 2 seconds with no word the wrapper prints one line,
`moxi-comms-claude: waiting for the comms daemon to bind <name> (up to 15s)` (or
`moxi-comms-codex: ...`). A bind the daemon doesn't answer in the 15 seconds ends the
launch: the wrapper prints `<name> was not launched: the comms daemon did not answer in
15s; try again, or run moxi comms doctor` with the adapter's log, exits 75, and the
harness never starts. Neither wrapper starts its harness anyway once the wait runs out.

The adapter that bound late has nobody to tell. Its word to the wrapper can't be
delivered, so it lets the name go (the daemon unbinds it and its claim file goes) and
ends, before it delivers anything; an adapter whose wrapper has already ended when it
starts never binds. A launcher reads which harnesses' wrappers end this way, and the
longest of their waits, from comms' API: `ENDS_ON_BIND_TIMEOUT` (`{"claude", "codex"}`)
and `BIND_WAIT` (15 seconds). The OpenCode wrapper isn't one of them: it binds in its
own process before it starts OpenCode's server, so it waits for no adapter's word.

A Claude Code session's adapter that dies while `claude` runs is replaced with no
relaunch. Its claim file stays, with a socket that answers no one under a live `claude`.
At the session's next start or prompt, comms' listener sees it and starts a new adapter
for the session. The new adapter takes a handle on the same `claude` (the claim file's
root, its pid checked against the start time recorded there), reads the claim file, binds
the same name and serves the same claim socket, with the tool token the session's
environment carries. It finds the session's endpoint as any adapter does, and ends with
`claude`. The listener waits for its word within its 1.5-second bound and gives the
session one line, `comms: this session's comms adapter had stopped. A new one has bound
<name> again`, before the start's layer one and roster or the prompt's roster changes,
which the session gets as it would with its adapter alive. A daemon that is not running
is no failure: the new adapter binds when it returns. Mail sent while the session was
unbound waits at the daemon and is delivered once the name is bound again.

When no new adapter can be started (the claim file's `claude` can't be found, or the
system has no process handle to watch it with) or the new one's bind is refused (another
session took the name meanwhile), the claim file and the session's roster memory stay,
and the line is `comms: mail has stopped for <name>`, with why: no message reaches the
session and a send from it is refused. The next start or prompt tries again; a relaunch
gets the mail back for good.
Until an adapter binds it, `moxi comms doctor` prints an `adapter` line for the session
and `moxi doctor` a fail row `adapter: <name>`.

## Codex

**Through turns' host.** `moxi comms codex` (also `moxi-comms-codex`) starts a Codex
session through Moxi turns' Codex host, `moxi-turns-codex-host`, the turns command that
runs `codex app-server` and its hooks and keeps the session's endpoint. Install it with
`moxi install comms --with codex`.

```text
moxi comms codex --name NAME [--scope SCOPE | --global] [--director NAME]...
                 [--model M] [--sandbox MODE | --permissions PROFILE]
                 [--option KEY=VALUE]... [--options-file F]
                 [--no-window] [--dry-run]

moxi-comms-codex IDENTITY [--config PATH] [--window] [--dry-run]
```

The settings flags are the host's own, so a launch reads the same with or without
comms. The second form reads the row IDENTITY names in the config file's `[codex]` table
(or in the file `--config` names, checked whole) and turns it into the same flags; it
runs with no window unless it passes `--window`. The first form runs in the current
directory, with the host's window unless it passes `--no-window`.

The wrapper checks the whole host line with turns' own parser before anything starts,
binds the name, forks the session's adapter, writes the comms instructions to a 0600
file in the session's runtime directory, and execs the host with them, so the host is
the session's process in the terminal. To every launch comms adds multi-agent off
(`--option agents.enabled=false`); with no sandbox or permission profile named,
`--sandbox workspace-write`; and on a sandbox, network access unless the launch sets it
(the agent's shell needs it to reach the daemon socket) and comms' state directory in
the writable roots. The host lays the settings on the thread's start and
every resume, and refuses to deliver into a load whose sandbox did not take; `moxi
doctor` shows why, in turns' rows. `--dry-run` prints the host line and starts nothing.

A bad flag, row or config file exits 2, a name the daemon refuses (held, reserved,
retired) exits 3 with the daemon's words, and a daemon that isn't running, or a bind
it doesn't answer in 15 seconds, exits 75, all before Codex starts. A host that can't start exits 1. Once the host runs, its exit
status is the launch's.

**How mail arrives.** Mail goes in at turn boundaries: while no turn runs, the adapter
hands turns one digest with `deliver`, which starts a turn. Mail that arrives while a
turn runs, yours or the one a digest started, waits for that turn's end and goes in as
one digest then. Mail sent with `--interrupt` goes into the running turn at once, by
turns' `steer`, with `[interrupt]` on its frame line so the model knows why it came
mid-turn; if the turn ended in between, it goes by `deliver` instead, once. Each digest
is acked when the turn that took it ends with an answer, and handed again otherwise. A
hand-in whose answer never came is waited on, not handed again: the text may already be
in a turn.

**A headless session.** `--headless` runs the session with no window and nobody in it:

```text
moxi comms codex --name NAME [--scope SCOPE | --global] [--director NAME]...
                 [settings flags] --headless [--exit WHEN]...
                 (--brief TEXT | --brief-file F) [--dry-run]
moxi comms exit-session NAME [--scope SCOPE | --global] [--wait-s 30]
```

The host line is the `--no-window` one. The wrapper writes the brief to `brief.md` (0600)
in the session's runtime directory, and the adapter hands it in as the session's first
turn, before any mail; mail waits for that turn's end. From then on mail starts turns as
in a session nobody types into. The claim file records `headless: true` and `exit`, the
rule as its `--exit` values.

`WHEN` is `done`, `cold`, `idle=<D>` or `after=<D>`, D a whole number with its unit (`s`,
`m`, `h`); with none the rule is `done`. The first to fire ends the session:

- `done`: the brief's turn ended with an answer, no turn runs, and once the daemon's
  settle window after the last turn has closed, the adapter's `look` (a release point)
  and its read of the daemon's `agents` answer show nothing handed to it and unacked.
  Rows held for a cold or unreadable cache don't hold it: they wait for the name's next
  holder, and the end line counts them.
- `cold`: while no turn runs, the adapter reads its own name's cache from the daemon
  every 60 seconds; a `cold` read ends it, and so does a cache read `unknown` for five
  minutes with no turn between (cause `cache-unreadable`). A cache that reads `no-cache`
  never goes cold; the adapter says so once.
- `idle=<D>`: no turn has run for D since the last turn ended.
- `after=<D>`: D since the bind, a running turn included.

`done`, `cold` and `idle=` count only once the brief's turn has ended with an answer. A
brief turns refuses or loses, or whose turn ends `error`, `aborted` or `refusal`, ends the
session (cause `brief-failed`), and so does a session the adapter has not found within
300 seconds of the bind (a Codex start that keeps retrying, say), or whose brief's turn
has not started within 300 seconds of its being found. A turn resets `idle=`.
Before `done`, `cold`, `idle=` or a failed brief turn ends an idle session, the adapter
asks the host for its background work (`agents`) and waits while any runs, up to an hour
after the last turn. `after=` and `exit-session` wait for nothing.

The end is one line in the adapter's log, `headless session ended: <cause>` with the rows
left waiting for the name, and SIGTERM to the host through its pidfd. The adapter then
releases the name as on any host's end. The host holds a lifeline whose other end only
the adapter holds (`MOXI_LIFELINE_FD`), so a host whose adapter died by any death stops
too. `moxi comms exit-session NAME` asks the adapter's claim socket to `end` the session
(any local caller may; an adapter whose claim is not headless refuses) and waits up to
`--wait-s` for the name to be released. An `end` that reaches the adapter before its bind
is answered is kept, and the session ends on it as soon as the name is bound. While a
second launch of the name is still binding, two claim files hold it; the verb asks the
`bound` one. It exits 0 once the name is released, 1 when the socket didn't answer or
the wait ran out, 3 when nobody holds the name or its session is not headless.

`--headless` is refused with `--window`, `--no-window`, `--resume` and in the roster
form; `--exit`, `--brief` and `--brief-file` are refused without it; so are a value
outside the rule, a value given twice, and `cold` without the cache module. Each exits 2
before anything starts. `--dry-run` prints the rule and the brief's file with the host's
line.

**One thread.** The adapter delivers into the thread this launch started or resumed. /clear, /new
and /fork in the window start another thread, and /resume opens one; comms does not
follow any of them, and mail still goes to the launch's thread. To start fresh, end the
launch and launch again.

## OpenCode

`moxi comms opencode <identity>` (also `"$(uv tool dir)/moxi/bin/moxi-comms-opencode" <identity>`) runs an [OpenCode](https://opencode.ai) session you sit in,
in the terminal you start it from. It is experimental. It was built and tested on
OpenCode 1.18.34, with OpenCode Zen's free models (no account and no key) and with a
local model through Ollama.

```toml
# CONFIG/comms/config.toml
[opencode.agents."coder"]
cwd       = "/home/me/src/my-project"    # best at a git root
directors = ["main"]
# model   = "opencode/minimax-m3.1-flash-free"  # the launch default; a list adds
#                                               # fallbacks; none: the shipped list
# binary  = "/home/me/.opencode/bin/opencode"
```

**What runs.** The wrapper registers the identity, then starts one
`opencode serve --hostname 127.0.0.1 --port 0` for it, with the identity and the wrapper's claim socket stamped into
the server's environment. OpenCode runs a session's tools in the server's own process,
so that is what the agent's shell has, and `moxi comms send` from there sends as the
identity through the claim socket. One server per identity, never shared. The server gets a password made for
this launch (`OPENCODE_SERVER_PASSWORD`), which the wrapper holds; the password keeps
other users and web pages out, not other processes of your own user, which can read the
server's environment. The wrapper creates a new session for each launch and keeps it
while it runs; if that session is gone during the launch, it says so and posts nothing
more. It opens `opencode attach <url> -s <session>` in your terminal. Closing the window
stops the wrapper and takes the identity offline. If the server dies, the window closes and reopens on the server the
wrapper starts in its place.

**How mail arrives.** The wrapper posts each digest to the server
(`POST /session/:id/prompt_async`) under a message id it chooses. The window is a client
of the same server and shows the turn as it runs. Nothing is typed into the window, so
what you have half-typed in its input box stays as it is. The wrapper posts only into
an idle session: it reads the session's status immediately before each post, and after
its own post it waits for that turn to end before the next. Mail that arrives meanwhile
goes as the next digest. Mail sent with `--interrupt` waits for idle too, and
`moxi comms agents --seats` says so on an OpenCode name's row: `interrupt delivers at
idle`.

**What it can't prevent.** OpenCode's session has one loop. A prompt that arrives while
a turn runs is taken at that turn's next step, and the running turn's own reply is never
written.

- If you press Enter in the few milliseconds between the wrapper's status read and the
  server admitting its post, the digest lands in your turn, and the reply you asked for
  may not be written. On loopback that window is a few milliseconds, plus the few
  milliseconds between your Enter and the server marking the session busy. This is the
  harness's remaining risk.
- If you submit a prompt while a digest's turn runs, your prompt takes that turn over:
  the model answers you, and the digest's own reply may not be written. The digest still
  counts as delivered, since the model saw it.

**When a digest is acked.** On the first idle after an assistant message answering the
digest appears, when the turn ended with `finish=stop`. A turn that ends by an abort or
an error is reported unhandled, and the digest is handed again.

**Failures.** A post that can't connect, a busy session, a 5xx or a 429 is tried again
after a second, and a run of five or more is said once in the status line. Any other
refusal (a 400, a wrong password, a session that is gone) stops mail into the session for
the rest of the launch and is said once; the undelivered messages come again at the next
launch. An event stream that closes, or sends nothing for 30 seconds (OpenCode sends a
heartbeat every 10), is reopened, and the session's messages are read again before any
more mail goes in.

**Your configuration.** The session loads your own OpenCode setup, as it does when you
run OpenCode yourself: your OpenCode config and sign-in, your `~/.claude/CLAUDE.md`, and
the skills under `~/.claude/skills` and `~/.agents/skills`. With no git root above the
roster's `cwd`, OpenCode treats a `.opencode/` directory in a parent as project config
and writes into it. `scripts/opencode-home-check.py` shows what a session would load
from your HOME, without running a model.

**The model.** The launch sets OpenCode's `model` and `small_model` to the first model of
the roster's `model` that the server offers free. A row that names no model gets comms'
shipped list, in order: `opencode/minimax-m3.1-flash-free`, `opencode/glm-5.3-flash-free`
and `opencode/deepseek-v4.1-flash-free`, the free OpenCode Zen models that keep no data
and train on none by [Zen's terms](https://opencode.ai/docs/zen/) as read on 2026-10-20.
MiniMax M3.1 Flash Free is free there for a limited time; when it is retired, a launch
takes the next. If the server offers none of the row's models, the launch uses the first
free Zen model offered. A launch on any model but the row's first says which before the
window opens, in the wrapper's log and in the identity's status line, and says whether
it is one of the shipped zero-retention defaults or a model that may keep or train on
what the session sends it. If no free model is offered, it sets none, and OpenCode uses
its own default, which may be a paid model if your own credentials allow one. comms never
names a paid model and adds no key, credential or provider of its own, and after the
launch it never changes the model: its posts name none, so a post runs on whatever model
the session uses. A model you pick in the window is yours, and the session keeps it, also
in later launches, since a session runs on the model it last used; it may keep or train
on what the session sends it, your `CLAUDE.md` and skill descriptions included. The
launch default applies to new sessions and to OpenCode's side requests, such as a new
session's title. A request that runs on any model but the launch default is written to
the wrapper's log and its status line, and nothing more is done.

**The model list.** OpenCode keeps the list of models it offers in
`~/.cache/opencode/models.json` (under `$XDG_CACHE_HOME` when that is set) and refreshes
it in the background, so a server that has just started can still read a list from
before a model was retired, and choose it. Before it starts the server, the wrapper
therefore runs `opencode models --refresh` under the server's environment, which fetches
the list and writes it at once (about 1.5 s and a 6 MB download), and chooses the model
from what the new server then offers. It gives the refresh 20 seconds. A refresh that
fails or takes longer leaves the list as it stands, and the launch says so before the
window opens, with how long ago the list was written. The file it refreshes is your
own OpenCode model cache, which OpenCode rewrites itself every hour. `--dry-run` runs no
refresh and says that a launch will. Two gaps remain: OpenCode's published list can lag
Zen, so a model Zen has retired and the list does not yet mark is still chosen and
fails at its first request; and a server the wrapper did not start keeps the list it
read when it started.

**The status line.** While the window covers the terminal, the wrapper's status lines
go to its log under `STATE/comms/opencode/` and to the identity's session file there.
`moxi comms explain <identity>` prints the newest ones, and when the window closes the
wrapper prints its model notes and any refusal again.

**Not contained.** OpenCode runs shell commands with no sandbox, as your user. The
launch config allows file tools outside the project (the shell reaches those files
anyway) and denies a tool call that keeps repeating itself (OpenCode's `doom_loop`
ask), so a session nobody is watching never waits on a question. Don't describe an OpenCode agent as contained.

**One session.** The wrapper reads and posts into the session this launch created only.
If you open another session in the window (/sessions), mail still goes to the launch's
session, and you see it when you switch back. /new and /clear are followed: the server
hears of the new session when you submit its first prompt, and from then on mail goes
there, starting with any digest still pending in the old one. Until that first prompt
the wrapper cannot know, and mail still goes to the old session.

## Send-only names

A send-only name is one a program sends under and nobody binds or mails. It comes in two
kinds, which share comms' `reserved` table and the daemon's checks:

- **A module's name.** A module declares the names it sends under in its announcement's
  `RESERVED_NAMES`, each bare and global (`console`). comms reserves each one when the
  module installs, with a token in the credential chain for that module, and frees it
  when the module goes. The module sends with `moxi_comms.api.send_as(module, name, to,
  text)` from its own code.
- **A name a program registers.** Any program or service, a Moxi module or not, can
  register a bare name once and then send under it:

  ```sh
  moxi comms send-only add notifier
  moxi comms send --as notifier main@proj-a "the nightly backup is done" --posture none
  ```

  From Python, `moxi_comms.api.send_under(name, to, text)` sends the same way. `add`
  mints a token into the first writable step of this process's credential chain and
  works with the daemon down. Run it, and `send --as`, from the environment the program
  sends from, so the token lands in a step that environment reads. Running `add` again
  changes nothing, so a program may run it at every start: it exits `0`, or `REFUSED`
  (`3`) with a line naming `moxi comms send-only renew NAME` when this process can't read
  the name's token back.

The three verbs for a program's name:

| verb | what it does |
|---|---|
| `moxi comms send-only add NAME [--take]` | register NAME; adopts a name an older comms kept |
| `moxi comms send-only renew NAME` | remove the old token from every step of this process's chain that holds it, then mint a new one; the old one is refused from then on |
| `moxi comms send-only remove NAME` | free NAME: it is an ordinary name again, and a session may bind it as someone new |

From inside a session, or from a process comms can't place, only plain `add` of a name
nobody uses goes ahead. `add --take`, `renew` and `remove` are refused there before
anything is touched, with a line saying to run them from your own shell: a confused agent
can't drop another's mail, take a program's token or free its name.

`renew` and `remove` refuse a module's name and say what does it instead (`moxi comms
reserved renew NAME`, or the module's uninstall), and `moxi comms reserved renew`
refuses a program's name. When `add` or `renew` can't read the new token back because an
earlier step holds an older copy (an environment variable such as
`MOXI_COMMS_SECRET_COMMS_SEND_ONLY_NOTIFIER`, or a stale keychain entry), it says which
step and exits `REFUSED`: remove that copy there. A step it couldn't reach is named too.

`add` checks, in order, and refuses with nothing written:

1. a name comms can't register: a scoped name (`notifier@proj`), a name that isn't a comms
   name, an `agent-N` name, `operator` or `auto`;
2. a name a module holds, active or in conflict (a name a program already holds is the
   no-op above);
3. a retired name: it means nobody, and sending under it would bring it back;
4. a standing identity, or a name a live session holds;
5. without `--take`, a name a session has held before, or one with mail waiting (the
   refusal says how many messages). With `--take` it goes ahead, and that mail is
   dropped.

A name an older comms kept as send-only (`send-only, nothing holds it`) goes to whoever
claims it first: a module that declares it, or `send-only add`, which needs no `--take`
for it. `send-only remove` can free one. When a module declares a name a program
registered, the name stays the program's: the module's install says so, and `moxi doctor`
warns each run until one of them lets it go.

`send --as` sends with posture `none`, leaving `--posture` out means `none`, and any
other posture is refused, since nobody can reply. It takes `--expires`, `--interrupt` and
`--check`; every other send flag is a usage error. It refuses a module's name
(`module-reserved`), a name no program registered (`not-registered`), and a process
inside a session (`name-reserved`). `api.send_under` gives the same `refusal.code`
values, and `api.send_as` refuses a name a program registered (`not-reserved`). There
is no `reply --as`.

No session binds a send-only name, of any adapter kind, whichever kind it is. A
session's binding token never sends under one, and the daemon refuses a send under one
from any process inside a live session, or one whose ancestry it can't read: a module or
program sends from outside every session. A send-only name an older comms kept has no
token; nothing sends under it until a module declares it or a program registers it.

`moxi comms agents` lists each send-only name after the bindings, as
`console                  send-only, reserved for launcher`,
`notifier                 send-only, registered` for a program's, or `send-only, nothing
holds it` for one an older comms kept. `agents --json`, `api.roster().standing` and
`api.roster_rows()` carry `holder` on each: `module`, `program`, or null. A message sent
under one is marked `(send-only)` on its frame line. A name declared but in conflict
isn't reserved, so it isn't listed; `moxi doctor` reports it.

Nothing is delivered to a send-only name. `moxi comms send` and `moxi comms reply` refuse
it as a recipient before anything is posted, and the daemon refuses a `send` whose line
zero names one (`not-mailable`); a record line that names it anyway is delivered to
nobody, pending for nobody and held for nobody, and opens no ask for it. A scoped name
(`console@acme`) is not the send-only one: it is an ordinary name.

Mail that was already waiting for a name when a module reserved it, or a program
registered it, is dropped undelivered, and stays dropped if the name is freed later: a
later holder of a freed name is someone else. Its sender is not told. `moxi comms explain
<name> <id>` says `reserved`, and `moxi comms history` marks it `[dropped for <name>:
reserved]`. The daemon drops it at its start, at the name's next release point, and when
`moxi comms agents` or `explain` asks, so a name another process reserved or registered
takes effect at the next of those. A name in conflict isn't reserved, and keeps its mail.
