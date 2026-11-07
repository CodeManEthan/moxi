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
`<environment_context>`) marks the message true. A message's text never makes it false.
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
way), and it adds two commands:

- `moxi sessions find SESSION [--harness claude|codex|opencode] [--root PATH] [--json]`
  prints where a session's records are, or every root it looked in and why each held none,
  and exits 1 then. `--root` adds a root to look in after the usual one (for OpenCode, a
  database).
- `moxi sessions root [--json]` prints the root each harness is read from and where that
  root came from: a config key, an environment variable, or the harness's default.

With `--json`, each command prints one JSON object that starts with the shape version.

Three settings name a root when it is not the usual one: `claude.projects`,
`codex.sessions` and `opencode.database`. `moxi config sessions` shows them.

## The page

With the `ui` extra (`moxi-ui`, Moxi's browser UI shell), sessions has a page in the shell
that shows one session: its directory, title and last activity, then its last five turns,
oldest first. Each message shows as text, cut at 2,000 characters with a way to show the
rest; each tool call as one line naming the tool and what it was called on; each tool
result as its size and whether it failed; compactions, aborted turns and rollbacks as
labelled rules; and each request's model and token counts. "Earlier" shows the turns
before. The page lists the session's subagents, and opening one shows its turns the same
way.

The page lists no sessions. It opens a session by harness and id: at the page's own
address it asks for both, and a session's address is `<harness>/<session id>`, or
`claude/<session id>/<agent id>` for a Claude Code subagent. An OpenCode child session and
a Codex subagent thread each open at their own id. The page server runs in the shell's
environment, so the roots it reads are the ones that environment and sessions' settings
name, which can differ from your terminal's. A session that can't be found or read shows
why.

Other modules' pages can place the same view with the shell's `place()`, as the `window`
block, with the attributes `harness` (`claude`, `codex` or `opencode`), `session` and, for
a Claude Code subagent, `agent`. It links to the page at the session's address. A
subagent chosen in the block opens in the block, with Back to return.

The page only reads. It sends no tool call's input and no tool's output, and sends at most
64 KiB of any one message, saying when it cut one. It reads a session when it is shown and
when you ask it to, and runs nothing in the background. Without the extra, nothing of
sessions changes.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install moxi
moxi install sessions
```

Before the modules are on PyPI, install the core from git and give `moxi install` the
module's git source:

```sh
uv tool install git+https://github.com/CodeManEthan/moxi#subdirectory=packages/moxi
moxi install sessions --from 'moxi-sessions @ git+https://github.com/CodeManEthan/moxi#subdirectory=packages/sessions'
```

The front door's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), covers the `moxi` command and
the other modules.

## Dependencies

`moxi`, Moxi's core, and nothing else. The optional extra `ui` adds `moxi-ui`, the browser
UI shell, which brings loopback, the base sessions' page server runs on.

## License

MIT. See [LICENSE](LICENSE).
