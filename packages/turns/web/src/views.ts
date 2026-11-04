// What turns' panel shows for each state of its data, worked out with no DOM so the Node
// tests can read it. Every string here goes into the page as text, never as markup.

import type { Agent, Failure, Session, SessionList } from "./feed.ts";

/** The panel's data as its `Feed` holds it. */
export interface FeedState {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: SessionList | null;
}

/** One agent's line under its session. */
export interface AgentView {
  id: string;
  kind: string;
  state: string;
  /** "final", "not final", or null when the harness gives no notice to read. */
  final: string | null;
  description: string | null;
}

/** One session's row as it is drawn. */
export interface RowView {
  key: string;
  harness: string;
  /** The session id's first characters, or a sentence when it has none yet. */
  shortId: string;
  /** The whole session id, for a title, or null. */
  sessionId: string | null;
  /** Display-only. */
  cwd: string | null;
  /** "turn 4", or null before the facts are read. */
  turn: string | null;
  /** Whether a turn is open, or null before the facts are read. */
  open: boolean | null;
  /** The line under the heading: started, pid, agents, draft, ready, in that order. */
  facts: string[];
  /** "endpoint 2.1, mod 0.2.0", or null when neither is known. */
  versions: string | null;
  /** The facts are not one moment. */
  stale: boolean;
  /** The endpoint's own trouble, as it says it. */
  trouble: string | null;
  /** turns' page server could not watch or read this session. */
  readError: string | null;
  agents: AgentView[];
  /** The agent list may be missing some: the harness could not list them all. */
  agentsIncomplete: boolean;
}

/** The panel as it is drawn. */
export interface PageView {
  /** `loading` (no read yet), `scanning` (the page server hasn't finished its first
   * look), `up`, `empty`, `failed` (the request failed) or `gone` (a 404; nothing is
   * drawn). */
  state: "loading" | "scanning" | "up" | "empty" | "failed" | "gone";
  rows: RowView[];
  /** With `failed`, what went wrong. */
  failure: string | null;
  /** The page server's last scan failed: its sentence. Shown beside any state. */
  scan: string | null;
}

export const SHORT_ID = 8;
export const NO_ID = "no session id yet";
export const NOT_READ = "not read yet";

/** A length of time in whole units: `45s`, `41m`, `1h4m`, `3d`. */
export function span(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m`;
  if (total < 86400) {
    const minutes = Math.floor((total % 3600) / 60);
    return `${Math.floor(total / 3600)}h${minutes ? `${minutes}m` : ""}`;
  }
  return `${Math.floor(total / 86400)}d`;
}

/** "5m ago" for an epoch time at the server's `now`; "at <UTC time>" before a read gives
 * the server's clock. */
export function ago(when: number, now: number | null): string {
  if (now === null) return `at ${new Date(when * 1000).toISOString().slice(0, 19)}Z`;
  return `${span(now - when)} ago`;
}

function agentView(agent: Agent): AgentView {
  const final = agent.final === null ? null : agent.final ? "final" : "not final";
  return { id: agent.id, kind: agent.kind, state: agent.state, final,
           description: agent.description };
}

/** The row for `session` at the server's time `now` (null before the first read). */
export function rowView(session: Session, now: number | null): RowView {
  const facts = session.facts;
  const line: string[] = [];
  if (session.started !== null) line.push(`started ${ago(session.started, now)}`);
  if (session.pid !== null) line.push(`pid ${session.pid}`);
  if (facts === null) {
    line.push(`facts ${NOT_READ}`);
  } else {
    if (facts.agents === null) line.push("agents unknown");
    else line.push(facts.agents === 1 ? "1 agent busy" : `${facts.agents} agents busy`);
    if (facts.draft) line.push("draft in the prompt box");
    if (facts.ready !== null) {
      const from = facts.ready.source ? ` from ${facts.ready.source}` : "";
      const when = facts.ready.at === null ? "" : ` ${ago(facts.ready.at, now)}`;
      line.push(`ready${from}${when}`);
    }
  }
  const versions = [
    session.endpoint_version === null ? null : `endpoint ${session.endpoint_version}`,
    session.mod_version === null ? null : `mod ${session.mod_version}`,
  ].filter((part): part is string => part !== null);
  const id = session.session_id;
  return {
    key: session.key,
    harness: session.harness || "unknown harness",
    shortId: id ? id.slice(0, SHORT_ID) : NO_ID,
    sessionId: id || null,
    cwd: session.cwd,
    turn: facts === null ? null : `turn ${facts.turn_number}`,
    open: facts === null ? null : facts.turn_open,
    facts: line,
    versions: versions.length ? versions.join(", ") : null,
    stale: facts?.stale === true,
    trouble: session.trouble,
    readError: session.read_error,
    agents: (session.agents?.entries ?? []).map(agentView),
    agentsIncomplete: session.agents !== null && !session.agents.complete,
  };
}

/** The panel at the server's time `now`. */
export function pageView(data: FeedState, now: number | null): PageView {
  const value = data.value;
  const rows = (value?.sessions ?? []).map((session) => rowView(session, now));
  const base = { rows, failure: null, scan: value?.detail ?? null };
  if (data.gone) return { ...base, rows: [], scan: null, state: "gone" };
  if (data.failure !== null) return { ...base, state: "failed", failure: data.failure.why };
  if (data.loading || value === null) return { ...base, state: "loading" };
  if (rows.length === 0) return { ...base, state: value.listed ? "empty" : "scanning" };
  return { ...base, state: "up" };
}
