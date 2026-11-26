# Changelog

## Unreleased

- The worker ceiling's allowlist reads shell text through turns' shell reader
  (`read_shell`) in place of a lexer of its own. It refuses and allows the same commands
  with the same reasons; `lex` keeps its signature, `AND` and `Refused`. The `turns`
  extra asks for moxi-turns 0.2.8 or later.
- `marks.default` is gone: a session with no `MOXI_USAGE_WARN_AT` stamp gets no notice,
  whether the launcher started it with no `warn_at` (a Codex launch that sets none) or
  did not start it. Nothing reads a config default; the key is retired, and a config that
  still sets it is refused, saying to set `warn_at` on the launch instead. `moxi usage
  marks` says `marks from nothing` where it said `marks from config`, and
  `mark.config_default` is removed.
- The warn-at notice reads `context 412k: past the 1x mark for <model>`, the context in
  thousands rounded halves up, in place of `context 412,330 passed the 1x restart point
  (398,112) for <model>`. A crossing handler's line on the same call is appended to the
  notice after `. `, so the model sees one line; a crossing on a call with no notice keeps
  its line on its own. `notice_line` takes `(tokens, mark, model)` and `thousands` is new.
- `Find a model` sends its search through moxi-ui's `readLater`, so the page root's
  `data-reading` mark counts the search from the keystroke until its matches are drawn,
  and a test's press waits for them. A user sees no change. The `ui` extra asks for
  moxi-ui 0.2.8 or later.
- On Windows usage depends on `tzdata`, so a zone named by its IANA name (`Europe/Paris`)
  resolves there: Windows has no IANA time zone database of its own for `zoneinfo` to
  read.
- Every fast tier in the shipped model table carries its own cache prices, read from
  Anthropic's pricing page (platform.claude.com/docs/en/about-claude/pricing) on
  2026-11-21: Opus 5.5 fast cache reads $0.40, 5-minute writes $10, 1-hour $16; Opus 5 and
  Opus 4.8 fast $1, $12.50 and $20. Opus 5.5's fast cache reads were filled at 0.1x the
  fast input price ($0.80), where Anthropic charges 0.05x; it is the only price that
  moves. Fable and Mythos 5.2 already carried theirs.
- While the reads under the filter line are out, after a day is picked on the heat map, a
  filter or the range is changed, or a pass finishes, everything under the line (the
  chart, the heat map and the sections) stays drawn as it was, faded, and each part is
  drawn anew as its read answers. The chart, the heat map and the sections were gone, or
  read `Reading…`, until then.
- The Sessions table scrolls to every row it has read. Under the shell's style policy
  (`style-src 'self'`) its spacer rows' height was refused, so the box stopped after
  about 30 rows, and each draw logged a console error. The page writes no style
  attribute now.
- A move to another view of the page (Sessions, one session, a session's subagents,
  Projects or Folders, Prices, or the page itself) adds a history entry, so Back returns
  to the view that was open before: from a session opened from the Sessions table, to
  the table, and from the table to the page. The page rewrote its address in place, so
  Back left it. A limit row, Models and Kinds still rewrite the address in place.
- The project filter reads `All projects` (`All folders` by folder) on its first item
  and on its button with none picked, where it read `all`.
- Under Tokens each chart has its own table: `Cache reads` with a Cache read column, and
  `Billable work` with Input, Cache create and Output, in the order its chart stacks
  them. One table held all four classes.
- Each chart has its own `Table` beside its name, which switches that chart alone; the
  chart by cost keeps its name over its table. One `Table` beside the sentence switched
  every chart of the group.
- Every chart, and the heat map, shows its chart again, not its table, each time the page
  draws it again: on a new address, range, filter, Cost / Tokens or split, and after a
  pass. A table stayed in a chart's place until its `Chart` was clicked.
- A cost on the page that is not whole is marked in its own cell: a dim `*`, why on
  hover, and `, not whole: <why>` in the cell's text for a screen reader. The tables
  that gave it a `Not whole` column (Sessions, Projects or Folders, Models, the chart's
  table and `Check against a bill`) have none now; the chart's table marks the day's or
  month's cell. `Check against a bill` names a price group's rule (`long-context`) in its
  Priced by cell, where the `Not whole` column held it.
- The token index moves to version 3 and keeps the prompt ids of each Claude transcript,
  a prompt that drew no request (`/exit`, `/clear`, another local command) included. The
  Sessions table's Turns counts a Claude session's prompts from them, where it counted
  only the prompts that drew a request; Codex and OpenCode sessions count their turns as
  before. The first pass after the upgrade reads every Claude transcript again from its
  start, about as long as `--rebuild` takes over them; Codex and OpenCode are not read
  again. A transcript that pass can't read, such as one on an offline archive drive,
  keeps its old count until it is read. An older usage can't open a version 3 index.
  usage now requires moxi-sessions 0.2.10, the first with `Batch.prompt_ids`.
- The page's opened Sessions section is its table of every session of the range, read at
  once; the two lists of two-line rows (the last seven days' ten dearest with `Show`, and
  `Earlier`) and the section's `Table` toggle are gone. A session opens first in the
  section, over the table, which stays. The table's columns are Project (or Folder
  without the projects module), Session, Started, Active, Turns, Input, Cache create,
  Cache read, Output, Subagent share and Est. cost; Harness, Models, Requests, Tokens,
  Context and the Folder column beside Project are gone. Each token class cell has a bar
  against its column's most among the rows read, in the class's shade, and a Codex
  session's name a `codex` badge. The Active header's hover says `First → last request
  (not the file's mtime)` and Turns' `Prompts in the main session file; never summed with
  subagents`; Subagent share's header has no hover.
- The page chart's table lists its periods oldest first, where it listed them newest
  first, and has the day and a column per part of the chart, with no Total column (the
  harness's own cost went with it). A day reads as
  its date (`2026-11-08`, where it read `Nov 8`), and a part with nothing that day reads
  `$0.00`, where its cell was blank.
- The page's chart by cost is named `Daily estimated cost — by class` or `— by model`
  (`Monthly` where its bars are months), and By model the line under it says `Per-model
  dollars, straight from the index.`
- The heat map names a day by its date: `2026-11-03: 1.75 USD` for a reader that cannot
  see its shade, `0.00 USD` for a day nothing was used, and `2026-11-03` in its hover and
  its table, where it wrote `Nov 3 2026` and `nothing used`.
- The Sessions table reads 500 sessions at a time (`/api/sessions` takes `top` up to
  500) and reads the next 500 as the rows drawn near the end, or on `Load more`, where it
  read 200 on `Show`. A next page that failed is read again only on a scroll that moves
  the rows drawn or on `Load more`. Its rows sit in a box of their own, 40 px each, only
  those in view drawn, and the arrow keys move a row cursor; Enter on a row opens its session. Rows of
  one value sort by session id then harness in either direction, where they kept the
  order by cost (then tokens). A session with no turn id counts 0 turns, where its cell
  was empty, and the subagent share is drawn as a meter.
- The page's heat map follows only the project (or folder) filter, no longer Harness and
  Model, and is drawn even when no day of the 53 weeks was used. Every day is a button,
  not only the used ones; weeks are a fixed 13 px apart, where the pitch shrank to fit;
  every month is named; each day's hover gives its cost and tokens, and its token classes
  (at 0 on a day nothing was used) only when the day is in the range, where a day nothing
  was used gave its cost alone; its table lists the days that cost something, and a used
  day whose cost is not whole, oldest first, where it listed every used day. It is named `Daily estimated
  cost — trailing 53 weeks`, and its line names the time zone of its days.
- The page's filter line has By class / By model beside Cost / Tokens. By model the chart
  by cost is stacked by model, the six that cost most in the range in four shades and two
  line fills of the text colour and the rest as one `Other` part. The chart's table
  follows the split: a column per token class By class, where it had a column per model,
  and a column per model By model. Under Tokens both buttons are off.
- The page names the token classes Input, Cache create, Cache read and Output (legends,
  hovers, table heads and the Token kinds rows), where it said Fresh input and Cache
  write(s), and a table's column of dollars reads `Est. cost`, where it read `Cost`. The
  Cost / Tokens switch, the Cost group's name and the 5-minute and 1-hour cache write
  prices keep their words.
- The page's Project (or Folder) button counts what is picked, one included: `1 project`
  or `1 folder`, where it used to show the one picked by name.
- `moxi usage context` with a session or agent it cannot find, or a session more than one
  harness holds, prints one line on stderr and nothing on stdout, `--json` included, and
  exits 2 as before; it used to print `context unknown: <why>` (or `"found": false`) on
  stdout. A session in more than one harness is named with the path of each record.
- The line a subagent leaves for its parent cuts the agent id to eight characters and `…`
  (one character), not `...`.
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
  A press on a row, or Enter on its session's name, opens the session first in the
  section, as its address does.
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
- The shipped model table prices `claude-fable-5-2` and `claude-mythos-5-2` ($8 / $40,
  cache reads $0.20, 5-minute writes $10, 1-hour $16, fast mode $16 / $80) and
  OpenCode's free `big-pickle`, `minimax-m3.1-flash-free` and `space-bunny-free` (at 0),
  each a supplement row with its source and date, where their tokens were unpriced. The
  LiteLLM snapshot is the same, so no other price moves. A Fable or Mythos session now
  gets its own read and write multipliers; a free model still gets no restart-point row,
  since its input price is 0.
- A model named in `thresholds.BORROWED_ROW` that has no measured row of its own for a
  population uses the named model's row for it: `claude-fable-5-2` borrows
  `claude-opus-5-5`'s, so a Fable session gets warn-at marks before it has the 20 files a
  row needs. Only S, P and g are borrowed; `r` and `w` come from the model's own prices.
  Its own row wins once the measure writes one. A `Threshold` names the lender in
  `borrowed_from`, as do `/api/restart-point` and `restart-point --json`; `restart-point`
  prints the row as `borrowed from claude-opus-5-5` and the thin line adds `borrows
  claude-opus-5-5's row`; an unavailable reason says when the row was borrowed.

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
