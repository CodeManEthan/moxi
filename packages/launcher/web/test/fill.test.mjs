import assert from "node:assert/strict";
import test from "node:test";

import {
  CHIP_ROWS, fillFace, fillIn, fillNote, fillOf, fillRows, fillText, fromLast, harnessStep, keptFill, lastKey, lastValue,
  lastWhen, parseLast, WHOSE, withoutFill,
} from "../src/fill-model.ts";
import { changes, offRows, turnedOn, valueNow, withOption } from "../src/options-model.ts";
import { filledFields, NO_PRESET, parseForm } from "../src/panel.ts";
import { lastQuery } from "../src/preset-model.ts";
import { launchRequest } from "../src/prompt-model.ts";

/** A catalog field as the page server sends one. */
const field = (name, extra = {}) => ({
  name, type: "string", default: "", values: null, doc: `About ${name}.`, hidden: false, posture: false,
  group: null, sub: null, label: null, danger: [], carry: true, modes: [], shown: true, typed: true, ...extra,
});

const MODE = field("permission_mode", {
  type: "enum", default: "default", values: ["default", "acceptEdits", "bypassPermissions", "plan"], posture: true,
  group: "access", sub: "Mode", label: "Permission mode", danger: ["bypassPermissions"], modes: ["interactive", "print"] });
const ADD_DIR = field("add_dir", {
  type: "list", default: null, repeatable: true, group: "access", sub: "Reach", label: "Extra folders" });
const REMOTE = field("remote_control", {
  optional_value: true, default: null, group: "runs-in", sub: "Remote", label: "Remote Control", modes: ["interactive"] });
const SEARCH = field("search", { type: "bool", default: "false", group: "tools", sub: "Web & browser", label: "Web search" });
const NATIVE = field("native_name", { group: "seat", sub: "Name & notes", label: "Native name" });
const WARN = field("warn_at", { group: "lifecycle", label: "Warn at", shown: false });
/** The rows a launch never fills: each is owned by a control other than the panel and the chip. */
const NEVER = ["cwd", "project", "scratch", "brief", "brief_file", "handoff", "target", "slot", "name"];
const CATALOG = [
  field("harness", { type: "enum", default: "claude", values: ["claude", "codex"], group: "model", label: "Harness" }),
  ...NEVER.map((name) => field(name, { carry: false, ...(name === "scratch" ? { type: "bool", group: "config" } : {}) })),
  field("model", { group: "model", label: "Model" }),
  field("effort", { type: "enum", values: ["low", "high"], group: "model", label: "Effort / reasoning" }),
  MODE, ADD_DIR, REMOTE, SEARCH, NATIVE, WARN,
];

/** `/api/form`'s answer for `harness`, each row `[name, value, source]`; a list row's value is its items. */
const formOf = (harness, ...rows) => parseForm({
  state: "up", detail: null, harness, refused: null, modes: [],
  rows: rows.map(([name, value, source = "shipped"]) => ({
    name, value: Array.isArray(value) ? null : value, items: Array.isArray(value) ? value : null, source, withheld: false })),
});

/** The rows the form holds at their shipped defaults, but those `over` names: `[name, value, source]`. */
const shippedForm = (harness, ...over) => {
  const given = new Map(over.map((row) => [row[0], row]));
  const rows = [["harness", "claude"], ["model", null], ["effort", null], ["permission_mode", "default"], ["add_dir", []],
                ["remote_control", null], ["search", "false"], ["native_name", null],
                ...NEVER.map((name) => [name, null])].map((row) => given.get(row[0]) ?? row);
  return formOf(harness, ...rows);
};

/** `/api/last`'s answer: `values` as `[name, value]`, a list's value its items. */
const lastOf = (harness, ...values) => parseLast({
  state: "up", detail: null, at: "2026-11-10T17:52:10.995351Z", harness,
  values: values.map(([name, value]) => ({ name, value: Array.isArray(value) ? null : value,
                                           items: Array.isArray(value) ? value : null })),
});

const filled = (form) => filledFields(CATALOG, form, NO_PRESET);

test("an answer of the last launch is read, and one that is not one is not", () => {
  assert.deepEqual(lastOf("claude", ["model", "opus"], ["add_dir", ["/work/a"]]), {
    state: "up", detail: null, at: "2026-11-10T17:52:10.995351Z", harness: "claude",
    values: [{ name: "model", value: "opus", items: null }, { name: "add_dir", value: null, items: ["/work/a"] }],
  });
  assert.deepEqual(parseLast({ state: "up", detail: null, at: null, harness: null, values: [] }),
                   { state: "up", detail: null, at: null, harness: null, values: [] });
  assert.equal(parseLast({ state: "up" }), null);
  assert.equal(parseLast("no"), null);
});

test("the key the query names is the first of seat, project and folder given", () => {
  assert.equal(lastKey(lastQuery({ kind: "seat", name: "planner" }, { kind: "project", key: "proj-a" })), "seat");
  assert.equal(lastKey(lastQuery(NO_PRESET, { kind: "project", key: "proj-a" })), "project");
  assert.equal(lastKey(lastQuery({ kind: "project", name: "proj-a" }, { kind: "folder", path: "/srv/work" })), "cwd");
  assert.equal(lastKey(""), null);
});

test("the harness of the last launch fills only where the form leaves the harness shipped", () => {
  const last = lastOf("codex", ["model", "gpt-5"]);
  // The form is drawn for the launch's harness: the values fill against it as it is.
  assert.equal(harnessStep(last, shippedForm("codex", ["harness", "claude"])), "same");
  // Shipped: the harness fills like a row, and the form is read again for it.
  assert.equal(harnessStep(last, shippedForm("claude")), "read");
  // `[defaults]` or the preset sets another harness: nothing fills.
  assert.equal(harnessStep(last, shippedForm("claude", ["harness", "claude", "defaults"])), "none");
  assert.equal(harnessStep(last, shippedForm("claude", ["harness", "claude", "preset"])), "none");
  // A preset that sets the launch's own harness fills as usual.
  assert.equal(harnessStep(last, shippedForm("codex", ["harness", "codex", "preset"])), "same");
  // No launch fills nothing.
  assert.equal(harnessStep(parseLast({ state: "up", detail: null, at: null, harness: null, values: [] }),
                           shippedForm("claude")), "none");
});

test("a value fills only a row the form has, shows here and leaves at its shipped default", () => {
  const form = shippedForm("claude", ["native_name", "set-by-defaults", "defaults"],
                           ["remote_control", "true", "preset"]);
  const last = lastOf("claude", ["harness", "claude"], ["model", "opus"], ["permission_mode", "acceptEdits"],
                      ["add_dir", ["/work/a"]], ["search", "true"], ["native_name", "mine"], ["remote_control", "box"],
                      ["warn_at", "80%"], ["agent", "reviewer"]);
  assert.deepEqual(fillOf(last, filled(form), form), {
    model: "opus", permission_mode: "acceptEdits", add_dir: ["/work/a"], search: "true",
  });
  // A row the user set here is his, and is not filled.
  assert.deepEqual(Object.keys(fillOf(last, filled(form), form, new Set(["model", "search"]))),
                   ["permission_mode", "add_dir"]);
  // A value the row holds already fills nothing.
  assert.deepEqual(fillOf(lastOf("claude", ["permission_mode", "default"], ["add_dir", []]), filled(form), form), {});
});

test("a row that is never carried from one launch to the next never fills, whatever the answer holds", () => {
  const form = shippedForm("claude");
  const last = lastOf("claude", ...NEVER.map((name) => [name, name === "scratch" ? "true" : `/srv/${name}`]),
                      ["model", "opus"]);
  assert.deepEqual(fillOf(last, filled(form), form), { model: "opus" });
  for (const name of NEVER) assert.equal(CATALOG.find((row) => row.name === name).carry, false, name);
});

test("a value of the last launch is held as its row's control holds it", () => {
  assert.deepEqual(lastValue(ADD_DIR, { name: "add_dir", value: null, items: ["/a", "/b"] }), ["/a", "/b"]);
  assert.equal(lastValue(REMOTE, { name: "remote_control", value: "true", items: null }), "");
  assert.equal(lastValue(REMOTE, { name: "remote_control", value: "box", items: null }), "box");
  assert.equal(lastValue(SEARCH, { name: "search", value: "true", items: null }), "true");
  assert.equal(lastValue(MODE, { name: "permission_mode", value: "plan", items: null }), "plan");
  assert.equal(lastValue(NATIVE, { name: "native_name", value: "", items: null }), null);
});

test("a filled value the form now fills with a value of its own is no longer filled", () => {
  const values = { harness: "codex", model: "opus", add_dir: ["/work/a"] };
  const form = shippedForm("codex", ["model", "sonnet", "preset"]);
  assert.deepEqual(keptFill(values, filled(form), form), { harness: "codex", add_dir: ["/work/a"] });
  // The harness stays filled only while the form is drawn for it.
  assert.deepEqual(keptFill(values, filled(shippedForm("claude")), shippedForm("claude")),
                   { model: "opus", add_dir: ["/work/a"] });
});

test("a launch takes the filled values of the chip's rows and of the rows its place draws", () => {
  const values = { model: "opus", permission_mode: "plan", remote_control: "" };
  assert.deepEqual(fillIn(values, CATALOG, "interactive"), values);
  assert.deepEqual(fillIn(values, CATALOG, "print"), { model: "opus", permission_mode: "plan" });
  assert.deepEqual(fillIn(values, CATALOG, null), { model: "opus" });
  assert.deepEqual(CHIP_ROWS, ["harness", "model", "effort"]);
});

test("the item names the one value's row, or how many, in the warning colour for a value that can do harm", () => {
  const home = "/home/alice";
  const rows = fillRows({ add_dir: ["/home/alice/notes", "/srv/x"] }, CATALOG, home);
  assert.deepEqual(rows, [{ name: "add_dir", label: "Extra folders", text: "~/notes, /srv/x", danger: false }]);
  assert.deepEqual(fillFace(rows), { text: "Extra folders", danger: false });
  const several = fillRows({ harness: "codex", permission_mode: "bypassPermissions", search: "true" }, CATALOG, home);
  assert.deepEqual(several.map((row) => [row.label, row.text, row.danger]), [
    ["Harness", "codex", false], ["Permission mode", "bypassPermissions", true], ["Web search", "on", false],
  ]);
  assert.deepEqual(fillFace(several), { text: "3 settings", danger: true });
  assert.equal(fillFace([]), null);
  assert.equal(fillText(REMOTE, "", home), "on");
  assert.equal(fillText(REMOTE, null, home), "off");
  assert.equal(fillText(NATIVE, "/home/alice/x", home), "~/x");
});

test("the sentence under the list says whose last launch filled it, by the line's key", () => {
  assert.deepEqual(WHOSE, { seat: "this agent's", project: "this project's", cwd: "this folder's" });
  assert.equal(fillNote("project", "today 18:45", 1),
               "Filled from this project's last launch, today 18:45. The preset sets nothing for it.");
  assert.equal(fillNote("seat", "today 18:45", 1),
               "Filled from this agent's last launch, today 18:45. The preset sets nothing for it.");
  assert.equal(fillNote("cwd", "Nov 3 08:20", 2),
               "Filled from this folder's last launch, Nov 3 08:20. The preset sets nothing for them.");
  assert.equal(fromLast("today 18:45"), "from the last launch, today 18:45");
  const hour = (at) => `${at.getHours()}:${String(at.getMinutes()).padStart(2, "0")}`;
  const day = (at) => `day ${at.getDate()}`;
  const now = new Date(2026, 10, 10, 20, 0);
  assert.equal(lastWhen(new Date(2026, 10, 10, 18, 45).toISOString(), now, hour, day), "today 18:45");
  assert.equal(lastWhen(new Date(2026, 10, 3, 8, 20).toISOString(), now, hour, day), "day 3 8:20");
  assert.equal(lastWhen("", now, hour, day), "");
});

const RUN = { target: "headless" };

test("the request carries each filled value and names its row in filled", () => {
  const form = shippedForm("claude");
  const fields = filled(form);
  const fill = { model: "opus", add_dir: ["/work/a"] };
  const body = launchRequest(fields, form, { ...fill, brief: "go" }, RUN, fields, NO_PRESET, Object.keys(fill));
  assert.equal(body.values.model, "opus");
  assert.deepEqual(body.values.add_dir, ["/work/a"]);
  assert.deepEqual(body.filled, ["model", "add_dir"]);
  // With nothing filled the request has no such key.
  assert.equal("filled" in launchRequest(fields, form, { brief: "go" }, RUN, fields), false);
});

test("a filled value that can do harm is not a change, asks nothing, counts as off and stays after a launch", () => {
  const form = shippedForm("claude");
  const fields = filled(form);
  const fill = { permission_mode: "bypassPermissions" };
  assert.deepEqual(changes(fields, {}), []);
  assert.deepEqual(offRows(fields, fields, {}, fill), [{ name: "permission_mode", label: "Permission mode", source: "filled" }]);
  const body = launchRequest(fields, form, fill, RUN, fields, NO_PRESET, ["permission_mode"]);
  assert.equal(body.values.permission_mode, "bypassPermissions");
  assert.equal(body.off_posture, true);
  // Only a value turned on by hand holds for one launch: a filled one is none of those.
  assert.deepEqual(turnedOn(fields, {}), {});
});

test("a filled row the user edits is his change and leaves the item; Clear puts the form's value back", () => {
  const form = shippedForm("claude");
  const fields = filled(form);
  let fill = { permission_mode: "plan", add_dir: ["/work/a"] };
  let options = {};
  const mode = fields.find((row) => row.name === "permission_mode");
  // Edited: the row is the user's change and no longer filled.
  fill = withoutFill(fill, "permission_mode");
  options = withOption(options, mode, "acceptEdits", fields);
  assert.deepEqual(changes(fields, options).map((change) => change.name), ["permission_mode"]);
  assert.deepEqual(fillRows(fill, fields, "").map((row) => row.name), ["add_dir"]);
  // Cleared: the row is back at what the form is filled with, and is no change.
  fill = withoutFill(fill, "add_dir");
  const dirs = fields.find((row) => row.name === "add_dir");
  assert.deepEqual(valueNow(dirs, { ...fill, ...options }), []);
  assert.equal(fillFace(fillRows(fill, fields, "")), null);
  assert.deepEqual(changes(fields, options).map((change) => change.name), ["permission_mode"]);
});
