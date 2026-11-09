# Changelog

## 0.1.0 (2026-11-08)

The first public release.

- A keyword index of Claude Code's, Codex's and OpenCode's session transcripts, read
  through sessions where each harness keeps them, with Porter stemming and BM25 ranking.
  Tier 1 holds the messages a person or an agent wrote and every tool call; tier 2 the end
  of each tool result, searched with `--results`; subagent sessions are indexed only with
  `index.subagents` on.
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
- The index is a series of generation files behind a pointer: a rebuild builds a new one
  while searches read the old, and drops the sessions whose transcripts are gone.
- Through turns, each session gets one start line, its agent's and its project's recent
  sessions, and a one-line reminder at each prompt. The lists read only memory's session
  table and answer within 1.5 s.
- Optional links: comms for each session's identity, the launcher for each session's
  launch kind, which keeps scratch launches out of default search and the lists.
- Setup counts each harness's sessions, prints how long each harness keeps transcripts,
  offers to turn Claude Code's auto memory off, and offers the first full read.
- Interface 1, `moxi_memory.api`: `search`, `status` and `cut`.
- `moxi doctor memory` rows: `index`, `ingest`, `sets`, `start`, `sessions`, `links` and
  `retention`.
- Linux only. Requires moxi 0.1.6 or later.
