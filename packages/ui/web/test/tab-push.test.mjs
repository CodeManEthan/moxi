// TabPush in one browser of fakes: a lock manager and a channel the tabs share, and each
// tab's own page events and push streams. The timers are node:test's mock timers.

import assert from "node:assert/strict";
import test from "node:test";

import { LOCK, REOPEN_MS, TabPush } from "../src/push.ts";

/** The Web Locks API, as much of it as TabPush uses: exclusive locks granted in order. */
class Locks {
  held = new Set();
  waiting = new Map();

  request(name, callback) {
    return new Promise((resolve, reject) => {
      const queue = this.waiting.get(name) ?? [];
      queue.push({ callback, resolve, reject });
      this.waiting.set(name, queue);
      this.grant(name);
    });
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

/** One browser: its locks, its channels, and the tabs opened in it. */
function browser() {
  const locks = new Locks();
  const channels = new Set();
  Object.defineProperty(globalThis, "navigator", { value: { locks }, configurable: true });
  globalThis.document = { visibilityState: "visible", addEventListener() {} };

  /** A tab: its TabPush, the streams it opened, and its page events to fire. */
  function tab() {
    const listeners = new Map();
    const streams = [];
    globalThis.addEventListener = (type, fn) => {
      listeners.set(type, [...(listeners.get(type) ?? []), fn]);
    };
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

test("a leader's stream closes on pagehide and opens again when shown from the cache", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const { tab } = browser();
  const a = tab();
  a.push.hub.add("fixture.one", () => {});
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(a.live(), [["fixture.one"]]);

  a.fire("pagehide", { persisted: true });
  assert.deepEqual(a.live(), []);
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
  assert.deepEqual(a.live(), [["fixture.one", "fixture.two"]]);
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
  await settle();
  t.mock.timers.tick(REOPEN_MS);
  assert.deepEqual(a.live(), [["fixture.one"]]);
  assert.deepEqual(b.streams, []);
});
