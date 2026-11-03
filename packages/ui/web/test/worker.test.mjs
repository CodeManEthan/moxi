import assert from "node:assert/strict";
import * as fs from "node:fs";
import test from "node:test";
import * as vm from "node:vm";

import { ICON_SIZES, manifest } from "../gen.mjs";
import { bandText } from "../src/gateway-model.ts";
import { PUSH_URL } from "../src/push.ts";
import {
  answerReach, handling, KEPT, navigationReach, OFFLINE, offlineDocument, offlineInfo, RETRY_MS, useKept,
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

test("a kept file answers from the cache only when the gateway can't", () => {
  for (const status of [null, 502, 503, 504]) assert.equal(useKept(status), true);
  for (const status of [200, 304, 403, 404]) assert.equal(useKept(status), false);
});

test("the worker keeps the app shell and the offline page, and nothing else", () => {
  assert.deepEqual([...KEPT].sort(), [
    "/icon-192.png", "/icon-512.png", "/m/ui/client.js", "/m/ui/theme.css", "/manifest.webmanifest",
    "/offline", "/offline.js",
  ]);
  assert.ok(KEPT.includes(OFFLINE));
  for (const size of ICON_SIZES) assert.ok(KEPT.includes(`/icon-${size}.png`));
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

  const { reach, last } = offlineInfo({ reach: "down", lastAnswered: "1700000000123" });
  assert.match(bandText(reach, "box.example.test", last), /answered, but Moxi isn't running or the gateway is off\. Moxi last answered at/);
  assert.match(bandText("away", "box.example.test", null), /^Can't reach the computer \(box\.example\.test\)/);
  assert.equal(RETRY_MS, 30_000);
});

test("the manifest starts the shell, standalone, in the theme's colour, with both icons", () => {
  const value = JSON.parse(manifest(tokens));
  assert.equal(value.name, "Moxi");
  assert.equal(value.start_url, "/m/ui/");
  assert.equal(value.scope, "/");
  assert.equal(value.display, "standalone");
  assert.equal(value.background_color, tokens.bg.$value);
  assert.equal(value.theme_color, tokens.bg.$value);
  assert.deepEqual(value.icons.map((icon) => icon.sizes), ["192x192", "512x512"]);
  for (const icon of value.icons) {
    assert.equal(icon.type, "image/png");
    assert.ok(icon.purpose.split(" ").includes("maskable"));
  }
  assert.throws(() => manifest({ ...tokens, bg: { $value: "white" } }), /bg/);
});

test("the built worker is a classic script, as a worker registered without a type must be", () => {
  const built = fs.readFileSync(new URL("../../src/moxi_ui/web/sw.js", import.meta.url), "utf8");
  assert.doesNotThrow(() => new vm.Script(built));
});
