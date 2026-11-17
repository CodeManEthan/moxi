import assert from "node:assert/strict";
import test from "node:test";

import { filledFields, formPath, NO_PRESET, parseForm } from "../src/panel.ts";
import {
  changedValue, changes, DEFAULTS, differs, drawnField, findRows, inPanel, keptOptions, modeOf, PANEL_GROUPS, PANEL_WIDTH_PX,
  panelFields, panelGroups, picksOnly, PLACE_ROWS, rowLabel, runsIn, shippedValue, summaryOf, valueNow, valueWords,
  withOption,
} from "../src/options-model.ts";
import { launchRequest } from "../src/prompt-model.ts";

/** A catalog field as the page server sends one, shown and typed unless `extra` says not. */
const field = (name, group, sub, extra = {}) => ({
  name, type: "string", default: "", values: null, doc: `About ${name}.`, hidden: false, posture: false,
  group, sub, label: null, danger: [], carry: true, modes: [], shown: true, typed: true, ...extra,
});

const MODE = field("permission_mode", "access", "Mode", {
  type: "enum", default: "default", values: ["default", "acceptEdits", "plan"], posture: true,
  label: "Permission mode", doc: "The permission mode.", flag: "--permission-mode" });
const ADD_DIR = field("add_dir", "access", "Reach", {
  type: "list", default: null, items: "path", repeatable: true, label: "Extra folders",
  doc: "Another directory the session's tools may reach." });
const TOOLS = field("tools", "tools", "Built-in", { type: "list", default: null, label: "Built-in tool set" });
const SEARCH = field("search", "tools", "Web & browser", { type: "bool", default: "false", label: "Web search" });
const AGENT = field("agent", "seat", "Role", { label: "Agent role" });
const IDENTITY = field("identity", "seat", "Comms", { label: "Identity", shown: false, doc: "The comms name." });
const POSTURE = field("posture", "seat", "Comms", { values: ["operator", "go"], label: "Start posture", typed: false });
const NATIVE = field("native_name", "seat", "Name & notes", { label: "Native name", flag: "-n",
                                                               doc: "The session's display name." });
const HANDOFF = field("handoff", "seat", null, { type: "path", label: "Handoff" });
const REMOTE = field("remote_control", "runs-in", "Remote", { optional_value: true, default: null, label: "Remote Control" });
const WARN = field("warn_at", "lifecycle", "Context", { label: "Warn at" });
const KEEP = field("keep_warm", "lifecycle", "Cache", { type: "enum", default: "off", values: ["off", "on"], label: "Keep warm" });
const EXTRA = field("extra_flags", "config", "Pass-through", { type: "list", default: null, hidden: true });
const SCRATCH = field("scratch", "config", null, { type: "bool", default: "false", label: "Scratch folder" });
const ODD = field("odd", "config", "Later", { label: "Odd one" });
const BARE = field("bare", "config", null, { type: "bool", default: "false", label: "Bare one" });

/** A form's fields in catalog order: the launcher's rows, then the harness's. */
const FIELDS = [
  field("harness", "model", null, { type: "enum", default: "claude", values: ["claude", "codex"] }),
  field("cwd", null, null, { type: "path" }), field("brief", null, null), field("target", null, null),
  SCRATCH, BARE, IDENTITY, HANDOFF, POSTURE, EXTRA, ODD, WARN, KEEP,
  field("model", "model", null), AGENT, TOOLS, MODE, ADD_DIR, REMOTE, NATIVE, SEARCH,
  field("session_id", "seat", "Role", { owned: true }),
  field("notes_dir", "seat", "Name & notes", { type: "path", needs_absent: true }),
];

const names = (rows) => rows.map((row) => row.name);

// --- which rows --------------------------------------------------------------------------------

test("the panel is 400 px wide and has six groups in the catalog's order", () => {
  assert.equal(PANEL_WIDTH_PX, 400);
  assert.deepEqual(PANEL_GROUPS.map((group) => group.key), ["access", "tools", "seat", "runs-in", "lifecycle", "config"]);
  assert.deepEqual(PANEL_GROUPS.map((group) => group.title), ["Access", "Tools", "Seat", "Runs in", "Lifecycle", "Config"]);
});

test("a row is in the panel only when it has one of its groups, is shown here and takes input", () => {
  assert.equal(inPanel(MODE), true);
  assert.equal(inPanel(IDENTITY), false, "not shown on this install");
  assert.equal(inPanel(field("model", "model", null)), false, "the chip's");
  assert.equal(inPanel(field("cwd", null, null)), false, "no group");
  assert.equal(inPanel(field("session_id", "seat", "Role", { owned: true })), false);
  assert.equal(inPanel(field("notes_dir", "seat", "Name & notes", { needs_absent: true })), false);
  // An answer from before the page server said `shown` draws the row.
  assert.equal(inPanel({ ...MODE, shown: undefined }), true);
});

test("the rows a launch sends as its first turn are not in the panel", () => {
  assert.equal(inPanel(HANDOFF), false);
  assert.equal(inPanel(field("brief", "seat", null)), false);
  assert.equal(inPanel(field("brief_file", "seat", null)), false);
  assert.ok(!names(panelFields(FIELDS)).includes("handoff"));
});

test("the rows that say where the session starts are not in the panel", () => {
  assert.deepEqual(PLACE_ROWS, ["cwd", "project", "scratch"]);
  assert.equal(inPanel(SCRATCH), false);
  assert.equal(inPanel(field("project", "config", null)), false);
  assert.deepEqual(findRows(FIELDS, "scratch"), []);
});

test("a row is called by its label, else by its name in words", () => {
  assert.equal(rowLabel(MODE), "Permission mode");
  assert.equal(rowLabel(EXTRA), "Extra flags");
  assert.equal(rowLabel(field("x", "seat", null, { label: "" })), "X");
});

// --- the groups --------------------------------------------------------------------------------

test("the groups hold their subgroups in order, with an unlisted one after and the bare rows last", () => {
  const groups = panelGroups(FIELDS, {});
  assert.deepEqual(groups.map((group) => group.key), ["access", "tools", "seat", "runs-in", "lifecycle", "config"]);
  const subs = Object.fromEntries(groups.map((group) => [group.key, group.subs.map((sub) => [sub.name, names(sub.rows)])]));
  assert.deepEqual(subs.access, [["Mode", ["permission_mode"]], ["Reach", ["add_dir"]]]);
  assert.deepEqual(subs.tools, [["Built-in", ["tools"]], ["Web & browser", ["search"]]]);
  // Comms keeps the one row this install shows; the group's rows with no subgroup are none here.
  assert.deepEqual(subs.seat, [["Role", ["agent"]], ["Comms", ["posture"]], ["Name & notes", ["native_name"]]]);
  assert.deepEqual(subs["runs-in"], [["Remote", ["remote_control"]]]);
  assert.deepEqual(subs.lifecycle, [["Context", ["warn_at"]], ["Cache", ["keep_warm"]]]);
  assert.deepEqual(subs.config, [["Pass-through", ["extra_flags"]], ["Later", ["odd"]], ["", ["bare"]]]);
  const extra = groups[5].subs[0].rows[0];
  assert.deepEqual([extra.label, extra.hidden], ["Extra flags", true]);
  assert.equal(groups[0].subs[0].rows[0].hidden, false);
});

test("a group with no row drawn is left out, and so is a subgroup", () => {
  const groups = panelGroups([MODE, IDENTITY, field("model", "model", null)], {});
  assert.deepEqual(groups.map((group) => group.key), ["access"]);
  assert.deepEqual(groups[0].subs.map((sub) => sub.name), ["Mode"]);
  assert.deepEqual(panelGroups([], {}), []);
});

// --- values and the summary --------------------------------------------------------------------

test("a row's value is the one set in the panel, else what the form is filled with", () => {
  const filled = { ...MODE, value: "acceptEdits" };
  assert.equal(valueNow(filled, {}), "acceptEdits");
  assert.equal(valueNow(filled, { permission_mode: "plan" }), "plan");
  assert.equal(valueNow(filled, { permission_mode: null }), null);
  assert.equal(shippedValue(filled), "default");
  assert.deepEqual(valueNow({ ...ADD_DIR, value_items: ["/a"] }, {}), ["/a"]);
  assert.deepEqual(shippedValue({ ...ADD_DIR, value_items: ["/a"] }), []);
});

test("a value differs when it launches something other than the shipped default", () => {
  assert.equal(differs(MODE, "default"), false);
  assert.equal(differs(MODE, "plan"), true);
  // Left out, an enum launches its default.
  assert.equal(differs(MODE, null), false);
  assert.equal(differs(MODE, "leave out (launches default)"), false);
  assert.equal(differs(ADD_DIR, []), false);
  assert.equal(differs(ADD_DIR, ["/a"]), true);
  assert.equal(differs(REMOTE, null), false);
  assert.equal(differs(REMOTE, ""), true);
  assert.equal(differs(SEARCH, "false"), false);
  assert.equal(differs(SEARCH, "true"), true);
  assert.equal(differs(NATIVE, null), false);
  assert.equal(differs(NATIVE, "helper"), true);
});

test("a value is said in a few words: a pick alone, any other row after its label", () => {
  assert.equal(valueWords(MODE, "plan"), "plan");
  assert.equal(valueWords(ADD_DIR, ["/a", "/b"]), "Extra folders 2");
  assert.equal(valueWords(ADD_DIR, []), "Extra folders none");
  assert.equal(valueWords(REMOTE, ""), "Remote Control on");
  assert.equal(valueWords(REMOTE, "desk"), "Remote Control desk");
  assert.equal(valueWords(REMOTE, null), "Remote Control off");
  assert.equal(valueWords(SEARCH, "true"), "Web search on");
  assert.equal(valueWords(NATIVE, "helper"), "Native name helper");
  assert.equal(valueWords(NATIVE, null), "Native name empty");
});

test("a closed group says its values that differ from the shipped default, else defaults", () => {
  assert.equal(summaryOf([MODE, ADD_DIR], {}), DEFAULTS);
  assert.equal(DEFAULTS, "defaults");
  assert.equal(summaryOf([MODE, ADD_DIR], { permission_mode: "plan", add_dir: ["/a"] }), "plan · Extra folders 1");
  // A value the user's defaults filled differs from the shipped one with nothing set here.
  assert.equal(summaryOf([{ ...MODE, value: "acceptEdits" }], {}), "acceptEdits");
  // Set back to the shipped default over the filled value, it is `defaults` again.
  assert.equal(summaryOf([{ ...MODE, value: "acceptEdits" }], { permission_mode: "default" }), DEFAULTS);
  const groups = panelGroups(FIELDS, { native_name: "helper", remote_control: "" });
  const summary = Object.fromEntries(groups.map((group) => [group.key, group.summary]));
  assert.deepEqual(summary, { access: DEFAULTS, tools: DEFAULTS, seat: "Native name helper",
                              "runs-in": "Remote Control on", lifecycle: DEFAULTS, config: DEFAULTS });
  const seat = groups.find((group) => group.key === "seat");
  assert.deepEqual(seat.subs.map((sub) => sub.summary), [DEFAULTS, DEFAULTS, "Native name helper"]);
});

// --- the find field ----------------------------------------------------------------------------

test("the find field reads a row's label, its name and its help text, whatever the case", () => {
  assert.deepEqual(names(findRows(FIELDS, "extra folders")), ["add_dir"]);
  assert.deepEqual(names(findRows(FIELDS, "ADD_D")), ["add_dir"]);
  assert.deepEqual(names(findRows(FIELDS, "display name")), ["native_name"]);
  // The panel's order, not the fields'.
  assert.deepEqual(names(findRows(FIELDS, "extra")), ["add_dir", "extra_flags"]);
  assert.deepEqual(findRows(FIELDS, "  "), []);
  assert.deepEqual(findRows(FIELDS, "no such thing"), []);
});

test("a found row says where it sits", () => {
  assert.equal(findRows(FIELDS, "native")[0].where, "Seat › Name & notes");
  assert.equal(findRows(FIELDS, "bare one")[0].where, "Config");
  assert.equal(findRows(FIELDS, "extra flags")[0].hidden, true);
});

test("a row this install does not show is in no group and is never found", () => {
  const all = panelGroups(FIELDS, {}).flatMap((group) => group.subs.flatMap((sub) => names(sub.rows)));
  for (const name of ["identity", "notes_dir", "session_id", "handoff", "model", "cwd", "brief", "scratch"]) {
    assert.ok(!all.includes(name), name);
  }
  assert.deepEqual(findRows(FIELDS, "identity"), []);
  assert.deepEqual(findRows(FIELDS, "comms name"), []);
  assert.deepEqual(findRows(FIELDS, "notes_dir"), []);
  // Shown, the same row is in its group and is found.
  const shown = FIELDS.map((item) => (item.name === "identity" ? { ...item, shown: true } : item));
  assert.deepEqual(names(findRows(shown, "comms name")), ["identity"]);
  const comms = panelGroups(shown, {}).find((group) => group.key === "seat").subs.find((sub) => sub.name === "Comms");
  assert.deepEqual(names(comms.rows), ["identity", "posture"]);
});

// --- drawing a row -----------------------------------------------------------------------------

test("a typed value is offered only where the page server says the row takes one", () => {
  assert.equal(picksOnly(POSTURE), true);
  assert.equal(picksOnly({ ...POSTURE, typed: true }), false);
  assert.equal(picksOnly({ ...POSTURE, typed: undefined }), false);
  assert.equal(picksOnly(NATIVE), false, "no values to pick from");
  assert.equal(picksOnly({ ...MODE, typed: false }), false, "an enum is a pick already");
  const pick = drawnField(POSTURE, null);
  assert.deepEqual([pick.type, pick.values, pick.value], ["enum", ["", "operator", "go"], ""]);
  assert.equal(drawnField(POSTURE, "go").value, "go");
  const typed = drawnField({ ...POSTURE, typed: true }, "with:planner");
  assert.deepEqual([typed.type, typed.value, typed.note], ["string", "with:planner", "Offered: operator, go"]);
  assert.equal(drawnField({ ...POSTURE, typed: true, note: "set in your [defaults]" }, null).note,
               "set in your [defaults]; Offered: operator, go");
});

test("a row is drawn with its label, in place, holding the value as its control spells it", () => {
  const hidden = drawnField(EXTRA, ["--verbose"]);
  assert.deepEqual([hidden.flag, hidden.hidden, hidden.value, hidden.value_items], ["Extra flags", false, null, ["--verbose"]]);
  const mode = drawnField(MODE, "plan");
  assert.deepEqual([mode.flag, mode.posture, mode.value, mode.type], ["Permission mode", false, "plan", "enum"]);
  assert.equal(drawnField(MODE, null).value, "leave out (launches default)");
  assert.equal(drawnField(KEEP, null).value, "leave out (launches off)");
  assert.equal(drawnField(NATIVE, null).value, "");
  assert.equal(drawnField(REMOTE, null).value, null);
  assert.equal(drawnField(REMOTE, "").value, true);
  assert.equal(drawnField(REMOTE, "desk").value, "desk");
  assert.equal(drawnField(SEARCH, "true").value, "true");
  assert.equal(drawnField(SEARCH, null).value, "false");
  // The catalog's field is not changed.
  assert.deepEqual([MODE.flag, EXTRA.hidden, MODE.posture], ["--permission-mode", true, true]);
});

// --- changing a row ----------------------------------------------------------------------------

test("a change from the form renderer gives the row's next value", () => {
  assert.equal(changedValue(MODE, "default", { name: "permission_mode", value: "plan" }), "plan");
  assert.equal(changedValue(NATIVE, "helper", { name: "native_name", value: "" }), null);
  assert.equal(changedValue(SEARCH, "false", { name: "search", value: "true" }), "true");
  assert.equal(changedValue(REMOTE, null, { name: "remote_control", value: "" }), "");
  assert.equal(changedValue(REMOTE, "", { name: "remote_control", value: "desk" }), "desk");
  assert.equal(changedValue(REMOTE, "desk", { name: "remote_control", reset: true }), null);
  assert.deepEqual(changedValue(ADD_DIR, [], { name: "add_dir", add: "/a" }), ["/a"]);
  assert.deepEqual(changedValue(ADD_DIR, ["/a"], { name: "add_dir", add: "/b" }), ["/a", "/b"]);
  assert.deepEqual(changedValue(ADD_DIR, ["/a", "/b"], { name: "add_dir", add: "/a" }), ["/a", "/b"]);
  assert.deepEqual(changedValue(ADD_DIR, ["/a", "/b", "/c"], { name: "add_dir", remove: "/b" }), ["/a", "/c"]);
  assert.deepEqual(changedValue(ADD_DIR, ["/a"], { name: "add_dir", remove: "/x" }), ["/a"]);
  assert.equal(changedValue(NATIVE, "helper", { name: "native_name" }), "helper");
});

test("the panel holds only what differs from what the form is filled with", () => {
  const filled = { ...MODE, value: "acceptEdits" };
  let options = withOption({}, filled, "plan");
  assert.deepEqual(options, { permission_mode: "plan" });
  options = withOption(options, NATIVE, "helper");
  options = withOption(options, filled, "acceptEdits");
  assert.deepEqual(options, { native_name: "helper" });
  assert.deepEqual(withOption(options, NATIVE, null), {});
  // Emptying a row the form filled is a change.
  assert.deepEqual(withOption({}, filled, null), { permission_mode: null });
});

test("a text given in the panel empties its other spelling, and the reverse", () => {
  const text = field("instructions", "seat", "Instructions", { value: "be brief" });
  const file = field("instructions_file", "seat", "Instructions", { type: "path" });
  const rows = [text, file, NATIVE];
  let options = withOption({}, file, "/srv/rules.md", rows);
  assert.deepEqual(options, { instructions_file: "/srv/rules.md", instructions: null });
  options = withOption(options, text, "be kind", rows);
  // The file is back at what the form filled it with, nothing, so it is no longer set.
  assert.deepEqual(options, { instructions: "be kind" });
  // Emptying one leaves the other as it is, and a row of no such group touches nothing.
  assert.deepEqual(withOption(options, text, null, rows), { instructions: null });
  assert.deepEqual(withOption({}, NATIVE, "helper", rows), { native_name: "helper" });
});

test("another harness's form keeps the values of the rows it still draws", () => {
  const options = { permission_mode: "plan", native_name: "helper", warn_at: "80%" };
  assert.deepEqual(keptOptions(options, [WARN, NATIVE, IDENTITY]), { native_name: "helper", warn_at: "80%" });
  assert.deepEqual(keptOptions(options, []), {});
});

// --- the request -------------------------------------------------------------------------------

/** The fields filled from a form answer that gives every row its shipped value. */
function filled(fields, rows = {}) {
  const form = parseForm({ state: "up", detail: null, harness: "claude", refused: null,
                           rows: fields.filter((item) => !item.needs_absent && !item.owned).map((item) => ({
                             name: item.name, value: rows[item.name] ?? (item.default || null),
                             items: item.type === "list" ? [] : null, source: "shipped", withheld: false })) });
  return { form, fields: filledFields(fields, form, NO_PRESET) };
}

test("a value set in the panel goes in the launch request with the rows the piece sets", () => {
  const { form, fields } = filled(FIELDS);
  const run = { target: "headless" };
  const plain = launchRequest(fields, form, { brief: "do it", cwd: "~" }, run);
  assert.equal(plain.values.permission_mode, "default");
  assert.ok(plain.leave_out.includes("native_name") && plain.leave_out.includes("add_dir"), plain.leave_out);
  let options = withOption({}, fields.find((item) => item.name === "native_name"), "helper");
  options = withOption(options, fields.find((item) => item.name === "add_dir"), ["/srv/proj-a"]);
  options = withOption(options, fields.find((item) => item.name === "remote_control"), "");
  options = withOption(options, fields.find((item) => item.name === "permission_mode"), null);
  const body = launchRequest(fields, form, { ...options, brief: "do it", cwd: "~" }, run);
  assert.equal(body.values.native_name, "helper");
  assert.deepEqual(body.values.add_dir, ["/srv/proj-a"]);
  assert.equal(body.values.remote_control, "true");
  assert.ok(body.leave_out.includes("permission_mode") && !("permission_mode" in body.values), body);
  assert.ok(!body.leave_out.includes("native_name"), body.leave_out);
  // The rows the piece draws elsewhere are as they were.
  assert.deepEqual([body.values.brief, body.values.cwd, body.values.target], ["do it", "~", "headless"]);
});

test("nothing set in the panel makes a first-turn row part of the request", () => {
  const { form, fields } = filled(FIELDS, { handoff: "/srv/handoff.md" });
  const options = withOption({}, fields.find((item) => item.name === "native_name"), "helper");
  const body = launchRequest(fields, form, { ...options, cwd: "~" }, { target: "terminal" });
  assert.ok(!("handoff" in body.values) && body.leave_out.includes("handoff"), body);
  assert.ok(!("brief" in body.values), body);
});

// --- the rows a launch's place draws -------------------------------------------------------

/** Claude's rows as the catalog gives their modes: the launcher's own have none. */
const MODED = [
  { ...MODE, modes: ["interactive", "print"] }, { ...ADD_DIR, modes: ["interactive", "print"] },
  { ...REMOTE, modes: ["interactive"] }, { ...NATIVE, modes: ["interactive", "print"] },
  { ...WARN, modes: [] }, { ...KEEP, modes: [] }, { ...AGENT, modes: ["interactive", "print"] },
  { ...IDENTITY, shown: true, modes: [] },
];
const drawnIn = (mode) => names(panelFields(MODED, mode));

test("the form's answer names the mode of each place a launch runs in, null where none can", () => {
  const form = parseForm({ state: "up", detail: null, harness: "claude", rows: [], refused: null,
                           modes: [{ target: "terminal", mode: "interactive" }, { target: "headless", mode: null },
                                   { target: "slot", mode: "interactive" }, { mode: "print" }, "x"] });
  assert.deepEqual(form.modes, [{ target: "terminal", mode: "interactive" }, { target: "headless", mode: null },
                                { target: "slot", mode: "interactive" }]);
  assert.equal(modeOf(form.modes, "slot"), "interactive");
  assert.equal(modeOf(form.modes, "headless"), null);
  // A place the answer does not name is not known, and every row is drawn for it.
  assert.equal(modeOf(form.modes, "elsewhere"), undefined);
  assert.equal(modeOf(parseForm({ state: "up", detail: null, harness: "claude", rows: [], refused: null }).modes, "slot"),
               undefined);
  // The read asks under the comms name the form holds, and under none when it holds none.
  assert.equal(formPath(NO_PRESET, "claude", "planner"), "api/form?harness=claude&identity=planner");
  assert.equal(formPath(NO_PRESET, "claude", ""), "api/form?harness=claude");
});

test("a row of the harness's is drawn only in a mode its flag is passed in", () => {
  assert.deepEqual(drawnIn("interactive"), names(MODED));
  // Remote Control is passed to an interactive session only.
  assert.deepEqual(drawnIn("print"), names(MODED).filter((name) => name !== "remote_control"));
  assert.equal(runsIn(MODED[2], "print"), false);
  assert.equal(inPanel(MODED[2], "print"), false);
  assert.equal(inPanel(MODED[2], "interactive"), true);
  // The launcher's own rows are drawn in every mode.
  assert.deepEqual(drawnIn("exec"), ["warn_at", "keep_warm", "identity"]);
  // Not known yet, every row is.
  assert.deepEqual(drawnIn(undefined), names(MODED));
  const groups = (mode) => panelGroups(MODED, {}, mode).map((group) => group.key);
  assert.deepEqual(groups("interactive"), ["access", "seat", "runs-in", "lifecycle"]);
  // A group with no row left to draw is left out.
  assert.deepEqual(groups("print"), ["access", "seat", "lifecycle"]);
});

test("the find field reaches only the rows the place's mode draws", () => {
  assert.deepEqual(names(findRows(MODED, "remote", {}, "interactive")), ["remote_control"]);
  assert.deepEqual(names(findRows(MODED, "remote", {}, "print")), []);
  assert.deepEqual(names(findRows(MODED, "permission", {}, null)), []);
  assert.deepEqual(names(findRows(MODED, "warn", {}, null)), ["warn_at"]);
});

test("set to launch in a place of another mode, the values of the rows it no longer draws are let go of", () => {
  const { fields } = filled(MODED);
  let options = withOption({}, fields.find((item) => item.name === "remote_control"), "");
  options = withOption(options, fields.find((item) => item.name === "permission_mode"), "plan");
  options = withOption(options, fields.find((item) => item.name === "warn_at"), "80");
  // While its row is drawn a value is kept.
  assert.deepEqual(keptOptions(options, fields, "interactive"), options);
  assert.deepEqual(keptOptions(options, fields, undefined), options);
  const kept = keptOptions(options, fields, "print");
  assert.deepEqual(kept, { permission_mode: "plan", warn_at: "80" });
  assert.deepEqual(names(changes(fields, kept)), ["permission_mode", "warn_at"]);
  // Back in an interactive place the row is drawn again at what the form is filled with.
  assert.equal(valueNow(fields.find((item) => item.name === "remote_control"), kept), null);
});

test("where no launch is possible in the place, no row of the harness's is drawn and none is kept", () => {
  const { fields } = filled(MODED);
  assert.deepEqual(drawnIn(null), ["warn_at", "keep_warm", "identity"]);
  assert.deepEqual(panelGroups(MODED, {}, null).map((group) => group.key), ["seat", "lifecycle"]);
  let options = withOption({}, fields.find((item) => item.name === "permission_mode"), "plan");
  options = withOption(options, fields.find((item) => item.name === "identity"), "planner");
  assert.deepEqual(keptOptions(options, fields, null), { identity: "planner" });
});
