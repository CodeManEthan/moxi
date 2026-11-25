import assert from "node:assert/strict";
import test from "node:test";

import {
  addressPath, closed, filteredView, goneLine, goneLineOf, itemAddress, NOTHING_OPEN, openKind, opensItem, parseRoute,
  pathToWrite, projectChoices, projectNamed, routeOf, sameAddress, sectionFor, sectionsFor, unfilteredLine,
} from "../src/address-model.ts";
import { problemsOf } from "../src/placed-model.ts";
import { fullView, HOME, withRow } from "./fixtures.mjs";

const ADDRESSES = [
  ["", { piece: "page" }],
  ["item/user/skill/tidy-notes", { piece: "item", section: "user", kind: "skill", name: "tidy-notes" }],
  ["item/!2Fw!2Fmy!20app/skill/demo!2Fmoxi-demo", { piece: "item", section: "/w/my app", kind: "skill",
                                                     name: "demo/moxi-demo" }],
  ["item/~!2Fcode!2Fkestrel/agent/api-lead", { piece: "item", section: "~/code/kestrel", kind: "agent", name: "api-lead" }],
  ["kind/C:!5Cw!5Capp/mcp", { piece: "kind", section: "C:\\w\\app", kind: "mcp" }],
  ["global", { piece: "global" }],
  ["kind/user/agent", { piece: "kind", section: "user", kind: "agent" }],
  ["add/skill/bump-version", { piece: "add", kind: "skill", name: "bump-version" }],
  ["add", { piece: "add", kind: null, name: null }],
  ["replace/user/agent:code-reviewer@codex", { piece: "replace", section: "user", id: "agent:code-reviewer@codex" }],
  ["replace/~!2Fcode!2Fkestrel/permissions:git-reads@codex", { piece: "replace", section: "~/code/kestrel",
                                                               id: "permissions:git-reads@codex" }],
  ["project/caf!C3!A9", { piece: "project", key: "café" }],
  ["project/atlas-api", { piece: "project", key: "atlas-api" }],
];

test("every address parses from its route and writes it back", () => {
  for (const [route, address] of ADDRESSES) {
    assert.deepEqual(parseRoute(route), address, route);
    assert.equal(routeOf(address), route);
  }
  assert.equal(addressPath({ piece: "global" }), "/m/loadout/global");
});

test("no address the page writes holds a byte the shell refuses to load", () => {
  // The shell loads a navigation whose path holds no `%`; a browser keeps these bytes as
  // they are in a path, and percent-encodes the rest.
  for (const [route] of ADDRESSES) assert.match(route, /^[A-Za-z0-9._~:@!/-]*$/, route);
  const odd = { piece: "item", section: "~/a b/100%!x", kind: "skill", name: "q?#" };
  assert.match(routeOf(odd), /^[A-Za-z0-9._~:@!/-]*$/);
  assert.deepEqual(parseRoute(routeOf(odd)), odd);
});

test("a route whose parts are URI-encoded opens the same thing", () => {
  assert.deepEqual(parseRoute("replace/user/agent%3Acode-reviewer%40codex"),
                   { piece: "replace", section: "user", id: "agent:code-reviewer@codex" });
  assert.deepEqual(parseRoute("item/~%2Fcode%2Fkestrel/skill/review-diff"),
                   { piece: "item", section: "~/code/kestrel", kind: "skill", name: "review-diff" });
});

test("a route naming nothing the page opens is the page", () => {
  for (const route of ["item/user/skill", "nonsense", "item//skill/x", "project/%E0%A4%A", "global/x", "add/skill",
                       "project/a!2", "project/!ZZ", "project/!C3"]) {
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
  assert.equal(gone("replace/user/agent:code-reviewer@codex"), null);
  assert.equal(gone("replace/user/agent:code-reviewer@claude"), "Nothing at agent:code-reviewer@claude needs Replace now.");
  assert.equal(gone("add/skill/bump-version", new Set(["skill/bump-version"])), null);
  assert.equal(gone("add/skill/bump-version", new Set()), "bump-version is no longer a file no section lists.");
  assert.equal(gone("project/nope", null, [{ key: "atlas-api", root: "/r" }]), "No project is named nope.");
  assert.equal(gone("global"), null);
});

const PROJECTS = [{ key: "Atlas-API", root: `${HOME}/code/atlas-api` }, { key: "kestrel", root: `${HOME}/code/kestrel` }];

test("the filtered join: the key section ignoring case, or the path section by its root exactly", () => {
  const view = fullView();
  view.sections.push({ name: `${HOME}/code/kestrel`, where: "path", root: `${HOME}/code/kestrel`, project: null,
                      refused: null });
  assert.equal(projectNamed(PROJECTS, "atlas-api").key, "Atlas-API");
  assert.equal(sectionFor(PROJECTS[0], view), "atlas-api");
  assert.equal(sectionFor(PROJECTS[1], view), `${HOME}/code/kestrel`);
  assert.equal(sectionFor({ key: "other", root: `${HOME}/code/kestrel/` }, view), null);
});

test("the filtered view: its line, Placed over its section and Your items, its own items, then Yours too", () => {
  let view = withRow(fullView(), { name: "api-lead", harness: "codex" }, { state: "modified", form: "generated" });
  view = withRow(view, { name: "tidy-notes", harness: "claude" }, { state: "missing" });
  view.sections.push({ name: "other", where: "key", root: null, refused: null });
  view.items.push({ ...view.items[0], scope: "other" });
  view.rows.push({ ...view.rows[0], scope: "other", state: "missing" });
  view.rows.push({ ...view.rows[0], scope: null, state: "stale", name: "dropped" });
  view.items.push({ ...view.items[0], name: "moxi-help", module: "launcher" });
  view.rows.push({ ...view.rows[0], name: "moxi-help", state: "ok" });
  const filtered = filteredView("atlas-api", PROJECTS, view);
  assert.deepEqual([filtered.key, filtered.where], ["Atlas-API", "what sessions in ~/code/atlas-api load"]);
  // Placed counts only the project's section and Your items: not `other`, not a removal
  // whose record names no section.
  assert.deepEqual(filtered.placed.problems.map((p) => [p.section, p.name]),
                   [["atlas-api", "api-lead"], ["user", "tidy-notes"]]);
  assert.equal(filtered.placed.verdict, "2 items need you");
  assert.equal(filtered.placed.counts, "Claude Code 10 of 11 placed · Codex 8 of 9 · OpenCode 8 of 8");
  assert.deepEqual(filtered.own.map((o) => [o.label, o.count]), [["Atlas-API", "2 of its own"]]);
  assert.deepEqual(filtered.own[0].group.rows.map((r) => r.name), ["release-checklist", "api-lead"]);
  assert.deepEqual([filtered.yours.label, filtered.yours.count], ["Yours too", "8 items"]);
  assert.equal(filtered.yours.group.global.label, "Global file");
  assert.deepEqual(filtered.modules.map((g) => g.label), ["launcher"]);
  // A project with no section shows Placed over Your items and Yours too.
  const none = filteredView("kestrel", PROJECTS, view);
  assert.deepEqual(none.own, []);
  assert.deepEqual(none.placed.problems.map((p) => p.name), ["tidy-notes"]);
  assert.equal(filteredView("nope", PROJECTS, view), null);
});

const path = (root, project) => ({ name: root, where: "path", root, project, refused: null });

test("every section of the project: its key section, then path sections at its root or under it", () => {
  const view = fullView();
  const web = `${HOME}/code/atlas-api/web`;
  view.sections.push(path(web, "Atlas-API"), path(`${HOME}/code/atlas-api`, "Atlas-API"),
                     path(`${HOME}/code/atlas-api/docs`, null), path(`${HOME}/code/kestrel/app`, "kestrel"));
  assert.deepEqual(sectionsFor(PROJECTS[0], view), ["atlas-api", web, `${HOME}/code/atlas-api`]);
  // Without projects' map, only a path section at the root exactly.
  assert.deepEqual(sectionsFor(PROJECTS[1], view), [`${HOME}/code/kestrel/app`]);
  assert.deepEqual(sectionsFor({ key: "kestrel", root: `${HOME}/code/kestrel` },
                               { sections: [path(`${HOME}/code/kestrel`, null)] }), [`${HOME}/code/kestrel`]);
  assert.deepEqual(sectionsFor({ key: "otter", root: "/w/otter" }, view), []);
  // Add's narrower test: a project with only a section under its root has none at its root.
  assert.equal(sectionFor(PROJECTS[1], view), null);
});

test("the filtered view of a project with a key section and a path section under its root shows both", () => {
  const web = `${HOME}/code/atlas-api/web`;
  const view = fullView();
  view.sections.push(path(web, "Atlas-API"), path(`${HOME}/code/elsewhere`, null));
  view.items.push({ ...view.items[0], name: "lint-web", scope: web });
  view.rows.push({ ...view.rows[0], name: "lint-web", scope: web, state: "missing" });
  view.items.push({ ...view.items[0], name: "far", scope: `${HOME}/code/elsewhere` });
  view.rows.push({ ...view.rows[0], name: "far", scope: `${HOME}/code/elsewhere`, state: "missing" });
  const filtered = filteredView("atlas-api", PROJECTS, view);
  assert.deepEqual(filtered.own.map((o) => [o.label, o.count]),
                   [["Atlas-API", "2 of its own"], ["~/code/atlas-api/web", "1 of its own"]]);
  assert.deepEqual(filtered.own[1].group.rows.map((r) => r.name), ["lint-web"]);
  assert.deepEqual(filtered.placed.problems.map((p) => [p.section, p.name]), [[web, "lint-web"]]);
  // Only the path section: it is the project's own, under its `~` path.
  const only = fullView({ sections: [view.sections[0], path(web, "Atlas-API")], items: view.items, rows: view.rows });
  assert.deepEqual(filteredView("atlas-api", PROJECTS, only).own.map((o) => o.label), ["~/code/atlas-api/web"]);
  // Without projects' map (no projects, or an older one) the section under the root isn't
  // the project's, and the view reads as before.
  const unmapped = fullView({ sections: [...fullView().sections, path(web, null)], items: view.items, rows: view.rows });
  assert.deepEqual(filteredView("atlas-api", PROJECTS, unmapped).own.map((o) => o.label), ["Atlas-API"]);
});

test("the filter line's picker, and the line when projects can't be read", () => {
  const view = fullView();
  assert.deepEqual(projectChoices(PROJECTS, PROJECTS[1], view), [
    { key: "Atlas-API", root: "~/code/atlas-api", picked: false },
    { key: "kestrel", root: "~/code/kestrel", picked: true },
  ]);
  assert.equal(unfilteredLine("atlas-api", "busy"),
               "Projects is busy, so this shows all of loadout, not only what atlas-api loads.");
  assert.equal(unfilteredLine("atlas-api", "unreadable"),
               "Projects can't be read, so this shows all of loadout, not only what atlas-api loads.");
});

test("Open and Close: an item, a kind, and an item opened inside its kind", () => {
  const item = itemAddress("user", "agent", "code-reviewer");
  assert.equal(routeOf(item), "item/user/agent/code-reviewer");
  assert.ok(sameAddress(item, parseRoute("item/user/agent/code-reviewer")));
  assert.ok(!sameAddress(item, itemAddress("atlas-api", "agent", "code-reviewer")));
  assert.ok(opensItem(item, "user", "agent", "code-reviewer"));
  assert.ok(!opensItem(item, "user", "skill", "code-reviewer"));
  assert.ok(!opensItem({ piece: "global" }, "user", "agent", "code-reviewer"));
  // A folded group shows open the kind the address opens, or the kind of the item it opens.
  assert.equal(openKind({ piece: "kind", section: "user", kind: "skill" }, "user"), "skill");
  assert.equal(openKind(item, "user"), "agent");
  assert.equal(openKind(item, "atlas-api"), null);
  assert.equal(openKind({ piece: "global" }, "user"), null);
  // Close on an item inside its kind leaves the kind open; anything else closes to nothing.
  assert.deepEqual(closed(item, true), { piece: "kind", section: "user", kind: "agent" });
  assert.deepEqual(closed(item, false), NOTHING_OPEN);
  assert.deepEqual(closed({ piece: "global" }, false), NOTHING_OPEN);
});

test("the gone line the page checks from the view: an item or a kind, nothing else", () => {
  const view = fullView();
  assert.equal(goneLineOf(itemAddress("user", "agent", "code-reviewer"), view), null);
  assert.equal(goneLineOf(itemAddress("user", "agent", "gone-one"), view), "Your items no longer lists the agent gone-one.");
  assert.equal(goneLineOf({ piece: "kind", section: "atlas-api", kind: "mcp" }, view),
               "atlas-api lists no mcp servers now.");
  assert.equal(goneLineOf({ piece: "global" }, view), null);
  assert.equal(goneLineOf({ piece: "replace", section: "user", id: "skill:x@codex" }, view),
               "Nothing at skill:x@codex needs Replace now.");
  assert.equal(goneLineOf({ piece: "add", kind: "skill", name: "x" }, view), null);
  assert.equal(goneLineOf({ piece: "add", kind: "skill", name: "x" }, view, [], new Set(["skill/y"])),
               "x is no longer a file no section lists.");
  assert.equal(goneLineOf({ piece: "project", key: "nope" }, view), null);
});
