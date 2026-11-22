// What sessions' `project-facts` component reports to the Projects page, as plain data:
// each project's newest activity and its sessions active recently, for the opened project
// its `Sessions` row and Remove's `Stays` line, and the folders worked in this week that
// no project holds. No DOM and no Lit here, so the Node tests check it directly. projects'
// README, "Facts on the Projects page", is the contract it fills.
//
// Every fact comes from `/api/projects`, which folds the sessions into projects in the
// page server, through projects' own read API. That answer's `state` is sessions' only
// judgment here: `up` reports, and any other word reports null. Its rows carry counts,
// claims and times, no verdict, so nothing else is mapped.

import type { Feed } from "./feed.ts";
import { SHAPE } from "./feed.ts";

/** The event a facts component dispatches, with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version. */
export const VERSION = 1;
/** Where sessions' parts, lines and section sit among other modules'. */
export const ORDER = 30;
/** sessions' `at` covers every session it reads, not a window of them. */
export const ACTIVITY = "all";
/** What sessions gathers under a project, for the Projects page's empty sentence. */
export const GATHERS = "sessions";
/** The opened project's row, and its link's words. */
export const LABEL = "Sessions";
/** How often the component reads `/api/projects` while the document shows. */
export const READ_MS = 60_000;
/** The longest folder the contract takes, in characters. */
export const PATH_MAX_CHARS = 4096;

/** A project as `/api/projects` gives it. `week` and `recent` are claims,
 * `<harness>/<session id>`; a session a program started is in neither. */
export interface ProjectRow {
  key: string;
  at: number | null;
  all: number;
  week: string[];
  recent: string[];
}

/** A folder no project holds, with this week's sessions there. */
export interface LooseDir {
  folder: string;
  at: number | null;
  week: number;
}

/** `/api/projects`. */
export interface ProjectsAnswer {
  shape: number;
  now: number;
  /** `up`, `off` (projects is not installed, or too old) or `error`. */
  state: string;
  detail: string | null;
  recently_s: number;
  projects: ProjectRow[];
  folders: LooseDir[];
  more: number;
}

export interface Count {
  one: string;
  many: string;
  after_one?: string;
  after_many?: string;
}

export interface Part {
  text: string;
  count?: Count;
  claims?: string[];
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
  at?: number;
  parts?: Part[];
  sections?: Section[];
  stays?: string[];
}

export interface FolderFacts {
  folder: string;
  at?: number;
  parts: Part[];
}

export interface Report {
  version: number;
  order: number;
  activity: string;
  gathers: string;
  projects: Record<string, ProjectFacts>;
  folders: FolderFacts[];
  more_folders: number;
}

// -- reading --------------------------------------------------------------------------------

type Obj = Record<string, unknown>;

const isObj = (value: unknown): value is Obj =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === "string";
const isTime = (value: unknown): value is number | null =>
  value === null || (typeof value === "number" && Number.isFinite(value));
const isCount = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0;
const isClaims = (value: unknown): value is string[] => Array.isArray(value) && value.every(isText);

function isProject(value: unknown): value is ProjectRow {
  return isObj(value) && isText(value.key) && isTime(value.at) && isCount(value.all)
    && isClaims(value.week) && isClaims(value.recent);
}

function isLoose(value: unknown): value is LooseDir {
  return isObj(value) && isText(value.folder) && isTime(value.at) && isCount(value.week);
}

/** `/api/projects`' value, or null when it isn't one. A row that can't be read makes the
 * whole value unreadable, so no project's facts are ever left out unseen. */
export function parseProjects(data: unknown): ProjectsAnswer | null {
  if (!isObj(data) || data.shape !== SHAPE || !isText(data.state) || !(data.detail === null || isText(data.detail))
      || !isCount(data.recently_s) || !Array.isArray(data.projects) || !Array.isArray(data.folders)
      || !isCount(data.more) || typeof data.now !== "number") return null;
  if (!data.projects.every(isProject) || !data.folders.every(isLoose)) return null;
  return {
    shape: data.shape, now: data.now, state: data.state, detail: data.detail, recently_s: data.recently_s,
    projects: data.projects.map((p) => ({ key: p.key, at: p.at, all: p.all, week: [...p.week],
                                          recent: [...p.recent] })),
    folders: data.folders.map((f) => ({ folder: f.folder, at: f.at, week: f.week })),
    more: data.more,
  };
}

/** The keys in the `projects` attribute; an item that names none is left out, and a
 * value that isn't a JSON list gives none. */
export function parseGiven(text: string | null): string[] {
  let list: unknown;
  try {
    list = JSON.parse(text ?? "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(list)) return [];
  return list.filter((item): item is Obj => isObj(item) && isText(item.key)).map((item) => item.key as string);
}

/** The value a report is made from: the last read's, or null when that read failed, was
 * refused, or found sessions gone. */
export function goodValue<V>(feed: Pick<Feed<V>, "value" | "failure" | "refused" | "gone">): V | null {
  return feed.failure === null && feed.refused === null && !feed.gone ? feed.value : null;
}

// -- the words ------------------------------------------------------------------------------

/** `1 active recently`, `3 active recently`: the closed row's part, over `recent`. */
export const RECENT_COUNT: Count = { one: "active recently", many: "active recently" };
/** `2 sessions this week`, and after other lines `3 more this week`, over `week`. */
export const WEEK_COUNT: Count = { one: "session this week", many: "sessions this week",
                                   after_one: "more this week", after_many: "more this week" };

/** Remove's `Stays` line for a project with `all` sessions. */
export function staysLine(all: number): string {
  return all === 1 ? "its 1 session and its transcript" : `its ${all} sessions and their transcripts`;
}

/** A folder row's part: how many sessions ran there this week. */
export function weekWords(week: number): string {
  return week === 1 ? "1 session this week" : `${week} sessions this week`;
}

const isAbsolutePath = (path: string): boolean =>
  path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path) || path.startsWith("\\\\");

function projectFacts(row: ProjectRow, opened: boolean): ProjectFacts {
  const facts: ProjectFacts = {};
  if (row.at !== null) facts.at = row.at;
  if (row.recent.length > 0) facts.parts = [{ text: "", count: RECENT_COUNT, claims: row.recent }];
  if (opened) {
    if (row.week.length > 0) {
      facts.sections = [{ label: LABEL, lines: [{ parts: [{ text: "", count: WEEK_COUNT, claims: row.week }] }],
                          link: { text: LABEL, module: "sessions", route: "" } }];
    }
    if (row.all > 0) facts.stays = [staysLine(row.all)];
  }
  return facts;
}

/**
 * The report for `answer` over the projects the page gave (`given`, by key) and the one
 * in `open`: null when there is no answer or its `state` isn't `up`. A project the page
 * didn't give is left out, as is a folder that isn't an absolute path the contract takes
 * (counted in `more_folders` instead).
 */
export function factsReport(answer: ProjectsAnswer | null, given: readonly string[],
                            open: string | null): Report | null {
  if (answer === null || answer.state !== "up") return null;
  const keys = new Set(given);
  const projects: Record<string, ProjectFacts> = {};
  for (const row of answer.projects) {
    if (keys.has(row.key)) projects[row.key] = projectFacts(row, row.key === open);
  }
  const folders: FolderFacts[] = [];
  let skipped = 0;
  for (const row of answer.folders) {
    if (!isAbsolutePath(row.folder) || [...row.folder].length > PATH_MAX_CHARS) {
      skipped += 1;
      continue;
    }
    const folder: FolderFacts = { folder: row.folder, parts: [{ text: weekWords(row.week) }] };
    if (row.at !== null) folder.at = row.at;
    folders.push(folder);
  }
  return { version: VERSION, order: ORDER, activity: ACTIVITY, gathers: GATHERS, projects, folders,
           more_folders: answer.more + skipped };
}
