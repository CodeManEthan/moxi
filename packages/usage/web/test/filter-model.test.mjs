import assert from "node:assert/strict";
import test from "node:test";

import {
  allTimePath, chartPath, customRange, figuresPath, filtered, filterLine, heldState, holdState,
  modelsPath, placesPath, START, stateKeys, tilde, watchState,
} from "../src/filter-model.ts";

const TODAY = "2026-11-08";
const state = (extra = {}) => ({ ...START, ...extra });

const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const report = (rows, extra = {}) => ({ since: null, until: null, tz: "UTC", filters: [], rows,
  total: sums({ cost: rows.reduce((total, row) => total + (row.cost ?? 0), 0) }), unpriced_models: [], rules: [],
  index: { passed: false, last_pass: null, note: null }, today: TODAY, ...extra });
const day = (period, cost) => ({ ...sums({ cost }), period, model: "m-a" });
const lane = (name, files) => ({ lane: name, harness: name, state: "online", reason: null, root: null,
  files, held: 0, held_reasons: [], requests: files });
const facts = (extra = {}) => ({ state: state(), today: TODAY, figures: null, allTime: null, places: null,
  folders: false, models: null, lanes: null, ...extra });
const labels = (menu) => menu.items.map((item) => item.label);

test("the page opens on the last 30 days, by cost, with no filter", () => {
  assert.deepEqual(START, { range: { kind: "30d" }, project: "", folder: "", harness: "", model: "", measure: "cost" });
  assert.equal(filtered(START), false);
  assert.equal(filtered(state({ model: "m-a" })), true);
});

test("the held state is changed in one place and every watcher is told", () => {
  const seen = [];
  const stop = watchState((now) => seen.push(now.range.kind));
  holdState({ range: { kind: "7d" } });
  assert.equal(heldState().range.kind, "7d");
  holdState({ project: "proj-a" });
  holdState({ folder: "/work/proj-b" });
  assert.deepEqual([heldState().project, heldState().folder], ["", "/work/proj-b"]);
  holdState({ project: "proj-a" });
  assert.deepEqual([heldState().project, heldState().folder], ["proj-a", ""]);
  stop();
  holdState({ ...START });
  assert.deepEqual(seen, ["7d", "7d", "7d", "7d"]);
  assert.deepEqual(heldState(), START);
});

test("a report is asked with the range's days and each filter that is set", () => {
  assert.deepEqual(stateKeys(state(), TODAY), [["since", "2026-10-10"], ["until", TODAY]]);
  const all = state({ range: { kind: "all" }, harness: "codex", project: "proj-a", model: "m-a" });
  assert.deepEqual(stateKeys(all, TODAY), [["harness", "codex"], ["project", "proj-a"], ["model", "m-a"]]);
  assert.deepEqual(stateKeys(state({ folder: "/work/proj-b" }), TODAY, ["since", "until"]), [["folder", "/work/proj-b"]]);
  const custom = state({ range: { kind: "custom", since: "2026-10-01", until: "2026-10-03" } });
  assert.deepEqual(stateKeys(custom, TODAY), [["since", "2026-10-01"], ["until", "2026-10-03"]]);
});

test("the chart is asked by day, and by month for all time and past 92 days", () => {
  assert.equal(chartPath(state(), TODAY), "api/daily?by=day&since=2026-10-10&until=2026-11-08");
  assert.equal(chartPath(state({ range: { kind: "today" }, harness: "claude" }), TODAY),
               "api/daily?by=day&since=2026-11-08&until=2026-11-08&harness=claude");
  assert.equal(chartPath(state({ range: { kind: "all" } }), TODAY), "api/daily?by=month");
  assert.equal(chartPath(state({ range: { kind: "custom", since: "2026-08-08", until: TODAY } }), TODAY),
               "api/daily?by=month&since=2026-08-08&until=2026-11-08");
  assert.equal(chartPath(state({ range: { kind: "custom", since: "2026-08-09", until: TODAY } }), TODAY),
               "api/daily?by=day&since=2026-08-09&until=2026-11-08");
});

test("the menu's figures have a read of their own only while a filter is set", () => {
  assert.equal(figuresPath(state({ range: { kind: "7d" } }), TODAY), null);
  assert.equal(figuresPath(state({ range: { kind: "7d" }, project: "proj-a" }), TODAY),
               "api/daily?since=2026-08-11&until=2026-11-08&project=proj-a");
  assert.equal(allTimePath(state()), "api/daily?by=month");
  assert.equal(allTimePath(state({ range: { kind: "7d" }, model: "m-a" })), "api/daily?by=month&model=m-a");
});

test("a filter's list is read under the range and the other filters, never its own", () => {
  const set = state({ harness: "codex", project: "proj-a", model: "m-a" });
  assert.equal(placesPath(set, TODAY, false), "api/projects?since=2026-10-10&until=2026-11-08&harness=codex&model=m-a");
  assert.equal(placesPath(state({ folder: "/work/proj-b" }), TODAY, true),
               "api/projects?by=folder&since=2026-10-10&until=2026-11-08");
  assert.equal(modelsPath(set, TODAY), "api/models?since=2026-10-10&until=2026-11-08&harness=codex&project=proj-a");
});

test("the Range menu: four presets with their figures, the two days, then all time", () => {
  const figures = report([day(TODAY, 19.54), day("2026-11-05", 3), day("2026-10-20", 5), day("2026-09-01", 7)]);
  const line = filterLine(facts({ figures }));
  assert.deepEqual([line.range.label, line.range.value, line.range.set], ["Range", "last 30 days", false]);
  assert.deepEqual(line.range.items.map((item) => [item.label, item.figure, item.picked, item.ruled]), [
    ["Today", "$19.54", false, false], ["Last 7 days", "$22.54", false, false],
    ["Last 30 days", "$27.54", true, false], ["Last 90 days", "$34.54", false, false],
    ["From … to …", null, false, true], ["All time", null, false, false]]);
  assert.deepEqual(line.range.items.map((item) => item.patch), [
    { range: { kind: "today" } }, { range: { kind: "7d" } }, { range: { kind: "30d" } }, { range: { kind: "90d" } },
    null, { range: { kind: "all" } }]);
  const month = { ...day("2026-01", 40) };
  const all = filterLine(facts({ state: state({ range: { kind: "all" } }), figures, allTime: report([month]) }));
  assert.deepEqual([all.range.value, all.range.set], ["all time", true]);
  assert.deepEqual(all.range.items.at(-1), { key: "all", label: "All time", figure: "$40.00", picked: true, ruled: false,
                                             patch: { range: { kind: "all" } } });
  // Until its report answers a figure is absent.
  assert.deepEqual(filterLine(facts()).range.items.map((item) => item.figure), [null, null, null, null, null, null]);
});

test("the two days open on the range in force and make a range only in order", () => {
  assert.deepEqual(filterLine(facts()).custom, { from: "From", to: "to", act: "Set", since: "2026-10-10", until: TODAY });
  const all = filterLine(facts({ state: state({ range: { kind: "all" } }) }));
  assert.deepEqual([all.custom.since, all.custom.until], ["2026-10-10", TODAY]);
  assert.deepEqual(customRange("2026-10-01", "2026-10-03"), { kind: "custom", since: "2026-10-01", until: "2026-10-03" });
  assert.deepEqual(customRange("2026-10-03", "2026-10-03"), { kind: "custom", since: "2026-10-03", until: "2026-10-03" });
  assert.equal(customRange("2026-10-04", "2026-10-03"), null);
  assert.equal(customRange("", "2026-10-03"), null);
  const custom = filterLine(facts({ state: state({ range: { kind: "custom", since: "2026-10-01", until: "2026-10-03" } }) }));
  assert.deepEqual([custom.range.value, custom.range.set], ["Oct 1 to Oct 3", true]);
  assert.equal(custom.range.items[4].picked, true);
});

test("Project lists the range's projects after all, and is absent until they are read", () => {
  assert.equal(filterLine(facts()).place, null);
  const places = report([{ ...sums(), project: "proj-a", cwd: "/work/proj-a", models: [] },
                         { ...sums(), project: null, cwd: "/work/loose", models: [] },
                         { ...sums(), project: "proj-b", cwd: "/work/proj-b", models: [] }], { home: "/work" });
  const line = filterLine(facts({ places }));
  assert.deepEqual([line.place.label, line.place.value, line.place.set], ["Project", "all", false]);
  assert.deepEqual(labels(line.place), ["all", "proj-a", "proj-b"]);
  assert.deepEqual(line.place.items[1].patch, { project: "proj-a", folder: "" });
  assert.deepEqual(line.place.items[0], { key: "", label: "all", figure: null, picked: true, ruled: false,
                                          patch: { project: "", folder: "" } });
  // One that is set leads the list when the range no longer holds it, and shows before the list answers.
  const set = filterLine(facts({ state: state({ project: "proj-c" }), places }));
  assert.deepEqual([set.place.value, set.place.set, labels(set.place)], ["proj-c", true, ["all", "proj-c", "proj-a", "proj-b"]]);
  assert.deepEqual(set.place.items.map((item) => item.picked), [false, true, false, false]);
  assert.deepEqual(labels(filterLine(facts({ state: state({ project: "proj-c" }) })).place), ["all", "proj-c"]);
  // A folder set while the filter is by project: the folder is what it says, the projects stay to pick.
  const folder = filterLine(facts({ state: state({ folder: "/work/loose" }), places }));
  assert.deepEqual([folder.place.label, folder.place.value, labels(folder.place)],
                   ["Project", "~/loose", ["all", "~/loose", "proj-a", "proj-b"]]);
  assert.deepEqual(folder.place.items[2].patch, { project: "proj-a", folder: "" });
});

test("without projects the filter is Folder, each folder written whole from the home folder", () => {
  const places = report([{ ...sums(), project: null, cwd: "/home/alice/code/proj-a", models: [] },
                         { ...sums(), project: null, cwd: "/srv/proj-b", models: [] },
                         { ...sums(), project: null, cwd: null, models: [] }], { home: "/home/alice" });
  const line = filterLine(facts({ places, folders: true }));
  assert.deepEqual([line.place.label, line.place.value], ["Folder", "all"]);
  assert.deepEqual(labels(line.place), ["all", "~/code/proj-a", "/srv/proj-b"]);
  assert.deepEqual(line.place.items[1].patch, { folder: "/home/alice/code/proj-a", project: "" });
  const set = filterLine(facts({ state: state({ folder: "/srv/proj-b" }), places, folders: true }));
  assert.deepEqual([set.place.value, set.place.set], ["/srv/proj-b", true]);
  assert.equal(tilde("/home/alice", "/home/alice/"), "~");
  assert.equal(tilde("/home/alicia/x", "/home/alice"), "/home/alicia/x");
  assert.equal(tilde("/work/x", null), "/work/x");
});

test("Harness and Model show only with more than one to choose, or when one is set", () => {
  const models = (...ids) => report(ids.map((model) => ({ ...sums(), model, provenance: [] })));
  const one = filterLine(facts({ lanes: [lane("claude", 3), lane("codex", 0)], models: models("m-a", null) }));
  assert.deepEqual([one.harness, one.model], [null, null]);
  const two = filterLine(facts({ lanes: [lane("codex", 2), lane("claude", 3), lane("opencode", 0)], models: models("m-a", "m-b") }));
  assert.deepEqual([two.harness.label, two.harness.value, two.harness.set, labels(two.harness)],
                   ["Harness", "all", false, ["all", "Claude", "Codex"]]);
  assert.deepEqual(two.harness.items[2].patch, { harness: "codex" });
  assert.deepEqual([two.model.label, two.model.value, labels(two.model)], ["Model", "all", ["all", "m-a", "m-b"]]);
  assert.deepEqual(two.model.items[1].patch, { model: "m-a" });
  const set = filterLine(facts({ state: state({ harness: "claude", model: "m-z" }), lanes: [lane("claude", 3)],
                                 models: models("m-a") }));
  assert.deepEqual([set.harness.value, set.harness.set, labels(set.harness)], ["Claude", true, ["all", "Claude"]]);
  assert.deepEqual([set.model.value, set.model.set, labels(set.model)], ["m-z", true, ["all", "m-z", "m-a"]]);
  // Set before the status or the models have answered.
  const early = filterLine(facts({ state: state({ harness: "codex" }) }));
  assert.deepEqual([labels(early.harness), early.model], [["all", "Codex"], null]);
});

test("Cost / Tokens says which one the chart shows", () => {
  assert.deepEqual(filterLine(facts()).measures, [{ key: "cost", label: "Cost", picked: true },
                                                  { key: "tokens", label: "Tokens", picked: false }]);
  assert.deepEqual(filterLine(facts({ state: state({ measure: "tokens" }) })).measures.map((m) => m.picked), [false, true]);
});
