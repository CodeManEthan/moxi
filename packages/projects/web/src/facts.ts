// The facts contract's report as the page reads it, with no DOM so the Node tests can
// drive it. A module adds facts to the Projects page through its `project-facts`
// component, which reports them in a `moxi-project-facts` event; `readReport` keeps what
// fits the contract and names each field it refuses, so the page can log it. projects'
// README, "Facts on the Projects page", states the contract whole.

/** The component name a module declares in its `COMPONENTS` to add facts. */
export const FACTS_COMPONENT = "project-facts";
/** The event a facts component dispatches with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version: a report with any other is refused whole. */
export const VERSION = 1;
/** The longest text a report may carry; a longer one is refused. */
export const TEXT_MAX_CHARS = 200;
/** The longest folder path a report may carry, as `/api/folder` takes one. */
export const PATH_MAX_CHARS = 4096;
/** The most parts one row or one line may have; more refuses the list. */
export const PARTS_MAX = 8;
/** The most lines one section may have; more refuses the section. */
export const LINES_MAX = 6;
/** The highest `order`. */
export const ORDER_MAX = 999;
/** The `order` a report with none, or a wrong one, takes. */
export const ORDER_DEFAULT = 500;

/** A project as the page gives it to every component, in the `projects` attribute. */
export interface Given {
  key: string;
  root: string;
  lead: string | null;
  associated: string[];
}

/** Whether `at` covers every record a component has (`all`) or only a window of them. */
export type Activity = "all" | "window";

/** The words for a counted part: `<n> <one|many>`, or after lines `<n> <after_one|after_many>`. */
export interface Count {
  one: string;
  many: string;
  after_one: string | null;
  after_many: string | null;
}

/** One phrase. `rank` is Home's `needs` scale, null for none. */
export interface Part {
  text: string;
  heavy: boolean;
  warn: boolean;
  rank: number | null;
  /** The sessions it names, `<harness>/<session id>`. */
  claims: string[];
  count: Count | null;
}

/** One line of an opened project's section. */
export interface Line {
  parts: Part[];
  claims: string[];
}

/** A link a section draws at its right: a press is `navigate(module, route)`. */
export interface Link {
  text: string;
  module: string;
  route: string;
}

/** One labelled row of the opened project. */
export interface Section {
  label: string;
  rank: number | null;
  lines: Line[];
  link: Link | null;
}

/**
 * What one module says of an agent: `runs`, whether it says the agent runs now; `state`,
 * its words for that; `where`, where it runs, only with `runs` true.
 */
export interface Say {
  runs: boolean;
  state: string;
  where: string | null;
}

/** What a report says of one project. Sections, agents and stays are the open one's only. */
export interface Facts {
  /** Epoch seconds of the newest activity it knows here, or null. */
  at: number | null;
  parts: Part[];
  sections: Section[];
  /** By agent name: the open project's lead and associated agents only. */
  agents: Map<string, Say>;
  /** The lines Remove's step lists under Stays. */
  stays: string[];
}

/** A folder no project holds. `at` only from a report with an `activity`. */
export interface FolderFacts {
  folder: string;
  at: number | null;
  parts: Part[];
}

/** A report, as far as it fits the contract. */
export interface Report {
  order: number;
  activity: Activity | null;
  gathers: string | null;
  /** By key, exactly as given. */
  projects: Map<string, Facts>;
  folders: FolderFacts[];
  more_folders: number;
}

/** A report read: what is kept, null for nothing to say, and each refusal in a line. */
export interface Read {
  report: Report | null;
  refused: string[];
}

type Obj = Record<string, unknown>;

const isObj = (value: unknown): value is Obj =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The length of `text` as a person counts characters, not UTF-16 units. */
const charsOf = (text: string): number => [...text].length;

/** Whether `path` is absolute on any platform a page server runs on. */
export function isAbsolutePath(path: string): boolean {
  return path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path) || path.startsWith("\\\\");
}

/** Where each refusal goes, with the field's place in the report. */
class Refusals {
  lines: string[] = [];

  add(place: string, why: string): void {
    this.lines.push(`${place}: ${why}`);
  }
}

/** A text field: a string of 1 to 200 characters, else null and refused. */
function readText(value: unknown, place: string, refused: Refusals): string | null {
  if (typeof value !== "string") {
    refused.add(place, "not a string");
    return null;
  }
  if (value === "") {
    refused.add(place, "empty");
    return null;
  }
  if (charsOf(value) > TEXT_MAX_CHARS) {
    refused.add(place, `over ${TEXT_MAX_CHARS} characters`);
    return null;
  }
  return value;
}

/** An optional text field: null when absent, and when refused. */
function readOptionalText(value: unknown, place: string, refused: Refusals): string | null {
  return value === undefined ? null : readText(value, place, refused);
}

function readFlag(value: unknown, place: string, refused: Refusals): boolean {
  if (value === undefined) return false;
  if (typeof value !== "boolean") {
    refused.add(place, "not true or false");
    return false;
  }
  return value;
}

function readRank(value: unknown, place: string, refused: Refusals): number | null {
  if (value === undefined) return null;
  if (!Number.isInteger(value) || (value as number) < 1 || (value as number) > ORDER_MAX) {
    refused.add(place, `not a whole number from 1 to ${ORDER_MAX}`);
    return null;
  }
  return value as number;
}

function readAt(value: unknown, place: string, refused: Refusals): number | null {
  if (value === undefined) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    refused.add(place, "not epoch seconds");
    return null;
  }
  return value;
}

/** A list of texts: each one refused alone; a value that is no list refuses them all. */
function readTexts(value: unknown, place: string, refused: Refusals): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) {
    refused.add(place, "not a list");
    return [];
  }
  const texts: string[] = [];
  value.forEach((item, index) => {
    const text = readText(item, `${place}[${index}]`, refused);
    if (text !== null) texts.push(text);
  });
  return texts;
}

function readCount(value: unknown, place: string, refused: Refusals): Count | null {
  if (value === undefined) return null;
  if (!isObj(value)) {
    refused.add(place, "not an object");
    return null;
  }
  const one = readText(value.one, `${place}.one`, refused);
  const many = readText(value.many, `${place}.many`, refused);
  if (one === null || many === null) return null;
  return {
    one, many,
    after_one: readOptionalText(value.after_one, `${place}.after_one`, refused),
    after_many: readOptionalText(value.after_many, `${place}.after_many`, refused),
  };
}

function readPart(value: unknown, place: string, refused: Refusals): Part | null {
  if (!isObj(value)) {
    refused.add(place, "not a part");
    return null;
  }
  const count = readCount(value.count, `${place}.count`, refused);
  let text: string | null;
  if (count !== null && value.text === "") {
    text = "";
  } else {
    text = readText(value.text, `${place}.text`, refused);
  }
  if (text === null) return null;
  return {
    text,
    heavy: readFlag(value.heavy, `${place}.heavy`, refused),
    warn: readFlag(value.warn, `${place}.warn`, refused),
    rank: readRank(value.rank, `${place}.rank`, refused),
    claims: readTexts(value.claims, `${place}.claims`, refused),
    count,
  };
}

/** A list of parts. Over `PARTS_MAX` refuses the list; a part that won't read is left out. */
function readParts(value: unknown, place: string, refused: Refusals): Part[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) {
    refused.add(place, "not a list");
    return [];
  }
  if (value.length > PARTS_MAX) {
    refused.add(place, `more than ${PARTS_MAX} parts`);
    return [];
  }
  const parts: Part[] = [];
  value.forEach((item, index) => {
    const part = readPart(item, `${place}[${index}]`, refused);
    if (part !== null) parts.push(part);
  });
  return parts;
}

/** A line, or null when it has no part left to draw. */
function readLine(value: unknown, place: string, refused: Refusals): Line | null {
  if (!isObj(value)) {
    refused.add(place, "not a line");
    return null;
  }
  const parts = readParts(value.parts, `${place}.parts`, refused);
  if (parts.length === 0) return null;
  return { parts, claims: readTexts(value.claims, `${place}.claims`, refused) };
}

function readLink(value: unknown, place: string, refused: Refusals): Link | null {
  if (value === undefined) return null;
  if (!isObj(value)) {
    refused.add(place, "not a link");
    return null;
  }
  const text = readText(value.text, `${place}.text`, refused);
  const module = readText(value.module, `${place}.module`, refused);
  let route: string | null = "";
  if (value.route !== "") route = readText(value.route, `${place}.route`, refused);
  if (text === null || module === null || route === null) return null;
  return { text, module, route };
}

/** A section, or null when it has no line left to draw. */
function readSection(value: unknown, place: string, refused: Refusals): Section | null {
  if (!isObj(value)) {
    refused.add(place, "not a section");
    return null;
  }
  const label = readText(value.label, `${place}.label`, refused);
  if (label === null) return null;
  if (!Array.isArray(value.lines)) {
    refused.add(`${place}.lines`, "not a list");
    return null;
  }
  if (value.lines.length > LINES_MAX) {
    refused.add(`${place}.lines`, `more than ${LINES_MAX} lines`);
    return null;
  }
  const lines: Line[] = [];
  value.lines.forEach((item, index) => {
    const line = readLine(item, `${place}.lines[${index}]`, refused);
    if (line !== null) lines.push(line);
  });
  if (lines.length === 0) return null;
  return {
    label, lines,
    rank: readRank(value.rank, `${place}.rank`, refused),
    link: readLink(value.link, `${place}.link`, refused),
  };
}

/** A `Say`. A `where` beside `runs` false contradicts it, so the whole `Say` is refused. */
function readSay(value: unknown, place: string, refused: Refusals): Say | null {
  if (!isObj(value)) {
    refused.add(place, "not a say");
    return null;
  }
  if (typeof value.runs !== "boolean") {
    refused.add(`${place}.runs`, "not true or false");
    return null;
  }
  if (!value.runs && value.where !== undefined) {
    refused.add(`${place}.where`, "given with runs false");
    return null;
  }
  const state = readText(value.state, `${place}.state`, refused);
  if (state === null) return null;
  const where = value.runs ? readOptionalText(value.where, `${place}.where`, refused) : null;
  return { runs: value.runs, state, where };
}

function readSections(value: unknown, place: string, refused: Refusals): Section[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) {
    refused.add(place, "not a list");
    return [];
  }
  const sections: Section[] = [];
  value.forEach((item, index) => {
    const section = readSection(item, `${place}[${index}]`, refused);
    if (section !== null) sections.push(section);
  });
  return sections;
}

function readAgents(value: unknown, names: ReadonlySet<string>, place: string,
                    refused: Refusals): Map<string, Say> {
  const agents = new Map<string, Say>();
  if (value === undefined) return agents;
  if (!isObj(value)) {
    refused.add(place, "not an object");
    return agents;
  }
  for (const [name, item] of Object.entries(value)) {
    const where = `${place}[${JSON.stringify(name)}]`;
    if (!names.has(name)) {
      refused.add(where, "no such agent was given for the open project");
      continue;
    }
    const say = readSay(item, where, refused);
    if (say !== null) agents.set(name, say);
  }
  return agents;
}

/** The fields only the open project may carry. */
const OPEN_ONLY = ["sections", "agents", "stays"] as const;

function readFacts(value: unknown, isOpen: boolean, names: ReadonlySet<string>, place: string,
                   refused: Refusals): Facts | null {
  if (!isObj(value)) {
    refused.add(place, "not an object");
    return null;
  }
  const facts: Facts = {
    at: readAt(value.at, `${place}.at`, refused),
    parts: readParts(value.parts, `${place}.parts`, refused),
    sections: [], agents: new Map(), stays: [],
  };
  if (!isOpen) {
    for (const name of OPEN_ONLY) {
      if (value[name] !== undefined) refused.add(`${place}.${name}`, "given for a project that is not open");
    }
    return facts;
  }
  facts.sections = readSections(value.sections, `${place}.sections`, refused);
  facts.agents = readAgents(value.agents, names, `${place}.agents`, refused);
  facts.stays = readTexts(value.stays, `${place}.stays`, refused);
  return facts;
}

function readProjects(value: unknown, given: readonly Given[], open: string | null,
                      refused: Refusals): Map<string, Facts> {
  const projects = new Map<string, Facts>();
  if (value === undefined) return projects;
  if (!isObj(value)) {
    refused.add("projects", "not an object");
    return projects;
  }
  const byKey = new Map(given.map((project) => [project.key, project]));
  for (const [key, item] of Object.entries(value)) {
    const place = `projects[${JSON.stringify(key)}]`;
    const project = byKey.get(key);
    if (project === undefined) {
      refused.add(place, "no such project was given");
      continue;
    }
    const names = new Set(project.lead === null ? project.associated : [project.lead, ...project.associated]);
    const facts = readFacts(item, key === open, names, place, refused);
    if (facts !== null) projects.set(key, facts);
  }
  return projects;
}

function readFolder(value: unknown, activity: Activity | null, place: string,
                    refused: Refusals): FolderFacts | null {
  if (!isObj(value)) {
    refused.add(place, "not an object");
    return null;
  }
  const folder = value.folder;
  if (typeof folder !== "string" || !isAbsolutePath(folder)) {
    refused.add(`${place}.folder`, "not an absolute path");
    return null;
  }
  if (charsOf(folder) > PATH_MAX_CHARS) {
    refused.add(`${place}.folder`, `over ${PATH_MAX_CHARS} characters`);
    return null;
  }
  let when: number | null = null;
  if (activity === null) {
    if (value.at !== undefined) refused.add(`${place}.at`, "given by a report with no activity");
  } else {
    when = readAt(value.at, `${place}.at`, refused);
  }
  return { folder, at: when, parts: readParts(value.parts, `${place}.parts`, refused) };
}

function readFolders(value: unknown, activity: Activity | null, refused: Refusals): FolderFacts[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) {
    refused.add("folders", "not a list");
    return [];
  }
  const folders: FolderFacts[] = [];
  const seen = new Set<string>();
  value.forEach((item, index) => {
    const place = `folders[${index}]`;
    const row = readFolder(item, activity, place, refused);
    if (row === null) return;
    if (seen.has(row.folder)) {
      refused.add(`${place}.folder`, "listed twice");
      return;
    }
    seen.add(row.folder);
    folders.push(row);
  });
  return folders;
}

function readOrder(value: unknown, refused: Refusals): number {
  if (Number.isInteger(value) && (value as number) >= 0 && (value as number) <= ORDER_MAX) {
    return value as number;
  }
  refused.add("order", `not a whole number from 0 to ${ORDER_MAX}; read as ${ORDER_DEFAULT}`);
  return ORDER_DEFAULT;
}

function readActivity(value: unknown, refused: Refusals): Activity | null {
  if (value === undefined) return null;
  if (value === "all" || value === "window") return value;
  refused.add("activity", "not all or window");
  return null;
}

function readMore(value: unknown, refused: Refusals): number {
  if (value === undefined) return 0;
  if (!Number.isInteger(value) || (value as number) < 0) {
    refused.add("more_folders", "not a whole number from 0");
    return 0;
  }
  return value as number;
}

/**
 * Reads a `moxi-project-facts` event's `detail`, given the projects the page gave the
 * component and the key of the open project (null for none).
 *
 * `null` (and `undefined`) is nothing to say. A detail that is no object, or whose
 * `version` is not `VERSION`, is refused whole. Otherwise each field that doesn't fit is
 * dropped and named in `refused`, and the rest is kept: a key or an agent the page did not
 * give, a field of the wrong type, a text over `TEXT_MAX_CHARS`, more than `PARTS_MAX`
 * parts on a row or a line, more than `LINES_MAX` lines in a section, an open project's
 * field on another, and a folder's `at` from a report with no `activity`. Fields the
 * contract doesn't name are ignored.
 */
export function readReport(detail: unknown, given: readonly Given[], open: string | null): Read {
  const refused = new Refusals();
  if (detail === null || detail === undefined) return { report: null, refused: [] };
  if (!isObj(detail)) {
    refused.add("report", "not an object");
    return { report: null, refused: refused.lines };
  }
  if (detail.version !== VERSION) {
    refused.add("version", `not ${VERSION}`);
    return { report: null, refused: refused.lines };
  }
  const activity = readActivity(detail.activity, refused);
  const report: Report = {
    order: readOrder(detail.order, refused),
    activity,
    gathers: readOptionalText(detail.gathers, "gathers", refused),
    projects: readProjects(detail.projects, given, open, refused),
    folders: readFolders(detail.folders, activity, refused),
    more_folders: readMore(detail.more_folders, refused),
  };
  return { report, refused: refused.lines };
}
