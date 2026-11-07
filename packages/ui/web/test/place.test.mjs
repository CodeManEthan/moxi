import assert from "node:assert/strict";
import test from "node:test";

import { placeOnceMore } from "../src/place-model.ts";

// A home block's load as the modules view makes it: `place` for the block, `check` a
// fetch of the block's own script.

/** A `place` that gives each answer in turn, counting its calls. */
const placer = (...answers) => {
  const calls = { n: 0 };
  const place = async () => {
    const answer = answers[calls.n++];
    if (answer instanceof Error) throw answer;
    return answer;
  };
  return { place, calls };
};
const block = () => ({ removed: false, remove() { this.removed = true; } });
const fine = async () => null;

test("a block whose script answers fine after a failed load is placed on one more try", async () => {
  const el = block();
  const { place, calls } = placer(null, el);
  assert.deepEqual(await placeOnceMore(place, fine, () => true), { placed: el });
  assert.equal(calls.n, 2);
});

test("a block whose second try fails too comes to the could-not-load state, with no third", async () => {
  const { place, calls } = placer(null, null, block());
  assert.deepEqual(await placeOnceMore(place, fine, () => true), { failure: null });
  assert.equal(calls.n, 2);
});

test("a block whose script fetch names a failure gets it at once, with no second try", async () => {
  const found = { title: "comms's page server did not start", lines: ["It exited with code 1."] };
  const { place, calls } = placer(null, block());
  assert.deepEqual(await placeOnceMore(place, async () => found, () => true), { failure: found });
  assert.equal(calls.n, 1);
});

test("a block placed at once is not checked", async () => {
  const el = block();
  let checks = 0;
  const load = await placeOnceMore(placer(el).place, async () => { checks += 1; return null; }, () => true);
  assert.deepEqual(load, { placed: el });
  assert.equal(checks, 0);
});

test("a block load the view replaced during the retry gives nothing and removes the block", async () => {
  let shown = true;
  const el = block();
  const { place: give, calls } = placer(null, el);
  const place = async () => {
    const got = await give();
    if (calls.n === 2) shown = false;
    return got;
  };
  assert.equal(await placeOnceMore(place, fine, () => shown), null);
  assert.equal(el.removed, true);
});
