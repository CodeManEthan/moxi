import assert from "node:assert/strict";
import test from "node:test";

import { entryOf, Feed, parseLaunches, parseStates, RETRIES, SessionPick } from "../src/feed.ts";
import { stateView } from "../src/views.ts";

const RUN = { run_id: "r1", kind: "relaunch", mode: "self", state: "ended", outcome: "failed",
              word: null, detail: "the pane went away", created_at: "2026-10-31T10:00:00.000000Z",
              updated_at: "2026-10-31T10:01:00.000000Z" };
const entry = (extra) => ({
  session_id: "s1", request_id: "q1",
  relaunch: { state: "failed", run: RUN, made_by: null, notices_refused: [], note_not_taken: false },
  recycle: { on_expiry: "off", at_limit: "off", limit_at: null, last: null, pending_compact: null,
             told_at: null, pending_dropped_at: null },
  keep_warm: { on: false, holds: [], nudges: 0, last: null, window: null },
  red: false, since: null, why: null, word: null, run_id: null, unreadable: null, ...extra });
const UP = { state: "up", detail: null, sessions: [entry()], refused: null };
const LAUNCH = { request_id: "q1", created_at: "2026-10-31T09:00:00.000000Z",
                 updated_at: "2026-10-31T09:00:01.000000Z", actor: "page", harness: "claude",
                 target: "slot", state: "first-sent", reason: null, posture: "default",
                 stale: false, preset_kind: null, preset_name: null, slot: "3", cwd: "/w",
                 project_key: null, identity: null, scope: null, session_id: "s1",
                 relaunch_of: null, resume_of: null };

const settled = () => new Promise((done) => setImmediate(done));

/** A feed over `answers`, read in order. */
function feedOf(answers, options = {}, parse = parseStates) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parse, () => { changes += 1; },
                        { wait: async (ms) => { waits.push(ms); }, ...options });
  return { feed, reads, waits, changes: () => changes };
}

test("a 200 gives the value", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: UP }]);
  assert.equal(feed.loading, true);
  await feed.refresh();
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value, UP);
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
  assert.deepEqual(feed.failure, { status: 503, why: "the launcher's page server answered 503" });
});

test("every other answer is a failure, and the last value is kept", async () => {
  const unreadable = "the launcher's page server sent an answer that can't be read";
  for (const [answer, why] of [
    [{ status: 500, data: null }, "the launcher's page server answered 500"],
    [{ status: 422, data: { refused: "x" } }, "the launcher's page server answered 422"],
    [{ status: 0, data: null }, "no answer from the shell"],
    [{ status: 200, data: null }, unreadable],
    [{ status: 200, data: { state: "maybe", sessions: [] } }, unreadable],
    [{ status: 200, data: { state: "up", sessions: "none" } }, unreadable],
    [{ status: 200, data: { state: "up", detail: 3, sessions: [] } }, unreadable],
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

test("a state: error body is a value, with its detail and nothing else", async () => {
  const { feed } = feedOf([{ status: 200, data: { state: "error", detail: "it broke", sessions: [entry()] } }]);
  await feed.refresh();
  assert.deepEqual(feed.value, { state: "error", detail: "it broke", sessions: [], refused: null });
});

test("entries that aren't a session's state are left out; a refusal is kept", async () => {
  const refused = { code: "not-launched", row: null, problem: "no launch holds s9", hint: "h" };
  const data = { state: "up", detail: null, sessions: [entry(), { session_id: 3 }, null, "x"], refused };
  const value = parseStates(data);
  assert.deepEqual(value.sessions, [entry()]);
  assert.deepEqual(value.refused, refused);
  // The topic sends no `refused`.
  assert.equal(parseStates({ state: "up", detail: null, sessions: [] }).refused, null);
});

test("the launch log's value", () => {
  assert.deepEqual(parseLaunches({ state: "up", detail: null, launches: [LAUNCH, { request_id: 1 }] }),
                   { state: "up", detail: null, launches: [LAUNCH] });
  assert.deepEqual(parseLaunches({ state: "error", detail: "no", launches: [LAUNCH] }),
                   { state: "error", detail: "no", launches: [] });
  assert.equal(parseLaunches({ state: "up", detail: null }), null);
  assert.equal(parseLaunches([]), null);
});

test("a topic event replaces the value once a read succeeded", async () => {
  const { feed, reads, changes } = feedOf([{ status: 200, data: UP }]);
  await feed.refresh();
  const red = { state: "up", detail: null, sessions: [entry({ red: true, word: "failed" })] };
  feed.topic(red);
  assert.equal(reads.length, 1);
  assert.equal(feed.value.sessions[0].red, true);
  assert.equal(changes(), 2);
});

test("a topic event before the first read, after a failure or that doesn't read, reads the route", async () => {
  {
    const { feed, reads } = feedOf([{ status: 200, data: UP }]);
    feed.topic(UP);
    await settled();
    assert.equal(reads.length, 1);
    assert.equal(feed.value.state, "up");
  }
  {
    const { feed, reads } = feedOf([{ status: 500, data: null }, { status: 200, data: UP }]);
    await feed.refresh();
    feed.topic(UP);
    await settled();
    assert.equal(reads.length, 2);
    assert.equal(feed.failure, null);
  }
  {
    const { feed, reads } = feedOf([{ status: 200, data: UP }, { status: 200, data: UP }]);
    await feed.refresh();
    feed.topic({ state: "sideways" });
    await settled();
    assert.equal(reads.length, 2);
  }
});

test("one session's topic: its entry replaces the value, an event without it reads the route", async () => {
  const two = { state: "up", detail: null,
                sessions: [entry({ session_id: "s2", request_id: "q2" }), entry({ red: true, word: "blocked" })] };
  const pick = new SessionPick("s1");
  const { feed, reads } = feedOf([{ status: 200, data: UP }, { status: 200, data: UP }],
                                 { topic: pick.topic }, pick.route);
  await feed.refresh();
  feed.topic(two);
  assert.equal(reads.length, 1);
  assert.deepEqual(feed.value.sessions.map((item) => item.session_id), ["s1"]);
  assert.equal(feed.value.sessions[0].word, "blocked");
  // The session is no longer live: it is in no topic, so the route says how it ended.
  feed.topic({ state: "up", detail: null, sessions: [entry({ session_id: "s2", request_id: "q2" })] });
  await settled();
  assert.equal(reads.length, 2);
  // A topic that failed is the topic's value.
  feed.topic({ state: "error", detail: "gathering failed", sessions: [] });
  assert.equal(feed.value.state, "error");
});

test("a pick takes the newest entry for the session until the route names its launch", () => {
  const pick = new SessionPick("s1");
  const value = pick.topic({ state: "up", detail: null,
                             sessions: [entry({ request_id: "new" }), entry({ request_id: "old" })] });
  assert.deepEqual(value.sessions.map((item) => item.request_id), ["new"]);
  assert.equal(pick.topic({ state: "up", detail: null, sessions: [] }), null);
  assert.equal(pick.topic(null), null);
  // Once the route has named the launch, its entry is the one, whatever id it carries.
  pick.route({ state: "up", detail: null, sessions: [entry({ request_id: "old" })], refused: null });
  assert.equal(pick.request, "old");
  assert.equal(entryOf([entry({ request_id: "new" }), entry({ session_id: "s9", request_id: "old" })],
                       "s1", pick.request).session_id, "s9");
  // A session no launch holds names none.
  pick.route({ state: "up", detail: null, sessions: [], refused: { code: "not-launched", row: null,
                                                                   problem: "p", hint: "h" } });
  assert.equal(pick.request, null);
});

test("a session given a new id by a clear or a compact is still followed", async () => {
  // Placed with the id its launch was first known by; the route resolves it and answers the
  // launch's state under the id a clear gave the session since.
  const cleared = { state: "up", detail: null, refused: null,
                    sessions: [entry({ session_id: "s2", red: true, word: "failed", why: "relaunch-failed" })] };
  const pick = new SessionPick("s1");
  const { feed, reads } = feedOf([{ status: 200, data: cleared }], { topic: pick.topic }, pick.route);
  await feed.refresh();
  const view = stateView(feed, pick.session, pick.request);
  assert.deepEqual([view.state, view.red, view.text], ["up", true, "failed"]);
  // A compact gives it a third id; the topic's entry for the launch replaces the state.
  feed.topic({ state: "up", detail: null,
               sessions: [entry({ session_id: "s3", word: null }), entry({ session_id: "s1", request_id: "q9" })] });
  assert.equal(reads.length, 1);
  assert.deepEqual(feed.value.sessions.map((item) => [item.session_id, item.request_id]), [["s3", "q1"]]);
  const after = stateView(feed, pick.session, pick.request);
  assert.deepEqual([after.state, after.red, after.text], ["up", false, "ok"]);
});

test("a refresh while a read runs makes one more read after it", async () => {
  let release;
  const gate = new Promise((done) => { release = done; });
  let reads = 0;
  const feed = new Feed(async () => { reads += 1; if (reads === 1) await gate; return { status: 200, data: UP }; },
                        parseStates, () => {});
  const first = feed.refresh();
  void feed.refresh();
  void feed.refresh();
  release();
  await first;
  assert.equal(reads, 2);
});

test("a closed feed drops every later answer and event", async () => {
  let release;
  const gate = new Promise((done) => { release = done; });
  let changes = 0;
  const feed = new Feed(async () => { await gate; return { status: 200, data: UP }; },
                        parseStates, () => { changes += 1; });
  const running = feed.refresh();
  feed.close();
  release();
  await running;
  feed.topic(UP);
  assert.equal(changes, 0);
  assert.equal(feed.value, null);
});
