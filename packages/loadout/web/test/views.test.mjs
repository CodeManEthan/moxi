import assert from "node:assert/strict";
import test from "node:test";

import {
  countLine, driftView, jobPathOf, jobRefusal, parseKept, saidOf, startedOf,
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

test("a refused job read ends the job with the refusal's own line, and no other failure does", () => {
  const gone = { status: 422, why: "loadout's page server answered 422",
                 said: "there is no page job j1; a job's record is kept for a day" };
  assert.equal(jobRefusal(gone), "there is no page job j1; a job's record is kept for a day");
  assert.equal(jobRefusal({ ...gone, said: null }), "loadout's page server answered 422");
  for (const status of [0, 404, 500, 503]) {
    assert.equal(jobRefusal({ status, why: "w", said: null }), null);
  }
  assert.equal(jobRefusal(null), null);
});

test("the job a tab keeps is its status path and label, or nothing", () => {
  assert.deepEqual(parseKept(JSON.stringify({ path: "api/job?job=a_B-1", label: "Install", anchor: "global" })),
                   { path: "api/job?job=a_B-1", label: "Install", anchor: "global" });
  assert.deepEqual(parseKept(JSON.stringify({ path: "/api/job?job=a1", anchor: 3 })),
                   { path: "api/job?job=a1", label: "A change", anchor: null });
  for (const raw of [null, "", "{", "null", "[]", JSON.stringify({ path: "api/other" }),
                     JSON.stringify({ path: "https://elsewhere/api/job?job=x" })]) {
    assert.equal(parseKept(raw), null, raw);
  }
});
