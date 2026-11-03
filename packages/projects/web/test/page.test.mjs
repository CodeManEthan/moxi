import assert from "node:assert/strict";
import test from "node:test";

import {
  actedOf, actSaid, agentBody, clearLeadBody, editBody, editForm, filterChoices, kindChoices, leadBody,
  listView, MAY_HAVE_ACTED, NO_GIT, projectFields, removedSaid, removeSheet, removeStatusPath, whereView,
} from "../src/views.ts";
import { parseOne, parseWritten } from "../src/feed.ts";

const state = (over) => ({ loading: false, gone: false, failure: null, value: null, ...over });
const PROJECT = { key: "alpha", root: "/w/alpha", kind: "project", lead: "lead-a",
                  associated: ["helper"], agents: ["lead-a", "helper"], notes_root: null,
                  created: "2026-01-02T03:04:05Z" };
const OLD = { ...PROJECT, key: "old", kind: "shelf-gone", lead: null, associated: [], agents: [] };
const KINDS = {
  found: [{ name: "agent", doc: "an agent's home", module: "projects", mint: "git" },
          { name: "project", doc: "code", module: "projects", mint: "git" },
          { name: "topic", doc: "a subject", module: "notes", mint: "dir" }],
  broken: [],
};

// -- the list -----------------------------------------------------------------------------------

test("the list: loading, up, empty for the kind, failed keeping the last list, gone", () => {
  assert.equal(listView(state({ loading: true }), null).state, "loading");
  const up = listView(state({ value: { projects: [PROJECT] } }), null);
  assert.deepEqual(up, { state: "up", projects: [PROJECT], line: null });
  assert.deepEqual(listView(state({ value: { projects: [] } }), "agent"),
                   { state: "empty", projects: [], line: "No project has the kind agent." });
  const failure = { status: 422, why: "x", said: "the store is newer than this code; fix: upgrade" };
  assert.deepEqual(listView(state({ value: { projects: [PROJECT] }, failure }), null),
                   { state: "failed", projects: [PROJECT], line: "the store is newer than this code; fix: upgrade" });
  assert.equal(listView(state({ gone: true, value: { projects: [PROJECT] } }), null).state, "gone");
});

test("the kind filter offers every kind, each listed kind, and a kind no module declares", () => {
  const choices = filterChoices(KINDS, [PROJECT, OLD], "");
  assert.deepEqual(choices.map((choice) => choice.value), ["", "agent", "project", "topic", "shelf-gone"]);
  assert.equal(choices[0].label, "Every kind");
  assert.equal(choices[3].label, "topic (notes)");
  assert.equal(choices[4].label, "shelf-gone (no installed module declares it)");
  // A filter that names a kind no longer listed stays offered, so the menu shows it.
  assert.deepEqual(filterChoices(null, [], "gone").map((choice) => choice.value), ["", "gone"]);
  // While the kinds are unknown, a listed kind is offered by name, not called undeclared.
  assert.deepEqual(filterChoices(null, [PROJECT], ""), [{ value: "", label: "Every kind" },
                                                       { value: "project", label: "project" }]);
});

test("the edit form's kinds keep the project's own kind", () => {
  assert.deepEqual(kindChoices(KINDS, "project").map((choice) => choice.value), ["agent", "project", "topic"]);
  assert.deepEqual(kindChoices(KINDS, "shelf-gone").map((choice) => choice.value),
                   ["agent", "project", "topic", "shelf-gone"]);
});

test("a project's fields are the card's, with its notes root when it has one", () => {
  assert.deepEqual(projectFields(PROJECT).map((field) => field.name), ["kind", "root", "lead", "agents"]);
  const fields = projectFields({ ...PROJECT, notes_root: "/n/alpha" });
  assert.deepEqual(fields.at(-1), { name: "notes_root", label: "Notes root", value: "/n/alpha" });
});

// -- the store and git ---------------------------------------------------------------------------

test("the store and git line shows what the server gives, and says when there is no git", () => {
  assert.equal(whereView(state({ loading: true })).state, "loading");
  assert.deepEqual(whereView(state({ value: { store: "/h/projects.db", git: "/usr/bin/git" } })),
                   { state: "up", store: "/h/projects.db", git: "/usr/bin/git", line: null });
  const none = whereView(state({ value: { store: "/h/projects.db", git: null } }));
  assert.equal(none.git, null);
  assert.match(NO_GIT, /create is refused/);
  const failed = whereView(state({ value: { store: "/h/projects.db", git: null },
                                   failure: { status: 0, why: "no answer from the shell", said: null } }));
  assert.deepEqual([failed.state, failed.store, failed.line], ["failed", "/h/projects.db", "no answer from the shell"]);
});

// -- the edit form --------------------------------------------------------------------------------

test("an edit sends only what changed, as the verb's flags", () => {
  const form = editForm(PROJECT);
  assert.deepEqual(form, { root: "/w/alpha", kind: "project", notesRoot: "" });
  assert.equal(editBody(PROJECT, form, { notes: true }), null);
  assert.deepEqual(editBody(PROJECT, { ...form, root: "/w/moved" }, { notes: false }), { key: "alpha", root: "/w/moved" });
  assert.deepEqual(editBody(PROJECT, { ...form, kind: "agent" }, { notes: false }), { key: "alpha", kind: "agent" });
  // An emptied root is left as it is: a root can't be cleared.
  assert.equal(editBody(PROJECT, { ...form, root: "" }, { notes: false }), null);
  // A relative root is sent as typed, so the server refuses it.
  assert.deepEqual(editBody(PROJECT, { ...form, root: "w/rel" }, { notes: false }), { key: "alpha", root: "w/rel" });
});

test("the notes root is set or cleared only while notes is installed", () => {
  const withNotes = { ...PROJECT, notes_root: "/n/alpha" };
  const form = editForm(withNotes);
  assert.equal(form.notesRoot, "/n/alpha");
  assert.deepEqual(editBody(withNotes, { ...form, notesRoot: "" }, { notes: true }), { key: "alpha", no_notes_root: true });
  assert.deepEqual(editBody(withNotes, { ...form, notesRoot: "/n/other" }, { notes: true }),
                   { key: "alpha", notes_root: "/n/other" });
  assert.equal(editBody(withNotes, { ...form, notesRoot: "" }, { notes: false }), null);
  assert.deepEqual(editBody(PROJECT, { ...editForm(PROJECT), notesRoot: "/n/a" }, { notes: true }),
                   { key: "alpha", notes_root: "/n/a" });
});

// -- lead and agents ------------------------------------------------------------------------------

test("lead and agents acts send what the verbs take", () => {
  assert.deepEqual(leadBody("alpha", "  scout "), { key: "alpha", lead: "scout" });
  assert.deepEqual(clearLeadBody("alpha"), { key: "alpha", no_lead: true });
  assert.deepEqual(agentBody("alpha", " helper "), { key: "alpha", name: "helper" });
});

test("an act's answer: done with its notes, refused with its line, or may have acted", () => {
  const written = { project: PROJECT, notes: ["the root /w/moved doesn't exist yet"] };
  assert.deepEqual(actSaid(actedOf(200, written, parseWritten), "Saved alpha."),
                   { tone: "ok", lines: ["Saved alpha.", "the root /w/moved doesn't exist yet"] });
  assert.deepEqual(actSaid(actedOf(200, { project: PROJECT }, parseOne), "Added helper to alpha."),
                   { tone: "ok", lines: ["Added helper to alpha."] });
  assert.deepEqual(actSaid(actedOf(422, { refused: "no such pair; fix: list them" }, parseOne), "x"),
                   { tone: "error", lines: ["no such pair; fix: list them"] });
  assert.deepEqual(actSaid(actedOf(504, null, parseOne), "x"), { tone: "warn", lines: [MAY_HAVE_ACTED] });
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
