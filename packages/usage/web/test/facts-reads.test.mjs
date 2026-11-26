import assert from "node:assert/strict";
import test from "node:test";

import { FactsReads, READ_EVERY_MS, sincePath } from "../src/facts-reads.ts";

const GIVEN = JSON.stringify([{ key: "atlas-api", root: "/home/me/code/atlas-api", lead: null, associated: [] },
                              { key: "kestrel-web", root: "/home/me/code/kestrel-web", lead: "kim", associated: [] }]);
const DAY = "2026-11-08";

const row = (project, cost, cwd = null) => ({
  project, cwd, models: [], requests: 1, input: 1, fresh_input: 1, cache_read: 0, cache_write: 0, cache_write_1h: 0,
  output: 1, tokens: 2, unpriced_tokens: 0, unknown_rows: 0, cost, complete: true, harness_cost: null, unknown: [],
  unpriced_models: [], rules: [],
});
const body = (rows, today = DAY) => ({
  today, since: null, until: null, tz: "local", filters: [], rows, total: row(null, 0), unpriced_models: [],
  rules: [], index: { passed: true, last_pass: null, note: null }, by: "project", home: "/home/me",
});
const ok = (rows, today) => ({ status: 200, data: body(rows, today) });
/** Each read's answer by the day it counts from. */
const ANSWERS = {
  [DAY]: ok([row("atlas-api", 8.49)]),
  "2026-11-02": ok([row("atlas-api", 15.84), row(null, 0.82, "/home/me/code/finch-scripts")]),
  "2026-10-10": ok([row("atlas-api", 21.49)]),
};
const sinceOf = (path) => new URLSearchParams(path.split("?")[1]).get("since");

/** A reader whose reads answer by the day they count from (`answers`, or a function of the
 * day and the read's number), whose timer is driven by hand, and whose reports are kept. */
function reader(answers = ANSWERS, { projects = GIVEN, open = null, today = DAY } = {}) {
  const reports = [];
  const timers = [];
  const asked = [];
  const read = async (path) => {
    asked.push(path);
    const since = sinceOf(path);
    return typeof answers === "function" ? answers(since, asked.length) : answers[since] ?? { status: 500, data: null };
  };
  const repeat = (ms, call) => {
    const timer = { ms, call, stopped: false };
    timers.push(timer);
    return () => { timer.stopped = true; };
  };
  const facts = new FactsReads(read, (detail) => reports.push(detail), { repeat, wait: async () => {}, today: () => today });
  facts.setGiven(projects, open);
  return { facts, reports, timers, asked, tick: () => timers.filter((t) => !t.stopped).forEach((t) => t.call()) };
}

const settle = () => new Promise((done) => setTimeout(done, 0));
const costLine = (report, key = "atlas-api") => report.projects[key]?.sections?.[0].lines[0].parts.map((p) => p.text);

test("start reads today and the last 7 days, reports once both end, and reads again every minute", async () => {
  const { facts, reports, timers, asked, tick } = reader();
  assert.deepEqual(reports, []);
  await facts.start();
  assert.deepEqual(asked.map(sinceOf).sort(), ["2026-11-02", DAY]);
  assert.equal(asked[0], sincePath(asked.map(sinceOf)[0]));
  assert.ok(asked.every((path) => path.startsWith("api/projects?since=")));
  assert.equal(reports.length, 1);
  assert.deepEqual(reports[0].projects["atlas-api"].parts, [{ text: "$8.49 today" }]);
  assert.equal(reports[0].folders[0].folder, "/home/me/code/finch-scripts");
  assert.equal(timers[0].ms, READ_EVERY_MS);
  assert.equal(READ_EVERY_MS, 60_000);
  tick();
  await settle();
  assert.equal(asked.length, 4);
  assert.equal(reports.length, 1, "the same answers report nothing new");
});

test("an open project is read from 30 days back too, at once when it opens and every minute", async () => {
  const { facts, reports, asked, tick } = reader();
  await facts.start();
  facts.setGiven(GIVEN, "atlas-api");
  await settle();
  assert.equal(asked.filter((path) => sinceOf(path) === "2026-10-10").length, 1);
  assert.deepEqual(costLine(reports.at(-1)), ["$8.49 today", "$15.84 in 7 days", "$21.49 in 30 days", "an estimate"]);
  tick();
  await settle();
  assert.equal(asked.filter((path) => sinceOf(path) === "2026-10-10").length, 2);
  // Closed again: the 30-day read stops and its figure goes with it.
  facts.setGiven(GIVEN, null);
  tick();
  await settle();
  assert.equal(asked.filter((path) => sinceOf(path) === "2026-10-10").length, 2);
  facts.setGiven(GIVEN, "atlas-api");
  assert.deepEqual(costLine(reports.at(-1)), ["$8.49 today", "$15.84 in 7 days", "an estimate"],
                   "no 30-day figure from a read made before it closed");
});

test("open at the start reads all three at once", async () => {
  const { facts, asked, reports } = reader(ANSWERS, { open: "atlas-api" });
  await facts.start();
  assert.deepEqual(asked.map(sinceOf).sort(), ["2026-10-10", "2026-11-02", DAY]);
  assert.equal(costLine(reports.at(-1)).length, 4);
});

for (const [name, since, gone] of [
  ["today's", DAY, (r) => r.projects["atlas-api"].parts === undefined && costLine(r)[0] === "$15.84 in 7 days"],
  ["the week's", "2026-11-02", (r) => !("folders" in r) && !costLine(r).includes("$15.84 in 7 days")],
  ["the 30 days'", "2026-10-10", (r) => !costLine(r).includes("$21.49 in 30 days")],
]) {
  for (const [kind, failed] of [["a 500", { status: 500, data: null }], ["no answer", { status: 0, data: null }],
                                ["a 404", { status: 404, data: null }], ["a 422", { status: 422, data: { error: "no" } }],
                                ["a body that isn't a report", { status: 200, data: { rows: 3 } }]]) {
    test(`${name} read failing (${kind}) after a good one drops what it fed, and the next good read puts it back`, async () => {
      let failing = false;
      const answers = (day) => (failing && day === since ? failed : ANSWERS[day]);
      const { facts, reports, tick } = reader(answers, { open: "atlas-api" });
      await facts.start();
      const whole = reports.at(-1);
      assert.equal(costLine(whole).length, 4);
      failing = true;
      tick();
      await settle();
      assert.ok(gone(reports.at(-1)), JSON.stringify(reports.at(-1)));
      assert.deepEqual(reports.at(-1).projects["atlas-api"].stays, ["what they cost, still listed under atlas-api on Usage"]);
      failing = false;
      tick();
      await settle();
      assert.deepEqual(reports.at(-1), whole);
    });
  }
}

test("every read failing reports null, and the next good reads put the facts back", async () => {
  let failing = false;
  const { facts, reports, tick } = reader((day) => (failing ? { status: 500, data: null } : ANSWERS[day]),
                                          { open: "atlas-api" });
  await facts.start();
  failing = true;
  tick();
  await settle();
  assert.equal(reports.at(-1), null);
  failing = false;
  tick();
  await settle();
  assert.equal(costLine(reports.at(-1)).length, 4);
});

test("a first read that fails reports null", async () => {
  const { facts, reports } = reader(() => ({ status: 500, data: null }));
  await facts.start();
  assert.deepEqual(reports, [null]);
});

test("the day counts from the newest an answer carried", async () => {
  const { facts, asked, tick } = reader((day) => ok([], "2026-11-09"), { today: DAY });
  await facts.start();
  tick();
  await settle();
  assert.deepEqual(asked.slice(-2).map(sinceOf).sort(), ["2026-11-03", "2026-11-09"]);
});

test("a change of the projects reports from the last reads without reading again", async () => {
  const { facts, reports, asked } = reader();
  await facts.start();
  const count = asked.length;
  facts.setGiven(JSON.stringify([{ key: "kestrel-web" }]), null);
  assert.equal(asked.length, count);
  assert.deepEqual(reports.at(-1).projects, {});
});

test("pause stops the timer; close drops a read still running", async () => {
  let release;
  const held = new Promise((done) => { release = done; });
  const { facts, reports, timers } = reader(async (day) => { await held; return ANSWERS[day]; });
  const started = facts.start();
  assert.ok(facts.running);
  facts.close();
  assert.equal(facts.running, false);
  assert.ok(timers[0].stopped);
  release();
  await started;
  assert.deepEqual(reports, []);
  await facts.start();
  assert.deepEqual(reports, [], "nothing more after close");
});
