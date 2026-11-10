# Changelog

## 0.2.1 (2026-11-10)

- Interface 2, which is interface 1 plus four things. usage still offers interface 1.
  - `report` takes `folder`, a folder's whole path: it keeps the files whose `cwd` it
    is, whatever project key they carry. A path that is not absolute is refused.
  - `report("projects", by="folder")` groups by `cwd` alone.
  - A `terms` report's total carries `saving`, what caching saved over the report:
    `{input_tokens, read_tokens, reads_at_input, reads_cost, write_extra, saved}`. It is
    an estimate, the same tokens with no cache at the same prices. It is None when the
    report holds no cache read, and when a price group that holds cache reads or writes
    is unpriced or lacks a price the sums need. `moxi usage terms --json` prints it.
  - A limits row carries `opened`, when its window started, or None when the readings
    cannot say. `moxi usage limits --json` prints it.
- The page server's reads changed. The page still shows what it showed.
  - New `GET /api/window?harness=&limit_id=&minutes=`: one fresh plan window, with when
    it opened, the first and last reading of its period, and the sessions of that
    harness with requests since it opened (how many, their requests and cost since then,
    and the ten dearest). It writes nothing and makes no pull.
  - New `GET /api/restart-point?model=`: `{model, tokens, reason}`, the context size past
    which carrying on with a main session of that model costs more than restarting it,
    from the restart-point measure; `tokens` is null with the reason when there is no
    measure, no row for the model, or a price the model table lacks. It writes nothing.
  - `/api/limits` makes no Codex pull and so no longer waits on turns; its `pull` is
    always null. The stream's poller still pulls, and the `limits` topic carries what
    the pull did. A row carries `opened`.
  - `/api/sessions` takes `skip` and answers with `skip` and `count` beside `top` and
    `more`. `top` is at most 200 (it was 1,000). Sessions come dearest first, then by
    tokens, harness and session id. Each row carries `context`, the whole prompt of the
    newest request of the session's main file and the window it ran against, or null.
    A row no longer carries `title`.
  - `/api/session` carries the session's `context`.
  - `/api/projects` takes `by=project|folder` and answers with `by` and `home`, the
    account's home folder.
  - Every report route takes `folder`.
  - `/api/terms` carries `saving` on its total.
  - Every report's answer, `/api/status`, `/api/window`, and `/api/limits` with its
    topic carry `today`, the page server's local date. The `limits` topic is published
    once more a day, when that date changes.

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
