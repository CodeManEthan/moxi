import assert from "node:assert/strict";
import test from "node:test";

import {
  ABSENT, addressRoute, frameOf, INSTALL_COMMAND, NO_SESSION, NONE_CAN_SHOW, NOT_RUNNING, parseAddress,
  reachLine, READING, readingLine, ROUTE_FAILED, SCAN_FAILED,
} from "../src/frame-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  seconds: (at) => new Date(at * 1000).toISOString().slice(11, 19),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const TODAY = "2026-11-18";
const at = (hhmm) => Date.parse(`${TODAY}T${hhmm}:00Z`) / 1000;
const ID = "aaaaaaaa-1111-4111-8111-111111111111";
const facts = (extra) => ({ session_id: ID, turn_number: 4, turn_open: false, turn_started_at: null,
                            agents: 0, draft: false, stale: false, ready: null, at: at("19:00"), ...extra });
const session = (extra) => ({ key: "claude-n1", harness: "claude", pid: 4321, session_id: ID,
  cwd: "/home/u/kestrel-web", started: at("08:20"), capabilities: ["agents"], endpoint_version: "0.2.4",
  mod_version: null, trouble: null, facts: facts(), agents: { entries: [], complete: true },
  read_error: null, ended_at: null, has_box: true, unread_since: null, ...extra });
const NOWHERE = { key: null, unknown: false };
const START = { claude: "start", codex: true };
const data = (extra, value) => ({ loading: false, gone: false, failure: null, readAt: at("19:00"),
  value: { listed: true, detail: null, home: "/home/u", reach: START, sessions: [session()], ...value },
  ...extra });

test("addresses: the panel, a session's row, and a route the page does not hold", () => {
  assert.deepEqual(parseAddress(""), { key: null, unknown: false });
  assert.deepEqual(parseAddress("session/claude-n1"), { key: "claude-n1", unknown: false });
  assert.deepEqual(parseAddress("session/a%20b"), { key: "a b", unknown: false });
  for (const bad of ["session/", "session/a/b", "other", "session/%E0%A4%A"]) {
    assert.deepEqual(parseAddress(bad), { key: null, unknown: true }, bad);
  }
  assert.equal(addressRoute(null), "");
  assert.equal(addressRoute("a b"), "session/a%20b");
  assert.deepEqual(parseAddress(addressRoute("claude-n1")), { key: "claude-n1", unknown: false });
});

test("every row of the reach line's table", () => {
  const BY_HAND = "Codex and OpenCode sessions started by hand don't.";
  const LAUNCHES = "Sessions Moxi launches in a terminal or a slot show here; other Codex and OpenCode sessions don't.";
  const line = (claude, launcher, codex = true) => reachLine({ claude, codex }, launcher);
  assert.deepEqual(line("start", false), { text: `Interactive Claude Code sessions show here from their start. ${BY_HAND}`,
                                           command: null, after: null });
  assert.deepEqual(line("setup", false), { text: "Interactive Claude Code sessions can't show here yet: run ",
                                           command: INSTALL_COMMAND, after: `. ${BY_HAND}` });
  assert.deepEqual(line("absent", false), { text: ABSENT, command: INSTALL_COMMAND, after: null });
  assert.deepEqual(line(null, false), { text: BY_HAND, command: null, after: null });
  assert.deepEqual(reachLine(null, false), { text: BY_HAND, command: null, after: null });
  assert.deepEqual(line("start", true), {
    text: "Interactive Claude Code sessions show here from their start, and sessions Moxi launches in a terminal "
      + "or a slot; other Codex and OpenCode sessions don't.", command: null, after: null });
  assert.deepEqual(line("setup", true), {
    text: `${LAUNCHES} Interactive Claude Code sessions started by hand can't show yet: run `,
    command: INSTALL_COMMAND, after: null });
  assert.deepEqual(line("absent", true), { text: LAUNCHES, command: null, after: null });
  assert.deepEqual(line(null, true), { text: LAUNCHES, command: null, after: null });
  assert.deepEqual(reachLine(null, true), { text: LAUNCHES, command: null, after: null });
});

test("the reach line with launcher and codex false leaves launched Codex sessions out", () => {
  const LAUNCHES = "Claude Code and OpenCode sessions Moxi launches in a terminal or a slot show here; "
    + "Codex sessions and other OpenCode sessions don't.";
  assert.equal(reachLine({ claude: null, codex: false }, true).text, LAUNCHES);
  assert.equal(reachLine({ claude: "absent", codex: false }, true).text, LAUNCHES);
  assert.equal(reachLine({ claude: "setup", codex: false }, true).text,
               `${LAUNCHES} Interactive Claude Code sessions started by hand can't show yet: run `);
  assert.equal(reachLine({ claude: "start", codex: false }, true).text,
               "Interactive Claude Code sessions show here from their start, and Claude Code and OpenCode sessions "
               + "Moxi launches in a terminal or a slot; Codex sessions and other OpenCode sessions don't.");
  // Without launcher no sentence names a launch, so codex changes nothing.
  assert.deepEqual(reachLine({ claude: "start", codex: false }, false), reachLine(START, false));
});

test("loading: no answer yet, the first scan not done, or every listed session waiting", () => {
  assert.equal(frameOf({ loading: true, gone: false, failure: null, value: null, readAt: null }, NOWHERE, false, TODAY, UTC).state,
               "loading");
  assert.equal(frameOf(data({}, { listed: false, sessions: [] }), NOWHERE, false, TODAY, UTC).state, "loading");
  const waiting = frameOf(data({}, { sessions: [session({ facts: null }), session({ key: "k2", facts: null })] }),
                          NOWHERE, false, TODAY, UTC);
  assert.equal(waiting.state, "loading");
  assert.deepEqual(waiting.groups, []);
  // One drawn row is enough to be up; the waiting one is left out.
  const one = frameOf(data({}, { sessions: [session({ facts: null }), session({ key: "k2" })] }), NOWHERE, false, TODAY, UTC);
  assert.equal(one.state, "up");
  assert.deepEqual(one.groups.map((g) => g.sessions.map((s) => s.key)), [["k2"]]);
});

test("up: groups, the reach line, no empty line", () => {
  const frame = frameOf(data(), NOWHERE, false, TODAY, UTC);
  assert.equal(frame.state, "up");
  assert.equal(frame.alert, null);
  assert.equal(frame.empty, null);
  assert.equal(frame.groups[0].label, "1 idle");
  assert.match(frame.reach.text, /^Interactive Claude Code sessions show here/);
  assert.deepEqual(frame.notes, []);
});

test("a failed route: the alert first, its status words, the last read, the last rows kept", () => {
  for (const [status, words] of [[0, "No answer from the shell."], [503, "turns' page server answered 503."],
                                 [500, "turns' page server answered 500."], [403, "turns' page server answered 403."],
                                 [200, "turns' page server sent an answer that can't be read."]]) {
    const frame = frameOf(data({ failure: { status, why: "x" } }), NOWHERE, false, TODAY, UTC);
    assert.equal(frame.state, "failed");
    assert.deepEqual(frame.alert, { lead: ROUTE_FAILED, rest: words, lastRead: "Last read 19:00. It may have changed since." });
    assert.equal(frame.groups.length, 1);
    assert.equal(frame.open, null);
  }
  const never = frameOf({ loading: false, gone: false, failure: { status: 0, why: "x" }, value: null, readAt: null },
                        NOWHERE, false, TODAY, UTC);
  assert.equal(never.alert.lastRead, null);
  assert.deepEqual(never.groups, []);
});

test("a failed scan: the alert first with its sentence, the rows as last read below it", () => {
  const frame = frameOf(data({}, { detail: "turns could not list its endpoints" }), NOWHERE, false, TODAY, UTC);
  assert.equal(frame.state, "up");
  assert.deepEqual(frame.alert, { lead: SCAN_FAILED, rest: "turns could not list its endpoints", lastRead: null });
  assert.equal(frame.groups.length, 1);
});

test("empty and the first time", () => {
  const empty = frameOf(data({}, { sessions: [] }), NOWHERE, false, TODAY, UTC);
  assert.equal(empty.state, "empty");
  assert.equal(empty.empty, NO_SESSION);
  assert.match(empty.reach.text, /^Interactive Claude Code sessions show here/);
  const first = frameOf(data({}, { sessions: [], reach: { claude: "absent", codex: true } }), NOWHERE, false, TODAY, UTC);
  assert.equal(first.state, "first");
  assert.equal(first.empty, NONE_CAN_SHOW);
  assert.deepEqual(first.reach, { text: ABSENT, command: INSTALL_COMMAND, after: null });
  // With launcher installed, absent is the plain empty state.
  const launched = frameOf(data({}, { sessions: [], reach: { claude: "absent", codex: true } }), NOWHERE, true, TODAY, UTC);
  assert.equal(launched.state, "empty");
  assert.equal(launched.empty, NO_SESSION);
  // Reach not read yet: empty, with the rows for null.
  assert.equal(frameOf(data({}, { sessions: [], reach: null }), NOWHERE, false, TODAY, UTC).state, "empty");
});

test("gone draws nothing", () => {
  const frame = frameOf(data({ gone: true }), NOWHERE, false, TODAY, UTC);
  assert.equal(frame.state, "gone");
  assert.deepEqual([frame.groups, frame.reach, frame.alert], [[], null, null]);
});

test("the address opens a drawn row, and an unknown one adds its line", () => {
  assert.equal(frameOf(data(), { key: "claude-n1", unknown: false }, false, TODAY, UTC).open, "claude-n1");
  const gone = frameOf(data(), { key: "claude-gone", unknown: false }, false, TODAY, UTC);
  assert.equal(gone.open, null);
  assert.deepEqual(gone.notes, [NOT_RUNNING]);
  assert.deepEqual(frameOf(data(), { key: null, unknown: true }, false, TODAY, UTC).notes, [NOT_RUNNING]);
  assert.deepEqual(frameOf(data({}, { sessions: [] }), { key: "x", unknown: false }, false, TODAY, UTC).notes, [NOT_RUNNING]);
  // A listed session still waiting for its first read is not called gone.
  const waiting = frameOf(data({}, { sessions: [session(), session({ key: "k2", facts: null })] }),
                          { key: "k2", unknown: false }, false, TODAY, UTC);
  assert.deepEqual([waiting.open, waiting.notes], [null, []]);
});

test("a watch that is down while the reads answer keeps the session in its group, with no panel line", () => {
  const late = session({ read_error: "Moxi can't watch it, so what shows is read every 30 s and may be up to 30 s old" });
  const frame = frameOf(data({}, { sessions: [late] }), NOWHERE, false, TODAY, UTC);
  assert.deepEqual(frame.groups.map((g) => g.group), ["idle"]);
  assert.deepEqual(frame.notes, []);
});

test("the loading state shows nothing for 400 ms, then Reading…; no other state shows it", () => {
  const loading = frameOf(data({ loading: true, value: null }), NOWHERE, false, TODAY, UTC);
  assert.equal(readingLine(loading, 0), null);
  assert.equal(readingLine(loading, 399), null);
  assert.equal(readingLine(loading, 400), READING);
  assert.equal(readingLine(frameOf(data(), NOWHERE, false, TODAY, UTC), 5000), null);
});
