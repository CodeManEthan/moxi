// How sessions' page and window block read their data: two routes, read on demand, with
// every answer that isn't a readable 200 mapped to a state. The shapes are the routes'
// own, as sessions' page server declares them. No DOM and no Lit here, so the Node tests
// drive it with a stubbed `read`.

import type { ApiResult } from "/m/ui/client.js";

/** Where a record was read: a line and byte offset in a file (the line may be unknown),
 * or a table and row id in OpenCode's database. */
export interface At {
  line?: number | null;
  offset?: number;
  table?: string;
  id?: string;
}

/**
 * One record as `/api/window` sends it. Every row has `kind`, `raw_kind`, `at` and
 * `unknown`; any other key is present only when the row's kind carries it, and a value the
 * harness did not say is left out and named in `unknown`. A field the page server marks as
 * content may come as null where a route is redacted, and is then shown as hidden.
 */
export interface Row {
  kind: string;
  raw_kind: string;
  at: At;
  unknown: string[];
  time?: number | null;
  turn_id?: string | null;
  agent_id?: string | null;
  // a message
  role?: string | null;
  text?: string | null;
  text_bytes?: number;
  truncated?: boolean;
  reasoning?: boolean;
  flags?: string[];
  model?: string | null;
  synthetic?: boolean | null;
  // a tool call
  call_id?: string | null;
  name?: string | null;
  summary?: string | null;
  server?: string | null;
  // a tool result
  is_error?: boolean | null;
  size_bytes?: number;
  // a marker
  marker?: string;
  trigger?: string | null;
  pre_tokens?: number | null;
  duration_ms?: number | null;
  title?: string | null;
  title_source?: string | null;
  provider?: string | null;
  service_tier?: string | null;
  // a request
  request_id?: string;
  response_model?: string | null;
  input_tokens?: number | null;
  cache_read_input_tokens?: number | null;
  cache_creation_input_tokens?: number | null;
  output_tokens?: number | null;
  reasoning_output_tokens?: number | null;
  cost_usd?: number | null;
  context_window?: number | null;
  sidechain?: boolean | null;
  final?: boolean;
  // limits
  limit_id?: string | null;
  limit_name?: string | null;
  plan_type?: string | null;
  windows?: LimitWindow[];
  // malformed
  why?: string;
  unterminated?: boolean;
  // other
  recognised?: boolean;
}

/** One window of a limits record. */
export interface LimitWindow {
  name: string;
  used_percent: number | null;
  window_minutes: number | null;
  resets_at: number | null;
  unknown: string[];
}

/** A session's header; each value is absent when the harness did not say it. */
export interface Header {
  cwd?: string | null;
  title?: string | null;
  version?: string | null;
  parent?: string | null;
  entrypoint?: string | null;
  unknown: string[];
}

/** `/api/window`: one session's last turns, oldest record first. */
export interface WindowAnswer {
  shape: number;
  harness: string;
  session: string;
  agent: string | null;
  /** The file read, or OpenCode's database. */
  path: string | null;
  header: Header;
  /** Epoch seconds: the newest record time in the window. */
  last_activity: number | null;
  turns: number;
  has_earlier: boolean;
  /** Sent back as `before` for the turns before these. */
  before: string | null;
  /** How the backward read ended: `start`, `limit` or null. */
  ended: string | null;
  records: Row[];
}

/** One subagent of a listing, with what opens it. */
export interface Entry {
  agent_id: string;
  /** What the window block sends as `session`. */
  session: string;
  /** What it sends as `agent`: a Claude Code subagent's id, null for a session of its own. */
  agent: string | null;
  /** The page's route that opens it. */
  route: string;
  path: string | null;
  size: number | null;
  agent_type?: string | null;
  description?: string | null;
  tool_use_id?: string | null;
  spawn_depth?: number | null;
  parent_agent_id?: string | null;
  workflow_id?: string | null;
  unknown: string[];
}

/** `/api/subagents`: a session's subagents, and the state of the listing. */
export interface Listing {
  shape: number;
  harness: string;
  session: string;
  state: "listed" | "none" | "unreadable" | "unsupported";
  reason: string | null;
  entries: Entry[];
  unreadable_parts: { where: string | null; reason: string | null }[];
}

/** A request that gave no readable value. */
export interface Failure {
  /** The answer's status: 0 when no answer came, 200 for a body that can't be read. */
  status: number;
  /** One line saying what went wrong. */
  why: string;
}

/** The shape version this page reads. */
export const SHAPE = 1;
/** How many times a 503 (the page server's in-flight cap) is read again, 1 s apart. */
export const RETRIES = 3;
export const RETRY_MS = 1000;

const isList = (value: unknown): value is unknown[] => Array.isArray(value);
const isRow = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === "string";
const isTextOrNull = (value: unknown): boolean => value === null || isText(value);

/** `/api/window`'s value, or null when it isn't one. The server declares the shape, so
 * only what the page reads before drawing is checked. */
export function parseWindow(data: unknown): WindowAnswer | null {
  if (!isRow(data) || data.shape !== SHAPE || !isText(data.harness) || !isText(data.session)) return null;
  if (!isRow(data.header) || !isList(data.records) || typeof data.has_earlier !== "boolean") return null;
  if (!isTextOrNull(data.before ?? null) || !isTextOrNull(data.agent ?? null)) return null;
  const rows = data.records;
  if (!rows.every((row) => isRow(row) && isText(row.kind) && isRow(row.at) && isList(row.unknown))) return null;
  return data as unknown as WindowAnswer;
}

/** `/api/subagents`' value, or null. */
export function parseListing(data: unknown): Listing | null {
  if (!isRow(data) || data.shape !== SHAPE || !isText(data.state) || !isList(data.entries)) return null;
  if (!["listed", "none", "unreadable", "unsupported"].includes(data.state)) return null;
  if (!data.entries.every((entry) => isRow(entry) && isText(entry.agent_id) && isText(entry.route)
                          && isText(entry.session))) return null;
  const parts = isList(data.unreadable_parts) ? data.unreadable_parts.filter(isRow) : [];
  return { ...(data as unknown as Listing), unreadable_parts: parts as Listing["unreadable_parts"] };
}

/** The line a failure shows. */
export function failureWhy(status: number, data: unknown = null): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "sessions' page server sent an answer that can't be read";
  if (status === 400 && isRow(data) && isText(data.error)) {
    return `sessions' page server refused the request: ${data.error}`;
  }
  return `sessions' page server answered ${status}`;
}

/** The text of a 422: the page server's own words for a session it can't find or read,
 * or a value it refuses. */
export function refusedText(data: unknown): string | null {
  if (!isRow(data)) return null;
  for (const key of ["refused", "detail", "error"]) {
    if (isText(data[key])) return data[key] as string;
  }
  return null;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/**
 * The data one part of the view shows. `refresh()` reads the route.
 *
 * - A 200 with a readable body gives the value.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means sessions went away while the page was open: `gone`.
 * - A 422 is the page server refusing the read (no such session, a source it can't read,
 *   a `before` it can't use): `refused` holds its reason, and the last value is dropped,
 *   since it answered another question.
 * - Every other answer (a 400, any other status, a network failure, a body that isn't the
 *   route's shape) is a failure. The last value is kept.
 */
export class Feed<V> {
  /** The last value read, or null before the first. */
  value: V | null = null;
  /** The last request's failure, null once a read succeeds. */
  failure: Failure | null = null;
  /** The page server's reason when it refused the read (a 422). */
  refused: string | null = null;
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
      this.#onChange();
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
      this.refused = null;
      this.gone = false;
    } else if (answer.status === 404) {
      this.gone = true;
      this.failure = null;
    } else if (answer.status === 422) {
      this.value = null;
      this.failure = null;
      this.refused = refusedText(answer.data) ?? "sessions' page server refused the read";
    } else {
      this.failure = { status: answer.status, why: failureWhy(answer.status, answer.data) };
    }
    this.#onChange();
  }
}
