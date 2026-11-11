# Changelog

## 0.2.1 (2026-11-10)

- usage's page is drawn anew, as one page with no tabs and no heading of its own.
  - The plan limits lead: a group per harness with a window read, a row per window with
    how much is used in words, a thin line with a tick at how much of the window has
    passed, when it resets and when it fills, and a warning line while any limit fills
    before it resets. A window whose period has ended is one dim row. A row opens in
    place, with the window's readings, why it has no fill time, and the sessions that
    used it since it opened.
  - Cost gives today, the last 7 days and the last 30, each beside the period before, and
    today's two dearest sessions.
  - The filter line holds Range (now with Today, From … to … and All time, each range
    with its figure), Project or Folder, Harness and Model, and Cost / Tokens. The chart
    is by month only for All time and a range over 92 days.
  - Sessions, Projects (or Folders), Models, Token kinds and Prices are each one row that
    opens in place. Sessions shows the week's ten dearest, `Show` for more and the days
    before as `Earlier`; a session opens in its row, with its subagents and one agent's
    requests. Token kinds says what caching saved, as an estimate. Prices holds `Find a
    model`, `Check against a bill` (the Reconcile table) and `Details`. Each opened
    section but Token kinds and Prices ends with `Table`, its rows with every figure
    their read carries.
  - A session is shown by the name typed for it, else by its folder and start, never by
    its id; a title the harness made up is no longer shown as its name.
  - The foot says when the sessions were read, the files by harness and `Read new
    sessions`, and under it what the figures leave out: a harness not read, files held
    back, a total that is not whole, a read that failed.
  - Addresses: `limit/<harness>/<limit_id>/<minutes>`, `sessions`, `sessions/<harness>/<id>`
    and its `/<agent>`, `projects` or `folders`, `models`, `kinds` and `prices`. The page
    rewrites its address in place, so Back leaves it. `daily`, `terms` and
    `session/<id>` are gone.
  - Left out from the old page: the tabs and the heading; colour for bad news, including
    the meter colours at 75% and 90%; a decimal on a percent under ten; cost by class as
    a stacked chart; a limits row for a window with no reading; a line for a harness
    whose folder was never there.
  - The `limits` and `cost` blocks on the home view are unchanged.
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
- The page server's reads changed.
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
  - An answer that names a session carries what names it: `{harness, session_id, name,
    folder, started, known}`. It is on each `/api/sessions` row, as `ref` on
    `/api/session`, and on `/api/window`'s last reading (`session`) and each session
    listed there. `name` is the name typed for the session in its harness, from the
    sessions module, null when there is none; a title the harness made up is not a
    name. `known` is true when the session's record was found, false when it is gone,
    and null when it was not looked for. For a session whose record is gone, `folder`
    is the index's `cwd` and `started` its earliest request in the index. The sessions
    module is asked once per answer.
- usage requires `moxi-sessions` 0.2.8 or later, for sessions' interface 5.
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
