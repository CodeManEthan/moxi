// What launcher says of the sessions it runs now, worked out with no DOM so the Node tests
// can drive it. With launcher installed the list reads launcher's `/api/running` and
// follows its topic `running`; the list's model (`list.ts`) draws each entry as a row of
// its running group. Only a launch counts as running: a session started by hand is never
// one of them, however recently it changed.
//
// The words are launcher's own, as Home's running rows say them, and a test holds them
// equal: where a launch runs (`slot 3 · atlas-api`), its cache fact, and the three cases.
// A session that waits on agents or subagents while its cache is warm is `covered` when
// keep-warm will nudge it before the cache goes cold, and `uncovered` when nothing will:
// that one is loud, and leads the group. Every other session is `ordinary`.
//
// An uncovered session that a press would cover (`coverable`) gets `Keep warm` at the
// desk. A press posts the launch's request id to launcher's `KEEP_WARM_PATH` and waits,
// the button disabled, for the route's answer and the next value of `running` after it,
// or for `PRESS_WAIT_MS`. A refusal's words are the row's note until the next value.
//
// A session's transcript asks launcher once whether a launch holds the session
// (`statePath`). Only launcher's refusal `not-launched` says it was started by hand; any
// other answer, a failed read too, says nothing.
//
// Every string here goes into the page as text.

/** Where a running launch runs. `folder` is its directory's last segment, never a path. */
export interface Where {
  target: string;
  slot: string | null;
  project_key: string | null;
  folder: string;
}

/** A running launch's cache: `until` is when a warm one goes cold. */
export interface Cache {
  state: "warm" | "cold";
  until: string | null;
  estimated: boolean;
}

/** What a running launch waits on while its own turn is closed. */
export interface Waits {
  kind: "agents" | "subagents";
  count: number;
}

/** The latest message in a running launch's session. */
export interface Activity {
  at: string;
  by: "you" | "agent" | null;
  text: string | null;
}

/** One launch running now, as launcher's `/api/running` and topic `running` send it. */
export interface RunningEntry {
  request_id: string;
  harness: string;
  session_id: string | null;
  name: string;
  identity: string | null;
  where: Where;
  started_at: string;
  cache: Cache | null;
  waits: Waits | null;
  covered: boolean;
  /** At most `cap` nudges in a row a press would send; null where a press would not cover it. */
  coverable: { cap: number } | null;
  activity: Activity | null;
}

/** The value of launcher's `/api/running` and of its topic `running`. */
export interface Running {
  state: "up" | "error";
  detail: string | null;
  count: number;
  sessions: RunningEntry[];
}

/** What the list knows of launcher's running launches: the last value read, or why the
 * last read failed. Null where nothing was read, or launcher is not there. */
export type RunningRead = { value: Running; failure: null } | { value: null; failure: string };

/** What a route answered, as the ui client's `api` gives it: `status` 0 when no answer
 * came, `data` the parsed body. */
export interface Answer {
  status: number;
  data: unknown;
}

/** The presses of `Keep warm` the list waits on, by request id, each true once the route
 * has answered it; and the note each refused one shows, by request id. */
export interface Presses {
  waiting: Readonly<Record<string, boolean>>;
  notes: Readonly<Record<string, string>>;
}

/** The read of launcher's state of one session, relative to launcher's page. */
export function statePath(session: string): string {
  return `api/state?${new URLSearchParams({ session })}`;
}

/** Whether launcher's answer to `statePath` refuses the session as `not-launched`: no
 * launch of Moxi's holds it. */
export function notLaunched(answer: Answer): boolean {
  const data = answer.data;
  return answer.status === 200 && isTable(data) && data.state === "up" && isTable(data.refused)
    && data.refused.code === "not-launched";
}

/** The route the list reads the running launches from, relative to launcher's page. */
export const RUNNING_PATH = "api/running";
/** The topic of launcher's stream that carries the same value. */
export const RUNNING_TOPIC = "running";
/** The route a press of `Keep warm` posts to, relative to launcher's page. */
export const KEEP_WARM_PATH = "api/keep-warm";
/** How long a pressed `Keep warm` stays disabled at most, in milliseconds. */
export const PRESS_WAIT_MS = 20_000;
/** No press waiting and no note. */
export const NO_PRESSES: Presses = { waiting: {}, notes: {} };
/** How each note a press leaves begins. */
export const NOT_KEPT = "Not kept warm: ";
export const KEEP_WARM = "Keep warm";

function isTable(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function parseCache(value: unknown): Cache | null {
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

function parseActivity(value: unknown): Activity | null {
  if (!isTable(value) || typeof value.at !== "string") return null;
  const by = value.by === "you" || value.by === "agent" ? value.by : null;
  return { at: value.at, by, text: text(value.text) };
}

function parseEntry(value: unknown): RunningEntry | null {
  if (!isTable(value) || typeof value.request_id !== "string" || typeof value.name !== "string") return null;
  const where = isTable(value.where) ? value.where : {};
  return {
    request_id: value.request_id,
    harness: text(value.harness) ?? "",
    session_id: text(value.session_id),
    name: value.name,
    identity: text(value.identity),
    where: { target: text(where.target) ?? "", slot: text(where.slot), project_key: text(where.project_key),
             folder: text(where.folder) ?? "" },
    started_at: text(value.started_at) ?? "",
    cache: parseCache(value.cache),
    waits: parseWaits(value.waits),
    covered: value.covered === true,
    coverable: parseCoverable(value.coverable),
    activity: parseActivity(value.activity),
  };
}

/** The route's or the topic's value, or null for anything else. An entry that is not one
 * is left out. */
export function parseRunning(data: unknown): Running | null {
  if (!isTable(data) || (data.state !== "up" && data.state !== "error") || !Array.isArray(data.sessions)) return null;
  const sessions = data.sessions.map(parseEntry).filter((entry): entry is RunningEntry => entry !== null);
  const count = typeof data.count === "number" && Number.isInteger(data.count) ? data.count : sessions.length;
  return { state: data.state, detail: text(data.detail), count: Math.max(count, sessions.length), sessions };
}

/**
 * What a read of `/api/running` leaves the list knowing: the value it brought, or why it
 * failed. A 404 is launcher gone, and leaves nothing: the list then draws no running
 * group at all.
 */
export function runningRead(answer: Answer): RunningRead | null {
  if (answer.status === 404) return null;
  const value = answer.status === 200 ? parseRunning(answer.data) : null;
  if (value !== null && value.state === "up") return { value, failure: null };
  let why: string;
  if (value !== null) why = value.detail ?? "launcher hit an error";
  else if (answer.status === 0) why = "no answer from the shell";
  else if (answer.status === 200) why = "launcher's page server sent an answer that can't be read";
  else why = `launcher's page server answered ${answer.status}`;
  return { value: null, failure: why.replace(/\.$/, "") };
}

/** A launcher time as epoch seconds, or null when it is no time. */
export function stampOf(at: string | null): number | null {
  const when = at === null ? NaN : Date.parse(at);
  return Number.isNaN(when) ? null : when / 1000;
}

/** Launcher's three cases: `uncovered` and `covered` wait on agents or subagents with the
 * cache warm; every other session is `ordinary`. */
export function caseOf(entry: RunningEntry): "ordinary" | "covered" | "uncovered" {
  if (entry.waits === null || entry.cache === null || entry.cache.state !== "warm") return "ordinary";
  return entry.covered ? "covered" : "uncovered";
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function waited(waits: Waits): string {
  return waits.kind === "agents" ? plural(waits.count, "agent", "agents") : plural(waits.count, "subagent", "subagents");
}

/**
 * The right end of a running row's first line, and whether it is heavy; null for none:
 * `goes cold at <time>` (uncovered), `kept warm while …` (covered), else `warm until
 * <time>` or `cold`, with `, est.` where launcher marks the time estimated. `clock` words
 * a time of day.
 */
export function factOf(entry: RunningEntry, clock: (at: number) => string): { text: string; heavy: boolean } | null {
  const cache = entry.cache;
  if (cache === null) return null;
  const until = stampOf(cache.until);
  const est = cache.estimated ? ", est." : "";
  switch (caseOf(entry)) {
    case "uncovered":
      return { text: `${until !== null ? `goes cold at ${clock(until)}` : "goes cold"}${est}`, heavy: true };
    case "covered": {
      const waits = entry.waits as Waits;
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
export function leadOf(entry: RunningEntry): string | null {
  return caseOf(entry) === "uncovered" ? `waiting on ${waited(entry.waits as Waits)}` : null;
}

/** Where a session runs, as Home's running rows say it: `slot N`, `terminal` or
 * `headless`, then its project's key, else its folder. */
export function whereText(where: Where): string {
  const runs = where.target === "slot" ? (where.slot ? `slot ${where.slot}` : "slot") : where.target;
  const place = where.project_key || where.folder;
  return [runs, place].filter(Boolean).join(" · ");
}

/** When a session last did something: its latest message, else when it was launched;
 * null when neither is a time. */
export function lastAt(entry: RunningEntry): number | null {
  return stampOf(entry.activity?.at ?? entry.started_at);
}

/** The sessions in the group's order, as Home orders them: the uncovered waiting ones
 * first, the soonest cold first; then the rest, the latest activity first. Ties keep the
 * order they came in. */
export function ordered(sessions: readonly RunningEntry[]): RunningEntry[] {
  const coldAt = (entry: RunningEntry): number => stampOf(entry.cache?.until ?? null) ?? Infinity;
  const last = (entry: RunningEntry): number => lastAt(entry) ?? -Infinity;
  const loud = sessions.filter((entry) => caseOf(entry) === "uncovered");
  const rest = sessions.filter((entry) => caseOf(entry) !== "uncovered");
  return [...loud.sort((a, b) => coldAt(a) - coldAt(b)), ...rest.sort((a, b) => last(b) - last(a))];
}

/** `Keep warm`'s title, which says what a press costs. */
export function keepWarmTitle(cap: number): string {
  return `Sends a short turn before each cold time while it waits, at most ${cap} in a row.`;
}

/** `Keep warm`'s title for `entry`, where the act is drawn: at the desk, on an uncovered
 * session that a press would cover. Null elsewhere. */
export function keepWarmOf(entry: RunningEntry, desk: boolean): string | null {
  if (!desk || entry.coverable === null || caseOf(entry) !== "uncovered") return null;
  return keepWarmTitle(entry.coverable.cap);
}

// --- a press of `Keep warm` -------------------------------------------------------------------

/** The body a press posts to `KEEP_WARM_PATH`. */
export function pressBody(requestId: string): { request_id: string } {
  return { request_id: requestId };
}

/** The note a press's answer leaves on its row: null when the route did not refuse it,
 * `Not kept warm: ` and the refusal's `problem` when it did, and a line of its own for an
 * answer that is not the route's. */
export function pressNote(answer: Answer): string | null {
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
 * answered, or when the value no longer draws its act (the session is covered, has no
 * act, or has ended). */
export function valueCame(presses: Presses, value: Running): Presses {
  const acts = new Set(value.sessions.filter((entry) => keepWarmOf(entry, true) !== null)
    .map((entry) => entry.request_id));
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
