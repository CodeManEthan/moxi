import assert from "node:assert/strict";
import test from "node:test";

import { openTurns, TURNS_PATH, workingIn } from "../src/turns-model.ts";

// One session as turns' `/api/sessions` sends it (its declared shape, every field), its
// facts read under `session_id`.
const facts = (session_id, turn_open, fields = {}) => ({
  session_id, turn_number: 3, turn_open, turn_started_at: turn_open ? 1_763_000_000 : null, agents: 0, draft: false,
  stale: false, ready: null, at: 1_763_000_100, ...fields,
});
const session = (key, session_id, sessionFacts) => ({
  key, harness: "claude", pid: 4242, session_id, cwd: "/w/atlas-api", started: 1_762_990_000,
  capabilities: ["agents"], endpoint_version: "1", mod_version: "1", trouble: null, facts: sessionFacts,
  agents: null, read_error: null, ended_at: null, has_box: false, unread_since: null,
});
const list = (sessions, fields = {}) => ({ listed: true, detail: null, home: "/w", reach: null, sessions, ...fields });

const row = (qualified, fields = {}) => ({
  qualified, name: qualified.split("@")[0], scope: null, live: false, kind: "agent", module: null, tier: null,
  adapter: null, directors: [], session_id: null, bound_at: null, awaiting: [], cache: null, holder: null, ...fields,
});
const bound = (qualified, session_id) =>
  row(qualified, { live: true, kind: null, tier: "worker", adapter: "claude", session_id });

test("the route is turns' list of sessions", () => {
  assert.equal(TURNS_PATH, "api/sessions");
});

test("the open turns: each session whose facts say a turn is open, by the id they were read under", () => {
  const value = list([
    session("claude-a", "s-planner", facts("s-planner", true)),
    session("claude-b", "s-reviewer", facts("s-reviewer", false)),
    session("claude-c", "s-builder", null),
    // The facts speak for the id they were read under, not the session's newer one.
    session("claude-d", "s-new", facts("s-old", true)),
    session("claude-e", "s-blank", facts(null, true)),
    session("claude-f", "s-odd", facts("s-odd", "yes")),
    null, 7,
  ]);
  assert.deepEqual([...openTurns(value)], ["s-planner", "s-old"]);
  assert.deepEqual([...openTurns(list([], { listed: false }))], []);
});

test("a value that isn't turns' list reads as none", () => {
  for (const bad of [null, undefined, 3, "x", [], { sessions: null }, { sessions: {} }]) {
    assert.equal(openTurns(bad), null);
  }
});

test("an address is working when its bound row's session has a turn open", () => {
  const rows = [bound("planner", "s-planner"), bound("reviewer", "s-reviewer"), row("docs-writer"),
                row("builder-2", { session_id: "s-planner" }), bound("builder-1", null)];
  const working = workingIn(rows, new Set(["s-planner", "s-other"]));
  assert.equal(working("planner"), true);
  assert.equal(working("reviewer"), false, "its turn is closed");
  assert.equal(working("docs-writer"), false, "no session");
  assert.equal(working("builder-2"), false, "a row no session holds now");
  assert.equal(working("builder-1"), false, "a bound row with no session id");
  assert.equal(working("nobody"), false, "not on the roster");
  assert.equal(workingIn(rows, null)("planner"), false, "no read");
  assert.equal(workingIn([], new Set(["s-planner"]))("planner"), false, "no roster");
});
