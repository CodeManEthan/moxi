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
  /** The path of the user's own git hook that runs after the pieces (or alone while each
   * is off), or null. */
  existing_hook: string | null;
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
  /** Its kind: `gone`, `unreadable`, `no-module`, `shim-moved` and the rest; null when
   * the listing names none. */
  code: string | null;
  /** The piece and the event it is about, when it is about one. */
  piece: string | null;
  event: string | null;
  /** Its fix is a refresh of this repo. */
  refresh: boolean;
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
  error_since: number | null;
  error_passes: number | null;
  index_busy_since: number | null;
  /** git's index lock has been held long enough for its commit record to name it. */
  index_stale: boolean;
  last_commit: string | null;
  last_commit_time: number | null;
  remote: string | null;
  pushed: { branch: string; sha: string }[];
  push_failed: { branch: string; error: string | null; failed_at: number | null }[];
  /** Each branch with commits its remote lacks; `ahead` null when it was never pushed. */
  unpushed: { branch: string; ahead: number | null }[];
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
  /** In repo's list; false for a project's repo that holds pieces but isn't. */
  indexed: boolean;
  git_version: string | null;
  mode: string | null;
  pinned: boolean;
  events: EventRow[];
  /** The events with no piece that have a git hook of the user's own. */
  own_hooks: string[];
  pieces: PieceRow[];
  problems: ProblemRow[];
  specs: SpecRow[];
  specs_problem: string | null;
  records: RecordRow[];
  /** The lines `moxi repo rules` prints under its heading, less the commit-proof line;
   * null when they can't be built. */
  rules: string[] | null;
  /** The commit-proof piece is registered here. */
  proof: boolean;
  commands: Command[];
}

/** A piece an installed module declares. */
export interface Offered {
  id: string;
  module: string;
  /** The part of `id` after `/`. */
  name: string;
  event: string;
  fail: string;
  order: number;
  doc: string;
  settings: string[];
}

/** A project whose repo has no piece of repo's and isn't in its list. */
export interface Unindexed {
  project: string;
  root: string;
}

/** The value of `/api/repos`, without `now`. */
export interface Repos {
  /** `up`, or `error` when the page server's listing failed. */
  state: "up" | "error";
  detail: string | null;
  /** The page server's date, `YYYY-MM-DD`; null when the answer has none. */
  today: string | null;
  /** The page server's home folder; a path under it is written in `~` form. Null when
   * nothing places one. */
  home: string | null;
  repos: RepoRow[];
  offered: Offered[];
  /** Declarations that can't be offered, and why. */
  refused: { id: string; why: string }[];
  /** Null without projects. */
  unindexed: Unindexed[] | null;
}

/** One item an act would write. `place` is `config` (a git config key of repo's, its
 * value before and after; `to` null on a removed key or a `seed` drawn as it is written),
 * `hooks` (`key` `shim`, `hook` or `launcher`: a file placed, moved or taken away),
 * `chain` (a line the user adds to another tool's hook: `from` the hook, `to` the line) or
 * `index` (the repo joins repo's list: `to` its root, `from` its root there before). */
export interface WriteItem {
  place: string;
  piece: string | null;
  event: string | null;
  key: string | null;
  from: string | null;
  to: string | null;
  seed: boolean;
}

/** A piece an act would register or rewrite. */
export interface PlannedPiece {
  id: string;
  event: string | null;
  new: boolean;
  /** The `everywhere` key that brings it in, when it wasn't named. */
  implied_by: string | null;
  /** The settings it would seed. */
  seeds: string[];
}

/** What an act would write in one repo: `/api/plan`'s `plan`, and a `changed` answer's. */
export interface Plan {
  root: string;
  name: string;
  is_repo: boolean;
  git_version: string | null;
  /** `config` or `shim` after the act. */
  mode: string | null;
  indexed: boolean;
  pieces: PlannedPiece[];
  mode_change: { from: string; to: string } | null;
  own_hooks: string[];
  /** `merge` or `rebase` when one is under way. */
  in_progress: string | null;
  writes: WriteItem[];
  /** What the act would refuse, with no `writes` and no `digest`. */
  refusal: { code: string | null; text: string } | null;
  digest: string | null;
}

/** `/api/plan`'s value: `up` with the plan, or `error` with `detail` and no plan. */
export interface Planned {
  state: "up" | "error";
  detail: string | null;
  plan: Plan | null;
}

/** A line launcher mode needs in another tool's hook, from an act's report. */
export interface ChainLine {
  event: string;
  hook: string;
  line: string;
  present: boolean;
}

/** An act's answer. */
export interface Answer {
  /** `done`, `nothing`, `changed`, `refused`, `partial` or `failed`. */
  outcome: string;
  lines: string[];
  problems: string[];
  chain: ChainLine[];
  /** The new plan, with `changed`. */
  plan: Plan | null;
  refusal: { code: string | null; text: string } | null;
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
  if (!isList(data.own_hooks) || !data.own_hooks.every((event) => typeof event === "string")) return null;
  return data as unknown as RepoRow;
}

/** A list of rows under `key`, `[]` when the answer has none, or null when it isn't one. */
function rowsOf(data: Record<string, unknown>, key: string): Record<string, unknown>[] | null {
  const value = data[key];
  if (value === undefined) return [];
  return isList(value) && value.every(isRow) ? value : null;
}

/** The route's value, or null when it isn't one. */
export function parseRepos(data: unknown): Repos | null {
  if (!isRow(data)) return null;
  const { state, detail, repos } = data;
  if (detail !== null && detail !== undefined && typeof detail !== "string") return null;
  const today = typeof data.today === "string" ? data.today : null;
  const home = typeof data.home === "string" && data.home !== "" ? data.home : null;
  const offered = rowsOf(data, "offered");
  const refused = rowsOf(data, "refused");
  const unindexed = data.unindexed === null || data.unindexed === undefined ? null : rowsOf(data, "unindexed");
  if (offered === null || refused === null || (data.unindexed != null && unindexed === null)) return null;
  const rest = { today, home, offered: offered as unknown as Offered[], refused: refused as Repos["refused"],
                 unindexed: unindexed as Unindexed[] | null };
  if (state === "error") return { state, detail: detail ?? null, repos: [], ...rest };
  if (state !== "up" || !isList(repos)) return null;
  const rows = repos.map(parseRepo);
  if (rows.some((row) => row === null)) return null;
  return { state, detail: null, repos: rows as RepoRow[], ...rest };
}

const isTexts = (value: unknown): value is string[] => isList(value) && value.every((v) => typeof v === "string");

/** A plan, or null when it isn't one: only what the page reads before drawing is checked. */
export function parsePlan(data: unknown): Plan | null {
  if (!isRow(data) || typeof data.root !== "string") return null;
  for (const key of ["pieces", "writes"]) {
    if (!isList(data[key]) || !(data[key] as unknown[]).every(isRow)) return null;
  }
  if (!isTexts(data.own_hooks)) return null;
  if (data.refusal != null && !(isRow(data.refusal) && typeof data.refusal.text === "string")) return null;
  return data as unknown as Plan;
}

/** `/api/plan`'s value, or null when it isn't one. */
export function parsePlanned(data: unknown): Planned | null {
  if (!isRow(data)) return null;
  if (data.state === "error") {
    return typeof data.detail === "string" ? { state: "error", detail: data.detail, plan: null } : null;
  }
  const plan = parsePlan(data.plan);
  return data.state === "up" && plan !== null ? { state: "up", detail: null, plan } : null;
}

const OUTCOMES = new Set(["done", "nothing", "changed", "refused", "partial", "failed"]);

/** An act's answer, or null when it isn't one. */
export function parseAnswer(data: unknown): Answer | null {
  if (!isRow(data) || typeof data.outcome !== "string" || !OUTCOMES.has(data.outcome)) return null;
  if (!isTexts(data.lines) || !isTexts(data.problems) || !isList(data.chain) || !data.chain.every(isRow)) return null;
  const plan = data.plan === null || data.plan === undefined ? null : parsePlan(data.plan);
  if (data.outcome === "changed" && plan === null) return null;
  const refusal = data.refusal ?? null;
  if (refusal !== null && !(isRow(refusal) && typeof refusal.text === "string")) return null;
  return { ...(data as unknown as Answer), plan, refusal: refusal as Answer["refusal"] };
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
