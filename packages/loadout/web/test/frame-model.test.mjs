import assert from "node:assert/strict";
import test from "node:test";

import {
  actCommand, emptyGlobal, emptyLines, failedLine, fixLine, folderView, footLines, noteGroup, pageState,
  READING_AFTER_MS,
} from "../src/frame-model.ts";
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
  assert.equal(pageState(read({ value: fullView({ items: [] }) }), 0), "empty");
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

test("the foot: the notes other than the homes' note as one line, and why the folder can't be read", () => {
  const note = "the homes differ";
  assert.deepEqual(footLines(fullView({ notes: [note, "codex: not yet confirmed", "x"], note }), dir()),
                   ["codex: not yet confirmed x"]);
  assert.deepEqual(footLines(fullView({ notes: [] }),
                             dir({ state: "error", detail: "git didn't answer in time", uncommitted: null })),
                   ["Git didn't answer in time."]);
  assert.deepEqual(footLines(null, null), []);
});
