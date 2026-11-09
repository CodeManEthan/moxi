# moxi-memory

Moxi's memory module lets an agent search its past sessions. It keeps a keyword index of
the transcripts Claude Code, Codex and OpenCode write, read where each harness keeps them,
and tells every session at its start that the search is there.

1. **It indexes what was written in each session.** The messages a person or an agent
   wrote and every tool call, with the end of each tool result in a second tier you search
   only when you ask. Every text is redacted with secrets' pattern sets before it is
   stored.
2. **It searches with stemming.** `moxi memory search "flaky render test"` finds `renders`
   and `tests` too, ranks the hits by BM25 and names the command that opens the session
   around each hit's time.
3. **It speaks at session start.** Through turns, each session gets one line saying the
   search exists, the agent's own recent sessions and the project's, and a one-line
   reminder at each prompt.
4. **It keeps up as sessions run.** An ended turn and an ending session each start a
   background read of that session's new lines, and a catch-up walk picks up what no
   trigger saw.

It writes nothing to a transcript, and its index holds nothing the transcripts can't give
back: it can be deleted and built again from them.

```sh
moxi install memory
moxi memory search "why the widget renders twice"
moxi memory status
moxi doctor memory
```

## Commands

| Command | Does |
|---|---|
| `moxi memory search WORDS [--project P] [--since TIME] [--harness H] [--results] [--subagents] [--scratch] [--limit N] [--json]` | search past sessions, best match first (below) |
| `moxi memory ingest [--all]` | start a catch-up walk in the background, or with `--all` a read of every transcript from its start, and return at once |
| `moxi memory reindex [--relabel]` | start a rebuild of the index in the background, and return at once (below) |
| `moxi memory status [--json]` | print the index's state (below) |

Exit codes: 0 a search that ran, with or without a hit, a started ingest or rebuild, and a
status with an index; 1 no index, or one that can't be read; 2 bad arguments or a setting
memory can't use.

### Search

The words are cut into tokens the way the index cuts text: letters, digits and marks make
a token, and spaces, punctuation and symbols only separate tokens. Each token is searched
as written and stemmed (Porter's English rules: `running` finds `runs`), and a hit needs
any one of them, not all. Hits are ranked by BM25; a tie goes to the newer row. Phrase,
prefix and NEAR queries are not offered, and no character you type can break a search. A
query keeps at most 64 tokens of at most 100 characters each.

- `--project P`: only sessions whose project label is P (below).
- `--since TIME`: only rows at or after TIME: an age (`30m`, `12h`, `7d`, `2w`), ISO 8601
  (`2026-11-06`, `2026-11-06T14:03:22Z`; no zone means local time) or epoch seconds.
- `--harness H`: only `claude`, `codex` or `opencode` sessions.
- `--results`: search the ends of tool results too.
- `--subagents`: search subagent sessions too, which are indexed only while
  `index.subagents` is on.
- `--scratch`: search sessions launched as scratch too (below).
- `--limit N`: how many hits, 10 by default.

Each hit is two lines: its number, harness, session id, time (local, to the minute),
project label and what the row is (`user`, `assistant`, `tool`, or `result` for the end
of a tool result, with `failed` when the tool failed and `subagent <id>` on a subagent's
row), then the matching part of the text with each match marked `«...»`. Under the hits
is one command that opens the first hit's session around its time:

```text
moxi sessions read <session> --around 2026-11-06T14:03:22Z
```

For a Codex or OpenCode hit it adds `--harness codex` or `--harness opencode`. `moxi
sessions read` is the sessions module's command; memory has no command of its own that
shows a session.

With no hit, search prints `no match in N rows`, where N counts the rows its filters let
it look through, and exits 0. A search that ran adds one line for each state it can see
that leaves its answer incomplete:

```text
index still filling: 812 of 17012 sources read
rebuild running since 2026-11-06 14:03; sessions since then are not in these results
```

The first shows until the first full read of every harness root has finished (while no
root has been listed yet it says `N sources read, the rest not listed yet`). The second
shows while a rebuild runs: a search reads the index from before it.

With no index, or one that can't be read, search exits 1 and says so on stderr, names
`moxi memory status`, and either says a rebuild is running or names `moxi memory reindex`,
which "starts a detached rebuild; this search can't wait for it". A missing index is never
answered with an empty list.

`--json` prints one object: `tokens`, `rows`, `hits` (each with `harness`, `session_id`,
`agent_id`, `time` in epoch seconds, `at` in UTC ISO 8601, `project`, `kind`, `tier` (`1`,
`2` or `"subagent"`), `failed`, `snippet`, `score` and its own `read` command), `filling`
(`{"read", "listed"}` or null), `rebuilding` (the rebuild's start in epoch seconds, or
null), `read` (the first hit's command) and `notes` (the incomplete-answer lines). With no
index it prints `{"error": "missing" | "unreadable", "message": ...}` and exits 1.

### Ingest and reindex

Neither runs the work itself. Each starts the ingest child, `moxi-memory-ingest`, detached,
prints `started ... in the background (process N)` and `watch it with: moxi memory
status`, and exits 0, so an agent that runs one is never held by it.

`moxi memory reindex` builds a new index from every transcript the harnesses still keep.
Before it starts it says that a rebuild drops every session whose transcript the harness
has deleted since memory read it, and that those sessions stay searchable until the
rebuild ends. Searches keep reading the old index while it runs. When it ends it switches
to the new index, removes the dropped sessions from the session table, records how many it
dropped, and starts one catch-up walk for what was written meanwhile. A rebuild already
running starts nothing. `--relabel` also derives each session's project label again from
its folder where that folder still exists; a session whose folder is gone keeps its label.

### Status

`moxi memory status` prints the index's generation and tokenizer; rows per tier and
harness; sources read, and those not read (compressed Codex rollouts); the time a source
was last read and each harness's last walk; the backlog, the sources changed since the
last walk; whether the first full read has finished; whether a rebuild is running, since
when and its process id; what the last rebuild dropped; and the session table's count by
launch kind and by whether a session has an identity. With no index, or one that can't
be read, it says so, still prints the rebuild and session lines, names `moxi memory
reindex` unless a rebuild is running, and exits 1. `--json` prints the same as one object.

## What each session gets

From the next session on, a Claude Code session, a Codex session started through turns'
Codex host and an OpenCode session under turns' host (experimental in turns) gets these
through turns. A Codex session started by hand, outside turns, gets none of them, but its
transcript is still indexed by the catch-up walk.

At each start, resume, `/clear` and compaction, one line:

```text
memory: `moxi memory search "<words>"` searches past sessions in every project (`--project <label>` for this one). `moxi sessions read <session>` shows part of one of the sessions listed below. Search when the task needs something from an earlier session.
```

`<label>` is the session's project label, quoted for a shell when it needs to be; with no
label the parenthesis is left out. The line is the same for every harness.

After it, two lists of recent sessions, session ids and times only:

```text
your recent sessions (<identity>):
- <session id>  <YYYY-MM-DD HH:MM>
recent sessions in <label>:
- <session id>  <YYYY-MM-DD HH:MM>  <identity or harness>
```

- The first list is the five sessions with the newest activity run under this session's
  comms identity, from any project. It shows only when the session has an identity, which
  needs comms.
- The second is the five newest in this session's project, leaving out those in the first
  list, each named by the identity that ran it, or by its harness when it had none.
- Both leave out this session, subagents, sessions launched as scratch, and sessions
  memory hasn't read a model answer of yet (below). An empty list is left out with its
  heading.
- The time is the newest record memory read of that session, in local time.
- `moxi sessions read <session>` opens a listed session, whichever harness ran it.

The lists come from memory's session table alone, never from the index, and work to a
deadline of 1.5 s inside turns' wait: a busy store or a slow comms answer costs a list,
never the start line. A missing or rebuilding index can't slow a start.

At each prompt of the main loop, unless `reminder` is off:

```text
memory: search past sessions if this turn needs them.
```

A launch through turns also lets the two commands the start line names run without an
approval prompt: Claude Code gets `Bash(moxi memory search:*)` and `Bash(moxi sessions
read:*)`, OpenCode the matching `bash` rules. The second also lets `moxi sessions read
--all` run. Codex has no rule of this kind for one launch, so turns adds nothing there.

## When transcripts are read

Every read is a detached `moxi-memory-ingest` child; no turn, prompt or command waits for
one. One child writes at a time, and a child that finds another writing exits at once and
leaves the work to the next.

- A turn of the main loop ended: that session's new lines, at most once a minute per
  session.
- A session's endpoint is ending: that session to its end, then a catch-up walk.
- A catch-up walk lists each harness root and reads every transcript changed since the
  last walk. It is what reads a Codex session started outside turns, and a session killed
  before it ended.
- The `ingest` timer, off by default, runs a catch-up walk every hour, for a machine where
  sessions rarely end.
- `moxi memory ingest`, `moxi memory reindex`, and setup's offer of the first full read.

Each transcript's read position is kept in the index beside its rows, so a read carries on
where the last one stopped, and a row read twice is stored once.

A failed child (a setting memory can't use, a redaction set that won't run) leaves a
`record ingest` row in `moxi doctor`, which the next child that finishes clears.

## What is indexed

| Tier | What | Kept per row | Searched |
|---|---|---|---|
| 1 | each `user` or `assistant` message of a main session, and each tool call, as its summary line and its input | a message's first 16,384 characters, a tool call's first 2,048 | always |
| 2 | the end of each main-session tool result, with whether the tool failed | the last `index.tail_chars` characters, 1,000 by default | with `--results` |
| subagents | tier 1 rows of subagent sessions, under their main session's id | as tier 1 | with `--subagents`, and only while `index.subagents` is on |

A cut text says how much went: `[... N more characters cut]` after a message, `[... N
earlier characters cut]` before a tail.

Not indexed: system and developer messages; messages the harness says it added itself,
or that a plugin, a channel, another session or a background task submitted, whatever
their role (sessions' `injected`); messages Claude Code flags as meta, and compaction
summaries; reasoning; this module's own start line and reminder; markers, requests and
rate-limit readings; whole tool results; subagent tool results; and Codex threads that
Codex imported from another harness, whose original is that harness's transcript.

A message whose harness doesn't say who submitted it is kept. So a prompt sent through
Claude Code's SDK or `codex exec`, a command line typed at the prompt (`/clear`), and most
user messages in Codex and OpenCode are in tier 1, and a program's text submitted as a
user turn there can't be told apart from a person's.

Subagents are Claude Code's subagent files, the threads a Codex thread spawned, and
OpenCode's child sessions.

### Project labels

A session's project label is the name of the nearest folder at or above its working
folder that holds `.git`. In a linked git worktree it is the main repository's folder
name, so a worktree's sessions share their project's label; for a bare repository it is
the repository's name without `.git`. With no `.git` above, it is the working folder's own
name. The label is fixed when memory first reads the session or sees it start, and a
rebuild keeps it, since by then a worktree may be gone. `moxi memory reindex --relabel`
derives it again where the folder still exists.

### Launch kinds

With the launcher installed, memory asks it how each session was launched. A session the
launcher launched as `scratch` is left out of search, in every tier, unless you pass
`--scratch`, and is in neither start list. `project`, `seat` and `one-off` sessions, and
sessions with no kind (started by hand, by a script, or before the launcher was
installed), are searched and listed. The kind is read from the session table at each
search, so a kind learned later applies at once.

A session joins the start lists once a child has read a model answer in it and the
launcher has answered for it, with a kind or with none; without the launcher, once a child
has read a model answer. A launcher that can't be asked leaves the session out until the
next read. So a session no child has read yet is in no list.

## Redaction

Every text memory stores goes through `moxi_secrets.redact` with the sets in `redact.sets`,
`credentials` and `broad` by default, and is cut to its size only after, so a cut never
leaves part of a secret whose shape no longer matches. A text secrets reports it could not
scan is stored as `[REDACTED:unscanned]` alone. What search prints, and what the library
returns, is the redacted text.

A tool call is a secret-path call when any string in its input, or any word of one, is a
path secrets says must not be read (`.env*`, `auth.json`, anything under `.ssh/`, `*.pem`
and the rest of secrets' list); strings inside lists, nested objects and JSON text count.
Such a call is stored as one line, the tool's name and the path, `Read(/srv/app/.env)`:
its input is not stored and its result gives no tail. The call stays findable; the file's
content is not in the index. An `echo .env` is a false hit that costs that call's input.

**What the default sets let through.** `broad` finds random-looking runs only when they
hold no `/`. A secret that holds a `/`, such as an AWS secret access key, can reach the
index under the default sets. Your own names and personal data are not redacted by
default; the `private-names` and `personal-data` sets are a `redact.sets` change away.

`moxi sessions read`, which opens a session, reads the transcript itself, not the index,
and redacts with its own fixed sets, `credentials` and `broad`: changing `redact.sets`
changes what search shows, not what the read shows. The read has no secret-path rule, so
for a shell command that reads a secret file it shows the command's first 160 characters,
redacted, where the index keeps only the tool and the path.

## How long sessions stay

memory reads transcripts where each harness keeps them, so nothing outlives a harness's
own cleanup for long. The index is derived from the transcripts, and a session leaves it
at the first rebuild after its transcript is gone: until you run `moxi memory reindex`, a
deleted session's rows stay searchable, though `moxi sessions read` can no longer open it.

- **Claude Code** deletes transcripts older than `cleanupPeriodDays` days, set in
  `settings.json` under `${CLAUDE_CONFIG_DIR:-~/.claude}`; unset, 30. To keep sessions
  longer, raise it there. memory never changes it.
- **Codex** deletes no rollout by age. With `features.local_thread_store_compression` on
  in Codex's `config.toml`, Codex compresses rollouts to `.jsonl.zst`, which sessions can't
  read: status and the doctor count the ones memory never read as not read, and a
  rebuild loses what memory had read of a rollout Codex has since compressed. An archived
  thread's rollout moves under `archived_sessions/`, which sessions doesn't read, so the
  next rebuild drops it.
- **OpenCode**: sessions reads no retention setting for it.

Setup prints these lines for each harness, and the doctor's `retention` row repeats them.

## Setup

`moxi install memory` brings turns, sessions and secrets, which memory requires, runs
memory's setup and asks about its timer, then offers the comms and launcher extras. At a terminal setup asks two
questions, each answered no unless you type yes; with nobody at the keyboard it asks
nothing, changes nothing, and prints the command for each. Run `moxi install memory` again
to see it again. In order, it:

1. **Counts the sessions in each harness root** sessions finds, main sessions only, or
   says why a root has none. When it finds Codex sessions that weren't started through
   turns, it says such a session is read only by a catch-up walk, which runs when some
   other session ends, and that one Codex compresses first is never read; and while the
   timer is off, that the `ingest` timer runs that walk every hour.
2. **Says how long each harness keeps transcripts** (above). It changes no retention
   setting.
3. **Offers to turn Claude Code's auto memory off.** When `autoMemoryEnabled` isn't false
   in Claude Code's user `settings.json` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY` isn't set,
   it says Moxi isn't meant to run beside a second memory tool, and asks. On a yes it sets
   that one key to false and keeps the file's mode, a symbolic link (it writes the
   target), and every other key's value; the file is written back from its parsed form, so
   key order and indentation may change, which it says before it asks. It won't write a
   file it can't parse, and says why. With no yes it prints the key to set. When auto
   memory is already off it says nothing. Skipped when `harnesses` leaves out `claude`.
4. **Says when redaction runs on secrets' slower engine**, when the `re2` module of
   secrets' fast engine isn't installed, so the first full read takes longer, with
   `moxi install secrets --with re2`.
5. **Offers the first full read**, every transcript from its start, in the background,
   with `moxi memory status` to watch it; or prints `moxi memory ingest --all`.

Right after setup, `moxi install` asks whether to turn the `ingest` timer on, default no;
`moxi config set memory ingest.timer true` turns it on later.

`moxi uninstall memory` leaves Claude Code's settings as they are and says so: auto memory
stays off if setup turned it off. With `--purge` it offers to remove the session table;
the index files in memory's cache folder are not a declared store, so delete that folder
yourself.

## Settings

`moxi config memory` shows every key; each is read where it is used, so an edit takes
effect at the next read.

| key | default | what |
|---|---|---|
| `harnesses` | `["claude", "codex", "opencode"]` | the harnesses whose transcripts are indexed |
| `index.subagents` | `false` | index subagent sessions too, searched only with `--subagents` |
| `index.tail_chars` | `1000` | how many characters from the end of each tool result tier 2 keeps; at least 1 |
| `redact.sets` | `["credentials", "broad"]` | the secrets sets every stored text is redacted with; at least one |
| `reminder` | `true` | send the reminder at each prompt |
| `ingest.timer` | `false` | run a catch-up walk every hour, outside sessions |

A change to `index.subagents`, `index.tail_chars` or `redact.sets` applies to what is read
after it; `moxi memory reindex` applies it to everything. To turn the start line and
lists off, take `memory` off turns' `start.order`.

memory reads no environment variable of its own. Its folders move with the core's, like
every module's: the session table, the writer lock and the rebuild lock in its state
folder (`~/.local/state/moxi/memory` by default), the index in its cache folder
(`~/.cache/moxi/memory`). `moxi paths memory` prints both.

## The optional links

| Link | Extra | With it | Without it |
|---|---|---|---|
| comms | `comms` (`moxi-comms`) | each session's comms identity (`name`, or `name@scope`), read from comms' local claim socket at start; the agent's own list; agents named in the project list | no identities: no agent list, and the project list names each session's harness |
| launcher | `launcher` (`moxi-launcher`) | each session's launch kind; scratch sessions searched only with `--scratch`, and in no list | no kinds: every session is searched by default and may be listed |

Two sessions share an identity only when comms' qualified names are the same, so
`planner@alpha` and `planner@beta` are two agents. memory reaches each link through
`moxi.modules.load` when it needs it, and a link installed later is used from then on.

## Doctor

`moxi doctor memory` shows these rows, and writes nothing:

| row | ok | otherwise |
|---|---|---|
| `index` | the generation, schema, tokenizer and row counts; also says when the first full read is still filling it, a rebuild is running, older generations are left to remove, or the last rebuild dropped sessions | `warn` with no index yet; `fail` when it can't be read |
| `ingest` | each harness's last walk and the sources changed since, the same count as status's backlog; a harness with no root here has nothing to read | `warn` with no index yet, for a harness root that has no walk yet, or for compressed Codex sessions not read |
| `sets` | memory's redaction sets load in secrets | `fail` for a set secrets doesn't have or can't run, which stops every ingest; `off` when memory's config can't be read |
| `start` | memory is on turns' start list for each harness it reads, and its listener rows import | `warn` when it is off the list; `fail` when a row doesn't import |
| `sessions` | the session table's sessions, with an identity, settled, and by launch kind; or that no session has started or been read yet | `warn` when the table can't be opened |
| `links` | whether comms and the launcher load, and what memory does without each | `warn` when one is installed but fails to load |
| `retention` | each harness's retention, from sessions | `warn` where sessions warns |

A `config` row fails when memory's config can't be read, and a `record ingest` row shows a
failed ingest child.

## Library

`moxi_memory.api` offers interface 1; another module loads it with
`moxi.modules.load("memory", interface=1)`.

- `search(query, *, project=None, since=None, harness=None, results=False,
  subagents=False, scratch=False, limit=10) -> Result`. `since` is epoch seconds or a
  `datetime` (no zone is local time). A `Result` holds `hits` (each a `Hit`: `harness`,
  `session_id`, always the main session's, `agent_id` on a subagent's hit, `time`,
  `project`, `kind`, `tier`, `snippet`, `key`, `score` and `failed`), `rows` (how many rows
  were searched), `tokens`, `filling` (a `Filling(read, listed)` while the first full read
  hasn't finished, else None), `rebuilding` (a running rebuild's start time, else None)
  and `generation`. With no index it raises `IndexMissing`, with one that can't be read
  `IndexUnreadable`; it never answers either with an empty result.
- `status() -> Status`: what `moxi memory status` prints, as fields. It doesn't raise for
  a missing index: `Status.index` is `ok`, `missing` or `unreadable`.
- `cut(query)`: the tokens a query is searched for.

A search only reads: it never takes the lock an ingest writes under, and a rebuild that
switches the index under it leaves the old one for it to read to the end.

## Install

Linux, Python 3.12 or later with an `sqlite3` module that has FTS5, and
[uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install memory
```

The first line installs `moxi`, Moxi's core, which provides the `moxi` command; the second
adds memory with turns, sessions and secrets and runs its setup. The repository's README,
at [CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi` command
and lists the other modules. A session reaches memory through turns, so a harness needs
turns' own setup: see
[turns' README](https://github.com/CodeManEthan/moxi/tree/main/packages/turns).

## Dependencies

`moxi`, Moxi's core; `moxi-turns`, which calls the start line, the lists, the reminder and
the ingest trigger; `moxi-sessions`, through which memory reads every transcript and each
harness's retention, and whose `moxi sessions read` opens a session; and `moxi-secrets`,
the redaction sets. The extras `comms` and `launcher` add the two links above. memory
parses no harness's transcript format itself.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/memory/LICENSE).
