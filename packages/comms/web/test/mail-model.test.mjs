import assert from "node:assert/strict";
import test from "node:test";

import { mailLine, namesWords, NAMED, parseMail, sinceWords, WAITS } from "../src/mail-model.ts";

// A clock in UTC that writes the time as `HH:MM`, so the words are the same in any zone.
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;
// Thursday, 12 November 2026, 16:00 UTC.
const NOW = at("2026-11-12T16:00:00Z");

const entry = (name, count, since, qualified = `${name}@proj-a`) => ({ qualified, name, count, since });
const up = (...waiting) => ({ state: "up", detail: null, waiting });
const line = (data) => mailLine(parseMail(data), NOW, UTC);

test("one name with one message: the singular, and isn't", () => {
  assert.deepEqual(line(up(entry("curator", 1, at("2026-11-12T14:02:00Z")))), {
    parts: [{ text: "1 message waiting", heavy: true }, { text: " · curator isn't running" }],
    time: "since 14:02", needs: WAITS,
  });
  assert.equal(WAITS, 20);
});

test("two names: the count is their sum, joined by and", () => {
  const got = line(up(entry("curator", 2, at("2026-11-12T09:15:00Z")),
                      entry("scout", 1, at("2026-11-12T11:00:00Z"))));
  assert.deepEqual(got.parts, [{ text: "3 messages waiting", heavy: true },
                               { text: " · curator and scout aren't running" }]);
  assert.equal(got.time, "since 09:15");
});

test("three names are all spelled out", () => {
  const got = line(up(entry("curator", 1, at("2026-11-12T09:00:00Z")),
                      entry("scout", 1, at("2026-11-12T10:00:00Z")),
                      entry("pilot", 1, at("2026-11-12T11:00:00Z"))));
  assert.equal(got.parts[0].text, "3 messages waiting");
  assert.equal(got.parts[1].text, " · curator, scout and pilot aren't running");
});

test("five names: the first two and how many more", () => {
  const names = ["curator", "scout", "pilot", "helper", "critic"];
  const got = line(up(...names.map((name, i) => entry(name, 2, at("2026-11-12T09:00:00Z") + i * 60))));
  assert.equal(got.parts[0].text, "10 messages waiting");
  assert.equal(got.parts[1].text, " · curator, scout and 3 more aren't running");
  assert.equal(NAMED, 3);
  assert.equal(namesWords(["curator", "scout", "pilot", "helper"]), "curator, scout and 2 more");
});

test("names go oldest first and the time is the oldest message's", () => {
  const got = line(up(entry("scout", 1, at("2026-11-12T13:00:00Z")),
                      entry("curator", 1, at("2026-11-12T08:30:00Z"))));
  assert.equal(got.parts[1].text, " · curator and scout aren't running");
  assert.equal(got.time, "since 08:30");
});

test("a qualified name, when the route gives one, is shown as given", () => {
  const got = line(up(entry("main@proj-a", 1, at("2026-11-12T08:00:00Z"), "main@proj-a"),
                      entry("main@proj-b", 1, at("2026-11-12T09:00:00Z"), "main@proj-b")));
  assert.equal(got.parts[1].text, " · main@proj-a and main@proj-b aren't running");
});

test("the time: today alone, the weekday within six days back, else the date", () => {
  assert.equal(sinceWords(at("2026-11-12T00:05:00Z"), NOW, UTC), "00:05");
  assert.equal(sinceWords(at("2026-11-11T23:10:00Z"), NOW, UTC), "Wed 23:10");
  assert.equal(sinceWords(at("2026-11-06T07:45:00Z"), NOW, UTC), "Fri 07:45");
  assert.equal(sinceWords(at("2026-11-05T07:45:00Z"), NOW, UTC), "Nov 5 07:45");
  assert.equal(line(up(entry("curator", 1, at("2026-11-10T18:20:00Z")))).time, "since Tue 18:20");
});

test("the day is the clock's own, not UTC's", () => {
  // At UTC-10, 03:00 UTC on the 12th is 17:00 on the 11th, and now is 06:00 on the 12th.
  const minus10 = {
    time: (t) => new Date((t - 36_000) * 1000).toISOString().slice(11, 16),
    day: (t) => new Date((t - 36_000) * 1000).toISOString().slice(0, 10),
  };
  assert.equal(sinceWords(at("2026-11-12T03:00:00Z"), NOW, UTC), "03:00");
  assert.equal(sinceWords(at("2026-11-12T03:00:00Z"), NOW, minus10), "Wed 17:00");
});

test("no row when nothing waits, or the daemon is offline or erroring", () => {
  assert.equal(line(up()), null);
  assert.equal(line({ state: "offline", detail: null, waiting: [] }), null);
  assert.equal(line({ state: "error", detail: "the comms daemon answered with an error", waiting: [] }), null);
  // Names on an answer that is not up are not shown either.
  assert.equal(line({ state: "error", detail: null, waiting: [entry("curator", 1, NOW)] }), null);
  assert.equal(mailLine(null, NOW, UTC), null);
});

test("the line carries only names, a count and a time", () => {
  const data = up(entry("curator", 1, NOW - 60));
  data.waiting[0].text = "the parser fix is ready";
  const got = line(data);
  assert.ok(!JSON.stringify(got).includes("parser"));
  assert.deepEqual(Object.keys(got), ["parts", "time", "needs"]);
});

test("parse refuses what it cannot read and leaves out a name it cannot say", () => {
  assert.equal(parseMail(null), null);
  assert.equal(parseMail("up"), null);
  assert.equal(parseMail({ state: "up" }), null);
  assert.equal(parseMail({ waiting: [] }), null);
  const got = parseMail(up(entry("curator", 1, NOW), entry("", 1, NOW), entry("scout", 0, NOW),
                           entry("pilot", 1.5, NOW), entry("helper", 1, "today"), null));
  assert.deepEqual(got.waiting.map((name) => name.name), ["curator"]);
});
