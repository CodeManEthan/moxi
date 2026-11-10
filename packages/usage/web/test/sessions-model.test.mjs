import assert from "node:assert/strict";
import test from "node:test";

import {
  addPage, depthPages, earlierLine, firstHeading, lastSegment, listRead, remainder, restOf, rowKey,
  sessionLists, sessionName, sessionsPath, sessionWhere, shareWords, showPath,
} from "../src/sessions-model.ts";
import { START } from "../src/filter-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const TODAY = "2026-11-08";
const at = (text) => Date.parse(`${text.replace(" ", "T")}:00Z`) / 1000;

const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const ref = (extra = {}) => ({ harness: "claude", session_id: "s-1", name: null, folder: "/work/proj-a",
  started: at("2026-11-08 18:45"), known: true, ...extra });
const row = (id, cost, extra = {}) => ({ ...sums({ cost }), ...ref({ session_id: id }), cwd: "/work/proj-a",
  project: null, models: ["m-a"], first_ts: at("2026-11-08 09:00"), last_ts: at("2026-11-08 10:00"),
  context: null, ...extra });
const answer = (rows, extra = {}) => ({ since: null, until: null, tz: "UTC", filters: [], rows,
  total: sums({ cost: 20 }), unpriced_models: [], rules: [], index: { passed: false, last_pass: 1, note: null },
  today: TODAY, top: rows.length, skip: 0, count: rows.length, more: 0, ...extra });

const name = (over, today = TODAY) => sessionName(ref(over), today, UTC);

test("a session is named by its typed name, with its folder's last segment beside it", () => {
  assert.deepEqual(name({ name: "fix the tests" }), { name: "fix the tests", where: "proj-a" });
  assert.deepEqual(name({ name: "  fix\nthe tests " }), { name: "fix the tests", where: "proj-a" });
  assert.deepEqual(name({ name: "fix the tests", folder: null }), { name: "fix the tests", where: null });
  assert.deepEqual(name({ name: "fix the tests", started: null }), { name: "fix the tests", where: "proj-a" });
});

test("without a name, a session is its folder and start, worded by the page's day words", () => {
  assert.deepEqual(name({}), { name: "proj-a · started today 18:45", where: null });
  assert.equal(name({ started: at("2026-11-07 22:25") }).name, "proj-a · started yesterday 22:25");
  assert.equal(name({ started: at("2026-11-03 08:52") }).name, "proj-a · started Tue 08:52");
  assert.equal(name({ started: at("2026-11-01 08:52") }).name, "proj-a · started Nov 1 08:52");
  assert.equal(name({ name: "   " }).name, "proj-a · started today 18:45");
  assert.equal(name({ folder: "/work/proj-b/" }).name, "proj-b · started today 18:45");
  assert.equal(name({}, null).name, "proj-a · started 18:45");
});

test("the Sessions page's null parts: no start, no folder, neither", () => {
  assert.deepEqual(name({ started: null }), { name: "proj-a", where: null });
  assert.deepEqual(name({ folder: null }), { name: "Claude Code session · started today 18:45", where: null });
  assert.deepEqual(name({ folder: "" }), { name: "Claude Code session · started today 18:45", where: null });
  assert.deepEqual(name({ folder: null, started: null }), { name: "Claude Code session", where: null });
  assert.equal(name({ harness: "codex", folder: null, started: null }).name, "Codex session");
  assert.equal(name({ harness: "opencode", folder: null, started: null }).name, "OpenCode session");
});

test("a session that is gone, or was not looked for, is named from what the answer carries", () => {
  // Gone: the folder and first request the index holds.
  assert.equal(name({ known: false, folder: "/work/proj-a", started: at("2026-10-01 07:00") }).name,
               "proj-a · started Oct 1 07:00");
  // Not looked for: no start.
  assert.equal(name({ known: null, started: null }).name, "proj-a");
});

test("a name never comes from the session's id, its cwd or its first request", () => {
  const named = sessionName({ ...row("0190b000-0000-7000-8000-000000000001", 1), folder: null, started: null },
                            TODAY, UTC);
  assert.deepEqual(named, { name: "Claude Code session", where: null });
});

test("a row's where is its project with projects installed, else the folder beside a typed name", () => {
  assert.equal(sessionWhere(row("s1", 1, { project: "atlas-api" }), true), "atlas-api");
  assert.equal(sessionWhere(row("s1", 1, { project: "atlas-api", name: "fix" }), true), "atlas-api");
  assert.equal(sessionWhere(row("s1", 1, { project: null, name: "fix" }), true), "proj-a");
  assert.equal(sessionWhere(row("s1", 1, { project: null }), true), null);
  assert.equal(sessionWhere(row("s1", 1, { project: "atlas-api", name: "fix" }), false), "proj-a");
  assert.equal(sessionWhere(row("s1", 1, { project: "atlas-api" }), false), null);
  assert.equal(lastSegment("/"), "/");
});

test("a range of one day: today's list alone, with no earlier list", () => {
  const lists = sessionLists({ kind: "today" }, TODAY);
  assert.deepEqual(lists, { first: { since: TODAY, until: TODAY }, earlier: null, week: false, today: TODAY });
  assert.equal(firstHeading(lists), "Today, dearest first");
  const list = listRead(answer([row("a", 5), row("b", 3)], { count: 5, more: 3, total: sums({ cost: 9.2 }) }));
  assert.deepEqual(remainder(list, lists.week), { text: "3 more, $1.20 between them", act: "Show" });
  assert.equal(earlierLine(lists, answer([row("x", 1)])), null);
  // Another single day is its date.
  const past = sessionLists({ kind: "custom", since: "2026-11-03", until: "2026-11-03" }, TODAY);
  assert.equal(firstHeading(past), "Nov 3, dearest first");
});

test("a range of seven days is this week, with no earlier list", () => {
  const lists = sessionLists({ kind: "7d" }, TODAY);
  assert.deepEqual(lists, { first: { since: "2026-11-02", until: TODAY }, earlier: null, week: true, today: TODAY });
  assert.equal(firstHeading(lists), "This week, dearest first");
  const list = listRead(answer([row("a", 5)], { count: 16, more: 15, total: sums({ cost: 15.09 }) }));
  assert.equal(remainder(list, lists.week).text, "15 more this week, $10.09 between them");
});

test("a range of thirty days is this week, then the earlier days from one read", () => {
  const lists = sessionLists({ kind: "30d" }, TODAY);
  assert.deepEqual(lists.first, { since: "2026-11-02", until: TODAY });
  assert.deepEqual(lists.earlier, { since: "2026-10-10", until: "2026-11-01" });
  assert.equal(firstHeading(lists), "This week, dearest first");
  const earlier = answer([row("e", 4)], { count: 29, more: 28, total: sums({ cost: 20.47 }) });
  assert.deepEqual(earlierLine(lists, earlier),
                   { text: "Earlier · 29 sessions, Oct 10 to Nov 1, $20.47", act: "Show" });
  assert.equal(earlierLine(lists, answer([], { count: 0, total: sums({ cost: 0 }) })), null);
  assert.equal(earlierLine(lists, null), null);
});

test("a range that ended last month words its own days and never says this week", () => {
  const lists = sessionLists({ kind: "custom", since: "2026-10-01", until: "2026-10-31" }, TODAY);
  assert.deepEqual(lists.first, { since: "2026-10-25", until: "2026-10-31" });
  assert.deepEqual(lists.earlier, { since: "2026-10-01", until: "2026-10-24" });
  assert.equal(lists.week, false);
  assert.equal(firstHeading(lists), "Oct 25 to Oct 31, dearest first");
  const list = listRead(answer([row("a", 2)], { count: 5, more: 4, total: sums({ cost: 6 }) }));
  assert.equal(remainder(list, lists.week).text, "4 more, $4.00 between them");
  assert.equal(earlierLine(lists, answer([row("e", 1)], { count: 2, total: sums({ cost: 1.5 }) })).text,
               "Earlier · 2 sessions, Oct 1 to Oct 24, $1.50");
});

test("other ranges: a short one ending today, all time, one in another year", () => {
  const short = sessionLists({ kind: "custom", since: "2026-11-06", until: TODAY }, TODAY);
  assert.equal(short.week, false);
  assert.equal(short.earlier, null);
  assert.equal(firstHeading(short), "Nov 6 to Nov 8, dearest first");
  const all = sessionLists({ kind: "all" }, TODAY);
  assert.equal(all.week, true);
  assert.deepEqual(all.earlier, { since: null, until: "2026-11-01" });
  assert.equal(earlierLine(all, answer([row("e", 1)], { count: 40, total: sums({ cost: 80 }) })).text,
               "Earlier · 40 sessions, up to Nov 1, $80.00");
  const old = sessionLists({ kind: "custom", since: "2025-12-20", until: "2026-01-02" }, TODAY);
  assert.equal(firstHeading(old), "Dec 27 2025 to Jan 2, dearest first");
  assert.equal(sessionLists({ kind: "30d" }, ""), null);
  assert.equal(sessionLists({ kind: "custom", since: "2026-11-05", until: "2026-11-01" }, TODAY), null);
});

test("each list is read over its own days under the filters, ten first and thirty a Show", () => {
  const lists = sessionLists({ kind: "30d" }, TODAY);
  const state = { ...START, harness: "codex", model: "m-a" };
  assert.equal(sessionsPath(state, TODAY, lists.first, 10),
               "api/sessions?since=2026-11-02&until=2026-11-08&harness=codex&model=m-a&top=10");
  assert.equal(sessionsPath(state, TODAY, lists.earlier, 1),
               "api/sessions?since=2026-10-10&until=2026-11-01&harness=codex&model=m-a&top=1");
  assert.equal(sessionsPath(state, TODAY, null, 1),
               "api/sessions?since=2026-10-10&until=2026-11-08&harness=codex&model=m-a&top=1");
  const all = sessionLists({ kind: "all" }, TODAY);
  assert.equal(sessionsPath({ ...START, range: { kind: "all" } }, TODAY, all.earlier, 1),
               "api/sessions?until=2026-11-01&top=1");
  const list = listRead(answer([row("a", 5)], { count: 16, more: 15 }));
  assert.equal(showPath(START, TODAY, lists.first, list),
               "api/sessions?since=2026-11-02&until=2026-11-08&top=30&skip=1");
});

test("a Show adds rows below what is shown, drops one already shown, and the remainder follows", () => {
  const first = listRead(answer([row("a", 5), row("b", 4)], { count: 6, more: 4, total: sums({ cost: 12 }) }));
  assert.equal(first.read, 2);
  // A pass between the reads moved `b` one place later.
  const next = addPage(first, answer([row("b", 4), row("c", 2), row("d", 0.5)],
                                     { skip: 2, count: 6, more: 1, total: sums({ cost: 12 }) }));
  assert.deepEqual(next.rows.map((item) => item.session_id), ["a", "b", "c", "d"]);
  assert.equal(next.read, 5);
  assert.deepEqual(remainder(next, true), { text: "1 more this week, $0.50", act: "Show" });
  const end = addPage(next, answer([row("e", 0.5)], { skip: 5, count: 6, more: 0, total: sums({ cost: 12 }) }));
  assert.equal(remainder(end, true), null);
});

test("what the rows leave out is not whole when the total holds what they do not, or has no price", () => {
  const unpriced = listRead(answer([row("a", 5)], { count: 3, more: 2,
    total: sums({ cost: 8, unpriced_tokens: 1200, complete: false }) }));
  assert.deepEqual(restOf(unpriced), { cost: 3, complete: false });
  assert.equal(remainder(unpriced, false).text, "2 more, at least $3.00 between them");
  // The rows shown hold all that is not whole: the rest is whole.
  const held = listRead(answer([row("a", 5, { unknown_rows: 2, complete: false })], { count: 3, more: 2,
    total: sums({ cost: 8, unknown_rows: 2, complete: false }) }));
  assert.deepEqual(restOf(held), { cost: 3, complete: true });
  const none = listRead(answer([row("a", null)], { count: 2, more: 1, total: sums({ cost: null, complete: false }) }));
  assert.equal(remainder(none, false).text, "1 more, no price");
  const rounding = listRead(answer([row("a", 0.1), row("b", 0.2)], { count: 2, more: 1, total: sums({ cost: 0.3 }) }));
  assert.equal(restOf(rounding).cost, 0);
});

test("a session on both sides of the cut is in both lists, each with that side's cost", () => {
  const lists = sessionLists({ kind: "30d" }, TODAY);
  const week = listRead(answer([row("a", 3.1), row("b", 1)], { count: 2 }));
  const earlier = listRead(answer([row("a", 0.9)], { count: 1 }));
  assert.ok(lists.earlier !== null);
  assert.deepEqual(week.rows.map((item) => [rowKey(item), item.cost]), [["claude/a", 3.1], ["claude/b", 1]]);
  assert.deepEqual(earlier.rows.map((item) => [rowKey(item), item.cost]), [["claude/a", 0.9]]);
});

test("rows with no session id are one file each", () => {
  const one = row(null, 1, { cwd: "/work/proj-a", first_ts: 100 });
  const two = row(null, 1, { cwd: "/work/proj-a", first_ts: 200 });
  assert.notEqual(rowKey(one), rowKey(two));
  assert.equal(addPage(listRead(answer([one])), answer([two], { skip: 1 })).rows.length, 2);
});

test("a list is read again to the depth it was read to, at most 200 rows a read", () => {
  assert.deepEqual(depthPages(0), [{ top: 10, skip: 0 }]);
  assert.deepEqual(depthPages(40), [{ top: 40, skip: 0 }]);
  assert.deepEqual(depthPages(1, 1), [{ top: 1, skip: 0 }]);
  assert.deepEqual(depthPages(430), [{ top: 200, skip: 0 }, { top: 200, skip: 200 }, { top: 30, skip: 400 }]);
});

test("a share is a whole percent rounded down, never 0% above zero, null without a whole", () => {
  assert.equal(shareWords(21.49, 63.72), "33%");
  assert.equal(shareWords(0.25, 63.72), "under 1%");
  assert.equal(shareWords(63.71, 63.72), "99%");
  assert.equal(shareWords(63.72, 63.72), "100%");
  assert.equal(shareWords(0, 63.72), "0%");
  assert.equal(shareWords(null, 63.72), null);
  assert.equal(shareWords(1, null), null);
  assert.equal(shareWords(1, 0), null);
});
