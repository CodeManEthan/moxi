import assert from "node:assert/strict";
import test from "node:test";

import { commandParts, factText, ModelReads, NO_READING_LINE, sessionPath, STOPPED_LINE } from "../src/model.ts";

test("a Watcher row's sentence: the commands it names are cut out to be drawn as code", () => {
  assert.deepEqual(commandParts(STOPPED_LINE).filter((part) => part.code).map((part) => part.text), ["moxi cache watch"]);
  assert.equal(commandParts(STOPPED_LINE).map((part) => part.text).join(""), STOPPED_LINE);
  assert.deepEqual(commandParts(NO_READING_LINE).filter((part) => part.code).map((part) => part.text),
                   ["moxi cache watch", "moxi install cache"]);
  assert.deepEqual(commandParts("It is running."), [{ text: "It is running.", code: false }]);
  assert.deepEqual(commandParts("moxi cache watch"), [{ text: "moxi cache watch", code: true }]);
});

test("a row's fact, with the record's code after it as the record carries it", () => {
  const base = { key: "claude/a", session: "a", harness: "claude", name: "n", where: null, rest: null, open: false };
  assert.equal(factText({ ...base, fact: "not read", code: "E_READ" }), "not read · E_READ");
  assert.equal(factText({ ...base, fact: "no cache", code: null }), "no cache");
  assert.equal(factText({ ...base, fact: "no cache", code: "" }), "no cache");
  assert.equal(factText({ ...base, fact: "warm until 19:52", code: null }), "warm until 19:52");
});

test("the opened row's model is asked for its one session", () => {
  assert.equal(sessionPath("abc-1"), "api/session?session=abc-1");
  assert.equal(sessionPath("a b&c"), "api/session?session=a%20b%26c");
});

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("an opened row's model: asked when it opens, Reading… while out, dropped when it closed", async () => {
  const asked = [];
  let changes = 0;
  const reads = new ModelReads((session) => {
    const one = deferred();
    asked.push({ session, one });
    return one.promise;
  }, () => { changes += 1; });
  assert.equal(reads.get("claude/a"), null);
  reads.open("claude/a", "a");
  assert.deepEqual(reads.get("claude/a"), { state: "reading" });
  assert.deepEqual(asked.map((one) => one.session), ["a"]);
  asked[0].one.resolve({ status: 200, data: { model: "claude-model-a" } });
  await new Promise((done) => setImmediate(done));
  assert.deepEqual(reads.get("claude/a"), { state: "read", model: "claude-model-a" });
  assert.equal(changes, 1);

  // closed while out, then opened again: the first answer is dropped
  reads.close("claude/a");
  assert.equal(reads.get("claude/a"), null);
  reads.open("claude/a", "a");
  reads.close("claude/a");
  reads.open("claude/a", "a");
  asked[1].one.resolve({ status: 200, data: { model: "old" } });
  await new Promise((done) => setImmediate(done));
  assert.deepEqual(reads.get("claude/a"), { state: "reading" });
  asked[2].one.resolve({ status: 200, data: { model: null } });
  await new Promise((done) => setImmediate(done));
  assert.deepEqual(reads.get("claude/a"), { state: "read", model: null });

  // a failed request, and one that raised, read as no model
  reads.open("claude/b", "b");
  asked[3].one.resolve({ status: 502, data: null });
  const raising = new ModelReads(() => Promise.reject(new Error("no")), () => {});
  raising.open("claude/c", "c");
  await new Promise((done) => setImmediate(done));
  assert.deepEqual(reads.get("claude/b"), { state: "read", model: null });
  assert.deepEqual(raising.get("claude/c"), { state: "read", model: null });
});
