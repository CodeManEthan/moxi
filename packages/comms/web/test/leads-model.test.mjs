import assert from "node:assert/strict";
import test from "node:test";

import { projectsLedBy, leadsWords, PROJECTS_PATH, wantsLeads } from "../src/leads-model.ts";

// One project as projects' `/api/projects` lists it (its declared shape, every field).
const project = (key, lead, fields = {}) => ({
  key, root: `/w/${key}`, kind: "project", lead, associated: [], agents: lead === null ? [] : [lead],
  notes_root: null, created: "2026-11-01T09:00:00Z", exists: true, ...fields,
});
const list = (projects) => ({ projects, home: "/w", notes_installed: false });
const other = (qualified, speaksFor = qualified, fields = {}) =>
  ({ qualified, sentBy: null, writable: true, speaksFor, ...fields });

test("the route is projects' list", () => {
  assert.equal(PROJECTS_PATH, "api/projects");
});

test("the leads: each lead's project keys in the list's order; a project with no lead leads nothing", () => {
  const leads = projectsLedBy(list([project("atlas-api", "builder-1"), project("harbor-infra", "ops-watch"),
                              project("orbit-cli", "builder-1"), project("reviewer-home", null)]));
  assert.deepEqual([...leads], [["builder-1", ["atlas-api", "orbit-cli"]], ["ops-watch", ["harbor-infra"]]]);
});

test("an associated agent leads nothing", () => {
  const leads = projectsLedBy(list([project("atlas-api", "builder-1", { associated: ["planner"],
                                                                  agents: ["builder-1", "planner"] })]));
  assert.equal(leads.has("planner"), false);
});

test("a value that isn't projects' list is no read; a malformed project is skipped", () => {
  for (const data of [null, [], "x", {}, { projects: null }, { projects: {} }]) assert.equal(projectsLedBy(data), null);
  const leads = projectsLedBy(list([null, "x", { key: 3, lead: "a" }, { key: "k", lead: 4 }, { key: "", lead: "a" },
                              { key: "k", lead: "" }, project("harbor-infra", "ops-watch")]));
  assert.deepEqual([...leads], [["ops-watch", ["harbor-infra"]]]);
});

test("an idle name that leads a project says leads and its key", () => {
  const leads = projectsLedBy(list([project("harbor-infra", "ops-watch")]));
  assert.equal(leadsWords(other("ops-watch"), leads), "leads harbor-infra");
  // The scoped first entry of a name with no bare address speaks for the name.
  assert.equal(leadsWords(other("ops-watch@x", "ops-watch"), leads), "leads harbor-infra");
});

test("a name that leads two projects names both; more than three, the first two and how many more", () => {
  const two = projectsLedBy(list([project("atlas-api", "main"), project("orbit-cli", "main")]));
  assert.equal(leadsWords(other("main"), two), "leads atlas-api and orbit-cli");
  const four = projectsLedBy(list(["a", "b", "c", "d"].map((key) => project(key, "main"))));
  assert.equal(leadsWords(other("main"), four), "leads a, b and 2 more");
});

test("nothing for a name that leads nothing, an entry that speaks for no name, and no read", () => {
  const leads = projectsLedBy(list([project("harbor-infra", "ops-watch")]));
  assert.equal(leadsWords(other("herald"), leads), null);
  assert.equal(leadsWords(other("ops-watch@x", null), leads), null);
  assert.equal(leadsWords(other("console", null, { sentBy: "launcher", writable: false }), leads), null);
  assert.equal(leadsWords(other("ops-watch"), null), null);
  assert.equal(leadsWords(other("ops-watch"), projectsLedBy(list([]))), null);
});

test("projects is wanted only while an entry speaks for an idle name", () => {
  assert.equal(wantsLeads([]), false);
  assert.equal(wantsLeads([other("planner@kestrel-web", null), other("console", null)]), false);
  assert.equal(wantsLeads([other("console", null), other("herald")]), true);
});
