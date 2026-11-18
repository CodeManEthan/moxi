import assert from "node:assert/strict";
import test from "node:test";

import {
  dayTime, dayWord, factOf, folderName, groupOf, groupsOf, harnessName, isBusy, isWaiting,
  isWatchAlone, LIST_NOT_WHOLE, MAY_HOLD_DRAFT, newsOf, ordered, problemsOf, READ_ON_TIMER, rowLines,
  sinceOf, timeWord, TROUBLE_LEAD, UNREAD_LEAD,
} from "../src/rows-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  seconds: (at) => new Date(at * 1000).toISOString().slice(11, 19),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const TODAY = "2026-11-18";
/** 2026-11-18 at hh:mm UTC, in epoch seconds. */
const at = (hhmm, day = TODAY) => Date.parse(`${day}T${hhmm}:00Z`) / 1000;

const ID = "aaaaaaaa-1111-4111-8111-111111111111";
const facts = (extra) => ({ session_id: ID, turn_number: 4, turn_open: false, turn_started_at: null,
                            agents: 0, draft: false, stale: false,
                            ready: { source: "startup", session_id: ID, at: at("08:20") },
                            at: at("19:00"), ...extra });
const session = (extra) => ({ key: "claude-n1", harness: "claude", pid: 4321, session_id: ID,
  cwd: "/work/kestrel-web", started: at("08:20"), capabilities: ["agents", "deliver"],
  endpoint_version: "0.2.4", mod_version: "0.1.0", trouble: null, facts: facts(),
  agents: { entries: [], complete: true }, read_error: null, ended_at: null, has_box: true,
  unread_since: null, ...extra });
const agent = (extra) => ({ id: "a1", kind: "subagent", state: "running", final: false,
                            description: null, parent_id: null, ...extra });

test("day words each side of midnight, and the weekday within six days", () => {
  assert.equal(dayWord("2026-11-18", TODAY), "today");
  assert.equal(dayWord("2026-11-17", TODAY), "yesterday");
  assert.equal(dayWord("2026-11-19", TODAY), "tomorrow");
  assert.equal(dayWord("2026-11-13", TODAY), "Fri");
  assert.equal(dayWord("2026-11-11", TODAY), "Nov 11");
  assert.equal(dayTime(at("23:59", "2026-11-17"), TODAY, UTC), "yesterday 23:59");
  assert.equal(dayTime(at("00:01"), TODAY, UTC), "today 00:01");
  assert.equal(timeWord(at("00:01"), TODAY, UTC), "00:01");
  assert.equal(timeWord(at("23:59", "2026-11-17"), TODAY, UTC), "yesterday 23:59");
});

test("names by folder and start, without started, without cwd, without harness", () => {
  assert.equal(folderName(session(), TODAY, UTC), "kestrel-web · started today 08:20");
  assert.equal(folderName(session({ cwd: "/work/kestrel-web/" }), TODAY, UTC), "kestrel-web · started today 08:20");
  assert.equal(folderName(session({ started: at("21:10", "2026-11-17") }), TODAY, UTC),
               "kestrel-web · started yesterday 21:10");
  assert.equal(folderName(session({ started: at("09:00", "2026-11-14") }), TODAY, UTC),
               "kestrel-web · started Sat 09:00");
  assert.equal(folderName(session({ started: at("09:00", "2026-10-30") }), TODAY, UTC),
               "kestrel-web · started Oct 30 09:00");
  assert.equal(folderName(session({ started: null }), TODAY, UTC), "kestrel-web");
  assert.equal(folderName(session({ cwd: null }), TODAY, UTC), "Claude Code session");
  assert.equal(folderName(session({ cwd: null, harness: "codex" }), TODAY, UTC), "Codex session");
  assert.equal(folderName(session({ cwd: null, harness: "opencode" }), TODAY, UTC), "OpenCode session");
  assert.equal(folderName(session({ cwd: null, harness: "fake" }), TODAY, UTC), "fake session");
  assert.equal(folderName(session({ cwd: null, harness: "" }), TODAY, UTC), "A session");
  assert.equal(harnessName(""), "unknown harness");
});

test("groups by the rule: a problem first, then idle, then working", () => {
  assert.equal(groupOf(session()), "idle");
  assert.equal(groupOf(session({ facts: facts({ turn_open: true }) })), "working");
  assert.equal(groupOf(session({ trouble: "the runner is old" })), "problem");
  assert.equal(groupOf(session({ read_error: "its turn could not be read, so what shows may be old",
                                 unread_since: at("19:09") })), "problem");
  assert.equal(groupOf(session({ facts: facts({ turn_open: true, agents: null }) })), "problem");
  assert.equal(groupOf(session({ facts: null, read_error: "its turn could not be read, so what shows may be old",
                                 unread_since: at("19:09") })), "problem");
  assert.equal(groupOf(session({ facts: null })), null);
});

test("a watch that is down while the reads answer is not a problem", () => {
  const late = session({ read_error: "Moxi can't watch it, so what shows is read every 30 s and may be up to 30 s old" });
  assert.equal(isWatchAlone(late), true);
  assert.deepEqual(problemsOf(late), []);
  assert.equal(groupOf(late), "idle");
  assert.equal(isWatchAlone(session()), false);
  // Before its first read it waits like any other.
  assert.equal(isWaiting({ ...late, facts: null }), true);
  // The watch and a read: unread_since is set, so it is a problem.
  const both = session({ read_error: "Moxi can't watch it or read its turn, and tries again", unread_since: at("19:09") });
  assert.equal(groupOf(both), "problem");
  assert.deepEqual(problemsOf(both), [{ lead: UNREAD_LEAD, reason: "Moxi can't watch it or read its turn, and tries again" }]);
});

test("a session with no facts and no error waits and is left out of every group", () => {
  assert.equal(isWaiting(session({ facts: null })), true);
  assert.equal(isWaiting(session({ facts: null, trouble: "x" })), false);
  assert.equal(isWaiting(session()), false);
  assert.deepEqual(groupsOf([session({ facts: null })]), []);
});

test("each problem case, and a row with two", () => {
  assert.deepEqual(problemsOf(session({ trouble: "the runner is old" })),
                   [{ lead: TROUBLE_LEAD, reason: "the runner is old" }]);
  assert.deepEqual(problemsOf(session({ read_error: "its agents could not be read, so what shows may be old",
                                        unread_since: 1 })),
                   [{ lead: UNREAD_LEAD, reason: "its agents could not be read, so what shows may be old" }]);
  assert.deepEqual(problemsOf(session({ facts: facts({ agents: null }), unread_since: 1 })),
                   [{ lead: UNREAD_LEAD, reason: LIST_NOT_WHOLE }]);
  assert.deepEqual(problemsOf(session({ trouble: "t", facts: facts({ agents: null }), unread_since: 1 })),
                   [{ lead: TROUBLE_LEAD, reason: "t" }, { lead: UNREAD_LEAD, reason: LIST_NOT_WHOLE }]);
});

test("since for working, idle with ended_at, turn 0 with a ready of this id or another, and neither", () => {
  assert.equal(sinceOf(session({ facts: facts({ turn_open: true, turn_started_at: at("18:52") }) })), at("18:52"));
  // An open turn held for its tie has no start yet.
  assert.equal(sinceOf(session({ facts: facts({ turn_open: true, turn_started_at: null }) })), null);
  assert.equal(sinceOf(session({ ended_at: at("17:35") })), at("17:35"));
  // The later of the turn's end and a ready of this id.
  const cleared = facts({ ready: { source: "clear", session_id: ID, at: at("17:40") } });
  assert.equal(sinceOf(session({ ended_at: at("17:35"), facts: cleared })), at("17:40"));
  // A ready of another id is no part of it.
  const other = facts({ ready: { source: "clear", session_id: "other", at: at("17:40") } });
  assert.equal(sinceOf(session({ ended_at: at("17:35"), facts: other })), at("17:35"));
  assert.equal(sinceOf(session({ facts: facts({ turn_number: 0 }) })), at("08:20"));
  assert.equal(sinceOf(session({ facts: facts({ turn_number: 0, ready: { source: "startup", session_id: "other", at: at("08:20") } }) })), null);
  assert.equal(sinceOf(session({ facts: facts({ turn_number: 0, ready: { source: "startup", session_id: null, at: at("08:20") } }) })), null);
  assert.equal(sinceOf(session({ facts: facts({ turn_number: 0, session_id: null, ready: { source: "startup", session_id: null, at: at("08:20") } }) })), null);
  assert.equal(sinceOf(session({ facts: facts({ turn_number: 0, ready: null }) })), null);
  assert.equal(sinceOf(session()), null);
  assert.equal(sinceOf(session({ facts: null })), null);
});

test("order: since-time newest first, then started newest first, then key", () => {
  const a = session({ key: "a", ended_at: at("17:00") });
  const b = session({ key: "b", ended_at: at("18:00") });
  const c = session({ key: "c", started: at("09:00") });
  const d = session({ key: "d", started: at("10:00") });
  const e = session({ key: "e", started: null });
  const f = session({ key: "f", started: at("10:00") });
  assert.deepEqual(ordered([e, c, f, a, d, b]).map((s) => s.key), ["b", "a", "d", "f", "c", "e"]);
});

test("groups carry their labels, come problem, idle, working, and leave out a group with no row", () => {
  const views = groupsOf([
    session({ key: "w", facts: facts({ turn_open: true }) }),
    session({ key: "p1", trouble: "t" }),
    session({ key: "p2", facts: facts({ agents: null }), unread_since: 1 }),
    session({ key: "wait", facts: null }),
  ]);
  assert.deepEqual(views.map((v) => [v.group, v.label, v.sessions.map((s) => s.key)]),
                   [["problem", "2 problems", ["p1", "p2"]], ["working", "1 working", ["w"]]]);
  const all = groupsOf([
    session({ key: "w", facts: facts({ turn_open: true }) }),
    session({ key: "i" }),
    session({ key: "p", trouble: "t" }),
  ]);
  assert.deepEqual(all.map((v) => [v.group, v.label, v.sessions.map((s) => s.key)]),
                   [["problem", "1 problem", ["p"]], ["idle", "1 idle", ["i"]], ["working", "1 working", ["w"]]]);
  assert.equal(groupsOf([session({ trouble: "t" })])[0].label, "1 problem");
  assert.equal(groupsOf([session(), session({ key: "k2" })])[0].label, "2 idle");
});

test("the fact: since on an idle or working row, the state on a problem row", () => {
  assert.equal(factOf(session({ ended_at: at("17:35") }), "idle", TODAY, UTC), "since 17:35");
  assert.equal(factOf(session(), "idle", TODAY, UTC), null);
  assert.equal(factOf(session({ ended_at: at("23:10", "2026-11-17"), facts: facts({ ready: null }) }), "idle", TODAY, UTC), "since yesterday 23:10");
  const trouble = { trouble: "t" };
  assert.equal(factOf(session({ ...trouble, ended_at: at("17:35") }), "problem", TODAY, UTC), "idle since 17:35");
  assert.equal(factOf(session({ ...trouble, facts: facts({ turn_open: true, turn_started_at: at("18:52") }) }),
                      "problem", TODAY, UTC), "working since 18:52");
  assert.equal(factOf(session(trouble), "problem", TODAY, UTC), "idle");
  assert.equal(factOf(session({ ...trouble, facts: facts({ turn_open: true }) }), "problem", TODAY, UTC), "working");
  assert.equal(factOf(session({ ...trouble, facts: null }), "problem", TODAY, UTC), null);
});

test("busy is turns' rule: final not true, and a state still going", () => {
  assert.equal(isBusy(agent()), true);
  assert.equal(isBusy(agent({ final: null })), true);
  assert.equal(isBusy(agent({ state: "waiting" })), true);
  assert.equal(isBusy(agent({ state: "pending" })), true);
  assert.equal(isBusy(agent({ final: true })), false);
  assert.equal(isBusy(agent({ state: "completed" })), false);
});

test("each news phrase, one entry's description, and the busy count without a list", () => {
  const list = (entries) => session({ agents: { entries, complete: true } });
  assert.deepEqual(newsOf(list([agent({ description: "Check the plan" })])),
                   [{ text: "1 subagent busy", description: "Check the plan" }]);
  assert.deepEqual(newsOf(list([agent({ id: "1" }), agent({ id: "2", final: null, description: "x" })])),
                   [{ text: "2 subagents busy", description: null }]);
  assert.deepEqual(newsOf(list([
    agent({ id: "s", kind: "shell", description: "npm test" }),
    agent({ id: "t", kind: "teammate" }), agent({ id: "u", kind: "teammate" }),
    agent({ id: "d", final: true }),
  ])), [{ text: "2 teammates busy", description: null }, { text: "1 shell running", description: "npm test" }]);
  assert.deepEqual(newsOf(list([agent({ kind: "shell", id: "1" }), agent({ kind: "shell", id: "2" })])),
                   [{ text: "2 shells running", description: null }]);
  // No list: the count stands in, only above 0.
  assert.deepEqual(newsOf(session({ agents: null, facts: facts({ agents: 3 }) })),
                   [{ text: "3 agents busy", description: null }]);
  assert.deepEqual(newsOf(session({ agents: null, facts: facts({ agents: 0 }) })), []);
  assert.deepEqual(newsOf(session({ agents: null, facts: facts({ agents: null }) })), []);
});

test("the draft cases on the wire each read `may hold a draft` or nothing on the closed row", () => {
  const cases = [
    ["a draft", facts({ draft: true }), true],
    ["a box that throws", facts({ draft: true }), true],
    ["agents_fail with the box empty", facts({ draft: false, agents: null }), false],
    ["agents_fail with a draft", facts({ draft: true, agents: null }), true],
    ["live_fails", facts({ draft: true, agents: null }), true],
  ];
  for (const [name, read, shown] of cases) {
    const words = newsOf(session({ facts: read })).map((n) => n.text);
    assert.deepEqual(words, shown ? [MAY_HOLD_DRAFT] : [], name);
    const all = JSON.stringify(rowLines(session({ facts: read }), groupOf(session({ facts: read })), TODAY, null, UTC));
    assert.doesNotMatch(all, /box|not known|draft not sent/, name);
  }
  // A harness whose box turns doesn't read says nothing of a draft.
  assert.deepEqual(newsOf(session({ has_box: false, facts: facts({ draft: true }) })), []);
});

test("line two and three on a problem row: lead heavy, reason in the text colour, the rest after", () => {
  const two = session({ trouble: "the runner is old", facts: facts({ agents: null }), unread_since: 1,
                        agents: { entries: [agent({ description: "Review" })], complete: false },
                        ended_at: at("17:35") });
  const lines = rowLines(two, "problem", TODAY, null, UTC);
  assert.equal(lines.name, "kestrel-web · started today 08:20");
  assert.equal(lines.fact, "idle since 17:35");
  assert.equal(lines.lead, TROUBLE_LEAD);
  assert.equal(lines.leadHeavy, true);
  assert.equal(lines.rest, "the runner is old");
  assert.equal(lines.restStrong, true);
  assert.equal(lines.tail, null);
  assert.equal(lines.note, `${UNREAD_LEAD} · ${LIST_NOT_WHOLE} · 1 subagent busy · Review`);
  const one = rowLines(session({ trouble: "t" }), "problem", TODAY, null, UTC);
  assert.equal(one.note, null);
});

test("line two on another row: the first phrase leads, its description is the rest, the others the tail", () => {
  const busy = session({ facts: facts({ turn_open: true, turn_started_at: at("18:52"), draft: true }),
                         agents: { entries: [agent({ description: "Check the plan" }),
                                             agent({ id: "s", kind: "shell", description: "npm test" })],
                                   complete: true } });
  const lines = rowLines(busy, "working", TODAY, null, UTC);
  assert.equal(lines.fact, "since 18:52");
  assert.equal(lines.lead, "1 subagent busy");
  assert.equal(lines.leadHeavy, false);
  assert.equal(lines.rest, "Check the plan");
  assert.equal(lines.restStrong, false);
  assert.equal(lines.tail, `1 shell running · npm test · ${MAY_HOLD_DRAFT}`);
  assert.equal(lines.note, null);
  const quiet = rowLines(session(), "idle", TODAY, null, UTC);
  assert.deepEqual([quiet.lead, quiet.rest, quiet.tail, quiet.note], [null, null, null, null]);
});

test("a row whose watch alone is down keeps its lines and says on line three it is read every 30 s", () => {
  const late = { read_error: "Moxi can't watch it, so what shows is read every 30 s and may be up to 30 s old" };
  assert.equal(READ_ON_TIMER, "read every 30 s");
  const idle = rowLines(session(late), "idle", TODAY, null, UTC);
  assert.deepEqual([idle.lead, idle.tail, idle.note], [null, null, "read every 30 s"]);
  const busy = session({ ...late, facts: facts({ turn_open: true, turn_started_at: at("18:52") }),
                         agents: { entries: [agent({ description: "Check the plan" })], complete: true } });
  assert.equal(groupOf(busy), "working");
  const working = rowLines(busy, "working", TODAY, null, UTC);
  assert.deepEqual([working.fact, working.lead, working.rest, working.note],
                   ["since 18:52", "1 subagent busy", "Check the plan", "read every 30 s"]);
  // On a problem row it comes after the problems and the news.
  const trouble = rowLines(session({ ...late, trouble: "t" }), "problem", TODAY, null, UTC);
  assert.equal(trouble.note, "read every 30 s");
  // A watch down with a read: a problem, and no timer line.
  const both = session({ read_error: "Moxi can't watch it or read its turn, and tries again", unread_since: at("19:09") });
  assert.equal(rowLines(both, "problem", TODAY, null, UTC).note, null);
});

test("a launch's name and where take the folder's place", () => {
  const lines = rowLines(session(), "idle", TODAY, { name: "planner-Nov-08-2026 12:45", where: "slot 3 · atlas-api" }, UTC);
  assert.equal(lines.name, "planner-Nov-08-2026 12:45");
  assert.equal(lines.where, "slot 3 · atlas-api");
  assert.equal(rowLines(session(), "idle", TODAY, null, UTC).where, null);
});
