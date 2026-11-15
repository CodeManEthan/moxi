import assert from "node:assert/strict";
import test from "node:test";

import {
  addedSaid, addPanel, addPanelBody, answerNow, createOffered, createWords, filled, FOLDER_WAIT_MS, folderRoute,
  folderSays, MAY_BE_ADDED, moreLine, named, nameTaken, parseAtPath,
} from "../src/panel-model.ts";

const at = (fields = {}) => ({
  path: "/home/me/code/finch-scripts", exists: true, git: false, project: null, how: null, key: "finch-scripts",
  taken: false, ...fields,
});

const KINDS = {
  found: [
    { name: "project", doc: "", module: "projects", mint: "git" },
    { name: "agent", doc: "", module: "projects", mint: "dir" },
    { name: "shelf", doc: "", module: "shelves", mint: "refuse" },
  ],
  broken: [],
};

const listed = (key) => ({
  key, root: `/home/me/code/${key}`, kind: "project", lead: null, associated: [], agents: [], notes_root: null,
  created: "2026-01-02T03:04:05Z", exists: true,
});

// -- the folder's answer -----------------------------------------------------------------------

test("the folder is read a quarter second after the last key, its path sent encoded", () => {
  assert.equal(FOLDER_WAIT_MS, 250);
  assert.equal(folderRoute("~/code/a b&c"), "api/folder?path=~%2Fcode%2Fa%20b%26c");
});

test("an answer of the route's shape is read, and any other is not", () => {
  assert.deepEqual(parseAtPath(at()), at());
  const none = { path: null, exists: null, git: null, project: null, how: null, key: null, taken: null };
  assert.deepEqual(parseAtPath(none), none);
  for (const bad of [null, [], {}, at({ how: "beside" }), at({ exists: "yes" }), at({ key: 3 }),
                     { ...at(), taken: undefined }]) {
    assert.equal(parseAtPath(bad), null, JSON.stringify(bad));
  }
});

test("the words under the field say what is there and how it stands to a project", () => {
  assert.equal(folderSays(at()), "The folder exists.");
  assert.equal(folderSays(at({ git: true })), "The folder exists. It is a git repository.");
  assert.equal(folderSays(at({ exists: false, git: null })), "No folder there yet.");
  assert.equal(folderSays(at({ git: true, project: "atlas-api", how: "root" })),
               "The folder exists. It is a git repository. That folder is atlas-api's already.");
  assert.equal(folderSays(at({ project: "atlas-api", how: "inside" })),
               "The folder exists. Inside atlas-api, a project already.");
  assert.equal(folderSays(at({ git: true, project: "atlas-api", how: "worktree" })),
               "The folder exists. It is a git repository. A worktree of atlas-api, a project already.");
  // A stat that didn't answer in time says nothing of its part.
  assert.equal(folderSays(at({ exists: null, git: null })), "");
  assert.equal(folderSays(at({ exists: null, git: null, project: "atlas-api", how: "inside" })),
               "Inside atlas-api, a project already.");
  // A path the server can't take (relative, `~user`) says nothing.
  assert.equal(folderSays(at({ path: null, exists: null, git: null, key: null, taken: null })), "");
});

test("an answer asked for a value the field no longer holds is dropped", () => {
  const asked = { typed: "~/code/fin", answer: at({ path: "/home/me/code/fin", key: "fin" }) };
  assert.equal(answerNow(asked, "~/code/fin"), asked.answer);
  assert.equal(answerNow(asked, "~/code/finch-scripts"), null);
  assert.equal(answerNow(asked, ""), null);
  assert.equal(answerNow(null, "~/code/fin"), null);
});

test("the box that makes the folder is offered only while nothing is there, in the kind's words", () => {
  assert.equal(createWords(KINDS, "project"), "Create the folder and run git init");
  assert.equal(createWords(KINDS, "agent"), "Create the folder");
  assert.equal(createWords(KINDS, "shelf"), null);
  assert.equal(createWords(KINDS, "unknown"), null);
  assert.equal(createWords(null, "project"), null);
  const missing = at({ exists: false, git: null });
  assert.equal(createOffered(missing, KINDS, "project"), "Create the folder and run git init");
  assert.equal(createOffered(missing, KINDS, "shelf"), null);
  assert.equal(createOffered(at(), KINDS, "project"), null);
  assert.equal(createOffered(at({ exists: null, git: null }), KINDS, "project"), null);
  assert.equal(createOffered(null, KINDS, "project"), null);
  assert.equal(createOffered(at({ path: null, exists: false }), KINDS, "project"), null);
});

// -- the name ------------------------------------------------------------------------------------

test("the name fills from the folder until he types in it, and again once he empties it", () => {
  let form = addPanel();
  assert.deepEqual(form, { folder: "", name: "", named: false, kind: "project", lead: "", notesRoot: "",
                           create: false });
  form = filled(form, at());
  assert.equal(form.name, "finch-scripts");
  // A folder whose last part isn't a key fills nothing.
  assert.equal(filled(form, at({ key: null })).name, "");
  form = named(form, "finch");
  assert.equal(filled(form, at({ key: "other" })).name, "finch");
  form = named(form, "");
  assert.equal(form.named, false);
  assert.equal(filled(form, at({ key: "other" })).name, "other");
  // A folder row's add opens with both filled.
  assert.deepEqual(addPanel({ folder: "~/code/finch-scripts", name: "finch-scripts" }),
                   { ...addPanel(), folder: "~/code/finch-scripts", name: "finch-scripts" });
});

test("a name a project already has is taken, ignoring case and spaces around it", () => {
  const projects = [listed("atlas-api"), listed("Kestrel-Web")];
  assert.equal(nameTaken("atlas-api", projects), true);
  assert.equal(nameTaken(" kestrel-web ", projects), true);
  assert.equal(nameTaken("atlas", projects), false);
  assert.equal(nameTaken("", projects), false);
});

// -- More ----------------------------------------------------------------------------------------

test("More's line names the kind, who leads and the notes folder when one is set", () => {
  assert.equal(moreLine(addPanel(), { notes: true }), "a code project · no lead");
  const form = { ...addPanel(), kind: "agent", lead: " builder-2 ", notesRoot: "~/notes/x" };
  assert.equal(moreLine(form, { notes: true }), "an agent's home · builder-2 leads · notes in ~/notes/x");
  assert.equal(moreLine(form, { notes: false }), "an agent's home · builder-2 leads");
  assert.equal(moreLine({ ...addPanel(), kind: "topic" }, { notes: false }), "topic · no lead");
});

// -- the add -------------------------------------------------------------------------------------

test("the add sends the paths as typed, and create only while it is offered and ticked", () => {
  const form = { ...addPanel({ folder: "~/code/finch-scripts" }), name: " finch-scripts ", lead: " builder-2 ",
                 notesRoot: "~/notes/finch", create: true };
  assert.deepEqual(addPanelBody(form, { notes: true, offered: true }), {
    key: "finch-scripts", root: "~/code/finch-scripts", kind: "project", lead: "builder-2",
    notes_root: "~/notes/finch", create: true,
  });
  assert.deepEqual(addPanelBody(form, { notes: false, offered: false }), {
    key: "finch-scripts", root: "~/code/finch-scripts", kind: "project", lead: "builder-2",
  });
  // An empty folder is left out, so the kind's module may give it.
  assert.deepEqual(addPanelBody({ ...addPanel(), name: "t", kind: "topic" }, { notes: true, offered: false }),
                   { key: "t", kind: "topic" });
});

test("what an add came to: added with its notes and warnings, refused with its text, or may have been", () => {
  const project = { ...listed("finch-scripts"), root: "/home/me/code/finch-scripts" };
  delete project.exists;
  assert.deepEqual(addedSaid({ outcome: "done", value: { project, notes: ["made it and ran git init"], warnings: [] } },
                             "/home/me"),
                   { outcome: "done", lines: ["Added finch-scripts at ~/code/finch-scripts.", "made it and ran git init"] });
  assert.deepEqual(addedSaid({ outcome: "done", value: { project, notes: [], warnings: ["skipped broken"] } }, null),
                   { outcome: "done", lines: ["Added finch-scripts at /home/me/code/finch-scripts.",
                                              "Warning: skipped broken"] });
  assert.deepEqual(addedSaid({ outcome: "refused", code: 422, text: "a project has that root; fix: ..." }, null),
                   { outcome: "refused", lines: ["a project has that root; fix: ..."] });
  assert.deepEqual(addedSaid({ outcome: "unknown", text: "x" }, null), { outcome: "unknown", lines: [MAY_BE_ADDED] });
  assert.equal(MAY_BE_ADDED, "It may have been added.");
});
