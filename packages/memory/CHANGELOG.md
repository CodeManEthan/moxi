# Changelog

## Unreleased

- A recall fork's plain-read test reads the shell text through turns' shell reader
  (`read_shell`) in place of a splitter of its own, after the same character filter. It
  now refuses a comment (`grep x #c`), which it allowed; everything else it allowed or
  refused, it still does. memory asks for moxi-turns 0.2.8 or later.
- With the secrets release whose credentials set finds `sk-` keys and real URL
  passwords, new rows are stored with those redacted. Rows indexed before it keep them
  until `moxi memory reindex` rebuilds the index.
- The subconscious, memory's second way to recall: in a session launched with it on (the
  launcher's `subconscious` row, which stamps `MOXI_MEMORY_SUBCONSCIOUS`, exported as
  `api.SUBCONSCIOUS_ENV`), a second agent searches past sessions and notes and sends the
  main agent what it finds, at each prompt `subconscious.trigger` counts (`automatic`)
  or when the agent runs `moxi memory subconscious recall TOPIC` (`on-demand`, with a
  start line naming it and at most `subconscious.on_demand_per_prompt` between two
  counted prompts). A recall runs on Claude Code as a subagent forked through turns'
  `fork`, or in a slot on the slot route (next entry); a session where no route can run
  has the subconscious off, told by an `off` line in the log at the first try and
  whenever the reason changes, by the on-demand command's refusal and by the doctor.
  A recall's detached child runs a first search (none for a prompt with no keyword,
  never this session's own rows) and reads the notes with notes installed, writes the
  fork's brief, and streams rounds through `fork` and `fork.continue`, each kept by
  turns until memory acks it and bounded at `subconscious.limit_s` plus 30 seconds,
  until `NOTHING`, `End of recall.`, `max_rounds`, `max_tools` or `limit_s`. A line whose
  child died is taken over by the next recall child or by a taker started at session
  end. New settings under `subconscious.*` (`limit_s` held to 1 to 3600 by the config
  check), a log shown by `moxi memory subconscious list`, and `moxi memory subconscious
  discard`, which ends a line still sending and refuses every line on the subagent
  route, since the harness brings every answer back itself. Recall records are swept
  after a day unless their line is still kept.
- The slot route: with the launcher's `subconscious_route = "slot"` (stamped as
  `MOXI_MEMORY_SUBCONSCIOUS_ROUTE`), or on Claude Code when turns offers no `fork`, a
  recall forks the session into a tmux slot through the launcher's interface 3, the
  fork stamped `MOXI_MEMORY_SUBCONSCIOUS_FORK` with the recall's id
  (`api.SUBCONSCIOUS_FORK_ENV`). It needs the launcher, tmux slots here (the launcher's
  interface 3 `slots_here` says so; an older launcher reads as `no slots here`) and
  comms with scoped senders. On Codex only the slot route runs a recall, and
  only where the launch picks it; on `subagent` a Codex session has the subconscious off
  (`Codex: off unless the launch picks the slot route`). A route stamp naming neither
  route runs no recall, with an off reason that names it. No recall starts inside a slot
  fork, and the on-demand command refuses there. Two turns guards hold a slot fork to
  the same reads: `memory.subconscious-slot` (every call, Claude Code's and Codex's,
  whose shell reaches the guard as `Bash`) and `memory.subconscious-slot-ask` (every
  permission ask, Claude Code).
- A recall on the slot route sends each answer by comms, to the session alone, under
  the session's recall sender (`<comms name>-subconscious`, comms' scoped sender for the
  word `subconscious`, which memory declares). The word is declared once memory is
  installed beside comms, subconscious on or not, so from then no session or program on
  the machine can take a name of the form `<comms name>-subconscious`; one held before
  stays its holder's and puts that parent's recall sender in conflict (the slot route
  off for its session, `moxi doctor` naming the holder). After the session has ended, an answer
  still goes to the comms name the session held when the recall started. The line reads
  `sending` until comms
  takes it, tried again every 10 seconds for a minute and then every 5 minutes, each
  refusal on the line; `list` and the doctor show the wait and the last refusal, and a
  discard ends a line comms will never take. A round past its bound is noted
  (`past_bound`) and its answer is still sent if it comes within 300 seconds; past that
  the line is left for a taker, which settles it once the fork's round ends. On Codex
  the message lands as one more request in the same turn, so that turn's final answer is
  the reply to the recall. Under `user-and-comms` a delivery whose senders are all this
  session's recall senders starts no recall; with other senders too, the lines memory
  already sent leave the recall's query and reach its fork as already recalled. The off reasons name comms' trouble, the recall
  sender in conflict among them. Every recall child and taker starts through a detached
  starter, outside every session where the system takes an orphan as its own (Linux,
  macOS); on native Windows, where no such route runs today, comms could not place it.
- A slot fork is exited through the launcher once its recall's last round is settled,
  by the child, a taker, or a discard of a line whose owner is gone, and the line notes
  how (`fork_exit`). The last answer is sent first; when comms refuses it, the fork is
  exited at that refusal, so the slot isn't held while the send is tried again. An exit still held after a minute is left: memory's doctor lists
  the fork with its session, slot and age, and memory never ends it on its own.
- memory's side of Codex's host fork (`host-fork` in the log): its rounds through turns'
  Codex `fork` and `fork.continue`, its answers sent by comms, nothing to exit. No
  session is routed to it.
- A recall's record names the process that holds it from its first write: the
  listener or the on-demand command starts the detached starter under the session's
  lock and names it, the starter names the child, and the child names itself. The
  recall guards wait for a fork's id only while that holder runs and at most
  `subconscious.limit_s` plus 60 seconds from the recall's start; memory's doctor lists
  a record past that wait. On Linux the holder's liveness and these ages are read on
  clocks a wall-clock step doesn't move. Records are ordered by their write sequence
  (`seq`), and a record's `at` later than now is clamped by whoever holds the session's
  lock (each write, the guard's judge, the day sweep). The guards run only in a session
  on the subagent route (`MOXI_MEMORY_SUBCONSCIOUS_ROUTE`, exported as
  `api.SUBCONSCIOUS_ROUTE_ENV`; unset means `subagent`).
- A recall's first search under `subconscious.scope = "project-first"` (the default)
  now runs two parts every time: the project's best `prefetch_hits` hits and notes
  files, then the whole index's best hits and the notes root's best files that the
  project's part didn't already show, each part under its own heading in the brief. It
  no longer waits for the project to find nothing before it widens. The fork's own
  searches run without `--project`, and the brief names the project and its notes
  directory. The notes search reads Markdown files only, reads the notes root whole (no
  cap on how many files), cuts each shown line at 200 characters, takes a notes
  directory that doesn't exist as none, and orders equal matches by path. The log line
  records `project_hits` and `beyond_hits` in place of `widened`; older lines keep
  `widened`.
- A recall's fork gives its first answer as soon as it has one useful point, from the
  first search alone when that holds one, and later answers with only what is new. The
  brief says a backslash, `(`, `)`, `$` or `!` in a shell call is refused, even quoted,
  and points to `grep -E "one|other"` or the Grep tool.
- Two turns guards, run only in sessions with the subconscious on, hold a recall's fork
  to reads: `memory.subconscious-fork` allows Read, Grep, Glob and a foreground shell call
  that is a plain read of `subconscious.fork_commands` (alone or in a pipeline), counted
  against `max_tools` over all rounds and refused past `limit_s`;
  `memory.subconscious-fork-ask` refuses every permission ask of a recall fork, and a
  fault in its judge refuses the ask too.
- A session launched as a fork (`moxi launcher fork`) is left out of search, in every
  tier, unless `--forks` (`forks` in the API and in `search_memory`), and out of both
  start lists, as a scratch launch is. The state store's list indexes are made again for
  it (its schema step 4). A subagent that ran as a recall's fork is never indexed, even
  with `index.include_subagents` on.
- The doctor's new `subconscious` row names each live session launched with the
  subconscious on where no route can run, the recalls stopped since memory's config last
  read cleanly while it is in error, recall lines whose child is gone, recalls whose
  child could not start, and records starting or hurried with no line naming their child; its `links` row
  names notes too.
- A `notes` extra: a recall reads notes files in its first search.
- `search_memory` cuts every local path in what it shows to its last segment and every
  loopback address to `local`, as `read_session` does: sessions' `cut_local` runs inside
  `chat_redact`, after the whitespace squeeze, for snippets, project labels, notes and
  the text part. The words must match the text after the cut, so a row whose only match
  was inside a path is no hit and counts in `hits_dropped`.
- A source a rebuild or a walk couldn't read because its folder raised (an archive folder
  offline, a permission or read error) waits on that folder, in a new `waiting` table of
  the state store (its schema step 3). Every later walk reads it again from its start,
  whatever its time, until a read reaches its end or the file is gone, so a session a
  reindex kept without reading gets its rows' writers. A rebuild with a folder that
  didn't answer lists and reads that harness once more before it decides. The doctor's
  `index` row and the connector's `search` row count the sessions waiting.
- The rows with no writer are told apart in the doctor's `index` row and the `search`
  row: those a `moxi memory reindex` writes again, which alone get that advice, and those
  of compressed Codex rollouts, which sessions can't read, so no reindex gives them a
  writer and `search_memory` never finds them.
- A `connector` extra and a `moxi.connector.providers` entry: the public MCP host serves
  `search_memory` to the Claude apps. It searches in one reader of the index and returns
  only rows whose writer is the person, the model, or unknown with no tag at the start
  (after the opening characters sessions publishes), never a row with no writer and no
  mail from other sessions. The rows it ranks are the newest 500 holding every word and
  the newest 500 holding any, each still matching once secrets' `chat_redact` has run
  over the whole row, with the text within 64 characters of a stored cut dropped first;
  they are ranked by bm25 over the redacted text, and each snippet is cut from it, at
  most 300 characters, with no marks. `results`, `subagents` and `scratch` are off by
  default. The walk stops 16 seconds after the call began, and the host's shared limit
  runs two calls at once. It refuses `redaction-off` when secrets or a chat set can't
  run or `redact.sets` lacks `credentials` or `broad`, `no-index`, `index-unreadable`
  (a generation still at schema 1 among them), `bad-since`, and `no-sessions` when the
  sessions installed doesn't offer interface 7, which the `search` row then names. The provider's status
  gives the `search` and `redaction` rows. The `connector` extra brings python-stdnum,
  which secrets' personal-data set runs with; the `secrets` extra names that set and adds
  nothing more.
- Each row records its writer (schema 2): who the record's harness says wrote it, from
  sessions' `writer` at interface 7, which memory now reads sessions at. A generation
  still at schema 1 gains the column when memory next writes to it; its rows, like every
  row stored before, have no writer until `moxi memory reindex` reads them again, and a
  rebuild's copy keeps the writer a row was stored with. The doctor's `index` row says
  how many rows have none.
- Each index records which secrets `redact` stored its rows (`redact_version` in its
  meta; an index without it counts as 1). A typed `moxi memory reindex` over an index
  below the installed secrets' `REDACT_VERSION` is a version rebuild: it reads every
  source again and keeps, per source, everything of each source it didn't read to its
  end, redacted again and trimmed at a stored cut to the first whitespace at least 64
  characters away. It drops no session. No ingest starts it. The doctor's `index` row
  shows the redact version and warns while it is below secrets', and `reindex` says first
  what a version rebuild keeps. With this secrets, a secret on a line marked
  `gitleaks:allow` and a token split by an invisible character are redacted in new rows,
  and in old ones after that reindex.
- An index stored under secrets' redact version 2 asks for one `moxi memory reindex`.
  secrets' version 3 redacts a key followed by an escaped quote (`\"`), which a tool
  call's input holds when a command quotes a key inside a string; version 2 stored it
  whole. The reindex redacts every stored row again.
- Archive folders: memory reads every folder sessions names for a harness, its live root
  and then its archive folders (`claude.archives`, `codex.archives`), each from its own
  last walk. A folder that is offline, fails at any depth during a run, or fails the test
  just before the run's decision didn't answer that run; nor does one whose top level
  lists nothing while memory read sources from it or a listing once showed it holding an
  entry, since an unmounted disk can leave its mount point an empty directory. A rebuild
  drops a session only when every folder of its harness answered; otherwise it keeps the
  session's rows, under today's settings and labels, and its session-table row. Offline is
  not gone. Each folder's answer is kept in the index.
- The doctor's new `folders` row names each folder that didn't answer the last run (why,
  since when, its first error), an archive folder never listed whole, and one sessions'
  config no longer names. `status --json` gains `roots` and `kept`. A search names the
  archive folders not read yet in `unread` and one line; `reindex` says when a rebuild
  drops a session and names each folder that didn't answer the last run, and `status`
  says what the last rebuild kept. Callers that compared those documents whole
  must allow the new keys. memory needs the sessions release with archive folders
  (interface 6).
- memory needs the sessions release with interface 7 (`writer` and `opening`), and
  `search_memory` needs the secrets release with `chat_redact` and the connector release
  with `Limit`. No released version of the three carries them yet; memory's floors on
  them rise when they are released.

## 0.1.0 (2026-11-08)

The first public release.

- A keyword index of Claude Code's, Codex's and OpenCode's session transcripts, read
  through sessions where each harness keeps them, with Porter stemming and BM25 ranking.
  Tier 1 holds the messages a person or an agent wrote and every tool call; tier 2 the end
  of each tool result, searched with `--results`; subagent sessions are indexed only with
  `index.include_subagents` on.
- Every stored text is redacted with secrets' sets (`credentials` and `broad` by default)
  before it is cut, and a tool call that names a secret file keeps only the tool and the
  path. A secret that holds a `/` can reach the index under the default sets.
- `moxi memory search`, `ingest`, `reindex` and `status`. A search that ran exits 0 and
  says in one line each when the first full read is still filling the index or a rebuild
  is running; a missing or unreadable index exits 1. `ingest` and `reindex` ask for the
  work in the background and return at once. What they ask for waits, and is not lost,
  while another ingest holds the writer lock; `status` and the doctor show it waiting.
- A detached ingest child, `moxi-memory-ingest`, started when a turn ends and when a
  session ends, by a catch-up walk, and by an optional hourly timer. One child writes at a
  time.
- The index is a series of generation files behind a pointer, in a rebuildable store that
  `moxi uninstall memory --purge` removes: a rebuild builds a new one while searches read
  the old, and drops the sessions whose transcripts are gone.
- Through turns, each session gets one start line, its agent's and its project's recent
  sessions, and a one-line reminder at each prompt. The lists read only memory's session
  table and answer within 1.5 s.
- Optional links: comms for each session's identity, the launcher for each session's
  launch kind, which keeps scratch launches out of default search and the lists.
- Setup counts each harness's sessions, prints how long each harness keeps transcripts,
  offers to turn Claude Code's auto memory off, and offers the first full read.
- Interface 1, `moxi_memory.api`: `search`, `status` and `cut`. A search raises
  `IndexMissing` or `IndexUnreadable`, or `StateUnreadable` when only the state store
  can't be read.
- `moxi doctor memory` rows: `index`, `ingest`, `sets`, `start`, `sessions`, `links` and
  `retention`.
- Linux only. Requires moxi 0.1.7 or later and turns 0.2.4 or later.
