import assert from "node:assert/strict";
import test from "node:test";

import {
  AGENTS_FIRST, agentMap, agentPath, moreLine, openedAgent, shownCount, subagentLines,
} from "../src/subagents.ts";

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
const agent = (name, cost, extra = {}) => ({ ...sums({ cost, requests: 2, tokens: 1000 }), unpriced_models: [],
  rules: [], agent: name, kind: name === "main" ? "main" : "subagent", title: null, model: "m-opus",
  first_ts: at("2026-11-08 12:45"), last_ts: at("2026-11-08 13:00"), ...extra });
const request = (n, ts, extra = {}) => ({ n, ts: at(ts), model: "m-haiku", tier: null, turn: 1, turn_start: n === 1,
  context: 1000 * n, window: 200_000, fresh_input: 10, cache_read: 100, cache_write_5m: 20, cache_write_1h: 0,
  output: 5, unknown: [], synthetic: false, cost: 0.01 * n, split: [], ...extra });
const requestsTotal = (extra = {}) => ({ marks: [], turns: 1, claimed_elsewhere: 0, copied_marks: 0,
  marks_known: true, ...extra });

test("the rows are the agents other than main, dearest first, each its models summed with its share", () => {
  const answer = report([
    agent("main", 3), agent("a-cheap", 0.2, { first_ts: at("2026-11-07 09:05") }),
    agent("a-dear", 0.5), agent("a-dear", 0.3, { model: "m-haiku", first_ts: at("2026-11-08 12:40"), tokens: 500 }),
    agent("a-free", null, { tokens: 50, unpriced_tokens: 50, complete: false }),
  ], { cost: 4 });
  const lines = subagentLines(answer, TODAY, UTC);
  assert.deepEqual(lines.map((line) => line.agent), ["a-dear", "a-cheap", "a-free"]);
  const [dear, cheap, free] = lines;
  assert.equal(dear.kind, "subagent");
  assert.equal(dear.models, "m-opus, m-haiku");
  assert.equal(dear.first, "first 12:40");
  assert.equal(dear.figures, "4 requests, 1.50k tokens");
  assert.equal(dear.cost, "$0.80");
  assert.equal(dear.share, "20% of the session");
  assert.equal(cheap.first, "first yesterday 09:05");
  assert.equal(cheap.share, "5% of the session");
  // No price: the cost says so and no share is said.
  assert.equal(free.cost, "no price");
  assert.equal(free.share, null);
});

test("a session with no priced cost gives its rows no share, and with only main no row", () => {
  const unpriced = report([agent("main", null), agent("a-1", null)], { cost: null });
  assert.equal(subagentLines(unpriced, TODAY, UTC)[0].share, null);
  assert.deepEqual(subagentLines(report([agent("main", 1)], { cost: 1 }), TODAY, UTC), []);
});

test("Show lists ten, thirty more each time, and always reaches the opened agent", () => {
  const lines = Array.from({ length: 45 }, (_, i) => ({ agent: `a-${i}` }));
  assert.equal(AGENTS_FIRST, 10);
  assert.equal(shownCount(lines, 0, null), 0);
  assert.equal(shownCount(lines, 1, null), 10);
  assert.equal(moreLine(lines, 10), "35 more");
  assert.equal(shownCount(lines, 40, null), 40);
  assert.equal(shownCount(lines, 70, null), 45);
  assert.equal(moreLine(lines, 45), null);
  // An address that names the twentieth agent shows the rows down to it.
  assert.equal(shownCount(lines, 0, "a-19"), 20);
  assert.equal(shownCount(lines, 0, "a-3"), 10);
  // An agent the answer does not hold opens nothing.
  assert.equal(shownCount(lines, 0, "a-nowhere"), 0);
  assert.equal(shownCount(lines.slice(0, 3), 1, null), 3);
});

test("an agent's requests are read with the session's query", () => {
  assert.equal(agentPath("s-1", "a-1", [["since", "2026-11-02"], ["harness", "claude"]]),
               "api/requests?session=s-1&agent=a-1&since=2026-11-02&harness=claude");
});

test("the map holds every agent, main among them, sized by cost and filling the box", () => {
  const answer = report([agent("main", 3), agent("a-1", 1), agent("a-2", 0)], { cost: 4 });
  const map = agentMap(answer, 400, 96);
  assert.equal(map.measure, "cost");
  assert.equal(map.aria, "The session's agents by cost");
  assert.deepEqual(map.cells.map((cell) => cell.agent), ["main", "a-1"]);
  const area = (cell) => cell.width * cell.height;
  assert.ok(area(map.cells[0]) > 2.5 * area(map.cells[1]));
  for (const cell of map.cells) {
    assert.ok(cell.x >= 0 && cell.y >= 0 && cell.x + cell.width <= 400 && cell.y + cell.height <= 96);
  }
  assert.deepEqual(map.cells[0].words, ["main", "$3.00"]);
  assert.equal(map.cells[1].title, "a-1: $1.00, 25% of the session");
});

test("the map goes by tokens with nothing priced, names a cell only where the words fit, and is absent with nothing", () => {
  const tokens = agentMap(report([agent("main", null, { tokens: 9000 }), agent("a-long-agent-id", null, { tokens: 300 })],
                                 { cost: null }), 300, 96);
  assert.equal(tokens.measure, "tokens");
  assert.deepEqual(tokens.cells[0].words, ["main", "9.00k tokens"]);
  assert.deepEqual(tokens.cells[1].words, []);
  assert.equal(tokens.cells[1].title, "a-long-agent-id: 300 tokens");
  assert.equal(agentMap(report([agent("main", 0, { tokens: 0 })], { cost: 0 }), 300, 96), null);
});

test("an opened agent says its requests and its last context, then its chart, notes and table", () => {
  const answer = report([request(1, "2026-11-08 12:45"), request(2, "2026-11-08 12:50", { window: null }),
                         request(3, "2026-11-08 12:55", { context: null })], { requests: 3 }, {});
  answer.total = { ...answer.total, ...requestsTotal() };
  const model = openedAgent({ value: answer, loading: false }, TODAY, UTC);
  // The last request's context is not known, so there is no Context line.
  assert.deepEqual(model.items.map((item) => item.kind === "line" ? item.label : item.kind), ["Requests", "chart"]);
  assert.equal(words(model.items[0].parts), "3 requests from 12:45 to 12:55 · the dearest $0.03 at 12:55");
  const known = openedAgent({ value: { ...answer, rows: answer.rows.slice(0, 2).concat(
    request(3, "2026-11-08 12:56", { synthetic: true, context: null })) }, loading: false }, TODAY, UTC);
  // A synthetic request is passed over; the last real one has no window known.
  assert.equal(words(known.items[1].parts), "2k tokens on the last request");
  const windowed = openedAgent({ value: { ...answer, rows: [request(1, "2026-11-08 12:45", { context: 50_000 })] },
                                loading: false }, TODAY, UTC);
  assert.equal(words(windowed.items[1].parts), "50k of 200k tokens on the last request, 25% full");
  assert.deepEqual(model.notes, ["window unknown for m-haiku"]);
  assert.equal(model.table.rows.length, 3);
});

test("an opened agent reads Reading… while its read is out, and nothing when it failed", () => {
  assert.deepEqual(openedAgent({ value: null, loading: true }, TODAY, UTC).items, [{ kind: "reading", key: "agent" }]);
  assert.deepEqual(openedAgent({ value: null, loading: false }, TODAY, UTC),
                   { items: [], notes: [], table: null });
  const none = openedAgent({ value: { ...report([]), total: { ...sums(), ...requestsTotal() } }, loading: false },
                           TODAY, UTC);
  assert.deepEqual(none.items, []);
  assert.equal(none.table, null);
});
