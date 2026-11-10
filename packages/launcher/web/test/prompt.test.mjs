import assert from "node:assert/strict";
import test from "node:test";

import { fieldsFor, filledFields, NO_PRESET, parseCatalog, parseForm, parsePreview } from "../src/panel.ts";
import {
  buttonWords, copied, edited, followed, following, IDLE, inFlight, launched, LAUNCHED, launchRequest, notesOf, NO_TARGETS,
  afterNoAnswer, aroundPage, parseTargets, PRESSED, UNREAD_TARGETS, previewed, runMenu, runOf, slotWords, startedWords, stopped,
  chipFace, chipMenu, firstOpen, folderFace, formValues, harnessName, hasEffort, HOME, openWhere, parsePlaces, pickOf, pickValues, placed, placeRows,
  placeTime, projectKeys, samePick, tilde, typedFolder, valueOf, whereFace, withProjects,
} from "../src/prompt-model.ts";

const place = (project_key, cwd, at = "2026-11-10T18:45:00Z") => ({ project_key, cwd, at });
const ALICE = "/home/alice";
const PLACES = { state: "up", detail: null, projects: true, actor: "operator", home: ALICE,
                 places: [place("proj-a", "/home/alice/code/proj-a/web"), place(null, "/home/alice/notes"),
                          place("proj-b", "/srv/proj-b")] };
const field = (name, extra = {}) => ({ name, type: "string", default: null, values: null, ...extra });

/** Reads that answer from a table by `module path`, and say what was asked. */
function reads(table) {
  const asked = [];
  const read = async (module, path) => {
    asked.push(`${module} ${path}`);
    return table[`${module} ${path}`] ?? { status: 404, data: null };
  };
  return { read, asked };
}

// --- the places ----------------------------------------------------------------------------

test("the places read keeps each place's project, folder and time", () => {
  const read = parsePlaces(PLACES);
  assert.equal(read.projects, true);
  assert.equal(read.home, ALICE);
  assert.equal(parsePlaces({ ...PLACES, home: undefined }).home, "");
  assert.deepEqual(read.places[0], place("proj-a", "/home/alice/code/proj-a/web"));
  assert.equal(read.places[1].project_key, null);
  assert.deepEqual(parsePlaces({ ...PLACES, places: [{ cwd: "" }, "x", { project_key: 3, cwd: "/a" }] }).places,
                   [{ project_key: null, cwd: "/a", at: "" }]);
});

test("a places answer that is an error or no answer reads as none", () => {
  assert.equal(parsePlaces({ state: "error", detail: "it broke", projects: true, places: [] }), null);
  assert.equal(parsePlaces({ state: "up", projects: true }), null);
  assert.equal(parsePlaces("nope"), null);
});

test("projects is there only when the page server takes a project and the shell has the picker", () => {
  const yes = parsePlaces(PLACES);
  const no = parsePlaces({ ...PLACES, projects: false });
  assert.equal(withProjects(yes, true), true);
  assert.equal(withProjects(yes, false), false);
  assert.equal(withProjects(no, true), false);
  assert.equal(withProjects(no, false), false);
  assert.equal(withProjects(null, true), false);
});

// --- the pick ------------------------------------------------------------------------------

test("a place with a project is picked as the project with projects and as its folder without", () => {
  const row = place("proj-a", "/home/alice/code/proj-a/web");
  assert.deepEqual(pickOf(row, true), { kind: "project", key: "proj-a" });
  assert.deepEqual(pickOf(row, false), { kind: "folder", path: "/home/alice/code/proj-a/web" });
  assert.deepEqual(pickOf(place(null, "/srv/x"), true), { kind: "folder", path: "/srv/x" });
});

test("a pick sets its own place row and empties the other", () => {
  assert.deepEqual(pickValues({ kind: "project", key: "proj-a" }), { project: "proj-a", cwd: null });
  assert.deepEqual(pickValues({ kind: "folder", path: "~/notes" }), { project: null, cwd: "~/notes" });
  assert.deepEqual(pickValues({ kind: "choose" }), { project: null, cwd: null });
  assert.equal(placed({ kind: "choose" }), false);
  assert.equal(placed({ kind: "folder", path: HOME }), true);
});

test("the first open is the one project, a choice among several, else the home folder", () => {
  const home = { kind: "folder", path: "~" };
  assert.deepEqual(firstOpen(true, ["proj-a"]), { kind: "project", key: "proj-a" });
  assert.deepEqual(firstOpen(true, ["proj-a", "proj-b"]), { kind: "choose" });
  assert.deepEqual(firstOpen(true, []), home);
  assert.deepEqual(firstOpen(true, null), home);
  assert.deepEqual(firstOpen(false, ["proj-a"]), home);
  assert.deepEqual(projectKeys({ projects: [{ key: "proj-a", root: "/a" }, { root: "/b" }] }), ["proj-a"]);
  assert.equal(projectKeys({ state: "up" }), null);
});

test("Home opens on the newest place and does not read the projects", async () => {
  const { read, asked } = reads({ "launcher api/places": { status: 200, data: PLACES } });
  const where = await openWhere(read, true);
  assert.equal(where.withProjects, true);
  assert.deepEqual(where.pick, { kind: "project", key: "proj-a" });
  assert.equal(where.places.length, 3);
  assert.deepEqual(asked, ["launcher api/places"]);
});

test("without the picker the newest place is its folder, though the page server takes projects", async () => {
  const { read } = reads({ "launcher api/places": { status: 200, data: PLACES } });
  const where = await openWhere(read, false);
  assert.equal(where.withProjects, false);
  assert.deepEqual(where.pick, { kind: "folder", path: "/home/alice/code/proj-a/web" });
});

test("a Home that never launched reads the projects once and opens by how many there are", async () => {
  const none = { status: 200, data: { ...PLACES, places: [] } };
  const opened = async (projects) => {
    const { read, asked } = reads({ "launcher api/places": none, ...(projects ? { "projects api/projects": projects } : {}) });
    return { where: await openWhere(read, true), asked };
  };
  const one = await opened({ status: 200, data: { projects: [{ key: "proj-a" }] } });
  assert.deepEqual(one.where.pick, { kind: "project", key: "proj-a" });
  assert.deepEqual(one.asked, ["launcher api/places", "projects api/projects"]);
  const several = await opened({ status: 200, data: { projects: [{ key: "proj-a" }, { key: "proj-b" }] } });
  assert.deepEqual(several.where.pick, { kind: "choose" });
  assert.equal(several.where.withProjects, true);
  assert.deepEqual((await opened({ status: 200, data: { projects: [] } })).where.pick, { kind: "folder", path: "~" });
  assert.deepEqual((await opened(null)).where.pick, { kind: "folder", path: "~" });
  assert.deepEqual((await opened({ status: 0, data: null })).where.pick, { kind: "folder", path: "~" });
});

test("a Home that never launched, without projects, opens on the home folder and asks projects nothing", async () => {
  for (const [data, picker] of [[{ ...PLACES, places: [] }, false], [{ ...PLACES, projects: false, places: [] }, true]]) {
    const { read, asked } = reads({ "launcher api/places": { status: 200, data } });
    const where = await openWhere(read, picker);
    assert.deepEqual(where, { withProjects: false, home: ALICE, places: [], pick: { kind: "folder", path: "~" } });
    assert.deepEqual(asked, ["launcher api/places"]);
  }
});

test("a places read that fails opens on the home folder with no projects", async () => {
  const { read, asked } = reads({});
  assert.deepEqual(await openWhere(read, true), { withProjects: false, home: "", places: [], pick: { kind: "folder", path: "~" } });
  assert.deepEqual(asked, ["launcher api/places"]);
});

// --- folders -------------------------------------------------------------------------------

test("the home folder the page server names is written ~ and any other path as it is", () => {
  assert.equal(tilde("/home/alice/code/proj-a", ALICE), "~/code/proj-a");
  assert.equal(tilde("/home/alice", ALICE), "~");
  assert.equal(tilde("/home/alice/notes/", "/home/alice/"), "~/notes");
  assert.equal(tilde("/home/alice-two/x", ALICE), "/home/alice-two/x");
  assert.equal(tilde("/home/bob/x", ALICE), "/home/bob/x");
  assert.equal(tilde("/srv/proj-b", ALICE), "/srv/proj-b");
  assert.equal(tilde("~/notes", ALICE), "~/notes");
  assert.equal(tilde("/", ALICE), "/");
});

test("a home folder of any form is written ~, and none is recognised by its form", () => {
  assert.equal(tilde("/data/people/alice/code", "/data/people/alice"), "~/code");
  assert.equal(tilde("/home/alice/code", "/data/people/alice"), "/home/alice/code");
  // With no home folder named, or the root as one, every path is drawn whole.
  assert.equal(tilde("/home/alice/code/", ""), "/home/alice/code");
  assert.equal(tilde("/root/x", ""), "/root/x");
  assert.equal(tilde("/srv/x", "/"), "/srv/x");
});

test("a folder's face is the dim path and its own name", () => {
  assert.deepEqual(folderFace("/home/alice/code/proj-a", ALICE), { dim: "~/code/", name: "proj-a" });
  assert.deepEqual(folderFace("/srv/proj-b", ALICE), { dim: "/srv/", name: "proj-b" });
  assert.deepEqual(folderFace("/home/alice", ALICE), { dim: "", name: "~" });
  assert.deepEqual(folderFace("~", ALICE), { dim: "", name: "~" });
  assert.deepEqual(folderFace("/", ALICE), { dim: "", name: "/" });
  assert.deepEqual(folderFace("/srv", ALICE), { dim: "/", name: "srv" });
});

test("a long folder is cut in the middle of its dim part, keeping the first segment and the name", () => {
  assert.deepEqual(folderFace("/home/alice/one/two/three/four/proj-a", ALICE, 16), { dim: "~/…/four/", name: "proj-a" });
  assert.deepEqual(folderFace("/home/alice/one/two/three/four/proj-a", ALICE, 24), { dim: "~/…/three/four/", name: "proj-a" });
  assert.deepEqual(folderFace("/var/lib/one/two/proj-a", ALICE, 16), { dim: "/var/…/", name: "proj-a" });
  assert.deepEqual(folderFace("/home/alice/one/a-very-long-folder-name-that-is-never-cut", ALICE, 20),
                   { dim: "~/…/", name: "a-very-long-folder-name-that-is-never-cut" });
  // Nothing between the first segment and the name to cut.
  assert.deepEqual(folderFace("/home/alice/a-very-long-folder-name", ALICE, 10), { dim: "~/", name: "a-very-long-folder-name" });
});

test("a typed folder must be absolute or start with ~", () => {
  assert.equal(typedFolder(" /srv/proj-b "), "/srv/proj-b");
  assert.equal(typedFolder("~/notes"), "~/notes");
  assert.equal(typedFolder("~"), "~");
  assert.equal(typedFolder("notes"), null);
  assert.equal(typedFolder("./notes"), null);
  assert.equal(typedFolder(""), null);
});

// --- the line and its menu -----------------------------------------------------------------

test("the line reads Project with a key and Folder with a path", () => {
  assert.deepEqual(whereFace({ kind: "project", key: "proj-a" }, ALICE), { word: "Project", dim: "", name: "proj-a" });
  assert.deepEqual(whereFace({ kind: "folder", path: "/home/alice/code/proj-a" }, ALICE),
                   { word: "Folder", dim: "~/code/", name: "proj-a" });
  assert.deepEqual(whereFace({ kind: "folder", path: "~" }, ALICE), { word: "Folder", dim: "", name: "~" });
  assert.deepEqual(whereFace({ kind: "choose" }, ALICE), { word: "Project", dim: "", name: "choose" });
});

test("two picks are the same place by key, or by folder however home is written", () => {
  assert.equal(samePick({ kind: "folder", path: "~/notes" }, { kind: "folder", path: "/home/alice/notes/" }, ALICE), true);
  assert.equal(samePick({ kind: "project", key: "proj-a" }, { kind: "project", key: "proj-a" }, ALICE), true);
  assert.equal(samePick({ kind: "project", key: "notes" }, { kind: "folder", path: "notes" }, ALICE), false);
  assert.equal(samePick({ kind: "choose" }, { kind: "choose" }, ALICE), false);
});

test("a place's time is the time of day today and the day before that", () => {
  const now = new Date(2026, 10, 10, 19, 0);
  const hour = (at) => `h${at.getHours()}`;
  const day = (at) => `d${at.getDate()}`;
  assert.equal(placeTime(new Date(2026, 10, 10, 8, 20).toISOString(), now, hour, day), "h8");
  assert.equal(placeTime(new Date(2026, 10, 9, 23, 59).toISOString(), now, hour, day), "d9");
  assert.equal(placeTime(new Date(2025, 10, 10, 8, 20).toISOString(), now, hour, day), "d10");
  assert.equal(placeTime("", now), "");
  assert.equal(placeTime("not a time", now), "");
  assert.match(placeTime(new Date(2026, 10, 10, 8, 20).toISOString(), now), /20/);
});

test("with projects a place that has a project is its key alone, and the current one is ticked", () => {
  const now = new Date("2026-11-10T19:00:00Z");
  const rows = placeRows(parsePlaces(PLACES).places, true, { kind: "project", key: "proj-b" }, ALICE, now, () => "t", () => "d");
  assert.deepEqual(rows.map((row) => [row.dim, row.name, row.current]),
                   [["", "proj-a", false], ["~/", "notes", false], ["", "proj-b", true]]);
  assert.deepEqual(rows[0].pick, { kind: "project", key: "proj-a" });
  assert.deepEqual(rows[1].pick, { kind: "folder", path: "/home/alice/notes" });
});

test("without projects every place is its folder and a pick of it sets the folder", () => {
  const now = new Date("2026-11-10T19:00:00Z");
  const rows = placeRows(parsePlaces(PLACES).places, false, { kind: "folder", path: "~/notes" }, ALICE, now);
  assert.deepEqual(rows.map((row) => [row.dim, row.name, row.current]),
                   [["~/code/proj-a/", "web", false], ["~/", "notes", true], ["/srv/", "proj-b", false]]);
  assert.ok(rows.every((row) => row.pick.kind === "folder"));
  assert.deepEqual(pickValues(rows[0].pick), { project: null, cwd: "/home/alice/code/proj-a/web" });
});

test("two places drawn the same are one row, the newer", () => {
  const places = [place("proj-a", "/srv/x", "2026-11-10T18:00:00Z"), place(null, "/srv/x", "2026-11-09T18:00:00Z")];
  const rows = placeRows(places, false, { kind: "choose" }, ALICE, new Date("2026-11-10T19:00:00Z"), () => "new", () => "old");
  assert.deepEqual(rows.map((row) => row.time), ["new"]);
  assert.equal(placeRows(places, true, { kind: "choose" }, ALICE, new Date()).length, 2);
});

// --- the form's values ---------------------------------------------------------------------

test("the form's values are each row's filled value with the rows set here over them", () => {
  const fields = [field("harness", { type: "enum", value: "claude" }), field("model", { value: "opus" }),
                  field("brief", { value: "" }), field("cwd", { type: "path", value: "/srv/old" }),
                  field("project", { needs_absent: true }), field("add_dir", { type: "list", value_items: ["/a"] })];
  const values = formValues(fields, { brief: "Fix the build", ...pickValues({ kind: "folder", path: "~/notes" }) });
  assert.deepEqual(values, { harness: "claude", model: "opus", brief: "Fix the build", cwd: "~/notes", add_dir: ["/a"] });
  assert.equal(valueOf(fields, {}, "model"), "opus");
  assert.equal(valueOf(fields, { model: null }, "model"), "");
  assert.equal(valueOf(fields, { model: "sonnet" }, "model"), "sonnet");
  assert.equal(valueOf(fields, {}, "effort"), "");
});

// --- the chip ------------------------------------------------------------------------------

const HARNESS_ROW = field("harness", { type: "enum", default: "claude", values: ["claude", "codex"] });
const CATALOG = parseCatalog({ state: "up", detail: null, harnesses: [
  { name: "claude", fields: [HARNESS_ROW, field("model", { default: "", values: ["fable", "opus", "sonnet"] }),
                             field("effort", { type: "enum", default: "", values: ["low", "medium", "high"] })] },
  { name: "codex", fields: [HARNESS_ROW, field("model", { default: "" })] },
] });
const row = (name, value) => ({ name, value, items: null, source: "shipped", withheld: false });
/** The fields the piece holds for a harness, filled as `/api/form` answers. */
const filled = (harness, rows = []) => filledFields(
  fieldsFor(CATALOG, harness), parseForm({ state: "up", detail: null, harness, rows, refused: null }), NO_PRESET);

test("the chip's face names the harness, the model or that it is the default, and a set effort", () => {
  assert.equal(chipFace("claude", "", ""), "claude · default model");
  assert.equal(chipFace("claude", "sonnet", ""), "claude · sonnet");
  assert.equal(chipFace("claude", "sonnet", "medium"), "claude · sonnet · medium");
  assert.equal(chipFace("claude", "", "medium"), "claude · default model · medium");
});

test("a harness with no effort row never shows the third part", () => {
  assert.equal(hasEffort(filled("claude")), true);
  assert.equal(hasEffort(filled("codex")), false);
  assert.equal(chipFace("codex", "gpt-5", null), "codex · gpt-5");
  assert.equal(chipFace("codex", "", null), "codex · default model");
  assert.equal(hasEffort([field("effort", { type: "enum", needs_absent: true })]), false);
});

test("the harnesses are drawn by their names, and one with no name as its value", () => {
  assert.equal(harnessName("claude"), "Claude Code");
  assert.equal(harnessName("codex"), "Codex");
  assert.equal(harnessName("other"), "other");
  assert.equal(harnessName("constructor"), "constructor");
});

test("the menu lists the harnesses, the models after Default, and the efforts after Default", () => {
  const menu = chipMenu(filled("claude"), "claude", "opus", "high");
  assert.deepEqual(menu.harness, [{ value: "claude", label: "Claude Code", current: true },
                                  { value: "codex", label: "Codex", current: false }]);
  assert.deepEqual(menu.model.map((item) => [item.label, item.value, item.current]),
                   [["Default", "", false], ["fable", "fable", false], ["opus", "opus", true], ["sonnet", "sonnet", false]]);
  assert.deepEqual(menu.effort.map((item) => [item.label, item.current]),
                   [["Default", false], ["low", false], ["medium", false], ["high", true]]);
  assert.equal(menu.typed, "");
});

test("with the model and the effort empty, Default is the current choice of each", () => {
  const menu = chipMenu(filled("claude"), "claude", "", "");
  assert.deepEqual(menu.model.filter((item) => item.current).map((item) => item.label), ["Default"]);
  assert.deepEqual(menu.effort.filter((item) => item.current).map((item) => item.label), ["Default"]);
});

test("a full model name typed is none of the list's and is kept as typed", () => {
  const menu = chipMenu(filled("claude"), "claude", "claude-opus-4-1", "");
  assert.equal(menu.typed, "claude-opus-4-1");
  assert.ok(menu.model.every((item) => !item.current));
});

test("a harness whose model row offers no names has Default alone and no effort list", () => {
  const menu = chipMenu(filled("codex"), "codex", "", "");
  assert.deepEqual(menu.model, [{ value: "", label: "Default", current: true }]);
  assert.equal(menu.effort, null);
  assert.deepEqual(menu.harness.map((item) => item.current), [false, true]);
  assert.equal(chipMenu(filled("codex"), "codex", "gpt-5", "").typed, "gpt-5");
});

test("the chip reads the values the form is filled with until one is set here", () => {
  const fields = filled("claude", [row("model", "sonnet"), row("effort", "medium")]);
  const face = (set) => chipFace("claude", valueOf(fields, set, "model"), valueOf(fields, set, "effort"));
  assert.equal(face({}), "claude · sonnet · medium");
  assert.equal(face({ model: "opus" }), "claude · opus · medium");
  assert.equal(face({ model: null, effort: null }), "claude · default model");
  assert.deepEqual(formValues(fields, { model: null, effort: "high" }), { harness: "claude", model: null, effort: "high" });
});

// --- where it runs -------------------------------------------------------------------------

const slot = (number, state, extra = {}) =>
  ({ number, state, launchable: false, holder: null, until: null, taken_by: null, ...extra });
const TARGETS = {
  state: "up", detail: null, new_slot: true, default: { target: "slot", slot: 2 },
  slots: [
    slot(1, "occupied", { taken_by: { request_id: "r1", label: "planner" } }),
    slot(2, "free", { launchable: true }),
    slot(3, "free", { holder: "reviewer", until: 1900000000.5 }),
    slot(4, "missing"),
    slot(5, "occupied"),
    slot(6, "ended", { launchable: true }),
  ],
};
const NONE_FREE = { ...TARGETS, default: { target: "slot", slot: null }, slots: [TARGETS.slots[0], TARGETS.slots[3]] };
const NO_SLOTS = { state: "up", detail: null, slots: [], new_slot: false, default: { target: "terminal", slot: null } };
const SLOT_2 = { target: "slot", slot: 2, named: false };
const NEW_SLOT = { target: "slot", slot: null, named: false };
const clock = () => "20:42";

test("the listing's default is the lowest free slot, a new slot with none free, the terminal without slots", () => {
  assert.deepEqual(parseTargets(TARGETS).default, SLOT_2);
  assert.deepEqual(parseTargets(TARGETS).slots.map((row) => row.number), [1, 2, 3, 4, 5, 6]);
  assert.deepEqual(parseTargets(NONE_FREE).default, NEW_SLOT);
  assert.deepEqual(parseTargets(NO_SLOTS), NO_TARGETS);
});

test("a listing that can't be read offers no slot and the command to copy", () => {
  assert.deepEqual(parseTargets(null), NO_TARGETS);
  assert.deepEqual(parseTargets({ ...NO_SLOTS, state: "error", detail: "RuntimeError" }), NO_TARGETS);
  assert.deepEqual(parseTargets({ state: "up" }), NO_TARGETS);
  // Slots that are there and can't be read are told apart from no slots at all.
  assert.equal(NO_TARGETS.unread, false);
  assert.deepEqual(parseTargets({ ...NO_SLOTS, unread: true }), UNREAD_TARGETS);
  assert.deepEqual(parseTargets({ ...NO_SLOTS, state: "error", detail: "RuntimeError", unread: true }), UNREAD_TARGETS);
  assert.deepEqual(parseTargets({ ...NO_SLOTS, unread: false }), NO_TARGETS);
  assert.equal(parseTargets(TARGETS).unread, false);
  assert.deepEqual(UNREAD_TARGETS.default, { target: "terminal" });
  // A request with no answer: unread only where the reading before it had slots.
  assert.deepEqual(afterNoAnswer(null), NO_TARGETS);
  assert.deepEqual(afterNoAnswer(parseTargets(NO_SLOTS)), NO_TARGETS);
  assert.deepEqual(afterNoAnswer(parseTargets(TARGETS)), UNREAD_TARGETS);
  assert.deepEqual(afterNoAnswer(UNREAD_TARGETS), UNREAD_TARGETS);
  // A default that names a slot the listing does not show free is a new slot's.
  assert.deepEqual(parseTargets({ ...TARGETS, default: { target: "slot", slot: 1 } }).default, NEW_SLOT);
  // Headless is never the default, whatever the answer says.
  assert.deepEqual(parseTargets({ ...NO_SLOTS, default: { target: "headless", slot: null } }).default, { target: "terminal" });
});

test("the button's four words", () => {
  assert.equal(buttonWords(SLOT_2), "Launch in slot 2");
  assert.equal(buttonWords(NEW_SLOT), "Launch in a new slot");
  assert.equal(buttonWords({ target: "terminal" }), "Copy command");
  assert.equal(buttonWords({ target: "headless" }), "Launch headless");
});

test("with no pick the launch runs at the listing's default, and a pick holds while it is offered", () => {
  const targets = parseTargets(TARGETS);
  assert.deepEqual(runOf(targets, null), SLOT_2);
  const six = { target: "slot", slot: 6, named: true };
  assert.deepEqual(runOf(targets, six), six);
  assert.deepEqual(runOf(targets, { target: "headless" }), { target: "headless" });
  assert.deepEqual(runOf(parseTargets(NO_SLOTS), { target: "headless" }), { target: "headless" });
  // A picked slot that was taken since, or is gone, falls back to the default.
  assert.deepEqual(runOf(parseTargets(NONE_FREE), six), NEW_SLOT);
  assert.deepEqual(runOf(parseTargets(NO_SLOTS), six), { target: "terminal" });
});

test("the menu's rows say each slot's state, and only a free one can be picked", () => {
  const menu = runMenu(parseTargets(TARGETS), SLOT_2, clock);
  assert.deepEqual(menu.slots.map((row) => [row.lead, row.words, row.run !== null, row.current]), [
    ["Slot 1", "taken · planner", false, false],
    ["Slot 2", "free", true, true],
    ["Slot 3", "held for reviewer until 20:42", false, false],
    ["Slot 4", "not running", false, false],
    ["Slot 5", "taken", false, false],
    ["Slot 6", "free", true, false],
  ]);
  assert.deepEqual(menu.slots[5].run, { target: "slot", slot: 6, named: true });
  assert.deepEqual(menu.others.map((row) => [row.lead, row.words, row.current]), [
    ["Your terminal", "copies the command for you to paste", false],
    ["Headless", "no terminal, runs in the background", false],
  ]);
  assert.equal(slotWords({ ...parseTargets(TARGETS).slots[2], until: null }), "held for reviewer");
});

test("a new slot is offered only while no slot is free, and is then the ticked default", () => {
  assert.equal(runMenu(parseTargets(TARGETS), SLOT_2, clock).slots.some((row) => row.lead === "A new slot"), false);
  const menu = runMenu(parseTargets(NONE_FREE), NEW_SLOT, clock);
  assert.deepEqual(menu.slots.map((row) => [row.lead, row.current]), [["Slot 1", false], ["Slot 4", false], ["A new slot", true]]);
  assert.deepEqual(menu.slots[2].run, NEW_SLOT);
});

test("without slots the menu is the terminal, ticked, and headless", () => {
  const menu = runMenu(parseTargets(NO_SLOTS), { target: "terminal" });
  assert.deepEqual(menu.slots, []);
  assert.deepEqual(menu.others.map((row) => [row.lead, row.current]), [["Your terminal", true], ["Headless", false]]);
  assert.equal(runMenu(parseTargets(NO_SLOTS), { target: "headless" }).others[1].current, true);
});

// --- the request ---------------------------------------------------------------------------

const FORM_FIELDS = [
  field("harness", { type: "enum", values: ["claude", "codex"], value: "claude" }),
  field("brief", { value: "" }), field("cwd", { type: "path", value: "" }), field("project", { value: "" }),
  field("model", { value: "" }), field("instructions", { value: "" }),
  field("target", { type: "enum", values: ["terminal", "headless", "slot"], default: "terminal", value: "headless" }),
  field("slot", { value: "7" }),
];
const SET = { brief: "fix the build", project: null, cwd: "~/code" };

test("the request always says where it runs, and names a slot only for one picked by its number", () => {
  const byDefault = launchRequest(FORM_FIELDS, null, SET, SLOT_2);
  assert.deepEqual(byDefault.values, { harness: "claude", brief: "fix the build", cwd: "~/code", target: "slot" });
  assert.deepEqual(byDefault.leave_out.sort(), ["instructions", "model", "project", "slot"]);
  assert.equal("off_posture" in byDefault || "seat" in byDefault || "project_preset" in byDefault, false);
  const picked = launchRequest(FORM_FIELDS, null, SET, { target: "slot", slot: 6, named: true });
  assert.equal(picked.values.slot, "6");
  assert.equal(picked.leave_out.includes("slot"), false);
  assert.equal(launchRequest(FORM_FIELDS, null, SET, NEW_SLOT).leave_out.includes("slot"), true);
});

test("a target or slot the form is filled with never places the launch", () => {
  // The form's rows say headless and slot 7; the button says the terminal.
  const sent = launchRequest(FORM_FIELDS, null, SET, { target: "terminal" });
  assert.equal(sent.values.target, "terminal");
  assert.equal("slot" in sent.values, false);
  assert.equal(sent.leave_out.includes("slot"), true);
  assert.equal(launchRequest(FORM_FIELDS, null, SET, { target: "headless" }).values.target, "headless");
});

test("an install with no slot row sends no slot either way", () => {
  const fields = FORM_FIELDS.map((item) => item.name === "slot" ? { ...item, needs_absent: true } : item);
  const sent = launchRequest(fields, null, SET, { target: "terminal" });
  assert.equal("slot" in sent.values || sent.leave_out.includes("slot"), false);
});

test("an empty box leaves out a brief the form withheld, and the box's text replaces it", () => {
  const form = { state: "up", detail: null, harness: "claude", refused: null, rows: [
    { name: "brief", value: null, items: null, source: "defaults", withheld: true },
    { name: "instructions", value: null, items: null, source: "defaults", withheld: true }] };
  assert.equal(launchRequest(FORM_FIELDS, null, { ...SET, brief: null }, SLOT_2).leave_out.includes("brief"), true);
  const empty = launchRequest(FORM_FIELDS, form, { ...SET, brief: null }, SLOT_2);
  assert.equal("brief" in empty.values, false);
  assert.equal(empty.leave_out.includes("brief"), true);
  // Standing instructions the form withheld are no part of the box, and are kept.
  assert.equal("instructions" in empty.values || empty.leave_out.includes("instructions"), false);
  assert.equal(launchRequest(FORM_FIELDS, form, SET, SLOT_2).values.brief, "fix the build");
});

test("a brief file the form is filled with is left out, with the box empty and with text in it", () => {
  const fields = [...FORM_FIELDS, field("brief_file", { type: "path", value: "notes/first.md" })];
  const empty = launchRequest(fields, null, { ...SET, brief: null }, SLOT_2);
  assert.equal("brief_file" in empty.values, false);
  assert.equal(empty.leave_out.includes("brief_file") && empty.leave_out.includes("brief"), true);
  const typed = launchRequest(fields, null, SET, SLOT_2);
  assert.equal(typed.values.brief, "fix the build");
  assert.equal("brief_file" in typed.values, false);
  assert.equal(typed.leave_out.includes("brief_file"), true);
  // A form with no such row sends nothing of it.
  assert.equal(launchRequest(FORM_FIELDS, null, SET, SLOT_2).leave_out.includes("brief_file"), false);
});

// --- the launch, and the lines under the box -----------------------------------------------

const PREVIEW = { refused: [], harness: "claude", mode: "interactive", target: "slot", argv: ["claude"], stamps: [],
                  first_turn: null, not_applied: [], warnings: [], left_out: [], command: null,
                  request_id: "r1", ticket: "r1.9.abc", expires_at: "2026-11-10T19:10:00Z" };
const ok = (data) => ({ status: 200, data });
const refusal = (problem, hint, code = "bad-value") => ({ code, row: "cwd", problem, hint });
const launch = (state, extra = {}) => ({ request_id: "r1", created_at: "2026-11-10T19:06:00Z", updated_at: "", harness: "claude",
                                      target: "slot", state, reason: null, stale: false, slot: "2", cwd: "/srv/x", ...extra });
const status = (...rows) => ok({ state: "up", detail: null, launches: rows, refused: null });
const STARTED = { status: 202, data: { status: "/api/launches?request=r1" } };
const texts = (flight) => [...flight.lines, ...flight.notes].map((line) => line.text);
const back = (flight) => !inFlight(flight);

test("a press turns the button off and takes the last launch's lines away", () => {
  assert.equal(inFlight(PRESSED), true);
  assert.deepEqual(texts(PRESSED), []);
  assert.equal(inFlight(IDLE), false);
});

test("a refused preview gives the button back with each refusal's problem and hint", () => {
  const answer = previewed(ok({ ...PREVIEW, ticket: null, refused: [
    refusal("the folder /nope is not there", "name a folder that exists"), refusal("claude is not found.", "")] }));
  assert.equal(answer.next, "end");
  assert.equal(back(answer.flight), true);
  assert.deepEqual(texts(answer.flight), ["the folder /nope is not there. name a folder that exists", "claude is not found"]);
  assert.deepEqual(answer.flight.lines.map((line) => line.name), ["refused", "refused"]);
  assert.equal(answer.flight.started, false);
});

test("a preview that can't be read ends the launch and says why", () => {
  assert.deepEqual(texts(previewed({ status: 0, data: null }).flight), ["The launch failed: no answer from the shell"]);
  assert.deepEqual(texts(previewed({ status: 503, data: { error: "busy" } }).flight),
                   ["The launch failed: the launcher's page server answered 503: busy"]);
  assert.deepEqual(texts(previewed(ok({ ...PREVIEW, ticket: null })).flight),
                   ["The launch failed: the launcher's page server gave no ticket to launch with"]);
});

test("a preview with a ticket is launched, and one for the terminal gives its command to copy", () => {
  assert.deepEqual(previewed(ok(PREVIEW)), { next: "launch", ticket: "r1.9.abc", notes: [] });
  const terminal = previewed(ok({ ...PREVIEW, target: "terminal", ticket: null, command: "moxi launcher run --cwd /srv/x" }));
  assert.deepEqual(terminal, { next: "copy", command: "moxi launcher run --cwd /srv/x", notes: [] });
});

test("the command copied ends the launch with a line, and one the browser would not copy is shown", () => {
  const done = copied("moxi launcher run", true, []);
  assert.deepEqual([back(done), texts(done), done.started], [true, ["Copied. Paste it in your terminal."], false]);
  const shown = copied("moxi launcher run", false, []);
  assert.equal(back(shown), true);
  assert.deepEqual(shown.lines.map((line) => [line.name, line.command]), [["not-copied", "moxi launcher run"]]);
});

test("warnings are dim lines, and the rows left out are named without slot and target", () => {
  const notes = notesOf(parsePreview({ ...PREVIEW, warnings: ["the model is old"], left_out: ["slot", "model", "target", "effort"] }));
  assert.deepEqual(notes.map((line) => [line.tone, line.text]), [["dim", "the model is old"], ["dim", "Left out: model, effort"]]);
  assert.deepEqual(notesOf(parsePreview({ ...PREVIEW, left_out: ["slot", "target"] })), []);
  // They stay under the line of every end that follows the preview.
  assert.deepEqual(texts(copied("moxi launcher run", true, notes)).slice(1), ["the model is old", "Left out: model, effort"]);
});

test("a started job keeps the button off and names the slot the button named", () => {
  const { flight, copy } = launched(STARTED, SLOT_2, []);
  assert.equal(copy, null);
  assert.deepEqual([flight.phase, inFlight(flight), following(flight), texts(flight)],
                   ["starting", true, true, ["Starting in slot 2…"]]);
  assert.equal(flight.status.path, "api/launches?request=r1");
  assert.deepEqual(texts(launched(STARTED, NEW_SLOT, []).flight), ["Starting…"]);
  assert.deepEqual(texts(launched(STARTED, { target: "headless" }, []).flight), ["Starting…"]);
});

test("every answer of the launch route that starts nothing gives the button back with its words", () => {
  const refused = launched(ok({ refused: refusal("the ticket is too old", "preview the launch again", "expired"), command: null }), SLOT_2, []);
  assert.deepEqual([back(refused.flight), texts(refused.flight)], [true, ["the ticket is too old. preview the launch again"]]);
  const used = launched({ status: 409, data: { conflict: "the ticket for launch r1 was used already" } }, SLOT_2, []);
  assert.deepEqual([back(used.flight), texts(used.flight)], [true, ["the ticket for launch r1 was used already"]]);
  const gone = launched({ status: 0, data: null }, SLOT_2, []);
  assert.deepEqual([back(gone.flight), texts(gone.flight)], [true, ["The launch failed: no answer from the shell"]]);
  const lost = launched({ status: 202, data: {} }, SLOT_2, []);
  assert.equal(back(lost.flight), true);
  assert.match(texts(lost.flight)[0], /^The launch failed: /);
  // A terminal launch is never run by the page: its command is copied instead.
  const terminal = launched(ok({ refused: refusal("a launch in a terminal is not run by the page", "", "terminal"),
                                 command: "moxi launcher run" }), { target: "terminal" }, []);
  assert.equal(terminal.copy, "moxi launcher run");
});

test("a row that started ends the launch, says where and when, and empties the box", () => {
  const { flight } = launched(STARTED, SLOT_2, []);
  for (const state of LAUNCHED) {
    const done = followed(flight, status(launch(state, { slot: "3" })), clock);
    assert.deepEqual([back(done), following(done), done.started, texts(done)], [true, false, true, ["Started in slot 3 · 20:42"]]);
  }
  assert.equal(startedWords(launch("started", { target: "headless", slot: null }), clock), "Started headless · 20:42");
  assert.equal(startedWords(launch("started", { created_at: "" }), clock), "Started in slot 2");
});

test("a failed row, or a refusal met before the row, ends the launch with the reason and keeps the box", () => {
  const { flight } = launched(STARTED, SLOT_2, []);
  const failed = followed(flight, status(launch("failed", { reason: "slot 2 is occupied" })));
  assert.deepEqual([back(failed), following(failed), failed.started, texts(failed)],
                   [true, false, false, ["The launch failed: slot 2 is occupied"]]);
  const refused = followed(flight, ok({ state: "up", detail: null, launches: [],
                                        refused: refusal("codex is not found on the PATH", "install it") }));
  assert.deepEqual([back(refused), refused.started, texts(refused)],
                   [true, false, ["The launch failed: codex is not found on the PATH. install it"]]);
});

test("a failed row shows the sentence recorded for it, and its reason word where none was", () => {
  const flight = launched(STARTED, SLOT_2, []).flight;
  const row = launch("failed", { reason: "occupied" });
  const told = followed(flight, ok({ state: "up", detail: null, launches: [row], refused: null,
                                     why: "slots refused the slot: slot 2 is taken" }));
  assert.deepEqual(texts(told), ["The launch failed: slots refused the slot: slot 2 is taken"]);
  assert.deepEqual(texts(followed(flight, ok({ state: "up", detail: null, launches: [row], refused: null, why: null }))),
                   ["The launch failed: occupied"]);
  assert.deepEqual(texts(followed(flight, status(row))), ["The launch failed: occupied"]);
});

test("the two lines that send the user to the launcher's page are cut around its name", () => {
  const flight = launched(STARTED, SLOT_2, []).flight;
  const late = followed(flight, status(launch("spawning", { stale: true })));
  assert.deepEqual(aroundPage(late.lines[0]), ["Not started yet, and it should have been by now. The ", " lists it."]);
  assert.deepEqual(aroundPage(stopped(flight).lines[0]), ["Stopped watching this launch. The ", " lists it."]);
  assert.equal(aroundPage(flight.lines[0]), null);
  assert.equal(aroundPage({ name: "warning", tone: "dim", text: "The Launcher page is elsewhere" }), null);
});

test("no row yet, a row still starting, and a read that failed change nothing", () => {
  const { flight } = launched(STARTED, SLOT_2, []);
  assert.equal(followed(flight, status()), flight);
  assert.equal(followed(flight, status(launch("spawning"))), flight);
  assert.equal(followed(flight, { status: 0, data: null }), flight);
  assert.equal(followed(IDLE, status(launch("started"))), IDLE);
});

test("a row that should have started gives the button back and is followed on to its end", () => {
  const { flight } = launched(STARTED, SLOT_2, []);
  const late = followed(flight, status(launch("spawning", { stale: true })));
  assert.deepEqual([late.phase, back(late), following(late), late.started, texts(late)],
                   ["late", true, true, false, ["Not started yet, and it should have been by now. The Launcher page lists it."]]);
  assert.equal(followed(late, status(launch("spawning", { stale: true }))), late);
  assert.deepEqual(texts(followed(late, status(launch("started")), clock)), ["Started in slot 2 · 20:42"]);
  assert.deepEqual(texts(followed(late, status(launch("failed", { reason: "tmux is gone" })))), ["The launch failed: tmux is gone"]);
});

test("the end of the follow gives the button back, with no row or a row still starting", () => {
  const { flight } = launched(STARTED, SLOT_2, []);
  const late = followed(flight, status(launch("spawning", { stale: true })));
  for (const open of [flight, late]) {
    const done = stopped(open);
    assert.deepEqual([back(done), following(done), done.started, texts(done)],
                     [true, false, false, ["Stopped watching this launch. The Launcher page lists it."]]);
  }
  assert.equal(stopped(IDLE), IDLE);
});

test("an edit of the box takes an ended launch's lines away and leaves a starting one's", () => {
  const { flight } = launched(STARTED, SLOT_2, []);
  assert.equal(edited(flight), flight);
  assert.equal(edited(PRESSED), PRESSED);
  assert.equal(edited(copied("moxi launcher run", true, [])), IDLE);
  const late = followed(flight, status(launch("spawning", { stale: true })));
  assert.deepEqual([edited(late), following(edited(late))], [IDLE, false]);
});
