import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";

import { filledFields, NO_PRESET, parseForm } from "../src/panel.ts";
import {
  afterLaunch, asksFirst, CHANGED, changes, dangerNames, findRows, isDanger, offRows, OPTIONS, optionsWord, panelGroups,
  presetOffLine, RESET, summaryOf, summaryWords, turnedOn, withOption,
} from "../src/options-model.ts";
import { CONFIRMED, DANGER_SAYS, dangerSentences, launchedValue, launchRequest, offPosture } from "../src/prompt-model.ts";

/** A catalog field as the page server sends one. */
const field = (name, group, sub, extra = {}) => ({
  name, type: "string", default: "", values: null, doc: `About ${name}.`, hidden: false, posture: false,
  group, sub, label: null, danger: [], carry: true, modes: [], shown: true, typed: true, ...extra,
});

const MODE = field("permission_mode", "access", "Mode", {
  type: "enum", default: "default", values: ["default", "acceptEdits", "bypassPermissions", "plan"], posture: true,
  label: "Permission mode", doc: "The permission mode.", danger: ["bypassPermissions"] });
const SANDBOX = field("sandbox", "access", "Mode", {
  type: "enum", default: "workspace-write", values: ["read-only", "workspace-write", "danger-full-access"], posture: true,
  label: "Sandbox", danger: ["danger-full-access"] });
const APPROVAL = field("approval", "access", "Mode", {
  type: "enum", default: "on-request", values: ["untrusted", "on-request", "never"], posture: true, label: "Approval" });
const ADD_DIR = field("add_dir", "access", "Reach", { type: "list", default: null, repeatable: true, label: "Extra folders" });
const ALLOWED = field("allowed_tools", "tools", "Built-in", { type: "list", default: null, posture: true, label: "Allowed tools" });
const DENIED = field("disallowed_tools", "tools", "Built-in", {
  type: "list", default: null, posture: true, label: "Disallowed tools" });
const SEARCH = field("search", "tools", "Web & browser", { type: "bool", default: "false", label: "Web search" });
const NATIVE = field("native_name", "seat", "Name & notes", { label: "Native name" });
const REMOTE = field("remote_control", "runs-in", "Remote", { optional_value: true, default: null, label: "Remote Control" });
const RISKY = field("risky", "config", null, { type: "enum", default: "safe", values: ["safe", "wild"], danger: ["wild"],
                                                doc: "How wild the session is." });

/** A form's fields in catalog order: the launcher's rows, then the harness's. */
const CATALOG = [
  field("harness", "model", null, { type: "enum", default: "claude", values: ["claude", "codex"] }),
  field("cwd", null, null, { type: "path" }), field("brief", null, null), field("target", null, null),
  field("model", "model", null, { label: "Model" }), RISKY, REMOTE, NATIVE, SEARCH, DENIED, ALLOWED, ADD_DIR, MODE, SANDBOX, APPROVAL,
];

const POSTURE_ROWS = ["disallowed_tools", "allowed_tools", "permission_mode", "sandbox", "approval"];

/** The fields filled from a form answer: every row at its shipped value, but for `rows`,
 * each a value or a list's items, said to come from `source`. */
function filled(rows = {}, source = "defaults") {
  const form = parseForm({ state: "up", detail: null, harness: "claude", refused: null,
                           rows: CATALOG.map((item) => ({
                             name: item.name,
                             value: item.type === "list" ? null : rows[item.name] ?? (item.default || null),
                             items: item.type === "list" ? rows[item.name] ?? [] : null,
                             source: item.name in rows ? source : "shipped", withheld: false })) });
  return { form, fields: filledFields(CATALOG, form, NO_PRESET) };
}

const FIELDS = filled().fields;
const by = (fields, name) => fields.find((item) => item.name === name);
const names = (rows) => rows.map((row) => row.name);
const RUN = { target: "headless" };

/** The options after each of `sets`, a row's name and its new value, is set in the panel. */
function set(fields, sets, options = {}) {
  let out = options;
  for (const [name, value] of Object.entries(sets)) out = withOption(out, by(fields, name), value, fields);
  return out;
}

// --- the line of changed rows ------------------------------------------------------------------

test("nothing changed gives no line and the bare word", () => {
  assert.deepEqual(changes(FIELDS, {}), []);
  assert.equal(optionsWord(0), "Options");
  assert.deepEqual([OPTIONS, CHANGED, RESET], ["Options", "Changed", "Reset"]);
});

test("each row set in the panel is named by its label, in the panel's order, and counted", () => {
  const options = set(FIELDS, { native_name: "helper-1", search: "true", add_dir: ["/srv/proj-a"], remote_control: "" });
  const rows = changes(FIELDS, options);
  // Access, Tools, Seat, Runs in: the panel's order, whatever the order they were set in.
  assert.deepEqual(rows.map((row) => row.label), ["Extra folders", "Web search", "Native name", "Remote Control"]);
  assert.deepEqual(names(rows), ["add_dir", "search", "native_name", "remote_control"]);
  assert.ok(rows.every((row) => !row.danger));
  assert.equal(optionsWord(rows.length), "Options · 4");
  // A row with no label of its own is named by its name in words.
  assert.deepEqual(changes(FIELDS, set(FIELDS, { risky: null })).map((row) => row.label), ["Risky"]);
});

test("a row set back to what the form is filled with is no longer changed", () => {
  let options = set(FIELDS, { native_name: "helper-1", permission_mode: "plan" });
  assert.equal(changes(FIELDS, options).length, 2);
  options = set(FIELDS, { permission_mode: "default" }, options);
  assert.deepEqual(names(changes(FIELDS, options)), ["native_name"]);
  options = set(FIELDS, { native_name: null }, options);
  assert.deepEqual(changes(FIELDS, options), []);
  // A value that is the form's own, however it got among the options, is not a change.
  assert.deepEqual(changes(FIELDS, { permission_mode: "default", add_dir: [] }), []);
});

test("the baseline is the form's fill: a value the user's defaults or a preset set is not a change", () => {
  for (const source of ["defaults", "preset"]) {
    const { fields } = filled({ permission_mode: "plan", native_name: "captain", add_dir: ["/srv/proj-a"] }, source);
    assert.deepEqual(changes(fields, {}), [], source);
    // Setting such a row to its shipped value is the change.
    const options = set(fields, { permission_mode: "default", add_dir: [] });
    assert.deepEqual(names(changes(fields, options)), ["permission_mode", "add_dir"], source);
    assert.deepEqual(changes(fields, set(fields, { permission_mode: "plan" }, options)).map((row) => row.name), ["add_dir"]);
  }
});

test("only the panel's rows are changed rows: not the box, the place, the chip's or the button's", () => {
  const options = { brief: "do it", cwd: "/srv/proj-a", model: "opus", harness: "codex", target: "headless", native_name: "mate" };
  assert.deepEqual(names(changes(FIELDS, options)), ["native_name"]);
  // A row this install does not show is not named either.
  const hidden = FIELDS.map((item) => item.name === "native_name" ? { ...item, shown: false } : item);
  assert.deepEqual(changes(hidden, options), []);
});

test("after a reset no row is changed and the request is the form's own again", () => {
  const { form, fields } = filled();
  const options = set(fields, { permission_mode: "plan", native_name: "helper-1" });
  const before = launchRequest(fields, form, { ...options, brief: "do it", cwd: "~", model: "opus" }, RUN);
  assert.deepEqual([before.values.permission_mode, before.values.native_name, before.off_posture], ["plan", "helper-1", true]);
  const after = launchRequest(fields, form, { brief: "do it", cwd: "~", model: "opus" }, RUN);
  assert.deepEqual(changes(fields, {}), []);
  assert.equal(after.values.permission_mode, "default");
  assert.ok(after.leave_out.includes("native_name") && !("off_posture" in after), after);
  // What the box, the line and the chip set is still there.
  assert.deepEqual([after.values.brief, after.values.cwd, after.values.model], ["do it", "~", "opus"]);
});

// --- the values that can do harm ---------------------------------------------------------------

test("a value is one that can do harm only when its row lists it", () => {
  assert.equal(isDanger(MODE, "bypassPermissions"), true);
  assert.equal(isDanger(MODE, "plan"), false);
  assert.equal(isDanger(MODE, null), false);
  assert.equal(isDanger(SANDBOX, "danger-full-access"), true);
  assert.equal(isDanger(APPROVAL, "never"), false);
  assert.equal(isDanger({ ...NATIVE, danger: undefined }, "bypassPermissions"), false);
  assert.equal(isDanger(ADD_DIR, ["bypassPermissions"]), false);
});

test("a changed row whose value can do harm is marked and named first on the line", () => {
  const options = set(FIELDS, { add_dir: ["/srv/proj-a"], native_name: "helper-1", sandbox: "danger-full-access",
                                permission_mode: "bypassPermissions" });
  const rows = changes(FIELDS, options);
  assert.deepEqual(rows.map((row) => [row.label, row.danger]), [
    ["Permission mode", true], ["Sandbox", true], ["Extra folders", false], ["Native name", false]]);
  assert.equal(optionsWord(rows.length), "Options · 4");
  // The same row at a value that does no harm is in its own place.
  const mild = changes(FIELDS, set(FIELDS, { permission_mode: "plan" }, options));
  assert.deepEqual(mild.map((row) => [row.name, row.danger]), [
    ["sandbox", true], ["permission_mode", false], ["add_dir", false], ["native_name", false]]);
});

test("in the panel the row is marked, and so is its value in the summaries above it", () => {
  const options = set(FIELDS, { permission_mode: "bypassPermissions", add_dir: ["/srv/proj-a"] });
  const access = panelGroups(FIELDS, options)[0];
  assert.deepEqual(access.words, [{ text: "Extra folders 1", danger: false }, { text: "bypassPermissions", danger: true }]);
  assert.equal(access.summary, "Extra folders 1 · bypassPermissions");
  const mode = access.subs.find((sub) => sub.name === "Mode");
  assert.deepEqual(mode.words, [{ text: "bypassPermissions", danger: true }]);
  assert.deepEqual(mode.rows.map((row) => [row.name, row.danger]), [
    ["permission_mode", true], ["sandbox", false], ["approval", false]]);
  const reach = access.subs.find((sub) => sub.name === "Reach");
  assert.deepEqual(reach.words, [{ text: "Extra folders 1", danger: false }]);
  assert.equal(reach.rows[0].danger, false);
  // A group at its defaults has no word to mark.
  assert.deepEqual(panelGroups(FIELDS, options)[1].words, []);
  assert.deepEqual(summaryWords([MODE], {}), []);
  assert.equal(summaryOf([MODE], { permission_mode: "bypassPermissions" }), "bypassPermissions");
  // A row the find field found is marked the same way.
  assert.deepEqual(findRows(FIELDS, "permission", options).map((row) => [row.name, row.danger]), [["permission_mode", true]]);
  assert.deepEqual(findRows(FIELDS, "permission").map((row) => row.danger), [false]);
});

test("a value that can do harm in the form's own fill is marked in the panel and adds no line and no count", () => {
  for (const source of ["defaults", "preset"]) {
    const { form, fields } = filled({ permission_mode: "bypassPermissions" }, source);
    const access = panelGroups(fields, {})[0];
    assert.equal(access.subs[0].rows[0].danger, true, source);
    assert.deepEqual(access.words, [{ text: "bypassPermissions", danger: true }], source);
    assert.deepEqual(changes(fields, {}), [], source);
    assert.equal(optionsWord(changes(fields, {}).length), "Options", source);
    // As the user's own default it is the usual value, so the request says nothing of it.
    assert.equal("off_posture" in launchRequest(fields, form, { cwd: "~" }, RUN), false);
  }
});

test("the values of an earlier launch or of a preset name their rows that can do harm", () => {
  const values = { permission_mode: "bypassPermissions", sandbox: "read-only", native_name: "bypassPermissions",
                   add_dir: ["danger-full-access"] };
  assert.deepEqual(dangerNames(FIELDS, values), ["permission_mode"]);
  assert.deepEqual(dangerNames(FIELDS, { ...values, sandbox: "danger-full-access" }), ["permission_mode", "sandbox"]);
  assert.deepEqual(dangerNames(FIELDS, {}), []);
});

test("a value that can do harm waits for a second act only when picked by hand over another value", () => {
  assert.equal(asksFirst(MODE, "default", "bypassPermissions"), true);
  assert.equal(asksFirst(MODE, "plan", "bypassPermissions"), true);
  assert.equal(asksFirst(MODE, "bypassPermissions", "bypassPermissions"), false, "it is on already");
  assert.equal(asksFirst(MODE, "bypassPermissions", "plan"), false);
  assert.equal(asksFirst(MODE, "default", "plan"), false);
  assert.equal(asksFirst(SANDBOX, null, "danger-full-access"), true);
  // A value carried over from an earlier launch is set with no pick, so nothing asks: it
  // is marked like any other.
  const carried = { permission_mode: "bypassPermissions" };
  assert.equal(panelGroups(FIELDS, carried)[0].subs[0].rows[0].danger, true);
});

test("a value turned on by hand holds for the launch that starts or is copied with it, and is asked for again after", () => {
  const options = set(FIELDS, { permission_mode: "bypassPermissions", sandbox: "danger-full-access", add_dir: ["/srv/proj-a"],
                                native_name: "helper-1" });
  const used = turnedOn(FIELDS, options);
  assert.deepEqual(used, { permission_mode: "bypassPermissions", sandbox: "danger-full-access" });
  const after = afterLaunch(options, used);
  assert.deepEqual(after, { add_dir: ["/srv/proj-a"], native_name: "helper-1" });
  // The line, the count and the colour follow: what is left is plain.
  assert.deepEqual(changes(FIELDS, after).map((row) => [row.name, row.danger]), [["add_dir", false], ["native_name", false]]);
  assert.equal(optionsWord(changes(FIELDS, after).length), "Options · 2");
  assert.equal(panelGroups(FIELDS, after)[0].subs[0].rows[0].danger, false);
  // The row is back at the form's fill, so picking the value again asks again.
  assert.equal(asksFirst(MODE, "default", "bypassPermissions"), true);
  // A row changed since the press stays as it is now.
  const since = set(FIELDS, { sandbox: "read-only" }, options);
  assert.deepEqual(afterLaunch(since, used), { sandbox: "read-only", add_dir: ["/srv/proj-a"], native_name: "helper-1" });
  // Nothing turned on lets go of nothing.
  assert.deepEqual(afterLaunch(after, turnedOn(FIELDS, after)), after);
});

test("a value that can do harm in the form's own fill is never among those turned on by hand", () => {
  for (const source of ["defaults", "preset"]) {
    const { fields } = filled({ permission_mode: "bypassPermissions" }, source);
    assert.deepEqual(turnedOn(fields, {}), {}, source);
    // Picked again over another value, it is the fill, so the panel holds nothing for it.
    const back = set(fields, { permission_mode: "bypassPermissions" }, set(fields, { permission_mode: "plan" }));
    assert.deepEqual(back, {}, source);
    assert.deepEqual(turnedOn(fields, back), {}, source);
  }
});

/** The values each catalog file says can do harm, as `{row: [value, ...]}`. */
function declaredDanger() {
  const folder = new URL("../../src/moxi_launcher/catalog/", import.meta.url);
  const out = {};
  for (const file of readdirSync(folder).filter((name) => name.endsWith(".toml"))) {
    let row = "";
    for (const line of readFileSync(new URL(file, folder), "utf8").split("\n")) {
      const head = /^\[([A-Za-z0-9_]+)\]\s*$/.exec(line);
      if (head !== null) row = head[1];
      const danger = /^danger\s*=\s*(\[.*\])\s*$/.exec(line);
      if (danger !== null) out[row] = [...(out[row] ?? []), ...JSON.parse(danger[1])];
    }
  }
  return out;
}

test("the sentences are keyed by exactly the values the catalog files say can do harm", () => {
  const declared = declaredDanger();
  assert.deepEqual(declared, { permission_mode: ["bypassPermissions"], sandbox: ["danger-full-access"] });
  const keyed = Object.fromEntries(Object.entries(DANGER_SAYS).map(([row, says]) => [row, Object.keys(says)]));
  assert.deepEqual(keyed, declared);
});

test("a value that can do harm says what it does, then that it holds for this launch only", () => {
  assert.deepEqual(dangerSentences(MODE, "bypassPermissions"), [
    "Every tool runs without asking, whatever the mode and the rules say.", "Confirmed for this launch only."]);
  assert.deepEqual(dangerSentences(SANDBOX, "danger-full-access"), [
    "Commands run with no sandbox: they can read and change anything your account can.", CONFIRMED]);
  // A value with no sentence of its own says its row's help text.
  assert.deepEqual(dangerSentences(RISKY, "wild"), ["How wild the session is.", CONFIRMED]);
  assert.deepEqual(dangerSentences(MODE, "plan"), ["The permission mode.", CONFIRMED]);
});

// --- off the usual permissions -----------------------------------------------------------------

test("a row left empty launches with its shipped default", () => {
  assert.equal(launchedValue(MODE, null), "default");
  assert.equal(launchedValue(by(FIELDS, "permission_mode"), "leave out (launches default)"), "default");
  assert.equal(launchedValue(MODE, "plan"), "plan");
  assert.deepEqual(launchedValue(ALLOWED, []), []);
  assert.equal(launchedValue(NATIVE, ""), null);
  assert.equal(launchedValue(SEARCH, null), "false");
});

/** A value of each permission row that is not its shipped one. */
const OFF = { disallowed_tools: ["WebSearch"], allowed_tools: ["Bash"], permission_mode: "plan", sandbox: "read-only",
              approval: "never" };

test("the form as it is filled, with no preset, is never off the usual", () => {
  const { form, fields } = filled();
  assert.deepEqual(offPosture(fields, fields, {}), []);
  assert.deepEqual(offRows(fields, fields, {}), []);
  assert.equal("off_posture" in launchRequest(fields, form, { brief: "do it", cwd: "~" }, RUN), false);
  // Rows that are not permission rows never make it so.
  const options = set(fields, { native_name: "helper-1", add_dir: ["/srv/proj-a"], search: "true", risky: "wild" });
  assert.deepEqual(offPosture(fields, fields, options), []);
  assert.equal("off_posture" in launchRequest(fields, form, { ...options, cwd: "~" }, RUN), false);
});

for (const name of POSTURE_ROWS) {
  test(`${name} set in the panel off its usual value is named as typed and the request says so`, () => {
    const { form, fields } = filled();
    const options = set(fields, { [name]: OFF[name] });
    assert.deepEqual(offPosture(fields, fields, options), [name]);
    assert.deepEqual(offRows(fields, fields, options), [{ name, label: by(fields, name).label, source: "typed" }]);
    // The row is one the line under the box names.
    assert.deepEqual(names(changes(fields, options)), [name]);
    const body = launchRequest(fields, form, { ...options, cwd: "~" }, RUN);
    assert.equal(body.off_posture, true);
    assert.deepEqual(body.values[name], OFF[name]);
    // Set back, the request says nothing.
    const back = set(fields, { [name]: by(fields, name).type === "list" ? [] : by(fields, name).default }, options);
    assert.deepEqual(offPosture(fields, fields, back), []);
    assert.equal("off_posture" in launchRequest(fields, form, { ...back, cwd: "~" }, RUN), false);
  });

  test(`${name} carried over from an earlier launch off its usual value is named as filled`, () => {
    const { form, fields } = filled();
    const carried = { [name]: OFF[name] };
    assert.deepEqual(offRows(fields, fields, {}, carried), [{ name, label: by(fields, name).label, source: "filled" }]);
    // It is not a changed row, and the request, built from the same values, says so.
    assert.deepEqual(changes(fields, {}), []);
    assert.equal(launchRequest(fields, form, { ...carried, cwd: "~" }, RUN).off_posture, true);
    // Edited in the panel it is the user's change.
    const mine = by(fields, name);
    const other = mine.type === "list" ? ["Read"] : mine.values.find((value) => value !== mine.default && value !== OFF[name]);
    const options = set(fields, { [name]: other });
    assert.deepEqual(offRows(fields, fields, options, carried).map((row) => row.source), ["typed"]);
  });

  test(`${name} set by a preset off its usual value is named as the preset's`, () => {
    const base = filled().fields;
    const { form, fields } = filled({ [name]: OFF[name] }, "preset");
    assert.deepEqual(offRows(fields, base, {}), [{ name, label: by(fields, name).label, source: "preset" }]);
    assert.deepEqual(changes(fields, {}), []);
    assert.equal(launchRequest(fields, form, { cwd: "~" }, RUN, base).off_posture, true);
    // Typed back to the usual value over the preset, the launch is on the usual again.
    const usual = by(base, name).type === "list" ? [] : by(base, name).default;
    const options = set(fields, { [name]: usual });
    assert.deepEqual(offRows(fields, base, options), []);
    assert.equal("off_posture" in launchRequest(fields, form, { ...options, cwd: "~" }, RUN, base), false);
    assert.deepEqual(names(changes(fields, options)), [name]);
  });

  test(`${name} at the user's own default is the usual value, and its shipped one is then off`, () => {
    const { form, fields } = filled({ [name]: OFF[name] }, "defaults");
    assert.deepEqual(offPosture(fields, fields, {}), []);
    assert.equal("off_posture" in launchRequest(fields, form, { cwd: "~" }, RUN), false);
    // Left out, the row launches with its shipped default, which is not the user's.
    const options = set(fields, { [name]: by(fields, name).type === "list" ? [] : null });
    assert.deepEqual(offRows(fields, fields, options).map((row) => [row.name, row.source]), [[name, "typed"]]);
    const body = launchRequest(fields, form, { ...options, cwd: "~" }, RUN);
    assert.ok(body.leave_out.includes(name) && body.off_posture === true, body);
  });
}

test("every row off the usual is named, each with what gave it its value", () => {
  const base = filled().fields;
  const { form, fields } = filled({ permission_mode: "plan", allowed_tools: ["Bash"] }, "preset");
  const options = set(fields, { sandbox: "read-only", native_name: "helper-1" });
  const carried = { approval: "never", search: "true" };
  const rows = offRows(fields, base, options, carried);
  assert.deepEqual(rows.map((row) => [row.name, row.source]), [
    ["allowed_tools", "preset"], ["permission_mode", "preset"], ["sandbox", "typed"], ["approval", "filled"]]);
  assert.equal(presetOffLine("planner", rows), "Preset planner sets Allowed tools, Permission mode off your defaults");
  assert.equal(presetOffLine("planner", rows.filter((row) => row.source !== "preset")), null);
  assert.equal(launchRequest(fields, form, { ...carried, ...options, cwd: "~" }, RUN, base).off_posture, true);
});

test("a list holding the usual items in another order is off the usual, as the launcher reads it", () => {
  const { fields } = filled({ allowed_tools: ["Bash", "Read"] }, "defaults");
  assert.deepEqual(offPosture(fields, fields, { allowed_tools: ["Bash", "Read"] }), []);
  assert.deepEqual(offPosture(fields, fields, { allowed_tools: ["Read", "Bash"] }), ["allowed_tools"]);
});

test("a permission row the form does not send is not counted, and one the no-preset form lacks is held to its shipped value", () => {
  const absent = FIELDS.map((item) => item.name === "sandbox" ? { ...item, needs_absent: true } : item);
  assert.deepEqual(offPosture(absent, absent, { sandbox: "read-only" }), []);
  const base = FIELDS.filter((item) => item.name !== "approval");
  assert.deepEqual(offPosture(FIELDS, base, {}), []);
  assert.deepEqual(offPosture(FIELDS, base, { approval: "never" }), ["approval"]);
});
