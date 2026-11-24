import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import {
  answered, caseOf, expired, factOf, joinRunning, KEEP_WARM_PATH, keepWarmTitle, NO_PRESSES, NOT_KEPT, parseRunning,
  pressBody, pressed, pressNote, PRESS_WAIT_MS, RUNNING_PATH, runningLine, stampOf, valueCame, waitsWords, whereText,
} from "../src/launched-model.ts";

// Times in UTC as `HH:MM`, so the words are the same in any zone.
const when = (at) => new Date(at * 1000).toISOString().slice(11, 16);
const iso = (hhmm) => `2026-11-12T${hhmm}:00Z`;

const row = (qualified, fields = {}) => ({
  qualified, name: qualified.split("@")[0], scope: qualified.includes("@") ? qualified.split("@")[1] : null,
  live: false, kind: "agent", module: null, tier: null, adapter: null, directors: [], session_id: null,
  bound_at: null, awaiting: [], cache: null, ...fields,
});
const bound = (qualified, fields = {}) =>
  row(qualified, { live: true, kind: null, tier: "worker", adapter: "claude", session_id: `s-${qualified}`, ...fields });

// One running launch as launcher's `/api/running` sends it (its declared shape, every field).
const entry = (request, fields = {}) => ({
  request_id: request, harness: "claude", session_id: null, name: `${request}-session`, identity: null,
  where: { target: "slot", slot: "4", project_key: "atlas-api", folder: "atlas-api" }, started_at: iso("12:00"),
  cache: { state: "warm", until: iso("19:22"), estimated: false }, waits: null, covered: false, coverable: null,
  activity: null, ...fields,
});
const running = (sessions, fields = {}) => ({ state: "up", detail: null, count: sessions.length, sessions, ...fields });
const uncovered = (request, fields = {}) =>
  entry(request, { waits: { kind: "agents", count: 2 }, coverable: { cap: 3 }, ...fields });

test("the route's value: an entry that is not one is left out; anything else is null", () => {
  const got = parseRunning(running([entry("r1"), { request_id: 5 }, null, entry("r2", { cache: { state: "hot" } })]));
  assert.deepEqual(got.sessions.map((one) => [one.request_id, one.cache]),
                   [["r1", { state: "warm", until: iso("19:22"), estimated: false }], ["r2", null]]);
  assert.equal(parseRunning({ state: "down", sessions: [] }), null);
  assert.equal(parseRunning(null), null);
  assert.equal(parseRunning(running([entry("r1", { waits: { kind: "agents", count: 0 } })])).sessions[0].waits, null);
  assert.equal(stampOf("not a time"), null);
  assert.equal(stampOf(iso("19:22")), Date.parse(iso("19:22")) / 1000);
});

test("the join: by session id, else by identity; bound rows only; one entry to one row", () => {
  const rows = [bound("planner"), bound("builder-1", { session_id: "s-b1" }), bound("reviewer", { session_id: null }),
                row("docs-writer"), bound("builder-2@kestrel-web", { session_id: null })];
  const value = running([
    entry("r-planner", { session_id: "s-planner", identity: "not-planner" }),
    entry("r-b1-name", { identity: "builder-1" }),
    entry("r-b1", { session_id: "s-b1" }),
    entry("r-reviewer", { identity: "reviewer" }),
    entry("r-reviewer-old", { identity: "reviewer" }),
    entry("r-docs", { identity: "docs-writer" }),
    entry("r-kw", { identity: "builder-2@kestrel-web" }),
  ]);
  const got = joinRunning(value, rows, when);
  assert.deepEqual([...got.entries()].map(([qualified, launched]) => [qualified, launched.request_id]).sort(), [
    ["builder-1", "r-b1"], ["builder-2@kestrel-web", "r-kw"], ["planner", "r-planner"], ["reviewer", "r-reviewer"],
  ]);
  assert.equal(joinRunning(running([], { state: "error", detail: "no" }), rows, when).size, 0);
  assert.equal(joinRunning(null, rows, when).size, 0);
});

test("launcher's words: where, the three cases, the fact with est., the lead, the title", () => {
  const one = (fields) => joinRunning(running([entry("r1", { identity: "a", ...fields })]), [bound("a")], when).get("a");
  const loud = one({ waits: { kind: "agents", count: 2 }, coverable: { cap: 3 } });
  assert.deepEqual([loud.where, loud.fact, loud.uncovered, loud.waits, loud.coverable, loud.title], [
    "slot 4 · atlas-api", { text: "goes cold at 19:22", heavy: true }, true, "waiting on 2 agents", true,
    "Sends a short turn before each cold time while it waits, at most 3 in a row.",
  ]);
  // The loud case rises with coverable null and draws no act.
  const bare = one({ waits: { kind: "subagents", count: 1 }, cache: { state: "warm", until: iso("19:22"), estimated: true } });
  assert.deepEqual([bare.fact.text, bare.uncovered, bare.waits, bare.coverable, bare.title],
                   ["goes cold at 19:22, est.", true, "waiting on 1 subagent", false, null]);
  const covered = one({ waits: { kind: "agents", count: 1 }, covered: true });
  assert.deepEqual([covered.fact, covered.uncovered, covered.waits, covered.cache.kept],
                   [{ text: "kept warm while 1 agent works", heavy: false }, false, null, "kept warm while 1 agent works"]);
  assert.equal(one({ waits: { kind: "subagents", count: 2 }, covered: true }).fact.text, "kept warm while 2 subagents run");
  assert.equal(one({ waits: { kind: "holds", count: 1 }, covered: true }).fact.text, "kept warm while 1 hold is open");
  assert.equal(one({ waits: { kind: "holds", count: 2 }, covered: true }).fact.text, "kept warm while 2 holds are open");
  // A session at its cap with only holds open is loud, and a press would cover it.
  const held = one({ waits: { kind: "holds", count: 1 }, coverable: { cap: 10 } });
  assert.deepEqual([held.uncovered, held.waits, held.coverable], [true, "waiting on 1 open hold", true]);
  assert.deepEqual(one({}).fact, { text: "warm until 19:22", heavy: false });
  assert.equal(one({ cache: { state: "warm", until: null, estimated: true } }).fact.text, "warm, est.");
  assert.deepEqual(one({ cache: { state: "cold", until: iso("18:35"), estimated: false } }).fact, { text: "cold", heavy: false });
  // A cold or unread cache is no loud case, whatever it waits on.
  assert.equal(one({ waits: { kind: "agents", count: 2 }, cache: { state: "cold", until: null, estimated: false } }).uncovered,
               false);
  assert.equal(one({ cache: null }).fact, null);
  assert.equal(one({ cache: null }).cache, null);
  assert.deepEqual(one({ cache: { state: "cold", until: iso("18:35"), estimated: false } }).cache,
                   { warm: false, until: Date.parse(iso("18:35")) / 1000, estimated: false, kept: null });
  assert.equal(whereText({ target: "terminal", slot: null, project_key: null, folder: "notes" }), "terminal · notes");
  assert.equal(whereText({ target: "slot", slot: null, project_key: "p", folder: "f" }), "slot · p");
});

test("the Session line's facts: the launch's name and harness, and its transcript's route", () => {
  const one = (fields, rows = [bound("a", { session_id: "s/1" })]) =>
    joinRunning(running([entry("r1", { identity: "a", name: "builder one", ...fields })]), rows, when).get("a").session;
  assert.deepEqual(one({ session_id: "s/1" }), { name: "builder one", harness: "claude", route: "claude/s%2F1" });
  // The roster's session id stands in for a launch that sent none.
  assert.equal(one({}).route, "claude/s%2F1");
  assert.equal(one({}, [bound("a", { session_id: null })]).route, null);
});

test("Keep warm's words, path and wait are launcher's own", async () => {
  assert.equal(PRESS_WAIT_MS, 20_000);
  assert.equal(KEEP_WARM_PATH, "api/keep-warm");
  assert.equal(RUNNING_PATH, "api/running");
  assert.equal(NOT_KEPT, "Not kept warm: ");
  const answers = [
    { status: 200, data: { refused: null } },
    { status: 200, data: { refused: { problem: "it is not waiting" } } },
    { status: 200, data: { refused: { problem: "" } } },
    { status: 200, data: "x" },
    { status: 0, data: null },
    { status: 502, data: null },
  ];
  assert.deepEqual(answers.map(pressNote), [
    null, "Not kept warm: it is not waiting", "Not kept warm: the answer could not be read",
    "Not kept warm: the answer could not be read", "Not kept warm: no answer from the shell",
    "Not kept warm: the shell answered 502",
  ]);
  assert.deepEqual(pressBody("r1"), { request_id: "r1" });
  // Held equal to launcher's model, where its source sits beside comms' in the repository.
  const theirs = new URL("../../../launcher/web/src/running-model.ts", import.meta.url);
  if (!existsSync(theirs)) return;
  const launcher = await import(theirs.href);
  assert.equal(PRESS_WAIT_MS, launcher.PRESS_WAIT_MS);
  assert.equal(KEEP_WARM_PATH, launcher.KEEP_WARM_PATH);
  assert.deepEqual(answers.map(pressNote), answers.map(launcher.pressNote));
  assert.equal(keepWarmTitle(3), launcher.keepWarmTitle(3));
  // The cases, the facts (worded by launcher's clock) and the leads, over the same entries.
  const clock = (at) => launcher.clock(new Date(at * 1000).toISOString());
  const entries = [entry("a"), uncovered("b"), uncovered("c", { covered: true }), uncovered("d", { coverable: null }),
                   entry("e", { cache: { state: "cold", until: null, estimated: false } }), entry("f", { cache: null }),
                   uncovered("g", { waits: { kind: "subagents", count: 1 }, cache: { state: "warm", until: null, estimated: true } }),
                   uncovered("h", { covered: true, waits: { kind: "subagents", count: 2 } }),
                   uncovered("i", { waits: { kind: "holds", count: 2 } }),
                   uncovered("j", { covered: true, waits: { kind: "holds", count: 1 } })];
  for (const one of entries) {
    const parsed = parseRunning(running([one])).sessions[0];
    const their = launcher.parseRunning(running([one])).sessions[0];
    assert.equal(caseOf(parsed), launcher.caseOf(their), one.request_id);
    assert.deepEqual(factOf(parsed, clock), launcher.factOf(their), one.request_id);
    assert.equal(waitsWords(parsed), launcher.leadOf(their), one.request_id);
    assert.equal(whereText(parsed.where), launcher.whereText(their.where), one.request_id);
  }
});

test("a press waits for the answer and the next value, or 20 s; a refusal's note lasts until the next value", () => {
  let presses = pressed(NO_PRESSES, "r1");
  assert.deepEqual(presses, { waiting: { r1: false }, notes: {} });
  // A value before the answer keeps it waiting while the act is still drawn.
  const still = running([uncovered("r1")]);
  presses = valueCame(presses, parseRunning(still));
  assert.deepEqual(presses.waiting, { r1: false });
  presses = answered(presses, "r1", "Not kept warm: it is not waiting");
  assert.deepEqual(presses, { waiting: { r1: true }, notes: { r1: "Not kept warm: it is not waiting" } });
  presses = valueCame(presses, parseRunning(still));
  assert.deepEqual(presses, NO_PRESSES);
  // An act no longer drawn stops the wait; the 20 s stop it too and keep the note.
  presses = valueCame(pressed(NO_PRESSES, "r2"), parseRunning(running([uncovered("r2", { covered: true })])));
  assert.deepEqual(presses.waiting, {});
  presses = expired(answered(pressed(NO_PRESSES, "r3"), "r3", "Not kept warm: no answer from the shell"), "r3");
  assert.deepEqual(presses, { waiting: {}, notes: { r3: "Not kept warm: no answer from the shell" } });
  // Pressing again clears the old note.
  assert.deepEqual(pressed(presses, "r3"), { waiting: { r3: false }, notes: {} });
});

test("launcher's read that failed is a line; a 404 is none", () => {
  assert.equal(runningLine(200, parseRunning(running([]))), null);
  assert.equal(runningLine(404, null), null);
  assert.equal(runningLine(0, null), "The running sessions can't be read: no answer from the shell.");
  assert.equal(runningLine(502, null), "The running sessions can't be read: launcher's page server answered 502.");
  assert.equal(runningLine(200, null),
               "The running sessions can't be read: launcher's page server sent an answer that can't be read.");
  assert.equal(runningLine(200, parseRunning(running([], { state: "error", detail: "The log can't be read." }))),
               "The running sessions can't be read: The log can't be read.");
});
