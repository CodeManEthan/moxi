import assert from "node:assert/strict";
import test from "node:test";

import {
  contextLine, kWords, newestModel, openedKeys, openedPaths, openedSession, placedRow, requestsLine, requestsTable,
  restartLine, restartPath, sessionChart, sessionLine, subagentsLine, wentLine,
} from "../src/opened-session.ts";
import { START } from "../src/filter-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const TODAY = "2026-11-08";
const at = (text) => Date.parse(`${text.replace(" ", "T")}:00Z`) / 1000;
const words = (parts) => parts.map((part) => part.text).join("");

const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], ...extra });
const report = (rows, total = {}, extra = {}) => ({ since: null, until: null, tz: "UTC", filters: [], rows,
  total: sums(total), unpriced_models: [], rules: [], index: { passed: true, last_pass: 1, note: null },
  today: TODAY, ...extra });
const request = (n, ts, extra = {}) => ({ n, ts: ts === null ? null : at(ts), model: "m-opus", tier: null, turn: 1,
  turn_start: n === 1, context: 1000 * n, window: 200_000, fresh_input: 10, cache_read: 100, cache_write_5m: 20,
  cache_write_1h: 0, output: 5, unknown: [], synthetic: false, cost: 0.01 * n,
  split: [{ class: "input", cost: 0.001 }, { class: "output", cost: 0.002 }], ...extra });
const requestsTotal = (extra = {}) => ({ marks: [], turns: 1, claimed_elsewhere: 0, copied_marks: 0,
  marks_known: true, ...extra });
const agent = (name, cost, extra = {}) => ({ ...sums({ cost, requests: 3 }), unpriced_models: [], rules: [],
  agent: name, kind: name === "main" ? "main" : "subagent", title: null, model: "m-opus",
  first_ts: at("2026-11-08 12:45"), last_ts: at("2026-11-08 19:09"), ...extra });
const REF = { harness: "claude", session_id: "s-1", name: "fix the tests", folder: "/work/proj-a",
  started: at("2026-11-08 12:45"), known: true };

test("an opened session reads its list's days, the filters and its row's harness, or over no range when addressed", () => {
  const state = { ...START, harness: "codex", model: "m-a" };
  const days = { since: "2026-11-02", until: "2026-11-08" };
  assert.deepEqual(openedKeys(state, TODAY, days, "claude"),
                   [["since", "2026-11-02"], ["until", "2026-11-08"], ["model", "m-a"], ["harness", "claude"]]);
  assert.deepEqual(openedKeys(state, TODAY, { since: null, until: "2026-11-01" }, "claude"),
                   [["until", "2026-11-01"], ["model", "m-a"], ["harness", "claude"]]);
  assert.deepEqual(openedKeys(state, TODAY, null, "claude"), [["harness", "claude"]]);
  assert.deepEqual(openedPaths("s 1", [["harness", "claude"]]), {
    requests: "api/requests?session=s+1&agent=main&harness=claude",
    terms: "api/terms?by=agent&session=s+1&harness=claude",
    session: "api/session?session=s+1&harness=claude",
  });
  assert.equal(restartPath("m-opus[1m]"), "api/restart-point?model=m-opus%5B1m%5D");
});

test("the requests line counts them, gives their first and last times and the dearest with its turn", () => {
  const rows = [request(1, "2026-11-08 12:45"), request(2, "2026-11-08 18:34", { cost: 0.28, turn: 9, turn_start: true }),
                request(3, "2026-11-08 19:09")];
  assert.equal(words(requestsLine(report(rows, { requests: 3 }, { total: { ...sums({ requests: 3 }), ...requestsTotal({ turns: 9 }) } }), TODAY, UTC)),
               "3 requests from 12:45 to 19:09 · the dearest $0.28 at 18:34, the start of turn 9");
  // With one turn the turn is not named; on another day the days are said.
  assert.equal(words(requestsLine(report([request(1, "2026-11-03 08:52"), request(2, "2026-11-03 10:20")],
                                         { requests: 2 }, { total: { ...sums({ requests: 2 }), ...requestsTotal() } }), TODAY, UTC)),
               "2 requests from Tue 08:52 to 10:20 · the dearest $0.02 at Tue 10:20");
  assert.equal(words(requestsLine(report([request(1, "2026-11-07 20:11"), request(2, "2026-11-08 01:10")],
                                         { requests: 2 }, { total: { ...sums({ requests: 2 }), ...requestsTotal() } }), TODAY, UTC)),
               "2 requests from yesterday 20:11 to today 01:10 · the dearest $0.02 at today 01:10");
  assert.equal(words(requestsLine(report([request(1, "2026-11-08 12:45")], { requests: 1 },
                                         { total: { ...sums(), ...requestsTotal() } }), TODAY, UTC)), "1 request at 12:45");
  assert.equal(requestsLine(report([], {}, { total: { ...sums(), ...requestsTotal() } }), TODAY, UTC), null);
  // An opened session's requests are its main agent's, and the line says so.
  assert.equal(words(requestsLine(report(rows, { requests: 315 }, { total: { ...sums({ requests: 315 }), ...requestsTotal({ turns: 9 }) } }),
                                  TODAY, UTC, "the main agent")),
               "315 requests by the main agent from 12:45 to 19:09 · the dearest $0.28 at 18:34, the start of turn 9");
});

test("the context line gives the last request's whole prompt against its window, rounded down", () => {
  assert.equal(words(contextLine({ tokens: 268_400, window: 1_000_000 })),
               "268k of 1,000k tokens on the last request, 26% full");
  assert.equal(words(contextLine({ tokens: 40_000, window: null })), "40k tokens on the last request");
  assert.equal(contextLine(null), null);
  assert.equal(kWords(950), "950");
});

test("went to sums the classes dearest first, with a tier's part and the harness's own figure", () => {
  const terms = report([{ ...sums(), unpriced_models: [], rules: [], model: null, period: null, agent: "main", kind: "main",
                          groups: [], split: [] }],
    { cache_write: 10, output: 5, cache_read: 50, fresh_input: 3, harness_cost: 3.5 },
    { total: { ...sums({ cache_write: 10, output: 5, cache_read: 50, fresh_input: 3, harness_cost: 3.5 }), saving: null,
               split: [{ class: "cache_write_5m", tier: null, tokens: 10, cost: 1.81 },
                       { class: "output", tier: null, tokens: 5, cost: 0.85 },
                       { class: "cache_read", tier: null, tokens: 50, cost: 0.69 },
                       { class: "cache_read", tier: "batch", tokens: 5, cost: 0.10 },
                       { class: "input", tier: null, tokens: 3, cost: 0.22 }] } });
  const parts = wentLine(terms);
  assert.equal(words(parts), "cache writes $1.81 · output $0.85 · cache reads $0.79 · $0.10 at the batch price"
    + " · fresh input $0.22 · harness $3.50");
  assert.deepEqual(parts.filter((part) => !part.dim).map((part) => part.text),
                   ["cache writes $1.81", "output $0.85", "cache reads $0.79", "fresh input $0.22"]);
  assert.equal(wentLine(report([], {}, { total: { ...sums(), split: [], saving: null } })), null);
});

test("the subagents line counts the agents but main and gives their share of the session's cost", () => {
  const session = report([agent("main", 2.93), agent("a-1", 0.5), agent("a-1", 0.04, { model: "m-b" }), agent("a-2", 0.2)],
                         { cost: 3.67 });
  assert.equal(words(subagentsLine(session)), "2 subagents · $0.74 of the $3.67");
  assert.equal(subagentsLine(report([agent("main", 1)], { cost: 1 })), null);
});

test("the session line gives harness, newest model and start, each absent when unknown", () => {
  assert.equal(words(sessionLine("claude", "m-opus", at("2026-11-08 12:45"), TODAY, UTC)), "claude · m-opus · started today 12:45");
  assert.equal(words(sessionLine("codex", null, null, TODAY, UTC)), "codex");
  assert.equal(newestModel([request(1, null), request(2, null, { model: "m-b" }), request(3, null, { model: null })]), "m-b");
});

test("the restart point is a dim line, past it in the text colour at or over it, absent with no measure", () => {
  const point = { model: "m-opus", tokens: 101_000, reason: null };
  assert.deepEqual(restartLine(point, "m-opus", 50_000), [{ text: "101k tokens for m-opus", dim: true }]);
  assert.deepEqual(restartLine(point, "m-opus", 101_000),
                   [{ text: "101k tokens for m-opus", dim: true }, { text: " · this session is past it", dim: false }]);
  assert.equal(restartLine({ model: "m-opus", tokens: null, reason: "no measure" }, "m-opus", 1), null);
});

test("a read still out shows one Reading in place of the first line it fills, and lines come in order", () => {
  const out = { value: null, loading: true };
  const outModel = openedSession({ harness: "claude", id: "s-1", requests: out, terms: out, session: out, restart: null,
                                   today: TODAY }, UTC);
  assert.deepEqual(outModel.items, [{ kind: "reading", key: "requests" }]);
  const rows = [request(1, "2026-11-08 12:45"), request(2, "2026-11-08 13:00", { context: 150_000 })];
  const requests = { value: report(rows, {}, { total: { ...sums({ requests: 2 }), ...requestsTotal() } }), loading: false };
  const session = { value: report([agent("main", 1), agent("a-1", 0.5)], { cost: 1.5 },
                                  { ref: REF, context: { tokens: 150_000, window: 200_000 } }), loading: false };
  const model = openedSession({ harness: "claude", id: "s-1", requests, terms: out, session,
                                restart: { value: null, loading: true }, today: TODAY }, UTC);
  assert.deepEqual(model.items.map((item) => item.kind === "line" ? item.key : `${item.kind}:${item.key ?? ""}`),
                   ["requests", "context", "chart:", "reading:terms", "subagents", "session", "reading:restart"]);
  assert.deepEqual(model.sessions, { harness: "claude", id: "s-1" });
  const done = openedSession({ harness: "claude", id: "s-1", requests, terms: out, session,
                               restart: { value: { model: "m-opus", tokens: 100_000, reason: null }, loading: false },
                               today: TODAY }, UTC);
  assert.equal(words(done.items.find((item) => item.key === "restart").parts),
               "100k tokens for m-opus · this session is past it");
  // The session's requests line is its main agent's, and says so.
  assert.equal(words(done.items.find((item) => item.key === "requests").parts),
               "2 requests by the main agent from 12:45 to 13:00 · the dearest $0.02 at 13:00");
  // A session whose record is gone has no Open in Sessions.
  const gone = { value: { ...session.value, ref: { ...REF, known: false } }, loading: false };
  assert.equal(openedSession({ harness: "claude", id: "s-1", requests, terms: out, session: gone, restart: null,
                               today: TODAY }, UTC).sessions, null);
});

test("the notes under the chart say what the requests cannot show, each absent at zero", () => {
  const rows = [request(1, "2026-11-08 12:45", { window: null })];
  const requests = { value: report(rows, {}, { total: { ...sums(), ...requestsTotal({ claimed_elsewhere: 2, turns: null }) } }),
                     loading: false };
  const out = { value: null, loading: true };
  assert.deepEqual(openedSession({ harness: "claude", id: "s-1", requests, terms: out, session: out, restart: null,
                                  today: TODAY }, UTC).notes,
                   ["2 requests of this agent are counted under another file", "this harness wrote no turns here",
                    "window unknown for m-opus"]);
});

test("the table has a row a request with tokens and cost by class, and a compaction as a row", () => {
  const rows = [request(1, "2026-11-08 12:45"), request(2, "2026-11-08 13:00", { unknown: ["output"], cost: null })];
  const table = requestsTable(report(rows, {}, { total: { ...sums(), ...requestsTotal({
    marks: [{ after: 1, ts: at("2026-11-08 12:50"), trigger: "auto", pre_tokens: 150_000 }] }) } }), TODAY, UTC);
  assert.deepEqual(table.columns.map((column) => column.label),
                   ["#", "Time", "Turn", "Model", "Context", "Fresh input", "Cache read", "Cache write 5m",
                    "Cache write 1h", "Output", "Cost"]);
  assert.deepEqual(table.rows.map((row) => row.key), ["r:1", "c:1", "r:2"]);
  assert.deepEqual(table.rows[0].cells, ["1", "today 12:45", "1", "m-opus", "1,000", "10 · <$0.01", "100", "20", "0",
                                         "5 · <$0.01", "$0.01"]);
  assert.deepEqual(table.rows[1].cells.slice(0, 5), ["", "today 12:50", "", "compaction (auto)", "150,000 before"]);
  assert.equal(table.rows[2].cells[9], "? · <$0.01");
  assert.equal(table.rows[2].cells[10], "no price");
});

test("the chart puts requests at their times, the dearest in the text colour, the context under the window", () => {
  const rows = [request(1, "2026-11-08 12:45", { context: 100_000 }), request(2, "2026-11-08 13:45", { cost: 0.5, context: 200_000 }),
                request(3, "2026-11-08 14:45", { context: 268_000, window: 1_000_000, turn_start: true, turn: 2 })];
  const chart = sessionChart(report(rows, {}, { total: { ...sums(), ...requestsTotal({ turns: 2,
    marks: [{ after: 1, ts: null, trigger: null, pre_tokens: null }] }) } }), "cost", 500, TODAY, UTC);
  assert.equal(chart.end, 442);
  assert.deepEqual(chart.bars.map((bar) => Math.round(bar.x + bar.width / 2)), [46, 244, 442]);
  assert.deepEqual(chart.bars.map((bar) => bar.dearest), [false, true, false]);
  assert.equal(chart.bars[1].y, 8);
  assert.equal(chart.top, "$0.50");
  assert.equal(chart.window.y, 78);
  assert.equal(chart.window.words, "1,000k");
  assert.match(chart.window.d, /^M46,\d+(\.\d)?H\d+(\.\d)?H\d+(\.\d)?V78.5H442$/);
  assert.equal(chart.dot.words, "268k");
  assert.equal(chart.turns.length, 1);
  assert.equal(chart.compactions[0].label, "compaction");
  assert.deepEqual(chart.labels.map((label) => label.text), ["12:45", "13:00", "14:00", "14:45"].filter((text) =>
    chart.labels.some((label) => label.text === text)));
  assert.equal(chart.labels[0].text, "12:45");
  assert.equal(chart.labels[chart.labels.length - 1].text, "14:45");
  assert.match(chart.hits[0].title, /^#1 · /);
  assert.equal(sessionChart(report(rows, {}, { total: { ...sums(), ...requestsTotal() } }), "tokens", 500, TODAY, UTC).top,
               kWords(135));
  // With a request's time missing, requests stand one per column and the axis has no words.
  const untimed = sessionChart(report([request(1, null), request(2, "2026-11-08 13:00")], {},
                                      { total: { ...sums(), ...requestsTotal() } }), "cost", 500, TODAY, UTC);
  assert.deepEqual(untimed.labels, []);
  assert.equal(sessionChart(report([], {}, { total: { ...sums(), ...requestsTotal() } }), "cost", 500, TODAY, UTC), null);
});

test("an addressed session's row comes from the session answer over its whole life", () => {
  const session = report([agent("main", 2), agent("a-1", 1, { first_ts: at("2026-11-08 12:00") })], { cost: 3, requests: 6 },
                         { ref: REF, context: { tokens: 10, window: 100 } });
  const row = placedRow(session, [request(1, null, { model: "m-b" })]);
  assert.equal(row.session_id, "s-1");
  assert.equal(row.cost, 3);
  assert.equal(row.requests, 6);
  assert.equal(row.first_ts, at("2026-11-08 12:00"));
  assert.deepEqual(row.models, ["m-b", "m-opus"]);
  assert.deepEqual(row.context, { tokens: 10, window: 100 });
  assert.equal(placedRow(report([], {}, { ref: REF }), null), null);
  assert.equal(placedRow(report([agent("main", 1)], {}, { ref: null }), null), null);
});
