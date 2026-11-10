import assert from "node:assert/strict";
import test from "node:test";

import { fieldsFor, filledFields, NO_PRESET, parseCatalog, parseForm } from "../src/panel.ts";
import {
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
