import assert from "node:assert/strict";
import test from "node:test";

import { rosterView, rowView, SERVER_ERROR, span } from "../src/views.ts";

const NOW = 10_000;
const row = (extra) => ({ qualified: "core/alpha", name: "alpha", scope: "core", live: true, kind: null,
  module: null, tier: "worker", adapter: "claude", directors: [], session_id: "s1", bound_at: NOW - 3840,
  awaiting: [], ...extra });
const up = (rows) => ({ loading: false, gone: false, failure: null, value: { state: "up", detail: null, rows } });

test("a live row: its state, adapter, time held and session", () => {
  assert.deepEqual(rowView(row({ directors: ["core/lead"], awaiting: ["core/beta"] }), NOW), {
    qualified: "core/alpha", live: true, tags: ["live"], adapter: "claude", bound: "bound for 1h4m",
    directors: ["core/lead"], awaiting: ["core/beta"], session: "s1",
  });
});

test("a standing row has no time held and no session", () => {
  const view = rowView(row({ live: false, kind: "agent", session_id: null, bound_at: null, adapter: null }), NOW);
  assert.deepEqual(view.tags, ["standing"]);
  assert.equal(view.bound, null);
  assert.equal(view.session, null);
  // A standing row's stray session id places no chip.
  assert.equal(rowView(row({ live: false }), NOW).session, null);
});

test("send-only by kind or adapter, and reserved by a module", () => {
  assert.deepEqual(rowView(row({ live: false, kind: "send-only", module: "turns", session_id: null }), NOW).tags,
                   ["standing", "send-only", "reserved by turns"]);
  assert.deepEqual(rowView(row({ adapter: "send-only", kind: "send-only", module: "turns" }), NOW).tags,
                   ["live", "send-only", "reserved by turns"]);
  assert.deepEqual(rowView(row({ adapter: "send-only" }), NOW).tags, ["live", "send-only"]);
});

test("the time held counts on the server's clock", () => {
  assert.equal(rowView(row({ bound_at: NOW - 30 }), NOW).bound, "bound for 30s");
  assert.equal(rowView(row({ bound_at: NOW + 5 }), NOW).bound, "bound for 0s");
  assert.equal(rowView(row(), null).bound, null);
  assert.equal(span(2 * 86400 + 5), "2d");
});

test("the roster's states", () => {
  assert.equal(rosterView({ loading: true, gone: false, failure: null, value: null }, null).state, "loading");
  assert.equal(rosterView(up([]), NOW).state, "empty");
  const rows = rosterView(up([row(), row({ qualified: "core/beta", live: false })]), NOW);
  assert.equal(rows.state, "up");
  assert.deepEqual(rows.rows.map((item) => item.qualified), ["core/alpha", "core/beta"]);
  const offline = rosterView({ ...up([]), value: { state: "offline", detail: null, rows: [] } }, NOW);
  assert.deepEqual([offline.state, offline.rows], ["offline", []]);
  const error = rosterView({ ...up([]), value: { state: "error", detail: null, rows: [] } }, NOW);
  assert.deepEqual([error.state, error.detail], ["error", SERVER_ERROR]);
  const failed = rosterView({ ...up([row()]), failure: { status: 500, why: "answered 500" } }, NOW);
  assert.deepEqual([failed.state, failed.rows.length, failed.detail], ["failed", 1, "answered 500"]);
  assert.equal(rosterView({ ...up([row()]), gone: true }, NOW).state, "gone");
});
