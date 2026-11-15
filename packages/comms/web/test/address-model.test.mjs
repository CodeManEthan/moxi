import assert from "node:assert/strict";
import test from "node:test";

import { addressPath, addressRoute, addressWrite, parseAddress, sameAddress, threadAddress }
  from "../src/address-model.ts";

test("each route reads as what it opens and writes back the same", () => {
  const cases = [
    ["", { piece: "none" }],
    ["agent/planner", { piece: "agent", qualified: "planner" }],
    ["agent/builder-2%40kestrel-web", { piece: "agent", qualified: "builder-2@kestrel-web" }],
    ["reply/1000000000000000001", { piece: "reply", message_id: "1000000000000000001" }],
    ["thread/builder-1/planner", { piece: "thread", a: "builder-1", b: "planner" }],
    ["mail/planner", { piece: "mail", qualified: "planner" }],
    ["others", { piece: "others" }],
  ];
  for (const [route, address] of cases) {
    assert.deepEqual(parseAddress(route), address, route);
    assert.equal(addressRoute(address), route);
    assert.deepEqual(parseAddress(addressRoute(address)), address);
  }
});

test("slashes, a query and a fragment are not part of the route; @ reads as written or encoded", () => {
  assert.deepEqual(parseAddress("/agent/planner/"), { piece: "agent", qualified: "planner" });
  assert.deepEqual(parseAddress("agent/planner?x=1#y"), { piece: "agent", qualified: "planner" });
  assert.deepEqual(parseAddress("agent/builder-2@kestrel-web"), { piece: "agent", qualified: "builder-2@kestrel-web" });
});

test("a thread's pair is put in name order", () => {
  assert.deepEqual(parseAddress("thread/planner/builder-1"), { piece: "thread", a: "builder-1", b: "planner" });
  assert.deepEqual(threadAddress("planner", "builder-1"), threadAddress("builder-1", "planner"));
  assert.equal(addressRoute(threadAddress("planner", "builder-1")), "thread/builder-1/planner");
});

test("what the page has no reading for is unknown, and writes as the page's own route", () => {
  for (const route of ["agents", "agent", "agent/a/b", "reply/12x", "reply/", "thread/a", "thread/a/a", "others/x",
                       "agent/%E0%A4%A", "agent//x"]) {
    assert.deepEqual(parseAddress(route), { piece: "unknown" }, route);
  }
  assert.equal(addressRoute({ piece: "unknown" }), "");
});

test("the address is rewritten only on the page, and only when it changes", () => {
  const at = { piece: "agent", qualified: "planner" };
  assert.equal(addressPath(at), "/m/comms/agent/planner");
  assert.equal(addressWrite(at, "/m/comms/"), "/m/comms/agent/planner");
  assert.equal(addressWrite(at, "/m/comms/agent/planner"), null);
  assert.equal(addressWrite({ piece: "none" }, "/m/comms/agent/planner"), "/m/comms/");
  assert.equal(addressWrite(at, "/m/usage/"), null);
  assert.ok(sameAddress(parseAddress("thread/b/a"), parseAddress("thread/a/b")));
  assert.ok(!sameAddress(at, { piece: "mail", qualified: "planner" }));
});
