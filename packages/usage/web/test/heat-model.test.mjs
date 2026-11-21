import assert from "node:assert/strict";
import test from "node:test";

import {
  HEAT_DAYS, HEAT_SENTENCE, heatDraw, heatModel, heatSince, heatStep, heatThresholds,
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
  assert.equal(model.sentence, HEAT_SENTENCE);
  assert.equal(model.aria, "Estimated cost each day, last 53 weeks");
  assert.equal(heatModel(report([]), TODAY), null);
  // A day before the 53 weeks is not in them.
  assert.equal(heatModel(report([day("2025-11-02", 4)]), TODAY), null);
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
    day: "2026-11-03", step: 1, used: true, aria: "Nov 3 2026: $1.75",
    hover: "Nov 3 2026: $1.75, 2.10k tokens\nInput 100\nCache create 300\nCache read 1.20k\nOutput 500",
  });
  assert.deepEqual(model.days.at(-1).aria, "Nov 8 2026: no price");
  assert.equal(model.days.at(-1).step, 0);
  assert.deepEqual(model.days.at(-2), { day: "2026-11-07", step: 0, used: false, aria: "Nov 7 2026: nothing used",
                                       hover: "Nov 7 2026: nothing used" });
  // The table holds the used days, newest first.
  assert.deepEqual(model.table.columns.map((column) => column.label), ["Day", "Est. cost", "Tokens"]);
  assert.deepEqual(model.table.rows.map((row) => row.cells),
                   [["Nov 8 2026", "no price", "10"], ["Nov 3 2026", "$1.75", "2.10k"]]);
});

test("laid out: weeks as columns, Sunday on top, as wide a pitch as fits from 9 to 13 px", () => {
  const model = heatModel(report([day(TODAY, 2)]), TODAY);
  // 371 days from a Monday fill 54 columns.
  const wide = heatDraw(model, 800);
  assert.deepEqual([wide.cell, wide.width, wide.height], [11, 22 + 54 * 13, 14 + 7 * 13]);
  assert.deepEqual([wide.cells[0].x, wide.cells[0].y], [22, 14 + 13]);
  // Today, a Sunday, opens the last column.
  assert.deepEqual([wide.cells.at(-1).x, wide.cells.at(-1).y], [22 + 53 * 13, 14]);
  const fitted = heatDraw(model, 580);
  assert.deepEqual([fitted.cell, fitted.width], [8, 22 + 54 * 10]);
  const phone = heatDraw(model, 340);
  assert.deepEqual([phone.cell, phone.width], [7, 22 + 54 * 9]);
  assert.deepEqual(wide.weekdays.map((weekday) => weekday.text), ["M", "W", "F"]);
  assert.equal(wide.weekdays[0].y, 14 + 13 + 5.5);
  // Each month named once over the column its first day falls in, none within three columns of the last.
  assert.deepEqual(wide.months.map((month) => month.text),
                   ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]);
  assert.equal(wide.months[1].x, 22 + 4 * 13);
});
