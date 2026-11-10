# Changelog

## 0.2.9 (2026-11-10)

- A list row's `prompt` is the newest message in the whole session that the record marks
  as typed by a person, and nothing else. It was looked for in the last five turns only.
  A session with no such message has no `prompt`. Codex and OpenCode rows have none:
  sessions reads no typed mark from their records (the README says what Codex would
  need).
- The read for it: a session is read from its start once, whatever its size, and where
  that read ended is kept in memory. An unchanged file is not read again, and a file
  that grew is read from the kept place only, so a list over long sessions that are
  being written pays for their new lines and not for their length. A file that shrank,
  was written again or was replaced is read from its start. A last line with no newline
  yet is not read until it has one. The README states what is kept and the two changes
  to a file this read does not see.
- `moxi_sessions.catalog`: `PROMPT_TURNS` is gone, and `Tail` no longer has `prompt`.
  New: `TYPED`, `WINDOW`, `Kept`, `PromptRead`, `read_prompt`, `reads_prompt` and the
  counters `Catalog.prompt_reads`, `prompt_whole` and `prompt_bytes`.
  `moxi_sessions.claude` has `may_be_typed`, which tells from a line's bytes whether
  it can hold a typed message. It says no for no line the decode would read as typed:
  a line that writes the key or the kind with an escape, or is in UTF-16 or UTF-32, is
  left to the decode.

- The page lists the sessions at its own address: the ones started in a terminal that
  changed in the last hour, then a group per day and `Earlier`, each row named by its
  typed name or its folder and start, with the newest typed message as its second line.
  A day folds past eight rows, and the sessions a program started sit behind one line
  under their day. A find field (a session id or part of a folder's name) and three
  filters, Folder, Harness and When, narrow the list, with the count beside them. The
  list is read again every 30 seconds while its tab is visible. A harness whose sessions
  could not be read, and Codex sessions left out because they are compressed, each get a
  line under the list.
- A row of the list opens in place: the newest typed message and the reply after it, the
  session's subagents, a `Session` line (harness, model, start to last change), the
  harness's own resume command with `Copy`, and under them `Read the transcript`, which
  goes to the session's address, and `Copy id`. The session is read when its row is
  opened, and again only when a newer list shows it changed.
- The form that asked for a harness and a session id is gone: type the id in the find
  field.
- A session's own address has a new header: `Sessions`, which goes back to the list, and
  the session's name as the list names it; under it the harness, the model and from when
  to when. The page's heading and its opening sentence are gone. What the old header
  listed (the harness's name, the id, title, directory, last activity, version, parent
  session, entrypoint, the file read) is behind `Details`. A subagent and a session
  another one started keep a heading by id or title, with those facts in the open. The
  turns, "Earlier", `Refresh` and the subagents are as before, without the accent,
  warning and error colours. A tool result that failed starts with `Failed` and the
  message Claude Code writes for a request that failed starts with `Error`, each in the
  heavier weight, here and in the window block. A read on its way and a read that failed
  each say so in words. The header's name and facts come from `/api/session`, the read an
  opened row of the list makes, asked beside the turns: it reads no other session, so
  on a page server's first open the name is drawn with the turns rather than seconds
  after them. A transcript says only its own reason for a read that failed; the list's
  line for a harness whose sessions were not all read is not drawn over it.
- `/api/list` and `/api/session` carry `home`, the home folder of the account the page
  server runs as (null when it has none), marked as a path. `/api/session` also carries
  `today`, the server's local date, as the list does. The page writes a folder
  under it with `~`: the resume line's folder, the path in the Folder menu and the
  directory in `Details`.
- The README says how to read the page and lists what the page leaves out, each with
  what it needs.

## 0.2.8 (2026-11-10)

- Interface 5, which is interface 4 plus `session_facts(keys)`: for up to 200
  `(harness, session)` pairs, each session's name, folder and start, in the order asked.
  The name is the one a list's row carries, read by the same code: the name typed for
  the session in the harness, never a title the harness made up, and None for a Codex or
  an OpenCode session. A pair that is no main session of any root has no item, and the
  answer's `roots` names the roots that were not listed whole. It raises ValueError for
  more than 200 pairs, a key that is not a pair and a harness that is not one, and never
  for a session that cannot be read.
- What it reads: each root's listing, then of each session it answers the first lines
  and one turn, the last; no turn of a Codex or an OpenCode session. It keeps a name or
  None for at most 5,000 sessions, in memory, and no other text of the turn. A session a
  list has read is named with no further read, and a list reads the last turn of a
  session that was only named, so a row loses nothing to the cheaper read.

## 0.2.7 (2026-11-10)

- New command `moxi sessions list [--harness H] [--since TIME] [--folder PATH]
  [--limit N] [--text] [--json]`: the main sessions of the three harnesses, newest change
  first, 30 unless asked, one line each: when it started, when it last changed, its
  harness, its name or folder and its id. It prints what was said in a session only with
  `--text`, redacted when the secrets module is installed.
- Interface 4, which is interface 3 plus the list: `sessions_list` (the sessions counted
  and cut into the ones active recently, seven days and the rest), `sessions_more`,
  `sessions_newest`, `sessions_folders` and `session_opened`. Each reads through the
  calling process's own memory and takes no cache from its caller.
- What a list reads: each root's listing; the first lines of every session of the days
  it returns, shown or not, and of every Codex rollout, once each; and the last turn of
  the rows it returns. Asking by folder or by `find` reads every session's first lines
  once. What was read is kept in memory, and nothing is written to disk.
- A list tells a harness that is not on the machine from a root that could not be read.
  A harness whose default root does not exist gives nothing: no count, no reason. A root
  a setting names that does not exist, and any root that exists and cannot be read, is
  named with its reason, and no count is given for that harness. A root listed in part
  is named with how many folders and files under it could not be listed, since its counts
  leave their sessions out; a session whose subagents folder could not be listed has no
  subagent count.
- `Header` has `started`, when the session's record begins, and a Codex header's `meta`
  holds line 1's `timestamp`.
- New setting `list.recent_s` (3600): how many seconds after its last change a session
  counts as active recently.
- With the `ui` extra, the page server answers four more reads, `/api/list`, `/api/more`,
  `/api/session` and `/api/folders`. The page does not show them yet.
- `moxi_sessions.resume` holds each harness's command for picking a session up again,
  checked against Claude Code 2.1.322, Codex 0.153.0 and OpenCode 1.18.34.

## 0.2.6 (2026-11-08)

- Docs name turns' `guards.run` key.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_sessions.api.projects_root`: `environ` is now `env`;
  `moxi_sessions.api.read_back`: `head` is now `read_head`;
  `moxi_sessions.api.records_back`: `head` is now `read_head`;
  `moxi_sessions.api.retention`: `environ` is now `env`; `moxi_sessions.api.root`:
  `environ` is now `env`; `moxi_sessions.api.Window`: `has_earlier` is now keyword-only.
- New setting `guard.transcript_read_escapes`: the verbs of this environment's `moxi`
  whose commands the transcript-read guard lets name a transcript path. The default is
  `["sessions"]`; add a module's verb to let it through too.
- sessions needs moxi 0.1.6 or later.
- The README installs from PyPI, links to the core's README for the `moxi` command, and
  its links work from PyPI. The package declares keywords and classifiers.

## 0.2.5 (2026-11-07)

Not published.

- New guard for turns, `sessions.transcript-read`: once it is on, an agent's Read, Grep or
  Glob of a path under a transcript root, or a shell command naming one, is refused and
  pointed to `moxi sessions read <session>`. A simple command that runs `moxi sessions`
  passes. Off by default; its switch is `guard.transcript_read`, and the install asks
  about it.
- New extra `turns`, which installs moxi-turns to enforce the guard.
- New doctor row, `guard`: `off`, `ok` when the guard is on and turns enforces it, or
  `warn` when it is on and nothing does.

## 0.2.0 to 0.2.4 (2026-10-30 to 2026-11-06)

Not published; summarised from the repository's history.

- One record shape for every harness: `request`, `message`, `tool_call`, `tool_result`,
  `marker`, `limits`, `malformed` and `other`, with an `UNKNOWN` value that is neither
  None nor zero. Claude Code's transcripts, Codex's rollouts and OpenCode's database all
  map into it.
- `find`, `sources` and `subagents` for Claude Code, Codex and OpenCode. `find` names
  every root it asked and what each answered. `moxi sessions find` takes `--harness
  opencode`, and `moxi sessions root` prints all three roots and where each came from.
- The doctor gives one row per harness root.
- `moxi_sessions.api` offers interface 2, imports each name on first use, and exports the
  reads, the shape and the record kinds. A forward read that keeps only some kinds does
  less work per line.
- `moxi_sessions.testing` writes Claude Code transcripts and subagent files for tests.
- A message says who submitted it (`injected`): true when the harness's own field names
  something other than the person at the prompt, false when it names the person or the
  message is the model's, unknown when the harness doesn't say.
- `moxi sessions read SESSION` prints part of one session: its last five turns, `--turns
  N`, the turns `--around` a time, or `--all`; `--json` gives the rows the page gets. With
  the new `secrets` extra it redacts credentials first.
- `retention()` reads how long each harness keeps its transcripts. It changes nothing.
  The doctor has a retention row, and setup prints the same lines. Interface 3 is
  interface 2 plus `retention()`.
- With the new `ui` extra, sessions has a page in Moxi's browser shell that shows one
  session's last turns and its subagents, and a `window` block other pages can place.
  The extra needs moxi-ui 0.2.

## 0.1.1 (2026-10-22)

- A README and the MIT license.
- Test names and docstrings describe the reference readers as what they are.
- The source distribution lists the paths it includes.

## 0.1.0 (2026-10-21)

The first public release.

- Read-only readers of Claude Code transcripts and Codex rollouts, through one line
  decoder, and a backward read of a record.
- `moxi sessions find SESSION [--harness claude|codex]` prints where a session's record
  is, or every root it probed and why none held it. `moxi sessions root` prints the
  Claude Code projects root and where it came from.
- `moxi_sessions.api`, interface 1.
- Python 3.11 or later.
