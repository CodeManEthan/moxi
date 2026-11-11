import assert from "node:assert/strict";
import test from "node:test";

import { filledFields, NO_PRESET, parseForm } from "../src/panel.ts";
import {
  boxedWord, changes, drawnField, EVERY_TOOL, isToolsView, NOT_IN_SET, offRows, optionsWord, OTHER_RULES, otherRules,
  panelGroups, pressTool, rulesWord, scopedTool, toolLines, toolLists, withoutRule, withTools,
} from "../src/options-model.ts";
import { BUILT_IN_TOOLS, launchRequest } from "../src/prompt-model.ts";

/** A catalog field as the page server sends one. */
const field = (name, group, sub, extra = {}) => ({
  name, type: "string", default: "", values: null, doc: `About ${name}.`, hidden: false, posture: false,
  group, sub, label: null, danger: [], carry: true, modes: ["interactive", "print"], shown: true, typed: true, ...extra,
});

const list = (name, label, extra = {}) => field(name, "tools", "Built-in", { type: "list", default: null, label, ...extra });
const CATALOG = [
  field("harness", "model", null, { type: "enum", default: "claude", values: ["claude", "codex"], modes: [] }),
  field("cwd", null, null, { type: "path", modes: [] }), field("brief", null, null, { modes: [] }),
  field("target", null, null, { modes: [] }),
  list("tools", "Built-in tool set"), list("allowed_tools", "Allowed tools", { posture: true }),
  list("disallowed_tools", "Disallowed tools", { posture: true }),
  field("native_name", "seat", "Name & notes", { label: "Native name" }),
];

/** The fields filled from a form answer whose list rows hold `rows`' items, from `[defaults]`. */
function filled(rows = {}) {
  const form = parseForm({ state: "up", detail: null, harness: "claude", refused: null,
                           rows: CATALOG.map((item) => ({
                             name: item.name, value: item.type === "list" ? null : item.default || null,
                             items: item.type === "list" ? rows[item.name] ?? [] : null,
                             source: item.name in rows ? "defaults" : "shipped", withheld: false })) });
  return { form, fields: filledFields(CATALOG, form, NO_PRESET) };
}

/** Lists holding a name the page does not list, scoped rules and MCP tools, around bare names. */
const START = {
  tools: [],
  allowed_tools: ["Read", "NotebookEdit", "Bash(git status)", "mcp__proj-a__search", "Grep"],
  disallowed_tools: ["WebSearch", "Bash(rm:*)", "mcp__proj-b__send", "Grep"],
};
const RUN = { target: "headless" };

/** A row's entries but `tool`'s bare name, each in its place. */
const others = (items, tool) => items.filter((entry) => entry !== tool);

test("the page names nine built-in tools, one line each, in its order", () => {
  assert.deepEqual([...BUILT_IN_TOOLS], ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch", "Task"]);
  assert.deepEqual(toolLines(START).map((line) => line.tool), [...BUILT_IN_TOOLS]);
});

test("a line boxes allow or deny by the bare name the rows hold, deny winning, else neither", () => {
  const boxed = Object.fromEntries(toolLines(START).map((line) => [line.tool, line.boxed]));
  assert.deepEqual(boxed, { Bash: null, Read: "allow", Edit: null, Write: null, Glob: null, Grep: "deny", WebFetch: null,
                            WebSearch: "deny", Task: null });
  // A scoped rule is not the tool's bare name.
  assert.equal(boxedWord({ tools: [], allowed_tools: ["Bash(ls)"], disallowed_tools: [] }, "Bash"), null);
});

for (const tool of BUILT_IN_TOOLS) {
  for (const word of ["allow", "deny"]) {
    test(`${word} on ${tool}'s line changes only ${tool}'s bare name, every other entry in its place`, () => {
      const after = pressTool(START, tool, word);
      const was = boxedWord(START, tool);
      // The set of built-in tools is never written by a line.
      assert.deepEqual(after.tools, START.tools);
      // Taken the bare name out of both rows, each row is as it was, in its order.
      for (const row of ["allowed_tools", "disallowed_tools"]) assert.deepEqual(others(after[row], tool), others(START[row], tool));
      const mine = word === "allow" ? "allowed_tools" : "disallowed_tools";
      const theirs = word === "allow" ? "disallowed_tools" : "allowed_tools";
      if (was === word) {
        // The boxed word takes the name out of its row and leaves the other row alone.
        assert.equal(after[mine].includes(tool), false);
        assert.deepEqual(after[theirs], START[theirs]);
      } else {
        assert.equal(boxedWord(after, tool), word);
        assert.equal(after[theirs].includes(tool), false);
        // An added name goes last; one already there stays where it was.
        if (START[mine].includes(tool)) assert.deepEqual(after[mine], START[mine]);
        else assert.deepEqual(after[mine], [...START[mine], tool]);
      }
      // The input is not changed.
      assert.deepEqual(START.allowed_tools, ["Read", "NotebookEdit", "Bash(git status)", "mcp__proj-a__search", "Grep"]);
    });
  }
}

test("pressing the boxed word takes the name out, and with both rows holding it the allow is boxed after", () => {
  assert.deepEqual(pressTool(START, "Read", "allow").allowed_tools,
                   ["NotebookEdit", "Bash(git status)", "mcp__proj-a__search", "Grep"]);
  const grep = pressTool(START, "Grep", "deny");
  assert.deepEqual(grep.disallowed_tools, ["WebSearch", "Bash(rm:*)", "mcp__proj-b__send"]);
  assert.equal(boxedWord(grep, "Grep"), "allow");
  // Pressed again, it is back where it was by its own rule: neither boxed, the other entries still there.
  const back = pressTool(pressTool(START, "Edit", "deny"), "Edit", "deny");
  assert.deepEqual(back, START);
});

test("a named tool's scoped rules are counted on its line; every other entry is under Other rules", () => {
  const bash = toolLines(START).find((line) => line.tool === "Bash");
  assert.deepEqual(bash.rules, [{ row: "allowed_tools", entry: "Bash(git status)", at: 2 },
                                { row: "disallowed_tools", entry: "Bash(rm:*)", at: 1 }]);
  assert.equal(rulesWord(bash.rules.length), "2 rules");
  assert.equal(rulesWord(1), "1 rule");
  assert.deepEqual(toolLines(START).filter((line) => line.rules.length > 0).map((line) => line.tool), ["Bash"]);
  assert.deepEqual(otherRules(START).map((rule) => [rule.row, rule.entry]), [
    ["allowed_tools", "NotebookEdit"], ["allowed_tools", "mcp__proj-a__search"], ["disallowed_tools", "mcp__proj-b__send"]]);
  assert.equal(OTHER_RULES, "Other rules");
  // None left, there is nothing to list.
  assert.deepEqual(otherRules({ tools: [], allowed_tools: ["Read", "Bash(ls)"], disallowed_tools: [] }), []);
  assert.equal(scopedTool("Bash(git status)"), "Bash");
  assert.equal(scopedTool("Bash"), null);
  assert.equal(scopedTool("NotebookEdit(x)"), null);
});

test("Remove takes out that one rule; every other entry stays in its place", () => {
  const [, rm] = toolLines(START).find((line) => line.tool === "Bash").rules;
  const after = withoutRule(START, rm);
  assert.deepEqual(after.disallowed_tools, ["WebSearch", "mcp__proj-b__send", "Grep"]);
  assert.deepEqual(after.allowed_tools, START.allowed_tools);
  const [notebook] = otherRules(START);
  assert.deepEqual(withoutRule(START, notebook).allowed_tools, ["Read", "Bash(git status)", "mcp__proj-a__search", "Grep"]);
  // A rule no longer where it was read is not taken out.
  assert.deepEqual(withoutRule(after, rm), after);
});

test("while the set of built-in tools holds names, the lines it lacks are dim", () => {
  const set = { ...START, tools: ["Bash", "Read", "mcp__proj-a__search"] };
  assert.deepEqual(toolLines(set).filter((line) => line.dim).map((line) => line.tool),
                   ["Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch", "Task"]);
  assert.deepEqual(toolLines(START).filter((line) => line.dim), []);
  assert.equal(NOT_IN_SET, "not in this launch's tools");
  // A press on a dim line still writes only the rules, never the set.
  assert.deepEqual(pressTool(set, "Edit", "deny").tools, set.tools);
});

test("the set of built-in tools is its plain list row, which reads every built-in tool while empty", () => {
  const { fields } = filled();
  const tools = fields.find((item) => item.name === "tools");
  assert.equal(drawnField(tools, []).note, EVERY_TOOL);
  assert.equal(EVERY_TOOL, "every built-in tool");
  assert.notEqual(drawnField(tools, ["Bash"]).note, EVERY_TOOL);
  assert.equal(drawnField(fields.find((item) => item.name === "allowed_tools"), []).note ?? null, null);
});

test("Tools > Built-in is the Tools view where both rows of rules are drawn", () => {
  const { fields } = filled();
  const [group] = panelGroups(fields, {}, "interactive").filter((item) => item.key === "tools");
  assert.equal(isToolsView(group.key, group.subs[0]), true);
  const without = fields.filter((item) => item.name !== "disallowed_tools");
  const [lone] = panelGroups(without, {}).filter((item) => item.key === "tools");
  assert.equal(isToolsView(lone.key, lone.subs[0]), false);
});

test("a press is written to the two rows of rules alone, and the set of built-in tools never", () => {
  const { fields } = filled({ allowed_tools: START.allowed_tools, disallowed_tools: START.disallowed_tools, tools: ["Bash"] });
  const now = toolLists(fields, {});
  assert.deepEqual(now, { ...START, tools: ["Bash"] });
  // Even a change to the set handed in is not written.
  const options = withTools({}, fields, { ...pressTool(now, "Edit", "deny"), tools: [] });
  assert.deepEqual(Object.keys(options), ["disallowed_tools"]);
  assert.deepEqual(options.disallowed_tools, [...START.disallowed_tools, "Edit"]);
  // Pressed back, the rows hold what the form is filled with, and nothing is set.
  assert.deepEqual(withTools(options, fields, pressTool(toolLists(fields, options), "Edit", "deny")), {});
});

test("a Tools press is a changed permission row: named, counted, off the usual, and sent with the rest", () => {
  // The user's defaults hold a scoped rule; the launch is on the usual while it is left alone.
  const { form, fields } = filled({ disallowed_tools: ["Bash(git push:*)"] });
  assert.equal("off_posture" in launchRequest(fields, form, { brief: "do it", cwd: "~" }, RUN), false);
  let options = withTools({}, fields, pressTool(toolLists(fields, {}), "WebSearch", "deny"));
  assert.deepEqual(changes(fields, options).map((row) => row.label), ["Disallowed tools"]);
  assert.equal(optionsWord(changes(fields, options).length), "Options · 1");
  assert.deepEqual(offRows(fields, fields, options), [{ name: "disallowed_tools", label: "Disallowed tools", source: "typed" }]);
  const body = launchRequest(fields, form, { ...options, brief: "do it", cwd: "~" }, RUN);
  assert.equal(body.off_posture, true);
  assert.deepEqual(body.values.disallowed_tools, ["Bash(git push:*)", "WebSearch"]);
  // An allow too: both rows named, in the panel's order.
  options = withTools(options, fields, pressTool(toolLists(fields, options), "Read", "allow"));
  assert.deepEqual(changes(fields, options).map((row) => row.name), ["allowed_tools", "disallowed_tools"]);
  assert.deepEqual(offRows(fields, fields, options).map((row) => row.name), ["allowed_tools", "disallowed_tools"]);
  // Taken back by their boxed words, nothing is changed and the request is on the usual.
  options = withTools(options, fields, pressTool(toolLists(fields, options), "Read", "allow"));
  options = withTools(options, fields, pressTool(toolLists(fields, options), "WebSearch", "deny"));
  assert.deepEqual(options, {});
  assert.equal("off_posture" in launchRequest(fields, form, { brief: "do it", cwd: "~" }, RUN), false);
});
