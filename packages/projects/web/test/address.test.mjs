import assert from "node:assert/strict";
import test from "node:test";

import {
  addressPath, keyIn, listState, NOT_ANSWERING, NOTHING_OPEN, noSuchProject, parseRoute, pathToWrite, READING,
  READING_AFTER_MS, routeOf,
} from "../src/address.ts";

const listed = (key) => ({
  key, root: `/home/me/code/${key}`, kind: "project", lead: null, associated: [], agents: [], notes_root: null,
  created: "2026-01-02T03:04:05Z", exists: true,
});

// -- routes ------------------------------------------------------------------------------------

test("each route opens what it names", () => {
  assert.deepEqual(parseRoute(""), NOTHING_OPEN);
  assert.deepEqual(parseRoute("open/atlas-api"), { piece: "open", key: "atlas-api" });
  assert.deepEqual(parseRoute("edit/atlas-api"), { piece: "edit", key: "atlas-api" });
  assert.deepEqual(parseRoute("remove/atlas-api"), { piece: "remove", key: "atlas-api" });
  assert.deepEqual(parseRoute("add"), { piece: "add" });
  // A slash either side is the same route; a key is decoded.
  assert.deepEqual(parseRoute("/open/atlas-api/"), { piece: "open", key: "atlas-api" });
  assert.deepEqual(parseRoute("open/delta.web"), { piece: "open", key: "delta.web" });
  assert.deepEqual(parseRoute("open/a%20b"), { piece: "open", key: "a b" });
});

test("a route that names nothing the page opens is the list", () => {
  for (const route of ["open", "open/", "add/atlas-api", "show/atlas-api", "open/a/b", "open/%E0%A4%A", "projects"]) {
    assert.deepEqual(parseRoute(route), NOTHING_OPEN, route);
  }
});

test("an address goes back to its route and path", () => {
  for (const route of ["", "open/atlas-api", "edit/atlas-api", "remove/atlas-api", "add"]) {
    assert.equal(routeOf(parseRoute(route)), route);
  }
  assert.equal(routeOf({ piece: "open", key: "a b/c" }), "open/a%20b%2Fc");
  assert.deepEqual(parseRoute(routeOf({ piece: "open", key: "a b/c" })), { piece: "open", key: "a b/c" });
  assert.equal(addressPath({ piece: "open", key: "atlas-api" }), "/m/projects/open/atlas-api");
  assert.equal(addressPath(NOTHING_OPEN), "/m/projects/");
});

test("the address is written only at the page, and only when it differs", () => {
  const open = { piece: "open", key: "atlas-api" };
  assert.equal(pathToWrite(open, "/m/projects/"), "/m/projects/open/atlas-api");
  assert.equal(pathToWrite(open, "/m/projects"), "/m/projects/open/atlas-api");
  assert.equal(pathToWrite(open, "/m/projects/open/atlas-api"), null);
  assert.equal(pathToWrite(NOTHING_OPEN, "/m/projects/open/atlas-api"), "/m/projects/");
  // Another page is in the document: the address is its own.
  assert.equal(pathToWrite(open, "/m/usage/"), null);
  assert.equal(pathToWrite(open, "/m/projectsx/"), null);
});

test("a key is matched ignoring case, an exact match first", () => {
  const projects = [listed("atlas-api"), listed("Beta"), listed("beta")];
  assert.equal(keyIn(projects, "atlas-api"), "atlas-api");
  assert.equal(keyIn(projects, "ATLAS-API"), "atlas-api");
  assert.equal(keyIn(projects, "Beta"), "Beta");
  assert.equal(keyIn(projects, "beta"), "beta");
  assert.equal(keyIn(projects, "nope"), null);
  assert.equal(keyIn([], "atlas-api"), null);
  assert.equal(noSuchProject("nope"), "No project is named nope.");
});

// -- the list's states ----------------------------------------------------------------------------

const read = (over) => ({ value: null, failure: null, gone: false, ...over });
const LISTED = { projects: [listed("atlas-api")] };

test("loading shows nothing, then Reading… after 400 ms", () => {
  assert.deepEqual(listState(read({}), 0), { state: "loading", line: null, rows: false, acts: false });
  assert.deepEqual(listState(read({}), READING_AFTER_MS - 1).line, null);
  assert.deepEqual(listState(read({}), READING_AFTER_MS), { state: "loading", line: READING, rows: false, acts: false });
  assert.equal(READING, "Reading…");
});

test("read: up with projects, empty without, each with its acts", () => {
  assert.deepEqual(listState(read({ value: LISTED }), 5000), { state: "up", line: null, rows: true, acts: true });
  assert.deepEqual(listState(read({ value: { projects: [] } }), 0), { state: "empty", line: null, rows: true, acts: true });
});

test("a page server that isn't answering keeps the last list, with no acts", () => {
  for (const failure of [{ status: 503, why: "projects' page server answered 503", said: null },
                         { status: 0, why: "no answer from the shell", said: null },
                         { status: 502, why: "projects' page server answered 502", said: "bad gateway" },
                         { status: 200, why: "projects' page server sent an answer that can't be read", said: null }]) {
    assert.deepEqual(listState(read({ value: LISTED, failure }), 0),
                     { state: "down", line: NOT_ANSWERING, rows: true, acts: false }, failure.status);
    assert.deepEqual(listState(read({ failure }), 0), { state: "down", line: NOT_ANSWERING, rows: false, acts: false });
  }
  assert.equal(NOT_ANSWERING, "Projects' page server isn't answering.");
});

test("a store that refuses gives its reason and fix, and no rows or acts", () => {
  const said = "the projects store at /h/projects.db is newer than this code; fix: upgrade moxi-projects";
  for (const status of [422, 409]) {
    assert.deepEqual(listState(read({ value: LISTED, failure: { status, why: "", said } }), 0),
                     { state: "refused", line: said, rows: false, acts: false });
  }
  // A 422 with no line of its own is not one the store gave.
  assert.equal(listState(read({ failure: { status: 422, why: "x", said: null } }), 0).state, "down");
});

test("projects gone is drawn as nothing of the list", () => {
  assert.deepEqual(listState(read({ value: LISTED, gone: true }), 0), { state: "gone", line: null, rows: false, acts: false });
});
