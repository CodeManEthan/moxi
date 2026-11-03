import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { outcomeOf } from "../src/confirm-model.ts";

// The same vectors drive the gateway's Python copy of this table, so the two stay in step.
const vectors = JSON.parse(readFileSync(new URL("./outcome-vectors.json", import.meta.url), "utf8"));

test("every shared outcome vector maps as the gateway's copy maps it", () => {
  assert.ok(vectors.length > 0);
  for (const { status, data, outcome } of vectors) {
    assert.deepEqual(outcomeOf(status, data), outcome, `${status} ${JSON.stringify(data)}`);
  }
});
