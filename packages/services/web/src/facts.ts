// What services' `project-facts` component reports to the Projects page, as plain data:
// each project's failed services and the two warnings on its row's second line, and for
// the opened project its `Services` row. No DOM and no Lit here, so the Node tests check it
// directly. projects' README, "Facts on the Projects page", is the contract it fills.
//
// It reports services' own judgment, each row's verdict, by the table in `partOf` and
// `lineOf`: never whether a row is down or stopped from its raw fields. A raw field is
// read only for a time (`since`, `last_run.at`) or a word (`schedule`).

import type { Row, Rows } from "./feed.ts";

/** The event a facts component dispatches, with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version. */
export const VERSION = 1;
/** Where services' parts, lines and section sit among other modules'. */
export const ORDER = 60;
/** What services gathers under a project, for the Projects page's empty sentence. */
export const GATHERS = "services";
/** The opened project's row, and its link's words. */
export const LABEL = "Services";
/** Home's `needs` rank for something broken: a failed service. */
export const BROKEN = 30;
/** The longest text the contract takes. */
export const TEXT_MAX_CHARS = 200;
/** The most parts one row's second line, or one line, may have. */
export const PARTS_MAX = 8;
/** The most lines a section may have; the failed and warned rows take at most one less,
 * so the quiet last line always has its place. */
export const LINES_MAX = 6;

/** A project as the Projects page gives it, in the `projects` attribute. */
export interface Given {
  key: string;
  root: string;
  lead: string | null;
  associated: string[];
}

export interface Part {
  text: string;
  heavy?: boolean;
  warn?: boolean;
  rank?: number;
}

export interface Line {
  parts: Part[];
}

export interface Section {
  label: string;
  rank?: number;
  lines: Line[];
  link: { text: string; module: string; route: string };
}

export interface ProjectFacts {
  parts?: Part[];
  sections?: Section[];
}

export interface Report {
  version: number;
  order: number;
  gathers: string;
  projects: Record<string, ProjectFacts>;
}

/** How the component reads a moment (epoch seconds): its hour and minute as ui shows
 * one, and its day, `YYYY-MM-DD`. */
export interface Clock {
  time(at: number): string;
  day(at: number): string;
}

const pad = (n: number): string => String(n).padStart(2, "0");

/** This browser's zone and its own way of writing a time. */
export const LOCAL: Clock = {
  time: (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  day: (at) => {
    const d = new Date(at * 1000);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },
};

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

// -- times --------------------------------------------------------------------------------

/** A time services gives (ISO 8601 with `Z` or an offset) as epoch seconds; null when it
 * isn't one, or names no zone. */
export function epochOf(iso: string | null): number | null {
  if (iso === null) return null;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.test(iso)) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms / 1000;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 86_400_000;

function stamp(day: string): number {
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? Date.parse(`${day}T00:00:00Z`) : NaN;
}

/** A day against `today`, as the Projects page words one: `yesterday`, `tomorrow`, the
 * weekday within six days either side (`Fri`), else the date (`Oct 10`). */
function dayWord(day: string, today: string): string {
  const at = stamp(day);
  const off = Math.round((at - stamp(today)) / DAY_MS);
  if (Number.isNaN(off)) return day;
  if (off === -1) return "yesterday";
  if (off === 1) return "tomorrow";
  if (Math.abs(off) <= 6) return WEEKDAYS[new Date(at).getUTCDay()];
  const d = new Date(at);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** A moment in words: `18:00` on today's date, else `yesterday 22:10`, `Fri 22:10`,
 * `Oct 10 08:52`. */
export function whenWord(at: number, now: number, clock: Clock = LOCAL): string {
  const time = clock.time(at);
  const day = clock.day(at);
  const today = clock.day(now);
  return day === today ? time : `${dayWord(day, today)} ${time}`;
}

// -- the table ----------------------------------------------------------------------------

/** What the parts and lines are worded against: now, in epoch seconds, and the clock. */
export interface When {
  now: number;
  clock?: Clock;
}

/** `text` cut to the contract's longest, ending `…` when cut. */
export function clip(text: string): string {
  const chars = Array.from(text);
  return chars.length <= TEXT_MAX_CHARS ? text : `${chars.slice(0, TEXT_MAX_CHARS - 1).join("")}…`;
}

function at(iso: string | null, when: When): string | null {
  const found = epochOf(iso);
  return found === null ? null : whenWord(found, when.now, when.clock);
}

/** Whether a row's verdict says nothing the Projects page draws: services can't say. */
function silent(row: Row): boolean {
  return row.verdict.state === "warn" && row.verdict.unknown;
}

/**
 * A row's part for its project's second line, or null for none:
 *
 * - `fail`, cause `not-running`: heavy, ranked, `<name> failed at <time>`, the time from
 *   `since`, left out unless the supervisor's state is `failed` or `inactive` and `since`
 *   parses;
 * - `fail`, any other cause: heavy, ranked, `<name> failed`;
 * - `warn` that services could read, cause `stopped`: a warning, `<name> is stopped`;
 * - `warn` that services could read, cause `last-run`: a warning, `<name>'s last run failed
 *   at <time>`, the time from `last_run.at`, left out when it doesn't parse;
 * - every other verdict, and a cause services may add later: none.
 */
export function partOf(row: Row, when: When): Part | null {
  const { state, cause } = row.verdict;
  if (state === "fail") {
    const time = cause === "not-running" && (row.state === "failed" || row.state === "inactive")
      ? at(row.since, when) : null;
    return { text: clip(time === null ? `${row.name} failed` : `${row.name} failed at ${time}`),
             heavy: true, rank: BROKEN };
  }
  if (state !== "warn" || silent(row)) return null;
  if (cause === "stopped") return { text: clip(`${row.name} is stopped`), warn: true };
  if (cause === "last-run") {
    const time = at(row.last_run?.at ?? null, when);
    return { text: clip(time === null ? `${row.name}'s last run failed` : `${row.name}'s last run failed at ${time}`),
             warn: true };
  }
  return null;
}

/** The verdict's reason as a part, or none when the phone's gateway left it out. */
function reasonOf(row: Row): Part[] {
  const reason = row.verdict.reason;
  return reason === null || reason === "" ? [] : [{ text: clip(reason) }];
}

/**
 * A failed or warned row's line in the opened `Services` row, or null for none: a failed
 * row's part, then its reason; a stopped row's or a failed last run's part; any other warn
 * services could read, its name as a warning, then its reason (`won't start at login`).
 */
export function lineOf(row: Row, when: When): Line | null {
  const { state } = row.verdict;
  if (state === "fail") return { parts: [partOf(row, when)!, ...reasonOf(row)] };
  if (state !== "warn" || silent(row)) return null;
  const part = partOf(row, when);
  if (part !== null) return { parts: [part] };
  return { parts: [{ text: clip(row.name), warn: true }, ...reasonOf(row)] };
}

/**
 * An `ok` or `off` row's part on the quiet last line, or null for another verdict: a
 * running daemon, `<name> running since <time>`; a timer, `<name> <schedule>, last ran
 * <time>`, each clause left out when it is absent; any other `ok` row, `<name>` then its
 * reason (`idle, socket listening`); an `off` row, `<name> off`.
 */
export function quietOf(row: Row, when: When): Part | null {
  const { state } = row.verdict;
  if (state === "off") return { text: clip(`${row.name} off`) };
  if (state !== "ok") return null;
  if (row.kind === "daemon" && row.state === "active") {
    const time = at(row.since, when);
    return { text: clip(time === null ? `${row.name} running` : `${row.name} running since ${time}`) };
  }
  if (row.kind === "timer") {
    const time = at(row.last_run?.at ?? null, when);
    const ran = time === null ? null : `last ran ${time}`;
    if (row.schedule !== null && ran !== null) return { text: clip(`${row.name} ${row.schedule}, ${ran}`) };
    if (row.schedule !== null) return { text: clip(`${row.name} ${row.schedule}`) };
    if (ran !== null) return { text: clip(`${row.name} ${ran}`) };
  }
  const reason = row.verdict.reason;
  return { text: clip(reason ? `${row.name} ${reason}` : row.name) };
}

/** `parts` kept to `PARTS_MAX`: past it, the first one fewer and `<n> more`, which keeps
 * the weight of the strongest part it stands for. */
export function capped(parts: Part[]): Part[] {
  if (parts.length <= PARTS_MAX) return parts;
  const kept = parts.slice(0, PARTS_MAX - 1);
  const rest = parts.slice(PARTS_MAX - 1);
  const more: Part = { text: `${rest.length} more` };
  const ranked = rest.find((part) => part.rank !== undefined);
  if (ranked !== undefined) {
    more.heavy = true;
    more.rank = ranked.rank;
  } else if (rest.some((part) => part.warn)) {
    more.warn = true;
  }
  return [...kept, more];
}

// -- the report ---------------------------------------------------------------------------

const RANKED = { fail: 0, warn: 1, ok: 2, off: 2 } as const;

/** The opened project's `Services` row: a line per failed row, then per warned row that has
 * one, at most five, then one quiet last line of the failed and warned rows past those
 * five (keeping their weight) and every `ok` and `off` row. Ranked while a row fails. */
export function sectionOf(rows: readonly Row[], when: When): Section | null {
  const lines: Line[] = [];
  const past: Part[] = [];
  const quiet: Part[] = [];
  for (const row of rows) {
    const line = lineOf(row, when);
    if (line !== null) {
      if (lines.length < LINES_MAX - 1) {
        lines.push(line);
      } else {
        past.push(partOf(row, when) ?? line.parts[0]);
      }
      continue;
    }
    const part = quietOf(row, when);
    if (part !== null) quiet.push(part);
  }
  const last = [...past, ...quiet];
  if (last.length) lines.push({ parts: capped(last) });
  if (!lines.length) return null;
  const section: Section = { label: LABEL, lines, link: { text: LABEL, module: "services", route: "" } };
  if (rows.some((row) => row.verdict.state === "fail")) section.rank = BROKEN;
  return section;
}

/**
 * What the component reports for `rows` (services' `/api/rows` value, or null when the
 * last read failed), with `given` the Projects page's projects and `open` the opened key:
 * null unless the read is good and its `state` is `up`. Each given project that has a row
 * gets its second line's parts, failed rows first, and the opened one its `Services` row.
 * A row that names no given project, and every module's own row, is left out.
 */
export function factsReport(rows: Rows | null, given: readonly Given[], open: string | null,
                            when: When): Report | null {
  if (rows === null || rows.state !== "up") return null;
  const keys = new Set(given.map((project) => project.key));
  const by = new Map<string, Row[]>();
  for (const row of rows.rows) {
    if (row.form === "module" || row.project === null || !keys.has(row.project)) continue;
    const list = by.get(row.project) ?? [];
    list.push(row);
    by.set(row.project, list);
  }
  const projects: Record<string, ProjectFacts> = {};
  for (const [key, list] of by) {
    const ordered = [...list].sort((a, b) => RANKED[a.verdict.state] - RANKED[b.verdict.state]);
    const facts: ProjectFacts = {};
    const parts = ordered.map((row) => partOf(row, when)).filter((part): part is Part => part !== null);
    if (parts.length) facts.parts = capped(parts);
    if (key === open) {
      const section = sectionOf(ordered, when);
      if (section !== null) facts.sections = [section];
    }
    projects[key] = facts;
  }
  return { version: VERSION, order: ORDER, gathers: GATHERS, projects };
}
