import assert from "node:assert/strict";
import test from "node:test";

import { clock } from "../src/health-model.ts";
import {
  accessLine, arrived, dayWord, deviceWords, failedWords, momentWords, pairWords, revokeAsk, revokedWords, signinLines,
  stateFailed, stateOf,
} from "../src/phone-model.ts";

const STATE = { installed: true, switch: true, on: true, host: "gw.example.test", port: 18461, exposure: "tailnet",
                reads_only: true, problem: null };

function at(year, month, day, hour = 12, minute = 0) {
  return new Date(year, month - 1, day, hour, minute);
}

function seconds(date) {
  return date.getTime() / 1000;
}

test("the state is read from the route's answer, and anything else is no state", () => {
  assert.deepEqual(stateOf(STATE), STATE);
  assert.deepEqual(stateOf({ ...STATE, host: "", port: 1.5, exposure: "provider", problem: "" }),
                   { ...STATE, host: null, port: null, exposure: null, problem: null });
  assert.equal(stateOf(null), null);
  assert.equal(stateOf({ installed: "yes", switch: true, on: true }), null);
  assert.equal(stateOf({ ...STATE, reads_only: "no" }).reads_only, false);
});

test("Phone access says where phones reach it while it serves, and that they only read", () => {
  assert.equal(accessLine(STATE), "At gw.example.test:18461. Phones read only.");
  assert.equal(accessLine({ ...STATE, reads_only: false }), "At gw.example.test:18461.");
  assert.equal(accessLine({ ...STATE, port: null }), "At gw.example.test. Phones read only.");
  assert.equal(accessLine({ ...STATE, host: null, reads_only: false }), null);
});

test("Phone access with the switch on and the gateway not serving says why, or that it can't", () => {
  const down = { ...STATE, on: false };
  assert.equal(accessLine({ ...down, problem: "tunnel isn't installed" }), "Phones can't reach it now: tunnel isn't installed.");
  assert.equal(accessLine({ ...down, problem: "the address is applying." }), "Phones can't reach it now: the address is applying.");
  assert.equal(accessLine(down), "Phones can't reach it now.");
});

test("Phone access with the switch off says nothing more than its terminal line", () => {
  assert.equal(accessLine({ ...STATE, switch: false, on: false }), null);
  assert.equal(accessLine({ ...STATE, installed: true, switch: false, on: true }), null);
});

test("a failure says what failed, as the Gateway section said it", () => {
  assert.equal(failedWords(0, null, "reading the phones"), "No answer from the shell, so reading the phones failed.");
  assert.equal(failedWords(403, null, "pairing"), "This browser is signed out.");
  assert.equal(failedWords(409, { refused: "the gateway has no address" }, "pairing"), "the gateway has no address");
  assert.equal(failedWords(409, { conflict: "busy" }, "pairing"), "busy");
  assert.equal(failedWords(500, { error: "boom" }, "revoking"), "boom");
  assert.equal(failedWords(502, "Bad Gateway", "revoking"), "The shell answered 502 for revoking.");
  assert.equal(stateFailed(0, null), "The phone's state can't be read: No answer from the shell, so reading it failed.");
  assert.equal(stateFailed(500, { error: "boom" }), "The phone's state can't be read: boom");
});

test("a day is worded as the Comms page words one", () => {
  const now = at(2026, 11, 15, 19, 0);
  assert.equal(dayWord(at(2026, 11, 15, 0, 5), now), "today");
  assert.equal(dayWord(at(2026, 11, 14, 23, 59), now), "yesterday");
  assert.equal(dayWord(at(2026, 11, 11), now), "Wed");
  assert.equal(dayWord(at(2026, 10, 25), now), "Oct 25");
  assert.equal(dayWord(at(2026, 11, 2), now), "Nov 2");
});

test("each phone's line: paired, its last sign-in, its passkey, and a stale one's warning", () => {
  const now = at(2026, 11, 15, 19, 0);
  const used = at(2026, 11, 15, 18, 40);
  const row = { id: "a", name: "pocket", created: seconds(at(2026, 10, 25)), used: seconds(used), passkey: true,
                stale: false, host: "gw.example.test", provider: "tailscale" };
  const time = clock(used.toISOString());
  assert.equal(deviceWords(row, now), `paired Oct 25 · last sign-in today ${time} · passkey`);
  assert.equal(deviceWords({ ...row, used: 0, passkey: false }, now), "paired Oct 25 · last sign-in never · no passkey");
  assert.equal(deviceWords({ ...row, stale: true }, now),
               `paired Oct 25 · last sign-in today ${time} · passkey · the address changed; pair it again`);
  const old = at(2026, 11, 2, 8, 3);
  assert.equal(momentWords(seconds(old), now), `Nov 2 ${clock(old.toISOString())}`);
});

test("Revoke asks in place, and its answer names the passkey left on the phone", () => {
  assert.deepEqual(revokeAsk("pocket"), {
    ask: "Revoke pocket?", why: "It is signed out within two seconds and must be paired again to come back.",
    keep: "Keep it", button: "Revoke pocket",
  });
  assert.equal(revokedWords({ name: "pocket", passkey: true }),
               "Revoked pocket. Its passkey stays on the phone until you remove it in the phone's settings.");
  assert.equal(revokedWords({ name: "work-phone", passkey: false }), "Revoked work-phone.");
});

test("the pairing sentence says until when, and for whom on a tailnet address", () => {
  const until = at(2026, 11, 15, 19, 12);
  const end = clock(until.toISOString());
  assert.deepEqual(pairWords(seconds(until), "tailnet"),
                   { lead: "Scan this with the phone's camera.", rest: `It works once, until ${end}, and only for a phone on your tailnet.` });
  assert.equal(pairWords(seconds(until), "internet").rest, `It works once, until ${end}.`);
  assert.equal(pairWords(seconds(until), null).rest, `It works once, until ${end}.`);
});

test("a phone that arrives while a code lives is the first one not known", () => {
  const rows = [{ id: "a", name: "old" }, { id: "b", name: "new" }];
  assert.equal(arrived(rows, new Set(["a"])).name, "new");
  assert.equal(arrived(rows, new Set(["a", "b"])), null);
});

test("Activity names each phone's sign-ins by its name", () => {
  const signins = new Map([["a", ["t1", "t2"]], ["gone", ["t3"]], ["b", []]]);
  assert.deepEqual(signinLines(signins, [{ id: "a", name: "pocket" }], (text) => text.toUpperCase()),
                   ["pocket signed in T1, T2.", "gone signed in T3."]);
});
