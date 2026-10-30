import assert from "node:assert/strict";
import test from "node:test";

import { BACKOFF_MS, HIDDEN_MS, Hub, Leader, REOPEN_MS } from "../src/push.ts";

class Clock {
  now = 0;
  timers = new Map();
  next = 1;
  set(fn, ms) {
    const id = this.next++;
    this.timers.set(id, { at: this.now + ms, fn });
    return id;
  }
  clear(id) {
    this.timers.delete(id);
  }
  advance(ms) {
    const end = this.now + ms;
    for (;;) {
      const due = [...this.timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]);
      this.now = due[1].at;
      due[1].fn();
    }
    this.now = end;
  }
}

function setup() {
  const clock = new Clock();
  const streams = [];
  const delivered = [];
  const replays = [];
  let out = 0;
  const leader = new Leader({
    clock,
    open: (topics, handlers) => {
      const stream = { topics: [...topics], closed: false, ended: false };
      // A stream that ends is over, as a real one is.
      stream.handlers = { event: handlers.event, end: (ending) => { stream.ended = true; handlers.end(ending); } };
      streams.push(stream);
      return { close: () => { stream.closed = true; } };
    },
    deliver: (name, raw) => delivered.push([name, raw]),
    replay: (tab, events) => replays.push([tab, events]),
    signedOut: () => { out += 1; },
  });
  const live = () => streams.filter((s) => !s.closed && !s.ended);
  return { clock, streams, live, delivered, replays, leader, out: () => out };
}

test("one stream for the union, opened 50 ms after the union last changed", () => {
  const t = setup();
  t.leader.setTab("a", ["fixture.one"], true);
  t.clock.advance(REOPEN_MS - 10);
  t.leader.setTab("b", ["fixture.two", "fixture.one"], true);
  t.clock.advance(REOPEN_MS - 10);
  assert.equal(t.streams.length, 0);
  t.clock.advance(10);
  assert.deepEqual(t.streams.map((s) => s.topics), [["fixture.one", "fixture.two"]]);
  // Visibility alone does not reopen; a new topic does, once.
  t.leader.setTab("b", ["fixture.one", "fixture.two"], false);
  t.clock.advance(1000);
  assert.equal(t.streams.length, 1);
  t.leader.setTab("a", ["fixture.one", "ui.servers"], true);
  t.clock.advance(REOPEN_MS);
  assert.equal(t.live().length, 1);
  assert.deepEqual(t.live()[0].topics, ["fixture.one", "fixture.two", "ui.servers"]);
  assert.ok(t.streams[0].closed);
});

test("events go to every tab, and a joining tab gets the last values it asks for", () => {
  const t = setup();
  t.leader.setTab("a", ["fixture.one", "fixture.two"], true);
  t.clock.advance(REOPEN_MS);
  const { handlers } = t.live()[0];
  handlers.event("fixture.one", "1");
  handlers.event("fixture.two", "2");
  handlers.event("moxi.state", JSON.stringify({ module: "fixture", state: "up", detail: null }));
  assert.equal(t.delivered.length, 3);
  t.leader.setTab("b", ["fixture.two"], true);
  assert.deepEqual(t.replays, [["b", [["fixture.two", "2"],
    ["moxi.state", JSON.stringify({ module: "fixture", state: "up", detail: null })]]]]);
  // The union did not change, so the stream stays.
  t.clock.advance(REOPEN_MS);
  assert.equal(t.streams.length, 1);
});

test("the stream closes after 10 s with no visible tab, and opens when one is shown", () => {
  const t = setup();
  t.leader.setTab("a", ["fixture.one"], true);
  t.clock.advance(REOPEN_MS);
  t.leader.setTab("a", ["fixture.one"], false);
  t.clock.advance(HIDDEN_MS - 1);
  assert.equal(t.live().length, 1);
  t.clock.advance(1);
  assert.equal(t.live().length, 0);
  t.leader.setTab("a", ["fixture.one"], true);
  t.clock.advance(REOPEN_MS);
  assert.equal(t.live().length, 1);
  assert.equal(t.streams.length, 2);
});

test("a tab shown again within 10 s keeps the stream", () => {
  const t = setup();
  t.leader.setTab("a", ["fixture.one"], true);
  t.clock.advance(REOPEN_MS);
  t.leader.setTab("a", ["fixture.one"], false);
  t.clock.advance(HIDDEN_MS / 2);
  t.leader.setTab("a", ["fixture.one"], true);
  t.clock.advance(HIDDEN_MS * 2);
  assert.equal(t.streams.length, 1);
  assert.equal(t.live().length, 1);
});

test("reconnects back off 1, 2, 5, 10, then 30 s, and an event resets the wait", () => {
  const t = setup();
  t.leader.setTab("a", ["fixture.one"], true);
  t.clock.advance(REOPEN_MS);
  for (const wait of [...BACKOFF_MS, 30_000]) {
    t.live()[0].handlers.end({ kind: "network" });
    const count = t.streams.length;
    t.clock.advance(wait - 1);
    assert.equal(t.streams.length, count, `no reopen before ${wait} ms`);
    t.clock.advance(1);
    assert.equal(t.streams.length, count + 1, `a reopen at ${wait} ms`);
  }
  t.live()[0].handlers.event("fixture.one", "1");
  t.live()[0].handlers.end({ kind: "closed" });
  t.clock.advance(BACKOFF_MS[0]);
  assert.equal(t.live().length, 1);
});

test("busy waits its Retry-After; a 400 waits for the union to change; a 403 signs out", () => {
  const t = setup();
  t.leader.setTab("a", ["fixture.one"], true);
  t.clock.advance(REOPEN_MS);
  t.live()[0].handlers.end({ kind: "status", status: 503, error: "busy", retryAfter: 5 });
  t.clock.advance(4999);
  assert.equal(t.live().length, 0);
  t.clock.advance(1);
  assert.equal(t.live().length, 1);

  const logged = [];
  const error = console.error;
  console.error = (...args) => logged.push(args.join(" "));
  try {
    t.live()[0].handlers.end({ kind: "status", status: 400, error: "bad: topics", retryAfter: null });
  } finally {
    console.error = error;
  }
  assert.match(logged[0], /refused the push topics fixture.one/);
  t.clock.advance(120_000);
  assert.equal(t.live().length, 0);
  t.leader.setTab("a", ["fixture.two"], true);
  t.clock.advance(REOPEN_MS);
  assert.deepEqual(t.live()[0].topics, ["fixture.two"]);

  t.live()[0].handlers.end({ kind: "status", status: 403, error: null, retryAfter: null });
  assert.equal(t.out(), 1);
  t.clock.advance(120_000);
  assert.equal(t.live().length, 0);
  t.leader.reset();
  assert.equal(t.live().length, 1);
});

test("a tab that leaves takes its topics with it; no topics, no stream", () => {
  const t = setup();
  t.leader.setTab("a", ["fixture.one"], true);
  t.leader.setTab("b", ["fixture.two"], true);
  t.clock.advance(REOPEN_MS);
  t.leader.dropTab("b");
  t.clock.advance(REOPEN_MS);
  assert.deepEqual(t.live().map((s) => s.topics), [["fixture.one"]]);
  t.leader.setTab("a", [], true);
  t.clock.advance(REOPEN_MS);
  assert.equal(t.live().length, 0);
});

test("events from a closed stream are dropped", () => {
  const t = setup();
  t.leader.setTab("a", ["fixture.one"], true);
  t.clock.advance(REOPEN_MS);
  const old = t.live()[0];
  t.leader.setTab("a", ["fixture.two"], true);
  t.clock.advance(REOPEN_MS);
  old.handlers.event("fixture.one", "late");
  old.handlers.end({ kind: "network" });
  assert.deepEqual(t.delivered, []);
  t.clock.advance(60_000);
  assert.equal(t.streams.length, 2);
});

test("the hub: current value to a late subscriber, no repeat of the same value", async () => {
  let changes = 0;
  const hub = new Hub(() => { changes += 1; });
  const got = [];
  const stop = hub.add("fixture.one", (v) => got.push(["first", v]));
  assert.equal(changes, 1);
  hub.deliver("fixture.one", '{"n":1}');
  hub.deliver("fixture.one", '{"n":1}');
  hub.deliver("fixture.two", '{"n":9}');
  const stopLate = hub.add("fixture.one", (v) => got.push(["late", v]));
  assert.equal(changes, 1);
  await Promise.resolve();
  hub.deliver("fixture.one", '{"n":2}');
  assert.deepEqual(got, [["first", { n: 1 }], ["late", { n: 1 }], ["first", { n: 2 }], ["late", { n: 2 }]]);
  stop();
  assert.equal(changes, 1);
  stopLate();
  assert.equal(changes, 2);
  assert.deepEqual(hub.topics(), []);
});

test("the hub passes each module's state to its watchers", () => {
  const hub = new Hub(() => {});
  const seen = [];
  hub.onState((s) => seen.push(s));
  hub.deliver("moxi.state", JSON.stringify({ module: "fixture", state: "down", detail: "not exported: x" }));
  hub.deliver("moxi.state", "not json");
  assert.deepEqual(seen, [{ module: "fixture", state: "down", detail: "not exported: x" }]);
  assert.equal(hub.state("fixture").state, "down");
  assert.equal(hub.state("other"), null);
});
