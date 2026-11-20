import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTIVITY, factsReport, GATHERS, goodValue, LABEL, ORDER, parseGiven, parseProjects, READ_MS, RECENT_COUNT,
  staysLine, VERSION, WEEK_COUNT, weekWords,
} from "../src/facts.ts";
import { Feed } from "../src/feed.ts";

const NOW = Date.parse("2026-11-16T20:00:00Z") / 1000;
const CLAUDE_A = "claude/0190b000-0000-7000-8000-000000000001";
const CLAUDE_B = "claude/0190b000-0000-7000-8000-000000000002";
const CODEX_A = "codex/0190c000-0000-7000-8000-000000000001";
const OPENCODE_A = "opencode/ses_7f3a";

/** An `/api/projects` answer, with `fields` laid over it. */
const answer = (fields = {}) => ({
  shape: 1, now: NOW, today: "2026-11-16", home: "/home/me", recently_s: 3600, state: "up", detail: null,
  projects: [
    { key: "atlas-api", at: NOW - 600, all: 9, week: [CLAUDE_A, CODEX_A, CLAUDE_B], recent: [CLAUDE_A] },
    { key: "kestrel-web", at: NOW - 3 * 86400, all: 1, week: [OPENCODE_A], recent: [] },
    { key: "old-thing", at: NOW - 40 * 86400, all: 4, week: [], recent: [] },
    { key: "not-given", at: NOW, all: 2, week: [CLAUDE_B], recent: [CLAUDE_B] },
  ],
  folders: [{ folder: "/home/me/code/finch-scripts", at: NOW - 7200, week: 2 },
            { folder: "/home/me/scratch", at: null, week: 1 }],
  more: 3,
  ...fields,
});
const GIVEN = ["atlas-api", "kestrel-web", "old-thing", "quiet"];
const report = (fields = {}, open = null) => factsReport(parseProjects(answer(fields)), GIVEN, open);

// -- the frame ------------------------------------------------------------------------------

test("a good read reports version 1, order 30, activity all, `sessions` gathered", () => {
  const found = report();
  assert.deepEqual([found.version, found.order, found.activity, found.gathers], [VERSION, ORDER, ACTIVITY, GATHERS]);
  assert.deepEqual([VERSION, ORDER, ACTIVITY, GATHERS, LABEL, READ_MS], [1, 30, "all", "sessions", "Sessions", 60_000]);
});

test("only projects the page gave and the route knows are reported, by key", () => {
  assert.deepEqual(Object.keys(report().projects), ["atlas-api", "kestrel-web", "old-thing"]);
  assert.equal(factsReport(parseProjects(answer()), [], null).projects["atlas-api"], undefined);
});

// -- a closed row ---------------------------------------------------------------------------

test("a closed row has its newest activity and a part counting its recent claims", () => {
  const { projects } = report();
  assert.deepEqual(projects["atlas-api"], {
    at: NOW - 600, parts: [{ text: "", count: RECENT_COUNT, claims: [CLAUDE_A] }] });
  assert.deepEqual(RECENT_COUNT, { one: "active recently", many: "active recently" });
});

test("a project with nothing recent has its time and no part; one with no time has neither", () => {
  const { projects } = report({ projects: [{ key: "old-thing", at: null, all: 1, week: [], recent: [] },
                                           { key: "kestrel-web", at: NOW - 60, all: 1, week: [OPENCODE_A], recent: [] }] });
  assert.deepEqual(projects["old-thing"], {});
  assert.deepEqual(projects["kestrel-web"], { at: NOW - 60 });
});

test("sections and stays are the open project's only", () => {
  const { projects } = report({}, "atlas-api");
  assert.equal(projects["kestrel-web"].sections, undefined);
  assert.equal(projects["kestrel-web"].stays, undefined);
  assert.ok(projects["atlas-api"].sections);
});

// -- the opened project ---------------------------------------------------------------------

test("the opened project has a Sessions row counting this week's claims, linked to the page", () => {
  const atlas = report({}, "atlas-api").projects["atlas-api"];
  assert.deepEqual(atlas.sections, [{
    label: "Sessions",
    lines: [{ parts: [{ text: "", count: WEEK_COUNT, claims: [CLAUDE_A, CODEX_A, CLAUDE_B] }] }],
    link: { text: "Sessions", module: "sessions", route: "" },
  }]);
  assert.deepEqual(WEEK_COUNT, { one: "session this week", many: "sessions this week",
                                 after_one: "more this week", after_many: "more this week" });
});

test("the opened project's Stays line counts all its sessions, one in the singular", () => {
  assert.deepEqual(report({}, "atlas-api").projects["atlas-api"].stays, ["its 9 sessions and their transcripts"]);
  assert.deepEqual(report({}, "kestrel-web").projects["kestrel-web"].stays, ["its 1 session and its transcript"]);
  assert.equal(staysLine(2), "its 2 sessions and their transcripts");
});

test("an opened project with no session this week has no Sessions row, and keeps its Stays line", () => {
  const old = report({}, "old-thing").projects["old-thing"];
  assert.equal(old.sections, undefined);
  assert.deepEqual(old.stays, ["its 4 sessions and their transcripts"]);
});

test("an opened project the route doesn't hold, and one with no sessions, say nothing", () => {
  assert.equal(report({}, "quiet").projects.quiet, undefined);
  const none = report({ projects: [{ key: "quiet", at: null, all: 0, week: [], recent: [] }] }, "quiet");
  assert.deepEqual(none.projects.quiet, {});
});

// -- folders --------------------------------------------------------------------------------

test("each folder in no project is a row with its time and this week's count, and more_folders", () => {
  const found = report();
  assert.deepEqual(found.folders, [
    { folder: "/home/me/code/finch-scripts", at: NOW - 7200, parts: [{ text: "2 sessions this week" }] },
    { folder: "/home/me/scratch", parts: [{ text: "1 session this week" }] },
  ]);
  assert.equal(found.more_folders, 3);
  assert.deepEqual([weekWords(1), weekWords(5)], ["1 session this week", "5 sessions this week"]);
});

test("a folder the contract would refuse is left out and counted in more_folders", () => {
  const found = report({ folders: [{ folder: "relative/dir", at: NOW, week: 1 },
                                   { folder: `/${"x".repeat(4096)}`, at: NOW, week: 1 },
                                   { folder: "C:\\work\\atlas", at: NOW, week: 1 }], more: 0 });
  assert.deepEqual(found.folders.map((f) => f.folder), ["C:\\work\\atlas"]);
  assert.equal(found.more_folders, 2);
});

test("no folders gives an empty list and more_folders 0", () => {
  const found = report({ folders: [], more: 0 });
  assert.deepEqual([found.folders, found.more_folders], [[], 0]);
});

// -- sessions' judgment: the state ----------------------------------------------------------

test("state off, error or any other word reports null; so does no answer", () => {
  for (const state of ["off", "error", "busy", ""]) {
    assert.equal(report({ state, detail: "why" }), null, state);
  }
  assert.equal(factsReport(null, GIVEN, "atlas-api"), null);
});

// -- the route's shape ----------------------------------------------------------------------

test("an answer out of shape reads as none", () => {
  const bad = [
    null, [], { ...answer(), shape: 2 }, { ...answer(), state: 1 }, { ...answer(), projects: {} },
    { ...answer(), folders: null }, { ...answer(), more: -1 }, { ...answer(), recently_s: "3600" },
    answer({ projects: [{ key: "a", at: "now", all: 1, week: [], recent: [] }] }),
    answer({ projects: [{ key: "a", at: null, all: 1.5, week: [], recent: [] }] }),
    answer({ projects: [{ key: "a", at: null, all: 1, week: [1], recent: [] }] }),
    answer({ projects: [{ key: "a", at: null, all: 1, week: [] }] }),
    answer({ folders: [{ folder: "/x", at: null }] }),
    answer({ folders: [{ folder: null, at: null, week: 1 }] }),
  ];
  for (const data of bad) assert.equal(parseProjects(data), null, JSON.stringify(data)?.slice(0, 80));
  assert.ok(parseProjects(answer()));
});

test("the given projects are read from the attribute's JSON, and a bad value gives none", () => {
  assert.deepEqual(parseGiven(JSON.stringify([{ key: "a", root: "/a", lead: null, associated: [] }, { root: "/b" },
                                              "c", { key: 3 }])), ["a"]);
  assert.deepEqual(parseGiven("not json"), []);
  assert.deepEqual(parseGiven(null), []);
  assert.deepEqual(parseGiven('{"key": "a"}'), []);
});

// -- the read failing -----------------------------------------------------------------------

/** A feed over `answers`, read in order, and the report it gives after each read. */
async function reportsOver(answers) {
  let i = 0;
  const feed = new Feed(async () => answers[i++], parseProjects, () => {}, { wait: async () => {} });
  const out = [];
  for (let n = 0; n < answers.length; n += 1) {
    await feed.refresh();
    out.push(factsReport(goodValue(feed), GIVEN, null));
  }
  return out;
}

test("a failed read drops every fact at once, and the next good read puts them back", async () => {
  const ok = { status: 200, data: answer() };
  const reports = await reportsOver([ok, { status: 500, data: null }, ok]);
  assert.ok(reports[0].projects["atlas-api"]);
  assert.equal(reports[1], null);
  assert.deepEqual(reports[2], reports[0]);
});

test("a refused read, sessions gone, no answer and an unreadable body each report null", async () => {
  const ok = { status: 200, data: answer() };
  for (const bad of [{ status: 422, data: { refused: "sessions' config cannot be read" } },
                     { status: 404, data: null }, { status: 0, data: null },
                     { status: 200, data: { shape: 1 } }]) {
    const reports = await reportsOver([ok, bad]);
    assert.ok(reports[0], JSON.stringify(bad));
    assert.equal(reports[1], null, JSON.stringify(bad));
  }
});
