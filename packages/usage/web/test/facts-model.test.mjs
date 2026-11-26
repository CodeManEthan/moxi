import assert from "node:assert/strict";
import test from "node:test";

import {
  clip, costSection, ESTIMATE, factsReport, figure, folderFacts, GATHERS, LABEL, ORDER, parseGiven, staysLine,
  TEXT_MAX_CHARS, VERSION,
} from "../src/facts-model.ts";
import { parseReport } from "../src/feed.ts";

const GIVEN = [{ key: "atlas-api" }, { key: "kestrel-web" }, { key: "orbit-cli" }];

/** One `/api/projects` row: the sums usage gives, with `cost` and `complete` as given. */
const row = (project, cost, { complete = true, cwd = null } = {}) => ({
  project, cwd, models: ["claude-sonnet-4-5"], requests: 3, input: 10, fresh_input: 10, cache_read: 0,
  cache_write: 0, cache_write_1h: 0, output: 5, tokens: 15, unpriced_tokens: 0, unknown_rows: 0,
  cost, complete, harness_cost: null, unknown: [], unpriced_models: [], rules: [],
});
/** `/api/projects`' answer holding `rows`, read through usage's own parser. */
const answer = (rows) => parseReport({
  today: "2026-11-08", since: null, until: null, tz: "local", filters: [], rows,
  total: row(null, 0), unpriced_models: [], rules: [], index: { passed: true, last_pass: null, note: null },
  by: "project", home: "/home/me",
});

const TODAY = answer([row("atlas-api", 8.49), row("kestrel-web", 3.29), row("orbit-cli", 0)]);
const WEEK = answer([row("atlas-api", 15.84), row("kestrel-web", 9.9),
                     row(null, 0.82, { cwd: "/home/me/code/finch-scripts" })]);
const MONTH = answer([row("atlas-api", 21.49), row("kestrel-web", 18.06)]);
const ALL = { today: TODAY, week: WEEK, month: MONTH };
const LINK = (key) => ({ text: "Usage", module: "usage", route: `project/${key}` });
const parts = (found, key) => found.projects[key]?.parts?.map((part) => part.text);
const costLine = (found, key) => found.projects[key]?.sections?.[0].lines[0].parts.map((part) => part.text);

test("the report's frame: version 1, order 50, gathers cost, no activity", () => {
  const found = factsReport(ALL, GIVEN, null);
  assert.equal(found.version, VERSION);
  assert.equal(VERSION, 1);
  assert.equal(found.order, ORDER);
  assert.equal(ORDER, 50);
  assert.equal(found.gathers, GATHERS);
  assert.equal(GATHERS, "cost");
  assert.equal("activity" in found, false);
  assert.equal(found.folders.every((folder) => !("at" in folder)), true, "usage gives no folder a time");
});

test("a closed row says today's cost, and nothing for a project with none or none above zero", () => {
  const found = factsReport(ALL, GIVEN, null);
  assert.deepEqual(parts(found, "atlas-api"), ["$8.49 today"]);
  assert.deepEqual(parts(found, "kestrel-web"), ["$3.29 today"]);
  assert.equal(found.projects["orbit-cli"], undefined, "$0.00 today says nothing");
  const none = factsReport({ ...ALL, today: answer([]) }, GIVEN, null);
  assert.deepEqual(none.projects, {});
});

test("a cost usage could not price is no figure; one it could not price whole ends +", () => {
  const today = answer([row("atlas-api", null, { complete: false }), row("kestrel-web", 8.49, { complete: false }),
                        row("orbit-cli", 0, { complete: false })]);
  const found = factsReport({ ...ALL, today }, GIVEN, null);
  assert.equal(found.projects["atlas-api"], undefined);
  assert.deepEqual(parts(found, "kestrel-web"), ["$8.49+ today"]);
  assert.deepEqual(parts(found, "orbit-cli"), ["$0.00+ today"], "a cost not whole says so even at zero");
  assert.equal(figure(row("x", null)), null);
  assert.equal(figure(row("x", 0.004)), "<$0.01");
  assert.equal(figure(row("x", 1234.5, { complete: false })), "$1,235+");
  assert.equal(figure(undefined), null);
});

test("the opened project's Cost row: today, 7 days, 30 days, an estimate, linking to Usage filtered", () => {
  const found = factsReport(ALL, GIVEN, "kestrel-web");
  const [section] = found.projects["kestrel-web"].sections;
  assert.equal(section.label, LABEL);
  assert.equal(LABEL, "Cost");
  assert.deepEqual(section.link, LINK("kestrel-web"));
  assert.deepEqual(costLine(found, "kestrel-web"), ["$3.29 today", "$9.90 in 7 days", "$18.06 in 30 days", ESTIMATE]);
  assert.equal(ESTIMATE, "an estimate");
  assert.equal(found.projects["atlas-api"].sections, undefined, "only the open project has sections");
  assert.equal(found.projects["atlas-api"].stays, undefined);
});

test("the Cost row carries only the figures a read gave, + where not whole, and is gone with none", () => {
  const month = answer([row("atlas-api", null), row("kestrel-web", 18.06, { complete: false })]);
  const found = factsReport({ ...ALL, month }, GIVEN, "atlas-api");
  assert.deepEqual(costLine(found, "atlas-api"), ["$8.49 today", "$15.84 in 7 days", ESTIMATE]);
  assert.deepEqual(costLine(factsReport({ ...ALL, month }, GIVEN, "kestrel-web"), "kestrel-web"),
                   ["$3.29 today", "$9.90 in 7 days", "$18.06+ in 30 days", ESTIMATE]);
  // orbit-cli has a cost of zero today and nothing else: a figure, so a row.
  assert.deepEqual(costLine(factsReport(ALL, GIVEN, "orbit-cli"), "orbit-cli"), ["$0.00 today", ESTIMATE]);
  const unknown = factsReport({ today: answer([]), week: answer([]), month: answer([]) },
                              [{ key: "lumen-docs" }], "lumen-docs");
  assert.equal(unknown.projects["lumen-docs"].sections, undefined, "no figure, no Cost row");
  assert.equal(costSection({ today: null, week: null, month: null }, "atlas-api"), null);
});

test("the opened project's Stays line names the key its cost stays listed under", () => {
  const found = factsReport(ALL, GIVEN, "atlas-api");
  assert.deepEqual(found.projects["atlas-api"].stays, ["what they cost, still listed under atlas-api on Usage"]);
  const bare = factsReport({ today: answer([]), week: null, month: null }, [{ key: "lumen-docs" }], "lumen-docs");
  assert.deepEqual(bare.projects["lumen-docs"], { stays: [staysLine("lumen-docs")] },
                   "while any read is good the line stays, figures or not");
});

test("an open key the page does not give has nothing", () => {
  const found = factsReport(ALL, GIVEN, "harbor-infra");
  assert.equal(found.projects["harbor-infra"], undefined);
});

test("folders in no project: the 7-day read's rows with no key and a folder, each with its figure", () => {
  const week = answer([row("atlas-api", 15.84), row(null, 0.82, { cwd: "/home/me/code/finch-scripts" }),
                       row(null, 2.5, { cwd: "/home/me/scratch", complete: false }),
                       row(null, null, { cwd: "/home/me/unpriced" }), row(null, 0, { cwd: "/home/me/free" }),
                       row(null, 1, { cwd: null }), row(null, 1, { cwd: "" })]);
  assert.deepEqual(folderFacts(week), [
    { folder: "/home/me/code/finch-scripts", parts: [{ text: "$0.82" }] },
    { folder: "/home/me/scratch", parts: [{ text: "$2.50+" }] },
  ]);
  assert.deepEqual(folderFacts(null), []);
  const found = factsReport({ today: TODAY, week, month: null }, GIVEN, null);
  assert.equal(found.folders.length, 2);
  const none = factsReport({ ...ALL, week: answer([row("atlas-api", 1)]) }, GIVEN, null);
  assert.equal("folders" in none, false, "no folder, no list");
});

test("each read failing in turn drops only what it fed", () => {
  // Today's read failed: no line-two figure, no today figure on the Cost row.
  let found = factsReport({ ...ALL, today: null }, GIVEN, "atlas-api");
  assert.equal(parts(found, "atlas-api"), undefined);
  assert.deepEqual(costLine(found, "atlas-api"), ["$15.84 in 7 days", "$21.49 in 30 days", ESTIMATE]);
  assert.equal(found.folders.length, 1);
  // The week's read failed: no 7-day figure and no folders.
  found = factsReport({ ...ALL, week: null }, GIVEN, "atlas-api");
  assert.deepEqual(parts(found, "atlas-api"), ["$8.49 today"]);
  assert.deepEqual(costLine(found, "atlas-api"), ["$8.49 today", "$21.49 in 30 days", ESTIMATE]);
  assert.equal("folders" in found, false);
  // The 30-day read failed: no 30-day figure.
  found = factsReport({ ...ALL, month: null }, GIVEN, "atlas-api");
  assert.deepEqual(costLine(found, "atlas-api"), ["$8.49 today", "$15.84 in 7 days", ESTIMATE]);
  assert.deepEqual(found.projects["atlas-api"].stays, [staysLine("atlas-api")]);
  // All three failed: null.
  assert.equal(factsReport({ today: null, week: null, month: null }, GIVEN, "atlas-api"), null);
});

test("the given projects are read from the page's JSON; anything else gives none", () => {
  assert.deepEqual(parseGiven(JSON.stringify([{ key: "a", root: "/r", lead: null, associated: [] }, { root: "/x" }, 3])),
                   [{ key: "a" }]);
  assert.deepEqual(parseGiven(null), []);
  assert.deepEqual(parseGiven("{"), []);
  assert.deepEqual(parseGiven("{}"), []);
});

test("a long key keeps the Stays line inside the contract's cap", () => {
  const line = staysLine("k".repeat(300));
  assert.equal(Array.from(line).length, TEXT_MAX_CHARS);
  assert.ok(line.endsWith("…"));
  assert.equal(clip("short"), "short");
});
