import assert from "node:assert/strict";
import test from "node:test";

import {
  addDays, dateWord, dayPart, dayWord, daysFrom, daysOf, isDay, LOCAL, monthWord, seconds, whenWord,
} from "../src/days.ts";

/** A clock that reads every moment in UTC, on 24 hours. */
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;
const TODAY = "2026-11-08";    // a Sunday

test("a time on today's date stands alone", () => {
  assert.equal(whenWord(at("2026-11-08T21:34:00Z"), TODAY, UTC), "21:34");
  assert.equal(whenWord(at("2026-11-08T00:00:00Z"), TODAY, UTC), "00:00");
  assert.equal(whenWord(at("2026-11-08T23:59:59Z"), TODAY, UTC), "23:59");
});

test("each side of midnight is yesterday and tomorrow", () => {
  assert.equal(whenWord(at("2026-11-07T23:59:59Z"), TODAY, UTC), "yesterday 23:59");
  assert.equal(whenWord(at("2026-11-07T00:00:00Z"), TODAY, UTC), "yesterday 00:00");
  assert.equal(whenWord(at("2026-11-09T00:00:00Z"), TODAY, UTC), "tomorrow 00:00");
  assert.equal(whenWord(at("2026-11-09T17:51:00Z"), TODAY, UTC), "tomorrow 17:51");
});

test("within six days either side a day is its weekday, and past that its date", () => {
  assert.equal(whenWord(at("2026-11-10T22:29:00Z"), TODAY, UTC), "Tue 22:29");
  assert.equal(whenWord(at("2026-11-11T23:58:00Z"), TODAY, UTC), "Wed 23:58");
  assert.equal(whenWord(at("2026-11-14T08:00:00Z"), TODAY, UTC), "Sat 08:00");
  assert.equal(whenWord(at("2026-11-15T08:00:00Z"), TODAY, UTC), "Nov 15 08:00");
  assert.equal(whenWord(at("2026-11-06T08:00:00Z"), TODAY, UTC), "Fri 08:00");
  assert.equal(whenWord(at("2026-11-02T08:00:00Z"), TODAY, UTC), "Mon 08:00");
  assert.equal(whenWord(at("2026-11-01T08:52:00Z"), TODAY, UTC), "Nov 1 08:52");
  assert.equal(whenWord(at("2025-11-03T08:52:00Z"), TODAY, UTC), "Nov 3 08:52");
});

test("the day turns with the server's today, not with the moment", () => {
  const moment = at("2026-11-08T23:30:00Z");
  assert.equal(whenWord(moment, "2026-11-08", UTC), "23:30");
  assert.equal(whenWord(moment, "2026-11-09", UTC), "yesterday 23:30");
  assert.equal(whenWord(moment, "2026-11-07", UTC), "tomorrow 23:30");
});

test("the month and the year turn like any day", () => {
  assert.equal(whenWord(at("2026-10-31T23:00:00Z"), "2026-11-01", UTC), "yesterday 23:00");
  assert.equal(whenWord(at("2027-01-01T01:00:00Z"), "2026-12-31", UTC), "tomorrow 01:00");
  assert.equal(whenWord(at("2026-12-28T01:00:00Z"), "2027-01-02", UTC), "Mon 01:00");
});

test("today is named only when asked, for a line that holds another day", () => {
  assert.equal(whenWord(at("2026-11-08T11:00:00Z"), TODAY, UTC, true), "today 11:00");
  assert.equal(whenWord(at("2026-11-07T11:00:00Z"), TODAY, UTC, true), "yesterday 11:00");
  assert.equal(dayPart(at("2026-11-08T11:00:00Z"), TODAY, UTC), null);
  assert.equal(dayPart(at("2026-11-09T11:00:00Z"), TODAY, UTC), "tomorrow");
});

test("with no today held, a moment is its time and no day is said", () => {
  assert.equal(whenWord(at("2026-11-03T08:52:00Z"), null, UTC), "08:52");
  assert.equal(whenWord(at("2026-11-03T08:52:00Z"), null, UTC, true), "08:52");
  assert.equal(dayPart(at("2026-11-03T08:52:00Z"), null, UTC), null);
});

test("a day against today, and a date alone", () => {
  assert.equal(dayWord("2026-11-08", TODAY), "today");
  assert.equal(dayWord("2026-11-07", TODAY), "yesterday");
  assert.equal(dayWord("2026-11-09", TODAY), "tomorrow");
  assert.equal(dayWord("2026-11-04", TODAY), "Wed");
  assert.equal(dayWord("2026-11-01", TODAY), "Nov 1");
  assert.equal(dayWord("2026-10-10", TODAY), "Oct 10");
  assert.equal(dateWord("2026-10-10"), "Oct 10");
  assert.equal(dateWord("2026-01-01"), "Jan 1");
  assert.equal(dateWord("not a day"), "not a day");
  assert.equal(monthWord("2026-10"), "Oct 2026");
  assert.equal(monthWord("2026-13"), "2026-13");
});

test("days are counted and stepped by the calendar", () => {
  assert.equal(addDays("2026-11-01", -1), "2026-10-31");
  assert.equal(addDays("2028-02-28", 1), "2028-02-29");
  assert.equal(daysFrom("2026-10-10", "2026-11-08"), 29);
  assert.equal(daysFrom("2026-11-08", "2026-11-07"), -1);
  assert.deepEqual(daysOf("2026-10-30", "2026-11-01"), ["2026-10-30", "2026-10-31", "2026-11-01"]);
  assert.deepEqual(daysOf("2026-11-02", "2026-11-01"), []);
});

test("an ISO time is epoch seconds, and nothing else is a time", () => {
  assert.equal(seconds("2026-11-08T21:34:00Z"), 1794173640);
  assert.equal(seconds(null), null);
  assert.equal(seconds("soon"), null);
});

test("the page's own clock is this browser's zone and ui's way of writing a time", () => {
  const moment = at("2026-11-08T21:34:00Z");
  const d = new Date(moment * 1000);
  const two = (n) => String(n).padStart(2, "0");
  assert.equal(LOCAL.day(moment), `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`);
  assert.equal(LOCAL.time(moment), d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
});

test("a value that is not a day can't be moved: no day is said, and nothing is thrown", () => {
  for (const not of ["", "today", "2026-11", "2026-11-08T00:00:00Z", "2026-13-40"]) {
    assert.equal(isDay(not), false, not);
    assert.equal(addDays(not, -29), null, not);
    assert.deepEqual(daysOf(not, TODAY), [], not);
    assert.deepEqual(daysOf(TODAY, not), [], not);
  }
  assert.equal(isDay(TODAY), true);
  assert.equal(isDay(null), false);
  assert.equal(addDays(TODAY, -29), "2026-10-10");
});
