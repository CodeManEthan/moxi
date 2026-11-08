import assert from "node:assert/strict";
import * as fs from "node:fs";
import test from "node:test";
import * as vm from "node:vm";

import { OPTIONS as BUILD_OPTIONS } from "../../../../tools/web/build.mjs";
import { manifest, OPTIONS } from "../gen.mjs";
import { bandText } from "../src/gateway-model.ts";
import { PUSH_URL } from "../src/push.ts";
import {
  ANSWERED, AHEAD_MS, answeredAt, answerReach, CACHE, EARLIEST_MS, GATEWAY_HEADERS, handling,
  navigationReach, offlineDocument, offlineInfo, ownHeaders, RETRY_MS,
} from "../src/worker-model.ts";

const ORIGIN = "https://gw.example.test";
const url = (path) => new URL(path, ORIGIN);
const tokens = JSON.parse(fs.readFileSync(new URL("../tokens.json", import.meta.url), "utf8"));

test("the worker answers same-origin GETs and leaves the rest to the browser", () => {
  assert.equal(handling("GET", url("/m/ui/"), ORIGIN, "navigate"), "navigation");
  assert.equal(handling("GET", url("/m/repo/rows?x=1"), ORIGIN, "navigate"), "navigation");
  assert.equal(handling("GET", url("/m/ui/client.js"), ORIGIN, "cors"), "get");
  assert.equal(handling("GET", url("/m/ui/api/modules"), ORIGIN, "cors"), "get");
  assert.equal(handling("GET", url("/icon-192.png"), ORIGIN, "no-cors"), "get");
  assert.equal(handling("POST", url("/gw/act/prepare"), ORIGIN, "cors"), "pass");
  assert.equal(handling("GET", new URL("https://other.example.test/x"), ORIGIN, "cors"), "pass");
});

test("the offline page and its script are the worker's own, on every request", () => {
  assert.equal(handling("GET", url("/offline"), ORIGIN, "navigate"), "own");
  assert.equal(handling("GET", url("/offline"), ORIGIN, "cors"), "own");
  assert.equal(handling("GET", url("/offline.js"), ORIGIN, "script"), "own");
  assert.equal(handling("GET", url("/offline.json"), ORIGIN, "cors"), "get");
});

test("the pairing and sign-in pages and the push stream pass the worker", () => {
  assert.equal(handling("GET", url("/pair"), ORIGIN, "navigate"), "pass");
  assert.equal(handling("GET", url("/signin?n=0123456789abcdef"), ORIGIN, "navigate"), "pass");
  assert.equal(handling("GET", url(`${PUSH_URL}?topics=ui.modules`), ORIGIN, "cors"), "pass");
});

test("a navigation's 502, 503 or 504 shows the offline page; a redirect is the gateway's", () => {
  for (const status of [502, 503, 504]) assert.equal(navigationReach(status, "basic"), "down");
  for (const status of [200, 303, 403, 404]) assert.equal(navigationReach(status, "basic"), "up");
  assert.equal(navigationReach(0, "opaqueredirect"), "up");
});

test("another answer reads as the band reads it: a JSON 502 is the gateway's own", () => {
  assert.equal(answerReach(502, "text/html"), "down");
  assert.equal(answerReach(503, null), "down");
  assert.equal(answerReach(502, "application/json; charset=utf-8"), "up");
  assert.equal(answerReach(200, "text/javascript"), "up");
  assert.equal(answerReach(0, null), "away");
});

test("the worker's own files carry the gateway's one policy and no-store", () => {
  const headers = ownHeaders("text/html; charset=utf-8");
  assert.equal(headers.get("Content-Type"), "text/html; charset=utf-8");
  assert.equal(headers.get("Cache-Control"), "no-store");
  assert.match(headers.get("Content-Security-Policy"), /^default-src 'none'; .*; manifest-src 'self'; worker-src 'self'$/);
  assert.equal([...headers.keys()].length, GATEWAY_HEADERS.length + 1);
});

test("when the gateway last answered is read only as a time in range", () => {
  const now = Date.UTC(2026, 10, 3);
  assert.equal(answeredAt("1700000000123", now), 1_700_000_000_123);
  assert.equal(answeredAt(String(EARLIEST_MS), now), EARLIEST_MS);
  assert.equal(answeredAt(String(now + AHEAD_MS), now), now + AHEAD_MS);
  for (const bad of [null, undefined, "", " 1700000000123", "1700000000123.5", "-1", "1e12",
                     "0x18", "Infinity", "<b>", String(EARLIEST_MS - 1), String(now + AHEAD_MS + 1),
                     "9".repeat(16)]) {
    assert.equal(answeredAt(bad, now), null, String(bad));
  }
});

test("the offline page says the case the worker saw and when the gateway last answered", () => {
  const kept = '<!doctype html>\n<html lang="en">\n<head></head></html>';
  const down = offlineDocument(kept, "down", 1_700_000_000_123.7);
  assert.match(down, /<html data-reach="down" data-last-answered="1700000000123" lang="en">/);
  const away = offlineDocument(kept, "away", null);
  assert.match(away, /<html data-reach="away" lang="en">/);

  assert.deepEqual(offlineInfo({ reach: "down", lastAnswered: "1700000000123" }),
                   { reach: "down", last: new Date(1_700_000_000_123) });
  assert.deepEqual(offlineInfo({ reach: "away" }), { reach: "away", last: null });
  assert.deepEqual(offlineInfo({}), { reach: "away", last: null });
  assert.deepEqual(offlineInfo({ reach: "up", lastAnswered: "soon" }), { reach: "away", last: null });
  assert.deepEqual(offlineInfo({ reach: "down", lastAnswered: "" }), { reach: "down", last: null });
  assert.deepEqual(offlineInfo({ reach: "down", lastAnswered: "99999999999999" }), { reach: "down", last: null });

  const { reach, last } = offlineInfo({ reach: "down", lastAnswered: "1700000000123" });
  assert.match(bandText(reach, "box.example.test", last), /answered, but Moxi isn't running or the gateway is off\. Moxi last answered at/);
  assert.match(bandText("away", "box.example.test", null), /^Can't reach the computer \(box\.example\.test\)/);
  assert.equal(RETRY_MS, 30_000);
});

test("the manifest starts the shell, standalone, in the light set's background, with both icons", () => {
  const value = JSON.parse(manifest(tokens));
  const light = Object.keys(tokens.$sets).find((name) => tokens.$sets[name].mode === "light");
  assert.equal(value.name, "Moxi");
  assert.equal(value.start_url, "/m/ui/");
  assert.equal(value.scope, "/");
  assert.equal(value.display, "standalone");
  assert.equal(value.background_color, tokens.bg.$extensions.moxi.sets[light]);
  assert.equal(value.background_color, "#f6f4f1");
  assert.equal(value.theme_color, value.background_color);
  assert.deepEqual(value.icons.map((icon) => icon.sizes), ["192x192", "512x512"]);
  for (const icon of value.icons) {
    assert.equal(icon.type, "image/png");
    assert.ok(icon.purpose.split(" ").includes("maskable"));
  }
  assert.throws(() => manifest({ ...tokens, bg: { $type: "color", $extensions: { moxi: { sets: { [light]: "white" } } } } }),
                /bg/);
});

test("the built worker is a classic script, as a worker registered without a type must be", () => {
  const built = fs.readFileSync(new URL("../../src/moxi_ui/web/sw.js", import.meta.url), "utf8");
  assert.doesNotThrow(() => new vm.Script(built));
});

test("gen.mjs bundles with the build's own options", () => {
  assert.deepEqual(OPTIONS, BUILD_OPTIONS);
});

const BUILT = new URL("../../src/moxi_ui/web/", import.meta.url);

/** The built worker in a sandbox: its listeners, and a Cache Storage a squatter's page
 * filled with its own `/offline`, `/offline.js` and `client.js`, under the worker's cache
 * name and another, with `answered` as the kept time. The network never answers. */
function sandbox(answered) {
  const planted = (body) => new Response(body, { headers: { "Content-Type": "text/html" } });
  const store = new Map([
    ["/offline", planted("<title>planted</title><script>planted()</script>")],
    ["/offline.js", planted("planted()")],
    ["/m/ui/client.js", planted("planted()")],
    [ANSWERED, new Response(answered)],
  ]);
  const read = [];
  const cache = {
    async match(key) {
      const path = new URL(typeof key === "string" ? key : key.url, ORIGIN).pathname;
      read.push(path);
      return store.get(path)?.clone();
    },
    async put() {},
    async keys() { return []; },
    async delete() { return true; },
  };
  const caches = {
    async open() { return cache; },
    async match(key) { return cache.match(key); },
    async keys() { return [CACHE, "planted"]; },
    async delete() { return true; },
    async has() { return true; },
  };
  const listeners = {};
  const scope = {
    location: { origin: ORIGIN },
    async skipWaiting() {},
    clients: { async claim() {} },
    addEventListener(type, fn) { listeners[type] = fn; },
  };
  const fetch = async () => { throw new TypeError("no network"); };
  vm.runInNewContext(fs.readFileSync(new URL("sw.js", BUILT), "utf8"),
                     { self: scope, caches, fetch, Response, Headers, Request, URL });
  const answer = (path) => new Promise((done, failed) => {
    listeners.fetch({
      request: new Request(url(path)),
      respondWith(promise) { Promise.resolve(promise).then(done, failed); },
      waitUntil() {},
    });
  });
  return { answer, read };
}

test("the built worker answers its own offline page and script, never a stored copy", async () => {
  const script = fs.readFileSync(new URL("offline.js", BUILT), "utf8");
  const page = fs.readFileSync(new URL("../../web/offline.html", import.meta.url), "utf8");
  const { answer, read } = sandbox("1700000000123");
  const js = await answer("/offline.js");
  assert.equal(await js.text(), script);
  assert.equal(js.headers.get("Content-Type"), "text/javascript; charset=utf-8");
  const offline = await answer("/offline");
  const html = await offline.text();
  assert.equal(html, offlineDocument(page, "away", 1_700_000_000_123));
  for (const [name, value] of GATEWAY_HEADERS) assert.equal(offline.headers.get(name), value);
  await assert.rejects(answer("/m/ui/client.js"), /no network/);
  assert.deepEqual([...new Set(read)], [ANSWERED]);
});

test("the built worker shows the offline page when the kept time is bad", async () => {
  for (const bad of ["", "<script>", "99999999999999", "1.5"]) {
    const { answer } = sandbox(bad);
    const html = await (await answer("/offline")).text();
    assert.match(html, /<html data-reach="away" lang="en">/, bad);
    assert.doesNotMatch(html, /data-last-answered/);
  }
});
