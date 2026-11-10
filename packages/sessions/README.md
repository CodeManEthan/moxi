# moxi-sessions

Moxi's sessions module reads the records Claude Code, Codex and OpenCode keep of their
own sessions, and turns each into one set of record kinds with one set of field names. It
only reads: it writes nothing to a record, keeps no state of its own, and leaves what a
record or a miss means to the caller.

It reads from three roots, one per harness:

- Claude Code's projects root, `${CLAUDE_CONFIG_DIR:-~/.claude}/projects`, where each
  session is a transcript and each subagent a file beside it;
- Codex's sessions root, `${CODEX_HOME:-~/.codex}/sessions`, where each thread is one or
  more rollout files (a compressed `.jsonl.zst` one is found and marked, but not read);
- OpenCode's database, `${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db`, which it
  opens read only and reads only the session, message and part tables of.

It finds a session's records, asking each root in turn and saying for each whether it held
the session, held none, could not be read, or was not asked because the id is not one; only
a root that was read and held none counts as a miss. It lists every source under a root with
the keys a caller needs to tell what changed, and lists a session's subagents: Claude Code's
subagent files, the Codex threads a thread spawned (named in the parent's rollout, each
found by its thread id), and OpenCode's child sessions. Claude Code's listing of a session
holds every agent of the session, nested ones included. A Codex listing, like OpenCode's,
holds the direct children only: a nested spawn is listed by calling `subagents()` on the
child's entry. A spawned Codex thread's header names its parent thread, its depth and its
task.

## The records

Every harness's lines and rows map into the same eight kinds of record:

- `request`, one model request: its id, model, provider, token counts (the whole prompt
  in `input_tokens`, with cache reads and cache writes also counted apart), cost where the
  harness gives one, and whether more of it can still arrive (`final`);
- `message`, a user, assistant, system or developer message, or a thinking item;
- `tool_call` and `tool_result`;
- `marker`, a point in a session such as a compaction, the start or end of a turn, or a
  change of settings;
- `limits`, a rate-limit reading;
- `malformed`, a line or row that could not be read;
- `other`, a line or row no other kind maps, with whether the harness's kind is one the
  mapping knows.

A message says who submitted it, in `injected`. It is true when the harness's own field
for where the message came from names something other than the person at the session's
prompt: the harness itself, a plugin, a channel, another session or a background task.
It is false when that field names the person, or the message is the model's own, and
unknown when the harness writes no such field. Claude Code writes `origin.kind` and flags
the lines it adds or summarises (`isMeta`, `isCompactSummary`); Codex writes which kinds
of content a user message holds; OpenCode marks a text part it added `synthetic`. Where
no such field is written, a short fixed list of wrappers the harness puts around text it
composed (Claude Code's `<local-command-stdout>`, an older Codex rollout's
`<environment_context>`) marks the message true. Three more cases read as true because the
harness wrote the message itself: a Claude Code assistant message whose model is
`<synthetic>`, a Codex `developer` or `system` message, and an OpenCode `summary`
message. A message's text never makes it false.
So a prompt sent through Claude Code's SDK, a command line typed at the prompt, and most
user messages in Codex and OpenCode read as unknown: the harness does not say who wrote
them.

Nothing is dropped: a line sessions cannot map still comes back, as `malformed` or
`other`, so a caller never takes a gap for nothing there. Every record says which harness
and session it came from, where in the source it sits, its time, its turn, and the
harness's own name for the line. A field the harness did not write is `UNKNOWN`, which is
neither None nor 0 and refuses to be read as true or false or added to a number, so an
unknown never turns into a zero by accident.

The record fields follow the OpenTelemetry GenAI semantic conventions where those have a
name: `session_id`, `model`, `provider`, `request_id`, the token counts and a tool's
`name` and `call_id` are the tails of the `gen_ai.*` attributes. They were checked
against the conventions' 1.41.0 release (`OTEL_CONVENTIONS`). The conventions are still
experimental; if one of these names changes there, sessions raises its shape version
rather than renaming the field quietly.

`SHAPE` is that shape version, an integer, now 1. It is the first key of every JSON
document sessions writes, `{"shape": 1, ...}`. Adding a field or a kind keeps the number;
renaming or removing one, or changing what a field means, raises it. A reader takes a kind
it does not know as `other` and skips fields it does not know.

## Reading a session

Five reads take a source (a file, or a session in OpenCode's database) and return its
records with the session's header, all from one snapshot of the source:

- `records` reads every record, oldest first, holding one line at a time.
- `records_back` reads from the end, newest first, for a question about how a session
  ends, without reading the whole file.
- `last_request` is the source's own newest request: on a main session, its own and not
  a subagent's. Its input tokens are the size of the session's current prompt.
- `records_since` reads what was written since a cursor it handed back last time.
  sessions stores nothing; the caller keeps the cursor. A cursor it cannot trust starts
  the read over, and says so.
- `window` reads the last few turns, and pages back from there.

A read that cannot open or read its source raises `Unreadable`, never an empty read. A
read backward gives each field the value a forward read gives it, or `UNKNOWN` where it
cannot know it from the lines it read: a Codex request's provider, for one, when the
setting that names it was written before the turn the read reached.

## Use

Other Moxi modules use it through `moxi_sessions.api` (cache reads its transcripts this
way), and it adds four commands:

- `moxi sessions find SESSION [--harness claude|codex|opencode] [--root PATH] [--json]`
  prints where a session's records are, or every root it looked in and why each held none,
  and exits 1 then. `--root` adds a root to look in after the usual one (for OpenCode, a
  database).
- `moxi sessions root [--json]` prints the root each harness is read from and where that
  root came from: a config key, an environment variable, or the harness's default.
- `moxi sessions list [--harness H] [--since TIME] [--folder PATH] [--limit N] [--text]
  [--json]` prints the main sessions of the three harnesses, newest change first, 30
  unless `--limit` asks for another number. Each line is a session: when it started, when
  it last changed (both in UTC), its harness, the name typed for it in the harness or
  else its folder, and its id. `-` stands for a fact the record did not give. A subagent
  is not a line of its own. `--since` takes the times `read --around` takes, and
  `--folder` an absolute path. Nothing a person or a model said is printed unless
  `--text` is given; then the newest thing said in each session follows its line, cut at
  300 bytes. A root that can't be listed is named on stderr and the others are listed.
- `moxi sessions read SESSION [--harness H] [--around TIME] [--turns N] [--all] [--json]`
  prints part of one session, the way the page below shows it: the last five turns, or
  `--turns` of them (at most 50); with `--around TIME` (ISO 8601 such as
  `2026-11-06T14:03:22Z`, a time with no zone read as local time, or epoch seconds) the
  turns around that time; with `--all` the whole session. Each line starts with its time
  in UTC, in the form `--around` takes. A message shows as its text, cut at 64 KiB with
  the cut said; a tool call as one line naming the tool and what it was called on, cut at
  160 characters; a tool result as its size and whether it failed. No flag prints more of
  a tool's input than that line, or any of a tool's output. A session no root holds, or
  a source that can't be read, exits 1 with the reason.

With `--json`, each command prints one JSON object that starts with the shape version.
`read --json` holds the same rows the page is sent, under `records`, with the header and
whether earlier or later turns were left out. `list --json` holds the counts (`all`,
`total`, one per harness that was read), the roots that could not be listed and the
sessions under `rows`; a row's `activity` and `prompt` carry their `text` only with
`--text`.

**Redaction.** When the secrets module is installed (the `secrets` extra), `read` passes
each message's text, each tool call's line and each title through secrets' `credentials`
and `broad` sets before it is cut, so a cut never leaves part of a secret that no longer
matches. The sets are fixed. Without secrets, `read` prints as written and says so once on
stderr, and `read --json` says `"redacted": false`. `list` passes a session's name
through the same sets, and with `--text` what was said; without secrets it says so when
`--text` is given, and `list --json` says `"redacted": false`. What `list --text` shows
of a message is cut at 300 bytes before it is redacted, and the prompt in `list --json
--text` at 64 KiB, so a secret the cut splits can show its first part. `read` keeps no secret-path rule: a
shell command that reads a secret file shows its first 160 characters, redacted.

## Listing sessions

`sessions_list` (interface 4) answers the main sessions of all three roots as plain data,
for a page or a caller that wants the same list: how many there are before and after its
filters, how many each harness holds, and the sessions in groups. The main list is the
sessions started in a terminal. The first group is those that changed in the last hour
(the setting `list.recent_s`, in seconds); then one group for each of the newest seven calendar days that hold a session,
in the reading process's time zone; then the rest as a count with its oldest change. A
session whose record says a program started it (a Claude Code `entrypoint` starting
`sdk`) is never in the first group: it is counted under its day, apart from the others,
and listed when asked for (`kind` `program`; the others are `main`). A session whose
record does not say how it started is listed with the ones started in a terminal.

A row holds the session's harness, id, path, last change and size, how many subagents it
has, its folder and start, the name typed for it in the harness (never a title the
harness made up), its model, the newest thing said in it and the newest prompt a person
typed. A fact that was not read is None, never zero or an empty text.

A row's `prompt` is the newest message in the whole session that the record marks as
typed by a person, and nothing else. Claude Code writes that mark (`origin.kind` is
`human`). A reply is not a prompt, and neither is text the record marks as injected (a
hook's or a plugin's text, a channel delivery, a task notification) or a message with no
mark, such as a slash command. A session with no marked message has no `prompt`; on a
machine where most sessions are started and fed by other programs, most rows have none.
A session a program started has none either.

For the prompt, a Claude Code session a row shows is read from its start once,
whatever its size. What that read keeps, in memory, is the file's device, inode, size
and modification time, the place just after the last complete line it read, a SHA-256 of
the 4,096 bytes before that place, and the prompt. From then on:

- A file whose device, inode, size and modification time are the kept ones is not read.
- A file that grew is read from the kept place only, as long as the 4,096 bytes before
  it are still the ones that were hashed. The last typed message in the new lines is the
  prompt; when they hold none, the kept prompt stands.
- Any other file is read from its start again: one that shrank, one written again or
  replaced by another file of the same name, one shorter than 4,096 bytes.

A last line with no newline yet is not read for the prompt until its newline is
written, so a message can show as the newest thing said a moment before it shows as the
prompt. The prompts of at most 5,000 files are kept, the least recently used dropped
first. A read that fails gives no prompt for that answer, keeps nothing new, and is
made again for the next.

So the prompt a row carries is the one a read of the whole file would give, with two
limits:

- A file written again so that its device, inode, size and modification time are all as
  before is taken as unchanged and is not read. The last turn's facts have the same
  limit.
- A change that keeps the file's length and touches only bytes more than 4,096 before
  the kept place is not seen: the lines after that place are read, and the kept prompt
  stands.

Claude Code writes a session by adding lines at the end of its file, which is neither
of them.

**Left out.** Codex rows have no `prompt`. A Codex rollout does say which messages were
submitted as a person's text (the `user.text` item kind), but sessions does not read
that kind as a typed mark, so it cannot say a Codex message was typed. To give Codex
rows a `prompt`, sessions would need to read `user.text` as the mark and leave out the
rollouts of `exec` runs, where a program passes the text. OpenCode rows have no `prompt`
because OpenCode's record does not say who submitted a message.

A harness that is not on the machine is not a failure. When a harness's root is its
default one and does not exist, the list holds nothing for that harness: no count and no
reason. When a root you set (`claude.projects`, `codex.sessions`, `opencode.database`)
does not exist, or any root exists and cannot be read, the answer names it under `roots`
with the reason, gives no count for that harness, and still lists the others. When a
root was listed but a folder or a file under it could not be, its sessions are missing
from every count; the answer names that harness under `roots` with no reason and with
`parts`, how many such folders and files there were, and `moxi sessions list` says so on
stderr. A session whose subagents folder could not be listed has no subagent count.

Filters: `harness`, `since` and `until` on the last change, `folder` (the folder the
session's own record names, with or without a slash at its end) and `find` (the start of an id, or part of the folder's last
segment). `sessions_more` answers the next rows of a group from the place an answer gave,
`sessions_newest` the same sessions as one run with no groups (what `moxi sessions list`
prints), `sessions_folders` the folders with how many sessions each holds, and
`session_opened` one session's row with its last reply, the eight subagents that changed last and the
harness's command for picking it up again. Claude Code's and OpenCode's commands work
only in the session's folder, so they come with that folder, and no command is given when
the folder was not read; Codex's runs anywhere and comes with no folder.

A list opens no transcript to find the sessions: it lists each root. To tell the sessions
a program started from the others, it then reads the first lines of every session that
changed in the last hour or on one of the seven days it returns, whether a row shows the
session or not. It reads the last turn of each session its rows show. Every Codex
rollout's first line is read once, whatever its age, since that line says whether
another thread spawned it. Asking by `folder` or `find`, or for the folders, reads every
session's first lines once. What was read is kept in the reading process's memory for as
long as it runs and read again only when a file changed; nothing is written to disk. So the page server answers from what it
has read, and a module that calls `sessions_list` from another process pays its own first
reads. The functions take no cache from their caller, and `recent_s` left out is the
setting `list.recent_s`. A compressed Codex rollout is counted, not
listed.

## How long transcripts last

sessions reads transcripts where each harness keeps them, so nothing outlives a harness's
own cleanup. `retention()` (interface 3) reads each harness's setting as it is set:

- Claude Code: `cleanupPeriodDays` in `settings.json` under
  `${CLAUDE_CONFIG_DIR:-~/.claude}`. Claude Code deletes transcripts older than that many
  days; unset, 30.
- Codex: no setting deletes rollouts by age. The switch
  `features.local_thread_store_compression` in `${CODEX_HOME:-~/.codex}/config.toml`
  compresses rollouts to `.jsonl.zst`, which sessions finds but can't read, and an
  archived thread's rollout moves under `archived_sessions/`, which sessions doesn't read.
- OpenCode: sessions reads no retention setting for it.

sessions' setup prints these lines at install, and `moxi doctor sessions` shows them in its
`retention` row, which warns when Codex compresses rollouts, a settings file can't be
read, or `cleanupPeriodDays` is not a whole number of days, 1 or more. Neither changes a setting: to keep Claude Code's transcripts longer, raise
`cleanupPeriodDays` yourself.

Three settings name a root when it is not the usual one: `claude.projects`,
`codex.sessions` and `opencode.database`. `moxi config sessions` shows them.

## The transcript-read guard

With turns installed (the `turns` extra), sessions offers turns a guard,
`sessions.transcript-read`, that keeps agents out of harness transcripts. It is off until
you turn it on, with `guard.transcript_read` (the install asks) or by adding
`sessions.transcript-read` to turns' `guards.run`:

    moxi config set sessions guard.transcript_read true

When it is on, turns refuses, before it runs, an agent's call that names a path under a
transcript root: Claude Code's Read, Grep and Glob, and a shell command on Claude Code or
Codex. A shell command is split into simple commands, each judged on its own, and one is
refused when one of its words is such a path. The refusal points the agent to `moxi
sessions read <session>`. The roots are each harness's root, the configured one included.

A simple command that runs this environment's own `moxi sessions` passes whatever paths it
names. The command after it on the same line is judged on its own, so `moxi sessions root;
cat <transcript>` is refused. The verbs that pass are the list
`guard.transcript_read_escapes`, `["sessions"]` by default. A module of your own that
reads transcripts for its agents can be let through by adding its verb:

    moxi config set sessions guard.transcript_read_escapes '["sessions", "<verb>"]'

A call it can't judge runs, so one fault never refuses every read; `moxi doctor sessions`
shows the fault in its `guard` row. That row is `off` when the guard is off, `ok` when it
is on and turns is there to enforce it, and `warn` when it is on and turns is not
installed, since then nothing enforces it.

The guard is a nudge, not a boundary. Its known gaps:

- A relative path after `cd`, a variable, or a path a script builds gets past it.
- A path counts only when it is a transcript root or lies under one, so a search that
  starts above a root passes: `grep -r x ~/.claude`, Grep with the path `~/.claude` and
  the glob `projects/**`, or Glob with the path `~/.claude` and the pattern
  `**/projects/*.jsonl`.
- A shell's `-c` script is read only when the options before it are short ones:
  `bash -l -c '...'` and `bash -lc '...'` are read, `bash --login -c '...'` is not.
- OpenCode's root is its database file, so the `opencode.db-wal` and `opencode.db-shm`
  files beside it are not under it.
- The doctor's `ok` means the switch is on and turns is installed. It doesn't check that
  turns' guard loader has the guard on, so a loader that misses it without reporting a
  problem still reads `ok`.
- A word that starts with `/` is read as a path even when it is text, quotes or not: a
  heredoc line naming a transcript, or `echo '<transcript path>'`, is refused. A quoted
  sentence that holds such a path is one word that doesn't start with `/`, and passes.

## The page

With the `ui` extra (`moxi-ui`, Moxi's browser UI shell), sessions has a page in the shell.
At its own address it lists the sessions; at a session's address it shows that session.

**The list.** The sessions started in a terminal that changed in the last hour come first,
under `Active recently` (the setting `list.recent_s`). Then one group per day, for the
newest seven days that hold a session, and `Earlier` for the rest, with how many there are
and how far back they go; its `Show` brings the next older days. A day shows its first
eight sessions, and `N more · Show` adds the next thirty. The sessions a program started
are not in those rows: a day that has any ends with `N started by a program · Show`, which
shows them under the line and hides them again.

A row is named by the name typed for the session in its harness, with its folder beside
it; without one, by the folder and when it started. Its second line is the newest message
a person typed in the session, with its time and `You:`, and `2 subagents` before it when
there are any. A session with no such message, and every Codex and OpenCode session, is
one line. Most rows are one line.

Above the list, the find field takes a session id or the start of one, or part of a
folder's name, from two characters on, and its matches replace the groups. Under it are
three filters: Folder (the folders the sessions ran in, which you can narrow by typing),
Harness (the harnesses that have sessions) and When (today, the last 7 or 30 days, or a
range of days), and the count: `62 sessions`, or `15 of 62 sessions` once something
narrows the list. The list is read when the page opens, when its tab is shown again, and
every 30 seconds while the tab is visible; groups you showed more of stay as they were.

A row opens in place, by its `Open` or by a click, Enter or Space on its line, and several
can be open at once. The session is read when its row is opened. Opened, a row shows the
newest message a person typed (`You`) and the model's newest message after it (`Reply`),
each with its time and cut at 600 characters; up to eight of its subagents, the ones that
changed last, each with what it was asked, its type, when it ran and the newest thing it
said; the `Session` line (the harness, the model, from the start to the last change); and
`Resume`, the harness's own command for picking the session up again, with the folder to
run it in where the harness needs one, and `Copy`, which puts the command on the
clipboard. A folder under your home folder is written with `~`, here and in the Folder
menu; any other folder is written whole. A piece the record does not give is not drawn. Under them `Read the
transcript` goes to the session's address and `Copy id` copies its id. An opened row is
read again after a newer list only when that list shows the session changed. Leaving the
list for a transcript and coming back gives the list from its top, with no filter set
and no row open.

Under the list a dim line names each harness whose sessions could not be read, with the
reason, and says how many Codex sessions are left out because their files are compressed.
On a machine with no session the page says so in place of the list.

**One session.** A session's address is `<harness>/<session id>`, or
`claude/<session id>/<agent id>` for a Claude Code subagent. An OpenCode child session and
a Codex subagent thread each open at their own id. Over the session, the first line has
`Sessions`, which goes back to the list, the session's name as the list names it (a name
typed for it, else its folder and when it started) and `Refresh`. The second has the
harness, the model and from when to when, and `Details`, which shows and hides the rest:
the harness's name, the session's id, its title, directory, last activity, version,
parent session, entrypoint and the file it was read from. A subagent, and a session
another one started, is not in the list, so it keeps a heading that names it by its id or
title, with those facts in the open. Under the header come the session's last five turns,
oldest first. Each message shows as
text, cut at 2,000 characters with a way to show the rest; each tool call as one line
naming the tool and what it was called on; each tool result as its size and whether it
failed; compactions, aborted turns and rollbacks as labelled rules; and each request's
model and token counts. "Earlier" shows the turns before. The page lists the session's
subagents, and opening one shows its turns the same way. While the turns are read the page
says so. A session that can't be found or read shows why, and when its harness's sessions
were not all read the line that says so under the list stands under the header too.

The page server runs in the shell's environment, so the roots it reads are the ones that
environment and sessions' settings name, which can differ from your terminal's.

Other modules' pages can place the one-session view with the shell's `place()`, as the
`window` block, with the attributes `harness` (`claude`, `codex` or `opencode`), `session`
and, for a Claude Code subagent, `agent`. It links to the page at the session's address. A
subagent chosen in the block opens in the block, with Back to return.

The list is four reads of the page server: `/api/list` (the counts and the groups, with
the first 30 recent rows and the first 8 of each day), `/api/more` (the next 30 rows of a
group, or the next older days), `/api/session` (one session opened) and `/api/folders`.
`/api/list` and `/api/session` also carry `home`, the home folder of the account the page
server runs as, which is what the page writes as `~`. The header over one session asks
`/api/list` for that session by its harness and id, which reads every session's first
lines once, as any `find` does.
They take the list's filters as query keys and read the setting `list.recent_s` at each
request. Of a message they send at most 64 KiB, saying when it was cut; of the newest
thing said on a row and of a subagent's last message, 300 bytes. The server keeps what
the list read in memory for as long as it runs, and keeps no more of a message than it
sends.

The page only reads. It sends no tool call's input and no tool's output, and sends at most
64 KiB of any one message, saying when it cut one. It reads a session when it is shown and
when you ask it to, and the list as said above; the page server runs nothing in the
background. Without the extra, nothing of
sessions changes.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install sessions
```

The core's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi/tree/main/packages/moxi), covers
the `moxi` command and how `moxi install` works; the repository's README lists the other
modules.

## Dependencies

`moxi`, Moxi's core, and nothing else. The optional extra `ui` adds `moxi-ui`, the browser
UI shell, whose page-server base sessions' page server runs on. The optional extra
`secrets` adds `moxi-secrets`, which `moxi sessions read` redacts with. The optional
extra `turns` adds `moxi-turns`, which enforces the transcript-read guard.

## Interfaces

`moxi_sessions.api` offers interfaces 2, 3 and 4. Interface 3 is interface 2 plus
`retention()` and `Retention`; a module that calls them loads sessions with
`interface=3`. Interface 4 is interface 3 plus the list's reads: `sessions_list`,
`sessions_more`, `sessions_newest`, `sessions_folders` and `session_opened`; a module
that calls them loads sessions with `interface=4`.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/sessions/LICENSE).
