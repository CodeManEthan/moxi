// What Home's Sessions row says of the launches running now, worked out with no DOM so the
// Node tests can drive it. running.ts draws it.
//
// One entry gives one of three cases. A session that waits on agents or subagents while
// its cache is warm is `covered` when keep-warm will nudge it before the cache goes cold,
// and `uncovered` when nothing will: that one is loud, wherever its cold time is. Every
// other session is `ordinary`, among them a waiting session whose cache is already cold
// or was not read.
//
// The row's line counts the sessions. An uncovered one, closed at the desk, takes the
// line's fact and the count moves to the right end. Opened, the list shows the first four
// in order (uncovered first, the soonest cold first; then the latest activity first) and
// a link to all of them.
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

/** The latest message in a running launch's session. `text` is null on the phone. */
export interface Activity {
  at: string;
  by: "you" | "agent" | null;
  text: string | null;
}

/** One launch running now, as `/api/running` and the topic `running` send it. */
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
  activity: Activity | null;
}

/** The value of `/api/running` and of the topic `running`. */
export interface Running {
  state: "up" | "error";
  detail: string | null;
  count: number;
  sessions: RunningEntry[];
}

export type Case = "ordinary" | "covered" | "uncovered";

/** The row's line, as the `detail` of a `moxi-home-row` event. */
export interface RowReport {
  parts: Array<{ text: string; heavy?: boolean }>;
  time?: string;
  needs: number;
  opens: true;
  open?: true;
}

/** What one session's `<moxi-ui-row>` carries: each attribute, null when it is not set. */
export interface RowAttrs {
  name: string;
  where: string | null;
  fact: string | null;
  factHeavy: boolean;
  lead: string | null;
  leadHeavy: boolean;
  time: string | null;
  saidBy: "you" | null;
  rest: string | null;
}

/** Where a link goes: `navigate(module, route)`. */
export interface Place {
  module: string;
  route: string;
}

/** The most sessions the opened list shows. */
export const SHOWN = 4;
/** On Home's needs scale: something waits for the user. */
export const WAITING = 20;

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
    activity: parseActivity(value.activity),
  };
}

/** The route's or the topic's value, or null for anything else. An entry that is not one
 * is left out. */
export function parseRunning(data: unknown): Running | null {
  if (!isTable(data) || (data.state !== "up" && data.state !== "error") || !Array.isArray(data.sessions)) {
    return null;
  }
  const sessions = data.sessions.map(parseEntry).filter((entry): entry is RunningEntry => entry !== null);
  const count = typeof data.count === "number" && Number.isInteger(data.count) ? data.count : sessions.length;
  return { state: data.state, detail: text(data.detail), count: Math.max(count, sessions.length), sessions };
}

/** `at` as the time of day ui shows; "" when it is no time. */
export function clock(at: string | null): string {
  const when = at === null ? NaN : Date.parse(at);
  if (Number.isNaN(when)) return "";
  return new Date(when).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Which of the three cases `entry` is. */
export function caseOf(entry: RunningEntry): Case {
  if (entry.waits === null || entry.cache === null || entry.cache.state !== "warm") return "ordinary";
  return entry.covered ? "covered" : "uncovered";
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function waited(waits: Waits): string {
  return waits.kind === "agents" ? plural(waits.count, "agent", "agents") : plural(waits.count, "subagent", "subagents");
}

function estimated(cache: Cache): string {
  return cache.estimated ? ", est." : "";
}

/** `goes cold at HH:MM`, or `goes cold` when the time can't be read. */
function goesCold(cache: Cache): string {
  const at = clock(cache.until);
  return at ? `goes cold at ${at}` : "goes cold";
}

/** The right end of a session's first line, and whether it is heavy; null for none. With
 * `short`, the phone's: the covered case says only `kept warm`. */
export function factOf(entry: RunningEntry, short = false): { text: string; heavy: boolean } | null {
  const cache = entry.cache;
  switch (caseOf(entry)) {
    case "uncovered":
      return { text: `${goesCold(cache as Cache)}${estimated(cache as Cache)}`, heavy: true };
    case "covered": {
      const waits = entry.waits as Waits;
      if (short) return { text: "kept warm", heavy: false };
      const work = waits.kind === "agents"
        ? `${plural(waits.count, "agent", "agents")} ${waits.count === 1 ? "works" : "work"}`
        : `${plural(waits.count, "subagent", "subagents")} ${waits.count === 1 ? "runs" : "run"}`;
      return { text: `kept warm while ${work}`, heavy: false };
    }
    default: {
      if (cache === null) return null;
      if (cache.state === "cold") return { text: "cold", heavy: false };
      const at = clock(cache.until);
      return { text: `${at ? `warm until ${at}` : "warm"}${estimated(cache)}`, heavy: false };
    }
  }
}

/** What leads a session's second line: `waiting on n agents` in the uncovered case only. */
export function leadOf(entry: RunningEntry): string | null {
  return caseOf(entry) === "uncovered" ? `waiting on ${waited(entry.waits as Waits)}` : null;
}

/** Where a session runs, as its row says it: `slot N`, `terminal` or `headless`, then its
 * project's key, else its folder. */
export function whereText(where: Where): string {
  const runs = where.target === "slot" ? (where.slot ? `slot ${where.slot}` : "slot") : where.target;
  const place = where.project_key || where.folder;
  return [runs, place].filter(Boolean).join(" · ");
}

function stamp(at: string | null): number {
  const when = at === null ? NaN : Date.parse(at);
  return Number.isNaN(when) ? -Infinity : when;
}

/** When a session last did something: its latest message, else when it was launched. */
function lastAt(entry: RunningEntry): number {
  return stamp(entry.activity?.at ?? entry.started_at);
}

/** When an uncovered session goes cold; one with no readable time sorts after the rest. */
function coldAt(entry: RunningEntry): number {
  const at = stamp(entry.cache?.until ?? null);
  return at === -Infinity ? Infinity : at;
}

/** The sessions in the list's order: the uncovered waiting ones first, the soonest cold
 * first; then the rest, the latest activity first, a session with none by when it was
 * launched. Ties keep the order they came in. */
export function ordered(sessions: RunningEntry[]): RunningEntry[] {
  const loud = sessions.filter((entry) => caseOf(entry) === "uncovered");
  const rest = sessions.filter((entry) => caseOf(entry) !== "uncovered");
  return [...loud.sort((a, b) => coldAt(a) - coldAt(b)), ...rest.sort((a, b) => lastAt(b) - lastAt(a))];
}

/** The line Home draws for the row: null when nothing runs (or the read failed, which
 * leaves nothing to count). `desk` is false on the phone, where the row is always open and
 * the count sits beside the label; `open` is whether Home has it opened at the desk. */
export function rowReport(value: Running | null, { desk, open }: { desk: boolean; open: boolean }): RowReport | null {
  if (value === null || value.state !== "up" || value.count === 0) return null;
  const count = `${value.count} running`;
  const loud = ordered(value.sessions).find((entry) => caseOf(entry) === "uncovered");
  const needs = loud ? WAITING : 0;
  if (!desk) return { parts: [], time: count, needs, opens: true, open: true };
  if (loud && !open) {
    return { parts: [{ text: `${loud.identity ?? loud.name} ${goesCold(loud.cache as Cache)}`, heavy: true }],
             time: count, needs, opens: true };
  }
  return { parts: [{ text: count }], needs, opens: true };
}

/** One session's row. At the desk: its name, where, the case's fact and lead, the time of
 * its latest message (else of its launch) and that message, or `launched`. On the phone
 * (`desk` false): its name, the fact cut short, the lead, and where it runs on line two,
 * with no time and no message. */
export function rowAttrs(entry: RunningEntry, desk: boolean): RowAttrs {
  const fact = factOf(entry, !desk);
  const lead = leadOf(entry);
  const base = { name: entry.name, fact: fact?.text ?? null, factHeavy: fact?.heavy ?? false, lead,
                 leadHeavy: lead !== null };
  if (!desk) return { ...base, where: null, time: null, saidBy: null, rest: whereText(entry.where) || null };
  const activity = entry.activity;
  return {
    ...base,
    where: whereText(entry.where) || null,
    time: clock(activity?.at ?? entry.started_at) || null,
    saidBy: activity?.by === "you" ? "you" : null,
    rest: activity ? activity.text || null : "launched",
  };
}

/** The rows the opened list shows: the first `SHOWN` in order. */
export function shownSessions(value: Running | null): RunningEntry[] {
  return value === null ? [] : ordered(value.sessions).slice(0, SHOWN);
}

/** The link under the list. */
export function allLabel(value: Running): string {
  return `All sessions (${value.count})`;
}

/** Where `Open` goes: with sessions installed, the session's transcript on its page, by
 * the route that page takes (`<harness>/<session id>`, each part encoded); else,
 * or for a launch with no session id, the launcher's page. */
export function openPlace(entry: RunningEntry, sessions: boolean): Place {
  if (sessions && entry.session_id) {
    return { module: "sessions", route: [entry.harness, entry.session_id].map(encodeURIComponent).join("/") };
  }
  return { module: "launcher", route: "" };
}

/** Where `All sessions` goes: sessions' list, else the launcher's page. */
export function allPlace(sessions: boolean): Place {
  return sessions ? { module: "sessions", route: "" } : { module: "launcher", route: "" };
}

/** A place as a link's `href`. */
export function hrefOf(place: Place): string {
  return `/m/${place.module}/${place.route}`;
}
