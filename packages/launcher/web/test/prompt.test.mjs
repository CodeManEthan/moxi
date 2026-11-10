import assert from "node:assert/strict";
import test from "node:test";

import {
  firstOpen, folderFace, formValues, HOME, openWhere, parsePlaces, pickOf, pickValues, placed, placeRows,
  placeTime, projectKeys, samePick, tilde, typedFolder, valueOf, whereFace, withProjects,
} from "../src/prompt-model.ts";

const place = (project_key, cwd, at = "2026-11-10T18:45:00Z") => ({ project_key, cwd, at });
const PLACES = { state: "up", detail: null, projects: true, actor: "operator",
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
    assert.deepEqual(where, { withProjects: false, places: [], pick: { kind: "folder", path: "~" } });
    assert.deepEqual(asked, ["launcher api/places"]);
  }
});

test("a places read that fails opens on the home folder with no projects", async () => {
  const { read, asked } = reads({});
  assert.deepEqual(await openWhere(read, true), { withProjects: false, places: [], pick: { kind: "folder", path: "~" } });
  assert.deepEqual(asked, ["launcher api/places"]);
});

// --- folders -------------------------------------------------------------------------------

test("the home folder is written ~ and any other path as it is", () => {
  assert.equal(tilde("/home/alice/code/proj-a"), "~/code/proj-a");
  assert.equal(tilde("/home/alice"), "~");
  assert.equal(tilde("/Users/alice/notes/"), "~/notes");
  assert.equal(tilde("/root/x"), "~/x");
  assert.equal(tilde("/homework/alice"), "/homework/alice");
  assert.equal(tilde("/srv/proj-b"), "/srv/proj-b");
  assert.equal(tilde("~/notes"), "~/notes");
  assert.equal(tilde("/"), "/");
});

test("a folder's face is the dim path and its own name", () => {
  assert.deepEqual(folderFace("/home/alice/code/proj-a"), { dim: "~/code/", name: "proj-a" });
  assert.deepEqual(folderFace("/srv/proj-b"), { dim: "/srv/", name: "proj-b" });
  assert.deepEqual(folderFace("/home/alice"), { dim: "", name: "~" });
  assert.deepEqual(folderFace("~"), { dim: "", name: "~" });
  assert.deepEqual(folderFace("/"), { dim: "", name: "/" });
  assert.deepEqual(folderFace("/srv"), { dim: "/", name: "srv" });
});

test("a long folder is cut in the middle of its dim part, keeping the first segment and the name", () => {
  assert.deepEqual(folderFace("/home/alice/one/two/three/four/proj-a", 16), { dim: "~/…/four/", name: "proj-a" });
  assert.deepEqual(folderFace("/home/alice/one/two/three/four/proj-a", 24), { dim: "~/…/three/four/", name: "proj-a" });
  assert.deepEqual(folderFace("/var/lib/one/two/proj-a", 16), { dim: "/var/…/", name: "proj-a" });
  assert.deepEqual(folderFace("/home/alice/one/a-very-long-folder-name-that-is-never-cut", 20),
                   { dim: "~/…/", name: "a-very-long-folder-name-that-is-never-cut" });
  // Nothing between the first segment and the name to cut.
  assert.deepEqual(folderFace("/home/alice/a-very-long-folder-name", 10), { dim: "~/", name: "a-very-long-folder-name" });
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
  assert.deepEqual(whereFace({ kind: "project", key: "proj-a" }), { word: "Project", dim: "", name: "proj-a" });
  assert.deepEqual(whereFace({ kind: "folder", path: "/home/alice/code/proj-a" }),
                   { word: "Folder", dim: "~/code/", name: "proj-a" });
  assert.deepEqual(whereFace({ kind: "folder", path: "~" }), { word: "Folder", dim: "", name: "~" });
  assert.deepEqual(whereFace({ kind: "choose" }), { word: "Project", dim: "", name: "choose" });
});

test("two picks are the same place by key, or by folder however home is written", () => {
  assert.equal(samePick({ kind: "folder", path: "~/notes" }, { kind: "folder", path: "/home/alice/notes/" }), true);
  assert.equal(samePick({ kind: "project", key: "proj-a" }, { kind: "project", key: "proj-a" }), true);
  assert.equal(samePick({ kind: "project", key: "notes" }, { kind: "folder", path: "notes" }), false);
  assert.equal(samePick({ kind: "choose" }, { kind: "choose" }), false);
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
  const rows = placeRows(parsePlaces(PLACES).places, true, { kind: "project", key: "proj-b" }, now, () => "t", () => "d");
  assert.deepEqual(rows.map((row) => [row.dim, row.name, row.current]),
                   [["", "proj-a", false], ["~/", "notes", false], ["", "proj-b", true]]);
  assert.deepEqual(rows[0].pick, { kind: "project", key: "proj-a" });
  assert.deepEqual(rows[1].pick, { kind: "folder", path: "/home/alice/notes" });
});

test("without projects every place is its folder and a pick of it sets the folder", () => {
  const now = new Date("2026-11-10T19:00:00Z");
  const rows = placeRows(parsePlaces(PLACES).places, false, { kind: "folder", path: "~/notes" }, now);
  assert.deepEqual(rows.map((row) => [row.dim, row.name, row.current]),
                   [["~/code/proj-a/", "web", false], ["~/", "notes", true], ["/srv/", "proj-b", false]]);
  assert.ok(rows.every((row) => row.pick.kind === "folder"));
  assert.deepEqual(pickValues(rows[0].pick), { project: null, cwd: "/home/alice/code/proj-a/web" });
});

test("two places drawn the same are one row, the newer", () => {
  const places = [place("proj-a", "/srv/x", "2026-11-10T18:00:00Z"), place(null, "/srv/x", "2026-11-09T18:00:00Z")];
  const rows = placeRows(places, false, { kind: "choose" }, new Date("2026-11-10T19:00:00Z"), () => "new", () => "old");
  assert.deepEqual(rows.map((row) => row.time), ["new"]);
  assert.equal(placeRows(places, true, { kind: "choose" }, new Date()).length, 2);
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
