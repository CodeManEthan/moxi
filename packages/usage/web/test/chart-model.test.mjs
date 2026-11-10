import assert from "node:assert/strict";
import test from "node:test";

import { axisWords, chartModel, chartTable, niceTop, NOTHING_USED } from "../src/chart-model.ts";

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
                                    words: "$1.00", now: false, whole: true, hover: "Oct 10: $1.00" });
  assert.deepEqual(chart.bars[1], { period: "2026-10-11", label: "Oct 11", empty: true, value: 0, share: 0,
                                    words: "$0.00", now: false, whole: true, hover: null });
  assert.deepEqual(chart.bars[29], { period: TODAY, label: "today", empty: false, value: 19.54, share: 0.977,
                                     words: "$19.54", now: true, whole: true, hover: "today: $19.54" });
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
  assert.equal(tokens.sentence, "at least 2.00k tokens in these 7 days. The most was today, at least 1.50k.");
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

test("the cost table: a column per model, dearest first, newest day first", () => {
  const table = chartTable(report([
    day("2026-11-07", 1, 0, { model: "m-b" }), day(TODAY, 2, 0, { model: "m-b" }),
    day(TODAY, 17.54, 0, { model: "m-a" }), day("2026-11-07", 0.5, 0, { model: null }),
  ]), "cost");
  assert.deepEqual(table.columns, [{ label: "Day", num: false }, { label: "m-a", num: true }, { label: "m-b", num: true },
                                   { label: "unknown model", num: true }, { label: "Total", num: true }]);
  assert.deepEqual(table.rows, [{ key: TODAY, cells: ["Nov 8", "$17.54", "$2.00", "", "$19.54"] },
                                { key: "2026-11-07", cells: ["Nov 7", "", "$1.00", "$0.50", "$1.50"] }]);
});

test("the table says the harness's own cost, why a row is not whole, and the rows with no time", () => {
  const table = chartTable(report([
    day(TODAY, 2, 0, { harness_cost: 1.9 }),
    day("2026-11-07", 1, 0, { complete: false, unpriced_tokens: 1200, unpriced_models: ["mystery-9"] }),
    day("2026-11-07", null, 0, { model: "mystery-9", complete: false }),
    day(null, 0.25),
  ]), "cost");
  assert.deepEqual(table.columns.map((column) => column.label), ["Day", "m-a", "mystery-9", "Total", "Not whole"]);
  assert.deepEqual(table.rows.map((row) => row.cells), [
    ["Nov 8", "$2.00", "", "$2.00 · harness $1.90", ""],
    ["Nov 7", "$1.00", "no price", "at least $1.00", "1.20k tokens have no price (mystery-9)"],
    ["no time", "$0.25", "", "$0.25", ""],
  ]);
  assert.equal(table.rows[2].key, "no time");
});

test("the tokens table: a column per token class, by day or by month", () => {
  const rows = [day("2026-11", 1, 1700, { fresh_input: 100, cache_read: 1200, cache_write: 300, output: 100 }),
                day("2026-11", 1, 300, { model: "m-b", fresh_input: 200, output: 100, unknown_rows: 1, complete: false }),
                day("2026-10", null, 50, { output: 50, complete: false })];
  const table = chartTable(report(rows), "tokens", "month");
  assert.deepEqual(table.columns.map((column) => column.label),
                   ["Month", "Fresh input", "Cache read", "Cache write", "Output", "Total", "Not whole"]);
  assert.deepEqual(table.rows.map((row) => row.cells), [
    ["Nov 2026", "300", "1.20k", "300", "200", "2.00k", "1 request with a token count the harness didn't report"],
    ["Oct 2026", "0", "0", "0", "50", "50", ""],
  ]);
  assert.deepEqual(chartTable(report([]), "tokens").rows, []);
});
