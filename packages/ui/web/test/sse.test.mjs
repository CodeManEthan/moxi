import assert from "node:assert/strict";
import test from "node:test";

import { SseParser } from "../src/sse.ts";

test("named events, comments and multi-line data", () => {
  const parser = new SseParser();
  const got = parser.feed(": heartbeat\n\nevent: ui.servers\ndata: {\"a\":1}\n\nevent: moxi.state\ndata: x\ndata: y\n\n");
  assert.deepEqual(got, [{ event: "ui.servers", data: "{\"a\":1}" }, { event: "moxi.state", data: "x\ny" }]);
});

test("a frame split across chunks, CRLF split between them", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.feed("event: a.b\r"), []);
  assert.deepEqual(parser.feed("\ndata: 1"), []);
  assert.deepEqual(parser.feed("\r\n\r\n"), [{ event: "a.b", data: "1" }]);
});

test("a lone CR ends a line, and an event without data is no event", () => {
  const parser = new SseParser();
  // A CR that ends the text may be half of a CRLF, so that line waits for the next chunk.
  assert.deepEqual(parser.feed("event: a.b\r\revent: c.d\rdata:2\r\r"), []);
  assert.deepEqual(parser.feed(": next\n"), [{ event: "c.d", data: "2" }]);
});

test("an event that names no type is a message", () => {
  assert.deepEqual(new SseParser().feed("data: z\n\n"), [{ event: "message", data: "z" }]);
});
