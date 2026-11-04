import assert from "node:assert/strict";
import test from "node:test";

import { Feed, parseProjects, parseRow, parseRows, parseStatus, RETRIES, statusPathOf } from "../src/feed.ts";
import { moduleRow, row, rows, timer } from "./fixtures.mjs";

const settled = () => new Promise((done) => setImmediate(done));

/** A feed over `answers`, read in order. */
function feedOf(answers, parse = parseRows) {
  const reads = [];
  const waits = [];
  let changes = 0;
  const feed = new Feed(async () => { reads.push(1); return answers.shift() ?? { status: 0, data: null }; },
                        parse, () => { changes += 1; }, { wait: async (ms) => { waits.push(ms); } });
  return { feed, reads, waits, changes: () => changes };
}

test("every row form and kind parses, and the value is a copy", () => {
  const data = rows([row(), timer(), moduleRow()]);
  const value = parseRows(data);
  assert.deepEqual(value, data);
  value.rows[0].argv.push("x");
  assert.equal(data.rows[0].argv.length, 3);
});

test("on the phone every redacted text is null, and the rows still parse", () => {
  const phone = row({
    doc: null, detail: null, argv: null, health: null, cwd: "@h/0123456789ab/docs",
    verdict: { state: "fail", reason: null, fix: null, unknown: false },
    checked: { state: "fail", line: null },
  });
  const value = parseRows(rows([phone, timer({ last_run: { failed: true, text: null, at: null } })],
                              { problems: [{ module: "notes", reason: null }] }));
  assert.equal(value.rows[0].verdict.reason, null);
  assert.equal(value.rows[1].last_run.text, null);
  assert.deepEqual(value.problems, [{ module: "notes", reason: null }]);
});

test("a row that isn't one makes the whole list unreadable", () => {
  for (const bad of [
    { ...row(), form: "foreign" },
    { ...row(), kind: "socket" },
    { ...row(), verdict: { state: "bad", reason: "x", fix: null, unknown: false } },
    { ...row(), pid: 1.5 },
    { ...row(), argv: "/bin/true" },
    { ...row(), last_run: { failed: "no", text: "ok", at: null } },
    { ...row(), launches_session: null },
  ]) {
    assert.equal(parseRow(bad), null, JSON.stringify(bad));
    assert.equal(parseRows(rows([row(), bad])), null);
  }
  assert.equal(parseRows({ state: "maybe", detail: null, rows: [], problems: [] }), null);
  assert.equal(parseRows(null), null);
});

test("a store that can't be read is the error state with its detail", () => {
  const value = parseRows({ state: "error", detail: "services' store is too new", rows: [], problems: [] });
  assert.equal(value.state, "error");
  assert.equal(value.detail, "services' store is too new");
});

test("projects and status parse, and each refuses what isn't its shape", () => {
  assert.deepEqual(parseProjects({ projects: [{ key: "alpha", root: "/w/a" }], reason: null }),
                   { projects: [{ key: "alpha", root: "/w/a" }], reason: null });
  assert.equal(parseProjects({ projects: [{ key: "alpha" }], reason: null }), null);
  const status = { job: "k", act: "stop", id: "services/web", state: "done", code: 0, lines: ["stopped"], fix: null };
  assert.deepEqual(parseStatus(status), status);
  assert.deepEqual(parseStatus({ ...status, lines: [null] }).lines, [null]);
  assert.equal(parseStatus({ ...status, state: "removed" }), null);
  assert.equal(parseStatus({ ...status, code: "0" }), null);
});

test("a status link is taken only as services' own status route", () => {
  assert.equal(statusPathOf("/api/status?job=abc_DEF-123"), "api/status?job=abc_DEF-123");
  assert.equal(statusPathOf("api/status?job=abc"), "api/status?job=abc");
  for (const bad of ["/api/other?job=a", "/api/status?job=", "/api/status?job=a&x=1", "//evil/api/status?job=a",
                     "/api/status?job=a/../b", null, 7]) {
    assert.equal(statusPathOf(bad), null, String(bad));
  }
});

test("a 200 gives the value; a 503 is read again after 1 s, and four are a failure", async () => {
  const up = { status: 200, data: rows([row()]) };
  const once = feedOf([up]);
  await once.feed.refresh();
  assert.equal(once.feed.loading, false);
  assert.equal(once.feed.value.rows[0].id, "services/web");
  assert.equal(once.changes(), 1);

  const busy = { status: 503, data: { error: "busy" } };
  const again = feedOf([busy, up]);
  await again.feed.refresh();
  assert.deepEqual(again.waits, [1000]);
  assert.equal(again.feed.failure, null);

  const stuck = feedOf([busy, busy, busy, busy, up]);
  await stuck.feed.refresh();
  assert.equal(stuck.reads.length, RETRIES + 1);
  assert.deepEqual(stuck.feed.failure, { status: 503, why: "services' page server answered 503", said: "busy" });
});

test("a failure keeps the last value, a 422 keeps its refusal, and a 404 is gone", async () => {
  const { feed } = feedOf([{ status: 200, data: rows([row()]) }, { status: 0, data: null },
                           { status: 422, data: { refused: "no row web; fix: moxi services list" } },
                           { status: 404, data: null }]);
  await feed.refresh();
  await feed.refresh();
  assert.equal(feed.value.rows.length, 1);
  assert.deepEqual(feed.failure, { status: 0, why: "no answer from the shell", said: null });
  await feed.refresh();
  assert.equal(feed.failure.said, "no row web; fix: moxi services list");
  await feed.refresh();
  assert.equal(feed.gone, true);
  assert.equal(feed.failure, null);
});

test("a topic event replaces the value at once after a good read", async () => {
  const { feed, reads, changes } = feedOf([{ status: 200, data: rows([row()]) }]);
  await feed.refresh();
  feed.topic(rows([row(), timer()]));
  assert.equal(reads.length, 1);
  assert.equal(feed.value.rows.length, 2);
  assert.equal(changes(), 2);
});

test("a topic event that can't be read, or comes after a failure, reads the route again", async () => {
  const { feed, reads } = feedOf([{ status: 0, data: null }, { status: 200, data: rows([row()]) },
                                  { status: 200, data: rows([]) }]);
  await feed.refresh();
  feed.topic(rows([timer()]));
  await settled();
  assert.equal(reads.length, 2);
  assert.equal(feed.value.rows[0].id, "services/web");
  feed.topic({ nonsense: true });
  await settled();
  assert.equal(reads.length, 3);
  assert.equal(feed.value.rows.length, 0);
});

test("a closed feed drops every later answer and event", async () => {
  const { feed, changes } = feedOf([{ status: 200, data: rows([row()]) }]);
  const read = feed.refresh();
  feed.close();
  await read;
  feed.topic(rows([timer()]));
  assert.equal(feed.value, null);
  assert.equal(changes(), 0);
});
