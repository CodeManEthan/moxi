import assert from "node:assert/strict";
import test from "node:test";

import {
  FACTS_COMPONENT, FACTS_EVENT, isAbsolutePath, LINES_MAX, ORDER_DEFAULT, PARTS_MAX, readReport,
  TEXT_MAX_CHARS, VERSION,
} from "../src/facts.ts";

const GIVEN = [
  { key: "alpha", root: "/w/alpha", lead: "lead-a", associated: ["helper"] },
  { key: "Beta", root: "/w/beta", lead: null, associated: [] },
];
const report = (over) => ({ version: 1, order: 30, ...over });
const read = (detail, open = null) => readReport(detail, GIVEN, open);
const part = (text, over = {}) => ({ text, heavy: false, warn: false, rank: null, claims: [], count: null,
                                     ...over });

test("the contract's names", () => {
  assert.equal(FACTS_COMPONENT, "project-facts");
  assert.equal(FACTS_EVENT, "moxi-project-facts");
  assert.equal(VERSION, 1);
  assert.equal(TEXT_MAX_CHARS, 200);
  assert.equal(PARTS_MAX, 8);
  assert.equal(LINES_MAX, 6);
});

test("null and undefined are nothing to say, refusing nothing", () => {
  assert.deepEqual(read(null), { report: null, refused: [] });
  assert.deepEqual(read(undefined), { report: null, refused: [] });
});

test("a detail that is no object is refused whole", () => {
  for (const detail of ["text", 3, [report({})], true]) {
    assert.deepEqual(read(detail), { report: null, refused: ["report: not an object"] });
  }
});

test("another version is refused whole, whatever else it carries", () => {
  for (const version of [2, 0, "1", undefined, null]) {
    const got = read({ version, order: 10, projects: { alpha: { at: 5 } } });
    assert.deepEqual(got, { report: null, refused: ["version: not 1"] });
  }
});

test("a full report reads with every field kept", () => {
  const detail = report({
    activity: "all", gathers: "sessions",
    projects: {
      alpha: {
        at: 1763200000,
        parts: [{ text: "x", count: { one: "running", many: "running" }, claims: ["claude/a1"] },
                { text: "queue-worker failed", heavy: true, rank: 30 },
                { text: "docs-preview is stopped", warn: true }],
        sections: [{ label: "Sessions", rank: 30,
                     lines: [{ parts: [{ text: "planner, slot 1" }], claims: ["claude/a1"] }],
                     link: { text: "Sessions", module: "sessions", route: "" } }],
        agents: { "lead-a": { runs: true, state: "running now", where: "in slot 4" },
                  helper: { runs: false, state: "not running now" } },
        stays: ["its 2 sessions and their transcripts"],
      },
      Beta: { parts: [{ text: "1 active recently" }] },
    },
    folders: [{ folder: "/w/finch", at: 1763100000, parts: [{ text: "2 sessions this week" }] }],
    more_folders: 3,
  });
  const got = read(detail, "alpha");
  assert.deepEqual(got.refused, []);
  const { report: kept } = got;
  assert.equal(kept.order, 30);
  assert.equal(kept.activity, "all");
  assert.equal(kept.gathers, "sessions");
  assert.equal(kept.more_folders, 3);
  assert.deepEqual([...kept.projects.keys()], ["alpha", "Beta"]);
  const alpha = kept.projects.get("alpha");
  assert.equal(alpha.at, 1763200000);
  assert.deepEqual(alpha.parts, [
    part("x", { claims: ["claude/a1"], count: { one: "running", many: "running", after_one: null, after_many: null } }),
    part("queue-worker failed", { heavy: true, rank: 30 }),
    part("docs-preview is stopped", { warn: true }),
  ]);
  assert.deepEqual(alpha.sections, [{
    label: "Sessions", rank: 30,
    lines: [{ parts: [part("planner, slot 1")], claims: ["claude/a1"] }],
    link: { text: "Sessions", module: "sessions", route: "" },
  }]);
  assert.deepEqual([...alpha.agents], [
    ["lead-a", { runs: true, state: "running now", where: "in slot 4" }],
    ["helper", { runs: false, state: "not running now", where: null }],
  ]);
  assert.deepEqual(alpha.stays, ["its 2 sessions and their transcripts"]);
  assert.deepEqual(kept.projects.get("Beta"),
                   { at: null, parts: [part("1 active recently")], sections: [], agents: new Map(), stays: [] });
  assert.deepEqual(kept.folders, [{ folder: "/w/finch", at: 1763100000, parts: [part("2 sessions this week")] }]);
});

test("a bare report has its defaults", () => {
  const got = read(report({}));
  assert.deepEqual(got, {
    report: { order: 30, activity: null, gathers: null, projects: new Map(), folders: [], more_folders: 0 },
    refused: [],
  });
});

test("an order that is missing or out of range reads as the default, and is named", () => {
  for (const order of [undefined, -1, 1000, 2.5, "10", null]) {
    const got = read({ version: 1, order });
    assert.equal(got.report.order, ORDER_DEFAULT);
    assert.deepEqual(got.refused, ["order: not a whole number from 0 to 999; read as 500"]);
  }
  assert.equal(read(report({ order: 0 })).report.order, 0);
  assert.equal(read(report({ order: 999 })).report.order, 999);
});

test("activity, gathers and more_folders of the wrong type are dropped", () => {
  const got = read(report({ activity: "some", gathers: 4, more_folders: -2 }));
  assert.equal(got.report.activity, null);
  assert.equal(got.report.gathers, null);
  assert.equal(got.report.more_folders, 0);
  assert.deepEqual(got.refused, ["activity: not all or window", "gathers: not a string",
                                 "more_folders: not a whole number from 0"]);
  assert.equal(read(report({ activity: "window" })).report.activity, "window");
});

test("a key the page did not give is refused; the keys given are matched exactly", () => {
  const got = read(report({ projects: { alpha: { at: 1 }, ALPHA: { at: 2 }, zeta: { at: 3 }, beta: {} } }));
  assert.deepEqual([...got.report.projects.keys()], ["alpha"]);
  assert.deepEqual(got.refused, ['projects["ALPHA"]: no such project was given',
                                 'projects["zeta"]: no such project was given',
                                 'projects["beta"]: no such project was given']);
});

test("a key that names an object's own field is no project", () => {
  const got = read(JSON.parse('{"version": 1, "order": 1, "projects": {"__proto__": {"at": 1}, "constructor": {}}}'));
  assert.equal(got.report.projects.size, 0);
  assert.equal(got.refused.length, 2);
});

test("projects and a project that are not objects are refused", () => {
  assert.deepEqual(read(report({ projects: [] })).refused, ["projects: not an object"]);
  const got = read(report({ projects: { alpha: "x", Beta: { at: 1 } } }));
  assert.deepEqual([...got.report.projects.keys()], ["Beta"]);
  assert.deepEqual(got.refused, ['projects["alpha"]: not an object']);
});

test("at must be epoch seconds", () => {
  for (const at of ["2026-11-15T10:00:00Z", -5, Infinity, NaN, null]) {
    const got = read(report({ projects: { alpha: { at } } }));
    assert.equal(got.report.projects.get("alpha").at, null);
    assert.deepEqual(got.refused, ['projects["alpha"].at: not epoch seconds']);
  }
  assert.equal(read(report({ projects: { alpha: { at: 0 } } })).report.projects.get("alpha").at, 0);
  assert.equal(read(report({ projects: { alpha: { at: 1.5 } } })).report.projects.get("alpha").at, 1.5);
});

test("text over 200 characters drops its part, and the rest of the row is kept", () => {
  const long = "a".repeat(201);
  const got = read(report({ projects: { alpha: { parts: [{ text: long }, { text: "kept" }] } } }));
  assert.deepEqual(got.report.projects.get("alpha").parts, [part("kept")]);
  assert.deepEqual(got.refused, ['projects["alpha"].parts[0].text: over 200 characters']);
  const edge = "é".repeat(200);
  assert.deepEqual(read(report({ projects: { alpha: { parts: [{ text: edge }] } } })).refused, []);
  const astral = "😀".repeat(200);
  assert.deepEqual(read(report({ projects: { alpha: { parts: [{ text: astral }] } } })).refused, [],
                   "characters are counted, not UTF-16 units");
});

test("a part with no text, an empty one or one that is no object is left out", () => {
  const got = read(report({ projects: { alpha: { parts: [{}, { text: "" }, "x", { text: 3 }, { text: "ok" }] } } }));
  assert.deepEqual(got.report.projects.get("alpha").parts, [part("ok")]);
  assert.deepEqual(got.refused, [
    'projects["alpha"].parts[0].text: not a string',
    'projects["alpha"].parts[1].text: empty',
    'projects["alpha"].parts[2]: not a part',
    'projects["alpha"].parts[3].text: not a string',
  ]);
});

test("a part's optional fields of the wrong type are dropped, the part kept", () => {
  const raw = { text: "t", heavy: "yes", warn: 1, rank: 0, claims: "claude/a", count: { one: "x" } };
  const got = read(report({ projects: { alpha: { parts: [raw] } } }));
  assert.deepEqual(got.report.projects.get("alpha").parts, [part("t")]);
  assert.deepEqual(got.refused, [
    'projects["alpha"].parts[0].count.many: not a string',
    'projects["alpha"].parts[0].heavy: not true or false',
    'projects["alpha"].parts[0].warn: not true or false',
    'projects["alpha"].parts[0].rank: not a whole number from 1 to 999',
    'projects["alpha"].parts[0].claims: not a list',
  ]);
});

test("ranks are whole numbers on Home's scale", () => {
  for (const rank of [10, 20, 30, 40]) {
    assert.equal(read(report({ projects: { alpha: { parts: [{ text: "t", rank }] } } }))
      .report.projects.get("alpha").parts[0].rank, rank);
  }
  for (const rank of [0, -10, 30.5, "30", 1000]) {
    assert.equal(read(report({ projects: { alpha: { parts: [{ text: "t", rank }] } } }))
      .report.projects.get("alpha").parts[0].rank, null);
  }
});

test("claims drop each one that won't read", () => {
  const got = read(report({ projects: { alpha: { parts: [{ text: "t", claims: ["claude/a", 3, "", "codex/b"] }] } } }));
  assert.deepEqual(got.report.projects.get("alpha").parts[0].claims, ["claude/a", "codex/b"]);
  assert.equal(got.refused.length, 2);
});

test("a counted part keeps its words, and may leave its text empty", () => {
  const count = { one: "running", many: "running", after_one: "more this week", after_many: "more this week" };
  const got = read(report({ projects: { alpha: { parts: [{ text: "", count, claims: ["claude/a"] }] } } }));
  assert.deepEqual(got.refused, []);
  assert.deepEqual(got.report.projects.get("alpha").parts[0].count, count);
  const bad = read(report({ projects: { alpha: { parts: [{ text: "", count: "2 running" }] } } }));
  assert.deepEqual(bad.report.projects.get("alpha").parts, []);
  assert.deepEqual(bad.refused, ['projects["alpha"].parts[0].count: not an object',
                                 'projects["alpha"].parts[0].text: empty']);
});

test("more than 8 parts on one row refuses the row's parts", () => {
  const parts = Array.from({ length: 9 }, (_, index) => ({ text: `p${index}` }));
  const got = read(report({ projects: { alpha: { at: 4, parts } } }));
  assert.deepEqual(got.report.projects.get("alpha").parts, []);
  assert.equal(got.report.projects.get("alpha").at, 4);
  assert.deepEqual(got.refused, ['projects["alpha"].parts: more than 8 parts']);
  assert.equal(read(report({ projects: { alpha: { parts: parts.slice(0, 8) } } }))
    .report.projects.get("alpha").parts.length, 8);
});

test("sections, agents and stays are taken for the open project only", () => {
  const facts = { sections: [{ label: "Cost", lines: [{ parts: [{ text: "$1" }] }] }],
                  agents: { "lead-a": { runs: false, state: "not running now" } }, stays: ["x"], at: 3 };
  const closed = read(report({ projects: { alpha: facts } }), null);
  assert.deepEqual(closed.report.projects.get("alpha"),
                   { at: 3, parts: [], sections: [], agents: new Map(), stays: [] });
  assert.deepEqual(closed.refused, [
    'projects["alpha"].sections: given for a project that is not open',
    'projects["alpha"].agents: given for a project that is not open',
    'projects["alpha"].stays: given for a project that is not open',
  ]);
  const other = read(report({ projects: { alpha: facts } }), "Beta");
  assert.equal(other.refused.length, 3);
  const opened = read(report({ projects: { alpha: facts } }), "alpha");
  assert.deepEqual(opened.refused, []);
  assert.equal(opened.report.projects.get("alpha").sections.length, 1);
});

test("a section over 6 lines is refused; its lines' parts keep the row's cap", () => {
  const line = { parts: [{ text: "l" }] };
  const sections = [{ label: "Long", lines: Array(7).fill(line) },
                    { label: "Six", lines: Array(6).fill(line) },
                    { label: "Wide", lines: [{ parts: Array(9).fill({ text: "w" }) }, line] }];
  const got = read(report({ projects: { alpha: { sections } } }), "alpha");
  const kept = got.report.projects.get("alpha").sections;
  assert.deepEqual(kept.map((section) => [section.label, section.lines.length]), [["Six", 6], ["Wide", 1]]);
  assert.deepEqual(got.refused, ['projects["alpha"].sections[0].lines: more than 6 lines',
                                 'projects["alpha"].sections[2].lines[0].parts: more than 8 parts']);
});

test("a section with no label, no lines or none left to draw is dropped", () => {
  const sections = [{ lines: [{ parts: [{ text: "a" }] }] }, { label: "No lines" },
                    { label: "Empty", lines: [{ parts: [] }, { parts: [{ text: "x".repeat(201) }] }] },
                    { label: "Kept", lines: [{ parts: [{ text: "k" }] }], rank: "high", link: { text: "Go" } }];
  const got = read(report({ projects: { alpha: { sections } } }), "alpha");
  assert.deepEqual(got.report.projects.get("alpha").sections,
                   [{ label: "Kept", rank: null, lines: [{ parts: [part("k")], claims: [] }], link: null }]);
  assert.deepEqual(got.refused, [
    'projects["alpha"].sections[0].label: not a string',
    'projects["alpha"].sections[1].lines: not a list',
    'projects["alpha"].sections[2].lines[1].parts[0].text: over 200 characters',
    'projects["alpha"].sections[3].rank: not a whole number from 1 to 999',
    'projects["alpha"].sections[3].link.module: not a string',
    'projects["alpha"].sections[3].link.route: not a string',
  ]);
});

test("an agent the page did not give for the open project is refused", () => {
  const agents = { "lead-a": { runs: true, state: "running now" }, scout: { runs: false, state: "not running now" },
                   helper: { runs: false, state: "not running now" } };
  const got = read(report({ projects: { alpha: { agents } } }), "alpha");
  assert.deepEqual([...got.report.projects.get("alpha").agents.keys()], ["lead-a", "helper"]);
  assert.deepEqual(got.refused, ['projects["alpha"].agents["scout"]: no such agent was given for the open project']);
  const leadless = read(report({ projects: { Beta: { agents: { "lead-a": { runs: true, state: "x" } } } } }), "Beta");
  assert.equal(leadless.report.projects.get("Beta").agents.size, 0);
});

test("a where on a say whose runs is false refuses the say", () => {
  const agents = { "lead-a": { runs: false, state: "not running now", where: "in slot 4" },
                   helper: { runs: true, state: "running now", where: "in slot 2" } };
  const got = read(report({ projects: { alpha: { agents } } }), "alpha");
  assert.deepEqual([...got.report.projects.get("alpha").agents],
                   [["helper", { runs: true, state: "running now", where: "in slot 2" }]]);
  assert.deepEqual(got.refused, ['projects["alpha"].agents["lead-a"].where: given with runs false']);
});

test("a say needs runs and a state", () => {
  const agents = { "lead-a": { state: "running now" }, helper: { runs: true, state: "" } };
  const got = read(report({ projects: { alpha: { agents } } }), "alpha");
  assert.equal(got.report.projects.get("alpha").agents.size, 0);
  assert.deepEqual(got.refused, ['projects["alpha"].agents["lead-a"].runs: not true or false',
                                 'projects["alpha"].agents["helper"].state: empty']);
  const where = read(report({ projects: { alpha: { agents: { helper: { runs: true, state: "s", where: 4 } } } } }), "alpha");
  assert.deepEqual(where.report.projects.get("alpha").agents.get("helper"), { runs: true, state: "s", where: null });
});

test("stays drop each line that won't read", () => {
  const got = read(report({ projects: { alpha: { stays: ["one", 2, "x".repeat(201), "two"] } } }), "alpha");
  assert.deepEqual(got.report.projects.get("alpha").stays, ["one", "two"]);
  assert.equal(got.refused.length, 2);
});

test("a folder's at is dropped from a report with no activity", () => {
  const folders = [{ folder: "/w/finch", at: 1763100000, parts: [{ text: "$0.82" }] }];
  const none = read(report({ folders }));
  assert.deepEqual(none.report.folders, [{ folder: "/w/finch", at: null, parts: [part("$0.82")] }]);
  assert.deepEqual(none.refused, ["folders[0].at: given by a report with no activity"]);
  const window = read(report({ activity: "window", folders }));
  assert.equal(window.report.folders[0].at, 1763100000);
  assert.deepEqual(window.refused, []);
  const wrong = read(report({ activity: "nightly", folders }));
  assert.equal(wrong.report.folders[0].at, null, "an activity that is refused is none");
});

test("a folder must be an absolute path, listed once", () => {
  const folders = [{ folder: "code/finch" }, { folder: "~/finch" }, { folder: 3 }, { folder: "/w/a" },
                   { folder: "/w/a", parts: [{ text: "again" }] }, { folder: "C:\\work\\b" },
                   { folder: "\\\\host\\share" }, { folder: `/${"d".repeat(4096)}` }, "x"];
  const got = read(report({ activity: "all", folders }));
  assert.deepEqual(got.report.folders.map((row) => row.folder), ["/w/a", "C:\\work\\b", "\\\\host\\share"]);
  assert.deepEqual(got.refused, [
    "folders[0].folder: not an absolute path", "folders[1].folder: not an absolute path",
    "folders[2].folder: not an absolute path", "folders[4].folder: listed twice",
    "folders[7].folder: over 4096 characters", "folders[8]: not an object",
  ]);
  assert.deepEqual(read(report({ folders: {} })).refused, ["folders: not a list"]);
});

test("absolute paths on each platform", () => {
  for (const path of ["/", "/home/jp", "C:\\Users\\jp", "c:/x", "\\\\srv\\share"]) assert.ok(isAbsolutePath(path), path);
  for (const path of ["", "~", "~/x", "x/y", "C:x", "\\x"]) assert.ok(!isAbsolutePath(path), path);
});

test("fields the contract doesn't name are ignored", () => {
  const got = read(report({ colour: "red", projects: { alpha: { at: 1, style: "bold", parts: [{ text: "t", size: 3 }] } } }));
  assert.deepEqual(got.refused, []);
  assert.deepEqual(got.report.projects.get("alpha").parts, [part("t")]);
});
