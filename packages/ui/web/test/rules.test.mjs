import assert from "node:assert/strict";
import test from "node:test";

import { apiTarget, checkAttributes, panelTarget, scriptUrl } from "../src/rules.ts";

const ORIGIN = "http://127.0.0.1:8460";

test("api paths stay under the module's own prefix", () => {
  assert.equal(apiTarget("fixture", "api/summary", ORIGIN), "/m/fixture/api/summary");
  assert.equal(apiTarget("ui", "api/settings?module=cache", ORIGIN), "/m/ui/api/settings?module=cache");
  for (const path of ["/m/other/api", "../other/api", "api/../../other", "%2e%2e/other/api",
                      "http://elsewhere/x", "//elsewhere/x", "api\\x", 7]) {
    assert.throws(() => apiTarget("fixture", path, ORIGIN), TypeError, String(path));
  }
  assert.throws(() => apiTarget("Fixture", "api", ORIGIN), TypeError);
});

test("navigate's routes stay under the module's prefix", () => {
  assert.deepEqual(panelTarget("ui", "", ORIGIN), { url: "/m/ui/", route: "" });
  assert.deepEqual(panelTarget("ui", "/settings?module=x", ORIGIN), { url: "/m/ui/settings?module=x", route: "settings?module=x" });
  assert.throws(() => panelTarget("ui", "../fixture/", ORIGIN), TypeError);
});

const chip = {
  name: "chip", element: "moxi-cache-chip", script: "chip.js", url: "/m/cache/chip.js",
  title: "t", doc: "d", contract: 1, place: "inline", home: false,
  attributes: [
    { name: "session", type: "string", values: [], required: true, doc: "" },
    { name: "count", type: "int", values: [], required: false, doc: "" },
    { name: "dim", type: "bool", values: [], required: false, doc: "" },
    { name: "size", type: "enum", values: ["s", "m"], required: false, doc: "" },
  ],
};

test("attributes are declared, of their type, and the required ones given", () => {
  checkAttributes(chip, { session: "abc", count: "-3", dim: "true", size: "m" });
  assert.throws(() => checkAttributes(chip, {}), /needs its attribute session/);
  assert.throws(() => checkAttributes(chip, { session: "a", colour: "red" }), /not declared/);
  assert.throws(() => checkAttributes(chip, { session: "a", count: "1.5" }), /is a int/);
  assert.throws(() => checkAttributes(chip, { session: "a", dim: "yes" }), /is a bool/);
  assert.throws(() => checkAttributes(chip, { session: "a", size: "xl" }), /is a enum/);
  assert.throws(() => checkAttributes(chip, { session: 1 }), /as a string/);
  // A page takes the route the shell gives it; nothing else takes it undeclared.
  checkAttributes({ ...chip, place: "page", attributes: [] }, { route: "a/b" });
  assert.throws(() => checkAttributes(chip, { session: "a", route: "x" }), /not declared/);
});

test("a script is loaded only from its own module's prefix on this origin", () => {
  assert.equal(scriptUrl(chip, "cache", ORIGIN), `${ORIGIN}/m/cache/chip.js`);
  assert.equal(scriptUrl({ ...chip, url: "/m/other/chip.js" }, "cache", ORIGIN), null);
  assert.equal(scriptUrl({ ...chip, url: "http://elsewhere/m/cache/chip.js" }, "cache", ORIGIN), null);
});
