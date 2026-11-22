import assert from "node:assert/strict";
import test from "node:test";

import { Feed, parseBoard, readRecord, RETRIES } from "../src/feed.ts";

const settled = () => new Promise((done) => setImmediate(done));

const ROUND = { name: "kestrel/a", project: "kestrel", slug: "a", group: "moving", phrase: [], mine: 0, rev: "r1",
                lines: [{ kind: "round", stage: null, branch: null, fold: null, phrase: [], cells: [] }],
                folds: { more: 0, landed: 0, not_started: { n: 0, ready: 0, held: 0, child: 0 } } };
const BOARD = {
  today: "2026-11-21", home: "/home/u", read_at: "2026-11-21T12:00:00Z", state: "up", detail: null,
  columns: [{ id: "design", label: "design", part: "round" }],
  waiting: { questions: [{ holder: "kestrel/a", id: "Q1", text: "Which?" }], decisions: [], waits: [], not_shown: [],
             parked: 1 },
  recent: [], rounds: [ROUND], closed: [], unreadable: [],
};
const UP = { now: 1000, ...BOARD };

/** A feed over `answers`, read in order; the clock reads 400. */
function feedOf(answers) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parseBoard, () => { changes += 1; },
                        { wait: async (ms) => { waits.push(ms); }, clock: () => 400 });
  return { feed, reads, waits, changes: () => changes };
}

test("parseBoard takes the route's and the topic's value", () => {
  assert.deepEqual(parseBoard(BOARD), BOARD);
  assert.deepEqual(parseBoard(UP), BOARD);
  assert.equal(parseBoard({ ...BOARD, home: "" }).home, null);
  assert.equal(parseBoard({ ...BOARD, detail: 3 }).detail, null);
});

test("parseBoard refuses what isn't the shape, and drops rows it can't draw", () => {
  for (const bad of [null, 5, "x", [], { ...BOARD, today: 1 }, { ...BOARD, rounds: {} }, { ...BOARD, waiting: [] },
                     { ...BOARD, columns: null }, { ...BOARD, state: null }]) {
    assert.equal(parseBoard(bad), null, JSON.stringify(bad));
  }
  const kept = parseBoard({ ...BOARD, rounds: [ROUND, { name: "kestrel/b", lines: [] }, { lines: ROUND.lines }, null, 7],
                            waiting: { ...BOARD.waiting, questions: [{ holder: "x" }, ...BOARD.waiting.questions] } });
  assert.deepEqual(kept.rounds, [ROUND]);
  assert.deepEqual(kept.waiting.questions, BOARD.waiting.questions);
  assert.deepEqual(parseBoard({ ...BOARD, recent: undefined, closed: "x", unreadable: [{ reason: "r" }] }).closed, []);
});

test("a 200 gives the value and the server clock's offset", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: UP }]);
  assert.equal(feed.loading, true);
  await feed.refresh();
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value, BOARD);
  assert.equal(feed.offset, 600);
  assert.equal(feed.readAt, 1000);
  assert.equal(feed.now(), 1000);
  assert.equal(feed.failure, null);
  assert.equal(changes(), 1);
});

test("a 503 is read again after 1 s; four 503s are a failure", async () => {
  const once = feedOf([{ status: 503, data: null }, { status: 200, data: UP }]);
  await once.feed.refresh();
  assert.equal(once.reads.length, 2);
  assert.deepEqual(once.waits, [1000]);
  assert.equal(once.feed.failure, null);
  const busy = { status: 503, data: null };
  const always = feedOf([busy, busy, busy, busy, { status: 200, data: UP }]);
  await always.feed.refresh();
  assert.equal(always.reads.length, RETRIES + 1);
  assert.deepEqual(always.feed.failure, { status: 503, why: "rounds' page server answered 503" });
});

test("every other answer is a failure, and the last value is kept", async () => {
  for (const [answer, why] of [
    [{ status: 500, data: null }, "rounds' page server answered 500"],
    [{ status: 0, data: null }, "no answer from the shell"],
    [{ status: 200, data: null }, "rounds' page server sent an answer that can't be read"],
    [{ status: 200, data: BOARD }, "rounds' page server sent an answer that can't be read"],
  ]) {
    const { feed } = feedOf([{ status: 200, data: UP }, answer]);
    await feed.refresh();
    await feed.refresh();
    assert.equal(feed.failure?.why, why, JSON.stringify(answer));
    assert.equal(feed.value.rounds.length, 1);
  }
});

test("a 404 means the module went away", async () => {
  const { feed } = feedOf([{ status: 404, data: null }]);
  await feed.refresh();
  assert.equal(feed.gone, true);
  assert.equal(feed.failure, null);
});

test("a topic event replaces the value while the last read succeeded; otherwise it reads the route", async () => {
  const good = feedOf([{ status: 200, data: UP }]);
  await good.feed.refresh();
  good.feed.topic({ ...BOARD, rounds: [] });
  assert.deepEqual(good.feed.value.rounds, []);
  assert.equal(good.reads.length, 1);

  const odd = feedOf([{ status: 200, data: UP }, { status: 200, data: UP }]);
  await odd.feed.refresh();
  odd.feed.topic({ nope: true });
  await settled();
  assert.equal(odd.reads.length, 2);

  const failed = feedOf([{ status: 500, data: null }, { status: 200, data: UP }]);
  await failed.feed.refresh();
  failed.feed.topic(BOARD);
  await settled();
  assert.equal(failed.reads.length, 2);
  assert.equal(failed.feed.failure, null);
});

test("a refresh during a read makes one more read; a closed feed drops what comes after", async () => {
  const { feed, reads } = feedOf([{ status: 200, data: UP }, { status: 200, data: UP }, { status: 200, data: UP }]);
  const first = feed.refresh();
  feed.refresh();
  feed.refresh();
  await first;
  assert.equal(reads.length, 2);
  const closing = feedOf([{ status: 200, data: UP }]);
  const pending = closing.feed.refresh();
  closing.feed.close();
  await pending;
  closing.feed.topic(BOARD);
  assert.equal(closing.changes(), 0);
});

test("a record read: the record, gone, or the failure, with the 503 retries", async () => {
  const answers = (list) => async () => list.shift();
  const record = { round: "kestrel/a", column: "design" };
  assert.deepEqual(await readRecord(answers([{ status: 200, data: { gone: false, record } }])), { kind: "record", record });
  assert.deepEqual(await readRecord(answers([{ status: 200, data: { gone: true, record: null } }])), { kind: "gone" });
  assert.deepEqual(await readRecord(answers([{ status: 503, data: null }, { status: 200, data: { gone: true, record: null } }]),
                                    { wait: async () => {} }), { kind: "gone" });
  assert.deepEqual(await readRecord(answers([{ status: 400, data: { error: "x" } }])),
                   { kind: "failed", failure: { status: 400, why: "rounds' page server answered 400" } });
  assert.deepEqual(await readRecord(answers([{ status: 200, data: { record } }])),
                   { kind: "failed", failure: { status: 200, why: "rounds' page server sent an answer that can't be read" } });
});
