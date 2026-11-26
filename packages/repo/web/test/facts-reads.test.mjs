import assert from "node:assert/strict";
import test from "node:test";

import { FactsReads, READ_EVERY_MS } from "../src/facts-reads.ts";
import { AT, record, repo, UTC, value } from "./fixtures.mjs";

const GIVEN = JSON.stringify([{ key: "atlas-api", root: "/home/jp/code/atlas-api", lead: null, associated: [] }]);
const BROKEN = value({ repos: [repo({ project: "atlas-api", records: [record({ at: AT("17:55") })] })] });
const QUIET = value({ repos: [repo({ project: "atlas-api" })] });
const ok = (data) => ({ status: 200, data: { ...data, now: 1_000 } });
const part = (report) => report?.projects["atlas-api"]?.parts?.[0].text;

function reader(answers, open = "atlas-api") {
  const reports = [];
  const timers = [];
  let reads = 0;
  const read = async () => answers[Math.min(reads++, answers.length - 1)];
  const repeat = (ms, call) => {
    const timer = { ms, call, stopped: false };
    timers.push(timer);
    return () => { timer.stopped = true; };
  };
  const facts = new FactsReads(read, (detail) => reports.push(detail), { repeat, wait: async () => {}, clock: UTC });
  facts.setGiven(GIVEN, open);
  return { facts, reports, timers, reads: () => reads, tick: () => timers.filter((t) => !t.stopped).forEach((t) => t.call()) };
}

const settle = () => new Promise((done) => setTimeout(done, 0));

test("start reads once, reports, and reads again every minute", async () => {
  const { facts, reports, timers, reads, tick } = reader([ok(BROKEN)]);
  await facts.start();
  assert.equal(reads(), 1);
  assert.equal(part(reports[0]), "a git hook broke at 17:55");
  assert.equal(timers[0].ms, READ_EVERY_MS);
  assert.equal(READ_EVERY_MS, 60_000);
  tick();
  await settle();
  assert.equal(reads(), 2);
  assert.equal(reports.length, 1);
});

for (const [kind, failed] of [["a 500", { status: 500, data: null }], ["no answer", { status: 0, data: null }],
                              ["a 404", { status: 404, data: null }], ["a body that isn't repos", { status: 200, data: { now: 1 } }],
                              ["state error", ok(value({ state: "error", detail: "listing failed" }))]]) {
  test(`a read after a good one that comes back ${kind} reports null, and the next good read puts the facts back`, async () => {
    const { facts, reports, tick } = reader([ok(BROKEN), failed, ok(BROKEN)]);
    await facts.start();
    tick();
    await settle();
    assert.equal(reports.at(-1), null);
    tick();
    await settle();
    assert.equal(part(reports.at(-1)), "a git hook broke at 17:55");
  });
}

test("a broken repo mended drops its part at the next read", async () => {
  const { facts, reports, tick } = reader([ok(BROKEN), ok(QUIET)]);
  await facts.start();
  tick();
  await settle();
  assert.deepEqual(reports.at(-1).projects, {});
});

test("a change of the open key reports from the last read without reading again", async () => {
  const { facts, reports, reads } = reader([ok(BROKEN)], null);
  await facts.start();
  assert.equal(reports[0].projects["atlas-api"].sections, undefined);
  facts.setGiven(GIVEN, "atlas-api");
  assert.equal(reads(), 1);
  assert.equal(reports.at(-1).projects["atlas-api"].sections[0].label, "Git hooks");
});

test("pause stops the timer; close drops a read still running", async () => {
  let release;
  const held = new Promise((done) => { release = done; });
  const reports = [];
  const timers = [];
  const facts = new FactsReads(async () => { await held; return ok(BROKEN); }, (detail) => reports.push(detail),
                               { repeat: (ms, call) => { const t = { stopped: false }; timers.push(t); return () => { t.stopped = true; }; } });
  const started = facts.start();
  assert.ok(facts.running);
  facts.pause();
  assert.equal(facts.running, false);
  facts.close();
  release();
  await started;
  assert.deepEqual(reports, []);
});
