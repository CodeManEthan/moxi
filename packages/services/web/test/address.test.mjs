import assert from "node:assert/strict";
import test from "node:test";

import {
  addressPath, NO_SUCH_ADDRESS, NOTHING_OPEN, noSuchProject, noSuchRow, parseRoute, pathToWrite, resolve, routeOf,
} from "../src/address.ts";

const at = (project, row, panel = null) => ({ project, row, panel });

test("each address round-trips through its route", () => {
  const cases = [
    ["", NOTHING_OPEN],
    ["row/web", at(null, "services/web")],
    ["row/slots/server", at(null, "slots/server")],
    ["add", at(null, null, "add")],
    ["adopt", at(null, null, "adopt")],
    ["project/atlas-api", at("atlas-api", null)],
    ["project/atlas-api/row/web", at("atlas-api", "services/web")],
    ["project/a%20b/row/ui/shell", at("a b", "ui/shell")],
  ];
  for (const [route, address] of cases) {
    assert.deepEqual(parseRoute(route), { address, known: true }, route);
    assert.equal(routeOf(address), route, route);
  }
  assert.deepEqual(parseRoute("/row/web/").address, at(null, "services/web"));
  assert.equal(addressPath(at(null, "cache/watch")), "/m/services/row/cache/watch");
});

test("a route that names nothing the page opens is the page with nothing opened", () => {
  for (const route of ["rows", "row", "row/a/b/c", "project", "project/x/add", "project/x/y", "row/%E0%A4%A",
                       "add/more", "row//x"]) {
    assert.deepEqual(parseRoute(route), { address: NOTHING_OPEN, known: false }, route);
  }
});

test("the address is written only where it changes, and only at the page", () => {
  assert.equal(pathToWrite(at(null, "services/web"), "/m/services/"), "/m/services/row/web");
  assert.equal(pathToWrite(at(null, "services/web"), "/m/services/row/web"), null);
  assert.equal(pathToWrite(NOTHING_OPEN, "/m/projects/"), null);
});

test("an address naming what the page doesn't have opens nothing, with the foot's line", () => {
  const ids = ["services/web", "ui/shell"];
  assert.deepEqual(resolve(at("atlas", "services/web"), ids, ["atlas"], true),
                   { address: at("atlas", "services/web"), unknown: null });
  assert.deepEqual(resolve(at(null, "services/gone"), ids, null, true),
                   { address: NOTHING_OPEN, unknown: "No service is named gone." });
  assert.deepEqual(resolve(at(null, "cache/watch"), ids, null, true).unknown, "No service is named cache/watch.");
  assert.deepEqual(resolve(at("zeta", null), ids, ["atlas"], true),
                   { address: NOTHING_OPEN, unknown: noSuchProject("zeta") });
  // Without projects there is no filter.
  assert.equal(resolve(at("atlas", null), ids, null, true).unknown, "No project is named atlas.");
  // The panel is the desk's.
  assert.deepEqual(resolve(at(null, null, "add"), ids, null, false), { address: NOTHING_OPEN, unknown: NO_SUCH_ADDRESS });
  assert.deepEqual(resolve(at(null, null, "adopt"), ids, null, true), { address: at(null, null, "adopt"), unknown: null });
  assert.equal(noSuchRow("services/web"), "No service is named web.");
});
