import assert from "node:assert/strict";
import test from "node:test";

import { waitedMs } from "../src/wait-model.ts";

test("before the timer fires the wait is what the clock reads", () => {
  assert.equal(waitedMs(1000, 1250, 400, false), 250);
  assert.equal(waitedMs(1000, 1400, 400, false), 400);
});

test("once the timer has fired the wait is due even when the clock reads short", () => {
  assert.equal(waitedMs(1000, 1399, 400, true), 400);
  assert.equal(waitedMs(1000, 1000, 400, true), 400);
});

test("a fired timer never shortens a longer wait", () => {
  assert.equal(waitedMs(1000, 61000, 400, true), 60000);
});

test("a clock that reads before the start is no wait at all", () => {
  assert.equal(waitedMs(1000, 990, 400, false), 0);
});
