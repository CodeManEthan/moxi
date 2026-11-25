import assert from "node:assert/strict";
import test from "node:test";

import { addAct } from "../src/acts-model.ts";
import { ADD_NOTE, BY_NAME, EVERY_HARNESS, harnessPicker, notListed, pickerOf } from "../src/add-model.ts";
import { ATLAS, FOLDER, fullView, USER } from "./fixtures.mjs";

const dir = (unlisted) => ({ state: "ok", detail: null, uncommitted: [], more: 0, unlisted, reinstalls: false });

test("Not listed: a line per file no section lists, none until the folder is read", () => {
  const view = fullView();
  assert.deepEqual(notListed(null, view), []);
  assert.deepEqual(notListed(dir([{ kind: "skill", name: "bump-version", source: `${FOLDER}/skills/bump-version` }]), view), [
    { key: "skill/bump-version", kind: "skill", name: "bump-version", line: "skill · in your folder, in no section",
      from: "From ~/my-loadout/skills/bump-version" }]);
  assert.equal(ADD_NOTE, "Lists it in loadout.toml and places it at once.");
  assert.equal(BY_NAME, "Add something by its name instead");
});

test("the picker: Your items, each project section, then projects with no section", () => {
  const view = fullView({ sections: [USER, ATLAS, { name: "/w/kestrel", where: "path", root: "/w/kestrel", refused: null }] });
  const projects = { projects: [{ key: "Atlas-API", root: "/w/atlas" }, { key: "kestrel", root: "/w/kestrel" },
                                { key: "otter", root: "/w/otter" }], reason: null, cause: null };
  const picker = pickerOf(view, projects);
  assert.deepEqual(picker.options.map((o) => [o.label, o.section]),
                   [["Your items", null], ["atlas-api", "atlas-api"], ["/w/kestrel", "/w/kestrel"], ["otter", "otter"]]);
  assert.equal(picker.line, null);
});

test("the picker's dim line by projects' cause; nothing when projects is absent", () => {
  const view = fullView();
  const none = (cause) => pickerOf(view, { projects: [], reason: "x", cause }).line;
  assert.equal(none("busy"),
               "Projects is busy, so projects without a section aren't offered; open Add again shortly");
  assert.equal(none("unreadable"), "Projects can't be read, so projects without a section aren't offered");
  assert.equal(none("error"), "Projects can't be read, so projects without a section aren't offered");
  assert.equal(none("absent"), null);
  assert.equal(pickerOf(view, null).line, null);
  // The words never carry the install hint the reason may.
  for (const cause of ["busy", "unreadable", "error", "absent"]) assert.ok(!(none(cause) ?? "").includes("moxi install"));
});

test("Add and place: the user section, a project section, or a project's key", () => {
  assert.deepEqual(addAct("skill", "bump-version", null), {
    path: "api/add", body: { kind: "skill", name: "bump-version" }, argv: ["add", "skill", "bump-version"] });
  assert.deepEqual(addAct("agent", "lead", "otter"), {
    path: "api/add", body: { kind: "agent", name: "lead", project: "otter" },
    argv: ["add", "agent", "lead", "--project=otter"] });
});

test("Add for one harness: `harnesses` in the body, `--harness` in the words", () => {
  assert.deepEqual(addAct("skill", "bump-version", null, "codex"), {
    path: "api/add", body: { kind: "skill", name: "bump-version", harnesses: ["codex"] },
    argv: ["add", "skill", "bump-version", "--harness=codex"] });
  assert.deepEqual(addAct("agent", "lead", "otter", "claude"), {
    path: "api/add", body: { kind: "agent", name: "lead", project: "otter", harnesses: ["claude"] },
    argv: ["add", "agent", "lead", "--project=otter", "--harness=claude"] });
  assert.deepEqual(addAct("skill", "x", null, null), addAct("skill", "x", null));
});

test("For: every harness, then each harness on this machine and no other", () => {
  assert.equal(EVERY_HARNESS, "every harness");
  const picker = harnessPicker(fullView({ present: ["claude", "opencode"] }));
  assert.deepEqual(picker.map((o) => [o.value, o.label, o.harness]),
                   [["", "every harness", null], ["claude", "Claude Code", "claude"], ["opencode", "OpenCode", "opencode"]]);
  assert.deepEqual(harnessPicker(fullView({ present: [] })).map((o) => o.label), ["every harness"]);
});
