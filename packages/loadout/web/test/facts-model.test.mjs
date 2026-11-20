import assert from "node:assert/strict";
import test from "node:test";

import {
  clip, factsReport, itemParts, LABEL, ORDER, PARTS_MAX, parseGiven, TEXT_MAX_CHARS, VERSION,
} from "../src/facts-model.ts";
import { parseListing } from "../src/feed.ts";

const HOME = "/home/me";
const ATLAS = { key: "atlas-api", root: `${HOME}/code/atlas-api` };
const KESTREL = { key: "kestrel-web", root: `${HOME}/code/kestrel-web` };
const GIVEN = [ATLAS, KESTREL];

const section = (name, where = "key", root = null) => ({ name, where, root });
const item = (kind, name, scope) => ({ kind, name, scope, harnesses: null, source: `${HOME}/my-loadout/x`,
                                       disabled: [] });
/** `/api/list`'s answer: Your items, and atlas-api's section by key with its two items. */
const listing = (over = {}) => ({
  problem: null, manifest: `${HOME}/my-loadout/loadout.toml`,
  sections: [section("user", "user"), section("atlas-api")],
  items: [item("skill", "tidy-notes", "user"), item("skill", "release-checklist", "atlas-api"),
          item("agent", "api-lead", "atlas-api")],
  ...over,
});
const LINK = { text: "Loadout", module: "loadout", route: "project/atlas-api" };
const report = (over = {}, open = "atlas-api", given = GIVEN) => factsReport(listing(over), given, open);
const texts = (found, key = "atlas-api") => found.projects[key].sections[0].lines[0].parts.map((part) => part.text);

// -- the frame -------------------------------------------------------------------------------

test("a good read reports version 1, order 80, no activity and nothing gathered", () => {
  const found = report({}, null);
  assert.deepEqual(found, { version: VERSION, order: ORDER, projects: {} });
  assert.deepEqual([VERSION, ORDER, LABEL], [1, 80, "Loadout"]);
});

test("the opened project gets one `Loadout` row: one line of `<kind> <name>`, linked to its filtered view", () => {
  assert.deepEqual(report(), { version: 1, order: 80, projects: { "atlas-api": { sections: [
    { label: "Loadout", lines: [{ parts: [{ text: "skill release-checklist" }, { text: "agent api-lead" }] }],
      link: LINK }] } } });
});

test("only the opened project gets a row, and only when the page gave it", () => {
  assert.deepEqual(report({}, null).projects, {});
  assert.deepEqual(report({}, "kestrel-web").projects, {});
  assert.deepEqual(report({}, "atlas-api", [KESTREL]).projects, {});
  assert.deepEqual(report({}, "ATLAS-API").projects, {}, "the opened key is one the page gave, as given");
});

test("the link's route writes the key as loadout's address does", () => {
  const given = [{ key: "café", root: `${HOME}/code/cafe` }];
  const found = factsReport(listing({ sections: [section("Café")], items: [item("skill", "a", "Café")] }),
                            given, "café");
  assert.equal(found.projects["café"].sections[0].link.route, "project/caf!C3!A9");
});

// -- the section match -----------------------------------------------------------------------

test("an item is joined to its section by `scope`: another section's items, and Your items, stay out", () => {
  const found = report({ sections: [section("user", "user"), section("atlas-api"), section("kestrel-web")],
                         items: [item("skill", "tidy-notes", "user"), item("skill", "release-checklist", "atlas-api"),
                                 item("skill", "deploy", "kestrel-web"), item("agent", "api-lead", "atlas-api")] });
  assert.deepEqual(texts(found), ["skill release-checklist", "agent api-lead"]);
});

test("a key section is matched ignoring case, and its items carry the section's own name", () => {
  const found = report({ sections: [section("Atlas-API")],
                         items: [item("skill", "release-checklist", "Atlas-API")] });
  assert.deepEqual(texts(found), ["skill release-checklist"]);
  assert.equal(found.projects["atlas-api"].sections[0].link.route, "project/atlas-api");
});

test("a path section at the project's root is matched; one under the root, or beside it, is not", () => {
  const at = section(`${HOME}/code/atlas-api`, "path", `${HOME}/code/atlas-api`);
  const found = report({ sections: [at], items: [item("agent", "api-lead", at.name)] });
  assert.deepEqual(texts(found), ["agent api-lead"]);
  for (const root of [`${HOME}/code/atlas-api/server`, `${HOME}/code/atlas-api2`, `${HOME}/code/atlas-api/`]) {
    const other = section(root, "path", root);
    assert.deepEqual(report({ sections: [other], items: [item("agent", "api-lead", root)] }).projects, {}, root);
  }
});

test("a key section is the project's before a path section at its root, as on the Loadout page", () => {
  const at = section(`${HOME}/code/atlas-api`, "path", `${HOME}/code/atlas-api`);
  const found = report({ sections: [at, section("atlas-api")],
                         items: [item("skill", "by-path", at.name), item("skill", "by-key", "atlas-api")] });
  assert.deepEqual(texts(found), ["skill by-key"]);
});

test("a path section named like the key is no key section, and the user section is never a project's", () => {
  assert.deepEqual(report({ sections: [section("atlas-api", "path", "/elsewhere")],
                            items: [item("skill", "x", "atlas-api")] }).projects, {});
  const user = { key: "user", root: `${HOME}/code/user` };
  assert.deepEqual(factsReport(listing(), [user], "user").projects, {});
});

test("a project with a section that lists nothing, or no section, has no row", () => {
  assert.deepEqual(report({ items: [item("skill", "tidy-notes", "user")] }).projects, {});
  assert.deepEqual(report({ sections: [section("user", "user")], items: [] }).projects, {});
});

// -- the line --------------------------------------------------------------------------------

test("the items keep loadout's order, every kind as loadout names it", () => {
  const kinds = ["skill", "agent", "hook", "permissions", "mcp"];
  const found = report({ items: kinds.map((kind) => item(kind, `${kind}-one`, "atlas-api")) });
  assert.deepEqual(texts(found), kinds.map((kind) => `${kind} ${kind}-one`));
});

test("up to eight items are all drawn; past eight, the first seven and `<n> more`", () => {
  const items = (n) => Array.from({ length: n }, (_, i) => ({ kind: "skill", name: `s${i + 1}` }));
  assert.equal(PARTS_MAX, 8);
  assert.deepEqual(itemParts(items(8)).map((part) => part.text), items(8).map((one) => `skill ${one.name}`));
  const nine = itemParts(items(9)).map((part) => part.text);
  assert.deepEqual(nine, [...items(7).map((one) => `skill ${one.name}`), "2 more"]);
  const many = itemParts(items(30));
  assert.equal(many.length, PARTS_MAX);
  assert.deepEqual(many.at(-1), { text: "23 more" });
  assert.deepEqual(itemParts([]), []);
});

test("a long name is cut to the contract's longest, ending `…`", () => {
  const name = "n".repeat(TEXT_MAX_CHARS + 5);
  const [part] = itemParts([{ kind: "skill", name }]);
  assert.equal(Array.from(part.text).length, TEXT_MAX_CHARS);
  assert.ok(part.text.endsWith("…"));
  assert.equal(clip("short"), "short");
});

// -- what loadout can't say ------------------------------------------------------------------

test("a list loadout can't read (`problem` set) reports null", () => {
  assert.equal(report({ problem: "loadout.toml can't be read: line 3", sections: [], items: [] }), null);
  assert.equal(report({ problem: "loadout.toml can't be read" }), null, "whatever else the answer holds");
});

test("a failed read reports null", () => {
  assert.equal(factsReport(null, GIVEN, "atlas-api"), null);
  assert.equal(factsReport(null, GIVEN, null), null);
});

// -- the given projects and the route's shape ------------------------------------------------

test("the given projects: each with a key and a root, the rest left out; anything else gives none", () => {
  const text = JSON.stringify([{ key: "atlas-api", root: "/w/a", lead: "kim", associated: ["x"] },
                               { key: "no-root" }, { key: 3, root: "/w/b" }, "atlas-api", null]);
  assert.deepEqual(parseGiven(text), [{ key: "atlas-api", root: "/w/a" }]);
  for (const bad of [null, "", "not json", "{}", '"x"']) assert.deepEqual(parseGiven(bad), [], String(bad));
});

test("`/api/list`'s answer parses, and one that isn't its shape gives null", () => {
  assert.deepEqual(parseListing(listing()), listing());
  assert.deepEqual(parseListing(listing({ problem: "no", manifest: null, sections: [], items: [] })).problem, "no");
  for (const bad of [null, [], { ...listing(), problem: 3 }, { ...listing(), sections: [{ name: "x" }] },
                     { ...listing(), items: [{ kind: "skill", name: "x" }] }, { problem: null, manifest: null }]) {
    assert.equal(parseListing(bad), null, JSON.stringify(bad));
  }
});
