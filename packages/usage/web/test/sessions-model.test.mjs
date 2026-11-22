import assert from "node:assert/strict";
import test from "node:test";

import {
  addPage, depthPages, lastSegment, listRead, rowKey, sessionName, sessionsPath, sessionWhere, shareWords,
} from "../src/sessions-model.ts";
import { START } from "../src/filter-model.ts";
import { rowName } from "../../../sessions/web/src/list.ts";

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
  assert.equal(name({ started: at("2026-11-03 08:52") }).name, "proj-a · started Nov 3 08:52");
  assert.equal(name({ started: at("2025-12-30 23:10") }).name, "proj-a · started Dec 30, 2025 23:10");
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

test("a read of /api/sessions is over its own days under the filters, or over the range", () => {
  const state = { ...START, harness: "codex", model: "m-a" };
  assert.equal(sessionsPath(state, TODAY, { since: TODAY, until: TODAY }, 2),
               "api/sessions?since=2026-11-08&until=2026-11-08&harness=codex&model=m-a&top=2");
  assert.equal(sessionsPath(state, TODAY, null, 1),
               "api/sessions?since=2026-10-10&until=2026-11-08&harness=codex&model=m-a&top=1");
  assert.equal(sessionsPath({ ...START, range: { kind: "all" } }, TODAY, { since: null, until: "2026-11-01" }, 1, 30),
               "api/sessions?until=2026-11-01&top=1&skip=30");
});

test("a page more adds rows below those read and drops one already read", () => {
  const first = listRead(answer([row("a", 5), row("b", 4)], { count: 6, more: 4, total: sums({ cost: 12 }) }));
  assert.equal(first.read, 2);
  // A pass between the reads moved `b` one place later.
  const next = addPage(first, answer([row("b", 4), row("c", 2), row("d", 0.5)],
                                     { skip: 2, count: 6, more: 1, total: sums({ cost: 12 }) }));
  assert.deepEqual(next.rows.map((item) => item.session_id), ["a", "b", "c", "d"]);
  assert.deepEqual([next.read, next.more], [5, 1]);
  assert.deepEqual(next.rows.map((item) => rowKey(item)), ["claude/a", "claude/b", "claude/c", "claude/d"]);
});

test("rows with no session id are one file each", () => {
  const one = row(null, 1, { cwd: "/work/proj-a", first_ts: 100 });
  const two = row(null, 1, { cwd: "/work/proj-a", first_ts: 200 });
  assert.notEqual(rowKey(one), rowKey(two));
  assert.equal(addPage(listRead(answer([one])), answer([two], { skip: 1 })).rows.length, 2);
});

test("a list is read again to the depth it was read to, at most 500 rows a read", () => {
  assert.deepEqual(depthPages(0, 10), [{ top: 10, skip: 0 }]);
  assert.deepEqual(depthPages(40, 10), [{ top: 40, skip: 0 }]);
  assert.deepEqual(depthPages(1, 1), [{ top: 1, skip: 0 }]);
  assert.deepEqual(depthPages(430, 10), [{ top: 430, skip: 0 }]);
  assert.deepEqual(depthPages(1100, 10), [{ top: 500, skip: 0 }, { top: 500, skip: 500 }, { top: 100, skip: 1000 }]);
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

test("a session without a typed name is named as the Sessions page names it, day words and all", () => {
  const zone = { clock: UTC.time, day: UTC.day, start: (day) => Date.parse(`${day}T00:00:00Z`) / 1000 };
  const starts = ["2026-11-08 18:45", "2026-11-08 00:05", "2026-11-07 22:25", "2026-11-03 08:52", "2026-11-01 08:52",
                  "2026-10-01 07:00", "2025-12-30 23:10"];
  for (const start of starts) {
    for (const folder of ["/work/proj-a", null]) {
      const row = { harness: "claude", folder, name: null, started: at(start) };
      assert.equal(sessionName(row, TODAY, UTC).name, rowName(row, TODAY, zone).name, `${start} ${folder}`);
    }
  }
});
