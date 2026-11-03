import assert from "node:assert/strict";
import test from "node:test";

import { MAY_HAVE_ACTED, NOT_SENT, SIGNED_OUT } from "../src/confirm-model.ts";
import {
  bandText, committed, deskLines, deskOutcome, endingReach, gatewayConfirm, gatewayInfo, LAPSED, LAPSED_LINE,
  prepared, REASON_LINES, reachOf, spans,
} from "../src/gateway-model.ts";

test("the document says whether it came through the gateway and whether acts mount", () => {
  assert.deepEqual(gatewayInfo({}), { gateway: false, acts: false, reason: null });
  assert.deepEqual(gatewayInfo({ gateway: "on", gatewayActs: "on" }), { gateway: true, acts: true, reason: null });
  assert.deepEqual(gatewayInfo({ gateway: "on", gatewayActs: "off", gatewayReason: "tcp" }),
                   { gateway: true, acts: false, reason: "tcp" });
  assert.deepEqual(gatewayInfo({ gateway: "on", gatewayActs: "off" }), { gateway: true, acts: false, reason: null });
  // The desktop's document carries none of them, and acts never mount off the gateway.
  assert.deepEqual(gatewayInfo({ gatewayActs: "on", gatewayReason: "tcp" }), { gateway: false, acts: false, reason: null });
  // Only "on" counts.
  assert.equal(gatewayInfo({ gateway: "true", gatewayActs: "true" }).gateway, false);
});

test("the desk pointer names the computer and the module, then the reason's line", () => {
  assert.deepEqual(deskLines("box.example.com", "services", "tcp"),
                   ["Do this at the desk, on box.example.com's Moxi page for services.", REASON_LINES.tcp]);
  for (const reason of ["record", "tcp", "target", "origin"]) {
    assert.equal(deskLines("h", "m", reason)[1], REASON_LINES[reason], reason);
  }
  assert.match(REASON_LINES.record, /^Moxi can't read the tunnel's record/);
  assert.match(REASON_LINES.target, /moxi tunnel repair/);
  assert.match(REASON_LINES.origin, /Give the gateway a new address/);
  assert.equal(deskLines("h", "m", "lapsed")[1], LAPSED_LINE);
  assert.equal(LAPSED_LINE, "The phone can't run acts on this computer right now. Reload the page to see why.");
  // A reason this script doesn't know, or none, still points at the desk.
  for (const reason of [null, "gap", "toString"]) {
    assert.equal(deskLines("h", "m", reason)[1], "The phone can't run acts on this computer right now.", String(reason));
  }
});

test("prepare: the gateway's echo, a 404 that sends the sheet to the desk, or a refusal", () => {
  const act = "A".repeat(43);
  assert.deepEqual(prepared(200, { act, module: "fixture", path: "/api/confirm", body: { b: 1, a: 2 }, expires: "x" }),
                   { act, body: { b: 1, a: 2 } });
  assert.equal(prepared(404, null), LAPSED);
  assert.deepEqual(prepared(403, null), { outcome: "refused", code: 403, text: SIGNED_OUT });
  assert.deepEqual(prepared(400, { error: "name is a string" }), { outcome: "refused", code: 400, text: "name is a string" });
  assert.deepEqual(prepared(413, null), { outcome: "refused", code: 413, text: NOT_SENT });
  assert.equal(prepared(429, { error: "busy" }).outcome, "refused");
  // Nothing reaches a module from a prepare, so no failure is unknown.
  for (const status of [0, 500, 502, 503, 504]) {
    assert.deepEqual(prepared(status, null), { outcome: "refused", code: status, text: NOT_SENT }, String(status));
  }
  assert.deepEqual(prepared(200, { act }), { outcome: "refused", code: 200, text: NOT_SENT });
});

test("commit: the gateway's outcome, the desk on a 404, its own refusals, else unknown", () => {
  assert.deepEqual(committed(202, { act: "x", outcome: "accepted", status: "/api/job?job=j1" }),
                   { outcome: "accepted", status: "/api/job?job=j1" });
  assert.deepEqual(committed(202, { act: "x", outcome: "refused", code: 409, text: "this act is still running" }),
                   { outcome: "refused", code: 409, text: "this act is still running" });
  assert.deepEqual(committed(202, { act: "x", outcome: "unknown", text: "the act is still being sent" }),
                   { outcome: "unknown", text: "the act is still being sent" });
  // A 202 that can't be read may still have acted.
  for (const data of [null, {}, { outcome: "accepted" }, { outcome: "refused", code: "409", text: "t" }]) {
    assert.deepEqual(committed(202, data), { outcome: "unknown", text: MAY_HAVE_ACTED });
  }
  assert.equal(committed(404, null), LAPSED);
  assert.deepEqual(committed(403, null), { outcome: "refused", code: 403, text: SIGNED_OUT });
  assert.equal(committed(429, { error: "busy" }).code, 429);
  assert.deepEqual(committed(400, { error: "the body is an object of act" }),
                   { outcome: "refused", code: 400, text: "the body is an object of act" });
  for (const status of [0, 500, 502, 503, 504]) {
    assert.deepEqual(committed(status, null), { outcome: "unknown", text: MAY_HAVE_ACTED }, String(status));
  }
});

test("the band: nothing answered, or the computer answered without Moxi, or Moxi answered", () => {
  assert.equal(reachOf(0, false), "away");
  for (const status of [502, 503, 504]) {
    assert.equal(reachOf(status, false), "down", String(status));
    // The shell's own answer for a module that is down is JSON, and Moxi answered it.
    assert.equal(reachOf(status, true), "up", `${status} json`);
  }
  for (const status of [200, 202, 303, 403, 404, 500]) assert.equal(reachOf(status, false), "up", String(status));
  const at = new Date(2026, 0, 2, 9, 5);
  assert.match(bandText("away", "box.example.com", null), /^Can't reach the computer \(box\.example\.com\)\. It may be asleep or off/);
  assert.match(bandText("down", "box.example.com", null), /Moxi isn't running or the gateway is off\.$/);
  assert.match(bandText("away", "h", at), /Moxi last answered at .*09.*05/);
  assert.equal(bandText("up", "h", at), "");
});

test("the push stream's end tells the band whether the computer answered", () => {
  assert.equal(endingReach({ kind: "network" }), "away");
  assert.equal(endingReach({ kind: "stalled" }), "away");
  assert.equal(endingReach({ kind: "closed" }), null);
  assert.equal(endingReach({ kind: "status", status: 502, error: null, retryAfter: null }), "down");
  assert.equal(endingReach({ kind: "status", status: 503, error: "busy", retryAfter: 5 }), "up");
  assert.equal(endingReach({ kind: "status", status: 403, error: null, retryAfter: null }), "up");
});

test("a line's backtick spans show as code", () => {
  assert.deepEqual(spans("Run `moxi tunnel repair` on the computer."), [
    { text: "Run ", code: false }, { text: "moxi tunnel repair", code: true }, { text: " on the computer.", code: false },
  ]);
  assert.deepEqual(spans("no code"), [{ text: "no code", code: false }]);
  assert.deepEqual(spans("one ` alone"), [{ text: "one ` alone", code: false }]);
  assert.deepEqual(deskOutcome(["a.", "b."]), { outcome: "desk", text: "a. b." });
});

/** Fake steps: `answers` the gateway's answers in order; records what was posted and shown. */
function steps(answers, { confirmIt = true } = {}) {
  const posted = [];
  const shown = [];
  return {
    posted, shown,
    async post(path, body) {
      posted.push([path, body]);
      const next = answers.shift();
      assert.ok(next, `no answer left for ${path}`);
      return next;
    },
    async ask(body, send) {
      shown.push({ body });
      if (!confirmIt) return { outcome: "cancelled" };
      const answer = await send();
      if ("desk" in answer) {
        shown.push({ desk: answer.desk });
        return deskOutcome(answer.desk);
      }
      return answer;
    },
    async desk(lines) {
      shown.push({ desk: lines });
      return deskOutcome(lines);
    },
  };
}

const ON = { gateway: true, acts: true, reason: null };

test("where acts aren't mounted, confirm points at the desk and sends nothing", async () => {
  const fake = steps([]);
  const got = await gatewayConfirm({ gateway: true, acts: false, reason: "tcp" }, "box.example.com", "services",
                                   "/api/start", { name: "a" }, fake);
  assert.deepEqual(fake.posted, []);
  assert.deepEqual(fake.shown, [{ desk: deskLines("box.example.com", "services", "tcp") }]);
  assert.equal(got.outcome, "desk");
  assert.equal(got.text, deskLines("box.example.com", "services", "tcp").join(" "));
});

test("where acts mount, confirm prepares, shows the stored body, and commits once", async () => {
  const fake = steps([
    { status: 200, data: { act: "A".repeat(43), module: "services", path: "/api/start", body: { name: "a" }, expires: "x" } },
    { status: 202, data: { act: "A".repeat(43), outcome: "accepted", status: "/api/status?job=1" } },
  ]);
  const got = await gatewayConfirm(ON, "h", "services", "/api/start", { name: "a", extra: 1 }, fake);
  assert.deepEqual(fake.posted, [
    ["/gw/act/prepare", { module: "services", path: "/api/start", body: { name: "a", extra: 1 } }],
    ["/gw/act/commit", { act: "A".repeat(43) }],
  ]);
  // The sheet shows the gateway's echo, not the page's copy.
  assert.deepEqual(fake.shown, [{ body: { name: "a" } }]);
  assert.deepEqual(got, { outcome: "accepted", status: "/api/status?job=1" });
});

test("a cancelled sheet commits nothing", async () => {
  const fake = steps([{ status: 200, data: { act: "x", body: {} } }], { confirmIt: false });
  assert.deepEqual(await gatewayConfirm(ON, "h", "m", "/api/a", {}, fake), { outcome: "cancelled" });
  assert.equal(fake.posted.length, 1);
});

test("a prepare's refusal resolves with no sheet, and its 404 points at the desk", async () => {
  let fake = steps([{ status: 429, data: { error: "busy" } }]);
  const busy = await gatewayConfirm(ON, "h", "m", "/api/a", {}, fake);
  assert.equal(busy.outcome, "refused");
  assert.equal(busy.code, 429);
  assert.deepEqual(fake.shown, []);
  fake = steps([{ status: 404, data: null }]);
  const lapsed = await gatewayConfirm(ON, "h", "m", "/api/a", {}, fake);
  assert.deepEqual(fake.shown, [{ desk: deskLines("h", "m", LAPSED) }]);
  assert.deepEqual(lapsed, deskOutcome(deskLines("h", "m", LAPSED)));
});

test("a commit's 404 turns the open sheet into the desk pointer; no answer is unknown", async () => {
  let fake = steps([{ status: 200, data: { act: "x", body: {} } }, { status: 404, data: null }]);
  const lapsed = await gatewayConfirm(ON, "h", "m", "/api/a", {}, fake);
  assert.deepEqual(fake.shown, [{ body: {} }, { desk: deskLines("h", "m", LAPSED) }]);
  assert.equal(lapsed.outcome, "desk");
  fake = steps([{ status: 200, data: { act: "x", body: {} } }, { status: 0, data: null }]);
  assert.deepEqual(await gatewayConfirm(ON, "h", "m", "/api/a", {}, fake), { outcome: "unknown", text: MAY_HAVE_ACTED });
  assert.equal(fake.posted.length, 2);
});
