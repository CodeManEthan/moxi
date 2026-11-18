import assert from "node:assert/strict";
import test from "node:test";

import { noHooksActs, repoRow, reposView, specNews, whereLine } from "../src/rows-model.ts";
import { AT, event, HOME, piece, problem, record, repo, spec, T, TODAY, UTC, value } from "./fixtures.mjs";

const opts = { home: HOME, today: TODAY, clock: UTC };
const line = (row) => [row.name, row.where, row.fact, row.factHeavy, row.lead, row.note];

test("where: pieces in run order, off ones, then its own hook, and own hooks of events with no piece", () => {
  const pieces = [piece({ id: "notes/push-gate", event: "pre-push", order: 10 }),
                  piece({ id: "repo/commit-rules", event: "commit-msg", order: 50 }),
                  piece({ id: "repo/commit-proof", event: "commit-msg", order: 60, enabled: false }),
                  piece({ id: "notes/secret-gate", event: "pre-commit", order: 10 }),
                  piece({ id: "x/odd", event: "zz-event", order: 1 })];
  const events = [event({ event: "pre-commit", existing_hook: "/r/.git/hooks/pre-commit.local" })];
  assert.equal(whereLine({ pieces, events, own_hooks: ["post-merge", "post-commit"] }),
               "secret-gate, then its own hook · commit-rules · commit-proof off · push-gate · odd"
               + " · its own hook after a commit · its own hook after a merge or pull");
  // Order breaks ties by id, and two pieces with one name show by id.
  const twins = [piece({ id: "b/gate", event: "pre-commit", order: 5 }), piece({ id: "a/gate", event: "pre-commit", order: 5 })];
  assert.equal(whereLine({ pieces: twins, events: [], own_hooks: [] }), "a/gate · b/gate");
});

test("the fact: a need's short words, heavy; else the quiet ones", () => {
  const left = problem({ code: "no-module", piece: "inkwell/prose-check", event: "pre-commit" });
  const cases = [
    [repo({ problems: [left] }), "refuses every commit", true],
    [repo({ problems: [{ ...left, event: "pre-push" }] }), "refuses every push", true],
    [repo({ problems: [{ ...left, event: "post-commit" }] }), "a piece has no module", true],
    [repo({ specs: [spec({ refused_since: T("18:45") })] }), "auto-commit refused", true],
    [repo({ specs: [spec({ error_since: T("18:45") })] }), "auto-commit failed", true],
    [repo({ specs: [spec({ index_stale: true })] }), "auto-commit held up", true],
    [repo({ specs: [spec({ push_failed: [{ branch: "main", error: null, failed_at: null }] })] }), "can't push", true],
    [repo({ records: [record({ piece: "a/b" })] }), "a piece broke", true],
    [repo({ problems: [problem({ code: "shim-missing" })] }), "hooks can't run", true],
    [repo({ problems: [problem({ code: "gone" })] }), "the folder is gone", false],
    [repo({ problems: [problem({ code: "unreadable" })], pieces: [piece()] }), "can't be read", false],
    [repo({ pieces: [] }), "no hooks", false],
    [repo({ indexed: false, pieces: [piece()] }), "not in the list", false],
    [repo({ pieces: [piece()], problems: [problem({ state: "warn" })] }), null, false],
  ];
  for (const [row, fact, heavy] of cases) {
    const drawn = repoRow(row, opts);
    assert.deepEqual([drawn.fact, drawn.factHeavy], [fact, heavy], JSON.stringify(row.problems));
  }
});

test("line two and the note: led by the heaviest, newest first, no time after a time", () => {
  const notes = repo({
    root: `${HOME}/notes`, pieces: [piece({ id: "notes/secret-gate", event: "pre-commit" })],
    records: [record({ piece: "notes/secret-gate", at: AT("16:40") }), record({ piece: "x/y", at: "soon" })],
    specs: [spec({ refused_since: T("18:45"), last_commit_time: T("18:30") }),
            spec({ name: "notes/b", remote: "origin" })],
  });
  assert.deepEqual(line(repoRow(notes, opts)), [
    "~/notes", "secret-gate", "auto-commit refused", true, "Auto-commit notes/snapshot: refused since 18:45 · last commit 18:30",
    "secret-gate broke at 16:40 · y broke · Auto-commit notes/b: last commit 18:30, pushed",
  ]);
  const quiet = repoRow(repo({ project: "atlas-api", pieces: [piece()] }), opts);
  assert.deepEqual(line(quiet), ["atlas-api", "commit-rules", null, false, null, null]);
  assert.deepEqual([quiet.open, quiet.refresh, quiet.add], [true, false, false]);
});

test("a repo leads with its heaviest item: a need before a warning, then the newest, untimed last", () => {
  const left = problem({ code: "no-module", piece: "inkwell/prose-check", event: "pre-commit" });
  const lead = (row) => { const drawn = repoRow(row, opts); return [drawn.fact, drawn.lead]; };
  // A hook that refuses every commit and broke leads over an older break.
  assert.deepEqual(lead(repo({ problems: [left], records: [
    record({ piece: "a/old", at: AT("09:00") }), record({ piece: "inkwell/prose-check", at: AT("17:55") })] })),
  ["refuses every commit", "prose-check broke at 17:55"]);
  // The newest need leads: a spec refused since 18:45 over a break at 16:40.
  assert.deepEqual(lead(repo({ records: [record({ piece: "a/b", at: AT("16:40") })],
                               specs: [spec({ refused_since: T("18:45") })] })),
                   ["auto-commit refused", "Auto-commit notes/snapshot: refused since 18:45 · last commit 18:30"]);
  // A need with no time comes after a timed need, but before any warning.
  assert.deepEqual(lead(repo({ problems: [left], records: [record({ piece: "a/b", at: AT("08:00") })] })),
                   ["a piece broke", "b broke at 08:00"]);
  assert.deepEqual(lead(repo({ problems: [problem({ state: "warn", detail: "late" }), left] })),
                   ["refuses every commit", null]);
  // A spec's line with no item comes after the news of a need, whatever its time.
  assert.deepEqual(lead(repo({ records: [record({ piece: "a/b", at: AT("08:00") })], specs: [spec()] })),
                   ["a piece broke", "b broke at 08:00"]);
});

test("a spec's line: refused, failed, last commit, and pushed only with a remote and nothing left", () => {
  assert.equal(specNews(spec({ error_since: T("17:00"), last_commit_time: null }), TODAY, UTC),
               "Auto-commit notes/snapshot: failed since 17:00");
  assert.equal(specNews(spec(), TODAY, UTC), "Auto-commit notes/snapshot: last commit 18:30");
  assert.equal(specNews(spec({ remote: "origin" }), TODAY, UTC), "Auto-commit notes/snapshot: last commit 18:30, pushed");
  assert.equal(specNews(spec({ remote: "origin", unpushed: [{ branch: "main", ahead: 2 }] }), TODAY, UTC),
               "Auto-commit notes/snapshot: last commit 18:30");
  assert.equal(specNews(spec({ remote: "origin", push_failed: [{ branch: "main", error: "x", failed_at: null }] }),
                        TODAY, UTC), "Auto-commit notes/snapshot: last commit 18:30");
  assert.equal(specNews(spec({ last_commit_time: T("22:10", "2026-11-17") }), TODAY, UTC),
               "Auto-commit notes/snapshot: last commit yesterday 22:10");
  assert.equal(specNews(spec({ last_commit_time: null }), TODAY, UTC), null);
});

test("acts: Refresh where an item's fix is one, Add on no hooks and not in the list", () => {
  const stale = repoRow(repo({ pieces: [piece()], problems: [problem({ refresh: true })] }), opts);
  assert.equal(stale.refresh, true);
  assert.equal(repoRow(repo(), opts).add, true);
  assert.equal(repoRow(repo({ indexed: false, pieces: [piece()] }), opts).add, true);
});

test("rows: needs by their first item, then project keys, then ~ folders, a gone repo last", () => {
  const repos = [
    repo({ root: `${HOME}/code/zeta`, pieces: [piece()] }),
    repo({ root: `${HOME}/code/gone`, problems: [problem({ code: "gone" })] }),
    repo({ root: "/r/b", project: "bravo", pieces: [piece()] }),
    repo({ root: "/r/late", project: "late", records: [record({ piece: "a/b", at: AT("17:00") })] }),
    repo({ root: "/r/a", project: "alpha", pieces: [piece()] }),
    repo({ root: "/r/early", project: "early", records: [record({ piece: "a/b", at: AT("09:00") })] }),
    repo({ root: `${HOME}/code/able`, pieces: [piece()] }),
  ];
  const view = reposView(value({ repos, unindexed: [{ project: "reviewer-home", root: "/r/rh" },
                                                    { project: "orbit-cli", root: "/r/oc" }] }), { home: HOME, clock: UTC });
  assert.deepEqual(view.rows.map((row) => row.name),
                   ["early", "late", "alpha", "bravo", "~/code/able", "~/code/zeta", "~/code/gone"]);
  assert.equal(view.noHooks.text, "No hooks: orbit-cli, reviewer-home");
  assert.deepEqual(view.noHooks.projects.map((p) => p.root), ["/r/oc", "/r/rh"]);
});

test("with no repo in the list, each project with no hooks is a row; without projects, nothing", () => {
  const view = reposView(value({ repos: [], unindexed: [{ project: "orbit-cli", root: "/r/oc" }] }), { clock: UTC });
  assert.equal(view.noHooks, null);
  assert.deepEqual(view.rows.map((row) => [row.name, row.fact, row.add, row.open]), [["orbit-cli", "no hooks", true, false]]);
  assert.deepEqual(reposView(value({ repos: [repo({ pieces: [piece()] })] }), { clock: UTC }).noHooks, null);
  assert.deepEqual(reposView(value({ repos: [repo({ pieces: [piece()] })], unindexed: [] }), { clock: UTC }).noHooks, null);
});

test("the No hooks line's Add: one for one project, one per project named for several", () => {
  const orbit = { project: "orbit-cli", root: "/home/u/code/orbit-cli" };
  const home = { project: "reviewer-home", root: "/home/u/code/reviewer-home" };
  assert.deepEqual(noHooksActs({ text: "No hooks: orbit-cli", projects: [orbit] }), [{ label: "Add", project: orbit }]);
  assert.deepEqual(noHooksActs({ text: "", projects: [orbit, home] }).map((act) => act.label),
                   ["Add orbit-cli", "Add reviewer-home"]);
});
