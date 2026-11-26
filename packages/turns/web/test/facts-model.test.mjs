import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import {
  claimOf, factsReport, goodValue, LIVE_MAX, liveEntry, liveOf, ORDER, READ_MS, VERSION,
} from "../src/facts-model.ts";
import { Feed, parseList } from "../src/feed.ts";
import { stateOf } from "../src/rows-model.ts";

const NOW = Date.parse("2026-11-18T19:00:00Z") / 1000;
const CLAUDE = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f";
const ROLLOUT = "019a2b3c-4d5e-7f60-8192-a3b4c5d6e7f8";
const OPENCODE = "ses_7f3a";

const facts = (id, extra) => ({ session_id: id, turn_number: 4, turn_open: false, turn_started_at: null,
                                agents: 0, draft: false, stale: false, ready: null, at: NOW, ...extra });
/** A session as `/api/sessions` lists it, its facts read under its id, with `extra` laid
 * over it (`extra.facts` over the facts). */
const session = (key, id, { facts: more, ...extra } = {}, harness = "claude") => ({
  key, harness, pid: 4321, session_id: id, cwd: "/work/kestrel-web", started: NOW - 600,
  capabilities: ["agents"], endpoint_version: "0.2.4", mod_version: "0.1.0", trouble: null,
  facts: facts(id, more), agents: { entries: [], complete: true }, read_error: null,
  ended_at: null, has_box: true, unread_since: null, ...extra,
});
const list = (sessions, extra = {}) => ({ listed: true, detail: null, home: "/home/me", reach: null, sessions, ...extra });

const WORKING = session("claude-a", CLAUDE, { facts: { turn_open: true, turn_started_at: NOW - 60 } });
const IDLE = session("codex-b", ROLLOUT, {}, "codex");

// -- the frame ------------------------------------------------------------------------------

test("a good read reports version 1, order 20, no activity, no project, and `live`", () => {
  assert.deepEqual([VERSION, ORDER, READ_MS, LIVE_MAX], [1, 20, 30_000, 100]);
  const found = factsReport(list([WORKING, IDLE]));
  assert.deepEqual(found, { version: 1, order: 20, projects: {}, live: {
    [`claude/${CLAUDE}`]: { in_turn: true, state: "working" },
    [`codex/${ROLLOUT}`]: { in_turn: false, state: "idle" },
  } });
  assert.equal("activity" in found, false);
  assert.deepEqual(factsReport(list([])), { version: 1, order: 20, projects: {}, live: {} });
});

test("no list, or one whose first scan hasn't finished, reports null", () => {
  assert.equal(factsReport(null), null);
  assert.equal(factsReport(list([WORKING], { listed: false, detail: "the scan failed" })), null);
  assert.equal(factsReport(list([], { listed: false })), null);
});

// -- the claim ------------------------------------------------------------------------------

test("a session's claim is its harness and the id the harness wrote; none without either", () => {
  assert.equal(claimOf(WORKING), `claude/${CLAUDE}`);
  assert.equal(claimOf(IDLE), `codex/${ROLLOUT}`);
  assert.equal(claimOf(session("opencode-c", OPENCODE, {}, "opencode")), `opencode/${OPENCODE}`);
  assert.equal(claimOf(session("claude-d", null)), null);
  assert.equal(claimOf(session("claude-d", "")), null);
  assert.equal(claimOf(session("x-d", CLAUDE, {}, "")), null);
});

test("each harness's claim is the launcher's claim for the same session", async () => {
  // Held equal to launcher's model, where its source sits beside turns' in the repository.
  const theirs = new URL("../../../launcher/web/src/facts-model.ts", import.meta.url);
  if (!existsSync(theirs)) return;
  const launcher = await import(theirs.href);
  const launch = (harness, id) => ({ request_id: "req-1", harness, session_id: id });
  for (const [harness, id] of [["claude", CLAUDE], ["codex", ROLLOUT], ["opencode", OPENCODE]]) {
    assert.equal(claimOf(session(`${harness}-x`, id, {}, harness)), launcher.claimOf(launch(harness, id)), harness);
  }
  // A launch with no session id yet claims itself, which no turns session ever matches.
  assert.equal(launcher.claimOf(launch("claude", null)), "request/req-1");
});

// -- what turns can say ---------------------------------------------------------------------

test("in_turn is the facts' turn_open, and state the panel's word for it", () => {
  for (const one of [WORKING, IDLE]) {
    assert.deepEqual(liveEntry(one), { in_turn: one.facts.turn_open, state: stateOf(one) });
  }
  // A problem turns shows on its panel doesn't change whether a turn is open.
  assert.deepEqual(liveEntry({ ...WORKING, trouble: "the endpoint is old" }), { in_turn: true, state: "working" });
});

test("a session turns can't say of now is left out", () => {
  const out = {
    "no session id": session("claude-a", null),
    "no facts read": { ...WORKING, facts: null },
    "facts under another id": { ...WORKING, facts: { ...WORKING.facts, session_id: "an-older-id" } },
    "facts not one moment": { ...WORKING, facts: { ...WORKING.facts, stale: true } },
    "a run of unread readings": { ...WORKING, unread_since: NOW - 90 },
    "no turn_open": { ...WORKING, facts: { ...WORKING.facts, turn_open: undefined } },
  };
  for (const [why, one] of Object.entries(out)) {
    assert.equal(liveEntry(one), null, why);
    assert.deepEqual(liveOf(list([one, IDLE])), { [`codex/${ROLLOUT}`]: { in_turn: false, state: "idle" } }, why);
  }
  // Only the watch being down is no unread reading: turns still says.
  assert.deepEqual(liveEntry({ ...WORKING, read_error: "the watch is down" }), { in_turn: true, state: "working" });
});

test("two sessions with one claim are both left out, even when one alone could be said", () => {
  const again = { ...WORKING, key: "claude-z", facts: null };
  assert.deepEqual(liveOf(list([WORKING, again, IDLE])), { [`codex/${ROLLOUT}`]: { in_turn: false, state: "idle" } });
  assert.deepEqual(liveOf(list([WORKING, { ...WORKING, key: "claude-y" }])), {});
});

test("newest first, and past 100 the rest are left out", () => {
  const many = Array.from({ length: LIVE_MAX + 5 }, (_, n) => session(`claude-${n}`, `id-${n}`));
  const claims = Object.keys(liveOf(list(many)));
  assert.equal(claims.length, LIVE_MAX);
  assert.deepEqual(claims, many.slice(0, LIVE_MAX).map((one) => `claude/${one.session_id}`));
  // Left-out rows don't count against the cap.
  const mixed = [session("claude-n", null), ...many];
  assert.equal(Object.keys(liveOf(list(mixed))).length, LIVE_MAX);
});

// -- reads ----------------------------------------------------------------------------------

/** A feed over `answers`, read in order, and the report it gives after each read. */
async function reportsOver(answers) {
  let i = 0;
  const feed = new Feed(async () => answers[i++], parseList, () => {}, { wait: async () => {} });
  const out = [];
  for (let n = 0; n < answers.length; n += 1) {
    await feed.refresh();
    out.push(factsReport(goodValue(feed)));
  }
  return out;
}

test("a failed, refused, unreadable or gone read reports null, and the next good read puts `live` back", async () => {
  const ok = { status: 200, data: { now: NOW, ...list([WORKING]) } };
  for (const bad of [{ status: 500, data: null }, { status: 403, data: null }, { status: 0, data: null },
                     { status: 200, data: { now: NOW, listed: "yes", sessions: [] } }, { status: 404, data: null }]) {
    const [first, second, third] = await reportsOver([ok, bad, ok]);
    assert.deepEqual(first.live, { [`claude/${CLAUDE}`]: { in_turn: true, state: "working" } });
    assert.equal(second, null, JSON.stringify(bad));
    assert.deepEqual(third, first);
  }
});

test("a topic event replaces the list after a good read", async () => {
  const feed = new Feed(async () => ({ status: 200, data: { now: NOW, ...list([IDLE]) } }), parseList, () => {},
                        { wait: async () => {} });
  await feed.refresh();
  feed.topic(list([WORKING, IDLE]));
  assert.deepEqual(Object.keys(factsReport(goodValue(feed)).live), [`claude/${CLAUDE}`, `codex/${ROLLOUT}`]);
});
