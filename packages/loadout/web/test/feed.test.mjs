import assert from "node:assert/strict";
import test from "node:test";

import {
  Feed, FINE, parseDrift, parseGlobal, parseJob, parseListing, parseProjects, parseStatus,
  RETRIES, STATES,
} from "../src/feed.ts";

const ROW = { scope: "user", kind: "skill", name: "tidy-notes", harness: "claude",
              state: "missing", reason: "not placed yet", target: "/h/.claude/skills/tidy-notes",
              replaceable: false };
const OUTCOME = { rows: [ROW], lines: [], notes: [], counts: { missing: 1 }, exit: 1 };
const DRIFT = { problem: null, count: 2, note: null };

/** A feed of `/api/drift` over `answers`, read in order. */
function feedOf(answers) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parseDrift, () => { changes += 1; },
                        { wait: async (ms) => { waits.push(ms); } });
  return { feed, reads, waits, changes: () => changes };
}

test("a 200 gives the value", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: DRIFT }]);
  assert.equal(feed.loading, true);
  const read = feed.refresh();
  assert.equal(feed.reading, true);
  await read;
  assert.equal(feed.reading, false);
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value, DRIFT);
  assert.equal(feed.failure, null);
  assert.equal(changes(), 1);
});

test("a 503 is read again after 1 s, and four are a failure", async () => {
  const again = feedOf([{ status: 503, data: null }, { status: 200, data: DRIFT }]);
  await again.feed.refresh();
  assert.deepEqual(again.waits, [1000]);
  assert.equal(again.feed.value.count, 2);
  const busy = { status: 503, data: null };
  const { feed, reads } = feedOf([busy, busy, busy, busy, { status: 200, data: DRIFT }]);
  await feed.refresh();
  assert.equal(reads.length, RETRIES + 1);
  assert.deepEqual(feed.failure, { status: 503, why: "loadout's page server answered 503", said: null });
});

test("every other answer is a failure, and the last value is kept", async () => {
  for (const [answer, why, said = null] of [
    [{ status: 500, data: null }, "loadout's page server answered 500"],
    [{ status: 401, data: { error: "x" } }, "loadout's page server answered 401", "x"],
    [{ status: 422, data: { refused: "there is no page job j1" } }, "loadout's page server answered 422",
     "there is no page job j1"],
    [{ status: 0, data: null }, "no answer from the shell"],
    [{ status: 200, data: null }, "loadout's page server sent an answer that can't be read"],
    [{ status: 200, data: { problem: null, count: "2", note: null } }, "loadout's page server sent an answer that can't be read"],
  ]) {
    const { feed } = feedOf([{ status: 200, data: DRIFT }, answer]);
    await feed.refresh();
    await feed.refresh();
    assert.deepEqual(feed.failure, { status: answer.status, why, said });
    assert.deepEqual(feed.value, DRIFT);
  }
});

test("a 404 is gone, and a read after close changes nothing", async () => {
  const { feed } = feedOf([{ status: 404, data: null }]);
  await feed.refresh();
  assert.equal(feed.gone, true);
  const closed = feedOf([{ status: 200, data: DRIFT }]);
  closed.feed.close();
  await closed.feed.refresh();
  assert.equal(closed.feed.value, null);
  assert.equal(closed.changes(), 0);
});

test("a refresh while one runs reads once more after it", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: DRIFT }, { status: 200, data: { ...DRIFT, count: 0 } }]);
  const first = feed.refresh();
  const second = feed.refresh();
  assert.equal(first, second);
  await first;
  assert.equal(reads.length, 2);
  assert.equal(feed.value.count, 0);
});

test("the states and the fine ones are the page server's", () => {
  assert.deepEqual([...STATES], ["ok", "missing", "stale", "modified", "blocked", "would-commit",
                                 "bad-source", "off"]);
  assert.deepEqual([...FINE].sort(), ["off", "ok"]);
});

test("drift reads its count and note, and refuses a count that isn't a whole number", () => {
  assert.deepEqual(parseDrift({ problem: null, count: 0, note: "a line" }),
                   { problem: null, count: 0, note: "a line" });
  assert.deepEqual(parseDrift({ problem: "loadout: x", count: 0, note: null, extra: 1 }),
                   { problem: "loadout: x", count: 0, note: null });
  for (const bad of [null, [], { count: 1 }, { problem: null, count: 1.5, note: null },
                     { problem: null, count: -1, note: null }, { problem: 3, count: 1, note: null }]) {
    assert.equal(parseDrift(bad), null, JSON.stringify(bad));
  }
});

test("status reads an outcome with its problem", () => {
  assert.deepEqual(parseStatus({ problem: null, ...OUTCOME }), { problem: null, ...OUTCOME });
  assert.equal(parseStatus({ problem: null, ...OUTCOME, rows: [{ ...ROW, state: 3 }] }), null);
  const { replaceable, ...unflagged } = ROW;
  assert.equal(replaceable, false);
  assert.equal(parseStatus({ problem: null, ...OUTCOME, rows: [unflagged] }), null);
  assert.equal(parseStatus({ problem: null, ...OUTCOME, counts: { ok: "1" } }), null);
  assert.equal(parseStatus(OUTCOME), null);
});

test("the listing, the global preview and the projects read their shapes", () => {
  const listing = { problem: null, manifest: "/l/loadout.toml",
                    sections: [{ name: "user", where: "user", root: null }],
                    items: [{ kind: "skill", name: "tidy-notes", scope: "user", harnesses: null,
                              source: "/l/tidy-notes", disabled: [] }] };
  assert.deepEqual(parseListing(listing), listing);
  assert.equal(parseListing({ ...listing, items: [{ kind: "skill" }] }), null);
  const global = { problem: null, mode: "managed", chosen: true,
                   files: [{ file: "/h/.claude/CLAUDE.md", changes: true, diff: "+x", placed: false,
                             state: null }] };
  assert.deepEqual(parseGlobal(global), global);
  assert.equal(parseGlobal({ ...global, chosen: "yes" }), null);
  const projects = { projects: [{ key: "alpha", root: "/w/alpha" }], reason: null };
  assert.deepEqual(parseProjects(projects), projects);
  assert.deepEqual(parseProjects({ projects: [], reason: "projects isn't installed" }),
                   { projects: [], reason: "projects isn't installed" });
  assert.equal(parseProjects({ projects: [{ key: "alpha" }], reason: null }), null);
});

test("a job reads in each of its three states", () => {
  const running = { job: "j1", argv: ["add", "skill", "tidy-notes"], state: "running", exit: null,
                    outcome: null, lines: [] };
  assert.deepEqual(parseJob(running), running);
  const done = { ...running, state: "done", exit: 0, outcome: { ...OUTCOME, exit: 0 } };
  assert.deepEqual(parseJob(done), done);
  const gone = { ...running, state: "interrupted", argv: null, lines: ["run it again"] };
  assert.deepEqual(parseJob(gone), gone);
  assert.equal(parseJob({ ...running, state: "queued" }), null);
  assert.equal(parseJob({ ...done, exit: "0" }), null);
  assert.equal(parseJob({ ...done, outcome: { rows: [] } }), null);
});
