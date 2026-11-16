import assert from "node:assert/strict";
import test from "node:test";

import {
  FIND_HELP, FIND_ROWS, RANK_KEY, RANK_LABEL, RANK_WORDS, addressOf, changedView, find, hits, parseAddress, rankOf,
} from "../src/find-model.ts";
import { paneKeys } from "../src/settings-model.ts";

function key(name, type, value, fallback, extra = {}) {
  return { name, type, json: JSON.stringify(value), default_json: JSON.stringify(fallback), source: "file",
           mark: "applies now", doc: `the ${name}`, label: null, line: null, common: false, group: null, unit: null,
           choices: null, pick: false, hurts: null, terminal: null, elsewhere: null, needs_absent: false, locked: false,
           changed: JSON.stringify(value) !== JSON.stringify(fallback), ...extra };
}

const section = (module, keys, extra = {}) => ({ module, keys, error: null, fix: null, readonly: false, file: null,
                                                 count: paneKeys(module, keys).length, ...extra });

const UI = [
  key("port", "int", 18460, 8460, { label: "Port", line: "Where this page is served, on this computer only.", common: true }),
  key("idle_s", "int", 900, 900, { label: "Idle shutdown", unit: "seconds" }),
  key("gateway.enabled", "bool", true, false, { label: "Phone access", terminal: { on: "a", off: "b" } }),
  key("gateway.port", "int", 18461, 8461, { label: "Phone port" }),
];
const LAUNCHER = [
  key("ready.wait_s", "int", 60, 60, { label: "For a started session to be ready" }),
  key("first.wait_s", "int", 120, 120, { label: "For the first turn to start" }),
  key("startup.wait_s", "int", 20, 20, { label: "For startup screens, in a slot" }),
  key("page.launch_wait_s", "int", 600, 600, { label: "A launch from the page may take" }),
  key("relaunch.wait_s", "int", 900, 900, { label: "For the session to be quiet" }),
  key("recycle.limit_wait_s", "int", 1800, 1800, { label: "For idle, to compact at the limit" }),
  key("name.template", "string", "", "", { elsewhere: "The session name is edited in Home's Options, with the launch it names." }),
  key("runs.keep_days", "int", 14, 30, { label: "Keep run files for", doc: "A launch's prompt file and wait log." }),
];
const CORE = [key("skills.harnesses", "list", ["claude"], ["claude", "codex"], { label: "Skills are placed for" })];

const all = () => new Map([["moxi", section("moxi", CORE)], ["ui", section("ui", UI)], ["launcher", section("launcher", LAUNCHER)]]);
const LISTED = ["moxi", "launcher", "ui"];

test("a key matches by its label, then its key, then its line or value", () => {
  const port = UI[0];
  assert.equal(rankOf(port, "port"), RANK_LABEL);
  assert.equal(rankOf(key("a.port_x", "int", 1, 1, { label: "Something" }), "port"), RANK_KEY);
  assert.equal(rankOf(port, "computer"), RANK_WORDS);
  assert.equal(rankOf(port, "18460"), RANK_WORDS);
  assert.equal(rankOf(port, "nothing"), null);
  // A key with no label is matched by its key as its label.
  assert.equal(rankOf(key("snapshot.timer", "bool", false, false), "timer"), RANK_LABEL);
});

test("results group by module in the list's order, labels ahead of keys ahead of sentences", () => {
  const found = find("port", LISTED, all(), true);
  assert.equal(found.count, "2 settings in 1 module");
  assert.deepEqual(found.groups.map((g) => [g.module, g.tally, g.keys.map((k) => k.name)]),
                   [["ui", "2 of 4", ["port", "gateway.port"]]]);
  // Labels first: a key matched only by its line comes after one matched by its label.
  const order = find("served", LISTED, all(), true).groups[0].keys.map((k) => k.name);
  assert.deepEqual(order, ["port"]);
  const ranked = find("s", ["moxi", "launcher"], all(), true, new Set(["launcher"])).groups[1].keys.map((k) => k.name);
  assert.deepEqual(ranked.slice(0, 2), ["ready.wait_s", "first.wait_s"]);
  assert.equal(found.none, null);
  assert.equal(found.reading, null);
});

test("ui's gateway keys match only while the Phone group shows", () => {
  const found = find("phone", LISTED, all(), false);
  assert.deepEqual(found.groups, []);
  assert.equal(found.none, "No setting matches “phone”");
  assert.deepEqual(find("phone", LISTED, all(), true).groups.map((g) => g.tally), ["2 of 4"]);
});

test("a module shows four rows, then how many more, until Show them", () => {
  const found = find("wait", LISTED, all(), true);
  const [launcher] = found.groups;
  assert.equal(launcher.keys.length, FIND_ROWS);
  assert.equal(launcher.tally, "6 of 7");
  assert.equal(launcher.more, "2 more in launcher · Show them");
  const shown = find("wait", LISTED, all(), true, new Set(["launcher"]));
  assert.equal(shown.groups[0].keys.length, 6);
  assert.equal(shown.groups[0].more, null);
});

test("a match on a key edited elsewhere adds its sentence under the results", () => {
  const found = find("name", LISTED, all(), true);
  assert.deepEqual(found.elsewhere, ["The session name is edited in Home's Options, with the launch it names."]);
  assert.equal(found.groups.length, 0);
});

test("no match says so, and only once every section is in", () => {
  const sections = all();
  sections.delete("launcher");
  const partial = find("zzz", LISTED, sections, true);
  assert.equal(partial.none, null);
  assert.equal(partial.reading, "still reading 1 module");
  const done = find("zzz", LISTED, all(), true);
  assert.equal(done.none, "No setting matches “zzz”");
  assert.equal(FIND_HELP, "Search reads every installed module's settings. Pick a module at the left to see all of its settings.");
});

test("a section arriving late is searched once it does", () => {
  const sections = all();
  const late = sections.get("launcher");
  sections.delete("launcher");
  assert.deepEqual(find("keep", LISTED, sections, true).groups, []);
  sections.set("launcher", late);
  assert.deepEqual(find("keep", LISTED, sections, true).groups.map((g) => g.module), ["launcher"]);
});

test("a section that can't be read is not searched", () => {
  const sections = all();
  sections.set("launcher", section("launcher", [], { error: "won't parse" }));
  assert.deepEqual(find("wait", LISTED, sections, true).groups, []);
});

test("matches are cut for the underline, in any case", () => {
  assert.deepEqual(hits("Phone port", "PORT"), [{ text: "Phone ", hit: false }, { text: "port", hit: true }]);
  assert.deepEqual(hits("a.b.a", "a"), [{ text: "a", hit: true }, { text: ".b.", hit: false }, { text: "a", hit: true }]);
  assert.deepEqual(hits("text", " "), [{ text: "text", hit: false }]);
});

test("the changed view: each module's changed drawn keys with its tally", () => {
  const view = changedView(LISTED, all(), true, "ui");
  assert.equal(view.count, "5 settings changed from the default, in 3 modules");
  assert.equal(view.back, "Back to ui");
  assert.deepEqual(view.groups.map((g) => [g.module, g.tally, g.keys.map((k) => k.name)]), [
    ["moxi", "1 of 1", ["skills.harnesses"]], ["ui", "3 of 4", ["port", "gateway.enabled", "gateway.port"]],
    ["launcher", "1 of 7", ["runs.keep_days"]]]);
  const small = changedView(LISTED, all(), false, "moxi");
  assert.deepEqual(small.groups.find((g) => g.module === "ui").tally, "1 of 2");
  const sections = all();
  sections.delete("moxi");
  assert.equal(changedView(LISTED, sections, true, "ui").reading, "still reading 1 module");
});

// --- addresses ---------------------------------------------------------------------------------------

test("each address opens what it names", () => {
  const listed = ["moxi", "ui", "usage"];
  const base = { view: "module", module: "moxi", more: false, pair: false, missing: null };
  assert.deepEqual(parseAddress("settings", listed), base);
  assert.deepEqual(parseAddress("/settings/", listed), base);
  assert.deepEqual(parseAddress("settings/usage", listed), { ...base, module: "usage" });
  assert.deepEqual(parseAddress("settings/usage/more", listed), { ...base, module: "usage", more: true });
  assert.deepEqual(parseAddress("settings/ui/pair", listed), { ...base, module: "ui", pair: true });
  assert.deepEqual(parseAddress("settings/usage/pair", listed), { ...base, module: "usage" });
  assert.deepEqual(parseAddress("settings/changed", listed), { ...base, view: "changed" });
});

test("a module named changed that is listed wins; a name not listed opens the core with a line", () => {
  assert.deepEqual(parseAddress("settings/changed", ["moxi", "changed"]),
                   { view: "module", module: "changed", more: false, pair: false, missing: null });
  assert.deepEqual(parseAddress("settings/cache", ["moxi", "ui"]),
                   { view: "module", module: "moxi", more: false, pair: false, missing: "cache has no settings here." });
});

test("the page writes the address of what it shows", () => {
  const at = (view, module, more = false, pair = false) => addressOf({ view, module, more, pair });
  assert.equal(at("module", "moxi"), "settings");
  assert.equal(at("module", "moxi", true), "settings/moxi/more");
  assert.equal(at("module", "usage"), "settings/usage");
  assert.equal(at("module", "usage", true), "settings/usage/more");
  assert.equal(at("module", "ui", false, true), "settings/ui/pair");
  assert.equal(at("changed", "ui"), "settings/changed");
  for (const route of ["settings", "settings/usage", "settings/usage/more", "settings/ui/pair", "settings/changed"]) {
    assert.equal(addressOf(parseAddress(route, ["moxi", "ui", "usage"])), route);
  }
});

test("within a module, label matches come ahead of key matches, ahead of line and value matches", () => {
  const keys = [key("a", "int", 1, 1, { label: "First", line: "about the trust screen" }),
                key("trust.b", "int", 1, 1, { label: "Second" }),
                key("c", "int", 1, 1, { label: "Trust third" })];
  const found = find("trust", ["moxi", "demo"], new Map([["moxi", section("moxi", [])], ["demo", section("demo", keys)]]), true);
  assert.deepEqual(found.groups[0].keys.map((k) => k.name), ["c", "trust.b", "a"]);
});
