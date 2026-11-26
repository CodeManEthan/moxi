// What launcher says of the sessions it runs, joined to the roster's rows, worked out with
// no DOM so the Node tests can drive it. With launcher installed the page reads its
// `/api/running`; each running entry joins the bound roster row with its session id, else
// the bound row whose address is its identity. The joined address then takes launcher's
// words: where it runs, its cache fact (which wins over comms', so Home and this page say
// the same), the `Session` line, and launcher's three cache cases. A session that waits on
// agents, subagents or open holds while its cache is warm is `covered` when keep-warm will
// nudge it before the cache goes cold, and `uncovered` when nothing will: that one is loud.
//
// An uncovered session that a press would cover (`coverable`) gets `Keep warm`. A press
// posts its request id to launcher's `KEEP_WARM_PATH` and waits, the button disabled, for
// the route's answer and the next value of `running` after it, or for `PRESS_WAIT_MS`. A
// refusal's words lead the row's line three until the next value. The words, the path and
// the wait are launcher's own (its Home row's), and a test holds them equal.
//
// Launcher's cache times are date strings, not epoch seconds: each is parsed as a date and
// worded by the page's `when`, never mixed with comms' own `until` in one fact.
//
// The `Session` line carries the model and effort the launch was started with, where
// launcher gives them. A covered session's `Cache` line says when its next keep-alive comes:
// keep-warm's first ask about a session is `KEEP_ALIVE_LEAD_S` before its cache goes cold,
// and a covered session is one that ask would nudge. Once that time has passed the line says
// nothing of it, since the ask is then already under way.
//
// Every string here goes into the page as text.

import type { RosterRow } from "./feed.ts";

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

/** What a running launch waits on while its own turn is closed: subagents, else agents it
 * directs, else its open holds. */
export interface Waits {
  kind: "agents" | "subagents" | "holds";
  count: number;
}

/** One launch running now, as launcher's `/api/running` sends it: the fields the page reads. */
export interface RunningEntry {
  request_id: string;
  harness: string;
  session_id: string | null;
  name: string;
  identity: string | null;
  where: Where;
  /** The model and effort the launch was started with; null where it set none. */
  model: string | null;
  effort: string | null;
  cache: Cache | null;
  waits: Waits | null;
  covered: boolean;
  /** At most `cap` nudges in a row a press would send; null where a press would not cover it. */
  coverable: { cap: number } | null;
}

/** The value of launcher's `/api/running`. */
export interface Running {
  state: "up" | "error";
  detail: string | null;
  sessions: RunningEntry[];
}

/** What the route answered a press, as the ui client's `api` gives it: `status` 0 when no
 * answer came, `data` the parsed body. */
export interface Answer {
  status: number;
  data: unknown;
}

/** The presses of `Keep warm` the page waits on, by request id, each true once the route
 * has answered it; and the note each refused one shows, by request id. */
export interface Presses {
  waiting: Readonly<Record<string, boolean>>;
  notes: Readonly<Record<string, string>>;
}

/** The route a press of `Keep warm` posts to, relative to launcher's page. */
export const KEEP_WARM_PATH = "api/keep-warm";
/** The route the page reads the running launches from, relative to launcher's page. */
export const RUNNING_PATH = "api/running";
/** How long a pressed `Keep warm` stays disabled at most, in milliseconds. */
export const PRESS_WAIT_MS = 20_000;
/** How long before a session's cache goes cold keep-warm first asks whether to nudge it, in
 * seconds: launcher's keep-warm timer opens there. */
export const KEEP_ALIVE_LEAD_S = 900;
/** No press waiting and no note. */
export const NO_PRESSES: Presses = { waiting: {}, notes: {} };
/** How each note a press leaves begins. */
export const NOT_KEPT = "Not kept warm: ";
export const SESSION_LABEL = "Session";
export const SESSIONS_LINK = "Sessions";

function isTable(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function word(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function parseCache(value: unknown): Cache | null {
  if (!isTable(value) || (value.state !== "warm" && value.state !== "cold")) return null;
  return { state: value.state, until: text(value.until), estimated: value.estimated === true };
}

function parseWaits(value: unknown): Waits | null {
  if (!isTable(value) || (value.kind !== "agents" && value.kind !== "subagents" && value.kind !== "holds")) {
    return null;
  }
  const count = typeof value.count === "number" && Number.isInteger(value.count) ? value.count : 0;
  return count > 0 ? { kind: value.kind, count } : null;
}

function parseCoverable(value: unknown): { cap: number } | null {
  if (!isTable(value) || typeof value.cap !== "number" || !Number.isInteger(value.cap) || value.cap < 1) return null;
  return { cap: value.cap };
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
    model: word(value.model),
    effort: word(value.effort),
    cache: parseCache(value.cache),
    waits: parseWaits(value.waits),
    covered: value.covered === true,
    coverable: parseCoverable(value.coverable),
  };
}

/** The route's value, or null for anything else. An entry that is not one is left out. */
export function parseRunning(data: unknown): Running | null {
  if (!isTable(data) || (data.state !== "up" && data.state !== "error") || !Array.isArray(data.sessions)) return null;
  const sessions = data.sessions.map(parseEntry).filter((entry): entry is RunningEntry => entry !== null);
  return { state: data.state, detail: text(data.detail), sessions };
}

/** A launcher time as epoch seconds, or null when it is no time. */
export function stampOf(at: string | null): number | null {
  const when = at === null ? NaN : Date.parse(at);
  return Number.isNaN(when) ? null : when / 1000;
}

/** Launcher's three cases: `uncovered` and `covered` wait on agents, subagents or open
 * holds with the cache warm; every other session is `ordinary`. */
export function caseOf(entry: RunningEntry): "ordinary" | "covered" | "uncovered" {
  if (entry.waits === null || entry.cache === null || entry.cache.state !== "warm") return "ordinary";
  return entry.covered ? "covered" : "uncovered";
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function waited(waits: Waits): string {
  if (waits.kind === "holds") return plural(waits.count, "open hold", "open holds");
  return waits.kind === "agents" ? plural(waits.count, "agent", "agents") : plural(waits.count, "subagent", "subagents");
}

/** `kept warm while <n> agent(s) work(s)` (or subagents run, or holds are open), launcher's
 * words for a covered session. */
export function keptWords(waits: Waits): string {
  const one = waits.count === 1;
  const work = waits.kind === "holds"
    ? `${plural(waits.count, "hold", "holds")} ${one ? "is" : "are"} open`
    : waits.kind === "agents"
      ? `${plural(waits.count, "agent", "agents")} ${one ? "works" : "work"}`
      : `${plural(waits.count, "subagent", "subagents")} ${one ? "runs" : "run"}`;
  return `kept warm while ${work}`;
}

/** The fact launcher gives a session, and whether it is heavy; null for none. `goes cold at
 * <time>` (uncovered), `kept warm while …` (covered), `warm until <time>` or `cold`, with
 * `, est.` where launcher marks the time estimated. */
export function factOf(entry: RunningEntry, when: (at: number) => string): { text: string; heavy: boolean } | null {
  const cache = entry.cache;
  if (cache === null) return null;
  const until = stampOf(cache.until);
  const est = cache.estimated ? ", est." : "";
  switch (caseOf(entry)) {
    case "uncovered":
      return { text: `${until !== null ? `goes cold at ${when(until)}` : "goes cold"}${est}`, heavy: true };
    case "covered":
      return { text: keptWords(entry.waits as Waits), heavy: false };
    default:
      if (cache.state === "cold") return { text: "cold", heavy: false };
      return { text: `${until !== null ? `warm until ${when(until)}` : "warm"}${est}`, heavy: false };
  }
}

/** What leads line two in the uncovered case: `waiting on <n> agents`; null otherwise. */
export function waitsWords(entry: RunningEntry): string | null {
  return caseOf(entry) === "uncovered" ? `waiting on ${waited(entry.waits as Waits)}` : null;
}

/** Where a session runs, as Home's running rows say it: `slot N`, `terminal` or
 * `headless`, then its project's key, else its folder. */
export function whereText(where: Where): string {
  const runs = where.target === "slot" ? (where.slot ? `slot ${where.slot}` : "slot") : where.target;
  const place = where.project_key || where.folder;
  return [runs, place].filter(Boolean).join(" · ");
}

/** `Keep warm`'s title, which says what a press costs. */
export function keepWarmTitle(cap: number): string {
  return `Sends a short turn before each cold time while it waits, at most ${cap} in a row.`;
}

/** The cache line of an opened row, as launcher reads it: its value, and the tail after it
 * (`kept warm while …` when covered). */
export interface LaunchedCache {
  warm: boolean;
  /** When a warm cache goes cold, or a cold one went cold; null when launcher gives none. */
  until: number | null;
  estimated: boolean;
  kept: string | null;
  /** When keep-warm next asks to nudge a covered session, in epoch seconds; null where the
   * session is not covered or launcher gives no time. */
  keepAlive: number | null;
}

/** What launcher says of the launch running at an address. `fact` and `where` are its row's
 * words; `uncovered` is launcher's loud case, with `waits` leading line two; `coverable`
 * draws `Keep warm` with `title`; `session` is the `Session` line, with the model and effort
 * the launch was started with and the route of the Sessions page's transcript when the
 * launch has a session id. */
export interface Launched {
  where: string | null;
  fact: { text: string; heavy: boolean } | null;
  uncovered: boolean;
  waits: string | null;
  coverable: boolean;
  title: string | null;
  request_id: string;
  session: { name: string; harness: string; model: string | null; effort: string | null; route: string | null };
  cache: LaunchedCache | null;
}

/** The Sessions page's route for a session's transcript, `<harness>/<session id>`. */
function sessionRoute(harness: string, sessionId: string | null): string | null {
  return harness && sessionId ? [harness, sessionId].map(encodeURIComponent).join("/") : null;
}

function launchedOf(entry: RunningEntry, row: RosterRow, when: (at: number) => string): Launched {
  const cache = entry.cache;
  const kind = caseOf(entry);
  const until = cache === null ? null : stampOf(cache.until);
  return {
    where: whereText(entry.where) || null,
    fact: factOf(entry, when),
    uncovered: kind === "uncovered",
    waits: waitsWords(entry),
    coverable: kind === "uncovered" && entry.coverable !== null,
    title: kind === "uncovered" && entry.coverable !== null ? keepWarmTitle(entry.coverable.cap) : null,
    request_id: entry.request_id,
    session: { name: entry.name, harness: entry.harness, model: entry.model, effort: entry.effort,
               route: sessionRoute(entry.harness, entry.session_id ?? row.session_id) },
    cache: cache === null ? null : {
      warm: cache.state === "warm", until, estimated: cache.estimated,
      kept: kind === "covered" ? keptWords(entry.waits as Waits) : null,
      keepAlive: kind === "covered" && until !== null ? until - KEEP_ALIVE_LEAD_S : null,
    },
  };
}

/** Each bound address's running launch: an entry joins the bound row with its session id,
 * else the bound row whose address is its identity. A row takes one entry, an entry one
 * row; the session id wins, then the newest launch (launcher sends the newest first). */
export function joinRunning(running: Running | null, rows: readonly RosterRow[], when: (at: number) => string)
  : Map<string, Launched> {
  const joined = new Map<string, Launched>();
  if (running === null || running.state !== "up") return joined;
  const bound = rows.filter((row) => row.live);
  const taken = new Set<string>();
  for (const row of bound) {
    const entry = row.session_id === null ? undefined
      : running.sessions.find((one) => one.session_id === row.session_id && !taken.has(one.request_id));
    if (entry === undefined) continue;
    taken.add(entry.request_id);
    joined.set(row.qualified, launchedOf(entry, row, when));
  }
  for (const row of bound.filter((one) => !joined.has(one.qualified))) {
    const entry = running.sessions.find((one) => one.identity === row.qualified && !taken.has(one.request_id));
    if (entry === undefined) continue;
    taken.add(entry.request_id);
    joined.set(row.qualified, launchedOf(entry, row, when));
  }
  return joined;
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
  const acts = new Set(value.sessions.filter((entry) => caseOf(entry) === "uncovered" && entry.coverable !== null)
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

/** The line of a read of `/api/running` that failed or answered `error`; null otherwise. A
 * 404 is no line: launcher's facts are then absent. */
export function runningLine(status: number, value: Running | null): string | null {
  let why: string | null = null;
  if (status === 404) return null;
  if (status === 200 && value !== null) why = value.state === "error" ? value.detail ?? "launcher hit an error" : null;
  else if (status === 0) why = "no answer from the shell";
  else if (status === 200) why = "launcher's page server sent an answer that can't be read";
  else why = `launcher's page server answered ${status}`;
  return why === null ? null : `The running sessions can't be read: ${why.replace(/\.$/, "")}.`;
}
