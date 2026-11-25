import assert from "node:assert/strict";
import test from "node:test";

import {
  actCommand, emptyGlobal, emptyLines, failedLine, fixLine, folderView, footLines, GLOBAL_PLACE, INSTALL_ALL_PLACE,
  itemPlace, jobPlace, noteGroup, pageState, parseShownOnly, PLACED_PLACE, problemPlace, READING_AFTER_MS, shownOnly,
} from "../src/frame-model.ts";
import { itemsView } from "../src/items-model.ts";
import { INSTALL_ALL, placedView } from "../src/placed-model.ts";
import { fullView, withRow } from "./fixtures.mjs";

const read = (over) => ({ loading: false, gone: false, failure: null, value: null, ...over });
const dir = (over) => ({ state: "ok", detail: null, uncommitted: [], more: 0, unlisted: [], reinstalls: false,
                         ...over });

test("the page's state: nothing at first, then Reading…, gone, failed, a problem, empty or up", () => {
  assert.equal(pageState(read({ loading: true }), 0), "waiting");
  assert.equal(pageState(read({ loading: true }), READING_AFTER_MS), "reading");
  assert.equal(pageState(read({ gone: true, value: fullView() }), 0), "gone");
  assert.equal(pageState(read({ failure: { why: "no answer from the shell" } }), 0), "failed");
  assert.equal(failedLine("no answer from the shell"), "The page can't be read: no answer from the shell.");
  assert.equal(pageState(read({ value: fullView({ problem: "loadout: loadout.toml line 3: bad" }) }), 0), "problem");
  assert.equal(pageState(read({ value: fullView({ items: [], rows: [] }) }), 0), "empty");
  // Nothing listed, but what loadout placed is still there: the page, whose Placed says so.
  const emptied = fullView({ items: [] });
  emptied.rows = emptied.rows.map((row) => ({ ...row, state: "stale" }));
  assert.equal(pageState(read({ value: emptied }), 0), "up");
  const placed = placedView(emptied);
  assert.ok(placed.problems.length > 0);
  assert.ok(placed.problems.some((p) => /^The skill tidy-notes is no longer listed and still placed$/.test(p.sentence)
                                        && p.act?.verb === "install"));
  // A failure after a read keeps the page drawn from what it read.
  assert.equal(pageState(read({ value: fullView(), failure: { why: "x" } }), 0), "up");
});

test("the folder line: the path, what git hasn't committed, and the names with repo's line", () => {
  const view = fullView();
  assert.deepEqual(folderView(view, dir({ uncommitted: null })), { path: "~/my-loadout", line: null, names: null });
  assert.deepEqual(folderView(view, dir()), { path: "~/my-loadout", line: "nothing uncommitted", names: null });
  assert.deepEqual(folderView(view, dir({ uncommitted: ["loadout.toml", "skills/tidy-notes/SKILL.md"],
                                          reinstalls: true })),
                   { path: "~/my-loadout", line: "2 files changed, not committed",
                     names: "loadout.toml, skills/tidy-notes/SKILL.md · a commit places them again" });
  assert.deepEqual(folderView(view, dir({ uncommitted: ["a"], more: 0 })).line, "1 file changed, not committed");
  const many = folderView(view, dir({ uncommitted: Array.from({ length: 20 }, (_, i) => `f${i}`), more: 3 }));
  assert.equal(many.line, "23 files changed, not committed");
  assert.match(many.names, /, f19, and 3 more$/);
  // git that failed, or no answer yet: the path only.
  assert.deepEqual(folderView(view, dir({ state: "error", detail: "git didn't answer", uncommitted: null })),
                   { path: "~/my-loadout", line: null, names: null });
  assert.deepEqual(folderView(view, null), { path: "~/my-loadout", line: null, names: null });
  assert.deepEqual(folderView(view, dir(), { empty: true }),
                   { path: "~/my-loadout", line: "loadout.toml lists nothing", names: null });
  assert.equal(folderView(fullView({ folder: null }), dir()), null);
});

test("the empty state names only the harnesses of the table, and the global file's mode", () => {
  const empty = emptyLines(fullView({ items: [], harnesses: ["claude", "codex"] }));
  assert.equal(empty.line, "Nothing is listed yet");
  assert.equal(empty.parts.map((p) => p.text).join(""),
               "Put a skill in skills/<name>/SKILL.md or an agent file in agents/<name>.md in your loadout "
               + "folder. It shows here to add, and loadout places it for Claude Code and Codex.");
  assert.deepEqual(empty.parts.filter((p) => p.code).map((p) => p.text), ["skills/<name>/SKILL.md", "agents/<name>.md"]);
  const view = fullView({ items: [] });
  assert.deepEqual(emptyGlobal({ ...view, global: { ...view.global, mode: "off", chosen: true } }),
                   { mode: "Off", line: "loadout touches no global file" });
  assert.deepEqual(emptyGlobal({ ...view, global: { ...view.global, mode: "managed", chosen: false } }),
                   { mode: "Off", line: "loadout touches no global file" });
  assert.deepEqual(emptyGlobal({ ...view, global: { ...view.global, mode: "if-absent", chosen: true } }),
                   { mode: "Only where absent", line: null });
});

test("with the note set the page says its changes are off, and each act is its command", () => {
  assert.equal(noteGroup(fullView()), null);
  const note = "MOXI_ANCHOR_HOME is set here, so the page would write where your terminal doesn't";
  assert.deepEqual(noteGroup(fullView({ note })),
                   { label: "This page", heading: "Changes from this page are off", aside: "read only", note,
                     last: "Or run each change in a terminal." });
  assert.equal(actCommand(INSTALL_ALL), "moxi loadout install");
  assert.equal(actCommand({ verb: "install", scope: { project: "atlas api" }, id: null, section: null }),
               "moxi loadout install '--project=atlas api'");
  const view = withRow(fullView(), { kind: "agent", name: "code-reviewer", harness: "codex" },
                       { state: "modified", form: "generated", replaceable: true });
  const [problem] = placedView(view).problems;
  assert.equal(actCommand(problem.act), "moxi loadout install --replace=agent:code-reviewer@codex --user");
  assert.equal(fixLine(problem, { typed: true }),
               "Make the change in the source instead, or put loadout's version back with");
  assert.equal(fixLine(problem, { typed: false }),
               "Make the change in the source instead, or put loadout's version back");
});

test("the foot: the notes no item draws, other than the homes' note, a sentence each; why the folder can't be read", () => {
  const note = "the homes differ";
  assert.deepEqual(footLines(fullView({ notes: [note, "codex agent x: drops model", "x."], note }), dir()),
                   ["Codex agent x: drops model.", "X."]);
  // The trust note and the unconfirmed sentence stand on guard-rm, which the page lists.
  const trust = "hook guard-rm: codex runs a new hook only once you trust it in codex";
  const codex = "not yet confirmed by a run: that codex reads hooks.json and rules/loadout.rules as loadout writes them";
  const opencode = "not yet confirmed by a run: the opencode.json shapes loadout writes (permission, mcp)";
  const view = fullView({ notes: [trust, codex, opencode, "hook guard-rm: not for opencode, which extends by plugins"] });
  view.item_notes.push({ kind: "hook", name: "guard-rm", harness: "codex", code: "unconfirmed" });
  assert.deepEqual(footLines(view, dir()),
                   ["Not yet confirmed by a run: the opencode.json shapes loadout writes (permission, mcp)."]);
  // A note on an item the page doesn't list stays at the foot.
  view.items = view.items.filter((i) => i.name !== "guard-rm");
  assert.equal(footLines(view, dir()).length, 4);
  assert.deepEqual(footLines(fullView({ notes: [] }),
                             dir({ state: "error", detail: "git didn't answer in time", uncommitted: null })),
                   ["Git didn't answer in time."]);
  assert.deepEqual(footLines(null, null), []);
});

test("the line under an act goes at the act, else under Placed, else at the foot", () => {
  const drawn = new Set([problemPlace("p1"), INSTALL_ALL_PLACE, itemPlace("k"), GLOBAL_PLACE]);
  for (const place of drawn) assert.equal(jobPlace(place, drawn, { placed: true }), place);
  // A problem an Install fixed is gone from Placed: its line goes under Placed.
  assert.equal(jobPlace(problemPlace("p2"), drawn, { placed: true }), PLACED_PLACE);
  assert.equal(jobPlace(null, drawn, { placed: true }), PLACED_PLACE);
  // With no Placed drawn (the empty state), at the foot.
  assert.equal(jobPlace(problemPlace("p2"), drawn, { placed: false }), null);
});

test("a harness the page doesn't draw loses its column, rows, notes, global file and counts", () => {
  const view = fullView({ shown: ["claude", "opencode"],
                          counts: { ok: 30, off: 1, missing: 1 } });
  const missing = withRow(view, { name: "tidy-notes", harness: "codex" }, { state: "missing" });
  const cut = shownOnly(missing);
  assert.deepEqual(cut.harnesses, ["claude", "opencode"]);
  assert.ok(cut.rows.every((r) => r.harness !== "codex"));
  assert.ok(cut.item_notes.every((n) => n.harness !== "codex"));
  assert.deepEqual(cut.global.files.map((f) => f.harness), ["claude", "opencode"]);
  const codexRows = missing.rows.filter((r) => r.harness === "codex");
  assert.equal(cut.counts.missing, undefined);
  assert.equal(cut.counts.off, undefined);
  assert.equal(cut.counts.ok, 30 - codexRows.filter((r) => r.state === "ok").length);
  // Every cell, Placed and the verdict read the cut view.
  assert.ok(itemsView(cut).groups.every((g) => g.rows.every((r) => r.cells.length === 2)));
  assert.equal(placedView(missing).problems.length, 1);
  assert.equal(placedView(cut).problems.length, 0);
  // Nothing hidden: the view as it is.
  const all = fullView();
  assert.equal(shownOnly(all), all);
  assert.deepEqual(parseShownOnly(missing), cut);
  assert.equal(parseShownOnly({}), null);
});
