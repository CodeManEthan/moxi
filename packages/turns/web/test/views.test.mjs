import assert from "node:assert/strict";
import test from "node:test";

import { ago, NO_ID, pageView, rowView, span } from "../src/views.ts";

const NOW = 10_000;
const FACTS = { turn_number: 4, turn_open: true, agents: 2, draft: false, stale: false,
                ready: { source: "resume", at: NOW - 180 }, at: NOW - 1 };
const session = (extra) => ({ key: "claude-n1", harness: "claude", pid: 4321,
  session_id: "aaaaaaaa-1111-4111-8111-111111111111", cwd: "/work/proj", started: NOW - 300,
  capabilities: ["agents", "deliver"], endpoint_version: "2.0", mod_version: "0.2.0",
  trouble: null, facts: FACTS, agents: { entries: [], complete: true }, read_error: null, ...extra });
const up = (sessions, extra) => ({ loading: false, gone: false, failure: null,
                                   value: { listed: true, detail: null, sessions, ...extra } });

test("span and ago", () => {
  assert.equal(span(45), "45s");
  assert.equal(span(-3), "0s");
  assert.equal(span(2460), "41m");
  assert.equal(span(3840), "1h4m");
  assert.equal(span(7200), "2h");
  assert.equal(span(3 * 86400 + 5), "3d");
  assert.equal(ago(NOW - 300, NOW), "5m ago");
  assert.equal(ago(0, null), "at 1970-01-01T00:00:00Z");
});

test("a healthy session's row", () => {
  const row = rowView(session(), NOW);
  assert.deepEqual(row, {
    key: "claude-n1", harness: "claude", shortId: "aaaaaaaa",
    sessionId: "aaaaaaaa-1111-4111-8111-111111111111", cwd: "/work/proj",
    turn: "turn 4", open: true,
    facts: ["started 5m ago", "pid 4321", "2 agents busy", "ready from resume 3m ago"],
    versions: "endpoint 2.0, mod 0.2.0", stale: false, trouble: null, readError: null,
    agents: [], agentsIncomplete: false,
  });
});

test("a session before its facts are read, with no id and no versions", () => {
  const row = rowView(session({ session_id: null, facts: null, agents: null, harness: "",
                                endpoint_version: null, mod_version: null, pid: null,
                                started: null, cwd: null }), NOW);
  assert.equal(row.shortId, NO_ID);
  assert.equal(row.sessionId, null);
  assert.equal(row.harness, "unknown harness");
  assert.equal(row.turn, null);
  assert.equal(row.open, null);
  assert.deepEqual(row.facts, ["facts not read yet"]);
  assert.equal(row.versions, null);
  assert.equal(row.cwd, null);
  assert.equal(row.agentsIncomplete, false);
});

test("the facts' words: one agent, unknown agents, a draft, a ready with no source or time", () => {
  const one = rowView(session({ facts: { ...FACTS, agents: 1, draft: true, ready: null } }), NOW);
  assert.deepEqual(one.facts, ["started 5m ago", "pid 4321", "1 agent busy", "draft in the prompt box"]);
  const unknown = rowView(session({ facts: { ...FACTS, agents: null, ready: { source: null, at: null } } }), NOW);
  assert.deepEqual(unknown.facts.slice(2), ["agents unknown", "ready"]);
  const idle = rowView(session({ facts: { ...FACTS, turn_open: false, agents: 0 } }), NOW);
  assert.equal(idle.open, false);
  assert.equal(idle.facts[2], "0 agents busy");
});

test("a stale reading, trouble and a read error pass through", () => {
  const row = rowView(session({ facts: { ...FACTS, stale: true }, trouble: "the box is stuck",
                                read_error: "turns could not read this session's facts; what is shown may be old" }), NOW);
  assert.equal(row.stale, true);
  assert.equal(row.trouble, "the box is stuck");
  assert.match(row.readError, /may be old/);
});

test("agents are listed with state and final, and an incomplete list is said", () => {
  const entries = [
    { id: "a1", kind: "subagent", state: "running", final: false, description: "<b>look</b>", parent_id: null },
    { id: "a2", kind: "shell", state: "done", final: true, description: null, parent_id: "a1" },
    { id: "a3", kind: "teammate", state: "idle", final: null, description: null, parent_id: null },
  ];
  const row = rowView(session({ agents: { entries, complete: false } }), NOW);
  assert.deepEqual(row.agents, [
    { id: "a1", kind: "subagent", state: "running", final: "not final", description: "<b>look</b>" },
    { id: "a2", kind: "shell", state: "done", final: "final", description: null },
    { id: "a3", kind: "teammate", state: "idle", final: null, description: null },
  ]);
  assert.equal(row.agentsIncomplete, true);
});

test("the page: loading, scanning, empty, rows, a scan failure, a failed request, gone", () => {
  assert.equal(pageView({ loading: true, gone: false, failure: null, value: null }, null).state, "loading");
  assert.equal(pageView(up([], { listed: false }), NOW).state, "scanning");
  assert.equal(pageView(up([]), NOW).state, "empty");
  const rows = pageView(up([session(), session({ key: "codex-n2" })]), NOW);
  assert.equal(rows.state, "up");
  assert.deepEqual(rows.rows.map((row) => row.key), ["claude-n1", "codex-n2"]);
  assert.equal(rows.scan, null);
  const scan = pageView(up([session()], { detail: "turns' page server could not list the sessions (OSError); its log has the details" }), NOW);
  assert.equal(scan.state, "up");
  assert.match(scan.scan, /could not list/);
  const failed = pageView({ ...up([session()]), failure: { status: 0, why: "no answer from the shell" } }, NOW);
  assert.deepEqual([failed.state, failed.failure, failed.rows.length], ["failed", "no answer from the shell", 1]);
  const gone = pageView({ ...up([session()]), gone: true }, NOW);
  assert.deepEqual([gone.state, gone.rows.length], ["gone", 0]);
});
