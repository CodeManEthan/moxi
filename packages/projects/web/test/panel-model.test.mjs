import assert from "node:assert/strict";
import test from "node:test";

import {
  addedSaid, addPanel, addPanelBody, agentAdded, alsoHere, answerNow, createOffered, createWords, editPanel,
  editReread, editTouched, filled, FOLDER_WAIT_MS, folderRoute, folderSays, MAY_BE_ADDED, MAY_BE_SAVED, moreLine,
  named, NAME_STAYS, nameTaken, parseAtPath, partlySaved, saveSteps, setChanges, takenOff,
} from "../src/panel-model.ts";

const at = (fields = {}) => ({
  path: "/home/me/code/finch-scripts", exists: true, has_git: false, project: null, how: null, key: "finch-scripts",
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
  const none = { path: null, exists: null, has_git: null, project: null, how: null, key: null, taken: null };
  assert.deepEqual(parseAtPath(none), none);
  for (const bad of [null, [], {}, at({ how: "beside" }), at({ exists: "yes" }), at({ key: 3 }),
                     { ...at(), taken: undefined }]) {
    assert.equal(parseAtPath(bad), null, JSON.stringify(bad));
  }
});

test("the words under the field say what is there and how it stands to a project", () => {
  assert.equal(folderSays(at()), "The folder exists.");
  assert.equal(folderSays(at({ has_git: true })), "The folder exists. It is a git repository.");
  assert.equal(folderSays(at({ exists: false, has_git: null })), "No folder there yet.");
  assert.equal(folderSays(at({ has_git: true, project: "atlas-api", how: "root" })),
               "The folder exists. It is a git repository. That folder is atlas-api's already.");
  assert.equal(folderSays(at({ project: "atlas-api", how: "inside" })),
               "The folder exists. Inside atlas-api, a project already.");
  assert.equal(folderSays(at({ has_git: true, project: "atlas-api", how: "worktree" })),
               "The folder exists. It is a git repository. A worktree of atlas-api, a project already.");
  // A stat that didn't answer in time says nothing of its part.
  assert.equal(folderSays(at({ exists: null, has_git: null })), "");
  assert.equal(folderSays(at({ exists: null, has_git: null, project: "atlas-api", how: "inside" })),
               "Inside atlas-api, a project already.");
  // A path the server can't take (relative, `~user`) says nothing.
  assert.equal(folderSays(at({ path: null, exists: null, has_git: null, key: null, taken: null })), "");
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
  const missing = at({ exists: false, has_git: null });
  assert.equal(createOffered(missing, KINDS, "project"), "Create the folder and run git init");
  assert.equal(createOffered(missing, KINDS, "shelf"), null);
  assert.equal(createOffered(at(), KINDS, "project"), null);
  assert.equal(createOffered(at({ exists: null, has_git: null }), KINDS, "project"), null);
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
  assert.equal(moreLine(addPanel(), { notesInstalled: true }), "a code project · no lead");
  const form = { ...addPanel(), kind: "agent", lead: " builder-2 ", notesRoot: "~/notes/x" };
  assert.equal(moreLine(form, { notesInstalled: true }), "an agent's home · builder-2 leads · notes in ~/notes/x");
  assert.equal(moreLine(form, { notesInstalled: false }), "an agent's home · builder-2 leads");
  assert.equal(moreLine({ ...addPanel(), kind: "topic" }, { notesInstalled: false }), "topic · no lead");
});

// -- the add -------------------------------------------------------------------------------------

test("the add sends the paths as typed, and create only while it is offered and ticked", () => {
  const form = { ...addPanel({ folder: "~/code/finch-scripts" }), name: " finch-scripts ", lead: " builder-2 ",
                 notesRoot: "~/notes/finch", create: true };
  assert.deepEqual(addPanelBody(form, { notesInstalled: true, canCreate: true }), {
    key: "finch-scripts", root: "~/code/finch-scripts", kind: "project", lead: "builder-2",
    notes_root: "~/notes/finch", create: true,
  });
  assert.deepEqual(addPanelBody(form, { notesInstalled: false, canCreate: false }), {
    key: "finch-scripts", root: "~/code/finch-scripts", kind: "project", lead: "builder-2",
  });
  // An empty folder is left out, so the kind's module may give it.
  assert.deepEqual(addPanelBody({ ...addPanel(), name: "t", kind: "topic" }, { notesInstalled: true, canCreate: false }),
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

// -- Edit ----------------------------------------------------------------------------------------

const HOME = "/home/me";
const FACTS = { home: HOME, notesInstalled: true };
const crewed = (fields = {}) => ({
  key: "atlas-api", root: "/home/me/code/atlas-api", kind: "project", lead: "builder-1",
  associated: ["planner", "reviewer"], agents: ["builder-1", "planner", "reviewer"],
  notes_root: "/home/me/notes/atlas", created: "2026-01-02T03:04:05Z", ...fields,
});

test("the Edit panel opens over the project with its folders in ~ form and nothing to save", () => {
  const form = editPanel(crewed(), HOME);
  assert.deepEqual(form, { base: crewed(), folder: "~/code/atlas-api", kind: "project", lead: "builder-1",
                           notesRoot: "~/notes/atlas", off: [], added: [], agent: "", stale: false });
  assert.deepEqual(alsoHere(form), ["planner", "reviewer"]);
  assert.deepEqual(saveSteps(form, FACTS), []);
  assert.equal(editTouched(form, FACTS), false);
  // No lead and no notes folder: both fields empty, still nothing to save.
  const bare = editPanel(crewed({ lead: null, notes_root: null }), null);
  assert.deepEqual([bare.folder, bare.lead, bare.notesRoot], ["/home/me/code/atlas-api", "", ""]);
  assert.equal(editTouched(bare, FACTS), false);
  assert.equal(NAME_STAYS, "The name stays as it is: everything filed under this project is filed by its name.");
});

test("names are taken off and added in the form, and one taken off comes back when added again", () => {
  let form = takenOff(editPanel(crewed(), HOME), "planner");
  assert.deepEqual(alsoHere(form), ["reviewer"]);
  form = agentAdded({ ...form, agent: " scout " });
  assert.deepEqual([alsoHere(form), form.agent], [["reviewer", "scout"], ""]);
  // Added twice, or one already there: listed once.
  assert.deepEqual(alsoHere(agentAdded({ ...form, agent: "scout" })), ["reviewer", "scout"]);
  assert.deepEqual(alsoHere(agentAdded({ ...form, agent: "reviewer" })), ["reviewer", "scout"]);
  // One added here and taken off again is simply gone; one taken off and typed again is back.
  assert.deepEqual(alsoHere(takenOff(form, "scout")), ["reviewer"]);
  assert.deepEqual(alsoHere(agentAdded({ ...form, agent: "planner" })), ["planner", "reviewer", "scout"]);
  // Nothing typed adds nothing.
  assert.deepEqual(alsoHere(agentAdded({ ...form, agent: "  " })), ["reviewer", "scout"]);
});

test("a save sends the names taken off, then the fields, then the names added", () => {
  // The lead moves onto an agent who works there: taken off first, so the lead rule holds.
  let form = { ...takenOff(editPanel(crewed(), HOME), "planner"), lead: "planner", agent: "builder-1" };
  assert.deepEqual(saveSteps(form, FACTS), [
    { route: "api/agents-remove", body: { key: "atlas-api", name: "planner" }, parts: ["planner taken off"] },
    { route: "api/set", body: { key: "atlas-api", lead: "planner" }, parts: ["the lead"] },
    { route: "api/agents-add", body: { key: "atlas-api", name: "builder-1" }, parts: ["builder-1 added"] },
  ]);
  // The fields: each one changed, the paths as typed.
  form = { ...editPanel(crewed(), HOME), folder: "~/code/atlas-moved", kind: "agent", lead: "",
           notesRoot: "/srv/notes" };
  assert.deepEqual(setChanges(form, FACTS), {
    body: { key: "atlas-api", root: "~/code/atlas-moved", kind: "agent", no_lead: true, notes_root: "/srv/notes" },
    parts: ["the folder", "the kind", "the lead", "the notes folder"],
  });
});

test("only what differs from the project is sent, in either form of a path", () => {
  const base = editPanel(crewed(), HOME);
  // The stored path typed in full, or an emptied folder, is no change.
  assert.equal(setChanges({ ...base, folder: "/home/me/code/atlas-api" }, FACTS), null);
  assert.equal(setChanges({ ...base, folder: "" }, FACTS), null);
  assert.equal(setChanges({ ...base, lead: " builder-1 " }, FACTS), null);
  // An emptied notes folder clears it, but only while notes is installed.
  assert.deepEqual(setChanges({ ...base, notesRoot: "" }, FACTS),
                   { body: { key: "atlas-api", no_notes_root: true }, parts: ["the notes folder"] });
  assert.equal(setChanges({ ...base, notesRoot: "" }, { home: HOME, notesInstalled: false }), null);
  // A notes folder set where there was none.
  const none = editPanel(crewed({ notes_root: null }), HOME);
  assert.deepEqual(setChanges({ ...none, notesRoot: "~/n" }, FACTS),
                   { body: { key: "atlas-api", notes_root: "~/n" }, parts: ["the notes folder"] });
});

test("a save that stopped sends only the rest next time", () => {
  const form = { ...takenOff(editPanel(crewed(), HOME), "planner"), lead: "planner" };
  // The name was taken off; the set was refused. The project as stored now has planner off.
  const after = { ...form, base: crewed({ associated: ["reviewer"], agents: ["builder-1", "reviewer"] }) };
  assert.deepEqual(saveSteps(after, FACTS),
                   [{ route: "api/set", body: { key: "atlas-api", lead: "planner" }, parts: ["the lead"] }]);
});

test("what a stopped save says: what was saved, then the refusal", () => {
  assert.equal(partlySaved([], "no such agent; fix: ..."), "no such agent; fix: ...");
  assert.equal(partlySaved(["the folder", "the lead"], "refused; fix: ..."),
               "Saved the folder and the lead. Not saved: refused; fix: ...");
  assert.equal(partlySaved(["planner taken off", "the folder", "the lead"], "x"),
               "Saved planner taken off, the folder and the lead. Not saved: x");
  assert.equal(MAY_BE_SAVED, "It may have been saved.");
});

test("a project read again restarts an untouched form, and marks a touched one stale", () => {
  const form = editPanel(crewed(), HOME);
  const moved = crewed({ root: "/home/me/code/atlas-moved" });
  assert.deepEqual(editReread(form, moved, FACTS), editPanel(moved, HOME));
  const typed = { ...form, kind: "agent" };
  const stale = editReread(typed, moved, FACTS);
  assert.deepEqual(stale, { ...typed, stale: true });
  // His values stand, told apart from the project he opened: the folder he didn't touch
  // is not sent back.
  assert.deepEqual(saveSteps(stale, FACTS),
                   [{ route: "api/set", body: { key: "atlas-api", kind: "agent" }, parts: ["the kind"] }]);
  // Read again as it was: not stale.
  assert.equal(editReread(stale, crewed(), FACTS).stale, false);
  // Only a name typed, not yet added, counts as touched.
  assert.equal(editTouched({ ...form, agent: "scout" }, FACTS), true);
});
