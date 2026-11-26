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

For Claude Code and Codex it can also read archive folders, older transcripts kept
outside the harness's root (see [Archive folders](#archive-folders)), only for a caller
that asks for them.

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
  the read over, and says so. A Claude batch also names the prompt ids its lines moved
  the turn to (`prompt_ids`), a prompt that drew no request included, whatever kinds it
  keeps.
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
  and exits 1 then. It looks in the live root, then the harness's archive folders, then
  each `--root` (for OpenCode, a database). When no folder holds the session and an
  archive folder didn't answer, it says the session is unknown, names the folder, and
  exits 1.
- `moxi sessions root [--json]` prints the root each harness is read from and where that
  root came from: a config key, an environment variable, or the harness's default; and
  each archive folder, without testing it.
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
  prints part of one session, the way the page below shows a transcript: the last five turns, or
  `--turns` of them (at most 50); with `--around TIME` (ISO 8601 such as
  `2026-11-06T14:03:22Z`, a time with no zone read as local time, or epoch seconds) the
  turns around that time; with `--all` the whole session. Each line starts with its time
  in UTC, in the form `--around` takes. A message shows as its text, cut at 64 KiB with
  the cut said; a tool call as one line naming the tool and what it was called on, cut at
  160 characters; a tool result as its size and whether it failed. No flag prints more of
  a tool's input than that line, or any of a tool's output. Without `--harness` it reads
  the session from whichever harness's live root holds it, and asks the archive folders
  only when none does; when more than one harness holds it, it names them and exits 2 for
  `--harness` to choose. With `--harness` it asks that harness's live root, then its
  archive folders. A session no folder holds, or a source that can't be read, exits 1
  with the reason. A session no folder holds while an archive folder didn't answer is
  unknown: the read says so, names the folder, and exits 1.

With `--json`, each command prints one JSON object that starts with the shape version.
`read --json` holds the same rows the page is sent, under `records`, with the header and
whether earlier or later turns were left out. `list --json` holds the counts (`all`,
`total`, one per harness that was read), the roots that could not be listed and the
sessions under `rows`; a row's `activity` and `prompt` carry their `text` only with
`--text`.

**Redaction.** When the secrets module is installed (the `secrets` extra), `read` passes
each message's text, each tool call's line and each title through secrets' `credentials`
and `broad` sets before it is cut, so a cut never leaves part of a secret that no longer
matches. The sets are fixed. A `gitleaks:allow` mark beside a secret doesn't keep it, nor
does a line a rule's allowlist names (a secret mount, say), and a token split by an
invisible character (a zero-width space, a soft hyphen) is still found and redacted. Without secrets, `read` prints as written and says so once on
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
prints), `session_dirs` the folders with how many sessions each holds, and
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

## Naming sessions by key

`session_facts(keys)` (interface 5) is for a caller that already holds sessions' keys
from somewhere else, such as a page that ranks sessions by a figure of its own, and
wants to show each by the name the list shows. `keys` is up to 200 `(harness, session)`
pairs:

```python
from moxi import modules

sessions = modules.load("sessions", interface=5).api
answer = sessions.session_facts([("claude", "0190b000-0000-7000-8000-000000000001"),
                                 ("codex", "0190c000-0000-7000-8000-000000000002")])
# {"sessions": [{"harness": "claude", "session": "0190b000-0000-7000-8000-000000000001",
#                "name": "fix the tests", "folder": "/work/proj-a",
#                "started": 1793354400.0},
#               {"harness": "codex", "session": "0190c000-0000-7000-8000-000000000002",
#                "name": None, "folder": "/work/proj-b", "started": 1793354405.0}],
#  "roots": []}
```

`sessions` holds one item for each pair that is a main session of a root, in the order
asked. `name` is the name a list's row carries, read by the same code: the name typed for
the session in the harness, never a title the harness made up; it is None for a Codex or
an OpenCode session. `folder` and `started` come from the first lines of the session's
record. Each of the three is None where it was not read, so a session whose file cannot
be read is answered with three Nones.

A pair with no item is no main session of any root: its record is gone, or it is a
compressed Codex rollout, a subagent, or a Codex thread another thread spawned. `roots`
holds `{harness, root, reason}` for each harness whose root was not listed whole, as a
list's `roots` does: with a `reason` when the root could not be listed, with `reason`
None when some folders or files under it could not be. A pair of such a harness that has
no item may be in the part that was not listed, so "no item" means "not held" only for a
harness that is not in `roots`.

It raises ValueError for more than 200 pairs, for a key that is not a pair and for a
harness that is not one, and never for a session that cannot be read.

It reads each root's listing (and every Codex rollout's first line, once), then, of each
session it answers, the first lines and one turn, the last; it reads no turn of a Codex
or an OpenCode session, which have no name. It keeps a name or None for at most 5,000
sessions in the reading process's memory, for as long as the file's size and mtime
stand, and no other text of the turn. A session a list has already read is named with
no further read. A list reads the last turn of a session that was only named, so a row
loses nothing to this cheaper read.

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

## Archive folders

A harness deletes or moves old transcripts, so you may keep copies elsewhere: copied out
before Claude Code's cleanup, or on another machine's disk. Name those folders in
sessions' config and sessions reads them after the live root:

    moxi config set sessions claude.archives '["/mnt/old/claude-projects"]'
    moxi config set sessions codex.archives '["/mnt/old/codex-sessions"]'

Each folder is laid out as the harness lays out its root: Claude Code's
`<project>/<session>.jsonl` (subagent files beside it as usual), Codex's
`YYYY/MM/DD/rollout-...jsonl`. They are read in the order named, after the live root, and a
session an earlier folder holds is read from that folder only, whatever the copies' times.
OpenCode has no archive key: its records are one database the harness keeps. None is
configured by default.

**sessions never writes, creates, moves, renames, deletes or locks anything in an archive
folder, and never changes a mode there.** It opens files there read only. Claude Code
can't resume a session from an archive folder, and nothing cleans one but you.

**Offline is not gone.** A folder answers when it can be listed within `archives.answer_wait_s`
(5 seconds by default; raise it for a slow link). Before a call asks archive folders,
sessions tests every one it will ask together, one child process each, and waits for all
of them at most once, however many don't answer. A folder that is absent, isn't a
directory, fails, or doesn't answer in time is offline: a read skips it and says so, and it
is never read as holding no session. A process keeps each folder's answer for a minute.
Once a folder has answered, its listing and reads run in the calling process, and a mount
that stops answering partway can hang that call; the wait doesn't bound that.

Only a caller that asks pays that wait:

- `moxi sessions read` asks the archives only when no live root holds the session, and
  `moxi sessions find` asks them when the live root doesn't hold it.
- Through the API, `find(harness, session, include_archives=True)` asks the live root,
  then each archive folder; `find` without it and `sources` ask the live root only, as
  before. `corpus(harness, since=None, heads=True)` lists every folder of a harness, the
  live root and then each archive folder, one `Listing` each, for a caller that walks
  them all.
- Every other reader (the sessions page, the list, other modules' own lookups) reads the
  live root only. A session only in an archive folder is refused by the page, which names
  the root it asked.

What the API says of a folder: a probe of one that doesn't answer is `unreadable: <why>`,
never `miss`; `Found.unknown` is true when no folder asked holds the session and one
didn't answer; a `corpus` listing of one is `ok=False` with its reason, never an empty
listing. A `corpus` listing records in `unreadable`, at any depth, a directory it can't
list, a file it can't open, and an entry it listed and then found gone, since a mount that
drops partway often shows as files not found. It marks a folder whose top level holds no
entry `empty`, since a disk that isn't mounted can leave its mount point an empty
directory, and `archive_answers(folders, fresh=True)` says the same of a folder now. A live
root that doesn't exist is `absent` and the archive folders are listed; a live root that
exists and can't be listed lists none of them, since which of their sessions it holds is
not known. Every Codex folder is read with the live root's imports file.

Setup prints each archive folder and whether it answers now; `moxi doctor sessions` has an
`archives` row, `off` with none configured, `ok` when each answers, and `warn` naming each
that doesn't, with the fix "make it answer, or take it off
`claude.archives`/`codex.archives`". `retention()` names each harness's archive folders in
`archive_dirs` without touching them.

## The transcript-read guard

With turns installed (the `turns` extra), sessions offers turns a guard,
`sessions.transcript-read`, that keeps agents out of harness transcripts. It is off until
you turn it on, with `guard.transcript_read` (the install asks) or by adding
`sessions.transcript-read` to turns' `guards.run`:

    moxi config set sessions guard.transcript_read true

When it is on, turns refuses, before it runs, an agent's call that names a path under a
transcript root: Claude Code's Read, Grep and Glob, and a shell command on Claude Code or
Codex. turns' shell reader splits a shell command into simple commands, each judged on its
own, and so are the commands of each substitution in it, a `$(...)` in double quotes or
in an unquoted heredoc's body included; one is refused when one of its words, or the file
one of its redirects names, is such a path. Where bash splits an unquoted `${...}`
default or alternate into words (`cat ${x:- <transcript> }`), each of them is judged as a
word. A script a command hands a shell is read the
same way, in that shell's language: a `-c` argument (`bash -lc`, `bash --login -c`,
`bash -co pipefail`), a heredoc or here-string fed to a shell, literal text `echo` or
`cat` pipes into one, and `pwsh -Command` and `cmd /c` scripts. The refusal points the
agent to `moxi sessions read <session>`. The roots are each harness's root, the configured one included,
and each archive folder. An archive folder is compared by its path as written, with a
trailing `/`, `//`, `.` and `x/..` folded by the text alone, and is never resolved, so
judging a call touches no archive folder.

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
- A heredoc's body fed to anything but a shell is not read as commands; only the
  substitutions of an unquoted one are.
- A path counts only when it is a transcript root or lies under one, so a search that
  starts above a root passes: `grep -r x ~/.claude`, Grep with the path `~/.claude` and
  the glob `projects/**`, or Glob with the path `~/.claude` and the pattern
  `**/projects/*.jsonl`.
- A script is read only where turns' shell reader finds one: a script file, text handed
  to a program other than a shell (`python -c`, `ssh`), a file or another program's
  output piped into a shell, and a shell option the reader doesn't know get past it. A
  script inside a script is read three deep; past that its words are judged as words.
- A path that reaches an archive folder through a symbolic link passes, since the guard
  compares an archive folder by its written path and never resolves it.
- OpenCode's root is its database file, so the `opencode.db-wal` and `opencode.db-shm`
  files beside it are not under it.
- The doctor's `ok` means the switch is on and turns is installed. It doesn't check that
  turns' guard loader has the guard on, so a loader that misses it without reporting a
  problem still reads `ok`.
- A word that starts with `/` is read as a path even when it is text, quotes or not:
  `echo '<transcript path>'` is refused. A quoted
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
one line. One-line rows are expected: on the machine this was built on, 50 of the 133
rows of a week's sessions started in a terminal had a second line.

Above the list, the find field takes a session id or the start of one, or part of a
folder's name, from two characters on, and its matches replace the groups. Under it are
three filters: Folder (the folders the sessions ran in, which you can narrow by typing),
Harness (the harnesses that have sessions) and When (today, the last 7 or 30 days, or a
range of days), and the count: `62 sessions`, or `15 of 62 sessions` once something
narrows the list. The list is read when the page opens, when its tab is shown again, and
every 30 seconds while the tab is visible; groups you showed more of stay as they were.

**Running now.** With launcher installed, the sessions it runs now come first, under `N
running`, in the order Home draws them: a session that waits on agents while nothing will
keep its cache warm leads. A row is named by the launch's name, with where it runs beside
it (`slot 3 · atlas-api`), its cache fact (`goes cold at 14:20`), what it waits on
(`waiting on 2 agents`) and its newest message, or `launched` before it has one. A running
session is drawn in this group only, not again under `Active recently` or its day, though
`N more` still counts it. With a filter set the group keeps the launches whose session
the narrowed list holds; under the find field there is no group. With nothing running
there is no group, and a read that fails draws `Running` with `Moxi couldn't read the
running sessions`. A waiting session whose cache a press would cover has `Keep warm` at the
desk (not through the phone gateway): it asks launcher to keep that cache warm, and stays
disabled until launcher has answered and its next value has come, or for 20 seconds; a
refusal's words show on the row until the next value. A launch whose session id launcher
has not learned has nothing to open. Without launcher none of this is drawn and the list
starts with `Active recently`.

**Live state.** With turns installed too, each running row says what turns says of its
session, paired by session id as turns' Live sessions panel pairs them: `working` while a
turn is open, and no state word while it is idle, since its time says since when; then
what is busy (`1 subagent busy`, `2 teammates busy`, `3 shells running`, Codex's background
terminals among the shells). After a heavy `waiting on 2 subagents`, which counts
subagents and teammates, neither is counted again; shells still are. A session turns
can't read says so on a dim third line in turns' words (`Moxi can't read its live state:
…`), and so does every launch with a session id when turns' list can't be read. An opened running row adds turns' `Now` (`working on turn 9
since 18:34`), `Agents` (each subagent, teammate and shell with its state) and `Prompt
box` lines. A launch turns has no session for says nothing of it. Without turns, and
through the phone gateway, none of this is drawn and turns is not read. With sessions
installed, turns' `Live sessions` leaves the band of pages, and the list links it instead:
`Live sessions`, worded with turns' page title, at the right of the filter line before the
count, or alone at the right of its own line wherever no filter line is drawn. It is there
in every state of the page and opens `/m/turns/`. It is drawn only with turns installed,
and not through the phone gateway.

**Cache.** With cache installed, at the desk, the list reads cache's `/api/clocks` when it
opens and when its tab is shown again, and follows cache's topic `clocks`. A row of
`Active recently` whose session cache keeps a clock record for, matched by harness and
session id, ends its first line in a dim `warm until 19:12`, or `cold` once that time has
passed on cache's clock; the list is drawn again at that time, so the row turns cold with
no read. Opened, the row has a `Cache` line, `warm until 19:12` or `cold since 18:35` (with when cache last read it,
where cache has not rewritten the record lately), and a `Cache` link to cache's page.
Which records have a clock time and when each goes cold are cache's rules. On a running
launch launcher's cache fact wins, as it does on Home and the Comms page: the row's fact
and its `Cache` line are launcher's (`kept warm while 1 agent works` after a covered
one's), and cache's record is used only where launcher gives the launch no cache. A
record with no clock time, a session with no record and a read that fails draw nothing.
The rows of a day's group, of `Earlier` and of a find say nothing of cache, closed or
opened, whatever cache holds for them.
Without cache, and through the phone gateway, none of this is drawn and cache is not read.

A row opens in place, by its `Open` or by a click, Enter or Space on its line, and several
can be open at once. The session is read when its row is opened. Opened, a row shows the
newest message a person typed (`You`) and the model's newest message after it (`Reply`),
each with its time and cut at 600 characters; up to eight of its subagents, the ones that
changed last, each with what it was asked, its type, when it ran and the newest thing it
said; the `Session` line (the harness, the model, from the start to the last change); the
`Cache` line, with cache's or launcher's fact (above); and `Resume`, the harness's own command for picking the session up again, with the folder to
run it in where the harness needs one, and `Copy`, which puts the command on the
clipboard. A folder under your home folder is written with `~`, here and in the Folder
menu; any other folder is written whole. A piece the record does not give is not drawn. Under them `Read the
transcript` goes to the session's address and `Copy id` copies its id. An opened row is
read again after a newer list only when that list shows the session changed.

**The list's address.** The list keeps its state in the query of its own address,
`/m/sessions/`: `folder` (an absolute path), `harness`, `when` (`today`, `7d`, `30d`, or a
range of days such as `2026-11-01..2026-11-05`, either end left open), `find` (the text as
it was sent), `more` (how many times each group's `Show` read more, such as
`recent:1,2026-11-09:2`) and `program` (each day whose program line is open, and how far it
was paged). A key is written only when it differs from its default, so the plain list is
`/m/sessions/` with no `?`. Each change rewrites the address in place and adds no history
entry, so one Back leaves the list. Back from a transcript, a reload and a link opened
elsewhere bring the list back with its filters, find and depth: the list reads each group
again to the depth the address names, one read at a time and at most 40 reads, then writes
the depth it reached. Back and a reload also bring back the rows that were open and the
scroll, which the browser's history entry keeps; a link carries neither. The scroll lands
once those rows and, with launcher installed, the running group are drawn. A transcript
opened from the list carries the list's query in its own address, on through its subagents
and its parent, and its `Sessions` link goes back to the list with it.

An address is a request. A value the list's own controls could have set is taken. Anything
else (a harness the list does not know, a folder that is not absolute, a `when`, `more` or
`program` value of another form, a count of 0, a group or a key given twice, a key the list
does not use) is dropped before the list is read, and one dim line above the list names
it: `This address held harness=foo, which the list does not take; the list is shown
without it.` A value that matches nothing narrows the list to nothing, as the filter would.
A depth the list cannot reach is read as far as the list goes. Once the list has read to
the address's depth, the address is rewritten to say exactly what the list shows.

Under the list a dim line names each harness whose sessions could not be read, with the
reason, and says how many Codex sessions are left out because their files are compressed.
On a machine with no session the page says so in place of the list.

**One session.** A session's address is `<harness>/<session id>`, or
`claude/<session id>/<agent id>` for a Claude Code subagent. An OpenCode child session and
a Codex subagent thread each open at their own id. Over the session, the first line has
`Sessions`, which goes back to the list (as it was left, when the session was opened from
it), the session's name as the list names it (a name
typed for it, else its folder and when it started) and `Refresh`. The second has the
harness, the model and from when to when, and `Details`, which shows and hides the rest:
the harness's name, the session's id, its title, directory, last activity, version,
parent session, entrypoint and the file it was read from. Where the turns reach a Claude Code
session's start, the line there reads `Started today 07:40 in harbor-infra, by hand` when
both of these say a person started it: its head names an entrypoint that isn't a
program's (`by_program` false), and launcher, asked once when the transcript opens,
refuses the session as never launched. Anywhere else (a Codex or OpenCode session, whose
record doesn't say who started it, a Claude Code session whose head wasn't read or names
a program, one a launch holds, or no launcher) the line is the plain `This is the start
of the session.` A subagent, and a session
another one started, is not in the list, so it keeps a heading that names it by its id or
title, with those facts in the open. Under the header come the session's last five turns,
oldest first. Each message shows as
text, cut at 2,000 characters with a way to show the rest; each tool call as one line
naming the tool and what it was called on; each tool result as its size and whether it
failed; compactions, aborted turns and rollbacks as labelled rules; and each request's
model and token counts. A result that failed starts with `Failed`, and the message Claude
Code writes for a request that failed starts with `Error`, each in heavier text: the page
marks what went wrong with a word, not a colour. "Earlier" shows the turns before. The page lists the session's
subagents, and opening one shows its turns the same way. While the turns are read the page
says so. A session that can't be found or read shows why, and only that: the list's line
for a harness whose sessions were not all read is not repeated over a transcript.

The page server runs in the shell's environment, so the roots it reads are the ones that
environment and sessions' settings name, which can differ from your terminal's.

Other modules' pages can place the one-session view with the shell's `place()`, as the
`window` block, with the attributes `harness` (`claude`, `codex` or `opencode`), `session`
and, for a Claude Code subagent, `agent`. It links to the page at the session's address. A
subagent chosen in the block opens in the block, with Back to return.

**On the Projects page.** With the `projects` extra too, and projects' page in the shell,
sessions places a `project-facts` component on the Projects page. A project's row gets
when it was last active (the newest change of any of its sessions) and `N active
recently`, its sessions that changed within `list.recent_s`. sessions reads every session,
and draws none of these facts when one can't be read, so `not used yet` means the project
has no session. An opened project gets a `Sessions` row, `N sessions this week` with a
link to this page, left out when none changed in the last 7 days; and Remove lists `its N
sessions and their transcripts` among what stays. Under the projects, `Not a project`
lists up to 20 folders no project holds where a session changed in the last 7 days, each
with `N sessions this week`, and says how many more there are. A session a program started
counts toward a project's last activity and its Remove line, never toward `active
recently` or the week. The component reads the page server when it is placed and every 60
seconds while the tab is visible. It reports nothing, so the page draws none of these
facts, when projects is not installed or is too old, when projects' store can't be read,
when a session can't be read (a harness's sessions folder exists and can't be listed, a
folder or a file under it that may hold a session can't be listed, or a session's first
lines can't be read: it would be missing from the counts), and when the read fails. A
session's subagents folder that can't be listed holds no session of its own, so it changes
no count and the facts still show.

Its read is `/api/projects`. It reads every session's first lines, once for as long as the
page server runs, as `/api/folders` does, then asks projects' read API (`for_paths`) once
which project holds each folder the sessions ran in; sessions never decides that itself.
It answers `projects` (`{key, at, all, week, recent}` each: the newest change, how many
sessions, and the sessions changed in the last 7 days and within `list.recent_s`, each
named `<harness>/<session id>`), `folders` (`{folder, at, week}`) and `more`, beside
`now`, `today`, `home`, `recently_s`, `state` and `detail`. `state` is `up`, `off`
without projects or with one older than its read API's interface 2, or `error`; `detail`
is then a fixed sentence that names no file, and an `error`'s reason goes to the page
server's log.

The list is four reads of the page server: `/api/list` (the counts and the groups, with
the first 30 recent rows and the first 8 of each day), `/api/more` (the next 30 rows of a
group, or the next older days), `/api/session` (one session opened) and `/api/folders`.
`/api/list` and `/api/session` also carry `home`, the home folder of the account the page
server runs as, which is what the page writes as `~`, and `today`, the server's local date.
The header over one session asks `/api/session` for that one session beside its turns,
which does not search every session's first lines as a `find` does. On a page server's
first open the name comes a fraction of a second after the turns, while the server lists
the sessions it can show.
They take the list's filters as query keys and read the setting `list.recent_s` at each
request. With launcher installed the page also reads three of launcher's routes and its
push topic: `/api/running` (the launches running now) with each read of the list; the topic
`running` at the desk, which brings each new value as launcher has it; `/api/keep-warm` on a
`Keep warm` press; and `/api/state?session=<id>` once when a Claude Code session's transcript
opens. With turns installed, at the desk, it reads turns' `/api/sessions` with each read of
the list and follows turns' topic `sessions`. Of a message they send at most 64 KiB, saying when it was cut; of the newest
thing said on a row and of a subagent's last message, 300 bytes. The server keeps what
the list read in memory for as long as it runs, and keeps no more of a message than it
sends.

The page only reads. It sends no tool call's input and no tool's output, and sends at most
64 KiB of any one message, saying when it cut one. It reads a session when it is shown and
when you ask it to, and the list as said above; the page server runs nothing in the
background. Without the extra, nothing of
sessions changes.

**Left out.** The page shows what sessions reads from the harnesses' own records and
nothing else. A piece whose fact sessions does not read is not drawn: no empty place, no
zero and no greyed control stands in for it. What is left out, and what each needs:

- A second line on Codex rows: sessions would need to read the rollout's `user.text` item
  kind as the mark of a typed message (see "Listing sessions"). A second line on OpenCode
  rows: OpenCode's record has no such mark to read.
- Find over names and typed prompts: it needs an index of them in sessions, which is a
  store with what was typed in it. Find takes an id or a folder's name.
- The template a running session was started from, the effort on the `Session` line, and
  `Resume` that starts the session from the page: they need more of launcher's read of
  what it launched than `/api/running` gives.
- A session that waits for an answer: it needs turns to say when a session asks you
  something.
- A Project filter in place of Folder, and the project beside a row's name: the
  `projects` extra answers many folders' projects in one read, and the list doesn't use
  it yet.
- Whether the cache of a session launcher doesn't run is warm or cold and until when,
  with a way to keep it warm: it needs the cache module's state by session.
- Cost, the number of prompts and the context's size, on a row or on a transcript: they
  need the usage module's figures by session.
- Mail counts, such as one message held for a session: they need comms' count of mail
  held by name.
- A time zone and a clock of your choice for the page's times: it needs a setting in the
  core. Times are written as the shell writes them.
- The phone: the shell's phone view does not offer this page. It needs sessions' reads
  opened to the phone.

## In the Claude apps

With its `connector` and `secrets` extras, sessions serves two read-only tools to the
Claude apps through the connector, Moxi's public MCP host:

```sh
moxi install sessions --with connector,secrets
```

That installs `moxi-sessions[connector,secrets]`. The `connector` extra brings the host;
the `secrets` extra brings secrets, whose redaction every tool needs. Install both: while
secrets is missing, or one of its chat sets can't run, every tool refuses `redaction-off`
and the `sessions redaction` row of `moxi doctor connector` says why.

**`list_sessions`** lists recent sessions, newest change first. Inputs: `harness`
(`claude`, `codex` or `opencode`), `since` (an age such as `7d` or `12h`, or an ISO 8601
time) and `limit` (1 to 30, default 10). Each row gives `session`, `harness`, `kind`
(`main`, or `program` for one a program started), `started` and `changed` in UTC, `name`
and `folder`. `folder` is the last part of the session's folder only, never its full
path. Roots that couldn't be listed are named in `unlisted`, by their last part too.

**`read_session`** reads part of one session's transcript. Inputs: `session` (required),
`harness` (needed only when more than one harness holds the id), `agent` (a subagent's
id at any depth, as `search_memory` gives it, to read that subagent's own transcript; a
Codex or OpenCode subagent's subagent is found down the session's tree), `around` (an
ISO 8601 time or epoch seconds; left out, the last turns), `turns` (1 to 10, default 3)
and `thoughts` (default false).
It finds a session as `moxi sessions read` does, archive folders included, and reads the
records, since only a record says who wrote a message (`writer`, under Interfaces):

- A message's text shows when its writer is the person at the prompt, the model writing
  its own turn, or another agent's session (`peer` or `coordinator` mail, named on the
  row). A message whose writer sessions doesn't know shows when its text doesn't open
  with `<` and a letter. In each case a text that opens with an output wrapper
  (`OUTPUT_WRAPPERS`) shows as one line instead. The text goes through the chat
  redaction whole, then is cut at 4,000 characters, the cut said.
- Every other message is one line of its kind and its size in characters: `unknown
  writer`, `channel`, `hook`, `task`, `harness`, `summary` (a compaction summary) or
  `command output`.
- The model's thoughts show only with `thoughts`.
- A tool call is its one-line summary, redacted before its cut. A call whose input names
  a secret path shows as its tool and that path only: `cat ~/.env` shows `Bash(.env)`.
- A tool result is its size and whether it failed. No tool output shows.

The header gives `session`, `harness`, `kind`, `title` and `folder` (its last part), and
`earlier` and `later` say whether turns were left out on either side. An answer holds at
most 40,000 characters of text: the oldest turns go first, so the turn around `around`
(or the last turn) always stays, and inside one turn too long by itself, the rows
farthest from that point go first. Refusals: `bad-session`, `unknown-session` (naming an
archive folder that didn't answer, by its last part), `ambiguous`, `unknown-agent`,
`unreadable`, `bad-around`, `redaction-off`, `busy` and `timeout`.

**Local paths.** No full local path leaves through `read_session`. In every string it
shows (message text, tool-call lines, the title, the folder, refusal texts), each path
under a root is cut to its last segment, so `Read(/home/you/notes/x.md)` shows as
`Read(x.md)`, and each loopback origin (`localhost`, `127.0.0.1`, `[::1]` and the like,
with any scheme, port and path) shows as `local`. The roots are your home folder, `~/`,
`$HOME/`, `/home/`, `/tmp/`, `/root/`, `/etc/`, `/var/`, `/usr/`, `/opt/`, `/mnt/`,
`/media/`, `/run/` and `/srv/`, in any case; a path runs to the first space, quote,
backtick or one of `<>|,;(){}[]`, and takes a redaction placeholder in it whole, so
`/home/you/[REDACTED:github-pat]/x.md` shows as `x.md`. A bare root shows its own name
(`/home/` shows `home`, `~/` shows `~`). The cut runs inside `chat_redact`, between its
two passes, and so do the folder's cut and the squeeze that puts a tool call's argument on
one line (before the path cut). Only a cut to length comes after, so the last redaction
sees exactly the text sent. A path outside the roots in a message stays whole;
the `folder` is always its last part. A refusal or `unlisted` reason that names a path
sessions holds (a root, an archive folder, the session's file and its folder) cuts that
path to its last part wherever it occurs, under any root or none, and a path under the
same top folder (`/data/` for `/data/sessions`) too.

**Redaction.** Every string an answer holds goes through secrets' `chat_redact`, which
redacts with its chat sets (`credentials`, `broad` and `personal-data`) in strict mode:
message text, tool-call lines, names, titles, folders, and refusal texts that name a
folder. Session ids, harness names, kinds, times, counts and flags come from a fixed
vocabulary or format and go as they are. `moxi sessions read` keeps its own redaction and
shows more than these tools do.

**Limits.** At most two calls of sessions' tools run at once; a third waits up to 5
seconds and then refuses `busy`. A call still running at 20 seconds refuses `timeout`.

**Status.** `moxi connector status` and `moxi doctor connector` show a `redaction` row
(whether the chat sets load, and secrets' engine) and a `roots` row (each harness's root,
and any that can't be listed).

**The call log** records `rows` for `list_sessions`, and `harness`, `turns`, `rows`,
`cut` and `redactions` for `read_session`, each with `refusal`. It never holds a session
id or text read.

What a chat that reads these tools can do with what it read, and what redaction doesn't
catch, is in the connector's
[docs/security.md](https://github.com/CodeManEthan/moxi/blob/moxi-sessions-v0.2.10/packages/connector/docs/security.md).
Read it before you turn the tools on.

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
`secrets` adds `moxi-secrets`, which `moxi sessions read` and the tools in the Claude
apps redact with. The optional
extra `turns` adds `moxi-turns`, which enforces the transcript-read guard. The optional
extra `projects` adds `moxi-projects`, whose read API (interface 2, `for_paths`) says which
project holds each folder, for the facts on the Projects page; a projects that doesn't
offer it reads as absent.
The optional extra `connector` adds `moxi-connector`, the public MCP host, and
python-stdnum for the chat redaction: it serves the tools of "In the Claude apps", and
needs the `secrets` extra beside it.

## Interfaces

`moxi_sessions.api` offers interfaces 2 to 7. Interface 3 is interface 2 plus
`retention()` and `Retention`; a module that calls them loads sessions with
`interface=3`. Interface 4 is interface 3 plus the list's reads: `sessions_list`,
`sessions_more`, `sessions_newest`, `session_dirs` and `session_opened`; a module
that calls them loads sessions with `interface=4`. Interface 5 is interface 4 plus
`session_facts`; a module that calls it loads sessions with `interface=5`. Interface 6
is interface 5 plus the archive folders: `corpus`, `find`'s `include_archives`,
`Found.unknown`, a listing's `is_archive`, `absent`, `empty` and `shadowed`,
`archive_dirs`, `archive_answers`, `ArchiveAnswer`, and `Retention`'s `archive_dirs`; a
module that calls them loads sessions with `interface=6`. Interface 7 is interface 6
plus who wrote a message and what it opens with: `writer`, `WRITERS`, `OPENING`,
`opening`, `OUTPUT_WRAPPERS`, `opens_with_output` and `opens_with_tag`, and the local-path
cut `cut_local`; a module that calls them loads sessions with `interface=7`. Every call of interface 6 behaves as it
did.

`writer(record)` names who a message or tool call's harness says wrote it, from the
record's role, `injected`, `origin_kind` and flags, never its text: `person` (typed at
the prompt), `model` (the model's own turn or call), `peer` and `coordinator` (mail
another agent's session sent, named only by the line's own `origin` object), `unknown`
(a user message whose submitter the harness doesn't say), or the kind of a message
something else wrote: `channel`, `hook`, `task`, `harness` or `summary`. `OPENING` is the
characters every test of what a message opens with skips first (U+0000 to U+0020,
U+007F to U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F, U+3000 and
every Cf character), and `opening(text)` drops them from a text's front. Claude Code's
and Codex's wrapper tests skip exactly that set, so a wrapper after a zero-width space or
a no-break space still marks a message injected.

`cut_local(text, env=None)` makes each loopback origin in a text `local` and cuts each
local path under a root (the home folder `env` names, `~/`, `$HOME/` and the system roots
above) to its last segment; a redaction placeholder (`[REDACTED:<rule>]`) inside a path
counts as part of it. It changes text, so a caller that redacts runs it as
`chat_redact`'s `between`, never before or after the redaction.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-sessions-v0.2.10/packages/sessions/LICENSE).
