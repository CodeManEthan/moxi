# Changelog

## Unreleased

- The page's chart by cost stacks each bar by token class (fresh input, cache write,
  cache read, output), each part the class's own dollars from `/api/terms` by the chart's
  period, under a legend, with every class on hover. By tokens it draws two charts:
  `Cache reads` alone and `Billable work` (fresh input, cache writes, output) stacked,
  each with its share of the range's tokens.
- The page has a heat map under the chart: each day's estimated cost over the 53 weeks to
  today, whatever the range, under the other filters, in five shades set by the
  quantiles of the days that cost something. A used day is a button that sets the range
  to it; `Table` lists the used days.
- The Sessions section's `Table` lists every session of the range, 200 at a time with
  `Show` for more, where it listed only the rows the lists had shown. A header button
  sorts by its column (`aria-sort`), highest first, and a second click turns it around.
  It has a Turns column and a Subagent share column, each sortable.
- The page's Project (or Folder) filter picks several at once: each entry is a check that
  adds it or takes it away and leaves the menu open, the button counts them (`2
  projects`), and `all` clears them. Everything under the filter line reads the files of
  any of them.
- A report's `project` and `work_dir` each take one key or a sequence of them, and keep
  the files any of them matches; several go out in `filters` joined by `, `. The page's
  report routes take several projects or folders in `project` and `folder`, one to a
  line. `/api/sessions` takes `sort` (`cost`, `tokens`, `requests`, `fresh_input`,
  `cache_write`, `cache_read`, `output`, `started`, `active`, `turns`, `subagent_share`,
  `project`) and `order` (`asc` or `desc`), and pages with `top` and `skip` in that
  order. Its rows carry `turns`, the distinct turns of the session's main file, and
  `subagent_share`, the part of the session's cost its subagents ran up.
- The `sessions` report's rows carry `turns` and `subagent_cost`.
- `moxi usage context SESSION` with no `--harness` asks every harness and refuses a
  session more than one holds, naming them, where it took the first. It exits 0 whenever
  it names the context's state, `context unknown` included, and 2 for a session or agent
  it cannot find or a session in more than one harness; it used to exit 1 for any unknown
  context. `Context` has `found`, and its JSON a `found` key.
- `moxi usage restart-point --measure --days N` measures over the last N days this once;
  `restart_point.days` stays the window of every other measure. N below 1, or `--days`
  without `--measure`, exits 2.
- The worker ceiling's default warning and refusal carry the wrap-up recipe: commit with
  one `-m` in single quotes and no heredoc, `$` or backtick, get the commit line from
  `moxi repo rules`, put every trailer in the last paragraph, and return a
  `CEILING HANDOFF` block. `ceiling.allow_commands` defaults to `moxi repo rules`, the
  command the refusal names; it used to start empty.
- A command in `ceiling.allow_commands` is allowed past the worker ceiling only whole: a
  command with a word more or fewer is refused, where any words after an entry used to
  pass. A word written `<...>`, such as `<key>`, stands for one name word, so an entry
  can allow one argument and no more. An entry that relied on trailing words needs a
  `<...>` word for each.
- Every setting carries page fields, which `moxi config usage show --json` prints and
  the settings page draws: a plain label, a line where the label can't carry it, the
  unit of each wait, age and token count, seven settings shown first (the default
  warn-at mark, the worker ceiling and its two numbers, the Claude Code folders to read,
  the weekly restart-point measure and the price table's age), the rest under Prices,
  Reading, Limits, Marks, Restart point and Worker ceiling, and a can-hurt mark on
  turning the worker ceiling off. usage needs moxi 0.1.9 or later.
- The opened session's chart measures the words it places after the plot (`a request`,
  `window` and the last context) in the face the page draws them in, and ends the plot
  where the widest still fits, so they stay inside the chart in any font. A compaction's
  word turns to end at its line by the same measure.
- Public names renamed for the naming rules (callers passing the old keyword or reading
  the old name must change): `moxi_usage.api.report`: `folder` is now `work_dir`. The
  CLI flag `--folder`, the page's query parameter and the report's JSON filter key stay
  `folder`.
- usage has a row on Home in the browser UI shell, the `row` component (`<moxi-usage-row>`,
  order 300). It says on one line how full the main 5-hour and weekly windows of the
  harness read last are, when the 5-hour one resets and today's cost:
  `Claude 5-hour 49%, resets 21:34 · weekly 60% · today $19.54`, with `read 19:08` at the
  right. A window that fills before it resets takes the row, set heavy, and the row then
  needs the user (40 on Home's scale): `Claude weekly limit fills tomorrow 17:51, resets
  Wed 23:58 · today $19.54`. A cost with an unpriced model reads `$19.54+`; with no window
  read the row is the cost alone, and with neither there is no row. It reads its route
  when it is shown and every 30 s while the window is visible. The row is a link to the
  usage page.
- A new read, `GET /api/summary`, the row's: `{now, limits, cost}`. `limits` is the
  harness whose fresh main windows hold the newest reading, with each of those windows'
  percent, reset and `full_at`; `cost` is today's dollars over every model and whether
  they are whole. It never pulls a Codex reading through turns, so it never starts a
  Codex app-server; it says what the readings store and the newest rollout hold. It is
  the one route of usage the gateway serves, so the row shows on the phone too; it
  carries numbers, times and a harness's name, nothing that can hold a path or text.
- usage announces a status line segment, `moxi_usage.segment`. Its `from_input` reads
  the `rate_limits` Claude Code hands its status line command and shows each window as
  `5h:42% 7d:61%`, `5h:reset` once a window has reset, with a window that has no percent
  left out; green under 50%, yellow from 50%, red from 80%. It reads no file and records
  nothing, and shows nothing when the input has no `rate_limits`.
- The window rules (each Claude kind's window, a row's label and its state) move into
  `moxi_usage.windows`, which imports only `math`, so the segment loads without the
  readings store. `readings` and `limits` import them back, so every name is where it
  was and nothing behaves differently.

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
