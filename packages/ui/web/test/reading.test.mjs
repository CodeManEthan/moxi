import assert from "node:assert/strict";
import test from "node:test";

import { FIRST_VALUE_MS, ReadingCount } from "../src/reading-model.ts";

/** A `later` that holds each call until the test runs it. */
function held() {
  const calls = [];
  return { calls, later: (fn, ms) => { calls.push({ fn, ms }); } };
}

test("a read counts from its start until a turn after it ends", () => {
  const { calls, later } = held();
  const seen = [];
  const count = new ReadingCount((n) => seen.push(n), later);
  const end = count.begin();
  assert.equal(count.count, 1);
  end();
  assert.equal(count.count, 1, "still out until the turn after the answer");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].ms, 0);
  calls[0].fn();
  assert.equal(count.count, 0);
  assert.deepEqual(seen, [1, 0]);
});

test("two reads out count two, and an end called twice takes one off", () => {
  const { calls, later } = held();
  const count = new ReadingCount(() => {}, later);
  const one = count.begin();
  count.begin();
  assert.equal(count.count, 2);
  one();
  one();
  assert.equal(calls.length, 1);
  calls[0].fn();
  assert.equal(count.count, 1);
});

test("a topic's first value ends its read, after the listener has drawn it", () => {
  const { calls, later } = held();
  const count = new ReadingCount(() => {}, later);
  const got = [];
  const first = count.firstValue((data) => got.push([data, count.count]));
  assert.equal(count.count, 1);
  first.listener("a");
  assert.deepEqual(got, [["a", 1]], "the listener runs while the read still counts");
  const ending = calls.find((call) => call.ms === 0);
  ending.fn();
  assert.equal(count.count, 0);
  first.listener("b");
  assert.equal(calls.filter((call) => call.ms === 0).length, 1, "later values count nothing");
  assert.deepEqual(got.map(([data]) => data), ["a", "b"]);
});

test("a topic with no first value stops counting at the bound or when it is stopped", () => {
  const { calls, later } = held();
  const count = new ReadingCount(() => {}, later);
  count.firstValue(() => {});
  const bound = calls.find((call) => call.ms === FIRST_VALUE_MS);
  bound.fn();
  calls.find((call) => call.ms === 0).fn();
  assert.equal(count.count, 0);

  const stopped = count.firstValue(() => {});
  stopped.stopped();
  calls.filter((call) => call.ms === 0).at(-1).fn();
  assert.equal(count.count, 0);
});
