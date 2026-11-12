import assert from "node:assert/strict";
import test from "node:test";

import {
  actsOf, allLabel, allPlace, answered, caseOf, clock, expired, factOf, hrefOf, KEEP_WARM_PATH, keepWarmOf, keepWarmTitle,
  leadOf, NO_PRESSES, openPlace, ordered, parseRunning, pressBody, pressed, pressNote, PRESS_WAIT_MS, rowAttrs,
  rowReport, shownSessions, SHOWN, valueCame, WAITING, whereText,
} from "../src/running-model.ts";

const SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f";
const hhmm = (at) => new Date(Date.parse(at)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** One entry as `/api/running` sends it, with nothing joined unless `more` says. */
const entry = (request, more = {}) => ({
  request_id: request, harness: "claude", session_id: SID, name: `${request}-name`, identity: null,
  where: { target: "slot", slot: "3", project_key: "proj-a", folder: "proj-a" },
  started_at: "2026-03-01T10:00:00.000000Z", cache: null, waits: null, covered: false, coverable: null, activity: null,
  ...more,
});
const warm = (until, estimated = false) => ({ state: "warm", until, estimated });
const subagents = (count) => ({ kind: "subagents", count });
const agents = (count) => ({ kind: "agents", count });
const answer = (sessions, count = sessions.length) => parseRunning({ state: "up", detail: null, count, sessions });

const UNTIL = "2026-03-01T19:22:00Z";
const uncovered = (request, until = UNTIL, more = {}) =>
  entry(request, { cache: warm(until), waits: agents(2), covered: false, ...more });

// --- reading the answer ------------------------------------------------------------------------

test("an answer that is not the route's shape reads as nothing", () => {
  assert.equal(parseRunning(null), null);
  assert.equal(parseRunning({ state: "up" }), null);
  assert.equal(parseRunning({ state: "maybe", sessions: [] }), null);
});

test("an entry that is not one is left out, and a wait of none reads as no wait", () => {
  const value = parseRunning({ state: "up", detail: null, count: 2,
                               sessions: [entry("r1", { waits: subagents(0) }), { name: 3 }] });
  assert.equal(value.sessions.length, 1);
  assert.equal(value.sessions[0].waits, null);
  assert.equal(value.count, 2);
});

// --- the three cases ---------------------------------------------------------------------------

test("a session with no wait, no cache fact or a cold cache is ordinary", () => {
  assert.equal(caseOf(entry("r1")), "ordinary");
  assert.equal(caseOf(entry("r1", { cache: warm(UNTIL) })), "ordinary");
  assert.equal(caseOf(entry("r1", { waits: agents(2) })), "ordinary");
  assert.equal(caseOf(entry("r1", { waits: agents(2), cache: { state: "cold", until: null, estimated: false } })),
               "ordinary");
});

test("a waiting session with a warm cache is covered or uncovered as keep-warm covers it", () => {
  assert.equal(caseOf(entry("r1", { waits: agents(2), cache: warm(UNTIL), covered: true })), "covered");
  assert.equal(caseOf(uncovered("r1")), "uncovered");
});

test("an ordinary session's fact is its warm time, cold, or nothing, all dim", () => {
  assert.equal(factOf(entry("r1")), null);
  assert.deepEqual(factOf(entry("r1", { cache: warm(UNTIL) })), { text: `warm until ${hhmm(UNTIL)}`, heavy: false });
  assert.deepEqual(factOf(entry("r1", { cache: warm(UNTIL, true) })),
                   { text: `warm until ${hhmm(UNTIL)}, est.`, heavy: false });
  assert.deepEqual(factOf(entry("r1", { cache: { state: "cold", until: null, estimated: false } })),
                   { text: "cold", heavy: false });
  assert.equal(leadOf(entry("r1", { cache: warm(UNTIL) })), null);
});

test("a covered session says what keeps it warm, dim, singular for one", () => {
  const covered = (waits) => entry("r1", { waits, cache: warm(UNTIL), covered: true });
  assert.deepEqual(factOf(covered(agents(2))), { text: "kept warm while 2 agents work", heavy: false });
  assert.deepEqual(factOf(covered(agents(1))), { text: "kept warm while 1 agent works", heavy: false });
  assert.deepEqual(factOf(covered(subagents(3))), { text: "kept warm while 3 subagents run", heavy: false });
  assert.deepEqual(factOf(covered(subagents(1))), { text: "kept warm while 1 subagent runs", heavy: false });
  assert.equal(leadOf(covered(agents(2))), null);
});

test("an uncovered session says when it goes cold and what it waits on, both heavy", () => {
  assert.deepEqual(factOf(uncovered("r1")), { text: `goes cold at ${hhmm(UNTIL)}`, heavy: true });
  assert.deepEqual(factOf(uncovered("r1", UNTIL, { cache: warm(UNTIL, true) })),
                   { text: `goes cold at ${hhmm(UNTIL)}, est.`, heavy: true });
  assert.equal(leadOf(uncovered("r1")), "waiting on 2 agents");
  assert.equal(leadOf(uncovered("r1", UNTIL, { waits: agents(1) })), "waiting on 1 agent");
  assert.equal(leadOf(uncovered("r1", UNTIL, { waits: subagents(3) })), "waiting on 3 subagents");
  assert.equal(leadOf(uncovered("r1", UNTIL, { waits: subagents(1) })), "waiting on 1 subagent");
});

// --- the row's report --------------------------------------------------------------------------

const DESK = { desk: true, open: false };

test("nothing running, a read that failed or no answer yet is no row", () => {
  assert.equal(rowReport(null, DESK), null);
  assert.equal(rowReport(answer([]), DESK), null);
  assert.equal(rowReport(parseRunning({ state: "error", detail: "no", count: 0, sessions: [] }), DESK), null);
});

test("at the desk with no uncovered session the row says the count and needs nothing", () => {
  const value = answer([entry("r1"), entry("r2", { waits: agents(1), cache: warm(UNTIL), covered: true })]);
  assert.deepEqual(rowReport(value, DESK), { parts: [{ text: "2 running" }], needs: 0, opens: true });
});

test("closed at the desk, the soonest cold uncovered session leads the row, heavy, with the count at the end", () => {
  const value = answer([
    entry("r1"),
    uncovered("late", "2026-03-01T19:40:00Z", { identity: "captain" }),
    uncovered("soon", "2026-03-01T19:22:00Z", { identity: "mate" }),
  ]);
  assert.deepEqual(rowReport(value, DESK), {
    parts: [{ text: `mate goes cold at ${hhmm("2026-03-01T19:22:00Z")}`, heavy: true }],
    time: "3 running", needs: WAITING, opens: true,
  });
});

test("the uncovered session's who is its comms name, else its name", () => {
  const report = rowReport(answer([uncovered("r1")]), DESK);
  assert.equal(report.parts[0].text, `r1-name goes cold at ${hhmm(UNTIL)}`);
});

test("opened at the desk the row says the count alone and keeps its need", () => {
  const value = answer([entry("r1"), uncovered("r2")]);
  assert.deepEqual(rowReport(value, { desk: true, open: true }), {
    parts: [{ text: "2 running" }], needs: WAITING, opens: true,
  });
});

test("on the phone the row is open, the count on the label's line and the fact empty", () => {
  assert.deepEqual(rowReport(answer([entry("r1")]), { desk: false, open: false }), {
    parts: [], time: "1 running", needs: 0, opens: true, open: true,
  });
  assert.deepEqual(rowReport(answer([uncovered("r1")]), { desk: false, open: true }), {
    parts: [], time: "1 running", needs: WAITING, opens: true, open: true,
  });
});

test("the count is the answer's, not the entries it carries", () => {
  assert.equal(rowReport(answer([entry("r1")], 7), DESK).parts[0].text, "7 running");
});

// --- the list ------------------------------------------------------------------------------------

test("the uncovered come first, soonest cold first, then the latest activity first", () => {
  const sessions = [
    entry("old", { started_at: "2026-03-01T09:00:00Z" }),
    entry("said", { activity: { at: "2026-03-01T18:00:00Z", by: "agent", text: "Done." } }),
    uncovered("cold-late", "2026-03-01T19:50:00Z"),
    entry("new", { started_at: "2026-03-01T17:00:00Z" }),
    uncovered("cold-soon", "2026-03-01T19:10:00Z"),
    entry("covered", { waits: agents(1), cache: warm(UNTIL), covered: true,
                       activity: { at: "2026-03-01T18:30:00Z", by: "you", text: "Go on." } }),
  ];
  assert.deepEqual(ordered(sessions).map((e) => e.request_id),
                   ["cold-soon", "cold-late", "covered", "said", "new", "old"]);
});

test("the opened list shows four, and the link counts every one", () => {
  const value = answer(Array.from({ length: 6 }, (_, at) => entry(`r${at}`)), 6);
  assert.equal(SHOWN, 4);
  assert.equal(shownSessions(value).length, 4);
  assert.equal(allLabel(value), "All sessions (6)");
  assert.deepEqual(shownSessions(null), []);
});

test("where a session runs: the slot, terminal or headless, then the project, else the folder", () => {
  assert.equal(whereText({ target: "slot", slot: "3", project_key: "proj-a", folder: "work" }), "slot 3 · proj-a");
  assert.equal(whereText({ target: "terminal", slot: null, project_key: null, folder: "proj-b" }),
               "terminal · proj-b");
  assert.equal(whereText({ target: "headless", slot: null, project_key: null, folder: "" }), "headless");
});

test("a desk row with no activity says when it was launched", () => {
  const started = "2026-03-01T10:00:00.000000Z";
  assert.deepEqual(rowAttrs(entry("r1"), true), {
    name: "r1-name", where: "slot 3 · proj-a", fact: null, factHeavy: false, lead: null, leadHeavy: false,
    time: hhmm(started), saidBy: null, rest: "launched",
  });
});

test("a desk row with activity says its time and text, and You when the user said it", () => {
  const at = "2026-03-01T18:21:00Z";
  const said = entry("r1", { cache: warm(UNTIL), activity: { at, by: "you", text: "Run the suite." } });
  assert.deepEqual(rowAttrs(said, true), {
    name: "r1-name", where: "slot 3 · proj-a", fact: `warm until ${hhmm(UNTIL)}`, factHeavy: false, lead: null,
    leadHeavy: false, time: hhmm(at), saidBy: "you", rest: "Run the suite.",
  });
  const agent = rowAttrs(entry("r1", { activity: { at, by: "agent", text: "Done." } }), true);
  assert.equal(agent.saidBy, null);
  assert.equal(agent.rest, "Done.");
});

test("an uncovered desk row has its heavy fact and heavy lead", () => {
  const row = rowAttrs(uncovered("r1"), true);
  assert.equal(row.fact, `goes cold at ${hhmm(UNTIL)}`);
  assert.equal(row.factHeavy, true);
  assert.equal(row.lead, "waiting on 2 agents");
  assert.equal(row.leadHeavy, true);
});

test("a phone row has no time, no message and no where, and says where it runs on line two", () => {
  const at = "2026-03-01T18:21:00Z";
  const row = rowAttrs(uncovered("r1", UNTIL, { activity: { at, by: "you", text: null } }), false);
  assert.deepEqual(row, {
    name: "r1-name", where: null, fact: `goes cold at ${hhmm(UNTIL)}`, factHeavy: true,
    lead: "waiting on 2 agents", leadHeavy: true, time: null, saidBy: null, rest: "slot 3 · proj-a",
  });
  const covered = rowAttrs(entry("r1", { waits: agents(3), cache: warm(UNTIL), covered: true }), false);
  assert.equal(covered.fact, "kept warm");
});

// --- where the links go -------------------------------------------------------------------------

test("Open goes to the transcript with sessions installed, else to the launcher's page", () => {
  assert.deepEqual(openPlace(entry("r1"), true), { module: "sessions", route: `claude/${SID}` });
  assert.deepEqual(openPlace(entry("r1"), false), { module: "launcher", route: "" });
  assert.deepEqual(openPlace(entry("r1", { session_id: null }), true), { module: "launcher", route: "" });
  assert.equal(hrefOf(openPlace(entry("r1"), true)), `/m/sessions/claude/${SID}`);
});

test("All sessions goes to sessions' list, else to the launcher's page", () => {
  assert.deepEqual(allPlace(true), { module: "sessions", route: "" });
  assert.deepEqual(allPlace(false), { module: "launcher", route: "" });
});

test("times are the time of day, and nothing for no time", () => {
  assert.equal(clock(null), "");
  assert.equal(clock("not a time"), "");
  assert.equal(clock(UNTIL), hhmm(UNTIL));
});

// --- Keep warm ----------------------------------------------------------------------------------

const coverable = (request, cap = 3, more = {}) => uncovered(request, UNTIL, { coverable: { cap }, ...more });
const one = (raw) => answer([raw]).sessions[0];

test("coverable is read as a cap of nudges, and anything else as none", () => {
  assert.deepEqual(one(coverable("r1", 3)).coverable, { cap: 3 });
  assert.equal(one(entry("r1")).coverable, null);
  const { coverable: _, ...without } = entry("r1");
  assert.equal(one(without).coverable, null);
  for (const bad of [{}, { cap: "3" }, { cap: 2.5 }, { cap: 0 }, [3], 3]) {
    assert.equal(one(entry("r1", { coverable: bad })).coverable, null, JSON.stringify(bad));
  }
});

test("an uncovered session a press would cover has Keep warm, before Open", () => {
  const acts = actsOf(one(coverable("r1", 3)), true);
  assert.deepEqual(acts.map((act) => act.kind), ["keep-warm", "open"]);
  assert.equal(acts[0].title, keepWarmTitle(3));
});

test("Keep warm is not drawn on an uncovered session no press would cover", () => {
  assert.deepEqual(actsOf(one(uncovered("r1")), true), [{ kind: "open" }]);
  assert.equal(keepWarmOf(one(uncovered("r1")), true), null);
});

test("Keep warm is not drawn on a covered or an ordinary session, even with coverable set", () => {
  const cases = [
    entry("r1", { waits: agents(2), cache: warm(UNTIL), covered: true, coverable: { cap: 3 } }),
    entry("r1", { coverable: { cap: 3 } }),
    entry("r1", { cache: warm(UNTIL), coverable: { cap: 3 } }),
    entry("r1", { waits: agents(2), cache: { state: "cold", until: null, estimated: false }, coverable: { cap: 3 } }),
    entry("r1", { waits: agents(2), coverable: { cap: 3 } }),
  ];
  for (const raw of cases) {
    const parsed = one(raw);
    assert.notEqual(caseOf(parsed), "uncovered");
    assert.deepEqual(actsOf(parsed, true), [{ kind: "open" }]);
    assert.equal(keepWarmOf(parsed, true), null);
  }
});

test("the phone draws no act, Keep warm included", () => {
  assert.deepEqual(actsOf(one(coverable("r1")), false), []);
  assert.equal(keepWarmOf(one(coverable("r1")), false), null);
});

test("Keep warm's title says what a press costs, with the cap", () => {
  assert.equal(keepWarmTitle(3), "Sends a short turn before each cold time while it waits, at most 3 in a row.");
  assert.equal(keepWarmOf(one(coverable("r1", 10)), true).title,
               "Sends a short turn before each cold time while it waits, at most 10 in a row.");
});

test("a press posts the session's request id to the keep-warm route", () => {
  assert.equal(KEEP_WARM_PATH, "api/keep-warm");
  assert.deepEqual(pressBody(one(coverable("r1"))), { request_id: "r1" });
});

test("Keep warm keeps the row's other words as the uncovered case has them", () => {
  const row = rowAttrs(one(coverable("r1")), true);
  assert.deepEqual(row, rowAttrs(one(uncovered("r1")), true));
  assert.equal(row.factHeavy, true);
  assert.equal(row.lead, "waiting on 2 agents");
});

test("an answer that refuses nothing leaves no note, and a refusal leaves its problem", () => {
  assert.equal(pressNote({ status: 200, data: { refused: null } }), null);
  const refused = { code: "draft", row: null, problem: "There is text typed in the session.",
                    hint: "clear the text typed in the session" };
  assert.equal(pressNote({ status: 200, data: { refused } }), "There is text typed in the session.");
});

test("an answer that is not the route's leaves a note of its own", () => {
  assert.equal(pressNote({ status: 0, data: null }), "Not kept warm: no answer from the shell");
  assert.equal(pressNote({ status: 502, data: null }), "Not kept warm: the shell answered 502");
  assert.equal(pressNote({ status: 200, data: null }), "Not kept warm: the answer could not be read");
  assert.equal(pressNote({ status: 200, data: { refused: { code: "draft" } } }),
               "Not kept warm: the answer could not be read");
});

test("a pressed button waits through the route's answer and stops at the next value after it", () => {
  const value = answer([coverable("r1")]);
  let presses = pressed(NO_PRESSES, "r1");
  assert.deepEqual(presses.waiting, { r1: false });
  presses = valueCame(presses, value);
  assert.deepEqual(presses.waiting, { r1: false }, "a value before the answer does not end the press");
  presses = answered(presses, "r1", null);
  assert.deepEqual(presses, { waiting: { r1: true }, notes: {} });
  presses = valueCame(presses, value);
  assert.deepEqual(presses, NO_PRESSES);
});

test("a press ends at a value that shows the session covered, even before the answer", () => {
  const covered = answer([entry("r1", { waits: agents(2), cache: warm(UNTIL), covered: true })]);
  assert.deepEqual(valueCame(pressed(NO_PRESSES, "r1"), covered), NO_PRESSES);
  assert.deepEqual(valueCame(pressed(NO_PRESSES, "r1"), answer([])), NO_PRESSES, "or the session has ended");
});

test("a pressed button waits at most the press's wait", () => {
  assert.equal(PRESS_WAIT_MS, 20_000);
  const presses = expired(pressed(NO_PRESSES, "r1"), "r1");
  assert.deepEqual(presses, NO_PRESSES);
});

test("a refusal's note stays until the next value, and outlasts the press's wait", () => {
  let presses = answered(pressed(NO_PRESSES, "r1"), "r1", "There is text typed in the session.");
  assert.deepEqual(presses.notes, { r1: "There is text typed in the session." });
  presses = expired(presses, "r1");
  assert.deepEqual(presses, { waiting: {}, notes: { r1: "There is text typed in the session." } });
  presses = valueCame(presses, answer([coverable("r1")]));
  assert.deepEqual(presses.notes, {});
});

test("an answer after the press's wait still leaves its note", () => {
  const presses = answered(expired(pressed(NO_PRESSES, "r1"), "r1"), "r1", "The cold time is too near.");
  assert.deepEqual(presses, { waiting: {}, notes: { r1: "The cold time is too near." } });
});

test("pressing again clears the row's note, and presses on two rows wait apart", () => {
  let presses = answered(pressed(NO_PRESSES, "r1"), "r1", "Refused.");
  presses = expired(presses, "r1");
  presses = pressed(presses, "r1");
  assert.deepEqual(presses, { waiting: { r1: false }, notes: {} });
  presses = pressed(presses, "r2");
  presses = answered(presses, "r2", null);
  presses = valueCame(presses, answer([coverable("r1"), coverable("r2")]));
  assert.deepEqual(presses.waiting, { r1: false });
});
