import assert from "node:assert/strict";
import test from "node:test";

import { commandOf, jobId, problemAct, replaceAct, shellWord } from "../src/acts-model.ts";
import {
  fromLine, fromParts, openedGlobal, openedItem, replaceArgv, rowWords, tildeCommand,
} from "../src/opened-model.ts";
import { problemsOf } from "../src/placed-model.ts";
import { ATLAS, FOLDER, fullView, HOME, item, row, USER, withRow } from "./fixtures.mjs";

const line = (opened, harness) => opened.lines.find((l) => l.harness === harness);
const brief = (l) => [l.label, l.word, l.where, l.why];

test("the opened agent: From, a line per harness, its acts, Remove", () => {
  const view = withRow(fullView(), { name: "code-reviewer", harness: "codex" },
                       { state: "modified", replaceable: true, target: `${HOME}/.codex/agents/code-reviewer.toml` });
  const opened = openedItem(view, "user", "agent", "code-reviewer");
  assert.equal(opened.from, "From ~/my-loadout/agents/code-reviewer.md");
  assert.deepEqual(brief(line(opened, "claude")), ["Claude Code", "placed", "a link at ~/.claude/agents/code-reviewer", null]);
  assert.deepEqual(brief(line(opened, "codex")), [
    "Codex", "changed by hand", "~/.codex/agents/code-reviewer.toml",
    "Codex reads its own form of an agent file, which loadout makes from the source. Someone edited that file, "
      + "so loadout leaves it alone."]);
  assert.deepEqual(brief(line(opened, "opencode")), ["OpenCode", "placed",
                                                     "~/.config/opencode/agents/code-reviewer, made from the source", null]);
  assert.equal(line(opened, "claude").toggle.label, "Turn off");
  assert.deepEqual(line(opened, "claude").toggle.act, {
    path: "api/disable", body: { id: "agent:code-reviewer@claude", user: true },
    argv: ["disable", "agent:code-reviewer@claude", "--user"] });
  assert.deepEqual(line(opened, "codex").replace.id, "agent:code-reviewer@codex");
  assert.equal(line(opened, "claude").replace, null);
  assert.deepEqual([opened.remove.label, opened.remove.note], ["Remove from your items", "its files stay in the folder"]);
  assert.deepEqual(opened.remove.act, { path: "api/remove", body: { kind: "agent", name: "code-reviewer" },
                                        argv: ["remove", "agent", "code-reviewer"] });
  assert.equal(opened.enableAll, null);
});

test("every row of the opened item's table", () => {
  const words = (over) => { const w = rowWords(row({ target: `${HOME}/t`, ...over }), HOME); return [w.word, w.where, w.why]; };
  assert.deepEqual(words({ form: "copy" }), ["placed", "a copy at ~/t", null]);
  assert.deepEqual(words({ form: "junction" }), ["placed", "a link at ~/t", null]);
  assert.deepEqual(words({ form: "entry" }), ["placed", "an entry in ~/t", null]);
  assert.deepEqual(words({ state: "missing" }), ["not placed", "~/t", "Install places it here."]);
  assert.deepEqual(words({ state: "modified", form: "entry" }), ["changed by hand", "an entry in ~/t",
    "Someone edited loadout's entry after loadout wrote it, so loadout leaves it alone."]);
  assert.deepEqual(words({ state: "blocked", edited: true, form: "copy" }),
                   ["changed by hand", "a copy at ~/t", "Claude Code's copy was edited, so loadout leaves it alone."]);
  assert.deepEqual(words({ state: "blocked", edited: true }),
                   ["changed by hand", "~/t", "A file now stands where loadout's link was, so loadout leaves it alone."]);
  assert.deepEqual(words({ state: "blocked" }),
                   ["in the way", "~/t", "Something that isn't loadout's is there, so loadout leaves it alone."]);
  assert.deepEqual(words({ state: "stale" }), ["out of date", "~/t", "Install places the new version."]);
  assert.deepEqual(words({ state: "would-commit", reason: "git would commit it" }),
                   ["would be committed", "~/t", "git would commit it"]);
  assert.deepEqual(words({ state: "bad-source", reason: "no SKILL.md" }), ["can't be read", null, "no SKILL.md"]);
});

test("off for one harness: Turn on; the prior and experimental notes", () => {
  let view = withRow(fullView(), { name: "git-reads", harness: "claude" }, { prior: true });
  view = { ...view, item_notes: [...view.item_notes, { kind: "permissions", name: "git-reads", harness: "opencode",
                                                       code: "unconfirmed" }] };
  const changelog = openedItem(view, "user", "skill", "write-changelog");
  assert.deepEqual(brief(line(changelog, "codex")), ["Codex", "off", null, "you switched it off for Codex"]);
  assert.deepEqual([line(changelog, "codex").toggle.label, line(changelog, "codex").toggle.act.path],
                   ["Turn on", "api/enable"]);
  const perms = openedItem(view, "user", "permissions", "git-reads");
  assert.deepEqual(line(perms, "claude").notes, ["It was in your file before loadout, so it stays yours"]);
  assert.deepEqual(line(perms, "opencode").notes,
                   ["Experimental: no run has confirmed yet that OpenCode reads the file as loadout writes it."]);
  assert.equal(line(perms, "codex").where, "an entry in ~/.codex/rules/loadout.rules");
});

test("the hook: From with its command in ~ form, the trust note, OpenCode takes no hooks", () => {
  const opened = openedItem(fullView(), "user", "hook", "guard-rm");
  assert.equal(opened.from, "From loadout.toml · runs python3 ~/my-loadout/hooks/guard_rm.py before each Bash call");
  assert.deepEqual(line(opened, "codex").notes,
                   ["Codex runs a new hook only once you trust it in Codex, and loadout can't tell whether you have."]);
  assert.deepEqual(brief(line(opened, "opencode")), ["OpenCode", null, null, null]);
  assert.deepEqual(line(opened, "opencode").notes, ["OpenCode takes no hooks"]);
  assert.equal(line(opened, "opencode").toggle, null);
  const hook = (event, matcher) => fromLine(item({ kind: "hook", what: { event, matcher, command: "x", url: null,
                                                                         allow: null, ask: null, deny: null } }), HOME);
  assert.equal(hook("PostToolUse", "Edit"), "From loadout.toml · runs x after each Edit call");
  assert.equal(hook("SessionStart", null), "From loadout.toml · runs x on SessionStart");
  assert.equal(tildeCommand(`${HOME}/a ${HOME}2/b ${HOME}`, HOME), `~/a ${HOME}2/b ${HOME}`);
  assert.equal(fromLine(item({ module: "demo", source: "module demo" }), HOME), "From demo");
});

test("off through @*: one Turn on for every harness, naming who comes back", () => {
  const view = fullView();
  view.items = view.items.map((i) => i.name === "tidy-notes" ? { ...i, disabled: ["*", "codex"] } : i);
  view.rows = view.rows.map((r) => r.name === "tidy-notes" ? { ...r, state: "off" } : r);
  const opened = openedItem(view, "user", "skill", "tidy-notes");
  assert.ok(opened.lines.every((l) => l.toggle === null));
  assert.equal(line(opened, "claude").why, "you switched it off for every harness");
  assert.deepEqual([opened.enableAll.label, opened.enableAll.note], ["Turn on for every harness",
                                                                     "Claude Code and OpenCode come back"]);
  assert.deepEqual(opened.enableAll.act.body, { id: "skill:tidy-notes@*", user: true });
});

test("a project section's item: acts name the project; a refused section's lines", () => {
  const view = fullView({ sections: [USER, { ...ATLAS, refused: { cause: "no-projects", line: "x", last_root: null } }] });
  view.rows = view.rows.map((r) => r.scope === "atlas-api" ? { ...r, state: "blocked", applies: null } : r);
  const opened = openedItem(view, "atlas-api", "agent", "api-lead");
  assert.equal(opened.remove.label, "Remove from atlas-api");
  assert.deepEqual(opened.remove.act.body, { kind: "agent", name: "api-lead", project: "atlas-api" });
  assert.deepEqual(line(opened, "codex").toggle.act.argv, ["disable", "agent:api-lead@codex", "--project=atlas-api"]);
  assert.equal(line(opened, "codex").word, "not placed");
  assert.ok(!JSON.stringify(opened).includes("moxi install projects"));
  assert.equal(openedItem(view, "atlas-api", "agent", "gone"), null);
});

test("the global file opened: each mode's sentence, none chosen, each form", () => {
  const view = fullView();
  view.global.files = [
    { ...view.global.files[0], form: "line" },
    { ...view.global.files[1], file: `${HOME}/.codex/AGENTS.md`, form: "block", changes: true, diff: "-a\n+b\n" },
    { ...view.global.files[2], file: `${HOME}/.config/opencode/opencode.json`, form: "instructions" },
  ];
  const g = openedGlobal(view);
  assert.equal(g.from, `From ~/my-loadout/global.md`);
  assert.equal(g.line, "Managed: your own text stays, and loadout adds its part to each file.");
  assert.deepEqual(g.modes.map((m) => [m.label, m.current, m.act?.path ?? null]),
                   [["Off", false, null], ["Managed", true, null], ["Only where absent", false, "api/global-mode"]]);
  assert.deepEqual(g.files.map((f) => [f.label, f.file, f.adds, f.next, f.diff]), [
    ["Claude Code", "~/.claude/CLAUDE.md", "one line that names global.md", "the next install changes nothing", null],
    ["Codex", "~/.codex/AGENTS.md", "a marked block", "the next install changes it", "-a\n+b\n"],
    ["OpenCode", "~/.config/opencode/opencode.json", "one instructions entry", "the next install changes nothing", null],
  ]);
  assert.deepEqual(g.turnOff, { label: "Turn off", note: "takes loadout's part out of each file and leaves your text",
                                confirm: "Turn off takes loadout's part out of each file and leaves your text" });
  const absent = openedGlobal({ ...view, global: { ...view.global, mode: "if-absent",
    files: [{ ...view.global.files[0], form: "placed" }, { ...view.global.files[1], form: "left" }] } });
  assert.equal(absent.line, "Only where absent: where a harness has no global file, loadout places global.md there.");
  assert.deepEqual(absent.files.map((f) => f.adds), ["placed where it had none",
                                                     "it has its own file, so loadout leaves it"]);
  const off = openedGlobal({ ...view, global: { ...view.global, mode: "off" } });
  assert.equal(off.line, "Off: loadout touches no global file.");
  assert.equal(off.turnOff, null);
  const none = openedGlobal({ ...view, global: { ...view.global, mode: "off", chosen: false } });
  assert.equal(none.line, "The manifest chooses no global mode yet, so it reads off and loadout touches no global file.");
  assert.ok(none.turnOff !== null);
  assert.ok(none.modes.every((m) => !m.current));
});

test("acts and the commands they become under the note", () => {
  const view = withRow(fullView(), { name: "api-lead", harness: "codex" }, { state: "blocked", replaceable: true });
  const [p] = problemsOf(view);
  const job = jobId(new Uint8Array(16).fill(171));
  assert.equal(job, "ab".repeat(16));
  assert.deepEqual(problemAct(p.act, job), {
    path: "api/replace", body: { replace: "agent:api-lead@codex", project: "atlas-api", job },
    argv: ["install", "--replace=agent:api-lead@codex", "--project=atlas-api"] });
  assert.deepEqual(replaceAct("global:global@*", { whole: true }, job).body, { replace: "global:global@*", job });
  assert.equal(commandOf(problemAct(p.act, job).argv),
               "moxi loadout install --replace=agent:code-reviewer@codex".replace("code-reviewer", "api-lead")
                 + " --project=atlas-api");
  assert.equal(shellWord("--project=/w/my app"), "'--project=/w/my app'");
  assert.equal(shellWord("it's"), "'it'\\''s'");
  assert.equal(commandOf(["install", "--project=~/x"]), "moxi loadout install --project=~/x");
  assert.ok(!FOLDER.includes(" "));
});

test("From split as drawn, and a harness line's Replace as its command", () => {
  assert.deepEqual(fromParts("From ~/my-loadout/agents/code-reviewer.md"),
                   { lead: "~/my-loadout/agents/code-reviewer.md", rest: null });
  assert.deepEqual(fromParts("From loadout.toml · runs python3 ~/h.py before each Bash call"),
                   { lead: "loadout.toml", rest: "runs python3 ~/h.py before each Bash call" });
  const view = withRow(fullView(), { name: "code-reviewer", harness: "codex" },
                       { state: "modified", replaceable: true, applies: "section" });
  const codex = openedItem(view, "user", "agent", "code-reviewer").lines.find((l) => l.harness === "codex");
  assert.deepEqual(replaceArgv(codex.replace), ["install", "--replace=agent:code-reviewer@codex", "--user"]);
  const whole = withRow(fullView(), { name: "api-lead", harness: "codex" },
                        { state: "blocked", replaceable: true, applies: "whole" });
  const lead = openedItem(whole, "atlas-api", "agent", "api-lead").lines.find((l) => l.harness === "codex");
  assert.deepEqual(replaceArgv(lead.replace), ["install", "--replace=agent:api-lead@codex"]);
});
