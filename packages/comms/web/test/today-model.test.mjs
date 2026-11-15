import assert from "node:assert/strict";
import test from "node:test";

import {
  authorIn, idBelow, joined, messageRow, messagesPath, messagesView, MOST, parseMessagesAnswer, reread, rereadPaths,
  todayOf,
} from "../src/today-model.ts";

const CLOCK = { time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
                day: (at) => new Date(at * 1000).toISOString().slice(0, 10) };
const when = CLOCK.time;
const at = (hhmm, day = "2026-11-12") => Date.parse(`${day}T${hhmm}:00Z`) / 1000;
const TODAY = "2026-11-12";

const message = (id, fields = {}) => ({
  message_id: id, at: at("18:28"), author: "planner", author_tier: "worker", to: ["builder-1"], posture: "once",
  reply_to: null, text: "Is the pagination branch ready?", withdrawn: false, rows: [], ...fields,
});
const waiting = (qualified) => ({ qualified, state: "waiting", held: false, takeable: false });
const readers = (fields = {}) => ({ bound: (q) => q === "builder-1" || q === "reviewer", cache: true,
                                    him: (tag) => tag === "operator", ...fields });
const day = (messages, more = 0, earlier = null, d = TODAY) => ({ day: d, messages, more, earlier });

test("the answer read: messages, more and earlier, and what isn't one", () => {
  const earlier = { day: "2026-11-11", count: 447, first_at: at("09:00", "2026-10-12"), before: "1000000000000000101" };
  const got = parseMessagesAnswer({ state: "up", detail: null, today: TODAY, day: TODAY, messages: [message("1000000000000000200")],
                                    more: 38, earlier });
  assert.deepEqual([got.messages.length, got.more, got.earlier, got.today], [1, 38, earlier, TODAY]);
  assert.equal(parseMessagesAnswer({ state: "up", messages: [], more: 0, earlier: { ...earlier, before: 1000 } }).earlier, null);
  assert.equal(parseMessagesAnswer({ state: "up", messages: [], more: 0, earlier: { ...earlier, count: 0 } }).earlier, null);
  const failed = parseMessagesAnswer({ state: "error", detail: "the record can't be read", messages: [message("1")] });
  assert.deepEqual([failed.messages, failed.detail], [[], "the record can't be read"]);
  assert.equal(parseMessagesAnswer({ state: "maybe" }), null);
  assert.equal(parseMessagesAnswer(null), null);
});

test("ids compare as strings of digits, never as numbers", () => {
  assert.equal(idBelow("999", "1000"), true);
  assert.equal(idBelow("9223372036854775807", "9223372036854775808"), true);
  assert.equal(idBelow("9223372036854775808", "9223372036854775807"), false);
  assert.deepEqual(joined([message("9223372036854775808")], [message("9223372036854775807"), message("9223372036854775808")])
    .map((m) => m.message_id), ["9223372036854775808", "9223372036854775807"]);
});

test("the read's route: the day, the cursor, the limit within its bounds", () => {
  assert.equal(messagesPath({ limit: 12 }), "api/messages?limit=12");
  assert.equal(messagesPath({ day: "2026-11-11", before: "1000000000000000101", limit: 30 }),
               "api/messages?day=2026-11-11&before=1000000000000000101&limit=30");
  assert.equal(messagesPath({ limit: 900 }), "api/messages?limit=500");
});

test("a message row: names, each recipient's read state, the time, the text on one line, its thread", () => {
  const row = messageRow(message("1", { rows: [waiting("builder-1")] }), readers(), when);
  assert.deepEqual(row, { message_id: "1", name: "planner", where: "to builder-1 · not read yet, held until its next turn",
                          time: "18:28", text: "Is the pagination branch ready?", thread: { a: "planner", b: "builder-1" } });
  const unbound = messageRow(message("2", { to: ["docs-writer"], rows: [waiting("docs-writer")] }), readers(), when);
  assert.equal(unbound.where, "to docs-writer · not read yet, docs-writer isn't running");
  const noCache = messageRow(message("3", { rows: [waiting("builder-1")] }), readers({ cache: false }), when);
  assert.equal(noCache.where, "to builder-1 · not read yet");
  const handed = messageRow(message("4", { rows: [{ ...waiting("builder-1"), state: "handed" }] }), readers(), when);
  assert.equal(handed.where, "to builder-1 · not read yet");
  const read = messageRow(message("5"), readers(), when);
  assert.equal(read.where, "to builder-1");
});

test("his messages read you, a message to him reads to you, a withdrawn one taken back", () => {
  const his = messageRow(message("1", { author: "the-desk", author_tier: "operator", to: ["reviewer"],
                                        text: "Review from\\n the branch head.\n\nThanks", rows: [waiting("reviewer")],
                                        withdrawn: true }), readers(), when);
  assert.equal(his.name, "you");
  assert.equal(his.where, "to reviewer · taken back");
  assert.equal(his.text, "Review from\\n the branch head. Thanks");
  const toHim = messageRow(message("2", { author: "reviewer", to: ["operator"] }), readers(), when);
  assert.deepEqual([toHim.where, toHim.thread], ["to you", { a: "reviewer", b: "operator" }]);
  const many = messageRow(message("3", { to: ["builder-1", "reviewer"], rows: [waiting("builder-1"), waiting("reviewer")] }),
                          readers(), when);
  assert.equal(many.where, "to builder-1 and reviewer · not read yet, held until its next turn");
});

test("Today with Earlier today's count and cursor; no Today with no message today", () => {
  const earlier = { day: "2026-11-11", count: 447, first_at: at("09:00", "2026-10-12"), before: "1000000000000000101" };
  const view = messagesView(day([message("1000000000000000200"), message("1000000000000000150")], 38, earlier), [],
                            readers(), when, TODAY, CLOCK);
  assert.deepEqual(view.groups.map((g) => [g.key, g.label, g.rows.length, g.more, g.show]), [
    ["today", "Today", 2, "38 more messages", { day: null, before: "1000000000000000150" }]]);
  assert.deepEqual([view.before.label, view.before.text], ["Before today", "447 messages, back to Mon Oct 12"]);
  const quiet = messagesView(day([], 0, earlier), [], readers(), when, TODAY, CLOCK);
  assert.deepEqual([quiet.groups, quiet.before.label], [[], "Before today"]);
  const none = messagesView(day([], 0, null), [], readers(), when, TODAY, CLOCK);
  assert.deepEqual(none, { groups: [], before: null });
  const one = messagesView(day([message("1")], 1, null), [], readers(), when, TODAY, CLOCK);
  assert.equal(one.groups[0].more, "1 more message");
});

test("a day added sits under its own label, and Before moves down carrying the next earlier", () => {
  const first = { day: "2026-11-11", count: 447, first_at: at("09:00", "2026-10-12"), before: "1000000000000000101" };
  const next = { day: "2026-11-09", count: 400, first_at: at("09:00", "2026-10-12"), before: "1000000000000000051" };
  const yesterday = day([message("1000000000000000100", { at: at("21:15", "2026-11-11") })], 5, next, "2026-11-11");
  const view = messagesView(day([message("1000000000000000200")], 0, first), [yesterday], readers(), when, TODAY, CLOCK);
  assert.deepEqual(view.groups.map((g) => [g.key, g.label, g.more, g.show]), [
    ["today", "Today", null, null],
    ["day:2026-11-11", "Yesterday", "5 more messages", { day: "2026-11-11", before: "1000000000000000100" }]]);
  assert.deepEqual([view.before.label, view.before.text, view.before.earlier], ["Before yesterday",
                                                                              "400 messages, back to Mon Oct 12", next]);
  const older = messagesView(null, [day([message("1000000000000000050")], 0, null, "2026-11-01")], readers(), when, TODAY,
                             CLOCK);
  assert.deepEqual([older.groups[0].label, older.before], ["Sun Nov 1", null]);
});

test("a day added is read again from its cursor, as many as it shows, so its read states follow the books", () => {
  const cursor = "1000000000000000101";
  const held = { ...day([message("1000000000000000100", { rows: [waiting("builder-1")] }),
                         message("1000000000000000099", { rows: [waiting("reviewer")] })], 5, null, "2026-11-11"), cursor };
  assert.deepEqual(rereadPaths(held), [`api/messages?day=2026-11-11&before=${cursor}&limit=2`]);
  // Today is read again by its own read, with as many as it shows.
  assert.deepEqual(rereadPaths(day([message("1")])), []);
  // Past one read's most, the next read starts below the last message of the one before.
  const ids = Array.from({ length: MOST + 3 }, (_, i) => String(2000 - i));
  const big = { ...day(ids.map((id) => message(id)), 0, null, "2026-11-10"), cursor: "2001" };
  assert.deepEqual(rereadPaths(big), [`api/messages?day=2026-11-10&before=2001&limit=${MOST}`,
                                      `api/messages?day=2026-11-10&before=${ids[MOST - 1]}&limit=3`]);
  // The first message has since been read, the second taken back; one not read again stays.
  const fresh = [message("1000000000000000100", { rows: [] }),
                 message("1000000000000000099", { rows: [{ ...waiting("reviewer"), state: "withdrawn" }], withdrawn: true })];
  const now = reread(held, fresh);
  assert.deepEqual(now.messages.map((m) => messageRow(m, readers(), when).where),
                   ["to builder-1", "to builder-1 · taken back"]);
  assert.deepEqual([now.more, now.cursor, now.day], [5, cursor, "2026-11-11"]);
  assert.deepEqual(reread(held, fresh.slice(0, 1)).messages[1], held.messages[1]);
  const view = messagesView(day([]), [now], readers(), when, TODAY, CLOCK);
  assert.deepEqual(view.groups[0].rows.map((r) => r.where), ["to builder-1", "to builder-1 · taken back"]);
});

test("the author of a message the page holds, for a reply address", () => {
  const today = day([message("1000000000000000200", { author: "reviewer" })]);
  assert.equal(authorIn([today, null], "1000000000000000200"), "reviewer");
  assert.equal(authorIn([today], "1000000000000000201"), null);
  const read = (value) => ({ loading: false, gone: false, failure: null, value });
  assert.equal(todayOf(read(null)), null);
  assert.equal(todayOf(read({ state: "error", messages: [] })), null);
  assert.deepEqual(todayOf(read({ state: "up", day: TODAY, messages: [], more: 0, earlier: null })), day([]));
});
