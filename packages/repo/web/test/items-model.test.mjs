import assert from "node:assert/strict";
import test from "node:test";

import { FOLD_PAST, foldedLine, itemsOf, repoItems, runnable, timeWords, verdict } from "../src/items-model.ts";
import { AT, event, HOME, piece, problem, record, repo, spec, T, TODAY, UTC, value } from "./fixtures.mjs";

const opts = { home: HOME, today: TODAY, clock: UTC };
const one = (row) => {
  const items = repoItems(row, opts);
  assert.equal(items.length, 1, JSON.stringify(items.map((item) => item.title)));
  return items[0];
};
const words = (item) => ({ weight: item.weight, title: item.title, dim: item.dim, fix: item.fix,
                           time: timeWords(item, TODAY, UTC) });

const LEFT = problem({ code: "no-module", piece: "inkwell/prose-check", event: "pre-commit",
                       detail: "inkwell/prose-check: no installed module declares it",
                       fix: "moxi repo git-hooks remove /r inkwell/prose-check" });

test("a piece with no module on an event that refuses, its break merged", () => {
  const broke = record({ piece: "inkwell/prose-check", text: "prose-check exited 2\nmore", at: AT("17:55") });
  const item = one(repo({ project: "lumen-docs", problems: [LEFT], records: [broke] }));
  assert.deepEqual(words(item), {
    weight: "need", title: "lumen-docs refuses every commit: prose-check has no module",
    dim: ["no installed module declares it", "it broke at 17:55"],
    fix: { label: "Take the piece out", text: "moxi repo git-hooks remove /r inkwell/prose-check", copy: "moxi repo git-hooks remove /r inkwell/prose-check",
           refresh: false },
    time: "17:55",
  });
  assert.equal(item.record, broke);
  // With no record open, no break and no time.
  const quiet = one(repo({ project: "lumen-docs", problems: [LEFT] }));
  assert.deepEqual(quiet.dim, ["no installed module declares it"]);
  assert.equal(quiet.time, null);
  // A record whose at doesn't parse merges with no time.
  const odd = one(repo({ project: "lumen-docs", problems: [LEFT],
                         records: [record({ piece: "inkwell/prose-check", at: "soon" })] }));
  assert.deepEqual(odd.dim, ["no installed module declares it", "it broke"]);
  assert.equal(odd.time, null);
});

test("the word for what a missing module refuses follows the event, and a non-refusing event says it doesn't run", () => {
  const on = (eventName) => one(repo({ project: "x", problems: [{ ...LEFT, event: eventName }] }));
  assert.equal(on("pre-push").title, "x refuses every push: prose-check has no module");
  assert.equal(on("commit-msg").title, "x refuses every commit: prose-check has no module");
  assert.equal(on("pre-merge-commit").title, "x refuses every merge: prose-check has no module");
  const after = on("post-commit");
  assert.equal(after.title, "x: prose-check has no module");
  assert.deepEqual(after.dim, ["no installed module declares it, so it doesn't run"]);
  assert.equal(after.weight, "need");
});

test("a gone folder is a warning with the command that drops it", () => {
  const gone = problem({ code: "gone", state: "fail", detail: "the folder is gone",
                         fix: "moxi repo git-hooks remove ~/code/quill-mobile" });
  const item = one(repo({ root: `${HOME}/code/quill-mobile`, problems: [gone] }));
  assert.deepEqual(words(item), {
    weight: "warn", title: "~/code/quill-mobile's folder is gone",
    dim: ["~/code/quill-mobile", "its hooks went with it; only this list still names it"],
    fix: { label: "Drop it from the list", text: "moxi repo git-hooks remove ~/code/quill-mobile", copy: "moxi repo git-hooks remove ~/code/quill-mobile",
           refresh: false },
    time: null,
  });
});

test("a needed chain line is a warning with its line to copy", () => {
  const needed = event({ event: "pre-commit", via: "launcher", chain_line: "needed", hook: `${HOME}/code/a/.husky/pre-commit`,
                         line: "sh .git/moxi-repo/launcher pre-commit" });
  const item = one(repo({ project: "a", mode: "shim", events: [needed, event({ event: "commit-msg", chain_line: "present" })] }));
  assert.deepEqual(words(item), {
    weight: "warn", title: "a: before a commit needs a line in another tool's hook",
    dim: ["~/code/a/.husky/pre-commit"],
    fix: { label: null, text: "sh .git/moxi-repo/launcher pre-commit", copy: "sh .git/moxi-repo/launcher pre-commit", refresh: false }, time: null,
  });
});

test("any other problem is its detail, with its fix, and Refresh where the fix is one", () => {
  const items = repoItems(repo({ project: "a", problems: [
    problem({ code: "shim-missing", detail: "pre-commit: its script is missing", fix: "moxi repo git-hooks refresh /r",
              refresh: true }),
    problem({ code: "unreadable", state: "warn", detail: "git can't read it", fix: null }),
    problem({ code: "shim-moved", state: "ok", detail: "the hooks folder moved" }),
  ] }), opts);
  assert.deepEqual(items.map(words), [
    { weight: "need", title: "a: pre-commit: its script is missing", dim: [],
      fix: { label: null, text: "moxi repo git-hooks refresh /r", copy: "moxi repo git-hooks refresh /r", refresh: true }, time: null },
    { weight: "warn", title: "a: git can't read it", dim: [], fix: null, time: null },
  ]);
  const bare = one(repo({ project: "a", problems: [problem({ detail: "x", fix: null, refresh: true })] }));
  assert.deepEqual(bare.fix, { label: null, text: null, copy: null, refresh: true });
});

test("a piece's record not merged is its own need", () => {
  const broke = record({ piece: "notes/secret-gate", text: "secret-gate crashed\nTraceback", fix: "run it again",
                         at: AT("16:40") });
  assert.deepEqual(words(one(repo({ project: "a", records: [broke] }))), {
    weight: "need", title: "a: secret-gate broke", dim: ["secret-gate crashed"],
    fix: { label: null, text: "run it again", copy: "run it again", refresh: false }, time: "16:40",
  });
});

test("a broken piece's Copy takes the command that runs; the alternative stays shown", () => {
  const fix = "moxi repo git-hooks disable /r lint/lint (or: moxi repo git-hooks remove /r lint/lint)";
  const item = one(repo({ project: "a", records: [record({ piece: "lint/lint", fix })] }));
  assert.equal(item.fix.text, fix);
  assert.equal(item.fix.copy, "moxi repo git-hooks disable /r lint/lint");
  assert.equal(runnable("moxi repo git-hooks remove /r"), "moxi repo git-hooks remove /r");
  assert.equal(runnable("run (or: this) and that"), "run (or: this) and that");
});

const COMMIT = record({ kind: "commit", piece: null, spec: "notes/snapshot",
                        text: "refused: drafts/private/ is never committed (refused since 18:45, 2 passes)\n  hook line",
                        fix: "the spec's next pass that commits cleanly clears this", at: AT("18:50") });

test("a refused spec reads the open commit record's words and keeps them after a throttled pass", () => {
  const refused = spec({ status: "throttled", line: "throttled: ran 30s ago", refused_since: T("18:45"),
                         refused_passes: 2, last_commit_time: T("18:30") });
  const item = one(repo({ root: `${HOME}/notes`, specs: [refused], records: [COMMIT] }));
  assert.deepEqual(words(item), {
    weight: "need", title: "Auto-commit notes/snapshot is refused",
    dim: ["refused: drafts/private/ is never committed (refused since 18:45, 2 passes)", "~/notes", "refused 2 passes",
          "last commit 18:30"],
    fix: { label: null, text: "Its next pass that commits clears this.", copy: null, refresh: false },
    time: "since 18:45",
  });
  assert.equal(item.record, COMMIT);
  // With no record open, the spec's own facts alone.
  const bare = one(repo({ root: `${HOME}/notes`, specs: [{ ...refused, refused_passes: 1, last_commit_time: null }] }));
  assert.deepEqual(bare.dim, ["~/notes", "refused 1 pass"]);
});

test("a failed spec stays failed after a throttled pass, with the record's fix", () => {
  const failed = spec({ status: "throttled", error_since: T("17:00"), error_passes: 3 });
  const rec = { ...COMMIT, text: "error: git commit failed\n  detail", fix: "look at the log" };
  assert.deepEqual(words(one(repo({ project: "n", specs: [failed], records: [rec] }))), {
    weight: "need", title: "Auto-commit notes/snapshot failed",
    dim: ["error: git commit failed", "n", "last commit 18:30"],
    fix: { label: null, text: "look at the log", copy: "look at the log", refresh: false }, time: "since 17:00",
  });
  // With no record, no fix.
  assert.equal(one(repo({ project: "n", specs: [failed] })).fix, null);
});

test("a stale index lock is a need from the record", () => {
  const held = spec({ status: "index-busy", index_busy_since: T("12:00"), index_stale: true });
  const rec = { ...COMMIT, text: "repo: notes/snapshot: git's index lock /r/.git/index.lock has been held",
                fix: "if no git is running in /r, remove /r/.git/index.lock" };
  assert.deepEqual(words(one(repo({ project: "n", specs: [held], records: [rec] }))), {
    weight: "need", title: "Auto-commit notes/snapshot is held up by git's index lock",
    dim: ["repo: notes/snapshot: git's index lock /r/.git/index.lock has been held", "n"],
    fix: { label: null, text: "if no git is running in /r, remove /r/.git/index.lock", copy: "if no git is running in /r, remove /r/.git/index.lock", refresh: false },
    time: "since 12:00",
  });
  // An index-busy run that isn't stale yet is no item.
  assert.deepEqual(repoItems(repo({ specs: [spec({ status: "index-busy", index_busy_since: T("12:00") })] }), opts), []);
});

test("refused wins over failed, and failed over a stale index", () => {
  const both = spec({ refused_since: T("18:45"), error_since: T("17:00"), index_stale: true });
  assert.equal(one(repo({ specs: [both] })).source, "refused");
  assert.equal(one(repo({ specs: [{ ...both, refused_since: null }] })).source, "failed");
});

test("a commit record from a pass that committed, with hook lines, is a warning in its own words", () => {
  const said = { ...COMMIT, text: "committed abc1234 (3 paths); 1 left out\n  secret-gate: left out a.env",
                 fix: "see the paths left out", at: AT("19:00") };
  assert.deepEqual(words(one(repo({ project: "n", specs: [spec()], records: [said] }))), {
    weight: "warn", title: "committed abc1234 (3 paths); 1 left out", dim: ["n"],
    fix: { label: null, text: "see the paths left out", copy: "see the paths left out", refresh: false }, time: "19:00",
  });
});

test("a failed push is a need with its unpushed commits, its record merged", () => {
  const pushing = spec({ remote: "origin", push_failed: [{ branch: "main", error: "over quota", failed_at: T("17:00") }],
                         unpushed: [{ branch: "main", ahead: 2 }] });
  const pushRec = record({ kind: "push", spec: "notes/snapshot", branch: "main", text: "push failed: over quota" });
  const item = one(repo({ root: `${HOME}/notes`, specs: [pushing], records: [pushRec] }));
  assert.deepEqual(words(item), {
    weight: "need", title: "Auto-commit notes/snapshot can't push: over quota",
    dim: ["~/notes", "2 commits on main not pushed"],
    fix: { label: null, text: "The next pass pushes.", copy: null, refresh: false }, time: "17:00",
  });
  assert.equal(item.record, pushRec);
  const never = one(repo({ specs: [{ ...pushing, push_failed: [{ branch: "main", error: null, failed_at: null }],
                                     unpushed: [{ branch: "main", ahead: null }] }] }));
  assert.equal(never.title, "Auto-commit notes/snapshot can't push");
  assert.deepEqual(never.dim, ["~/code/atlas-api"]);
});

test("a refused spec whose push fails too is two things to fix", () => {
  const both = spec({ refused_since: T("18:45"), remote: "origin",
                      push_failed: [{ branch: "main", error: "over quota", failed_at: T("17:00") }] });
  assert.deepEqual(repoItems(repo({ specs: [both] }), opts).map((item) => item.source), ["refused", "push"]);
});

test("specs that can't be read are one warning", () => {
  assert.deepEqual(words(one(repo({ project: "a", specs_problem: "the state file can't be read" }))), {
    weight: "warn", title: "a: its auto-commits can't be read", dim: ["the state file can't be read"], fix: null,
    time: null,
  });
  const two = one(repo({ project: "a", specs: [spec({ problem: "bad push" }), spec({ name: "b", problem: "bad name" })] }));
  assert.deepEqual(two.dim, ["bad push", "bad name"]);
});

test("items: needs first, oldest first, no time after a time, a gone repo last", () => {
  const gone = repo({ project: "q", problems: [problem({ code: "gone", fix: "x" })] });
  const warned = repo({ project: "w", problems: [problem({ state: "warn", detail: "late" })] });
  const later = repo({ project: "b", records: [record({ piece: "p/x", at: AT("17:00") })] });
  const earlier = repo({ project: "c", records: [record({ piece: "p/y", at: AT("09:00") })] });
  const untimed = repo({ project: "d", problems: [problem({ detail: "no time" })] });
  const items = itemsOf(value({ repos: [gone, warned, later, untimed, earlier] }), { home: HOME, clock: UTC });
  assert.deepEqual(items.map((item) => item.name), ["c", "b", "d", "w", "q"]);
});

test("the verdict's words", () => {
  const need = repo({ project: "a", problems: [problem()] });
  const warn = repo({ project: "b", problems: [problem({ state: "warn" })] });
  const twoNeeds = repo({ project: "c", problems: [problem(), problem()] });
  const of = (repos) => {
    const v = verdict(itemsOf(value({ repos }), { clock: UTC }), repos);
    return [v.head, v.rest, v.heavy];
  };
  assert.deepEqual(of([need]), ["1 thing needs you", null, true]);
  assert.deepEqual(of([twoNeeds, warn]), ["2 things need you", ", 1 warning", true]);
  assert.deepEqual(of([need, warn, warn]), ["1 thing needs you", ", 2 warnings", true]);
  assert.deepEqual(of([warn]), ["1 warning", null, false]);
  const fine = [repo({ pieces: [piece(), piece({ id: "notes/secret-gate", enabled: false })] }),
                repo({ pieces: [piece()] }),
                repo({ indexed: false, pieces: [piece()] })];
  assert.deepEqual(of(fine), ["Every repo runs its hooks.", "2 repos · 2 pieces in place", false]);
  assert.deepEqual(of([repo({ pieces: [piece()] })]), ["Every repo runs its hooks.", "1 repo · 1 piece in place", false]);
  assert.deepEqual(of([]), ["No repo has a hook yet.", null, false]);
  assert.deepEqual(of([repo({ indexed: false, pieces: [piece()] })]), ["No repo has a hook yet.", null, false]);
});

test("past five items the warnings fold to one line", () => {
  const repos = [repo({ project: "a", problems: [problem(), problem(), problem(), problem()] }),
                 repo({ project: "b", problems: [problem({ state: "warn" }), problem({ state: "warn" })] })];
  const v = verdict(itemsOf(value({ repos }), { clock: UTC }), repos);
  assert.equal(FOLD_PAST, 5);
  assert.equal(v.shown.length, 4);
  assert.equal(v.folded.length, 2);
  assert.equal(foldedLine(v), "2 warnings");
  const five = [repo({ project: "a", problems: [problem(), problem(), problem(), problem({ state: "warn" }),
                                                problem({ state: "warn" })] })];
  const kept = verdict(itemsOf(value({ repos: five }), { clock: UTC }), five);
  assert.equal(kept.shown.length, 5);
  assert.equal(foldedLine(kept), null);
});
