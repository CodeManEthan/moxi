import assert from "node:assert/strict";
import test from "node:test";

import { repoItems } from "../src/items-model.ts";
import {
  autoView, eventsOf, gitLine, openedView, RULES_NONE, RULES_ONLY_PROOF, RULES_PROOF, RULES_SAID, rulesView,
  todayLines, turnOffView,
} from "../src/opened-model.ts";
import { AT, event, HOME, offered, piece, problem, record, repo, spec, T, TODAY, UTC } from "./fixtures.mjs";

const GATE = offered({ id: "notes/secret-gate", event: "pre-commit", order: 10, doc: "leaves out a file that holds a secret" });
const RULES = offered({ id: "repo/commit-rules", doc: "checks a message's trailers" });

test("events: git's order, pieces in run order with their doc, off, undeclared, own hook and chain", () => {
  const r = repo({
    mode: "launcher",
    pieces: [piece({ id: "repo/commit-rules", settings: [{ key: "tag-pattern", values: ["ATL-[0-9]+"] }] }),
             piece({ id: "notes/secret-gate", event: "pre-commit", order: 10, enabled: false,
                     settings: [{ key: "skip", values: ["a", "b"] }] }),
             piece({ id: "inkwell/prose-check", event: "pre-commit", order: 20, declared: false })],
    events: [event({ event: "pre-commit", existing_hook: `${HOME}/code/atlas-api/.git/hooks/pre-commit.local` }),
             event({ event: "commit-msg", chain_line: "present", hook: `${HOME}/code/atlas-api/.husky/commit-msg` })],
  });
  const events = eventsOf(r, [GATE, RULES], HOME);
  assert.deepEqual(events.map((e) => e.label), ["Before a commit", "On the message"]);
  const [before, message] = events;
  assert.deepEqual(before.pieces.map((p) => [p.text, p.act]), [
    ["secret-gate is off · leaves out a file that holds a secret", "turn-on"],
    ["prose-check · no installed module declares it", null],
  ]);
  assert.deepEqual(before.pieces[0].settings, ["skip: a, b"]);
  assert.equal(before.pieces[0].tucked, false);
  assert.equal(before.own, "then its own hook, ~/code/atlas-api/.git/hooks/pre-commit.local");
  assert.equal(before.chain, null);
  assert.deepEqual(message.pieces.map((p) => [p.text, p.act, p.tucked]),
                   [["commit-rules · checks a message's trailers", "turn-off", true]]);
  assert.equal(message.chain, "run from a line in ~/code/atlas-api/.husky/commit-msg");
});

test("Today: today's open records only, newest first, each in words with its first line", () => {
  const r = repo({
    pieces: [piece({ id: "notes/secret-gate", event: "pre-commit" })],
    specs: [spec({ remote: "origin", refused_since: T("18:45") })],
    records: [record({ piece: "notes/secret-gate", text: "a token\nmore", at: AT("11:24") }),
              record({ kind: "commit", spec: "notes/snapshot", text: "drafts/private/ is never committed", at: AT("18:45") }),
              record({ kind: "push", spec: "notes/snapshot", branch: "main", at: AT("17:00") }),
              record({ piece: "notes/secret-gate", at: AT("09:00", "2026-11-17") }),
              record({ piece: "notes/secret-gate", at: "not a time" })],
  });
  assert.deepEqual(todayLines(r, TODAY, UTC).map((l) => [l.time, l.what, l.first]), [
    ["18:45", "the auto-commit was refused", "drafts/private/ is never committed"],
    ["17:00", "the push to origin failed", "it broke"],
    ["11:24", "secret-gate broke", "a token"],
  ]);
  assert.deepEqual(todayLines(r, null, UTC), []);
  const failed = repo({ specs: [spec({ error_since: T("10:00") })], records: [record({ kind: "commit", spec: "notes/snapshot" })] });
  assert.equal(todayLines(failed, TODAY, UTC)[0].what, "the auto-commit failed");
  const said = repo({ specs: [spec()], records: [record({ kind: "commit", spec: "notes/snapshot" })] });
  assert.equal(todayLines(said, TODAY, UTC)[0].what, "the auto-commit said something");
});

test("an auto-commit: state words, last commit, pushes, last pass only with no item, unsaved", () => {
  const quiet = spec({ remote: "origin", pushed: [{ branch: "main", sha: "fedcba9876543" }], saved: false });
  const view = autoView(quiet, [], TODAY, UTC);
  assert.equal(view.state, "last commit 18:30, pushed");
  assert.deepEqual(view.lines, ["last commit abc1234 at 18:30", "main pushed fedcba9",
                                "last pass committed at 18:30: committed abc1234", "ran without being saved"]);
  const refused = spec({ refused_since: T("18:45"), refused_passes: 2 });
  const r = repo({ specs: [refused] });
  const items = repoItems(r, { today: TODAY, clock: UTC });
  const view2 = autoView(refused, items, TODAY, UTC);
  assert.equal(view2.state, "refused since 18:45 · last commit 18:30");
  assert.deepEqual(view2.lines, ["last commit abc1234 at 18:30", "doesn't push"]);
  assert.equal(autoView(spec({ last_commit: null, last_commit_time: null, status: null }), [], TODAY, UTC).state, null);
});

test("Rules: the count, the lines and what follows, none set, with commit-proof alone, unreadable", () => {
  assert.deepEqual(rulesView(repo({ rules: ["Refs: ATL-<n>"], proof: true })),
                   { head: "1 line a committer is told before each commit", lines: ["Refs: ATL-<n>"],
                     after: [RULES_SAID, RULES_PROOF] });
  assert.equal(rulesView(repo({ rules: ["a", "b"] })).head, "2 lines a committer is told before each commit");
  assert.deepEqual(rulesView(repo({ rules: ["a"] })).after, [RULES_SAID]);
  assert.equal(rulesView(repo({ rules: [] })).head, RULES_NONE);
  assert.equal(rulesView(repo({ rules: [], proof: true })).head, RULES_ONLY_PROOF);
  assert.equal(rulesView(repo({ rules: null })), null);
});

test("Git: version, mode in words, pinned, root in full; a moved shim that is fine under it", () => {
  assert.equal(gitLine(repo()), `2.56.0 · hooks run from the repo's own git config · ${HOME}/code/atlas-api`);
  assert.equal(gitLine(repo({ mode: "shim", pinned: true, git_version: null })),
               `hooks run from scripts in its hooks folder · pinned · ${HOME}/code/atlas-api`);
  const r = repo({ problems: [problem({ state: "ok", code: "shim-moved", detail: "its hooks folder moved" })] });
  const view = openedView(r, [], [], { home: HOME, today: TODAY, clock: UTC });
  assert.equal(view.moved, "its hooks folder moved");
  assert.equal(view.project, null);
  assert.deepEqual(view.commands.map((c) => c.label), ["List", "Refresh", "Specs"]);
});

test("Turn off: what stops, what stays, the two commands and the note", () => {
  const r = repo({ project: "atlas-api", pieces: [piece({ id: "notes/secret-gate", event: "pre-commit" })] });
  const [line] = eventsOf(r, [GATE])[0].pieces;
  const view = turnOffView(line, r, [GATE], HOME);
  assert.equal(view.title, "Turn secret-gate off in atlas-api?");
  assert.equal(view.stops, "Stops: leaves out a file that holds a secret");
  assert.equal(view.stays, "Stays: its place and settings · Turn on brings it back as it was");
  assert.match(view.toggle.command, /disable/);
  assert.match(view.remove.command, /remove/);
});
