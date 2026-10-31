import assert from "node:assert/strict";
import test from "node:test";

import { ago, doctorRows, failurePanel, homeBlocks, moduleLines, moduleOrder, serverView, serverViews } from "../src/views.ts";

const NOW = Date.parse("2026-10-30T12:00:00Z");

const row = (extra) => ({
  module: "fixture", state: "idle", server_id: null, version: null, transport: null, pid: null,
  child: false, log: "/state/ui/log/shell.log", starts: 0, failed_starts: 0, failed_in_row: 0,
  crashes: 0, last_exit: null, last_exit_at: null, last_crash_at: null, reason: null, ...extra,
});

test("times read as how long ago", () => {
  assert.equal(ago("2026-10-30T11:59:30Z", NOW), "just now");
  assert.equal(ago("2026-10-30T11:45:00Z", NOW), "15 min ago");
  assert.equal(ago("2026-10-30T09:00:00Z", NOW), "3 h ago");
  assert.equal(ago(null, NOW), "");
});

test("a running server: its pid, version and transport", () => {
  const view = serverView(row({ state: "running", pid: 41, version: "0.1.0", transport: "unix", child: true, starts: 1 }), NOW);
  assert.equal(view.tone, "ok");
  assert.equal(view.state, "running");
  assert.deepEqual(view.lines.map((line) => line.text),
                   ["pid 41, version 0.1.0, over a private socket", "1 start since the shell started"]);
});

test("an idle server, and one that crashed within the hour", () => {
  assert.equal(serverView(row({}), NOW).tone, "off");
  const view = serverView(row({ starts: 2, crashes: 1, last_exit: -9, last_exit_at: "2026-10-30T11:50:00Z",
                                last_crash_at: "2026-10-30T11:50:00Z" }), NOW);
  assert.equal(view.tone, "warn");
  const text = view.lines.map((line) => line.text);
  assert.ok(text.includes("2 starts, 1 crash since the shell started"));
  assert.ok(text.includes("last exited with code -9, 10 min ago"));
  assert.ok(text.includes("crashed 10 min ago"));
  assert.ok(text.includes("log: /state/ui/log/shell.log"));
  // An hour later the crash is no longer a warning.
  assert.equal(serverView(row({ crashes: 1, last_crash_at: "2026-10-30T10:30:00Z" }), NOW).tone, "off");
});

test("one or two failed starts are no warning, and three in a row fail, as the doctor reads them", () => {
  for (const n of [1, 2]) {
    // Inside the 10 s wait before the next try, and after it.
    const failed = serverView(row({ state: "failed", reason: "exited 3 before ready", failed_starts: n, failed_in_row: n, starts: n }), NOW);
    assert.equal(failed.tone, "off");
    assert.equal(failed.state, "not running");
    assert.equal(failed.lines[0].text, "The last start failed: exited 3 before ready");
    assert.ok(failed.lines.every((line) => line.tone === null));
    const later = serverView(row({ failed_starts: n, failed_in_row: n, starts: n }), NOW);
    assert.equal(later.tone, "off");
    assert.equal(later.state, "not running");
  }
  assert.equal(serverView(row({ state: "failed", failed_starts: 1, failed_in_row: 1, starts: 1 }), NOW).lines[0].text,
               "The last start failed.");
  const thrice = serverView(row({ failed_starts: 3, failed_in_row: 3, starts: 3 }), NOW);
  assert.equal(thrice.tone, "fail");
  assert.ok(thrice.lines.some((line) => line.text === "the last 3 starts failed"));
  const failing = serverView(row({ state: "failed", reason: "exited 3 before ready", failed_starts: 3, failed_in_row: 3, starts: 3 }), NOW);
  assert.equal(failing.tone, "fail");
});

test("a server draining for another version says it restarts", () => {
  const view = serverView(row({ state: "draining", pid: 5, version: "0.1.0" }), NOW);
  assert.equal(view.tone, "warn");
  assert.equal(view.state, "restarting");
});

test("the servers block's rows come by module name, and a bad value gives none", () => {
  const views = serverViews({ servers: [row({ module: "zeta" }), row({ module: "alpha" }), { nope: 1 }] }, NOW);
  assert.deepEqual(views.map((view) => view.module), ["alpha", "zeta"]);
  assert.deepEqual(serverViews(null, NOW), []);
  assert.deepEqual(serverViews({ servers: "x" }, NOW), []);
});

const mod = (name, extra = {}) => ({
  name, state: "present", reason: "", version: "0.1.0", summary: "", page: true, mounted: true,
  export_error: null, components: [], refused: [], ...extra,
});

test("the modules view: the shell first, present modules, then the rest", () => {
  const order = moduleOrder([mod("zeta"), mod("broken", { state: "broken", reason: "no module" }), mod("alpha"), mod("ui")]);
  assert.deepEqual(order.map((item) => item.name), ["ui", "alpha", "zeta", "broken"]);
});

test("home blocks: each present module's blocks that ask for home, the shell's first", () => {
  const block = (name, home = true, place = "block") => ({ name, title: name, doc: "", place, home, contract: 1 });
  const blocks = homeBlocks([
    mod("fixture", { components: [block("page", false, "page"), block("today"), block("one", true, "inline")] }),
    mod("ui", { components: [block("page", false, "page"), block("servers")] }),
    mod("gone", { state: "broken", components: [block("x")] }),
  ]);
  assert.deepEqual(blocks.map((item) => `${item.module}.${item.name}`), ["ui.servers", "fixture.today"]);
});

test("a module's lines name a failed export and each refused component with its reason", () => {
  const lines = moduleLines(mod("fixture", {
    export_error: "export exited 1", components: [{ name: "today", title: "Today" }],
    refused: [{ name: "chip", reason: "it is written for contract 2, and this shell places contract 1; upgrade moxi-ui" }],
  }));
  assert.deepEqual(lines.map((line) => [line.tone, line.text]), [
    ["fail", "Its page can't be served: export exited 1"],
    [null, "Components: Today"],
    ["warn", "chip is not shown: it is written for contract 2, and this shell places contract 1; upgrade moxi-ui"],
  ]);
});

test("doctor rows: the worst first, an unknown state as warn", () => {
  const rows = doctorRows([
    { module: "ui", name: "shell", state: "ok", detail: "running", fix: null },
    { module: "ui", name: "page:fixture", state: "fail", detail: "the last three starts failed", fix: null },
    { module: "ui", name: "x", state: "odd", detail: "", fix: null },
  ]);
  assert.deepEqual(rows.map((item) => [item.name, item.tone]), [["page:fixture", "fail"], ["x", "warn"], ["shell", "ok"]]);
});

test("the error panel for a start that failed names the exit and the log", () => {
  const panel = failurePanel("fixture", 502, { error: "start", module: "fixture", exit: 3, log: "/state/ui/log/shell.log" });
  assert.equal(panel.title, "fixture's page server did not start");
  assert.deepEqual(panel.lines, ["It exited with code 3.", "Its log: /state/ui/log/shell.log",
                                 "The next try is at the first request after 10 s."]);
  const silent = failurePanel("fixture", 502, { error: "start", module: "fixture", exit: null, log: "/l" });
  assert.equal(silent.lines[0], "It gave no ready line within 10 s and was stopped.");
  assert.equal(failurePanel("fixture", 504, { error: "timeout", module: "fixture" }).title,
               "fixture's page server is not answering");
  assert.equal(failurePanel("fixture", 0, null).title, "No answer from the shell");
  assert.equal(failurePanel("fixture", 404, null), null);
});
