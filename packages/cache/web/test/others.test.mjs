// The page's model with launcher's and usage's facts: what each says of a row and of an
// opened row, and that without either the page is the page without them.

import assert from "node:assert/strict";
import test from "node:test";

import { parseRates } from "../src/cost.ts";
import { KEEP_WARM_HOLDS_TAIL, KEEP_WARM_OFF_TAIL, keepWarmTitle, NO_PRESSES, OtherRead, parseKeepWarms, parseLaunched,
         UNLESS_KEPT } from "../src/launched.ts";
import { Arrivals, isLoud, keep, NO_OTHERS, openedModel, pageModel, rowModel } from "../src/model.ts";

const iso = (at) => new Date(at * 1000).toISOString();
const UTC = { time: (at) => iso(at).slice(11, 16), seconds: (at) => iso(at).slice(11, 19), day: (at) => iso(at).slice(0, 10) };
const at = (text) => Date.parse(`${text}Z`) / 1000;
const TODAY = "2026-11-10";
const NOW = at("2026-11-10T19:09:00");
const SONNET = "claude-sonnet-4-5";

/** A clock record whose last request was `ago` seconds before now. */
const row = (session, ago, extra = {}) => ({ session, harness: "claude", kind: "warm", last_request: NOW - ago, ttl: 3600,
  warn_before: 900, seen_at: NOW - 5, stale_after: 30, code: null, ...extra });
/** A running launch of `session`, warm until its record's cold time. */
const entry = (request, session, ago, extra = {}) => ({
  request_id: request, harness: "claude", session_id: session, name: `${request}-name`, identity: null,
  where: { target: "slot", slot: "4", project_key: "atlas-api", folder: "atlas-api" },
  started_at: iso(NOW - 7200), model: "sonnet", effort: "high",
  cache: { state: "warm", until: iso(NOW - ago + 3600), estimated: false },
  waits: null, covered: false, coverable: null, activity: null, ...extra,
});
const launched = (entries) => parseLaunched({ state: "up", detail: null, count: entries.length, sessions: entries });
const states = (list) => parseKeepWarms({ state: "up", detail: null, refused: null, sessions: list.map(([request, session, keepWarm]) =>
  ({ session_id: session, request_id: request, keep_warm: keepWarm })) });
const rates = parseRates({ models: [{ cache_read: 0.3, cache_write_5m: 3.75, cache_write_1h: 6, tiers: [] }] }, [SONNET]);
const usageOf = (tokens) => ({ facts: (key) => (tokens[key] === undefined ? null : { tokens: tokens[key], model: SONNET }), rates });
const others = (extra = {}) => ({ ...NO_OTHERS, ...extra });
const names = (items) => new Map(items.map((one) => [`claude/${one.session}`, { harness: "claude", name: null, folder: null,
  started: null, known: true, ...one }]));

test("a launched row is named by its launch where none was typed, and placed where it runs", () => {
  const facts = others({ launched: launched([entry("r-1", "s1", 600), entry("r-2", "s2", 600, {
    where: { target: "terminal", slot: null, project_key: null, folder: "kestrel-web" } })]) });
  const named = names([{ session: "s1", folder: "/work/atlas", started: NOW - 7200 }, { session: "s2", name: "planner",
                         folder: "/work/kestrel-web" }, { session: "s3", folder: "/work/orbit-cli", started: NOW - 3600 }]);
  const one = rowModel(row("s1", 600), NOW, named, TODAY, false, UTC, facts);
  assert.deepEqual([one.name, one.where, one.fact, one.request], ["r-1-name", "slot 4 · atlas-api", "warm until 19:59", "r-1"]);
  const typed = rowModel(row("s2", 600), NOW, named, TODAY, false, UTC, facts);
  assert.deepEqual([typed.name, typed.where], ["planner", "terminal · kestrel-web"]);
  const byHand = rowModel(row("s3", 600), NOW, named, TODAY, false, UTC, facts);
  assert.deepEqual([byHand.name, byHand.where, byHand.request], ["orbit-cli · started today 18:09", null, null]);
  // without launcher, the same rows as before
  const before = rowModel(row("s1", 600), NOW, named, TODAY, false, UTC);
  assert.deepEqual([before.name, before.where, before.fact, before.request, before.tail],
                   ["atlas · started today 17:09", null, "warm until 19:59", null, null]);
});

test("launcher's case wins while cache's clock counts the row warm", () => {
  const facts = others({ launched: launched([
    entry("r-1", "s1", 600, { waits: { kind: "agents", count: 1 }, covered: true }),
    entry("r-2", "s2", 600, { waits: { kind: "agents", count: 2 }, coverable: { cap: 3 } }),
    entry("r-3", "s3", 600, { cache: { state: "warm", until: iso(NOW + 3000), estimated: true } }),
    entry("r-4", "s4", 4000, { waits: { kind: "agents", count: 2 }, coverable: { cap: 3 } }),
  ]), usage: usageOf({ "claude/s1": 412_000, "claude/s2": 655_000, "claude/s4": 100_000 }) });
  const draw = (session, ago) => rowModel(row(session, ago), NOW, new Map(), TODAY, false, UTC, facts);
  const covered = draw("s1", 600);
  assert.deepEqual([covered.fact, covered.factHeavy, covered.lead, covered.rest, covered.tail, covered.keepWarm],
                   ["kept warm while 1 agent works", false, null, "last request 18:59", null, null]);
  const loud = draw("s2", 600);
  assert.deepEqual([loud.fact, loud.factHeavy, loud.lead, loud.rest, loud.tail, loud.keepWarm, loud.pressing],
                   ["goes cold at 19:59", true, "waiting on 2 agents", null, "about $3.93 to re-read if cold", keepWarmTitle(3), false]);
  const est = draw("s3", 600);
  assert.deepEqual([est.fact, est.factHeavy, est.tail], ["warm until 19:59, est.", false, null]);
  // cold by cache's clock: no case of launcher's, its own words and the cost of the next turn
  const cold = draw("s4", 4000);
  assert.deepEqual([cold.fact, cold.factHeavy, cold.lead, cold.keepWarm, cold.tail],
                   ["cold since 19:02", false, null, null, "about $0.60 to re-read on its next turn"]);
  // away from the desk, no act
  const phone = rowModel(row("s2", 600), NOW, new Map(), TODAY, false, UTC, { ...facts, desk: false });
  assert.equal(phone.keepWarm, null);
  // a press waiting and its note
  const waiting = rowModel(row("s2", 600), NOW, new Map(), TODAY, false, UTC,
                           { ...facts, presses: { waiting: { "r-2": false }, notes: { "r-2": "Not kept warm: no" } } });
  assert.deepEqual([waiting.pressing, waiting.note], [true, "Not kept warm: no"]);
  assert.equal(isLoud(row("s2", 600), NOW, facts.launched), true);
  assert.equal(isLoud(row("s4", 4000), NOW, facts.launched), false);
  assert.equal(isLoud(row("s2", 600), NOW, null), false);
});

test("a warm row ends in its next keep-alive where launcher gives one, and in nothing stale", () => {
  const covered = { waits: { kind: "agents", count: 1 }, covered: true };
  const usage = usageOf({ "claude/s1": 412_000, "claude/s2": 412_000 });
  const draw = (entries, session = "s1", ago = 600, now = NOW) =>
    rowModel(row(session, ago), now, new Map(), TODAY, false, UTC, others({ launched: launched(entries), usage }));
  const later = draw([entry("r-1", "s1", 600, { ...covered, next_keep_alive_at: iso(NOW + 2400) })]);
  assert.deepEqual([later.fact, later.rest, later.tail],
                   ["kept warm while 1 agent works", "last request 18:59", "next keep-alive 19:49"]);
  // a time already come: the nudge goes at the next ask
  const due = draw([entry("r-1", "s1", 600, { ...covered, next_keep_alive_at: iso(NOW - 60) })]);
  assert.equal(due.tail, "next keep-alive due");
  // the same time once the clock passes it
  assert.equal(draw([entry("r-1", "s1", 600, { ...covered, next_keep_alive_at: iso(NOW + 2400) })], "s1", 600, NOW + 2400).tail,
               "next keep-alive due");
  // null, missing and no launcher: the row as before, with no piece and no cost on a covered row
  assert.equal(draw([entry("r-1", "s1", 600, { ...covered, next_keep_alive_at: null })]).tail, null);
  assert.equal(draw([entry("r-1", "s1", 600, covered)]).tail, null);
  assert.equal(draw([]).tail, "about $2.47 to re-read if cold");
  // a covered launch the page words as ordinary (it waits on holds): the time in place of the cost
  const holds = draw([entry("r-1", "s1", 600, { waits: { kind: "holds", count: 1 }, covered: true,
                                                    next_keep_alive_at: iso(NOW + 2400) })]);
  assert.deepEqual([holds.fact, holds.tail], ["warm until 19:59", "next keep-alive 19:49"]);
  // cold by cache's clock: its own words and the cost, never a keep-alive
  const cold = draw([entry("r-1", "s1", 4000, { ...covered, next_keep_alive_at: iso(NOW - 1500) })], "s1", 4000);
  assert.deepEqual([cold.fact, cold.tail], ["cold since 19:02", "about $2.47 to re-read on its next turn"]);
});

test("the loud row leads the warm group; the rest keep their order", () => {
  const clocks = [row("soon", 3300), row("loud", 600), row("later", 60), row("gone", 4000)];
  const value = { state: "up", detail: null, today: TODAY, reading: { tick_at: NOW - 2, listed_at: NOW - 2, stale_after: 30 },
                  clocks };
  const feed = { loading: false, gone: false, failure: null, value };
  const arrivals = new Arrivals();
  arrivals.names({ state: "up", sessions: clocks.map((one) => ({ session: one.session, harness: "claude" })) });
  const input = { feed, kept: keep(null, feed, NOW), now: NOW, names: { answer: null, failed: false }, arrivals, nowMs: 0,
                  waitedMs: 0, opened: new Set(), untimedShown: false, clock: UTC };
  const order = (model) => model.groups.map((group) => group.rows.map((one) => one.session));
  assert.deepEqual(order(pageModel(input)), [["soon", "loud", "later"], ["gone"]]);
  const loud = others({ launched: launched([entry("r-1", "loud", 600, { waits: { kind: "agents", count: 2 } })]) });
  assert.deepEqual(order(pageModel({ ...input, others: loud })), [["loud", "soon", "later"], ["gone"]]);
});

test("an opened launched row: Keep warm, If cold with its Usage link, and the effort", () => {
  const facts = (entries, warm, tokens) => others({ launched: launched(entries), keepWarms: states(warm), usage: usageOf(tokens) });
  const open = (session, ago, extra) => openedModel(row(session, ago), NOW, new Map(), null,
                                                    { state: "read", model: SONNET }, TODAY, UTC, extra);
  const ifCold = { label: "If cold", value: "Next turn re-reads 412k tokens: about $2.47, or $0.12 warm", tails: ["estimate"],
                   link: { module: "usage", route: "sessions/claude/s1", text: "Usage" } };

  const plain = open("s1", 600, facts([entry("r-1", "s1", 600)], [["r-1", "s1", { on: false, holds: [], nudges: 0 }]],
                                      { "claude/s1": 412_000 }));
  assert.deepEqual(plain.lines.slice(2), [ifCold, { label: "Keep warm", value: "off", tails: [KEEP_WARM_OFF_TAIL] }]);
  assert.deepEqual(plain.session.map((part) => part.text), ["Claude Code", SONNET, "high"]);

  const covered = open("s1", 600, facts([entry("r-1", "s1", 600, { waits: { kind: "agents", count: 1 }, covered: true })],
    [["r-1", "s1", { on: true, holds: ["builder-1: orders pagination"], nudges: 0 }]], { "claude/s1": 412_000 }));
  assert.equal(covered.lines[1].after, UNLESS_KEPT);
  assert.deepEqual(covered.lines.slice(2), [
    { label: "Keep warm", value: "on", tails: ["holding for “builder-1: orders pagination”"] }, ifCold]);

  const loud = open("s1", 600, facts([entry("r-1", "s1", 600, { waits: { kind: "agents", count: 2 } })],
    [["r-1", "s1", { on: false, holds: [], nudges: 0 }]], {}));
  assert.equal(loud.lines[1].after, undefined);
  assert.deepEqual(loud.lines.slice(2), [{ label: "Keep warm", value: "off, and it waits on 2 agents", tails: [KEEP_WARM_HOLDS_TAIL] }]);

  // a state launcher could not read, no launch, no usage: neither line, and no effort
  const unread = open("s1", 600, facts([entry("r-1", "s1", 600, { effort: null })], [["r-1", "s1", null]], {}));
  assert.equal(unread.lines.length, 2);
  assert.deepEqual(unread.session.map((part) => part.text), ["Claude Code", SONNET]);
  const bare = open("s1", 600, NO_OTHERS);
  assert.equal(bare.lines.length, 2);
  // usage's cost on a session started by hand
  const byHand = open("s1", 600, others({ usage: usageOf({ "claude/s1": 412_000 }) }));
  assert.deepEqual(byHand.lines.slice(2), [ifCold]);
  assert.deepEqual(NO_OTHERS.presses, NO_PRESSES);
});

test("a topic value of launcher's whose state is not up leaves no piece of its last good answer", async () => {
  // launcher publishes its gather's failure on both topics as `state: "error"` and no sessions
  let changes = 0;
  const changed = () => { changes += 1; };
  const loud = entry("r-1", "s1", 600, { waits: { kind: "agents", count: 2 }, coverable: { cap: 3 } });
  const runningRead = new OtherRead(async () => ({ status: 200, data: { state: "up", detail: null, count: 1, sessions: [loud] } }),
                                    parseLaunched, changed);
  const stateRead = new OtherRead(async () => ({ status: 200, data: { state: "up", detail: null, refused: null,
    sessions: [{ session_id: "s1", request_id: "r-1", keep_warm: { on: false, holds: [], nudges: 0 } }] } }), parseKeepWarms, changed);
  await Promise.all([runningRead.refresh(), stateRead.refresh()]);
  const facts = () => others({ desk: true, launched: runningRead.value, keepWarms: stateRead.value });
  const drawn = () => rowModel(row("s1", 600), NOW, new Map(), TODAY, false, UTC, facts());
  const opened = () => openedModel(row("s1", 600), NOW, new Map(), null, { state: "read", model: SONNET }, TODAY, UTC, facts());
  const withoutLauncher = [rowModel(row("s1", 600), NOW, new Map(), TODAY, false, UTC, others({ desk: true })),
                           openedModel(row("s1", 600), NOW, new Map(), null, { state: "read", model: SONNET }, TODAY, UTC,
                                       others({ desk: true }))];
  const good = drawn();
  assert.deepEqual([good.name, good.where, good.lead, good.factHeavy, good.request, good.keepWarm],
                   ["r-1-name", "slot 4 · atlas-api", "waiting on 2 agents", true, "r-1", keepWarmTitle(3)]);
  assert.ok(opened().lines.some((one) => one.label === "Keep warm"));
  assert.notDeepEqual([drawn(), opened()], withoutLauncher);

  runningRead.topic({ state: "error", detail: "the gather failed: OSError", count: 0, sessions: [] });
  stateRead.topic({ state: "error", detail: "the gather failed: OSError", sessions: [] });
  assert.deepEqual([runningRead.value, stateRead.value], [null, null]);
  assert.equal(changes, 4);
  assert.deepEqual([drawn(), opened()], withoutLauncher);
});
