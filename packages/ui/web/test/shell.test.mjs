import assert from "node:assert/strict";
import test from "node:test";

import { menuEntries, where } from "../src/shell.ts";

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
