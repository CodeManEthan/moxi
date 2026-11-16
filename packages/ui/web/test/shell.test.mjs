import assert from "node:assert/strict";
import test from "node:test";

import { bandEntries, bandTab, isCurrent, loadPanel, pageName, settingsShown, topName, where } from "../src/shell.ts";

test("the URL names the module and the route", () => {
  assert.deepEqual(where("/m/ui/"), { module: "ui", route: "" });
  assert.deepEqual(where("/m/fixture/a/b"), { module: "fixture", route: "a/b" });
  assert.equal(where("/m/Bad/"), null);
  assert.equal(where("/m/ui"), null);
  assert.equal(where("/elsewhere"), null);
});

const page = (title, group, order) => ({ title, group, order });
const mod = (name, title, group, order) => ({ name, page: page(title, group, order), refused: null });
const names = (entries) => entries.map((e) => `${e.group}:${e.module}/${e.route}:${e.title}`);

test("the band: Home first, each group by order then title, Modules last", () => {
  const entries = bandEntries([
    mod("zeta", "Zeta", "setup", 10),
    mod("beta", "Beta", "daily", 30),
    mod("ui", "Modules", "setup", 500),
    mod("delta", "Delta", "daily", 30),
    mod("alpha", "Alpha", "daily", 40),
    mod("gamma", "Gamma", "daily", 5),
    mod("eta", "Eta", "setup", 5),
    { name: "nopage", page: null, refused: "its script is missing" },
  ]);
  assert.deepEqual(names(entries), [
    "daily:ui/:Home", "daily:gamma/:Gamma", "daily:beta/:Beta", "daily:delta/:Delta", "daily:alpha/:Alpha",
    "setup:eta/:Eta", "setup:zeta/:Zeta", "setup:ui/modules:Modules"]);
});

test("a page with a wrong or absent group is in setup, and one with no order is at 500", () => {
  const entries = bandEntries([
    mod("alpha", "Alpha", "dialy", 10),
    mod("beta", "Beta", null, null),
    mod("gamma", "Gamma", "setup", 499),
    mod("delta", "Delta", "setup", 501),
    { name: "eta", page: { title: "Eta" }, refused: null },
  ]);
  assert.deepEqual(names(entries), [
    "daily:ui/:Home", "setup:alpha/:Alpha", "setup:gamma/:Gamma", "setup:beta/:Beta", "setup:eta/:Eta",
    "setup:delta/:Delta", "setup:ui/modules:Modules"]);
});

test("the band holds the shell's own two pages with no module, and never Settings", () => {
  assert.deepEqual(names(bandEntries([])), ["daily:ui/:Home", "setup:ui/modules:Modules"]);
  const full = bandEntries([mod("ui", "Modules", "setup", 500), mod("alpha", "Settings", "daily", 1)]);
  assert.deepEqual(full.filter((e) => e.module === "ui").map((e) => e.route), ["", "modules"]);
});

test("the current entry: the module's page, and on the shell's own routes Home or Modules only", () => {
  const home = { module: "ui", route: "" };
  const modules = { module: "ui", route: "modules" };
  const alpha = { module: "alpha", route: "" };
  const current = (path) => [home, modules, alpha].filter((entry) => isCurrent(entry, where(path)))
    .map((entry) => `${entry.module}/${entry.route}`);
  assert.deepEqual(current("/m/ui/"), ["ui/"]);
  assert.deepEqual(current("/m/ui/modules"), ["ui/modules"]);
  assert.deepEqual(current("/m/ui/settings"), []);
  assert.deepEqual(current("/m/ui/settings/alpha"), []);
  assert.deepEqual(current("/m/alpha/"), ["alpha/"]);
  assert.deepEqual(current("/m/alpha/deep/route"), ["alpha/"]);
  assert.deepEqual(current("/elsewhere"), []);
});

test("the page's name on every route, and none where the shell shows a message", () => {
  const mods = [mod("ui", "Modules", "setup", 500), mod("alpha", "Alpha", "daily", 1),
                { name: "nopage", page: null, refused: null }];
  const name = (path) => pageName(where(path), mods);
  assert.equal(name("/m/ui/"), "Home");
  assert.equal(name("/m/ui/modules"), "Modules");
  assert.equal(name("/m/ui/settings"), "Settings");
  assert.equal(name("/m/ui/settings/alpha"), "Settings");
  assert.equal(name("/m/ui/settingsx"), null);
  assert.equal(name("/m/ui/elsewhere"), null);
  assert.equal(name("/m/alpha/"), "Alpha");
  assert.equal(name("/m/alpha/a/b"), "Alpha");
  assert.equal(name("/m/nopage/"), null);
  assert.equal(name("/m/missing/"), null);
  assert.equal(name("/elsewhere"), null);
});

test("on the phone the top line names Home on a settings route, and every other page as the desk does", () => {
  const mods = [mod("ui", "Modules", "setup", 500), mod("alpha", "Alpha", "daily", 1)];
  const top = (path, gateway) => topName(gateway, where(path), mods);
  assert.equal(top("/m/ui/settings", true), "Home");
  assert.equal(top("/m/ui/settings/alpha", true), "Home");
  assert.equal(top("/m/ui/settings", false), "Settings");
  for (const path of ["/m/ui/", "/m/ui/modules", "/m/alpha/", "/m/ui/settingsx", "/elsewhere"]) {
    assert.equal(top(path, true), pageName(where(path), mods), path);
  }
});

test("the top line's Settings is hidden on the Settings page and on the phone, and shown elsewhere", () => {
  const shown = (path, gateway = false) => settingsShown(gateway, where(path));
  for (const path of ["/m/ui/", "/m/ui/modules", "/m/alpha/", "/m/ui/settingsx", "/elsewhere"]) {
    assert.equal(shown(path), true, path);
    assert.equal(shown(path, true), false, path);
  }
  for (const path of ["/m/ui/settings", "/m/ui/settings/", "/m/ui/settings/alpha/more", "/m/ui/settings/changed"]) {
    assert.equal(shown(path), false, path);
  }
  // Tab skips it as where it is hidden on the phone.
  assert.equal(bandTab(3, 4, false, shown("/m/ui/settings")), "close");
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

test("Tab with the band open: from the button to the first name, from the last to Settings, then on", () => {
  const tab = (from, settingsShown = true) => bandTab(from, 4, false, settingsShown);
  assert.equal(tab("button"), "first");
  assert.equal(tab(3), "settings");
  assert.equal(tab("settings"), "close");
  // Between the names, and from anywhere else, the browser's own Tab moves on.
  for (const from of [0, 1, 2, null]) assert.equal(tab(from), null);
});

test("Tab from the band's last name closes it where Settings is hidden", () => {
  assert.equal(bandTab(3, 4, false, false), "close");
  assert.equal(bandTab("button", 4, false, false), "first");
});

test("Shift+Tab with the band open walks back the same way", () => {
  const back = (from, settingsShown = true) => bandTab(from, 4, true, settingsShown);
  assert.equal(back("settings"), "last");
  assert.equal(back(0), "button");
  for (const from of ["button", 1, 2, 3, null]) assert.equal(back(from), null);
  assert.equal(back(0, false), "button");
});

test("a band of one name is its first and its last", () => {
  assert.equal(bandTab("button", 1, false, true), "first");
  assert.equal(bandTab(0, 1, false, true), "settings");
  assert.equal(bandTab(0, 1, false, false), "close");
  assert.equal(bandTab(0, 1, true, true), "button");
  assert.equal(bandTab("settings", 1, true, true), "last");
});

test("a band with no names leaves Tab alone", () => {
  for (const from of ["button", "settings", null]) {
    assert.equal(bandTab(from, 0, false, true), null);
    assert.equal(bandTab(from, 0, true, true), null);
  }
});
