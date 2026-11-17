import assert from "node:assert/strict";
import test from "node:test";

import { parseReplacePreview } from "../src/feed.ts";
import { confirmView, DIFF_SHOWN, questionOf } from "../src/confirm-model.ts";
import { fullView, HOME, row, withRow } from "./fixtures.mjs";

const KEPT_IN = `${HOME}/.local/share/moxi/moxi/displaced`;
const preview = (over) => ({ problem: null, case: "keeps", target: null, kept_in: KEPT_IN, tracked: false,
                             others: 0, label: "file", diff: "--- a\n+++ b\n-x\n+y\n", lines: 4, skipped: [], ...over });
const UNTIL = "~/.local/share/moxi/moxi/displaced, where it stays until you delete it.";

test("the preview parses, and refuses a field of the wrong type", () => {
  const p = preview({});
  assert.deepEqual(parseReplacePreview(p), p);
  assert.equal(parseReplacePreview({ ...p, others: "2" }), null);
  assert.equal(parseReplacePreview({ ...p, skipped: [{ path: "/x" }] }), null);
});

test("the question for each form", () => {
  assert.equal(questionOf([row({ kind: "agent", name: "rev", harness: "codex", form: "generated" })]),
               "Put loadout's version of rev back for Codex?");
  assert.equal(questionOf([row({ kind: "hook", name: "g", form: "entry" })]), "Put loadout's version of g back for Claude Code?");
  assert.equal(questionOf([row({ name: "t", form: "copy" }), row({ name: "t", harness: "codex", form: "copy" })]),
               "Put the folder's version of t back for Claude Code and Codex?");
  assert.equal(questionOf([row({ kind: "permissions", name: "*", harness: "codex", covers: ["a"] })]),
               "Put loadout's rules file back for Codex?");
  assert.equal(questionOf([row({ kind: "global", name: "global", harness: "codex" })]),
               "Put loadout's part of Codex's global file back?");
});

test("keeps, generated: the edited file kept; the legend; what stays", () => {
  const view = withRow(fullView(), { name: "code-reviewer", harness: "codex" },
                       { state: "modified", replaceable: true, target: `${HOME}/.codex/agents/code-reviewer.toml` });
  const rows = view.rows.filter((r) => r.name === "code-reviewer" && r.harness === "codex");
  const c = confirmView(rows, preview({ target: `${HOME}/.codex/agents/code-reviewer.toml` }), view);
  assert.equal(c.question, "Put loadout's version of code-reviewer back for Codex?");
  assert.equal(c.what, `Keeps the edited file as it is now in ${UNTIL}`);
  assert.deepEqual(c.notes, []);
  assert.equal(c.target, "~/.codex/agents/code-reviewer.toml");
  assert.equal(c.legend, "lines with − are in the file now, lines with + are what loadout writes");
  assert.deepEqual(c.diff, ["--- a", "+++ b", "-x", "+y"]);
  assert.equal(c.showAll, null);
  assert.equal(c.stays, "Stays the source, agents/code-reviewer.md, and the files Claude Code and OpenCode read");
  assert.deepEqual(c.scope, { user: true });
  assert.equal(c.canReplace, true);
});

test("the target and the diff's file lines in ~ form; a generated file where its harness reads it", () => {
  const view = withRow(fullView(), { name: "code-reviewer", harness: "codex" },
                       { state: "modified", replaceable: true, target: `${HOME}/.codex/agents/code-reviewer.toml` });
  const rows = view.rows.filter((r) => r.name === "code-reviewer" && r.harness === "codex");
  const store = `${HOME}/.local/state/moxi/loadout/generated/codex/code-reviewer.toml`;
  const diff = `--- ${store}\n+++ ${store}\n@@ -1,2 +1,1 @@\n x\n--- y\n+++ z\n`;
  const c = confirmView(rows, preview({ target: store, diff, lines: 6 }), view);
  assert.equal(c.target, "~/.codex/agents/code-reviewer.toml");
  // A hunk's own lines that look like file lines stay as they are.
  assert.deepEqual(c.diff, ["--- ~/.codex/agents/code-reviewer.toml", "+++ ~/.codex/agents/code-reviewer.toml",
                            "@@ -1,2 +1,1 @@", " x", "--- y", "+++ z"]);
  const entry = withRow(fullView(), { name: "guard-rm", harness: "claude" }, { state: "modified", replaceable: true });
  const er = entry.rows.filter((r) => r.name === "guard-rm" && r.harness === "claude");
  const file = `${HOME}/.claude/settings.json`;
  const e = confirmView(er, preview({ target: file, diff: `--- ${file}\n+++ ${file}\n@@ -1 +1 @@\n-a\n+b\n` }), entry);
  assert.deepEqual(e.diff.slice(0, 2), ["--- ~/.claude/settings.json", "+++ ~/.claude/settings.json"]);
});

test("keeps, entry: a copy of the file", () => {
  const view = withRow(fullView(), { name: "guard-rm", harness: "claude" }, { state: "modified", replaceable: true });
  const rows = view.rows.filter((r) => r.name === "guard-rm" && r.harness === "claude");
  const c = confirmView(rows, preview({ target: `${HOME}/.claude/settings.json` }), view);
  assert.equal(c.what, `Keeps a copy of ~/.claude/settings.json as it is now in ${UNTIL}`);
  assert.equal(c.stays, "Stays everything else in ~/.claude/settings.json");
});

test("moves, by what is there: an edited copy, a file over the link, an occupant", () => {
  const base = (over) => {
    const view = withRow(fullView(), { name: "tidy-notes", harness: "codex" }, { state: "blocked", replaceable: true, ...over });
    return { view, rows: view.rows.filter((r) => r.name === "tidy-notes" && r.harness === "codex") };
  };
  const copy = base({ edited: true, form: "copy" });
  let c = confirmView(copy.rows, preview({ case: "moves", target: `${HOME}/.codex/skills/tidy-notes` }), copy.view);
  assert.equal(c.what, `Moves your edited copy to ${UNTIL}`);
  assert.equal(c.legend, "lines with − are in the copy now, lines with + are your folder's");
  assert.equal(c.stays, "Stays the folder's tidy-notes, and Claude Code's link and OpenCode's link");
  const link = base({ edited: true });
  c = confirmView(link.rows, preview({ case: "moves" }), link.view);
  assert.equal(c.what, `Moves the file that stands where loadout's link was to ${UNTIL}`);
  const way = base({});
  c = confirmView(way.rows, preview({ case: "moves" }), way.view);
  assert.equal(c.what, `Moves what is there to ${UNTIL}`);
  assert.equal(c.legend, "lines with − are in the file now, lines with + are what loadout writes");
});

test("each optional line only when its fact is set", () => {
  const view = withRow(fullView(), { name: "api-lead", harness: "claude" },
                       { state: "would-commit", replaceable: true });
  const rows = view.rows.filter((r) => r.name === "api-lead" && r.harness === "claude");
  const c = confirmView(rows, preview({ case: "moves", tracked: true, others: 2, skipped: [
    { path: `${HOME}/code/x/a.bin`, why: "binary" }, { path: `${HOME}/code/x/l`, why: "link" },
    { path: `${HOME}/code/x/big`, why: "large" }, { path: `${HOME}/code/x/d`, why: "many" }] }), view);
  assert.deepEqual(c.notes, [
    "git tracks it, so git will show it deleted, and the row reads would be committed until you commit that.",
    "This runs Install for atlas-api, so 2 other changes waiting there are made too.",
    "Not shown here: ~/code/x/a.bin (not text)", "Not shown here: ~/code/x/l (a link)",
    "Not shown here: ~/code/x/big (too large)", "Not shown here: ~/code/x/d (past 200 files)",
  ]);
  const rules = withRow(fullView(), { name: "*", harness: "codex" }, { state: "modified", replaceable: true });
  const rr = rules.rows.filter((r) => r.covers !== null);
  assert.deepEqual(confirmView(rr, preview({ others: 1 }), rules).notes, [
    "The whole file is written again, with every rule in it.",
    "This runs Install for your items, so 1 other change waiting there is made too."]);
  const global = withRow(fullView(), { kind: "global", harness: "codex" }, { state: "modified", replaceable: true });
  const gr = global.rows.filter((r) => r.kind === "global" && r.harness === "codex");
  assert.deepEqual(confirmView(gr, preview({ others: 3 }), global).notes,
                   ["This runs a whole install, so 3 other changes waiting are made too."]);
  assert.deepEqual(confirmView(gr, preview({ others: 1 }), global).notes,
                   ["This runs a whole install, so 1 other change waiting is made too."]);
});

test("an MCP server its CLI writes: the entry's legend", () => {
  const view = fullView();
  const r = row({ kind: "mcp", name: "docs", form: "entry", state: "modified", replaceable: true });
  assert.equal(confirmView([r], preview({ label: "entry" }), view).legend,
               "the docs server's entry as it is now (−) and as loadout will send it (+)");
});

test("past 200 lines: the first 200 and Show all", () => {
  const diff = Array.from({ length: 250 }, (_, i) => `+line ${i}`).join("\n") + "\n";
  const r = row({ state: "blocked", replaceable: true });
  const c = confirmView([r], preview({ case: "moves", diff, lines: 250 }), fullView());
  assert.equal(c.diff.length, DIFF_SHOWN);
  assert.equal(c.more.length, 50);
  assert.equal(c.showAll, "Show all 250 lines");
});

test("no case, a problem, or no answer yet: the question and no Replace", () => {
  const r = row({ state: "blocked", replaceable: true });
  for (const p of [null, preview({ case: null }), preview({ problem: "loadout: x" })]) {
    const c = confirmView([r], p, fullView());
    assert.equal(c.canReplace, false);
    assert.equal(c.what, null);
  }
});

test("no warning colour and no Lost case in any confirm", () => {
  const r = row({ kind: "agent", name: "a", form: "generated", state: "modified", replaceable: true });
  const text = JSON.stringify(confirmView([r], preview({}), fullView()));
  assert.ok(!/warn|danger|Lost|lose/.test(text));
});

test("the rows a Replace names: a problem's, else the view's by id, the rules file by an item it holds", async () => {
  const { replaceRows } = await import("../src/confirm-model.ts");
  const { problemsOf } = await import("../src/placed-model.ts");
  const { fullView: full, withRow: wr } = await import("./fixtures.mjs");
  let view = wr(full(), { name: "code-reviewer", harness: "codex" }, { state: "modified", replaceable: true, applies: "section" });
  view = wr(view, { name: "*", harness: "codex" }, { state: "modified", replaceable: true, applies: "section" });
  const problems = problemsOf(view);
  const agent = replaceRows(view, problems, "user", "agent:code-reviewer@codex");
  assert.deepEqual(agent.map((r) => [r.name, r.harness]), [["code-reviewer", "codex"]]);
  assert.deepEqual(replaceRows(view, [], "user", "permissions:git-reads@codex").map((r) => r.target),
                   [view.rows.find((r) => r.covers !== null).target]);
  assert.deepEqual(replaceRows(view, [], "atlas-api", "agent:code-reviewer@codex"), []);
  assert.deepEqual(replaceRows(view, [], "user", "nonsense"), []);
});
