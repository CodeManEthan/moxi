// How projects' page and components read their data: one route each, read on demand, with
// every answer that isn't a readable 200 mapped to a state. The shapes are the routes'
// own, as projects' page server declares them. No DOM and no Lit here, so the Node tests
// drive it with a stubbed `read`.

import type { ApiResult } from "/m/ui/client.js";

/** One project, as the read API gives it. `agents` is the lead first, then the
 * associated set. A root is shown, never sent back to a host or put in a request that
 * names another project. */
export interface Project {
  key: string;
  root: string;
  kind: string;
  lead: string | null;
  associated: string[];
  agents: string[];
  notes_root: string | null;
  /** UTC, ISO 8601 with `Z`. */
  created: string;
}

/** `/api/projects`: every project, by key. */
export interface Projects {
  projects: Project[];
}

/** A project as the page's list gives it: whether its root is a directory now, null when
 * the page server can't say in time. */
export interface Listed extends Project {
  exists: boolean | null;
}

/** `/api/projects` as the page reads it: every project, the page server's home folder (null
 * when nothing places one) and whether notes is installed. */
export interface Listing {
  projects: Listed[];
  home: string | null;
  notes_installed: boolean;
}

/** `/api/project`: one project. */
export interface One {
  project: Project;
}

/** How `add --create` makes a kind's root: `git` (a directory and `git init`), `dir` (a
 * directory only) or `refuse` (create is refused for that kind). */
export type Mint = "git" | "dir" | "refuse";

/** One kind a project may have; `module` is `projects` for a built-in one. */
export interface Kind {
  name: string;
  doc: string;
  module: string;
  mint: Mint;
}

/** A contribution that won't load, or a kind that is blocked, and why. */
export interface Broken {
  module: string;
  reason: string;
}

/** `/api/kinds`: the kinds, and what can't be used. */
export interface Kinds {
  found: Kind[];
  broken: Broken[];
}

/** `/api/where`: the store file the page server reads and writes, the git a create would
 * run there, null when it finds none, and `note`, the line a change would be refused with
 * now (the page server would write elsewhere than the user's terminal, or can't tell),
 * null when a change would go ahead. */
export interface Where {
  store: string;
  git: string | null;
  note: string | null;
}

/** An add's or a set's answer: the project as it now stands, and notes that refuse
 * nothing. */
export interface Written {
  project: Project;
  notes: string[];
}

/** `/api/add`'s value: a write, and the warnings for what the add went ahead without (a
 * contribution skipped since it won't load, with the fix). */
export interface Added extends Written {
  warnings: string[];
}

/** `/api/remove-status`: what a remove request came to. */
export interface RemoveStatus {
  job: string;
  project: string;
  created: string;
  state: "removed" | "refused";
  key: string;
  reason: string | null;
  fix: string | null;
  sentences: string[];
  exit_code: number | null;
}

/** One thing a remove would be refused with now: projects' own (the associated agents) or a
 * contribution's sentence, which carries its own fix; `fix` is set only for a
 * contribution that won't load, whose `sentence` is the reason. */
export interface Refusal {
  module: string;
  sentence: string;
  fix: string | null;
}

/** `/api/remove-check`: the project's key as stored, and what a remove would be refused
 * with now, empty when nothing would. */
export interface RemoveCheck {
  key: string;
  refusals: Refusal[];
}

/**
 * The event every projects component listens for on the document, and reads its route
 * again: one of projects' acts has ended, so the list may have changed. The add form and
 * the page send it; it carries no detail.
 */
export const CHANGED = "moxi-projects-changed";

/**
 * The event the picker sends its host when the user chooses a project: bubbling and
 * composed, so it leaves the picker's shadow root. Its detail is `{key}` and nothing
 * else.
 */
export const PICKED = "moxi-change";

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

const MINTS: ReadonlySet<string> = new Set(["git", "dir", "refuse"]);

const isObj = (value: unknown): value is Obj =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isStr = (value: unknown): value is string => typeof value === "string";
const isStrOrNull = (value: unknown): value is string | null => value === null || isStr(value);
const isInt = (value: unknown): value is number => Number.isInteger(value);
const isStrings = (value: unknown): value is string[] => Array.isArray(value) && value.every(isStr);
const isListOf = <T>(value: unknown, check: (item: unknown) => item is T): value is T[] =>
  Array.isArray(value) && value.every(check);

export function isProject(value: unknown): value is Project {
  return isObj(value) && isStr(value.key) && isStr(value.root) && isStr(value.kind)
    && isStrOrNull(value.lead) && isStrings(value.associated) && isStrings(value.agents)
    && isStrOrNull(value.notes_root) && isStr(value.created);
}

function isKind(value: unknown): value is Kind {
  return isObj(value) && isStr(value.name) && isStr(value.doc) && isStr(value.module)
    && isStr(value.mint) && MINTS.has(value.mint);
}

function isBroken(value: unknown): value is Broken {
  return isObj(value) && isStr(value.module) && isStr(value.reason);
}

const project = (value: Project): Project => ({
  key: value.key, root: value.root, kind: value.kind, lead: value.lead,
  associated: [...value.associated], agents: [...value.agents], notes_root: value.notes_root,
  created: value.created,
});

/** `/api/projects`'s value, or null when it isn't one. */
export function parseProjects(data: unknown): Projects | null {
  if (!isObj(data) || !isListOf(data.projects, isProject)) return null;
  return { projects: data.projects.map(project) };
}

const isMaybeBool = (value: unknown): value is boolean | null => value === null || typeof value === "boolean";

/** `/api/projects`' value with the page's fields, or null when it isn't one. */
export function parseListing(data: unknown): Listing | null {
  if (!isObj(data) || !Array.isArray(data.projects) || !isStrOrNull(data.home)
      || typeof data.notes_installed !== "boolean") return null;
  const projects: Listed[] = [];
  for (const item of data.projects) {
    if (!isObj(item) || !isProject(item) || !isMaybeBool(item.exists)) return null;
    projects.push({ ...project(item), exists: item.exists });
  }
  return { projects, home: data.home, notes_installed: data.notes_installed };
}

/** `/api/project`'s value, or null when it isn't one. */
export function parseOne(data: unknown): One | null {
  if (!isObj(data) || !isProject(data.project)) return null;
  return { project: project(data.project) };
}

/** `/api/kinds`'s value, or null when it isn't one. */
export function parseKinds(data: unknown): Kinds | null {
  if (!isObj(data) || !isListOf(data.found, isKind) || !isListOf(data.broken, isBroken)) return null;
  return {
    found: data.found.map(({ name, doc, module, mint }) => ({ name, doc, module, mint })),
    broken: data.broken.map(({ module, reason }) => ({ module, reason })),
  };
}

/** `/api/where`'s value, or null when it isn't one. */
export function parseWhere(data: unknown): Where | null {
  if (!isObj(data) || !isStr(data.store) || !isStrOrNull(data.git) || !isStrOrNull(data.note)) {
    return null;
  }
  return { store: data.store, git: data.git, note: data.note };
}

/** An add's or a set's answer, or null when it isn't one. */
export function parseWritten(data: unknown): Written | null {
  if (!isObj(data) || !isProject(data.project) || !isStrings(data.notes)) return null;
  return { project: project(data.project), notes: [...data.notes] };
}

/** `/api/add`'s value, or null when it isn't one. */
export function parseAdded(data: unknown): Added | null {
  const written = parseWritten(data);
  if (written === null || !isObj(data) || !isStrings(data.warnings)) return null;
  return { ...written, warnings: [...data.warnings] };
}

/** `/api/remove-status`'s value, or null when it isn't one. */
export function parseRemoveStatus(data: unknown): RemoveStatus | null {
  if (!isObj(data) || !isStr(data.job) || !isStr(data.project) || !isStr(data.created)
      || (data.state !== "removed" && data.state !== "refused") || !isStr(data.key)
      || !isStrOrNull(data.reason) || !isStrOrNull(data.fix) || !isStrings(data.sentences)
      || (data.exit_code !== null && !isInt(data.exit_code))) return null;
  return { job: data.job, project: data.project, created: data.created, state: data.state,
           key: data.key, reason: data.reason, fix: data.fix, sentences: [...data.sentences],
           exit_code: data.exit_code };
}

/** `/api/remove-check`'s value, or null when it isn't one. */
export function parseRemoveCheck(data: unknown): RemoveCheck | null {
  if (!isObj(data) || !isStr(data.key) || !Array.isArray(data.refusals)) return null;
  const refusals: Refusal[] = [];
  for (const one of data.refusals) {
    if (!isObj(one) || !isStr(one.module) || !isStr(one.sentence) || !isStrOrNull(one.fix)) return null;
    refusals.push({ module: one.module, sentence: one.sentence, fix: one.fix });
  }
  return { key: data.key, refusals };
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
  if (status === 200) return "projects' page server sent an answer that can't be read";
  return `projects' page server answered ${status}`;
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
 *   isn't the route's shape) is a failure, and the last value is kept. A 422 is the
 *   route's refusal, and `said` holds its reason and fix.
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
