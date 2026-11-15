import assert from "node:assert/strict";
import test from "node:test";

import {
  between, parseThread, refreshed, THREAD_FIRST, THREAD_MORE, ThreadReads, threadPath, threadView, withEarlier,
} from "../src/thread-model.ts";

const when = (at) => new Date(at * 1000).toISOString().slice(11, 16);
const at = (hhmm) => Date.parse(`2026-11-12T${hhmm}:00Z`) / 1000;

const message = (id, fields = {}) => ({
  message_id: id, at: at("18:28"), author: "planner", author_tier: "worker", to: ["builder-1"], posture: "once",
  reply_to: null, text: "Is the pagination branch ready?", withdrawn: false, rows: [], ...fields,
});
const waiting = (qualified, fields = {}) => ({ qualified, state: "waiting", held: false, takeable: false, ...fields });
const row = (qualified, fields = {}) => ({
  qualified, name: qualified.split("@")[0], scope: null, live: true, kind: null, module: null, tier: "worker",
  adapter: "claude", directors: [], session_id: `s-${qualified}`, bound_at: 0, awaiting: [], cache: null, ...fields,
});
const answer = (fields = {}) => ({ state: "up", detail: null, today: "2026-11-12", messages: [], count_today: 0,
                                   earlier: null, changed: null, ...fields });
const facts = (fields = {}) => ({ a: "builder-1", b: "planner", roster: [row("planner"), row("builder-1")], owedBy: [],
                                  him: new Set(["operator"]), cache: true, when, ...fields });
const held = (messages, fields = {}) => ({ messages, earlier: null, count_today: 0, ...fields });

const PLAN = [
  message("1000000000000000010", { at: at("12:58"), text: "The plan is in docs/plans." }),
  message("1000000000000000011", { at: at("13:20"), author: "builder-1", to: ["planner"], reply_to: "1000000000000000010",
                                   text: "On it. Do we keep offset?" }),
  message("1000000000000000012", { at: at("13:24"), to: ["builder-1"], posture: "none", reply_to: "1000000000000000011",
                                   text: "Keep offset for one release." }),
  message("1000000000000000013", { at: at("18:28"), rows: [waiting("builder-1")], text: "Is the branch ready?" }),
];

test("the answer read: messages, today's count, earlier and changed, and what isn't one", () => {
  const got = parseThread(answer({ messages: [PLAN[0]], count_today: 4, earlier: { count: 31, first_at: at("09:45") },
                                   changed: [{ message_id: "1000000000000000013", rows: [waiting("builder-1")] },
                                             { message_id: 13, rows: [] }] }));
  assert.deepEqual([got.messages.length, got.count_today, got.earlier, got.changed],
                   [1, 4, { count: 31, first_at: at("09:45") },
                    [{ message_id: "1000000000000000013", rows: [waiting("builder-1")] }]]);
  assert.equal(parseThread(answer({ earlier: { count: 0, first_at: 1 } })).earlier, null);
  assert.equal(parseThread(answer()).changed, null);
  const failed = parseThread({ state: "error", detail: "the record can't be read", messages: [PLAN[0]] });
  assert.deepEqual([failed.messages, failed.detail], [[], "the record can't be read"]);
  assert.equal(parseThread({ state: "maybe" }), null);
});

test("the read's route: the pair or one address, the cursors, the limit", () => {
  assert.equal(threadPath({ a: "builder-1", b: "planner", limit: THREAD_FIRST }),
               "api/thread?a=builder-1&b=planner&limit=6");
  assert.equal(threadPath({ a: "builder-2@kestrel-web", b: null, after: "12" }), "api/thread?a=builder-2%40kestrel-web&after=12");
  assert.equal(threadPath({ a: "a", b: "b", before: "10", limit: THREAD_MORE }), "api/thread?a=a&b=b&before=10&limit=20");
});

test("the drawn thread: the header, each message's wants, the unread hold, the foot", () => {
  const view = threadView(held(PLAN, { count_today: 4 }),
                          facts({ roster: [row("planner", { awaiting: ["builder-1"] }), row("builder-1")] }));
  assert.equal(view.header, "builder-1 and planner · 4 messages today · builder-1 owes one reply");
  assert.deepEqual(view.messages.map((m) => [m.author, m.rest]), [
    ["planner", "· 12:58 · wants one reply"],
    ["builder-1", "· 13:20 · replies, and asks for one reply"],
    ["planner", "· 13:24 · replies · wants no reply"],
    ["planner", "· 18:28 · wants one reply · not read yet, held until builder-1's next turn"],
  ]);
  assert.deepEqual([view.write, view.lastId, view.earlier], [["builder-1", "planner"], "1000000000000000013", null]);
});

test("the header: one message, none today, nobody owing; an unbound reader and no cache", () => {
  const one = threadView(held([PLAN[3]], { count_today: 1 }), facts({ roster: [row("planner"), row("builder-1", { live: false })] }));
  assert.equal(one.header, "builder-1 and planner · 1 message today");
  assert.equal(one.messages[0].rest, "· 18:28 · wants one reply · not read yet, builder-1 isn't running");
  const none = threadView(held([PLAN[3]]), facts({ cache: false }));
  assert.equal(none.header, "builder-1 and planner");
  assert.equal(none.messages[0].rest, "· 18:28 · wants one reply · not read yet");
});

test("with him: you, you owe one reply, no Message to yourself; another recipient's row is not this thread's", () => {
  const asks = message("1000000000000000020", { author: "reviewer", to: ["operator", "planner"],
                                                rows: [waiting("operator"), waiting("planner")] });
  const mine = message("1000000000000000021", { author: "operator", author_tier: "operator", to: ["reviewer"],
                                                posture: "none", reply_to: null, withdrawn: true });
  const view = threadView(held([asks, mine], { count_today: 2 }),
                          facts({ a: "operator", b: "reviewer", owedBy: ["reviewer"], roster: [row("reviewer")] }));
  assert.equal(view.header, "you and reviewer · 2 messages today · you owe one reply");
  assert.deepEqual(view.messages.map((m) => [m.author, m.rest]), [
    ["reviewer", "· 18:28 · wants one reply"], ["you", "· 18:28 · wants no reply · taken back"]]);
  assert.deepEqual(view.write, ["reviewer"]);
});

test("All mail with one address: its header, every recipient's row, Message to it", () => {
  const view = threadView(held([PLAN[3]], { count_today: 9 }), facts({ a: "planner", b: null }));
  assert.equal(view.header, "All mail with planner · 9 messages today");
  assert.equal(view.messages[0].rest, "· 18:28 · wants one reply · not read yet, held until builder-1's next turn");
  assert.deepEqual(view.write, ["planner"]);
});

test("Show earlier: the count, back to its time, and the cursor below the oldest held", () => {
  const view = threadView(held(PLAN.slice(2), { earlier: { count: 31, first_at: at("09:45") } }), facts());
  assert.deepEqual(view.earlier, { text: "31 messages, back to 09:45", before: "1000000000000000012" });
  const more = withEarlier(held(PLAN.slice(2)), answer({ messages: PLAN.slice(0, 3), earlier: { count: 29, first_at: 5 } }));
  assert.deepEqual([more.messages.map((m) => m.message_id.slice(-2)), more.earlier],
                   [["10", "11", "12", "13"], { count: 29, first_at: 5 }]);
});

test("a refresh adds the newer messages and sets the pair's rows from changed, keeping another recipient's", () => {
  const two = message("1000000000000000013", { to: ["builder-1", "reviewer"],
                                               rows: [waiting("builder-1"), waiting("reviewer")] });
  const newer = message("1000000000000000014", { author: "builder-1", to: ["planner"], at: at("19:00") });
  const got = refreshed(held([PLAN[2], two], { count_today: 2 }), answer({ messages: [newer], count_today: 3, changed: [] }),
                        "planner", "builder-1");
  assert.deepEqual(got.messages.map((m) => [m.message_id.slice(-2), m.rows.map((r) => r.qualified)]),
                   [["12", []], ["13", ["reviewer"]], ["14", []]]);
  assert.equal(got.count_today, 3);
  // By or to one address, changed carries every row: a message it doesn't list has none.
  const one = refreshed(held([two]), answer({ changed: [{ message_id: "1000000000000000013",
                                                         rows: [{ ...waiting("builder-1"), state: "withdrawn" }] }] }),
                        null, "planner");
  assert.deepEqual(one.messages[0].rows.map((r) => [r.qualified, r.state]), [["builder-1", "withdrawn"]]);
});

test("between: by one with a tag naming the other; him by his mark and the tag operator", () => {
  const him = new Set(["operator", "the-desk"]);
  assert.equal(between(PLAN[0], "builder-1", "planner", him), true);
  assert.equal(between(message("1", { to: ["reviewer"] }), "builder-1", "planner", him), false);
  assert.equal(between(message("2", { author: "the-desk", author_tier: "operator", to: ["reviewer"] }),
                       "operator", "reviewer", him), true);
  assert.equal(between(message("3", { author: "reviewer", to: ["operator"] }), "reviewer", "the-desk", him), true);
});

const flush = () => new Promise((done) => setImmediate(done));

/** A stub of the page's `get`: answers in order. */
function getter(...answers) {
  const calls = [];
  const get = (path) => {
    calls.push(path);
    return Promise.resolve(answers.shift() ?? { status: 0, data: null });
  };
  return { get, calls };
}

test("the reads: the first, a refresh above the newest held, Show earlier, a failure kept as a line", async () => {
  const ok = (fields) => ({ status: 200, data: answer(fields) });
  const stub = getter(ok({ messages: PLAN.slice(2), count_today: 2, earlier: { count: 2, first_at: at("12:58") } }),
                      ok({ messages: [], count_today: 2, changed: [] }),
                      ok({ messages: PLAN.slice(0, 2), earlier: null }),
                      { status: 502, data: null });
  let changes = 0;
  const reads = new ThreadReads("builder-1", "planner", { get: stub.get, onChange: () => { changes += 1; },
                                                          newestKnown: () => "1000000000000000099" });
  assert.equal(reads.loading, true);
  await reads.refresh();
  assert.deepEqual([reads.loading, reads.held.messages.length, reads.today], [false, 2, "2026-11-12"]);
  await reads.refresh();
  assert.equal(stub.calls[1], "api/thread?a=builder-1&b=planner&after=1000000000000000013");
  assert.deepEqual(reads.held.messages[1].rows, []);
  await reads.showEarlier();
  assert.equal(stub.calls[2], "api/thread?a=builder-1&b=planner&before=1000000000000000012&limit=20");
  assert.deepEqual([reads.held.messages.length, reads.held.earlier, reads.showing], [4, null, false]);
  await reads.showEarlier();
  assert.equal(stub.calls.length, 3);
  await reads.refresh();
  assert.equal(reads.failure, "comms' page server answered 502");
  assert.equal(reads.held.messages.length, 4);
  assert.ok(changes >= 5);
});

test("an empty thread's refresh reads above the newest message the lists held; a closed one drops its answers", async () => {
  const stub = getter({ status: 200, data: answer() }, { status: 200, data: answer({ messages: [PLAN[3]], changed: [] }) });
  const reads = new ThreadReads("builder-1", "planner", { get: stub.get, onChange: () => {},
                                                          newestKnown: () => "1000000000000000012" });
  await reads.refresh();
  await reads.refresh();
  assert.equal(stub.calls[1], "api/thread?a=builder-1&b=planner&after=1000000000000000012");
  assert.equal(reads.held.messages.length, 1);
  const closed = new ThreadReads("a", "b", { get: getter({ status: 200, data: answer({ messages: [PLAN[0]] }) }).get,
                                             onChange: () => {}, newestKnown: () => null });
  const out = closed.refresh();
  closed.close();
  await out;
  await flush();
  assert.equal(closed.held, null);
});
