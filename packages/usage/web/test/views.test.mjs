import assert from "node:assert/strict";
import test from "node:test";

import {
  agents, agentTreemap, cellLabel, classTotals, costAge, costDays, costText, dailyChart,
  filterKeys, flags, fmtAgo, fmtCost, fmtPercent, fmtSpan, fmtTokens, heldLine, heldReasons,
  labelEvery, legacyRoute, MARGIN, menuPaths, meter, NO_FILTERS, parseRoute, projectMenu, provenanceLine,
  rangeFor, refusedLine, reportState, segmentTitle, sessionFilters, sessionRoute, shiftDay,
  shouldPass, strip, tickText, viewPath,
  areaPath, BARS_UP_TO, classChart, classCosts, contextChart, LABEL_CHAR, LABEL_HEIGHT, linePath,
  pricesPath, rateAsks,
  rateLine, readout, reconcileLines, requestAxis, requestNotes, requestRules, requestsChart,
  requestsPath, splitBars, splitKeys, splitLabel, stepPath, termCell, tierText,
} from "../src/views.ts";

const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const report = (rows, extra = {}) => ({ since: null, until: null, tz: "local", filters: [], rows,
  total: sums(), unpriced_models: [], rules: [],
  index: { passed: false, last_pass: 100, note: "no pass ran first; this is the index as it is" }, ...extra });
const up = (value) => ({ loading: false, gone: false, failure: null, refused: null, value });

// --- formatters ---

test("token counts", () => {
  assert.equal(fmtTokens(950), "950");
  assert.equal(fmtTokens(1234), "1.23k");
  assert.equal(fmtTokens(12_345), "12.3k");
  assert.equal(fmtTokens(123_456), "123k");
  assert.equal(fmtTokens(4_560_000), "4.56M");
  assert.equal(fmtTokens(1.2e9), "1.20B");
  assert.equal(fmtTokens(null), "—");
});

test("dollars, with a dash for nothing priced", () => {
  assert.equal(fmtCost(12.345), "$12.35");
  assert.equal(fmtCost(0), "$0.00");
  assert.equal(fmtCost(0.004), "<$0.01");
  assert.equal(fmtCost(1234.5), "$1,235");
  assert.equal(fmtCost(null), "—");
  assert.equal(costText(sums({ cost: 2, harness_cost: 1.5 })), "$2.00 (harness $1.50)");
  assert.equal(costText(sums({ cost: 2 })), "$2.00");
});

test("percents and spans", () => {
  assert.equal(fmtPercent(42.4), "42%");
  assert.equal(fmtPercent(4.54), "4.5%");
  assert.equal(fmtPercent(null), "—");
  assert.equal(fmtSpan(45), "45s");
  assert.equal(fmtSpan(12 * 60), "12m");
  assert.equal(fmtSpan(3 * 3600 + 5 * 60), "3h 05m");
  assert.equal(fmtSpan(2 * 86400 + 4 * 3600), "2d 4h");
  assert.equal(fmtAgo(null, 100), "never");
  assert.equal(fmtAgo(98, 100), "just now");
  assert.equal(fmtAgo(100 - 600, 100), "10m ago");
});

// --- days, routes and queries ---

test("ranges are worked out from the browser's day, both ends included", () => {
  assert.equal(shiftDay("2026-03-01", -1), "2026-02-28");
  assert.deepEqual(rangeFor("7d", "2026-11-02"), { since: "2026-10-27", until: "2026-11-02" });
  assert.deepEqual(rangeFor("30d", "2026-11-02"), { since: "2026-10-04", until: "2026-11-02" });
  assert.deepEqual(rangeFor("all", "2026-11-02"), {});
});

test("routes", () => {
  assert.deepEqual(parseRoute(""), { view: "daily" });
  assert.deepEqual(parseRoute("daily"), { view: "daily" });
  assert.deepEqual(parseRoute("/sessions/"), { view: "sessions" });
  assert.deepEqual(parseRoute("projects"), { view: "projects" });
  assert.deepEqual(parseRoute("models"), { view: "models" });
  assert.deepEqual(parseRoute(sessionRoute("ses a/b")), { view: "session", session: "ses a/b", agent: "main" });
  assert.deepEqual(parseRoute(sessionRoute("s1", "a/1")), { view: "session", session: "s1", agent: "a/1" });
  assert.equal(sessionRoute("s1", "main"), "session/s1");
  assert.deepEqual(parseRoute("terms"), { view: "terms" });
  assert.deepEqual(parseRoute("session/s1/a/b"), { view: "unknown" });
  assert.deepEqual(parseRoute("session/"), { view: "unknown" });
  assert.deepEqual(parseRoute("pricing"), { view: "unknown" });
});

test("each view sends only the keys its route reads", () => {
  const today = "2026-11-02";
  const all = { preset: "7d", harness: "codex", model: "m1", project: "p1" };
  assert.equal(viewPath({ view: "daily" }, NO_FILTERS, today, "month"),
               "api/daily?by=month&since=2026-10-04&until=2026-11-02");
  assert.equal(viewPath({ view: "sessions" }, all, today),
               "api/sessions?top=200&since=2026-10-27&until=2026-11-02&harness=codex&project=p1&model=m1");
  assert.equal(viewPath({ view: "session", session: "s 1" }, { ...NO_FILTERS, preset: "all" }, today),
               "api/session?session=s+1");
  assert.equal(viewPath({ view: "projects" }, { ...NO_FILTERS, preset: "all" }, today), "api/projects");
  assert.equal(viewPath({ view: "models" }, { ...all, preset: "all" }, today),
               "api/models?harness=codex&project=p1&model=m1");
  assert.equal(viewPath({ view: "unknown" }, all, today), null);
  const menus = menuPaths(all, today);
  assert.ok(!menus.projects.includes("project=") && menus.projects.includes("model=m1"));
  assert.ok(!menus.models.includes("model=") && menus.models.includes("project=p1"));
  assert.deepEqual(filterKeys({ ...NO_FILTERS, preset: "all" }, today), []);
});

// --- states and flags ---

test("a sessions row opens its session under its own harness", () => {
  const today = "2026-11-02";
  const opened = sessionFilters({ ...NO_FILTERS, model: "m" }, { harness: "codex" });
  assert.deepEqual(opened, { ...NO_FILTERS, model: "m", harness: "codex" });
  const query = new URLSearchParams(viewPath({ view: "session", session: "s1" }, opened, today).split("?")[1]);
  assert.equal(query.get("harness"), "codex");
  assert.equal(query.get("session"), "s1");
});

test("the project menu offers project keys only, never a row's cwd", () => {
  const rows = [{ project: "alpha", cwd: null }, { project: null, cwd: "/work/loose" }, { project: "beta", cwd: null },
                { project: "alpha", cwd: null }];
  assert.deepEqual(projectMenu(rows, ""), ["alpha", "beta"]);
  assert.deepEqual(projectMenu(rows, "beta"), ["alpha", "beta"]);
  assert.deepEqual(projectMenu(rows, "gamma"), ["gamma", "alpha", "beta"]);
  assert.deepEqual(projectMenu([{ project: null, cwd: "/work/loose" }], ""), []);
});

test("a row is flagged with each reason its figures aren't whole", () => {
  assert.deepEqual(flags(sums()), []);
  assert.deepEqual(flags(sums({ cost: null, complete: false })), ["nothing here could be priced"]);
  const partly = flags(sums({ cost: 1, unpriced_tokens: 2500, unpriced_models: ["m9"], complete: false }));
  assert.deepEqual(partly, ["2.50k tokens have no price (m9), so the cost is the priced part"]);
  const unknown = flags(sums({ unknown_rows: 3, complete: false }));
  assert.equal(unknown.length, 1);
  assert.match(unknown[0], /^3 requests with a token count/);
  assert.deepEqual(flags(sums({ complete: false })), ["some of these figures are unknown"]);
});

test("a report view's states", () => {
  assert.equal(reportState({ loading: true, gone: false, failure: null, refused: null, value: null }).state, "loading");
  assert.equal(reportState({ loading: false, gone: true, failure: null, refused: null, value: null }).state, "gone");
  assert.deepEqual(reportState({ loading: false, gone: false, failure: null, refused: "bad date", value: null }),
                   { state: "refused", detail: "bad date" });
  assert.deepEqual(reportState({ loading: false, gone: false, failure: { status: 0, why: "no answer" }, refused: null, value: null }),
                   { state: "failed", detail: "no answer" });
  const none = report([], { index: { passed: false, last_pass: null, note: "there is no index yet; run `moxi usage index` to build it" } });
  assert.deepEqual(reportState(up(none)), { state: "empty", detail: none.index.note });
  assert.equal(reportState(up(report([sums()]))).state, "up");
});

// --- the daily chart ---

test("the daily chart stacks tokens by class on a nice scale", () => {
  const rows = [
    { period: "2026-10-01", model: "a", ...sums({ fresh_input: 100, cache_read: 300, cache_write: 50, output: 50, tokens: 500 }) },
    { period: "2026-10-01", model: "b", ...sums({ fresh_input: 100, tokens: 100 }) },
    { period: "2026-10-02", model: "a", ...sums({ output: 900, tokens: 900, complete: false }) },
    { period: null, model: "a", ...sums({ tokens: 5 }) },
  ];
  const chart = dailyChart(rows, "tokens", 400, 200);
  assert.deepEqual(chart.keys, ["fresh_input", "cache_read", "cache_write", "output"]);
  assert.deepEqual(chart.bars.map((bar) => [bar.period, bar.total, bar.incomplete]),
                   [["2026-10-01", 600, false], ["2026-10-02", 900, true]]);
  assert.equal(chart.undated, 1);
  assert.deepEqual(chart.ticks.map((tick) => tick.value), [0, 200, 400, 600, 800, 1000]);
  assert.equal(chart.ticks[0].y, 200 - MARGIN.bottom);
  assert.equal(chart.ticks.at(-1).y, MARGIN.top);
  const first = chart.bars[0];
  assert.deepEqual(first.segments.map((seg) => [seg.key, seg.value]),
                   [["fresh_input", 200], ["cache_read", 300], ["cache_write", 50], ["output", 50]]);
  const height = 200 - MARGIN.bottom - MARGIN.top;
  assert.ok(Math.abs(sum(first.segments.map((seg) => seg.height)) - height * 600 / 1000) < 1e-9);
  assert.ok(first.x >= MARGIN.left && chart.bars[1].x + chart.bars[1].width <= 400 - MARGIN.right);
  assert.ok(chart.bars.every((bar) => bar.width <= 56));
  const narrow = dailyChart(rows, "tokens", 4000, 200);
  assert.ok(narrow.bars.every((bar) => bar.width === 56));
});

test("the cost chart splits by model, an unpriced row counting nothing", () => {
  const rows = [
    { period: "2026-10", model: "b", ...sums({ cost: 2 }) },
    { period: "2026-10", model: null, ...sums({ cost: 1 }) },
    { period: "2026-11", model: "a", ...sums({ cost: null, complete: false }) },
  ];
  const chart = dailyChart(rows, "cost", 300, 200);
  assert.deepEqual(chart.keys, ["a", "b", "unknown model"]);
  assert.deepEqual(chart.bars.map((bar) => bar.total), [3, 0]);
  assert.equal(chart.bars[1].segments.length, 0);
  assert.equal(chart.bars[1].incomplete, true);
  assert.equal(tickText(1.5, "cost"), "$1.50");
  assert.equal(tickText(2, "cost"), "$2");
  assert.equal(tickText(2000, "tokens"), "2.00k");
});

test("the daily view totals each token class, leaving out one the index can't say", () => {
  const total = sums({ input: 5000, fresh_input: 200, cache_write: 800, cache_read: 4000, output: 1500 });
  assert.deepEqual(classTotals(total).map(({ key, value }) => [key, value]),
                   [["input", 5000], ["fresh_input", 200], ["cache_write", 800], ["cache_read", 4000], ["output", 1500]]);
  assert.deepEqual(classTotals(total).map(({ label }) => label),
                   ["Input", "Fresh input", "Cache write", "Cache read", "Output"]);
  const unsaid = classTotals(sums({ input: 5000, unknown: ["fresh_input", "cache_write"] }));
  assert.deepEqual(unsaid.map(({ key, value }) => [key, value]),
                   [["input", 5000], ["cache_write", null], ["cache_read", 0], ["output", 0]]);
});

test("each segment of a bar has its own title", () => {
  const rows = [{ period: "2026-11-01", model: "a", ...sums({ fresh_input: 10, cache_read: 3000, cache_write: 20, output: 40 }) }];
  const chart = dailyChart(rows, "tokens", 400, 200);
  assert.deepEqual(chart.bars[0].segments.map((seg) => segmentTitle("2026-11-01", seg.key, seg.value, "tokens")),
                   ["2026-11-01, fresh input: 10 tokens", "2026-11-01, cache read: 3.00k tokens",
                    "2026-11-01, cache write: 20 tokens", "2026-11-01, output: 40 tokens"]);
  assert.equal(segmentTitle("2026-11", "claude-x", 1.5, "cost"), "2026-11, claude-x: $1.50");
});

test("period labels thin out on a narrow chart", () => {
  assert.equal(labelEvery(7, 640), 1);
  assert.equal(labelEvery(90, 360), 15);
});

// --- one session ---

const agentRows = [
  { agent: "main", kind: "main", title: "Fix the parser", model: "m1", first_ts: 10, last_ts: 20, ...sums({ cost: 6, tokens: 600, requests: 3 }) },
  { agent: "main", kind: "main", title: null, model: "m2", first_ts: 5, last_ts: 8, ...sums({ cost: 2, tokens: 100, requests: 1 }) },
  { agent: "a1", kind: "subagent", title: "look around", model: "m1", first_ts: 12, last_ts: 14, ...sums({ cost: 2, tokens: 300, requests: 2, unknown_rows: 1, complete: false }) },
  { agent: "a2", kind: "subagent", title: null, model: "m3", first_ts: null, last_ts: null, ...sums({ cost: null, tokens: 50, unpriced_tokens: 50, complete: false }) },
];

test("a session's agents, their models' rows summed", () => {
  const list = agents(agentRows);
  assert.deepEqual(list.map((a) => [a.agent, a.kind, a.cost, a.tokens, a.requests, a.first_ts]),
                   [["main", "main", 8, 700, 4, 5], ["a1", "subagent", 2, 300, 2, 12], ["a2", "subagent", null, 50, 1, null]]);
  assert.deepEqual(list[0].models, ["m1", "m2"]);
  assert.equal(list[0].title, "Fix the parser");
  assert.deepEqual(list[0].flags, []);
  assert.equal(list[1].flags.length, 1);
  assert.deepEqual(list[2].flags, ["nothing here could be priced"]);
});

test("the treemap sizes each agent's models by cost and fills the box", () => {
  const map = agentTreemap(agentRows, 300, 200);
  assert.equal(map.measure, "cost");
  assert.deepEqual(map.cells.map((cell) => [cell.agent, cell.model, cell.value]).sort(),
                   [["a1", "m1", 2], ["main", "m1", 6], ["main", "m2", 2]]);
  for (const cell of map.cells) {
    assert.ok(cell.x >= 0 && cell.y >= 0 && cell.x + cell.width <= 300 && cell.y + cell.height <= 200);
  }
  const area = sum(map.cells.map((cell) => cell.width * cell.height));
  assert.ok(area > 300 * 200 * 0.85 && area <= 300 * 200);
  const big = map.cells.find((cell) => cell.model === "m1" && cell.agent === "main");
  assert.ok(big.width * big.height > 2 * Math.max(...map.cells.filter((c) => c !== big).map((c) => c.width * c.height)));
  assert.equal(map.cells.find((cell) => cell.agent === "a1").incomplete, true);
});

test("with nothing priced the treemap goes by tokens, and with nothing at all it is empty", () => {
  const unpriced = agentRows.map((row) => ({ ...row, cost: null }));
  const map = agentTreemap(unpriced, 300, 200);
  assert.equal(map.measure, "tokens");
  assert.equal(map.cells.length, 4);
  assert.deepEqual(agentTreemap([], 300, 200).cells, []);
  assert.equal(cellLabel({ agent: "main", model: "m1", width: 40, height: 40 }, "cost"), null);
  assert.deepEqual(cellLabel({ agent: "main", model: "m1", value: 6, width: 100, height: 60 }, "cost"), ["main", "m1 $6.00"]);
});

// --- models ---

test("a price's source, in a line", () => {
  const line = provenanceLine({ layer: "shipped", step: 1, rule: "exact", row: "m1", snapshot: "2026-10-01",
    overridden: [], period: { from: "2026-01-01", to: null }, note: null, filled: ["cache_write"], variant: null });
  assert.equal(line, "exact · layer shipped · row m1 · snapshot 2026-10-01 · from 2026-01-01 to now · filled cache_write");
});

// --- the limits meters ---

const NOW = Date.parse("2026-11-02T10:00:00Z") / 1000;
const window_ = (extra = {}) => ({ harness: "claude", limit_id: "claude", label: "5h", state: "fresh",
  window_minutes: 300, reason: null, used_percent: 40, resets_at: "2026-11-02T12:05:00Z",
  observed_at: "2026-11-02T09:58:00Z", full_at: null, projection: "resets first", note: null,
  dollars: [], unknown: [], ...extra });

test("a meter's fill, tone, reset, age and fill time", () => {
  const m = meter(window_(), NOW);
  assert.equal(m.title, "claude 5h");
  assert.equal(m.fill, 0.4);
  assert.equal(m.percent, "40%");
  assert.equal(m.tone, "ok");
  assert.match(m.reset, /^resets in 2h 05m \(/);
  assert.equal(m.age, "read 2m ago");
  assert.equal(m.full, "no fill time: resets first");
  assert.equal(meter(window_({ used_percent: 80 }), NOW).tone, "warn");
  assert.equal(meter(window_({ used_percent: 95 }), NOW).tone, "error");
  assert.equal(meter(window_({ used_percent: 130 }), NOW).fill, 1);
  assert.match(meter(window_({ full_at: "2026-11-02T11:00:00Z", projection: null }), NOW).full, /^full in 1h 00m/);
});

test("a window with no reading is muted and says why", () => {
  const m = meter(window_({ state: "unavailable", used_percent: null, resets_at: null, observed_at: null,
                            projection: null, reason: "turns is not installed" }), NOW);
  assert.equal(m.tone, "muted");
  assert.equal(m.fill, 0);
  assert.equal(m.percent, "—");
  assert.equal(m.reset, null);
  assert.equal(m.age, null);
  assert.equal(m.full, null);
  assert.equal(m.reason, "turns is not installed");
});

// --- the cost block ---

test("the cost block sums today and the 7 days by day", () => {
  const rows = [
    { period: "2026-11-02", model: "a", ...sums({ cost: 1.5, tokens: 100 }) },
    { period: "2026-11-02", model: "b", ...sums({ cost: null, tokens: 50, complete: false }) },
    { period: "2026-10-28", model: "a", ...sums({ cost: 3, tokens: 400 }) },
    { period: "2026-10-27", model: "a", ...sums({ cost: null, tokens: 5, complete: false }) },
  ];
  const found = costDays(report(rows, { today: "2026-11-02" }));
  assert.deepEqual(found.days.map((day) => day.day),
                   ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]);
  assert.deepEqual(found.today, { day: "2026-11-02", cost: 1.5, tokens: 150, complete: false });
  assert.deepEqual(found.days[0], { day: "2026-10-27", cost: null, tokens: 5, complete: false });
  assert.deepEqual(found.days[2], { day: "2026-10-29", cost: 0, tokens: 0, complete: true });
  assert.equal(found.week.cost, 4.5);
  assert.equal(found.week.tokens, 555);
  assert.equal(found.week.complete, false);
  assert.equal(costDays(report(rows)), null);
});

test("the cost block says how old the index is, and flags it past 10 minutes", () => {
  assert.deepEqual(costAge(1000, 1300), { text: "as of 5m ago", stale: false });
  assert.deepEqual(costAge(1000, 1600), { text: "as of 10m ago", stale: false });
  const old = costAge(1000, 1000 + 2 * 3600);
  assert.equal(old.stale, true);
  assert.match(old.text, /^as of 2h 00m ago, so these may leave out what came since/);
  assert.equal(costAge(null, 1000).stale, true);
});

// --- the index strip ---

const status = (total = {}, lanes = [], note = "no pass ran first; this is the index as it is") => ({
  lanes, index: { passed: false, last_pass: total.last_pass ?? null, note },
  total: { files: 3, held: 0, requests: 9, held_rows: [], last_pass: 1000, index_version: 3, ...total } });

test("the strip with no index offers the command", () => {
  const view = strip(up(status({ index_version: 0, last_pass: null }, [], "there is no index yet; run `moxi usage index` to build it")), null, 2000);
  assert.equal(view.state, "none");
  assert.equal(view.command, "moxi usage index");
});

test("the strip's last pass, lanes not online, held files and note", () => {
  const lanes = [{ lane: "claude", state: "online", reason: null }, { lane: "codex", state: "offline", reason: "no root" },
                 { lane: "opencode", state: null, reason: null }];
  const view = strip(up(status({ held: 2 }, lanes)), null, 1600);
  assert.equal(view.state, "up");
  assert.equal(view.lastPass, "last read 10m ago");
  assert.deepEqual(view.lanes, [{ lane: "codex", state: "offline", reason: "no root" },
                                { lane: "opencode", state: "not read yet", reason: null }]);
  assert.equal(view.held, 2);
  assert.equal(view.note, null);
  assert.equal(view.canPass, true);
  const older = strip(up(status({}, [], "the index is at version 2, before 3; run `moxi usage index` to bring it up")), null, 1600);
  assert.match(older.note, /version 2/);
  assert.equal(older.command, "moxi usage index");
  assert.equal(older.canPass, false);
  assert.equal(refusedLine(older.note, older), null);
  assert.equal(refusedLine("a pass already holds the index", older), "a pass already holds the index");
  assert.equal(refusedLine(null, view), null);
});

test("the strip says why files are held back, from each lane's reasons or the held rows", () => {
  const lanes = [
    { lane: "claude", state: "online", reason: null, held_reasons: [{ name: "too large", value: 2 }, { name: "unreadable", value: 1 }] },
    { lane: "codex", state: "online", reason: null, held_reasons: [{ name: "unreadable", value: 2 }] },
  ];
  const view = strip(up(status({ held: 5 }, lanes)), null, 1600);
  assert.deepEqual(view.heldReasons, [{ reason: "unreadable", count: 3 }, { reason: "too large", count: 2 }]);
  assert.equal(heldLine(view), "5 files held back: unreadable (3); too large (2)");
  const rows = [{ lane: "claude", path: "/x/a", part: "", reason: "too large" },
                { lane: "claude", path: "/x/b", part: "", reason: "too large" }];
  const fromRows = strip(up(status({ held: 2, held_rows: rows }, [])), null, 1600);
  assert.deepEqual(heldReasons(status({ held: 2, held_rows: rows }, [])), [{ reason: "too large", count: 2 }]);
  assert.equal(heldLine(fromRows), "2 files held back: too large");
  assert.equal(heldLine(strip(up(status({ held: 1 }, [])), null, 1600)), "1 file held back");
  assert.equal(heldLine(strip(up(status()), null, 1600)), null);
  assert.doesNotMatch(heldLine(view), /moxi usage/);
});

test("an index event moves the strip's last pass and says when a pass runs", () => {
  const running = strip(up(status()), { running: true, last_pass: 1000, note: null }, 1600);
  assert.equal(running.running, true);
  const done = strip(up(status()), { running: false, last_pass: 1590, note: "the pass failed (OSError)" }, 1600);
  assert.equal(done.running, false);
  assert.equal(done.lastPass, "last read 10s ago");
  assert.equal(done.note, "the pass failed (OSError)");
  assert.equal(strip({ loading: true, gone: false, failure: null, refused: null, value: null }, null, 0).state, "loading");
  assert.equal(strip({ loading: false, gone: false, failure: { status: 0, why: "x" }, refused: null, value: null }, null, 0).state, "failed");
});

test("the page starts a pass as it opens only over an index read more than 10 minutes ago", () => {
  assert.equal(shouldPass(status({ last_pass: 1000 }), 1500), false);
  assert.equal(shouldPass(status({ last_pass: 1000 }), 1700), true);
  assert.equal(shouldPass(status({ last_pass: null }), 1700), true);
  assert.equal(shouldPass(status({ index_version: 0, last_pass: null }), 1700), false);
  const older = status({ index_version: 2, last_pass: null }, [], "the index is at version 2, before 3; run `moxi usage index` to bring it up");
  assert.equal(shouldPass(older, 1700), false);
  assert.equal(shouldPass(null, 1700), false);
});

function sum(values) {
  return values.reduce((a, b) => a + b, 0);
}


// --- the cost terms ---

const term = (cls, tokens, price, extra = {}) => ({ class: cls, tokens, price,
  cost: price === null ? null : tokens * price / 1e6, price_from: "row", unknown: [], ...extra });
const provenance = { layer: "shipped", step: 2, rule: "exact", row: null, snapshot: "2026-10-30",
  overridden: [], period: null, note: null, filled: [], variant: null };
const group = (model, terms, extra = {}) => ({ model, provider: null, tier: null, above: 0, period: null,
  provenance, requests: 1, terms, cost: terms.every((t) => t.cost !== null) ? terms.reduce((a, t) => a + t.cost, 0) : null,
  unpriced_tokens: 0, unknown_rows: 0, rule: null, why: null, unknown: [], ...extra });
const five = (input, read, w5, w1, out, prices = [3, 0.3, 3.75, 6, 15]) =>
  ["input", "cache_read", "cache_write_5m", "cache_write_1h", "output"].map((cls, i) =>
    term(cls, [input, read, w5, w1, out][i], prices[i]));
const termsRow = (model, groups, split = []) => ({ ...sums({ cost: groups.reduce((a, g) => a + (g.cost ?? 0), 0) }),
  model, period: null, agent: null, kind: null, groups, split });

test("a Reconcile cell's title is tokens x price = dollars", () => {
  const cell = termCell(term("cache_read", 98_765_432, 0.8, { price_from: "tier-input" }));
  assert.equal(cell.text, "$79.01");
  assert.equal(cell.title, "cache read: 98,765,432 x $0.8/M = $79.01 (from the tier's input price)");
  assert.equal(termCell(term("output", 10, null)).title, "output: 10 x no price = not priced");
});

test("a model of two groups gets a subtotal equal to their sum, and one of one gets none", () => {
  const fast = group("m-a", five(1000, 0, 0, 0, 100, [6, 0.6, 7.5, 12, 30]), { tier: "fast" });
  const standard = group("m-a", five(2000, 500, 0, 0, 300));
  const other = group("m-b", five(100, 0, 0, 0, 10, [1, 0.1, 1.25, 2, 5]));
  const rows = [termsRow("m-a", [standard, fast]), termsRow("m-b", [other])];
  const lines = reconcileLines(report(rows, { total: sums({ cost: rows[0].cost + rows[1].cost }) }));
  assert.deepEqual(lines.map((line) => [line.kind, line.model]), [
    ["group", "m-a"], ["group", "m-a"], ["subtotal", "m-a total"], ["group", "m-b"], ["total", "Total"]]);
  const [a, b, subtotal, , total] = lines;
  assert.ok(Math.abs(subtotal.cost - (a.cost + b.cost)) < 1e-9);
  assert.equal(subtotal.cells[0].title, "fresh input: 3,000 tokens over 2 groups = $0.01");
  assert.ok(b.pricedBy.includes("tier fast"));
  const groupsOnly = lines.filter((line) => line.kind === "group").reduce((sum_, line) => sum_ + line.cost, 0);
  assert.ok(Math.abs(total.cost - groupsOnly) < 1e-9);
});

test("the class split's bars and keys name class and tier", () => {
  const split = [{ class: "input", tier: null, tokens: 10, cost: 1 }, { class: "output", tier: "fast", tokens: 5, cost: 3 },
                 { class: "output", tier: null, tokens: 5, cost: 1 }];
  assert.deepEqual(splitKeys(split), ["input", "output", "output:fast"]);
  assert.equal(splitLabel("output:fast"), "output, fast");
  const [bar] = splitBars([termsRow("m-a", [], split)], 100);
  assert.equal(bar.total, 5);
  assert.deepEqual(bar.segments.map((seg) => [seg.key, seg.hatch, seg.width]), [
    ["input", false, 20], ["output:fast", true, 60], ["output", false, 20]]);
  assert.match(bar.segments[1].title, /^m-a, output, fast: 5 tokens, \$3\.00, 60%$/);
  assert.deepEqual(classCosts(split).map((item) => [item.key, item.value]), [["input", 1], ["output", 4]]);
  const chart = classChart([{ ...termsRow("m-a", [], split), model: null, period: "2026-10-01" }], 400, 200);
  assert.deepEqual(chart.bars[0].segments.map((seg) => [seg.key, seg.index, seg.hatch]), [
    ["input", 0, false], ["output", 4, false], ["output:fast", 4, true]]);
});

// --- the rates ---

const rate = (extra = {}) => ({ model: "m-a", asked_provider: null, input: 3, output: 15, cache_read: 0.3,
  cache_write_5m: 3.75, cache_write_1h: 6, window: 200000, max_output: 64000, provider: "anthropic", tiers: [],
  provenance: { ...provenance, filled: ["cache_read", "cache_write_5m", "cache_write_1h"] }, unknown: [], ...extra });

test("a tiered model's Tiers cell and a provider default's mark", () => {
  const line = rateLine(rate({ tiers: [{ name: "fast", conditions: { above_tokens: null, service_tier: null, speed: "fast" },
    prices: { input: 30, output: 150, cache_read: 3, cache_write_5m: 37.5, cache_write_1h: 60 },
    from_tier_input: ["cache_read", "cache_write_5m", "cache_write_1h"], from_row: [] }] }));
  assert.deepEqual(line.tiers, ["fast (speed fast): $30 / $150, cache from tier input"]);
  assert.deepEqual(line.cells.map((cell) => [cell.text, cell.mark]), [
    ["$3", null], ["$15", null], ["$0.3", "provider default"], ["$3.75", "provider default"], ["$6", "provider default"]]);
  assert.equal(line.window, "200,000");
  const mine = rateLine(rate({ provenance: { ...provenance, layer: "override", overridden: ["input", "output"],
                                             period: { from: "2026-01-01", to: null } } }));
  assert.deepEqual(mine.cells.slice(0, 2).map((cell) => cell.mark), ["your config", "your config"]);
  assert.equal(mine.period, "2026-01-01 to now");
  assert.equal(tierText({ name: "above_200k", conditions: { above_tokens: 200000, service_tier: null, speed: null },
    prices: { input: 6, output: 22.5 }, from_tier_input: [], from_row: [] }), "above_200k (above 200k): $6 / $22.5");
});

test("the Rates section asks for each model under its groups' provider", () => {
  const rows = [termsRow("m-a", [group("m-a", five(1, 0, 0, 0, 1)), group("m-a", five(1, 0, 0, 0, 1), { provider: "openrouter" })]),
                termsRow("m-b", [group("m-b", five(1, 0, 0, 0, 1))])];
  const { asks, more } = rateAsks(rows);
  assert.deepEqual(asks, [{ model: "m-a", provider: null }, { model: "m-a", provider: "openrouter" }, { model: "m-b", provider: null }]);
  assert.equal(more, 0);
  const query = new URLSearchParams(pricesPath(asks).split("?")[1]);
  assert.equal(query.get("models"), "m-a,m-a,m-b");
  assert.equal(query.get("providers"), ",openrouter,");
});

// --- the requests ---

const req = (n, extra = {}) => ({ n, ts: 1_700_000_000 + n * 60, model: "m-a", tier: null, turn: 1, turn_start: false,
  context: 1000 * n, window: 200000, fresh_input: 100, cache_read: 800, cache_write_5m: 100, cache_write_1h: 0,
  output: 50, unknown: [], synthetic: false, cost: 0.01,
  split: [["input", 0.002], ["cache_read", 0.003], ["cache_write_5m", 0.001], ["cache_write_1h", 0], ["output", 0.004]]
    .map(([cls, cost]) => ({ class: cls, cost })), ...extra });
const total = (extra = {}) => ({ ...sums(), marks: [], turns: 1, claimed_elsewhere: 0, copied_marks: 0, marks_known: true, ...extra });

test("the requests chart draws bars up to 120 requests and an area past them", () => {
  const rows = (count) => Array.from({ length: count }, (_, i) => req(i + 1));
  const bars = requestsChart(rows(BARS_UP_TO), "tokens", 640, 200);
  assert.equal(bars.mode, "bars");
  assert.equal(bars.bars.length, 120);
  const area = requestsChart(rows(121), "tokens", 640, 200);
  assert.equal(area.mode, "area");
  assert.equal(area.bars.length, 0);
  assert.equal(area.areas.length, 5);
});

test("the cost measure stacks each request's split up to its cost", () => {
  const chart = requestsChart([req(1), req(2, { cost: 0.02, split: req(1).split.map((s) => ({ ...s, cost: s.cost * 2 })) })],
                              "cost", 640, 200);
  const y0 = 200 - MARGIN.bottom;
  const [one, two] = chart.bars.map((bar) => bar.segments.reduce((a, seg) => a + seg.value, 0));
  assert.ok(Math.abs(one - 0.01) < 1e-12 && Math.abs(two - 0.02) < 1e-12);
  // the top segment's top is the row's cost on the scale
  const top = chart.bars[1].segments.at(-1).y;
  assert.ok(Math.abs((y0 - top) / (y0 - chart.bars[0].segments.at(-1).y) - 2) < 1e-9);
  const xs = [10, 20];
  assert.equal(areaPath(xs, [100, 100], [80, 60]), "M10,80L20,60L20,100L10,100Z");
});

test("turn and compaction rules sit between the requests", () => {
  const rows = [req(1), req(2), req(3, { turn: 2, turn_start: true }), req(4, { turn: 2 })];
  const axis = requestAxis(4, 4 * 10 + MARGIN.left + MARGIN.right);
  assert.equal(axis.step, 10);
  const rules = requestRules(rows, total({ turns: 2, marks: [{ after: 2, ts: null, trigger: "auto", pre_tokens: 167412 }] }), axis);
  assert.deepEqual(rules.turns, [{ n: 3, x: MARGIN.left + 20 }]);
  assert.equal(rules.compactions[0].x, MARGIN.left + 20);
  assert.equal(rules.compactions[0].label, "compaction (auto)");
  assert.equal(rules.compactions[0].dot, 3);
  assert.match(rules.compactions[0].title, /167,412 before/);
  assert.deepEqual(requestRules(rows, total({ turns: 1 }), axis).turns, []);
});

test("a compaction label goes right of its rule, and left of it at the right edge", () => {
  const rows = Array.from({ length: 10 }, (_, i) => req(i + 1));
  const axis = requestAxis(10, 360);
  const marks = [{ after: 1, ts: null, trigger: "auto", pre_tokens: null },
                 { after: 10, ts: null, trigger: "auto", pre_tokens: null }];
  const [first, last] = requestRules(rows, total({ marks }), axis).compactions;
  assert.equal(first.anchor, "start");
  assert.equal(first.labelX, first.x + 3);
  assert.ok(first.labelX + first.label.length * LABEL_CHAR <= axis.right);
  assert.equal(last.x, axis.right);
  assert.equal(last.anchor, "end");
  assert.equal(last.labelX, last.x - 3);
  assert.ok(last.labelX - last.label.length * LABEL_CHAR >= 0);
});

test("the context line breaks where context is unknown or synthetic", () => {
  assert.equal(linePath([{ x: 1, y: 5 }, { x: 2, y: 6 }, { x: 3, y: null }, { x: 4, y: 7 }]), "M1,5L2,6M4,7");
  const chart = contextChart([req(1), req(2, { context: null }), req(3, { synthetic: true }), req(4)], 640, 200);
  assert.equal(chart.points, 2);
  assert.equal((chart.line.match(/M/g) ?? []).length, 2);
});

test("the window steps where it changes and the y domain reaches it", () => {
  assert.equal(stepPath([{ x0: 0, x1: 10, y: 50 }, { x0: 10, x1: 20, y: 50 }, { x0: 20, x1: 30, y: 20 }, { x0: 30, x1: 40, y: null }]),
               "M0,50H10H20V20H30");
  const chart = contextChart([req(1, { window: 200000 }), req(2, { window: 1_000_000 })], 640, 200);
  assert.deepEqual(chart.windowLabels.map((label) => label.text), ["window 200,000", "window 1,000,000"]);
  // the larger window's line is the top of the chart: its label goes under that line,
  // and the smaller's above its own, both inside the chart
  const [low, high] = chart.windowLabels;
  assert.equal(chart.ticks.at(-1).y, MARGIN.top);
  assert.equal(high.y, MARGIN.top + LABEL_HEIGHT + 2);
  for (const label of chart.windowLabels) {
    assert.ok(label.y - LABEL_HEIGHT >= 0 && label.y <= 200 - MARGIN.bottom);
    assert.equal(label.anchor, "start");
  }
  assert.ok(low.y > high.y);
  const ys = [...chart.window.matchAll(/[MV]-?[\d.]+,?(-?[\d.]+)?/g)].map((m) => m[0]);
  assert.equal(ys.length, 2);
  // the top tick reaches the larger window
  assert.ok(Math.max(...chart.ticks.map((tick) => tick.value)) >= 1_000_000);
  const unknown = contextChart([req(1, { window: null, model: "m-b" })], 640, 200);
  assert.equal(unknown.window, "");
  assert.equal(unknown.unknownWindow, "window unknown for m-b");
});

test("the readout and the notes", () => {
  const line = readout(req(5, { turn: 2, unknown: ["output"] }));
  assert.match(line, /^#5 · .* · turn 2 · m-a · context 5.00k · 2.5% of 200,000 · fresh input 100, .*output \? · \$0\.01$/);
  assert.deepEqual(requestNotes([req(1)], total({ turns: null, claimed_elsewhere: 2, copied_marks: 1, marks_known: false })), [
    "2 requests of this agent are counted under another file",
    "1 compaction copied from another file isn't drawn",
    "compactions aren't read yet for a file of this agent; the next pass that can read it brings them in",
    "this harness wrote no turns here"]);
  assert.equal(requestsPath({ view: "session", session: "s1", agent: "a" }, { ...NO_FILTERS, preset: "all" }, "2026-10-01"),
               "api/requests?session=s1&agent=a");
});

test("an address is served by the view kept from before, until its section is built", () => {
  const session = (agent) => ({ piece: "sessions", harness: "claude", session: "s 1", agent });
  assert.equal(legacyRoute({ piece: "sessions", harness: null, session: null, agent: null }), "sessions");
  assert.equal(legacyRoute(session(null)), "session/s%201");
  assert.equal(legacyRoute(session("main")), "session/s%201");
  assert.equal(legacyRoute(session("a/1")), "session/s%201/a%2F1");
  assert.equal(legacyRoute({ piece: "projects" }), "projects");
  assert.equal(legacyRoute({ piece: "folders" }), "projects");
  assert.equal(legacyRoute({ piece: "models" }), "models");
  assert.equal(legacyRoute({ piece: "kinds" }), "terms");
  assert.equal(legacyRoute({ piece: "prices" }), "terms");
  assert.equal(legacyRoute({ piece: "none" }), null);
  assert.equal(legacyRoute({ piece: "unknown" }), null);
  assert.equal(legacyRoute({ piece: "limit", harness: "claude", limit: "plan", minutes: 300 }), null);
});
