import assert from "node:assert/strict";
import test from "node:test";

import {
  answered, caseOf, expired, factOf, KEEP_WARM, KEEP_WARM_HOLDS_TAIL, KEEP_WARM_OFF_TAIL, KEEP_WARM_PATH, keepWarmLine,
  keepWarmOf, keepWarmStateOf, keepWarmTitle, launchOf, leadOf, NO_PRESSES, NOT_KEPT, OtherRead, parseKeepWarms,
  parseLaunched, PRESS_WAIT_MS, pressBody, pressed, pressNote, RUNNING_PATH, RUNNING_TOPIC, STATE_PATH, STATE_TOPIC,
  valueCame, whereText,
} from "../src/launched.ts";
import * as sessions from "../../../sessions/web/src/running.ts";

const iso = (at) => new Date(at * 1000).toISOString();
const NOW = Date.parse("2026-11-10T19:09:00Z") / 1000;
const hhmm = (at) => iso(at).slice(11, 16);

/** One entry of launcher's `/api/running`, in its declared shape. */
const entry = (request, session, extra = {}) => ({
  request_id: request, harness: "claude", session_id: session, name: `${request}-name`, identity: null,
  where: { target: "slot", slot: "4", project_key: "atlas-api", folder: "atlas-api" },
  started_at: iso(NOW - 3600), model: "opus", effort: "high",
  cache: { state: "warm", until: iso(NOW + 780), estimated: false },
  waits: null, covered: false, coverable: null,
  activity: { at: iso(NOW - 60), by: "agent", text: "Done." },
  ...extra,
});
const running = (entries, extra = {}) => ({ state: "up", detail: null, count: entries.length, sessions: entries, ...extra });
const one = (extra = {}) => parseLaunched(running([entry("r-1", "s-1", extra)])).get("claude/s-1");

test("the routes and topics are launcher's own, as the Sessions page reads them", () => {
  assert.equal(RUNNING_PATH, sessions.RUNNING_PATH);
  assert.equal(RUNNING_TOPIC, sessions.RUNNING_TOPIC);
  assert.equal(KEEP_WARM_PATH, sessions.KEEP_WARM_PATH);
  assert.equal(KEEP_WARM, sessions.KEEP_WARM);
  assert.equal(PRESS_WAIT_MS, sessions.PRESS_WAIT_MS);
  assert.equal(NOT_KEPT, sessions.NOT_KEPT);
  assert.equal(STATE_PATH, "api/state");
  assert.equal(STATE_TOPIC, "sessions");
});

test("launches are found by harness and session id; one with no id, name or target is left out", () => {
  const found = parseLaunched(running([
    entry("r-1", "s-1"),
    entry("r-2", null),
    entry("r-3", "s-3", { name: " " }),
    entry("r-4", "s-4", { where: { target: "", slot: null, project_key: null, folder: "x" } }),
    entry("r-5", "s-1"),
    entry("r-6", "s-6", { harness: "codex", name: "planner\n two", effort: null }),
    "not one",
  ]));
  assert.deepEqual([...found.keys()], ["claude/s-1", "codex/s-6"]);
  assert.equal(found.get("claude/s-1").request_id, "r-1");
  assert.equal(found.get("codex/s-6").name, "planner two");
  assert.equal(found.get("codex/s-6").effort, null);
  assert.equal(found.get("claude/s-1").effort, "high");
  assert.equal(launchOf(found, "claude/s-1").name, "r-1-name");
  assert.equal(launchOf(found, "claude/s-2"), null);
  assert.equal(launchOf(null, "claude/s-1"), null);
});

test("a value launcher could not read, and a body that is not one, match nothing", () => {
  assert.equal(parseLaunched(running([entry("r-1", "s-1")], { state: "error", detail: "boom" })), null);
  assert.equal(parseLaunched({ state: "up" }), null);
  assert.equal(parseLaunched(null), null);
  assert.equal(parseLaunched([]), null);
});

test("where a launch runs reads as Home's and the Sessions page's running rows say it", () => {
  const wheres = [
    { target: "slot", slot: "4", project_key: "atlas-api", folder: "atlas" },
    { target: "slot", slot: null, project_key: null, folder: "atlas" },
    { target: "terminal", slot: null, project_key: null, folder: "kestrel-web" },
    { target: "headless", slot: null, project_key: "harbor-infra", folder: "harbor" },
    { target: "terminal", slot: null, project_key: null, folder: "" },
  ];
  assert.deepEqual(wheres.map(whereText), ["slot 4 · atlas-api", "slot · atlas", "terminal · kestrel-web",
                                           "headless · harbor-infra", "terminal"]);
  for (const where of wheres) assert.equal(whereText(where), sessions.whereText(where));
});

test("the three cases, their facts and the heavy lead are the Sessions page's words", () => {
  const cases = [
    {},
    { cache: { state: "warm", until: iso(NOW + 780), estimated: true } },
    { cache: { state: "cold", until: null, estimated: false } },
    { cache: { state: "warm", until: null, estimated: false } },
    { waits: { kind: "agents", count: 2 } },
    { waits: { kind: "agents", count: 1 }, covered: true },
    { waits: { kind: "subagents", count: 3 }, covered: true },
    { waits: { kind: "subagents", count: 1 } },
    { waits: { kind: "agents", count: 2 }, cache: { state: "cold", until: null, estimated: false } },
    { waits: { kind: "holds", count: 2 } },
    { cache: null },
  ];
  const words = cases.map((extra) => {
    const launch = one(extra);
    const theirs = sessions.parseRunning(running([entry("r-1", "s-1", extra)])).sessions[0];
    assert.deepEqual(factOf(launch, hhmm), sessions.factOf(theirs, hhmm));
    assert.equal(leadOf(launch), sessions.leadOf(theirs));
    assert.equal(caseOf(launch), sessions.caseOf(theirs));
    return [caseOf(launch), factOf(launch, hhmm), leadOf(launch)];
  });
  assert.deepEqual(words, [
    ["ordinary", { text: "warm until 19:22", heavy: false }, null],
    ["ordinary", { text: "warm until 19:22, est.", heavy: false }, null],
    ["ordinary", { text: "cold", heavy: false }, null],
    ["ordinary", { text: "warm", heavy: false }, null],
    ["uncovered", { text: "goes cold at 19:22", heavy: true }, "waiting on 2 agents"],
    ["covered", { text: "kept warm while 1 agent works", heavy: false }, null],
    ["covered", { text: "kept warm while 3 subagents run", heavy: false }, null],
    ["uncovered", { text: "goes cold at 19:22", heavy: true }, "waiting on 1 subagent"],
    ["ordinary", { text: "cold", heavy: false }, null],
    ["ordinary", { text: "warm until 19:22", heavy: false }, null],
    ["ordinary", null, null],
  ]);
});

test("Keep warm is offered at the desk on an uncovered session a press would cover", () => {
  const loud = one({ waits: { kind: "agents", count: 2 }, coverable: { cap: 3 } });
  assert.equal(keepWarmOf(loud, true), keepWarmTitle(3));
  assert.equal(keepWarmTitle(3), sessions.keepWarmTitle(3));
  assert.equal(keepWarmOf(loud, false), null);
  assert.equal(keepWarmOf(one({ waits: { kind: "agents", count: 2 } }), true), null);
  assert.equal(keepWarmOf(one({ waits: { kind: "agents", count: 2 }, covered: true, coverable: { cap: 3 } }), true), null);
  assert.equal(keepWarmOf(one({ coverable: { cap: 3 } }), true), null);
});

test("launcher's state of each launch: its keep-warm by request and session", () => {
  const value = {
    state: "up", detail: null, refused: null, sessions: [
      { session_id: "s-1", request_id: "r-1", keep_warm: { on: true, holds: ["builder-1: orders", " "], nudges: 1, last: null, window: null } },
      { session_id: "s-2", request_id: "r-2", keep_warm: { on: false, holds: [], nudges: 0, last: null, window: null } },
      { session_id: "s-3", request_id: "r-3", keep_warm: null, unreadable: "the store is locked" },
      { session_id: "s-4", request_id: "r-4", keep_warm: { on: true, holds: [], nudges: 0, last: null, window: "no window: lifetime 300 s" } },
    ],
  };
  const states = parseKeepWarms(value);
  const of = (request, session) => keepWarmStateOf(states, { request_id: request, session_id: session });
  assert.deepEqual(of("r-1", "s-1"), { on: true, holds: ["builder-1: orders"], window: null });
  assert.deepEqual(of("r-2", "s-2"), { on: false, holds: [], window: null });
  assert.equal(of("r-3", "s-3"), null);
  assert.equal(of("r-1", "s-2"), null);
  assert.deepEqual(of("r-4", "s-4").window, "no window: lifetime 300 s");
  assert.equal(parseKeepWarms({ ...value, state: "error" }), null);
  assert.equal(parseKeepWarms(null), null);
  assert.equal(keepWarmStateOf(null, { request_id: "r-1", session_id: "s-1" }), null);
});

test("the opened Keep warm line: on with its holds, off while waiting uncovered, else off", () => {
  const loud = one({ waits: { kind: "agents", count: 2 } });
  const plain = one();
  assert.deepEqual(keepWarmLine(plain, { on: true, holds: ["builder-1: orders pagination"], window: null }),
                   { value: "on", tails: ["holding for “builder-1: orders pagination”"] });
  assert.deepEqual(keepWarmLine(plain, { on: true, holds: ["a", "b"], window: "no window: lifetime 300 s" }),
                   { value: "on", tails: ["holding for “a”, “b”", "no window: lifetime 300 s"] });
  assert.deepEqual(keepWarmLine(plain, { on: true, holds: [], window: null }), { value: "on", tails: [] });
  assert.deepEqual(keepWarmLine(loud, { on: false, holds: [], window: null }),
                   { value: "off, and it waits on 2 agents", tails: [KEEP_WARM_HOLDS_TAIL] });
  assert.deepEqual(keepWarmLine(plain, { on: false, holds: [], window: null }), { value: "off", tails: [KEEP_WARM_OFF_TAIL] });
  assert.equal(keepWarmLine(plain, null), null);
});

test("a press: its body, its notes, and when it stops waiting, as the Sessions page has them", () => {
  assert.deepEqual(pressBody("r-1"), sessions.pressBody("r-1"));
  const answers = [
    { status: 200, data: { refused: null } },
    { status: 200, data: { refused: { code: "covered", problem: "it is already covered" } } },
    { status: 200, data: { refused: {} } },
    { status: 0, data: null },
    { status: 502, data: null },
    { status: 200, data: "nonsense" },
  ];
  for (const answer of answers) assert.equal(pressNote(answer), sessions.pressNote(answer));
  assert.equal(pressNote(answers[1]), "Not kept warm: it is already covered");

  let presses = pressed(NO_PRESSES, "r-1");
  assert.deepEqual(presses, { waiting: { "r-1": false }, notes: {} });
  presses = answered(presses, "r-1", "Not kept warm: no");
  assert.deepEqual(presses, { waiting: { "r-1": true }, notes: { "r-1": "Not kept warm: no" } });
  const loud = parseLaunched(running([entry("r-1", "s-1", { waits: { kind: "agents", count: 2 }, coverable: { cap: 3 } }),
                                      entry("r-2", "s-2", { waits: { kind: "agents", count: 2 }, coverable: { cap: 3 } })]));
  assert.deepEqual(valueCame(presses, loud), { waiting: {}, notes: {} });
  const out = pressed(pressed(NO_PRESSES, "r-1"), "r-2");
  assert.deepEqual(valueCame(out, loud).waiting, { "r-1": false, "r-2": false });
  const covered = parseLaunched(running([entry("r-1", "s-1", { waits: { kind: "agents", count: 2 }, covered: true })]));
  assert.deepEqual(valueCame(out, covered).waiting, {});
  assert.deepEqual(expired(answered(out, "r-1", "x"), "r-1"), { waiting: { "r-2": false }, notes: { "r-1": "x" } });
});

test("another module's read: one at a time, and a failed read leaves nothing of the last answer", async () => {
  const answers = [];
  let calls = 0;
  let changes = 0;
  const read = new OtherRead(async () => { calls += 1; return answers.shift(); }, parseLaunched, () => { changes += 1; });
  answers.push({ status: 200, data: running([entry("r-1", "s-1")]) });
  await read.refresh();
  assert.equal(read.value.size, 1);
  answers.push({ status: 500, data: null });
  await read.refresh();
  assert.equal(read.value, null);
  answers.push({ status: 200, data: running([entry("r-1", "s-1")]) });
  await read.refresh();
  answers.push({ status: 200, data: running([], { state: "error", detail: "x" }) });
  await read.refresh();
  assert.equal(read.value, null);
  answers.push({ status: 404, data: null });
  await read.refresh();
  assert.equal(read.value, null);
  const thrown = new OtherRead(async () => { throw new Error("down"); }, parseLaunched, () => {});
  await thrown.refresh();
  assert.equal(thrown.value, null);
  read.topic(running([entry("r-2", "s-2")]));
  assert.deepEqual([...read.value.keys()], ["claude/s-2"]);
  read.topic({ nonsense: true });
  assert.deepEqual([...read.value.keys()], ["claude/s-2"]);
  assert.equal(calls, 5);
  assert.equal(changes, 6);

  let release;
  const slow = new OtherRead(() => new Promise((done) => { release = done; }), parseLaunched, () => {});
  const first = slow.refresh();
  slow.refresh();
  slow.close();
  release({ status: 200, data: running([entry("r-1", "s-1")]) });
  await first;
  assert.equal(slow.value, null);
});
