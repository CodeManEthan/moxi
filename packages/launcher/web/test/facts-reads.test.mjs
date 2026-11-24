import assert from "node:assert/strict";
import test from "node:test";

import { EVERY_MS, FactsReads, RUNNING_TOPIC } from "../src/facts-reads.ts";

const UP = { state: "up", detail: null };
const entry = (request) => ({
  request_id: request, harness: "claude", session_id: `${request}-sid`, name: request, identity: null,
  where: { target: "headless", slot: null, project_key: "proj", folder: "proj" }, started_at: "2026-11-19T18:00:00Z",
  cache: null, waits: null, covered: false, coverable: null, activity: null,
});
const runningOf = (...requests) => ({ ...UP, count: requests.length, sessions: requests.map(entry) });
const ANSWERS = {
  "api/running": runningOf("r1"),
  "api/presets": { ...UP, presets: [], problems: [] },
  "api/catalog": { ...UP, harnesses: [] },
  "api/last?project=proj": { ...UP, at: "2026-11-19T18:45:00Z", harness: "claude", values: [] },
  "api/name?project=proj": { ...UP, name: "proj-x", template: "<project>-x", from: "project", project: "proj",
                             refused: null, save: null },
  "api/last?project=other": { ...UP, at: null, harness: null, values: [] },
  "api/name?project=other": { ...UP, name: "other-x", template: "<project>-x", from: "global", project: null,
                              refused: null, save: null },
};

/** A shell whose reads answer from `answers` (a path's own answer, else `ANSWERS`'), or wait
 * while the path is held; it records every read, subscription and timer. */
function fakeShell(answers = {}) {
  const shell = {
    asked: [], topics: [], timers: [], held: new Map(), answers: { ...ANSWERS, ...answers }, page: null,
    read(path) {
      shell.asked.push(path);
      if (shell.held.has(path)) return new Promise((done) => shell.held.get(path).push(done));
      const answer = shell.answers[path];
      return Promise.resolve(typeof answer === "number" ? { status: answer, data: null } : { status: 200, data: answer });
    },
    subscribe(topic, fn) {
      const sub = { topic, fn, live: true };
      shell.topics.push(sub);
      return () => { sub.live = false; };
    },
    every(ms, fn) {
      const timer = { ms, fn, live: true };
      shell.timers.push(timer);
      return () => { timer.live = false; };
    },
    hold(path) {
      shell.held.set(path, []);
    },
    release(path, data) {
      for (const done of shell.held.get(path) ?? []) done({ status: 200, data });
      shell.held.delete(path);
    },
    turns() {
      return shell.page;
    },
  };
  return shell;
}

/** turns' session list with one session per `[session id, turn open]`. */
const liveOf = (...pairs) => ({ listed: true, detail: null, home: "/home/you", reach: null, sessions: pairs.map(
  ([sid, open]) => ({ key: `claude-${sid}`, harness: "claude", session_id: sid,
                      facts: { session_id: sid, turn_number: 1, turn_open: open, agents: 0 } })) });

/** turns' page: `answer` is what `api/sessions` answers (a status, or a value), or held
 * while `held` is set; it records every read and subscription. */
function fakeTurns(answer = liveOf(["r1-sid", true])) {
  const page = {
    asked: [], topics: [], answer, held: null,
    read(path) {
      page.asked.push(path);
      if (page.held !== null) return new Promise((done) => page.held.push(done));
      return Promise.resolve(typeof page.answer === "number" ? { status: page.answer, data: null }
                                                             : { status: 200, data: page.answer });
    },
    subscribe(topic, fn) {
      const sub = { topic, fn, live: true };
      page.topics.push(sub);
      return () => { sub.live = false; };
    },
  };
  return page;
}

/** Started as `started` is, with turns' page given. */
function startedWithTurns(page = fakeTurns(), open = "proj") {
  const shell = fakeShell();
  shell.page = page;
  let changes = 0;
  const reads = new FactsReads(shell, () => { changes += 1; });
  reads.setOpen(open);
  reads.start();
  return { shell, page, reads, changes: () => changes };
}

const settle = () => new Promise((done) => setTimeout(done, 0));

function started(answers = {}, open = "proj") {
  const shell = fakeShell(answers);
  let changes = 0;
  const reads = new FactsReads(shell, () => { changes += 1; });
  reads.setOpen(open);
  reads.start();
  return { shell, reads, changes: () => changes };
}

test("started, it reads the three routes and the opened project's two, follows `running` and reads every 30 s", async () => {
  const { shell, reads } = started();
  await settle();
  assert.deepEqual(shell.asked.sort(), ["api/catalog", "api/last?project=proj", "api/name?project=proj",
                                        "api/presets", "api/running"]);
  assert.deepEqual(shell.topics.map((sub) => sub.topic), [RUNNING_TOPIC]);
  assert.equal(RUNNING_TOPIC, "running");
  assert.deepEqual(shell.timers.map((timer) => timer.ms), [EVERY_MS]);
  assert.equal(EVERY_MS, 30_000);
  const got = reads.reads();
  assert.equal(got.running.sessions.length, 1);
  assert.equal(got.presets.state, "up");
  assert.equal(got.catalog.state, "up");
  assert.equal(got.last.at, "2026-11-19T18:45:00Z");
  assert.equal(got.name.name, "proj-x");
});

test("with no project opened it reads neither `/api/last` nor `/api/name`", async () => {
  const { shell, reads } = started({}, null);
  await settle();
  assert.deepEqual(shell.asked.sort(), ["api/catalog", "api/presets", "api/running"]);
  assert.equal(reads.reads().last, null);
  assert.equal(reads.reads().name, null);
});

test("each 30 s tick reads all five routes again", async () => {
  const { shell } = started();
  await settle();
  shell.asked.length = 0;
  shell.timers[0].fn();
  await settle();
  assert.deepEqual(shell.asked.sort(), ["api/catalog", "api/last?project=proj", "api/name?project=proj",
                                        "api/presets", "api/running"]);
});

test("a `running` event replaces the running launches without a read", async () => {
  const { shell, reads } = started();
  await settle();
  shell.asked.length = 0;
  shell.topics[0].fn(runningOf("r1", "r2"));
  assert.deepEqual(reads.reads().running.sessions.map((found) => found.request_id), ["r1", "r2"]);
  assert.deepEqual(shell.asked, []);
});

test("opening another project reads its two routes; a late answer for the one before is dropped", async () => {
  const { shell, reads, changes } = started();
  await settle();
  shell.hold("api/last?project=proj");
  shell.timers[0].fn();
  await settle();
  const before = changes();
  shell.asked.length = 0;
  reads.setOpen("other");
  assert.equal(changes(), before + 1);
  assert.equal(reads.reads().last, null);
  await settle();
  assert.deepEqual(shell.asked.sort(), ["api/last?project=other", "api/name?project=other"]);
  assert.equal(reads.open, "other");
  assert.equal(reads.reads().name.from, "global");
  shell.release("api/last?project=proj", ANSWERS["api/last?project=proj"]);
  await settle();
  assert.equal(reads.reads().last.at, null);
});

test("closing the opened project drops its facts at once", async () => {
  const { reads } = started();
  await settle();
  reads.setOpen(null);
  assert.equal(reads.open, null);
  assert.equal(reads.reads().last, null);
  assert.equal(reads.reads().name, null);
  assert.notEqual(reads.reads().running, null);
});

test("a failed read drops that route's value, and only it; the next good read puts it back", async () => {
  const { shell, reads } = started();
  await settle();
  shell.answers["api/presets"] = 500;
  shell.timers[0].fn();
  await settle();
  const got = reads.reads();
  assert.equal(got.presets, null);
  assert.ok(got.running !== null && got.catalog !== null && got.last !== null && got.name !== null);
  shell.answers["api/presets"] = ANSWERS["api/presets"];
  shell.timers[0].fn();
  await settle();
  assert.notEqual(reads.reads().presets, null);
});

test("an answer whose `state` isn't `up` drops the route's value; so does a 404 or an answer that won't read", async () => {
  const { reads } = started({ "api/running": { state: "error", detail: "x", count: 0, sessions: [] },
                              "api/catalog": 404, "api/last?project=proj": { nonsense: true },
                              "api/name?project=proj": { state: "error", detail: "x" } });
  await settle();
  const got = reads.reads();
  assert.deepEqual([got.running, got.catalog, got.last, got.name], [null, null, null, null]);
  assert.notEqual(got.presets, null);
});

test("paused, it stops the topic and the timer and keeps its values; started again it reads once more", async () => {
  const { shell, reads } = started();
  await settle();
  reads.pause();
  assert.equal(reads.following, false);
  assert.deepEqual(shell.topics.map((sub) => sub.live), [false]);
  assert.deepEqual(shell.timers.map((timer) => timer.live), [false]);
  assert.notEqual(reads.reads().running, null);
  shell.asked.length = 0;
  reads.setOpen("other");
  await settle();
  assert.deepEqual(shell.asked, []);
  reads.start();
  await settle();
  assert.deepEqual(shell.asked.sort(), ["api/catalog", "api/last?project=other", "api/name?project=other",
                                        "api/presets", "api/running"]);
  assert.deepEqual(shell.topics.map((sub) => sub.live), [false, true]);
});

test("closed, it stops everything and a later answer changes nothing", async () => {
  const shell = fakeShell();
  shell.hold("api/running");
  let changes = 0;
  const reads = new FactsReads(shell, () => { changes += 1; });
  reads.start();
  await settle();
  const before = changes;
  reads.close();
  assert.equal(reads.following, false);
  shell.release("api/running", runningOf("r1"));
  await settle();
  assert.equal(changes, before);
  reads.start();
  assert.equal(reads.following, false);
});

test("`read` stays false until a read has finished", async () => {
  const shell = fakeShell();
  for (const path of ["api/running", "api/presets", "api/catalog"]) shell.hold(path);
  const reads = new FactsReads(shell, () => {});
  reads.start();
  await settle();
  assert.equal(reads.read, false);
  shell.release("api/catalog", ANSWERS["api/catalog"]);
  await settle();
  assert.equal(reads.read, true);
});

// -- turns' list ---------------------------------------------------------------------------------

const workingIds = (live) => live.sessions.filter((one) => one.turn_open).map((one) => one.session_id);

test("without turns' page it reads nothing of turns, and says nothing of it", async () => {
  const { shell, reads } = started();
  await settle();
  assert.ok(shell.asked.every((path) => !path.includes("sessions")));
  assert.equal(reads.reads().turns, null);
});

test("with turns' page it reads turns' `api/sessions` and follows turns' topic `sessions`, beside its own", async () => {
  const { shell, page, reads } = startedWithTurns();
  await settle();
  assert.deepEqual(page.asked, ["api/sessions"]);
  assert.deepEqual(page.topics.map((sub) => sub.topic), ["sessions"]);
  assert.deepEqual(shell.topics.map((sub) => sub.topic), [RUNNING_TOPIC]);
  assert.ok(shell.asked.every((path) => path !== "api/sessions"));
  assert.deepEqual(workingIds(reads.reads().turns), ["r1-sid"]);
  assert.notEqual(reads.reads().running, null);
});

test("turns' topic replaces the list without a read; each 30 s tick reads it again", async () => {
  const { page, reads, shell, changes } = startedWithTurns();
  await settle();
  const before = changes();
  page.asked.length = 0;
  page.topics[0].fn(liveOf(["r1-sid", false], ["r2-sid", true]));
  assert.deepEqual(workingIds(reads.reads().turns), ["r2-sid"]);
  assert.equal(changes(), before + 1);
  assert.deepEqual(page.asked, []);
  shell.timers[0].fn();
  await settle();
  assert.deepEqual(page.asked, ["api/sessions"]);
  assert.deepEqual(workingIds(reads.reads().turns), ["r1-sid"]);
});

test("turns' read failing drops turns' list and only it; the next good read puts it back", async () => {
  const { page, reads, shell } = startedWithTurns(fakeTurns(500));
  await settle();
  let got = reads.reads();
  assert.equal(got.turns, null);
  assert.ok(got.running !== null && got.presets !== null && got.catalog !== null && got.last !== null
            && got.name !== null);
  page.answer = liveOf(["r1-sid", true]);
  shell.timers[0].fn();
  await settle();
  assert.deepEqual(workingIds(reads.reads().turns), ["r1-sid"]);
  for (const bad of [404, 0, { nonsense: true }]) {
    page.answer = bad;
    shell.timers[0].fn();
    await settle();
    got = reads.reads();
    assert.equal(got.turns, null, JSON.stringify(bad));
    assert.notEqual(got.running, null);
  }
});

test("turns gone at a read: its topic stops and its list is forgotten; back at a later read, it is read again", async () => {
  const page = fakeTurns();
  const { shell, reads, changes } = startedWithTurns(page);
  await settle();
  shell.page = null;
  const before = changes();
  shell.timers[0].fn();
  await settle();
  assert.equal(reads.reads().turns, null);
  assert.deepEqual(page.topics.map((sub) => sub.live), [false]);
  assert.ok(changes() > before);
  page.asked.length = 0;
  shell.page = page;
  shell.timers[0].fn();
  await settle();
  assert.deepEqual(page.asked, ["api/sessions"]);
  assert.deepEqual(page.topics.map((sub) => sub.live), [false, true]);
  assert.deepEqual(workingIds(reads.reads().turns), ["r1-sid"]);
});

test("paused, it stops turns' topic and keeps the list; started again it reads and follows turns once more", async () => {
  const { page, reads } = startedWithTurns();
  await settle();
  reads.pause();
  assert.deepEqual(page.topics.map((sub) => sub.live), [false]);
  assert.notEqual(reads.reads().turns, null);
  page.asked.length = 0;
  reads.start();
  await settle();
  assert.deepEqual(page.asked, ["api/sessions"]);
  assert.deepEqual(page.topics.map((sub) => sub.live), [false, true]);
});

test("closed, a late answer from turns changes nothing", async () => {
  const page = fakeTurns();
  page.held = [];
  const { reads, changes } = startedWithTurns(page);
  await settle();
  reads.close();
  const before = changes();
  for (const done of page.held) done({ status: 200, data: liveOf(["r1-sid", true]) });
  await settle();
  assert.equal(changes(), before);
  assert.equal(reads.reads().turns, null);
  assert.deepEqual(page.topics.map((sub) => sub.live), [false]);
});

test("turns' list alone is no first read: `read` waits for one of the launcher's own", async () => {
  const shell = fakeShell();
  for (const path of ["api/running", "api/presets", "api/catalog"]) shell.hold(path);
  shell.page = fakeTurns();
  const reads = new FactsReads(shell, () => {});
  reads.start();
  await settle();
  assert.notEqual(reads.reads().turns, null);
  assert.equal(reads.read, false);
});
