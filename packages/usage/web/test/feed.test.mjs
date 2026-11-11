import assert from "node:assert/strict";
import test from "node:test";

import {
  Feed, parseIndexEvent, parseLimits, parseReport, parseStatus, passOutcome, RETRIES,
} from "../src/feed.ts";

const SUMS = { requests: 2, input: 300, fresh_input: 100, cache_read: 150, cache_write: 50,
               cache_write_1h: 0, output: 40, tokens: 340, unpriced_tokens: 0, unknown_rows: 0,
               cost: 0.5, complete: true, harness_cost: null, unknown: [] };
const REPORT = { since: null, until: null, tz: "local", filters: [], by: "day",
                 rows: [{ period: "2026-10-01", model: "m1", ...SUMS, unpriced_models: [], rules: [] }],
                 total: SUMS, unpriced_models: [], rules: [],
                 index: { passed: false, last_pass: 900, note: "no pass ran first; this is the index as it is" } };
const WINDOW = { harness: "codex", limit_id: "codex", label: "5h", state: "fresh", window_minutes: 300,
                 reason: null, slot: "primary", source: "rollout", session_id: null, plan_type: null,
                 projection: null, used_percent: 40, resets_at: "2026-11-02T12:00:00Z",
                 observed_at: "2026-11-02T10:00:00Z", full_at: null, fit: 0, note: null,
                 dollars: [], unknown: [] };
const LIMITS = { now: 1000, rows: [WINDOW], pull: null, notes: [] };

const settled = () => new Promise((done) => setImmediate(done));

/** A feed over `answers`, read in order; the clock reads 400. */
function feedOf(answers, parse = parseReport) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parse, () => { changes += 1; },
                        { wait: async (ms) => { waits.push(ms); }, clock: () => 400 });
  return { feed, reads, waits, changes: () => changes };
}

test("a report's 200 gives the value; with no `now` the clock is this browser's", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: REPORT }]);
  assert.equal(feed.loading, true);
  await feed.refresh();
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value.rows, REPORT.rows);
  assert.equal(feed.offset, null);
  assert.equal(feed.now(), 400);
  assert.equal(changes(), 1);
});

test("the limits' `now` sets the server clock's offset", async () => {
  const { feed } = feedOf([{ status: 200, data: LIMITS }], parseLimits);
  await feed.refresh();
  assert.equal(feed.offset, 600);
  assert.equal(feed.now(), 1000);
  assert.equal(feed.value.rows[0].label, "5h");
});

test("a 503 is read again 1 s apart, up to three times, then is a failure", async () => {
  const busy = { status: 503, data: null };
  const { feed, reads, waits } = feedOf([busy, busy, busy, busy, { status: 200, data: REPORT }]);
  await feed.refresh();
  assert.equal(reads.length, RETRIES + 1);
  assert.deepEqual(waits, [1000, 1000, 1000]);
  assert.equal(feed.failure.status, 503);
  assert.equal(feed.failure.why, "usage's page server answered 503");
  assert.equal(feed.value, null);
});

test("a failure keeps the last value; a 404 is gone", async () => {
  const { feed } = feedOf([{ status: 200, data: REPORT }, { status: 0, data: null }, { status: 404, data: null }]);
  await feed.refresh();
  await feed.refresh();
  assert.equal(feed.failure.why, "no answer from the shell");
  assert.ok(feed.value !== null);
  await feed.refresh();
  assert.equal(feed.gone, true);
  assert.equal(feed.failure, null);
});

test("a 422 is the API's refusal, with its text, and drops the last value", async () => {
  const { feed } = feedOf([{ status: 200, data: REPORT },
                           { status: 422, data: { refused: "until is before since" } }]);
  await feed.refresh();
  await feed.refresh();
  assert.equal(feed.refused, "until is before since");
  assert.equal(feed.value, null);
  assert.equal(feed.failure, null);
});

test("a body that isn't the route's shape is a failure", async () => {
  const { feed } = feedOf([{ status: 200, data: { rows: "x" } }]);
  await feed.refresh();
  assert.equal(feed.failure.status, 200);
  assert.equal(feed.failure.why, "usage's page server sent an answer that can't be read");
});

test("a topic event replaces the value after a good read, and reads again otherwise", async () => {
  const good = { status: 200, data: LIMITS };
  const { feed, reads } = feedOf([good, good, good], parseLimits);
  feed.topic({ rows: [], pull: null, notes: [] });          // before any read: read
  await feed.refresh();
  assert.ok(reads.length >= 1);
  const before = reads.length;
  const later = { rows: [{ ...WINDOW, used_percent: 70 }], pull: null, notes: ["n"] };
  feed.topic(later);
  assert.equal(feed.value.rows[0].used_percent, 70);
  assert.deepEqual(feed.value.notes, ["n"]);
  assert.equal(reads.length, before);
  feed.topic({ nonsense: true });
  await settled();
  assert.equal(reads.length, before + 1);
});

test("a held value shows until the feed's own read answers", async () => {
  const held = { ...REPORT, rows: [] };
  const { feed } = feedOf([{ status: 200, data: REPORT }]);
  assert.equal(feed.hold(held), feed);
  assert.equal(feed.value, held);
  assert.equal(feed.loading, true);
  await feed.refresh();
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value.rows, REPORT.rows);
});

test("a closed feed drops answers and events", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: REPORT }]);
  const running = feed.refresh();
  feed.close();
  await running;
  feed.topic(REPORT);
  assert.equal(feed.value, null);
  assert.equal(changes(), 0);
});

test("a refresh while one runs reads once more after it", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: REPORT }, { status: 200, data: REPORT }]);
  const one = feed.refresh();
  const two = feed.refresh();
  await Promise.all([one, two]);
  assert.equal(reads.length, 2);
  assert.equal(feed.reading, false);
});

test("the parsers take their shapes and refuse others", () => {
  assert.equal(parseReport({ ...REPORT, rows: [1] }), null);
  assert.equal(parseReport({ ...REPORT, total: null }), null);
  const status = { lanes: [{ lane: "claude", state: "online" }], index: REPORT.index,
                   total: { files: 1, held: 0, requests: 2, held_rows: [], last_pass: 5, index_version: 3 } };
  assert.equal(parseStatus(status).total.index_version, 3);
  assert.equal(parseStatus({ ...status, total: { ...status.total, index_version: "3" } }), null);
  assert.equal(parseLimits({ rows: [{ label: 1 }], notes: [] }), null);
  assert.deepEqual(parseIndexEvent({ running: false, last_pass: 7, note: null }),
                   { running: false, last_pass: 7, note: null });
  assert.equal(parseIndexEvent({ running: "no", last_pass: 7 }), null);
  assert.equal(parseIndexEvent({ running: true, last_pass: null, note: 5 }), null);
});

test("the pass act's answers", () => {
  assert.deepEqual(passOutcome({ status: 200, data: { started: true, running: true } }), { outcome: "started" });
  assert.deepEqual(passOutcome({ status: 200, data: { started: false, running: true } }), { outcome: "running" });
  assert.deepEqual(passOutcome({ status: 422, data: { refused: "there is no index yet; run `moxi usage index` to build it" } }),
                   { outcome: "refused", text: "there is no index yet; run `moxi usage index` to build it" });
  assert.deepEqual(passOutcome({ status: 0, data: null }), { outcome: "failed", why: "no answer from the shell" });
});
