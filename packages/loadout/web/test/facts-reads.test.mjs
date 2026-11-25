import assert from "node:assert/strict";
import test from "node:test";

import { FactsReads, READ_EVERY_MS } from "../src/facts-reads.ts";

const GIVEN = JSON.stringify([{ key: "atlas-api", root: "/home/me/code/atlas-api", lead: null, associated: [] },
                              { key: "kestrel-web", root: "/home/me/code/kestrel-web", lead: "kim", associated: [] }]);
const item = (kind, name, scope) => ({ kind, name, scope, harnesses: null, source: "/x", disabled: [] });
const LIST = {
  problem: null, manifest: "/home/me/my-loadout/loadout.toml",
  sections: [{ name: "user", where: "user", root: null, project: null },
             { name: "atlas-api", where: "key", root: null, project: null }],
  items: [item("skill", "release-checklist", "atlas-api"), item("agent", "api-lead", "atlas-api")],
};
const ok = (data = LIST) => ({ status: 200, data });
const ROW = [{ label: "Loadout", lines: [{ parts: [{ text: "skill release-checklist" }, { text: "agent api-lead" }] }],
               link: { text: "Loadout", module: "loadout", route: "project/atlas-api" } }];
const OPENED = { version: 1, order: 80, projects: { "atlas-api": { sections: ROW } } };
const CLOSED = { version: 1, order: 80, projects: {} };

/** A reader whose reads answer `answers` in turn (the last one again once they run out),
 * whose timer is driven by hand, and whose reports are kept. A read can be held open by
 * answering a promise. */
function reader(answers, { projects = GIVEN, open = "atlas-api" } = {}) {
  const reports = [];
  const timers = [];
  let reads = 0;
  const read = async () => {
    const answer = answers[Math.min(reads, answers.length - 1)];
    reads += 1;
    return answer;
  };
  const repeat = (ms, call) => {
    const timer = { ms, call, stopped: false };
    timers.push(timer);
    return () => { timer.stopped = true; };
  };
  const facts = new FactsReads(read, (detail) => reports.push(detail), { repeat, wait: async () => {} });
  facts.setGiven(projects, open);
  return { facts, reports, timers, reads: () => reads, tick: () => timers.filter((t) => !t.stopped).forEach((t) => t.call()) };
}

const settle = () => new Promise((done) => setTimeout(done, 0));

test("start reads once, reports, and reads again every minute while it runs", async () => {
  const { facts, reports, timers, reads, tick } = reader([ok()]);
  assert.deepEqual(reports, [], "nothing before the first read ends");
  await facts.start();
  assert.equal(reads(), 1);
  assert.deepEqual(reports, [OPENED]);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, READ_EVERY_MS);
  assert.equal(READ_EVERY_MS, 60_000);
  assert.ok(facts.running);
  tick();
  await settle();
  assert.equal(reads(), 2);
  assert.deepEqual(reports, [OPENED], "the same answer reports nothing new");
});

test("a later read that changes what it would say reports again", async () => {
  const fewer = { ...LIST, items: [item("skill", "release-checklist", "atlas-api")] };
  const { facts, reports, tick } = reader([ok(), ok(fewer)]);
  await facts.start();
  tick();
  await settle();
  assert.equal(reports.length, 2);
  assert.deepEqual(reports[1].projects["atlas-api"].sections[0].lines[0].parts, [{ text: "skill release-checklist" }]);
});

test("a change of the open key, or of the projects, reports from the last read without reading again", async () => {
  const { facts, reports, reads } = reader([ok()], { open: null });
  await facts.start();
  assert.deepEqual(reports, [CLOSED]);
  facts.setGiven(GIVEN, "atlas-api");
  assert.deepEqual(reports, [CLOSED, OPENED]);
  facts.setGiven(GIVEN, "atlas-api");
  assert.equal(reports.length, 2, "nothing changed, nothing sent");
  facts.setGiven(JSON.stringify([{ key: "kestrel-web", root: "/w/k" }]), "atlas-api");
  assert.deepEqual(reports.at(-1), CLOSED, "the opened key the page no longer gives has no row");
  assert.equal(reads(), 1);
});

test("before the first read ends, a change of the given projects reports nothing", () => {
  const { facts, reports } = reader([ok()]);
  facts.setGiven(GIVEN, "kestrel-web");
  assert.deepEqual(reports, []);
});

for (const [what, answer] of [
  ["a failed read", { status: 500, data: { error: "boom" } }],
  ["no answer", { status: 0, data: null }],
  ["a 404, loadout gone", { status: 404, data: null }],
  ["an answer that isn't the route's shape", ok({ problem: null })],
  ["a list loadout can't read", ok({ problem: "loadout.toml can't be read", manifest: null, sections: [], items: [] })],
]) {
  test(`${what} reports null, and the next good read puts the facts back`, async () => {
    const { facts, reports, tick } = reader([ok(), answer, ok()]);
    await facts.start();
    tick();
    await settle();
    assert.deepEqual(reports, [OPENED, null]);
    tick();
    await settle();
    assert.deepEqual(reports, [OPENED, null, OPENED]);
  });
}

test("a first read that fails reports null at once", async () => {
  const { facts, reports } = reader([{ status: 500, data: null }]);
  await facts.start();
  assert.deepEqual(reports, [null]);
});

test("pause stops the timer; start again reads once and runs one timer", async () => {
  const { facts, timers, reads, tick } = reader([ok()]);
  await facts.start();
  facts.pause();
  assert.ok(!facts.running);
  assert.ok(timers[0].stopped);
  tick();
  await settle();
  assert.equal(reads(), 1, "no read while paused");
  await facts.start();
  assert.equal(reads(), 2, "shown again, it reads once");
  assert.equal(timers.filter((t) => !t.stopped).length, 1);
  await facts.start();
  assert.equal(timers.filter((t) => !t.stopped).length, 1, "a second start starts no second timer");
});

test("close stops the timer, drops a read still running, and starts nothing again", async () => {
  let answer;
  const held = new Promise((done) => { answer = done; });
  const reports = [];
  const timers = [];
  let reads = 0;
  const facts = new FactsReads(() => { reads += 1; return held; }, (detail) => reports.push(detail), {
    repeat: (ms, call) => { const t = { stopped: false }; timers.push(t); return () => { t.stopped = true; }; },
  });
  facts.setGiven(GIVEN, "atlas-api");
  const first = facts.start();
  facts.close();
  answer(ok());
  await first;
  assert.deepEqual(reports, []);
  assert.ok(timers[0].stopped);
  await facts.start();
  assert.equal(reads, 1);
  assert.equal(timers.length, 1);
  facts.setGiven(GIVEN, null);
  assert.deepEqual(reports, []);
});
