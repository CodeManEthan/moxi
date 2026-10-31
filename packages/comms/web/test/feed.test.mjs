import assert from "node:assert/strict";
import test from "node:test";

import { Feed, parseRoster, RETRIES } from "../src/feed.ts";

const settled = () => new Promise((done) => setImmediate(done));

const ROW = { qualified: "core/alpha", name: "alpha", scope: "core", live: true, kind: null, module: null,
              tier: "worker", adapter: "claude", directors: ["core/lead"], session_id: "s1",
              bound_at: 100, awaiting: [] };
const UP = { now: 1000, state: "up", detail: null, rows: [ROW] };

/** A feed over `answers`, read in order; the clock reads 400. */
function feedOf(answers) {
  const reads = [];
  const waits = [];
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parseRoster, () => {},
                        { wait: async (ms) => { waits.push(ms); }, clock: () => 400 });
  return { feed, reads, waits };
}

test("the route's three states, and what isn't one", () => {
  assert.deepEqual(parseRoster(UP), { state: "up", detail: null, rows: [ROW] });
  assert.deepEqual(parseRoster({ state: "offline", detail: null, rows: [] }), { state: "offline", detail: null, rows: [] });
  assert.deepEqual(parseRoster({ state: "error", detail: "no", rows: [] }), { state: "error", detail: "no", rows: [] });
  for (const bad of [null, "up", [], { state: "up" }, { state: "up", rows: {} }, { state: "down", rows: [] },
                     { state: "error", detail: 4, rows: [] }]) {
    assert.equal(parseRoster(bad), null, JSON.stringify(bad));
  }
  // A row without an address is dropped; lists that aren't lists of strings are emptied.
  const value = parseRoster({ state: "up", detail: null,
                              rows: [{ name: "x" }, { ...ROW, directors: null, awaiting: [1, "a"] }] });
  assert.equal(value.rows.length, 1);
  assert.deepEqual(value.rows[0].directors, []);
  assert.deepEqual(value.rows[0].awaiting, ["a"]);
});

test("a 200 gives the rows and the server clock's offset", async () => {
  const { feed } = feedOf([{ status: 200, data: UP }]);
  await feed.refresh();
  assert.equal(feed.value.rows[0].qualified, "core/alpha");
  assert.equal(feed.now(), 1000);
});

test("a 503 then a 200 shows no failure; four 503s are one", async () => {
  const busy = { status: 503, data: null };
  const once = feedOf([busy, { status: 200, data: UP }]);
  await once.feed.refresh();
  assert.equal(once.feed.failure, null);
  assert.deepEqual(once.waits, [1000]);
  const four = feedOf([busy, busy, busy, busy]);
  await four.feed.refresh();
  assert.equal(four.reads.length, RETRIES + 1);
  assert.equal(four.feed.failure.status, 503);
});

test("any other status or an unreadable body is a failure that keeps the last rows", async () => {
  for (const answer of [{ status: 500, data: null }, { status: 502, data: null }, { status: 504, data: null },
                        { status: 401, data: null }, { status: 0, data: null }, { status: 200, data: null }]) {
    const { feed } = feedOf([{ status: 200, data: UP }, answer]);
    await feed.refresh();
    await feed.refresh();
    assert.equal(feed.failure.status, answer.status);
    assert.equal(feed.value.rows.length, 1);
  }
});

test("offline rows return on the next topic event", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: { now: 1000, state: "offline", detail: null, rows: [] } }]);
  await feed.refresh();
  assert.equal(feed.value.state, "offline");
  feed.topic({ state: "up", detail: null, rows: [ROW] });
  assert.equal(feed.value.state, "up");
  assert.equal(reads.length, 1);
});

test("after a failure a topic event reads the route again", async () => {
  const { feed, reads } = feedOf([{ status: 502, data: null }, { status: 200, data: UP }]);
  await feed.refresh();
  feed.topic({ state: "up", detail: null, rows: [] });
  await settled();
  assert.equal(reads.length, 2);
  assert.equal(feed.failure, null);
  assert.equal(feed.value.rows.length, 1);
});

test("a 404 means comms went away", async () => {
  const { feed } = feedOf([{ status: 404, data: null }]);
  await feed.refresh();
  assert.equal(feed.gone, true);
});
