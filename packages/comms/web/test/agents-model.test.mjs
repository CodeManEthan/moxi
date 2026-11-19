import assert from "node:assert/strict";
import test from "node:test";

import { agentsOf, authorsWords, cacheWords, CASES, leadOf, namedInFull, noteText, operatorAddresses, parseMailState,
         parsePageRoster } from "../src/agents-model.ts";
import { parseRoster } from "../src/feed.ts";

// Times in UTC as `HH:MM`, so the words are the same in any zone.
const when = (at) => new Date(at * 1000).toISOString().slice(11, 16);
const at = (hhmm) => Date.parse(`2026-11-12T${hhmm}:00Z`) / 1000;
const NOW = at("19:10");

const row = (qualified, fields = {}) => ({
  qualified, name: qualified.split("@")[0], scope: qualified.includes("@") ? qualified.split("@")[1] : null,
  live: false, kind: "agent", module: null, tier: null, adapter: null, directors: [], session_id: null,
  bound_at: null, awaiting: [], cache: null, via: null, ...fields,
});
const bound = (qualified, fields = {}) =>
  row(qualified, { live: true, kind: null, tier: "worker", adapter: "claude", session_id: `s-${qualified}`,
                   bound_at: at("12:00"), ...fields });
const waiting = (qualified, count, since) => ({ qualified, count, since: at(since) });
const roster = (rows, waits = []) => ({ state: "up", detail: null, rows, waiting: waits, today: "2026-11-12" });
// An author as the server marks it: the fixtures' operator is the name `operator`.
const author = (qualified, isOperator = qualified === "operator") => ({ qualified, isOperator });
const queued = (qualified, waiting, from = [], handed = 0) =>
  ({ qualified, waiting, handed, from: from.map((who) => (typeof who === "string" ? author(who) : who)) });
const mail = (fields = {}) => ({ state: "up", detail: null, today: "2026-11-12", queued: [], owed: [], asks: [],
                                 last: [], operator_names: ["operator"], ...fields });
const owed = (author, hhmm, id = "1000000000000000001") =>
  ({ message_id: id, author, at: at(hhmm), text: "Is it ready?" });
const warm = (hhmm) => ({ kind: "warm", until: at(hhmm) });

const agents = (r, m = null, options = {}) => agentsOf(r, m, { now: NOW, when, hasCache: true, ...options });
const only = (r, m, options) => {
  const { rows } = agents(r, m, options);
  assert.equal(rows.length, 1, JSON.stringify(rows.map((x) => x.name)));
  return rows[0];
};
const launched = (fields = {}) => ({ where: "slot 4 · atlas-api", fact: { text: "goes cold at 19:22", heavy: true },
                                     uncovered: true, waits: "waiting on 2 agents", coverable: false, title: null,
                                     request_id: "r1", session: { name: "builder one", harness: "claude", route: null },
                                     cache: null, ...fields });

// --- the reads ------------------------------------------------------------------------------

test("the roster's value gains waiting and today; a bad waiting entry is left out", () => {
  const data = { state: "up", detail: null, rows: [row("a")], today: "2026-11-12",
                 waiting: [waiting("a", 2, "17:40"), { qualified: "b", count: 0, since: 1 },
                           { qualified: "c", count: 1, since: "x" }, null] };
  const got = parsePageRoster(data, parseRoster);
  assert.deepEqual(got.waiting, [waiting("a", 2, "17:40")]);
  assert.equal(got.today, "2026-11-12");
  assert.equal(parsePageRoster({ ...data, today: undefined }, parseRoster).today, null);
  assert.deepEqual(parsePageRoster({ state: "offline", detail: null, rows: [], waiting: [] }, parseRoster).waiting, []);
  assert.equal(parsePageRoster(null, parseRoster), null);
});

test("the mail state: what is not one is left out, owed oldest first", () => {
  const got = parseMailState({
    state: "up", detail: null, today: "2026-11-12",
    queued: [{ qualified: "a", waiting: 1, handed: 0,
               from: [{ qualified: "b", is_operator: false }, 3, "c", { qualified: "d" },
                      { qualified: "e", is_operator: "yes" }, { qualified: "f", operator: true }] },
             { qualified: "x", waiting: -1, handed: 0, from: [] }],
    owed: [owed("b", "18:00", "2"), owed("c", "17:00", "3"), { ...owed("d", "17:00"), message_id: 12 }],
    asks: [{ asker: "a", awaited: "b", message_id: "4", at: 1, read: false },
           { asker: "a", awaited: "c", message_id: "5", at: 1, read: true }],
    last: [{ qualified: "a", at: 5 }, { qualified: "b" }],
  });
  assert.deepEqual(got.queued, [queued("a", 1, ["b"])]);
  assert.deepEqual(got.owed.map((entry) => entry.author), ["c", "b"]);
  assert.deepEqual(got.asks.map((ask) => ask.awaited), ["b"]);
  assert.deepEqual(got.last, [{ qualified: "a", at: 5 }]);
  assert.deepEqual(parseMailState({ state: "error", detail: "no", today: "2026-11-12", queued: [1] }).queued, []);
  for (const bad of [null, [], { state: "offline" }, { state: "up", detail: 3 }]) {
    assert.equal(parseMailState(bad), null, JSON.stringify(bad));
  }
});

// --- rows by name -----------------------------------------------------------------------------

test("one row per name: a scoped address with mail is a line under its name's row", () => {
  // builder-2 is a standing bare name with no mail; builder-2@kestrel-web has one message.
  const r = roster([row("builder-2")], [waiting("builder-2@kestrel-web", 1, "16:30")]);
  const m = mail({ queued: [queued("builder-2@kestrel-web", 1, ["planner@atlas"])] });
  const got = only(r, m);
  assert.equal(got.name, "builder-2");
  assert.equal(got.lead, "builder-2");
  assert.equal(got.case, "mail");
  assert.deepEqual(got.fact, { text: "1 message waiting", heavy: true });
  // Its address line carries the time and authors, so line two doesn't repeat them.
  assert.deepEqual(got.line, { lead: "not running", heavy: true, more: null });
  assert.deepEqual(got.note.lines, [{ qualified: "builder-2@kestrel-web",
                                      text: "builder-2@kestrel-web · 1 message since 16:30, from planner" }]);
  assert.equal(noteText(got), "builder-2@kestrel-web · 1 message since 16:30, from planner");
  assert.deepEqual(got.acts, [{ kind: "message", to: "builder-2" }]);
});

test("a scoped address with neither mail nor a session goes to Other names, the bare one stays", () => {
  const r = roster([bound("planner"), row("planner@kestrel-web"), row("ops-watch"), row("ops-watch@x")]);
  const { rows, others } = agents(r);
  assert.deepEqual(rows.map((x) => x.name), ["planner"]);
  assert.deepEqual(rows[0].addresses.map((a) => a.qualified), ["planner"]);
  assert.deepEqual(rows[0].note.lines, []);
  assert.deepEqual(others, [
    { qualified: "ops-watch", sentBy: null, writable: true },
    { qualified: "ops-watch@x", sentBy: null, writable: true },
    { qualified: "planner@kestrel-web", sentBy: null, writable: true },
  ]);
});

test("a send-only name is in Other names with its module and no Message", () => {
  const r = roster([row("console", { kind: "send-only", module: "ui" }), row("relay", { kind: "send-only" })]);
  const { rows, others } = agents(r);
  assert.deepEqual(rows, []);
  assert.deepEqual(others, [{ qualified: "console", sentBy: "ui", writable: false },
                            { qualified: "relay", sentBy: null, writable: false }]);
});

test("a send-only name a program registered is sent by a program", () => {
  const r = roster([row("ticker", { kind: "send-only", module: null, holder: "program" })]);
  const { others } = agents(r);
  assert.deepEqual(others, [{ qualified: "ticker", sentBy: "a program", writable: false }]);
});

test("an owed author with no other fact is a row of its own, with Reply", () => {
  const got = only(roster([]), mail({ owed: [owed("tester@atlas", "18:02")] }));
  assert.equal(got.name, "tester");
  assert.equal(got.lead, "tester@atlas");
  assert.equal(got.case, "owed");
  assert.deepEqual(got.fact, { text: "waiting for your reply", heavy: true });
  assert.deepEqual(got.line, { lead: "wrote to you 18:02, wants one reply", heavy: false, more: null });
  assert.deepEqual(got.acts, [{ kind: "reply", to: "tester@atlas", message_id: "1000000000000000001" },
                              { kind: "message", to: "tester@atlas" }]);
});

test("Reply writes to the asker's own address when it is not the lead", () => {
  const r = roster([bound("planner", { cache: warm("20:00") })]);
  const m = mail({ owed: [owed("planner@kestrel-web", "18:02", "7"), owed("planner", "18:30", "8")] });
  const got = only(r, m);
  assert.equal(got.lead, "planner");
  // The oldest ask is answered, at the address that asked.
  assert.deepEqual(got.acts, [{ kind: "reply", to: "planner@kestrel-web", message_id: "7" },
                              { kind: "message", to: "planner" }]);
  assert.equal(got.line.lead, "wrote to you 18:02, wants one reply");
});

// --- the lead address -------------------------------------------------------------------------

test("the lead: bare and bound, then the newest bound, then bare, then oldest mail, then oldest owed", () => {
  const address = (qualified, fields = {}) => ({ qualified, name: qualified.split("@")[0], row: null, bound: false,
                                                 waiting: null, queued: null, owed: [], last: null, launched: null,
                                                 ...fields });
  const bareBound = address("x", { bound: true, row: bound("x", { bound_at: 1 }) });
  const newer = address("x@b", { bound: true, row: bound("x@b", { bound_at: 9 }) });
  const older = address("x@a", { bound: true, row: bound("x@a", { bound_at: 5 }) });
  assert.equal(leadOf([older, bareBound, newer]).qualified, "x");
  assert.equal(leadOf([older, address("x"), newer]).qualified, "x@b");
  const early = address("x@a", { waiting: waiting("x@a", 1, "10:00") });
  const late = address("x@b", { waiting: waiting("x@b", 1, "09:00") });
  assert.equal(leadOf([early, address("x"), late]).qualified, "x");
  assert.equal(leadOf([early, late]).qualified, "x@b");
  const asked = (q, hhmm) => address(q, { owed: [owed(q, hhmm)] });
  assert.equal(leadOf([asked("x@a", "12:00"), asked("x@b", "11:00")]).qualified, "x@b");
});

// --- cases -------------------------------------------------------------------------------------

test("each line-two case, read off the lead", () => {
  const m = mail({
    queued: [queued("reviewer", 1, ["operator"]), queued("builder-1", 1, ["planner"])],
    asks: [{ asker: "planner", awaited: "builder-1", message_id: "9", at: at("18:28"), read: false }],
    last: [{ qualified: "claude-app", at: at("18:21") }],
  });
  const r = roster([
    bound("reviewer", { cache: { kind: "cold", until: at("18:35") } }),
    bound("builder-1", { cache: warm("19:22") }),
    bound("planner", { cache: warm("20:04"), awaiting: ["builder-1"] }),
    bound("claude-app"),
    row("docs-writer"),
  ], [waiting("reviewer", 1, "18:50"), waiting("builder-1", 1, "18:28"), waiting("docs-writer", 1, "17:40")]);
  const m2 = { ...m, queued: [...m.queued, queued("docs-writer", 1, ["builder-1"])] };
  const byName = Object.fromEntries(agents(r, m2).rows.map((x) => [x.name, x]));

  assert.deepEqual([byName["docs-writer"].fact, byName["docs-writer"].line],
                   [{ text: "1 message waiting", heavy: true },
                    { lead: "not running", heavy: true, more: "since 17:40, from builder-1" }]);
  assert.deepEqual([byName.reviewer.case, byName.reviewer.fact, byName.reviewer.line],
                   ["held", { text: "cold", heavy: false },
                    { lead: "1 message held until its next turn", heavy: false, more: "from you 18:50" }]);
  assert.deepEqual([byName["builder-1"].fact, byName["builder-1"].line],
                   [{ text: "warm until 19:22", heavy: false },
                    { lead: "1 message held until its next turn", heavy: false, more: "from planner 18:28" }]);
  assert.deepEqual([byName.planner.case, byName.planner.fact, byName.planner.line],
                   ["awaiting", { text: "warm until 20:04", heavy: false },
                    { lead: "waiting on builder-1's reply since 18:28", heavy: false,
                      more: "builder-1 hasn't read it yet" }]);
  assert.deepEqual([byName["claude-app"].case, byName["claude-app"].fact, byName["claude-app"].line],
                   ["idle", null, { lead: "no mail waiting", heavy: false, more: "last message 18:21" }]);
});

test("a held row and an awaiting row have no idle line three; held over awaiting gives awaiting", () => {
  const ask = { asker: "planner", awaited: "builder-1", message_id: "9", at: at("18:28"), read: false };
  const m = mail({ queued: [queued("builder-1", 4, ["planner"])], asks: [ask], last: [{ qualified: "builder-1", at: at("18:28") }] });
  const held = only(roster([bound("builder-1", { cache: warm("19:22") })], [waiting("builder-1", 4, "18:00")]), m);
  assert.equal(held.case, "held");
  assert.equal(held.second, null);
  assert.equal(noteText(held), null);
  const awaiting = only(roster([bound("planner", { cache: warm("20:04"), awaiting: ["builder-1"] })]), m);
  assert.equal(awaiting.case, "awaiting");
  assert.equal(awaiting.second, null);
  assert.equal(noteText(awaiting), null);
  const both = only(roster([bound("planner", { cache: warm("20:04"), awaiting: ["builder-1"] })],
                           [waiting("planner", 2, "18:10")]), m);
  assert.equal(both.case, "held");
  assert.equal(both.second, "awaiting");
  assert.equal(noteText(both), "warm until 20:04 · waiting on builder-1's reply since 18:28 · builder-1 hasn't read it yet");
  // Uncovered alone has no line three either.
  const uncovered = only(roster([bound("builder-1", { cache: warm("19:22") })]), mail(),
                         { launched: new Map([["builder-1", launched()]]) });
  assert.equal(uncovered.case, "uncovered");
  assert.equal(noteText(uncovered), null);
});

test("an owed row whose lead is bound with nothing waiting: the idle case on line three", () => {
  const r = roster([bound("planner", { cache: warm("20:04") })]);
  const got = only(r, mail({ owed: [owed("planner", "19:02")], last: [{ qualified: "planner", at: at("19:02") }] }));
  assert.equal(got.case, "owed");
  assert.equal(got.second, "idle");
  assert.equal(noteText(got), "warm until 20:04 · no mail waiting · last message 19:02");
});

test("awaiting with no ask in the read says only whose reply; a read ask drops the second part", () => {
  const r = roster([bound("planner", { awaiting: ["builder-1@atlas"] })]);
  assert.deepEqual(only(r, mail()).line, { lead: "waiting on builder-1's reply", heavy: false, more: null });
  const m = mail({ asks: [{ asker: "planner", awaited: "builder-1@atlas", message_id: "9", at: at("18:28"), read: null }] });
  assert.deepEqual(only(r, m).line, { lead: "waiting on builder-1's reply since 18:28", heavy: false, more: null });
  // An ask the roster's awaiting doesn't name is not this row's.
  const other = mail({ asks: [{ asker: "planner", awaited: "tester", message_id: "9", at: at("18:28"), read: false }] });
  assert.deepEqual(only(r, other).line, { lead: "waiting on builder-1's reply", heavy: false, more: null });
});

test("idle with no newest message within the day: only no mail waiting", () => {
  assert.deepEqual(only(roster([bound("tester")])).line, { lead: "no mail waiting", heavy: false, more: null });
});

test("the plural, and from shown only when the books and the roster agree on the count", () => {
  const r = roster([row("release-notes")], [waiting("release-notes", 3, "09:15")]);
  const agree = mail({ queued: [queued("release-notes", 3, ["planner", "builder-3@kestrel-web"])] });
  assert.deepEqual(only(r, agree).line.more, "since 09:15, from planner and builder-3");
  assert.equal(only(r, agree).fact.text, "3 messages waiting");
  const differ = mail({ queued: [queued("release-notes", 2, ["planner"])] });
  assert.deepEqual(only(r, differ).line.more, "since 09:15");
  assert.deepEqual(only(r, null).line.more, "since 09:15");
});

test("from drops only the disagreeing address's authors", () => {
  const r = roster([row("x")], [waiting("x@a", 1, "10:00"), waiting("x@b", 2, "11:00")]);
  const m = mail({ queued: [queued("x@a", 1, ["planner"]), queued("x@b", 1, ["tester"])] });
  const got = only(r, m);
  assert.equal(got.fact.text, "3 messages waiting");
  // Home names both in full, so each has its line, x@b's without its authors.
  assert.deepEqual(got.note.lines.map((line) => line.text),
                   ["x@a · 1 message since 10:00, from planner", "x@b · 2 messages since 11:00"]);
});

test("authors: the one the server marks the operator as you, short names unless two share one", () => {
  const words = (...names) => authorsWords(names.map((name) => author(name)));
  assert.equal(words("operator"), "you");
  assert.equal(authorsWords([author("the-desk", true), author("planner@a")]), "you and planner");
  assert.equal(words("x@a", "x@b", "y"), "x@a, x@b and y");
  assert.equal(words("a", "b", "c", "d"), "a, b and 2 more");
});

test("the cache fact: warm until, cold, a warm read past its time cold, none", () => {
  assert.deepEqual(cacheWords(warm("20:00"), NOW, when), { text: "warm until 20:00", heavy: false });
  assert.deepEqual(cacheWords({ kind: "cold", until: at("18:00") }, NOW, when), { text: "cold", heavy: false });
  assert.deepEqual(cacheWords(warm("19:00"), NOW, when), { text: "cold", heavy: false });
  assert.equal(cacheWords(null, NOW, when), null);
});

// --- a name with several addresses ------------------------------------------------------------

test("not running only when no address is bound; else the address that isn't", () => {
  const one = roster([bound("builder-2", { cache: warm("20:00") })], [waiting("builder-2@kestrel-web", 1, "16:30")]);
  const got = only(one, mail({ queued: [queued("builder-2@kestrel-web", 1, ["planner"])] }));
  assert.equal(got.lead, "builder-2");
  assert.equal(got.case, "mail");
  // Mail waits at the name, so the lead is not idle: line three is the address line alone.
  assert.equal(got.second, null);
  assert.deepEqual(got.line, { lead: "builder-2@kestrel-web isn't running", heavy: true, more: null });
  assert.equal(noteText(got), "builder-2@kestrel-web · 1 message since 16:30, from planner");

  const two = roster([bound("builder-2")], [waiting("builder-2@a", 1, "16:30"), waiting("builder-2@b", 2, "17:00")]);
  const both = only(two, null);
  assert.deepEqual(both.fact, { text: "3 messages waiting", heavy: true });
  assert.deepEqual(both.line, { lead: "2 addresses aren't running", heavy: true, more: null });
  assert.deepEqual(both.note.lines.map((line) => line.qualified), ["builder-2@a", "builder-2@b"]);
});

test("a bound address that is not the lead has a line with its cache fact and what it holds", () => {
  const r = roster([bound("tester", { cache: warm("20:00") }),
                    bound("tester@b", { cache: { kind: "cold", until: at("18:00") } }),
                    bound("tester@a")],
                   [waiting("tester@b", 2, "18:00")]);
  const got = only(r, mail());
  assert.equal(got.lead, "tester");
  assert.deepEqual(got.note.lines.map((line) => line.text), ["tester@a", "tester@b · cold · 2 messages held"]);
  assert.equal(only(r, mail(), { hasCache: false }).note.lines[1].text, "tester@b · cold · 2 messages not read yet");
});

test("the second case under owed, and mail as the second case always on the row", () => {
  // An agent that asked him has mail waiting at its own unbound address.
  const r = roster([], [waiting("docs-writer", 2, "17:40")]);
  const m = mail({ owed: [owed("docs-writer", "17:00")], queued: [queued("docs-writer", 2, ["builder-1"])] });
  const got = only(r, m);
  assert.equal(got.case, "owed");
  assert.equal(got.second, "mail");
  assert.equal(noteText(got), "2 messages waiting · not running · since 17:40, from builder-1");

  // When every address with the mail has its own line, that line carries it.
  const scoped = roster([], [waiting("docs-writer@a", 2, "17:40")]);
  const m2 = mail({ owed: [owed("docs-writer", "17:00")], queued: [queued("docs-writer@a", 2, ["builder-1"])] });
  const carried = only(scoped, m2);
  assert.equal(carried.lead, "docs-writer");
  assert.equal(carried.note.second, null);
  assert.equal(noteText(carried), "docs-writer@a · 2 messages since 17:40, from builder-1");
});

test("an owed row whose lead is bound and awaiting: the awaiting case on line three", () => {
  const r = roster([bound("planner", { cache: warm("20:04"), awaiting: ["builder-1"] })]);
  const m = mail({ owed: [owed("planner", "19:02")],
                   asks: [{ asker: "planner", awaited: "builder-1", message_id: "9", at: at("18:28"), read: false }] });
  const got = only(r, m);
  assert.equal(got.line.lead, "wrote to you 19:02, wants one reply");
  assert.equal(noteText(got), "warm until 20:04 · waiting on builder-1's reply since 18:28 · builder-1 hasn't read it yet");
});

test("the address an open box sent to is kept as a row: no case until a read gives it one", () => {
  const empty = roster([]);
  assert.deepEqual(agents(empty, mail()).rows, []);
  const got = only(empty, mail(), { keep: "critic" });
  assert.deepEqual([got.name, got.lead, got.case, got.second, got.fact, got.line, noteText(got), got.acts],
                   ["critic", "critic", null, null, null, { lead: "", heavy: false, more: null }, null,
                    [{ kind: "message", to: "critic" }]]);
  // The books already hold the message for it: that is its case.
  const books = only(empty, mail({ queued: [queued("critic", 1, ["operator"])] }), { keep: "critic" });
  assert.equal(books.case, "mail");
  assert.deepEqual(books.fact, { text: "1 message waiting", heavy: true });
  // A standing scoped address kept is under its row, not in Other names.
  const scoped = agents(roster([row("critic@a")]), mail(), { keep: "critic@a" });
  assert.deepEqual([scoped.rows.map((x) => x.lead), scoped.others], [["critic@a"], []]);
  // The operator is never a row.
  assert.deepEqual(agents(empty, mail(), { keep: "operator" }).rows, []);
});

// --- Home and the page name the same mail by the same name -----------------------------------

test("Home names an address in full when another unbound one shares its name, and so does the page", () => {
  // Home's rule: unbound addresses with mail; a shared short name is written in full.
  const shared = roster([], [waiting("builder-2@a", 1, "16:30"), waiting("builder-2@b", 1, "17:00")]);
  assert.deepEqual([...namedInFull(shared)].sort(), ["builder-2@a", "builder-2@b"]);
  const got = only(shared, null);
  // The lead is the oldest mail's address, and it has its own line all the same.
  assert.equal(got.lead, "builder-2@a");
  assert.deepEqual(got.note.lines.map((line) => line.qualified), ["builder-2@a", "builder-2@b"]);
  assert.equal(got.line.more, null);

  // One unbound scoped address: Home writes `builder-2`, the page counts it on builder-2's row.
  const single = roster([], [waiting("builder-2@a", 1, "16:30")]);
  assert.deepEqual([...namedInFull(single)], []);
  const alone = only(single, null);
  assert.equal(alone.name, "builder-2");
  assert.deepEqual(alone.note.lines, []);
  assert.deepEqual(alone.line, { lead: "not running", heavy: true, more: "since 16:30" });

  // A bound address is not Home's, so its name is short there.
  const mixed = roster([bound("builder-2@b")], [waiting("builder-2@a", 1, "16:30"), waiting("builder-2@b", 1, "17:00")]);
  assert.deepEqual([...namedInFull(mixed)], []);
});

// --- launcher's uncovered case ----------------------------------------------------------------

test("the uncovered case rises with coverable false and draws no Keep warm", () => {
  const r = roster([bound("builder-1", { cache: warm("19:22") })], [waiting("builder-1", 1, "18:28")]);
  const join = new Map([["builder-1", launched()]]);
  const got = only(r, mail(), { launched: join });
  assert.equal(got.case, "uncovered");
  assert.equal(got.where, "slot 4 · atlas-api");
  assert.deepEqual(got.fact, { text: "goes cold at 19:22", heavy: true });
  assert.deepEqual(got.line, { lead: "waiting on 2 agents", heavy: true, more: null });
  assert.equal(got.second, "held");
  assert.equal(noteText(got), "goes cold at 19:22 · 1 message held until its next turn · since 18:28");
  assert.deepEqual(got.acts, [{ kind: "message", to: "builder-1" }]);
  const coverable = only(r, mail(), { launched: new Map([["builder-1", launched({ coverable: true, title: "T" })]]) });
  assert.deepEqual(coverable.acts,
                   [{ kind: "keep-warm", request_id: "r1", title: "T" }, { kind: "message", to: "builder-1" }]);
});

test("launcher's fact wins over comms'; covered is no loud case", () => {
  const r = roster([bound("planner", { cache: warm("20:04") })]);
  const covered = launched({ where: "slot 3 · atlas-api", fact: { text: "kept warm while 1 agent works", heavy: false },
                             uncovered: false, waits: null });
  const got = only(r, mail(), { launched: new Map([["planner", covered]]) });
  assert.equal(got.case, "idle");
  assert.deepEqual(got.fact, { text: "kept warm while 1 agent works", heavy: false });
  const blank = launched({ fact: null, uncovered: false, waits: null });
  assert.deepEqual(only(r, mail(), { launched: new Map([["planner", blank]]) }).fact,
                   { text: "warm until 20:04", heavy: false });
});

test("without launcher: no where, comms' cache fact, no loud case", () => {
  const got = only(roster([bound("builder-1", { cache: warm("19:22") })]), mail());
  assert.equal(got.where, null);
  assert.deepEqual(got.fact, { text: "warm until 19:22", heavy: false });
  assert.equal(got.case, "idle");
});

// --- comms' words for the connector's binding ----------------------------------------------------

const CONNECTOR = "Claude's apps, through the connector";

test("the connector's binding has comms' words for it as its where, with or without launcher", () => {
  const app = bound("claude-app", { adapter: "connector", via: CONNECTOR });
  const r = roster([app, bound("builder-1", { cache: warm("19:22") })]);
  const read = mail({ last: [{ qualified: "claude-app", at: at("18:21") }] });
  const got = agents(r, read);
  assert.deepEqual(got.rows.map((x) => [x.name, x.where]), [["claude-app", CONNECTOR], ["builder-1", null]]);
  const claude = got.rows[0];
  assert.equal(claude.fact, null);
  assert.equal(claude.line.lead, "no mail waiting");
  // Launcher's where for another row stays its own; the binding's words win on its own row.
  const join = new Map([["builder-1", launched({ fact: null, uncovered: false, waits: null })],
                        ["claude-app", launched({ where: "terminal · elsewhere", fact: null, uncovered: false, waits: null })]]);
  assert.deepEqual(agents(r, read, { launched: join }).rows.map((x) => [x.name, x.where]),
                   [["claude-app", CONNECTOR], ["builder-1", "slot 4 · atlas-api"]]);
});

test("a connector-kind row without comms' words has no where", () => {
  const got = only(roster([bound("claude-app", { adapter: "connector" })]), mail());
  assert.equal(got.where, null);
});

test("from you: the server's mark decides, not the author's name", () => {
  const r = roster([bound("reviewer")], [waiting("reviewer", 2, "18:50")]);
  const got = only(r, mail({ queued: [queued("reviewer", 2, [author("the-desk", true), author("operator", false)])] }));
  assert.equal(got.line.more, "from you and operator 18:50");
});

test("without cache: no fact, and held mail reads not read yet", () => {
  const r = roster([bound("reviewer")], [waiting("reviewer", 1, "18:50")]);
  const got = only(r, mail({ queued: [queued("reviewer", 1, ["operator"])] }), { hasCache: false });
  assert.equal(got.fact, null);
  assert.deepEqual(got.line, { lead: "1 message not read yet", heavy: false, more: "from you 18:50" });
});

test("without launcher and without cache: the rows keep their cases and words", () => {
  const r = roster([bound("builder-1"), row("docs-writer")],
                   [waiting("builder-1", 2, "18:28"), waiting("docs-writer", 1, "17:40")]);
  const { rows } = agents(r, mail(), { hasCache: false, launched: null });
  assert.deepEqual(rows.map((x) => [x.name, x.case, x.where, x.fact?.text ?? null, x.line.lead]), [
    ["docs-writer", "mail", null, "1 message waiting", "not running"],
    ["builder-1", "held", null, null, "2 messages not read yet"],
  ]);
});

// --- order and groups -------------------------------------------------------------------------

test("the order: owed, then mail, then uncovered, then bound by newest message; ties by name", () => {
  const r = roster([
    bound("b-old"), bound("b-new"), bound("b-none"), bound("a-none"), bound("loud"),
    row("mail-late"), row("mail-early"),
  ], [waiting("mail-late", 1, "17:00"), waiting("mail-early", 1, "09:00")]);
  const m = mail({
    owed: [owed("ask-late", "15:00", "2"), owed("ask-early", "14:00", "3")],
    last: [{ qualified: "b-old", at: at("10:00") }, { qualified: "b-new", at: at("18:00") }],
  });
  const join = new Map([["loud", launched()]]);
  const { rows } = agents(r, m, { launched: join });
  assert.deepEqual(rows.map((x) => x.name),
                   ["ask-early", "ask-late", "mail-early", "mail-late", "loud", "b-new", "b-old", "a-none", "b-none"]);
  assert.deepEqual(CASES, ["owed", "mail", "uncovered", "held", "awaiting", "idle"]);
});

test("a bound row's newest message is the newest of any of its addresses", () => {
  const r = roster([bound("a"), bound("a@x"), bound("b")]);
  const m = mail({ last: [{ qualified: "a@x", at: at("18:00") }, { qualified: "b", at: at("17:00") }] });
  assert.deepEqual(agents(r, m).rows.map((x) => x.name), ["a", "b"]);
});

test("the operator is never a row: his names and the authors the server marks are left out", () => {
  const r = roster([bound("planner"), row("the-desk")],
                   [waiting("operator", 1, "17:40"), waiting("the-desk", 2, "17:50"), waiting("planner", 1, "18:00")]);
  const m = mail({ operator_names: ["operator"],
                   queued: [queued("the-desk", 2, [author("planner")]),
                            queued("planner", 1, [author("the-desk", true)])] });
  assert.deepEqual([...operatorAddresses(m)].sort(), ["operator", "the-desk"]);
  const { rows, others } = agents(r, m);
  assert.deepEqual(rows.map((x) => x.name), ["planner"]);
  assert.deepEqual(others, []);
  // His mail to an agent is still that agent's, by him.
  assert.equal(rows[0].line.more, "from you 18:00");
  // Before the mail state is read nothing is known of him.
  assert.deepEqual(operatorAddresses(null), new Set());
  assert.deepEqual(agents(r, null).rows.map((x) => x.name).sort(), ["operator", "planner", "the-desk"]);
  assert.deepEqual(parseMailState({ ...m, operator_names: ["operator", 7] }).operator_names, ["operator"]);
  assert.deepEqual(parseMailState({ ...m, operator_names: undefined }).operator_names, []);
});

test("a mail state that is an error reads as none", () => {
  const r = roster([bound("planner")]);
  const got = only(r, { ...mail({ owed: [owed("x", "10:00")] }), state: "error" });
  assert.equal(got.case, "idle");
});
