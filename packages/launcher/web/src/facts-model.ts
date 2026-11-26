// What the launcher's `project-facts` component reports to the Projects page, as plain
// data, so the Node tests check it directly. projects' README, "Facts on the Projects
// page", is the contract it fills.
//
// The launcher's judgment that a launch runs is that `/api/running` lists it; nothing
// here decides it from a launch's fields. Each route feeds its own facts, and a route
// whose last read failed, or answered a `state` other than `up`, drops exactly those:
//
// - `/api/running`: each project's `at`, its `N running`, the opened project's `agents`,
//   its `Sessions` lines and its running `stays` line, and the report's `activity`;
// - `/api/presets` and `/api/catalog`: the `Launch` line's preset clause, which needs
//   both, since a preset shown without the values of it that can do harm would read safer
//   than it is;
// - `/api/last`: that line's `last launched` clause;
// - `/api/name`: the `Launch` row's second line.
//
// The words are Home's for the same facts: `runWords` for where a launch runs,
// `runsWhere` and `clock` for a running session's line, `presetSets` for what a preset
// sets and `lastWhen` for when the last launch was made.
//
// Every string here goes into the page as text.

import type { Catalog, Preset, Presets } from "./panel.ts";
import { fieldsFor } from "./panel.ts";
import type { PanelField, Word } from "./options-model.ts";
import { projectPreset, presetSets } from "./preset-model.ts";
import type { Last } from "./fill-model.ts";
import { lastWhen } from "./fill-model.ts";
import type { Named } from "./name-model.ts";
import { DAY, HOUR, runWords } from "./prompt-model.ts";
import type { Clock } from "./prompt-model.ts";
import { clock, runsWhere } from "./running-model.ts";
import type { Running, RunningEntry, Where } from "./running-model.ts";

/** The event a facts component dispatches, with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version. */
export const VERSION = 1;
/** Where the launcher's parts, lines and sections sit among other modules'. */
export const ORDER = 10;
/** Its `at` covers only what runs now. */
export const ACTIVITY = "window";
/** The longest text the contract takes. */
export const TEXT_MAX_CHARS = 200;
/** The most lines a section may have. */
export const LINES_MAX = 6;

/** The opened project's rows. */
export const LAUNCH = "Launch";
export const SESSIONS = "Sessions";
/** What an agent with a running launch is, in `agents`. */
export const RUNNING_NOW = "running now";
/** The `Launch` line's clause for a project with no preset of its own. */
export const NO_PRESET = "no preset";
/** The words of the count of running launches: `2 running`. */
export const RUNNING_COUNT = { one: "running", many: "running" } as const;

/** A project as the Projects page gives it, in the `projects` attribute. */
export interface Given {
  key: string;
  root: string;
  lead: string | null;
  associated: string[];
}

export interface Part {
  text: string;
  claims?: string[];
  count?: { one: string; many: string };
  /** A part that holds only while the line's session is not in a turn: a running line's
   * `last active`. The page may leave it out when another module says the session is in
   * one. */
  idle_only?: true;
}

export interface Line {
  parts: Part[];
  claims?: string[];
  /** The launcher's word that the line's session runs now: on each running launch's line,
   * never on the `<n> more` line. */
  runs?: true;
}

export interface Section {
  label: string;
  lines: Line[];
}

/** What the launcher says of an agent: only that it runs, and where when it knows. */
export interface Say {
  runs: true;
  state: string;
  where?: string;
}

export interface ProjectFacts {
  at?: number;
  parts?: Part[];
  sections?: Section[];
  agents?: Record<string, Say>;
  stays?: string[];
}

export interface Report {
  version: number;
  order: number;
  activity?: typeof ACTIVITY;
  projects: Record<string, ProjectFacts>;
}

/**
 * The last good value of each route, null where the last read failed, was refused or
 * answered a `state` other than `up`, or where there was none. `last` and `name` are the
 * reads for the opened project only.
 */
export interface Reads {
  running: Running | null;
  presets: Presets | null;
  catalog: Catalog | null;
  last: Last | null;
  name: Named | null;
}

/** What the times are worded against: now, and how the browser writes an hour and a day. */
export interface When {
  now: Date;
  hour?: Clock;
  day?: Clock;
}

// -- the given projects -------------------------------------------------------------------

const isObj = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The `projects` attribute's projects; those that aren't one are left out, and a value
 * that isn't a JSON list gives none. */
export function parseGiven(text: string | null): Given[] {
  let list: unknown;
  try {
    list = JSON.parse(text ?? "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(list)) return [];
  const given: Given[] = [];
  for (const item of list) {
    if (!isObj(item) || typeof item.key !== "string" || typeof item.root !== "string") continue;
    const associated = Array.isArray(item.associated)
      ? item.associated.filter((name): name is string => typeof name === "string") : [];
    given.push({ key: item.key, root: item.root,
                 lead: typeof item.lead === "string" ? item.lead : null, associated });
  }
  return given;
}

// -- each route's reading -----------------------------------------------------------------

/** A route's value when the module says it is up, else null: its judgment can't be read. */
export function upOnly<V extends { state: string }>(value: V | null): V | null {
  return value !== null && value.state === "up" ? value : null;
}

/** `/api/name`'s value when the read gave one, else null. */
export function nameUp(value: Named | null): Named | null {
  return value !== null && value.failed === "" ? value : null;
}

// -- the words ----------------------------------------------------------------------------

/** `text` cut to the contract's longest, ending `…` when cut. */
export function clip(text: string): string {
  const chars = Array.from(text);
  return chars.length <= TEXT_MAX_CHARS ? text : `${chars.slice(0, TEXT_MAX_CHARS - 1).join("")}…`;
}

/** An ISO time as epoch seconds; null when it doesn't parse. */
export function epochOf(iso: string | null): number | null {
  if (iso === null || iso === "") return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

/**
 * The session a running launch names, as the contract spells a claim: `<harness>/<session
 * id>`, the harness as Moxi names it and the id the harness wrote. A launch whose session
 * id isn't known yet claims itself, `request/<request id>`, which matches no other
 * module's claim.
 */
export function claimOf(entry: RunningEntry): string {
  return entry.session_id !== null && entry.session_id !== "" && entry.harness !== ""
    ? `${entry.harness}/${entry.session_id}` : `request/${entry.request_id}`;
}

/** A slot's number as a preset or a running launch spells it; null when it is none. */
function slotNumber(text: string | null): number | null {
  return text !== null && /^\d+$/.test(text) ? Number(text) : null;
}

/** Where a running launch runs, in `agents`: `in slot 4`, `in your terminal`, `headless`;
 * null for a slot with no number or a target the launcher doesn't name. */
export function runningWhere(where: Where): string | null {
  if (where.target === "terminal" || where.target === "headless") return runWords({ target: where.target });
  if (where.target !== "slot") return null;
  const slot = slotNumber(where.slot);
  return slot === null ? null : runWords({ target: "slot", slot, named: true });
}

/** One value a preset sets, as text; null when it sets none. */
function presetValue(preset: Preset, name: string): string | null {
  const found = preset.values.find((row) => row.name === name);
  return found !== undefined && typeof found.value === "string" && found.value !== "" ? found.value : null;
}

/** Where a launch into a slot that names none goes: the launcher takes the lowest slot
 * free then, or makes a new one when none is. The page reads no slot, so it states the
 * rule rather than which case holds now. */
export const ANY_SLOT = "in the lowest free slot, or a new one when none is free";

/** Where a preset puts the launch: `in slot 3`, `in your terminal`, `headless`, or
 * `ANY_SLOT` for a slot it names no number for; null when it sets no target, or a slot
 * that is no number. */
export function presetWhere(preset: Preset): string | null {
  const target = presetValue(preset, "target");
  if (target === "terminal" || target === "headless") return runWords({ target });
  if (target !== "slot") return null;
  const said = presetValue(preset, "slot");
  if (said === null) return ANY_SLOT;
  const slot = slotNumber(said);
  return slot === null ? null : runWords({ target: "slot", slot, named: true });
}

/** What a preset sets, as Home's menu row lists it: its harness, model and effort, then
 * each value that can do harm. A preset that names no harness is checked against every
 * harness the catalog has, so no value that can do harm is left out. */
export function presetWords(preset: Preset, catalog: Catalog): Word[] {
  const harnesses = preset.harness !== null ? [preset.harness] : catalog.harnesses.map((item) => item.name);
  const words: Word[] = [];
  for (const harness of harnesses.length ? harnesses : [""]) {
    for (const word of presetSets(preset, fieldsFor(catalog, harness) as PanelField[])) {
      if (!words.some((seen) => seen.text === word.text && seen.danger === word.danger)) words.push(word);
    }
  }
  return words;
}

/** The `Launch` line's preset clause: `preset claude · sonnet · medium, in your terminal`,
 * or `no preset`. */
export function presetClause(presets: Presets, catalog: Catalog, key: string): string {
  const preset = projectPreset(presets.presets, key);
  if (preset === null) return NO_PRESET;
  const sets = presetWords(preset, catalog).map((word) => word.text).join(" · ");
  const where = presetWhere(preset);
  const head = sets === "" ? "preset" : `preset ${sets}`;
  return clip(where === null ? head : `${head}, ${where}`);
}

/** The `Launch` line's last-launch clause, `last launched today 18:45`; null with no launch
 * there, or a time that doesn't parse. */
export function lastClause(last: Last, when: When): string | null {
  if (last.at === null) return null;
  const said = lastWhen(last.at, when.now, when.hour ?? HOUR, when.day ?? DAY);
  return said === "" ? null : `last launched ${said}`;
}

/** The `Launch` row's second line: `names sessions like <name>, its own template` for a
 * project's own template, else `names sessions with the template for every project`;
 * null when a project's template gave no name. */
export function nameLine(name: Named): string | null {
  if (name.from !== "project") return "names sessions with the template for every project";
  return name.name === null || name.name === "" ? null : clip(`names sessions like ${name.name}, its own template`);
}

/** The `Launch` row, or null when neither of its lines has a part. */
export function launchSection(reads: Reads, key: string, when: When): Section | null {
  const one: Part[] = [];
  if (reads.presets !== null && reads.catalog !== null) one.push({ text: presetClause(reads.presets, reads.catalog, key) });
  const last = reads.last === null ? null : lastClause(reads.last, when);
  if (last !== null) one.push({ text: last });
  const lines: Line[] = [];
  if (one.length) lines.push({ parts: one });
  const two = reads.name === null ? null : nameLine(reads.name);
  if (two !== null) lines.push({ parts: [{ text: two }] });
  return lines.length ? { label: LAUNCH, lines } : null;
}

/** One running launch's line in the `Sessions` row: its name, how it runs, and when its
 * session was last active, each left out when it is none. It claims its session and says
 * it runs (`runs`); its `last active` part holds only while the session is not in a turn
 * (`idle_only`), so the page can join another module's word for a turn in its place. */
export function sessionLine(entry: RunningEntry): Line {
  const parts: Part[] = [{ text: clip(entry.name || entry.request_id) }];
  const where = runsWhere(entry.where);
  if (where !== "") parts.push({ text: clip(where) });
  const at = entry.activity === null || epochOf(entry.activity.at) === null ? "" : clock(entry.activity.at);
  if (at !== "") parts.push({ text: `last active ${at}`, idle_only: true });
  return { parts, claims: [claimOf(entry)], runs: true };
}

/** The `Sessions` row: a line per running launch, newest first; past the contract's cap,
 * the last line says how many more and claims their sessions. */
export function sessionsSection(entries: readonly RunningEntry[]): Section | null {
  if (!entries.length) return null;
  if (entries.length <= LINES_MAX) return { label: SESSIONS, lines: entries.map(sessionLine) };
  const shown = entries.slice(0, LINES_MAX - 1);
  const rest = entries.slice(LINES_MAX - 1);
  return { label: SESSIONS, lines: [...shown.map(sessionLine),
                                    { parts: [{ text: `${rest.length} more` }], claims: rest.map(claimOf) }] };
}

/** Remove's `Stays` line while launches run there. */
export function staysLine(count: number): string {
  return count === 1 ? "the one running now keeps running" : `the ${count} running now keep running`;
}

/** The opened project's agents that run now, by name: the newest running launch under each
 * of its names. The launcher knows only its own launches, so it says nothing of a name
 * with none. */
export function agentsOf(entries: readonly RunningEntry[], project: Given): Record<string, Say> {
  const names = new Set([...(project.lead === null ? [] : [project.lead]), ...project.associated]);
  const agents: Record<string, Say> = {};
  for (const entry of entries) {
    const name = entry.identity;
    if (name === null || !names.has(name) || Object.hasOwn(agents, name)) continue;
    const where = runningWhere(entry.where);
    agents[name] = where === null ? { runs: true, state: RUNNING_NOW } : { runs: true, state: RUNNING_NOW, where };
  }
  return agents;
}

// -- the report ---------------------------------------------------------------------------

/**
 * What the component reports for `reads`, with `given` the Projects page's projects and
 * `open` the opened key: null when every route failed, or `/api/running` failed with no
 * project opened. Each given project with a launch running gets its `at` and its count,
 * and the opened one its `agents`, its `Launch` and `Sessions` rows and its `stays` line,
 * each from the routes that feed it.
 */
export function factsReport(reads: Reads, given: readonly Given[], open: string | null, when: When): Report | null {
  const running = reads.running;
  const opened = open === null ? undefined : given.find((project) => project.key === open);
  if (running === null && opened === undefined) return null;
  if (running === null && reads.presets === null && reads.catalog === null && reads.last === null
      && reads.name === null) return null;
  const keys = new Set(given.map((project) => project.key));
  const by = new Map<string, RunningEntry[]>();
  for (const entry of running?.sessions ?? []) {
    const key = entry.where.project_key;
    if (key === null || !keys.has(key)) continue;
    const list = by.get(key) ?? [];
    list.push(entry);
    by.set(key, list);
  }
  const projects: Record<string, ProjectFacts> = {};
  for (const [key, list] of by) {
    const facts: ProjectFacts = {};
    const times = list.map((entry) => epochOf(entry.activity?.at ?? null)).filter((at): at is number => at !== null);
    if (times.length) facts.at = Math.max(...times);
    facts.parts = [{ text: "", count: { ...RUNNING_COUNT }, claims: list.map(claimOf) }];
    projects[key] = facts;
  }
  if (opened !== undefined) {
    const facts = projects[opened.key] ?? {};
    const list = by.get(opened.key) ?? [];
    const sections: Section[] = [];
    const launch = launchSection(reads, opened.key, when);
    if (launch !== null) sections.push(launch);
    const sessions = sessionsSection(list);
    if (sessions !== null) sections.push(sessions);
    if (sections.length) facts.sections = sections;
    const agents = agentsOf(list, opened);
    if (Object.keys(agents).length) facts.agents = agents;
    if (list.length) facts.stays = [staysLine(list.length)];
    projects[opened.key] = facts;
  }
  const report: Report = { version: VERSION, order: ORDER, projects };
  if (running !== null) report.activity = ACTIVITY;
  return report;
}
