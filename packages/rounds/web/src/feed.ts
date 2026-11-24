// How rounds' page reads its data: the board's route first, then its topic, and one
// record at a time, with every answer that isn't a readable 200 mapped to a state. No DOM
// and no Lit here, so the Node tests drive it with a stubbed `read` and clock. The types
// are the routes' shapes as `page.py` declares them.

import type { ApiResult } from "/m/ui/client.js";

/** One step's cell; null where the round has no step in the column. */
export interface Cell {
  /** One of the eight: `yours`, `none`, `done`, `failed`, `owed`, `waiting`, `child`,
   * `todo`. */
  state: string;
  word: string | null;
  count: number | null;
  settled: boolean;
  here: boolean;
  /** rounds' own state word for the step; null for the close. */
  step_state: string | null;
  by: string | null;
  who: string | null;
}

export interface Column {
  id: string;
  label: string;
  /** `round`, `stage` or `end`. */
  part: string;
}

/** A part of a "where it is" phrase. */
export interface PhrasePart {
  text: string;
  /** `yours`, `bad`, `waits`, `moves` or `quiet`. */
  weight: string;
}

export interface Option {
  key: string;
  text: string;
}

/** A question's newest prepared answer: who prepared it, the option it recommends, how
 * sure it is and why, and whether it was prepared for the options the question offers
 * now (`current`). */
export interface Prepared {
  by: string | null;
  model: string | null;
  recommend: string | null;
  sure: string | null;
  why: string | null;
  flips: string | null;
  read: string[];
  options: { key: string; text?: string | null; costs: string | null; forecloses: string | null }[];
  actor: string | null;
  at: string | null;
  current: boolean;
}

/** A round's lead, its own or its project's (`from`: `round` or `project`). */
export interface Lead {
  name: string;
  from: string;
}

/** A question or a decision, as what waits on the operator gives it. */
export interface Entry {
  /** The round (`<project>/<slug>`) or the project that holds it. */
  holder: string;
  id: string;
  text: string | null;
  from?: string | null;
  blocks?: string[];
  blocks_word?: string | null;
  state?: string | null;
  by?: string | null;
  at?: string | null;
  builder?: string | null;
  options?: Option[];
  stage?: string | null;
  weight?: string | null;
  forecloses?: boolean | null;
  alone?: boolean | null;
  /** For a decision not put to the operator, why not. */
  not_put?: string | null;
  /** What a parked question waits for, if anything. */
  until?: string | null;
  /** A question's newest prepared answer; null when it has none. */
  prepared?: Prepared | null;
}

/** A job for the operator. */
export interface Job {
  round: string;
  id: string;
  stage: string | null;
  text: string | null;
  report: string | null;
  by: string | null;
  at: string | null;
}

/** A ruling not yet shown to the operator. */
export interface Shown {
  holder: string;
  id: string;
  text: string | null;
  rules?: string | null;
  record?: string | null;
  /** The ruling this one replaces, when it rules the same thing again. */
  supersedes?: string | null;
  at?: string | null;
}

export interface Waiting {
  questions: Entry[];
  /** The parked questions: not put to the operator, listed apart. */
  parked_questions?: Entry[];
  decisions: Entry[];
  waits: Job[];
  not_shown: { project: string; through: string | null; rows: Shown[] }[];
  parked: number;
}

/** One build of the strip. */
export interface Recent {
  round: string;
  project: string;
  stage: string;
  /** The line of the build's commission in its round's log: two builds of one stage differ. */
  build: number;
  closed: boolean;
  act_at: string | null;
  act: string;
  builder: string | null;
  branch: string | null;
  /** The stage's step ids, parallel to `cells`. */
  steps: string[];
  cells: (Cell | null)[];
}

export interface Line {
  /** `round`, `build`, or `stage` for a folded one. */
  kind: string;
  stage: string | null;
  branch: string | null;
  /** `more`, `landed`, `not-started`, or null for a line always shown. */
  fold: string | null;
  phrase: PhrasePart[];
  /** One per column, in the columns' order. */
  cells: (Cell | null)[];
}

export interface Folds {
  more: number;
  landed: number;
  not_started: { n: number; ready: number; held: number; child: number };
}

export interface Round {
  name: string;
  project: string;
  slug: string;
  description: string | null;
  flow: string | null;
  standing: string | null;
  version: number | null;
  step: { step: string; stage: string | null; state: string } | null;
  counts: { open: number | null; parked: number | null; not_shown: number | null };
  lead?: Lead | null;
  /** `yours`, `stuck`, `wrapping` or `moving`. */
  group: string;
  phrase: PhrasePart[];
  mine: number;
  last_at: string | null;
  rev: string;
  lines: Line[];
  folds: Folds;
}

export interface Closed {
  name: string;
  project: string;
  flow: string | null;
  ended_at: string | null;
  how: string | null;
}

export interface Unreadable {
  name: string;
  reason: string;
  fix: string | null;
}

/** The board, as the route (without `now`) and the topic carry it. */
export interface Board {
  today: string;
  home: string | null;
  read_at: string;
  /** `up`, or `error` when the reads every row needs failed. */
  state: string;
  detail: string | null;
  columns: Column[];
  waiting: Waiting;
  recent: Recent[];
  rounds: Round[];
  closed: Closed[];
  unreadable: Unreadable[];
}

/** One event of a record. */
export interface Happened {
  n: number | null;
  at: string | null;
  by: string | null;
  type: string | null;
  /** The type in words, as the strip says it. */
  words: string | null;
  of: string | null;
}

export interface Findings {
  blocker: number;
  major: number;
  minor: number;
  note: number;
}

/** A ruling as a record gives it. */
export interface RulingFields {
  id: string;
  text: string | null;
  record: string | null;
  actor: string | null;
  at: string | null;
  shown: boolean;
}

/** A record: a step's, a card's, a waiting item's or a ruling's. Each holds only its own
 * kind's keys, so every key is optional. */
export interface RecordValue {
  round?: string;
  column?: string;
  stage?: string | null;
  state?: string | null;
  word?: string | null;
  next?: {
    state: string; by: string | null; who: string | null;
    waits: { reason: string; fix: string | null }[];
    then: string[]; reads: string[]; write: string | null; questions: string[];
  } | null;
  facts?: { key: string; value: string }[];
  verdicts?: {
    version: number | null; verdict: string | null; pass: string | null; at: string | null;
    by: string | null; model: string | null; report: string | null; findings: Findings;
    open: number | null;
  }[];
  findings?: {
    open: { id: string; severity: string | null; state: string | null; text: string | null;
            step: string | null; raised: number | null; set_by: number | null }[];
    rest: number;
  } | null;
  ci?: {
    commit: string | null; at: string | null; reading: string | null;
    jobs: { name: string | null; reading: string | null; url: string | null; workflow: string | null }[];
  } | null;
  runs?: {
    step: string | null; exit_code: number | null; duration_s: number | null; commit: string | null;
    at: string | null; output: string | null;
  }[];
  drift?: { why: string | null; owed: boolean; moved: number | null; meeting: string[]; shared: string[] } | null;
  gates?: {
    unmet: { number: number | null; reason: string; fix: string | null }[];
    actions: { id: string; kind: string | null; state: string | null; landing: string | null; n: number; at: string | null }[];
  } | null;
  decisions?: { n: number; majors: number; unruled: number } | null;
  items?: string[];
  settled?: { id: string; text: string | null; chose: string | null; ruling: string | null }[];
  close?: string[];
  fixes?: { id: string; since: string | null }[];
  events?: Happened[];
  rev?: string | null;
  // a round's card
  name?: string;
  project?: string;
  description?: string | null;
  flow?: string | null;
  opened?: string | null;
  standing?: string | null;
  stages?: { finished: number; building: number; not_started: number };
  rulings?: number;
  newest_rulings?: { id: string; text: string | null; at: string | null }[];
  phrase?: PhrasePart[];
  ended?: { how: string | null; at: string | null } | null;
  ends?: { stage: string; how: string }[];
  // a waiting item's record
  what?: string;
  item?: Entry & Partial<Job> & Partial<Shown> & { project?: string | null; ruling?: string | null; chose?: string | null };
  answer?: string;
  passage?: string | null;
  /** The ruling that put a question where it is: ruled, parked or withdrawn; in a ruling's
   * record, the ruling itself. */
  ruling?: RulingFields | null;
  // a ruling's record
  /** The round or project whose log holds the ruling. */
  holder?: string;
  /** The newest ruling that replaces it, shown or not; null while none has. */
  replaced_by?: RulingFields | null;
  lead?: Lead | null;
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

const object = (data: unknown): data is Record<string, unknown> =>
  typeof data === "object" && data !== null && !Array.isArray(data);

/** The route's or the topic's value, or null when it isn't one. A round with no text
 * `name`, or with no lines, is dropped: it can't be drawn or told apart. */
export function parseBoard(data: unknown): Board | null {
  if (!object(data)) return null;
  const { today, read_at, state, columns, waiting, recent, rounds, closed, unreadable } = data;
  if (typeof today !== "string" || typeof state !== "string" || !Array.isArray(columns)
      || !object(waiting) || !Array.isArray(rounds)) return null;
  const list = <T>(value: unknown, keep: (one: any) => boolean): T[] =>
    Array.isArray(value) ? value.filter((one) => object(one) && keep(one)) as T[] : [];
  const kept = (one: any) => typeof one.id === "string";
  return {
    today,
    home: typeof data.home === "string" && data.home !== "" ? data.home : null,
    read_at: typeof read_at === "string" ? read_at : "",
    state,
    detail: typeof data.detail === "string" ? data.detail : null,
    columns: list<Column>(columns, (one) => typeof one.id === "string"),
    waiting: {
      questions: list<Entry>(waiting.questions, kept),
      parked_questions: list<Entry>(waiting.parked_questions, kept),
      decisions: list<Entry>(waiting.decisions, kept),
      waits: list<Job>(waiting.waits, kept),
      not_shown: list<Waiting["not_shown"][number]>(waiting.not_shown, (one) => Array.isArray(one.rows)),
      parked: typeof waiting.parked === "number" ? waiting.parked : 0,
    },
    recent: list<Recent>(recent, (one) => typeof one.round === "string" && Array.isArray(one.cells)),
    rounds: list<Round>(rounds, (one) => typeof one.name === "string" && Array.isArray(one.lines)
                                         && one.lines.length > 0),
    closed: list<Closed>(closed, (one) => typeof one.name === "string"),
    unreadable: list<Unreadable>(unreadable, (one) => typeof one.name === "string"),
  };
}

/** The line a failure shows. */
export function failureWhy(status: number): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "rounds' page server sent an answer that can't be read";
  return `rounds' page server answered ${status}`;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/** Reads once, and again after 1 s while it answers 503, up to three times. */
async function readPatiently(read: () => Promise<ApiResult>, wait: Wait,
                             isClosed: () => boolean = () => false): Promise<ApiResult> {
  let answer = await read();
  for (let tries = 0; answer.status === 503 && tries < RETRIES && !isClosed(); tries += 1) {
    await wait(RETRY_MS);
    if (isClosed()) return answer;
    answer = await read();
  }
  return answer;
}

/**
 * The board, kept current. `refresh()` reads the route; `topic()` takes a topic event.
 * `onChange` is called after every change.
 *
 * - A 200 with a readable body gives the value and the offset of the server's clock.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means the module went away while the page was open: `gone`.
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
  /** When the value was last read, on the server's clock. Null before the first read. */
  readAt: number | null = null;

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
    this.readAt = this.now();
    this.#onChange();
  }

  async #once(): Promise<void> {
    const answer = await readPatiently(this.#read, this.#wait, () => this.#closed);
    if (this.#closed) return;
    const at = this.#clock();
    this.loading = false;
    const data = answer.data as Record<string, unknown> | null;
    const value = answer.status === 200 ? this.#parse(data) : null;
    const now = data?.now;
    if (value !== null && typeof now === "number" && Number.isFinite(now)) {
      this.value = value;
      this.offset = now - at;
      this.readAt = now;
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

/** What one read of `/api/record` gave: the record, `gone` when what it names is no
 * longer open, or the failure. */
export type RecordRead =
  | { kind: "record"; record: RecordValue }
  | { kind: "gone" }
  | { kind: "failed"; failure: Failure };

/** Reads one record, with the board's 503 retries. */
export async function readRecord(read: () => Promise<ApiResult>,
                                 { wait = sleep }: { wait?: Wait } = {}): Promise<RecordRead> {
  const answer = await readPatiently(read, wait);
  const data = answer.data as Record<string, unknown> | null;
  if (answer.status === 200 && object(data) && typeof data.gone === "boolean") {
    if (data.gone || !object(data.record)) return { kind: "gone" };
    return { kind: "record", record: data.record as RecordValue };
  }
  return { kind: "failed", failure: { status: answer.status, why: failureWhy(answer.status) } };
}
