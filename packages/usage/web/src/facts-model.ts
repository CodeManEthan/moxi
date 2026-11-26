// What usage's `project-facts` component reports to the Projects page, as plain data: each
// project's cost today on its closed row, the opened project's `Cost` row and its `Stays`
// line, and the cost of each folder in no project over the last 7 days. No DOM and no Lit
// here, so the Node tests check it directly. projects' README, "Facts on the Projects
// page", is the contract it fills.
//
// Usage's judgment of a figure is its row's `cost` and `complete`, read as usage's own Home
// row reads them: a `cost` of null draws no figure, and `complete` false draws the figure
// with `+` after it. It never sums or prices tokens itself. Each figure comes from one of
// three reads of `/api/projects`, and a read that failed drops what it fed:
//
//   today            `$8.49 today` on line two; the `Cost` row's today figure
//   the last 7 days  the `Cost` row's 7-day figure; the folders
//   the last 30 days the `Cost` row's 30-day figure (read only while a project is open)

import { addressPath, projectAddress } from "./address-model.ts";
import type { ProjectRow, Report as UsageReport } from "./feed.ts";
import { fmtCost } from "./views.ts";

/** The event a facts component dispatches, with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version. */
export const VERSION = 1;
/** Where usage's parts and section sit among other modules'. */
export const ORDER = 50;
/** What usage gathers under a project, for the Projects page's empty state. */
export const GATHERS = "cost";
/** The opened project's row, and its link's words. */
export const LABEL = "Cost";
export const LINK_TEXT = "Usage";
/** The last words of the `Cost` row: the figures are usage's estimate, not a bill. */
export const ESTIMATE = "an estimate";
/** The longest text the contract takes. */
export const TEXT_MAX_CHARS = 200;

/** A project as the Projects page gives it, in the `projects` attribute: the key is all
 * this component reads. */
export interface Given {
  key: string;
}

export interface Part {
  text: string;
}

export interface Line {
  parts: Part[];
}

export interface Section {
  label: string;
  lines: Line[];
  link: { text: string; module: string; route: string };
}

export interface ProjectFacts {
  parts?: Part[];
  sections?: Section[];
  stays?: string[];
}

export interface FolderFacts {
  folder: string;
  parts: Part[];
}

export interface Report {
  version: number;
  order: number;
  gathers: string;
  projects: Record<string, ProjectFacts>;
  folders?: FolderFacts[];
}

/** The three reads' last good answers; null for one whose last read failed, or that has
 * not been read (the 30-day one with no project open). */
export interface Reads {
  today: UsageReport<ProjectRow> | null;
  week: UsageReport<ProjectRow> | null;
  month: UsageReport<ProjectRow> | null;
}

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
  return list.filter((item): item is Record<string, unknown> => isObj(item) && typeof item.key === "string")
    .map((item) => ({ key: item.key as string }));
}

/** `text` cut to the contract's longest, ending `…` when cut. */
export function clip(text: string): string {
  const chars = Array.from(text);
  return chars.length <= TEXT_MAX_CHARS ? text : `${chars.slice(0, TEXT_MAX_CHARS - 1).join("")}…`;
}

/** A row's figure as usage words it: `$8.49`, `$8.49+` when some of it could not be
 * priced; null when nothing in it could be (`cost` null). */
export function figure(row: Pick<ProjectRow, "cost" | "complete"> | undefined): string | null {
  if (row === undefined || row.cost === null || !Number.isFinite(row.cost)) return null;
  return `${fmtCost(row.cost)}${row.complete ? "" : "+"}`;
}

/** Whether a row's figure says something on a closed row: a cost above zero, or one that
 * is not whole. */
function worthSaying(row: Pick<ProjectRow, "cost" | "complete"> | undefined): boolean {
  return row !== undefined && row.cost !== null && Number.isFinite(row.cost) && (row.cost > 0 || !row.complete);
}

/** The report's row for project `key`; undefined when it has none. */
export function rowOf(report: UsageReport<ProjectRow> | null, key: string): ProjectRow | undefined {
  return report?.rows.find((row) => row.project === key);
}

/** The `Cost` row of project `key`: today, 7 days and 30 days, each only where its read
 * gave the project a figure, then `an estimate`; null when no read gave one. Its link opens
 * Usage filtered to the project. */
export function costSection(reads: Reads, key: string): Section | null {
  const said = [
    [figure(rowOf(reads.today, key)), "today"],
    [figure(rowOf(reads.week, key)), "in 7 days"],
    [figure(rowOf(reads.month, key)), "in 30 days"],
  ].filter((pair): pair is [string, string] => pair[0] !== null);
  if (!said.length) return null;
  return {
    label: LABEL,
    lines: [{ parts: [...said.map(([cost, when]) => ({ text: `${cost} ${when}` })), { text: ESTIMATE }] }],
    link: { text: LINK_TEXT, module: "usage", route: addressPath(projectAddress(key)).slice("/m/usage/".length) },
  };
}

/** The opened project's `Stays` line: usage keeps the key a session's cost was stored
 * under, so its cost stays listed under the key once the project is gone. */
export function staysLine(key: string): string {
  return clip(`what they cost, still listed under ${key} on Usage`);
}

/** Each folder in no project the 7-day read gives a figure, with that figure. */
export function folderFacts(week: UsageReport<ProjectRow> | null): FolderFacts[] {
  if (week === null) return [];
  const folders: FolderFacts[] = [];
  const seen = new Set<string>();
  for (const row of week.rows) {
    if (row.project !== null || !row.cwd || seen.has(row.cwd) || !worthSaying(row)) continue;
    seen.add(row.cwd);
    folders.push({ folder: row.cwd, parts: [{ text: figure(row) as string }] });
  }
  return folders;
}

/**
 * What the component reports for `reads`, with `given` the Projects page's projects and
 * `open` the opened key: null when no read is good. A project's closed row says
 * `$8.49 today` when today's read gives it a cost above zero, or one that is not whole; the
 * opened project, when it is one of `given`, gets its `Cost` row while a read gives it a
 * figure, and its `Stays` line.
 */
export function factsReport(reads: Reads, given: readonly Given[], open: string | null): Report | null {
  if (reads.today === null && reads.week === null && reads.month === null) return null;
  const projects: Record<string, ProjectFacts> = {};
  for (const { key } of given) {
    const facts: ProjectFacts = {};
    const today = rowOf(reads.today, key);
    if (worthSaying(today)) facts.parts = [{ text: `${figure(today)} today` }];
    if (key === open) {
      const section = costSection(reads, key);
      if (section !== null) facts.sections = [section];
      facts.stays = [staysLine(key)];
    }
    if (Object.keys(facts).length) projects[key] = facts;
  }
  const report: Report = { version: VERSION, order: ORDER, gathers: GATHERS, projects };
  const folders = folderFacts(reads.week);
  if (folders.length) report.folders = folders;
  return report;
}
