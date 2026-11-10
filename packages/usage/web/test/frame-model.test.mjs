import assert from "node:assert/strict";
import test from "node:test";

import {
  costPath, filesByHarness, frameModel, INDEX_COMMAND, newestDay, passLine, shouldPass,
} from "../src/frame-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;
const TODAY = "2026-11-08";
const NOW = at("2026-11-08T19:12:00Z");
const PASS = at("2026-11-08T19:10:00Z");

const read = (value, extra = {}) => ({ value, loading: false, failure: null, refused: null, gone: false, ...extra });
const OUT = read(null, { loading: true });

const lane = (name, files, extra = {}) => ({ lane: name, harness: name, state: "online", reason: null, root: null,
  files, held: 0, held_reasons: [], requests: files * 3, ...extra });
const status = (extra = {}, total = {}) => ({
  today: TODAY, lanes: [lane("claude", 72), lane("codex", 8), lane("opencode", 1)],
  total: { files: 81, held: 0, requests: 680, held_rows: [], last_pass: PASS, index_version: 7, ...total },
  index: { passed: false, last_pass: PASS, note: "no pass ran first; this report reads the index as it is" },
  ...extra });

const sums = (extra = {}) => ({ requests: 1, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], unpriced_models: [], rules: [], ...extra });
const daily = (rows = [sums({ period: TODAY, model: "m-a", cost: 19.54, requests: 204 })]) => ({
  today: TODAY, since: null, until: null, tz: "UTC", filters: [], rows, total: sums(), unpriced_models: [],
  rules: [], index: { passed: false, last_pass: PASS, note: null } });

const window = (extra = {}) => ({ harness: "codex", limit_id: "codex", window_minutes: 300, label: "5h",
  state: "fresh", reason: null, used_percent: 34, resets_at: "2026-11-08T21:10:00Z",
  observed_at: "2026-11-08T18:38:00Z", full_at: null, opened: "2026-11-08T16:10:00Z", projection: null,
  note: null, dollars: [], unknown: [], ...extra });
const limits = (rows = [window()], notes = []) => ({ today: TODAY, rows, pull: null, notes });

const facts = (extra = {}) => ({ limits: read(limits()), status: read(status()), daily: read(daily()),
  event: null, pass: null, most: null, range: null, others: [], unknownAddress: false, now: NOW, ...extra });
const frame = (extra = {}) => frameModel(facts(extra), UTC);
const texts = (found) => found.lines.map((line) => line.text);

test("Reading… shows until the first read answers, and nothing else does", () => {
  const first = frame({ limits: OUT, status: OUT, daily: OUT });
  assert.equal(first.reading, "Reading…");
  assert.deepEqual([first.limits.groups, first.nothing, first.cost, first.below, first.foot, first.lines],
                   [[], null, null, false, null, []]);
  assert.equal(frame({ status: OUT, daily: OUT }).reading, null);
  assert.equal(frame({ limits: OUT, daily: OUT }).reading, null);
  assert.equal(frame({ limits: OUT, status: OUT }).reading, null);
});

test("until the status answers only the limits are drawn", () => {
  const found = frame({ status: OUT });
  assert.deepEqual(found.limits.groups.map((group) => group.name), ["Codex"]);
  assert.deepEqual([found.cost, found.below, found.foot, found.nothing], [null, false, null, null]);
});

test("an index with requests: the limits, Cost, the place below it and the foot", () => {
  const found = frame({ most: "most to planner $4.21" });
  assert.equal(found.nothing, null);
  assert.equal(found.below, true);
  assert.deepEqual(found.cost.rows.map((row) => row.label), ["Today", "7 days", "30 days"]);
  assert.equal(found.cost.rows[0].figure, "$19.54 over 204 requests");
  assert.deepEqual([found.cost.label, found.cost.most, found.cost.caption, found.cost.prices, found.cost.read],
                   ["Cost", "most to planner $4.21", "Estimated from a local price table. Not billing data.", "Prices",
                    "sessions read 19:10"]);
  assert.deepEqual(found.foot, { line: "Sessions read 19:10 · 81 files: Claude 72, Codex 8, OpenCode 1",
                                 running: false, lastPass: PASS, act: "Read new sessions" });
  assert.deepEqual(found.lines, []);
  assert.equal(found.today, TODAY);
});

test("Cost is absent until its report answers, and the foot stands without it", () => {
  const found = frame({ daily: OUT });
  assert.equal(found.cost, null);
  assert.equal(found.foot.act, "Read new sessions");
  assert.equal(frame().cost.most, null);
});

test("the foot names only the harnesses with files, and counts one file as one", () => {
  const only = frame({ status: read(status({ lanes: [lane("claude", 72), lane("codex", 0), lane("opencode", 1)] })) });
  assert.equal(only.foot.line, "Sessions read 19:10 · 73 files: Claude 72, OpenCode 1");
  const one = frame({ status: read(status({ lanes: [lane("codex", 1)] })) });
  assert.equal(one.foot.line, "Sessions read 19:10 · 1 file: Codex 1");
  const archive = [lane("claude", 2), lane("archive:old", 3, { harness: "claude" }), lane("pilot", 4)];
  assert.deepEqual(filesByHarness(archive), [{ harness: "claude", files: 5 }, { harness: "pilot", files: 4 }]);
  const never = frame({ status: read(status({ lanes: [lane("claude", 0)] }, { last_pass: null })) });
  assert.deepEqual([never.foot.line, never.foot.act], [null, "Read new sessions"]);
});

test("while a pass runs the foot leads with it and the act is absent", () => {
  const found = frame({ event: { running: true, last_pass: PASS, note: null } });
  assert.deepEqual(found.foot, { line: "Reading new sessions… · 81 files: Claude 72, Codex 8, OpenCode 1",
                                 running: true, lastPass: PASS, act: null });
  const ended = frame({ event: { running: false, last_pass: PASS + 240, note: null } });
  assert.equal(ended.foot.line, "Sessions read 19:14 · 81 files: Claude 72, Codex 8, OpenCode 1");
  assert.equal(ended.cost.read, "sessions read 19:14");
  assert.equal(ended.foot.act, "Read new sessions");
});

test("the last pass is worded against the newest day any answer carried", () => {
  const next = frame({ limits: read({ ...limits(), today: "2026-11-09" }) });
  assert.equal(next.today, "2026-11-09");
  assert.equal(next.foot.line, "Sessions read yesterday 19:10 · 81 files: Claude 72, Codex 8, OpenCode 1");
  assert.equal(newestDay(null, undefined, "2026-11-08", "2026-11-07"), "2026-11-08");
  assert.equal(newestDay(null, undefined), null);
  assert.equal(newestDay("", "soon", "2026-11-08T23:00:00Z", "2026-11-07"), "2026-11-07");
  assert.equal(newestDay("", "soon"), null);
  assert.equal(costPath(""), "api/daily?since=&until=");
});

test("an index with no request: the block with when it last looked, and the act", () => {
  const found = frame({ status: read(status({}, { requests: 0 })) });
  assert.deepEqual(found.nothing, {
    state: "empty", title: "Nothing counted yet",
    text: "Usage adds up what your sessions cost from the transcripts on this machine, and shows your plan "
      + "limits once a session has reported them. It found no sessions when it last looked, at 19:10.",
    act: "Read new sessions", running: null, command: null });
  assert.deepEqual([found.cost, found.below, found.foot], [null, false, null]);
  assert.deepEqual(found.limits.groups.map((group) => group.name), ["Codex"]);
  const earlier = frame({ status: read(status({}, { requests: 0, last_pass: PASS - 86400 })) });
  assert.match(earlier.nothing.text, /when it last looked, yesterday at 19:10\.$/);
  const never = frame({ status: read(status({}, { requests: 0, last_pass: null })) });
  assert.match(never.nothing.text, /reported them\.$/);
  const running = frame({ status: read(status({}, { requests: 0 })), event: { running: true, last_pass: null, note: null } });
  assert.deepEqual([running.nothing.act, running.nothing.running], [null, "Reading new sessions…"]);
});

test("no index, or one of another version: the block without its last sentence, and the command", () => {
  const refusedDaily = read(null, { refused: "there is no index; run `moxi usage index` to build it" });
  for (const state of [
    status({ index: { passed: false, last_pass: null, note: "there is no index; run `moxi usage index` to build it" } },
           { index_version: 0, requests: 0, last_pass: null }),
    status({ index: { passed: false, last_pass: PASS, note: "an older usage wrote this index; run `moxi usage index`" } }),
  ]) {
    const found = frame({ status: read(state), daily: refusedDaily, pass: "run `moxi usage index` to build the index" });
    assert.deepEqual(found.nothing, {
      state: "none", title: "Nothing counted yet",
      text: "Usage adds up what your sessions cost from the transcripts on this machine, and shows your plan "
        + "limits once a session has reported them.",
      act: null, running: null, command: INDEX_COMMAND });
    assert.deepEqual([found.cost, found.below, found.foot, found.lines], [null, false, null, []]);
    assert.equal(found.limits.groups.length, 1);
    assert.equal(shouldPass(state, NOW), false);
  }
});

test("a status that cannot be read hides nothing and says so", () => {
  const found = frame({ status: read(null, { failure: { why: "usage's page server answered 500" } }) });
  assert.deepEqual([found.below, found.foot, found.nothing], [true, null, null]);
  assert.equal(found.cost.rows.length, 3);
  assert.equal(found.cost.read, null);
  assert.deepEqual(texts(found), ["The index's status can't be read: usage's page server answered 500."]);
});

test("a lane the last pass did not read has a line; one that was never there has none", () => {
  const lanes = [lane("claude", 72), lane("codex", 8, { state: "offline", reason: "its folder can't be listed" }),
                 lane("opencode", 0, { state: "absent", reason: "the OpenCode database /h/opencode.db is missing" }),
                 lane("archive:old", 2, { harness: "claude", state: "held" }),
                 lane("pilot", 0, { state: null })];
  assert.deepEqual(texts(frame({ status: read(status({ lanes })) })), [
    "Codex not read: offline, its folder can't be listed", "Claude (archive:old) not read: held",
    "Pilot not read yet"]);
});

test("only a folder that was never there has no line", () => {
  const line = (...lanes) => texts(frame({ status: read(status({ lanes: [lane("claude", 72), ...lanes] })) }));
  // Never there: the pass could not list it, the index holds nothing for it, and the
  // reason is that it does not exist.
  assert.deepEqual(line(lane("codex", 0, { state: "absent", reason: "sessions root does not exist (unmounted?)" })), []);
  assert.deepEqual(line(lane("opencode", 0, { state: "absent", reason: "the OpenCode database /h/opencode.db is missing" })), []);
  assert.deepEqual(line(lane("codex", 0, { state: "absent" })), []);
  // There, and it could not be read: the index holds nothing for it, so the pass calls
  // it absent too, and the line still shows.
  assert.deepEqual(line(lane("codex", 0, { state: "absent", reason: "sessions root unreadable: Permission denied" })),
                   ["Codex not read: absent, sessions root unreadable: Permission denied"]);
  assert.deepEqual(line(lane("codex", 0, { state: "absent", reason: "sessions root is not a directory (unmounted?)" })),
                   ["Codex not read: absent, sessions root is not a directory (unmounted?)"]);
  assert.deepEqual(line(lane("opencode", 0, { state: "absent", reason: "the OpenCode database /h/opencode.db cannot be read" })),
                   ["OpenCode not read: absent, the OpenCode database /h/opencode.db cannot be read"]);
  // There, unreadable, with files from an earlier pass.
  assert.deepEqual(line(lane("codex", 8, { state: "offline", reason: "sessions root unreadable: Permission denied", held: 8 })),
                   ["Codex not read: offline, sessions root unreadable: Permission denied"]);
  // Read on an earlier pass and gone now: its files are kept, so it is offline.
  assert.deepEqual(line(lane("codex", 8, { state: "offline", reason: "sessions root does not exist (unmounted?)", held: 8 })),
                   ["Codex not read: offline, sessions root does not exist (unmounted?)"]);
  // Read on an earlier pass, and its folder lists nothing now.
  assert.deepEqual(line(lane("codex", 8, { state: "held", reason: "codex listed no sources at /h/sessions while the index holds rows for it", held: 8 })),
                   ["Codex not read: held, codex listed no sources at /h/sessions while the index holds rows for it"]);
});

test("held files are counted with why", () => {
  const held = (lanes, total) => texts(frame({ status: read(status({ lanes }, total)) }));
  assert.deepEqual(held([lane("claude", 5, { held: 3, held_reasons: [{ name: "written to just now", value: 3 }] })],
                        { held: 3 }), ["3 files held back: written to just now"]);
  assert.deepEqual(held([lane("claude", 5, { held_reasons: [{ name: "too new", value: 1 }, { name: "locked", value: 2 }] })],
                        { held: 3 }), ["3 files held back: locked (2); too new (1)"]);
  assert.deepEqual(held([lane("claude", 5)], { held: 1, held_rows: [{ lane: "claude", path: "x", part: "", reason: "locked" }] }),
                   ["1 file held back: locked"]);
  assert.deepEqual(held([lane("claude", 5)], { held: 2 }), ["2 files held back"]);
});

test("the range's total that is not whole says why, and a whole one says nothing", () => {
  const sum = { rows: 2, requests: 8, tokens: 100, cost: 19.54, complete: false, unknown_rows: 0,
                unpriced_tokens: 12300, unpriced_models: ["mystery-9"], harness_cost: null };
  assert.deepEqual(texts(frame({ range: sum })),
                   ["This range's total is not whole: 12.3k tokens have no price (mystery-9)"]);
  assert.deepEqual(texts(frame({ range: { ...sum, complete: true, unpriced_tokens: 0, unpriced_models: [] } })), []);
});

test("the limits answer's notes are kept as they came", () => {
  const found = frame({ limits: read(limits([window()], ["the newest rollout could not be read: it is cut short"])) });
  assert.deepEqual(texts(found), ["the newest rollout could not be read: it is cut short"]);
});

test("a window that could not be read has a line only for a harness with files, and never for missing turns", () => {
  const away = (extra) => window({ state: "unavailable", used_percent: null, resets_at: null, observed_at: null,
                                   opened: null, ...extra });
  const rows = [
    away({ reason: "no rollout holds a reading" }),
    away({ window_minutes: 10080, label: "7d", reason: "no rollout holds a reading" }),
    away({ harness: "claude", limit_id: "plan", reason: "turns is not installed" }),
    away({ harness: "pilot", limit_id: "pilot", reason: "nothing to read" }),
  ];
  const found = frame({ limits: read(limits(rows)) });
  assert.deepEqual(found.limits.groups, []);
  assert.deepEqual(texts(found), ["Codex 5-hour limit not read: no rollout holds a reading",
                                  "Codex Weekly limit not read: no rollout holds a reading"]);
  const noFiles = frame({ limits: read(limits(rows)), status: read(status({ lanes: [lane("claude", 72)] })) });
  assert.deepEqual(texts(noFiles), []);
  assert.deepEqual(texts(frame({ limits: read(limits(rows)), status: OUT })), []);
});

test("a read that failed, was refused or is gone has a line with its reason; a kept value still draws", () => {
  const found = frame({
    limits: read(limits(), { failure: { why: "no answer from the shell" } }),
    daily: read(null, { refused: "since must be a date." }),
    others: [{ what: "The chart", read: read(null, { gone: true }), report: true },
             { what: "The sessions", read: OUT, report: true }],
  });
  assert.deepEqual(texts(found), [
    "The limits can't be read: no answer from the shell.", "Cost can't be read: since must be a date.",
    "The chart can't be read: usage's page server has no such read."]);
  assert.equal(found.limits.groups.length, 1);
  assert.equal(found.cost, null);
});

test("a pass that ended with a note, a refused pass and an unknown address each have a line", () => {
  const ended = frame({ event: { running: false, last_pass: PASS, note: "the pass failed (OSError); the log says why" } });
  assert.deepEqual(texts(ended), ["the pass failed (OSError); the log says why"]);
  const refused = frame({ pass: "another pass holds the index" });
  assert.deepEqual(texts(refused), ["another pass holds the index"]);
  const same = frame({ event: { running: false, last_pass: PASS, note: "the index is busy" }, pass: "the index is busy" });
  assert.deepEqual(texts(same), ["the index is busy"]);
  assert.deepEqual(texts(frame({ unknownAddress: true })), ["There is no such view here."]);
  assert.equal(passLine({ outcome: "refused", text: "another pass holds the index" }), "another pass holds the index");
  assert.equal(passLine({ outcome: "failed", why: "no answer from the shell" }),
               "The pass could not be started: no answer from the shell.");
  assert.equal(passLine({ outcome: "started" }), null);
  assert.equal(passLine({ outcome: "running" }), null);
});

test("the lines come in one order: lanes, held files, the range, notes, windows, reads, the pass, the address", () => {
  const found = frame({
    status: read(status({ lanes: [lane("claude", 72), lane("codex", 8, { state: "offline", reason: "gone" })] }, { held: 1 })),
    limits: read(limits([window({ state: "unavailable", reason: "no reading" })], ["a note"]),
                 { failure: { why: "no answer from the shell" } }),
    range: { rows: 1, requests: 1, tokens: 1, cost: null, complete: false, unknown_rows: 0, unpriced_tokens: 5,
             unpriced_models: [], harness_cost: null },
    pass: "refused", unknownAddress: true,
  });
  assert.deepEqual(found.lines.map((line) => line.key),
                   ["lane:codex", "held", "range", "note:0", "window:0", "read:0", "pass", "address"]);
  assert.equal(new Set(found.lines.map((line) => line.key)).size, found.lines.length);
});

test("the page asks for a pass as it opens only over an index whose last pass is over ten minutes old", () => {
  assert.equal(shouldPass(null, NOW), false);
  assert.equal(shouldPass(status(), NOW), false);
  assert.equal(shouldPass(status({}, { last_pass: NOW - 601 }), NOW), true);
  assert.equal(shouldPass(status({}, { last_pass: null }), NOW), true);
});

test("the Cost rows' report is asked over the 90 days to today", () => {
  assert.equal(costPath("2026-11-08"), "api/daily?since=2026-08-11&until=2026-11-08");
});
