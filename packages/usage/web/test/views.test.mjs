import assert from "node:assert/strict";
import test from "node:test";

import {
  costAge, costDays, flags, fmtAgo, fmtCost, fmtPercent, fmtSpan, fmtTokens, meter, pricesPath, provenanceLine,
  rateAsks, rateLine, readout, reconcileLines, requestNotes, shiftDay, termCell, tierText,
} from "../src/views.ts";

const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const report = (rows, extra = {}) => ({ since: null, until: null, tz: "local", filters: [], rows,
  total: sums(), unpriced_models: [], rules: [],
  index: { passed: false, last_pass: 100, note: "no pass ran first; this is the index as it is" }, ...extra });

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

test("dollars, with a dash for nothing priced", () => {
  assert.equal(fmtCost(12.345), "$12.35");
  assert.equal(fmtCost(0), "$0.00");
  assert.equal(fmtCost(0.004), "<$0.01");
  assert.equal(fmtCost(1234.5), "$1,235");
  assert.equal(fmtCost(null), "—");
});

test("a day moves across a month's end", () => {
  assert.equal(shiftDay("2026-03-01", -1), "2026-02-28");
});

// --- flags ---

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

test("a bill by agent names each line by its agent and model, and a subtotal by its agent", () => {
  const main = { ...termsRow(null, [group("m-a", five(1000, 0, 0, 0, 100)), group("m-b", five(10, 0, 0, 0, 1))]),
                 agent: "main", kind: "main" };
  const helper = { ...termsRow(null, [group("m-b", five(100, 0, 0, 0, 10))]), agent: "a-1", kind: "subagent" };
  const lines = reconcileLines(report([main, helper], { total: sums({ cost: main.cost + helper.cost }) }));
  assert.deepEqual(lines.map((line) => [line.kind, line.model]), [
    ["group", "main · m-a"], ["group", "main · m-b"], ["subtotal", "main total"], ["group", "a-1 · m-b"],
    ["total", "Total"]]);
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

test("a request's readout and the notes under its chart", () => {
  const line = readout(req(5, { turn: 2, unknown: ["output"] }));
  assert.match(line, /^#5 · .* · turn 2 · m-a · context 5.00k · 2.5% of 200,000 · fresh input 100, .*output \? · \$0\.01$/);
  assert.deepEqual(requestNotes([req(1)], total({ turns: null, claimed_elsewhere: 2, copied_marks: 1, marks_known: false })), [
    "2 requests of this agent are counted under another file",
    "1 compaction copied from another file isn't drawn",
    "compactions aren't read yet for a file of this agent; the next pass that can read it brings them in",
    "this harness wrote no turns here"]);
});
