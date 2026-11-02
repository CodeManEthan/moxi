// How repo's page reads its data: the one route, read on demand, with every answer that
// isn't a readable 200 mapped to a state. No DOM and no Lit here, so the Node tests drive
// it with a stubbed `read` and clock.

import type { ApiResult } from "/m/ui/client.js";

/** A command line the page offers to copy; the page runs none. */
export interface Command {
  label: string;
  command: string;
}

export interface EventRow {
  event: string;
  /** `config entry`, `shim` or `launcher`; null when every piece of it is disabled. */
  via: string | null;
  pieces: string[];
  existing: string | null;
  /** In launcher mode, `present` or `needed`; else null. */
  chain_line: string | null;
  hook: string | null;
  line: string | null;
}

export interface PieceRow {
  id: string;
  event: string | null;
  enabled: boolean;
  fail: string;
  order: number;
  settings: { key: string; values: string[] }[];
  declared: boolean;
  recorded: boolean;
  toggle: Command;
  remove: Command;
}

export interface ProblemRow {
  /** `fail`, `warn` or `ok`. */
  state: string;
  detail: string;
  fix: string | null;
}

export interface SpecRow {
  name: string;
  saved: boolean;
  problem: string | null;
  /** The last pass's status, null when none has run. */
  status: string | null;
  line: string | null;
  time: number | null;
  refused_since: number | null;
  refused_passes: number | null;
  last_commit: string | null;
  last_commit_time: number | null;
  remote: string | null;
  pushed: { branch: string; sha: string }[];
  push_failed: { branch: string; error: string | null; failed_at: number | null }[];
  run: Command;
  forget: Command;
}

export interface RecordRow {
  kind: string;
  piece: string | null;
  spec: string | null;
  branch: string | null;
  text: string;
  fix: string | null;
  at: string | null;
}

export interface RepoRow {
  root: string;
  project: string | null;
  git_version: string | null;
  mode: string | null;
  pinned: boolean;
  events: EventRow[];
  pieces: PieceRow[];
  problems: ProblemRow[];
  specs: SpecRow[];
  specs_problem: string | null;
  records: RecordRow[];
  commands: Command[];
}

/** The value of `/api/repos`, without `now`. */
export interface Repos {
  /** `up`, or `error` when the page server's listing failed. */
  state: "up" | "error";
  detail: string | null;
  repos: RepoRow[];
}

/** A request that gave no readable value. */
export interface Failure {
  /** The answer's status: 0 when no answer came, 200 for a body that can't be read. */
  status: number;
  /** One line saying what went wrong. */
  why: string;
}

/** How many times a 503 (the page server's in-flight cap) is read again, 1 s apart. */
export const RETRIES = 3;
export const RETRY_MS = 1000;

const isList = (value: unknown): value is unknown[] => Array.isArray(value);
const isRow = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** One repo, or null when it isn't one: the server declares the shape, so only what the
 * page reads before drawing is checked. */
function parseRepo(data: unknown): RepoRow | null {
  if (!isRow(data) || typeof data.root !== "string") return null;
  for (const key of ["events", "pieces", "problems", "specs", "records", "commands"]) {
    if (!isList(data[key]) || !(data[key] as unknown[]).every(isRow)) return null;
  }
  return data as unknown as RepoRow;
}

/** The route's value, or null when it isn't one. */
export function parseRepos(data: unknown): Repos | null {
  if (!isRow(data)) return null;
  const { state, detail, repos } = data;
  if (detail !== null && detail !== undefined && typeof detail !== "string") return null;
  if (state === "error") return { state, detail: detail ?? null, repos: [] };
  if (state !== "up" || !isList(repos)) return null;
  const rows = repos.map(parseRepo);
  if (rows.some((row) => row === null)) return null;
  return { state, detail: null, repos: rows as RepoRow[] };
}

/** The line a failure shows. */
export function failureWhy(status: number): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "repo's page server sent an answer that can't be read";
  return `repo's page server answered ${status}`;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/**
 * The page's data. `refresh()` reads the route; `onChange` is called after every read.
 *
 * - A 200 with a readable body gives the value and the offset of the server's clock.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means the module went away while the page was open: `gone`.
 * - Every other answer (any other status, a network failure, a body that isn't JSON or
 *   isn't the route's shape) is a failure, and the last value is kept.
 */
export class Feed {
  /** The last value read, or null before the first. */
  value: Repos | null = null;
  /** The last request's failure, null once a read succeeds. */
  failure: Failure | null = null;
  /** The route answered 404. */
  gone = false;
  /** No read has finished yet. */
  loading = true;
  /** A read is running. */
  reading = false;
  /** The server's clock minus this browser's, in seconds; null before the first read. */
  offset: number | null = null;

  #read: () => Promise<ApiResult>;
  #onChange: () => void;
  #wait: Wait;
  #clock: () => number;
  #running: Promise<void> | null = null;
  #again = false;
  #closed = false;

  constructor(read: () => Promise<ApiResult>, onChange: () => void,
              { wait = sleep, clock = () => Date.now() / 1000 }: { wait?: Wait; clock?: () => number } = {}) {
    this.#read = read;
    this.#onChange = onChange;
    this.#wait = wait;
    this.#clock = clock;
  }

  /** The server's time now, in epoch seconds, or null before the first read. */
  now(): number | null {
    return this.offset === null ? null : this.#clock() + this.offset;
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
    const at = this.#clock();
    this.loading = false;
    const data = answer.data as Record<string, unknown> | null;
    const value = answer.status === 200 ? parseRepos(data) : null;
    const now = data?.now;
    if (value !== null && typeof now === "number" && Number.isFinite(now)) {
      this.value = value;
      this.offset = now - at;
      this.failure = null;
      this.gone = false;
    } else if (answer.status === 404) {
      this.gone = true;
      this.failure = null;
    } else {
      this.failure = { status: answer.status, why: failureWhy(answer.status) };
    }
  }
}
