import assert from "node:assert/strict";
import test from "node:test";

import {
  BROKE, BROKEN, cappedLines, factsReport, GATHERS, LABEL, LINES_MAX, ORDER, OFF_HERE, parseGiven, piecesLine,
  projectRoute, recordWeight, VERSION,
} from "../src/facts-model.ts";
import { AT, HOME, piece, problem, record, repo, spec, UTC, value } from "./fixtures.mjs";

const GIVEN = [{ key: "atlas-api" }, { key: "kestrel-web" }];
const LINK = { text: "Git hooks", module: "repo", route: "project/atlas-api" };
const atlas = (over = {}) => repo({ project: "atlas-api", ...over });
const report = (repos, open = "atlas-api") => factsReport(value({ repos }), GIVEN, open, { clock: UTC });
const texts = (line) => line.parts.map((part) => part.text);
const lines = (found, key = "atlas-api") => found.projects[key].sections[0].lines.map(texts);

test("the report's frame: version 1, order 70, gathers git hooks, no activity", () => {
  const found = report([]);
  assert.equal(found.version, VERSION);
  assert.equal(VERSION, 1);
  assert.equal(found.order, ORDER);
  assert.equal(ORDER, 70);
  assert.equal(found.gathers, GATHERS);
  assert.equal(GATHERS, "git hooks");
  assert.equal("activity" in found, false);
  assert.deepEqual(found.projects, {});
});

test("a fail problem: heavy on line two, no time; its line leads with that, then its detail", () => {
  const found = report([atlas({ problems: [problem({ detail: "the shim is gone" })] })]);
  assert.deepEqual(found.projects["atlas-api"].parts, [{ text: BROKE, heavy: true, rank: BROKEN }]);
  assert.equal(BROKE, "a git hook broke");
  assert.equal(BROKEN, 30);
  const [section] = found.projects["atlas-api"].sections;
  assert.equal(section.rank, BROKEN);
  assert.deepEqual(section.lines[0].parts, [{ text: BROKE, heavy: true }, { text: "the shim is gone" }]);
});

test("a piece record and a push record: heavy with the time; their lines lead with that, then the text's first line", () => {
  for (const kind of ["piece", "push"]) {
    const found = report([atlas({ records: [record({ kind, at: AT("17:55"), text: "exit 2\nmore" })] })]);
    assert.deepEqual(found.projects["atlas-api"].parts, [{ text: `${BROKE} at 17:55`, heavy: true, rank: BROKEN }], kind);
    assert.deepEqual(found.projects["atlas-api"].sections[0].lines[0].parts,
                     [{ text: `${BROKE} at 17:55`, heavy: true }, { text: "exit 2" }], kind);
  }
});

test("the time is worded against the answer's day, and left out when it does not read", () => {
  let found = report([atlas({ records: [record({ at: AT("22:10", "2026-11-17") })] })]);
  assert.equal(found.projects["atlas-api"].parts[0].text, `${BROKE} at yesterday 22:10`);
  found = report([atlas({ records: [record({ at: "not a time" })] })]);
  assert.equal(found.projects["atlas-api"].parts[0].text, BROKE);
  found = report([atlas({ records: [record({ at: null })] })]);
  assert.equal(found.projects["atlas-api"].parts[0].text, BROKE);
});

test("line two's time is the newest among the repo's broken records", () => {
  const found = report([atlas({ records: [record({ at: AT("09:00") }), record({ kind: "push", at: AT("17:55") }),
                                           record({ at: AT("12:00") })] })]);
  assert.deepEqual(found.projects["atlas-api"].parts, [{ text: `${BROKE} at 17:55`, heavy: true, rank: BROKEN }]);
});

test("a commit record whose spec's status is error is broken; refused is a gate doing its job", () => {
  const error = atlas({ specs: [spec({ name: "notes/snapshot", status: "error" })],
                        records: [record({ kind: "commit", spec: "notes/snapshot", text: "git failed" })] });
  let found = report([error]);
  assert.equal(found.projects["atlas-api"].parts[0].heavy, true);
  assert.deepEqual(lines(found)[0], [`${BROKE} at 16:40`, "git failed"]);

  const refused = atlas({ specs: [spec({ name: "notes/snapshot", status: "refused" })],
                          records: [record({ kind: "commit", spec: "notes/snapshot", text: "secret-gate refused it\nx" })] });
  found = report([refused]);
  assert.equal(found.projects["atlas-api"].parts, undefined, "no line-two part");
  const [section] = found.projects["atlas-api"].sections;
  assert.equal(section.rank, undefined, "not ranked");
  assert.deepEqual(section.lines[0].parts, [{ text: "secret-gate refused it" }], "a normal-weight line");
});

test("a commit record with no spec of its name, or another status, is not broken", () => {
  for (const specs of [[], [spec({ name: "other", status: "error" })], [spec({ name: "notes/snapshot", status: "committed" })],
                       [spec({ name: "notes/snapshot", status: null })]]) {
    const one = atlas({ specs, records: [record({ kind: "commit", spec: "notes/snapshot", text: "dropped a.txt" })] });
    assert.equal(recordWeight(one.records[0], one), "said");
    const found = report([one]);
    assert.equal(found.projects["atlas-api"].parts, undefined);
    assert.deepEqual(found.projects["atlas-api"].sections[0].lines[0].parts, [{ text: "dropped a.txt" }]);
  }
});

test("a warn problem: nothing on line two, a normal-weight line of its detail; an ok problem says nothing", () => {
  const found = report([atlas({ problems: [problem({ state: "warn", detail: "an old shim" }), problem({ state: "ok" })] })]);
  assert.equal(found.projects["atlas-api"].parts, undefined);
  assert.deepEqual(lines(found), [["an old shim"]]);
});

test("the pieces line: enabled pieces by the Git hooks page's names in run order, then each switched off", () => {
  const pieces = [piece({ id: "secrets/secret-gate", event: "pre-commit", enabled: false }),
                  piece({ id: "repo/commit-rules", event: "commit-msg", order: 50 }),
                  piece({ id: "repo/push-gate", event: "pre-push" }),
                  piece({ id: "notes/tidy", event: "pre-commit", order: 10 })];
  assert.deepEqual(piecesLine({ pieces }).map((part) => part.text),
                   ["tidy, commit-rules, push-gate", `secret-gate ${OFF_HERE}`]);
  assert.equal(OFF_HERE, "is switched off here");
  assert.deepEqual(piecesLine({ pieces: [piece({ enabled: false })] }).map((part) => part.text),
                   [`commit-rules ${OFF_HERE}`]);
  assert.equal(piecesLine({ pieces: [] }), null);
  // Two pieces of one name are named by their ids, as the Git hooks page names them.
  assert.deepEqual(piecesLine({ pieces: [piece({ id: "a/gate", event: "pre-commit" }), piece({ id: "b/gate", event: "pre-commit" })] })
    .map((part) => part.text), ["a/gate, b/gate"]);
});

test("the opened Git hooks row: broken lines first, then the rest, then the pieces; linking to the project's repo", () => {
  const found = report([atlas({
    pieces: [piece({ id: "repo/commit-rules" }), piece({ id: "secrets/secret-gate", event: "pre-commit", enabled: false })],
    problems: [problem({ state: "warn", detail: "an old shim" })],
    records: [record({ at: AT("17:55"), text: "boom" })],
  })]);
  const [section] = found.projects["atlas-api"].sections;
  assert.equal(section.label, LABEL);
  assert.deepEqual(section.link, LINK);
  assert.deepEqual(lines(found), [[`${BROKE} at 17:55`, "boom"], ["an old shim"],
                                  ["commit-rules", `secret-gate ${OFF_HERE}`]]);
  assert.equal(found.projects["kestrel-web"], undefined);
});

test("a quiet repo with pieces: a row of its pieces, unranked, and nothing on line two", () => {
  const found = report([atlas({ pieces: [piece()] })]);
  assert.equal(found.projects["atlas-api"].parts, undefined);
  assert.deepEqual(lines(found), [["commit-rules"]]);
  assert.equal(found.projects["atlas-api"].sections[0].rank, undefined);
  assert.deepEqual(report([atlas()]).projects, {}, "a repo with nothing to say leaves no row");
});

test("sections are the open project's only; closed rows keep their line-two part", () => {
  const found = report([atlas({ records: [record()] })], "kestrel-web");
  assert.equal(found.projects["atlas-api"].sections, undefined);
  assert.equal(found.projects["atlas-api"].parts.length, 1);
});

test("two repos in one project: a part for each broken one, and each line starts with the repo's folder", () => {
  const found = report([
    atlas({ records: [record({ at: AT("17:55") })], pieces: [piece()] }),
    atlas({ root: `${HOME}/code/atlas-docs`, pieces: [piece({ id: "notes/tidy", event: "pre-commit" })] }),
    atlas({ root: "/srv/atlas-ops", problems: [problem()] }),
  ]);
  assert.deepEqual(found.projects["atlas-api"].parts.map((part) => part.text), [`${BROKE} at 17:55`, BROKE]);
  assert.deepEqual(lines(found), [
    ["~/code/atlas-api", `${BROKE} at 17:55`, "it broke"], ["~/code/atlas-api", "commit-rules"],
    ["~/code/atlas-docs", "tidy"], ["/srv/atlas-ops", BROKE, "something is wrong"]]);
});

test("past six lines, the last ends <n> more, heavy when a line past it is broken", () => {
  const quiet = (n) => Array.from({ length: n }, (_, i) => problem({ state: "warn", detail: `warn ${i}` }));
  let found = report([atlas({ problems: quiet(6) })]);
  assert.equal(lines(found).length, LINES_MAX);
  assert.equal(LINES_MAX, 6);
  found = report([atlas({ problems: quiet(9) })]);
  assert.equal(lines(found).length, 6);
  assert.deepEqual(lines(found)[5], ["warn 5", "3 more"]);
  assert.equal(found.projects["atlas-api"].sections[0].lines[5].parts[1].heavy, undefined);
  const capped = cappedLines([...Array.from({ length: 6 }, () => ({ parts: [{ text: "q" }], broke: false })),
                              { parts: [{ text: "b", heavy: true }], broke: true }]);
  assert.deepEqual(capped[5].parts, [{ text: "q" }, { text: "1 more", heavy: true }]);
});

test("a long detail is cut to the contract's cap", () => {
  const found = report([atlas({ problems: [problem({ state: "warn", detail: "x".repeat(400) })] })]);
  assert.equal(Array.from(lines(found)[0][0]).length, 200);
});

test("repos of no given project, or of none, are left out", () => {
  const found = report([repo({ project: null, records: [record()] }), repo({ project: "harbor-infra", records: [record()] })]);
  assert.deepEqual(found.projects, {});
});

test("an answer whose state is error, and a failed read, report null", () => {
  assert.equal(factsReport(value({ state: "error", detail: "no" }), GIVEN, "atlas-api"), null);
  assert.equal(factsReport(null, GIVEN, "atlas-api"), null);
});

test("the link's route is repo's address for the project, its key written as repo writes a name", () => {
  assert.equal(projectRoute("atlas-api"), "project/atlas-api");
  assert.equal(projectRoute("a b"), "project/a!20b");
});

test("the given projects are read from the page's JSON", () => {
  assert.deepEqual(parseGiven(JSON.stringify([{ key: "a", root: "/r" }, { root: "/x" }])), [{ key: "a" }]);
  assert.deepEqual(parseGiven("nope"), []);
});
