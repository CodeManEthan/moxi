import assert from "node:assert/strict";
import test from "node:test";

import { Feed, failureWhy, parseListing, parseWindow, refusedText, RETRIES } from "../src/feed.ts";

const ROW = { kind: "message", raw_kind: "user", at: { line: null, offset: 0 }, unknown: [],
              role: "user", text: "hello", text_bytes: 5, truncated: false, reasoning: false, flags: [] };
const WINDOW = { shape: 1, harness: "claude", session: "s1", agent: null, path: "/p/s1.jsonl",
                 header: { cwd: "/work", unknown: [] }, last_activity: 1000, turns: 5,
                 has_earlier: false, before: null, ended: "start", records: [ROW] };
const ENTRY = { agent_id: "a1", session: "s1", agent: "a1", route: "claude/s1/a1", path: "/p/a1.jsonl",
                size: 10, unknown: [] };
const LISTING = { shape: 1, harness: "claude", session: "s1", state: "listed", reason: null,
                  entries: [ENTRY], unreadable_parts: [] };

/** A feed over `answers`, read in order. */
function feedOf(answers, parse = parseWindow) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parse, () => { changes += 1; }, { wait: async (ms) => { waits.push(ms); } });
  return { feed, reads, waits, changes: () => changes };
}

// -- parsing ------------------------------------------------------------------------------------

test("a window answer of shape 1 is read; another shape, or a row without its kind, is not", () => {
  assert.deepEqual(parseWindow(WINDOW), WINDOW);
  assert.equal(parseWindow({ ...WINDOW, shape: 2 }), null);
  assert.equal(parseWindow({ ...WINDOW, records: [{ raw_kind: "x", at: {}, unknown: [] }] }), null);
  assert.equal(parseWindow({ ...WINDOW, records: [{ ...ROW, unknown: undefined }] }), null);
  assert.equal(parseWindow({ ...WINDOW, header: null }), null);
  assert.equal(parseWindow({ ...WINDOW, before: 12 }), null);
  assert.equal(parseWindow(null), null);
  assert.equal(parseWindow([]), null);
});

test("a listing is read in each of its four states, and not in another", () => {
  for (const state of ["listed", "none", "unreadable", "unsupported"]) {
    assert.equal(parseListing({ ...LISTING, state }).state, state);
  }
  assert.equal(parseListing({ ...LISTING, state: "running" }), null);
  assert.equal(parseListing({ ...LISTING, entries: [{ ...ENTRY, route: null }] }), null);
  // A listing with no parts list reads as one with none.
  const { unreadable_parts: _, ...bare } = LISTING;
  assert.deepEqual(parseListing(bare).unreadable_parts, []);
});

test("a refusal's text is the answer's own", () => {
  assert.equal(refusedText({ refused: "no claude session s9 was found (looked in /p)" }),
               "no claude session s9 was found (looked in /p)");
  assert.equal(refusedText({ detail: "x" }), "x");
  assert.equal(refusedText("nope"), null);
  assert.equal(failureWhy(0), "no answer from the shell");
  assert.equal(failureWhy(500), "sessions' page server answered 500");
  assert.equal(failureWhy(400, { error: "turns is more than 50" }),
               "sessions' page server refused the request: turns is more than 50");
});

// -- the feed -----------------------------------------------------------------------------------

test("a 200 gives the value", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: WINDOW }]);
  assert.equal(feed.loading, true);
  const reading = feed.refresh();
  assert.equal(feed.reading, true);
  await reading;
  assert.equal(feed.loading, false);
  assert.equal(feed.reading, false);
  assert.equal(feed.value.session, "s1");
  assert.ok(changes() >= 2);
});

test("a 422 is the page server's refusal: its reason, and the last value dropped", async () => {
  const { feed } = feedOf([{ status: 200, data: WINDOW },
                           { status: 422, data: { refused: "before is a byte offset in a claude file" } }]);
  await feed.refresh();
  await feed.refresh();
  assert.equal(feed.value, null);
  assert.equal(feed.failure, null);
  assert.equal(feed.refused, "before is a byte offset in a claude file");
});

test("a failure keeps the last value; the next 200 clears it", async () => {
  const { feed } = feedOf([{ status: 200, data: WINDOW }, { status: 500, data: null },
                           { status: 200, data: WINDOW }]);
  await feed.refresh();
  await feed.refresh();
  assert.equal(feed.failure.status, 500);
  assert.equal(feed.value.session, "s1");
  await feed.refresh();
  assert.equal(feed.failure, null);
});

test("a 200 whose body is not the shape is a failure", async () => {
  const { feed } = feedOf([{ status: 200, data: { shape: 1 } }]);
  await feed.refresh();
  assert.equal(feed.failure.status, 200);
  assert.equal(feed.value, null);
});

test("a 404 is gone", async () => {
  const { feed } = feedOf([{ status: 404, data: null }]);
  await feed.refresh();
  assert.equal(feed.gone, true);
});

test("a 503 is read again 1 s apart, up to three times, then is a failure", async () => {
  const busy = { status: 503, data: null };
  const { feed, reads, waits } = feedOf([busy, busy, busy, busy, { status: 200, data: WINDOW }]);
  await feed.refresh();
  assert.equal(reads.length, RETRIES + 1);
  assert.deepEqual(waits, [1000, 1000, 1000]);
  assert.equal(feed.failure.status, 503);
});

test("a refresh while one runs makes one more read after it", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: WINDOW }, { status: 200, data: WINDOW }]);
  const first = feed.refresh();
  const second = feed.refresh();
  feed.refresh();
  await Promise.all([first, second]);
  assert.equal(reads.length, 2);
});

test("a closed feed drops a later answer", async () => {
  let release;
  const feed = new Feed(() => new Promise((done) => { release = done; }), parseWindow, () => {});
  const reading = feed.refresh();
  feed.close();
  release({ status: 200, data: WINDOW });
  await reading;
  assert.equal(feed.value, null);
  assert.equal(feed.closed, true);
});
