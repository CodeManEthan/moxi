import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import test from "node:test";

import {
  isConfirmed, KEY_BYTES, KEY_PATTERN, MAY_HAVE_ACTED, mintKey, NOT_SENT, outcomeOf, pagePath, SIGNED_OUT,
} from "../src/confirm-model.ts";

test("a key is 32 random bytes in base64url without padding, 43 characters", () => {
  let asked = 0;
  const key = mintKey((bytes) => {
    asked = bytes.length;
    bytes.set(randomBytes(bytes.length));
    return bytes;
  });
  assert.equal(asked, KEY_BYTES);
  assert.match(key, KEY_PATTERN);
  // Every byte value lands in the alphabet: all ones give `_`, all zeros `A`.
  assert.equal(mintKey((bytes) => bytes.fill(0xff)), "_".repeat(42) + "8");
  assert.equal(mintKey((bytes) => bytes.fill(0)), "A".repeat(43));
  assert.equal(mintKey((bytes) => bytes.fill(0xfb)), "-_v7".repeat(10) + "-_s");
  // The real source: each key differs and has the shape.
  const keys = new Set(Array.from({ length: 50 }, () => mintKey()));
  assert.equal(keys.size, 50);
  for (const one of keys) assert.match(one, KEY_PATTERN);
});

test("the module's 202 is accepted with its status path", () => {
  assert.deepEqual(outcomeOf(202, { status: "/api/job?job=j1" }), { outcome: "accepted", status: "/api/job?job=j1" });
  // A 202 without a status path can't say where the job is.
  assert.deepEqual(outcomeOf(202, null), { outcome: "unknown", text: MAY_HAVE_ACTED });
});

test("the module's 4xx is refused with its status and its text", () => {
  const cases = [
    [400, { error: "missing header: Idempotency-Key" }, "missing header: Idempotency-Key"],
    [409, { conflict: "this act is still running" }, "this act is still running"],
    [422, { refused: "this Idempotency-Key was used for another request" },
      "this Idempotency-Key was used for another request"],
    [413, null, "the request was too large for the module"],
    [403, null, SIGNED_OUT],
    [404, null, "the module has no such act now"],
    [418, null, "the module refused the act (418)"],
    [429, { busy: 1 }, "the module refused the act (429)"],
  ];
  for (const [code, data, text] of cases) {
    assert.deepEqual(outcomeOf(code, data), { outcome: "refused", code, text }, String(code));
  }
});

test("a 503 is refused, from the module or from a stopping server", () => {
  assert.deepEqual(outcomeOf(503, null),
                   { outcome: "refused", code: 503, text: "the module was too busy to take the act; try again" });
  assert.deepEqual(outcomeOf(503, { error: "stopping", module: "fixture" }),
                   { outcome: "refused", code: 503, text: NOT_SENT });
});

test("the shell's 502 start or unreachable is refused: nothing was sent", () => {
  for (const error of ["start", "unreachable"]) {
    assert.deepEqual(outcomeOf(502, { error, module: "fixture", log: "x" }),
                     { outcome: "refused", code: 502, text: NOT_SENT }, error);
  }
});

test("a sent act with no answer, a 500 or another 5xx is unknown", () => {
  const unknown = { outcome: "unknown", text: MAY_HAVE_ACTED };
  assert.deepEqual(outcomeOf(502, { error: "upstream", module: "fixture" }), unknown);
  assert.deepEqual(outcomeOf(502, null), unknown);
  assert.deepEqual(outcomeOf(504, { error: "timeout", module: "fixture" }), unknown);
  assert.deepEqual(outcomeOf(500, null), unknown);
  assert.deepEqual(outcomeOf(501, null), unknown);
  assert.deepEqual(outcomeOf(0, null), unknown);
  // Answers a confirmed act never gets are unknown too.
  for (const code of [200, 204, 301]) assert.deepEqual(outcomeOf(code, { count: 1 }), unknown, String(code));
});

test("a confirmed act is matched by its page path, whatever its query", () => {
  const confirmed = ["/api/confirm", "/api/stop"];
  assert.equal(pagePath("fixture", "/m/fixture/api/confirm?x=1"), "/api/confirm");
  assert.equal(isConfirmed(confirmed, "fixture", "/m/fixture/api/confirm"), true);
  assert.equal(isConfirmed(confirmed, "fixture", "/m/fixture/api/confirm?x=1"), true);
  assert.equal(isConfirmed(confirmed, "fixture", "/m/fixture/api/add"), false);
  assert.equal(isConfirmed(confirmed, "fixture", "/m/fixture/api/confirm/more"), false);
  assert.equal(isConfirmed([], "fixture", "/m/fixture/api/confirm"), false);
});
