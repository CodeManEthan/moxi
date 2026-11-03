import assert from "node:assert/strict";
import test from "node:test";

import {
  addChoices, canReplace, cellLine, choiceScope, commandLine, countLine, driftView, exitLine,
  globalOffSheet, globalView, installAct, installChoices, itemAct, itemsView, jobPathOf, jobView,
  jobRefusal, parseKept, readState, replaceAct, replaceValue, saidOf, startedOf, toggleAct,
} from "../src/views.ts";

const state = (over) => ({ loading: false, gone: false, failure: null, value: null, ...over });
const NOTE = "Claude Code's home here is /a, but the last run in a terminal used /b. ...";

test("the count's line", () => {
  assert.equal(countLine(0), "Every item is ok.");
  assert.equal(countLine(1), "1 item isn't ok.");
  assert.equal(countLine(7), "7 items aren't ok.");
});

test("loading, then a count in the warning colour, or none in the accent", () => {
  assert.equal(driftView(state({ loading: true })).state, "loading");
  assert.deepEqual(driftView(state({ value: { problem: null, count: 3, note: null } })),
                   { state: "up", count: 3, line: "3 items aren't ok.", tone: "warn", detail: null,
                     note: null });
  const fine = driftView(state({ value: { problem: null, count: 0, note: null } }));
  assert.equal(fine.tone, "ok");
  assert.equal(fine.line, "Every item is ok.");
});

test("the homes' note is shown beside the count and never changes it", () => {
  const view = driftView(state({ value: { problem: null, count: 0, note: NOTE } }));
  assert.equal(view.state, "up");
  assert.equal(view.count, 0);
  assert.equal(view.note, NOTE);
});

test("a problem shows its line and no count", () => {
  const view = driftView(state({ value: { problem: "loadout: loadout.toml line 3: bad", count: 0,
                                          note: null } }));
  assert.equal(view.state, "problem");
  assert.equal(view.count, null);
  assert.equal(view.line, null);
  assert.equal(view.detail, "loadout: loadout.toml line 3: bad");
});

test("a failure keeps the last count read, and a 404 draws nothing", () => {
  const failure = { status: 500, why: "loadout's page server answered 500" };
  const kept = driftView(state({ failure, value: { problem: null, count: 2, note: null } }));
  assert.equal(kept.state, "failed");
  assert.equal(kept.count, 2);
  assert.equal(kept.detail, failure.why);
  const first = driftView(state({ failure }));
  assert.equal(first.state, "failed");
  assert.equal(first.count, null);
  assert.equal(driftView(state({ gone: true, value: { problem: null, count: 2, note: null } })).state,
               "gone");
});

// -- the panel ------------------------------------------------------------------------------

const USER = { name: "user", where: "user", root: null };
const PROJ = { name: "/w/app", where: "path", root: "/w/app" };
const KEYED = { name: "site", where: "key", root: null };
const row = (over) => ({ scope: "user", kind: "skill", name: "tidy", harness: "claude",
                         state: "ok", reason: "", target: "/h/.claude/skills/tidy", replaceable: false,
                         ...over });
const item = (over) => ({ kind: "skill", name: "tidy", scope: "user", harnesses: null,
                          source: "/f/skills/tidy", disabled: [], ...over });
const outcome = (over) => ({ rows: [], lines: [], notes: [], counts: {}, exit: 0, ...over });

test("a toggle enables or disables one id in one section", () => {
  assert.deepEqual(toggleAct("skill", "tidy", "codex", { user: true }, false),
                   { path: "api/disable", body: { id: "skill:tidy@codex", user: true } });
  assert.deepEqual(toggleAct("mcp", "docs", "*", { project: "/w/app" }, true),
                   { path: "api/enable", body: { id: "mcp:docs@*", project: "/w/app" } });
});

test("add and remove name a project section only when it isn't the user's", () => {
  assert.deepEqual(itemAct("add", "agent", "rev", { user: true }),
                   { path: "api/add", body: { kind: "agent", name: "rev" } });
  assert.deepEqual(itemAct("remove", "skill", "x", { project: "site" }),
                   { path: "api/remove", body: { kind: "skill", name: "x", project: "site" } });
});

test("the install picker gives every section, then each, and its act", () => {
  const listing = { problem: null, manifest: "/f/loadout.toml", sections: [USER, PROJ], items: [] };
  assert.deepEqual(installChoices(listing).map((c) => c.value), ["", "user", "project:/w/app"]);
  assert.deepEqual(installAct(""), { path: "api/install", body: {} });
  assert.deepEqual(installAct("user"), { path: "api/install", body: { user: true } });
  assert.deepEqual(installAct("project:/w/app"), { path: "api/install", body: { projects: ["/w/app"] } });
});

test("the add picker offers the sections, then the projects by key that have none", () => {
  const listing = { problem: null, manifest: null, sections: [USER, KEYED], items: [] };
  const projects = { projects: [{ key: "site", root: "/w/site" }, { key: "blog", root: "/w/blog" }],
                     reason: null };
  const choices = addChoices(listing, projects);
  assert.deepEqual(choices.map((c) => c.value), ["user", "project:site", "project:blog"]);
  assert.match(choices[2].label, /blog \(new section\)/);
  assert.deepEqual(choiceScope("project:blog"), { project: "blog" });
  assert.deepEqual(choiceScope("user"), { user: true });
  assert.deepEqual(addChoices(null, null).map((c) => c.value), ["user"]);
});

test("items by section with each harness's state, the switched-off harness among them", () => {
  const listing = { problem: null, manifest: null, sections: [USER, PROJ], items: [
    item({ disabled: ["codex"] }),
    item({ scope: "/w/app" }),
    item({ name: "ext/helper", source: "module ext" }),
  ] };
  const status = { problem: null, ...outcome({ rows: [
    row({ state: "missing" }),
    row({ harness: "codex", state: "off", reason: "disabled", target: null }),
    row({ scope: "/w/app", state: "blocked", reason: "a file is there", target: "/w/app/.claude/skills/tidy",
          replaceable: true }),
    row({ scope: "/w/app", harness: "codex", target: "/w/app/.agents/skills/tidy" }),
    row({ name: "ext/helper", harness: "codex" }),
    row({ scope: null, kind: null, name: "/h/.gitignore", harness: null, state: "stale" }),
    row({ kind: "global", name: "global", harness: "*", state: "bad-source", target: null }),
  ] }) };
  const view = itemsView(listing, status);
  assert.deepEqual(view.sections.map((s) => s.key), ["user", "/w/app"]);
  const [own, module] = view.sections[0].groups;
  assert.equal(own.module, null);
  assert.equal(module.module, "ext");
  const tidy = own.items[0];
  assert.deepEqual(tidy.cells.map((c) => [c.harness, c.state, c.on]),
                   [["claude", "missing", true], ["codex", "off", false]]);
  assert.deepEqual(tidy.cells[1].toggle, { path: "api/enable", body: { id: "skill:tidy@codex", user: true } });
  assert.deepEqual(tidy.remove, { path: "api/remove", body: { kind: "skill", name: "tidy" } });
  // A module's skill with rows for one harness has that harness only.
  assert.deepEqual(module.items[0].cells.map((c) => c.harness), ["codex"]);
  const app = view.sections[1].groups[0].items[0];
  assert.deepEqual(app.cells[0].toggle, { path: "api/disable",
                                          body: { id: "skill:tidy@claude", project: "/w/app" } });
  assert.equal(canReplace(app.cells[0].row), true);
  assert.deepEqual(view.others.map((r) => r.name), ["/h/.gitignore", "global"]);
  assert.equal(cellLine(app.cells[0]), "blocked: a file is there");
});

test("an item off for every harness turns on only with @*", () => {
  const listing = { problem: null, manifest: null, sections: [USER], items: [
    item({ harnesses: ["claude", "opencode"], disabled: ["*"] })] };
  const view = itemsView(listing, null);
  const tidy = view.sections[0].groups[0].items[0];
  assert.equal(tidy.allOff, true);
  assert.deepEqual(tidy.cells.map((c) => [c.harness, c.on, c.toggle]),
                   [["claude", false, null], ["opencode", false, null]]);
  assert.deepEqual(tidy.enableAll, { path: "api/enable", body: { id: "skill:tidy@*", user: true } });
  assert.equal(cellLine(tidy.cells[0]), "off");
});

test("replace names the item for its harness, or the target, in the row's section", () => {
  const blocked = row({ scope: "/w/app", state: "blocked", target: "/w/app/.claude/skills/tidy",
                        replaceable: true });
  assert.equal(replaceValue(blocked), "skill:tidy@claude");
  const act = replaceAct(blocked);
  assert.deepEqual(act.body, { replace: ["skill:tidy@claude"], projects: ["/w/app"] });
  assert.equal(act.path, "api/replace");
  assert.match(act.sheet[0], /--replace skill:tidy@claude/);
  assert.match(act.sheet[1], /\/w\/app\/\.claude\/skills\/tidy/);
  assert.equal(act.sheet.length, 2);
  const ignore = row({ scope: null, kind: null, name: null, harness: null, state: "modified",
                       target: "/h/.gitignore" });
  assert.deepEqual(replaceAct(ignore).body, { replace: ["/h/.gitignore"] });
  assert.deepEqual(replaceAct(row({ state: "modified" })).body, { replace: ["skill:tidy@claude"], user: true });
  assert.equal(canReplace(row({ state: "missing" })), false);
  assert.equal(canReplace(row({ state: "would-commit" })), false);
});

test("Replace is offered where status says --replace places over the row, and only there", () => {
  // A blocked row --replace doesn't fix (another module's placement, a CLI not found).
  assert.equal(canReplace(row({ state: "blocked", reason: "it is ext's placement" })), false);
  // An ignore file edited by hand: restoring it is the fix, not --replace.
  assert.equal(canReplace(row({ kind: "ignore", name: "/w/app/.gitignore", harness: "*",
                                state: "modified", target: "/w/app/.gitignore" })), false);
  assert.equal(canReplace(row({ state: "modified", replaceable: true })), true);
  // A project's copy that git tracks: replaced, and the sheet says git shows it deleted.
  const tracked = row({ scope: "/w/app", harness: "codex", state: "would-commit",
                        reason: "git tracks it", target: "/w/app/.agents/skills/tidy",
                        replaceable: true });
  assert.equal(canReplace(tracked), true);
  const act = replaceAct(tracked);
  assert.deepEqual(act.body, { replace: ["skill:tidy@codex"], projects: ["/w/app"] });
  assert.match(act.sheet[1], /\/w\/app\/\.agents\/skills\/tidy now moves to Moxi's displaced folder/);
  assert.match(act.sheet[2], /git tracks the project's copy there.*git will show it deleted, .*until you commit that/);
});

test("the global view offers the modes not in force, and off unless chosen off", () => {
  const unchosen = globalView({ problem: null, mode: "off", chosen: false, files: [] });
  assert.deepEqual(unchosen.modes.map((m) => m.mode), ["managed", "if-absent"]);
  assert.equal(unchosen.canTurnOff, true);
  assert.match(unchosen.line, /chooses no global mode/);
  const managed = globalView({ problem: null, mode: "managed", chosen: true, files: [] });
  assert.deepEqual(managed.modes.map((m) => m.act), [{ path: "api/global-mode", body: { mode: "if-absent" } }]);
  assert.equal(globalView({ problem: null, mode: "off", chosen: true, files: [] }).canTurnOff, false);
});

test("the global off sheet says what leaves each file", () => {
  const lines = globalOffSheet({ problem: null, mode: "managed", chosen: true, files: [
    { file: "/h/.claude/CLAUDE.md", changes: false, diff: "", placed: false, state: null },
    { file: "/h/.codex/AGENTS.md", changes: false, diff: "", placed: true, state: "ok" }] });
  assert.match(lines[0], /global off/);
  assert.deepEqual(lines.slice(1), ["/h/.claude/CLAUDE.md: loadout's part comes out.",
                                    "/h/.codex/AGENTS.md: the copy loadout placed is removed."]);
});

test("an act's answer: a job to follow, or the line it was refused with", () => {
  assert.deepEqual(startedOf(202, { status: "/api/job?job=abc_D-1" }), { kind: "job", path: "api/job?job=abc_D-1" });
  assert.equal(startedOf(202, { status: "/api/other" }).kind, "refused");
  assert.deepEqual(startedOf(422, { refused: "loadout: no" }), { kind: "refused", text: "loadout: no" });
  assert.deepEqual(startedOf(400, { error: "unknown key: x" }), { kind: "refused", text: "unknown key: x" });
  assert.deepEqual(startedOf(0, null), { kind: "refused", text: "no answer from the shell" });
  assert.deepEqual(startedOf(500, null), { kind: "refused", text: "loadout's page server answered 500" });
  assert.equal(jobPathOf("api/job?job=x&y=1"), null);
  assert.equal(saidOf({ conflict: "busy" }), "busy");
});

test("a job running, done by its exit code, interrupted, or lost", () => {
  const job = (over) => ({ job: "j1", argv: ["add", "skill", "tidy"], state: "running", exit: null,
                           outcome: null, lines: [], ...over });
  const at = (value, over) => ({ loading: false, gone: false, failure: null, value, ...over });
  assert.equal(jobView(at(null)).title, "Starting…");
  const running = jobView(at(job()));
  assert.equal(running.state, "running");
  assert.equal(running.command, "moxi loadout add skill tidy");
  const done = jobView(at(job({ state: "done", exit: 0, outcome: outcome({
    rows: [row(), row({ harness: "codex", state: "blocked", reason: "a file" })],
    lines: ["placed skill tidy for claude"], notes: ["a note"] }) })));
  assert.equal(done.tone, "ok");
  assert.equal(done.title, "Done.");
  assert.deepEqual(done.lines, ["placed skill tidy for claude", "a note"]);
  assert.deepEqual(done.rows.map((r) => r.state), ["blocked"]);
  const refused = jobView(at(job({ state: "done", exit: 3, lines: ["Claude Code's home here is /a"] })));
  assert.equal(refused.tone, "warn");
  assert.deepEqual(refused.lines, ["Claude Code's home here is /a"]);
  assert.match(exitLine(75), /Run it again/);
  const cut = jobView(at(job({ state: "interrupted", lines: ["the job was interrupted; run it again"] })));
  assert.equal(cut.state, "interrupted");
  assert.deepEqual(cut.lines, ["the job was interrupted; run it again"]);
  const failure = { status: 0, why: "no answer from the shell" };
  assert.equal(jobView(at(job(), { failure }), true).state, "failed");
  assert.equal(jobView(at(job(), { failure })).state, "running");
  assert.equal(commandLine(["install", "--json"]), "moxi loadout install");
});

test("a refused job read ends the job with the refusal's own line", () => {
  const job = { job: "j1", argv: ["install"], state: "running", exit: null, outcome: null, lines: [] };
  const at = (value, failure) => ({ loading: false, gone: false, failure, value });
  const gone = { status: 422, why: "loadout's page server answered 422",
                 said: "there is no page job j1; a job's record is kept for a day" };
  assert.equal(jobRefusal(gone), "there is no page job j1; a job's record is kept for a day");
  assert.equal(jobRefusal({ ...gone, said: null }), "loadout's page server answered 422");
  for (const status of [0, 404, 500, 503]) {
    assert.equal(jobRefusal({ status, why: "w", said: null }), null);
  }
  assert.equal(jobRefusal(null), null);
  for (const value of [null, job]) {
    const view = jobView(at(value, gone));
    assert.equal(view.state, "failed");
    assert.equal(view.title, "there is no page job j1; a job's record is kept for a day");
    assert.doesNotMatch(view.title, /may still be running/);
  }
  // A job read done before keeps its outcome.
  assert.equal(jobView(at({ ...job, state: "done", exit: 0 }, gone)).state, "done");
});

test("the job a tab keeps is its status path and label, or nothing", () => {
  assert.deepEqual(parseKept(JSON.stringify({ path: "api/job?job=a_B-1", label: "Install" })),
                   { path: "api/job?job=a_B-1", label: "Install" });
  assert.deepEqual(parseKept(JSON.stringify({ path: "/api/job?job=a1" })),
                   { path: "api/job?job=a1", label: "A change" });
  for (const raw of [null, "", "{", "null", "[]", JSON.stringify({ path: "api/other" }),
                     JSON.stringify({ path: "https://elsewhere/api/job?job=x" })]) {
    assert.equal(parseKept(raw), null, raw);
  }
});

test("a read's state", () => {
  const at = (value, over) => ({ loading: false, gone: false, failure: null, value, ...over });
  assert.equal(readState(at(null, { loading: true })).state, "loading");
  assert.deepEqual(readState(at({ problem: "loadout: bad" })), { state: "problem", detail: "loadout: bad" });
  assert.equal(readState(at({ problem: null })).state, "up");
  assert.equal(readState(at({ problem: null }, { failure: { status: 500, why: "w" } })).state, "failed");
});
