import assert from "node:assert/strict";
import test from "node:test";

import {
  agentsLines, BOX_NOT_KNOWN, COPY_ID, detailsLine, EMPTY_BOX, LABELS, NOT_KNOWN, nowLine, openedOf, promptBoxLine,
  sessionLine, tildeForm,
} from "../src/opened-model.ts";
import { DRAFT_NOT_SENT } from "../src/rows-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  seconds: (at) => new Date(at * 1000).toISOString().slice(11, 19),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const TODAY = "2026-11-18";
const at = (hhmmss, day = TODAY) => Date.parse(`${day}T${hhmmss.length === 5 ? `${hhmmss}:00` : hhmmss}Z`) / 1000;

const ID = "aaaaaaaa-1111-4111-8111-111111111111";
const facts = (extra) => ({ session_id: ID, turn_number: 4, turn_open: false, turn_started_at: null,
                            agents: 0, draft: false, stale: false,
                            ready: { source: "startup", session_id: ID, at: at("08:20") },
                            at: at("19:00"), ended_at: null, live_error: null, ...extra });
const session = (extra) => ({ key: "claude-n1", harness: "claude", pid: 4321, session_id: ID,
  cwd: "/home/u/work/kestrel-web", started: at("08:20"), capabilities: ["agents", "deliver"],
  endpoint_version: "0.2.4", mod_version: "0.1.0", trouble: null, facts: facts(),
  agents: { entries: [], complete: true }, read_error: null, ended_at: null, has_box: true,
  unread_since: null, typed_name: null, ...extra });
const agent = (extra) => ({ id: "a1", kind: "subagent", state: "running", final: false,
                            description: null, parent_id: null, ...extra });

test("Now: working and idle, with and without a since-time, and turn 0 left off", () => {
  assert.equal(nowLine(session({ facts: facts({ turn_open: true, turn_started_at: at("18:52") }) }), TODAY, UTC),
               "working on turn 4 since 18:52");
  assert.equal(nowLine(session({ facts: facts({ turn_open: true }) }), TODAY, UTC), "working on turn 4");
  assert.equal(nowLine(session({ ended_at: at("17:35") }), TODAY, UTC), "idle since 17:35 · turn 4");
  assert.equal(nowLine(session(), TODAY, UTC), "idle · turn 4");
  assert.equal(nowLine(session({ facts: facts({ turn_number: 0 }) }), TODAY, UTC), "idle since 08:20");
  assert.equal(nowLine(session({ facts: facts({ turn_number: 0, ready: null }) }), TODAY, UTC), "idle");
  assert.equal(nowLine(session({ facts: null }), TODAY, UTC), null);
});

test("Agents: each entry with busy in the text colour, and not known when the list is not whole", () => {
  const lines = agentsLines(session({ agents: { entries: [
    agent({ description: "Check the plan" }), agent({ id: "a2", kind: "shell", state: "completed", final: true }),
    agent({ id: "a3", final: null, state: "waiting" }),
  ], complete: false } }));
  assert.deepEqual(lines, {
    entries: [
      { id: "a1", text: "Check the plan · subagent", state: "running", busy: true },
      { id: "a2", text: "shell", state: "completed", busy: false },
      { id: "a3", text: "subagent", state: "waiting", busy: true },
    ],
    notKnown: NOT_KNOWN,
  });
  assert.equal(agentsLines(session({ agents: { entries: [agent()], complete: true } })).notKnown, null);
  assert.deepEqual(agentsLines(session({ agents: null })), { entries: [], notKnown: NOT_KNOWN });
  assert.equal(agentsLines(session({ agents: null, capabilities: ["deliver"] })), null);
  // A whole list with no entry: no line. One that is not whole says so.
  assert.equal(agentsLines(session({ agents: { entries: [], complete: true } })), null);
  assert.deepEqual(agentsLines(session({ agents: { entries: [], complete: false } })),
                   { entries: [], notKnown: NOT_KNOWN });
});

test("Prompt box: draft not sent, empty, or not known for each draft case, only where turns reads the box", () => {
  const cases = [
    ["a draft", facts({ draft: true }), DRAFT_NOT_SENT],
    ["a box that throws", facts({ draft: null }), BOX_NOT_KNOWN],
    ["agents_fail with the box empty", facts({ draft: false, agents: null }), EMPTY_BOX],
    ["agents_fail with a draft", facts({ draft: true, agents: null }), DRAFT_NOT_SENT],
    ["live_fails", facts({ draft: null, agents: null, live_error: "failed" }), BOX_NOT_KNOWN],
  ];
  for (const [name, read, words] of cases) {
    assert.equal(promptBoxLine(session({ facts: read })), words, name);
    const opened = JSON.stringify(openedOf(session({ facts: read }), "/home/u", TODAY, UTC));
    assert.doesNotMatch(opened, /"may hold a draft"/, name);
  }
  assert.equal(BOX_NOT_KNOWN, "not known · it could not be read, so it may hold a draft");
  assert.equal(promptBoxLine(session({ has_box: false, facts: facts({ draft: true }) })), null);
  assert.equal(promptBoxLine(session({ facts: null })), null);
});

test("Session: the harness, the start and the folder in ~ form", () => {
  assert.equal(sessionLine(session(), "/home/u", TODAY, UTC), "Claude Code · started today 08:20 · ~/work/kestrel-web");
  assert.equal(sessionLine(session({ harness: "", started: null, cwd: null }), "/home/u", TODAY, UTC), "unknown harness");
  assert.equal(sessionLine(session({ harness: "codex" }), null, TODAY, UTC),
               "Codex · started today 08:20 · /home/u/work/kestrel-web");
  assert.equal(tildeForm("/home/u", "/home/u/"), "~");
  assert.equal(tildeForm("/home/user/x", "/home/u"), "/home/user/x");
  assert.equal(tildeForm("C:\\Users\\u\\x", "C:\\Users\\u"), "~\\x");
});

test("Details: pid, the ready part in each case, the runner only when it differs, can't read since", () => {
  assert.equal(detailsLine(session(), TODAY, UTC), "pid 4321 · ready since 08:20, from startup · turns 0.2.4 · runner 0.1.0");
  assert.equal(detailsLine(session({ mod_version: "0.2.4" }), TODAY, UTC), "pid 4321 · ready since 08:20, from startup · turns 0.2.4");
  const ready = (r) => detailsLine(session({ pid: null, mod_version: null, facts: facts({ ready: r }) }), TODAY, UTC);
  assert.equal(ready({ source: null, session_id: ID, at: at("08:20") }), "ready since 08:20 · turns 0.2.4");
  assert.equal(ready({ source: "resume", session_id: ID, at: null }), "ready, from resume · turns 0.2.4");
  assert.equal(ready({ source: null, session_id: ID, at: null }), "ready · turns 0.2.4");
  assert.equal(ready(null), "turns 0.2.4");
  assert.equal(detailsLine(session({ unread_since: at("19:09:58") }), TODAY, UTC),
               "pid 4321 · ready since 08:20, from startup · turns 0.2.4 · runner 0.1.0 · can't read since 19:09:58");
  assert.match(detailsLine(session({ unread_since: at("23:00:01", "2026-11-17") }), TODAY, UTC),
               /can't read since yesterday 23:00:01$/);
  assert.doesNotMatch(detailsLine(session(), TODAY, UTC), /can't read/);
});

test("Copy id copies the session id, and is absent while there is none", () => {
  assert.equal(openedOf(session(), "/home/u", TODAY, UTC).copyId, ID);
  assert.equal(openedOf(session({ session_id: null }), "/home/u", TODAY, UTC).copyId, null);
  assert.equal(openedOf(session({ session_id: "" }), "/home/u", TODAY, UTC).copyId, null);
});

test("the opened row's labels and its copy act", () => {
  assert.deepEqual(Object.values(LABELS), ["Now", "Agents", "Prompt box", "Session", "Details"]);
  assert.equal(COPY_ID, "Copy id");
});
