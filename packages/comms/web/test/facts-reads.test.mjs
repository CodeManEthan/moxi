import assert from "node:assert/strict";
import test from "node:test";

import { FactsReads, TOPIC } from "../src/facts-reads.ts";

const GIVEN = JSON.stringify([{ key: "atlas-api", root: "/w/a", lead: "builder-1", associated: ["planner"] }]);
const row = (name, live) => ({ qualified: name, name, scope: null, live, directors: [], policy_directors: [], awaiting: [] });
const up = (rows) => ({ status: 200, data: { state: "up", detail: null, rows, today: "2026-11-08", now: 1_000 } });
const NOT = { runs: false, state: "not running now" };
const said = (report) => report?.projects["atlas-api"]?.agents;

/** A reader whose reads answer `answers` in turn (the last again once they run out), whose
 * topic is driven by hand, and whose reports are kept. */
function reader(answers, open = "atlas-api") {
  const reports = [];
  const follows = [];
  let reads = 0;
  const read = async () => answers[Math.min(reads++, answers.length - 1)];
  const follow = (topic, listen) => {
    const one = { topic, listen, stopped: false };
    follows.push(one);
    return () => { one.stopped = true; };
  };
  const facts = new FactsReads(read, follow, (detail) => reports.push(detail), { wait: async () => {} });
  facts.setGiven(GIVEN, open);
  return { facts, reports, follows, reads: () => reads, event: (data) => follows.filter((f) => !f.stopped).forEach((f) => f.listen(data)) };
}

test("start reads the roster, reports, then follows its topic", async () => {
  const { facts, reports, follows, reads } = reader([up([row("builder-1", true)])]);
  await facts.start();
  assert.equal(reads(), 1);
  assert.deepEqual(reports.map(said), [{ planner: NOT }]);
  assert.equal(follows.length, 1);
  assert.equal(follows[0].topic, TOPIC);
  assert.equal(TOPIC, "roster");
  assert.ok(facts.following);
});

test("a topic event that changes the roster reports again; one that doesn't sends nothing", async () => {
  const { facts, reports, event } = reader([up([])]);
  await facts.start();
  assert.deepEqual(said(reports[0]), { "builder-1": NOT, planner: NOT });
  event({ state: "up", detail: null, rows: [row("planner", true)] });
  assert.deepEqual(said(reports.at(-1)), { "builder-1": NOT });
  event({ state: "up", detail: null, rows: [row("planner", true)] });
  assert.equal(reports.length, 2);
});

for (const [kind, failed] of [["a 500", { status: 500, data: null }], ["no answer", { status: 0, data: null }],
                              ["a 404", { status: 404, data: null }],
                              ["offline", { status: 200, data: { state: "offline", detail: "down", now: 1 } }],
                              ["error", { status: 200, data: { state: "error", detail: "no", now: 1 } }]]) {
  test(`a read after a good one that comes back ${kind} reports null, and the next good read puts the facts back`, async () => {
    const { facts, reports } = reader([up([]), failed, up([])]);
    await facts.start();
    assert.deepEqual(said(reports[0]), { "builder-1": NOT, planner: NOT });
    facts.pause();
    await facts.start();
    assert.equal(reports.at(-1), null);
    facts.pause();
    await facts.start();
    assert.deepEqual(said(reports.at(-1)), { "builder-1": NOT, planner: NOT });
  });
}

test("an offline event on the topic reports null at once", async () => {
  const { facts, reports, event } = reader([up([])]);
  await facts.start();
  event({ state: "offline", detail: "down", rows: [] });
  assert.equal(reports.at(-1), null);
});

test("a change of the open key reports from the last read without reading again", async () => {
  const { facts, reports, reads } = reader([up([])], null);
  await facts.start();
  assert.deepEqual(reports.at(-1).projects, {});
  facts.setGiven(GIVEN, "atlas-api");
  assert.equal(reads(), 1);
  assert.deepEqual(said(reports.at(-1)), { "builder-1": NOT, planner: NOT });
});

test("pause stops following; a start after it reads once and follows again", async () => {
  const { facts, follows, reads } = reader([up([])]);
  await facts.start();
  facts.pause();
  assert.ok(follows[0].stopped);
  assert.equal(facts.following, false);
  await facts.start();
  assert.equal(reads(), 2);
  assert.equal(follows.filter((f) => !f.stopped).length, 1);
});

test("close drops a read still running and follows nothing", async () => {
  let release;
  const held = new Promise((done) => { release = done; });
  const reports = [];
  const follows = [];
  const facts = new FactsReads(async () => { await held; return up([]); }, (topic) => { follows.push(topic); return () => {}; },
                               (detail) => reports.push(detail));
  facts.setGiven(GIVEN, "atlas-api");
  const started = facts.start();
  facts.close();
  release();
  await started;
  assert.deepEqual(reports, []);
  assert.deepEqual(follows, []);
});

test("a pause while the first read runs follows nothing once it ends", async () => {
  let release;
  const held = new Promise((done) => { release = done; });
  const follows = [];
  const facts = new FactsReads(async () => { await held; return up([]); }, (topic) => { follows.push(topic); return () => {}; },
                               () => {});
  const started = facts.start();
  facts.pause();
  release();
  await started;
  assert.deepEqual(follows, []);
});
