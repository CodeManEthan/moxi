import assert from "node:assert/strict";
import test from "node:test";

import { joinRunning } from "../src/launched-model.ts";
import {
  AGAIN_MS, folderWord, headerOf, isDue, keyOf, parseSessionHeader, rereadable, rereadAnswered, sessionLine, sessionOf,
  sessionPath, sessionWhere, startedWords, wantedSessions,
} from "../src/session-model.ts";

// A clock in UTC that writes the time as `HH:MM`, so the words are the same in any zone.
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (stamp) => Date.parse(`${stamp}Z`) / 1000;
const TODAY = "2026-11-12";

const row = (qualified, fields = {}) => ({
  qualified, name: qualified.split("@")[0], scope: qualified.includes("@") ? qualified.split("@")[1] : null,
  live: false, kind: "agent", module: null, tier: null, adapter: null, harness: null, directors: [],
  session_id: null, bound_at: null, awaiting: [], cache: null, holder: null, ...fields,
});
const bound = (qualified, fields = {}) =>
  row(qualified, { live: true, kind: null, tier: "worker", adapter: "claude-shim", harness: "claude",
                   session_id: `s-${qualified}`, ...fields });

// sessions' `/api/session` as it answers: its declared keys, every one.
const answer = (harness, session, fields = {}) => ({
  shape: 1, harness, session, path: `/home/u/.claude/projects/x/${session}.jsonl`, changed: at("2026-11-12T18:50:00"),
  size: 1200, subagents: null, folder: "/home/u/code/atlas-api", started: at("2026-11-12T12:45:00"),
  by_program: null, name: null, model: "claude-opus", activity: null, prompt: null, recent: true, reply: null,
  more: null, resume: null, today: TODAY, home: "/home/u", ...fields,
});
const header = (fields = {}) => parseSessionHeader(answer("claude", "s-planner", fields), { harness: "claude", session: "s-planner" });

test("a live row with a harness and a session id names its session; any other row none", () => {
  assert.deepEqual(sessionOf(bound("planner")), { harness: "claude", session: "s-planner" });
  assert.equal(sessionOf(row("idle")), null);
  assert.equal(sessionOf(bound("app", { adapter: "connector", harness: null })), null);
  assert.equal(sessionOf(bound("x", { session_id: null })), null);
  assert.equal(sessionOf(bound("x", { session_id: "" })), null);
  assert.equal(sessionOf(bound("x", { harness: 3 })), null);
  assert.equal(sessionOf({ ...bound("x"), harness: undefined }), null);
});

test("the path asks sessions for one session by harness and id, each encoded", () => {
  assert.equal(sessionPath({ harness: "claude", session: "s 1&x" }), "api/session?harness=claude&session=s%201%26x");
  assert.equal(keyOf({ harness: "codex", session: "abc" }), "codex/abc");
});

test("the sessions read: every live row launcher gives no where, once each", () => {
  const rows = [bound("planner"), bound("builder-1"), bound("builder-1@kestrel", { session_id: "s-builder-1" }),
                row("docs-writer"), bound("app", { harness: null })];
  assert.deepEqual(wantedSessions(rows, null).map(keyOf), ["claude/s-planner", "claude/s-builder-1"]);
  const entry = (request, sessionId, where) => ({
    request_id: request, harness: "claude", session_id: sessionId, name: request, identity: null, where,
    cache: null, waits: null, covered: false, coverable: null,
  });
  const launched = joinRunning({ state: "up", detail: null, count: 2, sessions: [
    entry("r1", "s-planner", { target: "slot", slot: "3", project_key: "atlas-api", folder: "atlas-api" }),
    entry("r2", "s-builder-1", { target: "", slot: null, project_key: null, folder: "" }),
  ] }, rows, UTC.time);
  // Launcher's where wins for planner; builder-1's launch names no where, so sessions is read.
  assert.deepEqual(wantedSessions(rows, launched).map(keyOf), ["claude/s-builder-1"]);
});

test("a read is due once, and again after a read that gave nothing", () => {
  assert.equal(isDue(undefined, 0), true);
  assert.equal(isDue({ state: "out" }, 1e12), false);
  assert.equal(isDue({ state: "read", header: header(), at: 0 }, 1e12), false);
  assert.equal(isDue({ state: "read", header: null, at: 1000 }, 1000 + AGAIN_MS - 1), false);
  assert.equal(isDue({ state: "read", header: null, at: 1000 }, 1000 + AGAIN_MS), true);
});

test("an opened row asks again once its session answered; never while out or unasked", () => {
  assert.equal(rereadable(undefined), false);
  assert.equal(rereadable({ state: "out" }), false);
  assert.equal(rereadable({ state: "read", header: header(), at: 0 }), true);
  assert.equal(rereadable({ state: "read", header: null, at: 0 }), true);
});

test("asked again: a new header replaces; nothing keeps the header held, else reads nothing", () => {
  const held = { state: "read", header: header(), at: 1000 };
  const named = header({ name: "parser fix" });
  assert.deepEqual(rereadAnswered(held, named, 5000), { state: "read", header: named, at: 5000 });
  assert.equal(rereadAnswered(held, null, 5000), held);
  assert.deepEqual(rereadAnswered({ state: "read", header: null, at: 1000 }, null, 5000), { state: "read", header: null, at: 5000 });
  assert.deepEqual(rereadAnswered({ state: "read", header: null, at: 1000 }, named, 5000), { state: "read", header: named, at: 5000 });
  assert.deepEqual(rereadAnswered(undefined, null, 5000), { state: "read", header: null, at: 5000 });
});

test("the answer is read as sent; another session's, or a value that isn't one, is none", () => {
  assert.deepEqual(header({ name: "reviewer-Nov-08-2026 15:50" }), {
    harness: "claude", session: "s-planner", name: "reviewer-Nov-08-2026 15:50", folder: "/home/u/code/atlas-api",
    started: at("2026-11-12T12:45:00"),
  });
  const key = { harness: "claude", session: "s-planner" };
  assert.equal(parseSessionHeader(answer("claude", "s-other"), key), null);
  assert.equal(parseSessionHeader(answer("codex", "s-planner"), key), null);
  for (const bad of [null, "x", [], 7]) assert.equal(parseSessionHeader(bad, key), null);
  assert.deepEqual([header({ folder: null, started: null, name: "" })].map((h) => [h.folder, h.started, h.name]),
                   [[null, null, null]]);
  assert.equal(header({ started: "noon" }).started, null);
  assert.equal(header({ started: Number.NaN }).started, null);
});

test("the where: the folder's last segment, when it started, the harness; the drawing's words", () => {
  assert.equal(sessionWhere(header(), TODAY, UTC), "atlas-api · started today 12:45 · claude");
  assert.equal(sessionWhere(header({ folder: "/home/u/reviewer/" }), TODAY, UTC), "reviewer · started today 12:45 · claude");
  assert.equal(sessionWhere(header({ started: at("2026-11-11T21:15:00") }), TODAY, UTC),
               "atlas-api · started yesterday 21:15 · claude");
  assert.equal(sessionWhere(header({ started: null }), TODAY, UTC), "atlas-api · claude");
  assert.equal(sessionWhere(header({ folder: null }), TODAY, UTC), "started today 12:45 · claude");
  // Neither a folder nor a start: nothing, not the harness alone.
  assert.equal(sessionWhere(header({ folder: null, started: null }), TODAY, UTC), null);
  assert.equal(sessionWhere(null, TODAY, UTC), null);
});

test("started: today, yesterday, a weekday, a date; the time alone with no today held", () => {
  assert.equal(startedWords(at("2026-11-12T00:05:00"), TODAY, UTC), "started today 00:05");
  assert.equal(startedWords(at("2026-11-10T09:00:00"), TODAY, UTC), "started Tue 09:00");
  assert.equal(startedWords(at("2026-10-01T09:00:00"), TODAY, UTC), "started Oct 1 09:00");
  assert.equal(startedWords(at("2026-11-12T09:00:00"), null, UTC), "started 09:00");
  assert.equal(folderWord("C:\\work\\atlas-api"), "atlas-api");
  assert.equal(folderWord("/"), "/");
});

test("the Session line: its typed name, else its folder; its transcript's route", () => {
  assert.deepEqual(sessionLine(header({ name: "reviewer-Nov-08-2026 15:50" }), TODAY, UTC), {
    value: "reviewer-Nov-08-2026 15:50 · started today 12:45 · claude", route: "claude/s-planner",
  });
  assert.equal(sessionLine(header(), TODAY, UTC).value, "atlas-api · started today 12:45 · claude");
  assert.equal(sessionLine(header({ folder: null, started: null, name: "x" }), TODAY, UTC), null);
  assert.equal(sessionLine(null, TODAY, UTC), null);
});

test("a row's header: only once read with one; out or nothing reads none", () => {
  const reads = new Map([["claude/s-planner", { state: "read", header: header(), at: 0 }],
                         ["claude/s-builder-1", { state: "out" }],
                         ["claude/s-reviewer", { state: "read", header: null, at: 0 }]]);
  assert.equal(headerOf(reads, bound("planner")).folder, "/home/u/code/atlas-api");
  assert.equal(headerOf(reads, bound("builder-1")), null);
  assert.equal(headerOf(reads, bound("reviewer")), null);
  assert.equal(headerOf(reads, row("planner", { session_id: "s-planner", harness: "claude" })), null);
  assert.equal(headerOf(reads, null), null);
});
