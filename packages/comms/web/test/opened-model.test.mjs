import assert from "node:assert/strict";
import test from "node:test";

import { agentsOf } from "../src/agents-model.ts";
import { joinRunning } from "../src/launched-model.ts";
import { parseMessage, parseQueue } from "../src/messages-model.ts";
import { COLD_TAIL, HELP, openedAgent, READING } from "../src/opened-model.ts";

const when = (at) => new Date(at * 1000).toISOString().slice(11, 16);
const at = (hhmm) => Date.parse(`2026-11-12T${hhmm}:00Z`) / 1000;
const NOW = at("19:10");

const row = (qualified, fields = {}) => ({
  qualified, name: qualified.split("@")[0], scope: qualified.includes("@") ? qualified.split("@")[1] : null,
  live: false, kind: "agent", module: null, tier: null, adapter: null, directors: [], session_id: null,
  bound_at: null, awaiting: [], cache: null, ...fields,
});
const bound = (qualified, fields = {}) =>
  row(qualified, { live: true, kind: null, tier: "worker", adapter: "claude", session_id: `s-${qualified}`,
                   bound_at: at("12:00"), ...fields });
const roster = (rows, waiting = []) => ({ state: "up", detail: null, rows, waiting, today: "2026-11-12" });
const mail = (fields = {}) => ({ state: "up", detail: null, today: "2026-11-12", queued: [], owed: [], asks: [], last: [], operator_names: ["operator"],
                                 ...fields });
const message = (id, fields = {}) => ({
  message_id: id, at: at("18:28"), author: "planner", author_tier: "worker", to: ["builder-1"], posture: "once",
  reply_to: null, text: "Is it ready?", withdrawn: false,
  rows: [{ qualified: "builder-1", state: "waiting", held: false, takeable: true }], ...fields,
});
const read = (value, fields = {}) => ({ loading: false, gone: false, failure: null, value, ...fields });
const queue = (messages, more = 0) => read({ state: "up", detail: null, messages, more });

function opened(r, m, name, fields = {}, launched = null) {
  const { rows } = agentsOf(r, m, { now: NOW, when, cache: true, launched });
  const agent = rows.find((x) => x.name === name);
  return openedAgent({ row: agent, to: agent.lead, roster: r.rows, asks: m.asks, queue: queue([]), now: NOW, when,
                       cache: true, details: false, ...fields });
}

test("a message and a queue are read as sent; what isn't one is left out", () => {
  assert.deepEqual(parseMessage(message("12")), message("12"));
  for (const bad of [null, message(12), message("1x"), message("12", { at: "x" }), message("12", { reply_to: 5 })]) {
    assert.equal(parseMessage(bad), null, JSON.stringify(bad));
  }
  const got = parseQueue({ state: "up", detail: null, messages: [message("12"), { message_id: "x" }], more: 3 });
  assert.deepEqual([got.messages.length, got.more], [1, 3]);
  assert.deepEqual(parseQueue({ state: "error", detail: "no", messages: [message("12")] }),
                   { state: "error", detail: "no", messages: [], more: 0 });
  assert.equal(parseQueue({ state: "down" }), null);
});

test("planner opened: cache, waits on with the unread ask, directs; the drawing's words", () => {
  const r = roster([bound("planner", { cache: { kind: "warm", until: at("20:04") }, awaiting: ["builder-1"] }),
                    bound("builder-1", { directors: ["planner"] })]);
  const m = mail({ asks: [{ asker: "planner", awaited: "builder-1", message_id: "7", at: at("18:28"), read: false }] });
  const got = opened(r, m, "planner");
  assert.deepEqual(got.before, [{ key: "cache", label: "Cache", value: "warm until 20:04", tail: null, link: { kind: "cache" } }]);
  assert.deepEqual(got.after.map((line) => [line.label, line.value, line.tail]), [
    ["Waits on", "builder-1",
     "for one reply to its 18:28 message, which builder-1 has not read: it is held until builder-1's next turn"],
    ["Directs", "builder-1", "builder-1 takes direction from planner"],
  ]);
  assert.deepEqual(got.after[0].link, { kind: "thread", a: "planner", b: "builder-1" });
  assert.deepEqual(got.choices, []);
  assert.equal(got.details, null);
});

test("waits on with no ask names the agent alone; without cache the ask is not called held", () => {
  const r = roster([bound("planner", { awaiting: ["builder-1"] }), bound("builder-1")]);
  assert.equal(opened(r, mail(), "planner").after[0].tail, null);
  const m = mail({ asks: [{ asker: "planner", awaited: "builder-1", message_id: "7", at: at("18:28"), read: false }] });
  assert.equal(opened(r, m, "planner", { cache: false }).after[0].tail,
               "for one reply to its 18:28 message, which builder-1 has not read");
});

test("a cold cache says since when, and that mail does not wake it; without cache no link", () => {
  const r = roster([bound("reviewer", { cache: { kind: "cold", until: at("18:35") } })]);
  assert.deepEqual(opened(r, mail(), "reviewer", { cache: false }).before,
                   [{ key: "cache", label: "Cache", value: "cold since 18:35", tail: COLD_TAIL, link: null }]);
  assert.deepEqual(opened(roster([bound("reviewer")]), mail(), "reviewer").before, []);
});

test("the queue: held for a bound address, waiting for an unbound one, unread once in its session", () => {
  const r = roster([bound("builder-1")], [{ qualified: "builder-1", count: 1, since: at("18:28") }]);
  const q = queue([
    message("1", { at: at("18:28") }),
    message("2", { at: at("18:50"), author: "the-desk", author_tier: "operator", text: "Review it." }),
    message("3", { rows: [{ qualified: "builder-1", state: "handed", held: false, takeable: false }] }),
  ], 2);
  const got = opened(r, mail(), "builder-1", { queue: q });
  assert.deepEqual(got.queue.map((group) => [group.label, group.items.map((item) => [item.time, item.author, item.takeable])]), [
    ["Held", [["18:28", "planner", false], ["18:50", "you", true]]],
    ["Unread", [["18:28", "planner", false]]],
  ]);
  assert.equal(got.more, "2 more");
  const unbound = roster([row("docs-writer")], [{ qualified: "docs-writer", count: 1, since: at("17:40") }]);
  const waiting = opened(unbound, mail(), "docs-writer", {
    queue: queue([message("4", { to: ["docs-writer"], rows: [{ qualified: "docs-writer", state: "waiting", held: false,
                                                                takeable: true }] })]) });
  assert.deepEqual(waiting.queue.map((group) => group.label), ["Waiting"]);
});

test("the queue's read: Reading… while out, its error said", () => {
  const r = roster([bound("builder-1")]);
  assert.equal(opened(r, mail(), "builder-1", { queue: read(null, { loading: true }) }).queueNote, READING);
  assert.equal(opened(r, mail(), "builder-1", { queue: read({ state: "error", detail: "The store can't be read.",
                                                              messages: [], more: 0 }) }).queueNote,
               "The mail held for builder-1 can't be read: The store can't be read.");
});

test("a name with two addresses offers both; directed by gathers the name's addresses", () => {
  const r = roster([row("builder-2", { directors: ["planner"] }), row("builder-2@kestrel-web", { directors: ["lead"] })],
                   [{ qualified: "builder-2", count: 1, since: at("17:00") },
                    { qualified: "builder-2@kestrel-web", count: 1, since: at("16:30") }]);
  const got = opened(r, mail(), "builder-2");
  assert.deepEqual(got.choices, [{ qualified: "builder-2", current: true }, { qualified: "builder-2@kestrel-web", current: false }]);
  assert.deepEqual(got.after.map((line) => [line.label, line.value]), [["Directed by", "lead and planner"]]);
});

test("details: the address, adapter, tier, bound at, the session, and the help sentence's words", () => {
  const got = opened(roster([bound("planner")]), mail(), "planner", { details: true });
  assert.deepEqual(got.details.map((line) => [line.label, line.value]), [
    ["Address", "planner"], ["Adapter", "claude"], ["Tier", "worker"], ["Bound at", "12:00"], ["Session", "s-planner"],
  ]);
  assert.equal(got.sessionId, "s-planner");
  assert.equal(HELP, "A name is held by one session at a time.");
  const asker = openedAgent({
    row: agentsOf(roster([]), mail({ owed: [{ message_id: "9", author: "scout", at: at("19:02"), text: "?" }] }),
                  { now: NOW, when, cache: true }).rows[0],
    to: "scout", roster: [], asks: [], queue: queue([]), now: NOW, when, cache: true, details: true });
  assert.deepEqual(asker.details.map((line) => line.label), ["Address"]);
  assert.equal(asker.sessionId, null);
});

// --- with launcher ------------------------------------------------------------------------------

const iso = (hhmm) => `2026-11-12T${hhmm}:00Z`;
const entry = (identity, fields = {}) => ({
  request_id: `r-${identity}`, harness: "claude", session_id: `s-${identity}`, name: "planner's session", identity,
  where: { target: "slot", slot: "3", project_key: "atlas-api", folder: "atlas-api" }, started_at: iso("12:00"),
  cache: { state: "warm", until: iso("20:04"), estimated: false }, waits: null, covered: false, coverable: null,
  activity: null, ...fields,
});
const join = (r, entries) => joinRunning({ state: "up", detail: null, count: entries.length, sessions: entries }, r.rows, when);

test("with launcher: Session leads, linked to its transcript with sessions; launcher's cache wins", () => {
  const r = roster([bound("planner", { cache: { kind: "warm", until: at("19:50") } })]);
  const launched = join(r, [entry("planner", { waits: { kind: "agents", count: 1 }, covered: true })]);
  const got = opened(r, mail(), "planner", { sessions: true }, launched);
  assert.deepEqual(got.before, [
    { key: "session", label: "Session", value: "planner's session · claude", tail: null,
      link: { kind: "sessions", route: "claude/s-planner" } },
    { key: "cache", label: "Cache", value: "warm until 20:04", tail: "kept warm while 1 agent works", link: { kind: "cache" } },
  ]);
  // Without sessions: no link.
  assert.equal(opened(r, mail(), "planner", { sessions: false }, launched).before[0].link, null);
});

test("with launcher: an estimated time says est.; a cold one since when; no cache read keeps comms'", () => {
  const r = roster([bound("planner", { cache: { kind: "cold", until: at("18:35") } })]);
  const est = join(r, [entry("planner", { cache: { state: "warm", until: iso("20:04"), estimated: true } })]);
  assert.equal(opened(r, mail(), "planner", {}, est).before[1].value, "warm until 20:04, est.");
  const cold = join(r, [entry("planner", { cache: { state: "cold", until: iso("18:40"), estimated: false } })]);
  assert.deepEqual(opened(r, mail(), "planner", { cache: false }, cold).before[1],
                   { key: "cache", label: "Cache", value: "cold since 18:40", tail: COLD_TAIL, link: null });
  const unread = join(r, [entry("planner", { cache: null })]);
  assert.deepEqual(opened(r, mail(), "planner", {}, unread).before.map((line) => [line.key, line.value]),
                   [["session", "planner's session · claude"], ["cache", "cold since 18:35"]]);
});

test("without launcher: no Session line", () => {
  const r = roster([bound("planner")]);
  assert.deepEqual(opened(r, mail(), "planner", { sessions: true }).before, []);
});
