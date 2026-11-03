import assert from "node:assert/strict";
import test from "node:test";

import {
  CHANGED, Feed, parseKinds, parseOne, parseProjects, parseRemoveStatus, parseWhere, parseWritten,
  PICKED, RETRIES, saidOf,
} from "../src/feed.ts";

const PROJECT = { key: "alpha", root: "/w/alpha", kind: "project", lead: "lead-a",
                  associated: ["helper"], agents: ["lead-a", "helper"], notes_root: null,
                  created: "2026-01-02T03:04:05Z" };

/** A feed of `/api/projects` over `answers`, read in order. */
function feedOf(answers) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parseProjects, () => { changes += 1; },
                        { wait: async (ms) => { waits.push(ms); } });
  return { feed, reads, waits, changes: () => changes };
}

test("the events are named", () => {
  assert.equal(PICKED, "moxi-change");
  assert.equal(CHANGED, "moxi-projects-changed");
});

test("a project list, and anything not of its shape refused", () => {
  assert.deepEqual(parseProjects({ projects: [PROJECT] }), { projects: [PROJECT] });
  assert.deepEqual(parseProjects({ projects: [] }), { projects: [] });
  assert.equal(parseProjects({ projects: [{ ...PROJECT, root: null }] }), null);
  assert.equal(parseProjects({ projects: [{ ...PROJECT, associated: "helper" }] }), null);
  assert.equal(parseProjects([]), null);
  assert.equal(parseProjects(null), null);
});

test("one project, and an add's answer with its notes", () => {
  assert.deepEqual(parseOne({ project: PROJECT }), { project: PROJECT });
  assert.equal(parseOne({ project: null }), null);
  assert.deepEqual(parseWritten({ project: PROJECT, notes: ["made /w/alpha"] }),
                   { project: PROJECT, notes: ["made /w/alpha"] });
  assert.equal(parseWritten({ project: PROJECT }), null);
});

test("kinds take only the three mints", () => {
  const kind = { name: "topic", doc: "a subject", module: "notes", mint: "dir" };
  const broken = { module: "fake", reason: "it won't import" };
  assert.deepEqual(parseKinds({ found: [kind], broken: [broken] }), { found: [kind], broken: [broken] });
  assert.equal(parseKinds({ found: [{ ...kind, mint: "maybe" }], broken: [] }), null);
  assert.equal(parseKinds({ found: [kind] }), null);
});

test("where: the store, and the git or none", () => {
  assert.deepEqual(parseWhere({ store: "/d/projects.db", git: null }), { store: "/d/projects.db", git: null });
  assert.deepEqual(parseWhere({ store: "/d/projects.db", git: "/usr/bin/git" }).git, "/usr/bin/git");
  assert.equal(parseWhere({ store: null, git: null }), null);
});

test("a remove's status: removed, or refused with its sentences", () => {
  const removed = { job: "K", project: "alpha", created: "2026-01-02T03:04:05Z", state: "removed",
                    key: "alpha", reason: null, fix: null, sentences: [], exit_code: null };
  assert.deepEqual(parseRemoveStatus(removed), removed);
  const refused = { ...removed, state: "refused", reason: "it has agents", fix: "remove them",
                    sentences: ["it has agents"], exit_code: 1 };
  assert.deepEqual(parseRemoveStatus(refused), refused);
  assert.equal(parseRemoveStatus({ ...removed, state: "gone" }), null);
  assert.equal(parseRemoveStatus({ ...removed, exit_code: "1" }), null);
});

test("an answer's own line", () => {
  assert.equal(saidOf({ refused: "no project named nope; fix: list them" }), "no project named nope; fix: list them");
  assert.equal(saidOf({ conflict: "the store is busy" }), "the store is busy");
  assert.equal(saidOf({ refused: "" }), null);
  assert.equal(saidOf("text"), null);
});

test("a 200 gives the value", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: { projects: [PROJECT] } }]);
  assert.equal(feed.loading, true);
  await feed.refresh();
  assert.equal(feed.loading, false);
  assert.deepEqual(feed.value, { projects: [PROJECT] });
  assert.equal(feed.failure, null);
  assert.equal(changes(), 1);
});

test("a 503 is read again after 1 s, and four are a failure", async () => {
  const again = feedOf([{ status: 503, data: null }, { status: 200, data: { projects: [] } }]);
  await again.feed.refresh();
  assert.deepEqual(again.waits, [1000]);
  assert.deepEqual(again.feed.value, { projects: [] });
  const busy = { status: 503, data: null };
  const { feed, reads } = feedOf([busy, busy, busy, busy, { status: 200, data: { projects: [] } }]);
  await feed.refresh();
  assert.equal(reads.length, RETRIES + 1);
  assert.deepEqual(feed.failure, { status: 503, why: "projects' page server answered 503", said: null });
});

test("a refusal keeps its line and the last value; a 404 is gone", async () => {
  const { feed } = feedOf([{ status: 200, data: { projects: [PROJECT] } },
                           { status: 422, data: { refused: "the store is newer; fix: upgrade" } },
                           { status: 404, data: null }]);
  await feed.refresh();
  await feed.refresh();
  assert.deepEqual(feed.failure, { status: 422, why: "projects' page server answered 422",
                                   said: "the store is newer; fix: upgrade" });
  assert.deepEqual(feed.value, { projects: [PROJECT] });
  await feed.refresh();
  assert.equal(feed.gone, true);
  assert.equal(feed.failure, null);
});

test("a body not of the shape is a failure", async () => {
  const { feed } = feedOf([{ status: 200, data: { projects: "none" } }]);
  await feed.refresh();
  assert.equal(feed.failure.status, 200);
  assert.equal(feed.value, null);
});

test("a refresh while one runs reads once more after it; a closed feed changes nothing", async () => {
  const { feed, reads, changes } = feedOf([{ status: 200, data: { projects: [] } },
                                           { status: 200, data: { projects: [PROJECT] } }]);
  const first = feed.refresh();
  const second = feed.refresh();
  await first;
  await second;
  assert.equal(reads.length, 2);
  assert.deepEqual(feed.value, { projects: [PROJECT] });
  assert.equal(changes(), 1);
  const closed = feedOf([{ status: 200, data: { projects: [PROJECT] } }]);
  closed.feed.close();
  await closed.feed.refresh();
  assert.equal(closed.feed.value, null);
  assert.equal(closed.changes(), 0);
});
