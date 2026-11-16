import assert from "node:assert/strict";
import test from "node:test";

import { parseJob } from "../src/feed.ts";
import { followLine, jobLine, keptLines, showView } from "../src/job-model.ts";
import { ATLAS, fullView, HOME, USER } from "./fixtures.mjs";

const LINE = "loadout: section atlas-api is named by a project key; install projects, or moxi install projects";
const plain = (over) => ({ scope: "user", kind: "skill", name: "tidy-notes", harness: "claude", state: "ok",
                            reason: "", target: null, replaceable: false, ...over });
const job = (over, outcome = {}) => ({
  job: "j1", argv: ["install", "--user", "--json"], state: "done", exit: 0, lines: [],
  outcome: { rows: [], kept: [], lines: [], notes: [], counts: {}, exit: 0, ...outcome }, ...over });

const small = () => {
  const view = fullView({ sections: [USER, { ...ATLAS, refused: { cause: "no-projects", line: LINE, last_root: null } }] });
  view.rows = view.rows.map((r) => r.scope === "atlas-api" ? { ...r, state: "blocked", reason: LINE, applies: null } : r);
  return view;
};

test("a job's outcome reads kept, and an outcome recorded without it reads none", () => {
  const raw = job({});
  delete raw.outcome.kept;
  assert.deepEqual(parseJob(raw).outcome.kept, []);
  const k = { target: "/t", copy: "/c", how: "copied", id: null, back: "cp '/c' '/t'" };
  assert.deepEqual(parseJob(job({}, { kept: [k] })).outcome.kept, [k]);
  assert.equal(parseJob(job({}, { kept: [{ ...k, back: null }] })), null);
});

test("the line under the act: the command and how it ended", () => {
  const view = fullView();
  assert.deepEqual(jobLine(job({}), view), { command: "moxi loadout install --user", ended: "done",
                                             line: "moxi loadout install --user: done", hasMore: false });
  assert.equal(jobLine(job({ state: "running", exit: null, outcome: null }), view).ended, "running");
  assert.equal(jobLine(job({ exit: 3 }, { rows: [plain({ state: "blocked" })] }), view).ended, "refused");
  assert.equal(jobLine(job({ exit: 1, lines: ["boom"] }), view).ended, "failed");
  assert.equal(jobLine(job({ state: "interrupted", exit: null, outcome: null }), view).ended, "failed");
  assert.equal(jobLine(job({ lines: ["said"] }), view).hasMore, true);
});

test("done, but a section that still can't be placed, when that is all that isn't fine", () => {
  const view = small();
  const ended = job({ exit: 3 }, { rows: [plain({ scope: "atlas-api", kind: "section", name: "atlas-api",
                                                  harness: "*", state: "blocked", reason: LINE }),
                                          plain({ scope: "atlas-api", kind: "agent", name: "api-lead",
                                                  state: "blocked", reason: LINE })] });
  assert.equal(jobLine(ended, view).ended, "done, but the section atlas-api still can't be placed");
  const more = job({ exit: 3 }, { rows: [plain({ scope: "atlas-api", kind: "agent", name: "api-lead", state: "blocked" }),
                                         plain({ state: "blocked" })] });
  assert.equal(jobLine(more, view).ended, "refused");
});

test("an act on one item that did its work reads done, naming what else still needs him", () => {
  const view = fullView();
  const edited = plain({ kind: "agent", name: "code-reviewer", harness: "codex", state: "modified" });
  const replace = job({ argv: ["install", "--replace=hook:guard-rm@claude", "--user", "--json"], exit: 3 },
                      { rows: [plain({ kind: "hook", name: "guard-rm" }), edited] });
  assert.equal(jobLine(replace, view).line,
               "moxi loadout install --replace=hook:guard-rm@claude --user: done, but the agent code-reviewer still needs you");
  assert.equal(jobLine(replace, view).hasMore, true);
  const off = job({ argv: ["disable", "skill:tidy-notes@codex", "--user"], exit: 3 },
                  { rows: [plain({ harness: "codex", state: "off" }), edited,
                           plain({ name: "release-checklist", state: "missing" })] });
  assert.equal(jobLine(off, view).ended, "done, but the agent code-reviewer and the skill release-checklist still need you");
  // Its own row refused: refused.
  const own = job({ argv: ["install", "--replace=agent:code-reviewer@codex", "--user"], exit: 3 }, { rows: [edited] });
  assert.equal(jobLine(own, view).ended, "refused");
  const every = job({ argv: ["enable", "agent:code-reviewer@*", "--user"], exit: 3 }, { rows: [edited] });
  assert.equal(jobLine(every, view).ended, "refused");
  // The same item in another section is not the act's own.
  const elsewhere = job({ argv: ["disable", "agent:code-reviewer@codex", "--project=atlas-api"], exit: 3 },
                        { rows: [edited] });
  assert.equal(jobLine(elsewhere, view).ended, "done, but the agent code-reviewer still needs you");
  // An Install names no item: refused as before.
  assert.equal(jobLine(job({ exit: 3 }, { rows: [edited] }), view).ended, "refused");
  // A usage refusal is no run's.
  assert.equal(jobLine(job({ argv: ["disable", "skill:tidy-notes@codex"], exit: 2 }), view).ended, "refused");
});

test("Show: rows in Placed's words, a section by its cause, never the install hint", () => {
  const view = small();
  const ended = job({ exit: 3, lines: [`refused: ${LINE}`] }, {
    rows: [plain({ scope: "atlas-api", kind: "section", name: "atlas-api", harness: "*", state: "blocked", reason: LINE }),
           plain({ kind: "agent", name: "code-reviewer", harness: "codex", state: "modified" })],
    lines: ["kept nothing"], notes: [LINE] });
  const show = showView(ended, view);
  assert.deepEqual(show.problems.map((p) => [p.sentence, p.word]), [
    ["The agent code-reviewer was changed by hand", "changed"],
    ["The section atlas-api is named by a project key, so nothing in it is placed", "not placed"],
  ]);
  assert.deepEqual(show.lines, ["The section atlas-api is named by a project key, so nothing in it is placed",
                                "kept nothing",
                                "The section atlas-api is named by a project key, so nothing in it is placed"]);
  assert.ok(!JSON.stringify(show).includes("moxi install projects"));
});

test("what a job kept: each how, its words, back as the verb wrote it", () => {
  const lines = keptLines([
    { target: `${HOME}/.codex/agents/rev.toml`, copy: `${HOME}/d/1/codex/rev.toml`, how: "copied",
      id: "agent:rev@codex", back: "cp '/c1' '/t1'" },
    { target: `${HOME}/.claude/settings.json`, copy: `${HOME}/d/1/claude/settings.json`, how: "copied", id: null,
      back: "cp '/c2' '/t2'" },
    { target: `${HOME}/.codex/skills/tidy`, copy: `${HOME}/d/1/codex/tidy`, how: "replaced", id: "skill:tidy@codex",
      back: "moxi loadout disable skill:tidy@codex --user && mv '/c3' '/t3'" },
    { target: `${HOME}/.claude/CLAUDE.md`, copy: `${HOME}/d/1/claude/CLAUDE.md`, how: "replaced",
      id: "global:global@claude", back: "moxi loadout global remove && mv '/c4' '/t4'" },
    { target: `${HOME}/.codex/skills/old`, copy: `${HOME}/d/1/codex/old`, how: "moved", id: null,
      back: "move /y \"C:\\c5\" \"C:\\t5\"" },
  ], HOME);
  assert.deepEqual(lines.map((l) => [l.text, l.before, l.back, l.after]), [
    ["~/.codex/agents/rev.toml as it was is kept at ~/d/1/codex/rev.toml", null, "cp '/c1' '/t1'",
     "puts it back as it was: loadout then reads it changed and leaves it"],
    ["~/.claude/settings.json as it was is kept at ~/d/1/claude/settings.json", null, "cp '/c2' '/t2'",
     "puts it back as it was: the whole file, so later changes to it go too"],
    ["~/.codex/skills/tidy as it was is kept at ~/d/1/codex/tidy", "This turns tidy off for Codex first, then",
     "moxi loadout disable skill:tidy@codex --user && mv '/c3' '/t3'",
     "puts it back; tidy stays off for Codex until you turn it on"],
    ["~/.claude/CLAUDE.md as it was is kept at ~/d/1/claude/CLAUDE.md", "This turns the global file off first, then",
     "moxi loadout global remove && mv '/c4' '/t4'",
     "puts it back; loadout leaves the global file off until you choose a mode again"],
    ["~/.codex/skills/old as it was is kept at ~/d/1/codex/old", null, "move /y \"C:\\c5\" \"C:\\t5\"", "puts it back"],
  ]);
  assert.equal(lines[0].copy, `${HOME}/d/1/codex/rev.toml`);
});

test("the line for the job the page follows: running, ended, refused or lost", () => {
  const command = "moxi loadout disable skill:tidy-notes@codex --user";
  assert.equal(followLine(null, command, fullView()).line, `${command}: running`);
  assert.equal(followLine(job({ state: "running", exit: null, outcome: null }), command, fullView()).line,
               "moxi loadout install --user: running");
  assert.equal(followLine(job({}), command, fullView()).line, "moxi loadout install --user: done");
  // A job that names no argv takes the act's command.
  assert.equal(followLine(job({ argv: null }), command, fullView()).line, `${command}: done`);
  assert.equal(followLine(null, command, fullView(), { refused: "no such job" }).line, `${command}: no such job`);
  assert.equal(followLine(job({ state: "running", exit: null, outcome: null }), command, fullView(), { lost: "no answer" }).line,
               `${command}: can't be read now (no answer); it may still be running`);
  // A job read as ended keeps how it ended, whatever stopped the reads after.
  assert.equal(followLine(job({}), command, fullView(), { lost: "no answer" }).ended, "done");
});
