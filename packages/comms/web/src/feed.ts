// How comms' roster reads its data: the route first, then the topic, with every answer
// that isn't a readable 200 mapped to a state. No DOM and no Lit here, so the Node tests
// drive it with a stubbed `read` and clock.

import type { ApiResult } from "/m/ui/client.js";

/** One address as comms' page sends it: `/api/roster`'s and the topic's row. */
export interface RosterRow {
  qualified: string;
  name: string;
  scope: string | null;
  /** A session holds the name now. */
  live: boolean;
  /** Its standing row's kind (`agent`, `send-only`), null with no standing row. */
  kind: string | null;
  /** The module whose process the name is reserved for, if any. */
  module: string | null;
  tier: string | null;
  adapter: string | null;
  directors: string[];
  session_id: string | null;
  /** When the session took the name, on the server's clock, epoch seconds. */
  bound_at: number | null;
  /** Whom the session waits on. */
  awaiting: string[];
}

/** The value of `/api/roster` (without `now`) and of the topic `roster`. */
export interface Roster {
  /** `up`; `offline` when comms' daemon isn't answering; `error` when it answered with an
   * error or the read failed. */
  state: "up" | "offline" | "error";
  detail: string | null;
  rows: RosterRow[];
}

/** A request that gave no readable value. */
export interface Failure {
  /** The answer's status: 0 when no answer came, 200 for a body that can't be read. */
  status: number;
  /** One line saying what went wrong, for a title. */
  why: string;
}

/** How many times a 503 (the page server's in-flight cap) is read again, 1 s apart. */
export const RETRIES = 3;
export const RETRY_MS = 1000;

/** The topic's value, or null when it isn't one. */
export function parseRoster(data: unknown): Roster | null {
  if (typeof data !== "object" || data === null) return null;
  const { state, detail, rows } = data as Record<string, unknown>;
  if (detail !== null && detail !== undefined && typeof detail !== "string") return null;
  if (state === "offline" || state === "error") return { state, detail: detail ?? null, rows: [] };
  if (state !== "up" || !Array.isArray(rows)) return null;
  const strings = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  const kept = rows
    .filter((row) => typeof row === "object" && row !== null && typeof row.qualified === "string")
    .map((row): RosterRow => ({ ...row, live: row.live === true, directors: strings(row.directors),
                                awaiting: strings(row.awaiting) }));
  return { state, detail: null, rows: kept };
}

/** The line a failure shows. */
export function failureWhy(status: number): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "comms' page server sent an answer that can't be read";
  return `comms' page server answered ${status}`;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/**
 * The data one component shows, kept current. `refresh()` reads the route; `topic()`
 * takes a topic event. `onChange` is called after every change.
 *
 * - A 200 with a readable body gives the value and the offset of the server's clock.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means the module went away while the component was open: `gone`.
 * - Every other answer (any other status, a network failure, a body that isn't JSON or
 *   isn't the route's shape) is a failure. The last value is kept, and the next topic
 *   event or `refresh()` reads the route again.
 * - A topic event replaces the value at once while the last read succeeded; otherwise it
 *   reads the route again, which also sets the clock's offset.
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
  /** The server's clock minus this browser's, in seconds; null before the first read. */
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

  /** The server's time now, in epoch seconds, or null before the first read. */
  now(): number | null {
    return this.offset === null ? null : this.#clock() + this.offset;
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
    this.#running = (async () => {
      try {
        do {
          this.#again = false;
          await this.#once();
        } while (this.#again && !this.#closed);
      } finally {
        this.#running = null;
      }
    })();
    return this.#running;
  }

  /** One topic event. */
  topic(data: unknown): void {
    if (this.#closed) return;
    const value = this.#parse(data);
    if (value === null || this.offset === null || this.failure !== null || this.gone) {
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
    this.#onChange();
  }
}
