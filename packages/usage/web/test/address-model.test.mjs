import assert from "node:assert/strict";
import test from "node:test";

import {
  addressPath, addressRoute, addressWrite, limitAddress, NOTHING_OPEN, parseAddress, sameAddress,
  sessionAddress, SESSIONS, settled,
} from "../src/address-model.ts";

/** Every address the page has, with the piece it opens. */
const KNOWN = [
  ["", { piece: "none" }],
  ["limit/claude/plan/300", { piece: "limit", harness: "claude", limit: "plan", minutes: 300 }],
  ["limit/codex/codex/10080", { piece: "limit", harness: "codex", limit: "codex", minutes: 10080 }],
  ["limit/claude/opus/10080", { piece: "limit", harness: "claude", limit: "opus", minutes: 10080 }],
  ["sessions", { piece: "sessions", harness: null, session: null, agent: null }],
  ["sessions/claude/0190b000-0000-7000-8000-0000000000e1",
   { piece: "sessions", harness: "claude", session: "0190b000-0000-7000-8000-0000000000e1", agent: null }],
  ["sessions/codex/thread-1/a0190b00000000e1",
   { piece: "sessions", harness: "codex", session: "thread-1", agent: "a0190b00000000e1" }],
  ["projects", { piece: "projects" }],
  ["folders", { piece: "folders" }],
  ["models", { piece: "models" }],
  ["kinds", { piece: "kinds" }],
  ["prices", { piece: "prices" }],
];

test("every address opens one piece and is written back as the same address", () => {
  for (const [route, address] of KNOWN) {
    assert.deepEqual(parseAddress(route), address, route);
    assert.equal(addressRoute(address), route);
    assert.equal(addressPath(address), `/m/usage/${route}`);
  }
});

test("slashes around a route, a query and a fragment are not part of it", () => {
  assert.deepEqual(parseAddress("/sessions/"), SESSIONS);
  assert.deepEqual(parseAddress("models?x=1"), { piece: "models" });
  assert.deepEqual(parseAddress("prices#top"), { piece: "prices" });
  assert.deepEqual(parseAddress("/"), NOTHING_OPEN);
});

test("a name with a slash, a space or a percent sign survives the address", () => {
  for (const address of [limitAddress("codex", "a/b c%", 300), sessionAddress("claude", "s/1 %41", "sub agent/2"),
                         sessionAddress("claude", "é", null)]) {
    const route = addressRoute(address);
    assert.deepEqual(parseAddress(route), address, route);
    assert.equal(addressRoute(parseAddress(route)), route);
  }
  assert.equal(addressRoute(limitAddress("codex", "a/b", 300)), "limit/codex/a%2Fb/300");
});

test("a route the page does not have is unknown and opens nothing", () => {
  const unknown = [
    "nope", "limit", "limit/claude", "limit/claude/plan", "limit/claude/plan/0", "limit/claude/plan/0300",
    "limit/claude/plan/5h", "limit/claude/plan/-300", "limit/claude/plan/3e2", "limit/claude/plan/300/more",
    "limit/claude//300", "limit/claude/plan/99999999999999999999",
    "sessions/claude", "sessions/claude/s1/agent/more", "sessions//s1", "sessions/claude/%E0%A4%A",
    "projects/proj-a", "models/m-a", "kinds/x", "prices/x", "folders/x", "Sessions", "session",
  ];
  for (const route of unknown) {
    assert.deepEqual(parseAddress(route), { piece: "unknown" }, route);
    assert.equal(addressRoute(parseAddress(route)), "");
  }
});

test("the views the page had before its sections have no address", () => {
  for (const route of ["daily", "terms", "session/0190b000-0000-7000-8000-0000000000e1", "session/s1/agent-1"]) {
    assert.deepEqual(parseAddress(route), { piece: "unknown" }, route);
  }
});

test("projects and folders are one section, named as the install has it", () => {
  assert.deepEqual(settled({ piece: "projects" }, true), { piece: "folders" });
  assert.deepEqual(settled({ piece: "folders" }, true), { piece: "folders" });
  assert.deepEqual(settled({ piece: "folders" }, false), { piece: "projects" });
  assert.deepEqual(settled({ piece: "projects" }, false), { piece: "projects" });
  assert.deepEqual(settled({ piece: "models" }, true), { piece: "models" });
  assert.deepEqual(settled(SESSIONS, true), SESSIONS);
});

test("two addresses are the same when they open the same piece", () => {
  assert.equal(sameAddress(limitAddress("claude", "plan", 300), parseAddress("limit/claude/plan/300")), true);
  assert.equal(sameAddress(limitAddress("claude", "plan", 300), limitAddress("claude", "plan", 10080)), false);
  assert.equal(sameAddress(SESSIONS, sessionAddress("claude", "s1")), false);
  assert.equal(sameAddress({ piece: "none" }, { piece: "unknown" }), false);
});

test("the page rewrites the entry it is on, only when the address is another, only on its own page", () => {
  assert.equal(addressWrite(limitAddress("claude", "plan", 300), "/m/usage/"), "/m/usage/limit/claude/plan/300");
  assert.equal(addressWrite(NOTHING_OPEN, "/m/usage/limit/claude/plan/300"), "/m/usage/");
  assert.equal(addressWrite(SESSIONS, "/m/usage/sessions"), null);
  assert.equal(addressWrite(NOTHING_OPEN, "/m/usage/"), null);
  assert.equal(addressWrite(NOTHING_OPEN, "/m/usage"), "/m/usage/");
  assert.equal(addressWrite(SESSIONS, "/m/ui/"), null);
  assert.equal(addressWrite(SESSIONS, "/m/usagex/"), null);
  assert.equal(addressWrite(SESSIONS, "/"), null);
});
