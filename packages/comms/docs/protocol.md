# Protocol

What Moxi comms implements, written from the running code: the message, how it is
routed and delivered, the rules every delivery adapter follows, and where the files
live. Numbers named here are in `src/moxi_comms/constants.py`.

## The pieces

- **The store**, `STATE/comms/comms.db`, one SQLite file in WAL mode (0600), holds the
  record of every message, the rooms, each name's waiting mail and digests, each name's
  cursor, the binding history, the identities and the operator, and the reserved names.
  `local` is the supported backend. Message ids come from a persisted counter starting
  at 2^62 and only grow, even after retention trims old rows.
- **The daemon**, `moxi-comms-daemon`, is the one writer of messages and delivery
  state. Every send goes through it, the operator's included; it works out who each
  message is for, and hands it to the adapter bound to that name. Which names are bound
  lives in memory; restarting the daemon rebuilds it from the adapters that reconnect,
  and what each name is owed stays in the store.
- **Delivery adapters** connect to the daemon, bind a name, and put mail in front of a
  model at a turn boundary: `claude-shim` (a Claude Code session), `codex` (a Codex
  app-server) and `opencode` (an OpenCode server). The kind `connector` is comms'
  provider in the connector, which serves the Claude apps; it is a person kind, so its
  mail is never held.
  A reserved name is one a module sends under and nobody binds or mails
  ([adapters.md](adapters.md#reserved-send-only-names)).
- **The CLI**, `moxi comms`, sends and reads through the daemon. With the daemon down
  nothing is sent: a send exits 75 and says so.

There is one room, `#names`, where every agent and the operator post addressed mail.

## Identities

An identity is a name and a scope: `planner@proj-a`, or a global `planner`. The tag
alone decides which identity it names, typed or read back from the record: a bare
`planner` is the global identity everywhere (`planner@global` is the same identity,
spelled bare), `planner@proj-a` is planner in project proj-a, and an `agent-N` is that
ephemeral name. No reader consults the scope of the sender, of a message's author, of
the reader or of the working directory. A project scope is for one thing: keeping apart
two agents of one name in different projects. A session that names no scope binds the
global identity. A send to an identity the binding history (below) has never seen bound,
with no standing row, is refused unless it passes `--new-name`; otherwise mail to an
identity that isn't bound waits for it. The binding history decides only that: it never changes
which identity a tag names. A retired identity is refused.

Tiers:

- **chosen**: a name a session claims at launch. Its adapter holds the binding token
  while connected.
- **standing**: provisioned as a durable identity (`"$(uv tool dir)/moxi/bin/moxi-comms-provision" --standing`).
- **ephemeral**: an `agent-N` name the daemon mints for a session that claimed none.
- **operator**: the human, whose lane reads the operator's credential through the
  configured resolver chain.

## Addressability

A name is addressable while a delivery adapter bound to it is connected to the daemon.
The connection is the liveness signal, and closing it is the deregistration. Mail to a
name that isn't bound waits in its queue and is handed over when it binds (see "Held
mail"). Latency, never loss.

## The machine line

Routing rides one line at position zero of the message body, and nowhere else:

```
::to planner, reviewer@proj-b re:4611686018427387905 [no reply]
The parser change landed; the suite is green.
```

```
::to <tag>[, <tag>]* [re:<id>] [audience:<tag>] [held | cold-at:<v>[,<v>]*]
     [interrupt] [expires:<t>] [cap:<n> | no-cap] [1 reply left | last reply]
     [no reply | reply-optional | reply-once | open]
```

- `::to` and a comma-separated list of `name` or `name@scope` tags.
- `re:<id>`: the message this one answers. Correlation lives in the record's own text.
- `[audience:<tag>]`: who the answer is for.
- `[held]`: hold this until the recipient's own next turn, or until its session
  starts, is cleared or is compacted (see "Held mail"). `moxi comms send --held` and `moxi comms
  reply --held` write it. The older spelling `[next-turn]` still parses as `[held]`.
- `[cold-at:<v>,...]`: the send's reading of each recipient's prompt cache, one value
  per tag in tag order: the Unix second at which that recipient's cache goes cold, or
  `-` for a read that delivers live (`fresh` or `no-cache`). The verb writes it on
  every line zero it posts that does not carry `[held]`, and never beside `[held]`. A
  value count unlike the tag count makes the line prose. The daemon reads it only for a
  recipient whose cache it reads `unknown` (see "Held mail"); the payload does not carry
  it and `moxi comms history` does not print it.
- `[interrupt]`: the sender asks that the message go into a running turn where the
  recipient's harness can take it there, rather than wait for the turn's end. It doesn't
  get past the cache hold: a held recipient gets it at a release point like any other
  mail. `--interrupt` writes it; with `--held` it is a usage error.
- `[expires:<t>]`: a Unix second after which the message is never handed to a model. A
  row still waiting then is dropped, and the sender is told nothing; `moxi comms explain`
  says `expired`. A line without it never expires. The verb writes it from `--expires`
  (`30m`, `6h`, `3d`, or `never` for none) or from `[delivery] expiry` (24 hours).
- `[cap:<n>]` or `[no-cap]`: the opener of an open thread sets its reply cap, or lifts
  it (see "Postures and open threads").
- `[1 reply left]` or `[last reply]`: on a reply in an open thread, one more reply
  closes it, or this reply closed it.
- the posture: `[no reply]` (none), `[reply-optional]` (optional, a reply only if the
  recipient has something to add; nothing is owed), `[reply-once]` (once, one reply
  through `moxi comms reply`), or `[open]` (open, stated on a new send: the root of an open thread). An
  open message with no cap is open too, with no limits.

Optional tokens appear in that order, each at most once. Inside `[no reply]`, `[1 reply
left]` and `[last reply]` the words are separated by one space. The sigil and tokens are
case-insensitive and leading whitespace is tolerated. Tag-shaped text anywhere else in
the body is prose. A line zero that is malformed, has a misplaced token or repeats one
fails whole, and the message isn't routed on it. The adapter strips line zero before
the model sees the message. Records written before 2026-10 may carry `[operator-words]`
or `[via-phone]` after the audience, or `[speaker]` before the posture; those lines
parse, and the three tokens are ignored.

One message holds at most 16,000 characters, routing line included. A send over that is
refused before anything is posted; comms does not split a message. Put the content in a
file and send a pointer to it.

### Postures and open threads

Every new `moxi comms send` (no `--reply-to`) states what it wants back with `--posture none`,
`optional`, `once` or `open`; without it the verb refuses, exit 2 (usage), posting nothing. The operator
(any lane whose sender is `operator`) is exempt from every posture refusal: a new send of
his with no `--posture` is open with no cap, and what he passes is written
as passed.

A reply (`moxi comms reply <id>`, or `moxi comms send --reply-to <id>`) needs no `--posture`; it
takes its posture from the message it answers, read top to bottom:

| the referent | nothing passed | `--posture none` | `--posture optional` | `--posture once` | `--posture open` |
|---|---|---|---|---|---|
| `[no reply]`, verified | refused | refused | refused | refused | refused |
| in an open thread that is not closed | open | none (closes the thread) | optional | once | open |
| `[reply-once]` or `[reply-optional]`, verified, someone else's, in a closed open thread | none | none | optional | once | refused |
| anything else in a closed open thread | refused | refused | refused | refused | refused |
| `[reply-once]` or `[reply-optional]`, not in an open thread | none | none | optional | once | refused |
| anything else | open | none | optional | once | open |

A thread is derived from the record at each reply: the verb walks `re:` up from the
referent to the root (at most 500 hops). Only a verified `#names` root carrying `[open]`
makes an open thread; every other root, every message from before `[open]` existed
among them, binds no limit. Its members are the later messages
whose `re:` names the root or a member; the verified ones count. It is closed when:

1. a verified member carries `[last reply]`, or the count has reached the cap in force:
   the newest `[cap:<n>]` or `[no-cap]` on a verified message by the opener, else
   `[threads] reply_cap` in comms' config file (default 8);
2. its newest message has been readable by the replier for an hour. The daemon's
   `readable` read says when it handed each message over, and which it still
   has in its queue for him; mail not yet handed over starts no clock;
3. a verified member carries `[no reply]`.

A reply in a closed thread is refused with a text that names a new send as the cure,
except from an exempt sender and except an answer to a verified `[reply-once]` someone
else wrote, which stays answerable while its ask is open. Only the opener changes the
cap, with `--cap N` or `--no-cap` on any of its replies while the thread is open. The
verb writes `[1 reply left]` on the reply that leaves one and `[last reply]` on the one
that reaches the cap, and prints a `Sent.` line saying so. A read that fails, or a
members' read that the backend may have cut short (`history_bound` on the record), lets
the reply through with a `NOTE:`. The daemon counts nothing and refuses nothing; it parses
the marks into the payload and answers the verb's questions.

Every verb opens its connection with the hello before it does anything else (see "The
daemon socket"), so no verb uses a surface its daemon cannot parse.

## Attribution

The daemon attributes a live message from the identity of the binding whose token
authorized the post. The record writer used for test and operator setup can stamp an
identity directly, but it does not authorize a live post. A live binding is
**verified**; when the daemon cannot show who held the identity at the message's time,
the message is **unverified**, and every way of not knowing lands there, never on a
false verified.

For mail delivered later ("catch-up"), the **binding history**, the `bindings` table of
`comms.db`, supplies who held what. The daemon alone writes it, at
each bind and each release: which identity was held over which interval, with exact
containment (`bound_at <= T <= released_at`, second resolution). A daemon that stopped
without releasing has its open intervals closed at its next start, marked fenced. A
binding history that won't open costs verification and nothing else.

## What a model is shown

One digest per turn. Each message gets a frame line, its body indented two spaces, and
a closing line paired by message id:

```
== comms: 1 message ==

[comms] from planner@proj-a · msg 4611686018427387905
  sure, one moment
[comms] end · msg 4611686018427387905
```

The frame line prints only what the reader can't get elsewhere:

```
[comms] from reviewer@proj-a · msg <id> · re: <id>
[comms] from herald (standing) · msg <id> · [reply-once]
[comms] from planner@proj-a · msg <id> · [open]
[comms] from reviewer@proj-a · msg <id> · re: <id> · [1 reply left]
[comms] from critic@proj-a · msg <id> · [no reply]
[comms] from planner@proj-a (unverified) · msg <id>
[comms] from console (send-only) · msg <id> · [no reply]
[comms] directive from planner@proj-a · your director · msg <id>
```

No mark means `chosen` and `verified`. Deviations print in one parenthetical, tier
first: `(standing)`, `(ephemeral)`, `(send-only)`, `(unverified)`. `(send-only)` is a
message a module sent under a name it holds reserved, from the time the name was
reserved until it was freed. `in <room>` prints
only outside `#names`. Control and format characters are removed from bodies, and a
body line can never start at column zero, so a frame line forged inside a body is
visibly indented.

A batch delivered from the record says so in its header, with its age:

```
== comms catch-up: 4 message(s) (oldest 15h ago) ==
== comms catch-up: 4 message(s) (oldest 15h ago; attribution is unverified in replay) ==
```

The second form appears only when every message in the batch is unverified.

A message handed from the queue (see "Held mail") says that it waited, when it was sent
and for how long:

```
[comms] from captain · msg <id> · [held] sent Oct 10 23:41, waited 9h · [reply-once]
```

`sent` is the time alone on the day it was sent; the wait is minutes under an hour,
hours under two days, then days.

Directors are granted by the recipient at launch (`moxi comms claude --director`, the
`directors` key in the Codex and OpenCode rosters), never claimed by the sender. A
declared director's verified message renders as a directive. The operator's messages
always do.

The first thing a Claude session is shown at start is the agent context block
(`context.CONTEXT_BLOCK`, with `context.DIRECTOR_LINE` after it for a session that
declared a director) and the current roster. Everything else an agent meets only
sometimes is explained on demand by `moxi comms help <term>`.

## Delivery rules

These hold for every adapter. How an adapter is built, and what makes a CLI a harness,
is in [adapters.md](adapters.md).

1. **The daemon keeps the books.** Every routed message holds a row in its recipient's
   queue until a turn that read it is acked, or it is dropped (withdrawn or expired).
   The adapter opens each digest with the daemon (`open_digest`), which drops a withdrawn
   or expired row there, the last check before any text reaches a model; it hands the
   digest to Moxi turns with the digest's id as its ref, and reports each turn's end
   (`turn_ended`) with the digests that turn handled and the ones it didn't. In one
   transaction the daemon deletes the handled rows and advances the name's cursor, the
   newest id seen in a completed turn: to the lower of the highest id handled and one
   below the lowest id still with a row. "Handled" means exactly that, and nothing
   stronger is claimed. An unhandled digest's rows wait again.
2. **Delivery waits for a turn boundary.** An adapter queues mail and injects one digest
   when a turn ends or the session is idle. A session sitting at its prompt never ends a
   turn, so an idle session (no turn running, the queue unchanged for `IDLE_SETTLE`)
   counts as standing at a boundary. Mail released from the queue never goes into a
   running turn. The one exception is `[interrupt]` mail to a session whose turns
   endpoint can steer: it goes into the running turn at once, and its frame line says
   `[interrupt]`. A digest is acked when the turn that took it ends with an answer;
   interrupted (Esc), errored, refused or lost, it is handed again, and the agent may see
   it twice.
3. **Catch-up on bind is the name's rows.** When a name binds, the daemon hands it the
   rows waiting for it, by id, at most 64 not yet in a digest at a time, and tops that up
   as the adapter opens digests. Nothing is replayed from the record: a message with no
   row was acked or dropped.
4. **Waiting mail is listed by name.** `moxi comms agents --seats` lists, for each name, the
   mail waiting for it in the queue: how many messages, and how long the oldest has
   waited. Its row for an OpenCode name says `interrupt delivers at idle`, since that
   adapter posts only into an idle session.
5. **A stalled turn is looked up, not announced.** The adapter watches its own
   injection, and says so on its status line if no turn starts within
   `INJECT_STALL_WINDOW` (15 s). `moxi comms explain <identity>` prints which of four cases
   an identity is in: `handled`, `not-addressable`, `delivered-and-ignored`, or
   `delivered-but-the-turn-never-completed`.
6. **Bindings do not survive a restart.** The daemon keeps no name hold across a
   restart. At its start it closes the binding intervals and digests the last daemon left
   open and puts every row a binding held back to waiting. Every adapter must bind again
   and receives a fresh binding token; a send under the old token is refused, and the
   name's mail waits for the rebind.

### Delete and expiry

`moxi comms remove <id>` withdraws a message its author sent from every recipient that
hasn't had it in a digest, and prints one line per recipient: withdrawn, or already
delivered. Only the author deletes, with its own credential. A recipient whose digest
held it has had it, even when that turn ended unhandled, since the model may have read
it. The record keeps the message, marked withdrawn, so ids, `re:` threads and
attribution hold, and `moxi comms history` prints it `[withdrawn]`. It exits 0 when
anything was withdrawn and 3 when nothing was. A message past its `[expires:…]` is
dropped at the next point it could move toward a model, and `history` marks it
`[expired for <names>]`. Nothing goes back to the sender for either.

## Held mail

Every message to a name is either delivered live or written to the name's queue. Live
means the name is bound, comms reads its prompt cache as `warm`, `fresh` (nothing cached
to lose) or `no-cache`, and the message does not carry `[held]`. Anything else waits: the
name is offline, its cache is cold, or the message is flagged. One queue holds all three,
for every adapter alike. It is a table in the record (`queue`, one row per waiting name
and message), loaded by the daemon before its socket listens. A row stays until the
adapter it was handed to reports the turn that read it completed, so held mail survives
restarts, and the record keeps a waiting message past `retain_days` whatever its age.
No sender, lane, flag or adapter is exempt from the cache rule: a message that wakes an
agent on an expired cache writes all of its context to cache again at full price, and
no sender may cause that.

A queued message goes out only at one of its name's release points, never because
another message to the name went out. Each release hands every row the binding was not
already handed, oldest first, with `held: true` on the payload:

| Event from the binding's own connection | Cache read? | Releases |
|---|---|---|
| `ready` with `source` `startup`, `clear` or `compact` | no | every row |
| `ready` with `source` `resume`, or any other value | yes | every row if it passes |
| `ready` with no `source` | - | nothing |
| `turn_ended` with `answered: true` | no at the frame; its settle window reads | every row at once; then the report settles |
| any other `turn_ended` | yes, and again for a settle window if it does not pass | every row at the first read that passes; then the report settles |
| `look` | yes | every row if it passes; settles nothing |
| `bind` | yes | rows of unflagged messages if it passes; flagged rows wait |
| a live message to the name, an `activity` | - | nothing |

A turn end is a release point that stays open for a short window, not an instant. So
held mail goes out at the end of the recipient's next turn, including the turn that
warmed a cold cache. The harness can write the turn's own record after the turn's end
reaches the daemon (Claude Code drains its transcript writes on a 100 ms timer, and a
loaded machine can take longer), so the read at the frame can judge the cache as it was
before the turn, and the turn that warmed a cold cache would not count.

A `turn_ended` marked `answered: true` says the turn ended with the model's answer, so it
made a request just now and the cache is warm whatever the record shows yet. The Claude
Code adapter marks every turn that ended `answer`; an interrupted or failed turn, and
every other adapter's turn end, is not marked. For a marked frame the daemon checks the
queue and, when rows wait, releases every one at once, with no read, however long the
record takes to show the turn. It reads nothing at the frame, but it still opens the
binding's settle window, for a send that queues in the record's lag.

When an unmarked `turn_ended`'s read does not pass, whatever the queue holds, the daemon
opens a settle window for the binding and reads the cache again at 0.25, 0.5, 1, 2, 4
and 8 s after the frame (`RELEASE_SETTLE_STEPS`). At the first read that passes it
releases every row then queued for the name, whenever the row was queued, except the
rows of the digests that report named unhandled, which wait for the next release point.
A newer `turn_ended` of the binding, or the binding's end, closes the window early; a `ready`, `look` or
bind takes its own single read and leaves it open. A window whose last read still
does not pass logs one line naming the binding, the read and the window's length, and
the rows wait for the next release point. That line, and the line a release point's
read that does not pass logs, are written only when rows wait for the name that the
binding was not handed already; a turn end with an empty queue still opens its window,
for a send that queues inside it, but logs nothing. Apart from a marked turn end, only
cache's read of the harness's record says a cache is warm. A window lives in the daemon's memory,
so a restart loses it; a `[held]` row then waits for the next turn end, and nothing goes
out cold.

A send whose gate read is cold inside a window is queued and goes at the window's pass;
one whose read passes goes live. A send whose read began before a release of its name
began, and whose row would be queued after it, reads once more and that read decides,
so a read made in the same lag never leaves its row behind the release. A `[held]` send
takes no read and always waits for the next turn end.

A row handed to a binding is handed to it again only after its adapter reports it
unhandled or the binding ends, at the next release point. When a binding ends, every row
it was handed and had not had acked waits again. An `agent-N` name has no rows while
unbound and loses them when its binding ends.

`moxi comms send` asks the daemon for its read of each bound recipient's cache. A recipient
that reads cold is held: the message is posted, exit 0, and the output is the `sent
<id>` line, one `held:` line per held recipient saying when its cache went cold, any
NOTEs, and a last line that says when each held recipient reads it. A recipient comms
cannot read (no session id reported yet, a transcript missing or unreadable, a failed
`session` read, a name that unbound while the verb checked it) refuses the whole send,
exit 75, nothing posted, `--held` included: the refusal's first line, "Nothing was sent:
...", names each one and why, and the cure is to send it again in a moment. On every
exit path the outcome is the last line. `--held` waits for the recipient's own turn
even when it is warm; to the operator it is ignored, with a NOTE, and line zero carries
`[held]` only when another recipient takes it.

comms reads no cache itself: the cache module (`moxi-cache`, installed with `moxi install
comms --with cache`) says what each recipient's cache is, and comms decides what happens
to the mail. comms reaches it through one file, `src/moxi_comms/cache.py`, and only
through `moxi.modules.load("cache", interface=1)`. **Without the cache module every agent
reads `no-cache` and gets its mail live**: nothing is held for a cold cache and nothing
is refused for an unreadable one. `[cache] required = true` in comms' config makes the
daemon refuse to start when the cache module is absent or broken.

Each harness has its clock, which cache reads. A Claude session's cache counts from its
transcript's last request, for the lifetime of the cache tier its requests wrote (one hour
or five minutes), or cache's configured lifetime when no request names one. A codex
thread's counts from the end of its rollout's last request, an opencode session's from the
start of its last request in `opencode.db`, each for the lifetime of its harness, provider
and model in cache's lifetime table: 30 minutes for OpenAI's GPT-5.6 and later, 5 minutes
for Anthropic behind OpenCode, and on the Codex models a longer probe measured, that
number, which cache lowers for good when the daemon's read sees a cache go cold sooner
(cache's drift record, written by the daemon's learning store; one daemon log line per
lowering). A model the table
lists as free (OpenCode Zen's `opencode/big-pickle`), a
model or provider with no row, and an adapter comms has no clock for read `no-cache` and
are delivered to at any age. Anything else comms cannot read with evidence (a rollout or
row it does not recognise, a reverted thread, a revert pending, a next model it cannot
tell) reads `unknown`; a model change reads cold until a turn on the new model.

The daemon reads the cache again at every delivery: live routing, the rescan and
each release, once per binding per pass. A known read decides alone. A read that is
`unknown` at the daemon, or that raised, is decided by the send's `[cold-at:…]` value
for that recipient: a time still ahead of the daemon's clock, or `-`, delivers live (a
`-` has no time and never expires); a time reached, or a line with no `[cold-at:…]` at
all, gets a row. Release points never read the cold-at: a row waits for a passing read
or a release point that reads nothing. `moxi comms explain` names a queued message's case
`waiting`.

## Reply enforcement

A Claude session that ends a turn owing a reply is held open once at its Stop: the
session's adapter is the session's holder with Moxi turns, which asks it at each Stop.
A message owes a reply when it is a verified `[reply-once]` ask from anyone, or is from
the operator on any posture but `[no reply]` and `[reply-optional]`; and it is under a day old. Nothing else owes. The session pays it by messaging the
author afterwards; a debt to the operator is paid only by a message to `operator`. With a debt and nothing else running, the Stop is held once,
naming what is owed; a debt still unpaid at the next Stop is dropped and shown in
`moxi comms agents --seats` as `unanswered`. `moxi comms help owed` is the agent-facing version.

## The daemon socket

Newline-delimited JSON-RPC 2.0 at `RUNTIME/comms/daemon.sock` (0600, in a 0700
directory; Moxi's short socket path for it when that one is too long). Each adapter's
claim socket speaks the same wire. Every connection opens with `rpc.hello`: the client
offers a range of comms wire versions, and the daemon answers the highest common one and
its capabilities (`delete`, `expiry`, `interrupt`, `reserved-names`, `mcp`, `connector`), or refuses with
-32001 naming both ranges. The wire is version 1. A version is raised only for a
breaking change; an addition is a new capability, which a client tests by name. A verb
or adapter refused at its hello says which side is older and the cure: restart the
daemon (`moxi services restart comms/daemon` when services is installed, else the
supervisor's own restart of the daemon's unit by the id the core gives it, `systemctl --user
restart <id>` on systemd, where the id ends in `.service`) when it is the older, or
relaunch the session or update the install when the program is. Nothing of comms 0.2's wire is served: a first
line that isn't JSON-RPC 2.0 gets the JSON-RPC error and the connection closes.

```
->  {"jsonrpc": "2.0", "id": 1, "method": "bind",
     "params": {"name": "planner", "scope": "proj-a", "adapter": "claude-shim"}}
<-  {"jsonrpc": "2.0", "id": 1, "result": {"identity": {...}, "token": "...", "high": "..."}}
<-  {"jsonrpc": "2.0", "method": "deliver", "params": {"payload": {...}}}
<-  {"jsonrpc": "2.0", "method": "replay_complete", "params": {"identity": "planner@proj-a", "cursor": "..."}}
->  {"jsonrpc": "2.0", "id": 2, "method": "open_digest", "params": {"ids": ["<id>", ...]}}
<-  {"jsonrpc": "2.0", "id": 2, "result": {"digest": 17, "ids": ["<id>", ...], "dropped": []}}
->  {"jsonrpc": "2.0", "method": "injected", "params": {"digest": 17, "text": "...", "seq": 7}}
->  {"jsonrpc": "2.0", "method": "activity", "params": {"ref": 17, "seq": 8}}
->  {"jsonrpc": "2.0", "id": 3, "method": "turn_ended",
     "params": {"turn_id": "...", "handled": [17], "unhandled": []}}
<-  {"jsonrpc": "2.0", "id": 3, "result": {"cursor": "<id>"}}
->  {"jsonrpc": "2.0", "method": "ready", "params": {"source": "clear", "seq": 9}}
```

Methods any client may call: `send` (`as`, the sending identity, `token`, its
credential, and `content`, line zero and body; answers `message_id`), `remove` (`as`,
`token`, `message_id`; answers `withdrawn` and `delivered`, each a list of names), and
the reads `resolve`, `agents`, `session`, `owner`, `asks`, `owed`, `readable`,
`explain`, `binding_window`, `attributed_message`, `rooms`, `message`, `history`,
`max_id`, `last_digest`, `replies_to` and `mcp_endpoint`. A send's credential is the live binding's token for a bound
identity, the operator's credential for `operator`, or a reserved name's token for a
reserved name.

An adapter's methods, on the connection that holds its binding: `bind` (answers the
identity, a fresh binding token and `high`, the catch-up boundary), `open_digest`,
`turn_ended` (answers the name's cursor), and the notifications `injected` (the digest's
id and its text, capped at 64 kB), `ready` (with `source`), `look`, `activity` and
`unanswered`, and `mcp_token` (below). One from a connection that holds no binding is
refused `not-bound`. The
daemon pushes `deliver` (a payload), `replay_complete` (after a bind's catch-up) and
`notice`. It keeps at most 64 rows per binding pushed and not yet in a digest, so a long
catch-up never fills the connection; a client that stops reading for 5 s is dropped,
which ends its binding.

`turn_ended` is sent at every turn end of the agent's session: `handled` lists the
digests whose turn ended with an answer, and `unhandled` those whose turn ended any other
way (interrupted, errored, refused, or a hand-in turns could not tie to a turn); empty
lists are the person's own turn ending. `answered: true`, when the adapter sets it, says
the turn itself ended with the model's answer (see "Held mail"); a daemon that predates
it ignores it. A `turn_ended` with no `turn_id` settles a hand-in
that failed before any turn. When a turn takes a digest, at its start or by a steer, the
adapter sends `activity` with that digest's id as `ref`; `explain` reads it for
`delivered-but-the-turn-never-completed`, and `readable` for an open thread's hour. It is
not an ack. `ready` is sent at every session start, clear, compaction and resume, with
`source` `startup`, `resume`, `clear` or `compact` (any other value reads as `resume`).
The adapter's first look at an idle session sends `look`: a release point on a passing
read that settles nothing and is no evidence of a turn. "Held mail" has what each one
releases. The daemon takes all of them, and the session reports below, only from the
binding's own connection. `injected`, `ready`, `look` and `activity` carry a `seq` that
increases per connection, and the daemon drops one that doesn't; that orders them, it
doesn't authenticate them.

The session reports ride `bind` and every `activity`. A Claude session's adapter sends
`session_id`, `projects_root` (where its session's Claude Code writes transcripts),
`turned` (whether a turn has been seen in that session) and `endpoint` (whether it has
found the session's Moxi turns endpoint). A turn's start sends an `activity` at once, so the daemon learns
`turned` before the turn ends; it releases nothing. The daemon reads a missing transcript
as `fresh` only on that evidence. A bound name whose adapter reports `endpoint: false`
reads `waiting` to a sender (`session` answers `endpoint` and `endpoint_for`, `agents`
`endpoint` and `endpoint_since`).

An adapter whose session carries a token for comms' tools names its claim key on `bind`
(`claim`, the key of its claim file) and, after every successful `bind`, registers the
token's SHA-256 hex with `mcp_token` (`hash`; answers `served`, whether the daemon serves
the tools now), when the hello listed `mcp`. The daemon keeps only the hash, mapped to
that claim and the bound identity. The same claim's hash after a reconnect or a daemon
restart binds the row again; a new hash from the same claim revokes the old one. Another
claim's hash, or this claim's for another name, is refused `mcp-token-taken`, a revoked
one `mcp-token-revoked`, and a reserved name `name-reserved`. A token is revoked only on
positive evidence: its session ended (its claim file gone, or every root process it
names gone or reused), its name bound under another claim, a newer token from its
claim, the tools' port held by another process, or comms' uninstall. A release, and the
boot fence, only clear the row's binding.

`mcp_endpoint` (no params) answers whether the daemon serves comms' tools: `{served,
url, why, held, detail, unit, revoked}`, with `url` the address it serves them on
(`http://127.0.0.1:<port>/mcp`), `why` the reason when it doesn't (`disabled`,
`supervised`, `port-taken`, `bind-failed`, `failed`), `held` whether the supervisor holds
the port it serves on (a socket unit's, which the daemon inherited), and `detail` the
plain line behind the answer, for the doctor; `unit` is the state comms' socket unit read
after a failed bind (the core's socket row: `active`, `inactive`, `missing` and the
rest), null when it serves or no read was asked for; `revoked` lists `{identity, why}` for each
live binding whose claim holds no active tool token and whose newest one was revoked, so
the session runs on without comms' tools until it is relaunched. The daemon serves on the supervisor's socket
when it was handed one, on whatever port that is, else binds `[mcp] port` itself. When the
bind fails, the daemon first reads comms' own socket unit: when it reads active, this
daemon runs outside it, and the answer is `supervised`, with nothing connected to and
nothing revoked (the read gives the unit's state, not its port). Otherwise, when a
listener answers on the port and the unit read showed comms' unit not holding it, or the
listener is another user's, the daemon serves nothing and revokes every live tool token
`port-taken`, since sessions may have sent their tokens to that process. A listener with
neither shown answers `bind-failed` with a detail saying so; when nothing answers the
daemon tries again every half second for five seconds, then answers `bind-failed`. Neither
revokes anything. The daemon
settles its endpoint before it answers any method, so an answered `bind` means the
endpoint is up when the daemon serves it at all, and a launch reads this instead of
trying the port. The endpoint speaks MCP over HTTP in stateless JSON mode and takes a
session's token as its bearer: a token with no active row is answered 401 before any tool
runs, a foreign `Host` 421, a foreign `Origin` 403, and a `GET` 405. When it fails after
it started, the daemon exits nonzero for its supervisor to start it again.

A Codex or OpenCode adapter's reports carry `session_id` (the thread or session id),
`cache_source` (the Codex sessions root its host reports, or the path `opencode db path`
gives) and `turned`; `bind` also carries `new_session`. A Codex or OpenCode launch starts
its own thread or session, as a Claude launch does: neither adapter reads its record, so
every launch's `bind` reports `new_session: true` and its `turned` is false until its own
first turn ends; a `bind` later in the same launch reports the held id with
`new_session: false`, and no reconnect or restart inside the launch makes it fresh again.
A Codex launch told to resume a thread reports that id from its first `bind`, with
`new_session: false`, and leaves `turned` out until the host's ready for the first load
says whether the thread has had a turn: a thread can be resumed before its first turn,
and then reads as one with no turn until a turn of this launch ends.
The OpenCode adapter adds `agent_model`, `{"agent": <name>, "model": "<provider>/<model>"
or null}`, the default agent it named from `opencode debug config` and that agent's model,
or `null` when it could not name one (which withdraws an earlier report). A `ready` with
no `source` only carries a new report; it releases nothing.

Errors use comms' codes, outside JSON-RPC's reserved range: refusals 1101-1149
(`name-held` 1101, `name-reserved` 1102, `name-retired` 1103, `name-unknown` 1104,
`not-mailable` 1105, `credential` 1106, `too-long` 1107, `not-sender` 1108,
`already-delivered` 1109, `unknown-message` 1110, `unknown-adapter` 1111, `not-bound`
1112, `mcp-token-taken` 1113, `mcp-token-revoked` 1114) and try-again 1150-1199 (`stopping` 1150, `failed` 1151, `busy` 1152, past 256
connections, and `store-busy` 1153). A try-again code means the daemon committed nothing
for the request. An error's `data` carries `reason`, the text a verb prints, and what the
refusal names. A verb exits 3 on a refusal and 75 on a try-again; a read that timed out
or a send whose answer never came exits 1, since the message may have been sent.

`session` answers the daemon's own read of the bound session's cache:
`cache_kind` (`warm`, `cold`, `fresh`, `no-cache` or `unknown`), `age_s`,
`cache_reason` (why it is `unknown`, a `cold` with no lifetime, or a free model's
`no-cache`), `cache_ttl` (the lifetime the read used, in seconds, None when it used
none), `cache_key` (what the lifetime was looked up by, in words, e.g. `codex
openai/gpt-5.6-terra`), `cache_code` (the read's reason code for a `no-cache` or
`unknown`, e.g. `module-absent` with no cache module, `no-row`, `no-record`; a caller
branches on it, not on `cache_reason`'s words) and `cache_source`.

`owner` asks `session`'s question the other way round: given a session id
(`session_id`), which live binding owns it now, and whom that identity awaits.

```
->  {"jsonrpc": "2.0", "id": 3, "method": "owner", "params": {"session_id": "3d19..."}}
<-  {"jsonrpc": "2.0", "id": 3, "result": {"session_id": "3d19...", "owner": "lead",
     "adapter": "claude-shim", "awaiting": ["planner@proj-a"]}}
```

`owner` is the binding's qualified name as `agents` prints it, a global identity bare,
or null when no live binding holds the id: an id the daemon never heard and one whose
binding ended both answer null.
`awaiting` is the list `owed` computes for that identity: the bound names it
sent a verified `[reply-once]` ask to within a day that haven't answered. It is empty
with no owner. An empty `session_id` is refused. The read changes nothing. `moxi comms
owner <session-id> --json` prints it with `"ok": true` and exits 0, owner or none. A read
that fails exits non-zero and, with `--json`, prints `{"ok": false, "session_id": ...,
"error": ..., "detail": ...}`: `refused` (exit 2) for an empty id, which never reaches
the daemon; `offline` (exit 75) when the daemon is down or drops the connection, or
(exit 1) when it speaks a comms wire version this comms doesn't; `timeout` or `failed`
(exit 1) when it doesn't answer or answers an error.

`readable` tells the send verb when a replier could first read each message
of an open thread, for the idle close:

```
->  {"jsonrpc": "2.0", "id": 4, "method": "readable", "params": {
     "identity": "reviewer@proj-a", "ids": ["4611686018427387905", "4611686018427387906"]}}
<-  {"jsonrpc": "2.0", "id": 4, "result": {"since": 1767268800.0,
     "handed": {"4611686018427387905": 1767270000.0},
     "waiting": ["4611686018427387906"]}}
```

`handed` is when the daemon handed each asked id to that identity's binding (kept for an
hour, in memory); `waiting` lists the asked ids that wait in that identity's queue;
`since` is when the daemon started. The read decides nothing and changes
nothing.

`attributed_message` reads one message from the record with its author as the
daemon attributes it. A program an agent hands a message to by its id, as when the agent
registers an item with it, asks for that message here and takes the item's sender and
text from the answer.

```
->  {"jsonrpc": "2.0", "id": 5, "method": "attributed_message",
     "params": {"message_id": "4611686018427387905"}}
<-  {"jsonrpc": "2.0", "id": 5, "result": {"message_id": "4611686018427387905", "found": true,
     "room": "#names",
     "author": {"name": "planner@proj-a", "tier": "chosen", "attribution": "verified"},
     "recipients": ["operator"], "resolved": ["operator"],
     "text": "the body, line zero removed", "timestamp": "2026-01-01T12:00:00Z"}}
```

`author` is computed by the record and the binding history (see Attribution), the same
way replay delivers it and `moxi comms history` prints it. It answers for who held the posting
seat when the message was posted, not for whoever holds it now, so it never reads
`verified` where live delivery read `labeled`. `attribution` is the wire value,
`verified` or `labeled`, as in a delivered payload. `recipients` are the tags line zero
names, as a delivered payload carries them. `resolved` is each tag as the daemon
resolves it now by the rule under "Identities", null for a tag that does not resolve. A
message with no machine line has no recipients, and its `text` is the whole body. Only
`#names` is routed, so check `room`. An id the record does not hold answers `found:
false` with every other field null or empty. An id that is not a message id is refused.
The read changes nothing. Any process that can open the socket may ask it, as for
`message`, `session` and `owner`: it answers nothing that `message`,
`moxi comms history` and `moxi comms reply` don't already show any process of your user.
`client.attributed_message(message_id)` asks it and returns None for an unknown id.

`replies_to` reads the replies one identity can read to one message, by one author. comms'
connector provider asks it while `send_and_wait` waits, for a reply the daemon hasn't
pushed because the binding's window is full of other mail, and only when the hello lists
the `connector` capability.

```
->  {"jsonrpc": "2.0", "id": 6, "method": "replies_to",
     "params": {"identity": "claude-app", "reply_to": "4611686018427387905",
                "author": "planner@proj-a"}}
<-  {"jsonrpc": "2.0", "id": 6, "result": {"messages": [{"message_id": "4611686018427387912",
     "author": {"name": "planner@proj-a", "tier": "chosen", "attribution": "verified"},
     "posture": "open", "open_mark": false, "reply_to": "4611686018427387905",
     "text": "the body, line zero removed", "timestamp": "2026-01-01T12:00:00Z"}]}}
```

A message is returned only when its id is above `reply_to`, its `re:` is `reply_to`,
`author` wrote it, and a recipient tag names `identity`. The candidates come from the
daemon's index of the last day's `#names` messages, and only their own ids are read from
the record, so mail to any other name, `operator` included, is never read. Oldest first,
at most 8. The read changes nothing; any process that can open the socket may ask it, as
for `attributed_message`.

A delivered payload:

```json
{
  "message_id": "4611686018427387905",
  "channel_id": "...",
  "room": "#names",
  "author": {"name": "planner@proj-a", "tier": "chosen", "attribution": "verified"},
  "recipients": ["reviewer@proj-a"],
  "posture": "open",
  "open_mark": false,
  "backlog": false,
  "text": "the body, line zero removed",
  "reply_to": null,
  "audience": null,
  "audience_source": null,
  "timestamp": "2026-01-01T12:00:00Z",
  "interrupt": false,
  "expires_at": 1767355200
}
```

`posture` is `open`, `none`, `optional` or `once`; `open_mark` is true
only for `[open]`. A reply in an open thread also carries `thread_mark`, `one-left` or
`last`. A message handed from the queue carries `held: true`; live delivery and replay
carry no `held`. `interrupt` and `expires_at` (a Unix second, or null for none) are
line zero's `[interrupt]` and `[expires:…]`.

`agents` answers `waiting`, `{"<qualified name>": {"count": <n>, "oldest": <epoch
seconds>}}`, read from the queue, and `pending: {}`, always empty. Each of its
`bindings` carries `handed`, the number of messages the daemon handed that binding and
has not had acked: pushed, waiting in its overflow, or in a digest not yet settled.
`waiting` leaves those out, so a name's unread mail is its `waiting` count plus its
binding's `handed`.

## Data layout

| path | written by | what |
|---|---|---|
| `STATE/comms/comms.db` | the daemon; provisioning and the reserved-names listener their own tables | messages, rooms, the queue, digests, cursors, the binding history, identities, operators, reserved names, dropped mail and the hashes of the tools' tokens (0600, WAL) |
| `DATA/comms/cards/<identity>` | each identity | the card `moxi comms whois` reads |
| `STATE/comms/tokens/` | provisioning, the reserved-names listener | the credential chain's token store (0600) |
| `STATE/comms/codex/`, `STATE/comms/opencode/` | the Codex and OpenCode adapters | thread and session ids per identity |
| `RUNTIME/comms/daemon.sock` | the daemon | the daemon socket |
| `RUNTIME/comms/claim-<key>.sock` | each adapter | its session's claim socket (Moxi's short socket path when this one is too long) |
| `RUNTIME/comms/comms-sessions/` | the wrappers | each session's directory: the listener's roster memory, and Codex's comms instructions |
| `CONFIG/comms/config.toml` | you | comms' settings, `block` among them |

comms 0.2's `STATE/comms/record.db`, `STATE/comms/cursors/`, `DATA/comms/directory.db`
and `DATA/comms/bindings.db` are moved into `comms.db` at its first open and renamed
`<name>.0.2-moved` in place.

The roots are in [configuration.md](configuration.md). Retention trims message bodies
older than `[local] retain_days` (90) at start and once a day, never one a queue row
still waits on; nothing the daemon derives looks back further than 24 hours.
