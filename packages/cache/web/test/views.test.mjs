import assert from "node:assert/strict";
import test from "node:test";

import { chipView, NO_RECORD, SERVER_ERROR, WATCHER_STOPPED } from "../src/views.ts";

const NOW = 10_000;
const row = (extra) => ({ session: "s1", harness: "claude", kind: "warm", last_request: NOW - 60,
  ttl: 3600, warn_before: 900, seen_at: NOW - 5, stale_after: 30, code: null, ...extra });
const up = (clocks) => ({ loading: false, gone: false, failure: null, value: { state: "up", detail: null, clocks } });

test("the chip, loading", () => {
  const view = chipView({ loading: true, gone: false, failure: null, value: null }, "s1", null);
  assert.equal(view.state, "loading");
  assert.equal(view.tone, "loading");
});

test("the chip's bands and the dimmed one", () => {
  assert.deepEqual(chipView(up([row()]), "s1", NOW),
                   { state: "up", shows: "warm", dim: false, tone: "warm", text: "warm 59m left", title: null });
  const dim = chipView(up([row({ seen_at: NOW - 31 })]), "s1", NOW);
  assert.equal(dim.dim, true);
  assert.equal(dim.title, WATCHER_STOPPED);
  assert.equal(dim.text, "warm 59m left");
});

test("a kind's word has its code as the title", () => {
  const view = chipView(up([row({ kind: "fresh", code: "no-request" })]), "s1", NOW);
  assert.equal(view.text, "fresh");
  assert.equal(view.tone, "word");
  assert.equal(view.title, "no-request");
});

test("no row for the session, or rule 2, is no reading", () => {
  for (const clocks of [[], [row({ session: "other" })], [row({ ttl: null })]]) {
    const view = chipView(up(clocks), "s1", NOW);
    assert.equal(view.text, "no reading");
    assert.equal(view.tone, "muted");
    assert.equal(view.title, NO_RECORD);
  }
});

test("a server error and a failed request are both 'cache ?'", () => {
  const error = chipView({ loading: false, gone: false, failure: null,
                           value: { state: "error", detail: "it broke", clocks: [] } }, "s1", NOW);
  assert.deepEqual([error.state, error.text, error.title], ["error", "cache ?", "it broke"]);
  const bare = chipView({ loading: false, gone: false, failure: null,
                          value: { state: "error", detail: null, clocks: [] } }, "s1", NOW);
  assert.equal(bare.title, SERVER_ERROR);
  const failed = chipView({ ...up([row()]), failure: { status: 502, why: "answered 502" } }, "s1", NOW);
  assert.deepEqual([failed.state, failed.text, failed.title], ["failed", "cache ?", "answered 502"]);
});

test("a 404 draws nothing", () => {
  assert.equal(chipView({ ...up([]), gone: true }, "s1", NOW).state, "gone");
});
