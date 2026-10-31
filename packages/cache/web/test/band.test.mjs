import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { NO_READING, reading, readingText, span } from "../src/band.ts";

// The one case table cache's segment test runs too.
const TABLE = JSON.parse(readFileSync(new URL("../../tests/band-cases.json", import.meta.url), "utf8"));
const KEYS = ["session", "harness", "kind", "last_request", "ttl", "warn_before", "seen_at",
              "stale_after", "code"];
const TIMES = ["last_request", "read_at", "seen_at"];

/** A record as cache's page sends it: the declared keys only, a missing one as null. */
const asRow = (record) => Object.fromEntries(KEYS.map((key) => [key, record[key] ?? null]));

/** A record written for a reading at `now` in place of the table's. */
const shifted = (record, now) => Object.fromEntries(Object.entries(record).map(([key, value]) =>
  [key, TIMES.includes(key) && value !== null ? value + now - TABLE.now : value]));

test("the table has a case for each word the chip shows", () => {
  const shows = new Set(TABLE.cases.map((item) => item.shows));
  for (const word of ["warm", "near", "cold", "fresh", "no-cache", "unknown", NO_READING]) {
    assert.ok(shows.has(word), word);
  }
  assert.ok(TABLE.cases.some((item) => item.dim));
});

for (const item of TABLE.cases) {
  test(`band case: ${item.name}`, () => {
    const found = reading(asRow(item.record), TABLE.now);
    assert.equal(found.shows, item.shows);
    assert.equal(found.dim, item.dim);
    // The same reading at another time, with every time moved with it.
    const later = 1900000123.5;
    const moved = reading(asRow(shifted(item.record, later)), later);
    assert.equal(moved.shows, item.shows);
    assert.equal(moved.dim, item.dim);
  });
}

test("no row for the session is no reading, never dim", () => {
  assert.deepEqual(reading(undefined, TABLE.now), { shows: NO_READING, band: null, dim: false, age: null, ttl: null });
  assert.equal(reading(null, TABLE.now).shows, NO_READING);
});

test("a kind outside the table shows its own word", () => {
  const row = asRow({ ...TABLE.cases[0].record, kind: "later" });
  assert.equal(reading(row, TABLE.now).shows, "later");
});

test("lengths of time read in whole units", () => {
  assert.equal(span(0), "0s");
  assert.equal(span(-4), "0s");
  assert.equal(span(59.9), "59s");
  assert.equal(span(60), "1m");
  assert.equal(span(41 * 60 + 59), "41m");
  assert.equal(span(3600), "1h");
  assert.equal(span(3600 + 4 * 60), "1h4m");
  assert.equal(span(26 * 3600), "1d");
});

test("the chip's words count down to the lifetime's end, then up from it", () => {
  const row = (lastAgo, ttl = 3600, warn = 900) => asRow({ kind: "warm", session: "s", harness: "claude",
    last_request: 10_000 - lastAgo, ttl, warn_before: warn, seen_at: 10_000, stale_after: 30, code: null });
  assert.equal(readingText(reading(row(19 * 60), 10_000)), "warm 41m left");
  assert.equal(readingText(reading(row(54 * 60), 10_000)), "near 6m left");
  assert.equal(readingText(reading(row(72 * 60), 10_000)), "cold 12m ago");
  // The browser test's short clock: ttl 2, warn_before 1.
  assert.equal(readingText(reading(row(0.5, 2, 1), 10_000)), "warm 2s left");
  assert.equal(readingText(reading(row(1.2, 2, 1), 10_000)), "near 1s left");
  assert.equal(readingText(reading(row(2, 2, 1), 10_000)), "cold 0s ago");
  assert.equal(readingText(reading(asRow({ kind: "fresh", session: "s" }), 10_000)), "fresh");
});
