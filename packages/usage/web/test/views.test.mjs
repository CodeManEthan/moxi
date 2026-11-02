import assert from "node:assert/strict";
import test from "node:test";

import {
  agents, agentTreemap, cellLabel, classTotals, costDays, costText, dailyChart, filterKeys, flags, fmtAgo,
  fmtCost, fmtPercent, fmtSpan, fmtTokens, heldLine, heldReasons, labelEvery, MARGIN, menuPaths, meter, NO_FILTERS,
  parseRoute, provenanceLine, rangeFor, reportState, segmentTitle, sessionRoute, shiftDay, shouldPass, strip,
  tickText, viewPath,
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
  assert.deepEqual(parseRoute(sessionRoute("ses a/b")), { view: "session", session: "ses a/b" });
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
  const older = strip(up(status({}, [], "the index is at version 2, before 3; run `moxi usage index` to bring it up")), null, 1600);
  assert.match(older.note, /version 2/);
  assert.equal(older.command, "moxi usage index");
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
  assert.equal(shouldPass(null, 1700), false);
});

function sum(values) {
  return values.reduce((a, b) => a + b, 0);
}
