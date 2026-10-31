import assert from "node:assert/strict";
import test from "node:test";

import {
  headline, launchLabel, NOT_LAUNCHED, said, SERVER_ERROR, sessionsView, stateLines, stateView,
  summary, UNREADABLE, when,
} from "../src/views.ts";

const T = "2026-10-31T10:01:02.123456Z";
const run = (extra) => ({ run_id: "r1", kind: "relaunch", mode: "self", state: "ended",
                          outcome: "relaunched", word: null, detail: null, created_at: T,
                          updated_at: T, ...extra });
const entry = (extra) => ({
  session_id: "s1", request_id: "q1",
  relaunch: { state: null, run: null, made_by: null, notices_refused: [], note_not_taken: false },
  recycle: { on_expiry: "off", at_limit: "off", limit_at: null, last: null, pending_compact: null,
             told_at: null, pending_dropped_at: null },
  keep_warm: { on: false, holds: [], nudges: 0, last: null, window: null },
  red: false, since: null, why: null, word: null, run_id: null, unreadable: null, ...extra });
const RED = entry({ red: true, word: "blocked", since: T, why: "person: someone is typing",
                    run_id: "r1" });
const UNREAD = entry({ relaunch: null, recycle: null, keep_warm: null,
                       unreadable: "reading this launch's state failed (OSError)" });
const up = (sessions, refused = null) => ({ loading: false, gone: false, failure: null,
                                             value: { state: "up", detail: null, sessions, refused } });
const LAUNCH = { request_id: "q1", created_at: T, updated_at: T, harness: "claude", target: "slot",
                 state: "first-sent", reason: null, stale: false, preset_kind: null,
                 preset_name: null, slot: "3", cwd: "/work/app", identity: "reviewer",
                 session_id: "s1", resume_of: null, relaunch_of: null };
const launches = (rows) => ({ loading: false, gone: false, failure: null,
                              value: { state: "up", detail: null, launches: rows } });

test("a store time is cut to the minute, in UTC", () => {
  assert.equal(when(T), "2026-10-31 10:01 UTC");
  assert.equal(when("soon"), "soon");
  assert.equal(when(null), "");
});

test("a run says its state, or its outcome once ended, and its word", () => {
  assert.equal(said(run()), "relaunched");
  assert.equal(said(run({ state: "waiting: person", outcome: null })), "waiting: person");
  assert.equal(said(run({ outcome: "blocked", word: "person" })), "blocked (person)");
  assert.equal(said(run({ outcome: null })), "ended");
  assert.equal(said(run({ outcome: "failed", word: "failed" })), "failed");
});

test("the lines say what `moxi launcher state` prints", () => {
  const busy = entry({
    relaunch: { state: "relaunch-failed", run: run({ outcome: "relaunch-failed", detail: "pane gone" }),
                made_by: null, notices_refused: ["lead", "ops"], note_not_taken: true },
    recycle: { on_expiry: "compact", at_limit: "relaunch", limit_at: "80%",
               last: run({ kind: "expiry", outcome: "compacted" }), pending_compact: T, told_at: T,
               pending_dropped_at: null },
    keep_warm: { on: true, holds: ["review"], nudges: 2, last: run({ kind: "keep-warm", outcome: "nudged" }),
                 window: "no window: lifetime 300 s" },
  });
  assert.deepEqual(stateLines(busy), [
    "relaunch: relaunch-failed: pane gone; its note to the session was not taken; notice refused by lead, ops",
    "recycle: on expiry compact, at limit relaunch (80%); last: expiry compacted at 2026-10-31 10:01 UTC; "
      + "a compact pending since 2026-10-31 10:01 UTC; told to recycle itself at 2026-10-31 10:01 UTC",
    "keep warm: on (no window: lifetime 300 s); holds: review; nudges in a row: 2; last: nudged at 2026-10-31 10:01 UTC",
  ]);
  assert.deepEqual(stateLines(entry()), [
    "relaunch: none", "recycle: on expiry off, at limit off", "keep warm: off"]);
  assert.deepEqual(stateLines(entry({ keep_warm: { on: true, holds: [], nudges: 0, last: null, window: null } }))[2],
                   "keep warm: on; holds: none");
  assert.deepEqual(stateLines(UNREAD), []);
});

test("the headline is ok, or the server's word, since when and why", () => {
  assert.equal(headline(entry()), "ok");
  assert.equal(headline(RED), "blocked since 2026-10-31 10:01 UTC: person: someone is typing");
  assert.equal(headline(UNREAD), "the state could not be read: reading this launch's state failed (OSError)");
});

test("the summary is the red word, or what is under way, or ok", () => {
  assert.equal(summary(entry()), "ok");
  assert.equal(summary(RED), "blocked");
  assert.equal(summary(entry({ red: true, word: "failed" })), "failed");
  assert.equal(summary(UNREAD), UNREADABLE);
  const open = entry({
    relaunch: { state: "armed", run: run({ state: "armed", outcome: null }), made_by: null,
                notices_refused: [], note_not_taken: false },
    recycle: { ...entry().recycle, pending_compact: T },
    keep_warm: { on: true, holds: [], nudges: 0, last: null, window: null },
  });
  assert.equal(summary(open), "relaunch armed · compact pending · warm");
  // An ended relaunch is not under way.
  assert.equal(summary(entry({ relaunch: { state: "relaunched", run: run(), made_by: null,
                                           notices_refused: [], note_not_taken: false } })), "ok");
});

test("red is the server's: a failed outcome the server didn't mark red shows ok", () => {
  const notRed = entry({ relaunch: { state: "failed", run: run({ outcome: "failed" }), made_by: null,
                                     notices_refused: [], note_not_taken: false } });
  const view = stateView(up([notRed]), "s1");
  assert.equal(view.red, false);
  assert.equal(view.tone, "ok");
});

test("the state component, up and red", () => {
  assert.deepEqual(stateView(up([entry()]), "s1"), {
    state: "up", tone: "ok", red: false, word: null, text: "ok",
    title: "ok\nrelaunch: none\nrecycle: on expiry off, at limit off\nkeep warm: off",
  });
  const red = stateView(up([RED]), "s1");
  assert.equal(red.tone, "red");
  assert.equal(red.red, true);
  assert.equal(red.word, "blocked");
  assert.equal(red.text, "blocked");
  assert.match(red.title, /^blocked since 2026-10-31 10:01 UTC: person: someone is typing\n/);
});

test("the state component's other states", () => {
  assert.equal(stateView({ loading: true, gone: false, failure: null, value: null }, "s1").state, "loading");
  assert.equal(stateView({ loading: false, gone: true, failure: null, value: null }, "s1").state, "gone");
  const failed = stateView({ loading: false, gone: false, failure: { status: 0, why: "no answer from the shell" },
                             value: null }, "s1");
  assert.deepEqual([failed.state, failed.tone, failed.text, failed.title],
                   ["failed", "muted", UNREADABLE, "no answer from the shell"]);
  const error = stateView({ loading: false, gone: false, failure: null,
                            value: { state: "error", detail: null, sessions: [], refused: null } }, "s1");
  assert.deepEqual([error.state, error.title], ["error", SERVER_ERROR]);
  const refused = { code: "not-launched", row: null, problem: "no launch holds the session s9", hint: "h" };
  const unknown = stateView(up([], refused), "s9");
  assert.deepEqual([unknown.state, unknown.tone, unknown.text, unknown.title],
                   ["unknown", "muted", NOT_LAUNCHED, "no launch holds the session s9"]);
  const unread = stateView(up([UNREAD]), "s1");
  assert.deepEqual([unread.state, unread.tone, unread.red, unread.text],
                   ["unreadable", "muted", false, UNREADABLE]);
});

test("a launch's label: harness, where, identity", () => {
  assert.equal(launchLabel(LAUNCH), "claude · slot 3 · reviewer");
  assert.equal(launchLabel({ ...LAUNCH, target: "headless", slot: null, identity: null }), "claude · headless");
});

test("the sessions component labels each live session from its launch row", () => {
  const second = entry({ session_id: "s2", request_id: "q2" });
  const view = sessionsView(up([RED, second]), launches([LAUNCH]));
  assert.equal(view.state, "up");
  assert.equal(view.red, 1);
  assert.deepEqual(view.rows.map((row) => [row.session, row.label, row.cwd, row.tone, row.text]), [
    ["s1", "claude · slot 3 · reviewer", "/work/app", "red", "blocked"],
    ["s2", "launch q2", null, "ok", "ok"],
  ]);
  assert.equal(view.rows[0].head, "blocked since 2026-10-31 10:01 UTC: person: someone is typing");
  assert.equal(view.rows[0].lines.length, 3);
});

test("the launch log failing leaves the labels out and nothing else", () => {
  const failed = { loading: false, gone: false, failure: { status: 500, why: "x" }, value: null };
  const view = sessionsView(up([entry()]), failed);
  assert.equal(view.state, "up");
  assert.equal(view.rows[0].label, "launch q1");
});

test("the sessions component's other states", () => {
  const none = launches([]);
  assert.equal(sessionsView(up([]), none).state, "empty");
  assert.equal(sessionsView({ loading: true, gone: false, failure: null, value: null }, none).state, "loading");
  assert.equal(sessionsView({ loading: false, gone: true, failure: null, value: null }, none).state, "gone");
  const error = sessionsView({ loading: false, gone: false, failure: null,
                               value: { state: "error", detail: "gathering failed (OSError)", sessions: [], refused: null } }, none);
  assert.deepEqual([error.state, error.detail, error.rows], ["error", "gathering failed (OSError)", []]);
  // A failed request keeps the last rows.
  const kept = sessionsView({ ...up([RED]), failure: { status: 0, why: "no answer from the shell" } }, none);
  assert.deepEqual([kept.state, kept.rows.length, kept.red, kept.detail], ["failed", 1, 1, "no answer from the shell"]);
  const unread = sessionsView(up([UNREAD]), none);
  assert.deepEqual([unread.rows[0].tone, unread.rows[0].text, unread.rows[0].red], ["muted", UNREADABLE, false]);
});
