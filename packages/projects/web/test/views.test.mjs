import assert from "node:assert/strict";
import test from "node:test";

import {
  actedOf, addBody, addSaid, brokenLines, cardFields, cardPath, cardView, createLine, createShown,
  emptyForm, emptyLine, kindLabel, MAY_EXIST, MAY_HAVE_ACTED, NOT_SENT, pickedDetail, pickerPath,
  pickerView, startKind,
} from "../src/views.ts";
import { parseWritten } from "../src/feed.ts";

const state = (over) => ({ loading: false, gone: false, failure: null, value: null, ...over });
const PROJECT = { key: "alpha", root: "/w/alpha", kind: "project", lead: "lead-a",
                  associated: ["helper", "scout"], agents: ["lead-a", "helper", "scout"],
                  notes_root: null, created: "2026-01-02T03:04:05Z" };
const BETA = { ...PROJECT, key: "Beta", root: "/w/beta", kind: "agent", lead: null, associated: [],
               agents: [] };
const KINDS = {
  found: [{ name: "agent", doc: "an agent's home", module: "projects", mint: "git" },
          { name: "project", doc: "code", module: "projects", mint: "git" },
          { name: "topic", doc: "a subject", module: "notes", mint: "dir" },
          { name: "shelf", doc: "made elsewhere", module: "shelves", mint: "refuse" }],
  broken: [{ module: "fake", reason: "its import failed: no module named x" }],
};

// -- the picker -------------------------------------------------------------------------------

test("the picker lists each project's key and root", () => {
  const view = pickerView(state({ value: { projects: [PROJECT, BETA] } }), null);
  assert.equal(view.state, "up");
  assert.deepEqual(view.choices, [{ key: "alpha", root: "/w/alpha" }, { key: "Beta", root: "/w/beta" }]);
});

test("the picker reads by kind, and says when none has it", () => {
  assert.equal(pickerPath(null), "api/projects");
  assert.equal(pickerPath("agent"), "api/projects?kind=agent");
  assert.equal(pickerPath("a b&c"), "api/projects?kind=a%20b%26c");
  const empty = pickerView(state({ value: { projects: [] } }), "agent");
  assert.equal(empty.state, "empty");
  assert.equal(empty.line, "No project has the kind agent.");
  assert.equal(emptyLine(null), "No project is listed yet.");
});

test("the picker loading, failed with its list kept, and gone", () => {
  assert.equal(pickerView(state({ loading: true }), null).state, "loading");
  const failure = { status: 500, why: "projects' page server answered 500", said: null };
  const kept = pickerView(state({ failure, value: { projects: [PROJECT] } }), null);
  assert.equal(kept.state, "failed");
  assert.equal(kept.line, failure.why);
  assert.equal(kept.choices.length, 1);
  assert.equal(pickerView(state({ gone: true }), null).state, "gone");
});

test("a choice sends the key and nothing else", () => {
  assert.deepEqual(pickedDetail("alpha"), { key: "alpha" });
  assert.deepEqual(Object.keys(pickedDetail("alpha")), ["key"]);
});

// -- the card ---------------------------------------------------------------------------------

test("the card shows kind, root, lead and the associated agents", () => {
  const view = cardView(state({ value: { project: PROJECT } }), "ALPHA");
  assert.equal(view.state, "up");
  assert.equal(view.key, "alpha");
  assert.deepEqual(view.fields.map((f) => [f.name, f.value]),
                   [["kind", "project"], ["root", "/w/alpha"], ["lead", "lead-a"], ["agents", "helper, scout"]]);
  assert.deepEqual(cardFields(BETA).map((f) => f.value), ["agent", "/w/beta", "none", "none"]);
  assert.equal(cardPath("delta.web"), "api/project?key=delta.web");
  assert.equal(cardPath("a&b"), "api/project?key=a%26b");
});

test("the card for a missing key shows the refusal and nothing read before", () => {
  const failure = { status: 422, why: "projects' page server answered 422",
                    said: "no project nope; fix: moxi projects list" };
  const view = cardView(state({ failure, value: { project: PROJECT } }), "nope");
  assert.deepEqual(view, { key: "nope", state: "refused", fields: [],
                           line: "no project nope; fix: moxi projects list" });
});

test("the card loading, failed with what was read kept, and gone", () => {
  assert.equal(cardView(state({ loading: true }), "alpha").state, "loading");
  const failure = { status: 0, why: "no answer from the shell", said: null };
  const kept = cardView(state({ failure, value: { project: PROJECT } }), "alpha");
  assert.equal(kept.state, "failed");
  assert.equal(kept.fields.length, 4);
  assert.equal(kept.line, "no answer from the shell");
  assert.equal(cardView(state({ gone: true }), "alpha").state, "gone");
});

// -- the add form -----------------------------------------------------------------------------

test("the form starts at the wanted kind, else project, else the first", () => {
  assert.equal(startKind(KINDS, "topic"), "topic");
  assert.equal(startKind(KINDS, "nosuch"), "project");
  assert.equal(startKind(KINDS, null), "project");
  assert.equal(startKind({ found: [KINDS.found[2]], broken: [] }, null), "topic");
  assert.equal(startKind(null, "topic"), null);
});

test("create is hidden for a refuse kind and while the kinds are unknown", () => {
  assert.equal(createShown(KINDS, "project"), true);
  assert.equal(createShown(KINDS, "topic"), true);
  assert.equal(createShown(KINDS, "shelf"), false);
  assert.equal(createShown(KINDS, "nosuch"), false);
  assert.equal(createShown(null, "project"), false);
  assert.match(createLine(KINDS, "project"), /git init/);
  assert.match(createLine(KINDS, "topic"), /no git/);
  assert.equal(createLine(KINDS, "shelf"), null);
});

test("a kind is named with its module when it isn't projects'; broken ones are listed", () => {
  assert.equal(kindLabel(KINDS.found[1]), "project");
  assert.equal(kindLabel(KINDS.found[2]), "topic (notes)");
  assert.deepEqual(brokenLines(KINDS), ["fake: its import failed: no module named x"]);
  assert.deepEqual(brokenLines(null), []);
});

test("the add's body leaves out what is empty, and what the form doesn't offer", () => {
  const form = { ...emptyForm("project"), key: " alpha ", root: "/w/alpha", create: true };
  assert.deepEqual(addBody(form, { kinds: KINDS, notes: false }),
                   { key: "alpha", root: "/w/alpha", kind: "project", create: true });
  assert.deepEqual(addBody({ ...emptyForm("topic"), key: "t" }, { kinds: KINDS, notes: true }),
                   { key: "t", kind: "topic" });
  const all = { key: "a", root: "/r", kind: "project", lead: " lead-a ", notesRoot: "/n", create: false };
  assert.deepEqual(addBody(all, { kinds: KINDS, notes: true }),
                   { key: "a", root: "/r", kind: "project", lead: "lead-a", notes_root: "/n" });
  assert.equal(addBody(all, { kinds: KINDS, notes: false }).notes_root, undefined);
  assert.equal(addBody({ ...all, kind: "shelf", create: true }, { kinds: KINDS, notes: false }).create,
               undefined);
});

test("the root is sent as typed, so the page server refuses a relative one", () => {
  assert.equal(addBody({ ...emptyForm("project"), key: "a", root: "~/x" }, { kinds: KINDS, notes: false }).root,
               "~/x");
});

test("an add's answer: done, refused with its line, or unknown", () => {
  const written = { project: PROJECT, notes: ["made /w/alpha and ran git init"] };
  const done = actedOf(200, written, parseWritten);
  assert.deepEqual(done, { outcome: "done", value: written });
  assert.deepEqual(addSaid(done, true),
                   { tone: "ok", lines: ["Added alpha at /w/alpha.", "made /w/alpha and ran git init"] });
  const refused = actedOf(422, { refused: "key alpha is taken; fix: choose another" }, parseWritten);
  assert.deepEqual(refused, { outcome: "refused", code: 422, text: "key alpha is taken; fix: choose another" });
  assert.deepEqual(addSaid(refused, false), { tone: "error", lines: ["key alpha is taken; fix: choose another"] });
  assert.equal(actedOf(409, { conflict: "the store is busy; try again" }, parseWritten).text,
               "the store is busy; try again");
  assert.equal(actedOf(422, null, parseWritten).text, "projects' page server refused it (422)");
});

test("an answer that never came, or that came from the shell, says what is known", () => {
  for (const [status, data] of [[0, null], [504, null], [502, { error: "upstream" }], [500, null],
                                [200, { project: null }]]) {
    assert.deepEqual(actedOf(status, data, parseWritten), { outcome: "unknown", text: MAY_HAVE_ACTED }, String(status));
  }
  for (const error of ["start", "unreachable"]) {
    assert.deepEqual(actedOf(502, { error }, parseWritten), { outcome: "refused", code: 502, text: NOT_SENT });
  }
  assert.equal(actedOf(503, { error: "stopping" }, parseWritten).text, NOT_SENT);
  assert.match(actedOf(503, null, parseWritten).text, /too busy/);
  const lost = actedOf(0, null, parseWritten);
  assert.deepEqual(addSaid(lost, true), { tone: "warn", lines: [MAY_HAVE_ACTED, MAY_EXIST] });
  assert.deepEqual(addSaid(lost, false), { tone: "warn", lines: [MAY_HAVE_ACTED] });
});
