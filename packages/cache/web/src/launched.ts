// What launcher says of the sessions it runs now, as the Cache page draws it, with no DOM so
// the Node tests can drive it. With launcher installed the page reads launcher's
// `/api/running` and `/api/state` and, at the desk, follows their topics `running` and
// `sessions`, which carry the same values. A clock record is matched to a running launch by
// its harness and session id; a record no running launch holds is matched to nothing.
//
// A matched row takes from its launch: its name where none was typed in the harness, where
// it runs (`slot 4 · atlas-api`, `terminal · kestrel-web`), the effort it was launched with,
// and the cache fact launcher gives it, which wins over cache's own words where launcher has
// a case of its own. A session that waits on agents or subagents while its cache is warm is
// `covered` when keep-warm will nudge it before the cache goes cold, and `uncovered` when
// nothing will: that one is loud, and leads its group. Every other session is `ordinary`.
// The words are launcher's own, as Home's and the Sessions page's running rows say them, and
// a test holds them equal.
//
// An uncovered session that a press would cover gets `Keep warm` at the desk. A press posts
// the launch's request id to launcher's `KEEP_WARM_PATH` and waits, the button disabled, for
// the route's answer and the next value of `running` after it, or for `PRESS_WAIT_MS`. A
// refusal's words are the row's note until the next value.
//
// A session keep-warm covers carries when its next keep-alive is due, launcher's
// `next_keep_alive_at`: line two says `next keep-alive 19:49`, or `next keep-alive due` once
// that time has come, since the nudge then goes at keep-warm's next ask. Where launcher
// gives no such time the piece is not drawn.
//
// The opened row's `Keep warm` line is launcher's state of the launch: on, with the holds it
// keeps the cache for; or off, with what keep-warm would do. Without launcher, or with a
// read failing, nothing is matched and the page draws none of it.
//
// Every string here goes into the page as text.

import type { ApiResult } from "/m/ui/client.js";

/** The route the page reads the running launches from, relative to launcher's page. */
export const RUNNING_PATH = "api/running";
/** The topic of launcher's stream that carries the same value. */
export const RUNNING_TOPIC = "running";
/** The route the page reads every live launch's state from, relative to launcher's page. */
export const STATE_PATH = "api/state";
/** The topic of launcher's stream that carries the same value. */
export const STATE_TOPIC = "sessions";
/** The route a press of `Keep warm` posts to, relative to launcher's page. */
export const KEEP_WARM_PATH = "api/keep-warm";
/** How long a pressed `Keep warm` stays disabled at most, in milliseconds. */
export const PRESS_WAIT_MS = 20_000;
export const KEEP_WARM = "Keep warm";
/** How each note a press leaves begins. */
export const NOT_KEPT = "Not kept warm: ";

/** Where a running launch runs. `folder` is its directory's last segment, never a path. */
export interface Where {
  target: string;
  slot: string | null;
  project_key: string | null;
  folder: string;
}

/** A running launch's cache: `until` is when a warm one goes cold. */
export interface LaunchCache {
  state: "warm" | "cold";
  until: string | null;
  estimated: boolean;
}

/** What a running launch waits on while its own turn is closed. */
export interface Waits {
  kind: "agents" | "subagents";
  count: number;
}

/** One launch running now, as far as the page draws it. */
export interface Launch {
  request_id: string;
  harness: string;
  session_id: string;
  name: string;
  where: Where;
  effort: string | null;
  cache: LaunchCache | null;
  waits: Waits | null;
  covered: boolean;
  /** At most `cap` nudges in a row a press would send; null where a press would not cover it. */
  coverable: { cap: number } | null;
  /** When keep-warm's next nudge is due, as launcher writes it; null where it gives none. */
  next_keep_alive_at: string | null;
}

/** The running launches by `<harness>/<session id>`. */
export type Launched = ReadonlyMap<string, Launch>;

const isTable = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): string | null => (typeof value === "string" ? value : null);
const filled = (value: unknown): string | null =>
  (typeof value === "string" && value.trim() !== "" ? value.trim() : null);

function parseCache(value: unknown): LaunchCache | null {
  if (!isTable(value) || (value.state !== "warm" && value.state !== "cold")) return null;
  return { state: value.state, until: text(value.until), estimated: value.estimated === true };
}

function parseWaits(value: unknown): Waits | null {
  if (!isTable(value) || (value.kind !== "agents" && value.kind !== "subagents")) return null;
  const count = typeof value.count === "number" && Number.isInteger(value.count) ? value.count : 0;
  return count > 0 ? { kind: value.kind, count } : null;
}

function parseCoverable(value: unknown): { cap: number } | null {
  if (!isTable(value) || typeof value.cap !== "number" || !Number.isInteger(value.cap) || value.cap < 1) return null;
  return { cap: value.cap };
}

function parseLaunch(value: unknown): Launch | null {
  if (!isTable(value)) return null;
  const request = filled(value.request_id);
  const harness = filled(value.harness);
  const session = filled(value.session_id);
  const name = filled(value.name);
  const where = isTable(value.where) ? value.where : null;
  const target = where === null ? null : filled(where.target);
  if (request === null || harness === null || session === null || name === null || where === null || target === null) {
    return null;
  }
  return {
    request_id: request, harness, session_id: session, name: name.replace(/\s+/g, " "),
    where: { target, slot: filled(where.slot), project_key: filled(where.project_key), folder: filled(where.folder) ?? "" },
    effort: filled(value.effort),
    cache: parseCache(value.cache),
    waits: parseWaits(value.waits),
    covered: value.covered === true,
    coverable: parseCoverable(value.coverable),
    next_keep_alive_at: filled(value.next_keep_alive_at),
  };
}

/** A row's key: its harness and session. */
const keyOf = (harness: string, session: string): string => `${harness}/${session}`;

/**
 * The running launches from a value of `/api/running` or of the topic `running`, by
 * harness and session id. A launch with no request id, session id, name or target is left
 * out; so is a second launch that claims the same session. Null for a body that is not
 * such a value, and for one whose state is not `up`: launcher could not say, so nothing is
 * matched.
 */
export function parseLaunched(data: unknown): Launched | null {
  if (!isTable(data) || data.state !== "up" || !Array.isArray(data.sessions)) return null;
  const found = new Map<string, Launch>();
  for (const entry of data.sessions) {
    const launch = parseLaunch(entry);
    if (launch === null) continue;
    const key = keyOf(launch.harness, launch.session_id);
    if (!found.has(key)) found.set(key, launch);
  }
  return found;
}

/** The running launch that holds the record `key`; null with nothing read. */
export function launchOf(launched: Launched | null, key: string): Launch | null {
  return launched?.get(key) ?? null;
}

// --- the words --------------------------------------------------------------------------

/** A launcher time as epoch seconds, or null when it is no time. */
export function stampOf(at: string | null): number | null {
  const when = at === null ? NaN : Date.parse(at);
  return Number.isNaN(when) ? null : when / 1000;
}

/** Launcher's three cases: `uncovered` and `covered` wait on agents or subagents with the
 * cache warm; every other session is `ordinary`. */
export function caseOf(launch: Launch): "ordinary" | "covered" | "uncovered" {
  if (launch.waits === null || launch.cache === null || launch.cache.state !== "warm") return "ordinary";
  return launch.covered ? "covered" : "uncovered";
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** `2 agents`, `1 subagent`. */
export function waited(waits: Waits): string {
  return waits.kind === "agents" ? plural(waits.count, "agent", "agents") : plural(waits.count, "subagent", "subagents");
}

/** Where a session runs, as Home's running rows say it: `slot N`, `terminal` or
 * `headless`, then its project's key, else its folder. */
export function whereText(where: Where): string {
  const runs = where.target === "slot" ? (where.slot ? `slot ${where.slot}` : "slot") : where.target;
  const place = where.project_key || where.folder;
  return [runs, place].filter(Boolean).join(" · ");
}

/**
 * The fact launcher gives a running launch's row, and whether it is heavy; null where it
 * gives none. `goes cold at <time>` (uncovered), `kept warm while …` (covered), else
 * `warm until <time>` or `cold`, with `, est.` where launcher marks the time estimated.
 * `clock` words a time of day.
 */
export function factOf(launch: Launch, clock: (at: number) => string): { text: string; heavy: boolean } | null {
  const cache = launch.cache;
  if (cache === null) return null;
  const until = stampOf(cache.until);
  const est = cache.estimated ? ", est." : "";
  switch (caseOf(launch)) {
    case "uncovered":
      return { text: `${until !== null ? `goes cold at ${clock(until)}` : "goes cold"}${est}`, heavy: true };
    case "covered": {
      const waits = launch.waits as Waits;
      const work = waits.kind === "agents"
        ? `${plural(waits.count, "agent", "agents")} ${waits.count === 1 ? "works" : "work"}`
        : `${plural(waits.count, "subagent", "subagents")} ${waits.count === 1 ? "runs" : "run"}`;
      return { text: `kept warm while ${work}`, heavy: false };
    }
    default:
      if (cache.state === "cold") return { text: "cold", heavy: false };
      return { text: `${until !== null ? `warm until ${clock(until)}` : "warm"}${est}`, heavy: false };
  }
}

/** What leads line two in the uncovered case, heavy: `waiting on <n> agents`; null otherwise. */
export function leadOf(launch: Launch): string | null {
  return caseOf(launch) === "uncovered" ? `waiting on ${waited(launch.waits as Waits)}` : null;
}

/** `Keep warm`'s title, which says what a press costs. */
export function keepWarmTitle(cap: number): string {
  return `Sends a short turn before each cold time while it waits, at most ${cap} in a row.`;
}

/** `Keep warm`'s title for `launch`, where the act is drawn: at the desk, on an uncovered
 * session that a press would cover. Null elsewhere. */
export function keepWarmOf(launch: Launch, desk: boolean): string | null {
  if (!desk || launch.coverable === null || caseOf(launch) !== "uncovered") return null;
  return keepWarmTitle(launch.coverable.cap);
}

/** How line two's piece of the next keep-alive begins. */
export const NEXT_KEEP_ALIVE = "next keep-alive";
/** What it says once the time has come. */
export const KEEP_ALIVE_DUE = "due";

/**
 * Line two's piece of the next keep-alive: `next keep-alive <time>`, or `next keep-alive
 * due` at or after that time (`now`, epoch seconds), since the nudge then goes at
 * keep-warm's next ask. Null where launcher gives no time. `clock` words a time of day.
 */
export function keepAliveOf(launch: Launch, now: number, clock: (at: number) => string): string | null {
  const at = stampOf(launch.next_keep_alive_at);
  if (at === null) return null;
  return `${NEXT_KEEP_ALIVE} ${at <= now ? KEEP_ALIVE_DUE : clock(at)}`;
}

/** What the opened `Goes cold` line says after the time of a covered session. */
export const UNLESS_KEPT = "if nothing keeps it warm";

// --- launcher's state of each launch ------------------------------------------------------

/** A launch's keep-warm, as launcher's state of it says. */
export interface KeepWarmState {
  on: boolean;
  /** The holds it keeps the cache for, by name. */
  holds: string[];
  /** Why keep-warm can't nudge it, in launcher's words; null when nothing stops it. */
  window: string | null;
}

/** Each live launch's keep-warm by request id and session id; null where launcher could
 * not read that launch's state. */
export type KeepWarms = ReadonlyMap<string, KeepWarmState | null>;

const stateKey = (request: string, session: string): string => `${request}\n${session}`;

function parseKeepWarm(value: unknown): KeepWarmState | null {
  if (!isTable(value) || typeof value.on !== "boolean") return null;
  const holds = Array.isArray(value.holds) ? value.holds.map(filled).filter((hold): hold is string => hold !== null) : [];
  return { on: value.on, holds, window: filled(value.window) };
}

/** The keep-warm of every launch in a value of `/api/state` or of the topic `sessions`.
 * Null for a body that is not such a value, and for one whose state is not `up`. */
export function parseKeepWarms(data: unknown): KeepWarms | null {
  if (!isTable(data) || data.state !== "up" || !Array.isArray(data.sessions)) return null;
  const found = new Map<string, KeepWarmState | null>();
  for (const entry of data.sessions) {
    if (!isTable(entry)) continue;
    const request = filled(entry.request_id);
    const session = filled(entry.session_id);
    if (request === null || session === null) continue;
    const key = stateKey(request, session);
    if (!found.has(key)) found.set(key, parseKeepWarm(entry.keep_warm));
  }
  return found;
}

/** The keep-warm of `launch`; null with nothing read, or none read for it. */
export function keepWarmStateOf(states: KeepWarms | null, launch: Launch): KeepWarmState | null {
  return states?.get(stateKey(launch.request_id, launch.session_id)) ?? null;
}

/** The opened row's `Keep warm` line: its value and its dim tails. */
export interface KeepWarmLine {
  value: string;
  tails: string[];
}

export const KEEP_WARM_OFF_TAIL = "set at launch; it applies while a session waits on its agents";
export const KEEP_WARM_HOLDS_TAIL = "Keep warm holds the cache until they finish";

/**
 * The `Keep warm` line of a launched row, from launcher's state of the launch: `on`, then
 * `holding for “<hold>”` for the holds it keeps the cache for and why it can't nudge,
 * where launcher says so; `off, and it waits on 2 agents` while it waits uncovered, which a
 * press would change; else `off`, with what keep-warm is for. Null with no state read.
 */
export function keepWarmLine(launch: Launch, state: KeepWarmState | null): KeepWarmLine | null {
  if (state === null) return null;
  if (state.on) {
    const tails: string[] = [];
    if (state.holds.length) tails.push(`holding for ${state.holds.map((hold) => `“${hold}”`).join(", ")}`);
    if (state.window !== null) tails.push(state.window);
    return { value: "on", tails };
  }
  if (caseOf(launch) === "uncovered") {
    return { value: `off, and it waits on ${waited(launch.waits as Waits)}`, tails: [KEEP_WARM_HOLDS_TAIL] };
  }
  return { value: "off", tails: [KEEP_WARM_OFF_TAIL] };
}

// --- a read of another module ---------------------------------------------------------

/**
 * One of another module's routes, read one request at a time, and its topic. `value` is
 * the last answer's, or null when it gave none: a failed read, a refusal, a 404 or a body
 * that is not the route's all leave nothing, so no piece of an older answer stays. A topic
 * value of the route's shape replaces it, and one whose `state` is not `up` leaves nothing,
 * as a failed read does. `onChange` is called after every answer and every event it takes.
 */
export class OtherRead<V> {
  value: V | null = null;
  #read: () => Promise<ApiResult>;
  #parse: (data: unknown) => V | null;
  #onChange: () => void;
  #running: Promise<void> | null = null;
  #again = false;
  #closed = false;

  constructor(read: () => Promise<ApiResult>, parse: (data: unknown) => V | null, onChange: () => void) {
    this.#read = read;
    this.#parse = parse;
    this.#onChange = onChange;
  }

  /** Stops every change: a later answer or event is dropped. */
  close(): void {
    this.#closed = true;
  }

  /** Reads the route; a call while a read runs reads once more after it. */
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

  /** One topic event: a value of the route's shape replaces the one held, and a table
   * whose `state` is not `up` (the module could not say) leaves nothing. Any other value
   * is dropped. */
  topic(data: unknown): void {
    if (this.#closed) return;
    const value = this.#parse(data);
    if (value === null && !(isTable(data) && "state" in data && data.state !== "up")) return;
    this.value = value;
    this.#onChange();
  }

  async #once(): Promise<void> {
    if (this.#closed) return;
    let answer: ApiResult;
    try {
      answer = await this.#read();
    } catch {
      answer = { status: 0, data: null };
    }
    if (this.#closed) return;
    this.value = answer.status === 200 ? this.#parse(answer.data) : null;
    this.#onChange();
  }
}

// --- a press of `Keep warm` -------------------------------------------------------------

/** The presses of `Keep warm` the page waits on, by request id, each true once the route
 * has answered it; and the note each refused one shows, by request id. */
export interface Presses {
  waiting: Readonly<Record<string, boolean>>;
  notes: Readonly<Record<string, string>>;
}

/** No press waiting and no note. */
export const NO_PRESSES: Presses = { waiting: {}, notes: {} };

/** The body a press posts to `KEEP_WARM_PATH`. */
export function pressBody(requestId: string): { request_id: string } {
  return { request_id: requestId };
}

/** The note a press's answer leaves on its row: null when the route did not refuse it,
 * `Not kept warm: ` and the refusal's `problem` when it did, and a line of its own for an
 * answer that is not the route's. */
export function pressNote(answer: ApiResult): string | null {
  const data = answer.data;
  if (answer.status === 200 && isTable(data) && "refused" in data) {
    if (data.refused === null) return null;
    if (isTable(data.refused) && typeof data.refused.problem === "string" && data.refused.problem) {
      return `${NOT_KEPT}${data.refused.problem}`;
    }
  }
  if (answer.status === 0) return `${NOT_KEPT}no answer from the shell`;
  return answer.status === 200 ? `${NOT_KEPT}the answer could not be read`
    : `${NOT_KEPT}the shell answered ${answer.status}`;
}

function without<T>(table: Readonly<Record<string, T>>, request: string): Record<string, T> {
  return Object.fromEntries(Object.entries(table).filter(([key]) => key !== request));
}

/** `request` pressed: it waits, not yet answered, and its old note goes. */
export function pressed(presses: Presses, request: string): Presses {
  return { waiting: { ...presses.waiting, [request]: false }, notes: without(presses.notes, request) };
}

/** The route answered `request`'s press, with `note` (see `pressNote`). A press that has
 * stopped waiting still takes its note. */
export function answered(presses: Presses, request: string, note: string | null): Presses {
  const waiting = request in presses.waiting ? { ...presses.waiting, [request]: true } : presses.waiting;
  const notes = note === null ? presses.notes : { ...presses.notes, [request]: note };
  return { waiting, notes };
}

/** A value of `running` came: every note goes, and a press stops waiting once it was
 * answered, or when the value no longer draws its act (the session is covered, has no act,
 * or has ended). */
export function valueCame(presses: Presses, launched: Launched): Presses {
  const acts = new Set([...launched.values()].filter((launch) => keepWarmOf(launch, true) !== null)
    .map((launch) => launch.request_id));
  const waiting: Record<string, boolean> = {};
  for (const [request, done] of Object.entries(presses.waiting)) {
    if (!done && acts.has(request)) waiting[request] = done;
  }
  return { waiting, notes: {} };
}

/** `request`'s press has waited `PRESS_WAIT_MS`: it stops waiting, and its note stays. */
export function expired(presses: Presses, request: string): Presses {
  return { waiting: without(presses.waiting, request), notes: presses.notes };
}
