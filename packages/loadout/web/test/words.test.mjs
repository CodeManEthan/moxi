import assert from "node:assert/strict";
import test from "node:test";

import {
  forHarnesses, harnessesLine, harnessWord, homeForm, joinAnd, KIND_WORDS, kindFolded, kindWord,
  namesLine, parentOf, sectionWord, stateWord, WORD_ORDER, wordRank,
} from "../src/words.ts";

test("the kind words, one table", () => {
  assert.deepEqual(Object.fromEntries(Object.entries(KIND_WORDS).map(([kind, w]) => [kind, [w.one, w.folded]])), {
    skill: ["skill", "Skills"], agent: ["agent", "Agents"], hook: ["hook", "Hooks"],
    permissions: ["permission rule", "Permission rules"], mcp: ["MCP server", "MCP servers"],
  });
  assert.equal(kindWord("permissions"), "permission rule");
  assert.equal(kindFolded("mcp"), "MCP servers");
  assert.equal(kindWord("plugin"), "plugin");
});

test("harnesses by the names the other pages use, joined A, B and C", () => {
  assert.equal(harnessWord("claude"), "Claude Code");
  assert.equal(harnessWord("codex"), "Codex");
  assert.equal(harnessWord("opencode"), "OpenCode");
  assert.equal(harnessWord("gemini"), "gemini");
  assert.equal(harnessesLine(["claude", "codex", "opencode"]), "Claude Code, Codex and OpenCode");
  assert.equal(harnessesLine(["codex"]), "Codex");
  assert.equal(joinAnd([]), "");
  assert.equal(joinAnd(["a", "b"]), "a and b");
});

test("the fix line's harness words: one by name, both, all three, all four", () => {
  assert.equal(forHarnesses(["codex"]), "for Codex");
  assert.equal(forHarnesses(["claude", "codex"]), "for both");
  assert.equal(forHarnesses(["claude", "codex", "opencode"]), "for all three");
  assert.equal(forHarnesses(["a", "b", "c", "d"]), "for all four");
});

test("item names past three end with how many more", () => {
  assert.equal(namesLine(["a", "b"]), "a and b");
  assert.equal(namesLine(["a", "b", "c"]), "a, b and c");
  assert.equal(namesLine(["a", "b", "c", "d", "e"]), "a, b, c and 2 more");
});

test("paths in ~ form", () => {
  assert.equal(homeForm("/home/me/my-loadout", "/home/me"), "~/my-loadout");
  assert.equal(homeForm("/home/me", "/home/me/"), "~");
  assert.equal(homeForm("/home/me2/x", "/home/me"), "/home/me2/x");
  assert.equal(homeForm("/srv/x", null), "/srv/x");
  assert.equal(homeForm("C:\\Users\\Me\\x", "c:\\users\\me"), "~\\x");
  assert.equal(parentOf("/w/app/.gitignore"), "/w/app");
});

test("a section's label: Your items, the key, or the path in ~ form", () => {
  assert.equal(sectionWord({ name: "user", where: "user", root: null }, "/home/me"), "Your items");
  assert.equal(sectionWord({ name: "atlas-api", where: "key", root: null }, "/home/me"), "atlas-api");
  assert.equal(sectionWord({ name: "/home/me/code/x", where: "path", root: "/home/me/code/x" }, "/home/me"),
               "~/code/x");
});

test("a word for every state, and their order", () => {
  const word = (state, edited = false) => stateWord({ state, edited });
  assert.equal(word("ok"), "placed");
  assert.equal(word("off"), "off");
  assert.equal(word("missing"), "not placed");
  assert.equal(word("modified"), "changed");
  assert.equal(word("blocked"), "in the way");
  assert.equal(word("blocked", true), "changed");
  assert.equal(word("stale"), "out of date");
  assert.equal(word("would-commit"), "would be committed");
  assert.equal(word("bad-source"), "can't be read");
  assert.equal(stateWord({ state: "blocked", edited: false }, { refused: true }), "not placed");
  assert.deepEqual([...WORD_ORDER], ["changed", "in the way", "not placed", "out of date",
                                     "would be committed", "can't be read"]);
  assert.ok(wordRank("strange") > wordRank("can't be read"));
});
