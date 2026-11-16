import assert from "node:assert/strict";
import test from "node:test";

import {
  addressPath, filteredView, goneLine, parseRoute, pathToWrite, projectNamed, routeOf, sectionFor,
} from "../src/address-model.ts";
import { itemsView } from "../src/items-model.ts";
import { placedView, problemsOf } from "../src/placed-model.ts";
import { fullView, HOME, withRow } from "./fixtures.mjs";

const ADDRESSES = [
  ["", { piece: "page" }],
  ["item/user/skill/tidy-notes", { piece: "item", section: "user", kind: "skill", name: "tidy-notes" }],
  ["item/%2Fw%2Fmy%20app/skill/demo%2Fmoxi-demo", { piece: "item", section: "/w/my app", kind: "skill",
                                                     name: "demo/moxi-demo" }],
  ["global", { piece: "global" }],
  ["kind/user/agent", { piece: "kind", section: "user", kind: "agent" }],
  ["add/skill/bump-version", { piece: "add", kind: "skill", name: "bump-version" }],
  ["add", { piece: "add", kind: null, name: null }],
  ["replace/user/agent%3Acode-reviewer%40codex", { piece: "replace", section: "user", id: "agent:code-reviewer@codex" }],
  ["project/atlas-api", { piece: "project", key: "atlas-api" }],
];

test("every address parses from its route and writes it back", () => {
  for (const [route, address] of ADDRESSES) {
    assert.deepEqual(parseRoute(route), address, route);
    assert.equal(routeOf(address), route);
  }
  assert.equal(addressPath({ piece: "global" }), "/m/loadout/global");
});

test("a route naming nothing the page opens is the page", () => {
  for (const route of ["item/user/skill", "nonsense", "item//skill/x", "project/%E0%A4%A", "global/x", "add/skill"]) {
    assert.deepEqual(parseRoute(route), { piece: "page" }, route);
  }
});

test("the address is written only when it changes, and only at the page", () => {
  assert.equal(pathToWrite({ piece: "global" }, "/m/loadout/"), "/m/loadout/global");
  assert.equal(pathToWrite({ piece: "global" }, "/m/loadout/global"), null);
  assert.equal(pathToWrite({ piece: "global" }, "/m/usage/"), null);
});

test("an address whose item is gone opens nothing, with a dim line", () => {
  const view = withRow(fullView(), { name: "code-reviewer", harness: "codex" }, { state: "modified", replaceable: true });
  const problems = problemsOf(view);
  const gone = (route, unlisted = null, projects = null) => goneLine(parseRoute(route), view, problems, unlisted, projects);
  assert.equal(gone("item/user/skill/tidy-notes"), null);
  assert.equal(gone("item/user/skill/gone"), "Your items no longer lists the skill gone.");
  assert.equal(gone("kind/atlas-api/mcp"), "atlas-api lists no mcp servers now.");
  assert.equal(gone("kind/user/skill"), null);
  assert.equal(gone("replace/user/agent%3Acode-reviewer%40codex"), null);
  assert.equal(gone("replace/user/agent%3Acode-reviewer%40claude"), "Nothing at agent:code-reviewer@claude needs Replace now.");
  assert.equal(gone("add/skill/bump-version", new Set(["skill/bump-version"])), null);
  assert.equal(gone("add/skill/bump-version", new Set()), "bump-version is no longer a file no section lists.");
  assert.equal(gone("project/nope", null, [{ key: "atlas-api", root: "/r" }]), "No project is named nope.");
  assert.equal(gone("global"), null);
});

const PROJECTS = [{ key: "Atlas-API", root: `${HOME}/code/atlas-api` }, { key: "kestrel", root: `${HOME}/code/kestrel` }];

test("the filtered join: the key section ignoring case, or the path section by its root exactly", () => {
  const view = fullView();
  view.sections.push({ name: `${HOME}/code/kestrel`, where: "path", root: `${HOME}/code/kestrel`, refused: null });
  assert.equal(projectNamed(PROJECTS, "atlas-api").key, "Atlas-API");
  assert.equal(sectionFor(PROJECTS[0], view), "atlas-api");
  assert.equal(sectionFor(PROJECTS[1], view), `${HOME}/code/kestrel`);
  assert.equal(sectionFor({ key: "other", root: `${HOME}/code/kestrel/` }, view), null);
});

test("the filtered view: its line, its problems, its own items, then Yours too", () => {
  let view = withRow(fullView(), { name: "api-lead", harness: "codex" }, { state: "modified", form: "generated" });
  view = withRow(view, { name: "tidy-notes", harness: "claude" }, { state: "missing" });
  view.sections.push({ name: "other", where: "key", root: null, refused: null });
  view.items.push({ ...view.items[0], scope: "other" });
  view.rows.push({ ...view.rows[0], scope: "other", state: "missing" });
  const items = itemsView(view);
  const filtered = filteredView("atlas-api", PROJECTS, view, items, placedView(view, items));
  assert.equal(filtered.line, "Project Atlas-API ▾ · what sessions in ~/code/atlas-api load");
  assert.deepEqual(filtered.problems.map((p) => [p.section, p.name]), [["atlas-api", "api-lead"], ["user", "tidy-notes"]]);
  assert.deepEqual([filtered.own.label, filtered.own.count], ["Atlas-API", "2 of its own"]);
  assert.deepEqual([filtered.yours.label, filtered.yours.count], ["Yours too", "8 items"]);
  assert.equal(filtered.yours.group.global.label, "Global file");
  const none = filteredView("kestrel", PROJECTS, view, items, placedView(view, items));
  assert.equal(none.own, null);
  assert.equal(filteredView("nope", PROJECTS, view, items, placedView(view, items)), null);
});
