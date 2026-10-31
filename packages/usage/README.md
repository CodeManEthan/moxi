# moxi-usage

Moxi's usage module answers four questions about the coding-agent sessions on a machine:

- how much of each plan window is used, and when it will run out (limits);
- how full a session's context is, and whether it has passed its mark (context and marks);
- whether a subagent should stop (the worker ceiling);
- what the tokens cost, by day, model, session or project (the token index).

It decodes no harness file of its own: every record it reads comes through Moxi's sessions
module, in sessions' one shape.

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
a digit, so it never covers every future model). A row that gives any price replaces all
of the lower layer's prices for that model, so a cache price never stays behind at an old
rate; `window` and `max_output` replace only themselves.

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
  `limits.codex_pull_after` seconds (600), `limits` asks Codex for a new one through
  turns; when it can't, it keeps the stored reading and says why.
- **Claude** readings come from Claude Code's own session report, which turns hands
  usage after each turn. Without turns there are none, and Claude's rows say so.

A window is known by its duration, whatever slot the harness puts it in: `5h`, `7d`, or
its minutes. A per-model weekly cap is its own row (`7d opus`). Each row is `fresh`,
`reset` or `unavailable` with the reason. Claude's row takes the reading with the latest
future reset time (on a tie the highest percent), so an idle session's old number never
passes for fresh; Codex's takes the newest reading. When a window has three or more
readings of its current period in the last `limits.projection_minutes` (60), a straight
line through them says when it reaches 100%, given only when that comes after now and
before the reset. Of what else a reading says, the line prints only a spend limit's
dollars; `--json` keeps all of it.

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

## Commands

    moxi usage limits [--harness claude|codex] [--no-pull] [--json]
    moxi usage prices [MODEL] [--provider P] [--json]
    moxi usage prices update [--dry-run] [--revert] [--json]
    moxi usage index [--rebuild] [--force] [--no-wait] [--check] [--json]

`limits` prints one line per harness: each window's percent used (used, not left), when
it resets, when it fills at the recent rate, and the age of the oldest reading on the line.
`--no-pull` never asks Codex for a new reading.

`prices` with a model prints its facts and where each came from; without one, it prints
the layers in effect. `prices update` is the only thing in usage that reaches the network,
and only when you run it: it fetches LiteLLM's file from `prices.url`, converts it, refuses
a result with under half the shipped table's rows, prints which models were added, removed
or repriced, and which of the models in your token index it reprices, prices for the first
time or leaves unpriced, and writes the snapshot layer. `--dry-run` prints and writes nothing.
`--revert` removes the snapshot layer, so the shipped table rules again. A fetch that fails
exits 75.

`index` runs a pass, prints what it did in one line, then the index's status: each lane's
state, its files, the requests it holds and how many are copies counted once, the rollouts
left out as imported, the rows held and why, the last pass and the index's schema version
(also `api.report("status")`). Only one pass writes at a time: another waits
up to `index.wait_seconds` (30) and then exits 75; `--no-wait` exits 75 at once.
`--rebuild` reads every source again from its start, in `index.workers` processes (0 picks
one per core, up to 8). A schema change that would drop the rows of an archive lane whose
drive is offline is refused (exit 3) until the drive is back, or until `--rebuild --force`
drops them. `--check` compares the stored claims with the claimed view and exits 1 naming
the keys that differ.

## Stores and the doctor

usage keeps its files in Moxi's roots, each made by its first write and readable only by
you: the token index `STATE/usage/index.db`, the limits readings `STATE/usage/readings.db`
(kept 8 days) and the model snapshot `DATA/usage/models-snapshot.json`. `marks/`,
`ceiling/` and `restart-point.json` under `STATE/usage` are declared for the context marks,
the worker ceiling and the restart-point measure, which are still to come. `moxi paths
usage` lists them all.

`moxi doctor usage` checks each store and adds usage's own rows, none of which writes:

- `models.snapshot`: the model table in effect is older than `prices.max_age_days` (120),
  or a snapshot on disk can't be read; the fix is `moxi usage prices update`.
- `models.unpriced`: each model the index saw in the last 30 days with no price or no window.
- `index`: the last pass and the counts; a warning for an offline archive lane, held rows
  (each lane's reasons) or a last pass more than 7 days old.
- `codex.imports`: a warning when the last pass couldn't read Codex's imports file, or
  counted rollouts whose only import mark is the desktop app's originator.
- `turns`: off when turns is not installed, since Claude limits need it.

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
status = api.report("status", fresh=False)    # the index itself, one row per lane
```

Every result has `as_dict()` for JSON: an unknown fact is left out and named in the
object's `"unknown"` list.
