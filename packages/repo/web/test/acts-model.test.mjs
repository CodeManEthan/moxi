import assert from "node:assert/strict";
import test from "node:test";

import {
  addRows, answerView, confirmView, folderLine, placeWords, planFailWords, transportView, writesOf,
} from "../src/acts-model.ts";
import { answer, HOME, offered, piece, plan, planned, repo, write } from "./fixtures.mjs";

const PROOF = offered({ id: "repo/commit-proof", order: 60, settings: ["salt"],
                        doc: "refuses a commit whose message lacks the line moxi repo rules prints" });
const RULES = offered({ id: "repo/commit-rules", order: 50, settings: ["tag-pattern"], doc: "checks a message's trailers" });
const GATE = offered({ id: "notes/secret-gate", event: "pre-commit", order: 10,
                       doc: "leaves out or refuses a file that holds a secret" });
const OFFERED = [PROOF, RULES, GATE];

/** The config items of a new registration of `id` on `event`. */
const registration = (id, event, { fail = "closed", order = "50" } = {}) =>
  [["event", event], ["target", "x"], ["fail", fail], ["order", order], ["enabled", "true"]]
    .map(([v, to]) => write("config", `moxi-repo.piece.${id}.${v}`, null, to, { piece: id, event }));
const entries = (event, from = null) =>
  ["command", "event"].map((v) => write("config", `hook.moxi-repo-${event}.${v}`, from, from === null ? "run" : null,
                                        { event }));
const context = (act, picked = [], over = {}) => ({ act, repo: "atlas-api", picked, offered: OFFERED, home: HOME, ...over });

test("place words: first, before and after, by order then id, with an earlier order", () => {
  const here = [piece({ id: "repo/commit-rules", order: 50 }), piece({ id: "a/late", order: 90 }),
                piece({ id: "notes/secret-gate", event: "pre-commit", order: 10 })];
  assert.equal(placeWords({ id: "repo/commit-proof", event: "commit-msg", order: 60 }, here), "after commit-rules");
  assert.equal(placeWords({ id: "x/early", event: "commit-msg", order: 5 }, here), "before commit-rules");
  assert.equal(placeWords({ id: "x/tie", event: "commit-msg", order: 50 }, here), "after commit-rules");
  assert.equal(placeWords({ id: "a/tie", event: "commit-msg", order: 50 }, here), "before commit-rules");
  assert.equal(placeWords({ id: "x/push", event: "pre-push", order: 10 }, here), "first");
});

test("Add a piece: each offered piece not here, in run order, with its place and Picked", () => {
  const rows = addRows(repo({ pieces: [piece({ id: "repo/commit-rules" })] }), OFFERED, new Set(["repo/commit-proof"]));
  assert.deepEqual(rows.map((row) => [row.name, row.where, row.doc, row.button]), [
    ["secret-gate", "before a commit, first", GATE.doc, "Add"],
    ["commit-proof", "on the message, after commit-rules", PROOF.doc, "Picked"],
  ]);
});

test("Add a piece's confirm: the piece, its salt and its entries; then its doc", () => {
  const view = confirmView(plan({
    pieces: [planned({ id: "repo/commit-proof", seeds: ["salt"] })],
    writes: [...registration("repo/commit-proof", "commit-msg", { order: "60" }),
             write("config", "moxi-repo.piece.repo/commit-proof.salt", null, null, { piece: "repo/commit-proof", seed: true }),
             ...entries("commit-msg")],
  }), context("add", ["repo/commit-proof"]));
  assert.equal(view.title, "Add commit-proof to atlas-api?");
  assert.deepEqual(view.writes, ["into this repo's git config · commit-proof, new, and its salt · the hook entries "
                                 + "for on the message"]);
  assert.deepEqual(view.then, [PROOF.doc]);
  assert.equal(view.leaves, "the repo's files and its other hooks as they are · taking it out again is a terminal command");
  assert.deepEqual([view.go, view.cancel, view.digest, view.refusal], ["Add commit-proof", "Cancel", "d1", null]);
});

test("an implied piece names its key, and a seed from his settings shows its value", () => {
  const view = confirmView(plan({
    pieces: [planned({ id: "notes/secret-gate", event: "pre-commit" }),
             planned({ id: "repo/commit-rules", implied_by: "commit_rules.everywhere", seeds: ["tag-pattern"] })],
    writes: [...registration("notes/secret-gate", "pre-commit"), ...registration("repo/commit-rules", "commit-msg"),
             write("config", "moxi-repo.piece.repo/commit-rules.tag-pattern", null, "\\bATL-[0-9]+\\b",
                   { piece: "repo/commit-rules", seed: true }),
             ...entries("pre-commit"), ...entries("commit-msg")],
  }), context("add", ["notes/secret-gate"]));
  assert.equal(view.title, "Add secret-gate to atlas-api?");
  assert.deepEqual(view.writes, ["into this repo's git config · secret-gate, new · commit-rules, new "
    + "(commit_rules.everywhere is on), and its tag-pattern set to \\bATL-[0-9]+\\b · the hook entries for before a "
    + "commit and on the message"]);
  assert.deepEqual(view.then, [`secret-gate: ${GATE.doc}`, `commit-rules: ${RULES.doc}`]);
});

test("a first add in shim mode: its mode, the scripts, his hook moved aside, the list", () => {
  const view = confirmView(plan({
    mode: "shim", indexed: false, own_hooks: ["pre-commit"],
    pieces: [planned({ id: "notes/secret-gate", event: "pre-commit" })],
    writes: [write("index", null, null, `${HOME}/code/atlas-api`),
             write("config", "moxi-repo.mode-set", null, "auto"), write("config", "moxi-repo.mode", null, "shim"),
             ...registration("notes/secret-gate", "pre-commit"),
             write("hooks", "hook", "/r/.git/hooks/pre-commit", "/r/.git/hooks/pre-commit.local", { event: "pre-commit" }),
             write("hooks", "shim", null, "/r/.git/hooks/pre-commit", { event: "pre-commit" }),
             write("config", "moxi-repo.moved", null, "pre-commit")],
  }), context("add", ["notes/secret-gate"]));
  assert.deepEqual(view.writes, [
    "into this repo's git config · secret-gate, new · hooks run from scripts in its hooks folder, as git's version "
      + "picks · notes its own hooks moved aside (pre-commit)",
    "in its hooks folder · scripts for before a commit · moves its own pre-commit hook to pre-commit.local, where it "
      + "still runs after the pieces",
    "into repo's list",
  ]);
  assert.equal(view.leaves, "the repo's files · taking it out again is a terminal command");
});

test("launcher mode: the launcher, and the chain line he adds under Then", () => {
  const view = confirmView(plan({
    mode: "shim",
    pieces: [planned({ id: "notes/secret-gate", event: "pre-commit" })],
    writes: [...registration("notes/secret-gate", "pre-commit"),
             write("hooks", "launcher", null, "/r/.git/moxi-repo/launcher"),
             write("chain", null, `${HOME}/code/atlas-api/.husky/pre-commit`, ". /r/.git/moxi-repo/launcher",
                   { event: "pre-commit" })],
  }), context("add", ["notes/secret-gate"]));
  assert.deepEqual(view.writes, ["into this repo's git config · secret-gate, new", "in its hooks folder · a launcher"]);
  assert.deepEqual(view.chain, [{ text: "the pieces run once this line is in ~/code/atlas-api/.husky/pre-commit:",
                                  line: ". /r/.git/moxi-repo/launcher" }]);
});

test("a mode change takes the entries away and says from what", () => {
  const view = confirmView(plan({
    mode: "shim", mode_change: { from: "config", to: "shim" },
    pieces: [planned({ id: "notes/secret-gate", event: "pre-commit" })],
    writes: [write("config", "moxi-repo.mode", "config", "shim"), ...registration("notes/secret-gate", "pre-commit"),
             ...entries("pre-commit", "run"), write("hooks", "shim", null, "/r/.git/hooks/pre-commit", { event: "pre-commit" }),
             write("hooks", "launcher", "/r/.git/moxi-repo/launcher", null)],
  }), context("add", ["notes/secret-gate"]));
  assert.deepEqual(view.writes, [
    "into this repo's git config · secret-gate, new · takes away the hook entries for before a commit · hooks run "
      + "from scripts in its hooks folder now, not from the repo's own git config",
    "in its hooks folder · scripts for before a commit · takes away its launcher",
  ]);
});

test("Turn on: the piece turned on and its entries back, then its doc", () => {
  const view = confirmView(plan({
    pieces: [planned({ id: "notes/secret-gate", event: "pre-commit", new: false })],
    writes: [write("config", "moxi-repo.piece.notes/secret-gate.enabled", "false", "true",
                   { piece: "notes/secret-gate", event: "pre-commit" }), ...entries("pre-commit")],
  }), context("enable", ["notes/secret-gate"], { repo: "kestrel-web" }));
  assert.equal(view.title, "Turn secret-gate on in kestrel-web?");
  assert.deepEqual(view.writes, ["into this repo's git config · secret-gate turned on · the hook entries for before a commit"]);
  assert.deepEqual(view.then, [GATE.doc]);
  assert.deepEqual([view.leaves, view.go], ["the repo's files and its other hooks as they are", "Turn it on"]);
});

test("Refresh: a hand edit put back is drawn with both values; nothing left reads so", () => {
  const view = confirmView(plan({
    pieces: [planned({ id: "repo/commit-rules", new: false })],
    writes: [write("config", "moxi-repo.piece.repo/commit-rules.fail", "open", "closed", { piece: "repo/commit-rules" }),
             write("config", "moxi-repo.piece.repo/commit-rules.target", null, "x", { piece: "repo/commit-rules" })],
  }), context("refresh"));
  assert.equal(view.title, "Refresh atlas-api?");
  assert.deepEqual(view.writes, ["into this repo's git config · commit-rules, rewritten · puts commit-rules's fail back "
    + "to closed (it reads open here) · puts commit-rules's target back to x (it isn't set here)"]);
  assert.deepEqual([view.then, view.leaves, view.go], [[], "the repo's files and its other hooks as they are", "Refresh"]);
  assert.deepEqual(confirmView(plan(), context("refresh")).writes, ["nothing; it is already so"]);
});

test("a merge in progress with commit-proof planned says its commit is refused", () => {
  const view = confirmView(plan({ in_progress: "merge", pieces: [planned({ id: "repo/commit-proof" })],
                                  writes: registration("repo/commit-proof", "commit-msg") }),
                           context("add", ["repo/commit-proof"]));
  assert.deepEqual(view.then, [PROOF.doc, "A merge is in progress here: its commit is refused until its message "
                                          + "carries the Commit-Check line."]);
  const without = confirmView(plan({ in_progress: "rebase", pieces: [planned({ id: "notes/secret-gate" })],
                                     writes: registration("notes/secret-gate", "pre-commit") }),
                              context("add", ["notes/secret-gate"]));
  assert.deepEqual(without.then, [GATE.doc]);
});

test("Add a repo: its folder line, the list alone, pieces counted, a hook it has", () => {
  const listOnly = confirmView(plan({ indexed: false, writes: [write("index", null, null, "/r")] }),
                               context("add-repo"));
  assert.deepEqual([listOnly.title, listOnly.writes, listOnly.leaves, listOnly.go],
                   [null, ["nothing in the repo; it joins this list"], "its files alone", "Add to the list"]);
  const two = confirmView(plan({
    indexed: false, own_hooks: ["pre-commit"],
    pieces: [planned({ id: "notes/secret-gate", event: "pre-commit" }), planned({ id: "repo/commit-rules" })],
    writes: [write("index", null, null, "/r"), ...registration("notes/secret-gate", "pre-commit"),
             ...registration("repo/commit-rules", "commit-msg"), ...entries("pre-commit"), ...entries("commit-msg")],
  }), context("add-repo", ["notes/secret-gate", "repo/commit-rules"]));
  assert.deepEqual(two.writes, ["into that repo's git config · secret-gate, new · commit-rules, new · the hook entries "
                                + "for before a commit and on the message", "into repo's list"]);
  assert.deepEqual([two.leaves, two.go],
                   ["its files alone · a hook it already has keeps running, after the pieces", "Add 2 pieces"]);
  assert.equal(folderLine(plan()), "a git repo · git 2.56.0");
  assert.equal(folderLine(plan({ is_repo: false, refusal: { code: "not-a-repo", text: "/r is not in a git repo" } })),
               "/r is not in a git repo.");
});

test("a plan that refuses draws its words and no button", () => {
  const text = `This page runs in a copy of Moxi whose home is ${HOME}/tmp/look. It changes no repo outside that `
    + "folder, and /r is outside it.";
  const view = confirmView(plan({ refusal: { code: "outside-copy", text }, digest: null }), context("refresh"));
  assert.deepEqual([view.refusal, view.writes, view.go], [text, [], null]);
  assert.equal(planFailWords({ detail: "repo's page server hit an error; its log has the details (OSError)" }),
               "repo's page server hit an error; its log has the details (OSError).");
  assert.equal(planFailWords({ status: 0 }), "The plan can't be read: no answer from the shell.");
});

test("every item of writes is drawn, an unknown place included", () => {
  const writes = [write("index", null, `${HOME}/old`, "/r"), write("config", "moxi-repo.mode-set", "auto", "shim"),
                  write("config", "moxi-repo.moved", "pre-commit", null), write("config", "core.other", "a", "b"),
                  write("hooks", "shim", "/r/.git/hooks/post-merge", null, { event: "post-merge" }),
                  write("hooks", "hook", "/r/.git/hooks/post-merge.local", "/r/.git/hooks/post-merge", { event: "post-merge" }),
                  write("hooks", "other", null, `${HOME}/x`), write("elsewhere", "k", null, "v"),
                  write("config", "moxi-repo.piece.a/b.setting", "1", null, { piece: "a/b" })];
  const { lines, drawn } = writesOf({ writes, pieces: [planned({ id: "a/b", new: false })] }, { act: "refresh", home: HOME });
  assert.equal(drawn.size, writes.length);
  assert.deepEqual(lines, [
    "into this repo's git config · takes out b's setting · its mode pinned to shim · notes that none of its own hooks "
      + "is moved aside · sets core.other to b (it reads a here)",
    "in its hooks folder · takes away the scripts for after a merge or pull · moves its own post-merge hook back from "
      + "post-merge.local · ~/x",
    "into repo's list (it was listed at ~/old)",
    "elsewhere k to v",
  ]);
});

test("answers: done, nothing, changed", () => {
  const chain = [{ event: "pre-commit", hook: `${HOME}/r/.husky/pre-commit`, line: ". launcher", present: false },
                 { event: "pre-push", hook: "/h", line: "x", present: true }];
  const done = answerView(answer({ lines: ["added notes/secret-gate"], problems: ["pre-push: can't place it"], chain }),
                          { home: HOME });
  assert.deepEqual([done.head, done.lines, done.dim, done.reread], [null, ["added notes/secret-gate"], true, true]);
  assert.deepEqual(done.copies, [{ text: "pre-push: can't place it", copy: "pre-push: can't place it" },
                                 { text: "the pieces run once this line is in ~/r/.husky/pre-commit:", copy: ". launcher" }]);
  const nothing = answerView(answer({ outcome: "nothing", chain }), { home: HOME });
  assert.deepEqual([nothing.head, nothing.heavy, nothing.copies.length], ["Nothing to write: it is already so.", false, 1]);
  const next = plan({ digest: "d2" });
  const changed = answerView(answer({ outcome: "changed", plan: next }));
  assert.deepEqual([changed.head, changed.plan], ["This changed since you looked:", next]);
});

test("answers: refused (outside the copy, busy), partial with Refresh, failed", () => {
  const outside = answerView(answer({ outcome: "refused", refusal: { code: "outside-copy", text: "It changes no repo "
    + "outside that folder, and /r is outside it." } }));
  assert.deepEqual([outside.head, outside.heavy, outside.lines],
                   ["Not done.", true, ["It changes no repo outside that folder, and /r is outside it."]]);
  const busy = answerView(answer({ outcome: "refused", refusal: { code: "busy", text: "another moxi repo command holds /r" } }));
  assert.deepEqual(busy.lines, ["another moxi repo command holds /r."]);
  const command = { label: "Refresh", command: "moxi repo git-hooks refresh /r" };
  const partial = answerView(answer({ outcome: "partial", refusal: { code: null, text: "git config could not set x" } }),
                             { command });
  assert.deepEqual([partial.head, partial.heavy, partial.lines, partial.refresh, partial.command],
                   ["It may be partly done.", true, ["git config could not set x.",
                    "Refresh puts back every entry repo keeps here."], true, command]);
  const failed = answerView(answer({ outcome: "failed", refusal: { code: null, text: "anything" } }));
  assert.deepEqual([failed.head, failed.lines], ["Not done.", ["repo's page server hit an error; its log has the details."]]);
});

test("the transport words: it may have been done, or the shell's status", () => {
  for (const status of [0, 200, 502, 504]) {
    assert.deepEqual([transportView(status).head, transportView(status).reread], ["It may have been done.", true]);
  }
  assert.deepEqual([transportView(400).head, transportView(400).reread], ["The shell refused the request (400).", false]);
  assert.equal(transportView(403).head, "The shell answered 403.");
});
