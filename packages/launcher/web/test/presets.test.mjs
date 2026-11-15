import assert from "node:assert/strict";
import test from "node:test";

import { filledFields, NO_PRESET, parseForm, parsePresets } from "../src/panel.ts";
import { changes, offRows, optionsWord } from "../src/options-model.ts";
import {
  afterPick, atLoad, homeIsPath, lastQuery, NO_PRESET_ROW, placeWords, presetFace, presetMenu, presetOff, presetPlace,
  presetSets, projectPreset, sameChoice,
} from "../src/preset-model.ts";
import { launchRequest } from "../src/prompt-model.ts";

/** A catalog field as the page server sends one. */
const field = (name, extra = {}) => ({
  name, type: "string", default: "", values: null, doc: `About ${name}.`, hidden: false, posture: false,
  group: null, sub: null, label: null, danger: [], carry: true, modes: [], shown: true, typed: true, ...extra,
});

const MODE = field("permission_mode", {
  type: "enum", default: "default", values: ["default", "acceptEdits", "bypassPermissions", "plan"], posture: true,
  group: "access", sub: "Mode", label: "Permission mode", danger: ["bypassPermissions"] });
const DENIED = field("disallowed_tools", {
  type: "list", default: null, posture: true, group: "tools", sub: "Built-in", label: "Disallowed tools" });
const CATALOG = [
  field("harness", { type: "enum", default: "claude", values: ["claude", "codex"] }),
  field("cwd", { type: "path" }), field("project"), field("brief"), field("target"), field("slot"),
  field("model", { label: "Model" }), field("effort", { type: "enum", values: ["low", "high"] }), DENIED, MODE,
];

/** `/api/presets`' answer holding `presets`, each `[kind, name, harness, values]`. */
const listed = (...presets) => parsePresets({
  state: "up", detail: null, problems: [],
  presets: presets.map(([kind, name, harness, values]) => ({
    kind, name, harness, values: Object.entries(values).map(([key, value]) =>
      (Array.isArray(value) ? { name: key, value: null, items: value } : { name: key, value, items: null })),
  })),
}).presets;

const PRESETS = listed(
  ["seat", "planner", "claude", { harness: "claude", model: "opus", effort: "high", home: "proj-b" }],
  ["seat", "reviewer", "claude", { model: "sonnet", permission_mode: "bypassPermissions", home: "~/review" }],
  ["seat", "scout", null, { model: "haiku" }],
  ["project", "proj-a", "codex", { harness: "codex", model: "gpt-5", effort: "low", project: "proj-a" }],
  ["project", "alt-a", "claude", { project: "proj-a", model: "opus" }],
  ["project", "b-first", null, { project: "proj-b" }],
  ["project", "b-second", null, { project: "proj-b" }],
  ["project", "folder", null, { cwd: "/srv/work" }],
);
const named = (kind, name) => PRESETS.find((preset) => preset.kind === kind && preset.name === name);
const PROJECT_A = { kind: "project", key: "proj-a" };
const FOLDER = { kind: "folder", path: "/srv/other" };

test("a project applies the preset named like it, else the first by name whose place it is, else none", () => {
  assert.equal(projectPreset(PRESETS, "proj-a").name, "proj-a");
  assert.equal(projectPreset(PRESETS, "proj-b").name, "b-first");
  assert.equal(projectPreset(PRESETS, "proj-c"), null);
  // A seat whose home is the project is not a project preset.
  assert.equal(projectPreset(listed(["seat", "proj-c", null, { project: "proj-c" }]), "proj-c"), null);
});

test("a project picked applies its preset; a folder leaves the preset; a seat stays whatever is picked", () => {
  assert.deepEqual(afterPick(NO_PRESET, PROJECT_A, PRESETS), { kind: "project", name: "proj-a" });
  assert.deepEqual(afterPick({ kind: "project", name: "proj-a" }, { kind: "project", key: "proj-b" }, PRESETS),
                   { kind: "project", name: "b-first" });
  assert.deepEqual(afterPick({ kind: "project", name: "proj-a" }, { kind: "project", key: "proj-c" }, PRESETS), NO_PRESET);
  assert.deepEqual(afterPick({ kind: "project", name: "proj-a" }, FOLDER, PRESETS), { kind: "project", name: "proj-a" });
  const seat = { kind: "seat", name: "planner" };
  assert.deepEqual(afterPick(seat, PROJECT_A, PRESETS), seat);
  assert.deepEqual(afterPick(seat, FOLDER, PRESETS), seat);
  // The place Home opens on applies its project's preset, and a folder applies none.
  assert.deepEqual(atLoad(PROJECT_A, PRESETS), { kind: "project", name: "proj-a" });
  assert.deepEqual(atLoad(FOLDER, PRESETS), NO_PRESET);
  assert.deepEqual(atLoad({ kind: "choose" }, PRESETS), NO_PRESET);
});

test("a seat's home is a path when it has a slash or starts with ~ or a dot, else a project's key", () => {
  for (const home of ["~/x", "/srv/x", "./x", "a/b", "a\\b"]) assert.equal(homeIsPath(home), true, home);
  for (const home of ["proj-a", "x.y", "proj~"]) assert.equal(homeIsPath(home), false, home);
});

test("a preset's place is a seat's home, else its project, else its folder", () => {
  assert.deepEqual(presetPlace(named("seat", "planner"), true), { kind: "project", key: "proj-b" });
  assert.deepEqual(presetPlace(named("seat", "reviewer"), true), { kind: "folder", path: "~/review" });
  assert.equal(presetPlace(named("seat", "scout"), true), null);
  assert.deepEqual(presetPlace(named("project", "proj-a"), true), PROJECT_A);
  assert.deepEqual(presetPlace(named("project", "folder"), true), { kind: "folder", path: "/srv/work" });
  // A relative home is no place the line can show.
  assert.equal(presetPlace(listed(["seat", "s", null, { home: "./here" }])[0], true), null);
});

test("without projects no project preset names a place, and a seat's project key is none", () => {
  for (const name of ["proj-a", "folder"]) assert.equal(presetPlace(named("project", name), false), null, name);
  assert.equal(presetPlace(named("seat", "planner"), false), null);
  assert.deepEqual(presetPlace(named("seat", "reviewer"), false), { kind: "folder", path: "~/review" });
  const menu = presetMenu(PRESETS, NO_PRESET, FOLDER, false, "/home/u", () => CATALOG);
  assert.deepEqual(menu[0].rows.map((row) => row.place), ["", "", "", "", ""]);
  assert.deepEqual(menu[1].rows.map((row) => row.place), ["", "in ~/review", ""]);
});

test("the menu lists the project presets, then the seats, each by name, with what each sets and its place", () => {
  const menu = presetMenu(PRESETS, { kind: "seat", name: "planner" }, PROJECT_A, true, "/home/u", () => CATALOG);
  assert.equal(NO_PRESET_ROW, "No preset");
  assert.deepEqual(menu.map((section) => section.title), ["Project presets", "Agent presets"]);
  assert.deepEqual(menu[0].rows.map((row) => row.name), ["alt-a", "b-first", "b-second", "folder", "proj-a"]);
  assert.deepEqual(menu[1].rows.map((row) => row.name), ["planner", "reviewer", "scout"]);
  const said = (row) => row.sets.map((word) => word.text).join(" · ");
  assert.deepEqual(menu[0].rows.map(said), ["claude · opus", "", "", "", "codex · gpt-5 · low"]);
  assert.deepEqual(menu[1].rows.map(said), ["claude · opus · high", "claude · sonnet · bypassPermissions", "haiku"]);
  assert.deepEqual(menu[0].rows.map((row) => row.place), ["this project", "in proj-b", "in proj-b", "in /srv/work",
                                                          "this project"]);
  assert.deepEqual(menu[1].rows.map((row) => row.place), ["in proj-b", "in ~/review", ""]);
  assert.deepEqual([...menu[0].rows, ...menu[1].rows].filter((row) => row.current).map((row) => row.name), ["planner"]);
  // A kind with no preset has no heading.
  const seats = presetMenu(PRESETS.filter((preset) => preset.kind === "seat"), NO_PRESET, null, true, "", () => CATALOG);
  assert.deepEqual(seats.map((section) => section.title), ["Agent presets"]);
  assert.equal(seats[0].rows.some((row) => row.current), false);
});

test("a value a preset sets that can do harm is marked in what its menu row says it sets", () => {
  assert.deepEqual(presetSets(named("seat", "reviewer"), CATALOG), [
    { text: "claude", danger: false }, { text: "sonnet", danger: false }, { text: "bypassPermissions", danger: true }]);
  // The harness's own rows say which values can do harm; another harness's know none.
  assert.deepEqual(presetSets(named("seat", "reviewer"), []).filter((word) => word.danger), []);
});

test("a menu row's place is this project only for the project on the line", () => {
  assert.equal(placeWords(PROJECT_A, PROJECT_A, ""), "this project");
  assert.equal(placeWords(PROJECT_A, FOLDER, ""), "in proj-a");
  assert.equal(placeWords({ kind: "folder", path: "/home/u/work" }, FOLDER, "/home/u"), "in ~/work");
  assert.equal(placeWords(null, PROJECT_A, ""), "");
});

test("the item reads none, dim, with no preset, else the applied preset's name", () => {
  assert.deepEqual(presetFace(NO_PRESET), { name: "none", dim: true });
  assert.deepEqual(presetFace({ kind: "seat", name: "planner" }), { name: "planner", dim: false });
  assert.equal(sameChoice(NO_PRESET, { kind: "", name: "" }), true);
  assert.equal(sameChoice({ kind: "seat", name: "a" }, { kind: "project", name: "a" }), false);
});

/** The fields for a form answer's rows, as the page fills them for `choice`. */
const filled = (rows, choice = NO_PRESET) => filledFields(CATALOG, parseForm({
  state: "up", detail: null, harness: "claude", refused: null, modes: [],
  rows: rows.map(([name, value, source]) => ({ name, value: Array.isArray(value) ? null : value,
                                              items: Array.isArray(value) ? value : null, source, withheld: false })),
}), choice);

const SEAT = { kind: "seat", name: "reviewer" };
const BASE = filled([["permission_mode", "default", "shipped"], ["disallowed_tools", [], "shipped"], ["model", "", "shipped"]]);
const WITH_SEAT = filled([["permission_mode", "bypassPermissions", "preset"], ["disallowed_tools", ["Bash(git push:*)"], "preset"],
                          ["model", "sonnet", "preset"], ["slot", "3", "preset"]], SEAT);
const RUN = { target: "slot", slot: 2, named: false };

test("the preset's values are the baseline, and its rows off the usual are named on its own line", () => {
  // Its values, a danger value among them, add no Changed line and no count.
  assert.deepEqual(changes(WITH_SEAT, {}), []);
  assert.equal(optionsWord(changes(WITH_SEAT, {}).length), "Options");
  assert.deepEqual(changes(WITH_SEAT, { permission_mode: "default" }).map((row) => row.name), ["permission_mode"]);
  const rows = offRows(WITH_SEAT, BASE, {});
  assert.deepEqual(rows.map((row) => [row.name, row.source]), [["disallowed_tools", "preset"], ["permission_mode", "preset"]]);
  const off = presetOff(SEAT, WITH_SEAT, {}, rows);
  assert.deepEqual(off, { lead: "Preset reviewer sets", tail: "off your defaults",
                          labels: [{ text: "Permission mode", danger: true }, { text: "Disallowed tools", danger: false }] });
  // A row set back to the usual by hand is a change, not the preset's.
  const typed = offRows(WITH_SEAT, BASE, { permission_mode: "default" });
  assert.deepEqual(presetOff(SEAT, WITH_SEAT, { permission_mode: "default" }, typed).labels.map((word) => word.text),
                   ["Disallowed tools"]);
  // No preset, or a preset at the usual values, has no line.
  assert.equal(presetOff(NO_PRESET, BASE, {}, offRows(BASE, BASE, {})), null);
  assert.equal(presetOff(SEAT, BASE, {}, offRows(BASE, BASE, {})), null);
});

test("a request names the applied preset alone, and is off the usual against the form read with no preset", () => {
  const body = launchRequest(WITH_SEAT, null, { brief: "go", cwd: "/w", project: null }, RUN, BASE, SEAT);
  assert.equal(body.seat, "reviewer");
  assert.equal("project_preset" in body, false);
  assert.equal(body.off_posture, true);
  assert.equal(body.values.permission_mode, "bypassPermissions");
  // The button says where it runs: a slot the preset pins is left out.
  assert.equal("slot" in body.values, false);
  assert.ok(body.leave_out.includes("slot"), body.leave_out);
  const project = launchRequest(BASE, null, { brief: "go" }, RUN, BASE, { kind: "project", name: "proj-a" });
  assert.equal(project.project_preset, "proj-a");
  assert.equal("seat" in project, false);
  assert.equal("off_posture" in project, false);
  // Read against the preset's own form, the same values would be the usual: never sent so.
  assert.equal("off_posture" in launchRequest(WITH_SEAT, null, { brief: "go" }, RUN, WITH_SEAT, SEAT), false);
  assert.equal("seat" in launchRequest(BASE, null, { brief: "go" }, RUN), false);
});

test("the last launch is keyed by the seat applied, else the project on the line, else the folder", () => {
  assert.equal(lastQuery(SEAT, PROJECT_A), "seat=reviewer");
  assert.equal(lastQuery({ kind: "project", name: "proj-a" }, PROJECT_A), "project=proj-a");
  assert.equal(lastQuery(NO_PRESET, PROJECT_A), "project=proj-a");
  assert.equal(lastQuery({ kind: "project", name: "proj-a" }, FOLDER), "cwd=%2Fsrv%2Fother");
  assert.equal(lastQuery(NO_PRESET, { kind: "choose" }), null);
  assert.equal(lastQuery(NO_PRESET, null), null);
});
