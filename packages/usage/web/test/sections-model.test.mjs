import assert from "node:assert/strict";
import test from "node:test";

import {
  CLOSED, modelsSection, NO_FOLDER, NO_PROJECT, placesSection, pricedBy,
} from "../src/sections-model.ts";

const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 100, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const place = (project, cwd, cost, extra = {}) => ({ ...sums({ cost }), project, cwd, models: ["m-a"], ...extra });
const model = (id, cost, extra = {}) => ({ ...sums({ cost }), model: id, provenance: [], ...extra });
const answer = (rows, total, extra = {}) => ({ since: null, until: null, tz: "UTC", filters: [], rows,
  total: sums({ cost: total }), unpriced_models: [], rules: [], index: { passed: false, last_pass: 1, note: null },
  today: "2026-11-08", ...extra });
const HOME = "/home/alice";

const PROJECTS = answer([
  place("proj-b", null, 18.06), place(null, "/home/alice/code/finch", 0.5), place("proj-a", null, 21.49),
  place("proj-c", null, 8.63), place(null, "/srv/loose", 0.32), place("proj-d", null, 7.14),
], 56.14, { by: "project", home: HOME });

test("projects go by cost and the closed row names the first three", () => {
  const section = placesSection(PROJECTS, false);
  assert.equal(section.key, "projects");
  assert.equal(section.label, "Projects");
  assert.equal(section.closed, "proj-a $21.49, proj-b $18.06, proj-c $8.63");
  assert.equal(CLOSED.projects, 3);
  assert.deepEqual(section.rows.map((row) => row.name), ["proj-a", "proj-b", "proj-c", "proj-d", NO_PROJECT]);
  assert.equal(section.count, "5 projects");
});

test("an opened project row has its share, rounded down, and its cost, and sets the project filter", () => {
  const [first] = placesSection(PROJECTS, false).rows;
  assert.deepEqual(first, { key: "project:proj-a", name: "proj-a", share: "38%", cost: "$21.49", why: null,
                            patch: { project: "proj-a", folder: "" } });
});

test("files with no project are one row, their sum, that opens nothing", () => {
  const row = placesSection(PROJECTS, false).rows.at(-1);
  assert.deepEqual(row, { key: "no-project", name: "no project", share: "1%", cost: "$0.82", why: null, patch: null });
});

test("the sentence names the folders with no project, whole, each filtering to itself", () => {
  const { sentence } = placesSection(PROJECTS, false);
  assert.equal(sentence.map((part) => part.text).join(""),
               "\"no project\" is ~/code/finch and /srv/loose. A project row opens the Sessions section above, filtered to it.");
  assert.deepEqual(sentence.filter((part) => part.patch !== null).map((part) => part.patch),
                   [{ folder: "/home/alice/code/finch", project: "" }, { folder: "/srv/loose", project: "" }]);
});

test("three folders with no project are listed with commas, and files with no folder are said", () => {
  const report = answer([place("proj-a", null, 3), place(null, "/w/a", 1), place(null, "/w/b", 1),
                         place(null, null, 0.5)], 5.5, { home: null });
  const words = placesSection(report, false).sentence.map((part) => part.text).join("");
  assert.equal(words, "\"no project\" is /w/a, /w/b and files with no folder. A project row opens the Sessions section above, filtered to it.");
});

test("with every file in a project the sentence only says what a row does", () => {
  const report = answer([place("proj-a", null, 3)], 3, { home: HOME });
  const section = placesSection(report, false);
  assert.deepEqual(section.sentence, [{ text: "A project row opens the Sessions section above, filtered to it.", patch: null }]);
  assert.equal(section.count, "1 project");
});

test("folders are written whole, the first two closed, with no row for files with no project", () => {
  const report = answer([place(null, "/home/alice/code/atlas", 21.49), place(null, "/home/alice/code/kestrel", 18.06),
                         place(null, "/srv/tool", 2), place(null, null, 1)], 42.55, { by: "folder", home: HOME });
  const section = placesSection(report, true);
  assert.equal(section.key, "folders");
  assert.equal(section.label, "Folders");
  assert.equal(section.closed, "~/code/atlas $21.49, ~/code/kestrel $18.06");
  assert.equal(section.count, "4 folders");
  assert.deepEqual(section.rows.map((row) => row.name), ["~/code/atlas", "~/code/kestrel", "/srv/tool", NO_FOLDER]);
  assert.ok(!section.rows.some((row) => row.name === NO_PROJECT));
  assert.deepEqual(section.rows[0].patch, { folder: "/home/alice/code/atlas", project: "" });
  assert.equal(section.rows[3].patch, null);
  assert.deepEqual(section.sentence, [{ text: "A folder row opens the Sessions section above, filtered to it.", patch: null }]);
});

test("a cost that is not whole reads at least and says why; an unpriced one is last and has no share", () => {
  const report = answer([place("proj-x", null, null, { unpriced_tokens: 500, unpriced_models: ["m-9"], complete: false }),
                         place("proj-a", null, 2, { unpriced_tokens: 2500, unpriced_models: ["m-9"], complete: false })],
                        2, { home: HOME });
  const [first, last] = placesSection(report, false).rows;
  assert.equal(first.cost, "at least $2.00");
  assert.equal(first.why, "2.50k tokens have no price (m-9)");
  assert.equal(last.name, "proj-x");
  assert.equal(last.cost, "no price");
  assert.equal(last.share, null);
  assert.equal(last.why, "nothing here could be priced");
});

test("a share above zero and under one percent reads under 1%", () => {
  const report = answer([model("m-a", 63.47), model("m-b", 0.25)], 63.72);
  assert.deepEqual(modelsSection(report).rows.map((row) => row.share), ["99%", "under 1%"]);
});

test("ties go by tokens, then by name", () => {
  const report = answer([model("m-c", 1, { tokens: 10 }), model("m-b", 1, { tokens: 50 }), model("m-a", 1, { tokens: 10 })], 3);
  assert.deepEqual(modelsSection(report).rows.map((row) => row.name), ["m-b", "m-a", "m-c"]);
});

test("models go by cost, the first two closed, as plain rows", () => {
  const report = answer([model("m-b", 11.08), model("m-a", 13.41), model(null, 0.5), model("m-c", 2)], 27);
  const section = modelsSection(report);
  assert.equal(section.closed, "m-a $13.41, m-b $11.08");
  assert.equal(section.count, "4 models");
  assert.deepEqual(section.rows.map((row) => row.name), ["m-a", "m-b", "m-c", "unknown model"]);
  assert.ok(section.rows.every((row) => row.patch === null));
  assert.equal(section.sentence, null);
});

test("no section before the read answers, and none with no row", () => {
  assert.equal(placesSection(null, false), null);
  assert.equal(placesSection(answer([], 0), true), null);
  assert.equal(modelsSection(null), null);
  assert.equal(modelsSection(answer([], 0)), null);
});

test("the projects table keeps each row with its folder, models, requests, tokens and cost", () => {
  const report = answer([place("proj-a", null, 3, { models: ["m-a", "m-b"], requests: 1204, tokens: 1_250_000, harness_cost: 2.5 }),
                         place(null, "/home/alice/w/loose", 1, { unknown_rows: 2, complete: false })], 4, { home: HOME });
  const { table } = placesSection(report, false);
  assert.deepEqual(table.columns.map((column) => column.label),
                   ["Project", "Folder", "Models", "Requests", "Tokens", "Cost", "Not whole"]);
  assert.deepEqual(table.rows[0], { key: "project:proj-a",
    cells: ["proj-a", "", "m-a, m-b", "1,204", "1.25M", "$3.00 · harness $2.50", ""] });
  assert.deepEqual(table.rows[1].cells, ["no project", "~/w/loose", "m-a", "1", "100", "at least $1.00",
                                         "2 requests with a token count the harness didn't report"]);
});

test("a table with every row whole has no column for why", () => {
  const report = answer([place(null, "/w/a", 1)], 1, { by: "folder", home: null });
  const { table } = placesSection(report, true);
  assert.deepEqual(table.columns.map((column) => column.label), ["Folder", "Models", "Requests", "Tokens", "Cost"]);
  assert.deepEqual(table.rows[0].cells, ["/w/a", "m-a", "1", "100", "$1.00"]);
});

test("the models table says where each model's prices came from", () => {
  const provenance = { layer: "base", step: 1, rule: "exact", row: "m-a", snapshot: null, overridden: [],
                       period: null, note: null, filled: [], variant: null };
  const report = answer([model("m-a", 2, { provenance: [provenance], rules: ["long-context"] }),
                         model("m-9", null, { unpriced_tokens: 100, unpriced_models: ["m-9"], complete: false })], 2);
  const { table } = modelsSection(report);
  assert.deepEqual(table.columns.map((column) => column.label),
                   ["Model", "Priced by", "Requests", "Tokens", "Cost", "Not whole"]);
  assert.deepEqual(table.rows[0].cells, ["m-a", "exact · layer base · row m-a; long-context", "1", "100", "$2.00", ""]);
  assert.deepEqual(table.rows[1].cells, ["m-9", "no price", "1", "100", "no price", "nothing here could be priced"]);
  assert.equal(pricedBy(model("m-b", 1)), "no price");
});
