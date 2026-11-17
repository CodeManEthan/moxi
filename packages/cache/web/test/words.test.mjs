import assert from "node:assert/strict";
import test from "node:test";

import { dayWord, folderWords, harnessWords, isDay, lifetimeWords, newestDay, sessionName, spanWords,
         whenWord } from "../src/words.ts";

// A clock in UTC, so the words are the same in any zone and locale.
const iso = (at) => new Date(at * 1000).toISOString();
const UTC = { time: (at) => iso(at).slice(11, 16), seconds: (at) => iso(at).slice(11, 19), day: (at) => iso(at).slice(0, 10) };
const at = (text) => Date.parse(`${text}Z`) / 1000;
const TODAY = "2026-11-10";

test("a day against today, each side of it", () => {
  assert.equal(dayWord("2026-11-10", TODAY), "today");
  assert.equal(dayWord("2026-11-09", TODAY), "yesterday");
  assert.equal(dayWord("2026-11-11", TODAY), "tomorrow");
  assert.equal(dayWord("2026-11-04", TODAY), "Wed");
  assert.equal(dayWord("2026-11-16", TODAY), "Mon");
  assert.equal(dayWord("2026-11-03", TODAY), "Nov 3");
  assert.equal(dayWord("2026-12-01", TODAY), "Dec 1");
  assert.equal(dayWord("2026-01-01", "2026-12-31"), "Jan 1");
  assert.equal(dayWord("2026-12-31", "2027-01-01"), "yesterday");
});

test("a time each side of midnight", () => {
  assert.equal(whenWord(at("2026-11-10T23:59:30"), TODAY, UTC), "23:59");
  assert.equal(whenWord(at("2026-11-11T00:00:00"), TODAY, UTC), "tomorrow 00:00");
  assert.equal(whenWord(at("2026-11-09T23:59:59"), TODAY, UTC), "yesterday 23:59");
  assert.equal(whenWord(at("2026-11-10T00:00:00"), TODAY, UTC), "00:00");
  assert.equal(whenWord(at("2026-11-11T00:00:00"), "2026-11-11", UTC), "00:00");
  assert.equal(whenWord(at("2026-11-10T19:52:10"), TODAY, UTC, { exact: true }), "19:52:10");
  assert.equal(whenWord(at("2026-11-09T19:52:10"), TODAY, UTC, { exact: true }), "yesterday 19:52:10");
  assert.equal(whenWord(at("2026-11-10T11:00:00"), TODAY, UTC, { named: true }), "today 11:00");
  assert.equal(whenWord(at("2026-11-06T22:10:00"), TODAY, UTC, { named: true }), "Fri 22:10");
  // with no today held, no day can be said
  assert.equal(whenWord(at("2026-11-09T23:59:59"), null, UTC), "23:59");
});

test("the newest today held", () => {
  assert.equal(newestDay("2026-11-10", "2026-11-11"), "2026-11-11");
  assert.equal(newestDay(null, "2026-11-10", undefined), "2026-11-10");
  assert.equal(newestDay("not a day", null), null);
  assert.equal(isDay("2026-11-10"), true);
  assert.equal(isDay("2026-11-10T00:00"), false);
});

test("a span each side of a minute, an hour and a day", () => {
  assert.equal(spanWords(0), "0 seconds");
  assert.equal(spanWords(1), "1 second");
  assert.equal(spanWords(59.9), "59 seconds");
  assert.equal(spanWords(60), "1 minute");
  assert.equal(spanWords(119), "1 minute");
  assert.equal(spanWords(3599), "59 minutes");
  assert.equal(spanWords(3600), "1 hour");
  assert.equal(spanWords(3600 + 240), "1 hour 4 minutes");
  assert.equal(spanWords(7200 + 60), "2 hours 1 minute");
  assert.equal(spanWords(7200), "2 hours");
  assert.equal(spanWords(86399), "23 hours 59 minutes");
  assert.equal(spanWords(86400), "1 day");
  assert.equal(spanWords(3 * 86400 + 5000), "3 days");
  assert.equal(spanWords(-5), "0 seconds");
});

test("a lifetime in words", () => {
  assert.equal(lifetimeWords(3600), "one hour after the last request");
  assert.equal(lifetimeWords(300), "five minutes after the last request");
  assert.equal(lifetimeWords(60), "one minute after the last request");
  assert.equal(lifetimeWords(1800), "30 minutes after the last request");
  assert.equal(lifetimeWords(7200), "120 minutes after the last request");
  assert.equal(lifetimeWords(90), "90 seconds after the last request");
  assert.equal(lifetimeWords(299.5), "299.5 seconds after the last request");
  assert.equal(lifetimeWords(1), "1 second after the last request");
});

test("a session's name: typed, folder and start, and each null part", () => {
  const ref = (extra) => ({ harness: "claude", name: null, folder: null, started: null, ...extra });
  const started = at("2026-11-10T16:27:00");
  assert.deepEqual(sessionName(ref({ name: "orders-skeleton", folder: "/work/kestrel-web/" }), TODAY, UTC),
                   { name: "orders-skeleton", where: "kestrel-web" });
  assert.deepEqual(sessionName(ref({ name: "  two\n words ", folder: null }), TODAY, UTC), { name: "two words", where: null });
  assert.deepEqual(sessionName(ref({ folder: "/work/orbit-cli", started }), TODAY, UTC),
                   { name: "orbit-cli · started today 16:27", where: null });
  assert.deepEqual(sessionName(ref({ folder: "/work/orbit-cli", started: at("2026-11-09T23:10:00") }), TODAY, UTC),
                   { name: "orbit-cli · started yesterday 23:10", where: null });
  assert.deepEqual(sessionName(ref({ folder: "/work/orbit-cli" }), TODAY, UTC), { name: "orbit-cli", where: null });
  assert.deepEqual(sessionName(ref({ name: " ", folder: "/work/orbit-cli" }), TODAY, UTC), { name: "orbit-cli", where: null });
  // with no folder, the harness in its place
  const fallback = `${harnessWords("claude")} session`;
  assert.deepEqual(sessionName(ref({ started }), TODAY, UTC), { name: `${fallback} · started today 16:27`, where: null });
  assert.deepEqual(sessionName(ref({}), TODAY, UTC), { name: fallback, where: null });
  assert.equal(sessionName(ref({ harness: "codex" }), TODAY, UTC).name, `${harnessWords("codex")} session`);
});

test("harness words and a folder under home", () => {
  assert.equal(harnessWords("claude"), "Claude Code");
  assert.equal(harnessWords("opencode"), "OpenCode");
  assert.equal(harnessWords("toString"), "toString");
  assert.equal(folderWords("/home/alice/code/proj-a", "/home/alice"), "~/code/proj-a");
  assert.equal(folderWords("/home/alice", "/home/alice/"), "~");
  assert.equal(folderWords("/home/alicex/proj-a", "/home/alice"), "/home/alicex/proj-a");
  assert.equal(folderWords("/srv/proj-a", null), "/srv/proj-a");
});
