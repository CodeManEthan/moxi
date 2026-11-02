import assert from "node:assert/strict";
import test from "node:test";

import { Feed, parseRepos, RETRIES } from "../src/feed.ts";

const REPO = { root: "/r/alpha-notes", project: null, git_version: "2.56.0", mode: "config",
               pinned: false, events: [], pieces: [], problems: [], specs: [],
               specs_problem: null, records: [], commands: [] };
const UP = { now: 1000, state: "up", detail: null, repos: [REPO] };

/** A feed over `answers`, read in order; the clock reads 400. */
function feedOf(answers) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        () => { changes += 1; },
                        { wait: async (ms) => { waits.push(ms); }, clock: () => 400 });
  return { feed, reads, waits, changes: () => changes };
}

test("a 200 gives the value and the server clock's offset", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: UP }]);
  assert.equal(feed.loading, true);
  const read = feed.refresh();
  assert.equal(feed.reading, true);
  await read;
  assert.equal(feed.reading, false);
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value, { state: "up", detail: null, repos: [REPO] });
  assert.equal(feed.now(), 1000);
  assert.equal(feed.failure, null);
  assert.equal(changes(), 1);
});

test("a 503 is read again after 1 s, and four are a failure", async () => {
  const again = feedOf([{ status: 503, data: null }, { status: 200, data: UP }]);
  await again.feed.refresh();
  assert.deepEqual(again.waits, [1000]);
  assert.equal(again.feed.value.state, "up");
  const busy = { status: 503, data: null };
  const { feed, reads } = feedOf([busy, busy, busy, busy, { status: 200, data: UP }]);
  await feed.refresh();
  assert.equal(reads.length, RETRIES + 1);
  assert.deepEqual(feed.failure, { status: 503, why: "repo's page server answered 503" });
});

test("every other answer is a failure, and the last value is kept", async () => {
  for (const [answer, why] of [
    [{ status: 500, data: null }, "repo's page server answered 500"],
    [{ status: 401, data: { error: "x" } }, "repo's page server answered 401"],
    [{ status: 0, data: null }, "no answer from the shell"],
    [{ status: 200, data: null }, "repo's page server sent an answer that can't be read"],
    [{ status: 200, data: { now: 5, state: "maybe", repos: [] } }, "repo's page server sent an answer that can't be read"],
    [{ status: 200, data: { now: 5, state: "up", repos: [{ root: "/r" }] } }, "repo's page server sent an answer that can't be read"],
    [{ status: 200, data: { state: "up", detail: null, repos: [] } }, "repo's page server sent an answer that can't be read"],
  ]) {
    const { feed } = feedOf([{ status: 200, data: UP }, answer]);
    await feed.refresh();
    await feed.refresh();
    assert.deepEqual(feed.failure, { status: answer.status, why });
    assert.deepEqual(feed.value.repos, [REPO]);
  }
});

test("a 404 is gone, and a read after close changes nothing", async () => {
  const { feed } = feedOf([{ status: 404, data: null }]);
  await feed.refresh();
  assert.equal(feed.gone, true);
  const closed = feedOf([{ status: 200, data: UP }]);
  closed.feed.close();
  await closed.feed.refresh();
  assert.equal(closed.feed.value, null);
  assert.equal(closed.changes(), 0);
});

test("a refresh while one runs reads once more after it", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: UP }, { status: 200, data: UP }]);
  const first = feed.refresh();
  const second = feed.refresh();
  await Promise.all([first, second]);
  assert.equal(reads.length, 2);
});

test("an error value keeps its detail and drops the repos", () => {
  assert.deepEqual(parseRepos({ state: "error", detail: "the index can't be read", repos: [REPO] }),
                   { state: "error", detail: "the index can't be read", repos: [] });
  assert.equal(parseRepos({ state: "up", detail: 3, repos: [] }), null);
});
