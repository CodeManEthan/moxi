import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { CSS } from "../src/gateway-page.ts";
import {
  creationOptions, credentialJson, defaultName, freshN, fromB64url, pairRefusal, passkeyFailure, requestOptions,
  signinRefusal, toB64url,
} from "../src/passkey.ts";

const bytes = (...values) => new Uint8Array(values);

test("base64url both ways, padded or not, and nothing else", () => {
  assert.equal(toB64url(bytes(0xfb, 0xff, 0xbf)), "-_-_");
  assert.equal(toB64url(bytes(1, 2).buffer), "AQI");
  assert.deepEqual(new Uint8Array(fromB64url("AQI")), bytes(1, 2));
  assert.deepEqual(new Uint8Array(fromB64url("AQI=")), bytes(1, 2));
  assert.deepEqual(new Uint8Array(fromB64url("-_-_")), bytes(0xfb, 0xff, 0xbf));
  for (const bad of ["a+b", "a/b", "a b", 7, null]) assert.throws(() => fromB64url(bad), TypeError, String(bad));
});

test("the gateway's options become what navigator.credentials takes", () => {
  const create = creationOptions({
    rp: { name: "Moxi", id: "moxi.example.com" }, user: { id: "AQI", name: "phone", displayName: "phone" },
    challenge: "AwQ", pubKeyCredParams: [{ type: "public-key", alg: -7 }], timeout: 120000,
    excludeCredentials: [{ type: "public-key", id: "BQY" }],
    authenticatorSelection: { residentKey: "required", userVerification: "required" }, attestation: "none",
  });
  assert.deepEqual(new Uint8Array(create.challenge), bytes(3, 4));
  assert.deepEqual(new Uint8Array(create.user.id), bytes(1, 2));
  assert.equal(create.user.name, "phone");
  assert.deepEqual(new Uint8Array(create.excludeCredentials[0].id), bytes(5, 6));
  assert.equal(create.authenticatorSelection.userVerification, "required");
  assert.equal("excludeCredentials" in creationOptions({ challenge: "", user: { id: "" } }), false);

  const get = requestOptions({ rpId: "moxi.example.com", challenge: "AwQ", allowCredentials: [], userVerification: "required" });
  assert.deepEqual(new Uint8Array(get.challenge), bytes(3, 4));
  assert.deepEqual(get.allowCredentials, []);
  assert.equal(get.userVerification, "required");
});

test("a new passkey and an assertion become WebAuthn's JSON", () => {
  const made = credentialJson({
    id: "AQI", rawId: bytes(1, 2).buffer, type: "public-key", authenticatorAttachment: "platform",
    getClientExtensionResults: () => ({ credProps: { rk: true } }),
    response: { clientDataJSON: bytes(7).buffer, attestationObject: bytes(8, 9).buffer, getTransports: () => ["internal"] },
  });
  assert.deepEqual(made, {
    id: "AQI", rawId: "AQI", type: "public-key", authenticatorAttachment: "platform",
    clientExtensionResults: { credProps: { rk: true } },
    response: { clientDataJSON: "Bw", attestationObject: "CAk", transports: ["internal"] },
  });
  const asserted = credentialJson({
    id: "AQI", rawId: bytes(1, 2).buffer, type: "public-key", authenticatorAttachment: null,
    response: { clientDataJSON: bytes(7).buffer, authenticatorData: bytes(10).buffer, signature: bytes(11).buffer,
                userHandle: bytes(12).buffer },
  });
  assert.deepEqual(asserted.response, { clientDataJSON: "Bw", authenticatorData: "Cg", signature: "Cw", userHandle: "DA" });
  assert.deepEqual(asserted.clientExtensionResults, {});
  const noHandle = credentialJson({
    id: "x", rawId: bytes(1).buffer, type: "public-key",
    response: { clientDataJSON: bytes(7).buffer, authenticatorData: bytes(10).buffer, signature: bytes(11).buffer, userHandle: null },
  });
  assert.equal("userHandle" in noHandle.response, false);
});

test("a phone suggests its model, else what its browser says it is", () => {
  assert.equal(defaultName("Mozilla/5.0 (Linux; Android 10; K)", "Pixel 9a"), "Pixel 9a");
  assert.equal(defaultName("Mozilla/5.0 (Linux; Android 10; K)", "  "), "Android phone");
  assert.equal(defaultName("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"), "iPhone");
  assert.equal(defaultName("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)"), "iPad");
  assert.equal(defaultName("Mozilla/5.0 (X11; Linux x86_64)"), "Phone");
  assert.equal(defaultName("", "m".repeat(80)).length, 64);
});

test("each refusal says what happened and how to go on", () => {
  assert.match(pairRefusal("begin", 403, null), /used or has expired.*Pair again/);
  assert.match(pairRefusal("finish", 403, null), /wasn't accepted.*Pair again/);
  assert.equal(pairRefusal("finish", 409, { error: "this Moxi already has 20 devices" }), "this Moxi already has 20 devices");
  assert.match(pairRefusal("begin", 0, null), /No answer/);
  assert.match(pairRefusal("begin", 503, null), /no gateway address/);
  assert.match(signinRefusal(403, null), /didn't accept this passkey/);
  assert.match(passkeyFailure({ name: "NotAllowedError" }), /No passkey was used/);
  assert.match(passkeyFailure(new Error("x")), /failed/);
  // No refusal names a command with code marks the page would show literally.
  for (const text of [pairRefusal("begin", 403, null), signinRefusal(403, null), pairRefusal("begin", 503, null)]) {
    assert.doesNotMatch(text, /`/);
  }
});

test("a fresh n is 16 hex digits", () => {
  assert.match(freshN(), /^[0-9a-f]{16}$/);
  assert.equal(freshN((b) => b.fill(0xab)), "ab".repeat(8));
});

test("the pages' colours are tokens.json's, the light set's and the dark set's", () => {
  const tokens = JSON.parse(readFileSync(new URL("../tokens.json", import.meta.url), "utf8"));
  const pairs = [...CSS.matchAll(/light-dark\((#[0-9a-f]{6}), (#[0-9a-f]{6})\)/g)];
  assert.ok(pairs.length >= 7);
  // Every colour in the sheet is a pair; none is written another way.
  assert.equal(CSS.match(/#[0-9a-f]{3,8}\b/g).length, pairs.length * 2);
  for (const name of ["bg", "bg-raised", "border", "text", "text-dim", "accent", "error"]) {
    const values = tokens[name].$extensions.moxi.sets;
    assert.ok(CSS.includes(`light-dark(${values["calm-workbench"]}, ${values["midnight-neon"]})`), name);
  }
  assert.match(CSS, /:root \{ color-scheme: light dark; \}/);
});

test("the pages load their scripts from their own paths and hold no inline script or style", () => {
  for (const [page, script] of [["pair.html", "/pair.js"], ["signin.html", "/signin.js"]]) {
    const text = readFileSync(new URL(`../${page}`, import.meta.url), "utf8");
    const scripts = [...text.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
    assert.equal(scripts.length, 1, page);
    assert.match(scripts[0][1], new RegExp(`src="${script.replace(".", "\\.")}"`), page);
    assert.equal(scripts[0][2], "", page);
    assert.doesNotMatch(text, /<style|\sstyle=|\son[a-z]+=/i, page);
  }
});
