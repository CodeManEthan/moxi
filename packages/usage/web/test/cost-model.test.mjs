import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPTION, costRows, costWords, countWords, mostTo, rangeBy, rangeDays, rangeLabel, rangeMenu,
  sessionsRead, sumDays, sumRows, whyNotWhole,
} from "../src/cost-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const TODAY = "2026-11-08";
const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const day = (period, cost, requests = 1, extra = {}) => sums({ period, model: "m-a", cost, requests, ...extra });
const report = (rows, extra = {}) => ({ today: TODAY, since: null, until: null, tz: "UTC", filters: [], rows,
  total: sums(), unpriced_models: [], rules: [], index: { passed: false, last_pass: 100, note: null }, ...extra });

/** Today, the six days before, the 23 before those, and the 60 days before the 30. */
const DAILY = report([
  day("2026-11-08", 15.5, 150), day("2026-11-08", 4.04, 54, { model: "m-b" }),
  day("2026-11-07", 5.24, 40), day("2026-11-03", 18.47, 144),
  day("2026-11-01", 7.67, 90), day("2026-10-28", 5.3, 100), day("2026-10-10", 7.5, 102),
  day("2026-10-09", 6.3, 30), day("2026-09-10", 5, 20),
  day("2026-08-11", 1.25, 9), day("2026-08-10", 99, 9),
]);

test("the three rows are sums of whole days, each with the period before it", () => {
  assert.deepEqual(costRows(DAILY), [
    { key: "today", label: "Today", figure: "$19.54 over 204 requests", why: null,
      before: "yesterday $5.24", beforeWhy: null },
    { key: "7d", label: "7 days", figure: "$43.25 over 388 requests", why: null,
      before: "the week before $12.97", beforeWhy: null },
    { key: "30d", label: "30 days", figure: "$63.72 over 680 requests", why: null,
      before: "the 30 days before $11.30", beforeWhy: null },
  ]);
});

test("a period ends at the report's today and holds exactly its days", () => {
  const edge = (period) => costRows(report([day(period, 1)]));
  assert.deepEqual(edge("2026-11-02").map((row) => row.figure),
                   ["$0.00 over 0 requests", "$1.00 over 1 request", "$1.00 over 1 request"]);
  assert.deepEqual(edge("2026-11-01").map((row) => row.figure),
                   ["$0.00 over 0 requests", "$0.00 over 0 requests", "$1.00 over 1 request"]);
  assert.deepEqual(edge("2026-11-01").map((row) => row.before),
                   ["yesterday $0.00", "the week before $1.00", "the 30 days before $0.00"]);
  assert.deepEqual(edge("2026-10-10").map((row) => row.figure)[2], "$1.00 over 1 request");
  assert.deepEqual(edge("2026-10-09").map((row) => [row.figure, row.before])[2],
                   ["$0.00 over 0 requests", "the 30 days before $1.00"]);
  assert.equal(edge("2026-08-10")[2].before, "the 30 days before $0.00");
  assert.equal(edge("2026-11-09")[0].figure, "$0.00 over 0 requests");
  const moved = costRows(report([day("2026-11-08", 2)], { today: "2026-11-09" }));
  assert.deepEqual([moved[0].figure, moved[0].before], ["$0.00 over 0 requests", "yesterday $2.00"]);
});

test("no rows until the report has answered with its day", () => {
  assert.deepEqual(costRows(null), []);
  assert.deepEqual(costRows(report([day(TODAY, 1)], { today: undefined })), []);
});

test("a sum that is not whole reads at least, and one with no price says so", () => {
  const unpriced = day(TODAY, 2, 3, { complete: false, unpriced_tokens: 12_300, unpriced_models: ["mystery-9"] });
  const [partly] = costRows(report([day(TODAY, 17.54, 5), unpriced]));
  assert.equal(partly.figure, "at least $19.54 over 8 requests");
  assert.equal(partly.why, "12.3k tokens have no price (mystery-9)");
  const [nothing] = costRows(report([day(TODAY, null, 4, { complete: false, unpriced_tokens: 900 })]));
  assert.equal(nothing.figure, "no price over 4 requests");
  assert.equal(nothing.why, "nothing here could be priced");
  const [uncounted] = costRows(report([day(TODAY, 1, 2, { complete: false, unknown_rows: 1 })]));
  assert.equal(uncounted.figure, "at least $1.00 over 2 requests");
  assert.equal(uncounted.why, "1 request with a token count the harness didn't report");
  const [, week] = costRows(report([day("2026-10-30", 3, 1, { complete: false })]));
  assert.deepEqual([week.before, week.beforeWhy], ["the week before at least $3.00", "some of these figures are unknown"]);
  assert.equal(costWords({ cost: 0, complete: true }), "$0.00");
  assert.equal(costWords({ cost: 0.004, complete: true }), "<$0.01");
});

test("sums: priced rows add, rows with no price do not make a zero", () => {
  const sum = sumRows([day(TODAY, 1.5, 2, { tokens: 10, harness_cost: 1.4 }), day(TODAY, null, 1, { tokens: 5, complete: false })]);
  assert.deepEqual([sum.rows, sum.requests, sum.tokens, sum.cost, sum.complete, sum.harness_cost], [2, 3, 15, 1.5, false, 1.4]);
  assert.equal(sumRows([]).cost, 0);
  assert.equal(sumRows([day(TODAY, null)]).cost, null);
  assert.equal(sumDays([day(null, 5), day("2026-11-07", 1)], "2026-11-01", TODAY).cost, 1);
  assert.equal(whyNotWhole(sumRows([day(TODAY, 1)])), null);
  assert.equal(whyNotWhole(sumRows([day(TODAY, null, 1, { complete: false })]), "tokens"), null);
  assert.equal(countWords(1204, "request"), "1,204 requests");
  assert.equal(countWords(1, "day"), "1 day");
});

test("most to: the sessions as named, dearest first, none with no price", () => {
  assert.equal(mostTo([{ name: "planner", cost: 4.21 }, { name: "tool · started today 12:45", cost: 3.67 }]),
               "most to planner $4.21, tool · started today 12:45 $3.67");
  assert.equal(mostTo([{ name: "planner", cost: 4.21 }, { name: "reviewer", cost: null }]), "most to planner $4.21");
  assert.equal(mostTo([{ name: "planner", cost: null }]), null);
  assert.equal(mostTo([]), null);
});

test("the caption, and when sessions were last read", () => {
  assert.equal(CAPTION, "Estimated from a local price table. Not billing data.");
  const at = Date.parse("2026-11-08T19:10:00Z") / 1000;
  assert.equal(sessionsRead(at, TODAY, UTC), "sessions read 19:10");
  assert.equal(sessionsRead(at, "2026-11-09", UTC), "sessions read yesterday 19:10");
  assert.equal(sessionsRead(null, TODAY, UTC), null);
});

test("a range's days, its words, and by month only past 92 days", () => {
  assert.deepEqual(rangeDays({ kind: "today" }, TODAY), { since: TODAY, until: TODAY });
  assert.deepEqual(rangeDays({ kind: "7d" }, TODAY), { since: "2026-11-02", until: TODAY });
  assert.deepEqual(rangeDays({ kind: "30d" }, TODAY), { since: "2026-10-10", until: TODAY });
  assert.deepEqual(rangeDays({ kind: "90d" }, TODAY), { since: "2026-08-11", until: TODAY });
  assert.equal(rangeDays({ kind: "all" }, TODAY), null);
  const custom = (since, until) => ({ kind: "custom", since, until });
  assert.deepEqual(rangeDays(custom("2026-10-10", "2026-11-01"), TODAY), { since: "2026-10-10", until: "2026-11-01" });
  assert.equal(rangeBy({ kind: "90d" }, TODAY), "day");
  assert.equal(rangeBy({ kind: "today" }, TODAY), "day");
  assert.equal(rangeBy(custom("2026-08-09", TODAY), TODAY), "day");      // 92 days
  assert.equal(rangeBy(custom("2026-08-08", TODAY), TODAY), "month");    // 93 days
  assert.equal(rangeBy({ kind: "all" }, TODAY), "month");
  assert.equal(rangeLabel({ kind: "30d" }), "last 30 days");
  assert.equal(rangeLabel({ kind: "today" }), "today");
  assert.equal(rangeLabel({ kind: "all" }), "all time");
  assert.equal(rangeLabel(custom("2026-10-10", "2026-11-01")), "Oct 10 to Nov 1");
  assert.equal(rangeLabel(custom("2026-10-10", "2026-10-10")), "Oct 10");
  assert.equal(rangeLabel(custom("2026-10-10", "2026-11-01"), TODAY), "Oct 10 to Nov 1");
  assert.equal(rangeLabel(custom("2025-12-28", "2026-01-05"), TODAY), "Dec 28 2025 to Jan 5");
  assert.equal(rangeLabel(custom("2025-01-01", "2025-01-01"), TODAY), "Jan 1 2025");
  assert.equal(rangeLabel(custom("2025-02-03", "2025-02-05"), TODAY), "Feb 3 to Feb 5 2025");
  assert.equal(rangeLabel(custom("2024-12-30", "2025-02-05"), TODAY), "Dec 30 2024 to Feb 5 2025");
});

test("the menu: each preset with its figure, the range in force checked", () => {
  const menu = rangeMenu({ kind: "30d" }, DAILY, null);
  assert.deepEqual(menu.map((item) => [item.label, item.figure, item.picked, item.ruled]), [
    ["Today", "$19.54", false, false], ["Last 7 days", "$43.25", false, false],
    ["Last 30 days", "$63.72", true, false], ["Last 90 days", "$76.27", false, false],
    ["From … to …", null, false, true], ["All time", null, false, false],
  ]);
  const all = report([day("2026-08", 180.5)], { total: sums({ cost: 180.5, complete: false }) });
  const later = rangeMenu({ kind: "all" }, DAILY, all);
  assert.deepEqual([later[5].figure, later[5].picked, later[2].picked], ["at least $180.50", true, false]);
  assert.equal(rangeMenu({ kind: "all" }, DAILY, report([], { total: sums({ requests: 0, cost: null }) }))[5].figure, "$0.00");
  assert.equal(rangeMenu({ kind: "custom", since: TODAY, until: TODAY }, DAILY, null)[4].picked, true);
});

test("the menu's figures follow the report it is given, and are absent before it answers", () => {
  assert.deepEqual(rangeMenu({ kind: "30d" }, null, null).map((item) => item.figure), [null, null, null, null, null, null]);
  const filtered = report([day(TODAY, 1.5), day("2026-09-01", null, 1, { complete: false })]);
  assert.deepEqual(rangeMenu({ kind: "7d" }, filtered, null).slice(0, 4).map((item) => item.figure),
                   ["$1.50", "$1.50", "$1.50", "at least $1.50"]);
});

test("a report whose day is not a day has no Cost row and no preset figure", () => {
  for (const today of ["", "not a day", "2026-11-08T06:00:00Z", undefined]) {
    const daily = report(DAILY.rows, { today });
    assert.deepEqual(costRows(daily), []);
    const menu = rangeMenu({ kind: "30d" }, daily, null);
    assert.deepEqual(menu.map((item) => item.figure), [null, null, null, null, null, null]);
    assert.equal(menu.length, 6);
  }
});

test("a preset counted back from a value that is not a day throws nothing and names no day", () => {
  assert.deepEqual(rangeDays({ kind: "30d" }, ""), { since: "", until: "" });
  assert.equal(rangeBy({ kind: "30d" }, ""), "day");
  assert.equal(rangeDays({ kind: "all" }, ""), null);
});
