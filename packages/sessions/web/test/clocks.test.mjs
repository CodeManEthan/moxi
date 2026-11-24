import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import {
  CACHE_LABEL, CACHE_LINK, clockFact, clockLine, clockOf, CLOCKS_PATH, CLOCKS_TOPIC, clocksRead, clocksTopic, coldAt,
  isCold, isOld, launchedLine, nextCold, parseClocks, READ_TAIL, serverNow, timed,
} from "../src/clocks.ts";
import { listView, NO_FILTERS, queryOf, refresh, rowView, withClock } from "../src/list.ts";
import { openedView } from "../src/opened.ts";
import { runningRead } from "../src/running.ts";

// Times in UTC as `HH:MM`, so the words are the same in any zone.
const UTC = {
  clock: (epoch) => new Date(epoch * 1000).toISOString().slice(11, 16),
  day: (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10),
  start: (day) => Date.parse(`${day}T00:00:00Z`) / 1000,
};
const at = (hhmm, day = "2026-11-08") => Date.parse(`${day}T${hhmm}:00Z`) / 1000;
const TODAY = "2026-11-08";
const NOW = at("19:10");
const when = (epoch) => UTC.clock(epoch);

// One clock record as cache's `/api/clocks` sends it: warm, with an hour's cache from 18:12.
const clock = (session, over = {}) => ({
  session, harness: "claude", kind: "warm", last_request: at("18:12"), ttl: 3600, warn_before: 300,
  seen_at: at("19:09"), stale_after: 120, code: null, ...over,
});
const answer = (rows, over = {}) => ({ now: NOW, state: "up", detail: null, today: TODAY, reading: null, clocks: rows, ...over });
const read = (rows, over = {}) => clocksRead({ status: 200, data: answer(rows, over) }, NOW);

// -- the read ------------------------------------------------------------------------------------

test("the route and the topic are cache's", () => {
  assert.equal(CLOCKS_PATH, "api/clocks");
  assert.equal(CLOCKS_TOPIC, "clocks");
  assert.equal(CACHE_LABEL, "Cache");
  assert.equal(CACHE_LINK, "Cache");
});

test("a value's records are kept by harness and session; a value that is not one is null", () => {
  const rows = parseClocks(answer([clock("a"), clock("b", { harness: "codex" }), { session: 5 }, null, { harness: "claude" }]));
  assert.deepEqual([...rows.keys()], ["claude/a", "codex/b"]);
  assert.deepEqual([...parseClocks({ state: "error", detail: "the records can't be read", clocks: [] }).keys()], []);
  assert.equal(parseClocks({ state: "down", clocks: [] }), null);
  assert.equal(parseClocks({ state: "up" }), null);
  assert.equal(parseClocks("x"), null);
});

test("a read is the records and the server's clock; anything else leaves nothing", () => {
  const got = clocksRead({ status: 200, data: answer([clock("a")], { now: NOW + 30 }) }, NOW);
  assert.deepEqual([...got.rows.keys()], ["claude/a"]);
  assert.equal(got.offset, 30);
  assert.equal(serverNow(got, NOW + 5), NOW + 35);
  for (const status of [0, 404, 500, 502, 503]) assert.equal(clocksRead({ status, data: null }, NOW), null, String(status));
  assert.equal(clocksRead({ status: 200, data: "x" }, NOW), null);
  assert.equal(clocksRead({ status: 200, data: answer([clock("a")], { now: null }) }, NOW), null);
  assert.equal(clocksRead({ status: 404, data: answer([clock("a")]) }, NOW), null);
  assert.equal(read([], { state: "error", detail: "no" }).rows.size, 0);
});

test("a topic value takes the read's clock; before a read, or unreadable, the route is read again", () => {
  const kept = read([clock("a")]);
  const next = clocksTopic(kept, { state: "up", detail: null, today: TODAY, reading: null, clocks: [clock("b")] });
  assert.deepEqual([...next.rows.keys()], ["claude/b"]);
  assert.equal(next.offset, kept.offset);
  assert.equal(clocksTopic(null, answer([clock("b")])), null);
  assert.equal(clocksTopic(kept, { state: "up" }), null);
  assert.equal(clocksTopic(kept, { state: "error", detail: "no", clocks: [] }).rows.size, 0);
});

// -- cache's rules ---------------------------------------------------------------------------------

const cases = [
  clock("warm"), clock("cold", { kind: "cold", last_request: at("17:00") }), clock("edge", { last_request: NOW - 3600 }),
  clock("fresh", { kind: "fresh", last_request: null, ttl: null }), clock("no-cache", { kind: "no-cache" }),
  clock("unknown", { kind: "unknown", code: "not-read" }), clock("no-ttl", { ttl: null }),
  clock("no-warn", { warn_before: null }), clock("no-stale", { stale_after: null }),
  clock("old", { seen_at: at("18:30") }), clock("ahead", { last_request: NOW + 60 }), clock("odd", { kind: "near" }),
];

test("which records are timed, and when each goes cold", () => {
  assert.equal(timed(cases[0]), true);
  assert.equal(coldAt(cases[0]), at("19:12"));
  assert.equal(isCold(cases[0], NOW), false);
  assert.equal(isCold(cases[1], NOW), true);
  // Cold from its cold time on.
  assert.equal(isCold(cases[2], NOW), true);
  assert.equal(isCold(cases[2], NOW - 1), false);
  for (const one of cases.slice(3, 9)) assert.equal(coldAt(one), null, one.session);
  assert.equal(isOld(cases[9], NOW), true);
  assert.equal(isOld(cases[0], NOW), false);
  assert.equal(isOld(cases[8], NOW + 10_000), false);
  assert.equal(coldAt(cases[11]), null);
});

test("the rules and words are cache's own, where its source sits beside sessions'", async () => {
  const theirs = new URL("../../../cache/web/src/model.ts", import.meta.url);
  if (!existsSync(theirs)) return;
  const cache = await import(theirs.href);
  for (const now of [NOW - 3600, NOW - 1, NOW, NOW + 1, NOW + 7200]) {
    for (const one of cases) {
      const name = `${one.session} at ${now}`;
      assert.equal(timed(one), cache.timed(one), name);
      assert.equal(coldAt(one), cache.coldAt(one), name);
      assert.equal(isCold(one, now), cache.isCold(one, now), name);
      assert.equal(isOld(one, now), cache.isOld(one, now), name);
    }
  }
  assert.equal(READ_TAIL, cache.READ_TAIL);
});

// -- the words -------------------------------------------------------------------------------------

test("a row's fact: warm until its cold time, cold from then on, and nothing without a clock time", () => {
  assert.equal(clockFact(cases[0], NOW, when), "warm until 19:12");
  assert.equal(clockFact(cases[0], at("19:12"), when), "cold");
  assert.equal(clockFact(cases[1], NOW, when), "cold");
  for (const one of cases.slice(3, 9)) assert.equal(clockFact(one, NOW, when), null, one.session);
});

test("the Cache line: warm until or cold since, and when an old record was read", () => {
  assert.deepEqual(clockLine(cases[0], NOW, when), { value: "warm until 19:12", tail: null });
  assert.deepEqual(clockLine(cases[1], NOW, when), { value: "cold since 18:00", tail: null });
  assert.deepEqual(clockLine(cases[9], NOW, when),
                   { value: "warm until 19:12", tail: "read 18:30, not rewritten since; this reading may be old" });
  assert.equal(clockLine(cases[3], NOW, when), null);
});

const launch = (over = {}) => ({
  request_id: "r1", harness: "claude", session_id: "s1", name: "planner-Nov-08-2026 12:45", identity: null,
  where: { target: "slot", slot: "3", project_key: "atlas-api", folder: "atlas-api" }, started_at: "2026-11-08T12:45:00Z",
  cache: { state: "warm", until: "2026-11-08T20:04:00Z", estimated: false }, waits: null, covered: false, coverable: null,
  activity: null, ...over,
});

test("the Cache line from launcher, as the Comms page words it", () => {
  assert.deepEqual(launchedLine(launch(), when), { value: "warm until 20:04", tail: null });
  assert.deepEqual(launchedLine(launch({ cache: { state: "warm", until: "2026-11-08T20:08:00Z", estimated: true } }), when),
                   { value: "warm until 20:08, est.", tail: null });
  assert.deepEqual(launchedLine(launch({ waits: { kind: "agents", count: 1 }, covered: true }), when),
                   { value: "warm until 20:04", tail: "kept warm while 1 agent works" });
  assert.deepEqual(launchedLine(launch({ waits: { kind: "agents", count: 2 }, covered: false, coverable: { cap: 3 } }), when),
                   { value: "warm until 20:04", tail: null });
  assert.deepEqual(launchedLine(launch({ cache: { state: "cold", until: "2026-11-08T18:35:00Z", estimated: false } }), when),
                   { value: "cold since 18:35", tail: null });
  assert.deepEqual(launchedLine(launch({ cache: { state: "cold", until: null, estimated: false } }), when),
                   { value: "cold", tail: null });
  assert.deepEqual(launchedLine(launch({ cache: { state: "warm", until: null, estimated: false } }), when),
                   { value: "warm", tail: null });
  assert.equal(launchedLine(launch({ cache: null }), when), null);
});

test("the next moment a warm record goes cold", () => {
  const clocks = read([clock("a"), clock("b", { last_request: at("18:20") }), cases[1], cases[3]]);
  assert.equal(nextCold(clocks, NOW), at("19:12"));
  assert.equal(nextCold(clocks, at("19:12")), at("19:20"));
  assert.equal(nextCold(clocks, at("19:20")), null);
  assert.equal(nextCold(null, NOW), null);
});

// -- the list --------------------------------------------------------------------------------------

const row = (session, changed, over = {}) => ({
  harness: "claude", session, path: null, changed, size: 1, subagents: 0, folder: "/work/orbit-cli",
  started: changed - 600, by_program: null, name: null, model: "m", activity: null,
  prompt: { at: changed, text: "Make the build smaller.", truncated: false }, recent: false, ...over,
});
const listAnswer = (recentRows, dayRows) => ({
  shape: 1, now: NOW, today: TODAY, all: recentRows.length + dayRows.length,
  total: recentRows.length + dayRows.length, unreadable: 0, roots: [], harnesses: [],
  groups: [{ key: "recent", count: recentRows.length, rows: recentRows, before: null },
           { key: TODAY, count: dayRows.length, rows: dayRows, before: null, program: { count: 0 } }],
});
const stateOf = (answer) => refresh(null, answer, queryOf(NO_FILTERS, TODAY, UTC)).state;
const cacheOf = (rows, now = NOW) => ({ clocks: read(rows), now });
const rowsOf = (view) => view.groups.flatMap((group) => group.rows);

test("a row takes the record of its own harness and session: the fact, and the opened row's Cache line", () => {
  const state = stateOf(listAnswer([row("s1", at("19:07")), row("s2", at("19:00")),
                                    row("s3", at("18:07")), row("s4", at("18:06"), { harness: "codex" })], []));
  const cache = cacheOf([clock("s1"), clock("s3", { kind: "cold", last_request: at("15:07") }), clock("s4"),
                         clock("s9")]);
  const view = listView(state, { zone: UTC, cache });
  assert.deepEqual(rowsOf(view).map((one) => [one.session, one.fact, one.cache?.value ?? null]), [
    ["s1", "warm until 19:12", "warm until 19:12"],
    ["s2", null, null],
    ["s3", "cold", "cold since 16:07"],
    // A Codex session is not the Claude record of the same id.
    ["s4", null, null],
  ]);
  assert.ok(rowsOf(view).every((one) => one.factHeavy === false));
});

test("cache speaks on Active recently's rows only; a day's rows and a find's say nothing of it", () => {
  const program = row("p1", at("19:05"), { by_program: "cron" });
  const state = stateOf(listAnswer([row("s1", at("19:07"))], [row("s3", at("15:07")), row("s5", at("14:07"))]));
  // `Active recently`'s fold of sessions started by a program, opened.
  state.groups[0].program = { count: 1, open: true, rows: [program], before: null, pages: 1 };
  const cache = cacheOf([clock("s1"), clock("p1"), clock("s3"), clock("s5", { kind: "cold", last_request: at("14:07") })]);
  const view = listView(state, { zone: UTC, cache });
  const facts = (group) => group.rows.concat(group.program?.rows ?? [])
    .map((one) => [one.session, one.fact, one.cache?.value ?? null]);
  assert.deepEqual(view.groups.map((group) => [group.key, facts(group)]), [
    ["recent", [["s1", "warm until 19:12", "warm until 19:12"], ["p1", "warm until 19:12", "warm until 19:12"]]],
    // Timed records, warm and cold, and the day's rows draw neither.
    [TODAY, [["s3", null, null], ["s5", null, null]]],
  ]);
  // A day's row opened has no Cache line.
  const opened = stateOf(listAnswer([], [row("s3", at("15:07"))]));
  opened.opened = ["claude/s3"];
  const shown = listView(opened, { zone: UTC, cache: cacheOf([clock("s3")]) }).groups[0].rows[0];
  assert.equal(shown.open, true);
  assert.equal(openedView(shown, null, { today: TODAY, zone: UTC, cacheLink: true }).cache, null);
  // A find's matches say nothing of cache either.
  const query = queryOf({ ...NO_FILTERS, find: "build" }, TODAY, UTC);
  const found = refresh(null, { ...listAnswer([], []), all: 1, total: 1,
                               groups: [{ key: "found", count: 1, rows: [row("s1", at("19:07"))], before: null }] },
                        query).state;
  assert.deepEqual(rowsOf(listView(found, { zone: UTC, cache })).map((one) => [one.fact, one.cache]), [[null, null]]);
});

test("without cache's records nothing of them is drawn, and the rows are as they were", () => {
  const state = stateOf(listAnswer([row("s1", at("19:07"))], []));
  const bare = listView(state, { zone: UTC });
  assert.deepEqual(listView(state, { zone: UTC, cache: null }), bare);
  assert.deepEqual(rowsOf(bare).map((one) => [one.fact, one.cache]), [[null, null]]);
  // A record with no clock time says nothing either.
  const untimed = listView(state, { zone: UTC, cache: cacheOf([clock("s1", { kind: "fresh", last_request: null })]) });
  assert.deepEqual(untimed, bare);
  // An empty read, as a read of an error leaves, says nothing.
  assert.deepEqual(listView(state, { zone: UTC, cache: { clocks: read([], { state: "error" }), now: NOW } }), bare);
});

test("a row turns cold at its time, on the server's clock", () => {
  const state = stateOf(listAnswer([row("s1", at("19:07"))], []));
  const fact = (now) => rowsOf(listView(state, { zone: UTC, cache: cacheOf([clock("s1")], now) }))[0].fact;
  assert.equal(fact(at("19:11")), "warm until 19:12");
  assert.equal(fact(at("19:12")), "cold");
});

test("a time not of today says its day", () => {
  const view = withClock(rowView(row("s1", at("19:07")), TODAY, TODAY, false, UTC),
                         cacheOf([clock("s1", { kind: "cold", last_request: at("17:35", "2026-11-07") })]), TODAY, UTC);
  assert.deepEqual([view.fact, view.cache.value], ["cold", "cold since yesterday 18:35"]);
});

test("on a running launch launcher's fact wins; cache's speaks only where launcher gives none", () => {
  const state = stateOf(listAnswer([row("s1", at("19:07")), row("s2", at("19:00"))], []));
  const running = runningRead({ status: 200, data: { state: "up", detail: null, count: 2, sessions: [
    launch(),
    launch({ request_id: "r2", session_id: "s2", name: "builder-1-Nov-08-2026 11:00", cache: null }),
  ] } });
  const cache = cacheOf([clock("s1", { kind: "cold", last_request: at("17:00") }), clock("s2")]);
  const view = listView(state, { zone: UTC, launcher: true, running, cache });
  assert.deepEqual(view.running.rows.map((one) => [one.session, one.fact, one.cache]), [
    ["s1", "warm until 20:04", { value: "warm until 20:04", tail: null }],
    ["s2", "warm until 19:12", { value: "warm until 19:12", tail: null }],
  ]);
  // The running sessions are drawn there only.
  assert.deepEqual(rowsOf(view), []);
  // Without cache, the launch launcher gives no cache has no fact.
  const alone = listView(state, { zone: UTC, launcher: true, running });
  assert.deepEqual(alone.running.rows.map((one) => [one.fact, one.cache?.value ?? null]),
                   [["warm until 20:04", "warm until 20:04"], [null, null]]);
});

test("the opened row's Cache line is the row's, there before the session's answer, linked where cache's page is", () => {
  const base = rowView(row("s1", at("19:07")), TODAY, TODAY, true, UTC);
  const opened = withClock(base, cacheOf([clock("s1")]), TODAY, UTC);
  assert.deepEqual(openedView(opened, null, { today: TODAY, zone: UTC, cacheLink: true }).cache,
                   { label: "Cache", value: "warm until 19:12", tail: null, link: "Cache" });
  assert.equal(openedView(opened, null, { today: TODAY, zone: UTC }).cache.link, null);
  assert.equal(openedView(base, null, { today: TODAY, zone: UTC, cacheLink: true }).cache, null);
  assert.deepEqual(clockOf(null, base), null);
});
