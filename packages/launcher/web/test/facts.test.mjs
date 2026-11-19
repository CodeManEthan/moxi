import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTIVITY, agentsOf, claimOf, clip, epochOf, factsReport, lastClause, LINES_MAX, nameLine, nameUp, NO_PRESET, ORDER,
  parseGiven, presetClause, presetWhere, runningWhere, sessionLine, sessionsSection, staysLine, TEXT_MAX_CHARS, upOnly,
  VERSION,
} from "../src/facts-model.ts";
import { parseLast } from "../src/fill-model.ts";
import { parseName } from "../src/name-model.ts";
import { parseCatalog, parsePresets } from "../src/panel.ts";
import { parseRunning } from "../src/running-model.ts";

const pad = (n) => String(n).padStart(2, "0");
/** The tests' clocks: the hour and the day in this zone, written the same in every locale. */
const HOUR = (at) => `${pad(at.getHours())}:${pad(at.getMinutes())}`;
const DAY = (at) => `${at.getMonth() + 1}/${at.getDate()}`;
const NOW = new Date(2026, 10, 19, 20, 0);
const WHEN = { now: NOW, hour: HOUR, day: DAY };
/** A time of day as ui's `clock` writes it, for a session's `last active`. */
const hhmm = (iso) => new Date(Date.parse(iso)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const local = (h, m, dayOff = 0) => new Date(2026, 10, 19 + dayOff, h, m).toISOString();

const SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f";
const ROLLOUT = "019a2b3c-4d5e-7f60-8192-a3b4c5d6e7f8";
const GIVEN = [
  { key: "atlas-api", root: "/home/me/code/atlas-api", lead: "builder-1", associated: ["planner", "reviewer"] },
  { key: "kestrel-web", root: "/home/me/code/kestrel-web", lead: "builder-2", associated: ["reviewer"] },
];

/** One entry as `/api/running` sends it, in `project`. */
const entry = (request, project, more = {}) => ({
  request_id: request, harness: "claude", session_id: `${request}-sid`, name: `${request}-name`, identity: null,
  where: { target: "slot", slot: "3", project_key: project, folder: project ?? "loose" },
  started_at: local(18, 0), cache: null, waits: null, covered: false, coverable: null,
  activity: { at: local(18, 52), by: "agent", text: null }, ...more,
});
const running = (sessions, state = "up") =>
  parseRunning({ state, detail: state === "up" ? null : "x", count: sessions.length, sessions });

const CATALOG = parseCatalog({ state: "up", detail: null, harnesses: [
  { name: "claude", fields: [
    { name: "harness", type: "choice" }, { name: "model", type: "string" }, { name: "effort", type: "choice" },
    { name: "permission_mode", type: "choice", danger: ["bypassPermissions"] },
    { name: "target", type: "choice" }, { name: "slot", type: "string" },
  ] },
  { name: "codex", fields: [
    { name: "harness", type: "choice" }, { name: "model", type: "string" },
    { name: "sandbox", type: "choice", danger: ["danger-full-access"] },
  ] },
] });

const preset = (name, project, values = [], harness = "claude") => ({
  kind: "project", name, harness, values: [{ name: "project", value: project, items: null },
                                           ...values.map(([key, value]) => ({ name: key, value, items: null }))],
});
const KESTREL = preset("kestrel-web", "kestrel-web", [["model", "sonnet"], ["effort", "medium"], ["target", "terminal"]]);
const presets = (list, state = "up") =>
  parsePresets({ state, detail: state === "up" ? null : "x", presets: list, problems: [] });

const last = (at, state = "up") =>
  parseLast({ state, detail: state === "up" ? null : "x", at, harness: at === null ? null : "claude", values: [] });
const named = (data) => parseName({ state: "up", detail: null, template: "<project>-<model>-<date>", refused: null,
                                    save: null, ...data }, "the launcher's page server answered 500");
const OWN = named({ name: "kestrel-web-sonnet-Nov-19 20:00", from: "project", project: "kestrel-web" });
const GLOBAL = named({ name: "kestrel-web-opus-Nov-19 20:00", from: "global", project: null });

/** Every read good: two running in kestrel-web (one the lead's in slot 4), one in atlas-api. */
const reads = (over = {}) => ({
  running: running([
    entry("r1", "kestrel-web", { identity: "builder-2", where: { target: "slot", slot: "4", project_key: "kestrel-web",
                                                                 folder: "kestrel-web" } }),
    entry("r2", "kestrel-web", { where: { target: "headless", slot: null, project_key: "kestrel-web",
                                          folder: "kestrel-web" }, activity: null }),
    entry("r3", "atlas-api", { activity: { at: local(19, 9), by: "you", text: null } }),
    entry("r4", null),
  ]),
  presets: presets([KESTREL]), catalog: CATALOG, last: last(local(18, 45)), name: OWN, ...over,
});
const report = (over = {}, open = "kestrel-web") => factsReport(reads(over), GIVEN, open, WHEN);
const kestrel = (over = {}, open = "kestrel-web") => report(over, open).projects["kestrel-web"];
const texts = (line) => line.parts.map((part) => part.text);
const section = (facts, label) => facts.sections?.find((found) => found.label === label);

// -- the report's frame ---------------------------------------------------------------------

test("a good read reports version 1, order 10, activity window, and only given projects with a launch", () => {
  const found = report({}, null);
  assert.equal(found.version, VERSION);
  assert.equal(found.order, ORDER);
  assert.equal(found.order, 10);
  assert.equal(found.activity, ACTIVITY);
  assert.equal(found.activity, "window");
  assert.deepEqual(Object.keys(found.projects).sort(), ["atlas-api", "kestrel-web"]);
  assert.equal(found.gathers, undefined);
});

test("closed, a project gets its newest activity as `at` and one counted part claiming its running launches", () => {
  const found = report({}, null);
  assert.deepEqual(found.projects["kestrel-web"], {
    at: Date.parse(local(18, 52)) / 1000,
    parts: [{ text: "", count: { one: "running", many: "running" }, claims: ["claude/r1-sid", "claude/r2-sid"] }],
  });
  assert.equal(found.projects["atlas-api"].at, Date.parse(local(19, 9)) / 1000);
});

test("a project whose running launches have no time that parses gets no `at`, and still its count", () => {
  const list = [entry("a", "atlas-api", { activity: { at: "not a time", by: null, text: null } }),
                entry("b", "atlas-api", { activity: null })];
  const found = factsReport(reads({ running: running(list) }), GIVEN, null, WHEN).projects["atlas-api"];
  assert.equal(found.at, undefined);
  assert.equal(found.parts[0].claims.length, 2);
});

test("nothing running and nothing opened is still a report: launcher can say nothing runs", () => {
  const found = factsReport(reads({ running: running([]) }), GIVEN, null, WHEN);
  assert.deepEqual(found, { version: 1, order: 10, activity: "window", projects: {} });
});

// -- claims ---------------------------------------------------------------------------------

test("a claim names the session as `<harness>/<session id>` for each harness", () => {
  assert.equal(claimOf(entry("a", "x", { harness: "claude", session_id: SID })), `claude/${SID}`);
  assert.equal(claimOf(entry("a", "x", { harness: "codex", session_id: ROLLOUT })), `codex/${ROLLOUT}`);
});

test("a launch with no session id yet claims itself by its request", () => {
  assert.equal(claimOf(entry("req-1", "x", { session_id: null })), "request/req-1");
  assert.equal(claimOf(entry("req-2", "x", { session_id: "" })), "request/req-2");
  assert.equal(claimOf(entry("req-3", "x", { harness: "" })), "request/req-3");
});

// -- times ----------------------------------------------------------------------------------

test("a time that doesn't parse is absent", () => {
  assert.equal(epochOf("2026-11-19T18:52:00Z"), Date.parse("2026-11-19T18:52:00Z") / 1000);
  for (const bad of [null, "", "yesterday", "2026-13-45T99:99:99Z"]) assert.equal(epochOf(bad), null, bad);
  const line = sessionLine(entry("a", "x", { activity: { at: "garbage", by: null, text: null } }));
  assert.deepEqual(texts(line), ["a-name", "slot 3"]);
});

test("the last launch's clause says today's time, else the day and the time; none for no launch or a bad time", () => {
  assert.equal(lastClause(last(local(18, 45)), WHEN), "last launched today 18:45");
  assert.equal(lastClause(last(local(9, 5, -1)), WHEN), "last launched 11/18 09:05");
  assert.equal(lastClause(last(null), WHEN), null);
  assert.equal(lastClause(last("not a time"), WHEN), null);
});

// -- the where-words ------------------------------------------------------------------------

test("where a preset puts the launch, in Home's words", () => {
  const where = (values) => presetWhere(preset("p", "x", values));
  assert.equal(where([["target", "slot"], ["slot", "3"]]), "in slot 3");
  assert.equal(where([["target", "slot"]]), "in a new slot");
  assert.equal(where([["target", "terminal"]]), "in your terminal");
  assert.equal(where([["target", "headless"]]), "headless");
  assert.equal(where([]), null);
  assert.equal(where([["target", "slot"], ["slot", "three"]]), null);
});

test("where a running launch runs, in `agents`", () => {
  const where = (target, slot) => runningWhere({ target, slot, project_key: null, folder: "" });
  assert.equal(where("slot", "4"), "in slot 4");
  assert.equal(where("terminal", null), "in your terminal");
  assert.equal(where("headless", null), "headless");
  assert.equal(where("slot", null), null);
  assert.equal(where("tmux", null), null);
});

test("a running line says how it runs as its Home row does: `slot 3`, `slot`, `headless`, `terminal`", () => {
  const line = (target, slot) => texts(sessionLine(entry("a", "x", { where: { target, slot, project_key: "x", folder: "x" },
                                                                     activity: null })));
  assert.deepEqual(line("slot", "3"), ["a-name", "slot 3"]);
  assert.deepEqual(line("slot", null), ["a-name", "slot"]);
  assert.deepEqual(line("headless", null), ["a-name", "headless"]);
  assert.deepEqual(line("terminal", null), ["a-name", "terminal"]);
});

// -- the Launch row ---------------------------------------------------------------------------

test("the Launch row: the preset in words with where, the last launch, and the project's own template", () => {
  const launch = section(kestrel(), "Launch");
  assert.deepEqual(launch.lines.map(texts), [
    ["preset claude · sonnet · medium, in your terminal", "last launched today 18:45"],
    ["names sessions like kestrel-web-sonnet-Nov-19 20:00, its own template"],
  ]);
  assert.equal(launch.link, undefined);
});

test("a preset's values that can do harm follow its harness, model and effort", () => {
  const risky = preset("atlas-api", "atlas-api", [["model", "opus"], ["permission_mode", "bypassPermissions"],
                                                  ["target", "slot"], ["slot", "2"]]);
  assert.equal(presetClause(presets([risky]), CATALOG, "atlas-api"),
               "preset claude · opus · bypassPermissions, in slot 2");
  const safe = preset("atlas-api", "atlas-api", [["permission_mode", "default"]]);
  assert.equal(presetClause(presets([safe]), CATALOG, "atlas-api"), "preset claude");
});

test("a value that can do harm only for another harness is not marked; a preset with no harness is read against each", () => {
  const codexOnly = preset("atlas-api", "atlas-api", [["sandbox", "danger-full-access"]]);
  assert.equal(presetClause(presets([codexOnly]), CATALOG, "atlas-api"), "preset claude");
  const none = { ...codexOnly, harness: null };
  assert.equal(presetClause(presets([none]), CATALOG, "atlas-api"), "preset danger-full-access");
});

test("a project with no preset of its own says `no preset`; the preset is Home's pick for that project", () => {
  assert.equal(presetClause(presets([]), CATALOG, "kestrel-web"), NO_PRESET);
  const other = preset("b-other", "kestrel-web", [["model", "haiku"]]);
  assert.equal(presetClause(presets([other, KESTREL]), CATALOG, "kestrel-web"),
               "preset claude · sonnet · medium, in your terminal");
  assert.equal(presetClause(presets([other]), CATALOG, "kestrel-web"), "preset claude · haiku");
});

test("the name line: its own template with the name, else the template for every project", () => {
  assert.equal(nameLine(OWN), "names sessions like kestrel-web-sonnet-Nov-19 20:00, its own template");
  assert.equal(nameLine(GLOBAL), "names sessions with the template for every project");
  assert.equal(nameLine(named({ name: null, from: "project", project: "kestrel-web" })), null);
  assert.equal(nameLine(named({ name: "x".repeat(300), from: "project" })).length, TEXT_MAX_CHARS);
});

// -- the Sessions row, agents and stays ---------------------------------------------------------

test("the Sessions row: a line per running launch, newest first, each claiming its session, no link", () => {
  const rows = section(kestrel(), "Sessions");
  assert.deepEqual(rows.lines.map(texts), [
    ["r1-name", "slot 4", `last active ${hhmm(local(18, 52))}`],
    ["r2-name", "headless"],
  ]);
  assert.deepEqual(rows.lines.map((line) => line.claims), [["claude/r1-sid"], ["claude/r2-sid"]]);
  assert.equal(rows.link, undefined);
});

test("past six running launches the last line says how many more and claims them", () => {
  const list = Array.from({ length: 9 }, (_, n) => parseRunning({ state: "up", detail: null, count: 1,
                                                                    sessions: [entry(`s${n}`, "x")] }).sessions[0]);
  const rows = sessionsSection(list);
  assert.equal(rows.lines.length, LINES_MAX);
  assert.deepEqual(texts(rows.lines[5]), ["4 more"]);
  assert.deepEqual(rows.lines[5].claims, ["claude/s5-sid", "claude/s6-sid", "claude/s7-sid", "claude/s8-sid"]);
  assert.equal(sessionsSection([]), null);
});

test("agents: a running launch under the lead's or an associated name runs now, where it runs; none else", () => {
  assert.deepEqual(kestrel().agents, { "builder-2": { runs: true, state: "running now", where: "in slot 4" } });
  const list = running([
    entry("new", "atlas-api", { identity: "planner", where: { target: "headless", slot: null, project_key: "atlas-api",
                                                              folder: "a" } }),
    entry("old", "atlas-api", { identity: "planner" }),
    entry("odd", "atlas-api", { identity: "builder-1", where: { target: "slot", slot: null, project_key: "atlas-api",
                                                                folder: "a" } }),
    entry("stranger", "atlas-api", { identity: "someone-else" }),
  ]).sessions;
  assert.deepEqual(agentsOf(list, GIVEN[0]), {
    planner: { runs: true, state: "running now", where: "headless" },
    "builder-1": { runs: true, state: "running now" },
  });
  assert.ok(Object.values(agentsOf(list, GIVEN[0])).every((say) => say.runs === true));
});

test("stays: the launches running there keep running", () => {
  assert.equal(staysLine(1), "the one running now keeps running");
  assert.equal(staysLine(3), "the 3 running now keep running");
  assert.deepEqual(kestrel().stays, ["the 2 running now keep running"]);
  assert.equal(kestrel({}, null).stays, undefined);
});

test("sections, agents and stays are the opened project's only", () => {
  const found = report();
  assert.equal(found.projects["atlas-api"].sections, undefined);
  assert.equal(found.projects["atlas-api"].agents, undefined);
  assert.deepEqual(found.projects["kestrel-web"].sections.map((row) => row.label), ["Launch", "Sessions"]);
});

test("an opened project with nothing running gets its Launch row and nothing of running", () => {
  const found = factsReport(reads({ running: running([]) }), GIVEN, "atlas-api", WHEN).projects["atlas-api"];
  assert.deepEqual(Object.keys(found), ["sections"]);
  assert.deepEqual(found.sections.map((row) => row.label), ["Launch"]);
});

// -- each route failing in turn ----------------------------------------------------------------

test("`/api/running` failing drops `at`, the count, agents, the Sessions lines, stays and activity", () => {
  const found = report({ running: null });
  assert.equal(found.activity, undefined);
  assert.deepEqual(Object.keys(found.projects), ["kestrel-web"]);
  const facts = found.projects["kestrel-web"];
  assert.deepEqual(Object.keys(facts), ["sections"]);
  assert.deepEqual(facts.sections.map((row) => row.label), ["Launch"]);
  assert.equal(section(facts, "Launch").lines.length, 2);
});

test("`/api/running` failing with nothing opened reports null", () => {
  assert.equal(report({ running: null }, null), null);
});

test("`/api/presets` failing drops the preset clause, and only it", () => {
  const facts = kestrel({ presets: null });
  assert.deepEqual(section(facts, "Launch").lines.map(texts), [
    ["last launched today 18:45"], ["names sessions like kestrel-web-sonnet-Nov-19 20:00, its own template"]]);
  assert.deepEqual(facts, { ...kestrel(), sections: facts.sections });
  assert.deepEqual(section(facts, "Sessions"), section(kestrel(), "Sessions"));
});

test("`/api/catalog` failing drops the preset clause too", () => {
  assert.deepEqual(section(kestrel({ catalog: null }), "Launch"), section(kestrel({ presets: null }), "Launch"));
});

test("`/api/last` failing drops the last-launch clause, and only it", () => {
  assert.deepEqual(section(kestrel({ last: null }), "Launch").lines.map(texts), [
    ["preset claude · sonnet · medium, in your terminal"],
    ["names sessions like kestrel-web-sonnet-Nov-19 20:00, its own template"]]);
  assert.deepEqual(section(kestrel({ last: null }), "Sessions"), section(kestrel(), "Sessions"));
});

test("`/api/name` failing drops the Launch row's second line, and only it", () => {
  assert.deepEqual(section(kestrel({ name: null }), "Launch").lines.map(texts), [
    ["preset claude · sonnet · medium, in your terminal", "last launched today 18:45"]]);
  assert.deepEqual(kestrel({ name: null }).agents, kestrel().agents);
});

test("the Launch row is absent with neither of its lines", () => {
  const facts = kestrel({ presets: null, last: null, name: null });
  assert.deepEqual(facts.sections.map((row) => row.label), ["Sessions"]);
  const nothing = factsReport(reads({ running: running([]), presets: null, last: null, name: null }), GIVEN,
                              "kestrel-web", WHEN);
  assert.deepEqual(nothing.projects, { "kestrel-web": {} });
});

test("every route failing reports null", () => {
  assert.equal(report({ running: null, presets: null, catalog: null, last: null, name: null }), null);
});

// -- the module's judgment ----------------------------------------------------------------------

test("an answer whose `state` isn't `up` counts as a failed read", () => {
  assert.equal(upOnly(running([], "error")), null);
  assert.equal(upOnly(presets([], "error")), null);
  assert.equal(upOnly(last(null, "error")), null);
  assert.equal(upOnly(parseCatalog({ state: "error", detail: "x" })), null);
  assert.ok(upOnly(running([])) !== null);
  assert.equal(nameUp(parseName({ state: "error", detail: "x" }, "500")), null);
  assert.equal(nameUp(parseName(null, "the launcher's page server answered 500")), null);
  assert.equal(nameUp(OWN), OWN);
});

test("a launch outside every given project, or in none, counts nowhere", () => {
  const found = factsReport(reads({ running: running([entry("a", "elsewhere"), entry("b", null)]) }), GIVEN, null, WHEN);
  assert.deepEqual(found.projects, {});
});

test("an opened key the page didn't give is not reported", () => {
  const found = report({}, "not-given");
  assert.deepEqual(Object.keys(found.projects).sort(), ["atlas-api", "kestrel-web"]);
  assert.equal(found.projects["kestrel-web"].sections, undefined);
});

// -- the given projects ------------------------------------------------------------------------

test("the `projects` attribute: projects that aren't one are left out, and a value that isn't a list gives none", () => {
  assert.deepEqual(parseGiven(JSON.stringify([...GIVEN, { key: 3 }, "x", { key: "k", root: "/r", lead: 7 }])),
                   [...GIVEN, { key: "k", root: "/r", lead: null, associated: [] }]);
  assert.deepEqual(parseGiven("{"), []);
  assert.deepEqual(parseGiven(null), []);
  assert.deepEqual(parseGiven("{}"), []);
});

test("text is cut to the contract's longest", () => {
  assert.equal(clip("short"), "short");
  const cut = clip("é".repeat(250));
  assert.equal(Array.from(cut).length, TEXT_MAX_CHARS);
  assert.ok(cut.endsWith("…"));
  const line = sessionLine(entry("a", "x", { name: "n".repeat(260), activity: null }));
  assert.equal(line.parts[0].text.length, TEXT_MAX_CHARS);
});
