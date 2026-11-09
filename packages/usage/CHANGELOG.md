# Changelog

## 0.2.0 (2026-11-08)

- Config keys `index.wait_seconds`, `limits.codex_pull_after` and `ceiling.on` are now
  `index.wait_s`, `limits.codex_pull_after_s` and `ceiling.enabled`. An old key, set in
  the config file, is refused, naming the new one.
- The limits row field `age_seconds` is now `age_s`.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_usage.api.Row`: `age_seconds` is now `age_s`.

The first public release.

- usage needs moxi 0.1.6 or later.
- LiteLLM's license notice ships with the model table derived from its price file, as
  `data/LICENSE-litellm`, in the wheel and the source distribution, and the README's
  License section names it.
- The README says how to install usage, what it needs, and where its files live. The
  source distribution includes this changelog. The package declares keywords and
  classifiers.
- `moxi usage terms` and the `terms` report print every term of a cost: each price
  group's tokens, price and dollars for input, cache read, the two cache writes and
  output, adding up to the cost. Grouped by model it is a reconciliation table; by day,
  month or a session's agents it gives cost by token class and by price tier.
- `moxi usage requests` and the `requests` report give one row per request of a session
  or one of its agents: tokens by class, cost, context against the model's window, turn
  and compaction marks.
- The page gains a Cost terms view (the Reconcile table and the rates of the models in
  use, with a search over the whole model table), a cost-by-class measure on the daily
  view, and a session view with a per-request chart and a context curve.
- The token index moves to version 2 and keeps compaction markers and the window a
  harness reports. The first pass after the upgrade (`moxi usage index`, any report verb
  without `--no-pass`, `api.report(fresh=True)` or the restart-point measure) is a
  rebuild and takes about as long as `--rebuild`. The page asks you to run
  `moxi usage index` until one has run. usage 0.1.0 can't open a version 2 index; to go
  back, remove the index and let 0.1.0 build its own.
- Pressed buttons and the current view use the theme's selected colours, and chart
  series take its six series colours.
- The `ui` extra needs moxi-ui 0.2.3 or later, which has those colours.
