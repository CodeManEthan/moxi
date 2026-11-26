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
5. **It can recall for the agent.** In a session launched with the subconscious on, a
   second agent searches past sessions and notes for each prompt, or for a topic the
   agent names, and its points come back to the main agent as it finds them (below).

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
| `moxi memory search WORDS [--project P] [--since TIME] [--harness H] [--results] [--subagents] [--scratch] [--forks] [--limit N] [--json]` | search past sessions, best match first (below) |
| `moxi memory ingest [--all]` | ask for a catch-up walk in the background, or with `--all` a read of every transcript from its start, and return at once |
| `moxi memory reindex [--relabel]` | ask for a rebuild of the index in the background, and return at once (below) |
| `moxi memory status [--json]` | print the index's state (below) |
| `moxi memory subconscious list [--session ID] [--json]` | print the subconscious's log of recalls (below) |
| `moxi memory subconscious recall TOPIC [--json]` | start a recall on TOPIC, from an agent's shell in an on-demand session (below) |
| `moxi memory subconscious discard RECALL_ID` | end a recall's line whose answer memory still holds to send (below) |

Exit codes: 0 a search that ran, with or without a hit, an ingest or rebuild asked for, and a
status with an index; 1 no index, or one that can't be read; 2 bad arguments or a setting
memory can't use; 3 a `subconscious recall` or `subconscious discard` refused, with the
reason.

### Search

The words are cut into tokens the way the index cuts text: letters, digits and marks make
a token, and spaces, punctuation and symbols only separate tokens. Each token is searched
as written and stemmed (Porter's English rules: `running` finds `runs`), and a hit needs
any one of them, not all. Hits are ranked by BM25; a tie goes to the newer row. Phrase,
prefix and NEAR queries are not offered, and no character you type can break a search. A
query keeps at most 64 tokens of at most 100 characters each.

- `--project P`: only sessions whose project label is P (below).
- `--since TIME`: only rows at or after TIME: an age (`30m`, `12h`, `7d`, `2w`), ISO 8601
  (`2026-11-06`, `2026-11-06T14:03:22Z`; no zone means local time) or epoch seconds. A
  bare number of four digits or fewer, such as `2026`, is refused rather than read as a
  moment in 1970; give `2026-01-01` for a year.
- `--harness H`: only `claude`, `codex` or `opencode` sessions.
- `--results`: search the ends of tool results too.
- `--subagents`: search subagent sessions too, which are indexed only while
  `index.include_subagents` is on.
- `--scratch`: search sessions launched as scratch too (below).
- `--forks`: search sessions launched as forks of another session too (below).
- `--limit N`: how many hits, 10 by default.

Each hit is two lines: its number, harness, session id, time (local, to the minute),
project label and what the row is (`user`, `assistant`, `tool`, or `result` for the end
of a tool result, with `failed` when the tool failed and `subagent <id>` on a subagent's
row; a delivered message shows who delivered it in place of `user`: `hook` for a plugin's,
comms mail among them, or `channel`, `peer`, `coordinator` or `task`), then the matching part of the text with each match marked `«...»`. Under the hits
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
shows while a rebuild runs: a search reads the index from before it. A third, `N
transcript folders not read yet: sessions there are not in these results`, shows while an
archive folder (below) has never been listed whole by a walk it answered. A folder a walk
has read before that doesn't answer now is not named on every search, since only what it
gained since is missing; `status` and the doctor name it.

With no index, or one that can't be read, search exits 1 and says so on stderr, names
`moxi memory status`, and either says a rebuild is running or names `moxi memory reindex`,
which "starts a detached rebuild; this search can't wait for it". A missing index is never
answered with an empty list. When the index reads but memory's state store (`state.db`,
which holds the session table) doesn't, search exits 1 the same way and names that file;
a rebuild doesn't repair it.

`--json` prints one object: `tokens`, `rows`, `hits` (each with `harness`, `session_id`,
`agent_id`, `time` in epoch seconds, `at` in UTC ISO 8601, `project`, `kind`, `writer`
(the row's writer, `person`, `model`, `hook` and so on, or null for a result's end),
`tier` (`1`, `2` or `"subagent"`), `failed`, `snippet`, `score` and `read_command`, the command that
opens its session), `filling` (`{"sources_read", "listed"}` or null), `rebuild_started_at`
(the rebuild's start in epoch seconds, or null), `top_read_command` (the first hit's
`read_command`) and `notes` (the incomplete-answer lines). With no
index it prints `{"error": "missing" | "unreadable", "message": ...}` and exits 1.

### Ingest and reindex

Neither runs the work itself. Each starts the ingest child, `moxi-memory-ingest`, detached,
and exits 0, so an agent that runs one is never held by it. The child records what was
asked for before it tries memory's writer lock, so another ingest holding that lock
delays the work but never loses it: the ingest that holds it starts the work when it
ends. Two requests made while the lock is held run once, as the larger of the two: a
relabel covers a rebuild, a rebuild covers a full read, and a full read covers a walk.

The command can't see whether an ingest holds the lock, only whether a rebuild is
running, so it never says the work started. It prints `asked for ... in the background
(process N)`, then either that the work is queued behind the rebuild that started at a
given time and runs when it ends, or that it runs now or, if an ingest holds memory's
writer lock, as soon as that ingest ends; and `watch it with: moxi memory status`, which
shows a request still waiting.

`moxi memory reindex` builds a new index from every transcript in every folder that
answers. Before it asks it says that a rebuild drops a session only when its transcript is
gone from every folder of its harness and every one of those folders answered the
rebuild, and that those sessions stay searchable until the rebuild ends. It names each
folder that didn't answer the last run, and why, whose harness's sessions will be kept if
it still doesn't answer; each folder walked before that sessions' config no longer
names, whose sessions will leave unless another folder holds them (a misspelt archive key
shows here); and each folder the last rebuild didn't read, with the advice to run
`reindex` again once it answers. Searches keep reading the old index while it runs. When
it ends it switches to the new index, removes the dropped sessions from the session table,
records how many it dropped and how many it kept, and starts one catch-up walk for what
was written meanwhile, or the waiting request, which reads everything anyway.

**What a rebuild keeps.** A rebuild that finds a folder not answering (an archive folder
offline, a permission or read error) lists and reads that harness once more before it
decides. A harness with a folder that still didn't answer keeps
every session it read no file for: the rebuild copies the session from the index it
replaces (rows, tails, read positions and secret-path calls), with today's `harnesses`,
`index.include_subagents`, `redact.sets` and tier sizes applied to what is copied and the
session table's label on each row, and keeps its session table row. A session it began
reading and couldn't finish is taken out of the new index and copied whole the same way. A
Codex session found only as compressed rollouts, which sessions lists and doesn't read,
counts as found and is copied too. What needs the transcript itself (a larger size, a set
taken away, a new tier rule) reaches a kept session only at a rebuild that reads it. A
harness config `harnesses` leaves out keeps its session table rows, and its rows leave the
index.

**Sessions waiting on a folder.** Each source a rebuild or a walk couldn't read because
its folder raised waits on that folder, recorded in memory's state store. Every later
walk reads it again from its start, whether or not the file changed, once the folder
lists again, so a session a rebuild kept without reading it gets its rows written again
with their writer. A source stops waiting when a read reaches its end, or when its
folder answers and the file is gone. One that exists and keeps failing waits on, and
`moxi doctor memory`'s `index` row counts the sessions waiting. A compressed Codex
rollout never waits: sessions can't read it, at a walk or at a rebuild.

`--relabel` also derives each session's project
label again from its folder where that folder still exists; a session whose folder is gone
keeps its label.

**After secrets' `redact` changes.** No ingest rebuilds the index, and none redacts again
the rows already stored: an ingest stores its new rows under the `redact` installed now
and leaves the rest as they are. Each index records which `redact` stored its rows
(secrets' `REDACT_VERSION`; an index without the record counts as version 1). When an
upgrade brings a secrets whose version is higher, the doctor's `index` row warns, and the
fix is to run `moxi memory reindex` once. That rebuild is a version rebuild: before it
asks, `reindex` says that it redacts every row again, drops no session and no row of a
source it can't read to its end, keeps those rows redacted again and trimmed at their
stored cuts, that a row of a source it reads to its end which the source no longer gives
still leaves (unless its session has a source it can't read), and that a later
`reindex` drops what a rebuild drops. The version rebuild is described under Redaction.

### Status

`moxi memory status` prints the index's generation and tokenizer; rows per tier and
harness; sources read, and those not read (compressed Codex rollouts); the time a source
was last read and each harness's last walk; the backlog, the sources changed since the
last walk; whether the first full read has finished; whether a rebuild is running, since
when and its process id; a request waiting for the writer lock (walk, full read, rebuild
or relabel) and when it was asked; what the last rebuild dropped and kept; each folder
that needs saying (one that didn't answer the last run, with why and since when, an
archive folder not listed whole yet, one the last rebuild didn't read, and one sessions'
config no longer names); and the session table's
count by launch kind and by whether a session has an identity, or that memory's state
store, which holds the session table, can't be read (the index line is apart from it).
For an index made before memory kept delivered messages (below), a `delivered messages:`
line says it holds none for the sessions it had read and names `moxi memory reindex`;
`--json` gives `keeps_delivered` false. With no index, or one that can't be read, it says so, still prints the rebuild and session lines, names `moxi memory
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
- Both leave out this session, subagents, sessions launched as scratch or as forks, and
  sessions memory hasn't read a model answer of yet (below). An empty list is left out with its
  heading.
- The time is the newest record memory read of that session, in local time.
- `moxi sessions read <session>` opens a listed session, whichever harness ran it.

The lists come from memory's session table alone, never from the index, and work to a
deadline of 1.5 s inside turns' wait: a busy store or a slow comms answer costs a list,
never the start line. A missing or rebuilding index can't slow a start.

At each prompt of the main loop, unless `reminder.enabled` is off:

```text
memory: search past sessions if this turn needs them.
```

A launch through turns also lets the two commands the start line names run without an
approval prompt: Claude Code gets `Bash(moxi memory search:*)` and `Bash(moxi sessions
read:*)`, OpenCode the matching `bash` rules. The second also lets `moxi sessions read
--all` run. Codex has no rule of this kind for one launch, so turns adds nothing there.

## When transcripts are read

Every read is a detached `moxi-memory-ingest` child; no turn, prompt or command waits for
one. One child writes at a time, and a child that finds another writing exits at once. A
turn's or a timer's child leaves its work to the next walk, which reads every transcript
that grew. Work someone asked for (`moxi memory ingest`, `reindex`, setup's first full
read) waits instead, and the child writing starts it when it ends. A child killed while it
runs that work doesn't run it again; a killed rebuild shows in the doctor's `index` row.

- A turn of the main loop ended: that session's new lines, at most once a minute per
  session.
- A session's endpoint is ending: that session to its end, then a catch-up walk.
- A catch-up walk lists each folder of each harness (its root, then its archive folders)
  and reads every transcript changed since that folder's last walk. It is what reads a
  Codex session started outside turns, and a session killed before it ended. A turn's or
  an endpoint's read asks the root only, and waits on no archive folder.
- The `ingest` timer, off by default, runs a catch-up walk every hour, for a machine where
  sessions rarely end.
- `moxi memory ingest`, `moxi memory reindex`, and setup's offer of the first full read.

### Archive folders

Older transcripts can be kept outside a harness's own root, copied out before its cleanup
deletes them or kept on another disk. They are sessions' feature: name them in sessions'
`claude.archives` and `codex.archives` (`moxi config sessions`), and memory reads them
through sessions after the root, a session an earlier folder holds read from that folder
only. Nothing memory or sessions does writes in an archive folder.

A folder can be offline (an unplugged disk, a network mount that doesn't answer), and
offline is not gone. A folder **answered** a walk or a rebuild only when it was listed at
the start, nothing in it failed at any depth (a directory that can't be listed, a file
that can't be read, a file listed and then gone), and it still answered when sessions
tested it again just before the run's decision. A folder whose top level lists nothing
didn't answer while memory has read sessions from it ("lists nothing, though memory read
N sources from it"), or once any listing showed it holding an entry, a session another
folder shadows included ("lists nothing, though it held entries at <time>"): an unmounted
disk can leave its mount point an empty directory, and an archive's copies are mostly
shadowed, since they were copied out before the harness's cleanup and memory read them
from the live root. A folder never seen holding anything answers, and so does a root that
doesn't exist, was never read from and was never seen holding an entry. If you empty an
archive folder on purpose, take it off sessions' config. Each folder's answer, why it
didn't answer, since when and its first error are kept in the index, and `status`,
`reindex` and the doctor's `folders` row show them.

A folder that answered moves its last walk to the walk's start. One that failed during a
walk moves it too and keeps the paths that failed, which the next walk reads again by
their session, so one bad file doesn't make every walk list the folder from an old time;
it stays not answered while any fail. One that was offline keeps its last walk, so the
next walk it answers lists it from there, and an archive folder first seen offline is
listed whole once it answers. The first full read finishes on the harness roots, whatever
the archive folders do.

Each run tests each archive folder twice at most, at its start and before its decision,
each test one wait (`archives.answer_wait_s` in sessions, 5 s) however many folders don't
answer. Once a folder answered, a mount that hangs partway can hang the child reading it,
which then holds the writer lock until the mount answers; a request made meanwhile waits,
and `status` shows it.

Each transcript's read position is kept in the index beside its rows, so a read carries on
where the last one stopped, and a row read twice is stored once.

A failed child (a setting memory can't use, a redaction set that won't run) leaves a
`record ingest` row in `moxi doctor`, which the next child that finishes clears.

## What is indexed

| Tier | What | Kept per row | Searched |
|---|---|---|---|
| 1 | each `user` or `assistant` message of a main session, delivered messages included, and each tool call, as its summary line and its input | a message's first 16,384 characters, a tool call's first 2,048 | always |
| 2 | the end of each main-session tool result, with whether the tool failed | the last `index.tail_chars` characters, 1,000 by default | with `--results` |
| subagents | tier 1 rows of subagent sessions, under their main session's id | as tier 1 | with `--subagents`, and only while `index.include_subagents` is on |

A cut text says how much went: `[... N more characters cut]` after a message, `[... N
earlier characters cut]` before a tail.

Delivered messages are indexed: what a plugin (comms mail among them), a channel,
another session (a peer or a coordinator) or a background task put into the session,
each row naming who delivered it (sessions' `writer`: `hook`, `channel`, `peer`,
`coordinator`, `task`), also when Claude Code flags the line as meta. An index made
before memory kept them holds none for the sessions it had read; `moxi memory status`
and `moxi doctor memory` say so, and one `moxi memory reindex` reads them in. Nothing
reindexes by itself.

Not indexed: system and developer messages; other messages the harness says it added
itself, or flags as meta (sessions' writer `harness`), and compaction summaries; the
subconscious's recall answers (below); reasoning; this module's own start line and
reminder; markers, requests and rate-limit readings; whole tool results; subagent tool
results; and Codex threads that Codex imported from another harness, whose original is
that harness's transcript.

A message whose harness doesn't say who submitted it is kept. So a prompt sent through
Claude Code's SDK or `codex exec`, a command line typed at the prompt (`/clear`), and most
user messages in Codex and OpenCode are in tier 1, and a program's text submitted as a
user turn there can't be told apart from a person's.

Subagents are Claude Code's subagent files, the threads a Codex thread spawned, and
OpenCode's child sessions. A subagent that ran as a subconscious recall's fork is never
indexed, even with `index.include_subagents` on: it holds a copy of its parent's
conversation. memory knows it by the fork id in its recall log, and takes out any rows
of it read before that id was written.

A recall's answers are not indexed either. On Claude Code each answer reaches the main
session as a background task's notice (`<task-notification>`) whose `<task-id>` is the
fork's id; memory leaves out every notice whose task id its recall log names, so search,
a later recall's first search and the chat's `search_memory` never return an earlier
recall's answer. Other notices, a subagent's or a background command's, are indexed as
before; a message that holds several notices keeps all but the recall's. A notice that
comes in before its fork's id reaches the log is still left out when memory reads the
log again within a minute of the notice.

On a route that answers by comms, each answer comes back as comms mail, and the recall
log keeps the id comms gave each one it took. memory leaves out every delivered comms
message whose id the log names, as comms reads the delivered text back (memory doesn't
read comms' delivery text itself); other mail in the same delivery is kept, and mail is
never left out for its sender's name alone. Without comms there is no such mail.

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
`--scratch`, and is in neither start list. A session launched as a `fork` (`moxi launcher
fork`, a copy of another session's whole conversation) is treated the same way, found
only with `--forks`. `project`, `seat` and `one-off` sessions, and
sessions with no kind (started by hand, by a script, or before the launcher was
installed), are searched and listed. The kind is read from the session table at each
search, so a kind learned later applies at once.

A session joins the start lists once a child has read a model answer in it and the
launcher has answered for it, with a kind or with none; without the launcher, once a child
has read a model answer. A launcher that can't be asked leaves the session out until the
next read. So a session no child has read yet is in no list.

## Redaction

Every text memory stores goes through `moxi_secrets.redact` with the sets in `redact.sets`,
`credentials` and `broad` by default, and is cut to its size only after, so a cut doesn't
leave part of a secret whose shape no longer matches. A long text is redacted a window at a
time, and the window grows until its redacted form runs 64 KiB past the cut, so only a
secret longer than 64 KiB that straddles the window's edge could leave a part in what is
kept. A text secrets reports it could not scan is stored as `[REDACTED:unscanned]` alone.
What search prints, and what the library returns, is the redacted text.

secrets' `redact` keeps no secret whole for text beside it: a secret on a line marked
`gitleaks:allow`, or on a line one of a rule's line allowlists names, is redacted. It also
reads past the characters a renderer may show as nothing (controls, format characters
such as the zero-width space and the soft hyphen, and the rest Unicode names
default-ignorable), so a token one of them splits is redacted too, the characters inside
it included, and every character outside a secret is stored as it was. It reads past the
backslashes that escape a quote as well, so a key quoted inside a command in a tool
call's input (`{\"key\": \"...\"}`) is redacted, the backslashes left in place.

**Rows stored under an older `redact`.** Rows already in the index were redacted by the
`redact` installed when they were stored. The rebuild a typed `moxi memory reindex` runs
over an index whose `redact` version is below the installed secrets' redacts every row
again. It reads every source from its start, as any rebuild does, so a source it reads to
its end gives its rows afresh. Then, source by source, it keeps everything of each source
it didn't read to its end: a deleted transcript or subagent file, a file in a folder that
didn't answer, a compressed rollout, a harness `harnesses` leaves out, subagents' files
with `index.include_subagents` off, an imported Codex thread, a read that failed. It keeps
their rows and tails, read positions and secret-path calls, and every session in the
session table, so it drops no session. A row is kept when any source that may have given
it is one it didn't read to its end, so in a session with such a source, a row a source it
did read no longer gives is kept too. Each kept text is redacted again with today's
`redact.sets`, cut to today's sizes and given the session table's label. A kept text
that was cut first loses its cut edge: from the cut to the first whitespace at least 64
characters away, or to the text's other end when there is none, and its marker counts
what went, since a secret the older `redact` let through could straddle the cut and leave
a fragment no rule matches. The characters `redact` reads past count nothing toward the
64 and don't end the span. A secret written over several lines, cut, from a source that
is gone, keeps the lines further than that from the cut. Kept rows of a folder that
didn't answer stay trimmed until a later rebuild reads that folder.

A tool call is a secret-path call when any whitespace-separated word of a string in its
input is a path secrets says must not be read (`.env*`, `auth.json`, anything under
`.ssh/`, `*.pem` and the rest of secrets' list); strings inside lists, nested objects and
JSON text count, and a word is also split at `=`, `<`, `>`, `|`, `;`, `&`, `,`, `:`,
quotes, backticks, parentheses, brackets, braces and NUL. Such a call
is stored as one line, the tool's name and the path, `Read(/srv/app/.env)`: its input is
not stored and its result gives no tail. The path is the one word that matched, so
`cat > /srv/app/.env <<EOF ...` is stored as `Bash(/srv/app/.env)`, and a path with a space
in it shows only its last part. The call stays findable; the file's content is not in the
index. An `echo .env` is a false hit that costs that call's input.

**What the default sets let through.** `broad` finds a random-looking run that holds a
`/` or `+`, such as an AWS secret access key, but about 2 in 100 random 40-character
base64 runs pass it by chance (secrets' README gives the rule). Rows stored before
secrets had that rule keep such a run as it was until the next `moxi memory reindex`,
which reads every transcript again from its start; a session the rebuild keeps without
reading it (one in a folder that didn't answer, or a compressed Codex rollout) keeps its
rows as they were stored. Your own names and personal data are not redacted by default;
the `private-names` and `personal-data` sets are a `redact.sets` change away.

`moxi sessions read`, which opens a session, reads the transcript itself, not the index,
and redacts with its own fixed sets, `credentials` and `broad`: changing `redact.sets`
changes what search shows, not what the read shows. The read has no secret-path rule, so
for a shell command that reads a secret file it shows the command's first 160 characters,
redacted, where the index keeps only the tool and the path.

## How long sessions stay

memory reads transcripts where each harness keeps them, so nothing outlives a harness's
own cleanup unless a copy is in an archive folder. The index is derived from the
transcripts, and a session leaves it at the first rebuild that finds its transcript in no
folder of its harness while every one of them answers: until you run `moxi memory
reindex`, a deleted session's rows stay searchable, though `moxi sessions read` can no
longer open it.

- **A folder that doesn't answer keeps its harness's sessions.** While one folder of a
  harness is offline, or keeps an error such as a file memory may not read, no rebuild
  drops a session of that harness, deleted on purpose or not, and there is no other way
  to take one out. The doctor's `folders` row names the folder and the path.
- **Claude Code's own cleanup during a rebuild.** Claude Code deletes old transcripts when
  it starts, and those are the ones a long rebuild reads late. A transcript deleted
  between the listing and its read is an error in the root, so that rebuild drops no
  Claude Code session; run `reindex` again, as `status` says.
- **The version rebuild drops nothing.** The one rebuild after an upgrade that raises
  secrets' `redact` version keeps every session and every row of a source it can't read
  (see Redaction). The rebuild after it is an ordinary one again: it drops each session
  whose transcript is gone, and the rows it kept of a gone source in a session it reads.
- **A session read from an archive copy** loses the lines written after the copy was made
  once the live file is gone, since it is read from the first folder that holds it.

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

## The subconscious

The subconscious is memory's second way to recall. Where search waits for the agent to
ask, a recall runs beside the agent: a second agent reads past sessions and notes for
what the prompt is about and sends the main agent the points worth knowing, while the
main agent works. Each recall spends tokens, so it is off unless a launch turns it on.

**Turning it on.** The launcher's `subconscious` row sets it for one launch, or for every
launch of a preset that sets it:

```sh
moxi launcher run --subconscious automatic
moxi launcher run --subconscious on-demand
```

The launch stamps the mode in the session's environment (`MOXI_MEMORY_SUBCONSCIOUS`). A
session with no stamp, such as one started by hand, has it off. The launcher's
`subconscious_route` row picks where its recalls run, `subagent` (the default) or `slot`
(`--subconscious-route slot`), and the launch stamps it beside the mode
(`MOXI_MEMORY_SUBCONSCIOUS_ROUTE`).

- `automatic`: each prompt `subconscious.trigger` counts starts a recall. By default that
  is the user's own prompts.
- `on-demand`: the agent starts one with `moxi memory subconscious recall "<topic>"`, at
  most `subconscious.on_demand_per_prompt` times between two counted prompts. The session
  gets one line at its start that names the command. The command returns at once; the
  points come back to the agent as the recall finds them. It exits 3 with the reason
  when it refuses: the subconscious off or automatic in this session, the count used up,
  memory's config file in error, or the session itself a recall's slot fork.

**Where it runs.** A recall runs on one of two routes:

- `subagent`, on Claude Code: a subagent forked from the session through turns' `fork`,
  so it starts from the session's own conversation and reads its prompt cache. Each
  answer reaches the main agent the way any subagent's answer does.
- `slot`: the launcher forks the session into a tmux slot you can watch, with the
  recall's id stamped on the fork (`MOXI_MEMORY_SUBCONSCIOUS_FORK`), and each answer
  comes back by comms (below). It needs the launcher (its interface 3), slots that run
  here, and comms with scoped senders and a comms name for the session. A session
  launched with `slot` runs it, and so does a Claude Code session on `subagent` whose
  turns offers no `fork`.

On Codex only the slot route runs a recall, and only where its launch picks it: a Codex
session on `subagent` has the subconscious off (`Codex: off unless the launch picks the
slot route`). A Codex slot fork doesn't read the session's prompt cache, and it reads
through the shell alone, since Codex has no read tools. memory's side of a Codex host
fork (a forked thread of the Codex session, `host-fork` in the log, its answers sent by
comms, a late answer sent too, nothing to exit) is in place, and no session is routed
to it.

A session where no route can run has the subconscious off. memory checks this at every
try, not once. When a try finds no route, it writes an `off` line to its log with the
reason, at the first such try and again only when the reason changes, the on-demand
command's refusal says it, and memory's doctor names each live session it holds for.
The reasons include no turns `fork` (with the slot route's own reason beside it, where
that can't run either), no slots here, a launcher without interface 3, comms not
installed or without scoped senders, no comms name for the session, and a recall sender
in conflict, which names the sender.

**One recall.** memory's prompt listener returns at once and starts a detached child,
so a recall never holds the prompt. The child:

1. reads the prompt's keywords (common words left out) and runs a first search in
   `subconscious.scope`, keeping the best `subconscious.prefetch_hits` hits, leaving out
   this session, subagents and fork launches, plus up to three Markdown notes files that
   hold the most of the keywords (with notes installed), each shown line cut at 200
   characters. Under `project-first` the search has two parts, both run every time: the
   project's best hits and its notes directory's best files, then the whole index's best
   hits and the notes root's best files that the project's part didn't already show,
   each part under its own heading in the brief. The notes are read whole, however many
   files there are, and a notes directory that doesn't exist counts as none. A prompt
   with no keyword gets no first search;
2. writes the fork's brief with those hits, the rules below and this session's id;
3. forks the session with the brief, by its route. The fork answers as soon as it has
   one useful point (from the first search alone when that holds one), at most
   `subconscious.max_points` points, or `NOTHING`;
4. asks for further rounds, each a turn of the same fork, until an answer is `NOTHING`
   or ends `End of recall.`, `subconscious.max_rounds` rounds have run, the fork has made
   `subconscious.max_tools` tool calls over all its rounds, or `subconscious.limit_s` has
   passed. Each round is bounded at `limit_s` plus 30 seconds from its own send.

**Answers by comms.** On the slot route the child sends each answer to the session alone,
as an interrupt that doesn't expire, under the session's recall sender: comms' scoped
sender `<comms name>-subconscious` (`builder-subconscious@acme` for `builder@acme`),
which memory declares to comms with the word `subconscious`. The word is declared once
memory is installed beside comms, whether or not anyone turns the subconscious on, and
from then no session or program on the machine can take a name of the form `<comms
name>-subconscious` (comms refuses the binding or the send-only name). Where one already
held such a name, it stays its holder's, and that one parent's recall sender is in
conflict: the slot route is off for its session with that reason, memory's doctor names
the sender, and `moxi doctor` names the holder. The message is the fork's
answer as it wrote it, cut on a line at 4,000 characters; `NOTHING` sends nothing. It
goes to the comms name the session holds when it is sent, or, once the session has ended
and holds none, to the name it held when the recall started, which comms keeps the
message for until a session holds that name again. The line reads `sending` until comms
takes the message, then `back`. A send comms refuses is
tried again every 10 seconds for a minute, then every 5 minutes, with no last try, each
refusal's reason on the line. On this route the child waits out each round's bound
itself. A round past its bound is noted on the line (`past_bound`) with its loss, and
the child waits up to 300 seconds more: an answer that comes in that time is still sent
(`sending`, the round marked `late`), and the record stays as the bound set it. Past
that the line is left for a taker, which settles it once the fork's round ends and never
only acknowledges such a round.

On Codex the message arrives after the main agent's answer and before any later prompt,
as one more request inside the same Codex turn, so a reader of that turn's final answer
sees the reply to the recall, not the answer before it.

Under `user-and-comms`, a comms delivery whose senders are all this session's recall
senders doesn't count: it starts no recall and resets no on-demand count. When a
delivery has other senders as well, the lines memory already sent this session are taken out of the
query and given to the fork as already recalled.

**The slot fork's end.** Once its recall's last round is settled and its answer sent,
the child exits the fork through the launcher's exit-session, and notes how on the line
(`fork_exit`: `exited`, `at-dialog`, `not-exited`, `held: <what holds it>`, or the
refusal). When comms refuses the send of that last answer, the fork is exited at the
refusal, its last turn having ended, so the slot isn't held while the send is tried
again. An exit
the launcher holds (a person in the slot's pane, a background shell) is asked again
every 5 seconds for at most a minute, then left: the fork keeps its slot, memory's
doctor lists it with its session, slot and age, and memory never ends it on its own;
`moxi launcher exit-session SESSION_ID` ends it once it is quiet. A taker that settles a
slot line ends its fork the same way, and a discard of a line whose owner is gone ends
it too, asking once. A line left at the late wait's last limit keeps its fork, whose
round is still open. The launcher's park leaves a recall's fork running and reserves no
slot for it, and neither restore nor crash discovery brings one back.

**What the fork may do.** turns guards hold every recall fork to reads, and run only in
sessions with the subconscious on or in a recall's slot fork. A fork may call Read, Grep and Glob, and the
shell only for a plain read: one command, or a pipeline of commands joined by `|`, each
starting with an entry of `subconscious.fork_commands` word for word, in the foreground
with no timeout of its own. Anything the guard can't see whole (`;`, `&`, redirection,
substitution, quoting tricks and the like) is refused. Only Claude Code's `Bash` tool
counts as the shell: its `PowerShell` tool (on Windows) and `Monitor` are refused, so a
fork with no `Bash` reads with Read, Grep and Glob alone. Every permission ask a recall
fork raises is refused rather than shown to you; a fault in that guard refuses the ask
too. Once the recall's tool calls or time are used up, every further call is refused
with a line telling the fork to answer from what it has.

The guards run in a session launched on the subagent route (`MOXI_MEMORY_SUBCONSCIOUS_ROUTE`
unset or `subagent`). A fork's calls are known by its agent id once the child has
written it. Before that, a subagent call from an unknown agent id is judged as the fork
of the newest recall still waiting for its fork (newest by the order the records were
written). A recall waits only while the process that holds it, named on its record
from its first write (the starter, then the child), still runs, and at most
`subconscious.limit_s` plus 60 seconds from its start (120 seconds at the defaults); a
record whose holder could not be named waits 30 seconds. On Linux these ages and the
holder's liveness are read on clocks a wall-clock step doesn't move; elsewhere on the
wall clock, a record's start later than now read as now.

On the slot route two more guards hold the fork to the same reads, in the fork's own
session (stamped `MOXI_MEMORY_SUBCONSCIOUS_FORK`): `memory.subconscious-slot` judges
every call of a Claude Code or Codex slot fork against its recall, found by the stamped
id (a fork whose record isn't found has every call refused, and a fault in the judge
refuses the call), and `memory.subconscious-slot-ask` refuses every permission ask of a
Claude Code slot fork. Codex names its shell `Bash` to the guard, so a Codex fork's shell
calls meet the same plain-read rule; `apply_patch` and every other Codex tool are
refused. Codex's hook doesn't tell the guard how long a command may run before it yields,
so on Codex a plain read that doesn't end (`tail -f`) goes on running in the background
until the slot fork exits with its recall.

**The log.** `moxi memory subconscious list` prints one line per recall (its id, state,
start, session, how it started, the head of its prompt or topic, its route, rounds and
the reason of a `lost` state) and one per stretch a session's subconscious was `off`.
`--session ID` keeps one session's lines and prints each round's answer. A line is
`open` while its round runs, `sending` while comms hasn't yet taken an answer (with
how long it has waited and comms' last refusal), `back` once an answer came, `lost` when
none did, `discarded` or `off`. A `lost` line shows its reason and its error. The reason is
`config` (the child's read of memory's config was refused), `spawn` (the child could not
be started), `hurried` (a newer recall of the session started before this one asked its
fork), `gone` (the fork went away with no answer, or its child died and turns kept no
end), or otherwise the first word of turns' own reason for the round: `denied` (the
harness refused to start the fork), `untied`, `expired`, `unanswered`, or a refusal such
as `busy-agents`. Each round's end stays with turns
until memory's line holds it. If a recall's child dies, the next recall child of any
session, or a taker memory starts when that session ends, finishes its line from what
turns kept. A recall's working record is removed after a day unless its line still
waits on turns. The log itself is never swept: memory leaves each recall's fork out of
its index by the fork id the log holds, and the answers it hands back too, so a sweep
added later must keep those ids. Each
recall child and each read of a subagent transcript reads the whole log, so that work
grows with every session that ever ran the subconscious.

`moxi memory subconscious discard RECALL_ID` ends a line still `sending`, an answer comms
will never take: the line reads `discarded` and memory sends no more of it. A late
answer's discard leaves the record as the round's bound set it. With the line's owner
gone, it also ends the line's slot fork, asking the launcher once and noting how on the
line. On Claude Code's subagent route the harness brings every answer back itself, so no
line there ever waits and the command refuses each one.

## Setup

`moxi install memory` brings turns, sessions and secrets, which memory requires, runs
memory's setup and asks about its timer, then offers the comms and launcher extras. At a terminal setup asks two
questions, each answered no unless you type yes; with nobody at the keyboard it asks
nothing, changes nothing, and prints the command for each. Run `moxi install memory` again
to see it again. In order, it:

1. **Counts the sessions in each harness root and archive folder** sessions finds, main
   sessions only, or says why a root has none and which archive folders don't answer. When it finds Codex sessions that weren't started through
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
   memory is already off it says nothing. Skipped when `harnesses` leaves out `claude`,
   and on a machine with no Claude Code, where neither its settings file nor its folder
   exists.
4. **Offers the first full read**, every transcript from its start, in the background,
   asked for as `moxi memory ingest --all` asks, with `moxi memory status` to watch it;
   or prints `moxi memory ingest --all`.

Right after setup, `moxi install` asks whether to turn the `ingest` timer on, default no;
`moxi config set memory ingest.timer.enabled true` turns it on later.

`moxi uninstall memory` leaves Claude Code's settings as they are and says so: auto memory
stays off if setup turned it off. With `--purge` it removes the index (the `index`
folder in memory's cache folder), which a reindex can make again, and offers to remove the
session table, which can't be made again.

## Settings

`moxi config memory` shows every key; each is read where it is used, so an edit takes
effect at the next read.

| key | default | what |
|---|---|---|
| `harnesses` | `["claude", "codex", "opencode"]` | the harnesses whose transcripts are indexed |
| `index.include_subagents` | `false` | index subagent sessions too, searched only with `--subagents` |
| `index.tail_chars` | `1000` | how many characters from the end of each tool result tier 2 keeps; at least 1 |
| `redact.sets` | `["credentials", "broad"]` | the secrets sets every stored text is redacted with; at least one |
| `reminder.enabled` | `true` | send the reminder at each prompt |
| `ingest.timer.enabled` | `false` | run a catch-up walk every hour, outside sessions |
| `subconscious.trigger` | `user` | which prompts count as new work: `user`, the user's own; `user-and-comms`, those and comms deliveries. In automatic mode each starts a recall; in on-demand mode each resets the agent's count |
| `subconscious.scope` | `project-first` | where a recall looks: `project`; `project-first`, every search covering the project and everything beyond it, the project's results first; or `everywhere`; a session with no project label (its folder unknown) searches everywhere under each |
| `subconscious.limit_s` | `60` | how long a recall may run, all its rounds, in seconds, 1 to 3600; each round is also bounded at this plus 30 |
| `subconscious.max_tools` | `8` | the most tool calls a recall's fork may make over all its rounds; at least 1 |
| `subconscious.max_rounds` | `4` | how many rounds a recall may run, each answer delivered; at least 1 |
| `subconscious.max_points` | `5` | the most points the brief allows in one answer; at least 1 |
| `subconscious.prefetch_hits` | `8` | how many hits the first search puts in the brief from each of its parts (the project's, and under `project-first` the one beyond it); 0 runs none |
| `subconscious.on_demand_per_prompt` | `2` | in on-demand mode, the most recalls between two counted prompts; 0 refuses every one |
| `subconscious.fork_commands` | `moxi memory search`, `moxi sessions read`, `grep`, `cat`, `head`, `tail`, `ls`, `wc`, `cut` | the shell commands a recall's fork may run; a program you add must not run another from its arguments |

A change to `index.include_subagents`, `index.tail_chars` or `redact.sets` applies to what is read
after it; `moxi memory reindex` applies it to everything. To turn the start line and
lists off, take `memory` off turns' `start.order`. A recall runs on the settings memory's
listener last read cleanly: a config file edited into error leaves running sessions on
the last good settings (on the defaults when none read cleanly yet), the listener logs
the error as a warning, once per error, and a recall child that must read the
file itself and can't stops with its line `lost` (`config`).

memory reads three environment variables of its own: `MOXI_MEMORY_SUBCONSCIOUS` and
`MOXI_MEMORY_SUBCONSCIOUS_ROUTE`, which the launcher stamps on a session launched with
the subconscious on (its mode, and its route; unset means `subagent`), and
`MOXI_MEMORY_SUBCONSCIOUS_FORK`, which memory has the launcher stamp on a recall's slot
fork (the recall's id). Its folders move with the core's, like
every module's: the session table, the writer lock and the rebuild lock in its state
folder (`~/.local/state/moxi/memory` by default), with the subconscious's log and
records under `subconscious/` there, the index in the `index` folder of its
cache folder (`~/.cache/moxi/memory/index`). `moxi paths memory` prints both.

## The optional links

| Link | Extra | With it | Without it |
|---|---|---|---|
| comms | `comms` (`moxi-comms`) | each session's comms identity (`name`, or `name@scope`), read from comms' local claim socket at start; the agent's own list; agents named in the project list; the recall sender the slot route answers by | no identities: no agent list, and the project list names each session's harness; no slot route |
| launcher | `launcher` (`moxi-launcher`) | each session's launch kind; scratch sessions searched only with `--scratch` and fork launches only with `--forks`, and neither in a list; the `subconscious` and `subconscious_route` rows; the slot route's fork into a slot and its exit, where the launcher says tmux slots run | no kinds: every session is searched by default and may be listed; no session is launched with the subconscious on |
| notes | `notes` (`moxi-notes`) | a recall's first search reads notes files too | a recall reads past sessions only |

Two sessions share an identity only when comms' qualified names are the same, so
`planner@alpha` and `planner@beta` are two agents. memory reaches each link through
`moxi.modules.load` when it needs it, and a link installed later is used from then on.

## Doctor

`moxi doctor memory` shows these rows, and writes nothing:

| row | ok | otherwise |
|---|---|---|
| `index` | the generation, schema, tokenizer, `redact version N` (which secrets `redact` stored its rows) and row counts; also says when the first full read is still filling it, a rebuild is running, older generations are left to remove, or the last rebuild dropped or kept sessions, or redacted the index again; and, told apart, the rows with no writer that `moxi memory reindex` writes again, the rows of compressed Codex rollouts, which no reindex gives a writer, and how many sessions wait on a folder that didn't answer, which each walk reads again | `warn` with no index yet, when a rebuild was killed before it finished and none has finished since, or while the redact version is below the installed secrets' (rows stored before may hold secrets today's `redact` catches); `fail` when it can't be read (fix for each: `moxi memory reindex`) |
| `ingest` | each harness's last walk and the sources changed since, the same count as status's backlog; a harness with no root here has nothing to read; a request waiting behind a running rebuild, or for less than ten minutes | `warn` with no index yet (fix: `moxi memory reindex`), for a harness root that has no walk yet, for compressed Codex sessions not read, or for a request waiting ten minutes or more with no rebuild running (fix: `moxi memory ingest`, whose child starts it when it ends) |
| `folders` | each folder of each harness as the last run found it, from the index; it tests none itself | `off` with no index; `warn` for a folder that didn't answer the last run (since when and why: offline, failed during the run with its first error's path, or lists nothing, with which of the two facts holds: the sources memory read from it, or when a listing last showed it holding an entry), an archive folder never listed whole, or one the last rebuild didn't read (fix: make it answer, or take it off sessions' config; its sessions then leave at the next `moxi memory reindex`; for an archive folder that lists nothing, take it off sessions' config if you emptied it on purpose), and for a folder walked before that sessions' config no longer names, whose sessions leave at the next rebuild unless another folder holds them (fix: put it back in sessions' config, or let them go) |
| `sets` | memory's redaction sets load in secrets | `fail` for a set secrets doesn't have (fix: the `moxi config` line that removes it) or can't run, which stops every ingest; `off` when memory's config can't be read |
| `start` | memory is on turns' start list for each harness it reads, and its listener rows import | `warn` when it is off the list; `fail` when a row doesn't import |
| `sessions` | the session table's sessions, with an identity, settled, and by launch kind; or that no session has started or been read yet | `warn` when the table can't be opened or read |
| `links` | whether comms, the launcher and notes load, and what memory does without each | `warn` when one is installed but fails to load |
| `retention` | each harness's retention, from sessions | `warn` where sessions warns |
| `subconscious` | how many live sessions have the subconscious on, and that a recall fork's permission asks are refused; or that none has it on | `warn` for a live session launched with it on where no route can run now, with the reason (fix: `moxi memory subconscious list`); while memory's config is in error, for the recalls it stopped since the config last read cleanly for a recall, with the newest error (fix: `moxi config memory`); for a recall line left at the late wait's last limit, with its fork and age, until the first taker after the fork's round ends settles it; for a recall line whose child and taker are gone, with its age, until the next recall child or session end takes it; for a recall still `sending`, with its wait and comms' last refusal (for one comms will never take: `moxi memory subconscious discard RECALL_ID`); for a slot fork memory did not exit that the launcher still runs (its exit held past the exit's wait or answered with anything but `exited`, or its owner gone before it asked for the exit), with its session, its slot where the launcher can say, how its exit came out and its age, which memory leaves running (`moxi launcher exit-session SESSION_ID` ends it once it is quiet); for recalls in the last day whose child could not start (reason `spawn`), with the newest error; for a recall on any route `starting` or `hurried` 30 seconds on with no fork and no log line naming its child (the child gone before it wrote its line, or with no identity of its own to write); and for one on the subagent route whose holder still runs past its wait for the fork's id, with its state and age |

A `config` row fails when memory's config can't be read, and a `record ingest` row shows a
failed ingest child.

## Library

`moxi_memory.api` offers interface 1; another module loads it with
`moxi.modules.load("memory", interface=1)`.

- `search(query, *, project=None, since=None, harness=None, results=False,
  subagents=False, scratch=False, forks=False, limit=10) -> Result`. `since` is epoch
  seconds or a
  `datetime` (no zone is local time). A `Result` holds `hits` (each a `Hit`: `harness`,
  `session_id`, always the main session's, `agent_id` on a subagent's hit, `time`,
  `project`, `kind`, `tier`, `snippet`, `key`, `score`, `failed` and `writer`), `rows` (how
  many rows
  were searched), `tokens`, `filling` (a `Filling(read, listed)` while the first full read
  hasn't finished, else None), `rebuilding` (a running rebuild's start time, else None),
  `unread` (the archive folders no walk they answered has listed whole) and
  `generation`. With no index it raises `IndexMissing`, with one that can't be read
  `IndexUnreadable`, and with a state store that can't be read `StateUnreadable`, a kind
  of `IndexUnreadable`; it never answers any of them with an empty result.
- `status() -> Status`: what `moxi memory status` prints, as fields. It doesn't raise for
  a missing index: `Status.index` is `ok`, `missing` or `unreadable`.
- `cut(query)`: the tokens a query is searched for.
- `SUBCONSCIOUS_ENV`: the name of the variable a launch stamps with the session's
  subconscious mode, which the launcher's `subconscious` row reads here.

A search only reads: it never takes the lock an ingest writes under, and a rebuild that
switches the index under it leaves the old one for it to read to the end.

## In the Claude apps

With its `connector` and `secrets` extras, memory serves one read-only tool,
`search_memory`, to the Claude apps through the connector, Moxi's public MCP host:

```sh
moxi install memory --with connector,secrets
```

That installs `moxi-memory[connector,secrets]`. The `connector` extra brings the host
and python-stdnum, the validators secrets' personal-data set runs with. secrets itself is
one of memory's dependencies, so with `connector` alone the tool redacts with every chat
set; the `secrets` extra names the personal-data set and adds nothing more. While secrets
or one of its chat sets can't run (python-stdnum removed, say), the tool refuses
`redaction-off` and the `memory redaction` row of `moxi doctor connector` says why.

**`search_memory`** searches past sessions in memory's index. Inputs: `words` (required,
1 to 500 characters, cut into at most 64 tokens as `moxi memory search` cuts them),
`project` (a project label), `since` (an age such as `7d` or `12h`, or an ISO 8601 time),
`harness` (`claude`, `codex` or `opencode`), `results`, `subagents`, `scratch` and
`forks` (each off by default, as the flags of `moxi memory search`), and `limit` (1 to 20, default 8).
Each hit gives `session` (the main session's id, which `read_session` opens), `agent`
(the subagent's id, or null), `harness`, `at` in UTC, `project`, `kind` (`user`,
`assistant`, `tool` or `result`), `writer`, `failed` and `snippet`. Beside the hits,
`rows` counts the rows the search looked through after its filters, whatever the words;
`notes` holds memory's lines for an incomplete answer (the index still filling, a rebuild
running, archive folders not read whole); and `cut` says the search stopped at its time
limit.

**Rows record their writer.** Each row memory stores names who wrote it, from sessions'
`writer`: `person` (typed at the prompt), `model` (the model's own turn or tool call),
`unknown` (a user message whose harness doesn't say who submitted it), or the kind of
message something else wrote, such as `summary`. A row stored before memory recorded
writers has none, and the tool never returns it. One `moxi memory reindex` writes every
row again with its writer, except the rows of a session the rebuild keeps without reading
it. A session in a folder that didn't answer the reindex (an archive folder offline, a
read error) waits on that folder: the reindex tries the folder once more before it ends,
and every later ingest walk reads the session again until it reads cleanly, which gives
its rows their writer. Until then the chat doesn't find them.

**A compressed Codex rollout is never found.** Codex can compress a rollout to
`.jsonl.zst`, and sessions can't read that. memory keeps the rows it read before the
rollout was compressed, but they record no writer, and no reindex or walk can give them
one, so `search_memory` never finds that session. `moxi memory search` at the machine
still does.

An index from before the writer column is migrated the next time memory writes to it,
and the tool refuses `index-unreadable` until then.

**What it returns.** Only what `read_session` would show as text: a row whose writer is
the person or the model, `unknown` with a text that opens neither with `<` and a letter
nor with a redaction placeholder, or a delivered message. A text that opens with one of
sessions' output wrappers (`<task-notification>`, `<local-command-stdout>`,
`<system-reminder>` and the rest) is never a hit unless the row is a delivered one, and
a `harness` or `summary` row never is. These are clauses of the search itself, so a
hidden row takes no place and changes no count. Leading spaces, no-break spaces and
invisible characters don't hide a wrapper.

A delivered message is what a plugin (comms mail among them), a channel, another
session or a background task put into the session: writer `hook`, `channel`, `peer`,
`coordinator` or `task`. It is a hit exactly when `moxi memory search` would return it.
Its writer comes from its line's own origin, so the wrapper and tag tests, which judge a
row whose writer is unsure, don't apply: a background task's notice opens with
`<task-notification>` and is still found. Its text is redacted, windowed and ranked
like every row's, the hit's `writer` names who delivered it, and its line in the text
part reads `delivered by hook` (or the other writer) where a typed row's reads `user`.
With `results`, the ends of tool results are searched too, each a `result` hit with no
writer.

**How it picks and ranks.** The tool doesn't take `moxi memory search`'s ranking, which
is computed over the stored text and so over what the chat redaction hides. It reads the
newest 500 rows holding every word and the newest 500 holding any word, newest first,
and puts each through the chat redaction. A row from the first group stays only when
every word still matches its redacted text, a row from the second when at least one
does, and a row in both counts once. The rows that stay are ranked together by BM25 over
their redacted text, a tie going to the newer, and the first `limit` are the hits. So a
word that sits only inside a redacted value neither makes a hit nor moves one, and which
hits come back, their order and the counts are the same whether a guessed value is in a
hidden span or in no row. What it costs: an older row holding only some of the words,
past the newest 500 that do, isn't found; `moxi memory search` at the machine finds it.

**Snippets.** Each row's text goes through three steps, in order. memory stored long
texts cut, redacted only with its own `redact.sets`, so the text within 64 characters of
a stored cut, widened to the next whitespace, shows as `[REDACTED:cut-edge]`: a secret
the cut split could leave a part no rule matches. Then the whole text goes through the
chat redaction. Between its two passes it makes each run of spaces, tabs and newlines one
space, then cuts each local path to its last segment and shows each loopback address as
`local`, as `read_session` does (sessions' `cut_local`): `/home/you/notes/x.md` shows as
`x.md`, `http://localhost:8080/api` as `local`. So its last pass sees the text as the
snippet shows it: a key and a value blank lines apart, which one space would join, are
redacted. Then the snippet is taken from the redacted text around the words, at most 64
tokens and 300 characters, cut at a space, with `…` where it leaves text out and no marks
around the words. Nothing else changes it. The words must match the text after the cut,
so a row whose only match for a word was inside a path (`zircon` in
`/home/you/zircon/run.sh`) is dropped, as a row whose word sat only in a redacted value
is.

**Redaction.** Every string an answer holds goes through secrets' `chat_redact`, which
redacts with its chat sets (`credentials`, `broad` and `personal-data`) in strict mode,
with the path cut between its two passes: snippets, project labels, notes, and an id
that isn't in a session id's shape. Session
ids, harness names, kinds, writers, times, counts and flags go as they are. The text part
joins a hit's project label and snippet into a line, so it goes through `chat_redact`
again whole: a label such as `keycloak` before `user: <value>` makes a keyed shape that
neither piece held. A row that
comes back unscanned is dropped. The tool also refuses `redaction-off` while memory's own
`redact.sets` lacks `credentials` or `broad`, since a stored cut could then split one of
their values where the cut-edge step can't see it.

**Limits and refusals.** The search stops 16 seconds after the call began, ranks what it
has and says `cut`. At most two calls of the tool run at once; a third waits up to 5
seconds and then refuses `busy`, and a call still running at 20 seconds refuses
`timeout`. Refusals: `no-index` (run `moxi memory reindex` at the machine),
`index-unreadable`, `bad-since`, `redaction-off`, `no-sessions` (the sessions installed
is missing, broken or too old to say who wrote each message; the `memory search` row
says which), `busy`, `timeout`, and `not-started` for a call before the host has
started the provider, which the host never makes.

**Status.** `moxi connector status` and `moxi doctor connector` show a `memory search`
row (the index's generation and rows, with a warning while it fills, at an old schema,
with rows that record no writer that `moxi memory reindex` would give one, which it
advises, or with sessions waiting on a folder that didn't answer, which it says each
walk reads again; the rows of compressed Codex rollouts are counted on a line of their
own that warns of nothing and advises no reindex, since none would help; and a failure
when the index or memory's sets can't be used)
and a `memory redaction` row (whether the chat sets load, and secrets' engine).

**The call log** records `words` (how many), `hits`, `hits_dropped` (rows whose words
matched only text the redaction hid or a path the cut shortened), `unscanned`, `redactions`, `cut` and `refusal`. It
never holds the words searched for, text read or a session id.

What a chat that reads this tool can do with what it read, and what redaction doesn't
catch, is in the connector's
[docs/security.md](https://github.com/CodeManEthan/moxi/blob/moxi-memory-v0.1.0/packages/connector/docs/security.md).
Read it before you turn the tool on.

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
the redaction sets. The extras `comms`, `launcher` and `notes` add the three links above. The extra
`connector` adds `moxi-connector`, the public MCP host, and python-stdnum, which
secrets' personal-data set runs with, and serves `search_memory` ("In the Claude apps").
The extra `secrets` names that set, `moxi-secrets[personal-data]`, and adds nothing
`connector` doesn't. memory parses no harness's transcript format itself.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-memory-v0.1.0/packages/memory/LICENSE).
