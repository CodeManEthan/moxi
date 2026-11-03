// How loadout's page and drift block read their data: one route each, read on demand, with
// every answer that isn't a readable 200 mapped to a state. The shapes are the routes'
// own, as loadout's page server declares them. No DOM and no Lit here, so the Node tests
// drive it with a stubbed `read`.

import type { ApiResult } from "/m/ui/client.js";

/** One item for one harness, or a placement no longer wanted. `state` is one of `STATES`. */
export interface Row {
  /** `user` or a project section's name; null for a placement whose record names none. */
  scope: string | null;
  kind: string | null;
  name: string | null;
  harness: string | null;
  state: string;
  reason: string;
  target: string | null;
  /** `install --replace` places over what stands in its way: an occupant, one git tracks
   * among them, or a file edited by hand. */
  replaceable: boolean;
}

/** What a run came to, as `--json` prints it. */
export interface Outcome {
  rows: Row[];
  /** The lines said on the way, the core lock's waiting line among them. */
  lines: string[];
  notes: string[];
  /** How many rows are in each state; a state no row is in is left out. */
  counts: Record<string, number>;
  exit: number;
}

/** `/api/status`: every item's state for each harness. */
export interface Status extends Outcome {
  /** Why there is nothing to show (a manifest, ledger or config that can't be read). */
  problem: string | null;
}

/** One item a section lists. */
export interface Item {
  kind: string;
  name: string;
  scope: string;
  /** null for every harness. */
  harnesses: string[] | null;
  /** A path, `module <name>` for a module's skill, or `held by the project`. */
  source: string;
  /** The harnesses it is switched off for there, `*` for all. */
  disabled: string[];
}

/** One section of the manifest. An act names a section by `name`. */
export interface Section {
  name: string;
  /** `user`, `path` or `key`. */
  where: string;
  /** A path section's absolute root, else null. */
  root: string | null;
}

/** `/api/list`: the items each section lists, and the sections. */
export interface Listing {
  problem: string | null;
  manifest: string | null;
  sections: Section[];
  items: Item[];
}

/** One global instruction file and what the next install makes of it. */
export interface GlobalFile {
  file: string;
  changes: boolean;
  /** A unified diff of the change, or, for a file `if-absent` placed, why its state isn't ok. */
  diff: string;
  placed: boolean;
  state: string | null;
}

/** `/api/global`: the global mode in force and each global file. */
export interface Global {
  problem: string | null;
  mode: string;
  /** False while the manifest sets no mode (it reads `off` then). */
  chosen: boolean;
  files: GlobalFile[];
}

export interface Project {
  key: string;
  root: string;
}

/** `/api/projects`: the projects a section can name by key, or why there are none. */
export interface Projects {
  projects: Project[];
  reason: string | null;
}

/** `/api/drift`: how many items aren't ok, and the harness homes' note. */
export interface Drift {
  problem: string | null;
  count: number;
  /** The page's harness homes aren't the ones the user's own last run used; never counted. */
  note: string | null;
}

/** `/api/job`: one page job. */
export interface Job {
  job: string;
  argv: string[] | null;
  state: "running" | "done" | "interrupted";
  exit: number | null;
  /** The verb's outcome once done, null when it printed none. */
  outcome: Outcome | null;
  lines: string[];
}

/** Every state an item's row can be in. */
export const STATES = ["ok", "missing", "stale", "modified", "blocked", "would-commit",
                       "bad-source", "off"] as const;
/** The states the drift count leaves out. */
export const FINE: ReadonlySet<string> = new Set(["ok", "off"]);

/**
 * The event the page sends on the document once one of its acts has ended, so that every
 * loadout component on the page reads its route again. A component listens for it; it
 * carries no detail.
 */
export const CHANGED = "moxi-loadout-changed";

/** A request that gave no readable value. */
export interface Failure {
  /** The answer's status: 0 when no answer came, 200 for a body that can't be read. */
  status: number;
  /** One line saying what went wrong. */
  why: string;
  /** The answer's own line (its `refused`, `conflict` or `error`), when it gave one. */
  said: string | null;
}

/** How many times a 503 (the page server's in-flight cap) is read again, 1 s apart. */
export const RETRIES = 3;
export const RETRY_MS = 1000;

// -- the shapes ---------------------------------------------------------------------------

type Obj = Record<string, unknown>;

const isObj = (value: unknown): value is Obj =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isStr = (value: unknown): value is string => typeof value === "string";
const isStrOrNull = (value: unknown): value is string | null => value === null || isStr(value);
const isInt = (value: unknown): value is number => Number.isInteger(value);
const isStrings = (value: unknown): value is string[] => Array.isArray(value) && value.every(isStr);
const isListOf = <T>(value: unknown, check: (item: unknown) => item is T): value is T[] =>
  Array.isArray(value) && value.every(check);

function isRow(value: unknown): value is Row {
  return isObj(value) && isStr(value.state) && isStr(value.reason)
    && isStrOrNull(value.scope) && isStrOrNull(value.kind) && isStrOrNull(value.name)
    && isStrOrNull(value.harness) && isStrOrNull(value.target)
    && typeof value.replaceable === "boolean";
}

function isCounts(value: unknown): value is Record<string, number> {
  return isObj(value) && Object.values(value).every(isInt);
}

function isOutcome(value: unknown): value is Outcome {
  return isObj(value) && isListOf(value.rows, isRow) && isStrings(value.lines)
    && isStrings(value.notes) && isCounts(value.counts) && isInt(value.exit);
}

function isItem(value: unknown): value is Item {
  return isObj(value) && isStr(value.kind) && isStr(value.name) && isStr(value.scope)
    && (value.harnesses === null || isStrings(value.harnesses)) && isStr(value.source)
    && isStrings(value.disabled);
}

function isSection(value: unknown): value is Section {
  return isObj(value) && isStr(value.name) && isStr(value.where) && isStrOrNull(value.root);
}

function isGlobalFile(value: unknown): value is GlobalFile {
  return isObj(value) && isStr(value.file) && typeof value.changes === "boolean"
    && isStr(value.diff) && typeof value.placed === "boolean" && isStrOrNull(value.state);
}

function isProject(value: unknown): value is Project {
  return isObj(value) && isStr(value.key) && isStr(value.root);
}

/** `/api/status`'s value, or null when it isn't one. */
export function parseStatus(data: unknown): Status | null {
  if (!isObj(data) || !isStrOrNull(data.problem) || !isOutcome(data)) return null;
  const { problem, rows, lines, notes, counts, exit } = data as unknown as Status;
  return { problem, rows, lines, notes, counts, exit };
}

/** `/api/list`'s value, or null when it isn't one. */
export function parseListing(data: unknown): Listing | null {
  if (!isObj(data) || !isStrOrNull(data.problem) || !isStrOrNull(data.manifest)
      || !isListOf(data.sections, isSection) || !isListOf(data.items, isItem)) return null;
  return { problem: data.problem, manifest: data.manifest, sections: data.sections,
           items: data.items };
}

/** `/api/global`'s value, or null when it isn't one. */
export function parseGlobal(data: unknown): Global | null {
  if (!isObj(data) || !isStrOrNull(data.problem) || !isStr(data.mode)
      || typeof data.chosen !== "boolean" || !isListOf(data.files, isGlobalFile)) return null;
  return { problem: data.problem, mode: data.mode, chosen: data.chosen, files: data.files };
}

/** `/api/projects`'s value, or null when it isn't one. */
export function parseProjects(data: unknown): Projects | null {
  if (!isObj(data) || !isListOf(data.projects, isProject) || !isStrOrNull(data.reason)) return null;
  return { projects: data.projects, reason: data.reason };
}

/** `/api/drift`'s value, or null when it isn't one. */
export function parseDrift(data: unknown): Drift | null {
  if (!isObj(data) || !isStrOrNull(data.problem) || !isInt(data.count) || data.count < 0
      || !isStrOrNull(data.note)) return null;
  return { problem: data.problem, count: data.count, note: data.note };
}

/** `/api/job`'s value, or null when it isn't one. */
export function parseJob(data: unknown): Job | null {
  if (!isObj(data) || !isStr(data.job) || !isStrings(data.lines)) return null;
  const { state, exit, outcome, argv } = data;
  if (state !== "running" && state !== "done" && state !== "interrupted") return null;
  if (exit !== null && !isInt(exit)) return null;
  if (outcome !== null && !isOutcome(outcome)) return null;
  if (argv !== null && !isStrings(argv)) return null;
  return { job: data.job, argv, state, exit, outcome, lines: data.lines };
}

/** The text an answer gives: its `refused`, `conflict` or `error`. */
export function saidOf(data: unknown): string | null {
  if (!isObj(data)) return null;
  for (const name of ["refused", "conflict", "error"]) {
    const value = data[name];
    if (isStr(value) && value !== "") return value;
  }
  return null;
}

// -- reading ------------------------------------------------------------------------------

/** The line a failure shows. */
export function failureWhy(status: number): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "loadout's page server sent an answer that can't be read";
  return `loadout's page server answered ${status}`;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/**
 * One route's data. `refresh()` reads the route; `onChange` is called after every read.
 *
 * - A 200 with a readable body gives the value.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means the module went away while the page was open: `gone`.
 * - Every other answer (any other status, a network failure, a body that isn't JSON or
 *   isn't the route's shape) is a failure, and the last value is kept.
 */
export class Feed<V> {
  /** The last value read, or null before the first. */
  value: V | null = null;
  /** The last request's failure, null once a read succeeds. */
  failure: Failure | null = null;
  /** The route answered 404. */
  gone = false;
  /** No read has finished yet. */
  loading = true;
  /** A read is running. */
  reading = false;

  #read: () => Promise<ApiResult>;
  #parse: (data: unknown) => V | null;
  #onChange: () => void;
  #wait: Wait;
  #running: Promise<void> | null = null;
  #again = false;
  #closed = false;

  constructor(read: () => Promise<ApiResult>, parse: (data: unknown) => V | null,
              onChange: () => void, { wait = sleep }: { wait?: Wait } = {}) {
    this.#read = read;
    this.#parse = parse;
    this.#onChange = onChange;
    this.#wait = wait;
  }

  /** Stops every change: a later answer is dropped. */
  close(): void {
    this.#closed = true;
  }

  /** Reads the route; a call while a read runs makes one more read after it. */
  refresh(): Promise<void> {
    if (this.#running) {
      this.#again = true;
      return this.#running;
    }
    this.reading = true;
    this.#running = (async () => {
      try {
        do {
          this.#again = false;
          await this.#once();
        } while (this.#again && !this.#closed);
      } finally {
        this.#running = null;
        this.reading = false;
        if (!this.#closed) this.#onChange();
      }
    })();
    return this.#running;
  }

  async #once(): Promise<void> {
    let answer = await this.#read();
    for (let tries = 0; answer.status === 503 && tries < RETRIES && !this.#closed; tries += 1) {
      await this.#wait(RETRY_MS);
      if (this.#closed) return;
      answer = await this.#read();
    }
    if (this.#closed) return;
    this.loading = false;
    const value = answer.status === 200 ? this.#parse(answer.data) : null;
    if (value !== null) {
      this.value = value;
      this.failure = null;
      this.gone = false;
    } else if (answer.status === 404) {
      this.gone = true;
      this.failure = null;
    } else {
      this.failure = { status: answer.status, why: failureWhy(answer.status),
                       said: saidOf(answer.data) };
    }
  }
}
