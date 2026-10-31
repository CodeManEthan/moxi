import assert from "node:assert/strict";
import test from "node:test";

import { Feed, parseClocks, RETRIES } from "../src/feed.ts";

const settled = () => new Promise((done) => setImmediate(done));

const ROW = { session: "s1", harness: "claude", kind: "warm", last_request: 100, ttl: 3600,
              warn_before: 900, seen_at: 100, stale_after: 30, code: null };
const UP = { now: 1000, state: "up", detail: null, clocks: [ROW] };

/** A feed over `answers`, read in order; the clock reads 400. */
function feedOf(answers) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parseClocks, () => { changes += 1; },
                        { wait: async (ms) => { waits.push(ms); }, clock: () => 400 });
  return { feed, reads, waits, changes: () => changes };
}

test("a 200 gives the value and the server clock's offset", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: UP }]);
  assert.equal(feed.loading, true);
  await feed.refresh();
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value, { state: "up", detail: null, clocks: [ROW] });
  assert.equal(feed.offset, 600);
  assert.equal(feed.now(), 1000);
  assert.equal(feed.failure, null);
  assert.equal(changes(), 1);
});

test("a 503 is read again after 1 s, and a 200 then shows no failure", async () => {
  const { feed, reads, waits } = feedOf([{ status: 503, data: null }, { status: 200, data: UP }]);
  await feed.refresh();
  assert.equal(reads.length, 2);
  assert.deepEqual(waits, [1000]);
  assert.equal(feed.failure, null);
  assert.equal(feed.value.state, "up");
});

test("four 503s are a failure", async () => {
  const busy = { status: 503, data: { error: "busy" } };
  const { feed, reads } = feedOf([busy, busy, busy, busy, { status: 200, data: UP }]);
  await feed.refresh();
  assert.equal(reads.length, RETRIES + 1);
  assert.deepEqual(feed.failure, { status: 503, why: "cache's page server answered 503" });
});

test("every other answer is a failure, and the last value is kept", async () => {
  for (const [answer, why] of [
    [{ status: 500, data: null }, "cache's page server answered 500"],
    [{ status: 502, data: null }, "cache's page server answered 502"],
    [{ status: 504, data: null }, "cache's page server answered 504"],
    [{ status: 401, data: { error: "x" } }, "cache's page server answered 401"],
    [{ status: 0, data: null }, "no answer from the shell"],
    [{ status: 200, data: null }, "cache's page server sent an answer that can't be read"],
    [{ status: 200, data: { now: 5, state: "maybe", clocks: [] } }, "cache's page server sent an answer that can't be read"],
    [{ status: 200, data: { state: "up", clocks: [] } }, "cache's page server sent an answer that can't be read"],
  ]) {
    const { feed } = feedOf([{ status: 200, data: UP }, answer]);
    await feed.refresh();
    await feed.refresh();
    assert.equal(feed.failure?.why, why, JSON.stringify(answer));
    assert.equal(feed.value.clocks.length, 1);
  }
});

test("a 404 means the module went away", async () => {
  const { feed } = feedOf([{ status: 404, data: null }]);
  await feed.refresh();
  assert.equal(feed.gone, true);
  assert.equal(feed.failure, null);
});

test("a state: error body is a value, with its detail", async () => {
  const { feed } = feedOf([{ status: 200, data: { now: 1, state: "error", detail: "it broke", clocks: [] } }]);
  await feed.refresh();
  assert.deepEqual(feed.value, { state: "error", detail: "it broke", clocks: [] });
  assert.equal(feed.failure, null);
});

test("a topic event replaces the value while the last read succeeded", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: UP }]);
  await feed.refresh();
  feed.topic({ state: "up", detail: null, clocks: [] });
  assert.deepEqual(feed.value.clocks, []);
  assert.equal(reads.length, 1);
});

test("after a failure, a topic event reads the route again", async () => {
  const { feed, reads } = feedOf([{ status: 500, data: null }, { status: 200, data: UP }]);
  await feed.refresh();
  assert.equal(feed.failure.status, 500);
  feed.topic({ state: "up", detail: null, clocks: [] });
  await settled();
  assert.equal(reads.length, 2);
  assert.equal(feed.failure, null);
  assert.equal(feed.value.clocks.length, 1);
});

test("a refresh during a read makes one more read, not two at once", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: UP }, { status: 200, data: UP }, { status: 200, data: UP }]);
  const first = feed.refresh();
  feed.refresh();
  feed.refresh();
  await first;
  assert.equal(reads.length, 2);
});

test("a closed feed drops what comes after", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: UP }]);
  const pending = feed.refresh();
  feed.close();
  await pending;
  feed.topic({ state: "up", detail: null, clocks: [] });
  assert.equal(changes(), 0);
  assert.equal(feed.value, null);
});
