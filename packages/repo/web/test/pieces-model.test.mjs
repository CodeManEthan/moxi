import assert from "node:assert/strict";
import test from "node:test";

import { failSentence, piecesView } from "../src/pieces-model.ts";
import { offered, piece, repo } from "./fixtures.mjs";

const SIX = [
  offered({ id: "repo/commit-rules", event: "commit-msg", order: 50 }),
  offered({ id: "repo/commit-proof", event: "commit-msg", order: 60 }),
  offered({ id: "notes/secret-gate", event: "pre-commit", order: 10 }),
  offered({ id: "notes/push-gate", event: "pre-push", order: 10 }),
  offered({ id: "loadout/reinstall-commit", event: "post-commit", fail: "open" }),
  offered({ id: "loadout/reinstall-merge", event: "post-merge", fail: "open" }),
];

test("Pieces folded: the count and the first three names in run order", () => {
  const view = piecesView({ offered: SIX, refused: [], repos: [] });
  assert.equal(view.count, "6 offered");
  assert.equal(view.names, "secret-gate, commit-rules, commit-proof and 3 more");
  const two = piecesView({ offered: SIX.slice(0, 2), refused: [], repos: [] });
  assert.equal(two.names, "commit-rules, commit-proof");
});

test("each piece's row: module, where it is in and off, its event", () => {
  const repos = [repo({ pieces: [piece({ id: "notes/secret-gate" })] }),
                 repo({ pieces: [piece({ id: "notes/secret-gate", enabled: false })] }),
                 repo({ pieces: [piece({ id: "repo/commit-rules" })] })];
  const view = piecesView({ offered: SIX, refused: [{ id: "x/bad", why: "it names no event" }], repos });
  const row = (id) => view.rows.find((r) => r.piece.id === id);
  assert.deepEqual([row("notes/secret-gate").from, row("notes/secret-gate").where, row("notes/secret-gate").event,
                    row("notes/secret-gate").git], ["from notes", "in 2 repos, off in 1", "before a commit", "pre-commit"]);
  assert.equal(row("repo/commit-rules").where, "in 1 repo");
  assert.equal(row("repo/commit-proof").where, "in no repo");
  assert.deepEqual(view.refused, ["x/bad can't be offered: it names no event"]);
});

test("the fail sentence", () => {
  assert.equal(failSentence(SIX), "If one of these breaks, the commit or push is refused; reinstall-commit and "
               + "reinstall-merge let it go on and leave a record. A piece is in no repo until you add it there.");
  assert.equal(failSentence(SIX.slice(0, 2)),
               "If one of these breaks, the commit or push is refused. A piece is in no repo until you add it there.");
  assert.equal(failSentence(SIX.slice(4, 5)), "If one of these breaks, reinstall-commit lets it go on and leaves a "
               + "record. A piece is in no repo until you add it there.");
  assert.equal(failSentence([]), null);
});
