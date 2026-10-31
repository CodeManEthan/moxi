import assert from "node:assert/strict";
import test from "node:test";

import { changeOf, check, heldOf, itemChange, plans, problem, resetOf, submitted } from "../src/form-model.ts";

const all = () => true;

// One key of each of the eight types, as the settings view gives them.
const KEYS = [
  { name: "on", type: "bool", default: "false", value: "true", source: "file", mark: "applies now", doc: "Turn it on" },
  { name: "port", type: "int", default: "8460", value: null, source: "default", mark: "applies at next start" },
  { name: "ratio", type: "float", default: "0.5", value: "0.25", source: "env", env: "MOXI_X_RATIO",
    note: "set by MOXI_X_RATIO in the shell's environment, which wins over the file" },
  { name: "label", type: "string", default: null, value: "hi", source: "file" },
  { name: "dir", type: "path", default: null, value: null, source: "default" },
  { name: "mode", type: "enum", default: "system", value: "dark", values: ["system", "light", "dark"], source: "file" },
  { name: "tags", type: "list", items: "string", default: "[]", value: '["a", "b"]', value_items: ["a", "b"], source: "file" },
  { name: "extra", type: "table", default: "{}", value: '{a = 1}', source: "file" },
];

test("each of the eight types gets its control, filled with its value", () => {
  const got = Object.fromEntries(plans(KEYS, all).map((plan) => [plan.name, plan.control]));
  assert.deepEqual(got.on, { kind: "checkbox", checked: true });
  assert.deepEqual(got.port, { kind: "number", step: "1", text: "8460" });
  assert.deepEqual(got.ratio, { kind: "number", step: "any", text: "0.25" });
  assert.deepEqual(got.label, { kind: "text", text: "hi", path: false });
  assert.deepEqual(got.dir, { kind: "text", text: "", path: true });
  assert.deepEqual(got.mode, { kind: "select", options: ["system", "light", "dark"], selected: "dark" });
  assert.deepEqual(got.tags, { kind: "list", items: ["a", "b"], itemType: "string" });
  assert.deepEqual(got.extra, { kind: "toml", text: "{a = 1}" });
});

test("a key shows its mark, its source, the default, a note, and reset only from the file", () => {
  const byName = Object.fromEntries(plans(KEYS, all).map((plan) => [plan.name, plan]));
  assert.deepEqual(byName.on.notes, ["applies now", "set in the settings file; the default is false"]);
  assert.equal(byName.on.help, "Turn it on");
  assert.equal(byName.on.resettable, true);
  assert.deepEqual(byName.port.notes, ["applies at next start", "the default"]);
  assert.equal(byName.port.resettable, false);
  assert.deepEqual(byName.ratio.notes, ["set by the shell's environment; the default is 0.5",
    "set by MOXI_X_RATIO in the shell's environment, which wins over the file"]);
  assert.equal(byName.ratio.resettable, false);
});

test("a key whose needs module is absent is shown off with the reason", () => {
  const [plan] = plans([{ name: "hold", type: "bool", needs: "cache", needs_absent: true,
                          note: "works only with the cache module, which is not installed" }], all);
  assert.equal(plan.off, "works only with the cache module, which is not installed");
});

test("a switch table: three toggles, one named only by a daemon off with not set", () => {
  // As `moxi config show --json` gives it with two entries in the file (one true, one
  // false) and one named only by a daemon's switch.
  const table = {
    name: "rows", type: "table", items: "bool", read: "use", default: "{}", source: "file",
    switch_table: true, mark: "applies now",
    entries: [
      { name: "alpha", key: "rows.alpha", value: true, source: "file", named_by: [], mark: "applies now" },
      { name: "beta", key: "rows.beta", value: false, source: "file", named_by: [], mark: "applies now" },
      { name: "backup", key: "rows.backup", value: false, source: "default", named_by: ["timer backup"],
        mark: "turns the daemon on or off now" },
    ],
  };
  const [plan] = plans([table], all);
  assert.equal(plan.control.kind, "toggles");
  assert.equal(plan.control.unavailable, null);
  assert.deepEqual(plan.control.toggles.map((t) => [t.key, t.checked, t.notes, t.resettable]), [
    ["rows.alpha", true, ["applies now"], true],
    ["rows.beta", false, ["applies now"], true],
    ["rows.backup", false, ["turns the daemon on or off now", "not set"], false],
  ]);
  // The table itself is never a TOML box and has no reset of its own.
  assert.equal(plan.resettable, false);
  // A toggle writes `<table key>.<entry>`, and an entry's reset unsets that entry.
  assert.deepEqual(changeOf("rows.backup", "true"), { name: "rows.backup", value: "true" });
  assert.deepEqual(resetOf("rows.alpha"), { name: "rows.alpha", reset: true });
});

test("a switch table whose declarations are unavailable gives the file's entries and the reason", () => {
  const reason = "moxi could not read the module's units. Entries not yet switched on can't be listed until this clears.";
  const [plan] = plans([{
    name: "rows", type: "table", switch_table: true, declarations: "unavailable", reason,
    entries: [{ name: "alpha", key: "rows.alpha", value: true, source: "file", mark: "applies now" }],
  }], all);
  assert.deepEqual(plan.control.toggles.map((t) => t.key), ["rows.alpha"]);
  assert.equal(plan.control.unavailable, reason);
});

test("a table without switch_table is the TOML text box", () => {
  const [plan] = plans([{ name: "extra", type: "table", value: "{}", switch_table: false }], all);
  assert.equal(plan.control.kind, "toml");
});

// Catalog rows, as a launcher gives them for its launch form.
const ROWS = [
  { name: "model", type: "string", flag: "--model", default: "opus" },
  { name: "add_dir", type: "path", flag: "--add-dir", repeatable: true, value: ["/srv/a"] },
  { name: "remote_control", type: "string", flag: "--remote-control", optional_value: true },
  { name: "append_system_prompt_file", type: "path", flag: "--append-system-prompt-file", hidden: true },
  { name: "director", type: "string", repeatable: true, requires: "comms" },
  { name: "session_id", type: "string", owned: true },
  { name: "permission_mode", type: "enum", values: ["default", "plan"], default: "default", posture: true },
];

test("catalog rows: a list, a switch with a value, more, off without its module, owned left out, posture marked", () => {
  const got = plans(ROWS, (module) => module !== "comms");
  const byName = Object.fromEntries(got.map((plan) => [plan.name, plan]));
  assert.equal(byName.session_id, undefined);
  assert.equal(byName.model.label, "--model");
  assert.deepEqual(byName.add_dir.control, { kind: "list", items: ["/srv/a"], itemType: "path" });
  assert.deepEqual(byName.remote_control.control, { kind: "optional", on: false, text: "" });
  assert.equal(byName.append_system_prompt_file.hidden, true);
  assert.equal(byName.director.off, "works only with the comms module, which is not installed");
  assert.equal(byName.permission_mode.posture, true);
  assert.equal(plans(ROWS, all).find((plan) => plan.name === "director").off, null);
});

test("submit gives every value that is not off, in the command line's spelling", () => {
  const held = new Map([
    ["remote_control", { kind: "optional", on: true, text: "" }],
    ["add_dir", { kind: "list", items: ["/srv/a", "/srv/b"] }],
  ]);
  const { values, problems } = submitted(ROWS, held, (module) => module !== "comms");
  assert.deepEqual(values, {
    model: "opus", add_dir: ["/srv/a", "/srv/b"], remote_control: "",
    append_system_prompt_file: null, permission_mode: "default",
  });
  assert.equal(problems.size, 0);
});

test("submit refuses a value not of its type, naming the field", () => {
  const held = new Map([["add_dir", { kind: "list", items: ["relative/dir"] }]]);
  const { problems } = submitted(ROWS, held, all);
  assert.equal(problems.get("add_dir"), "add_dir is an absolute path");
  const typed = submitted([{ name: "port", type: "int" }], new Map([["port", { kind: "scalar", text: "84x" }]]), all);
  assert.equal(typed.problems.get("port"), "port is a whole number");
});

test("a switch table in submit mode gives each entry as its own key", () => {
  const fields = [{ name: "rows", type: "table", switch_table: true, entries: [
    { name: "a", key: "rows.a", value: true, source: "file" },
    { name: "b", key: "rows.b", value: false, source: "default" }] }];
  const plan = plans(fields, all)[0];
  assert.deepEqual(heldOf(plan), { kind: "toggles", on: { "rows.a": true, "rows.b": false } });
  assert.deepEqual(submitted(fields, new Map(), all).values, { "rows.a": "true", "rows.b": "false" });
});

test("the checks made as the user types", () => {
  assert.equal(problem("int", "12"), null);
  assert.equal(problem("int", "-3"), null);
  assert.equal(problem("int", "1.5"), "is a whole number");
  assert.equal(problem("float", "1.5e3"), null);
  assert.equal(problem("float", "abc"), "is a number");
  assert.equal(problem("float", ""), "is a number");
  assert.equal(problem("path", "/home/x"), null);
  assert.equal(problem("path", "C:\\Users\\x"), null);
  assert.equal(problem("path", "\\\\host\\share"), null);
  assert.equal(problem("path", "x/y"), "is an absolute path");
  assert.equal(problem("enum", "dark", ["light", "dark"]), null);
  assert.equal(problem("enum", "blue", ["light", "dark"]), "is one of light, dark");
  assert.equal(problem("string", "anything"), null);
  assert.equal(check({ name: "tags", type: "list", items: "int" }, "x", true), "tags is a whole number");
  assert.deepEqual(itemChange("tags", "add", "c"), { name: "tags", add: "c" });
  assert.deepEqual(itemChange("tags", "remove", "a"), { name: "tags", remove: "a" });
});
