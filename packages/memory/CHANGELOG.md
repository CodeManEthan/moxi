# Changelog

## Unreleased

- The subconscious, memory's second way to recall: in a session launched with it on (the
  launcher's `subconscious` row, which stamps `MOXI_MEMORY_SUBCONSCIOUS`, exported as
  `api.SUBCONSCIOUS_ENV`), a second agent searches past sessions and notes and sends the
  main agent what it finds, at each prompt `subconscious.trigger` counts (`automatic`)
  or when the agent runs `moxi memory subconscious recall TOPIC` (`on-demand`, with a
  start line naming it and at most `subconscious.on_demand_per_prompt` between two
  counted prompts). For now a recall runs only on Claude Code, as a subagent forked
  through turns' `fork`; a Codex session, or a Claude Code session whose turns offers no
  `fork`, has the subconscious off, told by an `off` line in the log at the first try
  and whenever the reason changes, by the on-demand command's refusal and by the doctor.
  A recall's detached child runs a first search (none for a prompt with no keyword,
  never this session's own rows) and reads the notes with notes installed, writes the
  fork's brief, and streams rounds through `fork` and `fork.continue`, each kept by
  turns until memory acks it and bounded at `subconscious.limit_s` plus 30 seconds,
  until `NOTHING`, `End of recall.`, `max_rounds`, `max_tools` or `limit_s`. A line whose
  child died is taken over by the next recall child or by a taker started at session
  end. New settings under `subconscious.*` (`limit_s` held to 1 to 3600 by the config
  check), a log shown by `moxi memory subconscious list`, and `moxi memory subconscious
  discard`, which refuses every line on the subagent route since the harness brings
  every answer back itself. Recall records are swept after a day unless their line is
  still kept.
- A recall's fork gives its first answer as soon as it has one useful point, from the
  first search alone when that holds one, and the stream goes on after it while the fork
  has tool calls and time left: only a later answer's `End of recall.` ends it. The
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
