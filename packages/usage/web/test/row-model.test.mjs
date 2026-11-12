import assert from "node:assert/strict";
import test from "node:test";

import { parseSummary, rowWhen, RUNS_OUT, usageLine } from "../src/row-model.ts";

/** A clock that reads every moment in UTC, on 24 hours. */
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const secs = (iso) => Date.parse(iso) / 1000;
/** Thursday 12 November 2026, 19:10 UTC. */
const NOW = secs("2026-11-12T19:10:00Z");

const window = (name, used, resets, full = null) => ({ window: name, used_percent: used, resets_at: resets, full_at: full });
const limits = (windows, harness = "claude") => ({ harness, observed_at: "2026-11-12T19:08:00Z", windows });
const cost = (dollars, complete = true) => ({ day: "2026-11-12", dollars, complete });
const line = (value) => usageLine(parseSummary({ now: NOW, limits: null, cost: null, ...value }), UTC);
const text = (found) => found.parts.map((part) => part.text).join("");

const FIVE = window("5h", 49.4, "2026-11-12T21:34:00Z");
const WEEK = window("7d", 60, "2026-11-18T23:58:00Z");

test("with both nulls there is no row", () => {
  assert.equal(line({}), null);
  assert.equal(usageLine(null, UTC), null);
});

test("limits with no early fill: both windows, the 5-hour reset, today's cost and when they were read", () => {
  const found = line({ limits: limits([FIVE, WEEK]), cost: cost(19.54) });
  assert.equal(text(found), "Claude 5-hour 49%, resets 21:34 · weekly 60% · today $19.54");
  assert.equal(found.time, "read 19:08");
  assert.equal(found.needs, undefined);
  assert.ok(found.parts.every((part) => !part.heavy));
});

test("a window that is not there is left out with its separator, and so is a missing cost", () => {
  assert.equal(text(line({ limits: limits([WEEK], "codex") })), "Codex weekly 60%");
  assert.equal(text(line({ limits: limits([FIVE]), cost: cost(2) })), "Claude 5-hour 49%, resets 21:34 · today $2.00");
  assert.equal(text(line({ limits: limits([FIVE, WEEK]) })), "Claude 5-hour 49%, resets 21:34 · weekly 60%");
});

test("the windows keep their order however the answer lists them", () => {
  assert.equal(text(line({ limits: limits([WEEK, FIVE]) })), "Claude 5-hour 49%, resets 21:34 · weekly 60%");
});

test("a window filling before its reset takes the row, heavy, and needs the user", () => {
  const filling = window("7d", 81, "2026-11-18T23:58:00Z", "2026-11-13T17:51:00Z");
  const found = line({ limits: limits([FIVE, filling]), cost: cost(19.54) });
  assert.deepEqual(found.parts, [
    { text: "Claude weekly limit fills tomorrow 17:51", heavy: true },
    { text: ", resets Wed 23:58" },
    { text: " · today $19.54" },
  ]);
  assert.equal(found.time, "read 19:08");
  assert.equal(found.needs, RUNS_OUT);
  assert.equal(RUNS_OUT, 40);
});

test("with two windows filling early the earlier fill leads and the other follows it plainly", () => {
  const five = window("5h", 90, "2026-11-12T21:34:00Z", "2026-11-12T20:40:00Z");
  const week = window("7d", 81, "2026-11-18T23:58:00Z", "2026-11-13T17:51:00Z");
  const found = line({ limits: limits([week, five]), cost: cost(19.54) });
  assert.deepEqual(found.parts, [
    { text: "Claude 5-hour limit fills 20:40", heavy: true },
    { text: ", resets 21:34" },
    { text: ", weekly fills tomorrow 17:51" },
    { text: " · today $19.54" },
  ]);
  const later = window("5h", 90, "2026-11-13T00:30:00Z", "2026-11-13T00:10:00Z");
  assert.equal(text(line({ limits: limits([later, week]) })),
               "Claude 5-hour limit fills tomorrow 00:10, resets tomorrow 00:30, weekly fills tomorrow 17:51");
});

test("the cost alone is today's cost with no time", () => {
  const found = line({ cost: cost(19.54) });
  assert.deepEqual(found, { parts: [{ text: "today $19.54" }] });
});

test("a cost with an unpriced model carries a plus", () => {
  assert.equal(text(line({ cost: cost(19.54, false) })), "today $19.54+");
  assert.equal(text(line({ limits: limits([WEEK]), cost: cost(0, false) })), "Claude weekly 60% · today $0.00+");
});

test("a time today is the time alone, tomorrow's says tomorrow, a later one its weekday", () => {
  assert.equal(rowWhen(secs("2026-11-12T23:59:00Z"), "2026-11-12", UTC), "23:59");
  assert.equal(rowWhen(secs("2026-11-13T00:01:00Z"), "2026-11-12", UTC), "tomorrow 00:01");
  assert.equal(rowWhen(secs("2026-11-14T08:00:00Z"), "2026-11-12", UTC), "Sat 08:00");
  assert.equal(rowWhen(secs("2026-11-19T18:00:00Z"), "2026-11-12", UTC), "Thu 18:00",
               "a weekly reset seven days on still reads as its weekday");
});

test("the day is the one the clock reads at the server's now, not at UTC", () => {
  // At 23:30 in a zone two hours ahead of UTC it is already the 13th: 00:30 UTC on the
  // 13th is that zone's 02:30 on the same day.
  const ahead = {
    time: (at) => new Date((at + 7200) * 1000).toISOString().slice(11, 16),
    day: (at) => new Date((at + 7200) * 1000).toISOString().slice(0, 10),
  };
  const now = secs("2026-11-12T22:30:00Z");
  const found = usageLine(parseSummary({
    now, cost: null,
    limits: { harness: "claude", observed_at: "2026-11-12T22:29:00Z",
              windows: [window("5h", 10, "2026-11-13T00:30:00Z")] },
  }), ahead);
  assert.equal(text(found), "Claude 5-hour 10%, resets 02:30");
  assert.equal(found.time, "read 00:29");
});

test("a reading from an earlier day says which day it was read", () => {
  const found = line({ limits: { ...limits([WEEK]), observed_at: "2026-11-11T23:10:00Z" } });
  assert.equal(found.time, "read yesterday 23:10");
});

test("a percent rounds down, and a share under one percent says so", () => {
  assert.equal(text(line({ limits: limits([window("7d", 0.4, "2026-11-18T23:58:00Z")]) })), "Claude weekly under 1%");
  assert.equal(text(line({ limits: limits([window("7d", 99.9, "2026-11-18T23:58:00Z")]) })), "Claude weekly 99%");
});

test("an answer the row cannot read is none, and a window it cannot say is left out", () => {
  assert.equal(parseSummary(null), null);
  assert.equal(parseSummary({ limits: null, cost: null }), null);
  const found = parseSummary({ now: NOW, cost: null, limits: limits([window("90m", 5, "2026-11-12T21:34:00Z"),
                                                                     window("5h", null, "2026-11-12T21:34:00Z")]) });
  assert.equal(found.limits, null);
  assert.equal(usageLine(found, UTC), null);
});
