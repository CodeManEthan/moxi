import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import {
  agentsLines, BOX_NOT_KNOWN, busyWords, DRAFT_NOT_SENT, EMPTY_BOX, LABELS, LIST_NOT_WHOLE, LIVE_READ_FAILED, liveOpened,
  liveRead, liveRow, NO_ANSWER_IN_TIME, NOT_KNOWN, nowLine, pairsOf, parseLive, problemsOf, promptBoxLine, TROUBLE_LEAD,
  UNREAD_LEAD, unreadRow,
} from "../src/live.ts";
import { listView, NO_FILTERS, queryOf, refresh } from "../src/list.ts";
import { runningRead } from "../src/running.ts";

const at = (hhmm) => Date.parse(`2026-11-08T${hhmm}:00Z`) / 1000;
const clock = (epoch) => new Date(epoch * 1000).toISOString().slice(11, 16);

// One session as turns' `/api/sessions` sends it (its declared shape, every field).
const facts = (fields = {}) => ({
  session_id: "s1", turn_number: 9, turn_open: true, turn_started_at: at("18:34"), agents: 0, draft: false, stale: false,
  ready: { source: "startup", session_id: "s1", at: at("12:45") }, at: at("19:09"), ended_at: null, live_error: null,
  ...fields,
});
const agent = (id, kind, state = "running", fields = {}) =>
  ({ id, kind, state, final: null, description: null, parent_id: null, ...fields });
const session = (key, fields = {}) => ({
  key, harness: "claude", pid: 4242, session_id: "s1", cwd: "/work/atlas-api", started: at("12:45"),
  capabilities: ["agents"], endpoint_version: "1.0", mod_version: "1.0", trouble: null, facts: facts(),
  agents: { entries: [], complete: true }, read_error: null, ended_at: null, has_box: true, unread_since: null, typed_name: null,
  ...fields,
});
const list = (sessions) => ({ listed: true, detail: null, home: "/home/you", reach: null, sessions });
const one = (fields) => parseLive(list([session("claude-1", fields)])).sessions[0];

// A launch as launcher's `/api/running` sends it, parsed: the fields the join reads.
const launch = (request, id) => ({ request_id: request, session_id: id });

test("the route's value: a session with no key is left out, a field that isn't its kind reads as none", () => {
  const got = parseLive(list([session("a"), { harness: "claude" }, null,
                              session("b", { facts: { turn_open: "yes" }, agents: { entries: [agent("x", 5)] } })]));
  assert.deepEqual(got.sessions.map((s) => s.key), ["a", "b"]);
  assert.equal(got.sessions[1].facts, null);
  assert.deepEqual(got.sessions[1].agents, { entries: [], complete: false });
  assert.equal(parseLive({ listed: "yes", sessions: [] }), null);
  assert.equal(parseLive({ listed: true }), null);
  assert.deepEqual(parseLive(list([])), { sessions: [] });
});

test("a read is the value, a failure in turns' words, or nothing when turns is gone", () => {
  assert.equal(liveRead({ status: 200, data: list([session("a")]) }).value.sessions.length, 1);
  assert.equal(liveRead({ status: 404, data: null }), null);
  assert.deepEqual(liveRead({ status: 0, data: null }), { value: null, failure: "no answer from the shell" });
  assert.deepEqual(liveRead({ status: 502, data: null }), { value: null, failure: "turns' page server answered 502" });
  assert.deepEqual(liveRead({ status: 200, data: "x" }),
                   { value: null, failure: "turns' page server sent an answer that can't be read" });
});

test("a launch pairs with the one turns session of its id; an id two of either carry pairs none", () => {
  const liveValue = parseLive(list([session("k1", { session_id: "a" }), session("k2", { session_id: "b" }),
                                    session("k3", { session_id: "b" }), session("k4", { session_id: "c" }),
                                    session("k5", { session_id: null })]));
  const paired = pairsOf([launch("r1", "a"), launch("r2", "b"), launch("r3", "c"), launch("r4", "c"),
                          launch("r5", null), launch("r6", "d")], liveValue);
  assert.deepEqual([...paired].map(([request, s]) => [request, s.key]), [["r1", "k1"]]);
});

test("a running row says working while a turn is open, and no state word while it is idle", () => {
  assert.deepEqual(liveRow(one({})), { words: "working", trouble: null });
  assert.deepEqual(liveRow(one({ facts: facts({ turn_open: false }) })), { words: null, trouble: null });
  // Before turns' first read of the session nothing is said of it.
  assert.deepEqual(liveRow(one({ facts: null, agents: null })), { words: null, trouble: null });
});

test("then what is busy, by kind, in turns' order and words; a finished one is not counted", () => {
  const busy = one({ agents: { complete: true, entries: [
    agent("s3", "shell"), agent("a1", "subagent", "running", { description: "Check the plan" }),
    agent("a2", "subagent", "done", { final: true }), agent("t1", "teammate", "waiting"), agent("t2", "teammate", "pending"),
    agent("s1", "shell"), agent("s2", "shell", "running", { final: true }), agent("x", "subagent", "failed"),
  ] } });
  assert.deepEqual(busyWords(busy), ["1 subagent busy", "2 teammates busy", "2 shells running"]);
  assert.equal(liveRow(busy).words, "working · 1 subagent busy · 2 teammates busy · 2 shells running");
  const shell = one({ facts: facts({ turn_open: false }), agents: { complete: true, entries: [agent("s1", "shell")] } });
  assert.equal(liveRow(shell).words, "1 shell running");
  // With no list, turns' count of busy agents.
  assert.equal(liveRow(one({ agents: null, facts: facts({ agents: 2 }) })).words, "working · 2 agents busy");
  // A draft is said on the opened row only.
  assert.equal(liveRow(one({ facts: facts({ turn_open: false, draft: true }) })).words, null);
});

test("a kind another lead already counts is left out", () => {
  const waiting = one({ facts: facts({ turn_open: false }), agents: { complete: true, entries: [
    agent("a1", "subagent"), agent("a2", "subagent"), agent("s1", "shell")] } });
  assert.equal(liveRow(waiting, ["subagent"]).words, "1 shell running");
  const both = one({ facts: facts({ turn_open: false }), agents: { complete: true, entries: [
    agent("a1", "subagent"), agent("t1", "teammate"), agent("s1", "shell")] } });
  assert.equal(liveRow(both, ["subagent", "teammate"]).words, "1 shell running");
  assert.equal(liveRow(both, []).words, "1 subagent busy · 1 teammate busy · 1 shell running");
  assert.equal(liveRow(one({ facts: facts({ turn_open: false, agents: 2 }), agents: null }), ["subagent"]).words, null);
});

test("trouble is one dim line in turns' words, each problem in turns' order", () => {
  assert.deepEqual(liveRow(one({ facts: facts({ turn_open: false }), read_error: "the session did not answer in time.",
                                 unread_since: at("19:00") })),
                   { words: null, trouble: `${UNREAD_LEAD}: the session did not answer in time` });
  const both = liveRow(one({ trouble: "Codex load verification did not finish within 30 s", facts: facts({ agents: null }),
                             agents: null }));
  assert.equal(both.words, "working");
  assert.equal(both.trouble, `${TROUBLE_LEAD}: Codex load verification did not finish within 30 s · `
                             + `${UNREAD_LEAD}: ${LIST_NOT_WHOLE}`);
  // Why the live part did not read, as its facts say.
  for (const [liveError, reason] of [["timeout", NO_ANSWER_IN_TIME], ["failed", LIVE_READ_FAILED], [null, LIST_NOT_WHOLE]]) {
    assert.equal(liveRow(one({ facts: facts({ agents: null, live_error: liveError }), agents: null })).trouble,
                 `${UNREAD_LEAD}: ${reason}`, String(liveError));
  }
  // A watch that is down while the reads answer is no problem.
  assert.equal(liveRow(one({ read_error: "the watch closed", unread_since: null })).trouble, null);
  assert.deepEqual(unreadRow("turns' page server answered 502"),
                   { words: null, trouble: "Moxi can't read its live state: turns' page server answered 502" });
});

test("the opened row: Now, Agents and Prompt box, each only with its fact", () => {
  const opened = liveOpened(one({ agents: { complete: false, entries: [
    agent("a1", "subagent", "running", { description: "Check builder-1's branch" }), agent("a2", "subagent", "done")] } }),
  clock);
  assert.deepEqual(opened, {
    now: "working on turn 9 since 18:34",
    agents: { entries: [{ id: "a1", text: "Check builder-1's branch · subagent", state: "running", busy: true },
                        { id: "a2", text: "subagent", state: "done", busy: false }], notKnown: NOT_KNOWN },
    promptBox: EMPTY_BOX,
  });
  assert.equal(nowLine(one({ facts: facts({ turn_open: false }), ended_at: at("18:52") }), clock),
               "idle since 18:52 · turn 9");
  assert.equal(nowLine(one({ facts: facts({ turn_open: false, turn_number: 0 }) }), clock), "idle since 12:45");
  assert.equal(nowLine(one({ facts: facts({ turn_started_at: null }) }), clock), "working on turn 9");
  assert.equal(nowLine(one({ facts: null }), clock), null);
  assert.equal(agentsLines(one({})), null);
  assert.deepEqual(agentsLines(one({ agents: null })), { entries: [], notKnown: NOT_KNOWN });
  assert.equal(agentsLines(one({ agents: null, capabilities: [] })), null);
  assert.equal(promptBoxLine(one({ facts: facts({ draft: true }) })), DRAFT_NOT_SENT);
  // A box that was not read is not known, never empty.
  assert.equal(promptBoxLine(one({ facts: facts({ draft: null }) })), BOX_NOT_KNOWN);
  assert.equal(promptBoxLine(one({ facts: facts({ draft: "yes" }) })), BOX_NOT_KNOWN);
  assert.equal(promptBoxLine(one({ has_box: false, facts: facts({ draft: true }) })), null);
  assert.deepEqual(LABELS, { now: "Now", agents: "Agents", promptBox: "Prompt box" });
});

test("the words and rules are turns' own, where its source sits beside sessions'", async () => {
  const rows = new URL("../../../turns/web/src/rows-model.ts", import.meta.url);
  const opened = new URL("../../../turns/web/src/opened-model.ts", import.meta.url);
  const feed = new URL("../../../turns/web/src/feed.ts", import.meta.url);
  if (!existsSync(rows) || !existsSync(opened) || !existsSync(feed)) return;
  const turns = { ...await import(rows.href), ...await import(opened.href), ...await import(feed.href) };
  const ours = { TROUBLE_LEAD, UNREAD_LEAD, LIST_NOT_WHOLE, NO_ANSWER_IN_TIME, LIVE_READ_FAILED, DRAFT_NOT_SENT, NOT_KNOWN,
                 EMPTY_BOX, BOX_NOT_KNOWN };
  for (const name of Object.keys(ours)) assert.equal(ours[name], turns[name], name);
  for (const key of Object.keys(LABELS)) assert.equal(LABELS[key], turns.LABELS[key], key);
  for (const status of [0, 200, 502]) assert.equal(liveRead({ status, data: null }).failure, turns.failureWhy(status));
  const utc = { time: clock, seconds: clock, day: (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10) };
  const raws = [
    session("a"), session("b", { facts: facts({ turn_open: false }), ended_at: at("18:52") }),
    session("c", { facts: facts({ turn_open: false, turn_number: 0 }) }), session("d", { facts: null, agents: null }),
    session("e", { agents: { complete: false, entries: [agent("x", "subagent"), agent("y", "shell"), agent("z", "teammate"),
                                                        agent("w", "shell", "done", { final: true })] } }),
    session("f", { agents: null, facts: facts({ agents: 3 }) }), session("g", { facts: facts({ draft: true }) }),
    session("h", { trouble: "it broke", read_error: "no answer", unread_since: at("19:00"), facts: facts({ agents: null }) }),
    session("i", { read_error: "the watch closed" }), session("j", { agents: null, capabilities: [] }),
    session("k", { facts: facts({ draft: null }) }), session("l", { has_box: false, facts: facts({ draft: null }) }),
    session("m", { agents: null, facts: facts({ agents: null, live_error: "timeout" }) }),
    session("n", { agents: null, facts: facts({ agents: null, live_error: "failed" }) }),
  ];
  parseLive(list(raws)).sessions.forEach((mine, index) => {
    const theirs = raws[index];
    assert.deepEqual(problemsOf(mine), turns.problemsOf(theirs), mine.key);
    assert.deepEqual(busyWords(mine), turns.newsOf(theirs).map((news) => news.text).filter((text) => text !== DRAFT_NOT_SENT),
                     mine.key);
    assert.equal(liveRow(mine).words?.startsWith("working") ?? false, turns.stateOf(theirs) === "working", mine.key);
    assert.equal(nowLine(mine, clock), turns.nowLine(theirs, "2026-11-08", utc), mine.key);
    assert.deepEqual(agentsLines(mine), turns.agentsLines(theirs), mine.key);
    assert.equal(promptBoxLine(mine), turns.promptBoxLine(theirs), mine.key);
  });
});

// --- the running rows -------------------------------------------------------------------------

const UTC = { clock, day: (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10),
              start: (day) => Date.parse(`${day}T00:00:00Z`) / 1000 };
const TODAY = "2026-11-08";
const iso = (hhmm) => `${TODAY}T${hhmm}:00Z`;
// One running launch as launcher's `/api/running` sends it.
const entry = (request, fields = {}) => ({
  request_id: request, harness: "claude", session_id: `s-${request}`, name: `${request}-Nov-08-2026 12:45`, identity: null,
  where: { target: "slot", slot: "3", project_key: "atlas-api", folder: "atlas-api" }, started_at: iso("12:45"),
  cache: { state: "warm", until: iso("19:52"), estimated: false }, waits: null, covered: false, coverable: null,
  activity: { at: iso("18:52"), by: "agent", text: "Steps 1 and 2 match the plan." }, ...fields,
});
const up = (sessions) => runningRead({ status: 200, data: { state: "up", detail: null, count: sessions.length, sessions } });
const listAnswer = { shape: 1, now: at("19:10"), today: TODAY, all: 1, total: 1, unreadable: 0, roots: [], harnesses: [],
                     groups: [{ key: TODAY, count: 1, rows: [], before: null, program: { count: 0 } }] };
const state = refresh(null, listAnswer, queryOf(NO_FILTERS, TODAY, UTC)).state;
const turnsUp = (sessions) => liveRead({ status: 200, data: list(sessions) });
const rowsOf = (turnsRead, entries, options = {}) =>
  listView(state, { launcher: true, running: up(entries), zone: UTC, readsTurns: true, turnsRead, ...options }).running.rows;

test("a running row leads line two with turns' words, its trouble on the third line", () => {
  const turnsRead = turnsUp([
    session("k-a", { session_id: "s-a", agents: { complete: true, entries: [agent("x", "subagent")] } }),
    session("k-b", { session_id: "s-b", facts: facts({ turn_open: false }),
                     agents: { complete: true, entries: [agent("y", "shell")] } }),
    session("k-c", { session_id: "s-c", facts: facts({ turn_open: false }), read_error: "the session did not answer in time",
                     unread_since: at("19:00") }),
  ]);
  const [a, b, c, d] = rowsOf(turnsRead, [entry("a"), entry("b", { activity: { at: iso("18:38"), by: "agent", text: "x" } }),
                                          entry("c", { activity: { at: iso("17:20"), by: "agent", text: "y" } }),
                                          entry("d", { activity: { at: iso("17:00"), by: "agent", text: "z" } })]);
  assert.deepEqual([a.lead, a.leadHeavy, a.leadMore, a.note], ["working · 1 subagent busy", false, null, null]);
  assert.deepEqual([b.lead, b.note], ["1 shell running", null]);
  assert.deepEqual([c.lead, c.note], [null, "Moxi can't read its live state: the session did not answer in time"]);
  // A launch turns has no session for says nothing of it.
  assert.deepEqual([d.lead, d.note, d.liveLines], [null, null, null]);
  assert.equal(a.liveLines.now, "working on turn 9 since 18:34");
});

test("after launcher's heavy lead turns' words come second, without the subagents it already counts", () => {
  const waiting = entry("w", { waits: { kind: "subagents", count: 2 }, coverable: { cap: 3 } });
  const directed = entry("v", { waits: { kind: "agents", count: 1 }, coverable: { cap: 3 } });
  const busy = (id) => session(`k-${id}`, { session_id: `s-${id}`, facts: facts({ turn_open: false }), agents: {
    complete: true, entries: [agent("x", "subagent"), agent("y", "subagent"), agent("z", "shell")] } });
  const rows = rowsOf(turnsUp([busy("w"), busy("v")]), [waiting, directed]);
  const w = rows.find((one) => one.request === "w");
  const v = rows.find((one) => one.request === "v");
  assert.deepEqual([w.lead, w.leadHeavy, w.leadMore], ["waiting on 2 subagents", true, "1 shell running"]);
  assert.deepEqual([v.lead, v.leadHeavy, v.leadMore], ["waiting on 1 agent", true, "2 subagents busy · 1 shell running"]);
});

test("after waiting on n subagents, a busy teammate is not counted again either", () => {
  const waiting = (request, count) => entry(request, { waits: { kind: "subagents", count }, coverable: { cap: 3 } });
  const directed = (request) => entry(request, { waits: { kind: "agents", count: 1 }, coverable: { cap: 3 } });
  const busy = (id, ...kinds) => session(`k-${id}`, { session_id: `s-${id}`, facts: facts({ turn_open: false }), agents: {
    complete: true, entries: kinds.map((kind, index) => agent(`${id}${index}`, kind)) } });
  const rows = rowsOf(turnsUp([busy("both", "subagent", "teammate"), busy("sub", "subagent"), busy("mate", "teammate"),
                               busy("shell", "shell"), busy("named", "teammate")]),
                      [waiting("both", 2), waiting("sub", 1), waiting("mate", 1), waiting("shell", 1), directed("named")]);
  const row = (request) => rows.find((one) => one.request === request);
  // One subagent and one teammate are launcher's two.
  assert.deepEqual([row("both").lead, row("both").leadMore], ["waiting on 2 subagents", null]);
  assert.deepEqual([row("sub").lead, row("sub").leadMore], ["waiting on 1 subagent", null]);
  assert.deepEqual([row("mate").lead, row("mate").leadMore], ["waiting on 1 subagent", null]);
  // Launcher never counts a shell.
  assert.deepEqual([row("shell").lead, row("shell").leadMore], ["waiting on 1 subagent", "1 shell running"]);
  // The agents of `waiting on n agents` are comms names, not turns' agents.
  assert.deepEqual([row("named").lead, row("named").leadMore], ["waiting on 1 agent", "1 teammate busy"]);
});

test("a refused press's words come before turns' trouble on the third line", () => {
  const turnsRead = turnsUp([session("k-a", { session_id: "s-a", trouble: "it broke" })]);
  const presses = { waiting: {}, notes: { a: "Not kept warm: it is not waiting" } };
  const [a] = listView(state, { launcher: true, running: up([entry("a")]), zone: UTC, readsTurns: true, turnsRead, presses })
    .running.rows;
  assert.equal(a.note, "Not kept warm: it is not waiting · Its link to Moxi has trouble: it broke");
});

test("when turns' list can't be read, each launch with a session says so; without turns, or before its read, nothing", () => {
  const [a, b] = rowsOf(liveRead({ status: 502, data: null }), [entry("a"), entry("b", { session_id: null })]);
  assert.deepEqual([a.lead, a.note], [null, "Moxi can't read its live state: turns' page server answered 502"]);
  assert.deepEqual([b.lead, b.note], [null, null]);
  const turnsRead = turnsUp([session("k-a", { session_id: "s-a" })]);
  for (const options of [{ readsTurns: false }, { turnsRead: null }, { turnsRead: liveRead({ status: 404, data: null }) }]) {
    const [row] = rowsOf(turnsRead, [entry("a")], options);
    assert.deepEqual([row.lead, row.leadMore, row.note, row.liveLines], [null, null, null, null], JSON.stringify(options));
  }
});
