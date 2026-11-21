import assert from "node:assert/strict";
import test from "node:test";

import {
  axisWords, BILLABLE_SERIES, chartDraw, chartGroup, chartModel, chartTable, classCosts, COST_SERIES, modelSeries, niceTop,
  NOTHING_USED, tokenCharts,
} from "../src/chart-model.ts";

const TODAY = "2026-11-08";    // a Sunday
const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const day = (period, cost, tokens = 0, extra = {}) => sums({ period, model: "m-a", cost, tokens, ...extra });
const report = (rows) => ({ today: TODAY, since: null, until: null, tz: "UTC", filters: [], rows,
  total: sums(), unpriced_models: [], rules: [], index: { passed: false, last_pass: 100, note: null } });
const MONTH = { kind: "30d" };

/** 22 of the 30 days to today hold a session; today is the dearest. */
const used = ["2026-10-10", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-16", "2026-10-19", "2026-10-20",
              "2026-10-21", "2026-10-22", "2026-10-25", "2026-10-26", "2026-10-27", "2026-10-29", "2026-10-30",
              "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"];
const DAILY = report([...used.map((period) => day(period, 1, 2_376_190)), day(TODAY, 19.54, 22_000_000)]);

test("by cost: the dearest day in words, and how many days had no sessions", () => {
  const chart = chartModel(DAILY, MONTH, "cost", TODAY);
  assert.equal(chart.sentence, "The most in these 30 days was today, $19.54. 8 days had no sessions.");
  assert.equal(chart.aria, "Estimated cost each day, last 30 days");
  assert.deepEqual(chart.top, { value: 20, words: "$20" });
  assert.equal(chart.by, "day");
  assert.equal(chart.bars.length, 30);
  assert.deepEqual(chart.bars[0], { period: "2026-10-10", label: "Oct 10", empty: false, value: 1, share: 0.05,
                                    words: "$1.00", now: false, whole: true, hover: "Oct 10: $1.00", segments: [] });
  assert.deepEqual(chart.bars[1], { period: "2026-10-11", label: "Oct 11", empty: true, value: 0, share: 0,
                                    words: "$0.00", now: false, whole: true, hover: null, segments: [] });
  assert.deepEqual(chart.bars[29], { period: TODAY, label: "today", empty: false, value: 19.54, share: 0.977,
                                     words: "$19.54", now: true, whole: true, hover: "today: $19.54", segments: [] });
  assert.deepEqual([chart.name, chart.caption, chart.series], ["Daily estimated cost — by class", null, []]);
});

test("by tokens: the range's total, then the day with the most", () => {
  const chart = chartModel(DAILY, MONTH, "tokens", TODAY);
  assert.equal(chart.sentence, "71.9M tokens in these 30 days. The most was today, 22.0M.");
  assert.equal(chart.aria, "Tokens each day, last 30 days");
  assert.deepEqual(chart.top, { value: 25_000_000, words: "25M" });
  assert.deepEqual([chart.bars[29].words, chart.bars[29].share, chart.bars[29].hover], ["22.0M", 0.88, "today: 22.0M"]);
});

test("the dearest day is worded against today; the newest of equals is named", () => {
  const sentence = (rows, range = { kind: "7d" }) => chartModel(report(rows), range, "cost", TODAY).sentence;
  assert.equal(sentence([day("2026-11-07", 5), day(TODAY, 1)]),
               "The most in these 7 days was yesterday, $5.00. 5 days had no sessions.");
  assert.equal(sentence([day("2026-11-04", 5), day(TODAY, 1)]),
               "The most in these 7 days was Wed, $5.00. 5 days had no sessions.");
  assert.equal(sentence([day("2026-10-20", 5), day(TODAY, 1)], MONTH),
               "The most in these 30 days was Oct 20, $5.00. 28 days had no sessions.");
  assert.equal(sentence([day("2026-11-03", 5), day("2026-11-05", 5)]),
               "The most in these 7 days was Thu, $5.00. 5 days had no sessions.");
  const full = ["02", "03", "04", "05", "06", "07", "08"].map((d) => day(`2026-11-${d}`, 1));
  assert.equal(sentence(full), "The most in these 7 days was today, $1.00.");
  assert.equal(sentence([...full.slice(1), day("2026-11-03", 9, 0, { model: "m-b" })]),
               "The most in these 7 days was Tue, $10.00. 1 day had no sessions.");
});

test("a day that is not whole says at least, and a range with no price names no dearest day", () => {
  const partly = report([day(TODAY, 3, 10, { complete: false, unpriced_tokens: 5 }), day("2026-11-07", 1, 5)]);
  const chart = chartModel(partly, { kind: "7d" }, "cost", TODAY);
  assert.equal(chart.sentence, "The most in these 7 days was today, at least $3.00. 5 days had no sessions.");
  assert.deepEqual([chart.bars[6].whole, chart.bars[6].hover], [false, "today: at least $3.00, not whole"]);
  assert.equal(chartModel(partly, { kind: "7d" }, "tokens", TODAY).bars[6].whole, true);
  const unpriced = report([day(TODAY, null, 10, { complete: false }), day("2026-11-07", null, 5, { complete: false })]);
  const none = chartModel(unpriced, { kind: "7d" }, "cost", TODAY);
  assert.equal(none.sentence, "5 days had no sessions.");
  assert.equal(none.top, null);
  assert.deepEqual([none.bars[6].share, none.bars[6].words, none.bars[6].hover], [0, "no price", "today: no price, not whole"]);
  const uncounted = report([day(TODAY, 1, 1500, { complete: false, unknown_rows: 2 }), day("2026-11-07", 1, 500)]);
  const tokens = chartModel(uncounted, { kind: "7d" }, "tokens", TODAY);
  assert.equal(tokens.sentence, "At least 2.00k tokens in these 7 days. The most was today, at least 1.50k.");
  assert.equal(tokens.bars[6].hover, "today: at least 1.50k, not whole");
});

test("nothing used in the range is no chart, and one day has a bar and no sentence", () => {
  assert.equal(chartModel(report([]), MONTH, "cost", TODAY), null);
  assert.equal(chartModel(report([day(null, 4)]), MONTH, "cost", TODAY), null);
  assert.equal(NOTHING_USED, "Nothing was used in this range.");
  const one = chartModel(report([day(TODAY, 4)]), { kind: "today" }, "cost", TODAY);
  assert.equal(one.sentence, null);
  assert.deepEqual(one.bars.map((bar) => [bar.label, bar.words, bar.now]), [["today", "$4.00", true]]);
  assert.equal(one.aria, "Estimated cost each day, today");
});

test("a bar a month only for all time and a range over 92 days", () => {
  const rows = [day("2026-08", 12), day("2026-10", 30), day("2026-11", 19.54)];
  const all = chartModel(report(rows), { kind: "all" }, "cost", TODAY);
  assert.equal(all.by, "month");
  assert.deepEqual(all.bars.map((bar) => [bar.label, bar.empty, bar.now]),
                   [["Aug 2026", false, false], ["Sep 2026", true, false], ["Oct 2026", false, false], ["Nov 2026", false, true]]);
  assert.equal(all.sentence, "The most in these 4 months was Oct 2026, $30.00. 1 month had no sessions.");
  assert.equal(all.aria, "Estimated cost each month, all time");
  assert.equal(all.name, "Monthly estimated cost — by class");
  assert.equal(all.bars[2].hover, "Oct 2026: $30.00");
  const long = chartModel(report(rows), { kind: "custom", since: "2026-06-20", until: "2026-10-31" }, "tokens", TODAY);
  assert.deepEqual(long.bars.map((bar) => bar.period), ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]);
  assert.equal(long.aria, "Tokens each month, Jun 20 to Oct 31");
  const turn = chartModel(report([day("2025-12", 1), day("2026-02", 2)]), { kind: "all" }, "cost", TODAY);
  assert.deepEqual(turn.bars.map((bar) => bar.label), ["Dec 2025", "Jan 2026", "Feb 2026"]);
  const days = chartModel(report([day("2026-08-09", 1)]), { kind: "custom", since: "2026-08-09", until: TODAY }, "cost", TODAY);
  assert.deepEqual([days.by, days.bars.length], ["day", 92]);
  assert.equal(chartModel(report([day("2026-10-20", 1)]), { kind: "90d" }, "cost", TODAY).bars.length, 90);
});

test("the axis's top is a round figure at or over the tallest bar", () => {
  assert.deepEqual([19.54, 20, 0.3, 2.2, 7, 130, 22e6, 1].map(niceTop), [20, 20, 0.5, 2.5, 10, 200, 25e6, 1]);
  assert.equal(axisWords(20, "cost"), "$20");
  assert.equal(axisWords(2.5, "cost"), "$2.50");
  assert.equal(axisWords(2000, "cost"), "$2,000");
  assert.equal(axisWords(25e6, "tokens"), "25M");
  assert.equal(axisWords(2500, "tokens"), "2.5k");
  assert.equal(axisWords(500, "tokens"), "500");
});

test("the cost table split by model: Day and a column per model, dearest first, oldest day first", () => {
  const table = chartTable(report([
    day("2026-11-07", 1, 0, { model: "m-b" }), day(TODAY, 2, 0, { model: "m-b" }),
    day(TODAY, 17.54, 0, { model: "m-a" }), day("2026-11-07", 0.5, 0, { model: null }),
  ]), "cost", "day", "model");
  assert.deepEqual(table.columns, [{ label: "Day", num: false }, { label: "m-a", num: true }, { label: "m-b", num: true },
                                   { label: "unknown model", num: true }]);
  assert.deepEqual(table.rows, [{ key: "2026-11-07", cells: ["2026-11-07", "$0.00", "$1.00", "$0.50"] },
                                { key: TODAY, cells: [TODAY, "$17.54", "$2.00", "$0.00"] }]);
});

test("the table has no total column, says why a row is not whole, and puts the rows with no time last", () => {
  const table = chartTable(report([
    day(TODAY, 2, 0, { harness_cost: 1.9 }),
    day("2026-11-07", 1, 0, { complete: false, unpriced_tokens: 1200, unpriced_models: ["mystery-9"] }),
    day("2026-11-07", null, 0, { model: "mystery-9", complete: false }),
    day(null, 0.25),
  ]), "cost", "day", "model");
  assert.deepEqual(table.columns.map((column) => column.label), ["Day", "m-a", "mystery-9", "Not whole"]);
  assert.deepEqual(table.rows.map((row) => row.cells), [
    ["2026-11-07", "$1.00", "no price", "1.20k tokens have no price (mystery-9)"],
    ["2026-11-08", "$2.00", "$0.00", ""],
    ["no time", "$0.25", "$0.00", ""],
  ]);
  assert.equal(table.rows[2].key, "no time");
});

test("the tokens table: a column per token class, by day or by month", () => {
  const rows = [day("2026-11", 1, 1700, { fresh_input: 100, cache_read: 1200, cache_write: 300, output: 100 }),
                day("2026-11", 1, 300, { model: "m-b", fresh_input: 200, output: 100, unknown_rows: 1, complete: false }),
                day("2026-10", null, 50, { output: 50, complete: false })];
  const table = chartTable(report(rows), "tokens", "month");
  assert.deepEqual(table.columns.map((column) => column.label),
                   ["Month", "Input", "Cache read", "Cache create", "Output", "Not whole"]);
  assert.deepEqual(table.rows.map((row) => row.cells), [
    ["Oct 2026", "0", "0", "0", "50", ""],
    ["Nov 2026", "300", "1.20k", "300", "200", "1 request with a token count the harness didn't report"],
  ]);
  assert.deepEqual(chartTable(report([]), "tokens").rows, []);
});

// --- the group and the layout -------------------------------------------------------------

test("the group is the chart with Table beside its sentence, or the table with Chart", () => {
  const drawn = chartGroup(DAILY, MONTH, "cost", TODAY, false);
  assert.deepEqual([drawn.label, drawn.sentence, drawn.toggle, drawn.nothing, drawn.table],
                   ["Each day", "The most in these 30 days was today, $19.54. 8 days had no sessions.", "Table", null, null]);
  assert.equal(drawn.charts.length, 1);
  assert.equal(drawn.charts[0].bars.length, 30);
  const tabled = chartGroup(DAILY, MONTH, "cost", TODAY, true);
  assert.deepEqual([tabled.sentence, tabled.toggle, tabled.charts, tabled.nothing], [drawn.sentence, "Chart", [], null]);
  assert.equal(tabled.table.rows.length, 22);
  assert.equal(chartGroup(report([day("2026-10", 4)]), { kind: "all" }, "cost", TODAY, false).label, "Each month");
});

test("a range that holds nothing says so in the chart's place", () => {
  assert.deepEqual(chartGroup(report([]), MONTH, "cost", TODAY, true),
                   { label: "Each day", sentence: null, toggle: null, nothing: NOTHING_USED, charts: [], table: null });
});

test("rows that all have no time show as their table alone", () => {
  const group = chartGroup(report([day(null, 0.25)]), MONTH, "cost", TODAY, false);
  assert.deepEqual([group.sentence, group.toggle, group.nothing, group.charts], [null, null, null, []]);
  assert.deepEqual(group.table.rows.map((row) => row.cells[0]), ["no time"]);
});

test("the chart at 584 px: a column a day, today's value beside its bar, four labels", () => {
  const draw = chartDraw(chartModel(DAILY, MONTH, "cost", TODAY), 584);
  assert.deepEqual([draw.width, draw.height, draw.axis, draw.labelY], [584, 106, 84.5, 100]);
  assert.deepEqual(draw.grid, { y: 8.5, words: "$20" });
  assert.equal(draw.bars.length, 22);
  assert.deepEqual(draw.bars[0], { period: "2026-10-10", x: 4.2, y: 80.2, width: 11, height: 3.8, now: false,
                                   outline: null, slot: { x: 0, width: 19.5 }, hover: "Oct 10: $1.00", parts: [] });
  const now = draw.bars.at(-1);
  assert.deepEqual([now.period, now.x, now.y, now.height, now.now], [TODAY, 568.8, 9.7, 74.3, true]);
  assert.deepEqual(draw.value, { x: 563.8, y: 18.7, text: "$19.54", anchor: "end" });
  assert.deepEqual(draw.labels, [
    { x: 0, text: "Oct 10", anchor: "start", now: false }, { x: 204.4, text: "Oct 20", anchor: "middle", now: false },
    { x: 438, text: "Nov 1", anchor: "middle", now: false }, { x: 584, text: "today", anchor: "end", now: true }]);
});

test("a narrow chart names fewer days, never two within 56 px", () => {
  const draw = chartDraw(chartModel(DAILY, { kind: "90d" }, "cost", TODAY), 320);
  assert.equal(draw.labels[0].text, "Aug 11");
  assert.equal(draw.labels.at(-1).text, "today");
  const places = draw.labels.map((label) => label.x);
  assert.ok(places.every((x, i) => i === 0 || x - places[i - 1] >= 50), places.join(" "));
  assert.ok(draw.labels.length >= 3 && draw.labels.length <= 6, String(draw.labels.length));
});

test("a short range names every day, and a lone bar has its value at its right", () => {
  const week = chartDraw(chartModel(report([day("2026-11-05", 2), day(TODAY, 1)]), { kind: "7d" }, "cost", TODAY), 584);
  assert.deepEqual(week.labels.map((label) => label.text), ["Nov 2", "Nov 3", "Nov 4", "Nov 5", "Nov 6", "Nov 7", "today"]);
  assert.equal(week.bars[0].width, 28);
  const one = chartDraw(chartModel(report([day(TODAY, 1)]), { kind: "today" }, "cost", TODAY), 300);
  assert.deepEqual(one.labels, [{ x: 150, text: "today", anchor: "middle", now: true }]);
  assert.deepEqual([one.bars[0].x, one.bars[0].width], [136, 28]);
  assert.deepEqual(one.value, { x: 131, y: 17, text: "$1.00", anchor: "end" });
  const left = chartDraw(chartModel(report([day(TODAY, 1)]), { kind: "today" }, "cost", TODAY), 120);
  assert.deepEqual([left.value.x, left.value.anchor], [79, "start"]);
});

test("a period that is not whole has a dashed outline, tall enough to see with no bar", () => {
  const rows = [day("2026-11-07", 4, 0, { complete: false, unpriced_tokens: 1200 }),
                day("2026-11-06", null, 0, { complete: false, unpriced_tokens: 1200 }), day(TODAY, 8)];
  const draw = chartDraw(chartModel(report(rows), { kind: "7d" }, "cost", TODAY), 584);
  const [none, part, whole] = draw.bars;
  assert.deepEqual([none.height, none.outline], [0, { x: 360.9, y: 79.5, width: 29, height: 5 }]);
  assert.equal(none.hover, "Nov 6: no price, not whole");
  assert.deepEqual([part.height, part.outline], [30.4, { x: 444.4, y: 53.1, width: 29, height: 31.4 }]);
  assert.equal(part.hover, "Nov 7: at least $4.00, not whole");
  assert.equal(whole.outline, null);
});

test("by month the axis names the first, the last and each January", () => {
  const rows = ["2025-11", "2025-12", "2026-01", "2026-06", "2026-11"].map((month) => day(month, 3));
  const draw = chartDraw(chartModel(report(rows), { kind: "all" }, "cost", TODAY), 584);
  assert.deepEqual(draw.labels.map((label) => label.text), ["Nov 2025", "Jan 2026", "Nov 2026"]);
  assert.equal(draw.labels.at(-1).now, true);
});

test("beside the label column the first day's name is centred under its bar as the others are", () => {
  const model = chartModel(DAILY, MONTH, "cost", TODAY);
  assert.deepEqual(chartDraw(model, 584, 24).labels[0], { x: 9.7, text: "Oct 10", anchor: "middle", now: false });
  assert.deepEqual(chartDraw(model, 584).labels[0], { x: 0, text: "Oct 10", anchor: "start", now: false });
});

test("today's value stands over its bar where the bar beside it would be under the words", () => {
  const tall = chartDraw(chartModel(report([day("2026-11-01", 9), day("2026-11-07", 1.5), day(TODAY, 1)]),
                                    { kind: "30d" }, "cost", TODAY), 584);
  const now = tall.bars.find((bar) => bar.now);
  assert.deepEqual(tall.value, { x: now.x + now.width, y: now.y - 4, text: "$1.00", anchor: "end" });
  // A day beside it that is no taller than the words' foot leaves them where they were.
  const short = chartDraw(chartModel(report([day("2026-11-07", 1), day(TODAY, 9)]), { kind: "30d" }, "cost", TODAY), 584);
  const tallest = short.bars.find((bar) => bar.now);
  assert.deepEqual([short.value.x, short.value.anchor], [Math.round((tallest.x - 5) * 10) / 10, "end"]);
});

test("today's value stands above the grid line where taller bars are on both sides of its place", () => {
  const rows = [day("2026-11-05", 9), day("2026-11-06", 9), day("2026-11-07", 1), day(TODAY, 1)];
  const draw = chartDraw(chartModel(report(rows), { kind: "30d" }, "cost", TODAY), 320);
  const now = draw.bars.find((bar) => bar.now);
  assert.deepEqual(draw.value, { x: now.x + now.width, y: 4, text: "$1.00", anchor: "end" });
});

// --- the class split and the token charts --------------------------------------------------

/** A terms row by day: its cost terms by class, each `[class, dollars]`. */
const termsDay = (period, split) => sums({ period, model: null, agent: null, kind: null, groups: [],
  split: split.map(([cls, cost]) => ({ class: cls, tier: null, tokens: 1, cost })) });
const TERMS = report([
  termsDay("2026-10-10", [["input", 0.1], ["cache_read", 0.3], ["cache_write_5m", 0.2], ["cache_write_1h", 0.1],
                          ["output", 0.3]]),
  termsDay(TODAY, [["input", 1.54], ["cache_read", 6], ["cache_write_5m", 4], ["output", 8]]),
]);

test("by cost each bar is stacked by token class from the terms, the two cache writes as one", () => {
  const split = classCosts(TERMS);
  assert.deepEqual(split.get("2026-10-10").map((part) => [part.key, Number(part.value.toFixed(6))]),
                   [["input", 0.1], ["cache_write", 0.3], ["cache_read", 0.3], ["output", 0.3]]);
  const chart = chartModel(DAILY, MONTH, "cost", TODAY, TERMS);
  assert.deepEqual(chart.series.map((item) => item.label), ["Input", "Cache create", "Cache read", "Output"]);
  assert.equal(chart.series, COST_SERIES);
  const now = chart.bars[29];
  assert.deepEqual(now.segments, [{ key: "input", value: 1.54 }, { key: "cache_write", value: 4 },
                                  { key: "cache_read", value: 6 }, { key: "output", value: 8 }]);
  // The bar keeps the daily report's figure; the hover gives each class's dollars.
  assert.equal(now.value, 19.54);
  assert.equal(now.hover, "today: $19.54\nInput $1.54\nCache create $4.00\nCache read $6.00\nOutput $8.00");
  // A day the terms hold nothing for is drawn whole.
  assert.deepEqual(chart.bars[2].segments, []);
  assert.equal(chart.sentence, "The most in these 30 days was today, $19.54. 8 days had no sessions.");
  // Until the terms answer, no bar is stacked and there is no legend.
  assert.deepEqual(chartModel(DAILY, MONTH, "cost", TODAY).series, []);
});

test("a stacked bar is laid out bottom first, each part its share of the bar, a pixel apart", () => {
  const draw = chartDraw(chartModel(DAILY, MONTH, "cost", TODAY, TERMS), 584);
  const now = draw.bars.at(-1);
  assert.deepEqual([now.y, now.height], [9.7, 74.3]);
  assert.deepEqual(now.parts.map((part) => part.key), ["input", "cache_write", "cache_read", "output"]);
  assert.deepEqual(now.parts.map((part) => part.shade), [4, 2, 3, 1]);
  // The lowest part stands on the axis line; the top one reaches the bar's top.
  const lowest = now.parts[0];
  assert.equal(Math.round((lowest.y + lowest.height) * 10) / 10, 84);
  assert.equal(now.parts.at(-1).y, 9.7);
  // Output is 8 of 19.54 dollars: its share of the 74.3 px, less the pixel under it.
  assert.ok(Math.abs(now.parts.at(-1).height - (74.3 * 8 / 19.54 - 1)) < 0.1, now.parts);
  // A bar with no parts is drawn whole.
  assert.deepEqual(draw.bars[1].parts, []);
});

test("by tokens: the cache reads alone and the billable work stacked, each with its share", () => {
  const rows = [sums({ period: "2026-11-07", model: "m-a", fresh_input: 100, cache_read: 9_000, cache_write: 400,
                       output: 500, tokens: 10_000 }),
                sums({ period: TODAY, model: "m-a", fresh_input: 200, cache_read: 27_000, cache_write: 1_300,
                       output: 1_500, tokens: 30_000 })];
  const [cache, billable] = tokenCharts(report(rows), { kind: "7d" }, TODAY);
  assert.equal(cache.name, "Cache reads");
  assert.equal(cache.caption, "90% of all tokens in this range. Shown alone because a single stack would be one shade.");
  assert.equal(cache.aria, "Cache read tokens each day, last 7 days");
  assert.deepEqual(cache.series, []);
  assert.deepEqual([cache.bars[6].value, cache.bars[6].words, cache.bars[6].segments], [27_000, "27.0k", []]);
  assert.deepEqual(cache.top, { value: 50_000, words: "50k" });
  assert.equal(billable.name, "Billable work");
  assert.equal(billable.caption, "Input + cache writes + output: the 10% of tokens that is not a cache read.");
  assert.equal(billable.aria, "Billable tokens each day, last 7 days");
  assert.equal(billable.series, BILLABLE_SERIES);
  assert.deepEqual(billable.bars[6].segments, [{ key: "fresh_input", value: 200 }, { key: "cache_write", value: 1_300 },
                                               { key: "output", value: 1_500 }]);
  assert.deepEqual([billable.bars[6].value, billable.bars[5].value], [3_000, 1_000]);
  assert.equal(billable.bars[6].hover, "today: 3.00k\nInput 200\nCache create 1.30k\nOutput 1.50k");
  // The group draws both under the one sentence of all tokens.
  const group = chartGroup(report(rows), { kind: "7d" }, "tokens", TODAY, false);
  assert.deepEqual(group.charts.map((chart) => chart.name), ["Cache reads", "Billable work"]);
  assert.equal(group.sentence, "40.0k tokens in these 7 days. The most was today, 30.0k.");
  assert.equal(tokenCharts(report([]), MONTH, TODAY), null);
});

// --- the split ----------------------------------------------------------------------------

/** Eight models over two days, m-1 the dearest, m-7 and m-8 past the sixth. */
const MODELS = report([
  ...[1, 2, 3, 4, 5, 6, 7, 8].map((n) => day(TODAY, 9 - n, 0, { model: `m-${n}` })),
  day("2026-11-07", 0.5, 0, { model: "m-8" }), day("2026-11-07", 0.5, 0, { model: "m-6" }),
]);

test("split by model: the six dearest models each in a mark of its own, the rest as Other", () => {
  const series = modelSeries(MODELS.rows);
  assert.deepEqual(series.map((item) => [item.label, item.shade]),
                   [["m-1", 1], ["m-2", 2], ["m-3", 3], ["m-4", 4], ["m-5", 5], ["m-6", 6], ["Other", 7]]);
  const chart = chartModel(MODELS, MONTH, "cost", TODAY, TERMS, "model");
  assert.deepEqual(chart.series, series);
  assert.equal(chart.aria, "Estimated cost by model each day, last 30 days");
  assert.deepEqual([chart.name, chart.caption],
                   ["Daily estimated cost — by model", "Per-model dollars, straight from the index."]);
  const now = chart.bars[29];
  assert.deepEqual(now.segments.map((part) => part.value), [8, 7, 6, 5, 4, 3, 3]);
  assert.equal(now.hover.split("\n").at(-1), "Other $3.00");
  assert.deepEqual(chart.bars[28].segments.map((part) => part.value), [0, 0, 0, 0, 0, 0.5, 0.5]);
  // The parts fill the bar, the dearest at its foot.
  const drawn = chartDraw(chart, 584).bars.at(-1);
  assert.deepEqual(drawn.parts.map((part) => part.shade), [1, 2, 3, 4, 5, 6, 7]);
  // By tokens the split is by class whatever it is set to.
  assert.deepEqual(chartModel(MODELS, MONTH, "tokens", TODAY, TERMS, "model").series, []);
});

test("the cost table follows the split: a column per token class, or per model", () => {
  const classes = chartTable(DAILY, "cost", "day", "class", TERMS);
  assert.deepEqual(classes.columns.map((column) => column.label),
                   ["Day", "Input", "Cache create", "Cache read", "Output"]);
  const at = (table, key) => table.rows.find((row) => row.key === key).cells;
  const keys = classes.rows.map((row) => row.key);
  assert.deepEqual(keys, [...keys].sort());
  assert.deepEqual(at(classes, TODAY), [TODAY, "$1.54", "$4.00", "$6.00", "$8.00"]);
  // A day the terms hold nothing for is $0.00 in each class; terms not yet answered leave
  // the classes blank.
  assert.deepEqual(at(classes, "2026-11-07"), ["2026-11-07", "$0.00", "$0.00", "$0.00", "$0.00"]);
  assert.deepEqual(at(chartTable(DAILY, "cost"), TODAY), [TODAY, "", "", "", ""]);
  const models = chartTable(MODELS, "cost", "day", "model");
  assert.deepEqual(models.columns.map((column) => column.label),
                   ["Day", "m-1", "m-2", "m-3", "m-4", "m-5", "m-6", "Other"]);
  assert.deepEqual(at(models, "2026-11-07"), ["2026-11-07", "$0.00", "$0.00", "$0.00", "$0.00", "$0.00", "$0.50", "$0.50"]);
  // The group's table follows it too.
  assert.equal(chartGroup(MODELS, MONTH, "cost", TODAY, true, null, "model").table.columns[1].label, "m-1");
  assert.equal(chartGroup(DAILY, MONTH, "cost", TODAY, true, TERMS).table.columns[1].label, "Input");
});
