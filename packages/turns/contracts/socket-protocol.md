# turns' socket protocol, version 2

This is the contract between turns' endpoints and every caller. `moxi_turns` speaks it for
its callers; anything that speaks it the same way is a caller too. The legs inside an
endpoint (the Claude runner's JSON lines and its HTTP socket, a host's own connections to
its harness) are internal to the package and are not part of this contract.

Version 2 replaces version 1 whole. The two never meet on one connection: a version 2
endpoint serves only at version 2 addresses, and version 1's files and addresses are left
as version 1 left them (see "Versions").

## Finding an endpoint

Each session turns can reach has one endpoint: one turns process, keyed by its launch.

- The endpoint's name is `<harness>-<nonce>`, where `harness` is `claude`, `codex` or
  `opencode` and the nonce is minted when the endpoint starts. So the key is the launch,
  not the session id: a `/clear` keeps the endpoint, and its files follow the new id.
- Its caller address is `moxi.ipc.address("turns", "<harness>-<nonce>")`. On Linux and
  macOS that is the Unix socket `moxi.paths.socket_path` gives, `RUNTIME/turns/<name>.sock`
  (or a short hashed path under `/tmp/moxi-<uid>/` when that path is too long), with
  `moxi.ipc`'s lock file beside it. On Windows it is a named pipe. `RUNTIME` is Moxi's
  runtime root for turns (`moxi.paths.Layout("turns").runtime_dir()`, which
  `MOXI_TURNS_RUNTIME_DIR`, `MOXI_TURNS_HOME` or `MOXI_HOME` move).
- The endpoint keeps a directory, 0700, under version 2's own root:
  `RUNTIME/turns/v2/<harness>-<nonce>/`. It holds `hello.json` and the files of the
  endpoint's internal legs, which no caller uses.
- `hello.json` holds `address` (the caller address), `protocol: 2`, and every field of the
  `session` method's answer. It is written after the endpoint listens, and rewritten,
  atomically, when the session id changes.
- A directory with no readable `hello.json` is an endpoint starting, and is left alone.

**By session id.** Read every `RUNTIME/turns/v2/*/hello.json`. An endpoint matches when
its current `session_id` is the id, or else when `earlier` holds it. A match counts only if
a connection to its `address` passes `rpc.hello` and its `session` answer still names the
match.

**By process id.** Walk up from the pid through its parents; the nearest ancestor that is
an endpoint's `pid` matches. Never match on `endpoint_pid` (see `session`).

**Sweeping.** A caller removes a version 2 directory only on `moxi.ipc.sweep(address)`'s
word, never because a connect failed. When a connect to a directory's `address` fails, the
caller calls `sweep(address)`: on `removed` or `absent` it removes the directory's files
and then the directory; on `live` it leaves it. On Windows `sweep` only reports, `live` or
`absent`, since a pipe goes with its server; a caller acts on those two the same way.

**Version 1 directories.** A directory directly under `RUNTIME/turns/` with a readable
`hello.json` is a version 1 endpoint whose socket is `<dir>/sock`. A version 2 caller
matches it as it matches a version 2 one, by session id or pid. When the best match is a
version 1 directory, the caller sends version 1's own hello line,
`{"v": 1, "op": "hello", "id": 1}`, to `<dir>/sock`. If a version 1 answer comes back and
still names the match, the session's endpoint speaks version 1, and the caller reports
that (the library raises `VersionError`). If nothing answers, the match is skipped. A
version 2 caller never removes, renames or writes anything under a version 1 directory.

Any process running as the user can reach any endpoint; turns adds nothing to that trust
model.

## Framing

The wire is `moxi.ipc`'s: JSON-RPC 2.0, one message per line, UTF-8, each line ending in a
newline. A line is at most 4 MiB. Batches are refused.

- A line that is not JSON is answered -32700 and the connection stays open. A line over
  the cap is answered -32600 and the connection closes.
- The endpoint reads one message at a time per connection and answers each before it reads
  the next, so one connection's requests are served in the order sent. A method that can
  block (`wait` with a bound, `command`, `watch`, `stops.handle`) therefore wants a
  connection of its own: on a shared one it holds every later request behind it.
- The endpoint closes a connection only on an over-long line, a full write queue or a missed
  write deadline (a reader that stops reading), on a first request other than `rpc.hello`,
  after `session.gone`, or when it ends. It reads each
  request whole before it acts on it, so once a connection closes, a request sent on it was
  either acted on or never will be.

## Hello, versions and capabilities

A connection's first request is `rpc.hello`, which `moxi.ipc` answers for the endpoint.

- Params: `{"versions": [lo, hi], "capabilities": [...]}`. `moxi_turns` sends `[2, 2]`. The
  endpoint reads no client capability in version 2.
- Result: `{"version": 2, "capabilities": [...]}`, the session's capabilities below.
- No version in both ranges: error -32001, whose `data` is
  `{"client": [lo, hi], "server": [lo, hi]}`.
- A first request other than `rpc.hello`: error -32002, and the connection closes.

The session's capabilities:

| capability | Claude Code | Codex | OpenCode | what it offers |
|---|---|---|---|---|
| `deliver` | yes | yes | yes | `deliver` |
| `only-if-idle` | yes | yes | yes | `deliver` with `only_if_idle` |
| `steer` | yes | yes | no | `steer` |
| `stop-handler` | yes | yes | no | `stops.handle` |
| `clear` | yes | no | no | `command` `clear` |
| `exit` | yes | no | no | `command` `exit` |
| `compact` | yes | yes | yes | `command` `compact` |
| `agents` | yes | yes | yes | `agents` |
| `stop-shells` | yes | yes | no | `command` `clear` or `compact` with `stop_shells: true` |

`session`, `facts`, `watch` and `wait` are always there and carry no capability. A method or
option the session lacks is answered with the `capability` error, never a refusal. OpenCode
support is experimental.

## Errors

**The rule.** A JSON-RPC error means the request could not be acted on as asked against this
endpoint: it was malformed, named something the session does not offer, came to an endpoint
that is ending, or asked for the stop handler another connection holds. Sent again unchanged
it gets the same error. Every reason that the session's state decides is a result value
instead (`refused`, `lost`, `failed`), because the same reasons also arrive later through
`wait`, and because `only_if_idle`, `if_turn_number` and the refusal flags ask a
conditional question: "no, a turn is open" is its answer, not a failure of the request.

| code | name | when |
|---|---|---|
| -32700 | parse error | a line that is not JSON |
| -32600 | invalid request | not a JSON-RPC request, a batch, or a line over the cap |
| -32601 | method not found | a method turns does not have |
| -32602 | invalid params | params that break this contract: an empty or missing `text`; a `ref` that is not a string, or longer than 256 bytes as UTF-8; `if_turn_number`, `refuse_draft` or `refuse_agents` without `only_if_idle: true`, or of the wrong type; a bad `until` or `timeout_ms`; a `wait` with both or neither of `delivery` and `ref`; an unknown command name; `stop_shells` not a boolean, or given with `exit` |
| -32001 | hello refused | no version in both ranges; `data` names both |
| -32002 | hello required | a first request other than `rpc.hello`; the connection closes |
| 1001 | `gone` | the endpoint is ending or has ended |
| 1002 | `capability` | the session does not offer the method or option; `data` is `{"capability": <name>}`, for example `stop-shells` for `stop_shells: true` on a session without it |
| 1003 | `stop-handler-taken` | `stops.handle` while another connection handles stops |

**When `gone` comes.** `gone` is only for an endpoint that is ending, and an endpoint becomes
ending only through its own end conditions, never through a command's result. Until then
every method answers, `facts` included (its live read may come back `stale` or
unreadable). On Claude Code the end conditions are the end of the endpoint's process
(Claude Code ends it, as when its module unloads), the loss of its leg to the runner, and a
change or death of its parent; never a session-end hook. An `exit` that passes settles
`passed` and changes nothing else, so when Claude Code answers `/exit` with its exit dialog
instead of exiting, the endpoint keeps answering until the harness process exits.

## Values

Every result is an object with a `"kind"`. A value inside another value (`{id, ref}`, a
turn, an agent, a shell) is a plain object with no `kind`.

| kind | fields |
|---|---|
| `facts` | `session_id`, `turn_number`, `turn_open`, `turn`, `agents`, `draft`, `at`, `stale`, `ready` |
| `agents` | `entries`, `complete`, `at` |
| `started` | `delivery`, `ref`, `turn_id` |
| `refused` | `delivery`, `ref`, `reason` (a delivery's); `reason` alone (a command's) |
| `lost` | `delivery`, `ref`, `reason` |
| `ended` | `delivery`, `ref`, `turn_id`, `outcome` |
| `ran` | `new_session_id`, `shells` |
| `failed` | `reason` |
| `passed` | none: an `exit` the harness's gate let through (see `command`) |
| `stop` | `turn_id`, `session_id`, `stop_hook_active`, `task_types`, `crons` (each null when the harness did not give it) |

Every value of a delivery (`started`, `refused`, `lost`, `ended`) names the `delivery` id
and echoes the `ref` the caller gave it, null when it gave none. turns never acts on a
`ref`; `wait` only looks a delivery up by it.

**Facts.**

- `turn_number` is the current session's count of main-loop turns started; a new session id
  starts it again at 0.
- `turn_open` is true while a main-loop turn runs.
- `turn` is the open turn as its `turn.started` named it,
  `{turn_id, opening, command, started_at}`, or null. `opening` is the opening delivery's
  `{id, ref}`, or null when no delivery opened the turn: the person's own turn when
  `command` is also null, a turns command's turn (a Codex `compact`) when it is not.
  `started_at` is the wall-clock time (Unix seconds, a float like `at`) the turn began.
  `turn` is set in the same step that sends `turn.started` and cleared in the step that
  sends `turn.ended`. So a watch sees an open turn in its first facts or in a
  `turn.started`, never both and never neither. A turn the endpoint holds for its tie to a
  delivery (`turn_open` true, its `turn.started` not yet sent) reads `turn` null until it is
  tied, and once tied its `started_at` is still the time it began. When the endpoint closes
  a turn whose end it missed (as `error`) and opens the next in one step, it sends the old
  turn's `turn.ended` and the new one's `turn.started` in that step, so no reader sees the
  old turn after its end, or a gap between the two.
- `agents` counts the entries of `agents` whose `final` is not true and whose `state` is
  `pending`, `running` or `waiting`, and is null when the list could not be read whole
  (unknown, which counts as busy: a `deliver` with `refuse_agents` and a command refuse
  `busy-agents`).
- `draft` is true when the person has typed into the prompt box, and true when the box
  could not be read; false where the harness cannot tell.
- `at` is the wall-clock time of the read, in Unix seconds.
- `stale` is true when the turn ledger moved while the live facts were read, so the reading
  is not one moment.
- `ready` is the last `session.ready` the endpoint raised, `{source, session_id, at}`, or
  null before the first. A caller that found the endpoint after its ready reads it here
  instead of racing the watch.

The live part of the facts (`draft`, `agents`) is read from the harness within a bound: 1 s
on Claude Code, 2 s on Codex and OpenCode. Past the bound it reads unreadable: `draft` true
and `agents` null. That refuses and never passes.

**Agents.** `entries` is a list of `{id, kind, state, final, description, parent_id}`:

- `kind` is `subagent`, `teammate` or `shell` (a Codex background terminal).
- `state` is `pending`, `running`, `waiting`, `idle`, `done`, `failed`, `stopped` or
  `unknown`.
- `final` is true when the entry's latest notice is a final one (done, failed or stopped),
  false when not, and null when the harness gives no notice to read. An entry reads `final`
  true as soon as the harness records its final notice, whether or not a main-loop turn is
  open: `agents` reads the harness's own record on every call.
- `parent_id` is the agent that spawned it, or null.
- `complete` is false when the list could not be read whole; then `facts.agents` is null.

Per harness: on Claude Code the entries are what Claude Code lists as its agents (`completed`
reads `done`, `killed` reads `stopped`), and a workflow's agents are not listed. On Codex
they are the collaborating agents the host has seen (`pendingInit` reads `pending`,
`errored` reads `failed`, `interrupted` and `shutdown` read `stopped`, `notFound` reads
`unknown`), and each background terminal as a `shell` entry, `running`, `final` false. A
collaborating agent named before Codex gives its status reads `pending` with `final` null.
The Codex list is not complete while the host is not connected, when a resumed load could
not read the thread's history, or when the terminals could not be listed. On OpenCode they
are the session's child sessions, `running` when busy or retrying, else `idle`, with
`final` null: OpenCode sends no final notice. On every harness a status turns does not
know reads `unknown`, with `final` null.

**Ran.** `new_session_id` is the new session's id after `clear`, and null after `compact`
and `exit`. `shells` is null unless the command was given `stop_shells`, and then
`{stopped, left, complete}`:

- `stopped` lists the ids of the shells stopped.
- `left` lists each shell still running as `{id, why}`, where `why` is `timeout`, `denied`,
  `unsupported`, `unlisted` or the harness's own words.
- `complete` says whether turns could vouch for the list it stopped from. A list turns
  cannot vouch for is reported, never read as empty: when `complete` is false, `left` also
  holds one `{id: null, why: "unlisted"}`, standing for the shells turns could not name. So
  `{stopped: [], left: [], complete: true}` means nothing was running, and only that.

A shell left over does not make the command a failure: the command ran.

**Outcomes and sources.**

- `outcome` is one of `answer`, `aborted` (Esc), `error`, `refusal`.
- `source` is one of `startup`, `resume`, `clear`, `compact`, or null when the harness did
  not say.

**Refusals.** A delivery's refusal `reason` is one of:

- `busy-turn`: a turn is open, or a delivery is in flight or held (`only_if_idle`).
- `stale-turn`: the turn number moved from `if_turn_number`.
- `draft`: there is a draft in the prompt box (`refuse_draft`); an unreadable box counts.
- `busy-agents`: a subagent is live (`refuse_agents`); an unreadable list counts.
- `idle`: a steer with no turn running to take it.
- `loading`: a Codex host has no accepted load.
- `dropped`: another plugin's hook dropped the prompt.
- `unsupported`: the harness turned it down after the endpoint offered it (the Claude Code
  gate is not loaded).

A command's refusal `reason` is one of `busy-turn`, `stale-turn`, `draft`, `busy-agents`,
`not-allowed` (no consent), `in-flight` (one is already running), `rate` (one was accepted
in the last 60 s), `loading` (`compact` on a Codex host with no accepted load) and
`unsupported` (the harness has no way at run time; with `stop_shells`, the harness turned
the stop down before the command ran).

`gone` is not a refusal on the wire: a delivery whose endpoint ends reads `lost`, and a
request to an ending endpoint gets the `gone` error.

**Lost.**

- A `lost` `reason` that starts with `untied` or `expired` was decided by an endpoint that
  lives on: the delivery's submit closed with no turn tied to it (on Claude Code, a resolve
  with no turn in its window, a rejected submit, or the runner's bound; on Codex, a request
  Codex was sent that the host could not tie to a turn), or the harness neither started nor
  refused it within 300 s. The first settles one delivery.
- A `reason` that starts with `unknown delivery` means the endpoint holds no record of the
  delivery: an id it did not mint or settled long ago, or a `ref` no delivery of it
  carries. Nothing recorded the text.
- Any other `lost` means the endpoint went away (a reload, a crash, the session ended): a
  caller looks for the endpoint again.
- `moxi_turns` adds one `lost` reason, `unanswered`, for a delivery whose outcome it could
  not learn (see "Unanswered calls"). No endpoint sends it.

**Failed.** A `failed` `reason` that starts with `expired` is the endpoint's own bound: a
command handed to the harness that it does not settle within 300 s. `timeout` means the
call's own wait (`timeout_ms`) passed first, and the command may still run. `moxi_turns`
adds `unanswered` for a command it wrote and got no answer to, and `not-exited` (see
`command`).

### Per-harness notes

- **Claude Code.** A delivery whose submit failed before the endpoint admitted its prompt
  never runs: the prompt is dropped where it would enter. So a caller that hands the text
  again after such a `lost` delivers it once.
- **Codex host.** A turn ends only on Codex's own word: its `turn/completed` (whose status
  gives the outcome), a change of the thread to a non-active status with no
  `turn/completed` for the open turn within a short grace (the turn ends `error`), a load
  whose answer shows the turn gone, or a dead app-server. The Stop and Interrupt hooks
  close nothing: between Stop and `turn/completed` the turn is still open. Once a Stop is
  answered without a block the turn is ending: Codex may complete it at any moment and can
  leave a text it takes then unanswered, so it takes no more steers. A `deliver` waits and
  starts a new turn after `turn/completed`, and a `steer` is refused `idle`, as with no turn
  running. A delivery reads `refused` only when Codex did not take its text (the request
  was never sent, or Codex answered it with an error). A `turn/start` Codex was sent but
  that the host cannot tie to a turn (no answer came, or Codex took the text into a running
  turn the host had already reported or could not name) reads `lost` with a reason
  starting `untied`. A `turn/steer` that gets no answer within the host's 30 s request
  bound reads `lost`.
- **OpenCode host** (experimental). A delivery's turn is the one whose reply names the
  host's posted message as its parent. A turn that goes idle with no such reply is the
  person's, and the delivery reads `lost` with a reason starting `untied`.

## Methods

Params are an object; a missing optional param takes its default. In short:

| method | params | result |
|---|---|---|
| `session` | `{}` | `<session>` |
| `facts` | `{}` | `<facts>` |
| `agents` | `{}` | `<agents>` |
| `deliver` | `{text, ref?, only_if_idle?, if_turn_number?, refuse_draft?, refuse_agents?}` | `{delivery, ref, result?}` |
| `steer` | `{text, ref?}` | `{delivery, ref, result?}` |
| `wait` | `delivery` or `ref`, `until`, `timeout_ms` | `{result}` or `{pending: true}`, with `delivery` when asked by `ref` |
| `command` | `{name, if_turn_number?, timeout_ms?, stop_shells?}` | `{result}` |
| `watch` | `{}` | `{facts}`, then notifications |
| `stops.handle` | `{}` | `{}`, then `stop.asked` notifications |
| `stops.answer` | `{ask, reason}` | `{taken}` |

`text` is a non-empty string. `ref` is a string of at most 256 bytes as UTF-8, or absent
or null.

**No answer waits on the harness.** `session`, `deliver`, `steer` and a `wait` with
`timeout_ms` 0 answer from the endpoint's own record, at once. `facts` and `agents` answer
within the harness's live bound (above) whatever the harness is doing. Only `wait` with a
bound, `command` and the long-lived `watch` and `stops.handle` wait, each for its own
reason.

### `session`

Result: `{harness, pid, endpoint_pid, session_id, earlier, cwd, nonce, started,
endpoint_version, mod_version, trouble?}`.

- `harness` is `claude`, `codex` or `opencode`.
- `pid` is the harness process, the root of the session's process tree.
- `endpoint_pid` is the process that serves the endpoint, fixed for its life. A caller that
  names the session's tree uses `pid`; a caller that names the endpoint itself uses
  `endpoint_pid`. On Claude Code `pid` is Claude Code and `endpoint_pid` the endpoint
  process it runs as a child. On a Codex or OpenCode host the endpoint runs inside the host,
  so both are the host's pid; under a Codex launch run inside a caller's process, both are
  that process's pid.
- `session_id` is the current session id, null before the harness names one; `earlier`
  lists the earlier session ids of the same launch.
- `cwd` is the session's working directory; `nonce` the endpoint's; `started` the
  wall-clock time it started.
- `endpoint_version` is turns' package version; `mod_version` the version of the endpoint's
  harness-side code where there is one, else null.
- `trouble` is absent when the endpoint is healthy, and otherwise gives the host's current
  load warning or failure.

`moxi_turns` calls `session` right after `rpc.hello` on every connection it opens.

### `facts`

Result: `<facts>`. The endpoint reads its ledger, then the live facts, then the ledger
again; `stale` says whether the ledger moved between.

### `agents`

Result: `<agents>`, read from the harness's own record on each call. Needs `agents`.

### `deliver`

Params: `text`; `ref`; `only_if_idle` (boolean, default false); `if_turn_number` (integer
or null), `refuse_draft` and `refuse_agents` (booleans), all three valid only beside
`only_if_idle: true`.

Without `only_if_idle`: a new turn now if the session is idle, else when the running turn
ends. Deliveries are not coalesced: two are two turns, in the order handed. On a Codex host
with no accepted load, the delivery waits until a load is accepted.

With `only_if_idle: true`: a new turn, only if no turn is open. With `if_turn_number`, only
if the turn number still equals it. `refuse_draft` refuses on a draft in the prompt box,
`refuse_agents` while a subagent is live. A delivery already handed to the harness or held
for the running turn counts as an open turn. A Codex host with no accepted load refuses
`loading`. Needs `only-if-idle`.

Result: `{delivery, ref, result?}`. `delivery` is the delivery's id and `ref` echoes the
caller's. The endpoint answers once the delivery is recorded and the checks it decides from
its own record have passed; it never waits on the harness. `result` is present only when
one of those checks refused the delivery: `busy-turn`, `stale-turn`, `loading`, or
`unsupported`. A check that needs the harness (`draft`, `busy-agents`) and every refusal
the harness gives come as the delivery's settled value, through `wait`. Where the delivery
takes effect every check runs again against the session as it is then (on Claude Code as
the prompt enters, on an OpenCode host as the host reads the session's status just before
it posts, on a Codex host before it sends `turn/start`), so a turn that began after the
answer refuses the delivery; that refusal settles it `refused`.

### `steer`

Params: `text`, `ref`. Needs `steer`.

The text into the running turn. The endpoint records the delivery and answers as
`deliver` does: `{delivery, ref, result?}`, with `result` present only when a check the
endpoint decides from its own record refused the steer: `idle` when no turn runs, `loading`
when a Codex host has no accepted load, or `unsupported`. The harness's take comes later:
the delivery's `started` value and the `turn.steered` notification arrive when the harness
confirms it took the text, and its `ended` is that turn's end. A steer is either taken into
the turn that is running or settles `refused` `idle`; it never becomes a turn of its own.
So a caller that falls back to `deliver` on `idle` runs the text once whichever way the race
went. Steers reach the harness in the order the endpoint recorded them. A steer the harness
neither confirms nor refuses within 300 s settles `lost`, with a reason starting `expired`.

A caller that wants a steer where the session can take one, and a turn of its own where it
cannot, calls `steer` and, on a `refused` `idle` or `loading` it received (in the answer's
`result` or from `wait`), calls `deliver` with the same text. It falls back only on a
refusal it received; on an unknown outcome it waits for the steer's value.

### `wait`

Params: exactly one of `delivery` (the id) and `ref`; `until`, `started` or `ended`;
`timeout_ms`, a number of at least 0, or null to wait as long as the endpoint lives.

Waits for the delivery's value. Until `started`: `started`, `refused` or `lost`. Until
`ended`: `ended`, `refused` (the delivery never went in) or `lost`. A delivery id this
endpoint did not mint, or one settled long ago, answers `lost` with a reason starting
`unknown delivery`. A delivery handed to the harness that the harness neither starts nor
refuses within 300 s settles `lost`, with a reason starting `expired`, and the next held
delivery goes in.

Result: `{result: <value>}`, or `{pending: true}` when `timeout_ms` passed first.

With `ref`, the wait is for the endpoint's newest delivery carrying that `ref`, in flight or
among the settled ones it keeps, and the answer, `{result}` or `{pending: true}` alike, also
carries that delivery's id as `delivery`, so the caller can go on waiting by id. With no
delivery carrying it, the answer is `{delivery: null, result: <lost>}`, with a reason
starting `unknown delivery`.

### `command`

Params: `name`, `clear`, `exit` or `compact`, each needing the capability of the same name;
`if_turn_number` (integer or null); `timeout_ms` (a number of at least 0; absent or null
waits 30000 for `clear` and `exit` and 330000 for `compact`, plus 10000 with
`stop_shells`); `stop_shells` (boolean, default false, valid with `clear` and `compact`
only; true needs `stop-shells`).

Runs `/clear`, `/exit` or a compaction. Only with consent: the user's turns config
`commands.allow`, or the launch's `MOXI_TURNS_COMMANDS` (a comma-separated list) in the
session's environment. Only one in flight, one accepted per 60 s across all three, and
only from an idle snapshot: no open turn, no live subagent, no draft. With
`if_turn_number`, only if the turn number still equals it. The checks run again where the
command takes effect.

Result: `{result: <ran | refused | failed | passed>}`. There are two bounds. The endpoint
settles a command handed to the harness `failed`, with a reason starting `expired`, 300 s
after it was handed, which frees the slot for the next one (the 60 s rate still applies).
`timeout_ms` bounds only how long this call waits: past it the answer is `failed`
`timeout`, and the command may still run.

- **`clear`** reads `ran` with the new session's id.
- **`compact`** reads `ran` with `new_session_id` null. Compaction is confirmed by the
  session, not by the call's return: the endpoint settles a handed `compact` `ran` at the
  next `session.ready` with source `compact`, and `failed` when the harness answers an
  error or no such ready comes within its 300 s bound. A compaction often takes longer than
  30 s, so a caller's wait should outlast that bound. A harness or version where the call
  is missing settles `refused` `unsupported`. On Codex a compaction may run as a turn of its
  own: its notifications carry `command: "compact"`, and it counts in `turn_number` like
  any turn. On OpenCode the host compacts with the model of the session's last answer, or
  the launch's model option; with neither it settles `failed`.
- **`exit`** cannot be confirmed by the endpoint, which the harness ends before or as it
  exits. When the harness's gate lets an `exit` through, the endpoint answers `passed` at
  once (or at its own end, if that comes first). The caller then watches the harness
  process that `session` names as `pid` (its identity taken before the request) and reads
  the exit as run once that process has ended, or as failed (`not-exited`) when it still
  runs 5 s after `passed`. `moxi_turns` does this and answers `Ran` or
  `Failed("not-exited")`; a caller never sees `passed` from the library.

**`stop_shells`.** With `stop_shells: true`, `clear` or `compact` also stops the session's
background shells: on Claude Code its Bash-tool background shells and Monitors, on Codex
its background terminals. Subagents, teammates and workflows are not stopped. The stop and
the command are one checked step under the command's consent: they pass one set of checks,
count once toward the 60 s rate, and take the one command slot. The session's background
shells do not count in the agent check, at the snapshot or where the command takes effect.
Then:

1. The harness is asked whether the shells may be stopped, with nothing stopped yet. If it
   turns the stop down, the command settles `refused` `unsupported` and does not run.
2. The command runs. A refusal or failure settles as without `stop_shells`, and nothing is
   stopped.
3. Once the command has taken effect, the endpoint stops the shells it can list through the
   harness's own call, never by signal, within 10 s. A shell not stopped by then is `left`
   `timeout`.
4. The command settles `ran` with `shells` (see "Ran") only once the stop has reported, so
   `ran` always carries `shells`. The 300 s bound covers steps 1 and 2 and stops counting
   once the command has taken effect, so a stop that runs past it is not read `expired`.

The stop comes after the command, not before it, because a stopped shell can queue a task
notice that runs a turn: after a `clear` or a compaction that turn opens in the new, small
conversation, reads like the person's own turn (`turn.started` with no deliveries and
`command` null), and does not delay `ran`. A `deliver` with `only_if_idle` made right after
`ran` can be refused `busy-turn` by it.

### `watch`

Result: `{facts: <facts>}`, then the notifications below on this connection until the
caller hangs up or `session.gone` comes. The facts are read under the same lock that orders
the notifications, so nothing falls between the snapshot and the first notification. A
caller hangs up to stop watching.

### `stops.handle` and `stops.answer`

`stops.handle {}` makes this connection the session's one stop handler. Result `{}`, or the
`stop-handler-taken` error when another connection handles stops. Needs `stop-handler`.

While the connection lives, each Stop of the main loop sends the notification
`stop.asked {ask, facts: <stop>}` on it. The handler answers with the request
`stops.answer {ask, reason}`, where `reason` is a string or null: a string holds the turn
open with that text, null lets it end. Result `{taken: bool}`. `taken` is false for a late
answer, past `stop_handler.wait_ms` (turns config, default 2000), which let the turn end,
or for an unknown `ask`. Closing the connection gives the handler up.

## Notifications

On a `watch` connection, in the order the endpoint's ledger saw them:

| notification | params |
|---|---|
| `session.ready` | `source`, `session_id` |
| `turn.started` | `turn_id`, `session_id`, `turn_number`, `deliveries`, `command` |
| `turn.steered` | `turn_id`, `session_id`, `delivery: {id, ref}` |
| `turn.ended` | `turn_id`, `session_id`, `turn_number`, `outcome`, `deliveries`, `command` |
| `session.gone` | `reason`; the connection closes after it |

- `session.ready` comes at a start, resume, clear or compaction.
- `turn.started` and `turn.ended` come for every main-loop turn, the person's and the
  deliveries' alike.
- `deliveries` is a list of `{id, ref, how}`, `how` being `opened` (the delivery that
  started the turn) or `steered`. On `turn.started` it holds the opening delivery, or is
  empty for a turn no delivery opened. On `turn.ended` it holds every delivery the turn
  took: the opening one first, then each steered one in the order taken. So a caller needs
  no table of its own: a turn's notifications name each delivery and its `ref`.
- `turn.steered` comes when the harness confirms it took a steered delivery into the
  running turn.
- `command` is `compact` for a turn the harness ran to compact on a turns command (Codex),
  else null.
- A connection that ends without `session.gone` means the endpoint went away.

On a `stops.handle` connection the one notification is `stop.asked`.

## Unanswered calls

How `moxi_turns` reads a call that got no answer. Its callers rely on it, and any other
caller should read the same way.

**The rule.** A refusal is reported only when one was received, or when the request was
never written. A request written whose answer did not come has an unknown outcome, never a
refusal. Every connection passes `rpc.hello` and `session` before a method's line is
written on it, so the library can tell where a failure fell:

- **Never written.** Nothing listens at the address, or the connection failed during
  `rpc.hello` or `session`. Nothing can have acted on the request. `deliver`, `steer` and
  `command` read `refused` `gone`; `session`, `facts` and `agents` raise an error. `wait`
  reads `lost` `unanswered` only when nothing listens at the address. Any other failure
  before a wait's line went out (a stalled `rpc.hello` or `session` on an endpoint that
  still listens) raises a timeout: the delivery the wait names was recorded by an earlier
  call, so a connection that fails short of that says nothing about it. What ends such
  waits is the caller's own deadline, or the endpoint going, which leaves nothing
  listening.
- **Refused.** The `gone` error reads `refused` `gone` on `deliver`, `steer` and `command`,
  `lost` on `wait`, and an error on `session`, `facts` and `agents`. A value in an answer is
  returned as given.
- **Written, no answer** (the read bound passed, the connection closed, or a write failed
  part way):
  - `deliver` and `steer` give a delivery with no id, the caller's `ref` and no value. The
    library resolves it with `wait {ref, until, timeout_ms: 0}` on the connection the
    request went on: one connection's requests are served in order, so that answer comes
    after the endpoint has handled the unanswered request and names its id. If that
    connection has closed, the library connects again and resolves by `ref` there: the
    endpoint records a `deliver` or `steer` before it reads the connection's next line, so
    once a connection closes the delivery was recorded or never will be. The value is
    `lost` `unanswered` only when nothing listens any more or the call carried no `ref`; a
    resolve that finds no delivery with that `ref` returns the endpoint's `lost`
    `unknown delivery` as given.
  - `command` reads `failed` `unanswered`: the command may still run.
  - `wait` raises a timeout, as when the endpoint answers `pending`, and a later wait asks
    again; when its connection closed, the library connects again and waits by id. It reads
    `lost` `unanswered` only when nothing listens any more.
  - `session`, `facts` and `agents` raise an error; they change nothing, so a caller can
    ask again.
  - `stops.answer`: the outcome is logged as unknown, and a missing answer is never read as
    `taken: false`.

A missed read leaves its connection open: the late answer is dropped, and the resolve above
needs that connection.

## Reserved names

These are reserved for later additions, each one a hello can carry without a version bump.
An endpoint of this version offers none of them:

- session capabilities `guards`, `after-tool`, `usage` and `mcp-token`, and the client
  capability `guards`;
- the method spaces `guard.*`, `tool.*`, `usage.*` and `mcp.*`, and the notifications
  `tool.*`.

## Versions

This is version 2. A client offers the range it speaks in `rpc.hello`; an endpoint with no
version in that range refuses it with -32001, naming both ranges. A change to any message's
shape raises the version. Offering a reserved capability or method (see "Reserved names")
does not: the hello carries it.

A version 2 endpoint serves only at version 2 addresses and keeps its files under
`RUNTIME/turns/v2/`. A version 1 library reads only the directories directly under
`RUNTIME/turns/`, so it never finds, probes or sweeps a version 2 endpoint; for a session
whose endpoint is now version 2 it finds nothing. A version 2 library reads a version 1
directory only as version 1, reports a live one as speaking version 1 (the library's
`VersionError`, whose message names both versions and says to reload the session's plugins
or restart it), and never removes anything in it. No version 2 endpoint answers a version 1
line.
