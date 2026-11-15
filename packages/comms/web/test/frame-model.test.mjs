import assert from "node:assert/strict";
import test from "node:test";

import {
  agentsLabel, ANSWERING, DOCTOR, DOWN_LINE, DOWN_WHAT, frameModel, NAME_COMMAND, NO_AGENTS, NO_LONGER_OWED,
  NO_SUCH_VIEW, READING, SERVER_ERROR,
} from "../src/frame-model.ts";

// Times in UTC, so the words are the same in any zone.
const CLOCK = { time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
                day: (at) => new Date(at * 1000).toISOString().slice(0, 10) };
const at = (hhmm) => Date.parse(`2026-11-12T${hhmm}:00Z`) / 1000;
const NOW = at("19:10");
const TODAY = "2026-11-12";

const row = (qualified, fields = {}) => ({
  qualified, name: qualified.split("@")[0], scope: qualified.includes("@") ? qualified.split("@")[1] : null,
  live: false, kind: "agent", module: null, tier: null, adapter: null, directors: [], session_id: null,
  bound_at: null, awaiting: [], cache: null, ...fields,
});
const bound = (qualified, fields = {}) =>
  row(qualified, { live: true, kind: null, tier: "worker", adapter: "claude", session_id: `s-${qualified}`,
                   bound_at: at("12:00"), ...fields });
const roster = (rows, waiting = [], fields = {}) => ({ state: "up", detail: null, rows, waiting, today: TODAY, ...fields });
const read = (value, fields = {}) => ({ loading: false, gone: false, failure: null, value, ...fields });
const mail = (fields = {}) => read({ state: "up", detail: null, today: TODAY, queued: [], owed: [], asks: [], last: [], operator_names: ["operator"],
                                     ...fields });
const owed = (author, hhmm, id) => ({ message_id: id, author, at: at(hhmm), text: "Is it ready?" });

const CAST = roster([bound("planner"), bound("builder-1"), row("builder-2"), row("ops-watch")],
                    [{ qualified: "docs-writer", count: 1, since: at("17:40") },
                     { qualified: "builder-2@kestrel-web", count: 1, since: at("16:30") }]);

function frame(fields = {}) {
  const value = fields.rosterValue ?? CAST;
  return frameModel({
    roster: read(value), kept: value.state === "up" ? { value, at: at("19:10") } : null, mail: mail(), now: NOW,
    today: TODAY, cache: true, launcher: false, waitedMs: 0, at: { piece: "none" }, ...fields,
  }, CLOCK);
}

test("labels count names", () => {
  assert.equal(agentsLabel(1), "1 agent");
  assert.equal(agentsLabel(6), "6 agents");
});

test("before the roster's first answer: nothing, then Reading… after a while; a failure says so", () => {
  const loading = { loading: true, gone: false, failure: null, value: null };
  assert.deepEqual([frame({ roster: loading, waitedMs: 100 }).state, frame({ roster: loading, waitedMs: 100 }).reading],
                   ["loading", null]);
  assert.equal(frame({ roster: loading, waitedMs: 400 }).reading, READING);
  const failed = frame({ roster: { ...loading, loading: false, failure: { status: 0, why: "no answer from the shell" } },
                        waitedMs: 5000 });
  assert.equal(failed.reading, null);
  assert.deepEqual(failed.lines, [{ key: "roster", text: "The agents can't be read: no answer from the shell." }]);
});

test("a roster that went away draws nothing", () => {
  assert.equal(frame({ roster: { loading: false, gone: true, failure: null, value: null } }).state, "gone");
});

test("up: the label, one row per name, the foot; ops-watch has nothing and is no row", () => {
  const got = frame();
  assert.equal(got.state, "drawn");
  assert.equal(got.down, null);
  assert.equal(got.label, "4 agents");
  assert.deepEqual(got.rows.map((item) => item.row.name), ["builder-2", "docs-writer", "builder-1", "planner"]);
  assert.deepEqual(got.others.map((other) => other.qualified), ["ops-watch"]);
  assert.equal(got.rows[0].note, "builder-2@kestrel-web · 1 message since 16:30");
  assert.equal(got.foot, `${ANSWERING} · read 19:10`);
  assert.deepEqual(got.lines, []);
  assert.ok(got.rows.every((item) => !item.open && item.to === null));
});

test("offline: the first row, the last read, the last rows dim with no acts, nothing open", () => {
  const value = { state: "offline", detail: null, rows: [], waiting: [], today: null };
  const got = frame({ roster: read(value), kept: { value: CAST, at: at("19:04") }, at: { piece: "agent", qualified: "planner" } });
  assert.deepEqual(got.down, { what: DOWN_WHAT, line: DOWN_LINE, copy: DOCTOR });
  assert.equal(`${DOCTOR}${DOWN_LINE}`, "moxi doctor comms says why. Nothing can be sent until it answers.");
  assert.equal(got.lastRead, "Last read 19:04. It may have changed since.");
  assert.equal(got.stale, true);
  assert.equal(got.foot, null);
  assert.equal(got.rows.length, 4);
  for (const item of got.rows) {
    assert.deepEqual(item.row.acts, []);
    assert.equal(item.row.line.heavy, false);
    assert.equal(item.row.fact?.heavy ?? false, false);
    assert.equal(item.open, false);
  }
  assert.equal(got.opened.kind, "none");
  // Kept for when it answers again.
  assert.deepEqual(got.placed.opened, { kind: "agent", name: "planner", to: "planner", reply: null });
});

test("offline before any read answered: the first row alone", () => {
  const got = frame({ roster: read({ state: "offline", detail: null, rows: [], waiting: [], today: null }), kept: null });
  assert.equal(got.down.what, DOWN_WHAT);
  assert.equal(got.lastRead, null);
  assert.deepEqual(got.rows, []);
  assert.equal(got.label, null);
  assert.equal(got.empty, null);
});

test("error: its sentence in the first row's place, or the server's when it gives none", () => {
  const error = (detail) => frame({ roster: read({ state: "error", detail, rows: [], waiting: [], today: null }),
                                    kept: { value: CAST, at: at("19:04") } });
  assert.deepEqual(error("comms' daemon answered an error").down, { what: "comms' daemon answered an error", line: null, copy: null });
  assert.equal(error(null).down.what, SERVER_ERROR);
  assert.equal(error(null).stale, true);
});

test("empty: the sentence, with launcher a way to Home, without it the command", () => {
  const none = roster([]);
  const withLauncher = frame({ rosterValue: none, launcher: true }).empty;
  assert.equal(withLauncher.line, NO_AGENTS);
  assert.match(withLauncher.sentence, /^An agent is a name that one session holds at a time/);
  assert.deepEqual([withLauncher.launch, withLauncher.command], [true, null]);
  assert.deepEqual([frame({ rosterValue: none }).empty.launch, frame({ rosterValue: none }).empty.command], [false, NAME_COMMAND]);
  assert.equal(frame({ rosterValue: roster([row("ops-watch")]) }).empty, null);
  // An agent that asked him and whose session ended is a row, so the page isn't empty.
  const asked = frame({ rosterValue: none, mail: mail({ owed: [owed("planner", "19:02", "7")] }) });
  assert.equal(asked.empty, null);
  assert.equal(asked.label, "1 agent");
});

test("mail to the operator alone is no row and leaves the page empty", () => {
  const value = roster([], [{ qualified: "operator", count: 1, since: at("17:40") }]);
  const f = frame({ rosterValue: value });
  assert.equal(f.rows.length, 0);
  assert.notEqual(f.empty, null);
});

test("a mail read that failed or answered error is a line; a 404 is none", () => {
  assert.deepEqual(frame({ mail: read(null, { failure: { status: 502, why: "comms' page server answered 502" } }) }).lines,
                   [{ key: "mail", text: "The agents' mail can't be read: comms' page server answered 502." }]);
  assert.deepEqual(frame({ mail: mail({ state: "error", detail: "The store can't be read." }) }).lines,
                   [{ key: "mail", text: "The agents' mail can't be read: The store can't be read." }]);
  assert.deepEqual(frame({ mail: read(null, { gone: true }) }).lines, []);
});

test("agent/<address> opens its name's row written to that address", () => {
  const lead = frame({ at: { piece: "agent", qualified: "planner" } });
  const planner = lead.rows.find((item) => item.row.name === "planner");
  assert.deepEqual([planner.open, planner.to], [true, "planner"]);
  assert.equal(lead.rows.filter((item) => item.open).length, 1);
  const line = frame({ at: { piece: "agent", qualified: "builder-2@kestrel-web" } });
  const b2 = line.rows.find((item) => item.row.name === "builder-2");
  assert.deepEqual([b2.open, b2.to], [true, "builder-2@kestrel-web"]);
});

test("agent/<address> of Other names opens it there; an address the page doesn't know is a line", () => {
  assert.deepEqual(frame({ at: { piece: "agent", qualified: "ops-watch" } }).opened, { kind: "other", qualified: "ops-watch" });
  const gone = frame({ at: { piece: "agent", qualified: "nobody@x" } });
  assert.deepEqual(gone.opened, { kind: "none" });
  assert.deepEqual(gone.lines, [{ key: "address", text: "nobody@x isn't on this page." }]);
  assert.ok(gone.rows.every((item) => !item.open));
  assert.deepEqual(frame({ at: { piece: "mail", qualified: "nobody" } }).lines.map((l) => l.key), ["address"]);
  assert.deepEqual(frame({ at: { piece: "unknown" } }).lines, [{ key: "address", text: NO_SUCH_VIEW }]);
});

test("mail/<address> opens under its row; thread and others are handed on", () => {
  const got = frame({ at: { piece: "mail", qualified: "planner" } });
  assert.deepEqual(got.opened, { kind: "mail", name: "planner", qualified: "planner" });
  assert.equal(got.rows.find((item) => item.row.name === "planner").to, "planner");
  assert.deepEqual(frame({ at: { piece: "thread", a: "builder-1", b: "planner" } }).opened,
                   { kind: "thread", a: "builder-1", b: "planner" });
  assert.deepEqual(frame({ at: { piece: "others" } }).opened, { kind: "others" });
});

test("reply/<id> opens the asker's row, written to the asker, set to answer it", () => {
  const ask = owed("planner@atlas", "19:02", "1000000000000000007");
  const value = roster([bound("planner")]);
  const got = frame({ rosterValue: value, mail: mail({ owed: [ask] }), at: { piece: "reply", message_id: ask.message_id } });
  assert.deepEqual(got.opened, { kind: "agent", name: "planner", to: "planner@atlas", reply: ask });
  assert.equal(got.rows[0].to, "planner@atlas");
  // Until the mail state has answered, it waits; nothing is said.
  const waiting = frame({ rosterValue: value, mail: { loading: true, gone: false, failure: null, value: null },
                          at: { piece: "reply", message_id: ask.message_id } });
  assert.deepEqual([waiting.opened, waiting.placed, waiting.lines], [{ kind: "pending" }, null, []]);
});

test("reply/<id> no longer owed: the line, and the author's row when the page knows the author", () => {
  const at_ = { piece: "reply", message_id: "1000000000000000009" };
  const unknown = frame({ at: at_ });
  assert.deepEqual(unknown.opened, { kind: "none" });
  assert.deepEqual(unknown.lines, [{ key: "address", text: NO_LONGER_OWED }]);
  const known = frame({ at: at_, authorOf: (id) => (id === at_.message_id ? "planner" : null) });
  assert.deepEqual(known.opened, { kind: "agent", name: "planner", to: "planner", reply: null });
  assert.deepEqual(known.lines.map((l) => l.text), [NO_LONGER_OWED]);
});

test("reply/<id> no longer owed waits for today's messages before it places the author's row", () => {
  const at_ = { piece: "reply", message_id: "1000000000000000009" };
  const waiting = frame({ at: at_, messagesRead: false, authorOf: () => null });
  assert.deepEqual([waiting.opened, waiting.placed, waiting.lines], [{ kind: "pending" }, null, []]);
  const read_ = frame({ at: at_, messagesRead: true, authorOf: () => "planner" });
  assert.equal(read_.opened.kind, "agent");
});

test("a placement kept stays open after the ask it answered is gone", () => {
  const ask = owed("planner", "19:02", "1000000000000000007");
  const first = frame({ mail: mail({ owed: [ask] }), at: { piece: "reply", message_id: ask.message_id } });
  const later = frame({ mail: mail(), at: { piece: "reply", message_id: ask.message_id }, placed: first.placed });
  assert.deepEqual(later.opened, first.opened);
  assert.deepEqual(later.lines, []);
});
