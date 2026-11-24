import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import { LIVE_PATH, LIVE_TOPIC, pairsOf, parseLive, WORKING, workingOf } from "../src/working-model.ts";
import { parseRunning } from "../src/running-model.ts";

/** One running launch as launcher's `/api/running` sends it. */
const entry = (request, sid = `${request}-sid`) => ({
  request_id: request, harness: "claude", session_id: sid, name: `${request}-name`, identity: null,
  where: { target: "slot", slot: "3", project_key: "atlas-api", folder: "atlas-api" },
  started_at: "2026-11-19T18:00:00Z", cache: null, waits: null, covered: false, coverable: null, activity: null,
});
const entries = (...list) => parseRunning({ state: "up", detail: null, count: list.length, sessions: list }).sessions;

/** One session's facts as turns sends them. */
const facts = (more = {}) => ({
  session_id: null, turn_number: 4, turn_open: true, turn_started_at: 1_700_000_000, agents: 0, draft: false,
  stale: false, ready: null, at: 1_700_000_100, ...more,
});
/** One session as turns' `/api/sessions` sends it. */
const session = (sid, more = {}) => ({
  key: `claude-${sid}`, harness: "claude", pid: 4242, session_id: sid, cwd: "/home/you/code/atlas-api",
  started: 1_699_999_000, capabilities: ["agents"], endpoint_version: "1", mod_version: "1", trouble: null,
  facts: facts({ session_id: sid }), agents: { entries: [], complete: true }, read_error: null, ended_at: null,
  has_box: false, unread_since: null, ...more,
});
const list = (sessions) => ({ listed: true, detail: null, home: "/home/you", reach: null, sessions });

test("turns' route and topic", () => {
  assert.equal(LIVE_PATH, "api/sessions");
  assert.equal(LIVE_TOPIC, "sessions");
  assert.equal(WORKING, "working");
});

test("turns' list reads its sessions' ids and whether a turn is open; anything else reads as nothing", () => {
  assert.deepEqual(parseLive(list([session("s1"), session("s2", { facts: facts({ turn_open: false }) })])), {
    sessions: [{ key: "claude-s1", session_id: "s1", turn_open: true },
               { key: "claude-s2", session_id: "s2", turn_open: false }],
  });
  for (const bad of [null, [], "x", { sessions: [] }, { listed: true, sessions: {} }]) assert.equal(parseLive(bad), null);
  const odd = parseLive(list([{ session_id: "s1" }, session("s2", { facts: null }), session("s3", { session_id: 3 }),
                              session("s4", { facts: facts({ turn_open: "yes" }) })]));
  assert.deepEqual(odd.sessions, [{ key: "claude-s2", session_id: "s2", turn_open: null },
                                  { key: "claude-s3", session_id: null, turn_open: true },
                                  { key: "claude-s4", session_id: "s4", turn_open: null }]);
});

test("a launch is working while the turns session carrying its session id has a turn open", () => {
  const live = parseLive(list([session("a-sid"), session("b-sid", { facts: facts({ turn_open: false }) }),
                               session("c-sid", { facts: null })]));
  const found = workingOf(entries(entry("a"), entry("b"), entry("c"), entry("d")), live);
  assert.deepEqual([...found], ["a"]);
});

test("a launch with no session id, or one another launch shares, is never working; nor one turns lists twice", () => {
  const live = parseLive(list([session("a-sid"), session("shared"), session("twice"), session("twice")]));
  const running = entries(entry("a", null), entry("b", "shared"), entry("c", "shared"), entry("d", "twice"));
  assert.deepEqual([...workingOf(running, live)], []);
  assert.deepEqual([...pairsOf(running, live).keys()], []);
});

test("nothing is working when turns lists no session", () => {
  assert.deepEqual([...workingOf(entries(entry("a")), parseLive(list([])))], []);
});

test("the rule is turns' and the pairing the Sessions page's, where their sources sit beside the launcher's", async () => {
  const rows = new URL("../../../turns/web/src/rows-model.ts", import.meta.url);
  const live = new URL("../../../sessions/web/src/live.ts", import.meta.url);
  if (!existsSync(rows) || !existsSync(live)) return;
  const turns = await import(rows.href);
  const sessions = await import(live.href);
  assert.equal(WORKING, sessions.WORKING);
  const raws = [
    session("a-sid"), session("b-sid", { facts: facts({ turn_open: false }) }), session("c-sid", { facts: null }),
    session("d-sid", { trouble: "it broke", read_error: "no answer", unread_since: 1_700_000_000 }),
    session("shared"), session("twice"), session("twice"),
  ];
  const running = entries(entry("a"), entry("b"), entry("c"), entry("d"), entry("e"), entry("f", null),
                          entry("g", "shared"), entry("h", "shared"), entry("i", "twice"));
  const ours = workingOf(running, parseLive(list(raws)));
  const theirs = sessions.pairsOf(running, sessions.parseLive(list(raws)));
  assert.deepEqual([...pairsOf(running, parseLive(list(raws))).keys()], [...theirs.keys()]);
  for (const one of running) {
    const paired = theirs.get(one.request_id);
    const raw = paired === undefined ? undefined : raws.find((item) => item.key === paired.key);
    assert.equal(ours.has(one.request_id), raw !== undefined && turns.stateOf(raw) === "working", one.request_id);
  }
});
