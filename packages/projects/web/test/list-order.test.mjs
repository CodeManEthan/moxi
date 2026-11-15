import assert from "node:assert/strict";
import test from "node:test";

import { readReport } from "../src/facts.ts";
import {
  byKey, EARLIER_DAYS, HOLD_MS, keepOpenPlace, listView, LONG_PAST, ordered, orderHeld, projectRow,
  projectsWord,
} from "../src/list-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;
const NOW = at("2026-11-15T19:30:00Z");
const HOME = "/home/me";
const DAY_S = 86_400;

const listed = (key, over = {}) => ({
  key, root: `${HOME}/code/${key}`, kind: "project", lead: null, associated: [], agents: [],
  notes_root: null, created: "2026-01-02T03:04:05Z", exists: true, ...over,
});
/** The seven projects of the busy list. */
const SEVEN = ["atlas-api", "harbor-infra", "kestrel-web", "lumen-docs", "orbit-cli", "quill-mobile",
               "reviewer-home"].map((key) => listed(key, key === "quill-mobile" ? { exists: false } : {}));
const givenOf = (projects) => projects.map(({ key, root, lead, associated }) => ({ key, root, lead, associated }));

const reportOf = (projects, order, entries, extra = {}) => {
  const read = readReport({ version: 1, order, projects: entries, ...extra }, givenOf(projects), null);
  assert.deepEqual(read.refused, []);
  return read.report;
};
const ASK = { find: "", kind: null, held: false, previous: [] };
const view = (projects, reports = [], ask = {}, over = {}) =>
  listView(projects, { home: HOME, reports, open: null, now: NOW, clock: UTC, ...over }, { ...ASK, ...ask });
const keys = (rows) => rows.map((row) => row.key);

/** The busy list's facts: launcher's what-runs-now, sessions' all activity, services' and repo's bad news. */
function busy(projects = SEVEN) {
  const launcher = reportOf(projects, 10, {
    "atlas-api": { at: at("2026-11-15T19:09:00Z") }, "kestrel-web": { at: at("2026-11-15T19:09:00Z") },
    "harbor-infra": { at: at("2026-11-15T18:38:00Z") }, "reviewer-home": { at: at("2026-11-15T17:35:00Z") },
  }, { activity: "window" });
  const sessions = reportOf(projects, 20, {
    "lumen-docs": { at: at("2026-11-15T19:01:00Z") }, "orbit-cli": { at: at("2026-11-15T19:07:00Z") },
  }, { activity: "all" });
  const services = reportOf(projects, 50, {
    "atlas-api": { parts: [{ text: "queue-worker failed at 18:31", heavy: true, rank: 30 }] },
    "lumen-docs": { parts: [{ text: "docs-preview is stopped", warn: true }] },
  });
  const repo = reportOf(projects, 60, { "lumen-docs": { parts: [{ text: "a git hook broke at 17:55", heavy: true, rank: 30 }] } });
  return { launcher, sessions, services, repo, all: [launcher, sessions, services, repo] };
}

test("with an activity source: bad news first by rank, then newest activity, then by key", () => {
  const { all } = busy();
  assert.deepEqual(keys(view(SEVEN, all).rows), [
    "atlas-api", "lumen-docs", "quill-mobile", "kestrel-web", "orbit-cli", "harbor-infra", "reviewer-home",
  ]);
});

test("a lower rank rises above a higher one", () => {
  const blocked = reportOf(SEVEN, 70, { "orbit-cli": { parts: [{ text: "a reply waits", rank: 10 }] } });
  const got = keys(view(SEVEN, [...busy().all, blocked]).rows);
  assert.deepEqual(got.slice(0, 4), ["orbit-cli", "atlas-api", "lumen-docs", "quill-mobile"]);
});

test("with no activity source the list is by key, whatever ranks", () => {
  const { services, repo } = busy();
  const got = view(SEVEN, [services, repo]);
  assert.deepEqual(keys(got.rows), SEVEN.map((project) => project.key));
  assert.equal(got.rows.find((row) => row.key === "quill-mobile").lead, "Its folder doesn't exist",
               "a missing folder keeps its words without moving");
  assert.equal(got.rows.find((row) => row.key === "atlas-api").lead, "queue-worker failed at 18:31");
  assert.deepEqual(keys(view(SEVEN).rows), SEVEN.map((project) => project.key), "the small install");
});

test("a missing folder and a ranked part each rise only with an activity source", () => {
  const services = reportOf(SEVEN, 50, { "reviewer-home": { parts: [{ text: "x failed", heavy: true, rank: 30 }] } });
  assert.deepEqual(keys(view(SEVEN, [services]).rows).slice(-2), ["quill-mobile", "reviewer-home"]);
  const window = reportOf(SEVEN, 10, { "kestrel-web": { at: at("2026-11-15T19:00:00Z") } }, { activity: "window" });
  assert.deepEqual(keys(view(SEVEN, [services, window]).rows).slice(0, 3), ["quill-mobile", "reviewer-home", "kestrel-web"]);
});

test("a report with activity but no at keeps the list by key, nothing risen", () => {
  const { services, repo } = busy();
  const idle = reportOf(SEVEN, 10, { "atlas-api": { parts: [{ text: "nothing running" }] } }, { activity: "window" });
  assert.deepEqual(keys(view(SEVEN, [idle, services, repo]).rows), SEVEN.map((project) => project.key));
  const empty = reportOf(SEVEN, 10, {}, { activity: "all" });
  assert.deepEqual(keys(view(SEVEN, [empty, services, repo]).rows), SEVEN.map((project) => project.key));
});

test("rows with no at come last, by key", () => {
  const window = reportOf(SEVEN, 10, { "orbit-cli": { at: 5 } }, { activity: "window" });
  const got = keys(view(SEVEN.filter((project) => project.exists), [window]).rows);
  assert.deepEqual(got, ["orbit-cli", "atlas-api", "harbor-infra", "kestrel-web", "lumen-docs", "reviewer-home"]);
});

test("keys sort without case, then as they are", () => {
  assert.deepEqual(["beta", "Alpha", "alpha", "Gamma"].sort(byKey), ["Alpha", "alpha", "beta", "Gamma"]);
});

test("the first order is held by key until every component reported or 3 s", () => {
  assert.equal(HOLD_MS, 3000);
  assert.equal(orderHeld(2, 0, 0), true);
  assert.equal(orderHeld(2, 1, 2999), true);
  assert.equal(orderHeld(2, 2, 10), false);
  assert.equal(orderHeld(2, 1, 3000), false);
  assert.equal(orderHeld(0, 0, 0), false, "nothing placed holds nothing");
  const { all } = busy();
  assert.deepEqual(keys(view(SEVEN, all, { held: true }).rows), SEVEN.map((project) => project.key));
});

test("the open row keeps its place; the others are ordered around it", () => {
  const { all, launcher, sessions, services, repo } = busy();
  const first = view(SEVEN, all);
  assert.deepEqual(first.order.slice(0, 4), ["atlas-api", "lumen-docs", "quill-mobile", "kestrel-web"]);
  // harbor-infra is opened, then a report makes it the newest.
  const newer = reportOf(SEVEN, 10, { ...Object.fromEntries([...launcher.projects].map(([key, facts]) => [key, { at: facts.at }])),
                                      "harbor-infra": { at: at("2026-11-15T19:20:00Z") } }, { activity: "window" });
  const reports = [newer, sessions, services, repo];
  const opened = view(SEVEN, reports, { previous: first.order }, { open: "harbor-infra" });
  assert.deepEqual(opened.order, first.order, "it stays at its place while open");
  const closed = view(SEVEN, reports, { previous: opened.order });
  assert.deepEqual(closed.order.slice(0, 4), ["atlas-api", "lumen-docs", "quill-mobile", "harbor-infra"],
                   "closed, it takes its own place");
});

test("keeping the open row's place moves only that row", () => {
  const rows = ["a", "b", "c", "d"].map((key) => ({ key }));
  assert.deepEqual(keys(keepOpenPlace(rows, "d", ["d", "a", "b", "c"])), ["d", "a", "b", "c"]);
  assert.deepEqual(keys(keepOpenPlace(rows, "a", ["b", "c", "d", "a"])), ["b", "c", "d", "a"]);
  assert.deepEqual(keys(keepOpenPlace(rows, "a", ["x", "y", "z", "w", "v", "a"])), ["b", "c", "d", "a"]);
  assert.deepEqual(keys(keepOpenPlace(rows, "c", [])), ["a", "b", "c", "d"], "not drawn before");
  assert.deepEqual(keys(keepOpenPlace(rows, null, ["d", "c", "b", "a"])), ["a", "b", "c", "d"]);
});

test("ordered takes its rows as they are", () => {
  const facts = { home: HOME, reports: [], open: null, now: NOW, clock: UTC };
  const rows = SEVEN.map((project) => projectRow(project, facts));
  assert.deepEqual(keys(ordered(rows, { active: false, held: false })), SEVEN.map((project) => project.key));
});

// -- forty projects ---------------------------------------------------------------------------

const FORTY = Array.from({ length: 40 }, (_, index) =>
  listed(`p${String(index).padStart(2, "0")}`, index % 10 === 3 ? { kind: "agent", root: `${HOME}/agents/a${index}` } : {}));
/** p00 to p09 used in the last day, p10 to p19 forty to fifty days ago, the rest never. */
function forty() {
  const entries = {};
  for (let index = 0; index < 20; index += 1) {
    const ago = index < 10 ? index * 3600 : (40 + index - 10) * DAY_S;
    entries[`p${String(index).padStart(2, "0")}`] = { at: NOW - ago };
  }
  return reportOf(FORTY, 20, entries, { activity: "all", gathers: "sessions" });
}

test("past twelve: the find field, the kind filter and the fold", () => {
  assert.equal(LONG_PAST, 12);
  assert.equal(EARLIER_DAYS, 30);
  const got = view(FORTY, [forty()]);
  assert.equal(got.long, true);
  assert.deepEqual(got.kinds, ["agent", "project"]);
  assert.equal(got.count, "40 projects");
  assert.equal(got.shownOf, null);
  assert.deepEqual(keys(got.rows), ["p00", "p01", "p02", "p03", "p04", "p05", "p06", "p07", "p08", "p09"]);
  assert.equal(got.earlier.length, 30);
  assert.deepEqual(keys(got.earlier).slice(0, 3), ["p10", "p11", "p12"], "the newest first");
  assert.deepEqual(keys(got.earlier).slice(10, 13), ["p20", "p21", "p22"], "then the never-used by key");
  assert.equal(got.earlierLine, "30 projects not used since Oct 6");
  assert.equal(got.order.length, 40);
});

test("the fold at thirteen projects, and none at twelve", () => {
  const thirteen = FORTY.slice(0, 13);
  const report = reportOf(thirteen, 20, { p00: { at: NOW - 40 * DAY_S } }, { activity: "all" });
  const got = view(thirteen, [report]);
  assert.equal(got.rows.length, 0);
  assert.equal(got.earlierLine, "13 projects not used since Oct 6");
  const twelve = FORTY.slice(0, 12);
  const small = view(twelve, [reportOf(twelve, 20, { p00: { at: NOW - 40 * DAY_S } }, { activity: "all" })]);
  assert.equal(small.long, false);
  assert.deepEqual(small.kinds, []);
  assert.equal(small.earlier.length, 0);
  assert.equal(small.earlierLine, null);
  assert.equal(small.rows.length, 12);
});

test("a fold of only never-used projects has no day, and one project is singular", () => {
  const thirteen = FORTY.slice(0, 13);
  const recent = Object.fromEntries(thirteen.slice(0, 12).map(({ key }) => [key, { at: NOW - 60 }]));
  const got = view(thirteen, [reportOf(thirteen, 20, recent, { activity: "all" })]);
  assert.deepEqual(keys(got.earlier), ["p12"]);
  assert.equal(got.earlierLine, "1 project");
  assert.equal(projectsWord(1), "1 project");
  assert.equal(projectsWord(0), "0 projects");
});

test("the fold needs a source of all activity, and waits for the order", () => {
  const window = reportOf(FORTY, 10, { p00: { at: NOW } }, { activity: "window" });
  const got = view(FORTY, [window]);
  assert.equal(got.earlier.length, 0, "launcher alone never folds");
  assert.equal(got.rows[0].key, "p00");
  assert.equal(view(FORTY, [forty()], { held: true }).earlier.length, 0);
  assert.equal(view(FORTY, []).earlier.length, 0, "neither");
  assert.equal(view(FORTY, [forty(), window]).earlier.length, 30, "both");
});

test("a risen row and the open row never fold", () => {
  const missing = FORTY.map((project) => (project.key === "p30" ? { ...project, exists: false } : project));
  const got = view(missing, [forty()], {}, { open: "p25" });
  assert.deepEqual(keys(got.rows).slice(0, 1), ["p30"]);
  assert.ok(keys(got.rows).includes("p25"));
  assert.equal(got.earlier.length, 28);
});

test("find matches the key and the ~ root without case, and nothing folds while it is set", () => {
  const got = view(FORTY, [forty()], { find: " AGENTS/a1" });
  assert.deepEqual(keys(got.rows), ["p13"]);
  assert.equal(got.earlier.length, 0);
  assert.equal(got.count, "1 project");
  assert.equal(got.shownOf, "1 of 40 projects");
  assert.deepEqual(keys(view(FORTY, [], { find: "P0" }).rows).length, 10);
  assert.equal(view(FORTY, [], { find: "/home/me" }).rows.length, 0, "the root is matched in ~ form");
  assert.equal(view(FORTY, [], { find: "~/code/p01" }).rows.length, 1);
});

test("the kind filter shows that kind, and counts", () => {
  const got = view(FORTY, [], { kind: "agent" });
  assert.deepEqual(keys(got.rows), ["p03", "p13", "p23", "p33"]);
  assert.equal(got.shownOf, "4 of 40 projects");
  assert.equal(got.count, "4 projects");
  const both = view(FORTY, [], { kind: "agent", find: "p1" });
  assert.deepEqual(keys(both.rows), ["p13"]);
});

test("one kind has no filter; twelve or fewer neither field nor filter", () => {
  const one = FORTY.map((project) => ({ ...project, kind: "project" }));
  const got = view(one, [], { kind: "agent" });
  assert.deepEqual(got.kinds, []);
  assert.equal(got.rows.length, 40, "a filter for a kind the list doesn't offer is none");
  const few = view(SEVEN, [], { find: "atlas", kind: "agent" });
  assert.equal(few.rows.length, 7);
  assert.equal(few.shownOf, null);
  assert.equal(few.count, "7 projects");
});
