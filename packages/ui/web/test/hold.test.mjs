import assert from "node:assert/strict";
import test from "node:test";

import { Gate } from "../src/hold-model.ts";
import { panelSignature, where } from "../src/shell.ts";

/** The browser's history: entries, the one shown, and popstate on a step. */
class Steps {
  constructor(path) {
    this.entries = [{ path, state: null }];
    this.index = 0;
    this.onPopstate = () => {};
  }
  get state() { return this.entries[this.index].state; }
  get path() { return this.entries[this.index].path; }
  get paths() { return this.entries.map((entry) => entry.path); }
  pushState(state, _unused, url) {
    this.entries.splice(this.index + 1);
    this.entries.push({ path: url, state });
    this.index += 1;
  }
  replaceState(state, _unused, url) { this.entries[this.index] = { path: url, state }; }
  go(by) {
    this.index += by;
    this.onPopstate();
  }
  back() { this.go(-1); }
  forward() { this.go(1); }
}

const SETTINGS = "/m/ui/settings";
const mod = (name, title, url = `/m/${name}/page.js`) => ({ name, page: { title, url, contract: 1 }, refused: null });

/**
 * The shell's moves as `shell.ts` wires them to the gate: a link (band pick, the brand,
 * a page's own) and `navigate()` push and draw; popstate goes through `onPopstate`; a
 * modules or session event asks `refresh` with the panel's signature.
 */
function shell(start = SETTINGS, history = []) {
  const steps = new Steps(history[0] ?? start);
  for (const path of history.slice(1)) steps.pushState(null, "", path);
  if (history.length > 0) steps.pushState(null, "", start);
  let modules = [mod("alpha", "Alpha")];
  const sig = () => {
    const at = where(steps.path.split(/[?#]/)[0]);
    return panelSignature(at, modules, at !== null, at !== null);
  };
  const gate = new Gate(steps, () => steps.path);
  const panel = { at: null, draws: 0, shown: "" };
  const draw = () => {
    panel.at = steps.path;
    panel.draws += 1;
    panel.shown = sig();
    gate.markDrawn();
  };
  steps.onPopstate = () => gate.onPopstate(draw);
  const link = (path) => gate.move(() => {
    if (path !== steps.path) steps.pushState(null, "", path);
    draw();
  });
  const asks = [];
  const hold = () => gate.hold((go) => asks.push(go));
  draw();
  return {
    steps, gate, panel, asks, hold, link, navigate: link,
    modulesEvent(next) {
      if (next !== undefined) modules = next;
      gate.refresh(sig() !== panel.shown, draw);
    },
    sessionEvent(signedIn) {
      if (signedIn) gate.refresh(true, draw);
    },
  };
}

// --- with no hold, every path moves at once ------------------------------------------------

test("with no hold, a link, navigate(), Back and Forward each move at once", () => {
  const s = shell();
  s.link("/m/alpha/");
  assert.equal(s.panel.at, "/m/alpha/");
  s.link("/m/ui/");
  assert.equal(s.panel.at, "/m/ui/");
  s.navigate("/m/ui/modules");
  assert.equal(s.panel.at, "/m/ui/modules");
  s.steps.back();
  assert.equal(s.panel.at, "/m/ui/");
  s.steps.forward();
  assert.equal(s.panel.at, "/m/ui/modules");
  assert.equal(s.panel.draws, 6);
  assert.deepEqual(s.asks, []);
});

test("with no hold, a modules event that changes the panel draws it, and a session event draws it", () => {
  const s = shell("/m/alpha/");
  s.modulesEvent([mod("alpha", "Alpha", "/m/alpha/page.js?v=2")]);
  assert.equal(s.panel.draws, 2);
  s.sessionEvent(true);
  assert.equal(s.panel.draws, 3);
  s.sessionEvent(false);
  assert.equal(s.panel.draws, 3);
});

test("a modules event after the page writes its own address, with no hold, does not draw", () => {
  const s = shell();
  s.gate.setAddress("/m/ui/settings/alpha/more");
  assert.equal(s.steps.path, "/m/ui/settings/alpha/more");
  assert.equal(s.steps.entries.length, 1, "the entry is replaced, not added");
  assert.equal(s.gate.placed, "/m/ui/settings/alpha/more");
  s.modulesEvent([mod("alpha", "Alpha"), mod("beta", "Beta")]);
  assert.equal(s.panel.draws, 1);
  assert.equal(s.panel.at, SETTINGS);
});

test("the page's own address keeps the entry's state", () => {
  const steps = new Steps(SETTINGS);
  steps.entries[0].state = { kept: 1 };
  const gate = new Gate(steps, () => steps.path);
  gate.setAddress("/m/ui/settings/alpha");
  assert.deepEqual(steps.entries, [{ path: "/m/ui/settings/alpha", state: { kept: 1 } }]);
});

test("the panel's signature is its module, page name and facts, not its path", () => {
  const mods = [mod("alpha", "Alpha")];
  const sig = (path, list = mods) => panelSignature(where(path), list, true, true);
  assert.equal(sig("/m/ui/settings"), sig("/m/ui/settings/alpha/more"));
  assert.equal(sig("/m/alpha/"), sig("/m/alpha/deep/route"));
  assert.notEqual(sig("/m/ui/"), sig("/m/ui/settings"));
  assert.notEqual(sig("/m/ui/"), sig("/m/ui/modules"));
  assert.notEqual(sig("/m/alpha/"), sig("/m/alpha/", [mod("alpha", "Alpha", "/m/alpha/page.js?v=2")]));
  assert.notEqual(sig("/m/alpha/"), sig("/m/alpha/", [mod("alpha", "Alpha 2")]));
  assert.notEqual(panelSignature(where("/m/alpha/"), mods, true, true),
                  panelSignature(where("/m/alpha/"), mods, false, true));
  assert.equal(panelSignature(null, mods, false, false), "none");
});

// --- with a hold, each path asks and waits for go() ----------------------------------------

for (const [name, path] of [["a band pick", "/m/alpha/"], ["the Moxi link", "/m/ui/"],
                            ["a link the page draws", "/m/ui/modules"]]) {
  test(`${name} asks the holding page and moves only on go()`, () => {
    const s = shell();
    s.hold();
    s.link(path);
    assert.equal(s.asks.length, 1);
    assert.equal(s.panel.at, SETTINGS);
    assert.equal(s.steps.path, SETTINGS);
    assert.equal(s.panel.draws, 1);
    s.asks[0]();
    assert.equal(s.gate.held, false);
    assert.equal(s.panel.at, path);
    assert.deepEqual(s.steps.paths, [SETTINGS, path]);
  });
}

test("navigate() asks the holding page and moves only on go()", () => {
  const s = shell();
  s.hold();
  s.navigate("/m/alpha/x");
  assert.equal(s.asks.length, 1);
  assert.deepEqual(s.steps.paths, [SETTINGS]);
  assert.equal(s.panel.draws, 1);
  s.asks[0]();
  assert.equal(s.panel.at, "/m/alpha/x");
});

test("Back with a hold puts the page's address back and asks; go() lands on the entry it was going to", () => {
  const s = shell(SETTINGS, ["/m/ui/", "/m/alpha/"]);
  assert.deepEqual(s.steps.paths, ["/m/ui/", "/m/alpha/", SETTINGS]);
  s.gate.setAddress("/m/ui/settings/alpha");
  s.hold();
  s.steps.back();
  assert.equal(s.asks.length, 1);
  assert.equal(s.steps.path, "/m/ui/settings/alpha", "the page's own address is put back");
  assert.deepEqual(s.steps.paths, ["/m/ui/", "/m/alpha/", "/m/ui/settings/alpha"]);
  assert.equal(s.panel.draws, 1);
  s.asks[0]();
  assert.equal(s.gate.held, false);
  assert.equal(s.steps.path, "/m/alpha/");
  assert.equal(s.panel.at, "/m/alpha/");
  // The put-back entry stays after the target, so Forward returns to Settings.
  s.steps.forward();
  assert.equal(s.panel.at, "/m/ui/settings/alpha");
});

test("Forward with a hold puts the page's address back and asks; go() lands on the entry it was going to", () => {
  const s = shell("/m/alpha/", ["/m/ui/", SETTINGS]);
  s.steps.back();
  assert.equal(s.panel.at, SETTINGS);
  s.hold();
  s.steps.forward();
  assert.equal(s.asks.length, 1);
  assert.equal(s.steps.path, SETTINGS, "the page's address is put back");
  assert.equal(s.panel.at, SETTINGS);
  assert.equal(s.panel.draws, 2);
  s.asks[0]();
  assert.equal(s.steps.path, "/m/alpha/");
  assert.equal(s.panel.at, "/m/alpha/");
  assert.deepEqual(s.steps.paths, ["/m/ui/", SETTINGS, "/m/alpha/", SETTINGS]);
});

test("Stay leaves the page where it is, its address shown", () => {
  const s = shell(SETTINGS, ["/m/ui/"]);
  s.gate.setAddress("/m/ui/settings/alpha/more");
  s.hold();
  s.steps.back();
  assert.equal(s.steps.path, "/m/ui/settings/alpha/more");
  assert.equal(s.panel.at, SETTINGS);
  assert.equal(s.panel.draws, 1);
  assert.equal(s.gate.held, true);
});

test("a modules event that changes the panel while held marks it stale and draws it on release", () => {
  const s = shell();
  const release = s.hold();
  s.modulesEvent([mod("alpha", "Alpha"), { name: "ui", page: { title: "Modules", url: "/m/ui/page.js?v=2", contract: 1 }, refused: null }]);
  assert.equal(s.panel.draws, 1);
  assert.equal(s.gate.stale, true);
  assert.deepEqual(s.asks, [], "an event does not ask");
  release();
  assert.equal(s.panel.draws, 2);
  assert.equal(s.gate.stale, false);
});

test("a session event while held marks the panel stale and draws it on release", () => {
  const s = shell();
  const release = s.hold();
  s.sessionEvent(true);
  assert.equal(s.panel.draws, 1);
  assert.equal(s.gate.stale, true);
  release();
  assert.equal(s.panel.draws, 2);
});

test("a stale panel released by go() is drawn once, by the move", () => {
  const s = shell();
  s.hold();
  s.sessionEvent(true);
  s.link("/m/alpha/");
  s.asks[0]();
  assert.equal(s.panel.draws, 2);
  assert.equal(s.panel.at, "/m/alpha/");
  assert.equal(s.gate.stale, false);
});

test("while held the gate says so, for beforeunload; released, it does not", () => {
  const s = shell();
  assert.equal(s.gate.held, false);
  const release = s.hold();
  assert.equal(s.gate.held, true);
  release();
  assert.equal(s.gate.held, false);
});

// --- the hold's edges ------------------------------------------------------------------------

test("go() moves once, however often it is called", () => {
  const s = shell();
  s.hold();
  s.link("/m/alpha/");
  s.asks[0]();
  s.asks[0]();
  assert.deepEqual(s.steps.paths, [SETTINGS, "/m/alpha/"]);
  assert.equal(s.panel.draws, 2);
});

test("a release after go() or from a hold replaced does nothing", () => {
  const s = shell();
  const first = s.hold();
  const second = s.hold();
  first();
  assert.equal(s.gate.held, true);
  s.link("/m/alpha/");
  s.asks[0]();
  second();
  assert.equal(s.gate.held, false);
  assert.equal(s.panel.draws, 2);
});

test("go() after the page took the hold again asks again", () => {
  const s = shell();
  const release = s.hold();
  s.link("/m/alpha/");
  release();
  s.hold();
  s.asks[0]();
  assert.equal(s.asks.length, 2);
  assert.equal(s.panel.at, SETTINGS);
  s.asks[1]();
  assert.equal(s.panel.at, "/m/alpha/");
});

test("the hold takes a function", () => {
  const s = shell();
  assert.throws(() => s.gate.hold("no"), TypeError);
});
