import assert from "node:assert/strict";
import test from "node:test";

import { dateWord, dayWord, homeForm, kindPhrase, kindWord, lastActive, whenWord } from "../src/words.ts";

/** The clock in UTC, so the words are the same in any zone. */
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;

test("a day against today", () => {
  const today = "2026-11-15"; // a Sunday
  assert.equal(dayWord("2026-11-15", today), "today");
  assert.equal(dayWord("2026-11-14", today), "yesterday");
  assert.equal(dayWord("2026-11-16", today), "tomorrow");
  assert.equal(dayWord("2026-11-13", today), "Fri");
  assert.equal(dayWord("2026-11-09", today), "Mon");
  assert.equal(dayWord("2026-11-08", today), "Nov 8");
  assert.equal(dayWord("2026-10-10", today), "Oct 10");
  assert.equal(dayWord("2026-11-21", today), "Sat");
  assert.equal(dayWord("2026-11-22", today), "Nov 22");
  assert.equal(dayWord("soon", today), "soon");
  assert.equal(dateWord("2025-12-30"), "Dec 30");
});

test("a moment on today's date is its time alone, any other with its day", () => {
  const now = at("2026-11-15T19:30:00Z");
  assert.equal(whenWord(at("2026-11-15T19:09:00Z"), now, UTC), "19:09");
  assert.equal(whenWord(at("2026-11-14T22:10:00Z"), now, UTC), "yesterday 22:10");
  assert.equal(whenWord(at("2026-11-13T22:10:00Z"), now, UTC), "Fri 22:10");
  assert.equal(whenWord(at("2026-10-10T08:52:00Z"), now, UTC), "Oct 10 08:52");
  assert.equal(lastActive(at("2026-11-15T19:09:00Z"), now, UTC), "last active 19:09");
  assert.equal(lastActive(at("2026-11-13T22:10:00Z"), now, UTC), "last active Fri 22:10");
});

test("day words each side of midnight", () => {
  const justAfter = at("2026-11-15T00:01:00Z");
  assert.equal(whenWord(at("2026-11-14T23:59:00Z"), justAfter, UTC), "yesterday 23:59");
  assert.equal(whenWord(at("2026-11-15T00:00:00Z"), justAfter, UTC), "00:00");
  const justBefore = at("2026-11-14T23:59:00Z");
  assert.equal(whenWord(at("2026-11-14T00:00:00Z"), justBefore, UTC), "00:00");
  assert.equal(whenWord(at("2026-11-13T23:59:00Z"), justBefore, UTC), "yesterday 23:59");
  assert.equal(whenWord(at("2026-11-15T00:00:00Z"), justBefore, UTC), "tomorrow 00:00",
               "a clock a minute ahead of the browser's");
});

test("the browser's own zone decides the day", () => {
  const zoned = { time: (t) => UTC.time(t + 3600), day: (t) => UTC.day(t + 3600) };
  const now = at("2026-11-15T00:30:00Z"); // 01:30 on the 15th, an hour east
  assert.equal(whenWord(at("2026-11-14T23:10:00Z"), now, zoned), "00:10");
  assert.equal(whenWord(at("2026-11-14T23:10:00Z"), now, UTC), "yesterday 23:10");
});

test("a root in ~ form under the home, in full elsewhere", () => {
  const home = "/home/me";
  assert.equal(homeForm("/home/me", home), "~");
  assert.equal(homeForm("/home/me/", home), "~");
  assert.equal(homeForm("/home/me/code/atlas-api", home), "~/code/atlas-api");
  assert.equal(homeForm("/home/me2", home), "/home/me2");
  assert.equal(homeForm("/home/me2/code", home), "/home/me2/code");
  assert.equal(homeForm("/home/ME/code", home), "/home/ME/code", "a POSIX home is compared with case");
  assert.equal(homeForm("/srv/code", home), "/srv/code");
  assert.equal(homeForm("/home", home), "/home");
  assert.equal(homeForm("/home/me/code", "/home/me/"), "~/code", "a home with a trailing separator");
  assert.equal(homeForm("/home/me\\x", home), "/home/me\\x", "a backslash is no separator off Windows");
});

test("no home writes every root in full", () => {
  assert.equal(homeForm("/home/me/code", null), "/home/me/code");
  assert.equal(homeForm("/home/me/code", ""), "/home/me/code");
  assert.equal(homeForm("/x", "/"), "/x");
});

test("a Windows home is compared without case, either separator", () => {
  const home = "C:\\Users\\Me";
  assert.equal(homeForm("C:\\Users\\Me", home), "~");
  assert.equal(homeForm("c:\\users\\me\\code\\atlas", home), "~\\code\\atlas");
  assert.equal(homeForm("C:\\Users\\ME/code", home), "~/code");
  assert.equal(homeForm("C:\\Users\\Me2\\code", home), "C:\\Users\\Me2\\code");
  assert.equal(homeForm("D:\\Users\\Me\\code", home), "D:\\Users\\Me\\code");
  assert.equal(homeForm("\\\\srv\\home\\me\\code", "\\\\SRV\\home\\me"), "~\\code");
});

test("kind words", () => {
  assert.equal(kindWord("project"), null);
  assert.equal(kindWord("agent"), "agent's home");
  assert.equal(kindWord("topic"), "topic");
  assert.equal(kindPhrase("project"), "a code project");
  assert.equal(kindPhrase("agent"), "an agent's home");
  assert.equal(kindPhrase("topic"), "topic");
});
