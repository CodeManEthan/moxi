import assert from "node:assert/strict";
import test from "node:test";

import {
  ago, chainText, existingText, pageView, pieceTags, recordSubject, repoFacts, span, specLines, viaText,
} from "../src/views.ts";

const command = (label) => ({ label, command: `moxi repo ${label}` });
const REPO = { root: "/r/alpha-notes", project: "alpha", git_version: "2.56.0", mode: "shim",
               pinned: true, events: [], pieces: [], problems: [], specs: [],
               specs_problem: null, records: [], commands: [] };
const SPEC = { name: "notes/snapshot", saved: true, problem: null, status: "committed",
               line: "committed abc1234 (2 paths)", time: 940, refused_since: null,
               refused_passes: null, last_commit: "abc1234def5678900", last_commit_time: 700,
               remote: "origin", pushed: [{ branch: "main", sha: "abc1234def5678900" }],
               push_failed: [], run: command("commit"), forget: command("specs") };
const state = (over) => ({ loading: false, gone: false, failure: null, value: null, ...over });

test("the page's states", () => {
  assert.equal(pageView(state({ loading: true })).state, "loading");
  assert.equal(pageView(state({ gone: true })).state, "gone");
  assert.equal(pageView(state({ value: { state: "up", detail: null, repos: [] } })).state, "empty");
  assert.deepEqual(pageView(state({ value: { state: "error", detail: "no index", repos: [] } })),
                   { state: "error", repos: [], detail: "no index" });
  const up = { state: "up", detail: null, repos: [REPO] };
  assert.deepEqual(pageView(state({ value: up })), { state: "up", repos: [REPO], detail: null });
  // A failed read keeps the repos it last read.
  const failed = pageView(state({ value: up, failure: { status: 500, why: "it answered 500" } }));
  assert.deepEqual(failed, { state: "failed", repos: [REPO], detail: "it answered 500" });
});

test("times", () => {
  assert.deepEqual([span(45), span(2460), span(3840), span(3600), span(90000)], ["45s", "41m", "1h4m", "1h", "1d"]);
  assert.equal(ago(940, 1000), "1m ago");
  assert.equal(ago(0, null), "at 1970-01-01T00:00:00Z");
});

test("a repo's facts, events and chain line", () => {
  assert.deepEqual(repoFacts(REPO), ["project alpha", "git 2.56.0", "shim mode (pinned)"]);
  assert.deepEqual(repoFacts({ ...REPO, project: null, git_version: null, mode: null }), ["git version unknown"]);
  const event = { event: "pre-commit", via: "launcher", pieces: [], existing: "/r/.githooks/pre-commit",
                  chain_line: "needed", hook: "/r/.githooks/pre-commit", line: "the line" };
  assert.equal(viaText(event), "run through the launcher, from the existing git hook's chain line");
  assert.equal(viaText({ ...event, via: null }), "every piece disabled, so nothing of repo's runs");
  assert.equal(chainText(event),
               "Chain line needed: add this line to /r/.githooks/pre-commit, before any line that reads stdin:");
  assert.equal(chainText({ ...event, chain_line: "present" }), "The chain line is in /r/.githooks/pre-commit.");
  assert.equal(chainText({ ...event, via: "shim", chain_line: null }), null);
  assert.equal(existingText(event), "Existing git hook: /r/.githooks/pre-commit");
  assert.equal(existingText({ ...event, existing: null }), "No existing git hook.");
});

test("a piece's tags", () => {
  const piece = { id: "notes/secret-gate", event: "pre-commit", enabled: true, fail: "closed", order: 20,
                  settings: [], declared: true, recorded: false, toggle: command("d"), remove: command("r") };
  assert.deepEqual(pieceTags(piece), ["fail closed", "order 20"]);
  assert.deepEqual(pieceTags({ ...piece, enabled: false, declared: false, recorded: true }),
                   ["fail closed", "order 20", "disabled", "no installed module declares it", "doctor record open"]);
});

test("a spec's lines", () => {
  assert.deepEqual(specLines(SPEC, 1000), [
    "Last pass: committed, 1m ago: committed abc1234 (2 paths)",
    "Last commit: abc1234def56, 5m ago",
    "Last push of main to origin: abc1234def56",
  ]);
  const failing = { ...SPEC, status: "refused", line: "refused: a secret", refused_since: 400, refused_passes: 3,
                    push_failed: [{ branch: "main", error: "remote says no", failed_at: 880 }], pushed: [] };
  assert.deepEqual(specLines(failing, 1000), [
    "Last pass: refused, 1m ago: refused: a secret",
    "Refused since 10m ago, 3 passes.",
    "Last commit: abc1234def56, 5m ago",
    "Push of main failed 2m ago: remote says no",
  ]);
  const fresh = { ...SPEC, status: null, line: null, time: null, last_commit: null, last_commit_time: null,
                  remote: null, pushed: [] };
  assert.deepEqual(specLines(fresh, 1000), ["No pass has run.", "Last commit: none by its author.", "It doesn't push."]);
  assert.deepEqual(specLines({ ...fresh, saved: false, problem: "bad paths" }, 1000),
                   ["The saved spec can't be read: bad paths", "No pass has run."]);
});

test("what a record is about", () => {
  const record = { kind: "push", piece: null, spec: "notes/snapshot", branch: "main", text: "t", fix: null, at: null };
  assert.equal(recordSubject(record), "spec notes/snapshot, push of main");
  assert.equal(recordSubject({ ...record, kind: "commit" }), "spec notes/snapshot");
  assert.equal(recordSubject({ ...record, kind: "piece", piece: "notes/secret-gate" }), "git hook piece notes/secret-gate");
});
