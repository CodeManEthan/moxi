// How services' page and summary block read their data: `/api/rows` first, then each
// event of the topic `rows`, which carries the same value; the projects list for the add
// form; and an act's status route while its job runs. Every answer that isn't a readable
// 200 maps to a state. The shapes are the routes' own, as services' page server declares
// them. No DOM and no Lit here, so the Node tests drive it with a stubbed `read`.
//
// On the phone the gateway sends every text the page server marks `redact` as null (a
// verdict's reason and fix, the supervisor's detail, a command line, an act's lines), and
// every path as a handle. So each of those reads as a string or null here.

import type { ApiResult } from "/m/ui/client.js";

/** A verdict's states, best first. */
export const VERDICTS = ["ok", "warn", "fail", "off"] as const;
export type VerdictState = (typeof VERDICTS)[number];

/** A row's verdict. `unknown` is true for a `warn` given because a value couldn't be
 * read. */
export interface Verdict {
  state: VerdictState;
  reason: string | null;
  fix: string | null;
  unknown: boolean;
}

/** A timer's last run: a few words (`ok`, `exit 1`, ...) and when. */
export interface LastRun {
  failed: boolean;
  text: string | null;
  at: string | null;
}

/** A running daemon's last health check: its state and its first line. */
export interface Checked {
  state: string;
  line: string | null;
}

/** One row, as `/api/rows` gives it. */
export interface Row {
  /** `<module>/<name>`; a user row's module is `services`. */
  id: string;
  module: string;
  name: string;
  form: "module" | "written" | "adopted";
  kind: "daemon" | "timer";
  doc: string | null;
  project: string | null;
  launches_session: boolean;
  /** Its on/off, null when it can't be read. */
  flag: boolean | null;
  /** The settings key that is its on/off; null for the store's own flag. */
  switch: string | null;
  /** False for a module unit that declares no switch: it can be stopped, not turned off. */
  has_switch: boolean;
  verdict: Verdict;
  /** The supervisor's word, null when it wasn't read. */
  state: string | null;
  enabled: boolean | null;
  pid: number | null;
  /** UTC, ISO 8601 with `Z`. */
  since: string | null;
  detail: string | null;
  last_run: LastRun | null;
  checked: Checked | null;
  argv: (string | null)[] | null;
  cwd: string | null;
  unit: string | null;
  schedule: string | null;
  health: (string | null)[] | null;
  /** When a daemon was stopped by hand, UTC. */
  stopped: string | null;
  removing: boolean;
  platform: string | null;
}

/** A module whose run-time declarations failed. */
export interface Problem {
  module: string;
  reason: string | null;
}

/** `/api/rows`, and each event of the topic `rows`. */
export interface Rows {
  /** `up`, or `error` when the store can't be read; `detail` says why. */
  state: "up" | "error";
  detail: string | null;
  rows: Row[];
  problems: Problem[];
}

export interface Project {
  key: string;
  root: string;
}

/** `/api/projects`: the projects a row can name, or why there are none. */
export interface Projects {
  projects: Project[];
  reason: string | null;
}

export const JOB_STATES = ["running", "done", "failed", "refused", "unknown"] as const;
export type JobState = (typeof JOB_STATES)[number];

/** `/api/status`: what an act came to. */
export interface Status {
  job: string;
  act: string | null;
  id: string | null;
  state: JobState;
  /** The verb's exit code once the job has ended. */
  code: number | null;
  lines: (string | null)[];
  fix: string | null;
}

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

const FORMS: ReadonlySet<string> = new Set(["module", "written", "adopted"]);
const KINDS: ReadonlySet<string> = new Set(["daemon", "timer"]);
const VERDICT_SET: ReadonlySet<string> = new Set(VERDICTS);
const JOB_SET: ReadonlySet<string> = new Set(JOB_STATES);

const isObj = (value: unknown): value is Obj =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isStr = (value: unknown): value is string => typeof value === "string";
const isStrOrNull = (value: unknown): value is string | null => value === null || isStr(value);
const isBool = (value: unknown): value is boolean => typeof value === "boolean";
const isBoolOrNull = (value: unknown): value is boolean | null => value === null || isBool(value);
const isIntOrNull = (value: unknown): value is number | null => value === null || Number.isInteger(value);
const isWords = (value: unknown): value is (string | null)[] | null =>
  value === null || (Array.isArray(value) && value.every(isStrOrNull));

function verdictOf(value: unknown): Verdict | null {
  if (!isObj(value) || !isStr(value.state) || !VERDICT_SET.has(value.state)
      || !isStrOrNull(value.reason) || !isStrOrNull(value.fix) || !isBool(value.unknown)) return null;
  return { state: value.state as VerdictState, reason: value.reason, fix: value.fix, unknown: value.unknown };
}

function lastRunOf(value: unknown): LastRun | null | undefined {
  if (value === null) return null;
  if (!isObj(value) || !isBool(value.failed) || !isStrOrNull(value.text) || !isStrOrNull(value.at)) return undefined;
  return { failed: value.failed, text: value.text, at: value.at };
}

function checkedOf(value: unknown): Checked | null | undefined {
  if (value === null) return null;
  if (!isObj(value) || !isStr(value.state) || !isStrOrNull(value.line)) return undefined;
  return { state: value.state, line: value.line };
}

/** One row, or null when it isn't one. */
export function parseRow(value: unknown): Row | null {
  if (!isObj(value)) return null;
  const v = value;
  const verdict = verdictOf(v.verdict);
  const last = lastRunOf(v.last_run);
  const checked = checkedOf(v.checked);
  if (!isStr(v.id) || !isStr(v.module) || !isStr(v.name) || !isStr(v.form) || !FORMS.has(v.form)
      || !isStr(v.kind) || !KINDS.has(v.kind) || !isStrOrNull(v.doc) || !isStrOrNull(v.project)
      || !isBool(v.launches_session) || !isBoolOrNull(v.flag) || !isStrOrNull(v.switch)
      || !isBool(v.has_switch) || verdict === null || !isStrOrNull(v.state) || !isBoolOrNull(v.enabled)
      || !isIntOrNull(v.pid) || !isStrOrNull(v.since) || !isStrOrNull(v.detail) || last === undefined
      || checked === undefined || !isWords(v.argv) || !isStrOrNull(v.cwd) || !isStrOrNull(v.unit)
      || !isStrOrNull(v.schedule) || !isWords(v.health) || !isStrOrNull(v.stopped)
      || !isBool(v.removing) || !isStrOrNull(v.platform)) return null;
  return {
    id: v.id, module: v.module, name: v.name, form: v.form as Row["form"], kind: v.kind as Row["kind"],
    doc: v.doc, project: v.project, launches_session: v.launches_session, flag: v.flag,
    switch: v.switch, has_switch: v.has_switch, verdict, state: v.state, enabled: v.enabled,
    pid: v.pid, since: v.since, detail: v.detail, last_run: last, checked,
    argv: v.argv === null ? null : [...v.argv], cwd: v.cwd, unit: v.unit, schedule: v.schedule,
    health: v.health === null ? null : [...v.health], stopped: v.stopped, removing: v.removing,
    platform: v.platform,
  };
}

/** `/api/rows`' value, or a `rows` event's, or null when it isn't one. A row that can't
 * be read makes the whole value unreadable, so no row is ever left out unseen. */
export function parseRows(data: unknown): Rows | null {
  if (!isObj(data) || (data.state !== "up" && data.state !== "error") || !isStrOrNull(data.detail)
      || !Array.isArray(data.rows) || !Array.isArray(data.problems)) return null;
  const rows: Row[] = [];
  for (const item of data.rows) {
    const row = parseRow(item);
    if (row === null) return null;
    rows.push(row);
  }
  const problems: Problem[] = [];
  for (const item of data.problems) {
    if (!isObj(item) || !isStr(item.module) || !isStrOrNull(item.reason)) return null;
    problems.push({ module: item.module, reason: item.reason });
  }
  return { state: data.state, detail: data.detail, rows, problems };
}

/** `/api/projects`' value, or null when it isn't one. */
export function parseProjects(data: unknown): Projects | null {
  if (!isObj(data) || !Array.isArray(data.projects) || !isStrOrNull(data.reason)) return null;
  const projects: Project[] = [];
  for (const item of data.projects) {
    if (!isObj(item) || !isStr(item.key) || !isStr(item.root)) return null;
    projects.push({ key: item.key, root: item.root });
  }
  return { projects, reason: data.reason };
}

/** `/api/status`' value, or null when it isn't one. */
export function parseStatus(data: unknown): Status | null {
  if (!isObj(data) || !isStr(data.job) || !isStrOrNull(data.act) || !isStrOrNull(data.id)
      || !isStr(data.state) || !JOB_SET.has(data.state) || !isIntOrNull(data.code)
      || !Array.isArray(data.lines) || !data.lines.every(isStrOrNull) || !isStrOrNull(data.fix)) return null;
  return { job: data.job, act: data.act, id: data.id, state: data.state as JobState, code: data.code,
           lines: [...data.lines], fix: data.fix };
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

/** The status path an act's 202 or `confirm()` gave, relative to services' page
 * (`api/status?job=...`), or null when it isn't one. */
export function statusPathOf(status: unknown): string | null {
  if (!isStr(status)) return null;
  const match = /^\/?(api\/status\?job=[A-Za-z0-9_-]{1,64})$/.exec(status);
  return match ? match[1] : null;
}

// -- reading ------------------------------------------------------------------------------

/** The line a failure shows. */
export function failureWhy(status: number): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "services' page server sent an answer that can't be read";
  return `services' page server answered ${status}`;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/**
 * One route's data, kept current. `refresh()` reads the route; `topic()` takes a topic
 * event that carries the same value. `onChange` is called after every read and event.
 *
 * - A 200 with a readable body gives the value.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means the module went away while the page was open: `gone`.
 * - Every other answer (any other status, a network failure, a body that isn't JSON or
 *   isn't the route's shape) is a failure, and the last value is kept. A 422 is the
 *   route's refusal, and `said` holds its reason and fix.
 * - A topic event replaces the value at once while the last read succeeded; otherwise,
 *   or when the event can't be read, it reads the route again.
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

  /** Stops every change: a later answer or event is dropped. */
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

  /** One topic event. */
  topic(data: unknown): void {
    if (this.#closed) return;
    const value = this.#parse(data);
    if (value === null || this.loading || this.failure !== null || this.gone) {
      void this.refresh();
      return;
    }
    this.value = value;
    this.#onChange();
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
      this.failure = { status: answer.status, why: failureWhy(answer.status), said: saidOf(answer.data) };
    }
  }
}
