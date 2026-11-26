import assert from "node:assert/strict";
import test from "node:test";

import { agentNames, factsReport, NOT_RUNNING, ORDER, parseGiven, VERSION } from "../src/facts-model.ts";
import { parseRoster } from "../src/feed.ts";

const ATLAS = { key: "atlas-api", lead: "builder-1", associated: ["planner", "reviewer"] };
const KESTREL = { key: "kestrel-web", lead: "builder-2", associated: [] };
const GIVEN = [ATLAS, KESTREL];

const address = (name, live, scope = null) => ({
  qualified: scope === null ? name : `${scope}/${name}`, name, scope, live, kind: live ? null : "agent", module: null,
  tier: null, adapter: live ? "claude-code" : null, harness: live ? "claude" : null, directors: [],
  policy_directors: [], session_id: live ? "s-1" : null, bound_at: null, awaiting: [], cache: null, holder: null,
  via: null,
});
const roster = (rows, state = "up") => parseRoster({ state, detail: null, rows, today: "2026-11-08" });
const NOT = { runs: false, state: NOT_RUNNING };

test("the report's frame: version 1, order 20, agents only", () => {
  const found = factsReport(roster([]), GIVEN, "atlas-api");
  assert.equal(found.version, VERSION);
  assert.equal(VERSION, 1);
  assert.equal(found.order, ORDER);
  assert.equal(ORDER, 20);
  assert.deepEqual(Object.keys(found.projects["atlas-api"]), ["agents"]);
  assert.equal(NOT_RUNNING, "not running now");
});

test("a name no live row holds is not running now; a live name says nothing, never runs", () => {
  const found = factsReport(roster([address("builder-1", true), address("planner", false)]), GIVEN, "atlas-api");
  assert.deepEqual(found.projects["atlas-api"].agents, { planner: NOT, reviewer: NOT });
  for (const say of Object.values(found.projects["atlas-api"].agents)) {
    assert.equal(say.runs, false);
    assert.equal("where" in say, false, "comms gives no where");
  }
});

test("a live row under any scope holds the name", () => {
  const found = factsReport(roster([address("builder-1", true, "team-a")]), GIVEN, "atlas-api");
  assert.equal("builder-1" in found.projects["atlas-api"].agents, false);
});

test("every name live: the project has nothing; only the open project is reported", () => {
  const rows = ["builder-1", "planner", "reviewer"].map((name) => address(name, true));
  assert.deepEqual(factsReport(roster(rows), GIVEN, "atlas-api").projects, {});
  assert.deepEqual(factsReport(roster([]), GIVEN, null).projects, {});
  assert.deepEqual(factsReport(roster([]), GIVEN, "kestrel-web").projects,
                   { "kestrel-web": { agents: { "builder-2": NOT } } });
  assert.deepEqual(factsReport(roster([]), GIVEN, "harbor-infra").projects, {}, "a key the page did not give");
  assert.deepEqual(factsReport(roster([]), [{ key: "lumen-docs", lead: null, associated: [] }], "lumen-docs").projects, {});
});

test("a roster comms can't say (offline, error) and a failed read report null, never not running", () => {
  assert.equal(factsReport(roster([], "offline"), GIVEN, "atlas-api"), null);
  assert.equal(factsReport(roster([], "error"), GIVEN, "atlas-api"), null);
  assert.equal(factsReport(null, GIVEN, "atlas-api"), null);
});

test("the given projects and their agents are read from the page's JSON", () => {
  const text = JSON.stringify([{ key: "a", root: "/r", lead: "x", associated: ["y", 3, "", "x"] },
                               { key: "b", root: "/r", lead: "", associated: "nope" }, { root: "/x" }]);
  assert.deepEqual(parseGiven(text), [{ key: "a", lead: "x", associated: ["y", "x"] },
                                      { key: "b", lead: null, associated: [] }]);
  assert.deepEqual(agentNames(parseGiven(text)[0]), ["x", "y"], "the lead first, each name once");
  assert.deepEqual(parseGiven(null), []);
  assert.deepEqual(parseGiven("{"), []);
});
