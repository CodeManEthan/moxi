// What each session's row says on turns' panel, worked out with no DOM so the Node tests
// can read it: the group it goes in, the order inside a group, its name, its since-time,
// its problems and its news, and where each goes on the row's lines. Every word here is
// true in each case the wire value it reads can mean. Every string goes into the page as
// text, never as markup. The clock is passed in, so the tests read the same words in any
// zone and locale.

import type { Agent, Session } from "./feed.ts";

/** How the page reads a moment (epoch seconds): its time of day as shown, and its day. */
export interface Clock {
  /** The hour and minute, as ui shows a time. */
  time(at: number): string;
  /** The hour, minute and second. */
  seconds(at: number): string;
  /** The day, `YYYY-MM-DD`. */
  day(at: number): string;
}

const pad = (n: number): string => String(n).padStart(2, "0");

/** This browser's zone and its own way of writing a time. */
export const LOCAL: Clock = {
  time: (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  seconds: (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit",
                                                                second: "2-digit" }),
  day: (at) => {
    const d = new Date(at * 1000);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },
};

// --- days and times -------------------------------------------------------------------------

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 86_400_000;

/** A day (`YYYY-MM-DD`) as milliseconds at its UTC midnight; NaN when it is not one. */
function stamp(day: string): number {
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? Date.parse(`${day}T00:00:00Z`) : NaN;
}

/** A day against `today`, as the Usage and Comms pages word one: `today`, `yesterday`,
 * `tomorrow`, the weekday within six days either side (`Wed`), else the date (`Nov 3`). */
export function dayWord(day: string, today: string): string {
  const at = stamp(day);
  const off = Math.round((at - stamp(today)) / DAY_MS);
  if (Number.isNaN(off)) return day;
  if (off === 0) return "today";
  if (off === -1) return "yesterday";
  if (off === 1) return "tomorrow";
  const d = new Date(at);
  if (Math.abs(off) <= 6) return WEEKDAYS[d.getUTCDay()];
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** A moment with its day always said: `today 08:20`, `yesterday 21:10`, `Wed 09:02`,
 * `Nov 3 08:52`. */
export function dayTime(at: number, today: string, clock: Clock = LOCAL): string {
  return `${dayWord(clock.day(at), today)} ${clock.time(at)}`;
}

/** A moment: the time alone on `today`'s date, else its day and the time. */
export function timeWord(at: number, today: string, clock: Clock = LOCAL): string {
  return clock.day(at) === today ? clock.time(at) : dayTime(at, today, clock);
}

// --- what a session is ----------------------------------------------------------------------

/** The harness as a person names it: `Claude Code`, `Codex`, `OpenCode`, else as turns
 * names it, and `unknown harness` when turns gives none. */
export function harnessName(harness: string): string {
  const named: Record<string, string> = { claude: "Claude Code", codex: "Codex", opencode: "OpenCode" };
  return named[harness] ?? (harness || "unknown harness");
}

/** Whether only the session's watch is down: its reads answer, so what shows is read on
 * the page server's timer rather than as the session moves. `unread_since` is set for
 * every failed read but the watch's. */
export function isWatchAlone(session: Session): boolean {
  return session.read_error !== null && session.unread_since === null;
}

/** A row's dim line while only its watch is down: what shows comes from the page server's
 * timer, which reads every session again within 30 s. */
export const READ_ON_TIMER = "read every 30 s";

/** Whether the session waits for its first read: nothing read, nothing failed but maybe
 * the watch. It is not drawn, since any group would state a state not read. */
export function isWaiting(session: Session): boolean {
  return session.facts === null && session.trouble === null
    && (session.read_error === null || isWatchAlone(session));
}

/** One problem: a heavy lead and its reason. */
export interface Problem {
  lead: string;
  reason: string;
}

export const TROUBLE_LEAD = "Its link to Moxi has trouble";
export const UNREAD_LEAD = "Moxi can't read its live state";
/** The reason when the live part answered but its agent list was not whole. */
export const LIST_NOT_WHOLE = "its agent list could not be read whole";
/** The reason when the session did not answer the live read in time. */
export const NO_ANSWER_IN_TIME = "the session did not answer in time";
/** The reason when the session's live read failed. */
export const LIVE_READ_FAILED = "the session could not be asked";

/** Why the live part did not read, by the facts' `live_error`: the session did not answer
 * in time, its live read failed, or, when it answered, its agent list was not whole. */
export function liveReason(liveError: string | null): string {
  if (liveError === "timeout") return NO_ANSWER_IN_TIME;
  if (liveError === "failed") return LIVE_READ_FAILED;
  return LIST_NOT_WHOLE;
}

/** The session's problems, in order: the endpoint's trouble, a failed read with why it
 * failed, the live part that did not read (`facts.agents` null) with why. A watch that is
 * down while the reads answer is not one. */
export function problemsOf(session: Session): Problem[] {
  const found: Problem[] = [];
  if (session.trouble !== null) found.push({ lead: TROUBLE_LEAD, reason: session.trouble });
  if (session.read_error !== null && !isWatchAlone(session)) {
    found.push({ lead: UNREAD_LEAD, reason: session.read_error });
  }
  if (session.facts !== null && session.facts.agents === null) {
    found.push({ lead: UNREAD_LEAD, reason: liveReason(session.facts.live_error ?? null) });
  }
  return found;
}

export type Group = "problem" | "idle" | "working";
export const GROUPS: readonly Group[] = ["problem", "idle", "working"];

/** The first group that holds for the session, or null while it waits for its first read. */
export function groupOf(session: Session): Group | null {
  if (problemsOf(session).length > 0) return "problem";
  if (session.facts === null) return null;
  return session.facts.turn_open ? "working" : "idle";
}

/** Whether a turn is open, from the facts; null without them. */
export function stateOf(session: Session): "working" | "idle" | null {
  if (session.facts === null) return null;
  return session.facts.turn_open ? "working" : "idle";
}

/** Whether the last ready was raised for the session id the reading was taken under. */
export function isReadyOfId(session: Session): boolean {
  const facts = session.facts;
  const ready = facts?.ready ?? null;
  return facts !== null && ready !== null && ready.session_id !== null
    && ready.session_id === facts.session_id;
}

/**
 * Since when the session has been in its state, a moment turns saw, or null.
 *
 * Working: the open turn's start. Idle: `ended_at` when it is sent, or the later of it and
 * the ready's time when the ready is of the current id; else the ready's time when no turn
 * has run since that ready (turn 0, since the count starts again at each new id); else none.
 */
export function sinceOf(session: Session): number | null {
  const facts = session.facts;
  if (facts === null) return null;
  if (facts.turn_open) return facts.turn_started_at;
  const readyAt = isReadyOfId(session) ? facts.ready?.at ?? null : null;
  if (session.ended_at !== null) {
    return readyAt === null ? session.ended_at : Math.max(session.ended_at, readyAt);
  }
  return facts.turn_number === 0 ? readyAt : null;
}

/** Rows in a group's order: the latest change first, by since-time; rows with none after
 * them, the newest `started` first; ties by `key`. */
export function ordered(sessions: readonly Session[]): Session[] {
  const later = (a: number | null, b: number | null): number =>
    a === b ? 0 : a === null ? 1 : b === null ? -1 : b - a;
  return [...sessions].sort((a, b) =>
    later(sinceOf(a), sinceOf(b)) || later(a.started, b.started)
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
}

/** One drawn group: its kind, its dim label and its rows in order. */
export interface GroupView {
  group: Group;
  label: string;
  sessions: Session[];
}

/** The label of a group of `n` rows: `1 problem`, `2 problems`, `3 idle`, `1 working`. */
export function groupLabel(group: Group, n: number): string {
  if (group === "problem") return n === 1 ? "1 problem" : `${n} problems`;
  return `${n} ${group}`;
}

/** The groups that have a row, in the panel's order; a session that waits is left out. */
export function groupsOf(sessions: readonly Session[]): GroupView[] {
  const views: GroupView[] = [];
  for (const group of GROUPS) {
    const rows = ordered(sessions.filter((session) => groupOf(session) === group));
    if (rows.length > 0) views.push({ group, label: groupLabel(group, rows.length), sessions: rows });
  }
  return views;
}

// --- the name and the fact ------------------------------------------------------------------

/** The last segment of a folder: `kestrel-web` of `/work/kestrel-web/`. */
export function lastSegment(cwd: string): string {
  const parts = cwd.split(/[\\/]/).filter(Boolean);
  return parts.length === 0 ? cwd : parts[parts.length - 1];
}

/** The row's name by its folder: `kestrel-web · started today 08:20`, the segment alone
 * with no `started`; with no `cwd`, `Claude Code session`, or `A session` when the harness
 * is not named. */
export function folderName(session: Session, today: string, clock: Clock = LOCAL): string {
  if (session.cwd === null || session.cwd === "") {
    return session.harness ? `${harnessName(session.harness)} session` : "A session";
  }
  const segment = lastSegment(session.cwd);
  return session.started === null ? segment : `${segment} · started ${dayTime(session.started, today, clock)}`;
}

/** Line one's fact. On a problem row its state: `idle since 17:35`, `working since 18:52`,
 * `idle` without a since-time, nothing without facts. On another row `since 18:52`, or
 * nothing, since the group label says the state. */
export function factOf(session: Session, group: Group, today: string, clock: Clock = LOCAL): string | null {
  const since = sinceOf(session);
  const when = since === null ? null : `since ${timeWord(since, today, clock)}`;
  if (group !== "problem") return when;
  const state = stateOf(session);
  if (state === null) return null;
  return when === null ? state : `${state} ${when}`;
}

// --- the news -------------------------------------------------------------------------------

/** turns' own busy rule: no final notice yet, and a state that is still going. */
export function isBusy(agent: Agent): boolean {
  return agent.final !== true && ["pending", "running", "waiting"].includes(agent.state);
}

/** One dim phrase of news, with the description of its one entry when it has one. */
export interface News {
  text: string;
  description: string | null;
}

/** The news, and the `Prompt box` line, for a draft in the box. */
export const DRAFT_NOT_SENT = "draft not sent";

const KINDS: readonly { kind: string; one: string; many: string }[] = [
  { kind: "subagent", one: "subagent busy", many: "subagents busy" },
  { kind: "teammate", one: "teammate busy", many: "teammates busy" },
  { kind: "shell", one: "shell running", many: "shells running" },
];

/** The session's news, each only when true, in the panel's order: busy subagents,
 * teammates, running shells, the busy count when there is no list, and `draft not sent`
 * when turns reads its box and read a draft in it. A box that was not read (`draft` null)
 * is no news here; the opened row says it. */
export function newsOf(session: Session): News[] {
  const news: News[] = [];
  const facts = session.facts;
  if (session.agents !== null) {
    for (const { kind, one, many } of KINDS) {
      const busy = session.agents.entries.filter((agent) => agent.kind === kind && isBusy(agent));
      if (busy.length === 1) news.push({ text: `1 ${one}`, description: busy[0].description || null });
      else if (busy.length > 1) news.push({ text: `${busy.length} ${many}`, description: null });
    }
  } else if (facts !== null && facts.agents !== null && facts.agents > 0) {
    news.push({ text: `${facts.agents} agents busy`, description: null });
  }
  if (session.has_box && facts?.draft === true) news.push({ text: DRAFT_NOT_SENT, description: null });
  return news;
}

/** A phrase with its description after it: `1 subagent busy · Check the plan`. */
export function newsText(news: News): string {
  return news.description === null ? news.text : `${news.text} · ${news.description}`;
}

// --- the row's lines ------------------------------------------------------------------------

/** The text attributes of a session's `<moxi-ui-row>`; null leaves one unset. */
export interface RowLines {
  name: string;
  where: string | null;
  fact: string | null;
  lead: string | null;
  leadHeavy: boolean;
  rest: string | null;
  restStrong: boolean;
  tail: string | null;
  note: string | null;
}

/** A launch's name and where, when launcher's entry pairs with the row. */
export interface Launch {
  name: string;
  where: string | null;
}

/** The row's name: the name typed in the harness, else the launch's, else the folder and
 * start time. */
export function nameOf(session: Session, today: string, launch: Launch | null = null,
                       clock: Clock = LOCAL): string {
  return session.typed_name || launch?.name || folderName(session, today, clock);
}

/**
 * The row's lines. A problem row: line two is the first problem's lead (heavy) and its
 * reason in the text colour; line three is a second problem's lead and reason, then the
 * news. Another row: line two leads with the first news phrase, its description as the
 * rest, the other phrases as the tail; a row with no news is one line. While only the
 * session's watch is down, line three ends with `read every 30 s`.
 */
export function rowLines(session: Session, group: Group, today: string, launch: Launch | null = null,
                         clock: Clock = LOCAL): RowLines {
  const lines: RowLines = {
    name: nameOf(session, today, launch, clock),
    where: launch?.where || null,
    fact: factOf(session, group, today, clock),
    lead: null, leadHeavy: false, rest: null, restStrong: false, tail: null, note: null,
  };
  const news = newsOf(session);
  const timer = isWatchAlone(session) ? [READ_ON_TIMER] : [];
  if (group === "problem") {
    const [first, ...others] = problemsOf(session);
    if (first !== undefined) {
      Object.assign(lines, { lead: first.lead, leadHeavy: true, rest: first.reason, restStrong: true });
    }
    const note = [...others.map((problem) => `${problem.lead} · ${problem.reason}`), ...news.map(newsText),
                  ...timer];
    lines.note = note.length > 0 ? note.join(" · ") : null;
    return lines;
  }
  const [first, ...others] = news;
  if (first !== undefined) {
    lines.lead = first.text;
    lines.rest = first.description;
    lines.tail = others.length > 0 ? others.map(newsText).join(" · ") : null;
  }
  lines.note = timer[0] ?? null;
  return lines;
}
