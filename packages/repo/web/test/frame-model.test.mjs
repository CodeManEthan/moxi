import assert from "node:assert/strict";
import test from "node:test";

import {
  CANT_READ, noSuchRepo, pageState, parseRoute, pathToWrite, READING, routeOf, rowNamed, SERVER_ERROR,
} from "../src/frame-model.ts";
import { repoRow } from "../src/rows-model.ts";
import { HOME, repo, value } from "./fixtures.mjs";

test("addresses: each one read and written, a ~ name encoded", () => {
  const cases = [
    ["", { piece: "list" }], ["add", { piece: "add" }], ["pieces", { piece: "pieces" }],
    ["repo/atlas-api", { piece: "repo", name: "atlas-api" }],
    ["repo/~%2Fcode%2Ffinch", { piece: "repo", name: "~/code/finch" }],
    ["repo/~%2Fnotes/add", { piece: "repo-add", name: "~/notes" }],
    ["project/orbit-cli", { piece: "project", key: "orbit-cli" }],
  ];
  for (const [route, address] of cases) {
    assert.deepEqual(parseRoute(route), address, route);
    assert.equal(routeOf(address), route);
  }
  for (const odd of ["repo", "repo/", "repo/%E0", "repo/a/b", "elsewhere/x", "project/a/add"]) {
    assert.deepEqual(parseRoute(odd), { piece: "list" }, odd);
  }
  assert.equal(pathToWrite({ piece: "repo", name: "~/notes" }, "/m/repo/"), "/m/repo/repo/~%2Fnotes");
  assert.equal(pathToWrite({ piece: "list" }, "/m/repo/"), null);
  assert.equal(pathToWrite({ piece: "add" }, "/m/ui/"), null);
});

test("an address names a row by name, ~ folder or project; none gives the foot line", () => {
  const rows = [repo({ project: "atlas-api" }), repo({ root: `${HOME}/notes` })].map((r) => repoRow(r, { home: HOME }));
  assert.equal(rowNamed(parseRoute("repo/atlas-api"), rows, HOME), rows[0]);
  assert.equal(rowNamed(parseRoute("repo/~%2Fnotes"), rows, HOME), rows[1]);
  assert.equal(rowNamed(parseRoute("repo/~%2Fcode%2Fatlas-api"), rows, HOME), rows[0]);
  assert.equal(rowNamed(parseRoute("project/atlas-api"), rows, HOME), rows[0]);
  assert.equal(rowNamed(parseRoute("repo/kestrel"), rows, HOME), null);
  assert.equal(noSuchRepo(parseRoute("repo/kestrel")), "No repo is named kestrel.");
  assert.equal(noSuchRepo(parseRoute("pieces")), null);
});

test("states: reading, can't be read with the last value kept, the listing's error, up, gone", () => {
  const up = value();
  assert.deepEqual(pageState({ value: null, failure: null, gone: false }, 100), { state: "loading", line: null, drawn: false, acts: false });
  assert.equal(pageState({ value: null, failure: null, gone: false }, 400).line, READING);
  assert.deepEqual(pageState({ value: up, failure: { status: 0, why: "x" }, gone: false }, 0),
                   { state: "down", line: CANT_READ, drawn: true, acts: false });
  assert.equal(pageState({ value: null, failure: { status: 502, why: "x" }, gone: false }, 0).drawn, false);
  assert.deepEqual(pageState({ value: value({ state: "error", detail: "the index can't be read" }), failure: null, gone: false }, 0),
                   { state: "error", line: "the index can't be read", drawn: false, acts: false });
  assert.equal(pageState({ value: value({ state: "error", detail: null }), failure: null, gone: false }, 0).line, SERVER_ERROR);
  assert.deepEqual(pageState({ value: up, failure: null, gone: false }, 0), { state: "up", line: null, drawn: true, acts: true });
  assert.equal(pageState({ value: up, failure: null, gone: true }, 0).state, "gone");
});
