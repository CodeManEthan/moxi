import assert from "node:assert/strict";
import test from "node:test";

import { parsePasts } from "../src/panel.ts";
import {
  CONTINUE_LAST, continueWords, COPIES_RESUME, lastSession, ofPlace, RESUMES_IN_SLOT, resumeBody, SESSIONS_READ,
  takesBriefFile,
} from "../src/plus-model.ts";

const HOME = "/home/alice";
let made = 0;
/** One past session, newest first by the order a test lists them. */
const past = (extra = {}) => ({
  request_id: `r${(made += 1)}`, created_at: "2026-11-10T18:00:00Z", harness: "claude", target: "slot", slot: "2",
  cwd: "/home/alice/code/proj-a", project_key: "proj-a", actor: "operator", identity: null, state: "ended",
  session_id: `s${made}`, live: false, resumable: true, why: null, hint: null, unread: null, held_by: null,
  resume_of: null, relaunch_of: null, ...extra,
});
const PROJECT = { kind: "project", key: "proj-a" };
const FOLDER = { kind: "folder", path: "~/notes" };

test("the menu reads the twenty newest sessions", () => {
  assert.equal(SESSIONS_READ, "api/sessions?limit=20");
  assert.equal(CONTINUE_LAST, "Continue the last session");
});

test("the session offered is the newest resumable one of the line's project", () => {
  const live = past({ resumable: false, why: "live" });
  const other = past({ project_key: "proj-b", cwd: "/srv/proj-b" });
  const mine = past();
  const older = past();
  assert.equal(lastSession([live, other, mine, older], PROJECT, "operator", HOME), mine);
  assert.equal(lastSession([live, other], PROJECT, "operator", HOME), null);
  assert.equal(lastSession([], PROJECT, "operator", HOME), null);
});

test("a session of another actor in the same place is never offered", () => {
  const worker = past({ actor: "planner" });
  const mine = past();
  assert.equal(lastSession([worker, mine], PROJECT, "operator", HOME), mine);
  assert.equal(lastSession([worker], PROJECT, "operator", HOME), null);
  // An answer that names no actor, or a piece that has not read its own, offers nothing.
  assert.equal(lastSession([past({ actor: undefined })], PROJECT, "operator", HOME), null);
  assert.equal(lastSession([mine], PROJECT, "", HOME), null);
});

test("a folder on the line takes a session by its folder, however the home folder is written", () => {
  const there = past({ project_key: null, cwd: "/home/alice/notes/" });
  const inProject = past({ cwd: "/home/alice/notes" });
  const elsewhere = past({ project_key: null, cwd: "/home/alice/notes/deeper" });
  assert.equal(lastSession([elsewhere, there], FOLDER, "operator", HOME), there);
  // Its project does not matter when the line names a folder.
  assert.equal(ofPlace(inProject, FOLDER, HOME), true);
  assert.equal(ofPlace(there, { kind: "folder", path: "/home/alice/notes" }, HOME), true);
  // A project on the line takes only its own key, whatever the folder.
  assert.equal(ofPlace(there, PROJECT, HOME), false);
  assert.equal(ofPlace(inProject, { kind: "choose" }, HOME), false);
  assert.equal(lastSession([there], null, "operator", HOME), null);
});

test("a session with no id to resume is not offered", () => {
  assert.equal(lastSession([past({ session_id: null })], PROJECT, "operator", HOME), null);
});

test("the entry's dim line says what a click does, and the click sends the session alone", () => {
  assert.equal(continueWords(past()), RESUMES_IN_SLOT);
  assert.equal(continueWords(past({ target: "terminal", slot: null })), COPIES_RESUME);
  assert.deepEqual(resumeBody(past({ session_id: "abc" })), { session: "abc" });
});

test("the sessions read keeps project_key and actor", () => {
  const read = parsePasts({ state: "up", detail: null, sessions: [past({ project_key: null, actor: "alice" })] });
  assert.deepEqual([read.sessions[0].project_key, read.sessions[0].actor], [null, "alice"]);
});

test("a brief file is offered only where the form has the row", () => {
  assert.equal(takesBriefFile([{ name: "brief_file", type: "path" }]), true);
  assert.equal(takesBriefFile([{ name: "brief", type: "string" }]), false);
  assert.equal(takesBriefFile([{ name: "brief_file", type: "path", needs_absent: true }]), false);
});
