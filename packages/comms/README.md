# Moxi comms

Messages between AI coding agents on one machine. Each agent gets a name; any agent
can send any other a message with one shell command; a small local daemon delivers it
into the recipient's session at the end of its current turn, where the model reads it
and can answer the same way.

```sh
# in the session named planner
moxi comms send reviewer --posture once "The parser change is on branch fix-parse. Can you review it?"

# reviewer's session, when its turn ends, is shown:
#   == comms: 1 message ==
#
#   [comms] from planner · msg 4611686018427387905 · [reply-once]
#     The parser change is on branch fix-parse. Can you review it?
#   [comms] end · msg 4611686018427387905

# and answers; the reply closes the exchange
moxi comms reply 4611686018427387905 "Looks good; one nit on line 40."
```

No account and nothing off the machine: comms keeps everything in one SQLite file in
your home directory, and the CLI and the adapters reach the daemon over Unix sockets.
The daemon also serves comms' tools to sessions over MCP on `127.0.0.1:8450`, a loopback
TCP port on by default; every request needs the token minted for one session, and
`[mcp] enabled = false` in the config file and a daemon restart turn it off. Any account
on the machine can connect to that port, so read [docs/security.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/docs/security.md)
before using comms on a machine other people log in to.

```
 agent A ── moxi comms send ──▶ moxi-comms-daemon ──▶ comms.db ── socket ──▶ adapter ──▶ agent B
 (any shell)                 (writes, routes by name)  (SQLite)              (at a turn boundary)
```

Claude Code, Codex and OpenCode sessions take part through adapters. A Claude Code
session `moxi comms claude` launches gets an adapter process of its own, which binds the
session's name and delivers mail into the session through Moxi turns, the one way into a
harness session: `moxi comms claude` loads turns' Claude plugins into the session it
starts. Codex and OpenCode work through experimental adapters.

Two words come up throughout. The **operator** is the human running this system: you,
sending from your own terminal as the `operator` identity; the operator's messages are
always shown to an agent as directives. A **director** is an identity a session was told
at launch to take direction from (`moxi comms claude --director <name>`); its verified
messages are shown to that session as directives, and every other message as information
from a peer.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/). macOS and Windows are
not supported yet.

```sh
uv tool install moxi
moxi install comms
moxi comms doctor
```

`uv tool install moxi` puts the `moxi` command on PATH, and `moxi install comms` adds comms
and turns, which it requires, to the environment `moxi` runs in. Install comms through
`moxi install`, not on its own: a copy installed elsewhere is one `moxi` can't see. `uv
tool install` fetches Python 3.12 if the machine lacks it; otherwise `uv python install
3.12` supplies one.

comms' commands run as `moxi comms <verb>`. `moxi shortcut comms` adds a bare `comms`
command if you want one.

- `moxi comms doctor` prints what this machine resolved: where files live, the config
  file, the backend (`local`) and where that choice came from, the provisioned
  identities, and whether the daemon answers. It writes nothing.
- `moxi install comms` also writes the daemon as a unit of your system's supervisor (a
  systemd user unit on Linux) and starts it. On its first start the daemon provisions
  itself: the operator identity. Nothing else needs setting up. The unit runs the
  `moxi-comms-daemon` of the environment comms is installed in. Stop and start it with
  `moxi services` (when services is installed) or `systemctl --user`, and check it with
  `moxi comms doctor`.

The daemon also runs fine in the foreground, which is the simplest way to watch it.
`moxi install comms` puts it in the `moxi` tool's environment, whose scripts uv doesn't put
on PATH, so run it by its path there:

```sh
"$(uv tool dir)/moxi/bin/moxi-comms-daemon"
```

The unit's `ExecStart=` names the same path, and `moxi modules --json` names the
environment as `prefix`.

The user service stops when you log out. To keep it running, enable lingering for your
user (`loginctl enable-linger $USER`); comms doesn't do that for you, because it changes
your login policy for the whole machine.

To remove it: `moxi uninstall comms`, which stops and removes the daemon's unit and takes
comms out of the `moxi` tool. Your messages stay under `~/.local/state/moxi/` and
`~/.local/share/moxi/` until you delete them.

## Quick start with Claude Code

Each session needs turns' Claude plugins. `moxi comms claude` asks turns for them and
passes each as `--plugin-dir` on the launch it starts (`--dry-run` shows the line), so
there is nothing to add by hand.

Open two terminals in your project and launch a named session in each:

```sh
moxi comms claude --name planner
moxi comms claude --name reviewer
```

`moxi comms claude` stamps the session's identity, starts the session's comms adapter
(it binds the name before `claude` starts, and its log is under comms' state directory)
and starts `claude`; everything after `--` goes to `claude` unchanged. A name another
session holds, or one a module reserved, is refused before `claude` starts: the wrapper
prints why and exits 3. With the daemon not running it starts nothing and exits 75, so
try again once the daemon is up. A bind the daemon doesn't answer in 15 seconds also ends
the launch with 75, before `claude` starts, and the Codex wrapper does the same. The
launch names its session id to `claude`: the one after `--` (`--session-id <uuid>`, or `--resume <uuid>`
without `--fork-session`), else the wrapper's own `--session-id <uuid>`, else a new one,
added as `--session-id`. A resume that names no id (`--continue`, `--resume` with no id,
`--from-pr`, `--teleport`) goes through with nothing added, and a send to that session is
refused (`cache-unread`) until its adapter finds the session's turns endpoint. A name with no `@scope` is the global identity,
so these two are `planner` and `reviewer`; add `@scope` only to keep apart two agents of
one name in different projects. Each session is shown a short block at start explaining
how to send and reply, and the current roster.

In either session, ask the model to message the other, or type the command yourself:

```sh
moxi comms send reviewer --posture none "Ready for review when you are."
moxi comms agents                  # who you can message
moxi comms history reviewer        # the record, with who sent each message
```

Mail to a name that isn't running waits for it and is delivered when a session with that
name starts. A name that has never had a session needs `--new-name` on the
send; without it the send is refused. `moxi comms help <term>` explains any word a session
meets.

## Features

| | status |
|---|---|
| Local backend (SQLite record), daemon, `moxi comms` CLI | supported |
| Claude Code sessions through `moxi comms claude` | supported, through Moxi turns: the session must load turns' Claude plugin; until its adapter finds the session's turns endpoint, mail to it waits and a send says `waiting` |
| The daemon as a systemd user unit, written by `moxi install comms` | supported |
| Held mail, catch-up on start, delivery at turn boundaries | supported |
| Attribution and the binding history | supported |
| Reply enforcement for the operator's and directors' messages | supported |
| Codex adapter (`moxi-comms-codex`) | experimental |
| OpenCode adapter (`moxi-comms-opencode`) | experimental |
| Cold-cache hold: a message to an agent whose prompt cache is cold waits for its next session start, /clear, compaction or turn, on any adapter and from any sender; a send comms cannot read the cache for is refused; checked on send and again by the daemon at delivery | experimental |
| macOS, Windows through WSL2 | not yet |

## Commands

| command | what it does |
|---|---|
| `moxi comms send <name> --posture none\|optional\|once\|open "text"` | message one or more names (comma-separated) |
| `moxi comms reply <message-id> "text"` | answer a message |
| `moxi comms remove <message-id>` | take back a message you sent from whoever hasn't had it yet |
| `moxi comms agents` | who you can message (`--seats`: bindings and waiting mail; `--json`: one row per address, as comms' browser panel lists them) |
| `moxi comms history <name>` | an identity's record |
| `moxi comms help [term]` | explain a comms word |
| `moxi comms whois <name>` | the card an identity wrote about itself |
| `moxi comms explain <name> [id]` | why a message did or didn't land |
| `moxi comms cache` | how cold each agent's prompt cache is (for the callers `[policy] cache_callers` names) |
| `moxi comms doctor` | what this machine resolved, and whether the daemon answers |
| `moxi comms whoami` | which comms identity this session holds |
| `moxi comms send --as <name> <to> "text"` | send under a send-only name a program registered, from outside every session, with no reply |
| `moxi comms send-only add\|renew\|remove <name>` | register a send-only name for a program (any program or service, not only a module), replace its token, or free it; inside a session, only a plain `add` of an unused name |
| `moxi comms exit-session <name> [--scope S \| --global]` | end a headless session by its name, and wait for the name to be released |
| `moxi comms reserved renew <name>` | mint a fresh token for a send-only name a module reserved, into this process's credential chain |
| `moxi comms migrate [--dry-run]` | move an older comms' files into `comms.db` now (the daemon's start does it too) |
| `moxi comms claude` | launch a named Claude Code session (also `"$(uv tool dir)/moxi/bin/moxi-comms-claude"`) |
| `moxi comms codex` | launch a named Codex session through Moxi turns' Codex host (experimental) |
| `"$(uv tool dir)/moxi/bin/moxi-comms-opencode"` | launch an OpenCode session for a roster identity (experimental) |
| `"$(uv tool dir)/moxi/bin/moxi-comms-daemon"` | the daemon, in the foreground |
| `"$(uv tool dir)/moxi/bin/moxi-comms-provision"` | provision standing identities |

comms' own scripts (`moxi-comms-daemon`, `moxi-comms-claude`, `moxi-comms-codex`,
`moxi-comms-opencode`, `moxi-comms-provision`) are in the `moxi` tool's environment,
which `moxi install comms` adds comms to, and uv puts none of them on PATH. Run one by
its path: `"$(uv tool dir)/moxi/bin/<script>"`.

`explain`, `doctor`, `moxi comms cache` (how cold each agent's prompt cache is, for the
callers `[policy] cache_callers` names) and `moxi comms owner <session-id>` (which
identity owns a session, for a program that nudges idle sessions; left out of the help
list) are read-only: they ask the daemon or read local files and post nothing.

Every new `moxi comms send` says what it wants back with `--posture`: `none` (no reply; a
reply to it is refused), `optional` (a reply only if the recipient has something to add;
nothing is owed), `once` (a single answer, through `moxi comms reply`) or `open` (a
back-and-forth). A send without it is refused. A reply needs no flag: it takes its
posture from what it answers, so a reply to a `once` ask or an `optional` message closes
the exchange. An `open`
thread closes after 8 replies (`[threads] reply_cap`, or `--cap N` / `--no-cap` from
whoever opened it), after an hour with no message, or on a `--posture none` reply; after
that, start a new send. A send as the operator needs no `--posture`. Other useful flags:
`--held` (hold the message until the recipient's next turn, or until its session starts,
is cleared or is compacted), `--interrupt` (go into the recipient's running turn where
its harness can take it there), `--expires 30m|6h|3d|never` (drop the message if it
hasn't reached the recipient by then; the default is `[delivery] expiry`, 24 hours),
`--new-name` (the recipient has not started yet), and
`--check`, on `moxi comms send` and `moxi comms reply` (run every check the send makes before
posting and then stop: it prints `would send <N> message(s) to <names>`, any `held:`
lines and every NOTE the post would print, or the send's own refusal with its exit code,
and sends nothing).

No message reaches an agent live while its prompt cache is cold, because a message that
wakes an agent on an expired cache writes all of its context to cache again at full
price. A send to an agent whose cache has expired,
on any adapter, is posted and held: it exits 0, prints a `held:` line for that
recipient, and the agent reads the message at its next session start, `/clear`,
compaction or turn (when you type into it, say). Nothing wakes it for that message
alone. A send to an agent whose cache comms cannot read is refused with exit 75, and the
output says "Nothing was sent." first and last; send it again in a moment. An agent comms
has no cache clock for, one on a model with no lifetime row included, gets its mail live.
This holds for every sender, the operator included, and there is no
override. The daemon checks again at delivery, so a post that skips `moxi comms send` is held
too.

## Other agents (experimental)

[docs/adapters.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/docs/adapters.md) says what each adapter supports, what makes a CLI
a harness comms can reach, and how to add one.

comms reads a Codex agent's cache from its thread's rollout and an OpenCode agent's
from `opencode.db`, for the lifetime its harness, provider and model have in the table
comms ships. Under the cold-cache rule above, a message to one is delivered while its
cache counts warm and held once it is cold, as for a Claude session; an OpenCode agent
on one of Zen's free models is reached at any age, and so is one on a model the table
has no row for, which comms has no clock for. [docs/adapters.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/docs/adapters.md) has
the detail.

- **Codex**: `moxi comms codex --name <name>` launches one identity's thread through Moxi
  turns' Codex host, which runs the `codex app-server` and its hooks. comms mail goes
  into the thread at turn boundaries, and `--interrupt` mail into the running turn; a
  digest settles when its turn ends. `--resume THREAD_ID` continues an earlier
  thread instead of starting a new one. Mail goes to the thread this launch
  started or resumed: /clear, /new, /fork and /resume in the window start or open another thread,
  which comms does not follow. To start fresh, end the launch and launch again. Install
  the extra: `moxi install comms --with codex`. The settings flags are the host's own
  (`--model`, `--sandbox`, `--option KEY=VALUE`); an identity listed in the config file's
  `[codex]` table launches with `"$(uv tool dir)/moxi/bin/moxi-comms-codex" <name>`.
  `--headless` runs one with no window and nobody in it (below).
- **OpenCode**: `"$(uv tool dir)/moxi/bin/moxi-comms-opencode" <name>` runs an OpenCode server for one identity and
  opens its session in your terminal; mail goes to the server as its own prompts, never
  through your input box. It defaults the session to a free OpenCode Zen model and
  never changes it after launch. Each launch opens a new OpenCode session, as a new
  Claude Code session starts fresh, and keeps that session while the wrapper runs,
  across a restart of its server. /new and /clear in the window are followed. Another
  session opened with /sessions is not, and mail stays with the session the launch
  started. Identities are listed in the config file's `[opencode]` table:

```toml
[opencode.agents."coder"]
cwd  = "/home/me/src/my-project"
```

### A headless Codex session

`moxi comms codex --name <name> --headless --brief "TEXT"` (or `--brief-file F`) starts a
Codex session with no window that holds its name like any other. The brief is its first
turn; after that, mail sent to the name starts turns while its cache is warm, as in a
session nobody types into. It ends by its exit rule, `--exit WHEN`, which repeats; the
first value to fire ends it:

| value | ends the session |
|---|---|
| `done` (the default) | once the brief's turn has answered and no mail handed to it is left unanswered |
| `cold` | once comms reads its cache as cold, or as unreadable for five minutes |
| `idle=<D>` | once no turn has run for D (`90m`, `2h`) |
| `after=<D>` | D after it started, even mid-turn |

`moxi comms exit-session <name>` ends one at any time. It also ends if its brief is
refused or its turn doesn't end with an answer, if it isn't ready within five minutes,
or if its brief's turn hasn't started five minutes after it is. When it ends, its
adapter's log says why in one line, `headless session ended: <cause>`, and the name is
released. Nothing holds the name after that.

Mail sent once its cache is cold is held, as for any agent, and nothing in a headless
session starts the turn that would read it: it waits for the name's next holder. With
`idle=` or `after=` and no `cold`, the wrapper prints a warning saying so.

The wrapper runs the session in your terminal's foreground, as every comms wrapper does,
so it ends when that terminal closes. To leave it running, start it detached: through the
Moxi launcher (`--target headless`), from a timer, or with `setsid -f moxi comms codex ...
> LOG 2>&1`.

## In the browser

With Moxi's browser UI shell installed (the `ui` extra), comms shows a page there, at
`/m/comms/`, and one row on the shell's Home, `Comms`, which is a link to that page.

### The page

The page shows who can be messaged, what waits on whom, and today's mail, and lets you
write to an agent. It has no heading of its own: the shell's top line names it. Top to
bottom:

- **The agents**, `N agents`: one row per agent name, the part of an address before
  `@`. An address with a scope (`builder-2@kestrel-web`) is never a row of its own; it
  shows under its name's row while it holds mail or a session. You are never a row,
  though mail to you shows in the messages below. A row is a name that holds a session,
  has mail waiting, or wrote to you asking for a reply; the rest are `Other names`.
  Rows are ordered by what they need: a message waiting for your reply, oldest first;
  then mail no session takes, oldest first; then, with launcher, a session whose cache
  is warm and that waits on other agents with nothing keeping it warm; then running
  agents by their newest message.
- **Under the agents, the messages.** `Today`, the day's newest twelve, newest first: who
  wrote to whom (`you` for you), the time, and the text cut to one line. After each
  recipient, while comms' books hold its copy: `not read yet, held until its next turn`,
  `not read yet, <name> isn't running`, or `not read yet`; a message taken back reads
  `taken back`, an expired copy `<name>'s copy expired`, and one for a name that takes no
  mail `<name> takes no mail`. Your own copy of a message to you has no such words. `Open`
  opens the thread between its author and recipient in place. `Earlier today · N more ·
  Show` adds thirty more of today; `Before today · N messages, back to <day> · Show`
  adds the day it names under its own label, and the line moves below it to count the
  days before that (`Before yesterday`, `Before <date>`).
- **`Other names · <names> · Show`**: the standing names with no session, mail or ask;
  a scoped address with neither mail nor a session, by its full address; and the
  send-only names, `sent by <module>` for a module's and `sent by a program` for one a
  program registered. Each but a send-only name has
  `Message`.
- **The foot**: `Comms is answering · read <time>` and `Comms settings`. Under it, one
  dim line each while it holds: a read that failed and why, and an address the page
  doesn't have.

**An agent row.** Line one is the name, where it runs (with launcher), and at the right
the row's fact. Line two says what kind of waiting it is:

| case | fact | line two | acts |
|---|---|---|---|
| it asked you for one reply | `waiting for your reply` | `wrote to you <time>, wants one reply` | `Reply`, `Message` |
| mail no session takes | `3 messages waiting` | `not running · since <time>, from <names>` | `Message` |
| with launcher: warm, waiting on other agents, nothing keeping it warm | `goes cold at <time>` | `waiting on <n> agents` | `Keep warm`, `Message` |
| running, mail held for its next turn | the cache fact | `2 messages held until its next turn · from <names> <time>` | `Message` |
| running, waiting on another's reply | the cache fact | `waiting on <name>'s reply since <time> · <name> hasn't read it yet` | `Message` |
| running, nothing waiting | the cache fact | `no mail waiting · last message <time>` | `Message` |

The cache fact is `warm until <time>` or `cold`, and with launcher launcher's own words
(`kept warm while 2 agents work`). When a name meets two cases, the higher gives the fact
and line two, and the second leads line three. Line three also has one piece for each
other address of the name that holds mail or a session (`builder-2@kestrel-web · 2
messages since 14:02, from planner`), so every count Home's row names is on the page. When
one of the name's addresses runs and another has mail no session takes, line two reads
`<address> isn't running`. Each line keeps to one line and is cut with an ellipsis when
narrow.

**An agent opened** (pressing the row, `Message` or `Reply`) speaks for one address: the
row's lead, or the address line you pressed. In order, each line only when its fact is
there: `Address` (each of the name's addresses, to press); `Session`, launcher's name for
the session and its harness, with `Sessions`; `Cache`, the cache fact in full, with
`Cache`; the mail in the books for that address, `Held` (running) or `Waiting` (not
running) and `Unread` (in its session but not yet read), each message with its time,
author and text cut at four lines with `Show all`, and `Take back` on yours while the
books can still take it back; `Waits on`, each name it waits on for one reply, with
`Thread`; `Directs` and `Directed by`; the send box; `All mail with <name>`, every message
by or to that address, which takes the place of the lines and the box; and `Details`,
folded: the address in full, adapter, tier, when it was bound, the session id with `Copy
id`, and `A name is held by one session at a time.`

**The send box** writes to one address as you. Its controls are on one line: `From
operator`; `Reply wanted`, one of four (`no reply`, `only if it has something to add`,
`one reply`, `back and forth`, the last by default and `no reply` when answering); with
the cache module, `Deliver`, now or at its next turn; and `Send`. The box checks how the
message would go when it opens and when you change `Reply wanted` or `Deliver`, and says
so on one line, `If sent now` and then the words: `Delivered. planner reads it at its
next turn boundary.`, `Held until planner's next turn. Its cache went cold at 18:35, …`,
`planner isn't running. It waits until a session takes the name.`, or why it would be
refused, with `Check again`. With turns installed, a delivered line names a recipient whose
turn is open, `Delivered. planner is working; it reads this when its turn ends.`, read from
turns at each check. Send stays an outline until there is text and the last check
said it would go; then it is the one control on the page in the accent colour. It is
pressed once, and its answer is final: `Sent <time>`, how it was delivered, how many
messages now wait, and that you can take it back until it has been read; a refusal keeps
your text. `Reply` sets the box to answer the oldest message that asked you, quoted above
the controls (`Answering planner's 14:02 message:`), and `Message` clears the quote.

**A thread** opens in place under the newest message between its two names, or as its own
`Thread` group above `Today` when the lists show none: `<a> and <b> · <n> messages today
· <name> owes one reply`, then each message with its author, time and what it wants
(`wants one reply`, `replies · wants no reply`), and `not read yet, held until <name>'s
next turn` while unread, its text cut at four lines with `Show all`. It shows the last
six; `Show earlier · <n> messages, back to <time>` adds twenty. Its foot has `Message
<a>`, `Message <b>` and `Copy the last message's id`.

Messages are drawn as text, never as markup, and the page draws only in the shell's text,
border and background colours, with the accent on `Send` alone. A time is the hour and
minute; a day is worded against the page server's date: `yesterday`, the weekday within
six days, else the date.

**Its reads.** The roster follows comms' `roster` topic. The mail state, today's messages
and an opened address's queue are read when the page opens, when the roster's waiting
mail or running names change, after a send or a take-back, when the tab shows again, and
every 30 s while it is shown. Each refresh reads every message the page shows again, so
`not read yet` clears once its reader has it. An opened thread is read again with them,
above the newest message it holds and with the read state of the messages from the oldest
it holds, so a refresh costs what the page shows, not what comms has stored. The message
reads open comms' store on connections that can't write and ask the daemon nothing, so
they go on while the daemon is down.

**Its addresses**, after `/m/comms/`. Opening or closing something rewrites the address
in place, so Back leaves the page; a page placed anew (Back from another page) opens what
its address names, without the scroll, the `Show`s or the box's text:

| address | opens |
|---|---|
| none | the page, nothing opened |
| `agent/<address>` | that name's row, its box writing to that address |
| `reply/<message id>` | the author's row, its box answering that message, the message quoted |
| `thread/<a>/<b>` | the thread between `a` and `b` |
| `mail/<address>` | `All mail with` that address, under its row |
| `others` | `Other names`, shown |

An address whose name isn't on the page opens nothing and says `<name> isn't on this
page.` at the foot; one the page can't read says `There is no such view here.` and stays
as it is. A `reply/` whose message no longer waits for your reply (answered, or too old)
opens the author's row with an empty box and says `That message no longer waits for your
reply.`

**Its states:**

| state | what shows |
|---|---|
| reading | nothing for 400 ms, then a dim `Reading…` |
| the daemon not answering | a first row `The comms daemon isn't answering`, `moxi doctor comms says why. Nothing can be sent until it answers.`, with `Copy`, and `Last read <time>. It may have changed since.` once a read had answered; the last rows kept dim, with no acts and no box; the messages still read |
| the roster can't be read | its sentence in the first row's place, the same dim rows |
| no agents | `No agents yet.`, what an agent is, and `Launch one from Home` with launcher; without it, the command `moxi comms claude --name NAME` with `Copy` |
| no messages today | no `Today`; `Before today` stays when there are earlier messages |

**What each other module adds.** The page needs only comms and the shell; each module it
reads adds to it when installed:

- **launcher**: where each session runs, the `Session` line, launcher's cache words, the
  row for a warm session waiting on other agents with nothing keeping it warm, and `Keep
  warm` on it when launcher can keep it warm. A press is disabled until launcher answers and its next reading comes, or
  for 20 s; a refusal leads line three with `Not kept warm: <why>` until then. Without
  launcher: none of these, the cache fact from comms alone, and the empty state gives the
  command instead of `Launch one from Home`.
- **cache**: the cache fact, `Deliver`, the `held until its next turn` words and the
  `Cache` link. Without it, unread mail reads `not read yet`.
- **sessions**: the `Sessions` link on the `Session` line.

Not on the page yet: launching an agent from a name with mail (`Launch`), and whether a
session's turn is open.

### The row on Home

The row reads `GET /api/mail` when Home is shown and every 30 s while the window is
visible. It counts the mail waiting for names no live session holds, since mail for a
running agent waits on that session, not on you:

| what waits | the row |
|---|---|
| one message, for one name | `1 message waiting · curator isn't running`, and `since 14:02` at the right |
| mail for two or three names | `3 messages waiting · curator and scout aren't running` |
| mail for more than three | `10 messages waiting · curator, scout and 3 more aren't running` |
| nothing, or the daemon is down | no row |

The count is set heavy and the row needs you (20 on Home's scale), so it sits above the
rows that don't. A name is shown without its `@project` unless two names on the row share
it, and the page names that mail by the same word. The time is the oldest message's: its
weekday when it came on an earlier day this week, its date before that. The row never
shows a message's text. `/api/mail` is the one route of comms the phone's gateway serves,
so the row shows on the phone too, and nothing else of the page does.

## Security

A name is a claim made by a process running as your user. comms keeps agents from
confusing each other and shows who said what, but any process running as you can speak
as any name, read the token store, or write comms' store. It is not access control. Read
[docs/security.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/docs/security.md) before running agents you don't trust, and before
using comms on a machine other people log in to: it also covers the tools' loopback port
and the OpenCode adapter's server.

## Who the page's send box trusts

The page's send box acts as you: `Check` asks whether a message would go and how it
would be delivered, `Send` posts it under the operator's name, and `Take back` withdraws
a message you sent. Each runs in comms' page server, which the shell starts for you.

**The rule.** The three acts trust any process that holds the page server's runtime
token. That token is no stronger than your own comms token: a process that can read one
can read the other. So anything that confines your agents has to cover both. Whatever
keeps an agent from the credential chain (the environment, the keychain, the token
store) must also keep it from the page servers' runtime files: each is `page.json`, mode
0600, in its module's directory under `$XDG_RUNTIME_DIR/moxi/`, and holds that server's
token. A sandbox that denies an agent the token store but lets it read comms'
`page.json` breaks this model.

- **Your credential.** Each act reads it in the page server's process, from the same
  chain `moxi comms send` reads in your terminal, and drops it when the act ends. The
  browser never receives it, and no answer or log line carries it. `Check` reads it too,
  since it runs every step of a send but the post, so the check that runs when you open
  an agent's send box can unlock a locked keychain or ask you for its password. The
  message goes out under your user id: `MOXI_COMMS_OPERATOR` in the page server's
  environment when it is set, else the one operator comms' directory holds. With neither
  the act refuses and posts nothing.
- **The phone.** The acts are not the gateway's. `/api/mail` is the one route of comms
  the phone's gateway serves, so nothing on the phone can send, check or take back.
- **A request forged by another web page.** Every route of the page needs the shell's
  cookie (HttpOnly, SameSite=Strict) and the `X-Moxi-Key` header, which only a page of
  the shell's own origin can read and send. A form on another site can't add a custom
  header, and DNS rebinding gets neither. On Linux the connection must also come from
  your user. The shell passes the page server only a short list of the browser's
  headers, and its own token.
- **Script hidden in a message.** Agents write the text the page shows. What keeps that
  text from running as script that could read the key is the shell's content security
  policy, `script-src 'self'` and `require-trusted-types-for 'script'`. The page draws
  every message as text, never as markup.
- **A shell started inside an agent's session.** It starts its page servers there too,
  and the acts then refuse as `moxi comms send` refuses in a session: `Send` and `Check`
  answer `operator-in-session`, and `Take back` answers `no-identity`. That refusal keeps
  a session from taking your lane by mistake. It is not a wall against a process of
  yours.
- **What it doesn't add.** Any process running as you can read the page server's runtime
  file and call the acts with its token, and the page server can't tell that process
  from the shell. That process can already read your comms token from the keychain or
  the token store and post with it, so the send box gives it nothing new while the rule
  above holds.

## Configuration and files

Nothing needs configuring for the default path. Everything that can be set, every
environment variable and every key of the one config file, is in
[docs/configuration.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/docs/configuration.md); `moxi config comms` shows each key with
its value. By default:

- data: `~/.local/share/moxi/comms/`
- state, including comms' store (`comms.db`) and tokens: `~/.local/state/moxi/comms/`
- config: `~/.config/moxi/comms/config.toml`
- sockets: `$XDG_RUNTIME_DIR/moxi/comms/`

How messages are routed, delivered and attributed is in
[docs/protocol.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/docs/protocol.md).

## Troubleshooting

- **A session got nothing.** `moxi comms explain <name>` says whether the name is bound, what
  was delivered, and whether the turn that read it completed.
- **`moxi comms doctor` says the daemon isn't answering.** `moxi doctor` shows its unit's
  state and, when it failed, the line that reads its log. Restart it with `moxi services
  restart comms/daemon`, or with the supervisor (`systemctl --user restart` on its unit).
  Or run the daemon in a terminal: `"$(uv tool dir)/moxi/bin/moxi-comms-daemon"`.
- **The daemon refuses to start because a token is missing.** The token store lost
  credentials the store expects; see [docs/security.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/docs/security.md).

## Contributing

See [CONTRIBUTING.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/CONTRIBUTING.md). Changes are listed in
[CHANGELOG.md](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/CHANGELOG.md).

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-comms-v0.3.0/packages/comms/LICENSE).
