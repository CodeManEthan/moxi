import assert from "node:assert/strict";
import test from "node:test";

import { Feed, parseAnswer, parsePlanned, parseRepos, RETRIES } from "../src/feed.ts";
import { answer, plan } from "./fixtures.mjs";

const REPO = { root: "/r/alpha-notes", project: null, git_version: "2.56.0", mode: "config",
               pinned: false, events: [], own_hooks: [], pieces: [], problems: [], specs: [],
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
  assert.deepEqual(feed.value, { state: "up", detail: null, today: null, home: null, repos: [REPO], offered: [], refused: [],
                                 unindexed: null });
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
                   { state: "error", detail: "the index can't be read", today: null, home: null, repos: [], offered: [],
                     refused: [], unindexed: null });
  assert.equal(parseRepos({ state: "up", detail: 3, repos: [] }), null);
});

test("the value carries today, the offered and refused pieces and the projects with no hooks", () => {
  const offered = [{ id: "repo/commit-rules", module: "repo", name: "commit-rules", event: "commit-msg",
                     fail: "closed", order: 50, doc: "checks a message's trailers", settings: [] }];
  const value = parseRepos({ ...UP, today: "2026-11-18", offered, refused: [{ id: "x/y", why: "no event" }],
                             unindexed: [{ project: "orbit", root: "/r/orbit" }] });
  assert.equal(value.today, "2026-11-18");
  assert.equal(value.home, null);
  assert.equal(parseRepos({ ...UP, home: "/home/jp" }).home, "/home/jp");
  assert.equal(parseRepos({ ...UP, home: "" }).home, null);
  assert.deepEqual(value.offered, offered);
  assert.deepEqual(value.refused, [{ id: "x/y", why: "no event" }]);
  assert.deepEqual(value.unindexed, [{ project: "orbit", root: "/r/orbit" }]);
  for (const bad of [{ offered: {} }, { refused: [1] }, { unindexed: "x" }, { repos: [{ ...REPO, own_hooks: null }] }]) {
    assert.equal(parseRepos({ ...UP, ...bad }), null);
  }
});

test("a plan's value: up with its plan, error with its detail, anything else unread", () => {
  const up = parsePlanned({ state: "up", detail: null, plan: plan() });
  assert.deepEqual(up, { state: "up", detail: null, plan: plan() });
  const refused = plan({ refusal: { code: "not-a-repo", text: "/r is not in a git repo" }, digest: null });
  assert.equal(parsePlanned({ state: "up", detail: null, plan: refused }).plan.refusal.code, "not-a-repo");
  assert.deepEqual(parsePlanned({ state: "error", detail: "it broke", plan: null }),
                   { state: "error", detail: "it broke", plan: null });
  for (const bad of [null, [], { state: "up", plan: null }, { state: "error" }, { state: "up", plan: { root: "/r" } },
                     { state: "up", plan: plan({ writes: [1] }) }, { state: "up", plan: plan({ refusal: "no" }) }]) {
    assert.equal(parsePlanned(bad), null, JSON.stringify(bad));
  }
});

test("an act's answer: each outcome, a changed one needs its plan", () => {
  for (const outcome of ["done", "nothing", "refused", "partial", "failed"]) {
    assert.equal(parseAnswer(answer({ outcome })).outcome, outcome);
  }
  assert.equal(parseAnswer(answer({ outcome: "changed", plan: plan() })).plan.digest, "d1");
  for (const bad of [answer({ outcome: "changed" }), answer({ outcome: "maybe" }), answer({ lines: "x" }),
                     answer({ chain: [1] }), answer({ refusal: { code: null } }), null]) {
    assert.equal(parseAnswer(bad), null, JSON.stringify(bad));
  }
});
