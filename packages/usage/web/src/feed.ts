// How usage's page and blocks read their data: a route first, then a topic where there is
// one, with every answer that isn't a readable 200 mapped to a state. No DOM and no Lit
// here, so the Node tests drive it with a stubbed `read` and clock.

import type { ApiResult } from "/m/ui/client.js";

/** The sums every report row and every report's `total` carry. */
export interface Sums {
  requests: number;
  input: number;
  fresh_input: number;
  cache_read: number;
  cache_write: number;
  cache_write_1h: number;
  output: number;
  tokens: number;
  unpriced_tokens: number;
  unknown_rows: number;
  /** Dollars; null when nothing in the row could be priced. */
  cost: number | null;
  complete: boolean;
  /** The harness's own cost figure, where it wrote one. */
  harness_cost: number | null;
  /** The names of the values the index could not say. */
  unknown: string[];
}

/** A report row's sums, with what kept its cost from being whole. */
export interface RowSums extends Sums {
  unpriced_models: string[];
  rules: string[];
}

/** The index as a read found it. */
export interface IndexFacts {
  passed: boolean;
  last_pass: number | null;
  note: string | null;
}

/** A row of `/api/daily` and `/api/cost`. */
export interface DayRow extends RowSums {
  period: string | null;
  model: string | null;
}

/** The whole prompt of a session's newest request, and the window it ran against (null
 * when neither the harness nor the model table gives one). */
export interface Context {
  tokens: number;
  window: number | null;
}

/** What names one session, wherever an answer names one. The id is for addresses and
 * requests; a session is shown by its name, folder and start. */
export interface SessionRef {
  harness: string;
  /** Null when the row has no session view. */
  session_id: string | null;
  /** The name typed for the session; null when it has none. */
  name: string | null;
  /** The folder of the session's record, else the one the index stored. */
  folder: string | null;
  /** When the session started, epoch seconds. With `known` false it is the session's
   * earliest request in the index; with `known` null it is null. */
  started: number | null;
  /** True when the session's record was found, false when it is gone, null when it
   * was not looked for. */
  known: boolean | null;
}

/** A row of `/api/sessions`. */
export interface SessionRow extends RowSums, SessionRef {
  cwd: string | null;
  project: string | null;
  models: string[];
  first_ts: number | null;
  last_ts: number | null;
  /** The newest request's whole prompt against its window; null when it was not read. */
  context: Context | null;
}

/** A row of `/api/session`: one agent's use of one model. */
export interface AgentRow extends RowSums {
  agent: string;
  kind: string;
  title: string | null;
  model: string | null;
  first_ts: number | null;
  last_ts: number | null;
}

/** A row of `/api/projects`. */
export interface ProjectRow extends RowSums {
  project: string | null;
  cwd: string | null;
  models: string[];
}

/** Where one of a model's prices came from. */
export interface Provenance {
  layer: string | null;
  step: number;
  rule: string;
  row: string | null;
  snapshot: string | null;
  overridden: string[];
  period: { from: string | null; to: string | null } | null;
  note: string | null;
  filled: string[];
  variant: string | null;
}

/** A row of `/api/models`. */
export interface ModelRow extends RowSums {
  model: string | null;
  provenance: Provenance[];
}

/** One term of a price group's cost: a token class's tokens, the price they paid
 * (dollars per million, null when unknown) and the dollars (null when unpriced). */
export interface Term {
  class: string;
  tokens: number;
  price: number | null;
  cost: number | null;
  /** `row`, `tier` or `tier-input`; null for a model the table doesn't know. */
  price_from: string | null;
  unknown: string[];
}

/** One price group of a cost-terms row: a line of the Reconcile table. */
export interface Group {
  model: string | null;
  /** The requests' own provider, null where the harness gives none. */
  provider: string | null;
  tier: string | null;
  above: number;
  period: { from: string | null; to: string | null } | null;
  provenance: Provenance;
  requests: number;
  terms: Term[];
  cost: number | null;
  unpriced_tokens: number;
  unknown_rows: number;
  rule: string | null;
  why: string | null;
  unknown: string[];
}

/** A cost's dollars for one token class under one price tier (null: the row's prices). */
export interface SplitItem {
  class: string;
  tier: string | null;
  tokens: number;
  cost: number;
}

/** A row of `/api/terms`: its key is `model`, `period` or `agent`, as `by` says. */
export interface TermsRow extends RowSums {
  model: string | null;
  period: string | null;
  agent: string | null;
  kind: string | null;
  groups: Group[];
  split: SplitItem[];
}

/** What caching saved over a terms answer, in dollars but for the two token counts:
 * the cache reads at the input price, less what they cost and what the writes cost
 * over the input price. */
export interface Saving {
  input_tokens: number;
  read_tokens: number;
  reads_at_input: number;
  reads_cost: number;
  write_extra: number;
  /** Zero or less when caching did not pay. */
  saved: number;
}

/** `/api/terms`' total. */
export interface TermsTotal extends Sums {
  split: SplitItem[];
  /** Null with no cache read, or with a group holding cache tokens that has no price. */
  saving: Saving | null;
}

/** A row of `/api/requests`: one request, `n` its place on the request axis. */
export interface RequestRow {
  n: number;
  ts: number | null;
  model: string | null;
  tier: string | null;
  turn: number | null;
  turn_start: boolean;
  /** The whole prompt; null when unknown. */
  context: number | null;
  window: number | null;
  fresh_input: number;
  cache_read: number;
  cache_write_5m: number;
  cache_write_1h: number;
  output: number;
  unknown: string[];
  synthetic: boolean;
  cost: number | null;
  split: { class: string; cost: number | null }[];
}

/** A compaction, between request `after` and the next one. */
export interface Mark {
  after: number;
  ts: number | null;
  trigger: string | null;
  pre_tokens: number | null;
}

/** `/api/requests`' total: the sums, the compactions, and what it can't show. */
export interface RequestsTotal extends Sums {
  marks: Mark[];
  turns: number | null;
  claimed_elsewhere: number;
  copied_marks: number;
  marks_known: boolean;
}

/** A price set: dollars per million tokens, null where unknown. */
export interface PriceSet {
  input: number | null;
  output: number | null;
  cache_read: number | null;
  cache_write_5m: number | null;
  cache_write_1h: number | null;
}

/** A tier's conditions. */
export interface Conditions {
  above_tokens: number | null;
  service_tier: string | null;
  speed: string | null;
}

/** One model's rates now, from `/api/prices`. */
export interface Rate extends PriceSet {
  model: string | null;
  asked_provider: string | null;
  window: number | null;
  max_output: number | null;
  provider: string | null;
  tiers: { name: string; conditions: Conditions; prices: PriceSet; from_tier_input: string[]; from_row: string[] }[];
  provenance: Provenance;
  unknown: string[];
}

/** One `[[model]]` row of usage's config. */
export interface Override {
  id: string;
  match: string;
  prices: PriceSet;
  window: number | null;
  max_output: number | null;
  tiers: { name: string; conditions: Conditions; prices: PriceSet }[];
  provider: string | null;
  from: string | null;
  to: string | null;
  note: string | null;
}

/** `/api/prices`. */
export interface Prices {
  effective_date: string | null;
  rows: number;
  layers: { layer: string; date: string | null; rows: number }[];
  overrides: Override[];
  problems: string[];
  models: Rate[];
  matched: number;
}

/** Every report route's answer: the rows, their total and the index's facts. */
export interface Report<R, T extends Sums = Sums> {
  since: number | null;
  until: number | null;
  tz: string;
  filters: { name: string; value: string }[];
  rows: R[];
  total: T;
  unpriced_models: string[];
  rules: string[];
  index: IndexFacts;
  /** `/api/daily`'s, `/api/terms`' and `/api/projects`' `by`. */
  by?: string;
  /** `/api/sessions`' paging: the most rows asked, those skipped, all the range holds
   * and those after the rows sent. */
  top?: number;
  skip?: number;
  count?: number;
  more?: number;
  /** The page server's day, `YYYY-MM-DD` in its own zone. */
  today?: string;
  /** `/api/projects`' home folder; null when it cannot be said. */
  home?: string | null;
  /** `/api/session`'s context, as a sessions row carries it. */
  context?: Context | null;
  /** `/api/session`'s session; null when no one harness holds a session of that id. */
  ref?: SessionRef | null;
}

/** One transcript lane of `/api/status`. */
export interface Lane {
  lane: string;
  harness: string | null;
  /** `online`, `offline`, `held` or `absent`; null when no pass recorded it. */
  state: string | null;
  reason: string | null;
  root: string | null;
  files: number;
  held: number;
  /** Each reason files were held back for, with how many. */
  held_reasons: { name: string; value: number }[];
  requests: number;
}

/** `/api/status`: the lanes and the index's totals. */
export interface Status {
  /** The page server's day, `YYYY-MM-DD`. */
  today?: string;
  lanes: Lane[];
  total: {
    files: number;
    held: number;
    requests: number;
    held_rows: { lane: string; path: string; part: string; reason: string }[];
    last_pass: number | null;
    /** 0 when there is no index. */
    index_version: number;
  };
  index: IndexFacts;
}

/** One limits window. */
export interface Window {
  harness: string;
  limit_id: string;
  label: string;
  state: string;
  window_minutes: number | null;
  reason: string | null;
  slot?: string | null;
  source?: string | null;
  session_id?: string | null;
  plan_type?: string | null;
  used_percent: number | null;
  /** ISO 8601 times in UTC. */
  resets_at: string | null;
  observed_at: string | null;
  full_at: string | null;
  /** When the window opened; null when the readings do not say. */
  opened?: string | null;
  projection: string | null;
  /** How many readings the fill time was fitted to. */
  fit?: number;
  note: string | null;
  dollars: { name: string; value: number }[];
  unknown: string[];
}

/** `/api/limits` without `now`, and the topic `limits`. */
export interface Limits {
  /** The page server's day, `YYYY-MM-DD`; null from an answer that carried none. */
  today: string | null;
  rows: Window[];
  pull: { tried: boolean; ok: boolean; reason: string | null; via: string | null; added: number } | null;
  notes: string[];
}

/** One reading of a plan window. */
export interface Reading {
  /** ISO 8601 in UTC. */
  at: string;
  used_percent: number | null;
}

/** A session's part of an opened window. */
export interface UsedRow extends SessionRef {
  requests: number;
  cost: number | null;
}

/** `/api/window`: one fresh plan window, its period's readings and who used it. */
export interface OpenedWindow {
  now: number;
  today: string;
  harness: string;
  limit_id: string;
  window_minutes: number;
  resets_at: string;
  used_percent: number;
  opened: string | null;
  /** `first` is null when the period's earliest reading is `last`; `last.session` is
   * the session the reading came from, null when it names none. */
  readings: { count: number; first: Reading | null;
              last: Reading & { session: SessionRef | null } } | null;
  /** Null for a limit that is not the harness's main one, with no known start, and
   * with no index to read. `rows` are the ten dearest. */
  used: { since: string; count: number; requests: number; cost: number | null; complete: boolean;
          rows: UsedRow[] } | null;
}

/** `/api/restart-point`: exactly one of `tokens` and `reason` is null. */
export interface RestartPoint {
  model: string;
  tokens: number | null;
  reason: string | null;
}

/** The topic `index`: a pass this page server started began or ended. */
export interface IndexEvent {
  running: boolean;
  last_pass: number | null;
  note: string | null;
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
const isNum = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isNumOrNull = (value: unknown): boolean => value === null || isNum(value);

/** A report's value, or null when it isn't one. The server declares the shape, so only
 * what the page reads before drawing is checked. */
export function parseReport<R, T extends Sums = Sums>(data: unknown): Report<R, T> | null {
  if (!isRow(data) || !isList(data.rows) || !data.rows.every(isRow)) return null;
  if (!isRow(data.total) || !isRow(data.index) || !isList(data.filters)) return null;
  if (!isList(data.unpriced_models) || typeof data.tz !== "string") return null;
  return data as unknown as Report<R, T>;
}

/** `/api/prices`' value, or null. */
export function parsePrices(data: unknown): Prices | null {
  if (!isRow(data) || !isList(data.models) || !data.models.every(isRow)) return null;
  if (!isList(data.layers) || !isList(data.overrides) || !isNum(data.matched)) return null;
  return data as unknown as Prices;
}

/** `/api/status`'s value, or null. */
export function parseStatus(data: unknown): Status | null {
  if (!isRow(data) || !isList(data.lanes) || !data.lanes.every(isRow)) return null;
  if (!isRow(data.total) || !isRow(data.index) || !isNum(data.total.index_version)) return null;
  if (!isNumOrNull(data.total.last_pass) || !isList(data.total.held_rows)) return null;
  return data as unknown as Status;
}

/** `/api/limits`' value or the topic's, or null. */
export function parseLimits(data: unknown): Limits | null {
  if (!isRow(data) || !isList(data.rows) || !isList(data.notes)) return null;
  const rows = data.rows.filter((row): row is Record<string, unknown> =>
    isRow(row) && typeof row.label === "string" && typeof row.state === "string");
  if (rows.length !== data.rows.length) return null;
  return { today: typeof data.today === "string" ? data.today : null,
           rows: rows as unknown as Window[], pull: isRow(data.pull) ? data.pull as Limits["pull"] : null,
           notes: data.notes.filter((note): note is string => typeof note === "string") };
}

/** `/api/window`'s value, or null. */
export function parseWindow(data: unknown): OpenedWindow | null {
  if (!isRow(data) || typeof data.harness !== "string" || typeof data.limit_id !== "string") return null;
  if (!isNum(data.window_minutes) || typeof data.resets_at !== "string" || !isNum(data.used_percent)) return null;
  if (typeof data.today !== "string" || !isNum(data.now)) return null;
  if (data.readings !== null && !(isRow(data.readings) && isRow(data.readings.last))) return null;
  if (data.used !== null && !(isRow(data.used) && isList(data.used.rows) && data.used.rows.every(isRow))) return null;
  return data as unknown as OpenedWindow;
}

/** `/api/restart-point`'s value, or null. */
export function parseRestartPoint(data: unknown): RestartPoint | null {
  if (!isRow(data) || typeof data.model !== "string") return null;
  const tokens = data.tokens ?? null;
  const reason = data.reason ?? null;
  if (!isNumOrNull(tokens) || (reason !== null && typeof reason !== "string")) return null;
  return { model: data.model, tokens: tokens as number | null, reason };
}

/** The topic `index`'s value, or null. */
export function parseIndexEvent(data: unknown): IndexEvent | null {
  if (!isRow(data) || typeof data.running !== "boolean" || !isNumOrNull(data.last_pass)) return null;
  const note = data.note ?? null;
  if (note !== null && typeof note !== "string") return null;
  return { running: data.running, last_pass: data.last_pass as number | null, note };
}

/** The line a failure shows. */
export function failureWhy(status: number): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "usage's page server sent an answer that can't be read";
  return `usage's page server answered ${status}`;
}

/** What `POST /api/pass` came to. */
export type PassOutcome =
  | { outcome: "started" }
  | { outcome: "running" }
  | { outcome: "refused"; text: string }
  | { outcome: "failed"; why: string };

/** The outcome of an answer to `POST /api/pass`. */
export function passOutcome(answer: ApiResult): PassOutcome {
  const data = answer.data as Record<string, unknown> | null;
  if (answer.status === 200 && isRow(data) && typeof data.started === "boolean") {
    return data.started ? { outcome: "started" } : { outcome: "running" };
  }
  if (answer.status === 422 && isRow(data)) {
    const text = typeof data.refused === "string" ? data.refused
      : typeof data.detail === "string" ? data.detail : "the page server refused the pass";
    return { outcome: "refused", text };
  }
  return { outcome: "failed", why: failureWhy(answer.status) };
}

/** The text of a 422 a read gave: the API's own words for a bad argument or an index it
 * can't read. */
export function refusedText(data: unknown): string | null {
  if (!isRow(data)) return null;
  for (const key of ["refused", "detail", "error"]) {
    if (typeof data[key] === "string") return data[key] as string;
  }
  return null;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/**
 * The data one view or block shows, kept current. `refresh()` reads the route; `topic()`
 * takes a topic event. `onChange` is called after every change.
 *
 * - A 200 with a readable body gives the value and, when the body has `now`, the offset
 *   of the server's clock.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means the module went away while the page was open: `gone`.
 * - A 422 is the API refusing the read: `refused` holds its text, and the last value is
 *   dropped, since it answered other arguments.
 * - Every other answer (any other status, a network failure, a body that isn't JSON or
 *   isn't the route's shape) is a failure. The last value is kept, and the next topic
 *   event or `refresh()` reads the route again.
 * - A topic event replaces the value at once while the last read succeeded; otherwise it
 *   reads the route again.
 */
export class Feed<V> {
  /** The last value read, or null before the first. */
  value: V | null = null;
  /** The last request's failure, null once a read succeeds. */
  failure: Failure | null = null;
  /** The API's text when it refused the read (a 422). */
  refused: string | null = null;
  /** The route answered 404. */
  gone = false;
  /** No read has finished yet. */
  loading = true;
  /** A read is running. */
  reading = false;
  /** The server's clock minus this browser's, in seconds; null until a body gave `now`. */
  offset: number | null = null;

  #read: () => Promise<ApiResult>;
  #parse: (data: unknown) => V | null;
  #onChange: () => void;
  #wait: Wait;
  #clock: () => number;
  #running: Promise<void> | null = null;
  #again = false;
  #closed = false;

  constructor(read: () => Promise<ApiResult>, parse: (data: unknown) => V | null,
              onChange: () => void,
              { wait = sleep, clock = () => Date.now() / 1000 }: { wait?: Wait; clock?: () => number } = {}) {
    this.#read = read;
    this.#parse = parse;
    this.#onChange = onChange;
    this.#wait = wait;
    this.#clock = clock;
  }

  /** Starts the feed with `value`, read before under other arguments, which it shows
   * until its own first read answers. */
  hold(value: V | null): this {
    this.value = value;
    return this;
  }

  /** The server's time now in epoch seconds: this browser's clock until a body gave `now`. */
  now(): number {
    return this.#clock() + (this.offset ?? 0);
  }

  /** Stops every change: a later answer or event is dropped. */
  close(): void {
    this.#closed = true;
  }

  get closed(): boolean {
    return this.#closed;
  }

  /** Reads the route; a call while a read runs makes one more read after it. */
  refresh(): Promise<void> {
    if (this.#running) {
      this.#again = true;
      return this.#running;
    }
    this.#running = (async () => {
      this.reading = true;
      try {
        do {
          this.#again = false;
          await this.#once();
        } while (this.#again && !this.#closed);
      } finally {
        this.#running = null;
        this.reading = false;
      }
    })();
    return this.#running;
  }

  /** One topic event. */
  topic(data: unknown): void {
    if (this.#closed) return;
    const value = this.#parse(data);
    if (value === null || this.loading || this.failure !== null || this.refused !== null || this.gone) {
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
    const at = this.#clock();
    this.loading = false;
    const data = answer.data as Record<string, unknown> | null;
    const value = answer.status === 200 ? this.#parse(data) : null;
    if (value !== null) {
      this.value = value;
      const now = data?.now;
      if (isNum(now)) this.offset = now - at;
      this.failure = null;
      this.refused = null;
      this.gone = false;
    } else if (answer.status === 404) {
      this.gone = true;
      this.failure = null;
    } else if (answer.status === 422) {
      this.value = null;
      this.failure = null;
      this.refused = refusedText(data) ?? "usage refused the read";
    } else {
      this.failure = { status: answer.status, why: failureWhy(answer.status) };
    }
    this.#onChange();
  }
}
