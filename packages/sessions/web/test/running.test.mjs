import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import { listView, NO_FILTERS, queryOf, refresh, runningGroup, runningRowView } from "../src/list.ts";
import {
  answered, caseOf, expired, factOf, KEEP_WARM_PATH, keepWarmOf, keepWarmTitle, leadOf, NO_PRESSES, ordered, parseRunning,
  pressBody, pressed, pressNote, PRESS_WAIT_MS, runningRead, valueCame, whereText,
} from "../src/running.ts";

// Times in UTC as `HH:MM`, so the words are the same in any zone.
const UTC = {
  clock: (epoch) => new Date(epoch * 1000).toISOString().slice(11, 16),
  day: (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10),
  start: (day) => Date.parse(`${day}T00:00:00Z`) / 1000,
};
const iso = (hhmm) => `2026-11-08T${hhmm}:00Z`;
const at = (hhmm) => Date.parse(iso(hhmm)) / 1000;
const TODAY = "2026-11-08";

// One running launch as launcher's `/api/running` sends it (its declared shape, every field).
const entry = (request, fields = {}) => ({
  request_id: request, harness: "claude", session_id: `s-${request}`, name: `${request}-Nov-08-2026 12:45`, identity: null,
  where: { target: "slot", slot: "3", project_key: "atlas-api", folder: "atlas-api" }, started_at: iso("12:45"),
  cache: { state: "warm", until: iso("19:52"), estimated: false }, waits: null, covered: false, coverable: null,
  activity: { at: iso("18:52"), by: "agent", text: "Done. I changed three files." }, ...fields,
});
const uncovered = (request, fields = {}) =>
  entry(request, { waits: { kind: "agents", count: 2 }, coverable: { cap: 3 }, ...fields });
const running = (sessions, fields = {}) => ({ state: "up", detail: null, count: sessions.length, sessions, ...fields });
const up = (sessions) => runningRead({ status: 200, data: running(sessions) });

// A list answer of one recent group and one day, as `/api/list` sends it.
const row = (session, changed, over = {}) => ({
  harness: "claude", session, path: null, changed, size: 1, subagents: 0, folder: "/work/atlas-api",
  started: changed - 600, by_program: null, name: null, model: "m", activity: null, prompt: null, recent: false, ...over,
});
const listAnswer = (recentRows, dayRows) => ({
  shape: 1, now: at("19:10"), today: TODAY, all: recentRows.length + dayRows.length,
  total: recentRows.length + dayRows.length, unreadable: 0, roots: [], harnesses: [],
  groups: [{ key: "recent", count: recentRows.length, rows: recentRows, before: null },
           { key: TODAY, count: dayRows.length, rows: dayRows, before: null, program: { count: 0 } }],
});
const stateOf = (answer, filters = NO_FILTERS) => refresh(null, answer, queryOf(filters, TODAY, UTC)).state;

test("the route's value: an entry that is not one is left out; anything else is null", () => {
  const got = parseRunning(running([entry("r1"), { request_id: 5 }, null, entry("r2", { cache: { state: "hot" } })]));
  assert.deepEqual(got.sessions.map((one) => [one.request_id, one.cache?.state ?? null]), [["r1", "warm"], ["r2", null]]);
  assert.equal(parseRunning({ state: "down", sessions: [] }), null);
  assert.equal(parseRunning(running([entry("r1", { waits: { kind: "agents", count: 0 } })])).sessions[0].waits, null);
});

test("a read is the value, a failure in launcher's words, or nothing when launcher is gone", () => {
  assert.equal(up([entry("r1")]).value.sessions.length, 1);
  assert.equal(runningRead({ status: 404, data: null }), null);
  assert.deepEqual(runningRead({ status: 0, data: null }), { value: null, failure: "no answer from the shell" });
  assert.deepEqual(runningRead({ status: 502, data: null }), { value: null, failure: "launcher's page server answered 502" });
  assert.deepEqual(runningRead({ status: 200, data: "x" }),
                   { value: null, failure: "launcher's page server sent an answer that can't be read" });
  assert.deepEqual(runningRead({ status: 200, data: running([], { state: "error", detail: "the log is locked." }) }),
                   { value: null, failure: "the log is locked" });
});

test("the loud waiting sessions lead, the soonest cold first; then the latest activity first", () => {
  const sessions = parseRunning(running([
    entry("a", { activity: { at: iso("18:00"), by: null, text: "x" } }),
    uncovered("b", { cache: { state: "warm", until: iso("19:40"), estimated: false } }),
    entry("c", { activity: null, started_at: iso("19:00") }),
    uncovered("d", { cache: { state: "warm", until: iso("19:22"), estimated: false } }),
  ])).sessions;
  assert.deepEqual(ordered(sessions).map((one) => one.request_id), ["d", "b", "c", "a"]);
});

test("a running row is named and worded as Home's running rows: where, the fact, the lead, the time and message", () => {
  const state = stateOf(listAnswer([], []));
  const loud = runningRowView(parseRunning(running([uncovered("b")])).sessions[0], state, NO_PRESSES, { zone: UTC });
  assert.equal(loud.name, "b-Nov-08-2026 12:45");
  assert.equal(loud.where, "slot 3 · atlas-api");
  assert.deepEqual([loud.fact, loud.factHeavy, loud.lead, loud.leadHeavy], ["goes cold at 19:52", true, "waiting on 2 agents", true]);
  assert.deepEqual([loud.time, loud.saidBy, loud.rest], ["18:52", null, "Done. I changed three files."]);
  assert.equal(loud.keepWarm, keepWarmTitle(3));
  assert.deepEqual([loud.key, loud.route, loud.act], ["claude/s-b", "claude/s-b", "Open"]);

  const quiet = (fields) => runningRowView(parseRunning(running([entry("a", fields)])).sessions[0], state, NO_PRESSES,
                                           { zone: UTC });
  const terminal = quiet({ where: { target: "terminal", slot: null, project_key: null, folder: "reviewer-home" },
                           cache: { state: "cold", until: null, estimated: false },
                           activity: { at: iso("17:20"), by: "you", text: "Run  the\nsuite." } });
  assert.deepEqual([terminal.where, terminal.fact, terminal.factHeavy, terminal.lead], ["terminal · reviewer-home", "cold", false, null]);
  assert.deepEqual([terminal.time, terminal.saidBy, terminal.rest], ["17:20", "you", "Run the suite."]);
  const launched = quiet({ activity: null, session_id: null, cache: null });
  assert.deepEqual([launched.time, launched.rest, launched.fact, launched.act, launched.route], ["12:45", "launched", null, null, null]);
  assert.equal(launched.key, "launch/a");
  assert.equal(quiet({ waits: { kind: "subagents", count: 1 }, covered: true }).fact, "kept warm while 1 subagent runs");
  assert.equal(quiet({ cache: { state: "warm", until: iso("20:08"), estimated: true } }).fact, "warm until 20:08, est.");
  // No `Keep warm` where a press would not cover the session, nor on the phone.
  assert.equal(keepWarmOf(parseRunning(running([uncovered("b", { coverable: null })])).sessions[0], true), null);
  assert.equal(runningRowView(parseRunning(running([uncovered("b")])).sessions[0], state, NO_PRESSES,
                              { desk: false, zone: UTC }).keepWarm, null);
});

test("a running session shows in the running group only, and the rest keep their groups", () => {
  const answer = listAnswer([row("s-a", at("19:00")), row("hand", at("18:50"))], [row("s-b", at("15:00")), row("old", at("09:00"))]);
  const view = listView(stateOf(answer), { launcher: true, running: up([entry("a"), uncovered("b"), entry("c")]), zone: UTC });
  assert.equal(view.running.label, "3 running");
  assert.deepEqual(view.running.rows.map((one) => one.request), ["b", "a", "c"]);
  assert.deepEqual(view.groups.map((group) => [group.key, group.rows.map((one) => one.session)]),
                   [["recent", ["hand"]], [TODAY, ["old"]]]);
  // A group whose every row runs is not drawn.
  const all = listView(stateOf(listAnswer([row("s-a", at("19:00"))], [row("old", at("09:00"))])),
                       { launcher: true, running: up([entry("a")]), zone: UTC });
  assert.deepEqual(all.groups.map((group) => group.key), [TODAY]);
});

test("without launcher, or before its read, nothing of the running group is drawn and no row moves", () => {
  const state = stateOf(listAnswer([row("s-a", at("19:00"))], []));
  for (const options of [{ launcher: false, running: up([entry("a")]) }, { launcher: true, running: null }]) {
    const view = listView(state, { ...options, zone: UTC });
    assert.equal(view.running, null);
    assert.deepEqual(view.groups[0].rows.map((one) => one.session), ["s-a"]);
  }
});

test("nothing running says so; a failed read says why; a filter keeps the launches its list holds; find draws none", () => {
  const state = stateOf(listAnswer([row("s-a", at("19:00"))], [row("old", at("09:00"))]));
  assert.deepEqual(runningGroup(state, up([])).group, { label: "Running", rows: [], line: "Nothing is running", reason: null });
  assert.deepEqual(runningGroup(state, runningRead({ status: 0, data: null })).group,
                   { label: "Running", rows: [], line: "Moxi couldn't read the running sessions", reason: "no answer from the shell" });
  const filtered = stateOf(listAnswer([row("s-a", at("19:00"))], []), { ...NO_FILTERS, harness: "claude" });
  const kept = runningGroup(filtered, up([entry("a"), entry("z")]), NO_PRESSES, { zone: UTC });
  assert.deepEqual(kept.group.rows.map((one) => one.request), ["a"]);
  assert.equal(runningGroup(filtered, up([entry("z")])).group, null);
  const found = stateOf(listAnswer([row("s-a", at("19:00"))], []), { ...NO_FILTERS, find: "atlas" });
  assert.deepEqual(runningGroup(found, up([entry("a")])), { group: null, taken: new Set() });
});

test("with no session on the machine, the empty state stands unless a launch runs", () => {
  const empty = refresh(null, { ...listAnswer([], []), all: 0, total: 0, groups: [] }, queryOf(NO_FILTERS, TODAY, UTC)).state;
  const none = listView(empty, { launcher: true, running: up([]), zone: UTC });
  assert.equal(none.running, null);
  assert.equal(none.empty.line, "No sessions yet.");
  const one = listView(empty, { launcher: true, running: up([entry("a", { session_id: null })]), zone: UTC });
  assert.equal(one.empty, null);
  assert.equal(one.running.label, "1 running");
});

test("a press waits for the answer and the next value, or 20 s; a refusal's note lasts until the next value", () => {
  let presses = pressed(NO_PRESSES, "b");
  const state = stateOf(listAnswer([], []));
  const value = parseRunning(running([uncovered("b")]));
  assert.equal(runningRowView(value.sessions[0], state, presses).pressing, true);
  presses = answered(presses, "b", pressNote({ status: 200, data: { refused: { problem: "it is not waiting" } } }));
  assert.equal(runningRowView(value.sessions[0], state, presses).note, "Not kept warm: it is not waiting");
  assert.deepEqual(valueCame(presses, value), NO_PRESSES);
  assert.deepEqual(expired(pressed(NO_PRESSES, "b"), "b"), NO_PRESSES);
  assert.deepEqual(pressBody("b"), { request_id: "b" });
});

test("the words, the path and the wait are launcher's own, where its source sits beside sessions'", async () => {
  const theirs = new URL("../../../launcher/web/src/running-model.ts", import.meta.url);
  if (!existsSync(theirs)) return;
  const launcher = await import(theirs.href);
  assert.equal(PRESS_WAIT_MS, launcher.PRESS_WAIT_MS);
  assert.equal(KEEP_WARM_PATH, launcher.KEEP_WARM_PATH);
  const answers = [{ status: 200, data: { refused: null } }, { status: 200, data: { refused: { problem: "no" } } },
                   { status: 200, data: "x" }, { status: 0, data: null }, { status: 502, data: null }];
  assert.deepEqual(answers.map(pressNote), answers.map(launcher.pressNote));
  assert.equal(keepWarmTitle(3), launcher.keepWarmTitle(3));
  const clock = (epoch) => launcher.clock(new Date(epoch * 1000).toISOString());
  const entries = [entry("a"), uncovered("b"), uncovered("c", { covered: true }), uncovered("d", { coverable: null }),
                   entry("e", { cache: { state: "cold", until: null, estimated: false } }), entry("f", { cache: null }),
                   uncovered("g", { waits: { kind: "subagents", count: 1 }, cache: { state: "warm", until: null, estimated: true } }),
                   uncovered("h", { covered: true, waits: { kind: "subagents", count: 2 } }),
                   entry("i", { where: { target: "headless", slot: null, project_key: null, folder: "kestrel-web" } })];
  const ours = parseRunning(running(entries)).sessions;
  const their = launcher.parseRunning(running(entries)).sessions;
  ours.forEach((one, index) => {
    assert.equal(caseOf(one), launcher.caseOf(their[index]), one.request_id);
    assert.deepEqual(factOf(one, clock), launcher.factOf(their[index]), one.request_id);
    assert.equal(leadOf(one), launcher.leadOf(their[index]), one.request_id);
    assert.equal(whereText(one.where), launcher.whereText(their[index].where), one.request_id);
    assert.equal(keepWarmOf(one, true), launcher.keepWarmOf(their[index], true)?.title ?? null, one.request_id);
  });
  assert.deepEqual(ordered(ours).map((one) => one.request_id), launcher.ordered(their).map((one) => one.request_id));
});
