import assert from "node:assert/strict";
import test from "node:test";

import {
  BOARD_PAGE, BOARD_START, contextWords, nextOrder, SessionList, sessionLine, sessionsBoard, sessionsClosed, spanWords,
} from "../src/sessions-section.ts";
import { boardPath, earlierLine, listRead, sessionLists, sessionsPath } from "../src/sessions-model.ts";
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
const row = (id, cost, extra = {}) => ({ ...sums({ cost, requests: 15, tokens: 1200 }), harness: "claude",
  session_id: id, name: null, folder: "/work/proj-a", started: at("2026-11-03 08:52"), known: true,
  cwd: "/work/proj-a", project: null, models: ["m-a"], first_ts: at("2026-11-03 08:52"),
  last_ts: at("2026-11-03 10:20"), turns: null, subagent_share: null, context: null, ...extra });
const answer = (rows, extra = {}) => ({ since: null, until: null, tz: "UTC", filters: [], rows,
  total: sums({ cost: 20 }), unpriced_models: [], rules: [], index: { passed: false, last_pass: 1, note: null },
  today: TODAY, top: rows.length, skip: 0, count: rows.length, more: 0, ...extra });

test("the closed row names the dearest session with its cost and counts the range's sessions", () => {
  assert.deepEqual(sessionsClosed(answer([row("s-1", 5.35)], { count: 54, more: 53 }), TODAY, UTC),
                   { closed: "most to proj-a · started Nov 3 08:52, $5.35", count: "54 sessions" });
  assert.deepEqual(sessionsClosed(answer([row("s-1", 5.35, { name: "fix the tests" })], { count: 1 }), TODAY, UTC),
                   { closed: "most to fix the tests, $5.35", count: "1 session" });
  // A dearest with tokens with no price is at least its figure; with no price at all the
  // closed row says no figure and keeps its count.
  assert.equal(sessionsClosed(answer([row("s-1", 2, { unpriced_tokens: 5, complete: false })], { count: 3 }), TODAY, UTC)
    .closed, "most to proj-a · started Nov 3 08:52, at least $2.00");
  assert.deepEqual(sessionsClosed(answer([row("s-1", null)], { count: 2 }), TODAY, UTC),
                   { closed: null, count: "2 sessions" });
});

test("the closed row is absent until read and with no session in the range", () => {
  assert.equal(sessionsClosed(null, TODAY, UTC), null);
  assert.equal(sessionsClosed(answer([], { count: 0 }), TODAY, UTC), null);
});

test("a row's second line says its requests, first to last, models and how full its context was", () => {
  const line = sessionLine(row("s-1", 5.35, { models: ["m-a", "m-b"], context: { tokens: 40_000, window: 200_000 } }),
                           TODAY, false, UTC);
  assert.deepEqual(line, {
    key: "claude/s-1", name: "proj-a · started Nov 3 08:52", where: null, cost: "$5.35", why: null,
    requests: "15 requests", span: "Tue 08:52 to 10:20", models: "m-a, m-b", context: "context 20% full",
    session: { harness: "claude", id: "s-1" },
  });
  assert.equal(sessionLine(row("s-1", 1, { requests: 1 }), TODAY, false, UTC).requests, "1 request");
});

test("a row's context is absent with no context or no window, and rounds down", () => {
  assert.equal(contextWords(null), null);
  assert.equal(contextWords({ tokens: 5000, window: null }), null);
  assert.equal(contextWords({ tokens: 5000, window: 0 }), null);
  assert.equal(contextWords({ tokens: 199_999, window: 200_000 }), "context 99% full");
  assert.equal(contextWords({ tokens: 100, window: 200_000 }), "context under 1% full");
  assert.equal(sessionLine(row("s-1", 1, { context: { tokens: 5, window: null } }), TODAY, false, UTC).context, null);
});

test("first to last names each day it needs, today's as today", () => {
  assert.equal(spanWords(at("2026-11-08 11:00"), at("2026-11-08 18:22"), TODAY, UTC), "today 11:00 to 18:22");
  assert.equal(spanWords(at("2026-11-07 20:11"), at("2026-11-08 01:10"), TODAY, UTC), "yesterday 20:11 to today 01:10");
  assert.equal(spanWords(at("2026-10-01 09:00"), at("2026-10-01 09:00"), TODAY, UTC), "Oct 1 09:00");
  assert.equal(spanWords(null, at("2026-11-08 18:22"), TODAY, UTC), "today 18:22");
  assert.equal(spanWords(at("2026-11-08 11:00"), null, TODAY, UTC), "today 11:00");
  assert.equal(spanWords(null, null, TODAY, UTC), null);
});

test("a row's where is its project with projects, else the folder beside a typed name", () => {
  const typed = row("s-1", 1, { name: "fix the tests", project: "proj-b" });
  assert.equal(sessionLine(typed, TODAY, true, UTC).where, "proj-b");
  assert.equal(sessionLine(typed, TODAY, false, UTC).where, "proj-a");
  assert.equal(sessionLine(row("s-1", 1, { project: "proj-b" }), TODAY, false, UTC).where, null);
});

test("a row with no session id does not open and is never named by an id", () => {
  const line = sessionLine(row(null, 1, { name: null }), TODAY, false, UTC);
  assert.equal(line.session, null);
  assert.equal(line.name, "proj-a · started Nov 3 08:52");
  assert.match(line.key, /^claude\/\//);
});

test("a row with no model and a cost not whole leaves the models out and says why", () => {
  const line = sessionLine(row("s-1", 3, { models: [], unpriced_tokens: 1000, complete: false }), TODAY, false, UTC);
  assert.equal(line.models, null);
  assert.equal(line.cost, "at least $3.00");
  assert.match(line.why, /tokens have no price/);
});

test("the table: every figure of each session, a sort on each column the server sorts by", () => {
  const list = listRead(answer([row("s-1", 5.35, { context: { tokens: 10, window: 100 }, fresh_input: 300, cache_write: 400,
                                                cache_read: 200, output: 300, turns: 4, subagent_share: 0.25 })],
                               { count: 54, more: 53 }));
  const board = sessionsBoard(list, BOARD_START, TODAY, false, null, UTC);
  assert.deepEqual(board.columns.map((column) => [column.label, column.sort]), [
    ["Session", null], ["Folder", "project"], ["Harness", null], ["Models", null], ["Started", "started"],
    ["Active", "active"], ["Turns", "turns"], ["Requests", "requests"], ["Fresh input", "fresh_input"],
    ["Cache write", "cache_write"], ["Cache read", "cache_read"], ["Output", "output"], ["Tokens", "tokens"],
    ["Subagent share", "subagent_share"], ["Cost", "cost"], ["Context", null]]);
  assert.deepEqual(board.rows[0].cells, ["proj-a · started Nov 3 08:52", "proj-a", "claude", "m-a", "Tue 08:52",
                                         "1h 28m", "4", "15", "300", "400", "200", "300", "1.20k", "25%", "$5.35",
                                         "10 of 100"]);
  // Turns and Subagent share say what they count on hover.
  assert.deepEqual(board.columns.filter((column) => column.title !== null).map((column) => column.label),
                   ["Turns", "Subagent share"]);
  assert.equal(board.rows[0].key, "claude/s-1");
  // The column sorted by says its direction; the others say none; one with no sort says nothing.
  const cost = board.columns.find((column) => column.label === "Cost");
  assert.deepEqual([cost.aria, cost.arrow], ["descending", "↓"]);
  assert.deepEqual([board.columns[4].aria, board.columns[4].arrow], ["none", null]);
  assert.equal(board.columns[0].aria, null);
  assert.equal(board.line, "54 sessions · showing 1");
  assert.equal(board.more, true);
});

test("with projects the project column takes the project sort; a cost not whole says why", () => {
  const list = listRead(answer([row("s-1", 2, { project: "proj-b" }), row("s-2", 1, { unpriced_tokens: 9, complete: false })]));
  const board = sessionsBoard(list, { sort: "started", order: "asc" }, TODAY, true, null, UTC);
  assert.deepEqual(board.columns.slice(0, 3).map((column) => [column.label, column.sort]),
                   [["Session", null], ["Project", "project"], ["Folder", null]]);
  assert.deepEqual([board.columns[5].label, board.columns[5].aria, board.columns[5].arrow], ["Started", "ascending", "↑"]);
  assert.equal(board.columns.at(-1).label, "Not whole");
  assert.deepEqual(board.rows.map((line) => [line.cells[1], line.cells.at(-1)]),
                   [["proj-b", ""], ["", "9 tokens have no price"]]);
  assert.deepEqual([board.line, board.more], ["2 sessions · showing 2", false]);
});

test("a session with no turn ids or no known share leaves those cells empty; a share under 1% says so", () => {
  const list = listRead(answer([row("s-1", 2), row("s-2", 1, { turns: 0, subagent_share: 0 }),
                                row("s-3", 1, { turns: 12, subagent_share: 0.004 })]));
  const board = sessionsBoard(list, BOARD_START, TODAY, false, null, UTC);
  const at = (label) => board.columns.findIndex((column) => column.label === label);
  assert.deepEqual(board.rows.map((line) => [line.cells[at("Turns")], line.cells[at("Subagent share")]]),
                   [["", ""], ["0", "0%"], ["12", "under 1%"]]);
});

test("a click on the sorted column turns it around; another column starts highest first", () => {
  assert.deepEqual(nextOrder(BOARD_START, "cost"), { sort: "cost", order: "asc" });
  assert.deepEqual(nextOrder({ sort: "cost", order: "asc" }, "cost"), { sort: "cost", order: "desc" });
  assert.deepEqual(nextOrder({ sort: "cost", order: "asc" }, "started"), { sort: "started", order: "desc" });
});

test("the table writes a folder from the home folder, and as its last segment with no home read", () => {
  const list = listRead(answer([row("s-1", 2, { folder: "/home/alice/work/proj-a" }), row("s-2", 1, { folder: "/srv/proj-b" })]));
  const folders = (home) => sessionsBoard(list, BOARD_START, TODAY, false, home, UTC).rows.map((line) => line.cells[1]);
  assert.deepEqual(folders("/home/alice"), ["~/work/proj-a", "/srv/proj-b"]);
  assert.deepEqual(folders("/home/alice/"), ["~/work/proj-a", "/srv/proj-b"]);
  assert.deepEqual(folders(null), ["proj-a", "proj-b"]);
});

test("the table is read over the whole range in its order, a page at a time", async () => {
  const state = { ...START, harness: "claude" };
  assert.equal(boardPath(state, TODAY, { sort: "started", order: "asc" }, BOARD_PAGE),
               "api/sessions?since=2026-10-10&until=2026-11-08&harness=claude&sort=started&order=asc&top=200");
  assert.equal(boardPath({ ...START, range: { kind: "all" } }, TODAY, BOARD_START, BOARD_PAGE, 200),
               "api/sessions?sort=cost&order=desc&top=200&skip=200");
  const { ask, asked } = server(many(450));
  const board = new SessionList(ask, (top, skip) => boardPath(START, TODAY, BOARD_START, top, skip), BOARD_PAGE,
                                () => {}, BOARD_PAGE);
  await board.refresh();
  await board.show();
  assert.equal(board.feed.value.rows.length, 400);
  assert.equal(board.feed.value.more, 50);
  assert.deepEqual(asked.map((item) => item.replace(/^.*&sort/, "sort")),
                   ["sort=cost&order=desc&top=200", "sort=cost&order=desc&top=200&skip=200"]);
});

test("a range of seven days or fewer has no earlier line", () => {
  for (const kind of ["today", "7d"]) {
    const lists = sessionLists({ kind }, TODAY);
    assert.equal(lists.earlier, null);
    assert.equal(earlierLine(lists, answer([row("s-1", 1)], { count: 4 })), null);
  }
  const lists = sessionLists({ kind: "custom", since: "2026-11-02", until: "2026-11-08" }, TODAY);
  assert.equal(lists.earlier, null);
});

// A page server for the list: answers `/api/sessions` paths from rows dearest first.
function server(rows) {
  const asked = [];
  const ask = async (path) => {
    asked.push(path);
    const query = new URLSearchParams(path.split("?")[1]);
    const top = Number(query.get("top"));
    const skip = Number(query.get("skip") ?? 0);
    const page = rows.slice(skip, skip + top);
    return { status: 200, data: answer(page, { top, skip, count: rows.length, more: Math.max(0, rows.length - skip - page.length) }) };
  };
  return { ask, asked };
}

const days = { since: "2026-11-02", until: "2026-11-08" };
const path = (top, skip) => sessionsPath(START, TODAY, days, top, skip);
const many = (n) => Array.from({ length: n }, (_, i) => row(`s-${i}`, n - i));

test("a list reads ten, and each Show thirty more below them", async () => {
  const { ask, asked } = server(many(45));
  const list = new SessionList(ask, path, 10, () => {});
  await list.refresh();
  assert.deepEqual(list.feed.value.rows.map((item) => item.session_id), many(10).map((item) => item.session_id));
  assert.equal(list.feed.value.more, 35);
  await list.show();
  assert.equal(list.feed.value.rows.length, 40);
  assert.equal(list.feed.value.rows[10].session_id, "s-10");
  assert.equal(list.feed.value.more, 5);
  assert.deepEqual(asked, ["api/sessions?since=2026-11-02&until=2026-11-08&top=10",
                           "api/sessions?since=2026-11-02&until=2026-11-08&top=30&skip=10"]);
});

test("a list read again after a pass keeps its depth, and a failed Show keeps its rows", async () => {
  const rows = many(45);
  const { ask, asked } = server(rows);
  const list = new SessionList(ask, path, 10, () => {});
  await list.refresh();
  await list.show();
  asked.length = 0;
  await list.refresh();
  assert.deepEqual(asked, ["api/sessions?since=2026-11-02&until=2026-11-08&top=40"]);
  assert.equal(list.feed.value.rows.length, 40);
  const failing = new SessionList(async (asked) => (asked.includes("skip") ? { status: 500, data: null } : ask(asked)),
                                  path, 10, () => {});
  await failing.refresh();
  await failing.show();
  assert.equal(failing.feed.value.rows.length, 10);
  assert.notEqual(failing.feed.failure, null);
});
