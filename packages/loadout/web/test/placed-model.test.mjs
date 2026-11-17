import assert from "node:assert/strict";
import test from "node:test";

import { actArgv, actScope, placedView, problemsOf, verdictLine } from "../src/placed-model.ts";
import { ATLAS, FOLDER, fullView, HOME, row, USER, withRow } from "./fixtures.mjs";

const one = (view) => {
  const problems = problemsOf(view);
  assert.equal(problems.length, 1, JSON.stringify(problems.map((p) => p.sentence)));
  return problems[0];
};
const lines = (p) => [p.sentence, p.word, p.where, p.fix, p.act?.verb ?? null];

const missing = (view, harnesses, name = "release-checklist") => harnesses.reduce(
  (v, harness) => withRow(v, { name, scope: "user", harness }, { state: "missing" }), view);

test("nothing needs him: the verdict and the counts at its right", () => {
  const placed = placedView(fullView());
  assert.deepEqual(placed.problems, []);
  assert.equal(placed.verdict, "Everything is placed and unchanged");
  assert.equal(placed.heavy, false);
  assert.equal(placed.counts, "Claude Code 10 · Codex 9 · OpenCode 8");
  assert.equal(placed.installAll, "counts");
});

test("missing: not placed, Install for one, both or all three", () => {
  const all = one(missing(fullView(), ["claude", "codex", "opencode"]));
  assert.deepEqual(lines(all), ["The skill release-checklist is listed and not placed", "not placed",
                                "Your items · Claude Code, Codex and OpenCode", "Install places it for all three",
                                "install"]);
  assert.deepEqual(all.act.scope, { user: true });
  assert.deepEqual(actArgv(all.act), ["install", "--user"]);
  assert.equal(one(missing(fullView(), ["claude", "codex"])).fix, "Install places it for both");
  assert.equal(one(missing(fullView(), ["codex"])).fix, "Install places it for Codex");
});

test("modified generated agent: changed, its why, Replace", () => {
  const view = withRow(fullView(), { name: "code-reviewer", harness: "codex" },
                       { state: "modified", replaceable: true, target: `${HOME}/.codex/agents/code-reviewer.toml` });
  const p = one(view);
  assert.deepEqual(lines(p), ["The agent code-reviewer was changed by hand", "changed",
                              "Your items · Codex · the file Codex reads, made from the source, was edited",
                              "Make the change in the source instead, or put loadout's version back", "replace"]);
  assert.deepEqual(p.act, { verb: "replace", scope: { user: true }, id: "agent:code-reviewer@codex", section: "user" });
  assert.deepEqual(actArgv(p.act), ["install", "--replace=agent:code-reviewer@codex", "--user"]);
});

test("modified entry: its entry in the file, Replace", () => {
  const view = withRow(fullView(), { name: "guard-rm", harness: "claude" }, { state: "modified", replaceable: true });
  assert.deepEqual(lines(one(view)), [
    "The hook guard-rm was changed by hand", "changed",
    "Your items · Claude Code · its entry in ~/.claude/settings.json was edited after loadout wrote it",
    "Change it in loadout.toml instead, or put loadout's entry back", "replace"]);
});

test("the rules file: one problem naming the items it holds", () => {
  const view = withRow(fullView(), { name: "*", harness: "codex" }, { state: "modified", replaceable: true });
  const p = one(view);
  assert.deepEqual(lines(p), ["The Codex rules file was changed by hand", "changed",
                              "Your items · Codex · ~/.codex/rules/loadout.rules holds git-reads",
                              "Change the rules in loadout.toml instead, or put loadout's file back", "replace"]);
  assert.equal(p.act.id, "permissions:git-reads@codex");
});

test("the global file's part: one problem whatever harnesses it gathers, a whole run", () => {
  let view = withRow(fullView(), { kind: "global", harness: "claude" },
                     { state: "modified", replaceable: true, target: `${HOME}/.claude/CLAUDE.md` });
  view = withRow(view, { kind: "global", harness: "codex" },
                 { state: "modified", replaceable: true, target: `${HOME}/.codex/AGENTS.md` });
  const p = one(view);
  assert.deepEqual(lines(p), ["loadout's part of Claude Code and Codex's global file was changed by hand", "changed",
                              "Your items · Claude Code and Codex · ~/.claude/CLAUDE.md; ~/.codex/AGENTS.md",
                              "Change it in global.md instead, or put loadout's part back", "replace"]);
  assert.deepEqual(p.act, { verb: "replace", scope: { whole: true }, id: "global:global@*", section: "user" });
  assert.deepEqual(actArgv(p.act), ["install", "--replace=global:global@*"]);
  assert.equal(verdictLine([p]), "1 item needs you");
});

test("blocked: an edited copy and a file over the link read changed; anything else in the way", () => {
  const copy = withRow(fullView(), { name: "tidy-notes", harness: "codex" },
                       { state: "blocked", edited: true, form: "copy", replaceable: true });
  assert.deepEqual(lines(one(copy)), [
    "The skill tidy-notes was changed by hand", "changed",
    "Your items · Codex · skills are copied on this machine, and Codex's copy was edited",
    "Copy your edit into the folder to keep it, or put the folder's version back", "replace"]);
  const link = withRow(fullView(), { name: "tidy-notes", harness: "codex" },
                       { state: "blocked", edited: true, form: "link", replaceable: true });
  assert.equal(one(link).where, "Your items · Codex · a file now stands where loadout's link was");
  const way = withRow(fullView(), { name: "tidy-notes", harness: "codex" },
                      { state: "blocked", replaceable: true });
  assert.deepEqual(lines(one(way)), ["Something else is where the skill tidy-notes goes", "in the way",
                                     "Your items · Codex · ~/.codex/skills/tidy-notes isn't loadout's",
                                     "Replace moves it aside and places yours", "replace"]);
});

test("stale: out of date with Install; a removal takes it out, or waits when no run applies it", () => {
  const stale = withRow(fullView(), { name: "tidy-notes", harness: "claude" }, { state: "stale" });
  assert.deepEqual(lines(one(stale)), ["The skill tidy-notes is out of date", "out of date",
                                       "Your items · Claude Code", "Install places the new version", "install"]);
  const gone = fullView();
  gone.rows = [...gone.rows, row({ name: "old-skill", harness: "claude", state: "stale" })];
  const removal = one(gone);
  assert.deepEqual(lines(removal), ["The skill old-skill is no longer listed and still placed", "out of date",
                                    "Your items · Claude Code", "Install takes it out", "install"]);
  const held = fullView();
  held.rows = [...held.rows, row({ scope: null, name: "old-skill", harness: "claude", state: "stale", applies: null })];
  const waits = one(held);
  assert.deepEqual(lines(waits), ["The skill old-skill is no longer listed and still placed", "out of date",
                                  "Claude Code", "loadout takes it out once every section can be placed", null]);
  assert.equal(waits.act, null);
  const whole = fullView();
  whole.rows = [...whole.rows, row({ scope: "gone-section", name: "old", harness: "codex", state: "stale",
                                     applies: "whole" })];
  assert.deepEqual(one(whole).act.scope, { whole: true });
});

test("would-commit: Replace when replaceable, Install when it ignores, else the row's reason", () => {
  const view = (over) => withRow(fullView(), { name: "api-lead", harness: "claude" },
                                 { state: "would-commit", reason: "git would commit it", ...over });
  const tracked = one(view({ replaceable: true }));
  assert.deepEqual(lines(tracked), ["git would commit the agent api-lead in atlas-api", "would be committed",
                                    "atlas-api · Claude Code",
                                    "git tracks what is there; Replace moves it aside and places yours", "replace"]);
  assert.deepEqual(tracked.act.scope, { project: "atlas-api" });
  assert.deepEqual(actArgv(tracked.act), ["install", "--replace=agent:api-lead@claude", "--project=atlas-api"]);
  const ignores = one(view({ ignores: true }));
  assert.deepEqual([ignores.fix, ignores.act.verb], ["Install adds it to loadout's ignore file", "install"]);
  assert.deepEqual(actArgv(ignores.act), ["install", "--project=atlas-api"]);
  const plain = one(view({}));
  assert.deepEqual([plain.fix, plain.act], ["git would commit it", null]);
});

test("bad-source: can't be read, the row's reason, no act", () => {
  const view = withRow(fullView(), { name: "tidy-notes", harness: "claude" },
                       { state: "bad-source", reason: "skills/tidy-notes/SKILL.md is missing" });
  assert.deepEqual(lines(one(view)), ["The skill tidy-notes can't be read from your folder", "can't be read",
                                      "Your items · Claude Code", "skills/tidy-notes/SKILL.md is missing", null]);
});

test("an edited ignore file: changed, the row's reason, no act", () => {
  const view = fullView();
  view.rows = [...view.rows, row({ scope: "atlas-api", kind: "ignore", name: "/w/atlas/.claude/.gitignore",
                                   harness: "*", state: "modified", reason: "add /skills to it",
                                   target: "/w/atlas/.claude/.gitignore", form: null, applies: null })];
  assert.deepEqual(lines(one(view)), ["loadout's ignore file in /w/atlas/.claude was changed by hand", "changed",
                                      "atlas-api", "add /skills to it", null]);
});

const refused = (refusal) => {
  const view = fullView({ sections: [USER, { ...ATLAS, refused: refusal }] });
  view.rows = view.rows.map((r) => r.scope === "atlas-api"
    ? { ...r, state: "blocked", reason: refusal.line, applies: null } : r);
  return view;
};

test("a key section with no projects: one problem; the path form and Copy only with last_root", () => {
  const LINE = "loadout: section atlas-api is named by a project key; install projects, or moxi install projects";
  const withRoot = one(refused({ cause: "no-projects", line: LINE, last_root: `${HOME}/code/atlas-api` }));
  assert.deepEqual(lines(withRoot), [
    "The section atlas-api is named by a project key, so nothing in it is placed", "not placed",
    "loadout.toml · release-checklist and api-lead · Claude Code, Codex and OpenCode",
    "Name the section by its path: [project.\"~/code/atlas-api\"]", null]);
  assert.equal(withRoot.fixNote, "where loadout last placed it");
  assert.equal(withRoot.copy, "[project.\"~/code/atlas-api\"]");
  const bare = one(refused({ cause: "no-projects", line: LINE, last_root: null }));
  assert.deepEqual([bare.fix, bare.copy, bare.fixNote], ["Name the section by its path", null, null]);
  for (const p of [withRoot, bare]) {
    const drawn = [p.sentence, p.word, p.where, p.fix, p.fixNote, p.copy].join(" ");
    assert.ok(!drawn.includes("moxi install projects"));
  }
});

test("a section refused for another cause: its line", () => {
  const p = one(refused({ cause: "busy", line: "projects is busy; try again", last_root: null }));
  assert.deepEqual([p.sentence, p.fix, p.copy], ["Nothing in the section atlas-api is placed",
                                                 "projects is busy; try again", null]);
});

test("a refused section's items aren't counted again, and the verdict names sections apart", () => {
  const view = missing(refused({ cause: "no-projects", line: "x", last_root: null }), ["claude"]);
  const placed = placedView(view);
  assert.equal(placed.problems.length, 2);
  assert.equal(placed.verdict, "1 item and 1 section need you");
  assert.equal(placed.counts, "Claude Code 7 of 10 placed · Codex 7 of 9 · OpenCode 6 of 8");
  assert.equal(verdictLine(placed.problems.filter((p) => p.refused)), "1 section needs you");
});

test("an item with two words is two problems, the earlier word first", () => {
  let view = withRow(fullView(), { name: "code-reviewer", harness: "codex" },
                     { state: "modified", replaceable: true });
  view = withRow(view, { name: "code-reviewer", harness: "opencode" }, { state: "missing" });
  const placed = placedView(view);
  assert.deepEqual(placed.problems.map((p) => p.word), ["changed", "not placed"]);
  assert.equal(placed.verdict, "1 item needs you");
});

test("the order: by word, then section with Your items first, then name", () => {
  let view = missing(fullView(), ["claude"], "tidy-notes");
  view = withRow(view, { name: "release-checklist", scope: "atlas-api", harness: "claude" }, { state: "missing" });
  view = withRow(view, { name: "api-lead", harness: "codex" }, { state: "modified", form: "generated" });
  view = missing(view, ["codex"], "release-checklist");
  view = withRow(view, { name: "write-changelog", harness: "claude" }, { state: "stale" });
  const placed = placedView(view);
  assert.deepEqual(placed.problems.map((p) => [p.word, p.section, p.name]), [
    ["changed", "atlas-api", "api-lead"],
    ["not placed", "user", "release-checklist"],
    ["not placed", "user", "tidy-notes"],
    ["not placed", "atlas-api", "release-checklist"],
    ["out of date", "user", "write-changelog"],
  ]);
  assert.equal(placed.verdict, "5 items need you");
  assert.equal(placed.heavy, true);
  assert.equal(placed.installAll, "verdict");
});

test("Install all sits on the counts line with fewer than two Install fixes", () => {
  const view = missing(fullView(), ["claude"]);
  assert.equal(placedView(view).installAll, "counts");
});

test("a Replace gathering every replaceable row of the item names it @*", () => {
  let view = withRow(fullView(), { name: "tidy-notes", harness: "codex" }, { state: "blocked", replaceable: true });
  view = withRow(view, { name: "tidy-notes", harness: "claude" }, { state: "blocked", replaceable: true });
  assert.equal(one(view).act.id, "skill:tidy-notes@*");
  view = withRow(view, { name: "tidy-notes", harness: "opencode" },
                 { state: "blocked", edited: true, form: "copy", replaceable: true });
  const ids = problemsOf(view).map((p) => p.act.id);
  assert.deepEqual(ids, ["skill:tidy-notes@opencode", "skill:tidy-notes@claude"]);
});

test("a row no run applies draws no act; a row that isn't replaceable draws no Replace", () => {
  const view = withRow(fullView(), { name: "tidy-notes", harness: "codex" },
                       { state: "blocked", replaceable: false });
  assert.equal(one(view).act, null);
  const none = missing(fullView(), ["codex"]);
  const p = one(withRow(none, { name: "release-checklist", scope: "user", harness: "codex" }, { applies: null }));
  assert.equal(p.act, null);
  assert.equal(actScope("section", null), null);
});

test("no warning colour: nothing in the model names one", () => {
  const view = withRow(missing(fullView(), ["codex"]), { name: "code-reviewer", harness: "codex" },
                       { state: "modified", replaceable: true });
  const text = JSON.stringify(placedView(view));
  assert.ok(!/warn|danger|error/.test(text));
  assert.ok(!text.includes(FOLDER + "/"), "no folder path in a problem's words");
});
