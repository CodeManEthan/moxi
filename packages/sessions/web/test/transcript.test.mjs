import assert from "node:assert/strict";
import test from "node:test";

import { noticeOf } from "../src/list.ts";
import { openedStatus, OPENED_WAITING } from "../src/opened.ts";
import { BACK, DETAILS, detailFields, headPath, headRow, headView, REFRESH } from "../src/transcript.ts";

// -- fixtures -----------------------------------------------------------------------------------

/** A zone the tests can read: UTC, with a 24-hour clock. */
const UTC = {
  clock: (epoch) => new Date(epoch * 1000).toISOString().slice(11, 16),
  day: (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10),
  start: (day) => Date.parse(`${day}T00:00:00Z`) / 1000,
};
const at = (text) => Date.parse(`${text.replace(" ", "T")}:00Z`) / 1000;

const TODAY = "2026-11-08";
const ID = "0190b000-0000-7000-8000-000000000001";
const TARGET = { harness: "claude", session: ID, agent: null };

const row = (over = {}) => ({
  harness: "claude", session: ID, path: "/root/one.jsonl", changed: at("2026-11-08 08:55"), size: 100, subagents: 2,
  folder: "/home/alice/code/proj-a", started: at("2026-11-08 07:40"), by_program: null, name: null, model: "model-1",
  activity: null, prompt: null, recent: false, ...over,
});
const list = (rows, over = {}) => ({
  shape: 1, now: at("2026-11-08 19:10"), today: TODAY, home: "/home/alice", all: 62, total: rows.length, unreadable: 0,
  roots: [], harnesses: [{ harness: "claude", count: rows.length }],
  groups: [{ key: "found", count: rows.length, rows, before: null }], ...over,
});
const read = (value, over = {}) => ({ loading: false, gone: false, failure: null, refused: null, value, ...over });
const head = (value, target = TARGET, over = {}) => headView(target, read(value, over), UTC);
const WINDOW = {
  shape: 1, harness: "claude", session: ID, agent: null, path: "/home/alice/.claude/projects/p/one.jsonl",
  header: { cwd: "/home/alice/code/proj-a", title: "a title the harness made", version: "2.1.314", unknown: [] },
  last_activity: at("2026-11-08 08:55"), turns: 5, has_earlier: false, before: null, ended: "start", records: [],
};

// -- the request --------------------------------------------------------------------------------

test("the header asks the list for the route's one session, by its harness and its id", () => {
  assert.equal(headPath(TARGET), `api/list?harness=claude&find=${ID}`);
  assert.equal(headPath({ harness: "opencode", session: "ses 1", agent: null }), "api/list?harness=opencode&find=ses+1");
});

test("an id the find field would not send is not asked for", () => {
  assert.equal(headPath({ harness: "claude", session: "a", agent: null }), null);
  assert.equal(headPath({ harness: "claude", session: "a".repeat(257), agent: null }), null);
});

test("the header's row is the one with the route's harness and id, whatever else the text found", () => {
  const other = row({ session: `${ID}-fork` });
  const elsewhere = row({ harness: "codex" });
  assert.equal(headRow(TARGET, list([other, elsewhere, row({ name: "mine" })])).name, "mine");
  assert.equal(headRow(TARGET, list([other, elsewhere])), null);
  assert.equal(headRow(TARGET, null), null);
});

// -- the two lines ------------------------------------------------------------------------------

test("an unnamed session is named by its folder and start, with its harness, model and span under it", () => {
  assert.deepEqual(head(list([row()])), {
    mode: "lines",
    back: { text: "Sessions", route: "" },
    name: "proj-a · started today 07:40",
    where: null,
    facts: "claude · model-1 · today 07:40 to 08:55",
    details: "Details",
    refresh: "Refresh",
    notices: [],
    home: "/home/alice",
  });
  assert.deepEqual([BACK, DETAILS, REFRESH], ["Sessions", "Details", "Refresh"]);
});

test("a session with a typed name is named by it, with its folder's last segment beside it", () => {
  const view = head(list([row({ name: "fix-tooltips" })]));
  assert.equal(view.name, "fix-tooltips");
  assert.equal(view.where, "proj-a");
});

test("the name is never the id and never a title the harness made", () => {
  const bare = head(list([row({ folder: null, started: null, model: null, changed: null })]));
  assert.equal(bare.name, "Claude Code session");
  assert.equal(bare.facts, "claude");
  for (const view of [bare, head(list([row()]))]) {
    assert.ok(!JSON.stringify(view).includes(ID));
    assert.ok(!JSON.stringify(view).includes("a title the harness made"));
  }
});

test("the span says each end's day, and a part that was not read is left out", () => {
  const over = (o) => head(list([row(o)])).facts;
  assert.equal(over({ started: at("2026-11-07 22:25"), changed: at("2026-11-08 00:10") }),
               "claude · model-1 · yesterday 22:25 to today 00:10");
  assert.equal(over({ started: at("2026-11-06 21:30"), changed: at("2026-11-06 22:10") }),
               "claude · model-1 · Nov 6 21:30 to 22:10");
  assert.equal(over({ model: null }), "claude · today 07:40 to 08:55");
  assert.equal(over({ changed: null }), "claude · model-1 · started today 07:40");
  assert.equal(over({ started: null }), "claude · model-1 · last change today 08:55");
});

// -- sessions the list does not hold, and the list on its way --------------------------------------

test("a subagent keeps the viewer's own header, whatever the list holds", () => {
  const view = head(list([row()]), { ...TARGET, agent: "a1" });
  assert.equal(view.mode, "plain");
  assert.deepEqual([view.name, view.facts, view.details], [null, null, null]);
  assert.deepEqual(view.back, { text: "Sessions", route: "" });
});

test("a session the list does not hold keeps the viewer's own header", () => {
  const view = head(list([]));
  assert.equal(view.mode, "plain");
  assert.deepEqual([view.name, view.facts, view.details], [null, null, null]);
});

test("while the list's answer is on its way neither header is drawn", () => {
  assert.equal(head(null, TARGET, { loading: true }).mode, "waiting");
  assert.equal(head(null).mode, "waiting");
  assert.equal(head(null).back.text, "Sessions");
});

test("a list that could not be read leaves the viewer's own header", () => {
  assert.equal(head(null, TARGET, { failure: { status: 500, why: "sessions' page server answered 500" } }).mode, "plain");
  assert.equal(head(null, TARGET, { refused: "sessions' config cannot be read" }).mode, "plain");
  assert.equal(head(null, TARGET, { gone: true }).mode, "plain");
  assert.equal(headView(TARGET, null, UTC).mode, "plain");
});

test("a list read before a failure still names the session", () => {
  const view = head(list([row()]), TARGET, { failure: { status: 0, why: "no answer from the shell" } });
  assert.equal(view.mode, "lines");
  assert.equal(view.name, "proj-a · started today 07:40");
});

// -- a harness that was not read whole ------------------------------------------------------------

test("a root that could not be read has the list's line, for the route's harness only", () => {
  const roots = [
    { harness: "claude", root: "/home/alice/.claude/projects", reason: "Permission denied", parts: null },
    { harness: "codex", root: "/home/alice/.codex/sessions", reason: "Permission denied", parts: null },
  ];
  const view = head(list([], { roots }));
  assert.equal(view.mode, "plain");
  assert.deepEqual(view.notices, [{ harness: "claude", line: "Moxi couldn't read Claude Code's sessions", reason: "Permission denied" }]);
  assert.deepEqual(view.notices, [noticeOf(roots[0])]);
});

test("a root with parts that could not be read has its line over a session that was", () => {
  const roots = [{ harness: "claude", root: "/home/alice/.claude/projects", reason: null, parts: 2 }];
  const view = head(list([row()], { roots }));
  assert.equal(view.mode, "lines");
  assert.deepEqual(view.notices, [{ harness: "claude", reason: null,
                                    line: "Moxi couldn't read 2 parts of Claude Code's sessions; the counts leave them out" }]);
});

test("a subagent's route carries its harness's line too", () => {
  const roots = [{ harness: "claude", root: null, reason: "the config cannot be read", parts: null }];
  assert.equal(head(list([], { roots }), { ...TARGET, agent: "a1" }).notices.length, 1);
});

// -- the read of the turns ------------------------------------------------------------------------

test("the turns' read says it is on its way, why it was refused or failed, and nothing once read", () => {
  assert.deepEqual(openedStatus(read(null, { loading: true })), { state: "waiting", line: OPENED_WAITING });
  assert.deepEqual(openedStatus(read(null, { refused: "no claude session nosuch was found (/root: miss)" })),
                   { state: "refused", line: "Moxi couldn't read the session: no claude session nosuch was found (/root: miss)" });
  assert.deepEqual(openedStatus(read(WINDOW, { failure: { status: 500, why: "sessions' page server answered 500" } })),
                   { state: "failed", line: "Moxi couldn't read the session: sessions' page server answered 500" });
  assert.deepEqual(openedStatus(read(null, { gone: true })), { state: "gone", line: "sessions is no longer installed." });
  assert.equal(openedStatus(read(WINDOW)), null);
});

// -- Details --------------------------------------------------------------------------------------

test("Details lists every fact the viewer's header has, the folder worded against the home folder", () => {
  const fields = detailFields(WINDOW, "/home/alice");
  assert.deepEqual(fields.map((one) => one.name),
                   ["harness", "session", "title", "cwd", "last_activity", "version", "path"]);
  const value = (name) => fields.find((one) => one.name === name).value;
  assert.equal(value("harness"), "Claude Code");
  assert.equal(value("session"), ID);
  assert.equal(value("title"), "a title the harness made");
  assert.equal(value("cwd"), "~/code/proj-a");
  assert.equal(value("version"), "2.1.314");
  assert.equal(value("path"), "/home/alice/.claude/projects/p/one.jsonl");
});

test("Details shows a folder that is not under the home folder, or with no home folder known, as it is", () => {
  const cwd = (answer, home) => detailFields(answer, home).find((one) => one.name === "cwd").value;
  assert.equal(cwd(WINDOW, "/home/bob"), "/home/alice/code/proj-a");
  assert.equal(cwd(WINDOW, null), "/home/alice/code/proj-a");
  assert.equal(cwd(WINDOW), "/home/alice/code/proj-a");
  assert.equal(cwd({ ...WINDOW, header: { ...WINDOW.header, cwd: null } }, "/home/alice"), "(hidden)");
});

test("Details names a subagent and its parent", () => {
  const fields = detailFields({ ...WINDOW, agent: "a1", header: { ...WINDOW.header, parent: "s0", entrypoint: "cli" } }, null);
  assert.deepEqual(fields.map((one) => one.name),
                   ["harness", "session", "agent", "title", "cwd", "last_activity", "version", "parent", "entrypoint", "path"]);
});
