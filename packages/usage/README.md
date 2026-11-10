# moxi-usage

Moxi's usage module answers four questions about the coding-agent sessions on a machine:

- how much of each plan window is used, and when it will run out (limits);
- how full a session's context is, and whether it has passed its mark (context and marks);
- whether a subagent should stop (the worker ceiling);
- what the tokens cost, by day, model, session or project (the token index).

It decodes no harness file of its own: every record it reads comes through Moxi's sessions
module, in sessions' one shape.

## Install

```sh
uv tool install moxi
moxi install usage
```

`moxi install usage` brings sessions with it and asks about usage's optional extras:
`turns` (Claude limits, Codex limits pulled from its app-server, the warn-at mark and the
worker ceiling), `projects` (a session's project in the reports) and `ui` (the page). Add
one later with `moxi install usage --with <extra>`. The core's README, at
[CodeManEthan/moxi](https://github.com/CodeManEthan/moxi/tree/main/packages/moxi), covers
the `moxi` command.

## Requirements

- Linux and Python 3.12 or later, with [uv](https://docs.astral.sh/uv/) to install.
- `moxi`, Moxi's core, 0.1.6 or later, and `moxi-sessions` 0.2.8 or later.
- The live parts need turns and the harness it reaches: Claude limits, the warn-at mark
  and the worker ceiling need Claude Code (or Codex) sessions running under turns, and a
  pulled Codex limits reading needs `codex` on PATH. The token index, the reports, the
  prices and the stored limits need no harness running.
- `moxi usage prices update` fetches LiteLLM's price file from GitHub; nothing else in
  usage fetches from the web unless turns pulls a Codex reading (see Commands).

## Where its files live

usage writes under Moxi's per-module directories, named here by their root: `CONFIG/` is
`~/.config/moxi/`, `DATA/` is `~/.local/share/moxi/` and `STATE/` is
`~/.local/state/moxi/` on Linux (each follows `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and
`XDG_STATE_HOME`), or `$MOXI_HOME/config/`, `$MOXI_HOME/data/` and `$MOXI_HOME/state/`
with `MOXI_HOME` set. So `STATE/usage/index.db` is `~/.local/state/moxi/usage/index.db` by
default. `moxi paths usage` prints the real paths.

## Model facts

Price, cache prices and context window come from one table, which other modules read
through `moxi_usage.api` rather than keeping their own.

The table ships in the package as `data/models.json`. It is made from a dated copy of
LiteLLM's `model_prices_and_context_window.json` by `tools/models_from_litellm.py`, which
keeps every row that prices text generation per token (LiteLLM's `chat`, `responses` and
`completion` modes alike, so a model's facts never depend on which API endpoint LiteLLM
lists it for), stores prices in dollars per million tokens, and keeps the prompt-size and
service tiers LiteLLM gives (`above_200k`, `priority`, `flex`). It adds the rows of
`data/supplement.toml`, facts LiteLLM lacks, each with its source and the date it was
read: fast-mode prices, the `[1m]` long-context window, and each provider's default cache
multipliers. The snapshot's date and its file's sha256 are recorded in the table.

Three layers, a later one winning for a model id:

1. the shipped table;
2. `DATA/usage/models-snapshot.json`, written by `moxi usage prices update`;
3. your own `[[model]]` rows in `CONFIG/usage/config.toml`.

A `[[model]]` row:

```toml
[[model]]
id = "gpt-5.6-sol"
input = 4.0          # dollars per million tokens
output = 20.0
cache_read = 0.4
from = "2026-09-03"  # optional price period, matched on each request's own time
to = "2026-11-22"    # `from` inclusive, `to` exclusive
note = "promotional price"
```

`match = "prefix"` makes a row cover every id that starts with `id` (the prefix must carry
a digit, so it never covers every future model). A row that gives any price or tier
replaces all of the lower layer's prices and tiers for that model, so a cache price never
stays behind at an old rate. Such a row must give `input` and `output` too, or usage
refuses it, since it would leave the model unpriced. `window`, `max_output` and
`provider` replace only themselves.

A model id is looked up in this order, the first match winning:

1. `<synthetic>`, a harness's own stand-in for a request no model made, is free;
2. the id as it is;
3. the id under a provider prefix the table uses (the request's provider, `anthropic/`,
   `openai/`);
4. an id with a bracket suffix such as `[1m]`: the supplement's variant for that suffix
   over the base model's facts, or the base model's facts when the supplement has none;
5. the id with a date suffix (`-20251001`, `-2025-10-01`) taken off;
6. the longest prefix row, from your rows and the supplement's;
7. unknown.

Unknown is never a price of 0: the model's tokens are counted as unpriced, and every
report says so. Every answer carries its provenance: the layer, the step that matched, the
row it matched, and the date of the snapshot that row came from.

When a row gives a model's input price but not a cache price, the provider's default
multiplier fills it in (Anthropic: cache reads 0.1 of input, 5-minute writes 1.25, 1-hour
writes 2; OpenAI: cache writes billed as input), and the provenance names each field
filled that way.

A session's context window is the one the harness reported for the request when it gave
one, else the table's, else unknown.

## Limits

Every limits reading usage sees is kept for `limits.history_days` (8) in
`STATE/usage/readings.db`, so `moxi usage limits` answers with no session running:

- **Codex** readings come from its rollouts (the newest rollout is read on each call,
  and the token index's pass reads the rest) and, with turns installed, from Codex's
  app-server as turns hears it. When the newest Codex reading is older than
  `limits.codex_pull_after_s` seconds (600), `limits` asks Codex for a new one through
  turns; when it can't, it keeps the stored reading and says why, and it waits
  `limits.codex_pull_after_s` again before the next try.
- **Claude** readings come from Claude Code's own session report, which turns hands
  usage after each turn. Without turns there are none, and Claude's rows say so. A
  headless `claude -p` run has no turns endpoint, so it gives no Claude reading.

A window is known by its duration, whatever slot the harness puts it in: `5h`, `7d`, or
its minutes. A per-model weekly cap is its own row (`7d opus`). Each row is `fresh`,
`reset` or `unavailable` with the reason. Claude's row takes the reading with the latest
future reset time (on a tie the highest percent), so an idle session's old number never
passes for fresh; Codex's takes the newest reading. When a window has three or more
readings of its current period in the last `limits.projection_minutes` (60), a straight
line through them says when it reaches 100%, given only when that comes after now and
before the reset. Of what else a reading says, the line prints only a spend limit's
dollars; `--json` keeps all of it.

A row also says when its window opened (`opened` in `--json` and from Python): the reset
time less the window's duration. It is null when either is unknown, when that time is
not earlier than the first reading of the period, and for a window at 0% with one
reading: a window is not said to have opened at a time it had already been read at, nor
from one reading that shows no use.

## The token index

Every request every harness wrote is kept in one SQLite file, `STATE/usage/index.db`, so a
report never reads the harnesses' files itself. A pass brings it up to date: it lists each
lane's sources through sessions and reads only what changed since the last pass, from the
cursor it stored for each source.

The lanes, in the order a copy of the same request is claimed when two files hold it:

- `claude`: the live Claude Code transcripts, their subagent files and workflow agent files;
- `codex`: the Codex rollouts;
- `opencode`: OpenCode's database, one source, its requests filed under one row per
  session;
- `archive:<id>`: each archive root in the config, in config order: a directory holding a
  `projects/` tree, such as a backup of an older machine's Claude Code files.

```toml
[[claude.archive_roots]]
path = "/mnt/backup/old-laptop/.claude"
id = "old-laptop"
```

A pass never deletes rows on a listing that came back empty or unreadable. An unplugged
archive drive, an emptied or remounted projects directory, a directory the pass can't
read, or a rollout Codex compressed after a week idle keeps its rows, marked held with the
reason, and they still count. They clear the next time the source is listed and readable.
An archive copy of a session that still has a live transcript is not read. A Codex rollout
that Codex imported from elsewhere (its turn ids, or the imports file, say so) is skipped,
so it is not counted twice. A rollout whose only mark is the desktop app's originator is
counted.

With Moxi's projects module installed, the pass records each session's project key while
its directory exists, so a build worktree's sessions keep their project after the worktree
is removed. A directory that no longer exists is never looked up.

## Reports

    moxi usage daily|monthly|sessions|models|projects [--since DATE] [--until DATE]
        [--harness claude|codex|opencode] [--project KEY] [--model MODEL]
        [--utc] [--no-pass] [--json]

Each report runs the token index's pass first, so it counts what the harnesses have
written up to now; `--no-pass` reads the index as it is. A request that sits in several
files (an archive copy, a resumed session) is counted once. `daily` and `monthly` group by
day or month and model, cut in local time, or UTC with `--utc`; `sessions` puts a session
and its subagents together; `models` shows where each price came from; `projects` groups
by the project each session was recorded under, or by its directory when none was.
`--since` and `--until` are dates, both included.

    moxi usage terms [--by model|day|month|agent] [--session ID] [common flags]
    moxi usage requests SESSION [--agent ID] [common flags]

`terms` prints every term of each cost: for each price group (a model, provider, price
tier, prompt-size bound and price period), the tokens of fresh input, cache read, the two
cache writes and output, the price each paid and the dollars, which add up to the cost. By
model it is a reconciliation table; by day, month or (with `--session`) a session's agents
it gives cost by token class and by price tier. With `--json` its total carries `saving`,
what caching saved over the report: the cache reads at each price group's own input
price (`reads_at_input`), what those reads did cost (`reads_cost`), what the cache writes
cost over the input price (`write_extra`), and `saved`, the first less the other two,
with how many of the input tokens were read from cache (`read_tokens` of
`input_tokens`). It is an estimate: the same tokens with no cache, at the same table's
prices. `saved` is 0 or less where caching cost more than it saved. `saving` is null when
the report holds no cache read, and when a price group that holds cache reads or writes
is unpriced or lacks a price the sums need. `requests` gives one line per request of a
session's agent (`main` unless `--agent` names another): its tokens by class, cost,
context against the model's window, its turn, and a line where a compaction fell. It
exits 1 when no request of that agent is in range.

Cost is worked out when the report runs, from the model table, so a new price or a
`[[model]]` row applies to past requests too, each priced on its own time when the row has
a price period. Tiers apply as the table gives them: a long prompt's price, a priority or
flex service tier, fast mode (whose cache prices are the provider's multipliers over the
fast input price). Two marks keep a total honest:

- `u`, unpriced: tokens of a model the table cannot price are counted apart and left out
  of the cost, never priced at $0. Add a `[[model]]` row to price them.
- `?`, unknown counts: some request in the row had a token count its harness did not
  report, so the row's sums are at least what they show. Where a provider's rule stands in
  for a missing count (OpenAI bills cache writes as input, and Codex does not report
  them), the rule is named instead.

## Context and the restart point

`moxi usage context SESSION` prints how full a session's context is, from its newest
request: `context 412,330 / 1,000,000 (41%)`. The tokens are the whole prompt; the window
is the one the harness wrote on the request (Codex does), else the model table's, and when
neither is known the line says `window unknown`. `--agent ID` reads one of the session's
agents from its own file, `--harness` names the harness (else Claude Code, Codex and
OpenCode are asked in turn). It reads through sessions alone, so it needs no turns.

The restart point is where carrying on with a session costs more than restarting it. It
is Wilson's economic order quantity: a restart costs one cache write of the settled
context, and each call costs a cache read of a context that keeps growing. `moxi usage
restart-point --measure` measures it from the token index (no transcript is read again):
the last `restart_point.days` (21) of sessions with 5 requests or more, per model and per
population (main sessions; subagents and workflow agents). Main sessions count only when
the harness marked a person at the keyboard: Claude Code's `cli` entrypoint, Codex's `cli`
source or an originator in `restart_point.interactive_originators`; set
`restart_point.interactive_only = false` to take every main session. A population needs
20 files for a row; a model's subagents with no row of their own use its main row, and say
so. A restart's write cost `w` is each model's own, from the model table: its cache-write
price over its input price. A model priced for both 5-minute and 1-hour writes (Claude's)
weighs the two by how much of that population's writes were for an hour; a model with one
write price takes it, and one with none (OpenAI bills writes as input) takes 1.0. So
Codex and OpenCode sessions get rows on their own, never Claude's `w`. A model the table
doesn't know, or gives no input price or one of 0, has no row, and the measure names it
with the reason. The result goes to `STATE/usage/restart-point.json`. `moxi usage
restart-point` prints
the rows and each warn-at mark (1x, 1.5x, 2x) in tokens. A mark the measure or the model
table can't support (no row, no cache-read price) is shown as unavailable with the reason,
never guessed. Turn on `restart_point.measure_weekly` to measure once a week on a timer
(`moxi-usage-measure`, Mondays at 04:30).

## The warn-at mark

With turns installed, usage's after-tool listener tells the model, once, when a context
passes its mark: `context 120,000 passed the 1x restart point (100,808) for <model>`. The
mark is the launch's `MOXI_USAGE_WARN_AT` (`1x`, `1.5x`, `2x`, `off`), else
`marks.default` (`off`). The main session and each of its agents are measured against
their own context; an agent's line also reaches its parent at the parent's next tool
call. A mark fires once per rise past it: after a compaction brings the context below it,
it can fire again. A floor tells turns the context below which the main session's calls
have nothing for usage to do, so they skip the listener. The mark reads Claude Code,
Codex and OpenCode sessions alike, and a Codex or OpenCode subagent's own record; on
OpenCode, turns hears tool calls but has no way to hand the model a line, so there the
notice reaches only turns' log.

A launcher that recycles at a limit stamps `MOXI_USAGE_CROSSING_AT`; each rise of the
main session past that mark calls every handler registered on the entry-point group
`moxi.usage.crossings` with a `Crossing(harness, session_id, mark, tokens, threshold,
model, at)`. Handlers run one after another, each waited on for at most
`marks.handler_wait_ms` (200); a line a handler returns follows the notice. usage itself
does nothing on a crossing. The wait is per handler, and turns bounds the whole listener
call by its own `after_tool.wait_ms` (300): two handlers that each take most of their
200 ms outlast it, and turns then drops the call's answer, the notice with it. A handler
should return at once and start any slow work on its own.

Fired marks are kept in `STATE/usage/marks/`, so a restarted endpoint doesn't say one
twice; a session's file goes when its endpoint ends. `moxi usage marks SESSION` prints a
session's marks, thresholds and states; `moxi usage marks` lists the fired marks on disk.

## The worker ceiling

With turns installed, usage offers turns a guard, `usage.ceiling`, that stops a subagent
whose context has grown too far since its first request. It is off until you turn it on,
with `ceiling.enabled` (the install asks) or by adding `usage.ceiling` to turns' `guards.run`,
and it acts only when `ceiling.warn_tokens` and `ceiling.hard_tokens` are both above 0
(both start at 0):

    moxi config set usage ceiling.enabled true
    moxi config set usage ceiling.warn_tokens 250000
    moxi config set usage ceiling.hard_tokens 300000

Past `warn_tokens` of growth the agent is told once, in one added line
(`ceiling.warning_text`). Past `hard_tokens` the agent is latched: its tools are refused
(`ceiling.deny_text`) until it returns, and this holds after a compaction. A latched agent
may still call the return tools (`ceiling.return_tools`, `StructuredOutput`), write, edit
and read `.md` files under `ceiling.handoff_roots`, run git's status, diff, add and commit
forms in its working directory or under `ceiling.work_roots`, and run the commands whose
words start with an entry of `ceiling.allow_commands`, such as `[["make", "handoff"]]`.
Both root lists start empty, and no command beyond git is allowed until you add one.
Markers live in `STATE/usage/ceiling/<session>/<agent>/`; `moxi usage marks` lists the
agents past the ceiling. The guard covers Claude and Codex subagents; turns runs no guard
on OpenCode.

On Codex the same rules hold for its shell tool, and its file tool, `apply_patch`, is
allowed when the patch only adds or updates `.md` files under `ceiling.handoff_roots`; a
patch that deletes or moves a file, or that doesn't read as a patch, is refused. Codex
has no tool that reads a file on its own, so a latched Codex agent can add a new hand-off
file, but can update one only from what it still holds in its context. A patch's relative
paths are read from the agent's working directory, as Codex applies them.

One gap remains on Codex. Its shell tool can be told to run in another directory
(`workdir`), but Codex doesn't pass that directory to the guard, so the guard judges a
Codex shell call by the agent's working directory alone. A latched Codex agent can
therefore run git's status, diff, add and commit forms in a repository outside its
working directory and `ceiling.work_roots`. It can't push or run any other command that
way. If a later Codex passes the directory, the guard holds it to the same rule.

## Commands

    moxi usage limits [--harness claude|codex] [--no-pull] [--json]
    moxi usage context SESSION [--harness H] [--agent ID] [--json]
    moxi usage restart-point [--measure] [--json]
    moxi usage marks [SESSION] [--harness H] [--json]
    moxi usage prices [MODEL] [--provider P] [--json]
    moxi usage prices update [--dry-run] [--revert] [--json]
    moxi usage index [--rebuild] [--force] [--no-wait] [--check] [--json]

`limits` prints one line per harness: each window's percent used (used, not left), when
it resets, when it fills at the recent rate, and the age of the oldest reading on the line.
`--no-pull` never asks Codex for a new reading.

`prices` with a model prints its facts and where each came from; without one, it prints the
layers in effect. `prices update` is the only thing in usage that fetches from the web, and
only when you run it. (With turns installed, `limits` and the API's `limits()` may also ask
Codex for a new limits reading through its app-server, which asks OpenAI; `--no-pull` stops
that.) `prices update` fetches LiteLLM's file from `prices.url`, converts it, refuses a
result with under half the shipped table's rows, prints which models were added or repriced,
which models LiteLLM no longer lists (against the snapshot it replaces, or the shipped table
when there is none; the table may still price them from a lower layer), any supplement row
it left out because its model is gone from LiteLLM, and which of the models in your token
index it reprices, prices for the first time or leaves unpriced, and writes the snapshot
layer. `--dry-run` prints and writes nothing. `--revert` removes the snapshot layer, so the
shipped table rules again. A fetch that fails exits 75.

`index` runs a pass, prints what it did in one line, then the index's status: each lane's
state, its files, the requests it holds and how many are copies counted once, the rollouts
left out as imported, the rows held and why, each file whose every request another file
holds (named as a duplicate of the file that holds its first request), the last pass and
the index's schema version
(also `api.report("status")`). Only one pass writes at a time: another waits
up to `index.wait_s` (30) and then exits 75; `--no-wait` exits 75 at once.
`--rebuild` reads every source again from its start, in `index.workers` processes (0 picks
one per core, up to 8). A schema change that would drop the rows of an archive lane whose
drive is offline is refused (exit 3) until the drive is back, or until `--rebuild --force`
drops them. `--check` compares the stored claims with the claimed view and exits 1 naming
the keys that differ.

## The page

With the `ui` extra (`moxi-ui`, Moxi's browser UI shell), usage shows a page there: tokens and cost by day or month, by session (each session split
by agent and model), by project and by model with where its price came from, with the
limits meters and a strip about the index above them. The Cost terms view gives each
model's cost split by class and tier, the Reconcile table (every term of each price group,
a subtotal per model of several groups, and the total) and a Rates section with the rates
of the models in use and a search over the whole model table. The daily view can stack
cost by class. A session's view charts its main agent's requests, or any agent's, on one
request axis: tokens or cost by class per request, and the context against the window,
with turn and compaction marks. Two blocks go on the shell's home
view: the limits, one meter per window, and the cost of today and the last 7 days, with
how old the index behind it is, flagged once its last pass is over 10 minutes old. Without
the extra, nothing of usage changes.

The page server's reads, each a `GET` under `/api`:

| route | query | answers |
|---|---|---|
| `limits` | none | each plan window now, from the stored readings, with `opened` |
| `window` | `harness`, `limit_id`, `minutes` | one fresh window: when it opened, the first and last reading of its period with the session the last came from, and the sessions that used it |
| `daily` | `by` (`day`, `month`), filters | tokens and cost by day or month and model |
| `sessions` | `top` (1 to 200), `skip`, filters | sessions, dearest first: the `top` after `skip`, `count` of them all, `more` after those sent, each with its `context` and what names it |
| `session` | `session`, filters | one session by agent and model, with its `context` and what names it (`ref`) |
| `projects` | `by` (`project`, `folder`), filters | tokens and cost by project, or by folder alone, with the account's `home` folder |
| `models` | filters | tokens and cost by model, with where each price came from |
| `terms` | `by` (`model`, `day`, `month`, `agent`), `session`, filters | every term of each cost, and `saving` on the total |
| `requests` | `session`, `agent`, filters | one row per request of a session's agent |
| `prices` | `models`, `providers`, `q` | the model table's rates, for some models or a search; it reads no index |
| `restart-point` | `model` | `{model, tokens, reason}`: the context size past which carrying on with a main session of that model costs more than restarting it, or null with the reason |
| `status` | none | the index: its lanes, held rows and last pass |
| `cost` | none | today and the six days before it, by day and model |

The filters are `since`, `until` (dates, both included), `harness`, `project`, `folder`,
`model` and `utc`. `folder` is a folder's whole path and keeps the sessions run in it,
whatever project they were recorded under; a folder above matches nothing under it.
Every answer but `prices` and `restart-point` carries `today`, the page server's local
date. `POST /api/pass` starts a pass, and `GET /api/events` is the stream, with the
topics `limits` and `index`.

`limits` makes no Codex pull, so it never waits on turns: the stream's poller pulls and
publishes the `limits` topic. `window` and `restart-point` write nothing: `window` opens
the readings store only when it exists, and `restart-point` reads the measure's file and
the model table. A session's `context` is the whole prompt of the newest request of its
main file and the window it ran against, null when that is not known. `window`'s sessions
are given for the harness's main limit when its start is known and there is an index to
read, else null.

Wherever an answer names a session it carries `harness`, `session_id`, `name`, `folder`,
`started` and `known`. The page server asks the sessions module for them once per answer,
for the sessions that answer sends. `name` is the name typed for the session in its
harness; a Codex or an OpenCode session has none, and a title the harness made up is not
one. `known` is true when the session's record was found. It is false when every root of
the harness was listed and none holds it: the record is gone, the session keeps its
figures, `folder` is the `cwd` the index stored and `started` the session's earliest
request in the index. It is null when the session was not looked for (its harness's root
could not be listed whole, the row has no session id, or the sessions module could not be
asked), and then `name` and `started` are null too. The id is there for the page's
addresses and requests; the page shows a session by its name, or by its folder and start.

The page reads the index as it is: no view runs a pass, so none waits on the index's lock.
"Read new sessions" runs one in the page server, which the page also asks for when it opens
and the last pass is over 10 minutes old. With no index yet, or one an older usage wrote,
the page says to run `moxi usage index`, and it builds or migrates nothing itself. Days are
the page server's local days, or UTC when asked. The page only reads and shows; it changes
no setting and runs no command but the pass.

## Stores and the doctor

usage keeps its files in Moxi's roots (see "Where its files live"), each made by its first
write and readable only by you: the token index `STATE/usage/index.db` and the lock a pass holds on it,
`STATE/usage/index.lock`, the limits readings `STATE/usage/readings.db`
(kept 8 days), the restart-point measure `STATE/usage/restart-point.json` and the model
snapshot `DATA/usage/models-snapshot.json`, and the fired warn-at marks
`STATE/usage/marks/`, and the worker ceiling's markers `STATE/usage/ceiling/`.
`moxi paths usage` lists them all.

`moxi doctor usage` checks each store and adds usage's own rows; only `marks` deletes:

- `models.snapshot`: the model table in effect is older than `prices.max_age_days` (120),
  or a snapshot on disk can't be read; the fix is `moxi usage prices update`.
- `models.unpriced`: each model the index saw in the last 30 days with no price or no window.
- `index`: the last pass and the counts; a warning for an offline archive lane, held rows
  (each lane's reasons) or a last pass more than 7 days old.
- `codex.imports`: a warning when the last pass couldn't read Codex's imports file, or
  counted rollouts whose only import mark is the desktop app's originator.
- `restart_point`: a warning naming each model and population that measuring now would
  give a row (20 files or more by the measure's own rules) but the measure in effect has
  none for (so no warn-at mark), or when the measure is over 14 days old; the fix is
  `moxi usage restart-point --measure`. Models and populations seen in the last 30 days
  that the measure gives no row (too few files, headless or short sessions, or no usable
  input price: unknown, missing or 0) are counted, not warned on.
- `marks`: deletes fired-mark files older than `marks.max_age_days` (7) and the worker
  ceiling's agent markers older than `ceiling.max_age_days` (7), and counts them; a
  warning when the worker ceiling is on but a threshold is 0, so it allows every call.
- `turns`: off when turns is not installed, since Claude limits, Codex limits from the
  app-server, the warn-at mark and the worker ceiling need it; else the events usage
  registers with turns, and its worker-ceiling guard.

## From Python

```python
from moxi_usage import api

facts = api.model_facts("claude-opus-5-5", at=request_time)
facts.input, facts.cache_read, facts.window   # numbers, or api.UNKNOWN
facts.provenance.as_dict()
api.context_window("claude-opus-5-5[1m]", reported=None)
api.models()                                   # every row, with provenance

now = api.limits()                             # api.limits(harness="codex", pull=False)
for row in now.rows:
    row.label, row.state, row.used_percent, row.resets_at   # api.UNKNOWN when not read
    row.full_at                                # None when there is no projection
report = api.report("daily", since="2026-10-01", tz="UTC")
for row in report.rows:
    row["day"], row["model"], row["tokens"], row["cost"]   # cost may be api.UNKNOWN
report.unpriced_models, report.total["unknown_rows"]

ctx = api.context(session_id)                  # api.context(sid, agent_id="a...")
state = api.mark(session_id)                   # MarkState: purposes, tokens, agents
ctx.tokens, ctx.window, ctx.percent, ctx.text()
point = api.restart_point()                    # rows per model and population, marks
api.threshold("claude-opus-5-5", "1.5x", "main").tokens   # None when unavailable
status = api.report("status", fresh=False)    # the index itself, one row per lane

# interface 2: modules.load("usage", interface=2)
row.opened                                     # when the window started, or None
api.report("daily", folder="/work/app")        # the sessions run in one folder
api.report("projects", by="folder")            # by folder alone, whatever the project
api.report("terms").total["saving"]            # what caching saved, or None
```

usage offers interfaces 1 and 2. Interface 2 is interface 1 plus those four: a limits
row's `opened`, `report`'s `folder` filter, the `projects` report by folder, and `saving`
on a `terms` total. A module that needs one of them loads usage with `interface=2`.

Every result has `as_dict()` for JSON: an unknown fact is left out and named in the
object's `"unknown"` list.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/usage/LICENSE).

The shipped model table, `data/models.json`, is derived from LiteLLM's
`model_prices_and_context_window.json` (MIT, Copyright (c) 2023 Berri AI). LiteLLM's
license notice ships beside it as
[`data/LICENSE-litellm`](https://github.com/CodeManEthan/moxi/blob/main/packages/usage/src/moxi_usage/data/LICENSE-litellm),
in the wheel and the source distribution.
