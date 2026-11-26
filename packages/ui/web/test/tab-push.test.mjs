// TabPush in one browser of fakes: a lock manager, a channel and a visibility the tabs
// share, and each tab's own page events and push streams. The timers are node:test's mock
// timers.

import assert from "node:assert/strict";
import test from "node:test";

import { LOCK, REOPEN_MS, TabPush } from "../src/push.ts";

/** The Web Locks API, as much of it as TabPush uses: exclusive locks granted in order, and
 * a waiting request withdrawn by its `signal`. With `signals` false it ignores the signal,
 * as when the lock was already on its way to the tab when the signal fired. */
class Locks {
  held = new Set();
  waiting = new Map();

  constructor({ signals = true } = {}) {
    this.signals = signals;
  }

  request(name, options, callback) {
    if (callback === undefined) [options, callback] = [{}, options];
    const { signal } = options;
    return new Promise((resolve, reject) => {
      const queue = this.waiting.get(name) ?? [];
      const entry = { callback, resolve, reject };
      if (this.signals && signal !== undefined) {
        signal.addEventListener("abort", () => {
          const at = queue.indexOf(entry);
          if (at === -1) return;
          queue.splice(at, 1);
          reject(new DOMException("The request was aborted.", "AbortError"));
        });
      }
      queue.push(entry);
      this.waiting.set(name, queue);
      this.grant(name);
    });
  }

  /** How many requests for `name` are waiting. */
  pending(name) {
    return (this.waiting.get(name) ?? []).length;
  }

  grant(name) {
    const queue = this.waiting.get(name) ?? [];
    if (this.held.has(name) || queue.length === 0) return;
    const { callback, resolve, reject } = queue.shift();
    this.held.add(name);
    Promise.resolve()
      .then(() => callback({ name }))
      .then(resolve, reject)
      .finally(() => {
        this.held.delete(name);
        this.grant(name);
      });
  }

  async query() {
    const pending = [...this.waiting].flatMap(([name, queue]) => queue.map(() => ({ name })));
    return { held: [...this.held].map((name) => ({ name })), pending };
  }
}

/** One browser: its locks, its channels, and the tabs opened in it. With `locks` false it
 * has no Web Locks, and each tab leads a stream of its own; `signals` goes to the locks. */
function browser({ locks: hasLocks = true, signals = true } = {}) {
  const locks = new Locks({ signals });
  const channels = new Set();
  Object.defineProperty(globalThis, "navigator", {
    value: hasLocks ? { locks } : {},
    configurable: true,
  });

  /** A tab: its TabPush, the streams it opened, and its page events to fire. */
  function tab() {
    const listeners = new Map();
    const streams = [];
    const listen = (type, fn) => {
      listeners.set(type, [...(listeners.get(type) ?? []), fn]);
    };
    globalThis.addEventListener = listen;
    globalThis.document = { visibilityState: "visible", addEventListener: listen };
    globalThis.BroadcastChannel = class {
      constructor(name) {
        this.name = name;
        this.onmessage = null;
        channels.add(this);
      }
      postMessage(data) {
        const copy = structuredClone(data);
        for (const other of channels) {
          if (other !== this && other.name === this.name) {
            queueMicrotask(() => other.onmessage?.({ data: copy }));
          }
        }
      }
    };
    const push = new TabPush((topics) => {
      const stream = { topics: [...topics], closed: false };
      streams.push(stream);
      return { close: () => { stream.closed = true; } };
    }, () => {});
    push.start();
    return {
      push,
      streams,
      live: () => streams.filter((s) => !s.closed).map((s) => s.topics),
      fire: (type, event = {}) => {
        for (const fn of listeners.get(type) ?? []) fn(event);
      },
    };
  }

  return { locks, tab };
}

/** Lets every promise, lock grant and channel message settle. */
async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

test("a leader gives up its stream and lock on pagehide and leads again when shown from the cache", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const { locks, tab } = browser();
  const a = tab();
  a.push.hub.add("fixture.one", () => {});
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(a.live(), [["fixture.one"]]);
  assert.ok(locks.held.has(LOCK));

  a.fire("pagehide", { persisted: true });
  assert.deepEqual(a.live(), []);
  await settle();
  assert.ok(!locks.held.has(LOCK), "the leader lock is free for another tab");
  // A topic change while away opens nothing, however long the page stays away.
  a.push.hub.add("fixture.two", () => {});
  await settle();
  t.mock.timers.tick(60_000);
  await settle();
  assert.equal(a.streams.length, 1);

  // A pageshow that is not from the cache is the page's first load, and changes nothing.
  a.fire("pageshow", { persisted: false });
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.equal(a.streams.length, 1);

  a.fire("pageshow", { persisted: true });
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.ok(locks.held.has(LOCK));
  assert.deepEqual(a.live(), [["fixture.one", "fixture.two"]]);
});

test("when the leader's page leaves another tab leads, and the page rejoins when shown from the cache", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const { tab } = browser();
  const a = tab();
  a.push.hub.add("fixture.one", () => {});
  await settle();
  const b = tab();
  b.push.hub.add("fixture.two", () => {});
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(a.live(), [["fixture.one", "fixture.two"]]);

  // a goes into the back-forward cache: b takes the lock and opens a stream for what the
  // tabs still here ask for, and a, being away, does not answer b's ask.
  a.fire("pagehide", { persisted: true });
  assert.deepEqual(a.live(), []);
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(b.live(), [["fixture.two"]]);
  // A prune finds a's tab lock still held, and keeps a out of the union all the same.
  t.mock.timers.tick(60_000);
  await settle();
  assert.deepEqual(b.live(), [["fixture.two"]]);

  // a is shown again: it waits for the lock behind b, and b's stream takes its topics.
  a.fire("pageshow", { persisted: true });
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(b.live(), [["fixture.one", "fixture.two"]]);
  assert.equal(a.streams.length, 1);

  // b's page goes for good: a leads again.
  b.fire("pagehide", { persisted: false });
  assert.deepEqual(b.live(), []);
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(a.live(), [["fixture.one"]]);
});

for (const signals of [true, false]) {
  const how = signals ? "withdraws its request" : "gives up a lock granted while it is away";
  test(`a tab that leaves while it waits for the lock ${how}, and asks again when shown`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
    const { locks, tab } = browser({ signals });
    const a = tab();
    a.push.hub.add("fixture.a", () => {});
    await settle();
    const b = tab();
    b.push.hub.add("fixture.b", () => {});
    await settle();
    const c = tab();
    c.push.hub.add("fixture.c", () => {});
    await settle();
    t.mock.timers.tick(REOPEN_MS);
    assert.deepEqual(a.live(), [["fixture.a", "fixture.b", "fixture.c"]]);
    assert.equal(locks.pending(LOCK), 2);

    // b goes into the back-forward cache while its request waits behind a.
    b.fire("pagehide", { persisted: true });
    await settle();
    if (signals) assert.equal(locks.pending(LOCK), 1, "b's request is withdrawn");
    t.mock.timers.tick(REOPEN_MS);
    assert.deepEqual(a.live(), [["fixture.a", "fixture.c"]]);

    // a's page goes for good: c, which is here, leads, never b.
    a.fire("pagehide", { persisted: false });
    await settle();
    t.mock.timers.tick(REOPEN_MS);
    await settle();
    assert.deepEqual(b.streams, []);
    assert.deepEqual(c.live(), [["fixture.c"]]);
    assert.ok(locks.held.has(LOCK));

    // b is shown again: it waits behind c, and c's stream takes its topics.
    b.fire("pageshow", { persisted: true });
    await settle();
    t.mock.timers.tick(REOPEN_MS);
    assert.deepEqual(c.live(), [["fixture.b", "fixture.c"]]);
    assert.equal(locks.pending(LOCK), 1);

    // b leaves again before its new request is granted, then c goes: nobody here leads,
    // and b, being away, takes no lock.
    b.fire("pagehide", { persisted: true });
    c.fire("pagehide", { persisted: false });
    await settle();
    t.mock.timers.tick(60_000);
    await settle();
    assert.deepEqual(b.streams, []);
    assert.ok(!locks.held.has(LOCK));

    // b is shown again with no other tab: it leads.
    b.fire("pageshow", { persisted: true });
    await settle();
    t.mock.timers.tick(REOPEN_MS);
    assert.deepEqual(b.live(), [["fixture.b"]]);
  });
}

test("with no Web Locks, a tab closes its own stream on pagehide and opens it again when shown", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const { tab } = browser({ locks: false });
  const a = tab();
  a.push.hub.add("fixture.one", () => {});
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(a.live(), [["fixture.one"]]);
  a.fire("pagehide", { persisted: true });
  assert.deepEqual(a.live(), []);
  t.mock.timers.tick(60_000);
  a.fire("pageshow", { persisted: true });
  assert.deepEqual(a.live(), [["fixture.one"]]);
});

test("a tab that is not the leader says bye on pagehide, and the leader drops its topics", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const { locks, tab } = browser();
  const a = tab();
  a.push.hub.add("fixture.one", () => {});
  await settle();
  const b = tab();
  b.push.hub.add("fixture.two", () => {});
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.ok(locks.held.has(LOCK));
  assert.deepEqual(a.live(), [["fixture.one", "fixture.two"]]);
  assert.deepEqual(b.streams, []);

  b.fire("pagehide", { persisted: false });
  // The page is hidden after pagehide; a page that has left says nothing more.
  b.fire("visibilitychange");
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(a.live(), [["fixture.one"]]);
  assert.deepEqual(b.streams, []);
});
