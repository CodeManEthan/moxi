import assert from "node:assert/strict";
import test from "node:test";

import { factsModules, FirstOrder, givenOf, HeldReports, presentModules } from "../src/frame.ts";
import { HOLD_MS } from "../src/list-model.ts";

const listed = (key, over = {}) => ({
  key, root: `/home/me/code/${key}`, kind: "project", lead: null, associated: [], agents: [],
  notes_root: null, created: "2026-01-02T03:04:05Z", exists: true, ...over,
});
const GIVEN = givenOf([listed("atlas-api", { lead: "builder-1", associated: ["planner"] }), listed("orbit-cli")]);

/** A schedule that runs nothing until the test calls `frame()`. */
function frames() {
  const waiting = [];
  return {
    schedule: (draw) => waiting.push(draw),
    frame: () => { for (const draw of waiting.splice(0)) draw(); },
    get asked() { return waiting.length; },
  };
}

const said = (order, projects) => ({ version: 1, order, projects });

// -- which modules ----------------------------------------------------------------------------

test("the modules placed: present, declaring the component, by name, a module listed twice skipped", () => {
  const value = { contracts: [1], modules: [
    { name: "usage", state: "present" }, { name: "comms", state: "present" }, { name: "repo", state: "missing" },
    { name: "sessions", state: "present" }, { name: "sessions", state: "present" }, { name: "ui", state: "present" },
  ] };
  const declares = (module) => module !== "ui";
  assert.deepEqual(factsModules(value, declares), ["comms", "usage"]);
  assert.deepEqual(factsModules(value, () => false), []);
  assert.deepEqual(factsModules(null, declares), []);
  assert.deepEqual(factsModules({ modules: "no" }, declares), []);
  assert.deepEqual(factsModules({ modules: [null, { name: "" }, { name: 3, state: "present" }] }, declares), []);
});

test("the modules present, for the opened project's links", () => {
  const value = { modules: [{ name: "usage", state: "present" }, { name: "repo", state: "missing" }, null,
                            { name: "", state: "present" }, { name: "fixture", state: "present" }] };
  assert.deepEqual([...presentModules(value)].sort(), ["fixture", "usage"]);
  assert.deepEqual([...presentModules(undefined)], []);
  assert.deepEqual([...presentModules({ modules: "no" })], []);
});

test("the projects attribute: key, absolute root, lead and associated agents of each project", () => {
  assert.deepEqual(GIVEN, [
    { key: "atlas-api", root: "/home/me/code/atlas-api", lead: "builder-1", associated: ["planner"] },
    { key: "orbit-cli", root: "/home/me/code/orbit-cli", lead: null, associated: [] },
  ]);
});

// -- reports, drawn once a frame --------------------------------------------------------------

test("reports heard within one frame are drawn once, each module's last replacing its earlier", () => {
  const clock = frames();
  let draws = 0;
  const held = new HeldReports(clock.schedule, () => { draws += 1; }, () => {});
  held.heard("usage", said(300, { "atlas-api": { parts: [{ text: "$1 today" }] } }), GIVEN, null);
  held.heard("sessions", said(100, { "orbit-cli": { at: 10 } }), GIVEN, null);
  held.heard("usage", said(300, { "atlas-api": { parts: [{ text: "$2 today" }] } }), GIVEN, null);
  assert.equal(clock.asked, 1);
  assert.equal(draws, 0);
  assert.deepEqual(held.reports(), []);
  assert.equal(held.reported, 0);
  clock.frame();
  assert.equal(draws, 1);
  assert.equal(held.reported, 2);
  assert.deepEqual(held.reports().map((report) => report.order), [100, 300]);
  assert.equal(held.reports()[1].projects.get("atlas-api").parts[0].text, "$2 today");
  // A tight loop of reports asks for one more frame, and draws once more.
  for (let n = 0; n < 50; n += 1) held.heard("sessions", said(100, { "orbit-cli": { at: n } }), GIVEN, null);
  assert.equal(clock.asked, 1);
  clock.frame();
  assert.equal(draws, 2);
  assert.equal(held.reports()[0].projects.get("orbit-cli").at, 49);
});

test("a null report counts as reported and is drawn as nothing to say", () => {
  const clock = frames();
  const held = new HeldReports(clock.schedule, () => {}, () => {});
  held.heard("usage", null, GIVEN, null);
  clock.frame();
  assert.equal(held.reported, 1);
  assert.ok(held.has("usage"));
  assert.deepEqual(held.reports(), []);
  // A report then null again takes the facts away.
  held.heard("usage", said(300, { "atlas-api": { at: 5 } }), GIVEN, null);
  clock.frame();
  assert.equal(held.reports().length, 1);
  held.heard("usage", null, GIVEN, null);
  clock.frame();
  assert.deepEqual(held.reports(), []);
});

test("a module taken away takes its report, drawn at the next frame", () => {
  const clock = frames();
  let draws = 0;
  const held = new HeldReports(clock.schedule, () => { draws += 1; }, () => {});
  held.heard("usage", said(300, { "atlas-api": { at: 5 } }), GIVEN, null);
  clock.frame();
  held.forget("usage");
  assert.equal(held.reports().length, 0);
  assert.equal(held.reported, 0);
  clock.frame();
  assert.equal(draws, 2);
  // A report heard and taken away within one frame is never drawn.
  held.heard("comms", said(400, {}), GIVEN, null);
  held.forget("comms");
  clock.frame();
  assert.equal(held.has("comms"), false);
  // Forgetting a module never heard asks for nothing.
  held.forget("repo");
  assert.equal(clock.asked, 0);
});

test("each report is read against what its component was given, and its refusals logged once per module", () => {
  const clock = frames();
  const logged = [];
  const held = new HeldReports(clock.schedule, () => {}, (module, refused) => logged.push([module, refused]));
  const bad = said(300, { "atlas-api": { at: 5, sections: [] }, "not-given": { at: 6 } });
  held.heard("usage", bad, GIVEN, null);
  held.heard("usage", bad, GIVEN, null);
  held.heard("sessions", said(100, { "quill-mobile": {} }), GIVEN, null);
  clock.frame();
  assert.deepEqual(logged.map(([module]) => module), ["usage", "sessions"]);
  assert.ok(logged[0][1].length >= 2, logged[0][1]);
  // The rest of the report is kept.
  const [, usage] = held.reports();
  assert.equal(usage.projects.get("atlas-api").at, 5);
  assert.equal(usage.projects.has("not-given"), false);
  // The open project's sections are taken.
  held.heard("usage", said(300, { "atlas-api": { sections: [{ label: "Cost", lines: [{ parts: [{ text: "$2 today" }] }] }] } }), GIVEN, "atlas-api");
  clock.frame();
  assert.equal(held.reports()[1].projects.get("atlas-api").sections[0].label, "Cost");
  assert.equal(logged.length, 2);
});

// -- the first order --------------------------------------------------------------------------

test("the first order is held until every placed component reported, then stays let go", () => {
  const first = new FirstOrder(1000);
  assert.equal(first.held(2, 0, 1000), true);
  assert.equal(first.held(2, 1, 2000), true);
  assert.equal(first.held(2, 2, 2100), false);
  // A component placed later doesn't hold it again.
  assert.equal(first.held(3, 2, 2200), false);
});

test("the first order is let go after the hold's time, and held while the components aren't known", () => {
  const first = new FirstOrder(0);
  assert.equal(first.held(null, 0, HOLD_MS - 1), true);
  assert.equal(first.held(null, 0, HOLD_MS), false);
  assert.equal(new FirstOrder(0).held(1, 0, HOLD_MS + 5), false);
  // With no component to wait for, nothing holds it.
  assert.equal(new FirstOrder(0).held(0, 0, 0), false);
});
