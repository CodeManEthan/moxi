// What turns says of each running session's live state, joined to the running group's rows,
// worked out with no DOM so the Node tests can drive it. With turns installed the list
// reads turns' `/api/sessions` and follows its topic `sessions`; a running row takes the
// state of the one turns session that carries its session id.
//
// A running row says one state word, `working`, while a turn is open, and nothing while
// the session is idle: its time says since when. After it come what turns counts busy
// (`1 subagent busy`, `2 shells running`). A session turns can't read says so on a dim
// line of its own, in turns' words. Its opened row adds turns' `Now`, `Agents` and
// `Prompt box` lines. A row turns has no session for says nothing of it, and neither does
// any row without turns installed.
//
// The words, and the rules that pick them, are turns' own, as its Live sessions panel says
// them; a test holds them equal where turns' source sits beside sessions'.
//
// Every string here goes into the page as text.

import type { RunningEntry } from "./running.ts";

/** The last `session.ready` an endpoint raised. */
export interface Ready {
  source: string | null;
  session_id: string | null;
  at: number | null;
}

/** One session's facts, as turns' last read gave them. */
export interface Facts {
  /** The session id the reading was taken under. */
  session_id: string | null;
  turn_number: number;
  turn_open: boolean;
  /** When the open turn started; null when no turn is open, or its start is not known. */
  turn_started_at: number | null;
  /** Busy agents; null when they could not be listed. */
  agents: number | null;
  /** On a session with `has_box`: true for a draft, false for an empty box, null when the
   * box was not read. */
  draft: boolean | null;
  ready: Ready | null;
  /** Why the live part did not read: `timeout` (the session did not answer in time) or
   * `failed`; null when it answered. */
  live_error: string | null;
}

/** One subagent, teammate or background shell. */
export interface Agent {
  id: string;
  kind: string;
  state: string;
  final: boolean | null;
  description: string | null;
}

/** One session turns can reach: the fields the list reads. */
export interface LiveSession {
  key: string;
  harness: string;
  session_id: string | null;
  capabilities: string[];
  trouble: string | null;
  facts: Facts | null;
  agents: { entries: Agent[]; complete: boolean } | null;
  read_error: string | null;
  ended_at: number | null;
  has_box: boolean;
  unread_since: number | null;
}

/** The value of turns' `/api/sessions` and of its topic `sessions`. */
export interface Live {
  sessions: LiveSession[];
}

/** What the list knows of turns' sessions: the last value read, or why the last read failed. */
export type LiveRead = { value: Live; failure: null } | { value: null; failure: string };

/** What a route answered, as the ui client's `api` gives it. */
export interface Answer {
  status: number;
  data: unknown;
}

/** The route the list reads turns' sessions from, relative to turns' page. */
export const LIVE_PATH = "api/sessions";
/** The topic of turns' stream that carries the same list. */
export const LIVE_TOPIC = "sessions";

export const WORKING = "working";
export const TROUBLE_LEAD = "Its link to Moxi has trouble";
export const UNREAD_LEAD = "Moxi can't read its live state";
export const LIST_NOT_WHOLE = "its agent list could not be read whole";
export const NO_ANSWER_IN_TIME = "the session did not answer in time";
export const LIVE_READ_FAILED = "the session gave no answer Moxi could use";
export const DRAFT_NOT_SENT = "draft not sent";
export const NOT_KNOWN = "not known · the list could not be read whole, so some may still run";
export const EMPTY_BOX = "empty";
export const BOX_NOT_KNOWN = "not known · it could not be read, so it may hold a draft";
/** The opened row's labels for turns' lines. */
export const LABELS = { now: "Now", agents: "Agents", promptBox: "Prompt box" } as const;

function isTable(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const text = (value: unknown): string | null => (typeof value === "string" ? value : null);
const finite = (value: unknown): number | null =>
  (typeof value === "number" && Number.isFinite(value) ? value : null);

function parseReady(value: unknown): Ready | null {
  if (!isTable(value)) return null;
  return { source: text(value.source), session_id: text(value.session_id), at: finite(value.at) };
}

function parseFacts(value: unknown): Facts | null {
  if (!isTable(value) || typeof value.turn_open !== "boolean") return null;
  const turn = finite(value.turn_number);
  const agents = finite(value.agents);
  return {
    session_id: text(value.session_id),
    turn_number: turn !== null && Number.isInteger(turn) ? turn : 0,
    turn_open: value.turn_open,
    turn_started_at: finite(value.turn_started_at),
    agents: agents !== null && Number.isInteger(agents) ? agents : null,
    draft: typeof value.draft === "boolean" ? value.draft : null,
    ready: parseReady(value.ready),
    live_error: text(value.live_error),
  };
}

function parseAgent(value: unknown): Agent | null {
  if (!isTable(value) || typeof value.id !== "string" || typeof value.kind !== "string"
      || typeof value.state !== "string") return null;
  return { id: value.id, kind: value.kind, state: value.state,
           final: typeof value.final === "boolean" ? value.final : null, description: text(value.description) };
}

function parseSession(value: unknown): LiveSession | null {
  if (!isTable(value) || typeof value.key !== "string") return null;
  const listed = isTable(value.agents) && Array.isArray(value.agents.entries) ? value.agents : null;
  return {
    key: value.key,
    harness: text(value.harness) ?? "",
    session_id: text(value.session_id),
    capabilities: Array.isArray(value.capabilities) ? value.capabilities.filter((one) => typeof one === "string") : [],
    trouble: text(value.trouble),
    facts: parseFacts(value.facts),
    agents: listed === null ? null : {
      entries: (listed.entries as unknown[]).map(parseAgent).filter((one): one is Agent => one !== null),
      complete: listed.complete === true,
    },
    read_error: text(value.read_error),
    ended_at: finite(value.ended_at),
    has_box: value.has_box === true,
    unread_since: finite(value.unread_since),
  };
}

/** The route's or the topic's value, or null for anything else. A session with no text
 * `key` is left out. */
export function parseLive(data: unknown): Live | null {
  if (!isTable(data) || typeof data.listed !== "boolean" || !Array.isArray(data.sessions)) return null;
  return { sessions: data.sessions.map(parseSession).filter((one): one is LiveSession => one !== null) };
}

/** What a read of `/api/sessions` leaves the list knowing: the value it brought, or why it
 * failed in turns' words. A 404 is turns gone, and leaves nothing. */
export function liveRead(answer: Answer): LiveRead | null {
  if (answer.status === 404) return null;
  const value = answer.status === 200 ? parseLive(answer.data) : null;
  if (value !== null) return { value, failure: null };
  let why: string;
  if (answer.status === 0) why = "no answer from the shell";
  else if (answer.status === 200) why = "turns' page server sent an answer that can't be read";
  else why = `turns' page server answered ${answer.status}`;
  return { value: null, failure: why };
}

/**
 * The turns session of each running launch, by request id: only where one launch and one
 * turns session carry the same session id, as turns' panel pairs them. A launch with no
 * session id pairs with none.
 */
export function pairsOf(entries: readonly RunningEntry[], liveValue: Live): Map<string, LiveSession> {
  const paired = new Map<string, LiveSession>();
  const count = (ids: (string | null)[]): Map<string, number> => {
    const seen = new Map<string, number>();
    for (const id of ids) if (id) seen.set(id, (seen.get(id) ?? 0) + 1);
    return seen;
  };
  const launches = count(entries.map((entry) => entry.session_id));
  const sessions = count(liveValue.sessions.map((session) => session.session_id));
  for (const entry of entries) {
    const id = entry.session_id;
    if (!id || launches.get(id) !== 1 || sessions.get(id) !== 1) continue;
    paired.set(entry.request_id, liveValue.sessions.find((session) => session.session_id === id) as LiveSession);
  }
  return paired;
}

// --- turns' rules, as its panel has them ------------------------------------------------------

/** Whether only the session's watch is down: its reads answer, so it is no problem. */
export function isWatchAlone(session: LiveSession): boolean {
  return session.read_error !== null && session.unread_since === null;
}

/** One problem: its lead and its reason. */
export interface Problem {
  lead: string;
  reason: string;
}

/** Why the live part did not read, by the facts' `live_error`: the session did not answer
 * in time, its live read failed, or, when it answered, its agent list was not whole. */
export function liveReason(liveError: string | null): string {
  if (liveError === "timeout") return NO_ANSWER_IN_TIME;
  if (liveError === "failed") return LIVE_READ_FAILED;
  return LIST_NOT_WHOLE;
}

/** The session's problems, in turns' order: the endpoint's trouble, a failed read, the
 * live part that did not read, with why. */
export function problemsOf(session: LiveSession): Problem[] {
  const found: Problem[] = [];
  if (session.trouble !== null) found.push({ lead: TROUBLE_LEAD, reason: session.trouble });
  if (session.read_error !== null && !isWatchAlone(session)) {
    found.push({ lead: UNREAD_LEAD, reason: session.read_error });
  }
  if (session.facts !== null && session.facts.agents === null) {
    found.push({ lead: UNREAD_LEAD, reason: liveReason(session.facts.live_error) });
  }
  return found;
}

/** turns' busy rule: no final notice yet, and a state that is still going. */
export function isBusy(agent: Agent): boolean {
  return agent.final !== true && ["pending", "running", "waiting"].includes(agent.state);
}

const KINDS: readonly { kind: string; one: string; many: string }[] = [
  { kind: "subagent", one: "subagent busy", many: "subagents busy" },
  { kind: "teammate", one: "teammate busy", many: "teammates busy" },
  { kind: "shell", one: "shell running", many: "shells running" },
];

/** What turns counts busy, in its panel's order: `1 subagent busy`, `2 teammates busy`,
 * `3 shells running`, and `2 agents busy` when it has the count and no list. A kind with
 * none says nothing. `without` leaves out the phrases of its kinds, and the count, which
 * can't be split by kind. */
export function busyWords(session: LiveSession, without: readonly string[] = []): string[] {
  const words: string[] = [];
  if (session.agents !== null) {
    for (const { kind, one, many } of KINDS) {
      if (without.includes(kind)) continue;
      const busy = session.agents.entries.filter((agent) => agent.kind === kind && isBusy(agent)).length;
      if (busy > 0) words.push(busy === 1 ? `1 ${one}` : `${busy} ${many}`);
    }
  } else if (session.facts !== null && session.facts.agents !== null && session.facts.agents > 0
             && without.length === 0) {
    words.push(`${session.facts.agents} agents busy`);
  }
  return words;
}

/** Whether the last ready was raised for the session id the reading was taken under. */
function isReadyOfId(facts: Facts): boolean {
  return facts.ready !== null && facts.ready.session_id !== null && facts.ready.session_id === facts.session_id;
}

/** Since when the session has been in its state, as turns tells it, or null. */
export function sinceOf(session: LiveSession): number | null {
  const facts = session.facts;
  if (facts === null) return null;
  if (facts.turn_open) return facts.turn_started_at;
  const readyAt = isReadyOfId(facts) ? facts.ready?.at ?? null : null;
  if (session.ended_at !== null) return readyAt === null ? session.ended_at : Math.max(session.ended_at, readyAt);
  return facts.turn_number === 0 ? readyAt : null;
}

// --- a running row ----------------------------------------------------------------------------

/** What turns adds to a running row: line two's leading words, and the dim third line. */
export interface LiveRow {
  /** `working · 1 subagent busy`, `2 shells running`; null with nothing to say. */
  words: string | null;
  /** `Moxi can't read its live state: …`; null when turns reads the session. */
  trouble: string | null;
}

/** A problem as its line says it. */
const problemText = (problem: Problem): string => `${problem.lead}: ${problem.reason.replace(/\.$/, "")}`;

/**
 * A running row's live words from its turns session: `working` while a turn is open, then
 * what is busy, each only when true; and the session's problems on one line. `without`
 * leaves out the phrases of the kinds another lead already counts.
 */
export function liveRow(session: LiveSession, without: readonly string[] = []): LiveRow {
  const words = [...(session.facts?.turn_open === true ? [WORKING] : []), ...busyWords(session, without)];
  const problems = problemsOf(session);
  return {
    words: words.length > 0 ? words.join(" · ") : null,
    trouble: problems.length > 0 ? problems.map(problemText).join(" · ") : null,
  };
}

/** The line a running row with a session id says when turns' list could not be read. */
export function unreadRow(failure: string): LiveRow {
  return { words: null, trouble: problemText({ lead: UNREAD_LEAD, reason: failure }) };
}

// --- the opened row ---------------------------------------------------------------------------

/** One entry of the `Agents` line: its words before the state, its state, and whether it
 * is busy (drawn in the text colour). */
export interface AgentLine {
  id: string;
  text: string;
  state: string;
  busy: boolean;
}

/** turns' lines on an opened running row. A null line is not drawn. */
export interface LiveOpened {
  now: string | null;
  agents: { entries: AgentLine[]; notKnown: string | null } | null;
  promptBox: string | null;
}

/** `Now`: `working on turn 4 since 18:52`, `idle since 17:35 · turn 4`, without a
 * since-time `working on turn 4` or `idle · turn 4`; ` · turn 0` is left off. `when`
 * words a moment as the row's other times are worded. Null without facts. */
export function nowLine(session: LiveSession, when: (at: number) => string): string | null {
  const facts = session.facts;
  if (facts === null) return null;
  const since = sinceOf(session);
  const sinceWords = since === null ? "" : ` since ${when(since)}`;
  if (facts.turn_open) return `working on turn ${facts.turn_number}${sinceWords}`;
  return facts.turn_number === 0 ? `idle${sinceWords}` : `idle${sinceWords} · turn ${facts.turn_number}`;
}

/** `Agents`: each entry, `<description> · <kind>` or the kind alone, with its state; then
 * `not known` when the list is not whole. `not known` alone for a session with the
 * `agents` capability whose list is null; null without that capability, and for a whole
 * list with no entry. */
export function agentsLines(session: LiveSession): LiveOpened["agents"] {
  if (session.agents === null) {
    return session.capabilities.includes("agents") ? { entries: [], notKnown: NOT_KNOWN } : null;
  }
  if (session.agents.entries.length === 0 && session.agents.complete) return null;
  const entries = session.agents.entries.map((agent) => ({
    id: agent.id,
    text: agent.description ? `${agent.description} · ${agent.kind}` : agent.kind,
    state: agent.state,
    busy: isBusy(agent),
  }));
  return { entries, notKnown: session.agents.complete ? null : NOT_KNOWN };
}

/** `Prompt box`, only where turns reads the box: `draft not sent` when it read a draft,
 * `empty` when it read none, and `not known` when the box was not read (`draft` null). */
export function promptBoxLine(session: LiveSession): string | null {
  if (!session.has_box || session.facts === null) return null;
  const draft = session.facts.draft;
  return draft === true ? DRAFT_NOT_SENT : draft === false ? EMPTY_BOX : BOX_NOT_KNOWN;
}

/** turns' lines for an opened running row. */
export function liveOpened(session: LiveSession, when: (at: number) => string): LiveOpened {
  return { now: nowLine(session, when), agents: agentsLines(session), promptBox: promptBoxLine(session) };
}
