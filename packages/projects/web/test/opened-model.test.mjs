import assert from "node:assert/strict";
import test from "node:test";

import { readReport } from "../src/facts.ts";
import {
  addBackBody, agentPhrase, agentsLine, emptySentence, folderGroup, GOES, joinNames, MAY_BE_REMOVED, mintJob,
  MISSING_SAYS, moreFolders, NO_PROJECTS, NOTHING_CHANGED, openedRows, pressedOf, refusedRows, refusedTitle,
  removedAt, removedLine, removeStep,
} from "../src/opened-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;
const NOW = at("2026-11-15T19:30:00Z");
const HOME = "/home/me";

const listed = (key, over = {}) => ({
  key, root: `${HOME}/code/${key}`, kind: "project", lead: null, associated: [], agents: [],
  notes_root: null, created: "2026-01-02T03:04:05Z", exists: true, ...over,
});
const ATLAS = listed("atlas-api", { lead: "builder-1", associated: ["planner", "reviewer"],
                                    notes_root: `${HOME}/notes/code/atlas-api` });
const GIVEN = [ATLAS].map(({ key, root, lead, associated }) => ({ key, root, lead, associated }));
const INSTALLED = new Set(["sessions", "usage", "services"]);

const report = (order, facts, extra = {}) => {
  const read = readReport({ version: 1, order, projects: { "atlas-api": facts }, ...extra }, GIVEN, "atlas-api");
  assert.deepEqual(read.refused, []);
  return read.report;
};
const opened = (project, reports = [], over = {}) =>
  openedRows(project, { home: HOME, notesInstalled: true, reports, installed: INSTALLED, ...over });
const texts = (row) => row.lines.map((shown) => shown.parts.map((part) => part.text).join(" · "));
const labels = (rows) => rows.map((row) => row.label);

// -- sections ---------------------------------------------------------------------------------

test("the opened project on the small install: Folder, Agents and Notes", () => {
  const rows = opened(ATLAS);
  assert.deepEqual(labels(rows), ["Folder", "Agents", "Notes"]);
  assert.deepEqual(texts(rows[0]), ["~/code/atlas-api · a code project"]);
  assert.deepEqual(texts(rows[1]), ["builder-1 leads · planner and reviewer also work here"]);
  assert.deepEqual(texts(rows[2]), ["~/notes/code/atlas-api"]);
  assert.ok(rows.every((row) => row.link === null && !row.changeFolder));
});

test("Notes only while notes is installed and the project has a notes folder", () => {
  assert.deepEqual(labels(opened(ATLAS, [], { notesInstalled: false })), ["Folder", "Agents"]);
  assert.deepEqual(labels(opened({ ...ATLAS, notes_root: null })), ["Folder", "Agents"]);
});

test("a missing folder: its heavy words, what Moxi keeps, and Change folder", () => {
  const [folder] = opened({ ...ATLAS, exists: false });
  assert.equal(folder.changeFolder, true);
  assert.deepEqual(folder.lines, [{ parts: [{ text: "~/code/atlas-api doesn't exist", heavy: true }] },
                                  { parts: [{ text: MISSING_SAYS, heavy: false }] }]);
  assert.equal(MISSING_SAYS,
               "Moxi keeps the project's name and settings. Point it at where the folder went, or remove the project.");
});

test("the kind's phrase on the Folder row", () => {
  assert.deepEqual(texts(opened(listed("r", { kind: "agent", root: `${HOME}/agents/reviewer` }))[0]),
                   ["~/agents/reviewer · an agent's home"]);
  assert.deepEqual(texts(opened(listed("g", { kind: "topic", root: "/srv/g" }))[0]), ["/srv/g · topic"]);
});

test("ranked sections lead, then Folder and Agents, then the rest by order", () => {
  const launcher = report(10, { sections: [
    { label: "Launch", lines: [{ parts: [{ text: "preset claude" }] }] },
    { label: "Sessions", lines: [{ parts: [{ text: "planner, slot 3" }] }] },
  ] });
  const usage = report(40, { sections: [{ label: "Cost", lines: [{ parts: [{ text: "$8.49 today" }] }],
                                          link: { text: "Usage", module: "usage", route: "project/atlas-api" } }] });
  const services = report(50, { sections: [{ label: "Services", rank: 30, lines: [{ parts: [
    { text: "queue-worker failed at 18:31", heavy: true }, { text: "exited 1, not running" }] }],
    link: { text: "Services", module: "services", route: "" } }] });
  const repo = report(60, { sections: [{ label: "Git hooks", rank: 20, lines: [{ parts: [{ text: "a git hook broke" }] }] }] });
  const loadout = report(70, { sections: [{ label: "Loadout", lines: [{ parts: [{ text: "skill x" }] }] }] });
  const rows = opened(ATLAS, [loadout, services, usage, repo, launcher]);
  assert.deepEqual(labels(rows), ["Git hooks", "Services", "Folder", "Agents", "Launch", "Sessions", "Cost", "Loadout",
                                  "Notes"]);
  assert.deepEqual(rows[1].lines[0].parts, [{ text: "queue-worker failed at 18:31", heavy: true },
                                            { text: "exited 1, not running", heavy: false }]);
  assert.deepEqual(rows[1].link, { text: "Services", module: "services", route: "" });
  assert.deepEqual(rows[6].link, { text: "Usage", module: "usage", route: "project/atlas-api" });
  assert.equal(rows[0].link, null);
});

test("sections of one label from two reports are one row: lines in order, first link, lowest rank", () => {
  const launcher = report(10, { sections: [{ label: "Sessions", lines: [{ parts: [{ text: "planner, slot 3" }] }],
                                             link: { text: "Launcher", module: "launcher", route: "" } }] });
  const sessions = report(20, { sections: [{ label: "Sessions", rank: 40, lines: [{ parts: [{ text: "7 this week" }] }],
                                             link: { text: "Sessions", module: "sessions", route: "" } }] });
  const other = report(30, { sections: [{ label: "Sessions", rank: 20, lines: [{ parts: [{ text: "z" }] }] }] });
  const rows = opened(ATLAS, [other, sessions, launcher]);
  assert.deepEqual(labels(rows), ["Sessions", "Folder", "Agents", "Notes"]);
  assert.deepEqual(texts(rows[0]), ["planner, slot 3", "7 this week", "z"]);
  assert.deepEqual(rows[0].link, { text: "Sessions", module: "sessions", route: "" },
                   "the first link whose module is installed");
  const withLauncher = opened(ATLAS, [launcher, sessions], { installed: new Set(["launcher", "sessions"]) });
  assert.equal(withLauncher[0].link.module, "launcher");
});

test("a link to a module not installed is not drawn", () => {
  const usage = report(40, { sections: [{ label: "Cost", lines: [{ parts: [{ text: "$1" }] }],
                                          link: { text: "Usage", module: "usage", route: "" } }] });
  assert.equal(opened(ATLAS, [usage], { installed: new Set() })[2].link, null);
});

test("unranked sections of one order keep the order listed", () => {
  const one = report(10, { sections: [{ label: "B", lines: [{ parts: [{ text: "b" }] }] },
                                      { label: "A", lines: [{ parts: [{ text: "a" }] }] }] });
  const two = report(10, { sections: [{ label: "C", lines: [{ parts: [{ text: "c" }] }] }] });
  assert.deepEqual(labels(opened(ATLAS, [one, two])).slice(2, 5), ["B", "A", "C"]);
});

test("counted lines: running lines claim their sessions, so the count after them says more", () => {
  const more = { one: "session this week", many: "sessions this week", after_one: "more this week",
                 after_many: "more this week" };
  const launcher = report(10, { sections: [{ label: "Sessions", lines: [
    { parts: [{ text: "planner-Nov-08-2026 12:45" }, { text: "slot 3" }], claims: ["claude/a"] },
    { parts: [{ text: "builder-1-Nov-08-2026 11:00" }, { text: "slot 4" }], claims: ["claude/b"] },
  ] }] });
  const week = ["claude/a", "claude/b", "claude/c", "claude/d", "codex/e"];
  const sessions = report(20, { sections: [{ label: "Sessions", lines: [{ parts: [{ text: "", count: more, claims: week }] }] }] });
  const rows = opened(ATLAS, [launcher, sessions]);
  assert.deepEqual(texts(rows[2]), ["planner-Nov-08-2026 12:45 · slot 3", "builder-1-Nov-08-2026 11:00 · slot 4",
                                    "3 more this week"]);
  const alone = opened(ATLAS, [sessions]);
  assert.deepEqual(texts(alone[2]), ["5 sessions this week"], "with no line above, its own words");
  const allRunning = report(20, { sections: [{ label: "Sessions",
                                               lines: [{ parts: [{ text: "", count: more, claims: ["claude/a", "claude/b"] }] }] }] });
  assert.deepEqual(texts(opened(ATLAS, [launcher, allRunning])[2]).length, 2, "a count of 0 is not drawn");
  assert.deepEqual(labels(opened(ATLAS, [allRunning, report(10, { parts: [{ text: "", count: more, claims: ["claude/a", "claude/b"] }] })])),
                   ["Folder", "Agents", "Notes"], "a section with nothing left to draw is left out");
});

// -- agents -----------------------------------------------------------------------------------

const says = (order, agents) => report(order, { agents });
const NOT_RUNNING = { runs: false, state: "not running now" };
const IN_SLOT = { runs: true, state: "running now", where: "in slot 4" };

test("a report saying the lead is not running, alone", () => {
  assert.equal(agentPhrase([says(20, { "builder-1": NOT_RUNNING })], "atlas-api", "builder-1"), "not running now");
  assert.deepEqual(texts(opened(ATLAS, [says(20, { "builder-1": NOT_RUNNING })])[1]),
                   ["builder-1 leads · not running now · planner and reviewer also work here"]);
});

test("a report that says nothing of a live name draws no phrase, so a hand-started session never reads running", () => {
  const silent = says(20, { planner: NOT_RUNNING });
  assert.equal(agentPhrase([silent], "atlas-api", "builder-1"), null);
  assert.deepEqual(texts(opened(ATLAS, [silent])[1]), ["builder-1 leads · planner and reviewer also work here"]);
});

test("a report saying the lead runs, with where", () => {
  assert.equal(agentPhrase([says(10, { "builder-1": IN_SLOT })], "atlas-api", "builder-1"), "running now in slot 4");
});

test("the join: two fixture reports agreeing that the lead runs", () => {
  const first = says(10, { "builder-1": IN_SLOT });
  const second = says(30, { "builder-1": { runs: true, state: "up and running", where: "in slot 9" } });
  assert.equal(agentPhrase([second, first], "atlas-api", "builder-1"), "running now in slot 4");
  const noWhere = says(5, { "builder-1": { runs: true, state: "running" } });
  assert.equal(agentPhrase([noWhere, first], "atlas-api", "builder-1"), "running in slot 4",
               "the state of the lowest order, the where of the lowest order that gives one");
});

test("the join: not running now beside running in slot 4 draws nothing", () => {
  const notRunning = says(20, { "builder-1": NOT_RUNNING });
  const running = says(10, { "builder-1": IN_SLOT });
  assert.equal(agentPhrase([notRunning, running], "atlas-api", "builder-1"), null);
  assert.deepEqual(texts(opened(ATLAS, [notRunning, running])[1]),
                   ["builder-1 leads · planner and reviewer also work here"]);
});

test("the join: two reports agreeing the lead is not running, in different words", () => {
  const first = says(20, { "builder-1": NOT_RUNNING });
  const second = says(15, { "builder-1": { runs: false, state: "idle" } });
  assert.equal(agentPhrase([first, second], "atlas-api", "builder-1"), "idle");
});

test("a where on a say whose runs is false is refused, so it draws nothing", () => {
  const read = readReport({ version: 1, order: 20, projects: { "atlas-api": { agents: {
    "builder-1": { runs: false, state: "not running now", where: "in slot 4" } } } } }, GIVEN, "atlas-api");
  assert.equal(read.refused.length, 1);
  assert.equal(agentPhrase([read.report], "atlas-api", "builder-1"), null);
});

test("the phrase is drawn after the lead only", () => {
  const line = agentsLine(ATLAS, [says(20, { planner: NOT_RUNNING, reviewer: NOT_RUNNING })]);
  assert.deepEqual(line.parts.map((part) => part.text), ["builder-1 leads", "planner and reviewer also work here"]);
});

test("the Agents line's three forms", () => {
  const line = (over) => agentsLine({ ...ATLAS, ...over }, []).parts.map((part) => part.text).join(" · ");
  assert.equal(line({ associated: ["planner"] }), "builder-1 leads · planner also works here");
  assert.equal(line({ associated: ["planner", "reviewer"] }), "builder-1 leads · planner and reviewer also work here");
  assert.equal(line({ associated: ["a", "b", "c"] }), "builder-1 leads · a, b and c also work here");
  assert.equal(line({ associated: [] }), "builder-1 leads");
  assert.equal(line({ lead: null, associated: ["planner"] }), "planner also works here");
  assert.equal(line({ lead: null, associated: [] }), "no lead");
  assert.equal(joinNames([]), "");
  assert.equal(joinNames(["a"]), "a");
});

// -- remove -----------------------------------------------------------------------------------

const FACTS = { home: HOME, notesInstalled: true, reports: [], installed: INSTALLED };

test("the remove step with a notes folder and each report's stays", () => {
  const sessions = report(20, { stays: ["its 2 sessions and their transcripts"] });
  const launcher = report(10, { stays: ["the one running now keeps running"] });
  assert.deepEqual(removeStep(ATLAS, { ...FACTS, reports: [sessions, launcher] }), {
    title: "Remove atlas-api from Moxi?",
    goes: "the project's entry in Moxi: its name and its kind. Other pages stop grouping by it.",
    stays: ["the folder ~/code/atlas-api and every file in it", "the notes in ~/notes/code/atlas-api",
            "the one running now keeps running", "its 2 sessions and their transcripts"],
    foot: "Nothing on disk is deleted. Adding ~/code/atlas-api again under the same name puts it back.",
  });
  assert.equal(GOES, "the project's entry in Moxi: its name and its kind. Other pages stop grouping by it.");
});

test("the remove step without notes, and with a missing folder", () => {
  assert.deepEqual(removeStep(ATLAS, { ...FACTS, notesInstalled: false }).stays,
                   ["the folder ~/code/atlas-api and every file in it"]);
  assert.deepEqual(removeStep({ ...ATLAS, notes_root: null }, FACTS).stays,
                   ["the folder ~/code/atlas-api and every file in it"]);
  assert.deepEqual(removeStep({ ...ATLAS, exists: false }, FACTS).stays, ["the notes in ~/notes/code/atlas-api"]);
  assert.deepEqual(removeStep({ ...ATLAS, exists: null }, { ...FACTS, notesInstalled: false }).stays,
                   ["the folder ~/code/atlas-api and every file in it"], "a root it can't stat may still be there");
});

test("the refused step: projects' agents row, a module's sentence, a broken one's fix", () => {
  const titles = { services: "Services", notes: null };
  const rows = refusedRows(ATLAS, [
    { module: "projects", sentence: "project 'atlas-api' still has associated agents: planner reviewer", fix: null },
    { module: "services", sentence: "queue-worker still runs here; stop it first", fix: null },
    { module: "notes", sentence: "its import failed", fix: "repair it, or moxi uninstall notes" },
  ], (module) => titles[module] ?? null);
  assert.deepEqual(rows, [
    { label: "Agents", lines: ["planner and reviewer also work here. Take them off the project first."], page: null, edit: true },
    { label: "Services", lines: ["queue-worker still runs here; stop it first"], page: "services", edit: false },
    { label: "notes", lines: ["its import failed", "repair it, or moxi uninstall notes"], page: null, edit: false },
  ]);
  const one = refusedRows({ ...ATLAS, associated: ["planner"] }, [{ module: "projects", sentence: "s", fix: null }], () => null);
  assert.deepEqual(one[0].lines, ["planner also works here. Take it off the project first."]);
  assert.equal(refusedTitle("atlas-api"), "atlas-api can't be removed yet");
  assert.equal(NOTHING_CHANGED, "Nothing was changed.");
  assert.equal(removedLine("reviewer-home"), "reviewer-home was removed from Moxi. Nothing on disk was deleted.");
});

test("each press mints a job of its own, 32 hexadecimal characters", () => {
  const jobs = new Set(Array.from({ length: 50 }, () => mintJob()));
  assert.equal(jobs.size, 50);
  for (const job of jobs) assert.match(job, /^[0-9a-f]{32}$/);
});

test("a press comes to removed, or held for a reason the check is read again for", () => {
  const kept = { job: "j", project: "atlas-api", created: "2026-01-02T03:04:05Z", key: "atlas-api", reason: null,
                 fix: null, sentences: [], exit_code: null };
  assert.deepEqual(pressedOf({ ...kept, state: "removed" }), { outcome: "removed" });
  assert.deepEqual(pressedOf({ ...kept, state: "refused", reason: "project 'atlas-api' is still held", fix: "free it",
                               sentences: ["fixture: held"], exit_code: 3 }),
                   { outcome: "held", reason: "project 'atlas-api' is still held" });
  assert.equal(MAY_BE_REMOVED, "It may have been removed.");
});

test("Add it back sends the key, folder, kind and lead, and the notes folder while notes is installed", () => {
  assert.deepEqual(addBackBody(ATLAS, { notesInstalled: true }), {
    key: "atlas-api", root: "/home/me/code/atlas-api", kind: "project", lead: "builder-1",
    notes_root: "/home/me/notes/code/atlas-api" });
  assert.deepEqual(addBackBody(ATLAS, { notesInstalled: false }),
                   { key: "atlas-api", root: "/home/me/code/atlas-api", kind: "project", lead: "builder-1" });
  assert.deepEqual(addBackBody(listed("solo", { kind: "agent" }), { notesInstalled: true }),
                   { key: "solo", root: "/home/me/code/solo", kind: "agent" });
});

test("a removed project's line takes its row's place", () => {
  const keys = ["atlas-api", "kestrel-web", "orbit-cli"];
  assert.equal(removedAt(keys, null), 0);
  assert.equal(removedAt(keys, "atlas-api"), 1);
  assert.equal(removedAt(keys, "orbit-cli"), 3);
  // The row before it isn't drawn now (filtered, folded): first.
  assert.equal(removedAt(keys, "lumen-docs"), 0);
});

// -- the empty list ---------------------------------------------------------------------------

test("the empty sentence joins what each report gathers, in order", () => {
  const gathers = (order, word) => readReport({ version: 1, order, gathers: word }, [], null).report;
  assert.equal(NO_PROJECTS, "No projects yet.");
  assert.equal(emptySentence([]), "A project is a folder with a short name.");
  assert.equal(emptySentence([gathers(10, null)]), "A project is a folder with a short name.");
  assert.equal(emptySentence([gathers(20, "sessions")]),
               "A project is a folder with a short name. Its sessions are then gathered under that name.");
  assert.equal(emptySentence([gathers(40, "cost"), gathers(20, "sessions")]),
               "A project is a folder with a short name. Its sessions and cost are then gathered under that name.");
  assert.equal(emptySentence([gathers(60, "git hooks"), gathers(40, "cost"), gathers(50, "services"),
                              gathers(20, "sessions"), gathers(45, "cost")]),
               "A project is a folder with a short name. Its sessions, cost, services and git hooks are then gathered under that name.");
});

// -- folder rows ------------------------------------------------------------------------------

const folders = (order, rows, extra = {}) => readReport({ version: 1, order, folders: rows, ...extra }, [], null).report;
const group = (reports, over = {}) => folderGroup(reports, { home: HOME, now: NOW, hasProjects: true, showAll: false,
                                                             clock: UTC, ...over });

test("a folder row shows only once a report gives it an at", () => {
  const usage = folders(40, [{ folder: `${HOME}/code/finch-scripts`, parts: [{ text: "$0.82" }] }]);
  assert.equal(group([usage]), null, "usage alone never shows a folder");
  const sessions = folders(20, [{ folder: `${HOME}/code/finch-scripts`, at: at("2026-11-13T22:10:00Z"),
                                  parts: [{ text: "2 sessions this week" }] }], { activity: "all" });
  assert.deepEqual(group([usage, sessions]), {
    label: "Not a project",
    rows: [{ folder: `${HOME}/code/finch-scripts`, name: "~/code/finch-scripts", where: "2 sessions this week · $0.82",
             fact: "last active Fri 22:10", at: at("2026-11-13T22:10:00Z") }],
    more: null, canShow: false,
  });
  assert.equal(group([usage, sessions], { hasProjects: false }).label, "Worked in this week");
});

test("an at from a report with no activity is dropped, so its folder doesn't show", () => {
  const read = readReport({ version: 1, order: 40, folders: [{ folder: "/w/x", at: NOW, parts: [] }] }, [], null);
  assert.equal(group([read.report]), null);
});

test("folder rows: newest first, then by folder, the newest at of any report", () => {
  const one = folders(20, [{ folder: "/w/b", at: 100, parts: [] }, { folder: "/w/a", at: 100, parts: [] },
                           { folder: "/w/c", at: 50, parts: [] }], { activity: "all" });
  const two = folders(10, [{ folder: "/w/c", at: 200, parts: [{ text: "1 running" }] }], { activity: "window" });
  const got = group([one, two]);
  assert.deepEqual(got.rows.map((row) => row.folder), ["/w/c", "/w/a", "/w/b"]);
  assert.equal(got.rows[0].at, 200);
  assert.equal(got.rows[2].where, null);
});

test("five rows, then the rest counted with more_folders, and Show", () => {
  const rows = Array.from({ length: 8 }, (_, index) => ({ folder: `/w/f${index}`, at: 1000 - index, parts: [] }));
  const sessions = folders(20, rows, { activity: "all", more_folders: 4 });
  const usage = folders(40, [], { more_folders: 50 });
  const got = group([sessions, usage]);
  assert.equal(got.rows.length, 5);
  assert.equal(got.more, "7 more folders");
  assert.equal(got.canShow, true);
  const all = group([sessions, usage], { showAll: true });
  assert.equal(all.rows.length, 8);
  assert.equal(all.more, "4 more folders");
  assert.equal(all.canShow, false);
  const few = group([folders(20, rows.slice(0, 3), { activity: "all", more_folders: 1 })]);
  assert.deepEqual([few.rows.length, few.more, few.canShow], [3, "1 more folder", false]);
  assert.equal(group([folders(20, rows.slice(0, 5), { activity: "all" })]).more, null);
  assert.equal(moreFolders(2), "2 more folders");
});
