import assert from "node:assert/strict";
import test from "node:test";

import {
  HEAT_DAYS, HEAT_TITLE, heatDraw, heatModel, heatSentence, heatSince, heatStep, heatThresholds,
} from "../src/heat-model.ts";

const TODAY = "2026-11-08";    // a Sunday
const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const day = (period, cost, extra = {}) => sums({ period, model: "m-a", cost, ...extra });
const report = (rows) => ({ today: TODAY, since: null, until: null, tz: "UTC", filters: [], rows,
  total: sums(), unpriced_models: [], rules: [], index: { passed: false, last_pass: 100, note: null } });

test("the heat map holds the 371 days to today, the first a Sunday's week's day", () => {
  assert.equal(heatSince(TODAY), "2025-11-03");
  const model = heatModel(report([day(TODAY, 2)]), TODAY);
  assert.equal(model.days.length, HEAT_DAYS);
  assert.deepEqual([model.days[0].day, model.days.at(-1).day], ["2025-11-03", TODAY]);
  // Nov 3 2025 is a Monday: the first column starts one row down.
  assert.equal(model.offset, 1);
  assert.equal(model.title, HEAT_TITLE);
  assert.equal(HEAT_TITLE, "Daily estimated cost — trailing 53 weeks");
  assert.equal(model.sentence, heatSentence("UTC"));
  assert.equal(model.sentence,
               "Trailing 53 weeks, independent of the range filter · days in UTC. Click a day to filter to it.");
  assert.equal(model.aria, "Estimated cost per day, trailing 53 weeks");
  // With no day used it is drawn all the same, every day unused; a day before the 53
  // weeks is not in them.
  for (const rows of [[], [day("2025-11-02", 4)]]) {
    const empty = heatModel(report(rows), TODAY);
    assert.equal(empty.days.length, HEAT_DAYS);
    assert.ok(empty.days.every((item) => !item.used && item.step === 0));
    assert.deepEqual(empty.table.rows, []);
  }
});

test("five steps on the quantiles of the days that cost something", () => {
  const values = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
  assert.deepEqual(heatThresholds(values), [3, 5, 7, 9]);
  assert.deepEqual(values.map((value) => heatStep(value, heatThresholds(values))), [0, 1, 1, 1, 2, 2, 3, 3, 4, 4, 5]);
  assert.deepEqual(heatThresholds([0, 0]), [0, 0, 0, 0]);
  assert.equal(heatStep(0.5, [0, 0, 0, 0]), 5);
});

test("a used day says its cost, its tokens and its classes; an unused one says so", () => {
  const model = heatModel(report([
    day("2026-11-03", 1.25, { tokens: 2_100, fresh_input: 100, cache_write: 300, cache_read: 1_200, output: 500 }),
    day("2026-11-03", 0.5, { model: "m-b", tokens: 0 }),
    day(TODAY, null, { complete: false, tokens: 10 }),
  ]), TODAY);
  const used = model.days.find((item) => item.day === "2026-11-03");
  assert.deepEqual(used, {
    day: "2026-11-03", step: 1, used: true, aria: "2026-11-03: 1.75 USD",
    hover: "2026-11-03: 1.75 USD, 2.10k tokens\nInput 100\nCache create 300\nCache read 1.20k\nOutput 500",
  });
  assert.deepEqual(model.days.at(-1).aria, "2026-11-08: no price");
  assert.equal(model.days.at(-1).step, 0);
  assert.deepEqual(model.days.at(-2), { day: "2026-11-07", step: 0, used: false, aria: "2026-11-07: 0.00 USD",
                                       hover: "2026-11-07: 0.00 USD" });
  // The table holds the used days, oldest first.
  assert.deepEqual(model.table.columns.map((column) => column.label), ["Day", "Est. cost", "Tokens"]);
  assert.deepEqual(model.table.rows.map((row) => row.cells),
                   [["2026-11-03", "$1.75", "2.10k"], ["2026-11-08", "no price", "10"]]);
});

test("a used day names its classes only when it is in the page's range", () => {
  const rows = [day("2026-11-03", 1.25, { tokens: 2_100, fresh_input: 100, cache_write: 300, cache_read: 1_200, output: 500 })];
  const hover = (range) => heatModel(report(rows), TODAY, range).days.find((item) => item.day === "2026-11-03").hover;
  assert.equal(hover({ kind: "7d" }), "2026-11-03: 1.25 USD, 2.10k tokens\nInput 100\nCache create 300\nCache read 1.20k\nOutput 500");
  assert.equal(hover({ kind: "today" }), "2026-11-03: 1.25 USD, 2.10k tokens");
  assert.equal(hover({ kind: "all" }), hover({ kind: "7d" }));
});

test("laid out: weeks as columns, Sunday on top, 13 px a week whatever the width", () => {
  const model = heatModel(report([day(TODAY, 2)]), TODAY);
  // 371 days from a Monday fill 54 columns.
  const wide = heatDraw(model);
  assert.deepEqual([wide.cell, wide.width, wide.height], [11, 22 + 54 * 13, 14 + 7 * 13]);
  assert.deepEqual([wide.cells[0].x, wide.cells[0].y], [22, 14 + 13]);
  // Today, a Sunday, opens the last column.
  assert.deepEqual([wide.cells.at(-1).x, wide.cells.at(-1).y], [22 + 53 * 13, 14]);
  assert.deepEqual(wide.weekdays.map((weekday) => weekday.text), ["M", "W", "F"]);
  assert.equal(wide.weekdays[0].y, 14 + 13 + 5.5);
  // Each month named once over the column its first day falls in.
  assert.deepEqual(wide.months.map((month) => month.text),
                   ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]);
  assert.equal(wide.months[1].x, 22 + 4 * 13);
  // A month that starts a column after the first day's is named too; only one over the
  // same column as the last name is left out.
  const close = heatDraw(heatModel(report([day("2026-10-25", 2)]), "2026-10-25"));
  assert.deepEqual(close.months.slice(0, 2).map((month) => [month.text, month.x]), [["Oct", 22], ["Nov", 22 + 13]]);
  const same = heatDraw(heatModel(report([day("2026-11-01", 2)]), "2026-11-01"));
  assert.deepEqual(same.months.slice(0, 2).map((month) => month.text), ["Oct", "Dec"]);
});
