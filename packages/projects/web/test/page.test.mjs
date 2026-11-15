import assert from "node:assert/strict";
import test from "node:test";

import { kindChoices, removedSaid, removeSheet, removeStatusPath } from "../src/views.ts";

const PROJECT = { key: "alpha", root: "/w/alpha", kind: "project", lead: "lead-a",
                  associated: ["helper"], agents: ["lead-a", "helper"], notes_root: null,
                  created: "2026-01-02T03:04:05Z" };
const KINDS = {
  found: [{ name: "agent", doc: "an agent's home", module: "projects", mint: "git" },
          { name: "project", doc: "code", module: "projects", mint: "git" },
          { name: "topic", doc: "a subject", module: "notes", mint: "dir" }],
  broken: [],
};

test("the Edit panel's kinds keep the project's own kind", () => {
  assert.deepEqual(kindChoices(KINDS, "project").map((choice) => choice.value), ["agent", "project", "topic"]);
  assert.deepEqual(kindChoices(KINDS, "shelf-gone").map((choice) => choice.value),
                   ["agent", "project", "topic", "shelf-gone"]);
});

// -- remove ---------------------------------------------------------------------------------------

test("the remove sheet names the command, the key and the root", () => {
  const sheet = removeSheet(PROJECT);
  assert.equal(sheet[0], "moxi projects remove alpha");
  assert.match(sheet[1], /alpha/);
  assert.match(sheet[1], /\/w\/alpha/);
});

test("the status link is read only when it is the remove status route", () => {
  const key = "A".repeat(43);
  assert.equal(removeStatusPath(`/api/remove-status?job=${key}`), `api/remove-status?job=${key}`);
  assert.equal(removeStatusPath(`api/remove-status?job=${key}`), `api/remove-status?job=${key}`);
  for (const bad of [null, 3, "/api/other?job=x", "/api/remove-status?job=a/b", "https://x/api/remove-status?job=a"]) {
    assert.equal(removeStatusPath(bad), null, String(bad));
  }
});

test("a remove's outcome: removed, or each sentence and the fix", () => {
  const base = { job: "k", project: "alpha", created: "2026-01-02T03:04:05Z", key: "alpha" };
  assert.deepEqual(removedSaid({ ...base, state: "removed", reason: null, fix: null, sentences: [], exit_code: null }),
                   { tone: "ok", lines: ["Removed alpha. Its root and its files are left as they were."] });
  const held = { ...base, state: "refused", reason: "project 'alpha' is still held (shelves: a; notes: b)",
                 fix: "free it as each says", sentences: ["shelves: a", "notes: b"], exit_code: 3 };
  assert.deepEqual(removedSaid(held), { tone: "error", lines: ["shelves: a", "notes: b", "fix: free it as each says"] });
  const bare = { ...held, sentences: [], fix: null };
  assert.deepEqual(removedSaid(bare).lines, [held.reason]);
});
