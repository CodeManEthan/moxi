import assert from "node:assert/strict";
import test from "node:test";

import { parseView } from "../src/feed.ts";
import { FOLD_PAST, harnessCounts, countsLine, itemsView, kindCell, modeWord } from "../src/items-model.ts";
import { ATLAS, fullView, HARNESSES, item, row, rowsFor, withRow } from "./fixtures.mjs";

const words = (cells) => cells.map((cell) => cell.word);
const rowOf = (view, name, group = 0) => itemsView(view).groups[group].rows.find((r) => r.name === name);

test("the view parses, and a field of the wrong type refuses it", () => {
  const view = fullView();
  assert.deepEqual(parseView(view), view);
  assert.equal(parseView({ ...view, rows: [{ ...view.rows[0], applies: 3 }] }), null);
  assert.equal(parseView({ ...view, sections: [{ ...ATLAS, refused: { cause: "busy" } }] }), null);
  assert.equal(parseView({ ...view, global: null }), null);
  for (const field of ["present", "shown"]) {
    assert.equal(parseView({ ...view, [field]: undefined }), null, field);
    assert.equal(parseView({ ...view, [field]: [3] }), null, field);
  }
  assert.equal(parseView({ ...view, sections: [{ ...ATLAS, project: undefined }] }), null);
  assert.equal(parseView({ ...view, sections: [{ ...ATLAS, project: 3 }] }), null);
});

test("the full install's grid: Your items with the global file last, then atlas-api", () => {
  const items = itemsView(fullView());
  assert.deepEqual(items.harnesses, HARNESSES);
  assert.deepEqual(items.groups.map((g) => [g.label, g.count]), [["Your items", "8 items"], ["atlas-api", "2 items"]]);
  const mine = items.groups[0];
  assert.deepEqual(mine.rows.map((r) => [r.name, r.kindWord]), [
    ["tidy-notes", "skill"], ["review-diff", "skill"], ["write-changelog", "skill"],
    ["release-checklist", "skill"], ["code-reviewer", "agent"], ["guard-rm", "hook"],
    ["git-reads", "permission rule"],
  ]);
  assert.equal(mine.kinds, null);
  assert.deepEqual([mine.global.label, mine.global.mode], ["Global file", "managed"]);
  assert.deepEqual(words(mine.global.cells), ["placed", "placed", "placed"]);
  assert.equal(items.groups[1].global, null);
});

test("cells: placed, dim off, blank where not listed, the state word heavy", () => {
  const view = withRow(fullView(), { name: "release-checklist", scope: "user", harness: "codex" }, { state: "missing" });
  assert.deepEqual(words(rowOf(view, "review-diff").cells), ["placed", "placed", null]);
  const changelog = rowOf(view, "write-changelog").cells;
  assert.deepEqual(words(changelog), ["placed", "off", "placed"]);
  assert.equal(changelog[1].tone, "dim");
  const release = rowOf(view, "release-checklist").cells;
  assert.deepEqual([release[1].word, release[1].tone], ["not placed", "heavy"]);
  assert.equal(release[0].tone, "plain");
  // A hook has no OpenCode row: blank.
  assert.deepEqual(words(rowOf(view, "guard-rm").cells), ["placed", "placed", null]);
});

test("an item off for every harness reads off where it has no row", () => {
  const view = fullView();
  view.items = view.items.map((i) => i.name === "tidy-notes" ? { ...i, disabled: ["*"] } : i);
  view.rows = view.rows.filter((r) => r.name !== "tidy-notes");
  assert.deepEqual(words(rowOf(view, "tidy-notes").cells), ["off", "off", "off"]);
});

test("the Codex rules file's state is drawn in each permission item it covers", () => {
  let view = fullView();
  view.items.push(item({ kind: "permissions", name: "npm-reads", source: "/x" }));
  view.rows = [...view.rows, row({ kind: "permissions", name: "npm-reads", harness: "claude", form: "entry" })];
  view.rows = view.rows.map((r) => r.covers ? { ...r, covers: ["git-reads", "npm-reads"] } : r);
  view = withRow(view, { name: "*", harness: "codex" }, { state: "modified" });
  assert.deepEqual(words(rowOf(view, "git-reads").cells), ["placed", "changed", "placed"]);
  assert.deepEqual(words(rowOf(view, "npm-reads").cells), ["placed", "changed", null]);
  assert.equal(rowOf(view, "npm-reads").cells[1].row.covers.length, 2);
});

test("a section that can't be placed reads not placed in every cell", () => {
  const view = fullView({ sections: [fullView().sections[0],
                                     { ...ATLAS, refused: { cause: "no-projects", line: "x", last_root: null } }] });
  view.rows = view.rows.map((r) => r.scope === "atlas-api" ? { ...r, state: "blocked", applies: null } : r);
  const atlas = itemsView(view).groups[1];
  assert.deepEqual(atlas.rows.map((r) => words(r.cells)), [["not placed", "not placed", "not placed"],
                                                           ["not placed", "not placed", "not placed"]]);
});

test("a second line only from Codex's trust note", () => {
  const view = fullView();
  assert.equal(rowOf(view, "guard-rm").second, "Codex runs a new hook only once you trust it in Codex");
  assert.equal(rowOf(view, "tidy-notes").second, null);
});

test("module skills are a group per module after Your items, by module name", () => {
  const view = fullView();
  view.items.push(item({ name: "zeta/moxi-z", source: "module zeta", module: "zeta" }),
                  item({ name: "demo/moxi-demo", source: "module demo", module: "demo" }));
  const groups = itemsView(view).groups;
  assert.deepEqual(groups.map((g) => g.label), ["Your items", "demo", "zeta", "atlas-api"]);
  assert.deepEqual([groups[1].count, groups[1].module, groups[1].global], ["1 item", "demo", null]);
  assert.equal(groups[0].count, "8 items");
});

test("a path section by its root in ~ form, and a section that lists nothing", () => {
  const view = fullView();
  view.sections.push({ name: "/home/me/code/kestrel", where: "path", root: "/home/me/code/kestrel", refused: null });
  const last = itemsView(view).groups.at(-1);
  assert.deepEqual([last.label, last.count, last.empty, last.rows.length], ["~/code/kestrel", "0 items",
                                                                            "It lists no item.", 0]);
  assert.equal(itemsView(view).groups[0].empty, null);
});

test("past twelve items a section folds by kind, cells counted", () => {
  const view = fullView();
  const extra = [];
  for (let i = 0; i < 6; i += 1) {
    view.items.push(item({ name: `s${i}`, harnesses: ["claude", "codex"] }));
    extra.push(...rowsFor("skill", `s${i}`, ["claude", "codex"], i === 0 ? { codex: "missing" } : {}));
  }
  view.items.push(item({ kind: "mcp", name: "docs" }), item({ kind: "mcp", name: "web", disabled: ["codex"] }));
  extra.push(...rowsFor("mcp", "docs", HARNESSES, {}, { form: "entry" }),
             ...rowsFor("mcp", "web", HARNESSES, { codex: "off" }, { form: "entry" }));
  view.rows = [...view.rows, ...extra];
  const mine = itemsView(view).groups[0];
  assert.equal(mine.rows.length, 15);
  assert.ok(mine.rows.length > FOLD_PAST);
  assert.equal(mine.count, "16 items");
  assert.deepEqual(mine.kinds.map((k) => k.label),
                   ["Skills 10", "Agents 1", "Hooks 1", "Permission rules 1", "MCP servers 2"]);
  const skills = mine.kinds[0].cells;
  assert.deepEqual(skills.map((c) => [c.text, c.tone]), [["10 placed", "plain"], ["8 of 9", "heavy"],
                                                         ["3 placed", "plain"]]);
  assert.deepEqual(mine.kinds[4].cells.map((c) => c.text), ["2 placed", "1 · 1 off", "2 placed"]);
  assert.deepEqual(mine.kinds[2].cells.map((c) => c.text), ["1 placed", "1 placed", null]);
  assert.equal(mine.kinds[0].rows.length, 10);
  // Twelve items don't fold.
  assert.equal(itemsView(fullView()).groups[0].kinds, null);
});

test("a kind's cell with every item off", () => {
  assert.deepEqual(kindCell([{ word: "off" }, { word: "off" }]), { text: "2 off", tone: "dim" });
  assert.deepEqual(kindCell([{ word: null }]), { text: null, tone: "plain" });
});

test("the global file's mode words", () => {
  assert.equal(modeWord("managed", true), "managed");
  assert.equal(modeWord("if-absent", true), "only where absent");
  assert.equal(modeWord("off", true), "off");
  assert.equal(modeWord("off", false), "off");
});

test("counts per harness, both forms, the global file as one", () => {
  const fine = fullView();
  const counts = harnessCounts(itemsView(fine));
  assert.deepEqual(counts.map((c) => [c.harness, c.placed, c.listed]),
                   [["claude", 10, 10], ["codex", 9, 9], ["opencode", 8, 8]]);
  assert.equal(countsLine(counts, true), "Claude Code 10 · Codex 9 · OpenCode 8");
  let view = withRow(fine, { name: "release-checklist", scope: "user", harness: "claude" }, { state: "missing" });
  view = withRow(view, { name: "release-checklist", scope: "user", harness: "codex" }, { state: "missing" });
  view = withRow(view, { name: "release-checklist", scope: "user", harness: "opencode" }, { state: "missing" });
  view = withRow(view, { name: "code-reviewer", harness: "codex" }, { state: "modified" });
  assert.equal(countsLine(harnessCounts(itemsView(view)), false),
               "Claude Code 9 of 10 placed · Codex 7 of 9 · OpenCode 7 of 8");
});

test("a harness nothing is listed for is left out of the counts", () => {
  const view = fullView({ harnesses: [...HARNESSES, "gemini"] });
  assert.deepEqual(harnessCounts(itemsView(view)).map((c) => c.harness), HARNESSES);
  assert.equal(itemsView(view).groups[0].rows[0].cells.length, 4);
});
