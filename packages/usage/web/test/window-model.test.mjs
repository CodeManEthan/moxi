import assert from "node:assert/strict";
import test from "node:test";

import { openedLimit, openedRow, readingKey, windowPath } from "../src/window-model.ts";

/** UTC, 24-hour: the same words in any zone. */
const CLOCK = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;
const NOW = at("2026-11-08T19:10:00Z");

const answer = (extra = {}) => ({
  now: NOW, today: "2026-11-08", harness: "claude", limit_id: "plan", window_minutes: 300,
  resets_at: "2026-11-08T21:34:00Z", used_percent: 49.4, opened: "2026-11-08T16:34:00Z",
  readings: { count: 5, first: { at: "2026-11-08T17:45:00Z", used_percent: 22.0 },
              last: { at: "2026-11-08T19:08:00Z", used_percent: 49.4 } },
  used: { since: "2026-11-08T16:34:00Z", count: 6, requests: 63, cost: 6.95, complete: true,
          rows: [{ harness: "claude", session_id: "s1", requests: 18, cost: 1.74 },
                 { harness: "claude", session_id: "s2", requests: 1, cost: null }] },
  ...extra,
});
const row = (extra = {}) => ({ full_at: null, projection: null, note: null, ...extra });
const opened = (window, extra = {}) =>
  openedLimit({ window, loading: false, row: row(), now: NOW, ...extra }, CLOCK);
const line = (model, key) => model.lines.find((found) => found.key === key) ?? null;

test("an opened row says when its window opened and resets, and how much is gone and used", () => {
  const model = opened(answer());
  assert.equal(model.close, "Close");
  assert.equal(model.reading, null);
  assert.deepEqual(line(model, "window"),
    { key: "window", label: "Window", text: "opened 16:34, resets 21:34", dim: "52% of it gone, 49% used" });
});

test("a window whose start is not known says its reset and its use, and nothing of how much is gone", () => {
  assert.deepEqual(line(opened(answer({ opened: null })), "window"),
    { key: "window", label: "Window", text: "resets 21:34", dim: "49% used" });
});

test("a weekly window names its days, and today only beside another day", () => {
  const weekly = answer({ window_minutes: 10080, opened: "2026-11-04T23:58:00Z", resets_at: "2026-11-11T23:58:00Z" });
  assert.equal(line(opened(weekly), "window").text, "opened Wed 23:58, resets Wed 23:58");
  const fromToday = answer({ opened: "2026-11-08T22:00:00Z", resets_at: "2026-11-09T03:00:00Z" });
  assert.equal(line(opened(fromToday), "window").text, "opened today 22:00, resets tomorrow 03:00");
});

test("percents are rounded down and a share under one is said so", () => {
  const model = opened(answer({ used_percent: 0.4, opened: "2026-11-08T19:08:00Z", resets_at: "2026-11-09T00:08:00Z",
    readings: { count: 1, first: null, last: { at: "2026-11-08T19:08:00Z", used_percent: 0.4 } } }));
  assert.equal(line(model, "window").dim, "under 1% of it gone, under 1% used");
  assert.equal(line(model, "readings").text, "under 1% at 19:08");
  assert.equal(line(opened(answer({ used_percent: 99.7 })), "window").dim, "52% of it gone, 99% used");
});

test("the readings are the period's first and the row's own; no session is named", () => {
  assert.deepEqual(line(opened(answer()), "readings"),
    { key: "readings", label: "Readings", text: "22% at 17:45, 49% at 19:08", dim: null });
});

test("the readings end with the session the last one came from, by its name", () => {
  const last = { at: "2026-11-08T19:08:00Z", used_percent: 49.4,
                 session: { harness: "claude", session_id: "s1", name: "planner", folder: "/work/proj-a",
                            started: at("2026-11-08T12:45:00Z"), known: true } };
  const named = answer({ readings: { count: 5, first: { at: "2026-11-08T17:45:00Z", used_percent: 22.0 }, last } });
  const name = (ref) => ref.name;
  assert.deepEqual(line(opened(named, { name }), "readings"),
    { key: "readings", label: "Readings", text: "22% at 17:45, 49% at 19:08", dim: "the last from planner" });
  // A session the page has no name for, and a reading that names none, add nothing.
  assert.equal(line(opened(named, { name: () => null }), "readings").dim, null);
  assert.equal(line(opened(named), "readings").dim, null);
  const none = answer({ readings: { count: 1, first: null, last: { ...last, session: null } } });
  assert.equal(line(opened(none, { name }), "readings").dim, null);
});

test("one reading stands alone, and a first on another day names both days", () => {
  const single = answer({ readings: { count: 1, first: null, last: { at: "2026-11-08T19:08:00Z", used_percent: 49.4 } } });
  assert.equal(line(opened(single), "readings").text, "49% at 19:08");
  const days = answer({ readings: { count: 9, first: { at: "2026-11-05T08:00:00Z", used_percent: 3 },
                                    last: { at: "2026-11-08T19:08:00Z", used_percent: 60 } } });
  assert.equal(line(opened(days), "readings").text, "3% at Thu 08:00, 60% at today 19:08");
});

test("a reading with no percent is left out, and the line is absent with none or with no readings", () => {
  const firstBlank = answer({ readings: { count: 2, first: { at: "2026-11-08T17:45:00Z", used_percent: null },
                                          last: { at: "2026-11-08T19:08:00Z", used_percent: 49.4 } } });
  assert.equal(line(opened(firstBlank), "readings").text, "49% at 19:08");
  const blank = answer({ readings: { count: 1, first: null, last: { at: "2026-11-08T19:08:00Z", used_percent: null } } });
  assert.equal(line(opened(blank), "readings"), null);
  assert.equal(line(opened(answer({ readings: null })), "readings"), null);
});

test("who used it: the count since the window opened and its cost; a session with no name has no line", () => {
  const model = opened(answer());
  assert.deepEqual(model.used, { label: "Used by", rows: [], total: "6 sessions, 63 requests since 16:34", cost: "$6.95" });
  const text = JSON.stringify(model);
  assert.equal(text.includes("s1") || text.includes("s2"), false);
});

test("sessions that have a name are listed with their requests and cost", () => {
  const name = (used) => (used.session_id === "s1" ? "planner" : used.session_id === "s2" ? "proj-a · started 16:27" : null);
  const model = opened(answer(), { name });
  assert.deepEqual(model.used.rows, [
    { name: "planner", requests: "18 requests", cost: "$1.74" },
    { name: "proj-a · started 16:27", requests: "1 request", cost: "no price" },
  ]);
  const some = opened(answer(), { name: (used) => (used.session_id === "s2" ? "reviewer" : null) });
  assert.deepEqual(some.used.rows.map((found) => found.name), ["reviewer"]);
});

test("a total that is not whole says at least, one with no price says so, and one session is one", () => {
  const partial = opened(answer({ used: { since: "2026-11-08T16:34:00Z", count: 1, requests: 1, cost: 0.5,
                                          complete: false, rows: [] } }));
  assert.deepEqual([partial.used.total, partial.used.cost], ["1 session, 1 request since 16:34", "at least $0.50"]);
  const unpriced = opened(answer({ used: { since: "2026-11-08T16:34:00Z", count: 2, requests: 3, cost: null,
                                           complete: false, rows: [] } }));
  assert.equal(unpriced.used.cost, "no price");
  const none = opened(answer({ used: { since: "2026-11-08T16:34:00Z", count: 0, requests: 0, cost: 0,
                                       complete: true, rows: [] } }));
  assert.deepEqual([none.used.total, none.used.cost], ["0 sessions, 0 requests since 16:34", "$0.00"]);
});

test("a limit with no report of who used it has no such line", () => {
  assert.equal(opened(answer({ used: null })).used, null);
});

test("each line is absent when its fact is: a window with only a reset still says it", () => {
  const model = opened(answer({ opened: null, readings: null, used: null }));
  assert.deepEqual(model.lines.map((found) => found.key), ["window"]);
  assert.equal(model.used, null);
  assert.deepEqual(model.notes, []);
});

test("while the first read is out the row says it is reading; a read that failed says nothing here", () => {
  const waiting = openedLimit({ window: null, loading: true, row: row(), now: NOW }, CLOCK);
  assert.deepEqual(waiting, { close: "Close", reading: "Reading…", lines: [], used: null, notes: [] });
  const failed = openedLimit({ window: null, loading: false, row: row(), now: NOW }, CLOCK);
  assert.deepEqual(failed, { close: "Close", reading: null, lines: [], used: null, notes: [] });
});

test("the row's own lines: why it has no fill time, and its reading's note", () => {
  const why = "1 reading of this period in the last 60 minutes; a projection needs 3";
  const model = opened(answer(), { row: row({ projection: why, note: "no newer reading: the rollout is older" }) });
  assert.deepEqual(model.notes, [`No fill time: ${why}`, "no newer reading: the rollout is older"]);
  const filling = opened(answer(), { row: row({ full_at: "2026-11-08T20:05:00Z", projection: "fitted to 3 readings" }) });
  assert.deepEqual(filling.notes, []);
  assert.deepEqual(opened(answer(), { row: null }).notes, []);
  const early = openedLimit({ window: null, loading: true, row: row({ projection: why }), now: NOW }, CLOCK);
  assert.deepEqual(early.notes, [`No fill time: ${why}`]);
});

const fresh = (extra = {}) => ({ harness: "claude", limit_id: "plan", window_minutes: 300, state: "fresh",
  observed_at: "2026-11-08T19:08:00Z", used_percent: 49.4, resets_at: "2026-11-08T21:34:00Z", ...extra });
const AT = { harness: "claude", limit: "plan", minutes: 300 };

test("an opened row stands under the fresh row of its window, or is closed", () => {
  const rows = [fresh({ window_minutes: 10080 }), fresh({ harness: "codex", limit_id: "codex" }), fresh()];
  assert.equal(openedRow(rows, AT), rows[2]);
  assert.equal(openedRow([fresh({ state: "reset" })], AT), null);
  assert.equal(openedRow([fresh({ state: "unavailable" })], AT), null);
  assert.equal(openedRow([fresh({ limit_id: "opus" })], AT), null);
  assert.equal(openedRow([], AT), null);
});

test("the window is read again when its row's reading, percent or reset changes, and not otherwise", () => {
  const key = readingKey(fresh());
  assert.equal(readingKey(fresh({ full_at: "2026-11-08T20:00:00Z", note: "x" })), key);
  assert.notEqual(readingKey(fresh({ observed_at: "2026-11-08T19:09:00Z" })), key);
  assert.notEqual(readingKey(fresh({ used_percent: 50 })), key);
  assert.notEqual(readingKey(fresh({ resets_at: "2026-11-08T21:35:00Z" })), key);
});

test("the window's read names its harness, limit and length", () => {
  assert.equal(windowPath(AT), "api/window?harness=claude&limit_id=plan&minutes=300");
  assert.equal(windowPath({ harness: "codex", limit: "a b/c", minutes: 10080 }),
    "api/window?harness=codex&limit_id=a+b%2Fc&minutes=10080");
});
