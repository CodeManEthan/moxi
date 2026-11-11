import assert from "node:assert/strict";
import test from "node:test";

import { parseLimits } from "../src/feed.ts";
import { harnessName, limitLabel, limitRoute, limitsModel, percentWords, spanWords } from "../src/limits-model.ts";

/** A clock that reads every moment in UTC, on 24 hours. */
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;
const TODAY = "2026-11-08";    // a Sunday
const NOW = at("2026-11-08T19:10:00Z");

const row = (extra = {}) => ({
  harness: "claude", limit_id: "plan", window_minutes: 300, label: "5h", state: "fresh", reason: null,
  slot: null, used_percent: 49, resets_at: "2026-11-08T21:34:00Z", observed_at: "2026-11-08T19:08:00Z",
  source: "turns", session_id: null, plan_type: null, full_at: null, projection: null, fit: 0,
  opened: "2026-11-08T16:34:00Z", note: null, dollars: [], unknown: [], ...extra });
const claudeWeek = (extra = {}) => row({ window_minutes: 10080, label: "7d", used_percent: 60,
  resets_at: "2026-11-11T23:58:00Z", opened: "2026-11-04T23:58:00Z", ...extra });
const claudeOpus = (extra = {}) => claudeWeek({ limit_id: "opus", label: "7d opus", used_percent: 33, ...extra });
const codex = (extra = {}) => row({ harness: "codex", limit_id: "codex", used_percent: 34, source: "rollout",
  resets_at: "2026-11-08T21:10:00Z", observed_at: "2026-11-08T18:38:00Z", opened: "2026-11-08T16:10:00Z", ...extra });
const codexWeek = (extra = {}) => codex({ window_minutes: 10080, label: "7d", used_percent: 41,
  resets_at: "2026-11-11T19:10:00Z", opened: "2026-11-04T19:10:00Z", ...extra });
const limits = (rows, today = TODAY) => ({ today, rows, pull: null, notes: [] });
const model = (rows, now = NOW, today = TODAY) => limitsModel(limits(rows, today), now, UTC);
const labels = (group) => group.rows.map((item) => item.label);

test("an ordinary moment: Claude then Codex, each row in words, no warning", () => {
  const found = model([codex(), codexWeek(), row(), claudeWeek(), claudeOpus()]);
  assert.equal(found.warning, null);
  assert.deepEqual(found.groups.map((group) => [group.name, group.read]),
                   [["Claude", "read 19:08"], ["Codex", "read 18:38"]]);
  assert.deepEqual(labels(found.groups[0]), ["5-hour", "Weekly", "Weekly Opus"]);
  assert.deepEqual(labels(found.groups[1]), ["5-hour", "Weekly"]);
  assert.deepEqual(found.groups[0].rows[0], {
    key: "claude/plan/300", harness: "claude", limit_id: "plan", minutes: 300, state: "fresh",
    label: "5-hour", used: "49% used",
    meter: { fill: 49, tick: 52, title: "49% used, 52% of the window gone" },
    fills: null, resets: "resets 21:34", read: null, route: "limit/claude/plan/300" });
  assert.equal(found.groups[0].rows[1].resets, "resets Wed 23:58");
  assert.equal(found.groups[0].rows[2].route, "limit/claude/opus/10080");
  assert.equal(found.groups[1].rows[1].meter.title, "41% used, 57% of the window gone");
});

test("labels: the two plan windows by length, a scoped limit by name, any other as answered", () => {
  assert.equal(limitLabel(row()), "5-hour");
  assert.equal(limitLabel(claudeWeek()), "Weekly");
  assert.equal(limitLabel(claudeOpus()), "Weekly Opus");
  assert.equal(limitLabel(row({ limit_id: "opus", label: "5h opus" })), "5-hour Opus");
  assert.equal(limitLabel(codexWeek()), "Weekly");
  assert.equal(limitLabel(row({ window_minutes: 1440, label: "1440m" })), "1440m");
  assert.equal(limitLabel(row({ window_minutes: null, label: "primary" })), "primary");
  assert.equal(harnessName("opencode"), "OpenCode");
  assert.equal(harnessName("other"), "Other");
  assert.equal(limitRoute("claude", "a/b c", 300), "limit/claude/a%2Fb%20c/300");
});

test("the tick is the share of the window behind now, and is absent with no known start", () => {
  const tick = (extra, now = NOW) => model([row(extra)], now).groups[0].rows[0].meter;
  assert.equal(tick({}, at("2026-11-08T16:34:00Z")).tick, 0);
  assert.equal(tick({}, at("2026-11-08T19:04:00Z")).tick, 50);
  assert.equal(tick({}, at("2026-11-08T21:34:00Z")).tick, 100);
  assert.equal(tick({}, at("2026-11-08T23:00:00Z")).tick, 100);
  assert.equal(tick({}, at("2026-11-08T16:00:00Z")).tick, 0);
  assert.deepEqual(tick({ opened: null }), { fill: 49, tick: null, title: "49% used" });
  assert.equal(tick({ resets_at: null }).tick, null);
  assert.equal(tick({ opened: "2026-11-08T21:34:00Z" }).tick, null);
  assert.equal(tick({ used_percent: 104 }).fill, 100);
});

test("a percent is a whole number rounded down, and never says more than is used", () => {
  const first = (extra, now = NOW) => model([row(extra)], now).groups[0].rows[0];
  assert.equal(first({ used_percent: 48.6 }).used, "48% used");
  assert.equal(first({ used_percent: 48.6 }).meter.title, "48% used, 52% of the window gone");
  assert.equal(first({ used_percent: 0 }).used, "0% used");
  assert.equal(first({ used_percent: 0.4 }).used, "under 1% used");
  assert.equal(first({ used_percent: 0.4 }).meter.fill, 0);
  assert.equal(first({ used_percent: 99.6 }).used, "99% used");
  assert.equal(first({ used_percent: 99.6 }).meter.fill, 99);
  assert.equal(first({ used_percent: 100 }).used, "100% used");
  assert.equal(first({ used_percent: 104 }).used, "100% used");
  // The clock a minute into a five-hour window, and a minute from its end.
  assert.equal(first({}, at("2026-11-08T16:35:00Z")).meter.title, "49% used, under 1% of the window gone");
  assert.equal(first({}, at("2026-11-08T21:33:00Z")).meter.title, "49% used, 99% of the window gone");
  assert.deepEqual([percentWords(0), percentWords(0.01), percentWords(1), percentWords(99.99), percentWords(100)],
                   ["0%", "under 1%", "1%", "99%", "100%"]);
});

test("a scoped limit's lead names it inside the sentence", () => {
  const found = model([row(), claudeOpus({ full_at: "2026-11-10T22:29:00Z" })]);
  assert.equal(found.warning.lead, "Claude's weekly Opus limit fills Tue at 22:29");
});

test("a row states only what its reading carries", () => {
  const [bare] = model([row({ used_percent: null, resets_at: null, observed_at: null, opened: null })]).groups[0].rows;
  assert.equal(bare.used, null);
  assert.equal(bare.meter, null);
  assert.equal(bare.resets, null);
  assert.equal(bare.read, null);
  assert.equal(model([row({ observed_at: null })]).groups[0].read, null);
  const [unsized] = model([row({ window_minutes: null, label: "primary" })]).groups[0].rows;
  assert.equal(unsized.route, null);
  assert.equal(unsized.label, "primary");
});

test("a window with no reading has no row, and a harness with none has no group", () => {
  const away = { state: "unavailable", reason: "turns is not installed", used_percent: null, resets_at: null,
                 observed_at: null, opened: null };
  const found = model([row(away), claudeWeek(away), codex(), codexWeek(away)]);
  assert.deepEqual(found.groups.map((group) => group.name), ["Codex"]);
  assert.deepEqual(labels(found.groups[0]), ["5-hour"]);
  assert.deepEqual(model([row(away)]), { warning: null, groups: [] });
  assert.deepEqual(limitsModel(null, NOW, UTC), { warning: null, groups: [] });
});

test("a reading whose period has ended is one dim row: no percent, no line, nothing to open", () => {
  const ended = row({ state: "reset", resets_at: "2026-11-08T16:34:00Z", observed_at: "2026-11-08T15:02:00Z",
                      full_at: "2026-11-08T16:00:00Z" });
  const found = model([ended, claudeWeek()]);
  assert.equal(found.warning, null);
  assert.deepEqual(found.groups[0].rows[0], {
    key: "claude/plan/300", harness: "claude", limit_id: "plan", minutes: 300, state: "reset",
    label: "5-hour", used: null, meter: null, fills: null, resets: "reset 16:34", read: "read 15:02",
    route: null });
  assert.equal(model([row({ state: "reset", resets_at: null })]).groups[0].rows[0].resets, "reset");
  assert.equal(model([row({ state: "reset", resets_at: "2026-11-07T21:34:00Z" })]).groups[0].rows[0].resets,
               "reset yesterday 21:34");
});

test("the heading's read time is the newest row's, and a row read at another minute says its own", () => {
  const found = model([row({ observed_at: "2026-11-08T19:08:40Z" }), claudeWeek({ observed_at: "2026-11-08T19:08:05Z" }),
                       claudeOpus({ observed_at: "2026-11-08T17:20:00Z" })]);
  assert.equal(found.groups[0].read, "read 19:08");
  assert.deepEqual(found.groups[0].rows.map((item) => item.read), [null, null, "read 17:20"]);
  const old = model([codex({ observed_at: "2026-11-07T22:15:00Z" }), codexWeek({ observed_at: "2026-11-05T09:00:00Z" })]);
  assert.equal(old.groups[0].read, "read yesterday 22:15");
  assert.deepEqual(old.groups[0].rows.map((item) => item.read), [null, "read Thu 09:00"]);
});

test("one early row: it leads its group and the warning names it alone", () => {
  const found = model([row(), claudeWeek({ full_at: "2026-11-09T17:51:00Z" }), claudeOpus(), codex(), codexWeek()]);
  assert.deepEqual(found.warning, { lead: "Claude's weekly limit fills tomorrow at 17:51", before: "two days before it resets",
                                    more: null, text: "Claude's weekly limit fills tomorrow at 17:51, two days before it resets" });
  assert.deepEqual(labels(found.groups[0]), ["Weekly", "5-hour", "Weekly Opus"]);
  assert.equal(found.groups[0].rows[0].fills, "fills tomorrow 17:51");
  assert.equal(found.groups[0].rows[0].resets, "resets Wed 23:58");
  assert.equal(found.groups[0].rows[1].fills, null);
});

test("two early rows: the soonest leads and the line names the other", () => {
  const found = model([row(), claudeWeek({ full_at: "2026-11-09T17:51:00Z" }),
                       claudeOpus({ full_at: "2026-11-10T22:29:00Z" }), codex(), codexWeek()]);
  assert.equal(found.warning.text,
               "Claude's weekly limit fills tomorrow at 17:51, two days before it resets · weekly Opus fills Tue 22:29");
  assert.equal(found.warning.more, "weekly Opus fills Tue 22:29");
  assert.deepEqual(labels(found.groups[0]), ["Weekly", "Weekly Opus", "5-hour"]);
  const across = model([row(), claudeWeek({ full_at: "2026-11-09T17:51:00Z" }),
                        codex({ used_percent: 81, full_at: "2026-11-08T20:05:00Z" })]);
  assert.equal(across.warning.text,
               "Codex's 5-hour limit fills at 20:05, an hour before it resets · Claude weekly fills tomorrow 17:51");
  assert.deepEqual(across.groups.map((group) => group.name), ["Codex", "Claude"]);
});

test("three early rows: the soonest in full, the rest counted, the soonest group first", () => {
  const found = model([row(), claudeWeek({ full_at: "2026-11-09T17:51:00Z" }),
                       claudeOpus({ full_at: "2026-11-10T22:29:00Z" }),
                       codex({ used_percent: 81, full_at: "2026-11-08T20:05:00Z" }), codexWeek()]);
  assert.deepEqual(found.warning, {
    lead: "Codex's 5-hour limit fills at 20:05", before: "an hour before it resets",
    more: "2 more fill early, the next tomorrow 17:51",
    text: "Codex's 5-hour limit fills at 20:05, an hour before it resets · 2 more fill early, the next tomorrow 17:51" });
  assert.deepEqual(found.groups.map((group) => group.name), ["Codex", "Claude"]);
  assert.deepEqual(labels(found.groups[0]), ["5-hour", "Weekly"]);
  assert.deepEqual(labels(found.groups[1]), ["Weekly", "Weekly Opus", "5-hour"]);
  const first = found.groups[0].rows[0];
  assert.deepEqual([first.used, first.fills, first.resets], ["81% used", "fills 20:05", "resets 21:10"]);
  assert.equal(found.groups[1].rows[1].fills, "fills Tue 22:29");
  const sameDay = model([row({ full_at: "2026-11-08T20:40:00Z" }), claudeWeek({ full_at: "2026-11-09T17:51:00Z" }),
                         codex({ full_at: "2026-11-08T20:05:00Z" })]);
  assert.equal(sameDay.warning.more, "2 more fill early, the next at 20:40");
});

test("how long before the reset is said in whole units, never more than it is", () => {
  assert.equal(spanWords(59), null);
  assert.equal(spanWords(60), "a minute");
  assert.equal(spanWords(40 * 60), "40 minutes");
  assert.equal(spanWords(5 * 60), "five minutes");
  assert.equal(spanWords(65 * 60), "an hour");
  assert.equal(spanWords(119 * 60), "an hour");
  assert.equal(spanWords(23 * 3600 + 3599), "23 hours");
  assert.equal(spanWords(24 * 3600), "a day");
  assert.equal(spanWords(2 * 86400 + 6 * 3600), "two days");
  const close = model([row({ full_at: "2026-11-08T21:33:30Z" })]);
  assert.deepEqual([close.warning.before, close.warning.text], [null, "Claude's 5-hour limit fills at 21:33"]);
  assert.equal(model([row({ full_at: "2026-11-08T20:00:00Z", resets_at: null })]).warning.before, null);
});

test("a reading that is not fresh never fills early", () => {
  const found = model([row({ state: "unavailable", full_at: "2026-11-08T20:00:00Z" }),
                       claudeWeek({ state: "reset", full_at: "2026-11-08T20:00:00Z" })]);
  assert.equal(found.warning, null);
  assert.equal(found.groups[0].rows[0].fills, null);
});

test("days are worded against the answer's today, each side of midnight", () => {
  const late = [row({ resets_at: "2026-11-09T00:20:00Z", opened: "2026-11-08T19:20:00Z",
                      observed_at: "2026-11-08T23:50:00Z" })];
  const before = model(late, at("2026-11-08T23:55:00Z"), "2026-11-08");
  assert.deepEqual([before.groups[0].read, before.groups[0].rows[0].resets], ["read 23:50", "resets tomorrow 00:20"]);
  const after = model(late, at("2026-11-09T00:05:00Z"), "2026-11-09");
  assert.deepEqual([after.groups[0].read, after.groups[0].rows[0].resets], ["read yesterday 23:50", "resets 00:20"]);
  const none = model(late, at("2026-11-09T00:05:00Z"), null);
  assert.deepEqual([none.groups[0].read, none.groups[0].rows[0].resets], ["read 23:50", "resets 00:20"]);
});

test("the parser keeps the answer's today and its rows' opened time", () => {
  const parsed = parseLimits({ now: NOW, today: TODAY, rows: [row()], pull: null, notes: [] });
  assert.equal(parsed.today, TODAY);
  assert.equal(parsed.rows[0].opened, "2026-11-08T16:34:00Z");
  assert.equal(parseLimits({ rows: [], pull: null, notes: [] }).today, null);
  assert.equal(limitsModel(parsed, NOW, UTC).groups[0].rows[0].resets, "resets 21:34");
});
