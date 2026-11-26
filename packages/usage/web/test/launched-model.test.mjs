import assert from "node:assert/strict";
import test from "node:test";

import { launchOf, parseLaunched, RUNNING_PATH, RUNNING_TOPIC, runsWord } from "../src/launched-model.ts";
import { RUNNING_PATH as SESSIONS_RUNNING_PATH, RUNNING_TOPIC as SESSIONS_RUNNING_TOPIC, whereText }
  from "../../../sessions/web/src/running.ts";

const entry = (extra = {}) => ({ request_id: "r-1", harness: "claude", session_id: "s-1", name: "builder-1",
  identity: null, where: { target: "slot", slot: "4", project_key: "atlas-api", folder: "atlas-api" },
  started_at: "2026-11-08T11:00:00Z", model: "opus", effort: "high", cache: null, waits: null, covered: false,
  coverable: null, activity: null, ...extra });
const running = (sessions, extra = {}) => ({ state: "up", detail: null, count: sessions.length, sessions, ...extra });

test("the page reads launcher's running launches where the Sessions page does", () => {
  assert.equal(RUNNING_PATH, SESSIONS_RUNNING_PATH);
  assert.equal(RUNNING_TOPIC, SESSIONS_RUNNING_TOPIC);
});

test("a running launch is kept by harness and session id with its name, where it runs and its effort", () => {
  const launched = parseLaunched(running([entry(), entry({ request_id: "r-2", harness: "codex", session_id: "s-2",
    name: "planner", where: { target: "terminal", slot: null, project_key: null, folder: "kestrel" }, effort: null })]));
  assert.deepEqual([...launched.keys()], ["claude/s-1", "codex/s-2"]);
  assert.deepEqual(launched.get("claude/s-1"), { name: "builder-1", runs: "slot 4", effort: "high" });
  assert.deepEqual(launched.get("codex/s-2"), { name: "planner", runs: "terminal", effort: null });
});

test("where a launch runs begins as Home's where does", () => {
  for (const where of [
    { target: "slot", slot: "4", project_key: null, folder: "a" },
    { target: "slot", slot: null, project_key: null, folder: "a" },
    { target: "terminal", slot: null, project_key: null, folder: "a" },
    { target: "headless", slot: null, project_key: null, folder: "a" },
  ]) {
    assert.equal(`${runsWord(where)} · a`, whereText(where));
  }
  assert.equal(runsWord(null), null);
  assert.equal(runsWord({ target: "" }), null);
});

test("a launch with no session id, name or target is left out, and the first of two for one session kept", () => {
  const launched = parseLaunched(running([
    entry({ session_id: null }), entry({ request_id: "r-2", session_id: "s-2", name: "  " }),
    entry({ request_id: "r-3", session_id: "s-3", where: null }),
    entry({ request_id: "r-4", session_id: "s-4", name: "first" }),
    entry({ request_id: "r-5", session_id: "s-4", name: "second" }), "not an entry",
  ]));
  assert.deepEqual([...launched.keys()], ["claude/s-4"]);
  assert.equal(launched.get("claude/s-4").name, "first");
});

test("a name's runs of space are one space, and an empty effort is none", () => {
  const launched = parseLaunched(running([entry({ name: " build\n one ", effort: "" })]));
  assert.deepEqual(launched.get("claude/s-1"), { name: "build one", runs: "slot 4", effort: null });
});

test("a value launcher could not gather gives no launch, and a body that is no value gives null", () => {
  assert.equal(parseLaunched(running([entry()], { state: "error", detail: "failed" })).size, 0);
  assert.equal(parseLaunched(null), null);
  assert.equal(parseLaunched({ state: "up" }), null);
  assert.equal(parseLaunched([]), null);
});

test("a session is matched by harness and session id, and to nothing with no id, nothing read or no match", () => {
  const launched = parseLaunched(running([entry()]));
  assert.deepEqual(launchOf(launched, { harness: "claude", session_id: "s-1" }),
                   { name: "builder-1", runs: "slot 4", effort: "high" });
  assert.equal(launchOf(launched, { harness: "codex", session_id: "s-1" }), null);
  assert.equal(launchOf(launched, { harness: "claude", session_id: "s-9" }), null);
  assert.equal(launchOf(launched, { harness: "claude", session_id: null }), null);
  assert.equal(launchOf(null, { harness: "claude", session_id: "s-1" }), null);
});
