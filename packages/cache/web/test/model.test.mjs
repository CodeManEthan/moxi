import assert from "node:assert/strict";
import test from "node:test";

import {
  Arrivals, CACHE_LINE, coldAt, countdown, EMPTY_LINE, isCold, isOld, keep, keyOf, lastLines, NAME_WAIT_MS,
  NAMES_FAILED, NamesFeed, NO_READING_LINE, NOT_READING_LINE, openedModel, pageModel, parseNames, parsePageClocks,
  parseSession, READ_TAIL, sortRows, STOPPED_LINE, timed, untimedFact, WAITING_LINE, WAITING_MS, WATCH_COMMAND,
  watcherRow, watching, namesBy,
} from "../src/model.ts";
import { sessionName } from "../src/words.ts";

const iso = (at) => new Date(at * 1000).toISOString();
const UTC = { time: (at) => iso(at).slice(11, 16), seconds: (at) => iso(at).slice(11, 19), day: (at) => iso(at).slice(0, 10) };
const at = (text) => Date.parse(`${text}Z`) / 1000;
const TODAY = "2026-11-10";
const NOW = at("2026-11-10T19:09:00");

const row = (session, extra = {}) => ({ session, harness: "claude", kind: "warm", last_request: NOW - 60, ttl: 3600,
  warn_before: 900, seen_at: NOW - 5, stale_after: 30, code: null, ...extra });
const reading = (tick, listed, stale = 30) => ({ tick_at: tick, listed_at: listed, stale_after: stale });
const clocks = (rows, extra = {}) => ({ state: "up", detail: null, today: TODAY, reading: reading(NOW - 2, NOW - 2), clocks: rows, ...extra });
const feedOf = (value, extra = {}) => ({ loading: false, gone: false, failure: null, value, ...extra });
const item = (session, extra = {}) => ({ session, harness: "claude", name: null, folder: null, started: null, known: true, ...extra });
const namesUp = (items, extra = {}) => ({ state: "up", today: TODAY, home: "/home/alice", sessions: items, ...extra });
/** The rule's own words for a Claude Code record with no name, folder or start. */
const NO_NAME = sessionName({ harness: "claude", name: null, folder: null, started: null }, TODAY, UTC).name;

/** Every row drawn at once: arrivals that held them all. */
function allShown(rows) {
  const arrivals = new Arrivals();
  arrivals.names(namesUp(rows.map((one) => item(one.session, { known: false }))));
  return arrivals;
}

function page(value, extra = {}) {
  const feed = extra.feed ?? feedOf(value);
  const now = extra.now ?? NOW;
  const kept = "kept" in extra ? extra.kept : keep(null, feed, now);
  return pageModel({ feed, kept, now, names: extra.names ?? { answer: null, failed: false },
                     arrivals: extra.arrivals ?? allShown(value?.clocks ?? []), nowMs: extra.nowMs ?? 0,
                     waitedMs: extra.waitedMs ?? 0, opened: extra.opened ?? new Set(),
                     untimedShown: extra.untimedShown ?? false, clock: UTC });
}

test("timed: warm or cold with all four values finite", () => {
  assert.equal(timed(row("a")), true);
  assert.equal(timed(row("a", { kind: "cold" })), true);
  for (const extra of [{ warn_before: null }, { stale_after: null }, { ttl: null }, { last_request: null },
                       { kind: "fresh" }, { kind: "near" }, { kind: "unknown" }, { ttl: Infinity }]) {
    assert.equal(timed(row("a", extra)), false, JSON.stringify(extra));
    assert.equal(coldAt(row("a", extra)), null);
  }
});

test("cold at the edge, on the server's clock", () => {
  const one = row("a", { last_request: NOW - 3600 });
  assert.equal(coldAt(one), NOW);
  assert.equal(isCold(one, NOW - 0.001), false);
  assert.equal(isCold(one, NOW), true);
  // the record's own kind does not decide: the clock does
  assert.equal(isCold(row("a", { kind: "cold", last_request: NOW - 60 }), NOW), false);
  const before = page(clocks([one]), { now: NOW - 1 });
  assert.deepEqual(before.groups.map((group) => group.label), ["1 warm"]);
  const after = page(clocks([one]), { now: NOW });
  assert.deepEqual(after.groups.map((group) => group.label), ["1 cold"]);
  assert.equal(after.groups[0].rows[0].fact, "cold since 19:09");
});

test("both orders, and their ties by session id", () => {
  const rows = [
    row("w-late", { last_request: NOW - 60 }),
    row("w-b", { last_request: NOW - 600 }),
    row("w-a", { last_request: NOW - 600 }),
    row("c-old", { last_request: NOW - 7200 }),
    row("c-b", { last_request: NOW - 3700 }),
    row("c-a", { last_request: NOW - 3700 }),
    row("u-b", { kind: "fresh", last_request: null }),
    row("u-a", { kind: "unknown" }),
  ];
  const sorted = sortRows(rows, NOW);
  assert.deepEqual(sorted.warm.map((one) => one.session), ["w-a", "w-b", "w-late"]);
  assert.deepEqual(sorted.cold.map((one) => one.session), ["c-a", "c-b", "c-old"]);
  assert.deepEqual(sorted.untimed.map((one) => one.session), ["u-a", "u-b"]);
  const drawn = page(clocks(rows));
  assert.deepEqual(drawn.groups.map((group) => group.rows.map((one) => one.session)),
                   [["w-a", "w-b", "w-late"], ["c-a", "c-b", "c-old"]]);
});

test("group counts come from the clocks answer, not the rows drawn so far", () => {
  const rows = [row("a"), row("b"), row("c", { last_request: NOW - 4000 })];
  const arrivals = new Arrivals();
  arrivals.clocks(rows, 0);
  const waiting = page(clocks(rows), { arrivals, nowMs: 500 });
  assert.deepEqual(waiting.groups.map((group) => [group.label, group.count, group.rows.length]),
                   [["2 warm", 2, 0], ["1 cold", 1, 0]]);
  assert.equal(waiting.sentences.length, 0);
  assert.equal(waiting.foot.reading, "Last reading 19:08");
  // a group with none is not drawn
  assert.deepEqual(page(clocks([row("a")])).groups.map((group) => group.kind), ["warm"]);
  assert.deepEqual(page(clocks([row("a", { last_request: NOW - 4000 })])).groups.map((group) => group.kind), ["cold"]);
});

test("a row's first line and line two, with null parts and with names", () => {
  const rows = [row("s1"), row("s2"), row("s3"), row("s4")];
  const names = namesUp([
    item("s1", { name: "orders-skeleton", folder: "/home/alice/kestrel-web" }),
    item("s2", { folder: "/home/alice/orbit-cli", started: at("2026-11-10T16:27:00") }),
    item("s3", { known: false }),
  ]);
  const drawn = page(clocks(rows), { names: { answer: names, failed: false } });
  const byId = Object.fromEntries(drawn.groups[0].rows.map((one) => [one.session, one]));
  assert.deepEqual([byId.s1.name, byId.s1.where], ["orders-skeleton", "kestrel-web"]);
  assert.deepEqual([byId.s2.name, byId.s2.where], ["orbit-cli · started today 16:27", null]);
  assert.deepEqual([byId.s3.name, byId.s3.where], [NO_NAME, null]);
  assert.deepEqual([byId.s4.name, byId.s4.where], [NO_NAME, null]);
  assert.equal(byId.s1.fact, "warm until 20:08");
  assert.equal(byId.s1.rest, "last request 19:08");
  for (const one of Object.values(byId)) assert.ok(!one.name.includes(one.session));
  // an error answer names nothing, whatever an answer before it said
  const error = page(clocks(rows), { names: { answer: { state: "error", today: TODAY, home: null, sessions: [] }, failed: false } });
  assert.ok(error.groups[0].rows.every((one) => one.name === NO_NAME));
});

test("day words each side of midnight, from the clocks answer's today with no names answer", () => {
  const late = at("2026-11-10T23:30:00");
  const one = row("a", { last_request: late - 60, seen_at: late - 2 });
  const value = clocks([one], { reading: reading(late - 2, late - 2) });
  const drawn = page(value, { now: late });
  assert.equal(drawn.groups[0].rows[0].fact, "warm until tomorrow 00:29");
  assert.equal(drawn.groups[0].rows[0].rest, "last request 23:29");
  assert.equal(drawn.foot.reading, "Last reading 23:29");
  // past midnight, the answer's new today words the same times again
  const next = clocks([one], { today: "2026-11-11", reading: reading(late - 2, late - 2) });
  const after = page(next, { now: late });
  assert.equal(after.groups[0].rows[0].fact, "warm until 00:29");
  assert.equal(after.groups[0].rows[0].rest, "last request yesterday 23:29");
  // the newest today held wins: a names answer of the day after
  const both = page(value, { now: late, names: { answer: namesUp([], { today: "2026-11-11" }), failed: false } });
  assert.equal(both.groups[0].rows[0].fact, "warm until 00:29");
});

test("the countdown each side of a minute, an hour and a day, warm and cold", () => {
  const warmLeft = (left) => countdown(row("a", { last_request: NOW + left - 3600 }), NOW);
  assert.equal(warmLeft(45), "in 45 seconds");
  assert.equal(warmLeft(59.2), "in 1 minute");
  assert.equal(warmLeft(60), "in 1 minute");
  assert.equal(warmLeft(0.4), "in 1 second");
  assert.equal(warmLeft(41 * 60), "in 41 minutes");
  assert.equal(warmLeft(3599), "in 59 minutes");
  const long = (left) => countdown(row("a", { last_request: NOW - 10, ttl: left + 10 }), NOW);
  assert.equal(long(3600), "in 1 hour");
  assert.equal(long(3600 + 240), "in 1 hour 4 minutes");
  assert.equal(long(86399), "in 23 hours 59 minutes");
  assert.equal(long(86400), "in 1 day");
  assert.equal(long(2 * 86400 + 7), "in 2 days");
  const coldFor = (since) => countdown(row("a", { last_request: NOW - since - 3600 }), NOW);
  assert.equal(coldFor(0), "0 seconds ago");
  assert.equal(coldFor(59.9), "59 seconds ago");
  assert.equal(coldFor(60), "1 minute ago");
  assert.equal(coldFor(12 * 60 + 30), "12 minutes ago");
  assert.equal(coldFor(3599), "59 minutes ago");
  assert.equal(coldFor(3600), "1 hour ago");
  assert.equal(coldFor(7200 + 120), "2 hours 2 minutes ago");
  assert.equal(coldFor(86400), "1 day ago");
  assert.equal(countdown(row("a", { ttl: null }), NOW), null);
});

test("the fact of each kind with no clock time, with and without a code", () => {
  assert.equal(untimedFact(row("a", { kind: "fresh" })), "no request yet");
  assert.equal(untimedFact(row("a", { kind: "no-cache" })), "no cache");
  assert.equal(untimedFact(row("a", { kind: "unknown" })), "not read");
  assert.equal(untimedFact(row("a", { kind: "warm", warn_before: null })), "no reading");
  assert.equal(untimedFact(row("a", { kind: "cold", stale_after: null })), "no reading");
  assert.equal(untimedFact(row("a", { kind: "later-kind" })), "no reading");
  const rows = [row("a", { kind: "fresh", code: "no-request" }), row("b", { kind: "no-cache" }), row("t")];
  const hidden = page(clocks(rows));
  assert.deepEqual([hidden.untimed.line, hidden.untimed.act, hidden.untimed.rows.length],
                   ["2 with no cache reading", "Show", 0]);
  const shown = page(clocks(rows), { untimedShown: true });
  assert.equal(shown.untimed.act, "Hide");
  assert.deepEqual(shown.untimed.rows.map((one) => [one.fact, one.code, one.rest]),
                   [["no request yet", "no-request", null], ["no cache", null, null]]);
  assert.equal(page(clocks([row("t")])).untimed, null);
});

test("one record old by its own stale_after beside fresh ones: its Read line, no Watcher row", () => {
  const old = row("old", { seen_at: NOW - 31, stale_after: 30 });
  const rows = [old, row("fresh1"), row("fresh2", { seen_at: NOW - 100, stale_after: 300 })];
  assert.equal(isOld(old, NOW), true);
  assert.equal(isOld(rows[2], NOW), false);
  assert.equal(isOld(row("x", { seen_at: NOW - 1000, stale_after: null }), NOW), false);
  const drawn = page(clocks(rows, { reading: null }));
  assert.equal(drawn.watcher, null);
  assert.equal(drawn.foot.reading, "Last reading 19:08");
  const opened = openedModel(old, NOW, new Map(), null, null, TODAY, UTC);
  assert.deepEqual(opened.lines.at(-1), { label: "Read", value: "19:08:29", tails: [READ_TAIL] });
  assert.ok(!openedModel(rows[1], NOW, new Map(), null, null, TODAY, UTC).lines.some((line) => line.label === "Read"));
  // a record with no clock time is judged old the same way
  const untimedOld = row("u", { kind: "unknown", seen_at: NOW - 60 });
  assert.equal(openedModel(untimedOld, NOW, new Map(), null, null, TODAY, UTC).lines[0].label, "Read");
});

// The same cases as the Python function's test: the page and doctor judge alike.
const rec = (seen, stale = 30) => ({ ...row("r"), seen_at: seen, stale_after: stale });
const WATCHING = [
  [reading(NOW - 2, NOW - 2), [], "fresh", NOW - 2, NOW - 2],
  [null, [rec(NOW - 2)], "fresh", NOW - 2, NOW - 2],
  [reading(NOW - 2, NOW - 600), [rec(NOW - 10)], "fresh", NOW - 10, NOW - 2],
  [reading(NOW - 2, NOW - 600), [], "not-reading", NOW - 600, NOW - 2],
  [reading(NOW - 2, null), [], "not-reading", null, NOW - 2],
  [reading(NOW - 2, null), [rec(NOW - 2, null)], "not-reading", null, NOW - 2],
  [reading(NOW - 600, NOW - 600), [], "stopped", NOW - 600, NOW - 600],
  [reading(NOW - 600, null), [], "stopped", null, NOW - 600],
  [reading(NOW - 900, null), [rec(NOW - 300)], "stopped", NOW - 300, NOW - 300],
  [null, [rec(NOW - 600)], "stopped", NOW - 600, NOW - 600],
  [null, [], "no-reading", null, null],
  [null, [rec(NOW - 2, null)], "no-reading", null, null],
  [null, [rec(NOW - 60, 300), rec(NOW - 40, 30)], "stopped", NOW - 40, NOW - 40],
  [null, [rec(NOW - 40, 30), rec(NOW - 40, 300)], "fresh", NOW - 40, NOW - 40],
  [reading(NOW - 40, NOW - 40, 300), [rec(NOW - 40, 30)], "fresh", NOW - 40, NOW - 40],
  [reading(NOW - 30, NOW - 30), [], "fresh", NOW - 30, NOW - 30],
  [reading(NOW - 30, NOW - 31), [], "not-reading", NOW - 31, NOW - 30],
];

test("the watcher is one outcome for every input", () => {
  for (const [found, records, outcome, atTime, last] of WATCHING) {
    assert.deepEqual(watching(found, records, NOW), { outcome, at: atTime, last },
                     JSON.stringify({ found, records }));
  }
});

test("each outcome on the page: the row, its words, its Copy, the foot", () => {
  const fresh = page(clocks([row("a")], { reading: reading(NOW - 2, NOW - 2) }));
  assert.equal(fresh.watcher, null);
  assert.deepEqual(fresh.foot, { reading: "Last reading 19:08" });

  const notReading = page(clocks([row("a", { seen_at: NOW - 600 })], { reading: reading(NOW - 2, NOW - 600) }));
  assert.deepEqual(notReading.watcher, { what: "Not reading since 18:59", line: NOT_READING_LINE, copy: null });
  assert.deepEqual(notReading.foot, { reading: null });
  assert.equal(notReading.groups[0].label, "1 warm");

  const never = page(clocks([], { reading: reading(NOW - 2, null) }));
  assert.equal(never.watcher.what, "Not reading");
  assert.deepEqual([never.foot, never.sentences], [{ reading: null }, []]);

  const stopped = page(clocks([row("a", { seen_at: NOW - 600 })], { reading: reading(NOW - 600, NOW - 600) }));
  assert.deepEqual(stopped.watcher, { what: "Stopped at 18:59", line: STOPPED_LINE, copy: WATCH_COMMAND });
  assert.deepEqual(stopped.foot, { reading: null });
  assert.equal(stopped.groups[0].rows[0].fact, "warm until 20:08");

  // stopped with no record: the row and the foot, no sentence on what a cache is
  const stoppedEmpty = page(clocks([], { reading: reading(NOW - 600, NOW - 600) }));
  assert.equal(stoppedEmpty.watcher.what, "Stopped at 18:59");
  assert.deepEqual([stoppedEmpty.foot, stoppedEmpty.sentences, stoppedEmpty.groups], [{ reading: null }, [], []]);

  // an old tick and no record, and no listing: stopped at the tick
  assert.equal(page(clocks([], { reading: reading(NOW - 86400 - 60, null) })).watcher.what, "Stopped at yesterday 19:08");

  const noReading = page(clocks([], { reading: null }));
  assert.deepEqual(noReading.watcher, { what: "No reading yet", line: NO_READING_LINE, copy: WATCH_COMMAND });
  assert.deepEqual([noReading.sentences, noReading.foot], [[CACHE_LINE], null]);

  // a record with no stale_after takes no part: still no reading, and its line under it
  const noPart = page(clocks([row("a", { stale_after: null })], { reading: null }));
  assert.equal(noPart.watcher.what, "No reading yet");
  assert.deepEqual([noPart.sentences, noPart.foot, noPart.untimed.line], [[CACHE_LINE], null, "1 with no cache reading"]);
  assert.equal(watcherRow({ outcome: "fresh", at: NOW, last: NOW }, TODAY, UTC), null);
});

test("the empty state only when a fresh reading backs it and no record went unread", () => {
  const empty = page(clocks([]));
  assert.deepEqual([empty.sentences, empty.foot, empty.watcher, empty.untimed], [[EMPTY_LINE, CACHE_LINE], null, null, null]);
  const freshOnly = page(clocks([row("a", { kind: "fresh", last_request: null }), row("b", { kind: "no-cache" })]));
  assert.deepEqual([freshOnly.sentences, freshOnly.foot, freshOnly.untimed.line],
                   [[EMPTY_LINE, CACHE_LINE], null, "2 with no cache reading"]);
  for (const kind of ["unknown", "later-kind"]) {
    const unread = page(clocks([row("a", { kind: "fresh" }), row("b", { kind })]));
    assert.deepEqual([unread.sentences, unread.foot, unread.untimed.count], [[], { reading: "Last reading 19:08" }, 2], kind);
  }
  const missing = page(clocks([row("a", { warn_before: null })]));
  assert.deepEqual([missing.sentences, missing.untimed.rows.length], [[], 0]);
});

test("a row arriving by the topic before its names answer, and after", () => {
  const arrivals = new Arrivals();
  const first = [row("a")];
  arrivals.clocks(first, 0);
  arrivals.names(namesUp([item("a", { name: "alpha" })]));
  const names = { answer: namesUp([item("a", { name: "alpha" })]), failed: false };
  assert.deepEqual(page(clocks(first), { arrivals, names, nowMs: 10 }).groups[0].rows.map((one) => one.name), ["alpha"]);

  // b arrives by the topic before any answer holds it: counted, not drawn, for 2 s
  const second = [row("a"), row("b", { last_request: NOW - 120 })];
  arrivals.clocks(second, 1000);
  const held = page(clocks(second), { arrivals, names, nowMs: 1000 + NAME_WAIT_MS - 1 });
  assert.equal(held.groups[0].label, "2 warm");
  assert.deepEqual(held.groups[0].rows.map((one) => one.session), ["a"]);
  assert.equal(arrivals.due(1500), NAME_WAIT_MS - 500);
  // 2 s on, drawn with the rule's words for no name
  const late = page(clocks(second), { arrivals, names, nowMs: 1000 + NAME_WAIT_MS });
  assert.deepEqual(late.groups[0].rows.map((one) => [one.session, one.name]), [["b", NO_NAME], ["a", "alpha"]]);
  assert.equal(arrivals.due(1000 + NAME_WAIT_MS), null);
  // and worded again when an answer holds it
  const answer = namesUp([item("a", { name: "alpha" }), item("b", { name: "beta" })]);
  arrivals.names(answer);
  const named = page(clocks(second), { arrivals, names: { answer, failed: false }, nowMs: 1000 + NAME_WAIT_MS });
  assert.deepEqual(named.groups[0].rows.map((one) => one.name), ["beta", "alpha"]);

  // c arrives after an answer that already held it: drawn at once, named
  const third = [...second, row("c", { last_request: NOW - 180 })];
  const ahead = namesUp([...answer.sessions, item("c", { name: "gamma" })]);
  arrivals.names(ahead);
  arrivals.clocks(third, 5000);
  const atOnce = page(clocks(third), { arrivals, names: { answer: ahead, failed: false }, nowMs: 5000 });
  assert.deepEqual(atOnce.groups[0].rows.map((one) => one.name), ["gamma", "beta", "alpha"]);

  // an error answer holds nothing: the wait still runs
  const other = new Arrivals();
  other.clocks([row("d")], 0);
  other.names({ state: "error", today: TODAY, home: null, sessions: [] });
  assert.equal(other.shown(keyOf(row("d")), 1999), false);
  assert.equal(other.shown(keyOf(row("d")), 2000), true);
  // a session that left and comes back waits again
  other.clocks([], 3000);
  other.clocks([row("d")], 4000);
  assert.equal(other.shown(keyOf(row("d")), 4500), false);
});

test("the page's last lines, with a foot and without one", () => {
  const failedNames = { answer: null, failed: true };
  const errorNames = { answer: { state: "error", today: TODAY, home: null, sessions: [] }, failed: false };
  const withFoot = page(clocks([row("a")]), { names: failedNames });
  assert.ok(withFoot.foot !== null);
  assert.deepEqual(withFoot.last, [{ text: NAMES_FAILED, dim: true }]);
  const noFoot = page(clocks([]), { names: errorNames });
  assert.equal(noFoot.foot, null);
  assert.deepEqual(noFoot.last, [{ text: NAMES_FAILED, dim: true }]);
  assert.deepEqual(page(clocks([row("a")])).last, []);

  // a failed read keeps the rows last read, the watcher judged as of that answer
  const value = clocks([row("a")]);
  const kept = keep(null, feedOf(value), NOW);
  const failure = { status: 502, why: "cache's page server answered 502" };
  const later = NOW + 600;
  const failed = page(value, { feed: feedOf(value, { failure }), kept, now: later, names: failedNames });
  assert.deepEqual(failed.last, [{ text: NAMES_FAILED, dim: true },
                                 { text: "The clock records can't be read: cache's page server answered 502.", dim: false }]);
  assert.equal(failed.groups[0].rows[0].session, "a");
  assert.equal(failed.watcher, null);
  assert.equal(failed.foot.reading, "Last reading 19:08");

  // the page server's error keeps them too, in its own words
  const error = { state: "error", detail: "it broke", today: TODAY, reading: null, clocks: [] };
  const broken = page(value, { feed: feedOf(error), kept, now: later });
  assert.deepEqual(broken.last, [{ text: "cache's page server hit an error: it broke", dim: false }]);
  assert.equal(broken.groups[0].count, 1);
  const bare = page(value, { feed: feedOf({ ...error, detail: null }), kept, now: later });
  assert.deepEqual(bare.last, [{ text: "cache's page server hit an error.", dim: false }]);

  // with no answer read yet, the line alone
  const alone = page(null, { feed: feedOf(null, { failure }), kept: null, now: null });
  assert.deepEqual([alone.state, alone.groups, alone.foot, alone.last.length], ["failed", [], null, 1]);
  assert.deepEqual(lastLines(feedOf(null, { gone: true }), failedNames), [{ text: NAMES_FAILED, dim: true }]);
});

test("waiting, then Reading…, and a 404 drawing nothing", () => {
  const loading = { loading: true, gone: false, failure: null, value: null };
  assert.deepEqual([page(null, { feed: loading, kept: null, now: null, waitedMs: WAITING_MS - 1 }).waiting],
                   [null]);
  const waited = page(null, { feed: loading, kept: null, now: null, waitedMs: WAITING_MS });
  assert.deepEqual([waited.state, waited.waiting, waited.foot], ["waiting", WAITING_LINE, null]);
  const gone = page(clocks([row("a")]), { feed: feedOf(clocks([row("a")]), { gone: true }) });
  assert.deepEqual([gone.state, gone.groups, gone.last, gone.foot], ["gone", [], [], null]);
});

test("an opened row: its lines to the second, the Session line, the link and the id", () => {
  const one = row("s1", { last_request: at("2026-11-10T19:07:30"), ttl: 300 });
  const names = namesBy(namesUp([item("s1", { folder: "/home/alice/code/orbit-cli", started: at("2026-11-10T16:27:00") })]));
  const opened = openedModel(one, NOW, names, "/home/alice", { state: "read", model: "claude-opus-4" }, TODAY, UTC);
  assert.deepEqual(opened.lines, [
    { label: "Last request", value: "19:07:30", tails: [] },
    { label: "Goes cold", value: "19:12:30", tails: ["in 3 minutes", "five minutes after the last request"] },
  ]);
  assert.deepEqual(opened.session.map((part) => part.text),
                   ["Claude Code", "claude-opus-4", "started today 16:27", "~/code/orbit-cli"]);
  assert.equal(opened.sessions, "claude/s1");
  assert.equal(opened.copy, "s1");
  const cold = openedModel(one, NOW + 3600, names, "/home/alice", null, TODAY, UTC);
  assert.deepEqual(cold.lines[1], { label: "Went cold", value: "19:12:30", tails: ["56 minutes ago", "five minutes after the last request"] });
  // the model while it is read, and absent when it answers null; nothing sessions does not hold
  const reading = openedModel(one, NOW, new Map(), null, { state: "reading" }, TODAY, UTC);
  assert.deepEqual(reading.session, [{ text: "Claude Code", dim: false }, { text: WAITING_LINE, dim: true }]);
  assert.equal(reading.sessions, null);
  const bare = openedModel(one, NOW, new Map(), null, { state: "read", model: null }, TODAY, UTC);
  assert.deepEqual(bare.session.map((part) => part.text), ["Claude Code"]);
  // a row with no clock time opens to its Session line and Copy id
  assert.deepEqual(openedModel(row("u", { kind: "fresh" }), NOW, new Map(), null, null, TODAY, UTC).lines, []);
});

test("the answers' parsers", () => {
  const value = parsePageClocks({ state: "up", detail: null, today: TODAY, reading: reading(1, null), clocks: [row("a"), 7] });
  assert.deepEqual([value.today, value.reading, value.clocks.length], [TODAY, reading(1, null), 1]);
  assert.equal(parsePageClocks({ state: "up", detail: null, today: "x", reading: { tick_at: 1 }, clocks: [] }).reading, null);
  assert.equal(parsePageClocks({ state: "up", detail: null, clocks: [] }).today, null);
  assert.deepEqual(parsePageClocks({ state: "error", detail: "d", today: TODAY, reading: reading(1, 1) }),
                   { state: "error", detail: "d", clocks: [], today: TODAY, reading: null });
  assert.equal(parsePageClocks({ state: "what" }), null);

  const names = parseNames({ state: "up", today: TODAY, home: "/home/alice", sessions: [
    { session: "a", harness: "claude", name: "n", folder: "/f", started: 5, known: true },
    { session: "b", harness: "claude", name: 3, folder: null, started: "x", known: "yes" },
    { harness: "claude" },
  ] });
  assert.deepEqual(names.sessions, [item("a", { name: "n", folder: "/f", started: 5 }),
                                    item("b", { known: null })]);
  assert.deepEqual(parseNames({ state: "error", today: TODAY, home: null }), { state: "error", today: TODAY, home: null, sessions: [] });
  assert.equal(parseNames({ state: "up" }), null);
  assert.deepEqual([...namesBy(names).keys()], ["claude/a"]);

  assert.deepEqual(parseSession({ model: "m" }), { model: "m" });
  assert.deepEqual(parseSession({ model: null }), { model: null });
  assert.equal(parseSession({ model: 3 }), null);
  assert.equal(parseSession({}), null);
});

test("keep: the last answer that was up, and when it was taken", () => {
  const value = clocks([row("a")]);
  const kept = keep(null, feedOf(value), NOW);
  assert.deepEqual(kept, { value, at: NOW });
  assert.equal(keep(kept, feedOf(value), NOW + 5), kept);
  const newer = clocks([row("b")]);
  assert.deepEqual(keep(kept, feedOf(newer), NOW + 5), { value: newer, at: NOW + 5 });
  assert.equal(keep(kept, feedOf(value, { failure: { status: 0, why: "w" } }), NOW + 9), kept);
  assert.equal(keep(kept, feedOf({ ...value, state: "error", clocks: [] }), NOW + 9), kept);
  assert.equal(keep(null, feedOf(value), null), null);
});

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("names are asked one at a time, and a change while one is out is asked once after", async () => {
  const pending = [];
  let changes = 0;
  const feed = new NamesFeed(() => {
    const one = deferred();
    pending.push(one);
    return one.promise;
  }, () => { changes += 1; });
  const first = feed.ask();
  assert.equal(pending.length, 1);
  // the first clocks answer only notes the set; the opening asked already
  feed.clocks([row("a")]);
  assert.equal(pending.length, 1);
  // two changes of the set while the request is out: one more request, after it
  feed.clocks([row("a"), row("b")]);
  feed.clocks([row("a"), row("b"), row("c")]);
  feed.clocks([row("c"), row("b"), row("a")]);
  assert.equal(pending.length, 1);
  pending[0].resolve({ status: 200, data: namesUp([item("a", { name: "alpha" })]) });
  await new Promise((done) => setImmediate(done));
  assert.equal(pending.length, 2);
  assert.equal(feed.answer.sessions[0].name, "alpha");
  pending[1].resolve({ status: 503, data: null });
  await first;
  assert.equal(pending.length, 2);
  assert.equal(changes, 2);
  // a failure keeps the answer and is marked; the next readable answer clears it
  assert.equal(feed.failed, true);
  assert.equal(feed.answer.sessions[0].name, "alpha");
  const third = feed.ask();
  pending[2].resolve({ status: 200, data: { state: "error", today: TODAY, home: null } });
  await third;
  assert.deepEqual([feed.failed, feed.answer.state], [false, "error"]);
  // the same set again asks nothing
  feed.clocks([row("a"), row("b"), row("c")]);
  assert.equal(pending.length, 3);
  // a read that throws is a failure; a closed feed drops what comes
  const throwing = new NamesFeed(() => Promise.reject(new Error("no")), () => {});
  await throwing.ask();
  assert.equal(throwing.failed, true);
  const closed = new NamesFeed(() => Promise.resolve({ status: 200, data: namesUp([]) }), () => {});
  closed.close();
  await closed.ask();
  assert.equal(closed.answer, null);
});
