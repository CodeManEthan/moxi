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
line through them says when it reaches 100%, given only when that comes before the reset.

## Commands

    moxi usage limits [--harness claude|codex] [--no-pull] [--json]
    moxi usage prices [MODEL] [--provider P] [--json]
    moxi usage prices update [--dry-run] [--revert] [--json]

`limits` prints one line per harness: each window's percent used (used, not left), when
it resets, when it fills at the recent rate, and the age of the oldest reading on the line.
`--no-pull` never asks Codex for a new reading.

`prices` with a model prints its facts and where each came from; without one, it prints
the layers in effect. `prices update` is the only thing in usage that reaches the network,
and only when you run it: it fetches LiteLLM's file from `prices.url`, converts it, refuses
a result with under half the shipped table's rows, prints which models were added, removed
or repriced, and writes the snapshot layer. `--dry-run` prints and writes nothing.
`--revert` removes the snapshot layer, so the shipped table rules again. A fetch that fails
exits 75.

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
    row.label, row.state, row.used_percent, row.resets_at, row.full_at
```

Every result has `as_dict()` for JSON: an unknown fact is left out and named in the
object's `"unknown"` list.
