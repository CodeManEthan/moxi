import assert from "node:assert/strict";
import test from "node:test";

import { Feed, parseList, RETRIES } from "../src/feed.ts";

const settled = () => new Promise((done) => setImmediate(done));

const SESSION = { key: "claude-n1", harness: "claude", pid: 10, session_id: "s1", cwd: "/w",
                  started: 100, capabilities: ["agents"], endpoint_version: "2.0",
                  mod_version: "0.2.0", trouble: null, facts: null, agents: null,
                  read_error: null };
const UP = { now: 1000, listed: true, detail: null, sessions: [SESSION] };

/** A feed over `answers`, read in order; the clock reads 400. */
function feedOf(answers) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parseList, () => { changes += 1; },
                        { wait: async (ms) => { waits.push(ms); }, clock: () => 400 });
  return { feed, reads, waits, changes: () => changes };
}

test("parseList takes the route's and the topic's value", () => {
  assert.deepEqual(parseList({ listed: true, detail: null, sessions: [SESSION] }),
                   { listed: true, detail: null, sessions: [SESSION] });
  assert.deepEqual(parseList({ listed: false, sessions: [] }), { listed: false, detail: null, sessions: [] });
  assert.equal(parseList({ listed: true, detail: "d", sessions: [] }).detail, "d");
});

test("parseList refuses what isn't the shape, and drops rows with no key", () => {
  for (const bad of [null, 5, "x", [], { sessions: [] }, { listed: "yes", sessions: [] },
                     { listed: true, sessions: {} }, { listed: true, detail: 3, sessions: [] }]) {
    assert.equal(parseList(bad), null, JSON.stringify(bad));
  }
  const kept = parseList({ listed: true, detail: null, sessions: [SESSION, { harness: "x" }, null, 7] });
  assert.deepEqual(kept.sessions, [SESSION]);
});

test("a 200 gives the value and the server clock's offset", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: UP }]);
  assert.equal(feed.loading, true);
  await feed.refresh();
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value, { listed: true, detail: null, sessions: [SESSION] });
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
  assert.equal(feed.value.sessions.length, 1);
});

test("four 503s are a failure", async () => {
  const busy = { status: 503, data: { error: "busy" } };
  const { feed, reads } = feedOf([busy, busy, busy, busy, { status: 200, data: UP }]);
  await feed.refresh();
  assert.equal(reads.length, RETRIES + 1);
  assert.deepEqual(feed.failure, { status: 503, why: "turns' page server answered 503" });
});

test("every other answer is a failure, and the last value is kept", async () => {
  for (const [answer, why] of [
    [{ status: 500, data: null }, "turns' page server answered 500"],
    [{ status: 502, data: null }, "turns' page server answered 502"],
    [{ status: 401, data: { error: "x" } }, "turns' page server answered 401"],
    [{ status: 0, data: null }, "no answer from the shell"],
    [{ status: 200, data: null }, "turns' page server sent an answer that can't be read"],
    [{ status: 200, data: { now: 5, listed: "no", sessions: [] } }, "turns' page server sent an answer that can't be read"],
    [{ status: 200, data: { listed: true, detail: null, sessions: [] } }, "turns' page server sent an answer that can't be read"],
  ]) {
    const { feed } = feedOf([{ status: 200, data: UP }, answer]);
    await feed.refresh();
    await feed.refresh();
    assert.equal(feed.failure?.why, why, JSON.stringify(answer));
    assert.equal(feed.value.sessions.length, 1);
  }
});

test("a 404 means the module went away", async () => {
  const { feed } = feedOf([{ status: 404, data: null }]);
  await feed.refresh();
  assert.equal(feed.gone, true);
  assert.equal(feed.failure, null);
});

test("a topic event replaces the value while the last read succeeded", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: UP }]);
  await feed.refresh();
  feed.topic({ listed: true, detail: null, sessions: [] });
  assert.deepEqual(feed.value.sessions, []);
  assert.equal(reads.length, 1);
});

test("a topic event that isn't the shape reads the route again", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: UP }, { status: 200, data: UP }]);
  await feed.refresh();
  feed.topic({ nope: true });
  await settled();
  assert.equal(reads.length, 2);
  assert.equal(feed.value.sessions.length, 1);
});

test("after a failure, a topic event reads the route again", async () => {
  const { feed, reads } = feedOf([{ status: 500, data: null }, { status: 200, data: UP }]);
  await feed.refresh();
  assert.equal(feed.failure.status, 500);
  feed.topic({ listed: true, detail: null, sessions: [] });
  await settled();
  assert.equal(reads.length, 2);
  assert.equal(feed.failure, null);
  assert.equal(feed.value.sessions.length, 1);
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
  feed.topic({ listed: true, detail: null, sessions: [] });
  assert.equal(changes(), 0);
  assert.equal(feed.value, null);
});
