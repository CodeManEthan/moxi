// How the launcher's components read their data: a route first, then a topic of the
// stream, with every answer that isn't a readable 200 mapped to a state. No DOM and no Lit
// here, so the Node tests drive it with a stubbed `read` and wait.
//
// The stream has two topics: `sessions`, the state of every live launch, and `launches`,
// the newest page of the launch log. Each carries what its route answers.

import type { ApiResult } from "/m/ui/client.js";

/** A refusal as the launcher words it. */
export interface Refusal {
  code: string;
  row: string | null;
  problem: string;
  hint: string;
}

/** One run as a session's state shows it. `state` is the open run's state, `stale`, or
 * `ended`. */
export interface RunLine {
  run_id: string;
  kind: string;
  mode: string;
  state: string;
  outcome: string | null;
  word: string | null;
  detail: string | null;
  created_at: string;
  updated_at: string;
}

export interface RelaunchState {
  /** The open relaunch run's state, or the last relaunch's outcome; null with none. */
  state: string | null;
  run: RunLine | null;
  made_by: string | null;
  notices_refused: string[];
  note_not_taken: boolean;
}

export interface RecycleState {
  on_expiry: string;
  at_limit: string;
  limit_at: string | null;
  last: RunLine | null;
  pending_compact: string | null;
  told_at: string | null;
  pending_dropped_at: string | null;
}

export interface KeepWarmState {
  on: boolean;
  holds: string[];
  nudges: number;
  last: RunLine | null;
  window: string | null;
}

/** A launched session's state, as `/api/state` and the topic `sessions` send it. `red`
 * says it needs a look, `word` is then `blocked` or `failed`. When the server couldn't
 * read one launch's state, `unreadable` says why and the three parts are null. */
export interface SessionState {
  session_id: string;
  request_id: string;
  relaunch: RelaunchState | null;
  recycle: RecycleState | null;
  keep_warm: KeepWarmState | null;
  red: boolean;
  since: string | null;
  why: string | null;
  word: string | null;
  run_id: string | null;
  unreadable: string | null;
}

/** The value of `/api/state` and of the topic `sessions`. */
export interface States {
  /** `up`, or `error` when the page server's read failed. */
  state: "up" | "error";
  detail: string | null;
  sessions: SessionState[];
  /** `/api/state?session=` for a session no launch holds; the topic sends none. */
  refused: Refusal | null;
}

/** One launch log row, as `/api/launches` and the topic `launches` send it. */
export interface LaunchRow {
  request_id: string;
  created_at: string;
  updated_at: string;
  harness: string;
  target: string;
  state: string;
  reason: string | null;
  stale: boolean;
  preset_kind: string | null;
  preset_name: string | null;
  slot: string | null;
  cwd: string;
  identity: string | null;
  session_id: string | null;
  resume_of: string | null;
  relaunch_of: string | null;
}

/** The value of `/api/launches` and of the topic `launches`. */
export interface Launches {
  state: "up" | "error";
  detail: string | null;
  launches: LaunchRow[];
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

type Fields = Record<string, unknown>;

function isTable(value: unknown): value is Fields {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `{state, detail}` of a value, or null when it isn't one. */
function standing(data: unknown): { state: "up" | "error"; detail: string | null; fields: Fields } | null {
  if (!isTable(data)) return null;
  const { state, detail } = data;
  if (detail !== null && detail !== undefined && typeof detail !== "string") return null;
  if (state !== "up" && state !== "error") return null;
  return { state, detail: state === "error" ? (detail ?? null) : null, fields: data };
}

function isRefusal(value: unknown): value is Refusal {
  return isTable(value) && typeof value.code === "string" && typeof value.problem === "string";
}

function isSessionState(value: unknown): value is SessionState {
  return isTable(value) && typeof value.session_id === "string"
    && typeof value.request_id === "string" && typeof value.red === "boolean";
}

function isLaunch(value: unknown): value is LaunchRow {
  return isTable(value) && typeof value.request_id === "string"
    && typeof value.harness === "string" && typeof value.state === "string";
}

/** The value of `/api/state` or of the topic `sessions`, or null when it isn't one. */
export function parseStates(data: unknown): States | null {
  const read = standing(data);
  if (read === null) return null;
  const { sessions, refused } = read.fields;
  if (read.state === "error") return { state: "error", detail: read.detail, sessions: [], refused: null };
  if (!Array.isArray(sessions)) return null;
  return { state: "up", detail: null, sessions: sessions.filter(isSessionState),
           refused: isRefusal(refused) ? refused : null };
}

/** The value of `/api/launches` or of the topic `launches`, or null when it isn't one. */
export function parseLaunches(data: unknown): Launches | null {
  const read = standing(data);
  if (read === null) return null;
  if (read.state === "error") return { state: "error", detail: read.detail, launches: [] };
  const { launches } = read.fields;
  if (!Array.isArray(launches)) return null;
  return { state: "up", detail: null, launches: launches.filter(isLaunch) };
}

/**
 * The entry for one session among `sessions`: its launch's (`request`), once the route has
 * said which launch holds the session, else the first under the session id.
 */
export function entryOf(sessions: SessionState[], session: string,
                        request: string | null): SessionState | undefined {
  return sessions.find((entry) => (request !== null ? entry.request_id === request
                                                    : entry.session_id === session));
}

/**
 * Which launch one session's state component follows. A clear or a compact gives a
 * session a new id. The route resolves any id the launch has had and answers under the
 * newest, so the id the component was placed with may be one no entry carries. Once the
 * route has answered, the component follows the launch by its request id, which never
 * changes.
 */
export class SessionPick {
  readonly session: string;
  /** The launch the route resolved the session to; null until it has, or when no launch
   * holds the session. */
  request: string | null = null;

  constructor(session: string) {
    this.session = session;
  }

  /** The route's answer, read as `parseStates` reads it; it holds at most the one launch
   * that holds the session, which the pick follows from then on. */
  route = (data: unknown): States | null => {
    const value = parseStates(data);
    if (value !== null && value.state === "up") this.request = value.sessions[0]?.request_id ?? null;
    return value;
  };

  /** A topic event cut to the followed launch's entry. Null when the topic holds none
   * (a session that isn't live is in no topic), so the component reads its route instead. */
  topic = (data: unknown): States | null => {
    const value = parseStates(data);
    if (value === null || value.state === "error") return value;
    const found = entryOf(value.sessions, this.session, this.request);
    return found === undefined ? null : { ...value, sessions: [found] };
  };
}

/** The line a failure shows as its title. */
export function failureWhy(status: number): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "the launcher's page server sent an answer that can't be read";
  return `the launcher's page server answered ${status}`;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/** What a `Feed` takes beside its route: how a topic event reads, and how to wait. */
export interface FeedOptions<V> {
  /** A topic event's value, null to read the route instead; the route's parse by default. */
  topic?: (data: unknown) => V | null;
  wait?: Wait;
}

/**
 * The data one component shows, kept current. `refresh()` reads the route; `topic()`
 * takes a topic event. `onChange` is called after every change.
 *
 * - A 200 with a readable body gives the value.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means the module went away while the component was open: `gone`.
 * - Every other answer (any other status, a network failure, a body that isn't JSON or
 *   isn't the route's shape) is a failure. The last value is kept, and the next topic
 *   event or `refresh()` reads the route again.
 * - A topic event replaces the value at once once a read has succeeded and the last one
 *   did; otherwise, or when the event doesn't read, it reads the route again.
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

  #read: () => Promise<ApiResult>;
  #parse: (data: unknown) => V | null;
  #parseTopic: (data: unknown) => V | null;
  #onChange: () => void;
  #wait: Wait;
  #running: Promise<void> | null = null;
  #again = false;
  #closed = false;

  constructor(read: () => Promise<ApiResult>, parse: (data: unknown) => V | null,
              onChange: () => void, { topic, wait = sleep }: FeedOptions<V> = {}) {
    this.#read = read;
    this.#parse = parse;
    this.#parseTopic = topic ?? parse;
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
    const value = this.#parseTopic(data);
    if (value === null || this.value === null || this.failure !== null || this.gone) {
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
      this.failure = { status: answer.status, why: failureWhy(answer.status) };
    }
    this.#onChange();
  }
}
