import assert from "node:assert/strict";
import test from "node:test";

import {
  costOf, ifColdWords, moneyWords, parseRates, parseUsageSession, pricesPath, restCost, tokensWords, UsageReads,
  usageRoute, usageSessionPath,
} from "../src/cost.ts";
import { addressRoute } from "../../../usage/web/src/address-model.ts";

const SONNET = "claude-sonnet-4-5";
const OPUS = "claude-opus-4-1";
/** One row of usage's `/api/session`. */
const agentRow = (agent, model, last) => ({ agent, kind: agent === "main" ? "main" : "subagent", title: null, model,
  first_ts: last - 600, last_ts: last });
const sessionAnswer = (rows, context) => ({ today: "2026-11-10", rows, total: {}, context, ref: null });
/** One model of usage's `/api/prices`, dollars per million tokens. */
const rate = (prices, tiers = []) => ({ model: "m", asked_provider: null, input: 3, output: 15, ...prices, tiers });
const SONNET_RATE = rate({ cache_read: 0.3, cache_write_5m: 3.75, cache_write_1h: 6 });

test("usage's session: its context, and the model of the main file's newest rows", () => {
  const answer = sessionAnswer([agentRow("main", OPUS, 100), agentRow("main", SONNET, 300), agentRow("a1", "haiku", 900)],
                               { tokens: 412_000, window: 1_000_000 });
  assert.deepEqual(parseUsageSession(answer), { tokens: 412_000, model: SONNET });
  assert.deepEqual(parseUsageSession(sessionAnswer([], null)), { tokens: null, model: null });
  assert.deepEqual(parseUsageSession(sessionAnswer([agentRow("main", null, 5)], { tokens: 0, window: null })),
                   { tokens: null, model: null });
  assert.equal(parseUsageSession({ rows: "no" }), null);
  assert.equal(parseUsageSession(null), null);
});

test("the prices come back in the order asked; one that is not a price is unknown", () => {
  const rates = parseRates({ models: [SONNET_RATE, rate({ cache_read: null, cache_write_5m: -1, cache_write_1h: "x" })] },
                           [SONNET, OPUS]);
  assert.deepEqual(rates.get(SONNET), { cache_read: 0.3, cache_write_5m: 3.75, cache_write_1h: 6, tiers: [] });
  assert.deepEqual(rates.get(OPUS), { cache_read: null, cache_write_5m: null, cache_write_1h: null, tiers: [] });
  assert.equal(parseRates({ models: [SONNET_RATE] }, [SONNET, OPUS]), null);
  assert.equal(parseRates(null, [SONNET]), null);
});

test("going cold costs the context at the write price of the record's lifetime", () => {
  const rates = parseRates({ models: [SONNET_RATE] }, [SONNET]);
  const facts = { tokens: 412_000, model: SONNET };
  const hour = costOf(3600, facts, rates);
  assert.equal(hour.tokens, 412_000);
  assert.ok(Math.abs(hour.cold - 2.472) < 1e-9);
  assert.ok(Math.abs(hour.warm - 0.1236) < 1e-9);
  assert.ok(Math.abs(costOf(300, facts, rates).cold - 1.545) < 1e-9);
  assert.equal(costOf(1800, facts, rates), null);
  assert.equal(costOf(null, facts, rates), null);
  assert.equal(costOf(3600, { tokens: null, model: SONNET }, rates), null);
  assert.equal(costOf(3600, { tokens: 10, model: null }, rates), null);
  assert.equal(costOf(3600, { tokens: 10, model: OPUS }, rates), null);
  assert.equal(costOf(3600, null, rates), null);
  assert.equal(costOf(3600, facts, null), null);
  const noRead = parseRates({ models: [rate({ cache_read: null, cache_write_5m: 3.75, cache_write_1h: 6 })] }, [SONNET]);
  assert.equal(costOf(3600, facts, noRead).warm, null);
  const noWrite = parseRates({ models: [rate({ cache_read: 0.3, cache_write_5m: 3.75, cache_write_1h: null })] }, [SONNET]);
  assert.equal(costOf(3600, facts, noWrite), null);
});

test("a prompt over a tier's size pays that tier's prices; a tier a request chooses is not taken", () => {
  const tier = (above, prices, extra = {}) => ({ name: `over ${above}`, conditions: { above_tokens: above, service_tier: null,
    speed: null, ...extra }, prices: { input: 6, output: 22.5, ...prices }, from_tier_input: [], from_row: [] });
  const tiers = [tier(200_000, { cache_read: 0.6, cache_write_5m: 7.5, cache_write_1h: 12 }),
                 tier(100_000, { cache_read: 9, cache_write_5m: 9, cache_write_1h: 9 }),
                 tier(0, { cache_read: 99, cache_write_5m: 99, cache_write_1h: 99 }, { service_tier: "priority" })];
  const rates = parseRates({ models: [rate({ cache_read: 0.3, cache_write_5m: 3.75, cache_write_1h: 6 }, tiers)] }, [SONNET]);
  assert.equal(rates.get(SONNET).tiers.length, 2);
  assert.ok(Math.abs(costOf(3600, { tokens: 655_000, model: SONNET }, rates).cold - 7.86) < 1e-9);
  assert.ok(Math.abs(costOf(3600, { tokens: 150_000, model: SONNET }, rates).cold - 1.35) < 1e-9);
  assert.ok(Math.abs(costOf(3600, { tokens: 50_000, model: SONNET }, rates).cold - 0.3) < 1e-9);
});

test("the words: dollars, tokens, line two and the opened If cold line", () => {
  assert.deepEqual([2.6249, 0.004, 0.005, 12.3].map(moneyWords), ["$2.62", "under $0.01", "$0.01", "$12.30"]);
  assert.deepEqual([900, 1000, 655_400, 999_499, 999_500, 1_200_000, 12_400_000].map(tokensWords),
                   ["900", "1k", "655k", "999k", "1M", "1.2M", "12M"]);
  const cost = { tokens: 655_000, cold: 2.62, warm: 0.0655 };
  assert.equal(restCost(cost, false), "about $2.62 to re-read if cold");
  assert.equal(restCost(cost, true), "about $2.62 to re-read on its next turn");
  assert.equal(ifColdWords(cost), "Next turn re-reads 655k tokens: about $2.62, or $0.07 warm");
  assert.equal(ifColdWords({ ...cost, warm: null }), "Next turn re-reads 655k tokens: about $2.62");
});

test("the routes: usage's session, its prices, and the address usage reads a session's row from", () => {
  assert.equal(usageSessionPath("claude", "s 1"), "api/session?session=s+1&harness=claude");
  assert.equal(pricesPath([SONNET, OPUS]), `api/prices?models=${encodeURIComponent(`${SONNET},${OPUS}`)}`);
  const address = { piece: "sessions", harness: "claude", session: "a/b", agent: null };
  assert.equal(usageRoute("claude", "a/b"), addressRoute(address));
});

/** A stand-in for usage's routes: each session's answer by id, and the prices by model. */
function usageStub() {
  const stub = { sessions: new Map(), prices: new Map(), asked: [], fail: new Set() };
  stub.read = async (path) => {
    stub.asked.push(path);
    const url = new URL(path, "http://x/");
    if (stub.fail.has(url.pathname.slice(1))) return { status: 500, data: null };
    if (url.pathname === "/api/session") {
      const answer = stub.sessions.get(url.searchParams.get("session"));
      return answer === undefined ? { status: 500, data: null } : { status: 200, data: answer };
    }
    const models = url.searchParams.get("models").split(",");
    return { status: 200, data: { models: models.map((model) => stub.prices.get(model) ?? rate({})) } };
  };
  return stub;
}

const settle = () => new Promise((done) => setTimeout(done, 0));
const asked = (key, last) => ({ key: `claude/${key}`, harness: "claude", session: key, last });

test("usage's reads: each session once, again when its last request moves, and the prices of their models", async () => {
  const stub = usageStub();
  stub.sessions.set("s1", sessionAnswer([agentRow("main", SONNET, 1)], { tokens: 412_000, window: null }));
  stub.sessions.set("s2", sessionAnswer([agentRow("main", OPUS, 1)], { tokens: 10_000, window: null }));
  stub.prices.set(SONNET, SONNET_RATE);
  let changes = 0;
  const reads = new UsageReads(stub.read, () => { changes += 1; });
  reads.sessions([asked("s1", 100), asked("s2", 100)]);
  await settle(); await settle(); await settle(); await settle();
  assert.deepEqual(reads.facts("claude/s1"), { tokens: 412_000, model: SONNET });
  assert.equal(reads.rates.get(SONNET).cache_write_1h, 6);
  assert.equal(stub.asked.filter((path) => path.startsWith("api/prices")).length, 1);
  assert.ok(changes >= 3);

  stub.asked.length = 0;
  reads.sessions([asked("s1", 100), asked("s2", 100)]);
  await settle();
  assert.deepEqual(stub.asked, []);
  reads.sessions([asked("s1", 200), asked("s2", 100)]);
  await settle(); await settle();
  assert.deepEqual(stub.asked, [usageSessionPath("claude", "s1")]);

  stub.asked.length = 0;
  stub.sessions.delete("s1");
  reads.sessions([asked("s1", 300)]);
  await settle(); await settle(); await settle(); await settle();
  assert.equal(reads.facts("claude/s1"), null);
  assert.equal(reads.facts("claude/s2"), null);
  assert.equal(reads.rates, null);

  stub.sessions.set("s1", sessionAnswer([agentRow("main", SONNET, 1)], { tokens: 412_000, window: null }));
  reads.again();
  await settle(); await settle(); await settle(); await settle();
  assert.deepEqual(reads.facts("claude/s1"), { tokens: 412_000, model: SONNET });
  assert.ok(reads.rates !== null);
  stub.fail.add("api/prices");
  reads.again();
  await settle(); await settle(); await settle(); await settle();
  assert.equal(reads.rates, null);
  reads.close();
});
