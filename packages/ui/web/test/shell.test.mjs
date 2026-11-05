import assert from "node:assert/strict";
import test from "node:test";

import { loadPanel, menuEntries, where } from "../src/shell.ts";

test("the URL names the module and the route", () => {
  assert.deepEqual(where("/m/ui/"), { module: "ui", route: "" });
  assert.deepEqual(where("/m/fixture/a/b"), { module: "fixture", route: "a/b" });
  assert.equal(where("/m/Bad/"), null);
  assert.equal(where("/m/ui"), null);
  assert.equal(where("/elsewhere"), null);
});

test("the menu: the shell's panel first, the others by name, then Settings", () => {
  const page = (title) => ({ title });
  const entries = menuEntries([
    { name: "zeta", page: page("Zeta"), refused: null },
    { name: "alpha", page: page("Alpha"), refused: null },
    { name: "ui", page: page("Modules"), refused: null },
    { name: "nopage", page: null, refused: "its script is missing" },
  ]);
  assert.deepEqual(entries.map((e) => `${e.module}/${e.route}:${e.title}`),
                   ["ui/:Modules", "alpha/:Alpha", "zeta/:Zeta", "ui/settings:Settings"]);
});

test("on the phone the menu leaves out Settings, which only the desk reaches", () => {
  const entries = menuEntries([{ name: "ui", page: { title: "Modules" }, refused: null }], false);
  assert.deepEqual(entries.map((e) => `${e.module}/${e.route}`), ["ui/"]);
});

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
const panel = () => ({ removed: false, remove() { this.removed = true; } });
const DID_NOT_LOAD = { title: "fixture's panel did not load",
                       lines: ["fixture's panel script could not be loaded. Run moxi doctor fixture to see why."] };

test("a panel placed at once is not checked or tried again", async () => {
  const el = panel();
  const { place, calls } = placer(el);
  let checks = 0;
  const load = await loadPanel("fixture", place, async () => { checks += 1; return null; }, () => true);
  assert.deepEqual(load, { placed: el });
  assert.equal(calls.n, 1);
  assert.equal(checks, 0);
});

test("a panel script found fine after a failed load gets one more try, which places it", async () => {
  const el = panel();
  const { place, calls } = placer(null, el);
  let checks = 0;
  const load = await loadPanel("fixture", place, async () => { checks += 1; return null; }, () => true);
  assert.deepEqual(load, { placed: el });
  assert.equal(calls.n, 2);
  assert.equal(checks, 1);
});

test("one more try that fails too says the panel did not load, with no third", async () => {
  const { place, calls } = placer(null, new Error("dropped again"), panel());
  const quiet = console.error;
  console.error = () => {};
  try {
    const load = await loadPanel("fixture", place, async () => null, () => true);
    assert.deepEqual(load, { error: DID_NOT_LOAD });
  } finally {
    console.error = quiet;
  }
  assert.equal(calls.n, 2);
});

test("a failure the check names is shown, with no second try", async () => {
  const found = { title: "fixture's page server did not start", lines: ["exit 1"] };
  const { place, calls } = placer(null, panel());
  const load = await loadPanel("fixture", place, async () => found, () => true);
  assert.deepEqual(load, { error: found });
  assert.equal(calls.n, 1);
});

test("a load another one replaced during the check gives nothing and no second try", async () => {
  let shown = true;
  const { place, calls } = placer(null, panel());
  const check = async () => { shown = false; return null; };
  assert.equal(await loadPanel("fixture", place, check, () => shown), null);
  assert.equal(calls.n, 1);
});

test("a load another one replaced during the second try gives nothing and removes its panel", async () => {
  let shown = true;
  const el = panel();
  const { place: give, calls } = placer(null, el);
  const place = async () => {
    const got = await give();
    if (calls.n === 2) shown = false;
    return got;
  };
  assert.equal(await loadPanel("fixture", place, async () => null, () => shown), null);
  assert.equal(el.removed, true);
});
