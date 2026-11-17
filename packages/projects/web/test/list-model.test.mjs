import assert from "node:assert/strict";
import test from "node:test";

import { claimedBelow, inOrder, partText } from "../src/claims.ts";
import { readReport } from "../src/facts.ts";
import {
  hasActivity, MISSING, MISSING_FIX, MISSING_RANK, newestAt, NOT_USED, projectRow, rowAttributes, saysUnused,
} from "../src/list-model.ts";

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
const ATLAS = listed("atlas-api");
const QUILL = listed("quill-mobile", { exists: false });
const GIVEN = [ATLAS, QUILL].map(({ key, root, lead, associated }) => ({ key, root, lead, associated }));

/** A report as the page holds it, read through the page's own reader. */
const report = (order, projects, extra = {}) => {
  const read = readReport({ version: 1, order, projects, ...extra }, GIVEN, null);
  assert.deepEqual(read.refused, []);
  return read.report;
};
const row = (project, reports = [], over = {}) =>
  projectRow(project, { home: HOME, reports, open: null, now: NOW, clock: UTC, ...over });
const running = (claims) => ({ text: "", count: { one: "running", many: "running" }, claims });
const recent = (claims) => ({ text: "", count: { one: "active recently", many: "active recently" }, claims });

test("a quiet row is one line: the key, the ~ root and Open", () => {
  assert.deepEqual(row(ATLAS), {
    key: "atlas-api", name: "atlas-api", where: "~/code/atlas-api", fact: null, act: "Open",
    lead: null, leadMore: null, rest: null, tail: null, changeFolder: false, missing: false, rank: null,
    at: null,
  });
});

test("a quiet row sets none of line two's attributes", () => {
  assert.deepEqual(rowAttributes(row(ATLAS)), {
    name: "atlas-api", where: "~/code/atlas-api", fact: null, lead: null, "lead-heavy": null,
    "lead-more": null, rest: null, "rest-strong": null, tail: null, toggles: "", open: null,
  });
  const usage = report(40, { "atlas-api": { parts: [{ text: "$8.49 today" }] } });
  const busy = rowAttributes(row(ATLAS, [usage], { open: "atlas-api" }));
  assert.equal(busy.tail, "$8.49 today");
  assert.equal(busy.open, "");
  const missing = rowAttributes(row(QUILL));
  assert.deepEqual([missing.lead, missing["lead-heavy"], missing["lead-more"]], [MISSING, "", MISSING_FIX]);
  const warned = rowAttributes(row(ATLAS, [report(10, { "atlas-api": { parts: [{ text: "w", warn: true }] } })]));
  assert.deepEqual([warned.rest, warned["rest-strong"], warned.lead], ["w", "", null]);
});

test("the open row's act is Close", () => {
  assert.equal(row(ATLAS, [], { open: "atlas-api" }).act, "Close");
  assert.equal(row(ATLAS, [], { open: "quill-mobile" }).act, "Open");
});

test("the kind's word follows the root, but for a code project", () => {
  assert.equal(row(listed("reviewer-home", { root: `${HOME}/agents/reviewer`, kind: "agent" })).where,
               "~/agents/reviewer · agent's home");
  assert.equal(row(listed("gardening", { root: "/srv/topics/gardening", kind: "topic" })).where,
               "/srv/topics/gardening · topic");
  assert.equal(row(listed("x", { root: "/home/me2/x" })).where, "/home/me2/x");
  assert.equal(row(ATLAS, [], { home: null }).where, `${HOME}/code/atlas-api`);
});

test("a missing folder leads line two, heavy, with its fix and Change folder", () => {
  const got = row(QUILL);
  assert.equal(got.lead, MISSING);
  assert.equal(got.lead, "Its folder doesn't exist");
  assert.equal(got.leadMore, MISSING_FIX);
  assert.equal(got.leadMore, "point it at where the folder went, or remove it");
  assert.equal(got.changeFolder, true);
  assert.equal(got.missing, true);
  assert.equal(got.rank, MISSING_RANK);
  assert.equal(got.rest, null);
  assert.equal(got.tail, null);
});

test("with a missing folder and heavy news, the fix follows the folder's words", () => {
  const services = report(50, { "quill-mobile": { parts: [
    { text: "queue-worker failed at 18:31", heavy: true, rank: 30 },
    { text: "docs-preview is stopped", warn: true },
  ] } });
  const got = row(QUILL, [services]);
  assert.deepEqual([got.lead, got.leadMore, got.rest],
                   [MISSING, MISSING_FIX, "queue-worker failed at 18:31 · docs-preview is stopped"]);
  assert.equal(got.rank, 30);
  const attributes = rowAttributes(got);
  assert.deepEqual([attributes["lead-heavy"], attributes["rest-strong"]], ["", ""]);
});

test("a root the page server can't stat now says nothing", () => {
  const got = row(listed("atlas-api", { exists: null }));
  assert.equal(got.lead, null);
  assert.equal(got.changeFolder, false);
  assert.equal(got.rank, null);
});

test("line two: heavy ranked parts lead, then ranked light ones and warnings, then the rest in order", () => {
  const services = report(50, { "atlas-api": { parts: [
    { text: "queue-worker failed at 18:31", heavy: true, rank: 30 },
    { text: "docs-preview is stopped", warn: true },
  ] } });
  const repo = report(60, { "atlas-api": { parts: [{ text: "a git hook broke at 17:55", heavy: true, rank: 20 }] } });
  const usage = report(40, { "atlas-api": { parts: [{ text: "$8.49 today" }] } });
  const launcher = report(10, { "atlas-api": { parts: [running(["claude/a", "claude/b"])] } });
  const waits = report(70, { "atlas-api": { parts: [{ text: "a reply waits", rank: 20 },
                                                    { text: "a budget runs out", rank: 40 }] } });
  const got = row(ATLAS, [services, repo, usage, launcher, waits]);
  assert.equal(got.lead, "a git hook broke at 17:55 · queue-worker failed at 18:31");
  assert.equal(got.leadMore, null);
  assert.equal(got.rest, "a reply waits · a budget runs out · docs-preview is stopped");
  assert.equal(got.tail, "2 running · $8.49 today");
  assert.equal(got.rank, 20);
  assert.equal(got.changeFolder, false);
});

test("the missing folder comes before every report's heavy part", () => {
  const services = report(50, { "quill-mobile": { parts: [{ text: "queue-worker failed", heavy: true, rank: 10 }] } });
  const got = row(QUILL, [services]);
  assert.equal(got.lead, "Its folder doesn't exist");
  assert.equal(got.rest, "queue-worker failed", "after the folder's fix");
  assert.equal(got.rank, 10);
});

test("a heavy part with no rank is drawn heavy, after the ranked ones; it doesn't rank the row", () => {
  const one = report(10, { "atlas-api": { parts: [{ text: "odd", heavy: true }, { text: "plain" }] } });
  const two = report(20, { "atlas-api": { parts: [{ text: "broken", heavy: true, rank: 30 }] } });
  const got = row(ATLAS, [one, two]);
  assert.equal(got.lead, "broken · odd");
  assert.equal(got.tail, "plain");
  assert.equal(row(ATLAS, [one]).rank, null);
});

test("a warning doesn't rank the row", () => {
  const got = row(ATLAS, [report(10, { "atlas-api": { parts: [{ text: "docs-preview is stopped", warn: true }] } })]);
  assert.equal(got.rest, "docs-preview is stopped");
  assert.equal(got.rank, null);
});

test("parts of one order keep the order the reports were placed in", () => {
  const first = report(20, { "atlas-api": { parts: [{ text: "first" }] } });
  const second = report(20, { "atlas-api": { parts: [{ text: "second" }] } });
  assert.equal(row(ATLAS, [first, second]).tail, "first · second");
  assert.equal(row(ATLAS, [second, first]).tail, "second · first");
});

test("claims: a session counted by a lower order is not counted again", () => {
  const launcher = report(10, { "atlas-api": { parts: [running(["claude/a", "claude/b"])] } });
  const sessions = report(20, { "atlas-api": { parts: [recent(["claude/b", "codex/c"])] } });
  assert.equal(row(ATLAS, [sessions, launcher]).tail, "2 running · 1 active recently");
  const allRunning = report(20, { "atlas-api": { parts: [recent(["claude/a", "claude/b"])] } });
  assert.equal(row(ATLAS, [launcher, allRunning]).tail, "2 running", "a count of 0 is not drawn");
  assert.equal(row(ATLAS, [allRunning]).tail, "2 active recently", "with no lower order, every claim counts");
});

test("a claim on another project subtracts nothing", () => {
  const launcher = report(10, { "quill-mobile": { parts: [running(["claude/a"])] } });
  const sessions = report(20, { "atlas-api": { parts: [recent(["claude/a"])] } });
  assert.equal(row(ATLAS, [launcher, sessions]).tail, "1 active recently");
});

test("a request claim names no session, so it never matches another's", () => {
  const launcher = report(10, { "atlas-api": { parts: [running(["request/r1"])] } });
  const sessions = report(20, { "atlas-api": { parts: [recent(["claude/a"])] } });
  assert.equal(row(ATLAS, [launcher, sessions]).tail, "1 running · 1 active recently");
});

test("counted words: one and many; a claim listed twice counts once", () => {
  const part = { text: "", heavy: false, warn: false, rank: null, claims: ["claude/a", "claude/a"],
                 count: { one: "session", many: "sessions", after_one: "more", after_many: "more" } };
  assert.equal(partText(part, new Set()), "1 session");
  assert.equal(partText({ ...part, claims: ["claude/a", "claude/b"] }, new Set()), "2 sessions");
  assert.equal(partText({ ...part, claims: ["claude/a", "claude/b"] }, new Set(), { afterLines: true }), "2 more");
  assert.equal(partText({ ...part, count: { ...part.count, after_one: null } }, new Set(), { afterLines: true }),
               "1 session");
  assert.equal(partText(part, new Set(["claude/a"])), null);
  assert.equal(partText({ ...part, count: null, text: "plain" }, new Set(["claude/a"])), "plain");
});

test("claimed below takes parts and section lines of lower orders only", () => {
  const given = [{ key: "atlas-api", root: "/r", lead: null, associated: [] }];
  const low = readReport({ version: 1, order: 10, projects: { "atlas-api": {
    parts: [{ text: "p", claims: ["claude/p"] }],
    sections: [{ label: "Sessions", lines: [{ parts: [{ text: "l", claims: ["claude/q"] }], claims: ["claude/l"] }] }],
  } } }, given, "atlas-api").report;
  const same = readReport({ version: 1, order: 20, projects: { "atlas-api": { parts: [{ text: "s", claims: ["claude/s"] }] } } },
                          given, "atlas-api").report;
  assert.deepEqual([...claimedBelow([low, same], "atlas-api", 20)].sort(), ["claude/l", "claude/p", "claude/q"]);
  assert.deepEqual([...claimedBelow([low, same], "atlas-api", 10)], []);
  assert.deepEqual(inOrder([same, low]).map((r) => r.order), [10, 20]);
});

test("the fact: the newest at any report gave, as last active", () => {
  const sessions = report(20, { "atlas-api": { at: at("2026-11-15T19:01:00Z") } }, { activity: "all" });
  const launcher = report(10, { "atlas-api": { at: at("2026-11-15T19:09:00Z") } }, { activity: "window" });
  assert.equal(row(ATLAS, [sessions, launcher]).fact, "last active 19:09");
  assert.equal(row(ATLAS, [sessions, launcher]).at, at("2026-11-15T19:09:00Z"));
  assert.equal(newestAt([sessions, launcher], "atlas-api"), at("2026-11-15T19:09:00Z"));
  const friday = report(20, { "atlas-api": { at: at("2026-11-13T22:10:00Z") } }, { activity: "all" });
  assert.equal(row(ATLAS, [friday]).fact, "last active Fri 22:10");
});

test("not used yet only while a source of all activity has reported", () => {
  const all = report(20, { "atlas-api": { at: at("2026-11-15T19:01:00Z") } }, { activity: "all" });
  const window = report(10, { "atlas-api": { at: at("2026-11-15T19:09:00Z") } }, { activity: "window" });
  assert.equal(row(QUILL, [all]).fact, NOT_USED);
  assert.equal(row(QUILL, [all]).fact, "not used yet");
  assert.equal(row(QUILL, [all, window]).fact, "not used yet");
  assert.equal(row(QUILL, [window]).fact, null, "a window source can't say a project was unused");
  assert.equal(row(QUILL, []).fact, null);
  assert.equal(saysUnused([window]), false);
  assert.equal(saysUnused([window, all]), true);
});

test("an activity source is a report that gives some project an at", () => {
  assert.equal(hasActivity([]), false);
  assert.equal(hasActivity([report(10, { "atlas-api": { parts: [{ text: "$1" }] } })]), false);
  assert.equal(hasActivity([report(10, {}, { activity: "all" })]), false, "activity with no at is no source");
  assert.equal(hasActivity([report(10, { "atlas-api": { parts: [{ text: "idle" }] } }, { activity: "window" })]), false);
  assert.equal(hasActivity([report(10, { "atlas-api": { at: 5 } })]), true);
  assert.equal(hasActivity([report(10, {}, { activity: "window" }), report(20, { "atlas-api": { at: 5 } }, { activity: "all" })]),
               true);
});

test("rows with facts from four components", () => {
  const launcher = report(10, { "atlas-api": { at: at("2026-11-15T19:09:00Z"), parts: [running(["claude/a", "claude/b"])] } },
                          { activity: "window" });
  const sessions = report(20, {
    "atlas-api": { at: at("2026-11-15T19:01:00Z"), parts: [recent(["claude/a"])] },
  }, { activity: "all" });
  const usage = report(40, { "atlas-api": { parts: [{ text: "$8.49 today" }] } });
  const services = report(50, { "atlas-api": { parts: [{ text: "queue-worker failed at 18:31", heavy: true, rank: 30 }] } });
  const atlas = row(ATLAS, [launcher, sessions, usage, services]);
  assert.deepEqual([atlas.name, atlas.where, atlas.fact, atlas.act], ["atlas-api", "~/code/atlas-api", "last active 19:09", "Open"]);
  assert.deepEqual([atlas.lead, atlas.rest, atlas.tail], ["queue-worker failed at 18:31", null, "2 running · $8.49 today"]);
  const quill = row(QUILL, [launcher, sessions, usage, services]);
  assert.deepEqual([quill.fact, quill.lead, quill.leadMore, quill.tail, quill.changeFolder],
                   ["not used yet", MISSING, MISSING_FIX, null, true]);
});
