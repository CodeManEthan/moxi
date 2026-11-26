// The lines of a session's opened row on turns' panel, worked out with no DOM so the Node
// tests can read them: `Now`, `Agents`, `Prompt box`, `Session`, `Details` and the id
// `Copy id` copies. Each line shows only when its fact is there. Every string goes into
// the page as text, never as markup.

import type { Session } from "./feed.ts";
import type { Clock } from "./rows-model.ts";
import { dayTime, dayWord, DRAFT_NOT_SENT, harnessName, isBusy, LOCAL, sinceOf, timeWord } from "./rows-model.ts";

/** What the `Agents` line says when the list is not complete, or not read at all. */
export const NOT_KNOWN = "not known · the list could not be read whole, so some may still run";
/** The `Prompt box` line when the box was read empty. */
export const EMPTY_BOX = "empty";
/** The `Prompt box` line when the box was not read. */
export const BOX_NOT_KNOWN = "not known · it could not be read, so it may hold a draft";
/** The dim label at the left of each opened line, and the act that copies the id. */
export const LABELS = { now: "Now", agents: "Agents", promptBox: "Prompt box", session: "Session",
                        details: "Details" } as const;
export const COPY_ID = "Copy id";

/** One entry of the `Agents` line: its words before the state, and its state, set in the
 * text colour while it is busy and dim otherwise. */
export interface AgentLine {
  id: string;
  /** `<description> · <kind>`, or the kind alone. */
  text: string;
  state: string;
  busy: boolean;
}

/** The `Agents` line: its entries in the list's order, then `not known` when the list is
 * not whole. */
export interface AgentsLines {
  entries: AgentLine[];
  notKnown: string | null;
}

/** The opened row. A null line is not drawn. */
export interface Opened {
  now: string | null;
  agents: AgentsLines | null;
  promptBox: string | null;
  session: string;
  details: string;
  /** The session id `Copy id` copies; null hides it. */
  copyId: string | null;
}

/** A folder in `~` form when it is under `home`. */
export function tildeForm(cwd: string, home: string | null): string {
  if (home === null || home === "") return cwd;
  const base = home.replace(/[\\/]+$/, "");
  if (cwd === base) return "~";
  for (const sep of ["/", "\\"]) {
    if (cwd.startsWith(base + sep)) return `~${sep}${cwd.slice(base.length + 1)}`;
  }
  return cwd;
}

/** `Now`: `working on turn 4 since 18:52`, `idle since 17:35 · turn 4`, without a
 * since-time `working on turn 4` or `idle · turn 4`; ` · turn 0` is left off. Null without
 * facts. */
export function nowLine(session: Session, today: string, clock: Clock = LOCAL): string | null {
  const facts = session.facts;
  if (facts === null) return null;
  const since = sinceOf(session);
  const when = since === null ? "" : ` since ${timeWord(since, today, clock)}`;
  if (facts.turn_open) return `working on turn ${facts.turn_number}${when}`;
  return facts.turn_number === 0 ? `idle${when}` : `idle${when} · turn ${facts.turn_number}`;
}

/** `Agents`: one entry per agent, then `not known` when the list is not whole; `not
 * known` alone for a session with the `agents` capability whose list is null; null for a
 * session without that capability, and for a whole list with no entry, which has nothing
 * to say on the line. */
export function agentsLines(session: Session): AgentsLines | null {
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
export function promptBoxLine(session: Session): string | null {
  if (!session.has_box || session.facts === null) return null;
  const draft = session.facts.draft;
  return draft === true ? DRAFT_NOT_SENT : draft === false ? EMPTY_BOX : BOX_NOT_KNOWN;
}

/** `Session`: the harness, `started <day> <time>`, the folder in `~` form. */
export function sessionLine(session: Session, home: string | null, today: string, clock: Clock = LOCAL): string {
  const parts = [harnessName(session.harness)];
  if (session.started !== null) parts.push(`started ${dayTime(session.started, today, clock)}`);
  if (session.cwd) parts.push(tildeForm(session.cwd, home));
  return parts.join(" · ");
}

/** The ready's part of `Details`: `ready since 08:20, from startup`, `ready since 08:20`,
 * `ready, from startup` or `ready`; null with no ready. */
export function readyPart(session: Session, today: string, clock: Clock = LOCAL): string | null {
  const ready = session.facts?.ready ?? null;
  if (ready === null) return null;
  const when = ready.at === null ? "" : ` since ${timeWord(ready.at, today, clock)}`;
  return ready.source ? `ready${when}, from ${ready.source}` : `ready${when}`;
}

/** `can't read since 19:09:58`, the start of the run of unread readings, with seconds;
 * its day before it when it is not `today`. */
export function unreadPart(at: number, today: string, clock: Clock = LOCAL): string {
  const day = clock.day(at);
  const time = clock.seconds(at);
  return `can't read since ${day === today ? time : `${dayWord(day, today)} ${time}`}`;
}

/** `Details`: the pid, the ready, turns' version and the runner's when it is another
 * string, and `can't read since` while readings go unread. */
export function detailsLine(session: Session, today: string, clock: Clock = LOCAL): string {
  const parts: string[] = [];
  if (session.pid !== null) parts.push(`pid ${session.pid}`);
  const ready = readyPart(session, today, clock);
  if (ready !== null) parts.push(ready);
  if (session.endpoint_version !== null) parts.push(`turns ${session.endpoint_version}`);
  if (session.mod_version !== null && session.mod_version !== session.endpoint_version) {
    parts.push(`runner ${session.mod_version}`);
  }
  if (session.unread_since !== null) parts.push(unreadPart(session.unread_since, today, clock));
  return parts.join(" · ");
}

/** The opened row's lines for `session`. */
export function openedOf(session: Session, home: string | null, today: string, clock: Clock = LOCAL): Opened {
  return {
    now: nowLine(session, today, clock),
    agents: agentsLines(session),
    promptBox: promptBoxLine(session),
    session: sessionLine(session, home, today, clock),
    details: detailsLine(session, today, clock),
    copyId: session.session_id || null,
  };
}
